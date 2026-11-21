# Shinaide dcCart 源面覆盖 RED 诊断

日期：2026-09-27。此记录只诊断 40901 的隔离候选网格，不修改原始 `.cfdproj`、STL、mesher 算法或质量阈值，也没有运行风冷求解。候选为隔离分支 `codex/dccart-mesh-quality-trial-20260927@d449aca9` 生成的 source-region 网格；其严格 `checkMesh -allTopology -allGeometry` 仍有 **5 项失败**，不能交给 CFD 求解。

## 复现入口与契约

- 网格：`/mnt/lv/home/litianyu/benchmarks/shinaide_dccart_source_region_trial_20260927/mesh-region/case/Mesh`。
- 终检日志：`/mnt/lv/home/litianyu/benchmarks/shinaide_dccart_source_region_trial_20260927/mesh-region/checkMesh-all.log`。
- 映射 fixture：[shinaide_dccart_source_support_red.json](../../../src/backend/tests/fixtures/shinaide_dccart_source_support_red.json)，显式指定 `processor0` 的 cell `46965–46967`，将墙面 master/slave 关联到 `merged_wall_baffle.stl:con_f`（工程 UID `m_22`），地板面 master/slave 关联到 `wallBaffle_floor_Raised_Floor.stl:Raised_Floor`（工程 UID `m_52`）。这一映射只用于该局部案例，聚合墙 patch 不能在全域推断为 `con_f`。
- 只读诊断：[mesh_source_support_probe.py](../../../src/backend/aircooling/runner/tools/mesh_source_support_probe.py) 读取 OpenFOAM v2412 little-endian、32-bit label、64-bit scalar 的 binary `polyMesh` 和 ASCII 多 solid STL。对所选 cell 相邻且已声明来源的边界面，取顶点均值作为采样点，正交投影到每个源三角面，在三角面上求最短距离；只有投影距离 ≤ `1e-5 m` 且离源面平面的距离 ≤ `0.003 m` 才算通过。平面距离预算承认 dcCart 将墙面量化到网格面，不会把偏移与面内越界混为一谈。此单点检查是必要条件，不证明整个 polygon 都被源 STL 覆盖。

在 40901 非 NSCC 主机用受控 CPU 运行：

```bash
PYTHONPATH=src python3 -m backend.aircooling.runner.tools.mesh_source_support_probe \
  --poly-mesh /mnt/lv/home/litianyu/benchmarks/shinaide_dccart_source_region_trial_20260927/mesh-region/case/Mesh/processor0/constant/polyMesh \
  --tri-surface /mnt/lv/home/litianyu/benchmarks/shinaide_dccart_source_region_trial_20260927/mesh-region/case/Mesh/constant/triSurface \
  --spec src/backend/tests/fixtures/shinaide_dccart_source_support_red.json
```

本次实际把同一脚本/fixture 复制到 40901 `/tmp`，在 CPU0–3 affinity、`nice 10` 下对以上源路径执行；进程返回码 **2**，完整逐面 JSON 为 [RED 结果](../../benchmarks/shinaide-mesh-source-support-red-2026-09-27.json)。脚本若无法读到预期六面会报错，而不是把空采样误当作通过。STL SHA-256 是 `merged_wall_baffle.stl` 的 `9b48e16ecad23606bbd44fb0a1298e4547820ed14cb3558251df5fd19d12e149`、`wallBaffle_floor_Raised_Floor.stl` 的 `2dab439e94d675195ef8505703409ce5ffd01dad58281056920ea842dba652bf`。

| cell | 最终 patch face | 源 UID | 面内越界 | 判定 |
|---|---:|---|---:|---|
| 46965 | `merged_wall_baffle_master` 155105 | `m_22` | 9.8939 mm | 失败 |
| 46965 | `Raised_Floor_master` 156068 | `m_52` | 0 | 阳性对照通过 |
| 46966 | `merged_wall_baffle_slave` 155729 | `m_22` | 9.8939 mm | 失败 |
| 46966 | `Raised_Floor_master` 156069 | `m_52` | 2.1942 mm | 失败 |
| 46967 | `merged_wall_baffle_master` 155155 | `m_22` | 1.1952 mm | 失败 |
| 46967 | `Raised_Floor_slave` 158150 | `m_52` | 0 | 阳性对照通过 |

这三个 cell 均是六面正交 hexa，z 区间 `2.812482609–2.814872980 m`，厚 **2.390 mm**；底部/侧面分别被标为 Raised_Floor 和 merged_wall_baffle 边界。源 `con_f` 墙片 x=`1.490737 m`、y 从 `0.8 m` 起、z 从 `2.814873 m` 起；最终墙 patch 量化到 x=`1.493066667 m`，其中两个 face 延到 y=`0.780357–0.8 m`，另一个虽在 y>`0.8 m`，z 仍在墙源片底边以下。源 Raised_Floor 在 y=`0.8 m`，在 x>`1.490738 m` 的此处需到 z=`2.815872 m` 才有三角面；46966 的地板 face 中心 x=`1.5139 m`、z≈`2.813678 m` 位于源面裁切空域。墙与地板的相关源顶点还存在约 **1 μm x 偏差**及 **0.999 mm z 错位**；这只是几何观测，不证明哪一生成阶段引入了错误。

## 剩余四类质量失败不是同一个局部现象

严格日志同时报告 277 个 face-tet 质量/负体积问题、34,562 个 concave cell、90 个低 interpolation weight 面、188 个低 volume-ratio 面。只读统计了八个 processor 的 `polyMesh/sets` 及面/单元位置：face-tet set 的本地条目合计 274，其中 143 个是 internal face，processor5 有 112 个，主要集中于 x≈5 m、z≈4 m 的 ACU/机柜附近；日志 277 与 set 条目 274 的计数口径不一致，不能合并当作同一数量。concave cell 每分区 3,877–4,820 个，约 19,552 个邻接至少一个边界 patch；低权重和体积比问题也跨多个 ACU/机柜及 MPI 边界，常见 z≈`4.014657 m`、`1.610309 m` 的薄层。因此修好上述三个接缝 cell 仍不会使全例 `checkMesh` 通过。

下一步先用两张正交零厚度源片的局部 fixture 固定本诊断的必要条件，逐阶段检查 builder 初始 faceZone、`cutBaffles`、后续合并/切片及 `createBaffles` 的覆盖归属，找到第一次将 patch/zone 延到源三角支持域外的步骤，再做单点算法修复。该 RED 记录不支持直接移动 Shinaide 的原始设备/地板、启用全局阈值放宽、跳过质量门禁或启动 CFD 求解。其余四类质量失败需各自复现和归因。

后续[逐阶段原生追踪](dccart-stage-support-trace-2026-09-27.md)已将首次越界定位到 `02baffles`，并记录 first-layer 焊接在裁切前移动零厚度源片顶点；近网格小例复现 12 面中 3 面 RED，远网格对照 52 面全通过。关闭 first-layer 会让面积覆盖门禁失败，仍不是修法。上述算法边界与其他质量失败继续分开处理。
