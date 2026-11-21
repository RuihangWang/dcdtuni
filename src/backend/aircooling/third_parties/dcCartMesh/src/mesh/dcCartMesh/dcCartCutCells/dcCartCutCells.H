/*---------------------------------------------------------------------------*\
    dcCartCutCells — true cut cells for dcCartMesh

    The builder assembles a stair-step mesh: every cell is a full hex and a
    surface lands on whichever grid face is nearest to it. This module takes
    that assembled mesh and clips it against the STL surfaces so the mesh
    boundary (and every internal baffle) is the surface itself:

      1. every mesh point is put on one SIDE of the surface (A / B / ON);
      2. every edge whose ends differ gets its crossing point (an actual
         surface hit — a cell whose mixed edges cannot all be hit keeps its
         stair-step shape, so the scheme degrades gracefully at a surface's
         free edge);
      3. every face with points on both sides is split into an A polygon and
         a B polygon along the chord between its crossings;
      4. every cell with points on both sides becomes two polyhedra joined by
         a CAP face — the surface inside the cell. For a carving surface the
         B (solid) half is dropped and the cap is a boundary face of the
         surface's patch; for a baffle both halves stay and the cap is an
         internal face in the baffle's faceZone;
      5. cut cells whose volume fell below a fraction of their hex are merged
         into a neighbour, so the solver never sees a sliver.

    Everything works on plain vectors of points / polygons / owner /
    neighbour, so the geometry kernel is independent of the octree.
\*---------------------------------------------------------------------------*/

#ifndef dcCartCutCells_H
#define dcCartCutCells_H

#include "dcCartSurfaces.H"
#include "dcCartMeshBuilder.H"
#include "dcCartGrid.H"
#include "dcCartCompleteCavityOriginCore.H"
#include "dcCartPreparedFacetBirth.H"
#include "dcCartPostCarveCanonicalWallFan.H"
#include "dcCartNativeInterfaceGeometry.H"
#include "point.H"
#include "labelList.H"
#include "boolList.H"
#include "wordList.H"
#include <vector>
#include <functional>
#include <memory>
#include <map>
#include <set>
#include <string>
#include <array>

namespace Foam
{

class polyMesh;
class Time;
namespace dcCartCrossGridFluidPairTransaction { class Phase; }

class dcCartCutCells
{
public:

    struct writerCoupledBaseline;
    class writerCoupledTransition;
    struct writerNumericData;
    struct writerCurrentContext;

    // Default-empty authority: the singleton/global absence bool is NOT enough.
    // Normal native provenance, not TRACE; parent labels are handles only.
    struct weightUnionOrigin
    {
        label nativeParent=-1,rank=-1,level=-1;
        uint64_t leaf=0,piece=0;
        std::string region;
        // Original source-facet ID + physical patch + face-zone identity.
        std::vector<std::array<label,3>> sourceFacets;
    };
    struct weightUnionAuthority
    {
        bool globallyNoMaterialNoBulk;
        weightUnionAuthority() : globallyNoMaterialNoBulk(false) {}
        std::string rawSha256,rolesDigest,homogeneousFluidKey;
        std::function<bool(const std::vector<weightUnionOrigin>&)> homogeneousOrigins;
        std::function<scalar()> remainingSeconds;
        // Immutable normal post-carve origin producer; absence refuses W.
        std::function<weightUnionOrigin(label)> normalOriginFromSnapshot;
    };
    // Separate many-origin source permission; never a replacement for the
    // existing exactly-two/distinct-parent W authority or geometry guards.
    struct completeCavityAuthority
    {
        dcCartCompleteCavityOriginCore::Fingerprint sourceFingerprint;
        // Same immutable post-carve object verified by read(); no recapture/TRACE.
        // Read-only shape evidence, not an additional permission or ready() fallback.
        std::shared_ptr<const dcCartFluidOriginSnapshotCore::Snapshot> sourceSnapshot;
        // Read() captures the same immutable instance as completeOrigins.
        std::function<bool(const dcCartFluidOriginSnapshotCore::Snapshot*)> sourceSnapshotIsRecorded;
        std::function<bool(label)> recordedParent;
        std::function<weightUnionOrigin(label)> normalOriginFromSnapshot;
        std::function<bool(const std::vector<weightUnionOrigin>&)> completeOrigins;
        std::function<scalar()> remainingSeconds;
        bool ready() const
        {
            return bool(recordedParent) && bool(normalOriginFromSnapshot)
                && bool(completeOrigins) && bool(remainingSeconds);
        }
    };
    struct writerWeightUnionState;
    struct writerCompleteCavityState;

    struct writerBindings
    {
        // Native input slot -> the actual compact polyMesh slot.
        std::vector<label> cell, face, point, alias;
        // Every original constituent maps to the actual compact carrier here;
        // the ordinary face[] map keeps its old deleted-face semantics.
        std::map<label,label> physicalCompositeFaceCarrier;
        std::map<label,label> nativeInterfaceFaceCarrier;
    };

    //- The mutable mesh the passes work on
    // Only dcCartCutCells' actual complete Source preflight can construct a
    // permit. Geometry CompositeRecord and a public bool cannot mint one.
    class physicalCompositePermit
    {
        friend class dcCartCutCells;
        dcCartPostCarveCanonicalWallFan::CompositeRecord record_;
        dcCartCompleteCavityOriginCore::Fingerprint fingerprint_;
        std::shared_ptr<const dcCartFluidOriginSnapshotCore::Snapshot> snapshot_;
        std::vector<weightUnionOrigin> origins_;
        label publishedOwner_=-1;bool published_=false;
        physicalCompositePermit(const dcCartPostCarveCanonicalWallFan::CompositeRecord& record,
            const completeCavityAuthority& authority,const std::vector<weightUnionOrigin>& origins)
        :record_(record),fingerprint_(authority.sourceFingerprint),snapshot_(authority.sourceSnapshot),origins_(origins){}
    public:
        physicalCompositePermit()=delete;
        const dcCartPostCarveCanonicalWallFan::CompositeRecord& record()const noexcept{return record_;}
    };
    // Only an actually Source/VM/Stock-qualified two-end transaction constructs
    // this permit. A geometry Record or a public bool never grants publication.
    class nativeInterfacePermit
    {
        friend class dcCartCutCells;
        dcCartNativeInterfaceGeometry::Record record_;
        dcCartCompleteCavityOriginCore::Fingerprint fingerprint_;
        std::shared_ptr<const dcCartFluidOriginSnapshotCore::Snapshot> snapshot_;
        std::array<std::vector<weightUnionOrigin>,2> origins_;
        std::vector<label> publicationOutline_,publishedCells_;
        std::array<std::size_t,2> endpointCellCounts_{{0,0}};
        label publishedOwner_=-1,publishedNeighbour_=-1;
        bool preimageChecked_=false,published_=false;
        nativeInterfacePermit(const dcCartNativeInterfaceGeometry::Record& record,
            const completeCavityAuthority& authority,
            const std::array<std::vector<weightUnionOrigin>,2>& origins,
            const std::array<std::size_t,2>& cellCounts)
        :record_(record),fingerprint_(authority.sourceFingerprint),snapshot_(authority.sourceSnapshot),origins_(origins),
            publicationOutline_(record.outline),endpointCellCounts_(cellCounts)
        {publishedCells_.reserve(cellCounts[0]+cellCounts[1]);}
    public:
        nativeInterfacePermit()=delete;
        const dcCartNativeInterfaceGeometry::Record& record()const noexcept{return record_;}
    };
    struct cutMesh
    {
        std::vector<point> points;
        std::vector<std::vector<label>> faces;
        std::vector<label> owner;       // always >= 0 for a live face
        std::vector<label> neighbour;   // -1 on a boundary face
        std::vector<label> patch;       // boundary patch id, -1 internal
        std::vector<label> zone;        // faceZone id, -1 none
        std::vector<bool> faceAlive;
        label nCells = 0;
        // Default-OFF local extraction proof. Placeholders are false; not writer authority.
        std::vector<bool> preparedCompleteCells;
        // Exact native pre-split ancestry to original extracted point IDs; no geometry permission.
        std::vector<std::vector<label>> preparedPointFootprints;
        // Extraction-scoped, atomically committed typed D birth topology.
        // Empty/default state grants no completeness; original points still use global ID stars.
        dcCartPreparedFacetBirth::State<label> preparedBirth;
        // Normal cone construction metadata only. It indexes an actual born
        // derived AP; it is never point/source/MPI or displacement permission.
        struct normalConeAPBirth
        {
            label nativePoint=-1, constructorPart=-1, recordedSourceRoot=-1, rank=-1;
            uint64_t constructionGeneration=0, extractionEpochAtSeal=0;
            std::array<uint64_t,3> bornPointBits{{0,0,0}};
        };
        std::vector<normalConeAPBirth> normalConeAPBirths;
        uint64_t normalConeAPConstructionGeneration=0;
        bool normalConeAPBirthsSealed=false;
        // Same-mesh canonical production records; coordinates/roles must be
        // revalidated before any optional future recipe proposal.
        std::vector<dcCartPostCarveCanonicalWallFan::Record> canonicalPhysicalFanOrigins;
        // Value-owned actual APP wall-baffle producer relation, indexed by zone.
        // Empty/default carries no pair information or coalescence permission.
        std::vector<std::array<label,2>> physicalWallPatchPairs;
        // Actual APP procPatch producer: PATCH ID -> remote rank. Empty/-1 is
        // unknown; a name/old endpoint snapshot never establishes this relation.
        std::vector<label> processorPatchPeers;
        // Value-owned actual APP identity/source/surface role, not a fan-origin
        // or an absence-of-pair inference. Unknown/default permits no plain wall.
        std::vector<dcCartPostCarveCanonicalWallFan::Role> physicalPlainWallRoles;
        // Separate actual APP role for composite BC eligibility; old plain-wall
        // exclusions remain the default. Never Source or peer authority.
        std::vector<dcCartPostCarveCanonicalWallFan::Role> physicalCompositeBCRoles;
        // Sparse actual baffle-constructor region lineage and immutable source
        // face qualification. Neither carries Source/VM/peer permission.
        std::map<label,dcCartPostCarveCanonicalWallFan::BaffleRegionBirth> physicalBaffleRegionBirths;
        std::map<label,dcCartPostCarveCanonicalWallFan::Role> physicalCompositeSourceBCRoles;
        std::set<label> physicalCompositeSourceBCRejected;
        std::vector<std::shared_ptr<const physicalCompositePermit>> physicalCompositeCarriers;
        std::vector<std::shared_ptr<const nativeInterfacePermit>> nativeInterfaceCarriers;
        // Pure coordinate preservation, not source/material/coupled authority.
        // Empty, or one zero/one entry per native point.
        std::vector<unsigned char> preparedExactPoints;
        // Shared-seam edge births canonicalize only current live-face references.
        // Unreferenced old IDs retain identity and cannot contribute footprints.
        bool activePointCanonicalization=false;
        std::vector<bool> cellAlive;
        //- Assembled from the ORIGINAL carve (true) or added as a cut
        //  candidate (false). Only original cells anchor the fluid side.
        std::vector<bool> cellOrigAlive;
        //- Hex volume and centre of the cell each mesh cell descends from
        std::vector<scalar> cellRefVol;
        std::vector<point> cellRefCentre;
        //- Refinement level, kept for the merge heuristics
        std::vector<label> cellLevel;
        //- Rank that owns the cell; a cell of another rank in this rank's
        //  halo is cut like any other and dropped at the end. One rank (a
        //  run without -parallel) owns everything.
        std::vector<label> cellRank;
        //- Packed (i,j,k) of the octree leaf the cell descends from; with
        //  cellLevel it names the leaf identically on every rank
        std::vector<uint64_t> cellLeaf;
        //- Which piece of that leaf: a hash of the split history (A half
        //  and B half of every split). Splits are a pure function of the
        //  cell and the surfaces, so every rank that holds the piece gives
        //  it the same value; (level, leaf, piece) is the cell's global id.
        std::vector<uint64_t> cellPiece;
        //- Optional cone evidence only: input-stage parent/base labels.
        //  Empty in the ordinary path; never used by geometry or ownership.
        std::vector<label> coneEvidenceParent;
        std::vector<label> coneEvidenceBase;
        //- Points [0, nGridPoints) are octree grid corners, all further
        //  apart than the coincident-point tolerance (0: not known)
        label nGridPoints = 0;
        // Empty unless the default-OFF literal experiment builds its one
        // preoperator installed-writer oracle. Normal authorization never
        // depends on optional TRACE files.
        std::shared_ptr<writerCoupledBaseline> writerBaseline;
        // Only Phase can mint after actual common YES.
        std::vector<std::shared_ptr<const writerCoupledTransition>> writerCoupledTransitions;
        // Sparse normal W lineage. Independent of TRACE and the coupled baseline.
        std::shared_ptr<writerWeightUnionState> weightUnionState;
        // Normal pre-cone lineage: one bounded native-slot key per child.
        // Empty when source authority is absent; no TRACE IDs or coordinates.
        dcCartCompleteCavityOriginCore::Lineage<label> completeCavityLineage;
        // Default-OFF bounded current installed numeric views; no topology permission.
        std::shared_ptr<writerCurrentContext> completeCavityContext;
        // Owned pre-staged topology/installed journal; default-OFF, independent of TRACE.
        std::shared_ptr<writerCompleteCavityState> completeCavityState;
        bool completeCavityRepairRequested=false; // agreed normal control, not permission
    };

    struct writerCoupledFace
    {
        label nativeFace=-1, nativeOwner=-1, nativeNeighbour=-1, verifiedTransitionKeeper=-1;
        label rawPatch=-1, rawZone=-1, relativeFace=-1, compactFace=-1, compactOwner=-1;
        std::string patchName, patchType, patchRole;
        std::vector<label> nativePoints, pointAliases, ringAliases, compactPoints;
        std::vector<point> coordinates;
        vector area=vector::zero;
        point faceCentre=point::zero, cellCentre=point::zero;
        scalar volume=0;
        std::vector<std::pair<std::string,bool>> zoneRoles;
    };

    // Sparse real installed SDK values; native handles never grant Source.
    struct writerSDKFace {
        label nativeFace=-1,owner=-1,neighbour=-1,patch=-1,rawPatch=-1,zone=-1;
        std::string patchName,patchType,patchRole;
        bool coupled=false;std::vector<label> points;
        vector area=vector::zero;point centre=point::zero;
    };
    struct writerSDKCell {
        label nativeCell=-1;std::vector<label> faces;
        point centre=point::zero;scalar volume=0,determinant=0,aspect=GREAT;
    };
    struct writerTransitionShell {
        label owner=-1;std::vector<writerSDKFace> faces;std::map<label,point> points;
    };
    struct writerTransitionEndpoint {
        label nativeFace=-1,beforeOwner=-1,finalOwner=-1,peerRank=-1,peerNativeFace=-1,source=-1;
        writerTransitionShell before,final;
    };
    class writerCoupledTransition {
        friend class dcCartCrossGridFluidPairTransaction::Phase;
        writerCoupledTransition()=default;
        bool published_=false,sourceProved_=false,stockProved_=false,peerProved_=false;
        label rank_=-1;uint64_t phaseRevision_=0,lineageRevision_=0,publicationRevision_=0;
        dcCartCompleteCavityOriginCore::Fingerprint fingerprint_;
        std::shared_ptr<const ::dcCartFluidOriginSnapshotCore::Snapshot> sourceSnapshot_;
        std::shared_ptr<const writerCoupledBaseline> baseline_;
        std::vector<weightUnionOrigin> origins_;
        std::vector<writerTransitionEndpoint> endpoints_;
        std::vector<std::pair<label,label>> keepers_;
        std::shared_ptr<const writerCoupledBaseline> originalSDKProof_;
        std::map<label,label> originalKeepers_,originalCarriers_;
        std::vector<label> originalAffected_;
        std::size_t payloadBytes_=0;
    public:
        writerCoupledTransition(const writerCoupledTransition&)=delete;
        writerCoupledTransition& operator=(const writerCoupledTransition&)=delete;
        bool published()const noexcept{return published_;}
        bool complete()const noexcept{return published_&&sourceProved_&&stockProved_&&peerProved_&&baseline_&&sourceSnapshot_;}
        label rank()const noexcept{return rank_;}
        uint64_t publicationRevision()const noexcept{return publicationRevision_;}
        const dcCartCompleteCavityOriginCore::Fingerprint& fingerprint()const noexcept{return fingerprint_;}
        const std::shared_ptr<const writerCoupledBaseline>& baseline()const noexcept{return baseline_;}
        const std::vector<writerTransitionEndpoint>& endpoints()const noexcept{return endpoints_;}
        const std::vector<std::pair<label,label>>& keepers()const noexcept{return keepers_;}
        const std::shared_ptr<const writerCoupledBaseline>& originalSDKProof()const noexcept{return originalSDKProof_;}
        const std::map<label,label>& originalKeepers()const noexcept{return originalKeepers_;}
        const std::map<label,label>& originalCarriers()const noexcept{return originalCarriers_;}
        const std::vector<label>& originalAffected()const noexcept{return originalAffected_;}
        std::size_t payloadBytes()const noexcept{return payloadBytes_;}
    };

    struct writerCoupledBaseline
    {
        // Earliest immutable preoperator inputs; never updated after a union.
        std::shared_ptr<const cutMesh> checkpoint;
        // SAME original constructed oracle, never an afterSame SDK BEFORE.
        std::shared_ptr<const polyMesh> retainedInstalledOracle;
        writerBindings retainedInstalledBindings;
        // Value-only actual retire->keeper incidence journal; no Source grant.
        std::vector<label> originalHeads,originalTails,originalNext,originalCurrentOwner;
        bool originalRepresentationComplete=false;
        void recordRetirement(label drop,label keep)noexcept {
            if(!originalRepresentationComplete)return;
            if(drop<0||keep<0||drop==keep||std::size_t(drop)>=originalHeads.size()
              ||std::size_t(keep)>=originalHeads.size()||originalHeads[drop]<0||originalHeads[keep]<0
              ||originalTails[keep]<0||std::size_t(originalTails[keep])>=originalNext.size())
            {originalRepresentationComplete=false;return;}
            label member=originalHeads[drop];std::size_t guard=0;
            while(member>=0){if(std::size_t(member)>=originalNext.size()||++guard>originalNext.size())
                {originalRepresentationComplete=false;return;}
                originalCurrentOwner[member]=keep;member=originalNext[member];}
            originalNext[originalTails[keep]]=originalHeads[drop];originalTails[keep]=originalTails[drop];
            originalHeads[drop]=-1;originalTails[drop]=-1;
        }
        std::size_t retainedInstalledFieldBytes=0;
        std::vector<scalar> retainedSDKDeterminants,retainedSDKAspects;
        // One true original SDK inversion. Const actor reads grant no Source.
        std::vector<label> retainedSDKNativeCells,retainedSDKNativeFaces,retainedSDKNativePoints;
        const polyMesh* retainedSDKInverseMesh=nullptr;
        const label* retainedSDKInverseCellBinding=nullptr;
        const label* retainedSDKInverseFaceBinding=nullptr;
        const label* retainedSDKInversePointBinding=nullptr;
        bool retainedSDKInverseComplete=false;
        std::vector<label> originalFaceCarriers;
        std::function<bool(const std::vector<label>&,writerCoupledBaseline&,const std::function<bool()>&)> originalSDKCapture;
        void recordFaceCarrier(label oldFace,label carrier)noexcept {
            if(!originalRepresentationComplete)return;
            if(oldFace<0||carrier<0||std::size_t(oldFace)>=originalFaceCarriers.size()
              ||std::size_t(carrier)>=originalFaceCarriers.size())
            {originalRepresentationComplete=false;return;}
            originalFaceCarriers[oldFace]=carrier;
        }
        label currentFaceCarrier(label f)const noexcept {
            for(std::size_t guard=0;guard<originalFaceCarriers.size();++guard){
                if(f<0||std::size_t(f)>=originalFaceCarriers.size())return -1;
                const label next=originalFaceCarriers[f];if(next==f)return f;if(next<0)return -1;f=next;}
            return -1;
        }
        std::vector<writerCoupledFace> faces;
        // Actual initial installed coupled native owners, not raw patch guesses.
        std::vector<unsigned char> installedCoupledOwnerMask;
        bool installedCoupledOwnerMaskComplete=false;
        std::map<label,writerSDKCell> installedCells;
        std::map<label,writerSDKFace> installedFaces;
        std::map<label,point> installedPoints;
        bool installedSDKComplete=false;std::size_t installedSDKPayloadBytes=0;
        static constexpr std::size_t installedSDKStructuralLimit=64*1024*1024;
        std::map<std::string,std::string> patchRoles;
        std::map<std::string,label> patchSizes;
        scalar copySeconds=0, constructSeconds=0, snapshotSeconds=0;
        std::size_t cutPayloadBytes=0, writerGeometryBytes=0;
    };

    struct settings
    {
        // Same APP job-clock origin. Empty preserves existing callers; never resets time.
        std::function<bool()> workAvailable;

        //- Points closer than this to a baffle sheet count as ON it
        scalar onTolerance = 1e-6;
        //- An exact-outline sheet whose plane is tilted (a rack rotated 45
        //  degrees, a duct's expansion) counts points within this fraction
        //  of onTolerance as ON it, and so does an edge plane coplanar with
        //  such a sheet. The tilted plane grazes the grid corners along a
        //  whole diagonal; with the exact band each graze carves a corner
        //  wedge, consecutive wedges share their neighbours, and no convex
        //  merge can take them (Tri_angle: 47 zero-determinant cells, 901
        //  volume ratios). 0 keeps the exact band.
        scalar tiltedOnFraction = 0;
        //- Points closer than this to a CARVING surface count as ON it.
        //  Kept small: an ON hex corner beside a box edge hands the cap a
        //  strip lying in the hex's own face, which blunts the edge.
        scalar onToleranceCarve = 1e-6;
        //- Cut cells smaller than this fraction of their hex are merged
        scalar minVolumeFraction = 0.10;
        //- A sliver below this fraction of its hex that found no convex
        //  host is DROPPED (handed to the solid): its faces with the
        //  neighbours become boundary faces under its own dominant patch.
        //  Reserve this for near-degenerate remnants. A small but valid
        //  prism must stay: removing it moves a wall by a whole cell height.
        //  Cells touching a baffle are never dropped.
        //
        //  NOTE this is NOT the near-wall thickness floor, and raising it is
        //  not the fix for one. For a slab of thickness t cut from a hex of
        //  side dx, V/Vref = t/dx, so a fraction floor f does imply
        //  t = f*dx and y_P = f*dx/2 -- which is why the pre-68bf5dab value
        //  0.005 coincided with the y_P = 5.0e-4 floor the converging meshes
        //  show at dx = 0.2 m. Restoring 0.005 on today's code was measured
        //  and rejected: the cells that actually matter (1 mm duct-riser
        //  tetrahedra, V 1.5e-10, y_P 1.3e-4) are exempt from this pass
        //  anyway -- they touch a baffle or sit inside a thin closed shell --
        //  so min volume stayed at 1.8e-12 while 54 other cells were deleted,
        //  the tet count fell 39 -> 4, "hosts out of reach" rose 48 -> 932
        //  and max skewness went 3.97 -> 151.25 (Failed 1). The floor has to
        //  be expressed as y_P on wall-adjacent cells, with the exemptions
        //  overridden for it, not as a bigger volume fraction here.
        scalar killVolumeFraction = 1e-6;
        //- Per faceZone: the wall patch of a zone that is a wall on both
        //  sides yet stays a zone through the merges (wallPairZones), -1
        //  otherwise. Their cells may go to the solid at the end of the merge:
        //  the cell across the face keeps a wall in that patch.
        labelList wallZonePatch;
        //- A sliver (below minVolumeFraction of its hex) THINNER than this,
        //  thickness t = V / A_max, is handed to the solid as well. Along a
        //  straight axis-aligned pipe a grid line 0.66 mm outside a facet of
        //  the 16-gon cut a triangular wedge from every cell of the run
        //  (Shinaide_liquid: 7.5e-4 of the hex); joining it to the cell
        //  beside it skewed the pipe face to 8-27, so the passes chained
        //  the wedges along the pipe into a 12 cm strip of 1e-7 m3 next to
        //  full cells: 563 faces below checkMesh's volume ratio 0.01. Only a
        //  sliver whose boundary faces are all plain walls goes (the faces it
        //  hands its neighbours take its patch, and an inlet, outlet or baffle
        //  must not gain sideways faces); the wall moves by less than t. The
        //  application sets it to the ON band, onTolerance: geometry closer
        //  than that to a cell face is below the mesher's resolution anyway.
        //  dcCartMeshDict killCutThickness overrides; 0 disables.
        scalar killThickness = 0;
        //- The same floor as a fraction of the sliver's own reference hex
        //  edge (the larger of the two applies). A coarse cell beside a
        //  surface refined less than the finest level saw only the finest
        //  cell's band: a 2.3 mm corner tetrahedron that DAML's duct-riser
        //  elbow cut off a 61 mm cell (1e-3 of it, no convex host) stayed,
        //  above the 1.55 mm of a level-4 cell elsewhere. 0 disables.
        scalar killThicknessFraction = 0;
        //- Degenerate cells -- below this fraction of their hex -- are absorbed
        //  into the neighbour across their largest plain face, REGARDLESS of
        //  the baffle / thin-shell exemptions above and without any quality
        //  guard: a cell of no volume cannot make its host worse, while it
        //  makes checkMesh report "zero volume" and the solver NaN at step 1
        //  (Tri_angle at base 0.25: a column of 244 cells, 0 to 3.4e-9 of
        //  their hex, between a rotated rack's perforated blanking panel and
        //  its partition sheet, each carrying a zone face, so neither the
        //  merge nor the kill would take them). The default equals
        //  killVolumeFraction: a cell the kill would take but for an
        //  exemption is absorbed instead, so nothing below 1e-6 survives
        //  either way. 0 disables.
        scalar degenerateVolumeFraction = 1e-6;
        //- A merge (rejoin or sliver) may not leave the union facing a
        //  neighbour more obliquely than this many degrees, unless one of the
        //  two cells already did (0 disables the guard)
        scalar mergeMaxNonOrtho = 75;
        //- A crumb (below 1 % of its hex) with a baffle face may join a host
        //  of at least minVolumeFraction whose union scores up to this factor
        //  x the merge skew limit (0 disables)
        scalar crumbBaffleSkewFactor = 1.1;
        //- baffleCrumb, the last round of mergeSmallCells: a crumb below this
        //  fraction of its hex that carries a faceZone face and shares a
        //  plain face with a neighbour more than a hundred times its volume
        //  (checkMesh's volume ratio 0.01), and that no merge took, goes to
        //  the solid -- but only when every face it hands over, the zone face
        //  to the cell across the sheet included, keeps checkMesh's boundary
        //  skewness within mergeMaxSkew from the receiver's own centre. The
        //  baffle exemptions above exist because a kill can leave a micron
        //  notch at the receiver's far corner (skew ~36); that is the case
        //  this guard refuses. The dc-builder example at meshBaseSize 0.34 m
        //  had two such crumbs: a corner pocket between a perforated blanking
        //  panel, a rack frame and a server bottom (docs/benchmarks/
        //  dccart-034-volume-ratio-2026-10-22). A mesh that passes strict
        //  checkMesh has no face it admits. 0 disables.
        scalar baffleCrumbFraction = 0.01;
        //- Reject merges that introduce an over-limit skewed face. Includes
        //  the internal stencil and the wall measure of future baffles.
        //  Coplanar pieces sharing an edge and patch can form one face
        //  (coalesceCoplanarFaces joins them), and a baffle face is measured
        //  as the wall createBaffles makes of it: coplanar touching pieces of
        //  one faceZone as one face only for the same cell pair (0 disables)
        scalar mergeMaxSkew = 4;
        //- collapseMicroFacePoints: a boundary face below this fraction of its
        //  cell's reference face area is a MICRO face, the residue of a
        //  surface feature smaller than the cell can honour. 0 disables.
        scalar microFaceFraction = 0.005;
        //- collapseMicroFacePoints: how far (as a fraction of the cell's own
        //  size) a cut point may be moved onto another point of the cell to
        //  take a micro face away. 0 disables.
        scalar microSnapFraction = 0.02;
        //- repairThinWallCells: a wall-adjacent cell whose own wall face sits
        //  closer to its centre than this is cut residue, not a resolved
        //  feature. Metres, and the floor actually applied is
        //  min(this, 0.25 x the cell's own hex edge) so a fine grid is never
        //  asked for more than it can give. This is a WALL-FUNCTION
        //  requirement, not a geometry tolerance: epsilonWallFunction's
        //  viscous branch is eps = 2*k*nu/y^2, so y enters squared. Measured
        //  on this project -- every mesh that converges floors at exactly
        //  y_P = 5.000e-4; the one that diverged reached 1.0e-4 on 1 mm
        //  duct-riser tetrahedra, eps ran away to 3.9e11, k collapsed to
        //  1e-8, nut went to zero and the steady RAS solve oscillated at
        //  Ux ~ 0.4 for 500 iterations. Set BELOW that 5.0e-4 on purpose: the
        //  healthy meshes sit exactly on it, and a floor at the same value
        //  would delete them on a rounding error. No surveyed cut-cell mesher
        //  has this knob -- FDS prescribes tau_w (Werner-Wengle, bounded as
        //  y -> 0) and code_saturne floors the distance inside its IBM wall
        //  law, so neither needs one. OpenFOAM's eps = 2*k*nu/y^2 is unbounded
        //  and we are downstream of it. 0 disables.
        scalar minWallCellDistance = 4.0e-4;
        //- repairThinWallCells: above this fraction of its own hex a thin wall
        //  cell is a RESOLVED feature and is never touched -- a 1 mm panel in
        //  a 12.6 mm cell is 0.08, forty times this. Below it the cell is a
        //  sliver the strict rounds stranded.
        //
        //  0 (the default) turns the REPAIR off and leaves the census and the
        //  minWallCellDistance warning on, which is the part that pays for
        //  itself: it is what would have caught the killVolumeFraction
        //  regression at mesh time instead of at solve time. The repair itself
        //  is measured and not yet a win -- on DAML-Testbed it finds the right
        //  1 mm duct-riser cells (11 merged away from the wall under a full
        //  checkMesh-skew guard, 75 stranded, 83 exempt) but the 75 stranded
        //  ones have no host that lifts them clear without taking some other
        //  face over the limit, so max skewness goes 3.97 -> 4.81 and "Mesh OK"
        //  -> Failed. Handing them to the solid is worse still (22.85, and
        //  151.25 for the killVolumeFraction=0.005 revert) because every kill
        //  hands a neighbour a narrow strip at its far corner, which nothing
        //  downstream can repair. The cells have to stop being CREATED -- the
        //  tet count is 33-39 here against 0 in every mesh that converges --
        //  which is AMReX's build-time answer: cover the small cell and snap
        //  the level set onto the grid node, in a rescue loop, before the
        //  merge rounds ever see it. Set this to 0.02 to re-measure.
        scalar wallSliverVolumeFraction = 0;
        //- wedgeKill (route 3 of the DAML thin-wall wedge work package): the
        //  TERMINAL y_P-floor admission, running as the last round of
        //  mergeSmallCells -- after the sliver rounds, the quality rounds and
        //  bandAbsorb have all refused these cells, so "no host lifts them
        //  clear" is already proven by construction. A live cell is handed to
        //  the solid when ALL hold: V < wedgeVolumeFraction * its hex (a
        //  micro wedge, never a centroid-luck full cell -- the DAML killers
        //  measure V/Vref 1e-9..3e-4, while the healthy corner-cut cells that
        //  merely PROJECT their centroid near a wall plane sit at 0.6-8 and
        //  must stay), its nearest plain-wall face plane is closer to its
        //  volume centroid than wedgeWallDistance (the census metric), it
        //  carries NO faceZone face and is not inside a thin closed shell
        //  (Shinaide's containment walls). The faces it shared become
        //  boundary faces of the neighbours under its dominant patch, exactly
        //  as killExtremeSlivers does; unlike that pass the admission is
        //  y_P-driven, not volume-driven, so it reaches the duct-riser
        //  tetrahedra (V/Vref 1e-5, y_P 1.3e-4 -- the DAML eps=2*k*nu/y^2
        //  killer) whose unions with every neighbour are concave
        //  (bandAbsorb's foamOut gate refuses them, and a merge that cannot
        //  lift y_P over the floor is not a fix).
        //  Baffle-side (zone-face) wedges are EXEMPT, measured: killing them
        //  leaves the receiver an unrepairable micron notch face (checkMesh
        //  skew ~36, Failed) whatever the face-count or micro-face rule
        //  tried; they are prevented upstream instead (baffleEdgeSplit off
        //  removes the junction tetrahedra; see the round's comment).
        //  0 (the default) disables; requires `wallPatch`.
        scalar wedgeVolumeFraction = 0;
        scalar wedgeWallDistance = 0;

        //- repairThinWallCells: the relaxed tier's limits, used only for a
        //  stranded thin wall sliver. Two-tier gating is what snappyHexMesh
        //  (meshQualityDict's relaxed{ maxNonOrtho } 65 -> 75) and cfMesh
        //  (findBadFacesRelaxed, which keeps only pyramid-volume and face-area
        //  positivity) both do: strict while there is a choice, permissive
        //  when there is not, and never a silent no-op -- AMReX aborts, FDS
        //  blocks and logs, code_saturne disables and re-iterates.
        scalar mergeMaxNonOrthoRelaxed = 85;
        scalar mergeMaxSkewRelaxed = 8;

        //- The refinement ceiling from dcCartMeshDict, 0 when unset. Only used
        //  to make a baffle-seal refusal say whether the ceiling caused it.
        label maxGlobalCells = 0;
        //- Split the cells a baffle sheet's FREE edge runs through by the
        //  plane through that edge normal to the sheet, before the sheet's
        //  own pass: a cap the sheet covers only in part otherwise leaves a
        //  covered strip and a phantom strip between the same two cells, and
        //  the thinner one is a skewed face (and a skewed wall once the
        //  baffle is split)
        bool baffleEdgeSplit = true;
        //- Only where the edge leaves the thinner part below this fraction of
        //  the cell's extent across the edge (a strip that skews its face);
        //  an edge through the middle of a cell leaves faces that pass
        scalar baffleEdgeSplitFraction = 0.35;
        //- Clip against carving surfaces
        bool cutCarving = true;
        //- Layers of neighbouring leaves each rank cuts around its own. The
        //  merge rounds' reach limits must fit in them (mergeSmallCells: a
        //  sliver's one layer + 1 + a union's two layers)
        label haloLayers = 4;
        //- This rank (0 without -parallel). Counters count owned cells only,
        //  so their sum over ranks does not depend on the rank count.
        label myRank = 0;
        //- Split cells along baffles
        bool cutBaffles = true;
    };

    //- Cut and coalesce counters count the cells (face pairs) this rank
    //  owns, so their sum over ranks is the mesh's own; nMerged,
    //  nPhantomJoined, nKilled and nMergeDeferred come from rounds every
    //  rank resolves alike and are already the mesh's own on every rank.
    struct report
    {
        label nCarveCut = 0;
        label nCarveFallback = 0;
        label nBaffleCut = 0;
        label nBaffleFallback = 0;
        label nMerged = 0;
        label nQualityMerged = 0;
        //- re-admitted cells dropped again because nothing could split them
        label nReadmitKilled = 0;
        label nCapFaces = 0;
        //- coplanar face pieces between one cell pair joined into one face
        label nCoalesced = 0;
        //- of those, the joins that crossed a patch / zone difference to
        //  absorb a coplanar sliver (coalesceCoplanarFaces' second pass)
        label nTagCoalesced = 0;
        //- cells split by a facet plane ahead of carving (convex-edge cells)
        label nPreSplit = 0;
        //- pre-split halves joined again (both fluid, union convex)
        label nPhantomJoined = 0;
        //- extreme slivers dropped after the merge (see killVolumeFraction)
        label nKilled = 0;
        //- degenerate cells absorbed regardless of exemptions (see degenerateVolumeFraction)
        label nDegenerateAbsorbed = 0;
        //- ... of them, absorbed into the largest-face host although that
        //  union fails a face pyramid, because no host's union kept them all
        //  (mesh-global count)
        label nDegenerateInverted = 0;
        //- wedgeKill round: micro wedges below the y_P floor handed to the
        //  solid after every merge round refused them (mesh-global count)
        label nWedgeKilled = 0;
        //- baffleCrumb round: crumbs against a baffle handed to the solid
        //  (mesh-global count)
        label nBaffleCrumbKilled = 0;
        //- cells split at a baffle sheet's free edge (baffleEdgeSplit)
        label nBaffleEdgeSplit = 0;
        //- thin cells cut along a patch seam of their own boundary face
        //  (splitSeamedThinCells)
        label nSeamSplit = 0;
        //- pre-split rejoins refused for a skewed face (mergeMaxSkew)
        label nSkewRefused = 0;
        //- Sliver host proposals refused for introducing excessive skewness
        label nSkewRejected = 0;
        //- Quality-pass merges accepted below the skew limit only because they
        //  strictly lowered the cell's own worst face skewness (no host could
        //  reach the limit)
        label nSkewRepairBelowLimit = 0;
        //- sliver merges put off to a later round: a cell across a tiny face
        //  of the union had already changed in the same round
        label nMergeDeferred = 0;
        //- slivers not proposed and hosts passed over for reaching too far
        //  from their leaf (owned proposals)
        label nMergeOutOfReach = 0;
        //- corner-wedge clusters merged (a wedge, two halves and their corner cell)
        label nCornerClusters = 0;
        //- repairThinWallCells: stranded thin wall slivers merged in the
        //  relaxed tier (host chosen opposite the thin wall face)
        label nThinWallMerged = 0;
        //- repairThinWallCells: stranded thin wall slivers handed to the solid
        label nThinWallKilled = 0;
        //- repairThinWallCells: thin wall slivers left alone because they carry
        //  a baffle face or touch a rank boundary
        label nThinWallKept = 0;
        //- meshQualityCensus, over the whole mesh (reduced across ranks):
        //  the smallest distance from a plain wall face to its own cell's
        //  centre, the smallest cell volume as a fraction of its hex, and the
        //  smallest face area. GREAT / 1 / GREAT when nothing was measured.
        scalar minWallDistance = GREAT;
        scalar minVolumeFractionSeen = 1;
        scalar minFaceAreaSeen = GREAT;
        //- meshQualityCensus, by boundary class (minWallDistance counts
        //  plain walls only): the same distance for faceZone (baffle) faces
        //  and for non-wall boundary faces (inlet / outlet / opening), each
        //  reduced across ranks. GREAT when there is none.
        scalar minBaffleDistance = GREAT;
        scalar minOpenDistance = GREAT;
        //- meshQualityCensus, thin cells: t = V / A_max is a slab's
        //  thickness, the y its wall function divides by. The smallest t,
        //  the cells below 1 mm, the cells below `thinFloor` (the first-layer
        //  floor the caller passed) and, of those, the ones carrying a
        //  faceZone face and the ones touching no boundary at all;
        //  thinCellsPerPatch attributes each to the largest boundary face it
        //  has (sized like wallPatch, THIS RANK ONLY: patch indices are not
        //  comparable across ranks, the caller gathers by patch name).
        scalar thinFloor = 0;
        scalar minThickness = GREAT;
        label nThinCells1mm = 0;
        label nThinCells = 0;
        label nThinAtBaffle = 0;
        label nThinInterior = 0;
        labelList thinCellsPerPatch;
    };


    // Static Member Functions

        //- Copy the builder's primitives into a cutMesh.
        //  `origAlive` is per builder cell.
        static void fromBuilder
        (
            const dcCartMeshBuilder& builder,
            const dcCartGrid& grid,
            const boolList& origAlive,
            cutMesh& m
        );

        //- Side of every point with respect to the carving surfaces:
        //  +1 fluid, -1 solid, 0 on the surface, -2 on the surface but
        //  reached from no cell centre. A point is fluid when the centre of
        //  some ORIGINAL live cell that owns it reaches it without crossing a
        //  carving surface. Works on bare corner topology: `pointCells` lists
        //  per point the original live cells whose faces hold it (sorted and
        //  deduplicated here), `cellCentre` gives those cells' reference
        //  centres. Only the points in `only` (all when null) that some cell
        //  lists are classified; the rest keep -1.
        static void classifyCorners
        (
            const pointField& points,
            std::vector<std::vector<label>>& pointCells,
            const std::function<point(label)>& cellCentre,
            const dcCartSurfaces& surfaces,
            scalar tolOn,
            std::vector<signed char>& side,
            const std::vector<bool>* only
        );

        //- Split the cells that hold a convex feature edge of shell `sh` by
        //  one of the edge's facet planes, so the carving that follows cuts
        //  each half with one facet (planar cap, convex halves, exact edge).
        static void featurePreSplit
        (
            cutMesh& m,
            const dcCartSurfaces& surfaces,
            label sh,
            const settings& s,
            report& r,
            std::vector<bool>& everSplit,
            bool completePlanes = false
        );

        //- Clip the mesh against the carving surfaces (step 1-4 above).
        static void cutCarving
        (
            cutMesh& m,
            const dcCartSurfaces& surfaces,
            const settings& s,
            report& r
        );

        //- Rename boundary faces that lie IN a carving facet's plane after
        //  a different region: the stair-step naming of a grid-boundary
        //  face beside a wall unit whose STL pokes into the wall. Returns
        //  the number renamed.
        static label relabelCoplanarPatches
        (
            cutMesh& m,
            const dcCartSurfaces& surfaces,
            const settings& s
        );

        //- COLLECTIVE: overwrite the shell-classification `side` of every
        //  point this rank does not own with the value computed by its owner
        //  (lowest cellRank among the live cells containing the point), via a
        //  band exchange with the two slab neighbours. The anchor set behind
        //  a classification is the LOCAL mesh, and the halo ends before the
        //  two-ring anchor reach, so without this a halo rank can classify a
        //  shared point differently from its owner and the two ranks clip
        //  the interface cells differently. Every rank must call this for
        //  every shell pass — including passes whose reach is empty (pass an
        //  empty cutMesh); skipping it deadlocks the others.
        static void syncSideOwners
        (
            cutMesh& m,
            std::vector<signed char>& side,
            //- bit k: rank k holds a cell of the shell's box; only those
            //  ranks take part, each with its holding slab neighbours
            uint64_t sideRanks=~0ULL
        );

        //- One carving shell's clip pass (pre-split, classification, cut) on
        //  the neighbourhood cutCarving hands it
        static void carveShellPass
        (
            cutMesh& m,
            const dcCartSurfaces& surfaces,
            const settings& s,
            report& r,
            label sh,
            const boundBox& sbb,
            std::vector<bool>& everSplit,
            std::vector<signed char>& gside,
            const std::vector<bool>& origSlot,
            const std::function<bool(label)>& preparedPointStar=std::function<bool(label)>(),
            //- the ranks holding a cell of this shell's box (bit k = rank
            //  k): the side exchange runs among them only
            uint64_t sideRanks=~0ULL
        );

        //- One baffle sheet's split pass, on the neighbourhood cutBaffles
        //  hands it
        static void baffleSheetPass
        (
            cutMesh& m,
            const dcCartSurfaces& surfaces,
            const settings& s,
            report& r,
            label sh,
            const boundBox& sbb
        );

        //- Split the cells a free edge of baffle sheet `sh` runs through by
        //  the plane through the edge normal to the sheet (baffleEdgeSplit);
        //  `edgeCells` (when given) marks the cells that split made
        static void baffleEdgePreSplit
        (
            cutMesh& m,
            const dcCartSurfaces& surfaces,
            const settings& s,
            report& r,
            label sh,
            std::vector<bool>* edgeCells = nullptr,
            bool exactOutline = false
        );

        //- Cut a THIN cell along the seam where its own boundary face changes
        //  patch, so each half's face on that plane is a single patch covering
        //  the half exactly. Axis-aligned seams only; cells carrying a baffle
        //  face are left alone.
        static void splitSeamedThinCells(cutMesh& m, const settings& s, report& r);

        //- Split cells along every baffle faceZone.
        static void cutBaffles
        (
            cutMesh& m,
            const dcCartSurfaces& surfaces,
            const settings& s,
            report& r
        );

        //- Merge cells smaller than minVolumeFraction of their hex, then
        //  hand those below killVolumeFraction with no host to the solid.
        //  Runs in rounds that communicate with -parallel and give the
        //  same cells for any number of ranks. A final bandAbsorb round
        //  (MeshGenerator S9e port; toggle DCCART_BAND_ABSORB=0 to disable)
        //  also absorbs THIN-PLATE slabs whose thickness t = V/A_iface is
        //  below h_leaf/8 even when their volume fraction is not small --
        //  the near-coincident/tilted-junction plates the volume trigger and
        //  convexUnionT's 2% margin strand. See the porting plan and the
        //  rank-invariance argument at the round in the definition.
        static void mergeSmallCells
        (
            cutMesh& m,
            const settings& s,
            report& r,
            const dcCartSurfaces* surfaces = nullptr,
            //- per-patch plain-wall flag for the wedgeKill round's y_P
            //  admission (settings::wedgeVolumeFraction); null disables it
            const boolList* wallPatch = nullptr
        );

        //- Pre-capture union of bounded current raw fluid cavities. The
        //  caller supplies the real globally homogeneous normal-carve proof;
        //  every physical ring/patch/zone and actual quality role is preserved.
        static void mergeRawQualityClusters
        (
            cutMesh& m,const wordList& patchTypes,
            const std::vector<std::string>& sourceKeys,label myRank,
            bool homogeneousFluid,const std::function<scalar()>& remainingSeconds
        );

        //- Split every internal face of a zone listed in `masters` into the
        //- master/slave patch pair: the face goes to masters[zone] and a
        //- reversed copy is appended under slaves[zone], so patch face k of
        //- one is the partner of face k of the other -- the order a cyclic
        //- pair needs, and the one createBaffles produces by walking the
        //- zone twice in ascending face order. Which of the two cells is the
        //- MASTER side is decided by the sheet, not by the face's current
        //- owner (see baffleFaceSide): a face pointing the slave's way is
        //- turned first. Zones with masters[z] < 0 are left alone. Returns
        //- the faces split; `nTurned` (optional) counts the faces that had
        //- to be turned, and `undecidedPerZone` (optional, sized to
        //- masters) the faces per zone the geometry could not decide
        //- (baffleFaceSide == 0), which keep their current owner as the
        //- master -- both over the cells this rank owns.
        static label splitBaffleZones
        (
            cutMesh& m,
            const labelList& masters,
            const labelList& slaves,
            const dcCartSurfaces& surfaces,
            label* nTurned = nullptr,
            labelList* undecidedPerZone = nullptr
        );

        //- Which side of its baffle pair zone face `f`, AS IT IS ORIENTED
        //- NOW, belongs to: +1 master, -1 slave, 0 when the geometry cannot
        //- tell (no sheet of the zone, a degenerate face, or a face standing
        //- across the sheet's normal). The master side of a baffle is the
        //- cell the exported normal of the sheet triangle under the face
        //- points into, so a master face points AGAINST that normal -- the
        //- orientation the clip pass gives a sheet's own caps (owner on the
        //- +normal side). A pure function of the face's geometry and the
        //- surfaces: the two ranks holding a processor face see the same
        //- polygon from opposite sides, so they return opposite signs and
        //- agree on which CELL is the master; and every face that returns
        //- non-zero lands on the side its sheet gives, whatever pass made it.
        //- A 0 keeps the caller's fallback (the face's owner, or the rank
        //- order across a rank boundary); the callers count those faces and
        //- warn.
        //- The sign is the opposite of createBaffles on a snappyHexMesh
        //- faceZone (whose masters point ALONG the sheet normal). No boundary
        //- condition notices -- every baffle condition the runner writes is
        //- symmetric in the face normal -- but the mesh preview does: it
        //- shows the master side by default, and here that is the -normal
        //- side (a rack's perforated panels face out of the rack, its U-slot
        //- partitions face up), the other side from a snappy mesh. The
        //- preview draws a sheet whose slave is hidden from both sides.
        static label baffleFaceSide
        (
            const cutMesh& m,
            label f,
            const dcCartSurfaces& surfaces
        );

        //- How far from sheet sh's plane a point still counts as on it, in
        //- the sheet's cut and in everything that asks whether a face lies
        //- in the sheet: the ON tolerance, or far less for an exact outline.
        static scalar baffleSheetPlaneTolerance
        (
            const dcCartSurfaces& surfaces,
            const settings& s,
            label sh
        );

        //- Hand the higher rank's cell across a baffle face to the lower
        //- rank, until no such face is left or the rest cannot move. Only
        //- processor faces move. Returns the cells moved; nRefused counts the
        //- ones whose new rank does not hold them whole. Collective.
        static label gatherBaffleCells(cutMesh& m, label& nRefused);

        //- One point id per position (duplicates from crossings and pieces)
        static void mergeCoincidentPoints
        (
            cutMesh& m, const boundBox* within = nullptr,
            std::vector<label>* flattenedAlias = nullptr,
            const bool activeOnly = false
        );

        //- Rewrite every live cell's cellPiece as a hash of its CURRENT
        //  GEOMETRY (quantised centroid, bounding box and the sorted vertex
        //  coordinates of every face), so the cell's global id (leaf, piece)
        //  is a pure function of geometry again. The history hash the clip
        //  passes stamp is NOT rank-invariant: the owner of an interface
        //  cell and the rank holding it as halo can run the degenerate-split
        //  and splice decisions in different orders (their reaches and
        //  neighbourhoods are cut by the halo edge), arrive at the SAME
        //  final polygons, and still carry different split histories. Every
        //  gid-arbitrated round (mergeSmallCells, refineLongCells) must call
        //  this at entry, before the first gidOf, or the ranks arbitrate on
        //  names that do not denote the same cell.
        static void rekeyPiecesByGeometry(cutMesh& m);

        //- Drop an id listed twice in a row in a face (after the merge)
        static void dedupFaceVertices(cutMesh& m);

        //- Carry a hanging edge vertex into every face of the adjacent cells.
        static label stitchOpenCellEdges(cutMesh& m);

        //- The opposite repair: drop a face corner the cell's other faces do
        //- not share, when doing so closes the cell. `procPatch` marks the
        //- processor patches, whose faces are matched with another rank and
        //- are never trimmed.
        static label dropHangingFaceCorners(cutMesh& m, const labelList& procPatch);

        //- Move a cut point onto a neighbouring point of the same cell when
        //- that takes away a MICRO face of a cell whose boundary faces are
        //- already over the skewness limit. Returns the points moved; the
        //- caller re-runs coalesceCoplanarFaces, which folds the flattened
        //- micro face into the face it now lies in.
        //- `procPatch` marks the processor patches: a point on a rank
        //- interface is shared with another rank's copy of the same face, so
        //- it never moves.
        static label collapseMicroFacePoints
        (
            cutMesh& m,
            const settings& s,
            const labelList& procPatch
        );

        //- Last resort for the faces checkMesh itself refuses: measure every
        //- alive face with checkMesh's own skewness (primitiveMeshTools::
        //- faceSkewness for an internal face, boundaryFaceSkewness for a
        //- boundary, baffle or processor one) and repair the ones over
        //- mergeMaxSkew. Two repairs, each accepted only when it strictly
        //- lowers the number of over-limit faces it can see:
        //-   join  two coplanar boundary faces of ONE cell that share an edge
        //-         and a patch -- the pieces a baffle wall is left in when the
        //-         sheet's two sides are different cells, which no earlier
        //-         pass may join because a face has one neighbour;
        //-   merge the smaller of the two cells of an over-limit internal
        //-         face into a neighbour, concave unions included -- what the
        //-         sliver rounds refuse when the cell is too big to be a
        //-         sliver or the union too bent to be convex.
        //- Returns 0 without touching anything when no face is over the
        //- limit, so a mesh checkMesh already passes is bit-identical.
        //- Runs AFTER splitBaffleZones: the join needs the baffle pieces to
        //- be wall faces of one cell. `cyclicZone[z]` marks the zones whose
        //- pair is cyclic, where master and slave must stay face for face.
        //- `procPatch` marks the processor patches; `coupledPatch[p]` marks
        //- every patch checkMesh measures against the REAL cell on the other
        //- side (polyMeshTools::faceSkewness swaps boundary cell centres for
        //- coupled patches) instead of a mirror image: processor and cyclic
        //- pairs. Those faces are neither measured nor touched here -- one
        //- rank cannot see the other side's cell centre, and joining or
        //- merging a coupled face would leave the two sides disagreeing.
        static label repairSkewFaces
        (
            cutMesh& m,
            const settings& s,
            const labelList& procPatch,
            const boolList& coupledPatch
        );

        //- checkMesh's interpolation weight (-allGeometry, < 0.05): the
        //  internal faces whose two cell centres lie almost on one side of
        //  them are taken off the mesh by uniting their two cells, the smaller
        //  into the larger, when the union keeps every face pyramid, does not
        //  raise a face above the skewness limit and leaves no other face of
        //  it below the weight limit. Rank-local: a processor face is not
        //  measured (the other side's centre is not here) nor removed.
        static label repairLowWeightFaces
        (
            cutMesh& m,
            const settings& s
        );

        //- Terminal repair for the cells the strict merge rounds strand: a
        //- wall-adjacent sliver whose wall face is closer to its own centre
        //- than minWallCellDistance. Tries ONE relaxed merge first, into the
        //- neighbour opposite that wall face so the union grows AWAY from the
        //- wall and y_P rises -- AMReX picks its redistribution stencil by the
        //- EB face normal for exactly this reason (MakeITracker), where dcCart
        //- picks by largest shared area and so grows the union ALONG the wall,
        //- leaving the thin direction thin and every host refused. If no host
        //- passes even the relaxed tier, the cell is handed to the solid.
        //- This deliberately OVERRIDES killExtremeSlivers' thin-closed-shell
        //- exemption: that guard exists so a containment wall is not deleted
        //- (Shinaide's con_* patches came out empty), and it must not protect
        //- a 1e-10 m3 tetrahedron sitting on the OUTSIDE of a duct. The two
        //- exemptions that remain are physical: a cell carrying a baffle face
        //- (deleting it punches a hole in a sheet) and one touching a rank
        //- boundary (the other rank would keep its copy of the face).
        //- `wallPatch[p]` marks the patches of type plain `wall`: only those
        //- carry a law of the wall, so only those have a y that matters. An
        //- inlet or an opening can legitimately have a cell centre microns
        //- from its face (Base604 does, at 4.1e-5, and converges), and
        //- counting it would make the invariant cry wolf on a healthy mesh.
        static void repairThinWallCells
        (
            cutMesh& m,
            const settings& s,
            const labelList& procPatch,
            const boolList& wallPatch,
            report& r
        );

        //- Census of the finished mesh: the smallest wall-face distance (by
        //- boundary class), the smallest cell volume as a fraction of its
        //- hex, the smallest face area, and the thin-cell count below
        //- `thinFloor` (t = V / A_max), each reduced over the ranks. Pure
        //- reporting, no change to the mesh -- the caller turns
        //- minWallDistance into the invariant that fails a run at mesh time
        //- instead of at solve time.
        static void meshQualityCensus
        (
            const cutMesh& m,
            const labelList& procPatch,
            const boolList& wallPatch,
            scalar thinFloor,
            report& r
        );

        //- Cut a large cell parallel to the face it shares with a cell too
        //- small for it, so that the pair meets checkMesh's interpolation
        //- weight and volume ratio. Returns the cells split on this rank.
        static label gradeBesideSmallCells(cutMesh& m, label myRank);

        //- Subdivide very long corner prisms without moving their boundary.
        static label refineLongCells(cutMesh& m, label myRank, scalar maxAspect = 900);

        //- Default-off conservative source-box subdivision experiment.
        static label refineThinAxisBoxes(cutMesh& m, const boolList& protectedPatch, label myRank);

        //- Parallel: drop the halo cells of other ranks; a face between an
        //  owned cell and a halo cell becomes a processor-patch face
        //  (`procPatchOfRank[rank]`, -1 = none: the face is dropped).
        //- Opt-in exact-outline cleanup with all-incidence and coupled veto.
        //- Read-only, opt-in regular transition candidate audit.
        static label decomposeConformingCones
        (
            cutMesh& m, const wordList& patchNames, const wordList& patchTypes,
            const wordList& patchNeighbours, label myRank,
            const Time& runTime, const wordList& faceZoneNames,
            const wordList& patchGroups,
            const weightUnionAuthority& weightAuthority = weightUnionAuthority(),
            const completeCavityAuthority& cavityAuthority = completeCavityAuthority(),
            // PRIVATE explicit argument; normal APP remains default OFF.
            const bool earliestPhysicalPointRepair = false,
            const std::function<bool()>& workAvailable = std::function<bool()>()
        );

        static label auditRegularTransitionRoofs(cutMesh& m, const boolList& coupledPatch);

        static label removeExactZeroBoundaryPairs
        (cutMesh& m, const boolList& plainWall, const boolList& protectedPatch,
         const wordList* originalPatchTypes=nullptr);

        static label removeRedundantEdgePoints(cutMesh& m, const boolList& coupledPatch);

        static void dropGhostCells
        (
            cutMesh& m, label myRank, const std::vector<label>& procPatchOfRank
        );

        //- Join coplanar face pieces between the same two cells (or the
        //  same cell and patch / zone) into one face.
        //  `myRank` only selects what the counter counts.
        //- `plainWall`, when given, is a per-patch flag marking the patches of
        //  type plain `wall`: two coplanar, edge-sharing BOUNDARY faces of one
        //  cell whose patches are both flagged then join even though the patch
        //  NAMES differ, the survivor keeping its own. Opt-in via
        //  DCCART_COOALESCE_WALLS; see the definition for the measurement.
        static void coalesceCoplanarFaces
        (
            cutMesh& m,
            report& r,
            label myRank = 0,
            const boolList* plainWall = nullptr
        );

        //- Give every face a tet decomposition checkMesh accepts. checkMesh
        //  -allGeometry needs, per face, a base vertex whose fan has no
        //  zero-area triangle (findBasePoint / findSharedBasePoint), and the
        //  face-centre tets of a non-convex face turn inside out at its reflex
        //  corner. A cut face with hanging vertices on every side (a refined
        //  neighbour along each edge) has no such vertex; a cut face around a
        //  frame post's corner is L-shaped. A non-convex face is split along
        //  diagonals into convex pieces (no new point); a piece with no base
        //  vertex is fanned about its own centre (one new point, in the face).
        //  Pieces keep the face's cells, patch and zone. Processor faces
        //  (skip[patch]) are left alone. Pure geometry, so every rank holding
        //  a face splits it alike. Returns the faces split on this rank.
        static label repairFaceTets(cutMesh& m, const boolList& skip);

        //- Shared normal numeric lowering; mutates only the supplied working
        //  mesh. No patch MPI/IO or permissions. Local callers must use owned
        //  scratch, not live state or a guessed compact-cell ordering.
        static void lowerWriterNumeric
        (
            cutMesh& m, const wordList& patchNames, const wordList& patchTypes,
            const wordList& faceZoneNames, writerNumericData& out,
            writerBindings* bindings = nullptr
        );

        //- Compact, order and build the polyMesh.
        //- `patchNeighbours[p]` is the cyclic partner of patch p (null for
        //  every other type) and `patchGroups[p]` the extra inGroups entry
        //  createBaffles would have given it -- both are what makes a baffle
        //  pair written here indistinguishable from one written by
        //  createBaffles.
        static autoPtr<polyMesh> toPolyMesh
        (
            cutMesh& m,
            const Time& runTime,
            const word& regionName,
            const wordList& patchNames,
            const wordList& patchTypes,
            const wordList& faceZoneNames,
            const wordList& patchNeighbours = wordList(),
            const wordList& patchGroups = wordList(),
            const bool baselineOnly = false,
            writerBindings* bindings = nullptr,
        // Empty disables the experimental installed-point repair stage.
        const std::function<scalar()>& remainingPointRepairSeconds = std::function<scalar()>(),
        // Runner source proof; absent/invalid contracts default to refusal.
        const bool singletonSourceRoleEligible = false,
        // Optional read-only construction patch -> final boundary address map.
        labelList* writtenPatchAddressMap = nullptr
        );

        //- Volume of a cell from its faces (outward-oriented sum)
        static scalar cellVolume(const cutMesh& m, label c);
};

} // End namespace Foam

#endif
