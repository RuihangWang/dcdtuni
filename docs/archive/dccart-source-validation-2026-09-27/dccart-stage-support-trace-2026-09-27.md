# dcCart 接缝源面支持域阶段定位（隔离 RED）

日期：2026-09-27。此实验只在 40901 非 NSCC 主机的隔离工作树和 scratch 进行；未修改原始 Shinaide `.cfdproj`、源 STL、dcCart 算法或质量阈值，也未运行 CFD 求解。隔离分支从 `codex/liquid-control-platform@2b9bf448` 建立；重跑使用已有候选 `codex/dccart-mesh-quality-trial-20260927@d449aca9` 对应的二进制，SHA-256 为 `033edd85db991e56803b804e8299d3070c9bbe34764ea45ecb9f1`。该候选原有严格 `checkMesh` 仍失败 5 项。

## 最小原生 RED 与阳性对照

[原生 fixture](../../../src/backend/tests/native/dccart_source_support/generate_case.py)生成两张正交、零厚度的三角片。墙为 `x=0.100000 m, y,z≥0.100000 m`；地板为 `y=0.100000 m, x≥0.100001 m, z≥0.101000 m`。二者接缝在 x 方向差约 1 μm、z 方向差 1 mm。两组运行的 STL 完全一致，只有背景网格位置不同。

- 阳性对照的源面远离网格平面：`02baffles` 的 52 个去重带 zone 面取中心投影，全部落在相应源三角片内；G1 焊接未移动顶点。
- RED 组将 x/y/z 网格平面置于 `0.102330/0.080357/0.097610 m`：同阶段 12 面中有 3 面超出源三角片支持域，最大面内距离 `0.0021949995 m`。日志记录 G1 把 `x=0.100001` 的坐标类移至 `0.102330 m`，6 顶点移动，最大位移 `0.0023295 m`。此小例的后续合并消除了这些标签，因此**终态并无越界 patch**；RED 固定的是更早的 `cutBaffles` 阶段不变量。
- `DCCART_FIRSTLAYER_FRACTION=0` 并非修法：同一 RED 几何未移动，但 `cutBaffles` 面积门禁测得 wall zone 仅覆盖 `0.00082142/0.0064 m²`（0.128347），网格器拒绝写出。

在 OpenFOAM v2412 环境中设置 `DCDT_MESHER` 为上述二进制路径，运行 `bash src/backend/tests/native/dccart_source_support/Allrun /新的scratch路径`。当前实现的**预期退出码为 2**：先打印对照通过，再将 `02baffles` 的 RED 明细写入 `near-grid/stage-support.json`。修复后该命令应退出 0；其独立检查器可用于别的显式源面映射。采样只验证面中心这一必要条件，不等于整块 polygon 的覆盖证明。

## Shinaide 首次越界阶段

把既有候选的准备后 `system/` 与 13 份 `constant/triSurface/` 原样复制到 `/mnt/lv/home/litianyu/benchmarks/dccart_support_domain_trace_trial_20260927/full_stage`，仅将 scratch 的 `decomposeParDict` 从 8 改为 4，以 CPU0–3、nice 10、4 MPI 运行同一候选二进制；没有网格参数/阈值变化。`DCCART_DEBUG_BOX=1.485,0.775,2.809,1.545,0.825,2.825` 只打印接缝邻域的阶段快照。原始准备后 `merged_wall_baffle.stl` SHA-256 为 `9b48e16ecad23606bbd44fb0a1298e4547820ed14cb3558251df5fd19d12e149`，`wallBaffle_floor_Raised_Floor.stl` 为 `2dab439e94d675195ef8505703409ce5ffd01dad58281056920ea842dba652bf`。

阶段检查按原始 STL 的 `con_f` 对应 faceZone 3、`Raised_Floor` 对应 faceZone 4，并按中心落在上述 ROI 的面去重：`00assemble` 与 `01carving` 无这两个 zone 面；**`02baffles` 首次产生 4 个，4 个均超出源三角片投影**。`03splitwall`、`04relabel`、`05seamsplit`、`08preghost`、`09dropghost` 的同 4 个中心和越界距离不变。最大面内距离为 `9.89388 mm`（`con_f`）；可在源面平面上的 `Raised_Floor` 面超出 `2.19420 mm`。全例终态 master/slave patch 在该 ROI 有 6 个越界中心；原 8 MPI 案例的逐面 fixture 则为 6 面中 4 面越界，两种分区的采样范围和面 ID 不同，不能混称同一计数。

`DCCART_WELD_DEBUG` 给出输入三角片被 cut 前改写的具体类：`x=1.49074→1.49307 m`（25 顶点）以及 `z=2.81587→2.81248 m`（38 顶点），均由 first-layer floor 触发；全例总计 798 顶点移动，最大 6.99003 mm。`weldNearCoincident` 按坐标类改写**所有**位于该坐标的顶点，以保持连接；一个面的法向坐标改写也会改变另一正交源面的面内边界。因此阶段 RED 是“输入支持域被改写 + `cutBaffles` 在改写后的几何上赋 zone”的可复现结果，不应单独归咎于后续合并。上述关联不证明每个越界面只由某一 G1 移动造成，仍需逐三角来源跟踪。

## 下一个最小算法边界

先明确双层几何契约：导出 STL 是物理支持域，网格器的允许法向位移与面内轮廓变化要分别界定。之后在零厚度 source UID 的局部试验中，给 G1 移动保留每张源片的前后顶点对应关系，并在 `cutBaffles` 赋 zone 时核查候选 polygon 的**投影三角覆盖**，而非仅看面中心、同 plane 或全 zone 面积。若原源片与质量要求无法兼容，应在网格阶段明确拒绝并报告需修复的源片/接缝；不能将越界区域悄悄归给 wall/floor，也不能简单取消 first-layer floor、裁掉标签、放宽 `checkMesh` 或启动风冷求解。任何允许的接缝几何修复需要显式 source UID/配对规则，不能依据近距离猜测独立源面是一对。

机器可复查日志与局部数据保留在 `/mnt/lv/home/litianyu/benchmarks/dccart_support_domain_trace_trial_20260927/`：`native_red_rerun/{control,near-grid}`、`full_stage/mesher.log`、`full_stage/mesher_weld_trace.log`、`full_stage/iface_dbg/`。结构化数字见 [阶段证据](../../benchmarks/shinaide-dccart-stage-support-2026-09-27.json)。此实验没有让 Shinaide 网格通过，更不构成 CFD 预测。
