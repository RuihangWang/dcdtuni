# Task 1 水力包独立审查

日期：2026-09-22（SGT）。审查范围：`fa5e83ce..6964e3fe`；分支 `codex/liquid-control-platform`；工作树 `/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform`。审查时 HEAD 为 `6964e3fe3a04e2bb29f5615999c78186b2b3aeb3`，工作树干净。

## 结论

- **Spec verdict：PASS。** 逐时间片准稳态水力、阀门物理压降、显式关闭与孤立压力诊断、原 API 和标量诊断均符合本任务约定。
- **Code-quality verdict：APPROVE WITH MINOR NOTE。** 未发现应阻塞本水力包的 Critical / Important 问题。存在一个已复现的零开度参数梯度问题，见下文；本包不承诺控制轨迹反传，既有被检查的 DDL 路径也不使用该阀门开度梯度，因此不将其升级为本包阻塞项。
- 这是 **Task 1 水力包门禁**，不是完整平台、动态热传播、闭环控制、端到端可微、现场参数校准或 PEK02 验证通过。

## 审查证据与方法

依次阅读 task-1-brief.md、task-1-report.md、task-1-review.diff，并核对当前 HEAD 与三个变更文件。没有读取、访问或复用第三方 DiffLiquidTwin；没有修改源代码、index 或分支。

针对明确风险补充查看：`CompiledGraph` 的控制张量和 BC 形状（缓存是否漏掉时变输入）；原 hydraulic / vectorised / valve 测试（API 和黄金值回归）；`ddl/src/softlc.py` 的实际导入、forward、残差与 adjoint 调用（接口和求解语义兼容性）；`ddl/src/netgen.py` 的 N_t / 控制 / 组件生成（现有 DDL 是否使用动态阀门）；handler base（梯度疑点的现有契约边界）。只运行了下述零开度梯度最小复现，没有重跑大套件。

实际查看日志尾部：`task-1-green-final.log` 为 **69 passed in 5.54s**；`task-1-full-verified.log` 为 **278 passed in 165.14s**。这是实现者留存、审查者核对的证据，不声称审查者独立重跑这些测试。现有测试与 golden 文件未在此提交修改；vectorised golden 测试仍断言原压力、流量和迭代次数。

## 数值与契约核对

1. **ISA 单位只换算一次。** 由 Q[m³/h] = Kv sqrt(ΔP[bar]/(rho/1000))、m = rho Q/3600 得 R = 1e5(rho/1000)(3600/(rho Kv))²。实现 `valve_headloss` 的 ΔP = R m sqrt(m²+eps²) 与此一致；导数 R(sqrt(m²+eps²)+m²/sqrt(m²+eps²)) 为所用平滑公式的准确导数。反向流 ΔP 为奇函数，导数为偶函数。新增独立 1 bar ISA oracle、正反向测试和三种特性覆盖合理。
2. **阀门特性一致。** GGA 与 handler 共享 linear、rangeability=50 的 equal-percentage、quick-opening Kv 计算。正开度不再被无依据地钳成 0.001。零开度理想关闭；equal-percentage 在零点不连续已明确记录，不假装是连续泄漏模型。
3. **关闭约束物理明确。** 关闭边的 flow 强制零、Schur conductance 为零、压力损失方程不作为该边的约束；这允许关闭阀承受任意压差。开边连通分量缺少压力参考时显式 `singular_pressure_system`，不会靠虚拟泄漏制造压力参考。死端压力恢复保留零流时的重力/泵静压；闭阀切断唯一压力参考先被拓扑检查拒绝。
4. **每时间片与缓存。** `_at_time` 支持 `[component]`、`[component,1]`、`[component,N_t]`。泵和阀门都读当前时间索引。只有相邻控制完全相同且上一片收敛时才复用；失败后不复用失败结果，以最近一次收敛流量 warm start。当前 `CompiledGraph.bc_P` 和其余水力参数是静态字段，因此此处没有漏掉已定义的 BC 时间维。未来引入时变 BC 或流体物性必须扩充缓存键。
5. **收敛与诊断。** aggregate `converged` 取每片 AND；标量 iterations/max_dm 保留并有明确聚合语义。per_step 含时间索引、重用状态、标量迭代数、质量/动量残差和失败原因。小更新还要通过有限性与独立质量/动量容差；不存在仅凭小更新或 inf<=inf 接受的可见路径。失败片返回猜测并显式标失败，报告没有将其说成有效解。
6. **兼容性。** `gga_solve` 签名不变，P/m 输出形状不变，旧 `_compute_all_headlosses(m, graph)` 默认为 time_index=0。原 callback 两个标量参数保留，各片重启编号，文档已说明。自有 `ddl/src/softlc.py` 的旧调用能够沿用；其生成器目前 `N_t=1`、泵 rpm=1.0、无 valve，并且自身有不允许死端的约束。未发现本 diff 对这条已有稳态 DDL 路径的接口破坏；不外推为其对新增动态/闭阀场景的 adjoint 已适配或已测试。
7. **范围控制。** 三个文件改动没有进入 thermal、frontend、server、fixtures 或第三方代码。保留旧泵曲线、undamped GGA 与 Jacobian clamp 是明示边界；这里没有为它们作额外物理验证背书。

## Critical / Important findings

无。

## Minor：零开度的未选中 sqrt 分支污染 opening 梯度

位置：`src/backend/liquidcooling/handlers/valve.py:18-21`，尤其第 20 行 `alpha.sqrt()`。

`torch.where` 会计算所有分支。在 opening=0 时，quick-opening 分支的 sqrt 导数为无穷；即使实际选择 linear/equal-percentage，或最终外层 where 选择理想关闭的常数零，backward 仍会遇到 0×inf 并产生 NaN。forward 的关闭值和本次 GGA 数值结果不受影响。

最小复现通过 `ValveBatchHandler.compute_residuals`，而非仅测试孤立表达式：一个阀门，Kv_max=2.5，rho=998，压力从 200000 到 100000 Pa，opening=`[[0., .5]]` 且 requires_grad=True，m 为零，对 momentum residual 的 sum 求 opening 梯度。以工作树 src 和 dcdtuni Python、OMP/MKL 1 线程运行，输出：

```text
characteristic 0 residual [[0.0, -0.3468748262323695]] opening_gradient [[nan, -0.693749652464739]]
characteristic 1 residual [[0.0, -0.0981110167407255]] opening_gradient [[nan, -0.3838125545756641]]
characteristic 2 residual [[0.0, -0.4905550837036276]] opening_gradient [[nan, -0.4905550837036276]]
```

建议：对 inactive/shutoff 分支先提供有限的 sqrt 输入，再应用选择/关闭掩码，保持所有正开度公式和精确零泄漏不变；为三种特性补一个独立 helper/handler backward 测试。这里不要求建立完整轨迹可微契约，只建议避免共享 helper 本身引入 NaN。由于本包明确推迟控制反传、当前 GGA 不计算 opening 梯度、检查到的既有 DDL 不用阀门，该项记为非阻塞；若后续包要训练/优化 opening，必须先修复。

## 验收限制

本次是有界静态审查加一个疑点复现。没有独立重跑 69/278 套件，没有 GPU 或服务端到端验证，没有 DDL 广泛实验复测。报告中的 CPU 测试和回归证据支持本包 gate；下一包仍需独立验证热推进、控制与优化路径。

## Scoped re-review：fcd1a665（零开度梯度修复）

审查日期：2026-09-22（SGT）。范围严格为 `6964e3fe..fcd1a665` 的 `task-1-fix-review.diff`，并核对追加的实现报告与 RED/GREEN 日志；未重审其他水力逻辑，未运行测试套件，未读取/触碰并行中的参数包修改。

**结论：PASS / APPROVE。此前唯一 minor 已解决；本修复无新增 Critical、Important 或 Minor finding。**

- `effective_valve_kv` 先将 `alpha==0` 的 sqrt 输入替换为常数 1，再以既有 shutoff mask 输出精确零。所有实际参与 backward 的 sqrt 输入在关闭状态下都有限，原来的 `0*inf -> NaN` 路径已消除；关闭点/负开度采用零梯度的数值约定已明确记录。
- `alpha>0` 时 sqrt 输入与此前完全相同；linear、equal-percentage、quick-opening 的正开度值和导数均保持原式。该修复没有恢复泄漏下限，也没有改动水力装配、时间步调度或返回 API。使用 `alpha==0` 而非一般正值判定，不会把 NaN 开度输入静默替换为有效值。
- 新增六项测试覆盖共享 helper 和真实 handler、三种特性、负开度/零开度/1e-12/0.5/1.0；用独立解析式检查值和导数，包含严格的零关闭与有限性断言。
- 核对日志：`task-1-review-fix-red.log` 为 **6 failed, 19 deselected in 3.68s**；`task-1-review-fix-green.log` 为 **67 passed in 4.20s**。本次审查独立执行的 `git diff --check 6964e3fe..fcd1a665` 通过。未把此前 278-pass 结果冒认为修复后全量重测。

最终水力包门禁通过。这里确认的是共享阀门公式的局部 backward 修复，仍不代表控制轨迹或完整平台的可微与闭环能力已经验证。
