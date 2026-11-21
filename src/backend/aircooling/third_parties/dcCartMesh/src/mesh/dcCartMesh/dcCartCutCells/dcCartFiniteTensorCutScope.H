#ifndef dcCartFiniteTensorCutScope_H
#define dcCartFiniteTensorCutScope_H
#include "point.H"
#include <array>
#include <cstdint>
#include <cstring>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <utility>
#include <vector>

namespace Foam { namespace dcCartFiniteTensorCutScope
{
// Geometry preparation only. Neither this policy nor a native label is a
// Source, material, quality, remote-peer or publication permission.
enum class CutScope { Propagating, FiniteTensor };
using Plane=std::pair<label,scalar>;
using Bits=std::array<std::uint64_t,3>;
static constexpr std::size_t volumeParentCap=128,haloParentCap=512;
static constexpr std::size_t surfaceFaceCap=4096,pointCap=8192,ringCap=64;
struct Request
{
    CutScope mode=CutScope::Propagating;
    std::set<label> volumeParents; // Value-owned actual draft cells for this cut.
};
// A genuine old-edge birth request. Local native labels are meaningful only
// with this source mesh; peer matching uses all three binary64 coordinate bits.
struct SurfaceEdgeBirth
{
    label source=-1,root=-1,containingFace=-1,first=-1,second=-1;
    Plane plane{-1,0};
    Bits firstBits{},secondBits{},birthBits{};
};
struct SurfaceHalo
{
    std::set<label> faces,parents;
    // Discovery only: actual crossed-edge processor incidences still require
    // the existing qualified matched request on both ranks. Never a ready plan.
    // Keys are bounded by faces; values are deduplicated planes from the
    // finite value-owned initial request queue (no invented/proposed planes).
    std::map<label,std::vector<Plane>> coupledCuts;
    // Actual crossed-edge incidences, never faces x proposed planes.
    std::map<label,std::vector<SurfaceEdgeBirth>> coupledEdgeBirths;
};
inline Bits pointBits(const point& p)
{
    Bits b{};static_assert(sizeof(scalar)==sizeof(std::uint64_t),"DP finite source refinement");
    for(label k=0;k<3;++k){const scalar v=p[k];std::memcpy(&b[k],&v,sizeof(v));}return b;
}
struct SurfaceRefinementProducer;
class SurfaceRefinement
{
    friend struct SurfaceRefinementProducer;
    label source_=-1,owner_=-1,neighbour_=-1,patch_=-1,zone_=-1;
    std::vector<label> before_,after_;
    std::map<label,std::vector<label>> pieces_;
    std::map<label,Bits> points_;
    SurfaceRefinement()=default;
public:
    label source()const{return source_;}
    label owner()const{return owner_;}
    label neighbour()const{return neighbour_;}
    label patch()const{return patch_;}
    label zone()const{return zone_;}
    const std::vector<label>& before()const{return before_;}
    const std::vector<label>& after()const{return after_;}
    const std::map<label,std::vector<label>>& pieces()const{return pieces_;}
    const std::map<label,Bits>& pointBits()const{return points_;}
};
// Only the actual prepare producer captures a record. Restore independently
// reconstructs its original ring, genuine births and exact Source integrals;
// a private constructor is not used as a substitute for that validation.
struct SurfaceRefinementProducer
{
    template<class Mesh,class Coordinate>
    static std::shared_ptr<const SurfaceRefinement> capture(const Mesh& m,label source,
        const std::vector<label>& after,
        const std::map<label,std::vector<label>>& pieces,Coordinate at,
        const std::function<bool()>& available)
    {
        try
        {
            const auto work=[&](){return !available||available();};
            if(!work()||source<0||std::size_t(source)>=m.faces.size()||!m.faceAlive[source]
                ||m.faces[source].size()<3||m.faces[source].size()>ringCap
                ||after.size()<3||after.size()>ringCap||pieces.empty()
                ||pieces.size()>ringCap||!pieces.count(source))return {};
            std::set<label> outlineIds(after.begin(),after.end()),pieceIds;
            if(outlineIds.size()!=after.size())return {};
            for(const auto& piece:pieces)
            {
                if(!work()||piece.first<0
                    ||(piece.first!=source&&std::size_t(piece.first)<m.faces.size())
                    ||piece.second.size()<3||piece.second.size()>ringCap)return {};
                std::set<label> unique;
                for(label id:piece.second)
                {
                    if(!work()||id<0||!outlineIds.count(id)||!unique.insert(id).second)return {};
                    pieceIds.insert(id);
                }
            }
            if(pieceIds!=outlineIds)return {};
            std::shared_ptr<SurfaceRefinement> p(new SurfaceRefinement);
            p->source_=source;p->owner_=m.owner[source];p->neighbour_=m.neighbour[source];
            p->patch_=m.patch[source];p->zone_=m.zone[source];
            p->before_=m.faces[source];p->after_=after;p->pieces_=pieces;
            for(const auto* ring:{&p->before_,&p->after_})
                for(label id:*ring)
                {
                    if(!work()||id<0)return {};
                    const point& x=at(id);
                    p->points_.emplace(id,dcCartFiniteTensorCutScope::pointBits(x));
                    if(p->points_.size()>2*ringCap)return {};
                }
            return p;
        }
        catch(...){return {};}
    }
    // The original single-ring producer remains a literal one-piece snapshot.
    template<class Mesh,class Coordinate>
    static std::shared_ptr<const SurfaceRefinement> capture(const Mesh& m,label source,
        const std::vector<label>& after,Coordinate at,const std::function<bool()>& available)
    {
        try
        {
            const std::map<label,std::vector<label>> pieces{{source,after}};
            return capture(m,source,after,pieces,at,available);
        }
        catch(...){return {};}
    }
};
} }
#endif
