#ifndef dcCartCompleteCavityLocalOracle_H
#define dcCartCompleteCavityLocalOracle_H
#include "dcCartCutCells.H"
#include "dcCartCompleteCavityAuthority.H"
#include "dcCartWeightUnionPreview.H"
#include "primitiveMesh.H"
#include "primitiveMeshTools.H"
#include "bitSet.H"
#include <algorithm>
#include <map>
#include <set>
#include <vector>
#include <cstdint>
#include <atomic>
#include <cstring>
namespace Foam { namespace dcCartCompleteCavityLocalOracle
{
using Cut=dcCartCutCells::cutMesh;
using Bindings=dcCartCutCells::writerBindings;
using Authority=dcCartCutCells::completeCavityAuthority;
using Origin=dcCartCutCells::weightUnionOrigin;
using Fingerprint=dcCartCompleteCavityOriginCore::Fingerprint;
inline bool finite(scalar x){return std::isfinite(x);}
inline bool finite(const point& p){return finite(p.x())&&finite(p.y())&&finite(p.z());}
inline bool bits(scalar a,scalar b){return finite(a)&&finite(b)&&!std::memcmp(&a,&b,sizeof(scalar));}
inline bool bits(const point& a,const point& b)
{return bits(a.x(),b.x())&&bits(a.y(),b.y())&&bits(a.z(),b.z());}
inline bool available(const Authority& a)
{try{if(!a.ready()||!a.remainingSeconds)return false;const scalar r=a.remainingSeconds();return finite(r)&&r>0;}catch(...){return false;}}
struct Caps
{
    static constexpr std::size_t core=128,outside=256,secondHalo=512;
    static constexpr std::size_t faces=2048,points=8192,ring=8;
    static constexpr std::size_t aliasMembers=8192,payload=16u*1024u*1024u;
    static constexpr std::size_t normalIndexBytes=64u*1024u*1024u;
};
enum class Refusal
{
    none,missingSource,clock,coreCap,haloCap,faceCap,pointCap,shape,
    mapping,nonfinite,coupled,zone,rawDependency,aliasDependency,
    installedBinding,legacyJournal,protectedJournal,unknownEndpoint
};
struct Cell
{
    label native=-1,ordinal=-1;
    std::vector<label> rawFaces;
    std::vector<label> faces; // native IDs, owner faces then neighbour faces
    point centre=point::zero;scalar volume=0,determinant=0;
};
struct Face
{
    label native=-1,ordinal=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    label rawOwner=-1,rawNeighbour=-1,ownerOrdinal=-1,neighbourOrdinal=-1;
    bool internal=false,coupled=false,determinantSelected=false,metric=false;
    bool immutablePhysicalSourceRole=false; // normal source facet; never an internal-zone exemption
    std::vector<label> rawRing,ring; // native raw / normal alias representatives
    vector area=vector::zero;point centre=point::zero;
    std::array<scalar,4> quality{{1,1,0,0}};
    std::string patchRole;
};
struct Alias
{
    label representative=-1;
    point world=point::zero;
    std::vector<label> members;
    std::vector<point> rawWorld;
};
struct View
{
    Refusal refusal=Refusal::missingSource;
    std::uint64_t epoch=0;
    Fingerprint source;
    label actualRank=-1,nativeCells=-1,nativePoints=-1,nGridPoints=-1;
    std::vector<label> core,outside,secondHalo;
    std::vector<Origin> origins;
    std::vector<Cell> cells;
    std::vector<Face> faces;
    std::vector<Alias> aliases;
    std::size_t payloadBytes=0;
    bool ready()const{return refusal==Refusal::none&&epoch&&!core.empty();}
};
// Geometric endpoint-only cells below are NEVER a quality/source permission.
// Original internal faces stay internal even when an endpoint is outside the
// closed stencil; this preserves owner/neighbour accumulation and D-selected
// roles instead of relabelling them as a tiny artificial boundary.
class GeometryMesh final:public primitiveMesh
{
    const pointField& p_;const faceList& f_;const labelList& o_;const labelList& n_;
public:
    GeometryMesh(const pointField& p,const faceList& f,const labelList& o,
                 const labelList& n,label cells)
    :primitiveMesh(p.size(),n.size(),f.size(),cells),p_(p),f_(f),o_(o),n_(n){}
    const pointField& points()const override{return p_;}
    const pointField& oldPoints()const override{return p_;}
    const faceList& faces()const override{return f_;}
    const labelList& faceOwner()const override{return o_;}
    const labelList& faceNeighbour()const override{return n_;}
    static scalar actualPlanarCosAngle(){return planarCosAngle_;}
};
struct Values
{
    Refusal refusal=Refusal::shape;
    std::map<label,std::pair<point,scalar>> cells;
    std::map<label,scalar> determinant;
    std::map<label,std::pair<point,vector>> faces;
    std::map<label,std::array<scalar,4>> metrics;
    void clear(Refusal r){cells.clear();determinant.clear();faces.clear();metrics.clear();refusal=r;}
};
inline bool originalMetrics(const Face& role,const GeometryMesh& mesh,label localFace,
                            label own,label nei,std::array<scalar,4>& q)
{
    if(role.coupled||own<0||own>=mesh.nCells()
     ||(role.internal&&(nei<0||nei>=mesh.nCells())))return false;
    const auto& sf=mesh.faceAreas();const auto& fc=mesh.faceCentres();
    const auto& cc=mesh.cellCentres();const auto& v=mesh.cellVolumes();
    if(!finite(sf[localFace])||!finite(fc[localFace])||!finite(cc[own])
     ||!finite(v[own])||v[own]<=VSMALL||mag(sf[localFace])<=1e-12)return false;
    q={{1,1,0,0}};
    if(role.internal)
    {
        if(!finite(cc[nei])||!finite(v[nei])||v[nei]<=VSMALL)return false;
        const scalar a=mag(sf[localFace]&(fc[localFace]-cc[own]));
        const scalar b=mag(sf[localFace]&(cc[nei]-fc[localFace]));
        q[0]=min(a,b)/(a+b+VSMALL);
        q[1]=min(v[own],v[nei])/(max(v[own],v[nei])+VSMALL);
        q[2]=primitiveMeshTools::faceSkewness(mesh.faces(),mesh.points(),fc,sf,
                                            localFace,cc[own],cc[nei]);
        const scalar cosine=primitiveMeshTools::faceOrthogonality
            (cc[own],cc[nei],sf[localFace]);
        q[3]=std::acos(max(scalar(-1),min(scalar(1),cosine)))*180/constant::mathematical::pi;
    }
    else q[2]=primitiveMeshTools::boundaryFaceSkewness
        (mesh.faces(),mesh.points(),fc,sf,localFace,cc[own]);
    for(scalar x:q)if(!finite(x)||x<0)return false;
    return true;
}
// Evaluates complete cell recipes, retaining actual global writer face order.
// Raw point overrides are proposal-only; final installed guards still decide.
inline bool evaluate(const View& view,const std::map<label,point>& overridePoints,
                     const std::function<bool()>& clock,Values& out,
                     const std::function<bool(const GeometryMesh&,const std::map<label,label>&,
                                              const std::map<label,label>&)>& installedGuard={})
{
    out.clear(Refusal::shape);
    struct FailureReset
    {
        Values& out;bool success=false;
        ~FailureReset(){if(!success){const auto why=out.refusal;out.clear(why);}}
    } reset{out};
    if(!view.ready()||view.core.size()>Caps::core
     ||view.outside.size()>Caps::outside||view.secondHalo.size()>Caps::secondHalo
     ||view.faces.empty()||view.faces.size()>Caps::faces
     ||view.aliases.empty()||view.aliases.size()>Caps::points)return false;
    auto awake=[&](){try{return clock&&clock();}catch(...){return false;}};
    if(!awake()){out.clear(Refusal::clock);return false;}
    try
    {
        std::map<label,label> cellOrdinal,cellLocal,pointLocal,faceLocal;
        std::set<label> full;
        for(const auto& c:view.cells)
        {
            if(c.native<0||c.ordinal<0||!full.insert(c.native).second)return false;
            cellOrdinal.emplace(c.native,c.ordinal);
        }
        for(const auto& f:view.faces)
        {
            if(f.ordinal<0||f.coupled||f.determinantSelected!=(f.internal||f.coupled)||f.ring.size()<3||f.ring.size()>Caps::ring
             ||f.owner<0||f.ownerOrdinal<0||(f.internal&&(f.neighbour<0||f.neighbourOrdinal<0)))return false;
            // Endpoint ordinals are real global native-slot ordering. Only
            // full geometry records below receive quality/lineage verdicts.
            auto o=cellOrdinal.find(f.owner);
            if(o!=cellOrdinal.end()&&o->second!=f.ownerOrdinal)return false;
            cellOrdinal.emplace(f.owner,f.ownerOrdinal);
            if(f.internal)
            {
                auto n=cellOrdinal.find(f.neighbour);
                if(n!=cellOrdinal.end()&&n->second!=f.neighbourOrdinal)return false;
                cellOrdinal.emplace(f.neighbour,f.neighbourOrdinal);
            }
        }
        std::vector<std::pair<label,label>> order;
        for(const auto& x:cellOrdinal)order.emplace_back(x.second,x.first);
        std::sort(order.begin(),order.end());
        for(std::size_t i=0;i<order.size();++i)
        {
            if(i&&order[i].first==order[i-1].first)return false;
            cellLocal.emplace(order[i].second,label(i));
        }
        pointField pts(view.aliases.size());
        for(std::size_t i=0;i<view.aliases.size();++i)
        {
            const auto& a=view.aliases[i];point p=a.world;
            auto changed=overridePoints.find(a.representative);
            if(changed!=overridePoints.end())p=changed->second;
            if(a.representative<0||!finite(p)||!pointLocal.emplace(a.representative,label(i)).second)return false;
            pts[i]=p;
        }
        // This first geometry grammar preserves literal physical facets.
        // Flat-boundary point motion has a separate footprint contract and is
        // deliberately unavailable to this complete-cavity view.
        for(const auto& f:view.faces)if(!f.internal)
            for(label p:f.ring)
            {
                auto changed=overridePoints.find(p);
                if(changed!=overridePoints.end()
                 &&(!pointLocal.count(p)||!bits(changed->second,view.aliases[pointLocal.at(p)].world)))return false;
            }
        // Do not accept an override for an unknown alias or a nonfinite value.
        for(const auto& x:overridePoints)if(!pointLocal.count(x.first)||!finite(x.second))return false;
        std::vector<const Face*> ordered;
        for(const auto& f:view.faces)ordered.push_back(&f);
        std::sort(ordered.begin(),ordered.end(),[](const Face* a,const Face* b){return a->ordinal<b->ordinal;});
        label nInt=0;
        for(std::size_t i=0;i<ordered.size();++i)
        {
            if(i&&ordered[i]->ordinal==ordered[i-1]->ordinal)return false;
            if(ordered[i]->internal)
            {if(nInt!=label(i))return false;++nInt;}
        }
        faceList faces(ordered.size());labelList own(ordered.size()),nei(nInt);
        bitSet selected(ordered.size());
        for(std::size_t i=0;i<ordered.size();++i)
        {
            if((i&63)==0&&!awake()){out.clear(Refusal::clock);return false;}
            const auto& f=*ordered[i];face& ring=faces[i];ring.setSize(f.ring.size());
            for(std::size_t k=0;k<f.ring.size();++k)
            {auto p=pointLocal.find(f.ring[k]);if(p==pointLocal.end())return false;ring[k]=p->second;}
            own[i]=cellLocal.at(f.owner);
            if(f.internal)nei[i]=cellLocal.at(f.neighbour);
            if(f.determinantSelected)selected.set(i);
            if(!faceLocal.emplace(f.native,label(i)).second)return false;
        }
        GeometryMesh mesh(pts,faces,own,nei,order.size());
        const auto& fc=mesh.faceCentres();const auto& sf=mesh.faceAreas();
        const auto& cc=mesh.cellCentres();const auto& vol=mesh.cellVolumes();
        const auto d=primitiveMeshTools::cellDeterminant(mesh,Vector<label>(1,1,1),sf,selected);
        for(const auto& c:view.cells)
        {
            const label local=cellLocal.at(c.native);
            if(!finite(cc[local])||!finite(vol[local])||!finite(d()[local])||vol[local]<=VSMALL)return false;
            out.cells.emplace(c.native,std::make_pair(cc[local],vol[local]));
            out.determinant.emplace(c.native,d()[local]);
        }
        for(const auto& f:view.faces)
        {
            const label local=faceLocal.at(f.native);
            if(!finite(fc[local])||!finite(sf[local]))return false;
            out.faces.emplace(f.native,std::make_pair(fc[local],sf[local]));
            if(f.metric)
            {
                if(!full.count(f.owner)||(f.internal&&!full.count(f.neighbour)))
                {out.clear(Refusal::unknownEndpoint);return false;}
                std::array<scalar,4> q;
                if(!originalMetrics(f,mesh,local,own[local],f.internal?nei[local]:-1,q))return false;
                out.metrics.emplace(f.native,q);
            }
        }
        if(installedGuard&&!installedGuard(mesh,cellLocal,faceLocal))return false;
        if(!awake()){out.clear(Refusal::clock);return false;}
        out.refusal=Refusal::none;reset.success=true;return true;
    }
    catch(...){out.clear(Refusal::shape);return false;}
}
inline bool installedBinding(const View& v,const Values& values)
{
    if(values.refusal!=Refusal::none||values.cells.size()!=v.cells.size()
     ||values.faces.size()!=v.faces.size())return false;
    for(const auto& c:v.cells)
    {
        auto x=values.cells.find(c.native);auto d=values.determinant.find(c.native);
        if(x==values.cells.end()||d==values.determinant.end()
         ||!bits(x->second.first,c.centre)||!bits(x->second.second,c.volume)
         ||!bits(d->second,c.determinant))return false;
    }
    for(const auto& f:v.faces)
    {
        auto x=values.faces.find(f.native);
        if(x==values.faces.end()||!bits(x->second.first,f.centre)||!bits(x->second.second,f.area))return false;
        if(f.metric)
        {
            auto q=values.metrics.find(f.native);if(q==values.metrics.end())return false;
            for(unsigned k=0;k<4;++k)if(!bits(q->second[k],f.quality[k]))return false;
        }
    }
    return true;
}
inline bool inverse(const std::vector<label>& map,label size,std::vector<label>& out)
{
    out.assign(size,-1);
    for(label i=0;i<label(map.size());++i)if(map[i]>=0)
    {
        if(map[i]>=size||out[map[i]]>=0){out.clear();return false;}
        out[map[i]]=i;
    }
    if(std::find(out.begin(),out.end(),label(-1))!=out.end()){out.clear();return false;}
    return true;
}
struct NormalIndex
{
    const polyMesh* actual=nullptr;
    const Bindings* bindings=nullptr;
    std::vector<label> nativeCell,nativeFace,nativePoint;
    bool ready=false;
};
inline bool produceIndex(const Cut& raw,const polyMesh& actual,const Bindings& bindings,
                         const Authority& authority,NormalIndex& out)
{
    out=NormalIndex();
    if(!available(authority)||bindings.cell.size()!=std::size_t(raw.nCells)
     ||bindings.face.size()!=raw.faces.size()||bindings.point.size()!=raw.points.size()
     ||bindings.alias.size()!=raw.points.size())return false;
    const std::size_t count=std::size_t(actual.nCells())+std::size_t(actual.nFaces())+std::size_t(actual.nPoints());
    if(count>Caps::normalIndexBytes/sizeof(label))return false;
    if(!inverse(bindings.cell,actual.nCells(),out.nativeCell)
     ||!available(authority)||!inverse(bindings.face,actual.nFaces(),out.nativeFace)
     ||!available(authority)||!inverse(bindings.point,actual.nPoints(),out.nativePoint)
     ||!available(authority)){out=NormalIndex();return false;}
    out.actual=&actual;out.bindings=&bindings;out.ready=true;return true;
}
// Source-zone exterior permission is NOT inferred from empty written zones or
// sourceBase/TRACE. It requires the exact native pre-cone facet handle, patch
// and zone in this live physical owner's fresh complete normal source record.
// Read-only halo permission does not add any origins to the removed core.
inline bool immutablePhysicalRole(const Cut& raw,const Authority& authority,const Face& f)
{
    if(f.internal||f.coupled||f.patch<0||f.zone<0||f.native<0
     ||f.native>=label(raw.faces.size())||f.owner<0||f.owner>=raw.nCells
     ||raw.patch[f.native]!=f.patch||raw.zone[f.native]!=f.zone
     ||(raw.owner[f.native]>=0&&raw.neighbour[f.native]>=0))return false;
    std::vector<Origin> origins;
    if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(raw,authority,{f.owner},origins))return false;
    std::size_t matches=0;
    for(const auto& origin:origins)for(const auto& facet:origin.sourceFacets)
        if(facet[0]==f.native&&facet[1]==f.patch&&facet[2]==f.zone)++matches;
    // Multiple origins can share this exact facet; they must agree literally.
    return matches>0;
}
inline bool protectedRole(const Face& f)
{
    return f.coupled||f.zone<-1||f.patch<-1||(f.internal&&f.patch!=-1)
        ||(!f.internal&&f.patch<0)||(f.zone>=0&&
        (f.internal||!f.immutablePhysicalSourceRole||f.patch<0));
}
// Normal producer entry: consumes an ACTUAL installed mesh and its exact
// numeric writer bindings. No TRACE, guessed labels or tiny patch permissions.
inline bool capture(const Cut& raw,const polyMesh& actual,const Bindings& binding,
                    const Authority& authority,const std::vector<label>& core,
                    const std::vector<std::vector<label>>& rawCellFaces,
                    const NormalIndex& index,std::uint64_t epoch,View& out)
{
    out=View();
    struct FailureReset
    {
        View& out;bool success=false;
        ~FailureReset(){if(!success){const auto why=out.refusal;out=View();out.refusal=why;}}
    } reset{out};
    if(core.empty()||core.size()>Caps::core){out.refusal=Refusal::coreCap;return false;}
    if(!epoch||!available(authority)||!dcCartCompleteCavityOriginCore::validFingerprint(authority.sourceFingerprint))
    {out.refusal=Refusal::missingSource;return false;}
    try
    {
        if(binding.cell.size()!=std::size_t(raw.nCells)||binding.face.size()!=raw.faces.size()
         ||binding.point.size()!=raw.points.size()||binding.alias.size()!=raw.points.size()
         ||raw.cellAlive.size()!=std::size_t(raw.nCells)||raw.cellRank.size()!=std::size_t(raw.nCells)
         ||rawCellFaces.size()!=std::size_t(raw.nCells)||raw.faceAlive.size()!=raw.faces.size()
         ||raw.owner.size()!=raw.faces.size()||raw.neighbour.size()!=raw.faces.size()
         ||raw.patch.size()!=raw.faces.size()||raw.zone.size()!=raw.faces.size())
        {out.refusal=Refusal::mapping;return false;}
        if(!index.ready||index.actual!=&actual||index.bindings!=&binding
         ||index.nativeCell.size()!=std::size_t(actual.nCells())
         ||index.nativeFace.size()!=std::size_t(actual.nFaces())
         ||index.nativePoint.size()!=std::size_t(actual.nPoints()))
        {out.refusal=Refusal::mapping;return false;}
        const auto& nativeCell=index.nativeCell;const auto& nativeFace=index.nativeFace;
        const auto& nativePoint=index.nativePoint;
        const label rank=Pstream::myProcNo();
        std::set<label> coreSet(core.begin(),core.end()),outside,second,full,faceSet,metricSet;
        if(coreSet.size()!=core.size()){out.refusal=Refusal::shape;return false;}
        auto expand=[&](const std::set<label>& cells,std::set<label>& destination,std::size_t cap)->bool
        {
            for(label c:cells)
            {
                if(!available(authority)){out.refusal=Refusal::clock;return false;}
                if(c<0||c>=raw.nCells||!raw.cellAlive[c]||raw.cellRank[c]!=rank||binding.cell[c]<0)
                {out.refusal=Refusal::mapping;return false;}
                for(label f:actual.cells()[binding.cell[c]])
                {
                    if(dcCartWeightUnionPreview::coupled(actual,f))
                    {out.refusal=Refusal::coupled;return false;}
                    if(f<actual.nInternalFaces())
                    {
                        const label a=nativeCell[actual.faceOwner()[f]],b=nativeCell[actual.faceNeighbour()[f]];
                        const label n=a==c?b:a;
                        if(!coreSet.count(n)&&!cells.count(n))
                        {destination.insert(n);if(destination.size()>cap){out.refusal=Refusal::haloCap;return false;}}
                    }
                }
            }
            return true;
        };
        if(!expand(coreSet,outside,Caps::outside)||!expand(outside,second,Caps::secondHalo))return false;
        for(label c:outside)second.erase(c);
        full=coreSet;full.insert(outside.begin(),outside.end());full.insert(second.begin(),second.end());
        if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(raw,authority,core,out.origins))
        {out.refusal=Refusal::missingSource;return false;}
        for(label c:full)
        {
            if(!available(authority)){out.refusal=Refusal::clock;return false;}
            const label compact=binding.cell[c];
            if(compact<0||raw.cellRank[c]!=rank||dcCartWeightUnionPreview::zonedCell(actual,compact))
            {out.refusal=Refusal::zone;return false;}
            Cell cell;cell.native=c;cell.ordinal=compact;
            for(label f:actual.cells()[compact])
            {
                if(dcCartWeightUnionPreview::coupled(actual,f)){out.refusal=Refusal::coupled;return false;}
                if(dcCartWeightUnionPreview::zonedFace(actual,f)){out.refusal=Refusal::zone;return false;}
                cell.faces.push_back(nativeFace[f]);faceSet.insert(f);
                if(coreSet.count(c)||outside.count(c))metricSet.insert(f);
                if(faceSet.size()>Caps::faces){out.refusal=Refusal::faceCap;return false;}
            }
            if(rawCellFaces[c].empty()||rawCellFaces[c].size()>Caps::faces)
            {out.refusal=Refusal::shape;return false;}
            cell.rawFaces=rawCellFaces[c];std::sort(cell.rawFaces.begin(),cell.rawFaces.end());
            for(label f:cell.rawFaces)
                if(f<0||f>=label(raw.faces.size())||!raw.faceAlive[f]
                 ||(raw.owner[f]!=c&&raw.neighbour[f]!=c)||binding.face[f]<0)
                {out.refusal=Refusal::rawDependency;return false;}
            if(cell.rawFaces.size()!=cell.faces.size())
            {out.refusal=Refusal::rawDependency;return false;}
            cell.centre=actual.cellCentres()[compact];cell.volume=actual.cellVolumes()[compact];
            cell.determinant=dcCartWeightUnionPreview::determinant(actual,compact);
            if(!finite(cell.centre)||!finite(cell.volume)||!finite(cell.determinant)||cell.volume<=VSMALL)
            {out.refusal=Refusal::nonfinite;return false;}
            out.cells.push_back(std::move(cell));
        }
        std::set<label> reps;
        std::set<std::pair<label,label>> internalOrderKeys;
        for(label compact:faceSet)
        {
            if(!available(authority)){out.refusal=Refusal::clock;return false;}
            Face f;f.ordinal=compact;f.native=nativeFace[compact];
            f.rawOwner=raw.owner[f.native];f.rawNeighbour=raw.neighbour[f.native];
            if(f.rawOwner<-1||f.rawNeighbour<-1||f.rawOwner>=raw.nCells||f.rawNeighbour>=raw.nCells)
            {out.refusal=Refusal::mapping;return false;}
            f.patch=raw.patch[f.native];f.zone=raw.zone[f.native];f.rawRing=raw.faces[f.native];
            f.internal=compact<actual.nInternalFaces();
            f.coupled=dcCartWeightUnionPreview::coupled(actual,compact);
            f.determinantSelected=f.internal||f.coupled;f.metric=metricSet.count(compact);
            f.ownerOrdinal=actual.faceOwner()[compact];
            f.neighbourOrdinal=f.internal?actual.faceNeighbour()[compact]:-1;
            f.owner=nativeCell[f.ownerOrdinal];
            f.neighbour=f.internal?nativeCell[f.neighbourOrdinal]:-1;
            if(f.internal&&!internalOrderKeys.emplace(f.ownerOrdinal,f.neighbourOrdinal).second)
            {out.refusal=Refusal::mapping;return false;}
            if(f.zone>=0&&!f.internal&&!f.coupled)
                f.immutablePhysicalSourceRole=immutablePhysicalRole(raw,authority,f);
            if(f.rawRing.size()>Caps::ring||f.rawRing.size()<3||protectedRole(f))
            {out.refusal=f.coupled?Refusal::coupled:(f.zone>=0?Refusal::zone:Refusal::shape);return false;}
            for(label p:actual.faces()[compact])
            {
                const label rep=nativePoint[p];
                if(rep<0||rep>=label(binding.alias.size())||binding.alias[rep]!=rep)
                {out.refusal=Refusal::aliasDependency;return false;}
                f.ring.push_back(rep);reps.insert(rep);
                if(reps.size()>Caps::points){out.refusal=Refusal::pointCap;return false;}
            }
            std::vector<label> expected;
            for(label p:f.rawRing)
            {
                if(p<0||p>=label(binding.alias.size())){out.refusal=Refusal::mapping;return false;}
                expected.push_back(binding.alias[p]);
            }
            const label rawO=f.rawOwner>=0?binding.cell[f.rawOwner]:-1;
            const label rawN=f.rawNeighbour>=0?binding.cell[f.rawNeighbour]:-1;
            if((rawO<0&&rawN>=0)||(rawN>=0&&rawO>rawN))std::reverse(expected.begin(),expected.end());
            if(expected!=f.ring){out.refusal=Refusal::rawDependency;return false;}
            f.centre=actual.faceCentres()[compact];f.area=actual.faceAreas()[compact];
            if(!f.internal)f.patchRole=dcCartWeightUnionPreview::patchRole
                (actual.boundaryMesh()[actual.boundaryMesh().patchID(compact)]);
            if(f.metric)f.quality=dcCartWeightUnionPreview::metrics(actual,compact);
            out.faces.push_back(std::move(f));
        }
        std::map<label,std::size_t> aliasIndex;
        for(label rep:reps)
        {
            Alias alias;alias.representative=rep;alias.world=actual.points()[binding.point[rep]];
            if(!finite(alias.world)||!bits(alias.world,raw.points[rep]))
            {out.refusal=Refusal::aliasDependency;return false;}
            aliasIndex.emplace(rep,out.aliases.size());out.aliases.push_back(std::move(alias));
        }
        std::size_t memberCount=0;
        for(label p=0;p<label(binding.alias.size());++p)
        {
            if((p&255)==0&&!available(authority)){out.refusal=Refusal::clock;return false;}
            const auto found=aliasIndex.find(binding.alias[p]);if(found==aliasIndex.end())continue;
            if(++memberCount>Caps::aliasMembers){out.refusal=Refusal::pointCap;return false;}
            auto& alias=out.aliases[found->second];
            alias.members.push_back(p);alias.rawWorld.push_back(raw.points[p]);
        }
        for(const auto& alias:out.aliases)if(alias.members.empty())
        {out.refusal=Refusal::aliasDependency;return false;}
        out.core.assign(coreSet.begin(),coreSet.end());out.outside.assign(outside.begin(),outside.end());
        out.secondHalo.assign(second.begin(),second.end());out.source=authority.sourceFingerprint;
        out.epoch=epoch;out.actualRank=rank;out.nativeCells=raw.nCells;out.nativePoints=raw.points.size();
        out.nGridPoints=raw.nGridPoints;out.refusal=Refusal::none;
        out.payloadBytes=sizeof(View)+out.origins.size()*sizeof(Origin);
        for(const auto& c:out.cells)out.payloadBytes+=sizeof(Cell)+(c.faces.size()+c.rawFaces.size())*sizeof(label);
        for(const auto& f:out.faces)
        {
            if(f.patchRole.size()>4096){out.refusal=Refusal::shape;return false;}
            out.payloadBytes+=sizeof(Face)+(f.ring.size()+f.rawRing.size())*sizeof(label)+f.patchRole.size();
        }
        for(const auto& a:out.aliases)out.payloadBytes+=sizeof(Alias)+a.members.size()*(sizeof(label)+sizeof(point));
        for(const auto& origin:out.origins)out.payloadBytes+=origin.sourceFacets.size()*sizeof(std::array<label,3>)+origin.region.size();
        if(out.payloadBytes>Caps::payload){out.refusal=Refusal::pointCap;return false;}
        Values before;
        if(!evaluate(out,{},[&](){return available(authority);},before)||!installedBinding(out,before))
        {out=View();out.refusal=Refusal::installedBinding;return false;}
        reset.success=true;return true;
    }
    catch(...){out=View();out.refusal=Refusal::shape;return false;}
}
// Current view admission after old passes: every complete shape/ordered ring
// and original point alias class is literal. Journal endpoints are protected;
// no old packet is discarded, no earliest oracle is refreshed.
enum class CurrentRefusal
{
    none,ready,legacy,budget,rankSize,checkpoint,pointCapacity,pointActive,
    alias,packetOverlap,cellRingRole,sourceOrigin,exception,count
};
inline const char* currentRefusalName(CurrentRefusal why)
{
    switch(why)
    {
        case CurrentRefusal::none:return "none";
        case CurrentRefusal::ready:return "ready";
        case CurrentRefusal::legacy:return "legacy";
        case CurrentRefusal::budget:return "budget";
        case CurrentRefusal::rankSize:return "rank-size";
        case CurrentRefusal::checkpoint:return "checkpoint";
        case CurrentRefusal::pointCapacity:return "point-capacity";
        case CurrentRefusal::pointActive:return "point-active";
        case CurrentRefusal::alias:return "alias";
        case CurrentRefusal::packetOverlap:return "packet-overlap";
        case CurrentRefusal::cellRingRole:return "cell-ring-role";
        case CurrentRefusal::sourceOrigin:return "source-origin";
        case CurrentRefusal::exception:return "exception";
        case CurrentRefusal::count:break;
    }
    return "unknown";
}
// Optional first-failed admission predicate; observation only, not permission.
// Keep the original bool API, evaluation order and exception refusal.
inline bool current(const View& view,const Cut& live,
                    const Authority& authority,label legacyAccepted,
                    const std::vector<std::vector<label>>& currentCellFaces,
                    CurrentRefusal* refusal=nullptr)
{
    if(refusal)*refusal=CurrentRefusal::none;
    auto refuse=[&](CurrentRefusal why)
    {if(refusal)*refusal=why;return false;};
    try
    {
    if(!view.ready())return refuse(CurrentRefusal::ready);
    if(legacyAccepted!=0)return refuse(CurrentRefusal::legacy);
    if(!available(authority))return refuse(CurrentRefusal::budget);
    if(view.actualRank!=Pstream::myProcNo()||view.nativeCells!=live.nCells
     ||view.nativePoints!=label(live.points.size())||view.nGridPoints!=live.nGridPoints
     ||currentCellFaces.size()!=std::size_t(live.nCells))return refuse(CurrentRefusal::rankSize);
    // The existing preoperator checkpoint is the normal owner of point
    // provenance. A changed point anywhere can change alias membership:
    // before topology consumption this first grammar conservatively requires
    // the full native point payload unchanged, never a hash-only assertion.
    if(!live.writerBaseline||!live.writerBaseline->checkpoint)return refuse(CurrentRefusal::checkpoint);
    const auto& earliest=*live.writerBaseline->checkpoint;
    if(earliest.points.size()!=live.points.size()||earliest.nGridPoints!=live.nGridPoints)return refuse(CurrentRefusal::checkpoint);
    if(live.points.size()>Caps::normalIndexBytes/2)return refuse(CurrentRefusal::pointCapacity);
    std::vector<bool> beforeActive(live.points.size(),false),afterActive(live.points.size(),false);
    auto markActive=[&](const Cut& mesh,std::vector<bool>& active)
    {
        if(mesh.faceAlive.size()!=mesh.faces.size())return refuse(CurrentRefusal::pointActive);
        for(std::size_t f=0;f<mesh.faces.size();++f)
        {
            if((f&255)==0&&!available(authority))return refuse(CurrentRefusal::budget);
            if(!mesh.faceAlive[f])continue;
            for(label p:mesh.faces[f])
            {if(p<0||p>=label(active.size()))return refuse(CurrentRefusal::pointActive);active[p]=true;}
        }
        return true;
    };
    if(!markActive(earliest,beforeActive)||!markActive(live,afterActive))return false;
    if(beforeActive!=afterActive)return refuse(CurrentRefusal::pointActive);
    for(std::size_t p=0;p<live.points.size();++p)
    {
        if((p&255)==0&&!available(authority))return refuse(CurrentRefusal::budget);
        if(!bits(live.points[p],earliest.points[p]))return refuse(CurrentRefusal::alias);
    }
    if(live.weightUnionState)
        for(const auto& packet:live.weightUnionState->committed)
        {
            for(const auto& c:packet.cells)for(const auto& ours:view.cells)if(c.id==ours.native)return refuse(CurrentRefusal::packetOverlap);
            for(const auto& f:packet.faces)for(const auto& ours:view.faces)if(f.id==ours.native)return refuse(CurrentRefusal::packetOverlap);
            for(const auto& alias:packet.aliases)
                for(const auto& ours:view.aliases)
                    if(alias.first==ours.representative
                     ||std::find(ours.members.begin(),ours.members.end(),alias.first)!=ours.members.end())return refuse(CurrentRefusal::packetOverlap);
        }
    for(const auto& c:view.cells)
    {
        if(!available(authority))return refuse(CurrentRefusal::budget);
        if(c.native<0||c.native>=live.nCells||!live.cellAlive[c.native])return refuse(CurrentRefusal::cellRingRole);
        auto faces=currentCellFaces[c.native];
        if(faces.size()>Caps::faces)return refuse(CurrentRefusal::cellRingRole);std::sort(faces.begin(),faces.end());
        if(faces!=c.rawFaces)return refuse(CurrentRefusal::cellRingRole);
    }
    for(const auto& f:view.faces)
    {
        if(f.native<0||f.native>=label(live.faces.size())||!live.faceAlive[f.native]
         ||live.owner[f.native]!=f.rawOwner||live.neighbour[f.native]!=f.rawNeighbour
         ||live.patch[f.native]!=f.patch||live.zone[f.native]!=f.zone
         ||live.faces[f.native]!=f.rawRing||protectedRole(f))return refuse(CurrentRefusal::cellRingRole);
        if(f.zone>=0&&(!f.immutablePhysicalSourceRole||!immutablePhysicalRole(live,authority,f)))return refuse(CurrentRefusal::cellRingRole);
    }
    for(const auto& a:view.aliases)for(std::size_t q=0;q<a.members.size();++q)
        if(a.members[q]<0||a.members[q]>=label(live.points.size())
         ||!bits(live.points[a.members[q]],a.rawWorld[q]))return refuse(CurrentRefusal::alias);
    const auto& old=view.source;const auto& fresh=authority.sourceFingerprint;
    if(old.raw!=fresh.raw||old.roles!=fresh.roles||old.materialKey!=fresh.materialKey
     ||old.material!=fresh.material||old.producer!=fresh.producer
     ||old.boundaryMembership!=fresh.boundaryMembership)return refuse(CurrentRefusal::sourceOrigin);
    std::vector<Origin> now;
    if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(live,authority,view.core,now)
     ||now.size()!=view.origins.size())return refuse(CurrentRefusal::sourceOrigin);
    for(std::size_t i=0;i<now.size();++i)
    {
        const auto& a=now[i];const auto& b=view.origins[i];
        if(a.nativeParent!=b.nativeParent||a.rank!=b.rank||a.level!=b.level
         ||a.leaf!=b.leaf||a.piece!=b.piece||a.region!=b.region||a.sourceFacets!=b.sourceFacets)return refuse(CurrentRefusal::sourceOrigin);
    }
    return true;
    }
    catch(...){return refuse(CurrentRefusal::exception);}
}
struct Catalogue
{
    std::vector<View> views;
    label proposed=0,captured=0,refused=0,currentReady=0;
    scalar captureSeconds=0;
    std::uint64_t epoch=0;
    std::size_t payloadBytes=0;
};
// Default-OFF normal discovery/capture only. No topology is consumed here.
// Seeds are actual installed D-bad cells; the two complete interior point
// stars are derived from true adjacent cells, never fixture IDs or TRACE.
inline void captureCatalogue(const Cut& raw,const polyMesh& actual,const Bindings& bindings,
                             const Authority& authority,
                             const std::vector<std::vector<label>>& rawCellFaces,Catalogue& out)
{
    clockTime clock;out=Catalogue();
    struct Duration {Catalogue& out;clockTime& clock;~Duration(){out.captureSeconds=clock.elapsedTime();}} duration{out,clock};
    static std::atomic<std::uint64_t> nextEpoch{1};
    out.epoch=nextEpoch.fetch_add(1,std::memory_order_relaxed);
    if(!out.epoch||!available(authority))return;
    Authority bounded=authority;
    bounded.remainingSeconds=[&authority,&clock]()
    {
        if(!authority.remainingSeconds)return scalar(0);
        const scalar job=authority.remainingSeconds(),stage=4-clock.elapsedTime();
        return finite(job)&&finite(stage)?max(scalar(0),min(job,stage)):scalar(0);
    };
    const Authority& currentAuthority=bounded;
    NormalIndex index;
    if(!produceIndex(raw,actual,bindings,currentAuthority,index))return;
    const auto& pf=actual.pointFaces();
    std::set<std::pair<label,label>> seen;
    auto freePoint=[&](label p)
    {
        if(p<0||p>=actual.nPoints()||!finite(actual.points()[p])||pf[p].empty()||pf[p].size()>512
         ||dcCartWeightUnionPreview::zonedPoint(actual,p))return false;
        for(label f:pf[p])
            if(f>=actual.nInternalFaces()||dcCartWeightUnionPreview::zonedFace(actual,f))return false;
        return true;
    };
    auto star=[&](label p,std::set<label>& core)
    {
        for(label f:pf[p])
        {
            if(!available(currentAuthority))return false;
            core.insert(index.nativeCell[actual.faceOwner()[f]]);
            core.insert(index.nativeCell[actual.faceNeighbour()[f]]);
            if(core.size()>Caps::core)return false;
        }
        return true;
    };
    using Key=dcCartWeightUnionPreview::Key;
    std::vector<std::pair<Key,label>> seeds;
    bool censusComplete=true;
    for(label c=0;c<actual.nCells();++c)
    {
        if(!available(currentAuthority)||clock.elapsedTime()>=4){censusComplete=false;break;}
        const scalar determinant=dcCartWeightUnionPreview::determinant(actual,c);
        if(!finite(determinant)||determinant<0||determinant>=.001)continue;
        if(seeds.size()>=4096){censusComplete=false;break;} // no truncated world-ranked pool
        auto key=dcCartWeightUnionPreview::key(actual,c);
        if(!key.empty())seeds.emplace_back(std::move(key),c);
    }
    if(!censusComplete){out.captureSeconds=clock.elapsedTime();return;}
    std::sort(seeds.begin(),seeds.end(),[](const std::pair<Key,label>& a,const std::pair<Key,label>& b)
        {return a.first<b.first;});
    auto sortedFreePoints=[&](label c)
    {
        std::set<label> points;
        for(label f:actual.cells()[c])for(label p:actual.faces()[f])if(freePoint(p))points.insert(p);
        std::vector<label> result(points.begin(),points.end());
        if(result.size()>16){result.clear();return result;}
        std::sort(result.begin(),result.end(),[&](label a,label b)
        {
            const auto& x=actual.points()[a];const auto& y=actual.points()[b];
            return std::make_tuple(x.x(),x.y(),x.z())<std::make_tuple(y.x(),y.y(),y.z());
        });
        for(std::size_t i=1;i<result.size();++i)
            if(actual.points()[result[i]]==actual.points()[result[i-1]]){result.clear();break;}
        return result;
    };
    for(std::size_t seed=0;seed<seeds.size()&&out.proposed<16;++seed)
    {
        if((seed&&seeds[seed].first==seeds[seed-1].first)
         ||(seed+1<seeds.size()&&seeds[seed].first==seeds[seed+1].first))continue;
        const label c=seeds[seed].second;
        const auto ownPoints=sortedFreePoints(c);
        // Shared faces already have actual global writer order.
        for(label shared:actual.cells()[c])
        {
            if(shared>=actual.nInternalFaces())continue;
            const label o=actual.faceOwner()[shared],n=actual.faceNeighbour()[shared],other=o==c?n:o;
            const auto nextPoints=sortedFreePoints(other);
            for(label p:ownPoints)for(label q:nextPoints)
            {
                if(!available(currentAuthority)||clock.elapsedTime()>=4||out.proposed>=16)goto finished;
                if(p==q||!seen.insert(std::minmax(p,q)).second)continue;
                ++out.proposed;std::set<label> core;
                if(!star(p,core)||!star(q,core)){++out.refused;continue;}
                std::vector<label> cells(core.begin(),core.end());View view;
                if(capture(raw,actual,bindings,currentAuthority,cells,rawCellFaces,index,out.epoch,view))
                {
                    if(view.payloadBytes>Caps::payload-out.payloadBytes){++out.refused;goto finished;}
                    out.payloadBytes+=view.payloadBytes;++out.captured;out.views.push_back(std::move(view));
                }
                else ++out.refused;
            }
        }
    }
finished:
    out.captureSeconds=clock.elapsedTime();
}
} // local oracle
struct dcCartCutCells::writerCurrentContext:dcCartCompleteCavityLocalOracle::Catalogue {};
} // Foam
#endif
