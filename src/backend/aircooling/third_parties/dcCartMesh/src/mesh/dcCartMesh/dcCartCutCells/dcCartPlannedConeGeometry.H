#ifndef dcCartPlannedConeGeometry_H
#define dcCartPlannedConeGeometry_H
#include "point.H"
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartConformingConeSides.H"
#include "dcCartPostCarveCanonicalWallFan.H"
#include "dcCartNativeInterfaceGeometry.H"
#include "dcCartSourcePlanarConeGroups.H"
#include "dcCartOriginalNodePositiveEarFacets.H"
#include <algorithm>
#include <array>
#include <utility>
#include <map>
#include <set>
#include <vector>
#include <functional>
#include <limits>
#include <cmath>

namespace Foam { namespace dcCartPlannedConeGeometry
{
namespace S=dcCartSharedAxisSeamVolumePartition;
struct LocalFace {std::vector<label> ring;bool forward=true,selected=true;label nativeBase=-1;};
struct EdgeUse {label child,a,b;};
struct Parent
{
    S::Plan geometry;
    std::vector<label> baseFaces,baseToChild;
    std::vector<std::vector<LocalFace>> children;
    std::vector<dcCartConformingConeSides::Side> sides;
    // Geometry-only records. Every old native base still occurs in baseFaces
    // exactly once; a child's composite face is the actual single carrier.
    std::map<label,dcCartPostCarveCanonicalWallFan::CompositeRecord> composites;
    // Distinct ordinary interfaces, never boundary/wall Composite records.
    std::map<label,dcCartNativeInterfaceGeometry::Record> interfaces;
    bool known=false;
};

// Literal two-endpoint child-shell binding for an ordinary native interface.
// Every original base remains exactly once in baseFaces/baseToChild; the complete
// numeric shell contains only the one carrier. Geometry and Source are separate.
inline bool nativeInterfaceRecipe(label mother,const Parent& recipe,
    const dcCartNativeInterfaceGeometry::Record& r,
    const std::function<bool()>& available)
{
    const auto work=[&](){return !available||available();};
    try
    {
    if(!work()||!recipe.known||(mother!=r.owner&&mother!=r.neighbour)
        ||r.endpointChild.size()!=2||!r.endpointChild.count(r.owner)||!r.endpointChild.count(r.neighbour)
        ||recipe.baseFaces.empty()||recipe.baseFaces.size()>128
        ||recipe.baseFaces.size()!=recipe.baseToChild.size()
        ||recipe.children.empty()||recipe.children.size()>128
        ||recipe.interfaces.size()>128
        ||r.constituents.size()<2||r.constituents.size()>128)return false;
    const auto child=r.endpointChild.find(mother);
    if(child==r.endpointChild.end()||child->second<0||std::size_t(child->second)>=recipe.children.size())return false;
    const auto stored=recipe.interfaces.find(r.carrier);
    if(stored==recipe.interfaces.end()||!dcCartNativeInterfaceGeometry::same(stored->second,r))return false;
    std::set<label> parts;
    for(const auto& f:r.constituents)
    {
        if(!work()||!parts.insert(f.native).second||recipe.composites.count(f.native))return false;
        const auto base=std::find(recipe.baseFaces.begin(),recipe.baseFaces.end(),f.native);
        if(base==recipe.baseFaces.end()||std::count(recipe.baseFaces.begin(),recipe.baseFaces.end(),f.native)!=1
            ||recipe.baseToChild[std::distance(recipe.baseFaces.begin(),base)]!=child->second)return false;
    }
    for(const auto& other:recipe.interfaces)if(other.first!=r.carrier)
        for(const auto& f:other.second.constituents)
            if(!work()||parts.count(f.native))return false;
    std::size_t carriers=0;
    for(std::size_t ordinal=0;ordinal<recipe.children.size();++ordinal)for(const auto& face:recipe.children[ordinal])
    {
        if(!work())return false;
        if(!parts.count(face.nativeBase))continue;
        if(face.nativeBase!=r.carrier||ordinal!=std::size_t(child->second)
            ||face.ring!=r.outline||face.forward!=(mother==r.owner)||!face.selected)return false;
        ++carriers;
    }
    return carriers==1&&work();
    }
    catch(...){return false;}
}

struct Metrics
{
    std::vector<vector> sf;
    std::vector<point> fc;
    vector sum=vector::zero;
    scalar selectedArea=0,volume=0;
    label selectedCount=0;
    point centre=point::zero;
};
inline bool finite(const vector& p)
{return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z());}
// Only geometry/provenance equality. Source authorization remains an explicit
// caller callback against the immutable actual authority, never this return bool.
template<class Points>
bool physicalCompositeGeometry(const dcCartPostCarveCanonicalWallFan::CompositeRecord& r,
 const Points& points,const std::function<bool()>& available,const char** why=nullptr)
{
    namespace G=dcCartSourcePlanarConeGroups;
    namespace I=dcCartOriginalNodePositiveEarFacets;
    const auto no=[&](const char* s){if(why)*why=s;return false;};
    const auto work=[&](){return !available||available();};
    try
    {
        if(!work()||r.carrier<0||r.parent<0||r.child<0||r.patch<0
            ||r.constituents.size()<2||r.constituents.size()>128
            ||r.outline.size()<3||r.outline.size()>64||!r.role.known
            ||r.role.entity.empty()||r.role.region.empty()||r.role.sha.size()!=64)
            return no("composite-shape-or-BC-binding");
        std::set<label> faces,sources;std::map<label,int> compact;
        std::vector<G::Point> localPoints;std::vector<G::Ring> pieces;
        const auto ring=[&](const std::vector<label>& native,G::Ring& local)->bool
        {
            if(native.size()<3||native.size()>64)return false;
            for(label id:native){if(!work()||id<0||std::size_t(id)>=points.size()||!finite(points[id]))return false;
                auto at=compact.find(id);if(at==compact.end()){
                    if(compact.size()>=512)return false;const point& p=points[id];
                    at=compact.emplace(id,int(localPoints.size())).first;localPoints.push_back({{p.x(),p.y(),p.z()}});}
                local.push_back(at->second);}
            return true;
        };
        for(const auto& f:r.constituents){if(!work()||f.native<0||f.source<0
            ||!faces.insert(f.native).second||f.owner!=r.parent||f.neighbour!=-1
            ||f.patch!=r.patch||f.zone!=r.zone)return no("composite-real-constituent-role");
            sources.insert(f.source);G::Ring local;if(!ring(f.ring,local))return no("composite-real-point-ring");pieces.push_back(std::move(local));}
        if(sources.size()<2||sources.size()>32||*faces.begin()!=r.carrier)return no("composite-distinct-source-bound");
        I::E::Context exact(work);I::E::Vector normal,origin;
        if(!G::Detail::plane(pieces.front(),localPoints,normal,origin,exact))return no("composite-exact-plane");
        for(const auto& p:pieces)if(!G::Detail::aligned(p,localPoints,normal,origin,exact))return no("composite-exact-plane");
        G::Ring boundary;if(!G::Detail::outline(pieces,boundary,exact))return no("composite-directed-outline");
        G::Ring actual;if(!ring(r.outline,actual)||!G::Detail::sameCycle(boundary,actual)
            ||!G::Detail::plane(actual,localPoints,normal,origin,exact))return no("composite-real-outline");
        if(r.pointBits.size()!=compact.size())return no("composite-complete-point-bits");
        for(const auto& p:compact){const auto before=r.pointBits.find(p.first);
            if(before==r.pointBits.end()||dcCartPostCarveCanonicalWallFan::bits(points[p.first])!=before->second)
                return no("composite-current-point-bits");}
        I::Integrals sum;const auto at=[&](label id)->const point&{return points[id];};
        for(const auto& f:r.constituents)I::add(sum,I::integrals(f.ring,at,exact),exact);
        if(!exact.good()||!I::same(sum,I::integrals(r.outline,at,exact),exact)||!exact.good())return no("composite-exact-surface-6V24M");
        return work()||no("clock");
    }
    catch(...){return no("composite-geometry-unknown-or-allocation");}
}

// Same immutable recipe as the normal cone publisher. No slots, births or
// source permissions are published; the synthetic apex ID exceeds old IDs.
template<class Mesh>
bool build
(
    const Mesh& m,label parent,const std::vector<std::vector<label>>& bases,
    const point& proposed,const std::vector<bool>& polygonalWarpBase,
    const std::vector<bool>& polygonalPlanarBase,
    const std::vector<std::vector<label>>& rawCf,
    bool conformingSideUnion,bool planarOutlineEnabled,
    const std::function<bool(label)>& coupledType,
    const std::function<bool()>& available,Parent& out,
    const std::set<label>* wholeColumns=nullptr
)
{
    out=Parent();
    if(parent<0||std::size_t(parent)>=bases.size()
        ||std::size_t(parent)>=rawCf.size()
        ||m.points.size()>=std::size_t(std::numeric_limits<label>::max()))return false;
    const label ap=m.points.size();
    auto clock=[&](){return !available||available();};
    if(wholeColumns&&wholeColumns->count(parent))
    {
        std::vector<LocalFace> shell;
        for(label f:bases[parent])
        {
            if(!clock())return false;
            const bool own=m.owner[f]==parent;
            if(!own&&m.neighbour[f]!=parent)return false;
            shell.push_back({m.faces[f],own,m.patch[f]<0||coupledType(m.patch[f]),f});
        }
        if(shell.size()<4||!clock())return false;
        out.baseFaces=bases[parent];out.baseToChild.assign(bases[parent].size(),0);
        out.children.push_back(std::move(shell));out.known=true;return true;
    }
        S::Plan geometry;geometry.births.push_back({-1,-1,proposed});
        std::vector<std::vector<LocalFace>> children(bases[parent].size());
        std::map<std::pair<label,label>,std::vector<EdgeUse>> edges;
        bool known=true,constructPolygonalSides=false;
        if(conformingSideUnion)for(label original:rawCf[parent])
        {
            if(polygonalWarpBase[original]
                ||(planarOutlineEnabled&&polygonalPlanarBase[original]))
            {constructPolygonalSides=true;break;}
        }
        for(label child=0;child<label(bases[parent].size());++child)
        {
            if(!clock())return false;
            const label f=bases[parent][child];
            const bool own=m.owner[f]==parent;
            if(!own&&m.neighbour[f]!=parent){known=false;break;}
            children[child].push_back
                ({m.faces[f],own,m.patch[f]<0||coupledType(m.patch[f]),f});
            const auto& poly=m.faces[f];
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
    out.baseFaces=bases[parent];out.baseToChild.resize(bases[parent].size());
    for(label j=0;j<label(bases[parent].size());++j)out.baseToChild[j]=j;
    out.geometry=std::move(geometry);
    out.children=std::move(children);out.known=known;
    return true;
}
// Literal full-shell OF cell-centre/volume accumulation from the census.
// Caller owns the original per-child clock checkpoint.
template<class Mesh,class FaceMetric>
bool measureWithMetric(const Mesh& m,const Parent& parent,
    const std::vector<LocalFace>& shell,Metrics& out,FaceMetric faceMetric)
{
    out=Metrics();const auto& geometry=parent.geometry;
            bool valid=shell.size()>=4;point estimate(point::zero);
            scalar selectedArea=0;label selectedCount=0;
            auto& sf=out.sf;auto& fc=out.fc;
            sf.reserve(shell.size());fc.reserve(shell.size());
            for(const auto& f:shell)
            {
                vector a;point c;faceMetric(m,geometry,f,a,c);if(!f.forward)a=-a;
                if(!finite(a)||!finite(c)||mag(a)<=1e-12)valid=false;
                sf.push_back(a);fc.push_back(c);estimate+=c;
                if(f.selected){selectedArea+=mag(a);++selectedCount;}
            }
            if(!valid||!selectedCount||!std::isfinite(selectedArea)||selectedArea<=VSMALL)
            {return false;}
            estimate/=shell.size();scalar volume3=0;point weighted(point::zero);
            vector sum(Zero);
            for(std::size_t j=0;j<shell.size();++j)
            {
                const scalar p=sf[j]&(fc[j]-estimate);volume3+=p;
                weighted+=p*(.75*fc[j]+.25*estimate);sum+=cmptMag(sf[j]);
            }
            const scalar volume=volume3/3;
            if(!std::isfinite(volume3)||volume3<=VSMALL||!finite(weighted)
                ||!finite(weighted/volume3))
            {return false;}
    out.selectedArea=selectedArea;out.selectedCount=selectedCount;
    out.volume=volume;out.centre=weighted/volume3;out.sum=sum;return true;
}
// The census/default caller retains literal face integration. Only an explicit
// caller-owned accessor may reuse immutable face metrics.
template<class Mesh>
bool measure(const Mesh& m,const Parent& parent,
    const std::vector<LocalFace>& shell,Metrics& out)
{
    return measureWithMetric(m,parent,shell,out,
        [](const Mesh& mesh,const S::Plan& geometry,
            const LocalFace& face,vector& area,point& centre)
        {S::metric(mesh,geometry,face.ring,area,centre);});
}
} }
#endif
