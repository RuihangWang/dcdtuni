# .cfdrun 字段参考（V2，含 v3/v4 变化）

> **状态（截至 2026-10-21）：** 后端接受 `schemaVersion` 2、3、4（`CfdrunV2.validate_schema_version`）。**React 编辑器导出 v4**（`src/frontend/react/src/legacy/cfdrunBuild.ts` 的 `buildManifestV2`），旧 HTML 编辑器导出 v2。v3 在 v2 上加了瞬态时间轴块和边界绑定上的时间表；v4 在 v3 的基础上只保留**根级一份** `simulationConfig`，runner 由它推导实际执行的配置。版本差异集中写在下文 §15「v3 与 v4」，其余各节的字段表对三个版本通用。权威结构源 = 后端严格 Pydantic 模型 `src/backend/aircooling/runner/cfdrun_schema.py`（根模型 `CfdrunV2`，嵌套模型 `extra="forbid"`），前端生成器 = `src/frontend/shared/js/cfdrun_export.js`（`buildManifest`，两个编辑器共用）加 React 的 `buildManifestV2`，后端物化 = `cfdrun_reader.py`（`resolve_run()`）与 `derived_config.py`（`execution_blob()`）。
>
> **历史（2026-06-14）：** 本文由设计稿升级为实现版字段参考时，已对照 `cfdrun_schema.py`、`cfdrun_export.js`、`cfdrun_schema.js`、`cfdrun_reader.py` 逐字段复核；Plan 4（cfdproj V4 / cfdrun V2 重构）完成了 5 例 §9.4 验收，registry 全等，网格都在基线 0.13% 以内。

本文档描述基于 `.cfdproj` V4/V5 的 `.cfdrun` 文件结构（v5 工程导出时先物化到 t = 0，时间变化另写成时间表，见 §15）。`.cfdrun` V2 不是第二套房间/设备 schema，也不是 `.cfdproj` 的完整拷贝；它是一次运行的冻结 manifest。

生成位置与时机（2026-06-12 修订）：**`.cfdrun` 由前端浏览器在用户触发导出/网格/求解时，从当前实时 UI 状态生成。** 未保存到 `.cfdproj` 的修改（例如刚改过的 server 功率）同样进入 `.cfdrun`。

实现文件划分（2026-06-12 修订，D7——schema 独立成文件，不与迁移/翻译/编排逻辑混置）：

| 模块 | 职责 |
|---|---|
| `src/frontend/shared/js/cfdrun_schema.js` | V2 manifest 结构定义：版本号、各 section 骨架构造器、`bcType`/`role` 枚举、`validateManifest()` 客户端结构校验。不读取 state，不含翻译逻辑。 |
| `src/frontend/shared/js/cfdrun_export.js` | translator 层：读取实时 state，按设备类型填充 schema（依赖 `cfdrun_schema.js` 与 `cfdproj_schema.js`）。 |
| `src/backend/aircooling/runner/cfdrun_schema.py` | Pydantic v2 模型，全量声明字段并严格校验（`extra="forbid"`，不再依赖 `extra="allow"` 携带未声明字段）。不做 I/O。（v1 几何 schema `CFDExport` 校验 `.cfdrun` 的 `meshGeometry` 块；该文件原误名 `cfdproj_schema.py`，2026-06-15 更名为 `cfdrun_schema.py`。） |
| `src/backend/aircooling/runner/cfdrun_reader.py` | 专用 reader/adapter：发现并严格校验 V2 manifest；可选的 STL 解析核对文件路径、solid 名与已声明面积。当前 `STLGenRunner` 用 `resolve_stl=False` 物化 BC registry；混合风冷重新网格前另由 `stl_geometry_index.verify_exported_stl_hashes` 核对导出时 SHA-256、文件集合和已声明源面所在 solid。Reader 把边界、挡板、源项及后处理配置物化为 `ResolvedRun`，runner 不自行推导边界绑定。 |

推荐的数据流如下：

```text
.cfdproj
  ├── geometry / models / constructions / instrumentation
  └── 用于导出 STL 几何

运行面板 / 批处理任务 / API 请求
  └── runtime inputs / mesh config / simulation config

.cfdrun
  ├── inputs
  ├── geometryExport
  ├── boundaryBindings
  ├── sourceTerms
  ├── porousBindings
  ├── meshConfig
  ├── simulationConfig
  └── postprocessConfig

OpenFOAM Parser
  └── consume .cfdrun -> generate OpenFOAM case
```

核心原则：**OpenFOAM Parser 只读取 `.cfdrun`。`.cfdproj` 负责编辑态领域模型和 STL 导出；`.cfdrun` 负责保存这一次 case 实际运行所需的输入、artifact、边界绑定和任务配置。** `.cfdproj` 是用户显式保存的文档；`.cfdrun` 是触发运行时从实时 UI 状态生成的冻结快照——两者的角色契约见 [cfdproj-v4-schema.md](cfdproj-v4-schema.md) 前言。

---

## 从 `.cfdproj` 到 `.cfdrun` 再到 OpenFOAM case

`.cfdproj` 保存可编辑的领域事实：房间几何、设备构件和默认物理参数。`.cfdrun` 保存一次运行的冻结事实：STL artifact、本次运行输入、mesh/solver 配置、patch 绑定、source term 和后处理配置。OpenFOAM case 的生成只基于 `.cfdrun`。

```text
.cfdproj
  -> STL export
  -> .cfdrun generation = STL artifacts + runtime inputs + mesh/simulation config + patch bindings
  -> OpenFOAM Parser consumes .cfdrun and generates the final OpenFOAM case
```

OpenFOAM 的 patch 类型、`0/` 场文件、`fvOptions`、STL solid 名称和 fallback 边界策略由 `.cfdrun` 生成器和 OpenFOAM Parser 共同负责，不写回 `.cfdproj`。

### 编译目标

`.cfdrun` 生成器按设备类型定义 Translator 规则。每个 Translator 读取 V4 `.cfdproj` 中对应的设备实例、模型默认值、导出的 STL solid 和 `.cfdrun.inputs`，然后写入 OpenFOAM Parser 所需的信息。

| `.cfdrun` 内容 | 来源 | 用途 |
|---|---|---|
| `inputs` | 运行面板、批处理任务或 API 请求 | 保存本次 case 实际运行输入。 |
| `geometryExport` | STL 导出结果 | 保存 STL 文件、solid 名称、domain object path 和 surface uid 的对应关系。 |
| `boundaryBindings` | 设备 surface role、设备物理参数、`.cfdrun.inputs` | 保存 patch role、OpenFOAM patch name、`bc_type` 和 BC 参数。OpenFOAM Parser 由它生成 `0/` patchField。 |
| `sourceTerms` | server power、chip power、cold plate/RDHX/ACU 换热量、room heat gains | 保存体热源、冷源、porous zone 或其他源项参数。OpenFOAM Parser 由它生成 `fvOptions`。 |
| `porousBindings` | `PatchSurface.perforation`、rack leakage、baffle/floor opening | 保存 porous baffle、cyclic pair、cellZone porous media 或等效泄漏边界参数。 |
| `postprocessConfig` | `instrumentation.probes`、`.cfdrun.inputs.labels` | 保存 probes、surfaceFieldValue 和后处理测点配置。 |

### 数据优先级

同一个物理量只能有一个最终事实源。编译时按以下优先级取值：

```text
.cfdrun.inputs 显式工况覆盖
  > constructions 设备实例字段
  > models 模型库默认值
  > .cfdrun 生成器默认策略
```

例如 ACU 的本次送风温度来自 `.cfdrun.inputs.acus[acuId].supplyAirTemperatureC`；若缺失，则可用 ACU controller 或 heat exchanger 模型默认值；仍缺失时才使用后端默认策略。这个优先级可以避免旧格式中 `physics`、`patchPhysics`、`defaultBC`、`openings` 同时作为事实源的问题。

### Patch 角色推导

`.cfdrun` 生成器先把 V4 对象的 surface 编译为统一的 patch role。role 是领域语义，不是 OpenFOAM patchField 本身。

| V4 来源 | Patch role | 说明 |
|---|---|---|
| `ACU.geometry.supplyFace` | `acu_supply` | ACU 送风面。温度和风量由 `.cfdrun.inputs.acus` 与 `ACU.constructions.heatExchanger/fans` 决定。 |
| `ACU.geometry.returnFace` | `acu_return` | ACU 回风面。可与 supply patch 配对，用于 capacity 或 return-temperature 控制。 |
| `Server.geometry.inletFace` | `fan_inlet` | 风冷服务器进风面，通常是机柜冷通道侧。 |
| `Server.geometry.outletFace` | `fan_outlet` | 风冷服务器出风面。出风温度由功率、流量、液冷分流比例计算。 |
| `Server.constructions.chips.*.geometry` | `chip_heat_source` | 芯片或局部热源。可编译为表面热通量、固定温度或体热源。 |
| `Server.constructions.coldPlates.*` | `cold_plate_sink` | 冷板换热构件。进入液冷网络求解或等效热移除项。 |
| `Rack.constructions.rdhx.geometry` | `rdhx_surface` | RDHX 后门换热面。与 `rdhx.heatExchanger/fans` 和 `.cfdrun.inputs.racks.*.rdhx` 共同生成边界或源项。 |
| `Floor.openings[]`、`Baffle.openings[]`、`PartitionWall.holes[]`、`PatchSurface.perforation` | `perforated_baffle` | 多孔地板、挡板、机柜门开孔。由 `perforation.openAreaPct` 等参数生成阻力。 |
| 普通 `faces`、墙体、盒体封闭面 | `wall` | 默认固壁。 |
| `Surface` 标记为固定温度或热通量 | `fixed_temperature` / `heat_source_flux` | 用于非设备热边界或校准边界。 |

### `.cfdrun` 到 OpenFOAM field 的映射

OpenFOAM Parser 消费 `.cfdrun.boundaryBindings`。实现上可沿用现有 `BridgeBCRegistry` 风格，把 patch name 绑定到 `bc_type` 和参数，再由 field generator 写入 `0/U`、`0/T`、`0/p_rgh`、`0/k`、`0/epsilon`、`0/nut`、`0/alphat`。

| `bc_type` | `U` | `T` | `p_rgh` | 参数来源 |
|---|---|---|---|---|
| `wall` | `noSlip` | `zeroGradient` | `fixedFluxPressure` 或默认 wall pressure | 普通墙面、设备封闭面。 |
| `acu_supply` | `flowRateInletVelocity` | `fixedValue` 或控制表达式 | `fixedFluxPressure` | `.cfdrun.inputs.acus.supplyAirVolumeFlowRateM3s`、`.cfdrun.inputs.acus.supplyAirTemperatureC`、`ACU.constructions.fans`、`ACU.constructions.heatExchanger`。 |
| `acu_return` | `zeroGradient` | `zeroGradient` | `fixedValue 0` 或回风参考压力 | `ACU.geometry.returnFace`，可与 supply patch 配对。 |
| `fan_inlet` | `flowRateOutletVelocity` | `zeroGradient` | `fixedFluxPressure` | `.cfdrun.inputs.servers.airVolumeFlowRateM3s` 或 `Server.cooling.volumeFlowRate`。 |
| `fan_outlet` | `flowRateInletVelocity` | `fixedValue` 或 `exprFixedValue` | `fixedFluxPressure` | server 功率、风量、温升、液冷分流比例。 |
| `nonfan_fixed_temp` | `noSlip` | `fixedValue` | `fixedFluxPressure` | 非风冷设备或 chip 固定温度输入。 |
| `fixedTemperature` | `noSlip` | `fixedValue` | `fixedFluxPressure` | 通用固定温度 surface。 |
| `heat_source_flux` | `noSlip` | `externalWallHeatFluxTemperature` | `fixedFluxPressure` | chip/server/自定义 surface 的功率除以有效面积。 |
| `nonfan_heat_flux` | `noSlip` | `externalWallHeatFluxTemperature` | `fixedFluxPressure` | 非风冷设备表面热通量（功率/面积），现有 `fields.py` 已实现。 |
| `perforated_baffle` | `cyclic` 或 wall 退化 | `cyclic` 或 wall 退化 | `porousBafflePressure` | `PatchSurface.perforation.openAreaPct`、孔型、孔径；`openAreaPct=0` 退化为 wall，接近 100% 退化为 cyclic。 |
| `wall_baffle` | `noSlip` | `zeroGradient` | `fixedFluxPressure` | 封闭挡板、关闭的 opening。 |
| `through_zone` | —— | —— | —— | 信息性类型：机柜泄漏直通区，纯流体区域，不写 patchField，仅用于追溯（2026-04-17 起无多孔源项）。 |

湍流边界由 flow inlet patch 派生，不由 `.cfdproj` 显式保存。`acu_supply` 和 `fan_outlet` 可按流量、patch 面积、水力直径和内部默认湍流强度生成 `k/epsilon`；无流量或无面积时退回默认 inlet/outlet 语义。

### 设备 Translator

| Translator | 输入 | 生成内容 |
|---|---|---|
| `ACUTranslator` | `constructions.acus.* + .cfdrun.inputs.acus` | `supplyFace -> acu_supply`、`returnFace -> acu_return`；从 heat exchanger 取制冷能力和性能表，从 fans 取风量/压升/功耗。 |
| `ServerTranslator` | `Rack.constructions.servers`、`constructions.servers`、`.cfdrun.inputs.servers` | `inletFace -> fan_inlet`、`outletFace -> fan_outlet`；按 `bcMode` 由功率、风量、温升三者推导出缺失量。 |
| `ChipTranslator` | `Server.constructions.chips + .cfdrun.inputs.servers.*.chips` | 生成 `heat_source_flux`、固定温度边界或体热源。chip 是 server construction，不作为 room-level device。 |
| `ColdPlateTranslator` | `Server.constructions.coldPlates + .cfdrun.inputs.servers.*.coldPlates` | 生成液冷网络节点、换热源项或 server 热量分流比例。cold plate 是 server construction。 |
| `RackTranslator` | `constructions.racks + .cfdrun.inputs.racks` | 生成 rack 壳体、门板、盲板、泄漏开孔；`constructions.rdhx` 交给 RDHX translator。 |
| `RDHXTranslator` | `Rack.constructions.rdhx + .cfdrun.inputs.racks.*.rdhx` | 生成 RDHX 换热面、风机面、冷源或液冷网络连接。RDHX 是 rack construction。 |
| `OpeningTranslator` | `Floor.openings`、`Baffle.openings`、`PartitionWall.holes`、`PatchSurface.perforation` | 生成 `perforated_baffle`、`wall_baffle` 或泄漏 porous 边界。 |
| `LiquidNetworkTranslator` | `constructions.cdus`、`Server.constructions.coldPlates`、`pipeSegments`、`pipeJunctions`、`.cfdrun.inputs` | 生成液冷网络拓扑、端口连接和冷板/CDU 边界输入。 |
| `MonitorTranslator` | `instrumentation`、`.cfdrun.inputs.labels` | 生成 probes 和 surface monitors。 |

### 关键物理量推导

| 目标量 | 推导规则 |
|---|---|
| ACU 送风温度 | 优先取 `.cfdrun.inputs.acus.supplyAirTemperatureC`；若使用 capacity 模式，则由 return 温度、heat exchanger capacity、coolant temperature 和性能曲线推导。 |
| ACU 风量 | 优先取 `.cfdrun.inputs.acus.supplyAirVolumeFlowRateM3s`；否则汇总 `ACU.constructions.fans.*.cooling.designAirFlowRate` 或 `maximumFlowRate`。 |
| Server 出风温度 | 若 `bcMode=powerAndFlow`，用 `Tin + Qair / (rho * cp * flow)`；`Qair = inputPowerW * (1 - liquidPercentage)`。若给定 `temperatureRiseC`，直接使用温升。 |
| Chip 热通量 | `powerW / surfaceArea`。`powerW` 优先来自 `.cfdrun.inputs.servers.*.chips.*.powerW`，否则来自 `Chip.cooling.ratedPowerW` 或 server aggregate power 的分配。 |
| Cold plate 热移除 | 由 `.cfdrun.inputs.servers.*.coldPlates` 的流量和进口温度、`ColdPlate.cooling` 模型、CDU/pipe 网络共同求解；简化模式可先用 `liquidPercentage` 从 server 总功率中扣除。 |
| RDHX 换热量 | 由 `.cfdrun.inputs.racks.*.rdhx`、`RDHX.heatExchanger.cooling` 和 RDHX 风机流量推导；可作为后门换热边界或冷源项。 |
| 多孔阻力 | `PatchSurface.perforation.openAreaPct` 映射到 OpenFOAM `porousBafflePressure` 的阻力系数；孔型和孔径影响经验系数。 |

### 输出边界绑定

`.cfdrun` 生成器应为每个可生成 STL 的 surface 输出稳定绑定关系：

```text
domain object path
  -> surface uid / role
  -> STL solid name
  -> sanitized OpenFOAM patch name
  -> BridgeBCSpec 或 fvOptions entry
```

绑定关系应写入 `.cfdrun`。OpenFOAM Parser 读取 `.cfdrun` 后，可以把这些绑定加载成运行时 registry，例如现有 `BridgeBCRegistry`，但 registry 只是内存形态，不是 `.cfdproj` 的持久字段。这样 `.cfdproj` 仍保持为领域模型，OpenFOAM Parser 也只依赖 `.cfdrun` 生成最终 case。

---

## 1. 顶层字段

```text
CfdrunV2
  ├── schemaVersion
  ├── runId
  ├── name
  ├── sourceProject
  ├── inputs
  ├── geometryExport
  ├── boundaryBindings
  ├── sourceTerms
  ├── porousBindings
  ├── meshConfig
  ├── simulationConfig
  ├── postprocessConfig
  ├── artifacts
  ├── meshGeometry        # 过渡块（见 §14）
  └── transient           # v3/v4：瞬态时间轴（见 §15）
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schemaVersion` | `integer` | 是 | `.cfdrun` 文件格式版本，取 `2`、`3` 或 `4`（React 导出 `4`）。其它值（包括无此字段的 v1）在载入时直接报错，错误信息含 `re-export`，不做迁移（A12 硬切换）。版本差异见 §15。 |
| `runId` | `string` | 否（默认 `""`） | 本次运行 ID。每次 mesh/solve 任务应生成新的 ID。 |
| `name` | `string` | 否（默认 `""`） | 本次运行显示名。 |
| `sourceProject` | `SourceProject \| null` | 否 | 来源 `.cfdproj` 的追踪信息。只用于溯源，不作为 OpenFOAM Parser 的运行事实源。 |
| `inputs` | `RunInputs` | 否（默认空骨架） | 本次运行输入快照。 |
| `geometryExport` | `GeometryExport` | 否（默认空骨架） | STL 文件、solid、surface uid、domain object path 的对应关系。 |
| `boundaryBindings` | `Record<uid, BoundaryBinding>` | 否（默认 `{}`） | OpenFOAM patch 边界绑定。**根级 `model_validator` 校验所有 `patchName`（sanitize 后）在全 manifest 内唯一。** |
| `sourceTerms` | `Record<uid, SourceTerm>` | 否（默认 `{}`） | 热源、冷源、换热源项、多孔区源项等。 |
| `porousBindings` | `Record<uid, PorousBinding>` | 否（默认 `{}`） | 多孔挡板、地板开孔、机柜门、泄漏孔洞等阻力边界。 |
| `meshConfig` | `MeshConfig` | 否（默认空骨架） | 本次网格任务配置（见 §8，全字段）。 |
| `simulationConfig` | `SimulationConfig` | 否（默认空骨架） | 本次求解任务配置（见 §9，全字段）。 |
| `postprocessConfig` | `PostprocessConfig` | 否 | probes、监控面、输出字段和后处理配置。 |
| `artifacts` | `RunArtifacts` | 否 | 本次运行生成或依赖的文件路径，例如 STL、OpenFOAM case、液冷结果、日志。 |
| `meshGeometry` | `object \| null` | 否 | **过渡块（已实现，spec §12 已知偏差）。** 网格管线（封闭房间、createBaffles、背景网格包围盒、refinement、隔墙孔 fvOptions）仍读取 v1 几何 blob。浏览器把 `buildCfdrunJson()` 产出的 blob 挂在这里，runner 用 `CFDExport.model_validate(execution_blob(raw))` 读取它做网格，而用 V2 `boundaryBindings` 做 BC registry。**求解实际使用的 `simulationConfig`：v2/v3 取 `meshGeometry.simulationConfig`；v4 的 blob 里没有这一块，由根级 `simulationConfig` 推导（`derived_config.execution_blob`）。** `meshConfig` 在三个版本里都取 blob 的那一份。详见 §14。 |
| `transient` | `TransientBlock \| null` | v3 必填，v4 可选，v2 不允许 | 瞬态时间轴：`durationS`（> 0）与 `start`（`{kind: steadyConverged \| uniform, uniform: {airT_C, waterT_C, solidT_C}}`）。见 §15。 |

---

## 2. 来源项目 `sourceProject`

```text
sourceProject
  ├── uid
  ├── schemaVersion
  ├── projectName
  ├── filePath
  ├── contentHash
  └── exportedAt
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `uid` | `string` | 来源 `.cfdproj` 的项目 ID。 |
| `schemaVersion` | `integer` | 来源 `.cfdproj` schema 版本：`4` 或 `5`（React 保存的工程为 5）。 |
| `projectName` | `string` | 来源项目名称。 |
| `filePath` | `string` | 来源 `.cfdproj` 路径。可为空，避免移动 case 后失效。 |
| `contentHash` | `string` | 导出时**实时 V4 状态序列化结果**的 SHA-256（浏览器 SubtleCrypto 计算）。注意：由于 `.cfdrun` 来自实时 UI 状态而非保存文件，该 hash 可能与磁盘上 `.cfdproj` 文件的 hash 不同——不同即说明运行包含未保存修改（2026-06-12 修订）。 |
| `exportedAt` | `string` | 导出时间，ISO 8601 字符串。 |

---

## 3. 运行输入 `inputs`

`inputs` 描述本次仿真工况。它不保存设备额定能力，也不直接保存 OpenFOAM patchField。挡板、地板开孔等不随时间变化的构造参数应保留在 `.cfdproj.constructions` 中，并在 `.cfdrun` 中体现为 `geometryExport`、`boundaryBindings` 或 `porousBindings`，不放入 `inputs`。

```text
inputs
  ├── racks
  ├── acus
  ├── servers
  ├── cdus
  ├── partitionWalls
  ├── pipeSegments
  ├── liquid
  └── labels
```

### 3.1 `inputs.racks[rackId]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `rdhx.enabled` | `boolean` | rack 内 RDHX 是否开启。 |
| `rdhx.waterFlowRateLpm` | `number` | RDHX 水流量，单位 L/min。 |
| `rdhx.waterInletTempC` | `number` | RDHX 进水温度，单位 C。 |
| `rdhx.fanSpeedRatio` | `number` | RDHX 风机转速比例，0 到 1。 |

### 3.2 `inputs.acus[acuId]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `acuSwitch` | `boolean` | ACU 是否开启。 |
| `supplyAirTemperatureC` | `number` | 送风温度，单位 C。 |
| `supplyAirVolumeFlowRateM3s` | `number` | 送风体积流量，单位 m3/s。 |
| `coolingMode` | `fixedTemp, linearCapacity, tableCapacity` | 本次仿真的冷却控制模式。 |
| `setPointTemperatureC` | `number` | 控制设定温度，单位 C。 |
| `coolantTemperatureC` | `number` | 冷冻水或冷却液温度，单位 C。 |
| `surfaceOverrides` | `object` | 对 ACU 某些 surface 的运行覆盖，例如临时关闭某个送风面。 |
| `schedules` | `Record<string, Schedule>` | 随时间变化的输入，例如送风温度 schedule。 |

### 3.3 `inputs.servers[serverId]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `serverSwitch` | `boolean` | 服务器是否开启。 |
| `inputPowerW` | `number` | 本次仿真输入功率，单位 W。 |
| `inputPowerPercentage` | `number` | 相对额定功率百分比。 |
| `airVolumeFlowRateM3s` | `number` | 本次仿真空气流量，单位 m3/s。 |
| `temperatureRiseC` | `number` | 服务器进出风温升，单位 C。 |
| `liquidPercentage` | `number` | 热量由液冷带走的百分比，0 到 100。 |
| `liquidMassFlowRateKgs` | `number` | 液冷质量流量，单位 kg/s。 |
| `bcMode` | `powerAndFlow, tempRiseAndFlow, powerAndTempRise` | 用哪些量组合生成 CFD 边界条件。 |
| `chips` | `Record<chipId, object>` | server 内部芯片运行输入，例如功率、负载率、固定表面温度。 |
| `coldPlates` | `Record<coldPlateId, object>` | server 内部冷板运行输入，例如液冷流量、进口温度、阀门开度。 |
| `schedules` | `Record<string, Schedule>` | 功率、风量等时变输入。 |

### 3.4 `inputs.cdus[cduId]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `coolingWaterSupplyTemperatureSpC` | `number` | 二次侧或冷却水供水温度设定，单位 C。 |
| `chilledWaterSupplyTemperatureC` | `number` | 冷冻水供水温度，单位 C。 |
| `chilledWaterMassFlowRateKgs` | `number` | 冷冻水质量流量，单位 kg/s。 |
| `supplyPressureKpa` | `number` | 供水压力，单位 kPa。 |
| `valveOpening` | `number` | 阀门开度，0 到 1。 |

### 3.5 `inputs.partitionWalls[wallId]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `holeOverrides` | `object` | 按 hole ID 覆盖孔洞泄漏率、开关状态或边界类型。仅用于运行时临时覆盖。 |

### 3.6 `inputs.pipeSegments[pipeId]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `valveOpenings` | `Record<valveId, number>` | 管段上阀门开度，0 到 1。 |
| `blindEnds` | `Record<portId, boolean>` | 端口是否盲堵。 |

### 3.7 `inputs.liquid`

（2026-06-12 修订：新增。承接当前顶层 `liquidCooling` 摘要与 `inheritLiquidResults` 标志。）

| 字段 | 类型 | 说明 |
|---|---|---|
| `enabled` | `boolean` | 本次运行是否启用液冷模式。 |
| `inheritResults` | `boolean` | 早期设计中风冷求解继承液冷结果（扣除残余热量）的开关，字段按原语义保留，runner 不读取。现行的风液耦合是单向热量交接：先完成液冷 DAE 会话，再由 `/api/run-mesh` 的 `hybridHeat` 把已验收液冷样本的热量写进派生的风冷 `.cfdrun`（`hybrid_air_heat_handoff.py`，测试 `test_hybrid_air_heat_handoff.py`）。 |

### 3.8 `inputs.labels`

| 字段 | 类型 | 说明 |
|---|---|---|
| `sensorMeasurements` | `Record<sensorId, SensorMeasurement>` | 实测传感器值，可用于校准、验证或监督学习标签。 |
| `sensorMeasurements.*.temperatureC` | `number` | 传感器温度，单位 C。 |
| `sensorMeasurements.*.relativeHumidityPct` | `number` | 相对湿度，百分比。 |

---

## 4. STL 导出 `geometryExport`

```text
geometryExport
  ├── coordinateSystem
  ├── unit
  ├── stlFiles
  ├── solids
  └── sourceSurfaces?    # 源STL region身份；不等于网格faceZone
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `coordinateSystem` | `string` | 坐标系说明，例如 `Y-up`。 |
| `unit` | `string` | 几何单位，推荐固定为 `m`。 |
| `stlFiles` | `Record<uid, StlFile>` | 已导出的 STL 文件清单。 |
| `solids` | `Record<uid, StlSolidBinding>` | STL solid 与领域对象/surface 的绑定。 |
| `sourceSurfaces` | `Record<stlFileId:solidName, SourceSurfaceBinding>` | 可选的源面身份，不随 wallBaffle、机柜面或地板面合并而丢失工程 UID；旧清单缺失时仍合法。 |

### 4.1 `StlFile`

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | `string` | STL 文件相对 case/model 或 case/Mesh 的路径。 |
| `objectPath` | `string` | 来源领域对象路径，例如 `constructions.racks.R1`。 |
| `kind` | `string` | STL 类型；React grouped 导出使用 `grouped_ascii`。 |
| `contentHash` | `string` | 导出时原始 STL 字节的 SHA-256；混合风冷重新网格前强制逐文件核对。 |

### 4.2 `StlSolidBinding`

| 字段 | 类型 | 说明 |
|---|---|---|
| `solidName` | `string` | STL 内的 `solid <name>` 名称。 |
| `patchName` | `string` | OpenFOAM sanitize 后的 patch 名。 |
| `stlFileId` | `string` | 所属 `stlFiles` ID。 |
| `objectPath` | `string` | 来源领域对象路径。 |
| `surfaceUid` | `string` | 来源 surface/opening/face uid。 |
| `role` | `string` | 领域角色，例如 `acu_supply`、`fan_outlet`、`perforated_baffle`、`wall`。 |
| `areaM2` | `number \| null` | 浏览器可先填已知面积或留空；混合风液热通量编译从实际 STL 三角形计算目标热面面积，网格后还须用实际 patch 面积重算 `q`。 |
| `perimeterM` | `number` | 可选周长，用于水力直径推导（2026-06-12 修订：新增）。 |

### 4.3 `SourceSurfaceBinding`（2026-09-27 增量）

每个键固定为 `<stlFileId>:<solidName>`，`stlFileId` 必须存在于本清单的 `stlFiles`。字段仅允许 `stlFileId`、`solidName`、`sourceObjectUid`、`sourceFaceUid`、`representation`；`representation` 为 `zeroThicknessSheet` 或 `surfaceFace`。Shinaide 当前导出为三块独立挡板和 Raised_Floor 留下各自工程 UID，服务器面保留稳定机柜/服务器与面 ID；机柜空槽挡板按保存的机柜 UID、侧面与 U 区间绑定到准确的 STL solid，两块机柜共用一个按 U 跨度聚合的 STL 文件时仍各有不同 `sourceFaceUid`。混合风冷预检要求该 STL solid 真实存在且面积为正，并将 `sourceSurfaces` 摘要绑定到网格来源回执。`zeroThicknessSheet` 的 master/slave 是同一源片生成的两侧，不表示两份需要几何近邻去重的源面。

这是**来源溯源的第一步**，尚未把可选的物理厚壁双侧 `pairUid` 引入严格工程/schema，也尚未传入 dcCart 的裁切/覆盖计算。无 `pairUid` 时不得凭同一 `faceZone`、法向、距离或外接框推断两个独立面是同一物理壁；具体失败反例和待实现契约见 [dcCart 配对说明](../archive/dccart-source-validation-2026-09-27/dccart-pairing-contract-counterexamples-2026-09-27.md)。

---

## 5. 边界绑定 `boundaryBindings`

`boundaryBindings` 是 OpenFOAM Parser 生成 `0/` 字段的主要输入。它保存规则应用后的结果，不保存 parser 规则库。

```text
boundaryBindings[uid]
  ├── patchName
  ├── solidName
  ├── objectPath
  ├── surfaceUid
  ├── role
  ├── bcType
  └── parameters
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `patchName` | `string` | OpenFOAM patch 名。 |
| `solidName` | `string` | STL solid 名。 |
| `objectPath` | `string` | 来源领域对象路径。 |
| `surfaceUid` | `string` | 来源 surface/opening/face uid。 |
| `role` | `string` | 领域角色。 |
| `bcType` | `wall, acu_supply, acu_return, fan_inlet, fan_outlet, nonfan_fixed_temp, nonfan_heat_flux, fixedTemperature, heat_source_flux, perforated_baffle, wall_baffle, through_zone` | 后端 field generator 支持的边界类型。（2026-06-12 修订：补全现有 `fields.py`/`bridge_bc.py` 已实现的 `nonfan_fixed_temp`、`nonfan_heat_flux` 与信息性 `through_zone`。） |
`parameters` 是 **AD8 camelCase 词表**（`BoundaryBindingParameters`，全字段 Optional，`extra="forbid"`）。`cfdrun_reader.py` 把它逆映射回 `bridge_bc.BCSpec` 的 snake_case 字段（注释中标出对应关系）。

| 字段 | 类型 | 对应 BCSpec | 说明 |
|---|---|---|---|
| `parameters.temperatureC` | `number` | `temperature` | 温度边界，单位 °C。 |
| `parameters.flowRateM3s` | `number` | `flow_rate` | 体积流量，单位 m³/s。 |
| `parameters.powerW` | `number` | `power` | 功率，单位 W。 |
| `parameters.sigma` | `number` | `sigma` | 多孔/挡板等效开孔系数。 |
| `parameters.perforation` | `Perforation` | `perforated` | 多孔板物理描述（`shape`/`holeDiameterMm`/`openAreaPct`，见 §7）。 |
| `parameters.heatFluxWm2` | `number` | `heat_flux` | 热通量，单位 W/m²。 |
| `parameters.physicalFlowAreaM2` | `number` | `physical_flow_area` | 物理流通面积，单位 m²，用于 fan patch 湍流和速度换算。 |
| `parameters.surfaceAreaM2` | `number` | `surface_area` | 该 patch 表面积，单位 m²。 |
| `parameters.coolingCapacityW` | `number` | `cooling_capacity` | ACU 制冷量，单位 W（= kW×1000）。 |
| `parameters.minSupplyTempC` | `number` | `min_supply_temp` | ACU 最低送风温度，单位 °C。 |
| `parameters.setPointTempC` | `number` | `set_point_temp` | ACU 控制设定温度，单位 °C。 |
| `parameters.coolantTempC` | `number` | `coolant_temp` | ACU 冷冻水/冷却液温度，单位 °C。 |
| `parameters.returnPatchName` | `string` | `return_patch_name` | ACU supply 对应的 return patch。 |
| `parameters.coolingModelMode` | `string` | `cooling_model_mode` | ACU 冷却模型模式（`fixedTemp`/`linearCapacity`/`tableCapacity`）。 |
| `parameters.ratedReturnTempC` | `number` | `rated_return_temp` | ACU 额定回风温度，单位 °C。 |
| `parameters.coolingPerformanceTable` | `array` | `cooling_performance_table` | ACU 性能曲线表（`tableCapacity` 模式）。 |
| `parameters.timeVarying` | `object \| null` | `timeVarying` | 时变输入表。物化时空 `{}` 归一化为 `null`。 |

> **bcType ↔ parameters 对应：** `acu_supply` 用 temperature/flowRate/coolingCapacity/minSupplyTemp/setPointTemp/coolantTemp/returnPatchName/coolingModelMode/ratedReturnTemp/coolingPerformanceTable；`acu_return` 用 returnPatchName；`fan_inlet`/`fan_outlet` 用 temperature/flowRate/physicalFlowArea；`nonfan_fixed_temp` 用 temperature；`nonfan_heat_flux`/`heat_source_flux` 用 heatFlux/surfaceArea；`perforated_baffle`/`wall_baffle` 用 sigma/perforation；`wall`/`through_zone` 通常无参数。

---

## 6. 源项 `sourceTerms`

```text
sourceTerms[uid]
  ├── type
  ├── objectPath
  ├── region
  └── parameters
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | `heatSource, heatSink, liquidHeatExchange, rdhxHeatExchange, porousZone` | 源项类型（`SourceTermTypeLiteral`）。 |
| `objectPath` | `string` | 来源领域对象路径。 |
| `region` | `string` | OpenFOAM cellZone、patch 或几何区域名称（porousZone 用 cellZone 名）。 |
| `parameters.powerW` | `number` | 热源或冷源功率，单位 W。 |
| `parameters.temperatureC` | `number` | 固定温度或参考温度，单位 °C。 |
| `parameters.flowRate` | `number` | 与源项相关的流量。 |
| `parameters.schedule` | `object` | 时变源项。 |
| `parameters.surfaceAreaM2` | `number` | 热源表面积，单位 m²，用于热通量换算。 |
| `parameters.timeVarying` | `object \| null` | 时变源项表。 |

**porousZone 源项的 Darcy–Forchheimer 参数**（隔墙孔等阻力区，由浏览器预计算几何）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `parameters.sigma` | `number` | 等效开孔率，用于 Darcy 系数 `f=(1/depth)·((1/(0.6·σ))−1)²`。 |
| `parameters.d` | `number[3]` | Darcy 黏性阻力系数 `[dCoeff, 0, 0]`（多孔区 d 为 0）。 |
| `parameters.f` | `number[3]` | Forchheimer 惯性阻力系数 `[fCoeff, 0, 0]`。 |
| `parameters.origin` | `number[3]` | 阻力区坐标系原点 `[x, y, z]`。 |
| `parameters.e1` | `number[3]` | 阻力区第一基向量 `[x, y, z]`（流向）。 |
| `parameters.e2` | `number[3]` | 阻力区第二基向量 `[x, y, z]`。 |

---

## 7. 多孔与泄漏 `porousBindings`

```text
porousBindings[uid]
  ├── patchName
  ├── objectPath
  ├── surfaceUid
  ├── type
  └── parameters
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `patchName` | `string` | OpenFOAM patch 或 baffle patch 名。 |
| `objectPath` | `string` | 来源领域对象路径。 |
| `surfaceUid` | `string` | 来源 surface/opening/face uid（`PorousBinding.surfaceUid`，Optional）。 |
| `type` | `perforatedBaffle, wallBaffle, cyclicOpening, porousZone, leakageZone` | 多孔/泄漏类型。 |
| `parameters.perforation.shape` | `circle, rect, diamond` | 开孔形状。 |
| `parameters.perforation.holeDiameterMm` | `number` | 孔径，单位 mm。 |
| `parameters.perforation.openAreaPct` | `number` | 开孔率，0 到 100。 |
| `parameters.sigma` | `number` | 等效开孔或泄漏系数，0 到 1。 |
| `parameters.seedConfig` | `object` | baffle 或 region 的种子点配置。 |

---

## 8. 网格配置 `meshConfig`

`meshConfig` 属于运行任务配置。它描述本次 OpenFOAM case 如何划分网格；默认值来自 `.cfdproj.runDefaults.meshConfig`，导出时被运行面板的临时修改覆盖后冻结。

**全字段表**（`MeshConfig`，`extra="forbid"`，全部 Optional）。JS `buildManifest` 把 `state.runDefaults.meshConfig` 逐键深拷贝，所以任何 case 出现过的键都在此正式声明。

| 字段 | 类型 | 说明 |
|---|---|---|
| `baseSize` | `number` | 背景网格基础尺寸，单位 m。 |
| `detectBaseSize` | `number \| null` | 自动检测 refinement level 使用的参考尺寸。 |
| `refinementLevel` | `integer[]` | 全局表面 refinement level `[min, max]`。 |
| `rackRegionLevel` | `integer` | rack `searchableBox` 区域 refinement level。 |
| `parallelMesh` | `boolean` | 是否并行网格。 |
| `meshProcess` | `integer` | 网格并行进程数。 |
| `locationInMesh` | `Vertex \| {x,y,z}` | 主流体区域种子点。 |
| `extraLocationsInMesh` | `array` | 额外流体区域种子点。 |
| `meshBaseSize` | `number` | V4 state 中 `baseSize` 的别名。 |
| `meshDetectBaseSize` | `number` | V4 state 中 `detectBaseSize` 的别名。 |
| `maxGlobalCells` | `integer` | snappyHexMesh 全局最大单元数。 |
| `maxLocalCells` | `integer` | 每进程最大单元数。 |
| `blockMeshBounds` | `object \| null` | 手动 blockMesh 包围盒（null = 自动）。 |
| `blockMeshEditActive` | `boolean` | UI：blockMesh 编辑模式是否激活。 |
| `blockMeshMoveMode` | `boolean` | UI：blockMesh 拖动模式。 |
| `locInMeshVisible` | `boolean` | UI：种子点球体是否可见。 |
| `refinementZones` | `array` | 手动 refinement 区域列表。 |
| `acuRefinementLevel` | `integer[]` | ACU 表面 refinement level。 |
| `acuFeatureLevel` | `integer` | ACU 特征边 refinement level。 |
| `boxRefinementLevel` | `integer[]` | box 表面 refinement level。 |
| `boxFeatureLevel` | `integer` | box 特征边 level。 |
| `baffleFeatureLevel` | `integer` | 挡板特征边 level。 |
| `wallBafflesRefinementLevel` | `integer[]` | 墙挡板表面 refinement level。 |
| `floorRefinementLevel` | `integer[]` | 地板表面 refinement level。 |
| `floorFeatureLevel` | `integer` | 地板特征边 level。 |
| `defaultFeatureLevel` | `integer` | 默认特征边 level。 |
| `partitionWallRefinementLevel` | `integer[]` | 隔墙表面 refinement level。 |
| `partitionWallFeatureLevel` | `integer` | 隔墙特征边 level。 |

网格引擎与 dcCart 尺寸键（2026-09 起加入；前端产品预设见 `src/frontend/shared/js/mesh_sizing.js` 的 `dcCartProductMeshConfig`，语义见 [网格引擎](mesh-engines.md)）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `meshEngine` | `snappy \| dcCart` | 生成网格的程序。缺省或不认识的值按 `dcCart` 处理（`_resolve_mesh_engine`）；`meshgen` 已于 2026-10-26 停用，schema 与 runner 直接报错拒绝，不换用其他引擎。 |
| `meshTimeLimitSeconds` | `number` | 网格计时目标，秒（缺省 300）。2026-10-20 起只计时和告警，不终止网格。 |
| `weldTolerance` | `number` | dcCart 近重合点焊接容差，m；缺省 1.2e-3，`0` 关闭。 |
| `firstLayerMinFraction` | `number` | dcCart 首层单元最小厚度占该级网格间距的比例；runner 缺省 0.3。 |
| `firstLayerMax` | `number` | 首层下限的上限，m；缺省 0.005。 |
| `firstLayerSealGap` | `number` | 首层下限允许封住的最宽固体间隙，m；`-1`（缺省）表示等于焊接容差。 |
| `rackCellMinSize` | `number` | 机柜区域单元不会被自动加密到低于此尺寸，m；产品预设 0.0175。 |
| `firstCellSize` | `number` | 目标近壁单元尺寸，m（换算成墙面/地面 level）。 |
| `meshMode` | `bodyFitted \| cartesianLocalized \| structured` | **已退役（2026-09-09）**：只为读取旧文件而保留，runner 忽略，语义由 `meshEngine` 决定。 |
| `cartesianMode` / `structuredGrid` | `boolean` | 同上，更早的布尔开关，已忽略。 |

---

## 9. 求解配置 `simulationConfig`

`simulationConfig` 属于运行任务配置。它描述本次 OpenFOAM solver 如何运行；默认值来自 `.cfdproj.runDefaults.simulationConfig`，导出时冻结。

**全字段表**（`SimulationConfig`，`extra="forbid"`，`populate_by_name=True`，全部 Optional）。`buildManifest` 把 `state.runDefaults.simulationConfig` 逐键深拷贝。

核心求解参数：

| 字段 | 类型 | 说明 |
|---|---|---|
| `solverType` | `steady \| transient` | 稳态或瞬态。 |
| `solverName` | `string` | 显式指定求解器名称；缺省时稳态用 `buoyantBoussinesqSimpleFoam_new`、瞬态用 `buoyantBoussinesqPimpleFoam_new`（仓库自建，见 `third_parties/foam/README.md`）。 |
| `parallelSolve` | `boolean` | 是否并行求解。 |
| `solveProcess` | `integer` | 求解并行进程数（`decomposePar` 子域数）。 |
| `endTime` | `integer` | **稳态迭代步数**或瞬态步数。稳态 controlDict 由此驱动（`generate_control_dict` 读取，缺省 500）。 |
| `endTimeSeconds` | `number` | 瞬态物理结束时间，单位 s（缺省 300）。 |
| `deltaT` | `number` | 时间步长。 |
| `writeInterval` | `integer` | 写出间隔。 |
| `roomTemperatureC` | `number` | 初始房间温度，单位 °C。 |
| `gravity` | `{x,y,z}` | 重力方向和大小（嵌套形式）。 |
| `residualControl` | `ResidualControlV2` | 残差阈值：`p_rgh`/`U`/`T`/`k_epsilon`（各为 number 或 string）。2026-10-20 起稳态收敛只看 ACU 回风温度是否平稳在 0.5 K 内，残差只作诊断写进 `convergence.json`；稳态自动停算也以回风判据为准。 |
| `schemes` | `Record<string,string>` | 离散格式覆盖。 |
| `relaxationFactors` | `Record<string,number>` | 松弛因子覆盖。 |
| `monitoring` | `object` | 监控面配置（serverTemperature/acuReturnTemperature patch 列表）。 |
| `startFrom` | `latestTime \| startTime` | 起始时间策略。 |
| `solverPreset` | `string` | 求解预设（`fast`/`balanced`/…）。 |
| `writePrecision` | `single \| double` | 写出精度。 |

时间步进与格式附加项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `doublePrecisionWrite` | `boolean` | 是否双精度写出。 |
| `maxDeltaT` | `number` | 瞬态最大时间步。 |
| `maxCo` | `number` | 最大 Courant 数。 |
| `writeIntervalSeconds` | `number` | 瞬态物理写出间隔，单位 s。 |
| `adjustTimeStep` | `boolean` | 是否自适应时间步。 |
| `nOuterCorrectors` | `integer` | PIMPLE 外迭代次数。 |
| `nCorrectors` | `integer` | 压力修正次数。 |
| `ddtScheme` | `string` | 时间离散格式（`Euler`/`CrankNicolson`）。 |
| `crankNicolsonCoeff` | `number` | CrankNicolson 系数。 |
| `gravityX` / `gravityY` / `gravityZ` | `number` | 重力分量（扁平形式，与 `gravity` 对象并存）。 |
| `nonOrthoCorrection` | `boolean` | 是否非正交修正。 |
| `nNonOrthogonalCorrectors` | `integer` | 非正交修正次数。 |
| `transientResidualControl` | `ResidualControlV2` | 瞬态每个时间步内的外迭代收敛阈值（E8c），与稳态的 `residualControl` 分开设置。 |
| `pressureLinearSolver` | `{preconditioner?: DIC \| GAMG, tolerance?, relTol?, maxIter?}` | 可选的 `p_rgh` PCG 线性求解器设置；缺的项沿用所选预设。 |

**每单元 refinement/feature level 字典**（V4 state 中携带在 `simulationConfig`，逐键声明以保证 Pydantic round-trip 干净）。这些键的值多为 `integer`、`integer[]` 或按 slot 字符串索引的字典（例如 `rackDoorPerMinUnitLevel = {"2": [3,4], "3": [3,4]}`）：

`rackRefinementLevel`、`rackFeatureLevel`、`serverFaceRefinementLevel`、`serverFacePerUnitLevel`、`serverFaceFeaturePerUnitLevel`、`serverFaceFeatureLevel`、`rackDoorRefinementLevel`、`rackDoorFeatureLevel`、`rackDoorPerMinUnitLevel`、`rackDoorFeaturePerMinUnitLevel`、`leakageRefinementLevel`、`leakageFeatureLevel`、`blankingPlateRefinementLevel`、`blankingPlatePerUnitLevel`、`blankingPlateFeatureLevel`、`blankingPlateFeaturePerUnitLevel`、`rackPartitionRefinementLevel`、`rackPartitionPerUnitLevel`、`rackPartitionFeatureLevel`、`rackPartitionFeaturePerUnitLevel`、`rackThroughZoneFeatureLevel`、`rackThroughZoneFeaturePerUnitLevel`、`rackThroughZonePerUnitLevel`、`rackLeakageFeatureLevel`、`rackLeakageFeaturePerUnitLevel`、`rackLeakagePerUnitLevel`、`partitionWallRefinementLevel`、`partitionWallFeatureLevel`。

> `rackDoorFeaturePerMinUnitLevel`（按 slot 字符串索引的门特征 level 字典，如 `{"1": 4, "5": 2}`）由 P4 全 case 扫描在 Base_coldAisleIso 上暴露——5 个基线 fixture 没有带 per-unit 门特征 level 的机柜门，故 schema 一度漏声明。

其它：

| 字段 | 类型 | 说明 |
|---|---|---|
| `extraLocations` | `array` | 额外 locationInMesh 列表（state 副本）。 |
| `_nextExtraLocId` | `integer` | extra location 自增 ID（Pydantic 用 `nextExtraLocId` 别名，因为字段名不能以下划线开头）。 |
| `meshConfig` | `object` | V4 state 遗留：`simulationConfig` 内嵌的 meshConfig 副本。 |
| `baseSize` | `number` | **vestigial**：v3→v4 迁移把它落到 `simulationConfig`（实际归属 `meshConfig.baseSize`），runner 忽略，仅为通过 `extra="forbid"` 而声明（P4 扫描在 GTC 暴露）。 |
| `polygon` | `any` | **vestigial**：房间多边形残留（实际归属 `geometry.plane`），runner 忽略，仅为通过 `extra="forbid"` 而声明（P4 扫描在 paintToDC1 暴露）。 |

---

## 10. 后处理配置 `postprocessConfig`

```text
postprocessConfig
  ├── probes
  ├── surfaceMonitors
  ├── outputFields
  └── labels
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `probes` | `Record<uid, object>` | 从 `.cfdproj.instrumentation.probes` 编译来的 probe。 |
| `surfaceMonitors` | `Record<uid, object>` | patch 平均温度、流量、压差等监控面。 |
| `outputFields` | `string[]` | 后处理输出字段，例如 `T`、`U`、`p_rgh`。 |
| `labels` | `object` | 来自 `inputs.labels` 的对比标签或实测值。 |

---

## 11. 运行产物 `artifacts`

```text
artifacts
  ├── caseDir
  ├── modelDir
  ├── triSurfaceDir
  ├── openfoamCaseDir
  ├── liquidResultsPath
  └── logs
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `caseDir` | `string` | case 根目录。 |
| `modelDir` | `string` | `.cfdrun` 和模型文件目录。 |
| `triSurfaceDir` | `string` | OpenFOAM triSurface 目录。 |
| `openfoamCaseDir` | `string` | OpenFOAM case 目录。 |
| `liquidResultsPath` | `string, null` | 液冷求解结果路径。目前没有读取方：风液耦合改走 `/api/run-mesh` 请求体里的 `hybridHeat` 交接（`hybrid_air_heat_handoff.py`），由服务端把已验收液冷运行的热量写进派生的风冷 `.cfdrun`。 |
| `logs` | `Record<string, string>` | mesh、solve、postprocess 等日志路径。 |

（2026-06-12 修订）`artifacts.*` 路径由**服务端**在 `_write_active_cfdrun` 写盘时填充/更新——浏览器不可靠地知道绝对 case 路径，服务端知道。前端 translator 输出该字段为空骨架。

---

## 12. 不应进入 `.cfdrun` 的内容

| 不应保存 | 原因 |
|---|---|
| 完整 `.cfdproj.models` | 模型库属于编辑态。`.cfdrun` 只保存本次运行解析后的参数。 |
| 完整 `.cfdproj.constructions` | 设备树属于领域模型。`.cfdrun` 保存 artifact、输入和绑定结果。 |
| UI 状态、相机、选择、undo history | 与运行无关。 |
| OpenFOAM parser 规则库 | 规则属于后端代码或配置。`.cfdrun` 保存规则应用后的结果。 |
| 会随 `.cfdproj` 编辑变化的缓存 | `.cfdrun` 生成后应稳定。需要更新时重新导出。 |

---

## 13. 与当前 `.cfdrun` 的兼容迁移

当前 `.cfdrun` 使用 `racks`、`acus`、`baffles`、`floors` 等字段同时表达几何、运行输入和边界条件。V2 可以按以下方式渐进迁移：

| 当前字段 | V2 去向 |
|---|---|
| `racks.*.servers.*.physics` | `inputs.servers` + `boundaryBindings` + `sourceTerms`。 |
| `acus.*.supplyFaces.*.physics` | `inputs.acus` + `boundaryBindings`。 |
| `heatSources` | `sourceTerms`。若来源是 server 内部 chip，应回到 `Server.constructions.chips` 后再编译。 |
| `baffles.physics`、`floors.openings.physics` | `geometryExport` + `porousBindings`，不进入 `inputs`。 |
| `sensorPoints` | `postprocessConfig.probes`。 |
| `meshConfig`、`simulationConfig` | 保持为运行态配置。 |

迁移策略（2026-06-12 修订，硬切换；2026-10-21 补注：现在接受 2、3、4，见 §15）：**Runner 不接受 v1**。旧 `.cfdrun`（version "1.0"，无 schemaVersion 字段）不做迁移、不做双格式兼容——遇到旧格式直接报错并提示"请在编辑器中重新导出"。`.cfdrun` 本就是从 `.cfdproj` 一键再生的派生产物，重新导出的成本远低于维护双格式解析。上表仅作为实现 translator 时的字段对照参考。`case/Slab_dataset/` 的 1000 个 v1 `.cfdrun` 标记为过期产物，需要时按文档化流程批量重导出。

---

## 14. 过渡块 `meshGeometry`（已实现，spec §12 已知偏差）

`meshGeometry` 是根级键，类型为 `object | null`（Pydantic 中是 `Dict[str, Any]` 直通，不做严格字段校验）。它存在的原因：V2 把几何域模型挪进了 STL 文件，但 **runner 的网格管线仍按 v1 几何 blob 工作**（封闭房间 `_ensure_watertight_room_enclosure`、`createBaffles`、背景网格包围盒、refinement、隔墙孔 `fvOptions` 等读取 `self._data` 的 racks/floors/baffles/room/partitionWalls/…，都在 `stlgen_runner.py`）。把这些全部迁到纯 V2 几何仍是后续工作（见下文"迁移进度"）。

**数据来源与读取路径：**

- 浏览器导出时先用 `cfdrunExport.buildManifest()` 生成并校验纯 V2 manifest，**之后**才把 `buildCfdrunJson()`（共享模块 `src/frontend/shared/js/cfdrun_build.js`）产出的 blob 挂到 `meshGeometry`，所以过渡块**有意绕过**客户端的 V2 `validateManifest` 校验。React 导出 v4 时还会删掉 blob 里的 `simulationConfig`，把只有它带的房间温度与监控配置移到根级（`buildManifestV2`）。
- runner 有两个校验站点，代码相同：`_load()` 与 `_load_cfd_json_only()`（`validate_geometry()` 与仅网格预检路径）。两者都执行 `CFDExport.model_validate(execution_blob(raw))`，再 `model_dump(mode="json")` 得到 `self._data`。`execution_blob()`（`derived_config.py`）对 v2/v3 原样返回 blob；对 v4 要求 blob 里**没有** `simulationConfig`、根级带 `roomTemperatureC`，并由根级 `simulationConfig` 推导出执行用的那一份放回 blob。`meshGeometry` 为 null 或缺失时得到空 `CFDExport`（空几何），不会抛 `KeyError`。
- BC registry 走 V2 路径：`cfdrun_reader.resolve_run()` 物化 `boundaryBindings`/`porousBindings`/`sourceTerms` → `bridge_registry`。

**结构：** `meshGeometry` 的内部就是 v1 `.cfdrun` 的几何域（`racks`/`acus`/`baffles`/`floors`/`walls`/`partitionWalls`/`wallObjects`/`heatSources`/`room`/`sensorPoints`/`meshConfig`/`simulationConfig`（v4 无），以及 `version`/`source`/`projectName`）。其中 **`sensorPoints`** 承载后处理探针（驱动 controlDict `probeLocations` 与传感器采集），迁移时不可遗漏。字段语义见 `cfdrun_schema.py` 里的 `CFDExport` 与旧版 [.cfdrun（v1）Schema](legacy/cfdrun-schema.md)。blob 是 `buildCfdrunJson()` 的**派生形状**（嵌套 `gravity {x,y,z}`、计算出的 `blockMeshBounds`、`monitoring`、`roomTemperature`），与根级/编辑器原始配置的扁平形状不同，两者不能互相当作对方读取。

**实现要点（务必牢记）：**

1. **求解实际使用的 `simulationConfig`：** v2/v3 取 `meshGeometry.simulationConfig`（根级那份 runner 不读），手工给稳态限步时要改 blob 里的 `endTime`；v4 只有根级一份，改根级即可。
2. **`meshConfig` 仍取 blob 的那一份。** 根级 `meshConfig` 由同一份工程设置导出，`DCDT_CFDRUN_V3_SHADOW=1` 时 runner 会比对后端推导值与 blob 是否一致（`derived_config.shadow_drift`，v4 只比 `meshConfig`）。
3. `meshGeometry` 是**待移除的过渡设计**：网格管线迁到纯 V2 几何后即可删除。

**迁移进度（截至 2026-10-21）：** cfdrun V3 薄契约设计（`docs/superpowers/specs/2026-07-08-cfdrun-v3-thin-contract-design.md`）的 Stage 1（后端推导配置 `derived_config.py` 与影子比对）已完成；Stage 2 中房间温度与监控配置的根级化已由 v4 完成。下一步是零丢失设置审计并把 `meshConfig` 也收到根级，然后是 `geometryExport.meshHints`（Stage 3）与删除几何 blob（Stage 4）。

**执行预算一致性守卫（2026-09-27）：** 在过渡设计消除之前，`_load()` 与 `_load_cfd_json_only()` 会在启动 OpenFOAM 前检查根级与实际生效的 `meshGeometry` 是否同时给出且一致的 `parallelMesh`、`meshProcess`、`maxGlobalCells`、`parallelSolve`、`solveProcess`、`endTime`、`writeInterval` 和 `startFrom`。任一成对字段不一致即明确拒绝运行；历史文件某侧缺失字段仍按原兼容路径处理。手工调整派生 `.cfdrun` 的 CPU 进程数或求解时域，必须同时更新两份对应字段；更推荐从 `.cfdproj` 编辑并重新导出。Shinaide 的一次隔离网格试验正是只修改根级 `meshProcess=4`、兼容块仍为 8，实际启动了八个 MPI 进程；相关失败与修正后的执行记录见 [风冷链路验收](../benchmarks/shinaide-air-smoke-2026-09-27.json)。React 导出器的 Shinaide 回归测试还验证：从 `.cfdproj` 修改网格/求解进程数及求解时域后，导出的根级与兼容块执行字段逐项一致，并通过严格校验。

---

## 15. v3 与 v4

| 版本 | 引入 | 相对前一版的变化 | 谁产生 |
|---|---|---|---|
| 2 | 2026-06-14 | 基线（本文 §1–§14）。不得带 `transient`，边界绑定不得带时间表。 | 旧 HTML 编辑器 |
| 3 | 2026-09-30（瞬态关键帧 T4） | **必须**带根级 `transient` 块（`durationS`、`start`）；`boundaryBindings[*].parameters.schedules` 可以给可调度参数写时间表。仍然有两份 `simulationConfig`，runner 执行 blob 那份。 | 早期瞬态导出；现在的 React 不再产生 |
| 4 | 2026-10-01（E8a） | **只有根级一份 `simulationConfig`**：`meshGeometry` 里不得再有 `simulationConfig`，根级必须带 `roomTemperatureC`，`monitoring` 也在根级；runner 用 `execution_blob()` 由根级推导执行配置。`transient` 变为可选：稳态运行不带；带时间表就必须带 `transient`；带 `transient` 时 `solverType` 必须是 `transient` 且 `endTimeSeconds` 等于 `transient.durationS`。 | React 编辑器、无头编译器 |

**时间表（v3/v4）：** `boundaryBindings[*].parameters.schedules` 是 `{参数名: [[t, value], …]}`。可调度的参数只有 `temperatureC`、`flowRateM3s`、`directionWorld`、`supplyAngleDeg`、`powerW`、`heatFluxWm2`。行按时间不减，同一时刻最多两行（即阶跃），时间落在 `[0, transient.durationS]` 内。这些行就是 cfdproj v5 轨道的角点表（见 [.cfdproj v5 参考](cfdproj-v5-schema.md) §2.2），runner 把它们写成 OpenFOAM 的时间表边界条件，并用 `transient.start` 决定从稳态收敛场还是均匀初场出发。

**React 导出流程（`buildManifestV2`）：** 先把工程物化到 t = 0 编译出稳态 manifest；空气侧为瞬态时，再按时间轴采样出各绑定的时间表并加上 `transient`；然后挂上 `meshGeometry`、把根级 `simulationConfig` 补上 `roomTemperatureC` 与 `monitoring`（瞬态时 `endTimeSeconds` 取 `durationS`），最后写 `schemaVersion = 4`。

---

## 修订记录

| 日期 | 修订 |
|---|---|
| 2026-06-10 | 初稿（V2 提案）。 |
| 2026-06-12 | 设计评审修订：生成位置固定为浏览器内、实时 UI 状态快照（前言）；Pydantic 全量声明严格校验，`meshConfig`/`simulationConfig` 不再有未声明字段（§8、§9）；`bcType` 枚举补全 `nonfan_fixed_temp`/`nonfan_heat_flux`/`through_zone`（§5 及映射表）；`StlSolidBinding` 新增 `areaM2`/`perimeterM`（§4.2）；`contentHash` 定义为实时状态 SHA-256（§2）；新增 `inputs.liquid`，耦合修复明确延期（§3.7）；`artifacts` 由服务端填充（§11）；§13 改为硬切换（不兼容旧格式）。 |
| 2026-06-14 | **实现版对齐（Plan 3/4 完成）。** 与 `cfdrun_schema.py` 逐字段对齐：§1 顶层新增 `meshGeometry`（第 14 键）并标注求解配置实际来源；§5 `boundaryBindings.parameters` 补齐 AD8 全词表（sigma/perforation/surfaceArea/ACU 全套/coolingPerformanceTable）并加 bcType↔parameters 对应；§6 `sourceTerms.parameters` 补齐 porousZone Darcy 全字段（sigma/d/f/origin/e1/e2）；§8 `meshConfig`、§9 `simulationConfig` 扩成全字段表（含每单元 refinement/feature level 字典与 `_nextExtraLocId` 别名）；新增 **§14 `meshGeometry` 过渡块**说明。5 例 §9.4 验收通过、registry 全等。 |
| 2026-06-12 (D7) | schema 独立成文件：前端拆分为 `js/cfdrun_schema.js`（结构）+ `js/cfdrun_export.js`（translator）；后端新增专用 `cfdrun_schema.py`（Pydantic）+ `cfdrun_reader.py`（manifest + STL 解析 adapter），`STLGenRunner` 只消费 `ResolvedRun`（前言实现文件划分表）；验证既有 case 必须通过 GPU WebGL 版 browser-use（设计文档 §9.4）。 |
| 2026-07-08 | **§14 审计修订。** 更正过时行号（`_load` 由 `stlgen_runner.py:1823` → `1963`，校验在 `:1980`）；补第二个校验站点 `_load_cfd_json_only()@1142`（校验 `:1159`，mesh-only 预检路径）；更正校验片段为 null-safe `CFDExport.model_validate(raw.get("meshGeometry") or {})` + `model_dump(mode="json")`；结构列表补 `sensorPoints`（后处理探针）/`version`/`source`/`projectName`；补前端导出 `buildManifest` 先校验、后附加 `meshGeometry` 从而绕过 13-key 校验的说明；`self._data` 读取点计数精确到 46 站点 / 45 引用。 |
| 2026-10-21 | 按现行代码同步：前言改为接受 v2/v3/v4、React 导出 v4；§1 补 `transient` 根键并改写 `meshGeometry` 一行；§8 补网格引擎与 dcCart 尺寸键，标注已退役的 `meshMode`；§9 补 `transientResidualControl`、`pressureLinearSolver`，`solverName` 缺省为 `*_new`，收敛判据改为回风温度；§14 按 `execution_blob()` 改写读取路径、去掉失效行号并写明迁移进度；新增 §15 v3 与 v4。 |
