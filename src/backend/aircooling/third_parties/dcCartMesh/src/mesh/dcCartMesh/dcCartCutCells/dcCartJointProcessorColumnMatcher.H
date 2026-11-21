#ifndef dcCartJointProcessorColumnMatcher_H
#define dcCartJointProcessorColumnMatcher_H
#include "label.H"
#include "scalar.H"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace Foam { namespace dcCartJointProcessorColumnMatcher
{
using Point=std::array<scalar,3>;
using Ring=std::vector<Point>;
using PointBits=std::array<std::uint64_t,3>;
using RingBits=std::vector<PointBits>;
using Available=std::function<bool()>;
struct Source
{
    // Existing qualified source key, not a new coordinate-only identity.
    std::string key,patch;
    label rank=-1,peerRank=-1,nativeFace=-1,owner=-1;
    int sense=0;
};
struct Plane
{
    int axis=-1;
    std::uint64_t coordinateBits=0;
};
enum class RequestKind : unsigned char {VolumePlane,EndpointOnly,SurfaceEdgeBirth};
struct EdgeBirth
{
    Plane plane;
    PointBits first{},second{},birth{}; // Canonical binary64 endpoint order.
};
// An endpoint-only request requires a real paired future endpoint but no cut.
// Its unused Plane is not a slicing instruction or material/source permission.
struct Request
{std::string sourceKey;label requester=-1;Plane plane;bool endpointOnly=false;
 RequestKind kind=RequestKind::VolumePlane;EdgeBirth edgeBirth;};
struct LocalRequest
{Source source;Plane plane;bool endpointOnly=false;
 RequestKind kind=RequestKind::VolumePlane;EdgeBirth edgeBirth;};
inline RequestKind requestKind(const Request& r)
{return r.kind==RequestKind::SurfaceEdgeBirth?r.kind:
    (r.endpointOnly?RequestKind::EndpointOnly:RequestKind::VolumePlane);}
inline RequestKind requestKind(const LocalRequest& r)
{return r.kind==RequestKind::SurfaceEdgeBirth?r.kind:
    (r.endpointOnly?RequestKind::EndpointOnly:RequestKind::VolumePlane);}
struct Schedule
{
    std::vector<LocalRequest> local;
    std::map<std::string,std::vector<Plane>> required;
    std::map<std::string,std::vector<EdgeBirth>> requiredBirths;
    bool ready=false;
};
enum class EndpointKind : unsigned char {NativeChild,PlannedChild};
struct EndpointIdentity
{
    EndpointKind kind=EndpointKind::NativeChild;
    label nativeChild=-1,sourceParent=-1,plannedChildOrdinal=-1;
};
struct Subface
{
    // Actual physical piece slot. A planned recipe ordinal is not a native child.
    label nativeFace=-1;
    EndpointIdentity endpoint;
    Ring outwardRing;
    Point centre{{0,0,0}};
    scalar volume=0;
};
struct PreparedOffer
{
    Source source;std::vector<Plane> planes;
    std::vector<EdgeBirth> edgeBirths;
    std::vector<Subface> faces;
    // Caller sets this only after its complete local shell/source/VM preflight.
    bool prepared=false;
};
// Current offer-construction readiness, not the final publication vote.
struct RankVote {label rank=-1;bool ready=false;};
struct RemoteEndpoint
{
    std::string sourceKey;
    label rank=-1,nativeFace=-1;
    EndpointIdentity endpoint;
    Point centre{{0,0,0}};
    scalar volume=0;
};
struct MatchResult
{
    std::map<std::pair<label,label>,RemoteEndpoint> peers;
    bool ready=false;
};
namespace Detail
{
inline bool work(const Available& available){return !available||available();}
inline bool dp()
{return sizeof(scalar)==sizeof(std::uint64_t)&&std::numeric_limits<scalar>::is_iec559;}
inline std::uint64_t bits(scalar value)
{std::uint64_t result=0;std::memcpy(&result,&value,sizeof(result));return result;}
inline bool plane(const Plane& p)
{
    if(!dp()||p.axis<0||p.axis>2)return false;
    scalar value=0;std::memcpy(&value,&p.coordinateBits,sizeof(value));
    return std::isfinite(value);
}
inline bool samePlane(const Plane& a,const Plane& b)
{return a.axis==b.axis&&a.coordinateBits==b.coordinateBits;}
inline bool lessPlane(const Plane& a,const Plane& b)
{return a.axis!=b.axis?a.axis<b.axis:a.coordinateBits<b.coordinateBits;}
inline bool normalizePlanes(const std::vector<Plane>& input,const Available& available,
                            std::vector<Plane>& out)
{
    out.clear();if(input.empty())return false;
    for(const auto& p:input)
    {if(!work(available)||!plane(p))return false;out.push_back(p);}
    std::sort(out.begin(),out.end(),lessPlane);
    out.erase(std::unique(out.begin(),out.end(),samePlane),out.end());
    return work(available);
}
inline bool samePlanes(const std::vector<Plane>& a,const std::vector<Plane>& b)
{
    if(a.size()!=b.size())return false;
    for(std::size_t j=0;j<a.size();++j)if(!samePlane(a[j],b[j]))return false;
    return true;
}
inline bool validBirth(const EdgeBirth& b)
{
    if(!plane(b.plane)||!(b.first<b.second)||b.birth==b.first||b.birth==b.second)return false;
    for(const auto* values:{&b.first,&b.second,&b.birth})for(auto bits:*values)
    {scalar value=0;std::memcpy(&value,&bits,sizeof(value));if(!std::isfinite(value))return false;}
    return true;
}
inline bool lessBirth(const EdgeBirth& a,const EdgeBirth& b)
{
    if(lessPlane(a.plane,b.plane))return true;if(lessPlane(b.plane,a.plane))return false;
    if(a.first!=b.first)return a.first<b.first;
    if(a.second!=b.second)return a.second<b.second;return a.birth<b.birth;
}
inline bool sameBirth(const EdgeBirth& a,const EdgeBirth& b)
{return samePlane(a.plane,b.plane)&&a.first==b.first&&a.second==b.second&&a.birth==b.birth;}
inline bool normalizeBirths(const std::vector<EdgeBirth>& input,const Available& available,
 std::vector<EdgeBirth>& out)
{
    out.clear();for(const auto& b:input)
    {if(!work(available)||!validBirth(b))return false;out.push_back(b);}
    std::sort(out.begin(),out.end(),lessBirth);
    out.erase(std::unique(out.begin(),out.end(),sameBirth),out.end());return work(available);
}
inline bool sameBirths(const std::vector<EdgeBirth>& a,const std::vector<EdgeBirth>& b)
{
    if(a.size()!=b.size())return false;
    for(std::size_t j=0;j<a.size();++j)if(!sameBirth(a[j],b[j]))return false;return true;
}
inline bool sameSource(const Source& a,const Source& b)
{return a.key==b.key&&a.patch==b.patch&&a.rank==b.rank&&a.peerRank==b.peerRank
    &&a.nativeFace==b.nativeFace&&a.owner==b.owner&&a.sense==b.sense;}
inline bool validSource(const Source& s,label ranks)
{
    if(s.rank<0||s.rank>=ranks||s.peerRank<0||s.peerRank>=ranks||s.rank==s.peerRank
      ||s.nativeFace<0||s.owner<0||(s.sense!=1&&s.sense!=-1))return false;
    if(s.patch!="procBoundary"+std::to_string(s.rank)+"to"+std::to_string(s.peerRank))return false;
    const std::string prefix="processor:"+std::to_string(std::min(s.rank,s.peerRank))
        +":"+std::to_string(std::max(s.rank,s.peerRank))+":";
    return s.key.size()>prefix.size()&&s.key.compare(0,prefix.size(),prefix)==0;
}
using Sources=std::map<std::string,std::array<Source,2>>;
inline bool sources(const std::vector<Source>& input,label ranks,
                    const Available& available,Sources& out)
{
    out.clear();if(ranks<2||!dp())return false;
    std::map<std::string,std::vector<Source>> byKey;
    std::set<std::pair<label,label>> slots;
    for(const auto& s:input)
    {
        if(!work(available)||!validSource(s,ranks)||!slots.insert({s.rank,s.nativeFace}).second)return false;
        byKey[s.key].push_back(s);
    }
    for(auto& entry:byKey)
    {
        if(!work(available)||entry.second.size()!=2)return false;
        auto& a=entry.second[0];auto& b=entry.second[1];
        if(a.rank!=b.peerRank||a.peerRank!=b.rank||a.sense!=-b.sense)return false;
        if(b.rank<a.rank)std::swap(a,b);
        out.emplace(entry.first,std::array<Source,2>{{a,b}});
    }
    return true;
}
inline bool canonical(const Ring& ring,bool reverse,const Available& available,RingBits& out)
{
    out.clear();if(!dp()||ring.size()<3)return false;
    RingBits values;values.reserve(ring.size());std::set<PointBits> unique;
    for(const auto& p:ring)
    {
        if(!work(available))return false;
        PointBits value;
        for(std::size_t k=0;k<3;++k)
        {if(!std::isfinite(p[k]))return false;value[k]=bits(p[k]);}
        if(!unique.insert(value).second)return false;
        values.push_back(value);
    }
    // Minimum bit-coordinate rotation is a matching key only. No native ring,
    // point, face arithmetic or published operand order is modified.
    const std::size_t start=std::min_element(values.begin(),values.end())-values.begin();
    out.reserve(values.size());
    for(std::size_t j=0;j<values.size();++j)
    {
        if(!work(available))return false;
        const std::size_t i=reverse?(start>=j?start-j:values.size()-(j-start))
                                   :(j<values.size()-start?start+j:j-(values.size()-start));
        out.push_back(values[i]);
    }
    return true;
}
inline bool finiteEndpoint(const Subface& f)
{
    const auto& e=f.endpoint;
    bool identity=false;
    if(e.kind==EndpointKind::NativeChild)
        identity=e.nativeChild>=0&&e.sourceParent==-1&&e.plannedChildOrdinal==-1;
    else if(e.kind==EndpointKind::PlannedChild)
        identity=e.nativeChild==-1&&e.sourceParent>=0&&e.plannedChildOrdinal>=0;
    if(f.nativeFace<0||!identity||!std::isfinite(f.volume)||f.volume<=0)return false;
    for(scalar x:f.centre)if(!std::isfinite(x))return false;
    return true;
}
}
// Prepared once for an unchanged uniformly gathered source vector. Only the
// complete original source-pair validation can create a ready catalogue; later
// rounds receive const access to that map and still validate their live inputs.
class Catalogue
{
    label ranks_=0;
    Detail::Sources sources_;
    bool ready_=false;
    friend bool prepareCatalogue(const std::vector<Source>&,label,
        const Available&,Catalogue&,const char**);
public:
    Catalogue()=default;
    bool ready(label ranks) const{return ready_&&ranks_==ranks;}
    const Detail::Sources& sourcePairs() const{return sources_;}
};
inline bool prepareCatalogue(const std::vector<Source>& input,label ranks,
 const Available& available,Catalogue& out,const char** why=nullptr)
{
    out=Catalogue();Catalogue staged;
    if(!Detail::sources(input,ranks,available,staged.sources_))
    {if(why)*why="joint-old-source-pair";return false;}
    staged.ranks_=ranks;staged.ready_=true;out=std::move(staged);return true;
}
namespace Detail
{
// Pure scheduling over one uniformly gathered round. Closure-discovered source
// faces are submitted again in a later uniform round; no collective lives here.
// Other ranks must participate with empty local messages in the caller.
inline bool scheduleValidated(const Sources& sources,
 const std::vector<Request>& requests,const Available& available,
 Schedule& out,const char** why)
{
    out=Schedule();auto no=[&](const char* s){out=Schedule();if(why)*why=s;return false;};
    Schedule staged;
    for(const auto& request:requests)
    {
        if(!Detail::work(available))return no("joint-clock");
        const auto found=sources.find(request.sourceKey);
        const auto kind=requestKind(request);
        if((request.kind!=RequestKind::VolumePlane&&request.kind!=RequestKind::EndpointOnly
                &&request.kind!=RequestKind::SurfaceEdgeBirth)
            ||(request.kind==RequestKind::EndpointOnly&&!request.endpointOnly)
            ||found==sources.end()||(kind!=RequestKind::EndpointOnly&&!Detail::plane(request.plane))
            ||(kind==RequestKind::SurfaceEdgeBirth&&(request.endpointOnly
                ||!Detail::validBirth(request.edgeBirth)
                ||!Detail::samePlane(request.plane,request.edgeBirth.plane))))
            return no("joint-request-source-plane");
        if(request.requester!=found->second[0].rank&&request.requester!=found->second[1].rank)
            return no("joint-request-rank");
        auto& required=staged.required[request.sourceKey];
        if(kind==RequestKind::VolumePlane)required.push_back(request.plane);
        else if(kind==RequestKind::SurfaceEdgeBirth)
            staged.requiredBirths[request.sourceKey].push_back(request.edgeBirth);
    }
    for(auto& entry:staged.required)
    {
        const auto births=staged.requiredBirths.find(entry.first);
        if(births!=staged.requiredBirths.end())
        {
            std::vector<EdgeBirth> normalized;
            if(!Detail::normalizeBirths(births->second,available,normalized))
                return no("joint-request-edge-birth");
            births->second=std::move(normalized);
            for(const auto& source:sources.at(entry.first))for(const auto& birth:births->second)
            {
                if(!Detail::work(available))return no("joint-clock");
                LocalRequest local;local.source=source;local.plane=birth.plane;
                local.kind=RequestKind::SurfaceEdgeBirth;local.edgeBirth=birth;
                staged.local.push_back(std::move(local));
            }
        }
        if(entry.second.empty())
        {
            if(births!=staged.requiredBirths.end())continue;
            for(const auto& source:sources.at(entry.first))
            {
                if(!Detail::work(available))return no("joint-clock");
                staged.local.push_back({source,Plane{},true});
            }
            continue;
        }
        std::vector<Plane> planes;
        if(!Detail::normalizePlanes(entry.second,available,planes))return no("joint-request-source-plane");
        entry.second=std::move(planes);
        for(const auto& source:sources.at(entry.first))for(const auto& p:entry.second)
        {
            if(!Detail::work(available))return no("joint-clock");
            staged.local.push_back({source,p});
        }
    }
    staged.ready=true;out=std::move(staged);return true;
}
// Caller exchanges exact scalar bits. These votes concern offer construction.
// The returned peer CC/V then permits explicit coupled stock/outside preflight.
// Caller finishes that preflight, its real fresh Lineage/source callbacks and
// sparse allocations before its separate final collective publication vote.
// Result ready is pairing readiness, never source authority or permission to
// publish an unchecked shell.
inline bool matchValidated(const Sources& sources,const Schedule& scheduled,
 const std::vector<PreparedOffer>& offers,const std::vector<RankVote>& votes,
 label ranks,const Available& available,MatchResult& out,const char** why)
{
    out=MatchResult();auto no=[&](const char* s){out=MatchResult();if(why)*why=s;return false;};
    if(!scheduled.ready)return no("joint-schedule");
    std::vector<unsigned char> seen(std::size_t(ranks),0);
    for(const auto& vote:votes)
    {
        if(!Detail::work(available))return no("joint-clock");
        if(vote.rank<0||vote.rank>=ranks||seen[vote.rank]++||!vote.ready)return no("joint-rank-not-ready");
    }
    if(votes.size()!=std::size_t(ranks))return no("joint-missing-rank-vote");
    std::map<std::string,std::vector<const PreparedOffer*>> byKey;
    for(const auto& offer:offers)
    {
        if(!Detail::work(available))return no("joint-clock");
        const auto requested=scheduled.required.find(offer.source.key);
        const auto old=sources.find(offer.source.key);
        if(requested==scheduled.required.end()||old==sources.end()||!offer.prepared
          ||offer.faces.empty())return no("joint-offer-unprepared");
        std::vector<Plane> offerPlanes,requiredPlanes;
        if(requested->second.empty())
        {
            if(!offer.planes.empty())return no("joint-offer-planes");
        }
        else if(!Detail::normalizePlanes(offer.planes,available,offerPlanes)
          ||!Detail::normalizePlanes(requested->second,available,requiredPlanes)
          ||!Detail::samePlanes(requiredPlanes,offerPlanes))return no("joint-offer-planes");
        const auto birthRequest=scheduled.requiredBirths.find(offer.source.key);
        if(birthRequest!=scheduled.requiredBirths.end()||!offer.edgeBirths.empty())
        {
        std::vector<EdgeBirth> actualBirths,expectedBirths;
        if(birthRequest!=scheduled.requiredBirths.end())expectedBirths=birthRequest->second;
        if(!Detail::normalizeBirths(offer.edgeBirths,available,actualBirths)
            ||!Detail::sameBirths(expectedBirths,actualBirths))return no("joint-offer-edge-births");
        if(!expectedBirths.empty())
        {
            std::set<PointBits> actualPoints;
            for(const auto& face:offer.faces)for(const auto& point:face.outwardRing)
            {
                if(!Detail::work(available))return no("joint-clock");
                PointBits value;for(label k=0;k<3;++k)value[k]=Detail::bits(point[k]);
                actualPoints.insert(value);
            }
            for(const auto& birth:expectedBirths)
                if(!actualPoints.count(birth.first)||!actualPoints.count(birth.second)
                    ||!actualPoints.count(birth.birth))return no("joint-offer-missing-edge-birth");
        }
        }
        const auto& expected=old->second[offer.source.rank==old->second[0].rank?0:1];
        if(!Detail::sameSource(expected,offer.source))return no("joint-offer-source");
        byKey[offer.source.key].push_back(&offer);
    }
    if(byKey.size()!=scheduled.required.size())return no("joint-missing-source-offer");
    MatchResult staged;
    for(const auto& required:scheduled.required)
    {
        if(!Detail::work(available))return no("joint-clock");
        const auto found=byKey.find(required.first);
        if(found==byKey.end()||found->second.size()!=2)return no("joint-source-offer-count");
        const auto& a=*found->second[0];const auto& b=*found->second[1];
        if(a.source.rank!=b.source.peerRank||a.source.peerRank!=b.source.rank
          ||a.source.sense!=-b.source.sense||a.faces.size()!=b.faces.size())
            return no("joint-source-offer-pair");
        std::map<RingBits,const Subface*> peer;
        for(const auto& face:b.faces)
        {
            RingBits reversed;
            if(!Detail::finiteEndpoint(face)||!Detail::canonical(face.outwardRing,true,available,reversed)
              ||!peer.emplace(std::move(reversed),&face).second)return no("joint-peer-ring");
        }
        for(const auto& face:a.faces)
        {
            RingBits oriented;
            if(!Detail::finiteEndpoint(face)||!Detail::canonical(face.outwardRing,false,available,oriented))
                return no("joint-own-ring");
            const auto twin=peer.find(oriented);
            if(twin==peer.end())return no("joint-subring-mismatch");
            const auto& other=*twin->second;
            RemoteEndpoint forA{required.first,b.source.rank,other.nativeFace,other.endpoint,other.centre,other.volume};
            RemoteEndpoint forB{required.first,a.source.rank,face.nativeFace,face.endpoint,face.centre,face.volume};
            if(!staged.peers.emplace(std::make_pair(a.source.rank,face.nativeFace),std::move(forA)).second
              ||!staged.peers.emplace(std::make_pair(b.source.rank,other.nativeFace),std::move(forB)).second)
                return no("joint-duplicate-native-face");
            peer.erase(twin);
        }
        if(!peer.empty())return no("joint-unmatched-peer-ring");
    }
    if(!Detail::work(available))return no("joint-clock");
    staged.ready=true;out=std::move(staged);return true;
}
}
// Catalogue overloads do not rebuild or revalidate the immutable source map.
// A current clock check remains mandatory, including empty-message rounds.
inline bool schedule(const Catalogue& catalogue,
 const std::vector<Request>& requests,label ranks,const Available& available,
 Schedule& out,const char** why=nullptr)
{
    out=Schedule();auto no=[&](const char* s){if(why)*why=s;return false;};
    if(!catalogue.ready(ranks))return no("joint-old-source-pair");
    if(!Detail::work(available))return no("joint-clock");
    return Detail::scheduleValidated(catalogue.sourcePairs(),requests,available,out,why);
}
inline bool match(const Catalogue& catalogue,const Schedule& scheduled,
 const std::vector<PreparedOffer>& offers,const std::vector<RankVote>& votes,
 label ranks,const Available& available,MatchResult& out,const char** why=nullptr)
{
    out=MatchResult();auto no=[&](const char* s){if(why)*why=s;return false;};
    if(!scheduled.ready)return no("joint-schedule");
    if(!catalogue.ready(ranks))return no("joint-old-source-pair");
    if(!Detail::work(available))return no("joint-clock");
    return Detail::matchValidated(catalogue.sourcePairs(),scheduled,offers,votes,ranks,available,out,why);
}
// Retain the vector entry points with their original validation order and
// clock calls; they share the unchanged live scheduling/matching loops.
inline bool schedule(const std::vector<Source>& oldSources,
 const std::vector<Request>& requests,label ranks,const Available& available,
 Schedule& out,const char** why=nullptr)
{
    out=Schedule();Detail::Sources sources;
    if(!Detail::sources(oldSources,ranks,available,sources))
    {if(why)*why="joint-old-source-pair";return false;}
    return Detail::scheduleValidated(sources,requests,available,out,why);
}
inline bool match(const std::vector<Source>& oldSources,const Schedule& scheduled,
 const std::vector<PreparedOffer>& offers,const std::vector<RankVote>& votes,
 label ranks,const Available& available,MatchResult& out,const char** why=nullptr)
{
    out=MatchResult();
    if(!scheduled.ready){if(why)*why="joint-schedule";return false;}
    Detail::Sources sources;
    if(!Detail::sources(oldSources,ranks,available,sources))
    {if(why)*why="joint-old-source-pair";return false;}
    return Detail::matchValidated(sources,scheduled,offers,votes,ranks,available,out,why);
}
} }
#endif
