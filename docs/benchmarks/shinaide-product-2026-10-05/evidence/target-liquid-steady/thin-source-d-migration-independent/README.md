# Thin-stage source identity and D migration — independent offline audit

This is a diagnostic mathematical audit of frozen N2 and current-star meshes. Both actual native runs retain three failed strict checkMesh categories. Nothing here authorizes a mesh, changes a physical point, runs CFD, or proves all geometry checks.

The independently rerun all-eight-rank tool reproduces the classification below. 480 original input files (3,446,365,059 bytes) were freshly hashed and matched to AIR's frozen trace/written identities; actual underdeterminedCells sets are included. The two .cfdproj documents are identical after removing only meta.casePath, including their geometry, materials, run defaults and UID. Shared realized source-patch IDs have the same actual written name/type on each rank. Frozen trace-reader/geometry-source bytes, their dependency hash and tested native source identities are recorded here.

## D migration

A parent signature contains all complete outward coordinatecycles, patch IDs and zone IDs. Binary doubles are used without rounding; cyclic vertex rotation and face ordering are canonical, reversal is distinct. A retained numeric parent slot merely nominates a candidate. A child transition further requires a unique identical actual written base-piece coordinate/role signature in both runs. New/appended source slots remain unknown ancestry unless separately proved.

The 1,147 current D failures comprise 557 under exact unchanged source geometry, 477 under changed retained source geometry, and 113 under appended slots. Within the exact unique unchanged base pieces there are 556 bad→bad, two bad→good, and one good→bad. The latter is r6 parent9558: old c482690 D=0.0010149311458340791 → new c484392 D=0.0009731437730636501, with four selected faces. It is independently matched by full source/piece signatures; the whole +61 count is not a raw-label regression count.

All 563 current source parents producing these bad children have their original source-body selected-Sf determinant ≥0.001. This is only one source-body metric, not source-body allGeometry acceptance. 342 axis boxes of aspect≤4 contribute 566 failures; 128 thin axis boxes contribute 324; 93 nonbox parents contribute 257. Changed/append geometry contributes 590 bad children, including 354 from healthy-aspect axis boxes. More thin rounds alone are not justified by these data.

For r1 parent30430 the outer bbox and volume are preserved while its source face/vertex counts grow 12/17→45/67 and written children grow 23→98; D failures grow 0→18. Its aspect is 1.234375 and source-body D is 74459.587189. This is a tiling/cone-child issue rather than a still-thin body. AIR independently owns its actual new-plane propagation analysis.

## Three necessary thin families

For old rank1 parents652847/652920/652956, each current family is identified by every original outward halfspace and a positive closed convex axis-box part; every external subface must uniquely match an original support facet with the same patch/zone. Pairwise positive part-box overlap is exactly zero. Original facet area vectors and area first moments, directed boundary closure, total volume and volume first moment are preserved to recorded floating tolerances; no bbox-only ancestry is used.

| Old parent | Current source parts | Written children | Current D failures |
|---|---:|---:|---:|
| 652847 | 32 | 32 | 0 |
| 652920 | 30 | 81 | 0 |
| 652956 | 32 | 72 | 0 |

Maximum absolute volume error is 2.605e-19, maximum V-centroid moment error 1.362e-18, and maximum facet area-vector error 6.506e-19. Some parts still have aspect slightly above four; that is not evidence that these three D-clean families require more rounds.

AIR's actual guard context records 2,216 successful thin splits, 51 local predicted-face-fan veto visits and seven remote veto visits. These counters are visits, not unique source-parent counts. Parent652920 had a round3 predicted-fan veto but subsequently has the complete D-clean family above. The independent audit does not duplicate AIR's global STL/point-prefix/veto proof.

## Exact axis-prism sufficient class

Among the 93 nonbox parents, 54 concave axis prisms contribute 90 D failures, three convex prisms contribute five, and 36 unsupported parents contribute 162. The unsupported class remains unknown. Across their three axis attempts the reasons are 68 nonparallel/noncap face attempts, 30 interior cap planes, nine V/M1 mismatches and one wall-support mismatch; these are attempt counts.

The classifier requires finite positive directed-closed source shells; exact cap planes at axis ends; exact Fraction projected cap edge cancellation into one simple outline; identical end outlines; every side-wall projection on an original outline segment; and extrusion V/M1 agreement within 1e-10 relative scale. Collinear cap nodes may be simplified only in the analysis comparison: original source nodes are retained unchanged. No tolerance snapping is used in projected collinearity or outline identity. Axis/Sf equality is an intentionally sufficient gate.

A bounded possible next route is a shared canonical simple-cap partition into strictly convex polygons, extruding each partition along the same axis and preserving all original outline nodes, patch/zone roles, external interfaces, V/M1 and closure. It gives each piece a separate kernel rather than blindly moving one common apex. Conforming neighbor subdivision and the complete actual strict checker still have to be implemented and proved. The 36 unsupported parents and the dominant healthy-box tiling issue are not covered by this proposal.

## Reproduction

From the integration checkout, with input trees preserved or restored inside the repository:

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python tools/audit_air_thin_source_migration.py --old-mesh ../air/.codex-tmp/strict-stage-conforming-cones-nonorth-sequential/Shinaide_liquid_1d98c44a/Mesh --new-mesh ../air/.codex-tmp/strict-stage-conforming-cones-thin-current-star/Shinaide_liquid_1d98c44a/Mesh --trace-tool docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/thin-source-d-migration-independent/frozen-trace_reader.py --output-dir .codex-tmp/thin-source-reproduction --box-family 1:652847,652920,652956

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python -m pytest -q src/backend/tests/test_air_thin_source_migration.py --basetemp=.codex-tmp/thin-source-reproduction-pytest

The actual offline CLI result and 18-test log are retained. Tests cover orientation/rotation/ULP/role identity, compacted patch namespaces, selected tensor masks, subfacet role/support rejection, simple/concave/collinear/near-prism geometry and three compact actual frozen fixtures. The initial four test failures were authoring expectation mismatches (floating determinant equality, a box having multiple valid extrusion axes, and choosing the first valid axis), then corrected without relaxing the classifier; they are not a native before/after claim.

Before the AIR worktree is deleted, preserve the original trace and polyMesh bytes listed in input-identities.json. Compact fixtures support the local classification tests but do not replace the all-rank original inputs.
