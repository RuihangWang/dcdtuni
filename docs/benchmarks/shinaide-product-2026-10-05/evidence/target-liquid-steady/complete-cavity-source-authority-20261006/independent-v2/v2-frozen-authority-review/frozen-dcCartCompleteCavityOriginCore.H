#ifndef dcCartCompleteCavityOriginCore_H
#define dcCartCompleteCavityOriginCore_H
#include "dcCartFluidOriginSnapshotCore.H"
#include <limits>
#include <functional>
#include <new>
namespace dcCartCompleteCavityOriginCore
{
constexpr std::size_t maxOrigins=8,maxCavityCells=128,maxCompositeSets=4096;
constexpr std::size_t maxKeyBytes=64u*1024u*1024u,maxCompositeBytes=1024u*1024u;
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
    if (!available || !available() || actualRank<0 || !validFingerprint(fresh)
     || origins.empty() || origins.size()>maxOrigins
     || snapshot.boundaryMembershipDigest!=fresh.boundaryMembership) return false;
    std::set<int> unique;
    for (const Origin& o:origins)
    {
        if (!available() || std::get<0>(o.identity)!=actualRank
         || !unique.insert(std::get<1>(o.identity)).second
         || !dcCartFluidOriginSnapshotCore::verify
            (snapshot,o.identity,o.facets,fresh.raw,fresh.roles,fresh.materialKey,
             fresh.material,fresh.producer)) return false;
    }
    return available();
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
    bool started_=false,active_=false;
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
    bool active() const { return active_; }
    bool started() const { return started_; }
    std::size_t size() const { return keys_.size(); }
    std::size_t compositeCount() const { return composite_.size(); }
    std::size_t keyCapacityBytes() const { return keys_.capacity()*sizeof(Label); }
    std::size_t compositeCapacityBytes() const { return composite_.capacity()*sizeof(Composite); }
    const std::array<std::size_t,std::size_t(Refusal::count)>& refusals() const { return refusals_; }
    bool initialize
    (
        std::size_t cells,const std::function<bool(Label)>& recordedParent,
        const std::function<bool()>& available
    )
    {
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
        if (!active_) return;
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
        if (!active_) return false;
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
        if (!active_ || cells.empty() || cells.size()>maxCavityCells) return false;
        std::set<Label> uniqueCells;
        for (Label cell:cells)
            if (cell<0 || std::size_t(cell)>=keys_.size()
             || !uniqueCells.insert(cell).second || !decode(keys_[cell],out))
            { out.clear();return false; }
        std::sort(out.begin(),out.end());out.erase(std::unique(out.begin(),out.end()),out.end());
        if (out.empty() || out.size()>maxOrigins) { out.clear();return false; }
        return true;
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
