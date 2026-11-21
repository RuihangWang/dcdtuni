# Reproduce PRIVATE full-system MMS
Read REPORT.md and DERIVATION.md before running. Normal application and UI acceptance are pending.

FROZEN-v1 contains exact source snapshots, a complete ten-file patch, owned inputs, literal scripts/tests, all raw results, and lossless compressed large raw payloads. archive-map.json records original path/byte SHA and gzip path. decode_archive.py restores these only into a newly created directory under the integration repository .codex-tmp; it refuses an existing destination. It never touches normal source.

For a new owned sibling workspace, restore sources/results first and create its node_modules symlink to the pinned integration frontend dependencies. Before re-execution, preserve restored terminal results elsewhere in that new workspace; do not overwrite this frozen packet. The source programs use their own __file__ parent, and old fixture references assume a sibling .codex-tmp directory under IT. Actual source input/path identities are in the manifests.

Run with taskset -c32-33, CUDA_VISIBLE_DEVICES empty, OMP_NUM_THREADS=2, MKL_NUM_THREADS=2, OPENBLAS_NUM_THREADS=2, Python interop1 and graph threads≤2, and all TMP/TMPDIR/TEMP/XDG_CACHE_HOME/PYTHONPYCACHEPREFIX inside that new workspace.
- compile.test.ts + vitest config create baseline manifests with literal React compiler.
- generate_manufactured.py builds independent sources and four .cfdproj grids. It never calls residual/Newton to create sources.
- manufactured.test.ts compiles strict v4 and negative tests.
- backend_tests.py after contract-only audits concrete admission/initial/pointwise gates.
- backend_tests.py before default / after default prove default parity.
- backend_tests.attempt01.py is the exact executed first four-grid positive numerical driver, but its negative-seal conclusions are withdrawn. Prefer corrected backend_tests.py for a new run.
- generate_fine.py/fine.test.ts/fine_newton.py exercise two finer grids.
- sensitivity.py tests actual adjoint/FD with explicit independent source dependence.
- private TypeScript config invokes --noEmit only.
Exact commands, environments, return codes, raw stdout/stderr and numerical arrays are retained. Use a controlled wrapper to capture any new run separately. No normal service/API/browser/native mesh/wmake/Git action is part of reproduction.

- audit_mixing_convergence.py recomputes retained mixing/sign proof and all EOC metrics without numerical reruns.
