#ifndef dcCartSingleSourcePrefixCoalescence_H
#define dcCartSingleSourcePrefixCoalescence_H
#include "dcCartPrefixSourceRingRestoration.H"

namespace Foam { namespace dcCartSingleSourcePrefixCoalescence
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace R=dcCartPrefixSourceRingRestoration;
namespace E=dcCartExactBinary64Geometry;
// Labels of one already measured live whole SourceFacet/SourceFacet veto.
// This metadata is neither a quality result nor source/publication authority.
struct Witness
{
    label parent=-1,source=-1,first=-1,second=-1;
};
struct Result
{
    S::Plan plan;
    std::set<label> rawParents;
    std::map<label,label> currentParents;
    std::map<label,std::vector<label>> sourceGroups;
    std::vector<label> oldFaces,outline,expectedReleasedPrefixPoints;
    label source=-1;
};
template<class Raw,class Current>
bool prepare(const Raw& raw,const Current& current,
 const std::vector<label>& unionBase,const R::Index& liveIndex,
 const std::vector<label>& seamSourceParent,label rank,const Witness& witness,
 const std::function<bool()>& available,Result& out,std::string* why=nullptr)
{
    out=Result();auto fail=[&](const char* s){if(why)*why=s;return false;};
    try
    {
        if(!R::work(available))return fail("coalescence-clock");
        if(rank<0||raw.nCells<0||current.nCells<0
           ||raw.cellAlive.size()!=std::size_t(raw.nCells)
           ||raw.cellRank.size()!=std::size_t(raw.nCells)
           ||current.cellAlive.size()!=std::size_t(current.nCells)
           ||current.cellRank.size()!=std::size_t(current.nCells)
           ||seamSourceParent.size()!=std::size_t(current.nCells)
           ||raw.owner.size()!=raw.faces.size()||raw.neighbour.size()!=raw.faces.size()
           ||raw.patch.size()!=raw.faces.size()||raw.zone.size()!=raw.faces.size()
           ||raw.faceAlive.size()!=raw.faces.size()
           ||current.owner.size()!=current.faces.size()||current.neighbour.size()!=current.faces.size()
           ||current.patch.size()!=current.faces.size()||current.zone.size()!=current.faces.size()
           ||current.faceAlive.size()!=current.faces.size()
           ||raw.faces.size()>current.faces.size()||raw.points.size()>current.points.size()
           ||unionBase.size()!=current.faces.size()
           ||liveIndex.faceCount!=current.faces.size()||liveIndex.pointCount!=current.points.size()
           ||liveIndex.cellCount!=current.nCells||liveIndex.bySource.size()!=raw.faces.size()
           ||liveIndex.pointReferences.size()!=current.points.size())return fail("coalescence-shape");
        const label source=witness.source;
        if(source<0||std::size_t(source)>=raw.faces.size()||!raw.faceAlive[source]
           ||witness.first<0||witness.second<0||witness.first==witness.second)
            return fail("coalescence-witness");
        const label owner=raw.owner[source],neighbour=raw.neighbour[source];
        if(owner<0||owner>=raw.nCells||owner>=current.nCells
           ||neighbour<-1||neighbour>=raw.nCells||neighbour>=current.nCells
           ||owner==neighbour||(witness.parent!=owner&&witness.parent!=neighbour))
            return fail("coalescence-endpoint");
        for(label p:{owner,neighbour})if(p>=0)
        {
            if(!R::work(available))return fail("coalescence-clock");
            // The mature adapter currently supports only real unsplit identities.
            if(!raw.cellAlive[p]||!current.cellAlive[p]
               ||raw.cellRank[p]!=rank||current.cellRank[p]!=rank
               ||seamSourceParent[p]!=p)return fail("coalescence-parent-state");
        }
        const auto& cohort=liveIndex.bySource[source];
        if(cohort.size()<2||cohort.size()>8
           ||std::find(cohort.begin(),cohort.end(),witness.first)==cohort.end()
           ||std::find(cohort.begin(),cohort.end(),witness.second)==cohort.end())
            return fail("coalescence-prefix-cohort");
        std::map<label,std::size_t> uses;label previous=-1;
        for(label f:cohort)
        {
            if(!R::work(available))return fail("coalescence-clock");
            if(f<=previous||f<0||std::size_t(f)>=current.faces.size()
               ||!current.faceAlive[f]||unionBase[f]!=source
               ||current.faces[f].size()<3||current.faces[f].size()>64)
                return fail("coalescence-source-index");
            previous=f;std::set<label> unique;
            for(label p:current.faces[f])
            {
                if(!R::work(available))return fail("coalescence-clock");
                if(p<0||std::size_t(p)>=current.points.size()||!unique.insert(p).second
                   ||!S::finite(current.points[p]))return fail("coalescence-point");
                ++uses[p];
            }
        }
        if(raw.faces[source].size()<3||raw.faces[source].size()>64)
            return fail("coalescence-original-ring");
        for(label p:raw.faces[source])
            if(!R::work(available)||p<0||std::size_t(p)>=raw.points.size()
               ||!S::finite(raw.points[p]))return fail("coalescence-original-point");
        R::Ring outer;
        if(!R::Detail::coverageFaceRestage(raw,current,source,cohort,owner,neighbour,
                                         available,outer,why))return false;
        if(outer.size()<3||outer.size()>64)return fail("coalescence-outline-bound");
        const std::set<label> outlineNodes(outer.begin(),outer.end());
        if(outlineNodes.size()!=outer.size())return fail("coalescence-outline-node");
        // Original and current outline nodes are mandatory. A post-raw interior
        // node may become unused only when this complete cohort owns every live
        // incidence; Restore and its prepared index independently prove release.
        std::vector<label> expectedReleased;
        for(const auto& use:uses)
        {
            if(!R::work(available))return fail("coalescence-clock");
            if(liveIndex.pointReferences[use.first]<use.second)
                return fail("coalescence-protected-node");
            if(!outlineNodes.count(use.first))
            {
                if(std::size_t(use.first)<raw.points.size()
                   ||liveIndex.pointReferences[use.first]!=use.second)
                    return fail("coalescence-protected-node");
                expectedReleased.push_back(use.first);
            }
        }
        E::Context exact([&](){return R::work(available);});
        const auto original=R::Detail::integrals(raw,raw.faces[source],exact);
        const auto merged=R::Detail::integrals(current,outer,exact);
        if(!exact.good()||!R::Detail::same(original,merged,exact))
            return fail("coalescence-source-integrals");
        const auto normal=merged.area;
        if(!R::Detail::simpleFaceRestageOriginal(current,outer,normal,exact))
            return fail("coalescence-outline-simple");
        for(std::size_t j=0;j<outer.size();++j)
        {
            if(!exact.take())return fail("coalescence-clock");
            const auto a=R::Detail::exact(current,outer[j],exact);
            const auto edge=E::difference(R::Detail::exact(current,outer[(j+1)%outer.size()],exact),a,exact);
            for(label p:outer)
                if(E::sign(E::dot(E::cross(edge,E::difference(R::Detail::exact(current,p,exact),a,exact),exact),normal,exact))<0)
                    return fail("coalescence-outline-nonconvex");
        }
        if(!exact.good()||!R::work(available))return fail("coalescence-clock");
        Result result;result.source=source;result.oldFaces=cohort;result.outline=std::move(outer);
        result.expectedReleasedPrefixPoints=std::move(expectedReleased);
        for(label p:{owner,neighbour})if(p>=0)
        {result.rawParents.insert(p);result.currentParents.emplace(p,p);}
        result.sourceGroups.emplace(source,std::vector<label>{source});
        S::Row row;row.native=row.source=source;row.owner=owner;row.neighbour=neighbour;
        row.patch=raw.patch[source];row.zone=raw.zone[source];
        // This is the raw point domain. Restore inserts the certified current
        // outer-edge nodes, rechecks coverage and preserves all current nodes.
        row.ring=raw.faces[source];result.plan.rows.push_back(std::move(row));
        if(!R::work(available))return fail("coalescence-clock");
        result.plan.ready=true;out=std::move(result);if(why)why->clear();return true;
    }
    catch(...){return fail("coalescence-exception");}
}

// A dependency cohort is complete source metadata, never another measured FC
// witness. The only measured seed is seedWitness; Stock/permission stay outside.
struct CompleteResult
{
    S::Plan plan;
    std::set<label> rawParents;
    std::map<label,label> currentParents;
    std::map<label,std::vector<label>> sourceGroups,outlinesBySource;
    // Complete adjacent source metadata is read-only, not a restaging row.
    std::set<label> readOnlyShellSources;
    std::vector<label> oldFaces,expectedReleasedPrefixPoints,orderedSources;
    Witness seedWitness;
    // Observation only; source/quality acceptance never consults this count.
    std::size_t observedCurrentInventoryFaces=0;
};
template<class Raw,class Current,class CurrentFaces>
#if defined(__GNUC__)
__attribute__((noinline))
#endif
bool prepareComplete(const Raw& raw,const Current& current,
 const std::vector<label>& unionBase,const R::Index& liveIndex,
 const std::vector<label>& seamSourceParent,
 const std::vector<std::vector<label>>& rawCf,CurrentFaces currentFaces,
 label rank,const Witness& witness,const std::function<bool()>& available,
 CompleteResult& out,std::string* why=nullptr,bool selectedSourceOnly=false)
{
    out=CompleteResult();std::set<label> fullCurrentInventory;
    auto fail=[&](const char* s)
    {out.observedCurrentInventoryFaces=fullCurrentInventory.size();if(why)*why=s;return false;};
    try
    {
        if(!R::work(available))return fail("coalescence-clock");
        if(rank<0||raw.nCells<0||current.nCells<0
           ||rawCf.size()!=std::size_t(raw.nCells)
           ||raw.cellAlive.size()!=std::size_t(raw.nCells)||raw.cellRank.size()!=std::size_t(raw.nCells)
           ||current.cellAlive.size()!=std::size_t(current.nCells)||current.cellRank.size()!=std::size_t(current.nCells)
           ||seamSourceParent.size()!=std::size_t(current.nCells)
           ||raw.owner.size()!=raw.faces.size()||raw.neighbour.size()!=raw.faces.size()
           ||raw.patch.size()!=raw.faces.size()||raw.zone.size()!=raw.faces.size()||raw.faceAlive.size()!=raw.faces.size()
           ||current.owner.size()!=current.faces.size()||current.neighbour.size()!=current.faces.size()
           ||current.patch.size()!=current.faces.size()||current.zone.size()!=current.faces.size()
           ||current.faceAlive.size()!=current.faces.size()||raw.faces.size()>current.faces.size()
           ||raw.points.size()>current.points.size()||unionBase.size()!=current.faces.size()
           ||liveIndex.faceCount!=current.faces.size()||liveIndex.pointCount!=current.points.size()
           ||liveIndex.cellCount!=current.nCells||liveIndex.bySource.size()!=raw.faces.size()
           ||liveIndex.pointReferences.size()!=current.points.size())return fail("coalescence-complete-shape");
        const label source=witness.source;
        if(witness.parent<0||source<0||std::size_t(source)>=raw.faces.size()||!raw.faceAlive[source]
           ||witness.first<0||witness.second<0||witness.first==witness.second)
            return fail("coalescence-witness");
        const label own=raw.owner[source],nei=raw.neighbour[source];
        if(witness.parent!=own&&witness.parent!=nei)return fail("coalescence-endpoint");
        const auto& seed=liveIndex.bySource[source];
        if(seed.size()<2
           ||std::find(seed.begin(),seed.end(),witness.first)==seed.end()
           ||std::find(seed.begin(),seed.end(),witness.second)==seed.end())
            return fail("coalescence-prefix-cohort");
        std::set<label> reached,pending,sources,readOnlyShellSources;
        // Physical face-only coalescence changes one complete source cohort.
        // Adjacent cohorts belong to full-shell proof, not recursive recomposition.
        if(selectedSourceOnly)sources.insert(source);
        auto addParent=[&](label parent)->bool
        {
            if(parent<0)return parent==-1;
            if(!R::work(available)||parent>=raw.nCells||parent>=current.nCells
               ||!raw.cellAlive[parent]||!current.cellAlive[parent]
               ||raw.cellRank[parent]!=rank||current.cellRank[parent]!=rank
               ||seamSourceParent[parent]!=parent)return false;
            if(!reached.count(parent))
            {
                reached.insert(parent);pending.insert(parent);
            }
            return true;
        };
        if(!addParent(own)||!addParent(nei))return fail("coalescence-complete-endpoint-state");
        while(!pending.empty())
        {
            if(!R::work(available))return fail("coalescence-clock");
            const label parent=*pending.begin();pending.erase(pending.begin());
            const auto& originals=rawCf[parent];
            if(originals.empty()||(!selectedSourceOnly&&originals.size()>64))
                return fail("coalescence-complete-inventory-bound");
            std::set<label> rawIds,expected;
            for(label f:originals)
            {
                if(!R::work(available))return fail("coalescence-clock");
                if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
                   ||!rawIds.insert(f).second||(raw.owner[f]!=parent&&raw.neighbour[f]!=parent)
                   ||raw.owner[f]<0||raw.owner[f]>=raw.nCells
                   ||raw.neighbour[f]<-1||raw.neighbour[f]>=raw.nCells
                   ||raw.owner[f]==raw.neighbour[f])return fail("coalescence-complete-raw-inventory");
                const auto& cohort=liveIndex.bySource[f];
                if(cohort.empty())return fail("coalescence-complete-cohort-bound");
                label previous=-1;
                for(label face:cohort)
                {
                    if(!R::work(available))return fail("coalescence-clock");
                    if(face<=previous||face<0||std::size_t(face)>=current.faces.size()
                       ||!current.faceAlive[face]||unionBase[face]!=f
                       ||current.owner[face]!=raw.owner[f]||current.neighbour[face]!=raw.neighbour[f]
                       ||current.patch[face]!=raw.patch[f]||current.zone[face]!=raw.zone[f]
                       ||current.faces[face].size()<3||current.faces[face].size()>64
                       ||!expected.insert(face).second)return fail("coalescence-complete-current-inventory");
                    previous=face;
                    // One actual native-face inventory over the whole closure;
                    // a shared internal face is counted once, never once per cell.
                    fullCurrentInventory.insert(face);
                    out.observedCurrentInventoryFaces=fullCurrentInventory.size();
                    // Read-only full shells use the original work clock.
                    // Actual selected/retired cohorts remain bounded below.
                    if(!selectedSourceOnly&&(fullCurrentInventory.size()>64||expected.size()>64))
                        return fail("coalescence-complete-inventory-bound");
                    std::set<label> unique;
                    for(label point:current.faces[face])
                    {
                        if(!R::work(available))return fail("coalescence-clock");
                        if(point<0||std::size_t(point)>=current.points.size()
                           ||!unique.insert(point).second||!S::finite(current.points[point]))
                            return fail("coalescence-point");
                    }
                }
                if(selectedSourceOnly&&f!=source)readOnlyShellSources.insert(f);
                if(!selectedSourceOnly&&cohort.size()>1)
                {
                    sources.insert(f);
                    for(label endpoint:{raw.owner[f],raw.neighbour[f]})if(endpoint>=0)
                    {
                        if(!addParent(endpoint))return fail("coalescence-complete-endpoint-state");
                    }
                }
            }
            std::vector<label> actual;
            if(!currentFaces(parent,actual))return fail("coalescence-complete-live-inventory");
            if(actual.empty()||(!selectedSourceOnly&&actual.size()>64))
                return fail("coalescence-complete-inventory-bound");
            std::set<label> actualSet;
            for(label f:actual)
            {
                if(!R::work(available))return fail("coalescence-clock");
                if(f<0||std::size_t(f)>=current.faces.size()||!current.faceAlive[f]
                   ||(current.owner[f]!=parent&&current.neighbour[f]!=parent)
                   ||!actualSet.insert(f).second)return fail("coalescence-complete-live-inventory");
            }
            if(actualSet!=expected)return fail("coalescence-complete-missing-cohort");
        }
        if(!sources.count(source))return fail("coalescence-complete-seed");
        CompleteResult result;result.seedWitness=witness;
        result.readOnlyShellSources=std::move(readOnlyShellSources);
        std::set<label> oldIds,mandatory;
        std::map<label,std::size_t> uses;
        for(label f:sources)
        {
            if(!R::work(available))return fail("coalescence-clock");
            const auto& cohort=liveIndex.bySource[f];
            if(raw.faces[f].size()<3||raw.faces[f].size()>64)return fail("coalescence-original-ring");
            for(label p:raw.faces[f])
                if(!R::work(available)||p<0||std::size_t(p)>=raw.points.size()
                   ||!S::finite(raw.points[p]))return fail("coalescence-original-point");
            R::Ring outer;
            if(!R::Detail::coverageFaceRestage(raw,current,f,cohort,raw.owner[f],raw.neighbour[f],
                                             available,outer,why))return false;
            if(outer.size()<3||outer.size()>64)return fail("coalescence-outline-bound");
            const std::set<label> outerIds(outer.begin(),outer.end());
            if(outerIds.size()!=outer.size())return fail("coalescence-outline-node");
            mandatory.insert(outerIds.begin(),outerIds.end());
            E::Context exact([&](){return R::work(available);});
            const auto original=R::Detail::integrals(raw,raw.faces[f],exact);
            const auto merged=R::Detail::integrals(current,outer,exact);
            if(!exact.good()||!R::Detail::same(original,merged,exact))return fail("coalescence-source-integrals");
            const auto normal=merged.area;
            if(!R::Detail::simpleFaceRestageOriginal(current,outer,normal,exact))return fail("coalescence-outline-simple");
            for(std::size_t j=0;j<outer.size();++j)
            {
                if(!exact.take())return fail("coalescence-clock");
                const auto a=R::Detail::exact(current,outer[j],exact);
                const auto edge=E::difference(R::Detail::exact(current,outer[(j+1)%outer.size()],exact),a,exact);
                for(label p:outer)
                    if(E::sign(E::dot(E::cross(edge,E::difference(R::Detail::exact(current,p,exact),a,exact),exact),normal,exact))<0)
                        return fail("coalescence-outline-nonconvex");
            }
            if(!exact.good())return fail("coalescence-clock");
            for(label face:cohort)
            {
                if(!oldIds.insert(face).second)return fail("coalescence-complete-duplicate-piece");
                if(oldIds.size()>64)return fail("coalescence-complete-retired-bound");
                for(label p:current.faces[face])++uses[p];
            }
            S::Row row;row.native=row.source=f;row.owner=raw.owner[f];row.neighbour=raw.neighbour[f];
            row.patch=raw.patch[f];row.zone=raw.zone[f];row.ring=raw.faces[f];
            result.plan.rows.push_back(std::move(row));result.sourceGroups.emplace(f,std::vector<label>{f});
            result.outlinesBySource.emplace(f,std::move(outer));result.orderedSources.push_back(f);
        }
        for(const auto& use:uses)
        {
            if(!R::work(available))return fail("coalescence-clock");
            if(liveIndex.pointReferences[use.first]<use.second)return fail("coalescence-protected-node");
            if(!mandatory.count(use.first))
            {
                if(std::size_t(use.first)<raw.points.size()||liveIndex.pointReferences[use.first]!=use.second)
                    return fail("coalescence-protected-node");
                result.expectedReleasedPrefixPoints.push_back(use.first);
            }
        }
        for(label p:reached){result.rawParents.insert(p);result.currentParents.emplace(p,p);}
        result.oldFaces.assign(oldIds.begin(),oldIds.end());
        result.observedCurrentInventoryFaces=fullCurrentInventory.size();
        if(!R::work(available))return fail("coalescence-clock");
        result.plan.ready=true;out=std::move(result);if(why)why->clear();return true;
    }
    catch(...){return fail("coalescence-exception");}
}
}}
#endif
