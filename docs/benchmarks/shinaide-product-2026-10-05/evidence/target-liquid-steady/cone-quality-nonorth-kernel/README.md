# Non-orthogonality candidate construction and selected-baseline protection

This is a read-only mathematical audit of actual Shinaide written geometry, not a case acceptance. The native two-pass experiment remains **full checkMesh FAIL3** (determinant, weight and volume ratio). No physical STL node, native mesh, acceptance threshold, production source or solver was changed by this audit.

## Mathematical scope

For planar cone bases, child centre C_i(A)=3B_i/4+A/4. Across a same-parent side, d=C_neighbour-C_owner is fixed and the oriented side area vector S_f(A) is affine, perpendicular to its fixed edge e. Let u be the normalized part of d perpendicular to e and v=e cross u. Writing s=S_f.u and t=S_f.v, alpha=cos(theta)|d|/|d_perp| and beta=sqrt(1-alpha^2)/alpha gives the exact acute-angle branch

    s > 0, beta*s-t >= 0, beta*s+t >= 0.

Zero d_perp, alpha>1 and nonfinite inputs cannot supply a candidate. At alpha=1, beta=0 still requires positive s. This is not a proof for changed topology or arbitrary warped bases.

For an external fixed face with fixed external centre, d(A) is affine. Sixteen transverse directions t_j use

    n.d > 0,
    cos(pi/16)*tan(theta)*(n.d)-t_j.d >= 0.

The cosine factor makes this polygon a **sufficient inner approximation** of the circular cone. An empty polygon does not prove the original circular constraint impossible.

Actual binary outlines have minute floating warps. The floating halfplanes, LP and vertex clipping are candidate generators. Complete original floating geometry and all five original quality metrics are reevaluated after construction. Candidate reserves (69.99999 degrees, .05000001 weight, .01000001 ratio) tighten proposal generation; the actual .001/.05/.01/4/70 acceptance thresholds are unchanged. Coupled remote/transformed/AMI/mapped stencils are explicitly unsupported.

## Actual complete local stencil

The already committed input in ../cone-quality-kernel/final-ratio-parents-input.json supplies rank 3, source parent 9836: all 36 children and 94 affected faces, including their fixed external neighbour geometry. Only the internal shared apex moves mathematically. The entire 744-row W/R/kernel system and 735 added angle rows are retained in parent9836-analysis.json.

Three generic proposals pass every local absolute quality threshold, every local five-metric Pareto gate and the geometry/volume/moment checks. Four near-original blends are valid partial improvements but retain a determinant below .001; they are not called all-quality passes. The external neighbours themselves may have pre-existing unaffected determinant failures: this result covers the 36 children and 94 affected interfaces, not an assertion that every external cell or the entire mesh passes.

| Derived candidate | min D | min weight | min ratio | max skew | max angle (degrees) | All five local thresholds |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| linearMaxSlack | 0.00265577918 | 0.0869586536 | 0.0767899309 | 2.31746764 | 68.6594015 | yes |
| vertexMean | 0.00141017662 | 0.0745380293 | 0.107388572 | 1.32037782 | 68.6529908 | yes |
| vertexBBoxMid | 0.00486016385 | 0.103363558 | 0.141292603 | 1.3412857 | 69.9868668 | yes |
| nearCurrent | 0.000238178679 | 0.05000001 | 0.058706072 | 2.43004175 | 69.99999 | D still fails |
| near75Mean25 | 0.000374677644 | 0.0552975057 | 0.0780457425 | 2.41776083 | 69.6796498 | D still fails |
| near50Mean50 | 0.000585523839 | 0.0610998317 | 0.0867679493 | 2.40906506 | 69.3483811 | D still fails |
| near25Mean75 | 0.000910468834 | 0.0674827609 | 0.0964883123 | 1.84448141 | 69.0061398 | D still fails |

All child edge cycles are closed with opposite orientation; strict vertex halfspaces/face-centre support, polygon support, planarity, positive pyramid volumes, shared original-point and centre-fan tets, writer area floor and parent volume/first-moment conservation are checked independently on the original full local stencil. All eight frozen source point arrays and all eight written point arrays were scanned separately; the three complete-quality candidates stay at least 42.416655 mm from those fixed coordinates, excluding only the old apex. This does not certify future jointly moved apices.

The mathematical coordinates are evidence, never a request to hardcode this case in production. Portable clipping uses the existing normalized convex-polytope algorithm; the scientific SciPy LP only cross-checks candidate construction. The replay uses scientific coefficients rather than claiming bitwise identity with native four-sample coefficient fitting.

## Genuine baseline regression and correction

native-baseline-regression.json preserves the first N-factor defect: across all eight ranks, the WR baseline has 1180 underdetermined cells and the first N factor 1087, but **six formerly passing labels become bad**. Actual written coordinates, complete cell topology, original source-apex bytes, native candidate profiles and core-topology hashes bind the cause. The candidate comparator uses the original source AP where those six determinants were already bad; it can therefore replace a WR-selected repair by a new bad value.

The second native experiment first completes the unchanged WR pass, then appends N candidates against that actual selected baseline. Independently reading its complete eight-rank D sets gives **1180 to 1086, zero newly bad D labels**. faces/owner/neighbour/boundary match byte-for-byte on every rank, so this is a valid label-set comparison. The original AIR receipts retain full checkMesh and the other quality sets; this audit does not replace them. The final native mesh still fails three strict checks.

## Reproduction

Run from the integration repository; place all outputs, temporary files and pytest basetemp inside it:

    env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 taskset -c 24-31 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python tools/audit_air_cone_nonorth_kernel.py --input docs/benchmarks/shinaide-product-2026-10-05/evidence/target-liquid-steady/cone-quality-kernel/final-ratio-parents-input.json --rank 3 --parent 9836 --output .codex-tmp/nonorth-recompute.json

For either historical native comparison, use --baseline-mesh and --new-mesh with the exact absolute frozen paths recorded in receipt.json. The tool always compares all eight original D sets and topology hashes, even when no newly bad cell exists. Original live cases must first be restored from their preserved AIR evidence if worktree cleanup has removed them.

    env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 taskset -c 24-31 /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python -m pytest -q src/backend/tests/test_cone_nonorth_kernel.py --basetemp .codex-tmp/nonorth-pytest

The final run is **13 passed in 2.80s**. Tests include full-report JSON serialization after an initial numpy-bool CLI refusal, preserved unchanged in audit-initial-json-refusal.*. Actual-cell coordinates in native-baseline-regression.json permit independent determinant recomputation without trusting saved profile values. The complete sets and SHA bindings support the zero-new-label comparison after raw mesh restoration.

The global point-gap check is reproducible from global-frozen-point-gap.json: for each recorded source scan load the original float64 array and reshape (-1,3); for written scans use the repository polymesh reader; omit exactly excludedWrittenPoint when present; compute min(norm(points-apex,axis=1)) for each of its three recorded candidates. Check every original array SHA before comparing. The result is the minimum over all sixteen recorded scans.

receipt.json binds inputs, code and scientific scope. sha256.json binds every preserved artifact except itself.
