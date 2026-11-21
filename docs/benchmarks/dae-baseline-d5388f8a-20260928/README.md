# Stage 0 基线与 compile 可行性探针回执 — dae-baseline-d5388f8a-20260928

基线:`main@d5388f8a`(feat/liquid-dae-gpu-compile 基点;512f33de 仅改文档)。
环境:conda env `dcdtuni`,py3.10.20,torch 2.5.1+cu121,dynamo+inductor 就绪,CPU 探针(taskset 0-3,OMP/MKL=2,float64)。
计划:OneDrive `Research/DCTwin/LIQUID_DAE_UPGRADE_PLAN_2026-09-28.md`(Stage 0 条目 ①②③ + 假设 A5)。

## 基线测试(2026-09-28)
- 全量 `pytest src/backend/tests -q`:**1751 passed / 6 failed / 63 skipped / 3 subtests,282.98s**。
- 液冷定向 `src/backend/tests/test_liquid`:**573 passed / 4 skipped,220.47s**。
- 6 项失败全部为既有类别(2026-09-27 全量口径已记录,非液冷路径,本升级不顺手修):
  test_mesh_engine::test_meshgen_pipeline_command_shape_and_skips(meshgen 命令)、
  test_room_enclosure_guard::test_dcCart_pipeline_checks_the_enclosure_after_createBaffles(room-enclosure 假对象)、
  test_template_params ×4(模板数值:TestTimeVaryingBC/TestServerBCModes)。

## 探针结论(5 轮,fixture = test_equation_graph.adapter() CP+HX 闭环小图,exact_state 物理态)
| 轮 | 内容 | 结果 |
|---|---|---|
| R1 | `torch._dynamo.explain(residual)` | **1 graph / 0 breaks / 212 ops**(A5 成立) |
| R2-E | value 路径真编译 | **bit-exact(value+grad diff=0)**,首次编译 48s(fw+bw 全链),warm **12.4×**(67μs vs 830μs) |
| R2-F | compile 外层 + `torch.func.jvp` 内层 | 真编译(6.1s、144× warm)但 **tangent 数值错(diff=32.7)** ← S2 matvec 编译化调查项(疑与 residual 内 in-place `index_copy` 的 dual 传播交互) |
| R2-G | jvp 外层 + compile 内层(round-1 形态) | **dynamo 完全回退 eager**(frames 15 调用 12 skip,0.043s bit-exact)→ round-1 的 B/C/D“全绿”判定作废 |
| R3 | solve_steady(compiled residual) | **硬崩溃**:`jacobian(vectorize=True)` 的 run_backward 执行 compiled AOT backward 节点,inductor C++ kernel 报 `Cannot access data pointer of Tensor that doesn't have storage` |
| R4 | 仅按 functorch level 路由(`maybe_current_level`) | **仍崩**:`_newton` :206 在 grad 模式建图的 compiled 前向,其 AOT backward 节点进入 jacobian/嵌套 backward 执行上下文 |
| R5c | **路由规则 v3**(见下) | steady/BE **bit-identical**;probe-4 崩溃场景(theta requires_grad 反传)**通过,参数梯度 diff=0.0**;solve 内 8 次建图调用全部 eager |
| R5d | no_grad value 奇偶(修正 5c part3 传参 bug:误把 scaled z 当物理 y,差值 149798≈压力 scale,非数值问题) | **diff=0.0,warm 12.3×**(64μs vs 794μs),inductor 缓存命中后首调 0.0s |

## S1 架构决定(由探针证据塑形)
**RoutedResidual 规则 v3**(将实现为 `solver/compile_support.py`):
1. functorch transform 激活(`torch._C._functorch.maybe_current_level() is not None`)→ eager;
2. grad 模式开启 **且** 任一输入 `requires_grad`(将建图)→ eager —— AOT compiled backward 图**绝不允许存在**于 vmap/jacobian/嵌套 backward 可能执行的上下文(R3/R4 崩溃类);
3. 其余(no_grad 或无 grad 输入:推断 rollout、诊断、后处理、Newton 值调用)→ compiled。
**产品改动面**:`dae_newton.py` `_newton` :206 初始残差与 :235 回溯候选值调用显式 `no_grad`(它们只用于 norm/merit/detach,从不反传)→ 规则 3 接管,获得 ~12× warm 加速面;梯度路径(jacobian/jvp/伴随)保持 eager+functorch(bit-identical)。路由计数(n_compiled/n_eager_transform/n_eager_grad)必须暴露,**禁静默回退**。
**S2 登记项**:F 轮 tangent 错误根因(matvec 编译化前置条件);dense `jacobian(vectorize=True)` 在 vmap 下永远 eager → S2 手写批量化 Jacobian-free 路径才可能吃到编译。

## 文件
- probe_compile.py(R1)/ probe_compile2.py(R2)/ probe_compile3.py(R3,崩溃无 json)/ probe_compile4.py(R4,崩溃无 json)/ probe_compile5.py(R5a,崩溃无 json)/ probe_compile6.py(R5c)/ probe_compile7.py(R5d)
- results.json / results_round2.json / results_round5c.json / results_round5d.json
- source_sha256.txt(基线源文件哈希)
