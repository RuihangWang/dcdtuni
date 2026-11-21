# PRIVATE reproduction

Never execute against normal cases or write into FROZEN-v1. First copy the payload to a fresh directory inside dcdtuni and set R to that copy. Symlink only read-only existing IT node_modules to R/node_modules and R/source/src/frontend/react/node_modules; dependencies are not vendored by the packet. The exact pinned source and original input arrays are in this package. Create R/tmp and R/cache, retaining all output within R. The original independent-design scripts are audit copies with original declared dependencies; only the real compiler/numerical/control runners below are self-contained against the copied source/data.

Set PYTHONDONTWRITEBYTECODE=1, CUDA_VISIBLE_DEVICES empty, OMP_NUM_THREADS=2, OPENBLAS_NUM_THREADS=2, MKL_NUM_THREADS=2, TMPDIR/TMP/TEMP=R/tmp, XDG_CACHE_HOME=R/cache. Execute one worker at a time with taskset -c 32-33 and /mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python.

1. python R/run_react.py: real public load/save/reopen→strict compile four cases; private pinned full React tsc. Correct original receipts are results/commands.json and react-compiler-receipt.json. Vite/vitest config directs cache inside R.
2. python R/run_backend.py: actual full public constructor, all four original coarse grids, independent reference and BE error comparison, executed-prefix admission, checkpoint v3 all-eight-array bit-exact replay. Expected original elapsed244.69s; no threshold or solver modifications.
3. python R/run_controls.py: intended four real rejection gates, fixed Pa/W source parameter dependency, one real t0→1s all56solidMean heat AD and three centered FD step sizes plus independent matrix sensitivity. Other three controls AD-only and valve upper-bound limitation are explicit.

Preserve every original script/log/receipt when rerunning. Setup missing-private-vendor/JSON attempts and the first negative-harness typed-digest mistake are retained and classified; none is a product RED. Numeric results are in coarse-product-final.json and control-ad-and-negative-gates.json. Actual browser acceptance is pending Root and must follow GUI_RUNBOOK.md rather than any scripted bypass.
