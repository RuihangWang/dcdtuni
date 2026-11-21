
// Private controlled admission witness. No business mesh/quality acceptance.
#include "dcCartCompleteCavityOriginCore.H"
#include "dcCartCutCells.H"
#include "dcCartCompleteCavityAuthority.H"
#include "dcCartWeightUnionPreview.H"
#include "primitiveMesh.H"
#include "primitiveMeshTools.H"
#include "bitSet.H"
#include "clockTime.H"
#include <iostream>
#include <cstring>
#include <string>
#include <array>
#include <iomanip>
namespace Controlled
{
static std::string trace;
static bool traceOverflow=false;
static double elapsed=0;
inline void event(char c)
{if(trace.size()<1024u*1024u)trace.push_back(c);else traceOverflow=true;}
struct alignas(Foam::clockTime) Clock
{
    unsigned char pad[sizeof(Foam::clockTime)];
    Clock(){event('C');}
    double elapsedTime()const{event('E');return elapsed;}
};
static_assert(sizeof(Clock)==sizeof(Foam::clockTime),"Clock layout differs");
static_assert(alignof(Clock)==alignof(Foam::clockTime),"Clock alignment differs");
}
#define clockTime Controlled::Clock
#include "dcCartCompleteCavityLocalOracle.H"
#undef clockTime
#ifdef P17_AFTER
namespace dcCartLegacyJournalObservation
{
thread_local Store state{};
thread_local Store* active=nullptr;
}
#endif
namespace CL=Foam::dcCartCompleteCavityLocalOracle;
namespace Controlled
{
struct Hash
{
    std::uint64_t h=1469598103934665603ull;
    std::string canonical;
    template<class T>void pod(const T& v)
    {const auto* b=reinterpret_cast<const unsigned char*>(&v);canonical.append(reinterpret_cast<const char*>(b),sizeof(T));for(std::size_t i=0;i<sizeof(T);++i){h^=b[i];h*=1099511628211ull;}}
    void string(const std::string& v){pod(v.size());canonical.append(v);for(char b:v){h^=static_cast<unsigned char>(b);h*=1099511628211ull;}}
    std::string hex()const{static const char* digits="0123456789abcdef";std::string out;out.reserve(canonical.size()*2);for(unsigned char b:canonical){out.push_back(digits[b>>4]);out.push_back(digits[b&15]);}return out;}
    void labels(const std::vector<Foam::label>& v){pod(v.size());pod(v.capacity());for(auto x:v)pod(x);}
    void points(const std::vector<Foam::point>& v){pod(v.size());pod(v.capacity());for(const auto& p:v){pod(p.x());pod(p.y());pod(p.z());}}
    void origin(const CL::Origin& o)
    {pod(o.nativeParent);pod(o.rank);pod(o.level);pod(o.leaf);pod(o.piece);string(o.region);pod(o.sourceFacets.size());pod(o.sourceFacets.capacity());for(const auto& f:o.sourceFacets)for(auto x:f)pod(x);}
    void snapshot(const CL::LegacySnapshot& s)
    {
        labels(s.core);labels(s.outside);labels(s.secondHalo);pod(s.bytes);pod(s.complete);
        pod(s.cells.size());pod(s.cells.capacity());
        for(const auto& x:s.cells){pod(x.native);pod(x.rank);pod(x.alive);labels(x.rawFaces);pod(x.origins.size());pod(x.origins.capacity());for(const auto& o:x.origins)origin(o);}
        pod(s.faces.size());pod(s.faces.capacity());
        for(const auto& x:s.faces){pod(x.native);pod(x.owner);pod(x.neighbour);pod(x.patch);pod(x.zone);pod(x.alive);labels(x.ring);string(x.role);}
        pod(s.aliases.size());pod(s.aliases.capacity());
        for(const auto& x:s.aliases){pod(x.representative);pod(x.world.x());pod(x.world.y());pod(x.world.z());labels(x.members);points(x.rawWorld);}
    }
    void journal(const CL::LegacyJournal* j)
    {
        const bool have=j;pod(have);if(!j)return;
        pod(j->epoch);pod(j->rank);pod(j->nativeCells);pod(j->nativePoints);pod(j->nGridPoints);
        string(j->source.raw);string(j->source.roles);string(j->source.materialKey);string(j->source.material);string(j->source.producer);string(j->source.boundaryMembership);
        labels(j->alias);labels(j->head);labels(j->next);pod(j->payloadBytes);pod(j->workSeconds);pod(j->complete);pod(j->commits.size());pod(j->commits.capacity());
        for(const auto& x:j->commits){pod(x.keep);pod(x.drop);pod(x.acceptedBefore);pod(x.prepared);pod(x.complete);snapshot(x.before);snapshot(x.after);}
    }
    void cut(const CL::Cut* m,const std::vector<std::vector<Foam::label>>* cf)
    {
        const bool have=m;pod(have);if(!m)return;
        points(m->points);pod(m->faces.size());pod(m->faces.capacity());for(const auto& f:m->faces)labels(f);
        labels(m->owner);labels(m->neighbour);labels(m->patch);labels(m->zone);
        pod(m->faceAlive.size());for(bool v:m->faceAlive)pod(v);pod(m->cellAlive.size());for(bool v:m->cellAlive)pod(v);
        labels(m->cellRank);pod(m->nCells);pod(m->nGridPoints);
        const bool haveCF=cf;pod(haveCF);if(cf){pod(cf->size());pod(cf->capacity());for(const auto& f:*cf)labels(f);}
    }
};
static const CL::Cut* registeredCut=nullptr;
static const std::vector<std::vector<Foam::label>>* registeredCF=nullptr;
static const CL::LegacyJournal* registeredJournal=nullptr;
inline void reg(const CL::Cut* c,const std::vector<std::vector<Foam::label>>* f,const CL::LegacyJournal* j)
{registeredCut=c;registeredCF=f;registeredJournal=j;}
inline void mark(const char* name,const CL::Cut* c,const std::vector<std::vector<Foam::label>>* f,const CL::LegacyJournal* j)
{
    Hash mesh,journal;mesh.cut(c,f);journal.journal(j);
    std::cout<<"{\"controlledState\":\""<<name<<"\",\"mesh\":"<<mesh.h<<",\"journal\":"<<journal.h<<",\"meshCanonical\":\""<<mesh.hex()<<"\",\"journalCanonical\":\""<<journal.hex()<<"\",\"journalComplete\":"<<(j&&j->complete?"true":"false")<<",\"workSeconds\":"<<(j?j->workSeconds:0)<<"}\n";
}
inline void mark(const char* name){mark(name,registeredCut,registeredCF,registeredJournal);}
}
#include "dcCartCompleteCavityLocalOracle.H"
#include "dcCartWriterNumericContext.H"
#include "Time.H"
#include "IFstream.H"
#include "dcCartCompleteCavityReplayObservation.H"
namespace dcCartCompleteCavityReplaySourceSidecar
{
thread_local Reference state;
}
namespace Foam { namespace dcCartCompleteCavityReplayObservation
{ thread_local Pending pending; }}
#include "polyPatch.H"
#include <iostream>
#include <limits>
using namespace Foam;
namespace L=dcCartCompleteCavityLocalOracle;
static int passed=0,failed=0;
static void test(const char* name,bool ok){Controlled::mark(name);std::cout<<"{\"test\":\""<<name<<"\",\"pass\":"<<(ok?"true":"false")<<"}\n";if(ok)++passed;else ++failed;}

static void diagnostic(const char* name,const L::View& view,const L::Cut& cut,
                       const L::Authority& auth,label legacy,
                       const std::vector<std::vector<label>>& cf,bool wanted,
                       const char* expected)
{
    auto counted=auth;auto clock=auth.remainingSeconds;std::size_t calls=0;
    counted.remainingSeconds=[&](){Controlled::event('A');++calls;return clock();};
#ifdef TYPED_CURRENT
    L::CurrentRefusal reason=L::CurrentRefusal::exception;
    const bool actual=L::current(view,cut,counted,legacy,cf,&reason);
    const char* observed=L::currentRefusalName(reason);
    const bool ok=actual==wanted&&std::string(observed)==expected;
#else
    const bool actual=L::current(view,cut,counted,legacy,cf);
    const char* observed="API absent";
    const bool ok=actual==wanted;
#endif
    std::cout<<"{\"test\":\""<<name<<"\",\"pass\":"<<(ok?"true":"false")
        <<",\"actual\":"<<(actual?"true":"false")<<",\"clockCalls\":"<<calls
        <<",\"reason\":\""<<observed<<"\",\"expected\":\""<<expected<<"\"}\n";
    Controlled::mark(name,&cut,&cf,view.legacyJournal.get());
    if(ok)++passed;else ++failed;
}


int main(int argc,char** argv)
{
    if(argc!=4)return 2;
    const bool replayOn=std::string(argv[2])=="on";
    if(!replayOn&&std::string(argv[2])!="off")return 2;
    setenv("DCCART_COMPLETE_REPLAY_OBSERVATION",replayOn?"1":"0",1);
    dcCartLegacyJournalObservation::Scope originalObservation(true,0);
    const label nc=12,np=4*(nc+1),ni=nc-1;
    pointField pts(np);
    for(label x=0;x<=nc;++x)
    {pts[4*x]=point(x,0,0);pts[4*x+1]=point(x,1,0);pts[4*x+2]=point(x,1,1);pts[4*x+3]=point(x,0,1);}
    std::vector<std::vector<label>> rings;
    std::vector<label> own,nei;
    auto add=[&](std::initializer_list<label> ring,label o,label n=-1)
    {rings.emplace_back(ring);own.push_back(o);if(n>=0)nei.push_back(n);};
    for(label x=1;x<nc;++x)add({4*x,4*x+1,4*x+2,4*x+3},x-1,x);
    add({0,3,2,1},0);add({4*nc,4*nc+1,4*nc+2,4*nc+3},nc-1);
    for(label c=0;c<nc;++c)
    {
        label a=4*c,b=4*(c+1);
        add({a,b,b+3,a+3},c);add({a+1,a+2,b+2,b+1},c);
        add({a,a+1,b+1,b},c);add({a+3,b+3,b+2,a+2},c);
    }
    dcCartCutCells::cutMesh raw;
    raw.points.assign(pts.begin(),pts.end());raw.faces=rings;raw.owner=own;raw.neighbour.assign(rings.size(),-1);
    for(label f=0;f<ni;++f)raw.neighbour[f]=nei[f];
    raw.patch.assign(rings.size(),0);for(label f=0;f<ni;++f)raw.patch[f]=-1;
    raw.zone.assign(rings.size(),-1);raw.faceAlive.assign(rings.size(),true);
    raw.nCells=nc;raw.cellAlive.assign(nc,true);raw.cellRank.assign(nc,0);raw.nGridPoints=np;
    std::vector<std::vector<label>> cf(nc);
    for(label f=0;f<label(rings.size());++f){cf[own[f]].push_back(f);if(f<ni)cf[nei[f]].push_back(f);}
    faceList faces(rings.size());labelList owner(rings.size()),neighbour(ni);
    for(label f=0;f<label(rings.size());++f){faces[f].setSize(rings[f].size());for(label k=0;k<label(rings[f].size());++k)faces[f][k]=rings[f][k];owner[f]=own[f];}
    for(label f=0;f<ni;++f)neighbour[f]=nei[f];
    auto time=Time::New(fileName(argv[1]));
    auto mesh=autoPtr<polyMesh>(new polyMesh(IOobject("typedCavityOracle","constant",*time,IOobject::NO_READ,IOobject::NO_WRITE,false),
        std::move(pts),std::move(faces),std::move(owner),std::move(neighbour)));
    List<polyPatch*> patches(1);patches[0]=polyPatch::New(word("wall"),word("literalWall"),rings.size()-ni,ni,0,mesh->boundaryMesh()).ptr();
    mesh->addPatches(patches,false);
    dcCartCutCells::writerBindings map;
    for(label c=0;c<nc;++c)map.cell.push_back(c);
    for(label f=0;f<label(rings.size());++f)map.face.push_back(f);
    for(label p=0;p<np;++p){map.point.push_back(p);map.alias.push_back(p);}

    for(label c=0;c<nc;++c){raw.cellLevel.push_back(0);raw.cellLeaf.push_back(c);raw.cellPiece.push_back(0);}
    IFstream in(argv[3]);dictionary material(in);
    wordList patchNames(1,word("literalWall")),patchTypes(1,word("wall")),zones;
    std::function<scalar()> remaining=[](){Controlled::event('A');return scalar(100);};
    const auto source=dcCartPostCarveFluidOwnershipPreview::capture(raw,material,patchNames,patchTypes,zones,0,true,remaining);
    auto pair=dcCartWeightUnionSourceContract::readAuthority(material,dcCartPostCarveFluidOwnershipPreview::verifier(source),remaining,
        [source](label parent){return dcCartPostCarveFluidOwnershipPreview::origin(source,parent);});
    auto authority=dcCartCompleteCavityAuthority::read(material,source,pair,remaining);
    test("realParsedNormalSourceProducer",source&&source->complete&&authority.ready());
    test("defaultOffSidecarNoReference",replayOn?dcCartCompleteCavityReplaySourceSidecar::reference().snapshot==source:
        !dcCartCompleteCavityReplaySourceSidecar::reference().snapshot);
    raw.completeCavityLineage.initialize(nc,authority.recordedParent,[]{return true;});
    Controlled::reg(&raw,&cf,nullptr);
    L::NormalIndex index;test("actualNormalIndex",L::produceIndex(raw,*mesh,map,authority,index));
    L::View view;test("actualInstalledFullView",L::capture(raw,*mesh,map,authority,{7},cf,index,17,view));
    auto journal=std::make_shared<L::LegacyJournal>();
    test("actualNormalJournalInitialize",L::initializeLegacyJournal(*journal,raw,map,authority,17));
    auto live=raw;auto liveCF=cf;L::LegacyCommit pending;
    test("actualLegacyProducerBefore",L::prepareLegacyCommit(*journal,live,authority,liveCF,patchTypes,8,9,0,pending));
    std::set<label> a(liveCF[8].begin(),liveCF[8].end()),b(liveCF[9].begin(),liveCF[9].end());
    std::vector<label> shared,outer;
    std::set_intersection(a.begin(),a.end(),b.begin(),b.end(),std::back_inserter(shared));
    std::set_symmetric_difference(a.begin(),a.end(),b.begin(),b.end(),std::back_inserter(outer));
    if(shared.size()!=1)return 3;
    live.completeCavityLineage.join(8,9,[]{return true;});
    live.faceAlive[shared[0]]=false;live.cellAlive[9]=false;liveCF[9].clear();liveCF[8]=outer;
    for(label f:outer)
    {
        if(live.owner[f]==9)live.owner[f]=8;if(live.neighbour[f]==9)live.neighbour[f]=8;
        if(live.neighbour[f]>=0&&live.owner[f]>live.neighbour[f])
        {std::swap(live.owner[f],live.neighbour[f]);std::reverse(live.faces[f].begin()+1,live.faces[f].end());}
    }
    test("actualLegacyProducerAfter",L::finishLegacyCommit(*journal,live,authority,liveCF,patchTypes,1,pending));
    view.legacyJournal=journal;L::Catalogue catalogue;catalogue.epoch=17;catalogue.views.push_back(view);
    // The event is produced by the REAL current predicate, not a hand-filled binding.
    L::CurrentRefusal why=L::CurrentRefusal::none;
    {dcCartLegacyJournalObservation::PhaseScope census(dcCartLegacyJournalObservation::Phase::census);
      const bool admitted=L::current(view,live,authority,1,liveCF,&why);
      test("normalCurrentDependencyRefusesUnchanged",!admitted&&why==L::CurrentRefusal::legacy);}
    auto beforeState=dcCartLegacyJournalObservation::state;
    auto beforeSide=dcCartLegacyJournalObservation::dependencyStorage();
    auto* beforeActive=dcCartLegacyJournalObservation::active;
    const auto initialTrace=Controlled::trace;
    Controlled::Hash hBefore;hBefore.cut(&live,&liveCF);hBefore.journal(journal.get());
    auto lineageBefore=live.completeCavityLineage;
    const std::string output=std::string(argv[1])+"/original-decisions-observation.json";
    namespace R=dcCartCompleteCavityReplayObservation;
    const auto result=R::capture(R::enabled(),output.c_str(),catalogue,live,liveCF,patchTypes,beforeState,beforeSide);
    Controlled::Hash hAfter;hAfter.cut(&live,&liveCF);hAfter.journal(journal.get());
    test("captureDefaultOffOrComplete",replayOn?result.complete&&result.written:!result.enabled&&!result.written);
    test("callbackTraceExactlyUnchanged",initialTrace==Controlled::trace);
    test("StoreAndSidecarExactlyUnchanged",!std::memcmp(&beforeState,&dcCartLegacyJournalObservation::state,sizeof(beforeState))
        &&!std::memcmp(&beforeSide,&dcCartLegacyJournalObservation::dependencyStorage(),sizeof(beforeSide))
        &&beforeActive==dcCartLegacyJournalObservation::active);
    test("allWriterInputAndJournalBitsUnchanged",hBefore.canonical==hAfter.canonical);
    bool lineageSame=live.completeCavityLineage.revision()==lineageBefore.revision()
        &&live.completeCavityLineage.refusals()==lineageBefore.refusals();
    {dcCartLegacyJournalObservation::Scope quiet(false);
      for(label c=0;c<nc;++c){std::vector<label> x,y;
        const bool p=live.completeCavityLineage.originsForCells({c},x),q=lineageBefore.originsForCells({c},y);
        lineageSame=lineageSame&&p==q&&x==y;}}
    test("normalLiveOriginSetsUnchanged",lineageSame);
    std::cout<<"{\"normalDecisionTrace\":\""<<initialTrace<<"\",\"writerInputHash\":"<<hBefore.h
        <<",\"firstCauseSite\":"<<beforeState.last.site<<",\"firstCauseOrdinal\":"<<beforeState.last.ordinal
        <<",\"journalComplete\":"<<(journal->complete?"true":"false")<<",\"captureComplete\":"<<(result.complete?"true":"false")
        <<",\"captureEnabled\":"<<(result.enabled?"true":"false")<<",\"captureReason\":\""<<result.refusal<<"\",\"seconds\":"<<result.seconds<<"}\n";
    // Actual cached earliest + final binding read, after the original decisions.
    // This does not refresh fields: prove missing caches explicitly refuse first.
    namespace R2=dcCartCompleteCavityReplayObservation;
    setenv("DCCART_COMPLETE_REPLAY_OBSERVATION","1",1);
    const auto rebound=dcCartCompleteCavityAuthority::read(material,source,pair,remaining);
    R2::BindingRequest needed;R2::Writer rawWriter;R2::Result rawCounts;
    R2::build(rawWriter,catalogue,live,liveCF,patchTypes,beforeState,beforeSide,rawCounts,&needed);
    R2::Writer installedWriter;std::string installedRefusal="none";
    try{R2::installed(installedWriter,needed,*mesh,map,"controlled-existing-earliest",false);}
    catch(const char* reason){installedRefusal=reason;}
    test("actualCachedFieldsCompleteNoRecalculation",installedRefusal=="none"&&R2::cachedGeometry(*mesh));
    const auto traceBeforePair=Controlled::trace;
    const std::string pairOutput=std::string(argv[1])+"/paired-raw.json";
    const auto paired=R2::capturePairedRaw(true,pairOutput.c_str(),catalogue,live,liveCF,patchTypes,
        beforeState,beforeSide,mesh.get(),&map);
    test("pairedRealEarliestAndRawComplete",paired.complete&&paired.written&&R2::pending.ready);
    auto readOnlyMap=map;
    const std::string finalOutput=std::string(argv[1])+"/paired-final.json";
    const auto finalResult=R2::captureFinal(true,finalOutput.c_str(),*mesh,&readOnlyMap);
    test("lateFinalBindsActualCachedFields",finalResult.complete&&finalResult.written&&!R2::pending.ready);
    test("pairedNoCallbackOrRawWriterMutation",traceBeforePair==Controlled::trace&&map.cell==readOnlyMap.cell&&map.face==readOnlyMap.face
         &&map.point==readOnlyMap.point&&map.alias==readOnlyMap.alias);
    auto capturedTry=[&](const auto& requested,const auto& mappings,const char* wanted,bool allowRemoved)
    {
        R2::Writer w;
        try{R2::installed(w,requested,*mesh,mappings,"controlled-actual",allowRemoved);return std::string(wanted)=="none";}
        catch(const char* reason){return std::string(reason)==wanted;}
    };
    auto missingFace=needed;missingFace.faces.pop_back();
    test("completeInstalledCellCannotOmitFacet",capturedTry(missingFace,map,"installed-complete-cell-face-missing",false));
    auto badMap=map;badMap.cell[needed.cells.front()]=mesh->nCells();
    test("actualCompactCellMappingRangeRefuses",capturedTry(needed,badMap,"installed-cell-binding",false));
    badMap=map;badMap.cell[needed.cells[1]]=badMap.cell[needed.cells[0]];
    test("duplicateCompactCellMappingRefuses",capturedTry(needed,badMap,"installed-cell-binding-duplicate",false));
    badMap=map;badMap.point[needed.points.front()]=mesh->nPoints();
    test("realPointBindingCannotGuessAlias",capturedTry(needed,badMap,"installed-point-or-alias-binding",false));
    badMap=map;badMap.alias[needed.points.front()]=-1;
    test("unknownAliasRefusesNoInventedIdentity",capturedTry(needed,badMap,"installed-point-or-alias-binding",false));
    const std::string notRetained=std::string(argv[1])+"/missing-earliest.json";
    const auto missingEarly=R2::capturePairedRaw(true,notRetained.c_str(),catalogue,live,liveCF,patchTypes,beforeState,beforeSide,nullptr,&map);
    test("absentEarliestExplicitlyNotComplete",!missingEarly.complete&&missingEarly.written&&!R2::pending.ready
         &&std::string(missingEarly.refusal)=="earliest-installed-not-retained");
    R2::pending.request=needed;R2::pending.rawPath=pairOutput;R2::pending.ready=true;R2::pending.chargedSeconds=R2::Limits::seconds;
    const std::string exhausted=std::string(argv[1])+"/late-budget-refusal.json";
    const auto lateBudget=R2::captureFinal(true,exhausted.c_str(),*mesh,&map);
    test("earliestAndFinalShareFiniteObservationBudget",!lateBudget.complete&&lateBudget.written&&
         std::string(lateBudget.refusal)=="observation-clock"&&!R2::pending.ready);
    const std::string noPair=std::string(argv[1])+"/missing-pair-final.json";
    const auto missingPair=R2::captureFinal(true,noPair.c_str(),*mesh,&map);
    test("lateFinalRequiresSamePairedRequest",!missingPair.complete&&missingPair.written
         &&std::string(missingPair.refusal)=="raw-installed-pair-not-ready");
    // Clear only this controlled object after its original decisions; the OBS
    // guard must refuse without calling any demand-driven geometry accessor.
    mesh->clearGeom();
    test("clearActualGeometryReallyRemovesCaches",!R2::cachedGeometry(*mesh));
    test("cacheAbsentRefusesWithoutCreatingFields",capturedTry(needed,map,"installed-cache-not-present",false)&&!R2::cachedGeometry(*mesh));
    // Subsequent focused serializer tests do NOT authorize a business cavity.
    // Re-read the ACTUAL dictionary/authority to populate the const source sidecar.
    setenv("DCCART_COMPLETE_REPLAY_OBSERVATION","1",1);
    auto sameAuthority=dcCartCompleteCavityAuthority::read(material,source,pair,remaining);
    test("sameActualReadBindsProducerWithoutNewPermission",sameAuthority.ready()&&dcCartCompleteCavityReplaySourceSidecar::reference().snapshot==source);
    auto buildTry=[&](const char* name,const L::Catalogue& cat,const L::Cut& cut,
                      const std::vector<std::vector<label>>& shape,const dcCartLegacyJournalObservation::Store& store,
                      const dcCartLegacyJournalObservation::DependencyStorage& bindings,std::size_t cap=R::Limits::bytes,double seconds=10)
    {
        R::Writer writer(cap,seconds);R::Result counts;
        try{R::build(writer,cat,cut,shape,patchTypes,store,bindings,counts);
            std::cout<<"{\"buildCase\":\""<<name<<"\",\"complete\":true,\"bytes\":"<<writer.text.size()<<"}\n";
            return std::string("none");}
        catch(const char* reason){std::cout<<"{\"buildCase\":\""<<name<<"\",\"complete\":false,\"reason\":\""<<reason<<"\"}\n";return std::string(reason);}
        catch(...){return std::string("exception");}
    };
    auto expect=[&](const char* name,const std::string& actual,const char* expected){test(name,actual==expected);};
    expect("completeAllJournalAndRawClosure",buildTry("positive",catalogue,live,liveCF,beforeState,beforeSide),"none");
    auto duplicate=catalogue;duplicate.views.push_back(view);
    expect("ambiguousViewRefuses",buildTry("duplicate",duplicate,live,liveCF,beforeState,beforeSide),"ambiguous-view-binding");
    auto noEvent=beforeState;noEvent.used=0;
    expect("noRecordedDependencyRefuses",buildTry("noevent",catalogue,live,liveCF,noEvent,beforeSide),"no-observed-complete-dependency");
    auto uncertain=beforeSide;for(auto& b:uncertain.samples)b.clockUnknown=true;
    expect("clockUnknownNoInventedBinding",buildTry("unknown",catalogue,live,liveCF,beforeState,uncertain),"no-observed-complete-dependency");
    auto malformed=beforeState;malformed.used=24;
    expect("eventCapRefusesBeforeLookup",buildTry("eventcap",catalogue,live,liveCF,malformed,beforeSide),"dependency-observer-disabled-or-invalid");
    expect("strictOutputByteCapDiscardsFullClaim",buildTry("bytecap",catalogue,live,liveCF,beforeState,beforeSide,16),"observation-byte-cap");
    expect("clockBudgetRefuses",buildTry("clock",catalogue,live,liveCF,beforeState,beforeSide,R::Limits::bytes,0),"observation-clock");
    auto bad=live;bad.points[5*4].x()=std::numeric_limits<scalar>::quiet_NaN();
    expect("nonfiniteRawPointRefuses",buildTry("nan",catalogue,bad,liveCF,beforeState,beforeSide),"observation-nonfinite");
    auto resized=live;resized.points.push_back(point::zero);
    expect("newCapacityExplicitRefusal",buildTry("resized",catalogue,resized,liveCF,beforeState,beforeSide),"current-raw-capacity-or-alias-index");
    auto badCF=liveCF;badCF[8].push_back(0);
    expect("unknownIncidenceRefuses",buildTry("badcf",catalogue,live,badCF,beforeState,beforeSide),"current-cell-incidence");
    auto oldSource=dcCartCompleteCavityReplaySourceSidecar::reference();
    dcCartCompleteCavityReplaySourceSidecar::reference().snapshot.reset();
    expect("missingRealProducerRefuses",buildTry("missingproducer",catalogue,live,liveCF,beforeState,beforeSide),"missing-normal-producer-reference");
    dcCartCompleteCavityReplaySourceSidecar::reference()=oldSource;
    auto stale=std::make_shared<dcCartFluidOriginSnapshotCore::Snapshot>(*source);stale->boundaryMembershipDigest[0]='f';
    dcCartCompleteCavityReplaySourceSidecar::reference().snapshot=stale;
    expect("freshBoundaryDigestMandatory",buildTry("stale",catalogue,live,liveCF,beforeState,beforeSide),"stale-normal-producer-reference");
    dcCartCompleteCavityReplaySourceSidecar::reference()=oldSource;
    auto partial=std::make_shared<L::LegacyJournal>(*journal);partial->commits[0].after.complete=false;
    auto broken=catalogue;broken.views.front().legacyJournal=partial;
    expect("incompleteSnapshotNeverCalledFull",buildTry("partial",broken,live,liveCF,beforeState,beforeSide),"legacy-snapshot-incomplete");
    partial=std::make_shared<L::LegacyJournal>(*journal);partial->next[20]=20;partial->head[20]=20;
    broken=catalogue;broken.views.front().legacyJournal=partial;
    expect("aliasCycleRefuses",buildTry("cycle",broken,live,liveCF,beforeState,beforeSide),"raw-alias-cycle-or-membership");
    stale=std::make_shared<dcCartFluidOriginSnapshotCore::Snapshot>(*source);stale->faces[source->parents[6].facets[0]].zone=7;
    dcCartCompleteCavityReplaySourceSidecar::reference().snapshot=stale;
    expect("sourceFacetRoleTamperRefuses",buildTry("sourcerole",catalogue,live,liveCF,beforeState,beforeSide),"recorded-origin-source-facet");
    dcCartCompleteCavityReplaySourceSidecar::reference()=oldSource;
    const std::string exclusive=std::string(argv[1])+"/exclusive.json";
    test("exclusiveOutputNeverOverwrites",R::exclusiveWrite(exclusive.c_str(),"original")&&!R::exclusiveWrite(exclusive.c_str(),"replacement"));
    const std::string failedOutput=std::string(argv[1])+"/refusal.json";
    auto refused=R::capture(true,failedOutput.c_str(),broken,live,liveCF,patchTypes,beforeState,beforeSide);
    test("failureFileExplicitNotFull",!refused.complete&&refused.written);
    test("strictExactOneFlag",R::exactFlag("1")&&!R::exactFlag("01")&&!R::exactFlag("true")&&!R::exactFlag("0")&&!R::exactFlag(nullptr));
    // Failed actual source read clears reference, preventing sticky source permission.
    material.subDict("dcdtuniWeightUnionSourceContract").set("boundaryMembershipDigest",Foam::string(std::string(64,'f')));
    auto denied=dcCartCompleteCavityAuthority::read(material,source,pair,remaining);
    test("failedReadClearsStaleSidecar",!denied.ready()&&!dcCartCompleteCavityReplaySourceSidecar::reference().snapshot);
    std::cout<<"{\"summary\":true,\"passed\":"<<passed<<",\"failed\":"<<failed<<"}\n";
    return failed?1:0;
}
