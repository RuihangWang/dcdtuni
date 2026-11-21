# One actual concave axis-prism extrusion — rejected offline candidate

The frozen **rank 3 sourceparent 618482** is an exact concave X-extruded L-cap,
with 14 original nodes and nine source faces. Its current family has 19 written
conechildren. This tool tries one bounded reconstruction; it does not alter a
product mesh, run native meshing/checkMesh or authorize solving.

The two actual end caps have the same four positive canonical triangles. We
extrude these through two slabs divided by the **existing original X seam**
3.710737943649292. This produces eight positive triangular prisms. The bottom
seam separates physical patch 152 (...outlet_wall) from patch 151
(...outlet_p0); physical patch 153 is ...top. Every exterior fragment keeps
its original source face, patch/zone, and exact actual outside-cell identity.
Different outside neighbors and different physical roles are never merged.
Exact Fraction intersections create six new points, while all 14 original
binary-double coordinates remain bitwise unchanged. No snapping or coordinate
tolerance is used for original geometry or the clipping topology.

The candidate's local D/W/R/skew/N values are encouraging but **the actual
stock face-centre predicate rejects it**:

| Reconstructed scope | Result |
|---|---|
| Eight new cells | D min 0.00443025; positive edge-paired closed shells |
| 33 affected internal/parent-exterior faces | W min 0.230919; R min 0.112213; skew max 1.88703; N max 36.006 degrees |
| Volume / first moment vs original written family | errors below 1e-18 in this frozen local computation |
| Source exterior facet area vector / area first moment | errors below 1e-18, with unchanged source roles and outside ID sets |
| Stock cell face-centre concavity predicate | **2 new cells + 5 previously good outside cells fail**, all by coplanar fragments |
| Modeled stencil interfaces | 37 distinct pairs, reverse exact with one common rational point set |
| Outside-other interfaces beyond this modeled stencil | Seven faces gain edge nodes; the opposite geometry is not modeled or certified |

The two rejected new cells are new:0 and new:4: one side still contains
different coplanar actual-neighbor fragments on source face 740814. The five
newly rejected old outside cells are 175054, 175134, 466371, 687074 and 687075.
Their volumes/moments are preserved, but the new multiple coplanar facets are
not accepted by the full-geometry cell-centre predicate. Their D values do
not rescue this check. The output explicitly records acceptance:false and
REJECTED_OFFLINE_STOCK_FACE_CENTRE.

This is the installed-style checkConcaveCells face-to-other-face-centre
predicate, not a claim that a complete vertex-convexity requirement is
necessary. The utility caller in the preserved official v2412 source
../conforming-cone-quad-union-independent/upstream-v2412-checkGeometry.C,
lines 937–955, adds a failed check when this call returns true under
allGeometry. Its SHA is
95b223c7300c2072b5521dc0d15109531c8d6e5b03941d52f23a10c3508e23a3.
This candidate has **not** been written into OpenFOAM or run through the
utility; the failure here is a frozen-coordinate reconstruction of that
stock predicate. Face-tet tests and the unmodeled global stencil are not
claimed to pass.

## Necessary neighbor propagation and its bounded extent

When a new cut intersects an exterior facet whose actual outside fragment
is fixed, merely splicing new points or subdividing that interface can create
coplanar multiple faces in either cell. A conforming repair must propagate
the actual subdivision into its real neighbors (or design a synchronous
compatible partition); it cannot merge interfaces with different outside
IDs, silently drop boundary roles, or judge only the ideal prism.

For **whole-parent propagation of this existing X plane**, the five failed
outside written cells belong to four direct original sourceparents:
239231, 239312, 618043 and 239148. Those four are actual closed convex
axis boxes. Together with focal parent 618482, their complete current-source
vertex stars contain no face zone or realized coupled patch. Physical faces
are allowed only with their original role and outline retained.
This current-source snapshot does not infer unavailable preGhost cellRank
metadata or authorize a coupled operation.

The bounded audit examines exactly these five original shells. Their crossed
internal source faces reach **seven additional original sourceparents**:
239112, 239149, 239232, 239311, 239313, 239394, 618042.
Thus this particular five-parent whole-plane propagation region is **not
closed**. No recursion or search of those seven was performed; there is no
claim of a finite successful global propagation size. Protected zone/coupled
roles require a veto; regression tests inject each role and check that veto.
If the propagation cap is reached, this route must stop and report its
unresolved frontier. An alternative compatible cap partition may have a
different frontier.

## Frozen inputs and reproduction

rank3-parent618482-input.json contains the original source shell, all 19
written children, 19 actual external faces, and the complete 14 actual outside
cells. bounded-frontier-input.json contains the five original source shells
and their complete current original vertex-star face snapshots (coordinates,
point IDs, source owner/neighbour, patch/zone and realized role).
Both bind 25 actual source/written/boundary files by SHA/bytes and the trace
reader by SHA. The compact inputs can reproduce this proof after AIR is
archived; absolute mesh paths inside them are provenance only.

From the repository root, using the dcdtuni Python environment:

    E=docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/axis-prism-extrusion-independent
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 python tools/audit_air_axis_prism_extrusion.py --input "$E/rank3-parent618482-input.json" --output ".codex-tmp/prism-recheck/candidate.json"
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 python tools/audit_air_axis_prism_extrusion.py --frontier --input "$E/bounded-frontier-input.json" --output ".codex-tmp/prism-recheck/frontier.json"
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 python -m pytest -q src/backend/tests/test_air_axis_prism_extrusion.py --basetemp="$PWD/.codex-tmp/prism-recheck/pytest"

Fresh extraction, if the hash-bound AIR raw inputs still exist, is provided
by extract-compact-inputs.py --mesh ... --reader ... --rank 3 --parent 618482
--stencil-output <fresh-repo-path> --frontier-output <fresh-repo-path>.
It refuses to overwrite a frozen output. The generic reconstruction CLI
accepts only repository-contained paths and forbids overwriting its own input.

19 meaningful tests pass, including actual 2+5 failure preservation,
original-node/V/M1/role/outside-ID checks, reverse-exact modeled interfaces,
the unresolved frontier, and negative tests for wrong metadata, missing
neighbor geometry, incomplete facets, wrong old values, nonfinite coordinates,
incomplete source stars and protected roles. No production CPP, defaults,
thresholds, frontend, native process, API, browser or Git worktree changed.
