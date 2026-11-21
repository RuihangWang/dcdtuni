# P12 independent raw structural recomputation

This is a second implementation of the P12 eligibility census. It reads the frozen selected binary owner/neighbour/face arrays and native/source-parent maps, plus the actual ASCII label sets. It never reads the saved rank census, classification, pair rows, or receipt totals as algorithm inputs.

The executed command was:

    GIT_OPTIONAL_LOCKS=0 PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= taskset -c 36-39 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/p12-census-root-independent-20261006/recompute_census.py

The subprocess stdout/stderr were saved in recompute.log. Exit status was 0. The raw calculation took 3.177 seconds, including fresh hashes before and after reading. To reproduce without mutating this freeze, copy the script into another NEW sibling directory beneath integration/.codex-tmp and run the copied script there; it deliberately derives the input sibling from its own path. The existing input and current output freezes must remain untouched.

Results independently rebuilt from raw input:

- 833 unique rank/compact D cells: 699 genuine four-triangle/four-point tetrahedra and 134 five-face/five-point quad pyramids. No cell has fewer than three faces.
- Exclusive coverage: 337 with a cross-source-parent complete-halo tet pair; 199 with a same-parent pair only; 163 tetrahedra with neither route; 134 quad pyramids outside the true-tet grammar.
- 1,375 unordered tet pairs: 1,102 same-parent and 273 different-parent. Complete first/second halo metadata screening admits 926 same-parent and 250 different-parent pairs.
- The 23 excluded cross-parent pairs touch coupled faces in their complete halo. This is a protected eligibility limitation, not a geometric impossibility certificate.
- 377 bad-D cells touch physical faces directly; 9 touch coupled faces directly. Physical shell faces are retained and do not themselves veto the metadata union route.

True tetrahedra require exactly four triangular facets and exactly four distinct vertices. Pair collection includes an actual bad-D tetrahedron, an actual internal shared face and an adjacent true tetrahedron; unordered pairs are deduplicated. The shell must contain six original faces after removing the shared one. The halo is rebuilt from both pair cells, all first neighbours, all second neighbours, and ALL their faces and points; it is never truncated. Coupled/unknown roles veto it, with caps 128 cells / 512 faces / 1024 points matching the frozen source. The world geometry and quality of these pairs were NOT evaluated.

Fresh SHA/size checks verify all 52 selected binary files and all 16 raw label sets against both the 98-payload input manifest and the P12 full-case manifest, before and after calculation. Four selected witness extras are hash-verified but are not needed for this structural classification. The frozen native reader is explicitly SHA-bound; label sets are parsed as inline ASCII even though their FoamFile headers say binary.

The selected 52 payloads do not contain zone dictionaries. Written empty zone membership is therefore inherited solely from the separate SHA-bound frozen 24-zone identity binding and its original-membership proof. It was checked for internal consistency and P12 file identity here, but not freshly parsed. Empty written membership is not permission to erase planned source/material/BC/zone roles, and this census does not issue a fresh source contract.

No mesher, checkMesh, solver, API, browser, source mutation, service operation, or Git/index mutation ran. No live P13 inputs were read. This is neither an emitted native ranked queue nor a per-pair refusal history. Existing same-parent consumption (223 old accepted unions) prevents treating all metadata-covered pairs as new successes. Stock quality, full lineage, installed arithmetic, planned semantic authority, and coupled certificates remain necessary.

Files: recompute_census.py is the independent implementation; recompute.log is its actual output; independent-summary.json records counts and scope; raw-D-cell-classification.json records all 833 actual identities/shapes/routes; raw-two-tet-pair-metadata.json records all 1,375 deduplicated actual pairs and complete-halo sizes; input-bindings.json pins all raw inputs and support files. manifest.json binds only this NEW audit's seven payloads.
