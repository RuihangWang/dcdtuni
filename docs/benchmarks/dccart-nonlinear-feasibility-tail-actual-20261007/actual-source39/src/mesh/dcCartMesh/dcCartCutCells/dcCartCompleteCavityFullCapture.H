#ifndef dcCartCompleteCavityFullCapture_H
#define dcCartCompleteCavityFullCapture_H
#include <chrono>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include <type_traits>
#include <string>
#include <limits>
#include <algorithm>
#include <unistd.h>
#include <sys/stat.h>

// Default-OFF diagnostic only. Exact bits/data are not source/quality permission.
namespace dcCartFullCaptureIO {
using Clock=std::chrono::steady_clock;
inline bool enabled() noexcept {
 const char* v=std::getenv("DCCART_POINT_STAR_FULL_CAPTURE");
 return v&&v[0]=='1'&&!v[1];
}
struct Limits {
 static constexpr std::uint64_t bytes=std::uint64_t(1024)*1024*1024;
 static constexpr std::uint64_t copyBytes=std::uint64_t(512)*1024*1024;
 static constexpr std::uint64_t elements=16u*1024u*1024u;
 static constexpr double seconds=10;
 static constexpr unsigned ranks=10;
};
struct Stream {
 FILE* file=nullptr;int owned=-1;struct stat inode{};
 std::string path;std::uint64_t bytes=0,cap;Clock::time_point start=Clock::now();
 double limit;bool complete=false,ownKnown=false;
 explicit Stream(const char* name,std::uint64_t c=Limits::bytes,double s=Limits::seconds):path(name?name:""),cap(c),limit(s) {
  if(path.empty()||!(limit>0))throw "capture-path-or-clock";
  file=std::fopen(path.c_str(),"wx");if(!file)throw "capture-exclusive-open";
  ownKnown=::fstat(::fileno(file),&inode)==0;owned=::dup(::fileno(file));
  if(!ownKnown||owned<0){abort();throw "capture-owned-inode";}
 }
 Stream(const Stream&)=delete;Stream& operator=(const Stream&)=delete;
 double elapsed()const{return std::chrono::duration<double>(Clock::now()-start).count();}
 void awake()const{if(!(limit>0)||elapsed()>=limit)throw "capture-clock";}
 void write(const void* data,std::uint64_t n) {
  awake();if(n>cap||bytes>cap-n)throw "capture-byte-cap";
  const auto* p=static_cast<const unsigned char*>(data);
  for(std::uint64_t at=0;at<n;) {
   awake();const auto part=std::size_t(std::min(std::uint64_t(8192),n-at));
   if(std::fwrite(p+at,1,part,file)!=part)throw "capture-write";
   at+=part;bytes+=part;
  }
 }
 template<class T>void pod(T x){static_assert(std::is_trivially_copyable<T>::value,"capture POD");write(&x,sizeof(x));}
 void count(std::uint64_t n){if(n>Limits::elements)throw "capture-element-cap";pod(n);}
 void text(const std::string& s){if(s.size()>64u*1024u*1024u)throw "capture-text-cap";pod<std::uint64_t>(s.size());write(s.data(),s.size());}
 void abort() noexcept {
  const bool cleanupOwned=ownKnown;ownKnown=false; // spend the capability exactly once
  if(cleanupOwned){struct stat current{};if(::lstat(path.c_str(),&current)==0&&S_ISREG(current.st_mode)
   &&current.st_dev==inode.st_dev&&current.st_ino==inode.st_ino)::unlink(path.c_str());}
  if(file){std::fclose(file);file=nullptr;}
  if(owned>=0){::close(owned);owned=-1;}
 }
 void finish() {
  awake();pod(std::uint64_t(0x434F4D504C455445ull));awake();
  const bool flush=std::fflush(file)==0;const bool close=std::fclose(file)==0;file=nullptr;
  if(!flush||!close){abort();throw "capture-flush-close";}
  awake();complete=true; // keep the created inode lease until final total-time admission
 }
 ~Stream(){if(!complete)abort();else if(owned>=0)::close(owned);}
};
}
#ifndef DCCART_FULL_CAPTURE_IO_ONLY
#include "dcCartCompleteCavityReplayObservation.H"
namespace Foam { namespace dcCartCompleteCavityFullCapture {
namespace IO=::dcCartFullCaptureIO;
using Cut=dcCartCutCells::cutMesh;using Bindings=dcCartCutCells::writerBindings;
namespace L=dcCartCompleteCavityLocalOracle;
namespace R=dcCartCompleteCavityReplayObservation;
namespace W=dcCartWeightUnionPreview;
struct Pending {
 std::vector<std::vector<label>> currentCF;label nativeCells=-1;
 double seconds=0;std::uint64_t bytes=0;bool present=false;const char* refusal="capture-current-CF-absent";
};
extern thread_local Pending pending;
inline void handoffCF(bool on,std::vector<std::vector<label>>&& cf,const Cut& raw) noexcept {
 if(!on)return;pending=Pending();if(Pstream::nProcs()>IO::Limits::ranks){pending.refusal="capture-rank-cap";return;}const auto start=IO::Clock::now();
 try {
  if(cf.size()!=std::size_t(raw.nCells)||cf.size()>IO::Limits::elements)throw "capture-current-CF-cardinality";
  std::uint64_t bytes=cf.size()*sizeof(std::vector<label>);const auto cap=std::uint64_t(64)*1024*1024;
  for(std::size_t i=0;i<cf.size();++i){
   if(!(i&1023)&&std::chrono::duration<double>(IO::Clock::now()-start).count()>=IO::Limits::seconds)throw "capture-current-CF-clock";
   if(cf[i].size()>IO::Limits::elements||bytes>cap||cf[i].size()>(cap-bytes)/sizeof(label))throw "capture-current-CF-cap";
   bytes+=cf[i].size()*sizeof(label);
  }
  pending.currentCF=std::move(cf);pending.nativeCells=raw.nCells;pending.bytes=bytes;pending.present=true;pending.refusal="none";
 }catch(const char* reason){pending.refusal=reason;}catch(...){pending.refusal="capture-current-CF-exception";}
 pending.seconds=std::chrono::duration<double>(IO::Clock::now()-start).count();
}
struct Lease {std::unique_ptr<Cut> raw;double copySeconds=0;std::size_t copyBytes=0;const char* refusal="disabled";};
inline void awake(IO::Clock::time_point start) {
 if(std::chrono::duration<double>(IO::Clock::now()-start).count()>=IO::Limits::seconds)throw "capture-copy-clock";
}
inline std::size_t copiedBytes(const Cut& m,IO::Clock::time_point start) {
 std::uint64_t b=sizeof(Cut);
 auto add=[&](std::uint64_t n,std::uint64_t width){
  if(n>IO::Limits::elements||width>IO::Limits::copyBytes||n>(IO::Limits::copyBytes-b)/width)throw "capture-copy-cap";
  b+=n*width;
 };
 add(m.points.size(),sizeof(point));add(m.faces.size(),sizeof(std::vector<label>));
 std::size_t i=0;for(const auto& f:m.faces){if(!(i++&1023))awake(start);add(f.size(),sizeof(label));}
 for(const auto* xs:{&m.owner,&m.neighbour,&m.patch,&m.zone,&m.cellLevel,&m.cellRank,&m.coneEvidenceParent,&m.coneEvidenceBase})add(xs->size(),sizeof(label));
 add(m.cellRefVol.size(),sizeof(scalar));add(m.cellRefCentre.size(),sizeof(point));
 add(m.cellLeaf.size()+m.cellPiece.size(),sizeof(std::uint64_t));
 add(m.faceAlive.size()+m.cellAlive.size()+m.cellOrigAlive.size(),1); // conservative for vector<bool>
 add(m.completeCavityLineage.size(),sizeof(label));
 add(m.completeCavityLineage.compositeCount(),sizeof(std::array<label,8>)+8);
 return std::size_t(b);
}
inline Lease retain(bool on,const Cut& m) noexcept {
 Lease x;if(!on)return x;if(Pstream::nProcs()>IO::Limits::ranks){x.refusal="capture-rank-cap";return x;}const auto start=IO::Clock::now();
 try {
  x.copyBytes=copiedBytes(m,start);awake(start);
  x.raw.reset(new Cut(m));awake(start);x.refusal="none";
 }catch(const char* reason){x.raw.reset();x.refusal=reason;}catch(...){x.raw.reset();x.refusal="capture-copy-exception";}
 x.copySeconds=std::chrono::duration<double>(IO::Clock::now()-start).count();return x;
}
inline void value(IO::Stream& s,label x){s.pod<std::int32_t>(x);}
inline void value(IO::Stream& s,scalar x){s.pod<double>(x);}
inline void value(IO::Stream& s,std::uint64_t x){s.pod(x);}
inline void value(IO::Stream& s,const point& x){value(s,x.x());value(s,x.y());value(s,x.z());}
inline void value(IO::Stream& s,const std::string& x){s.text(x);}
inline void bit(IO::Stream& s,bool x){s.pod<std::uint8_t>(x?1:0);}
template<class Seq>inline void labels(IO::Stream& s,const Seq& xs) {
 using E=typename std::remove_cv<typename std::remove_reference<decltype(xs[0])>::type>::type;
 static_assert(sizeof(E)==4,"actual labels must be contiguous int32");
 s.count(xs.size());if(!xs.empty())s.write(&xs[0],std::uint64_t(xs.size())*4);
}
template<class Seq>inline void points(IO::Stream& s,const Seq& xs) {
 static_assert(sizeof(point)==3*sizeof(scalar),"actual point contiguous binary64 triple");
 s.count(xs.size());if(!xs.empty())s.write(&xs[0],std::uint64_t(xs.size())*sizeof(point));
}
template<class Seq>inline void bits(IO::Stream& s,const Seq& xs) {
 s.count(xs.size());for(auto x:xs)bit(s,bool(x));
}
inline void origin(IO::Stream& s,const L::Origin& x) {
 value(s,x.nativeParent);value(s,x.rank);value(s,x.level);value(s,x.leaf);value(s,x.piece);value(s,x.region);
 s.count(x.sourceFacets.size());for(const auto& f:x.sourceFacets)for(label i:f)value(s,i);
}
inline void origins(IO::Stream& s,const std::vector<L::Origin>& xs){s.count(xs.size());for(const auto& x:xs)origin(s,x);}
template<class Seq>inline void scalars(IO::Stream& s,const Seq& xs) {
 s.count(xs.size());if(!xs.empty())s.write(&xs[0],std::uint64_t(xs.size())*8);
}
inline void raw(IO::Stream& s,const Cut& m) {
 s.text("raw-native-before-writer-lowering");value(s,m.nCells);value(s,m.nGridPoints);
 points(s,m.points);s.count(m.faces.size());for(const auto& f:m.faces)labels(s,f);
 for(const auto* xs:{&m.owner,&m.neighbour,&m.patch,&m.zone})labels(s,*xs);
 bits(s,m.faceAlive);bits(s,m.cellAlive);bits(s,m.cellOrigAlive);
 scalars(s,m.cellRefVol);points(s,m.cellRefCentre);
 labels(s,m.cellLevel);labels(s,m.cellRank);
 scalars(s,m.cellLeaf);scalars(s,m.cellPiece);
 labels(s,m.coneEvidenceParent);labels(s,m.coneEvidenceBase);
 const auto& l=m.completeCavityLineage;
 bit(s,l.started());bit(s,l.active());value(s,l.revision());labels(s,l.observationKeys());
 s.count(l.observationComposites().size());for(const auto& c:l.observationComposites()){
  s.pod<std::uint8_t>(c.count);for(label p:c.parents)value(s,p);
 }
}
inline void binding(IO::Stream& s,const Bindings& b){labels(s,b.cell);labels(s,b.face);labels(s,b.point);labels(s,b.alias);}
inline void source(IO::Stream& s,const dcCartFluidOriginSnapshotCore::Snapshot& q) {
 s.text("fresh-normal-post-carve-source");bit(s,q.completedFluidOnlyPath);bit(s,q.complete);
 for(const auto* x:{&q.rawSha256,&q.rolesDigest,&q.materialKey,&q.materialDigest,&q.producerDigest,&q.boundaryMembershipDigest})value(s,*x);
 static_assert(sizeof(dcCartFluidOriginSnapshotCore::Coord)==24,"actual source coordinates");
 s.count(q.points.size());if(!q.points.empty())s.write(q.points.data(),q.points.size()*24);
 s.count(q.faces.size());for(const auto& f:q.faces){
  value(s,label(f.handle));value(s,label(f.owner));value(s,label(f.neighbour));value(s,label(f.patch));value(s,label(f.zone));labels(s,f.ring);
 }
 s.count(q.parents.size());for(const auto& p:q.parents){
  value(s,label(std::get<0>(p.identity)));value(s,label(std::get<1>(p.identity)));value(s,label(std::get<2>(p.identity)));
  value(s,std::get<3>(p.identity));value(s,std::get<4>(p.identity));labels(s,p.facets);
 }bits(s,q.valid);
}
inline void packet(IO::Stream& s,const W::Packet& p) {
 value(s,label(p.seedKind==W::SeedKind::D));for(label x:{p.nativeFace,p.a,p.b,p.keep,p.drop})value(s,x);
 value(s,p.weight);
 auto key=[&](const W::Key& k){s.count(k.size());for(const auto& t:k){value(s,std::get<0>(t));value(s,std::get<1>(t));value(s,std::get<2>(t));}};
 key(p.key);key(p.keeperKeys[0]);key(p.keeperKeys[1]);origins(s,p.origins);
 s.count(p.faces.size());for(const auto& f:p.faces){
  for(label x:{f.id,f.owner,f.neighbour,f.patch,f.zone,f.installedOwner,f.installedNeighbour})value(s,x);
  labels(s,f.ring);points(s,f.rawCoordinates);points(s,f.installedCoordinates);value(s,f.physicalPatchRole);
  for(scalar x:f.quality)value(s,x);
 }
 s.count(p.cells.size());for(const auto& c:p.cells){
  value(s,c.id);labels(s,c.rawFaces);value(s,c.d);value(s,c.volume);value(s,c.centre);
 }
 s.count(p.aliases.size());for(const auto& a:p.aliases){value(s,a.first);value(s,a.second.first);value(s,a.second.second);}
 labels(s,p.outer);value(s,std::uint64_t(p.payload));
}
inline void weights(IO::Stream& s,const W::State* q) {
 bit(s,q);if(!q)return;s.text("already-decided-normal-weight-state");bit(s,q->enabled);bit(s,q->poolRefused);bit(s,q->dEnabled);
 value(s,q->discoveryCaptureSeconds);
 for(label x:{q->crossClassRefused,q->examined,q->tried,q->accepted,q->dependencyRefused,q->guardRefused,q->protectedRefused,q->geometryRefused,q->qualityRefused})value(s,x);
 s.count(q->pool.size());for(const auto& p:q->pool)packet(s,p);
 s.count(q->committed.size());for(const auto& p:q->committed)packet(s,p);
 s.count(q->survivorOrigins.size());for(const auto& o:q->survivorOrigins){value(s,o.first);origins(s,o.second);}
 s.count(q->authorizedPhysicalPruned.size());for(const auto& p:q->authorizedPhysicalPruned){value(s,p.first);value(s,p.second);}
 s.count(q->installedPrefixCells.size());for(const auto& p:q->installedPrefixCells){value(s,p.first);value(s,p.second);}
 s.count(q->installedPrefixFaces.size());for(const auto& p:q->installedPrefixFaces){value(s,p.first);for(scalar x:p.second)value(s,x);}
 value(s,std::uint64_t(q->payload)); // std::function/clock objects are never invoked/serialized.
}
inline std::string catalogue(const L::Catalogue* q,double seconds) {
 R::Writer w(64u*1024u*1024u,seconds);w.put("{\"observedComplete\":true,\"cataloguePresent\":");w.boolean(q);
 if(q){
  w.put(",\"epoch\":");w.natural(q->epoch);w.put(",\"views\":[");bool first=true;
  for(const auto& v:q->views){if(!first)w.put(",");first=false;R::view(w,v);}w.put("]");
  w.put(",\"legacyJournalPresent\":");w.boolean(bool(q->legacyJournal));
  if(q->legacyJournal){
   const auto& j=*q->legacyJournal;w.put(",\"journal\":{\"epoch\":");w.natural(j.epoch);w.put(",\"rank\":");w.integer(j.rank);
   w.put(",\"nativeCells\":");w.integer(j.nativeCells);w.put(",\"nativePoints\":");w.integer(j.nativePoints);
   w.put(",\"nGridPoints\":");w.integer(j.nGridPoints);w.put(",\"complete\":");w.boolean(j.complete);
   w.put(",\"source\":");R::fingerprint(w,j.source);w.put(",\"chargedBytes\":");w.natural(j.payloadBytes);
   w.put(",\"workSeconds\":");w.scalarValue(j.workSeconds);w.put(",\"alias\":");w.labels(j.alias);
   w.put(",\"head\":");w.labels(j.head);w.put(",\"next\":");w.labels(j.next);w.put(",\"commits\":[");first=true;
   for(const auto& c:j.commits){
    if(!first)w.put(",");first=false;w.put("{\"keep\":");w.integer(c.keep);w.put(",\"drop\":");w.integer(c.drop);
    w.put(",\"acceptedBefore\":");w.integer(c.acceptedBefore);w.put(",\"prepared\":");w.boolean(c.prepared);
    w.put(",\"complete\":");w.boolean(c.complete);w.put(",\"before\":");R::snapshot(w,c.before);
    w.put(",\"after\":");R::snapshot(w,c.after);w.put("}");
   }w.put("]}");
  }
 }w.put("}\n");return std::move(w.text);
}
inline void installed(IO::Stream& s,const polyMesh& m,const Bindings& b) {
 // Required test precedes all demand-driven access; absent caches refuse.
 if(!R::cachedGeometry(m))throw "capture-final-caches-absent";
 s.text("actual-final-after-all-normal-decisions");binding(s,b);
 value(s,m.nCells());value(s,m.nInternalFaces());points(s,m.points());
 s.count(m.faces().size());for(const auto& f:m.faces())labels(s,f);
 labels(s,m.faceOwner());labels(s,m.faceNeighbour());
 points(s,m.faceAreas());points(s,m.faceCentres());points(s,m.cellCentres());
 scalars(s,m.cellVolumes());
 s.count(m.boundaryMesh().size());for(const auto& p:m.boundaryMesh()){
  value(s,p.index());value(s,p.start());value(s,p.size());bit(s,p.coupled());
  OStringStream os;p.write(os);value(s,std::string(os.str()));
 }
 auto zones=[&](const auto& qs){s.count(qs.size());for(const auto& z:qs){value(s,std::string(z.name()));labels(s,z);}};
 zones(m.cellZones());zones(m.pointZones());
 s.count(m.faceZones().size());for(const auto& z:m.faceZones()){value(s,std::string(z.name()));labels(s,z);bits(s,z.flipMap());}
}
inline bool sourceBound(const dcCartCompleteCavityReplaySourceSidecar::Reference& ref,
                        label rank,const L::Catalogue* context) noexcept {
 // Only the latest successfully verified thread-local producer is observable.
 // Equivalent current copies are valid; reset/refresh retires older copies.
 const auto& current=dcCartCompleteCavityReplaySourceSidecar::reference();
 if(!current.snapshot||!current.verified||current.rank<0
  ||!dcCartCompleteCavityOriginCore::validFingerprint(current.verifiedSource)
  ||ref.snapshot!=current.snapshot||ref.rank!=current.rank)return false;
 const auto& latest=current.verifiedSource;const auto& retained=ref.verifiedSource;
 if(latest.raw!=retained.raw||latest.roles!=retained.roles
  ||latest.materialKey!=retained.materialKey||latest.material!=retained.material
  ||latest.producer!=retained.producer||latest.boundaryMembership!=retained.boundaryMembership)return false;
 if(!ref.snapshot||!ref.snapshot->complete||!ref.snapshot->completedFluidOnlyPath
  ||rank<0||ref.rank!=rank||!ref.verified)return false;
 const auto& fp=ref.verifiedSource;const auto& q=*ref.snapshot;
 if(!dcCartCompleteCavityOriginCore::validFingerprint(fp)||fp.raw!=q.rawSha256
  ||fp.roles!=q.rolesDigest||fp.materialKey!=q.materialKey||fp.material!=q.materialDigest
  ||fp.producer!=q.producerDigest||fp.boundaryMembership!=q.boundaryMembershipDigest)return false;
 // An existing journal remains an additional exact binding. Absence is literal
 // empty observation state, not a substitute journal or quality/source authority.
 if(context&&context->legacyJournal){
  const auto& jf=context->legacyJournal->source;
  if(!dcCartCompleteCavityOriginCore::validFingerprint(jf)||jf.raw!=fp.raw
   ||jf.roles!=fp.roles||jf.materialKey!=fp.materialKey||jf.material!=fp.material
   ||jf.producer!=fp.producer||jf.boundaryMembership!=fp.boundaryMembership)return false;
 }
 return true;
}
struct Result {bool enabled=false,complete=false;const char* refusal="disabled";std::uint64_t bytes=0;double copySeconds=0,writeSeconds=0;};
inline Result capture(const char* path,Lease& lease,const Cut& lowered,const polyMesh& actual,const Bindings* b) noexcept {
 Result r;r.enabled=IO::enabled();if(!r.enabled)return r;r.copySeconds=lease.copySeconds+pending.seconds;
 const auto start=IO::Clock::now();
 try {
  if(!lease.raw)throw lease.refusal;
  if(!pending.present||pending.nativeCells!=lease.raw->nCells)throw pending.refusal;
  if(!b||!lowered.writerBaseline||!lowered.writerBaseline->checkpoint)throw "capture-normal-pair-missing";
  const auto& ref=dcCartCompleteCavityReplaySourceSidecar::reference();
  if(!sourceBound(ref,Pstream::myProcNo(),lease.raw->completeCavityContext.get()))throw "capture-normal-source-binding-missing-or-stale";
  if(!R::cachedGeometry(actual))throw "capture-final-caches-absent";
  const double remaining=IO::Limits::seconds-r.copySeconds-std::chrono::duration<double>(IO::Clock::now()-start).count();
  IO::Stream s(path,IO::Limits::bytes,remaining);
  static_assert(sizeof(label)==4&&sizeof(scalar)==8,"capture requires native label32 binary64");
  std::uint32_t endian=1;if(*reinterpret_cast<unsigned char*>(&endian)!=1)throw "capture-endian";
  s.text("DCCART-FULL-POINT-STAR-CAPTURE-1");s.pod<std::uint32_t>(1);s.pod<std::uint32_t>(4);s.pod<std::uint32_t>(8);
  value(s,label(Pstream::myProcNo()));value(s,r.copySeconds);
  raw(s,*lowered.writerBaseline->checkpoint);raw(s,*lease.raw);
  s.text("actual-normal-post-union-currentCF");s.count(pending.currentCF.size());for(const auto& f:pending.currentCF)labels(s,f);
  source(s,*ref.snapshot);
  const auto json=catalogue(lease.raw->completeCavityContext.get(),std::max(0.0,remaining-s.elapsed()));
  s.text(json);weights(s,lease.raw->weightUnionState.get());
  installed(s,actual,*b);
  s.text("pre-finite-current-geometry-not-captured-requires-private-real-OF-reconstruction");
  s.finish();lease.raw.reset();pending=Pending();
  if(r.copySeconds+std::chrono::duration<double>(IO::Clock::now()-start).count()>=IO::Limits::seconds){
   s.complete=false;s.abort();throw "capture-total-clock";
  }
  r.bytes=s.bytes;r.complete=true;r.refusal="none";
 }catch(const char* reason){r.refusal=reason;}catch(...){r.refusal="capture-exception";}
 lease.raw.reset();pending=Pending();r.writeSeconds=std::chrono::duration<double>(IO::Clock::now()-start).count();return r;
}
}}
#endif
#endif
