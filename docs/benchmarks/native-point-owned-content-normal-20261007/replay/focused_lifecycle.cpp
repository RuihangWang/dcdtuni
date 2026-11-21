#include "dcCartPhysicalPointLifecycle.H"
#include "Time.H"
#include "IStringStream.H"
#include <iostream>
#include <iomanip>
#include <fstream>
using namespace Foam;
namespace O=dcCartCompleteCavityLocalOracle;
namespace L=dcCartPhysicalPointLifecycle;
namespace E=dcCartExactBinary64Geometry;
namespace W=dcCartWeightUnionPreview;
static int passed=0,failed=0;
static void check(const char* name,bool ok){std::cout<<"{\"test\":\""<<name<<"\",\"pass\":"<<(ok?"true":"false")<<"}\n";if(ok)++passed;else++failed;}
static void cf(const dcCartCutCells::cutMesh& r,std::vector<std::vector<label>>& out)
{out.assign(r.nCells,{});for(label f=0;f<label(r.faces.size());++f)if(r.faceAlive[f])
 {if(r.owner[f]>=0)out[r.owner[f]].push_back(f);if(r.neighbour[f]>=0)out[r.neighbour[f]].push_back(f);}}
int main(int argc,char** argv)
{
 if(argc!=2)return 2;
 dcCartCutCells::cutMesh mother;mother.nCells=2;mother.cellAlive.assign(2,true);mother.cellOrigAlive.assign(2,true);
 mother.cellRank.assign(2,Pstream::myProcNo());mother.cellLevel.assign(2,0);mother.cellLeaf={0,1};mother.cellPiece={0,0};
 for(label x=0;x<=2;++x){mother.points.emplace_back(x,0,0);mother.points.emplace_back(x,1,0);mother.points.emplace_back(x,1,1);mother.points.emplace_back(x,0,1);}
 mother.points.emplace_back(1,.00001,0);mother.nGridPoints=0;
 auto add=[&](std::initializer_list<label> ring,label o,label n=-1){mother.faces.emplace_back(ring);mother.owner.push_back(o);mother.neighbour.push_back(n);mother.patch.push_back(n>=0?-1:0);mother.zone.push_back(-1);mother.faceAlive.push_back(true);};
 add({4,12,5,6,7},0,1);add({0,3,2,1},0);add({8,9,10,11},1);
 add({0,4,7,3},0);add({1,2,6,5},0);add({0,1,5,12,4},0);add({3,7,6,2},0);
 add({4,8,11,7},1);add({5,6,10,9},1);add({4,12,5,9,8},1);add({7,11,10,6},1);
 const std::string sha(64,'a');
 std::string dt="dcdtuniPointRepairSourceRoleContract { version 1; eligible true; scope globallyNoBulkSourcesAndNoMaterialDomains; rawSha256 \""+sha+"\"; rolesDigest \""+sha+"\"; reasons (); }\n"
 "dcdtuniWeightUnionSourceContract { version 1; eligible true; scope globalSinglePhaseAirMaterial; rawSha256 \""+sha+"\"; rolesDigest \""+sha+"\"; materialKey \"controlled-air\"; materialDigest \""+sha+"\"; producerDigest \""+sha+"\"; boundaryMembershipDigest \""+sha+"\"; ownershipPolicy adjacentFinalCarvedFluidCells; reasons (); }\n";
 IStringStream input(dt);dictionary dict(input);wordList names(1),types(1),zones;names[0]="controlledWall";types[0]="wall";
 scalar remainingSeconds=100;clockTime jobClock;
 auto remaining=[&](){return remainingSeconds-jobClock.elapsedTime();};
 auto snapshot=dcCartPostCarveFluidOwnershipPreview::capture(mother,dict,names,types,zones,Pstream::myProcNo(),true,remaining);
 check("actualFreshPostCarveFactory",snapshot&&snapshot->complete&&snapshot->completedFluidOnlyPath);
 auto pair=dcCartWeightUnionSourceContract::readAuthority(dict,dcCartPostCarveFluidOwnershipPreview::verifier(snapshot),remaining,[snapshot](label p){return dcCartPostCarveFluidOwnershipPreview::origin(snapshot,p);});
 auto authority=dcCartCompleteCavityAuthority::read(dict,snapshot,pair,remaining);
 check("actualSixDigestAuthoritySameInstance",authority.ready()&&authority.sourceSnapshot.get()==snapshot.get());
#ifdef NORMAL_DRIVER
 auto native=mother;native.cellRefVol.assign(2,1);native.cellRefCentre={point(.5,.5,.5),point(1.5,.5,.5)};
 auto time=Time::New(fileName(argv[1]));wordList neighbours(1),groups(1);
 neighbours[0]=word::null;groups[0]=word::null;
 const bool requested=getenv("CONTROL_PHYSICAL")&&std::string(getenv("CONTROL_PHYSICAL"))=="1";
#ifdef REFERENCE_ABI
 dcCartCutCells::decomposeConformingCones(native,names,types,neighbours,Pstream::myProcNo(),*time,zones,groups,pair,authority,requested);
#else
 dcCartCutCells::decomposeConformingCones(native,names,types,neighbours,Pstream::myProcNo(),*time,zones,groups,pair,authority,requested);
#endif
 check("directNormalCEarliestPhysicalCommitProduced",native.completeCavityContext&&bool(native.completeCavityContext->physicalPointJournal)==requested);
 check("directNormalCSourceLineageStillActive",native.completeCavityLineage.active());
 check("directNormalCEarliestNativePointUnchanged",native.writerBaseline&&O::bits(native.writerBaseline->checkpoint->points[12],mother.points[12]));
 check("directNormalCCurrentPointActuallyChanged",(!O::bits(native.points[12],mother.points[12]))==requested);
 auto final=dcCartCutCells::toPolyMesh(native,*time,polyMesh::defaultRegion,names,types,zones,neighbours,groups);
 final->cellCentres();final->cellVolumes();final->faceAreas();final->faceCentres();
 if(const char* output=getenv("CONTROL_OUTPUT"))
 {
  std::ofstream out(output,std::ios::binary);auto labelOut=[&](label x){out.write(reinterpret_cast<const char*>(&x),sizeof(x));};
  auto scalarOut=[&](scalar x){out.write(reinterpret_cast<const char*>(&x),sizeof(x));};
  auto pointOut=[&](const point& x){scalarOut(x.x());scalarOut(x.y());scalarOut(x.z());};
  labelOut(final->nPoints());labelOut(final->nFaces());labelOut(final->nInternalFaces());labelOut(final->nCells());
  for(const auto& p:final->points())pointOut(p);
  for(label f=0;f<final->nFaces();++f)
  {
   const auto& ring=final->faces()[f];labelOut(ring.size());for(label p:ring)labelOut(p);
   labelOut(final->faceOwner()[f]);labelOut(f<final->nInternalFaces()?final->faceNeighbour()[f]:-1);
   pointOut(final->faceAreas()[f]);pointOut(final->faceCentres()[f]);
   const auto q=W::metrics(*final,f);for(scalar v:q)scalarOut(v);
  }
  for(label c=0;c<final->nCells();++c){pointOut(final->cellCentres()[c]);scalarOut(final->cellVolumes()[c]);scalarOut(W::determinant(*final,c));}
  labelOut(final->boundaryMesh().size());for(const auto& patch:final->boundaryMesh())
   {const auto role=W::patchRole(patch);labelOut(role.size());out.write(role.data(),role.size());labelOut(patch.size());}
  out.flush();check("normalActualFieldsRecorded",out.good());
 }
 std::cout<<"{\"directNormalCells\":"<<final->nCells()<<",\"pointWriterComplete\":true}\n";
 std::cout<<"{\"summary\":true,\"passed\":"<<passed<<",\"failed\":"<<failed<<"}\n";return failed?1:0;
#else
 dcCartCutCells::cutMesh raw;raw.points=mother.points;raw.nCells=2;raw.cellAlive.assign(2,false);raw.cellOrigAlive.assign(2,false);
 raw.cellRank=mother.cellRank;raw.cellLevel=mother.cellLevel;raw.cellLeaf=mother.cellLeaf;raw.cellPiece=mother.cellPiece;raw.nGridPoints=0;
 raw.completeCavityLineage.initialize(2,authority.recordedParent,[&](){return O::available(authority);});
 std::vector<label> creator,parents(2,-1);
 std::map<std::vector<label>,label> faces;
 std::vector<label> ap;
 for(label p=0;p<2;++p){ap.push_back(raw.points.size());raw.points.emplace_back(p+.5,.5,.5);}
 E::Context exact([&](){return O::available(authority);});
 auto worldLess=[&](label a,label b){const auto& x=raw.points[a];const auto& y=raw.points[b];return std::make_tuple(x.x(),x.y(),x.z())<std::make_tuple(y.x(),y.y(),y.z());};
 for(label f=0;f<label(mother.faces.size());++f)
 {
  const auto& ring=mother.faces[f];std::vector<label> anchors=ring;std::sort(anchors.begin(),anchors.end(),worldLess);
  label anchor=-1;
  for(label candidate:anchors)
  {
   auto pos=std::find(ring.begin(),ring.end(),candidate)-ring.begin();bool ok=true;
   for(std::size_t j=1;j+1<ring.size();++j)
    if(L::collinear(raw.points[candidate],raw.points[ring[(pos+j)%ring.size()]],raw.points[ring[(pos+j+1)%ring.size()]],exact))ok=false;
   if(ok){anchor=pos;break;}
  }
  if(anchor<0){std::cerr<<"no legal fan\n";return 3;}
  for(label parent:{mother.owner[f],mother.neighbour[f]})if(parent>=0)
   for(std::size_t j=1;j+1<ring.size();++j)
   {
    std::vector<label> tri{ring[anchor],ring[(anchor+j)%ring.size()],ring[(anchor+j+1)%ring.size()]};
    if(parent!=mother.owner[f])std::reverse(tri.begin(),tri.end());
    const label c=raw.nCells++;raw.cellAlive.push_back(true);raw.cellOrigAlive.push_back(false);
    raw.cellRank.push_back(Pstream::myProcNo());raw.cellLevel.push_back(0);raw.cellLeaf.push_back(parent);raw.cellPiece.push_back(c);
    raw.completeCavityLineage.inherit(c,parent,[&](){return O::available(authority);});parents.push_back(parent);
    const label a=tri[0],b=tri[1],d=tri[2],p=ap[parent];
    std::vector<std::vector<label>> shell{tri,{p,b,a},{p,d,b},{p,a,d}};
    for(std::size_t k=0;k<shell.size();++k)
    {
     auto key=shell[k];std::sort(key.begin(),key.end());auto prior=faces.find(key);
     if(prior==faces.end())
     {
      const label id=raw.faces.size();faces.emplace(key,id);raw.faces.push_back(shell[k]);raw.owner.push_back(c);raw.neighbour.push_back(-1);
      raw.patch.push_back(k==0?mother.patch[f]:-1);raw.zone.push_back(-1);raw.faceAlive.push_back(true);creator.push_back(k==0?f:-1);
     }
     else {const label id=prior->second;raw.neighbour[id]=c;raw.patch[id]=-1;if(k==0&&creator[id]!=f)return 4;}
    }
   }
 }
 raw.points.emplace_back(99,99,99); // unused global alias input remains under the literal old-point gate
 auto lowered=raw;dcCartCutCells::writerNumericData numeric;dcCartCutCells::writerBindings bindings;
 dcCartCutCells::lowerWriterNumeric(lowered,names,types,zones,numeric,&bindings);
 auto time=Time::New(fileName(argv[1]));
 auto mesh=std::shared_ptr<polyMesh>(new polyMesh(IOobject("controlledPhysicalPointEarliest","constant",*time,IOobject::NO_READ,IOobject::NO_WRITE,false),
  std::move(numeric.points),std::move(numeric.faces),std::move(numeric.owner),std::move(numeric.neighbour),false));
 List<polyPatch*> patches(1);patches[0]=polyPatch::New(word("wall"),names[0],numeric.nF-numeric.nInt,numeric.nInt,0,mesh->boundaryMesh()).ptr();mesh->addPatches(patches,false);
 mesh->cellCentres();mesh->cellVolumes();mesh->faceAreas();mesh->faceCentres();
 auto checkpoint=std::make_shared<dcCartCutCells::cutMesh>(raw);
 raw.writerBaseline=std::make_shared<dcCartCutCells::writerCoupledBaseline>();raw.writerBaseline->checkpoint=checkpoint;
 std::vector<std::vector<label>> cellFaces;cf(raw,cellFaces);
 O::Catalogue cat;O::captureCatalogue(raw,*mesh,bindings,authority,cellFaces,cat);
 std::vector<label> retained=creator;
 check("normalJournalInitializedBeforePhysical",cat.legacyJournal&&cat.legacyJournal->complete&&cat.legacyJournal->commits.empty());
 check("normalFacetSourcesCopiedWithoutMovingCreator",O::retainNormalFacetSources(raw,retained,authority,cat)&&retained.empty()&&creator.size()==raw.faces.size());
 check("realWriterNonidentityNativeCellMap",bindings.cell[0]<0&&bindings.cell[1]<0&&bindings.cell[2]==0);
 W::State seeds;seeds.enabled=true;seeds.dEnabled=true;seeds.authority=pair; // clock starts before physical stage
 W::State oldSeeds=seeds;W::capture(oldSeeds,raw,*mesh,bindings,parents,authority.normalOriginFromSnapshot);
 labelList patchMap(1,0);L::Transaction transaction;
 check("ownedStageStartsBeforeCapture",transaction.begin(authority));
 check("sameStageCannotResetDeadline",!transaction.begin(authority));
 const auto discovery=L::discover(*mesh,transaction);
 check("actualDWorldGrammarIncludesPhysicalSubdivision",discovery.complete&&std::find(discovery.compactPoints.begin(),discovery.compactPoints.end(),bindings.point[12])!=discovery.compactPoints.end());
 bool captured=transaction.capture(raw,*mesh,bindings,authority,cat,patchMap,bindings.point[12]);
 check("completePhysicalStarNormalCapture",captured);
 std::cout<<"{\"captureRefusal\":"<<int(transaction.refusal())<<",\"oldSeedCount\":"<<oldSeeds.pool.size()<<"}\n";
 if(!captured)return 5;
 O::Values old;O::evaluate(transaction.before(),{},[&](){return O::available(authority);},old);
 point target;check("geometryDerivedUniqueCommonEdgeMidpoint",transaction.midpoint(target)&&target==point(1,.5,0));
 const point q=raw.points[12]+.25*(target-raw.points[12]);O::Values denied;
 check("oldGenericPhysicalOverrideStillRefused",!O::evaluate(transaction.before(),{{12,q}},[&](){return O::available(authority);},denied));
#ifdef RED_STALE
 auto onlyRaw=raw;onlyRaw.points[12]=q;O::NormalIndex staleIndex;O::produceIndex(onlyRaw,*mesh,bindings,authority,staleIndex);O::View failedView;
 check("rawOnlyPublicationMustHaveRealCurrentInstalledBinding",
  O::capture(onlyRaw,*mesh,bindings,authority,transaction.before().core,cellFaces,staleIndex,cat.epoch,failedView));
 std::cout<<"{\"summary\":true,\"passed\":"<<passed<<",\"failed\":"<<failed<<"}\n";return failed?1:0;
#else
 L::Statistics stats;bool local=transaction.local(q,stats);
 check("sourceCoverageStrongTetAndHardProgress",local);
 std::cout<<"{\"localRefusal\":"<<int(transaction.refusal())<<",\"Dold\":"<<stats.oldD<<",\"Dnew\":"<<stats.newD<<"}\n";
 if(!local)return 6;
 bool staged=transaction.stage(*time,names,types,zones,seeds,bindings,patchMap,creator,parents,stats);
 check("realWriterCurrentInstalledAndSeedStage",staged);
 std::cout<<"{\"stageRefusal\":"<<int(transaction.refusal())<<"}\n";
 if(!staged)return 7;
 const auto earliest=mesh;const auto oldPoint=raw.points[12];std::shared_ptr<L::Frame> current;
 bool committed=transaction.commit(raw,current);check("noThrowNativePointAndCurrentFrameCommit",committed&&current&&O::bits(raw.points[12],q));
 check("atomicCurrentCatalogueAndWDPublication",raw.completeCavityContext&&raw.weightUnionState
  &&raw.completeCavityContext->physicalPointJournal==current->physicalJournal&&raw.weightUnionState->pool.size()==current->seeds->pool.size());
 check("earliestOracleNotRefreshedOrReactivated",O::bits(earliest->points()[bindings.point[12]],oldPoint)&&!O::bits(earliest->points()[bindings.point[12]],q));
 check("currentInstalledNumericActuallyChanged",current&&O::bits(current->mesh->points()[bindings.point[12]],q)&&L::sameBindings(bindings,current->bindings));
 check("completeSourceLineageVersionUnchanged",raw.completeCavityLineage.active()&&raw.completeCavityLineage.revision()==lowered.completeCavityLineage.revision());
 std::cout<<"{\"seedComparison\":true,\"before\":"<<oldSeeds.pool.size()<<",\"after\":"<<current->seeds->pool.size()
  <<",\"originalClockElapsed\":"<<seeds.clock.elapsedTime()<<",\"newClockElapsed\":"<<current->seeds->clock.elapsedTime()<<"}\n";
 bool ceased=true;
 for(const auto& p:oldSeeds.pool)if(p.seedKind==W::SeedKind::D)
 {
  const scalar a=W::determinant(*current->mesh,current->bindings.cell[p.a]);
  const scalar b=W::determinant(*current->mesh,current->bindings.cell[p.b]);
  if(a<.001||b<.001)ceased=false;
 }
 check("removedActualDSeedNowBothEndpointsPass",!oldSeeds.pool.empty()&&current->seeds->pool.empty()&&ceased);
 O::NormalIndex actualCurrentIndex;O::produceIndex(raw,*current->mesh,current->bindings,authority,actualCurrentIndex);
 O::View publishedView;bool rebound=O::capture(raw,*current->mesh,current->bindings,authority,
  transaction.before().core,current->cellFaces,actualCurrentIndex,current->catalogue->epoch,publishedView);
 publishedView.legacyJournal=current->catalogue->legacyJournal;
 check("actualPublishedCompleteCurrentRebind",rebound);
 check("oldLiteralGlobalPointPredicateStillRefuses",!O::current(publishedView,raw,authority,0,current->cellFaces));
 publishedView.physicalPointJournal=current->physicalJournal;
 check("sealedNormalPointCommitCurrentAll12Admit",O::current(publishedView,raw,authority,0,current->cellFaces));
 const point unused=raw.points.back();raw.points.back().x()+=1;
 check("unknownGlobalPointStillLiteralRefusal",!O::current(publishedView,raw,authority,0,current->cellFaces));raw.points.back()=unused;
 const point known=raw.points[0];raw.points[0].x()+=.001;
 check("knownReadAliasMutationStillRefused",!O::current(publishedView,raw,authority,0,current->cellFaces));raw.points[0]=known;
 const auto savedRing=raw.faces[0];std::rotate(raw.faces[0].begin(),raw.faces[0].begin()+1,raw.faces[0].end());
 check("point0ReadRingMutationStillRefused",!O::current(publishedView,raw,authority,0,current->cellFaces));raw.faces[0]=savedRing;
 bool fresh=true;for(const auto& p:current->seeds->pool)for(const auto& f:p.faces)
  for(std::size_t k=0;k<f.ring.size();++k)if(!O::bits(f.rawCoordinates[k],raw.points[f.ring[k]]))fresh=false;
 check("actualWDSeedsUseCurrentRawCoordinates",fresh);
 check("overlappingUnionNeedsFullJointGuard",!transaction.disjointUnion(transaction.before().core,{}));
 check("disjointUnionNotBlanketVetoed",transaction.disjointUnion({1000000},{1000000}));
 // Actual normal lowering consumes the changed native recipe a second time;
 // final verification sees genuinely transformed owners/rings and nonidentity map.
 auto finalRaw=raw;dcCartCutCells::writerNumericData finalNumeric;dcCartCutCells::writerBindings finalMap;
 dcCartCutCells::lowerWriterNumeric(finalRaw,names,types,zones,finalNumeric,&finalMap);
 auto finalMesh=std::shared_ptr<polyMesh>(new polyMesh(IOobject("controlledPhysicalPointFinal","constant",*time,IOobject::NO_READ,IOobject::NO_WRITE,false),
   std::move(finalNumeric.points),std::move(finalNumeric.faces),std::move(finalNumeric.owner),std::move(finalNumeric.neighbour),false));
 List<polyPatch*> finalPatches(1);finalPatches[0]=polyPatch::New(word("wall"),names[0],finalNumeric.nF-finalNumeric.nInt,finalNumeric.nInt,0,finalMesh->boundaryMesh()).ptr();finalMesh->addPatches(finalPatches,false);
 // Journal is instance bound. The normal writer lowers the same object, so
 // swap only the actual lowered arrays into that instance for this control.
 raw.owner.swap(finalRaw.owner);raw.neighbour.swap(finalRaw.neighbour);raw.faces.swap(finalRaw.faces);
 check("sealedFinalRealWriterNonidentityInstalledAdmit",current->physicalJournal->verifyInstalled(raw,*finalMesh,finalMap));
 auto badMap=finalMap;badMap.alias[12]=0;
 check("finalAliasRepresentativeMutationRefused",!current->physicalJournal->verifyInstalled(raw,*finalMesh,badMap));
 auto role=raw.patch[0];raw.patch[0]=1;
 check("finalPhysicalPatchMutationRefused",!current->physicalJournal->verifyInstalled(raw,*finalMesh,finalMap));raw.patch[0]=role;
 auto ring=raw.faces[0];std::rotate(raw.faces[0].begin(),raw.faces[0].begin()+1,raw.faces[0].end());
 check("finalPoint0MutationRefused",!current->physicalJournal->verifyInstalled(raw,*finalMesh,finalMap));raw.faces[0]=ring;
 raw.owner.swap(finalRaw.owner);raw.neighbour.swap(finalRaw.neighbour);raw.faces.swap(finalRaw.faces);
 check("transactionCannotPublishTwice",!transaction.commit(raw,current));
 L::Transaction stale;check("newCurrentRequiresItsOwnOwnedStage",stale.begin(authority));
 check("expiredEarliestViewCannotActAsCurrent",!stale.capture(raw,*earliest,bindings,authority,cat,patchMap,bindings.point[12]));
 auto wrong=authority;wrong.sourceSnapshot=std::make_shared<dcCartPostCarveFluidOwnershipPreview::Snapshot>(*snapshot);
 L::Transaction foreign;foreign.begin(wrong);
 check("sameDigestForeignSourceInstanceRefused",!foreign.capture(raw,*current->mesh,current->bindings,wrong,*current->catalogue,patchMap,bindings.point[12]));
 auto staleStamp=authority;staleStamp.sourceFingerprint.producer=std::string(64,'b');
 check("sealedJournalFreshProducerDigestMismatchRefused",!O::current(publishedView,raw,staleStamp,0,current->cellFaces));
 auto malformed=transaction.newValues();malformed.determinant[1000000]=0;L::Statistics badStats;
 check("pointHardExtraDeterminantRoleRefused",!L::pointHard(transaction.oldValues(),malformed,badStats));
 const point savedFinal=raw.points[12];const auto previousContext=raw.completeCavityContext;
 check("preallocatedPointAndAllCurrentStateRollback",transaction.rollback(raw,current)&&!current
  &&O::bits(raw.points[12],oldPoint)&&!raw.completeCavityContext&&!raw.weightUnionState);
 check("rollbackCannotGrantSecondRollback",!transaction.rollback(raw,current));
 auto lostSources=cat;for(label id=0;id<label(raw.faces.size());++id)
  if(lostSources.normalFacetSources[id]==5){lostSources.normalFacetSources[id]=-1;break;}
 L::Transaction missing;missing.begin(authority);
 check("normalPhysicalCreatorMissingRefusesBeforeWrite",!missing.capture(raw,*earliest,bindings,authority,lostSources,patchMap,bindings.point[12])&&O::bits(raw.points[12],oldPoint));
 bool armMutation=false;
 auto mutating=authority;mutating.remainingSeconds=[&](){if(armMutation){raw.points.back().x()+=.25;armMutation=false;}return remaining();};
 L::Transaction callback;callback.begin(mutating);L::Statistics callbackStats;
 bool callbackPrepared=callback.capture(raw,*earliest,bindings,mutating,cat,patchMap,bindings.point[12])
  &&callback.local(q,callbackStats)&&callback.stage(*time,names,types,zones,seeds,bindings,patchMap,creator,parents,callbackStats);
 check("normalCommitCallbackNegativePreparedByRealAPI",callbackPrepared);
 const point beforeCallback=raw.points.back();armMutation=true;std::shared_ptr<L::Frame> refusedCurrent;
 check("lastCallbackInputMutationRefusesAtomicPublication",!callback.commit(raw,refusedCurrent)&&!refusedCurrent
  &&O::bits(raw.points[12],oldPoint)&&!raw.completeCavityContext&&!raw.weightUnionState);
 raw.points.back()=beforeCallback;
 L::Transaction timed;timed.begin(authority);L::Statistics timedStats;
 bool timedPrepared=timed.capture(raw,*earliest,bindings,authority,cat,patchMap,bindings.point[12])
  &&timed.local(q,timedStats)&&timed.stage(*time,names,types,zones,seeds,bindings,patchMap,creator,parents,timedStats);
 check("realExpiryNegativePreparedWithoutClockReset",timedPrepared);
 remainingSeconds=0;std::shared_ptr<L::Frame> expiredCurrent;
 check("expiredAfterStagingNoPointOrCurrentPublication",!timed.commit(raw,expiredCurrent)&&!expiredCurrent
  &&O::bits(raw.points[12],oldPoint)&&!raw.completeCavityContext&&!raw.weightUnionState);
 L::Transaction expired;
 check("realJobClockExpiredNoReset",!expired.begin(authority));
 std::cout<<"{\"summary\":true,\"passed\":"<<passed<<",\"failed\":"<<failed<<"}\n";return failed?1:0;
#endif
#endif
}
