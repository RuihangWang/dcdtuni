# React 迁移缺口登记表

> 归档注记（2026-10-21）：本表是 2026-08-29 审计、2026-08-30 全部收口的历史登记。后续的第四轮对抗式审计见同目录的 [react-migration-gaps-round4.md](react-migration-gaps-round4.md)，其 39 条也已全部在浏览器里验证。当前前端状态见 [PROJECT_STATE.md](../../developer/PROJECT_STATE.md)。

审计 2026-08-29，全部 **77 项已实现并验证**（2026-08-30）。原始审计方法：三个 survey agent 并行枚举 legacy `STL_generate.html` 的用户可见能力并逐条在 React 源码中搜索实现，第四个 critic agent 逐条反驳（推翻 2 条误报）并补充 5 条遗漏区域。

状态含义：**VERIFIED** = 编排者亲自在浏览器里按过、并与 :8005 legacy 数值比对；**AGENT-VERIFIED** = 实现方给出了浏览器实测数值，编排者复核了其证据但未逐项重跑。

> `parity/id_diff.py` 仍为「HTML 有而 React 缺的 id：0」。注意它比的是 DOM 元素是否存在，不是行为是否正确——这 77 项里大部分是它看不见的。

## 网格 / 求解 / 运行（11 项：大 2 / 中 4 / 小 5）

| ID | 能力 | 体量 | 状态 |
|---|---|---|---|
| G01 | Live solver monitoring window during an air solve: floating draggable/resizable chart of residual convergence, per-server temperature and ACU return t | 大 | ✅ AGENT-VERIFIED |
| G02 | Per-surface mesh seed configuration: anchor readout, direction X/Y/Z, offset, reset to default, and the seed-helper visibility toggle | 中 | ✅ VERIFIED |
| G03 | 执行网格生成 pushes the CURRENT geometry (freshly built .cfdrun manifest + grouped STL files) to the server | 中 | ✅ VERIFIED |
| G04 | Pre-flight validation before a mesh run (digit-leading model names, floor Z vs room height, seed/geometry collisions, non-uniform door porosity, dupli | 中 | ✅ VERIFIED |
| G05 | Transient events for RACKS (power change, shutdown) and the one-click shutdown preset for every device kind | 中 | ✅ AGENT-VERIFIED |
| G06 | Refinement zone rename, per-zone visibility checkbox, and numeric MIN/MAX X/Y/Z bound entry | 小 | ✅ AGENT-VERIFIED |
| G07 | Freeze the whole UI (panels, tree, tabs, toolbar, file bar) while a mesh/solve job runs | 小 | ✅ VERIFIED |
| G08 | 检测网格状态 — surface the per-check checkMesh warning DETAIL, not just a count | 小 | ✅ AGENT-VERIFIED |
| G09 | Mesh-quality gate before 执行求解 — warn and require confirmation when checkMesh flagged severity ≤ 2 | 小 | ✅ AGENT-VERIFIED |
| G10 | Transient tab is shown only when solverType is transient, and switching away auto-returns to the solver tab | 小 | ✅ AGENT-VERIFIED |
| G26 | Model groups: create a named group from a multi-selection, rename in place, add to an existing group, remove a model from its group, dissolve, collaps | 大 | ✅ VERIFIED |

## 几何建模与编辑（23 项：大 3 / 中 7 / 小 13）

| ID | 能力 | 体量 | 状态 |
|---|---|---|---|
| G11 | Add a circle / rectangle PATCH to a model face (ACU supply-return openings, floor tiles, wall and partition-wall leakage, baffle openings) | 大 | ✅ VERIFIED |
| G12 | Rack model inspector: rack size (W/D), height mode (total vs unit height), unit count, slot width ratio, top/bottom safe margins, default span — all r | 大 | ✅ VERIFIED |
| G13 | Model library: pin a rack/ACU as a reusable template, then instantiate from it | 中 | ✅ VERIFIED |
| G14 | Delete a patch (plain and 'delete with cavity'), and auto-fill a patch across its face horizontally / vertically | 中 | ✅ VERIFIED |
| G15 | Wall-region inspector (a face split by patches): rename the region, set its opacity | 中 | ✅ VERIFIED |
| G16 | Partition-wall model inspector: dedicated W/D/H section and per-patch leakage rate (%) | 中 | ✅ AGENT-VERIFIED |
| G17 | Rack servers quick-add panel: list the rack's servers, add one by start-U + span + kind, delete one | 中 | ✅ AGENT-VERIFIED |
| G18 | Patch projection vector: show/hide the direction arrow and reset it to the face default | 小 | ✅ VERIFIED |
| G19 | Polygon floor: edit the bounding-box W/D of a non-rectangular floor (rescales the polygon) | 小 | ✅ AGENT-VERIFIED |
| G20 | Model-level 'Surface Orientation' section with a current-state badge and flip button | 小 | ✅ AGENT-VERIFIED |
| G21 | Keyboard: Delete/Backspace removes the selection; Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z undo-redo | 小 | ✅ AGENT-VERIFIED |
| G22 | First-person walkthrough: the crosshair click-to-select hand-off | 小 | ✅ AGENT-VERIFIED |
| G23 | Room temperature (ambient initial condition): preset select plus custom value | 小 | ✅ VERIFIED |
| G24 | ACU cooling-model driving inputs for linearCapacity / tableCapacity: set point, rated cooling capacity, rated return temperature, coolant temperature | 小 | ✅ AGENT-VERIFIED |
| G25 | Per-patch fixed surface temperature boundary condition (enable checkbox + surface temperature in °C) on wall, custom-STL and box patches | 小 | ✅ VERIFIED |
| G27 | Feature tree: drag a model node onto another to reorder it within its bucket | 中 | ✅ VERIFIED |
| G29 | Click a CATEGORY node to select every model of that kind at once | 小 | ✅ VERIFIED |
| G30 | Toolbar transform with NO selection falls through to the whole scene (rotate / mirror / reset the wrapperGroup) | 小 | ✅ AGENT-VERIFIED |
| G31 | 导出 CFD 工程包 as a single zip named after the project, with per-kind stlFile remapping to merged group files | 小 | ✅ VERIFIED |
| G74 | Drag a patch across its face, and resize a patch or a model by dragging its edge handles (with sticky face/edge snapping) | 大 | ✅ AGENT-VERIFIED |
| G75 | Drag-to-place creation: press-and-drag the Wall / Partition Wall / Baffle button to drop the model at a floor point, with bounds clamping and snap on  | 中 | ✅ AGENT-VERIFIED |
| G76 | Per-model opacity slider in the inspector, with live preview while dragging | 小 | ✅ AGENT-VERIFIED |
| G77 | Anchor marker: the per-model origin marker in the 3D scene and its show/hide checkbox | 小 | ✅ AGENT-VERIFIED |

## 后处理与监测（15 项：大 2 / 中 8 / 小 5）

| ID | 能力 | 体量 | 状态 |
|---|---|---|---|
| G28 | Field list built from server metadata: scalar/vector optgroups plus derived magnitude and per-component entries (Umag, Ux, Uy, Uz) | 中 | ✅ AGENT-VERIFIED |
| G32 | AI inference box is manipulable in the 3D view — grab the centre sphere to move it, grab a face plane to resize along that axis, with hover cursors | 大 | ✅ AGENT-VERIFIED |
| G33 | Patch-list selection model: shift/ctrl range and multi-select, row highlight, matching 3D patch highlight, and viewport-click-selects-the-row sync | 大 | ✅ AGENT-VERIFIED |
| G34 | Sensor probe spheres drawn in the 3D viewport and draggable to reposition a probe | 中 | ✅ AGENT-VERIFIED |
| G35 | Harvest sensor readings during a solve (poll the probe file, write the sensor JSON) and stream live probe values onto a chart | 中 | ✅ AGENT-VERIFIED |
| G36 | Double-click a streamline set in the list to reopen it in a floating edit dialog (centre, radius, seeds, tube radius, field, colormap) and regenerate  | 中 | ✅ AGENT-VERIFIED |
| G37 | Slice preview ring is hover-highlighted and draggable along the plane normal, writing back to the origin inputs | 中 | ✅ AGENT-VERIFIED |
| G38 | Double-click a slice in the list to reopen it in a floating edit dialog and re-cut in place | 中 | ✅ AGENT-VERIFIED |
| G39 | Patch-list filter bar: text filter with case-sensitive (Aa), whole-word (ab/) and regex (.*) toggles, on both the post-processing and mesh-preview lis | 中 | ✅ VERIFIED |
| G40 | Mesh preview: the render-order chip panel, the kind/model grouped patch tree, the filter bar and multi-select | 中 | ✅ AGENT-VERIFIED |
| G41 | 打开最近 sensor JSON | 小 | ✅ AGENT-VERIFIED |
| G42 | Enable post-processing: NaN-field warnings and a cancellable init | 小 | ✅ AGENT-VERIFIED |
| G43 | Streamline colouring by 'speed' (/U/), the legacy's default first option | 小 | ✅ VERIFIED |
| G44 | Volume rendering: the 64 / 128 / 256 resolution selector | 小 | ✅ VERIFIED |
| G45 | Confirming an AI inference switches the volume section on, points it at AI_T, sets its range and RENDERS it immediately | 小 | ✅ AGENT-VERIFIED |

## 液冷模式（13 项：大 1 / 中 8 / 小 4）

| ID | 能力 | 体量 | 状态 |
|---|---|---|---|
| G46 | CP-CU (cold plate / cooling unit) pair authoring on a server: add/remove pairs, cpu/gpu/custom preset, name, Q_W, R_th, dp_rated_kPa, Q_rated_LPM, C_s | 大 | ✅ VERIFIED |
| G47 | Contextual liquid creation for the selection: Create CRH (rear-door heat exchanger), Create Cold Plate, Create Heat Source | 中 | ✅ VERIFIED |
| G48 | Pipe segment inspector: name, loop-role colour, start/end waypoint readout, diameter, waypoint count, port connections, opacity, delete | 中 | ✅ VERIFIED |
| G49 | Rack detail editor: liquid manifold section (layout, mount face, pipe diameter, offset) when the rack is liquid-cooled | 中 | ✅ AGENT-VERIFIED |
| G50 | Feature-tree children for a pipe: control-point count, start/end port, blind ends, and each valve / T-junction fitting (clickable to select the fittin | 中 | ✅ AGENT-VERIFIED |
| G51 | Pipe drag-connect, waypoint editing, junction gizmo, valve placement and the pipe right-click context menu — the shared liquid modules' missing window | 中 | ✅ AGENT-VERIFIED |
| G52 | CP-CU monitoring list: one checkbox per cold plate, select-all/none, feeding monitor_cps on the solve request | 中 | ✅ VERIFIED |
| G53 | Liquid post-processing right panel: liquid solve-record selector with refresh, plus the 'pick a CP-CU or CDU in the model tree' hint | 中 | ✅ VERIFIED |
| G54 | Clicking a CDU or a cold-plate-bearing server in the model tree opens the liquid post-process chart window, with temperature / flow / pressure tabs | 中 | ✅ AGENT-VERIFIED |
| G55 | Liquid-mode creation toolbar drops every air-geometry tool (rack, ACU, wall, partition wall, baffle, floor) | 小 | ✅ VERIFIED |
| G56 | CDU inspector: the connected-pipes group listing supply and return pipes, each row clickable to select that pipe | 小 | ✅ AGENT-VERIFIED |
| G57 | CDU monitoring metric checkboxes during a liquid solve (return pressure, supply pressure, total flow) | 小 | ✅ VERIFIED |
| G58 | Liquid results panel: refresh-from-disk button, CDU overview block, and the per-server T_out column | 小 | ✅ AGENT-VERIFIED |

## 工程 I/O（13 项：大 1 / 中 7 / 小 5）

| ID | 能力 | 体量 | 状态 |
|---|---|---|---|
| G61 | STL Preview mode: a whole second display mode that renders exactly what the STL writer will emit, with a per-model/per-region tree (triangle counts, r | 大 | ✅ VERIFIED |
| G62 | Custom-mesh patch: execute the projection Bool Diff onto the imported solid, and un-apply it to bring the cutter body back | 中 | ✅ VERIFIED |
| G63 | Solid-region inspector for an imported STL region (region label, per-region projection patch buttons) | 中 | ✅ VERIFIED |
| G64 | Viewport selection of a face, a patch, a wall region or an imported region on a NON-rack model | 中 | ✅ VERIFIED |
| G65 | Import a custom STL as a new model | 中 | ✅ VERIFIED |
| G66 | 执行求解 re-exports the .cfdrun so the current solver settings reach the run | 中 | ✅ VERIFIED |
| G67 | Login overlay and automatic re-authentication when a request returns 401 / the session expires | 中 | ✅ AGENT-VERIFIED |
| G68 | Load a .cfdproj from the user's OWN machine (the load dialog's local-file tab) | 中 | ✅ VERIFIED |
| G69 | Export a single model to STL from its inspector | 小 | ✅ AGENT-VERIFIED |
| G70 | Face inspector: toggle the export-name prefix on/off (btn.add_prefix / btn.remove_prefix) | 小 | ✅ AGENT-VERIFIED |
| G71 | 保存工程 — Save As to a chosen remote path, and 'save to local file' download | 小 | ✅ VERIFIED |
| G72 | Server file browser: show-hidden-files toggle, create-new-folder, and name/date column sorting | 小 | ✅ VERIFIED |
| G73 | Edit the project name | 小 | ✅ VERIFIED |

## 2D 房间设计器（2 项：大 1 / 中 1 / 小 0）

| ID | 能力 | 体量 | 状态 |
|---|---|---|---|
| G59 | 2D room designer: models drawn on the floor plan and draggable there (rack/ACU/box), with a rotate ring and edge/vertex snapping between models | 大 | ✅ VERIFIED |
| G60 | 2D room designer: annotations / aisle labels, axes toggle, background colour, PNG canvas export, 90° view rotation, vertex-snap / edge-align / rotatio | 中 | ✅ VERIFIED |

---

## 逐项证据

### G01 — Live solver monitoring window during an air solve: floating draggable/resizable chart of residual convergence, per-server temperature and ACU return temperature, parsed from the streaming log

- **legacy 依据**：STL_generate.html:25103 window._smCreateMonitorWindow, :25354 _smDrawChart, :25587 _smParseLogLine, :25532 _smUpdateLegend; activated from runMeshGeneration :28235-28255 and runSolveOnly :28398-28418.
- **迁移前的状态**：`grep -rniE "_smParseLogLine|MonitorWindow|smCreateMonitor" src/frontend/react/src` → 0 hits. components/TimeSeriesChart.tsx exists but `grep -rn TimeSeriesChart src/frontend/react/src` shows only LiquidMonitorPanel.tsx:6/175/215 and LiquidResultsPanel.tsx:14/228 — never the air solve, which renders raw log text in a <pre> (SolverPanel.tsx:782-799). NUANCE the surveyor missed: components/SolverMonitorPanel.tsx (169 lines, mounted at App.tsx:139) IS ported — it is the 求解监控 CONFIG panel choosing which probes the .cfdrun asks for. So the data is requested; only the plotting window is gone. sm.tab_residual / sm.tab_temp / sm.tab_sensor_label remain unreferenced.

### G02 — Per-surface mesh seed configuration: anchor readout, direction X/Y/Z, offset, reset to default, and the seed-helper visibility toggle

- **legacy 依据**：STL_generate.html:7468-7485 buildSeedInspectorHtml (inspector.seed_helper at :7474), called from the patch inspector at :10827; window.toggleSeedHelperVisibility:28736, updateSeedSurfaceDirectionComponent:28746, updateSeedSurfaceOffset:28774, resetSeedSurfaceConfig:28796; storage helpers setStoredSeedConfigForSurface:5032 / clearStoredSeedConfigForSurface:5070.
- **迁移前的状态**：`grep -rniE "seed_helper|seedConfig|seedSurface" src/frontend/react/src` → 0 hits across the whole React source. label.seed_anchor, label.seed_direction_x/y/z, label.seed_offset, label.show_seed_helper, label.seed_status_default/custom, hint.seed_helper_desc, hint.seed_offset_min and btn.reset_seed_default are all unreferenced. Two surveyors reported this separately (geometry area and mesh area); merged.

### G03 — 执行网格生成 pushes the CURRENT geometry (freshly built .cfdrun manifest + grouped STL files) to the server

- **legacy 依据**：STL_generate.html:28054 window.runMeshGeneration → buildCfdrunJsonV2() at :28078, buildGroupedStlMap + base64 at :28186-28201, POST /api/run-mesh with {cfdrunJson, cfdrunFilename, stlFiles, meshOnly} at :28210-28219.
- **迁移前的状态**：Read session.ts:161-200 in full. runJob POSTs `{meshOnly, solveOnly}` only, and its own docstring at :163-170 states 'No cfdrunJson or stlFiles in the payload... this re-runs the LAST EXPORT — it does not push the geometry currently on screen.' `grep -rn "cfdrunJson|stlFiles" src/frontend/react/src` finds cfdrunJson ONLY in that comment. stlExport.ts:49 uploadStlFiles() exists but its only occurrence is its own definition — no caller. MeshPanel.tsx:504 surfaces this as t('hint.remesh_last_export').

### G04 — Pre-flight validation before a mesh run (digit-leading model names, floor Z vs room height, seed/geometry collisions, non-uniform door porosity, duplicate solid names) plus the step-by-step progress overlay

- **legacy 依据**：STL_generate.html:28090-28309 inside runMeshGeneration: _showDigitNameWarningDialog:27878, collectFloorHeightErrors:27911 / showFloorHeightErrorDialog:27927, collectSeedCollisionWarnings:27772 / showSeedCollisionWarningDialog:27959, door-porosity check :28275-28293, checkDuplicateSolidNames:28305, _createProgressOverlay:27804.
- **迁移前的状态**：session.ts runJob (:161-200) performs no checks. All 11 progress.* keys and all 9 dialog.* keys (floor_height_error_title/desc/item, seed_collision_title/desc/item) are unreferenced in React, as are error.digit_name_title, error.digit_name, status.floor_height_error, status.seed_collision_cancelled, btn.floor_height_error_ok, btn.seed_collision_back and btn.seed_collision_ignore. duplicateSolidNames() exists in stlExport.ts:71 but is called only from ExportDialog.tsx:57, never on the mesh path.

### G05 — Transient events for RACKS (power change, shutdown) and the one-click shutdown preset for every device kind

- **legacy 依据**：STL_generate.html:471-482 transient panel; window.openAddEventDialog:26533, device list :26537 covering 'acu'|'rack'|'box', _getEventTypesForKind:26508 with a shutdown preset per kind; _drawTransientTimeline:26412.
- **迁移前的状态**：Read transient.ts:31-48 directly: ACU_PARAMS and HEAT_PARAMS only, and paramsForBucket returns [] for everything else with the comment 'Racks vary per equipment item (power:<id>), which needs the equipment tree; until that is migrated a rack has nothing this panel can offer.' `grep -rniE "shutdown" src/frontend/react/src` → 0 hits; event.shutdown, event.airflow_change, event.temp_change, event.power_change, event.init_from_steady and error.no_devices_for_event are unreferenced. The list, inline add form, canvas timeline and per-event delete ARE ported (TransientPanel.tsx). Legacy also covers kind 'box', which React's heatGains bucket only partly overlaps.

### G06 — Refinement zone rename, per-zone visibility checkbox, and numeric MIN/MAX X/Y/Z bound entry

- **legacy 依据**：STL_generate.html:26997-27028 _rebuildRefinementZoneList; window.renameRefinementZone:27259, toggleRefinementZoneVisibility:27293, setRefinementZoneBound:27274.
- **迁移前的状态**：`grep -rniE "renameRefinementZone|toggleRefinementZoneVisibility|setRefinementZoneBound" src/frontend/react/src` → 0 hits. MeshPanel.tsx:353-389 renders a static name label, one level NumberField and a delete button. meshVisuals.ts:420 READS `zone.visible === false` and :637-638 writes zone.min/max, but only from a 3D drag. For contrast, the per-kind refinement LEVEL grid IS fully ported: meshConfig.ts:145-200 REFINEMENT_ROWS + MeshPanel.tsx:196-235, and it even adds a floor row the legacy lacks.

### G07 — Freeze the whole UI (panels, tree, tabs, toolbar, file bar) while a mesh/solve job runs

- **legacy 依据**：STL_generate.html:28287 window._setGlobalUIFrozen, definition :28288-28345, disabling mesh/solver/inspector panels, all six tab buttons, file bar, toolbar and tree, with _freezeSkipIds keeping the abort buttons live.
- **迁移前的状态**：`grep -rniE "Frozen|frozen|uiFrozen" src/frontend/react/src` → 4 hits, all prose comments (translateGizmo.ts:109 and rotateModelGizmo.ts:55 about camera controls, Viewport.tsx:970, MeshPanel.tsx:463). Only the run buttons disable (MeshPanel.tsx:513 `disabled={!validated || running}`).

### G08 — 检测网格状态 — surface the per-check checkMesh warning DETAIL, not just a count

- **legacy 依据**：STL_generate.html:397 #check-mesh-btn → window.checkMeshReady:25663, storing data.meshWarnings into uiState.session.meshWarnings at :25709.
- **迁移前的状态**：`grep -rniE "meshWarnings" src/frontend/react/src` → 0 hits, and all 10 mesh_warn.* keys (level_critical/warning/info, title_*, desc_*, btn_continue_risky/continue/cancel) are unreferenced. session.ts:203-250 runCheckMesh does perform the same two-step probe and stores checkMeshOk/failedChecks, and SolverPanel.tsx:722-742/:769-780 shows OK / n-warnings — only the detail and the log lines are dropped.

### G09 — Mesh-quality gate before 执行求解 — warn and require confirmation when checkMesh flagged severity ≤ 2

- **legacy 依据**：STL_generate.html:28357-28361 inside runSolveOnly: `if (cfg.meshWarnings.some(w => w.level <= 2)) { const proceed = await showMeshQualityWarningDialog(...); if (!proceed) return; }`; dialog at :28001.
- **迁移前的状态**：SolverPanel.tsx:686-693 run() calls session.runJob('solve') directly. `grep -rniE "MeshQuality|showMeshQuality|meshWarnings" src/frontend/react/src` → 0 hits; the mesh_warn.btn_continue_risky / btn_cancel keys that make up the dialog are unreferenced.

### G10 — Transient tab is shown only when solverType is transient, and switching away auto-returns to the solver tab

- **legacy 依据**：STL_generate.html:141 tab-btn-transient style="display:none"; window._toggleTransientParams:26179, lines 26186-26191 show/hide the tab and redirect the active tab back to 'solver'.
- **迁移前的状态**：App.tsx:68-75 LEFT_TABS always includes transient, and App.tsx:86-87 gates it on validation+mesh rather than on solverType. TransientPanel.tsx:51-59 compensates with an in-panel t('hint.transient_needs_solver_type') notice — React-specific mitigation, not the legacy behaviour.

### G26 — Model groups: create a named group from a multi-selection, rename in place, add to an existing group, remove a model from its group, dissolve, collapse

- **legacy 依据**：STL_generate.html:4521 createModelGroup, :4541 dissolveModelGroup, :4552 removeModelFromGroup, :4564 addModelsToGroup, :4580 renameModelGroup, :4589 selectGroupNode, :4605 toggleGroupCollapse; group tree node :12602-12603; multi-select UI :11027-11038; per-model Remove from Group :8953.
- **迁移前的状态**：`grep -rniE "modelGroup|createModelGroup|dissolveGroup|groupNode" src/frontend/react/src` → 1 hit, sceneBuilder.ts:26, a prose comment about `_mergeModelGroup` (an unrelated STL merge helper). FeatureTree.tsx renders categories → models → sub-nodes only; MultiSelectPanel.tsx has no group section.

### G11 — Add a circle / rectangle PATCH to a model face (ACU supply-return openings, floor tiles, wall and partition-wall leakage, baffle openings)

- **legacy 依据**：STL_generate.html:14508 window.addPatch; buttons :8402-8403 (wall/baffle/floor), :8982-8983 (partitionWall), :10579/:10586 (face inspector).
- **迁移前的状态**：`grep -rniE "add_circle_patch|add_rect_patch|addPatch" src/frontend/react/src` → 0 hits (empty output). Also unreferenced: btn.add_proj_circle_patch, btn.add_proj_rect_patch, btn.add_proj_circle_patch_region, equipment.add_patch, equipment.patch_type, equipment.shape_rect/circle, equipment.patch_width/height/radius. PatchInspector.tsx FaceInspector (:350-403) shows only a read-only patch count.

### G12 — Rack model inspector: rack size (W/D), height mode (total vs unit height), unit count, slot width ratio, top/bottom safe margins, default span — all re-running syncRackModelState

- **legacy 依据**：STL_generate.html:9274-9296 rack size + U-slot layout groups; window.updateRackModel:14028, updateRackSetting:14063, both calling syncRackModelState and reverting on out-of-bounds.
- **迁移前的状态**：`grep -rniE "unit_count|height_mode|slot_width_ratio|safe_margin|default_span|uslot" src/frontend/react/src` → 0 hits (empty output). inspector.rack_size, inspector.uslot_layout, inspector.uslot_settings, label.total_height_h, label.unit_height, label.auto_total_height, label.auto_unit_height all unreferenced. A rack falls to Inspector.tsx ModelProperties, whose size boxes go through projectStore.setGeometryComponent (:328-343) writing geometry.size directly — never calling rackState().syncRackModelState, which React DOES call elsewhere (rackSlots.ts:184/230/318, rackDetail.ts:177/187), so the seam exists and is simply not used here.

### G13 — Model library: pin a rack/ACU as a reusable template, then instantiate from it

- **legacy 依据**：STL_generate.html:13946 window.pinModelToLibrary, :13965 window.addInstanceFromTemplate; toolbar library section :12310-12325; inspector pin buttons :8754, :9300; js/model_library.js.
- **迁移前的状态**：`grep -rniE "pinModelToLibrary|addInstanceFromTemplate|model_library|modelLibrary|library\." src/frontend/react/src` → 0 hits (empty output). src/frontend/web/js/model_library.js is loaded by the legacy page but never imported anywhere under src/frontend/react/src.

### G14 — Delete a patch (plain and 'delete with cavity'), and auto-fill a patch across its face horizontally / vertically

- **legacy 依据**：STL_generate.html:14686 window.deletePatch, :14701 deletePatchWithCavity, :14634 window.autoFill; buttons :10843 (btn.delete_patch_cavity), :10845 (btn.delete_patch_destroy), :10733-10734 (btn.fill_horizontal / btn.fill_vertical).
- **迁移前的状态**：`grep -rniE "delete_patch|autoFill|fill_horizontal" src/frontend/react/src` → 0 hits (empty output).

### G15 — Wall-region inspector (a face split by patches): rename the region, set its opacity

- **legacy 依据**：STL_generate.html:10602 renderWallRegionInspector (inspector.wall_region_title at :10646); window.updateWallName:13605, previewWallOpacity:13581, updateWallOpacity:13592; window.selectWallRegionNode:12904.
- **迁移前的状态**：`grep -rniE "updateWallName|previewWallOpacity|wall_region_title" src/frontend/react/src` → 0 hits; inspector.wall_region, label.wall_region_zone and hint.wall_region_desc also unreferenced. treeNodes.ts:212-217 creates wallRegion nodes but Inspector.tsx:70-85 dispatches only patch/face/equipment/partition/serverFace/rackSlot, so 'wallRegion' falls through to ModelProperties. Credit where due: the remaining-area BC IS ported, onto the FACE panel (PatchInspector.tsx:406-459).

### G16 — Partition-wall model inspector: dedicated W/D/H section and per-patch leakage rate (%)

- **legacy 依据**：STL_generate.html:8965-8990 partitionWall branch of renderModelInspector; window.updatePartitionWallPatchPhysics:28724; leakage input :10821-10822 (label.leakage_rate) inside the block at :10811-10825.
- **迁移前的状态**：`grep -rniE "leakageRate|partition_wall_settings|resistanceEnabled" src/frontend/react/src` → hits only in unrelated prose (cfdproj.ts:6 and room2d.test.ts:21 name the fixture case Base_leakage; stlExport.ts:30-40 `includeLeakage` is the export toggle). hint.partition_wall_leakage, hint.partition_wall_role and inspector.partition_wall_cfd are unreferenced. components/PartitionInspector.tsx is the RACK partition inspector, not partitionWall. Two surveyors reported this; merged. The `resistanceEnabled` half of the second report is refuted separately.

### G17 — Rack servers quick-add panel: list the rack's servers, add one by start-U + span + kind, delete one

- **legacy 依据**：STL_generate.html:9310-9345 servers panel; window.rackServerPanelAdd:9360, addRackServerQuick:14151, deleteRackEquipment:14434.
- **迁移前的状态**：`grep -rniE "rack\.servers|addRackServerQuick" src/frontend/react/src` → 2 hits, LiquidResultsPanel.tsx:146 and liquidResults.ts:182, both iterating `rack.servers` on a SOLVED result object (a different shape). All 12 rack.servers.* keys plus btn.add_fan_server / btn.add_non_fan / btn.add_liquid_server are unreferenced. Creation exists only via viewport slot clicks (RackSlotInspector.tsx:133-165 → rackSlots.createEquipmentAtSlot).

### G18 — Patch projection vector: show/hide the direction arrow and reset it to the face default

- **legacy 依据**：STL_generate.html:14664 window.togglePatchVectorVisibility, :14674 window.resetPatchVector; UI :10741 (label.show_arrow), :10746 (btn.reset_proj_default).
- **迁移前的状态**：`grep -rniE "show_arrow|resetPatchVector|PatchVectorVis" src/frontend/react/src` → 0 hits. The companion keys label.vector_x/y/z and inspector.proj_vector are also unreferenced.

### G19 — Polygon floor: edit the bounding-box W/D of a non-rectangular floor (rescales the polygon)

- **legacy 依据**：STL_generate.html:8377-8383 polygon branch of renderWallBaffleInspector; window.updateFloorPolygonBBox:28916.
- **迁移前的状态**：`grep -rniE "bbox_w|FloorPolygonBBox|floor_shape" src/frontend/react/src` → 0 hits; label.bbox_d, label.polygon_vertices and hint.polygon_floor_edit also unreferenced. Inspector.tsx ModelProperties shows plain size X/Y/Z boxes writing geometry.size without touching geometry.polygon.

### G20 — Model-level 'Surface Orientation' section with a current-state badge and flip button

- **legacy 依据**：STL_generate.html:7455-7466 buildWallFloorOrientationInspectorHtml; window.togglePrimaryFaceSolidNormal:28947.
- **迁移前的状态**：`grep -rniE "surface_orientation|flip_surface|PrimaryFaceSolidNormal" src/frontend/react/src` → 0 hits; label.orientation_default, label.orientation_flipped, label.current_state, hint.surface_orientation_legend and hint.surface_orientation_desc also unreferenced. The flip itself IS reachable — PatchInspector.tsx:388-395 face-reverse-normal (key preview.reverse_normal) on the FACE node — so only the model-level section and its badge are absent.

### G21 — Keyboard: Delete/Backspace removes the selection; Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z undo-redo

- **legacy 依据**：STL_generate.html:29696-29723 document keydown handler.
- **迁移前的状态**：`grep -rniE "addEventListener\('keydown'|key === 'Delete'|'Backspace'|ctrlKey" src/frontend/react/src` → the only global keydown listener is FileBrowser.tsx:111 (Escape to close the dialog); every other hit is a per-input Enter-to-blur or FeatureTree.tsx:243 / Viewport.tsx:477 reading e.ctrlKey for multi-SELECT. Undo/redo exist as buttons only (LogPanel.tsx:87-91 → projectStore.ts:178/187) — and those buttons carry `title="Ctrl+Z"`, advertising a binding that does not exist.

### G22 — First-person walkthrough: the crosshair click-to-select hand-off

- **legacy 依据**：STL_generate.html:132 #fps-toggle-btn → fpsMode.toggle(); the hand-off is published at :29656-29665 (window.__three.edit = { handleViewportSelection, canSelectGeometry }).
- **迁移前的状态**：Read both sides. legacy/firstPerson.ts:62-68 declares `edit?: { handleViewportSelection; canSelectGeometry }` with the comment 'Optional click-to-select hand-off; first-person is inspect-only.' Viewport.tsx:559-565 calls installFirstPerson with exactly {camera, controls, renderer, scene, globalGroup} — no `edit` field. Walking itself works.

### G23 — Room temperature (ambient initial condition): preset select plus custom value

- **legacy 依据**：STL_generate.html:8270-8278 室温 group with a preset select (window._onRoomTempSelect:26006) and a custom number input (window.setRoomTemperature:25998), writing state.geometry.temperature.
- **迁移前的状态**：Read Inspector.tsx:172-183 directly — the same i18n group is rendered but the value is a `<span className="value">`, not an input. `grep -rniE "setRoomTemperature|_onRoomTempSelect|roomTemperature" src/frontend/react/src` → 0 hits, and inspector.room_temp_custom, room_temp_custom_input and room_temp_current are unreferenced.

### G24 — ACU cooling-model driving inputs for linearCapacity / tableCapacity: set point, rated cooling capacity, rated return temperature, coolant temperature

- **legacy 依据**：STL_generate.html:8653 _buildAcuCoolingModelHtml — window.updateAcuCoolingModel:28646 plus inputs at :8666 (setPointTemperature), :8667 (coolingCapacity), :8668 (ratedReturnTemp), :8669/:8672 (coolantTemperature); addAcuPerfTableRow:28655, updateAcuPerfTableCell:28680.
- **迁移前的状态**：`grep -rniE "setPointTemperature|coolingCapacity|ratedReturnTemp|coolantTemperature" src/frontend/react/src` → 0 hits across the whole source; label.rated_cooling, label.perf_table_return and label.perf_table_capacity are unreferenced. The mode select (AcuCoolingModel.tsx:79-86) and the tableCapacity return-temp/capacity table with add/remove (:90-135) ARE ported.

### G25 — Per-patch fixed surface temperature boundary condition (enable checkbox + surface temperature in °C) on wall, custom-STL and box patches

- **legacy 依据**：STL_generate.html:10829-10841 — the `if (model.kind === 'wall' || model.kind === 'custom' || model.kind === 'box')` branch of the patch inspector, rendering the enable checkbox and the temperature input, both calling window.updatePatchCfd(modelId, faceId, patchId, 'fixedTemperature', ...).
- **迁移前的状态**：Not on any surveyor's list — their patch claims covered creation, deletion, projection vector and bool diff, never this BC. Found via the i18n diff: inspector.heat_properties, label.enable_fixed_temp, label.surface_temp_c, label.surface_temp, label.delta_t_fixed and hint.fixed_value_t are all used in the legacy and unreferenced in React. `grep -rn fixedTemperature src/frontend/react/src` → 3 hits, all a DIFFERENT control: BoxCfdSection.tsx:41/106 sets a whole-box heat-source temperature and EquipmentInspector.tsx:195 reads a server face temperature. Read components/PatchInspector.tsx end to end — it renders the ACU role/flow/temp block (:175-222), baffle type (:246-252), floor opening type (:281-292) and the perforation block (:320-341), and has no heat-properties group for any model kind.

### G27 — Feature tree: drag a model node onto another to reorder it within its bucket

- **legacy 依据**：STL_generate.html:7901 tree node with draggable="true" ondragstart/ondragover/ondrop; window.beginModelTreeDrag:13057, handleModelTreeDragOver:13069, dropModelTree:13083, endModelTreeDrag:13102; window.reorderModel:1567.
- **迁移前的状态**：`grep -rniE "beginModelTreeDrag|dropModelTree|reorderModel|draggable" src/frontend/react/src` → the only `draggable` is PostprocessRightPanel.tsx:250 (postprocess layer chips); the rest are prose comments in translateGizmo.ts / meshVisuals.ts / PanelResizers.tsx. FeatureTree.tsx ModelNode has no draggable attribute and no drag handlers.

### G29 — Click a CATEGORY node to select every model of that kind at once

- **legacy 依据**：STL_generate.html:13167 window.selectCategory; category node onclick at :12679.
- **迁移前的状态**：`grep -rniE "selectCategory" src/frontend/react/src` → 0 hits (empty output). FeatureTree.tsx:156 category onClick calls only toggle(key), which collapses.

### G30 — Toolbar transform with NO selection falls through to the whole scene (rotate / mirror / reset the wrapperGroup)

- **legacy 依据**：STL_generate.html:822-845 toolbar buttons; window.globalTransform:16219, whole-scene fallback :16224-16233.
- **迁移前的状态**：Confirmed by reading both sides. STL_generate.html:16219-16234: globalTransform calls transformSelectedModel(action) and `if (...) return`; when that returns false the code below rotates/mirrors/resets `wrapperGroup` directly. React Toolbar.tsx renders rot-sel-x/y/z, rot-apply-*, mirror-x/y/z and reset-transform all `disabled={!hasSelection}` (:112, :121, :141, :159, :172), so the fallback branch is unreachable.

### G31 — 导出 CFD 工程包 as a single zip named after the project, with per-kind stlFile remapping to merged group files

- **legacy 依据**：STL_generate.html:154 → window.exportCFDProject:29161; name prompt :29173; stlFile remap :29197-29203; JSZip pack of `<proj>_cfd/` into `<proj>_cfd.zip` :29206-29215.
- **迁移前的状态**：ExportDialog.tsx:76-101 mode='package' builds the same manifest (buildManifestV2) and STL set but downloads them as separate staggered files; its own comment says 'A zip needs a library the React app does not carry.' `grep -rni "jszip|\.zip" src/frontend/react/src` → 0 hits, and error.jszip_not_loaded is an unreferenced key. The project-name prompt (dialog.enter_project_name_export) and the acu/wallObject/baffle/floor/wall/partitionWall stlFile remap are also absent.

### G74 — Drag a patch across its face, and resize a patch or a model by dragging its edge handles (with sticky face/edge snapping)

- **legacy 依据**：STL_generate.html:16266 beginPatchDrag (handle hit test :16271-16289, patch resize 'boxRectResize' :16317-16330, model base-edge resize :16600-16680 with snapFace/snapEdge), :16850 patch snap for BOX_PATCH_DRAG_KINDS.
- **迁移前的状态**：`grep -rniE "resizeHandle|boxRectResize|beginPatchDrag|patchDrag" src/frontend/react/src` → 0 hits. Viewport.tsx installs only the rotate gizmo (:295 rotateGizmoRef.begin) and the move gizmo (:989).

### G75 — Drag-to-place creation: press-and-drag the Wall / Partition Wall / Baffle button to drop the model at a floor point, with bounds clamping and snap on release

- **legacy 依据**：STL_generate.html:17931 window.beginDragCreate, updateDragCreate:17943-17997, endDragCreate:17999+; wired as onpointerdown at :12348-12350 and :12370-12372.
- **迁移前的状态**：`grep -rniE "beginDragCreate|dragCreate" src/frontend/react/src` → 0 hits. Every onPointerDown in React is unrelated (PanelResizers, Viewport rotate-ring, meshVisuals, Room2DEditor). CreationToolbar buttons carry only onClick.

### G76 — Per-model opacity slider in the inspector, with live preview while dragging

- **legacy 依据**：STL_generate.html:13484 previewModelOpacity, :13493 updateModelOpacity; slider at :8445 (wall/baffle/floor), :8538 (CDU), :8745 (ACU), :9013 (box/custom), :9270 (rack), all keyed label.model_opacity.
- **迁移前的状态**：`grep -rniE "model_opacity|updateModelOpacity|previewModelOpacity" src/frontend/react/src` → 1 hit, PostprocessRightPanel.tsx:141 `t('pp.model_opacity')` — the post-processing GLOBAL geometry fade, a different i18n key (pp.model_opacity vs the legacy's label.model_opacity) and a different control.

### G77 — Anchor marker: the per-model origin marker in the 3D scene and its show/hide checkbox

- **legacy 依据**：STL_generate.html:5351 createAnchorMarkerForModel, :16187 window.toggleAnchorMarkerVisibility; inspector checkbox :8333 (inspector.show_anchor).
- **迁移前的状态**：`grep -rniE "show_anchor|AnchorMarker" src/frontend/react/src` → 5 hits, all schema/factory declarations that merely persist the flag: cfdproj.ts:32, legacy/cfdprojModules.ts:38, legacy/factories.ts:197/245/270. Nothing reads it to build or toggle a marker. inspector.anchor_x/y/z, inspector.anchor_displacement, inspector.geometry_move and inspector.axis_forbid are also unreferenced.

### G28 — Field list built from server metadata: scalar/vector optgroups plus derived magnitude and per-component entries (Umag, Ux, Uy, Uz)

- **legacy 依据**：STL_generate.html:21244 window._ppPopulateFieldDropdown, magnitude at :21265, components :21268-21274.
- **迁移前的状态**：Verified all three layers. Server: backend/aircooling/runner/postprocess.py:1985 emits `"components": [f"{name}x", f"{name}y", f"{name}z"]` for vectors. Legacy: STL_generate.html:21244-21296 builds optgroups, emits `f.name+'mag'` and each component, then copies that markup into the slice, volume and streamline selects. React: PostprocessPanel.tsx:281 `const fields = (meta.fields ?? []).map((f) => f.name)` and :153 fieldOptions rendered flat at :713, :882, :1103, :1224. `grep -rniE "optgroup|scalar_fields|vector_fields" src/frontend/react/src` → 0 hits.

### G32 — AI inference box is manipulable in the 3D view — grab the centre sphere to move it, grab a face plane to resize along that axis, with hover cursors

- **legacy 依据**：STL_generate.html:20696 _ppAiHoverCheck (centreSphere + facePlanes raycast, cursors ew-/ns-/row-resize), :20744 beginPPAIBoxDrag.
- **迁移前的状态**：The box is drawn only: ppRenderer.ts:388 buildAiBox (userData.ppLayer='aiBox'), ppLayers.ts:204-205. `grep -n aiBox src/frontend/react/src/components/Viewport.tsx` → 0 hits, so it is never raycast. No centre sphere or face planes are built for it — the `facePlanes` hits in meshVisuals.ts:237/372/380/391/402/445 belong to the refinement-zone gizmo, a different object.

### G33 — Patch-list selection model: shift/ctrl range and multi-select, row highlight, matching 3D patch highlight, and viewport-click-selects-the-row sync

- **legacy 依据**：js/patch_selection.js createSelectionModel; wired for post-processing at STL_generate.html:21190-21215 (ppHighlightPatch:24701) and mesh preview at :24886-24940 (mpHighlightPatch, ensureViewportClickHandler:24950).
- **迁移前的状态**：`grep -rniE "createSelectionModel|patch_selection|ppHighlightPatch|mpHighlightPatch|row-selected" src/frontend/react/src` → 0 hits. src/frontend/web/js/patch_selection.js is loaded by the legacy page (STL_generate.html:1199) but never imported under src/frontend/react/src. PostprocessRightPanel.tsx:117-130 patchRow has only a checkbox onChange.

### G34 — Sensor probe spheres drawn in the 3D viewport and draggable to reposition a probe

- **legacy 依据**：STL_generate.html:23263 builds a sphere per instrumentation.probes entry; :23420-23478 _sensorSpheresGroup / _updateSensorSpheres; drag handlers :23479-23575; solver-tab visibility gate :23210.
- **迁移前的状态**：`grep -rniE "sensorSpheres|probes-latest|harvest-sensor" src/frontend/react/src` → 0 hits; `grep -rniE "sensor|probe" Viewport.tsx meshVisuals.ts sceneBuilder.ts` → 0 hits. meshVisuals.ts builds the locationInMesh seed and refinement-zone gizmos but nothing for probes. SensorPanel.tsx:80 still renders t('sensor.drag_hint') = '3D 视图中可直接拖拽彩色球调整位置'. Two surveyors reported this; merged.

### G35 — Harvest sensor readings during a solve (poll the probe file, write the sensor JSON) and stream live probe values onto a chart

- **legacy 依据**：STL_generate.html:23576-23638 window._smSensorPoller (start/stop/fetchNow, fetch('/api/probes-latest?since='+this._lastStep), pushes into _solverMonitor.sensors, calls _smDrawChart); started from runMeshGeneration :28249-28253 and runSolveOnly :28412-28416.
- **迁移前的状态**：`grep -rniE "harvest-sensor|smSensorPoller|probes-latest|sensorResults" src/frontend/react/src` → 0 hits. session.ts streamLog() does nothing sensor-related. SensorPanel.tsx:95 reads a `lastValueK` that nothing in React ever writes. Both server endpoints exist and are unused by React: server.py:1409 GET /api/probes-latest (registered :3790) and server.py:1503 POST /api/harvest-sensor-results (registered :3926).

### G36 — Double-click a streamline set in the list to reopen it in a floating edit dialog (centre, radius, seeds, tube radius, field, colormap) and regenerate in place

- **legacy 依据**：STL_generate.html:21977 lbl.addEventListener('dblclick', … window.ppEditStreamlineSet(s.id)); dialog :22050-22190.
- **迁移前的状态**：`grep -rniE "dblclick|onDoubleClick" src/frontend/react/src` → 3 hits, all unrelated: FileBrowser.tsx:166/180 (navigate/pick) and ColorbarOverlay.tsx:154. PostprocessPanel.tsx:500-535 setRow renders checkbox/label/opacity/delete only.

### G37 — Slice preview ring is hover-highlighted and draggable along the plane normal, writing back to the origin inputs

- **legacy 依据**：STL_generate.html:22768-22886 — pointermove hover recolour 0xffaa00 + cursor 'grab', pointerdown with controls.enabled=false, pointermove projecting onto the normal and writing pp-slice-ox/oy/oz.
- **迁移前的状态**：The ring is BUILT (ppRenderer.ts:306-329 buildSliceRing, userData.ppLayer='sliceRing'; ppLayers.ts:193) but never picked: `grep -n sliceRing src/frontend/react/src/components/Viewport.tsx` → :876 (visibility) and :909 (renderOrder) only. Viewport.tsx's onClick (:301-478) has no sliceRing branch and no pointer handler references it.

### G38 — Double-click a slice in the list to reopen it in a floating edit dialog and re-cut in place

- **legacy 依据**：STL_generate.html:23079 lbl.addEventListener('dblclick', … window.ppEditSlice(s.id)); dialog :23655.
- **迁移前的状态**：`grep -rniE "ppEditSlice|edit_slice" src/frontend/react/src` → 0 hits; the dblclick sweep above found no handler on PostprocessPanel.tsx:500-535 setRow.

### G39 — Patch-list filter bar: text filter with case-sensitive (Aa), whole-word (ab|) and regex (.*) toggles, on both the post-processing and mesh-preview lists, preserved across rebuilds

- **legacy 依据**：js/patch_filter.js:35 createFilterBar / buildPredicate / applyFilterToDOM; wired for post-processing at STL_generate.html:21178-21185 and mesh preview at :24878-24884.
- **迁移前的状态**：`grep -rniE "createFilterBar|patch_filter|filterBar|applyFilterToDOM" src/frontend/react/src` → 0 hits (empty output). src/frontend/web/js/patch_filter.js is loaded by the legacy page (STL_generate.html:1198) but never imported under src/frontend/react/src. PostprocessRightPanel.tsx:288-408 and MeshPreviewPanel.tsx:160-180 render unfiltered lists.

### G40 — Mesh preview: the render-order chip panel, the kind/model grouped patch tree, the filter bar and multi-select

- **legacy 依据**：STL_generate.html:1029 #mp-render-order-panel filled by _roBuildPanel('mp-render-order-panel','meshPreview') at :22460; mpBuildPatchList:24846 calling _buildGroupedPatchTree; mpLoadMesh:24338, mpClose:24511, mpSetOpacity:24532, mpSetGeoOpacity:24546, mpToggleAll:24561.
- **迁移前的状态**：`grep -rn "render-order" src/frontend/react/src` → PostprocessRightPanel.tsx:239/241 only (pp-render-order-panel); MeshPreviewPanel.tsx has no render-order markup, so the post-processing half was ported and the mesh half was not. `grep -rn groupPatchesByModelKind src/frontend/react/src` → ppPatchTree.ts:77 (a faithful, tested port) and PostprocessRightPanel.tsx:7/13/110 — again never MeshPreviewPanel.tsx, whose list at :160-180 is flat. Load/close/mesh-opacity/geo-opacity/select-all ARE ported with the same DOM ids. Two surveyors reported overlapping halves; merged.

### G41 — 打开最近 sensor JSON

- **legacy 依据**：STL_generate.html:1173-1175 #sensor-open-json-btn → window.openLatestSensorJson:23644, opening /api/read-file?path=<window._lastSensorJsonPath> (set from the solve log at :28542).
- **迁移前的状态**：Verified against the server, not just React. SensorPanel.tsx:36 fetches '/api/sensor/latest-json'; `grep -n sensor src/server/server.py` shows the only sensor routes are /api/probes-latest (:3790) and /api/harvest-sensor-results (:3926) — '/api/sensor/latest-json' does not exist, so the button always takes the error branch. App.tsx:269 shows /api/read-file IS available to React, so the legacy mechanism exists but is unused, and nothing records the path the solve wrote. Two surveyors reported this; merged.

### G42 — Enable post-processing: NaN-field warnings and a cancellable init

- **legacy 依据**：STL_generate.html:508 #btn-init-postprocess → window.ppInitPostProcess:21353; NaN warnings :21391-21397; cancellable showBlockingTaskDialog + AbortController :21363-21367.
- **迁移前的状态**：`grep -rniE "nan_warnings|nanWarning" src/frontend/react/src` → 0 hits, and InitResult (postprocess.ts:167-173) declares no nan_warnings field. `grep -rn AbortController src/frontend/react/src` → liquidSession.ts:58/144 only, never the pp path; PostprocessPanel.tsx:273-320 uses a busy flag with no cancel. The init itself IS ported (postprocess.ts:183 initRun).

### G43 — Streamline colouring by 'speed' (|U|), the legacy's default first option

- **legacy 依据**：STL_generate.html:21292 `slFieldSel.innerHTML = '<option value="speed">Speed (|U|)</option>' + sel.innerHTML`; consumed by ppGenerateStreamlines :21839 and labelled '|U|' at :22190.
- **迁移前的状态**：`grep -rniE "speed" src/frontend/react/src` returns no such string. PostprocessPanel.tsx:874-887 #pp-sl-field renders only the same flat fieldOptions as every other select.

### G44 — Volume rendering: the 64 / 128 / 256 resolution selector

- **legacy 依据**：STL_generate.html:681-687 radios input[name="pp-vol-res"] values 64/128/256, read by ppApplyVolume :20265-20269 and passed to _ppBuildVolumeMesh :20233.
- **迁移前的状态**：`grep -rniE "pp-vol-res|vol_res" src/frontend/react/src` → 0 hits. ppConfig.ts:172 hard-wires `resolution: 128`; ppVolume.ts:64 fetchVolume and :224/:258 (`maxSteps = min(max(resolution||128,64)*3, 768)`) accept the parameter but no control ever changes it. Everything else in the section is ported (PostprocessPanel.tsx:1202-1285).

### G45 — Confirming an AI inference switches the volume section on, points it at AI_T, sets its range and RENDERS it immediately

- **legacy 依据**：STL_generate.html:20671-20689 — sets #pp-vol-show, calls ppToggleVolumeConfig(true), sets #pp-vol-field='AI_T', writes ranges.AI_T into the clim boxes, then `await window.ppApplyVolume()`.
- **迁移前的状态**：AiInferencePanel.tsx:88-97 confirm() calls onFields(); PostprocessPanel.tsx:1299-1315 sets volume.show/visible/field/clim but never calls applyVolume() — which exists at :448 and is otherwise wired only to the Apply button at :1274. applyLayers (:224-232) then passes `volumeRef.current?.payload ?? null`, still null, so nothing renders.

### G46 — CP-CU (cold plate / cooling unit) pair authoring on a server: add/remove pairs, cpu/gpu/custom preset, name, Q_W, R_th, dp_rated_kPa, Q_rated_LPM, C_solid, V_fluid_mL

- **legacy 依据**：STL_generate.html:10175 renderEquipmentLiquidInspector, _addCpCuPair:10230, _removeCpCuPair:10250, _updateCpCuPair:10260, _applyCpCuPreset:10272, COLD_PLATE_PRESETS:10170.
- **迁移前的状态**：`grep -rniE "cpCuPairs|cp_cu|R_th|dp_rated|Q_rated|C_solid|V_fluid" src/frontend/react/src` → 1 hit, LiquidMonitorPanel.tsx:191 `t('lsm.cp_cu_monitor')`, a monitoring label rather than authoring. RackSlotInspector.tsx, ServerFaceInspector.tsx and EquipmentInspector.tsx carry no liquid section.

### G47 — Contextual liquid creation for the selection: Create CRH (rear-door heat exchanger), Create Cold Plate, Create Heat Source

- **legacy 依据**：STL_generate.html:12385-12392 contextual buttons; window.createCRHForRack:13701, createColdPlateForRack:13724 (→ createColdPlateModel:2890), createHeatSourceForServer:13738.
- **迁移前的状态**：`grep -rniE "createCRHForRack|createColdPlateForRack|createHeatSourceForServer|cold_plate|heat_source|crh" src/frontend/react/src` returns only read-only kind lists — legacy/cfdprojModules.ts:130 (a comment) and BoxCfdSection.tsx:30 `BOX_KINDS = new Set(['box','heat_source','heatSource','crh'])`. No creation path in CreationToolbar.tsx or Inspector.tsx.

### G48 — Pipe segment inspector: name, loop-role colour, start/end waypoint readout, diameter, waypoint count, port connections, opacity, delete

- **legacy 依据**：STL_generate.html:8546 renderPipeSegmentInspector; window.updatePipeDiameter:8590.
- **迁移前的状态**：`grep -rniE "updatePipeDiameter|pipe_diameter|diameterM" src/frontend/react/src` → hits only for `holeDiameterMm` (perforated-plate hole size: PatchInspector.tsx:315-341, RackDetailPanel.tsx:344, rackDetail.ts:150) — a different quantity. All 19 `pipe.*` i18n keys are unreferenced. Inspector.tsx dispatches 'cdu' (:119), 'rack' (:326), 'floor'/'wall' (:519-523); a pipe_segment falls to the generic box panel with W/D/H boxes, meaningless for geometry that is a waypoint list. Two surveyors reported this; merged.

### G49 — Rack detail editor: liquid manifold section (layout, mount face, pipe diameter, offset) when the rack is liquid-cooled

- **legacy 依据**：STL_generate.html:9057-9086 _lmHtml block (liquid.manifold_layout_title at :9062); window.setManifoldLayout:9393, setManifoldMountFace:9404, setManifoldParam:9415.
- **迁移前的状态**：`grep -rniE "manifold" src/frontend/react/src` → 4 hits, all unrelated: transformActions.ts:148/153 clears connectedManifoldPortId on a moved valve, legacy/stlWriter.test.ts:114-116 asserts an isLiquidManifold mesh is skipped at export. RackDetailPanel.tsx has no manifold section; all liquid.manifold_* keys unreferenced.

### G50 — Feature-tree children for a pipe: control-point count, start/end port, blind ends, and each valve / T-junction fitting (clickable to select the fitting)

- **legacy 依据**：STL_generate.html:8135-8163 pipe_segment / pipe_junction branch of renderModelNode; window._selectFittingById:12908.
- **迁移前的状态**：`grep -nE "pipe|waypoint|valve|blind" src/frontend/react/src/treeNodes.ts src/frontend/react/src/components/FeatureTree.tsx` → 0 hits. treeNodes.ts:258-262 dispatches rack / partition / floor-wall / box only.

### G51 — Pipe drag-connect, waypoint editing, junction gizmo, valve placement and the pipe right-click context menu — the shared liquid modules' missing window.* dependencies

- **legacy 依据**：js/liquid_mode.js:2525-2552 exposes the API; the bare globals it depends on are defined in STL_generate.html at :1554 (addModel), :1558 (removeModelByUid), :7142 (_getManifoldEndpoint); Connect-to-Manifold button at liquid_mode.js:1806.
- **迁移前的状态**：Read legacy/liquidScene.ts:93-190 in full: it shims state, globalGroup, scene, camera, renderer, controls, allModels, getModelById, _modelMap, requestRender, uiState, isLiquidMode, logOperation, pushUndoCheckpoint, updateUI and _liqCfg/_liqFluid/_liqSetFluid/_liqNetMain — and nothing else. Then confirmed the callers: liquid_mode.js:202 `window.addModel(pipe)`, :1190-1192 three more addModel calls, :1023 and :1186 `window.removeModelByUid(...)`, :699 and :1651 `window._getManifoldEndpoint(...)`. `grep -rn "addModel|removeModelByUid|_getManifoldEndpoint" src/frontend/react/src` finds no shim for any of the three. Note liquid_mode.js:699/1648 guard _getManifoldEndpoint with a truthiness check, so those paths fail SILENTLY; addModel/removeModelByUid are unguarded and throw.

### G52 — CP-CU monitoring list: one checkbox per cold plate, select-all/none, feeding monitor_cps on the solve request

- **legacy 依据**：STL_generate.html:1082-1096 #lsm-cp-list; js/liquid_solve_monitor.js:76 lsmLoadCPList, :102 lsmToggleAllCP, :107 lsmGetMonitoringConfig; js/liquid_cooling.js:174 window.lsmLoadCPList(exported) and :192 monitor_cps: monCfg.cpList.map(cp => cp.id).
- **迁移前的状态**：Traced the whole chain. LiquidSolverPanel.tsx:92 calls `liquid.runLiquidSolve({ device: cfg.solver.device })` with no monitorCps; liquidSession.ts:131 declares the optional field and :151 sends `monitor_cps: opts.monitorCps ?? []`, i.e. always empty. server.py:3408/3448 then passes monitor_cp_ids=None and backend/liquidcooling/runner.py:26 returns early on `if not monitor_cp_ids`. LiquidMonitorPanel.tsx:100-104 derives cpIds from frames that never arrive. `grep -rn lsmLoadCPList src/frontend/react/src` → 0 hits.

### G53 — Liquid post-processing right panel: liquid solve-record selector with refresh, plus the 'pick a CP-CU or CDU in the model tree' hint

- **legacy 依据**：STL_generate.html:1125-1141 #liquid-postprocess-right (#lsm-pp-run-select, #lsm-pp-hint, #lsm-pp-status), shown by the tab switcher at :19371-19373 which calls window._lsmRefreshPPRuns (js/liquid_solve_monitor.js:192) and _lsmOnPPRunChange (:203).
- **迁移前的状态**：`grep -rniE "lsm-pp-run-select|liquid-postprocess-right|lsm.pp_header|_lsmRefreshPPRuns" src/frontend/react/src` → 0 hits. App.tsx:470 shows LiquidResultsPanel on the left when coolingMode==='liquid', and App.tsx:566 unconditionally renders the AIR PostprocessRightPanel on the right — there is no liquid variant.

### G54 — Clicking a CDU or a cold-plate-bearing server in the model tree opens the liquid post-process chart window, with temperature / flow / pressure tabs

- **legacy 依据**：STL_generate.html:12854 window._lsmOnTreeClickPostProcess (called from the tree-node click at :12848) → js/liquid_solve_monitor.js:781 lsmShowPostProcessWindow → _lsmShowPPWindow:798 building temperature/flow/pressure datasets, _lsmCreatePostProcessWindow:899.
- **迁移前的状态**：`grep -rniE "lsmShowPostProcessWindow|_lsmOnTreeClickPostProcess|lsm-monitor-window" src/frontend/react/src` → 0 hits. LiquidResultsPanel accepts a focusEdgeIds prop (:31-36, used at :154) but App.tsx:470 renders `<LiquidResultsPanel />` with no props, so tree selection is never linked to the charts. Its ChipChart (:205-243) plots temperature series only (T_outlet at :218) — no flow or pressure.

### G55 — Liquid-mode creation toolbar drops every air-geometry tool (rack, ACU, wall, partition wall, baffle, floor)

- **legacy 依据**：STL_generate.html:12366-12373 — _renderLiquidToolbar emits the full air button set BEFORE the 液冷/liquid separator at :12374.
- **迁移前的状态**：Read CreationToolbar.tsx directly: LIQUID_TOOLS (:64-83) is [box, cdu, pipe_segment, pipe_junction]; AIR_TOOLS (:30-62) is gated on mode==='air' at :92. Two surveyors reported this independently; merged.

### G56 — CDU inspector: the connected-pipes group listing supply and return pipes, each row clickable to select that pipe

- **legacy 依据**：STL_generate.html:8499 renderCduModelInspector; _buildCduConnectedPipesHtml:8457 building the supply/return lists with selectModelInScene on each row.
- **迁移前的状态**：`grep -rniE "cdu_connected_pipes|connectedPipes|supplyPipes|returnPipes" src/frontend/react/src` → 0 hits, and `grep -n "pipe|Pipe" src/frontend/react/src/components/CduInspector.tsx` → 0 hits. Name/size/supply temp/supply pressure/total flow/served racks/delete ARE ported (CduInspector.tsx:36-141).

### G57 — CDU monitoring metric checkboxes during a liquid solve (return pressure, supply pressure, total flow)

- **legacy 依据**：STL_generate.html:1108-1122 .lsm-cdu-cb with data-metric return_P / supply_P / total_flow, collected as cduMetrics by js/liquid_solve_monitor.js:118-122.
- **迁移前的状态**：`grep -rniE "lsm-cdu-cb|cduMetrics|cdu_return_pressure|cdu_supply_pressure" src/frontend/react/src` → 0 hits. The `total_flow` hits (liquidResults.ts:34/96/181, LiquidResultsPanel.tsx:85/130) are RESULT fields, not monitoring toggles. LiquidMonitorPanel.tsx has only the loss and CP groups.

### G58 — Liquid results panel: refresh-from-disk button, CDU overview block, and the per-server T_out column

- **legacy 依据**：STL_generate.html:776-783 #liquid-results-group plus the 🔄 Refresh button at :782; js/liquid_results_panel.js:26 buildResultsPanel — CDU overview :46-57, system totals :68-75, server table with a T_out column :95.
- **迁移前的状态**：`grep -rn refresh_liquid_results src/frontend/react/src` → 0 hits (btn.refresh_liquid_results is unreferenced). `grep -n cdu src/frontend/react/src/components/LiquidResultsPanel.tsx` → a doc comment at :20 only. Read the table header at :137-142: Server / Flow / T_chip / Q — no T_out column. T_outlet does appear at :218, but that is a series in the per-cold-plate ChipChart, not the server table. Status, totals, per-rack sections and the chart ARE ported.

### G61 — STL Preview mode: a whole second display mode that renders exactly what the STL writer will emit, with a per-model/per-region tree (triangle counts, region colours), per-model and per-region visibility checkboxes, per-region reverse-normal, export-filename readout, solid-region/triangle summary and a regenerate button

- **legacy 依据**：STL_generate.html:11419 renderStlPreviewRootInspector, :11442 renderStlPreviewModelInspector, :11466 renderStlPreviewRegionInspector, :11498 getStlPreviewTreeHtmlForModel, :11517 updateStlPreviewUI; window.refreshStlPreview:11653, window.toggleStlPreviewMode:11731; mode predicate isStlPreviewMode:3792 branched on at :5229, :6030, :7490, :7853, :12619, :17743, :17764, :18162.
- **迁移前的状态**：None of the three surveyors mentioned this area. Found by diffing the SHARED i18n dictionary (React imports web/i18n/*.json directly, so key usage is comparable): 22 of the 23 `preview.*` keys are used in STL_generate.html and referenced nowhere under src/frontend/react/src — root_desc, summary, model_count, visible_count, actions, model_title, model_desc, export_info, model_type, export_filename, show_model, region_title, region_desc, region_info, parent_model, show_region, tree_header, mode_badge, prop_title_model, toggle_model_title, toggle_region_title, tree_original_regions — together with btn.stl_preview, btn.back_to_modeling, btn.refresh_preview, btn.regenerate_preview, btn.view_parent_model, error.stl_preview_failed, error.no_models_for_preview and error.view_switch_failed. `grep -rniE "stlPreview|previewMode|regenerate_preview|back_to_modeling|triangleCount" src/frontend/react/src` → 5 hits, all inert: sceneBuilder.ts:100-101/123-124 merely initialise `previewRegionVisibility: {}` and `previewRegionReverse: {}` so the flags round-trip through save, and picking.ts:36 gives isPreviewRegion a picking priority for a node type nothing ever creates. CAVEAT, stated honestly: the legacy's own entry button is gone — refreshStlPreviewToggleButton (STL_generate.html:3883) looks up `document.getElementById('toggle-preview-btn')` and `grep -n 'toggle-preview-btn' STL_generate.html` finds only that lookup, no element. So today the mode is reachable only via window.toggleStlPreviewMode() from the console or from its own in-mode buttons. Same class of half-dead feature as the surveyors' importCustomSTL item; a parity gap only if the entry point is restored.

### G62 — Custom-mesh patch: execute the projection Bool Diff onto the imported solid, and un-apply it to bring the cutter body back

- **legacy 依据**：STL_generate.html:14606 window.applyCustomPatchBoolDiff, :14625 window.unapplyCustomPatch; buttons :10715-10716 (btn.reshow_beam / btn.execute_bool_diff_op).
- **迁移前的状态**：`grep -rniE "applyCustomPatchBoolDiff|execute_bool_diff" src/frontend/react/src` → 0 hits. Also unreferenced: inspector.patch_3d_body, inspector.bool_diff_section, label.cylinder_radius, label.plate_width, label.beam_length, label.center_x/y/z, label.orient_rx/ry/rz — the whole 3D cutter-body section. React DOES port the SEPARATE model-vs-model bool diff (boolDiff.ts, MultiSelectPanel.tsx:3, Inspector.tsx:16), which is a different feature.

### G63 — Solid-region inspector for an imported STL region (region label, per-region projection patch buttons)

- **legacy 依据**：STL_generate.html:11404 renderRegionInspector, :11413 addPatchToRegion buttons; window.selectRegion:13202.
- **迁移前的状态**：`grep -rniE "original_solid_region|region_patch_ops" src/frontend/react/src` → 0 hits; inspector.region_info, inspector.mount_region and hint.region_patch_desc are also unreferenced. treeNodes.ts:265-278 DOES create the region nodes (select type 'region'), but Inspector.tsx:68-72 documents in its own comment that a region selection falls back to the model panel.

### G64 — Viewport selection of a face, a patch, a wall region or an imported region on a NON-rack model

- **legacy 依据**：STL_generate.html:17803-17805 refIds → setSelection('patch'), :17843 isImportedRegion → setSelection('region'), :17851-17856 wallIndex → setSelection('wallRegion'), :17890/:17893 setSelection('face').
- **迁移前的状态**：Read Viewport.tsx onClick (:301-478) end to end. It branches on isRackSlot (:358), isRackEquipment (:415) and the rack face→slot resolve (:440), then falls to `onSelectRef.current(uid, e.ctrlKey||e.metaKey)` at :477, which selects the whole MODEL. `data.isPatch` / `data.patchId` are destructured at :349-357 but read ONLY inside the two rack-equipment branches. No branch produces a 'face', 'patch', 'wallRegion' or 'region' selection for a wall, floor, baffle or ACU.

### G65 — Import a custom STL as a new model

- **legacy 依据**：STL_generate.html:15349 window.importCustomSTL; error.stl_import_failed and inspector.import_hint exist in the dictionary but nothing renders them in React.
- **迁移前的状态**：`grep -rniE "importCustomSTL|file-stl" src/frontend/react/src` → 0 hits, and `grep -rn 'type="file"' src/frontend/react/src` → 0 hits (React has NO file input at all). React can render an already-stored custom mesh (stlAssets.ts:18/80/106-113) but cannot ingest a new one. CAVEAT the surveyor was right to flag: this is dead in the legacy too — window.importCustomSTL reads document.getElementById('file-stl') and the only file input on the page is #file-proj (:119). Parity item only if the legacy entry point is restored.

### G66 — 执行求解 re-exports the .cfdrun so the current solver settings reach the run

- **legacy 依据**：STL_generate.html:28348 window.runSolveOnly — comment at :28364 'Export .cfdrun manifest (for updated solver settings like endTime, gravity)', buildCfdrunJsonV2() at :28372, POST with {cfdrunJson, cfdrunFilename, stlFiles:{}, solveOnly:true} at :28385-28393.
- **迁移前的状态**：SolverPanel.tsx:686-693 → session.ts runJob('solve') POSTs only {meshOnly:false, solveOnly:true}; no manifest is built. `grep -rn buildManifestV2 src/frontend/react/src` → legacy/cfdrunBuild.ts:94 (definition) and ExportDialog.tsx:87 (only caller).

### G67 — Login overlay and automatic re-authentication when a request returns 401 / the session expires

- **legacy 依据**：STL_generate.html:1229 _showAuthOverlay (form POSTing /api/auth/login at :1251), invoked from the patched fetch on session expiry (:1284), on first use (:4359) and after logout (:29962).
- **迁移前的状态**：`grep -rniE "auth/login|AuthOverlay|401" src/frontend/react/src` → 0 hits for a login flow; all 6 auth.* keys (required, enter_credentials, signing_in, login_failed, network_error, sign_in) plus error.not_authenticated are unreferenced. No fetch wrapper intercepts 401 anywhere. React does call /api/auth/check read-only (SystemPanel.tsx:33) to display the username, which is not a sign-in path.

### G68 — Load a .cfdproj from the user's OWN machine (the load dialog's local-file tab)

- **legacy 依据**：STL_generate.html:119 `<input type="file" id="file-proj" accept=".cfdproj,.json" onchange="loadProject(event)">`; window.saveProject:14941 → openProjectFileDialog, whose remote/local tab bar is at :15193-15195 and whose local read path is that #file-proj input.
- **迁移前的状态**：The surveyors covered the SAVE side (Save As / local download) but not the LOAD side. All 9 loadproj.* keys are used in the legacy and unreferenced in React — title, tab_remote, tab_local, local_desc, local_btn, load_btn, server_unavailable, no_file_selected, read_failed — plus error.project_load_failed. `grep -rn 'type="file"' src/frontend/react/src` → 0 hits: the React app has no file input of any kind. App.tsx:264-286 load() is server-side only, fetching /api/read-file with an absolute path chosen through the server-side FileBrowser.

### G69 — Export a single model to STL from its inspector

- **legacy 依据**：STL_generate.html:29226 window.exportSingleModel; buttons :8442, :9010 (btn.export_current_model), :9267 (btn.export_current_rack), :8741 (btn.export_current_acu).
- **迁移前的状态**：`grep -rniE "exportSingleModel|export_current" src/frontend/react/src` → 0 hits (empty output). label.single_export and label.export_filename are also unreferenced.

### G70 — Face inspector: toggle the export-name prefix on/off (btn.add_prefix / btn.remove_prefix)

- **legacy 依据**：STL_generate.html:10416 renderFaceInspector; window.toggleFacePrefix:13597; buttons :10516-10521.
- **迁移前的状态**：`grep -rniE "toggleFacePrefix|add_prefix|prefix_status" src/frontend/react/src` → 0 hits; label.prefix_enabled / label.prefix_disabled / inspector.face_naming_prefix also unreferenced. The rest of the face inspector IS ported — PatchInspector.tsx:350-403 has face display name, face opacity and face-reverse-normal.

### G71 — 保存工程 — Save As to a chosen remote path, and 'save to local file' download

- **legacy 依据**：STL_generate.html:129 → window.saveProject:14941 → openProjectFileDialog('save') with a remote path picker (window._lpConfirmSave:15289) AND a local download tab (_lpConfirmLocalSave:15312, _lpUpdateLocalName:15272, #lp-tab remote/local :15193-15195).
- **迁移前的状态**：Read App.tsx:289-315: save() POSTs /api/write-file to store.getPath() with overwrite:true and `if (!current || !path) return` — its own comment says 'A new path would need the dialog.' `grep -rniE "saveAs|_lpConfirmLocalSave|lp-tab" src/frontend/react/src` → 0 hits, and all 11 saveproj.* keys are unreferenced.

### G72 — Server file browser: show-hidden-files toggle, create-new-folder, and name/date column sorting

- **legacy 依据**：STL_generate.html:18838 sort indicator, :18864 date column header (window._fbSort), :18909 show-hidden checkbox, :18926 new-folder button → window._fbNewFolder:19076; the same three controls repeat in the project dialog at :15093, :15151, :15206.
- **迁移前的状态**：React HAS a FileBrowser (components/FileBrowser.tsx), which is presumably why nobody checked it — but it is a fresh implementation with its own keys, and 19 of the 21 legacy `filebrowser.*` keys are unreferenced: title, desc, go_up, show_hidden, selected, selected_placeholder, new_folder, cancel, confirm, loading, load_failed, no_selection, new_folder_prompt, create_failed, error_loading, col_name, col_date, sort_asc, sort_desc. `grep -n "t('" src/frontend/react/src/components/FileBrowser.tsx` shows it renders only common.cancel, filebrowser.parent, filebrowser.go, common.loading, filebrowser.empty and filebrowser.pick_a_file — no hidden-file toggle, no new-folder button, no sortable columns.

### G73 — Edit the project name

- **legacy 依据**：STL_generate.html:8293 — `<input type="text" value="${state.meta.projectName}" onchange="setProjectName(this.value)" placeholder="${t('inspector.project_name_placeholder')}">`.
- **迁移前的状态**：Not on any list. Found while confirming the room-temperature claim: Inspector.tsx:186-190 renders t('inspector.project_name_title') / t('inspector.project_name_label') and then `<span className="value">{project.name ?? '—'}</span>` — read-only, exactly like the room-temperature group two blocks above it. `grep -rn "setProjectName|project_name_placeholder" src/frontend/react/src` → 0 hits.

### G59 — 2D room designer: models drawn on the floor plan and draggable there (rack/ACU/box), with a rotate ring and edge/vertex snapping between models

- **legacy 依据**：room_2d_editor.js:2046 _DRAGGABLE_KINDS iteration drawing models from geometry.location/size, :1668-1725 _dragModel with vertex-proximity and edge-alignment snapping, :1599-1605 _rotateHandle drag, :1767-1780 rotate-ring hover.
- **迁移前的状态**：`grep -nE "allModels|_DRAGGABLE_KINDS" src/frontend/react/src/components/Room2DEditor.tsx` → 0 hits. The React canvas draws the footprint polygon, grid and vertex handles only (:120-210); its onPointerDown (:230) handles vertices/edges, never models.

### G60 — 2D room designer: annotations / aisle labels, axes toggle, background colour, PNG canvas export, 90° view rotation, vertex-snap / edge-align / rotation-snap toggles, grid-badge dbl-click edit, box-select and delete of vertices/edges, and the room-settings readout (vertex count, perimeter, area, edge angle)

- **legacy 依据**：room_2d_editor.js:2067 setShowAxes, :2072 setAnnotations, :2077 setBgColor, :2082 exportCanvasPNG, :2097 setViewRotation, :2113 toggleVertexSnap, :2119 toggleEdgeAlign, :2124 toggleRotationSnap, :2158 editGridBadge, :1734-1760 box select, :1899-1920 keyboard edge-break/vertex delete, :1966-2001 room-settings readout; toolbar buttons STL_generate.html:881-899.
- **迁移前的状态**：`grep -rniE "setAnnotations|exportCanvasPNG|setViewRotation|toggleVertexSnap|toggleEdgeAlign|rotateView90" src/frontend/react/src` → 0 hits; room2d.room_settings, room2d.polygon_info, room2d.vertices_count, room2d.perimeter, room2d.area, room2d.edge_angle, label.vertex_snap, label.edge_align and btn.rotate_90 are all unreferenced. Room2DEditor.tsx exposes only gridSnap (:323-327) and zoomToFit (:344). I ADDED the room-settings readout to this item — the surveyor missed it; polygonArea exists in room2d.ts:50 but nothing renders it.
