#ifndef dcCartFiniteOutsidePromotionMetadata_H
#define dcCartFiniteOutsidePromotionMetadata_H
#include "dcCartFiniteTensorCutScope.H"
#include <functional>
#include <map>
#include <set>
#include <tuple>
#include <vector>
#include <cstring>
#include <cmath>
#include <utility>

namespace Foam { namespace dcCartFiniteOutsidePromotionMetadata
{
using Plane=dcCartFiniteTensorCutScope::Plane;
// Production proposal provenance only. Never a Source/peer/quality permission.
struct Incidence
{
    label root=-1,sourceFace=-1,owner=-1,neighbour=-1,a=-1,b=-1;
    Plane plane={-1,0};
};
class Recorder
{
    using Key=std::tuple<label,label,label,label,label,std::uint64_t>;
    std::set<Key> seen_;
    std::function<bool(label)> interested_;
    std::function<bool()> available_;
    bool enabled_=false;
    void incomplete(const char* why) noexcept
    {complete=false;reason=why;hits.clear();seen_.clear();}
public:
    std::vector<Incidence> hits;
    bool complete=false;
    const char* reason="outside-metadata-disabled";
    void configure(std::function<bool(label)> interested,std::function<bool()> available)
    {
        interested_=std::move(interested);available_=std::move(available);
        enabled_=bool(interested_);reset();
    }
    void disable() noexcept
    {enabled_=false;incomplete("outside-metadata-unavailable");}
    void reset() noexcept
    {hits.clear();seen_.clear();complete=enabled_;reason=enabled_?"complete":"outside-metadata-disabled";}
    // Called only after the original strict crossed-edge hasEdge test and
    // haloFace(sourceFace) succeeded. The caller supplies literal existing data.
    // Exceptions/capacity/work loss only disable this optional proposal record.
    template<class Declared>
    void record(label sourceFace,label owner,label neighbour,label a,label b,
        const Plane& plane,const Declared& initialVolumeRoots) noexcept
    {
        if(!complete||!enabled_)return;
        try
        {
            if(sourceFace<0||owner<0||neighbour<-1||owner==neighbour
                ||a<0||b<0||a>=b||plane.first<0||plane.first>2||!std::isfinite(plane.second))
            {incomplete("outside-metadata-invalid");return;}
            std::uint64_t bits=0;static_assert(sizeof(scalar)==sizeof(bits),"binary64 plane identity");
            std::memcpy(&bits,&plane.second,sizeof(bits));
            for(label root:{owner,neighbour})
            {
                if(root<0||initialVolumeRoots.count(root))continue; // explicitly nonvolume only
                if(available_&&!available_()){incomplete("outside-metadata-work");return;}
                if(!interested_(root))continue; // selection is metadata only
                const Key key=std::make_tuple(root,sourceFace,a,b,plane.first,bits);
                if(seen_.count(key))continue;
                if(hits.size()>=dcCartFiniteTensorCutScope::surfaceFaceCap)
                {incomplete("outside-metadata-capacity");return;}
                seen_.insert(key);hits.push_back({root,sourceFace,owner,neighbour,a,b,plane});
            }
        }
        catch(...){incomplete("outside-metadata-exception");}
    }
};
}}
#endif
