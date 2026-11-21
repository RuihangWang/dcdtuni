# Domain 阶段 2：物理端口、运行内存快照与显示载体的最小接入方案

日期：2026-10-06。独立只读源码审查；本文是待实施契约和验收建议，未运行业务、测试、build、solver、checkMesh 或浏览器。源码身份绑定同目录 `source-identity.json`，逐行摘录在 `source-excerpts.txt`；精确 SHA 见文末。阶段 1 的实际数据枚举/兼容纯数据回执另在 `.codex-tmp/domain-object-independent-review-20261006/final-manifest.json`，不能代替本阶段物理和渲染验收。

## 当前可以确认的边界

1. **领域类已解决记录身份/恢复入口，尚未实现完整独立几何和物理能力。** `domain/BaseEntity.ts:23–65` 的 `record` 是原工程记录引用；`geometry()`/`physics()` 返回引用和声明袋；`70–135` 多数具体类仅继承。`readonly` 不是运行时冻结。后续必须继续通过 store 的 edit/undo/input clock 改工程，不能让 adapter 修改实体 record。
2. **液冷的图编译输入来自工程 JSON，未遍历 Three 场景。** `liquid/dynamicLiquidGraph.ts:52–81` 复制工程并恢复 kind、把稳定 UID 写入编译专用名字，调用唯一共享 graphgen；`59–75` 明确以 `cpCuPairs` 为冷板物理成员/顺序/数值真源，coldPlates 字典只回填 UID。不能因为 domain 同时枚举两种存储形态而生成双份冷板。动态编译随后加 CDU 泵/HX、阀、对象绑定和 v4 温升模式（`98–192`）。
3. **液冷仍依赖浏览器全局上下文和 Three 数学类型。** `legacy/liquidGraphgen.ts:25–26,58–68,87–88` 导入共享模块并临时写 `window.state`；`liquid/liquidRunGraph.ts:33–58` 用 descriptor guard 避免 accessor 冲突。`shared/js/liquid_graphgen.js:59–92` 从 `window.geometryPure` 取端口/局部点变换，缺失模型回 `[0,0,0]`，缺变换方法回 anchor；`geometry_pure.js:270–307,353–378` 实际使用 THREE.Vector3/Quaternion，但不需要 live scene。`liquid_graphgen.js:815–819` 错误依赖 console/alert/null。此债务应在现有唯一 compiler 的上下文入口修，不另造液冷编译器。
4. **React 新液冷 DAE 入口已有 RAM manifest → owned simulation。** `LiquidExperimentPanel.tsx:151–164` 保存所选实验意图后 compileSavedExperiment 并附 documentInputRevision；`liquidRuntimeClient.ts:87–96` POST `/api/liquid/runs`；`server/liquid_runtime_api.py:116–135` 传入 manager。`run_manager.py:165–179` 区分 DAE 与旧 runtime，`dae_runtime.py:35–42` 严格解析并产生独立 manifest/config hash 和 DAEExperiment。`run_manager.py:209–215,483–525` 在内存 simulation 建立后同步写输入/checkpoint/result/executed 工件，因此目前 admission 仍依赖工件写成功；不能称完全无落盘。旧 `server.py:5249–5258` 的 `/api/liquid/transient` 501 是历史端点，不能当作当前 React DAE 未接入的证据。JSON/checkpoint 没有 autograd tape（`dae_runtime.py:1–4`）。
5. **风冷 cfdrun 在浏览器内存里派生，但内容仍依赖 live Three 并以文件执行。** `run/runSubmit.ts:95–116` domain 只提供顶层 UID/export admission，随后需要 SceneBuilder 和 globalGroup；`legacy/cfdrunBuild.ts:207–242` 从 STL mapping、projectAtStart、时间轨道和 legacy geometry blob 生成 manifest。`shared/js/cfdrun_build.js:127–137` 房间缺省 bounding box 来自 Three graph；`1313–1332` grouped STL 依赖 window.allModels + SceneBuilder method，并缓存 `window._meshExportGroupedMap`。`runPayload.ts:147–154` 序列化派生 manifest 与 STL 哈希。`server.py:793–868,894–982` staging/lock/active manifest 写盘，`4583–4628` prepare-case 返回 `--from-cfdrun` CLI；`stlgen_runner.py:1899–1922` 重读文件并核 SHA。RAM 运行会话尚不存在这一入口，不能把 cfdrun 当已脱离文件接力的对象。
6. **风冷 v4 的唯一 simulationConfig 已在 root。** `legacy/cfdrunBuild.ts:227–242` 移除 meshGeometry.simulationConfig；`derived_config.py:171–199` 拒绝 v4 重复 config，从 root 派生执行 blob。v2/v3 兼容规则仍读旧 blob。后续不能把旧文档口径恢复为双份配置。
7. **OV JSON → Kit 内存 USD 已有真实代码通路，但 producer 仍是 live Three。** `sceneSnapshot.ts:68–78,416–450,497–502` 读取 Object3D/buffer/material/world matrix；primitive ID 含 Three child traversal path，模型树重排后没有独立组件身份保证。`RendererControl.tsx:214–224` 从 window.globalGroup 捕获 packet/hash；`ovProtocol.ts:333–391` 分块直发；Kit `render_scene.py:225–232` 在内存组 JSON，`378–402,448–492` CPU author 内存 stage/UID/customData/材质并设 Y-up、metersPerUnit=1，`extension.py:625–651` 绑定 ack project hash/revision。**贴图文件仍写 owned cache**（`render_scene.py:394–401`）；结果层分树与独立 provenance 检查已有实现。当前 USD 保存显示元数据，并不含可恢复完整工程的所有物理参数/端口/未知 JSON，不能从它恢复 cfdproj 或升级为第二物理真源。

## 最小契约（建议，不更改既有 wire schema）

### 1. Entity 与物理端口能力

新增 domain 纯数据能力模块，具体实体可委派到经过复用的 family adapter；避免一次重写所有 geometry builder。**不导入 Three、window、USD、store，也不调用 renderer。**

- `EntityAddress = {projectUid, sourcePath, uid, parentPath?, parentUid?}`：顶层 UID 和父层局部 UID 都保留；实际身份用 sourcePath+父关系，不用显示名、scene child index，也不把相同 coldPlate UID 镜像记录合并成唯一物理成员。
- `PortAddress = {owner: EntityAddress, localPortId, role, domain, medium}`；`PortFrame = {localPosition_m, localDirection, worldPosition_m, worldDirection}`。pose 为工程的 meter/Y-up/right-handed/location + quaternion + scale；复现现行 normalize quaternion、mirror scale 方向与 rack slot/manifold transform。供回端、server manifold 接点、管段 start/end、waypoint 和 fitting 都明确 ownership；connection 解析按 authored endpoint ID/valve id，未知 port 返回结构化诊断。没有几何依据时不能悄悄用原点/最近 mesh/名字猜物理连线。现行宽松历史端口 alias 可留显式映射并给诊断，拒绝任意字符串都默认 return/supply。
- `PhysicalCapability` 分清 `sourceDeclaration`、`resolvedValue`、`unit`、`parameterRef`、`origin = authored|profile|legacy-default`；节点/边 adapter 保留当前严格 schema 与 parameter pack 默认，**本阶段不新增物理参数/default、不重新调参**。SI 只在 compile boundary 转一次：C→K、kPa→Pa、L/min→kg/s、mL→m³、R_th→UA；原 cfdproj 字段/单位保持可恢复。
- `admission(project, entity, mode)` 分开 persisted activation、mode physics inclusion、viewport visibility、selection/highlight、result overlay。关闭显示或切换 OV 不能更改物理成员；也不能把所有保留的未知实体默认为可求解。未知物理/几何实体保持 load/save，但仍沿用当前 supported/export gate。
- `PhysicalTopology = {activeMembers, ports, connections, legacyIdBindings, diagnostics}`：保留现有 node/edge order 和 wire IDs，补 sourcePath 的映射；不顺手重命名 rack/server UID 拼接生成的 ID。cpCuPairs 只生成一次 cold_plate，CDU internals 与动态泵/HX 不能再生成重复边；定义库记录不等于 active body。

### 2. Owned RunSnapshot

内部 `RunSnapshot` 是 compile 后的执行输入和所有权封套；**cfdrun/liquidrun 是它的派生序列化形式，不是可反写的工程域对象**。

```
SourceStamp { projectUid, projectSchemaVersion, producerContentHash, documentInputRevision }
RunSnapshot { version, runId, sourceStamp, t0_s, manifestKind, manifest,
              entityPortBindings, geometrySourceBindings, inputHashes, diagnostics }
```

producerContentHash 继续使用既有 canonicalProjectForHash/projectContentHashSync，v5 tracks 的 disk form 规则保持；不要改成只 hash domain wrappers。源工程 hash、严格 manifest/config hash、实际 STL/geometry buffer hash 各有语义，不能互换。渲染 playhead time 不会改变 disk-form hash，所以 snapshot time 和时间轨道必须单独记录。在线命令在 accepted-input ledger 追加，不重写原 `.cfdproj` 或假装保存 autograd 状态。

capture 一次：在用户确认的源 input revision 上 compile 并将必要物理数据/缓冲的所有权交给 run；await 后重新检查 revision/generation，编辑导致结果 stale 应显式处理。不得把可变 BaseEntity.record 或 Three mesh/material 引用交给异步 job。可以复制必要执行输入或使用版本不可变工程视图；不能在每次枚举/每个端口/每帧做全工程 deep-clone。已有 `dynamicLiquidGraph.ts:52` 全工程 clone 和 cfdrun hash/transient copy 的重复成本是逐步优化项；优化前保证既有输出一致。

### 3. Geometry/Material carrier

`GeometryRecipe`（body/surface/polyline/imported、局部 transform、部件角色/源地址、参数/assetRef）、`MaterialRecipe`（物理显示材质字段、texture hash/reference、UV policy）属于 domain 派生声明；`TessellatedGeometry` 是有稳定 component ID 的纯 buffer+matrix+source binding。Three adapter 创建 Object3D；OV adapter 写现有 render-scene packet；Kit USD 仅消费 packet。所有 renderer 的 selection/camera/opacity/result tint 放 view/result state。custom STL/CSG/rack patches 继续复用单一 tessellation/patch-naming 内核，不能第二套几何重实现产生不同 OpenFOAM patch。

阶段 2 可暂保 `ThreeSceneExportAdapter` 作为明确过渡依赖；只在中间层标记 source/component ID 并抽取已有 buffers，不称已实现 renderer-independent geometry core。最终 stable component IDs 来自 source address + semantic part + instance/material group，不能用 child traversal index。保持 ownerUid/equipmentUid、valve-owner check、camera、material maps/uv/normals/vertex colors、实例矩阵和结果层 provenance/ACK 规则。贴图缓存、USD 文件导出与 solver 工件是显式载体 I/O，不参与物理 truth。

## 可执行顺序和验收（未执行）

**P2a，先建立纯物理/端口视图。** 在 domain 添加 immutable 值型 descriptor、pose/port 解析和 existing-parameter provenance。覆盖真实 CDU/rack/server/CP/pipe/valve/ACU/RDHX/face/patch 的现行参与者；先沿用现有分类/default/单位，未知/坏绑定给诊断。验收用已固定 LiquidSingle、Shinaide input 身份，对 authored sourcePath/parent/port ID 逐项比对；旋转、非均匀/镜像 scale、rack slot 高度、manifold layout、显式阀 ownership 一致。保存/重载 source hash、稳定 UID、全部物理字段与未知 JSON 的语义保持。本阶段不运行 solver。

**P2b，接入唯一 liquid graph compiler。** `liquid_graphgen.js` 加显式 compiler context（project/canonical models/port resolver/diagnostic sink）；移除编译中的 `window.state` 依赖和 alert side effect，React/legacy caller 传 context。现行旧入口可兼容包裹，不复制 compiler。dynamicLiquidGraph 继续产生相同 strict v3/v4 输入、object_bindings、parameter provenance、schedules 与 thermal mode。验收逐项对比既有实际 inputs 的 node/edge ID、数量、顺序、连接、位置、全部 SI 参数、pair membership、grid/time waveforms、source hash；与现有 strict DAE admission 同源。数学实现改纯数组时对齐现行 Quaternion normalization，不以近似视觉判断。只有之后业务回归通过才称 liquid 接入完成；MMS/梯度验证仍是后续科学证据。

**P2c，统一 frontend owned run snapshot，并接 wind/air compile seam。** 先去掉 groupedMap 的 window cache 和 window.allModels 读源，把 manifest/STL 返回同一个 owned compiled bundle；给 CfdrunContext 注入 ports/poses/bounds/tessellation lineage，从 domain/sourceAtStart 取物理而非 live Three。阶段性的 tessellation adapter 可以内部复用 Three，但 source revision/hash、mesh geometry/STL bytes 必须同一捕获批次，不能用当前显示模式/rack detail/材质决定空气 patch。验收 v4 root config、transient duration、meshConfig、boundary/porous/source terms、STL solid/patch名称/bytes/hash 和既有执行 blob 保持；solve-only 不重写 STL。对 mesh preflight/refusal 和 unknown export gate 不降级。

**P2d，air session 接执行边界。** 现在 prepare-case → shell CLI 是文件交接，若保留此链就只能称 frontend RAM snapshot，不能宣告全程 RAM。实现后端按 session/run ID 持有 accepted bytes/validated manifest 及 source-STL hashes，并让 runner 的 input provider 读 pinned owned bytes；CLI 文件入口继续兼容同一 validator，文件只用于显式 artifact/replay。PTY 中断需要 session owner/job token/cancel 状态与锁释放方案；跨进程边界采用已存在可验证通道或明确 server job owner，不新增依赖或偷偷写另一份中转 JSON 充当 RAM。OpenFOAM mesh/case/STL/log、源码 lineage/质量回执仍允许显式落盘；不把求解器文件 I/O 当工程真源。验收移除/改写旧 model/*.cfdrun 不会让 active accepted run 换输入，源编辑/重载不会污染运行，prepare 失败/取消/异常均解锁；后续真实 mesh 必须保留 checkMesh/收敛门和用户 0.25 baseSize/≤5min 目标，本文没有验证这些。

**P2e，逐类迁移 carrier + 浏览器终验。** 先统一 component identity/geometry+material recipes，再从相同 buffers 输出 Three/render-scene/USD，保持现有 wire v1 和 ack/source hash 协议；独立体/网格/result layers 不被模型树/renderer switch混淆。回归真实 rack/servers/CP、CDU/PDU、pipes/fittings、custom mesh、openings/patches 和材质贴图；allEntities 数不等于 render primitive 或 active solver body 数。React→Three↔OV、实体 shift pick/highlight、隐藏/显示、相机、结果时间/着色、展开UI持续cube等由 ROOT 在后续实际 browser-use 做验收。本文没有新建浏览器会话或声称显示通过。

## 明确保留的事实边界

- `.cfdproj` 是权威工程恢复来源，需保存 UID/物理参数/拓扑/unknown JSON；cfdrun/liquidrun/Three/USD 均为派生载体。显示级 USD 导出不自动带完整工程恢复或外部标准认证能力。
- differentiability 位于 PyTorch DAE tensor/implicit-adjoint 路径，不位于 React JSON/graphgen/端口 UI；本次未运行 MMS 或梯度实验。
- 当前 geometryTopology 声明仍被真实 air export 阻断（`cfdrun_export.js:1685–1688`）；在 source topology compile/validation完成前不因 domain 类存在就解除。
- 本方案不替代既有 mesh/STL/source provenance 验收，不调 solver/物性/default 来制造一致输出。

## 精确源码身份

所有 35 路径全文 SHA/bytes/lineCount/已读范围在 `source-identity.json`。下表是关键接口全文 SHA，不是缩写提交号；line 引用绑定 `source-excerpts.txt` 保存的同一读本。

| 路径 | SHA-256 |
|---|---|
| `src/frontend/react/src/domain/BaseEntity.ts` | `bc86e03fc5113900930e07d3b9a2af97b7312f5e9484ca4a0f58e4384dce7e30` |
| `src/frontend/react/src/liquid/dynamicLiquidGraph.ts` | `81f391a6d7173270c5d6aed09e6c01492160be1f8d0620a410d5c14175572ce6` |
| `src/frontend/shared/js/liquid_graphgen.js` | `592d29972f2b5c58f8000b5b796cbb9fca6e462c4045d5efc3873291e1b4e3b6` |
| `src/frontend/shared/js/geometry_pure.js` | `0444bcc31968577c25a1a097cc1fe129debb54054be7425ba262c4d414e46645` |
| `src/frontend/react/src/legacy/cfdrunBuild.ts` | `c43931a923c930747a1dc379ba400687576ceb17c7284908fb632c1f8670547c` |
| `src/frontend/react/src/run/runSubmit.ts` | `8afd5bf49db2877b462f61084b3805dafad6b1057ff2ccba84e1069c4eb6dd66` |
| `src/server/server.py` | `407d686f728094d90a421978f77bd0208a6fcf98fe560f6ee114912db81ce1dc` |
| `src/backend/aircooling/runner/stlgen_runner.py` | `742fe46371eccb97db029118c326b08c985dfa0e253ff4b5141e885c50457f86` |
| `src/backend/aircooling/runner/derived_config.py` | `a14a483efdabc836c5d3aa93855758ad685efb7627ae028f12ae8a40772ee689` |
| `src/backend/liquidcooling/dae_runtime.py` | `a574369e7bdc3e32468eae9b47b7d9582e59d5da83820e29c29f578ad33302ab` |
| `src/frontend/react/src/renderer/sceneSnapshot.ts` | `0277265af35686ebf979516ff8f1d11e62234fa0d4f204da641cf86088610132` |
| `src/frontend/omniverse/cfd_demo/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/render_scene.py` | `701a23d4867d3e33b58bb99356d7e2d7a97fe8b825b434a6f6a011bff000f79e` |

执行 guard：SSH taskset 28–31；CUDA_VISIBLE_DEVICES 空；TMPDIR 为本证据目录。只读取指定源码并写本文/摘录/identity/manifest；未改产品源码/tests/Git/服务/浏览器，未新增依赖。
