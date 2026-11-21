# dcCartMesh — cut-cell Cartesian mesh generator

> **Directory name (2026-09-22).** This tree was `third_parties/snappyhex` until
> 2026-09-22 and is now `third_parties/dcCartMesh`, to say what it is. The
> built binaries carry an ABSOLUTE rpath to `platforms/<tag>/lib`, so after
> any move or rename of this directory run `./deploy.sh` again (it wipes the
> stale wmake dependency trees first when they point elsewhere: `wclean` the
> four `Make/` dirs or delete `Make/linux64*`). The runner also puts every
> `platforms/*/lib` on `LD_LIBRARY_PATH` when it launches a repo-built mesher,
> so a stale rpath no longer stops a mesh job.


A purpose-built cut-cell mesher for data-centre CFD, and the runner's default
mesh engine (`meshConfig.meshEngine: dcCart`, the default since 2026-10-12). An
axis-aligned background grid is refined around the geometry by an octree; the
cells a surface crosses are cut by the STL itself (one clip pass per connected
shell, baffle sheets kept as internal faces), slivers are merged into
neighbours, and the polyMesh is assembled once. **No surface snapping and no
stair-step boundary.** It runs on N MPI ranks with one algorithm; one rank is
the same code without mpirun (see "Parallel run").

**Status (2026-10-21).** The ten release cases — Base2-9, Base604, Shinaide,
CMI-China, STDCT, Slab, Tri_angle, Base_leakage, DAML-Testbed and
Shinaide_Liquid — mesh through the React editor's mesh button and pass strict
`checkMesh -allTopology -allGeometry`; the receipts are summarised in
`docs/benchmarks/air-release-cases-2026-10-21/`. Mesh time is a soft
five-minute target: the runner writes `timeLimitSeconds 0`, times the run and
logs a warning when it is slower; it never aborts the mesh for time.

Built from source in this directory. See `deploy.sh`. The table of environment
switches is under "Environment switches" below.

```bash
cd src/backend/aircooling/third_parties/dcCartMesh
./deploy.sh                 # check env -> build -> smoke test
source env.sh               # puts dcCartMesh on PATH
dcCartMesh -overwrite       # in a case dir, reads system/dcCartMeshDict
```

---

## Why not just run snappyHexMesh with `snap off`

That is what the pipeline did before, and it is not the same thing.

snappyHexMesh discovers its refinement **iteratively**: mark cells, refine,
rebuild the entire mesh through `polyTopoChange`, re-partition across
processors, repeat. On Base_leakage that is 14 full mesh rebuilds and 6 load
re-partitions to reach 607k cells, and the castellation machinery still runs
snap-problem detection, free-standing-baffle merging and leak closure — all
dead work when nothing ever snaps.

dcCartMesh decides the whole octree **before a single face exists**, then
assembles the mesh exactly once.

The comparison below was measured on the first, single-process version
(stair-step carve, 2026-06). It still shows the structural difference; current
timings for the cut-cell, MPI version are under "Parallel run" and
"Performance".

| | snappyHexMesh (`snap off`, tuned) | dcCartMesh (first version) |
|---|---|---|
| mesh rebuilds | 14 | 1 |
| load re-partitions | 6 | none — it partitions itself (see "Parallel run") |
| processes | 8 | 1 |

Same geometry, same dictionaries, `checkMesh` clean in both cases:

| case | snappyHexMesh | dcCartMesh | |
|---|---|---|---|
| SingleRack | 36,972 cells, ~15 s (8 proc) | 26,178 cells, **0.39 s** (1 proc) | ~38x |
| Base_leakage | 607,344 cells, ~10 s (8 proc) + 18 s of decompose / reconstruct / baffles | 481,358 cells, **7.2 s** (1 proc) | |

dcCartMesh lands 20-30 % fewer cells because it does no feature-edge
refinement — snappy's `level (min max)` `max` only triggers where the
intersection angle exceeds `resolveFeatureAngle`, which on axis-aligned
data-centre boxes buys little.

The algorithm class is the one Aftosmis, Berger and Melton described in
*Robust and Efficient Cartesian Mesh Generation for Component-Based Geometry*
(AIAA 97-0196): linear complexity, most of the domain plain Cartesian cells,
the work concentrated in the cut cells at the boundary.

---

## Architecture

```
src/mesh/dcCartMesh/
├── dcCartGrid/          graded background grid; the only place that knows
│                        about physical coordinates
├── dcCartOctree/        sparse leaf-only octree, 2:1 balanced
├── dcCartMeshBuilder/   octree + alive mask -> polyMesh, in one pass
├── dcCartSurfaces/      the STL geometry as the mesher asks about it:
│                        refinement level per box, segment blocking (carve +
│                        patch name), inside tests, weld and snap of coordinates
└── dcCartCutCells/      the cut-cell kernel: per-shell clip passes, baffle
                         sheets, feature pre-split, sliver merge rounds,
                         coplanar coalesce, quality repairs, output

applications/utilities/mesh/generation/dcCartMesh/
└── dcCartMesh.C         driver; reads system/dcCartMeshDict, runs the stages,
                         memory guard, MPI setup
```

### dcCartGrid

Tensor product of three sorted breakpoint arrays, so spacing may be
non-uniform and breakpoints may sit on object faces. The runner writes a
**uniform** background (plus per-object refinement); the variant with
breakpoints on object faces (`DCDTUNI_DCCART_ALIGNED_GRID=1`) is an experiment
that improves checkMesh figures but whose solve diverged on Base604 — see the
switch table below.

A level-`L` cell index `i` maps back as `I = i >> L` (background interval) plus
a uniform sub-division inside it, so refinement never moves a grid plane and
parent/child arithmetic is a shift.

### dcCartOctree

Only leaves are stored, per level: one bit per `(i,j,k)` of the level (a flat
open-addressing table of packed keys when a level has more than 2³⁰ cells),
plus the cells that were refined, so asking which level holds a point is a few
bit reads. No pointers, no per-cell allocation. Refining is "clear one bit, set
eight".

Kept 2:1 balanced across faces (with `nCellsBetweenLevels` buffer), which
constrains what the builder can meet across a face to exactly three cases: one
leaf at the same level, one leaf a level coarser, or four leaves a level finer.

### dcCartMeshBuilder

Emits every face from **one** side only, so nothing is created twice:

| neighbour | action |
|---|---|
| one level FINER | skip — the four finer cells emit it |
| one level COARSER | emit here (the finer side owns it) |
| SAME level | emit only on the +x/+y/+z side |
| no live neighbour | boundary face, emit here |

Every emitted face is a rectangle at the emitting cell's own level. A coarse
cell facing refined neighbours ends up with 9, 12 or 15 faces instead of 6 —
exactly the shape `hexRef8` produces.

**Hanging points.** Four corners are not enough. Where a neighbour is refined,
its extra corner lands mid-edge of this face, and a polyMesh face must list
every point on its perimeter or `checkMesh` reports a face-face T-junction.
After all corner points are registered, each face's four edges are walked by
recursive midpoint subdivision and any already-registered point is spliced in.
That is why face vertex counts come out 4 / 5 / 6 rather than all 4 — the same
distribution snappyHexMesh produces.

Internal faces are then written in upper-triangular order and boundary faces
grouped per patch, with normals pointing owner → neighbour.

---

## Mesh quality

`checkMesh -allTopology -allGeometry` on a uniform 20³ background refined to
level 3 (153,824 cells):

```
Upper triangular ordering OK.        Max aspect ratio = 1            OK
Face-face connectivity OK.           Max skewness = 0.333            OK
Topological cell zip-up check OK.    Non-orthogonality Max: 25.2°    OK
Number of regions: 1 (OK).           Cell determinant min 0.125      OK
Max cell openness = 1.7e-16 OK.      Total volume = 8 (exact)
```

The 25° non-orthogonality maximum occurs only at refinement transitions and is
inherent to any octree mesh.

**One expected "failure":** `***Concave cells (using face planes) found`. Those
are the transition cells whose refined side is four coplanar sub-faces;
checkMesh's face-plane concavity test flags them. Stock snappyHexMesh reports
the identical thing on its own castellated meshes — 36,128 concave cells on
Base_leakage, which is exactly its 33,674 nine-faced + 2,386 twelve-faced + 68
fifteen-faced cells. `deploy.sh` asserts that this is the *only* failing check.

### Coordinate snaps keep shells closed

The two snaps of `dcCartSurfaces` (grid planes, then near-duplicate
coordinates) and the weld all move surface points, and solid shells are the
connected components of point *labels*. So every stage that moves points ends
in `setMovedPoints`: points that became bit-equal are merged into one label and
triangles that collapsed are dropped. Before 2026-11-11 the snaps only moved
the points. CMI-Guichou's rack J7 sits at z=11.6079995, so its server fronts
arrive as 11.728 on some facets and 11.727999 on others. The snap put both on
one value, but the copies kept separate labels. Each server box split into
open pieces (14 shells instead of 10) and was cut as thin sheets with 193
stair-stepped cells. A third of every fan face was left as internal faces, and
the hot aisle drained through those boxes into the cold aisle. The runner now
measures every server fan face after meshing (`mesh_acceptance.fan_face_shortfalls`,
warning below 80% of the STL area), and the acceptance gate fails on it.

### dcCheckMesh

`Allwmake` also builds `dcCheckMesh`
(`applications/utilities/mesh/manipulation/dcCheckMesh`). It is OpenFOAM
v2412's checkMesh with one change in the patch-topology table. In a parallel
run, upstream `checkPatch()` synchronises a mesh-sized edge-count list across
the processors once **per patch**. A data hall has one patch per server-slot
face, often thousands of them. dcCheckMesh synchronises every patch's
coupled-edge face counts in **one** `globalMeshData::syncData` before the
table, and `checkPatch()` reads each patch's count from that. The counts are
the same `plusEqOp` sums, so the report and the sets written are identical.
Face zones and the "all boundary patches" row keep the upstream path.

On CMI-Guichou (10.1 M cells, about 7,700 patches, 8 ranks,
`-allTopology -allGeometry`), stock checkMesh took 66–69 s and dcCheckMesh
took 25 s. Its output differed only in the header and timing lines, and every
cellSet, faceSet and pointSet was byte-identical. The runner, the server's
"check mesh" job and the mesh-quality receipt use it when it is built
(`air_mesh_quality_receipt.resolve_checkmesh_binary`), and fall back to
`checkMesh` otherwise.

---

## Dictionary

`system/dcCartMeshDict`:

```
maxLevel            3;      // refinement ceiling
nCellsBetweenLevels 1;      // buffer cells around a level jump
surfaceBufferCells  1;      // rings of SAME-level cells kept around every
                            // surface-refined cell (0 disables). A cut cell
                            // beside a coarser neighbour leaves slivers no
                            // convex host can take (L-shaped union) and the
                            // solver diverges on the 1e-3 volume ratio.
minCutVolumeFraction 0.25;  // cut cells below this fraction of their hex merge
killCutVolumeFraction 0.005; // below this, a sliver with no convex host is
                            // handed to the solid (its faces become boundary)
degenerateCutVolumeFraction 1e-6; // below this a cell is absorbed into the
                            // neighbour across its largest plain face no
                            // matter what it touches (baffle, thin shell): a
                            // zero-volume cell NaNs the solver at step 1
baffleCrumbFraction 0.01;   // last merge round: a crumb below this fraction of
                            // its hex with a baffle face and a plain face at
                            // volume ratio < 0.01, which no merge took, goes
                            // to the solid -- only if every face it hands over
                            // (the baffle face included) keeps boundary
                            // skewness <= 4 from the receiver's centre (0 = off)
mergeMaxNonOrthogonality 75; // degrees: a merge may not leave the union facing
                            // a neighbour more obliquely unless one of the
                            // two cells already did (0 disables)
weldTolerance       0.0012; // near-coincidence weld (m): an axis-aligned
                            // surface within this of a finest-level grid
                            // plane is rewritten onto it (0 = off). The
                            // runner writes 1.2e-3.
firstLayerMinFraction 0;    // first-layer floor: per surface class the weld
                            // band widens to this x the class's OWN
                            // refinement-level spacing (0 = off, 0.5 = snap
                            // every axis-aligned surface, 6Sigma style)
firstLayerMax       0.005;  // cap on that band (m); 0 = no cap
firstLayerSealGap   -1;     // widest gap between two solids the floor may
                            // seal (m); < 0 = weldTolerance

background                  // breakpoints, one sorted list per axis
{
    x   (0 0.17 0.34 ... 4.569);
    y   (...);
    z   (...);
}

refinementBoxes             // volume refinement
{
    rack0 { min (0.6 0 1.0); max (1.3 2.1 2.2); level 3; }
}
```

Duplicate breakpoints (two objects sharing a face plane) are collapsed on read.

---

## Status

| stage | state |
|---|---|
| graded background grid | done |
| sparse octree + 2:1 balance | done |
| box refinement regions | done |
| polyMesh assembly incl. hanging points | done, `checkMesh`-validated |
| binary polyMesh output | done |
| build + deploy script + smoke test | done |
| STL surfaces: load, region → patch | done |
| surface-driven refinement | done |
| refinement shells (searchableBox / RotatedBox) | done |
| carve (flood fill from locationsInMesh) | done |
| patch assignment from surface regions | done |
| wall-clock budget (`-timeLimit` / `timeLimitSeconds`) | available; the runner writes `0` (soft five-minute target, timed and logged, never aborted) |
| faceZones for `faceType internal` surfaces (baffles) | done — with `splitBaffles true` (the runner's default) the mesher writes each pair's master/slave patches itself, so redistributePar and createBaffles are skipped |
| true cut cells (`dcCartCutCells`: per-shell clip, baffle sheets, feature pre-split, sliver merge, coplanar coalesce) | done — the ten release cases pass strict `checkMesh -allTopology -allGeometry` (as of 2026-10-21; receipts in `docs/benchmarks/air-release-cases-2026-10-21/`) |
| runner integration (`meshEngine: dcCart`) | done — `STLGenRunner._run_dcCart_mesh_pipeline`; the default engine since 2026-10-12 |
| **MPI parallel (`mpirun -np N dcCartMesh -parallel`)** | done — see below |
| memory preflight guard (`DCCART_MEM_GUARD`, runner + mesher) | done — see "Memory" |
| near-coincidence weld (`weldTolerance`, runner default 1.2e-3 m) | done — see "Memory" note on the weld default |
| first-layer floor (`firstLayerMinFraction` / `firstLayerMax` / `firstLayerSealGap`) | done — see "First-layer floor" |
| mesh census by boundary class + thin-cell count (`Cut mesh: thin cells`) | done — see "First-layer floor" |
| 2-rank parallel smoke in `deploy.sh` (same cells as serial) | done |
| feature-edge refinement | optional |

## Environment switches

A normal run needs none of these: the runner writes everything it wants into
`system/dcCartMeshDict`. The switches exist for A/B runs, for bisecting a
defect, and for the strict-mesh campaign's experiments. As of 2026-10-30 the
mesher source reads 125 distinct `DCCART_*` variables. The runner reads five;
two of them (`DCCART_MEM_GUARD`, `DCCART_MEM_AVAILABLE_BYTES`) are the same
variables the mesher reads, so there are 128 distinct names in all. List them
again with

```bash
cd src/backend/aircooling/third_parties/dcCartMesh
grep -rhoE 'getenv\("DCCART_[A-Z0-9_]+"\)' src/mesh/dcCartMesh/dcCart* \
    applications/utilities/mesh/generation/dcCartMesh --include='*.[CH]' | sort -u
grep -noE '"(DCDTUNI_)?DCCART_[A-Z0-9_]+"' ../../runner/stlgen_runner.py
```

(`lnInclude/` holds copies of the same headers; leave it out of the count.)
"Default" below is what happens when the variable is unset. Values the runner
normally writes into `dcCartMeshDict` are named; an environment override wins
over the dictionary.

**Read by the runner** (`src/backend/aircooling/runner/stlgen_runner.py`):

| switch | default / how to set | what it controls |
|---|---|---|
| `DCCART_MEM_GUARD` | on; `off`/`0`/`false`/`no` disables | the runner's memory preflight (the mesher reads the same variable) |
| `DCCART_MEM_AVAILABLE_BYTES` | measured | overrides the available memory both guards use (the test seam) |
| `DCCART_NO_SPLIT_BAFFLES` | unset = `splitBaffles true`; set writes `false` | baffle pairs written by the mesher, or left to createBaffles |
| `DCCART_NO_WALL_PAIR_ZONES` | unset = wall-baffle zones listed; set lists none | which baffle zones the mesher treats as walls rather than flow-through pairs |
| `DCDTUNI_DCCART_ALIGNED_GRID` | `0`; `1` enables (experiment) | background breakpoints on object faces. Better checkMesh figures, but the Base604 solve diverged at iteration 93; off until the cause is found and fixed |

**Read by the mesher** — grouped by what setting them does:

**Production path — read on every run; set one only for an A/B run** (29):

| switch | default / how to set | what it controls | read in |
|---|---|---|---|
| `DCCART_BAFFLE_COVERAGE_MIN` | refuse below 0.995; `off` keeps only the diagnostic | baffle sheet coverage check (non-perforated baffles) | `dcCartCutCells.C` |
| `DCCART_BAND_ABSORB` | on; `0` disables | absorb thin band cells into a neighbour | `dcCartCutCells.C` |
| `DCCART_CARVE_ON_FRACTION` | dict `onToleranceCarveFraction` (0.01); may only lower it | ON tolerance used by the carve | `dcCartMesh.C` |
| `DCCART_COOALESCE_TINY_FRACTION` | 0.25 | tag fraction below which coplanar coalescing treats a face as tiny | `dcCartCutCells.C` |
| `DCCART_COORDSNAP_FRACTION` | dict `coordSnapFraction` (1e-4) | coordinate clustering of surface vertices | `dcCartMesh.C` |
| `DCCART_FIRSTLAYER_FRACTION` | dict `firstLayerMinFraction` (runner writes 0.3) | first-layer floor fraction | `dcCartMesh.C` |
| `DCCART_FIRSTLAYER_MAX` | dict `firstLayerMax` (0.005 m) | first-layer floor cap | `dcCartMesh.C` |
| `DCCART_GRIDSNAP_FRACTION` | dict `gridSnapFraction` (0.01) | snap of near-grid surface coordinates | `dcCartMesh.C` |
| `DCCART_KEEP_EMPTY_PATCHES` | unset = empty patches dropped; set keeps them | empty boundary patches in the output | `dcCartCutCells.C` |
| `DCCART_LEGACY_CORNERS` | unset = direct corner lookup; set restores the assembled path (same mesh) | corner classification route | `dcCartMesh.C` |
| `DCCART_MEM_AVAILABLE_BYTES` | measured (MemAvailable / cgroup) | overrides the available-memory number both guards use | `dcCartMesh.C` |
| `DCCART_MEM_GUARD` | on; `off`/`0`/`false`/`no` disables | memory preflight guard (mesher; the runner reads the same variable) | `dcCartMesh.C` |
| `DCCART_MERGE_TIMEOUT` | 240 s; `0` = unlimited | wall-clock budget of the sliver-merge phase; on timeout the run fails naming the phase | `dcCartMesh.C` |
| `DCCART_NOCOORDSNAP` | unset = stage on; set skips it | coordinate clustering stage | `dcCartMesh.C` |
| `DCCART_NOGRIDSNAP` | unset = stage on; set skips it | grid snap stage | `dcCartMesh.C` |
| `DCCART_NOPRESPLIT` | unset = stage on; set skips it | feature pre-split of cut cells | `dcCartCutCells.C` |
| `DCCART_NOSEAMSPLIT` | unset = stage on; set skips it | split of thin cells at face-group seams | `dcCartCutCells.C` |
| `DCCART_NOSNAP` | unset = stage on; set skips it | snap of cut points onto feature lines | `dcCartCutCells.C` |
| `DCCART_NOSNAP_CORNER` | unset = stage on; set skips it | corner branch of the feature snap | `dcCartCutCells.C` |
| `DCCART_NO_BAFFLE_EDGE_SPLIT` | unset = stage on; set skips it | pre-split where a baffle sheet's free edge crosses cells | `dcCartMesh.C` |
| `DCCART_NO_WEDGE_READMIT` | unset = stage on; set skips it | wedge re-admission rounds after the carve | `dcCartMesh.C` |
| `DCCART_PROMOTE_ROUNDS` | 3 | rounds of ON-point promotion | `dcCartCutCells.C` |
| `DCCART_STAR_UNIONS` | unset = convex-union test; set restores the centroid-only test | admission test for merged unions | `dcCartCutCells.C` |
| `DCCART_CARVE_IN_INDEX_ORDER` | unset = scheduled order when it is estimated at least 10 % faster (parallel runs); set keeps shell index order | carving order of the shells | `dcCartCutCells.C` |
| `DCCART_SYNC_ALL_SHELLS` | unset = neighbour-only sync (≤ 64 ranks); set syncs every shell | parallel shell synchronisation | `dcCartCutCells.C` |
| `DCCART_WELD` | dict value; `0` forces off, other values enable | weld on/off | `dcCartMesh.C` |
| `DCCART_WELD_MIN_PLANE_AREA` | -1 (no area filter) | only weld planar patches at least this large (m²) | `dcCartSurfaces.C` |
| `DCCART_WELD_PEER` | on; `0` disables | peer-class weld between coordinate classes | `dcCartSurfaces.C` |
| `DCCART_WELD_TOL` | dict `weldTolerance` (runner writes 1.2e-3 m) | near-coincidence weld band, metres | `dcCartMesh.C` |

**Debug and observation — logs or evidence files; `DCCART_SKIP` is the one that changes the mesh** (46):

| switch | default / how to set | what it controls | read in |
|---|---|---|---|
| `DCCART_CANONICAL_FAN_INVENTORY` | off; `1` turns it on | inventory of the post-carve wall fan | `dcCartMesh.C` |
| `DCCART_CAP_BIRTH_OBSERVE` | off; `1` turns it on (with `DCCART_CAP_BIRTH_WINDOWS`) | observe where baffle caps are born | `dcCartCapBirthObservation.H` |
| `DCCART_CAP_BIRTH_PRIORITY_CELLS` | off; `rank:cell` list | cells to report first | `dcCartCapBirthObservation.H` |
| `DCCART_CAP_BIRTH_SOURCE_NAME` | off; a prepared source name | limit the observation to one source | `dcCartCutCells.C` |
| `DCCART_CAP_BIRTH_WINDOWS` | off; window list | windows for the cap-birth observation | `dcCartCapBirthObservation.H` |
| `DCCART_CARVE_TRACE` | off; six space-separated numbers | flood path back to its seed for cells kept in a box | `dcCartMesh.C` |
| `DCCART_CARVE_TRACE_ALL` | off; any value turns it on | print every step of that path | `dcCartMesh.C` |
| `DCCART_CELL_DEBUG_BOX` | off; six numbers | per-stage cell snapshots only (cheaper than DEBUG_BOX) | `dcCartMesh.C` |
| `DCCART_COMPARE_CORNERS` | off; any value turns it on | audit the direct corner lookup against the assembled one | `dcCartMesh.C` |
| `DCCART_COMPARE_REGION_QUERIES` | off; any value turns it on | compare indexed and reference region queries | `dcCartSurfaces.C` |
| `DCCART_COMPLETE_REPLAY_OBSERVATION` | off; `1` turns it on | complete-cavity replay observation | `dcCartCompleteCavityReplayObservation.H`, `dcCartCompleteCavityReplaySourceSidecar.H` |
| `DCCART_CONE_AXIS_KERNEL_CENTRE` | off; any value turns it on | explore cone kernel centres (exploration only) | `dcCartCutCells.C` |
| `DCCART_CONE_AXIS_KERNEL_PARETO` | off; any value turns it on | Pareto profile of cone kernels (exploration only) | `dcCartCutCells.C` |
| `DCCART_CONE_EVIDENCE` | off; any value turns it on | JSONL evidence from the cone and thin-box passes | `dcCartCutCells.C`, `dcCartRawQualityClusterUnion.H` |
| `DCCART_CONE_KERNEL_AXIS_CANDIDATES` | off; any value turns it on (needs `…_PARETO`) | axis kernel candidates (exploration only) | `dcCartCutCells.C` |
| `DCCART_CONE_KERNEL_TEST` | off; `rank parent x y z …` | evaluate one candidate cone kernel | `dcCartCutCells.C` |
| `DCCART_CONE_NONORTH_KERNEL_CANDIDATES` | off; any value turns it on (needs the two above) | non-orthogonality kernel candidates (exploration only) | `dcCartCutCells.C` |
| `DCCART_CONE_QUALITY_KERNEL_CANDIDATES` | off; any value turns it on (needs `…_PARETO`) | quality kernel candidates (exploration only) | `dcCartCutCells.C` |
| `DCCART_CURRENT_PREFIX_PROVIDER_INVENTORY` | off; `1` turns it on | inventory of prefix providers | `dcCartCutCells.C` |
| `DCCART_DEBUG` | off; any value turns it on | verbose cut-cell log lines | `dcCartCutCells.C` |
| `DCCART_DEBUG_BOX` | off; six numbers `xmin,ymin,zmin,xmax,ymax,zmax` | per-stage cell dumps in a box (space-separated form explains merges) | `dcCartCutCells.C`, `dcCartMesh.C` |
| `DCCART_DEBUG_CELL` | off; six comma-separated numbers | split-decision trace for cells in a box | `dcCartCutCells.C` |
| `DCCART_DEBUG_FOLD` | off; any value turns it on | fold-guard trace | `dcCartCutCells.C` |
| `DCCART_DIAGNOSTIC_GLOBAL_ALIVE_REFERENCE` | off; `1` turns it on | reference run without rank-local halo truncation (diagnostic comparison) | `dcCartMesh.C` |
| `DCCART_FINITE_ADMISSION_OBSERVE` | off; set enables | finite point-repair admission observation | `dcCartFinitePointRepair.H`, `dcCartMesh.C` |
| `DCCART_FPSH_ALL` | off; any value turns it on | feature-pre-split shell report on every rank, not only rank 0 | `dcCartCutCells.C` |
| `DCCART_FRONT_COVERAGE_DIAGNOSTIC` | off; `1` turns it on | coverage diagnostic for one Shinaide inlet face | `dcCartCutCells.C` |
| `DCCART_LEGACY_JOURNAL_OBSERVE` | off; `1` turns it on | legacy journal observation | `dcCartCutCells.C` |
| `DCCART_MERGE_TRACE` | off; any value turns it on | owned-cell counts through the merge rounds | `dcCartCutCells.C` |
| `DCCART_MICRO_DEBUG` | off; any value turns it on | micro-cell pass trace | `dcCartCutCells.C` |
| `DCCART_POINT_STAR_FULL_CAPTURE` | off; `1` turns it on | full point-star capture to files | `dcCartCompleteCavityFullCapture.H` |
| `DCCART_PREPARED_FAILURE_INPUT` | off; `1` turns it on | capture the input of a prepared-partition failure | `dcCartPreparedPartitionObservation.H` |
| `DCCART_PREPARED_PARTITION_DIAGNOSTIC` | off; `1` turns it on | prepared facet-partition refusal reasons | `dcCartCutCells.C` |
| `DCCART_PROFILE` | off; any value turns it on | stage timing | `dcCartCutCells.C`, `dcCartPreparedFacetInstall.H` |
| `DCCART_PYR_DEBUG` | off; any value turns it on | checkMesh face-pyramid test after each stage (`grep pyrdbg`) | `dcCartMesh.C` |
| `DCCART_REDUNDANT_EDGE_FAILURE_DIAGNOSTIC` | off; `1` turns it on | detail for a redundant-edge failure (the fatal error stays) | `dcCartCutCells.C` |
| `DCCART_SEAM_COORD_EVIDENCE` | off; any value turns it on | writes seam-plane-coordinates.json | `dcCartCutCells.C` |
| `DCCART_SHELL_TRACE` | off; a directory | per-shell carving time and side-exchange wait per rank (`carve.<rank>.tsv`), the carving order (`schedule.tsv`) | `dcCartCutCells.C` |
| `DCCART_SKEW_DEBUG` | off; any value turns it on | skew-join pass trace | `dcCartCutCells.C` |
| `DCCART_SKIP` | off; comma list of stages | skip post-cut stages (`merge,coalesce,refine,stitch,relabel,dropghost,strip,splitwall,seamsplit`) to bisect a defect — changes the mesh | `dcCartMesh.C` |
| `DCCART_THINWALL_DEBUG` | off; any value turns it on | thin-wall pass trace | `dcCartCutCells.C` |
| `DCCART_TRANSITION_ROOF_AUDIT` | off; any value turns it on | read-only audit of refinement-transition roofs | `dcCartMesh.C` |
| `DCCART_TRANSITION_ROOF_EVIDENCE` | off; any value turns it on | JSONL evidence of transition-roof components | `dcCartCutCells.C` |
| `DCCART_WEDGE_DEBUG` | off; any value turns it on | wedge-kill pass trace | `dcCartCutCells.C` |
| `DCCART_WEIGHT_DEBUG` | off; any value turns it on | interpolation-weight repair trace | `dcCartCutCells.C` |
| `DCCART_WELD_DEBUG` | off; any value turns it on | weld trace | `dcCartPlanarSheetPerimeterWeld.H`, `dcCartSurfaces.C` |

**Experiments — off unless set, change the algorithm, not used for the release cases** (50):

| switch | default / how to set | what it controls | read in |
|---|---|---|---|
| `DCCART_BAFFLE_WIDE_REACH` | off; any value turns it on | wider sheet reach for baffle passes | `dcCartCutCells.C` |
| `DCCART_COMPLETE_CAVITY_CONTEXT` | off; `1` turns it on | complete-cavity final subdivision context | `dcCartCutCells.C`, `dcCartMesh.C` |
| `DCCART_COMPLETE_CAVITY_REPAIR` | off; `1` turns it on (needs the context flag) | complete-cavity repair | `dcCartCutCells.C`, `dcCartMesh.C` |
| `DCCART_CONES_BOUNDED_EAR_PARTITION` | off; `1` turns it on | bounded ear partition of cone bases | `dcCartCutCells.C` |
| `DCCART_CONES_CONFORMING_SIDE_UNION` | off; `1` turns it on | conforming side union of cone bases | `dcCartCutCells.C` |
| `DCCART_CONES_EAR_REFAN` | off; any value turns it on | re-fan small outlines from canonical ears | `dcCartCutCells.C` |
| `DCCART_CONES_KEEP_PLANAR_COLLINEAR_BASE` | off; `1` turns it on | keep planar bases with collinear points | `dcCartCutCells.C` |
| `DCCART_CONES_KEEP_UNSPLIT_WARPED` | off; any value turns it on | keep warped bases unsplit | `dcCartCutCells.C` |
| `DCCART_CONES_PREFER_VERTEX_FAN` | off; any value turns it on | prefer a vertex fan for convex bases | `dcCartCutCells.C`, `dcCartRawQualityClusterUnion.H` |
| `DCCART_CONES_PRESERVE_PLAIN_TRIANGLE_ORDER` | off; `1` turns it on | keep the writer's operand order for plain triangles | `dcCartCutCells.C` |
| `DCCART_CONES_REFAN_QUALITY` | off; any value turns it on | quality re-fan of canonical bases | `dcCartCutCells.C` |
| `DCCART_CONES_SELECTED_FOUR_TRIANGLE_BOUND` | off; any value turns it on | triangle bound on selected four-point bases | `dcCartCutCells.C` |
| `DCCART_CONES_SIGNED_MOMENT` | off; any value turns it on | signed-moment centre for non-convex bases | `dcCartCutCells.C` |
| `DCCART_CONES_STAR_BOUNDARY_FC_FAN` | off; `1` turns it on | face-centre fan for star boundary bases | `dcCartCutCells.C` |
| `DCCART_CONES_STAR_WARPED_BASE` | off; `1` turns it on | star split of warped bases | `dcCartCutCells.C` |
| `DCCART_CONES_STRONG_TRIANGLE_BOUND` | off; any value turns it on | strong triangle bound | `dcCartCutCells.C` |
| `DCCART_CONE_LITERAL_UNIONS` | off; any value turns it on | literal cone unions | `dcCartCutCells.C`, `dcCartMesh.C` |
| `DCCART_CONE_QUALITY_AGGLOMERATION` | off; `1` turns it on (inside literal unions) | quality-driven cone agglomeration | `dcCartCutCells.C` |
| `DCCART_CONE_SAME_PHYSICAL_ROLE` | off; any value turns it on (inside literal unions) | restrict unions to one physical role | `dcCartCutCells.C`, `dcCartMesh.C` |
| `DCCART_CONFORMING_CONES` | off; any value turns it on | conforming cones across cyclic patches | `dcCartCutCells.C`, `dcCartMesh.C` |
| `DCCART_COOALESCE_CONFORMING_BASE` | off; `1` turns it on | conforming-base guard in coalesce and skew join | `dcCartCutCells.C` |
| `DCCART_COOALESCE_WALLS` | off; any value turns it on | coalesce across different plain wall patches | `dcCartCutCells.C` |
| `DCCART_CROSS_GRID_FLUID_PAIR` | off; `1` turns it on (inside literal unions) | cross-grid fluid pair transaction | `dcCartCutCells.C` |
| `DCCART_CROSS_PARENT_D_UNIONS` | off; `1` turns it on | cross-parent D unions | `dcCartCutCells.C` |
| `DCCART_CROSS_PARENT_WEIGHT_UNIONS` | off; `1` turns it on | cross-parent weight unions | `dcCartCutCells.C`, `dcCartMesh.C` |
| `DCCART_DEMOTE` | off; any value turns it on | demote ON points on the solid side of a neighbouring facet | `dcCartCutCells.C` |
| `DCCART_EARLIEST_PHYSICAL_POINT_REPAIR` | off; `1` turns it on | earliest physical point repair | `dcCartMesh.C` |
| `DCCART_EXACT_ZERO_BOUNDARY_PAIRS` | off; `1` turns it on | remove exact-zero boundary face pairs | `dcCartMesh.C` |
| `DCCART_EXACT_ZERO_UNTOUCHED_PROCESSOR` | off; `1` turns it on (with the flag above) | keep processor patches untouched in that pass | `dcCartMesh.C` |
| `DCCART_FINITE_CLOSED_COMPONENT_PREFIX` | off; `1` turns it on | closed-component prefix in finite point repair | `dcCartFinitePointRepair.H` |
| `DCCART_FOLDSPLIT` | off; any value turns it on | split non-planar folded caps | `dcCartCutCells.C` |
| `DCCART_INDEX_REGIONS` | off; any value turns it on | indexed region-area queries instead of the reference route | `dcCartSurfaces.C` |
| `DCCART_INTERSECTION_SEGMENT_PLANES` | off; `1` turns it on | keep intersection segment planes | `dcCartCutCells.C` |
| `DCCART_ONON` | off; any value turns it on | ON–ON lookup in the split | `dcCartCutCells.C` |
| `DCCART_PIERCE` | off; any value turns it on | pierce handling in the clip pass | `dcCartCutCells.C` |
| `DCCART_PRECARVE_CURVATURE_REFINE` | `0` (only `0`/`1` accepted) | curvature refinement before the carve | `dcCartMesh.C` |
| `DCCART_PREPARED_FACET_PARTITION` | off; `1` turns it on | prepared facet partition | `dcCartCutCells.C` |
| `DCCART_REDUNDANT_EDGE_POINTS` | off; any value turns it on | remove redundant edge points | `dcCartMesh.C` |
| `DCCART_REFINE_THIN_BOXES` | off; any value turns it on | refine thin box cells | `dcCartMesh.C` |
| `DCCART_SEAMSPLIT_ALLPATCH` | off; any value turns it on | seam split at every seam, not only patch changes | `dcCartCutCells.C` |
| `DCCART_SERVER_COMPONENT_FINALIZATION` | off; `1` turns it on | prepared server component finalisation | `dcCartMesh.C` |
| `DCCART_SHARED_POINT_REPAIR` | off; `1` turns it on | shared point repair (same value required on every rank) | `dcCartMesh.C` |
| `DCCART_STRICT_WALLDIST` | off; any value turns it on | fail the run when the minimum wall distance is below the configured floor | `dcCartMesh.C` |
| `DCCART_THINWALL_KILL` | off; any value turns it on | hand stranded thin-wall cells to the solid instead of keeping them | `dcCartCutCells.C` |
| `DCCART_THIN_BOX_CURRENT_STAR_GUARD` | off; any value turns it on | current-star guard in the thin-box pass | `dcCartCutCells.C` |
| `DCCART_TRANSITION_ROOF_COMPONENTS` | off; any value turns it on | transition-roof component repair | `dcCartCutCells.C` |
| `DCCART_TRANSITION_ROOF_RECTANGULAR` | off; any value turns it on | rectangular transition-roof repair | `dcCartCutCells.C` |
| `DCCART_WEDGE_KILL` | off (dict keys 0); `1` enables with 2e-3 / 5.2e-4 m, `0` forces off | hand thin wall wedges to the solid | `dcCartMesh.C` |
| `DCCART_WEDGE_VOL` | dict `wedgeVolumeFraction` (0) | wedge admission V/Vref | `dcCartMesh.C` |
| `DCCART_WEDGE_Y` | dict `wedgeWallDistance` (0) | wedge admission wall distance (m) | `dcCartMesh.C` |

Next step for this list: walk the experiments against the release path, then
either retire each one in the code or record here why it stays.

## First-layer floor

The classic weld (`weldTolerance`) fixes millimetre grazes: an axis-aligned
surface plane within 1.2 mm of a finest-level grid plane is rewritten onto
it, so the carver never sees a hairline film. Anything farther off stays,
and a rack face 3 mm off a 25 mm cell face still carves a 3 mm slab whose
cell centre sits 1.5 mm from the wall.

`firstLayerMinFraction` turns that into a per-object guarantee in the spirit
of 6Sigma's snap-to-grid structured meshes (the measured 6Sigma rule is in
`docs/reference/sixsigma-cell-size-rule.md`) (there, every object face IS a
grid line and the first cell is a full cell). For every axis-aligned
coordinate class the band becomes

    max(weldTolerance, min(firstLayerMinFraction * h_L, firstLayerMax))

with `h_L` the spacing of the finest refinement level the class's own
surfaces ask for, measured against the level-L planes. A class left in
place is therefore at least that far from every plane a cell that cuts it
can put a face on (coarser levels' planes are a subset), so the first cell
off every such wall is either a full cell or a cut at least the floor
thick. `0.3` with the 5 mm cap is the runner's default (level-3 racks on a
0.34 m background: 42 mm cells, floor 5 mm). Measured 2026-09-22 at np8:
Base604 min cell volume 1.6e-4 -> 2.1e-3 of its hex, max aspect ratio 130 ->
41, no cell under 1 mm, and the steady solve's k residual stops freezing
(9e-7 with 0 solver iterations -> 2e-4, zero bounding events, the rack E15
outlet hot spot k 14.8 -> 2.2); DAML's residuals an order lower; Tri_angle
unchanged.

Guards, in order: the same-solid guard (two opposing faces of one solid
onto one plane between them would seal the solid's own slot) is unchanged;
the new gap guard keeps two DIFFERENT solids facing each other across a gap
apart unless the classic band would have sealed them anyway or the gap is
at most `firstLayerSealGap` (default: the weld tolerance). Kept pairs are
counted in the log line.

A vertex whose class is marked on two axes composes both moves (each axis
writes its own component). The first-claim rule that used to skip the
second axis left every such box edge on a ~1 mm ramp (Base604 B12/B14 rack
frames), and a ramp's triangles are no longer axis-aligned, so no later
round could mark them again -- the ramps carved the 52 um crumbs behind
the Base604 quality floor of 1.7e-5 m.

The census that follows the cut now reports, reduced over the ranks:

```
Cut mesh: quality floor — min wall distance 1.3e-04 m (baffle faces 1.2e-05 m, open boundary 6.3e-04 m), min cell volume ... of its hex, min face area ... m2
Cut mesh: thin cells (t = V/Amax) — min t 1.5e-05 m; 43 below 1 mm; 49 below the 0.0012 m floor (32 carry a baffle face, 2 touch no boundary): Duct_riser_2 11, Duct_riser_1 4
```

`t = V / A_max` is a slab cell's thickness -- the y its wall function
divides by. The floor it counts against is the first-layer floor in force
(the weld tolerance when the floor is off, never below 1 mm), and the
per-patch list names the boundary patch each thin cell touches, worst
first, so the log says WHERE the thin cells are (DAML: the tilted duct
risers, which no axis-aligned rewrite reaches) instead of one number.
Environment overrides for A/B runs: `DCCART_FIRSTLAYER_FRACTION`,
`DCCART_FIRSTLAYER_MAX`.

## Parallel run

There is ONE algorithm. A run without `-parallel` is its one-rank case: that
rank owns every leaf, has no halo and no processor patch, and every step below
runs unchanged. OpenFOAM refuses `-parallel` on one rank, so the runner starts
the one-rank case without mpirun; that is the only difference.

`mpirun -np N dcCartMesh -overwrite -parallel` needs `system/decomposeParDict`
(only `numberOfSubdomains` is read) and empty `processor0..N-1` directories.
Unlike snappyHexMesh there is no `decomposePar` step: the mesher partitions
itself.

- Every rank builds the same background grid, octree and flood-fill carve
  (cheap; it keeps the partition deterministic without communication).
- Leaves are owned by finest-column quantiles along x, so every rank gets an
  equal share. Each rank cuts its own cells **plus a 3-layer halo** of its
  neighbours' cells with the same code — a cut is a pure function of the cell
  and the STL — so both sides of a processor face compute the same polygon.
- Every cut cell carries a global id that any rank holding it computes alike:
  its octree leaf (level, i, j, k) and a hash of its split history
  (`cutMesh::cellLeaf`, `cellPiece`).
- `mergeSmallCells` (pre-split halves joined again, slivers merged into a
  neighbour, hostless extreme slivers handed to the solid) runs in ROUNDS:
  each rank proposes for the cells it owns from the mesh as it stands at the
  start of the round, the proposals of all ranks are gathered on every rank
  (`Pstream::allGatherList`), and every rank resolves them in one global order
  (pre-split halves smallest first, slivers by global id, which is leaf order;
  at most one merge per cell per round) and applies the merges whose two cells
  it holds. A merge may join the cells of two ranks; the union keeps the host's
  rank. The cells therefore do not depend on the rank count. (Before the
  rounds, merges stopped at rank boundaries: the meshes of different rank
  counts differed in hundreds of cells and left unmerged slivers along every
  interface.)
- Report counters count owned cells and are summed over the ranks (the merge
  counters count the gathered proposals, which every rank holds alike), so
  they read the same for any rank count. Sizes are per rank: the `Assembled`
  and faceZone lines, and the `Cut mesh` line, which prints the total cell
  count and the rank count next to this rank's own cells, points and faces.
- `dropGhostCells` then discards the halo; an owned–halo face becomes a face of
  `procBoundary<me>to<other>` (type `processor`). OpenFOAM couples the two
  sides **by order and by vertex 0** (`reconstructParMesh` "assumes vertex0
  matched"), so the faces are sorted by fuzzy face centre and rotated to start
  at their lexicographically smallest point. Without the rotation the merged
  mesh has thousands of zero-area faces and negative-volume cells even though
  every processor mesh is valid on its own.
- Both sides of a baffle stay on one rank. A baffle between two ranks' cells
  becomes a `processorCyclic` pair, and neither `reconstructParMesh` nor
  `redistributePar` can handle one, so the solve would be held to the mesher's
  rank count. Two passes keep the partitions free of them, for any baffle in
  any direction. After the slabs are cut, a pair of leaves on two ranks whose
  centres lie on either side of a baffle goes to the lower rank, until no such
  pair is left (a baffle inside a leaf splits only that leaf). After the
  merges, which can carry a sliver's baffle face across an interface, the cell
  of the higher rank across such a face goes to the lower rank. Both passes
  work on data every rank holds alike and only move processor faces: Alibaba-sige
  went from 24 `processorCyclic` patches to none with the global mesh unchanged
  (36,735,281 faces, 7,094 patches compared face by face).
- Each rank writes `processor<i>/constant/polyMesh`. The runner keeps the
  partitions as the product (`checkMesh -parallel` on them; the mesher already
  wrote the baffle master/slave pairs, so createBaffles and redistributePar are
  skipped) and reconstructs only when the SOLVE stage needs a different rank
  count (`_decompose_for_solve`). `reconstructParMesh` segfaults on a
  `processorCyclic` patch, so for a one-rank-vs-N-rank comparison merge the
  partition set by coordinate instead of reconstructing it. A failed parallel
  run is an error; it does not fall back to one rank, which would only hide
  the failure.

Acceptance: mesh the same case at 1 and N ranks, reconstruct, write `0/C` and
`0/V` (`postProcess -func writeCellCentres/writeCellVolumes`) and compare the
cell sets (write them at `writePrecision 15`: at the default 6 digits a centre
that sits on a rounding boundary reads as a different cell). The testbed case,
270,008 cells, with one checkMesh report for all four rank counts:

| ranks | wall time | cells | differing cells vs 1 rank |
|---|---|---|---|
| 1 | 11 s | 270,008 | — |
| 2 | 9 s | 270,008 | 0 (max \|ΔV\|/V 4e-14) |
| 4 | 7 s | 270,008 | 0 |
| 8 | 6.6 s | 270,008 | 0 |

(48 / 30 / 19 / 15 s before the work described under Performance, with the
same cells.)

Before the rounds the 2-, 4- and 8-rank meshes had 269,760 / 269,764 / 269,767
cells and differed from the 1-rank mesh in 41 / 349 / 573 cells.

### Merge rules the rounds settled on

Resolving in rounds removes the accidental order of the old sequential loop,
and some of that order was doing work. Measured on the testbed:

- **Pre-split halves rejoin smallest first**, into the sibling sharing the most
  face area. In a global order a 5e-5 corner crumb lost all three of its
  convex siblings to other joins and was left a 6e-9 m³ cell.
- **Slivers resolve in leaf order** (thinnest-first and fewest-hosts-first left
  more slivers).
- **A crumb below `killCutVolumeFraction` may join a sliver in any round**; the
  "no sliver into a sliver before the last round" rule exists for slab slivers
  along a rail, not for crumbs.
- **`mergeMaxNonOrthogonality` (75°)**: a union may not face a neighbour more
  obliquely than that unless one of the two cells already did. A convex union
  can still leave a zig-zag interface; without the guard the worst face was 85–89°.
- **A baffle-side crumb no merge takes goes to the solid at the very end
  (`baffleCrumbFraction`)**, when it would otherwise fail checkMesh's volume
  ratio and every face it hands over passes the boundary skewness limit. The
  receiver-skew guard is what keeps the measured DAML failure (a micron notch,
  skew ~36) out; the dc-builder example at base 0.34 m went from two failing
  faces to a passing mesh (`docs/benchmarks/dccart-034-volume-ratio-2026-10-22/`).

Against the old sequential kernel on the same case: max non-orthogonality 75.6°
(was 81.7°), minimum face volume ratio 4.8e-3 (was 3.3e-4), 337 faces below
0.01 (was 418), 700 small interpolation weights (was 723), same smallest cell;
worse: 982 highly skewed faces (was 658, almost all boundary faces on rack-frame
strips a sliver rejoin puts at the side of a 25 mm cell) and 263 faces above
70° (was 156). Keeping slivers out of the rejoin halves the skewed faces (≈290)
but costs orthogonality (89°) — the trade was taken for the solver's sake.

## Performance

The data-hall case (15.6 × 24.6 × 4.4 m, 142 racks: 27 surfaces with 17k
triangles, 366 refinement shells) meshes to 5.45 M octree leaves and 3.6 M cut
cells. Wall times on a shared 48-thread host under other users' load:

| run | before | now |
|---|---|---|
| data hall, 8 ranks | 302 s | 69 s |
| data hall, 1 rank | aborted by the 900 s budget (carving alone 590 s) | 228 s |
| testbed, 1 / 2 / 4 / 8 ranks | 48 / 30 / 19 / 15 s | 11 / 9 / 7 / 6.6 s |

**Every change below leaves the mesh bit for bit the same**: the polyMesh files
compare equal with `cmp`, at one rank against the previous one-rank mesh and at
eight ranks processor directory by processor directory. What was slow, and what
replaced it:

- **Refinement and proximity** asked every surface's octree once per cell, and
  `triSurfaceMesh::overlaps` answers a yes/no question by collecting every
  triangle in the box into a HashSet, building a bound box per triangle. Now a
  refined cell hands its children the triangles that overlap it
  (`levelInBoxAboveCand`, the proximity walk). A child's box lies inside its
  parent's bit for bit (grid planes divide by powers of two), and the
  separating-axis test is monotone in the box, so a triangle missing the parent
  misses the child: each child tests a handful of triangles. The test runs on
  per-triangle precomputed axes (`dcCartTriSat`); a box's extent along an axis
  is the axis dotted with its two extreme corners, which equals the eight-corner
  scan because the dot product is monotone in each coordinate.
- **Surface masks.** The proximity walk records which carving surfaces and
  faceZone baffles come within half a diagonal of each cell. A segment between
  two cell centres (the carve flood) or from a cell centre one cell size out
  (patch and faceZone lookups of the assembly) lies inside those boxes, so the
  surfaces not in the mask cannot block or cross it and are not asked.
- **Ray queries** (`blockingRegionMasked`, `carvingHit`,
  `crossedFaceZoneMasked`, `solidShellHits`) walk OpenFOAM's octree with the
  same triangle test behind a bound-box rejection grown far past the
  intersection tolerance (`boxedIntersectOp`); `findLineAll` and its
  `checkUniqueHit` are reproduced line for line around it.
- **Triangle lists whose order matters** downstream (a shell's triangles feed
  the feature pre-split and the cap split) come out in `labelHashSet::toc()`
  order without the set: `findBoxToc` replays the table (capacity from
  `reserve`, identity hash, head insertion, relinking on growth).
- **Octree leaf sets** are one bit per cell of a level (a flat hash table when a
  level has more than 2³⁰ cells), and refined cells are recorded, so a
  containing-level query is a few bit reads.
- **Cut passes work on topological neighbourhoods**: the cells overlapping a
  shell's box, every cell overlapping their joint box, and two face rings,
  instead of a geometric margin of 16 cell sizes that swept in 25 times the
  cells.
- **Corner classification** assembles only the corner topology of the
  stair-step mesh (`dcCartMeshBuilder::cornerCells`: the same points under the
  same ids, no faces kept) and each rank classifies a hashed share of the
  corners.
- **Allocation and hashing**: flat open-addressing tables for points, keys and
  coincident-point buckets; grid corners filed without a search in the point
  merge (they are further apart than its tolerance); face geometry cached in
  the merge rounds; counted reserves; faces transferred instead of copied; one
  `static const` read of each debug switch that the cap split, the feature
  snap, the clip pass and the face output checked per cell or face
  (`relabelCoplanarPatches` still calls `getenv` per boundary face).

Where the time goes now. At eight ranks the ranks work 51 to 63 s each and
wait on the slowest for the rest. The slowest spends 14 s in the carving
passes, 8 s assembling the corner topology for the classification (every rank
assembles all of it: the share of corners a rank classifies is drawn by point
id), 6.5 s in the baffle passes and about 15 s allocating, spread over all of
them. At one rank the carving passes take 104 s of the 228, the baffle passes
35 s, the assembly 20 s and the merge rounds 15 s.

### Carving order at several ranks

A shell's side exchange is among the ranks holding its reach, and each rank
waits for its slab neighbours in it. Walked in shell index order this chained
the slabs: on the 41 × 21 m hall (Alibaba_hangchou, 10.4 M cells, 920 shells,
8 ranks) a row of 13 rack blocks lies end to end along x, each pair of
neighbours shared by two neighbouring ranks, so rank 2 waited for ranks 0 and
1, rank 3 for rank 2, and so on. Each rank carved for about 60 s of a 277 s
stage.

The ranks now walk one common order built by `scheduleCarving` before the
loop. Every rank reports the cell count and the box of each shell's reach. The
boxes are joined over the ranks. A shell keeps after every lower-indexed shell
whose reach box overlaps its own by a volume. Reaches that only touch end on
the same grid plane and share no cell, so their order is free. A list schedule
on the reach cell counts orders the rest, and every rank computes the same
order from the same reduced data. The ranks of a shell meet at it in the same
sequence, so the exchange cannot deadlock. One rank, a run whose estimate
gains less than 10 %, or `DCCART_CARVE_IN_INDEX_ORDER` keeps index order.

The order changes point and cell numbering, so the files differ, but the mesh
does not: the global face-centre multiset and the patch face counts (processor
faces counted once) equal those of the index-order mesh. Leaving every shell
free does change it (10,031 stair-step cells kept on the same hall), which is
why overlapping reaches keep their order.

| case, ranks | carving, index order | scheduled | mesh stage | faces / patches vs index order |
|---|---|---|---|---|
| Alibaba_hangchou, 8 | 277 s | 80 s | 594 → 399 s | 34,155,564 / 7,990 identical |
| Base604, 8 | 40.3 s | 41.3 s (estimate 8 %: index order since) | — | 22,854,403 / 4,336 identical |
| Shinaide, 4 | 6.8 s | 7.7 s (estimate 4 %: index order since) | — | 2,379,639 / 337 identical |

`DCCART_SHELL_TRACE=<dir>` writes one line per shell and rank
(`carve.<rank>.tsv`: reach and part cells, seconds, of them in the side
exchange and blocked in it, the step at which the shell was carved), and the
master writes the order with the reach boxes and per-rank costs
(`schedule.tsv`).

To check a change, mesh the testbed at one rank and `cmp` every
`constant/polyMesh` file against the previous build's, then mesh the data hall
at eight ranks and `cmp` every `processor*/constant/polyMesh` file. A sampling
profiler (SIGPROF plus `backtrace`, preloaded into each rank) found every item
above; the host has neither `perf` nor `gdb`.

To find the stage that makes a checkMesh failure: `DCCART_DEBUG_BOX` with six
COMMA-separated numbers (`xmin,ymin,zmin,xmax,ymax,zmax`) writes every cell
with a point in that box after each stage to `iface_dbg/<stage>.<rank>.txt`
(the SPACE-separated form instead explains the merge decisions about the cells
centred in it), and `DCCART_PYR_DEBUG=1` runs checkMesh's face-pyramid test
after each of those stages (`grep pyrdbg log`), so the stage where an
"incorrectly oriented" face first appears is named in the log.

## Memory

The whole mesh is resident at once, and every rank duplicates the octree and
carve bookkeeping (`levels`/`indices`, the leaf id map, the alive/owner
arrays and the whole stair-step mesh's pass-1 corner topology, all kept for
the run), so the peak RAM of a run **rises with the rank count**:

```
total peak ≈ leaves × 1.6 KB + ranks × (100 MB + leaves × 0.35 KB)      (× 1.15 safety)
```

`leaves` is the octree leaf count after the 2:1 balance (the `Balanced: … ->
leaves N` line). Measured points (RSS summed over the ranks, 2026-09-21):

| case | octree leaves | ranks | peak sum | law × 1.15 |
|---|---|---|---|---|
| Base604 (cap 24M, level 4) | 19.7 M | 16 | 145.6 GB | 165 GB |
| Slab | 329 k | 1 | 0.80 GB | 0.75 GB |
| Slab | 329 k | 4 | 1.60 GB | 1.6 GB |
| Slab | 329 k | 16 | 4.56 GB | 4.6 GB |

So 19.7 M leaves need ~150 GB on 16 ranks and would need ~90 GB on 4: more
ranks cut wall time but cost RAM, the opposite of snappyHexMesh, whose per-rank
share shrinks with `np`. Size `maxGlobalCells` from the host's RAM: at 16 ranks
a host with 190 GB available fits about 22 M leaves. The constants are
provisional — every run now prints its own measurement
(`Memory: peak RSS sum X GB (largest rank Y GB) for N octree leaves on P
rank(s); guard estimate Z GB`, from `VmHWM` per rank), so re-fit them from the
logs when a case lands outside the table. They live in TWO places that must
stay equal: `kMem*` in `dcCartMesh.C` and `DCCART_MEM_*` in
`stlgen_runner.py`.

Two guards enforce the law, both before anything is written:

- **runner** (`STLGenRunner._check_dcCart_memory`, right after the manifest is
  loaded, before STL preparation): `maxGlobalCells` is only a FLOOR on the leaf
  count — the refinement pass that crosses it and the balance still run, up to
  a whole level of overshoot (cap 6 M gave 5.5 M leaves on Base604 at level 4,
  cap 24 M gave 19.7 M) — so the runner refuses only when a LOWER-bound law
  (1 KB/leaf + ranks × (50 MB + 0.15 KB/leaf)) already exceeds the budget, and
  otherwise logs the estimate at the cap (`dcCart memory guard: …`), warning
  when it does not fit with headroom;
- **dcCartMesh** (after `Balanced:`, before the carve): the exact leaf count
  against the budget, `Memory guard: N leaves on P rank(s): estimate X GB (+ 4
  GB headroom), Y GB available`, and a `FatalError` naming the largest leaf
  count that fits when it does not. The master rank reads the numbers and
  broadcasts them, so every rank takes the same decision; in parallel the
  FatalError aborts the whole job.

"Available" is the smaller of `MemAvailable` in `/proc/meminfo` — never
`MemFree`, which on a host with 150 GB of page cache reads 8 GB while
`MemAvailable` reads 195 GB — and, inside a cgroup v2 with a `memory.max`,
that limit minus `memory.current` (the tightest ancestor wins).
`DCCART_MEM_GUARD=off` disables both guards; `DCCART_MEM_AVAILABLE_BYTES=<n>`
overrides the measured number (the test seam; the runner and the mesher both
honour it). A refusal names the largest `maxGlobalCells` / leaf count that
fits on that rank count and says that fewer ranks need less.

The guard is an estimate, not an enforcement. To keep sshd and other users'
jobs alive when it is wrong, the mesher can be started under a memory cgroup:
`systemd-run --user --scope -p MemoryMax=<available − 8 GB> -p
MemorySwapMax=0 mpirun …` needs no root when the user manager delegates the
memory controller (`systemctl show -p Delegate -p DelegateControllers
user@<uid>.service` → `yes`, `memory pids`; true on the dev host), but the
scope lives under `user@<uid>.service`, which stops on the user's last logout
unless `loginctl enable-linger` is set — a server started from an SSH session
that then closes would lose its mesh job — so it is NOT wired into the runner;
use it by hand on a lingering user. `ulimit -v` is not a substitute:
`RLIMIT_AS` is per process (divide by the rank count) and virtual size runs
well ahead of RSS, so a tight value produces a spurious `bad_alloc` in one
rank.

**Weld default.** The runner writes `weldTolerance 0.0012;` into
`dcCartMeshDict` (manifest key `meshConfig.weldTolerance`, `0` turns it off).
The DAML testbed's 1.3e-4 m wall wedge made `buoyantBoussinesqSimpleFoam`'s
`k` collapse at step 20; with the weld the same case converges (1500 steps:
every residual down ≥ 2.5 orders, continuity 1e-4, sensors drifting < 0.01 K
per 20 steps). Regression on the other cases: Tri_angle 0 vertices moved;
Slab 26, Shinaide 146, Base604 3682 vertices moved, every coverage gate still
exactly 1, cell counts within 0.02 %; Base604's minimum wall distance drops
from 4.1e-5 to 1.7e-5 m, so watch its solve.

## Reading the dictionaries the pipeline already writes

`dcCartMeshDict` carries the background grid and the options. Geometry,
`refinementSurfaces`, `refinementRegions` and `locationsInMesh` are read from
`snappyHexMeshDict` when it exists, so patch names come out identical to what
the boundary conditions already bind to — verified on SingleRack
(`ACU_1_Front_patch`, `Room_Floor`, `Default_Rack_1_frame`, `Room_Wall_1..4`, …)
and Base_leakage (52 STL regions + 6 domain sides).

A surface listed only under `geometry` is a refinement volume, never a patch: a
rack through-zone that claimed a boundary patch would put a wall across the
opening it exists to keep open.
