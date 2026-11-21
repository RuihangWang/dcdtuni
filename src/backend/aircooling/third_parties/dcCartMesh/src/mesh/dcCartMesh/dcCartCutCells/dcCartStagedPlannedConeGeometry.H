#ifndef dcCartStagedPlannedConeGeometry_H
#define dcCartStagedPlannedConeGeometry_H
#include "dcCartPlannedConeGeometry.H"

namespace Foam { namespace dcCartStagedPlannedConeGeometry
{
namespace Planned=dcCartPlannedConeGeometry;
namespace S=dcCartSharedAxisSeamVolumePartition;
using LocalFace=Planned::LocalFace;
using EdgeUse=Planned::EdgeUse;
using Planned::finite;
struct BaseRow
{
    label logicalId=-1,nativeBase=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    std::vector<label> ring;
};
// Recipe only: native-owner-oriented rings, exact caller base order, true staged
// point IDs. Points.size() includes every staged birth; operator[] returns the
// stable real coordinate. The synthetic apex is exactly the following ID.
// originalLogicalFaces and sparse flag callbacks must cover the actual current
// logical parent recipe. The caller supplies stock/V-M/source and publication
// proofs, and keeps this recipe/apex/peer recipe fixed until consumption.
// whole is an explicit already-qualified recipe choice, never permission.
template<class Points,class WarpFlags,class PlanarFlags>
bool build(const Points& points,label parent,const std::vector<BaseRow>& baseRows,
    const std::vector<label>& originalLogicalFaces,const point& proposed,
    WarpFlags warpFlags,PlanarFlags planarFlags,
    bool conformingSideUnion,bool planarOutlineEnabled,
    const std::function<bool(label)>& coupledType,
    const std::function<bool()>& available,Planned::Parent& out,bool whole=false)
{
    out=Planned::Parent();
    if(parent<0||points.size()>=std::size_t(std::numeric_limits<label>::max()))return false;
    struct MeshView {const Points& points;} m{points};
    std::vector<label> baseFaces;baseFaces.reserve(baseRows.size());
    std::set<label> seenNative;
    for(const BaseRow& row:baseRows)
    {
        if(row.nativeBase<0||!seenNative.insert(row.nativeBase).second
            ||(row.owner!=parent&&row.neighbour!=parent))return false;
        for(label id:row.ring)
            if(id<0||std::size_t(id)>=points.size())return false;
        baseFaces.push_back(row.nativeBase);
    }
    const label ap=m.points.size();
    auto clock=[&](){return !available||available();};
    if(whole)
    {
        std::vector<LocalFace> shell;
        for(const BaseRow& row:baseRows)
        {
            if(!clock())return false;
            const bool own=row.owner==parent;
            if(!own&&row.neighbour!=parent)return false;
            shell.push_back({row.ring,own,row.patch<0||coupledType(row.patch),row.nativeBase});
        }
        if(shell.size()<4||!clock())return false;
        out.baseFaces=baseFaces;out.baseToChild.assign(baseRows.size(),0);
        out.children.push_back(std::move(shell));out.known=true;return true;
    }
        S::Plan geometry;geometry.births.push_back({-1,-1,proposed});
        std::vector<std::vector<LocalFace>> children(baseRows.size());
        std::map<std::pair<label,label>,std::vector<EdgeUse>> edges;
        bool known=true,constructPolygonalSides=false;
        if(conformingSideUnion)for(label original:originalLogicalFaces)
        {
            if(warpFlags(original)
                ||(planarOutlineEnabled&&planarFlags(original)))
            {constructPolygonalSides=true;break;}
        }
        for(label child=0;child<label(baseRows.size());++child)
        {
            if(!clock())return false;
            const BaseRow& row=baseRows[child];const label f=row.nativeBase;
            const bool own=row.owner==parent;
            if(!own&&row.neighbour!=parent){known=false;break;}
            children[child].push_back
                ({row.ring,own,row.patch<0||coupledType(row.patch),f});
            const auto& poly=row.ring;
            for(std::size_t j=0;j<poly.size();++j)
            {
                label a=poly[j],b=poly[(j+1)%poly.size()];if(!own)std::swap(a,b);
                edges[std::minmax(a,b)].push_back({child,a,b});
            }
        }
        auto writable=[&](const std::vector<label>& ring)
        {
            vector n(vector::zero);point c(point::zero);
            const label k=ring.size();if(k<3)return false;
            for(label j=0;j<k;++j)
            {
                const point& a=S::coordinate(m,geometry,ring[j]);
                const point& b=S::coordinate(m,geometry,ring[(j+1)%k]);
                n.x()+=(a.y()-b.y())*(a.z()+b.z());
                n.y()+=(a.z()-b.z())*(a.x()+b.x());
                n.z()+=(a.x()-b.x())*(a.y()+b.y());c+=a;
            }
            n*=.5;c/=scalar(k);return finite(n)&&finite(c)&&mag(n)>1e-12;
        };
        auto appendSide=[&](const dcCartConformingConeSides::Side& side)
        {
            out.sides.push_back(side);
            children[side.owner].push_back({side.ring,true,true});
            children[side.neighbour].push_back({side.ring,false,true});
        };
        std::vector<dcCartConformingConeSides::Side> sides;sides.reserve(edges.size());
        if(known)for(const auto& entry:edges)
        {
            if(!clock())return false;
            const auto& e=entry.second;
            if(e.size()!=2||e[0].a!=e[1].b||e[0].b!=e[1].a||e[0].child==e[1].child)
            {known=false;break;}
            const std::vector<label> ring{e[0].b,e[0].a,ap};
            if(!writable(ring)){known=false;break;}
            sides.push_back({e[0].child,e[1].child,ring});
        }
        if(known&&constructPolygonalSides)
        {
            const auto plan=dcCartConformingConeSides::build(sides,[&](label p)
            {const point& q=S::coordinate(m,geometry,p);return std::array<double,3>{{q.x(),q.y(),q.z()}};},available);
            bool use=plan.complete;
            if(use)for(const auto& side:plan.sides)use=use&&writable(side.ring);
            if(use)for(const auto& side:plan.sides)appendSide(side);
            else for(const auto& side:sides)appendSide(side);
        }
        else if(known)for(const auto& side:sides)appendSide(side);
        if(!clock())return false;
    out.baseFaces=baseFaces;out.baseToChild.resize(baseRows.size());
    for(label j=0;j<label(baseRows.size());++j)out.baseToChild[j]=j;
    out.geometry=std::move(geometry);
    out.children=std::move(children);out.known=known;
    return true;
}
// Delegate the entire metric arithmetic to the canonical existing function.
template<class Points>
bool measure(const Points& points,const Planned::Parent& parent,
    const std::vector<LocalFace>& shell,Planned::Metrics& out)
{
    struct MeshView {const Points& points;} m{points};
    return Planned::measure(m,parent,shell,out);
}
} }
#endif
