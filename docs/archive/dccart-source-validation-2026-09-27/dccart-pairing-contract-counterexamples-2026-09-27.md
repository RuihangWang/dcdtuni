# dcCart sheet/paired-wall contract: review counterexamples

**Stop condition:** candidate `1028a2c6` is not integration-ready. It fixes the
initial local plane propagation example but fails the two native review cases
below. This follow-up changes tests/documentation only. No production fix,
complete mesh or CFD solve was attempted after these failures.

## Native results against the fixed-version 1028 library

`test_dccart_sheet_support.py`: **2 failed, 4 passed in 0.82 s**.
The same real `dcCartSurfaces` and `dcCartCutCells::cutBaffles` probe is used.
Its only geometry-interface extension is configurable cell z bounds for the
translation; multiple STL files already enter through the geometry dictionary.
The source STLs are asserted unchanged after execution.

- `test_opposite_normal_wall_coverage_is_translation_invariant`: two same-zone,
  same-footprint faces 28 micrometres apart have opposite normals. Moving the
  whole fixture from z=0 to z=2.7 changes `coverage_want` from **0.08544 to
  0.17088 m2**. Subtracting separate signed plane offsets is not invariant when
  the normal signs differ. This assertion does not decide whether the pair
  represents one wall or two: either semantic choice must be translation-invariant.
- `test_same_aabb_complementary_triangles_keep_both_source_areas`: two separate
  STL sources contain complementary half-rectangles on one plane. Each has
  area0.04272m2 and the same AABB. Expected total **0.08544m2**, observed
  **0.04272m2**. Matching projected bounding extents do not prove duplicate
  coverage.

Evidence on40901:
`/mnt/lv/home/litianyu/benchmarks/dccart_plane_support_evidence_20260927/review-red.log`
and `review-red-fixtures/`. The native probe is in `review-bin/` and links the
frozen1028 library in `dccart_plane_support_relink_20260927/lib/`.

## What the current source knows

`dcCartSurfaces::baffleSheet` exposes zone, normal, origin and bounds.
Private `sheetTris_` stores pairs of surface-file index and triangle index;
`baffleSheetTrianglesInBox` provides the actual source triangles/region ids.
These establish geometric provenance. Neither a common file, common zone,
parallel normals nor nearby coordinates declares that two surfaces are the
opposing sides of one physical wall. One merged STL can contain many separate
assets; two files can also contribute complementary parts of one footprint.

## Proposed explicit contract (design only, not implemented)

1. Preserve stable physical barrier/asset UID and source-face UID through export,
   triangulation, sheet construction and cutting. A zone remains a boundary
   condition group, not a physical-pair identifier.
2. Default representation is an independent zero-thickness sheet: count its
   actual supported area. Do not infer a thin-wall pair from a distance or AABB.
3. A declared two-sided wall must carry an explicit pair UID, two side/source-face
   references, and the declared thickness/geometry relation. Exactly two sides
   belong to a pair; a side cannot participate in another pair. Select one
   coverage representative directly from this identity, never by chaining
   nearest-neighbour matches or considering previously skipped sheets.
4. Pair validation needs actual projected-coverage evidence. Prefer an exporter
   supplied canonical footprint/contour identity from the same wall primitive,
   checked against both side geometries. An AABB is only a fast rejection test.
   Different triangulations require a defined coverage-equivalence validator;
   do not improvise a polygon-union implementation in this patch.
5. Any distance comparison uses a common unit normal, for example
   `abs((origin_b-origin_a) dot n_reference)`. This fixes the sign convention,
   but does not by itself establish pair identity or fix complementary coverage.
6. Define sheet partitioning independently of triangle order. Prefer declared
   planar source-face identity plus edge connectivity, with geometric planarity
   checked against that source plane. Approximate coplanarity is non-transitive;
   merely choosing the first input triangle as a seed is not an adequate
   order-invariant contract.
7. Legacy files without pairing provenance need an explicit compatibility or
   migration policy. Do not silently claim the old5cm heuristic is equivalent
   to the new independent-sheet or declared-pair interpretation.

The other review examples are design constraints, **not executed in this round**:
independent same-footprint walls0.02m apart must remain two barriers; layers at
0/0.04/0.08m cannot pair transitively through an already skipped middle layer;
three shallow connected facets must not change grouping when triangle order
changes. The explicit identity/cardinality and deterministic plane contract
above address why geometric proximity alone is insufficient.

This requires an exporter/schema/kernel agreement beyond a safe local
normal-sign or tolerance patch. Preserve these failing tests and stop until
that contract is selected. Do not run full Shinaide with this candidate.
