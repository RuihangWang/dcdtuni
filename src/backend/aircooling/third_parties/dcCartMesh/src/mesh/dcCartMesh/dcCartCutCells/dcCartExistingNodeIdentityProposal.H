#ifndef dcCartExistingNodeIdentityProposal_H
#define dcCartExistingNodeIdentityProposal_H
#include "dcCartPrefixSourceRingRestoration.H"

namespace Foam { namespace dcCartExistingNodeIdentityProposal
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace R=dcCartPrefixSourceRingRestoration;
// Exact current carrier bookkeeping only. This is not source authority,
// a quality result, a whole-cell permission, or a publication certificate.
struct Result
{
    S::Plan plan;
    std::set<label> rawParents;
    std::map<label,label> currentParents;
    std::map<label,std::vector<label>> sourceGroups;
    label debtParent=-1,source=-1,currentCarrier=-1;
};
template<class Raw,class Current>
bool prepare(const Raw& raw,const Current& current,
 const std::vector<label>& unionBase,const R::Index& liveIndex,
 const std::vector<label>& seamSourceParent,label rank,label debtParent,
 label source,const std::function<bool()>& available,Result& out,
 std::string* why=nullptr)
{
    out=Result();auto fail=[&](const char* s){if(why)*why=s;return false;};
    try
    {
        if(!R::work(available))return fail("identity-clock");
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
           ||liveIndex.pointReferences.size()!=current.points.size())return fail("identity-shape");
        if(source<0||std::size_t(source)>=raw.faces.size()
           ||debtParent<0||debtParent>=raw.nCells||debtParent>=current.nCells
           ||!raw.faceAlive[source]||!current.faceAlive[source]
           ||raw.owner[source]!=debtParent)return fail("identity-carrier");
        const label owner=raw.owner[source],neighbour=raw.neighbour[source];
        if(neighbour<-1||neighbour>=raw.nCells||neighbour>=current.nCells
           ||neighbour==owner)return fail("identity-endpoint");
        for(label p:{owner,neighbour})if(p>=0)
        {
            if(!R::work(available))return fail("identity-clock");
            // The first entry supports actual raw==current endpoint identities;
            // no appended child is substituted for an original parent.
            if(!raw.cellAlive[p]||!current.cellAlive[p]
               ||raw.cellRank[p]!=rank||current.cellRank[p]!=rank
               ||seamSourceParent[p]!=p)return fail("identity-parent-state");
        }
        const auto& cohort=liveIndex.bySource[source];
        if(cohort.size()!=1||cohort.front()!=source||unionBase[source]!=source)
            return fail("identity-prefix-cohort");
        if(current.owner[source]!=owner||current.neighbour[source]!=neighbour
           ||current.patch[source]!=raw.patch[source]||current.zone[source]!=raw.zone[source])
            return fail("identity-current-role");
        const auto& ring=raw.faces[source];
        if(ring.size()<3||ring.size()>64
           ||!R::Detail::sameDirectedFaceRestageCycle(ring,current.faces[source]))
            return fail("identity-ring");
        std::set<label> nodes;
        for(label p:ring)
        {
            if(!R::work(available))return fail("identity-clock");
            if(p<0||std::size_t(p)>=raw.points.size()||std::size_t(p)>=current.points.size()
               ||!nodes.insert(p).second||!S::finite(raw.points[p])||!S::finite(current.points[p])
               ||!R::samePointBits(raw.points[p],current.points[p])
               ||liveIndex.pointReferences[p]==0)return fail("identity-point-bits");
        }
        // Reuse the existing exact simple-planar original-ring witness.
        // Multi-piece coverage is deliberately not admitted by this factory.
        R::Ring outer;
        if(!R::Detail::coverageFaceRestage(raw,current,source,cohort,owner,neighbour,
                                         available,outer,why))return false;
        if(outer!=current.faces[source])return fail("identity-coverage-ring");
        Result result;result.debtParent=debtParent;result.source=source;
        result.currentCarrier=source;
        for(label p:{owner,neighbour})if(p>=0)
        {result.rawParents.insert(p);result.currentParents.emplace(p,p);}
        result.sourceGroups.emplace(source,std::vector<label>{source});
        S::Row row;row.native=source;row.source=source;row.owner=owner;row.neighbour=neighbour;
        row.patch=current.patch[source];row.zone=current.zone[source];
        // Preserve current cyclic starting point and all coordinate bit patterns.
        row.ring=current.faces[source];result.plan.rows.push_back(std::move(row));
        if(!R::work(available))return fail("identity-clock");
        result.plan.ready=true;out=std::move(result);if(why)why->clear();return true;
    }
    catch(...){return fail("identity-exception");}
}
}}
#endif
