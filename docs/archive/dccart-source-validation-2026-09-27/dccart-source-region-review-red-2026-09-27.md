# dcCart source-region candidate: native review counterexamples

Status: **review RED; do not integrate `d449aca9` as a general sheet fix.** These
fixtures run the actual `dcCartSurfaces` and `dcCartCutCells::cutBaffles` kernel
through the existing native `dccartSheetProbe`. They do not run a whole mesh or
CFD solve. The isolated review branch adds only tests and this document; the
candidate algorithm is unchanged.

## Pinned input and reproduction

Use `src/backend/tests/test_dccart_source_region.py` in the review worktree at
`/mnt/lv/home/litianyu/benchmarks/dccart_region_review_worktree_20260927`.
The frozen candidate and pre-change probe binaries are under
`/mnt/lv/home/litianyu/benchmarks/shinaide_dccart_source_region_trial_20260927/{bin,baseline-bin}/dccartSheetProbe`.
Each invocation uses CPU affinity 0–3, one OpenMP/BLAS thread and hidden CUDA:

```bash
source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh
conda activate dcdtuni
cd /mnt/lv/home/litianyu/benchmarks/dccart_region_review_worktree_20260927
export CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DCDT_DCCART_SHEET_PROBE=/mnt/lv/home/litianyu/benchmarks/shinaide_dccart_source_region_trial_20260927/bin/dccartSheetProbe
taskset -c 0-3 nice -n 10 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python -m pytest -q src/backend/tests/test_dccart_source_region.py
```

For the pre-change comparison, change only `bin/` to `baseline-bin/` in
`DCDT_DCCART_SHEET_PROBE`. The full captured outputs are
`/mnt/lv/home/litianyu/benchmarks/dccart_region_review_evidence_20260927/{baseline,candidate}.log`.
The existing B06/B07 correction test passes only on the candidate. The three
new test outcomes are:

| Fixture | Pre-change | `d449aca9` | Meaning |
| --- | --- | --- | --- |
| Two different STL solids, same B07 footprint, parallel planes 28 µm apart with opposite normals; legacy input represents two sides of one physical wall | 1 sheet, 3 cells, one cut plane; new guard passes | 2 sheets, 4 cells, cuts at 2.696872 and 2.6968999 m; new guard fails | **New compatibility regression**: an unresolved legacy wall-pair becomes a 28 µm fluid cell. A future explicit pair contract may reject this input rather than silently accept it. |
| One STL solid containing disjoint B06/B07 panels 28 µm apart, with facet order reversed | B06-first cuts B07 at 2.69689989 m; fails | Same failure; B07-first cuts at 2.696871996 m | **Existing gap**: the 0.1 mm plane bin chooses the first triangle's origin and is order-dependent. |
| One STL solid containing disjoint B06/B07 panels on planes 1 mm apart | `have=0.12816`, `want=0.06408` m²; fails | Same failure | **Existing gap**: same-region 5 cm dedup drops an independent panel's expected area, so a lower-bound coverage gate can pass despite a hole. |

The two original candidate tests remain green on the candidate. The resulting
suites are **baseline: 2 passed, 3 failed** and **candidate: 2 passed, 3 failed**;
the failed test identities differ, as the table shows. The older six-case
probe's connected, coplanar, *different-solid* assertion also changes sheet
count from one to two, but a direct native probe produced the same three mesh
cells, cut plane and `have=want=0.08544 m²` on both versions. That assertion
alone does not establish a geometric regression.

A separate same-region opposite-normal check also showed that translating an
otherwise identical 1 mm two-sided wall from z=0 to z=2.7 m changes expected
coverage from 0.08544 to 0.17088 m². The signed-offset comparison in
`dcCartCutCells.C` is not translation-invariant for opposite normals. This is
inherited behavior and is not part of the three committed RED tests.

## Integration decision

`d449aca9` fixes the actual Shinaide B06/B07 different-solid source-plane
mixing, reducing low-determinant cells from 76 to 3 in its isolated full-case
trial. It also changes *all* distinct-region near-plane legacy surfaces into
independent sheets, including unresolved two-sided thin walls. The initial 284-row Shinaide source-UID preview did not annotate its blanking-panel
solids. A newer 354-row preview includes them, but this candidate does not
consume those UIDs; STL region names alone cannot prove independent physical
barriers.

A safer migration keeps the old grouping and old coverage rule for wholly
unannotated inputs. UID-based isolation should activate only when all baffle
regions in the relevant faceZone have validated source-face identity and an
explicit zero-thickness-sheet representation. Mixed annotated/unannotated
regions in one faceZone must fail before meshing. Two different source-face UIDs
with nearly coincident projected footprint still require an explicit pair or
an ambiguity rejection; UID inequality does not prove that the space between
them is fluid. The strict Shinaide wind mesh still fails five `checkMesh`
checks, and no CFD solve was started from this candidate.
