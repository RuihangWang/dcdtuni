#ifndef dcCartCompleteCavityOriginCore_H
#define dcCartCompleteCavityOriginCore_H
#include "dcCartFluidOriginSnapshotCore.H"
#include <limits>
#include <functional>
#include <new>
#include <type_traits>
#include <cstdint>
#include <stdexcept>
#include <cstdio>
#include <cmath>
#include <cstring>
#include <exception>

// P17 observation storage is independent of every charged authority/journal/view type.
namespace dcCartLegacyJournalObservation
{
enum class Reason:unsigned char {pass,noHandle,incomplete,aliasIndex,capacity,deadline,authority,sequence,identity,shape,alias,origin,pending,mutation,dependency,exception,count};
enum class Phase:unsigned char {init,prepare,before,finish,after,closure,census,repair,current,count};
enum class Source:unsigned char {core,authority,oracle,consumer,cli};
struct Record
{
    std::uint64_t epoch,ordinal,entries,bytes,indexBytes;
    std::int64_t a,b,c,d,contextA,contextB,contextC,contextD;
    double workSeconds;
    unsigned site;
    unsigned char source,reason,phase,kind,resultKnown,result,preComplete,postComplete,completeKnown,currentWhy;
};
struct Store
{
    std::uint64_t attempt[unsigned(Phase::count)],passed[unsigned(Phase::count)],failed[unsigned(Phase::count)];
    std::uint64_t reasonCount[unsigned(Phase::count)][unsigned(Reason::count)];
    std::uint64_t dropped,saturated,ordinal,payloadHigh,indexHigh,entriesHigh;
    std::uint64_t captured,proposed,outputBytes,outputRecords;
    Record first,last,context,records[23];
    unsigned used,depth;
    int rank;
    Phase phase;
    bool enabled,haveFirst,haveLast,sealed,outputError;
};
static_assert(std::is_trivial<Record>::value&&std::is_standard_layout<Record>::value,"observation record must be POD");
static_assert(std::is_trivial<Store>::value&&std::is_standard_layout<Store>::value,"observation store must be POD");
static_assert(sizeof(Record)<=256,"observation witness cap");
static_assert(sizeof(Store)<=16u*1024u,"observation store cap");
extern thread_local Store state;
extern thread_local Store* active;
inline void increment(std::uint64_t& n) noexcept
{if(n!=std::numeric_limits<std::uint64_t>::max())++n;else if(active&&&n!=&active->saturated&&active->saturated!=std::numeric_limits<std::uint64_t>::max())++active->saturated;}
inline bool enabled()noexcept{return active&&active->enabled;}
// P18 diagnostic-only sidecar: no charged Record/Store/authority ABI changes.
#define DCTWIN_DEPENDENCY_BINDING_V1 1
struct DependencyBinding
{
    std::uint64_t entry;
    std::int64_t keep,drop,legacyCell,viewCell,legacyParent,viewParent,legacyRank,viewRank;
    std::int64_t legacyFace,viewFace,legacyAlias,viewAlias,legacyMember,viewMember;
    std::int64_t coreFirst;
    std::uint64_t legacyCounts[4],viewCounts[5];
    unsigned kind,side,legacyMembership,viewMembership;
    bool valid,lookupBounded,clockUnknown;
};
struct DependencyStorage {DependencyBinding pending,samples[23];};
static_assert(std::is_trivial<DependencyBinding>::value,"binding sidecar POD");
static_assert(sizeof(DependencyStorage)<=16u*1024u,"independent binding storage cap");
inline DependencyStorage& dependencyStorage()noexcept
{static thread_local DependencyStorage store{};return store;}
struct Scope
{
    Store* previous;
    explicit Scope(bool on,int rank=0)noexcept:previous(active)
    {
        if(on){state=Store{};dependencyStorage()=DependencyStorage{};state.enabled=true;state.rank=rank;active=&state;}
        else active=nullptr;
    }
    ~Scope(){active=previous;}
};
struct PhaseScope
{
    Phase previous;bool on;
    explicit PhaseScope(Phase p)noexcept:previous(Phase::init),on(enabled())
    {if(on){previous=active->phase;active->phase=p;}}
    ~PhaseScope(){if(on)active->phase=previous;}
};
inline void failure(Reason reason,Source source,unsigned site,
                    std::int64_t a=0,std::int64_t b=0,std::int64_t c=0,std::int64_t d=0)noexcept
{
    if(!enabled()||active->haveLast)return;
    active->last=active->context;active->last.reason=static_cast<unsigned char>(reason);
    active->last.source=static_cast<unsigned char>(source);active->last.site=site;
    active->last.phase=static_cast<unsigned char>(active->phase);active->last.a=a;active->last.b=b;active->last.c=c;active->last.d=d;
    active->haveLast=true;increment(active->reasonCount[unsigned(active->phase)][unsigned(reason)]);
}
inline bool observe(bool failed,Reason reason,Source source,unsigned site,
                    std::int64_t a=0,std::int64_t b=0,std::int64_t c=0,std::int64_t d=0)noexcept
{if(failed)failure(reason,source,site,a,b,c,d);return failed;}
inline bool greater(std::size_t value,std::size_t bound,Source source,unsigned site,std::size_t unit=1,bool index=false)noexcept
{
    if(enabled()&&index)
    {
        const std::size_t measured=unit&&value>std::numeric_limits<std::size_t>::max()/unit
            ?std::numeric_limits<std::size_t>::max():value*unit;
        if(measured>active->indexHigh)active->indexHigh=measured;
    }
    return observe(value>bound,Reason::capacity,source,site,std::int64_t(value),std::int64_t(bound),std::int64_t(unit));
}
inline bool atLeast(double value,double bound,Source source,unsigned site)noexcept
{
    const bool failed=value>=bound;
    if(failed){failure(Reason::deadline,source,site);if(enabled()&&active->haveLast&&active->last.source==static_cast<unsigned char>(source)&&active->last.site==site)active->last.workSeconds=value;}
    return failed;
}
inline bool reject(Reason reason,Source source,unsigned site,
                   std::int64_t a=0,std::int64_t b=0,std::int64_t c=0,std::int64_t d=0)noexcept
{failure(reason,source,site,a,b,c,d);return false;}
inline bool overlap(Source source,unsigned site,std::int64_t a=0,std::int64_t b=0,std::int64_t c=0,std::int64_t d=0)noexcept
{failure(Reason::dependency,source,site,a,b,c,d);return true;}
inline void invalidation(bool previouslyComplete)noexcept
{
    if(!enabled()||!previouslyComplete||active->haveFirst)return;
    if(!active->haveLast)failure(Reason::incomplete,Source::oracle,110);
    active->first=active->last;active->first.kind=1;
    active->first.completeKnown=1;active->first.preComplete=1;active->first.postComplete=0;
    active->haveFirst=true;
}
inline void sample(const Record& record)noexcept
{
    if(!enabled())return;
    if(active->used<23)
    {
        const unsigned i=active->used;active->records[active->used++]=record;
        dependencyStorage().samples[i]=
            record.reason==unsigned(Reason::dependency)?dependencyStorage().pending:DependencyBinding{};
    }
    else increment(active->dropped);
}
struct Operation
{
    Store* store;Record previous,record;Phase previousPhase;const bool* complete;bool returned,resultValue,sampled;
    Operation(Phase phase,Source source,unsigned site,const bool* completePtr=nullptr,
              bool sampleResult=false)noexcept
    :store(enabled()?active:nullptr),previous{},record{},previousPhase(Phase::init),
     complete(completePtr),returned(false),resultValue(false),sampled(sampleResult)
    {
        if(!store)return;
        previous=store->context;previousPhase=store->phase;
        if(store->depth++==0){store->last=Record{};store->haveLast=false;dependencyStorage().pending=DependencyBinding{};}
        store->phase=phase;increment(store->attempt[unsigned(phase)]);
        record.source=static_cast<unsigned char>(source);record.site=site;record.phase=static_cast<unsigned char>(phase);
        increment(store->ordinal);record.ordinal=store->ordinal;
        if(complete){record.completeKnown=1;record.preComplete=*complete;}
        store->context=record;
    }
    void bind(std::uint64_t epoch,std::uint64_t entries,std::uint64_t bytes,double work,
              std::int64_t a=0,std::int64_t b=0,std::int64_t c=0,std::int64_t d=0,
              std::uint64_t index=0)noexcept
    {
        if(!store)return;
        record.epoch=epoch;record.entries=entries;record.bytes=bytes;record.workSeconds=work;
        record.a=a;record.b=b;record.c=c;record.d=d;
        record.contextA=a;record.contextB=b;record.contextC=c;record.contextD=d;
        record.indexBytes=index;store->context=record;
        if(bytes>store->payloadHigh)store->payloadHigh=bytes;
        if(index>store->indexHigh)store->indexHigh=index;
        if(entries>store->entriesHigh)store->entriesHigh=entries;
    }
    bool result(bool value)noexcept{returned=true;resultValue=value;return value;}
    ~Operation()noexcept
    {
        if(!store)return;
        record.resultKnown=returned;record.result=resultValue;
        if(complete)record.postComplete=*complete;
        if(returned)
        {
            increment(resultValue?store->passed[unsigned(record.phase)]:store->failed[unsigned(record.phase)]);
            if(!resultValue&&store->haveLast)
            {
                const Record base=record;record=store->last;
                record.ordinal=base.ordinal;record.kind=base.kind;record.resultKnown=1;record.result=0;
                record.completeKnown=base.completeKnown;record.preComplete=base.preComplete;record.postComplete=base.postComplete;
                record.currentWhy=base.currentWhy;
            }
            else if(resultValue)record.reason=static_cast<unsigned char>(Reason::pass);
        }
        if(sampled)sample(record);
        --store->depth;store->context=previous;store->phase=previousPhase;
    }
};
template<class Why> struct CurrentOutcome
{
    const Why* why;Operation operation;
    CurrentOutcome(const Why* p,const bool* journalComplete)noexcept
    :why(p),operation(enabled()&&(active->phase==Phase::census||active->phase==Phase::repair)?active->phase:Phase::current,
                       Source::oracle,953,journalComplete,true){}
    ~CurrentOutcome()noexcept
    {
        if(why&&unsigned(*why)!=0)
        {
            static const Reason families[]={Reason::pass,Reason::shape,Reason::noHandle,Reason::authority,Reason::identity,Reason::pending,Reason::capacity,Reason::alias,Reason::alias,Reason::dependency,Reason::mutation,Reason::origin,Reason::exception};
            const unsigned value=unsigned(*why);
            failure(value<13?families[value]:Reason::exception,Source::oracle,953);
        }
        if(why){operation.record.currentWhy=static_cast<unsigned char>(unsigned(*why));operation.result(unsigned(*why)==0);}
    }
};
inline const char* name(Reason r)noexcept
{
    static const char* const names[]={"pass","no-handle","incomplete","alias-index","capacity","journal-deadline","authority","sequence","identity","shape","alias","origin","pending","mutation","dependency","exception"};
    const unsigned n=unsigned(r);return n<unsigned(Reason::count)?names[n]:"invalid";
}
inline bool writeRecord(FILE* file,const char* text,std::size_t length)noexcept
{
    if(!length||length>4096||state.outputRecords>=32||state.outputBytes>128u*1024u||length>128u*1024u-state.outputBytes)
    {increment(state.dropped);return false;}
    if(std::fwrite(text,1,length,file)!=length){state.outputError=true;increment(state.dropped);return false;}
    state.outputBytes+=length;++state.outputRecords;return true;
}
inline void flush(const char* path)noexcept
{
    if(!state.enabled||state.sealed)return;
    state.sealed=true;FILE* file=std::fopen(path,"wb");
    if(!file){state.outputError=true;increment(state.dropped);return;}
    char buffer[4096];
    int size=std::snprintf(buffer,sizeof(buffer),"{\"schema\":\"dctwin-legacy-journal-observation/v1\",\"siteSchema\":\"p17-held-be7595-df5a4\",\"kind\":\"header\",\"rank\":%d,\"proposed\":%llu,\"captured\":%llu,\"complete\":false,\"scope\":\"observation-only\"}\n",
        state.rank,(unsigned long long)state.proposed,(unsigned long long)state.captured);
    bool ok=size>0&&std::size_t(size)<sizeof(buffer)&&writeRecord(file,buffer,std::size_t(size));
    auto emit=[&](const Record& r,const DependencyBinding* binding)
    {
        char work[64];
        if(std::isfinite(r.workSeconds))std::snprintf(work,sizeof(work),"%.17g",r.workSeconds);
        else std::snprintf(work,sizeof(work),"null");
        const int length=std::snprintf(buffer,sizeof(buffer),
            "{\"kind\":\"%s\",\"phase\":%u,\"source\":%u,\"site\":%u,\"reason\":\"%s\",\"ordinal\":%llu,\"epoch\":%llu,\"resultKnown\":%s,\"result\":%s,\"completeKnown\":%s,\"preComplete\":%s,\"postComplete\":%s,\"currentWhy\":%u,\"entries\":%llu,\"payload\":%llu,\"indexBytes\":%llu,\"workSeconds\":%s,\"ids\":[%lld,%lld,%lld,%lld],\"contextIds\":[%lld,%lld,%lld,%lld]}\n",
            r.kind==1?"first-invalidating":"outcome",unsigned(r.phase),unsigned(r.source),r.site,name(Reason(r.reason)),
            (unsigned long long)r.ordinal,(unsigned long long)r.epoch,r.resultKnown?"true":"false",r.result?"true":"false",
            r.completeKnown?"true":"false",r.preComplete?"true":"false",r.postComplete?"true":"false",unsigned(r.currentWhy),
            (unsigned long long)r.entries,(unsigned long long)r.bytes,(unsigned long long)r.indexBytes,work,
            (long long)r.a,(long long)r.b,(long long)r.c,(long long)r.d,
            (long long)r.contextA,(long long)r.contextB,(long long)r.contextC,(long long)r.contextD);
        if(!(length>0&&std::size_t(length)<sizeof(buffer)))return false;
        int total=length;
        if(binding&&binding->valid)
        {
            char extra[2048];
            const auto& b=*binding;
            const int n=std::snprintf(extra,sizeof(extra),
                ",\"dependencyBinding\":{\"scope\":\"matching-item-and-counts-only\",\"entry\":%llu,\"keep\":%lld,\"drop\":%lld,\"snapshotSide\":%u,\"kind\":%u,\"legacyCell\":%lld,\"viewCell\":%lld,\"legacyMembership\":%u,\"viewMembership\":%u,\"legacyParent\":%lld,\"viewParent\":%lld,\"legacyRank\":%lld,\"viewRank\":%lld,\"legacyFace\":%lld,\"viewFace\":%lld,\"legacyAlias\":%lld,\"viewAlias\":%lld,\"legacyMember\":%lld,\"viewMember\":%lld,\"coreFirst\":%lld,\"legacyCounts\":[%llu,%llu,%llu,%llu],\"viewCounts\":[%llu,%llu,%llu,%llu,%llu],\"lookupBounded\":%s,\"clockUnknown\":%s}}\n",
                (unsigned long long)b.entry,(long long)b.keep,(long long)b.drop,b.side,b.kind,
                (long long)b.legacyCell,(long long)b.viewCell,b.legacyMembership,b.viewMembership,
                (long long)b.legacyParent,(long long)b.viewParent,(long long)b.legacyRank,(long long)b.viewRank,
                (long long)b.legacyFace,(long long)b.viewFace,(long long)b.legacyAlias,(long long)b.viewAlias,
                (long long)b.legacyMember,(long long)b.viewMember,(long long)b.coreFirst,
                (unsigned long long)b.legacyCounts[0],(unsigned long long)b.legacyCounts[1],(unsigned long long)b.legacyCounts[2],(unsigned long long)b.legacyCounts[3],
                (unsigned long long)b.viewCounts[0],(unsigned long long)b.viewCounts[1],(unsigned long long)b.viewCounts[2],(unsigned long long)b.viewCounts[3],(unsigned long long)b.viewCounts[4],
                b.lookupBounded?"true":"false",b.clockUnknown?"true":"false");
            // Diagnostic bytes must never suppress an original first cause/summary.
            if(n>0&&std::size_t(n)<sizeof(extra)&&std::size_t(length-2+n)<sizeof(buffer))
            {std::memcpy(buffer+length-2,extra,n+1);total=length-2+n;}
        }
        if(binding&&r.reason==unsigned(Reason::dependency)&&total==length)
        {
            static const char missing[]=",\"dependencyBinding\":null,\"dependencyBindingMissing\":true}\n";
            if(std::size_t(length-2)+sizeof(missing)<=sizeof(buffer))
            {std::memcpy(buffer+length-2,missing,sizeof(missing));total=length-2+sizeof(missing)-1;}
        }
        return writeRecord(file,buffer,std::size_t(total));
    };
    if(ok&&state.haveFirst)ok=emit(state.first,nullptr);
    for(unsigned i=0;ok&&i<state.used;++i)ok=emit(state.records[i],&dependencyStorage().samples[i]);
    if(ok)
    {
        std::size_t used=0;
        auto append=[&](const char* format,unsigned long long value)
        {
            if(used>=sizeof(buffer))return false;
            const int n=std::snprintf(buffer+used,sizeof(buffer)-used,format,value);
            if(n<=0||std::size_t(n)>=sizeof(buffer)-used)return false;
            used+=std::size_t(n);return true;
        };
        ok=append("{\"kind\":\"summary\",\"dropped\":%llu",(unsigned long long)state.dropped)
          &&append(",\"saturated\":%llu",(unsigned long long)state.saturated)
          &&append(",\"payloadHigh\":%llu",(unsigned long long)state.payloadHigh)
          &&append(",\"indexHigh\":%llu",(unsigned long long)state.indexHigh)
          &&append(",\"entriesHigh\":%llu",(unsigned long long)state.entriesHigh)
          &&append(",\"counts\":[%llu",(unsigned long long)state.reasonCount[0][0]);
        for(unsigned p=0;ok&&p<unsigned(Phase::count);++p)
            for(unsigned r=(p?0:1);ok&&r<unsigned(Reason::count);++r)
                ok=append(",%llu",(unsigned long long)state.reasonCount[p][r]);
        ok=ok&&append("],\"attempt\":[%llu",(unsigned long long)state.attempt[0]);
        for(unsigned p=1;ok&&p<unsigned(Phase::count);++p)ok=append(",%llu",(unsigned long long)state.attempt[p]);
        ok=ok&&append("],\"pass\":[%llu",(unsigned long long)state.passed[0]);
        for(unsigned p=1;ok&&p<unsigned(Phase::count);++p)ok=append(",%llu",(unsigned long long)state.passed[p]);
        ok=ok&&append("],\"fail\":[%llu",(unsigned long long)state.failed[0]);
        for(unsigned p=1;ok&&p<unsigned(Phase::count);++p)ok=append(",%llu",(unsigned long long)state.failed[p]);
        ok=ok&&append("],\"complete\":true",0);
        if(ok&&used+2<sizeof(buffer)){buffer[used++]='}';buffer[used++]='\n';buffer[used]='\0';}
        else ok=false;
        if(ok)ok=writeRecord(file,buffer,used);
    }
    if(!ok){state.outputError=true;increment(state.dropped);}
    if(std::fclose(file)!=0){state.outputError=true;increment(state.dropped);}
}
} // dcCartLegacyJournalObservation
namespace dcCartCompleteCavityOriginCore
{
constexpr std::size_t maxOrigins=8,maxCavityCells=128,maxCompositeSets=4096;
constexpr std::size_t maxKeyBytes=64u*1024u*1024u,maxCompositeBytes=1024u*1024u;
// Independent components retain their separate singleton keys. These are
// proposal-work/scratch limits, not origin/geometry/quality thresholds.
constexpr std::size_t maxIndependentComponents=128,maxIndependentCellReferences=512;
constexpr std::size_t maxIndependentScratchBytes=16u*1024u;
enum class Refusal : unsigned char
{
    none,missingAuthority,budget,cellCapacity,invalidIndex,unknownOrigin,
    originCapacity,compositeCapacity,repeatInitialization,invalidBinding,count
};
struct Fingerprint
{
    std::string raw,roles,materialKey,material,producer,boundaryMembership;
};
struct Origin
{
    dcCartFluidOriginSnapshotCore::Identity identity;
    std::vector<std::array<int,3>> facets;
};
inline bool digest(const std::string& s)
{
    return s.size()==64 && std::all_of(s.begin(),s.end(),[](char c)
        { return (c>='0' && c<='9') || (c>='a' && c<='f'); });
}
inline bool validFingerprint(const Fingerprint& p)
{
    return digest(p.raw) && digest(p.roles) && !p.materialKey.empty()
        && digest(p.material) && digest(p.producer) && digest(p.boundaryMembership);
}
// A complete normal-origin permission is only a necessary source guard.
// It does not authorize any topology replacement or geometry/quality result.
inline bool verifyMany
(
    const dcCartFluidOriginSnapshotCore::Snapshot& snapshot,
    const std::vector<Origin>& origins,const Fingerprint& fresh,int actualRank,
    const std::function<bool()>& available
)
{
    if (dcCartLegacyJournalObservation::observe(!available,dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::core,47) || dcCartLegacyJournalObservation::observe(!available(),dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::core,47) || actualRank<0 || !validFingerprint(fresh)
     || origins.empty() || dcCartLegacyJournalObservation::greater(origins.size(),maxOrigins,dcCartLegacyJournalObservation::Source::core,48)
     || snapshot.boundaryMembershipDigest!=fresh.boundaryMembership) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::origin,dcCartLegacyJournalObservation::Source::core,49);
    std::set<int> unique;
    for (const Origin& o:origins)
    {
        if (dcCartLegacyJournalObservation::observe(!available(),dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::core,53) || std::get<0>(o.identity)!=actualRank
         || !unique.insert(std::get<1>(o.identity)).second
         || !dcCartFluidOriginSnapshotCore::verify
            (snapshot,o.identity,o.facets,fresh.raw,fresh.roles,fresh.materialKey,
             fresh.material,fresh.producer)) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::origin,dcCartLegacyJournalObservation::Source::core,57);
    }
    const bool p17Available=available();
    if(!p17Available)dcCartLegacyJournalObservation::failure(dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::core,59);
    return p17Available;
}
// Native cell slots keep one key, not a copy of every source polygon.
// Nonnegative keys name immutable pre-cone parent slots. -1 is unknown.
// Other negative keys refer to a bounded, interned complete-origin set.
template<class Label> class Lineage
{
    struct Composite
    {
        std::array<Label,maxOrigins> parents{};
        unsigned char count=0;
    };
    static_assert(std::is_integral<Label>::value && std::is_signed<Label>::value
        && sizeof(Label)>=4,"normal lineage requires signed >=32-bit native handles");
    bool started_=false,active_=false;
    std::uint64_t revision_=0;
    // Single-threaded phase exclusion: never a substitute for the caller's
    // mesh/shadow exclusion or its real collective physical vote.
    bool publicationReserved_=false;
    std::uint64_t publicationNonce_=0;
    unsigned operationDepth_=0;
    class MutationScope
    {
        Lineage* owner_;
    public:
        explicit MutationScope(Lineage& owner) noexcept
        :owner_(owner.publicationReserved_ ? nullptr : &owner)
        { if(owner_)++owner_->operationDepth_; }
        ~MutationScope() noexcept { if(owner_)--owner_->operationDepth_; }
        MutationScope(const MutationScope&)=delete;
        MutationScope& operator=(const MutationScope&)=delete;
        explicit operator bool()const noexcept{return owner_!=nullptr;}
    };
    bool publicationUnlocked()const noexcept{return !publicationReserved_;}
    std::vector<Label> keys_;
    // Fixed-size entries: no nested vectors/maps with unbounded capacities.
    std::vector<Composite> composite_;
    std::array<std::size_t,std::size_t(Refusal::count)> refusals_{};
    void refuse(Refusal reason) { ++refusals_[std::size_t(reason)]; }
    void invalidate()
    {
        active_=false;std::vector<Label>().swap(keys_);
        std::vector<Composite>().swap(composite_);
    }
    bool reserveOne()
    {
        const std::size_t limit=maxKeyBytes/sizeof(Label);
        if (keys_.size()>=limit) return false;
        if (keys_.size()==keys_.capacity())
        {
            const std::size_t next=std::min(limit,std::max(std::size_t(1),keys_.capacity()*2));
            keys_.reserve(next);
        }
        return true;
    }
    bool decode(Label key,std::vector<Label>& out) const
    {
        if (key>=0) { out.push_back(key);return true; }
        if (key==Label(-1)) return false;
        const std::size_t index=std::size_t(-(key+Label(2)));
        if (index>=composite_.size()) return false;
        const auto& entry=composite_[index];
        out.insert(out.end(),entry.parents.begin(),entry.parents.begin()+entry.count);return true;
    }
public:
    Lineage()=default;
    // An independent copy has no reservation; source tokens retain their owner.
    Lineage(const Lineage& other)
      :started_(other.started_),active_(other.active_),revision_(other.revision_),
       keys_(other.keys_),composite_(other.composite_),refusals_(other.refusals_){}
    Lineage(Lineage&& other) noexcept
    {
        MutationScope operation(other);if(!operation)return;
        started_=other.started_;active_=other.active_;revision_=other.revision_;
        keys_=std::move(other.keys_);composite_=std::move(other.composite_);
        refusals_=other.refusals_;other.active_=false;++other.revision_;
    }
    Lineage& operator=(const Lineage& other)
    {
        if(this==&other)return *this;
        MutationScope operation(*this);if(!operation)return *this;
        Lineage prepared(other); // allocation failure leaves all live fields unchanged.
        const auto next=revision_+1;
        keys_.swap(prepared.keys_);composite_.swap(prepared.composite_);
        started_=other.started_;active_=other.active_;refusals_=other.refusals_;revision_=next;
        return *this;
    }
    Lineage& operator=(Lineage&& other) noexcept
    {
        if(this==&other)return *this;
        MutationScope operation(*this);if(!operation)return *this;
        MutationScope sourceOperation(other);if(!sourceOperation)return *this;
        const auto next=revision_+1;
        keys_=std::move(other.keys_);composite_=std::move(other.composite_);
        started_=other.started_;active_=other.active_;refusals_=other.refusals_;revision_=next;
        other.active_=false;++other.revision_;return *this;
    }
    // Revoke optional current-origin permission after an unsupported atomic mutation.
    // Retain refusal/history fields; no fabricated clock or replacement proof.
    void revokePermission() noexcept
    { MutationScope operation(*this);if(!operation)return;++revision_;invalidate(); }
    bool active() const { return active_; }
    std::uint64_t revision() const noexcept { return revision_; } // stale prepared geometry fence only
    bool started() const { return started_; }
    std::size_t size() const { return keys_.size(); }
    std::size_t compositeCount() const { return composite_.size(); }
    // Read-only observation export; no decode/permission/clock/counter call.
    const std::vector<Label>& observationKeys() const noexcept { return keys_; }
    const auto& observationComposites() const noexcept { return composite_; }
    std::size_t keyCapacityBytes() const { return keys_.capacity()*sizeof(Label); }
    std::size_t compositeCapacityBytes() const { return composite_.capacity()*sizeof(Composite); }
    const std::array<std::size_t,std::size_t(Refusal::count)>& refusals() const { return refusals_; }
    bool initialize
    (
        std::size_t cells,const std::function<bool(Label)>& recordedParent,
        const std::function<bool()>& available
    )
    {
        MutationScope operation(*this);if(!operation)return false;
        ++revision_; // Every old mutation path invalidates prepared tokens.
        // Never recapture a later accepted geometry as the earliest origin.
        if (started_) { refuse(Refusal::repeatInitialization);invalidate();return false; }
        started_=true;
        if (!recordedParent || !available)
        { refuse(Refusal::missingAuthority);return false; }
        if (!available()) { refuse(Refusal::budget);return false; }
        if (cells>maxKeyBytes/sizeof(Label)
         || cells>std::size_t(std::numeric_limits<Label>::max()))
        { refuse(Refusal::cellCapacity);return false; }
        try
        {
            keys_.assign(cells,Label(-1));
            for (std::size_t c=0;c<cells;++c)
            {
                if ((c&1023u)==0 && !available())
                { refuse(Refusal::budget);invalidate();return false; }
                if (recordedParent(Label(c))) keys_[c]=Label(c);
            }
        }
        catch (const std::bad_alloc&)
        { refuse(Refusal::cellCapacity);invalidate();return false; }
        if (!available()) { refuse(Refusal::budget);invalidate();return false; }
        active_=true;return true;
    }
    void inherit(Label child,Label parent,const std::function<bool()>& available)
    {
        MutationScope operation(*this);if(!operation)return;
        if (!active_) return;
        ++revision_;
        if (child<0 || std::size_t(child)!=keys_.size() || parent<0
         || std::size_t(parent)>=keys_.size())
        { refuse(Refusal::invalidIndex);invalidate();return; }
        if ((keys_.size()&1023u)==0 && (!available || !available()))
        { refuse(Refusal::budget);invalidate();return; }
        try
        {
            if (!reserveOne())
            { refuse(Refusal::cellCapacity);invalidate();return; }
            keys_.push_back(keys_[parent]);
        }
        catch (const std::bad_alloc&)
        { refuse(Refusal::cellCapacity);invalidate(); }
    }
    bool join(Label keep,Label drop,const std::function<bool()>& available)
    {
        MutationScope operation(*this);if(!operation)return false;
        if (!active_) return false;
        ++revision_;
        if (!available || !available())
        { refuse(Refusal::budget);invalidate();return false; }
        if (keep<0 || drop<0 || keep==drop || std::size_t(keep)>=keys_.size()
         || std::size_t(drop)>=keys_.size())
        { refuse(Refusal::invalidIndex);invalidate();return false; }
        std::vector<Label> parents;
        if (!decode(keys_[keep],parents) || !decode(keys_[drop],parents))
        { refuse(Refusal::unknownOrigin);keys_[keep]=keys_[drop]=Label(-1);return false; }
        std::sort(parents.begin(),parents.end());
        parents.erase(std::unique(parents.begin(),parents.end()),parents.end());
        if (parents.size()>maxOrigins)
        { refuse(Refusal::originCapacity);keys_[keep]=keys_[drop]=Label(-1);return false; }
        Label key=parents.front();
        if (parents.size()>1)
        {
            std::size_t index=0;
            for (;index<composite_.size();++index)
            {
                if ((index&63u)==0 && !available())
                { refuse(Refusal::budget);invalidate();return false; }
                const auto& entry=composite_[index];
                if (entry.count==parents.size()
                 && std::equal(parents.begin(),parents.end(),entry.parents.begin())) break;
            }
            if (index==composite_.size())
            {
                const std::size_t limit=std::min(maxCompositeSets,maxCompositeBytes/sizeof(Composite));
                if (index>=limit)
                { refuse(Refusal::compositeCapacity);keys_[keep]=keys_[drop]=Label(-1);return false; }
                try
                {
                    if (composite_.empty()) composite_.reserve(limit);
                    Composite entry;entry.count=parents.size();
                    std::copy(parents.begin(),parents.end(),entry.parents.begin());
                    composite_.push_back(entry);
                }
                catch (const std::bad_alloc&)
                { refuse(Refusal::compositeCapacity);keys_[keep]=keys_[drop]=Label(-1);return false; }
            }
            key=Label(-2)-Label(index);
        }
        keys_[keep]=key;keys_[drop]=Label(-1);return true;
    }
    bool originsForCells(const std::vector<Label>& cells,std::vector<Label>& out) const
    {
        out.clear();
        if (!active_ || cells.empty() || dcCartLegacyJournalObservation::greater(cells.size(),maxCavityCells,dcCartLegacyJournalObservation::Source::core,236)) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::origin,dcCartLegacyJournalObservation::Source::core,236);
        std::set<Label> uniqueCells;
        for (Label cell:cells)
            if (cell<0 || std::size_t(cell)>=keys_.size()
             || !uniqueCells.insert(cell).second || !decode(keys_[cell],out))
            { out.clear();return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::origin,dcCartLegacyJournalObservation::Source::core,241); }
        std::sort(out.begin(),out.end());out.erase(std::unique(out.begin(),out.end()),out.end());
        if (out.empty() || out.size()>maxOrigins) { out.clear();return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::capacity,dcCartLegacyJournalObservation::Source::core,243); }
        return true;
    }
    // Source-only preparation; no geometry/coverage permission and no live mutation.
    class PreparedReplacement
    {
        friend class Lineage<Label>;
        const Lineage* owner_=nullptr;
        std::uint64_t revision_=0;
        bool ready_=false,useDenseCopy_=false,replaceComposite_=false;
        Label key_=Label(-1);
        std::size_t finalSize_=0;
        std::vector<Label> cells_,oldKeys_,slots_,parents_,dense_;
        std::vector<Composite> composites_;
        Refusal refusal_=Refusal::none;
    public:
        PreparedReplacement()=default;
        PreparedReplacement(const PreparedReplacement&)=default;
        PreparedReplacement& operator=(const PreparedReplacement& other)
        {
            if(this==&other)return *this;
            PreparedReplacement staged(other); // failure leaves this token unchanged.
            *this=std::move(staged);return *this; // no-throw ownership transfer.
        }
        PreparedReplacement(PreparedReplacement&& other) noexcept
        {*this=std::move(other);}
        PreparedReplacement& operator=(PreparedReplacement&& other) noexcept
        {
            if(this==&other)return *this;
            owner_=other.owner_;revision_=other.revision_;ready_=other.ready_;
            useDenseCopy_=other.useDenseCopy_;replaceComposite_=other.replaceComposite_;
            key_=other.key_;finalSize_=other.finalSize_;refusal_=other.refusal_;
            cells_=std::move(other.cells_);oldKeys_=std::move(other.oldKeys_);
            slots_=std::move(other.slots_);parents_=std::move(other.parents_);
            dense_=std::move(other.dense_);composites_=std::move(other.composites_);
            other.owner_=nullptr;other.ready_=false;other.revision_=0;
            other.useDenseCopy_=other.replaceComposite_=false;
            other.key_=Label(-1);other.finalSize_=0;return *this;
        }
        bool ready()const{return ready_;}
        Refusal refusal()const{return refusal_;}
        const std::vector<Label>& origins()const{return parents_;}
        std::size_t densePreparedBytes()const{return dense_.capacity()*sizeof(Label);}
        std::size_t compositePreparedBytes()const{return composites_.capacity()*sizeof(Composite);}
    };
private:
    // Bounded metadata admission precedes any external clock/source callback.
    // Prepared payloads are private; dense outside slots are prepared internally.
    bool validPreparedStructure(const PreparedReplacement& p)const noexcept
    {return publicationUnlocked()&&preparedStructure(p);}
    // Internal lease admission holds the reservation before external callbacks.
    bool preparedStructure(const PreparedReplacement& p)const noexcept
    {
        if(p.cells_.empty()||p.cells_.size()>maxCavityCells
         ||p.slots_.empty()||p.slots_.size()>maxCavityCells
         ||p.parents_.empty()||p.parents_.size()>maxOrigins
         ||p.oldKeys_.size()!=p.cells_.size()
         ||p.finalSize_<keys_.size()||p.finalSize_-keys_.size()>p.slots_.size()
         ||p.finalSize_>maxKeyBytes/sizeof(Label)
         ||p.finalSize_>std::size_t(std::numeric_limits<Label>::max()))return false;
        for(std::size_t i=0;i<p.parents_.size();++i)
            if(p.parents_[i]<0||(i&&p.parents_[i]<=p.parents_[i-1]))return false;
        for(std::size_t i=0;i<p.cells_.size();++i)
        {
            if(p.cells_[i]<0||std::size_t(p.cells_[i])>=keys_.size()
             ||p.oldKeys_[i]==Label(-1))return false;
            for(std::size_t j=0;j<i;++j)if(p.cells_[i]==p.cells_[j])return false;
        }
        std::size_t appended=0;
        for(std::size_t i=0;i<p.slots_.size();++i)
        {
            const Label slot=p.slots_[i];
            if(slot<0||std::size_t(slot)>=p.finalSize_)return false;
            for(std::size_t j=0;j<i;++j)if(slot==p.slots_[j])return false;
            if(std::size_t(slot)>=keys_.size())++appended;
            else if(std::find(p.cells_.begin(),p.cells_.end(),slot)==p.cells_.end())return false;
        }
        if(appended!=p.finalSize_-keys_.size())return false;
        if(p.useDenseCopy_)
        {
            if(p.dense_.size()!=p.finalSize_
             ||p.dense_.capacity()>maxKeyBytes/sizeof(Label))return false;
            for(Label slot:p.slots_)if(p.dense_[slot]!=p.key_)return false;
            for(Label cell:p.cells_)
                if(std::find(p.slots_.begin(),p.slots_.end(),cell)==p.slots_.end()
                 &&p.dense_[cell]!=Label(-1))return false;
        }
        else if(!p.dense_.empty()||p.finalSize_>keys_.capacity())return false;
        if(p.parents_.size()==1)
            return p.key_==p.parents_[0]&&!p.replaceComposite_&&p.composites_.empty();
        if(p.key_>=Label(-1))return false;
        const std::size_t index=std::size_t(-(p.key_+Label(2)));
        const std::vector<Composite>& directory=p.replaceComposite_?p.composites_:composite_;
        if(p.replaceComposite_)
        {
            if(p.composites_.size()!=composite_.size()+1
             ||p.composites_.size()>maxCompositeSets
             ||p.composites_.capacity()>maxCompositeBytes/sizeof(Composite)
             ||index!=composite_.size())return false;
        }
        else if(!p.composites_.empty())return false;
        if(index>=directory.size()||directory[index].count!=p.parents_.size())return false;
        return std::equal(p.parents_.begin(),p.parents_.end(),directory[index].parents.begin());
    }
    static bool budgetSafe(const std::function<bool()>& available)
    {try{return available&&available();}catch(...){return false;}}
    static bool authoritySafe(const std::function<bool(const std::vector<Label>&)>& authority,
                              const std::vector<Label>& parents)
    {try{return authority&&authority(parents);}catch(...){return false;}}
public:
    bool prepareReplacement
    (
        const std::vector<Label>& cells,const std::vector<Label>& replacementSlots,
        const std::function<bool(const std::vector<Label>&)>& completeOriginAuthority,
        const std::function<bool()>& available,PreparedReplacement& out
    ) const
    {
        // This prepares an external token and may allocate/call user callbacks.
        // A sealed reservation forbids it; ordinary read-only origin queries do not.
        if(!publicationUnlocked())return false;
        out=PreparedReplacement();
        auto no=[&](Refusal r){out=PreparedReplacement();out.refusal_=r;return false;};
        // Complete caller bounds BEFORE clock, source scans or allocation.
        if(cells.empty()||cells.size()>maxCavityCells||replacementSlots.empty()
         ||replacementSlots.size()>maxCavityCells)return no(Refusal::cellCapacity);
        if(!active_||!completeOriginAuthority||!available)return no(Refusal::missingAuthority);
        if(!budgetSafe(available))return no(Refusal::budget);
        try
        {
            std::set<Label> core,newSlots;
            for(Label c:cells)
            {
                if(!budgetSafe(available))return no(Refusal::budget);
                if(c<0||std::size_t(c)>=keys_.size()||!core.insert(c).second)
                    return no(Refusal::invalidIndex);
                if(!decode(keys_[c],out.parents_))return no(Refusal::unknownOrigin);
            }
            std::sort(out.parents_.begin(),out.parents_.end());
            out.parents_.erase(std::unique(out.parents_.begin(),out.parents_.end()),out.parents_.end());
            if(out.parents_.empty()||out.parents_.size()>maxOrigins)return no(Refusal::originCapacity);
            std::size_t append=0;
            for(Label c:replacementSlots)
            {
                if(!budgetSafe(available))return no(Refusal::budget);
                if(c<0||!newSlots.insert(c).second)return no(Refusal::invalidBinding);
                if(std::size_t(c)<keys_.size())
                {if(!core.count(c))return no(Refusal::invalidBinding);}
                else ++append;
            }
            std::size_t next=keys_.size();
            for(Label c:newSlots)if(std::size_t(c)>=keys_.size())
            {if(std::size_t(c)!=next++)return no(Refusal::invalidBinding);}
            if(next>maxKeyBytes/sizeof(Label)||next>std::size_t(std::numeric_limits<Label>::max()))
                return no(Refusal::cellCapacity);
            if(!authoritySafe(completeOriginAuthority,out.parents_))return no(Refusal::missingAuthority);
            if(!budgetSafe(available))return no(Refusal::budget);
            out.owner_=this;out.revision_=revision_;out.cells_=cells;out.slots_=replacementSlots;
            for(Label c:cells)out.oldKeys_.push_back(keys_[c]);
            out.finalSize_=next;out.key_=out.parents_.front();
            if(out.parents_.size()>1)
            {
                std::size_t i=0;
                for(;i<composite_.size();++i)
                {
                    if((i&63u)==0&&!budgetSafe(available))return no(Refusal::budget);
                    if(composite_[i].count==out.parents_.size()
                     &&std::equal(out.parents_.begin(),out.parents_.end(),composite_[i].parents.begin()))break;
                }
                if(i==composite_.size())
                {
                    const std::size_t limit=std::min(maxCompositeSets,maxCompositeBytes/sizeof(Composite));
                    if(i>=limit)return no(Refusal::compositeCapacity);
                    out.composites_.reserve(i+1);
                    if(out.composites_.capacity()>maxCompositeBytes/sizeof(Composite))
                        return no(Refusal::compositeCapacity);
                    for(std::size_t j=0;j<i;++j)
                    {if((j&63u)==0&&!budgetSafe(available))return no(Refusal::budget);
                     out.composites_.push_back(composite_[j]);}
                    Composite entry;entry.count=out.parents_.size();
                    std::copy(out.parents_.begin(),out.parents_.end(),entry.parents.begin());
                    out.composites_.push_back(entry);out.replaceComposite_=true;
                }
                out.key_=Label(-2)-Label(i);
            }
            // Usually dense append capacity is already sufficient. Otherwise
            // prepare an owned <=64MiB copy; never reserve or modify live keys.
            out.useDenseCopy_=append>keys_.capacity()-keys_.size();
            if(out.useDenseCopy_)
            {
                const std::size_t limit=maxKeyBytes/sizeof(Label);
                const std::size_t capacity=std::min(keys_.capacity(),limit);
                const std::size_t half=capacity/2;
                const std::size_t grown=half>limit-capacity?limit:capacity+half;
                out.dense_.reserve(std::max(out.finalSize_,grown));
                if(out.dense_.capacity()>maxKeyBytes/sizeof(Label))return no(Refusal::cellCapacity);
                for(std::size_t j=0;j<keys_.size();++j)
                {if((j&1023u)==0&&!budgetSafe(available))return no(Refusal::budget);
                 out.dense_.push_back(keys_[j]);}
                out.dense_.resize(out.finalSize_,Label(-1));
                for(Label c:cells)out.dense_[c]=Label(-1);
                for(Label c:replacementSlots)out.dense_[c]=out.key_;
            }
            if(!budgetSafe(available))return no(Refusal::budget);
            out.ready_=true;return true;
        }
        catch(const std::bad_alloc&){return no(Refusal::cellCapacity);}
        catch(const std::length_error&){return no(Refusal::cellCapacity);}
    }
    bool commitReplacement
    (
        PreparedReplacement& prepared,
        const std::function<bool(const std::vector<Label>&)>& completeOriginAuthority,
        const std::function<bool()>& available
    )
    {
        MutationScope operation(*this);if(!operation)return false;
        auto no=[&](Refusal r){prepared.refusal_=r;return false;};
        if(!prepared.ready_||prepared.owner_!=this||prepared.revision_!=revision_||!active_
         ||!validPreparedStructure(prepared))return no(Refusal::invalidBinding);
        if(!budgetSafe(available))return no(Refusal::budget);
        for(std::size_t i=0;i<prepared.cells_.size();++i)
        {
            if(!budgetSafe(available))return no(Refusal::budget);
            const Label c=prepared.cells_[i];
            if(c<0||std::size_t(c)>=keys_.size()||keys_[c]!=prepared.oldKeys_[i])
                return no(Refusal::invalidBinding);
        }
        if(!authoritySafe(completeOriginAuthority,prepared.parents_))return no(Refusal::missingAuthority);
        // No allocator/clock/authority call after the final preflight.
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!prepared.useDenseCopy_&&prepared.finalSize_>keys_.capacity())
            return no(Refusal::cellCapacity);
        if(prepared.replaceComposite_)composite_.swap(prepared.composites_);
        if(prepared.useDenseCopy_)keys_.swap(prepared.dense_);
        else
        {
            keys_.resize(prepared.finalSize_,Label(-1)); // preflight capacity, integral no-throw writes.
            for(Label c:prepared.cells_)keys_[c]=Label(-1);
            for(Label c:prepared.slots_)keys_[c]=prepared.key_;
        }
        ++revision_;prepared.ready_=false;prepared.refusal_=Refusal::none;return true;
    }
    // Two independently prepared same-origin components publish together.
    // Keep each original root; never form a combined composite directory key.
    bool commitIndependentSameOriginPair
    (
        PreparedReplacement& first,PreparedReplacement& second,
        const std::function<bool(const std::vector<Label>&)>& completeOriginAuthority,
        const std::function<bool()>& available
    )
    {
        MutationScope operation(*this);if(!operation)return false;
        auto no=[&](Refusal r){first.refusal_=second.refusal_=r;return false;};
        if(&first==&second||!active_)return no(Refusal::invalidBinding);
        for(const PreparedReplacement* p:{&first,&second})
        {
            if(!p->ready_||p->owner_!=this||p->revision_!=revision_
             ||!validPreparedStructure(*p)||p->cells_.size()!=2||p->slots_.size()!=1
             ||p->parents_.size()!=1||p->useDenseCopy_||p->replaceComposite_
             ||p->finalSize_!=keys_.size())return no(Refusal::invalidBinding);
        }
        if(first.parents_[0]==second.parents_[0])return no(Refusal::invalidBinding);
        for(Label a:first.cells_)for(Label b:second.cells_)
            if(a==b)return no(Refusal::invalidBinding);
        const auto stillCurrent=[&]() noexcept {
            if(!active_||first.revision_!=revision_||second.revision_!=revision_
             ||first.finalSize_!=keys_.size()||second.finalSize_!=keys_.size())return false;
            for(const PreparedReplacement* p:{&first,&second})
            {
                if(!p->ready_||p->owner_!=this||p->cells_.size()!=2||p->oldKeys_.size()!=2
                 ||p->slots_.size()!=1||p->parents_.size()!=1||!validPreparedStructure(*p))return false;
                for(std::size_t i=0;i<2;++i)
                    if(p->cells_[i]<0||std::size_t(p->cells_[i])>=keys_.size()
                     ||keys_[p->cells_[i]]!=p->oldKeys_[i])return false;
            }
            if(first.parents_[0]==second.parents_[0]
             ||first.useDenseCopy_||second.useDenseCopy_
             ||first.replaceComposite_||second.replaceComposite_)return false;
            for(Label a:first.cells_)for(Label b:second.cells_)if(a==b)return false;
            return true;
        };
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        for(const PreparedReplacement* p:{&first,&second})
        {
            for(std::size_t i=0;i<p->cells_.size();++i)
            {
                if(!budgetSafe(available))return no(Refusal::budget);
                if(!stillCurrent())return no(Refusal::invalidBinding);
            }
            if(!authoritySafe(completeOriginAuthority,p->parents_))return no(Refusal::missingAuthority);
            if(!stillCurrent())return no(Refusal::invalidBinding);
        }
        // Final original-clock check and bounded stale fence precede all writes.
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        for(PreparedReplacement* p:{&first,&second})
        {
            for(Label c:p->cells_)keys_[c]=Label(-1);
            keys_[p->slots_[0]]=p->key_;
            p->ready_=false;p->refusal_=Refusal::none;
        }
        ++revision_;return true;
    }
    // Commit a complete bounded set of disjoint same-origin components.
    // Roots may repeat across components; each component keeps its own key.
    // Geometry, full affected Stock, and real future-peer permission belong to
    // the caller's complete physical transaction, never to this lineage API.
    bool commitIndependentSameOriginComponents
    (
        const std::vector<PreparedReplacement*>& components,
        const std::function<bool(const std::vector<Label>&)>& completeOriginAuthority,
        const std::function<bool()>& available
    )
    {
        MutationScope operation(*this);if(!operation)return false;
        // Reject the whole proposal; never accept a truncated prefix.
        const std::size_t count=components.size();
        if(count<2||count>maxIndependentComponents)return false;
        std::array<PreparedReplacement*,maxIndependentComponents> prepared{};
        struct ComponentPreimage
        { Label keeper,key,root;std::size_t memberCount; };
        std::array<ComponentPreimage,maxIndependentComponents> preimage{};
        // Reuse the second fixed array: first disjointness sort, then old keys.
        std::array<Label,maxIndependentCellReferences> members{},oldKeys{};
        static_assert(sizeof(prepared)+sizeof(preimage)+sizeof(members)+sizeof(oldKeys)
            <=maxIndependentScratchBytes,"bounded independent-component scratch");
        std::copy(components.begin(),components.end(),prepared.begin());
        auto no=[&](Refusal r){
            for(std::size_t i=0;i<count;++i)
                if(prepared[i])prepared[i]->refusal_=r;
            return false;
        };
        if(!active_||!completeOriginAuthority||!available)
            return no(Refusal::missingAuthority);
        const std::uint64_t batchRevision=revision_;
        const std::size_t finalSize=keys_.size();
        std::size_t used=0;
        for(std::size_t i=0;i<count;++i)
        {
            const PreparedReplacement* p=prepared[i];
            if(!p)return no(Refusal::invalidBinding);
            for(std::size_t j=0;j<i;++j)
                if(p==prepared[j])return no(Refusal::invalidBinding);
            if(!p->ready_||p->owner_!=this||p->revision_!=batchRevision
             ||!validPreparedStructure(*p)||p->cells_.size()<2||p->slots_.size()!=1
             ||p->parents_.size()!=1||p->useDenseCopy_||p->replaceComposite_
             ||p->finalSize_!=finalSize)return no(Refusal::invalidBinding);
            if(p->cells_.size()>maxIndependentCellReferences-used)
                return no(Refusal::cellCapacity);
            preimage[i]={p->slots_[0],p->key_,p->parents_[0],p->cells_.size()};
            for(Label cell:p->cells_)members[used++]=cell;
        }
        if(used>finalSize)return no(Refusal::invalidBinding);
        // Complete publisher preimage and disjointness BEFORE any callback.
        // Sorting is bounded integral work; no new source/clock is introduced.
        std::copy(members.begin(),members.begin()+used,oldKeys.begin());
        std::sort(oldKeys.begin(),oldKeys.begin()+used);
        if(std::adjacent_find(oldKeys.begin(),oldKeys.begin()+used)
            !=oldKeys.begin()+used)return no(Refusal::invalidBinding);
        std::size_t copied=0;
        for(std::size_t i=0;i<count;++i)
            for(Label key:prepared[i]->oldKeys_)oldKeys[copied++]=key;
        if(copied!=used)return no(Refusal::invalidBinding);
        // Every callback is followed by a complete no-allocation preimage fence.
        const auto stillCurrent=[&]() noexcept {
            if(!active_||revision_!=batchRevision||keys_.size()!=finalSize)return false;
            std::size_t offset=0;
            for(std::size_t i=0;i<count;++i)
            {
                const PreparedReplacement* p=prepared[i];
                if(!p->ready_||p->owner_!=this||p->revision_!=batchRevision
                 ||!validPreparedStructure(*p)||p->cells_.size()!=preimage[i].memberCount
                 ||p->slots_.size()!=1||p->parents_.size()!=1
                 ||p->slots_[0]!=preimage[i].keeper||p->key_!=preimage[i].key
                 ||p->parents_[0]!=preimage[i].root||p->useDenseCopy_||p->replaceComposite_
                 ||p->finalSize_!=finalSize||offset>used
                 ||p->cells_.size()>used-offset)return false;
                for(std::size_t j=0;j<p->cells_.size();++j)
                {
                    const std::size_t index=offset++;
                    const Label cell=p->cells_[j];
                    if(cell!=members[index]||cell<0||std::size_t(cell)>=keys_.size()
                     ||p->oldKeys_[j]!=oldKeys[index]||keys_[cell]!=oldKeys[index])return false;
                }
            }
            return offset==used;
        };
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        for(std::size_t i=0;i<count;++i)
        {
            if(!budgetSafe(available))return no(Refusal::budget);
            if(!stillCurrent())return no(Refusal::invalidBinding);
            if(!authoritySafe(completeOriginAuthority,prepared[i]->parents_))
                return no(Refusal::missingAuthority);
            if(!stillCurrent())return no(Refusal::invalidBinding);
        }
        // All source/clock/freshness checks finish before integral no-throw writes.
        // No allocator, callback, resize, or composite-directory change follows.
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        for(std::size_t i=0;i<count;++i)
        {
            PreparedReplacement* p=prepared[i];
            for(Label cell:p->cells_)keys_[cell]=Label(-1);
            keys_[p->slots_[0]]=p->key_;
            p->ready_=false;p->refusal_=Refusal::none;
        }
        ++revision_;return true;
    }
    // The caller keeps this owner and its mesh/shadow transaction alive and
    // exclusively owned from preparation through collective vote/publication.
    // This opaque lease is lineage-only: it cannot grant geometry/source/MPI
    // permission and never assumes a prior Point commit.
    class IndependentPublicationLease
    {
        friend class Lineage<Label>;
        struct ComponentPreimage
        {Label keeper,key,root;std::size_t offset,memberCount;};
        Lineage* owner_=nullptr;
        std::uint64_t nonce_=0,revision_=0;
        std::size_t finalSize_=0,count_=0,used_=0;
        bool ready_=false,readOnly_=false;
        std::array<ComponentPreimage,maxIndependentComponents> components_{};
        std::array<Label,maxIndependentCellReferences> members_{},oldKeys_{};
        void forget()noexcept
        {owner_=nullptr;nonce_=revision_=0;finalSize_=count_=used_=0;ready_=readOnly_=false;}
        void take(IndependentPublicationLease& other)noexcept
        {
            if(owner_)std::terminate(); // move into a nonempty reservation is misuse.
            owner_=other.owner_;nonce_=other.nonce_;revision_=other.revision_;
            finalSize_=other.finalSize_;count_=other.count_;used_=other.used_;
            ready_=other.ready_;readOnly_=other.readOnly_;components_=other.components_;
            members_=other.members_;oldKeys_=other.oldKeys_;other.forget();
        }
    public:
        IndependentPublicationLease()=default;
        IndependentPublicationLease(const IndependentPublicationLease&)=delete;
        IndependentPublicationLease& operator=(const IndependentPublicationLease&)=delete;
        IndependentPublicationLease(IndependentPublicationLease&& other)noexcept{take(other);}
        IndependentPublicationLease& operator=(IndependentPublicationLease&&)=delete;
        ~IndependentPublicationLease()noexcept{cancel();}
        void cancel()noexcept
        {
            if(owner_&&owner_->publicationReserved_&&owner_->publicationNonce_==nonce_)
                owner_->publicationReserved_=false;
            forget(); // release only, never mutate live keys/composites/revision.
        }
        bool readyForVote()const noexcept
        {
            if(!ready_||!owner_||!owner_->publicationReserved_
             ||owner_->publicationNonce_!=nonce_||owner_->operationDepth_!=0
             ||!owner_->active_||owner_->revision_!=revision_
             ||owner_->keys_.size()!=finalSize_)return false;
            for(std::size_t i=0;i<used_;++i)
                if(members_[i]<0||std::size_t(members_[i])>=finalSize_
                 ||owner_->keys_[members_[i]]!=oldKeys_[i])return false;
            return true;
        }
    };
    static_assert(sizeof(IndependentPublicationLease)<=maxIndependentScratchBytes,
        "bounded immutable lineage publication lease");
    bool prepareIndependentSameOriginPublication
    (
        const std::vector<PreparedReplacement*>& components,
        const std::function<bool(const std::vector<Label>&)>& completeOriginAuthority,
        const std::function<bool()>& available,IndependentPublicationLease& out
    )
    {
        // No lease may be acquired inside an unfinished old mutation callback.
        // Acquire the exclusion before any fallible external source/clock call.
        if(out.owner_||publicationReserved_||operationDepth_!=0
         ||revision_==std::numeric_limits<std::uint64_t>::max()
         ||publicationNonce_==std::numeric_limits<std::uint64_t>::max())return false;
        // Reject the whole proposal; never accept a truncated prefix.
        const std::size_t count=components.size();
        if(count<1||count>maxIndependentComponents)return false;
        std::array<PreparedReplacement*,maxIndependentComponents> prepared{};
        IndependentPublicationLease staged;
        staged.owner_=this;staged.nonce_=++publicationNonce_;
        staged.revision_=revision_;staged.finalSize_=keys_.size();
        publicationReserved_=true;
        auto& preimage=staged.components_;
        // Reuse oldKeys before callbacks for the complete disjointness sort.
        auto& members=staged.members_;auto& oldKeys=staged.oldKeys_;
        static_assert(sizeof(prepared)+sizeof(staged)<=maxIndependentScratchBytes,
            "bounded lease preparation storage");
        std::copy(components.begin(),components.end(),prepared.begin());
        auto no=[&](Refusal r){
            for(std::size_t i=0;i<count;++i)
                if(prepared[i])prepared[i]->refusal_=r;
            return false;
        };
        if(!active_||!completeOriginAuthority||!available)
            return no(Refusal::missingAuthority);
        const std::uint64_t batchRevision=revision_;
        const std::size_t finalSize=keys_.size();
        std::size_t used=0;
        for(std::size_t i=0;i<count;++i)
        {
            const PreparedReplacement* p=prepared[i];
            if(!p)return no(Refusal::invalidBinding);
            for(std::size_t j=0;j<i;++j)
                if(p==prepared[j])return no(Refusal::invalidBinding);
            if(!p->ready_||p->owner_!=this||p->revision_!=batchRevision
             ||!preparedStructure(*p)||p->cells_.size()<2||p->slots_.size()!=1
             ||p->parents_.size()!=1||p->useDenseCopy_||p->replaceComposite_
             ||p->finalSize_!=finalSize)return no(Refusal::invalidBinding);
            if(p->cells_.size()>maxIndependentCellReferences-used)
                return no(Refusal::cellCapacity);
            preimage[i]={p->slots_[0],p->key_,p->parents_[0],used,p->cells_.size()};
            for(Label cell:p->cells_)members[used++]=cell;
        }
        if(used>finalSize)return no(Refusal::invalidBinding);
        // Complete publisher preimage and disjointness BEFORE any callback.
        // Sorting is bounded integral work; no new source/clock is introduced.
        std::copy(members.begin(),members.begin()+used,oldKeys.begin());
        std::sort(oldKeys.begin(),oldKeys.begin()+used);
        if(std::adjacent_find(oldKeys.begin(),oldKeys.begin()+used)
            !=oldKeys.begin()+used)return no(Refusal::invalidBinding);
        std::size_t copied=0;
        for(std::size_t i=0;i<count;++i)
            for(Label key:prepared[i]->oldKeys_)oldKeys[copied++]=key;
        if(copied!=used)return no(Refusal::invalidBinding);
        // Every callback is followed by a complete no-allocation preimage fence.
        const auto stillCurrent=[&]() noexcept {
            if(!publicationReserved_||publicationNonce_!=staged.nonce_||operationDepth_!=0
             ||!active_||revision_!=batchRevision||keys_.size()!=finalSize)return false;
            std::size_t offset=0;
            for(std::size_t i=0;i<count;++i)
            {
                const PreparedReplacement* p=prepared[i];
                if(!p->ready_||p->owner_!=this||p->revision_!=batchRevision
                 ||!preparedStructure(*p)||p->cells_.size()!=preimage[i].memberCount
                 ||p->slots_.size()!=1||p->parents_.size()!=1
                 ||p->slots_[0]!=preimage[i].keeper||p->key_!=preimage[i].key
                 ||p->parents_[0]!=preimage[i].root||p->useDenseCopy_||p->replaceComposite_
                 ||p->finalSize_!=finalSize||offset>used
                 ||p->cells_.size()>used-offset)return false;
                for(std::size_t j=0;j<p->cells_.size();++j)
                {
                    const std::size_t index=offset++;
                    const Label cell=p->cells_[j];
                    if(cell!=members[index]||cell<0||std::size_t(cell)>=keys_.size()
                     ||p->oldKeys_[j]!=oldKeys[index]||keys_[cell]!=oldKeys[index])return false;
                }
            }
            return offset==used;
        };
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        for(std::size_t i=0;i<count;++i)
        {
            if(!budgetSafe(available))return no(Refusal::budget);
            if(!stillCurrent())return no(Refusal::invalidBinding);
            if(!authoritySafe(completeOriginAuthority,prepared[i]->parents_))
                return no(Refusal::missingAuthority);
            if(!stillCurrent())return no(Refusal::invalidBinding);
        }
        // All source/clock/freshness checks finish before integral no-throw writes.
        // No allocator, callback, resize, or composite-directory change follows.
        if(!budgetSafe(available))return no(Refusal::budget);
        if(!stillCurrent())return no(Refusal::invalidBinding);
        // The fixed publisher plan owns every target/key/member preimage. The
        // source tokens may be destroyed or retargeted after this transfer.
        staged.count_=count;staged.used_=used;staged.ready_=true;
        for(std::size_t i=0;i<count;++i)
        {prepared[i]->ready_=false;prepared[i]->refusal_=Refusal::none;}
        out.take(staged);return true;
    }
    // Explicit read-only participation, never an empty-components shortcut.
    // completeSupportAuthority binds the caller's real full current/future peer
    // endpoint preimage under its mesh/shadow exclusion. No dummy origin token,
    // observer snapshot or old-before-as-future permission is manufactured here.
    bool prepareReadOnlyPublication
    (
        const std::function<bool()>& completeSupportAuthority,
        const std::function<bool()>& available,IndependentPublicationLease& out
    )
    {
        if(out.owner_||publicationReserved_||operationDepth_!=0||!active_
         ||!completeSupportAuthority||!available
         ||publicationNonce_==std::numeric_limits<std::uint64_t>::max())return false;
        IndependentPublicationLease staged;
        staged.owner_=this;staged.nonce_=++publicationNonce_;
        staged.revision_=revision_;staged.finalSize_=keys_.size();
        staged.readOnly_=true;publicationReserved_=true;
        const auto stillCurrent=[&]()noexcept {
            return publicationReserved_&&publicationNonce_==staged.nonce_&&operationDepth_==0
                &&active_&&revision_==staged.revision_&&keys_.size()==staged.finalSize_;
        };
        if(!budgetSafe(available)||!stillCurrent())return false;
        try{if(!completeSupportAuthority())return false;}catch(...){return false;}
        if(!stillCurrent())return false;
        if(!budgetSafe(available)||!stillCurrent())return false;
        staged.ready_=true;out.take(staged);return true;
    }
    void publishIndependentSameOrigin(IndependentPublicationLease&& lease)noexcept
    {
        // Invoke ONLY after the caller's real all-rank yes while its mesh/shadow
        // exclusion is still held. These are misuse invariants, not new quality,
        // clock/source votes or a normal post-vote refusal path.
        if(lease.owner_!=this||!lease.ready_||!publicationReserved_
         ||publicationNonce_!=lease.nonce_||operationDepth_!=0
         ||revision_!=lease.revision_||keys_.size()!=lease.finalSize_)
            std::terminate();
        for(std::size_t i=0;i<lease.count_;++i)
        {
            const auto& component=lease.components_[i];
            for(std::size_t j=0;j<component.memberCount;++j)
                keys_[lease.members_[component.offset+j]]=Label(-1);
            keys_[component.keeper]=component.key;
        }
        if(!lease.readOnly_)++revision_;
        publicationReserved_=false;lease.forget();
    }
    // The normal writer's real native->compact binding is required; no
    // guessed identity mapping and no implicit duplicate survivor permission.
    bool compactKeys(const std::vector<Label>& nativeToCompact,std::size_t count,
                     std::vector<Label>& out) const
    {
        out.clear();
        if (!active_ || nativeToCompact.size()!=keys_.size()
         || count>maxKeyBytes/sizeof(Label)) return false;
        out.assign(count,Label(-1));std::vector<unsigned char> seen(count,0);
        for (std::size_t c=0;c<keys_.size();++c)
        {
            const Label compact=nativeToCompact[c];
            if (compact==Label(-1)) continue;
            if (compact<0 || std::size_t(compact)>=count || seen[compact])
            { out.clear();return false; }
            seen[compact]=1;out[compact]=keys_[c];
        }
        if (std::find(seen.begin(),seen.end(),0)!=seen.end()) { out.clear();return false; }
        return true;
    }
};
}
#endif
