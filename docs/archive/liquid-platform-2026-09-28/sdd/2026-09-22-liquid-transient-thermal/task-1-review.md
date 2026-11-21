# Task 1 独立代码审查 — conservative dynamic thermal kernel

日期：2026-09-22（SGT）
审查范围：`b63b7f63df772d9cdcf6b93a8e28f61cdd9e672b..91adb3b15636d23d42ab35d1394fd3412a860575`。
工作树：`NTU-Litianyu-40901:/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform`。

结论：**Approve with minor follow-up；未发现 Important/Critical 问题。** 本结论仅针对本次热瞬态内核、hybrid 接入和结果提取的范围，不代表完整液冷平台、控制器、现场标定或 PEK02 验证完成。

## 审查方法与证据边界

读取 task-1-brief.md、task-1-report.md、task-1-review.diff，逐行检查提交后的 thermal_step.py、hybrid.py、runner.py 相关代码及新测试；交叉读取 model、BC enforcer、hydraulic convergence contract 和 HX handler，以确认调用约定。开始时 HEAD 为 91adb3b1；control.py 和 test_liquid_control.py 的未提交更改属于 root，不在本次范围。

提交者报告 326 项全套测试通过，但该进程在最后的存储状态 overflow guard 加入前已加载内核；guard 后 20 项新热测试和 3 项旧 hybrid 测试通过。这里没有将前者误报为最终字节版本的完整重测，也没有重跑广泛测试。0.1729 s、10x10 节点矩阵、约 5.99e-9 W 能量缺口是提交者的单次性能证据，不是本审查的新测量。

本审查只运行一次小型定点复现，核查实时监测的具体疑点；环境为现有 dcdtuni Python，OMP/MKL 线程各 1，PYTHONPATH 明确指向本工作树 src，禁用 pyc 写入。没有服务启动、代码/索引/分支变更、第三方仓库读取或额外 agent。

## Important / Critical

无可证实的问题。

## 数值实现核查

- **闭环同时混合正确。** thermal_step.py:93–138 对每条边构造关于真实上游温度的仿射出口，139–160 以实际有向入流质量权重组装节点线性系统。闭环通过 torch.linalg.solve 联立闭合，不依赖边序、Kahn 遍历或固定混合迭代次数。固定温度和零入流节点分别使用规定值及前一帧显示温度。
- **管段瞬态正确。** thermal_step.py:102–110 的逐单元消元等价于 `(C/dt + |m| cp) Tnew = C Told/dt + |m| cp Tup + Qcell`；反向流改变单元遍历次序，状态仍回填到原单元索引。distributed cell source 和 cell_T_init 在 hybrid.py:120–129 保留。零流量时管段只按照自身源项与储热变化。
- **冷板模型在声明假设内正确。** thermal_step.py:119–127 使用稳定的 expm1 计算 effectiveness，`G=|m| cp eps`，同时消去有限固体热容方程。零流量时 G=0、eps=0，固体积累 Q dt/C，出口的显示温度不产生能量传递。V_fluid 没有独立储热状态；模块和报告均明确限制，未错误宣称已求解该状态。
- **HX 已进入闭环方程。** thermal_step.py:128–130 的出口为 `(1-eps) Tin + eps Tfacility`，设施侧为规定温度热库。177 行以真实上游温度与边出口计算带符号拒热，既允许冷却也允许设施侧更热时加热。这里不是完整一次侧动态模型。
- **质量与能量诊断含义一致。** thermal_step.py:174–190 分别计算管段/固体储热、HX 拒热、外边界净输入及热源；在内部质量守恒时，`source + boundary - storage - rejection` 正确抵消。压力 BC 和固定温度节点计入边界换热。调用者提供的内部不平衡流量没有被假装修复，mass_residual 会暴露它；生产路径先要求 GGA 的逐帧质量/动量收敛。固定温度热库交换与单元储热没有重复计算。
- **纯内核的梯度路径保留。** 物理运算经 torch tensor、index_add 和 linalg.solve 传播，无 no_grad、NumPy 或 differentiable physical input 的 item/detach。方向和零流分支是声明过的非光滑事件；在固定非零流方向内 bool 分支不切断选中表达式的梯度。新测试对流量、CP UA、多步热源、HX UA 分别用中心差分作数值对照，而非只检查 requires_grad。
- **失败不会伪装成完整收敛轨迹。** hybrid.py:46–50 拒绝任意非收敛水力帧；thermal_step.py:51–86、157–173 校验 dt、前态、参数、线性求解及重建状态。奇异系统和非有限状态会抛错。hybrid_converged 只在全部热步结束后置 True；runner.py:281–282 优先读显式状态。最终 total 改为线性温度残差，旧 ODIL/BDF 残差只作兼容诊断，报告对此有解释。
- **结果与兼容性。** hybrid.py:128–143 消费每帧 m[:,t]，保留 tensor 历史，再将值复制到旧 display parameters；t=0 是初值。runner 的最终温度、CP 水温与进出口压力按实际流向提取；无管段或无冷板可用空储热数组。旧 _build_T_all_nodes 仍是 ODIL 路径，不能用其结果代替新闭环 rollout；此边界已在报告说明。现有回调参数签名保留，但其 CP 监测值仍有下述小问题。

## Minor：实时 CP monitor 没有使用当前热步

位置：`src/backend/liquidcooling/solver/hybrid.py:138–143`；`src/backend/liquidcooling/runner.py:27–43`。

`thermal_node_history` 在整个循环完成后才赋值，而每个 timestep callback 在赋值之前执行。`_extract_cp_monitor` 因此在这些回调中落回旧的 `_build_T_all_nodes`；它也始终使用 T_s/m 的 `-1` 列，而不是当前已完成的时间索引。这会导致进度监测展示尚未求解的末帧，且当末帧流量不同或反向时会进一步采用错误的监测流向。最终 callback 和 `_extract_results` 在历史赋值后读数正确。

定点复现：使用新测试的 loop_graph(n=4)，将所有流量设为 0.3 kg/s，在 `_solve_thermal_direct` 的 timestep_callback 中比较 callback 的 max_chip_K 与 `_extract_cp_monitor(..., ['cold_plate_1'])`：

- step 1：当前固体温度 305.26917 K，monitor 为 340.00 K，thermal_node_history 尚不存在。
- step 2：当前固体温度 298.75107 K，monitor 为 340.00 K，thermal_node_history 尚不存在。
- step 3：当前固体温度 297.25152 K，monitor 为 297.25 K；历史仍在 callback 返回后才建立。
- 完成后 monitor 为 297.25 K。

建议后续在发布每步 callback 前暴露当前已求解的节点温度和时间索引，并使 monitor 按该索引读取 T_s/m；保持既有 callback 公共参数兼容。增加一个具有变化流量的中间步骤 monitor 断言即可。该行为的末列读取/旧重建 fallback 是既有监测限制，不是此次数值内核引入的物理回归，因此不阻止本数值包合入。

## 审查限制

未独立重跑完整测试或性能基准；未测试 CUDA 生产服务、UI、控制优化、现场数据或整个平台端到端流程。报告中的 approve 仅表示本范围未发现可证实的 Important/Critical 代码问题，不能推导更广泛能力已经验证。


## 可复现脚本

在本工作树中设置 `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src"`，用 `/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python` 运行：

```python
import torch
from backend.tests.test_liquid.test_dynamic_thermal import loop_graph
from backend.liquidcooling.solver.model import LiquidODILModel
from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
from backend.liquidcooling.runner import _extract_cp_monitor

g = loop_graph(n=4)
model = LiquidODILModel(g)
with torch.no_grad():
    model.state['m'].fill_(.3)

def cb(t, n, max_chip):
    monitor = _extract_cp_monitor(model, g, ['cold_plate_1'])['cold_plate_1']
    print(t, max_chip, monitor, hasattr(model, 'thermal_node_history'))

_solve_thermal_direct(model, g, timestep_callback=cb)
print(_extract_cp_monitor(model, g, ['cold_plate_1']))
```

结束核查时 root 已提交其范围外 PID 更改，HEAD 为 3b2f3137；本次审查的四个源码/测试文件相对 91adb3b1 无差异，工作树干净。此审查未操作 Git 状态。

## Monitor 修复复审 — 3b2f3137..ada9ac9d

复审日期：2026-09-22（SGT）。检查 task-1-fix-review.diff、task-1-report.md 的 follow-up 段与新增回归测试；HEAD 为 ada9ac9d0408123665e856add7b6a6e343f9cf3a，工作树干净。范围仅限上一轮 Minor 的当前帧监测修复，不扩展到新的数值/平台审查。

结论：**Approve；此前 Minor 已解决，未发现本次修复的残留 Important/Critical 或需要另行记录的 Minor。**

- `_solve_thermal_direct` 先复制当前求解 T_w/T_s，再在 callback 前发布包含 t=0..t 的 `thermal_node_history`，设置 `thermal_time_index=t`。因此 monitor 已有当前节点温度，无需落回旧重建。
- CP 的固体温度、流量、流向选择、上游节点温度全部使用同一 t；NTU 出口基于该帧计算。新增测试的流量序列 `.3, .2, -.1, .4` 能检测旧末帧读取及中间帧反向选择错误。
- CDU 的供/回压力、名义供液出边流量及供液温度同样按 t 读取。温度优先来自已求解节点历史；没有历史时依次保留 prescribed series/static BC 兼容路径。现有首个 pressure BC 的设备识别启发式没有被冒称为多 CDU 资产映射，此点仍明确限定在后续 runtime 工作范围。
- 活动索引由 `finally` 清除；正常完成后完整历史已建立，非回调调用仍读取末帧。旧 FakeGraph/无索引调用兼容；动态 BC 无历史情况下现在正确读取最后一个 series 样本。
- 热步事件同时携带 `time_index=t` 和 `time_s=t*dt`；最终事件使用 `N_t-1` 与 `(N_t-1)*dt`。原 SolveLog 字段、losses 中 thermal_step 及低层三参数 timestep callback 签名保留。水力迭代事件未虚构热物理时间。

提交者的 3 个 RED 回归与最终 36 项相关 GREEN 证据，与实际修复范围及新增断言一致。本复审未重跑测试，更未重跑完整套件；内核未改动，代码检查未产生额外需要定点复现的疑点。审查只追加本报告，没有源码、索引、分支或服务操作。
