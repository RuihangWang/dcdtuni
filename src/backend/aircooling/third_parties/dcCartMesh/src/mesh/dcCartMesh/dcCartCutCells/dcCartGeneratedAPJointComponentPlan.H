#ifndef dcCartGeneratedAPJointComponentPlan_H
#define dcCartGeneratedAPJointComponentPlan_H
#include "dcCartRawQualityClusterUnion.H"
#include <deque>
#include <stdexcept>
#include <cstring>
#include <iomanip>
#include <map>
#include <set>
#include <tuple>
#include <vector>
namespace Foam { namespace dcCartGeneratedAPJointComponentPlan {
namespace Q=dcCartRawUnionStockMetrics;
namespace X=dcCartRawQualityClusterUnion;
using Ring=std::vector<label>;
using CurrentRoot=std::function<bool(label,label&)>;
struct CostObservation {
 std::size_t shellCalls=0,shellBuilds=0,shellHits=0;
 std::size_t oldFaceCalls=0,oldFaceBuilds=0,oldFaceHits=0;
 std::size_t futureFaceCalls=0,futureFaceBuilds=0,futureFaceHits=0;
 std::size_t rebuilds=0,exactAreaHits=0,exactAreaMisses=0,exactPlanarHits=0;
 std::array<uint64_t,7> exceptionClasses{};label lastExceptionClass=0; // 1 clock,2 CF,3 endpoint,4 alloc,5 range,6 other
}; // fixed-size observations; no permission or decision fields

struct NFeedbackGeometryObservation {
 static constexpr uint32_t cellCap=512,faceCap=8192,pointCap=16384,referenceCap=65536;
 struct Cell {label id=-1,rank=-1,root=-1;bool alive=false,rootObserved=false;uint32_t begin=0,count=0;};
 struct Face {label id=-1,owner=-1,neighbour=-1,base=-1,patch=-1,zone=-1;bool alive=false;uint32_t begin=0,count=0;};
 struct Point {label id=-1;std::array<uint64_t,3> before{},shadow{};};
 struct Patch {label id=-1;bool value=false,consistent=true;};
 bool attempted=false,complete=false,coupledComplete=true,planReturnedTrue=false;
 label exceptionClass=0;std::array<char,96> planReason{};
 label status=0,rank=-1,floor=-1,nCells=-1;
 uint64_t globalFaces=0,globalPoints=0;
 uint32_t cells=0,primaryCells=0,faces=0,points=0,cfReferences=0,ringReferences=0;
 uint32_t stars=0,hints=0,groups=0,groupReferences=0,patches=0;
 std::array<Cell,cellCap> cell{};
 std::array<Face,faceCap> face{};
 std::array<Point,pointCap> point{};
 std::array<Patch,128> patch{};
 std::array<label,256> star{};
 std::array<label,128> hint{},groupBegin{},groupCount{};
 std::array<label,512> groupMember{};
 std::array<label,referenceCap> cf{},ring{},faceScratch{},pointScratch{};
 void observePatch(label id,bool value)noexcept {
  for(uint32_t i=0;i<patches;++i)if(patch[i].id==id){if(patch[i].value!=value){patch[i].consistent=false;coupledComplete=false;}return;}
  if(patches>=patch.size()){coupledComplete=false;return;}patch[patches++]={id,value,true};
 }
};
static_assert(std::is_trivially_copyable<NFeedbackGeometryObservation>::value,"fixed value-only raw geometry");
static_assert(sizeof(NFeedbackGeometryObservation)<=8*1024*1024,"single bounded first-hint buffer");
template<class Mesh>
void captureNFeedbackGeometry(NFeedbackGeometryObservation& o,const Mesh& mesh,const std::vector<Ring>& cf,
 const Ring& base,const std::vector<point>& before,const std::vector<point>& shadow,
 const Ring& star,const Ring* hint,label rank,label floor,const std::map<label,label>& roots,
 const std::map<label,std::vector<Q::Face>>& shells,const std::vector<std::set<label>>& groups)noexcept {
 try{
  o.rank=rank;o.floor=floor;o.nCells=mesh.nCells;o.globalFaces=mesh.faces.size();o.globalPoints=mesh.points.size();
  if(!hint||hint->empty()||hint->size()>128||star.size()>256||roots.size()>512||shells.size()>512||groups.size()>128){o.status=2;return;}
  o.hints=uint32_t(hint->size());std::copy(hint->begin(),hint->end(),o.hint.begin());
  o.stars=uint32_t(star.size());std::copy(star.begin(),star.end(),o.star.begin());
  const auto add=[&](label c)->bool{
   if(c<0||c>=mesh.nCells||std::size_t(c)>=cf.size()||std::size_t(c)>=mesh.cellRank.size()||std::size_t(c)>=mesh.cellAlive.size())return false;
   for(uint32_t i=0;i<o.cells;++i)if(o.cell[i].id==c)return true;
   if(o.cells>=512)return false;auto& q=o.cell[o.cells++];q.id=c;q.rank=mesh.cellRank[c];q.alive=mesh.cellAlive[c];
   const auto r=roots.find(c);q.rootObserved=r!=roots.end();q.root=q.rootObserved?r->second:-1;return true;};
  for(label c:star)if(!add(c)){o.status=2;return;}
  for(const auto& r:roots)if(!add(r.first)){o.status=2;return;}
  for(const auto& r:shells)if(!add(r.first)){o.status=2;return;}
  for(const auto& group:groups){
   if(group.size()>512-o.groupReferences){o.status=2;return;}
   o.groupBegin[o.groups]=label(o.groupReferences);o.groupCount[o.groups]=label(group.size());++o.groups;
   for(label c:group){if(!add(c)){o.status=2;return;}o.groupMember[o.groupReferences++]=c;}}
  for(label f:*hint){if(f<0||std::size_t(f)>=mesh.owner.size()||std::size_t(f)>=mesh.neighbour.size()
    ||!add(mesh.owner[f])||!add(mesh.neighbour[f])){o.status=3;return;}}
  o.primaryCells=o.cells;
  // Full real local endpoint geometry of primary CF; this does not grant roots.
  for(uint32_t i=0;i<o.primaryCells;++i)for(label f:cf.at(o.cell[i].id)){
   if(f<0||std::size_t(f)>=mesh.owner.size()||std::size_t(f)>=mesh.neighbour.size()){o.status=3;return;}
   for(label c:{mesh.owner[f],mesh.neighbour[f]})if(c>=0&&c<mesh.nCells&&mesh.cellRank.at(c)==rank)
    if(!add(c)){o.status=2;return;}}
  for(uint32_t i=0;i<o.cells;++i){auto& q=o.cell[i];const auto& ids=cf.at(q.id);
   if(ids.size()>65536-o.cfReferences){o.status=2;return;}
   q.begin=o.cfReferences;q.count=uint32_t(ids.size());
   for(label f:ids){if(f<0||std::size_t(f)>=mesh.faces.size()||std::size_t(f)>=base.size()){o.status=3;return;}o.cf[o.cfReferences]=f;o.faceScratch[o.cfReferences++]=f;}}
  std::sort(o.faceScratch.begin(),o.faceScratch.begin()+o.cfReferences);
  auto end=std::unique(o.faceScratch.begin(),o.faceScratch.begin()+o.cfReferences);
  if(std::size_t(end-o.faceScratch.begin())>8192){o.status=2;return;}o.faces=uint32_t(end-o.faceScratch.begin());
  for(uint32_t i=0;i<o.faces;++i){const label f=o.faceScratch[i];const auto& ring=mesh.faces.at(f);
   if(ring.size()>65536-o.ringReferences){o.status=2;return;}
   auto& q=o.face[i];q.id=f;q.owner=mesh.owner.at(f);q.neighbour=mesh.neighbour.at(f);q.base=base.at(f);
   q.patch=mesh.patch.at(f);q.zone=mesh.zone.at(f);q.alive=mesh.faceAlive.at(f);q.begin=o.ringReferences;q.count=uint32_t(ring.size());
   for(label id:ring){if(id<0||std::size_t(id)>=before.size()||std::size_t(id)>=shadow.size()){o.status=3;return;}o.ring[o.ringReferences]=id;o.pointScratch[o.ringReferences++]=id;}}
  std::sort(o.pointScratch.begin(),o.pointScratch.begin()+o.ringReferences);
  auto pointEnd=std::unique(o.pointScratch.begin(),o.pointScratch.begin()+o.ringReferences);
  if(std::size_t(pointEnd-o.pointScratch.begin())>16384){o.status=2;return;}o.points=uint32_t(pointEnd-o.pointScratch.begin());
  for(uint32_t i=0;i<o.points;++i){auto& q=o.point[i];q.id=o.pointScratch[i];
   for(label a=0;a<3;++a){static_assert(sizeof(scalar)==sizeof(uint64_t),"binary64 observation");
    std::memcpy(&q.before[a],&before[q.id][a],sizeof(uint64_t));std::memcpy(&q.shadow[a],&shadow[q.id][a],sizeof(uint64_t));}}
  for(uint32_t i=0;i<o.faces;++i){bool known=false;
   for(uint32_t j=0;j<o.patches;++j)if(o.patch[j].id==o.face[i].patch){known=o.patch[j].consistent;break;}
   if(!known)o.coupledComplete=false;
  }
  o.status=1;o.complete=true;
 }catch(...){o.status=3;o.complete=false;}
}
// Serialization occurs only after the original decision loop has closed.
// Current native IDs/roles/order remain literal; unknown roots/patches stay unknown.
template<class Stream>
void writeNFeedbackGeometry(Stream& out,const NFeedbackGeometryObservation* p){
 if(!p||!p->attempted){out<<"null";return;}const auto& g=*p;
 out<<"{\"pureGeometryOnly\":true,\"sourceAuthority\":false,\"complete\":"<<(g.complete?"true":"false")
  <<",\"status\":"<<g.status<<",\"PlanReturnedTrue\":"<<(g.planReturnedTrue?"true":"false")
  <<",\"PlanExceptionClass\":"<<g.exceptionClass<<",\"PlanReason\":"<<std::quoted(g.planReason.data())<<",\"coupledComplete\":"<<(g.coupledComplete?"true":"false")
  <<",\"rank\":"<<g.rank<<",\"floor\":"<<g.floor<<",\"nCells\":"<<g.nCells<<",\"globalFaces\":"<<g.globalFaces<<",\"globalPoints\":"<<g.globalPoints;
 if(g.complete){
  out<<",\"primaryCells\":"<<g.primaryCells<<",\"star\":[";for(uint32_t i=0;i<g.stars;++i){if(i)out<<',';out<<g.star[i];}
  out<<"],\"hints\":[";for(uint32_t i=0;i<g.hints;++i){if(i)out<<',';out<<g.hint[i];}
  out<<"],\"groups\":[";for(uint32_t i=0;i<g.groups;++i){if(i)out<<',';out<<'[';for(label k=0;k<g.groupCount[i];++k){if(k)out<<',';out<<g.groupMember[g.groupBegin[i]+k];}out<<']';}
  out<<"],\"cells\":[";for(uint32_t i=0;i<g.cells;++i){if(i)out<<',';const auto& q=g.cell[i];
   out<<"{\"id\":"<<q.id<<",\"rank\":"<<q.rank<<",\"alive\":"<<(q.alive?"true":"false")<<",\"rootObserved\":"<<(q.rootObserved?"true":"false")<<",\"root\":"<<q.root<<",\"CF\":[";
   for(uint32_t k=0;k<q.count;++k){if(k)out<<',';out<<g.cf[q.begin+k];}out<<"]}";}
  out<<"],\"faces\":[";for(uint32_t i=0;i<g.faces;++i){if(i)out<<',';const auto& q=g.face[i];
   out<<"{\"id\":"<<q.id<<",\"owner\":"<<q.owner<<",\"neighbour\":"<<q.neighbour<<",\"base\":"<<q.base<<",\"patch\":"<<q.patch<<",\"zone\":"<<q.zone<<",\"alive\":"<<(q.alive?"true":"false")<<",\"ring\":[";
   for(uint32_t k=0;k<q.count;++k){if(k)out<<',';out<<g.ring[q.begin+k];}out<<"]}";}
  out<<"],\"points\":[";for(uint32_t i=0;i<g.points;++i){if(i)out<<',';const auto& q=g.point[i];out<<"{\"id\":"<<q.id<<",\"beforeBits\":[";
   for(label k=0;k<3;++k){if(k)out<<',';out<<q.before[k];}out<<"],\"shadowBits\":[";for(label k=0;k<3;++k){if(k)out<<',';out<<q.shadow[k];}out<<"]}";}
  out<<"],\"observedCoupled\":[";for(uint32_t i=0;i<g.patches;++i){if(i)out<<',';const auto& q=g.patch[i];
   out<<"{\"patch\":"<<q.id<<",\"value\":"<<(q.value?"true":"false")<<",\"consistent\":"<<(q.consistent?"true":"false")<<'}';}out<<']';
 }out<<'}';
}

struct Component {label root=-1,keeper=-1;Ring members;};
struct Plan {
 std::vector<Component> components;
 std::map<label,Q::Face> afterFaces;
 std::map<label,Ring> afterCF,constituents;
 std::set<label> retired,affected,support;
 std::size_t closureSteps=0;
};
// Read-only geometric component proposal. It supplies no source, material,
// Point, peer, quality-inheritance or publication authority. Caller binds real
// phase incidence/recorded origins and checks the entire original->joint-final
// Stock/source/paired/peer contract, fresh moved-before VM and an atomic lease.
template<class Mesh>
bool propose(const Mesh& mesh,const std::vector<Ring>& cf,const Ring& base,
 const std::vector<point>& before,const std::vector<point>& shadow,
 const Ring& completeAPStar,label rank,label normalSideFloor,
 const CurrentRoot& currentRoot,const Q::FaceOF& oldMetric,const Q::FaceOF& futureMetric,
 const Q::CoupledTypePatch& originalCoupled,const Q::Available& work,Plan& out,const char** why=nullptr,CostObservation* costs=nullptr,const Ring* requiredGeneratedFaces=nullptr,NFeedbackGeometryObservation* geometryObservation=nullptr) {
 NFeedbackGeometryObservation* firstHint=geometryObservation&&requiredGeneratedFaces&&!geometryObservation->attempted?geometryObservation:nullptr;
 if(firstHint)firstHint->attempted=true;
 out=Plan();const auto no=[&](const char* reason){if(why)*why=reason;
  if(firstHint)std::strncpy(firstHint->planReason.data(),reason,firstHint->planReason.size()-1);out=Plan();return false;};
 const auto exception=[&](label kind,const char* reason){if(firstHint)firstHint->exceptionClass=kind;if(costs){costs->lastExceptionClass=kind;
  if(costs->exceptionClasses[kind]!=std::numeric_limits<uint64_t>::max())++costs->exceptionClasses[kind];}return no(costs?reason:"ap-graph-unknown-or-allocation");};

 Q::CoupledTypePatch observedCoupled;
 if(firstHint&&originalCoupled)try{observedCoupled=[&](label patch){const bool value=originalCoupled(patch);firstHint->observePatch(patch,value);return value;};}
 catch(...){firstHint->coupledComplete=false;}
 const Q::CoupledTypePatch& coupled=observedCoupled?observedCoupled:originalCoupled;
 try {
  if(!work||!Q::workAvailable(work)||!currentRoot||!oldMetric||!futureMetric||!coupled
   ||normalSideFloor<0||before.size()!=shadow.size()||before.size()!=mesh.points.size()
   ||base.size()!=mesh.faces.size()||cf.size()!=std::size_t(mesh.nCells)
   ||completeAPStar.empty()||completeAPStar.size()>256)return no("ap-graph-input-or-clock");
  auto local=[&](label c){return c>=0&&c<mesh.nCells&&mesh.cellAlive.at(c)&&mesh.cellRank.at(c)==rank;};
  auto face=[&](label f)->Q::Face{return {f,mesh.owner.at(f),mesh.neighbour.at(f),mesh.patch.at(f),mesh.zone.at(f),mesh.faces.at(f)};};
  // Call-local original-shell and pure numeric caches. The caller holds both
  // point views and original mesh incidence immutable for this one proposal;
  // no future cohort, Source authority or cache iterator escapes the call.
  // Bound overflow falls back to the original literal construction/calculation.
  std::map<label,std::vector<Q::Face>> originalShells;
  std::vector<Q::Face> uncachedShell;
  struct FaceValue {vector area;point centre;};
  std::map<Ring,FaceValue> oldFaceValues,futureFaceValues;
  const auto numericFace=[&](const Ring& ring,vector& sf,point& fc,bool future) {
   if(!Q::workAvailable(work))throw std::runtime_error("ap-graph-clock");
   auto& cache=future?futureFaceValues:oldFaceValues;
   if(costs){if(future)++costs->futureFaceCalls;else ++costs->oldFaceCalls;}
   // Keep the literal triangle/short-ring FaceOF cheaper than a map lookup.
   if(ring.size()<=3){
    (future?futureMetric:oldMetric)(ring,sf,fc);
    if(costs){if(future)++costs->futureFaceBuilds;else ++costs->oldFaceBuilds;}
    if(!Q::workAvailable(work))throw std::runtime_error("ap-graph-clock");return;
   }
   const auto found=cache.find(ring);
   if(found!=cache.end()){
    sf=found->second.area;fc=found->second.centre;
    if(costs){if(future)++costs->futureFaceHits;else ++costs->oldFaceHits;}
   }else{
    (future?futureMetric:oldMetric)(ring,sf,fc);
    if(costs){if(future)++costs->futureFaceBuilds;else ++costs->oldFaceBuilds;}
    if(Q::workAvailable(work)&&Q::finitePoint(sf)&&Q::finitePoint(fc)&&cache.size()<8192)
     cache.emplace(ring,FaceValue{sf,fc}); // unknown/nonfinite values are never cached
   }
   if(!Q::workAvailable(work))throw std::runtime_error("ap-graph-clock");
  };
  const Q::FaceOF fixedOld=[&](const Ring& ring,vector& sf,point& fc){numericFace(ring,sf,fc,false);};
  const Q::FaceOF fixedFuture=[&](const Ring& ring,vector& sf,point& fc){numericFace(ring,sf,fc,true);};
  std::map<label,label> rootCache;std::map<label,Q::Cell> beforeCells;
  auto root=[&](label c,label& r)->bool{if(!Q::workAvailable(work)||!local(c))return false;
   auto found=rootCache.find(c);if(found!=rootCache.end()){r=found->second;return true;}
   if(!currentRoot(c,r)||r<0)return false;rootCache.emplace(c,r);return true;};
  auto shell=[&](label c)->const std::vector<Q::Face>& {
   if(!Q::workAvailable(work))throw std::runtime_error("ap-graph-clock");if(costs)++costs->shellCalls;
   const auto saved=originalShells.find(c);
   if(saved!=originalShells.end()){
    if(costs)++costs->shellHits;if(!Q::workAvailable(work))throw std::runtime_error("ap-graph-clock");return saved->second;
   }
   std::vector<Q::Face> rows;rows.reserve(cf.at(c).size());std::set<label> seen;
   for(label f:cf.at(c)){if(!Q::workAvailable(work)||f<0||std::size_t(f)>=base.size()
    ||!mesh.faceAlive.at(f)||!seen.insert(f).second)throw std::runtime_error("ap-graph-current-CF");
    const auto row=face(f);if(row.owner!=c&&row.neighbour!=c)throw std::runtime_error("ap-graph-current-endpoint");rows.push_back(row);}
   if(costs)++costs->shellBuilds;if(!Q::workAvailable(work))throw std::runtime_error("ap-graph-clock");
   if(originalShells.size()<512)return originalShells.emplace(c,std::move(rows)).first->second;
   uncachedShell=std::move(rows);return uncachedShell;
  };
  auto oldCell=[&](label c)->const Q::Cell&{auto found=beforeCells.find(c);if(found==beforeCells.end())
   found=beforeCells.emplace(c,Q::measure(c,shell(c),before,fixedOld,coupled,work,false,false)).first;return found->second;};
  auto ordinary=[&](label f)->bool{return f>=normalSideFloor&&std::size_t(f)<base.size()
   &&mesh.faceAlive.at(f)&&base[f]==-1&&mesh.patch.at(f)==-1&&mesh.zone.at(f)==-1
   &&mesh.neighbour.at(f)>=0&&local(mesh.owner.at(f))&&local(mesh.neighbour.at(f));};
  auto other=[&](label f,label c)->label{const auto row=face(f);return row.owner==c?row.neighbour:(row.neighbour==c?row.owner:-1);};
  std::vector<std::set<label>> groups;
  struct FirstHintSeal {
   NFeedbackGeometryObservation* o;const Mesh& mesh;const std::vector<Ring>& cf;const Ring& base;
   const std::vector<point>& before;const std::vector<point>& shadow;const Ring& star;const Ring* hints;label rank,floor;
   const std::map<label,label>& roots;const std::map<label,std::vector<Q::Face>>& shells;const std::vector<std::set<label>>& groups;
   ~FirstHintSeal()noexcept{if(o)captureNFeedbackGeometry(*o,mesh,cf,base,before,shadow,star,hints,rank,floor,roots,shells,groups);}
  } seal{firstHint,mesh,cf,base,before,shadow,completeAPStar,requiredGeneratedFaces,rank,normalSideFloor,rootCache,originalShells,groups};
  auto groupIndex=[&](label c)->label{for(std::size_t i=0;i<groups.size();++i)if(groups[i].count(c))return label(i);return -1;};
  auto bounded=[&]()->bool{if(groups.size()>128)return false;std::size_t refs=0;std::set<label> all;
   for(const auto& g:groups){if(g.size()<2||g.size()>128)return false;refs+=g.size();for(label c:g)if(!all.insert(c).second)return false;}return refs<=512;};
  auto mergePath=[&](const Ring& path)->bool{
   if(path.size()<2||path.size()>128)return false;label expected=-1;std::set<label> joined;std::set<std::size_t> erase;
   for(label c:path){label r;if(!root(c,r)||(expected>=0&&r!=expected))return false;expected=r;joined.insert(c);
    const label i=groupIndex(c);if(i>=0)erase.insert(std::size_t(i));}
   for(std::size_t i:erase)joined.insert(groups[i].begin(),groups[i].end());
   if(joined.size()>128)return false;for(auto it=erase.rbegin();it!=erase.rend();++it)groups.erase(groups.begin()+*it);
   groups.push_back(std::move(joined));return bounded();};
  auto bridge=[&](label start,label end,Ring& path)->bool{
   label wanted,r;if(!root(start,wanted)||!root(end,r)||wanted!=r)return false;
   if(start==end){path={start};return true;}
   std::map<label,label> previous;std::deque<label> queue;previous.emplace(start,-1);queue.push_back(start);
   while(!queue.empty()){if(!Q::workAvailable(work))return false;const label c=queue.front();queue.pop_front();
    for(label f:cf.at(c)){if(!Q::workAvailable(work))return false;if(!ordinary(f))continue;const label n=other(f,c);label nr;
     if(n<0||previous.count(n)||!root(n,nr)||nr!=wanted)continue;
     if(previous.size()>=512)return false;previous.emplace(n,c);
     if(n==end){path.clear();for(label at=end;at>=0;at=previous.at(at))path.push_back(at);
      std::reverse(path.begin(),path.end());return path.size()<=128;}queue.push_back(n);}}
   return false;};
  // If both actual generated separator witnesses already meet the same
  // outside component, merging only their outside endpoints cannot close them.
  // Joining the two real adjacent components consumes ordinary internal sides;
  // it does not coalesce their planes or grant Source/Stock/peer authority.
  const auto commonOutsideGeneratedEdge=[&](const Q::Face& a,const Q::Face& b,
      label keeper,label expectedRoot,Ring& edge)->bool{
   if(a.id==b.id||!ordinary(a.id)||!ordinary(b.id))return false;
   const label x=a.owner==keeper?a.neighbour:(a.neighbour==keeper?a.owner:-1);
   const label y=b.owner==keeper?b.neighbour:(b.neighbour==keeper?b.owner:-1);
   if(x<0||x!=y||x==keeper)return false;
   label kr=-1,xr=-1;if(!root(keeper,kr)||!root(x,xr)||kr!=xr||kr!=expectedRoot)return false;
   const auto canonicalNow=[&](label c)->label{
    const label i=groupIndex(c);return i<0?c:*groups.at(std::size_t(i)).begin();};
   for(label id:{a.id,b.id}){
    const label owner=canonicalNow(mesh.owner.at(id)),neighbour=canonicalNow(mesh.neighbour.at(id));
    if(!((owner==keeper&&neighbour==x)||(owner==x&&neighbour==keeper)))return false;
   }
   edge=Ring{mesh.owner.at(a.id),mesh.neighbour.at(a.id)};return true;
  };
  // Propose repairs for debt in the complete moved-before star. This is
  // a read-only joint-future proposal, never permission for a bare Point edit.
  // Original->final inheritance and source conservation remain caller gates.
  std::set<label> debt;
  for(label c:completeAPStar){label r;if(!root(c,r))return no("ap-graph-star-origin");const auto& old=oldCell(c);
   if(!old.finite)return no("ap-graph-unmeasured-before-cell");
   const auto moved=Q::measure(c,shell(c),shadow,fixedFuture,coupled,work,false,false);
   if(!moved.finite)return no("ap-graph-unmeasured-moved-before-cell");
   if(moved.determinant<.001||moved.aspect>1000)debt.insert(c);}
  if(debt.empty())return no("ap-graph-no-prospective-D-AR-debt");
  std::set<label> visited;
  for(label seed:debt){if(visited.count(seed))continue;label sr;if(!root(seed,sr))return no("ap-graph-seed-origin");
   std::set<label> connected;std::deque<label> queue{seed};visited.insert(seed);
   while(!queue.empty()){if(!Q::workAvailable(work))return no("clock");label c=queue.front();queue.pop_front();connected.insert(c);
    for(label f:cf.at(c)){if(!ordinary(f))continue;const label n=other(f,c);label nr;
     if(debt.count(n)&&!visited.count(n)&&root(n,nr)&&nr==sr){visited.insert(n);queue.push_back(n);}}}
   Ring path(connected.begin(),connected.end());
   // A connected debt component already containing multiple cells is a
   // complete candidate. Only a singleton requires a genuine adjacent cell;
   // gratuitous large neighbours can cross a physical concave corner.
   if(path.size()==1){label neighbour=-1;scalar largest=-1;
    for(label c:connected)for(label f:cf.at(c)){if(!Q::workAvailable(work))return no("clock");if(!ordinary(f))continue;
     const label n=other(f,c);label nr;if(n<0||connected.count(n)||!root(n,nr)||nr!=sr)continue;
     const auto& old=oldCell(n);if(old.finite&&old.volume>largest){largest=old.volume;neighbour=n;}}
    if(neighbour<0)return no("ap-graph-no-real-debt-neighbour");path.push_back(neighbour);}
   // connected is a real BFS-connected set; singleton neighbours share a side.
   if(!mergePath(path))return no("ap-graph-seed-component-bound");
  }
  // Actual failed-facet feedback is geometric debt only, never Source permission.
  if(requiredGeneratedFaces){
   if(requiredGeneratedFaces->empty()||requiredGeneratedFaces->size()>128)return no("ap-graph-N-feedback-capacity");
   std::set<label> seenRequired;
   for(label f:*requiredGeneratedFaces){
    if(!Q::workAvailable(work)||!seenRequired.insert(f).second||!ordinary(f))return no("ap-graph-N-feedback-current-generated-side");
    const label owner=mesh.owner.at(f),neighbour=mesh.neighbour.at(f);label a=-1,b=-1;
    if(!root(owner,a)||!root(neighbour,b)||a!=b)return no("ap-graph-N-feedback-current-root");
    if(!mergePath(Ring{owner,neighbour}))return no("ap-graph-N-feedback-component-bound");
   }
  }
  X::Geometry exact(shadow,work); // existing exact FIFO remains the only exact cache
  struct ExactCacheObservation {
   const X::Geometry& geometry;CostObservation* costs;
   ~ExactCacheObservation()noexcept{if(costs){costs->exactAreaHits+=geometry.areaHits;
    costs->exactAreaMisses+=geometry.areaMisses;costs->exactPlanarHits+=geometry.planarHits;}}
  } exactObservation{exact,costs};
  auto rebuild=[&](Plan& p)->bool{
   if(costs)++costs->rebuilds;
   p=Plan();std::map<label,label> selected;std::set<label> keepers,allFaces;
   for(const auto& members:groups){label r=-1;Component component;component.keeper=*members.begin();component.members.assign(members.begin(),members.end());
    for(label c:members){label current;if(!root(c,current)||(r>=0&&current!=r)||!selected.emplace(c,component.keeper).second)return false;
     r=current;p.affected.insert(c);for(label f:cf.at(c))allFaces.insert(f);}component.root=r;keepers.insert(component.keeper);p.components.push_back(std::move(component));}
   if(allFaces.size()>4096)return false;
   using Key=std::tuple<label,label,label,label,label,X::Q,X::R>;std::map<Key,Ring> buckets;
   for(label f:allFaces){if(!Q::workAvailable(work)||!mesh.faceAlive.at(f))return false;auto row=face(f);
    const auto oo=selected.find(row.owner),nn=selected.find(row.neighbour);
    if(oo!=selected.end())row.owner=oo->second;if(nn!=selected.end())row.neighbour=nn->second;
    if(row.owner==row.neighbour){if(!ordinary(f))return false;p.retired.insert(f);continue;}
    if(row.neighbour>=0&&row.owner>row.neighbour){std::swap(row.owner,row.neighbour);std::reverse(row.ring.begin()+1,row.ring.end());}
    p.afterFaces.emplace(f,row);p.constituents.emplace(f,Ring{f});X::Q normal;
    if(!coupled(row.patch)&&exact.simplePlanar(row.ring,normal)){
     bool candidate=base[f]>=0;
     if(base[f]==-1&&ordinary(f)){label a,b;candidate=root(mesh.owner[f],a)&&root(mesh.neighbour[f],b)&&a==b;}
     if(candidate){auto primitive=exact.primitiveNormal(row.ring,normal);
      buckets[Key(base[f],row.owner,row.neighbour,row.patch,row.zone,primitive,exact.planeBound(row.ring,primitive))].push_back(f);}}
   }
   std::map<label,label> represented;
   for(const auto& entry:buckets){if(!Q::workAvailable(work))return false;const auto& ids=entry.second;if(ids.size()<2)continue;
    std::vector<Ring> rings;for(label f:ids)rings.push_back(p.afterFaces.at(f).ring);Ring outline;
    if(!exact.outline(rings,outline)){if(!Q::workAvailable(work))return false;continue;}
    const label carrier=*std::min_element(ids.begin(),ids.end());p.afterFaces.at(carrier).ring=std::move(outline);p.constituents.at(carrier)=ids;
    for(label f:ids){represented[f]=carrier;if(f!=carrier){p.afterFaces.erase(f);p.constituents.erase(f);p.retired.insert(f);}}
   }
   std::set<label> outside;
   for(const auto& row:p.afterFaces)for(label c:{row.second.owner,row.second.neighbour})
    if(c>=0&&!keepers.count(c)){if(!local(c))return false;outside.insert(c);}
   for(label c:keepers){Ring rows;for(const auto& entry:p.afterFaces)
    if(entry.second.owner==c||entry.second.neighbour==c)rows.push_back(entry.first);p.afterCF.emplace(c,std::move(rows));}
   for(label c:outside){if(!Q::workAvailable(work))return false;Ring rows;std::set<label> seen;
    for(label f:cf.at(c)){const auto mapped=represented.find(f);const label carrier=mapped==represented.end()?f:mapped->second;
     if(p.retired.count(carrier))continue;if(!seen.insert(carrier).second)continue;
     if(!p.afterFaces.count(carrier))p.afterFaces.emplace(carrier,face(carrier));const auto& row=p.afterFaces.at(carrier);
     if(row.owner!=c&&row.neighbour!=c)return false;rows.push_back(carrier);}p.afterCF.emplace(c,std::move(rows));p.affected.insert(c);}
   for(const auto& component:p.components)for(label c:component.members)
    if(c!=component.keeper)p.afterCF.emplace(c,Ring());
   p.support=p.affected;
   for(label c:p.affected)if(local(c))for(label f:cf.at(c)){const auto row=face(f);
    for(label other:{row.owner,row.neighbour})if(other>=0)p.support.insert(other);}
   if(p.support.size()>512||p.afterFaces.size()>8192)return false;
   return Q::workAvailable(work);
  };
  for(std::size_t step=0;step<128;++step){if(!Q::workAvailable(work))return no("clock");Plan candidate;
   if(!rebuild(candidate))return no("ap-graph-future-geometry-bound");bool expanded=false;
   for(const auto& component:candidate.components){std::vector<Q::Face> rows;for(label f:candidate.afterCF.at(component.keeper))rows.push_back(candidate.afterFaces.at(f));
    const auto neo=Q::measure(component.keeper,rows,shadow,fixedFuture,coupled,work,true,true);
    if(neo.finite&&neo.strictGeometry&&neo.determinant>=.001&&neo.aspect<=1000)continue;
    // Q::measure sets finite only after strict completion. A real strict
    // failure carries its original geometric witness; it is not numeric success.
    const bool geometricWitness=neo.reason&&(
     !std::strcmp(neo.reason,"face-centre-concavity")||!std::strcmp(neo.reason,"cell-convexity")
     ||!std::strcmp(neo.reason,"positive-pyramid")||!std::strcmp(neo.reason,"repaired-distance-3um"));
    if(!neo.finite&&(!geometricWitness||!Q::finitePoint(neo.centre)||!std::isfinite(neo.volume)
      ||neo.volume<=VSMALL||neo.faceIds.size()!=rows.size()))
     return no(neo.reason?neo.reason:"ap-graph-future-measure");
    label first=neo.blockingPair.face,second=neo.blockingPair.otherFace;
    if(first>=0&&second>=0){const auto& a=candidate.afterFaces.at(first);const auto& b=candidate.afterFaces.at(second);
     if(base.at(first)!=base.at(second)||a.patch!=b.patch||a.zone!=b.zone)return no("ap-graph-different-source-or-boundary-C-witness");
     const label x=a.owner==component.keeper?a.neighbour:a.owner,y=b.owner==component.keeper?b.neighbour:b.owner;
     if(x<0||y<0)return no("ap-graph-physical-C-witness");Ring path;
     if(x==y){
      if(!commonOutsideGeneratedEdge(a,b,component.keeper,component.root,path)||!mergePath(path))
       return no("ap-graph-no-real-same-root-C-bridge");
     }else{
     if(!bridge(x,y,path)||path.size()<2||!mergePath(path))return no("ap-graph-no-real-same-root-C-bridge");
     }
     expanded=true;break;
    }
    label blocking=neo.blockingFace;
    if(blocking<0)for(std::size_t j=0;j<neo.faceIds.size();++j){const vector& area=neo.areas.at(j);const scalar projection=area&(neo.centres.at(j)-neo.centre);
     if(!std::isfinite(projection)||projection/3<=1e-15*neo.volume||projection/mag(area)<=3e-6){blocking=neo.faceIds[j];break;}}
    // Evaluate the entire bounded set of genuine adjacent candidates. Volume
    // alone is not an admissible choice: a large neighbour can introduce a
    // physical concave corner while a smaller one closes the actual debt.
    std::set<label> choices;
    for(label c:component.members)for(label f:cf.at(c)){if(!Q::workAvailable(work))return no("clock");if(!ordinary(f))continue;
     const label n=other(f,c);label nr;if(n<0||std::find(component.members.begin(),component.members.end(),n)!=component.members.end()
      ||!root(n,nr)||nr!=component.root)continue;
     if(blocking>=0){const auto& row=candidate.afterFaces.at(blocking);const label demanded=row.owner==component.keeper?row.neighbour:row.owner;
      if(n!=demanded)continue;}choices.insert(n);}
    if(choices.empty()||choices.size()>128)return no("ap-graph-no-real-bounded-repair-neighbour");
    const auto originalGroups=groups;std::vector<std::set<label>> chosenGroups;
    int bestClass=4;scalar bestD=-1,bestAspect=GREAT,bestVolume=-1;
    for(label n:choices){if(!Q::workAvailable(work))return no("clock");groups=originalGroups;
     Ring path=component.members;path.push_back(n);if(!mergePath(path))continue;
     const label gi=groupIndex(component.keeper);if(gi<0)continue;const label keeper=*groups.at(gi).begin();
     Plan trial;if(!rebuild(trial))continue;std::vector<Q::Face> trialRows;
     for(label f:trial.afterCF.at(keeper))trialRows.push_back(trial.afterFaces.at(f));
     const auto measured=Q::measure(keeper,trialRows,shadow,fixedFuture,coupled,work,true,true);
     int category=4;
     if(measured.finite&&measured.strictGeometry&&measured.determinant>=.001&&measured.aspect<=1000)category=0;
     else if(measured.reason&&!std::strcmp(measured.reason,"face-centre-concavity")
       &&measured.blockingPair.face>=0&&measured.blockingPair.otherFace>=0){
      const auto& a=trial.afterFaces.at(measured.blockingPair.face);const auto& b=trial.afterFaces.at(measured.blockingPair.otherFace);
      const label x=a.owner==keeper?a.neighbour:a.owner,y=b.owner==keeper?b.neighbour:b.owner;
      label xr,yr;Ring actualPath;X::Q an,bn;
      // base=-1 denotes every generated side, not a shared original plane.
      // A neighbour bridge can coalesce this witness only when the exact
      // primitive planes agree. Distinct generated planes stay distinct.
      if(x==y){
       if(commonOutsideGeneratedEdge(a,b,keeper,component.root,actualPath))category=2;
      }else{
      const bool planar=exact.simplePlanar(a.ring,an)&&exact.simplePlanar(b.ring,bn);
      bool commonPlane=false;
      if(planar){an=exact.primitiveNormal(a.ring,an);bn=exact.primitiveNormal(b.ring,bn);
       commonPlane=an==bn&&exact.planeBound(a.ring,an)==exact.planeBound(b.ring,bn);}
      if(commonPlane&&base.at(a.id)==base.at(b.id)&&a.patch==b.patch&&a.zone==b.zone&&x>=0&&y>=0&&x!=y
        &&root(x,xr)&&root(y,yr)&&xr==yr&&bridge(x,y,actualPath)&&actualPath.size()>=2)category=1;
      }
     }else if(measured.reason&&(!std::strcmp(measured.reason,"cell-convexity")
       ||!std::strcmp(measured.reason,"positive-pyramid")||!std::strcmp(measured.reason,"repaired-distance-3um"))){
      // Recover the same literal pyramid witness as the component loop.
      // An intermediate geometry debt still needs the original final gates.
      label blocking=measured.blockingFace;
      if(blocking<0)for(std::size_t j=0;j<measured.faceIds.size();++j){const vector& area=measured.areas.at(j);const scalar projection=area&(measured.centres.at(j)-measured.centre);
       if(!std::isfinite(projection)||projection/3<=1e-15*measured.volume||projection/mag(area)<=3e-6){blocking=measured.faceIds[j];break;}}
      if(blocking>=0){const auto& f=trial.afterFaces.at(blocking);const label x=f.owner==keeper?f.neighbour:f.owner;label xr;
       if(ordinary(f.id)&&x>=0&&root(x,xr)&&xr==component.root)category=2;}
     }else if(measured.finite&&measured.strictGeometry)category=3;
     if(category>=4)continue;const auto& old=oldCell(n);if(!old.finite)continue;
     const bool better=category<bestClass||(category==bestClass&&(
       measured.determinant>bestD||(measured.determinant==bestD&&(
        measured.aspect<bestAspect||(measured.aspect==bestAspect&&old.volume>bestVolume)))));
     if(better){bestClass=category;bestD=measured.determinant;bestAspect=measured.aspect;bestVolume=old.volume;chosenGroups=groups;}
    }
    groups=originalGroups;if(chosenGroups.empty())return no("ap-graph-no-closable-adjacent-candidate");
    groups=std::move(chosenGroups);expanded=true;break;
   }
   if(expanded)continue;
   // All complete prospective keepers passed original strict geometry/D/AR.
   // Outside/source/facet/consumed-role/peer/VM inheritance is caller admission.
   candidate.closureSteps=step;out=std::move(candidate);if(firstHint)firstHint->planReturnedTrue=true;return true;
  }
  return no("ap-graph-closure-bound");
 }catch(const std::bad_alloc&){return exception(4,"ap-graph-allocation");}
 catch(const std::out_of_range&){return exception(5,"ap-graph-out-of-range");}
 catch(const std::runtime_error& e){
  if(!std::strcmp(e.what(),"ap-graph-clock"))return exception(1,"clock");
  if(!std::strcmp(e.what(),"ap-graph-current-CF"))return exception(2,"ap-graph-current-CF");
  if(!std::strcmp(e.what(),"ap-graph-current-endpoint"))return exception(3,"ap-graph-current-endpoint");
  return exception(6,"ap-graph-unknown-or-allocation");
 }catch(...){return exception(6,"ap-graph-unknown-or-allocation");}
}
} }
#endif
