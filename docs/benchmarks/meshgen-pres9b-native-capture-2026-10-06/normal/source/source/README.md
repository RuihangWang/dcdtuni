# MeshGenerator — Agent Native CFD 网格生成器（结构 + 笛卡尔 cut-cell 双内核）

面向 CFD 的网格生成器：**C++ 确定性内核 + COMSOL 风格 GUI +
JSON-RPC Agent 接口 + subagent-swarm 自动布块原型**。终极目标：LLM Agent 自主完成
几何离散 → 网格生成 → 导出 OpenFOAM 的闭环。

**2026-09 起全力转向笛卡尔 cut-cell 技术**（Prompt #11）：纯 cut-cell（无 snap）
八叉树引擎，移植并魔改 snappyHexMesh castellation 语义（`refinementSurfaces`
level (min,max)、`nCellsBetweenLevels` 过渡层、2:1 平衡），OpenFOAM polyMesh 输出，
AMR 就绪（`cellLevel`/`pointLevel`/`level0Edge`/`dynamicMeshDict`）。
设计依据与验收记录：`docs/cartesian_dev/`（SPEC、理解报告、集成报告、checkMesh 矩阵）。

人类需求档案见 `prompt.md`（逐条原文存档）。

## 快速开始

```bash
brew install cmake ninja glfw glew        # 工具链（已验证 macOS arm64）
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build                    # 87 项核心测试

# GUI（COMSOL 式：顶栏工具 / 左栏流程+模型树 / 右栏属性 / 视口 / 控制台）
./build/gui/meshgen examples/three_element_airfoil_2d.json

# 无头批处理（结构网格）——运行输出一律写 case/logger/（仓库约定，禁建顶层 run/）
./build/cli/meshgen-cli examples/m6_wing_3d.json --auto-fix \
    --export-foam case/logger/m6/constant/polyMesh --report case/logger/m6.json

# 笛卡尔 cut-cell（3D 圆球扰流：表面 2 级加密、过渡层 3、OpenFOAM+AMR 输出）
./build/cli/meshgen-cli cartesian case/Sphere3D/Cartesian/Sphere3D.json \
    --export-foam case/Sphere3D/Cartesian/openfoam/constant/polyMesh \
    --export-vtu case/Sphere3D/Cartesian/openfoam/Sphere3D.vtu   # ~200 ms

# Agent JSON-RPC
./build/cli/meshgen-cli --server stdio      # 或 --server tcp:8787

# Swarm 自动布块原型
python3 scripts/autoblock_swarm.py --domain holed --out case/logger/swarm

# 经典算例再生成
python3 scripts/gen_examples.py examples

# GUI 自动化测试（模拟用户点击每个功能，不依赖屏幕解锁）
MESHGEN_AUTOTEST=1 ./build/gui/meshgen examples/two_block_duct.json
#   → case/logger/guitest/report.json（15 项 PASS/FAIL）+ step*.ppm 帧缓冲截图
```

## GUI 自动化测试

`MESHGEN_AUTOTEST=1` 启动应用级自动化：驱动按状态机向 ImGui 注入鼠标事件，
逐项模拟真实用户操作并断言状态——载入自动生成、点/线/块工具、模型树选择、
拉伸模态创建块、生成网格、自动匹配、魔法笔加密、视口选块、块切分、导出
OpenFOAM。每步用 GPU 颜色编码拾取验证命中；帧缓冲经 `glReadPixels` 落盘为
`step*.ppm`（转 PNG：`sips -s format png stepXX.ppm`），**无需屏幕解锁**即可
做标准 GUI 回归。当前 15/15 通过。

## 核心能力

- **笛卡尔 cut-cell 内核**（新主力，`core/src/Cartesian.cpp` ~2900 行）：
  snappy 式层级控制（`refinementSurfaces` level (min,max)、`nCellsBetweenLevels`
  锚定波过渡层【声明魔改，±1 层容差】、2:1 worklist 平衡+硬断言）、
  BVH 三角形求交、并查集洪泛内外判定（弃射线法）、粗侧单发射共形装配、
  小单元 face-pair 合并（禁止只删）、S10 闭合硬断言（ΣSf≈0，机器精度 2.6e-16）；
  Sphere3D（10547 流体胞）生成 **~190 ms**、`checkMesh` **Mesh OK**（regions=1）；
  AMR 就绪：`cellLevel`/`pointLevel`/`level0Edge`/`dynamicMeshDict` 落盘
  （ESI v2412 `dynamicRefineFvMesh` 契约，见 `docs/cartesian_dev/01_U3_amr_contract.md`）。
- **结构网格内核**：OpenFOAM 式加权混合插值（块边精确重现）+ 共形装配
  （共享面 DSU 归一 + 位置哈希兜底）+ 负体积目标化 untangle 平滑；
  SIMD（arm64 NEON / x86 AVX2，`core/include/meshgen/Simd.hpp`）行内核 +
  线程池按块并行（生成/质量/面装配本地缓冲无锁）。
- **布点律**：uniform / geometric（可调拉伸率 r）/ tanh / tanhSym / expSym；
  魔法笔自适应序列（间距推进法，激波捕捉）。
- **耦合分析**：面/边耦合检测、"同点数耦合边" 绿/红高亮、`autoFix` 单调传播
  （Spekreijse 边界契约思想的确定性实现）。
- **OpenFOAM 导出**：`constant/polyMesh`（points/faces/owner/neighbour/boundary）
  与 `blockMeshDict`（simpleGrading 近似）。
- **GUI 视觉调试**：GPU 颜色编码拾取、质量云图、耦合高亮脉冲、分布曲线/单元尺寸
  直方图、统计 HUD、块着色/隔离、线框采样步长。

## 算例（examples/，scripts/gen_examples.py 参数化生成）

| 算例 | 拓扑 | 质量状态 |
|---|---|---|
| `two_block_duct` | 2 块 H 型 | ✅ minSJ=1.0 |
| `single_hex` | 单块几何拉伸 | ✅ minSJ=1.0 |
| `cylinder_ogrid` | 4 块 O 型 | ✅ minSJ≈0.2+ |
| `three_element_airfoil_2d` | 3×5 块 O 型 + 背景 H 铺砌（93 块） | ⚠️ 拓扑正确；TE 强曲率处有折叠单元（路线图：椭圆光滑） |
| `m6_wing_3d` | O 型柱 + 展向 tanhSym | ⚠️ 同上 |
| `wing_body_3d` | 机身 O 环柱 + 共形机翼分支（80 块） | ⚠️ 尾锥强曲率同上 |

负体积单元仅出现在强曲率配置（钝尾缘 180° 角、尾锥收缩）；矩形/中等曲率配置全正。
椭圆（Poisson）块内光滑求解器列为路线图第一项，见 `docs/literature_review.md` §8。

## 目录

```
core/        内核库（SIMD/线程池/TFI/耦合/质量/导出/魔法笔）
cli/         meshgen-cli（批处理 + JSON-RPC stdio/tcp）
gui/         ImGui+GLFW+OpenGL GUI
scripts/     gen_examples.py（算例生成）、autoblock_swarm.py（swarm 原型）
docs/        literature_review.md、agent_native.md
Ref_code/    参考源码（OpenFOAM-dev / gmsh / dcdtuni / papers×10，git 忽略）
examples/    经典算例 JSON
tests/       87 项核心测试
```

## Agent Native 设计

- 内核与 GUI 解耦；一切操作可经 JSON-RPC 完成（`docs/agent_native.md`）。
- swarm 原型：块代理线程经消息总线 propose/claim/conflict/split/yield 协商占用，
  补漏代理做覆盖闭环，几何落地全走内核 → 带孔域 100% 覆盖、共形、minSJ=1.0。
- 文献基础：10 篇开放获取论文 + 逐篇笔记（`Ref_code/papers/`）。

## 路线图

1. 块内椭圆光滑（消除强曲率折叠）；2. 翼身相交分支拓扑模板自动化；
3. swarm 代理替换为真实 LLM subagent；4. 3D 中轴种子生成。
