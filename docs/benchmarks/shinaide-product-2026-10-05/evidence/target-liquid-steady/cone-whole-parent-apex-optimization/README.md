# Offline whole-parent AP optimization on frozen N2 stencils

Three geometry-selected, same-rank complete original-parent stencils were searched. Two retained internal-AP proposals improve determinant and nonorthogonality while passing the complete local geometry/conservation and five-metric Pareto guards. **None satisfies all five absolute stock thresholds.** A third supported parent has no meaningful retained improvement; three other representatives are refused because their coupled exterior geometry is unavailable.

| Frozen parent | Children / affected faces / complete exterior cells | Original → retained minimum D | Original → retained max N | Full local five-metric pass |
|---|---|---|---|---|
| r0 p19921 | 22 / 58 / 15 | 1.178081e-6 → 6.456945e-6 | 80.336175° → 77.382485° | No |
| r3 p10906 | 24 / 66 / 16 | 4.126942e-6 → 7.403530e-6 | 83.284971° → 82.865082° | No |
| r6 p9316 | 11 / 30 / 8 | 0.000366150; no meaningful retained improvement | 74.805761° | No |

The unchanged absolute thresholds are D≥0.001, interpolation weight≥0.05, volume ratio≥0.01, skew≤4 and nonorthogonality≤70°. These results are local offline candidate evidence. The actual frozen N2 product mesh still failed three stock checks (D1086/W110/R2); this tool neither runs nor authorizes a native mesh, solver or product case.

## Geometry and method

Inputs bind original source-parent planes, written points/faces/owner/neighbour/boundaries and native-to-written traces by SHA256. The single new common AP is proven internal to that original parent's children. All fixed source/STL points and base polygons are retained. Exact original-parent height necessary bounds exclude known thin impossible parents 652847/652920/652956 before selection. A necessary upper bound above the threshold is not a feasibility proof.

The fixed-base-necessary-bound-audit.json also rechecks all 14 qualified bad tets in these supported stencils against the rigorous 54/128 A²/P⁴ bound on their actual selected-face branch. None of these fixed-base bounds is below 0.001; this does not prove feasibility in the constrained original parent.

The offline SciPy SLSQP problem has four variables: three source-bounds-normalized AP coordinates and a worst-child determinant epigraph. Seeds come from the actual written AP, original source centroid, original-plane kernel centre and a W/R/N proposal maximum-slack point when available. A seed outside the strict original-plane kernel is rejected. No physical coordinate constants enter the algorithm.

Fast iterations compute the actual selected-face area-tensor determinant, each affected interface's weight/ratio/skew/nonorthogonality, and original-plane margins. Old passing roles must remain passing; original worst failed severity cannot worsen. Candidate construction uses the existing geometry-derived W/R/N proposal rows. A floating LP or nonlinear solver success is only a proposal. Each solved point and generic 0.25/0.5/0.75 blends from the actual AP is rechecked by the full original stencil evaluator: positive volumes/pyramids/tet fans, closure, plane/polygon support, face-centre predicate, point clearance, parent volume/first moment and all five actual Pareto metrics. Relative D improvement must exceed 1e-8, so a one-ULP change is not reported as progress.

fast-evaluator-baseline-comparison.json records zero absolute difference against the complete formula evaluator for all supported original D/CC/V/W/R/S/N arrays. Tests also compare moved faces/centres at geometry-derived perturbations. This comparison verifies the offline evaluator; it does not replace a full installed stock checker.

candidate-preservation-audit.json independently rechecks each retained proposal: fixed coordinates, boundary face area/centres/area moments and complete exterior cell centres/volumes are exactly unchanged. Both parent volume sums are identical; volume first-moment differences are 8.94056e-19 and 1.93948e-18 m⁴. This audit was added after the retained search outputs and leaves those original JSON bytes untouched.

## Reproduction

Run inside the integration repository with its existing Python environment and CPU-only one-thread settings:

    PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= python tools/audit_air_cone_whole_parent_optimizer.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-whole-parent-apex-optimization/rank0-parent19921-input.json --output .codex-tmp/whole-parent-recheck.json --maxiter 120

Use the corresponding input for p10906 or p9316. The final source adds the explicit preservation audit to reproduced output; retained search outputs, first-search outputs and stdout remain unchanged. Runtime/optimizer paths and tiny rounding may differ across SciPy/platform versions. Candidate geometry and actual metrics can be rechecked directly without repeating optimization.

    PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= python -m pytest src/backend/tests/test_cone_whole_parent_optimizer.py -q -p no:cacheprovider --basetemp=.codex-tmp/whole-parent-optimizer-recheck

16 tests passed; the exact original run timing is preserved in pytest-output.txt. Three search times were 1.78/2.19/1.57 s and describe offline search only, not mesh-generation or API deadlines. receipt.json records environment versions and raw result summaries. sha256.json binds all evidence files, final tool/test and evaluator dependencies.

## Limits

No search infeasibility result proves all APs or topologies impossible. The local fixed-point clearance does not establish all native point clearance. Coupled remote geometry is refused rather than invented; full cyclic/processor and installed stock quality remain separate native gates. The original threshold and 300 s whole-job deadline are unchanged. Neither a partial Pareto improvement nor a successful optimizer status is mesh acceptance.
