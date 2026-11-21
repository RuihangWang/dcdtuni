# Independent written-mesh verification of CUA15fe0b05

The 35 recorded fixed2 literal unions are independently recovered and replayed
against the frozen literal-OFF baseline. All 35 resulting actual written cells
and their literal exterior shells pass D/W/R/skew/non-orthogonality. The complete
stock mesh still fails three checks and is not authorized as a transient seed.

The baseline is AIR/.codex-tmp/strict-stage-conforming-cones-thin-current-star/
Shinaide_liquid_1d98c44a/Mesh (41c554a7). The actual final mesh is
AIR/.codex-tmp/cases/Shinaide_Liquid_LiteralUnion_CUA/Mesh (15fe0b05). The 96
input-stage source arrays/manifests are byte identical. All native coordinates,
surviving incidence roles, source-parent/base trace, and exterior polygon outlines
match. Every surviving actual boundary face has its patch name/type and the full\nsemantic boundary body (including cyclic/processor references and transforms)\ncompared after index remapping. All eight ranks have five named face zones with\nzero memberships and zero cell/point zones, verified from payloads, not assumed\nfrom hashes. Actual final compact faces use final patch roles. Five untouched ranks have eight polyMesh files each byte identical.

| Stock raw written sets | Baseline | Final |
| --- | ---: | ---: |
| Determinant cells | 1147 | 1111 |
| Low-weight faces | 102 | 102 |
| Low-volume-ratio faces | 2 | 2 |
| Skew faces | 0 | 0 |
| Non-orthogonal faces | 817 | 809 |

Native role comparisons have no new bad roles. The projected baseline D role
set contains 1146 survivor identities because one union combines two original
bad cells, 661502 and 661513; raw cardinality remains 1147. Each original role
is separately verified in the sequential stencil. See raw-count-reconciliation.json;
original SSE numbers are preserved.

Each accepted shell is also uniquely decomposed into two tetrahedra using only
the six original exterior triangles. The hidden shared triangle and both old
tetrahedral vertex sets match the literal-OFF baseline. All affected outside
cells have complete original faces, and all metrics are recomputed from points
and OF face/cell formulas. The merged cell inherits both old D roles. Surviving
face roles retain W/R/skew/N passing status; old bad severities do not worsen.
The volume and centred first-moment guards, edge closure, official face-centre
concavity predicate, planarity, vertex supports, and centre/original-anchor tet
fans pass on each complete affected stencil.

Written face 0 is not globally bit invariant: 13 recorded non-coupled internal
triangles rotate their cycle start (9 on rank1 and 4 on rank3). Their oriented
outline is identical. Coupled faces and every unchanged face retain point zero.
This distinction is explicit in each rank audit. Recorded exterior Sf vectors
have zero error after role sign matching; maximum FC difference is
4.440892098500626e-16 m.

Among 652 complete non-core outside cells, 608 centres and 632 volumes are bit
identical. Maximum actual baseline/final centre difference is
2.175583928816829e-15 m; maximum relative volume difference is
2.2258697045129955e-14. These floating differences are retained, not described as
bitwise equality. Sequential replay/final written centre and relative-volume
discrepancies are at most 2.0350724194510405e-15 m and 1.796842152958367e-14.
Native old/new D log discrepancies are at most 8.326672684688674e-17 and
4.440892098500626e-16. Native raw callbacks, flags, whole-job wall clock and final
checker acceptance remain governed by the original AIR/root CUA receipt.

Reproduce from the integration repository (read-only inputs; repository-local output):

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 \
    taskset -c 24-31 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python \
    tools/audit_air_literal_union_written.py \
    --baseline /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/strict-stage-conforming-cones-thin-current-star/Shinaide_liquid_1d98c44a/Mesh \
    --final /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/cases/Shinaide_Liquid_LiteralUnion_CUA/Mesh \
    --output .codex-tmp/primary-literal-written

The three gzipped baseline compact stencils preserve complete local original
points/faces/cells in native IDs. Original accepted JSONL bytes, all rank hashes,
the frozen source snapshot, and oracle/tool identities are retained here.
Full meshes remain necessary to repeat whole-rank topology and stock-set checks.
The semantic pytest file additionally checks two-bad-role accounting, unique
old-tet recovery, malformed outlines, cyclic-start semantics, open shells,
nonfinite metric rejection, physical/coupled role and source-zone tampering,\nnonempty zone refusal, invalid native maps, declared set-count mismatch, and\nthe remaining whole-mesh failure. All 20 semantic tests pass.
