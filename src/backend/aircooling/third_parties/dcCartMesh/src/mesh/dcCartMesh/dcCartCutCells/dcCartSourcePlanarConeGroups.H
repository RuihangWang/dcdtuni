#ifndef dcCartSourcePlanarConeGroups_H
#define dcCartSourcePlanarConeGroups_H
#include "dcCartExactBinary64Geometry.H"
#include <map>
#include <set>
#include <vector>
#include <array>
#include <algorithm>
namespace dcCartSourcePlanarConeGroups
{
using Label=int;
using Ring=std::vector<Label>;
using Point=dcCartExactBinary64Geometry::Point;
struct Source
{
    Label face=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    bool coupled=false,plainWall=false;
    Ring canonical; // Original-owner orientation; every original outline ID retained.
};
struct Base { Label child=-1,source=-1; bool retainedPhysicalWarp=false; Ring ring; };
struct Side { Label owner=-1,neighbour=-1; Ring ring; };
struct Group
{
    Label source=-1,sourceFace=-1,physicalChild=-1;
    std::vector<Label> children,pieces;
    Ring outer; // Parent-outward source subchain, never a physical boundary proposal.
};
struct Plan {bool complete=false;std::vector<Group> groups;};
namespace Detail
{
namespace E=dcCartExactBinary64Geometry;
using Uses=std::map<std::pair<Label,Label>,std::vector<std::pair<Label,Label>>>;
inline bool sameCycle(const Ring& a,const Ring& b)
{
    if(a.size()!=b.size()||a.empty())return false;
    auto p=std::find(b.begin(),b.end(),a[0]);if(p==b.end())return false;
    for(std::size_t j=0;j<a.size();++j)if(a[j]!=b[(std::size_t(p-b.begin())+j)%b.size()])return false;
    return true;
}
inline Ring reverse(Ring r){if(!r.empty())std::reverse(r.begin()+1,r.end());return r;}
inline bool outline(const std::vector<Ring>& pieces,Ring& result,E::Context& c)
{
    Uses edges;
    for(const Ring& r:pieces)for(std::size_t j=0;j<r.size();++j)
    {
        if(!c.take())return false;
        Label a=r[j],b=r[(j+1)%r.size()];if(a==b)return false;
        edges[std::minmax(a,b)].emplace_back(a,b);
    }
    std::map<Label,Label> next;std::set<Label> incoming;
    for(const auto& e:edges)
    {
        if(!c.take())return false;
        const auto& u=e.second;
        if(u.size()==2){if(u[0].first!=u[1].second||u[0].second!=u[1].first)return false;}
        else if(u.size()==1){if(!next.emplace(u[0]).second||!incoming.insert(u[0].second).second)return false;}
        else return false;
    }
    if(next.size()<3||next.size()>64||incoming.size()!=next.size())return false;
    Ring proposed;Label p=next.begin()->first,start=p;
    for(std::size_t j=0;j<next.size();++j)
    {
        if(!c.take())return false;
        auto i=next.find(p);
        if(i==next.end()||std::find(proposed.begin(),proposed.end(),p)!=proposed.end())return false;
        proposed.push_back(p);p=i->second;
    }
    if(p!=start)return false;result=std::move(proposed);return true;
}
inline bool validRing(const Ring& r,const std::vector<Point>& p,E::Context& c)
{
    if(r.size()<3||r.size()>64)return false;
    std::set<Label> ids;
    std::set<std::array<std::uint64_t,3>> coordinates;
    for(Label id:r)
    {
        if(!c.take()||id<0||std::size_t(id)>=p.size()||!ids.insert(id).second)return false;
        std::array<std::uint64_t,3> b{};
        for(unsigned k=0;k<3;++k)
        {
            std::memcpy(&b[k],&p[id][k],8);
            if(((b[k]>>52)&2047)==2047)return false;
            if((b[k]&0x7fffffffffffffffULL)==0)b[k]=0;
        }
        if(!coordinates.insert(b).second)return false;
    }
    return true;
}
inline bool plane(const Ring& r,const std::vector<Point>& p,E::Vector& n,E::Vector& o,E::Context& c)
{
    if(!validRing(r,p,c))return false;o=E::vector(p[r[0]],c);
    bool positive=false;
    for(std::size_t j=1;j+1<r.size();++j)
    {
        if(!c.take())return false;
        n=E::cross(E::difference(E::vector(p[r[j]],c),o,c),
                   E::difference(E::vector(p[r[j+1]],c),o,c),c);
        if(E::sign(n[0])||E::sign(n[1])||E::sign(n[2])){positive=true;break;}
    }
    if(!positive)return false;
    for(Label id:r)if(E::sign(E::dot(n,E::difference(E::vector(p[id],c),o,c),c)))return false;
    for(std::size_t j=0;j<r.size();++j)
    {
        auto a=E::vector(p[r[j]],c);
        auto edge=E::difference(E::vector(p[r[(j+1)%r.size()]],c),a,c);
        for(Label q:r)if(E::sign(E::dot(E::cross(edge,E::difference(E::vector(p[q],c),a,c),c),n,c))<0)return false;
    }
    return c.good(); // Exact convexity permits original collinear outline nodes.
}
inline bool aligned(const Ring& r,const std::vector<Point>& p,const E::Vector& n,const E::Vector& o,E::Context& c)
{
    E::Vector rn,ro;
    if(!plane(r,p,rn,ro,c)||E::sign(E::dot(rn,n,c))<=0)return false;
    for(Label id:r)if(E::sign(E::dot(n,E::difference(E::vector(p[id],c),o,c),c)))return false;
    return c.good();
}
}
// Proposal only: no geometry/metadata mutation, no source/quality authority.
// Caller must apply a full-star transaction with original V/M1/quality gates,
// and use the same source-face partition on BOTH original native endpoints.
inline Plan build(const std::vector<Point>& points,const std::vector<Source>& sources,
                  const std::vector<Base>& bases,const std::vector<Side>& sides,
                  Label parent,Label apex,const std::function<bool()>& available)
{
    namespace E=dcCartExactBinary64Geometry;
    using namespace Detail;
    Plan out;
    if(points.size()>256||sources.empty()||sources.size()>32||bases.size()>128
       ||sides.size()>512||parent<0||apex<0||std::size_t(apex)>=points.size())return out;
    E::Context c(available,262144);
    std::map<Label,std::size_t> childBase;
    std::vector<std::vector<std::size_t>> fan(sources.size());
    std::vector<Ring> canonical(sources.size());
    std::set<Label> originalFaces;
    for(std::size_t i=0;i<sources.size();++i)
    {
        if(!c.take())return Plan();
        const Source& s=sources[i];
        if(s.face<0||!originalFaces.insert(s.face).second||s.owner<0||(s.owner!=parent&&s.neighbour!=parent)
           ||s.neighbour==s.owner||!validRing(s.canonical,points,c))return Plan();
        canonical[i]=s.owner==parent?s.canonical:reverse(s.canonical);
    }
    for(std::size_t i=0;i<bases.size();++i)
    {
        const Base& b=bases[i];
        if(!c.take()||b.child<0||b.source<0||std::size_t(b.source)>=sources.size()
           ||!childBase.emplace(b.child,i).second||!validRing(b.ring,points,c))return Plan();
        const Source& s=sources[b.source];
        if(b.retainedPhysicalWarp && !(s.owner==parent&&s.neighbour==-1&&s.zone==-1
            &&s.patch>=0&&!s.coupled&&s.plainWall&&sameCycle(b.ring,canonical[b.source])))return Plan();
        fan[b.source].push_back(i);
    }
    // Prove complete source-fan coverage before planning a subset. This holds
    // every nonselected piece and its original ID, even if no proposal is made.
    std::vector<bool> eligible(sources.size(),false);
    for(std::size_t i=0;i<sources.size();++i)
    {
        if(fan[i].empty())return Plan();
        const Source& s=sources[i];
        if(s.neighbour<0||s.patch!=-1||s.zone!=-1||s.coupled||fan[i].size()<2)continue;
        E::Vector n,o;
        if(!plane(canonical[i],points,n,o,c))continue;
        std::vector<Ring> pieces;bool valid=true;
        for(std::size_t j:fan[i])
        {
            if(!aligned(bases[j].ring,points,n,o,c)){valid=false;break;}
            pieces.push_back(bases[j].ring);
        }
        Ring boundary;
        valid=valid&&outline(pieces,boundary,c)&&sameCycle(boundary,canonical[i]);
        if(!c.good())return Plan();
        eligible[i]=valid;
    }
    std::map<std::pair<Label,Label>,std::vector<std::pair<Label,Ring>>> contacts;
    for(const Side& side:sides)
    {
        if(!c.take()||side.owner<0||side.neighbour<0||side.owner==side.neighbour
            ||!childBase.count(side.owner)||!childBase.count(side.neighbour)
            ||side.ring.size()!=3||!validRing(side.ring,points,c)
            ||std::count(side.ring.begin(),side.ring.end(),apex)!=1)return Plan();
        for(unsigned direction=0;direction<2;++direction)
        {
            const Label physical=direction?side.neighbour:side.owner;
            const Label other=direction?side.owner:side.neighbour;
            const Base& pb=bases[childBase.at(physical)],&nb=bases[childBase.at(other)];
            if(!pb.retainedPhysicalWarp||!eligible[nb.source])continue;
            Ring contact=direction?reverse(side.ring):side.ring;
            contacts[{physical,nb.source}].emplace_back(other,std::move(contact));
        }
    }
    std::set<Label> assigned;
    for(const auto& entry:contacts)
    {
        if(!c.take())return Plan();
        const auto& hit=entry.second;
        if(hit.size()<2||hit.size()>32)continue;
        Label physical=entry.first.first,source=entry.first.second;
        const Ring& physicalRing=bases[childBase.at(physical)].ring;
        std::set<std::size_t> edges;std::set<Label> children;
        std::vector<Ring> part;bool valid=true;
        E::Vector n,o;
        if(!plane(hit.front().second,points,n,o,c))continue;
        for(const auto& h:hit)
        {
            if(!children.insert(h.first).second||assigned.count(h.first)
                ||!aligned(h.second,points,n,o,c)){valid=false;break;}
            const Ring& r=h.second;Ring edge;
            for(Label id:r)if(id!=apex)edge.push_back(id);
            bool found=false;
            for(std::size_t j=0;j<physicalRing.size();++j)
            {
                Label a=physicalRing[j],b=physicalRing[(j+1)%physicalRing.size()];
                if((a==edge[0]&&b==edge[1])||(a==edge[1]&&b==edge[0]))
                {found=edges.insert(j).second;break;}
            }
            if(!found){valid=false;break;}
            part.push_back(bases[childBase.at(h.first)].ring);
        }
        if(!c.good())return Plan();
        if(!valid||edges.size()!=children.size()||edges.size()>=physicalRing.size())continue;
        unsigned starts=0;
        for(std::size_t j:edges)if(!edges.count((j+physicalRing.size()-1)%physicalRing.size()))++starts;
        if(starts!=1)continue; // A single contiguous physical outline chain only.
        Ring outer;
        if(!outline(part,outer,c))continue;
        E::Vector sn,so;
        if(!plane(canonical[source],points,sn,so,c)||!aligned(outer,points,sn,so,c))continue;
        if(!c.good())return Plan();
        Group g;g.source=source;g.sourceFace=sources[source].face;g.physicalChild=physical;g.outer=std::move(outer);
        for(Label child:children){g.children.push_back(child);g.pieces.push_back(Label(childBase.at(child)));assigned.insert(child);}
        out.groups.push_back(std::move(g));
    }
    out.complete=c.good();return out.complete?out:Plan();
}
}
#endif
