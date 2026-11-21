# Independent same-parent union and conforming re-fan mathematics

These are pure mathematical diagnostics of actual trace-bound coordinates. No production C++,
native mesh, solver, API, browser or input project was modified. Local validity is kept separate
from full-parent and full-target acceptance.

## Fixed minimal unions

For two fixed planar bases sharing a fixed edge and common apex a, the actual cone centres are
C_i = (3 B_i + a)/4, where B_i is the installed-OF base area centre. The side normal is proportional
to the cross product of the fixed edge and a point-to-apex vector. Dotting that normal with each
in-plane base-centre offset gives a common altitude factor times the in-plane distance d_i.
Consequently, the installed interpolation weight is min(d_1,d_2)/(d_1+d_2). This holds for any
nondegenerate shared apex, including a tangentially displaced apex. It does not apply to bases
in different planes.

For parent 25147, union [507879,507880] against child 507881 has distances
0.021565855765056777 m and 0.0005210089296812456 m, giving weight 0.023589085045890232.
Parent 25148 has the same planar construction and weight 0.02358908504589022.
Five independently evaluated points in each actual kernel preserve this value within 1e-11.
Moving the shared apex therefore cannot repair these particular fixed minimal unions.

minimal-union-geometry.json records the exact base/edge coordinates. Noncoplanar interfaces
are explicitly marked inapplicable. coplanar-weight-invariance.json contains the formula and
direct installed-formula samples for every verified coplanar interface.

## Useful topology changes, with explicit remaining failures

Merging the whole original physical fan preserves its boundary but produces multiple coplanar
side facets against different adjacent cones. Those combinations are rejected by the strict
cell plane test. A three-to-two repartition preserves closure but creates an actual neighbour
volume ratio below 0.01; it is also rejected.

A different canonical fan anchor plus one adjacent triangle merge gives a constructive local
alternative. Parent 25147's original physical face has seven boundary vertices. Retain every
vertex and external side polygon, then replace its five old children with four cone bases:

- [19876,19877,43902]
- [19876,43902,43917]
- [19876,43917,43942,575910]
- [19876,575910,21167]

The third base is a convex quadrilateral. No new boundary point is introduced. Parent 25148
has the identical construction with point mapping
19876→19745, 19877→19742, 43902→43922, 43917→43921, 43942→575876,
575910→19985, 21167→19983.

The five-metric search uses the actual installed determinant, interpolation weight, volume
ratio, skewness and nonorthogonality formulas. It keeps their respective thresholds
0.001, 0.05, 0.01, 4 and a conservative 70 degrees. The latter is an additional conservative
candidate constraint, not a change to stock checkMesh's warning classification.

| Parent | Shared apex | Local min D | Local min weight | Local min ratio | Local max skew | Local max nonorth |
|---|---|---:|---:|---:|---:|---:|
| 25147 | [2.1565863539197214,0.876183015397571,4.007269004136064] | 0.00119998 | 0.0998562 | 0.0962876 | 0.520727 | 58.3365° |
| 25148 | [2.165668926409498,0.8669723014252045,5.233328828948712] | 0.00126639 | 0.0998562 | 0.112705 | 0.550275 | 55.2778° |

These local statistics include all four replacement children and all their interfaces,
using the real recomputed centres/volumes of neighbouring children. The complete parent
stencil is evaluated separately. Both complete parents retain other determinant and weight
failures, and all outside pre-existing low-determinant cells are reported unchanged.
These candidates must therefore NOT be installed as accepted whole-parent replacements.

The original minimal two-child unions for parents 30503 and 46851 also have local five-metric
feasible points (minimal-union-five-metric-candidates.json), while the complete parents still
fail. This distinction prevents a local scalar objective from hiding adjacent failures.

Every candidate preserves the complete parent's original boundary area vectors, area amounts,
area-weighted centres, volume and first moment within the recorded numerical tolerances.
All children retain directed edge closure, positive centroid pyramids, convex planar faces,
positive centre-fan/shared-base-point tetrahedra and original fixed point bits. Physical patch
roles are retained. Outside-cell geometries are fixed and recomputed rather than invented.
There are no affected coupled faces in these compact stencils; processorCyclic is not proved.

## Remaining source-face coordination

Parent 25147's child pair 507885/507886 on original internal base 2084724 retains an
apex-invariant bad weight of 0.02939290771484. Its opposite children are 507989/507990 in
parent 25234. Parent 25148 retains analogous low-weight interfaces on original bases
2084685 and 2084742; actual adjacent ancestry is recorded in internal-neighbour-ancestry.json.
A whole-parent solution needs conforming changes on both sides of those original internal
faces, followed by fresh evaluation of all affected children and outside interfaces.

A generic native implementation can enumerate legal canonical fan anchors and contiguous
fan-triangle merges, rejecting nonconvex polygons or cells and preserving every outline
vertex/patch/zone. Evaluate the worst installed metric over the entire affected stencil,
not only the replaced cone. Internal-face changes need synchronized matching subdivisions
on both sides. Exact metadata and shared point-zero contracts must be preserved; coordinated
processor or transformed-cyclic topology is outside this proof.

The constructive four-base result demonstrates that the fixed-triangle determinant and
fixed-minimal-union weight obstructions can be removed by changing the fan topology.
It does not establish that every case or the full target can be repaired by that construction.

## Reproduction and identity

- tools/audit_air_cone_union_repartition_independent.py: read-only compact extraction,
  exact external side preservation, minimal unions and two alternative physical re-fans.
- tools/audit_air_cone_union_candidate_search.py: seeded single-worker pure-math search
  on a compact JSON stencil; no native mesh is written.
- tools/audit_air_cone_candidate_independent.py: independent installed-formula geometry
  evaluator, also used for the separate full parent 9885 proof.
- source-identity.json: hashes of the actual rank-1 mesh, source/written trace arrays,
  ancestry samples and tools.
- refan-optimized-candidates.json preserves the initial four-metric candidates, including
  the front candidate's 74.36-degree nonorthogonality observation and failed whole-parent searches.
- refan-five-metric-candidates.json preserves the conservative local five-metric results.
- pytest.txt: 8 compact actual-geometry regressions passed on CPU 36–39; repository-local basetemp.

The searches and tests use repository files only. Final production acceptance still requires
the exact generated mesh to pass complete stock allTopology/allGeometry checks.
