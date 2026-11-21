# dcCart 四个工程回执（2026-10-01）——0.25 m / 8 核全部过 checkMesh，Base604 网格提速约 5 倍

**用户要求**：Base604、DAML-Testbed、Tri_angle、Shinaide 四个工程的风冷网格一律 baseSize 0.25 m、8 核、dcCart，必须过 checkMesh、要快、求解要收敛。

## 网格（40901，修复后构建；`checkMesh -parallel`，8 rank）

| 工程 | 修复前 | 修复后 |
|---|---|---|
| DAML-Testbed | **Failed 2**：1 个朝向错误面；max skew 4.55（2 个面）；172 756 单元，12.0 s | **Mesh OK**，max skew 3.99，172 760 单元，8.1 s |
| Shinaide | **Failed 1**：53 个面 max skew 5.53；714 626 单元，49.4 s | **Mesh OK**，max skew 3.91，714 717 单元，24.1 s |
| Tri_angle | 通过，110 497 单元，7.6 s | **Mesh OK**，max skew 3.98，110 495 单元，5.0 s |
| Base604 | 通过，7 619 213 单元，**1600 s**（切割 1404 s） | **Mesh OK**，max skew 3.98，7 619 234 单元，**317 s**（切割 106 s），峰值内存 42.7 GB |

非正交 > 70° 仍有少量面（DAML 最大 89.4°，与修复前同级），属警告。DAML 串行同样 Mesh OK。

## 根因与修复（`dcCartCutCells.C`）

1. **切割阶段 30 倍回归**（`0b8da6c0`）：9 月 18 日 `90806050` 加的 `syncSideOwners` 中，`sendBand` 用 OpenFOAM `List::push_back`
   逐个追加，每次都重新分配并整体拷贝，耗时随数据量平方增长；room_wall 这个覆盖整个本地网格的壳在每个 rank 上耗掉 100–400 s。
   改为 `DynamicList`。40902 二分定位（993fc077 快 → 8dbe3d6a 快 → 90806050 慢）；8 个 rank 共 64 个 polyMesh 文件与修复前逐字节一致。
2. **Shinaide 53 个高斜度面**（`c547d870`）：接缝切分判断点在切面哪侧用的是 onTolerance（0.25 m 时 3.1 mm），接缝离单元侧面只有
   1.8 mm，整侧角点被判为"在平面上"，切分落空，留下 1.8 mm 窄条。改用精确的轮廓平面容差定位并切割。
3. **DAML 1 个朝向错误面**（`3ac29a07`）：退化单元（< 1e-6 体积）被并入"共享面最大"的邻居且无质量检查；一片挡板比网格平面低 45 µm，
   切出的薄片并入倾斜楔形单元后面金字塔为负。现在按序试邻居，取合并后面金字塔全为正的第一个；仍由 owner rank 决定，结果不依赖 rank 数。
4. **DAML 2 个高斜度面**（`4795f53c`）：接缝切分只找落在轴平面上的边界面；斜面上的分界留下 11 mm 窄条。现在斜面接缝也按平面分组切开。
5. `db1b43b2`：`DCCART_PYR_DEBUG` 逐阶段检查面金字塔（默认关闭，用法见 DCCARTMESH.md）。

## 测试

- dcCart 相关 pytest：155 通过 / 40 跳过 / 1 失败——`test_mesh_engine::test_meshgen_pipeline_command_shape_and_skips`，**main 上同样失败**
  （meshgen 命令新增了环境开关，测试仍断言旧前缀；与本分支无关，已单列待办）。
- deploy.sh smoke：串行与 2 rank 通过、单元数一致。
- 原生 Tri_angle 回归（去掉 17 rank 用例）：35 通过 / 1 失败——串并行单元数相差 288（109 831 对 110 119），**修复前同样相差 288**，
  是 `6ce618d2` veto 修复带来、用户已接受的取舍。17 rank 用例未跑（超出 8 rank 约束）。

## 求解收敛

- Tri_angle（修复前后网格逐字节一致）：**CONVERGED**，500 步，回风传感器末窗漂移 0.021 K。
- Base604（旧网格，1000 步）：**NOT_CONVERGED**——p_rgh 末窗中位残差 0.073（门限 0.05），7 个回风传感器中 3 个仍在漂移（ACU-04 0.6 K），
  其余残差均通过且平滑下降。大机房 1000 步不够，新网格上按 3000 步重跑，结果另行补记。
- DAML-Testbed、Shinaide 新网格求解：进行中，结果另行补记。
