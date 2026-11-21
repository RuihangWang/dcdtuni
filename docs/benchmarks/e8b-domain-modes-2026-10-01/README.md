# E8b 回执（2026-10-01）——风冷、液冷各自稳态/瞬态，顶栏一处切换，界面重做

**结论：过门。** 用户裁决：风冷求解页不再设置稳态/瞬态，改到顶栏；液冷也放顶栏；顶栏切换「太丑、难用」要重做。

- **数据（cfdproj v5，增量）**：`transient.airMode` / `transient.liquidMode`；`transient.mode` 改为派生（任一为瞬态即瞬态），
  时间轴、t > 0 冻结、回放照旧读它。E8b 之前的 v5 文件按各自实际跑法推导：风冷看 `solverType`；液冷在时间轴为瞬态
  且 DAE 不是稳态时为瞬态——**打开旧文件不改变任何运行行为**（例：`LiquidSingle_Control_dae_v5` 时间轴稳态、DAE 自行推进，
  推导为液冷稳态，照旧按实验自身时长跑）。JS 与 Python 双实现，逐 case 对拍。
- **唯一写入口** `setSimulationMode(mode, domain=当前视图)`：风冷镜像到 `simulationConfig.solverType`，液冷镜像到 DAE
  `analysis.mode`（切稳态同时设稳态初始化、取消负载阶跃）。自动化 `setSolverType`、`setSolverConfig('solverType')` 走同一入口。
- **导出各看各的**：风冷进度表只在风冷瞬态时写；液冷按时间轴采样只在液冷瞬态时做。
- **顶栏**：原下拉框换成分段切换「风冷/液冷 │ 稳态 │ 瞬态」，跟随左侧风冷/液冷视图，一次点击切换；稳态青色、瞬态品红（与时间轴同色），
  另一领域方式不同时显示小标签（如「液冷·瞬态」），混合工况在两个视图都看得见。
- **风冷求解页**：只读行指向顶栏（不再需要「混合工况」提示）。**液冷面板**：分析模式改为只读；DAE 仍按自身时长推进而液冷为稳态时
  （旧文件、刚采用的 mock）明确说明。**轨道面板**：当前领域为稳态时提示其关键帧不参与求解。

环境：Mac 开发，40901 worktree `~/scratch/wt-e8` @ `feat/e8b-domain-modes` 临时 8390。

## 验证

| 项 | 结果 |
|---|---|
| vitest（`37f3a2c1`） | 320 文件 / 3188 通过，1 跳过；tsc 无错 |
| 新 `modeSwitch.test.tsx`（8） | 风冷视图写 airMode 与 solverType、不碰液冷；液冷视图写 liquidMode 与 DAE、不碰 solverType；撤销回到全部镜像；存盘重开保留；旧 v5 文件不变；v4 混合迁移为风冷稳态+液冷瞬态；两个导出各看各的领域 |
| JS/Python 对拍（uv + pytest） | 46 通过：全部 case 文件的迁移/投影一致；7 种旧 v5 块的推导一致；mode 与两领域矛盾时两边同样报错 |
| 金样 `LiquidSingle_Control_dae_v5.cfdproj` | 重录，只多两行 `airMode`/`liquidMode`（均为 steady） |

## 浏览器门（Shinaide_dae_cpu，界面点击）

1. 打开（v4 混合）：`airMode steady / liquidMode transient / mode transient`；顶栏「风冷 │ 稳态 │ 瞬态 │ 液冷·瞬态」，时间轴显示。
2. 风冷视图点「瞬态」：三者皆瞬态，`solverType transient`，标签消失；导出 cfdrun v4 带瞬态块。
3. 切到液冷视图点「稳态」：`liquidMode steady`，DAE `analysis {mode: steady, initialization: steady}`，`solverType` 仍为 transient，
   顶栏「液冷 │ 稳态 │ 瞬态 │ 风冷·瞬态」；液冷求解页只读行「稳态 · 单一工况 · 在顶栏切换（液冷视图）」，原下拉框已不存在。
4. 风冷改回稳态：两领域皆稳态，时间轴隐藏，导出不带瞬态块。日志逐条记录 `setSimulationMode air transient / liquid steady`。

## 留给 E9

液冷面板整体视觉重做（用户「分析模式这些界面设计太丑了」），以及「DAE 自行推进、不跟随时间轴」这一旧状态是否收敛为单一液冷模式。
