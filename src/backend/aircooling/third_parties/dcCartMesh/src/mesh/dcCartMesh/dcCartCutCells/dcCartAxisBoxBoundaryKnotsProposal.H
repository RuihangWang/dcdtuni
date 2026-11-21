#ifndef dcCartAxisBoxBoundaryKnotsProposal_H
#define dcCartAxisBoxBoundaryKnotsProposal_H
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartExactBinary64Geometry.H"
#include <algorithm>
#include <array>
#include <functional>
#include <iterator>
#include <cstdint>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <vector>

namespace Foam { namespace dcCartAxisBoxBoundaryKnotsProposal
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace E=::dcCartExactBinary64Geometry;
using Plane=std::pair<label,scalar>;
using Edge=std::pair<label,label>;
enum class Kind : label { None=0,AxisBoxBoundaryKnots=2 };
// Search metadata only: this is never Source, current-preimage, peer, Stock,
// embedding, volume-conservation or publication authority.
struct Face
{
    label native=-1,owner=-1,neighbour=-1,patch=-1,zone=-1,axis=-1;
    std::uint64_t coordinateBits=0;
    std::vector<label> outward;
};
struct BoundaryTile
{
    label axis=-1,side=-1,u=-1,v=-1,native=-1;
};
// Literal old-node witnesses only; native IDs retain their original tuples.
// These are geometric proposal coordinates, never Source permission.
struct Knot
{
    Plane plane{-1,0};
    std::vector<label> oldNodes,nativeFaces;
};
struct Proposal
{
    Kind kind=Kind::None;
    const void* immutableRaw=nullptr;
    label root=-1,rank=-1;
    std::size_t rawPoints=0,rawFaces=0,rawCells=0;
    std::vector<Face> faces;
    std::map<label,std::array<std::uint64_t,3>> pointBits;
    std::array<std::set<scalar>,3> levels;
    point lo=point::zero,hi=point::zero;
    std::vector<Plane> originalInternalPlanes;
    bool internalSupport=false;
    std::vector<BoundaryTile> boundaryTiles;
    std::vector<Knot> oldKnots;
};
inline std::uint64_t bits(scalar x)
{std::uint64_t b=0;static_assert(sizeof(x)==sizeof(b),"DP proposal bits");std::memcpy(&b,&x,sizeof(b));return b;}
inline bool work(const std::function<bool()>& available){return S::clock(available);}
inline bool within(scalar x,scalar a,scalar b)
{return std::min(a,b)<=x&&x<=std::max(a,b);}
template<class Raw>
bool ringSimple(const Raw& raw,const std::vector<label>& ring,
 const std::vector<label>& along,const std::function<bool()>& available)
{
    for(std::size_t i=0;i<ring.size();++i)for(std::size_t j=i+1;j<ring.size();++j)
    {
        if(!work(available))return false;
        const point& a=raw.points[ring[i]];const point& b=raw.points[ring[(i+1)%ring.size()]];
        const point& c=raw.points[ring[j]];const point& d=raw.points[ring[(j+1)%ring.size()]];
        const label u=along[i],v=along[j];
        const bool adjacent=j==i+1||(i==0&&j+1==ring.size());
        bool intersects=false;
        if(u==v)
        {
            bool collinear=true;
            for(label k=0;k<3;++k)if(k!=u)collinear=collinear&&a[k]==c[k];
            const scalar low=std::max(std::min(a[u],b[u]),std::min(c[u],d[u]));
            const scalar high=std::min(std::max(a[u],b[u]),std::max(c[u],d[u]));
            intersects=collinear&&low<=high;
            // Adjacent collinear birth/outline nodes are legal; reversal or
            // positive-length overlap is not a simple native ring.
            if(adjacent){if(collinear&&low<high)return false;continue;}
        }
        else intersects=within(c[u],a[u],b[u])&&within(a[v],c[v],d[v]);
        if(!adjacent&&intersects)return false;
    }
    return work(available);
}
template<class Raw>
bool qualify(const Raw& raw,label root,const std::vector<label>& fullCf,label rank,
 const std::function<bool()>& available,Proposal& out,std::string* why=nullptr)
{
    out=Proposal();
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    try
    {
        if(!work(available))return fail("axis-box-clock");
        if(root<0||root>=raw.nCells||!raw.cellAlive[root]||raw.cellRank[root]!=rank
            ||fullCf.size()<6||fullCf.size()>64)return fail("axis-box-root-or-CF-bound");
        Proposal p;p.immutableRaw=&raw;p.root=root;p.rank=rank;
        p.rawPoints=raw.points.size();p.rawFaces=raw.faces.size();p.rawCells=raw.nCells;
        p.lo=point(GREAT,GREAT,GREAT);p.hi=point(-GREAT,-GREAT,-GREAT);
        std::set<label> faceIds;
        std::map<label,point> nodes;
        std::map<std::tuple<scalar,scalar,scalar>,label> coordinateIds;
        struct Incidence{label count=0,balance=0;std::vector<label> faces;};
        std::map<Edge,Incidence> edges;
        std::map<label,std::set<label>> vertexFaces;
        for(label f:fullCf)
        {
            if(!work(available))return fail("axis-box-clock");
            if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]||!faceIds.insert(f).second
                ||raw.owner[f]<0||raw.owner[f]>=raw.nCells||raw.neighbour[f]<-1
                ||raw.neighbour[f]>=raw.nCells||raw.owner[f]==raw.neighbour[f]
                ||(raw.owner[f]==root)==(raw.neighbour[f]==root)
                ||raw.patch[f]<-1||raw.zone[f]<-1
                ||raw.faces[f].size()<3||raw.faces[f].size()>64)
                return fail("axis-box-complete-native-face");
            Face face;face.native=f;face.owner=raw.owner[f];face.neighbour=raw.neighbour[f];
            face.patch=raw.patch[f];face.zone=raw.zone[f];face.outward=raw.faces[f];
            if(face.owner!=root)std::reverse(face.outward.begin(),face.outward.end());
            std::set<label> unique;
            for(label id:face.outward)
            {
                if(!work(available))return fail("axis-box-clock");
                if(id<0||std::size_t(id)>=raw.points.size()||!S::finite(raw.points[id])
                    ||!unique.insert(id).second)return fail("axis-box-native-point");
                const point& q=raw.points[id];
                nodes.emplace(id,q);if(nodes.size()>512)return fail("axis-box-node-bound");
                const auto alias=coordinateIds.emplace(std::make_tuple(q.x(),q.y(),q.z()),id);
                if(!alias.second&&alias.first->second!=id)return fail("axis-box-coordinate-alias");
                p.pointBits.emplace(id,std::array<std::uint64_t,3>{{bits(q.x()),bits(q.y()),bits(q.z())}});
                for(label k=0;k<3;++k)p.levels[k].insert(q[k]);
                p.lo=min(p.lo,q);p.hi=max(p.hi,q);vertexFaces[id].insert(f);
            }
            for(label k=0;k<3;++k)
            {
                bool same=true;const scalar x=raw.points[face.outward.front()][k];
                for(label id:face.outward)same=same&&raw.points[id][k]==x;
                if(same)
                {
                    if(face.axis>=0)return fail("axis-box-degenerate-face-plane");
                    face.axis=k;face.coordinateBits=bits(x);
                }
            }
            if(face.axis<0)return fail("axis-box-non-axis-face");
            std::vector<label> along;
            for(std::size_t i=0;i<face.outward.size();++i)
            {
                const label a=face.outward[i],b=face.outward[(i+1)%face.outward.size()];
                label varying=-1;
                for(label k=0;k<3;++k)if(raw.points[a][k]!=raw.points[b][k])
                {if(varying>=0)return fail("axis-box-non-axis-edge");varying=k;}
                if(varying<0||varying==face.axis)return fail("axis-box-zero-edge");
                along.push_back(varying);
                auto& e=edges[std::minmax(a,b)];++e.count;e.balance+=a<b?1:-1;e.faces.push_back(f);
                if(e.count>2)return fail("axis-box-edge-incidence");
            }
            if(!ringSimple(raw,face.outward,along,available))return fail("axis-box-nonsimple-ring-or-clock");
            p.faces.push_back(std::move(face));
        }
        if(nodes.size()<8||!(p.lo.x()<p.hi.x()&&p.lo.y()<p.hi.y()&&p.lo.z()<p.hi.z()))
            return fail("axis-box-volume-bounds");
        std::map<label,std::set<label>> faceAdjacency;
        std::map<label,std::map<label,std::vector<label>>> vertexLinks;
        for(const auto& entry:edges)
        {
            if(!work(available))return fail("axis-box-clock");
            const auto& e=entry.second;
            if(e.count!=2||e.balance!=0||e.faces.size()!=2||e.faces[0]==e.faces[1])
                return fail("axis-box-directed-edge-closure");
            faceAdjacency[e.faces[0]].insert(e.faces[1]);faceAdjacency[e.faces[1]].insert(e.faces[0]);
            for(label id:{entry.first.first,entry.first.second})
            {vertexLinks[id][e.faces[0]].push_back(e.faces[1]);vertexLinks[id][e.faces[1]].push_back(e.faces[0]);}
        }
        std::set<label> reached;std::vector<label> queue{p.faces.front().native};
        for(std::size_t i=0;i<queue.size();++i)
        {
            if(!work(available))return fail("axis-box-clock");
            if(!reached.insert(queue[i]).second)continue;
            for(label f:faceAdjacency[queue[i]])if(!reached.count(f))queue.push_back(f);
        }
        if(reached.size()!=p.faces.size())return fail("axis-box-disconnected-CF");
        for(const auto& vertex:vertexFaces)
        {
            if(!work(available))return fail("axis-box-clock");
            const auto& links=vertexLinks.at(vertex.first);
            if(links.size()!=vertex.second.size())return fail("axis-box-vertex-link");
            for(const auto& link:links)if(link.second.size()!=2)return fail("axis-box-vertex-degree");
            reached.clear();queue.assign(1,*vertex.second.begin());
            for(std::size_t i=0;i<queue.size();++i)
            {
                if(!work(available))return fail("axis-box-clock");
                if(!reached.insert(queue[i]).second)continue;
                const auto at=links.find(queue[i]);if(at==links.end())return fail("axis-box-vertex-link");
                for(label f:at->second)if(!reached.count(f))queue.push_back(f);
            }
            if(reached.size()!=vertex.second.size())return fail("axis-box-pinched-vertex");
        }
        E::Context exact([&](){return work(available);});E::Dyadic sixVolume;
        const point& reference=nodes.begin()->second;
        const auto ev=[&](label id)
        {
            const point& q=raw.points[id];
            return E::difference(E::vector(E::Point{{q.x(),q.y(),q.z()}},exact),
                E::vector(E::Point{{reference.x(),reference.y(),reference.z()}},exact),exact);
        };
        for(const auto& face:p.faces)
        {
            const auto first=ev(face.outward.front());E::Dyadic area;
            for(std::size_t i=1;i+1<face.outward.size();++i)
            {
                if(!work(available))return fail("axis-box-clock");
                const auto a=ev(face.outward[i]),b=ev(face.outward[i+1]);
                area=E::add(area,E::cross(E::difference(a,first,exact),
                    E::difference(b,first,exact),exact)[face.axis],exact);
                sixVolume=E::add(sixVolume,E::dot(first,E::cross(a,b,exact),exact),exact);
            }
            if(!exact.good()||!E::sign(area))return fail("axis-box-exact-face-area");
            const scalar x=raw.points[face.outward.front()][face.axis];
            p.internalSupport=p.internalSupport||(p.lo[face.axis]<x&&x<p.hi[face.axis]);
        }
        if(!exact.good()||E::sign(sixVolume)<=0)return fail("axis-box-exact-positive-volume");
        std::size_t boxes=1;
        for(label k=0;k<3;++k)
        {
            const auto& levels=p.levels[k];
            if(levels.size()<2||levels.size()>65||boxes>64/(levels.size()-1))
                return fail("axis-box-original-grid-bound");
            boxes*=levels.size()-1;
            for(auto at=std::next(levels.begin());at!=std::prev(levels.end());++at)
                p.originalInternalPlanes.emplace_back(k,*at);
        }
        // Prove the entire bbox boundary, separately for all six sides.
        // Indices are exact ranks of existing binary64 coordinates. Every
        // elementary open tile has an odd integer centre; ring edges are on
        // even grid coordinates, so the parity test cannot touch a boundary.
        std::array<std::map<scalar,label>,3> index;
        for(label k=0;k<3;++k)
        {
            label j=0;for(scalar x:p.levels[k])index[k].emplace(x,j++);
        }
        std::map<std::pair<label,label>,std::vector<const Face*>> sides;
        for(const auto& face:p.faces)
        {
            if(!work(available))return fail("axis-box-clock");
            const scalar x=raw.points[face.outward.front()][face.axis];
            const label side=x==p.lo[face.axis]?0:(x==p.hi[face.axis]?1:-1);
            if(side<0)return fail("axis-box-not-bbox-boundary");
            const label u=(face.axis+1)%3,v=(face.axis+2)%3;
            std::int64_t twiceArea=0;
            for(std::size_t i=0;i<face.outward.size();++i)
            {
                if(!work(available))return fail("axis-box-clock");
                const point& a=raw.points[face.outward[i]];
                const point& b=raw.points[face.outward[(i+1)%face.outward.size()]];
                twiceArea+=std::int64_t(index[u].at(a[u]))*index[v].at(b[v])
                          -std::int64_t(index[u].at(b[u]))*index[v].at(a[v]);
            }
            // Cyclic projection (y,z), (z,x), (x,y) has positive axis normal.
            if((side==0&&twiceArea>=0)||(side==1&&twiceArea<=0))
                return fail("axis-box-boundary-outward");
            sides[{face.axis,side}].push_back(&face);
        }
        for(label axis=0;axis<3;++axis)for(label side=0;side<2;++side)
        {
            if(!work(available))return fail("axis-box-clock");
            const auto found=sides.find({axis,side});
            if(found==sides.end()||found->second.empty())
                return fail("axis-box-missing-bbox-side");
            const label u=(axis+1)%3,v=(axis+2)%3;
            const label nu=label(p.levels[u].size()-1),nv=label(p.levels[v].size()-1);
            for(label iu=0;iu<nu;++iu)for(label iv=0;iv<nv;++iv)
            {
                if(!work(available))return fail("axis-box-clock");
                const label cu=2*iu+1,cv=2*iv+1;
                label coverage=0,native=-1;
                for(const Face* face:found->second)
                {
                    bool inside=false;
                    for(std::size_t i=0;i<face->outward.size();++i)
                    {
                        if(!work(available))return fail("axis-box-clock");
                        const point& a=raw.points[face->outward[i]];
                        const point& b=raw.points[face->outward[(i+1)%face->outward.size()]];
                        const label au=2*index[u].at(a[u]),av=2*index[v].at(a[v]);
                        const label bu=2*index[u].at(b[u]),bv=2*index[v].at(b[v]);
                        // Only a vertical edge can straddle this odd v.
                        if((av>cv)!=(bv>cv))
                        {
                            if(au!=bu)return fail("axis-box-grid-non-axis-edge");
                            if(au>cu)inside=!inside;
                        }
                    }
                    if(inside){++coverage;native=face->native;}
                }
                if(coverage!=1)return fail(coverage?"axis-box-bbox-tile-overlap":"axis-box-bbox-tile-gap");
                if(p.boundaryTiles.size()>=384)return fail("axis-box-boundary-tile-bound");
                BoundaryTile tile;tile.axis=axis;tile.side=side;tile.u=iu;tile.v=iv;tile.native=native;
                p.boundaryTiles.push_back(tile);
            }
        }
        if(p.originalInternalPlanes.empty())return fail("axis-box-no-boundary-knot");
        for(const auto& plane:p.originalInternalPlanes)
        {
            if(!work(available))return fail("axis-box-clock");
            Knot knot;knot.plane=plane;
            std::set<label> supportingFaces;
            for(const auto& node:nodes)if(node.second[plane.first]==plane.second)
            {
                knot.oldNodes.push_back(node.first);
                supportingFaces.insert(vertexFaces.at(node.first).begin(),vertexFaces.at(node.first).end());
            }
            if(knot.oldNodes.empty()||supportingFaces.empty())
                return fail("axis-box-old-knot-witness");
            knot.nativeFaces.assign(supportingFaces.begin(),supportingFaces.end());
            p.oldKnots.push_back(std::move(knot));
        }
        if(!work(available))return fail("axis-box-clock");
        p.kind=Kind::AxisBoxBoundaryKnots;out=std::move(p);return true;
    }
    catch(...){out=Proposal();return fail("axis-box-proposal-exception");}
}
// Recomputed for each actual assigned/incoming union; never cache this result
// as immutable shape eligibility. This does not select Cartesian occupancy.
inline bool withinGrid(const Proposal& p,const std::vector<Plane>& planes,
 const std::function<bool()>& available,std::string* why=nullptr)
{
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(p.kind!=Kind::AxisBoxBoundaryKnots||!work(available))return fail("axis-box-grid-kind-or-clock");
    auto levels=p.levels;
    for(const auto& plane:planes)
    {
        if(!work(available)||plane.first<0||plane.first>2||!std::isfinite(plane.second)
            ||!(p.lo[plane.first]<plane.second&&plane.second<p.hi[plane.first]))
            return fail("axis-box-grid-plane");
        levels[plane.first].insert(plane.second);
    }
    std::size_t boxes=1;
    for(const auto& axis:levels)
    {
        if(axis.size()<2||axis.size()>65||boxes>64/(axis.size()-1))
            return fail("axis-box-grid-bound");
        boxes*=axis.size()-1;
    }
    return work(available);
}
// Only actual incoming planes and literal old-node boundary knots are emitted.
// No midpoint or synthetic supporting coordinate is invented by this family.
inline bool factoryPlanes(const Proposal& p,const std::vector<Plane>& assigned,
 const std::function<bool()>& available,std::vector<Plane>& out,
 Plane* proposedMidpoint=nullptr,std::string* why=nullptr)
{
    out.clear();if(proposedMidpoint)*proposedMidpoint={-1,0};
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    try
    {
        if(p.kind!=Kind::AxisBoxBoundaryKnots||p.oldKnots.empty()
            ||p.boundaryTiles.empty()||!work(available)
            ||!dcCartSignedCrossing::supportedFloatingEnvironment())
            return fail("axis-box-factory-kind-clock-or-floating-environment");
        // A shape alone cannot initiate promotion; the typed caller must supply
        // at least one plane bound to an actual failed-shell used birth.
        if(assigned.empty())return fail("axis-box-no-actual-incoming");
        std::vector<Plane> planes=p.originalInternalPlanes;
        planes.insert(planes.end(),assigned.begin(),assigned.end());
        std::sort(planes.begin(),planes.end());
        planes.erase(std::unique(planes.begin(),planes.end()),planes.end());
        if(planes.size()>64)return fail("axis-box-plane-count-bound");
        if(!withinGrid(p,planes,available,why))return false;
        if(!work(available))return fail("axis-box-clock");
        out=std::move(planes);return true;
    }
    catch(...){out.clear();return fail("axis-box-factory-exception");}
}
}}
#endif
