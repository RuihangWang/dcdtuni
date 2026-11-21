# Same-parent small cone unions: independent mathematical candidates

Both separate rank1/source-parent30430 candidates pass the local geometry, five absolute quality limits, and full original family role-inheritance checks. This is an offline diagnostic. It does not authorize a native mesh or establish a whole-case pass.

The frozen compact stencil contains 98 family children and 81 complete outside cells (179 cells, 494 faces, 145 original points). Both reports independently verify coordinates, polygons, owner/neighbour, physical patch, traced source base/zone and complete cell moments against the actual binary mesh and trace arrays. No coordinates move and no case is written.

| Separate union | Outer treatment | D | min weight | min ratio | max skew | max nonorthogonality |
|---|---|---:|---:|---:|---:|---:|
| 526902 + 526906 | six literal original facets | 0.08984335 | 0.1707317 | 0.09523810 | 0.7704465 | 61.80864° |
| 526919 + 526920 | same-physical-role edge union, five exterior facets | 0.1487595 | 0.08508914 | 0.03428571 | 0.2483440 | 49.24682° |

Each candidate replaces two cells with one. All 177 other complete cells have bit-identical recomputed centres and volumes. Directed boundary edges occur exactly twice in opposite directions. The actual stock other-face-centre predicate has no trigger; centroid pyramids, face-centre tet fans and shared original-point tet fans are positive. Original outer facets preserve their physical roles and outside neighbour identity. Relative volume errors are 1.26e-16 and 2.30e-14; first-moment differences are 3.79e-21 and 1.69e-18 m⁴ (normalized by volume and local span: 1.90e-14 and 1.49e-12).

The second candidate joins only faces1929316/1929317: same ACU_01_Front_patch, zone−1 and traced base1959121, with no outside cell. Exact opposite directed common edge27752–576508 cancels. All four original outline nodes remain. The joined polygon independently preserves vector area and unsigned area exactly; its area-weighted centroid differs by 6.28e-16m. A boundary facet count change here does not change the physical patch outline or measure.

For the first candidate, using the minimum cell label requires exchanging owner/neighbour on face1502868 and reversing its vertices while retaining vertex zero. The canonicalization audit proves the area vector changes sign exactly, face/cell centres and cell volumes are unchanged, and D/weight/ratio/nonorthogonality are unchanged. Skew changes only by 6.94e-17 from floating-point operation order. A real writer must perform this orientation repair before output.

Role inheritance is explicit: the merged cell inherits both original cell roles and the joined physical facet inherits both original facet roles. Tests preserve counterexamples where a replacement reuses an old failing numeric label, deletes a passing original role, and would evade a label-set-only check. Nonfinite metrics, disconnected groups, mismatched physical roles, affected unknown coupled interfaces, and wrong shared-edge orientation are rejected.

Both candidates separately reduce this family’s determinant failures from18 to17. Its existing12 low-weight faces and1 nonorthogonality failure remain. These results neither prove a combined merge nor repair the full mesh.

Reproduce from the repository root:

    python tools/audit_air_cone_small_union_independent.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-small-union-independent/p30430-complete-written-stencil.json --group 526902 526906 --verify-mesh --output .codex-tmp/small-union-no-coalesce.json
    python tools/audit_air_cone_small_union_independent.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/conforming-cone-small-union-independent/p30430-complete-written-stencil.json --group 526919 526920 --join-physical-faces 1929316 1929317 --verify-mesh --output .codex-tmp/small-union-physical.json

Omit --verify-mesh when using only the self-contained preserved compact stencil. The flag performs additional checks against the frozen original binary mesh path, which must still exist. The saved reports retain its hashes.

    python -m pytest src/backend/tests/test_cone_small_union_independent.py -q --basetemp=.codex-tmp/small-union-pytest

The 18 semantic regressions passed. Full native allTopology+allGeometry checking, parallel/coupled validation, source identity and the300s budget remain required independently.
