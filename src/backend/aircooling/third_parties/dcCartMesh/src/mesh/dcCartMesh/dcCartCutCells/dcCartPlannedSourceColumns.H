#ifndef dcCartPlannedSourceColumns_H
#define dcCartPlannedSourceColumns_H
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartRawUnionStockMetrics.H"
#include "dcCartStagedPlannedConeGeometry.H"
#include "dcCartNativeInterfaceGeometry.H"
#include <algorithm>
#include <array>
#include <map>
#include <set>
#include <tuple>
#include <cstdint>
#include <cstring>

namespace Foam { namespace dcCartPlannedSourceColumns
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace Q=dcCartRawUnionStockMetrics;
namespace NI=dcCartNativeInterfaceGeometry;
struct FacetRole
{
    Q::FacetQuality quality;
    label source=-1,parentOwner=-1,parentNeighbour=-1,patch=-1,zone=-1;
    point ownerCentre=point::zero,neighbourCentre=point::zero;
    scalar ownerVolume=0,neighbourVolume=0;bool internal=false;
    std::vector<point> ring;
    // Explicit original physical pieces of an actual composite carrier.
    std::vector<dcCartPostCarveCanonicalWallFan::CompositeConstituent> constituents;
    // Ordinary internal Source pieces; never a boundary-wall role.
    std::vector<S::Row> interfaceConstituents;
};
struct Proof
{
    std::map<label,std::vector<S::Row>> shells;
    std::map<label,Q::Cell> cells;
    std::map<label,FacetRole> facets;
    std::set<label> faces;
    bool ready=false;
};
// Preparation computes complete local child geometry before peer exchange.
// The final interface pass consumes typed real remote CC/V, then the caller
// still performs fresh source and uniform final publication readiness votes.
// Failure-only provenance. Stock keys, proposal-native slots and raw source
// identities are different domains; no field here is a source permission.
enum class FailedFaceKind : unsigned char {Unknown,SourceFacet,GeneratedCap,RecipeSide};
enum class FailedEndpointKind : unsigned char {Unknown,StagedCell,PlannedChild};
struct FailedEndpoint
{
    FailedEndpointKind kind=FailedEndpointKind::Unknown;
    label stockKey=-1,stagedParent=-1,plannedChild=-1,rawRoot=-1;
};
struct FailedFace
{
    FailedFaceKind kind=FailedFaceKind::Unknown;
    label stockKey=-1,proposedNativeFace=-1,sourceFace=-1;
    label recipeParent=-1,recipeSide=-1,recipeRawRoot=-1,patch=-1,zone=-1;
    label rawSourceOwner=-1,rawSourceNeighbour=-1;
    bool rawSourceKnown=false;
    // Source/cap rings use original+staged birth IDs. A RecipeSide may also
    // contain the recipe-only apex ID failedStagedPointCount; never a raw ID.
    std::vector<label> ring;
    FailedEndpoint owner,neighbour;
};
// Optional first-DAR failure observation, never a publication/source proof.
struct FailureGeometry
{
    enum class State : unsigned char {Unknown,Complete,BoundsExceeded,CaptureException,InvalidReference};
    State state=State::Unknown;
    label stockKey=-1,parent=-1,child=-1;
    bool planned=false,strictInput=false,requirePassingInput=false,repairedWhole=false;
    bool cellFinite=false,cellStrictGeometry=false,apexKnown=false;
    point centre=point::zero,apex=point::zero;
    scalar volume=0,determinant=0,aspect=0;
    // Face rings retain actual sparse stock point IDs; pointKeys binds them.
    std::vector<Q::Face> faces;
    std::vector<label> pointKeys,faceIds;
    std::vector<point> points,centres;
    std::vector<vector> areas;
    std::vector<unsigned char> selectedSf,coupled;
    std::vector<FailedFace> provenance;
};
// Failure-only actual future-cell geometry. Sparse stock point/cell keys,
// proposed native bases and recorded physical source handles are distinct domains.
// This fixed value-owned slot is not a Source/Point/peer/publication permission.
struct FirstFailedCellObservation
{
    enum class State : unsigned char {Unknown,Complete,BoundsExceeded,InvalidPoint,TextTruncated};
    struct Endpoint {label stockKey=-1,stagedParent=-1,plannedChild=-1;bool planned=false;};
    struct PointBits {label localStockPoint=-1;std::array<std::uint64_t,3> bits{};};
    struct Part {label proposedNativeBase=-1,sourceHandle=-1,beforeOwner=-1,beforeNeighbour=-1,patch=-1,zone=-1;
        std::size_t ringOffset=0,ringCount=0;};
    struct Face {label stockFace=-1,actualNativeBase=-1,patch=-1,zone=-1;
        Endpoint owner,neighbour;std::size_t ringOffset=0,ringCount=0,partOffset=0,partCount=0;
        bool composite=false,nativeInterface=false,metricKnown=false,metricFinite=false;
        std::array<std::uint64_t,3> areaBits{},centreBits{};};
    bool seen=false,planned=false,strictInput=false,requirePassingInput=false,repairedWhole=false,
        cellFinite=false,cellStrictGeometry=false,metricArraysComplete=false,apexKnown=false,reasonTruncated=false;
    State state=State::Unknown;
    label stockCell=-1,parent=-1,child=-1,blockingStockFace=-1,otherBlockingStockFace=-1,blockingStockPoint=-1;
    std::size_t basePointCount=0,stagedPointCount=0,faceCount=0,ringNodeCount=0,pointCount=0,
        partCount=0,sourceRingNodeCount=0;
    std::array<char,96> reason{};
    std::array<std::uint64_t,3> centreBits{},apexBits{};
    std::uint64_t volumeBits=0,determinantBits=0,aspectBits=0,blockingCosineBits=0;
    std::array<Face,32> faces{};std::array<label,128> ringNodes{};
    std::array<PointBits,128> points{};std::array<Part,64> parts{};
    std::array<label,256> sourceRingNodes{};
    static std::uint64_t bits(scalar v) noexcept
    {static_assert(sizeof(scalar)==sizeof(std::uint64_t),"binary64 observation only");std::uint64_t b=0;std::memcpy(&b,&v,sizeof(b));return b;}
    static std::array<std::uint64_t,3> bits(const point& p) noexcept
    {return {{bits(p.x()),bits(p.y()),bits(p.z())}};}
};
static_assert(sizeof(FirstFailedCellObservation)<=32768,"first failed cell fixed POD <=32KiB");
struct Prepared
{
    Proof proof;
    std::vector<point> points;
    std::map<label,std::vector<Q::Face>> rows;
    // Local stock keys are not published native cell IDs. Only plannedChildren
    // carries their explicit source-parent/child-ordinal identity.
    std::map<label,std::pair<label,label>> plannedChildren;
    // Appended with monotone stock keys: exactly the old map-filter order.
    std::map<label,std::vector<label>> parentChildren;
    std::map<label,S::Row> nativeRows;
    std::map<label,dcCartPostCarveCanonicalWallFan::CompositeRecord> composites;
    std::map<label,NI::Record> interfaces;
    // Preimages remain in nativeRows; these pieces have no future face slot.
    std::set<label> retiredInterfaceFaces;
    std::map<label,label> nativeOwners;
    std::set<label> nativeFaces;
    label failedParent=-1,failedChild=-1,failedNativeFace=-1,failedOtherNativeFace=-1;
    Q::BlockingFacePair failedPair;
    std::array<FailedFace,2> failedFaces;
    std::size_t failedBasePointCount=0,failedStagedPointCount=0;
    label failedRawRoot=-1;
    bool failedFacesCaptured=false;
    bool failedPlanned=false;
    // Failure-only scalar observation. Unknown unless the actual DAR veto ran.
    bool failedDarKnown=false;
    scalar failedDeterminant=0,failedAspect=0;
    bool geometryReady=false;
};
template<class Mesh,class Coupled>
bool prepareShells(const Mesh& m,const S::Plan& staged,
 const std::map<label,std::vector<S::Row>>& shells,Coupled coupled,
 const std::function<bool()>& available,Prepared& out,const char** why=nullptr)
{
    out=Prepared();auto fail=[&](const char* s){if(why)*why=s;return false;};
    if(!staged.ready||shells.empty())return fail("column-input");
    auto work=[&](){return !available||available();};Prepared draft;
    std::map<label,label> local;std::vector<point> points;
    std::map<label,std::vector<Q::Face>> rows;
    for(const auto& entry:shells)
    {
        if(!work())return fail("column-clock");
        for(const auto& row:entry.second)
        {
            if(row.owner!=entry.first&&row.neighbour!=entry.first)
                return fail("column-incidence");
            Q::Face f(row.native,row.owner,row.neighbour,row.patch,row.zone,{});
            for(label p:row.ring)
            {
                if(!work())return fail("column-clock");
                if(p<0||std::size_t(p)>=m.points.size()+staged.births.size())
                    return fail("column-point-index");
                auto found=local.find(p);
                if(found==local.end())
                {
                    const point& x=S::coordinate(m,staged,p);
                    if(!S::finite(x))return fail("column-point");
                    const label id=points.size();points.push_back(x);
                    found=local.emplace(p,id).first;
                }
                f.ring.push_back(found->second);
            }
            rows[entry.first].push_back(std::move(f));
        }
    }
    struct PointMesh {const std::vector<point>& points;} localMesh{points};
    S::Plan noBirths;
    const Q::FaceOF metric=[&](const std::vector<label>& ring,vector& sf,point& fc)
    {S::metric(localMesh,noBirths,ring,sf,fc);};
    const Q::CoupledTypePatch coupledPatch=[&](label patch){return coupled(patch);};
    for(const auto& entry:rows)
    {
        if(!work())return fail("column-clock");
        auto cell=Q::measure(entry.first,entry.second,points,metric,coupledPatch,available,true,true);
        if(!cell.finite||!cell.strictGeometry)return fail(cell.reason?cell.reason:"column-shell");
        if(cell.determinant<.001||cell.aspect>1000)return fail("column-D-AR");
        draft.proof.cells.emplace(entry.first,std::move(cell));
    }
    if(!work())return fail("column-clock");
    draft.proof.shells=shells;draft.points=std::move(points);draft.rows=std::move(rows);
    draft.geometryReady=true;out=std::move(draft);return true;
}
// Build the complete future local cells: actual split parts are single whole
// children; unchanged affected endpoints retain their actual staged cone recipe.
// Point and cell keys are sparse local stock coordinates, never native handles.
template<class Mesh,class Coupled>
bool prepareFuture(const Mesh& m,const S::Plan& staged,
 const std::map<label,std::vector<S::Row>>& physicalShells,
 const std::set<label>& wholeParts,
 const std::map<label,dcCartPlannedConeGeometry::Parent>& recipes,
 Coupled coupled,const std::function<bool()>& available,Prepared& out,
 const char** why=nullptr,bool strictGeometry=true,bool requirePassing=true,
 FailureGeometry* failureGeometry=nullptr,
 const std::function<bool(const dcCartPostCarveCanonicalWallFan::CompositeRecord&)>* compositeAuthority=nullptr,
 FirstFailedCellObservation* firstFailedCell=nullptr,
 const std::function<bool(const NI::Record&)>* interfaceAuthority=nullptr)
{
    if(failureGeometry)*failureGeometry=FailureGeometry();
    out=Prepared();label diagnosticParent=-1,diagnosticChild=-1,diagnosticFace=-1;
    bool diagnosticPlanned=false;label diagnosticOtherNativeFace=-1;
    Q::BlockingFacePair diagnosticPair;
    std::array<FailedFace,2> diagnosticFaces;
    std::size_t diagnosticStagedPointCount=0;
    bool diagnosticFacesCaptured=false;
    auto fail=[&](const char* s)
    {
        if(why)*why=s;out.failedParent=diagnosticParent;out.failedChild=diagnosticChild;
        out.failedNativeFace=diagnosticFace;out.failedPlanned=diagnosticPlanned;
        out.failedPair=diagnosticPair;out.failedOtherNativeFace=diagnosticOtherNativeFace;
        out.failedFaces=std::move(diagnosticFaces);
        out.failedBasePointCount=m.points.size();out.failedStagedPointCount=diagnosticStagedPointCount;
        out.failedFacesCaptured=diagnosticFacesCaptured;return false;
    };
    auto work=[&](){return !available||available();};
    if(!staged.ready||physicalShells.empty()||(wholeParts.empty()&&recipes.empty()))return fail("column-future-input");
    Prepared draft;
    const std::size_t stagedPointCount=m.points.size()+staged.births.size();
    diagnosticStagedPointCount=stagedPointCount;
    if(stagedPointCount>=std::size_t(std::numeric_limits<label>::max()))
        return fail("column-future-point-count");
    const label syntheticApex=stagedPointCount;
    std::map<std::pair<label,label>,label> childKeys;
    label nextCell=m.nCells;
    // Stock-only child keys follow every actual staged slot, including new
    // split parts that themselves now have genuine cone recipes.
    for(const auto& shell:physicalShells)
    {
        if(!work())return fail("column-clock");
        if(shell.first<0||shell.first==std::numeric_limits<label>::max())
            return fail("column-future-cell-count");
        nextCell=std::max(nextCell,label(shell.first+1));
    }
    for(label c:wholeParts)
    {
        if(c<0||c==std::numeric_limits<label>::max())return fail("column-future-cell");
        nextCell=std::max(nextCell,label(c+1));
        const auto shell=physicalShells.find(c);
        if(shell==physicalShells.end())return fail("column-missing-whole-shell");
        draft.proof.shells.emplace(c,shell->second);
    }
    for(const auto& entry:recipes)
    {
        if(!work())return fail("column-clock");
        const auto& recipe=entry.second;
        if(wholeParts.count(entry.first)||!recipe.known||recipe.children.empty()
            ||recipe.geometry.births.size()!=1
            ||recipe.baseFaces.size()!=recipe.baseToChild.size())
            return fail("column-future-recipe");
        for(label ordinal=0;ordinal<label(recipe.children.size());++ordinal)
        {
            if(nextCell==std::numeric_limits<label>::max())return fail("column-future-cell-count");
            const label key=nextCell++;
            childKeys.emplace(std::make_pair(entry.first,ordinal),key);
            draft.plannedChildren.emplace(key,std::make_pair(entry.first,ordinal));
            draft.parentChildren[entry.first].push_back(key);
        }
    }
    auto endpoint=[&](label parent,label face,label& key)
    {
        const auto recipe=recipes.find(parent);
        if(recipe==recipes.end()){key=parent;return parent>=0;}
        const auto& plan=recipe->second;
        const auto at=std::find(plan.baseFaces.begin(),plan.baseFaces.end(),face);
        if(at==plan.baseFaces.end())return false;
        const label ordinal=plan.baseToChild[std::distance(plan.baseFaces.begin(),at)];
        const auto found=childKeys.find({parent,ordinal});
        if(found==childKeys.end())return false;
        key=found->second;return true;
    };
    struct CompositePointView
    {
        const Mesh& mesh;const S::Plan& plan;
        std::size_t size()const{return mesh.points.size()+plan.births.size();}
        const point& operator[](label id)const{return S::coordinate(mesh,plan,id);}
    } compositePoints{m,staged};
    // Existing scalar rows remain the immutable physical/source inputs. A
    // composite shell can only replace their geometric representation after
    // a real caller Source verifier and complete exact record validation.
    for(const auto& entry:recipes)for(const auto& item:entry.second.composites)
    {
        const auto& r=item.second;
        if(!work()||item.first!=r.carrier||r.parent!=entry.first||r.child<0
            ||std::size_t(r.child)>=entry.second.children.size()
            ||!dcCartPlannedConeGeometry::physicalCompositeGeometry(r,compositePoints,available)
            ||!draft.composites.emplace(item.first,r).second)
            return fail("column-composite-real-source-or-geometry");
    }
    // Both genuine current mothers carry one identical ordinary-interface plan.
    // Original source endpoints are distinct from these current recipe endpoints;
    // their authoritative binding is checked by the caller's real Source verifier.
    for(const auto& entry:recipes)for(const auto& item:entry.second.interfaces)
    {
        if(!work())return fail("column-clock");
        const auto& r=item.second;
        if(item.first!=r.carrier||(entry.first!=r.owner&&entry.first!=r.neighbour)
            ||r.originalOwner<0||r.originalNeighbour<0||r.originalOwner==r.originalNeighbour
            ||r.endpointChild.size()!=2||!r.endpointChild.count(r.owner)||!r.endpointChild.count(r.neighbour)
            ||draft.composites.count(r.carrier)
            ||!NI::geometry(r,compositePoints,available))
            return fail("column-interface-real-source-or-geometry");
        for(label parent:std::array<label,2>{{r.owner,r.neighbour}})
        {
            const auto endpointRecipe=recipes.find(parent);
            if(endpointRecipe==recipes.end()||wholeParts.count(parent))
                return fail("column-interface-missing-endpoint-recipe");
            if(!dcCartPlannedConeGeometry::nativeInterfaceRecipe(parent,endpointRecipe->second,r,available))
                return fail("column-interface-endpoint-recipe-binding");
        }
        const auto inserted=draft.interfaces.emplace(r.carrier,r);
        if(!inserted.second&&!NI::same(inserted.first->second,r))
            return fail("column-interface-shared-record-mismatch");
    }
    label nextFace=0;
    for(const auto& cell:physicalShells)for(const auto& row:cell.second)
    {
        if(!work())return fail("column-clock");
        if(row.native<0||row.native==std::numeric_limits<label>::max())
            return fail("column-future-face-count");
        nextFace=std::max(nextFace,label(row.native+1));
        const auto inserted=draft.nativeRows.emplace(row.native,row);
        if(!inserted.second)
        {
            const auto& old=inserted.first->second;
            if(old.source!=row.source||old.owner!=row.owner||old.neighbour!=row.neighbour
                ||old.patch!=row.patch||old.zone!=row.zone||old.ring!=row.ring)
                return fail("column-future-shared-row");
        }
        draft.nativeFaces.insert(row.native);
    }
    // Capture all old rows first. Suppress a retired part only after complete
    // literal coverage on BOTH endpoint shells and both recipe maps is proved.
    std::set<label> interfaceParts;
    for(const auto& item:draft.interfaces)
    {
        const auto& r=item.second;
        for(const auto& old:r.constituents)
        {
            if(!work())return fail("column-clock");
            const auto actual=draft.nativeRows.find(old.native);
            if(actual==draft.nativeRows.end()||!interfaceParts.insert(old.native).second
                ||draft.composites.count(old.native))
                return fail("column-interface-overlapping-or-missing-native-part");
            const auto& row=actual->second;
            if(row.source!=old.source||row.owner!=old.owner||row.neighbour!=old.neighbour
                ||row.patch!=old.patch||row.zone!=old.zone||row.ring!=old.ring)
                return fail("column-interface-current-native-row-mismatch");
            for(label parent:std::array<label,2>{{r.owner,r.neighbour}})
            {
                const auto shell=physicalShells.find(parent);
                if(shell==physicalShells.end())return fail("column-interface-missing-endpoint-shell");
                std::size_t uses=0;
                for(const auto& held:shell->second)if(held.native==old.native)
                {
                    if(!work())return fail("column-clock");
                    if(held.source!=old.source||held.owner!=old.owner||held.neighbour!=old.neighbour
                        ||held.patch!=old.patch||held.zone!=old.zone||held.ring!=old.ring)
                        return fail("column-interface-endpoint-shell-preimage");
                    ++uses;
                }
                if(uses!=1)return fail("column-interface-endpoint-shell-exactly-once");
            }
            if(old.native!=r.carrier)
            {draft.retiredInterfaceFaces.insert(old.native);draft.nativeFaces.erase(old.native);}
        }
    }
    std::map<std::pair<label,label>,label> pointKeys;
    auto pointId=[&](label parent,label p,label& local)
    {
        if(!work()||p<0)return false;
        const bool apex=std::size_t(p)==stagedPointCount;
        const auto key=std::make_pair(apex?parent:label(-1),apex?label(-1):p);
        auto found=pointKeys.find(key);
        if(found==pointKeys.end())
        {
            point position;
            if(apex)
            {
                const auto recipe=recipes.find(parent);
                if(recipe==recipes.end()||recipe->second.geometry.births.size()!=1)return false;
                position=recipe->second.geometry.births.front().position;
            }
            else
            {
                if(std::size_t(p)>=stagedPointCount)return false;
                position=S::coordinate(m,staged,p);
            }
            if(!S::finite(position)||draft.points.size()>=std::size_t(std::numeric_limits<label>::max()))
                return false;
            const label id=draft.points.size();draft.points.push_back(position);
            found=pointKeys.emplace(key,id).first;
        }
        local=found->second;return true;
    };
    auto physicalFace=[&](label parent,const S::Row& row,Q::Face& face)
    {
        face=Q::Face(row.native,-1,-1,row.patch,row.zone,{});
        if(!endpoint(row.owner,row.native,face.owner)
            ||(row.neighbour>=0&&!endpoint(row.neighbour,row.native,face.neighbour)))return false;
        for(label p:row.ring)
        {
            label id=-1;if(!pointId(parent,p,id))return false;face.ring.push_back(id);
        }
        draft.nativeOwners[row.native]=face.owner;return true;
    };
    for(label cell:wholeParts)for(const auto& row:physicalShells.at(cell))
    {
        Q::Face face;if(!physicalFace(cell,row,face))return fail("column-future-whole-face");
        if(face.owner!=cell&&face.neighbour!=cell)return fail("column-future-whole-incidence");
        draft.rows[cell].push_back(std::move(face));
    }
    const label firstSideFaceKey=nextFace;
    std::map<label,std::set<label>> interfaceUses;
    for(const auto& entry:recipes)
    {
        const label parent=entry.first;const auto& recipe=entry.second;
        std::map<std::vector<label>,Q::Face> sides;
        for(const auto& side:recipe.sides)
        {
            if(!work())return fail("column-clock");
            if(nextFace==std::numeric_limits<label>::max())return fail("column-future-side-count");
            const auto own=childKeys.find({parent,side.owner}),nei=childKeys.find({parent,side.neighbour});
            if(own==childKeys.end()||nei==childKeys.end())return fail("column-future-side-child");
            Q::Face face(nextFace++,own->second,nei->second,-1,-1,{});
            for(label p:side.ring)
            {
                label id=-1;if(!pointId(parent,p,id))return fail("column-future-side-point");
                face.ring.push_back(id);
            }
            if(!sides.emplace(side.ring,std::move(face)).second)return fail("column-future-side-duplicate");
        }
        for(label ordinal=0;ordinal<label(recipe.children.size());++ordinal)
        {
            const label cell=childKeys.at({parent,ordinal});
            for(const auto& piece:recipe.children[ordinal])
            {
                if(!work())return fail("column-clock");
                Q::Face face;
                if(piece.nativeBase>=0)
                {
                    const auto row=draft.nativeRows.find(piece.nativeBase);
                    const auto composite=draft.composites.find(piece.nativeBase);
                    const auto interface=draft.interfaces.find(piece.nativeBase);
                    if(draft.retiredInterfaceFaces.count(piece.nativeBase))
                        return fail("column-interface-retired-native-piece-in-future");
                    if(interface!=draft.interfaces.end())
                    {
                        const auto& r=interface->second;
                        const auto expected=r.endpointChild.find(parent);
                        if(composite!=draft.composites.end()||row==draft.nativeRows.end()
                            ||expected==r.endpointChild.end()||expected->second!=ordinal
                            ||piece.ring!=r.outline||piece.forward!=(r.owner==parent)||!piece.selected
                            ||!interfaceUses[r.carrier].insert(parent).second)
                            return fail("column-interface-child-role");
                        S::Row actual=row->second;actual.ring=r.outline;
                        if(!physicalFace(parent,actual,face))return fail("column-interface-real-carrier");
                        const auto owner=childKeys.find({r.owner,r.endpointChild.at(r.owner)});
                        const auto neighbour=childKeys.find({r.neighbour,r.endpointChild.at(r.neighbour)});
                        if(owner==childKeys.end()||neighbour==childKeys.end()
                            ||face.owner!=owner->second||face.neighbour!=neighbour->second)
                            return fail("column-interface-real-future-endpoints");
                    }
                    else if(composite==draft.composites.end())
                    {
                        if(row==draft.nativeRows.end()||row->second.ring!=piece.ring
                            ||(row->second.owner==parent)!=piece.forward
                            ||!physicalFace(parent,row->second,face))
                            return fail("column-future-base-recipe");
                    }
                    else
                    {
                        const auto& r=composite->second;
                        if(r.parent!=parent||r.child!=ordinal||!piece.forward||piece.selected
                            ||piece.ring!=r.outline||row==draft.nativeRows.end())
                            return fail("column-composite-child-role");
                        for(const auto& old:r.constituents)
                        {
                            const auto actual=draft.nativeRows.find(old.native);
                            const auto base=std::find(recipe.baseFaces.begin(),recipe.baseFaces.end(),old.native);
                            if(actual==draft.nativeRows.end()||actual->second.source!=old.source
                                ||actual->second.owner!=old.owner||actual->second.neighbour!=old.neighbour
                                ||actual->second.patch!=old.patch||actual->second.zone!=old.zone
                                ||actual->second.ring!=old.ring||base==recipe.baseFaces.end()
                                ||std::count(recipe.baseFaces.begin(),recipe.baseFaces.end(),old.native)!=1
                                ||recipe.baseToChild[std::distance(recipe.baseFaces.begin(),base)]!=ordinal)
                                return fail("column-composite-native-base-exactly-once");
                        }
                        S::Row actual=row->second;actual.ring=r.outline;
                        if(!physicalFace(parent,actual,face))return fail("column-composite-real-carrier");
                        for(const auto& old:r.constituents)if(old.native!=r.carrier)draft.nativeFaces.erase(old.native);
                    }
                }
                else
                {
                    const auto side=sides.find(piece.ring);
                    if(side==sides.end())return fail("column-future-side-recipe");
                    face=side->second;
                    if((face.owner==cell)!=piece.forward)return fail("column-future-side-orientation");
                }
                if(face.owner!=cell&&face.neighbour!=cell)return fail("column-future-child-incidence");
                draft.rows[cell].push_back(std::move(face));
            }
        }
    }
    for(const auto& item:draft.interfaces)
    {
        if(!work())return fail("column-clock");
        const auto uses=interfaceUses.find(item.first);
        if(uses==interfaceUses.end()||uses->second.size()!=2
            ||!uses->second.count(item.second.owner)||!uses->second.count(item.second.neighbour))
            return fail("column-interface-complete-two-endpoint-future");
    }
    struct PointMesh {const std::vector<point>& points;} localMesh{draft.points};
    S::Plan noBirths;
    const Q::FaceOF metric=[&](const std::vector<label>& ring,vector& sf,point& fc)
        {S::metric(localMesh,noBirths,ring,sf,fc);};
    const Q::CoupledTypePatch coupledPatch=[&](label patch){return coupled(patch);};
    auto failureEndpoint=[&](label key,label actualParent)->FailedEndpoint
    {
        FailedEndpoint out;out.stockKey=key;
        const auto planned=draft.plannedChildren.find(key);
        if(planned!=draft.plannedChildren.end())
        {
            out.kind=FailedEndpointKind::PlannedChild;
            out.stagedParent=planned->second.first;out.plannedChild=planned->second.second;
        }
        else if(key>=0&&actualParent>=0)
        {out.kind=FailedEndpointKind::StagedCell;out.stagedParent=actualParent;}
        return out;
    };
    auto failureFace=[&](const Q::Face& measured)->FailedFace
    {
        FailedFace out;out.stockKey=measured.id;
        const auto physical=draft.nativeRows.find(measured.id);
        if(physical!=draft.nativeRows.end())
        {
            const auto& row=physical->second;
            out.kind=row.source>=0?FailedFaceKind::SourceFacet:FailedFaceKind::GeneratedCap;
            out.proposedNativeFace=row.native;out.sourceFace=row.source;
            out.patch=row.patch;out.zone=row.zone;out.ring=row.ring;
            const auto interface=draft.interfaces.find(measured.id);
            if(interface!=draft.interfaces.end())out.ring=interface->second.outline;
            out.owner=failureEndpoint(measured.owner,row.owner);
            out.neighbour=failureEndpoint(measured.neighbour,row.neighbour);
            return out;
        }
        // Reproduce only the already assigned integer side-key sequence. This
        // failure-only lookup does not rebuild any geometry, metric or recipe.
        label key=firstSideFaceKey;
        for(const auto& recipe:recipes)for(std::size_t i=0;i<recipe.second.sides.size();++i,++key)
            if(key==measured.id)
            {
                out.kind=FailedFaceKind::RecipeSide;out.recipeParent=recipe.first;
                out.recipeSide=label(i);out.ring=recipe.second.sides[i].ring;
                out.owner=failureEndpoint(measured.owner,-1);
                out.neighbour=failureEndpoint(measured.neighbour,-1);
                return out;
            }
        return out;
    };
    // Observe only the actual failed Q input; never redo Q/source/peer/work.
    auto captureFirstFailedCell=[&](label key,const std::vector<Q::Face>& rows,const Q::Cell& cell,const char* actualReason) noexcept
    {
        if(!firstFailedCell||firstFailedCell->seen)return;
        auto& a=*firstFailedCell;a.seen=true;a.stockCell=key;
        a.parent=diagnosticParent;a.child=diagnosticChild;a.planned=diagnosticPlanned;
        a.strictInput=strictGeometry;a.requirePassingInput=requirePassing;
        a.repairedWhole=wholeParts.count(key)!=0;
        a.basePointCount=m.points.size();a.stagedPointCount=stagedPointCount;
        a.cellFinite=cell.finite;a.cellStrictGeometry=cell.strictGeometry;
        a.centreBits=FirstFailedCellObservation::bits(cell.centre);
        a.volumeBits=FirstFailedCellObservation::bits(cell.volume);
        a.determinantBits=FirstFailedCellObservation::bits(cell.determinant);
        a.aspectBits=FirstFailedCellObservation::bits(cell.aspect);
        a.blockingStockFace=cell.blockingPair.face>=0?cell.blockingPair.face:cell.blockingFace;
        a.otherBlockingStockFace=cell.blockingPair.otherFace;a.blockingStockPoint=cell.blockingPoint;
        a.blockingCosineBits=FirstFailedCellObservation::bits(cell.blockingPair.cosine);
        const char* why=actualReason;std::size_t n=0;
        if(why){for(;n+1<a.reason.size()&&why[n];++n)a.reason[n]=why[n];a.reasonTruncated=why[n]!=0;}
        const auto recipe=recipes.find(diagnosticParent);
        if(diagnosticPlanned&&recipe!=recipes.end()&&recipe->second.geometry.births.size()==1)
        {a.apexKnown=true;a.apexBits=FirstFailedCellObservation::bits(recipe->second.geometry.births.front().position);}
        a.metricArraysComplete=cell.faceIds.size()==rows.size()&&cell.areas.size()==rows.size()&&cell.centres.size()==rows.size();
        if(rows.size()>a.faces.size()){a.state=FirstFailedCellObservation::State::BoundsExceeded;return;}
        auto endpoint=[&](label stock,label stagedParent)
        {
            FirstFailedCellObservation::Endpoint e;e.stockKey=stock;
            const auto p=draft.plannedChildren.find(stock);
            if(p!=draft.plannedChildren.end()){e.planned=true;e.stagedParent=p->second.first;e.plannedChild=p->second.second;}
            else if(stock>=0)e.stagedParent=stagedParent;
            return e;
        };
        auto part=[&](label native,label source,label owner,label neighbour,label patch,label zone,const std::vector<label>& ring)->bool
        {
            if(a.partCount==a.parts.size()||ring.size()>a.sourceRingNodes.size()-a.sourceRingNodeCount)return false;
            auto& p=a.parts[a.partCount++];p.proposedNativeBase=native;p.sourceHandle=source;
            p.beforeOwner=owner;p.beforeNeighbour=neighbour;p.patch=patch;p.zone=zone;
            p.ringOffset=a.sourceRingNodeCount;p.ringCount=ring.size();
            for(label id:ring)a.sourceRingNodes[a.sourceRingNodeCount++]=id;
            return true;
        };
        for(std::size_t i=0;i<rows.size();++i)
        {
            const auto& row=rows[i];
            if(row.ring.size()>a.ringNodes.size()-a.ringNodeCount){a.state=FirstFailedCellObservation::State::BoundsExceeded;return;}
            auto& f=a.faces[a.faceCount++];f.stockFace=row.id;f.patch=row.patch;f.zone=row.zone;
            f.ringOffset=a.ringNodeCount;f.ringCount=row.ring.size();f.partOffset=a.partCount;
            const auto native=draft.nativeRows.find(row.id);
            f.owner=endpoint(row.owner,native==draft.nativeRows.end()?label(-1):native->second.owner);
            f.neighbour=endpoint(row.neighbour,native==draft.nativeRows.end()?label(-1):native->second.neighbour);
            if(i<cell.faceIds.size()&&i<cell.areas.size()&&i<cell.centres.size()&&cell.faceIds[i]==row.id)
            {
                f.metricKnown=true;f.areaBits=FirstFailedCellObservation::bits(cell.areas[i]);f.centreBits=FirstFailedCellObservation::bits(cell.centres[i]);
                f.metricFinite=std::isfinite(cell.areas[i].x())&&std::isfinite(cell.areas[i].y())&&std::isfinite(cell.areas[i].z())
                    &&std::isfinite(cell.centres[i].x())&&std::isfinite(cell.centres[i].y())&&std::isfinite(cell.centres[i].z());
            }
            for(label id:row.ring)
            {
                if(id<0||std::size_t(id)>=draft.points.size()){a.state=FirstFailedCellObservation::State::InvalidPoint;return;}
                const auto& p=draft.points[id];
                if(!std::isfinite(p.x())||!std::isfinite(p.y())||!std::isfinite(p.z())){a.state=FirstFailedCellObservation::State::InvalidPoint;return;}
                a.ringNodes[a.ringNodeCount++]=id;bool found=false;
                for(std::size_t j=0;j<a.pointCount;++j)if(a.points[j].localStockPoint==id){found=true;break;}
                if(!found)
                {
                    if(a.pointCount==a.points.size()){a.state=FirstFailedCellObservation::State::BoundsExceeded;return;}
                    auto& q=a.points[a.pointCount++];q.localStockPoint=id;q.bits=FirstFailedCellObservation::bits(p);
                }
            }
            if(native!=draft.nativeRows.end())
            {
                f.actualNativeBase=native->second.native;
                const auto composite=draft.composites.find(row.id);
                const auto interface=draft.interfaces.find(row.id);
                if(interface!=draft.interfaces.end())
                {
                    f.nativeInterface=true;
                    for(const auto& p:interface->second.constituents)
                        if(!part(p.native,p.source,p.owner,p.neighbour,p.patch,p.zone,p.ring))
                        {a.state=FirstFailedCellObservation::State::BoundsExceeded;return;}
                }
                else if(composite!=draft.composites.end())
                {
                    f.composite=true;
                    for(const auto& p:composite->second.constituents)
                        if(!part(p.native,p.source,p.owner,p.neighbour,p.patch,p.zone,p.ring))
                        {a.state=FirstFailedCellObservation::State::BoundsExceeded;return;}
                }
                else
                {
                    const auto& p=native->second;
                    if(!part(p.native,p.source,p.owner,p.neighbour,p.patch,p.zone,p.ring))
                    {a.state=FirstFailedCellObservation::State::BoundsExceeded;return;}
                }
            }
            f.partCount=a.partCount-f.partOffset;
        }
        a.state=a.reasonTruncated?FirstFailedCellObservation::State::TextTruncated:FirstFailedCellObservation::State::Complete;
    };
    for(const auto& entry:draft.rows)
    {
        if(!work())return fail("column-clock");
        const auto identity=draft.plannedChildren.find(entry.first);
        diagnosticPlanned=identity!=draft.plannedChildren.end();
        diagnosticParent=diagnosticPlanned?identity->second.first:entry.first;
        diagnosticChild=diagnosticPlanned?identity->second.second:0;
        // Split whole columns retain their repaired depth test. Future cone
        // children use the actual stock pyramid test; the caller separately
        // retains its original 3um apex-to-real-base and point proximity tests.
        auto cell=Q::measure(entry.first,entry.second,draft.points,metric,coupledPatch,
            available,strictGeometry,wholeParts.count(entry.first)!=0);
        if(!cell.finite||(strictGeometry&&!cell.strictGeometry))
        {
            captureFirstFailedCell(entry.first,entry.second,cell,cell.reason?cell.reason:"column-future-shell");
            diagnosticPair=cell.blockingPair;
            const label face=diagnosticPair.face>=0?diagnosticPair.face:cell.blockingFace;
            diagnosticFace=draft.nativeFaces.count(face)?face:-1;
            diagnosticOtherNativeFace=draft.nativeFaces.count(cell.blockingPair.otherFace)
                ?cell.blockingPair.otherFace:-1;
            // Preserve both real failure operands while the source/recipe maps
            // are alive. A logging allocation failure keeps the original veto.
            try
            {
                const label keys[2]={face,cell.blockingPair.otherFace};
                for(std::size_t k=0;k<2;++k)
                {
                    const auto measured=std::find_if(entry.second.begin(),entry.second.end(),
                        [&](const Q::Face& f){return f.id==keys[k];});
                    if(measured!=entry.second.end())diagnosticFaces[k]=failureFace(*measured);
                }
                diagnosticFacesCaptured=diagnosticFaces[0].kind!=FailedFaceKind::Unknown
                    &&diagnosticFaces[1].kind!=FailedFaceKind::Unknown;
            }
            catch(...)
            {diagnosticFaces=std::array<FailedFace,2>();diagnosticFacesCaptured=false;}
            return fail(cell.reason?cell.reason:"column-future-shell");
        }
        if(requirePassing&&(cell.determinant<.001||cell.aspect>1000))
        {
            captureFirstFailedCell(entry.first,entry.second,cell,"column-D-AR");
            // Retain the existing measured values; no geometry/quality recompute.
            out.failedDarKnown=true;out.failedDeterminant=cell.determinant;
            out.failedAspect=cell.aspect;
            if(failureGeometry)
            {
                // Copy only this already measured failed shell. No new probe/math.
                try
                {
                    std::size_t corners=0,uniqueCount=0;std::array<label,256> keys;
                    bool bounds=entry.second.size()>64,invalid=false;
                    for(const auto& face:entry.second)
                    {
                        if(face.ring.size()>256||corners>256-face.ring.size())
                        {bounds=true;break;}
                        corners+=face.ring.size();
                        for(label p:face.ring)
                        {
                            if(p<0||std::size_t(p)>=draft.points.size()){invalid=true;break;}
                            if(std::find(keys.begin(),keys.begin()+uniqueCount,p)==keys.begin()+uniqueCount)
                            {
                                if(uniqueCount==keys.size()){bounds=true;break;}
                                keys[uniqueCount++]=p;
                            }
                        }
                        if(bounds||invalid)break;
                    }
                    if(bounds)failureGeometry->state=FailureGeometry::State::BoundsExceeded;
                    else if(invalid)failureGeometry->state=FailureGeometry::State::InvalidReference;
                    else
                    {
                        FailureGeometry snapshot;snapshot.stockKey=entry.first;
                        snapshot.parent=diagnosticParent;snapshot.child=diagnosticChild;
                        snapshot.planned=diagnosticPlanned;snapshot.strictInput=strictGeometry;
                        snapshot.requirePassingInput=requirePassing;
                        snapshot.repairedWhole=wholeParts.count(entry.first)!=0;
                        snapshot.cellFinite=cell.finite;snapshot.cellStrictGeometry=cell.strictGeometry;
                        snapshot.centre=cell.centre;snapshot.volume=cell.volume;
                        snapshot.determinant=cell.determinant;snapshot.aspect=cell.aspect;
                        snapshot.faces=entry.second;snapshot.faceIds=cell.faceIds;
                        snapshot.areas=cell.areas;snapshot.centres=cell.centres;
                        snapshot.pointKeys.assign(keys.begin(),keys.begin()+uniqueCount);
                        snapshot.points.reserve(uniqueCount);
                        for(label p:snapshot.pointKeys)snapshot.points.push_back(draft.points[p]);
                        snapshot.selectedSf.reserve(snapshot.faces.size());
                        snapshot.coupled.reserve(snapshot.faces.size());
                        snapshot.provenance.reserve(snapshot.faces.size());
                        std::size_t typedCorners=0;
                        for(const auto& face:snapshot.faces)
                        {
                            const bool isCoupled=face.patch>=0&&coupledPatch(face.patch);
                            snapshot.coupled.push_back(isCoupled);
                            snapshot.selectedSf.push_back(face.patch<0||isCoupled);
                            snapshot.provenance.push_back(failureFace(face));
                            const auto n=snapshot.provenance.back().ring.size();
                            if(n>256||typedCorners>256-n){bounds=true;break;}
                            typedCorners+=n;
                        }
                        const auto recipe=recipes.find(diagnosticParent);
                        if(diagnosticPlanned&&recipe!=recipes.end()&&recipe->second.geometry.births.size()==1)
                        {snapshot.apexKnown=true;snapshot.apex=recipe->second.geometry.births.front().position;}
                        if(bounds)failureGeometry->state=FailureGeometry::State::BoundsExceeded;
                        else
                        {snapshot.state=FailureGeometry::State::Complete;*failureGeometry=std::move(snapshot);}
                    }
                }
                catch(...)
                {
                    *failureGeometry=FailureGeometry();
                    failureGeometry->state=FailureGeometry::State::CaptureException;
                }
            }
            return fail("column-D-AR");
        }
        draft.proof.cells.emplace(entry.first,std::move(cell));
    }
    if(!work())return fail("column-clock");
    for(const auto& item:draft.composites)
        if(!work()||!compositeAuthority||!*compositeAuthority||!(*compositeAuthority)(item.second))
            return fail("column-composite-real-source");
    for(const auto& item:draft.interfaces)
        if(!work()||!interfaceAuthority||!*interfaceAuthority||!(*interfaceAuthority)(item.second))
            return fail("column-interface-real-source");
    draft.geometryReady=true;out=std::move(draft);return true;
}
// Canonical full-child boundary integral only. Quality is already measured by
// the complete future cell and facet stock passes; no raw whole-cell convexity
// or additional child-centre depth condition is used as an integral proxy.
inline bool outsideIntegral(const Prepared& prepared,label parent,const point& reference,
 const std::function<bool()>& available,scalar& volume,std::array<long double,4>& physical)
{
    volume=0;physical={{0,0,0,0}};
    auto work=[&](){return !available||available();};
    if(!prepared.geometryReady||!S::finite(reference))return false;
    struct PointMesh {const std::vector<point>& points;} mesh{prepared.points};S::Plan noBirths;
    bool found=false;
    const auto children=prepared.parentChildren.find(parent);
    if(children!=prepared.parentChildren.end())for(label child:children->second)
    {
        found=true;
        const auto cell=prepared.proof.cells.find(child);
        const auto rows=prepared.rows.find(child);
        if(cell==prepared.proof.cells.end()||rows==prepared.rows.end())return false;
        volume+=cell->second.volume;
        for(const auto& f:rows->second)
        {
            if(!work()||(f.owner!=child&&f.neighbour!=child))return false;
            vector sf;point centre;S::metric(mesh,noBirths,f.ring,sf,centre);
            if(!S::finite(sf)||!S::finite(centre))return false;
            for(std::size_t j=0;j<f.ring.size();++j)
            {
                if(!work())return false;
                label a=f.ring[j],b=f.ring[(j+1)%f.ring.size()];
                if(f.owner!=child)std::swap(a,b);
                std::array<long double,3> aa,bb,cc;
                for(label k=0;k<3;++k)
                {
                    aa[k]=static_cast<long double>(prepared.points[a][k])-reference[k];
                    bb[k]=static_cast<long double>(prepared.points[b][k])-reference[k];
                    cc[k]=static_cast<long double>(centre[k])-reference[k];
                }
                const long double v6=aa[0]*(bb[1]*cc[2]-bb[2]*cc[1])
                    +aa[1]*(bb[2]*cc[0]-bb[0]*cc[2])+aa[2]*(bb[0]*cc[1]-bb[1]*cc[0]);
                const long double tv=v6/6;physical[0]+=tv;
                for(label k=0;k<3;++k)physical[k+1]+=tv*(aa[k]+bb[k]+cc[k])/4;
            }
        }
    }
    for(long double x:physical)if(!std::isfinite(x))return false;
    return found&&std::isfinite(volume)&&volume>VSMALL&&physical[0]>0&&work();
}
template<class Coupled,class Peer,class Remote>
bool finish(const Prepared& prepared,Coupled coupled,Peer peer,Remote remote,
 const std::function<bool()>& available,Proof& out,const char** why=nullptr,
 bool requirePassing=true,bool requireValid=true,bool checkFaceAngle=true)
{
    out=Proof();auto fail=[&](const char* s){out=Proof();if(why)*why=s;return false;};
    if(!prepared.geometryReady)return fail("column-unprepared");
    auto work=[&](){return !available||available();};
    out=prepared.proof;const auto& points=prepared.points;const auto& rows=prepared.rows;
    struct PointMesh {const std::vector<point>& points;} localMesh{points};
    S::Plan noBirths;
    const Q::FaceOF metric=[&](const std::vector<label>& ring,vector& sf,point& fc)
    {S::metric(localMesh,noBirths,ring,sf,fc);};
    std::set<label> tested;
    for(const auto& entry:rows)for(const auto& f:entry.second)
    {
        if(!work())return fail("column-clock");
        if(!tested.insert(f.id).second)continue;
        const auto own=out.cells.find(f.owner);
        const auto nei=out.cells.find(f.neighbour);
        point ownCC,neiCC;scalar ownV=0,neiV=0;
        if(own!=out.cells.end()){ownCC=own->second.centre;ownV=own->second.volume;}
        else if(!peer(f.owner,f.id,ownCC,ownV))return fail("column-outside-owner");
        if(f.neighbour>=0)
        {
            if(nei!=out.cells.end()){neiCC=nei->second.centre;neiV=nei->second.volume;}
            else if(!peer(f.neighbour,f.id,neiCC,neiV))return fail("column-outside-neighbour");
        }
        Q::FacetQuality facet;
        if(coupled(f.patch))
        {
            Q::CoupledEndpoint endpoint;
            if(f.neighbour>=0||!remote(f.id,endpoint))return fail("column-missing-real-processor-peer");
            facet=Q::coupledFacet(f,points,metric,ownCC,ownV,endpoint,available);
        }
        else facet=Q::facet(f,points,metric,ownCC,ownV,
            f.neighbour>=0?&neiCC:nullptr,f.neighbour>=0?neiV:ownV,available);
        if(!facet.numeric||(requireValid&&!facet.valid))
            return fail(facet.reason?facet.reason:"column-facet");
        if(requirePassing)for(std::size_t k=1;k<5;++k)
            if(!Q::passes(facet.v[k],k))return fail("column-W-R-S-N");
        FacetRole role;role.quality=facet;role.patch=f.patch;role.zone=f.zone;
        const auto native=prepared.nativeRows.find(f.id);
        if(native!=prepared.nativeRows.end())role.source=native->second.source;
        const auto composite=prepared.composites.find(f.id);
        if(composite!=prepared.composites.end())
        {
            role.source=-1; // no invented scalar common Source
            role.constituents=composite->second.constituents;
        }
        const auto interface=prepared.interfaces.find(f.id);
        if(interface!=prepared.interfaces.end())
        {
            const auto& r=interface->second;
            if(composite!=prepared.composites.end()||f.ring.size()<3||f.patch!=-1||f.zone!=-1||f.neighbour<0)
                return fail("column-interface-facet-role");
            role.source=r.source;role.interfaceConstituents=r.constituents;
        }
        const auto oi=prepared.plannedChildren.find(f.owner),ni=prepared.plannedChildren.find(f.neighbour);
        role.parentOwner=oi==prepared.plannedChildren.end()?f.owner:oi->second.first;
        role.parentNeighbour=ni==prepared.plannedChildren.end()?f.neighbour:ni->second.first;
        role.ownerCentre=ownCC;role.ownerVolume=ownV;role.internal=f.neighbour>=0;
        if(role.internal){role.neighbourCentre=neiCC;role.neighbourVolume=neiV;}
        for(label p:f.ring)role.ring.push_back(points[p]);
        if(!out.facets.emplace(f.id,std::move(role)).second)return fail("column-facet-role");
        if(checkFaceAngle)
        {
        vector sf;point fc;metric(f.ring,sf,fc);
        const vector normal=sf/(mag(sf)+ROOTVSMALL);
        const scalar maxSin=std::sin(80*constant::mathematical::pi/180);
        vector previous=points[f.ring.front()]-points[f.ring.back()];
        scalar previousLength=mag(previous);previous/=previousLength+ROOTVSMALL;
        for(std::size_t j=0;j<f.ring.size();++j)
        {
            if(!work())return fail("column-clock");
            vector edge=points[f.ring[(j+1)%f.ring.size()]]-points[f.ring[j]];
            const scalar length=mag(edge);edge/=length+ROOTVSMALL;
            if(previousLength>SMALL&&length>SMALL)
            {
                vector turn=previous^edge;const scalar sine=mag(turn);
                if(sine>=maxSin)
                {turn/=sine;if((turn&normal)<SMALL)return fail("column-face-angle");}
            }
            previous=edge;previousLength=length;
        }
        }
        if(prepared.nativeFaces.empty()||prepared.nativeFaces.count(f.id))out.faces.insert(f.id);
    }
    if(!work())return fail("column-clock");out.ready=true;return true;
}
// Decisions use complete literal prefix/future recipes. Cell debts belong to
// one immutable root, facet debts to one source face and its two immutable roots.
// Collection does not grant mutation, and cannot borrow another source's debt.
template<class Root>
bool inheritsFuture(const Prepared& before,const Proof& oldProof,
 const Prepared& after,const Proof& newProof,Root originalRoot,
 const std::function<bool()>& available,const char** why=nullptr)
{
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    auto work=[&](){return !available||available();};
    if(!before.geometryReady||!after.geometryReady||!oldProof.ready||!newProof.ready)
        return fail("binary-prefix-unmeasured");
    struct CellRole {std::size_t badD=0,badAR=0;scalar worstD=GREAT,worstAR=0;};
    std::map<label,CellRole> oldCells,newCells;
    auto collectCells=[&](const Prepared& prepared,const Proof& proof,std::map<label,CellRole>& roles)
    {
        for(const auto& entry:proof.cells)
        {
            if(!work()||!entry.second.finite)return false;
            const auto child=prepared.plannedChildren.find(entry.first);
            const label parent=child==prepared.plannedChildren.end()?entry.first:child->second.first;
            const label root=originalRoot(parent);if(root<0)return false;
            const auto& cell=entry.second;auto& role=roles[root];
            role.badD+=!Q::passes(cell.determinant,0);role.badAR+=cell.aspect>1000;
            role.worstD=min(role.worstD,cell.determinant);role.worstAR=max(role.worstAR,cell.aspect);
        }
        return true;
    };
    if(!collectCells(before,oldProof,oldCells)||!collectCells(after,newProof,newCells))
        return fail("binary-cell-role");
    if(oldCells.size()!=newCells.size())return fail("binary-cell-root-set");
    for(const auto& entry:newCells)
    {
        if(!work())return fail("column-clock");
        const auto found=oldCells.find(entry.first);if(found==oldCells.end())return fail("binary-new-root");
        const auto& old=found->second;const auto& next=entry.second;
        if(next.badD>old.badD||!Q::inheritsNumeric(old.worstD,next.worstD,0))return fail("binary-inherited-D");
        if(next.badAR>old.badAR||!Q::inheritsAspect(old.worstAR,next.worstAR))return fail("binary-inherited-AR");
    }
    using Key=std::tuple<label,label,label,label,label>;
    std::map<Key,std::vector<const FacetRole*>> oldFaces,newFaces;
    auto collectFaces=[&](const Proof& proof,std::map<Key,std::vector<const FacetRole*>>& roles)
    {
        for(const auto& entry:proof.facets)
        {
            if(!work()||!entry.second.quality.numeric)return false;
            const auto& face=entry.second;
            if(!face.constituents.empty())return false; // no generated-role alias
            if(!face.interfaceConstituents.empty())
            {
                if(!face.internal||face.source<0||face.patch!=-1||face.zone!=-1
                    ||face.interfaceConstituents.size()<2||face.interfaceConstituents.size()>128)return false;
                std::set<label> native;
                for(const auto& part:face.interfaceConstituents)
                {
                    if(!work()||part.native<0||!native.insert(part.native).second||part.source!=face.source
                        ||part.owner!=face.parentOwner||part.neighbour!=face.parentNeighbour
                        ||part.patch!=-1||part.zone!=-1)return false;
                }
                if(native.empty()||*native.begin()!=entry.first)return false;
            }
            const label own=originalRoot(face.parentOwner);
            const label nei=face.parentNeighbour<0?-1:originalRoot(face.parentNeighbour);
            if(own<0||(face.parentNeighbour>=0&&nei<0))return false;
            const label lo=nei<0?own:min(own,nei),hi=nei<0?-1:max(own,nei);
            const bool physical=face.source>=0;
            roles[{physical?face.source:label(-1),lo,hi,
                physical?face.patch:label(-1),physical?face.zone:label(-1)}].push_back(&face);
        }
        return true;
    };
    if(!collectFaces(oldProof,oldFaces)||!collectFaces(newProof,newFaces))return fail("binary-facet-role");
    auto unchanged=[](const FacetRole& a,const FacetRole& b)
    {
        return a.ring==b.ring&&a.internal==b.internal
            &&a.ownerCentre==b.ownerCentre&&a.ownerVolume==b.ownerVolume
            &&(!a.internal||(a.neighbourCentre==b.neighbourCentre&&a.neighbourVolume==b.neighbourVolume));
    };
    for(const auto& entry:newFaces)
    {
        if(!work())return fail("column-clock");
        const auto found=oldFaces.find(entry.first);
        if(found==oldFaces.end())
        {
            for(const auto* face:entry.second)
            {
                if(!work()||!face->quality.valid)return fail("binary-new-facet-stock");
                for(std::size_t k=1;k<5;++k)
                    if(!Q::passes(face->quality.v[k],k))return fail("binary-new-facet-role");
            }
            continue;
        }
        const auto& old=found->second;const auto& next=entry.second;
        if(old.size()==1&&next.size()==1)
        {
            if(!Q::inheritsFacet(old.front()->quality,next.front()->quality,
                !unchanged(*old.front(),*next.front())))return fail("binary-inherited-facet");
            continue;
        }
        for(const auto* face:next)
            if(!work()||!face->quality.valid)return fail("binary-changed-facet-stock");
        for(std::size_t k=1;k<5;++k)
        {
            std::size_t oldBad=0,newBad=0;scalar oldWorst=k<3?GREAT:scalar(0),newWorst=oldWorst;
            for(const auto* face:old)
            {
                if(!work())return fail("column-clock");oldBad+=!Q::passes(face->quality.v[k],k);
                oldWorst=k<3?min(oldWorst,face->quality.v[k]):max(oldWorst,face->quality.v[k]);
            }
            for(const auto* face:next)
            {
                if(!work())return fail("column-clock");newBad+=!Q::passes(face->quality.v[k],k);
                newWorst=k<3?min(newWorst,face->quality.v[k]):max(newWorst,face->quality.v[k]);
            }
            if(newBad>oldBad||!Q::inheritsNumeric(oldWorst,newWorst,k))return fail("binary-inherited-interface-role");
        }
    }
    return work()||fail("column-clock");
}

template<class Mesh,class Coupled,class Peer>
bool qualify(const Mesh& m,const S::Plan& staged,
 const std::map<label,std::vector<S::Row>>& shells,Coupled coupled,Peer peer,
 const std::function<bool()>& available,Proof& out,const char** why=nullptr)
{
    // Original ordinary entry retains its explicit coupled refusal.
    out=Proof();
    for(const auto& cell:shells)for(const auto& row:cell.second)if(coupled(row.patch))
    {if(why)*why="column-needs-joint-processor-plan";return false;}
    Prepared prepared;
    if(!prepareShells(m,staged,shells,coupled,available,prepared,why))return false;
    const auto noRemote=[](label,Q::CoupledEndpoint&){return false;};
    return finish(prepared,coupled,peer,noRemote,available,out,why);
}
} }
#endif
