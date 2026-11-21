# React 迁移缺口 — 第四轮（对抗式审计）

> 归档注记（2026-10-21）：本登记的 39 条已全部在浏览器里验证（收口表已按后续提交更新），连同第一轮登记 [react-migration-gaps.md](react-migration-gaps.md) 一起归档。仍开放的只有：服务器风口之间的边缘吸附没有移植、Base604 选中延迟待复测、阀门落点吸附待用真实鼠标验证。当前前端状态见 [PROJECT_STATE.md](../../developer/PROJECT_STATE.md)。

审计日期 2026-08-31，在 legacy 编辑器删除之后进行（源码从 `legacy-html-editor-last-working`
取回作对照）。方法：8 个测量员按功能域并行枚举 legacy 的用户可见能力并在 React 中查找对应，
**每条候选再配一个专职反驳者**，任务是找出 React 的实现来推翻它，不确定时默认判为已反驳。

| | |
|---|---|
| 候选 | 42 |
| 经反驳后存活 | **39** |
| 被反驳（React 确实有） | 1 |
| 反驳者因 API 安全过滤失败、未核验 | 2 |
| 分级 | 大 3 / 中 21 / 小 15 |

## 收口状态（2026-09-01，2026-10-21 按后续提交更新）

| | |
|---|---|
| 已修并**在浏览器里验过** | **39 / 39** |
| 已接线但**没验成** | 0（原先剩下的机柜服务器面 patch 拖拽已在 2026-09-02 浏览器验证，见下） |
| 其中"早就修好、只是没标"的陈旧条目 | 7 |
| 顺手挖出并修掉的相邻缺陷 | 8（见每条的"顺手"段落） |

**2026-10-21 更新**：最后一条"已接线但没验成"的服务器面 patch 拖拽，已由 `a6c6baa81`（2026-09-02）修正分数坐标、钳位并补上边缘手柄后，在 GPU 浏览器里于 Base_leakage 上验证；`6ba767004` 与 `a98ce8cc2`（2026-09-17）又修好了近距离按到手柄、合并代理吃掉首次按下两处问题，并在旧版（8042）与 React（8040）两端、DMAL-Testbed-patched 上复验。服务器风口之间的边缘吸附仍按原决定没有移植（`faceDrag.ts` 对 `drag.server` 跳过吸附），这是唯一留下的功能差异。

**三条留下的观察**（都不是这轮的成果，写在这里免得被当成已解决；括号内是 2026-10-21 的现状）：
① Base604（142 机柜）点树里一行到重绘 **1.58 s**，与树行数无关（28,682 行 1583 ms / 14,296 行 1587 ms），一次树重渲染只占 373 ms，剩下约 1.2 s 在树以外，未查。（`5cf22c3f8`（2026-09-13）给特征树加了行窗口化，并把选中机柜时的整场景重建换成 `SceneBuilder.rebuildModels()`，在 Alibaba-hongchou 上实测从 699 ms 降到 5 ms；Base604 上还没有重新测量，下一步复测一次。）
② 平面体（wall/baffle/floor）的默认 region 名仍没有入口 —— `picking.ts` 对这些 kind 明确返回 null，而 legacy 的 `renderWallBaffleInspector` 里同样没有 `updateWallName`，两边一致。（现在点中平面体会选中它的面并打开模型面板，两端仍然都没有单独改默认区域名的入口，属于两端一致的现状，不是迁移缺口。）
③ 阀门拖拽的落点吸附（0.3 m 内写 `connectedRackId`）没能用合成事件复现：21×9 个采样里落点离目标端口最近 **0.595 m**，共享模块的几何未改动。（仍待验证：下一步用 browser-use 的 CDP 真实鼠标把阀门放到端口 0.3 m 以内，读回 `connectedRackId`。）

> **"缺口"的含义要看清。** 有些是能力真的没有（例：零厚度片体被写入厚度）；有些是
> 交互位置变了但能力还在（例：inspector 里没有删除按钮，但特征树行上有 ✕，Delete 键也能删）。
> 分级已经反映了这个区别，不要把 39 读成"39 个坏掉的功能"。

## 前两轮审计结构性看不见的一类：写好了、有测试、没人 import

本轮最强的几条共享一个签名——**React 的模块存在，甚至有专门的测试，但整个 app 没有任何地方
引用它**。前两轮问的是"React 有没有这段代码"（有，于是标 VERIFIED），没人问"运行中的 app
走不走到它"。机械核验（`grep` 全部 import 关系）确认了六个孤儿模块：

| 模块 | 行数 | 有测试 | 对应本轮条目 | 结局 |
|---|---|---|---|---|
| `components/AuthOverlay.tsx` | 161 | 否 | 登录浮层 —— 第一轮 G67 曾标记 ✅ VERIFIED | **本来就不是孤儿**：扫描器看不见 `run/auth.ts:229` 的动态 `import()` |
| `components/SensorEditDialog.tsx` | 109 | 否 | #20 传感器改名 / 精确坐标 | 已接入 `SensorPanel.tsx`（双击名字） |
| `geometry/duplicatePlacement.ts` | — | **是** | #2 复制体不重叠放置 | 已接入 `projectStore.duplicateInto` |
| `components/SheetSizeSection.tsx` | 66 | 否 | #1 零厚度片体尺寸 | 已接入 `Inspector.tsx` |
| `ui/inspectorTitle.ts` | 64 | **是** | inspector 分类标题（11 分支） | 已接入 `App.tsx` 两个面板标题 |
| `legacy/stlWriter.ts` | — | 否 | 共享 STL writer 的类型化访问器 | **删除**，没有接入：无消费者，而每一个 STL 字节都来自 `shared/js/geometry_scene.js` 读 `window.stlWriter` |

孤儿集合现在是空的，并由 `react/test/orphanModules.test.ts` 按**精确集合**断言守住：
新增一个孤儿会红，接好一个孤儿也会红。后者是故意的——名单只能手动缩短，
所以没人能悄悄修好、把债留在纸面上。

浏览器实证（生产构建，8040）：

- inspector 标题显示的是通用的「📦 模型设定」，而不是 `inspectorTitle.ts` 算出的分类标题。
- 挡板的尺寸组显示 `长 X / 高 Y / **宽 Z**` 三个框；往 Z 里输 0.35 后 `saveProjectJson()`
  得到 `size: {x:2, y:2, z:0.35}` —— 一个零厚度片体带着厚度进了 .cfdproj，而没有任何东西会画它。

---

## 全部 39 条

### 建模与模型级 inspector（6）

**[medium]** Sizing a zero-thickness sheet (wall / baffle / rectangular floor): the legacy inspector offers exactly the two in-plane boxes for that kind (wall W+H, baffle W+H, floor W+D) with a "this kind has no thickness" hint, and forces the third component back to 0 when it is touched; React shows a generic three-box X/Y/Z group instead, so a wall/baffle/floor gets an editable thickness box that is written to the .cfdproj and drawn by nothing.

- legacy 依据：src/frontend/web/STL_generate.html:8378-8397 (renderWallBaffleInspector `sizeHtml` per-kind branches + hint.floor_zero_thickness / hint.wall_zero_thickness / hint.baffle_zero_thickness), enforcement at :13997 (`baffle && key==='D' -> size.z = 0`) and :14011 (`floor && key==='H' -> size.y = 0`)
- 反驳者结论：React contains a complete port of this feature — src/components/SheetSizeSection.tsx (two in-plane boxes + zero-thickness hint) and src/geometry/sheetSizeFields.ts (per-kind field table, sheetSizeSpec/sheetDeadAxis) — but it is imported by nothing: `grep -rn "SheetSizeSection|sheetSizeSpec|sheetDeadAxis" src/` returns only the two modules themselves, with the only consumer being test/sheetSizeFields.test.ts. Inspector.tsx:396-437 branches rack → RackLayoutSection, partitionWall → PartitionWallSection, polygon floor → FloorPolygonSection, custom → ImportedStlOverview, else a generic (['x','y','z']).map NumberField group with no hint; wall, baffle and rectangular floor all fall into that else-…

- **已修**（`aa530964`，"孤儿模块"那一批）—— `SheetSizeSection` + `geometry/sheetSizeFields.ts` 早就写好且有测试，只是 `Inspector` 没 import；现在 `sheetSizeSpec(kind)` 命中就渲染它。**执行**那一半在 store：`setGeometryComponent` 里 `axis === sheetDeadAxis(kind)` 时直接写 0 —— 面板不给第三个框，但 `setGeometryComponent` 在自动化契约上，脚本照样能写，所以卡在值真正落地的地方而不是某几个调用点。
  浏览器实证（Base_leakage 的墙 m_45）：面板尺寸栏只有「宽度 W」「高度 H」两格 + 提示「墙壁为零厚度平面…」，没有厚度框；`__dcdtuni.setGeometryComponent('m_45','size','z',0.35)` 之后 size 仍是 `{x:4.4, y:3.633, z:0}`。

**[medium]** Where a duplicated model lands: the legacy searches ten directions x twelve rings for a free spot (stepping by the model's own size +8%) so the copy is never born inside its neighbour, and then tests the result against the project bounds — if nothing fits it deletes the copy, pops the undo checkpoint and says so.

- legacy 依据：src/frontend/web/STL_generate.html:5511 `placeDuplicatedModelWithoutOverlap`, called from window.duplicateModel :13866 and duplicateSelectedModels :13898; bounds refusal at :13867-13875 (`isModelWithinProjectBounds` -> removeModelByUid + popLastUndoCheckpoint + alert(t('error.duplicate_out_of_bounds')))
- 反驳者结论：I tried to refute this and could not. The logic EXISTS in React as a well-written, well-tested pure module, but it is dead code — nothing in the app imports it, and the live duplicate path does none of what the claim describes. What I searched, and where the wiring would have to be: 1. Import graph of the module itself. `grep -rn "duplicatePlacement"` across the whole React tree (excluding node_modules) returns exactly TWO files: `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/geometry/duplicatePlacement.ts` (the definition) and `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/test/duplicatePlacement.test.ts` (the test). Grepping the exported symbols (`placeDuplicateWith…

- **已修**（同上批）—— `geometry/duplicatePlacement.ts` 的 `SEARCH_DIRECTIONS` 与 legacy `placeDuplicatedModelWithoutOverlap` 的十个方向**逐项同序**（X/Y 优先、Z 最后），12 环、步长 `max(size, 0.12) * 1.08`，越界或找不到空位就返回 null，`duplicateInto` 据此把副本删掉并让调用方报错。零厚度片体按 1e-3 的薄板参与重叠判定 —— 否则平面之间"体积不相交"，副本正贴在原件上也算空位。
  浏览器实证（Base_leakage）：复制机柜 m_x（size.y=1.9579）→ 副本落在 y 1.000 → 5.229，正好是两环 × `1.9579*1.08`，不是原地重叠。

**[medium]** Refusing a typed geometry edit that would push a model outside the project bounds: in the legacy, committing a position or size (or a rack setting) that leaves the domain reverts the model to its previous anchor/size, pops the undo checkpoint and alerts.

- **已修** —— 拒绝放在 store 里（`setGeometryComponent` / `setModelLocation` 现在返回 boolean，越界就不 `edit`），因为**每个写者都得守这条规矩**，自动化契约也一样。检查放在 `edit()` **之前**：`edit()` 一进去就已经拍了 undo 快照，事后回滚会留下一条什么都不撤的 undo 记录 —— legacy 弹 checkpoint 正是为了这个。弹窗放在面板层（`ui/boundsAlert.ts`），因为 store 被每个单测 import、也被 `window.__dcdtuni` 驱动，headless 里 alert 会把脚本挂死而不是让它失败。`NumberField.onCommit` 返回 `false` 现在表示「被拒绝」，输入框把模型自己的值放回去 —— 否则面板上会留着一个文档里不存在的数字。机柜 H 走 `updateRackSize`（机柜每次编辑都要重推 slot），也接了同一个判断；**机柜设置**（U 数、宽度比、边距）故意没接：它们只通过 `syncRackModelState` 影响尺寸，而那才是「什么是合法机柜」的唯一答案。浏览器实证（yMin=0 开启）：位置 Y 输 −500 mm → 模型停在 0、undo 深度不变、弹出「操作被撤回：模型超出了工程边界限制。」、输入框自己弹回 0.0000；改输 250 mm 正常写入 0.25 m。守卫：`test/typedBoundsRefusal.test.ts`（10 条）。

- legacy 依据：src/frontend/web/STL_generate.html:14015-14023 (updateModel: `if (!isModelWithinProjectBounds(modelId))` -> restore prevAnchor/prevBase, popLastUndoCheckpoint, alert(t('error.undo_out_of_bounds'))), same guard in updateRackModel :14038-14045 and updateRackSetting :14077-14084
- 反驳者结论：Genuine gap. React has the bounds predicates (geometry/faceDragMath.ts:575 withinProjectBounds, :599 clampToProjectBounds) and the shared transform_tools binding (legacy/transformTools.ts:33-34), but they are invoked ONLY from pointer paths: moveGizmo.ts:163/:188 (drag revert to last valid frame), faceDrag.ts:678, dragCreate.ts:165 (clamp or cancel + alert), duplicatePlacement.ts:79. Every typed-edit writer bypasses them: Inspector.tsx:380 (Position) and :433 (Size), SheetSizeSection.tsx:58, CduInspector.tsx:71 all call store.setGeometryComponent; automation/contract.ts:293,297-299 calls setModelLocation/setGeometryComponent. projectStore.setGeometryComponent (:342) and setModelLocation (:37…

**[medium]** Geometry boxes following the project's input unit (mm / cm / m) — the legacy renders every model-level length through formatLength()/getLengthStep(), so in mm mode a CDU envelope reads 800 and a multi-select min-point reads 1500.

- **已修** —— `CduInspector` 与 `MultiSelectPanel` 是仅有的两个还写死米的面板。这不是显示瑕疵：两处都是**可编辑**的框，毫米工程里 CDU 显示 0.8 而旁边机柜显示 600，照着敲 800 就造出一台 800 米的机柜；最小点更糟，敲 1500 会把整组挪 1.5 公里。浏览器实证：CDU 包络显示 800/600/1800、标题「参数化尺寸 (mm)」；两个盒子 ctrl 多选后最小点显示 1500，输 2500 → 两个模型各 +1 m（1.5→2.5、3→4）。守卫：`test/components/UnitAwarePanels.test.tsx`（6 条）。

- legacy 依据：src/frontend/web/STL_generate.html:8513-8516 (CDU size rows: `formatLength(model.geometry.size.x)` with `withUnit(t('label.envelope_w'))` and `step=${getLengthStep()}`), and :11005-11008 (multi-select min point: formatLength + getLengthStep + `t('inspector.min_point_anchor', {unit: getLengthUnitLabel()})`)
- 反驳者结论：I could not find a React implementation, and I can say exactly where it would have to live. What I verified: 1. React DOES have the unit machinery: `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/lengthUnits.ts` (`unitOf`, `toDisplay`, `fromDisplay`, `stepFor`, reading `meta.inputUnit`), covered by `test/lengthUnits.test.ts`. Fifteen components import it (Inspector, SheetSizeSection, PatchInspector, RackLayoutSection, PartitionWallSection, FloorPolygonSection, PipeSegmentInspector, ImportedStlOverview, CustomMeshInspector, SeedInspector, StlPreviewInspector, SensorEditDialog, Room2DEditor, PostprocessPanel, PpEditDialogs). 2. The two named panels are NOT among them. `grep -n "un…

**[small]** Deleting the selected model from its own inspector — the legacy ends every model panel with a red danger button (btn.remove_model / btn.remove_rack / btn.remove_acu).

- **已修** —— `ModelProperties` 末尾补上 `#btn-remove-model`（`btn danger`），标签按 kind 取 `btn.remove_rack` / `btn.remove_acu` / `btn.remove_model`，与 legacy 五个面板的收尾按钮同位（:8453 / :8987 / :9035 / :8760 / :9304）。删**一个**、**不弹确认** —— legacy 的 `removeModel(null, uid)` 里 `evt` 是 null，批量分支和它那句 confirm 都不会跑；批量删除是多选面板的事，而那个面板本来就会把这一版替换掉。删完调用新的 `clearSelection` prop（App 传 `setSelection({uids:[],active:null})`），否则面板会继续渲染一个已经不在工程里的模型。
  浏览器实证（Base_leakage）：选中机柜 → 面板底部出现「🗑️ 从工程中移除此 RACK」，ACU 上是「…此 ACU」；点一下 → 模型 16→15、`findConstruction` 为空、`undoDepth` 0→1、场景可见 mesh 130→104、按钮随选择清空一起消失、日志 `removeModel | m_1`；一次 undo 全回来。
  顺带记一个把我绕了一下的数：删除前那次 214 是**选中状态**下量的 —— 选中一个机柜会多画约 84 个 U 位辅助 mesh，所以能比的是"都不选中"的 130 → 104，不是 214 → 104。

- legacy 依据：src/frontend/web/STL_generate.html:8453 (wall/baffle/floor panel), :8987 (partition wall panel), :9035 (generic box / imported-STL panel), :8760 (btn.remove_acu), :9304 (btn.remove_rack)
- 反驳者结论：Genuine gap, though narrow. React's generic model panel — `ModelProperties` in src/frontend/react/src/components/Inspector.tsx:261-472, the panel every box, rack, ACU, wall, partition wall, baffle, floor and imported STL gets — ends with the rotation prop-group and has no delete control. Searches run: (1) `grep -rn "removeModel|deleteModel|removeSelected|deleteSelected"` over src/ and test/ — every call site is projectStore.ts:249/323 (setters), interaction/editorKeys.ts:117-118 (Delete key), MultiSelectPanel.tsx:164 (batch), CduInspector.tsx:145, PipeSegmentInspector.tsx:171, FeatureTree.tsx:684 (tree ✕); nothing else. (2) `grep -rn "btn danger"` over all src/components/*.tsx — 19 hits, non…

**[small]** Hiding a whole selection at once from the model tree: the legacy's visibility checkbox toggles ALL currently selected models when the clicked one is part of a multi-selection, and shift+click toggles the range from the last-toggled anchor to the clicked row.

- **已修** —— `FeatureTree` 的可见性勾选框改走 `toggleVisibility`，三个分支照 legacy `toggleModelEnabled`（:12965）：点的是多选里的一个 → 整个选择跟着走；shift+点选择之外的一行 → 从**上一次勾过的那行**取区间；否则只动这一行。批量走新的 `store.setEnabledMany`，**一次 edit = 一步 undo**（20 个机柜不该在撤销栈里留 20 条）。区间锚点单独存一个 `visAnchor`，与选择锚点分开 —— legacy 也为此单留 `modelVisibilityToggleAnchorId`。另外照 legacy 把**被隐藏的模型踢出选择**（隐藏了还选着，inspector 就在编辑一个谁都看不见的东西，下一次拖拽会把它拖走）；勾回"显示"则不动选择。
  浏览器实证（Base604，177 个模型、其中 5 个原本就隐藏）：ctrl 选中 m_2/m_3/m_4 → 点其中一个的勾选框 → 隐藏数 5→8（正好这三个）、`undoDepth` 0→1、选中行 3→0、日志 `toggleModelEnabled | 3 models -> false`；**一次** undo 全部还原（隐藏回到 5，场景可见 mesh 8214→8391，+177 是那三台的几何）。区间分支：先勾掉 m_11（锚点），再 shift+勾 m_15 → 新隐藏正好 m_11..m_15 五个、又是一步。守卫：`test/components/TreeVisibilityBatch.test.tsx`（8 条）。
  测试里踩到的坑：原来写的"再点一次全部显示"是错的 —— 隐藏那一下已经清空了选择，第二次点就落进单行分支。改成"用户重新选中再点"，这才真正测到 `checked` 分支不动选择。

- legacy 依据：src/frontend/web/STL_generate.html:12953-13045 window.toggleModelEnabled (batch branch `selectedIds.includes(modelId) && selectedIds.length > 1 -> modelIdsToToggle = selectedIds`; range branch via modelVisibilityToggleAnchorId), checkbox wired at :7902
- 反驳者结论：I hunted for React's batch-visibility path and it genuinely is not there. What I searched and what I found: 1. The checkbox itself — `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/components/FeatureTree.tsx:643-651` is the ONLY `type="checkbox"` in FeatureTree (grep -n "checkbox" returns exactly one hit at :645). Its handler is `onChange={(e) => store.setEnabled(model.uid, e.target.checked)}` with `onClick={(e) => e.stopPropagation()}` above it, so the event object is never read for `shiftKey` and the row-click handler never runs (FeatureTree.tsx:324 explicitly filters `.node-visibility-toggle` out of `onRowClick`). No selection lookup, no anchor ref. 2. The store — `projectSto…

### 面、patch 与边界条件（4）

**[large]** Set the boundary condition of the REMAINING area of a floor or baffle (wall / wallBaffle / perforated, plus the hole shape, diameter, open-area % and derived K) on a sheet that has no patches cut into it yet — e.g. making a whole containment panel or a whole raised-floor plate perforated.

- **已修** — `RemainingAreaBc` 现在挂在模型 inspector 上（`SheetCfdSection`，原 `FloorCfdSection` 扩展为同时覆盖 floor 与 baffle），与 legacy 的 `renderFloorCfdInspector` / `renderBaffleCfdInspector` 同位；`FaceInspector` 里那一份是死代码（planar body 拿不到 face 节点）已删。浏览器实证：全新 floor 与 baffle 各自选中即可改 `区域类型 → 穿孔挡板`，孔径/开孔率/估算 K=1.50 全在，导出的 .cfdrun 里出现 `pb_Floor_1_master/slave` 与 `pb_Default_Baffle_1_master/slave`，sigma=0.64。守卫：`test/components/SheetCfdSection.test.tsx`（6 条，从 MODEL 选择起测）。

- legacy 依据：src/frontend/web/STL_generate.html:8848 renderBaffleCfdInspector (remaining-region baffleType select + renderPerforatedInspector) and :8887 renderFloorCfdInspector ("Remaining area block (always shown)", label.remaining_region_default_bc + updateFloorDefaultBC). Both are unconditionally embedded in the MODEL inspector at :8447-8448 (`${isBaffle ? renderBaffleCfdInspector(model) : ''}${isFloor ? re
- 反驳者结论：The capability EXISTS as code but has no UI route in the patch-less case, which is exactly what the claim scopes. What I found: - `RemainingAreaBc` (PatchInspector.tsx:856-935) is a faithful port: floor writes `cooling.defaultBoundaryType` via `setFloorDefaultBoundaryType` (geometry/floorDefaultBc.ts:78, with the room-boundary decoupling and seeded DEFAULT_PERFORATION), baffle writes `cooling.type` / `cooling.perforation` via `store.setModelCooling`, and `PerforatedEditor` (PatchInspector.tsx:740-783) carries shape / holeDiameterMm / openAreaPct / derived K. Tests exist (test/floorDefaultBc.test.ts, test/patchPhysics.test.ts). - Reachability: `grep -rn "RemainingAreaBc"` returns exactly two …

**[medium]** Move and edge-resize a rack SERVER's face patch (an inlet/outlet vent on a server) by dragging it in the 3D viewport, including the resize-handle cubes and the inter-server snapping that aligns one server's vent edges with another's.

- legacy 依据：src/frontend/web/STL_generate.html:16266 beginPatchDrag — the resize-handle branch takes `handlePatchId` through `getPatchContextById` and sets `isServerPatch`/`equipmentId` on a `boxRectResize` drag (:16295-16330); `boxPatchHit` at :16432-16443 explicitly admits `model.kind === 'rack' && topData.isRackEquipment && topData.isPatch`; :17029 adds the server-patch inter-server vertical/horizontal sna
- 反驳者结论：Genuine gap — React can SELECT a server patch in the viewport but cannot DRAG or edge-resize one there. What exists in React (so this is not a "different words" case): - Selection works: `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/components/Viewport.tsx:454-470` and `:515-535` both read `userData.isRackEquipment` + `isPatch` + `patchId` and emit a `{type:'serverPatch', uid, equipmentId, faceId, patchId}` tree node (the same userData the legacy `boxPatchHit` admits at STL_generate.html:16432-16443). So the pickable meshes and the hit data ARE there — they come from the SHARED `shared/js/geometry_scene.js` builder via `src/legacy/geometryBuilder.ts`. - Editing works, but only…

- **已接线，但浏览器里没能验成，如实记下**。三处实测把设计钉死了：
  ① 视口里**没有**每台服务器的坐标组 —— 服务器 patch mesh 的父链就是 `Mesh → Group{modelId} → Group → Group → Scene`，只有机柜根，所以拿不到"服务器局部空间"这个现成的 root；
  ② 共享模块有 `geometryPure.getServerFaceRect(box, faceId)`，直接给出**机柜局部**的 origin/right/up/w/h —— 正是 `facePlane`/`facePointAt` 需要的 layout，机柜根就是对的 root；
  ③ 但那个 `box` 是共享 builder 在建几何时**内联算出来的**（`serverBox`，geometry_scene.js），没有导出；在 React 里重算一遍槽位算术＝第二份实现，正是这个移植反复踩的那类漂移。
  结论：box 从**已建好的 mesh** 还原（每个都带 `equipmentId`，取并集），这样它与画出来的、导出的必然一致。
  已写好但**故意没提交**的纯逻辑：`geometry/serverPatchSite.ts`（`findServerPatch` / `serverBoxFromScene` / `commitServerPatchGeometry`）+ `test/serverPatchSite.test.ts`（8 条，"只并集这台服务器的 mesh"那条回退即红）。没提交的原因是 `test/orphanModules.test.ts` 正确地报了红：模块还没有生产调用点，而"写好没人 import"正是这个移植清单里最常见的一类缺陷。
  接线已完成：`beginPatchBodyDrag` 认 `node.type==='serverPatch'`（用上面的 layout 建平面与面坐标）、`DragState` 带 `server:{equipmentId,layout}` 让 `updatePatchDrag` 走 `findServerPatch`、释放走 `commitServerPatchGeometry`。**明确没做的两半**：边缘 8 个 handle 的改尺寸；legacy 的服务器间对齐吸附 —— `drag.server` 时**跳过**整段 snap，因为现有 snapper 工作在**模型**的面坐标系里，用在服务器 patch 上会把风口吸到机柜自己的边上，与其近似不如不做。
  **2026-10-21 补记：这一条已闭合。** `a6c6baa81`（2026-09-02）查清了接线里的三处错误（服务器 patch 以面的分数存储而接线写入了米制坐标；钳位用了机柜面而不是服务器面；`handleTarget` 没有 `serverPatch` 分支，只能移动不能改尺寸），补上边缘手柄，并在 GPU 浏览器里于 Base_leakage 上验证拖动与改尺寸；`6ba767004`、`a98ce8cc2`（2026-09-17）随后在两个编辑器里复验了 patch 主体拖动。下面保留当时"没验成"的原始记录。
  **没验成的那一半，连同现象一起记下**（不要当作"能用"）：在浏览器里我始终没能让这个拖拽 begin。相机贴到机柜背面 2 m、正对 `p_14` 风口时，`intersectObjects(renderMeshes,false)` 在那个像素上只命中 4 块 `Default_Rack_1_frame`（1.90/1.96/2.45/2.51 m），风口那块 quad **一次都没进命中列表**；直接对它 `intersectObject(mesh,false)` 也是 0，改 `DoubleSide` 仍是 0，而 `intersectObject(mesh,true)` 得到 4 —— 那 4 个来自它的 `LineSegments` 描边子对象。也就是说：射线穿过了描边却没穿过那两片三角形，原因我没查清（该 mesh 4 顶点 6 索引、visible、在 registry 里、matrix 为单位阵，世界包围盒中心就是我投影用的点）。legacy 的同一条守卫（命中最近的必须是这块 patch）也在，所以"看不见就拖不动"本身是对的；但"看得见时能拖动"这一半没有证据。下一步应先解释这次射线不命中的原因（怀疑与 vendored three-mesh-bvh 对 `Mesh.prototype.raycast` 的替换有关），再重新实证。

**[medium]** Place a wall-baffle/perforated surface's mesh seed pair by dragging it in the viewport — dragging the anchor ball to slide the seed across the surface plane, or dragging either tip ball to set the offset along the normal.

- **已修** —— 与 #15 是同一个特性，见上。

- legacy 依据：src/frontend/web/STL_generate.html:18033 beginSeedSurfaceDrag (state built at :18069), :18084 updateSeedSurfaceDrag — 'anchor' side re-projects the pointer onto the surface plane, tip side projects onto the fixed normal to set `offset`; :18143 endSeedSurfaceDrag logs 'dragSeedSurface'. Handles are hit-priority 120 (:2443) and the hover branch at :17317 sets a grab cursor.
- 反驳者结论：Real gap, and slightly larger than reported. React builds the seed helper for display only and never wires a pointer handler to it. Evidence: - `src/components/Viewport.tsx:1333-1356` is the ONLY seed-helper wiring: a redraw effect that calls `buildSeedHelper(surface)` and adds it to `globalGroup`. `onPointerDown` (`Viewport.tsx:359-368`) offers the event to `rotateGizmoRef.begin` then `faceDragRef.begin` and nothing else; `onPointerMove` (`:369`) only calls `faceDragRef.hover`, so there is no grab cursor for a seed handle either. - `isSeedHandle` appears in React only as non-interactive metadata: `src/interaction/picking.ts:32` (priority 120), `src/geometry/modelOpacity.ts:114,156` (opacity…

**[small]** Rename the default face region — the STL solid name the exporter gives the part of a face that no patch covers — on a face that has no patches yet, together with the live "Final STL Solid Name" readout for it.

- legacy 依据：src/frontend/web/STL_generate.html:10545-10561, renderFaceInspector "Default Face Region" group: `updateWallName(model.uid, faceId, 0, this.value)` with `ensureWallName(face, 0)` and `composeFaceScopedExportName(faceId, face, defaultPatchName)` shown beneath. The group is unconditional — it appears on every face, patches or not.
- 反驳者结论：React's only writer of face.wallNames[] is setWallRegionName (react/src/geometry/wallRegion.ts:137), called from exactly one component, WallRegionInspector.tsx:81. Both routes to a wallRegion selection require patches: treeNodes.ts:231 gates region rows on `patches.length > 0` (and boxChildren, treeNodes.ts:372-394, emits no region rows at all), and picking.ts:131/:139 both require ctx.facePatchCount(faceId) > 0. React's face panel is Inspector.tsx:173-174 = FacePrefixSection + FaceInspector; FaceInspector (PatchInspector.tsx:785-838) offers face.displayName (a different field, via updateFace), opacity, reverseSolidNormal, patch count, RemainingAreaBc (boundary condition, not naming) and Pat…

- **已修** —— `FaceInspector` 末尾补上 `DefaultFaceRegion` 组：`#face-region-name`（写 `setWallRegionName(uid, faceId, 0, …)`）与 `#face-region-final-name` 只读读数，**无条件**渲染，与 legacy 的第三段 `Default Face Region`（:10548）同位。三个 helper（`wallRegionName` / `setWallRegionName` / `wallRegionSolidName`）本来就在，只是唯一的入口 `WallRegionInspector` 要先有 patch 才够得着。
  浏览器实证（Base_leakage，ACU m_17 的 left 面，没有任何 patch）：树里点 `face:m_17:left` → 面板出现该组，`wall_1` / `ACU_1_Left`；输 `acu_side_panel` → 工程里 `faces.left.wallNames = ["acu_side_panel"]`，日志 `updateWallName m_17/left[0] -> acu_side_panel`。
  **两处必须说清的边界**：① 单区域时最终 solid 名按**模型**命名，区域名不进去 —— `wallRegionSolidName` 报的是导出器真会写的串（它自己的注释解释了 legacy 那个 `composeFaceScopedExportName` 读数从来对不上导出文件），所以测试里专门钉了"输了名字 solid 名不变"，免得以后有人"顺手"把两者拼起来；② **平面体（wall/baffle/floor/partitionWall）仍然够不着**：`picking.ts` 对这些 kind 明确返回 null（"没有面板可开"），树里也不给面行 —— 而 legacy 的 `renderWallBaffleInspector` 里同样没有 `updateWallName`，两边一样，作为遗留小口记在这里。守卫：`test/components/DefaultFaceRegion.test.tsx`（6 条，含"渲染不得把默认名写进工程"）。

### 机柜内部（4）

**[medium]** Set a rack door's perforation HOLE SHAPE (circle / rect / diamond) and read back the resulting pressure-loss coefficient K — the shape is exported into the .cfdrun and changes the door's K, so in React every door is silently a circle-hole door.

- **已修** —— 机柜门那块原本是把 `PerforatedEditor` 四个字段里的两个手抄过去，少了形状下拉和 K 读数。现在直接用同一个编辑器（新增 `idPrefix`，因为进/出风门同屏，重复的 element id 会让 label 指错控件）。浏览器实证：开孔形状选「菱形」→ 估算 K 从 1.50 变 1.75，导出的 .cfdrun 里是 `"inletDoor":{"enabled":true,"perforated":{"shape":"diamond",...}}`。守卫：`test/components/RackDoorPerforation.test.tsx`（5 条）。

- legacy 依据：src/frontend/web/STL_generate.html:9214 and :9228 call renderPerforatedInspector(_inletDoor.perforation / _outletDoor.perforation, "window._rdSetDoorPerforated(...)") — that inspector (:8817-8845) renders a shape <select> with label.hole_shape_circle/rect/diamond, holeDiameterMm, openAreaPct and a monospace K readout (label.k_estimate, _perforatedEstimateK) plus hint.perforated_formula. shape is l
- 反驳者结论：Gap confirmed. React's rack-door UI is only the block at src/frontend/react/src/components/RackDetailPanel.tsx:339-358, which renders exactly two fields: openAreaPct (:340-347) and holeDiameterMm (:349-358). There is no hole-shape control and no K readout in the rack path. Searches performed over /mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react (src + test): - grep -rni "perforation|holeShape|hole_shape|k_estimate|perforatedK|estimateK|openAreaPct|shape": perforation appears only in geometry/rackDetail.ts, geometry/patchPhysics.ts, components/PatchInspector.tsx, components/RackDetailPanel.tsx, geometry/floorDefaultBc.ts, run/meshPreflight.ts, legacy/factories.ts. - PerforatedEditor (t…

**[medium]** Browse and select a rack's U-slots from the feature tree — the legacy tree expands each rack's front/back face into one row per U with its status (wall / void / equipment name), groups multi-U servers with their child U rows, and adds a door row with a visibility checkbox and a remove button.

- legacy 依据：src/frontend/web/STL_generate.html:7940-8005 — face rows with a slot-count badge (:7944), per-slot rows `▫ U12 · void|wall|<server>` calling selectRackSlot(event,…) (:7984-7992), multi-U equipment group rows calling selectRackEquipmentSlotGroup (:7966), and the inlet/outlet door row with a renderDoor checkbox and a ✖ remove (:7995-8002). The legacy hint even tells the user this is the path: hint.s
- 反驳者结论：Genuine gap for the U-slot rows and the door row, with partial (not equivalent) coverage elsewhere. What I searched, and what would have had to be there: 1. The tree producer. `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/interaction/treeNodes.ts:151-215` `rackChildren()` pushes exactly two groups — `'🧰 Equipments'` (servers → EQUIPMENT_FACES → patches) and `'🪟 Partitions'` — and returns. `modelChildren()` (:413) routes `kind === 'rack'` there and afterwards only appends `原始区域` for imported solids. No face row, no per-U row, no door row. This is asserted as intended by `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/test/treeNodes.test.ts:100-101`, which expects the r…

- **已修** —— `rackChildren` 末尾补上 front/back 两个面行（徽章是槽位数），面下按 legacy 的顺序**自上而下**列 U 行（`▫ U12 · void | wall | <服务器名>`），多 U 服务器折成一行 `🧰 <名字>` 徽章 `NU`、它的 U 是它的子行，最后是门行（可见性勾选框 → `setDoorRender`，✖ → `setDoorEnabled(false)`）。`TreeChild` 因此多了 `checkbox` / `action` 两个可选描述，由 `SubNode` 渲染 —— 只有门行用得上，与其让组件按行类型分支，不如让节点自己带上。
  **这条不只是"能看"**：视口里点被服务器占用的槽位永远解析成 `serverFace`（`rackFaceGesture`），所以在这些行出现之前，**整个 app 没有任何手势能选中服务器里的某一个 U**，上一条的单槽移除按钮也就够不着。
  浏览器实证（Base_leakage）：点 `rack-slot:m_1:front:20`（14U 服务器 e_2 的一格）→ 右侧出现「U-SLOT #21」单槽面板与「➖ 从当前设备移除这个 U-slot」→ 点下去，e_2 从 15–28 拆成 15–19 与 21–28（新 `fan-server_1_split`），确认框弹了、一步 undo；树里那一格随即变成 `▫ U21 · wall`。
  **与 legacy 的一处刻意分歧，且是量出来的**：面行**默认折叠**（legacy 默认展开）。Base604（142 机柜）上展开时树是 **28,682** 行、折叠是 **14,296** 行；实测一次树重渲染（展开一个面）要 **373 ms**，行数翻倍就是每次选中都多花这么多。**同时必须澄清**：点一行到重绘的 **1.58 s** 是**本来就有的**（展开 1583 ms / 折叠 1587 ms，两者一样），不是这条引入的，也不是折叠修好的 —— 那 1.2 s 在树以外的地方，另记一笔待查。守卫：`test/components/RackSlotTree.test.tsx`（9 条）。
  ~~仍未做：面行的槽位可选范围没按 `safeMarginTop/Bottom` 裁（legacy 的 `isRackSlotSelectable`），现在是 0..slot-1 全列。~~
  **2026-10-21 更正：这一句是误读，不是缺口。** legacy 的 `isRackSlotSelectable`（`shared/js/geometry_pure.js` 中）只检查 `0 <= slotIndex < geometry.slot`，从不读 `safeMarginTop/Bottom`；legacy 树用的 `getRackSelectableSlotRange` 也返回 `{start: 0, end: slot}`。React 列出 0..slot-1 与 legacy 一致。

**[small]** Trim exactly ONE U off a server (or split a server by taking out its single middle U) by selecting that one slot and pressing "Remove U-slot from Equipment".

- legacy 依据：src/frontend/web/STL_generate.html:9602 — the SINGLE-slot inspector renders the btn.remove_slot_from_equipment button whenever getRackEquipmentSelectionContext reports canRemoveFromEquipment; that context (:9864-9917) computes removalMode = full / trimStart / trimEnd / split from a selection of ANY length, including one slot.
- 反驳者结论：Gap confirmed, but narrower than described: the LOGIC exists in React and is index-count agnostic — geometry/rackSelection.ts:68-124 computes full/trimStart/trimEnd/split for any selection, geometry/rackSlots.ts:415-468 implements all four (the split branch mints a new server carrying the original cooling block and faces, so power/flow/patch settings would be preserved), and test/rackSelection.test.ts:29-79 covers it. What is missing is the UI route for a ONE-slot selection. RackSlotInspector.tsx:88 gates MultiSlotPanel on `indices && indices.length > 1`; the single-slot branch's complete control set (ids at :104-303) is slot-open-this/-opposite, slot-create-{fanServer,nonFan,liquidServer}, …

- **已修（控件与逻辑）** —— 把移除按钮抽成共用的 `RemoveFromEquipmentButton`，单选面板与范围面板各渲染一份（id 分别是 `slot-remove-from-equipment` / `multislot-remove`，标签用 legacy 的两个键），confirm 与错误文案只有一份，不会两边跑偏。`selectionContext` 本来就与选择长度无关，四种模式（full / trimStart / trimEnd / split）全都现成。守卫：`test/components/SingleSlotRemove.test.tsx`（8 条：4U 服务器尾端裁剪不弹确认、首端裁剪、中间 U 弹确认后一分为二、拒绝确认则原样、1U 整机移除、空槽位没有按钮）。
  **但这条的"路"还差一步，说清楚**：React 里点击被服务器占用的槽位在视口里永远解析成 `serverFace`/`equipment`（`rackFaceGesture` 的表，照 legacy :17866），单个 `rackSlot` 选择只会出现在**空**槽位上 —— 所以这个按钮目前只有范围选择（从空槽 shift 拉过服务器）那条路能碰到，单槽那条要等下一条「特征树里的 U 位行」（legacy 正是从树里 `selectRackSlot` 进来的）落地。这一点在浏览器里查过 `rackFaceSelection` 的三个分支后才敢写。

**[small]** Type the rack's internal dimensions (mounting width, inlet/outlet door setbacks) in the project's chosen length unit — the rack detail editor honours the mm / cm / m setting the rest of the app uses.

- legacy 依据：src/frontend/web/STL_generate.html:9128-9160 — the detail panel labels read `${t('rack.inner_width_label')}(${getLengthUnitLabel()})` and `${t('rack.inlet_offset')} (${getLengthUnitLabel()})`, values go through formatLength() and getLengthStep(), and the setter converts back: window._rdSetInnerWidth (:11847-11857) calls fromDisplayLength(meters) before dividing by size.x.
- 反驳者结论：I could not refute this one — the gap is real, and it is narrowly scoped to the rack detail editor. What I searched: - `find` for any RackDetail*/lengthUnits* file: only `src/components/RackDetailPanel.tsx`, `src/lengthUnits.ts`, `test/lengthUnits.test.ts`. - Read `src/components/RackDetailPanel.tsx` end to end (497 lines). Its import block (lines 1–27) pulls from `../geometry/rackDetail`, `../geometry/rackManifold`, `../useTranslation`, `./NumberField` — nothing from `../lengthUnits`, and it never receives or reads the project/`meta.inputUnit`. Labels are literally `{t('rack.inner_width_label')} (m)` (:167), `{t('rack.inlet_offset')} (m)` (:199), `{t('rack.outlet_offset')} (m)` (:209). Valu…

- **已修** —— `RackDetailPanel` 本来就收着 `project` prop 只是没读，现在按 `meta.inputUnit` 取标签、step 与换算：内径宽度、距 inlet、距 outlet 三个框的 `value` 走 `toDisplay`、`onCommit` 走 `fromDisplay`（这三个 setter 的入参是**米**），标签写 `({unitLabel})`。**超出 gap 字面的一处**：上方那行只读的 `W: 0.600 m × D: … × H: …` 也跟着换单位 —— 同一个编辑器里，尺寸行说 0.600 m 而两行之下的宽度框标着 (mm) 写 500，是自己跟自己打架。液冷分歧管的直径/偏移仍是 mm（字段名就叫 `…Mm`，属于部件规格而非房间几何，与"只有几何字段跟随输入单位"的规则一致）。
  浏览器实证（Base_leakage 切 mm，机柜 m_1 = 0.6 m 宽、ratio 0.8）：面板显示 `宽度 (mm) 480`、`距 inlet (mm) 100`、step `0.1`；输 500 → `slotWidthRatio` 0.8333、实宽 **0.5000 m**、框回读 500；输 45 → `inletOffset` **0.045**，日志 `rackDetailSlotDepth inlet 0.045m`。修前同样的输入会被比例钳位吃掉（测试里量到 0.57 / 0.54）。守卫：`test/components/RackDetailUnits.test.tsx`（7 条）。
  另记一个坑：`test/i18n.test.ts` 的"组件标记里不许有硬编码中文"会连 **JSX 注释**一起扫，注释里举例写中文标签就会红。

### 网格（5）

**[large]** Drag the seed helper in the 3D viewport: grab the centre hub to slide a surface's seed anchor across its plane, or drag either tip ball to set the seed offset. In React the seed anchor cannot be changed at all — the panel shows it as a read-only badge and no other control writes it.

- **已修** — `geometry/seedDrag.ts` + Viewport 里的指针接线：hub 沿曲面平面滑动 anchor，两端球沿刚性杆改 offset（`Math.abs` + `offsetMin` 下限），拖拽期间 `mutateLive` + `reposition()` 逐帧移动已画好的 helper，松手时先还原起点再 `edit` 一次 —— **整段拖拽一条 undo**。与 legacy 的一处刻意分歧：legacy 每次拖拽都把 direction 写回曲面法向，会静默抹掉用户在面板里填的方向；这里用**存储的 direction**（也就是箭头实际画出来的轴）。浏览器实证（Base_leakage/m_51）：hub 拖拽 anchor 2.200,1.000,3.000 → 3.977,1.000,3.032（Y 不变 = 仍在面上），undo 一次全退回；端球拖拽 offset 0.0425 → 0.8517 且 anchor 不动。守卫：`test/seedDrag.test.ts`（18 条）。

- legacy 依据：src/frontend/web/STL_generate.html:18033 `beginSeedSurfaceDrag` (raycasts `seedSurfaceHelperMeshes`), :18085 `updateSeedSurfaceDrag` (anchor branch calls `projectPointToSeedSurfacePlane` + `setStoredSeedConfigForSurface`; tip branch recomputes `offset`), helper hit-meshes tagged `seedSide` at :7373 and :7397
- 反驳者结论：Genuine gap. React builds the seed helper (buildSeedHelper, seedHelperVisual.ts:84/110 tagging seedSide 'positive'/'negative'/'anchor' with isSeedHandle) but nothing raycasts or drags it. Viewport.tsx:1333-1356 only creates/adds/disposes the group inside a useEffect — no pointerdown, no raycast, no registration with any picking path. A whole-tree grep (excluding node_modules/dist) for seedSide/seedSurfaceKey/setStoredSeedConfig/seedDrag finds only the two tag sites; every seedDrag hit belongs to a different feature, the postprocess streamline seed sphere (setPpSeedDrag, ppViewState.ts:125; the translate gizmo at Viewport.tsx:1002-1026 attaches to ppLayer==='seed'), not the mesh seed anchor. …

**[medium]** Mesh preview stays usable on a big mesh: above 200 patches the legacy collapses every patch into one merged surface + one merged edge set (two draw calls), and rebuilds that merged pair when patch checkboxes change.

- **已修** —— `run/meshPreview.ts` 加了 `MERGE_THRESHOLD = 200`：超过就把所有可见 patch 合成**一个** surface + **一个** edge set（共用全局 position 属性，只拼接索引），逐 patch 对象照建但不进场景 —— 高亮和拾取仍按名字找得到它们，legacy 也是这么留的（:24439 建完再从 group 里移除）。
  浏览器实证（case/Base604，**4336** 个 patch）：预览组 `children` 恰好 **2**（修前是 8672）；勾一个 `merged_perforatedBlankingPanel_10U_master` 精确 +1296 三角形；全选 4341 项后 322 万三角形仍在一个合并面里、**FPS 60**。
  **过程中我自己引入并修掉一个更糟的 bug**：第一版 全选 是循环调 `setPatchVisible`，合并模式下每次都重建整条索引缓冲 —— 4336 次 × 300 万三角形，标签页直接卡死（实测浏览器 CDP 60s 无响应）。加了批量入口 `setPatchesVisible`，全选现在 **930 ms**、全不选 **4 ms**。守卫：`test/meshPreviewMerge.test.ts`（12 条）。
  另记一笔：那条"只重建一次"的测试第一版是**空的** —— 它在 dispose 回调里重挂监听，而那一刻替换用的 geometry 还没创建，重挂静默失败，两种实现都数到 1。改成临时替换 `BufferGeometry.prototype.dispose` 计数才真正会红。

- legacy 依据：src/frontend/web/STL_generate.html:24438 `_MP_MERGE_THRESHOLD = 200` → :24445 `_mpRebuildMerged(state, allNames)` / `state._isMergedMode = true`; builder at :24304; visibility path at :24571 `mpUpdateVisibility` re-merges when `_isMergedMode`
- 反驳者结论：I could not find any merged-geometry path for the React mesh preview. What I searched (all under /mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react): - `grep -rn "THRESHOLD|drawCall|draw call|mergeGeometries|BufferGeometryUtils|mergeVertices|merged|MERGE_THRESHOLD|_isMergedMode|_mpRebuildMerged"` over src/ and test/ — every hit is unrelated (DRAG_THRESHOLD_PX in faceDragMath/ViewCube/patchViewportPick, `merged_*.stl` export filenames in io/cfdPackage.ts and io/singleModelExport.ts, `merged_*` patch-name parsing in postprocess/ppPatchTree.ts). - Read src/run/meshPreview.ts end to end (191 lines). `buildMeshPreview` unconditionally does `decoded.patches.forEach(...)` creating one THREE.Me…

**[medium]** The post-load mesh statistics readout: "N patches, N cells, N boundary faces, N points, N triangles" shown when the mesh preview finishes loading (and logged to the operation log). The cell count is the number `maxGlobalCells` is set against.

- **已修**（与下面那条"可取消的分阶段对话框"是同一个手势，一起做的）—— `MeshMeta` 补上 `nCells`/`nBoundaryFaces`/`nBoundarySum`/`totalEdges`（服务端 server.py:2179 一直在发，是 React 的类型把它们挡在了边界外），`run/meshPreview.ts` 加纯函数 `meshPreviewSummary(meta)` 复刻 legacy 的那一行：cells 与 boundary faces 走 `toLocaleString`，`nCells` 为 null 时给 `?`，`nBoundaryFaces` 为 null 时退到 `nBoundarySum` 并标注 `(from boundary)`。落两处：面板上 `#mp-mesh-summary`（对话框关掉之后还留着）和操作日志 `logAction('meshPreview', …)`。
  浏览器实证（case/Base604）：`4336 patches, 2,941,074 cells, 1,596,336 boundary faces, 1814631 points, 3229582 triangles`；与 `Mesh/constant/polyMesh/owner` 的 note 逐字对得上（`nCells:2941074`，`nFaces 9970728 − nInternalFaces 8374392 = 1596336`）。守卫：`test/components/MeshPreviewLoad.test.tsx`（8 条，含三条纯函数分支）。

- legacy 依据：src/frontend/web/STL_generate.html:24467-24483 — builds `cellsStr` from `data.nCells`, `bFacesStr` from `data.nBoundaryFaces`/`nBoundarySum`, assembles `summary` at :24475 and shows it via `task.done(summary)` + `logOperation`
- 反驳者结论：Real gap — React drops the counts at the type boundary. `loadMeshPreview` (src/frontend/react/src/run/meshPreview.ts:184) parses the response as `MeshMeta`, and `MeshMeta` (src/frontend/react/src/postprocess/index.ts:57-65) declares only `patches`, `fields?`, `totalPoints?`, `totalTriangles?` — `nCells`, `nBoundaryFaces`, `nBoundarySum`, `totalEdges` are absent from the type, so the server's `counts = loader.read_counts()` payload (src/server/server.py:2172-2179) is structurally invisible downstream. `MeshPreviewPanel.tsx:131-156` (`load()`) reads only `meta.patches` and never calls `logOperation`; its render (:213-330) has the two opacity sliders, render-order chips, per-kind patch counts a…

**[small]** Cancel a mesh-preview load in progress, with a blocking dialog that reports each stage (requesting metadata / loading binary / parsing).

- **已修** —— `loadMeshPreview({signal, onStage})`：两个 fetch 拿同一个 `AbortSignal`，三个阶段在**发请求之前**就写进日志（否则对话框在整段等待里是空的）；面板起 `AbortController`，`busy` 时渲染既有的 `PpInitTask`（复用后处理那套 `blocking-task-dialog`/`pp-init-cancel`）。两个 await 之间被取消时两个 fetch 都已 resolve、没人会 reject，所以中间补了一次 `signal.aborted` 检查再抛（legacy 是 `task.isCancelled()`，:24352/24357）。取消判据用 `err.name === 'AbortError'` 而不是 `instanceof Error` —— 浏览器抛的是 DOMException，第一版用 instanceof 直接把"取消"渲染成了红色错误条。
  浏览器实证（Base604）：点加载后对话框标题「加载网格预览...」、日志行「请求网格元数据...」、下面是「取消」；定时 250 ms 点取消 → 对话框消失、summary 清空、**无**错误条、日志留「网格预览加载已取消」。

- legacy 依据：src/frontend/web/STL_generate.html:24340-24344 — `new AbortController()` + `showBlockingTaskDialog({ title: t('mp.loading_mesh'), onCancel: () => ac.abort() })`, with `task.log(...)` at :24350/:24356/:24361 and `signal: ac.signal` passed to both fetches
- 反驳者结论：Genuine gap. React has all the machinery but never wired it to the mesh preview. Evidence: - src/frontend/react/src/run/meshPreview.ts:184-192 — `loadMeshPreview(): Promise<{meta, buffer}>` takes NO AbortSignal and no progress callback; two bare `fetch('/api/mesh-preview/load')` / `fetch('/api/mesh-preview/binary')` calls. (Its own docstring says "Split out so the panel can report each step" — aspirational; the panel does not.) - Only call site: src/frontend/react/src/components/MeshPreviewPanel.tsx:130-158 `load()` — `setBusy(true)` / `await loadMeshPreview()` / `finally setBusy(false)`. The sole UI effect is the button at :216 flipping to `t('mp.loading_mesh')` and `disabled={busy}`. No di…

**[small]** Click an extra seed row in the list to fly the camera to that seed point.

- **已修** —— 行首那块「色点 + #N」就是控件（坐标那三格是可编辑输入框，legacy 里整条 label 可点，这里剩下的可点部分就是它），加 `id="extra-loc-<id>-locate"`、`title`（新键 `tooltip.locate_seed`）与 `flyToExtraSeed(entry)`。
  **与主种子按钮刻意不同的两种手势**：主种子的 📷 是**重新对准** —— 保持视线方向与距离，按下去房间不会转（legacy `locateLocInMesh`，:27304）；额外种子行是**飞过去** —— target 落在种子上、相机停在斜 3 m 外（`_flyToExtraLoc`，:27442）。两者都照搬。40 m 的房间里"重新对准"只会把种子留成画面正中一个点，对那个有自己小球和拖拽手柄的主种子够用，从六个里挑一个则毫无用处。
  浏览器实证（Base_leakage，种子 1 设在 1.5/2.25/3.75）：点一下行标签 → `controls.target` = `[1.5,2.25,3.75]`、`camera.position` = `[4.5,5.25,6.75]`（正好 +3/+3/+3），点击前是 target `[2.2,1.8,3]` / cam `[7.6,7.2,9.6]`；截图里视角确实贴到了机柜跟前。守卫：`test/components/ExtraSeedLocate.test.tsx`（6 条，含一条"主种子仍是重新对准、距离不变"的对照）。
  测试里踩到的坑：`addExtraLocation()` 是**确定性**的（主种子 +0.75 m，不像 legacy 随机），连加两个坐标完全一样 —— "每行去的地方不同"那条如果不先把两个种子挪开就是永远成立的空断言。

- legacy 依据：src/frontend/web/STL_generate.html:27428 `lbl.onclick = function() { _flyToExtraLoc(entry.id); }` (label `title = 'Click to locate'`), implementation `_flyToExtraLoc` at :27442 setting `controls.target` and camera position
- 反驳者结论：I could not find a React implementation, and I can state exactly where it would have to live. What I searched: 1. `grep -rn "locateSeed|flyTo|locate"` over `react/src` and `react/test` — the only camera-locate is `locateSeed()` at `src/components/MeshPanel.tsx:99-110`, which reads `mesh.locationInMesh` only (the PRIMARY seed) and is bound to the single `<button id="btn-locate-loc">` at `:392`. It takes no arguments, so it cannot be re-used per-seed. 2. `grep -rn "extraLocation|extraLoc|ExtraLocation|addSeed|add_seed"` over `src/` and `test/` — the complete set of call sites is: `projectStore.ts:972/1004/1019/1024/1033` (add/remove/set-axis/colour), `components/MeshPanel.tsx:90/395/417/423/43…

### 求解（6）

**[medium]** Rename a sensor probe, or type exact coordinates for one that already exists — legacy double-clicks the probe's name in 传感器位点 and gets a dialog with a name box, X/Y/Z boxes (in the project's input unit) and three validations (name must be [A-Za-z0-9_]+, must be unique, coords must be finite).

- **已修**（`aa530964`，"孤儿模块"那一批）—— `SensorEditDialog` + `run/sensorEdit.ts` 本来就写好了，只是没人 import；现在双击行内名字即可打开，三条校验与单位换算都在里面（XYZ 按 `meta.inputUnit` 显示/回写）。守卫：`test/components/SensorEditDialog.test.tsx`（从双击手势起测，而不是只测校验函数 —— 校验函数在孤儿期间一直是绿的）。

- legacy 依据：src/frontend/web/STL_generate.html:23336 `window.editSensorPointDialog` (dbl-click binding at :23280, apply path `window.editSensorPoint` at :23315, name-collision guard `_sensorNameTaken` at :23153)
- 反驳者结论：SensorEditDialog.tsx and its validator src/run/sensorEdit.ts are fully written but orphaned. Exhaustive grep of the react tree for SensorEditDialog/sensorEdit/validateSensorEdit/sensorNameTaken/toSensorDisplay/formatSensorLocation/sensorUnitLabel/sensorUnitScale returns hits ONLY inside those two files plus test/sensorEdit.test.ts, which exercises the pure validator and never renders the component. No import of SensorEditDialog exists anywhere (App.tsx imports SensorPanel only). setSensorProbe has exactly three non-test callers: its definition (projectStore.ts:811), the orphan dialog (SensorEditDialog.tsx:55), and SensorPanel.tsx:134 which passes only {enabled} from the checkbox — nothing in…

**[medium]** Server-temperature and ACU-return monitoring selections survive leaving the solver tab. In the legacy the checkbox lists are static DOM that is only hidden, so ticking 20 server patches, going to the geometry tab and coming back leaves them ticked, and the export still asks for those probes.

- **已修** —— 面板的三个状态改成从 `run/monitoring` 模块**回读**（模块本来就存在，只是没人拿它当真相来源）。真正把选择**销毁**掉的是另一半：mount 时的 publish effect 在 `/api/server-patches` 还没回来时就跑了一次，而 `publish` 会把已勾选的 id 拿空的 `servers`/`acus` 过滤一遍 —— 于是不是"没画出来"，是"当场写了个空配置覆盖掉"。现在 `loading` 期间不 publish。浏览器实证（Base_coldAisleIso，15 个 patch）：勾两个 → 切到几何标签（面板卸载、`data-patch` 数归零）→ 切回来两个仍勾着，`smGetMonitoringConfig()` 一直是那两个；导出的 manifest 里 `meshGeometry.simulationConfig.monitoring.serverTemperature` 就是这两个 patch。守卫：`test/components/SolverMonitorPersist.test.tsx`（5 条，含"加载期间不覆盖"和"算例里已不存在的 patch 要丢掉"）。

- legacy 依据：src/frontend/web/STL_generate.html:1052-1083 (`#solver-monitoring-right`, `#sm-server-list`, `#sm-acu-return-list` live in the static markup; `_applyLeftTabUI` only toggles display) + :25014 `window.smGetMonitoringConfig` reads those checkboxes out of the DOM at export time
- 反驳者结论：Genuine gap — confirmed after searching for a store, project persistence, localStorage, and the automation contract. React holds both selections as component-local state only: SolverMonitorPanel.tsx:36-37 (`useState<Set<string>>(new Set())` for serverOn and acuOn), with fully controlled checkboxes (`checked={on.has(p.patch)}` at :104), so unlike the legacy there is no static DOM retaining the ticks. The panel mounts only inside SolverRightColumn on the 'monitor' sub-tab (App.tsx:135) within the `solverRight ? …` branch (App.tsx:751), gated by `solverRight = leftTab === 'solver'` (App.tsx:205) — so both the 传感器位点 sub-tab and any other left tab unmount it. On remount the publish effect (Solver…

**[medium]** Starting a solve from the transient tab's ▶ 执行瞬态求解 does everything the solver tab's run does — the mesh-quality confirmation gate for level 1/2 checkMesh warnings, opening the live monitor window, and starting the probe poller — because in the legacy that button IS runSolveOnly.

- **已修** —— 新增 `run/useSolveLauncher.ts`，两个按钮共用（legacy 里它们本来就是同一个函数）。**闸门装错了地方才是重点**：瞬态求解是最长的一种跑法，偏偏它是唯一一个绕过 checkMesh 一级/二级确认、而且没有实时曲线可看的。共享的是**决策与副作用**，对话框仍留在各自面板里渲染（那是面板自己的 render）。浏览器实证（Base_coldAisleIso，solverType 切 transient 后瞬态标签才可达）：按 ▶ → `sm-monitor-window` 出现、显示「等待残差数据」、job kind=solve running=true；随后用 `/api/abort-job`（键名是 `jobId`）收掉，rc=-15。守卫：`test/solveLauncher.test.tsx`（9 条，其中 2 条直接按**真按钮**而不只是测 hook —— 上一轮审计一口气挖出五个"写好了没人 import"的模块，这个教训不重复）。

- legacy 依据：src/frontend/web/STL_generate.html:483 (`onclick="window.runTransientSolve()"`) and :26661 `window.runTransientSolve = function() { window.runSolveOnly(); }`, whose body at :28348-28430 runs showMeshQualityWarningDialog, `_smCreateMonitorWindow` and `_smSensorPoller.start`
- 反驳者结论：Genuine gap. React's transient tab button (components/TransientPanel.tsx:348-378, id="transient-solve-btn") calls session.runJob('solve') directly after only a validated/meshReady disable check. run/session.ts:290-328 (runJob) does no gate, no monitor, no poller work — it builds the request, POSTs /api/run-mesh, and streams the log. Repo-wide greps over react/src and react/test show openSolverMonitor and shouldOpenMonitor called from exactly one site (components/SolverPanel.tsx:813, inside SolverPanel's own submit()), and blockingMeshWarnings from exactly one site (SolverPanel.tsx:831, inside SolverPanel's run()). installSolverMonitorFeed / installMeshWarningFeed / installSolverMonitorBridge…

**[medium]** The sensor XYZ entry row is in the project's input unit: with meta.inputUnit = mm the legacy labels the row 'mm', multiplies what was typed by the unit scale before storing metres, and prints each probe's coordinates back in that unit at that unit's precision.

- legacy 依据：src/frontend/web/STL_generate.html:23206-23222 `window.addSensorPoint` (`var scale = unitInfo.scale || 1; var x = inputX * scale …`), the `#sensor-input-unit` label rewritten at :23257, and the list/drag readouts at :23254 and :23306 (`_formatSensorCoordText`)
- 反驳者结论：Genuine gap on the live path. The mounted sensor UI is SensorPanel.tsx (App.tsx:135). Its entry row hard-codes the unit label (`<span id="sensor-input-unit">m</span>`, SensorPanel.tsx:108), uses the metre step 0.0001 on all three NumberFields, and passes the raw typed values to store.addSensorProbe(x, y, z) (SensorPanel.tsx:113), whose own doc comment at projectStore.ts:786 states the arguments are METRES and writes geometry.location unchanged. The readout uses formatProbeCoord (run/sensorSpheres.ts:134), a unit-free `toFixed(2)` on metres, for both the list row (SensorPanel.tsx:142) and the drag update (sensorSpheres.ts:217). NumberField.tsx is unit-blind, and SensorPanel imports none of le…

- **已修** —— 录入行的单位标签、step 与换算全部跟 `meta.inputUnit`：`sensor-input-unit` 取 `LENGTH_UNITS[unit].label`，step 取 `stepFor(unit)`（mm 是 0.1，不再是米的 0.0001），`addSensorProbe` 前先 `fromSensorDisplay` —— 它的入参是**米**，此前把 3000 原样传进去，等于把探针放到 3 公里外。列表读数与拖拽时直接写 DOM 的那一处都改走 `formatSensorLocation`（legacy 的 `_formatSensorCoordText`，:23206 —— 那边同样只有一份，就是为了拖拽中途两边不会各说各话）。
  浏览器实证（Base_leakage 切到 mm）：标签 `mm`、step `0.1`、既有探针读作 `X 2150.0000  Y 3100.0000  Z 890.0000 mm`；录入 3000/1200/500 → 存下来是 `{x:3, y:1.2, z:0.5}`（米），行读回 `X 3000.0000  Y 1200.0000  Z 500.0000 mm`，日志 `addSensorPoint sensor_6 (3, 1.2, 0.5)`。
  过程中一个**假故障**值得记一笔：把三个框和「+」放在同一个 eval 里连发，Z 会存成 0 —— React 18 对同一任务里的离散事件还没 flush，按钮闭包拿的是旧的 z。拆成两个 eval（也就是真实用户必然的两次任务）就是正确的 0.5。判据要选真正随目标状态变化的东西：这里是存进工程的米值，不是 DOM 里的框值。

**[small]** The solver-accuracy preset row tells the truth after you hand-edit the advanced settings: legacy re-derives the preset from the actual schemes and relaxation factors on every scheme/relaxation change, so the three buttons un-highlight (preset becomes 'custom') once the numbers no longer match any preset.

- **已修** —— `solverPresets.ts` 加 `detectPreset(sim)` 与 `setSolverScheme(group,key,value)`：面板里所有格式/松弛因子（以及非正交修正器个数）的手改都走它，一个 undo 检查点里写值 + 反推名字，与 legacy `setSolverScheme`（:26092）同序。顺带把 legacy 的 DOM id 补齐（`slv-div-U/T/ke`、`slv-laplacian`、`slv-sngrad`、`slv-nnonortho`、`slv-relax-p/u/t/ke`），parity 扫描按 id 找控件。
  **与 `_detectCurrentPreset` 的两处刻意分歧**：① **认非正交修正**。legacy 拿原始预设比，而勾了非正交修正之后预设写进去的是修正版 laplacian/snGrad —— 于是在 legacy 里勾着这个框时，按下平衡再动任何一个数都立刻变 `custom`；`case/` 里 **15/22** 个工程勾着它。这里比的是"现在按下那个按钮会写出什么"，守住真正要紧的不变式：预设刚写完就必须被认成那个预设。② **比它写的每一个字段**，不是 legacy 的五个 —— 否则手改 div(T) 会在一个已经不平衡的算例上留着"平衡"的高亮。修正器个数照 legacy 排除在外（非正交会把它抬到 1，比它会让所有勾着框的 fast 单凭这条就变 custom）；工程里没有的键不算证据（跳过），但四个判别字段缺一就是 `custom`，免得空 schemes 空对空匹配上 fast。
  **连带修掉一个这次才够得着的坑**：`nonOrthoWrite` 对不认识的预设名回退到 fast，而 `custom` 从此是可达值 —— 取消勾选会把用户自己选的 laplacian 悄悄换成 fast 的。改成"不认识就什么都不写"（legacy 查不到就整段跳过，:26082）；原来那条断言 fast 回退的测试同步改写并注明为什么变。
  浏览器实证（Base_leakage，非正交修正勾着）：按 平衡 → `solverPreset=balanced`、只有 平衡 高亮；`slv-relax-u` 改 0.65 → `custom`、三个按钮**全部**取消高亮（classes 全是 `btn`）；改回 0.7 → 又变回 `balanced` 且重新高亮；再把 div(T) 换掉 → 又是 `custom`（这一条 legacy 根本不比）。操作日志里是 `[applySolverPreset] balanced` → `[setSolverScheme] U = 0.65` → `U = 0.7` → `divSchemeT = ...`。守卫：`test/solverPresetDetect.test.tsx`（11 条：三个预设的往返不变式、勾非正交时同样成立、缺键不算分歧、空配置不得空对空匹配、一步 undo 同时还原值与名字）。
  另记：真实工程里 `custom` 早就是合法存量值（`Base2-9` 就存着），后端 `solverPreset: str` 只透传不分支，导出链路不受影响。

- legacy 依据：src/frontend/web/STL_generate.html:26107 `_detectCurrentPreset()` (returns 'custom' when nothing matches), called from `window.setSolverScheme` :26092/:26102 and from the inline div(k,ε) handler at :372, with `_syncSolverPresetUI` :26120 clearing the button highlight
- 反驳者结论：I could not find any re-derivation of the preset in React; the claim holds. What I searched and what I found: 1. `src/run/solverPresets.ts` (read in full, 183 lines). It has `SOLVER_PRESETS`, the pure `presetWrite()` / `nonOrthoWrite()` helpers, `applySolverPreset()` (:164-173) and `setNonOrthoCorrection()` (:175-183). There is no `detectPreset`, no comparison of the live `schemes`/`relaxationFactors` back against `SOLVER_PRESETS`, and no export that would let a caller do it. The only ways the module reads the preset are `nonOrthoWrite(on, sim.solverPreset)` at :179 — i.e. it *trusts* the stored name. 2. `src/components/SolverPanel.tsx`. The three buttons (:551-566) highlight with a literal …

**[small]** Each probe in the sensor list carries a colour dot in the palette position that matches its 3D ball and its curve in the monitor chart legend, so a curve can be traced back to a probe by colour.

- **已修** —— 每行行首补上 8px 圆点（`data-sensor-dot`，legacy 的行序也是 点 → 勾选框 → 名字，:23267），颜色按**在探针列表中的位置**取色板 —— 与 3D 小球、曲线图例用的是同一条规则（不按图表顺序：还没上报的探针不在图里，按图表顺序会让别的曲线在它出现的瞬间集体换色）。
  **顺手把色板合并成一份**：原本 `sensorSpheres.ts` 的 `SENSOR_COLORS` 与 `solverChart.ts` 的 `SERIES_COLORS` 是两份一模一样的十个色值，现在都指向新的 `run/sensorPalette.ts`。三个消费者索引两份列表，正是"小球和曲线哪天不再同色而且什么都不会失败"的形状。
  浏览器实证（Base_leakage，4 个探针）：行圆点 `rgb(224,108,117) / (229,192,123) / (97,175,239) / (198,120,221)`，`scene.traverse` 读出的小球材质是 `#e06c75 / #e5c07b / #61afef / #c678dd` —— 逐个对上，且圆点都是行的 `firstElementChild`；截图里视口那四个球的颜色与右侧四行肉眼可对。守卫：`test/components/SensorRowColor.test.tsx`（5 条，其中两条分别对着 3D 小球和图例的色板断言，色板一旦分叉就红）。

- legacy 依据：src/frontend/web/STL_generate.html:23267-23271 (`var sColor = _tcPalette[sIdx % _tcPalette.length]` then the 8px round `dot` prepended to each row), same palette as `_smTempColors` :25064 and `_smSensorColorMap` :25067
- 反驳者结论：Genuine gap, small but real. React implements BOTH endpoints of the colour contract but not the panel swatch: IMPLEMENTED in React: - src/frontend/react/src/run/sensorSpheres.ts:40-47 — SENSOR_COLORS (identical ten hexes to legacy window._smTempColors) + sensorColor(index) with modulo wrap; used to tint the 3D balls at sensorSpheres.ts:289. - src/frontend/react/src/run/solverChart.ts:146-148 — seriesColor() delegates the 'sensor' tab to that same sensorColor, and SolverMonitorWindow.tsx:171-175 resolves a probe's colour by its POSITION IN THE PROBE LIST (not chart order), so curve colour == ball colour. - SolverMonitorWindow.tsx:396-404 and :429-437 render 12x4 rounded swatches in the chart …

### 后处理（6）

**[medium]** 清除全部渲染 (Clear all render) tears the whole post-processing session down: it deletes every slice and streamline set and their colorbars, disposes the volume, removes the preview ring and the AI box, restores the geometry from its dimmed 15% back to solid, resets the set-id counters, and hides the post-processing controls so the user has to press 开启后处理 again.

- **已修** —— 3D 那一半本来就有（`__ppViewport.clear()` 清空 Three.js group），缺的是**会话**那一半，而它属于另外两个 React 子树：切片/流线集合与 id 计数器在左面板，几何 15% 变暗是右面板自己的滑块。所以按下去只是把画面擦了，列表还在 —— `Slice #1` 连着色标留在那儿、什么都没画，下一刀还编号成 #2。新增 `postprocess/ppClearAll.ts`（一个纯事件总线，**没有**并进 `ppViewState`，因为那里每项都有"当前值"可供迟到的订阅者读，而这个没有）。浏览器实证（Base_coldAisleIso）：切两刀 → 列表 2 条、色标 12 个、几何 15% → 按 清除全部渲染 → 列表 0 条、色标 0 个、几何 100%；此时点 应用 **一个网络请求都不发**（`initialised: false` 生效）；重新 开启后处理 后下一刀编号回到 **#1**。守卫：`test/ppClearAll.test.ts`（5 条，含"通知期间退订不能让邻居被跳过"）。

- legacy 依据：src/frontend/web/STL_generate.html:24129 `window.ppClearAll` (runs to :24240; `_ppCloseSliceEditDialog`, `_ppRemoveSeedSphere`, per-set colorbar removal, `_ppAiRemoveVisuals`, `state.initialized = false`, `_restoreOriginalGeometry()`, `_ppSliceNextId = 1`), bound from the button at :972 and also called on project load at :14955
- 反驳者结论：Partial only — the session teardown is genuinely missing. React's 清除全部渲染 button (PostprocessRightPanel.tsx:337) calls window.__ppViewport.clear(), assigned in exactly one place (Viewport.tsx:1239) to the two-statement clear() at Viewport.tsx:1080: dispose the seed gizmo, then applyPostprocessLayers with an invisible surface, which via ppLayers.ts:155 -> clearPostprocessLayers (ppLayers.ts:123) does empty the Three.js group (slices, streamline tubes, volume, ring, AI box). So the 3D wipe exists. Everything else in legacy ppClearAll (STL_generate.html:24129-24245) does not. Verified by exhaustive search: grep for clearAll/ppClear/clearPostprocess/resetPostprocess/pp-clear-all/clear_all_render/…

**[medium]** A newly cut slice is colour-scaled to a robust 2–98% percentile of its own scalars, not to the raw min/max, and that percentile is what lands in the 值域范围 Min/Max boxes and what the slice colorbar's 自动缩放 returns — so one outlier cell cannot wash the cut to a single colour.

- **已修（单测 + 浏览器双实证）** —— `postprocess/index.ts` 新增 `sliceDisplayRange(payload)`：有标量就取自身的 2–98 百分位（`percentileRange` 早就移植了，但两个调用点都只服务体渲染），没有就退回 header 的原始范围。新切片与切片编辑两条路都改用它，`clim` 与 `range`（也就是色标 自动缩放 的返回值）一起改。守卫：`test/sliceDisplayRange.test.ts`（5 条，含「一个热点离群值不再把量程拉宽 105 K」、NaN、零宽度下限），red-green 验过。

  浏览器实证（Base_coldAisleIso，Y=1.4248 处切 T，3383 点）：直接打 `/api/pp/slice` 解出 header 的**原始** `scalarRange` 是 288.362–295.237 K = **15.21–22.09 °C**，而面板的 值域范围 显示 **15.69–22.08** —— 冷端那条尾巴被 2% 分位剪掉了，最大值恰好落在 98 分位上所以基本不动。旧代码这里会显示 15.21。

- legacy 依据：src/frontend/web/STL_generate.html:22943 `var slicePR = window._ppPercentileRange(scalars, 0.02, 0.98, null);` → `smin/smax` become the default `clim`, are written back into `pp-slice-clim-min/max` (:22957-22958) and stored as `sliceInfo.scalarRange` (:22987), which is what the per-slice colorbar's `onAutoScale` returns (:23044)
- 反驳者结论：I could not find any React implementation of a percentile-based colour range for slices, and I can state exactly where it would have to live. What I searched (all under /mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react): - `grep -rn "percentileRange|quantile|percentile|robust|0.02|0.98" src/ test/` — the ported helper is `src/postprocess/ppVolume.ts:88` (`percentileRange`, re-exported at `src/postprocess/index.ts:456`), and it has exactly TWO call sites, both volume-only: `src/components/PostprocessPanel.tsx:747` (`applyVolume`) and `:1145` (the volume colorbar's `onAutoScale`). No third caller anywhere in src/ or test/. - `grep -rn "scalarRange" src/ test/` — every slice path takes th…

**[medium]** Mesh preview stays usable on a large case: above 200 patches the legacy drops the per-patch objects and rebuilds the whole preview as one merged surface plus one merged edge set (2 draw calls), re-merging as patches are checked on and off.

- **已修** —— 与 #137 是同一个特性，见上。

- legacy 依据：src/frontend/web/STL_generate.html:24437-24449 (`var _MP_MERGE_THRESHOLD = 200; if (data.patches.length > _MP_MERGE_THRESHOLD) { ... _mpRebuildMerged(state, allNames); state._isMergedMode = true; }`) and the merged branch of `mpUpdateVisibility` at :24574-24578
- 反驳者结论：I could not find any merged/large-scene path for the mesh preview in React, and I can state exactly where it would have to live. What React actually does (`/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/run/meshPreview.ts:77-151`): `buildMeshPreview` does `decoded.patches.forEach(...)` and unconditionally creates one `THREE.Mesh` (MeshPhongMaterial) plus one `THREE.LineSegments` per patch, adds both to the group, and records the pair in `byPatch`. There is no patch-count branch, no threshold constant, no merged geometry. `setPatchVisible` (:154-159) and `setMeshOpacity` (:161-168) both work purely off that per-patch map, and `disposeMeshPreview` (:170-181) just traverses the gro…

**[small]** With the patch filter active, a group checkbox (the RACK/ACU/WALL kind row, or a per-model sub-row) toggles only the patches the filter left on screen; the mesh preview's 全选/全不选 does the same. Filtering to 'acu' and ticking a group is how a user turns on just those patches without disturbing the rest.

- **已修** —— `patchListDom.namesOnScreen(container, names)` 复刻 legacy 那三处同一行守卫（:21029 / :21080 / :24565），由 `usePatchListControls` 以 `controls.onScreen()` 暴露，六个调用点全部走它：网格预览的 kind / model 勾选框 + 全选/全不选，后处理树的 kind / model 勾选框 + 全选/全不选。只排除**存在且带 `filter-hidden`** 的行 —— React 折叠分组时行根本不渲染（legacy 只是 `display:none`，所以那边不会遇到），把"没有行"也当成被过滤掉会让折叠分组的勾选框彻底失效。
  **只改动作还不够，浏览器实证里当场发现的**：勾选框的 `checked` 原本由整组名字推导，Base604 有 22 个 patch 默认隐藏 → 组框恒为 unchecked → 过滤到 acu 之后每次点击都被解读成"打开"，而屏幕上那 48 个本来就是开的 —— **delta 恒为 0，组框卡死点不动**。所以过滤变化现在也触发一次 React 重渲（`bumpFilter`），`checked` 改为按 `onScreen()` 的子集推导。
  浏览器实证（Base604，4336 patch / 4314 勾选）：过滤 `acu` → 48/4336，`_other` 组框显示 **checked**；点一下 → 恰好 **48** 个关掉、被过滤掉的 4266 个**分毫未动**（前后都是 4266）。守卫：`test/patchFilterGroupToggle.test.tsx`（9 条，含纯函数三条与"折叠分组仍整组生效"）。
  写测试时踩到的坑记一笔：React 的 checkbox `onChange` 是从 **click** 事件派生的（`shouldUseClickEvent`），派发 `change` 事件一个 handler 都不会跑 —— 第一版九条全红，红的却是这个原因而不是被测的那条。

- legacy 依据：src/frontend/web/STL_generate.html:21025-21031 (kind checkbox: `if (row && row.classList.contains('filter-hidden')) continue;`), :21076-21082 (the identical guard on the per-model checkbox), :24561-24568 `window.mpToggleAll` with the same guard
- 反驳者结论：GENUINE GAP — confirmed, not a rename. What React has (the filter machinery is fully ported): `applyFilterToDOM` toggles `filter-hidden` on `div[data-patch]` rows and `filter-hidden-group` on empty ancestors (/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/geometry/patchListDom.ts:38-67); `getVisibleOrderedNames` (:77-81) excludes filtered rows and IS consumed — but only by shift-range row clicks (patchListControls.ts `onRowClick`) and by viewport picking (patchViewportPick.ts:84, :88). `patchList.css:62-67` makes `.filter-hidden`/`.filter-hidden-group` pure `display:none`, so — exactly as in legacy — a filtered row keeps its 3D visibility state; "toggle only what is on screen" i…

**[small]** The 渲染场光照强度 slider brightens or dims the volume rendering along with the surface and the tubes — the legacy pushes the same value into the ray-marcher's `uBrightness`.

- **已修（源码级 red-green + 浏览器实证）** —— `applyPpLightIntensity` 那一次遍历里多写一个 uniform：面/切片/流线管都叫 `uLightIntensity`，而体渲染的 ray-marcher 没有这个 uniform，它按 `uBrightness` 缩放行进颜色（`ppVolume.ts:194`）。所以遍历径直走过它，滑块把周围都调暗、独独留下那层纯颜色最亮。legacy 的 `ppSetLightIntensity` 也是两个都写（STL_generate.html:22304）。守卫：`test/ppLightIntensity.test.ts`（5 条，源码断言：体渲染确实用 `uBrightness`、applier 只有一份、两个 uniform 写的是同一个 `value`）。

  浏览器实证（G 查清之后补上）：体渲染上屏后把 渲染场光照强度 从 1 拖到 0.3 → `uBrightness` 与 `uLightIntensity` **同时**变成 0.3，截图里那块体渲染肉眼可见地暗下去，拖回 1 又亮回来。

- legacy 依据：src/frontend/web/STL_generate.html:22304-22306 in `ppSetLightIntensity`: `if (state.volumeMesh && ... .uniforms.uBrightness) { ... .uBrightness.value = val; }`, against the uniform declared at :20038 and used at :20130 (`color.rgb *= uBrightness`)
- 反驳者结论：Genuine gap — the slider exists in React, but its volume half does not. What I verified: 1. `grep -rn "uBrightness|Brightness|brightness"` across the whole React tree (src/ and test/) returns exactly three hits, all in `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/react/src/postprocess/ppVolume.ts`: the uniform declaration (:129), its use in the fragment shader (`color.rgb *= uBrightness`, :194), and its initialiser `uBrightness: { value: 1.0 }` (:270). There is no write to it anywhere — not in Viewport.tsx, not in ppLayers.ts, not in the store, not in `src/automation/`, not in test/. 2. `buildVolume(payload, colorMap, clim, opacity, resolution)` (ppLayers.ts:212) takes no brightness ar…

**[small]** Loading the mesh preview shows the same staged, cancellable task dialog the other long jobs use, and on success logs the mesh's size to the operation log: '<N> patches, <N> cells, <N> boundary faces, <N> points, <N> triangles'.

- **已修** —— 与上面"网格统计读数"和"取消载入"是同一条（这份清单里重复登记了两次），一起在 `dc59b162` 做掉：分阶段对话框 + 取消 + `<N> patches, <N> cells, …` 落面板与操作日志。实证见那一条。

- legacy 依据：src/frontend/web/STL_generate.html:24341-24345 (`showBlockingTaskDialog({ title: t('mp.loading_mesh'), onCancel: ac.abort })` plus `task.log(...)` at :24348/:24354/:24358) and :24470-24483 (the `nCells` / `nBoundaryFaces` summary, with the `nBoundarySum` fallback, into `logOperation` and `task.done`)
- 反驳者结论：I could not refute this one. The three pieces of the claim are each independently absent from React, and I found where each would have to live. 1. **Staged, cancellable dialog.** React DOES have the ported dialog: `src/components/PpInitTask.tsx:7` is explicitly "A port of `showBlockingTaskDialog` (STL_generate.html:23930)". But its only two call sites are `src/components/PostprocessPanel.tsx:1229` and `:1240`. `grep -rn "PpInitTask|showBlocking|BlockingTask|TaskDialog"` across `src/` returns nothing in `MeshPreviewPanel.tsx` or `run/meshPreview.ts`. Likewise `AbortController` appears in `PostprocessPanel.tsx:111,115,420,733`, `liquid/liquidSession.ts:58,144` and `workspaceReset.ts` — never i…

### 液冷（4）

**[large]** Orienting a T-branch (pipe_junction): selecting one in liquid mode attaches a Z-axis rotate gizmo whose drag turns the junction body and its six port spheres, and re-routes the endpoint of every pipe connected to it onto the rotated ports.

- **已修**，而且真正的病因比条目写的更深。gizmo 本来就在 React 里（`js/liquid_mode.js:261` 随共享模块一起加载，它读的每个全局 `camera`/`renderer`/`scene`/`controls`/`globalGroup`/`_modelMap`/`pushUndoCheckpoint`/`getSelectedModel` 都由 `liquidScene` 装好了），**缺的只是没人调用**。但补上调用后仍然不出现——`renderPipeJunction`（共享模块）停在 pre-V4：`!junctionModel.enabled`（V4 是 `meta.enabled`）让每个 junction 在第一行就 return，**cube 从来没画出来过，两个编辑器都一样**；`junctionId: junctionModel.id`（V4 是 `uid`）又让 `attachJunctionGizmo` 永远找不到目标。第三处在 React 侧：选中判断读了 `registriesRef.modelMap`，而 junction 不是几何构建器画的，那张表里根本没有它。浏览器实证：选中 T-Branch 出现单条 Z 环 → 拖 90° 写入 `cooling.quaternion={0,0,0.8366,0.5478}`、一条 undo；接在 port(0.3,0,0) 上的管子端点从 (1,1.2,1) 移到 (0.880,1.475,1)，与绕 Z 转 113.5° 完全吻合。守卫：`test/legacy/pipeJunctionRender.test.ts`（7 条，直接跑共享模块）。

- legacy 依据：shared/js/liquid_mode.js:214 `_ensureJunctionTC` (TransformControls mode 'rotate', showZ only), :243 `objectChange` writes `model.cooling.quaternion` then calls `window._reroutePipesForJunction`, :261 `attachJunctionGizmo`, :289 `refreshJunctionGizmo`; call site STL_generate.html:6397-6408 (`if (__lcSel.kind === 'pipe_junction') window._liquidMode.attachJunctionGizmo(...)`); the renderer that cons
- 反驳者结论：Genuine gap. React dynamically imports shared/js/liquid_mode.js (react/src/legacy/liquidScene.ts:211-215), so window._liquidMode.attachJunctionGizmo/refreshJunctionGizmo exist at runtime, and React even installs their prerequisites (THREE.TransformControls polyfilled onto the global THREE at src/interaction/translateGizmo.ts:151-152; camera/renderer/scene/controls/_modelMap/getSelectedModel in liquidScene.ts + liquidGlobals.ts). But the CALL SITE is not ported. The legacy branch at STL_generate.html:6397-6408 has no React counterpart: the only `liquidMode()?.` call in the entire app is Viewport.tsx:1250 -> onPipeSelectionChanged, which in liquid_mode.js:2379-2397 handles pipe_segment only an…

**[medium]** The colour scale for pipe temperature colouring: switching on 管道温度着色 in the liquid results panel pops a draggable colorbar showing the T range, with editable min/max clim, an auto-scale back to the data range, and a colormap preset — and applying any of them re-colours the pipes.

- **已修** —— 根因就是上面的 C（React 没装 `window._ppCreateColorbarWidget`）。桥装好后这条自动通了：浏览器实证 `cell_temp_K` 305.5–320.1 K → 标尺 32.4 °C / 47.0 °C；双击开对话框，改 min/max 为 35/42 再「应用」→ 标尺变 42.0/35.0 且 `onApply` 回调触发了`colorPipesByTemperature(true)` 重新着色。

- legacy 依据：shared/js/liquid_pipe_renderer.js:892-909 inside `colorPipesByTemperature` — `window._ppCreateColorbarWidget({label:'Pipe Temperature', fieldName:'T', climMin, climMax, cmapPreset:_lcCmapPreset, onAutoScale, onApply(min,max,cmap){ _lcClimMin=...; colorPipesByTemperature(true) }})`, with `_lcClimMin/_lcClimMax/_lcCmapPreset` at :743 and the widget factory supplied by the host at STL_generate.html:1
- 反驳者结论：Genuine gap, confirmed. React ports the widget as src/components/ColorbarOverlay.tsx but renders it only from the AIR PostprocessPanel (4 call sites: PostprocessPanel.tsx:1051 surface, :1079 slices, :1105 streamlines, :1133 volume). The liquid path never reaches it. Evidence: 1. The factory `_ppCreateColorbarWidget` is defined ONLY in the legacy host page (web/STL_generate.html:19652). shared/js/liquid_pipe_renderer.js:892-909 merely CALLS it behind a `typeof window._ppCreateColorbarWidget === 'function'` guard, so the "implemented in shared/ ⇒ exists in React" rule does not apply here — the capability lives in the host, and React is a different host. 2. A whole-tree grep of react/** (minus …

**[small]** Font sizing on the liquid post-processing chart: the A− / A+ buttons in the chart window header scale the axis labels, tick numbers and legend text between 0.6x and 2.0x.

- legacy 依据：shared/js/liquid_solve_monitor.js:1035-1048 — the `lsm-pp-font-up` / `lsm-pp-font-down` header spans clamping `window._lsmFontScale` to [0.6, 2] and redrawing; consumed throughout `_lsmDrawPPChart` (:1148 `Math.round(12*fs)`, :1154-1155 margins) and `_lsmUpdatePPLegend` (:1238). The same pair exists on the live monitor window at :522-531.
- 反驳者结论：Genuine gap, confirmed by exhaustive search. The STATE exists but is dead: src/liquid/liquidPostprocess.ts:307-325 defines PpWindowState.fontScale + PP_FONT_STEP/MIN/MAX (0.1/0.6/2) and :358-363 defines nudgePpFontScale() with the correct clamp, with a doc comment explicitly naming "the A− / A+ header buttons". But grep -rn 'nudgePpFontScale' over src+test returns ONLY the definition and test/liquidPpWindow.test.ts:58-71 — no production caller anywhere. The window header (src/components/LiquidPostprocessWindow.tsx:156-188) renders exactly two controls, lsm-pp-collapse-btn and lsm-pp-close; there is no font pair. The consumer side is missing too: TimeSeriesChartProps (src/components/TimeSerie…

- **已修** —— 表头补上 `lsm-pp-font-down` / `lsm-pp-font-up` 两个 A−/A+，接到本来就写好、也有测试、但没人调用的 `nudgePpFontScale`（0.6–2.0 的钳位照 legacy :1038-1048）；`TimeSeriesChart` 新增 `fontScale` prop，画布上**每一处** `ctx.font` 都过同一个 `fs()`，左右边距按 `max(base, base*fs+5)` 跟着长（legacy 的 :1155 同款），图例 `#lsm-chart-legend` 的 `fontSize` 也跟着 `Math.round(10*fs)`（legacy :1238）。
  **写的时候自己踩了一个坑并被测试逮住**：`fontScale` 一开始没进 `useEffect` 的依赖数组 —— 状态变了、按钮看着有反应、图却还是旧字号。回退这一行确认那条测试**会红**。
  浏览器实证（LiquidSingle；仓库里没有液冷结果文件，所以只把 `/api/liquid/results` 的响应打了桩，其余全是真组件真画布）：给 `CanvasRenderingContext2D.prototype.font` 挂探针，开窗时是 `10px monospace`；A+ ×10 后是 `20px monospace`、图例 `20px`；A− ×20 后是 `6px monospace`、图例 `6px`（两端都停在钳位上）。两张截图肉眼可见坐标数字与图例一大一小。守卫：`test/components/PpChartFontScale.test.tsx`（7 条）。

**[small]** Starting the valve→rack-manifold connection by dragging directly on the selected valve body (rubber-band dashed line to a manifold port, snapping within 0.3 m and setting the fitting's connectedRackId / connectedManifoldPortId).

- legacy 依据：shared/js/liquid_mode.js:2009 `_startValveDrag`, :2034 `_updateValveDrag`, :2117 `_endValveDrag` (manifold snap, `valve.connectedRackId = best.modelId` at :2182); wired by the host at STL_generate.html:18204 (pointerdown), :18239 (pointermove), :18305 (pointerup). The wiring matters because shared/js/liquid_graphgen.js:617 only emits the rack spoke edges when `v.connectedRackId && v.connectedManif
- 反驳者结论：Could not refute. Case-insensitive grep for `ValveDrag|valveDrag` over react/src and react/test returns zero hits, and since `_startValveDrag`/`_updateValveDrag`/`_endValveDrag` are reachable only by name off `window._liquidMode` (liquid_mode.js:2544-2546), that grep is decisive. `LiquidModeApi` (react/src/legacy/liquidScene.ts:56-71) declares renderValves, deselectAllValves, clearWaypointSpheres, buildWaypointSpheres, onPipeSelectionChanged, detachJunctionGizmo, handleControlPointClick, handleValveClick, handleContextMenu — and none of the three drag methods. Viewport.tsx's onPointerDown (registered :729) runs only rotateGizmoRef.begin and faceDragRef.begin; onPointerMove (:730) is faceDrag…

- **已修（接线部分）** —— 三个方法（`startValveDrag`/`updateValveDrag`/`endValveDrag`）本来就在共享模块 `liquid_mode.js` 里、也挂在 `window._liquidMode` 上，React 只是从没调用过。现在 `LiquidModeApi` 声明了它们，视口的 pointerdown/move/up 各接一处；顺手把 pointerdown 那条链抽成 `interaction/viewportPointerChain.ts` 的 `dispatchPointerDown`，按 legacy 的顺序（**阀门 → 种子 hub → 旋转环 → 改边把手**，:18204-18211）逐个问、谁要了就短路 —— 这些东西全是 depthTest 关掉画在目标上方的，顺序不是装饰。守卫：`test/viewportPointerChain.test.ts`（6 条：顺序、短路、没人要时四个都只问一次、液冷模式外没有阀门那一步）。
  **实证时先撞上一个更根本的问题并一并修掉**：干净加载后**阀门一个都没画出来**（工程里有 16 个），因为只有 `redrawPipes()`（拖拽时才走）会调 `renderValves()`；没有阀门 mesh 就选不中阀门，选不中就永远开始不了这个拖拽。现在视口每次重建后与模块装载后都补一次 `renderValves()`。对照数：**修前 0 个阀门 mesh，修后 16 个**。
  浏览器实证（case/Liquid，真的 canvas 事件）：pointerdown 落在选中的阀体上 → 场景里出现 1 条 `LineDashedMaterial` 橡皮筋（此前 0 条）；pointermove → 它的末端跟到 `[-0.61, 1.91, -1.24]`；pointerup → 线消失（`null`），之后再 move 也不再出现。截图里那条白色虚线清晰可见。
  **没验到的一半，如实记下**：落点吸附（0.3 m 内写 `connectedRackId`/`connectedManifoldPortId`）我用合成事件没能复现 —— 21×9 个屏幕采样里落点离目标端口最近也有 **0.595 m**，始终在吸附半径外，所以那次写入没有发生。那段是共享模块自己的几何、我没有改动；本条只声称"手势接上了并且真的活着"。

### 工程 I/O、视图与 2D 设计器（4）

**[medium]** Re-opening the 2D room designer does not pick up a ceiling height that was changed in 3D: the legacy re-derives the room height from the actual ceiling model before showing it, React shows the stale stored planeHeight (so Apply silently drags the ceiling back to the old height).

- **已修** —— `Room2DEditor` 的 height 初值改成 `inferRoomHeight(project)`（React 早就移植了这条规则，只是唯一的调用者是网格 preflight）。`planeHeight` 只是「上一次 2D 应用写下的值」，谁在 3D 里拖了天花板它都不会跟着变；而设计器的 应用 又会把它写回去，于是**只是打开看一眼平面图就会把天花板悄悄拽回旧高度**。浏览器实证：天花板拖到 y=4.2 m（`geometry.height` 仍是 3.6）后打开 2D 设计器，房间高度显示 4200 mm 而不是 3600。顺带给高度框加了 `id="room2d-height"`。守卫：`test/components/Room2DHeight.test.tsx`（5 条，含"取最高的那块天花板"和"抬升地板不算天花板"）。

- legacy 依据：src/frontend/web/room_2d_editor.js:125 `_getExplicitRoomCeilingHeight` and :137 `_syncRoomPolygonHeightFromExplicitCeiling`, called first thing in `room2d.enterEditor` at :162 — it scans kind==='floor' models whose cooling.roomBoundaryLocation==='top' and takes max geometry.location.y, writing it into state.geometry.planeHeight before the editor reads it.
- 反驳者结论：Genuine gap, though narrower than "missing feature": React ported the derivation helper but never calls it from the 2D editor. React HAS the identical logic — `inferRoomHeight()` at src/frontend/react/src/run/meshPreflight.ts:84 reproduces the legacy rule exactly (bucket 'floors', `cooling.roomBoundaryLocation === 'top'`, `Math.max` of `geometry.location.y`, else `planeHeight ?? height`), and its doc comment cites the legacy source. But its ONLY importers are src/run/runSubmit.ts and src/run/meshPreflightUi.ts, feeding `collectFloorHeightErrors` (the mesh-preflight "floor above the ceiling" warning). Nothing under src/room2d/* or components/Room2DEditor.tsx imports it. Searches performed acr…

**[medium]** In STL preview mode, selecting a solid no longer shows the yellow outward-normal arrow (arrow + tip sphere at the region centre) that tells you which way that solid's normals point.

- legacy 依据：src/frontend/web/STL_generate.html:7488 `updateStlPreviewNormalHelper` (builds a THREE.ArrowHelper 0xfff27a plus a tip sphere from `getAverageNormalForGeometry`), invoked from `updateHighlights` at :6683 and from `doRebuildStlPreview` at :7610; cleared by `clearStlPreviewNormalHelper` at :7253.
- 反驳者结论：Genuine gap. React has exactly three ArrowHelper uses (geometry/seedHelperVisual.ts:54, geometry/orientationHelperVisual.ts:190-191, components/AxisGizmo.tsx:60) and none is preview-related; a full case-insensitive "arrow" sweep over src/ adds only room2dDraw airflow arrows and the move/translate gizmos. The shared modules do not supply it either: grep for ArrowHelper/NormalHelper over src/frontend/shared/js yields only geometry_scene.js:1280 (0xffcc66 patch-depth arrow), so it does not arrive for free through src/legacy/*.ts. getAverageNormalForGeometry IS reachable in React (declared customMesh.ts:95 and patchActions.ts:66, backed by shared geometry_pure.js) but is used solely by dominantF…

- **已修** —— 新增 `geometry/previewNormalHelper.ts`：`normalHelperSpec` 是 legacy 那段算术（0.45×最大边、下限 0.15、上限 1.2、头长头宽与球半径的三处 clamp、法向退化时回落 Y-up），`buildNormalHelper` 出 `ArrowHelper(0xfff27a)` + 同色球，`updatePreviewNormalHelper(scene)` 按当前选中的 region 找到**画出来的那块 mesh**、把 helper 挂到**它自己的父组**上 —— preview 组带着 world-to-local 矩阵，挂到场景根会站到矩阵之外去。平均法向走**共享的** `getAverageNormalForGeometry`（导出器用的同一份，另算一份就会出现箭头与导出绕序打架）。
  **实证时当场发现并一并修掉的真问题**：点 region 行同时也会 `onSelect(model.uid)`，模型选择变化触发整场重建，把刚挂上的箭头连同旧 mesh 一起丢掉 —— 表现是**第一次**点另一个模型的 region 没有箭头，而在同一模型内再点一次就有。对照数：修前 `helpersAfterFirstClick=0`，修后 `=1`。修法是在会重建的三个调用点（选择、拖拽、贴图载入）后都补一次 `updatePreviewNormalHelper`，主重建那处本来就有。
  浏览器实证（Base_leakage，STL 预览）：选中 `Default_Rack_1_fan_server_1_inlet_patch` → 场景里恰好 1 个 `isPreviewNormalHelper`，子对象是 `ArrowHelper` + 球，球在 `[1.78, 2.02, 3.3]`；把相机飞过去截图，黄色箭头与球清晰立在那块进风板上。守卫：`test/previewNormalHelper.test.ts`（11 条：三处 clamp、退化法向回落、以及"只画一个/换选就换/取消选择就没有/预览模式外不画"）。

**[medium]** Viewport view/snap preferences no longer survive a reload: the XOZ grid on/off, grid spacing, and the face/edge/grid snap toggles reset to defaults every time the page is opened, instead of coming back the way that project was left.

- legacy 依据：src/frontend/web/STL_generate.html:3247 `UI_PREF_KEYS = ['displayMode','view','snapFace','snapEdge','snapGrid','treeCollapsed','previewVisibility','previewRegionVisibility','previewRegionReverse','groups']`, with `loadUiPrefs` at :3254, `saveUiPrefs` at :3264 (key `cfdproj.ui.<uid>`), a debounced save at :3277 plus a beforeunload flush, and the restore on load at STL_generate.html:14968.
- 反驳者结论：Searched exhaustively and found no React implementation. Greps over src/frontend/react/src and test for `snapGrid|snapFace|snapEdge|gridSpacing|showGrid|DEFAULT_VIEW_OPTIONS` return only: the ViewOptions type and defaults (components/Toolbar.tsx:27-55), the toggle buttons (Toolbar.tsx:225-298), the viewport consuming them via viewRef (components/Viewport.tsx:682, 717-719, 872, 1309-1311, 1796-1801), a single `useState<ViewOptions>(DEFAULT_VIEW_OPTIONS)` (App.tsx:238), and App.tsx:384 which overwrites gridSpacingMm from the loaded room's tileSize. No reader or writer of storage touches them. Storage audit: `grep -rn "localStorage|sessionStorage|uiPref|cfdproj.ui|prefs|persist|beforeunload"` o…

- **已修** —— 新增 `src/ui/viewPrefs.ts`：按工程 uid 存进 **legacy 的同一个 key** `cfdproj.ui.<uid>`、**同一种结构**（`view:{showGrid,gridSpacing}` 用**米**、三个 snap 布尔在顶层），只有这一版才有的 `lights`/`rotationSnap` 放在 `react` 子对象里。两个编辑器共用一个浏览器 profile、同一批工程，换个 key 会让同一个设置在两边各说各话，换个结构则会直接写坏对方的存档。保存走 **read-merge-write**，`treeCollapsed`/`groups`/预览可见性这些 legacy 的字段原样留着。防抖 800 ms + `beforeunload` 兜底，都照 legacy。
  **restore 挂在 uid 上而不是文件对话框的回调里**：工程可以从对话框、远端路径、以及 `window.__dcdtuni.loadProject`（脚本和 parity harness 走的那条）打开，写在其中一处等于另外两处没有 —— 这一点是浏览器实证时**当场撞上的**：第一版写在 `applyLoadedProject` 里，用契约路线加载时不但没恢复，随后的防抖保存还把默认值**覆盖**回了存档。
  浏览器实证（Base_leakage）：关网格 + 开网格吸附 → 存档写入 `{"view":{"showGrid":false,...},"snapFace":false,"snapEdge":false,"snapGrid":true,...}` → **刷新整页** → 空白页是默认值（网格开）→ 用契约路线加载工程 → 网格关、网格吸附亮、面吸附灭，截图里视口确实一根网格线都没有。守卫：`test/viewPrefs.test.tsx`（17 条；"保存不吞掉 legacy 的键"那条回退后确认会红）。
  **顺手修掉的相邻缺陷**：工具栏「网格吸附」按钮用的是 `{...view}` 展开而不是函数式更新，同一批次里的前一个切换会被它按旧值覆盖 —— 这正是 `ToolbarProps` 注释里写明要避免的那件事，而它是唯一没照做的调用点。也是实证撞出来的：一个 eval 里连点三个按钮，`showGrid` 没变而两个 snap 变了。守卫 `test/components/ToolbarViewBatch.test.tsx`（3 条，中间那条回退即红）。

**[small]** The server folder-browser dialog (used to pick the case directory) can no longer be resized — the legacy dialog has drag handles on all four edges and four corners so a deep path tree can be widened.

- legacy 依据：src/frontend/web/STL_generate.html:18934-18941 — eight `<div class="fb-resize fb-resize-{r,b,l,t,br,bl,tr,tl}">` handles, with the `startResize`/`onResize`/`endResize` drag logic (min 380x320) at :18959-19020 and hover/active highlight wiring just above.
- 反驳者结论：Genuine gap. React's folder browser is FileBrowser.tsx (src/frontend/react/src/components/FileBrowser.tsx, read in full, 325 lines): it renders div.modal-backdrop > div.modal.file-browser with header/.fb-path/.fb-col-header/.fb-list/.fb-actions and contains zero pointer handlers (grep for onPointerDown|onMouseDown|clientX returns nothing) and no handle elements. Its size is pinned by CSS at src/styles.css:1330 — `.file-browser { width: min(760px, 88vw); height: min(560px, 78vh); }` — and `grep -n "resize:" src/styles.css` returns ZERO hits, so there is not even a native CSS `resize: both` fallback on .modal/.file-browser. Both call sites (Inspector.tsx:544, SystemPanel.tsx:140 case-dir picke…

- **已修** —— 新增 `ui/dialogResize.ts`（纯算术 `resizeRect` + legacy 的八个方向与 380×320 下限）与 `ui/DialogResizeHandles.tsx`（八个把手，`pointerdown` 时先把对话框**钉住**再拖）。钉住这一步是必需的：对话框由 backdrop 的 flex 居中，直接改 width 会让**两条边**一起动、光标底下那条只走一半，所以先把量到的 rect 写成 `position:fixed` 的 left/top/width/height，之后每帧都是对这个起始 rect 的算术。监听挂在 **window** 上，指针滑出 6px 的把手条也不会中断。
  浏览器实证（Base_leakage）：右下角从 760×560 拖到 **1000×680**；左边缘往左拖 200 → left 580→380、宽 1000→1200，而**右边缘 1580 纹丝不动**（远端不跟着走，正是 `resizeRect` 钉的那条）。
  **顺手挖到并修掉的相邻缺陷（同一个已知陷阱的第三次）**：这个对话框原本渲染在右面板的 DOM 里，而 `#right-panel` 带 `backdrop-filter: blur(6px)` —— 它于是成了 `position: fixed` 的包含块，所谓"全屏"遮罩实际只有 **339×920 @ x=1581**（右面板那么宽），760px 的对话框被挤成 **420**。改成 `createPortal(..., document.body)`（`PpInitTask`、`SensorEditDialog` 之前踩过同一个坑）后量到遮罩 **1920×1080 @ 0**、对话框 **760×560 居中**。守卫：`test/dialogResize.test.tsx`（14 条，其中"先钉住再拖"回退即红；portal 那条用带 `backdrop-filter` 的宿主容器复现）。

---

## 修复过程中新挖出来的（不在原 39 条里）

四条，全部是"跑起来才会露"的类型，其中两条**两个编辑器都中招**。

**A. `renderPipeJunction` 停在 pre-V4 —— T 型接头的方块从 V4 迁移以来就没画出来过。**
`src/frontend/shared/js/liquid_pipe_renderer.js:283` 读 `junctionModel.enabled`
（V4 是 `meta.enabled`），第一行就 return；两行后 `junctionId: junctionModel.id`
（V4 是 `uid`）。共享模块，两个 app 都一样。已修 + `test/legacy/pipeJunctionRender.test.ts`。

**B. 服务器芯片温度着色从来没生效过，而且每次调用都抛异常。**
同一个文件 `_toggleServerTemperatureColoring`：`child.userData.modelId === rackModel.id`
（场景里打的是 `model.uid`，`geometry_scene.js:1003`），所以没有一台服务器被重新着色；
紧接着调用的 `_updateColorbar` **整个仓库里没有定义**，于是每次都 ReferenceError。
已修（改读 `uid`；colorbar 改用管路那条路径已有的 `_lcColorbarWidget`）+ 3 条测试 +
浏览器实证（液冷服务器 `4dcc7a` → `2196f3`，关掉后原样恢复）。

**C. React 没有 `window._ppCreateColorbarWidget`。** 它把 colorbar 移植成了 React 组件
`ColorbarOverlay.tsx`，而共享的液冷着色路径（管路与服务器两条）都按全局工厂去找它。
两条路径因此在 React 里都不显示色标 —— 有守卫，所以是静默无标尺，不是崩溃。
**已修**：`src/legacy/colorbarGlobal.ts` 是那座桥（每个 widget 一个挂在 body 上的 React root，
`update/show/hide/destroy` 与 legacy 同形；`destroy` 走 microtask，因为至少有一条调用路径
是在 React 渲染中间调的，同步 unmount 会抛）。由 `installLiquidScene` 在导入共享模块**之前**
装好。修的过程中还揪出一处双重换算：`_showColorbar` 自己减了 273.15，而 widget 拿到
`fieldName:'T'` 又换算一次，45 °C 的芯片在标尺上显示 −228 °C；管路那条路径传的就是裸开尔文，
现在两条一致。浏览器实证：两台液冷服务器 318.4/306.2 K → 标尺 45.3 °C / 33.1 °C。
守卫：`test/legacy/colorbarGlobal.test.ts`（10 条）。

**D. `window.updateUI()` 不会刷新特征树。** 它走 `store.notifyLiveEdit()`，只改
`state` 外壳、`state.project` 引用不变，所以按 project 记忆化的树不重渲染。共享液冷模块
用 `addModel()`（`mutateLive`，不通知）+ `updateUI()` 的组合加模型 —— 加完树上看不到，
直到下一次真正的 `store.edit`。
**已修**：store 加了 `revision()`（`emit()` 时自增），`FeatureTree` 的两个 `useMemo`
把它列进依赖。没有改成"每次 live edit 换一个 project 引用"，因为那会连带重跑 Viewport 里
所有 `[project]` 的 **effect** —— 每调一次不透明度就重建一遍场景。浏览器实证：
`window.addModel()` + `window.updateUI()` 之后，树立刻出现 T-Branch 行、根节点计数变 1
（此前一直停在 `· 0`）。守卫：`test/components/FeatureTreeLiveEdit.test.tsx`（根节点计数
那条是真守卫，行列表那条 jsdom 下两种实现都过，只作行为说明）。

**E. ~~视口里点不中 T 型接头。~~ 这条我写错了，浏览器推翻了它。**
`interaction/picking.ts` 确实不认识 `isJunctionBody`/`isJunctionPort`，但那不重要：
几何构建器会像对待任何 construction 一样给 junction 画一个盒子（7 个带 `modelId` 的
mesh），点击命中的是那个盒子，`isBaseWall` 优先级 40 稳赢管路渲染器画的紫方块（优先级 0）。
实测点击 T 型接头 → `sel.uid=m_1` 且旋转 gizmo 立刻出现。legacy 的 `getHitPriority`
同样没有 junction 条目，所以这本来就不是 React 与 legacy 的差异。
**一度按这条改过 picking，实测证明改动完全无效，已整体回退。**

---

**F.（已撤回）~~后处理在 React 面板里根本初始化不了~~ —— 这条是我判断错了。**

后处理**能**初始化、**能**切片。上一轮我写下这条，依据是「点了 开启后处理 之后按钮还在」，
但那个按钮**从来不会消失** —— 它只在 `busy === 'init'` 时禁用，跟 `state.initialised` 无关
（PostprocessPanel.tsx:1226）。把「按钮还在」读成「没初始化」是纯粹的误读。

真实情况是我最早那几次确实失败了，但原因是下面那条操作陷阱（求解记录下拉没跟着
`set-case-dir` 刷新，init 拿旧算例的 run 去问后端）；等我按了 `pp-refresh-runs` 之后，
后面的 init 其实已经成功了，我却还在用那个坏掉的判据看它。

这一轮在浏览器里挂了 `window.fetch` 探针重跑，证据一次就清楚了：

```
/api/pp/init        200  22ms
/api/pp/mesh        200  15ms
/api/pp/mesh/binary 200  29ms
/api/pp/field/T     200  21ms
status = "Already loaded: 47 patches · 47 patches · 11 fields"
```

那条 status 只在**成功分支**（:474 之后）写，所以 `initialised: true` 早就跑到了。
紧接着点 应用 切片，`/api/pp/slice` 200、列表里出现 `Slice #1 (Y, T)`、值域范围 15.69–22.08。

**教训**：判据要选那个真正随目标状态变化的东西。这里该看的是切片按钮能不能出结果，
或者面板的 status 文本，而不是一个恒常存在的按钮。

**操作陷阱（这条是真的，留着）**：`求解记录` 下拉不随 `set-case-dir` 刷新，
必须先按旁边的 🔄（`pp-refresh-runs`），否则 init 会拿旧算例的 run 去问后端，
报「No time-step directories found」。

---

**G.（已查清并修复）折叠 配置体渲染 会把体渲染从场景里删掉。**

上一轮只记了现象、没下判断，这一轮按 systematic-debugging 查到底。

**决定性证据**是操作日志，不是网络：点 `pp-apply-volume` 之后 `__dcdtuni.getLog()` 的新增行是
「PP: 体渲染参数已更新」—— 那正是 `applyVolume` 的**缓存命中**分支（field 与 resolution 都没变，
直接 `applyLayers()` 返回）。所以不发请求是**对的**，网格早就取回来了；真正的问题在
`applyLayers` 怎么把它交给视口。

`applyLayers` 里那一行是：

```ts
payload: s.volume.show ? (volumeRef.current?.payload ?? null) : null,
```

`s.volume.show` 是 **配置体渲染** —— 那个把设置区 `display` 切成 none 的折叠开关，
不是可见性。`ppLayers` 按 `payload && visible` 决定建不建，于是折叠设置区 =
payload 变 null = 体渲染从场景里消失，而 显示体渲染 还勾着，屏幕上没有任何东西解释。

对照 legacy：`ppToggleVolumeConfig`（STL_generate.html:22475）**只**设那个 div 的 `display`
并填一下字段下拉，从不碰 mesh。所以这是 React 独有的分歧。

浏览器实测（`visible` 全程为 true）：折叠 → 带 `uBrightness` 的材质 **0** 个；展开 → **1** 个。
修完再测：从零开始跑一次体渲染（日志 `体渲染完成 (128x128x128, T: 15.0~22.1)`）→ 1 个材质；
折叠设置区 → 仍是 **1** 个；取消勾 显示体渲染 → 0 个；重新勾上 → 1 个。
守卫：`test/volumeVisibility.test.ts`（6 条）。

写这个守卫时还踩了一脚自己的坑：第一版断言 `not.toContain('s.volume.show')` 直接命中了我为
这次修复写的**注释**（注释里提了这个字段名），也就是一个只可能靠巧合通过的测试。
现在断言前先剥掉注释行。
