# PRIVATE reproduction

NTU-litianyu-40901 only, CPU32–33, max2 numerical threads, CUDA empty. All outputs/tmp/cache inside this directory. These five commands were actually completed with rc0; no source/build/service/browser/GPU/Git operation is required.

```sh
R=/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/full-mms-quadratic-design-private-20261006
PY=/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python
export PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
export TMPDIR="$R/tmp" XDG_CACHE_HOME="$R/cache"
taskset -c 32-33 "$PY" "$R/quadratic_design.py"
taskset -c 32-33 "$PY" "$R/propose_fixture.py"
taskset -c 32-33 "$PY" "$R/quadratic_design_lowflow.py"
taskset -c 32-33 "$PY" "$R/propose_lowflow_fixture.py"
taskset -c 32-33 "$PY" "$R/structural_preservation.py"
```

Lowflow LP emits a SciPy OptimizeWarning that threads1 is passed verbatim to HiGHS; CPU affinity/max2 remain enforced. Both variants and results are preserved. Source generation is the explicit independent physical equations, never product F. Backend admission validates new typed namespaces only; no solver or browser acceptance is implied. Proposed .cfdproj files require subsequent real compiler identity proof.

Copy FROZEN-v1 to a new writable PRIVATE directory within dcdtuni before reproducing; do not overwrite frozen payloads. The helpers import exact original backend solely to obtain admitted graph/coefficient data.
