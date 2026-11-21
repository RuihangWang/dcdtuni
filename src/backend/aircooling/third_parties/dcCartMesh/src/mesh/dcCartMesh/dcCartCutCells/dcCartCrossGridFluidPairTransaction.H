#ifndef dcCartCrossGridFluidPairTransaction_H
#define dcCartCrossGridFluidPairTransaction_H
#include "dcCartCompleteCavityAuthority.H"
#include "dcCartRawQualityClusterUnion.H"
#include "dcCartOriginalNodePositiveEarFacets.H"
#include "dcCartGeneratedAPSourceDelta.H"
#include "dcCartGeneratedAPJointComponentPlan.H"
#include "dcCartProcessorFullCFEndpointExchange.H"
#include <cstdint>
#include <cstring>
#include <limits>
#include <type_traits>
#include <tuple>

namespace Foam { namespace dcCartCrossGridFluidPairTransaction {
namespace Q=dcCartRawUnionStockMetrics;
namespace X=dcCartRawQualityClusterUnion;
namespace I=dcCartOriginalNodePositiveEarFacets;
namespace E=dcCartProcessorFullCFEndpointExchange;
using Mesh=dcCartCutCells::cutMesh;
using Authority=dcCartCutCells::completeCavityAuthority;
using Origin=dcCartCutCells::weightUnionOrigin;
using Lineage=dcCartCompleteCavityOriginCore::Lineage<label>;
using Ring=std::vector<label>;
inline uint64_t bits(scalar x){uint64_t b;static_assert(sizeof b==sizeof x,"binary64 required");std::memcpy(&b,&x,sizeof b);return b;}
inline bool samePoint(const point& a,const point& b){for(label k=0;k<3;++k)if(bits(a[k])!=bits(b[k]))return false;return true;}
inline bool sameFace(const Q::Face& a,const Q::Face& b){return a.id==b.id&&a.owner==b.owner&&a.neighbour==b.neighbour&&a.patch==b.patch&&a.zone==b.zone&&a.ring==b.ring;}
inline bool passes(scalar v,unsigned k){static const scalar t[5]={.001,.05,.01,4,70};return std::isfinite(v)&&(k<3?v>=t[k]:v<=t[k]);}
inline bool inherited(scalar a,scalar b,unsigned k){return passes(a,k)?passes(b,k):(std::isfinite(a)&&std::isfinite(b)&&(k<3?b>=a*(1-1e-10):b<=a*(1+1e-10)));}

// Read-only endpoint measurements for the complete facets actually checked by
// FinalStock. These cells are not graph members, AP movers, Source origins,
// processor-request owners, SDK transition owners or publication cache entries.
struct LocalFacetMetricSupport {
 std::map<label,Q::Cell> before,final;
 std::map<label,Ring> fullCF;
 std::map<label,label> ranks,bases;
 std::map<label,Q::Face> faces;
 std::map<label,point> points;
};
inline bool localFacetMetricSupportFresh(const LocalFacetMetricSupport& support,
       const Mesh& mesh,const std::vector<Ring>& cf,const std::vector<label>& base,
       label rank,const Q::Available& work=Q::Available()) noexcept {
 try {
  for(const auto& value:support.fullCF){const label c=value.first;const auto saved=support.ranks.find(c);
   if(!Q::workAvailable(work)||c<0||std::size_t(c)>=std::size_t(mesh.nCells)
     ||std::size_t(c)>=cf.size()||std::size_t(c)>=mesh.cellAlive.size()
     ||std::size_t(c)>=mesh.cellRank.size()||!mesh.cellAlive[c]||mesh.cellRank[c]!=rank
     ||saved==support.ranks.end()||saved->second!=mesh.cellRank[c]||cf[c]!=value.second)return false;}
  for(const auto& value:support.faces){const label f=value.first;const auto src=support.bases.find(f);const auto& row=value.second;
   if(!Q::workAvailable(work)||f<0||std::size_t(f)>=mesh.faces.size()||std::size_t(f)>=base.size()
     ||std::size_t(f)>=mesh.faceAlive.size()||std::size_t(f)>=mesh.owner.size()
     ||std::size_t(f)>=mesh.neighbour.size()||std::size_t(f)>=mesh.patch.size()||std::size_t(f)>=mesh.zone.size()
     ||!mesh.faceAlive[f]||src==support.bases.end()||base[f]!=src->second||row.id!=f
     ||row.owner!=mesh.owner[f]||row.neighbour!=mesh.neighbour[f]||row.patch!=mesh.patch[f]
     ||row.zone!=mesh.zone[f]||row.ring!=mesh.faces[f])return false;}
  for(const auto& value:support.points)if(!Q::workAvailable(work)||value.first<0
     ||std::size_t(value.first)>=mesh.points.size()||!samePoint(value.second,mesh.points[value.first]))return false;
  return Q::workAvailable(work);
 }catch(...){return false;}
}
inline bool captureLocalFacetMetricSupport(const Mesh& mesh,const std::vector<Ring>& cf,
       const std::vector<label>& base,const std::vector<point>& shadow,label rank,
       const std::set<label>& required,const std::set<label>& forbidden,
       const std::map<label,Q::Face>& stagedFaces,const std::set<label>& retired,
       const std::map<label,Ring>& primaryCF,const std::map<label,Q::Face>& primaryFaces,
       const std::map<label,point>& primaryPoints,const Q::FaceOF& beforeMetric,
       const Q::FaceOF& finalMetric,const Q::CoupledTypePatch& coupled,const Q::Available& work,
       LocalFacetMetricSupport& out,const char** why=nullptr) {
 const auto no=[&](const char* text){if(why)*why=text;return false;};
 try {
  if(!out.fullCF.empty()||!out.before.empty()||!out.final.empty()||!out.faces.empty()||!out.points.empty()
    ||!out.ranks.empty()||!out.bases.empty()||!beforeMetric||!finalMetric||!coupled
    ||shadow.size()!=mesh.points.size()||cf.size()!=std::size_t(mesh.nCells)
    ||base.size()!=mesh.faces.size())return no("ap-joint-local-metric-input");
  if(primaryCF.size()+required.size()>1024)return no("ap-joint-local-metric-support-capacity");
  LocalFacetMetricSupport support;std::size_t faceCount=primaryFaces.size(),pointCount=primaryPoints.size();
  for(label c:required){
   if(!Q::workAvailable(work))return no("clock");
   if(c<0||std::size_t(c)>=std::size_t(mesh.nCells)||std::size_t(c)>=mesh.cellAlive.size()
     ||std::size_t(c)>=mesh.cellRank.size()||!mesh.cellAlive[c]||mesh.cellRank[c]!=rank
     ||forbidden.count(c)||primaryCF.count(c))return no("ap-joint-local-metric-not-readonly-local");
   const Ring& ids=cf[c];if(ids.size()<4||ids.size()>512)return no("ap-joint-local-metric-complete-CF");
   std::set<label> unique;std::vector<Q::Face> shell;shell.reserve(ids.size());
   std::map<std::pair<label,label>,std::pair<label,label>> edges;
   for(label f:ids){
    if(!Q::workAvailable(work))return no("clock");
    if(f<0||std::size_t(f)>=mesh.faces.size()||std::size_t(f)>=mesh.faceAlive.size()
      ||std::size_t(f)>=mesh.owner.size()||std::size_t(f)>=mesh.neighbour.size()
      ||std::size_t(f)>=mesh.patch.size()||std::size_t(f)>=mesh.zone.size()
      ||!mesh.faceAlive[f]||!unique.insert(f).second||retired.count(f))return no("ap-joint-local-metric-incidence");
    const Q::Face row(f,mesh.owner[f],mesh.neighbour[f],mesh.patch[f],mesh.zone[f],mesh.faces[f]);
    if((row.owner!=c&&row.neighbour!=c)||row.ring.size()<3||row.ring.size()>512)
      return no("ap-joint-local-metric-real-endpoint");
    const auto changed=stagedFaces.find(f);
    if(changed!=stagedFaces.end()&&!sameFace(row,changed->second))return no("ap-joint-local-metric-staged-mutation");
    const auto inserted=support.faces.emplace(f,row);
    if(inserted.second&&!primaryFaces.count(f)&&++faceCount>16384)return no("ap-joint-full-provider-inventory");
    support.bases.emplace(f,base[f]);shell.push_back(row);
    for(std::size_t j=0;j<row.ring.size();++j){
     if(!Q::workAvailable(work))return no("clock");label a=row.ring[j],b=row.ring[(j+1)%row.ring.size()];
     if(row.owner!=c)std::swap(a,b);auto& edge=edges[std::minmax(a,b)];++edge.first;edge.second+=a<b?1:-1;}
    for(label id:row.ring){
     if(!Q::workAvailable(work))return no("clock");
     if(id<0||std::size_t(id)>=mesh.points.size()||!Q::finitePoint(mesh.points[id])
       ||!samePoint(mesh.points[id],shadow[id]))return no("ap-joint-local-metric-point-not-literal");
     const auto pointInserted=support.points.emplace(id,mesh.points[id]);
     if(pointInserted.second&&!primaryPoints.count(id)&&++pointCount>65536)return no("ap-joint-full-provider-inventory");
    }
   }
   // The original Q strict-shell topological closure predicate, without a
   // convexity/3um or publication permission for this unchanged metric cell.
   for(const auto& edge:edges){
    if(!Q::workAvailable(work))return no("clock");
    if(edge.second.first!=2||edge.second.second!=0)return no("ap-joint-local-metric-directed-edge-closure");}
   // Both CC/V views are freshly measured from the actual complete current CF.
   // No strict self-cell permission is inferred for an unchanged extra endpoint.
   auto before=Q::measure(c,shell,mesh.points,beforeMetric,coupled,work,false,false);
   if(!before.finite)return no(before.reason?before.reason:"ap-joint-local-metric-before");
   auto final=Q::measure(c,shell,shadow,finalMetric,coupled,work,false,false);
   if(!final.finite)return no(final.reason?final.reason:"ap-joint-local-metric-final");
   if(!samePoint(before.centre,final.centre)||bits(before.volume)!=bits(final.volume))
     return no("ap-joint-local-metric-CCV-not-literal");
   support.fullCF.emplace(c,ids);support.ranks.emplace(c,mesh.cellRank[c]);
   support.before.emplace(c,std::move(before));support.final.emplace(c,std::move(final));
  }
  if(!localFacetMetricSupportFresh(support,mesh,cf,base,rank,work))return no("ap-joint-local-metric-stale-or-clock");
  out=std::move(support);return true;
 }catch(...){return no("ap-joint-local-metric-unknown-or-allocation");}
}

struct SourceProof {label source=-1,originalOwner=-1,originalNeighbour=-1,axis=-1;uint64_t supportBits=0;Ring originalRing;std::vector<Q::Face> cohort;};
struct OuterRepresentation {label source=-1,host=-1,outside=-1,patch=-1,zone=-1,carrier=-1,mateSource=-1;Ring members,outline;};
// A coordinate proposal only. No ledger/source/MPI or displacement permission.
enum class APSourceKind { PairedWall, ProducerPlainWall, RecordedInternal };
struct GeneratedAPProjection {
 label nativePoint=-1,constructorPart=-1,recordedRoot=-1,source=-1,mateSource=-1;
 APSourceKind sourceKind=APSourceKind::PairedWall;
 uint64_t constructionGeneration=0,lineageRevision=0;scalar fraction=1;
 point before=point::zero,target=point::zero;vector sourceArea=vector::zero;
 point sourceCentre=point::zero;Ring completeCurrentStar;
};
enum class Mode { CartesianCrossGrid, SameOriginGeneratedSide };
struct PositivePyramidSample {
 label cell=-1,keep=-1,drop=-1,oldCell=-1,face=-1,owner=-1,neighbour=-1,newOwner=-1,newNeighbour=-1,source=-1,sourceOwner=-1,sourceNeighbour=-1;
 Mode mode=Mode::CartesianCrossGrid;bool host=false,oldKnown=false;
 scalar oldPV=0,newPV=0,oldCutoff=0,newCutoff=0,oldVolume=0,newVolume=0;
 point oldCC=point::zero,newCC=point::zero,oldFC=point::zero,newFC=point::zero;
 vector oldArea=vector::zero,newArea=vector::zero;
};
struct PositivePyramidObservations {
 std::array<PositivePyramidSample,8> samples{},sameSamples{};std::size_t count=0,sameCount=0,hostRefused=0,outsideRefused=0;
};
class Phase;
// A source-delta/shadow preparation. This is not a Point or publication token.
class GeneratedAPShadow {
 public:
 // First existing FinalStock inheritance failure only; no authority or metric.
 // Branch 0 is unknown/cached, 1 local internal, 2 physical, 3 real coupled peer.
 struct FinalStockFailureObservation {
  bool observed=false,beforeNumeric=false,beforeValid=false,finalNumeric=false,finalValid=false,finalCached=false;
  label face=-1,carrier=-1,slot=-1;
  label beforeOwner=-1,beforeNeighbour=-1,beforePatch=-1,beforeZone=-1;
  label finalOwner=-1,finalNeighbour=-1,finalPatch=-1,finalZone=-1;
  label beforeBranch=0,finalBranch=0;
  std::array<uint64_t,5> beforeQualityBits{},finalQualityBits{};
 };
 static_assert(std::is_trivially_copyable<FinalStockFailureObservation>::value,"fixed value observation required");
 static_assert(sizeof(FinalStockFailureObservation)<=160,"bounded first-failure observation");
 private:
 friend class Phase;
 const Phase* phase_=nullptr;uint64_t revision_=0,lineageRevision_=0,nonce_=0;
 bool measured_=false;
 std::vector<GeneratedAPProjection> projections_;
 std::vector<dcCartGeneratedAPSourceDelta::Move> moves_;
 std::vector<dcCartGeneratedAPSourceDelta::CellShell> star_;
 dcCartGeneratedAPSourceDelta::Result sourceDelta_;
 std::vector<Origin> sourceOrigins_;
 std::shared_ptr<const dcCartPostCarveFluidOwnershipPreview::Snapshot> snapshot_;
 dcCartCompleteCavityOriginCore::Fingerprint fingerprint_;
 std::map<label,Q::Cell> movedBeforeCells_;
 dcCartGeneratedAPJointComponentPlan::Plan graph_;
 bool geometryPlanned_=false,vmAndRepresentationProved_=false;
 std::map<label,Q::Cell> originalCells_,finalCells_;
 LocalFacetMetricSupport localMetricSupport_;
 std::map<label,Ring> capturedCF_;
 std::map<label,Q::Face> capturedFaces_;
 std::map<label,label> capturedBases_,capturedRanks_;
 std::map<label,point> capturedPoints_;
 std::vector<std::vector<Origin>> componentOrigins_;
 bool stockProved_=false;
 FinalStockFailureObservation finalStockFailure_;
 label finalStockNClosureFace_=-1; // actual failed N, not observer or permission
 std::map<label,Q::FacetQuality> originalFacets_,finalFacets_;

 public:
 GeneratedAPShadow()=default;GeneratedAPShadow(const GeneratedAPShadow&)=delete;
 GeneratedAPShadow& operator=(const GeneratedAPShadow&)=delete;
 GeneratedAPShadow(GeneratedAPShadow&&) noexcept=default;
 GeneratedAPShadow& operator=(GeneratedAPShadow&&) noexcept=default;
 bool sourceDeltaMeasured()const noexcept{return measured_;}
 const std::vector<GeneratedAPProjection>& projections()const noexcept{return projections_;}
 const std::vector<dcCartGeneratedAPSourceDelta::CellShell>& currentStar()const noexcept{return star_;}
 const std::map<label,Q::Cell>& freshMovedBefore()const noexcept{return movedBeforeCells_;}
 const dcCartGeneratedAPJointComponentPlan::Plan* componentGeometry()const noexcept{return geometryPlanned_?&graph_:nullptr;}
 const FinalStockFailureObservation& finalStockFailureObservation()const noexcept{return finalStockFailure_;}

};
// Own every fallible publisher buffer and the opaque lineage lease. Phase,
// captured mesh and shadow remain exclusively alive through vote/cancellation.
class JointPublication {
 friend class Phase;
 Phase* owner_=nullptr;GeneratedAPShadow* shadow_=nullptr;
 uint64_t revision_=0,lineageRevision_=0;bool ready_=false,readOnly_=false,published_=false;
 std::vector<Lineage::PreparedReplacement> replacements_;
 Lineage::IndependentPublicationLease lease_;
 std::map<label,Ring> afterCF_,publishRings_;
 std::map<label,Q::Face> afterFaces_;
 std::map<label,Q::Cell> afterCells_;
 std::map<label,vector> faceAreas_;std::map<label,point> faceCentres_;
 std::vector<std::pair<label,label>> componentRoots_;
 Ring retiredFaces_,retiredCells_;
 std::vector<std::pair<label,label>> installedRetirements_;
 std::shared_ptr<dcCartCutCells::writerCoupledTransition> transition_;
 std::size_t transitionSlot_=0;
 public:
 JointPublication()=default;JointPublication(const JointPublication&)=delete;
 JointPublication& operator=(const JointPublication&)=delete;
 JointPublication(JointPublication&&)=delete;JointPublication& operator=(JointPublication&&)=delete;
 ~JointPublication()noexcept;
 bool published()const noexcept{return published_;}
 bool readOnly()const noexcept{return readOnly_;}
 const std::map<label,Q::Cell>& measuredAfter()const noexcept{return afterCells_;}
 const Ring& retiredCells()const noexcept{return retiredCells_;}
 const std::map<label,vector>& measuredFaceAreas()const noexcept{return faceAreas_;}
 const std::map<label,point>& measuredFaceCentres()const noexcept{return faceCentres_;}
 const std::vector<std::pair<label,label>>& componentRoots()const noexcept{return componentRoots_;}
};
class Prepared {
 friend class Phase;
 const Phase* phase_=nullptr;uint64_t revision_=0,lineageRevision_=0;bool ready_=false,committed_=false;
 Mode mode_=Mode::CartesianCrossGrid;label sameOriginParent_=-1;
 label keep_=-1,retire_=-1,mateKeep_=-1,mateRetire_=-1,mateOriginParent_=-1;std::vector<label> members_,mateMembers_,retired_,outer_;
 std::map<label,Ring> afterCF_;std::vector<OuterRepresentation> representations_;
 std::map<label,label> representedBy_;std::map<label,std::array<label,2>> pairedRoles_;
 std::map<label,Ring> publishRings_;
 std::map<label,Q::Face> beforeFaces_,afterFaces_;
 std::map<label,Ring> beforeCF_;std::map<label,point> points_;
 std::map<label,label> beforeBases_,beforeRanks_;
 std::map<label,Q::Cell> beforeCells_,afterCells_;
 std::map<label,Q::FacetQuality> beforeFacets_,afterFacets_;
 std::vector<SourceProof> sources_;std::vector<Origin> completeOrigins_;
 std::shared_ptr<const dcCartPostCarveFluidOwnershipPreview::Snapshot> snapshot_;
 dcCartCompleteCavityOriginCore::Fingerprint fingerprint_;
 Lineage::PreparedReplacement replacement_,mateReplacement_;
public:
 Prepared()=default;Prepared(const Prepared&)=delete;Prepared& operator=(const Prepared&)=delete;
 Prepared(Prepared&&) noexcept=default;Prepared& operator=(Prepared&&) noexcept=default;
 bool ready()const noexcept{return ready_;}bool committed()const noexcept{return committed_;}
 label keeper()const noexcept{return keep_;}label retiredCell()const noexcept{return retire_;}
 label mateKeeper()const noexcept{return mateKeep_;}label mateRetiredCell()const noexcept{return mateRetire_;}
 label mateOriginParent()const noexcept{return mateOriginParent_;}
 Mode mode()const noexcept{return mode_;}label sameOriginParent()const noexcept{return sameOriginParent_;}
 const std::map<label,Q::Cell>& measuredAfter()const noexcept{return afterCells_;}
 const std::vector<label>& retiredFaces()const noexcept{return retired_;}
 const std::map<label,Q::Face>& changedFaces()const noexcept{return afterFaces_;}
 const std::vector<SourceProof>& sourceProofs()const noexcept{return sources_;}
};

// One exclusive post-cone union phase. No point/face append or other mesh mutation
// may occur between begin and destruction; any other route must destroy/rebuild it.
// Source labels are immutable in this phase; alive incidence changes only through
// this class's local commit. No source/MPI/peer permission is cached here.
class Phase {
 friend class JointPublication;
 Mesh* mesh_=nullptr;const std::vector<label>* base_=nullptr;
 std::vector<std::vector<label>>* cf_=nullptr;
 std::vector<label> head_,next_;std::size_t faceCount_=0,pointCount_=0;
 const point* pointStorage_=nullptr;uint64_t revision_=0,lineageRevision_=0;
 std::vector<Prepared> journal_;label rank_=-1;bool ready_=false,readOnlyScope_=false;
  // Candidate acceleration only; no cached point/source/remote permission.
  std::vector<label> apBirthSlot_;
  std::vector<Ring> apIncidence_;
  Ring apPotentialCells_; // scheduling-only, from this phase complete incidence
  std::map<label,Ring> apBirthsByRoot_;
  bool apIncidenceReady_=false;
  uint64_t apIncidenceGeneration_=0;
  std::vector<point> jointShadowPoints_;
  bool jointShadowReady_=false;
  std::vector<dcCartGeneratedAPSourceDelta::Move> jointShadowMoves_;uint64_t jointShadowNonce_=0;
  bool jointPublicationActive_=false;
  std::map<label,Ring> processorFacesByPeer_;std::set<label> readOnlyProcessorFaces_;bool readOnlyStockProved_=false;
  std::map<label,Ring> readOnlyCF_;
  std::map<label,Q::Face> readOnlyFaces_;
  std::map<label,label> readOnlyBases_,readOnlyRanks_;
  std::map<label,point> readOnlyPoints_;
  std::map<label,std::vector<Origin>> readOnlyOrigins_;
  uint64_t readOnlyRevision_=0,readOnlyLineageRevision_=0;

 label generatedFaceFloor_=-1;PositivePyramidObservations* positiveObservation_=nullptr;Mode phaseMode_=Mode::CartesianCrossGrid;
 using PairKey=std::tuple<label,label,std::vector<uint64_t>>;
 std::map<PairKey,std::vector<label>> pairedSources_;
 std::vector<std::array<label,2>> indexedPairs_;
 const dcCartPostCarveFluidOwnershipPreview::Snapshot* indexedSnapshot_=nullptr;
 bool pairedIndexReady_=false;
 bool pairedOriginal(Prepared& p,label source,const Q::CoupledTypePatch& coupled,
                     const Q::Available& work,label& mate,const char** why) {
  const auto no=[&](const char* x){if(why)*why=x;return false;};
  const auto& snapshot=*p.snapshot_;const auto& original=snapshot.faces.at(source);
  if(original.zone<0){mate=-1;return true;}
  const label zone=original.zone;
  if(std::size_t(zone)>=mesh_->physicalWallPatchPairs.size())return no("outer-paired-producer-role-missing");
  const auto pair=mesh_->physicalWallPatchPairs[zone];
  if(pair[0]<0||pair[1]<0||pair[0]==pair[1]||(original.patch!=pair[0]&&original.patch!=pair[1])
    ||original.neighbour!=-1||coupled(original.patch))return no("outer-paired-producer-role");
  auto ringKey=[&](const auto& face,bool reverse,std::vector<uint64_t>& out)->bool {
   const auto& ring=face.ring;if(ring.size()<3||ring.size()>64)return false;
   std::vector<std::array<uint64_t,3>> points;points.reserve(ring.size());
   for(std::size_t j=0;j<ring.size();++j){if(!Q::workAvailable(work))return false;
    const label id=ring[reverse?(ring.size()-j)%ring.size():j];
    if(id<0||std::size_t(id)>=snapshot.points.size())return false;
    const auto& q=snapshot.points[id];for(unsigned k=0;k<3;++k)if(!std::isfinite(q[k]))return false;
    points.push_back({{bits(q[0]),bits(q[1]),bits(q[2])}});}
   std::size_t start=0;for(std::size_t j=1;j<points.size();++j){bool less=false;
    for(std::size_t k=0;k<points.size();++k){const auto& a=points[(j+k)%points.size()];const auto& b=points[(start+k)%points.size()];
     if(a<b){less=true;break;}if(b<a)break;}if(less)start=j;}
   out.clear();out.reserve(points.size()*3);for(std::size_t j=0;j<points.size();++j)
    for(uint64_t x:points[(start+j)%points.size()])out.push_back(x);return true;
  };
  if(pairedIndexReady_&&(indexedSnapshot_!=p.snapshot_.get()||indexedPairs_!=mesh_->physicalWallPatchPairs))
   return no("outer-paired-index-stale");
  if(!pairedIndexReady_){pairedSources_.clear();indexedPairs_=mesh_->physicalWallPatchPairs;
   for(std::size_t j=0;j<snapshot.faces.size();++j){if(!Q::workAvailable(work))return no("clock");
    const auto& row=snapshot.faces[j];if(row.zone<0||std::size_t(row.zone)>=indexedPairs_.size()||row.neighbour!=-1)continue;
    const auto role=indexedPairs_[row.zone];if(role[0]<0||role[1]<0||role[0]==role[1]
      ||(row.patch!=role[0]&&row.patch!=role[1])||coupled(row.patch))continue;
    std::vector<uint64_t> key;if(!ringKey(row,false,key))continue;
    pairedSources_[PairKey(row.zone,row.patch,std::move(key))].push_back(j);
   }
   if(!Q::workAvailable(work))return no("clock");indexedSnapshot_=p.snapshot_.get();pairedIndexReady_=true;
  }
  std::vector<uint64_t> key;if(!ringKey(original,true,key))return no("outer-paired-original-ring");
  const label opposite=original.patch==pair[0]?pair[1]:pair[0];
  const auto found=pairedSources_.find(PairKey(zone,opposite,std::move(key)));
  if(found==pairedSources_.end()||found->second.size()!=1||found->second.front()==source)
   return no("outer-paired-original-ring-ambiguous-or-missing");
  mate=found->second.front();p.pairedRoles_[zone]=pair;return true;
 }
 bool plainOriginal(label source,const Authority& authority,const Q::CoupledTypePatch& coupled)const {
  if(!authority.sourceSnapshot||source<0||std::size_t(source)>=authority.sourceSnapshot->faces.size())return false;
  const auto& original=authority.sourceSnapshot->faces[source];const label zone=original.zone;
  if(original.handle!=source||original.neighbour!=-1||original.patch<0||coupled(original.patch)||zone<0
     ||std::size_t(zone)>=mesh_->physicalPlainWallRoles.size()||std::size_t(zone)>=mesh_->physicalWallPatchPairs.size())return false;
  const auto& role=mesh_->physicalPlainWallRoles[zone];const auto pair=mesh_->physicalWallPatchPairs[zone];
  // The actual APP ordinary-wall role is the proof, never absent pair metadata.
  return role.known&&!role.entity.empty()&&!role.region.empty()&&!role.sha.empty()&&!role.operation.empty()
      &&pair[0]<0&&pair[1]<0;
 }
 bool live(const Q::Available& work)const {
  return ready_&&Q::workAvailable(work)&&mesh_&&base_&&cf_&&mesh_->faces.size()==faceCount_
   &&mesh_->points.size()==pointCount_&&mesh_->points.data()==pointStorage_
   &&base_->size()==faceCount_&&cf_->size()==std::size_t(mesh_->nCells)
   &&mesh_->owner.size()==faceCount_&&mesh_->neighbour.size()==faceCount_&&mesh_->patch.size()==faceCount_
   &&mesh_->zone.size()==faceCount_&&mesh_->faceAlive.size()==faceCount_&&mesh_->cellAlive.size()==cf_->size()&&mesh_->cellRank.size()==cf_->size()
   &&mesh_->completeCavityLineage.active()&&mesh_->completeCavityLineage.revision()==lineageRevision_;
 }
 Q::Face face(label f)const {const Mesh& m=*mesh_;return {f,m.owner.at(f),m.neighbour.at(f),m.patch.at(f),m.zone.at(f),m.faces.at(f)};}
 bool local(label c)const {return c>=0&&c<mesh_->nCells&&mesh_->cellAlive.at(c)&&mesh_->cellRank.at(c)==rank_;}
 bool permission(const Authority& a,const std::vector<label>& roots)const {
  if(!a.ready()||!dcCartCompleteCavityAuthority::available(a.remainingSeconds)||!a.sourceSnapshot
    ||!a.sourceSnapshotIsRecorded||!a.sourceSnapshotIsRecorded(a.sourceSnapshot.get()))return false;
  std::vector<Origin> origins;origins.reserve(roots.size());
  for(label c:roots){if(!dcCartCompleteCavityAuthority::available(a.remainingSeconds)||!a.recordedParent(c))return false;origins.push_back(a.normalOriginFromSnapshot(c));}
  return a.completeOrigins(origins);
 }
 // Read only the failed measurement and its already-produced before cache.
 // No authority/work calls, allocations, I/O, or planner operand mutation.
 void observePositive(const Prepared& p,label c,const Q::Cell& neo) noexcept {
  if(!positiveObservation_||!neo.reason||std::strcmp(neo.reason,"positive-pyramid"))return;
  auto& stats=*positiveObservation_;auto& total=(c==p.keep_||c==p.mateKeep_)?stats.hostRefused:stats.outsideRefused;
  if(total!=std::numeric_limits<std::size_t>::max())++total;
  auto& samples=p.mode_==Mode::SameOriginGeneratedSide?stats.sameSamples:stats.samples;
  auto& count=p.mode_==Mode::SameOriginGeneratedSide?stats.sameCount:stats.count;
  if(count>=samples.size())return;
  try {
   PositivePyramidSample sample;sample.cell=c;sample.keep=c==p.mateKeep_?p.mateKeep_:p.keep_;sample.drop=c==p.mateKeep_?p.mateRetire_:p.retire_;
   sample.mode=p.mode_;sample.host=c==p.keep_||c==p.mateKeep_;sample.newCC=neo.centre;sample.newVolume=neo.volume;
   sample.newCutoff=1e-15*neo.volume;
   for(std::size_t j=0;j<neo.faceIds.size();++j){const scalar pv=(neo.areas.at(j)&(neo.centres.at(j)-neo.centre))/3;
    if(std::isfinite(pv)&&pv>sample.newCutoff)continue;
    sample.face=neo.faceIds[j];sample.newPV=pv;sample.newFC=neo.centres[j];sample.newArea=neo.areas[j];
    const auto beforeFace=p.beforeFaces_.find(sample.face);
    if(beforeFace!=p.beforeFaces_.end()){sample.owner=beforeFace->second.owner;sample.neighbour=beforeFace->second.neighbour;}
    const auto changed=p.afterFaces_.find(sample.face);
    if(changed!=p.afterFaces_.end()){sample.newOwner=changed->second.owner;sample.newNeighbour=changed->second.neighbour;}
    else {sample.newOwner=sample.owner;sample.newNeighbour=sample.neighbour;}
    const auto source=p.beforeBases_.find(sample.face);if(source!=p.beforeBases_.end())sample.source=source->second;
    if(p.snapshot_&&sample.source>=0&&std::size_t(sample.source)<p.snapshot_->faces.size()){
     sample.sourceOwner=p.snapshot_->faces[sample.source].owner;sample.sourceNeighbour=p.snapshot_->faces[sample.source].neighbour;}
    sample.oldCell=c;
    // A new keeper face may come from the retired member's old full shell.
    if(sample.host&&sample.owner!=c&&sample.neighbour!=c
     &&(sample.owner==sample.drop||sample.neighbour==sample.drop))sample.oldCell=sample.drop;
    const auto old=p.beforeCells_.find(sample.oldCell);if(old!=p.beforeCells_.end()){
     const auto at=std::find(old->second.faceIds.begin(),old->second.faceIds.end(),sample.face);
     if(at!=old->second.faceIds.end()){const std::size_t k=at-old->second.faceIds.begin();sample.oldKnown=true;
      sample.oldCC=old->second.centre;sample.oldVolume=old->second.volume;sample.oldCutoff=1e-15*sample.oldVolume;
      sample.oldFC=old->second.centres.at(k);sample.oldArea=old->second.areas.at(k);
      sample.oldPV=(sample.oldArea&(sample.oldFC-sample.oldCC))/3;}}
    samples[count]=sample;++count;break;
   }
  }catch(...){} // Observation failure cannot refuse or publish a transaction.
 }
 static bool sameFingerprint(const dcCartCompleteCavityOriginCore::Fingerprint& a,const dcCartCompleteCavityOriginCore::Fingerprint& b) {
  return a.raw==b.raw&&a.roles==b.roles&&a.materialKey==b.materialKey&&a.material==b.material&&a.producer==b.producer&&a.boundaryMembership==b.boundaryMembership;
 }
public:
 Phase()=default;Phase(const Phase&)=delete;Phase& operator=(const Phase&)=delete;
private:
 // Once per phase: prove caller's maintained CF is the complete actual incidence,
 // and build immutable source->all face IDs (dead entries are filtered lazily).
 bool beginScope(Mesh& m,const std::vector<label>& base,std::vector<std::vector<label>>& cf,label rank,
            const Q::Available& work,std::size_t maximumCommits,const char** why,
            label normalGeneratedFaceFloor,PositivePyramidObservations* observation,Mode phaseMode,bool readOnlyScope) {
  const auto no=[&](const char* s){if(why)*why=s;return false;};
  if(ready_)return no("grid-pair-phase-already-started");
  try {
   if(!work||!Q::workAvailable(work)||(!maximumCommits&&!readOnlyScope)||(readOnlyScope&&maximumCommits)||maximumCommits>(phaseMode==Mode::SameOriginGeneratedSide?1024:128)||(phaseMode!=Mode::CartesianCrossGrid&&phaseMode!=Mode::SameOriginGeneratedSide)||m.nCells<0||m.faces.size()>std::size_t(std::numeric_limits<label>::max())
    ||base.size()!=m.faces.size()||cf.size()!=std::size_t(m.nCells)||m.owner.size()!=base.size()||m.neighbour.size()!=base.size()
    ||m.patch.size()!=base.size()||m.zone.size()!=base.size()||m.faceAlive.size()!=base.size()
    ||m.cellAlive.size()!=cf.size()||m.cellRank.size()!=cf.size()||!m.completeCavityLineage.active())return no("grid-pair-phase-input");
   std::vector<unsigned char> incidence(base.size(),0);std::vector<label> head(base.size(),-1),next(base.size(),-1);
   std::map<label,Ring> processorFaces;
    std::vector<label> apSlots;std::vector<Ring> apFaces;
    std::vector<unsigned char> apCellSeen;Ring apCells;
    std::map<label,Ring> apRoots;bool apIndex=m.normalConeAPBirthsSealed
        &&m.normalConeAPConstructionGeneration&&!m.normalConeAPBirths.empty();
    if(apIndex){
     if(m.normalConeAPBirths.size()>std::size_t(std::numeric_limits<label>::max()))apIndex=false;
     else {
      apSlots.assign(m.points.size(),-1);apFaces.resize(m.normalConeAPBirths.size());
      apCellSeen.assign(m.nCells,0);
      for(std::size_t i=0;i<m.normalConeAPBirths.size();++i){
       if((i&1023)==0&&!Q::workAvailable(work))return no("clock");
       const auto& birth=m.normalConeAPBirths[i];const label ap=birth.nativePoint;
       if(ap<0||std::size_t(ap)>=m.points.size()||birth.recordedSourceRoot<0
         ||birth.constructionGeneration!=m.normalConeAPConstructionGeneration)continue;
       if(apSlots[ap]!=-1){apSlots[ap]=-2;continue;}
       apSlots[ap]=label(i);apRoots[birth.recordedSourceRoot].push_back(label(i));
      }
     }
    }

   for(label c=0;c<m.nCells;++c){if((c&1023)==0&&!Q::workAvailable(work))return no("clock");
    if(!m.cellAlive[c]){if(!cf[c].empty())return no("grid-pair-dead-cell-incidence");continue;}
    for(label f:cf[c]){if(f<0||std::size_t(f)>=base.size()||!m.faceAlive[f])return no("grid-pair-current-CF");
     const unsigned char bit=m.owner[f]==c?1:(m.neighbour[f]==c?2:0);
     if(!bit||(incidence[f]&bit))return no("grid-pair-current-CF-duplicate");incidence[f]|=bit;}
   }
   for(label f=0;f<label(base.size());++f){if((f&1023)==0&&!Q::workAvailable(work))return no("clock");
    if(base[f]<-1||(base[f]>=0&&std::size_t(base[f])>=base.size()))return no("grid-pair-source-index");
    if(base[f]>=0){next[f]=head[base[f]];head[base[f]]=f;}
    if(!m.faceAlive[f]){if(incidence[f])return no("grid-pair-dead-face-incidence");continue;}
    if(m.neighbour[f]<0&&m.patch[f]>=0&&std::size_t(m.patch[f])<m.processorPatchPeers.size()){
     const label peer=m.processorPatchPeers[m.patch[f]];if(peer>=0&&peer!=rank)processorFaces[peer].push_back(f);}
    const label o=m.owner[f],n=m.neighbour[f];if(o<0||o>=m.nCells||!m.cellAlive[o]||n<-1||n==o
      ||(n>=0&&(n>=m.nCells||!m.cellAlive[n]))||incidence[f]!=(n>=0?3:1))return no("grid-pair-complete-incidence");
     // Same full native face walk: index every actual recorded-AP occurrence.
     // Current alive/endpoints/roles are still re-read on each proposal.
     if(apIndex)for(label id:m.faces[f]){
      if(id<0||std::size_t(id)>=apSlots.size()){apIndex=false;break;}
      const label slot=apSlots[id];if(slot<0)continue;
      Ring& found=apFaces[slot];if(found.empty()||found.back()!=f)found.push_back(f);
      if(m.cellRank[o]==rank)apCellSeen[o]=1;
      if(n>=0&&m.cellRank[n]==rank)apCellSeen[n]=1;
     }

   }
   if(apIndex)for(label c=0;c<m.nCells;++c){
    if((c&1023)==0&&!Q::workAvailable(work))return no("clock");
    if(apCellSeen[c])apCells.push_back(c);
   } // ascending native-cell traversal matches the original census order
   journal_.reserve(maximumCommits);if(!Q::workAvailable(work))return no("clock");
   mesh_=&m;base_=&base;cf_=&cf;head_.swap(head);next_.swap(next);faceCount_=m.faces.size();pointCount_=m.points.size();
   generatedFaceFloor_=normalGeneratedFaceFloor;positiveObservation_=observation;phaseMode_=phaseMode;readOnlyScope_=readOnlyScope;
   processorFacesByPeer_.swap(processorFaces);
    apBirthSlot_.swap(apSlots);apIncidence_.swap(apFaces);apBirthsByRoot_.swap(apRoots);
    apPotentialCells_.swap(apCells);
    apIncidenceReady_=apIndex;apIncidenceGeneration_=m.normalConeAPConstructionGeneration;
   pointStorage_=m.points.data();rank_=rank;lineageRevision_=m.completeCavityLineage.revision();ready_=true;return true;
  }catch(...){return no("grid-pair-phase-allocation-or-unknown");}
 }
public:
 // Original writable admission retains its nonzero publication capacity.
 bool begin(Mesh& m,const std::vector<label>& base,std::vector<std::vector<label>>& cf,label rank,
            const Q::Available& work,std::size_t maximumCommits=128,const char** why=nullptr,
            label normalGeneratedFaceFloor=-1,PositivePyramidObservations* observation=nullptr,Mode phaseMode=Mode::CartesianCrossGrid) {
  return beginScope(m,base,cf,rank,work,maximumCommits,why,normalGeneratedFaceFloor,observation,phaseMode,false);
 }
 // Exhausted local actor quota still permits complete real readonly endpoint
 // proof. This reserves zero publication records and grants no actor permission.
 bool beginReadOnlySupport(Mesh& m,const std::vector<label>& base,std::vector<std::vector<label>>& cf,label rank,
            const Q::Available& work,const char** why=nullptr,label normalGeneratedFaceFloor=-1,
            PositivePyramidObservations* observation=nullptr) {
  return beginScope(m,base,cf,rank,work,0,why,normalGeneratedFaceFloor,observation,Mode::SameOriginGeneratedSide,true);
 }
 // Potential producers only: every real proposal must find a recorded AP slot
 // in its bad child's actual shell. Full beginScope proved the source incidence;
 // this index removes only cells lacking that necessary occurrence. It is not
 // a current birth, root, Source, Point, Stock, or remote permission cache.
 const Ring* currentGeneratedAPCandidateCells(const Q::Available& work)const {
  return live(work)?&apPotentialCells_:nullptr;
 }
 const Prepared* lastCommitted()const noexcept{return journal_.empty()?nullptr:&journal_.back();}
 const std::vector<Prepared>& committedRecords()const noexcept{return journal_;}
 std::size_t committedCount()const noexcept{return journal_.size();}

 // Scheduling index only: actual AR debt with a held normal AP birth and
 // a current full-star source role. No projection, Point/source permission,
 // Stock proof or native mutation is produced by this query.
 bool hasGeneratedARSourceIndex(label cell,const Authority& authority,const Q::FaceOF& metric,
          const Q::CoupledTypePatch& coupled,const Q::Available& work)const {
  try {
   if(!live(work)||!metric||!coupled||!local(cell)||!mesh_->normalConeAPBirthsSealed
     ||!apIncidenceReady_||apIncidenceGeneration_!=mesh_->normalConeAPConstructionGeneration
     ||!authority.recordedParent||!authority.sourceSnapshot||!authority.sourceSnapshotIsRecorded)return false;
   const auto snapshot=authority.sourceSnapshot;
   if(!snapshot->complete||!snapshot->completedFluidOnlyPath||!authority.sourceSnapshotIsRecorded(snapshot.get())
     ||snapshot->rawSha256!=authority.sourceFingerprint.raw||snapshot->rolesDigest!=authority.sourceFingerprint.roles
     ||snapshot->materialKey!=authority.sourceFingerprint.materialKey||snapshot->materialDigest!=authority.sourceFingerprint.material
     ||snapshot->producerDigest!=authority.sourceFingerprint.producer||snapshot->boundaryMembershipDigest!=authority.sourceFingerprint.boundaryMembership
     ||generatedFaceFloor_<0||std::size_t(generatedFaceFloor_)<snapshot->faces.size()
     ||std::size_t(generatedFaceFloor_)>faceCount_)return false;
   std::vector<label> roots;if(!mesh_->completeCavityLineage.originsForCells({cell},roots)
     ||roots.size()!=1||!authority.recordedParent(roots.front()))return false;
   std::vector<Q::Face> shell;for(label f:cf_->at(cell))shell.push_back(face(f));
   const auto measured=Q::measure(cell,shell,mesh_->points,metric,coupled,work,false,false);
   if(!measured.finite||measured.aspect<=1000)return false;
   const label root=roots.front();std::set<label> slots;
   for(label f:cf_->at(cell))for(label id:mesh_->faces.at(f)){
    if(!Q::workAvailable(work)||id<0||std::size_t(id)>=apBirthSlot_.size())return false;
    if(apBirthSlot_[id]>=0)slots.insert(apBirthSlot_[id]);}
   for(label slot:slots){
    if(!Q::workAvailable(work)||slot<0||std::size_t(slot)>=mesh_->normalConeAPBirths.size()
      ||std::size_t(slot)>=apIncidence_.size())return false;
    const auto& birth=mesh_->normalConeAPBirths[slot];const label ap=birth.nativePoint;
    if(ap<0||std::size_t(ap)>=pointCount_||std::size_t(ap)>=apBirthSlot_.size()||apBirthSlot_[ap]!=slot
      ||birth.rank!=rank_||birth.recordedSourceRoot!=root||birth.constructionGeneration!=mesh_->normalConeAPConstructionGeneration
      ||birth.extractionEpochAtSeal!=uint64_t(mesh_->preparedBirth.extractionEpoch)||std::size_t(ap)<snapshot->points.size())continue;
    bool held=true;for(label axis=0;axis<3;++axis)held=held&&bits(mesh_->points[ap][axis])==birth.bornPointBits[axis];
    if(!held)continue;
    std::set<label> star;std::size_t count=0;bool eligible=true;
    for(label f:apIncidence_[slot]){
     if(!Q::workAvailable(work)||f<0||std::size_t(f)>=faceCount_)return false;
     if(!mesh_->faceAlive[f])continue;const auto row=face(f);
     if(std::find(row.ring.begin(),row.ring.end(),ap)==row.ring.end()||f<generatedFaceFloor_||base_->at(f)!=-1
       ||row.patch!=-1||row.zone!=-1||row.neighbour<0||!local(row.owner)||!local(row.neighbour)){eligible=false;break;}
     star.insert(row.owner);star.insert(row.neighbour);if(star.size()>128||++count>4096){eligible=false;break;}
    }
    if(!eligible||!star.count(cell))continue;
    for(label member:star){std::vector<label> memberRoots;
     if(!Q::workAvailable(work))return false;
     if(!mesh_->completeCavityLineage.originsForCells({member},memberRoots)||memberRoots.size()!=1||memberRoots.front()!=root)
       {eligible=false;break;}}
    if(!eligible)continue;
    for(label member:star)for(label f:cf_->at(member)){
     if(!Q::workAvailable(work))return false;
     bool composite=false;for(const auto& c:mesh_->physicalCompositeCarriers)if(!c||c->record().carrier==f)composite=true;
     if(composite)continue;
     const label source=base_->at(f);if(source<0||std::size_t(source)>=snapshot->faces.size())continue;
     const auto& original=snapshot->faces[source];const auto row=face(f);
     if(original.handle!=source||(original.owner!=root&&original.neighbour!=root)
       ||original.patch!=row.patch||original.zone!=row.zone||coupled(row.patch))continue;
     if(original.neighbour>=0&&row.neighbour>=0)return live(work);
     if(original.neighbour!=-1||row.neighbour!=-1||original.patch<0)continue;
     if(plainOriginal(source,authority,coupled))return live(work);
     if(original.zone<0||std::size_t(original.zone)>=mesh_->physicalWallPatchPairs.size())continue;
     const auto pair=mesh_->physicalWallPatchPairs[original.zone];
     if(pair[0]>=0&&pair[1]>=0&&pair[0]!=pair[1]&&(original.patch==pair[0]||original.patch==pair[1]))return live(work);
    }
   }
   return false;
  }catch(...){return false;} // Unknown index conservatively requests no reservation.
 }

 // Construction/lineage/current-incidence proposal only. No Point gate or write.
 bool proposeGeneratedAPTangentialCandidates(label badChild,const Authority& authority,
      const Q::FaceOF& metric,const Q::CoupledTypePatch& coupled,
      const Q::Available& work,std::vector<std::vector<GeneratedAPProjection>>& out,const char** why=nullptr) {
  const auto no=[&](const char* text){if(why)*why=text;out.clear();return false;};
  try {
   if(readOnlyScope_||!out.empty()||!live(work)||!metric||!coupled||!local(badChild)
     ||!mesh_->normalConeAPBirthsSealed||!mesh_->normalConeAPConstructionGeneration
      ||!apIncidenceReady_||apIncidenceGeneration_!=mesh_->normalConeAPConstructionGeneration)
    return no("ap-proposal-current-construction-or-clock");
   std::vector<label> roots;if(!mesh_->completeCavityLineage.originsForCells({badChild},roots)
     ||roots.size()!=1||!authority.recordedParent||!authority.recordedParent(roots.front()))
    return no("ap-proposal-single-recorded-origin");
   std::vector<Q::Face> badShell;badShell.reserve(cf_->at(badChild).size());
   for(label f:cf_->at(badChild))badShell.push_back(face(f));
   const auto debt=Q::measure(badChild,badShell,mesh_->points,metric,coupled,work,false,false);
   if(!debt.finite||(debt.determinant>=.001&&debt.aspect<=1000))return no("ap-proposal-no-actual-D-AR-debt");
   const label root=roots.front();const auto snapshot=authority.sourceSnapshot;
   if(!snapshot||!snapshot->complete||!snapshot->completedFluidOnlyPath
     ||!authority.sourceSnapshotIsRecorded||!authority.sourceSnapshotIsRecorded(snapshot.get()))
    return no("ap-proposal-recorded-source-snapshot");
   const auto& original=*snapshot;
   if(generatedFaceFloor_<0||std::size_t(generatedFaceFloor_)<original.faces.size()
     ||std::size_t(generatedFaceFloor_)>faceCount_)return no("ap-proposal-normal-generated-side-floor");
   if(original.rawSha256!=authority.sourceFingerprint.raw||original.rolesDigest!=authority.sourceFingerprint.roles
     ||original.materialKey!=authority.sourceFingerprint.materialKey||original.materialDigest!=authority.sourceFingerprint.material
     ||original.producerDigest!=authority.sourceFingerprint.producer||original.boundaryMembershipDigest!=authority.sourceFingerprint.boundaryMembership)
    return no("ap-proposal-current-source-fingerprint");
   std::set<label> candidates,badBirths;
    for(label f:cf_->at(badChild)){if(!Q::workAvailable(work))return no("clock");
     for(label id:mesh_->faces.at(f)){if(id<0||std::size_t(id)>=apBirthSlot_.size())return no("ap-proposal-point-index");
      const label slot=apBirthSlot_[id];if(slot>=0)badBirths.insert(slot);}}
    if(badBirths.empty())return no("ap-proposal-no-born-point-in-bad-child");
   // Proposal source scope equals the complete real AP star used by capture.
   // A failed cone may carry only an internal base while another child sharing
   // this same recorded AP carries the paired physical source. Read only the
   // maintained actual incidence; no observer or same-root tag substitutes it.
   for(label slot:badBirths){
    if(!Q::workAvailable(work))return no("clock");
    if(slot<0||std::size_t(slot)>=mesh_->normalConeAPBirths.size()||std::size_t(slot)>=apIncidence_.size())
      return no("ap-proposal-current-star-index");
    const auto& birth=mesh_->normalConeAPBirths[slot];const label ap=birth.nativePoint;
    if(ap<0||std::size_t(ap)>=mesh_->points.size()||std::size_t(ap)>=apBirthSlot_.size()
      ||apBirthSlot_[ap]!=slot||birth.rank!=rank_||birth.recordedSourceRoot!=root
      ||birth.constructionGeneration!=mesh_->normalConeAPConstructionGeneration
      ||birth.extractionEpochAtSeal!=uint64_t(mesh_->preparedBirth.extractionEpoch)
      ||std::size_t(ap)<original.points.size())continue;
    bool held=true;for(label axis=0;axis<3;++axis)held=held&&bits(mesh_->points[ap][axis])==birth.bornPointBits[axis];
    if(!held)continue;
    std::set<label> star;std::size_t faceCount=0;bool eligible=true;
    for(label f:apIncidence_[slot]){
     if(!Q::workAvailable(work))return no("clock");
     if(f<0||std::size_t(f)>=faceCount_)return no("ap-proposal-current-star-face-index");
     if(!mesh_->faceAlive[f])continue;
     if(std::find(mesh_->faces[f].begin(),mesh_->faces[f].end(),ap)==mesh_->faces[f].end())
       return no("ap-proposal-current-star-stale-incidence");
     const auto row=face(f);
     if(f<generatedFaceFloor_||base_->at(f)!=-1||row.patch!=-1||row.zone!=-1||row.neighbour<0
       ||!local(row.owner)||!local(row.neighbour)){eligible=false;break;}
     star.insert(row.owner);star.insert(row.neighbour);
     if(star.size()>128||++faceCount>4096){eligible=false;break;}
    }
    if(!eligible||!star.count(badChild))continue;
    for(label cell:star){
     std::vector<label> cellRoots;
     if(!Q::workAvailable(work))return no("clock");
     if(!mesh_->completeCavityLineage.originsForCells({cell},cellRoots)||cellRoots.size()!=1||cellRoots.front()!=root)
       {eligible=false;break;}
    }
    if(!eligible)continue;
    for(label cell:star)for(label f:cf_->at(cell)){
     if(!Q::workAvailable(work))return no("clock");
     for(const auto& c:mesh_->physicalCompositeCarriers)if(!c||c->record().carrier==f)
        return no("ap-proposal-composite-Source-context-unknown");
     const label source=base_->at(f);if(source<0||std::size_t(source)>=original.faces.size())continue;
     const auto& src=original.faces[source];const auto row=face(f);
     if(src.handle==source&&(src.owner==root||src.neighbour==root)
       &&src.patch==row.patch&&src.zone==row.zone&&!coupled(row.patch))candidates.insert(source);
     if(candidates.size()>32)return no("ap-proposal-source-candidate-bound");
    }
   }
   auto capture=[&](label expectedRoot,label source,label mate,bool requireBad,
                    GeneratedAPProjection& proposal)->bool {
    if(!authority.recordedParent(expectedRoot))return false;
    const auto& src=original.faces.at(source);
    if(src.handle!=source||(src.owner!=expectedRoot&&src.neighbour!=expectedRoot)||src.ring.size()<3||src.ring.size()>64)return false;
    Ring originalRing;originalRing.reserve(src.ring.size());
    for(const auto sourceID:src.ring){if(!Q::workAvailable(work)||sourceID<0
      ||uint64_t(sourceID)>uint64_t(std::numeric_limits<label>::max())
      ||std::size_t(sourceID)>=original.points.size()||std::size_t(sourceID)>=mesh_->points.size())return false;
     const label id=label(sourceID);const auto& q=original.points[id];
     if(!samePoint(point(q[0],q[1],q[2]),mesh_->points[id]))return false;originalRing.push_back(id);}
    X::Geometry exact(mesh_->points,work);X::Q sourceNormal;
    if(!exact.simplePlanar(originalRing,sourceNormal))return false;
    label foundPoint=-1;
     const auto rootBirths=apBirthsByRoot_.find(expectedRoot);
     if(rootBirths==apBirthsByRoot_.end())return false;
     for(label slot:rootBirths->second){
      if(!Q::workAvailable(work)||slot<0||std::size_t(slot)>=mesh_->normalConeAPBirths.size()
        ||std::size_t(slot)>=apIncidence_.size())return false;
      const auto& birth=mesh_->normalConeAPBirths[slot];
      if(birth.nativePoint<0||std::size_t(birth.nativePoint)>=apBirthSlot_.size()
        ||apBirthSlot_[birth.nativePoint]!=slot||(requireBad&&!badBirths.count(slot)))continue;
     if(birth.recordedSourceRoot!=expectedRoot||birth.rank!=rank_
       ||birth.constructionGeneration!=mesh_->normalConeAPConstructionGeneration
       ||birth.extractionEpochAtSeal!=uint64_t(mesh_->preparedBirth.extractionEpoch)
       ||birth.nativePoint<0||std::size_t(birth.nativePoint)>=mesh_->points.size()
       ||std::size_t(birth.nativePoint)<original.points.size())continue;
     const label ap=birth.nativePoint;bool bornHeld=true;
     for(label axis=0;axis<3;++axis)bornHeld=bornHeld&&bits(mesh_->points[ap][axis])==birth.bornPointBits[axis];
     // A prior point edit needs its real fresh binding; birth is never reactivated.
     if(!bornHeld)continue;
      std::set<label> star,apFaces;
      for(label f:apIncidence_[slot]){if(!Q::workAvailable(work))return false;
       if(f<0||std::size_t(f)>=faceCount_)return false;
       if(!mesh_->faceAlive[f])continue;
       if(std::find(mesh_->faces[f].begin(),mesh_->faces[f].end(),ap)==mesh_->faces[f].end())
        return false; // point-involving ring changes need a fresh phase index
      const auto row=face(f);
      if(f<generatedFaceFloor_||base_->at(f)!=-1||row.patch!=-1||row.zone!=-1||row.neighbour<0
        ||!local(row.owner)||!local(row.neighbour))return false;
      apFaces.insert(f);star.insert(row.owner);star.insert(row.neighbour);
      if(star.size()>128||apFaces.size()>4096)return false;
     }
     if(star.empty()||(requireBad&&!star.count(badChild)))continue;
     bool sameOrigin=true;for(label cell:star){std::vector<label> cellRoots;
      if(!Q::workAvailable(work)||!mesh_->completeCavityLineage.originsForCells({cell},cellRoots)
        ||cellRoots.size()!=1||cellRoots.front()!=expectedRoot){sameOrigin=false;break;}}
     if(!sameOrigin)continue;
     // Complete current source-ID/role index only. Exact surface coverage and
    // current paired reverse/coarsening proof still belong to real admission.
    bool cohortHeld=true;std::size_t pieces=0;
     for(label f=head_.at(source);f>=0;f=next_.at(f)){if(!Q::workAvailable(work))return false;if(!mesh_->faceAlive[f])continue;
      const auto row=face(f);if(base_->at(f)!=source||row.patch!=src.patch||row.zone!=src.zone||coupled(row.patch))
        {cohortHeld=false;break;}
       if(src.neighbour<0){if(row.neighbour!=-1||!star.count(row.owner)){cohortHeld=false;break;}}
       else if(row.neighbour<0||star.count(row.owner)==star.count(row.neighbour)){cohortHeld=false;break;}
      if(++pieces>512)return false;
     }
     if(!cohortHeld||!pieces)continue;
     if(foundPoint>=0)return false;foundPoint=ap;
     proposal.nativePoint=ap;proposal.constructorPart=birth.constructorPart;
     proposal.recordedRoot=expectedRoot;proposal.source=source;proposal.mateSource=mate;
     proposal.constructionGeneration=birth.constructionGeneration;
     proposal.lineageRevision=mesh_->completeCavityLineage.revision();proposal.before=mesh_->points[ap];
     proposal.completeCurrentStar.assign(star.begin(),star.end());
    }
    if(foundPoint<0)return false;
    metric(originalRing,proposal.sourceArea,proposal.sourceCentre);
    const scalar a2=magSqr(proposal.sourceArea);
    if(!Q::finitePoint(proposal.sourceArea)||!Q::finitePoint(proposal.sourceCentre)
      ||!std::isfinite(a2)||a2<=VSMALL)return false;
    const vector delta=proposal.sourceCentre-proposal.before;
    // Tangent first retains the normal coordinate literally on axis-aligned planes.
    const vector tangent=delta-proposal.sourceArea*((proposal.sourceArea&delta)/a2);
    proposal.target=proposal.before+tangent;
    // Exact coordinate-constant source support: preserve that native normal
    // coordinate even when floating cancellation leaves a tangential residual.
    for(label axis=0;axis<3;++axis){bool constant=true;
     for(label id:originalRing){if(!Q::workAvailable(work))return false;
      constant=constant&&bits(mesh_->points[id][axis])==bits(mesh_->points[originalRing.front()][axis]);}
     if(constant)proposal.target[axis]=proposal.before[axis];
    }
    return Q::finitePoint(proposal.target)&&Q::workAvailable(work);
   };
   for(label source:candidates){if(!Q::workAvailable(work))return no("clock");
    Prepared probe;probe.snapshot_=snapshot;probe.fingerprint_=authority.sourceFingerprint;
    const auto& sourceRow=original.faces[source];
     const bool internal=sourceRow.neighbour>=0&&sourceRow.patch==-1&&sourceRow.zone==-1;
     const bool plain=sourceRow.neighbour==-1&&plainOriginal(source,authority,coupled);
     if(internal||plain)
     {
      std::vector<GeneratedAPProjection> projections(1);
      if(!capture(root,source,-1,true,projections.front())||samePoint(projections.front().before,projections.front().target))continue;
      projections.front().sourceKind=internal?APSourceKind::RecordedInternal:APSourceKind::ProducerPlainWall;
      if(!live(work)||snapshot.get()!=authority.sourceSnapshot.get()||!sameFingerprint(probe.fingerprint_,authority.sourceFingerprint))
       return no("ap-proposal-stale-current-source");out.push_back(std::move(projections));continue;
     }
     // Only the actual paired producer creates the existing two-AP proposal.
     // Unknown material/BC/source classes remain refusals.
     label mate=-1;if(!pairedOriginal(probe,source,coupled,work,mate,why))continue;
     if(mate<0||std::size_t(mate)>=original.faces.size())continue;
     const auto mateOwner=original.faces[mate].owner;
    if(mateOwner<0||uint64_t(mateOwner)>uint64_t(std::numeric_limits<label>::max()))continue;
    const label mateRoot=label(mateOwner);
    if(mateRoot==root||!authority.recordedParent(mateRoot))continue;
    std::vector<GeneratedAPProjection> projections(2);
    if(!capture(root,source,mate,true,projections[0])
      ||!capture(mateRoot,mate,source,false,projections[1]))continue;
    if(projections[0].nativePoint==projections[1].nativePoint
      ||!std::any_of(projections.begin(),projections.end(),[](const GeneratedAPProjection& p){return !samePoint(p.before,p.target);}))continue;
    if(!live(work)||snapshot.get()!=authority.sourceSnapshot.get()
      ||!sameFingerprint(probe.fingerprint_,authority.sourceFingerprint))return no("ap-proposal-stale-current-source");
    out.push_back(std::move(projections));
   }
   if(out.empty())return no("ap-proposal-no-unique-known-source-birth");
   // Complete existing <=32-source enumeration precedes ordering. Source IDs
   // cannot suppress a distinct physical-source/target proposal. Actual paired
   // producers precede explicit plain walls, then recorded internal sources.
   // Tangent displacement and literal world geometry order each class; IDs
   // break only otherwise-identical geometry ties, never grant source roles.
   using Order=std::tuple<label,scalar,std::array<uint64_t,24>,std::array<label,6>>;
   std::vector<std::pair<Order,std::vector<GeneratedAPProjection>>> ordered;
   ordered.reserve(out.size());
   for(auto& candidate:out){
    if(!Q::workAvailable(work))return no("clock");
    std::array<uint64_t,24> geometry{};std::array<label,6> identities{};
    const label role=candidate.front().sourceKind==APSourceKind::PairedWall?0:
      candidate.front().sourceKind==APSourceKind::ProducerPlainWall?1:2;
    scalar displacement=0;std::size_t k=0,j=0;
    for(const auto& p:candidate){
     displacement+=magSqr(p.target-p.before);
     for(const point& value:{p.sourceCentre,p.sourceArea,p.before,p.target})
      for(label axis=0;axis<3;++axis)geometry[k++]=bits(value[axis]);
     identities[j++]=p.nativePoint;identities[j++]=p.source;identities[j++]=p.mateSource;
    }
    ordered.emplace_back(Order(role,std::isfinite(displacement)?-displacement:scalar(0),geometry,identities),std::move(candidate));
   }
   std::stable_sort(ordered.begin(),ordered.end(),[](const auto& a,const auto& b){return a.first<b.first;});
   out.clear();for(auto& entry:ordered)out.push_back(std::move(entry.second));
   if(!live(work)||snapshot.get()!=authority.sourceSnapshot.get())return no("ap-proposal-stale-current-source");
   return true;
  }catch(...){return no("ap-proposal-unknown-or-allocation");}
 }

 // Compatibility entry; the actual joint controller consumes all candidates.
 bool proposeGeneratedAPTangential(label badChild,const Authority& authority,
      const Q::FaceOF& metric,const Q::CoupledTypePatch& coupled,
      const Q::Available& work,std::vector<GeneratedAPProjection>& out,const char** why=nullptr) {
  if(!out.empty()){if(why)*why="ap-proposal-nonempty-output";return false;}
  std::vector<std::vector<GeneratedAPProjection>> candidates;
  if(!proposeGeneratedAPTangentialCandidates(badChild,authority,metric,coupled,work,candidates,why))return false;
  out=std::move(candidates.front());return true;
 }

  using ShadowFaceOF=std::function<void(const std::vector<point>&,const Ring&,vector&,point&)>;
  // Prepare only an uncommitted source-delta and fresh moved-CF view. Original
  // -> joint-final quality and remote proof are still mandatory before a lease.
  bool prepareGeneratedAPSourceShadow(const std::vector<GeneratedAPProjection>& proposals,
       const Authority& authority,const ShadowFaceOF& faceOF,const Q::CoupledTypePatch& coupled,
       const Q::Available& work,GeneratedAPShadow& out,const char** why=nullptr) {
   const auto no=[&](const char* text){if(why)*why=text;return false;};
   try {
    if(readOnlyScope_||jointPublicationActive_||out.measured_||!live(work)||!faceOF||!coupled||proposals.empty()||proposals.size()>8
      ||!mesh_->normalConeAPBirthsSealed||!apIncidenceReady_
      ||apIncidenceGeneration_!=mesh_->normalConeAPConstructionGeneration
      ||jointShadowNonce_==std::numeric_limits<uint64_t>::max())
     return no("ap-joint-shadow-scope-or-clock");
    if(!authority.sourceSnapshot||!authority.sourceSnapshotIsRecorded
      ||!authority.sourceSnapshotIsRecorded(authority.sourceSnapshot.get()))return no("ap-joint-recorded-snapshot");
    GeneratedAPShadow p;p.phase_=this;p.revision_=revision_;p.lineageRevision_=lineageRevision_;
    p.snapshot_=authority.sourceSnapshot;p.fingerprint_=authority.sourceFingerprint;p.projections_=proposals;
    std::set<label> points,cells;
    for(const auto& proposal:proposals){
     if(!Q::workAvailable(work)||proposal.nativePoint<0||std::size_t(proposal.nativePoint)>=pointCount_
       ||proposal.constructionGeneration!=mesh_->normalConeAPConstructionGeneration
       ||proposal.lineageRevision!=lineageRevision_||!samePoint(proposal.before,mesh_->points[proposal.nativePoint])
       ||!Q::finitePoint(proposal.target)
       ||(proposal.fraction!=1&&proposal.fraction!=.75&&proposal.fraction!=.5&&proposal.fraction!=.25)
       ||!points.insert(proposal.nativePoint).second)
      return no("ap-joint-stale-proposal");
     const label slot=apBirthSlot_.at(proposal.nativePoint);
     if(slot<0||std::size_t(slot)>=apIncidence_.size()||std::size_t(slot)>=mesh_->normalConeAPBirths.size())
      return no("ap-joint-actual-birth-index");
     const auto& birth=mesh_->normalConeAPBirths[slot];
     if(birth.nativePoint!=proposal.nativePoint||birth.constructorPart!=proposal.constructorPart
       ||birth.recordedSourceRoot!=proposal.recordedRoot||birth.rank!=rank_
       ||birth.constructionGeneration!=proposal.constructionGeneration
       ||birth.constructionGeneration!=mesh_->normalConeAPConstructionGeneration
       ||birth.extractionEpochAtSeal!=uint64_t(mesh_->preparedBirth.extractionEpoch))
      return no("ap-joint-actual-construction-binding");
     for(label axis=0;axis<3;++axis)if(bits(mesh_->points[proposal.nativePoint][axis])!=birth.bornPointBits[axis])
      return no("ap-joint-current-born-bits");
     if(proposal.source<0||std::size_t(proposal.source)>=p.snapshot_->faces.size())
      return no("ap-joint-original-source-range");
     const auto& original=p.snapshot_->faces[proposal.source];
     if(original.handle!=proposal.source||(original.owner!=proposal.recordedRoot&&original.neighbour!=proposal.recordedRoot)
        ||original.ring.size()<3||original.ring.size()>64)return no("ap-joint-original-source-owner");
     if(proposal.sourceKind==APSourceKind::RecordedInternal){
      if(original.neighbour<0||original.patch!=-1||original.zone!=-1||proposal.mateSource!=-1)
       return no("ap-joint-recorded-internal-role");
     }else if(proposal.sourceKind==APSourceKind::ProducerPlainWall){
      if(proposal.mateSource!=-1||!plainOriginal(proposal.source,authority,coupled))return no("ap-joint-actual-plainwall-role");
     }else if(proposal.sourceKind==APSourceKind::PairedWall){
      if(original.owner!=proposal.recordedRoot||original.neighbour!=-1)return no("ap-joint-paired-source-owner");
      Prepared probe;probe.snapshot_=p.snapshot_;probe.fingerprint_=p.fingerprint_;label mate=-1;
      if(!pairedOriginal(probe,proposal.source,coupled,work,mate,why)||mate!=proposal.mateSource)
       return no("ap-joint-current-original-pair-role");
     }else return no("ap-joint-unknown-source-kind");
     Ring ring;for(const auto id:original.ring){
      if(!Q::workAvailable(work)||id<0||std::size_t(id)>=p.snapshot_->points.size()
        ||std::size_t(id)>=mesh_->points.size())return no("ap-joint-original-source-points");
      const auto& x=p.snapshot_->points[id];
      if(!samePoint(point(x[0],x[1],x[2]),mesh_->points[id]))return no("ap-joint-source-bits");ring.push_back(id);
     }
     X::Geometry exact(mesh_->points,work);X::Q normal;
     if(!exact.simplePlanar(ring,normal))return no("ap-joint-full-source-planar");
     vector sourceSf;point sourceFc;faceOF(mesh_->points,ring,sourceSf,sourceFc);
     const scalar a2=magSqr(sourceSf);
     if(!Q::finitePoint(sourceSf)||!Q::finitePoint(sourceFc)||!std::isfinite(a2)||a2<=VSMALL)
      return no("ap-joint-original-source-metric");
     const vector delta=sourceFc-proposal.before;
     const vector tangent=delta-sourceSf*((sourceSf&delta)/a2);
     point actualTarget=proposal.before+tangent;
     for(label axis=0;axis<3;++axis){bool constant=true;for(label id:ring)
       constant=constant&&bits(mesh_->points[id][axis])==bits(mesh_->points[ring.front()][axis]);
      if(constant)actualTarget[axis]=proposal.before[axis];}
     if(proposal.fraction!=1){const point fullTarget=actualTarget;
      actualTarget=proposal.before+(fullTarget-proposal.before)*proposal.fraction;
      for(label axis=0;axis<3;++axis)if(bits(fullTarget[axis])==bits(proposal.before[axis]))actualTarget[axis]=proposal.before[axis];}
     if(!samePoint(actualTarget,proposal.target)||!samePoint(sourceSf,proposal.sourceArea)
       ||!samePoint(sourceFc,proposal.sourceCentre))return no("ap-joint-real-tangential-target");
     std::set<label> actual;
     for(label f:apIncidence_[slot]){
      if(!Q::workAvailable(work)||f<0||std::size_t(f)>=faceCount_)return no("clock");
      if(!mesh_->faceAlive[f])continue;const auto row=face(f);
      if(f<generatedFaceFloor_||base_->at(f)!=-1||row.patch!=-1||row.zone!=-1||row.neighbour<0
        ||!local(row.owner)||!local(row.neighbour)
        ||std::find(row.ring.begin(),row.ring.end(),proposal.nativePoint)==row.ring.end())
       return no("ap-joint-complete-normal-generated-star");
      actual.insert(row.owner);actual.insert(row.neighbour);
     }
     if(Ring(actual.begin(),actual.end())!=proposal.completeCurrentStar)return no("ap-joint-current-star-binding");
     for(label c:actual){std::vector<label> roots;
      if(!mesh_->completeCavityLineage.originsForCells({c},roots)||roots.size()!=1
        ||roots.front()!=proposal.recordedRoot)return no("ap-joint-current-recorded-root");cells.insert(c);}
     if(cells.size()>256)return no("ap-joint-complete-star-capacity");
     p.moves_.push_back({proposal.nativePoint,proposal.before,proposal.target});
    }
    if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(*mesh_,authority,
        Ring(cells.begin(),cells.end()),p.sourceOrigins_))return no("ap-joint-actual-source-permission");
    for(label c:cells){dcCartGeneratedAPSourceDelta::CellShell shell;shell.nativeCell=c;
     std::vector<label> roots;if(!mesh_->completeCavityLineage.originsForCells({c},roots)||roots.size()!=1)
      return no("ap-joint-source-root");shell.recordedRoot=roots.front();
     for(label f:cf_->at(c)){if(!Q::workAvailable(work))return no("clock");shell.faces.push_back(face(f));}
     p.star_.push_back(std::move(shell));
    }
    if(!jointShadowReady_){if(!Q::workAvailable(work))return no("clock");
     jointShadowPoints_=mesh_->points;jointShadowReady_=true;if(!Q::workAvailable(work))return no("clock");}
    // Reuse the one complete global-ID point layout. Only the previous/new
    // registered AP coordinates change in this private buffer, never mesh_.
    // Any write retires the preceding prepared view, including on allocation
    // failure while installing the next private move list.
    ++jointShadowNonce_;p.nonce_=jointShadowNonce_;
    for(const auto& move:jointShadowMoves_)jointShadowPoints_.at(move.nativePoint)=move.before;
    jointShadowMoves_=p.moves_;
    for(const auto& move:jointShadowMoves_)jointShadowPoints_.at(move.nativePoint)=move.target;
    if(!dcCartGeneratedAPSourceDelta::measure(mesh_->points,jointShadowPoints_,p.moves_,p.star_,work,p.sourceDelta_,why))
     return false;
    const Q::FaceOF movedMetric=[&](const Ring& ring,vector& sf,point& fc){faceOF(jointShadowPoints_,ring,sf,fc);};
    for(const auto& shell:p.star_){
     auto measured=Q::measure(shell.nativeCell,shell.faces,jointShadowPoints_,movedMetric,coupled,work,false,false);
     if(!measured.finite)return no(measured.reason?measured.reason:"ap-joint-fresh-moved-before");
     p.movedBeforeCells_.emplace(shell.nativeCell,std::move(measured));
    }
    if(!live(work)||!sameFingerprint(p.fingerprint_,authority.sourceFingerprint)
      ||p.snapshot_.get()!=authority.sourceSnapshot.get())return no("ap-joint-shadow-stale");
    p.measured_=true;out=std::move(p);return true;
   }catch(...){return no("ap-joint-shadow-unknown-or-allocation");}
  }
  const std::vector<point>* generatedAPShadowPoints(const GeneratedAPShadow& p)const noexcept {
   return p.measured_&&p.phase_==this&&p.revision_==revision_&&p.lineageRevision_==lineageRevision_
      &&p.nonce_==jointShadowNonce_&&jointShadowReady_?&jointShadowPoints_:nullptr;
  }


  // Generic all-component proposal over the true private moved-point view.
  // Source/Stock/peer/lease completion is separate and still required.
  bool prepareGeneratedAPJointGeometry(GeneratedAPShadow& p,const Authority& authority,
        const ShadowFaceOF& faceOF,const Q::CoupledTypePatch& coupled,const Q::Available& work,
        const char** why=nullptr,dcCartGeneratedAPJointComponentPlan::CostObservation* costs=nullptr,
        const Ring* requiredGeneratedFaces=nullptr,dcCartGeneratedAPJointComponentPlan::NFeedbackGeometryObservation* geometryObservation=nullptr) {
   const auto no=[&](const char* text){if(why)*why=text;return false;};
   try {
    if(p.geometryPlanned_||!generatedAPShadowPoints(p)||!live(work)||!faceOF||!coupled
      ||p.snapshot_.get()!=authority.sourceSnapshot.get()||!sameFingerprint(p.fingerprint_,authority.sourceFingerprint))
     return no("ap-joint-current-shadow-and-source");
    Ring star;for(const auto& shell:p.star_)star.push_back(shell.nativeCell);
    const Q::FaceOF originalMetric=[&](const Ring& ring,vector& sf,point& fc){faceOF(mesh_->points,ring,sf,fc);};
    const Q::FaceOF futureMetric=[&](const Ring& ring,vector& sf,point& fc){faceOF(jointShadowPoints_,ring,sf,fc);};
    const dcCartGeneratedAPJointComponentPlan::CurrentRoot root=[&](label cell,label& value){
     std::vector<label> roots; if(!Q::workAvailable(work)||!mesh_->completeCavityLineage.originsForCells({cell},roots)
       ||roots.size()!=1||!authority.recordedParent||!authority.recordedParent(roots.front()))return false;
     value=roots.front();return true;};
    dcCartGeneratedAPJointComponentPlan::Plan graph;
    if(!dcCartGeneratedAPJointComponentPlan::propose(*mesh_,*cf_,*base_,mesh_->points,jointShadowPoints_,
         star,rank_,generatedFaceFloor_,root,originalMetric,futureMetric,coupled,work,graph,why,costs,requiredGeneratedFaces,geometryObservation))return false;
    if(graph.components.empty()||graph.components.size()>128||graph.support.size()>1024
       ||graph.affected.size()>512||graph.afterFaces.size()>16384)return no("ap-joint-full-graph-capacity");
    std::vector<std::vector<Origin>> componentOrigins;
    std::map<label,Q::Cell> originalCells,finalCells;
    std::map<label,Ring> capturedCF;
    std::map<label,Q::Face> capturedFaces;
    std::map<label,label> capturedBases,capturedRanks;
    std::map<label,point> capturedPoints;
    std::set<label> selected,keepers;std::size_t references=0;
    for(const auto& group:graph.components){
     if(!Q::workAvailable(work)||group.members.size()<2||group.members.size()>128
       ||!keepers.insert(group.keeper).second||std::find(group.members.begin(),group.members.end(),group.keeper)==group.members.end())
      return no("ap-joint-independent-component-shape");
     references+=group.members.size();if(references>512)return no("ap-joint-complete-member-capacity");
     for(label c:group.members){label current=-1;if(!selected.insert(c).second||!root(c,current)||current!=group.root)
       return no("ap-joint-component-real-origin-and-disjoint");}
     std::vector<Origin> origins;if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(*mesh_,authority,group.members,origins)
       ||origins.size()!=1)return no("ap-joint-component-source-permission");
     componentOrigins.push_back(std::move(origins));
    }
    std::set<label> all=graph.support;all.insert(star.begin(),star.end());
    for(label c:all){
     if(!Q::workAvailable(work)||!local(c)||cf_->at(c).size()<4||cf_->at(c).size()>512)return no("ap-joint-complete-support-CF");
     capturedCF.emplace(c,cf_->at(c));capturedRanks.emplace(c,mesh_->cellRank.at(c));
     std::vector<Q::Face> originalShell;
     for(label f:cf_->at(c)){const auto row=face(f);originalShell.push_back(row);
      capturedFaces.emplace(f,row);capturedBases.emplace(f,base_->at(f));
      for(label id:row.ring)capturedPoints.emplace(id,mesh_->points.at(id));}
     if(capturedFaces.size()>16384||capturedPoints.size()>65536)return no("ap-joint-full-provider-inventory");
     auto original=Q::measure(c,originalShell,mesh_->points,originalMetric,coupled,work,false,false);
     if(!original.finite)return no("ap-joint-original-geometry");originalCells.emplace(c,std::move(original));
     const auto planned=graph.afterCF.find(c);const Ring& ids=planned==graph.afterCF.end()?cf_->at(c):planned->second;
     if(ids.empty())continue;
     std::vector<Q::Face> futureShell;for(label f:ids){
      if(graph.retired.count(f))return no("ap-joint-retired-future-incidence");
      const auto row=graph.afterFaces.find(f);futureShell.push_back(row==graph.afterFaces.end()?face(f):row->second);}
     const bool changedCore=keepers.count(c)||std::binary_search(star.begin(),star.end(),c);
     auto future=Q::measure(c,futureShell,jointShadowPoints_,futureMetric,coupled,work,true,changedCore);
     if(!future.finite||!future.strictGeometry)return no(future.reason?future.reason:"ap-joint-final-strict-geometry");
     const auto& oldCell=originalCells.at(c);
     if(keepers.count(c)){if(future.determinant<.001||future.aspect>1000)return no("ap-joint-final-host-D-AR");}
     else if(!inherited(oldCell.determinant,future.determinant,0)
       ||(oldCell.aspect<=1000?future.aspect>1000:future.aspect>oldCell.aspect*(1+1e-10)))
      return no("ap-joint-final-outside-D-AR-inheritance");
     finalCells.emplace(c,std::move(future));
    }
    // Actual complete original point values are the stale fence; a shadow may
    // differ only at the explicitly registered moves, never anywhere else.
    std::map<label,point> moved;for(const auto& move:p.moves_)moved.emplace(move.nativePoint,move.target);
    for(const auto& value:capturedPoints){const auto change=moved.find(value.first);
     const point& expected=change==moved.end()?value.second:change->second;
     if(!samePoint(expected,jointShadowPoints_.at(value.first)))return no("ap-joint-unregistered-shadow-point-change");}
    if(!live(work)||!generatedAPShadowPoints(p))return no("ap-joint-graph-stale-or-clock");
    p.componentOrigins_.swap(componentOrigins);p.originalCells_.swap(originalCells);p.finalCells_.swap(finalCells);
    p.capturedCF_.swap(capturedCF);p.capturedFaces_.swap(capturedFaces);p.capturedBases_.swap(capturedBases);
    p.capturedRanks_.swap(capturedRanks);p.capturedPoints_.swap(capturedPoints);
    p.graph_=std::move(graph);p.geometryPlanned_=true;return true;
   }catch(...){return no("ap-joint-graph-unknown-or-allocation");}
  }


  // Join VM uses the fresh moved geometry. Only the explicitly complete APstar
  // may change old individual-cell V/M; every other outside retains its old V/M.
  bool proveGeneratedAPJointVMAndRepresentation(GeneratedAPShadow& p,const Authority& authority,
        const ShadowFaceOF& faceOF,const Q::CoupledTypePatch& coupled,const Q::Available& work,
        const char** why=nullptr) {
   const auto no=[&](const char* text){if(why)*why=text;return false;};
   try {
    if(!p.geometryPlanned_||p.vmAndRepresentationProved_||!generatedAPShadowPoints(p)||!live(work)
      ||!faceOF||!coupled||p.snapshot_.get()!=authority.sourceSnapshot.get()
      ||!sameFingerprint(p.fingerprint_,authority.sourceFingerprint))return no("ap-joint-VM-current-scope");
    const Q::FaceOF metric=[&](const Ring& ring,vector& sf,point& fc){faceOF(jointShadowPoints_,ring,sf,fc);};
    X::Geometry exact(jointShadowPoints_,work);std::set<label> star,members;
    std::map<label,label> keeper;for(const auto& shell:p.star_)star.insert(shell.nativeCell);
    for(const auto& group:p.graph_.components)for(label c:group.members){members.insert(c);keeper.emplace(c,group.keeper);}
    const auto canonical=[&](label c){const auto found=keeper.find(c);return found==keeper.end()?c:found->second;};
    const auto originalRows=[&](label c){std::vector<Q::Face> rows;
     for(label f:p.capturedCF_.at(c))rows.push_back(p.capturedFaces_.at(f));return rows;};
    const auto finalRows=[&](label c){std::vector<Q::Face> rows;const auto planned=p.graph_.afterCF.find(c);
     const Ring& ids=planned==p.graph_.afterCF.end()?p.capturedCF_.at(c):planned->second;
     for(label f:ids){const auto changed=p.graph_.afterFaces.find(f);
      rows.push_back(changed==p.graph_.afterFaces.end()?p.capturedFaces_.at(f):changed->second);}return rows;};
    for(const auto& group:p.graph_.components){
     X::Moment expected,actual;scalar volume=0;point moment=point::zero;
     for(label c:group.members){if(!Q::workAvailable(work))return no("clock");
      const auto rows=originalRows(c);X::Moment value;if(!exact.moments(rows,c,value))return no("ap-joint-fresh-component-exact-shell");
      X::add(expected,value);
      const auto moved=Q::measure(c,rows,jointShadowPoints_,metric,coupled,work,false,false);
      if(!moved.finite)return no("ap-joint-fresh-component-OF-shell");volume+=moved.volume;moment+=moved.volume*moved.centre;
     }
     const auto rows=finalRows(group.keeper);
     if(!exact.moments(rows,group.keeper,actual)||!X::same(expected,actual))return no("ap-joint-fresh-component-exact6V24M");
     const auto& final=p.finalCells_.at(group.keeper);scalar span=1e-6;
     for(const auto& row:rows)for(label id:row.ring)span=max(span,mag(jointShadowPoints_.at(id)-final.centre));
     if(mag(final.volume-volume)/max(volume,VSMALL)>1e-10
       ||mag(final.volume*final.centre-moment)/max(volume*span,VSMALL)>1e-10)return no("ap-joint-fresh-component-OF-VM");
    }
    for(const auto& entry:p.finalCells_){const label c=entry.first;if(star.count(c)||members.count(c))continue;
     const auto& old=p.originalCells_.at(c);const auto& final=entry.second;scalar span=1e-6;
     for(const auto& row:finalRows(c))for(label id:row.ring)span=max(span,mag(jointShadowPoints_.at(id)-final.centre));
     if(mag(final.volume-old.volume)/max(old.volume,VSMALL)>1e-10
       ||mag(final.volume*final.centre-old.volume*old.centre)/max(old.volume*span,VSMALL)>1e-10)
      return no("ap-joint-non-AP-outside-old-OF-VM");
    }
    // Every merged outer bucket retains one real source, exact directed geometry
    // and identical actual final endpoint/patch/zone roles for all constituents.
    std::map<label,label> represented;std::set<label> changedSources;
    for(const auto& entry:p.graph_.constituents){const label f=entry.first;
     const auto target=p.graph_.afterFaces.find(f);if(target==p.graph_.afterFaces.end()||entry.second.empty())return no("ap-joint-carrier-row");
     const auto& row=target->second;I::E::Context context(work);I::Integrals sum;
     const auto at=[&](label id)->const point&{return jointShadowPoints_.at(id);};
     for(const auto& c:mesh_->physicalCompositeCarriers)if(!c||c->record().carrier==f)
        return no("ap-joint-composite-Source-context-unknown");
     const label source=base_->at(f);
     for(label oldFace:entry.second){if(!Q::workAvailable(work)||!represented.emplace(oldFace,f).second
       ||base_->at(oldFace)!=source)return no("ap-joint-real-single-source-bucket");
      const auto& old=p.capturedFaces_.at(oldFace);
      if(old.patch!=row.patch||old.zone!=row.zone)return no("ap-joint-source-patch-zone-held");
      label owner=canonical(old.owner),neighbour=old.neighbour<0?-1:canonical(old.neighbour);Ring ring=old.ring;
      if(owner!=row.owner){if(neighbour!=row.owner||owner!=row.neighbour)return no("ap-joint-real-outer-endpoints");
       std::swap(owner,neighbour);std::reverse(ring.begin()+1,ring.end());}
      if(owner!=row.owner||neighbour!=row.neighbour)return no("ap-joint-real-outside-held");
      I::add(sum,I::integrals(ring,at,context),context);
      if(source==-1){if(oldFace<generatedFaceFloor_||old.patch!=-1||old.zone!=-1||old.neighbour<0)
        return no("ap-joint-normal-generated-role");std::vector<label> a,b;
       if(!mesh_->completeCavityLineage.originsForCells({old.owner},a)
         ||!mesh_->completeCavityLineage.originsForCells({old.neighbour},b)||a.size()!=1||a!=b
         ||!authority.recordedParent(a.front()))return no("ap-joint-generated-single-real-origin");}
      else {
       if(source<0||std::size_t(source)>=p.snapshot_->faces.size())return no("ap-joint-original-source-range");
       const auto& origin=p.snapshot_->faces[source];std::vector<label> own,nei;
       if(origin.handle!=source||origin.patch!=old.patch||origin.zone!=old.zone
         ||!mesh_->completeCavityLineage.originsForCells({old.owner},own))return no("ap-joint-original-source-role");
       const auto contains=[](const std::vector<label>& values,label value){return std::find(values.begin(),values.end(),value)!=values.end();};
       if(origin.neighbour<0){if(old.neighbour!=-1||!contains(own,origin.owner))return no("ap-joint-boundary-source-owner");}
       else {
        if(old.neighbour<0||!mesh_->completeCavityLineage.originsForCells({old.neighbour},nei))return no("ap-joint-internal-source-neighbour");
        if(!(contains(own,origin.owner)&&contains(nei,origin.neighbour))
          &&!(contains(own,origin.neighbour)&&contains(nei,origin.owner)))return no("ap-joint-internal-source-endpoint-roots");
       }
      }
     }
     if(!context.good()||!I::same(sum,I::integrals(row.ring,at,context),context)||!context.good())
      return no("ap-joint-exact-surface12-6V24M");
     if(entry.second.size()>1&&source>=0)changedSources.insert(source);
    }
    const auto key=[&](const Ring& ring,bool reverse,std::vector<uint64_t>& out){
     if(ring.size()<3||ring.size()>512)return false;std::vector<std::array<uint64_t,3>> coords;
     for(std::size_t j=0;j<ring.size();++j){if(!Q::workAvailable(work))return false;
      const point& x=jointShadowPoints_.at(ring[reverse?(ring.size()-j)%ring.size():j]);coords.push_back({{bits(x.x()),bits(x.y()),bits(x.z())}});}
     std::size_t start=0;for(std::size_t j=1;j<coords.size();++j){bool less=false;
      for(std::size_t k=0;k<coords.size();++k){const auto& a=coords[(j+k)%coords.size()];const auto& b=coords[(start+k)%coords.size()];
       if(a<b){less=true;break;}if(b<a)break;}if(less)start=j;}
     out.clear();for(std::size_t j=0;j<coords.size();++j)for(uint64_t b:coords[(start+j)%coords.size()])out.push_back(b);return true;};
    // Verify the complete current producer-source surface, not merely the
    // merged bucket. No patch-wide geometric substitute creates source identity.
    for(label source:changedSources){if(!Q::workAvailable(work))return no("clock");
     const auto& origin=p.snapshot_->faces.at(source);Ring originalRing;
     for(const auto id:origin.ring){if(id<0||std::size_t(id)>=p.snapshot_->points.size()||std::size_t(id)>=pointCount_)
       return no("ap-joint-complete-source-original-point");const auto& x=p.snapshot_->points[id];
      if(!samePoint(point(x[0],x[1],x[2]),jointShadowPoints_.at(id)))return no("ap-joint-complete-source-original-bits");originalRing.push_back(id);}
     std::vector<Ring> rings;I::E::Context context(work);I::Integrals sum;
     const auto at=[&](label id)->const point&{return jointShadowPoints_.at(id);};
     for(label f=head_.at(source);f>=0;f=next_.at(f)){if(!Q::workAvailable(work))return no("clock");if(!mesh_->faceAlive.at(f))continue;
      const auto row=face(f);std::vector<label> own;if(base_->at(f)!=source||row.patch!=origin.patch||row.zone!=origin.zone
        ||!mesh_->completeCavityLineage.originsForCells({row.owner},own))return no("ap-joint-complete-source-current-role");
      Ring ring=row.ring;
      if(std::find(own.begin(),own.end(),origin.owner)==own.end()){
       if(origin.neighbour<0||std::find(own.begin(),own.end(),origin.neighbour)==own.end())return no("ap-joint-complete-source-current-owner");
       std::reverse(ring.begin()+1,ring.end());}
      rings.push_back(ring);I::add(sum,I::integrals(ring,at,context),context);if(rings.size()>512)return no("ap-joint-complete-source-cohort-capacity");
     }
     Ring outline;X::Q normal;
     if(rings.empty()||!exact.simplePlanar(originalRing,normal)||!exact.outline(rings,outline)
       ||!context.good()||!I::same(sum,I::integrals(originalRing,at,context),context)
       ||!I::same(I::integrals(outline,at,context),I::integrals(originalRing,at,context),context)||!context.good())
      return no("ap-joint-complete-original-source-surface-coverage");
    }
    std::set<std::pair<label,label>> pairs;
    for(label source:changedSources){const auto& original=p.snapshot_->faces.at(source);
     if(original.zone<0||original.neighbour>=0)continue;
     if(std::size_t(original.zone)<mesh_->physicalPlainWallRoles.size()
       &&mesh_->physicalPlainWallRoles[original.zone].known){
      const auto pair=std::size_t(original.zone)<mesh_->physicalWallPatchPairs.size()?mesh_->physicalWallPatchPairs[original.zone]:std::array<label,2>{{-1,-1}};
      if(pair[0]<0&&pair[1]<0)continue;
     }
     Prepared probe;probe.snapshot_=p.snapshot_;probe.fingerprint_=p.fingerprint_;label mate=-1;
     if(!pairedOriginal(probe,source,coupled,work,mate,why)||mate<0)return no("ap-joint-actual-paired-source-role");
     const auto pair=std::minmax(source,mate);if(!pairs.emplace(pair.first,pair.second).second)continue;
     std::map<std::vector<uint64_t>,label> sides[2];
     for(unsigned side=0;side<2;++side){const label src=side?pair.second:pair.first;const auto& origin=p.snapshot_->faces.at(src);
      for(label f=head_.at(src);f>=0;f=next_.at(f)){if(!Q::workAvailable(work))return no("clock");if(!mesh_->faceAlive.at(f))continue;
       const auto mapped=represented.find(f);const label carrier=mapped==represented.end()?f:mapped->second;
       if(carrier!=f)continue;if(p.graph_.retired.count(f))return no("ap-joint-physical-source-face-retired-without-carrier");
       const auto changed=p.graph_.afterFaces.find(f);const auto row=changed==p.graph_.afterFaces.end()?face(f):changed->second;
       if(row.neighbour!=-1||row.patch!=origin.patch||row.zone!=origin.zone||base_->at(f)!=src)
        return no("ap-joint-future-paired-role");
       std::vector<uint64_t> encoded;if(!key(row.ring,side==0,encoded)||!sides[side].emplace(std::move(encoded),f).second)
        return no("ap-joint-future-paired-ring-ambiguous");
      }
     }
     if(sides[0].empty()||sides[0].size()!=sides[1].size())return no("ap-joint-complete-future-paired-cohort");
     auto a=sides[0].begin(),b=sides[1].begin();for(;a!=sides[0].end();++a,++b)if(a->first!=b->first)
       return no("ap-joint-future-paired-ring-reverse");
    }
    if(!live(work)||!generatedAPShadowPoints(p))return no("ap-joint-VM-source-stale-or-clock");
    p.vmAndRepresentationProved_=true;return true;
   }catch(...){return no("ap-joint-VM-source-unknown-or-allocation");}
  }

 // Lookup must come from the complete actual MPI peer-shell exchange. Both
 // states are measured from their real current/staged full CF; no snapshot CC/V.
 using JointPeerLookup=std::function<bool(label,bool,Q::CoupledEndpoint&)>;
 bool generatedAPFresh(const GeneratedAPShadow& p,const Authority& authority,
                       const Q::Available& work,const char** why=nullptr)const {
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(!generatedAPShadowPoints(p)||!live(work)||p.snapshot_.get()!=authority.sourceSnapshot.get()
     ||!sameFingerprint(p.fingerprint_,authority.sourceFingerprint)
     ||!authority.sourceSnapshotIsRecorded||!authority.sourceSnapshotIsRecorded(p.snapshot_.get()))
    return no("ap-joint-stale-phase-source-or-clock");
   for(const auto& row:p.capturedCF_)if(!Q::workAvailable(work)||!local(row.first)
      ||mesh_->cellRank.at(row.first)!=p.capturedRanks_.at(row.first)||cf_->at(row.first)!=row.second)
    return no("ap-joint-stale-complete-CF");
   for(const auto& row:p.capturedFaces_)if(!Q::workAvailable(work)||!mesh_->faceAlive.at(row.first)
      ||base_->at(row.first)!=p.capturedBases_.at(row.first)||!sameFace(row.second,face(row.first)))
    return no("ap-joint-stale-face-source-role");
   for(const auto& value:p.capturedPoints_)if(!Q::workAvailable(work)||!samePoint(value.second,mesh_->points.at(value.first)))
    return no("ap-joint-stale-original-point-bits");
   if(!localFacetMetricSupportFresh(p.localMetricSupport_,*mesh_,*cf_,*base_,rank_,work))
    return no("ap-joint-stale-local-metric-support");
   return Q::workAvailable(work);
  }catch(...){return no("ap-joint-freshness-unknown");}
 }
 // Geometric feedback from an actual current first N failure, never permission.
 // All Source/component/VM/Stock/peer gates are rebuilt by a fresh next Shadow.
 // Existing branch values only:0 uncalled,1 accepted,2 structural,3 freshness,
 // 4 ordinary-face role/range,5 endpoints/current captured recipe,6 exception.
 struct NClosureCaptureObservation {
  label branch=0,face=-1;uint64_t phaseRevision=0;std::array<char,96> freshnessReason{};
 };
 static_assert(std::is_trivially_copyable<NClosureCaptureObservation>::value,"fixed observation");
 bool captureGeneratedAPNClosureFace(const GeneratedAPShadow& p,const Authority& authority,
      const Q::Available& work,label& out,uint64_t& phaseRevision,NClosureCaptureObservation* observation=nullptr) {
  if(!observation){
  try {
   if(out>=0||readOnlyScope_||p.phase_!=this||p.stockProved_||!p.vmAndRepresentationProved_
     ||p.finalStockNClosureFace_<0||!generatedAPFresh(p,authority,work))return false;
   const label f=p.finalStockNClosureFace_;
   if(f<generatedFaceFloor_||std::size_t(f)>=base_->size()||std::size_t(f)>=mesh_->faces.size()
     ||!mesh_->faceAlive.at(f)||base_->at(f)!=-1||mesh_->patch.at(f)!=-1||mesh_->zone.at(f)!=-1)return false;
   const label owner=mesh_->owner.at(f),neighbour=mesh_->neighbour.at(f);
   const auto old=p.capturedFaces_.find(f);
   if(!local(owner)||!local(neighbour)||old==p.capturedFaces_.end()
     ||old->second.owner!=owner||old->second.neighbour!=neighbour
     ||old->second.patch!=-1||old->second.zone!=-1||old->second.ring!=mesh_->faces.at(f))return false;
   out=f;phaseRevision=revision_;return true;
  }catch(...){return false;}
  }
  *observation=NClosureCaptureObservation();
  try {
   if(out>=0||readOnlyScope_||p.phase_!=this||p.stockProved_||!p.vmAndRepresentationProved_
     ||p.finalStockNClosureFace_<0){observation->branch=2;return false;}
   observation->face=p.finalStockNClosureFace_;
   const char* freshWhy="clock"; // existing final workAvailable false supplies no explicit why
   if(!generatedAPFresh(p,authority,work,&freshWhy)){
    observation->branch=3;if(freshWhy)std::strncpy(observation->freshnessReason.data(),freshWhy,observation->freshnessReason.size()-1);return false;}
   const label f=p.finalStockNClosureFace_;
   if(f<generatedFaceFloor_||std::size_t(f)>=base_->size()||std::size_t(f)>=mesh_->faces.size()
     ||!mesh_->faceAlive.at(f)||base_->at(f)!=-1||mesh_->patch.at(f)!=-1||mesh_->zone.at(f)!=-1){observation->branch=4;return false;}
   const label owner=mesh_->owner.at(f),neighbour=mesh_->neighbour.at(f);
   const auto old=p.capturedFaces_.find(f);
   if(!local(owner)||!local(neighbour)||old==p.capturedFaces_.end()
     ||old->second.owner!=owner||old->second.neighbour!=neighbour
     ||old->second.patch!=-1||old->second.zone!=-1||old->second.ring!=mesh_->faces.at(f)){observation->branch=5;return false;}
   out=f;phaseRevision=revision_;observation->phaseRevision=revision_;observation->branch=1;return true;
  }catch(...){observation->branch=6;return false;}
 }
 bool generatedAPNClosureScopeIsCurrent(const Phase* issued,uint64_t phaseRevision,uint64_t lineageRevision,
      uint64_t generation,const dcCartPostCarveFluidOwnershipPreview::Snapshot* snapshot,
      const dcCartCompleteCavityOriginCore::Fingerprint& fingerprint,const Authority& authority)const noexcept {
  try {return issued==this&&ready_&&!readOnlyScope_&&mesh_&&revision_==phaseRevision
     &&lineageRevision_==lineageRevision&&mesh_->normalConeAPConstructionGeneration==generation
     &&snapshot==authority.sourceSnapshot.get()&&sameFingerprint(fingerprint,authority.sourceFingerprint);}
  catch(...){return false;}
 }
 // Compare the unmodified live mesh directly with the jointly staged final
 // geometry. No Point-only intermediate is published or inherits Stock roles.
 bool proveGeneratedAPJointFinalStock(GeneratedAPShadow& p,const Authority& authority,
       const ShadowFaceOF& faceOF,const Q::CoupledTypePatch& coupled,const JointPeerLookup& peer,
       const Q::Available& work,const char** why=nullptr,bool observeFailure=false) {
  if(observeFailure)p.finalStockFailure_=GeneratedAPShadow::FinalStockFailureObservation();
  p.finalStockNClosureFace_=-1;
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(p.stockProved_||!p.vmAndRepresentationProved_||!faceOF||!coupled
      ||!generatedAPFresh(p,authority,work,why))return no("ap-joint-Stock-preconditions");
   const Q::FaceOF originalMetric=[&](const Ring& ring,vector& sf,point& fc){faceOF(mesh_->points,ring,sf,fc);};
   const Q::FaceOF futureMetric=[&](const Ring& ring,vector& sf,point& fc){faceOF(jointShadowPoints_,ring,sf,fc);};
   std::map<label,label> membership,represented;
   std::map<label,std::array<bool,4>> consumedPassing;
   for(const auto& group:p.graph_.components){consumedPassing.emplace(group.keeper,std::array<bool,4>{{false,false,false,false}});
    for(label c:group.members)if(!membership.emplace(c,group.keeper).second)return no("ap-joint-Stock-overlap");}
   const auto canonical=[&](label c){const auto i=membership.find(c);return i==membership.end()?c:i->second;};
   for(const auto& entry:p.graph_.constituents)for(label f:entry.second)
    if(!represented.emplace(f,entry.first).second)return no("ap-joint-Stock-carrier-duplicate");
   std::set<label> selected=p.graph_.affected,facets;
   for(const auto& shell:p.star_)selected.insert(shell.nativeCell);
   for(label c:selected){const auto captured=p.capturedCF_.find(c);if(captured==p.capturedCF_.end())return no("ap-joint-Stock-complete-original-CF");
    facets.insert(captured->second.begin(),captured->second.end());}
   if(facets.size()>16384)return no("ap-joint-Stock-facet-capacity");
   // Only local endpoints of the actual selected old/final Stock facets are
   // required. Do not recursively expand their neighbours or promote them to
   // graph/source/point/processor/publication scope.
   std::set<label> metricRequired,metricForbidden=selected;
   for(const auto& entry:p.graph_.afterCF)metricForbidden.insert(entry.first);
   const auto requireEndpoint=[&](label c,bool future)->bool {
    if(c<0||!local(c))return false;
    const auto& cells=future?p.finalCells_:p.originalCells_;const auto measured=cells.find(c);
    if(measured!=cells.end())return measured->second.finite;
    metricRequired.insert(c);return true;};
   for(label f:facets){
    if(!Q::workAvailable(work))return no("clock");
    const auto old=p.capturedFaces_.find(f);if(old==p.capturedFaces_.end())return no("ap-joint-Stock-original-face");
    if(!requireEndpoint(old->second.owner,false)
      ||(old->second.neighbour>=0&&!requireEndpoint(old->second.neighbour,false)))
      return no("ap-joint-before-local-metric-endpoint");
    const label owner=canonical(old->second.owner),neighbour=old->second.neighbour<0?-1:canonical(old->second.neighbour);
    if(neighbour>=0&&owner==neighbour)continue;
    const auto mapped=represented.find(f);const label carrier=mapped==represented.end()?f:mapped->second;
    const auto changed=p.graph_.afterFaces.find(carrier);const auto captured=p.capturedFaces_.find(carrier);
    if(changed==p.graph_.afterFaces.end()&&captured==p.capturedFaces_.end())return no("ap-joint-Stock-final-carrier");
    const auto& row=changed==p.graph_.afterFaces.end()?captured->second:changed->second;
    if(!requireEndpoint(row.owner,true)||(row.neighbour>=0&&!requireEndpoint(row.neighbour,true)))
      return no("ap-joint-final-local-metric-endpoint");
   }
   LocalFacetMetricSupport metricSupport;
   if(!captureLocalFacetMetricSupport(*mesh_,*cf_,*base_,jointShadowPoints_,rank_,metricRequired,metricForbidden,
        p.graph_.afterFaces,p.graph_.retired,p.capturedCF_,p.capturedFaces_,p.capturedPoints_,
        originalMetric,futureMetric,coupled,work,metricSupport,why))return false;
   std::map<label,Q::FacetQuality> beforeFacets,afterFacets;
   label observedMeasureBranch=0; // assigned only at the original entered branch
   const auto measure=[&](const Q::Face& row,bool future,Q::FacetQuality& result)->bool {
    if(observeFailure)observedMeasureBranch=0;
    const auto& cells=future?p.finalCells_:p.originalCells_;
    const auto& extra=future?metricSupport.final:metricSupport.before;
    const auto cellAt=[&](label c)->const Q::Cell* {const auto known=cells.find(c);
     if(known!=cells.end())return &known->second;const auto measured=extra.find(c);return measured==extra.end()?nullptr:&measured->second;};
    const Q::Cell* owner=cellAt(row.owner);
    if(!owner||!owner->finite){result.reason=future?"ap-joint-final-local-owner-metric-missing":"ap-joint-before-local-owner-metric-missing";return false;}
    const auto& pts=future?jointShadowPoints_:mesh_->points;
    const auto& metric=future?futureMetric:originalMetric;
    if(row.neighbour<0&&coupled(row.patch)){
     if(observeFailure)observedMeasureBranch=3;
     Q::CoupledEndpoint endpoint;if(!peer||!peer(row.id,future,endpoint)){
      result.reason=future?"ap-joint-final-real-peer-missing":"ap-joint-before-real-peer-missing";return false;}
     result=Q::coupledFacet(row,pts,metric,owner->centre,owner->volume,endpoint,work);
    }else{
     if(observeFailure)observedMeasureBranch=row.neighbour>=0?1:2;
     const Q::Cell* neighbour=nullptr;if(row.neighbour>=0){neighbour=cellAt(row.neighbour);
      if(!neighbour||!neighbour->finite){result.reason=future?"ap-joint-final-local-neighbour-metric-missing":"ap-joint-before-local-neighbour-metric-missing";return false;}}
     result=Q::facet(row,pts,metric,*owner,neighbour,work);
    }
    return Q::workAvailable(work);
   };
   for(label f:facets){if(!Q::workAvailable(work))return no("clock");
    const auto old=p.capturedFaces_.find(f);if(old==p.capturedFaces_.end())return no("ap-joint-Stock-original-face");
    Q::FacetQuality beforeQuality;if(!measure(old->second,false,beforeQuality)||!beforeQuality.numeric)
     return no(beforeQuality.reason?beforeQuality.reason:"ap-joint-real-before-peer-or-facet");
    const label beforeBranch=observeFailure?observedMeasureBranch:0;
    beforeFacets.emplace(f,beforeQuality);
    const label owner=canonical(old->second.owner),neighbour=old->second.neighbour<0?-1:canonical(old->second.neighbour);
    if(neighbour>=0&&owner==neighbour){const auto passing=consumedPassing.find(owner);
     if(passing==consumedPassing.end()||!p.graph_.retired.count(f))return no("ap-joint-consumed-real-separator");
     for(unsigned k=1;k<5;++k)passing->second[k-1]=passing->second[k-1]||passes(beforeQuality.v[k],k);
     continue;
    }
    const auto mapped=represented.find(f);const label carrier=mapped==represented.end()?f:mapped->second;
    const auto changed=p.graph_.afterFaces.find(carrier);
    const auto row=changed==p.graph_.afterFaces.end()?p.capturedFaces_.at(carrier):changed->second;
    if(p.graph_.retired.count(carrier))return no("ap-joint-Stock-retired-carrier");
    Q::FacetQuality finalQuality;const auto cached=afterFacets.find(carrier);
    label finalBranch=0; // a cached value has no new branch observation
    if(cached==afterFacets.end()){
     if(!measure(row,true,finalQuality)||!finalQuality.numeric||!finalQuality.valid)
      return no(finalQuality.reason?finalQuality.reason:"ap-joint-real-final-peer-or-Stock");
     if(observeFailure)finalBranch=observedMeasureBranch;
     afterFacets.emplace(carrier,finalQuality);
     vector sf;point fc;futureMetric(row.ring,sf,fc);const vector normal=sf/(mag(sf)+ROOTVSMALL);
     const scalar maxSin=std::sin(80*constant::mathematical::pi/180);
     vector previous=jointShadowPoints_.at(row.ring.front())-jointShadowPoints_.at(row.ring.back());
     scalar previousLength=mag(previous);previous/=previousLength+ROOTVSMALL;
     for(std::size_t j=0;j<row.ring.size();++j){if(!Q::workAvailable(work))return no("clock");
      vector edge=jointShadowPoints_.at(row.ring[(j+1)%row.ring.size()])-jointShadowPoints_.at(row.ring[j]);
      scalar length=mag(edge);edge/=length+ROOTVSMALL;
      if(previousLength>SMALL&&length>SMALL){vector turn=previous^edge;const scalar sine=mag(turn);
       if(sine>=maxSin){turn/=sine;if((turn&normal)<SMALL)return no("ap-joint-final-face-angle");}}
      previous=edge;previousLength=length;
     }
    }else finalQuality=cached->second;
    for(unsigned k=1;k<5;++k)if(!inherited(beforeQuality.v[k],finalQuality.v[k],k))
    {
     if(k==4)p.finalStockNClosureFace_=f;
     if(observeFailure&&!p.finalStockFailure_.observed){
      auto& o=p.finalStockFailure_;o.face=f;o.carrier=carrier;o.slot=label(k);
      o.beforeOwner=old->second.owner;o.beforeNeighbour=old->second.neighbour;
      o.beforePatch=old->second.patch;o.beforeZone=old->second.zone;
      o.finalOwner=row.owner;o.finalNeighbour=row.neighbour;o.finalPatch=row.patch;o.finalZone=row.zone;
      o.beforeNumeric=beforeQuality.numeric;o.beforeValid=beforeQuality.valid;
      o.finalNumeric=finalQuality.numeric;o.finalValid=finalQuality.valid;
      o.beforeBranch=beforeBranch;o.finalBranch=finalBranch;o.finalCached=cached!=afterFacets.end();
      for(unsigned j=0;j<5;++j){o.beforeQualityBits[j]=bits(beforeQuality.v[j]);o.finalQualityBits[j]=bits(finalQuality.v[j]);}
      o.observed=true;
     }
     return no("ap-joint-original-to-final-W-R-S-N-inheritance");
    }
   }
   for(const auto& group:p.graph_.components){const auto ids=p.graph_.afterCF.find(group.keeper);
    if(ids==p.graph_.afterCF.end()||ids->second.empty())return no("ap-joint-final-complete-host-CF");
    const auto& passing=consumedPassing.at(group.keeper);
    for(label f:ids->second){const auto q=afterFacets.find(f);if(q==afterFacets.end())return no("ap-joint-host-facet-role-coverage");
     for(unsigned k=1;k<5;++k)if(passing[k-1]&&!passes(q->second.v[k],k))
      return no("ap-joint-consumed-separator-role-inheritance");}
   }
   p.localMetricSupport_=std::move(metricSupport);
   if(!generatedAPFresh(p,authority,work,why))return false;
   p.originalFacets_.swap(beforeFacets);p.finalFacets_.swap(afterFacets);p.stockProved_=true;return true;
  }catch(...){return no("ap-joint-final-Stock-unknown-or-allocation");}
 }

 // Fixed-wave readonly participants capture their real live endpoint CF.
 // Their final processor quality is checked against the actual peer result.
 bool beginGeneratedAPReadOnlySupport(const Q::Available& work) {
  if(jointPublicationActive_||!live(work))return false;
  readOnlyCF_.clear();readOnlyFaces_.clear();readOnlyBases_.clear();readOnlyRanks_.clear();
  readOnlyPoints_.clear();readOnlyOrigins_.clear();readOnlyProcessorFaces_.clear();readOnlyStockProved_=false;readOnlyRevision_=revision_;readOnlyLineageRevision_=lineageRevision_;
  return Q::workAvailable(work);
 }
 bool captureGeneratedAPReadOnlyCell(label c,const Authority& authority,const Q::Available& work,
                                   const char** why=nullptr) {
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(jointPublicationActive_||!live(work)||readOnlyRevision_!=revision_
      ||readOnlyLineageRevision_!=lineageRevision_||!local(c))return no("ap-peer-readonly-current-cell");
   if(readOnlyCF_.count(c))return true;
   if(readOnlyCF_.size()>=256||cf_->at(c).size()<4||cf_->at(c).size()>512)return no("ap-peer-readonly-complete-CF-capacity");
   std::vector<Origin> origins;if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(*mesh_,authority,{c},origins))
    return no("ap-peer-readonly-real-source-permission");
   std::map<label,Q::Face> faces;std::map<label,label> bases;std::map<label,point> points;
   for(label f:cf_->at(c)){if(!Q::workAvailable(work)||!mesh_->faceAlive.at(f))return no("clock");
    const auto row=face(f);if(row.owner!=c&&row.neighbour!=c)return no("ap-peer-readonly-CF-incidence");
    faces.emplace(f,row);bases.emplace(f,base_->at(f));for(label id:row.ring)points.emplace(id,mesh_->points.at(id));}
   if(readOnlyFaces_.size()+faces.size()>16384||readOnlyPoints_.size()+points.size()>65536)
    return no("ap-peer-readonly-full-inventory-capacity");
   readOnlyCF_.emplace(c,cf_->at(c));readOnlyRanks_.emplace(c,mesh_->cellRank.at(c));readOnlyOrigins_.emplace(c,std::move(origins));
   readOnlyFaces_.insert(faces.begin(),faces.end());readOnlyBases_.insert(bases.begin(),bases.end());
   readOnlyPoints_.insert(points.begin(),points.end());return Q::workAvailable(work);
  }catch(...){return no("ap-peer-readonly-capture-unknown");}
 }
 bool generatedAPReadOnlyFresh(const Q::Available& work)const {
  try {
   if(!live(work)||readOnlyRevision_!=revision_||readOnlyLineageRevision_!=lineageRevision_)return false;
   for(const auto& row:readOnlyCF_)if(!Q::workAvailable(work)||!local(row.first)
      ||mesh_->cellRank.at(row.first)!=readOnlyRanks_.at(row.first)||cf_->at(row.first)!=row.second)return false;
   for(const auto& row:readOnlyFaces_)if(!Q::workAvailable(work)||!mesh_->faceAlive.at(row.first)
      ||base_->at(row.first)!=readOnlyBases_.at(row.first)||!sameFace(face(row.first),row.second))return false;
   for(const auto& value:readOnlyPoints_)if(!Q::workAvailable(work)||!samePoint(value.second,mesh_->points.at(value.first)))return false;
   return true;
  }catch(...){return false;}
 }
 bool processorOriginalWorldRing(label f,const Authority& authority,const Q::Available& work,E::WorldRing& ring)const {
  if(!live(work)||f<0||std::size_t(f)>=faceCount_||!mesh_->faceAlive.at(f)||mesh_->neighbour.at(f)!=-1
     ||mesh_->patch.at(f)<0||std::size_t(mesh_->patch.at(f))>=mesh_->processorPatchPeers.size()
     ||mesh_->processorPatchPeers[mesh_->patch[f]]<0||!authority.sourceSnapshot
     ||!authority.sourceSnapshotIsRecorded||!authority.sourceSnapshotIsRecorded(authority.sourceSnapshot.get()))return false;
  const label src=base_->at(f);const auto& snapshot=*authority.sourceSnapshot;
  if(src<0||std::size_t(src)>=snapshot.faces.size())return false;const auto& original=snapshot.faces[src];
  if(original.handle!=src||original.ring.size()<3||original.ring.size()>4096)return false;
  std::vector<label> roots;if(!mesh_->completeCavityLineage.originsForCells({mesh_->owner[f]},roots))return false;
  const bool own=std::find(roots.begin(),roots.end(),original.owner)!=roots.end();
  const bool nei=original.neighbour>=0&&std::find(roots.begin(),roots.end(),original.neighbour)!=roots.end();
  if(own==nei)return false;std::vector<Origin> permissionRows;
  if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(*mesh_,authority,{mesh_->owner[f]},permissionRows))return false;
  ring.clear();for(std::size_t j=0;j<original.ring.size();++j){if(!Q::workAvailable(work))return false;
   const auto id=original.ring[own?j:(original.ring.size()-j)%original.ring.size()];
   if(id<0||std::size_t(id)>=snapshot.points.size()||std::size_t(id)>=pointCount_)return false;
   const auto& x=snapshot.points[id];const point p(x[0],x[1],x[2]);
   if(!samePoint(p,mesh_->points[id]))return false;ring.push_back(p);}
  return Q::workAvailable(work);
 }
 bool processorCellShell(const GeneratedAPShadow* shadow,label c,bool future,const Q::CoupledTypePatch& coupled,
          const Q::Available& work,E::CellShell& out)const {
  if(!live(work)||!local(c)||!coupled)return false;
  const auto& points=shadow&&future?jointShadowPoints_:mesh_->points;
  const Ring* ids=&cf_->at(c);if(shadow&&future){const auto found=shadow->graph_.afterCF.find(c);
   if(found!=shadow->graph_.afterCF.end())ids=&found->second;}
  if(ids->size()<4||ids->size()>512)return false;E::CellShell shell;shell.owner=c;std::set<label> vertices,faces;
  for(label f:*ids){if(!Q::workAvailable(work)||!faces.insert(f).second)return false;
   auto row=face(f);if(shadow&&future){if(shadow->graph_.retired.count(f))return false;
    const auto changed=shadow->graph_.afterFaces.find(f);if(changed!=shadow->graph_.afterFaces.end())row=changed->second;}
   if(row.owner!=c&&row.neighbour!=c)return false;shell.faces.push_back({row,coupled(row.patch)});
   vertices.insert(row.ring.begin(),row.ring.end());}
  if(vertices.size()>65536)return false;for(label id:vertices){if(!Q::workAvailable(work)||id<0||std::size_t(id)>=points.size())return false;
   shell.points.push_back({id,points[id]});}
  out=std::move(shell);return true;
 }
 bool buildGeneratedAPProcessorRequests(const GeneratedAPShadow& p,const Authority& authority,
          const Q::CoupledTypePatch& coupled,const Q::Available& work,std::vector<E::Request>& out,const char** why=nullptr)const {
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(!out.empty()||!p.vmAndRepresentationProved_||!generatedAPFresh(p,authority,work,why))return no("ap-peer-request-fresh-source");
   std::vector<E::Request> requests;
   for(const auto& entry:p.capturedFaces_){const auto& row=entry.second;if(row.neighbour>=0||!coupled(row.patch))continue;
    if(row.patch<0||std::size_t(row.patch)>=mesh_->processorPatchPeers.size())return no("ap-peer-actual-processor-carrier");
    const label peer=mesh_->processorPatchPeers[row.patch];if(peer<0||peer==rank_)return no("ap-peer-unknown-or-cyclic-role");
    const auto changed=p.graph_.afterFaces.find(row.id);const auto final=changed==p.graph_.afterFaces.end()?row:changed->second;
    // This variant preserves the real processor representation literally. Owner
    // CF/CC/V may change, but no unknown cross-rank ring restage is authorized.
    if(p.graph_.retired.count(row.id)||final.id!=row.id||final.neighbour!=-1||final.patch!=row.patch
       ||final.zone!=row.zone||final.ring!=row.ring)return no("ap-peer-processor-ring-representation-change");
    E::Request request;request.senderRank=rank_;request.peerRank=peer;request.nativeFace=row.id;
    request.sourceHandle=base_->at(row.id);request.finalNativeFace=final.id;request.beforeOwner=row.owner;request.finalOwner=final.owner;
    if(!processorOriginalWorldRing(row.id,authority,work,request.originalRing))return no("ap-peer-real-original-source-ring");
    for(label id:row.ring){if(!samePoint(mesh_->points.at(id),jointShadowPoints_.at(id)))return no("ap-peer-processor-point-bits-change");
     request.currentRing.push_back(mesh_->points[id]);request.futureRing.push_back(jointShadowPoints_[id]);}
    if(!processorCellShell(&p,row.owner,false,coupled,work,request.before)
       ||!processorCellShell(&p,final.owner,true,coupled,work,request.final))return no("ap-peer-real-request-complete-CF");
    requests.push_back(std::move(request));if(requests.size()>128)return no("ap-peer-complete-request-capacity");
   }
   out.swap(requests);return generatedAPFresh(p,authority,work,why);
  }catch(...){return no("ap-peer-request-unknown-or-allocation");}
 }
 bool resolveGeneratedAPProcessorRequest(const E::Request& request,const GeneratedAPShadow* shadow,
          const Authority& authority,const Q::CoupledTypePatch& coupled,const Q::Available& work,E::Offer& out,bool metricOnly=false) {
  try {
   if(jointPublicationActive_||!live(work)||request.peerRank!=rank_||request.senderRank==rank_
      ||!generatedAPReadOnlyFresh(work)||(shadow&&!generatedAPFresh(*shadow,authority,work)))return false;
   const auto candidates=processorFacesByPeer_.find(request.senderRank);if(candidates==processorFacesByPeer_.end())return false;
   const auto reverse=[&](const Ring& ring,const std::vector<point>& points,const E::WorldRing& world){
    if(ring.size()!=world.size()||ring.empty())return false;std::size_t count=0;
    for(std::size_t start=0;start<ring.size();++start){if(!samePoint(points.at(ring[start]),world.front()))continue;
     bool same=true;for(std::size_t j=1;j<ring.size();++j)if(!samePoint(points.at(ring[(start+ring.size()-j)%ring.size()]),world[j])){same=false;break;}
     if(same&&++count>1)return false;}return count==1;};
   label match=-1;Q::Face future;
   for(label f:candidates->second){if(!Q::workAvailable(work)||!mesh_->faceAlive.at(f))continue;
    const auto row=face(f);if(row.neighbour!=-1||!coupled(row.patch)||!reverse(row.ring,mesh_->points,request.currentRing))continue;
    auto final=row;if(shadow){if(shadow->graph_.retired.count(f))return false;const auto changed=shadow->graph_.afterFaces.find(f);
     if(changed!=shadow->graph_.afterFaces.end())final=changed->second;}
    if(final.id!=f||final.neighbour!=-1||final.patch!=row.patch||final.zone!=row.zone||final.ring!=row.ring)return false;
    if(!reverse(final.ring,shadow?jointShadowPoints_:mesh_->points,request.futureRing))continue;
    E::WorldRing source;if(!processorOriginalWorldRing(f,authority,work,source)||!E::detail::uniqueReverse(request.originalRing,source))continue;
    if(match>=0)return false;match=f;future=std::move(final);
   }
   if(match<0)return false;const auto row=face(match);
   if(!metricOnly&&!captureGeneratedAPReadOnlyCell(row.owner,authority,work))return false;
   E::Offer offer;offer.senderRank=rank_;offer.actualPeerRank=request.senderRank;offer.requesterNativeFace=request.nativeFace;
   offer.nativeFace=match;offer.finalNativeFace=future.id;offer.sourceHandle=base_->at(match);
   if(!processorOriginalWorldRing(match,authority,work,offer.originalRing))return false;
   for(label id:row.ring)offer.currentRing.push_back(mesh_->points.at(id));
   for(label id:future.ring)offer.futureRing.push_back((shadow?jointShadowPoints_:mesh_->points).at(id));
   if(!processorCellShell(shadow,row.owner,false,coupled,work,offer.before)
      ||!processorCellShell(shadow,future.owner,true,coupled,work,offer.final))return false;
   if(!metricOnly)readOnlyProcessorFaces_.insert(match);out=std::move(offer);return true;
  }catch(...){return false;}
 }
 // Extra geometry metrics of PRIMARY affected/full-CF scope only.
 // Resolving a metric endpoint does not recursively add its owner to this scope.
 bool buildGeneratedAPMetricDiscovery(const E::Result& primary,const GeneratedAPShadow* shadow,
      const Authority& authority,const Q::CoupledTypePatch& coupled,const Q::Available& work,
      std::vector<E::Request>& out,std::vector<label>& required,const char** why=nullptr) {
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(!out.empty()||!required.empty()||!primary.complete||!primary.realMPIEstablished()||!coupled
      ||!generatedAPReadOnlyFresh(work)||(shadow&&!generatedAPFresh(*shadow,authority,work,why)))
    return no("ap-metric-primary-frame-or-current-CF");
   const auto& cells=shadow?shadow->capturedCF_:readOnlyCF_;
   const auto& faces=shadow?shadow->capturedFaces_:readOnlyFaces_;
   std::set<label> needed,bound;
   for(const auto& b:primary.endpointBindings())if(b.localFace<0||!bound.insert(b.localFace).second)
    return no("ap-metric-primary-duplicate-binding");
   for(const auto& cell:cells)for(label f:cell.second){
    if(!Q::workAvailable(work))return no("clock");const auto row=faces.find(f);
    if(row==faces.end())return no("ap-metric-primary-full-CF-missing-face");
    if(row->second.neighbour>=0||!coupled(row->second.patch))continue;
    if(row->second.patch<0||std::size_t(row->second.patch)>=mesh_->processorPatchPeers.size()
       ||mesh_->processorPatchPeers[row->second.patch]<0)return no("ap-metric-primary-unknown-processor-role");
    needed.insert(f);if(!shadow)readOnlyProcessorFaces_.insert(f);
   }
   needed.insert(bound.begin(),bound.end());if(needed.size()>128)return no("ap-metric-primary-complete-capacity");
   std::vector<E::Request> requests;
   for(label f:needed){
    if(bound.count(f))continue;if(!Q::workAvailable(work))return no("clock");
    const auto found=faces.find(f);if(found==faces.end())return no("ap-metric-extra-outside-primary-CF");
    const auto& row=found->second;const label peer=mesh_->processorPatchPeers.at(row.patch);
    if(peer<0||peer==rank_)return no("ap-metric-extra-unknown-peer");
    auto final=row;if(shadow){if(shadow->graph_.retired.count(f))return no("ap-metric-extra-retired-processor");
     const auto after=shadow->graph_.afterFaces.find(f);if(after!=shadow->graph_.afterFaces.end())final=after->second;}
    if(final.id!=f||final.neighbour!=-1||final.patch!=row.patch||final.zone!=row.zone||final.ring!=row.ring)
     return no("ap-metric-extra-processor-representation-change");
    E::Request request;request.senderRank=rank_;request.peerRank=peer;request.nativeFace=f;
    request.finalNativeFace=f;request.sourceHandle=base_->at(f);request.beforeOwner=row.owner;request.finalOwner=final.owner;
    if(!processorOriginalWorldRing(f,authority,work,request.originalRing))return no("ap-metric-extra-original-source-ring");
    for(label id:row.ring){const point& future=(shadow?jointShadowPoints_:mesh_->points).at(id);
     if(!samePoint(mesh_->points.at(id),future))return no("ap-metric-extra-processor-point-change");
     request.currentRing.push_back(mesh_->points.at(id));request.futureRing.push_back(future);}
    if(!processorCellShell(shadow,row.owner,false,coupled,work,request.before)
      ||!processorCellShell(shadow,final.owner,true,coupled,work,request.final))return no("ap-metric-extra-complete-CF");
    requests.push_back(std::move(request));
   }
   required.assign(needed.begin(),needed.end());out.swap(requests);
   return generatedAPReadOnlyFresh(work)&&(!shadow||generatedAPFresh(*shadow,authority,work,why));
  }catch(...){return no("ap-metric-discovery-unknown-or-allocation");}
 }

 bool proveGeneratedAPReadOnlyFinalStock(const Authority& authority,const ShadowFaceOF& faceOF,
        const Q::CoupledTypePatch& coupled,const JointPeerLookup& peer,const Q::Available& work,const char** why=nullptr) {
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(readOnlyStockProved_||!faceOF||!coupled||!generatedAPReadOnlyFresh(work))return no("ap-peer-readonly-Stock-current-CF");
   const Q::FaceOF metric=[&](const Ring& ring,vector& sf,point& fc){faceOF(mesh_->points,ring,sf,fc);};
   std::map<label,Q::Cell> measured;
   for(const auto& entry:readOnlyCF_){std::vector<Q::Face> shell;for(label f:entry.second)shell.push_back(readOnlyFaces_.at(f));
    auto cell=Q::measure(entry.first,shell,mesh_->points,metric,coupled,work,true,false);
    if(!cell.finite||!cell.strictGeometry)return no(cell.reason?cell.reason:"ap-peer-readonly-full-strict-geometry");
    measured.emplace(entry.first,std::move(cell));}
   for(label f:readOnlyProcessorFaces_){if(!Q::workAvailable(work))return no("clock");const auto& row=readOnlyFaces_.at(f);
    Q::CoupledEndpoint beforeRemote,finalRemote;if(!peer||!peer(f,false,beforeRemote)||!peer(f,true,finalRemote))return no("ap-peer-readonly-real-reciprocal-endpoints");
    const auto& own=measured.at(row.owner);
    const auto old=Q::coupledFacet(row,mesh_->points,metric,own.centre,own.volume,beforeRemote,work);
    const auto final=Q::coupledFacet(row,mesh_->points,metric,own.centre,own.volume,finalRemote,work);
    if(!old.numeric||!final.numeric||!final.valid)return no(final.reason?final.reason:"ap-peer-readonly-final-real-Stock");
    for(unsigned k=1;k<5;++k)if(!inherited(old.v[k],final.v[k],k))return no("ap-peer-readonly-original-to-final-W-R-S-N");
   }
   if(!generatedAPReadOnlyFresh(work)||!authority.sourceSnapshotIsRecorded(authority.sourceSnapshot.get()))return no("ap-peer-readonly-final-fresh-source");
   readOnlyStockProved_=true;return true;
  }catch(...){return no("ap-peer-readonly-final-Stock-unknown");}
 }

 // Genuine exchange carrier -> private value-owned receipt. No public bool
 // or numeric transport field can mint a receipt. This method is Phase-owned,
 // runs after full Source/VM/Stock proofs and before the opaque Core lease.
 bool prepareInstalledCoupledTransition(JointPublication& out,const GeneratedAPShadow* p,
       const Authority& authority,const E::Result& peers,const Q::Available& work,const char** why) {
  const auto no=[&](const char* x){if(why)*why=x;return false;};
  try {
   if(!mesh_->writerBaseline||!mesh_->writerBaseline->installedCoupledOwnerMaskComplete
     ||!mesh_->writerBaseline->installedSDKComplete||!authority.ready()
     ||!authority.sourceSnapshot||!authority.sourceSnapshotIsRecorded
     ||!authority.sourceSnapshotIsRecorded(authority.sourceSnapshot.get())||!Q::workAvailable(work))
      return no("ap-transition-earliest-installed-SDK-source-unknown");
   const auto& beforeCF=p?p->capturedCF_:readOnlyCF_;
   const auto& beforeFaces=p?p->capturedFaces_:readOnlyFaces_;
   const auto& beforePoints=p?p->capturedPoints_:readOnlyPoints_;
   const auto& mask=mesh_->writerBaseline->installedCoupledOwnerMask;
   std::set<label> required;
   for(const auto& cell:beforeCF){if(cell.first<0||std::size_t(cell.first)>=mask.size())return no("ap-transition-owner-mask-range");
    if(mask[cell.first])for(label f:cell.second){const auto found=beforeFaces.find(f);
     if(found==beforeFaces.end())return no("ap-transition-complete-before-CF");
     if(found->second.neighbour<0&&found->second.patch>=0
       &&std::size_t(found->second.patch)<mesh_->processorPatchPeers.size()
       &&mesh_->processorPatchPeers[found->second.patch]>=0)required.insert(f);}}
   if(!required.empty()&&!peers.realMPIEstablished())return no("ap-transition-real-MPI-carrier-required");
   using Receipt=dcCartCutCells::writerCoupledTransition;
   std::shared_ptr<Receipt> receipt(new Receipt);
   receipt->baseline_=mesh_->writerBaseline;receipt->sourceSnapshot_=authority.sourceSnapshot;
   receipt->fingerprint_=authority.sourceFingerprint;receipt->rank_=rank_;
   receipt->phaseRevision_=revision_;receipt->lineageRevision_=lineageRevision_;
   receipt->sourceProved_=true;receipt->stockProved_=p?p->stockProved_:readOnlyStockProved_;
   receipt->peerProved_=required.empty()||peers.realMPIEstablished();
   if(!receipt->stockProved_||!receipt->peerProved_)return no("ap-transition-real-Stock-or-peer-unproved");
   if(p){receipt->origins_=p->sourceOrigins_;for(const auto& group:p->graph_.components)
      receipt->keepers_.emplace_back(group.keeper,group.root);}
   else for(const auto& row:readOnlyOrigins_)receipt->origins_.insert(receipt->origins_.end(),row.second.begin(),row.second.end());
   if(p){if(!authority.completeOrigins(receipt->origins_))return no("ap-transition-complete-source-origins");}
   else for(const auto& row:readOnlyOrigins_)if(!authority.completeOrigins(row.second))
     return no("ap-transition-readonly-complete-source-origins");
   const auto copyShell=[&](const E::CellShell& source,dcCartCutCells::writerTransitionShell& target,bool future)->bool {
    if(source.installedSDK||source.owner<0||source.faces.size()<4||source.faces.size()>512)return false;
    target.owner=source.owner;std::set<label> seen;
    for(const auto& value:source.points){if(!Q::workAvailable(work)||!Q::finitePoint(value.value)
      ||!target.points.emplace(value.nativePoint,value.value).second)return false;
      const auto current=beforePoints.find(value.nativePoint);
      if(current==beforePoints.end())return false;
      const point expected=p&&future?jointShadowPoints_.at(value.nativePoint):current->second;
      if(!samePoint(expected,value.value))return false;}
    const Ring* ids=nullptr;
    if(p&&future){const auto found=p->graph_.afterCF.find(source.owner);if(found!=p->graph_.afterCF.end())ids=&found->second;}
    if(!ids){const auto found=beforeCF.find(source.owner);if(found==beforeCF.end())return false;ids=&found->second;}
    if(ids->size()!=source.faces.size())return false;
    for(std::size_t j=0;j<source.faces.size();++j){const auto& value=source.faces[j];const auto& row=value.face;
     if(!Q::workAvailable(work)||row.id!=(*ids)[j]||!seen.insert(row.id).second)return false;
     const auto captured=beforeFaces.find(row.id);if(captured==beforeFaces.end())return false;
     Q::Face expected=captured->second;if(p&&future){const auto changed=p->graph_.afterFaces.find(row.id);
       if(changed!=p->graph_.afterFaces.end())expected=changed->second;}
     if(!sameFace(expected,row))return false;
     dcCartCutCells::writerSDKFace f;f.nativeFace=row.id;f.owner=row.owner;f.neighbour=row.neighbour;
     f.patch=row.patch;f.rawPatch=row.patch;f.zone=row.zone;f.coupled=value.coupledByActualType;f.points=row.ring;
     if(!receipt->originalSDKProof_)return false;
     const auto sdk=receipt->originalSDKProof_->installedFaces.find(row.id);
     if(sdk==receipt->originalSDKProof_->installedFaces.end())return false;
     f.patch=sdk->second.patch;f.patchName=sdk->second.patchName;f.patchType=sdk->second.patchType;f.patchRole=sdk->second.patchRole;
     for(label id:row.ring)if(!target.points.count(id))return false;target.faces.push_back(std::move(f));}
    return true;};
   // Genuine ORIGINAL SDK -> eventual written-final identities. Never label
   // these old SDK shells as an SDK afterSame/pre-join frame.
   const auto& history=*mesh_->writerBaseline;
   if(!history.originalRepresentationComplete||!history.originalSDKCapture
      ||history.originalHeads.size()!=std::size_t(mesh_->nCells)
      ||history.originalCurrentOwner.size()!=history.originalNext.size())
      return no("ap-transition-original-SDK-publication-journal-unknown");
   std::map<label,label> selected;
   if(p)for(const auto& group:p->graph_.components)for(label c:group.members)selected.emplace(c,group.keeper);
   std::set<label> changedCurrent;
   if(p){changedCurrent=p->graph_.affected;for(const auto& shell:p->star_)changedCurrent.insert(shell.nativeCell);}
   else for(const auto& support:readOnlyCF_)changedCurrent.insert(support.first);
   std::vector<label> originals;
   for(const auto& current:beforeCF){label old=history.originalHeads.at(current.first);std::size_t guard=0;
    if(old<0)return no("ap-transition-original-SDK-owner-preimage-empty");
    const auto mapped=selected.find(current.first);const label keeper=mapped==selected.end()?current.first:mapped->second;
    while(old>=0){if(!Q::workAvailable(work)||std::size_t(old)>=history.originalNext.size()||++guard>history.originalNext.size()
      ||history.originalCurrentOwner.at(old)!=current.first||!receipt->originalKeepers_.emplace(old,keeper).second)
      return no("ap-transition-original-SDK-retirement-journal-stale");
     originals.push_back(old);if(changedCurrent.count(current.first))receipt->originalAffected_.push_back(old);
     if(originals.size()>4096)return no("ap-transition-original-SDK-reference-capacity");
     old=history.originalNext[old];}}
   std::shared_ptr<dcCartCutCells::writerCoupledBaseline> originalSDK(new dcCartCutCells::writerCoupledBaseline);
   if(!history.originalSDKCapture(originals,*originalSDK,work)||!originalSDK->installedSDKComplete)
      return no("ap-transition-original-SDK-full-affected-capture");
   for(const auto& face:originalSDK->installedFaces){const label oldFace=face.first;
    const label beforeCarrier=history.currentFaceCarrier(oldFace);label finalCarrier=beforeCarrier;
    if(p&&beforeCarrier>=0){for(const auto& group:p->graph_.constituents)
      if(std::find(group.second.begin(),group.second.end(),beforeCarrier)!=group.second.end()){
       if(finalCarrier!=beforeCarrier&&finalCarrier!=group.first)return no("ap-transition-original-SDK-face-carrier-ambiguous");finalCarrier=group.first;}}
    receipt->originalCarriers_.emplace(oldFace,finalCarrier);
    // Full halo roots/roles also require real current representative identity.
    for(label c:{face.second.owner,face.second.neighbour})if(c>=0&&!receipt->originalKeepers_.count(c)){
     if(std::size_t(c)>=history.originalCurrentOwner.size()||history.originalCurrentOwner[c]<0)
       return no("ap-transition-original-SDK-halo-owner-unknown");
     const label current=history.originalCurrentOwner[c];const auto mapped=selected.find(current);
     receipt->originalKeepers_.emplace(c,mapped==selected.end()?current:mapped->second);}
   }
   receipt->originalSDKProof_=std::move(originalSDK);
   std::set<label> bound;
   for(const auto& binding:peers.endpointBindings()){
    if(!Q::workAvailable(work)||binding.localFace<0||binding.peerRank<0||binding.peerRank==rank_
       ||binding.peerFace<0||binding.source<0||!bound.insert(binding.localFace).second)
       return no("ap-transition-carrier-identity-duplicate");
    const auto row=beforeFaces.find(binding.localFace);if(row==beforeFaces.end())return no("ap-transition-peer-face-outside-capture");
    if(row->second.patch<0||std::size_t(row->second.patch)>=mesh_->processorPatchPeers.size()
       ||mesh_->processorPatchPeers[row->second.patch]!=binding.peerRank
       ||base_->at(binding.localFace)!=binding.source)return no("ap-transition-real-processor-source-identity");
    dcCartCutCells::writerTransitionEndpoint endpoint;endpoint.nativeFace=binding.localFace;
    endpoint.beforeOwner=binding.before.owner;endpoint.finalOwner=binding.final.owner;
    endpoint.peerRank=binding.peerRank;endpoint.peerNativeFace=binding.peerFace;endpoint.source=binding.source;
    if(!copyShell(binding.before,endpoint.before,false)||!copyShell(binding.final,endpoint.final,true))
       return no("ap-transition-complete-carrier-CF-preimage");
    receipt->endpoints_.push_back(std::move(endpoint));
   }
   for(label f:required)if(!bound.count(f))return no("ap-transition-installed-owner-missing-real-peer-binding");
   // Include all owned vector/map/ring payload and existing publication slots.
   std::size_t bytes=sizeof(Receipt)+receipt->origins_.capacity()*sizeof(Origin)
     +receipt->keepers_.capacity()*sizeof(std::pair<label,label>)
     +receipt->endpoints_.capacity()*sizeof(dcCartCutCells::writerTransitionEndpoint);
   for(const auto& origin:receipt->origins_)bytes+=origin.region.capacity()+origin.sourceFacets.capacity()*sizeof(std::array<label,3>);
   for(const auto& endpoint:receipt->endpoints_)for(const auto* shell:{&endpoint.before,&endpoint.final}){
    bytes+=shell->faces.capacity()*sizeof(dcCartCutCells::writerSDKFace)
      +shell->points.size()*(sizeof(std::pair<const label,point>)+4*sizeof(void*));
    for(const auto& f:shell->faces)bytes+=f.points.capacity()*sizeof(label);}
   bytes+=receipt->originalSDKProof_->installedSDKPayloadBytes
       +receipt->originalAffected_.capacity()*sizeof(label)
       +(receipt->originalKeepers_.size()+receipt->originalCarriers_.size())*(sizeof(std::pair<const label,label>)+4*sizeof(void*));
   if(bytes>dcCartCutCells::writerCoupledBaseline::installedSDKStructuralLimit)return no("ap-transition-structural-capacity");
   const std::size_t ownedBytes=bytes;
   for(const auto& old:mesh_->writerCoupledTransitions){if(!old||!old->complete())return no("ap-transition-previous-receipt-unknown");
    if(old->payloadBytes()>dcCartCutCells::writerCoupledBaseline::installedSDKStructuralLimit-bytes)return no("ap-transition-structural-capacity");bytes+=old->payloadBytes();}
   if(bytes>dcCartCutCells::writerCoupledBaseline::installedSDKStructuralLimit)return no("ap-transition-structural-capacity");
   receipt->payloadBytes_=ownedBytes;
   out.transitionSlot_=mesh_->writerCoupledTransitions.size();
   mesh_->writerCoupledTransitions.reserve(out.transitionSlot_+1);out.transition_=std::move(receipt);
   return Q::workAvailable(work);
  }catch(...){return no("ap-transition-preflight-allocation-or-incomplete");}
 }

 bool prepareGeneratedAPJointPublication(GeneratedAPShadow& p,const Authority& authority,
         const E::Result& peers,const Q::Available& work,std::unique_ptr<JointPublication>& out,const char** why=nullptr) {
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(readOnlyScope_||out||jointPublicationActive_||!p.stockProved_||!generatedAPFresh(p,authority,work,why))
    return no("ap-joint-publication-preconditions");
   std::unique_ptr<JointPublication> staged(new JointPublication);
   staged->owner_=this;staged->shadow_=&p;staged->revision_=revision_;staged->lineageRevision_=lineageRevision_;
   staged->afterCF_=p.graph_.afterCF;staged->afterFaces_=p.graph_.afterFaces;staged->afterCells_=p.finalCells_;
   staged->retiredFaces_.assign(p.graph_.retired.begin(),p.graph_.retired.end());
   for(const auto& group:p.graph_.components)staged->componentRoots_.emplace_back(group.keeper,group.root);
   for(const auto& entry:p.finalCells_){const auto& cell=entry.second;
    if(cell.faceIds.size()!=cell.areas.size()||cell.faceIds.size()!=cell.centres.size())return no("ap-joint-cache-complete-measured-rows");
    for(std::size_t j=0;j<cell.faceIds.size();++j){const label f=cell.faceIds[j];
     const auto changed=p.graph_.afterFaces.find(f);const auto row=changed==p.graph_.afterFaces.end()?p.capturedFaces_.at(f):changed->second;
     const vector area=row.owner==entry.first?cell.areas[j]:-cell.areas[j];
     const auto inserted=staged->faceAreas_.emplace(f,area);
     if(!inserted.second&&!samePoint(inserted.first->second,area))return no("ap-joint-cache-owner-area-bits");
     const auto centre=staged->faceCentres_.emplace(f,cell.centres[j]);
     if(!centre.second&&!samePoint(centre.first->second,cell.centres[j]))return no("ap-joint-cache-centre-bits");
    }
   }
   for(const auto& group:p.graph_.components)for(label c:group.members)if(c!=group.keeper)
   {staged->retiredCells_.push_back(c);staged->installedRetirements_.emplace_back(c,group.keeper);}
   std::sort(staged->retiredCells_.begin(),staged->retiredCells_.end());
   for(const auto& row:staged->afterFaces_)staged->publishRings_.emplace(row.first,row.second.ring);
   // Every fallible caller-owned buffer precedes the opaque publisher lease.
   staged->replacements_.reserve(p.graph_.components.size());
   const std::function<bool(const std::vector<label>&)> permit=[&](const std::vector<label>& roots){return permission(authority,roots);};
   for(const auto& group:p.graph_.components){staged->replacements_.emplace_back();
    if(!mesh_->completeCavityLineage.prepareReplacement(group.members,{group.keeper},permit,work,staged->replacements_.back()))
     return no("ap-joint-real-component-lineage-prepare");}
   std::vector<Lineage::PreparedReplacement*> tokens;tokens.reserve(staged->replacements_.size());
   for(auto& token:staged->replacements_)tokens.push_back(&token);
   if(!generatedAPFresh(p,authority,work,why)
      ||!prepareInstalledCoupledTransition(*staged,&p,authority,peers,work,why))return false;
   if(!mesh_->completeCavityLineage.prepareIndependentSameOriginPublication(tokens,permit,work,staged->lease_))
    return no("ap-joint-real-preflight-lease");
   jointPublicationActive_=true;staged->ready_=true;out=std::move(staged);return true;
  }catch(...){return no("ap-joint-publication-preflight-unknown-or-allocation");}
 }
 bool prepareGeneratedAPReadOnlyPublication(const Authority& authority,const E::Result& peers,const Q::Available& work,
         std::unique_ptr<JointPublication>& out,const char** why=nullptr) {
  const auto no=[&](const char* text){if(why)*why=text;return false;};
  try {
   if(out||jointPublicationActive_||!readOnlyStockProved_||!generatedAPReadOnlyFresh(work)||!authority.ready()
      ||!authority.sourceSnapshot||!authority.sourceSnapshotIsRecorded
      ||!authority.sourceSnapshotIsRecorded(authority.sourceSnapshot.get()))return no("ap-peer-readonly-publication-preconditions");
   std::unique_ptr<JointPublication> staged(new JointPublication);staged->owner_=this;staged->readOnly_=true;
   staged->revision_=revision_;staged->lineageRevision_=lineageRevision_;
   const std::function<bool()> actualSupport=[&](){
    if(!generatedAPReadOnlyFresh(work))return false;
    for(const auto& entry:readOnlyOrigins_){if(!Q::workAvailable(work)||!authority.completeOrigins(entry.second))return false;}
    return generatedAPReadOnlyFresh(work)&&authority.sourceSnapshotIsRecorded(authority.sourceSnapshot.get());};
   if(!prepareInstalledCoupledTransition(*staged,nullptr,authority,peers,work,why))return false;
   if(!mesh_->completeCavityLineage.prepareReadOnlyPublication(actualSupport,work,staged->lease_))
    return no("ap-peer-real-readonly-preflight-lease");
   jointPublicationActive_=true;staged->ready_=true;out=std::move(staged);return true;
  }catch(...){return no("ap-peer-readonly-publication-unknown");}
 }
 // No callback/allocation in the last local fence. Real source/clock precede
 // the common vote; serial Phase exclusion remains held after this fence.
 bool generatedAPPublicationReadyForVote(const JointPublication& p)const noexcept {
  if(!p.transition_||mesh_->writerCoupledTransitions.size()!=p.transitionSlot_
    ||mesh_->writerCoupledTransitions.capacity()<=p.transitionSlot_
    ||p.transition_->baseline_.get()!=mesh_->writerBaseline.get())return false;
  if(!jointPublicationActive_||!p.ready_||p.owner_!=this||p.revision_!=revision_
     ||p.lineageRevision_!=lineageRevision_||!p.lease_.readyForVote())return false;
  const auto& cells=p.readOnly_?readOnlyCF_:p.shadow_->capturedCF_;
  const auto& faces=p.readOnly_?readOnlyFaces_:p.shadow_->capturedFaces_;
  const auto& bases=p.readOnly_?readOnlyBases_:p.shadow_->capturedBases_;
  const auto& points=p.readOnly_?readOnlyPoints_:p.shadow_->capturedPoints_;
  if(!mesh_||mesh_->faces.size()!=faceCount_||mesh_->points.size()!=pointCount_
      ||mesh_->points.data()!=pointStorage_||base_->size()!=faceCount_||cf_->size()!=std::size_t(mesh_->nCells))return false;
  if(!p.readOnly_&&(p.shadow_->phase_!=this||p.shadow_->nonce_!=jointShadowNonce_))return false;
  if(!p.readOnly_&&!localFacetMetricSupportFresh(p.shadow_->localMetricSupport_,*mesh_,*cf_,*base_,rank_))return false;
  for(const auto& row:cells){if(row.first<0||std::size_t(row.first)>=cf_->size()||!mesh_->cellAlive[row.first]
      ||mesh_->cellRank[row.first]!=rank_||(*cf_)[row.first]!=row.second)return false;}
  for(const auto& row:faces){const label f=row.first;const auto src=bases.find(f);
   if(f<0||std::size_t(f)>=faceCount_||!mesh_->faceAlive[f]||src==bases.end()||(*base_)[f]!=src->second
      ||mesh_->owner[f]!=row.second.owner||mesh_->neighbour[f]!=row.second.neighbour
      ||mesh_->patch[f]!=row.second.patch||mesh_->zone[f]!=row.second.zone||mesh_->faces[f]!=row.second.ring)return false;}
  for(const auto& value:points)if(value.first<0||std::size_t(value.first)>=pointCount_
       ||!samePoint(value.second,mesh_->points[value.first]))return false;
  return true;
 }
 // Only after the real common YES: no rejecting clock/source call, checked
 // index access or allocation remains on this irreversible publication path.
 void publishGeneratedAPJointAfterVote(JointPublication& p)noexcept {
  if(!jointPublicationActive_||!p.ready_||p.owner_!=this||p.published_)std::terminate();
  mesh_->completeCavityLineage.publishIndependentSameOrigin(std::move(p.lease_));
  if(!p.readOnly_){
   for(const auto& move:p.shadow_->moves_)mesh_->points[move.nativePoint]=move.target;
   for(label f:p.retiredFaces_)mesh_->faceAlive[f]=false;
   if(mesh_->writerBaseline)for(const auto& entry:p.shadow_->graph_.constituents)
       for(label f:entry.second)mesh_->writerBaseline->recordFaceCarrier(f,entry.first);
   for(const auto& r:p.installedRetirements_)if(mesh_->writerBaseline)mesh_->writerBaseline->recordRetirement(r.first,r.second);
   for(label c:p.retiredCells_){mesh_->cellAlive[c]=false;(*cf_)[c].clear();}
   for(auto& row:p.afterCF_)(*cf_)[row.first].swap(row.second);
   for(const auto& row:p.afterFaces_){mesh_->owner[row.first]=row.second.owner;mesh_->neighbour[row.first]=row.second.neighbour;}
   for(auto& row:p.publishRings_)mesh_->faces[row.first].swap(row.second);
   dcCartPreparedFacetBirth::invalidateCurrentIncidence(*mesh_);
   ++revision_;apIncidenceReady_=false;jointShadowReady_=false;jointShadowMoves_.clear();
  }
  // Pre-reserved move only after the real common YES; no allocator/callback.
  p.transition_->publicationRevision_=mesh_->completeCavityLineage.revision();p.transition_->published_=true;
  mesh_->writerCoupledTransitions.emplace_back(std::move(p.transition_));
  lineageRevision_=mesh_->completeCavityLineage.revision();p.ready_=false;p.published_=true;jointPublicationActive_=false;
 }

 bool legacyInstalledCoupledLiteral(const Prepared& p,const Q::Available& work,const char** why)const {
  const auto no=[&](const char* reason){if(why)*why=reason;return false;};
  if(!work||!work()||!mesh_||!mesh_->writerBaseline)
   return no("grid-pair-installed-coupled-view-unknown");
  const auto& view=*mesh_->writerBaseline;
  if(!view.installedCoupledOwnerMaskComplete
    ||view.installedCoupledOwnerMask.size()!=std::size_t(mesh_->nCells))
   return no("grid-pair-installed-coupled-view-incomplete");
  for(const auto& entry:p.beforeCF_){
   const label cell=entry.first;
   if(cell<0||cell>=mesh_->nCells)return no("grid-pair-installed-coupled-owner-range");
   if(!view.installedCoupledOwnerMask[cell])continue;
   if(cell==p.retire_||cell==p.mateRetire_||!mesh_->cellAlive[cell])
    return no("grid-pair-installed-coupled-owner-retirement");
   const auto futureCF=p.afterCF_.find(cell);
   if(futureCF!=p.afterCF_.end()&&futureCF->second!=entry.second)
    return no("grid-pair-installed-coupled-complete-CF-change");
   for(label f:entry.second){
    const auto before=p.beforeFaces_.find(f);
    if(before==p.beforeFaces_.end()||!mesh_->faceAlive.at(f))
     return no("grid-pair-installed-coupled-shell-face-missing");
    if(std::find(p.retired_.begin(),p.retired_.end(),f)!=p.retired_.end())
     return no("grid-pair-installed-coupled-shell-face-retirement");
    const auto after=p.afterFaces_.find(f);
    if(after!=p.afterFaces_.end()&&!sameFace(before->second,after->second))
     return no("grid-pair-installed-coupled-shell-incidence-or-ring-change");
    if(!sameFace(before->second,face(f)))
     return no("grid-pair-installed-coupled-shell-stale");
    for(label point:before->second.ring){
     const auto value=p.points_.find(point);
     if(value==p.points_.end()||!samePoint(value->second,mesh_->points.at(point)))
      return no("grid-pair-installed-coupled-shell-point-preimage");
    }
   }
  }
  return work()?true:no("grid-pair-installed-coupled-final-clock");
 }

 bool prepare(label a,label b,const Authority& authority,const Q::FaceOF& metric,
              const Q::CoupledTypePatch& coupled,const Q::Available& work,Prepared& out,const char** why=nullptr,
              Mode mode=Mode::CartesianCrossGrid) {
  const auto no=[&](const char* s){if(why)*why=s;return false;};
  if(out.ready_||out.committed_)return no("grid-pair-output-not-empty");
  try {
   if(readOnlyScope_||jointPublicationActive_||!live(work)||!metric||!coupled||journal_.size()==journal_.capacity())return no("grid-pair-phase-or-clock");
   if(a==b||!local(a)||!local(b))return no("grid-pair-local-members");
   // Composite physical provenance has no scalar Source equivalent. This
   // ordinary transaction does not implement the typed carrier contract.
   std::set<label> compositePhysicalFaces;
   if(mesh_->physicalCompositeCarriers.size()>16384)return no("grid-pair-composite-Source-context-bound");
   for(const auto& token:mesh_->physicalCompositeCarriers){
    if(!token)return no("grid-pair-composite-Source-context-unknown");
    compositePhysicalFaces.insert(token->record().carrier);
   }
   for(label c:{a,b})for(label f:cf_->at(c))if(compositePhysicalFaces.count(f))
    return no("grid-pair-composite-Source-context-unknown");
   Prepared p;p.phase_=this;p.revision_=revision_;p.lineageRevision_=lineageRevision_;p.keep_=min(a,b);p.retire_=max(a,b);p.members_={p.keep_,p.retire_};p.mode_=mode;
   if(mode!=Mode::CartesianCrossGrid&&mode!=Mode::SameOriginGeneratedSide)return no("grid-pair-mode");
   if(mode!=phaseMode_)return no("grid-pair-phase-mode");
   if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(*mesh_,authority,p.members_,p.completeOrigins_))return no("grid-pair-complete-origins");
   p.snapshot_=authority.sourceSnapshot;p.fingerprint_=authority.sourceFingerprint;
   if(!p.snapshot_||!p.snapshot_->complete||!p.snapshot_->completedFluidOnlyPath||!authority.sourceSnapshotIsRecorded
     ||!authority.sourceSnapshotIsRecorded(p.snapshot_.get()))return no("grid-pair-recorded-snapshot");
   const auto& s=*p.snapshot_;
   if(s.rawSha256!=p.fingerprint_.raw||s.rolesDigest!=p.fingerprint_.roles||s.materialKey!=p.fingerprint_.materialKey
     ||s.materialDigest!=p.fingerprint_.material||s.producerDigest!=p.fingerprint_.producer||s.boundaryMembershipDigest!=p.fingerprint_.boundaryMembership)return no("grid-pair-fresh-material");
   std::set<label> all,shared,sources,affected{a,b},support;
   for(label c:p.members_){if(cf_->at(c).size()<4||cf_->at(c).size()>512)return no("grid-pair-member-CF-bound");all.insert(cf_->at(c).begin(),cf_->at(c).end());}
   for(label f:all){const auto r=face(f);const bool o=r.owner==a||r.owner==b,n=r.neighbour==a||r.neighbour==b;
    if(o&&n){if(r.patch!=-1||r.zone!=-1||coupled(r.patch))return no("grid-pair-protected-separator");shared.insert(f);if(base_->at(f)>=0)sources.insert(base_->at(f));}
    else {p.outer_.push_back(f);for(label c:{r.owner,r.neighbour})if(c>=0)affected.insert(c);}
   }
   if(mode==Mode::CartesianCrossGrid) {
   if(shared.empty()||sources.empty()||sources.size()>32||p.outer_.size()<4||p.outer_.size()>512||affected.size()>128)return no("grid-pair-cross-grid-scope");
   }else {
    if(shared.empty()||!sources.empty()||p.outer_.size()<4||p.outer_.size()>512||affected.size()>128
      ||generatedFaceFloor_<0||std::size_t(generatedFaceFloor_)<s.faces.size()
      ||std::size_t(generatedFaceFloor_)>faceCount_||p.completeOrigins_.size()!=1)return no("same-origin-generated-side-scope");
    const auto& origin=p.completeOrigins_.front();const label root=origin.nativeParent;
    if(origin.rank!=rank_||root<0||!authority.recordedParent(root))return no("same-origin-recorded-parent");
    for(label c:p.members_){std::vector<label> roots;
     if(!mesh_->completeCavityLineage.originsForCells({c},roots)||roots.size()!=1||roots.front()!=root)
      return no("same-origin-current-lineage");}
    for(label f:shared){const auto row=face(f);
     if(f<generatedFaceFloor_||base_->at(f)!=-1||row.patch!=-1||row.zone!=-1||!local(row.owner)||!local(row.neighbour))
      return no("same-origin-real-generated-separator");}
    // Preserve every original-source exterior role; current generated fluid
    // separators alone are consumed. Bind retained roles to this sole origin.
    for(label f:p.outer_){const auto row=face(f);const label source=base_->at(f);
     if(source<0){if(source!=-1||f<generatedFaceFloor_||row.patch!=-1||row.zone!=-1||row.neighbour<0)
       return no("same-origin-generated-exterior-role");continue;}
     if(std::size_t(source)>=s.faces.size())return no("same-origin-original-source-index");
     const auto& original=s.faces[source];
     if(original.handle!=source||original.patch!=row.patch||original.zone!=row.zone
       ||(original.owner!=root&&original.neighbour!=root))return no("same-origin-original-source-role");
     if(!std::any_of(origin.sourceFacets.begin(),origin.sourceFacets.end(),[&](const std::array<label,3>& facet)
       {return facet[0]==source&&facet[1]==row.patch&&facet[2]==row.zone;}))return no("same-origin-complete-facet-membership");
    }
    p.sameOriginParent_=root;
   }
   p.retired_.assign(shared.begin(),shared.end());
   X::Geometry exact(mesh_->points,work);
   std::map<label,std::vector<Origin>> currentOriginRecords;
   auto sourceOrigin=[&](label c,label root)->bool {if(!Q::workAvailable(work))return false;auto at=currentOriginRecords.find(c);
    if(at==currentOriginRecords.end()){std::vector<Origin> os;if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(*mesh_,authority,{c},os))return false;at=currentOriginRecords.emplace(c,std::move(os)).first;}
    return std::any_of(at->second.begin(),at->second.end(),[&](const Origin& o){return o.nativeParent==root&&o.rank==rank_;});};
   for(label source:sources){if(!Q::workAvailable(work)||source<0||std::size_t(source)>=s.faces.size())return no("grid-pair-source-or-clock");
    const auto& old=s.faces[source];if(old.handle!=source||old.owner<0||old.neighbour<0||old.owner==old.neighbour||old.patch!=-1||old.zone!=-1
      ||old.ring.size()<3||old.ring.size()>64||std::size_t(max(old.owner,old.neighbour))>=s.parents.size()
      ||std::size_t(max(old.owner,old.neighbour))>=s.valid.size()||!s.valid[old.owner]||!s.valid[old.neighbour])return no("grid-pair-original-internal-grid");
    const auto& oi=s.parents[old.owner].identity;const auto& ni=s.parents[old.neighbour].identity;
    if(std::get<0>(oi)!=rank_||std::get<0>(ni)!=rank_||(std::get<2>(oi)==std::get<2>(ni)&&std::get<3>(oi)==std::get<3>(ni)))return no("grid-pair-distinct-Cartesian-leaves");
    SourceProof proof;proof.source=source;proof.originalOwner=old.owner;proof.originalNeighbour=old.neighbour;proof.originalRing.assign(old.ring.begin(),old.ring.end());
    for(label id:proof.originalRing){if(id<0||std::size_t(id)>=s.points.size()||std::size_t(id)>=mesh_->points.size())return no("grid-pair-original-point");
     const auto& v=s.points[id];point original(v[0],v[1],v[2]);if(!samePoint(original,mesh_->points[id]))return no("grid-pair-original-point-bits");}
    for(label k=0;k<3;++k){bool same=true;for(label id:proof.originalRing)same=same&&bits(mesh_->points[id][k])==bits(mesh_->points[proof.originalRing.front()][k]);
     if(same){if(proof.axis>=0)return no("grid-pair-degenerate-grid-support");proof.axis=k;proof.supportBits=bits(mesh_->points[proof.originalRing.front()][k]);}}
    if(proof.axis<0)return no("grid-pair-non-Cartesian-grid-support");
    std::vector<Ring> rings;std::set<label> nodes;
    for(label f=head_.at(source);f>=0;f=next_.at(f)){if(!Q::workAvailable(work))return no("clock");if(!mesh_->faceAlive.at(f))continue;
     const auto r=face(f);if(base_->at(f)!=source||r.patch!=-1||r.zone!=-1||!local(r.owner)||!local(r.neighbour)||r.ring.size()<3||r.ring.size()>64)return no("grid-pair-current-source-cohort");
     const bool oo=sourceOrigin(r.owner,old.owner),on=sourceOrigin(r.owner,old.neighbour),noo=sourceOrigin(r.neighbour,old.owner),nn=sourceOrigin(r.neighbour,old.neighbour);
     if(!((oo&&!on&&!noo&&nn)||(!oo&&on&&noo&&!nn)))return no("grid-pair-source-endpoint-origin-ambiguous");
     Ring ring=r.ring;if(!oo)std::reverse(ring.begin()+1,ring.end());
     for(label id:ring){if(id<0||std::size_t(id)>=mesh_->points.size()||bits(mesh_->points[id][proof.axis])!=proof.supportBits)return no("grid-pair-source-support-changed");nodes.insert(id);}
     rings.push_back(std::move(ring));proof.cohort.push_back(r);if(proof.cohort.size()>512||nodes.size()>512)return no("grid-pair-source-cohort-bound");
    }
    if(rings.empty())return no("grid-pair-empty-source-cohort");for(label id:proof.originalRing)if(!nodes.count(id))return no("grid-pair-source-original-node-lost");
    Ring outline;if(!exact.outline(rings,outline))return no("grid-pair-source-directed-outline");
    // Compare actual directed boundary after exact edge noding. No coordinate weld.
    const auto atomize=[&](const Ring& r,std::map<std::pair<label,label>,unsigned>& edges)->bool {
     for(std::size_t j=0;j<r.size();++j){if(!Q::workAvailable(work))return false;const label first=r[j],second=r[(j+1)%r.size()];
      unsigned k=0;while(k<3&&exact.at(first)[k]==exact.at(second)[k])++k;if(k==3)return false;
      std::vector<std::pair<X::R,label>> hits;for(label id:nodes)if(exact.segment(id,first,second))hits.push_back({(exact.at(id)[k]-exact.at(first)[k])/(exact.at(second)[k]-exact.at(first)[k]),id});
      std::sort(hits.begin(),hits.end());if(hits.size()<2||hits.front().second!=first||hits.back().second!=second)return false;
      for(std::size_t t=1;t<hits.size();++t){if(hits[t-1].first==hits[t].first)return false;++edges[{hits[t-1].second,hits[t].second}];}
     }return true;};
    std::map<std::pair<label,label>,unsigned> expectedEdges,actualEdges;
    if(!atomize(proof.originalRing,expectedEdges)||!atomize(outline,actualEdges)||expectedEdges!=actualEdges)return no("grid-pair-original-outline-changed");
    I::E::Context ec(work);auto at=[&](label id)->const point&{return mesh_->points.at(id);};I::Integrals sum;
    for(const auto& ring:rings)I::add(sum,I::integrals(ring,at,ec),ec);
    if(!I::same(sum,I::integrals(proof.originalRing,at,ec),ec)||!ec.good())return no("grid-pair-exact-source-surface12-6V24M");
    p.sources_.push_back(std::move(proof));
   }
   // Complete conservative exterior representation; actual native source
   // and incidence, never whole-patch or observer identity. No point append.
   auto componentKeeper=[&](label c)->label {
    if(c==a||c==b)return p.keep_;
    if(p.mateKeep_>=0&&(c==p.mateKeep_||c==p.mateRetire_))return p.mateKeep_;return -1;
   };
   auto retiredCell=[&](label c)->bool{return c==p.retire_||(p.mateRetire_>=0&&c==p.mateRetire_);};
   auto repairedHost=[&](label c)->bool{return c==p.keep_||(p.mateKeep_>=0&&c==p.mateKeep_);};
   using OuterKey=std::tuple<label,label,label,label,label,X::Q,X::R>;
   std::map<OuterKey,Ring> outerGroups;std::set<label> representationPeers;
   auto buildOuterGroups=[&]()->bool {
    outerGroups.clear();for(label f:p.outer_){if(!Q::workAvailable(work))return false;const auto row=face(f);
     const label ownerHost=componentKeeper(row.owner),neighbourHost=componentKeeper(row.neighbour);
     if((ownerHost>=0)==(neighbourHost>=0))return false;
     const label host=ownerHost>=0?ownerHost:neighbourHost,outside=ownerHost>=0?row.neighbour:row.owner;
     Ring ring=row.ring;if(ownerHost<0)std::reverse(ring.begin()+1,ring.end());
     X::Q normal;if(!exact.simplePlanar(ring,normal)){if(!Q::workAvailable(work))return false;continue;}
     const auto primitive=exact.primitiveNormal(ring,normal);
     outerGroups[OuterKey(host,base_->at(f),outside,row.patch,row.zone,primitive,exact.planeBound(ring,primitive))].push_back(f);
    }return true;
   };
   if(!buildOuterGroups())return no("outer-current-incidence-or-clock");
   auto sourceCohort=[&](label source,std::set<label>& peers)->bool {
    if(source<0||std::size_t(source)>=s.faces.size())return false;const auto& original=s.faces[source];
    if(original.handle!=source||original.ring.size()<3||original.ring.size()>64)return false;
    for(label id:original.ring){if(!Q::workAvailable(work)||id<0||std::size_t(id)>=s.points.size()
      ||std::size_t(id)>=mesh_->points.size())return false;const auto& q=s.points[id];
     if(!samePoint(point(q[0],q[1],q[2]),mesh_->points[id]))return false;}
    I::E::Context ec(work);auto at=[&](label id)->const point&{return mesh_->points.at(id);};I::Integrals sum;std::size_t count=0;std::vector<Ring> rings;std::set<label> nodes;
    for(label f=head_.at(source);f>=0;f=next_.at(f)){if(!Q::workAvailable(work))return false;if(!mesh_->faceAlive.at(f))continue;
     const auto row=face(f);if(base_->at(f)!=source||row.patch!=original.patch||row.zone!=original.zone||coupled(row.patch)
       ||!local(row.owner)||(row.neighbour>=0&&!local(row.neighbour)))return false;
     const bool oo=sourceOrigin(row.owner,original.owner),on=original.neighbour>=0&&sourceOrigin(row.owner,original.neighbour);
     bool forward=oo&&!on;
     if(original.neighbour>=0){const bool no=sourceOrigin(row.neighbour,original.owner),nn=sourceOrigin(row.neighbour,original.neighbour);
      if(!((oo&&!on&&!no&&nn)||(!oo&&on&&no&&!nn)))return false;}
     else if(row.neighbour!=-1||!forward)return false;
     Ring ring=row.ring;if(!forward)std::reverse(ring.begin()+1,ring.end());
     for(label id:ring)nodes.insert(id);if(nodes.size()>512)return false;rings.push_back(ring);
     I::add(sum,I::integrals(ring,at,ec),ec);peers.insert(row.owner);if(row.neighbour>=0)peers.insert(row.neighbour);
     if(++count>512||peers.size()>128)return false;
    }
    for(label id:original.ring)if(!nodes.count(id))return false;
    auto atomize=[&](const Ring& ring,std::map<std::pair<label,label>,unsigned>& edges)->bool {
     for(std::size_t j=0;j<ring.size();++j){if(!Q::workAvailable(work))return false;const label a=ring[j],b=ring[(j+1)%ring.size()];
      unsigned axis=0;while(axis<3&&exact.at(a)[axis]==exact.at(b)[axis])++axis;if(axis==3)return false;
      std::vector<std::pair<X::R,label>> hits;for(label id:nodes)if(exact.segment(id,a,b))
       hits.push_back({(exact.at(id)[axis]-exact.at(a)[axis])/(exact.at(b)[axis]-exact.at(a)[axis]),id});
      std::sort(hits.begin(),hits.end());if(hits.size()<2||hits.front().second!=a||hits.back().second!=b)return false;
      for(std::size_t k=1;k<hits.size();++k){if(hits[k-1].first==hits[k].first)return false;++edges[{hits[k-1].second,hits[k].second}];}
     }return true;
    };
    std::map<std::pair<label,label>,unsigned> expected,counts,actual;
    if(!atomize(original.ring,expected))return false;for(const auto& ring:rings)if(!atomize(ring,counts))return false;
    for(const auto& edge:counts){if(edge.second!=1)return false;const auto reverse=counts.find({edge.first.second,edge.first.first});
     if(reverse!=counts.end()){if(reverse->second!=1)return false;continue;}actual.emplace(edge);}
    return count&&actual==expected&&ec.good()&&I::same(sum,I::integrals(original.ring,at,ec),ec)&&ec.good();
   };
   auto currentRingKey=[&](const Ring& ring,bool reverse,std::vector<uint64_t>& key)->bool {
    if(ring.size()<3||ring.size()>64)return false;std::vector<std::array<uint64_t,3>> points;points.reserve(ring.size());
    for(std::size_t j=0;j<ring.size();++j){if(!Q::workAvailable(work))return false;
     const label id=ring[reverse?(ring.size()-j)%ring.size():j];if(id<0||std::size_t(id)>=mesh_->points.size())return false;
     const auto& q=mesh_->points[id];for(unsigned k=0;k<3;++k)if(!std::isfinite(q[k]))return false;
     points.push_back({{bits(q[0]),bits(q[1]),bits(q[2])}});}
    std::size_t start=0;for(std::size_t j=1;j<points.size();++j){bool less=false;
     for(std::size_t k=0;k<points.size();++k){const auto& x=points[(j+k)%points.size()];const auto& y=points[(start+k)%points.size()];
      if(x<y){less=true;break;}if(y<x)break;}if(less)start=j;}
    key.clear();key.reserve(points.size()*3);for(std::size_t j=0;j<points.size();++j)
     for(uint64_t x:points[(start+j)%points.size()])key.push_back(x);return true;
   };
   // At most one paired two-cell component, with its own recorded root.
   // Two independent replacements never assign combined roots to both keepers.
   for(const auto& item:outerGroups){const Ring& members=item.second;if(members.size()<2)continue;
    const label source=std::get<1>(item.first);if(source<0||std::get<4>(item.first)<0)continue;
    std::vector<Ring> rings;for(label f:members)rings.push_back(face(f).ring);Ring outline;
    if(!exact.outline(rings,outline))continue;
    label mate=-1;if(!pairedOriginal(p,source,coupled,work,mate,why))return false;if(mate<0)continue;
    if(!sourceCohort(source,representationPeers)||!sourceCohort(mate,representationPeers))return no("outer-paired-discovery-complete-cohort");
    std::set<label> owners,matched;
    for(label f:members){std::vector<uint64_t> expected;if(!currentRingKey(face(f).ring,true,expected))return no("outer-paired-discovery-ring");
     label match=-1;for(label g=head_.at(mate);g>=0;g=next_.at(g)){if(!Q::workAvailable(work))return no("clock");if(!mesh_->faceAlive.at(g))continue;
      std::vector<uint64_t> actual;if(!currentRingKey(face(g).ring,false,actual))return no("outer-paired-discovery-mate-ring");
      if(actual==expected){if(match>=0)return no("outer-paired-discovery-ambiguous");match=g;}}
     if(match<0||!matched.insert(match).second)return no("outer-paired-discovery-missing");owners.insert(face(match).owner);
    }
    if(owners.size()==1)continue;
    if(mode!=Mode::SameOriginGeneratedSide||owners.size()!=2)return no("outer-paired-independent-component-bound");
    const label mk=*owners.begin(),md=*owners.rbegin();if(mk==a||mk==b||md==a||md==b||!local(mk)||!local(md))return no("outer-paired-independent-component-disjoint");
    if(p.mateKeep_>=0){if(p.mateKeep_!=mk||p.mateRetire_!=md)return no("outer-paired-independent-component-conflict");continue;}
    std::vector<Origin> os;if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(*mesh_,authority,{mk,md},os)||os.size()!=1)
      return no("outer-paired-independent-complete-origin");
    const label root=os.front().nativeParent;if(root<0||root==p.sameOriginParent_||os.front().rank!=rank_||!authority.recordedParent(root))
      return no("outer-paired-independent-origin");
    for(label c:{mk,md}){std::vector<label> roots;if(!mesh_->completeCavityLineage.originsForCells({c},roots)||roots.size()!=1||roots.front()!=root)
      return no("outer-paired-independent-lineage");if(cf_->at(c).size()<4||cf_->at(c).size()>512)return no("outer-paired-independent-CF-bound");}
    std::set<label> mf;mf.insert(cf_->at(mk).begin(),cf_->at(mk).end());mf.insert(cf_->at(md).begin(),cf_->at(md).end());bool separator=false;
    for(label f:mf){const auto row=face(f);const bool o=row.owner==mk||row.owner==md,n=row.neighbour==mk||row.neighbour==md;
     if(o&&n){if(f<generatedFaceFloor_||base_->at(f)!=-1||row.patch!=-1||row.zone!=-1||coupled(row.patch))return no("outer-paired-independent-generated-separator");separator=true;}
     else {const label base=base_->at(f);if(base<0){if(base!=-1||f<generatedFaceFloor_||row.patch!=-1||row.zone!=-1||row.neighbour<0)return no("outer-paired-independent-generated-exterior");}
      else {if(std::size_t(base)>=s.faces.size())return no("outer-paired-independent-source-index");const auto& original=s.faces[base];
       if(original.handle!=base||original.patch!=row.patch||original.zone!=row.zone||(original.owner!=root&&original.neighbour!=root)
         ||!std::any_of(os.front().sourceFacets.begin(),os.front().sourceFacets.end(),[&](const std::array<label,3>& x)
          {return x[0]==base&&x[1]==row.patch&&x[2]==row.zone;}))return no("outer-paired-independent-source-role");}}
    }
    if(!separator)return no("outer-paired-independent-no-generated-side");
    p.mateKeep_=mk;p.mateRetire_=md;p.mateOriginParent_=root;p.mateMembers_={mk,md};
   }
   if(p.mateKeep_>=0){
    for(label c:p.mateMembers_){all.insert(cf_->at(c).begin(),cf_->at(c).end());affected.insert(c);}
    p.outer_.clear();shared.clear();for(label f:all){const auto row=face(f);const label o=componentKeeper(row.owner),n=componentKeeper(row.neighbour);
     if(o>=0&&n>=0){if(o!=n||base_->at(f)!=-1||f<generatedFaceFloor_||row.patch!=-1||row.zone!=-1||coupled(row.patch))
       return no("outer-paired-independent-separator-role");shared.insert(f);}
     else {p.outer_.push_back(f);for(label c:{row.owner,row.neighbour})if(c>=0)affected.insert(c);}
    }
    if(p.outer_.size()>512||affected.size()>128)return no("outer-paired-independent-scope-bound");
    p.retired_.assign(shared.begin(),shared.end());if(!buildOuterGroups())return no("outer-paired-independent-incidence-or-clock");
   }
   for(const auto& item:outerGroups){const Ring& members=item.second;if(members.size()<2)continue;
    std::size_t represented=0;for(label f:members)represented+=p.representedBy_.count(f);
    if(represented==members.size())continue;if(represented)return no("outer-paired-partial-representation");
    if(!Q::workAvailable(work))return no("clock");const label host=std::get<0>(item.first),source=std::get<1>(item.first),outside=std::get<2>(item.first);
    const label patch=std::get<3>(item.first),zone=std::get<4>(item.first);
    if(coupled(patch)||(outside>=0&&!local(outside)))return no("outer-representation-peer-unknown");
    std::vector<Ring> rings;for(label f:members){auto row=face(f);Ring r=row.ring;
     if(componentKeeper(row.owner)!=host)std::reverse(r.begin()+1,r.end());rings.push_back(std::move(r));}
    Ring outline;if(!exact.outline(rings,outline))continue; // Disconnected/non-convex buckets stay literal; full Stock decides.
    label mate=-1;
    if(source>=0){if(!sourceCohort(source,representationPeers))return no("outer-complete-original-source-cohort");
     if(!pairedOriginal(p,source,coupled,work,mate,why))return false;
     if(mate>=0&&!sourceCohort(mate,representationPeers))return no("outer-complete-paired-current-cohort");
    }else{if(source!=-1||patch!=-1||zone!=-1||outside<0)return no("outer-generated-role");
     label origin=-1;for(label f:members){const auto row=face(f);
      if(f<generatedFaceFloor_||generatedFaceFloor_<0||!local(row.owner)||!local(row.neighbour))return no("outer-generated-producer-floor");
      for(label c:{row.owner,row.neighbour}){std::vector<label> roots;
       if(!mesh_->completeCavityLineage.originsForCells({c},roots)||roots.size()!=1
         ||!authority.recordedParent(roots.front()))return no("outer-generated-single-recorded-origin");
       if(origin<0)origin=roots.front();else if(origin!=roots.front())return no("outer-generated-origin-mismatch");}
     }
    }
    I::E::Context ec(work);auto at=[&](label id)->const point&{return mesh_->points.at(id);};I::Integrals sum;
    for(const auto& ring:rings)I::add(sum,I::integrals(ring,at,ec),ec);
    if(!ec.good()||!I::same(sum,I::integrals(outline,at,ec),ec)||!ec.good())return no("outer-exact-surface12-6V24M");
    OuterRepresentation mateGroup;
    if(mate>=0){
     // Both current representations must participate. Original reversed rings
     // alone cannot authorize publishing only one side of a split wall pair.
     label mateOwner=-1,mateHost=-1;std::set<label> unique;std::vector<Ring> mateRings;
     for(label f:members){std::vector<uint64_t> expected;if(!currentRingKey(face(f).ring,true,expected))return no("outer-paired-current-ring");
      label matching=-1;for(label g=head_.at(mate);g>=0;g=next_.at(g)){if(!Q::workAvailable(work))return no("clock");
       if(!mesh_->faceAlive.at(g))continue;const auto row=face(g);std::vector<uint64_t> actual;
       if(!currentRingKey(row.ring,false,actual))return no("outer-paired-current-mate-ring");
       if(actual==expected){if(matching>=0)return no("outer-paired-current-mate-ambiguous");matching=g;}}
      if(matching<0||!unique.insert(matching).second)return no("outer-paired-current-mate-missing");
      const auto row=face(matching);if(row.neighbour!=-1||row.patch!=s.faces[mate].patch||row.zone!=zone||!local(row.owner))
       return no("outer-paired-current-mate-role");
      const label planned=componentKeeper(row.owner)>=0?componentKeeper(row.owner):row.owner;
      if(mateOwner<0){mateOwner=row.owner;mateHost=planned;}else if(mateHost!=planned)return no("outer-paired-current-multiple-unbound-owners");
      mateGroup.members.push_back(matching);mateRings.push_back(row.ring);
     }
     if(mateOwner<0||!exact.outline(mateRings,mateGroup.outline))return no("outer-paired-current-mate-outline");
     std::vector<uint64_t> expected,actual;if(!currentRingKey(outline,true,expected)
       ||!currentRingKey(mateGroup.outline,false,actual)||expected!=actual)return no("outer-paired-future-outline-reverse");
     I::Integrals mateSum;for(const auto& ring:mateRings)I::add(mateSum,I::integrals(ring,at,ec),ec);
     if(!ec.good()||!I::same(mateSum,I::integrals(mateGroup.outline,at,ec),ec)||!ec.good())return no("outer-paired-exact-surface12-6V24M");
     mateGroup.source=mate;mateGroup.host=mateHost;mateGroup.outside=-1;
     mateGroup.patch=s.faces[mate].patch;mateGroup.zone=zone;mateGroup.mateSource=source;
     mateGroup.carrier=*std::min_element(mateGroup.members.begin(),mateGroup.members.end());
     for(label g:mateGroup.members)if(p.representedBy_.count(g))return no("outer-paired-current-mate-already-represented");
    }
    OuterRepresentation group;group.source=source;group.host=host;group.outside=outside;group.patch=patch;group.zone=zone;
    group.carrier=*std::min_element(members.begin(),members.end());group.mateSource=mate;group.members=members;group.outline=std::move(outline);
    for(label f:members)if(!p.representedBy_.emplace(f,group.carrier).second)return no("outer-representation-duplicate");
    p.representations_.push_back(std::move(group));
    if(mate>=0){for(label g:mateGroup.members)if(!p.representedBy_.emplace(g,mateGroup.carrier).second)return no("outer-paired-representation-duplicate");
     p.representations_.push_back(std::move(mateGroup));}
   }
   affected.insert(representationPeers.begin(),representationPeers.end());
   // Complete first halo, then real endpoints of every halo facet. Only the
   // first halo changes; second-halo CC/V comes from its complete current CF.
   std::set<label> facets;for(label c:affected){if(!local(c)||cf_->at(c).size()<4||cf_->at(c).size()>512)return no("grid-pair-outside-current-CF");facets.insert(cf_->at(c).begin(),cf_->at(c).end());}
   for(label f:facets){const auto r=face(f);if(coupled(r.patch))return no("grid-pair-real-coupled-peer-unknown");for(label c:{r.owner,r.neighbour})if(c>=0)support.insert(c);}
   if(support.size()>128||facets.size()>4096)return no("grid-pair-full-peer-bound");
   for(label c:support){if(!local(c)||cf_->at(c).size()<4||cf_->at(c).size()>512)return no("grid-pair-real-peer-CF");p.beforeCF_.emplace(c,cf_->at(c));p.beforeRanks_.emplace(c,mesh_->cellRank.at(c));
    std::vector<Q::Face> rows;for(label f:cf_->at(c)){if(compositePhysicalFaces.count(f))return no("grid-pair-composite-Source-context-unknown");const auto r=face(f);if(coupled(r.patch))return no("grid-pair-peer-coupled-unknown");rows.push_back(r);p.beforeFaces_.emplace(f,r);p.beforeBases_.emplace(f,base_->at(f));for(label id:r.ring)p.points_.emplace(id,mesh_->points.at(id));}
    if(p.beforeFaces_.size()>8192||p.points_.size()>32768)return no("grid-pair-complete-provider-inventory-bound");
    const auto measured=Q::measure(c,rows,mesh_->points,metric,coupled,work,false,false);if(!measured.finite)return no("grid-pair-old-real-geometry");p.beforeCells_.emplace(c,measured);
   }
   for(label f:p.outer_){auto row=face(f);if(componentKeeper(row.owner)>=0)row.owner=componentKeeper(row.owner);if(componentKeeper(row.neighbour)>=0)row.neighbour=componentKeeper(row.neighbour);
    if(row.neighbour>=0&&row.owner>row.neighbour){std::swap(row.owner,row.neighbour);std::reverse(row.ring.begin()+1,row.ring.end());}p.afterFaces_.emplace(f,std::move(row));}
   for(const auto& group:p.representations_){for(label f:group.members)if(!p.afterFaces_.count(f)){
     auto row=face(f);if(componentKeeper(row.owner)>=0)row.owner=componentKeeper(row.owner);if(componentKeeper(row.neighbour)>=0)row.neighbour=componentKeeper(row.neighbour);
     p.afterFaces_.emplace(f,std::move(row));}
    auto carrier=p.afterFaces_.at(group.carrier);carrier.ring=group.outline;
    if(carrier.owner!=group.host)std::reverse(carrier.ring.begin()+1,carrier.ring.end());p.afterFaces_[group.carrier]=std::move(carrier);
    for(label f:group.members)if(f!=group.carrier){p.afterFaces_.erase(f);p.retired_.push_back(f);}
    for(label c:{group.host,group.outside})if(c>=0&&!repairedHost(c)){auto found=p.afterCF_.find(c);
     if(found==p.afterCF_.end())found=p.afterCF_.emplace(c,cf_->at(c)).first;
     auto& rows=found->second;rows.erase(std::remove_if(rows.begin(),rows.end(),[&](label f)
      {const auto at=p.representedBy_.find(f);return at!=p.representedBy_.end()&&at->second!=f;}),rows.end());}
   }
   std::set<std::pair<label,label>> checkedPairs;
   for(const auto& group:p.representations_)if(group.mateSource>=0){const auto pair=std::minmax(group.source,group.mateSource);
    if(!checkedPairs.emplace(pair.first,pair.second).second)continue;
    std::map<std::vector<uint64_t>,label> expected,actual;
    for(unsigned side=0;side<2;++side){const label source=side?pair.second:pair.first;auto& target=side?actual:expected;
     for(label f=head_.at(source);f>=0;f=next_.at(f)){if(!Q::workAvailable(work))return no("clock");if(!mesh_->faceAlive.at(f))continue;
      const auto represented=p.representedBy_.find(f);if(represented!=p.representedBy_.end()&&represented->second!=f)continue;
      const auto changed=p.afterFaces_.find(f);const auto row=changed==p.afterFaces_.end()?face(f):changed->second;
      if(row.neighbour!=-1||row.patch!=s.faces[source].patch||row.zone!=s.faces[source].zone)return no("outer-paired-future-role");
      std::vector<uint64_t> key;if(!currentRingKey(row.ring,side==0,key)||!target.emplace(std::move(key),f).second)
       return no("outer-paired-future-ring-ambiguous");}
    }
    if(expected.empty()||expected.size()!=actual.size())return no("outer-paired-future-cohort-size");
    auto x=expected.begin(),y=actual.begin();for(;x!=expected.end();++x,++y)
     if(x->first!=y->first)return no("outer-paired-future-cohort-reverse");
   }
   p.outer_.erase(std::remove_if(p.outer_.begin(),p.outer_.end(),[&](label f)
    {const auto at=p.representedBy_.find(f);return at!=p.representedBy_.end()&&at->second!=f;}),p.outer_.end());
   p.afterCF_[p.keep_].clear();if(p.mateKeep_>=0)p.afterCF_[p.mateKeep_].clear();
   for(label f:p.outer_){const auto& row=p.afterFaces_.at(f);for(label c:{row.owner,row.neighbour})
    if(repairedHost(c))p.afterCF_[c].push_back(f);}
   p.outer_=p.afterCF_.at(p.keep_);
   const auto rowsFor=[&](label c,const Ring& faces)->std::vector<Q::Face>{const auto planned=p.afterCF_.find(c);
    const Ring& ids=planned==p.afterCF_.end()?faces:planned->second;std::vector<Q::Face> rows;rows.reserve(ids.size());
    for(label f:ids){auto changed=p.afterFaces_.find(f);rows.push_back(changed==p.afterFaces_.end()?face(f):changed->second);}return rows;};
   X::Moment sum,actual;for(label c:p.members_){std::vector<Q::Face> oldRows;for(label f:cf_->at(c))oldRows.push_back(face(f));X::Moment v;if(!exact.moments(oldRows,c,v))return no("grid-pair-old-exact-shell");X::add(sum,v);}
   if(!exact.moments(rowsFor(p.keep_,p.outer_),p.keep_,actual)||!X::same(sum,actual))return no("grid-pair-exact-union-6V24M");
   if(p.mateKeep_>=0){X::Moment oldMate,newMate;for(label c:p.mateMembers_){std::vector<Q::Face> rows;
     for(label f:cf_->at(c))rows.push_back(face(f));X::Moment v;if(!exact.moments(rows,c,v))return no("outer-paired-old-exact-shell");X::add(oldMate,v);}
    if(!exact.moments(rowsFor(p.mateKeep_,p.afterCF_.at(p.mateKeep_)),p.mateKeep_,newMate)||!X::same(oldMate,newMate))
     return no("outer-paired-exact-independent-union-6V24M");}
   bool improved=false;for(label c:p.members_)improved=improved||p.beforeCells_.at(c).determinant<.001||p.beforeCells_.at(c).aspect>1000;
   const bool nativeWRRepair=mode==Mode::CartesianCrossGrid&&!improved;
   if(nativeWRRepair){
    bool sharedDebt=false;
    for(label f:shared){if(!Q::workAvailable(work))return no("clock");const auto old=face(f);
     if(base_->at(f)<0||old.patch!=-1||old.zone!=-1||old.neighbour<0)continue;
     const auto& own=p.beforeCells_.at(old.owner);const auto& nei=p.beforeCells_.at(old.neighbour);
     const auto q=Q::facet(old,mesh_->points,metric,own,&nei,work);
     if(!q.numeric)return no("grid-pair-native-facet-old-numeric");
     p.beforeFacets_.emplace(f,q);
     sharedDebt=sharedDebt||!passes(q.v[1],1)||!passes(q.v[2],2);
    }
    if(!sharedDebt)return no("grid-pair-no-D-AR-debt");
   }
   for(label c:support){if(retiredCell(c))continue;const auto rows=rowsFor(c,c==p.keep_?p.outer_:cf_->at(c));
    auto neo=Q::measure(c,rows,mesh_->points,metric,coupled,work,affected.count(c),repairedHost(c));
    if(!neo.finite||(affected.count(c)&&!neo.strictGeometry)){observePositive(p,c,neo);return no(neo.reason?neo.reason:"grid-pair-full-future-Stock");}
    if(repairedHost(c)&&(neo.determinant<.001||neo.aspect>1000))return no("grid-pair-host-D-AR");
    const auto& old=p.beforeCells_.at(c);
    if(affected.count(c)&&!repairedHost(c)){std::vector<Q::Face> oldRows;for(label f:cf_->at(c))oldRows.push_back(face(f));X::Moment beforeMoment,afterMoment;
     if(!exact.moments(oldRows,c,beforeMoment)||!exact.moments(rows,c,afterMoment)||!X::same(beforeMoment,afterMoment))return no("grid-pair-outside-exact-6V24M");
     scalar peerSpan=1e-6;for(const auto& r:rows)for(label id:r.ring)peerSpan=max(peerSpan,mag(mesh_->points[id]-old.centre));
     if(mag(neo.volume-old.volume)/max(old.volume,VSMALL)>1e-10||mag(neo.volume*(neo.centre-old.centre))/max(old.volume*peerSpan,VSMALL)>1e-10)return no("grid-pair-outside-OF-volume-moment");}
    if(!inherited(old.determinant,neo.determinant,0)||(old.aspect<=1000?neo.aspect>1000:neo.aspect>old.aspect*(1+1e-10)))return no("grid-pair-outside-D-AR-inheritance");
    p.afterCells_.emplace(c,std::move(neo));
   }
   scalar volume=0;point moment=point::zero;for(label c:p.members_){const auto& old=p.beforeCells_.at(c);volume+=old.volume;moment+=old.volume*old.centre;}
   const auto& host=p.afterCells_.at(p.keep_);scalar span=1e-6;for(label f:p.outer_)for(label id:p.afterFaces_.at(f).ring)span=max(span,mag(mesh_->points[id]-host.centre));
   if(mag(host.volume-volume)/max(volume,VSMALL)>1e-10||mag(host.volume*host.centre-moment)/max(volume*span,VSMALL)>1e-10)return no("grid-pair-OF-volume-first-moment");
   if(p.mateKeep_>=0){scalar v=0;point mom=point::zero;for(label c:p.mateMembers_){const auto& old=p.beforeCells_.at(c);v+=old.volume;mom+=old.volume*old.centre;}
    const auto& neo=p.afterCells_.at(p.mateKeep_);scalar width=1e-6;for(label f:p.afterCF_.at(p.mateKeep_))for(label id:p.afterFaces_.at(f).ring)
     width=max(width,mag(mesh_->points[id]-neo.centre));
    if(mag(neo.volume-v)/max(v,VSMALL)>1e-10||mag(neo.volume*neo.centre-mom)/max(v*width,VSMALL)>1e-10)
      return no("outer-paired-OF-independent-volume-first-moment");}
   std::array<bool,4> deletedPassing{{false,false,false,false}},mateDeletedPassing{{false,false,false,false}};
   for(label f:facets){const auto old=face(f);const auto& oo=p.beforeCells_.at(old.owner);const Q::Cell* on=old.neighbour>=0?&p.beforeCells_.at(old.neighbour):nullptr;
    // Only the new no-DAR native-interface role can already own this exact
    // before measurement. Default DAR and SameOrigin retain the old call.
    const auto cached=p.beforeFacets_.find(f);
    auto oq=nativeWRRepair&&cached!=p.beforeFacets_.end()?cached->second:Q::facet(old,mesh_->points,metric,oo,on,work);
    if(!oq.numeric)return no("grid-pair-old-facet-numeric");p.beforeFacets_.emplace(f,oq);
    if(shared.count(f)){auto& passing=componentKeeper(old.owner)==p.mateKeep_?mateDeletedPassing:deletedPassing;
     for(unsigned k=1;k<5;++k)passing[k-1]=passing[k-1]||passes(oq.v[k],k);
     if(mode==Mode::SameOriginGeneratedSide)improved=improved||oq.v[1]<.05||oq.v[2]<.01;continue;}
    const auto represented=p.representedBy_.find(f);const label carrier=represented==p.representedBy_.end()?f:represented->second;
    const auto change=p.afterFaces_.find(carrier);const auto neo=change==p.afterFaces_.end()?old:change->second;
    const auto& newOwner=p.afterCells_.at(neo.owner);const Q::Cell* nn=neo.neighbour>=0?&p.afterCells_.at(neo.neighbour):nullptr;
    auto nq=Q::facet(neo,mesh_->points,metric,newOwner,nn,work);if(!nq.numeric||!nq.valid)return no(nq.reason?nq.reason:"grid-pair-all-real-facet-tets");
    for(unsigned k=1;k<5;++k)if(!inherited(oq.v[k],nq.v[k],k))return no("grid-pair-W-R-S-N-inheritance");p.afterFacets_.emplace(carrier,nq);
    vector sf;point fc;metric(neo.ring,sf,fc);const vector normal=sf/(mag(sf)+ROOTVSMALL);const scalar maxSin=std::sin(80*constant::mathematical::pi/180);
    vector previous=mesh_->points[neo.ring.front()]-mesh_->points[neo.ring.back()];scalar previousLength=mag(previous);previous/=previousLength+ROOTVSMALL;
    for(std::size_t j=0;j<neo.ring.size();++j){if(!Q::workAvailable(work))return no("clock");vector edge=mesh_->points[neo.ring[(j+1)%neo.ring.size()]]-mesh_->points[neo.ring[j]];scalar length=mag(edge);edge/=length+ROOTVSMALL;
     if(previousLength>SMALL&&length>SMALL){vector turn=previous^edge;const scalar sine=mag(turn);if(sine>=maxSin){turn/=sine;if((turn&normal)<SMALL)return no("grid-pair-face-angle");}}previous=edge;previousLength=length;}
   }
   for(label c:{p.keep_,p.mateKeep_})if(c>=0){const auto& passing=c==p.keep_?deletedPassing:mateDeletedPassing;
    for(label f:p.afterCF_.at(c)){const auto& q=p.afterFacets_.at(f);for(unsigned k=1;k<5;++k)
     if(passing[k-1]&&!passes(q.v[k],k))return no("grid-pair-consumed-separator-role-inheritance");}}
   if(mode==Mode::SameOriginGeneratedSide&&!improved)return no("same-origin-no-D-AR-W-R-debt");
   if(nativeWRRepair){
    // Complete affected original/future W/R debt, not just a removed seed.
    // All preceding Source, strict host/outside, V/M and inherited role gates
    // have passed. Only legal shared/internal retirement can remove a face.
    std::array<std::size_t,2> oldBad{{0,0}},newBad{{0,0}};
    std::array<scalar,2> oldDebt{{0,0}},newDebt{{0,0}};
    const auto addDebt=[&](const Q::FacetQuality& q,std::array<std::size_t,2>& bad,
                          std::array<scalar,2>& debt)->bool {
     if(!q.numeric)return false;
     for(std::size_t k=0;k<2;++k)if(!passes(q.v[k+1],k+1)){
      ++bad[k];const scalar threshold=Q::thresholds()[k+1];
      debt[k]+=(threshold-q.v[k+1])/threshold;if(!std::isfinite(debt[k]))return false;
     }return true;
    };
    for(label f:facets){if(!Q::workAvailable(work))return no("clock");
     const auto q=p.beforeFacets_.find(f);if(q==p.beforeFacets_.end()||!addDebt(q->second,oldBad,oldDebt))
       return no("grid-pair-native-facet-full-old-debt");
    }
    std::set<label> finalFacets;
    for(label c:affected){if(retiredCell(c))continue;if(!Q::workAvailable(work))return no("clock");
     const auto future=p.afterCF_.find(c);const Ring& ids=future==p.afterCF_.end()?cf_->at(c):future->second;
     finalFacets.insert(ids.begin(),ids.end());
    }
    for(label f:finalFacets){if(!Q::workAvailable(work))return no("clock");
     const auto q=p.afterFacets_.find(f);if(q==p.afterFacets_.end()||!addDebt(q->second,newBad,newDebt))
       return no("grid-pair-native-facet-full-future-debt");
    }
    bool strictlyImproved=false;
    for(std::size_t k=0;k<2;++k){
     if(newBad[k]>oldBad[k]||newDebt[k]>oldDebt[k])return no("grid-pair-native-facet-debt-regression");
     strictlyImproved=strictlyImproved||newBad[k]<oldBad[k]||newDebt[k]<oldDebt[k];
    }
    if(!strictlyImproved)return no("grid-pair-native-facet-no-strict-improvement");
   }
   // Independent typed preflight. No reuse of LegacyComponentCommit and no
   // revocation followed by geometric publication on failed permission.
   const std::function<bool(const std::vector<label>&)> permit=[&](const std::vector<label>& roots){return permission(authority,roots);};
   if(!mesh_->completeCavityLineage.prepareReplacement(p.members_,{p.keep_},permit,work,p.replacement_))return no("grid-pair-lineage-prepare");
   if(p.mateKeep_>=0&&!mesh_->completeCavityLineage.prepareReplacement(p.mateMembers_,{p.mateKeep_},permit,work,p.mateReplacement_))
    return no("outer-paired-independent-lineage-prepare");
   for(const auto& row:p.afterCF_){if(retiredCell(row.first)||!local(row.first)||row.second.size()<4||row.second.size()>512)return no("outer-future-CF-scope");
    std::set<label> unique;for(label f:row.second){if(!unique.insert(f).second)return no("outer-future-CF-duplicate");
     const auto changed=p.afterFaces_.find(f);const auto faceRow=changed==p.afterFaces_.end()?face(f):changed->second;
     if(faceRow.owner!=row.first&&faceRow.neighbour!=row.first)return no("outer-future-CF-incidence");}}
   if(!legacyInstalledCoupledLiteral(p,work,why))return false;
   for(const auto& row:p.afterFaces_)p.publishRings_.emplace(row.first,row.second.ring);
   if(!live(work))return no("grid-pair-fresh-phase-or-clock");p.ready_=true;out=std::move(p);return true;
  }catch(const std::bad_alloc&){return no("grid-pair-allocation");}catch(const std::length_error&){return no("grid-pair-capacity");}
   catch(const X::X::Refusal&){return no("grid-pair-exact-or-clock");}catch(...){return no("grid-pair-input-role-or-authority-unknown");}
 }

 bool commit(Prepared& p,const Authority& authority,const Q::Available& work,const char** why=nullptr) {
  const auto no=[&](const char* s){if(why)*why=s;return false;};
  try {
   if(readOnlyScope_||jointPublicationActive_||!p.ready_||p.committed_||p.phase_!=this||p.revision_!=revision_||p.lineageRevision_!=lineageRevision_||!live(work)
    ||journal_.size()==journal_.capacity()||p.snapshot_.get()!=authority.sourceSnapshot.get()||!sameFingerprint(p.fingerprint_,authority.sourceFingerprint)
    ||!authority.sourceSnapshotIsRecorded||!authority.sourceSnapshotIsRecorded(p.snapshot_.get()))return no("grid-pair-stale-journal");
   for(const auto& row:p.beforeCF_)if(!mesh_->cellAlive.at(row.first)||mesh_->cellRank.at(row.first)!=p.beforeRanks_.at(row.first)||cf_->at(row.first)!=row.second)return no("grid-pair-stale-current-CF-or-cell-role");
    for(const auto& row:p.beforeBases_)if(base_->at(row.first)!=row.second)return no("grid-pair-stale-original-source-index");
   for(const auto& row:p.beforeFaces_)if(!mesh_->faceAlive.at(row.first)||!sameFace(row.second,face(row.first)))return no("grid-pair-stale-current-face");
   for(const auto& v:p.points_)if(!samePoint(v.second,mesh_->points.at(v.first)))return no("grid-pair-stale-point-bits");
   for(const auto& row:p.pairedRoles_)if(row.first<0||std::size_t(row.first)>=mesh_->physicalWallPatchPairs.size()
     ||mesh_->physicalWallPatchPairs[row.first]!=row.second)return no("outer-stale-paired-producer-role");
   if(!legacyInstalledCoupledLiteral(p,work,why))return false;
   const std::function<bool(const std::vector<label>&)> permit=[&](const std::vector<label>& roots){return permission(authority,roots);};
    if(p.mateKeep_>=0){if(!mesh_->completeCavityLineage.commitIndependentSameOriginPair(p.replacement_,p.mateReplacement_,permit,work))
      return no("outer-paired-independent-lineage-commit-refused");}
    else if(!mesh_->completeCavityLineage.commitReplacement(p.replacement_,permit,work))return no("grid-pair-lineage-commit-refused");
  }catch(...){return no("grid-pair-commit-preflight-unknown");}
  // Every owner buffer was prepared before the final lineage authority/clock.
  // Publication is outside the observation/preflight catch. No callback or
  // checked-index operation may conceal a partial geometric publication.
  for(label f:p.retired_)mesh_->faceAlive[f]=false;
   if(mesh_->writerBaseline)for(const auto& representation:p.representedBy_)
       mesh_->writerBaseline->recordFaceCarrier(representation.first,representation.second);
   if(mesh_->writerBaseline)mesh_->writerBaseline->recordRetirement(p.retire_,p.keep_);
   mesh_->cellAlive[p.retire_]=false;(*cf_)[p.retire_].clear();
   if(p.mateRetire_>=0){if(mesh_->writerBaseline)mesh_->writerBaseline->recordRetirement(p.mateRetire_,p.mateKeep_);
       mesh_->cellAlive[p.mateRetire_]=false;(*cf_)[p.mateRetire_].clear();}
  for(auto& row:p.afterCF_)(*cf_)[row.first].swap(row.second);
  for(const auto& entry:p.afterFaces_){const label f=entry.first;const auto& row=entry.second;mesh_->owner[f]=row.owner;mesh_->neighbour[f]=row.neighbour;}
  for(auto& entry:p.publishRings_)mesh_->faces[entry.first].swap(entry.second);
  // A published ring touching any recorded AP retires this candidate cache.
  // The next complete phase rebuilds actual incidence; never guess new rings.
  if(apIncidenceReady_)for(const auto& row:p.publishRings_){
   const auto touches=[&](const Ring& ring){for(label id:ring)
     if(id>=0&&std::size_t(id)<apBirthSlot_.size()&&apBirthSlot_[id]!=-1)return true;return false;};
   const auto prior=p.beforeFaces_.find(row.first);
   if(touches(mesh_->faces.at(row.first))||touches(row.second)
     ||(prior!=p.beforeFaces_.end()&&touches(prior->second.ring)))
    {apIncidenceReady_=false;break;}
  }
  dcCartPreparedFacetBirth::invalidateCurrentIncidence(*mesh_);
  ++revision_;lineageRevision_=mesh_->completeCavityLineage.revision();p.ready_=false;p.committed_=true;
  static_assert(std::is_nothrow_move_constructible<Prepared>::value,"journal publication must not allocate");journal_.push_back(std::move(p));return true;
 }
};
inline JointPublication::~JointPublication()noexcept {
 lease_.cancel();
 if(owner_&&ready_&&!published_)owner_->jointPublicationActive_=false;
}
} }
#endif
