# Liquid Cooling Dedicated Mode — Design Spec

**Date**: 2026-04-10
**Status**: Approved
**Scope**: Major UI/architecture refactor — liquid cooling becomes a separate mode with its own 3D workflow, air cooling mode is cleaned up

**Predecessor**: Phases 1–8 of `2026-04-09-liquid-cooling-integration-design.md` (data models + solver + coupling + UI widgets)

---

## 1. Motivation

The previous architecture embedded liquid cooling UI throughout the air cooling mode (solver tab, postprocess tab, rack inspector, CDU model type). This created cognitive load:

- Users building a pure air cooling project saw liquid cooling widgets they didn't need
- Liquid cooling settings were scattered across tabs, not discoverable
- CDU models and cold plates were parked alongside boxes and baffles with no clear grouping
- The "Run Coupled Simulation" flow was unclear — when did liquid cooling affect air CFD?

The new architecture introduces a **dedicated liquid cooling mode** with its own 3D workflow, cleanly separated from air cooling. Liquid cooling results flow into air CFD only when the user explicitly opts in.

---

## 2. Design Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | State sharing model | **A** — fully shared `state.models`, liquid objects as new kinds |
| 2 | Mode switch button location | **A** — in-place replace STL Preview button |
| 3 | Right-side panel layout | **A** — top/bottom split (toolbar + inspector), shared between modes |
| 4 | Contextual creation UX | **C** — toolbar buttons + property panel buttons + right-click menu |
| 5 | Pipe creation | **B+ii** — port drag-connect + oriented junction components with rotation gizmo |
| 6 | Result inheritance | **B+III** — residual heat power, separate prop-group in solver tab |

---

## 3. Architecture Overview

### 3.1 Core Concept

Introduce a global display mode `state.displayMode` with values `'air'` or `'liquid'`. Both modes share the same `state.models` array, but **rendering, interactions, toolbar content, and property panel content** change based on the current mode.

### 3.2 Key Principles

- **STL Preview mode is deprecated** — code stays commented out for reference, button is removed
- `state.displayMode` replaces `isStlPreviewMode()`; introduce `isAirMode()` / `isLiquidMode()` helpers
- Liquid cooling data moves out of nested properties (`model.rdhx`, `equipment.liquidCooling.coldPlates`) into **first-class model kinds**
- New kinds: `crh`, `cold_plate`, `heat_source`, `pipe_segment`, `pipe_junction` (`cdu` already exists from Phase 3)
- **Single source of truth**: one `.cfdproj` file contains both air and liquid models; mode switch is view-only, no save/load

### 3.3 Mode Switch Behavior

Clicking the "💧 液冷模拟 / Liquid Simulation" button:

1. `pushUndoCheckpoint('switchToLiquidMode')`
2. `state.displayMode = 'liquid'`
3. 3D viewport stays unchanged (same camera, same meshes)
4. Left panel rebuilds with 3 liquid tabs (see §4.2)
5. Right-side creation toolbar rebuilds with liquid buttons (see §4.3)
6. Air cooling models (Rack, ACU, Wall, etc.) become **read-only** — selectable for inspection, but geometry transforms are disabled
7. Liquid cooling models (CDU, cold plates, pipes) become **editable**
8. Button label changes to "← 返回风冷模拟 / Back to Air Mode"
9. `logOperation('switchToLiquidMode', ...)`

Returning to air mode reverses the process. **All edits made in liquid mode are preserved** — nothing is lost on mode switch.

### 3.4 Visibility Rules

| Mode | Air Models (Rack, ACU, Wall, Baffle, Floor, Box) | Liquid Models (CDU, CRH, Cold Plate, Heat Source, Pipe) |
|------|--------------------------------------------------|---------------------------------------------------------|
| Air | Visible + editable | Hidden by default (toggle available) |
| Liquid | Visible + read-only | Visible + editable |

Liquid models **do not participate in STL export** — they are visualization-only; the backend solver handles liquid hydraulics separately via the Phase 1 module.

---

## 4. UI Structure

### 4.1 File Bar (top row, always visible)

**Before**: `[👁 STL PREVIEW] [💾 保存工程] [🏠 房间2D设计] [📂 加载工程]`

**After** (air mode): `[💧 液冷模拟 / Liquid Simulation] [💾 保存工程] [🏠 房间2D设计] [📂 加载工程]`

**After** (liquid mode): `[← 返回风冷模拟 / Back to Air Mode] [💾 保存工程] [🏠 房间2D设计] [📂 加载工程]`

### 4.2 Left Panel Tab Bar

**Air mode** (unchanged from current): `[🗂️ 几何与属性] [⚙️ 网格] [🔬 求解] [📊 后处理] [ADMIN]`

**Liquid mode** (simplified): `[🗂️ 几何与属性] [🔬 液冷求解] [📊 液冷后处理]`

The reduced set reflects that liquid cooling has no mesh step (it's a 1D network) and no admin-level settings.

### 4.3 Right Panel (shared structure, mode-specific content)

Right panel is vertically split into **two zones**:

- **Top** (~200px fixed): **Creation toolbar** — mode-specific buttons
- **Bottom** (remaining space): **Property inspector** — shows details for the currently selected model

**Air mode creation toolbar** (migrated from current left panel):
```
┌─ ➕ 创建几何 / Create Geometry ─┐
│ 📦 新建Box                      │
│ 🗄️  新建Rack                    │
│ ❄️  新建ACU                     │
│ 🧱 新建Wall                     │
│ 📄 新建Baffle                   │
│ 🟫 新建Floor                    │
└──────────────────────────────────┘
```

**Liquid mode creation toolbar**:
```
┌─ 💧 液冷工具栏 / Liquid Tools ──┐
│ 🏭 新建CDU                       │  ← global
│ 🔗 新建管道 (拖拽模式)           │  ← global
│ ────────────────────────────── │
│ 上下文相关 / Contextual:         │
│ 🌊 为选中Rack建CRH               │  ← disabled until Rack selected
│ ❄️ 为选中Rack建冷板              │  ← disabled until Rack selected
│ 🔥 为选中Server建热源            │  ← disabled until Server selected
└──────────────────────────────────┘
```

### 4.4 Liquid Mode Geometry Tree

In liquid mode, the geometry tree (left panel's "几何与属性" tab) shows a **liquid-specific tree** instead of the air cooling tree:

```
📦 液冷系统 / Liquid System
├── 🏭 CDUs
│   ├── CDU_1 [🔌 服务: Rack_1, Rack_2]
│   └── CDU_2
├── 🗂️ Rack附件 / Rack Attachments
│   ├── Rack_1
│   │   ├── 🌊 CRH_1
│   │   └── 📦 服务器冷板 / Server Cold Plates
│   │       ├── Server_1 → ❄️ CPU冷板 + 🔥 CPU热源
│   │       └── Server_2 → ❄️ GPU冷板 + 🔥 GPU热源
│   └── Rack_2
│       └── ...
└── 🔗 管道网络 / Pipe Network
    ├── Pipe_1 (CDU_1 → Rack_1)
    └── Pipe_2 (CDU_1 → Rack_2)
```

Air models (Rack_1, Rack_2) are visible as branches only to show which racks have liquid cooling attachments. They are not editable from this tree.

---

## 5. Liquid Model Kinds

### 5.1 Summary Table

| Kind | Creation | Parent | Key Properties | 3D Appearance |
|------|----------|--------|---------------|---------------|
| `cdu` | Toolbar → drag into viewport | none (global) | supplyTemperature, totalFlowRate, servedRacks | Blue-teal box (existing from Phase 3) |
| `crh` | Context menu on Rack | Rack | effectiveness, waterFlowRate, waterInletTemp | Semi-transparent blue panel on rack back face |
| `cold_plate` | Context menu on Rack or Server | Rack/Server | spec, chipTDP, inlet/outlet ports, thermalModel | Metallic small block inside Rack or on Server |
| `heat_source` | Context menu on Server | Server | power (W), area, thermalMass | Red glow sphere inside Server |
| `pipe_segment` | Toolbar "new pipe" → drag between ports | none (spans multiple models) | startPort, endPort, waypoints, color (supply/return), junctions[] | Blue/red TubeGeometry (existing from Phase 4) |
| `pipe_junction` | Right-click on pipe waypoint → "升级为T型分支" | pipe_segment | quaternion, branchPipeId | Colored small cube with rotation gizmo |

### 5.2 Data Structure Examples

**CRH** (replaces existing `model.rdhx`):
```javascript
{
  id: 5001,
  kind: 'crh',
  name: 'CRH_Rack_1',
  enabled: true,
  parentRackId: 42,
  crh: {
    effectiveness: 0.5,
    waterFlowRate: 20,
    waterInletTemp: 25,
    heatRejectionCapacity: 30000
  }
}
```

**Cold Plate** (replaces nested `eq.liquidCooling.coldPlates`):
```javascript
{
  id: 5002,
  kind: 'cold_plate',
  name: 'CPU_CP_Rack_1_Server_3',
  enabled: true,
  parentRackId: 42,
  parentServerId: 103,
  anchor: { x, y, z },
  coldPlate: {
    specName: 'cp_default',
    chipTDP: 350,
    thermalModel: { chipArea: 0.004, chipThickness: 0.001, thermalConductivity: 148 },
    inletPortId: 'cp_5002_in',
    outletPortId: 'cp_5002_out'
  }
}
```

**Heat Source**:
```javascript
{
  id: 5003,
  kind: 'heat_source',
  name: 'CPU_Heat_Rack_1_Server_3',
  enabled: true,
  parentRackId: 42,
  parentServerId: 103,
  anchor: { x, y, z },
  heatSource: {
    power: 350,
    chipArea: 0.004,
    thermalMass: 60
  }
}
```

**Pipe Segment** (replaces existing `state.liquidCooling.piping.chains`):
```javascript
{
  id: 5004,
  kind: 'pipe_segment',
  name: 'Pipe_CDU1_to_Rack1_supply',
  enabled: true,
  pipe: {
    spec: 'pipe_25mm',
    startPortId: 'cdu_1_supply',
    endPortId: 'rack_1_manifold_in',
    waypoints: [
      { x: 1.0, y: 2.0, z: 1.5 },
      { x: 3.0, y: 2.0, z: 1.5 },
      { x: 3.0, y: 4.0, z: 1.5 }
    ],
    color: 'supply',  // 'supply' | 'return'
    junctions: [
      {
        afterWaypointIndex: 1,
        type: 'T_branch',
        quaternion: { x: 0, y: 0, z: 0, w: 1 },
        branchPipeId: 5005
      }
    ]
  }
}
```

**Pipe Junction** (standalone, referenced from pipe_segment):
```javascript
{
  id: 5005,
  kind: 'pipe_junction',
  name: 'T_branch_1',
  enabled: true,
  parentPipeId: 5004,
  anchor: { x: 3.0, y: 2.0, z: 1.5 },
  junction: {
    type: 'T_branch',
    quaternion: { x: 0, y: 0, z: 0, w: 1 },
    connectedPipes: [5004, 5006, 5007]
  }
}
```

### 5.3 Data Migration

When loading a `.cfdproj` file saved under the old (Phase 1–8) schema, automatically convert:

- Each `rack.rdhx` (if `enabled`) → standalone `crh` model with `parentRackId = rack.id`
- Each `equipment.liquidCooling.coldPlates[i]` → standalone `cold_plate` model + corresponding `heat_source` model
- Each chain in `state.liquidCooling.piping.chains` → standalone `pipe_segment` model

**Preserved** (no migration needed):
- `state.liquidCooling.fluid` — stays as global config
- `state.liquidCooling.specifications` — stays as global spec library
- `state.liquidCooling.cdus` references — already handled by existing `cdu` kind

**Migration runs once on load**. After successful migration, the project is marked with `state.schemaVersion = 2` (from implicit v1). On subsequent loads, migration is skipped if `schemaVersion >= 2`.

---

## 6. Interaction Details

### 6.1 Mode Switch

Clicking "💧 液冷模拟":
1. `pushUndoCheckpoint('switchToLiquidMode')`
2. `state.displayMode = 'liquid'`
3. Left panel tab bar swaps (air → liquid tab set)
4. Right panel creation toolbar swaps (air → liquid buttons)
5. 3D viewport visibility:
   - Air models: shown, but their materials dim slightly (e.g. 60% opacity) to indicate read-only state
   - Liquid models: shown at full opacity
6. Transform gizmos disabled for air models
7. Button text: "← 返回风冷模拟 / Back to Air Mode"
8. `logOperation('switchToLiquidMode', ...)`

Reverse on clicking "← Back to Air Mode".

### 6.2 CDU Creation (toolbar drag)

1. User clicks toolbar "🏭 新建CDU"
2. Button highlights, cursor becomes crosshair
3. User clicks (or drags to) a location in the 3D viewport
4. `createCduModel()` (existing Phase 3 function) is called at that position
5. New CDU auto-selected, inspector shows its properties
6. Toolbar button returns to normal state

### 6.3 Contextual Creation (Rack → CRH, etc.)

When user selects a Rack model in liquid mode, three entry points become available to create a CRH:

**Toolbar** (upper right panel):
```
┌─ 💧 液冷工具栏 ────────────┐
│ 🏭 新建CDU                  │  [Enabled]
│ 🔗 新建管道                 │  [Enabled]
│ ──────────────────────────│
│ 选中: Rack_1                │
│ 🌊 为Rack_1建CRH            │  [Enabled — was disabled]
│ ❄️ 为Rack_1建冷板           │  [Enabled — was disabled]
└─────────────────────────────┘
```

**Property inspector** (lower right panel):
```
┌─ Rack_1 (液冷模式) ────────┐
│ 液冷配置:                   │
│   CRH: ❌ 未配置             │
│   [➕ 添加CRH]               │
│                             │
│   冷板: 2个                 │
│   ├── CPU_CP on Server_1    │
│   └── GPU_CP on Server_2    │
│   [➕ 添加冷板]              │
└─────────────────────────────┘
```

**Right-click context menu** (on Rack in 3D viewport):
```
┌──────────────────────────┐
│ 为此Rack建CRH            │
│ 为此Rack建冷板           │
│ ─────────────────────── │
│ 删除                     │
└──────────────────────────┘
```

All three entry points call the same underlying function `createCRHForRack(rackId)`. Same pattern for cold plates and heat sources.

### 6.4 Pipe Drag-Connect

1. User clicks toolbar "🔗 新建管道"
2. Toolbar button highlights; application enters "pipe creation mode"
3. All liquid models (CDU, cold plates, CRHs) display their port indicators:
   - Blue dots for inlet ports (water in)
   - Red dots for outlet ports (water out)
4. User clicks and holds on a port (e.g., `cdu_1_supply`)
5. Mouse movement shows a dashed rubber-band line from the source port to the cursor
6. When cursor hovers over another port, it highlights (snap assistance)
7. User releases on the target port (e.g., `rack_1_manifold_in`)
8. System auto-generates an L-shaped path (horizontal → vertical → horizontal)
9. Creates a `pipe_segment` model with `color: 'supply'` (auto-detected from port type)
10. Pipe renders as a blue TubeGeometry (reuses existing Phase 4 pipe renderer)

**Validation**: reject connections where source type equals target type (e.g., outlet-to-outlet).

### 6.5 Adding T-Branch Junctions

1. Right-click on a waypoint of an existing `pipe_segment`
2. Context menu: "升级为T型分支 / Upgrade to T-Branch"
3. A `pipe_junction` model is created at that waypoint
4. Original pipe is split in two at the junction
5. Junction appears as a small colored cube
6. TransformControls attached to the junction with rotation-only mode
7. User can rotate around Z axis (most common) or XY axes
8. Default branch pipe stub appears from the junction's third port
9. User can drag-connect the stub to another port to complete the branch

### 6.6 Read-only Air Models in Liquid Mode

When liquid mode is active:
- Air models remain selectable (click to highlight)
- Selecting an air model shows its liquid-relevant properties in the inspector (e.g., "This rack has CRH + 2 cold plates")
- **Geometry edit controls are hidden**: no W/D/H input boxes, no anchor drag, no transform gizmo
- An "⚠️ 切换到风冷模式编辑几何 / Switch to air mode to edit geometry" hint is shown at the top of the inspector

---

## 7. Result Transfer (Air CFD Inheritance)

### 7.1 Air Solver Tab — New Prop Group

In the air solver tab, add a new prop-group titled "🔥 液冷结果继承 / Liquid Cooling Inheritance":

```
┌─ 🔥 液冷结果继承 / Liquid Cooling Inheritance ─┐
│                                                  │
│ ☐ 继承液冷模拟结果作为边界条件                    │
│   Inherit liquid cooling results as BCs          │
│                                                  │
│ (when checked, expands to:)                      │
│ ┌────────────────────────────────────────────┐ │
│ │ 最新液冷求解 / Latest: 2026-04-10 19:32    │ │
│ │ 服务器数 / Servers: 4                       │ │
│ │ 最高芯片温度 / Max T_chip: 68.5°C           │ │
│ │                                              │ │
│ │ [🔄 刷新液冷结果 / Refresh]                  │ │
│ │                                              │ │
│ │ 继承模式 / Inheritance Mode:                 │ │
│ │  ◉ 残余热量 / Residual Power (推荐)          │ │
│ │  ○ 芯片温度 / Chip Temperature               │ │
│ │  ○ 混合 / Both                               │ │
│ └────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

**Visibility rules**:
- Only shown if `case_dir/model/liquid_results.json` exists
- Otherwise shows placeholder: "请先在液冷模式中运行求解 / Please run solve in liquid mode first"

**Default state**: checkbox is **unchecked** (backward compatible with pure air cooling).

### 7.2 Phase 2 Backend Modification

The current Phase 2 behavior is: **automatically use `liquid_results.json` if it exists**. This needs to change to **explicit opt-in**.

Modification to `stlgen_runner._build_and_save_bridge_bc()`:

```python
liquid_results_path = self.model_dir / "liquid_results.json"
inherit_liquid = self._data.get("inheritLiquidResults", False)  # NEW

liquid_server_results = {}
if inherit_liquid and liquid_results_path.exists():  # CHANGED: added inherit_liquid check
    try:
        from dcdtuni.liquid.results import LiquidResult
        lr = LiquidResult.load(liquid_results_path)
        liquid_server_results = {sid: srv for sid, srv in lr.servers.items()}
        logger.info(f"Inheriting liquid cooling results for {len(liquid_server_results)} servers")
    except Exception as e:
        logger.warning(f"Failed to load liquid results: {e}")
```

The frontend writes `inheritLiquidResults: true` into `cfd_export.json` when the checkbox is checked.

### 7.3 Data Flow

```
Liquid mode → User clicks "Run Liquid Solve"
  ↓
POST /api/liquid/solve
  ↓
liquid_runner.run_liquid_solve()
  ↓
writes case_dir/model/liquid_results.json

---

Air mode → User checks "Inherit Liquid Results"
  ↓
User clicks "Run CFD"
  ↓
frontend writes cfd_export.json with inheritLiquidResults: true
  ↓
POST /api/run-mesh
  ↓
stlgen_runner._build_and_save_bridge_bc()
  ↓
  if inherit_liquid and liquid_results.json exists:
      for each server in liquid_results:
          P = residual_heat_to_air_W  (instead of server.power)
          T_outlet calculated from reduced P
  ↓
OpenFOAM with adjusted heat sources
```

---

## 8. Implementation Phases

Each phase is independently testable and deployable. Each phase leaves the application in a working state.

### Phase A: displayMode Infrastructure (low risk, pure plumbing)

- Add `state.displayMode` with values `'air'` | `'liquid'`
- Add `isAirMode()`, `isLiquidMode()` helpers
- Keep existing `isStlPreviewMode()` (deprecated but not removed)
- File bar button: STL Preview → "💧 液冷模拟" (wires to mode switch, no visual change yet)
- Button label swaps based on mode
- Mode switch triggers undo checkpoint + log operation
- **Acceptance**: clicking button toggles `state.displayMode`, button text updates, no other UI changes

### Phase B: Right Panel Split + Air Toolbar Migration

- Restructure right panel HTML: top `#creation-toolbar` (~200px) + bottom `#prop-content`
- Migrate air creation buttons (Box/Rack/ACU/Wall/Baffle/Floor) from current left panel location to right panel top
- `state.displayMode`-aware: toolbar shows air or liquid buttons
- Liquid toolbar initially contains only existing "新建CDU" + placeholder buttons
- **Acceptance**: air mode works as before but creation buttons are in the new location; liquid mode shows liquid toolbar placeholder

### Phase C: Liquid Left Tab Bar + Geometry Tree

- Liquid mode left panel shows 3-tab bar (几何/求解/后处理)
- Implement liquid geometry tree structure (CDUs → Rack attachments → Pipes)
- Visibility: liquid models hidden in air mode (default), air models read-only (dimmed) in liquid mode
- Mode switch preserves 3D viewport state
- **Acceptance**: switching between modes shows correct left panel content, viewport stays consistent

### Phase D: New Liquid Model Kinds + Data Migration

- Implement `crh`, `cold_plate`, `heat_source`, `pipe_segment`, `pipe_junction` as first-class model kinds
- Factory functions for each kind following existing `createCduModel` pattern
- Contextual creation: Rack → CRH/cold plate, Server → heat source
- Data migration on project load: old `model.rdhx` → `crh` model, `eq.liquidCooling.coldPlates` → `cold_plate` models
- `buildCfdExportJson()` adapted: new model structure exports to **existing backend JSON schema** (so Phase 1 solver unchanged)
- **Acceptance**: old projects load correctly with migrated models; new liquid models create/edit/delete correctly

### Phase E: Pipe Drag-Connect + T-Branch Junctions

- "Pipe creation mode" activated from toolbar button
- Display port indicators on all liquid models
- Drag-connect from port to port creates `pipe_segment`
- Right-click on waypoint → upgrade to T-branch → creates `pipe_junction`
- `pipe_junction` uses existing TransformControls (rotation-only mode)
- Junction rotation updates waypoint metadata and re-renders pipe path
- **Acceptance**: can drag pipes between CDU ports and Rack manifold ports; can insert T-branch and rotate its orientation

### Phase F: Result Inheritance + Air Mode Cleanup

- Air solver tab: new "🔥 液冷结果继承" prop-group (visible only if liquid_results.json exists)
- Backend: `stlgen_runner` modified to check `inheritLiquidResults` flag (default false, backward compatible)
- **Cleanup air mode UI**:
  - Remove liquid cooling section from solver tab (moved entirely to liquid mode)
  - Remove CDU creation from air mode (CDU only creatable in liquid mode)
  - Remove RDHx section from Rack inspector in air mode (replaced by `crh` model)
  - Remove liquid cooling from server equipment inspector in air mode
- Post-processing tab: liquid results panel **stays** (shared between modes)
- **Acceptance**: air mode UI is clean (only inheritance checkbox remains); liquid mode contains all liquid functionality; checking inheritance checkbox causes CFD to use residual heat

### Phase Dependencies

```
Phase A (infrastructure)
  ↓
Phase B (right panel split)
  ↓
Phase C (liquid tabs + tree)
  ↓
Phase D (new model kinds + migration)
  ↓
Phase E (pipe drag-connect)
  ↓
Phase F (inheritance + cleanup)
```

Strict sequential dependency — each phase builds on the previous.

---

## 9. File Impact Summary

### Frontend (`src/frontend/web/`)

| File | Change Type | Details |
|------|------------|---------|
| `STL_generate.html` | Large modification | File bar button, left tab bar logic, right panel restructure, new tree, new model kinds, migrations, pipe context menu, air solver inheritance panel |
| `js/liquid_cooling.js` | Modify | Add liquid-specific model kind factories |
| `js/liquid_pipe_renderer.js` | Modify | Support `pipe_segment` model kind (replace chain-based rendering) |
| `js/liquid_topology_editor.js` | Mostly unchanged | Still handles Equipment Level View for intra-server cold plate wiring |
| `js/liquid_results_panel.js` | Mostly unchanged | Used by both modes |
| `js/liquid_mode.js` | **New file** | Mode switch logic, contextual creation, pipe drag-connect state machine |

### Backend (`src/backend/` + `src/server/`)

| File | Change Type | Details |
|------|------------|---------|
| `src/backend/aircooling/runner/stlgen_runner.py` | Modify | Add `inheritLiquidResults` flag check |
| `src/backend/liquidcooling/*` | Unchanged | Solver and schema unchanged — backend is already agnostic to frontend data layout |
| `src/server/server.py` | Minor modify | API already supports the new flag via cfd_export.json |

### Documentation

| File | Change Type |
|------|------------|
| This spec | New |
| `AGENTS/specs/2026-04-09-liquid-cooling-integration-design.md` | Mark as **superseded by this spec for UI architecture** (data models and solver still valid) |

---

## 10. Acceptance Criteria (Whole System)

After all 6 phases are complete:

1. Starting a new project shows only air cooling UI — no liquid cooling widgets visible
2. Clicking "💧 液冷模拟" switches to liquid mode with dedicated 3D workflow
3. In liquid mode, user can: create CDU by drag, create CRH/cold plates via contextual menus, drag-connect pipes between ports, insert and rotate T-branches
4. In liquid mode, air models (Rack, ACU) are visible but read-only
5. Returning to air mode preserves all liquid edits
6. Running liquid solve in liquid mode produces results; returning to air mode shows "Inherit Liquid Results" option in solver tab
7. Checking the inheritance checkbox and running CFD uses residual heat (not full server power) for liquid-cooled servers
8. Loading an old project (Phase 1–8 schema) migrates `model.rdhx` → `crh` models, nested cold plates → independent `cold_plate` + `heat_source` models
9. Saving a project in either mode produces a single `.cfdproj` file containing both air and liquid data
10. Backend solver tests (55 total from Phases 1–8) all continue to pass

---

## 11. Open Questions (Deferred)

These are intentionally deferred and not part of this spec:

- **RDHx model visualization**: Phase 8 drew a semi-transparent plane on rack back face. The new `crh` model should do the same, but whether it's a separate visual element or inherent to the Rack rendering is TBD in implementation.
- **Pipe junction rotation axes**: Initially restricted to Z-axis only (most common for horizontal pipe layouts). Full 3-axis rotation is a future enhancement.
- **Multi-pipe junctions**: Only T-branch (3-way) is in scope. 4-way and 5-way junctions are future work.
- **Direct CPU temperature inheritance**: The "T_chip" mode in the inheritance panel is shown as an option but **only "residual power" mode is implemented** in Phase F. T_chip mode is a future enhancement.
