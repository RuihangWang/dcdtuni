#ifndef dcCartNativeInterfaceGeometry_H
#define dcCartNativeInterfaceGeometry_H
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartSourcePlanarConeGroups.H"
#include "dcCartOriginalNodePositiveEarFacets.H"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <functional>
#include <map>
#include <set>
#include <vector>
#include <utility>
namespace Foam { namespace dcCartNativeInterfaceGeometry
{
namespace S=dcCartSharedAxisSeamVolumePartition;
// Geometry/provenance only. This record does not contain a BC role or authority.
// Original immutable endpoints and current native endpoints are distinct domains.
struct Record
{
    label carrier=-1,source=-1,owner=-1,neighbour=-1;
    label originalOwner=-1,originalNeighbour=-1;
    label extractionEpoch=-1;std::uint64_t generation=0;
    std::vector<S::Row> constituents;
    std::vector<label> outline;
    std::map<label,std::array<std::uint64_t,3>> pointBits;
    std::map<label,label> endpointChild; // current mother -> genuine future child ordinal
};
inline std::array<std::uint64_t,3> bits(const point& p)
{
    static_assert(sizeof(scalar)==sizeof(std::uint64_t),"native interface binary64 preimage");
    std::array<std::uint64_t,3> out;
    for(label k=0;k<3;++k){const scalar x=p[k];std::memcpy(&out[k],&x,sizeof(x));}
    return out;
}
template<class Points>
bool geometry(const Record& r,const Points& points,const std::function<bool()>& available)
{
    namespace P=::dcCartSourcePlanarConeGroups;
    namespace I=dcCartOriginalNodePositiveEarFacets;
    const auto work=[&](){return !available||available();};
    try
    {
        if(!work()||r.carrier<0||r.source<0||r.owner<0||r.neighbour<0||r.owner==r.neighbour
            ||r.constituents.size()<2||r.constituents.size()>128
            ||r.outline.size()<3||r.outline.size()>64)return false;
        std::set<label> natives;std::map<label,int> compact;
        std::vector<P::Point> localPoints;std::vector<P::Ring> pieces;
        const auto ring=[&](const std::vector<label>& ids,P::Ring& out)
        {
            if(ids.size()<3||ids.size()>64)return false;
            for(label id:ids)
            {
                if(!work()||id<0||std::size_t(id)>=points.size())return false;
                const point& x=points[id];
                if(!S::finite(x))return false;
                auto at=compact.find(id);
                if(at==compact.end())
                {
                    if(compact.size()>=512)return false;
                    at=compact.emplace(id,int(localPoints.size())).first;
                    localPoints.push_back({{x.x(),x.y(),x.z()}});
                }
                out.push_back(at->second);
            }
            return true;
        };
        for(const auto& f:r.constituents)
        {
            if(!work()||f.native<0||!natives.insert(f.native).second||f.source!=r.source
                ||f.owner!=r.owner||f.neighbour!=r.neighbour||f.patch!=-1||f.zone!=-1)return false;
            P::Ring p;if(!ring(f.ring,p))return false;pieces.push_back(std::move(p));
        }
        if(*natives.begin()!=r.carrier)return false;
        I::E::Context exact(work);I::E::Vector normal,origin;
        if(!P::Detail::plane(pieces.front(),localPoints,normal,origin,exact))return false;
        for(const auto& p:pieces)if(!P::Detail::aligned(p,localPoints,normal,origin,exact))return false;
        P::Ring boundary,actual;
        if(!P::Detail::outline(pieces,boundary,exact)||!ring(r.outline,actual)
            ||!P::Detail::sameCycle(boundary,actual)
            ||!P::Detail::plane(actual,localPoints,normal,origin,exact))return false;
        if(r.pointBits.size()!=compact.size())return false;
        for(const auto& p:compact)
        {
            const auto before=r.pointBits.find(p.first);
            if(before==r.pointBits.end()||bits(points[p.first])!=before->second)return false;
        }
        I::Integrals sum;const auto at=[&](label id)->const point&{return points[id];};
        for(const auto& f:r.constituents)I::add(sum,I::integrals(f.ring,at,exact),exact);
        return exact.good()&&I::same(sum,I::integrals(r.outline,at,exact),exact)&&exact.good()&&work();
    }
    catch(...){return false;}
}

// Bounded actual native-piece geometry only. Original Source endpoints, current
// epoch/generation and both future child ordinals are filled by the real caller;
// this builder never assigns immutable origin identity or Source permission.
template<class Points>
bool build(const std::vector<S::Row>& parts,const Points& points,
    const std::function<bool()>& available,Record& out)
{
    out=Record();
    namespace P=::dcCartSourcePlanarConeGroups;
    namespace I=dcCartOriginalNodePositiveEarFacets;
    const auto work=[&](){return !available||available();};
    try
    {
        if(parts.size()<2||parts.size()>128||!work())return false;
        Record draft;draft.source=parts.front().source;
        draft.owner=parts.front().owner;draft.neighbour=parts.front().neighbour;
        if(draft.source<0||draft.owner<0||draft.neighbour<0||draft.owner==draft.neighbour)return false;
        std::set<label> native;std::map<label,int> compact;
        std::vector<label> pointIds;std::vector<P::Point> localPoints;std::vector<P::Ring> rings;
        for(const auto& f:parts)
        {
            if(!work()||f.native<0||!native.insert(f.native).second||f.source!=draft.source
                ||f.owner!=draft.owner||f.neighbour!=draft.neighbour||f.patch!=-1||f.zone!=-1
                ||f.ring.size()<3||f.ring.size()>64)return false;
            P::Ring ring;
            for(label id:f.ring)
            {
                if(!work()||id<0||std::size_t(id)>=points.size()||!S::finite(points[id]))return false;
                auto at=compact.find(id);
                if(at==compact.end())
                {
                    if(compact.size()>=512)return false;
                    const point& x=points[id];
                    at=compact.emplace(id,int(localPoints.size())).first;
                    pointIds.push_back(id);localPoints.push_back({{x.x(),x.y(),x.z()}});
                    draft.pointBits.emplace(id,bits(x));
                }
                ring.push_back(at->second);
            }
            rings.push_back(std::move(ring));
        }
        I::E::Context exact(work);I::E::Vector normal,origin;
        if(!P::Detail::plane(rings.front(),localPoints,normal,origin,exact))return false;
        for(const auto& ring:rings)if(!P::Detail::aligned(ring,localPoints,normal,origin,exact))return false;
        P::Ring outline;
        if(!P::Detail::outline(rings,outline,exact)||outline.size()<3||outline.size()>64
            ||!P::Detail::plane(outline,localPoints,normal,origin,exact)||!exact.good())return false;
        for(int id:outline)
        {
            if(id<0||std::size_t(id)>=pointIds.size())return false;
            draft.outline.push_back(pointIds[id]);
        }
        const auto at=[&](label id)->const point&{return points[id];};I::Integrals sum;
        for(const auto& f:parts)I::add(sum,I::integrals(f.ring,at,exact),exact);
        if(!exact.good()||!I::same(sum,I::integrals(draft.outline,at,exact),exact)
            ||!exact.good()||!work())return false;
        draft.carrier=*native.begin();draft.constituents=parts;
        out=std::move(draft);return true;
    }
    catch(...){out=Record();return false;}
}

inline bool same(const Record& a,const Record& b)
{
    if(a.carrier!=b.carrier||a.source!=b.source||a.owner!=b.owner||a.neighbour!=b.neighbour
        ||a.originalOwner!=b.originalOwner||a.originalNeighbour!=b.originalNeighbour
        ||a.extractionEpoch!=b.extractionEpoch||a.generation!=b.generation
        ||a.outline!=b.outline||a.pointBits!=b.pointBits||a.endpointChild!=b.endpointChild
        ||a.constituents.size()!=b.constituents.size())return false;
    for(std::size_t i=0;i<a.constituents.size();++i)
    {
        const auto& x=a.constituents[i];const auto& y=b.constituents[i];
        if(x.native!=y.native||x.source!=y.source||x.owner!=y.owner||x.neighbour!=y.neighbour
            ||x.patch!=y.patch||x.zone!=y.zone||x.ring!=y.ring)return false;
    }
    return true;
}
} }
#endif
