#ifndef dcCartOutsideRecipeRectilinearPromotion_H
#define dcCartOutsideRecipeRectilinearPromotion_H
#include "dcCartFiniteOutsidePromotionMetadata.H"
#include "dcCartPlannedSourceColumns.H"
#include <algorithm>
#include <array>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <limits>
#include <utility>

namespace Foam { namespace dcCartOutsideRecipeRectilinearPromotion
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace P=dcCartPlannedSourceColumns;
namespace F=dcCartFiniteTensorCutScope;
namespace M=dcCartFiniteOutsidePromotionMetadata;
using Plane=F::Plane;
using RootPlanes=std::map<label,std::vector<Plane>>;
enum class Kind : unsigned char { Unknown,ActualOutsideRecipeSideFC,ActualSurfaceWholeFC };
enum class GeometryKind : unsigned char { Unknown,RectilinearCompound,AxisBoxBoundaryKnots };
enum class BirthKind : unsigned char { Unknown,ExactSurfaceEdge,ActualStagedVolumeBirth };
struct BirthOrigin
{label ordinal=-1,first=-1,second=-1;Plane plane={-1,0};BirthKind kind=BirthKind::Unknown;};
struct Evidence
{
    Kind kind=Kind::Unknown;
    GeometryKind geometryKind=GeometryKind::Unknown;
    const void* rawIdentity=nullptr;
    std::size_t rawPoints=0,rawFaces=0;label rawCells=-1;
    label rawRoot=-1,currentMother=-1,plannedChild=-1,nativeWholeChild=-1;
    std::array<label,2> sideOrdinals{{-1,-1}},stockFaces{{-1,-1}};
    std::size_t basePoints=0,stagedPoints=0;
    std::vector<label> rawSources;
    std::vector<BirthOrigin> actualIncomingBirths;
    std::vector<Plane> initialPlanes;
    bool complete=false;
};
// This is actual failure identity selection, not a quality or Source test.
// The separate raw full-CF qualifier must still produce initialPlanes.
template<class Raw,class Mesh>
bool select(const Raw& raw,const Mesh& m,const S::Plan& staged,
 const P::Prepared& failure,const std::string& cause,label rank,
 const RootPlanes& assigned,const std::vector<std::vector<label>>& rawCf,
 const std::vector<label>& sourceParent,const std::vector<label>& currentSource,
 const std::map<label,std::vector<S::Row>>& shells,const std::set<label>& wholeParts,
 const std::map<label,dcCartPlannedConeGeometry::Parent>& outsideRecipes,
 const std::vector<label>& affected,const std::map<label,std::vector<label>>& sourceGroups,
 const F::SurfaceHalo& halo,const std::function<bool()>& available,
 Evidence& out,std::string* why=nullptr)
{
    out=Evidence();
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    auto work=[&](){return S::clock(available);};
    const label parent=failure.failedParent,root=failure.failedRawRoot;
    if(!work())return fail("outside-promotion-work");
    if(cause!="face-centre-concavity"||!failure.failedPlanned||!failure.failedFacesCaptured
        ||!staged.ready||staged.cutScope!=F::CutScope::FiniteTensor
        ||parent<0||parent>=m.nCells||!m.cellAlive[parent]||m.cellRank[parent]!=rank
        ||wholeParts.count(parent)||std::count(affected.begin(),affected.end(),parent)!=1)
        return fail("outside-promotion-kind-or-current-mother");
    if(root<0||root>=raw.nCells||std::size_t(root)>=rawCf.size()
        ||!raw.cellAlive[root]||raw.cellRank[root]!=rank
        ||std::size_t(parent)>=sourceParent.size()||sourceParent[parent]!=root
        ||assigned.count(root)||!halo.parents.count(root))
        return fail("outside-promotion-raw-origin-or-halo");
    if(failure.failedBasePointCount!=m.points.size()
        ||failure.failedStagedPointCount!=m.points.size()+staged.births.size()
        ||staged.birthPlanes.size()!=staged.births.size()
        ||failure.failedStagedPointCount>=std::size_t(std::numeric_limits<label>::max()))
        return fail("outside-promotion-point-domain");
    for(const auto& cell:staged.cells)
    {
        if(!work())return fail("outside-promotion-work");
        for(std::size_t i=0;i<S::partCount(cell);++i)
            if(S::partCell(cell,i)==parent)return fail("outside-promotion-is-volume-part");
    }
    const auto recipeIt=outsideRecipes.find(parent);
    const auto shellIt=shells.find(parent);
    if(recipeIt==outsideRecipes.end()||shellIt==shells.end())
        return fail("outside-promotion-missing-complete-recipe");
    const auto& recipe=recipeIt->second;const auto& shell=shellIt->second;
    if(!recipe.known||recipe.geometry.births.size()!=1
        ||!S::finite(recipe.geometry.births.front().position)
        ||recipe.children.empty()||failure.failedChild<0
        ||std::size_t(failure.failedChild)>=recipe.children.size()
        ||recipe.baseFaces.size()!=recipe.baseToChild.size()
        ||!recipe.composites.empty()||!recipe.interfaces.empty()
        ||shell.size()<6||shell.size()>64||recipe.baseFaces.size()!=shell.size())
        return fail("outside-promotion-recipe-domain");
    const label apex=label(failure.failedStagedPointCount);
    for(std::size_t k=0;k<2;++k)
    {
        const auto& face=failure.failedFaces[k];
        const label expectedStock=k?failure.failedPair.otherFace:failure.failedPair.face;
        if(!work())return fail("outside-promotion-work");
        if(face.kind!=P::FailedFaceKind::RecipeSide||face.recipeParent!=parent
            ||face.recipeRawRoot!=root||face.recipeSide<0
            ||std::size_t(face.recipeSide)>=recipe.sides.size()
            ||face.stockKey!=expectedStock||face.proposedNativeFace!=-1||face.sourceFace!=-1)
            return fail("outside-promotion-actual-recipe-side");
        const auto& side=recipe.sides[face.recipeSide];
        if(side.ring!=face.ring||side.owner<0||side.neighbour<0||side.owner==side.neighbour
            ||std::size_t(side.owner)>=recipe.children.size()
            ||std::size_t(side.neighbour)>=recipe.children.size()
            ||(side.owner==failure.failedChild)==(side.neighbour==failure.failedChild)
            ||face.owner.kind!=P::FailedEndpointKind::PlannedChild
            ||face.neighbour.kind!=P::FailedEndpointKind::PlannedChild
            ||face.owner.stagedParent!=parent||face.neighbour.stagedParent!=parent
            ||face.owner.rawRoot!=root||face.neighbour.rawRoot!=root
            ||face.owner.plannedChild!=side.owner||face.neighbour.plannedChild!=side.neighbour
            ||face.ring.size()<3||face.ring.size()>64
            ||std::count(face.ring.begin(),face.ring.end(),apex)!=1)
            return fail("outside-promotion-side-endpoint-or-apex");
        std::set<label> unique;
        for(label p:face.ring)
        {
            if(!work()||p<0||!unique.insert(p).second)
                return fail("outside-promotion-side-point-domain");
            if(p==apex)continue; // recipe-only AP: never a mesh point/birth
            if(std::size_t(p)>=failure.failedStagedPointCount||!S::finite(S::coordinate(m,staged,p)))
                return fail("outside-promotion-side-point-domain");
        }
        out.sideOrdinals[k]=face.recipeSide;out.stockFaces[k]=face.stockKey;
    }
    if(out.stockFaces[0]==out.stockFaces[1]||out.sideOrdinals[0]==out.sideOrdinals[1])
        return fail("outside-promotion-identical-blockers");
    std::set<label> rawSources;
    if(rawCf[root].size()<6||rawCf[root].size()>64)
        return fail("outside-promotion-raw-CF-bound");
    for(label f:rawCf[root])
    {
        if(!work()||f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
            ||(raw.owner[f]!=root&&raw.neighbour[f]!=root)||!rawSources.insert(f).second)
            return fail("outside-promotion-raw-CF-identity");
    }
    std::map<label,const S::Row*> stagedRows;
    for(const auto& row:staged.rows)if(row.owner==parent||row.neighbour==parent)
        if(!work()||!stagedRows.emplace(row.native,&row).second)
            return fail("outside-promotion-staged-row");
    std::set<label> nativeRows,representedSources;std::set<std::size_t> incomingBirths;
    for(const auto& row:shell)
    {
        if(!work()||row.native<0||row.source<0||!nativeRows.insert(row.native).second
            ||!rawSources.count(row.source)||(row.owner==parent)==(row.neighbour==parent)
            ||row.owner<0||row.neighbour<-1||row.owner==row.neighbour
            ||row.patch!=raw.patch[row.source]||row.zone!=raw.zone[row.source]
            ||row.ring.size()<3||row.ring.size()>64)
            return fail("outside-promotion-full-current-source-tuple");
        representedSources.insert(row.source);
        const auto changed=stagedRows.find(row.native);
        if(changed!=stagedRows.end())
        {
            const auto& a=*changed->second;
            if(a.source!=row.source||a.owner!=row.owner||a.neighbour!=row.neighbour
                ||a.patch!=row.patch||a.zone!=row.zone||a.ring!=row.ring)
                return fail("outside-promotion-restored-row-preimage");
        }
        else if(std::size_t(row.native)>=m.faces.size()||!m.faceAlive[row.native]
            ||std::size_t(row.native)>=currentSource.size()||currentSource[row.native]!=row.source
            ||m.owner[row.native]!=row.owner||m.neighbour[row.native]!=row.neighbour
            ||m.patch[row.native]!=row.patch||m.zone[row.native]!=row.zone||m.faces[row.native]!=row.ring)
            return fail("outside-promotion-current-row-preimage");
        std::set<label> unique;
        for(label id:row.ring)
        {
            if(!work()||id<0||std::size_t(id)>=failure.failedStagedPointCount
                ||!unique.insert(id).second||!S::finite(S::coordinate(m,staged,id)))
                return fail("outside-promotion-full-current-point-domain");
            if(std::size_t(id)<failure.failedBasePointCount)continue;
            const auto group=sourceGroups.find(row.source);
            if(group==sourceGroups.end()
                ||std::count(group->second.begin(),group->second.end(),row.native)!=1)
                return fail("outside-promotion-incoming-source-cohort");
            incomingBirths.insert(std::size_t(id)-failure.failedBasePointCount);
        }
    }
    if(representedSources!=rawSources||nativeRows.size()!=recipe.baseFaces.size())
        return fail("outside-promotion-complete-source-cohorts");
    std::set<label> recipeBases;
    for(std::size_t k=0;k<recipe.baseFaces.size();++k)
    {
        if(!work()||!nativeRows.count(recipe.baseFaces[k])||!recipeBases.insert(recipe.baseFaces[k]).second
            ||recipe.baseToChild[k]<0||std::size_t(recipe.baseToChild[k])>=recipe.children.size())
            return fail("outside-promotion-full-base-identity");
    }
    if(incomingBirths.empty())return fail("outside-promotion-no-actual-incoming-birth");
    for(std::size_t i:incomingBirths)
    {
        if(!work()||i>=staged.births.size()||i>=staged.birthPlanes.size())
            return fail("outside-promotion-incoming-birth-domain");
        const auto& birth=staged.births[i];const auto& plane=staged.birthPlanes[i];
        if(birth.first<0||birth.second<0||birth.first==birth.second
            ||std::size_t(birth.first)>=failure.failedBasePointCount+i
            ||std::size_t(birth.second)>=failure.failedBasePointCount+i
            ||!S::finite(birth.position)||plane.first<0||plane.first>2||!std::isfinite(plane.second))
            return fail("outside-promotion-incoming-producing-plane");
        out.actualIncomingBirths.push_back({label(i),birth.first,birth.second,plane,BirthKind::ActualStagedVolumeBirth});
    }
    out.kind=Kind::ActualOutsideRecipeSideFC;out.rawIdentity=static_cast<const void*>(&raw);
    out.rawPoints=raw.points.size();out.rawFaces=raw.faces.size();out.rawCells=raw.nCells;
    out.rawRoot=root;out.currentMother=parent;out.plannedChild=failure.failedChild;
    out.basePoints=failure.failedBasePointCount;out.stagedPoints=failure.failedStagedPointCount;
    out.rawSources.assign(rawSources.begin(),rawSources.end());out.complete=true;
    if(why)*why="actual-outside-recipe-side-FC-rectilinear-proposal";
    return true;
}
// Distinct actual native-whole FC observation. This is not an AP recipe
// and the already refused whole measurement is never rerun here.
template<class Raw,class Mesh>
bool selectSurfaceWhole(const Raw& raw,const Mesh& m,const S::Plan& staged,
 const P::Prepared& failure,const std::string& cause,label rank,
 const RootPlanes& assigned,const std::vector<std::vector<label>>& rawCf,
 const std::vector<label>& sourceParent,const std::vector<label>& currentSource,
 const std::map<label,std::vector<S::Row>>& shells,const std::set<label>& wholeParts,
 const std::map<label,dcCartPlannedConeGeometry::Parent>& outsideRecipes,
 const std::vector<label>& affected,const std::map<label,std::vector<label>>& sourceGroups,
 const F::SurfaceHalo& halo,const std::function<bool()>& available,
 Evidence& out,std::string* why=nullptr)
{
    out=Evidence();
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    auto work=[&](){return S::clock(available);};
    const label parent=failure.failedParent,root=failure.failedRawRoot;
    if(!work())return fail("outside-whole-promotion-work");
    if(cause!="face-centre-concavity"||failure.failedPlanned||failure.failedChild!=0
        ||!failure.failedFacesCaptured||!staged.ready||staged.cutScope!=F::CutScope::FiniteTensor
        ||staged.surfaceEdgeBirths.empty()||!staged.surfaceWholeParents.count(parent)
        ||parent<0||parent>=m.nCells||!m.cellAlive[parent]||m.cellRank[parent]!=rank
        ||wholeParts.count(parent)||outsideRecipes.count(parent)
        ||std::count(affected.begin(),affected.end(),parent)!=1)
        return fail("outside-whole-promotion-kind-or-current-mother");
    if(root<0||root>=raw.nCells||std::size_t(root)>=rawCf.size()
        ||!raw.cellAlive[root]||raw.cellRank[root]!=rank
        ||std::size_t(parent)>=sourceParent.size()||sourceParent[parent]!=root
        ||assigned.count(root)||!halo.parents.count(root))
        return fail("outside-whole-promotion-raw-origin-or-halo");
    if(failure.failedBasePointCount!=m.points.size()
        ||failure.failedStagedPointCount!=m.points.size()+staged.births.size()
        ||staged.birthPlanes.size()!=staged.births.size()
        ||failure.failedStagedPointCount>=std::size_t(std::numeric_limits<label>::max()))
        return fail("outside-whole-promotion-point-domain");
    for(const auto& cell:staged.cells)
    {
        if(!work())return fail("outside-whole-promotion-work");
        for(std::size_t i=0;i<S::partCount(cell);++i)
            if(S::partCell(cell,i)==parent)return fail("outside-whole-promotion-is-volume-part");
    }
    const auto shellIt=shells.find(parent);
    if(shellIt==shells.end()||shellIt->second.size()<6||shellIt->second.size()>64)
        return fail("outside-whole-promotion-missing-complete-shell");
    const auto& shell=shellIt->second;
    for(std::size_t k=0;k<2;++k)
    {
        if(!work())return fail("outside-whole-promotion-work");
        const auto& face=failure.failedFaces[k];
        const label key=k?failure.failedPair.otherFace:failure.failedPair.face;
        const auto at=std::find_if(shell.begin(),shell.end(),
            [&](const S::Row& row){return row.native==face.proposedNativeFace;});
        if(face.kind!=P::FailedFaceKind::SourceFacet||face.stockKey!=key||key<0
            ||face.proposedNativeFace!=key||at==shell.end()||face.sourceFace!=at->source
            ||face.patch!=at->patch||face.zone!=at->zone||face.ring!=at->ring
            ||face.recipeParent!=-1||face.recipeSide!=-1
            ||face.owner.kind!=P::FailedEndpointKind::StagedCell
            ||face.owner.stagedParent!=at->owner
            ||(at->neighbour>=0&&(face.neighbour.kind!=P::FailedEndpointKind::StagedCell
                ||face.neighbour.stagedParent!=at->neighbour))
            ||(at->neighbour<0&&face.neighbour.kind!=P::FailedEndpointKind::Unknown))
            return fail("outside-whole-promotion-actual-native-whole-blocker");
        out.stockFaces[k]=key;
    }
    if(out.stockFaces[0]==out.stockFaces[1])return fail("outside-whole-promotion-identical-blockers");
    std::set<label> rawSources;
    if(rawCf[root].size()<6||rawCf[root].size()>64)
        return fail("outside-whole-promotion-raw-CF-bound");
    for(label f:rawCf[root])
    {
        if(!work()||f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
            ||(raw.owner[f]!=root&&raw.neighbour[f]!=root)||!rawSources.insert(f).second)
            return fail("outside-whole-promotion-raw-CF-identity");
    }
    std::map<label,const S::Row*> stagedRows;
    for(const auto& row:staged.rows)if(row.owner==parent||row.neighbour==parent)
        if(!work()||!stagedRows.emplace(row.native,&row).second)
            return fail("outside-whole-promotion-staged-row");
    std::set<label> nativeRows,representedSources;std::set<std::size_t> incomingBirths;
    for(const auto& row:shell)
    {
        if(!work()||row.native<0||row.source<0||!nativeRows.insert(row.native).second
            ||!rawSources.count(row.source)||(row.owner==parent)==(row.neighbour==parent)
            ||row.owner<0||row.neighbour<-1||row.owner==row.neighbour
            ||row.patch!=raw.patch[row.source]||row.zone!=raw.zone[row.source]
            ||row.ring.size()<3||row.ring.size()>64)
            return fail("outside-whole-promotion-full-current-source-tuple");
        representedSources.insert(row.source);
        const auto changed=stagedRows.find(row.native);
        if(changed!=stagedRows.end())
        {
            const auto& a=*changed->second;
            if(a.source!=row.source||a.owner!=row.owner||a.neighbour!=row.neighbour
                ||a.patch!=row.patch||a.zone!=row.zone||a.ring!=row.ring)
                return fail("outside-whole-promotion-restored-row-preimage");
        }
        else if(std::size_t(row.native)>=m.faces.size()||!m.faceAlive[row.native]
            ||std::size_t(row.native)>=currentSource.size()||currentSource[row.native]!=row.source
            ||m.owner[row.native]!=row.owner||m.neighbour[row.native]!=row.neighbour
            ||m.patch[row.native]!=row.patch||m.zone[row.native]!=row.zone||m.faces[row.native]!=row.ring)
            return fail("outside-whole-promotion-current-row-preimage");
        std::set<label> unique;
        for(label id:row.ring)
        {
            if(!work()||id<0||std::size_t(id)>=failure.failedStagedPointCount
                ||!unique.insert(id).second||!S::finite(S::coordinate(m,staged,id)))
                return fail("outside-whole-promotion-full-current-point-domain");
            if(std::size_t(id)<failure.failedBasePointCount)continue;
            const auto group=sourceGroups.find(row.source);
            if(group==sourceGroups.end()
                ||std::count(group->second.begin(),group->second.end(),row.native)!=1)
                return fail("outside-whole-promotion-incoming-source-cohort");
            incomingBirths.insert(std::size_t(id)-failure.failedBasePointCount);
        }
    }
    if(representedSources!=rawSources)return fail("outside-whole-promotion-complete-source-cohorts");
    if(incomingBirths.empty())return fail("outside-whole-promotion-no-actual-incoming-birth");
    for(std::size_t i:incomingBirths)
    {
        if(!work()||i>=staged.births.size()||i>=staged.birthPlanes.size())
            return fail("outside-whole-promotion-incoming-birth-domain");
        const auto& birth=staged.births[i];const auto& plane=staged.birthPlanes[i];
        if(birth.first<0||birth.second<0||birth.first==birth.second
            ||std::size_t(birth.first)>=failure.failedBasePointCount+i
            ||std::size_t(birth.second)>=failure.failedBasePointCount+i
            ||!S::finite(birth.position)||plane.first<0||plane.first>2||!std::isfinite(plane.second))
            return fail("outside-whole-promotion-incoming-producing-plane");
        BirthKind origin=BirthKind::ActualStagedVolumeBirth;
        for(const auto& intent:staged.surfaceEdgeBirths)
        {
            if(!work())return fail("outside-whole-promotion-work");
            if(intent.first!=birth.first||intent.second!=birth.second||intent.plane!=plane
                ||F::pointBits(birth.position)!=intent.birthBits)continue;
            if(intent.source<0||std::size_t(intent.source)>=raw.faces.size()||!raw.faceAlive[intent.source]
                ||intent.first<0||intent.second<0||intent.first>=intent.second
                ||std::size_t(intent.second)>=raw.points.size()
                ||raw.faces[intent.source].size()<3||raw.faces[intent.source].size()>64
                ||F::pointBits(raw.points[intent.first])!=intent.firstBits
                ||F::pointBits(raw.points[intent.second])!=intent.secondBits)
                return fail("outside-whole-promotion-exact-surface-domain");
            bool onSource=false;
            for(std::size_t j=0;j<raw.faces[intent.source].size();++j)
            {
                if(!work())return fail("outside-whole-promotion-work");
                const auto& ring=raw.faces[intent.source];
                const S::Edge edge=std::minmax(ring[j],ring[(j+1)%ring.size()]);
                onSource=onSource||edge==S::Edge(intent.first,intent.second);
            }
            S::Birth exact;
            if(!onSource||!S::exactSurfaceEdgeBirth(raw,intent.first,intent.second,plane,available,exact)
                ||exact.first!=birth.first||exact.second!=birth.second
                ||F::pointBits(exact.position)!=intent.birthBits)
                return fail("outside-whole-promotion-exact-surface-preimage");
            origin=BirthKind::ExactSurfaceEdge;break;
        }
        // Compound appends unmatched volume-clipping births only after the
        // exact surface births. The literal restored Plan and producing plane
        // remain the identity source; no hypothetical plane or halo product.
        if(origin==BirthKind::ActualStagedVolumeBirth&&staged.cells.empty())
            return fail("outside-whole-promotion-no-actual-volume-birth");
        out.actualIncomingBirths.push_back({label(i),birth.first,birth.second,plane,origin});
    }
    out.kind=Kind::ActualSurfaceWholeFC;out.rawIdentity=static_cast<const void*>(&raw);
    out.rawPoints=raw.points.size();out.rawFaces=raw.faces.size();out.rawCells=raw.nCells;
    out.rawRoot=root;out.currentMother=parent;out.nativeWholeChild=failure.failedChild;
    out.basePoints=failure.failedBasePointCount;out.stagedPoints=failure.failedStagedPointCount;
    out.rawSources.assign(rawSources.begin(),rawSources.end());out.complete=true;
    if(why)*why="actual-surface-native-whole-FC-rectilinear-proposal";
    return true;
}
// Fixed, failure-independent observation only. Never read by any permission,
// quality, proposal or publication decision. Sampling: first16 of each kind.
struct Observation
{
    enum Kind : unsigned char
    {Select=0,Current=1,InitialFactory=2,Retain=3,Recorder=4,Augment=5,Commit=6,AugmentFactory=7};
    static constexpr std::size_t kindCount=8,quota=16,capacity=128,planeCap=8,hitCap=8;
    struct Frame
    {
        label jobIndex=-1,jobRank=-1,jobParent=-1,mode=-1,attempt=-1,round=0,localSeed=-1;
        bool localSeedKnown=false;
    };
    struct PlaneBits {label axis=-1;std::uint64_t bits=0;};
    struct Hit
    {
        label root=-1,source=-1,owner=-1,neighbour=-1,a=-1,b=-1,axis=-1;
        std::uint64_t bits=0;
    };
    struct Event
    {
        Frame frame;
        label root=-1,mother=-1,code=0,context=0;
        bool called=false,returned=false,result=false,complete=false,decodeKnown=false,decoded=false;
        std::size_t count=0,auxCount=0,planeCount=0,hitCount=0;
        bool whyTruncated=false,planesTruncated=false,hitsTruncated=false;
        std::array<char,96> why{};
        std::array<PlaneBits,planeCap> planes{};
        std::array<Hit,hitCap> hits{};
    };
    Frame live;
    label context=0;
    std::array<std::uint64_t,kindCount> calls{},stored{},omitted{},skipped{};
    std::array<Event,capacity> events{};
    bool detailTruncated=false,counterSaturated=false;
    static void increment(std::uint64_t& x,bool& saturated) noexcept
    {if(x==std::numeric_limits<std::uint64_t>::max())saturated=true;else ++x;}
    void begin(label index,label rank,label parent,label mode,label attempt) noexcept
    {live=Frame();live.jobIndex=index;live.jobRank=rank;live.jobParent=parent;live.mode=mode;live.attempt=attempt;context=0;}
    void nextRound() noexcept {++live.round;live.localSeedKnown=false;live.localSeed=-1;}
    void localSeed(label seed) noexcept {live.localSeed=seed;live.localSeedKnown=seed>=0;}
    Event* event(Kind kind,bool detail=true) noexcept
    {
        const std::size_t k=std::size_t(kind);increment(calls[k],counterSaturated);
        if(!detail){increment(skipped[k],counterSaturated);return nullptr;}
        if(stored[k]>=quota){increment(omitted[k],counterSaturated);return nullptr;}
        Event& out=events[k*quota+std::size_t(stored[k])];++stored[k];
        out.frame=live;out.context=context;return &out;
    }
    void reason(Event* out,const char* text) noexcept
    {
        if(!out)return;if(!text)text="";std::size_t i=0;
        for(;i+1<out->why.size()&&text[i];++i)out->why[i]=text[i];
        out->why[i]=0;out->whyTruncated=text[i]!=0;
        detailTruncated=detailTruncated||out->whyTruncated;
    }
    void planes(Event* out,const std::vector<Plane>& values) noexcept
    {
        if(!out)return;out->planeCount=values.size();out->planesTruncated=values.size()>planeCap;
        for(std::size_t i=0;i<values.size()&&i<planeCap;++i)
        {
            out->planes[i].axis=values[i].first;
            std::memcpy(&out->planes[i].bits,&values[i].second,sizeof(std::uint64_t));
        }
        detailTruncated=detailTruncated||out->planesTruncated;
    }
    void recorder(const M::Recorder& input,std::size_t registered,bool closureReady) noexcept
    {
        Event* out=event(Recorder,registered||!input.hits.empty()||!input.complete);
        if(!out)return;out->called=true;out->returned=true;out->result=closureReady;
        out->complete=input.complete;out->count=input.hits.size();out->auxCount=registered;
        reason(out,input.reason);out->hitCount=input.hits.size();out->hitsTruncated=input.hits.size()>hitCap;
        for(std::size_t i=0;i<input.hits.size()&&i<hitCap;++i)
        {
            const auto& hit=input.hits[i];auto& copy=out->hits[i];
            copy.root=hit.root;copy.source=hit.sourceFace;copy.owner=hit.owner;copy.neighbour=hit.neighbour;
            copy.a=hit.a;copy.b=hit.b;copy.axis=hit.plane.first;
            std::memcpy(&copy.bits,&hit.plane.second,sizeof(std::uint64_t));
        }
        detailTruncated=detailTruncated||out->hitsTruncated;
    }
    bool complete()const noexcept
    {if(detailTruncated||counterSaturated)return false;for(auto x:omitted)if(x)return false;return true;}
    template<class Stream> void write(Stream& out,label rank)const
    {
        static const char* names[kindCount]={"select","current","initial-factory","retain","recorder","augment","commit","augment-factory"};
        static const char hex[]="0123456789abcdef";
        out<<"{\"schema\":\"outside-promotion-original-results-observation/v1\",\"rank\":"<<rank
            <<",\"observationOnly\":true,\"authorityAdded\":false,\"complete\":"<<(complete()?"true":"false")
            <<",\"kindQuota\":16,\"eventCapacity\":128,\"planeCapacity\":8,\"hitCapacity\":8,\"detailTruncated\":"
            <<(detailTruncated?"true":"false")<<",\"counterSaturated\":"<<(counterSaturated?"true":"false")
            <<",\"counters\":[";
        for(std::size_t k=0;k<kindCount;++k)
        {if(k)out<<',';out<<"{\"kind\":\""<<names[k]<<"\",\"calls\":"<<calls[k]<<",\"stored\":"<<stored[k]
            <<",\"omitted\":"<<omitted[k]<<",\"skipped\":"<<skipped[k]<<'}';}
        out<<"],\"events\":[";bool first=true;
        for(std::size_t k=0;k<kindCount;++k)for(std::size_t i=0;i<stored[k];++i)
        {
            const auto& e=events[k*quota+i];if(!first)out<<',';first=false;
            out<<"{\"kind\":\""<<names[k]<<"\",\"jobIndex\":"<<e.frame.jobIndex<<",\"jobRank\":"<<e.frame.jobRank
                <<",\"jobParent\":"<<e.frame.jobParent<<",\"mode\":"<<e.frame.mode<<",\"attempt\":"<<e.frame.attempt
                <<",\"round\":"<<e.frame.round<<",\"localSeedKnown\":"<<(e.frame.localSeedKnown?"true":"false")
                <<",\"localSeed\":"<<e.frame.localSeed<<",\"root\":"<<e.root<<",\"mother\":"<<e.mother
                <<",\"context\":"<<e.context<<",\"code\":"<<e.code<<",\"called\":"<<(e.called?"true":"false")
                <<",\"returned\":"<<(e.returned?"true":"false")<<",\"result\":"<<(e.result?"true":"false")
                <<",\"complete\":"<<(e.complete?"true":"false")<<",\"decodeKnown\":"<<(e.decodeKnown?"true":"false")
                <<",\"decoded\":"<<(e.decoded?"true":"false")<<",\"count\":"<<e.count<<",\"auxCount\":"<<e.auxCount
                <<",\"whyTruncated\":"<<(e.whyTruncated?"true":"false")<<",\"whyUtf8Hex\":\"";
            for(char c:e.why){if(!c)break;const auto b=static_cast<unsigned char>(c);out<<hex[b>>4]<<hex[b&15];}
            out<<"\",\"planeCount\":"<<e.planeCount<<",\"planesTruncated\":"<<(e.planesTruncated?"true":"false")<<",\"planes\":[";
            for(std::size_t p=0;p<e.planeCount&&p<planeCap;++p)
            {if(p)out<<',';out<<'['<<e.planes[p].axis<<",\""<<e.planes[p].bits<<"\"]";}
            out<<"],\"hitCount\":"<<e.hitCount<<",\"hitsTruncated\":"<<(e.hitsTruncated?"true":"false")<<",\"hits\":[";
            for(std::size_t h=0;h<e.hitCount&&h<hitCap;++h)
            {
                if(h)out<<',';const auto& x=e.hits[h];
                out<<"{\"root\":"<<x.root<<",\"source\":"<<x.source<<",\"owner\":"<<x.owner<<",\"neighbour\":"<<x.neighbour
                    <<",\"a\":"<<x.a<<",\"b\":"<<x.b<<",\"axis\":"<<x.axis<<",\"coordinateBits\":\""<<x.bits<<"\"}";
            }
            out<<"]}";
        }
        out<<"]}\n";
    }
};
static_assert(sizeof(Observation)<=131072,"bounded outside observation POD <=128KiB");
class Cache
{
    std::map<label,Evidence> entries_;
public:
    std::size_t registeredCount()const noexcept {return entries_.size();}
    template<class Current>
    bool interested(label root,Current current)const
    {
        const auto at=entries_.find(root);
        return at!=entries_.end()&&current(at->second);
    }
    bool retain(bool decoded,const Evidence& candidate,const std::function<bool()>& available)
    {
        const bool typed=(candidate.kind==Kind::ActualOutsideRecipeSideFC
                &&candidate.plannedChild>=0&&candidate.nativeWholeChild==-1
                &&candidate.geometryKind==GeometryKind::RectilinearCompound)
            ||(candidate.kind==Kind::ActualSurfaceWholeFC
                &&candidate.plannedChild==-1&&candidate.nativeWholeChild==0
                &&(candidate.geometryKind==GeometryKind::RectilinearCompound
                    ||candidate.geometryKind==GeometryKind::AxisBoxBoundaryKnots));
        if(!decoded||!candidate.complete||!typed
            ||candidate.rawRoot<0||candidate.currentMother<0
            ||candidate.initialPlanes.empty()||candidate.initialPlanes.size()>64
            ||(available&&!available()))return false;
        // At most one value-owned qualified suggestion per original raw root.
        // It never contains a saved peer/Stock/Source/quality ready flag.
        Evidence value=candidate;
        const auto at=entries_.find(candidate.rawRoot);
        if(at==entries_.end()){entries_.emplace(candidate.rawRoot,std::move(value));return true;}
        std::swap(at->second,value);return false;
    }
    template<class Current,class Factory>
    bool augment(const M::Recorder& metadata,const RootPlanes& reached,RootPlanes& pending,
        const std::function<bool()>& available,Current current,Factory factory,Observation* observation=nullptr)const
    {
        auto* observed=observation?observation->event(Observation::Augment,
            !entries_.empty()||!metadata.hits.empty()||!metadata.complete):nullptr;
        if(observed){observed->called=true;observed->complete=metadata.complete;
            observed->count=metadata.hits.size();observed->auxCount=entries_.size();
            observation->reason(observed,metadata.reason);}
        const auto observedReturn=[&](bool value,label code)
        {if(observed){observed->returned=true;observed->result=value;observed->code=code;}return value;};
        if(!metadata.complete)return observedReturn(false,1);
        auto work=[&](){return !available||available();};
        RootPlanes incoming;std::set<label> invalid;
        try
        {
            for(const auto& hit:metadata.hits)
            {
                if(!work())return observedReturn(false,2);
                const auto entry=entries_.find(hit.root);
                if(entry==entries_.end()||reached.count(hit.root)||!current(entry->second))continue;
                if((hit.owner!=hit.root&&hit.neighbour!=hit.root)||hit.a<0||hit.a>=hit.b
                    ||hit.sourceFace<0||!std::binary_search(entry->second.rawSources.begin(),
                        entry->second.rawSources.end(),hit.sourceFace)
                    ||hit.plane.first<0||hit.plane.first>2||!std::isfinite(hit.plane.second))
                {invalid.insert(hit.root);continue;}
                incoming[hit.root].push_back(hit.plane);
            }
        }
        catch(...){return observedReturn(false,3);} // No pending request has changed.
        bool added=false;
        for(auto& root:incoming)try
        {
            if(!work())return observedReturn(added,4);
            if(invalid.count(root.first))continue;
            const auto entry=entries_.find(root.first);
            if(entry==entries_.end()||!current(entry->second))continue;
            const auto before=pending.find(root.first);
            if(before!=pending.end())root.second.insert(root.second.end(),before->second.begin(),before->second.end());
            std::sort(root.second.begin(),root.second.end());
            root.second.erase(std::unique(root.second.begin(),root.second.end()),root.second.end());
            std::vector<Plane> proposal;std::string why;
            auto* factoryObserved=observation?observation->event(Observation::AugmentFactory):nullptr;
            if(factoryObserved){factoryObserved->called=true;factoryObserved->root=root.first;
                factoryObserved->auxCount=root.second.size();}
            const bool factoryResult=factory(entry->second,root.second,proposal,&why);
            if(factoryObserved){factoryObserved->returned=true;factoryObserved->result=factoryResult;
                observation->reason(factoryObserved,why.c_str());observation->planes(factoryObserved,proposal);}
            if(!factoryResult||proposal.empty()||proposal.size()>64)continue;
            // The factory rebinds complete immutable CF and the actual union.
            // Build a complete local value before committing any request group.
            std::vector<Plane> combined=before==pending.end()?std::vector<Plane>():before->second;
            bool fresh=false;
            for(const auto& plane:proposal)
                if(std::find(combined.begin(),combined.end(),plane)==combined.end())
                {combined.push_back(plane);fresh=true;}
            if(!fresh)continue;
            std::sort(combined.begin(),combined.end());
            if(before==pending.end())pending.emplace(root.first,std::move(combined));
            else before->second.swap(combined);
            added=true; // Strong group commit; every actual gate runs next round.
            auto* committed=observation?observation->event(Observation::Commit):nullptr;
            if(committed){committed->called=true;committed->returned=true;committed->result=true;
                committed->root=root.first;observation->planes(committed,proposal);}
        }
        catch(...){} // Optional proposal loss never changes the original closure.
        return observedReturn(added,5);
    }
};
}}
#endif
