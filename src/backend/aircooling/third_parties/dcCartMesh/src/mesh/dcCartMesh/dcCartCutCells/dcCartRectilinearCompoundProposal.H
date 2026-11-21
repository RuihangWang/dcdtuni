#ifndef dcCartRectilinearCompoundProposal_H
#define dcCartRectilinearCompoundProposal_H
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

namespace Foam { namespace dcCartRectilinearCompoundProposal
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace E=::dcCartExactBinary64Geometry;
using Plane=std::pair<label,scalar>;
using Edge=std::pair<label,label>;
enum class Kind : label { None=0,RectilinearCompound=1 };
// Search metadata only: this is never Source, current-preimage, peer, Stock,
// embedding, volume-conservation or publication authority.
struct Face
{
    label native=-1,owner=-1,neighbour=-1,patch=-1,zone=-1,axis=-1;
    std::uint64_t coordinateBits=0;
    std::vector<label> outward;
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
        if(!work(available))return fail("rectilinear-clock");
        if(root<0||root>=raw.nCells||!raw.cellAlive[root]||raw.cellRank[root]!=rank
            ||fullCf.size()<6||fullCf.size()>64)return fail("rectilinear-root-or-CF-bound");
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
            if(!work(available))return fail("rectilinear-clock");
            if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]||!faceIds.insert(f).second
                ||raw.owner[f]<0||raw.owner[f]>=raw.nCells||raw.neighbour[f]<-1
                ||raw.neighbour[f]>=raw.nCells||raw.owner[f]==raw.neighbour[f]
                ||(raw.owner[f]==root)==(raw.neighbour[f]==root)
                ||raw.patch[f]<-1||raw.zone[f]<-1
                ||raw.faces[f].size()<3||raw.faces[f].size()>64)
                return fail("rectilinear-complete-native-face");
            Face face;face.native=f;face.owner=raw.owner[f];face.neighbour=raw.neighbour[f];
            face.patch=raw.patch[f];face.zone=raw.zone[f];face.outward=raw.faces[f];
            if(face.owner!=root)std::reverse(face.outward.begin(),face.outward.end());
            std::set<label> unique;
            for(label id:face.outward)
            {
                if(!work(available))return fail("rectilinear-clock");
                if(id<0||std::size_t(id)>=raw.points.size()||!S::finite(raw.points[id])
                    ||!unique.insert(id).second)return fail("rectilinear-native-point");
                const point& q=raw.points[id];
                nodes.emplace(id,q);if(nodes.size()>512)return fail("rectilinear-node-bound");
                const auto alias=coordinateIds.emplace(std::make_tuple(q.x(),q.y(),q.z()),id);
                if(!alias.second&&alias.first->second!=id)return fail("rectilinear-coordinate-alias");
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
                    if(face.axis>=0)return fail("rectilinear-degenerate-face-plane");
                    face.axis=k;face.coordinateBits=bits(x);
                }
            }
            if(face.axis<0)return fail("rectilinear-non-axis-face");
            std::vector<label> along;
            for(std::size_t i=0;i<face.outward.size();++i)
            {
                const label a=face.outward[i],b=face.outward[(i+1)%face.outward.size()];
                label varying=-1;
                for(label k=0;k<3;++k)if(raw.points[a][k]!=raw.points[b][k])
                {if(varying>=0)return fail("rectilinear-non-axis-edge");varying=k;}
                if(varying<0||varying==face.axis)return fail("rectilinear-zero-edge");
                along.push_back(varying);
                auto& e=edges[std::minmax(a,b)];++e.count;e.balance+=a<b?1:-1;e.faces.push_back(f);
                if(e.count>2)return fail("rectilinear-edge-incidence");
            }
            if(!ringSimple(raw,face.outward,along,available))return fail("rectilinear-nonsimple-ring-or-clock");
            p.faces.push_back(std::move(face));
        }
        if(nodes.size()<8||!(p.lo.x()<p.hi.x()&&p.lo.y()<p.hi.y()&&p.lo.z()<p.hi.z()))
            return fail("rectilinear-volume-bounds");
        std::map<label,std::set<label>> faceAdjacency;
        std::map<label,std::map<label,std::vector<label>>> vertexLinks;
        for(const auto& entry:edges)
        {
            if(!work(available))return fail("rectilinear-clock");
            const auto& e=entry.second;
            if(e.count!=2||e.balance!=0||e.faces.size()!=2||e.faces[0]==e.faces[1])
                return fail("rectilinear-directed-edge-closure");
            faceAdjacency[e.faces[0]].insert(e.faces[1]);faceAdjacency[e.faces[1]].insert(e.faces[0]);
            for(label id:{entry.first.first,entry.first.second})
            {vertexLinks[id][e.faces[0]].push_back(e.faces[1]);vertexLinks[id][e.faces[1]].push_back(e.faces[0]);}
        }
        std::set<label> reached;std::vector<label> queue{p.faces.front().native};
        for(std::size_t i=0;i<queue.size();++i)
        {
            if(!work(available))return fail("rectilinear-clock");
            if(!reached.insert(queue[i]).second)continue;
            for(label f:faceAdjacency[queue[i]])if(!reached.count(f))queue.push_back(f);
        }
        if(reached.size()!=p.faces.size())return fail("rectilinear-disconnected-CF");
        for(const auto& vertex:vertexFaces)
        {
            if(!work(available))return fail("rectilinear-clock");
            const auto& links=vertexLinks.at(vertex.first);
            if(links.size()!=vertex.second.size())return fail("rectilinear-vertex-link");
            for(const auto& link:links)if(link.second.size()!=2)return fail("rectilinear-vertex-degree");
            reached.clear();queue.assign(1,*vertex.second.begin());
            for(std::size_t i=0;i<queue.size();++i)
            {
                if(!work(available))return fail("rectilinear-clock");
                if(!reached.insert(queue[i]).second)continue;
                const auto at=links.find(queue[i]);if(at==links.end())return fail("rectilinear-vertex-link");
                for(label f:at->second)if(!reached.count(f))queue.push_back(f);
            }
            if(reached.size()!=vertex.second.size())return fail("rectilinear-pinched-vertex");
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
                if(!work(available))return fail("rectilinear-clock");
                const auto a=ev(face.outward[i]),b=ev(face.outward[i+1]);
                area=E::add(area,E::cross(E::difference(a,first,exact),
                    E::difference(b,first,exact),exact)[face.axis],exact);
                sixVolume=E::add(sixVolume,E::dot(first,E::cross(a,b,exact),exact),exact);
            }
            if(!exact.good()||!E::sign(area))return fail("rectilinear-exact-face-area");
            const scalar x=raw.points[face.outward.front()][face.axis];
            p.internalSupport=p.internalSupport||(p.lo[face.axis]<x&&x<p.hi[face.axis]);
        }
        if(!exact.good()||E::sign(sixVolume)<=0)return fail("rectilinear-exact-positive-volume");
        if(!p.internalSupport)return fail("rectilinear-no-internal-support");
        std::size_t boxes=1;
        for(label k=0;k<3;++k)
        {
            const auto& levels=p.levels[k];
            if(levels.size()<2||levels.size()>65||boxes>64/(levels.size()-1))
                return fail("rectilinear-original-grid-bound");
            boxes*=levels.size()-1;
            for(auto at=std::next(levels.begin());at!=std::prev(levels.end());++at)
                p.originalInternalPlanes.emplace_back(k,*at);
        }
        if(p.originalInternalPlanes.empty())return fail("rectilinear-no-original-planes");
        if(!work(available))return fail("rectilinear-clock");
        p.kind=Kind::RectilinearCompound;out=std::move(p);return true;
    }
    catch(...){out=Proposal();return fail("rectilinear-proposal-exception");}
}
// Recomputed for each actual assigned/incoming union; never cache this result
// as immutable shape eligibility. This does not select Cartesian occupancy.
inline bool withinGrid(const Proposal& p,const std::vector<Plane>& planes,
 const std::function<bool()>& available,std::string* why=nullptr)
{
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(p.kind!=Kind::RectilinearCompound||!work(available))return fail("rectilinear-grid-kind-or-clock");
    auto levels=p.levels;
    for(const auto& plane:planes)
    {
        if(!work(available)||plane.first<0||plane.first>2||!std::isfinite(plane.second)
            ||!(p.lo[plane.first]<plane.second&&plane.second<p.hi[plane.first]))
            return fail("rectilinear-grid-plane");
        levels[plane.first].insert(plane.second);
    }
    std::size_t boxes=1;
    for(const auto& axis:levels)
    {
        if(axis.size()<2||axis.size()>65||boxes>64/(axis.size()-1))
            return fail("rectilinear-grid-bound");
        boxes*=axis.size()-1;
    }
    return work(available);
}
// The longest-span midpoint is explicitly a fresh geometric proposal,
// distinct from all original-node planes. It is never a new Source coordinate.
inline bool factoryPlanes(const Proposal& p,const std::vector<Plane>& assigned,
 const std::function<bool()>& available,std::vector<Plane>& out,
 Plane* proposedMidpoint=nullptr,std::string* why=nullptr)
{
    out.clear();if(proposedMidpoint)*proposedMidpoint={-1,0};
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    try
    {
        if(p.kind!=Kind::RectilinearCompound||!work(available)
            ||!dcCartSignedCrossing::supportedFloatingEnvironment())
            return fail("rectilinear-factory-kind-clock-or-floating-environment");
        label axis=0;S::D::Rational longest=S::D::detail::number(p.hi[0])-S::D::detail::number(p.lo[0]);
        for(label k=1;k<3;++k)
        {
            if(!work(available))return fail("rectilinear-clock");
            const S::D::Rational span=S::D::detail::number(p.hi[k])-S::D::detail::number(p.lo[k]);
            if(span>longest){axis=k;longest=span;}
        }
        const scalar middle=S::D::detail::rounded
            ((S::D::detail::number(p.lo[axis])+S::D::detail::number(p.hi[axis]))/S::D::Rational(2));
        if(!std::isfinite(middle)||!(p.lo[axis]<middle&&middle<p.hi[axis])
            ||!(middle-p.lo[axis]>2*scalar(3e-6)&&p.hi[axis]-middle>2*scalar(3e-6)))
            return fail("rectilinear-midpoint-interior-or-3um");
        std::vector<Plane> planes=p.originalInternalPlanes;
        planes.insert(planes.end(),assigned.begin(),assigned.end());planes.emplace_back(axis,middle);
        std::sort(planes.begin(),planes.end());planes.erase(std::unique(planes.begin(),planes.end()),planes.end());
        if(planes.size()>64||!withinGrid(p,planes,available,why))return false;
        if(!work(available))return fail("rectilinear-clock");
        out=std::move(planes);if(proposedMidpoint)*proposedMidpoint={axis,middle};return true;
    }
    catch(...){out.clear();return fail("rectilinear-factory-exception");}
}
}}
#endif
