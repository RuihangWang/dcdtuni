# G1 门回执 — NV mock 1604 态 compiled 路由重算(2026-09-28)

S1(残差闭包 compile 化)的黄金门。脚本 `benchmark_nv_compiled.py`(诊断脚本,非产品代码),
在 `feat/liquid-dae-gpu-compile` 上以 compile 路由规则 v3(`solver/compile_support.py`)重算
NV PEK02 mock v3 夹具的稳态 + 一步 BE + 伴随热梯度,CPU-only、float64、2 线程(taskset 0-3)。

## 结果(nv_compiled_cpu2.json,EXIT=0)
- **heat_gradient = 0.19631667626512342,与黄金值逐位一致**(abs_diff = 0.0;黄金值来源
  `../liquid-dae-matrix-free-2026-09-27/nv_dense_cpu{1,4}.json`,dense 默认路径)。
- steady_residual 2.278829924762249e-12 / step_residual 1.2631062418222428e-12 — 与 dense 基线一致。
- primary_return_K ≈ 301.28008;energy_balance_W ≈ -1.69e-08(历史 1e-8 量级保持)。
- 路由计数:**7 compiled / 4 eager-grad(jacobian 前向)/ 0 transform / 0 fallback**,broken_latch=false
  — 无静默回退;compiled 面 = Newton merit/norm 值调用(no_grad 化后)。
- 计时:steady 32.99 s(**含 1604 态图首次 inductor 编译,单列不摊入稳态口径**)、step 0.90 s、
  adjoint 0.90 s。对照 dense 基线 0.82/0.76 s(CPU1)——值调用只占求解一小部分,**CPU 上
  不宣称加速,只记录**(计划纪律);加速论证在 S3/S4 GPU episode。
- input_sha256 = 460f7e24…(与 matrix-free 回执同锚);source_sha256 三文件在 JSON 内。

## 判读边界
这是 mock 夹具的数值一致性验证,不是现场 PEK02 校准;GPU 与 torch.compile 的组合验收在 S4。
