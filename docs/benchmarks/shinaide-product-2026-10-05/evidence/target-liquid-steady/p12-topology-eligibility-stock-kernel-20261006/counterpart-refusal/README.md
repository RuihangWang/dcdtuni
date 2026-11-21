# One minimum synchronized counterpart closure — rejected

This is a single memory-only actual-array diagnostic on the frozen P12 rank-0 halo. It does not run the business mesher, checkMesh, solver, or any API, and does not modify production source.

The initial same-origin pair 19189/538794 has a positive literal-shell determinant but real OpenFOAM 2412 checkConcaveCells rejects its four exact coplanar face-centre pairs. The installed check intentionally includes planar ties. The first frozen proof is ../same-origin-packet-design-20261006/manifest.json.

The minimum counterpart relation derived from that shell is three pairs:
- 19189/538794, source parent 19921;
- 535534/535535, source parent 18882;
- 538806/538807, source parent 19921 (quad pyramids).

The root directory preserves the earlier sufficient strict-outline screen, which retained all faces because the coalesced outlines have original collinear nodes. The separate v2 allows exact nonnegative convex support with those original nodes retained. It uses exact plane tests, directed-edge cancellation, identical remapped owner/neighbour, patch/written-zone/source-base, and exact signed area plus first moment. It coalesces only two face pairs whose neighbours become the same after synchronized cell replacement. Distinct remaining neighbours are never merged into one face.

## Actual installed result

The real Foam primitiveMesh API kernel uses 41 complete old cells, 122 complete facets and 39 original DP points, and reconstructs 38 cells/117 facets. It calls the installed face/cell geometry, determinant, checkConcaveCells and skew kernels. The original selected internal-Sf mask is retained even where the compact view exposes an outer halo face as a boundary face. Its traversal is internal-first/original face ID then boundary; it is not claimed identical to full-rank writer arithmetic.

The new core determinant is **0.0006222031401241677**, below the unchanged .001 gate. The pre-coalescence literal value .0034444042911781466 cannot be reused after selected face count and tensor normalization change. Core coplanarity disappears, but survivors 535534 and 538806 still fail installed concavity with 2 and 4 directed plane ties. The remaining counterpart faces have distinct actual outside IDs. One pair also has different source bases.

The measured complete stored metric scope has D 4→3, W/R/skew 0→0, N warnings 4→3, no original passing-role regression. This is an improvement diagnostic but **not a valid repair**: the stock concavity failures and mandatory merged-core D failure remain. Positive centroid pyramids, closed directed shells and centre-fan tetra quality do not override those failures.

All original point coordinates are unchanged. Exact Fraction shell volume and first moment agree for each pair, the six-cell cavity, and every stored outside cell. Physical facet roles/footprints are preserved. Written empty zones are bound; they do not create unknown source/material authorization.

## Reproduction and preservation

v2-nonnegative-convex-outline/installed-kernel-build-receipt.json records the real DP Int32 OpenFOAM compile, not named stubs. Build and run exit 0; the run is a local no-write geometry kernel, not business checkMesh. The source, generated literal fixture, executable, raw JSONL, exact conservation replay and all result files are in the manifest. Run replay_audit.py to assert the recorded local hard counts, inherited passing roles and exact conservation. Existing source/tool/library inputs are SHA-bound in dependency-bindings.json. Production C/H/helper hashes remain the supplied live baseline.

A subsequent legitimate route could change the internal cavity partition or use a larger fully synchronized polycell closure while preserving the unchanged exterior, roles, full neighbour metric stencil and true installed formulas. Its existence is unproved; this result does not authorize a source exception, global predicate change, wider sweep, or native import.
