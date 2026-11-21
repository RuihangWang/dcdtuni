# PRIVATE 可迁移复现

scripts 只读证据；写出目录必须在仓库 PRIVATE 内、证据目录之外。按当前 AGENTS 与资源授权执行；原验收采用 CPU32–33、最多2数值线程、CUDA空。需要现有 NumPy/SciPy/PyTorch/Pydantic 与 Node；不安装依赖、不起服务、不调用浏览器/API、不操作 Git。

~~~sh
B=/mnt/lv/home/litianyu/mycode/dcdtuni
P="$B/docs/benchmarks/liquid-quadratic-mms-real-gui-20261006"
R="$B/.codex-tmp/quadratic-mms-repro-20261006"
PY=/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python
export PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
mkdir -p "$R/tmp" "$R/cache"
export TMPDIR="$R/tmp" TMP="$R/tmp" TEMP="$R/tmp" XDG_CACHE_HOME="$R/cache"
taskset -c 32-33 "$PY" "$P/scripts/reproduce_sources.py" --out "$R/sources"
taskset -c 32-33 /usr/bin/node "$P/scripts/check_project_hash.cjs"
taskset -c 32-33 "$PY" "$P/scripts/verify_package.py" --out "$R/check" --replay-tag all
taskset -c 32-33 "$PY" "$P/scripts/reproduce_fixed_source_ad.py" --out "$R/ad"
~~~

仍在 PRIVATE装配目录时把 P指向 payload；scripts 按自身位置寻证据，不依赖原临时运行目录。default verify核四组全部状态/原EOC/energy/hash及已有replay；--replay-tag all才做四组新的公开 v3重放。forcing再生从声明b/v/a/m/pressure/物理系数及原literal公共方程，得到原四个精确 typed source SHA，不从产品residual生成forcing。AD保持原窗口、目标、FD步长与OR门；其他controls没有新FD验收。

真实 public React compiler 纯函数复现：

~~~sh
N=/path/to/existing/frontend/react/node_modules
taskset -c 32-33 "$PY" "$P/scripts/prepare_compiler_repro.py" --out "$R/compiler" --node-modules "$N"
cd "$R/compiler"
taskset -c 32-33 /usr/bin/node "$R/compiler/node_modules/vitest/vitest.mjs" run --config "$R/compiler/vitest.config.ts"
~~~

R/compiler 必须新建为空；脚本只在此复制 pinned源代码/真实GUI保存工程，并将已安装node_modules接为PRIVATE symlink。不会改正常源/依赖、装包或启动网页。原4test正是public reader/schema/revive、saved experiment compiler、serialize/save/reopen路径；编译输出落R/compiler/results。source/package.json保留，node_modules不复制；环境版本见 provenance/runtime-dependencies.json。

旧 evidence中的绝对路径脚本是原件，不是可迁移入口。NPZ是声明数学输入，不能称实测数据；旧高cycle/正弦判断不得逆改。source-copy-receipt可核源SHA、目标SHA与source mtime held。先验包manifest再使用；不在 frozen/evidence目录Prepare或落重放结果。

先做完整性只读校验：

```bash
"$PY" "$P/scripts/verify_manifest.py"
```

此校验覆盖包内逐文件 SHA、大小及精确 inventory；不写出文件。manifest.json 与 verification.json 为交付索引，不参与自己的循环 SHA。
