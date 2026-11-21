#ifndef dcCartSourceOriginalNativeShell_H
#define dcCartSourceOriginalNativeShell_H
#include "dcCartPrefixSourceRingRestoration.H"
#include "dcCartCompleteCavityOriginCore.H"
#include "dcCartOriginalNodePositiveEarFacets.H"
#include <memory>
#include <algorithm>
#include <cstring>

namespace Foam { namespace dcCartSourceOriginalNativeShell
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace R=dcCartPrefixSourceRingRestoration;
namespace I=dcCartOriginalNodePositiveEarFacets;
namespace O=::dcCartCompleteCavityOriginCore;
using Snapshot=::dcCartFluidOriginSnapshotCore::Snapshot;
using Bits=std::array<std::uint64_t,3>;
namespace Detail {struct Access;}
// This is an immutable preparation record, never a Source/quality/whole permit.
// Current prefix fragmentation and proposed immutable-Source rows are separate domains.
class Result
{
    friend struct Detail::Access;
    bool ready_=false;
    label target_=-1,rank_=-1,cells_=0;
    std::size_t points_=0,faces_=0;
    std::uint64_t epoch_=0,generation_=0;
    S::Plan plan_;
    std::set<label> parents_;
    std::map<label,label> currentParents_;
    std::map<label,std::vector<label>> groups_,cohorts_;
    std::map<label,R::Ring> outer_;
    std::map<label,S::Row> before_;
    std::map<label,Bits> bits_;
    std::shared_ptr<const Snapshot> snapshot_;
    O::Fingerprint fingerprint_;
public:
    Result()=default;
    bool ready()const{return ready_;}
    label target()const{return target_;}
    const S::Plan& plan()const{return plan_;}
    const std::set<label>& rawParents()const{return parents_;}
    const std::map<label,label>& currentParents()const{return currentParents_;}
    const std::map<label,std::vector<label>>& sourceGroups()const{return groups_;}
};
namespace Detail
{
inline Bits pointBits(const point& p)
{
    Bits b{};static_assert(sizeof(scalar)==sizeof(std::uint64_t),"DP original-source identity");
    for(label k=0;k<3;++k){const scalar x=p[k];std::memcpy(&b[k],&x,sizeof(x));}return b;
}
inline bool fingerprint(const O::Fingerprint& a,const O::Fingerprint& b)
{return a.raw==b.raw&&a.roles==b.roles&&a.materialKey==b.materialKey
    &&a.material==b.material&&a.producer==b.producer&&a.boundaryMembership==b.boundaryMembership;}
struct Access
{
    static constexpr std::size_t sourceCap=64,pieceCap=512,parentCap=128,faceCap=4096,pointCap=8192;
    template<class Mesh>
    static bool unchanged(const Mesh& m,const S::Row& r)
    {return r.native>=0&&std::size_t(r.native)<m.faces.size()&&m.faceAlive[r.native]
        &&m.owner[r.native]==r.owner&&m.neighbour[r.native]==r.neighbour
        &&m.patch[r.native]==r.patch&&m.zone[r.native]==r.zone&&m.faces[r.native]==r.ring;}
    template<class Raw,class Mesh>
    static bool current(const Raw& raw,const Mesh& m,const std::vector<label>& sources,
        const R::Index& index,const Result& p,const std::shared_ptr<const Snapshot>& snapshot,
        const O::Fingerprint& fresh,const std::function<bool()>& available,std::string* why)
    {
        const auto fail=[&](const char* text){if(why)*why=text;return false;};
        if(!p.ready_||!R::work(available)||!snapshot||snapshot.get()!=p.snapshot_.get()
            ||!fingerprint(fresh,p.fingerprint_)||!O::validFingerprint(fresh)
            ||m.nCells!=p.cells_||m.points.size()!=p.points_||m.faces.size()!=p.faces_
            ||m.preparedBirth.extractionEpoch!=p.epoch_||m.preparedBirth.generation!=p.generation_
            ||sources.size()!=m.faces.size()||index.faceCount!=m.faces.size()
            ||index.pointCount!=m.points.size()||index.cellCount!=m.nCells
            ||index.bySource.size()!=raw.faces.size()||index.pointReferences.size()!=m.points.size())
            return fail("native-original-current-state");
        for(const auto& entry:p.before_)
        {if(!R::work(available)||!unchanged(m,entry.second))return fail("native-original-current-preimage");}
        for(const auto& entry:p.bits_)
            if(!R::work(available)||entry.first<0||std::size_t(entry.first)>=m.points.size()
                ||pointBits(m.points[entry.first])!=entry.second)return fail("native-original-current-point-bits");
        for(const auto& group:p.cohorts_)
        {
            if(!R::work(available)||group.first<0||std::size_t(group.first)>=index.bySource.size()
                ||index.bySource[group.first]!=group.second)return fail("native-original-current-cohort");
            for(label f:group.second)
                if(f<0||std::size_t(f)>=sources.size()||sources[f]!=group.first)
                    return fail("native-original-current-source-map");
        }
        return R::work(available);
    }
    template<class Raw,class Mesh,class AllowedFacet,class AllowedParent>
    static bool prepare(const Raw& raw,const Mesh& m,const std::vector<std::vector<label>>& rawCF,
        const std::vector<std::vector<label>>& currentCF,const std::vector<std::vector<label>>& pieces,
        const std::vector<label>& sources,const R::Index& index,
        const std::vector<label>& sourceParents,label rank,label target,
        const std::shared_ptr<const Snapshot>& snapshot,const O::Fingerprint& fresh,
        const AllowedFacet& allowedFacet,const AllowedParent& allowedParent,
        const std::function<bool()>& available,Result& out,std::string* why)
    {
        out=Result();const auto fail=[&](const char* text){if(why)*why=text;return false;};
        try
        {
            if(!R::work(available)||!snapshot||!snapshot->complete||!snapshot->completedFluidOnlyPath
                ||!O::validFingerprint(fresh)||snapshot->rawSha256!=fresh.raw||snapshot->rolesDigest!=fresh.roles
                ||snapshot->materialKey!=fresh.materialKey||snapshot->materialDigest!=fresh.material
                ||snapshot->producerDigest!=fresh.producer||snapshot->boundaryMembershipDigest!=fresh.boundaryMembership)
                return fail("native-original-source-snapshot");
            if(rank<0||target<0||target>=raw.nCells||target>=m.nCells
                ||rawCF.size()!=std::size_t(raw.nCells)||raw.nCells>m.nCells
                ||currentCF.size()!=std::size_t(m.nCells)
                ||raw.cellAlive.size()!=std::size_t(raw.nCells)||raw.cellRank.size()!=std::size_t(raw.nCells)
                ||m.cellAlive.size()!=std::size_t(m.nCells)||m.cellRank.size()!=std::size_t(m.nCells)
                ||m.cellLevel.size()!=std::size_t(m.nCells)||m.cellLeaf.size()!=std::size_t(m.nCells)
                ||m.cellPiece.size()!=std::size_t(m.nCells)||sourceParents.size()!=std::size_t(m.nCells)
                ||raw.owner.size()!=raw.faces.size()||raw.neighbour.size()!=raw.faces.size()
                ||raw.patch.size()!=raw.faces.size()||raw.zone.size()!=raw.faces.size()||raw.faceAlive.size()!=raw.faces.size()
                ||m.owner.size()!=m.faces.size()||m.neighbour.size()!=m.faces.size()||m.patch.size()!=m.faces.size()
                ||m.zone.size()!=m.faces.size()||m.faceAlive.size()!=m.faces.size()||sources.size()!=m.faces.size()
                ||pieces.size()<raw.faces.size()||index.faceCount!=m.faces.size()||index.pointCount!=m.points.size()
                ||index.cellCount!=m.nCells||index.bySource.size()!=raw.faces.size()
                ||index.pointReferences.size()!=m.points.size()||raw.points.size()>m.points.size()
                ||raw.faces.size()>m.faces.size()||rawCF[target].size()<4||rawCF[target].size()>sourceCap)
                return fail("native-original-shape");
            Result p;p.target_=target;p.rank_=rank;p.cells_=m.nCells;p.points_=m.points.size();p.faces_=m.faces.size();
            p.epoch_=m.preparedBirth.extractionEpoch;p.generation_=m.preparedBirth.generation;
            p.snapshot_=snapshot;p.fingerprint_=fresh;std::set<label> selected;
            const auto sourceRow=[&](label f)->bool
            {
                if(!R::work(available)||f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
                    ||std::size_t(f)>=snapshot->faces.size())return false;
                const auto& original=snapshot->faces[f];
                if(original.handle!=f||original.owner!=raw.owner[f]||original.neighbour!=raw.neighbour[f]
                    ||original.patch!=raw.patch[f]||original.zone!=raw.zone[f]||original.ring!=raw.faces[f]
                    ||original.ring.size()<3||original.ring.size()>64
                    ||original.owner<0||original.owner>=raw.nCells||original.neighbour<-1
                    ||original.neighbour>=raw.nCells||original.owner==original.neighbour
                    ||!allowedFacet(original.patch,original.zone,original.neighbour))return false;
                std::set<label> unique;
                for(label q:original.ring)
                {
                    if(!R::work(available)||q<0||std::size_t(q)>=raw.points.size()
                        ||std::size_t(q)>=snapshot->points.size()||std::size_t(q)>=m.points.size()
                        ||!unique.insert(q).second||!S::finite(m.points[q]))return false;
                    const auto& x=snapshot->points[q];const point a(x[0],x[1],x[2]);
                    if(!R::samePointBits(a,raw.points[q])||!R::samePointBits(a,m.points[q]))return false;
                }
                return true;
            };
            for(label f:rawCF[target])
            {
                if(!sourceRow(f)||!selected.insert(f).second
                    ||(raw.owner[f]!=target&&raw.neighbour[f]!=target))return fail("native-original-source-CF");
                const auto& cohort=index.bySource[f];if(cohort.empty()||cohort.size()>pieceCap)
                    return fail("native-original-cohort-capacity");
                if(!m.faceAlive[f]||sources[f]!=f||std::find(cohort.begin(),cohort.end(),f)==cohort.end())
                    return fail("native-original-native-slot-not-current");
                p.cohorts_.emplace(f,cohort);p.groups_.emplace(f,std::vector<label>{f});
                for(label parent:{raw.owner[f],raw.neighbour[f]})if(parent>=0)
                {p.parents_.insert(parent);p.currentParents_.emplace(parent,parent);}
                S::Row row;row.native=f;row.source=f;row.owner=raw.owner[f];row.neighbour=raw.neighbour[f];
                row.patch=raw.patch[f];row.zone=raw.zone[f];row.ring=raw.faces[f];p.plan_.rows.push_back(std::move(row));
            }
            if(p.parents_.size()>parentCap)return fail("native-original-parent-capacity");
            // Every affected endpoint supplies its real full current shell, not only selected source pieces.
            for(label parent:p.parents_)
            {
                if(!R::work(available)||parent<0||parent>=raw.nCells||parent>=m.nCells
                    ||!raw.cellAlive[parent]||!m.cellAlive[parent]||raw.cellRank[parent]!=rank||m.cellRank[parent]!=rank
                    ||sourceParents[parent]!=parent||!allowedParent(parent)
                    ||std::size_t(parent)>=snapshot->parents.size()||std::size_t(parent)>=snapshot->valid.size()
                    ||!snapshot->valid[parent]||rawCF[parent].size()<4||rawCF[parent].size()>sourceCap
                    ||currentCF[parent]!=rawCF[parent]||snapshot->parents[parent].facets!=rawCF[parent])
                    return fail("native-original-parent-identity");
                const auto id=::dcCartFluidOriginSnapshotCore::Identity(rank,parent,m.cellLevel[parent],m.cellLeaf[parent],m.cellPiece[parent]);
                if(snapshot->parents[parent].identity!=id)return fail("native-original-immutable-identity");
                for(label f:rawCF[parent])
                {
                    if(!sourceRow(f))return fail("native-original-local-full-source-shell");
                    const auto& cohort=index.bySource[f];if(cohort.empty()||cohort.size()>pieceCap)
                        return fail("native-original-local-full-current-shell");
                    std::set<label> planned,actual(cohort.begin(),cohort.end());
                    for(label q:pieces[f])if(q>=0&&std::size_t(q)<m.faces.size()&&m.faceAlive[q])planned.insert(q);
                    if(planned!=actual||actual.size()!=cohort.size())return fail("native-original-current-CF-completeness");
                    for(label q:cohort)
                    {
                        if(!R::work(available)||q<0||std::size_t(q)>=m.faces.size()||!m.faceAlive[q]
                            ||sources[q]!=f||m.owner[q]!=raw.owner[f]||m.neighbour[q]!=raw.neighbour[f]
                            ||m.patch[q]!=raw.patch[f]||m.zone[q]!=raw.zone[f]
                            ||m.faces[q].size()<3||m.faces[q].size()>64)return fail("native-original-current-role");
                        for(label endpoint:{m.owner[q],m.neighbour[q]})if(endpoint>=0)
                            if(endpoint>=m.nCells||!m.cellAlive[endpoint]||m.cellRank[endpoint]!=rank)
                                return fail("native-original-nonlocal-current-endpoint");
                        S::Row old;old.native=q;old.source=f;old.owner=m.owner[q];old.neighbour=m.neighbour[q];
                        old.patch=m.patch[q];old.zone=m.zone[q];old.ring=m.faces[q];p.before_.emplace(q,std::move(old));
                        if(p.before_.size()>faceCap)return fail("native-original-current-inventory-capacity");
                        for(label pointId:m.faces[q])
                        {
                            if(!R::work(available)||pointId<0||std::size_t(pointId)>=m.points.size()
                                ||!S::finite(m.points[pointId]))return fail("native-original-current-point");
                            p.bits_.emplace(pointId,pointBits(m.points[pointId]));
                            if(p.bits_.size()>pointCap)return fail("native-original-current-point-capacity");
                        }
                    }
                }
            }
            bool representationChanged=false;
            for(const auto& group:p.cohorts_)
                representationChanged=representationChanged||group.second!=std::vector<label>{group.first}
                    ||m.faces[group.first]!=raw.faces[group.first];
            if(!representationChanged)return fail("native-original-no-restoration");
            std::size_t selectedPieces=0;
            for(const auto& group:p.cohorts_)
            {
                if(group.second.size()>pieceCap-selectedPieces)return fail("native-original-cohort-total-capacity");
                selectedPieces+=group.second.size();const label f=group.first;
                std::map<std::pair<label,label>,unsigned> edges;std::map<label,std::size_t> uses;
                for(label q:group.second)
                {
                    const auto& row=p.before_.at(q);std::set<label> unique;
                    for(std::size_t j=0;j<row.ring.size();++j)
                    {
                        if(!R::work(available)||!unique.insert(row.ring[j]).second)
                            return fail("native-original-current-ring");
                        const label a=row.ring[j],b=row.ring[(j+1)%row.ring.size()];
                        if(a==b||++edges[{a,b}]!=1)return fail("native-original-current-directed-incidence");++uses[a];
                    }
                }
                std::map<label,label> next;std::map<label,unsigned> incoming;
                for(const auto& edge:edges)if(!edges.count({edge.first.second,edge.first.first}))
                {if(!next.emplace(edge.first.first,edge.first.second).second||++incoming[edge.first.second]!=1)
                    return fail("native-original-current-boundary-branch");}
                if(next.size()<3||next.size()>64||incoming.size()!=next.size())return fail("native-original-current-boundary");
                R::Ring outer;label q=raw.faces[f].front();const label first=q;std::set<label> visited;
                do{if(!R::work(available)||!visited.insert(q).second||!next.count(q))
                    return fail("native-original-current-boundary-cycle");outer.push_back(q);q=next.at(q);}
                while(q!=first&&outer.size()<=next.size());
                if(q!=first||outer.size()!=next.size())return fail("native-original-current-boundary-components");
                std::set<label> outerIds(outer.begin(),outer.end()),rawIds(raw.faces[f].begin(),raw.faces[f].end());
                for(label pointId:rawIds)if(!outerIds.count(pointId))return fail("native-original-original-node-missing");
                R::E::Context exact(available);std::size_t pos=0,walked=0;
                for(std::size_t j=0;j<raw.faces[f].size();++j)
                {
                    const label a=raw.faces[f][j],b=raw.faces[f][(j+1)%raw.faces[f].size()];
                    if(outer[pos]!=a)return fail("native-original-source-edge-order");
                    label axis=-1;for(label k=0;k<3;++k)if(m.points[a][k]!=m.points[b][k]){axis=k;break;}
                    if(axis<0)return fail("native-original-source-edge-zero");
                    scalar previous=m.points[a][axis];const scalar finish=m.points[b][axis];
                    do
                    {
                        pos=(pos+1)%outer.size();++walked;const label pointId=outer[pos];
                        if(pointId==b)break;
                        const scalar coordinate=m.points[pointId][axis];
                        if(walked>outer.size()||rawIds.count(pointId)
                            ||!R::Detail::onSegment(m,a,b,pointId,exact)
                            ||(m.points[a][axis]<finish?!(previous<coordinate&&coordinate<finish)
                                :!(previous>coordinate&&coordinate>finish)))
                            return fail("native-original-non-source-edge-node");
                        previous=coordinate;
                    }while(true);
                }
                if(pos!=0||walked!=outer.size()||!exact.good())return fail("native-original-source-edge-completeness");
                // An interior fan node may disappear only when this entire true star is retired.
                for(const auto& pointUse:uses)if(!outerIds.count(pointUse.first)
                    &&index.pointReferences.at(pointUse.first)!=pointUse.second)
                    return fail("native-original-interior-node-still-used");
                p.outer_.emplace(f,std::move(outer));
            }
            if(p.plan_.rows.empty()||!R::work(available))return fail("native-original-empty-or-clock");
            p.plan_.ready=true;p.ready_=true;
            if(!current(raw,m,sources,index,p,snapshot,fresh,available,why))return false;
            out=std::move(p);if(why)why->clear();return true;
        }
        catch(...){return fail("native-original-unknown-or-allocation");}
    }
    template<class Raw,class Mesh>
    static bool coverage(const Result& p,const Raw& raw,const Mesh& m,label source,
        const std::vector<label>& faces,label owner,label neighbour,
        const std::function<bool()>& available,R::Ring& outer,std::string* why)
    {
        const auto fail=[&](const char* text){if(why)*why=text;return false;};
        const auto group=p.cohorts_.find(source),boundary=p.outer_.find(source);
        if(!p.ready_||!R::work(available)||group==p.cohorts_.end()||boundary==p.outer_.end()
            ||group->second!=faces||raw.owner[source]!=owner||raw.neighbour[source]!=neighbour)
            return fail("native-original-typed-coverage");
        for(label face:faces)if(!unchanged(m,p.before_.at(face)))return fail("native-original-typed-preimage");
        // This is deliberately NOT a claim that the current tessellation has exact raw I16.
        // Its complete before geometry is checked by the unchanged downstream physical VM.
        outer=boundary->second;return R::work(available);
    }
    template<class Raw,class Mesh>
    static bool candidate(const Raw& raw,const Mesh& m,const Result& p,
        const R::Result& staged,const std::function<bool()>& available,std::string* why)
    {
        const auto fail=[&](const char* text){if(why)*why=text;return false;};
        if(!staged.plan.ready||!staged.plan.cells.empty()||!staged.plan.births.empty()
            ||staged.plan.rows.size()!=p.plan_.rows.size()||staged.sourceGroups!=p.groups_
            ||!staged.unchangedCurrentCohorts.empty()||staged.expectedPoints!=m.points.size()
            ||staged.expectedFaces!=m.faces.size()||staged.expectedCells!=m.nCells)
            return fail("native-original-staged-domain");
        std::set<label> seen;
        for(const auto& row:staged.plan.rows)
        {
            if(!R::work(available)||!p.groups_.count(row.source)||!seen.insert(row.source).second
                ||row.native!=row.source||row.owner!=raw.owner[row.source]||row.neighbour!=raw.neighbour[row.source]
                ||row.patch!=raw.patch[row.source]||row.zone!=raw.zone[row.source]||row.ring.size()<3||row.ring.size()>64)
                return fail("native-original-staged-source-role");
            R::E::Context exact(available);
            const auto& originalRing=raw.faces[row.source];
            if(row.ring.front()!=originalRing.front())return fail("native-original-staged-source-cycle");
            std::set<label> originals(originalRing.begin(),originalRing.end());
            std::set<label> unique(row.ring.begin(),row.ring.end());
            if(unique.size()!=row.ring.size())return fail("native-original-staged-source-node-alias");
            std::size_t pos=0,walked=0;
            for(std::size_t j=0;j<originalRing.size();++j)
            {
                const label a=originalRing[j],b=originalRing[(j+1)%originalRing.size()];
                if(row.ring[pos]!=a)return fail("native-original-staged-original-order");
                label axis=-1;for(label k=0;k<3;++k)if(m.points[a][k]!=m.points[b][k]){axis=k;break;}
                if(axis<0)return fail("native-original-staged-source-edge-zero");
                scalar previous=m.points[a][axis];const scalar finish=m.points[b][axis];
                do
                {
                    pos=(pos+1)%row.ring.size();++walked;const label q=row.ring[pos];
                    if(q==b)break;
                    if(walked>row.ring.size()||originals.count(q)||q<0||std::size_t(q)>=m.points.size()
                        ||!R::Detail::onSegment(m,a,b,q,exact)
                        ||pointBits(m.points[q])==pointBits(m.points[a])
                        ||pointBits(m.points[q])==pointBits(m.points[b]))
                        return fail("native-original-staged-not-source-edge-subdivision");
                    const scalar coordinate=m.points[q][axis];
                    if(m.points[a][axis]<finish?!(previous<coordinate&&coordinate<finish)
                        :!(previous>coordinate&&coordinate>finish))
                        return fail("native-original-staged-source-edge-not-monotone");
                    previous=coordinate;
                }while(true);
            }
            if(pos!=0||walked!=row.ring.size())return fail("native-original-staged-source-edge-completeness");
            // Includes every real edge-refinement point added by the unchanged shared stage.
            // Only Source-exact subdivision is allowed; no point is moved or approximated.
            const auto rawAt=[&](label q)->const point&{return raw.points[q];};
            const auto currentAt=[&](label q)->const point&{return m.points[q];};
            const auto sourceAt=[&](label q)->point
            {const auto& x=p.snapshot_->points[q];return point(x[0],x[1],x[2]);};
            const auto original=I::integrals(originalRing,rawAt,exact);
            const auto source=I::integrals(p.snapshot_->faces[row.source].ring,sourceAt,exact);
            if(!I::same(source,original,exact)||!exact.good())
                return fail("native-original-original-source-sixteen-integrals");
            const auto future=I::integrals(row.ring,currentAt,exact);
            if(!I::same(source,future,exact)||!exact.good())
                return fail("native-original-future-source-sixteen-integrals");
        }
        return seen.size()==p.groups_.size()&&R::work(available);
    }
};
}
template<class Raw,class Mesh,class AllowedFacet,class AllowedParent>
bool prepare(const Raw& raw,const Mesh& m,const std::vector<std::vector<label>>& rawCF,
 const std::vector<std::vector<label>>& currentCF,const std::vector<std::vector<label>>& pieces,
 const std::vector<label>& sources,const R::Index& index,const std::vector<label>& sourceParents,
 label rank,label target,const std::shared_ptr<const Snapshot>& snapshot,const O::Fingerprint& fresh,
 const AllowedFacet& allowedFacet,const AllowedParent& allowedParent,
 const std::function<bool()>& available,Result& out,std::string* why=nullptr)
{return Detail::Access::prepare(raw,m,rawCF,currentCF,pieces,sources,index,sourceParents,
    rank,target,snapshot,fresh,allowedFacet,allowedParent,available,out,why);}
template<class Raw,class Mesh>
bool stage(const Raw& raw,const Mesh& m,const std::vector<label>& sources,const R::Index& index,
 const Result& proposal,const S::PointIndex& pointIndex,
 const std::shared_ptr<const Snapshot>& snapshot,const O::Fingerprint& fresh,
 const std::function<bool()>& available,R::Result& out,std::string* why=nullptr)
{
    out=R::Result();try
    {
        if(!Detail::Access::current(raw,m,sources,index,proposal,snapshot,fresh,available,why))return false;
        const auto coverage=[&](const Raw& before,const Mesh& current,label f,const std::vector<label>& fragments,
            label owner,label neighbour,const std::function<bool()>& work,R::Ring& outer,std::string* reason)
        {return Detail::Access::coverage(proposal,before,current,f,fragments,owner,neighbour,work,outer,reason);};
        R::Result staged;
        if(!R::Detail::stageFaceRestageWithCoverage(raw,m,sources,index,proposal.plan(),proposal.rawParents(),
            proposal.currentParents(),proposal.sourceGroups(),pointIndex,available,staged,why,nullptr,coverage)
            ||!Detail::Access::candidate(raw,m,proposal,staged,available,why))return false;
        out=std::move(staged);return true;
    }
    catch(...){if(why)*why="native-original-staging-exception";return false;}
}
}}
#endif
