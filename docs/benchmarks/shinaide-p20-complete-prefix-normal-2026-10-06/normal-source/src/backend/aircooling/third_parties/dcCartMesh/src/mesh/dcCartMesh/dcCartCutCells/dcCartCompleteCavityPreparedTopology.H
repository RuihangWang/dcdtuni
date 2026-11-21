#ifndef dcCartCompleteCavityPreparedTopology_H
#define dcCartCompleteCavityPreparedTopology_H
#include "dcCartCompleteCavityLocalOracle.H"
#include "dcCartExactBinary64Geometry.H"
#include "tetPointRef.H"
#include <type_traits>
#include <chrono>
#include <limits>
namespace Foam { namespace dcCartCompleteCavityPreparedTopology
{
namespace O=dcCartCompleteCavityLocalOracle;
namespace E=dcCartExactBinary64Geometry;
using Cut=O::Cut;using View=O::View;using Values=O::Values;using Authority=O::Authority;
using Lineage=dcCartCompleteCavityOriginCore::Lineage<label>;
// One owned monotonic deadline for the entire normal repair invocation.
// No external clock callback is needed after the final global input recheck.
class PublicationBudget
{
    using Clock=std::chrono::steady_clock;
    Clock::time_point until_{};bool valid_=false;
public:
    static PublicationBudget capture(const std::function<scalar()>& remaining) noexcept
    {
        PublicationBudget out;const auto begin=Clock::now();
        try
        {
            if(!remaining)return out;
            const scalar seconds=remaining();
            if(!std::isfinite(seconds)||seconds<=0)return out;
            const scalar bounded=std::min(scalar(4),seconds);
            using Duration=Clock::duration;using Rep=Duration::rep;
            const long double ticks=static_cast<long double>(bounded)
                *Duration::period::den/Duration::period::num;
            if(!(ticks>=1)||ticks>static_cast<long double>(std::numeric_limits<Rep>::max()))return out;
            const Duration span(static_cast<Rep>(ticks)); // truncation cannot extend the budget
            if(begin>Clock::time_point::max()-span)return out;
            out.until_=begin+span;out.valid_=true;
        }
        catch(...){}
        return out;
    }
    bool available()const noexcept {return valid_&&Clock::now()<until_;}
};
struct NewFace {std::vector<label> ring;label owner=-1,neighbour=-1;};
struct Recipe
{
    // Real native slots only. Appended points/faces/cells are contiguous.
    // No caller source/meta/alias/D-mask or "validated" bool is accepted.
    std::vector<point> appendedPoints;
    std::vector<label> replacementCells;
    std::map<label,label> exteriorCell;
    std::vector<NewFace> internalFaces;
};
enum class Refusal {none,clock,source,stale,shape,capacity,alias,geometry,
                    coverage,quality,allocation,token};
struct Certificate
{
    Values before,after;
    label oldDBad=0,newDBad=0;
    std::array<label,4> oldBad{{0,0,0,0}},newBad{{0,0,0,0}};
    std::array<scalar,4> oldWorst{{0,0,0,0}},newWorst{{0,0,0,0}};
    bool installedArithmetic=false,exactMoments=false,disjointCoverage=false;
};
inline E::Point exactPoint(const point& p){return {{p.x(),p.y(),p.z()}};}
inline const O::Alias* alias(const View& v,label p)
{for(const auto& a:v.aliases)if(a.representative==p)return &a;return nullptr;}
inline bool polygon(const View& v,const O::Face& f,bool outward,E::Face& out)
{
    out.clear();
    if(f.ring.size()<3||f.ring.size()>O::Caps::ring)return false;
    for(label p:f.ring){const auto* a=alias(v,p);if(!a||!O::finite(a->world))return false;out.push_back(exactPoint(a->world));}
    if(!outward)std::reverse(out.begin(),out.end());return true;
}
inline bool cellShell(const View& v,const O::Cell& c,E::Faces& out)
{
    out.clear();
    if(c.faces.size()<4||c.faces.size()>64)return false;
    for(label id:c.faces)
    {
        const O::Face* f=nullptr;
        for(const auto& x:v.faces)if(x.native==id){f=&x;break;}
        if(!f||(! (f->owner==c.native||f->neighbour==c.native)))return false;
        E::Face ring;if(!polygon(v,*f,f->owner==c.native,ring))return false;
        out.push_back(std::move(ring));
    }
    return true;
}
inline bool exactCoverage(const View& old,const View& next,
                          const std::function<bool()>& awake,Certificate& out)
{
    E::Context ec(awake);E::Faces oldShell,newShell;
    std::set<label> oldCore(old.core.begin(),old.core.end()),newCore(next.core.begin(),next.core.end());
    for(const auto& c:old.cells)if(oldCore.count(c.native))
    {E::Faces s;if(!cellShell(old,c,s))return false;oldShell.insert(oldShell.end(),s.begin(),s.end());}
    std::vector<E::Faces> cells;std::vector<std::vector<E::Point>> vertices;
    for(const auto& c:next.cells)if(newCore.count(c.native))
    {
        E::Faces s;if(!cellShell(next,c,s))return false;
        for(const auto& f:s){const auto q=E::planarConvex(f,ec);if(!q.known||!q.value)return false;}
        const auto closed=E::directedClosed(s,ec),convex=E::shellHalfspaces(s,ec);
        auto m=E::polynomialMoments(s,ec);
        if(!closed.known||!closed.value||!convex.known||!convex.value
         ||!m.known||E::sign(m.sixV)<=0)return false;
        std::vector<E::Point> vs;for(const auto& f:s)for(const auto& p:f)
            if(std::find(vs.begin(),vs.end(),p)==vs.end())vs.push_back(p);
        cells.push_back(s);vertices.push_back(std::move(vs));
        newShell.insert(newShell.end(),s.begin(),s.end());
    }
    const auto a=E::polynomialMoments(oldShell,ec),b=E::polynomialMoments(newShell,ec);
    const auto eq=E::equalMoments(a,b,ec);
    if(!eq.known||!eq.value)return false;out.exactMoments=true;
    // Positive convex pieces and pairwise SAT, not closed-shell alone.
    for(std::size_t i=0;i<cells.size();++i)for(std::size_t j=i+1;j<cells.size();++j)
    {
        bool separate=false;
        for(const auto& f:cells[i])
        {auto q=E::separatedByFace(vertices[i],vertices[j],{{f[0],f[1],f[2]}},ec);if(!q.known)return false;if(q.separated){separate=true;break;}}
        if(!separate)for(const auto& f:cells[j])
        {auto q=E::separatedByFace(vertices[i],vertices[j],{{f[0],f[1],f[2]}},ec);if(!q.known)return false;if(q.separated){separate=true;break;}}
        if(!separate)
            for(const auto& f:cells[i])for(std::size_t x=0;x<f.size()&&!separate;++x)
                for(const auto& g:cells[j])for(std::size_t y=0;y<g.size()&&!separate;++y)
                {
                    auto q=E::separatedByEdges(vertices[i],vertices[j],
                        {{f[x],f[(x+1)%f.size()]}},{{g[y],g[(y+1)%g.size()]}},ec);
                    if(!q.known)return false;if(q.separated)separate=true;
                }
        if(!separate)return false;
    }
    out.disjointCoverage=true;return ec.good();
}
// Exact installed checkConcaveCells local predicate (primitiveMeshCheck.C
// 838-872), excluding its MPI reduce at 892. No trial calls a collective.
inline bool installedGeometry(const O::GeometryMesh& m,
                              const std::map<label,label>& cellLocal,
                              const std::map<label,label>& faceLocal,
                              const View& v,const std::function<bool()>& awake)
{
    const auto& cc=m.cellCentres();const auto& sf=m.faceAreas();const auto& fc=m.faceCentres();
    const auto& vol=m.cellVolumes();const auto& own=m.faceOwner();
    std::set<label> affected(v.core.begin(),v.core.end());
    affected.insert(v.outside.begin(),v.outside.end());
    for(label id:affected)
    {
        if(!awake()||!cellLocal.count(id))return false;
        const label c=cellLocal.at(id);if(!O::finite(cc[c])||!O::finite(vol[c])||vol[c]<=VSMALL)return false;
        vector closure(Zero);scalar area=0;
        for(label f:m.cells()[c])
        {
            if(!O::finite(sf[f])||!O::finite(fc[f])||mag(sf[f])<=1e-12)return false;
            vector n=sf[f];n/=max(mag(n),VSMALL);if(own[f]!=c)n*=-1;
            const vector outward=own[f]==c?sf[f]:-sf[f];
            if((outward&(fc[f]-cc[c]))<=0)return false;
            closure+=outward;area+=mag(sf[f]);
            for(label other:m.cells()[c])if(other!=f)
            {
                vector delta=fc[other]-fc[f];delta/=max(mag(delta),VSMALL);
                if((delta&n)>-O::GeometryMesh::actualPlanarCosAngle())return false;
            }
        }
        if(mag(closure)/max(area,VSMALL)>1e-12)return false;
    }
    for(const auto& f:v.faces)if(f.metric)
    {
        if(!awake()||!faceLocal.count(f.native)||!cellLocal.count(f.owner)
         ||(f.internal&&!cellLocal.count(f.neighbour)))return false;
        const label fi=faceLocal.at(f.native);const auto& ring=m.faces()[fi];
        const point co=cc[cellLocal.at(f.owner)],cn=f.internal?cc[cellLocal.at(f.neighbour)]:point::zero;
        const scalar tol=sqr(SMALL);
        forAll(ring,j)
        {
            const point a=m.points()[ring[j]],b=m.points()[ring[(j+1)%ring.size()]];
            const scalar q=tetPointRef(a,b,fc[fi],co).quality();
            const scalar r=f.internal?tetPointRef(a,b,fc[fi],cn).quality():1;
            if(!O::finite(q)||!O::finite(r)||q>=-tol||r<=tol)return false;
        }
        bool anchor=false;
        for(label k=0;k<ring.size()&&!anchor;++k)
        {
            bool ok=true;
            for(label j=1;j+1<ring.size();++j)
            {
                const point a=m.points()[ring[k]],b=m.points()[ring[(k+j)%ring.size()]],d=m.points()[ring[(k+j+1)%ring.size()]];
                const scalar q=tetPointRef(a,b,d,co).quality(),r=f.internal?tetPointRef(a,b,d,cn).quality():1;
                if(!O::finite(q)||!O::finite(r)||q>=-tol||r<=tol){ok=false;break;}
            }
            anchor=ok;
        }
        if(!anchor)return false;
    }
    return true;
}
inline bool hardLineage(const View& old,const View& next,Certificate& p)
{
    if(p.before.refusal!=O::Refusal::none||p.after.refusal!=O::Refusal::none)return false;
    std::set<label> core(old.core.begin(),old.core.end()),replacement(next.core.begin(),next.core.end());
    scalar oldDWorst=0,newDWorst=0;
    for(const auto& c:old.cells)
    {
        const scalar d=p.before.determinant.at(c.native);
        if(!O::finite(d)||d<0)return false;
        if(d<.001){++p.oldDBad;oldDWorst=max(oldDWorst,dcCartWeightUnionPreview::severity(d,0));}
        
    }
    for(const auto& c:next.cells)
    {
        const scalar d=p.after.determinant.at(c.native);
        if(!O::finite(d)||d<0)return false;
        if(d<.001){++p.newDBad;newDWorst=max(newDWorst,dcCartWeightUnionPreview::severity(d,0));}
        if(!replacement.count(c.native))
        {auto o=p.before.determinant.find(c.native);if(o==p.before.determinant.end()||(o->second>=.001&&d<.001))return false;}
        else if(d<.001)return false; // every replacement passes, not count-only
    }
    if(p.newDBad>=p.oldDBad||newDWorst>oldDWorst)return false;
    for(const auto& x:p.before.metrics)for(unsigned k=0;k<4;++k)
    {
        if(!dcCartWeightUnionPreview::pass(x.second[k],k+1))
        {++p.oldBad[k];p.oldWorst[k]=max(p.oldWorst[k],dcCartWeightUnionPreview::severity(x.second[k],k+1));}
    }
    for(const auto& x:p.after.metrics)for(unsigned k=0;k<4;++k)
    {
        const bool passing=dcCartWeightUnionPreview::pass(x.second[k],k+1);
        if(!passing){++p.newBad[k];p.newWorst[k]=max(p.newWorst[k],dcCartWeightUnionPreview::severity(x.second[k],k+1));}
        auto oldFace=p.before.metrics.find(x.first);
        // New internal faces and every original passing hard role are protected.
        if(k<3&&((oldFace==p.before.metrics.end()&&!passing)
         ||(oldFace!=p.before.metrics.end()&&dcCartWeightUnionPreview::pass(oldFace->second[k],k+1)&&!passing)))return false;
    }
    // A removed passing hard face transfers that role to ALL new internal faces.
    for(const auto& x:p.before.metrics)if(!p.after.metrics.count(x.first))
        for(unsigned k=0;k<3;++k)if(dcCartWeightUnionPreview::pass(x.second[k],k+1))
            for(const auto& y:p.after.metrics)if(!p.before.metrics.count(y.first)
             &&!dcCartWeightUnionPreview::pass(y.second[k],k+1))return false;
    for(unsigned k=0;k<4;++k)
        if(p.newBad[k]>p.oldBad[k]||p.newWorst[k]>p.oldWorst[k])return false;
    return true;
}
} } // prepared topology
namespace Foam
{
struct dcCartCutCells::writerCompleteCavityState
{
    dcCartCompleteCavityLocalOracle::View oldView,nextView;
    dcCartCompleteCavityPreparedTopology::Certificate certificate;
    completeCavityAuthority authority;
    label oldNativePoints=-1;
    std::size_t bytes=0;
};
namespace dcCartCompleteCavityPreparedTopology
{
template<class T> struct PreparedArray
{
    std::size_t oldSize=0,oldCapacity=0,finalSize=0;std::vector<T> buffer;
    bool prepare(const std::vector<T>& live,std::size_t size,std::size_t& bytes)
    {
        oldSize=live.size();oldCapacity=live.capacity();finalSize=size;
        if(size<oldSize)return false;
        if(size>live.capacity())
        {
            if(size>256u*1024u*1024u/sizeof(T)||bytes>256u*1024u*1024u-size*sizeof(T))return false;
            bytes+=size*sizeof(T);buffer=live;buffer.resize(size);
        }
        return true;
    }
    bool bound(const std::vector<T>& live)const noexcept
    {return live.size()==oldSize&&live.capacity()==oldCapacity
         &&(buffer.empty()?finalSize<=live.capacity():buffer.size()==finalSize);}
    void publish(std::vector<T>& live)noexcept
    {if(!buffer.empty())live.swap(buffer);else live.resize(finalSize);}
};
struct PreparedFaces
{
    std::size_t oldSize=0,oldCapacity=0,finalSize=0;std::vector<std::vector<label>> buffer;
    bool prepare(const std::vector<std::vector<label>>& live,std::size_t size,std::size_t& bytes)
    {
        oldSize=live.size();oldCapacity=live.capacity();finalSize=size;
        if(size<oldSize)return false;
        if(size>live.capacity())
        {
            if(size>256u*1024u*1024u/sizeof(std::vector<label>)
             ||bytes>256u*1024u*1024u-size*sizeof(std::vector<label>))return false;
            bytes+=size*sizeof(std::vector<label>);buffer.resize(size);
        }
        return true;
    }
    bool bound(const std::vector<std::vector<label>>& live)const noexcept
    {return live.size()==oldSize&&live.capacity()==oldCapacity
         &&(buffer.empty()?finalSize<=live.capacity():buffer.size()==finalSize);}
    void publish(std::vector<std::vector<label>>& live)noexcept
    {
        if(buffer.empty())live.resize(finalSize);
        else {for(std::size_t i=0;i<oldSize;++i)live[i].swap(buffer[i]);live.swap(buffer);}
    }
};
class Prepared
{
    friend bool prepare(Cut&,const View&,const Authority&,label,const std::vector<std::vector<label>>&,
                        Recipe,const PublicationBudget&,const std::function<bool()>&,Prepared&);
    friend bool commit(Cut&,const Authority&,const std::function<bool()>&,Prepared&);
    const Cut* owner_=nullptr;bool ready_=false;label oldCells_=-1,oldPoints_=-1,oldFaces_=-1;
    View old_,next_;Recipe recipe_;Certificate certificate_;Refusal refusal_=Refusal::shape;
    PublicationBudget publicationBudget_;
    std::vector<label> removedFaces_;std::size_t preparedBytes_=0;
    struct Meta
    {
        label cell=-1,level=-1,rank=-1;std::uint64_t leaf=0,piece=0;
        bool alive=false,original=false;scalar referenceVolume=0;point referenceCentre=point::zero;
    };
    std::vector<Meta> metadata_;
    std::unique_ptr<Lineage> lineageShadow_;
    std::shared_ptr<dcCartCutCells::writerCompleteCavityState> journal_;
    std::uint64_t lineageRevision_=0;
    Lineage::PreparedReplacement lineage_;
    PreparedArray<point> points_,refCentre_;
    PreparedArray<scalar> refVol_;
    PreparedFaces faces_;
    PreparedArray<label> own_,nei_,patch_,zone_,level_,rank_,evidenceParent_,evidenceBase_;
    PreparedArray<std::uint64_t> leaf_,piece_;
    PreparedArray<bool> faceAlive_,cellAlive_,origAlive_;
public:
    Prepared()=default;Prepared(const Prepared&)=delete;Prepared& operator=(const Prepared&)=delete;
    Prepared(Prepared&& other) noexcept {*this=std::move(other);}
    Prepared& operator=(Prepared&& other) noexcept
    {
        if(this==&other)return *this;
        owner_=other.owner_;ready_=other.ready_;oldCells_=other.oldCells_;
        oldPoints_=other.oldPoints_;oldFaces_=other.oldFaces_;refusal_=other.refusal_;
        old_=std::move(other.old_);next_=std::move(other.next_);recipe_=std::move(other.recipe_);
        certificate_=std::move(other.certificate_);removedFaces_=std::move(other.removedFaces_);
        preparedBytes_=other.preparedBytes_;lineageRevision_=other.lineageRevision_;
        publicationBudget_=other.publicationBudget_;
        lineageShadow_=std::move(other.lineageShadow_);lineage_=std::move(other.lineage_);
        metadata_=std::move(other.metadata_);journal_=std::move(other.journal_);
        points_=std::move(other.points_);refCentre_=std::move(other.refCentre_);
        refVol_=std::move(other.refVol_);faces_=std::move(other.faces_);
        own_=std::move(other.own_);nei_=std::move(other.nei_);patch_=std::move(other.patch_);
        zone_=std::move(other.zone_);level_=std::move(other.level_);rank_=std::move(other.rank_);
        evidenceParent_=std::move(other.evidenceParent_);evidenceBase_=std::move(other.evidenceBase_);
        leaf_=std::move(other.leaf_);piece_=std::move(other.piece_);
        faceAlive_=std::move(other.faceAlive_);cellAlive_=std::move(other.cellAlive_);origAlive_=std::move(other.origAlive_);
        other.owner_=nullptr;other.ready_=false;other.lineageRevision_=0;
        other.publicationBudget_=PublicationBudget();return *this;
    }
    bool ready()const{return ready_;}Refusal refusal()const{return refusal_;}
    const Certificate& certificate()const{return certificate_;}
    const View& proposedView()const{return next_;}
    std::size_t preparedBytes()const{return preparedBytes_;}
};
inline bool assemble(const Cut& live,const View& old,const Recipe& r,View& next)
{
    if(r.appendedPoints.empty()||r.appendedPoints.size()>2||r.replacementCells.empty()
     ||r.replacementCells.size()>O::Caps::core||r.internalFaces.empty()
     ||r.internalFaces.size()>O::Caps::faces)return false;
    std::set<label> core(old.core.begin(),old.core.end()),slots(r.replacementCells.begin(),r.replacementCells.end());
    if(slots.size()!=r.replacementCells.size())return false;
    label appended=live.nCells;
    for(label c:slots)if(c<0||(c<live.nCells&&!core.count(c))||(c>=live.nCells&&c!=appended++))return false;
    next=old;next.core.assign(slots.begin(),slots.end());
    next.cells.erase(std::remove_if(next.cells.begin(),next.cells.end(),
        [&](const O::Cell& c){return core.count(c.native);}),next.cells.end());
    for(const auto& ap:r.appendedPoints)
    {
        if(!O::finite(ap))return false;
        O::Alias a;a.representative=live.points.size()+next.aliases.size()-old.aliases.size();
        a.world=ap;a.members.push_back(a.representative);a.rawWorld.push_back(ap);next.aliases.push_back(std::move(a));
    }
    std::set<label> exterior;
    for(auto& f:next.faces)
    {
        const bool a=core.count(f.owner),b=f.internal&&core.count(f.neighbour);
        if(a&&b)continue;
        if(a||b)
        {
            auto target=r.exteriorCell.find(f.native);
            if(target==r.exteriorCell.end()||!slots.count(target->second)||O::protectedRole(f))return false;
            exterior.insert(f.native);
            if(core.count(f.rawOwner))f.rawOwner=target->second;
            if(core.count(f.rawNeighbour))f.rawNeighbour=target->second;
        }
    }
    if(exterior.size()!=r.exteriorCell.size())return false;
    next.faces.erase(std::remove_if(next.faces.begin(),next.faces.end(),
        [&](const O::Face& f){return core.count(f.owner)&&(f.internal&&core.count(f.neighbour));}),next.faces.end());
    label newFace=live.faces.size();
    for(const auto& nf:r.internalFaces)
    {
        if(nf.ring.size()<3||nf.ring.size()>O::Caps::ring||!slots.count(nf.owner)
         ||!slots.count(nf.neighbour)||nf.owner==nf.neighbour)return false;
        O::Face f;f.native=newFace++;f.rawOwner=nf.owner;f.rawNeighbour=nf.neighbour;
        f.rawRing=nf.ring;f.internal=f.determinantSelected=f.metric=true;next.faces.push_back(std::move(f));
    }
    std::map<label,label> reps;for(const auto& a:next.aliases)for(label p:a.members)
        if(!reps.emplace(p,a.representative).second)return false;
    for(auto& f:next.faces)
    {
        f.owner=f.rawOwner;f.neighbour=f.rawNeighbour;f.ring.clear();
        for(label p:f.rawRing){auto a=reps.find(p);if(a==reps.end())return false;f.ring.push_back(a->second);}
        if(f.internal&&f.owner>f.neighbour){std::swap(f.owner,f.neighbour);std::reverse(f.ring.begin(),f.ring.end());}
        f.ownerOrdinal=f.owner;f.neighbourOrdinal=f.neighbour;
    }
    std::sort(next.faces.begin(),next.faces.end(),[](const O::Face& a,const O::Face& b)
    {
        if(a.internal!=b.internal)return a.internal;
        if(a.internal)return std::make_pair(a.owner,a.neighbour)<std::make_pair(b.owner,b.neighbour);
        return std::make_pair(std::max(label(0),a.patch),a.native)<std::make_pair(std::max(label(0),b.patch),b.native);
    });
    for(std::size_t i=0;i<next.faces.size();++i)
    {
        auto& f=next.faces[i];f.ordinal=i;
        if(i&&f.internal&&next.faces[i-1].internal&&f.owner==next.faces[i-1].owner
         &&f.neighbour==next.faces[i-1].neighbour)return false;
    }
    for(label c:slots){O::Cell row;row.native=c;row.ordinal=c;next.cells.push_back(std::move(row));}
    for(auto& c:next.cells)
    {
        c.ordinal=c.native;c.faces.clear();
        if(slots.count(c.native))c.rawFaces.clear();
        for(const auto& f:next.faces)if(f.owner==c.native)c.faces.push_back(f.native);
        for(const auto& f:next.faces)if(f.internal&&f.neighbour==c.native)c.faces.push_back(f.native);
        if(slots.count(c.native)){c.rawFaces=c.faces;std::sort(c.rawFaces.begin(),c.rawFaces.end());}
    }
    next.nativeCells=appended;next.nativePoints=live.points.size()+r.appendedPoints.size();
    next.payloadBytes=old.payloadBytes+r.replacementCells.size()*sizeof(O::Cell)
        +r.internalFaces.size()*(sizeof(O::Face)+O::Caps::ring*2*sizeof(label))
        +r.appendedPoints.size()*(sizeof(O::Alias)+sizeof(point)+sizeof(label));
    if(next.payloadBytes>O::Caps::payload)return false;
    return next.faces.size()<=O::Caps::faces&&next.aliases.size()<=O::Caps::points;
}
inline bool prepare(Cut& live,const View& view,const Authority& authority,label legacyAccepted,
                    const std::vector<std::vector<label>>& currentFaces,Recipe recipe,
                    const PublicationBudget& publicationBudget,const std::function<bool()>& clock,Prepared& out)
{
    out=Prepared();
    auto no=[&](Refusal why){out=Prepared();out.refusal_=why;return false;};
    auto awake=[&](){try{return publicationBudget.available()&&clock&&clock()&&O::available(authority);}catch(...){return false;}};
    if(view.core.empty()||view.core.size()>O::Caps::core||recipe.replacementCells.empty()
     ||recipe.replacementCells.size()>O::Caps::core||recipe.appendedPoints.size()>2
     ||recipe.internalFaces.size()>O::Caps::faces)return no(Refusal::capacity);
    if(!awake())return no(Refusal::clock);
    try
    {
        if(!O::current(view,live,authority,legacyAccepted,currentFaces))return no(Refusal::stale);
        if(!assemble(live,view,recipe,out.next_))return no(Refusal::shape);
        // certify append-only APs cannot enter/change any global native alias class.
        for(const auto& p:recipe.appendedPoints)
        {
            for(std::size_t k=0;k<live.points.size();++k)
            {if((k&255)==0&&!awake())return no(Refusal::clock);
             if(!O::finite(live.points[k])||magSqr(p-live.points[k])<=sqr(scalar(3e-6)))return no(Refusal::alias);}
        }
        if(recipe.appendedPoints.size()==2&&magSqr(recipe.appendedPoints[0]-recipe.appendedPoints[1])<=sqr(scalar(3e-6)))
            return no(Refusal::alias);
        if(!O::evaluate(view,{},awake,out.certificate_.before)||!O::installedBinding(view,out.certificate_.before))
            return no(Refusal::stale);
        if(!exactCoverage(view,out.next_,awake,out.certificate_))return no(Refusal::coverage);
        if(!O::evaluate(out.next_,{},awake,out.certificate_.after,
          [&](const O::GeometryMesh& g,const std::map<label,label>& cells,const std::map<label,label>& faces)
          {return installedGeometry(g,cells,faces,out.next_,awake);}))return no(Refusal::geometry);
        if(!hardLineage(view,out.next_,out.certificate_))return no(Refusal::quality);
        out.certificate_.installedArithmetic=true;
        auto originPermission=[&](const std::vector<label>& parents)
        {
            std::vector<O::Origin> records;
            for(label p:parents)
            {if(!awake()||!authority.recordedParent(p))return false;records.push_back(authority.normalOriginFromSnapshot(p));}
            return authority.completeOrigins(records);
        };
        // Source lineage is staged too: callbacks/allocations can never publish
        // it ahead of geometry. The heap owner remains stable when Prepared moves.
        out.lineageRevision_=live.completeCavityLineage.revision();
        out.lineageShadow_=std::make_unique<Lineage>(live.completeCavityLineage);
        if(!out.lineageShadow_->prepareReplacement(view.core,recipe.replacementCells,originPermission,awake,out.lineage_))
            return no(Refusal::source);
        out.preparedBytes_=out.lineageShadow_->keyCapacityBytes()+out.lineageShadow_->compositeCapacityBytes()
            +out.lineage_.densePreparedBytes()+out.lineage_.compositePreparedBytes();
        const std::size_t cells=out.next_.nativeCells,faces=live.faces.size()+recipe.internalFaces.size();
        std::size_t& bytes=out.preparedBytes_;
        if(live.cellAlive.size()!=std::size_t(live.nCells)||live.cellOrigAlive.size()!=std::size_t(live.nCells)
         ||live.cellRefVol.size()!=std::size_t(live.nCells)||live.cellRefCentre.size()!=std::size_t(live.nCells)
         ||live.cellLevel.size()!=std::size_t(live.nCells)||live.cellRank.size()!=std::size_t(live.nCells)
         ||live.cellLeaf.size()!=std::size_t(live.nCells)||live.cellPiece.size()!=std::size_t(live.nCells)
         ||live.owner.size()!=live.faces.size()||live.neighbour.size()!=live.faces.size()
         ||live.patch.size()!=live.faces.size()||live.zone.size()!=live.faces.size()
         ||live.faceAlive.size()!=live.faces.size())return no(Refusal::shape);
#define CAV_STAGE(member,input,size) if(!awake()||!out.member.prepare(live.input,size,bytes))return no(Refusal::capacity)
        CAV_STAGE(points_,points,live.points.size()+recipe.appendedPoints.size());
        CAV_STAGE(faces_,faces,faces);CAV_STAGE(own_,owner,faces);CAV_STAGE(nei_,neighbour,faces);
        CAV_STAGE(patch_,patch,faces);CAV_STAGE(zone_,zone,faces);CAV_STAGE(faceAlive_,faceAlive,faces);
        CAV_STAGE(cellAlive_,cellAlive,cells);CAV_STAGE(origAlive_,cellOrigAlive,cells);
        CAV_STAGE(refVol_,cellRefVol,cells);CAV_STAGE(refCentre_,cellRefCentre,cells);
        CAV_STAGE(level_,cellLevel,cells);CAV_STAGE(rank_,cellRank,cells);
        CAV_STAGE(leaf_,cellLeaf,cells);CAV_STAGE(piece_,cellPiece,cells);
        if(!live.coneEvidenceParent.empty())
        {if(live.coneEvidenceParent.size()!=std::size_t(live.nCells))return no(Refusal::shape);CAV_STAGE(evidenceParent_,coneEvidenceParent,cells);}
        if(!live.coneEvidenceBase.empty())
        {if(live.coneEvidenceBase.size()!=live.faces.size())return no(Refusal::shape);CAV_STAGE(evidenceBase_,coneEvidenceBase,faces);}
#undef CAV_STAGE
        if(live.completeCavityState)return no(Refusal::stale); // at most one replacement/rank
        for(const auto& cell:view.cells)
        {
            const label c=cell.native;
            out.metadata_.push_back({c,live.cellLevel[c],live.cellRank[c],live.cellLeaf[c],
                live.cellPiece[c],live.cellAlive[c],live.cellOrigAlive[c],live.cellRefVol[c],live.cellRefCentre[c]});
        }
        // Every allocation/source callback precedes all live publication.
        const std::size_t journalBytes=view.payloadBytes+out.next_.payloadBytes
            +(out.certificate_.before.cells.size()+out.certificate_.after.cells.size())*512
            +(out.certificate_.before.faces.size()+out.certificate_.after.faces.size())*256;
        if(journalBytes>O::Caps::payload)return no(Refusal::capacity);
        out.journal_=std::make_shared<dcCartCutCells::writerCompleteCavityState>();
        out.journal_->oldView=view;out.journal_->nextView=out.next_;
        out.journal_->certificate=out.certificate_;out.journal_->authority=authority;
        out.journal_->oldNativePoints=live.points.size();out.journal_->bytes=journalBytes;
        for(const auto& f:view.faces)if(std::binary_search(view.core.begin(),view.core.end(),f.owner)
          &&f.internal&&std::binary_search(view.core.begin(),view.core.end(),f.neighbour))out.removedFaces_.push_back(f.native);
        out.owner_=&live;out.old_=view;out.recipe_=std::move(recipe);
        out.publicationBudget_=publicationBudget;
        out.oldCells_=live.nCells;out.oldPoints_=live.points.size();out.oldFaces_=live.faces.size();
        if(!awake())return no(Refusal::clock);out.refusal_=Refusal::none;out.ready_=true;return true;
    }
    catch(...){return no(Refusal::allocation);}
}
inline bool commit(Cut& live,const Authority& authority,const std::function<bool()>& clock,Prepared& p)
{
    if(!p.ready_||p.owner_!=&live||!p.lineageShadow_||!p.journal_||live.completeCavityState||p.lineageRevision_!=live.completeCavityLineage.revision()||p.oldCells_!=live.nCells
     ||p.oldPoints_!=label(live.points.size())||p.oldFaces_!=label(live.faces.size()))return false;
    auto dependencies=[&]() noexcept
    {
        if(live.completeCavityLineage.revision()!=p.lineageRevision_||live.nGridPoints!=p.old_.nGridPoints
         ||live.nCells!=p.oldCells_||live.points.size()!=std::size_t(p.oldPoints_)
         ||live.faces.size()!=std::size_t(p.oldFaces_))return false;
        for(const auto& m:p.metadata_)
            if(m.cell<0||m.cell>=live.nCells||live.cellAlive[m.cell]!=m.alive
             ||live.cellOrigAlive[m.cell]!=m.original||live.cellLevel[m.cell]!=m.level
             ||live.cellRank[m.cell]!=m.rank||live.cellLeaf[m.cell]!=m.leaf||live.cellPiece[m.cell]!=m.piece
             ||!O::bits(live.cellRefVol[m.cell],m.referenceVolume)
             ||!O::bits(live.cellRefCentre[m.cell],m.referenceCentre))return false;
        for(const auto& f:p.old_.faces)
            if(f.native<0||f.native>=p.oldFaces_||!live.faceAlive[f.native]
             ||live.faces[f.native]!=f.rawRing||live.owner[f.native]!=f.rawOwner
             ||live.neighbour[f.native]!=f.rawNeighbour||live.patch[f.native]!=f.patch||live.zone[f.native]!=f.zone)return false;
        for(const auto& a:p.old_.aliases)for(std::size_t k=0;k<a.members.size();++k)
            if(a.members[k]<0||a.members[k]>=p.oldPoints_||!O::bits(live.points[a.members[k]],a.rawWorld[k]))return false;
        return p.points_.bound(live.points)&&p.faces_.bound(live.faces)&&p.own_.bound(live.owner)
         &&p.nei_.bound(live.neighbour)&&p.patch_.bound(live.patch)&&p.zone_.bound(live.zone)
         &&p.faceAlive_.bound(live.faceAlive)&&p.cellAlive_.bound(live.cellAlive)&&p.origAlive_.bound(live.cellOrigAlive)
         &&p.refVol_.bound(live.cellRefVol)&&p.refCentre_.bound(live.cellRefCentre)
         &&p.level_.bound(live.cellLevel)&&p.rank_.bound(live.cellRank)&&p.leaf_.bound(live.cellLeaf)&&p.piece_.bound(live.cellPiece)
         &&(live.coneEvidenceParent.empty()||p.evidenceParent_.bound(live.coneEvidenceParent))
         &&(live.coneEvidenceBase.empty()||p.evidenceBase_.bound(live.coneEvidenceBase));
    };
    auto awake=[&](){try{return p.publicationBudget_.available()&&clock&&clock()&&O::available(authority)&&dependencies();}catch(...){return false;}};
    auto source=[&](const std::vector<label>& ids)
    {
        if(authority.sourceFingerprint.raw!=p.old_.source.raw||authority.sourceFingerprint.roles!=p.old_.source.roles
         ||authority.sourceFingerprint.materialKey!=p.old_.source.materialKey||authority.sourceFingerprint.material!=p.old_.source.material
         ||authority.sourceFingerprint.producer!=p.old_.source.producer
         ||authority.sourceFingerprint.boundaryMembership!=p.old_.source.boundaryMembership)return false;
        std::vector<O::Origin> origins;
        for(label id:ids){if(!authority.recordedParent(id))return false;origins.push_back(authority.normalOriginFromSnapshot(id));}
        return authority.completeOrigins(origins)&&dependencies();
    };
    if(!awake())return false;
    // Commit only the owned source shadow. Every callback is before live publication.
    if(!p.lineageShadow_->commitReplacement(p.lineage_,source,awake))return false;
    // Recheck global point input after the last external callback; this loop
    // performs no allocation or callback and proves the global alias source input.
    const auto& earliest=*live.writerBaseline->checkpoint;
    if(earliest.points.size()!=live.points.size())return false;
    for(std::size_t i=0;i<live.points.size();++i)
    {
        if((i&4095)==0&&!p.publicationBudget_.available())
        {p.ready_=false;p.refusal_=Refusal::clock;return false;}
        if(!O::bits(live.points[i],earliest.points[i])){p.ready_=false;return false;}
    }
    if(!dependencies()){p.ready_=false;return false;}
    // No arbitrary callback can mutate the now-verified global point input.
    if(!p.publicationBudget_.available())
    {p.ready_=false;p.refusal_=Refusal::clock;return false;}
    live.completeCavityLineage=std::move(*p.lineageShadow_);
    // First live source write occurred above. All remaining operations are capacity-
    // preflighted default/copy constructions, vector swaps and bounded writes.
    // No allocator, callback, clock, logging or MPI call follows.
    p.points_.publish(live.points);p.faces_.publish(live.faces);
    p.own_.publish(live.owner);p.nei_.publish(live.neighbour);p.patch_.publish(live.patch);p.zone_.publish(live.zone);
    p.faceAlive_.publish(live.faceAlive);p.cellAlive_.publish(live.cellAlive);p.origAlive_.publish(live.cellOrigAlive);
    p.refVol_.publish(live.cellRefVol);p.refCentre_.publish(live.cellRefCentre);p.level_.publish(live.cellLevel);
    p.rank_.publish(live.cellRank);p.leaf_.publish(live.cellLeaf);p.piece_.publish(live.cellPiece);
    if(!live.coneEvidenceParent.empty())p.evidenceParent_.publish(live.coneEvidenceParent);
    if(!live.coneEvidenceBase.empty())p.evidenceBase_.publish(live.coneEvidenceBase);
    for(std::size_t i=0;i<p.recipe_.appendedPoints.size();++i)live.points[p.oldPoints_+i]=p.recipe_.appendedPoints[i];
    for(label f:p.removedFaces_)live.faceAlive[f]=false;
    for(const auto& x:p.recipe_.exteriorCell)
    {
        if(std::binary_search(p.old_.core.begin(),p.old_.core.end(),live.owner[x.first]))live.owner[x.first]=x.second;
        if(std::binary_search(p.old_.core.begin(),p.old_.core.end(),live.neighbour[x.first]))live.neighbour[x.first]=x.second;
    }
    for(std::size_t i=0;i<p.recipe_.internalFaces.size();++i)
    {
        auto& f=p.recipe_.internalFaces[i];const label id=p.oldFaces_+i;
        live.faces[id].swap(f.ring);live.owner[id]=f.owner;live.neighbour[id]=f.neighbour;
        live.patch[id]=live.zone[id]=-1;live.faceAlive[id]=true;
        if(!live.coneEvidenceBase.empty())live.coneEvidenceBase[id]=-1;
    }
    const label anchor=p.old_.core.front();
    const scalar referenceVolume=live.cellRefVol[anchor];const point referenceCentre=live.cellRefCentre[anchor];
    const label level=live.cellLevel[anchor],rank=live.cellRank[anchor];
    const std::uint64_t leaf=live.cellLeaf[anchor],piece=live.cellPiece[anchor];
    for(label c:p.old_.core){live.cellAlive[c]=false;if(!live.coneEvidenceParent.empty())live.coneEvidenceParent[c]=-1;}
    for(label c:p.recipe_.replacementCells)
    {
        live.cellAlive[c]=true;live.cellOrigAlive[c]=false;live.cellRefVol[c]=referenceVolume;live.cellRefCentre[c]=referenceCentre;
        live.cellLevel[c]=level;live.cellRank[c]=rank;live.cellLeaf[c]=leaf;live.cellPiece[c]=piece;
        if(!live.coneEvidenceParent.empty())live.coneEvidenceParent[c]=-1;
    }
    // The enclosing normal pipeline subsequently performs its existing geometry
    // rekey to give unique shape IDs. Lineage carries actual
    // complete origins; these inherited hints are never source permission.
    live.nCells=p.next_.nativeCells;live.completeCavityState.swap(p.journal_);
    p.ready_=false;return true;
}
} }
#endif
