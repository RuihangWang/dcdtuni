/*---------------------------------------------------------------------------*\
  dcCartSurfaces — the STL geometry, as the mesher needs to see it.

  Wraps one triSurfaceMesh per STL file and flattens their named regions into a
  single global list, because a region is exactly what becomes a boundary patch:
  a multi-solid ASCII STL carries one `solid <name>` block per boundary region,
  and the whole point of the pipeline is that each of those ends up addressable
  by a boundary condition.

  Only three questions are asked of the geometry, and each maps to one octree
  query on the surface's own search tree:

    levelInBoxAboveCand
                       does anything cross this cell, and how fine must it be?
                       -> drives refinement
    blockingRegionMasked
                       does the segment between two cell centres cross a
                       surface, and whose region did it hit?
                       -> drives the carve AND names the patch in one shot
    isInside           is this point inside a closed surface?
                       -> resolves which side of a wall a seed lies on

  Surfaces flagged `patchesOnly` (a through-zone, say) still answer
  levelInBoxAboveCand — they exist to drive refinement — but never block a
  segment and never claim a patch.

\*---------------------------------------------------------------------------*/

#ifndef dcCartSurfaces_H
#define dcCartSurfaces_H

#include "triSurfaceMesh.H"
#include "searchableSurface.H"
#include "PtrList.H"
#include "wordList.H"
#include "labelList.H"
#include "boundBox.H"
#include "treeBoundBox.H"
#include "labelPair.H"
#include "dictionary.H"
#include "fileName.H"
#include "string.H"
#include "Time.H"
#include <vector>
#include <unordered_map>
#include <cstdint>

namespace Foam
{

//- A triangle's separating-axis data for box overlap tests: its corners, the
//  axes boundBox::intersects projects onto (the area normal, and each edge
//  crossed with each unit axis) and the triangle's extent on them, computed
//  once with the products and sums that test repeats on every call.
struct dcCartTriSat
{
    point a, b, c;
    vector n;               //!< 0.5*((b - a)^(c - a))
    scalar tn = 0;          //!< n & a
    vector axis[9];         //!< (c-b, a-c, b-a) ^ (x, y, z)
    scalar lo[9], hi[9];    //!< triangle extent along axis[k]
};


class dcCartSurfaces
{
public:

    //- What one STL file contributes
    struct surfaceInfo
    {
        word name;              //!< file stem, e.g. merged_acu
        fileName stlFile;        //!< actual geometry dictionary file key
        label minLevel = 0;     //!< refinement level for cells it crosses
        label maxLevel = 0;     //!< level at sharp-feature cells
        bool carves = true;     //!< may remove cells / claim boundary faces
        //- `faceType internal` in the dict: a ZERO-THICKNESS baffle. It must
        //  NOT remove cells — fluid lives on both sides — and the faces it
        //  crosses become internal faces in `faceZone`, which createBaffles
        //  later splits into a cyclic master/slave pair.
        bool internalBaffle = false;
        bool wallBaffle = false; //!< split directly into independent wall sides
        word faceZone;          //!< zone name when internalBaffle
        label regionOffset = 0; //!< first global region id
        label nRegions = 0;
    };

    //- A baffle SHEET: the connected, coplanar part of a baffle surface. A
    //  slanted duct made of twelve baffles is twelve sheets; each is split
    //  against ITS OWN plane, so which way the STL normals happen to point
    //  cannot flip a cell's sides.
    struct baffleSheet
    {
        label zone;         //!< faceZone index
        point origin;
        vector normal;      //!< unit
        boundBox bounds;
    };


    // Export provenance only; never used to merge sheets or alter geometry.
    struct sourceSurfaceIdentity
    {
        string sourceObjectUid;
        string sourceFaceUid;
        word representation;
    };


private:

    List<List<sourceSurfaceIdentity>> sourceIdentities_;

    // Private data

        PtrList<triSurfaceMesh> surfaces_;
        List<surfaceInfo> info_;

        // Membership only: original surface/triangle order, built after welds.
        // Coordinates are read afresh, so these queries never cache geometry.
        std::unordered_map<label, std::vector<labelPair>> regionTris_;
        mutable label nRegionAreaAudit_ = 0;
        mutable label nRegionPlaneAudit_ = 0;
        mutable label nRegionThinAudit_ = 0;

        //- Global region id -> patch name / type, in patch order
        wordList patchNames_;
        wordList patchTypes_;

        //- Global region id -> owning surface index
        labelList regionSurface_;

        //- Refinement SHELLS: closed volumes (searchableBox,
        //  searchableRotatedBox, ...) that raise the level of the cells inside
        //  them. Distinct from carving surfaces: a shell never removes a cell
        //  and never claims a patch, it only says "be finer here".
        PtrList<searchableSurface> shells_;
        labelList shellLevel_;
        //- Bounding box per refinement shell: a point outside it is not inside
        List<boundBox> refineShellBounds_;

        //- Distinct faceZone names, in the order the builder writes them
        wordList faceZoneNames_;

        //- SOLID SHELLS: the connected components of the carving triangles
        //  (triangles sharing a vertex). A box is one shell of six regions;
        //  the four room walls one open shell. The cut-cell pass clips the
        //  mesh against one shell at a time, so a point's side is asked of
        //  ONE closed body — never confused by another surface it happens to
        //  lie on (a wall-mounted unit's corners on the wall plane).
        labelList shellSurface_;            //!< shell -> surface index
        List<labelList> triShell_;          //!< per surface: triangle -> shell
        List<boundBox> shellBounds_;
        boolList shellClosed_;              //!< every edge shared by 2 triangles
        //- per shell: its triangles as (surface, triangle) pairs
        List<List<labelPair>> shellTris_;
        //- baffle sheets and their triangles
        List<baffleSheet> sheets_;
        List<List<labelPair>> sheetTris_;

        //- Deepest level any surface or shell asks for
        label maxLevel_;

        //- Coordinate canonicalisation (snapSurfaceCoordinates): the tolerance
        //  in force (0 = off) and, per axis, the sorted canonical values the
        //  near-duplicate surface coordinates were rewritten onto.
        scalar coordSnapTol_ = 0;
        label nCoordSnapped_ = 0;
        std::vector<scalar> snapCanon_[3];


    // Private Member Functions

        scalar regionAreaReference(label globalRegion) const;
        bool regionPlaneReference(label globalRegion, vector& n, boundBox& bb) const;
        bool regionIsThinSheetReference(label globalRegion) const;

        dcCartSurfaces(const dcCartSurfaces&) = delete;
        void operator=(const dcCartSurfaces&) = delete;

        //- Rewrite every surface vertex whose coordinate shares a cluster
        //  with another onto the cluster's canonical value, and clear the
        //  surfaces' cached trees. Must run before ANY point-derived data is
        //  built (shell bounds, baffle sheet planes, triangle boxes).
        void snapSurfaceCoordinates(scalar tol);

        //- Rewrite every surface vertex that lies within `tol` of a level
        //  `maxLevel_` grid plane onto that plane's exact coordinate. A
        //  surface 0.2 mm off a grid plane otherwise cuts a 0.2 mm hairline
        //  cell that no merge can absorb, and checkMesh scores its 2 x 0.2 mm
        //  wall face 204 (it normalises by max(0.4|d|, the face's own reach),
        //  and |d| there is 0.08 mm). This is FDS's SNAP_GEOM_NODES run in the
        //  other direction -- it rewrites the surface node onto the cutting
        //  plane's analytic value (geom.f90:15662, :15738-15740) -- at the
        //  0.01 scale that VTK's MergeTolerance, CGAL's snap_grid_size and the
        //  ISR vertex-to-edge bound independently converge on.
        void snapToGridPlanes(const class dcCartGrid& grid, scalar tol);

        //- Near-coincidence weld (DAML thin-wall wedge WP, route 1). Rewrites
        //  the surfaces so no planar patch NEARLY touches the leaf grid:
        //
        //    G1  an axis-aligned planar patch whose coordinate lies within
        //        (1e-6, weldTol] of a level-maxLevel_ grid plane has its
        //        whole coordinate CLASS rewritten onto that plane (every
        //        vertex of every surface at that exact coordinate moves, so
        //        boxes stay watertight). This is snapToGridPlanes extended
        //        from the vertex-neighbourhood scale to the measured pathology
        //        band: a plane t off a grid plane carves a film of cells with
        //        y_P ~ t/2, so every t < 2 x the 5.0e-4 wall-function floor
        //        must become exact incidence (or nothing).
        //    G2  a TILTED planar patch whose plane passes within
        //        (1e-6, weldTol] of a grid POINT (the finest lattice inside
        //        its bounds) is translated along its normal onto exact
        //        incidence with that point. A tilted plane epsilon off a grid
        //        line/corner carves micro-wedges of scale epsilon along the
        //        whole graze (the DAML duct-expand baffle row at y_P 2.5e-5
        //        and the spiral duct-riser tetrahedra at y_P 1.3e-4); at exact
        //        incidence the fragment degenerates and the ON classification
        //        retires it.
        //
        //  Vertex moves are keyed on EXACT positions and applied to every
        //  surface, so a junction partner follows its patch and the solids
        //  stay watertight; a vertex claimed by two patches takes the first
        //  claim in a fixed order (G1 by axis then coordinate, G2 by surface
        //  then first triangle) and a later round re-measures the loser.
        //  Runs at most `rounds` rounds, stopping at a round that moves
        //  nothing. Pure function of the surface coordinates and the grid --
        //  identical on every rank, which is what keeps the any-np == serial
        //  red line. A guard drops a G1 pair that would collapse one solid's
        //  two opposing faces onto the same plane.
        //
        //  First-layer floor (`firstLayerMinFraction` > 0): the G1 band is
        //  widened PER COORDINATE CLASS to
        //      max(weldTol, min(firstLayerMinFraction * h_L, firstLayerMax))
        //  where h_L is the spacing of the finest level L the class's own
        //  surfaces refine to, measured against the level-L planes (every
        //  coarser level's planes are a subset, so a class left in place is
        //  at least that far from every plane a cell can put a face on).
        //  After the pass no axis-aligned surface carves a slab thinner than
        //  its floor: the first cell off every such wall is either a full
        //  cell (the surface is ON a plane) or a cut at least the floor
        //  thick -- the snap-to-grid first-layer guarantee, at the cut-cell
        //  scale. A class the floor leaves alone still gets the classic
        //  maxLevel_ test. Two DIFFERENT solids facing each other across a
        //  gap, both marked onto one plane between them, are sealed only
        //  when the classic band would have sealed them or the gap is at
        //  most `firstLayerSealGap` (< 0: the weld tolerance); otherwise
        //  both stay put and the pair is counted in nWeldGap(). Off (0)
        //  leaves the classic weld bit-for-bit.
        void weldNearCoincident
        (
            const class dcCartGrid& grid,
            scalar weldTol,
            label rounds,
            scalar firstLayerMinFraction = 0,
            scalar firstLayerMax = 0,
            scalar firstLayerSealGap = -1
        );

        //- weldNearCoincident statistics (0 = off / nothing welded)
        label nWeldRounds_ = 0;
        label nWeldCoords_ = 0;   //!< G1 coordinate classes rewritten
        label nWeldPatches_ = 0;  //!< G2 tilted patches translated
        label nWeldVerts_ = 0;    //!< vertices moved, all rounds
        label nWeldGuard_ = 0;    //!< G1 pairs dropped by the collapse guard
        scalar weldMaxShift_ = 0; //!< largest vertex displacement
        label nWeldFloor_ = 0;    //!< G1 classes moved by the first-layer floor only
        label nWeldGap_ = 0;      //!< facing pairs kept apart by the gap guard
        label nWeldSealed_ = 0;   //!< facing pairs sealed onto one plane
        scalar firstLayerFloor_ = 0; //!< floor in force at the finest level (m)

        //- Per surface: each triangle's bounding box, the one the surface
        //  octree builds for its overlap test (filled on first use)
        mutable List<List<boundBox>> triBb_;
        const List<boundBox>& triBounds(label i) const;

        //- Per surface: each triangle's separating-axis data (first use)
        mutable List<std::vector<dcCartTriSat>> triSat_;
        const std::vector<dcCartTriSat>& triSats(label i) const;

        //- False when surface i's findLine(p0, p1) cannot hit (both ends
        //  beyond one face of its octree's box)
        bool segmentMayHit(label i, const point& p0, const point& p1) const;

        //- Per surface: the triangle boxes grown well past the intersection
        //  tolerance (a segment beyond one face of it cannot hit)
        mutable List<List<boundBox>> triBbGrown_;
        const List<boundBox>& grownBounds(label i) const;

        //- triSurfaceMesh::findLine for one segment: the same octree walk and
        //  the same triangle test, skipping triangles whose grown box the
        //  segment cannot reach
        pointIndexHit findLineFast(label i, const point& p0, const point& p1) const;

        //- indexedOctree::findBox(bb) of surface i, element for element and in
        //  its order (the order of the HashSet it collects into), without
        //  building the set
        void findBoxToc(label i, const treeBoundBox& bb, std::vector<label>& out) const;

        //- Buckets over the refinement shells' bounds (filled on first use):
        //  the shells whose bounds can contain a point
        mutable bool shellGridBuilt_ = false;
        mutable boundBox shellGridBb_;
        mutable scalar shellGridH_ = 1;
        mutable label shellGridN_[3] = {1, 1, 1};
        mutable std::vector<std::vector<label>> shellGridCells_;
        void buildShellGrid() const;

        //- per baffle sheet: its triangle ids, sorted (filled on first use)
        mutable List<std::vector<label>> sheetMembers_;
        const std::vector<label>& sheetMembers(label sheet) const;

        //- triSurfaceMesh::overlaps, stopping at the first triangle
        bool anyOverlapCached(label i, const treeBoundBox& bb) const;

        //- The triangles of surface i overlapping bb (octree), appended to
        //  `out` as candidate entries in ascending triangle order
        void collectOverlapCached(label i, const treeBoundBox& bb, std::vector<uint64_t>& out) const;

        //- The entries of one surface's run [p, q) overlapping bb, appended
        void filterRun(label i, const treeBoundBox& bb, const uint64_t* p, const uint64_t* q, std::vector<uint64_t>& out) const;


public:

    // Constructors

        //- Read the `geometry` and `refinementSurfaces` blocks of a dict.
        //  Region names come from `refinementSurfaces.<surface>.regions` when
        //  present, else from the STL's own solid names — the same rule
        //  snappyHexMesh applies, so patch names match what the pipeline
        //  already binds boundary conditions to.
        //- `backgroundSpacing` is the background grid's median cell size; it
        //  only scales the coordinate snap (see snapSurfaceCoordinates), and
        //  0 turns the snap off.
        //- `weldTolerance` (metres, 0 = OFF, the default) enables the
        //  near-coincidence weld — see weldNearCoincident. Opt-in because it
        //  rewrites surface coordinates at up to `weldTolerance` scale, which
        //  changes the mesh of any case whose surfaces graze the grid; cases
        //  validated before the weld keep their exact baseline behaviour.
        //  `weldRounds` caps the re-measurement rounds (see the method).
        //  `firstLayerMinFraction` / `firstLayerMax` / `firstLayerSealGap`
        //  switch on the first-layer floor (see the method; 0 = off).
        dcCartSurfaces
        (
            const Time& runTime,
            const dictionary& geometryDict,
            const dictionary& refinementSurfacesDict,
            const dictionary& refinementRegionsDict = dictionary::null,
            const scalar backgroundSpacing = 0,
            const scalar coordSnapFraction = 1e-4,
            const class dcCartGrid* grid = nullptr,
            const scalar gridSnapFraction = 0.01,
            const scalar weldTolerance = 0,
            const label weldRounds = 3,
            const scalar firstLayerMinFraction = 0,
            const scalar firstLayerMax = 0.005,
            const scalar firstLayerSealGap = -1
        );


    // Member Functions

        //- Replace optional source provenance after validating literal STL
        //  file/region keys. Unlisted regions keep no identity. Returns count.
        label readSourceSurfaceIdentities(const dictionary& sourceSurfaces);

        //- Surface index and LOCAL STL region index (not global patch id).
        //  Null when provenance is absent; invalid indices are rejected.
        const sourceSurfaceIdentity* sourceIdentity
        (
            label surfaceI,
            label regionI
        ) const;

        // Access

            inline label size() const { return surfaces_.size(); }
            inline const List<surfaceInfo>& info() const { return info_; }
            inline const wordList& patchNames() const { return patchNames_; }
            inline const wordList& patchTypes() const { return patchTypes_; }
            inline label nPatches() const { return patchNames_.size(); }
            inline label maxLevel() const { return maxLevel_; }

            //- The coordinate snap tolerance in force (0 = off), and how many
            //  distinct surface coordinates it rewrote
            inline scalar coordSnapTol() const { return coordSnapTol_; }
            inline label nCoordSnapped() const { return nCoordSnapped_; }

            //- weldNearCoincident statistics (see the private members)
            inline label nWeldRounds() const { return nWeldRounds_; }
            inline label nWeldCoords() const { return nWeldCoords_; }
            inline label nWeldPatches() const { return nWeldPatches_; }
            inline label nWeldVerts() const { return nWeldVerts_; }
            inline label nWeldGuard() const { return nWeldGuard_; }
            inline scalar weldMaxShift() const { return weldMaxShift_; }
            inline label nWeldFloor() const { return nWeldFloor_; }
            inline label nWeldGap() const { return nWeldGap_; }
            inline label nWeldSealed() const { return nWeldSealed_; }
            //- The first-layer floor in force at the finest level, metres
            //  (weldTolerance when the floor is off; 0 when the weld is off)
            inline scalar firstLayerFloor() const { return firstLayerFloor_; }

            //- `p` with every coordinate that lies within `coordSnapTol()` of
            //  a canonical surface coordinate replaced by it. Lets a cut point
            //  land bit-exactly on the plane it was cut from.
            point snapPoint(const point& p) const;

            //- Overall bounding box of every carving surface
            boundBox bounds() const;


        // Queries

            //- Candidate lists for hierarchical box queries: entries
            //  (surface << 32 | triangle) grouped by ascending surface. A
            //  surface whose triangles are not narrowed down yet has the one
            //  entry (surface << 32 | 0xFFFFFFFF), answered by its octree.
            //  A box inside another can only overlap triangles overlapping
            //  the outer one, so a cell hands its children what it found.
            std::vector<uint64_t> refinementCandidates() const;

            //- The carving surfaces and the faceZone baffles (the surfaces a
            //  cell-centre segment is asked about), one sentinel each
            std::vector<uint64_t> proximityCandidates() const;

            //- Bit mask of the carving surfaces (bit i = surface i, i < 64)
            uint64_t carvingBits() const;

            //- True when the deepest level demanded by any surface
            //  overlapping `bb` is above `above` (the refinement decision).
            //  A surface demands its `level (min max)` FIRST entry: snappy
            //  refines a merely-intersected cell to `min` and only reaches
            //  `max` where the intersection angle exceeds resolveFeatureAngle.
            //  Using `max` everywhere inflated SingleRack 31x.
            //  [cb, ce) is a candidate list holding every triangle that can
            //  overlap `bb`. `keep` gets the list for the children:
            //  the overlapping triangles of each surface the decision tested,
            //  the given entries of the surfaces it skipped or did not reach.
            bool levelInBoxAboveCand
            (
                const boundBox& bb,
                label above,
                const uint64_t* cb,
                const uint64_t* ce,
                std::vector<uint64_t>& keep
            ) const;

            //- The candidates overlapping bb, appended to `out`
            void filterCand(const treeBoundBox& bb, const uint64_t* cb, const uint64_t* ce, std::vector<uint64_t>& out) const;

            //- True when a refinement shell containing `pt` demands a level
            //  above `above`. `mode inside` is tested at the cell CENTRE, as
            //  snappy does.
            bool shellLevelAbove(const point& pt, label above) const;

            inline label nShells() const { return shells_.size(); }

            //- Global region id of the nearest CARVING surface the segment
            //  p0 -> p1 crosses, or -1 when it crosses nothing. Baffles are
            //  invisible here: they do not block the carve. Only the surfaces
            //  whose bit is set in `mask` are asked (all of them when there
            //  are more than 64). A surface whose triangles miss every box
            //  the segment lies in cannot block it.
            label blockingRegionMasked(const point& p0, const point& p1, uint64_t mask) const;

            //- Index of the faceZone whose baffle the segment p0 -> p1
            //  crosses, or -1. Zone indices address faceZoneNames(). Only
            //  the surfaces whose bit is set in `mask` are asked (all of them
            //  when there are more than 64).
            label crossedFaceZoneMasked(const point& p0, const point& p1, uint64_t mask) const;

            //- Bit i set when surface i has a candidate overlapping bb
            uint64_t overlapMaskCand(const treeBoundBox& bb, const uint64_t* cb, const uint64_t* ce) const;

            inline const wordList& faceZoneNames() const { return faceZoneNames_; }

            //- True when `pt` is inside closed carving surface `surfI`.
            bool isInside(label surfI, const point& pt) const;


        // Cut-cell queries (dcCartCutCells)

            //- Nearest CARVING hit along p0 -> p1. Returns false when the
            //  segment crosses nothing; else `hit` is the point and `region`
            //  the global region id (the patch the cut face belongs to).
            bool carvingHit
            (
                const point& p0, const point& p1,
                point& hit, label& region
            ) const;

            //- Distance from `pt` to the nearest carving surface (GREAT when
            //  there is none). `maxDist` bounds the search.
            scalar nearestCarvingDistance(const point& pt, scalar maxDist) const;

            //- Global region id of the carving surface nearest to `pt`
            //  (-1 when there is none). For a cut face that has no crossing
            //  of its own to name its patch.
            label nearestCarvingRegion(const point& pt) const;

            //- nearestCarvingRegion, searched within `probe` of pt first: when
            //  the nearest found lies inside it, every surface that could win
            //  or tie does too; otherwise the full search
            label nearestCarvingRegionProbe(const point& pt, scalar probe) const;

            //- Global region id of a carving surface that passes through
            //  `pt` (within `radius`) with its facet PARALLEL to `n`, the
            //  nearest one when several do (`current` on a tie); -1 when
            //  none. A face on the wall plane beside a wall unit whose STL
            //  pokes into the wall is named by the wall, not by the unit's
            //  side; a floor face beside a rack's foot by the floor.
            label coplanarCarvingRegion
            (
                const point& pt, const vector& n, scalar radius, label current = -1
            ) const;

            //- Nearest hit along p0 -> p1 on the baffle surfaces of faceZone
            //  `zone`. False when none is crossed.
            bool baffleHit
            (
                label zone, const point& p0, const point& p1, point& hit
            ) const;

            //- Which side of faceZone `zone`'s baffle `pt` lies on: +1 / -1
            //  by the sign of (pt - nearest) . normal, 0 when within `tolOn`
            //  of the surface. -2 when the zone has no surface.
            label baffleSide(label zone, const point& pt, scalar tolOn) const;

            //- True when a baffle of faceZone `zone` overlaps `bb`.
            bool baffleOverlaps(label zone, const boundBox& bb) const;

            //- Plane of the baffle triangle nearest to `pt` (for extending a
            //  baffle that ends inside a cell to the cell's boundary).
            //  False when the zone has no surface.
            bool baffleNearestPlane
            (
                label zone, const point& pt, point& origin, vector& normal
            ) const;

            //- Same, over the carving surfaces.
            bool carvingNearestPlane
            (
                const point& pt, point& origin, vector& normal
            ) const;

            //- One surface triangle, for clipping a cut face by the patch
            //  regions it spans.
            struct triInfo
            {
                point p0, p1, p2;
                vector normal;      //!< unit, outward (into the fluid)
                label region;       //!< global region id
            };

            //- Every CARVING triangle whose bounding box overlaps `bb`.
            void carvingTrianglesInBox
            (
                const boundBox& bb, std::vector<triInfo>& out
            ) const;

        // Baffle sheets (dcCartCutCells)

            inline label nBaffleSheets() const { return sheets_.size(); }
            inline const baffleSheet& baffleSheetInfo(label s) const { return sheets_[s]; }
            //- Nearest hit of sheet `s` along p0 -> p1
            bool baffleSheetHit
            (
                label s, const point& p0, const point& p1, point& hit
            ) const;
            //- True when sheet `s` overlaps `bb`
            bool baffleSheetOverlaps(label s, const boundBox& bb) const;
            //- Total triangle area of sheet `s` (the footprint the mesh's
            //  faceZone faces have to tile).
            scalar baffleSheetArea(label s) const;

            //- The part of sheet `s` the mesh CAN tile: triangle area whose
            //  centroid is not buried inside a closed carving shell (a baffle
            //  sheet running through a rack can never become a faceZone face
            //  there, and counting it would report a hole where there is none).
            //- As above, but only counting triangle area inside `domain`
            //  (the background grid box): a baffle sheet that pokes through
            //  the room shell has footprint outside the fluid domain that no
            //  mesh can ever tile, and counting it reports a phantom hole.
            scalar baffleSheetExposedArea(label s, const boundBox& domain) const;

            //- The part of sheet `s` the mesh MUST tile: the area inside
            //  `domain` that is neither inside a solid body nor already closed
            //  by a carving surface of its own.
            //
            //  An imprecise model puts a containment baffle coplanar with the
            //  rack face it seals against (Shinaide: x=1.4907375 is both con_f
            //  and Rack_A_01's side, y=2.047 is both con_b_copy and every
            //  rack's back). There the mesh puts the RACK's wall in the sheet
            //  plane, no baffle face can exist, and counting that area reports
            //  a hole where the mesh is in fact sealed. Two probes decide:
            //
            //    inside a body   +-`probeDist` along the normal lands in a
            //                    closed shell at least 50 mm across, so the
            //                    sheet runs through a rack/server/ACU
            //    on a surface    a carving surface passes within `onTol` of the
            //                    point, so the plane carries that surface's
            //                    own wall face
            //
            //  A point that is neither is fluid on both sides with nothing in
            //  between: a real leak. Sheet triangles are subdivided adaptively
            //  (only the open/sealed boundary is refined, down to `minTriArea`)
            //  and every probe is memoised on the point's exact bits, so a
            //  wholly open sheet costs one test per triangle.
            scalar baffleSheetOpenArea
            (
                label s,
                const boundBox& domain,
                scalar probeDist,
                scalar minTriArea,
                scalar onTol
            ) const;

            //- Triangle area of one surface region (global region id).
            scalar regionArea(label globalRegion) const;

            // One compact receipt after every compared query has passed.
            void reportRegionQueryAudit() const;

            //- Area of a thin-sheet region that is BURIED IN SOLID-SOLID
            //  CONTACT: the part of its footprint covered by coplanar
            //  triangles of ANOTHER thick carving shell (a 1U switch stacked
            //  flush under a 2U server, a pedestal standing on the floor).
            //  No mesh face can exist in the contact — the two solids seal
            //  each other — so the thin-sheet coverage gate must not count
            //  it as a hole. The partner must be a DIFFERENT shell at least
            //  20 mm thick across (a zero-thickness panel seals nothing,
            //  and one shell's own coplanar overlaps are CSG hole/partner
            //  pairs or welded-stack duplicate rects, handled elsewhere);
            //  coplanarity is |distance to the region's plane| <=
            //  `contactTol` for all three vertices and |n·nR| >= 0.99 —
            //  triangle winding is NOT trusted, exports mix inward and
            //  outward normals per face. The overlap is an exact 2D
            //  triangle-triangle clip in the shared plane. Pure function of
            //  the geometry: every rank computes the identical number and
            //  the result does not depend on the decomposition.
            scalar regionContactArea
            (
                label globalRegion,
                scalar contactTol
            ) const;

            //- True when the region's shell is a thin sheet (min bbox span
            //  < 50 mm): a baffle/blanking-panel solid, as opposed to a box.
            bool regionIsThinSheet(label globalRegion) const;

            //- Area-weighted mean normal and triangle bounding box of a
            //  region, for the coplanar-coverage test in the thin-sheet gate.
            //  False when the region has no triangles.
            bool regionPlane
            (
                label globalRegion,
                vector& n,
                boundBox& bb
            ) const;

            //- True when `p` lies inside a CLOSED shell that is a thin sheet
            //  (min bbox span < 50 mm): a baffle/containment solid. Cells of
            //  such a solid are slivers against the reference hex volume and
            //  must never be handed to the sliver killer, or the baffle
            //  vanishes from the mesh and the aisles it seals connect.
            bool pointInThinSheetShell(const point& p) const;

            //- Every triangle of sheet `s` whose bounding box overlaps `bb`
            void baffleSheetTrianglesInBox
            (
                label s, const boundBox& bb, std::vector<triInfo>& out
            ) const;

        // Solid shells (dcCartCutCells)

            inline label nSolidShells() const { return shellSurface_.size(); }
            inline const boundBox& solidShellBounds(label sh) const { return shellBounds_[sh]; }
            inline bool solidShellClosed(label sh) const { return shellClosed_[sh]; }

            //- Every hit of shell `sh` along p0 -> p1, ordered from p0.
            //  Returns the points and their global region ids.
            void solidShellHits
            (
                label sh, const point& p0, const point& p1,
                std::vector<point>& hits, std::vector<label>& regions,
                std::vector<vector>* normals = nullptr
            ) const;

            //- Triangles of shell `sh` overlapping `bb`.
            void solidShellTrianglesInBox
            (
                label sh, const boundBox& bb, std::vector<triInfo>& out
            ) const;

            //- True when `pt` is inside CLOSED shell `sh` (false for an
            //  open shell): the surface's own inside test, then the nearest
            //  triangle names the shell.
            bool solidShellInside(label sh, const point& pt) const;

            //- Which side of shell `sh` `pt` lies on: 0 within `tol` of it,
            //  -1 behind its nearest triangle(s) (solid), +1 in front
            //  (fluid). Among triangles tied for nearest — an edge, a
            //  corner, two coincident faces of touching boxes — ONE saying
            //  "behind" is enough: the point is inside the union. Works for
            //  open shells too: behind the four room walls is outside the
            //  room. `region` is the nearest triangle's.
            label solidShellSide
            (
                label sh, const point& pt, scalar tol, label& region
            ) const;

            //- Generalized winding number of shell `sh` at `pt`
            //  (Jacobson et al. 2013): the solid angle the shell subtends,
            //  over 4 pi. +-1 deep inside a closed shell, 0 well outside,
            //  fractional near a hole — a box exported without one face
            //  still reads ~0.8 inside.
            scalar solidShellWinding(label sh, const point& pt) const;

            //- Distance from `pt` to shell `sh` (GREAT beyond `maxDist`),
            //  and the region of the nearest triangle (-1 when none).
            scalar solidShellDistance
            (
                label sh, const point& pt, scalar maxDist, label& region
            ) const;


        // Info

            string summary() const;
};


} // End namespace Foam

#endif
