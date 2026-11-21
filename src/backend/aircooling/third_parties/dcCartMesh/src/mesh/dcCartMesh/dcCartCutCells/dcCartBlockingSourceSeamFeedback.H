#ifndef dcCartBlockingSourceSeamFeedback_H
#define dcCartBlockingSourceSeamFeedback_H
#include "dcCartSharedAxisColumnCompound.H"
#include "dcCartPlannedSourceColumns.H"
#include "dcCartExactBinary64Geometry.H"
#include "dcCartRectangularSourceClosurePlanes.H"

namespace Foam { namespace dcCartBlockingSourceSeamFeedback
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace C=dcCartSharedAxisColumnCompound;
namespace P=dcCartPlannedSourceColumns;
namespace E=::dcCartExactBinary64Geometry;
enum class Origin : label { None=0, SourceFacetConcavity=1, RequiredWholeDar=2 };
struct Request
{
    Origin origin=Origin::None;
    label root=-1,firstSource=-1,secondSource=-1;
    // Failure identity only: neither a native key nor DAR grants Source authority.
    label actualPart=-1,originalMother=-1,partOrdinal=-1;
    C::Plane plane={-1,0};
};
// A request is failure feedback, not source, coverage, peer or quality authority.
// Only an actual whole-cell C blocker may add its unique original-edge plane.
template<class Raw,class Mesh>
bool select(const Raw& raw,const Mesh& m,const S::Plan& staged,
 const P::Prepared& failure,const std::string& cause,label rank,
 const C::RootPlanes& assigned,const std::function<bool()>& available,
 Request& out,std::string* why=nullptr)
{
    out=Request();
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(cause!="face-centre-concavity"||failure.failedPlanned
        ||!failure.failedFacesCaptured||!staged.ready)return fail("feedback-kind");
    const label root=failure.failedRawRoot;
    if(root<0||root>=raw.nCells||!raw.cellAlive[root]||raw.cellRank[root]!=rank)
        return fail("feedback-root");
    const auto ownPlanes=assigned.find(root);
    const std::vector<C::Plane> noPlanes;
    const auto& existingPlanes=ownPlanes==assigned.end()?noPlanes:ownPlanes->second;
    // Existing assignment is a search state, never source permission. A new
    // typed raw root is checked by the next complete closure's live gates.
    if(failure.failedBasePointCount!=m.points.size()
        ||failure.failedStagedPointCount!=m.points.size()+staged.births.size())
        return fail("feedback-point-domain");
    const auto& a=failure.failedFaces[0];const auto& b=failure.failedFaces[1];
    if(a.sourceFace==b.sourceFace)return fail("feedback-same-source");
    auto valid=[&](const P::FailedFace& f)
    {
        const label source=f.sourceFace;
        if(f.kind!=P::FailedFaceKind::SourceFacet||!f.rawSourceKnown
            ||source<0||std::size_t(source)>=raw.faces.size()||!raw.faceAlive[source]
            ||f.rawSourceOwner!=raw.owner[source]||f.rawSourceNeighbour!=raw.neighbour[source]
            ||(raw.owner[source]!=root&&raw.neighbour[source]!=root)
            ||f.patch!=raw.patch[source]||f.zone!=raw.zone[source]
            ||raw.faces[source].size()<3||raw.faces[source].size()>64
            ||f.ring.size()<3||f.ring.size()>64)return false;
        auto endpoint=[&](const P::FailedEndpoint& e)
        {return e.kind==P::FailedEndpointKind::StagedCell
            &&e.stagedParent==failure.failedParent&&e.rawRoot==root;};
        if(endpoint(f.owner)==endpoint(f.neighbour))return false;
        for(label p:raw.faces[source])
            if(p<0||std::size_t(p)>=raw.points.size()||!S::finite(raw.points[p]))return false;
        for(label p:f.ring)
            if(p<0||std::size_t(p)>=failure.failedStagedPointCount
                ||!S::finite(S::coordinate(m,staged,p)))return false;
        return true;
    };
    if(!valid(a)||!valid(b))return fail("feedback-source-provenance");
    const auto& ar=raw.faces[a.sourceFace];const auto& br=raw.faces[b.sourceFace];
    label support=-1;
    for(label k=0;k<3;++k)
    {
        if(!S::clock(available))return fail("clock");
        const scalar x=raw.points[ar[0]][k];bool same=true;
        for(label p:ar)same=same&&raw.points[p][k]==x;
        for(label p:br)same=same&&raw.points[p][k]==x;
        for(label p:a.ring)same=same&&S::coordinate(m,staged,p)[k]==x;
        for(label p:b.ring)same=same&&S::coordinate(m,staged,p)[k]==x;
        if(same){if(support>=0)return fail("feedback-degenerate-support");support=k;}
    }
    if(support<0)return fail("feedback-non-axis-support");
    E::Context exact([&](){return S::clock(available);});
    auto areaSign=[&](const std::vector<label>& ring,auto coordinate,bool reverse)
    {
        const point& x=coordinate(ring[0]);
        const E::Vector origin=E::vector(E::Point{{x.x(),x.y(),x.z()}},exact);
        E::Dyadic area;
        for(std::size_t i=1;i+1<ring.size();++i)
        {
            const point& p=coordinate(ring[i]);const point& q=coordinate(ring[i+1]);
            const auto u=E::difference(E::vector(E::Point{{p.x(),p.y(),p.z()}},exact),origin,exact);
            const auto v=E::difference(E::vector(E::Point{{q.x(),q.y(),q.z()}},exact),origin,exact);
            area=E::add(area,E::cross(u,v,exact)[support],exact);
        }
        return (reverse?-1:1)*E::sign(area);
    };
    auto rawCoordinate=[&](label p)->const point&{return raw.points[p];};
    auto stagedCoordinate=[&](label p)->const point&{return S::coordinate(m,staged,p);};
    const int rawA=areaSign(ar,rawCoordinate,raw.owner[a.sourceFace]!=root);
    const int rawB=areaSign(br,rawCoordinate,raw.owner[b.sourceFace]!=root);
    const int stagedA=areaSign(a.ring,stagedCoordinate,a.owner.stagedParent!=failure.failedParent);
    const int stagedB=areaSign(b.ring,stagedCoordinate,b.owner.stagedParent!=failure.failedParent);
    if(!exact.good()||!rawA||rawA!=rawB||rawA!=stagedA||rawB!=stagedB)
        return fail("feedback-exact-area");
    // Finite binary64 order is exact here; no centre, rounded plane or new point.
    auto half=[&](const std::vector<label>& ring,auto coordinate,const C::Plane& p)
    {
        bool positive=false,negative=false;
        for(label id:ring)
        {const scalar x=coordinate(id)[p.first];positive=positive||x>p.second;negative=negative||x<p.second;}
        return positive==negative?0:(positive?1:-1);
    };
    std::set<C::Plane> candidates;
    for(std::size_t i=0;i<ar.size();++i)for(std::size_t j=0;j<br.size();++j)
    {
        if(!S::clock(available))return fail("clock");
        label p=ar[i],q=ar[(i+1)%ar.size()],r=br[j],s=br[(j+1)%br.size()];
        if(raw.owner[a.sourceFace]!=root)std::swap(p,q);
        if(raw.owner[b.sourceFace]!=root)std::swap(r,s);
        if(p!=s||q!=r)continue; // exact original edge identity, never coordinate alias
        label along=-1;
        for(label k=0;k<3;++k)if(raw.points[p][k]!=raw.points[q][k])
        {if(along>=0){along=-2;break;}along=k;}
        if(along<0||along==support)continue;
        const label axis=3-support-along;const C::Plane plane={axis,raw.points[p][axis]};
        if(std::find(existingPlanes.begin(),existingPlanes.end(),plane)!=existingPlanes.end())continue;
        const int x=half(ar,rawCoordinate,plane),y=half(br,rawCoordinate,plane);
        if(!x||x!=-y||half(a.ring,stagedCoordinate,plane)!=x
            ||half(b.ring,stagedCoordinate,plane)!=y)continue;
        candidates.insert(plane);
    }
    if(candidates.size()!=1)return fail(candidates.empty()?"feedback-no-new-plane":"feedback-ambiguous-plane");
    if(!S::clock(available))return fail("clock");
    out.origin=Origin::SourceFacetConcavity;out.actualPart=failure.failedParent;
    out.root=root;out.firstSource=a.sourceFace;out.secondSource=b.sourceFace;
    out.plane=*candidates.begin();return true;
}

// A tensor midpoint is proposed only after the actual complete required-whole
// DAR veto. Planned-cone DAR, FC vetoes and optional diagnostic captures cannot
// enter this route. Every Source/peer/Stock/VM gate runs again on the next job.
template<class Raw,class Mesh>
bool selectRequiredWholeDar(const Raw& raw,const Mesh& m,const S::Plan& staged,
 const P::Prepared& failure,const std::string& cause,label rank,
 const C::RootPlanes& assigned,const std::vector<std::vector<label>>& rawCf,
 const std::vector<label>& sourceParent,
 const std::map<label,std::vector<S::Row>>& shells,const std::set<label>& wholeParts,
 label patchCount,const std::function<bool()>& available,
 Request& out,std::string* why=nullptr)
{
    out=Request();
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    auto work=[&](){return S::clock(available);};
    if(!work())return fail("tensor-dar-feedback-clock");
    if(cause!="column-D-AR"||failure.failedPlanned||!failure.failedDarKnown
        ||failure.failedChild!=0||!wholeParts.count(failure.failedParent)
        ||!staged.ready||staged.cutScope!=S::Finite::CutScope::FiniteTensor
        ||!std::isfinite(failure.failedDeterminant)||!std::isfinite(failure.failedAspect)
        ||!(failure.failedDeterminant<.001||failure.failedAspect>1000))
        return fail("tensor-dar-feedback-kind");
    if(failure.failedBasePointCount!=m.points.size()
        ||failure.failedStagedPointCount!=m.points.size()+staged.births.size())
        return fail("tensor-dar-feedback-point-domain");
    const label actualPart=failure.failedParent,root=failure.failedRawRoot;
    if(root<0||root>=raw.nCells||std::size_t(root)>=rawCf.size()
        ||!raw.cellAlive[root]||raw.cellRank[root]!=rank)
        return fail("tensor-dar-feedback-root");
    const auto ownPlanes=assigned.find(root);
    if(ownPlanes==assigned.end()||ownPlanes->second.empty())
        return fail("tensor-dar-feedback-unassigned-root");
    // Resolve the actual part through the real restored split, never the seed
    // or equality between a future part label and an original raw mother label.
    const S::Cell* split=nullptr;std::size_t ordinal=0;std::size_t matches=0;
    for(const auto& cell:staged.cells)
    {
        if(!work())return fail("tensor-dar-feedback-clock");
        for(std::size_t k=0;k<S::partCount(cell);++k)
            if(S::partCell(cell,k)==actualPart){split=&cell;ordinal=k;++matches;}
    }
    if(matches!=1||!split||S::partCount(*split)<2||S::partCount(*split)>64
        ||split->parent<0||std::size_t(split->parent)>=sourceParent.size()
        ||sourceParent[split->parent]!=root
        ||std::count(staged.volumeRoots.begin(),staged.volumeRoots.end(),split->parent)!=1)
        return fail("tensor-dar-feedback-part-origin");
    if(!dcCartRectangularSourceClosurePlanes::rectangular(raw,root,rawCf[root],available))
        return fail("tensor-dar-feedback-original-box-or-clock");
    const auto found=shells.find(actualPart);
    if(found==shells.end()||found->second.size()<6||found->second.size()>64)
        return fail("tensor-dar-feedback-complete-shell");
    const auto& shell=found->second;
    const auto& partShell=split->parts.empty()
        ?(ordinal==0?split->positive:split->negative):split->parts[ordinal].shell;
    if(partShell.size()!=shell.size())return fail("tensor-dar-feedback-part-shell");
    std::map<label,const S::Face*> expected;
    for(const auto& face:partShell)
        if(!work()||face.source<0||!expected.emplace(face.source,&face).second)
            return fail("tensor-dar-feedback-part-shell");
    std::map<label,const S::Row*> actualRows;
    for(const auto& row:staged.rows)if(row.owner==actualPart||row.neighbour==actualPart)
    {
        if(!work()||row.native<0||!actualRows.emplace(row.native,&row).second)
            return fail("tensor-dar-feedback-staged-row");
    }
    if(actualRows.size()!=shell.size())return fail("tensor-dar-feedback-row-coverage");
    point lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
    std::set<label> shellFaces;
    std::map<label,point> nodes;std::map<std::tuple<scalar,scalar,scalar>,label> coordinates;
    for(const auto& row:shell)
    {
        if(!work())return fail("tensor-dar-feedback-clock");
        const auto e=expected.find(row.native);const auto a=actualRows.find(row.native);
        if(e==expected.end()||a==actualRows.end()||!shellFaces.insert(row.native).second
            ||row.owner<0||row.neighbour<-1
            ||row.owner==row.neighbour||row.patch<-1||row.zone<-1
            ||(row.owner==actualPart)==(row.neighbour==actualPart)
            ||row.ring.size()<3||row.ring.size()>64
            ||e->second->ring!=row.ring||e->second->forward!=(row.owner==actualPart)
            ||a->second->source!=row.source||a->second->owner!=row.owner
            ||a->second->neighbour!=row.neighbour||a->second->patch!=row.patch
            ||a->second->zone!=row.zone||a->second->ring!=row.ring)
            return fail("tensor-dar-feedback-full-row-tuple");
        if(row.source>=0)
        {
            const label f=row.source;
            if(std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
                ||(raw.owner[f]!=root&&raw.neighbour[f]!=root)
                ||row.patch!=raw.patch[f]||row.zone!=raw.zone[f])
                return fail("tensor-dar-feedback-source-tuple");
        }
        else if(row.source!=-1||row.neighbour<0||row.patch!=-1||row.zone!=-1)
            return fail("tensor-dar-feedback-generated-internal-role");
        // A genuine generated internal cap retains source=-1. It is not
        // discarded and never receives an invented original Source identity.
        if(row.neighbour<0&&(row.patch<0||row.patch>=patchCount))
            return fail("tensor-dar-feedback-boundary-role");
        std::set<label> unique;
        for(label id:row.ring)
        {
            if(!work()||id<0||std::size_t(id)>=failure.failedStagedPointCount
                ||!unique.insert(id).second)
                return fail("tensor-dar-feedback-point-domain");
            const point& q=S::coordinate(m,staged,id);
            if(!S::finite(q))return fail("tensor-dar-feedback-nonfinite-point");
            nodes.emplace(id,q);lo=min(lo,q);hi=max(hi,q);
            if(nodes.size()>256)return fail("tensor-dar-feedback-box-point-bound");
            const auto same=coordinates.emplace(std::make_tuple(q.x(),q.y(),q.z()),id);
            if(!same.second&&same.first->second!=id)
                return fail("tensor-dar-feedback-coordinate-alias");
        }
    }
    if(!(lo.x()<hi.x()&&lo.y()<hi.y()&&lo.z()<hi.z()))
        return fail("tensor-dar-feedback-box-bounds");
    const auto& geometry=S::partGeometry(*split,ordinal);
    for(label k=0;k<3;++k)
        if(geometry.lo[k]!=lo[k]||geometry.hi[k]!=hi[k])
            return fail("tensor-dar-feedback-actual-part-bounds");
    // The unchanged strict whole-Q already checked the complete directed,
    // convex shell. This bounded proposal screen only verifies its box support.
    std::array<bool,6> support{};
    for(const auto& row:shell)
    {
        if(!work())return fail("tensor-dar-feedback-clock");
        label axis=-1;
        for(label k=0;k<3;++k)
        {
            bool same=true;
            for(label p:row.ring)same=same&&nodes.at(p)[k]==nodes.at(row.ring.front())[k];
            if(same){if(axis>=0)return fail("tensor-dar-feedback-degenerate-face");axis=k;}
        }
        if(axis<0)return fail("tensor-dar-feedback-non-axis-face");
        const scalar position=nodes.at(row.ring.front())[axis];
        const label side=position==lo[axis]?0:(position==hi[axis]?1:-1);
        if(side<0)return fail("tensor-dar-feedback-non-boundary-face");
        support[2*axis+side]=true;
        for(std::size_t j=0;j<row.ring.size();++j)
        {
            if(!work())return fail("tensor-dar-feedback-clock");
            const point& a=nodes.at(row.ring[j]);const point& b=nodes.at(row.ring[(j+1)%row.ring.size()]);
            label varying=0;for(label k=0;k<3;++k)varying+=a[k]!=b[k];
            if(varying!=1)return fail("tensor-dar-feedback-non-axis-edge");
        }
    }
    for(bool side:support)if(!side)return fail("tensor-dar-feedback-missing-box-support");
    for(unsigned corner=0;corner<8;++corner)
    {
        if(!work())return fail("tensor-dar-feedback-clock");
        const scalar x=(corner&1)?hi.x():lo.x(),y=(corner&2)?hi.y():lo.y(),z=(corner&4)?hi.z():lo.z();
        if(!coordinates.count(std::make_tuple(x,y,z)))return fail("tensor-dar-feedback-missing-corner");
    }
    if(!dcCartSignedCrossing::supportedFloatingEnvironment())
        return fail("tensor-dar-feedback-floating-environment");
    // Compare exact binary64 spans. Exact ties choose X, then Y, then Z.
    label axis=0;S::D::Rational longest=S::D::detail::number(hi[0])-S::D::detail::number(lo[0]);
    for(label k=1;k<3;++k)
    {
        if(!work())return fail("tensor-dar-feedback-clock");
        const S::D::Rational span=S::D::detail::number(hi[k])-S::D::detail::number(lo[k]);
        if(span>longest){axis=k;longest=span;}
    }
    if(!work())return fail("tensor-dar-feedback-clock");
    const scalar middle=S::D::detail::rounded
        ((S::D::detail::number(lo[axis])+S::D::detail::number(hi[axis]))/S::D::Rational(2));
    if(!std::isfinite(middle)||!(lo[axis]<middle&&middle<hi[axis]))
        return fail("tensor-dar-feedback-midpoint-not-interior");
    const C::Plane plane={axis,middle};
    if(std::find(ownPlanes->second.begin(),ownPlanes->second.end(),plane)!=ownPlanes->second.end())
        return fail("tensor-dar-feedback-plane-already-requested");
    // Each box child's normal span must exceed twice the unchanged 3um
    // pyramid-depth gate. Full birth-to-old/new-point clearance is not guessed:
    // S::prepare checks its original 9e-12 distances on the fresh full closure.
    if(!(middle-lo[axis]>2*scalar(3e-6)&&hi[axis]-middle>2*scalar(3e-6)))
        return fail("tensor-dar-feedback-midpoint-3um");
    bool positive=false,negative=false;
    for(const auto& node:nodes)
    {positive=positive||node.second[axis]>middle;negative=negative||node.second[axis]<middle;}
    if(!positive||!negative)return fail("tensor-dar-feedback-part-not-crossed");
    std::array<std::set<scalar>,3> grid;
    positive=negative=false;
    for(label f:rawCf[root])for(label id:raw.faces[f])
    {
        if(!work())return fail("tensor-dar-feedback-clock");
        const point& q=raw.points[id];for(label k=0;k<3;++k)grid[k].insert(q[k]);
        positive=positive||q[axis]>middle;negative=negative||q[axis]<middle;
    }
    if(!positive||!negative)return fail("tensor-dar-feedback-root-not-crossed");
    for(const auto& p:ownPlanes->second)
    {
        if(!work()||p.first<0||p.first>2||!std::isfinite(p.second))
            return fail("tensor-dar-feedback-request-plane");
        grid[p.first].insert(p.second);
    }
    grid[axis].insert(middle);std::size_t boxes=1;
    for(const auto& levels:grid)
    {
        if(levels.size()<2||levels.size()>65||boxes>64/(levels.size()-1))
            return fail("tensor-dar-feedback-existing-grid-bound");
        boxes*=levels.size()-1;
    }
    if(!work())return fail("tensor-dar-feedback-clock");
    out.origin=Origin::RequiredWholeDar;out.root=root;out.actualPart=actualPart;
    out.originalMother=split->parent;out.partOrdinal=label(ordinal);out.plane=plane;
    if(why)*why="tensor-required-whole-DAR-midpoint";
    return true;
}
}}
#endif
