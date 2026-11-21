# docs/benchmarks：回执库

这里存放带日期的验收回执：每个目录或文件记录一次实际运行或检查的原始结果（摘要、日志摘录、哈希、截图、复现脚本），供状态文档、论文和代码注释追溯数字的来源。它是证据库，不是现行说明：怎么用、现在是什么状态，请看 [docs/index.md](../index.md) 与 [工程现状](../developer/PROJECT_STATE.md)。

约定：

- **原地保留，不搬不删。** 代码、测试和文档里有不少地方按路径引用这里的文件；回执里写死的旧路径、旧数字是当时的事实记录，不随代码更新。
- **新回执**用 `<主题>-<YYYY-MM-DD>/` 或 `<主题>-<YYYYMMDD>/` 命名，目录里放 `README.md`（结论、证据、下一步）与机器可读的摘要；原始大文件被 `.gitignore` 排除时，提交逐字段摘录并绑定 SHA-256（示例：[air-release-cases-2026-10-21](air-release-cases-2026-10-21/README.md)）。
- 回执记录的是当时的判据和结果。判据后来改过的（例如 2026-10-20 起稳态收敛只看 ACU 回风温度），以回执注明的日期为准阅读。
- 下面的分组按主题排列，组内按名称排序；条目后面是回执 README 的第一行或摘要字段，原文语言照录。截至 2026-10-21。

## 风冷发布与产品流程

十个发布算例的严格网格与回风收敛回执、产品预设、网格计时与取消流程，以及 Shinaide 产品化整合。当前风冷主线结论的依据是 `air-release-cases-2026-10-21`，其余是到达那一步之前的阶段记录。

- [`air-product-presets-2026-10-05/`](air-product-presets-2026-10-05/README.md) — Air product presets and mesh budget (2026-10-05)
- [`air-release-cases-2026-10-21/`](air-release-cases-2026-10-21/README.md) — 十个风冷发布算例：严格网格与回风收敛回执摘要（2026-10-21）
- [`all-case-base025-20261006/`](all-case-base025-20261006/README.md) — Case base-size consistency (2026-10-06)
- [`mesh-budget-and-shinaide-seven-bases-20261008/`](mesh-budget-and-shinaide-seven-bases-20261008/README.md) — Shared mesh deadline and current Shinaide failure checkpoint
- [`mesh-cancel-flow-and-current-air-20261008/`](mesh-cancel-flow-and-current-air-20261008/README.md) — Normal cancellation and flow fixes; current air evidence
- [`shinaide-product-2026-10-05/`](shinaide-product-2026-10-05/README.md) — Shinaide product integration — 2026-10-05

## dcCart 网格与严格网格攻坚

dcCartMesh 切割单元网格的阶段回执：Base604 与四算例提速、源面身份与支撑、薄片与凹角修复、Shinaide 严格 checkMesh 攻坚过程中的失败与修复记录（2026-09-27 至 10-07）。

- [`air-mesh-quality-receipt-2026-09-27.md`](air-mesh-quality-receipt-2026-09-27.md) — Strict air-mesh quality receipt — isolated CPU evidence (2026-09-27)
- [`dccart-app-early-gate-20261007/`](dccart-app-early-gate-20261007/README.md) — dcCart APP early opt-in admission — 2026-10-07
- [`dccart-axis-point-repair-native-20261007/`](dccart-axis-point-repair-native-20261007/README.md) — Actual private axis and bounded point repair
- [`dccart-base604-2026-09-30/`](dccart-base604-2026-09-30/README.md) — dcCart × Base604 回执(2026-09-30)——"网格生成卡住"定位与修复
- [`dccart-dw-first-veto-actual-20261007/`](dccart-dw-first-veto-actual-20261007/README.md) — Actual native first-veto trace — strict failure retained
- [`dccart-dw-wider-native-20261007/`](dccart-dw-wider-native-20261007/README.md) — Actual wider-DW investigation, 2026-10-07
- [`dccart-face-tet-collinear-2026-09-27/`](dccart-face-tet-collinear-2026-09-27/README.md) — dcCart face-tet 共线节点 RED
- [`dccart-four-cases-2026-10-01/`](dccart-four-cases-2026-10-01/README.md) — dcCart 四个工程回执（2026-10-01）——0.25 m / 8 核全部过 checkMesh，Base604 网格提速约 5 倍
- [`dccart-genuine-wall-app-20261007/`](dccart-genuine-wall-app-20261007/README.md) — Genuine wall producer and actual normal APP — 2026-10-07
- [`dccart-nonlinear-feasibility-tail-actual-20261007/`](dccart-nonlinear-feasibility-tail-actual-20261007/README.md) — Actual PRIVATE nonlinear feasibility-tail oncepair — strict failure retained
- [`dccart-physical-f3-negative-20261007/`](dccart-physical-f3-negative-20261007/README.md) — dcCart PRIVATE F3 negative diagnosis and next bounded source build
- [`dccart-residual-rerank-native-actual-20261007/`](dccart-residual-rerank-native-actual-20261007/README.md) — dcCart residual reranking: actual tiny strict acceptance
- [`dccart-source-fan-construction-private-20261007/`](dccart-source-fan-construction-private-20261007/README.md) — PRIVATE source fan construction: real bug fixed locally, strict mesh still fails
- [`floor-source-leaves-actual-staging-20261007/`](floor-source-leaves-actual-staging-20261007/README.md) — Physical floor source leaves and actual carrier staging
- [`liquid-transport-closure-20261007/`](liquid-transport-closure-20261007/README.md) — Liquid transport literal carrier closure — 2026-10-07
- [`liquid-transport-solid-ray-preparation-20261007/`](liquid-transport-solid-ray-preparation-20261007/README.md) — Underground solid export and exact ray preparation — 2026-10-07
- [`native-point-owned-content-normal-20261007/`](native-point-owned-content-normal-20261007/README.md) — Native physical-point input ownership — normal source checkpoint
- [`shinaide-354-full-polygon-support-2026-09-27.json`](shinaide-354-full-polygon-support-2026-09-27.json)
- [`shinaide-354-full-polygon-support-2026-09-27.md`](shinaide-354-full-polygon-support-2026-09-27.md) — Shinaide 354-UID source support at the con_f / Raised_Floor seam
- [`shinaide-354-full-polygon-support-2026-09-27.py`](shinaide-354-full-polygon-support-2026-09-27.py) — Read-only projected polygon/triangle support for Shinaide con_f/floor ROI.
- [`shinaide-blanking-source-binding-2026-09-27.json`](shinaide-blanking-source-binding-2026-09-27.json) — CPU-only React export, 40901 backend verification, mesh dictionary generation and native identity read; no ful…
- [`shinaide-dccart-residual-2026-09-27/`](shinaide-dccart-residual-2026-09-27/)
- [`shinaide-dccart-source-tangent-guard-2026-09-27.json`](shinaide-dccart-source-tangent-guard-2026-09-27.json)
- [`shinaide-dccart-source-tangent-guard-2026-09-27.md`](shinaide-dccart-source-tangent-guard-2026-09-27.md) — dcCart 原源面轮廓门禁：隔离候选与仍失败的真实网格
- [`shinaide-dccart-stage-support-2026-09-27.json`](shinaide-dccart-stage-support-2026-09-27.json)
- [`shinaide-dccart-uid-sheet-mode-2026-09-27.json`](shinaide-dccart-uid-sheet-mode-2026-09-27.json)
- [`shinaide-dccart-uid-sheet-mode-2026-09-27.md`](shinaide-dccart-uid-sheet-mode-2026-09-27.md) — dcCart source-UID baffle mode — isolated CPU trial, 2026-09-27 SGT
- [`shinaide-mesh-defect-localization-2026-09-27.json`](shinaide-mesh-defect-localization-2026-09-27.json)
- [`shinaide-mesh-quality-propagation-2026-09-27.json`](shinaide-mesh-quality-propagation-2026-09-27.json) — Interpret the archived Shinaide final checkMesh output and propagate its verdict through a mesh job; no new CF…
- [`shinaide-mesh-source-support-red-2026-09-27.json`](shinaide-mesh-source-support-red-2026-09-27.json)
- [`shinaide-p19-dependency-binding-normal-browser-2026-10-06/`](shinaide-p19-dependency-binding-normal-browser-2026-10-06/README.md) — P19 dependency binding: normal React run, strict mesh failure
- [`shinaide-p20-complete-prefix-normal-2026-10-06/`](shinaide-p20-complete-prefix-normal-2026-10-06/README.md) — Shinaide P20 actual complete-prefix observation, 2026-10-06
- [`shinaide-partitioned-source-receipt-2026-09-27.json`](shinaide-partitioned-source-receipt-2026-09-27.json)
- [`shinaide-partitioned-source-receipt-2026-09-27.md`](shinaide-partitioned-source-receipt-2026-09-27.md) — Partitioned OpenFOAM mesh identity for source-surface receipts
- [`shinaide-server-patch-mesh-area-2026-09-27.json`](shinaide-server-patch-mesh-area-2026-09-27.json) — Existing Shinaide air smoke mesh only; mesh quality failed three checks; no new CFD solve.
- [`shinaide-server-patch-mesh-area-2026-09-27.md`](shinaide-server-patch-mesh-area-2026-09-27.md) — Shinaide 服务器热边界实际网格面积（2026-09-27）
- [`shinaide-source-component-exact-2026-09-27.json`](shinaide-source-component-exact-2026-09-27.json)
- [`shinaide-source-component-validation-2026-09-27.json`](shinaide-source-component-validation-2026-09-27.json)
- [`shinaide-source-surfaces-2026-09-27.json`](shinaide-source-surfaces-2026-09-27.json) — React export and backend strict/pre-mesh validation only; dcCart does not consume this map yet
- [`shinaide-source-uid-coverage-2026-09-27.json`](shinaide-source-uid-coverage-2026-09-27.json)
- [`shinaide-source-uid-coverage-2026-09-27.md`](shinaide-source-uid-coverage-2026-09-27.md) — Shinaide dcCart source UID coverage (CPU-only, no mesh solve)
- [`shinaide-strict-native-p15-2026-10-06/`](shinaide-strict-native-p15-2026-10-06/README.md) — Actual Shinaide P15 strict failure
- [`shinaide-strict-native-p16-2026-10-06/`](shinaide-strict-native-p16-2026-10-06/README.md) — P16: normal React run, bounded legacy journal still refused admission
- [`signed-crossing-native-refusal-20261007/`](signed-crossing-native-refusal-20261007/README.md) — Guarded signed-crossing: real native refusal retained
- [`source-body-fixed-temperature-normal-20261007/`](source-body-fixed-temperature-normal-20261007/README.md) — Normal source body and fixed-temperature exporter — 2026-10-07
- [`underground-source41-timeout-20261007/`](underground-source41-timeout-20261007/README.md) — Full underground carrier: preserved source41 timeout

## MeshGenerator（meshgen 引擎）

第三个网格引擎接入时的正式通道诊断与 S10 单元闭合失败记录；严格网格通过之前的阶段证据。

- [`meshgen-formal-channel-diagnostic-2026-10-06/`](meshgen-formal-channel-diagnostic-2026-10-06/README.md) — Formal MeshGenerator refusal and diagnostic repair
- [`meshgen-formal-s10-four-cells-2026-10-06/`](meshgen-formal-s10-four-cells-2026-10-06/README.md) — Formal Meshgen actual S10 four-cell failure audit
- [`meshgen-pres9b-native-capture-2026-10-06/`](meshgen-pres9b-native-capture-2026-10-06/README.md) — Actual normal MeshGenerator preS9b capture, 2026-10-06

## 风冷求解、边界与后处理

求解侧的分量参考、热通量符号小盒验证、Shinaide 风冷冒烟，以及风冷后处理的选定时刻与修订号门禁浏览器验收。

- [`air-postprocess-revision-browser-2026-09-27.md`](air-postprocess-revision-browser-2026-09-27.md) — 风冷后处理双标签版本门禁浏览器验收（CPU，2026-09-27）
- [`air-postprocess-time-browser-2026-09-27-mesh.png`](air-postprocess-time-browser-2026-09-27-mesh.png)
- [`air-postprocess-time-browser-2026-09-27-pending.png`](air-postprocess-time-browser-2026-09-27-pending.png)
- [`air-postprocess-time-browser-2026-09-27-t1.png`](air-postprocess-time-browser-2026-09-27-t1.png)
- [`air-postprocess-time-browser-2026-09-27.json`](air-postprocess-time-browser-2026-09-27.json) — CPU-only React Three.js postprocess display and explicit OpenFOAM time selection; synthetic fixture, no CFD pr…
- [`air-postprocess-time-browser-2026-09-27.md`](air-postprocess-time-browser-2026-09-27.md) — 风冷后处理选定时间与浏览器显示验收（CPU，2026-09-27）
- [`cfdrun-snapshot-ordinary-live-runtime-2026-10-06/`](cfdrun-snapshot-ordinary-live-runtime-2026-10-06/README.md) — 普通 RAM 快照：真实 React 小通道运行证据
- [`openfoam-heatflux-sign-2026-09-27.json`](openfoam-heatflux-sign-2026-09-27.json) — Isolated OpenFOAM v2412 buoyantBoussinesqSimpleFoam 1x10x1 thermal box, Y height 0.5 m, g=0, laminar, CPU affi…
- [`openfoam-heatflux-sign-2026-09-27.md`](openfoam-heatflux-sign-2026-09-27.md) — OpenFOAM 热通量正负号小盒验证（2026-09-27）
- [`pressure-component-gauge-2026-10-05/`](pressure-component-gauge-2026-10-05/README.md) — Pressure-component references, 2026-10-05
- [`run_shinaide_air_smoke.py`](run_shinaide_air_smoke.py) — Bound a Shinaide OpenFOAM solve and clean only processes in its scratch case.
- [`shinaide-air-smoke-2026-09-27.json`](shinaide-air-smoke-2026-09-27.json)
- [`shinaide-air-smoke-initial-2026-09-27.json`](shinaide-air-smoke-initial-2026-09-27.json)
- [`verify_shinaide_air_smoke.py`](verify_shinaide_air_smoke.py) — Verify archived Shinaide OpenFOAM smoke artifacts without running a solver.

## 风液混合

液冷到风冷单向热量交接的 CPU 验证、浏览器运行与分区网格 patch 面积试验。

- [`partitioned-hybrid-patch-area-2026-09-27.md`](partitioned-hybrid-patch-area-2026-09-27.md) — OpenFOAM v2412 分区风侧网格的严格质量与 patch 面积试验
- [`shinaide-hybrid-browser-2026-09-27.json`](shinaide-hybrid-browser-2026-09-27.json)
- [`shinaide-hybrid-heat-cpu-2026-09-27.json`](shinaide-hybrid-heat-cpu-2026-09-27.json) — Isolated self-owned dcdtuni mock integration; no NV telemetry/calibration; no accepted air CFD prediction
- [`shinaide-hybrid-heat-cpu-2026-09-27.md`](shinaide-hybrid-heat-cpu-2026-09-27.md) — Shinaide 风液热量交接 CPU 验证（2026-09-27）
- [`shinaide-hybrid-primary-return-mock-2026-09-27.png`](shinaide-hybrid-primary-return-mock-2026-09-27.png)

## 瞬态关键帧与 cfdrun 版本

cfdproj v5 时间轴（T1–T5）、.liquidrun v4、cfdrun v3/v4（E8a 单一 simulationConfig）、分域稳态/瞬态（E8b）与瞬态步内收敛（E8c）。

- [`e8a-single-simconfig-2026-10-01/`](e8a-single-simconfig-2026-10-01/README.md) — E8a 回执(2026-10-01)——cfdrun v4:只有一份 simulationConfig
- [`e8b-domain-modes-2026-10-01/`](e8b-domain-modes-2026-10-01/README.md) — E8b 回执（2026-10-01）——风冷、液冷各自稳态/瞬态，顶栏一处切换，界面重做
- [`e8c-transient-convergence-2026-10-01/`](e8c-transient-convergence-2026-10-01/README.md) — E8c 回执(2026-10-01)——瞬态每个时间步真正收敛,求解页可调稳态/瞬态两套非线性阈值
- [`transient-keyframes-t1-2026-09-30/`](transient-keyframes-t1-2026-09-30/README.md) — 瞬态关键帧 T1 回执(2026-09-30)——cfdproj v5 核心模型
- [`transient-keyframes-t25-2026-09-30/`](transient-keyframes-t25-2026-09-30/README.md) — 瞬态关键帧 T2.5 回执(2026-09-30)——液冷关键帧进入 v2/v3 编译,风冷关键帧先拒绝
- [`transient-keyframes-t2a-2026-09-30/`](transient-keyframes-t2a-2026-09-30/README.md) — 瞬态关键帧 T2a 回执(2026-09-30)——冻结正确性、顶栏稳态/瞬态切换、退役旧瞬态页
- [`transient-keyframes-t2b-2026-09-30/`](transient-keyframes-t2b-2026-09-30/README.md) — 瞬态关键帧 T2b 回执(2026-09-30)——进度条与轨道面板
- [`transient-keyframes-t2c-2026-09-30/`](transient-keyframes-t2c-2026-09-30/README.md) — 瞬态关键帧 T2c 回执(2026-09-30)——属性面板时刻语义与求解页瘦身
- [`transient-keyframes-t3-2026-09-30/`](transient-keyframes-t3-2026-09-30/README.md) — 瞬态关键帧 T3 回执(2026-09-30)——.liquidrun v4 与四种端点边界
- [`transient-keyframes-t4-2026-09-30/`](transient-keyframes-t4-2026-09-30/README.md) — 瞬态关键帧 T4 回执(2026-09-30)——风冷 cfdrun v3、OpenFOAM 表与稳态起点链
- [`transient-keyframes-t5-2026-09-30/`](transient-keyframes-t5-2026-09-30/README.md) — 瞬态关键帧 T5 回执(2026-09-30)——结果随播放头回放、瞬态判定、端到端

## 液冷 DAE 求解器与验证

Newton DAE 的数值证据：制造解（MMS）时间阶、伴随梯度与中心差分、CPU/GPU 一致、矩阵自由与预条件探针、NV mock 与 Shinaide 全图运行、在线输入与重放，以及配套的复现脚本（`verify_*.py`）和夹具。

- [`accepted-input-browser-2026-09-26.json`](accepted-input-browser-2026-09-26.json) — actual browser create/heat/single-step/pending/export/cancel; strict accepted prefix and persisted lineage
- [`accepted-input-replay-2026-09-26.json`](accepted-input-replay-2026-09-26.json) — actual NV/Shinaide browser trajectories replayed through standalone v3 file entry; no checkpoint passed to sol…
- [`accepted-input-replay-2026-09-26.md`](accepted-input-replay-2026-09-26.md) — 已接受输入的独立重放
- [`actual-liquid-ring-mms-20261006/`](actual-liquid-ring-mms-20261006/README.md) — 液冷环路 MMS：正常浏览器运行与独立核查（2026-10-06）
- [`authored-physical-t-fitting-20261007/`](authored-physical-t-fitting-20261007/README.md) — Authored physical T fitting and safe pipe split
- [`coldplate-pairs-authoritative-2026-09-30/`](coldplate-pairs-authoritative-2026-09-30/README.md) — 冷板单一真源回执(2026-09-30)——`cpCuPairs` 是服务器冷板的唯一列表
- [`dae-baseline-d5388f8a-20260928/`](dae-baseline-d5388f8a-20260928/README.md) — Stage 0 基线与 compile 可行性探针回执 — dae-baseline-d5388f8a-20260928
- [`dae-compiled-nv-20260928/`](dae-compiled-nv-20260928/README.md) — G1 门回执 — NV mock 1604 态 compiled 路由重算(2026-09-28)
- [`dae-deployment-2026-09-26.json`](dae-deployment-2026-09-26.json)
- [`dae-final-verification-2026-09-26.json`](dae-final-verification-2026-09-26.json)
- [`dae-gpu-s3a-20260928/`](dae-gpu-s3a-20260928/README.md) — S3a GPU episode 回执 — 物理 CUDA 验收 + A2 微基准(2026-09-28)
- [`dae-gpu-s3b-nv-20260928/`](dae-gpu-s3b-nv-20260928/README.md) — S3b GPU episode 回执 — NV mock 1604 态 GPU parity(dense,float64)
- [`dae-gpu-s4-nv-20260928/`](dae-gpu-s4-nv-20260928/README.md) — S4 GPU episode 回执 — NV 1604 GPU+compile 组合门与同步解剖(2026-09-28)
- [`dae-pgmres-nv-20260928/`](dae-pgmres-nv-20260928/README.md) — G2 门回执 — NV mock 1604 态 'pgmres'(右预条件重启 GMRES,matrix-free 前向)
- [`dae-regression-2026-09-26.json`](dae-regression-2026-09-26.json)
- [`dae-residual-vectorization-2026-09-26.json`](dae-residual-vectorization-2026-09-26.json)
- [`dae-review-2026-09-26.md`](dae-review-2026-09-26.md) — Targeted DAE correctness review
- [`equation-graph-vectorization-2026-09-26.json`](equation-graph-vectorization-2026-09-26.json) — CPU profiler of the actual EquationGraph residual; no CUDA execution
- [`equation-graph-vectorization-2026-09-26.md`](equation-graph-vectorization-2026-09-26.md) — EquationGraph 设备与张量化验证
- [`fixtures/`](fixtures/)
- [`journal-liquid-ov-integration-2026-09-28.md`](journal-liquid-ov-integration-2026-09-28.md) — Liquid DAE, Agent design and OV integration — 2026-09-28
- [`liquid-current-glycol25-jacfwd-2026-10-13/`](liquid-current-glycol25-jacfwd-2026-10-13/README.md) — Current Shinaide liquid DAE verification (2026-10-13 SGT)
- [`liquid-dae-matrix-free-2026-09-27/`](liquid-dae-matrix-free-2026-09-27/README.md) — 液冷 DAE 矩阵自由 Newton/伴随：CPU 数值与性能边界
- [`liquid-dae-mms-2026-09-26.json`](liquid-dae-mms-2026-09-26.json)
- [`liquid-dae-mms-product-20260928/`](liquid-dae-mms-product-20260928/README.md) — Stage 2b 回执 — MMS 产品 case 解析验收(2026-09-28)
- [`liquid-demo-browser-e2e-2026-09-29/`](liquid-demo-browser-e2e-2026-09-29/) — [CHECKLIST.md] Stage 5 — browser-use E2E 终验 checklist(2026-09-29 凌晨,SGT)
- [`liquid-execution-controls-20261007/`](liquid-execution-controls-20261007/README.md) — Shared liquid Solve execution controls — 2026-10-07
- [`liquid-full-mms-asymptotic-20261007/`](liquid-full-mms-asymptotic-20261007/README.md) — Full Shinaide ring MMS: two finer time steps, 2026-10-07
- [`liquid-fullcase-gpu-glycol-browser-20261007/`](liquid-fullcase-gpu-glycol-browser-20261007/README.md) — Actual full Shinaide GPU/glycol liquid runs in the Windows browser, 2026-10-07
- [`liquid-fullcase-gpu-parity-gradient-20261007/`](liquid-fullcase-gpu-parity-gradient-20261007/README.md) — Full Shinaide graph CPU/GPU parity and implicit heat gradient — 2026-10-07
- [`liquid-gui-post-ov-restart-20261007/`](liquid-gui-post-ov-restart-20261007/README.md) — GUI liquid run after normal OV debug restart
- [`liquid-health-2026-09-28/`](liquid-health-2026-09-28/README.md) — 40901 当前主仓库液冷运行复核
- [`liquid-mms-reproducibility-2026-10-05/`](liquid-mms-reproducibility-2026-10-05/README.md) — MMS fixture reproducibility repair (2026-10-05)
- [`liquid-monitor-failure-label-normal-20261007/`](liquid-monitor-failure-label-normal-20261007/README.md) — Current-session liquid failure and reference-label repair
- [`liquid-ordinary-control-ad-independent-20261007/`](liquid-ordinary-control-ad-independent-20261007/README.md) — Ordinary liquid-control AD evidence publication
- [`liquid-quadratic-mms-real-gui-20261006/`](liquid-quadratic-mms-real-gui-20261006/README.md) — 新二次曲率液冷 MMS：真实 GUI 四档回执（2026-10-06）
- [`liquid-runtime-slice-ab-2026-09-22.json`](liquid-runtime-slice-ab-2026-09-22.json)
- [`liquid-supply-temp-online-20260928/`](liquid-supply-temp-online-20260928/README.md) — Stage 1 回执 — 在线供水温度命令通道(primary_inlet_K,2026-09-28)
- [`local-replay-evidence-2026-09-26.json`](local-replay-evidence-2026-09-26.json) — Local inspection of transferred accepted-input replay artifacts
- [`migrate_shinaide_dae_fixture.py`](migrate_shinaide_dae_fixture.py) — Explicit verification-only v2 -> v3 mock conversion, never an editor migration.
- [`nv-cpu1-cpu4-2026-09-27.json`](nv-cpu1-cpu4-2026-09-27.json) — same 34-rack mock; independent CPU1/CPU4 processes, one accepted heat step and heat adjoint only
- [`nv-cpu1-cpu4-2026-09-27.md`](nv-cpu1-cpu4-2026-09-27.md) — NV mock CPU 1 / 4 comparison — 2026-09-27 SGT
- [`nv-dae-browser-2026-09-26.json`](nv-dae-browser-2026-09-26.json) — real React browser -> cfdproj compiler -> v3 HTTP runtime -> vectorized Newton DAE -> live curves; mock plant,…
- [`nv-dae-browser-reviewed-2026-09-26.json`](nv-dae-browser-reviewed-2026-09-26.json)
- [`nv-dae-gradient-2026-09-26.json`](nv-dae-gradient-2026-09-26.json) — synthetic 34-rack NV inventory mock; numerical differentiation only
- [`nv-pek02-mock-fixture.md`](nv-pek02-mock-fixture.md) — NV PEK02 inventory mock — project-owned DAE fixture
- [`nv-vectorized-cpu-2026-09-26.json`](nv-vectorized-cpu-2026-09-26.json) — synthetic inventory mock numerical probe; not NV calibration or a general hardware benchmark
- [`nv-vectorized-cpu-2026-09-26.md`](nv-vectorized-cpu-2026-09-26.md) — NV mock vectorized DAE CPU probe
- [`plot_shinaide_dae_response.py`](plot_shinaide_dae_response.py) — Plot only recorded CPU DAE values; no fitting, smoothing or simulated data.
- [`shinaide-browser-evidence-2026-09-26.json`](shinaide-browser-evidence-2026-09-26.json)
- [`shinaide-cpu-gradient-2026-09-26.json`](shinaide-cpu-gradient-2026-09-26.json) — Synthetic-parameter Shinaide code verification; not field calibration or validation.
- [`shinaide-cpu-gradient-2026-09-26.md`](shinaide-cpu-gradient-2026-09-26.md) — Shinaide CPU 梯度验证（2026-09-26）
- [`shinaide-cross-compiler-2026-09-26.md`](shinaide-cross-compiler-2026-09-26.md) — Shinaide 混合工程的双编译契约验证
- [`shinaide-dae-browser-2026-09-26.json`](shinaide-dae-browser-2026-09-26.json) — actual saved Shinaide hybrid project -> strict v3 DAE -> browser heat command -> persisted results and time-se…
- [`shinaide-dae-gradient-2026-09-26.json`](shinaide-dae-gradient-2026-09-26.json) — new unified Newton DAE, full 3-step trajectory on Shinaide geometry; mock parameters, no NV calibration
- [`shinaide-dae-load-step-2026-09-26.csv`](shinaide-dae-load-step-2026-09-26.csv)
- [`shinaide-dae-load-step-2026-09-26.json`](shinaide-dae-load-step-2026-09-26.json) — numerical experiment with explicit mock primary circuit; not NV calibration or schema/runtime/UI integration
- [`shinaide-dae-load-step-2026-09-26.md`](shinaide-dae-load-step-2026-09-26.md) — Shinaide DAE 600 s 功率阶跃（2026-09-26）
- [`shinaide-dae-probe-2026-09-26.json`](shinaide-dae-probe-2026-09-26.json) — numerical probe only; no calibrated NV primary circuit or schema/runtime/UI integration
- [`shinaide-dae-probe-2026-09-26.md`](shinaide-dae-probe-2026-09-26.md) — Shinaide 新 DAE 单核数值 probe（2026-09-26）
- [`shinaide-dae-response-2026-09-26.png`](shinaide-dae-response-2026-09-26.png)
- [`shinaide-dae-scene-export-2026-09-26.json`](shinaide-dae-scene-export-2026-09-26.json) — actual browser DAE time-selected colored scene and CPU OpenUSD; no Kit/RTX/live OV/GPU verification
- [`shinaide-dae-ui-cpu-threads-2026-09-27.json`](shinaide-dae-ui-cpu-threads-2026-09-27.json) — Saved Shinaide DAE .cfdproj CPU-thread choice through React v3 export and one actual CPU transient step
- [`shinaide-demo-provenance-2026-09-28/`](shinaide-demo-provenance-2026-09-28/README.md) — Stage 4 回执 — Shinaide demo 打磨(2026-09-28 夜)
- [`shinaide-full20s-control-ad-20261006/`](shinaide-full20s-control-ad-20261006/README.md) — Shinaide ordinary full-trajectory control AD verification — 2026-10-06
- [`shinaide-liquid-cuda-2026-10-05/`](shinaide-liquid-cuda-2026-10-05/README.md) — Shinaide 真实 React v4：CUDA float64 短轨迹回执，2026-10-05
- [`shinaide-liquid-full-graph-2026-10-05/`](shinaide-liquid-full-graph-2026-10-05/README.md) — Shinaide complete-graph liquid verification (2026-10-05)
- [`shinaide-liquid-mixed-keyframes-2026-10-06/`](shinaide-liquid-mixed-keyframes-2026-10-06/README.md) — Shinaide full mixed-keyframe liquid product proof — 2026-10-06
- [`shinaide-liquid-step-resolution-2026-10-05/`](shinaide-liquid-step-resolution-2026-10-05/README.md) — Shinaide physical load-step resolution (2026-10-05)
- [`shinaide-online-controls-browser-2026-09-27.json`](shinaide-online-controls-browser-2026-09-27.json) — Actual isolated React browser, project compiler, CPU PyTorch DAE, live heat/pump/valve inputs, independent equ…
- [`shinaide-online-controls-heat-audit-2026-09-27.json`](shinaide-online-controls-heat-audit-2026-09-27.json) — CPU-only v3 DAE online heat, pump and positive-valve input acceptance plus liquid heat-ledger verification; no…
- [`single-gpu-episode-2026-09-26.json`](single-gpu-episode-2026-09-26.json)
- [`single-gpu-numerical-2026-09-26.json`](single-gpu-numerical-2026-09-26.json)
- [`thermal-odil-test-regression-2026-09-27.md`](thermal-odil-test-regression-2026-09-27.md) — Legacy thermal ODIL test investigation (2026-09-27)
- [`verify_accepted_input_replay.py`](verify_accepted_input_replay.py) — Replay actual archived browser trajectories from executed.liquidrun alone.
- [`verify_local_replay_evidence.py`](verify_local_replay_evidence.py) — Verify transferred replay evidence without importing or running a solver.
- [`verify_nv_cpu1_cpu4_20260927.py`](verify_nv_cpu1_cpu4_20260927.py) — Bounded, independent-process CPU1/CPU4 NV physical DAE comparison.
- [`verify_nv_dae_gradient.py`](verify_nv_dae_gradient.py) — NV mock trajectory gradients: independent central differences, CPU only.
- [`verify_nv_vectorized_cpu.py`](verify_nv_vectorized_cpu.py) — Bounded CPU1/CPU2 replay of the same project-derived NV mock DAE input.
- [`verify_shinaide_cpu_gradient.py`](verify_shinaide_cpu_gradient.py) — Bounded, reproducible Shinaide gradient check using the production Torch path.
- [`verify_shinaide_dae_gradient.py`](verify_shinaide_dae_gradient.py) — Bounded CPU check of the NEW common DAE adjoint, not the split baseline.
- [`verify_shinaide_dae_load_step.py`](verify_shinaide_dae_load_step.py) — Single-core, 90-second-budget Shinaide DAE 600 s load-step measurement.
- [`verify_shinaide_dae_probe.py`](verify_shinaide_dae_probe.py) — Bounded CPU numerical probe of the actual Shinaide equation graph.
- [`verify_shinaide_dae_scenes.py`](verify_shinaide_dae_scenes.py) — Validate actual browser DAE frames and their portable CPU OpenUSD exports.
- [`verify_single_gpu_acceptance.py`](verify_single_gpu_acceptance.py) — One explicitly authorized CUDA acceptance phase; one physical GPU only.

## Omniverse 显示

Kit 显示桥从基线视频到 React 无缝切换、几何与网格通道、后处理与液冷着色、切换租约、视口拾取与相机交接、PBR 材质与纹理资产的分阶段回执。

- [`ov-bounded-readback-20261007/`](ov-bounded-readback-20261007/README.md) — Bounded OV readback checkpoint — 2026-10-07
- [`ov-bounded-readback-runtime-20261007/`](ov-bounded-readback-runtime-20261007/README.md) — OV bounded readback owned runtime promotion — 2026-10-07
- [`ov-camera-handoff-normal-20261008/`](ov-camera-handoff-normal-20261008/README.md) — OV camera handoff, actual normal deployment (2026-10-08 SGT)
- [`ov-common-grid-raster-v3-normal-20261007/`](ov-common-grid-raster-v3-normal-20261007/README.md) — Common React background-grid raster V3: normal source and owned runtime checkpoint
- [`ov-cpu-repair-2026-09-26.json`](ov-cpu-repair-2026-09-26.json)
- [`ov-display-air-liquid-2026-10-01/`](ov-display-air-liquid-2026-10-01/README.md) — OV 显示（液冷 + 风冷）回执（2026-10-01）
- [`ov-display-baseline-2026-09-29/`](ov-display-baseline-2026-09-29/README.md) — OV 显示战役 Stage 0 回执 — Kit→浏览器基线视频验证(2026-09-29)
- [`ov-display-browser-e2e-2026-09-30/`](ov-display-browser-e2e-2026-09-30/README.md) — OV 显示战役 Stage 5 回执(2026-09-30)——浏览器 E2E 终验
- [`ov-display-geometry-mesh-2026-09-29/`](ov-display-geometry-mesh-2026-09-29/README.md) — OV 显示战役 Stage 2 中期回执(2026-09-29 夜)——几何/网格通道已证,ack 中继回归在查
- [`ov-display-liquid-2026-09-30/`](ov-display-liquid-2026-09-30/README.md) — OV 显示战役 Stage 4 回执(2026-09-30)——液冷结果着色(全部件)+ 服务器签发血缘 + 可微 DAE 接入
- [`ov-display-postprocess-2026-09-30/`](ov-display-postprocess-2026-09-30/README.md) — OV 显示战役 Stage 3 回执(2026-09-30)——后处理三件套 + 唯二视口交互
- [`ov-display-react-switch-2026-09-29/`](ov-display-react-switch-2026-09-29/README.md) — OV 显示战役 Stage 1 回执 — start_server.sh 参数化 + 主 React↔OV 无缝切换(2026-09-29)
- [`ov-grid-pending-observer-20261007/`](ov-grid-pending-observer-20261007/README.md) — OV grid bounded pending observer — 2026-10-07
- [`ov-grid-status-retirement-20261007/`](ov-grid-status-retirement-20261007/README.md) — Retiring OV grid status when returning to Three.js, 2026-10-07
- [`ov-native-background-grid-20261007/`](ov-native-background-grid-20261007/README.md) — Native OV background grid: observed display and remaining limits
- [`ov-native-grid-and-view-handoff-20261008/`](ov-native-grid-and-view-handoff-20261008/README.md) — Native grid acknowledgement and view handoff — 2026-10-08
- [`ov-owned-output-layout-20261007/`](ov-owned-output-layout-20261007/README.md) — OV owned output layout: applied source and normal CPU tests
- [`ov-owned-output-room-mask-normal-2026-10-06/`](ov-owned-output-room-mask-normal-2026-10-06/README.md) — OV owned outputs and layered room-mask checkpoint, 2026-10-06
- [`ov-raster-camera-and-oo-a1-20261007/`](ov-raster-camera-and-oo-a1-20261007/README.md) — OV raster/camera and shared geometry A1 — 2026-10-07
- [`ov-render-only-startup-20261007/`](ov-render-only-startup-20261007/README.md) — OV render-only startup policy and actual display limits
- [`ov-render-output-observations-a-v2-normal-20261007/`](ov-render-output-observations-a-v2-normal-20261007/README.md) — Normal readonly OV render-output observations, F2
- [`ov-render-switch-2026-09-27/`](ov-render-switch-2026-09-27/README.md) — Shinaide React → Omniverse 实机切换验收
- [`ov-scene-content-identity-20261007/`](ov-scene-content-identity-20261007/README.md) — OV content identity checkpoint — 2026-10-07
- [`ov-selected-frame-live-and-native-failures-2026-10-06/`](ov-selected-frame-live-and-native-failures-2026-10-06/README.md) — Selected OV frame and strict native failure checkpoint — 2026-10-06
- [`ov-shared-postprocess-previews-cpu-20261007/`](ov-shared-postprocess-previews-cpu-20261007/README.md) — Shared React / OV postprocess previews: source and CPU checkpoint
- [`ov-source-pick-owned-20261007/`](ov-source-pick-owned-20261007/README.md) — Owned OV source selection — 2026-10-07
- [`ov-switch-fix-2026-10-01/`](ov-switch-fix-2026-10-01/README.md) — OV 渲染切换修复回执（2026-10-01）——带传感器/壁挂空调的工程能进 Omniverse，场景同步更小、有进度
- [`ov-switch-lease-2026-10-06/`](ov-switch-lease-2026-10-06/README.md) — OV sequential switch lease — 2026-10-06
- [`ov-whole-selection-camera-20261007/`](ov-whole-selection-camera-20261007/README.md) — Whole-object OV selection and bounded camera readback checkpoint — 2026-10-07
- [`pbr-materials-2026-09-30/`](pbr-materials-2026-09-30/README.md) — 内置模型 PBR 材质与纹理回执(2026-09-30)——Three.js 与 OV 两端
- [`project-uid-and-ov-selection-trace-20261007/`](project-uid-and-ov-selection-trace-20261007/README.md) — 工程 UID 与 OV 选择诊断 checkpoint — 2026-10-07
- [`react-ui-and-ov-display-20261007/`](react-ui-and-ov-display-20261007/README.md) — React UI and actual OV display checkpoint — 2026-10-07
- [`render-scene-persistence-2026-09-26.json`](render-scene-persistence-2026-09-26.json)
- [`single-gpu-kit-2026-09-26/`](single-gpu-kit-2026-09-26/README.md) — Bounded single-GPU Kit acceptance attempt
- [`texture-assets-2026-09-26.json`](texture-assets-2026-09-26.json) — real repository texture assets recovered from existing Mac LFS content; seven remaining local pointers pulled …

## React 编辑器、领域对象与界面

领域对象基础层与数值几何、工具栏与导航、共享显示驱动、尺寸单位、文件浏览器、整物体选择等前端改动的源码与真实浏览器验收。

- [`cabinet-shift-selection-normal-20261007/`](cabinet-shift-selection-normal-20261007/README.md) — Whole-object modifier selection normal checkpoint
- [`case-dir-tab-binding-2026-10-06/`](case-dir-tab-binding-2026-10-06/README.md) — Windows in-app browser: case directory binding across tabs
- [`dimension-units-react-live-ui-2026-10-06/`](dimension-units-react-live-ui-2026-10-06/README.md) — Dimension titles follow actual project input units
- [`domain-box-cdu-2026-10-06/`](domain-box-cdu-2026-10-06/README.md) — BOX/CDU structural geometry and actual liquid acceptance — 2026-10-06
- [`domain-box-cdu-material-2026-10-06/`](domain-box-cdu-material-2026-10-06/README.md) — BOX/CDU material creation recipe — preserved source evidence
- [`domain-box-cdu-material-live-ui-2026-10-06/`](domain-box-cdu-material-live-ui-2026-10-06/README.md) — Normal material recipe view — bounded UI observation
- [`domain-coldplate-pair-2026-10-06/`](domain-coldplate-pair-2026-10-06/README.md) — ColdPlatePair physical parameter ownership — source/build checkpoint
- [`domain-coldplate-pair-live-runtime-2026-10-06/`](domain-coldplate-pair-live-runtime-2026-10-06/README.md) — ColdPlatePair：正常浏览器与实际数值产物证据
- [`domain-object-foundation-2026-10-06/`](domain-object-foundation-2026-10-06/README.md) — Renderer-independent domain objects — stage plan, 2026-10-06
- [`domain-pose-ports-2026-10-06/`](domain-pose-ports-2026-10-06/README.md) — Numerical pose/ports acceptance, 2026-10-06
- [`domain-toolbar-main-integration-2026-10-06/`](domain-toolbar-main-integration-2026-10-06/README.md) — MAIN snapshot dependency integration and normal frontend build
- [`fe-public-contract-regression-20261007/`](fe-public-contract-regression-20261007/README.md) — The public2b full frontend suite is still RED (4359 passed,10 failed,4 skipped); original failed assertions an…
- [`file-browser-new-folder-2026-10-06/`](file-browser-new-folder-2026-10-06/README.md) — React 新建文件夹修复与 IT 浏览器验收（2026-10-06）
- [`frontend-ui-input-revision-2026-10-06/`](frontend-ui-input-revision-2026-10-06/README.md) — Engineering input revisions and same-UID project load — 2026-10-06
- [`frontend-unfiltered-e9-20261008/`](frontend-unfiltered-e9-20261008/README.md) — Ordinary e9 frontend full suite
- [`material-undefined-color-live-ui-2026-10-06/`](material-undefined-color-live-ui-2026-10-06/README.md) — Undefined material color — actual normal React UI
- [`react-browser-interaction-cpu-2026-09-27.md`](react-browser-interaction-cpu-2026-09-27.md) — Shinaide React 浏览器交互核查（CPU，2026-09-27）
- [`react-common-monitor-toolbar-axis-2026-10-13/`](react-common-monitor-toolbar-axis-2026-10-13/README.md) — React common monitor, toolbar and axis
- [`shared-react-display-driver-v3-normal-2026-10-06/`](shared-react-display-driver-v3-normal-2026-10-06/README.md) — Shared React display driver v3 — normal source, build and browser receipt
- [`shared-toolbar-density-20261007/`](shared-toolbar-density-20261007/README.md) — Shared React toolbar density, 2026-10-07
- [`shared-ui-orphan-cleanup-20261007/`](shared-ui-orphan-cleanup-20261007/README.md) — Shared React display ownership and orphan cleanup — 2026-10-07
- [`toolbar-expanded-budget-browser-20261007/`](toolbar-expanded-budget-browser-20261007/README.md) — Expanded React toolbar: normal-source and Windows browser evidence
- [`toolbar-fixed-timeline-2026-10-06/`](toolbar-fixed-timeline-2026-10-06/README.md) — Toolbar alignment and reserved transient timeline
- [`toolbar-navigation-2026-10-06/`](toolbar-navigation-2026-10-06/README.md) — Toolbar and orientation navigation — 2026-10-06
- [`toolbar-pinned-controls-20261007/`](toolbar-pinned-controls-20261007/README.md) — Pinned toolbar controls — normal source and real browser

## 平台、部署与工程检查点

启动器清理归属、按端口隔离的会话 cookie、工作区与启动界面、主机资源说明，以及几次主线同步检查点和交接记录。

- [`account-handoff-2026-10-06/`](account-handoff-2026-10-06/)
- [`auth-cookie-port-isolation-2026-10-06/`](auth-cookie-port-isolation-2026-10-06/README.md) — HTTP port session isolation
- [`cpu-resource-update-2026-09-27.json`](cpu-resource-update-2026-09-27.json)
- [`engineering-continuation-2026-10-11/`](engineering-continuation-2026-10-11/README.md) — Engineering continuation 2026-10-11
- [`launcher-cleanup-ownership-20261007/`](launcher-cleanup-ownership-20261007/README.md) — Launcher cleanup ownership — 2026-10-07
- [`main-checkpoint-2026-10-05/`](main-checkpoint-2026-10-05/README.md) — Main source and asset checkpoint — 2026-10-05
- [`source-checkpoints-20261007/`](source-checkpoints-20261007/README.md) — Native source checkpoint — 2026-10-07
- [`workspace-launcher-ui-2026-10-05/`](workspace-launcher-ui-2026-10-05/README.md) — Launcher and workspace UI validation, 2026-10-05
