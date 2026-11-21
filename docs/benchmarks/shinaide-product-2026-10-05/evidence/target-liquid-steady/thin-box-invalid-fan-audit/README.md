# Frozen thin-box experiment: source reconstruction of all 165 refusals

The actual React/API job bf25f0fa failed before the writer and before stock checkMesh. It was an experimental default-off topology branch, not an accepted mesh. The frozen source and full logs identify 1727 primitive thin-box splits, followed by a globally reduced invalid fans165 and zero near-coincident new points. Whole product time was 152.343422s, including the 51.130568s export and 95.082531s mesh API segment.

This independent audit reads only the frozen input-stage arrays and hash-bound native source. A full eight-rank screen inspected 11,948,055 alive faces with at least four nodes; 200 had an edge at most 1e-7m. That screen size is diagnostic, not a meshing tolerance. All saved face labels and coordinates were then independently matched against the raw source arrays.

Two distinct native branches explain the observed refusal count:

- 162 convex outlines contain retained collinear nodes and no original-vertex fan satisfying the unchanged signed-area and writer-area tests. Their source code selects an OF face-centre fan. One piece per face spans an edge only 1–2 representable double ULPs long. Its reconstructed Newell area is at most 1.963e-17m², below the original 1e-12m² writer floor. The rank1/3/6/7 counts are 9/29/114/10. These faces are absent from all quality-refan-changed witnesses, so the fallback is not replaced there.
- Three additional nonconvex outlines have no legal original-vertex fan: rank3 faces35379/35501 (nine nodes) and rank6 face1857488 (13 nodes). Their shortest edges exceed 15mm, so this is a separate cause. Exact binary-coordinate rational checks establish that each original outline is planar and simple; retained collinear boundary nodes defeat the current single-anchor algorithm. Source selection marks each face's incident cell, and its pieces.empty branch increments the counter once.

The 162 piece refusals plus three empty-fan branches match 165. This is a source-and-selection reconstruction, not a native dump naming each final failed piece. The raw native partition records omit those branches. Their 1739 saved records contain zero conservation failures; all 70 quality-changed face identities are bound in the input. No subsequent mesh quality or physical accuracy follows from counter agreement.

The 38 other short-edge snapshots are convex four-node caps with splitFace=false. They do not contribute to this fan counter. Their exceptionally small area is a separate potential writer-drop hazard if execution were allowed to continue; this audit does not suppress them or authorize continuation.

Exact coordinate membership tests against the prior frozen N2 source show neither endpoint of any of the 162 short edges in that earlier point array. This is coordinate membership only, not native birth ancestry. It does not establish which exact clipping call first produced a point.

The frozen thin-box crossing already assigns the new intersection's axis coordinate to the transported actual plane. The source's clean-plane screen examines candidate-own vertices, while later exact-side classification (delta==0) operates on the current mesh and groups exact double planes. A current whole-star clearance/veto is therefore a useful source qualification candidate. Bounded IEEE ULP incidence could also be studied while preserving all original point coordinates. Neither option changes the writer floor, STL geometry or stock quality thresholds. A deterministic polygon partition may address the three nonconvex fan failures separately; its complete geometry and downstream quality still require independent guards.

frozen-native-branch-extract.json binds the exact 713946-byte C source and preserves the relevant source lines, including helper operation order, split selection, writer floor and both refusal counters. source-input.json binds the full raw rank arrays, original API receipt/SSE and every partition witness. The two unrecorded-filter JSON files are AIR's original read-only search results, copied byte for byte; this tool rechecks their three remaining source faces independently. The report's full native-final-fan-dump flag remains false.

Recompute without running native meshing:

    env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 taskset -c 24-31 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python tools/audit_air_thin_box_invalid_fans.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/thin-box-invalid-fan-audit/source-input.json --output .codex-tmp/thin-box-fan-recompute.json

The optional --verify-source argument takes the frozen Mesh path and rechecks raw saved labels/coordinates; --baseline-source with --membership-output repeats coordinate membership. For a fresh full short-edge screen, --scan-mesh and --scan-rank read one raw rank; they write only the diagnostic JSON output and require no solver.

    env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 taskset -c 24-31 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python -m pytest -q src/backend/tests/test_thin_box_invalid_fans.py --basetemp .codex-tmp/thin-box-fan-test-recompute

Twelve regressions pass in 1.92s. They check all real snapshot classifications/counter scope, one-ULP identity, the unchanged strict writer floor, rotation/reversal coordinate preservation, non-split caps, exact simple nonconvex outlines and source-identity corruption. The first test's overly specific expectation of an exactly zero rounded Newell area is preserved in pytest-first-output.txt; the final audit preserves the actual native centroid operation order and checks the original absolute writer predicate.

Root independent verification (2026-10-05): all 13 saved manifest entries matched exact bytes and SHA256; 12 regressions passed in 2.69s. Direct raw-array verification matched all eight rank snapshot face labels/coordinates and the three additional no-anchor faces. All 25 common report keys, including complete reconstructed rows, matched the frozen report exactly. root-direct-reconstruction.json and its log preserve that recomputation. This remains pre-writer failure diagnosis, with no final per-fan native identity dump and no accepted mesh.
