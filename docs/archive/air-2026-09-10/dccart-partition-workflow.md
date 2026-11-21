# dcCart 分区网格、求解与后处理流程优化

状态：已实现并验证，2026-09-10。两例均使用 8 个 MPI ranks，网格规模保持不变，求解验证每次 100 步。

2026-09-11 的原生后处理、列表默认折叠与渲染修复见[后续说明](postprocess-rendering-fixes.md)。本报告的计时表保留 9 月 10 日测量值。

## 实际入口与改动范围

HTML 和 React 前端都通过同一个 Python 后端启动计算。选择 `meshEngine: dcCart` 后，优化后端公共流程即可同时覆盖两个界面，不需要分别实现两套 OpenFOAM 编排。

| 环节 | 代码入口 | 职责 |
| --- | --- | --- |
| HTML 按钮 | [STL_generate.html](../../../src/frontend/web/STL_generate.html)，`window.runMeshGeneration` / `runSolveOnly` | 从当前场景导出 `.cfdrun`；网格任务同时导出 STL，提交 `/api/run-mesh` |
| React 按钮 | [session.ts](../../../src/frontend/react/src/run/session.ts)，`runJob` | 调用 `buildRunRequest`，提交相同 API，按 `jobId` 接收日志 |
| React 请求构造 | [runSubmit.ts](../../../src/frontend/react/src/run/runSubmit.ts)、[runPayload.ts](../../../src/frontend/react/src/run/runPayload.ts) | 预检、构建 manifest、导出几何；求解请求复用网格，`stlFiles` 为空 |
| HTTP 后端 | [server.py](../../../src/server/server.py)，`handle_run_mesh` | 写入 `.cfdrun` 和上传的 STL，启动后台进程并维护任务状态 |
| CLI 分发 | [run_cfd.py](../../../src/backend/aircooling/runner/run_cfd.py) | `--mesh-only` → `run_mesh_only`；`--solve-only` → `run_solve_only`；否则 `run_simulation` |
| 网格与求解 | [stlgen_runner.py](../../../src/backend/aircooling/runner/stlgen_runner.py)，`STLGenRunner` | 按 mesh engine 选择流程，生成 OpenFOAM 配置并调用工具 |

后端启动命令为 `python -m backend.aircooling.runner.run_cfd --from-cfdrun <manifest>`，附加 `--mesh-only` 或 `--solve-only`。浏览器通过 `/api/log-stream?job_id=...` 接收运行日志。按钮到结果可用的总耗时还包括前端导出、请求传输和结果读取，不能只用 mesher 的执行时间代替。

## 修改前与修改后的命令链

一个关键澄清：修改前的 dcCart 路径已经不执行 `blockMesh`、`surfaceFeatureExtract` 和网格阶段的 `decomposePar`。这些命令属于 snappy 路径。dcCart 读取 `snappyHexMeshDict` 中的几何与加密配置，以及 `dcCartMeshDict` 中的背景网格平面，直接构建各 rank 网格。生成 `blockMeshDict` 文件也不等于执行了 `blockMesh`。

原有并行 dcCart 主流程为：

```text
前端导出与 API 写盘
  → dcCartMesh -parallel
  → reconstructParMesh -constant -mergeTol 1E-08 -noZero
  → 删除 processor 目录
  → [有挡板时] 生成临时字段、createBaffles -overwrite、修正壁面类型
  → checkMesh / 必要的拓扑清理
  → 生成串行 0/ 字段与公共配置
  → 复制 Mesh 到求解目录
  → decomposePar
  → 并行求解
  → reconstructPar -latestTime
  → writeExplicitBC
  → foamToVTK
  → PyVista / 前端绘图
```

新流程在网格与求解核数相同的常见情况下为：

```text
前端导出与 API 写盘
  → dcCartMesh -parallel
  → [挡板跨分区或无法确认时] redistributePar -parallel -overwrite（preserveFaceZones）
  → [有挡板时] createBaffles -overwrite -parallel、各 rank 壁面类型修正
  → checkMesh -parallel / 必要的并行拓扑清理
  → 各 processor 的 0/ 字段与公共配置
  → 仅复制求解所需输入
  → 直接在已有分区上并行求解
  → PyVista 原生读取各分区，在内存按物理边界名称拼接
  → PyVista / 前端绘图
```

`redistributePar` 在分区之间迁移现有网格，以 `preserveFaceZones` 约束把挡板两侧放到可建立配对 cyclic 边界的位置；它不生成全局串行网格。dcCart 成功写出所有 rank 后，将跨 processor 接口的 faceZone 面副本总数写入 `constant/dcCartPartitionInfo.json`。本轮信息明确为零时可以跳过迁移；元数据缺失、无效或核数不符时仍执行迁移。每次生成前删除旧元数据，避免读取上次结果。没有挡板字典也不执行迁移。

两个实测案例的面副本数分别是 102（DAML）和 378（Base604），因此它们都需要这一步。应单独计入其成本，不能把本次优化描述为这两个案例已省去所有网格搬运。零跨区面时跳过迁移的分支另有真实 OpenFOAM 小例验证。

## C++ 中跨分区 faceZone 的正确性与构建开销

[dcCartCutCells.C](../../../src/backend/aircooling/third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells/dcCartCutCells.C) 的 `toPolyMesh` 原来仅把 `internalIds` 中的面加入 faceZone。挡板恰好经过 processor 接口时，`dropGhostCells` 已将这个面转换为 processor 边界面，虽然仍有 zone 标签，写出时却没有保存它。后续重分区无法再完整识别挡板。

现在同时把带 zone 标签的 processor 边界面写入 faceZone。接口两侧面的法向相反，相应 `flipMap` 也必须相反；代码用 processor patch 的 owner 侧统一方向，内部面继续保持已有方向约定。这个修复保证重分区的 faceZone 约束能够看到跨接口的挡板面。

同一位置将 `List<labelList>` 改为 `List<DynamicList<label>>`，避免为大 faceZone 逐面追加时反复分配、复制已有标签数组。这改变的是列表构建方式，不改变网格加密或几何切割标准。

另一个主要开销来自 Python 的 `_reconcile_boundary_wall_types`：旧代码跨行反复匹配空白字符，并逐 patch 扫描整个 boundary 文件。现在按行扫描字段和 boundary，使用集合查找需要修正的壁面类型。在同一份 Base604 临时副本上，旧函数 19.159 秒，新函数 0.169 秒，输出逐字节一致；这是单函数测量，不是整个流程的加速倍数。

`createBaffles` 会把选中的面转换为边界；coupled 面的选择需要同时照顾另一侧，本仓库的 cyclic 挡板还需要满足配对关系。因此不能只把旧串行命令加上 `-parallel` 就认为结果等价。参见 [OpenFOAM createBaffles 源码](https://api.openfoam.com/2506/createBaffles_8C_source.html) 和 [工具说明](https://api.openfoam.com/2506/createBaffles_8C.html)。

## 为什么仍保留最终的并行 checkMesh

本次没有用“dcCart 是笛卡尔网格”作为跳过质量检查的理由。背景网格沿坐标方向并不意味着切割、合并后的每个单元或边界面都严格正交。体积、开口、非正交、面锥体和 skewness 检查仍有意义。

最终检查还应覆盖 mesher 之后的 `redistributePar`、挡板创建及边界类型调整。只在 dcCart 内部检查其刚生成的网格，无法证明后续拓扑变换后的网格仍然有效。当前保留 `checkMesh -parallel`，由 `_remove_disconnected_cells` 处理检查结果；出现需要清理的断连区域或孤立单元时，再进行相应拓扑操作及必要的复查。不是固定地为每个算例重复执行所有清理命令。

将来若要把更多检查放进 C++，需要先说明其覆盖哪些质量指标、在哪个拓扑版本上执行、如何生成与现有告警接口等价的结果，再衡量省下的进程启动和读盘成本。本次优先移除不必要的全局重构与格式转换，保留最终质量检查的覆盖范围。

## 求解和目录兼容性

[mesh_layout.py](../../../src/backend/aircooling/runner/mesh_layout.py) 统一识别串行和分区网格：processor 编号必须从 0 连续，且每个分区包含非空的 `points`、`faces`、`owner`、`neighbour`、`boundary`。旧算例若仍有根目录串行网格，则沿用根网格；新 dcCart 分区流程移除根 `polyMesh`，避免旧串行副本遮盖新分区。

`_setup_complete_case_in_mesh` 在每个 processor 生成 `0/`，公共物性和 system 配置仍写到 case 根目录。配对风扇入口的严格检查使用所有 rank 上 `nFaces > 0` 的物理 patch 并集：某个 rank 没有入口面是正常情况，所有 rank 都没有该入口才是错误。局部零面 patch 的边界条件仍保留，以支持并行边界条件的集体计算；各 rank 采用相同的全局湍流初始值来源。

基准还暴露出 Python 边界类型修正的开销。原 `_reconcile_boundary_wall_types` 对每个 wall-function patch 重新扫描完整 boundary 文件，字段发现阶段也使用了跨行的连续空白正则。现在字段与 boundary 均按行扫描一次，用 patch 名集合判断是否把 `type patch` 改为 `wall`，保留原有注释和其他 patch 类型。这尤其避免了把串行单次扫描的高开销重复到每个 processor。

在临时副本中，用 Base604 基线的 9 个初始字段及 806,210 字节 boundary 顺序执行旧、新函数，单次分别耗时 19.159 秒和 0.169 秒，两者输出均与原 boundary 字节一致。这是函数级对比，不代表整个网格流程的加速倍数；数据见 [微基准记录](../../../bench/dccart_partition_flow/results/reconcile_wall_types_microbenchmark.json)。

同核数求解直接使用这些分区。显式改用不同核数或串行求解时，仅在新的求解目录执行 `reconstructParMesh`、重新生成初始字段，再按需 `decomposePar`，并在日志说明原因。完成新的并行分区后删除这个求解副本中的临时串行网格；`Mesh/` 中的原网格保持可复用。

求解目录复制时排除已知无关产物：STL 几何、VTK 输出、旧日志、旧时间目录及预览缓存。其余未知文件继续保留，避免丢失用户在字典中引用的自定义输入表格、include 文件或库。稳态自定义 solver 已在各 rank 写出显式边界值，因此保留分区结果后不需要再运行 `reconstructPar` 或恢复其丢失边界值的 `writeExplicitBC`。非 dcCart 的既有重构流程保持兼容。

## 分区结果的预览与绘图

[postprocess.py](../../../src/backend/aircooling/runner/postprocess.py) 中的 `read_openfoam_case` 识别分区布局。PyVista 官方支持两种 case 模式，参见 [POpenFOAMReader.case_type](https://docs.pyvista.org/api/readers/_autosummary/pyvista.popenfoamreader.case_type)，但直接替换读取器还不足以覆盖真实大案例。

VTK 9.5.1 的字段字典查找对 patch 逐项进行正则匹配，见 [vtkFoamDict::Lookup](https://github.com/Kitware/VTK/blob/v9.5.1/IO/Geometry/vtkOpenFOAMReader.cxx#L4215) 和 [边界字段读取](https://github.com/Kitware/VTK/blob/v9.5.1/IO/Geometry/vtkOpenFOAMReader.cxx#L9286)。每个 processor 保留全局数千个 patch 的零面定义，会放大解析开销。因此建立临时读取视图，仅复制删去零面条目的 boundary 字典，其余几何和场文件使用符号链接。真实求解目录仍保留完整的边界列表。

稀疏视图的各 rank 活跃 patch 不同，而 VTK 的全局合并按 block 索引对齐，直接合并会混淆边界名称。最终实现用原生 `POpenFOAMReader` 单 case 模式分别读取这些分区视图，然后按物理 patch 名称显式拼接。每个 processor 只选择共同已写出的时间。普通 processor 接口隐藏，`processorCyclic` 面根据 `referPatch` 归回对应物理挡板，master/slave 保持分开。已有重构算例的 VTM 路径继续兼容。

分区接口还会产生坐标相同、局部插值不同的点。读取后以零容差在内存合并重复点，再从 cell data 统一计算 point data；各物理 patch 分别处理。两单元测试验证接口温度 300/310 K 得到同一个 305 K、速度 1/2 得到 1.5，并验证流线跨过分区接口。处理前后单元数不变，没有生成磁盘重构网格或 VTM。大 patch 列表的遍历采用整数索引，避免按名字反复线性查找；原生读取结束后恢复 locale，避免影响中文文件读写。

省去磁盘 VTM 并不代表读取成本为零：仍需读取 OpenFOAM 数据、在内存组织分区、提取表面，并向浏览器发送绘图所需数组。因此报告应分别记录 `openfoam_read`、几何处理和整体后处理初始化时间。API 中 `foamToVTK: 0.0` 只是说明没有执行转换。

## 实测结果与验证

环境为 Python 3.10.20、PyVista 0.47.3、VTK 9.5.1、OpenFOAM v2412；主机有 48 个逻辑 CPU，本轮 mesh / solve 均用 8 ranks。输入冻结在 `bench/dccart_partition_flow/frozen_inputs/`，manifest 哈希与 STL 逐字节核对完成。仅设置 dcCart、8 ranks、100 步及第 100 步写出，保留原几何、baseSize 和 refinement。用户原案例没有被本轮基准修改。

DAML-Testbed 为 **269,625 cells**，Base604 为 **3,511,847 cells**，优化前后相同。下表单位为秒：

| 环节 | DAML 原流程 | DAML 新流程 | Base604 原流程 | Base604 新流程 |
| --- | ---: | ---: | ---: | ---: |
| 网格 API | 15.755 | 19.147 | 192.735 | 139.849 |
| 100 步求解 API（含准备及收尾） | 40.893 | 24.135 | 484.150 | 434.920 |
| 冷后处理初始化 API | 8.475 | 5.463 | 113.276 | 94.684 |
| 后处理几何准备 API | 0.015 | 0.382 | 0.184 | 4.975 |
| 合计 | 65.138 | 49.127 | 790.345 | 674.428 |

合计分别减少 **24.58%** 和 **14.67%**。计时范围是 mesh API、100 步 solve API、冷 PP init API，以及首次 PP mesh 几何准备；不包括前端 STL 导出、网络传输和 GPU 绘制。几何准备必须单列：旧 VTM 路径在读取时已完成部分三角化，新路径在 get_mesh 时完成，合计已计入两者，避免将工作移动阶段误报为加速。

这是共享主机上的单次实测，不是控制负载后的多轮统计。DAML 的网格阶段本身从 15.755 秒增加到 19.147 秒，总代价由后续求解准备和收尾节省抵消；Base604 网格阶段节省 52.886 秒，约 27.4%。Base604 100 步数值迭代在新分区和当时负载下比基线慢，整体求解 API 仍因省去准备、重构和边界重写而缩短。因此这里没有把流程优化描述为求解器算法加速。

Base604 原流程求解准备约 71 秒，结果重构与边界重写约 104 秒；新流程直接复用 8 个分区并保留结果。最终 PP 原生读入各 rank 约 84.40 秒，内存拼接约 3.46 秒，点整理约 6.03 秒，其中边界整理约 3.94 秒。后者曾为 45.84 秒：串联相同原生 VTK 过滤器、仅最终包装，并保留单元数和数组 tuple 数校验后，避免了每个 patch 的重复 PyVista 包装/校验。

## 正确性与实际绘图

两例都完成 **100 步**，8 个 `processor*/100/T` 全部存在；最终求解目录没有根 `constant/polyMesh`、根 `100/` 或 `VTK/`。T、U、p_rgh 的全部单元值、表面 T / Umag、水平温度切片及速度流线均为有限值；每例生成 64 条验证流线。React 实际控件成功加载分区第 100 步并绘制 T 表面场及切片，后处理 API 全部返回 200，无页面异常。HTML 与 React 共用此次验证的后端 API；实际浏览器操作验证使用 React。

- [DAML React 切片](../../../bench/dccart_partition_flow/results/optimized_final/DAML-Testbed_browser/react_temperature_slice.png) · [流线图](../../../bench/dccart_partition_flow/results/optimized_final/DAML-Testbed_postprocess/velocity_streamlines.png) · [浏览器证据](../../../bench/dccart_partition_flow/results/optimized_final/DAML-Testbed_browser/browser_evidence.json) · [科学图及场值统计](../../../bench/dccart_partition_flow/results/optimized_final/DAML-Testbed_postprocess/postprocess.json)
- [Base604 React 切片](../../../bench/dccart_partition_flow/results/optimized_final/Base604_browser/react_temperature_slice.png) · [流线图](../../../bench/dccart_partition_flow/results/optimized_final/Base604_postprocess/velocity_streamlines.png) · [浏览器证据](../../../bench/dccart_partition_flow/results/optimized_final/Base604_browser/browser_evidence.json) · [科学图及场值统计](../../../bench/dccart_partition_flow/results/optimized_final/Base604_postprocess/postprocess.json)

原网格的质量警告没有隐藏或修复：DAML 原有 479 个 zip-up cells、195 个高偏斜面；Base604 原有 3,404 个高偏斜面，最大偏斜 24.779。Base604 旧生产路径未启用 `-allTopology`，在独立副本补上相同检查后也出现 **16,251 个 zip-up cells**，与新流程相同，见[质量对照](../../../bench/dccart_partition_flow/results/quality_parity/Base604_baseline_serial_allTopology.json)。

VTK 对 Base604 水平切片发出非流形/非闭合 polyhedron 警告。仅读基线内部 VTU 做同一 Y=2.2 m 切片，也出现相同类别的 **1,194 条警告**，切片点数同为 **74,385**，温度值有限，见[切片对照](../../../bench/dccart_partition_flow/results/optimized_final/Base604_postprocess/baseline_internal_slice.json)。这些警告属于输入网格已有问题。100 步验证证明启动、MPI、字段写入和绘图链条可用，不证明稳态收敛或预测精度。

最终相关回归 **166 项通过，另有 3 项 subtests 通过**，见[测试摘要](../../../bench/dccart_partition_flow/results/test_summary.json)。覆盖分区完整性、共同结果时间、严格 BC 与零面 rank、改核数/串行兼容、legacy VTM、边界名称与 processorCyclic、跨接口插值和流线、locale、缓存、预览及超时清理。改核数兼容的回归使用命令替身，本轮大案例实跑验证的是相同 8 核直接复用。

另有真实 OpenFOAM 小例：1,000 cells、挡板正好在两 rank 接口，200 个面副本经分区调整后两侧各 100 faces、面积 1；挡板在本地时副本数为 0，直接并行 createBaffles 同样通过。两者完整 checkMesh 均通过。分区孤岛清理小例也正确从 1,001 cells 清理为 1,000 cells，复查 Mesh OK。

## 使用和复现

源码和本仓库的 dcCart 二进制已更新。已运行的后端服务需重启，才能加载新的分区状态检测和后处理代码。前端选择 dcCart、启用并行网格，并保持 mesh / solve 核数一致，即可走复用路径，无需增加新的界面选项。

重新构建 dcCart：

```bash
cd src/backend/aircooling/third_parties/dcCartMesh
source env.sh
./Allwmake --cart-only -j 2
```

[基准目录说明](../../../bench/dccart_partition_flow/README.md)包含 API、浏览器和科学图脚本；[计时与输入冻结协议](../../../bench/dccart_partition_flow/BENCHMARK_NOTES.md)描述复现环境；[机器可读对比](../../../bench/dccart_partition_flow/results/comparison.json)保留完整数值。

中间环境失败、被终止的中间版本求解和两次读取超时均保留供诊断，未纳入最终对比。原生直接读取的初版在大案例上超时，最终版本通过稀疏读取视图和边界处理优化解决。最终性能数字来自修正后的独立 API 冷读取，科学图 CLI 的读取计时没有混入此表。
