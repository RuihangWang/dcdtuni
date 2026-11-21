# 文档归档索引

本目录保存已经完成使命、但仍有追溯价值的文档：带日期的交接、决策快照、审计与回执、已完成的计划，以及被新做法取代的方案稿。归档只移动位置，不改写正文结论；移动后仓库里指向它们的 Markdown 链接已同步改到新路径。当前状态请看 [PROJECT_STATE.md](../developer/PROJECT_STATE.md)，工程入口请看 [.claude/CLAUDE.md](../../.claude/CLAUDE.md)。

`docs/benchmarks/` 是回执库，代码和测试直接引用其中的路径，因此原地保留，不归入本目录。`docs/research/`、`docs/manuscript/`、`docs/manuscripts/` 与 `case/agent/` 属于研究证据，也不在这里。

部分归档目录里的 JSON 清单（例如 `liquid-platform-2026-09-28/manifest.json`）和 `docs/benchmarks/` 下的整树快照记录的是归档前的原路径，那是当时的事实，保持原样。

## 2026-10-21 整理（D1）

| 归档目录 | 原路径 | 归档原因 |
|---|---|---|
| [handoffs-2026-09-10/](handoffs-2026-09-10/) | `docs/developer/handoff-2026-09-21.md` | 带日期的交接；其中 T1/T2/T4/T5/T7 已由后续提交闭合，T3（机柜框架与服务器融合）与 T8 仍开放，现状见 PROJECT_STATE |
| | `docs/developer/liquid-platform-handoff-2026-09-27.md` | 液冷平台交接快照；其中"Three.js→OV→Three.js 未通过"已被 10-05 及 10-21 的实测取代 |
| | `docs/developer/HANDOFF_ACCOUNT_2026-10-06.md` | 带日期的交接 |
| | `docs/developer/HANDOFF_ENGINEERING_2026-10-07.md` | 带日期的工程交接 |
| | `docs/developer/HANDOFF_EMERGENCY_2026-10-15.md` | 10-15 应急合并交接；其中列出的严格网格、风冷收敛、工作树收口已由 10-16~10-21 的提交闭合 |
| | `docs/developer/PROJECT_STATE.md`（2026-10-21 重写前的全文）→ `PROJECT_STATE-history-2026-09-28_to_2026-10-05.md` | 2026-09-28 至 10-05 的逐日状态记录；原文件已重写为 10-21 的简明现状 |
| | 根目录 `AGENTS.md`「dcdtuni 项目工作约定」2026-09-28 版 → `AGENTS-worknotes-2026-09-28.md` | 09-28 的机器、端口、服务、集成基线与当日任务状态快照；长期有效的约定留在 AGENTS.md |
| [liquid-2026-09-10/](liquid-2026-09-10/) | `docs/developer/liquid-goal-evidence-2026-09-26.md` | 液冷目标证据快照 |
| | `docs/developer/gpu-acceptance-proposal-2026-09-26.md` | 一次性 GPU 验收提案，验收已结束；文中 Kit 路径是迁移前的旧路径 |
| | `docs/developer/liquid_demo_mms_decisions_2026-09-28.md` | 液冷 demo 与 MMS 决策快照 |
| | `docs/developer/liquid-solver-focus.md` | 2026-09-22 的优先级说明；其中"下一步数值工作"已被 Newton DAE 路线完成或取代 |
| | `docs/developer/shinaide-dae-workbench.md` | 2026-09-26 的工作台记录；所述工程已移到 `case/verification/shinaide-legacy-projects/`，当前工程是 `case/Shinaide_Liquid/Shinaide_liquid.cfdproj` |
| | `docs/developer/shinaide_liquid_full_graph_verification_2026-10-05.md` | 带日期的全图验证回执 |
| [omniverse-2026-09-10/](omniverse-2026-09-10/) | `docs/developer/ov-retest-plan-2026-09-27.md` | 已执行的复测计划 |
| | `docs/developer/ov-renderer-convergence-audit-2026-09-27.md` | 渲染契约审计快照 |
| | `docs/developer/ov_display_decisions_2026-09-29.md` | OV 显示战役决策快照（D2：主 React 为唯一 UI） |
| | `docs/developer/ov_slice_streamline_design_2026-09-29.md` | Stage 3 设计，已实施并过门 |
| | `docs/developer/ov_liquid_result_design_2026-09-30.md` | Stage 4 设计，已实施并过门 |
| | `docs/developer/ov_single_gpu_service.md` | 旧的 `kit_acceptance_harness.py --service` 起服方式；现由 `src/start_server.sh --app react --ov --gpu N` 取代，harness 仅作手工诊断 |
| | `docs/developer/shinaide-hybrid-rendering.md` | 2026-09-26/27 开发记录；其中 OV 超时的描述已被 09-29/09-30 回执取代 |
| | `docs/developer/render-result-layers-v1-2026-09-27.md` | 隔离分支阶段的契约评估；结果层模块已进入 main（`2e16fd9ed`） |
| | `docs/developer/ov-result-usd-adapter-2026-09-27.md` | 隔离分支阶段的 USD 结果层适配评估 |
| | `docs/developer/ov-edit-event-cpu-contract-2026-09-27.md` | 隔离分支阶段的 OV 编辑事件契约；`ovEditBridge.ts` 已在 main，视口编辑白名单仍为空 |
| | `docs/developer/postprocess-request-revision-2026-09-27.md` | 后处理修订号闸门的隔离分支说明；闸门已合入 main（`c3c3716b1`） |
| [omniverse-paraview-2026-03/](omniverse-paraview-2026-03/) | `docs/Omniverse-paraview/Omniverse-ParaView-Integration-Summary.md` | 2026-03 的 ParaView Connector 方案稿，未采用；现行做法是 React + `dc.cfd_bridge` 直连 Kit（见 [render-scene-contract.md](../developer/render-scene-contract.md)）。文中指向 `Ref_code/ParaView*` 的链接当时就不存在 |
| [dccart-source-validation-2026-09-27/](dccart-source-validation-2026-09-27/) | `docs/developer/dccart-pairing-contract-counterexamples-2026-09-27.md`、`dccart-stage-support-trace-2026-09-27.md`、`dccart-source-identities-2026-09-27.md`、`dccart-source-region-review-red-2026-09-27.md`、`dccart-source-support-red-2026-09-27.md` | 源面配对与支持域的隔离审计（RED 反例、阶段追踪、源身份交接）。仍开放的几何准入设计见 [adr-geometry-topology-v1-2026-09-27.md](../developer/adr-geometry-topology-v1-2026-09-27.md) |
| | `docs/developer/source-component-validation-v1-2026-09-27.md`、`source-component-validation-review-red-2026-09-27.md`、`source-component-exact-predicates-2026-09-27.md` | 只读源构件校验的隔离候选与复核；实现不在 main，三条 RED 反例是下一步几何准入工作的输入 |
| [air-2026-09-10/](air-2026-09-10/) | `docs/developer/tri-angle-mesh-fixes.md` 及 `docs/developer/assets/tri-angle-mesh-*` | 2026-09-11 的 Tri_angle 网格修复记录 |
| | `docs/developer/dccart-partition-workflow.md` | 2026-09-10 的分区网格流程报告 |
| | `docs/developer/postprocess-rendering-fixes.md` | 2026-09-11 的原生后处理修复记录 |
| | `docs/developer/e8_config_modes_convergence_2026-10-01.md` | E8（单一 simulationConfig、分离风/液模式、瞬态内收敛）设计与计划，已实施 |
| [transient-keyframes-2026-09-30/](transient-keyframes-2026-09-30/) | `docs/developer/transient_keyframes_plan_2026-09-30.md`、`transient_keyframes_requirements_2026-09-30.md` | 已完成的瞬态关键帧计划与需求。设计稿 `docs/developer/transient_keyframes_design_2026-09-30.md` 被代码注释引用，原地保留 |
| [react-migration-2026-08/](react-migration-2026-08/) | `docs/react-migration-gaps.md`、`docs/react-migration-gaps-round4.md` | 两轮 React 迁移缺口登记，已全部收口；归档前已把第四轮收口表更新到 2026-10-21 的真实状态 |
| [schema-refactor-2026-06/](schema-refactor-2026-06/) | `docs/reference/2026-06-14-schema-refactor-final-report.md` | cfdproj V4 / cfdrun V2 重构的结题报告 |
| [legacy-plans-2026-05/](legacy-plans-2026-05/) | `case/Base2-9/PLAN.md` | room.json + config.toml + GeometryManager 路线的旧计划，该路线已退役；算例目录只保留工程与数据 |
| [liquid-platform-2026-09-28/](liquid-platform-2026-09-28/) | `docs/developer/archives/liquid-platform-2026-09-28/` | 旧液冷工作树的清理回执；与本目录合并为同一个归档根 |
| [meshgenerator-2026-09/](meshgenerator-2026-09/) | `.claude/CLAUDE.md` 中 `Ref_code/MeshGenerator/` 条目的长段落 | 2026-09-08 至 09-10 MG 战役的指令与进度原文；CLAUDE.md 只保留仍然有效的指令要点与当前状态 |

以下带日期的开发文档仍被代码或测试注释引用，原地保留：`docs/developer/dccart-memory-profile-2026-09-21.md`、`docs/developer/mesh-castellation-performance-findings.md`、`docs/developer/transient_keyframes_design_2026-09-30.md`。

## 2026-10-21 整理（D3：docs 之外的子目录说明）

| 归档目录 | 原路径 | 归档原因 |
|---|---|---|
| [frontend-history/](frontend-history/) | `src/frontend/prompt.md` → `stlgen-dev-prompts.md` | 旧版 HTML 编辑器开发期的原始提示词（中文），只有历史追溯价值，不是现行说明；`.claude/CLAUDE.md` 参考文档表已改指新路径 |

`docs/drag-and-sim/` 是带日期的 SCANO 研究记录，本应归档，但 `cfd_demo/SCANO/GTC_A3_Numerical/scripts/roi_acceptance.py` 等脚本注释引用其中的文件，因此原地保留，只在 README 顶部补了 2026-10-21 的状态核对，并改正了启动方式。`src/frontend/Instruction.md`（旧版编辑器中文教程）同样原地保留，顶部加了历史说明。

## 2026-10-21 删除（无证据价值，不归档）

这些文件直接删除；需要时可从删除前的 Git 历史取回（例如 `git show 8db56b8d6:<路径>`）。

| 原路径 | 删除原因 |
|---|---|
| `AGENTS/BUGList.md`、`AGENTS/FeatureList(SpecList).md`、`AGENTS/HandOff.md` | 2026-04-24 建立以来只有一个换行符的空壳，名字容易与 `AGENTS.md`、PROJECT_STATE 混淆 |
| `docs/user-guide/config-toml.md`、`docs/reference/config-toml-schema.md` | 描述已退役的 `config.toml` 格式，当前没有任何代码读取它；两页早已排除在文档站之外 |

## 2026-10-21 原地改名与重写（D2，不归档）

| 原路径 | 现路径 | 说明 |
|---|---|---|
| `docs/reference/mesh-modes.md` | [docs/reference/mesh-engines.md](../reference/mesh-engines.md) | 2026-09-09 退役的三选一 `meshMode` 设计稿改写为现行网格引擎页（dcCart 默认、snappy、meshgen）；旧设计摘要保留在新页的"History"一节，全文可用 `git show 8db56b8d6:docs/reference/mesh-modes.md` 取回。仓库内链接已改到新路径 |
| `docs/user-guide/cfdproj-format.md` 中 v3 结构的正文 | 同一文件（改写为 v5 简明指南） | 与 [legacy/cfdproj-schema.md](../reference/legacy/cfdproj-schema.md) 重复且已被 v4/v5 取代；完整旧文可用 `git show 8db56b8d6:docs/user-guide/cfdproj-format.md` 取回 |

整理后的核验（2026-10-21）：对全部被跟踪的 Markdown 做了相对链接检查，没有因本次移动、改名或删除产生的断链；仍存在的断链都在整理前就已存在（例如 BHUMANS 笔记里的本地截图、原样保存的交接副本里的相对链接），检查脚本在本机被 Git 忽略的 `_tmp/campaign_D/linkcheck.py`（不随仓库分发）。`mkdocs build --strict` 无警告。

## 更早的归档

| 目录 | 内容 |
|---|---|
| [2026-10-08/](2026-10-08/README.md) | 放弃的期刊稿 paper2 与 paper3_icml，带 SHA-256 清单 |
| [2026-10-12/branch-cleanup/](2026-10-12/branch-cleanup/README.md) | 远端分支清理前保存的 bundle 与补丁 |
| [engineering-emergency-2026-10-15/](engineering-emergency-2026-10-15/README.md) | 10-15 应急合并时保全的 WIP 源码、补丁与失败运行 |
| [Json_description.md](Json_description.md) | 旧 `room.json` 格式说明（已退役的管线） |
