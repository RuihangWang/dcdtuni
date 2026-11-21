#ifndef dcCartConformingConeSides_H
#define dcCartConformingConeSides_H
#include "dcCartExactBinary64Geometry.H"
#include <map>
#include <set>
#include <vector>
#include <array>
#include <functional>
namespace dcCartConformingConeSides
{
using Label=int;
struct Side {Label owner=-1,neighbour=-1;std::vector<Label> ring;};
struct Plan {bool complete=false;std::size_t joined=0;std::vector<Side> sides;};
// Only freshly constructed INTERNAL cone sides enter. Original source bases
// never enter this function. An unsupported group keeps its original triangles.
// Capacity/clock refusal keeps the entire original side list.
template<class PointAt>
Plan build(const std::vector<Side>& input,PointAt pointAt,
           const std::function<bool()>& available)
{
    namespace E=dcCartExactBinary64Geometry;
    Plan result;
    if(input.size()>512)return result;
    E::Context c(available,131072);
    std::map<std::pair<Label,Label>,std::vector<Side>> groups;
    for(const Side& source:input)
    {
        if(!c.take()||source.owner<0||source.neighbour<0
          ||source.owner==source.neighbour||source.ring.size()!=3)return result;
        Side side=source;
        if(side.owner>side.neighbour)
        {std::swap(side.owner,side.neighbour);std::reverse(side.ring.begin(),side.ring.end());}
        std::set<Label> distinct(side.ring.begin(),side.ring.end());
        if(distinct.size()!=3||*distinct.begin()<0)return result;
        for(Label p:side.ring)for(double coordinate:pointAt(p))
        {
            std::uint64_t bits=0;std::memcpy(&bits,&coordinate,sizeof(bits));
            if(((bits>>52)&2047)==2047)return result;
        }
        groups[{side.owner,side.neighbour}].push_back(std::move(side));
    }
    bool repeated=false;
    for(const auto& entry:groups)repeated=repeated||entry.second.size()>1;
    if(!repeated){result.sides=input;result.complete=c.good();return result;}
    for(const auto& entry:groups)
    {
        const auto& group=entry.second;
        auto keep=[&](){result.sides.insert(result.sides.end(),group.begin(),group.end());};
        if(group.size()==1||group.size()>32){keep();continue;}
        const auto& first=group.front().ring;
        const E::Vector origin=E::vector(pointAt(first[0]),c);
        const E::Vector normal=E::cross(E::difference(E::vector(pointAt(first[1]),c),origin,c),
            E::difference(E::vector(pointAt(first[2]),c),origin,c),c);
        bool valid=E::sign(normal[0])||E::sign(normal[1])||E::sign(normal[2]);
        std::map<std::pair<Label,Label>,std::vector<std::pair<Label,Label>>> uses;
        for(const auto& side:group)
        {
            E::Vector a=E::vector(pointAt(side.ring[0]),c),b=E::vector(pointAt(side.ring[1]),c),
                      d=E::vector(pointAt(side.ring[2]),c);
            // Every triangle has the same exact plane and positive orientation.
            const auto direction=E::dot(E::cross(E::difference(b,a,c),E::difference(d,a,c),c),normal,c);
            valid=valid&&E::sign(direction)>0;
            for(Label p:side.ring)
            {valid=valid&&E::sign(E::dot(normal,E::difference(E::vector(pointAt(p),c),origin,c),c))==0;}
            for(std::size_t j=0;j<3;++j)
            {Label a=side.ring[j],b=side.ring[(j+1)%3];uses[std::minmax(a,b)].emplace_back(a,b);}
        }
        if(!c.good())return Plan();
        std::map<Label,Label> successor;std::map<Label,unsigned> incoming;
        for(const auto& edge:uses)
        {
            const auto& u=edge.second;
            if(u.size()==2)
            {valid=valid&&u[0].first==u[1].second&&u[0].second==u[1].first;}
            else if(u.size()==1)
            {valid=valid&&successor.emplace(u[0]).second;++incoming[u[0].second];}
            else valid=false;
        }
        if(successor.size()<3||successor.size()>64){keep();continue;}
        for(const auto& in:incoming)valid=valid&&in.second==1;
        std::vector<Label> ring;
        Label start=successor.begin()->first,p=start;
        for(std::size_t j=0;j<successor.size()&&valid;++j)
        {
            auto it=successor.find(p);
            if(it==successor.end()||std::find(ring.begin(),ring.end(),p)!=ring.end())
            {valid=false;break;}
            ring.push_back(p);p=it->second;
        }
        valid=valid&&p==start&&ring.size()==successor.size();
        // Exact inward halfspaces also reject crossed/concave outlines. Genuine
        // collinear original edge nodes remain in this internal polygon.
        for(std::size_t j=0;j<ring.size()&&valid;++j)
        {
            auto a=E::vector(pointAt(ring[j]),c);
            auto edge=E::difference(E::vector(pointAt(ring[(j+1)%ring.size()]),c),a,c);
            valid=valid&&(E::sign(edge[0])||E::sign(edge[1])||E::sign(edge[2]));
            for(Label q:ring)
            {valid=valid&&E::sign(E::dot(E::cross(edge,E::difference(E::vector(pointAt(q),c),a,c),c),normal,c))>=0;}
        }
        if(!c.good())return Plan();
        if(!valid){keep();continue;}
        result.sides.push_back(Side{entry.first.first,entry.first.second,std::move(ring)});
        result.joined+=group.size()-1;
    }
    if(result.joined==0)result.sides=input;
    result.complete=c.good();return result;
}
}
#endif
