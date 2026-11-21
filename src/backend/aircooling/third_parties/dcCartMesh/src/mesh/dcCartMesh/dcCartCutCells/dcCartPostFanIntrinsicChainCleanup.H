#ifndef dcCartPostFanIntrinsicChainCleanup_H
#define dcCartPostFanIntrinsicChainCleanup_H
#include "dcCartIntrinsicRoundoffChainCleanup.H"
#include "dcCartPostCarveCanonicalWallFan.H"
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartRawUnionStockMetrics.H"
#include "dcCartRetainedPlanarBaseStockTets.H"
#include "dcCartPreparedFacetBirth.H"

namespace Foam { namespace dcCartPostFanIntrinsicChainCleanup
{
namespace C=dcCartIntrinsicRoundoffChainCleanup;
namespace F=dcCartPostCarveCanonicalWallFan;
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace Q=dcCartRawUnionStockMetrics;
namespace T=dcCartRetainedPlanarBaseStockTets;
struct Result
{
    bool completed=false,published=false;
    label tested=0,refused=0,cohorts=0,chains=0,removedPointIds=0,retiredFaces=0;
    label shortTried=0,shortPublished=0;
    const char* reason="unchanged";
};
struct Edit
{
    label face=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    bool alive=true;
    std::vector<label> before,after;
};
inline bool available(const std::function<bool()>& work){return !work||work();}
template<class Types> bool coupled(const Types& types,label patch)
{
    if(patch<0)return false;
    if(std::size_t(patch)>=types.size())return true;
    const std::string t=types[patch].c_str();
    return t.find("processor")==0||t.find("cyclic")==0||t.find("mapped")==0
        ||t.find("AMI")!=std::string::npos||t=="overset";
}
// A geometry producer before the unique current Source snapshot, never a
// whole-cell acceptance or a Source/peer/publication permit. Old coordinate
// pins/footprints are unchanged: cancelling an incidence does not move its point.
template<class Mesh,class Types>
Result apply(Mesh& m,const std::vector<F::Role>& roles,const Types& types,label rank,
    const std::function<bool()>& work)
{
    Result out;
    const auto refuse=[&](const char* why){++out.refused;out.reason=why;};
    try
    {
        const std::size_t nf=m.faces.size(),np=m.points.size(),nc=std::size_t(m.nCells);
        if(!available(work)){out.reason="whole-clock";return out;}
        if(m.owner.size()!=nf||m.neighbour.size()!=nf||m.patch.size()!=nf||m.zone.size()!=nf
            ||m.faceAlive.size()!=nf||m.cellAlive.size()!=nc||m.cellRank.size()!=nc
            ||(!m.preparedExactPoints.empty()&&m.preparedExactPoints.size()!=np)
            ||(!m.preparedPointFootprints.empty()&&m.preparedPointFootprints.size()!=np))
        {out.reason="native-shape";return out;}
        if(m.writerBaseline||m.weightUnionState||m.completeCavityContext||m.completeCavityState)
        {out.reason="already-snapshotted";return out;}
        if(m.canonicalPhysicalFanOrigins.empty()){out.completed=true;return out;}
        // Independent value-owned origin; survivor publication cannot invalidate
        // this loop. Every actual tuple/ring/bit is checked before it is used.
        const auto origins=m.canonicalPhysicalFanOrigins;
        std::vector<unsigned char> valid(origins.size(),0);
        std::map<label,std::size_t> fanOfFace,fanOfOrigin;
        const auto local=[&](label c)
        {return c>=0&&std::size_t(c)<nc&&m.cellAlive[c]&&m.cellRank[c]==rank;};
        const auto currentRecord=[&](const F::Record& r)
        {
            if(!available(work)||!local(r.parent)||r.original<0||r.patch<0||r.zone!=-1
                ||std::size_t(r.patch)>=roles.size()||std::size_t(r.patch)>=types.size()
                ||std::string(types[r.patch].c_str())!="wall"
                ||!F::sameRole(r.role,roles[r.patch])||r.role.region.empty()
                ||r.role.sha.empty()||r.role.operation.empty()
                ||r.originalRing.size()<3||r.originalRing.size()>64
                ||r.actualFaces.size()!=r.originalRing.size()
                ||r.originalPointBits.size()!=r.originalRing.size()
                ||r.hub<0||std::size_t(r.hub)>=np||F::bits(m.points[r.hub])!=r.hubBits
                ||F::bits(r.storedFC)!=r.hubBits
                ||r.extractionEpoch!=m.preparedBirth.extractionEpoch
                ||r.generation!=m.preparedBirth.generation)return false;
            std::set<label> seen;
            for(std::size_t j=0;j<r.originalRing.size();++j)
            {
                if(!available(work))return false;
                const label p=r.originalRing[j],f=r.actualFaces[j];
                if(p<0||std::size_t(p)>=np||F::bits(m.points[p])!=r.originalPointBits[j]
                    ||!seen.insert(p).second||f<0||std::size_t(f)>=nf||!m.faceAlive[f]
                    ||m.owner[f]!=r.parent||m.neighbour[f]!=-1||m.patch[f]!=r.patch||m.zone[f]!=r.zone
                    ||m.faces[f]!=std::vector<label>{p,r.originalRing[(j+1)%r.originalRing.size()],r.hub})return false;
            }
            return true;
        };
        for(std::size_t k=0;k<origins.size();++k)
        {
            if(!available(work)){out.reason="whole-clock";return out;}
            if(!currentRecord(origins[k]))continue;
            if(!fanOfOrigin.emplace(origins[k].original,k).second)
            {out.reason="ambiguous-fan-origin";return out;}
            for(label f:origins[k].actualFaces)if(!fanOfFace.emplace(f,k).second)
            {out.reason="overlapping-fan-origins";return out;}
            valid[k]=1;
        }
        // One actual alive-face sweep builds both complete point stars and CF.
        // No chain/parent scans the full mesh again.
        std::vector<std::vector<label>> pointFaces(np),cellFaces(nc);
        for(label f=0;std::size_t(f)<nf;++f)
        {
            if(!available(work)){out.reason="whole-clock";return out;}
            if(!m.faceAlive[f])continue;
            const label o=m.owner[f],n=m.neighbour[f];
            if(o<0||std::size_t(o)>=nc||n==o||n<-1||(n>=0&&std::size_t(n)>=nc))
            {out.reason="native-endpoint-shape";return out;}
            cellFaces[o].push_back(f);if(n>=0)cellFaces[n].push_back(f);
            for(label p:m.faces[f])
            {
                if(!available(work)){out.reason="whole-clock";return out;}
                if(p<0||std::size_t(p)>=np||!C::finite(m.points[p]))
                {out.reason="native-point-shape";return out;}
                pointFaces[p].push_back(f);
            }
        }
        std::set<label> publishedParents;
        for(std::size_t seed=0;seed<origins.size();++seed)
        {
            if(!available(work)){out.reason="whole-clock";return out;}
            if(!valid[seed]||publishedParents.count(origins[seed].parent))continue;
            ++out.tested;
            // Local star cohort, not the whole connected pipe. The primitive's
            // 64 rows/512 points/64-node rings remain unchanged.
            std::map<label,C::Row> virtualRows;
            std::set<label> completePoints;
            // Only a removable point needs a complete local three-row star.
            // A branch/end point remains in every original ring; its unrelated
            // neighbours cannot veto a different, fully proved internal chain.
            // The one-pass index predates earlier publications. Any stale or
            // already-published incidence protects this point instead of being
            // omitted and accidentally turning an incomplete star into three.
            bool good=true;
            for(label p:origins[seed].originalRing)
            {
                std::map<label,C::Row> pointRows;bool supported=true;
                for(label f:pointFaces[p])
                {
                    if(!available(work)){out.reason="whole-clock";return out;}
                    if(!m.faceAlive[f]){supported=false;break;}
                    const auto birth=fanOfFace.find(f);
                    C::Row row;
                    if(birth!=fanOfFace.end())
                    {
                        const auto& r=origins[birth->second];
                        // Inspect every actual incident triangle before its
                        // verified producer ring is used as one virtual row.
                        if(publishedParents.count(r.parent)||!currentRecord(r)
                            ||std::find(r.originalRing.begin(),r.originalRing.end(),p)==r.originalRing.end())
                        {supported=false;break;}
                        row.native=r.original;row.owner=r.parent;row.neighbour=-1;
                        row.patch=r.patch;row.zone=r.zone;row.ring=r.originalRing;
                        row.boundaryType=types[r.patch].c_str();row.sourceRoleKnown=true;
                        row.sourceRegion=r.role.region;row.sourceSha=r.role.sha;row.operation=r.role.operation;
                    }
                    else
                    {
                        row.native=f;row.owner=m.owner[f];row.neighbour=m.neighbour[f];
                        row.patch=m.patch[f];row.zone=m.zone[f];row.ring=m.faces[f];
                        if(row.patch>=0)
                        {
                            if(std::size_t(row.patch)>=types.size()){supported=false;break;}
                            row.boundaryType=types[row.patch].c_str();
                            if(std::size_t(row.patch)<roles.size())
                            {
                                const auto& r=roles[row.patch];row.sourceRoleKnown=r.known;
                                row.sourceRegion=r.region;row.sourceSha=r.sha;row.operation=r.operation;
                            }
                        }
                    }
                    if(!local(row.owner)||(row.neighbour>=0&&!local(row.neighbour))
                        ||publishedParents.count(row.owner)||publishedParents.count(row.neighbour)
                        ||row.zone!=-1||row.ring.size()<3||row.ring.size()>64
                        ||std::count(row.ring.begin(),row.ring.end(),p)!=1)
                    {supported=false;break;}
                    pointRows.emplace(row.native,std::move(row));
                    if(pointRows.size()>3){supported=false;break;}
                }
                if(!supported||pointRows.size()!=3)continue;
                const C::Row* internal=nullptr;std::vector<const C::Row*> physical;
                std::array<label,2> adjacent{{-1,-1}};bool first=true;
                for(const auto& item:pointRows)
                {
                    const auto& row=item.second;
                    if(row.neighbour>=0)
                    {
                        if(internal||row.patch!=-1){supported=false;break;}
                        internal=&row;
                    }
                    else
                    {
                        const auto fan=fanOfOrigin.find(row.native);
                        if(row.patch<0||row.boundaryType!="wall"||!row.sourceRoleKnown
                            ||fan==fanOfOrigin.end())
                        {supported=false;break;}
                        physical.push_back(&row);
                    }
                    const auto at=std::find(row.ring.begin(),row.ring.end(),p);
                    const std::size_t k=at-row.ring.begin();
                    std::array<label,2> near{{row.ring[(k+row.ring.size()-1)%row.ring.size()],
                        row.ring[(k+1)%row.ring.size()]}};
                    std::sort(near.begin(),near.end());
                    if(first){adjacent=near;first=false;}
                    else if(adjacent!=near){supported=false;break;}
                }
                if(!supported||!internal||physical.size()!=2||adjacent[0]==adjacent[1])continue;
                const auto& a=*physical[0];const auto& b=*physical[1];
                if(a.owner==b.owner||a.patch!=b.patch
                    ||std::set<label>{a.owner,b.owner}!=std::set<label>{internal->owner,internal->neighbour}
                    ||!F::sameRole(origins[fanOfOrigin.at(a.native)].role,
                        origins[fanOfOrigin.at(b.native)].role))continue;
                completePoints.insert(p);
                for(auto& item:pointRows)virtualRows.emplace(item.first,std::move(item.second));
                if(virtualRows.size()>64){good=false;break;}
            }
            if(!good){refuse("local-complete-star-scope");continue;}
            std::vector<C::Row> input;input.reserve(virtualRows.size());
            for(const auto& kv:virtualRows)input.push_back(kv.second);
            // This producer never changes any point coordinate, pin bit,
            // footprint or point ID. Branch/source/zone/coupled vertices are
            // protected by their complete actual star, not invented pin data.
            const std::set<label> noCoordinateMoves;
            C::Result proposal;
            if(!C::prepare(m.points,input,completePoints,noCoordinateMoves,work,proposal))
            {if(!available(work)){out.reason="whole-clock";return out;}refuse(proposal.reason);continue;}
            std::map<label,Edit> edits;std::set<label> affectedParents,changedOrigins;
            std::set<std::size_t> changedFanOrder;
            const auto edit=[&](label f)->Edit&
            {
                auto at=edits.find(f);if(at!=edits.end())return at->second;
                Edit e;e.face=f;e.owner=m.owner[f];e.neighbour=m.neighbour[f];
                e.patch=m.patch[f];e.zone=m.zone[f];e.before=m.faces[f];e.after=e.before;
                return edits.emplace(f,std::move(e)).first->second;
            };
            for(const auto& kv:virtualRows)
            {
                const auto& row=kv.second;const auto& shortened=proposal.rings.at(row.native);
                if(shortened==row.ring)continue;
                const auto fan=fanOfOrigin.find(row.native);
                if(row.neighbour>=0)
                {
                    if(fan!=fanOfOrigin.end()){good=false;break;}
                    edit(row.native).after=shortened;
                    affectedParents.insert(row.owner);affectedParents.insert(row.neighbour);
                }
                else
                {
                    if(fan==fanOfOrigin.end()){good=false;break;}
                    const auto& r=origins[fan->second];
                    if(!currentRecord(r)||!changedOrigins.insert(r.original).second){good=false;break;}
                    changedFanOrder.insert(fan->second);
                    for(label f:r.actualFaces)edit(f).alive=false;
                    // Keep every unchanged actual triangle literally. A chain
                    // span reuses its first consumed face ID; all other span
                    // pieces retire. Its actual old hub never moves/recomputes.
                    for(std::size_t a=0;a<shortened.size();++a)
                    {
                        const label p=shortened[a],q=shortened[(a+1)%shortened.size()];
                        auto begin=std::find(r.originalRing.begin(),r.originalRing.end(),p);
                        if(begin==r.originalRing.end()){good=false;break;}
                        std::size_t j=begin-r.originalRing.begin(),start=j,consumed=0;
                        do{j=(j+1)%r.originalRing.size();++consumed;}
                        while(r.originalRing[j]!=q&&consumed<=r.originalRing.size());
                        if(consumed>r.originalRing.size()){good=false;break;}
                        auto& next=edit(r.actualFaces[start]);next.alive=true;
                        next.after={p,q,r.hub};
                    }
                    if(!good)break;
                    affectedParents.insert(row.owner);
                }
            }
            if(!good||edits.empty()||affectedParents.empty()||affectedParents.size()>16)
            {refuse("fixed-hub-cohort-scope");continue;}
            // Both physical sides must be genuinely the same actual role,
            // including entity; a region-shaped string alone grants nothing.
            for(const auto& chain:proposal.chains)
            {
                const F::Role* first=nullptr;
                for(label f:chain.faces)if(virtualRows.at(f).neighbour<0)
                {
                    const auto fan=fanOfOrigin.find(f);
                    if(fan==fanOfOrigin.end()){good=false;break;}
                    const auto& r=origins[fan->second].role;
                    if(!first)first=&r;else if(!F::sameRole(*first,r)){good=false;break;}
                }
                if(!good)break;
            }
            if(!good){refuse("actual-producer-role");continue;}
            S::Plan none;
            Q::FaceOF metric=[&](const std::vector<label>& ring,vector& sf,point& fc)
            {S::metric(m,none,ring,sf,fc);};
            Q::CoupledTypePatch isCoupled=[&](label p){return coupled(types,p);};
            // Representation choice only, never whole-cell permission. Try each
            // changed actual fan once in its original producer order; a failed
            // trial leaves the complete fixed-hub edits literally unchanged.
            const auto shortOwnerAccepted=[&](label parent,const std::map<label,Edit>& trial)
            {
                if(!available(work)||!local(parent)||publishedParents.count(parent))return false;
                const auto& cf=cellFaces[parent];
                if(cf.size()<4||cf.size()>64)return false;
                std::vector<Q::Face> shells[2];std::set<label> referencePoints;
                for(label f:cf)
                {
                    if(!available(work)||!m.faceAlive[f]||!local(m.owner[f])
                        ||(m.neighbour[f]>=0&&!local(m.neighbour[f]))
                        ||coupled(types,m.patch[f]))return false;
                    const auto e=trial.find(f);
                    shells[0].emplace_back(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
                    for(label p:m.faces[f])referencePoints.insert(p);
                    if(e==trial.end()||e->second.alive)
                        shells[1].emplace_back(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],
                            e==trial.end()?m.faces[f]:e->second.after);
                }
                if(referencePoints.empty()||referencePoints.size()>512
                    ||shells[1].size()<4||shells[1].size()>64)return false;
                point reference=point::zero;for(label p:referencePoints)reference+=m.points[p];
                reference/=referencePoints.size();
                S::Geometry geometries[2];
                for(unsigned state=0;state<2;++state)
                {
                    std::vector<S::Face> physical;physical.reserve(shells[state].size());
                    for(const auto& row:shells[state])physical.push_back({row.id,row.ring,row.owner==parent});
                    if(!S::geometry(m,none,physical,reference,work,geometries[state]))return false;
                }
                const auto& a=geometries[0];const auto& b=geometries[1];
                const scalar span=max(mag(a.hi-a.lo),scalar(1e-6));
                if(!std::isfinite(a.volume)||a.volume<=0||!std::isfinite(b.volume)
                    ||std::abs(a.volume-b.volume)>1e-10*a.volume)return false;
                for(unsigned k=0;k<4;++k)
                {
                    const long double den=std::max(a.physical[0]*(k?span:1),static_cast<long double>(VSMALL));
                    if(!std::isfinite(a.physical[k])||!std::isfinite(b.physical[k])
                        ||std::abs(a.physical[k]-b.physical[k])>1e-10L*den)return false;
                }
                Q::Cell plain;
                for(unsigned repaired=0;repaired<2;++repaired)
                {
                    auto q=Q::measure(parent,shells[1],m.points,metric,isCoupled,work,true,repaired!=0);
                    if(!available(work)||!q.finite||!q.strictGeometry
                        ||!std::isfinite(q.determinant)||q.determinant<.001
                        ||!std::isfinite(q.aspect)||q.aspect>1000)return false;
                    if(!repaired)plain=std::move(q);
                }
                if(plain.faceIds.size()!=shells[1].size()||plain.areas.size()!=shells[1].size()
                    ||plain.centres.size()!=shells[1].size()||!Q::finitePoint(plain.centre)
                    ||!std::isfinite(plain.volume)||plain.volume<=0)return false;
                for(std::size_t k=0;k<shells[1].size();++k)if(shells[1][k].neighbour<0)
                {
                    std::vector<point> ring;ring.reserve(shells[1][k].ring.size());
                    for(label p:shells[1][k].ring)ring.push_back(m.points[p]);
                    if(!T::accept(ring,plain.centres[k],plain.centre,nullptr,work,nullptr))return false;
                }
                return available(work);
            };
            label shortInCohort=0;
            for(std::size_t k:changedFanOrder)
            {
                if(!available(work)){out.reason="whole-clock";return out;}
                const auto& r=origins[k];const auto& outline=proposal.rings.at(r.original);
                ++out.shortTried;
                std::vector<label> pieces;
                for(label f:r.actualFaces)
                {
                    const auto e=edits.find(f);
                    if(e==edits.end()){good=false;break;}
                    if(e->second.alive)pieces.push_back(f);
                }
                if(!good||pieces.size()!=outline.size()||pieces.empty())
                {good=false;break;}
                auto trial=edits;
                const label carrier=*std::min_element(pieces.begin(),pieces.end());
                for(label f:pieces)trial.at(f).alive=false;
                trial.at(carrier).alive=true;trial.at(carrier).after=outline;
                if(shortOwnerAccepted(r.parent,trial))
                {edits.swap(trial);++shortInCohort;}
            }
            if(!good){refuse("short-outline-actual-fan-binding");continue;}
            // The original full-cohort VM/tet/preimage/publication contract still
            // runs on every affected parent, including fixed-hub refusals.
            for(label parent:affectedParents)
            {
                if(!available(work)){out.reason="whole-clock";return out;}
                const auto& cf=cellFaces[parent];
                if(!local(parent)||publishedParents.count(parent)||cf.size()<4||cf.size()>64){good=false;break;}
                std::vector<Q::Face> shells[2];std::set<label> referencePoints;
                for(label f:cf)
                {
                    if(!m.faceAlive[f]||!local(m.owner[f])
                        ||(m.neighbour[f]>=0&&!local(m.neighbour[f]))
                        ||coupled(types,m.patch[f])
                        ||(m.patch[f]>=0&&std::size_t(m.patch[f])>=types.size()))
                    {good=false;break;}
                    const auto ei=edits.find(f);
                    shells[0].emplace_back(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],m.faces[f]);
                    for(label p:m.faces[f])referencePoints.insert(p);
                    if(ei==edits.end()||ei->second.alive)
                        shells[1].emplace_back(f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],
                            ei==edits.end()?m.faces[f]:ei->second.after);
                }
                if(!good||referencePoints.empty()||referencePoints.size()>512
                    ||shells[1].size()<4||shells[1].size()>64){good=false;break;}
                point reference=point::zero;for(label p:referencePoints)reference+=m.points[p];
                reference/=referencePoints.size();
                S::Geometry geometries[2];
                for(unsigned state=0;state<2;++state)
                {
                    std::vector<S::Face> physical;physical.reserve(shells[state].size());
                    for(const auto& row:shells[state])physical.push_back({row.id,row.ring,row.owner==parent});
                    if(!S::geometry(m,none,physical,reference,work,geometries[state])){good=false;break;}
                }
                if(!good)break;
                const auto& a=geometries[0];const auto& b=geometries[1];
                const scalar span=max(mag(a.hi-a.lo),scalar(1e-6));
                good=a.volume>0&&std::isfinite(a.volume)&&std::isfinite(b.volume)
                    &&std::abs(a.volume-b.volume)<=1e-10*a.volume;
                for(unsigned k=0;k<4;++k)
                {
                    const long double den=std::max(a.physical[0]*(k?span:1),static_cast<long double>(VSMALL));
                    good=good&&std::isfinite(a.physical[k])&&std::isfinite(b.physical[k])
                        &&std::abs(a.physical[k]-b.physical[k])<=1e-10L*den;
                }
                if(!good)break;
                // Whole fan-shell C may already be negative. It is never
                // converted to success: only its actually computed full CC/V,
                // Sf/FC arrays feed the physical stock-tet check here.
                Q::Cell q=Q::measure(parent,shells[1],m.points,metric,isCoupled,work,true,false);
                if(q.faceIds.size()!=shells[1].size()||q.areas.size()!=shells[1].size()
                    ||q.centres.size()!=shells[1].size()||!Q::finitePoint(q.centre)
                    ||!std::isfinite(q.volume)||q.volume<=0){good=false;break;}
                for(std::size_t k=0;k<shells[1].size();++k)if(shells[1][k].neighbour<0)
                {
                    std::vector<point> ring;ring.reserve(shells[1][k].ring.size());
                    for(label p:shells[1][k].ring)ring.push_back(m.points[p]);
                    if(!T::accept(ring,q.centres[k],q.centre,nullptr,work,nullptr)){good=false;break;}
                }
                if(!good)break;
            }
            if(!good)
            {if(!available(work)){out.reason="whole-clock";return out;}refuse("complete-parent-physical-vm-or-tets");continue;}
            // Retain only untouched construction origins. A shortened old-hub
            // fan is NOT a new OF-FC canonical fan Record; no epoch relabelling.
            std::vector<F::Record> survivors;survivors.reserve(m.canonicalPhysicalFanOrigins.size());
            for(const auto& r:m.canonicalPhysicalFanOrigins)
                if(!affectedParents.count(r.parent))survivors.push_back(r);
            // Preallocate all bookkeeping before the first live mutation.
            std::set<label> nextPublished=publishedParents;
            nextPublished.insert(affectedParents.begin(),affectedParents.end());
            label retired=0;
            for(const auto& kv:edits)
            {
                const auto& e=kv.second;
                if(!available(work)){out.reason="whole-clock";return out;}
                if(!m.faceAlive[e.face]||m.owner[e.face]!=e.owner||m.neighbour[e.face]!=e.neighbour
                    ||m.patch[e.face]!=e.patch||m.zone[e.face]!=e.zone||m.faces[e.face]!=e.before)
                {good=false;break;}
                if(!e.alive)++retired;
            }
            if(!good){refuse("stale-native-preimage");continue;}
            if(!available(work)){out.reason="whole-clock";return out;}
            // No callbacks, allocation or throwing construction after here.
            for(auto& kv:edits)
            {
                auto& e=kv.second;
                if(e.alive)m.faces[e.face].swap(e.after);
                else m.faceAlive[e.face]=false;
            }
            m.canonicalPhysicalFanOrigins.swap(survivors);
            publishedParents.swap(nextPublished);
            dcCartPreparedFacetBirth::invalidateCurrentIncidence(m);
            out.published=true;++out.cohorts;out.chains+=proposal.chains.size();
            out.removedPointIds+=proposal.removed.size();out.retiredFaces+=retired;
            out.shortPublished+=shortInCohort;
            out.reason="fixed-actual-hub-chain-cleanup";
        }
        out.completed=available(work);if(!out.completed)out.reason="whole-clock";
        return out;
    }
    catch(const std::exception&)
    {
        // All allocating/geometry work belongs to a private cohort. Earlier
        // successful disjoint cohorts remain actual publications, never relabelled
        // unchanged; the current failing cohort has made no logical mesh edits.
        out.reason="cohort-resource-or-geometry";return out;
    }
}
}}
#endif
