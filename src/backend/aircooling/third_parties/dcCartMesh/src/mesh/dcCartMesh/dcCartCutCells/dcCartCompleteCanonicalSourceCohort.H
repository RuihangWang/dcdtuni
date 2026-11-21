#ifndef dcCartCompleteCanonicalSourceCohort_H
#define dcCartCompleteCanonicalSourceCohort_H
#include "dcCartCanonicalSourceBasePartition.H"

namespace Foam { namespace dcCartCompleteCanonicalSourceCohort
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace R=dcCartPrefixSourceRingRestoration;
namespace E=dcCartExactBinary64Geometry;
using Ring=std::vector<label>;
struct Result
{
    S::Plan plan;
    std::set<label> rawParents;
    std::map<label,std::vector<label>> orderedSourceGroups;
    std::size_t processedSourceCount=0;
};
// FACE-only proposal. All cell identities/points/sources remain real and unchanged.
// The caller must freshly validate current-parent mappings, source permissions,
// processor mirroring, full future Stock/VM and publication. No proof is cached.
template<class Raw>
bool prepare(const Raw& raw,const std::vector<std::vector<label>>& completeRawCf,
 const std::map<label,std::vector<Ring>>& positiveSourcePieces,
 const std::function<bool()>& available,Result& out,std::string* why=nullptr,
 std::size_t maxParents=512,std::size_t maxSources=4096)
{
    out=Result();
    auto fail=[&](const char* reason){if(why)*why=reason;return false;};
    auto work=[&](){return R::work(available);};
    const std::size_t nf=raw.faces.size(),bound=std::numeric_limits<label>::max();
    if(!work()||positiveSourcePieces.empty()||!maxParents||!maxSources
        ||raw.nCells<0||completeRawCf.size()!=std::size_t(raw.nCells)
        ||raw.cellAlive.size()<std::size_t(raw.nCells)||nf>bound
        ||raw.owner.size()!=nf||raw.neighbour.size()!=nf||raw.patch.size()!=nf
        ||raw.zone.size()!=nf||raw.faceAlive.size()!=nf)
        return fail("cohort-input-or-clock");
    std::set<label> affected,pending,processed,seenSources;
    std::map<label,std::vector<S::Row>> sourceRows;
    auto endpoint=[&](label c)->bool
    {
        return c>=0&&c<raw.nCells&&raw.cellAlive[c]&&!completeRawCf[c].empty();
    };
    for(const auto& input:positiveSourcePieces)
    {
        const label f=input.first;
        if(!work()||f<0||std::size_t(f)>=nf||!raw.faceAlive[f]
            ||input.second.empty()||!endpoint(raw.owner[f])
            ||(raw.neighbour[f]>=0&&!endpoint(raw.neighbour[f])))
            return fail("cohort-supplied-source");
        affected.insert(raw.owner[f]);
        if(raw.neighbour[f]>=0)affected.insert(raw.neighbour[f]);
    }
    pending=affected;
    while(!pending.empty())
    {
        if(!work())return fail("cohort-clock");
        if(affected.size()>maxParents)return fail("cohort-parent-bound");
        const label c=*pending.begin();pending.erase(pending.begin());processed.insert(c);
        if(!endpoint(c))return fail("cohort-parent-inventory");
        std::set<label> parentFaces;
        for(label f:completeRawCf[c])
        {
            if(!work())return fail("cohort-clock");
            if(f<0||std::size_t(f)>=nf||!raw.faceAlive[f]
                ||(raw.owner[f]!=c&&raw.neighbour[f]!=c)
                ||!parentFaces.insert(f).second)
                return fail("cohort-source-incidence");
            if(seenSources.count(f))continue;
            if(seenSources.size()>=maxSources)return fail("cohort-source-bound");
            seenSources.insert(f);
            const S::Row original{f,f,raw.owner[f],raw.neighbour[f],raw.patch[f],raw.zone[f],raw.faces[f]};
            S::Plan input;input.ready=true;
            const auto replacement=positiveSourcePieces.find(f);
            if(replacement==positiveSourcePieces.end())input.rows.push_back(original);
            else
            {
                if(replacement->second.size()-1>bound-nf)return fail("cohort-face-bound");
                vector originalSf;point originalFc;
                S::metric(raw,input,original.ring,originalSf,originalFc);
                if(!S::finite(originalSf)||!S::finite(originalFc)||magSqr(originalSf)<=VSMALL)
                    return fail("cohort-supplied-area");
                for(std::size_t i=0;i<replacement->second.size();++i)
                {
                    S::Row row=original;row.ring=replacement->second[i];
                    row.native=i?label(nf+i-1):f;
                    if(row.ring.size()<3||row.ring.size()>64)return fail("cohort-supplied-ring");
                    for(label pointId:row.ring)
                        if(pointId<0||std::size_t(pointId)>=raw.points.size()
                            ||!S::finite(raw.points[pointId]))return fail("cohort-supplied-point");
                    vector sf;point fc;S::metric(raw,input,row.ring,sf,fc);
                    if(!work()||!S::finite(sf)||!S::finite(fc)||!std::isfinite(sf&originalSf)
                        ||(sf&originalSf)<=0)return fail("cohort-supplied-orientation");
                    input.rows.push_back(std::move(row));
                }
            }
            S::Plan proposal;std::size_t changed=0;std::string reason;
            if(!dcCartCanonicalSourceBasePartition::partition(raw,input,work,proposal,changed,&reason))
            {if(why)*why=reason;return false;}
            if(!proposal.ready||!proposal.cells.empty()||!proposal.births.empty()||proposal.rows.empty())
                return fail("cohort-canonical-mode");
            if(proposal.rows.size()==1&&proposal.rows.front().ring==original.ring)continue;
            std::set<label> oldNodes(original.ring.begin(),original.ring.end()),newNodes;
            E::Context exact(work);R::Detail::Integrals total;
            const auto before=R::Detail::integrals(raw,original.ring,exact);
            std::map<std::pair<label,label>,unsigned> edges,outline;
            for(const auto& row:proposal.rows)
            {
                if(!work()||row.source!=f||row.owner!=original.owner||row.neighbour!=original.neighbour
                    ||row.patch!=original.patch||row.zone!=original.zone)
                    return fail("cohort-source-identity");
                newNodes.insert(row.ring.begin(),row.ring.end());
                R::Detail::add(total,R::Detail::integrals(raw,row.ring,exact),exact);
                for(std::size_t i=0;i<row.ring.size();++i)
                    ++edges[{row.ring[i],row.ring[(i+1)%row.ring.size()]}];
            }
            for(auto& edge:edges)
            {
                const auto other=edges.find({edge.first.second,edge.first.first});
                if(other==edges.end())continue;
                const unsigned count=std::min(edge.second,other->second);
                edge.second-=count;other->second-=count;
            }
            for(std::size_t i=0;i<original.ring.size();++i)
                ++outline[{original.ring[i],original.ring[(i+1)%original.ring.size()]}];
            for(const auto& edge:edges)if(edge.second!=outline[edge.first])
                return fail("cohort-source-outline");
            for(const auto& edge:outline)if(edges[edge.first]!=edge.second)
                return fail("cohort-source-outline");
            if(oldNodes!=newNodes||!exact.good()||!R::Detail::same(before,total,exact))
                return fail("cohort-source-nodes-or-exact-VM");
            sourceRows.emplace(f,std::move(proposal.rows));
            for(label neighbour:{original.owner,original.neighbour})if(neighbour>=0)
            {
                if(!endpoint(neighbour))return fail("cohort-live-endpoint");
                affected.insert(neighbour);
                if(affected.size()>maxParents)return fail("cohort-parent-bound");
                if(!processed.count(neighbour))pending.insert(neighbour);
            }
        }
    }
    if(sourceRows.empty())return fail("cohort-no-change");
    Result staged;label next=label(nf);
    for(auto& entry:sourceRows)for(std::size_t i=0;i<entry.second.size();++i)
    {
        if(!work())return fail("cohort-clock");
        S::Row row=std::move(entry.second[i]);
        if(i)
        {
            if(next==std::numeric_limits<label>::max())return fail("cohort-face-bound");
            row.native=next++;
        }
        else row.native=entry.first;
        staged.orderedSourceGroups[entry.first].push_back(row.native);
        staged.plan.rows.push_back(std::move(row));
    }
    if(!work())return fail("cohort-clock");
    staged.rawParents=std::move(affected);staged.processedSourceCount=seenSources.size();
    staged.plan.ready=true;out=std::move(staged);return true;
}
} }
#endif
