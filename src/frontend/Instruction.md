# 可视化 STL 生成器 — 完整功能说明文档

> **历史文档说明（2026-10-21）**：本文描述的是**旧版 HTML 单文件编辑器**（`src/frontend/web/STL_generate.html`）早期 V5.0 的界面与导出格式，保留作参考，正文不再随代码更新。现状与本文的主要差别：
>
> - 默认产品前端是 React 编辑器（`src/frontend/react/`），由 `./src/start_server.sh` 启动，默认端口 8000，界面与 `/api` 同一个端口；旧版编辑器仍可用 `./src/start_server.sh --app html` 打开 `/STL_generate.html`。不要再用 `open STL_generate.html` 或 `python -m http.server`，第三方库也已全部随仓库提供，不走 CDN。
> - 登录使用启动器本地模式的凭据文件 `src/server/auth-local/admin_credentials`，或环境变量 `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`。
> - 工程文件是 `.cfdproj` v4/v5，导出物是多区域 ASCII STL 加 `.cfdrun` 清单（写到 `case/<名称>/model/`），由服务器交给 `STLGenRunner` 网格与求解。第 8 节与第 14 节描述的 `cfd_export.json` + ZIP 工程包对接属于已退役的路径。
> - 默认网格引擎是 dcCartMesh（切割单元），不是 snappyHexMesh。
>
> 现行用法请看用户指南：[安装](../../docs/user-guide/installation.md)、[快速上手](../../docs/user-guide/quick-start.md)、[建模](../../docs/user-guide/modeling.md)、[后处理](../../docs/user-guide/post-processing.md)，以及 [React 编辑器说明](react/README.md)。

> **工具定位**：一款运行于浏览器的可视化多区域 ASCII STL 几何建模工具，专为 OpenFOAM / ParaView 热仿真工作流设计。用户可在 3D 视口中直接建立数据中心设备几何体，并为每个表面区域赋予 CFD 边界条件，最终一键导出满足 snappyHexMesh 规范的多区域 ASCII STL 文件和 CFD 工程包（JSON + STL ZIP）。
>
> 版本：V5.0（`STL_generate.html`，约 8600 行单文件）

---

## 目录

1. [快速启动](#1-快速启动)
2. [界面布局](#2-界面布局)
3. [模型类型与参数](#3-模型类型与参数)
4. [Patch（补丁）系统](#4-patch补丁系统)
5. [机架 U-Slot 与设备系统](#5-机架-u-slot-与设备系统)
6. [贯通空腔（漏风区域）系统](#6-贯通空腔漏风区域系统)
7. [导出格式详解](#7-导出格式详解)
8. [CFD 导出 JSON 规范（cfd_export.json）](#8-cfd-导出-json-规范cfd_exportjson)
9. [撤销 / 重做系统](#9-撤销--重做系统)
10. [项目保存与加载](#10-项目保存与加载)
11. [单位系统](#11-单位系统)
12. [快捷键与辅助工具](#12-快捷键与辅助工具)
13. [STL 固体命名规范总表](#13-stl-固体命名规范总表)
14. [与 DCTwin 的对接关系](#14-与-dctwin-的对接关系)

---

## 1. 快速启动

```bash
# 方式一：直接在浏览器打开（推荐 Chrome / Edge）
open STL_generate.html

# 方式二：通过本地 HTTP 服务器（解决部分跨域限制）
python -m http.server 8000
# 然后访问 http://localhost:8000/STL_generate.html
```

无需安装任何依赖，所有依赖库（Three.js r184、OrbitControls、STLLoader、polygon-clipping、three-csg-ts、JSZip）均通过 CDN 加载。

---

## 2. 界面布局

```
┌──────────────────────────────────────────────────────────┐
│  Toolbar（工具栏，可收起）                                  │
│  旋转/镜像/对齐/吸附/网格密度控制                            │
├─────────────┬────────────────────────┬───────────────────┤
│  Left Panel │    Center Viewport     │   Right Panel     │
│ （模型树）   │   （Three.js 3D 视口） │  （属性检查器）   │
│             │                        │                   │
│ ▸ 文件操作  │  OrbitControls 旋转    │  模型 / 面 /      │
│ ▸ 模型列表  │  背景网格              │  Patch / 设备     │
│ ▸ 导出按钮  │  视图立方体 (ViewCube) │  CFD 参数编辑     │
│             │                        │                   │
├─────────────┴────────────────────────┴───────────────────┤
│  Log Panel（日志面板，可收起）                              │
│  操作历史，带时间戳和分类标签                                │
└──────────────────────────────────────────────────────────┘
```

| 区域 | 默认宽度/高度 | 可调范围 |
|------|-------------|---------|
| Left Panel | 330 px | 260–680 px，可折叠 |
| Right Panel | 340 px | 280–760 px，可折叠 |
| Log Panel | 160 px | 60–400 px，可折叠 |
| Toolbar | 50 px | 可完全折叠 |

---

## 3. 模型类型与参数

系统支持 **7 种模型类型（kind）**：

### 3.1 Box（参数化长方体）

用于建模普通设备、热源、柜体外壳等任意长方体。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| W / D / H | 宽 × 深 × 高（米） | 0.6 × 0.4 × 1.8 |
| anchor | 坐标原点（左前下角） | (0,0,0) |
| orientation | 水平旋转角度（度） | 0 |
| transform.quaternion | 精确姿态四元数 | identity |
| opacity | 透明度 | 0.0（不透明） |

Box 模型具有 **6 个面（front/back/left/right/top/bottom）**，每个面可以独立添加 Patch，并可在右侧面板为 Patch 设置 CFD 物理参数（`fixedTemperature`、`heatPower` 等）。

**CFD 角色**：
- 默认无角色（`role: 'none'`）— 不导出到 CFD JSON
- 若添加了发热 Patch 则自动归入 `wallObjects` 分类

### 3.2 Rack（机架）

服务器机架模型，具有完整的 U-Slot 系统和设备槽管理。详见 [第 5 节](#5-机架-u-slot-与设备系统)。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| W / D / H | 机架外形尺寸（米） | 0.6 × 1.0 × 1.867 |
| unitCount | U 槽总数量 | 42 |
| unitHeight | 单个 U 槽高度（米） | 0.04445（1U） |
| slotWidthRatio | 槽口占机架宽度比例 | 0.8 |
| safeMarginTop | 顶部安全边距（米） | 0.04445 |
| safeMarginBottom | 底部安全边距（米） | 0.04445 |

### 3.3 ACU（空调机组）

精密空调模型，支持在任意面上添加 **送风 Patch（supply）** 和 **回风 Patch（return）**，每个 Patch 可以独立设置温度和流量。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| W / D / H | 外形尺寸（米） | 1.2 × 0.6 × 2.0 |
| anchor | 坐标原点 | (0,0,0) |
| 送风 Patch 物理参数 | temperature（℃），volumeFlowRate（m³/s），velocity（m/s） | 15°C，4.0 m³/s，2.0 m/s |
| 回风 Patch 物理参数 | volumeFlowRate，velocity（无温度） | 4.0 m³/s，2.0 m/s |

**多面多 Patch 支持**：一台 ACU 可以同时具有多个送风面和多个回风面，每个面的物理参数独立设置。所有送风 Patch 导出为 `supplyFaces[]` 数组，回风 Patch 导出为 `returnFaces[]` 数组。

### 3.4 Wall（墙面）

平面墙体，厚度为 0，用于建立房间边界。

| 参数 | 说明 |
|------|------|
| W / H | 墙面宽度和高度（米） |
| anchor | 墙面左下角坐标 |
| orientation | 墙面朝向角度（度） |

Wall 模型的 `stlFile` 在 CFD JSON 中为 `null`（不生成 STL 几何），仅提供几何参考。

### 3.5 Baffle（隔板）

平面隔板，厚度为 0，用于建立内部气流障碍物或穿孔板。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| W / H | 隔板尺寸（米） | — |
| baffleType | 类型：`'wall'`（实心）或 `'perforated'`（穿孔） | `'wall'` |
| porosity | 孔隙率（0=全封闭，1=全开放） | 0.0 |

### 3.6 Floor（地板 / 天花板）

水平平面，高度为 0，用于建立高架地板、穿孔砖等。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| W / D | 平面尺寸（米） | — |
| defaultBC | 默认边界条件类型 | `'wall'` |
| openings | 穿孔开口数组（每个开口含 solidName、porosity） | [] |

### 3.7 Custom（自定义导入 STL）

从外部 STL 文件导入任意几何体，支持在曲面上进行水转印（投影 Patch）操作。

| 参数 | 说明 |
|------|------|
| customSTLBase64 | 导入 STL 文件的 Base64 编码（创建后不可修改） |
| customOffset | 几何体相对锚点的偏移量 |
| solidRegions | STL 中检测到的所有 solid 名称列表 |

**水转印（Water Transfer Printing）**：
在 Custom 模型上添加的 Patch 通过三维投影光柱（圆柱体/长方体）沿指定方向投影到曲面上，并通过 CSG Boolean 运算提取出独立的 STL solid 区域。投影参数：

| Patch 参数 | 说明 |
|------------|------|
| px/py/pz | Patch 中心三维坐标（米） |
| rx/ry/rz | 投影方向旋转角（度） |
| vx/vy/vz | 投影方向向量（默认为面法向） |
| depth | 投影深度（米） |
| applied | 是否已执行 CSG 布尔运算（true/false） |

---

## 4. Patch（补丁）系统

Patch 是附着在模型面上的独立区域，每个 Patch 在 STL 中导出为一个独立的 `solid` 块，可独立赋予 CFD 边界条件。

### 4.1 Patch 形状

| 类型 | 参数 | 说明 |
|------|------|------|
| 矩形（`rect`） | x, y（左下角），w, h（宽高），单位：米 | 适用于所有模型 |
| 圆形（`circle`） | cx, cy（圆心），r（半径），单位：米 | 适用于参数化模型 |

### 4.2 Patch 属性结构

```javascript
{
    id: 'p_1',               // 内部唯一 ID（不可见）
    type: 'rect' | 'circle', // 形状类型
    name: 'Intake_Port',     // 用户可见名称（导出为 solid 名）
    color: '#4daafc',        // 显示颜色（调色板选择）

    // 矩形参数
    x: 0.0, y: 0.0,         // 左下角（相对面坐标）
    w: 0.4, h: 0.3,         // 宽高（米）

    // 圆形参数
    cx: 0.3, cy: 0.5,       // 圆心（相对面坐标）
    r: 0.15,                // 半径（米）

    // CFD 物理参数（右侧面板设置）
    cfd: {
        role: 'supply' | 'return' | 'wall' | 'none',  // 对 ACU 有效
        temperature: 15.0,       // ACU 送风温度（℃）
        volumeFlowRate: 4.0,     // 风量（m³/s）
        velocity: 2.0,           // 风速（m/s）
        fixedTemperature: 45.0,  // 固定温度（℃），用于 Box/Baffle/Floor
        heatPower: 0.0,          // 发热功率（W），用于 Box/WallObject
    }
}
```

### 4.3 Patch 命名与 STL solid 名

Patch 导出为 STL solid 时的命名规则（见 [第 13 节](#13-stl-固体命名规范总表)）：

- **参数化模型（Box/Wall/Baffle/Floor）**：`{面名称前缀}_{patch.name}` 若 `face.prefixEnabled = true`，否则直接使用 `patch.name`
- **ACU 模型**：直接使用 `patch.name`（无面前缀，无模型名前缀）
- **Custom 模型**：直接使用 `patch.name`

所有名称经过 `sanitizeName()` 处理：非字母数字字符全部替换为下划线 `_`。

### 4.4 安全约束

- Patch 自动保留相对面边界 10% 的安全边距
- 超出边界时自动裁剪并显示视觉警告
- Patch 可通过拖拽调整位置和大小（带面内吸附）

---

## 5. 机架 U-Slot 与设备系统

### 5.1 U-Slot 结构

机架正面/背面被分割为 `unitCount` 个水平 U-Slot（行）。每行高度为 `unitHeight = 0.04445m（1U）`。前后面各有 `unitCount` 个槽位，可独立删除：

```
deletedFaces: {
    front: [false, false, true, true, ...],  // 前面槽位删除状态
    back:  [false, false, true, true, ...]   // 后面槽位删除状态
}
```

槽口宽度 = 机架宽 × `slotWidthRatio`，居中放置（两侧保留竖向边框）。

### 5.2 设备类型

#### 5.2.1 fanServer（主动散热服务器）

- 前后面对应 U-Slot 均被删除，形成**贯通风道**
- 生成两个 STL solid：
  - `{rackName}_{serverName}_inlet`（前面开口）
  - `{rackName}_{serverName}_outlet`（后面开口）
- CFD 物理参数：`volumeFlowRate`（m³/s）、`power`（W）

```json
{
    "type": "fanServer",
    "inletSolidName": "Rack_A_Server_01_inlet",
    "outletSolidName": "Rack_A_Server_01_outlet",
    "physics": {
        "volumeFlowRate": 0.5,
        "power": 1000,
        "fixedTemperature": null,
        "fixedTempFace": null
    }
}
```

#### 5.2.2 nonFan（被动/固定温度设备）

- 设备体积封闭，前后面不贯通
- 生成 **4 个 STL solid**：
  - `{rackName}_{serverName}_nonfan_front`（前面板）
  - `{rackName}_{serverName}_nonfan_back`（后面板）
  - `{rackName}_{serverName}_nonfan_top`（顶板）
  - `{rackName}_{serverName}_nonfan_bottom`（底板）
- CFD 物理参数：`fixedTemperature`（℃）、`fixedTempFace`（`'inlet'` 或 `'outlet'`，指定哪个面为固定温度面）

```json
{
    "type": "nonFan",
    "inletSolidName": "Rack_A_Device_A_nonfan_front",
    "outletSolidName": "Rack_A_Device_A_nonfan_back",
    "topSolidName": "Rack_A_Device_A_nonfan_top",
    "bottomSolidName": "Rack_A_Device_A_nonfan_bottom",
    "physics": {
        "volumeFlowRate": null,
        "power": null,
        "fixedTemperature": 35.0,
        "fixedTempFace": "inlet"
    }
}
```

### 5.3 隔板（Partitions）

相邻 U-Slot 之间的水平隔板，默认自动生成。设备占用 N 个 U-Slot 时，设备范围内的隔板自动删除。

---

## 6. 贯通空腔（漏风区域）系统

### 6.1 什么是贯通空腔

当机架某连续范围的 U-Slot **前面** 和 **后面** 均被删除时（`deletedFaces.front[i] = true` 且 `deletedFaces.back[i] = true`，且无设备占用），这些 U-Slot 形成一个贯通空腔（Through-Cavity）。

贯通空腔用于模拟机架中未安装设备的空位，空气可以从前面穿透到后面（即"漏风"现象）。

### 6.2 如何创建贯通空腔

1. 选中一个 U-Slot 行（在右侧 inspector 中）
2. 点击 **"同步删除对侧面"** 按钮 → 同时删除该 U-Slot 的前面和后面
3. 连续多个 U-Slot 都执行此操作后，会自动形成一个贯通空腔组（Group）
4. 在 3D 视口中，贯通空腔以**半透明橙色**六面体可视化

### 6.3 贯通空腔的 σ 系数

每个贯通空腔组有独立的漏风系数 σ（sigma）：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| sigma | 漏风孔隙率系数（0 ~ 1） | 0.05 |

在右侧 inspector 中选中贯通空腔后可直接编辑 σ 值。σ 值按组存储（`rack.throughSlotSigma[groupStartUnit]`）。

### 6.4 溶解贯通空腔

选中贯通空腔后，点击右侧 inspector 中的 **"溶解贯通区域"** 按钮，将同时恢复该组所有 U-Slot 的前面和后面（撤销一个 Undo 检查点）。

### 6.5 STL 导出

每个贯通空腔组导出为一个独立的**封闭六面体 STL solid**（6 个面，12 个三角形），所有法向量朝外（指向 solid 外部）。

**Solid 命名规则**：

```
{sanitizeName(rackName)}_leakage_{startUnit}_{endUnit}
```

**示例**：机架 `Rack A`（sanitized 为 `Rack_A`），贯通区域从 U12 到 U19：

```
solid Rack_A_leakage_12_19
  facet normal 0.0 -1.0 0.0
    ...
  endfacet
  ...（共 12 个 facet）
endsolid Rack_A_leakage_12_19
```

**几何边界**：
- X: `offsetX` 到 `offsetX + slotWidth`（槽口区域，与机架正面设备槽对齐）
- Y: `0` 到 `rackDepth`（机架全深度）
- Z: `startUnit × unitHeight` 到 `(endUnit + 1) × unitHeight`

贯通空腔 STL solid 包含在机架 STL 文件中（分组导出时在 `merged_rack.stl` 里）。

### 6.6 CFD JSON 中的漏风配置

```json
"leakage": {
    "enabled": true,
    "throughSlots": [12, 13, 14, 15, 16, 17, 18, 19],
    "groups": [
        {
            "startUnit": 12,
            "endUnit": 19,
            "sigma": 0.05,
            "stlSolidName": "Rack_A_leakage_12_19"
        }
    ]
}
```

---

## 7. 导出格式详解

### 7.1 导出按钮概览

| 按钮 | 行为 |
|------|------|
| 导出 CFD 流形拓扑 STL | 弹出对话框，选择模型和导出选项 |
| 导出 CFD 工程包 (JSON+STL) | 无对话框，直接导出 ZIP（全选所有模型 + 分组 + JSON） |
| 单模型导出（模型树右键） | 导出单个模型为一个 STL 文件 |

### 7.2 导出对话框（流形拓扑 STL）

弹出对话框提供：
- **模型勾选框**：每个模型独立勾选（默认与模型树可见状态一致）
- **全选 / 全不选**
- **包含漏风区域**：是否将贯通空腔 STL solid 一并导出
- **分组导出**：勾选后按类型分别下载多个文件（见下）

### 7.3 分组导出（Grouped Export）

按模型类型生成独立文件，每个文件内可含多个 `solid...endsolid` 块：

| 文件名 | 包含模型类型 |
|--------|-------------|
| `merged_rack.stl` | 所有机架（含贯通空腔 solid） |
| `merged_acu.stl` | 所有 ACU |
| `merged_baffle.stl` | 所有隔板 |
| `merged_floor_ceiling.stl` | 所有地板/天花板 |
| `merged_box.stl` | 所有 Box 模型 |
| `room_wall.stl` | 所有 Wall 墙面 |

**注意**：分组导出时，**所有在导出对话框中勾选的模型都会被导出**，无论其在模型树中是否处于启用（Enabled）状态。被禁用的模型会临时渲染以提取正确几何和位置信息。

### 7.4 CFD 工程包 ZIP 结构

点击 **"导出 CFD 工程包 (JSON+STL)"** 按钮，直接导出包含**所有模型**（无论模型树可见状态）的完整工程包：

```
{projectName}_cfd.zip
├── merged_rack.stl
├── merged_acu.stl
├── merged_baffle.stl
├── merged_floor_ceiling.stl
├── merged_box.stl
├── room_wall.stl
└── cfd_export.json
```

### 7.5 ASCII STL 文件格式

```text
solid {solid_name_1}
  facet normal nx ny nz
    outer loop
      vertex x1 y1 z1
      vertex x2 y2 z2
      vertex x3 y3 z3
    endloop
  endfacet
  ...
endsolid {solid_name_1}

solid {solid_name_2}
  ...
endsolid {solid_name_2}
```

关键规范：
1. **必须是 ASCII 格式**（Binary STL 无法携带命名信息）
2. **水密性（Watertight）**：相邻区域共享精确相同的顶点坐标
3. **法向量朝外**：所有三角面遵循右手定则，法向指向流体域
4. 同一 STL 文件中可含任意数量的 `solid...endsolid` 块

---

## 8. CFD 导出 JSON 规范（cfd_export.json）

### 8.1 顶层结构

```json
{
    "version": "1.0",
    "source": "frontend",
    "projectName": "Data_Center_A",
    "room": {
        "height": 3.6,
        "plane": [
            {"x": 0, "y": 0, "z": 0},
            {"x": 20, "y": 0, "z": 0},
            {"x": 20, "y": 10, "z": 0},
            {"x": 0, "y": 10, "z": 0}
        ],
        "locationInMesh": {"x": 10, "y": 5, "z": 1.8}
    },
    "racks": { ... },
    "acus": { ... },
    "wallObjects": { ... },
    "baffles": { ... },
    "floors": { ... },
    "walls": { ... },
    "meshConfig": { ... },
    "simulationConfig": { ... }
}
```

### 8.2 机架条目（racks）

```json
"Rack_A": {
    "stlFile": "merged_rack.stl",
    "leakageStlFile": "merged_rack.stl",
    "geometry": {
        "anchor": {"x": 2.0, "y": 3.0, "z": 0.0},
        "size": {"W": 0.6, "D": 1.0, "H": 1.867},
        "orientation": 0.0,
        "unitCount": 42,
        "unitHeight": 0.04445,
        "slotWidthRatio": 0.8
    },
    "servers": [
        {
            "id": "Rack_A_Server_01",
            "type": "fanServer",
            "startSlot": 5,
            "spanSlots": 2,
            "inletSolidName": "Rack_A_Server_01_inlet",
            "outletSolidName": "Rack_A_Server_01_outlet",
            "physics": {
                "volumeFlowRate": 0.5,
                "power": 1000,
                "fixedTemperature": null,
                "fixedTempFace": null
            }
        },
        {
            "id": "Rack_A_Device_A",
            "type": "nonFan",
            "startSlot": 10,
            "spanSlots": 1,
            "inletSolidName": "Rack_A_Device_A_nonfan_front",
            "outletSolidName": "Rack_A_Device_A_nonfan_back",
            "topSolidName": "Rack_A_Device_A_nonfan_top",
            "bottomSolidName": "Rack_A_Device_A_nonfan_bottom",
            "physics": {
                "volumeFlowRate": null,
                "power": null,
                "fixedTemperature": 35.0,
                "fixedTempFace": "inlet"
            }
        }
    ],
    "leakage": {
        "enabled": true,
        "throughSlots": [12, 13, 14, 15, 16, 17, 18, 19],
        "groups": [
            {
                "startUnit": 12,
                "endUnit": 19,
                "sigma": 0.05,
                "stlSolidName": "Rack_A_leakage_12_19"
            }
        ]
    },
    "wallSolidNames": []
}
```

### 8.3 ACU 条目（acus）

```json
"ACU_01": {
    "stlFile": "merged_acu.stl",
    "geometry": {
        "anchor": {"x": 0.0, "y": 5.0, "z": 0.0},
        "size": {"W": 1.2, "D": 0.6, "H": 2.0},
        "orientation": 90.0
    },
    "supplyFaces": [
        {
            "solidName": "Supply_Front_Low",
            "patchId": "p_1",
            "face": "front",
            "physics": {
                "temperature": 15.0,
                "volumeFlowRate": 4.0,
                "velocity": 2.0
            }
        },
        {
            "solidName": "Supply_Front_High",
            "patchId": "p_2",
            "face": "front",
            "physics": {
                "temperature": 12.0,
                "volumeFlowRate": 2.0,
                "velocity": 1.5
            }
        }
    ],
    "returnFaces": [
        {
            "solidName": "Return_Back",
            "patchId": "p_3",
            "face": "back",
            "physics": {
                "volumeFlowRate": 6.0,
                "velocity": 2.5
            }
        }
    ],
    "wallSolidNames": ["ACU_01_front_wall", "ACU_01_top", "ACU_01_bottom"]
}
```

> **注意**：一台 ACU 可以有多个 supplyFaces 和多个 returnFaces，每个 Patch 的温度和流量独立设置。这是 V5 的核心扩展能力。

### 8.4 WallObject 条目（wallObjects，Box 发热源）

```json
"Heater_Box_1": {
    "stlFile": "merged_box.stl",
    "geometry": {
        "anchor": {"x": 5.0, "y": 2.0, "z": 0.0},
        "size": {"W": 0.4, "D": 0.4, "H": 0.6},
        "orientation": 0.0
    },
    "heatPatches": [
        {
            "solidName": "heater_top_surface",
            "patchId": "p_10",
            "physics": {
                "heatPower": 0,
                "fixedTemperature": 45.0
            }
        }
    ],
    "wallSolidNames": ["Heater_Box_1_front", "Heater_Box_1_back", ...]
}
```

### 8.5 隔板条目（baffles）

```json
"Baffle_01": {
    "stlFile": "merged_baffle.stl",
    "geometry": {
        "anchor": {"x": 10.0, "y": 4.0, "z": 0.0},
        "size": {"W": 1.0, "H": 2.4},
        "orientation": 0.0
    },
    "physics": {
        "baffleType": "perforated",
        "porosity": 0.25
    }
}
```

### 8.6 地板条目（floors）

```json
"RaisedFloor_01": {
    "stlFile": "merged_floor_ceiling.stl",
    "geometry": {
        "anchor": {"x": 0, "y": 0, "z": -0.6},
        "size": {"W": 20.0, "D": 10.0}
    },
    "defaultBC": "wall",
    "openings": [
        {
            "solidName": "perforated_tile_A",
            "patchId": "p_20",
            "physics": {
                "type": "perforated",
                "porosity": 0.5
            }
        }
    ],
    "wallSolidNames": ["RaisedFloor_01_base"]
}
```

### 8.7 网格配置（meshConfig）

```json
"meshConfig": {
    "baseSize": 0.35,
    "refinementLevel": [1, 1],
    "rackRefinementLevel": [2, 3],
    "serverFaceRefinementLevel": [2, 3],
    "baffleRefinementLevel": [1, 3],
    "parallelMesh": true,
    "meshProcess": 16
}
```

### 8.8 仿真配置（simulationConfig）

```json
"simulationConfig": {
    "endTime": 500,
    "writeInterval": 100,
    "solveProcess": 1
}
```

---

## 9. 撤销 / 重做系统

### 9.1 机制

- **完整状态快照**：每次操作前对整个 `state` 对象执行 `JSON.parse(JSON.stringify(state))` 深拷贝
- **撤销栈**：最大 200 条，超出时自动丢弃最旧的快照
- **重做栈**：执行新操作时清空

### 9.2 快捷键

| 操作 | 快捷键 |
|------|--------|
| 撤销 | Ctrl+Z（Mac: Cmd+Z） |
| 重做 | Ctrl+Y 或 Ctrl+Shift+Z |

### 9.3 Checkpoint 规则（开发规范）

| 规则 | 说明 |
|------|------|
| 状态变更函数必须首先调用 `pushUndoCheckpoint()` | 在任何 mutation 之前 |
| 拖拽操作：每次拖拽一个 Checkpoint | 不是每帧一个 |
| 预览/滑块：首次交互时一个 Checkpoint | 后续滑动不再打点 |
| 仅改变选中状态的函数：不打 Checkpoint | |

---

## 10. 项目保存与加载

### 10.1 保存（`.cfdproj`）

点击左侧面板的 **"保存"** 按钮，将整个工程序列化为 JSON 并下载：

```
V5_CFD_Station_{timestamp}.cfdproj
```

`.cfdproj` 文件包含：
- 所有模型的完整参数（`state.models[]`）
- 所有自定义 STL 的 Base64 编码（`customSTLBase64`）
- 全局变换状态、单位设置等

### 10.2 加载（`.cfdproj`）

点击 **"加载"** 按钮，选择 `.cfdproj` 文件恢复工程：
- 恢复所有模型和参数
- 从 Base64 重建几何缓存
- 清空重做栈

### 10.3 导入自定义 STL

点击 **"导入 STL"** 按钮，导入外部 STL 文件创建 Custom 模型：
- 支持 ASCII 和 Binary STL
- 自动检测所有命名 solid 区域
- 自动计算包围盒并缩放到合适尺寸

---

## 11. 单位系统

所有内部计算使用 **SI 单位（米）**。用户界面通过 `state.inputUnit` 切换显示单位：

| 单位 | 换算 | 输入步长 | 小数位 |
|------|------|---------|--------|
| mm | × 0.001 | 1 mm | 1 位 |
| cm | × 0.01 | 0.1 cm | 2 位 |
| m（默认） | × 1 | 0.01 m | 3 位 |

1U 机架高度：44.45 mm（`RACK_DEFAULT_UNIT_HEIGHT = 0.04445 m`）

---

## 12. 快捷键与辅助工具

### 12.1 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+Z | 撤销 |
| Ctrl+Y / Ctrl+Shift+Z | 重做 |
| Delete / Backspace | 删除选中模型 |
| Escape | 取消选中 |

### 12.2 吸附模式（三选一）

| 模式 | 说明 |
|------|------|
| 面吸附（Face Snap） | 拖拽时自动对齐模型面 |
| 边吸附（Edge Snap） | 拖拽时自动对齐模型边 |
| 网格吸附（Grid Snap） | 拖拽时吸附到背景网格点 |

### 12.3 全局变换

| 操作 | 功能 |
|------|------|
| 绕 X/Y/Z 轴旋转 90° | 整体旋转所有选中模型 |
| X/Y 方向镜像 | 整体镜像 |
| 重置变换 | 清除所有全局变换 |

### 12.4 背景网格

- 可切换显示/隐藏（默认显示）
- 网格密度：10–1000 mm（默认 100 mm）
- 投影于 Z=0 平面

---

## 13. STL 固体命名规范总表

所有名称经过 `sanitizeName()` 处理（非字母数字字符 → 下划线）。

### 机架模型（kind: 'rack'）

| 内容 | STL Solid 名称格式 | 示例 |
|------|--------------------|------|
| fanServer 进风面 | `{rackName}_{serverName}_inlet` | `Rack_A_Server_01_inlet` |
| fanServer 出风面 | `{rackName}_{serverName}_outlet` | `Rack_A_Server_01_outlet` |
| nonFan 前面 | `{rackName}_{serverName}_nonfan_front` | `Rack_A_Device_A_nonfan_front` |
| nonFan 后面 | `{rackName}_{serverName}_nonfan_back` | `Rack_A_Device_A_nonfan_back` |
| nonFan 顶面 | `{rackName}_{serverName}_nonfan_top` | `Rack_A_Device_A_nonfan_top` |
| nonFan 底面 | `{rackName}_{serverName}_nonfan_bottom` | `Rack_A_Device_A_nonfan_bottom` |
| **贯通空腔（漏风区域）** | **`{rackName}_leakage_{startUnit}_{endUnit}`** | **`Rack_A_leakage_12_19`** |
| 机架框架壁面 | `{rackName}_wall_{side}_{slotIndex}` | `Rack_A_wall_front_3` |
| 风道内壁 | `{rackName}_wall_channel_{serverName}_{side}` | `Rack_A_wall_channel_Server_01_left` |
| 隔板 | `{rackName}_partition_{n}` | `Rack_A_partition_5` |

### ACU 模型（kind: 'acu'）

| 内容 | STL Solid 名称格式 | 示例 |
|------|--------------------|------|
| 送风 Patch | `{patchName}` | `Supply_Front_Low` |
| 回风 Patch | `{patchName}` | `Return_Back` |
| 壁面 | `{patchName}` 或面名 | `ACU_01_top` |

### Box 模型（kind: 'box'）

| 内容 | STL Solid 名称格式 | 示例 |
|------|--------------------|------|
| 含 Patch 的面（有前缀） | `{faceName}_{patchName}` | `front_heater_surface` |
| 含 Patch 的面（无前缀） | `{patchName}` | `heater_surface` |
| 无 Patch 的面 | `{modelName}_{faceName}` | `Box_01_back` |

### Baffle / Floor / Wall

| 内容 | STL Solid 名称格式 | 示例 |
|------|--------------------|------|
| 主体面 | `{modelName}_{faceName}` | `Baffle_01_front` |
| 开口 Patch | `{faceName}_{patchName}` | `top_Perforated_A` |

### Custom 模型（kind: 'custom'）

| 内容 | STL Solid 名称格式 | 示例 |
|------|--------------------|------|
| 主体 solid | `{modelName}` | `Imported_ACU` |
| 投影 Patch | `{patchName}` | `Supply_Port_1` |

---

## 14. 与 DCTwin 的对接关系

### 14.1 工作流

```
Visual STL Generator            DCTwin CFD Engine
┌─────────────────────┐         ┌────────────────────────────┐
│  可视化建模          │         │  自动化 OpenFOAM 流程       │
│                     │         │                            │
│  创建几何模型 ──────────────→ cfd_export.json            │
│  设置 CFD 参数       │  ZIP    │  ↓                         │
│                     │ ──────→ merged_rack.stl            │
│  导出 CFD 工程包     │         │  merged_acu.stl            │
└─────────────────────┘         │  ...                       │
                                │                            │
                                │  CFDProjectImporter        │
                                │  → room.json               │
                                │  → bridge_bc.json          │
                                │  → snappyHexMesh           │
                                │  → buoyantBoussinesq...    │
                                └────────────────────────────┘
```

### 14.2 DCTwin 解析的 JSON 字段

DCTwin 的 `cfdproj_importer.py` 读取 `cfd_export.json` 并生成：
- `model/room.json`：DCTwin 内部模型
- `model/bridge_bc.json`：补丁名 → 边界条件映射（BridgeBCRegistry）

关键字段映射：

| cfd_export.json 字段 | DCTwin 用途 |
|----------------------|------------|
| `racks[*].servers[*].inletSolidName` | OpenFOAM 进风口边界 |
| `racks[*].leakage.groups[*].stlSolidName` | snappyHexMesh cellZone |
| `racks[*].leakage.groups[*].sigma` | 孔隙介质 σ 系数 |
| `acus[*].supplyFaces[*]` | 送风口 fixedValue 边界 |
| `acus[*].returnFaces[*]` | 回风口 flowRate 边界 |
| `wallObjects[*].heatPatches[*].physics.fixedTemperature` | 固定温度壁面 |

### 14.3 leakageStlFile 字段

`leakageStlFile` 和 `stlFile` 在分组导出中均指向同一文件（`merged_rack.stl`），因为贯通空腔 solid 已包含在机架 STL 文件中。DCTwin 使用 `stlSolidName` 字段在该文件中定位具体 solid 名称。

---

*本文档由 Claude Code 自动生成，基于对 `STL_generate.html`（V5.0，约 8600 行）的完整代码分析。*
*最后更新：2026-03-18*
