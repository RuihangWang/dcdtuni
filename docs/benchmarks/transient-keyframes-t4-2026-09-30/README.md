# 瞬态关键帧 T4 回执(2026-09-30)——风冷 cfdrun v3、OpenFOAM 表与稳态起点链

**结论:过门。** 风冷的关键帧第一次真正进到 OpenFOAM:瞬态工程(求解器为瞬态)导出 **cfdrun v3**
(= v2 + `transient {durationS, start}` + 各边界 `parameters.schedules`),runner 把它写成 OpenFOAM v2412 能读的时间表 /
表达式边界,起点可以是均匀初场,也可以是同一运行目录里先跑到收敛的稳态场。机读回执 `receipt.json`。

环境:40901,独立 worktree `~/scratch/wt-t4` 起 8390(`--build`),8360 未动;另开 `~/scratch/wt-base` 做 main 对照与最终全量测试。

## 做了什么

| 部分 | 做法 |
|---|---|
| 导出(T4b) | 与液冷采样器同一思路:在所有移动风冷轨道的每个拐点时刻(阶跃另加「刚好之前」一次)物化工程、编译清单、逐边界比对参数;变化的可调度参数写成拐点行(阶跃 = 同一 t 两行),拐点间线性。可调度:送风 `temperatureC`/`flowRateM3s`/`directionWorld`(+`supplyAngleDeg`)、风扇 `powerW`(+ 导出的出口温度估计,runner 不用)、热流面 `heatFluxWm2`;其余参数变化、或移动了却什么都没改变的轨道(热流服务器的整机功率)按名拒绝 |
| 模式 | JS `validateManifest` 与 Python `CfdrunV2` 都接受 2 与 3;v3 必须带 `transient` 块,v2 不许带调度 |
| runner 边界(T4c) | 调度 → 现有内部 timeVarying(airflow / supplyTemp / 壁挂送风矢量 / powerRows / heatOutput = q·A);阶跃写成 `(t−ε, 旧)(t, 新)`,ε = max(1 µs, |t|·1e-8);带表的运行 `writePrecision double`;壁挂送风 U 为矢量表;风扇出口 ΔT(t) 为分段线性 `time()` 表达式 |
| 起点(T4d) | `uniform`:室温取时间条的空气温度;`steadyConverged`:同一运行目录先跑 SIMPLE(表全部去掉取 t = 0 值),按处理器把内场写进瞬态 `0/`,稳态时间目录与 postProcessing 移到 `steadyStart/`,重新生成瞬态字典再跑 PIMPLE,记录 `steady_start.json` |

## 验证

| 项 | 结果 |
|---|---|
| 后端专项(`test_cfdrun_v3_schedules.py` 15、`test_transient_start.py` 3、`test_mpi_log_rotation.py` 2) | 全过。ε 行、线性表达式对独立求值器、模式接受/拒绝、读取器映射、场文件文本、稳态段剥表、写精度、均匀起点、内场播种 |
| 后端全量(40901,最终提交 `330c315d`) | 1844 通过 / 67 跳过 / 4 失败;4 项失败在干净的 main 上同样失败(2 项 dcCart、2 项 MMS 夹具不在 git);比 `wt-t4` 多的 1 项跳过是 `test_floor_room_boundary` 缺 `case/Slab/model/Slab.cfdrun`(环境;在 `wt-t4` 里通过) |
| vitest | 314 文件 / 3153 测试通过,1 文件按设计跳过(另一会话的冷板清单转储工具,需环境变量) |
| tsc | 干净 |
| 后端直跑(Slab,8 核,4 s,手工 + 浏览器清单) | 均匀起点与稳态起点两条链都实跑通过,数值见 `receipt.json` 的 `backend_runs` |

## 浏览器门(Slab,8 核,4 s)

全部经界面:顶栏切到瞬态;时间条右端时长 4、左端「起点:均匀初场」空气 24 °C;选中 PCU_1,轨道面板在 1 s 给送风温度加关键帧并在编辑框改为 12,
在 3 s 给风量加关键帧、值 3、斜坡 1 s;选中 R2,在 2 s 给服务器功率加关键帧、改为 7200 W。保存工程 → 刷新页面 → 重新载入(v5 往返无损)。
「检测网格状态」→「执行求解」。

- 页面导出的清单:schemaVersion 3,`transient {durationS 4, start uniform 24 °C}`,3 个边界带调度:
  PCU_1 送风 `temperatureC [[0,15],[1,15],[1,12],…]`、`flowRateM3s …[2,4],[3,3]…`;R2 风扇进/出口 `powerW …[2,3600],[2,7200]…`。
- OpenFOAM 实际拿到:送风 T `uniformFixedValue table ((0 288.15) (0.999999 288.15) (1 285.15) …)`;送风 U `flowRateInletVelocity table`;
  风扇出口 T 为阻尼表达式,内含 ε 形式的 ΔT 分段。PIMPLE、Euler、endTime 4、初场 T 均匀 297.15 K。12 分钟跑完。
- **写出时刻的 patch 平均值逐项对上**:

| t (s) | 0 | 0.5 | 1 | 1.5 | 2 | 2.5 | 3 | 3.5 | 4 |
|---|---|---|---|---|---|---|---|---|---|
| 送风流量 (m³/s) | — | 4.000 | 4.000 | 4.000 | 4.000 | 3.500 | 3.000 | 3.000 | 3.000 |
| 送风温度 (K) | 288.15 | 288.15 | 285.15 | 285.15 | 285.15 | 285.15 | 285.15 | 285.15 | 285.15 |
| R2 风扇温升 (K) | — | 6.712 | 6.715 | 6.723 | 11.754 | 13.431 | 13.428 | 13.427 | 13.429 |

  温升目标 3600 W → 6.712 K、7200 W → 13.423 K;2 s 那一帧是已记录的阻尼滞后(设计 §6 T4 第 4 条),之后稳定在目标上。
  R2 进风从初场 24 °C 随 12 °C 送风混入逐步下降(297.15 → 292.83 K)。

### 同一工程,起点改为「稳态收敛场」(界面下拉切换,清单其余不变)

- 运行日志:先 `Steady-converged start: SIMPLE at the t = 0 boundaries first`,SIMPLE(表全部去掉)在第 245 步由残差控制停下(上限 300);
  `steady_start.json` 记录 8 个处理器各自从 245 步写入 T、U、alphat、epsilon、k、nut、p、p_rgh;稳态时间目录与 postProcessing 在 `steadyStart/`;
  瞬态 `0/T` 内场为非均匀列表,边界时间表原样保留;随后 PIMPLE 跑满 4 s。整条链 13 分钟。
- 写出时刻对照:送风流量与送风温度与均匀起点那次逐时刻相同;风扇温升 6.712 / 6.712 / 6.712 / 11.748(阻尼滞后)/ 13.427 / 13.428 / 13.428 / 13.428 K。
- **起点确实是平衡态**:R2 进风在 0.5、1、1.5 s 为 294.479 / 294.480 / 294.456 K,几乎不动,直到送风降温的冷空气到达后才开始下降
  (均匀起点那次同一位置从 297.15 K 一路漂移)。

## 发现并修复

- **阶跃前采样落在求值器容差内**(`c07512b6`):「刚好之前」取在 `t − 1e-9·max(1,t)`,而求值器把 `t ≥ tk − 1e-9` 当成已到关键帧,
  所以 t ≤ 1 s 的阶跃前一行拿到了阶跃后的值(1 s 的送风温度阶跃成了 `[1,12],[1,12]`)。改为取在 t − 1 µs,补单测。单测原先只用 120 s、300 s 的阶跃,没覆盖到。
- **两处被 T4 正当改变的旧测试**(`be9c949f`):`schemaVersion 3` 不再是「须重新导出」(改测 4);`writeExplicitBC` 顺序的源码检查只看求解路径
  `_run_solve_from_mesh`(稳态起点段也调 `reconstructPar`,但只读内场,不需要显式边界值)。
- **轮转日志名错位**(`330c315d`,T4 之前就有):上一步的 mpirun 日志被标成下一步的名字,稳态段日志成了 `…01_buoyantBoussinesqPimpleF`。
  现在按日志自己的 OpenFOAM `Exec :` 行命名,缺省用本 runner 上一条命令。

## 取舍与记录

- **门禁跑的是哪版后端**:8390 进程在 `6ef00266` 启动后没有重启,之后只重建了前端;中间的提交都是前端或测试文件,所以两次门禁求解对
  T4 的运行时代码有效——**但早于 `330c315d`**:门禁运行目录里稳态段日志仍叫 `log.mpi_solver.01_buoyantBoussinesqPimpleF`,
  日志改名只由 2 项单测与最终全量测试证明。
- **只有文本单测、没进过 OpenFOAM 的路径**:壁挂送风的 `directionWorld`/`supplyAngleDeg` 矢量表,热流面 `heatFluxWm2` → `heatOutput`。
  Slab 是落地式空调 + 风扇服务器,两者都没跑到;计划的 T4e 门禁也不要求,记在这里。
- 观察到、未深究:稳态段存档 `steadyStart/processor*/` 里除 245 外还有一个 `1` 时间目录(日志第 1 步后有 `writeBCFields` 写出;9-14 的普通稳态运行没有)。
  播种取的是最新的 245(瞬态初场非均匀、R2 进风起始平稳可证),无影响。
- 回风不需要调度:runner 里回风是压力出口,流量由质量守恒决定(实测两台空调回风平分总送风),设计 §1 相应句已更正(§6 T4 第 6 条)。
- `convergence.json` 仍是稳态规则,瞬态运行一律报 NOT_CONVERGED(p_rgh 每个时间步的初始残差本来就高、传感器本来就在变);瞬态判定归 T5。
- 「simulationConfig 只保留一份」待用户裁决;无论哪种答案都是后续单独阶段,不影响 T4 合入。v3 的时长继续镜像到 `endTimeSeconds` 并由现有一致性检查守住。
- 求解页「正在运行 checkMesh 质量检测…」在求解开始后会重新出现(T4 之前就有,`SolverPanel` 状态行只在最近一次作业是已完成的 checkMesh 时隐藏),已另开任务。
- 与另一会话的 fan-outlet 测试修复(main `0edea95c`,只改测试文件)无冲突,已并入本分支,那 4 项在 T4 代码上通过。
