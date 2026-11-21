# .cfdproj V4 字段参考（参考 dclib Room）

> **当前版本（2026-10-21）：** React 编辑器保存的是 **v5**：沿用本页 v4 的全部结构，只在顶层加一个 `transient` 块，并把可时变参数（服务器功率、ACU 送风温度与风量、冷板热负载、泵转速、阀开度等）从模型里移到 `transient.tracks` 的关键帧。所以读 v5 文件时，本页列出的这些字段在磁盘上不存在，值在 t = 0 关键帧里。差异、参数登记表和迁移规则见 [.cfdproj v5 字段参考](cfdproj-v5-schema.md)。旧 HTML 编辑器仍只读写 v4。

> **状态（2026-06-14 — 已实现并验收）：** 本文档已由设计稿升级为**当前格式字段参考**。编辑器现在原生读写 schemaVersion 4，本文与实现一致。权威结构源：
> - `src/frontend/shared/js/cfdproj_schema.js` —— 顶层键、`constructions` 分桶顺序（`BUCKETS`）、`BUCKET_BY_KIND`、`MESH_KEYS`/`SIM_KEYS` 拆分、`emptyDocument()` 骨架、`validateDocument()` 结构校验。
> - `src/frontend/shared/js/cfdproj_migrate.js` / `src/frontend/scripts/cfdproj_migrate.py` —— v2/v3 → v4 迁移（JS/Python 值对等，加载即迁移）。
> - `src/frontend/shared/js/cfdproj_state_v4.js` —— V4 内存态机（`allModels`、bucket API、`normalizeStateV4`、patchPhysics 辅助）。
>
> **每个 model 的规范形状：** `model.geometry.{location, size, quaternion, scale, faces, cutouts}` 为唯一真值；物理参数挂在 `geometry.faces[*].patches[*].physics`；模型按类别分桶在 `constructions.{racks, acus, baffles, floors, walls, …}`；`uid` 即原 `id`（`m_N`）。下文字段树与字段表即按此实现给出。Plan 4 已批量把 20 个 tracked 项目转为 V4（`case/Slab_dataset/*` 按设计保留 v2）。
>
> **复核（2026-06-14）：** 本文已对照 `cfdproj_schema.js` / `cfdproj_migrate.js` / `cfdproj_state_v4.js` 逐字段复核，并修正了 3 处偏差：① `base{W,H,D}→geometry.size{x,y,z}` 轴向映射（原误写为 `base{W,D,H}`）；② Rack `cooling` 字段表补齐 `liquidManifold`/`blankingPanelType`/`leakageEnabled`/`leakageExtra`/`unboundPatchPhysics`，删除幻影字段 `hasBlankingPanel`，`defaultLeakageSigma` 默认 `0.05`；③ `meshConfig` 示例键名改用真实键 `meshBaseSize`。

本文档描述 `.cfdproj` V4 的字段结构。这里不保留完整 JSON Schema，而是用字段树和字段表说明每类对象的含义，便于后续设计评审、前端保存格式设计和 `.cfdrun` 导出器实现。

V4 参考 `dclib.room.Room` 的组织方式：房间是顶层对象，设备和构件按类别放在 `constructions`，可复用模板放在 `models`。本次仿真的运行输入属于 `.cfdrun`，不属于 `.cfdproj`。

V4 不在 `.cfdproj` 中保存 OpenFOAM parser 的规则库，也不保存本次运行输入。`.cfdproj` 应先用于导出 STL 几何，再与 `.cfdrun.inputs`、网格/求解配置一起生成 `.cfdrun`。OpenFOAM Parser 只消费 `.cfdrun`，并由 `.cfdrun` 生成最终 OpenFOAM case。

```text
CfdProjectV4
  ├── schemaVersion
  ├── uid
  ├── name
  ├── meta
  ├── geometry
  ├── models
  ├── constructions
  ├── instrumentation
  └── runDefaults
```

核心原则：**`.cfdproj` 描述可编辑的房间和设备领域模型；STL 导出器从 `.cfdproj` 生成几何文件；`.cfdrun` 记录本次运行输入、STL artifact、patch 绑定和任务配置；OpenFOAM Parser 只读取 `.cfdrun` 来生成完整 case。**

文件角色契约（2026-06-12 修订）：

- **`.cfdproj` 是用户显式保存的文档。** 只有用户点击保存时才写盘，保存的是可编辑的领域模型和可编辑的运行默认配置（`runDefaults`）。OpenFOAM Runner 永远不读取 `.cfdproj`。
- **`.cfdrun` 是触发运行时由前端从当前实时 UI 状态生成的冻结快照。** 用户在前端修改了某个 server 功率但没有点保存时，导出的 `.cfdrun` 仍然反映该修改，而 `.cfdproj` 不变。`.cfdrun` 在每次导出/网格/求解时重新生成。

---

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schemaVersion` | `integer` | 文件格式版本：v4 为 `4`；带 `transient` 块的 v5 为 `5`（见 [v5 参考](cfdproj-v5-schema.md)）。 |
| `uid` | `string` | 项目或房间唯一 ID，用于跨文件引用和稳定 diff。 |
| `name` | `string` | 项目或房间名称。 |
| `meta` | `RoomMeta` | 项目元信息，例如导出名、单位、case 路径。 |
| `geometry` | `RoomGeometry` | 房间主体几何，例如高度和平面轮廓。 |
| `models` | `Models` | 可复用模型库，参考 dclib `Models`。这里存模板和额定模型，不代表实例存在。 |
| `constructions` | `RoomConstructions` | 房间内真实存在的设备、墙体、地板、管路、传感器等实例。 |
| `instrumentation` | `Instrumentation` | probe、传感器测点和外部测量值。 |
| `runDefaults` | `RunDefaults` | 可编辑的运行默认配置（mesh/solver/液冷求解设置）。保存的是用户在面板中编辑的默认值；导出 `.cfdrun` 时被冻结为本次运行配置。Runner 不直接读取该字段。 |

前端编辑器状态，例如相机、选中对象、折叠面板、临时 UI 设置，不属于 `.cfdproj`（2026-06-12 修订：不使用 sidecar 文件，UI 偏好按项目 `uid` 持久化到浏览器 `localStorage`）。

---

## 2. Room 级字段

### 2.1 `meta`

```text
meta
  ├── projectName
  ├── exportFileName
  ├── description
  ├── inputUnit
  └── casePath
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `projectName` | `string` | 项目显示名。 |
| `exportFileName` | `string` | 导出 `.cfdrun`、STL、case 目录时使用的基础文件名。 |
| `description` | `string` | 项目说明，给人阅读，不参与编译。 |
| `inputUnit` | `string` | UI 输入解析单位（`m`/`mm` 等）。仅影响前端输入框换算；文件内所有坐标和尺寸始终以米存储。 |
| `casePath` | `string` | 关联的本地 case 路径。 |

### 2.2 `geometry`

```text
geometry
  ├── height
  ├── plane[]
  │   └── { x, y, z }
  ├── tileSize
  ├── temperature
  └── bounds
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `height` | `number` | 房间高度，单位 m。 |
| `plane` | `Vertex[]` | 房间平面多边形顶点。参考 dclib `RoomGeometry.plane`。**本项目约定（2026-06-12 修订）：Y-up，平面顶点由 `x/z` 表示，`y=0`，单位米。** |
| `tileSize` | `number` | 地板网格或地砖尺寸，单位 m。dclib 默认思路为 0.6 m。 |
| `temperature` | `number` | 房间初始温度或环境温度，单位 C。 |
| `bounds` | `object` | 可选包围盒或导出裁剪范围。 |

---

## 3. 可复用模型库 `models`

`models` 保存可复用模板或额定模型。它类似 dclib `Models`，只表达数据中心领域内的几何、材料、冷却、功率和控制模型。它不表示房间里真的放置了某个设备；真实实例放在 `constructions`。

```text
models
  ├── curveModels
  ├── geometryModels
  ├── coolingModels
  ├── powerModels
  ├── controlModels
  └── materialModels
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `curveModels` | `object` | 性能曲线库，例如 ACU 回风温度-制冷量曲线、风机曲线、泵曲线、阀门曲线。 |
| `geometryModels` | `object` | 几何模板库，例如 rack 尺寸、server 尺寸、ACU 供回风面模板、pipe 规格。 |
| `coolingModels` | `object` | 冷却模型库，例如 ACU 额定制冷量、server 风扇模型、CDU 换热器模型、cold plate 模型。 |
| `powerModels` | `object` | 功率模型库，例如 server 额定功率、ACU 额定功耗、泵功耗。 |
| `controlModels` | `object` | 控制模型库，例如 ACU fixedTemp / linearCapacity / tableCapacity，阀门控制策略。 |
| `materialModels` | `object` | 材料模型，例如墙体导热系数、地板材料、管材粗糙度、液冷工质。 |

模型库填充策略（2026-06-12 修订）：除以下迁移来源外，其余 bucket **保留为空**（reserved），按需启用，不预先设计内容：

- v3 `model.racks` / `model.acus` 模板库 → `models.geometryModels.{racks, acus}`。
- v3 `liquidCooling.specifications.{coldPlateSpec, pipeSpec, pumpSpec, hxSpec, valveSpec}` → `models.coolingModels.{coldPlates, pipes, pumps, heatExchangers, valves}`。
- v3 `liquidCooling.fluid` → `models.materialModels.fluids`。

推荐子结构：

```text
geometryModels
  ├── boxes
  ├── racks
  ├── servers
  ├── acus
  ├── cdus
  ├── coldPlates
  ├── chips
  ├── pipes
  └── surfaces

coolingModels
  ├── servers
  ├── cdus
  ├── coldPlates
  ├── heatExchangers
  ├── fans
  ├── pumps
  └── pipes

powerModels
  ├── servers
  ├── cdus
  ├── chips
  ├── fans
  └── pumps
```

---

## 4. 设备和构件实例 `constructions`

`constructions` 是 V4 最重要的实例层，参考 dclib `RoomConstruction` 的分桶方式。每个分桶都推荐使用 `uid -> object` 字典，而不是数组。这样便于引用、合并、diff 和局部更新。

ID 策略（2026-06-12 修订）：**`uid` 即现有前端模型 `id`（`m_N` 形式），迁移时原样保留，`validId` 字段取消。** 这样 `.cfdproj` 内所有内嵌引用（`pipe.startPortId = "<uid>.supply"`、`valve.connectedRackId`、`fromModel` 等）跨版本迁移时无需改写。

```text
constructions
  ├── raisedFloor
  ├── falseCeiling
  ├── boxes
  ├── customMeshes
  ├── surfaces
  ├── walls
  ├── partitionWalls
  ├── baffles
  ├── floors
  ├── racks
  ├── rows
  ├── servers
  ├── acus
  ├── cdus
  ├── pipeSegments
  ├── pipeJunctions
  ├── pipes
  ├── sensors
  ├── liquidFlowNetworks
  └── heatGains
```

`raisedFloor` 与 `falseCeiling` 是单例槽位（`emptyConstructions()` 里初始化为 `null`），不在 `BUCKETS` 分桶注册表里；其余各项与 `cfdproj_schema.js` 的 `BUCKETS` 顺序一致。

| 字段 | 类型 | 说明 |
|---|---|---|
| `raisedFloor` | `Panel, null` | 架空地板整体对象。适合表达地板高度、开孔和默认边界类型。 |
| `falseCeiling` | `Panel, null` | 吊顶整体对象。 |
| `boxes` | `Record<uid, Box>` | 普通盒体、障碍物、简化设备外壳。 |
| `customMeshes` | `Record<uid, CustomMesh>` | 用户导入 STL 或其他自定义网格。 |
| `surfaces` | `Record<uid, Surface>` | 通用面对象，可作为墙面、开口、局部热边界的基础表示。 |
| `walls` | `Record<uid, Wall>` | 墙体实例，通常由 surface 派生。 |
| `partitionWalls` | `Record<uid, PartitionWall>` | 隔墙，支持开洞和泄漏率。 |
| `baffles` | `Record<uid, Baffle>` | 挡板或多孔挡板。 |
| `floors` | `Record<uid, Floor>` | 地板、天花或局部楼板。 |
| `racks` | `Record<uid, Rack>` | 机柜实例，可内嵌 server。 |
| `rows` | `Record<uid, object>` | 机柜行或排列组，用于布局语义和批量操作。 |
| `servers` | `Record<uid, Server>` | 独立服务器。机柜内服务器优先放在 `racks.*.constructions.servers`。 |
| `acus` | `Record<uid, ACU>` | 空调设备。 |
| `cdus` | `Record<uid, CDU>` | 液冷 CDU。 |
| `pipeSegments` | `Record<uid, PipeSegment>` | 液冷管段。 |
| `pipeJunctions` | `Record<uid, PipeJunction>` | 管路三通、四通、manifold 等节点。 |
| `pipes` | `Record<uid, object>` | 风侧管道（风管）对象，2026-09-09 加入（`kind: pipe`）。与液冷管段 `pipeSegments` 无关。 |
| `sensors` | `Record<uid, Sensor>` | 传感器或测点对象。 |
| `liquidFlowNetworks` | `Record<uid, object>` | 液冷网络拓扑，引用 CDU、pipe、cold plate、junction。 |
| `heatGains` | `object` | dclib 风格的房间热增益集合，可用于非设备热源。 |

---

## 5. 通用字段类型

### 5.1 `Vertex`

| 字段 | 类型 | 说明 |
|---|---|---|
| `x` | `number` | X 坐标，单位 m。 |
| `y` | `number` | Y 坐标，单位 m。**本项目为 Y-up：`y` 是高度方向（2026-06-12 修订，约定固定）。** |
| `z` | `number` | Z 坐标，单位 m。**房间平面采用 `x/z`（2026-06-12 修订，约定固定）。** |

### 5.2 `Size`

| 字段 | 类型 | 说明 |
|---|---|---|
| `x` | `number` | X 方向尺寸，单位 m。 |
| `y` | `number` | Y 方向尺寸，单位 m。 |
| `z` | `number` | Z 方向尺寸，单位 m。 |

### 5.3 `PoseGeometry`

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | `string, null` | 引用 `models.geometryModels` 中的几何模板 ID。 |
| `location` | `Vertex` | 对象基准点位置。具体基准点由对象类型约定，例如中心点、底面中心或左前下角。 |
| `orientation` | `number` | 平面旋转角，单位 degree。用于 2D 布局和普通设备朝向。可由 `quaternion` 派生，仅为便利字段。 |
| `quaternion` | `object` | 四元数姿态。**存在时为姿态的事实源（2026-06-12 修订）**：v3 迁移保留原 `transform.quaternion` 不做角度换算。 |
| `scale` | `Vertex` | 可选缩放系数。普通参数化设备不建议依赖 scale。 |

### 5.4 `PatchSurface`

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | surface 在所属设备内的 ID，例如 `supply`、`return`、`frontDoor`。 |
| `side` | `front, rear, left, right, top, bottom` | 该 surface 依附的设备面。 |
| `role` | `SurfaceRole` | CFD 语义，例如 `supply`、`return`、`serverInlet`、`serverOutlet`、`wallBaffle`。 |
| `width` | `number` | surface 宽度，单位 m。 |
| `length` | `number` | surface 长度，单位 m。 |
| `offset` | `Vertex` | 相对所属设备基准点或所属面的偏移。 |
| `solidName` | `string` | 导出 STL 时对应的 `solid <name>`。可由 compiler 自动生成，也可显式指定。 |
| `area` | `number` | surface 面积，单位 m2。可由几何计算得到。 |
| `perforation` | `object` | 可选开孔参数。只在该 surface 为多孔面、机柜门板、开孔地板或多孔挡板时填写。 |
| `perforation.shape` | `circle, rect, diamond` | 孔形状。 |
| `perforation.holeDiameterMm` | `number` | 单孔特征直径或等效直径，单位 mm。 |
| `perforation.openAreaPct` | `number` | 开孔率，百分比。 |

开孔不作为设备级字段保存，而是作为 `PatchSurface.perforation` 保存。原因是开孔率、孔径和孔型本质上属于某个 surface/patch，而不是整个设备。对于地板 opening、机柜门、挡板面、隔墙孔洞，parser 都应优先读取对应 surface 或 opening 上的 `perforation`。

### 5.5 `ObjectMeta`

（2026-06-12 修订：补充定义。原提案多处引用 `ObjectMeta` 但未定义。）

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 对象显示名。 |
| `description` | `string` | 对象说明。 |
| `tags` | `string[]` | 标签。 |
| `enabled` | `boolean` | 对象是否启用。禁用对象保留在文档中，是否参与导出由导出器按对象类型决定（与 v3 行为一致）。 |
| `display` | `object` | 文档级展示属性，例如 `opacity`、`showAnchorMarker`。相机/选中等会话级 UI 状态不在此列。 |

---

## 6. 设备字段定义

V4 统一使用 `cooling` 表达设备或构件的热管理相关物理属性。这里的 `cooling` 是广义概念，不只表示主动制冷设备，也包括发热对象的热边界、服务器散热、机柜通风、挡板/地板开孔、液冷管路流动阻力等。这样可以避免同一类散热语义分散在 `thermal`、`flow`、`cooling` 多个字段中。

### 6.1 `Box` 普通盒体

```text
Box
  ├── uid
  ├── geometry
  │   ├── model
  │   ├── location
  │   ├── orientation
  │   ├── quaternion
  │   ├── scale
  │   ├── size
  │   ├── surfaces[]
  │   └── cutouts[]
  ├── cooling
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 盒体实例 ID。 |
| `geometry.model` | `string, null` | 引用盒体几何模板。 |
| `geometry.location` | `Vertex` | 盒体位置。 |
| `geometry.orientation` | `number` | 平面旋转角。 |
| `geometry.quaternion` | `object` | 可选完整 3D 姿态。 |
| `geometry.scale` | `Vertex` | 可选缩放。 |
| `geometry.size` | `Size` | 盒体长宽高。 |
| `geometry.surfaces` | `PatchSurface[]` | 盒体上的特殊面，例如热通量面、固定温度面、开口面。 |
| `geometry.cutouts` | `object[]` | 几何切口或孔洞。 |
| `cooling.boundaryMode` | `adiabatic, fixedTemperature, heatFlux, power` | 盒体默认热边界模式。 |
| `cooling.fixedTemperatureC` | `number` | 固定温度，单位 C。 |
| `cooling.heatFluxWm2` | `number` | 热流密度，单位 W/m2。 |
| `cooling.totalPowerW` | `number` | 总发热功率，单位 W。 |
| `cooling.convectionCoeffWm2K` | `number` | 对流换热系数，单位 W/(m2 K)。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.2 `CustomMesh` 自定义网格

`CustomMesh` 继承 `Box` 的位置、姿态、冷却/热边界能力，并增加导入网格字段。

```text
CustomMesh
  ├── Box fields
  └── customMesh
      ├── stlBase64
      ├── offset
      └── solidRegions[]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `customMesh.stlBase64` | `string` | 导入 STL 的 base64 数据。创建后应视为不可变。 |
| `customMesh.offset` | `Vertex` | STL 原始坐标到项目坐标的偏移。 |
| `customMesh.solidRegions` | `string[]` | STL 内部已有 solid 区域名列表。用于 patch 识别和复用。 |

### 6.3 `Surface` / `Wall` 通用面和墙体

```text
Surface
  ├── uid
  ├── geometry
  │   ├── location
  │   ├── size
  │   ├── orientation
  │   └── openings[]
  ├── construction
  └── meta

Wall = Surface
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 面或墙体 ID。 |
| `geometry.location` | `Vertex` | 面的位置。 |
| `geometry.size` | `Size` | 面的尺寸。对二维面，可用某一维为厚度。 |
| `geometry.orientation` | `number` | 平面旋转角。 |
| `geometry.openings` | `Opening[]` | 面上的孔洞或开口。 |
| `construction` | `object` | 墙体材料、厚度、层结构等构造信息。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.4 `Floor` 地板/架空地板/天花

```text
Floor
  ├── uid
  ├── geometry
  │   ├── location
  │   ├── size
  │   ├── orientation
  │   └── openings[]
  ├── construction
  ├── cooling
  │   ├── defaultBoundaryType
  │   └── roomBoundaryLocation
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `cooling.defaultBoundaryType` | `wall, wallBaffle, perforated` | 未被 opening 覆盖的地板默认 CFD 边界语义。 |
| `cooling.roomBoundaryLocation` | `none, top, bottom` | 该 floor 是否对应房间上/下边界，用于 blockMesh 或 STL 编译。 |
| `geometry.openings[].uid` | `string` | opening ID。 |
| `geometry.openings[].location` | `Vertex` | opening 在 floor 局部或全局坐标中的位置。 |
| `geometry.openings[].size` | `Size` | opening 尺寸。 |
| `geometry.openings[].boundaryType` | `wall, wallBaffle, perforated` | opening 的边界类型。 |
| `geometry.openings[].perforation` | `object` | opening 的开孔参数，结构同 `PatchSurface.perforation`。 |

### 6.5 `Baffle` 挡板/多孔挡板

```text
Baffle
  ├── uid
  ├── geometry
  │   ├── location
  │   ├── size
  │   ├── orientation
  │   └── openings[]
  ├── construction
  ├── cooling
  │   └── type
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `cooling.type` | `wall, perforated` | 挡板类型。`wall` 表示不可穿透挡板，`perforated` 表示多孔挡板。 |
| `geometry.openings` | `Opening[]` | 挡板上的局部开口。开孔参数写在 opening 或 surface 的 `perforation` 字段。 |

### 6.6 `PartitionWall` 隔墙

```text
PartitionWall
  ├── uid
  ├── geometry
  │   ├── location
  │   ├── size
  │   ├── orientation
  │   └── openings[]
  ├── construction
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `geometry.openings[].uid` | `string` | 隔墙孔洞 ID。 |
| `geometry.openings[].location` | `Vertex` | 孔洞位置。 |
| `geometry.openings[].size` | `Size` | 孔洞尺寸。圆孔可使用 `x` 表示直径。 |
| `geometry.openings[].boundaryType` | `opening, hole, perforated` | 孔洞或开口类型。 |
| `geometry.openings[].perforation` | `object` | 孔洞为多孔面时填写，结构同 `PatchSurface.perforation`。 |
| `geometry.openings[].defaultLeakageRatePct` | `number` | 默认泄漏率百分比。运行输入可覆盖该值。 |

### 6.7 `Rack` 机柜

Rack 参考 dclib `Rack`：`geometry + constructions.servers + meta`。V4 增加 rack 级冷却和通风语义。

```text
Rack
  ├── uid
  ├── geometry
  │   ├── model
  │   ├── location
  │   ├── orientation
  │   ├── size
  │   ├── slot
  │   ├── firstSlotOffset
  │   ├── slotHeight
  │   ├── slotWidthRatio
  │   └── faces[]
  ├── constructions
  │   ├── servers
  │   ├── partitions[]
  │   └── rdhx
  ├── cooling
  │   ├── type
  │   ├── liquidManifold
  │   ├── blankingPanelType
  │   ├── leakageEnabled
  │   ├── defaultLeakageSigma
  │   ├── leakageExtra
  │   └── unboundPatchPhysics
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 机柜实例 ID。 |
| `geometry.model` | `string, null` | 引用 `models.geometryModels.racks` 的模板。 |
| `geometry.location` | `Vertex` | 机柜位置。 |
| `geometry.orientation` | `number` | 机柜朝向。 |
| `geometry.size` | `Size` | 机柜外形尺寸。 |
| `geometry.slot` | `integer` | U 位数量，例如 42U。 |
| `geometry.firstSlotOffset` | `number` | 第一个 U 位相对机柜基准点的偏移，单位 m。 |
| `geometry.slotHeight` | `number` | 单个 U 位高度，默认 0.04445 m。 |
| `geometry.slotWidthRatio` | `number` | server 模块相对机柜宽度的占比，用于 U-slot 几何生成。 |
| `geometry.faces` | `PatchSurface[]` | 机柜门板或特殊面，例如前门、后门、封板区域。 |
| `constructions.servers` | `Record<uid, Server>` | 机柜内服务器实例。 |
| `constructions.partitions` | `object[]` | 机柜内隔板、盲板或局部构件。 |
| `constructions.rdhx` | `RDHX, null` | 机柜后门换热器。RDHX 是 rack 的内部构件，不放在 `cooling` 中。 |
| `cooling.type` | `air, liquid, hybrid` | 机柜冷却类型。迁移默认 `air`。 |
| `cooling.liquidManifold` | `object, 缺省` | 液冷分集水器配置，仅在源数据带 `liquidManifold` 时写入。 |
| `cooling.blankingPanelType` | `string, 缺省` | 盲板类型（替代旧 `hasBlankingPanel`），仅在源数据显式给出时写入；决定未占用 U 位的盲板补全方式。 |
| `cooling.leakageEnabled` | `boolean` | 是否启用泄漏边界，迁移自 `cfd.leakage.enabled`。 |
| `cooling.defaultLeakageSigma` | `number` | 机柜默认泄漏系数，迁移自 `cfd.leakage.sigma`，缺省 `0.05`，范围建议 0 到 1。 |
| `cooling.leakageExtra` | `object, 缺省` | `cfd.leakage` 中除 `enabled`/`sigma` 外的剩余字段，原样保留。 |
| `cooling.unboundPatchPhysics` | `object, 缺省` | 未能绑定到具体 face/patch 的孤立 patch 物理参数兜底容器（`patchPhysics` 回退）。各 model 类别迁移时通用。 |
| `geometry.faces[].perforation` | `object` | 机柜门板或特殊面的开孔参数，结构同 `PatchSurface.perforation`。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.8 `Server` 服务器

Server 参考 dclib `Server` 的主要设备结构，但 V4 不保留 computing 模型。芯片和冷板是 server 内部构件，放入 `constructions`；纯风冷服务器可以让 `constructions` 为空。

```text
Server
  ├── uid
  ├── type
  ├── geometry
  │   ├── model
  │   ├── slotPosition
  │   ├── slotOccupation
  │   ├── slotHeight
  │   ├── orientation
  │   ├── depth
  │   ├── width
  │   ├── inletFace
  │   ├── outletFace
  │   └── faces[]
  ├── constructions
  │   ├── chips
  │   └── coldPlates
  ├── cooling
  │   ├── model
  │   ├── fanType
  │   ├── volumeFlowRate
  │   ├── volumeFlowRateRatio
  │   ├── liquidCooling
  │   ├── liquidCoolingPercentage
  │   └── pipeDiameter
  ├── power
  │   ├── model
  │   ├── ratedPowerW
  │   ├── ratedCpuPowerW
  │   ├── ratedMemPowerW
  │   └── ratedDiskPowerW
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 服务器实例 ID。 |
| `type` | `fanServer, nonFan` | `fanServer` 表示有进出风通道；`nonFan` 表示实体热源或无风扇设备。 |
| `geometry.model` | `string, null` | 引用 `models.geometryModels.servers` 的模板。 |
| `geometry.slotPosition` | `integer` | 在 rack 内的起始 U 位。 |
| `geometry.slotOccupation` | `integer` | 占用 U 位数量。 |
| `geometry.slotHeight` | `number` | 单 U 高度，默认 0.04445 m。 |
| `geometry.orientation` | `number` | 相对 rack 或房间的朝向。 |
| `geometry.depth` | `number` | 服务器深度，单位 m。 |
| `geometry.width` | `number` | 服务器宽度，单位 m。 |
| `geometry.inletFace` | `PatchSurface` | 服务器进风面。 |
| `geometry.outletFace` | `PatchSurface` | 服务器出风面。 |
| `geometry.faces` | `PatchSurface[]` | 其他表面，例如热通量面、封闭面。 |
| `constructions.chips` | `Record<uid, Chip>` | 服务器内部芯片或局部热源。纯风冷简化模型可为空。 |
| `constructions.coldPlates` | `Record<uid, ColdPlate>` | 服务器内部冷板。纯风冷场景可为空。**现行实现(2026-09-30)**:只由 v3→v4 迁移写过一次,之后无人写入;冷板的唯一列表是 `liquidCooling.cpCuPairs`(检查器、参数登记表、时间轴、旧 HTML 编辑器都写它),编译边 id 取 pair 的 `uid`。`coldPlates` 只用于给没有 uid 的旧 pair 找回迁移时的键,见 `src/frontend/react/src/liquid/cpCuPairIdentity.ts`。 |
| `cooling.model` | `string, null` | 引用 `models.coolingModels.servers`。 |
| `cooling.fanType` | `Fixed, Variable` | 风扇流量是否固定。 |
| `cooling.volumeFlowRate` | `number` | 额定或默认体积流量，单位 m3/s。 |
| `cooling.volumeFlowRateRatio` | `number` | 相对额定风量比例。 |
| `cooling.liquidCooling` | `boolean` | 是否启用液冷。 |
| `cooling.liquidCoolingPercentage` | `number` | 热量由液冷带走的比例，0 到 1。 |
| `cooling.pipeDiameter` | `number` | 服务器液冷管径，单位 m。 |
| `power.model` | `string, null` | 引用 `models.powerModels.servers`。 |
| `power.ratedPowerW` | `number` | 服务器额定功率，单位 W。 |
| `power.ratedCpuPowerW` | `number` | CPU 额定功率，单位 W。 |
| `power.ratedMemPowerW` | `number` | 内存额定功率，单位 W。 |
| `power.ratedDiskPowerW` | `number` | 磁盘额定功率，单位 W。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.9 `ACU` 空调设备

ACU 参考 dclib 的设备拆分思路，但不把 ACU 作为一个整体 cooling/power 模型。V4 将 ACU 表达为外壳几何加内部构件：热交换器和风机。制冷量、风量、压升、功耗等参数应分散到 `constructions.heatExchanger` 和 `constructions.fans` 中。

```text
ACU
  ├── uid
  ├── geometry
  │   ├── model
  │   ├── location
  │   ├── orientation
  │   ├── size
  │   ├── supplyFaces[]
  │   ├── returnFaces[]
  │   ├── extraFaces[]
  │   └── coilGeometry
  ├── constructions
  │   ├── heatExchanger
  │   └── fans
  ├── controller
  │   ├── model
  │   └── supportedModes[]
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | ACU 实例 ID。 |
| `geometry.model` | `string, null` | 引用 `models.geometryModels.acus`。 |
| `geometry.location` | `Vertex` | ACU 位置。 |
| `geometry.orientation` | `number` | ACU 朝向。 |
| `geometry.size` | `Size` | ACU 外形尺寸。 |
| `geometry.supplyFaces` | `object[]` | 送风面引用列表 `[{faceId, patchId}]`，物理参数在对应 `PatchSurface.physics` 上（2026-06-12 修订：实际设备存在多送风面，改为复数列表）。 |
| `geometry.returnFaces` | `object[]` | 回风面引用列表，结构同 `supplyFaces`（2026-06-12 修订）。 |
| `geometry.extraFaces` | `PatchSurface[]` | 额外面，例如侧送风、检修口、传感面。 |
| `geometry.coilGeometry` | `object` | 盘管几何或盘管位置描述。 |
| `constructions.heatExchanger` | `HeatExchanger` | ACU 内部热交换器。制冷量、水流量、进出水/空气设计温度和性能表放在该构件中。 |
| `constructions.fans` | `Record<uid, Fan>` | ACU 内部风机。风量、压升、风机功耗和风机曲线放在 fan 构件中。 |
| `controller.model` | `string, null` | 引用 `models.controlModels.acus`。 |
| `controller.supportedModes` | `string[]` | 支持的控制模式，例如 `fixedTemp`、`linearCapacity`、`tableCapacity`。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.10 `CDU` 冷量分配单元

CDU 参考 dclib `CDU`：`geometry + constructions + model + meta`。

```text
CDU
  ├── uid
  ├── geometry
  │   ├── model
  │   ├── location
  │   ├── orientation
  │   ├── size
  │   └── ports[]
  ├── constructions
  │   ├── heatExchanger
  │   ├── pump
  │   └── valve
  ├── model
  ├── meta
  │   ├── racks[]
  │   └── room
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | CDU 实例 ID。 |
| `geometry.model` | `string, null` | 引用 `models.geometryModels.cdus`。 |
| `geometry.location` | `Vertex` | CDU 位置。 |
| `geometry.orientation` | `number` | CDU 朝向。 |
| `geometry.size` | `Size` | CDU 外形尺寸。 |
| `geometry.ports` | `Port[]` | CDU 供回水接口。 |
| `constructions.heatExchanger` | `object` | CDU 内部换热器模型或引用。 |
| `constructions.pump` | `object` | CDU 内部泵模型或引用。 |
| `constructions.valve` | `object` | CDU 内部阀门模型或引用。 |
| `model` | `object` | CDU 综合模型，可引用 cooling/power/control 模型。 |
| `meta.racks` | `string[]` | 该 CDU 服务的 rack ID 列表。 |
| `meta.room` | `string` | 所属房间 ID。 |

### 6.11 `HeatExchanger` 换热器

`HeatExchanger` 是 ACU、CDU、RDHX 等设备内部的换热构件。它保存换热器自身的制冷/换热能力，不放在上层设备的整体 `cooling` 字段中。

```text
HeatExchanger
  ├── uid
  ├── model
  ├── geometry
  ├── cooling
  │   ├── coolingType
  │   ├── ratedCapacityKw
  │   ├── effectiveness
  │   ├── designWaterFlowRate
  │   ├── designAirFlowRate
  │   ├── designInletWaterTemperatureC
  │   ├── designInletAirTemperatureC
  │   ├── designOutletAirTemperatureC
  │   ├── performanceCurve
  │   └── performanceTable[]
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 换热器构件 ID。 |
| `model` | `string, null` | 引用 `models.coolingModels.heatExchangers`。 |
| `geometry` | `object` | 换热器在设备内部的几何或相对位置。 |
| `cooling.coolingType` | `string` | 换热器类型，例如 `CW`、`DX`、`airToLiquid`。 |
| `cooling.ratedCapacityKw` | `number` | 额定换热或制冷能力，单位 kW。 |
| `cooling.effectiveness` | `number` | 换热有效度，0 到 1。 |
| `cooling.designWaterFlowRate` | `number` | 设计水流量。 |
| `cooling.designAirFlowRate` | `number` | 设计空气流量，推荐单位 m3/s。 |
| `cooling.designInletWaterTemperatureC` | `number` | 设计进水温度，单位 C。 |
| `cooling.designInletAirTemperatureC` | `number` | 设计进风/回风温度，单位 C。 |
| `cooling.designOutletAirTemperatureC` | `number` | 设计出风/送风温度，单位 C。 |
| `cooling.performanceCurve` | `string, null` | 引用 `models.curveModels` 中的性能曲线。 |
| `cooling.performanceTable` | `object[]` | 性能表。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.12 `Fan` 风机

```text
Fan
  ├── uid
  ├── model
  ├── geometry
  ├── cooling
  │   ├── maximumFlowRate
  │   ├── designAirFlowRate
  │   ├── pressureRisePa
  │   └── performanceCurve
  ├── power
  │   └── ratedPowerW
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 风机构件 ID。 |
| `model` | `string, null` | 引用 `models.coolingModels.fans` 或风机曲线模型。 |
| `geometry` | `object` | 风机在设备内部的位置或几何。 |
| `cooling.maximumFlowRate` | `number` | 最大空气流量，推荐单位 m3/s。 |
| `cooling.designAirFlowRate` | `number` | 设计空气流量，推荐单位 m3/s。 |
| `cooling.pressureRisePa` | `number` | 风机压升，单位 Pa。 |
| `cooling.performanceCurve` | `string, null` | 风机性能曲线 ID。 |
| `power.ratedPowerW` | `number` | 风机额定功率，单位 W。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.13 `RDHX` 后门换热器

`RDHX` 是 rack 的内部构件，应放在 `Rack.constructions.rdhx` 中。

```text
RDHX
  ├── uid
  ├── geometry
  ├── heatExchanger
  ├── fans
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | RDHX 构件 ID。 |
| `geometry` | `BoxGeometry` | 后门换热器几何，通常贴附在 rack 后侧。 |
| `heatExchanger` | `HeatExchanger` | RDHX 内部换热器。 |
| `fans` | `Record<uid, Fan>` | RDHX 内部风机。无风机型可为空。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.14 `ColdPlate` 冷板

```text
ColdPlate
  ├── uid
  ├── geometry
  ├── cooling
  │   ├── model
  │   ├── chipTdpW
  │   ├── chipAreaM2
  │   ├── chipThicknessM
  │   └── thermalConductivityWmK
  ├── ports[]
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 冷板实例 ID。 |
| `geometry` | `BoxGeometry` | 冷板几何。 |
| `cooling.model` | `string, null` | 冷板冷却模型或对 `models.coolingModels.coldPlates` 的引用。 |
| `cooling.chipTdpW` | `number` | 对应芯片 TDP，单位 W。 |
| `cooling.chipAreaM2` | `number` | 芯片面积，单位 m2。 |
| `cooling.chipThicknessM` | `number` | 芯片厚度，单位 m。 |
| `cooling.thermalConductivityWmK` | `number` | 芯片或导热层导热系数，单位 W/(m K)。 |
| `ports` | `Port[]` | 冷板进出口。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.15 `Chip` 芯片/局部热源

```text
Chip
  ├── uid
  ├── geometry
  ├── cooling
  │   ├── ratedPowerW
  │   ├── chipAreaM2
  │   └── thermalMassJk
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 芯片或局部热源 ID。 |
| `geometry` | `BoxGeometry` | 芯片、局部热块或简化热面几何。 |
| `cooling.ratedPowerW` | `number` | 额定发热功率，单位 W。这里表示需要被冷却系统移除的热负载。 |
| `cooling.chipAreaM2` | `number` | 发热面积，单位 m2。 |
| `cooling.thermalMassJk` | `number` | 热容，单位 J/K。用于瞬态模型。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.16 `PipeSegment` 管段

```text
PipeSegment
  ├── uid
  ├── geometry
  │   ├── model
  │   ├── waypoints[]
  │   ├── diameterM
  │   └── ports[]
  ├── cooling
  │   ├── spec
  │   ├── roughnessM
  │   ├── loopRole
  │   └── valves[]
  ├── components
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 管段 ID。 |
| `geometry.model` | `string, null` | 引用 `models.geometryModels.pipes`。 |
| `geometry.waypoints` | `Vertex[]` | 管段折线路径，至少两个点。 |
| `geometry.diameterM` | `number` | 管径，单位 m。 |
| `geometry.ports` | `Port[]` | 管段端口。 |
| `cooling.spec` | `string` | 管路规格名称或引用 ID。 |
| `cooling.roughnessM` | `number` | 管壁粗糙度，单位 m。 |
| `cooling.loopRole` | `supply, return, unconnected` | 管段在液冷回路中的角色。 |
| `cooling.valves` | `object[]` | 管段上的阀门、局部阻力件。 |
| `components` | `object` | 弯头、阀门、过滤器等附加组件。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.17 `PipeJunction` 管路节点

```text
PipeJunction
  ├── uid
  ├── geometry
  │   ├── location
  │   └── orientation
  ├── cooling
  │   ├── type
  │   └── lossCoeff
  ├── connectedPipes[]
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 节点 ID。 |
| `geometry.location` | `Vertex` | 节点位置。 |
| `geometry.orientation` | `number` | 节点朝向。 |
| `cooling.type` | `T_branch, cross, manifold` | 节点类型。 |
| `cooling.lossCoeff` | `number` | 局部阻力系数。 |
| `connectedPipes` | `string[]` | 连接到该节点的 pipe segment ID。 |
| `meta` | `ObjectMeta` | 描述、标签等。 |

### 6.18 `Port` 设备或管路接口

```text
Port
  ├── uid
  ├── role
  ├── location
  ├── normal
  └── connectedTo
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 端口 ID。 |
| `role` | `supply, return, inlet, outlet, bidirectional` | 端口角色。 |
| `location` | `Vertex` | 端口位置。 |
| `normal` | `Vertex` | 端口法向。 |
| `connectedTo` | `string, null` | 连接对象 ID，例如另一端口、管段或设备。 |

### 6.19 `Sensor` 传感器/测点

```text
Sensor
  ├── uid
  ├── geometry
  │   └── location
  └── meta
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 传感器 ID。 |
| `geometry.location` | `Vertex` | 传感器位置。 |
| `meta` | `ObjectMeta` | 传感器名称、类型、标签、备注。 |

---

## 7. 观测与测量 `instrumentation`

```text
instrumentation
  ├── probes
  └── measurements
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `probes` | `Record<uid, Sensor>` | 仿真 probe 位置。可与 `constructions.sensors` 共用 Sensor 结构。 |
| `measurements` | `Record<uid, SensorMeasurement>` | 外部测量数据，例如温度、湿度，用于对比仿真结果。 |

---

## 8. 运行默认配置 `runDefaults`

（2026-06-12 修订：新增。原提案将 `simulation` 从 `.cfdproj` 中移除但未给编辑器的网格/求解设置安排持久化位置；用户保存项目时期望这些设置被保存。）

```text
runDefaults
  ├── meshConfig
  ├── simulationConfig
  ├── liquid
  │   ├── enabled
  │   ├── solver
  │   └── inheritResults
  └── hybridHeatBudget?  （显式声明时才存在）
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `meshConfig` | `object` | 网格默认配置（`meshBaseSize`、各 refinement level、`blockMeshBounds`、`refinementZones`、`locationInMesh` 等，完整键集见 `cfdproj_schema.js` `MESH_KEYS`），字段集与 `.cfdrun.meshConfig` 一致。 |
| `simulationConfig` | `object` | 求解默认配置（solverType、endTime、schemes、relaxationFactors、residualControl 等），字段集与 `.cfdrun.simulationConfig` 一致。 |
| `liquid.enabled` | `boolean` | 液冷模式开关。 |
| `liquid.solver` | `object` | 液冷求解器默认配置（dt、epochs、lr、device、weights 等，即 v3 `liquidCooling.solver`）。 |
| `liquid.inheritResults` | `boolean` | 风冷求解是否继承液冷结果（当前耦合路径已知失效，字段保留，修复另行安排）。 |
| `hybridHeatBudget` | `HybridHeatBudget?` | 可选的风液总 IT 功耗声明；按稳定机柜/服务器 UID 绑定，不能从既有液冷功率字段自动推断。风冷热边界须由已审计的液侧运行另行编译。 |

定位：`runDefaults` 是**可编辑的默认值**，不是运行事实。导出 `.cfdrun` 时由前端将其冻结为 `meshConfig`/`simulationConfig`，运行面板上的临时修改优先于 `runDefaults`。Runner 只读 `.cfdrun`，从不读取本字段。v3 `simulation` 与 `liquidCooling.{solver, enabled, inheritResults}` 迁移至此；v3 `liquidCooling.results` 是运行产物，不迁移。

### 8.1 可选风液功耗账本 `hybridHeatBudget`

这个声明为同工程风液热量耦合给出**总 IT 功耗的明确来源**。旧工程没有此字段仍然合法；`emptyDocument()` 不填默认账本，迁移和打开旧工程也不能把 `server.cooling.power` 或冷板 `Q_W` 当作总 IT 功耗。以下数值仅示范格式，并非 Shinaide 或 NVIDIA 现场参数：

```json
{
  "version": 1,
  "source": "mock",
  "source_reference": "example-synthetic-heat-budget-v1",
  "load_mapping": "shared_heat_multiplier",
  "air_surface_allocation": { "front_fraction": 0.5, "rear_fraction": 0.5 },
  "servers": {
    "rack_1/server_1": { "total_it_power_W": 1500 }
  }
}
```

该对象放在 `runDefaults.hybridHeatBudget`。`version` 固定为 `1`；`source` 仅允许 `mock`、`vendor`、`calibrated`；`source_reference` 必须是非空的来源标识，`mock` 也要写清依据，`vendor`/`calibrated` 值本身不证明已经收到或校准现场数据。`load_mapping` 当前只允许 `shared_heat_multiplier`，表示总 IT 功耗轨迹与液冷热负载轨迹使用同一已接受的无量纲倍率；**本字段本身不执行倍率计算或风冷导出**。

`air_surface_allocation` 在声明账本时必填，只包含 `front_fraction` 和 `rear_fraction`。二者必须是有限、非负的无量纲数，合计为 1（允许 `1e-12` 浮点误差），因此 0/1、1/0 和 0.5/0.5 均合法。它仅规定当前液冷服务器的**空气侧余热**如何分摊至前后两个风侧表面热通量；并非 IT 功耗在液冷与风冷之间的比例，也不能替代各表面 STL 面积、冷板取热或储能计算。实际导出仍须从对应已接受运行与几何面积派生，不能把示例比例当作现场事实。

`servers` 是非空对象；键必须是 `constructions.racks[rackUid].constructions.servers[serverUid]` 对应的精确 `rackUid/serverUid`，不能使用显示名称或虚构设备。每项只能有 `total_it_power_W`，其值必须是有限且严格大于零的瓦特数；账本及单项中的未知字段均拒绝。schema 校验每个已声明的绑定，不自动补齐未声明服务器。后端热量交接编译器额外要求**恰好覆盖所有液冷服务器**、风侧 `.cfdrun` 的工程内容哈希一致、液侧运行输入/结果/回执身份一致且整个时域完成，再用 `P_IT−Q_liquid−dU_chip/dt` 计算空气余热。前/后面的固定温度边界被热通量或零热墙面替换；STL 面积用于初始编译，网格通过 `checkMesh` 后还要用实际 patch 面积重算 `q` 才可进入求解。此链路是单向液→风交接，并不证明风侧 CFD 已收敛或双向耦合成立。

新导出的 v3 `source_project.sha256` 与 v2 `sourceProject.contentHash` 必须同为当前规范化 `.cfdproj` 内容哈希；液侧旧文件若只有 UID，可以独立运行，但不能进入此热量交接。风侧 `geometryExport.stlFiles[*].contentHash` 则绑定 grouped STL 的导出时原始字节。混合风冷重新网格前复核这些 STL；网格完成后写 `hybrid-mesh-source.json`，绑定工程、STL 清单和 `points/faces/owner/neighbour/boundary` 的网格摘要。复用网格求解时必须核对该回执、严格质量检查和实际面积，再生成有界的最终热通量运行产物；缺一即拒绝风侧求解。

离线批处理可用 `backend.liquidcooling.runtime_batch.run_dae_manifest` 生成带 `executed-inputs.json` 的完整液侧目录，再用 `python -m backend.aircooling.runner.hybrid_air_case_cli --project ... --air ... --tri-surface ... --liquid-run ... --sample-index ... --output ...` 生成一个新的风侧 case；随后对该 case 分别执行 `run_cfd --mesh-only` 与 `--solve-only`。CLI 只写派生目录，不改 `.cfdproj`；网格或身份检查失败时求解入口拒绝继续。

---

## 9. 从 `.cfdproj` 到 `.cfdrun` 再到 OpenFOAM case

`.cfdproj` 保存可编辑的领域事实：房间几何、设备构件和物理参数。它不保存本次运行输入，也不直接作为 OpenFOAM Parser 的输入。运行流程应分为三步：

```text
.cfdproj
  -> STL export
  -> .cfdrun generation = STL artifacts + runtime inputs + mesh/simulation config + patch bindings
  -> OpenFOAM Parser consumes .cfdrun and generates the final OpenFOAM case
```

OpenFOAM 的 patch 类型、`0/` 场文件、`fvOptions`、STL solid 名称和 fallback 边界策略由 `.cfdrun` 生成器和 OpenFOAM Parser 共同负责，不写回 `.cfdproj`。

### 9.1 编译目标

`.cfdrun` 生成器按设备类型定义 Translator 规则。每个 Translator 读取 V4 schema 中对应的设备实例、模型默认值、导出的 STL solid 和 `.cfdrun.inputs`，然后写入 `.cfdrun` 中 OpenFOAM Parser 所需的信息。

| `.cfdrun` 内容 | 来源 | 用途 |
|---|---|---|
| `inputs` | 运行面板、批处理任务或 API 请求 | 保存本次 case 实际运行输入。 |
| `geometryExport` | STL 导出结果 | 保存 STL 文件、solid 名称、domain object path 和 surface uid 的对应关系。 |
| `boundaryBindings` | 设备 surface role、设备物理参数、`inputs` | 保存 patch role、OpenFOAM patch name、`bc_type` 和 BC 参数。OpenFOAM Parser 由它生成 `0/` patchField。 |
| `sourceTerms` | server power、chip power、cold plate/RDHX/ACU 换热量、room heat gains | 保存体热源、冷源、porous zone 或其他源项参数。OpenFOAM Parser 由它生成 `fvOptions`。 |
| `porousBindings` | `PatchSurface.perforation`、rack leakage、baffle/floor opening | 保存 porous baffle、cyclic pair、cellZone porous media 或等效泄漏边界参数。 |
| `postprocessConfig` | `instrumentation.probes`、`.cfdrun.inputs.labels` | 保存 probes、surfaceFieldValue 和后处理测点配置。 |

### 9.2 数据优先级

同一个物理量只能有一个最终事实源。编译时按以下优先级取值：

```text
inputs 显式工况覆盖
  > constructions 设备实例字段
  > models 模型库默认值
  > .cfdrun 生成器默认策略
```

例如 ACU 的本次送风温度来自 `inputs.acus[acuId].supplyAirTemperatureC`；若缺失，则可用 ACU controller 或 heat exchanger 模型默认值；仍缺失时才使用后端默认策略。这个优先级可以避免旧格式中 `physics`、`patchPhysics`、`defaultBC`、`openings` 同时作为事实源的问题。

### 9.3 Patch 角色推导

`.cfdrun` 生成器先把 V4 对象的 surface 编译为统一的 patch role。role 是领域语义，不是 OpenFOAM patchField 本身。

| V4 来源 | Patch role | 说明 |
|---|---|---|
| `ACU.geometry.supplyFace` | `acu_supply` | ACU 送风面。温度和风量由 `inputs.acus` 与 `ACU.constructions.heatExchanger/fans` 决定。 |
| `ACU.geometry.returnFace` | `acu_return` | ACU 回风面。可与 supply patch 配对，用于 capacity 或 return-temperature 控制。 |
| `Server.geometry.inletFace` | `fan_inlet` | 风冷服务器进风面，通常是机柜冷通道侧。 |
| `Server.geometry.outletFace` | `fan_outlet` | 风冷服务器出风面。出风温度由功率、流量、液冷分流比例计算。 |
| `Server.constructions.chips.*.geometry` | `chip_heat_source` | 芯片或局部热源。可编译为表面热通量、固定温度或体热源。 |
| `Server.constructions.coldPlates.*` | `cold_plate_sink` | 冷板换热构件。进入液冷网络求解或等效热移除项。 |
| `Rack.constructions.rdhx.geometry` | `rdhx_surface` | RDHX 后门换热面。与 `rdhx.heatExchanger/fans` 和 `inputs.racks.*.rdhx` 共同生成边界或源项。 |
| `Floor.openings[]`、`Baffle.openings[]`、`PatchSurface.perforation` | `perforated_baffle` | 多孔地板、挡板、机柜门开孔。由 `perforation.openAreaPct` 等参数生成阻力。 |
| `PartitionWall.holes[]` | `leakage_opening` | 隔墙孔洞或泄漏口，可按开孔率生成 porous/cyclic/wall 退化边界。 |
| 普通 `faces`、墙体、盒体封闭面 | `wall` | 默认固壁。 |
| `Surface` 标记为固定温度或热通量 | `fixed_temperature` / `heat_source_flux` | 用于非设备热边界或校准边界。 |

### 9.4 `.cfdrun` 到 OpenFOAM field 的映射

OpenFOAM Parser 消费 `.cfdrun.boundaryBindings`。实现上可沿用现有 `BridgeBCRegistry` 风格，把 patch name 绑定到 `bc_type` 和参数，再由 field generator 写入 `0/U`、`0/T`、`0/p_rgh`、`0/k`、`0/epsilon`、`0/nut`、`0/alphat`。

| `bc_type` | `U` | `T` | `p_rgh` | 参数来源 |
|---|---|---|---|---|
| `wall` | `noSlip` | `zeroGradient` | `fixedFluxPressure` 或默认 wall pressure | 普通墙面、设备封闭面。 |
| `acu_supply` | `flowRateInletVelocity` | `fixedValue` 或控制表达式 | `fixedFluxPressure` | `inputs.acus.supplyAirVolumeFlowRateM3s`、`inputs.acus.supplyAirTemperatureC`、`ACU.constructions.fans`、`ACU.constructions.heatExchanger`。 |
| `acu_return` | `zeroGradient` | `zeroGradient` | `fixedValue 0` 或回风参考压力 | `ACU.geometry.returnFace`，可与 supply patch 配对。 |
| `fan_inlet` | `flowRateOutletVelocity` | `zeroGradient` | `fixedFluxPressure` | `inputs.servers.airVolumeFlowRateM3s` 或 `Server.cooling.volumeFlowRate`。 |
| `fan_outlet` | `flowRateInletVelocity` | `fixedValue` 或 `exprFixedValue` | `fixedFluxPressure` | server 功率、风量、温升、液冷分流比例。 |
| `nonfan_fixed_temp` | `noSlip` | `fixedValue` | `fixedFluxPressure` | 非风冷设备或 chip 固定温度输入。 |
| `fixedTemperature` | `noSlip` | `fixedValue` | `fixedFluxPressure` | 通用固定温度 surface。 |
| `heat_source_flux` | `noSlip` | `externalWallHeatFluxTemperature` | `fixedFluxPressure` | chip/server/自定义 surface 的功率除以有效面积。 |
| `nonfan_heat_flux` | `noSlip` | `externalWallHeatFluxTemperature` | `fixedFluxPressure` | 非风冷设备表面热通量（功率/面积）。 |
| `perforated_baffle` | `cyclic` 或 wall 退化 | `cyclic` 或 wall 退化 | `porousBafflePressure` | `PatchSurface.perforation.openAreaPct`、孔型、孔径；`openAreaPct=0` 退化为 wall，接近 100% 退化为 cyclic。 |
| `wall_baffle` | `noSlip` | `zeroGradient` | `fixedFluxPressure` | 封闭挡板、关闭的 opening。 |
| `through_zone` | —— | —— | —— | 信息性类型：机柜泄漏直通区，纯流体区域，不写 patchField。 |

湍流边界由 flow inlet patch 派生，不由 `.cfdproj` 显式保存。`acu_supply` 和 `fan_outlet` 可按流量、patch 面积、水力直径和内部默认湍流强度生成 `k/epsilon`；无流量或无面积时退回默认 inlet/outlet 语义。

### 9.5 设备 translator

| Translator | 输入 | 生成内容 |
|---|---|---|
| `ACUTranslator` | `constructions.acus.* + inputs.acus` | `supplyFace -> acu_supply`、`returnFace -> acu_return`；从 heat exchanger 取制冷能力和性能表，从 fans 取风量/压升/功耗。 |
| `ServerTranslator` | `Rack.constructions.servers`、`constructions.servers`、`inputs.servers` | `inletFace -> fan_inlet`、`outletFace -> fan_outlet`；按 `bcMode` 由功率、风量、温升三者推导出缺失量。 |
| `ChipTranslator` | `Server.constructions.chips + inputs.servers.*.chips` | 生成 `heat_source_flux`、固定温度边界或体热源。chip 是 server construction，不作为 room-level device。 |
| `ColdPlateTranslator` | `Server.constructions.coldPlates + inputs.servers.*.coldPlates` | 生成液冷网络节点、换热源项或 server 热量分流比例。cold plate 是 server construction。 |
| `RackTranslator` | `constructions.racks + inputs.racks` | 生成 rack 壳体、门板、盲板、泄漏开孔；`constructions.rdhx` 交给 RDHX translator。 |
| `RDHXTranslator` | `Rack.constructions.rdhx + inputs.racks.*.rdhx` | 生成 RDHX 换热面、风机面、冷源或液冷网络连接。RDHX 是 rack construction。 |
| `OpeningTranslator` | `Floor.openings`、`Baffle.openings`、`PartitionWall.holes`、`PatchSurface.perforation` | 生成 `perforated_baffle`、`wall_baffle` 或泄漏 porous 边界。 |
| `LiquidNetworkTranslator` | `constructions.cdus`、`Server.constructions.coldPlates`、`pipeSegments`、`pipeJunctions`、`inputs` | 生成液冷网络拓扑、端口连接和冷板/CDU 边界输入。 |
| `MonitorTranslator` | `instrumentation`、`.cfdrun.inputs.labels` | 生成 probes 和 surface monitors。 |

### 9.6 关键物理量推导

| 目标量 | 推导规则 |
|---|---|
| ACU 送风温度 | 优先取 `inputs.acus.supplyAirTemperatureC`；若使用 capacity 模式，则由 return 温度、heat exchanger capacity、coolant temperature 和性能曲线推导。 |
| ACU 风量 | 优先取 `inputs.acus.supplyAirVolumeFlowRateM3s`；否则汇总 `ACU.constructions.fans.*.cooling.designAirFlowRate` 或 `maximumFlowRate`。 |
| Server 出风温度 | 若 `bcMode=powerAndFlow`，用 `Tin + Qair / (rho * cp * flow)`；`Qair = inputPowerW * (1 - liquidPercentage)`。若给定 `temperatureRiseC`，直接使用温升。 |
| Chip 热通量 | `powerW / surfaceArea`。`powerW` 优先来自 `inputs.servers.*.chips.*.powerW`，否则来自 `Chip.cooling.ratedPowerW` 或 server aggregate power 的分配。 |
| Cold plate 热移除 | 由 `inputs.servers.*.coldPlates` 的流量和进口温度、`ColdPlate.cooling` 模型、CDU/pipe 网络共同求解；简化模式可先用 `liquidPercentage` 从 server 总功率中扣除。 |
| RDHX 换热量 | 由 `inputs.racks.*.rdhx`、`RDHX.heatExchanger.cooling` 和 RDHX 风机流量推导；可作为后门换热边界或冷源项。 |
| 多孔阻力 | `PatchSurface.perforation.openAreaPct` 映射到 OpenFOAM `porousBafflePressure` 的阻力系数；孔型和孔径影响经验系数。 |

### 9.7 输出边界绑定

`.cfdrun` 生成器应为每个可生成 STL 的 surface 输出稳定绑定关系：

```text
domain object path
  -> surface uid / role
  -> STL solid name
  -> sanitized OpenFOAM patch name
  -> BridgeBCSpec 或 fvOptions entry
```

绑定关系应写入 `.cfdrun`。OpenFOAM Parser 读取 `.cfdrun` 后，可以把这些绑定加载成运行时 registry，例如现有 `BridgeBCRegistry`，但 registry 只是内存形态，不是 `.cfdproj` 的持久字段。这样 `.cfdproj` 仍保持为领域模型，OpenFOAM Parser 也只依赖 `.cfdrun` 生成最终 case。

### 9.8 `.cfdrun` schema

基于 V4 `.cfdproj` 后，`.cfdrun` 仍然有必要存在，但它的定位需要收窄：它不是第二套房间/设备 schema，也不是 `.cfdproj` 的完整拷贝，而是一次运行的冻结 manifest。

`.cfdrun` 的完整 V2 字段设计、运行输入 schema、STL artifact、边界绑定、源项、多孔绑定、mesh/solver 配置和兼容迁移规则见 [.cfdrun V2 proposal](cfdrun-v2-schema.md)。

---

## 10. v3 → v4 迁移

迁移策略沿用 v2→v3 的成熟做法（JS/Python 双实现 + parity 测试 + 浏览器验证门禁）：

- 加载链：v1/v2 → v3（现有 `migrateCfdprojV2toV3`）→ v4（新增 `migrateCfdprojV3toV4`），在每次加载时于内存中执行；**保存始终写 V4**。加载器拒绝 `schemaVersion > 4` 并给出明确报错。
- JS 实现位于 `src/frontend/shared/js/cfdproj_migrate.js`，Python 镜像位于 `src/frontend/scripts/cfdproj_migrate.py`，golden parity 测试保证两者输出**取值一致（value-parity）**：规范化数字表示后逐字段相等。浏览器 JSON 序列化器与 Python `json` 在科学计数法阈值等表示上无法逐字节统一（如 `1e-05` vs `0.00001`），但二者 round-trip 到相同数值；Python 侧将整数值浮点回收为 int（`7.0 → 7`）以保持常见情形写出的字节干净。（2026-06-12 决议：契约为 value-parity，非严格 byte-parity。）
- **schema 定义独立成文件**：V4 文档结构（版本号、分桶注册表、各 kind 默认工厂与 normalizer、结构校验）由专用模块 `src/frontend/shared/js/cfdproj_schema.js` 唯一持有；`cfdproj_migrate.js`、`cfdrun_export.js` 与主文件均通过它访问结构知识，不得在别处重复声明 schema（2026-06-12 修订，D7）。
- v3 `construction[]` 按 `kind` 映射入 `constructions.*` 分桶；v3 `simulation` 与 `liquidCooling.{solver, enabled, inheritResults}` → `runDefaults`；v3 `probeData`/`sensorData` → `instrumentation`；v3 `ui` 丢弃（UI 偏好迁入 `localStorage`）；未知字段原样携带。
- 完整字段映射表与机械重命名规则（`anchor → geometry.location`、`base{W,H,D} → geometry.size{x,y,z}`（即 `base.W→x`、`base.H→y`、`base.D→z`，见 `cfdproj_migrate.js` `_commonV4`）、`cfd → cooling` 等）见设计文档 `docs/superpowers/specs/2026-06-12-cfdproj-v4-cfdrun-v2-design.md` §5。

---

## 修订记录

| 日期 | 修订 |
|---|---|
| 2026-06-10 | 初稿（V4 提案）。 |
| 2026-06-12 | 设计评审修订：新增文件角色契约（§前言）与 `runDefaults`（§8）；`ui` 持久化改为 `localStorage`（不用 sidecar）；模型库 bucket 标记为按需启用并给出迁移来源（§3）；坐标约定固定为 Y-up / `x/z` 平面（§2.2、§5.1）；`quaternion` 为姿态事实源（§5.3）；取消 `validId`，`uid` 沿用前端 `id`（§4）；补充 `ObjectMeta` 定义（§5.5）；`meta` 增加 `inputUnit`（§2.1）；新增迁移章节（§10）。 |
| 2026-06-12 (D7) | schema 独立成文件：V4 文档结构由专用模块 `js/cfdproj_schema.js` 唯一持有（§10）；既有项目文件的加载验证必须通过 GPU WebGL 版 browser-use 完成（见设计文档 §9.4）。 |
| 2026-06-12 (Plan 1) | ACU `supplyFace`/`returnFace` 改为复数 `supplyFaces[]`/`returnFaces[]`（实际设备多送风面）。 |
| 2026-06-14 (Plan 2/4 完成) | **设计稿升级为实现版字段参考。** 顶部新增实现状态横幅，给出权威结构源（`cfdproj_schema.js` / `cfdproj_migrate.{js,py}` / `cfdproj_state_v4.js`）与每个 model 的规范形状（`geometry.{location,size,quaternion,scale,faces,cutouts}` + `geometry.faces[*].patches[*].physics`）。编辑器已端到端原生 V4（Plan 2，T16 `8d485d9`）；Plan 4 已批量转换 20 个 tracked 项目为 V4（`Slab_dataset` 保留 v2）。§4 分桶列表与 `cfdproj_schema.js` `BUCKETS` 核对一致。 |
| 2026-09-27 | §8.1 增加可选 `hybridHeatBudget` 声明与共享 schema 校验：显式来源、服务器稳定 UID、有限正瓦特值，以及必填的前后风侧表面分配比例；仅定义工程事实，不代表风液耦合导出或现场校准已完成。 |
| 2026-10-21 | 顶部加当前版本说明，指向 v5 参考；§1 `schemaVersion` 说明补 v5；§4 分桶树与字段表补风侧管道桶 `pipes`，注明 `raisedFloor`/`falseCeiling` 是单例槽位。 |
