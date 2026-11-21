#ifndef dcCartProcessorFullCFEndpointExchange_H
#define dcCartProcessorFullCFEndpointExchange_H
#include "dcCartRawUnionStockMetrics.H"
#include "Pstream.H"
#include "PstreamReduceOps.H"
#include "List.H"
#include "string.H"
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <functional>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace Foam { namespace dcCartProcessorFullCFEndpointExchange {
namespace Q=dcCartRawUnionStockMetrics;
using Ring=std::vector<label>;
using WorldRing=std::vector<point>;
static_assert(sizeof(scalar)==sizeof(std::uint64_t),"processor endpoint wire requires binary64 Foam scalar");
// Value-only transport handles. They do not grant source or mutation authority.

struct PointValue {label nativePoint=-1;point value=point::zero;};
struct FaceValue {Q::Face face;bool coupledByActualType=false;};
struct CellShell {
 label owner=-1;
 std::vector<PointValue> points;
 std::vector<FaceValue> faces; // Complete actual owner CF, original native order.
 // SDK values are measurement-only; never source/publication authority.
 bool installedSDK=false;point sdkCentre=point::zero;scalar sdkVolume=0;
 std::vector<vector> sdkAreas;std::vector<point> sdkCentres;
};
struct Request {
 label senderRank=-1,peerRank=-1,nativeFace=-1,finalNativeFace=-1,sourceHandle=-1;
 label beforeOwner=-1,finalOwner=-1;
 WorldRing originalRing,currentRing,futureRing;
 CellShell before,final;
};
struct Offer {
 label senderRank=-1,actualPeerRank=-1,requesterNativeFace=-1;
 label nativeFace=-1,finalNativeFace=-1,sourceHandle=-1;
 WorldRing originalRing,currentRing,futureRing;
 CellShell before,final;
};
// Resolver must read the held live local phase and fill an actual reciprocal
// processor endpoint; no nested collectives, snapshots, source grants or dummy
// CC/V. The helper independently transports and remeasures its complete shells.
using Resolver=std::function<bool(const Request&,Offer&)>;
using OwnedFaceOF=std::function<void(const std::vector<point>&,const Ring&,vector&,point&)>;
struct EndpointBinding {
 label localFace=-1,peerRank=-1,peerFace=-1,source=-1;CellShell before,final;
};
struct Result {
 private:
 bool realMPIEstablished_=false;std::vector<EndpointBinding> bindings_,metricBindings_;
  std::uint64_t exchangeSerial_=0;label exchangeRank_=-1,exchangeComm_=-1;
 friend bool exchange(const std::vector<Request>&,bool,const Resolver&,const OwnedFaceOF&,const Q::Available&,Result&,int,label);
  friend bool exchangeWithMetricDiscovery(const std::vector<Request>&,bool,const Resolver&,const Resolver&,
   const std::function<bool(const Result&,std::vector<Request>&,std::vector<label>&)>&,
   const OwnedFaceOF&,const Q::Available&,Result&,int,label);
 public:
 bool realMPIEstablished()const noexcept{return realMPIEstablished_;}
 const std::vector<EndpointBinding>& endpointBindings()const noexcept{return bindings_;}
  // Full private second-wave provenance; geometry-only, never primary Source activation.
  const std::vector<EndpointBinding>& metricEndpointBindings()const noexcept{return metricBindings_;}
 bool complete=false;
 const char* reason="processor-endpoint-not-exchanged";
 std::map<label,Q::CoupledEndpoint> before,final;
 std::vector<unsigned char> rankReady;
 std::size_t requestCount=0,responseCount=0;
 bool lookup(bool future,label localNativeFace,Q::CoupledEndpoint& endpoint) const {
  if(!complete)return false;
  const auto& rows=future?final:before;const auto found=rows.find(localNativeFace);
  if(found==rows.end())return false;endpoint=found->second;return true;
 }
};
// Builder labels are LOCAL native processor FACE IDs in the original primary
// affected/full-CF scope. Metric-only responders must not recursively join it.
using MetricRequestBuilder=std::function<bool(const Result&,std::vector<Request>&,std::vector<label>&)>;
namespace detail {
inline std::uint64_t nextExchangeSerial(){static std::uint64_t serial=0;return ++serial;}
constexpr std::size_t maxOffers=128,maxShells=256,maxFacesPerShell=512;
constexpr std::size_t maxPointEntries=65536,maxRingPoints=4096;
constexpr std::size_t maxWireBytes=16*1024*1024;
inline std::uint64_t bits(scalar value) {std::uint64_t v;std::memcpy(&v,&value,8);return v;}
inline scalar scalarBits(std::uint64_t v) {scalar out;std::memcpy(&out,&v,8);return out;}
inline bool samePoint(const point& a,const point& b) {
 return bits(a.x())==bits(b.x())&&bits(a.y())==bits(b.y())&&bits(a.z())==bits(b.z());
}
inline bool ringFinite(const WorldRing& ring) {
 if(ring.size()<3||ring.size()>maxRingPoints)return false;
 for(const point& p:ring)if(!Q::finitePoint(p))return false;return true;
}
// Exactly one reverse cyclic correspondence. Native point IDs differ by rank.
inline bool uniqueReverse(const WorldRing& a,const WorldRing& b) {
 if(!ringFinite(a)||!ringFinite(b)||a.size()!=b.size())return false;
 std::size_t count=0;
 for(std::size_t start=0;start<b.size();++start){
  if(!samePoint(a[0],b[start]))continue;bool equal=true;
  for(std::size_t j=1;j<a.size();++j)
   if(!samePoint(a[j],b[(start+b.size()-j)%b.size()])){equal=false;break;}
  if(equal&&++count>1)return false;
 }
 return count==1;
}
inline void putPoint(std::ostream& out,const point& p,std::size_t& points) {
 if(!Q::finitePoint(p)||++points>maxPointEntries)throw std::runtime_error("point-bound");
 out<<bits(p.x())<<' '<<bits(p.y())<<' '<<bits(p.z())<<' ';
}
inline point getPoint(std::istream& in,std::size_t& points) {
 std::uint64_t x,y,z;if(++points>maxPointEntries||!(in>>x>>y>>z))throw std::runtime_error("point-wire");
 point p(scalarBits(x),scalarBits(y),scalarBits(z));if(!Q::finitePoint(p))throw std::runtime_error("point-finite");return p;
}
inline std::size_t count(std::istream& in,std::size_t maximum) {
 std::uint64_t n;if(!(in>>n)||n>maximum)throw std::runtime_error("count-wire");return std::size_t(n);
}
inline void putRing(std::ostream& out,const WorldRing& ring,std::size_t& points) {
 if(!ringFinite(ring))throw std::runtime_error("ring-input");out<<ring.size()<<' ';for(const point& p:ring)putPoint(out,p,points);
}
inline WorldRing getRing(std::istream& in,std::size_t& points) {
 const auto n=count(in,maxRingPoints);if(n<3)throw std::runtime_error("ring-short");WorldRing ring;ring.reserve(n);
 for(std::size_t i=0;i<n;++i)ring.push_back(getPoint(in,points));return ring;
}
inline void putShell(std::ostream& out,const CellShell& shell,std::size_t& points) {
 if(shell.owner<0||shell.faces.size()<4||shell.faces.size()>maxFacesPerShell||shell.points.empty()
  ||shell.points.size()>maxPointEntries)throw std::runtime_error("shell-input");
 out<<shell.owner<<' '<<shell.points.size()<<' ';
 for(const auto& v:shell.points){if(v.nativePoint<0)throw std::runtime_error("point-id");out<<v.nativePoint<<' ';putPoint(out,v.value,points);}
 out<<shell.faces.size()<<' ';
 for(const auto& v:shell.faces){const auto& f=v.face;
  if(f.id<0||f.owner<0||f.neighbour<-1||f.owner==f.neighbour||f.patch<-1||f.zone<-1
   ||(f.owner!=shell.owner&&f.neighbour!=shell.owner)||f.ring.size()<3||f.ring.size()>maxRingPoints)
   throw std::runtime_error("face-input");
  out<<f.id<<' '<<f.owner<<' '<<f.neighbour<<' '<<f.patch<<' '<<f.zone<<' '
     <<unsigned(v.coupledByActualType)<<' '<<f.ring.size()<<' ';
  for(label p:f.ring){if(p<0)throw std::runtime_error("ring-id");out<<p<<' ';}
 }
 out<<unsigned(shell.installedSDK)<<' ';
 if(shell.installedSDK){
  if(shell.sdkAreas.size()!=shell.faces.size()||shell.sdkCentres.size()!=shell.faces.size()
   ||!Q::finitePoint(shell.sdkCentre)||!std::isfinite(shell.sdkVolume)||shell.sdkVolume<=VSMALL)throw std::runtime_error("SDK-cache-input");
  putPoint(out,shell.sdkCentre,points);out<<bits(shell.sdkVolume)<<' ';
  for(std::size_t j=0;j<shell.faces.size();++j){putPoint(out,shell.sdkAreas[j],points);putPoint(out,shell.sdkCentres[j],points);}
 }
}
inline CellShell getShell(std::istream& in,std::size_t& points) {
 CellShell shell;if(!(in>>shell.owner)||shell.owner<0)throw std::runtime_error("owner-wire");
 const auto np=count(in,maxPointEntries);if(!np)throw std::runtime_error("points-empty");shell.points.reserve(np);
 for(std::size_t i=0;i<np;++i){PointValue v;if(!(in>>v.nativePoint)||v.nativePoint<0)throw std::runtime_error("point-id-wire");v.value=getPoint(in,points);shell.points.push_back(v);}
 const auto nf=count(in,maxFacesPerShell);if(nf<4)throw std::runtime_error("CF-short");shell.faces.reserve(nf);
 for(std::size_t i=0;i<nf;++i){FaceValue v;auto& f=v.face;unsigned coupled;
  if(!(in>>f.id>>f.owner>>f.neighbour>>f.patch>>f.zone>>coupled)||coupled>1)throw std::runtime_error("face-wire");
  v.coupledByActualType=coupled!=0;const auto nv=count(in,maxRingPoints);if(nv<3)throw std::runtime_error("native-ring-short");
  f.ring.reserve(nv);for(std::size_t j=0;j<nv;++j){label p;if(!(in>>p)||p<0)throw std::runtime_error("ring-point-wire");f.ring.push_back(p);}shell.faces.push_back(std::move(v));
 }
 unsigned sdk=0;if(!(in>>sdk)||sdk>1)throw std::runtime_error("SDK-cache-wire");shell.installedSDK=sdk!=0;
 if(shell.installedSDK){shell.sdkCentre=getPoint(in,points);std::uint64_t v;if(!(in>>v))throw std::runtime_error("SDK-volume-wire");shell.sdkVolume=scalarBits(v);
  if(!Q::finitePoint(shell.sdkCentre)||!std::isfinite(shell.sdkVolume)||shell.sdkVolume<=VSMALL)throw std::runtime_error("SDK-cache-numeric");
  for(std::size_t j=0;j<shell.faces.size();++j){shell.sdkAreas.push_back(getPoint(in,points));shell.sdkCentres.push_back(getPoint(in,points));}}
 return shell;
}
inline void end(std::istream& in) {in>>std::ws;if(!in.eof())throw std::runtime_error("wire-tail");}
inline string packRequests(label rank,bool ready,const std::vector<Request>& requests) {
 std::ostringstream out;out<<1<<' '<<rank<<' '<<unsigned(ready)<<' '<<requests.size()<<' ';std::size_t points=0;
 if(requests.size()>maxOffers)throw std::runtime_error("request-bound");
 for(const auto& r:requests){out<<r.senderRank<<' '<<r.peerRank<<' '<<r.nativeFace<<' '<<r.finalNativeFace<<' '<<r.sourceHandle<<' '<<r.beforeOwner<<' '<<r.finalOwner<<' ';
  putRing(out,r.originalRing,points);putRing(out,r.currentRing,points);putRing(out,r.futureRing,points);
  putShell(out,r.before,points);putShell(out,r.final,points);}
 const std::string wire=out.str();if(wire.size()>maxWireBytes)throw std::runtime_error("wire-bound");return string(wire);
}
inline std::vector<Request> unpackRequests(const string& wire,label expected,bool& ready,label ranks) {
 if(wire.size()>maxWireBytes)throw std::runtime_error("wire-bound");std::istringstream in(wire);unsigned version,status;label rank;
 if(!(in>>version>>rank>>status)||version!=1||rank!=expected||status>1)throw std::runtime_error("request-header");ready=status!=0;
 const auto n=count(in,maxOffers);std::vector<Request> out;out.reserve(n);std::size_t points=0;std::set<label> ids;
 for(std::size_t i=0;i<n;++i){Request r;if(!(in>>r.senderRank>>r.peerRank>>r.nativeFace>>r.finalNativeFace>>r.sourceHandle>>r.beforeOwner>>r.finalOwner)
   ||r.senderRank!=expected||r.peerRank<0||r.peerRank>=ranks||r.peerRank==expected||r.nativeFace<0||r.finalNativeFace<0||r.sourceHandle<0
   ||r.beforeOwner<0||r.finalOwner<0||!ids.insert(r.nativeFace).second)throw std::runtime_error("request-identity");
  r.originalRing=getRing(in,points);r.currentRing=getRing(in,points);r.futureRing=getRing(in,points);
  r.before=getShell(in,points);r.final=getShell(in,points);
  if(r.before.owner!=r.beforeOwner||r.final.owner!=r.finalOwner)throw std::runtime_error("request-shell-owner");
  out.push_back(std::move(r));}
 end(in);return out;
}
inline string packOffers(label rank,bool ready,const std::vector<Offer>& offers) {
 if(offers.size()>maxOffers||2*offers.size()>maxShells)throw std::runtime_error("offer-bound");
 std::ostringstream out;out<<1<<' '<<rank<<' '<<unsigned(ready)<<' '<<offers.size()<<' ';std::size_t points=0;
 for(const auto& r:offers){out<<r.senderRank<<' '<<r.actualPeerRank<<' '<<r.requesterNativeFace<<' '<<r.nativeFace<<' '<<r.finalNativeFace<<' '<<r.sourceHandle<<' ';
  putRing(out,r.originalRing,points);putRing(out,r.currentRing,points);putRing(out,r.futureRing,points);
  putShell(out,r.before,points);putShell(out,r.final,points);}
 const std::string wire=out.str();if(wire.size()>maxWireBytes)throw std::runtime_error("wire-bound");return string(wire);
}
inline std::vector<Offer> unpackOffers(const string& wire,label expected,bool& ready,label ranks) {
 if(wire.size()>maxWireBytes)throw std::runtime_error("wire-bound");std::istringstream in(wire);unsigned version,status;label rank;
 if(!(in>>version>>rank>>status)||version!=1||rank!=expected||status>1)throw std::runtime_error("offer-header");ready=status!=0;
 const auto n=count(in,maxOffers);if(2*n>maxShells)throw std::runtime_error("shell-bound");std::vector<Offer> out;out.reserve(n);std::size_t points=0;
 for(std::size_t i=0;i<n;++i){Offer r;if(!(in>>r.senderRank>>r.actualPeerRank>>r.requesterNativeFace>>r.nativeFace>>r.finalNativeFace>>r.sourceHandle)
   ||r.senderRank!=expected||r.actualPeerRank<0||r.actualPeerRank>=ranks||r.actualPeerRank==expected||r.requesterNativeFace<0||r.nativeFace<0||r.finalNativeFace<0||r.sourceHandle<0)throw std::runtime_error("offer-identity");
  r.originalRing=getRing(in,points);r.currentRing=getRing(in,points);r.futureRing=getRing(in,points);
  r.before=getShell(in,points);r.final=getShell(in,points);out.push_back(std::move(r));}
 end(in);return out;
}
// A complete owner shell is independently closed and remeasured in its actual
// CF order. Remap only rank-local point labels; ring coordinates and operands
// remain exactly the transported bits. No old snapshot centres/volumes used.
inline bool endpoint(const CellShell& shell,label actualNativeProcessorFace,const WorldRing& actualProcessorRing,
 const OwnedFaceOF& faceOF,const Q::Available& work,Q::CoupledEndpoint& out) {
 if(!faceOF||!Q::workAvailable(work)||shell.owner<0||shell.faces.size()<4||shell.faces.size()>maxFacesPerShell)return false;
 std::map<label,label> remap;std::vector<point> points;points.reserve(shell.points.size());
 for(const auto& p:shell.points){if(!Q::workAvailable(work)||p.nativePoint<0||!Q::finitePoint(p.value)||!remap.emplace(p.nativePoint,label(points.size())).second)return false;points.push_back(p.value);}
 std::set<label> faces;std::map<label,bool> patchCoupled;std::map<std::pair<label,label>,std::array<unsigned,2>> edges;
 std::vector<Q::Face> rows;rows.reserve(shell.faces.size());std::size_t ringMatches=0;
 for(const auto& v:shell.faces){if(!Q::workAvailable(work))return false;Q::Face f=v.face;
  if(!faces.insert(f.id).second||f.id<0||f.owner<0||f.neighbour<-1||f.owner==f.neighbour
   ||(f.owner!=shell.owner&&f.neighbour!=shell.owner)||f.patch<-1||f.zone<-1||f.ring.size()<3||f.ring.size()>maxRingPoints)return false;
  if(f.patch>=0){auto inserted=patchCoupled.emplace(f.patch,v.coupledByActualType);if(!inserted.second&&inserted.first->second!=v.coupledByActualType)return false;}
  else if(v.coupledByActualType)return false;
  WorldRing world;world.reserve(f.ring.size());std::set<label> vertices;
  for(label& p:f.ring){auto id=remap.find(p);if(id==remap.end()||!vertices.insert(p).second)return false;world.push_back(points[id->second]);p=id->second;}
  if(f.id==actualNativeProcessorFace){
   if(!v.coupledByActualType||f.patch<0||f.neighbour!=-1||f.owner!=shell.owner)return false;
   // Same direction as the peer native face, allowing its current ring origin.
   WorldRing reverse=actualProcessorRing;std::reverse(reverse.begin(),reverse.end());if(uniqueReverse(world,reverse))++ringMatches;}
  Ring directed=f.ring;if(f.owner!=shell.owner)std::reverse(directed.begin()+1,directed.end());
  for(std::size_t j=0;j<directed.size();++j){const label a=directed[j],b=directed[(j+1)%directed.size()];if(a==b)return false;
   auto& n=edges[{min(a,b),max(a,b)}];++n[a<b?0:1];}
  rows.push_back(std::move(f));
 }
 if(ringMatches!=1)return false;for(const auto& e:edges)if(e.second[0]!=1||e.second[1]!=1)return false;
 if(shell.installedSDK){
  if(shell.sdkAreas.size()!=rows.size()||shell.sdkCentres.size()!=rows.size()
   ||!Q::finitePoint(shell.sdkCentre)||!std::isfinite(shell.sdkVolume)||shell.sdkVolume<=VSMALL)return false;
  vector closure(vector::zero);scalar total=0;
  for(std::size_t j=0;j<rows.size();++j){if(!Q::workAvailable(work)||!Q::finitePoint(shell.sdkAreas[j])||!Q::finitePoint(shell.sdkCentres[j])||mag(shell.sdkAreas[j])<=1e-12)return false;
   const vector a=(rows[j].owner==shell.owner?1:-1)*shell.sdkAreas[j];closure+=a;total+=mag(a);}
  if(mag(closure)/max(total,VSMALL)>1e-12)return false;
  out.centre=shell.sdkCentre;out.volume=shell.sdkVolume;return Q::workAvailable(work);
 }
 const Q::FaceOF actualMetric=[&](const Ring& ring,vector& sf,point& fc){faceOF(points,ring,sf,fc);};
 const Q::CoupledTypePatch actualCoupled=[&](label patch){auto found=patchCoupled.find(patch);return patch>=0&&(found==patchCoupled.end()||found->second);};
 const auto cell=Q::measure(shell.owner,rows,points,actualMetric,actualCoupled,work,false,false);
 if(!Q::workAvailable(work)||!cell.finite||!Q::finitePoint(cell.centre)||!std::isfinite(cell.volume)||cell.volume<=VSMALL)return false;
 out.centre=cell.centre;out.volume=cell.volume;return true;
}
inline bool install(std::map<label,Q::CoupledEndpoint>& values,label nativeFace,const Q::CoupledEndpoint& endpoint) {
 if(nativeFace<0)return false;auto existing=values.find(nativeFace);
 if(existing!=values.end())return samePoint(existing->second.centre,endpoint.centre)&&bits(existing->second.volume)==bits(endpoint.volume);
 values.emplace(nativeFace,endpoint);return true;
}
} // detail
// All ranks call at the same fixed phase, with localReady=false on local source,
// clock or preparation refusal. Never branch around the two actual collective
// waves. Readonly ranks resolve literal live before==final endpoints. This
// provides measurements only; Phase retains all source/peer/stale/quality/VM
// permissions and its final all-rank vote/atomic no-callback publication.
inline bool exchange(const std::vector<Request>& localRequests,bool localReady,
 const Resolver& resolver,const OwnedFaceOF& faceOF,const Q::Available& work,
 Result& out,int tag=UPstream::msgType(),label comm=UPstream::worldComm) {
 out=Result();const label rank=UPstream::myProcNo(comm),ranks=UPstream::nProcs(comm);
  const std::uint64_t serial=detail::nextExchangeSerial();
 if(!UPstream::is_parallel(comm)||ranks<2){out.reason="processor-endpoint-requires-real-MPI";return false;}
 const auto fail=[&](const char* reason){localReady=false;if(!std::strcmp(out.reason,"processor-endpoint-not-exchanged"))out.reason=reason;};
 List<string> requests(ranks);std::vector<Request> own;
 try {
  if(!localReady||!resolver||!faceOF||!work||!Q::workAvailable(work))fail("processor-endpoint-local-not-ready");
  if(localReady){own=localRequests;
   for(const auto& r:own){Q::CoupledEndpoint oldEndpoint,newEndpoint;
    if(r.before.owner!=r.beforeOwner||r.final.owner!=r.finalOwner
     ||!detail::endpoint(r.before,r.nativeFace,r.currentRing,faceOF,work,oldEndpoint)
     ||!detail::endpoint(r.final,r.finalNativeFace,r.futureRing,faceOF,work,newEndpoint))
     throw std::runtime_error("requester-full-CF-measure-or-clock");}
   requests[rank]=detail::packRequests(rank,true,own);
   bool ready=false;auto checked=detail::unpackRequests(requests[rank],rank,ready,ranks);if(!ready||checked.size()!=own.size())fail("processor-endpoint-local-request");}
 }catch(...){fail("processor-endpoint-local-request-bound-or-input");}
 if(!localReady){own.clear();requests[rank]=detail::packRequests(rank,false,own);}
 // Required communications are deliberately outside local measurement catches.
 Pstream::gatherList(requests,tag,comm);Pstream::scatterList(requests,tag,comm);
 std::vector<std::vector<Request>> allRequests(ranks);std::vector<unsigned char> requestReady(ranks,0);
 bool allReady=true;
 for(label sender=0;sender<ranks;++sender){try {bool ready=false;allRequests[sender]=detail::unpackRequests(requests[sender],sender,ready,ranks);requestReady[sender]=ready;allReady=allReady&&ready;}
  catch(...){allReady=false;fail("processor-endpoint-request-wire");}}
 std::vector<Offer> responses;
 if(!allReady)fail("processor-endpoint-request-rank-not-ready");
 if(localReady)try {
  for(label sender=0;sender<ranks;++sender)for(const auto& r:allRequests[sender])if(r.peerRank==rank){
   if(!Q::workAvailable(work))throw std::runtime_error("clock");Offer offer;
   Q::CoupledEndpoint requesterBefore,requesterFinal;
   if(!detail::endpoint(r.before,r.nativeFace,r.currentRing,faceOF,work,requesterBefore)
    ||!detail::endpoint(r.final,r.finalNativeFace,r.futureRing,faceOF,work,requesterFinal))
    throw std::runtime_error("incoming-request-full-CF-measure-or-clock");
   if(!resolver(r,offer)||offer.senderRank!=rank||offer.actualPeerRank!=sender||offer.requesterNativeFace!=r.nativeFace
    ||!detail::uniqueReverse(r.originalRing,offer.originalRing)||!detail::uniqueReverse(r.currentRing,offer.currentRing)
    ||!detail::uniqueReverse(r.futureRing,offer.futureRing))throw std::runtime_error("peer-resolver-or-three-rings");
   Q::CoupledEndpoint oldEndpoint,newEndpoint;
   if(!detail::endpoint(offer.before,offer.nativeFace,offer.currentRing,faceOF,work,oldEndpoint)
    ||!detail::endpoint(offer.final,offer.finalNativeFace,offer.futureRing,faceOF,work,newEndpoint))
    throw std::runtime_error("producer-full-CF-measure-or-clock");
   responses.push_back(std::move(offer));if(responses.size()>detail::maxOffers)throw std::runtime_error("response-bound");
  }
  if(!Q::workAvailable(work))throw std::runtime_error("clock");
 }catch(...){fail("processor-endpoint-peer-resolver-or-clock");}
 List<string> offers(ranks);
 try {offers[rank]=detail::packOffers(rank,localReady,responses);}catch(...){fail("processor-endpoint-response-bound-or-input");}
 if(!localReady){responses.clear();offers[rank]=detail::packOffers(rank,false,responses);}
 Pstream::gatherList(offers,tag,comm);Pstream::scatterList(offers,tag,comm);
 std::vector<std::vector<Offer>> allOffers(ranks);out.rankReady.assign(ranks,0);allReady=true;
 for(label sender=0;sender<ranks;++sender){try {bool ready=false;allOffers[sender]=detail::unpackOffers(offers[sender],sender,ready,ranks);out.rankReady[sender]=ready;allReady=allReady&&ready;}
  catch(...){allReady=false;fail("processor-endpoint-response-wire");}}
 if(!allReady){out.reason=localReady?"processor-endpoint-response-rank-not-ready":out.reason;return false;}
 try {
  for(const auto& r:own){const Offer* matched=nullptr;std::size_t count=0;
   for(const auto& v:allOffers[r.peerRank])if(v.actualPeerRank==rank&&v.requesterNativeFace==r.nativeFace){
    matched=&v;++count;}
   if(count!=1||!matched||!detail::uniqueReverse(r.originalRing,matched->originalRing)
    ||!detail::uniqueReverse(r.currentRing,matched->currentRing)||!detail::uniqueReverse(r.futureRing,matched->futureRing))
    {out.reason="processor-endpoint-not-unique-reciprocal-offer";return false;}
   Q::CoupledEndpoint oldEndpoint,newEndpoint;
   if(!detail::endpoint(matched->before,matched->nativeFace,matched->currentRing,faceOF,work,oldEndpoint)
    ||!detail::endpoint(matched->final,matched->finalNativeFace,matched->futureRing,faceOF,work,newEndpoint)){out.reason="processor-endpoint-full-CF-measure-or-clock";return false;}
   if(!detail::install(out.before,r.nativeFace,oldEndpoint)||!detail::install(out.final,r.finalNativeFace,newEndpoint)){out.reason="processor-endpoint-conflicting-local-face";return false;}
  out.bindings_.push_back({r.nativeFace,r.peerRank,matched->nativeFace,r.sourceHandle,r.before,r.final});
  }
  // A readonly responding rank also requires the requester actual changed
  // endpoint values for its own original->final coupled-face Stock checks.
  for(label sender=0;sender<ranks;++sender)for(const auto& r:allRequests[sender])if(r.peerRank==rank){
   const Offer* reciprocal=nullptr;std::size_t count=0;
   for(const auto& v:responses)if(v.actualPeerRank==sender&&v.requesterNativeFace==r.nativeFace){reciprocal=&v;++count;}
   if(count!=1||!reciprocal||!detail::uniqueReverse(r.originalRing,reciprocal->originalRing)
    ||!detail::uniqueReverse(r.currentRing,reciprocal->currentRing)||!detail::uniqueReverse(r.futureRing,reciprocal->futureRing))
    {out.reason="processor-endpoint-not-unique-local-response";return false;}
   Q::CoupledEndpoint requesterBefore,requesterFinal;
   if(!detail::endpoint(r.before,r.nativeFace,r.currentRing,faceOF,work,requesterBefore)
    ||!detail::endpoint(r.final,r.finalNativeFace,r.futureRing,faceOF,work,requesterFinal)
    ||!detail::install(out.before,reciprocal->nativeFace,requesterBefore)
    ||!detail::install(out.final,reciprocal->finalNativeFace,requesterFinal))
    {out.reason="processor-endpoint-reciprocal-measure-or-conflict";return false;}
  out.bindings_.push_back({reciprocal->nativeFace,sender,r.nativeFace,reciprocal->sourceHandle,reciprocal->before,reciprocal->final});
  }
  if(!Q::workAvailable(work)){out.reason="processor-endpoint-final-clock";return false;}
 }catch(...){out.reason="processor-endpoint-unknown-or-measurement";return false;}
 out.requestCount=own.size();out.responseCount=responses.size();out.reason="none";out.complete=true;out.realMPIEstablished_=true;
  out.exchangeSerial_=serial;out.exchangeRank_=rank;out.exchangeComm_=comm;return true;
}

// Fixed FOUR waves: old actual primary pair, then explicit bounded metric pair.
// Fallible geometry preparation precedes both fixed metric waves. No Source,
// Point or publication permission is conveyed by this transport result.
inline bool exchangeWithMetricDiscovery(const std::vector<Request>& primaryRequests,bool localReady,
 const Resolver& primaryResolver,const Resolver& metricResolver,const MetricRequestBuilder& buildMissing,
 const OwnedFaceOF& faceOF,const Q::Available& work,Result& out,
 int tag=UPstream::msgType(),label comm=UPstream::worldComm) {
 Result primary,metric;out=Result();
 const bool primaryOK=exchange(primaryRequests,localReady,primaryResolver,faceOF,work,primary,tag,comm);
 std::vector<Request> missing;std::vector<label> required;bool metricReady=primaryOK;
 const char* preparation="processor-metric-primary-not-ready";
 try {
  if(metricReady&&(!buildMissing||!metricResolver||!buildMissing(primary,missing,required)))
   {metricReady=false;preparation="processor-metric-discovery-not-ready";}
  if(metricReady){
   if(required.size()>detail::maxOffers||missing.size()>detail::maxOffers)throw std::runtime_error("metric-required-bound");
   std::set<label> needed,already,requested;
   for(label f:required)if(f<0||!needed.insert(f).second)throw std::runtime_error("metric-required-identity");
   for(const auto& b:primary.bindings_)if(b.localFace<0||!already.insert(b.localFace).second)
    throw std::runtime_error("primary-duplicate-binding");
   for(const auto& r:missing)if(!needed.count(r.nativeFace)||already.count(r.nativeFace)
     ||!requested.insert(r.nativeFace).second)throw std::runtime_error("metric-request-identity-or-old-frame");
  }
 }catch(...){metricReady=false;preparation="processor-metric-discovery-bound-or-input";}
 if(!metricReady)missing.clear();
 // NEVER branch around the second actual pair, including empty/failed ranks.
 const bool metricOK=exchange(missing,metricReady,metricResolver,faceOF,work,metric,tag,comm);
 bool geometryReady=primaryOK&&metricOK&&metricReady;
 if(!geometryReady)out.reason=!primaryOK?primary.reason:(!metricReady?preparation:metric.reason);
 try {if(geometryReady) {
  const label rank=UPstream::myProcNo(comm);
  if(!primary.realMPIEstablished_||!metric.realMPIEstablished_||primary.exchangeSerial_==0
    ||metric.exchangeSerial_!=primary.exchangeSerial_+1||primary.exchangeComm_!=comm||metric.exchangeComm_!=comm
    ||primary.exchangeRank_!=rank||metric.exchangeRank_!=rank||!Q::workAvailable(work))
   throw std::runtime_error("metric-real-MPI-frame");
  std::set<label> requiredSet(required.begin(),required.end()),bound;
  Result joined;joined.before=primary.before;joined.final=primary.final;joined.metricBindings_=metric.bindings_;
  for(const auto& b:primary.bindings_){if(!bound.insert(b.localFace).second)throw std::runtime_error("primary-duplicate-binding");joined.bindings_.push_back(b);}
  for(const auto& v:metric.before)if(!detail::install(joined.before,v.first,v.second))throw std::runtime_error("metric-before-conflict");
  for(const auto& v:metric.final)if(!detail::install(joined.final,v.first,v.second))throw std::runtime_error("metric-final-conflict");
  std::set<label> metricSeen;
  for(const auto& b:metric.bindings_){
   if(b.localFace<0||!metricSeen.insert(b.localFace).second)throw std::runtime_error("metric-duplicate-binding");
   // Endpoint-only responders provide numbers but acquire no primary Source
   // receipt role and do NOT create a recursive full-boundary requirement.
   if(!requiredSet.count(b.localFace))continue;
   if(!bound.insert(b.localFace).second)throw std::runtime_error("metric-overlapping-binding");
   joined.bindings_.push_back(b);
  }
  if(joined.bindings_.size()>detail::maxOffers)throw std::runtime_error("metric-merged-binding-bound");
  for(label f:required)if(!bound.count(f)||!joined.before.count(f)||!joined.final.count(f))
   throw std::runtime_error("metric-primary-full-CF-still-missing");
  if(!Q::workAvailable(work))throw std::runtime_error("metric-final-clock");
  joined.rankReady=metric.rankReady;joined.requestCount=primary.requestCount+metric.requestCount;
  joined.responseCount=primary.responseCount+metric.responseCount;
  joined.exchangeSerial_=metric.exchangeSerial_;joined.exchangeRank_=rank;joined.exchangeComm_=comm;
  joined.reason="none";joined.complete=true;joined.realMPIEstablished_=true;out=std::move(joined);
 }}catch(...){geometryReady=false;out.reason="processor-metric-frame-identity-or-conflict";}
 // Explicit common GEOMETRY readiness after all FOUR payload waves. Old local
 // endpoint/merge errors must not be described as a global completed result.
 // This scalar synchronization grants no Source/Point/publication permission.
 const label commonGeometryReady=returnReduce(label(geometryReady),minOp<label>(),tag,comm);
 if(!commonGeometryReady){out.complete=false;out.realMPIEstablished_=false;
  if(geometryReady)out.reason="processor-metric-other-rank-geometry-not-ready";return false;}
 return true;
}
} }
#endif
