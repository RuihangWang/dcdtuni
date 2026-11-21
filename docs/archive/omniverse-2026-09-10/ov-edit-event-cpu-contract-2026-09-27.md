# OV 编辑事件 V1：CPU 契约候选

本阶段只在隔离分支 `codex/ov-edit-bridge-cpu-20260927@191d441b` 实现事件校验与 React 事务映射，未连接 Kit 事件源或浏览器中的 OV 画面；不能称 OV 拖拽已可用。源工程仍为 `.cfdproj`，事件只能修改已经加载的工程构件，不能写 `.cfdrun`、`.liquidrun` 或结果层。

共享契约是 `src/frontend/shared/schema/ov-edit-event.schema.json`。每个事件含 `sceneRevision`、使用现有 `.cfdrun` 导出器稳定算法计算的 `projectHash`、快照 `primitive.id`、`modelUid`、`action`，以及模型**锚点**在工程坐标中的刚体世界位姿。坐标只接受 Y-up、右手、1 米/单位；不猜测毫米、Z-up 或 USD 子 prim 的偏移变换。位姿带 position、quaternion、scale；移动只改变 position，旋转仅接受启用的 yaw/pitch/roll 轴，其他分量必须与源工程一致。一个事件是一笔完成的操作，不是连续拖拽帧。

`createOvEditBridge` 对已验收的 `SceneSnapshot` 捕获工程哈希与 store 修订，并要求调用方提供当前场景修订、与 Three viewport 相同的编辑冻结判定和**显式源几何 primitive 白名单**。它先拒绝陈旧修订/工程、冻结状态、未列入快照的 prim、白名单外的结果层、UID 不匹配、坐标或位姿错误、关闭的 gizmo 轴，再调用 `projectStore.setModelLocation` 或 `projectStore.applyModelTransform`。两者已有单步撤销。选择事件调用现有 `onSelection`。无白名单就没有可编辑 prim；当前 RenderScene 还没有可证明来源类别的字段，因此绝不能把所有同 UID 的 primitive 自动设为可编辑。管段的移动/旋转涉及 waypoint 和连接，V1 明确拒绝。

接入前还需完成三件事：Kit 在 scene load ACK 后保存并回传 `sceneRevision/projectHash/primitiveId`，区分真实源几何与结果/网格/流线 prim；React 从同一快照的来源信息生成白名单，并在每次场景替换或结果层变化时更新修订；`OvViewport`/`RendererControl` 把 Kit 事件交给桥并处理拒绝、重新同步及交互预览。只有随后用实际 OV 视频画面测试 pick、移动、旋转、几何新增、网格与后处理，才能宣称渲染引擎切换满足用户目标。本 CPU-only 分支没有做这些集成，也没有使用 GPU。

定向 Vitest mock 测试 `test/ovEditBridge.test.ts` 覆盖 pick、移动/旋转与单步撤销、陈旧身份、来源白名单、坐标/轴/位姿拒绝。React 全量检查会按设计报 `renderer/ovEditBridge.ts` 为 orphan；这是未接入 UI 的真实状态，未修改 `orphanModules.test.ts` 来掩盖它。本候选在事件源与白名单接线前不得合入主开发分支。
