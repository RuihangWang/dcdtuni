# Actual written cone failures: missing bases and cyclic vertex zero

This audit diagnoses a rejected experimental native output. It does not accept the Shinaide mesh. Native meshing took 75.435 s and the API/checker sequence took 89.740 s, but the 5,892,014-cell output failed full topology with 38 illegal/open cells and subsequently rank 6 global-edge transform fatal. A rejection within the deadline is not a mesh PASS.

All 38 cells in six ranks have exactly three triangular faces and four retained point IDs. Their illegalCells and zipUpCells sets match exactly. Unpaired outward directed edges form one closed triangle per cell. Reconstructing that missing triangle from the exact written float64 coordinates gives areas at most 2.7755575615628914e-17 m2. Every inferred missing base is below the existing writer's area-drop condition of 1e-12 m2. Directly reproducing the writer's floating-point Newell formula for every rotation and reversal of each missing triangle gives a maximum area of 5.717775942840641e-17 m2, so this conclusion also holds for that numerical formula. The shortest edge or point-pair distance among these cells is 0.004888873616210931 m; all four IDs remain. These observations do not support attributing these holes to one-micrometre point merging.

For example rank 1 cells 476561 and 11671 are opposite tetrahedra adjacent to the missing base with point IDs [20144,20146,20148]. Its points are [2.2014,1.597222222,3.9448875], [2.2014,1.597222222,4.00660625], and [2.2014,1.5972222220000003,4.0220359375]. The centimetre-scale edge points are effectively collinear; a 2.22e-16 y difference produces a roundoff-scale positive area. The cone's relative-positive-fan condition could admit this triangle, while the existing writer would remove it. The actual missing face has been inferred from written edges; the first prewriter mutation was not recorded, so this is distinguished from an instrumented branch trace.

The cyclic audit examines all 24 same-rank cyclic pairs, with 25,186 faces per side. Exact geometric polygon multisets and geometric polygons at each relative face index match on every pair. Nevertheless, 28 corresponding faces have different vertex zero or the wrong reversed point sequence. Applying the installed cyclicPolyPatch point-pair formula yields 42 first nonidentity assignments between different physical mesh points. The report includes every mismatch and its coordinates.

The actual fatal IDs can be reconstructed independently. Installed globalMeshData.C:2090–2135 collects coupled faces in boundary order; PrimitivePatchMeshData.C:86–98 numbers points in their first face encounter order. On rank 6 this gives:

| coupled local ID | written mesh point | coordinates (m) |
| --- | --- | --- |
| 774 | 443569 | [6.373275, 2.7951388888125, 2.6950828125] |
| 1236 | 552380 | [6.373275, 2.802500009536743, 2.6950828125] |
| 1237 | 574409 | [6.35765, 2.802500009536743, 2.6950828125] |

These IDs and coordinates match the original rank 6 fatal message. On merged_perforatedBlankingPanel_4U relative face 580, master face 2041109 is [774,1236,1237] in coupled-local IDs and slave face 2043040 is [1237,1236,774]. Installed cyclicPolyPatch.C:1033–1056 requires corresponding face index and corresponding vertex zero, then pairs indexA with (size-indexA)%size. The written sequence thus incorrectly pairs 774→1237, 1236→774 and 1237→1236. The correct same-start reversed sequence is [774,1237,1236]. Adjacent original quad relative face 581 has the correct start and reversed order. This actual cyclic vertex-zero mismatch is separate from any possible processor centroid-sort instability.

A separate source-level risk also exists in the previous processor fuzzy centroid comparator: centres A=(0,0,0), B=(0.75e-6,-1,0), C=(1.5e-6,-2,0) produce A<C, C<B, B<A with the one-micrometre lexicographic gate. This violates std::sort's strict-weak-order requirement. It is a concrete comparator counterexample, not evidence that this caused the current same-rank cyclic fatal.

The proposed native repairs are being performed by the air owner in a new isolated run. Matching each new triangle's writability to the unchanged writer threshold, retaining shared vertex zero when reversing a polygon, and using a strict canonical ordering address the demonstrated issues. This audit did not change source, binaries, mesh points, physical geometry or any quality/deadline threshold. Neither source review nor these local proofs replace the next full parallel checker or processor/cyclic geometric pairing audit.

Thirteen focused tests pass: the seven signed-outline tests plus six written-failure regressions covering roundoff-scale open-tetrahedron holes with millimetre-scale edges, zero-area missing bases, a positive-area hole that cannot be blamed on the writer floor, the actual cyclic three-point rotation error, correct quad correspondence, and the fuzzy-order cycle.

Recompute from the repository root while the original diagnostic mesh remains available:

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= python tools/audit_air_cone_written_holes.py --mesh .codex-tmp/goal-20261005/air/.codex-tmp/strict-stage-conforming-cones-signed-moment/Shinaide_liquid_1d98c44a/Mesh --output hole-audit.json

    python tools/audit_air_cyclic_written_order.py --mesh SAME_MESH_PATH --output cyclic-audit.json

    python -m pytest -q src/backend/tests/test_audit_air_cone_written_failures.py src/backend/tests/test_audit_air_signed_face_partition.py --basetemp .codex-tmp/pytest-cone-written-failures

The full written mesh hashes, every illegal-cell face and coordinate, all cyclic mismatch coordinates, installed source excerpts, raw failed run log, per-rank written boundaries and raw illegal/zip-up sets are retained here. No remote processor pairing or complete global-edge graph is inferred from the cyclic-only check.
