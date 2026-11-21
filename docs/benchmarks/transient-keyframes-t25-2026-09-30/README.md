# 瞬态关键帧 T2.5 回执(2026-09-30)——液冷关键帧进入 v2/v3 编译,风冷关键帧先拒绝

**结论:过门。** 这一阶段修的是 T1 引入的一个回归:v4→v5 迁移把液冷实验的负载阶跃 / 供水计划转成了
`cp.heat` / `cdu.primaryInletT` 关键帧并清掉原设置,但液冷编译只读原设置——仓库内三个带负载阶跃的算例
(NV_PEK02_Mock、Shinaide_dae_cpu、Shinaide_hybrid_cpu)编译后阶跃**静默消失**。机读回执 `receipt.json`。

环境:40901,独立 worktree `~/scratch/wt-t25` 起 8390(`start_server.sh --build`);8360 全程未触碰。

## 改了什么

| 提交 | 内容 |
|---|---|
| `9bbdfe16` | 液冷图编译取冷板数值时,以编辑器/登记表/时间轴写的 `liquidCooling.cpCuPairs` 为准;`constructions.coldPlates` 只提供身份与顺序(见下「顺带发现」) |
| `b7d8b7c2` | `transient/runSchedules.ts`:瞬态 v5 工程的时间推进型液冷运行,按 `t_i = i·dt` 右连续采样全部轨道,每段相同采样只编译一次图,输出 v2/v3 事件(`Q_W`、`opening`)与 DAE 一次侧 `inlet_K`/`flow_kg_s` 序列;`N_t` 取时间轴时长。无处安放的时变输入(`cp.dT`、`pump.rpm`、`pump.flow`、`cdu.secondarySupplyT`、`cdu.returnPressure`,v2 下的一次侧)、离开 dt 网格的关键帧、旧负载阶跃/供水计划与关键帧叠加、其他图变化,一律**点名拒绝**。迁移(JS+Python)对无 DAE 的 v2 实验也在转换了阶跃/计划时置 `transient`。`buildManifestV2` 对「风冷瞬态 + t>0 关键帧」拒绝(T4 才编译进 OpenFOAM 表) |

风冷**不是**回归:runner 的边界登记表只由 `boundaryBindings` 生成(旧 `_build_bridge_bc()` 已删),旧 UI 的
`model.timeVarying` 从未进入其中(设计 §4 已记);但 T2b 的界面鼓励打关键帧,所以现在显式拒绝,不再静默按起点值跑。

## 测试

| 套件 | 结果 |
|---|---|
| vitest(Mac 与 40901 worktree) | 307 文件 / 3118 测试全过(新增 `liquidSchedules` 11 项、图编译 1 项) |
| `tsc -b` | 干净 |
| node 迁移测试 | 23/23 |
| pytest 迁移奇偶(40901) | 38/38 |
| pytest 液冷/混合/DAE/交接子集(40901) | 668 过 / 5 跳过 / 2 失败——两项均为 `test_mms_product_case`,原因是未入库的 `case/MMS_Product/Liquid/model/MMS_product.liquidrun` 不在 worktree 里;同测试在主 checkout 3/3 通过(环境问题,非回归) |

## 浏览器门(NV_PEK02_mock,DAE,dt 1 s,时长 10 s)

1. 经 `/api/read-file` + `__dcdtuni.loadProject` 打开 v4 文件 → v5、`transient`、34 条 `cp.heat` 轨道均为 `{0: 10000 W, 5 s: 12000 W 阶跃}`,顶栏时间条 5 s 处有菱形。
2. 液冷求解 →「准备实验」:页面实际 POST 给 `/api/liquid/runs` 的清单为 `.liquidrun` v3 DAE,`N_t=11`,34 块冷板各一条 `Q_W` 事件,全部在 5 s,倍率恰为 1.2。
3. 「开始」跑完 10 s / 11 帧:1 号冷板芯片温度 0–4 s 恒为 39.2985 °C,5 s 起上升(39.3375 → 39.5173 °C @10 s);初始升温率约 0.039 K/s,与 ΔQ/C_solid = 2000/50000 = 0.04 K/s 相符。全体冷板平均 43.0022 → 43.2272 °C。

## 顺带发现(已修 1 项,记录 3 项)

- **已修**:液冷图编译自 `f4c3f6aa`(09-22)起用 `constructions.coldPlates` 整体替换 `cpCuPairs`,而该副本只在 v3→v4 迁移时写过一次——在检查器里改冷板热负载从未进入液冷编译;v5 文件把 `Q_W` 放进轨道后,编译读的是冻结副本。所有 v4 算例两处数值一致,现有结果不变。
- **记录**:两处的**成员**仍可能不一致(在已迁移服务器上用编辑器增删冷板对,编译仍按 `coldPlates` 的成员);`liquidPostprocess.ts` 也读 `coldPlates`。留作后续单独修。
- **记录(设计待裁决)**:一个全局 `transient.mode` 无法表达「风冷稳态 + 液冷瞬态」的混合工程。现行规则:液冷按自身是否时间推进(v2 恒是;DAE 看 `analysis.mode`)且工程为 `transient` 才吃关键帧;风冷按 `solverType`。迁移来的 NV_PEK02/Shinaide_dae 为 `transient` + `solverType=steady`。
- **记录**:液冷实验面板仍提供负载阶跃/供水计划;与关键帧叠加时编译点名拒绝,T2c/T3 从面板移除。
