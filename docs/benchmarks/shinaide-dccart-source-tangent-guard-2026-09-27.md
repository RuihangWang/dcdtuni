# dcCart 原源面轮廓门禁：隔离候选与仍失败的真实网格

日期：2026-09-27。此工作在 40901 非 NSCC 主机的独立 `codex/dccart-residual-quality-20260927` 工作树进行；从自有开发主线 `09c6c238` 建立，没有修改用户 Shinaide `.cfdproj`、任务服务或原始 STL，没有使用 GPU，也没有启动风冷 CFD 求解。这里记录的 C++ 拒绝门禁仅为历史隔离候选，**未并入当前 main 的生产算法**；当前仓库只保存 RED 测试与紧凑证据，更不能称风冷网格已修复。

## 两个可复现的失败

1. 同提交 `09c6c238` 的**独立原版工作树**重新编译 dcCartMesh，用完全相同的 13 份准备后 STL 和 354 行 UID 字典，在 CPU0–7、8 MPI、nice 10、每 rank 1 线程上生成 371,843 个 cell。随后真实执行 `checkMesh -parallel -allTopology -allGeometry`。OpenFOAM 进程码虽然为 0，终检文本明确 **`Failed 6 mesh checks`**：15 个高长宽比 cell（最大 4166.67）、294 个 face-tet、76 个低 determinant、34,727 个 concave/planar、295 个低插值权重面、609 个低体积比面。仅把本 scratch 的 OpenFOAM 输出从 ASCII 转成 binary 后复核，六项数值完全相同。对最终 processor0 的 `con_f/Raised_Floor` 六个指定面做[原三角支持域面心检查](../../src/backend/aircooling/runner/tools/mesh_source_support_probe.py)，四个面心在源外。必要的[mesher日志](shinaide-dccart-residual-2026-09-27/mesher.txt)、[严格终检日志](shinaide-dccart-residual-2026-09-27/checkMesh-all.txt)与[逐面RED](shinaide-dccart-residual-2026-09-27/source-support-red.json)已迁入仓库；输入 13 STL 与已归档源逐字节相同。原完整 polyMesh 未纳入 Git。
2. 既有[双正交零厚度片原生 RED](../../src/backend/tests/native/dccart_source_support/Allrun)以本次源码匹配二进制重新跑：远网格对照通过，近网格在 `02baffles` 首次给原源片以外的区域赋 zone，预期返回 2。将两片显式标注为 `zeroThicknessSheet` UID 后，修改前近网格仍写出 mesh，新增[门禁测试](../../src/backend/tests/test_source_tangent_guard.py)先以预期断言失败。只跳过所有切向 G1 类的实验没有解决问题：墙 faceZone 覆盖率降到 `0.00082142/0.0064 = 0.128347`，网格器拒绝写出；这段实验代码已撤销。

## 候选门禁做了什么

`dcCartSurfaces` 在任何坐标规范化或 G1/G2 焊接前，暂存**内部挡板**从 STL 读取的原顶点；网格仍使用原有焊接和裁切。只有输入给该 STL region 声明 `sourceFaceUid` 与 `representation=zeroThicknessSheet` 时，来源解析后才逐三角比较原顶点与裁切用顶点。法向位移单独扣除；面内位移超过 `max(4×coordSnapTol, 1 µm)` 时，报出 UID、STL region、原/新坐标和允许值，在切网格前拒绝。这一允许值只容纳输入浮点坐标规范化，绝非放宽 `checkMesh`。无 UID 的旧输入不走新门禁。这个顶点判据只是防止一种静默改写；通过它**不证明**最终面 polygon 完全位于原三角内，尚需全 UID、全 rank、整面投影验收。

原生测试修改后 2 项通过：标注 UID 的近网格被拒，远网格及只有法向吸附的对照可运行。原无 UID 的远/近网格仍保持原 `02baffles` 支持域结果，结构化数字与修改前相同；来源身份解析 13 项通过。真实 Shinaide 354-UID、8 MPI 输入则在切 cell 前确定性拒绝，首个报告为 `proj_Shinaide/m_1/blanking/front/u1-u3/source-sheet` 的原轮廓面内位移 **3.99641 mm**，允许值 **0.016667 mm**。这说明除了已定位的 `con_f/Raised_Floor`，机柜挡板源面也受到 G1 影响；不能只移动一个接缝把问题掩盖。8-rank 隔离候选的[拒绝日志](shinaide-dccart-residual-2026-09-27/uid-guard-rejection.txt)已迁入仓库；它不是当前 main 算法的运行结果。

## 剩余问题的分层

`09c6c238` 基线的 76 个低 determinant cell 中，73 个在 Rack_B_07 的约 27.9 µm 假薄层，另 3 个在 `con_f/Raised_Floor` 接缝。已有不同 STL region 分组候选把前者消除，但会把旧两面同一物理壁误切成流体间隙，且真实严格网格仍有五项失败，因此不能合入。其余 face-tet、concave、权重和体积比失败分布在机柜、ACU 与 MPI 接口，不归于单一接缝。对另一隔离候选的只读检查表明多数 concave 项实际是共面分片的凸 cell；盲目合并会破坏不同 neighbour/processor/baffle 的面关系。一个[两相邻薄 cell 的 OpenFOAM RED/对照](dccart-face-tet-collinear-2026-09-27/README.md)确认，保留共享边所需的共线点可单独触发 face-tet 错误，单面删点也会破坏相邻拓扑。

下一步需要从 `.cfdproj` 明确编译物理源组件、双面 wallPair、独立流体间隙及允许的 edgeContact，并用**原始三角整面覆盖**限制每个最终 patch 面；网格器可在经验证的法向预算内移动片，但不能暗中扩大面内轮廓。然后要分别处理共线共享点/共面分片与真正内凹 cell，保持 MPI 和 master/slave 面配对。只有同一原始 Shinaide 8-rank 网格的严格 `checkMesh` 为 0 项失败，且所有来源面通过几何支持核验，才能启动风冷求解。

[结构化证据](shinaide-dccart-source-tangent-guard-2026-09-27.json)包含二进制哈希、输入身份、资源上限和局限。
