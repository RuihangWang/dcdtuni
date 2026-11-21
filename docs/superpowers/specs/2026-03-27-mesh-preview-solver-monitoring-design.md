# Mesh Preview Enhancement & Solver Monitoring System — Design Spec

## Goal

Three features for the STLGenerator web app:

1. **A — Mesh Preview Enhancement**: Upgrade the existing mesh preview patch list to match post-processing style (collapsible boundary/internal groups, Read Zones, slave filtering)
2. **B1 — Residual Monitoring**: Parse OpenFOAM solver log in real-time via SSE, display residual convergence curves in a floating chart window
3. **B2 — Server Temperature Monitoring**: Add OpenFOAM `surfaceFieldValue` function objects to output per-server-patch average temperatures, parse from log, display in real-time chart

## Architecture

- **Frontend**: All in `STLGeneratorForOpenFoam/web/STL_generate.html` (monolithic HTML+JS)
- **Backend**: `STLGeneratorForOpenFoam/server/server.py` (Python HTTP server)
- **CFD Engine**: `dctwin/dctwin/runner/case.py` (CaseRunner) + Jinja2 templates
- **Chart rendering**: Canvas 2D API (no external chart library — project avoids build dependencies)

---

## Feature A: Mesh Preview Enhancement

### Current State

The mesh preview already works:
- `mpLoadMesh()` calls POST `/api/mesh-preview/load` → GET `/api/mesh-preview/binary`
- Renders surface_with_edges (grey `MeshBasicMaterial` + dark `EdgesGeometry`)
- Right panel `mesh-preview-right` shows flat patch list with visibility checkboxes
- `defaultVisible` is false for patches ending in `_slave` (set server-side in `MeshPreviewLoader`)

### Changes Required

1. **Rename button**: "🔲 预览网格" → "🔲 显示网格"

2. **Hierarchical patch tree**: Replace the flat `mpBuildPatchList` with a tree structure matching post-processing:
   - **boundary** (collapsible group): all boundary patches, checkboxes
     - Patches ending in `_slave` → unchecked by default
     - All other boundary patches → checked by default
   - **internalMesh** entry: unchecked by default, not applicable for mesh preview (skip if no internal mesh data)

3. **Read Zones checkbox**: Add a "Read Zones" toggle that calls the existing backend to reload with zones. However, the current `MeshPreviewLoader` reads boundary patches only. For zones, it would need to call `CachedPostProcessor` which requires a solved case. For mesh preview (pre-solve), zones are not available. **Decision: include the Read Zones checkbox but disable it with tooltip "需要先完成求解" for mesh preview mode.** This keeps UI consistency without misleading the user.

4. **Select all / Deselect all**: Already exist. Keep as-is.

5. **Opacity slider**: Already exists in mesh preview right panel. Keep as-is.

### No Backend Changes Needed

The server-side `MeshPreviewLoader` already provides `defaultVisible` flags. The frontend restructuring is sufficient.

---

## Feature B: Solver Monitoring System

### B.1: Solver Tab Right Panel

When the solver tab is active, the right panel shows a **monitoring configuration panel**:

```
┌─────────────────────────────┐
│ 🔬 求解监控                 │
├─────────────────────────────┤
│ ☑ 监控残差收敛曲线          │
├─────────────────────────────┤
│ 📊 服务器温度监控            │
│ ┌─ 全选 / 全不选 ─────────┐│
│ │ ☐ A00 (inlet)           ││
│ │ ☐ A00 (outlet)          ││
│ │ ☐ A01a (inlet)          ││
│ │ ☐ A01a (outlet)         ││
│ │ ...                     ││
│ └─────────────────────────┘│
└─────────────────────────────┘
```

**Server patch list population**:
- When solver tab is selected, call a new API endpoint `GET /api/server-patches` that:
  1. Reads `Mesh/constant/polyMesh/boundary`
  2. Extracts patches matching `*_inlet_*` or `*_outlet_*` patterns (using same regex as `fields.py`)
  3. Groups by server/equipment ID
  4. Returns JSON: `[{id: "A00", inlet: "A00_PTN3900_inlet_patch", outlet: "A00_PTN3900_outlet_patch"}, ...]`
- If no mesh exists, show "请先生成网格" message

**State storage**: Selected monitoring options stored in `state.dctwinConfig.monitoring`:
```javascript
{
  residuals: true,           // default true
  serverTemperature: [],     // list of {id, patchName, type: "inlet"|"outlet"}
}
```

### B.2: Floating Chart Window

A shared floating window for residual and temperature charts. Created when solve starts (if any monitoring is enabled).

**Window structure**:
```
┌─[▼]──── 求解监控 ────────[×]─┐
│ ┌─────────────────────────────┐│
│ │   [残差] [温度]  tab buttons ││
│ ├─────────────────────────────┤│
│ │                             ││
│ │   Canvas 2D line chart      ││
│ │   (auto-scaling, legend)    ││
│ │                             ││
│ └─────────────────────────────┘│
└────────────────────────────────┘
```

**Properties**:
- **Position**: Fixed, initially top-right of viewport (offset from toolbar)
- **Size**: 520×360px default
- **Draggable**: Via title bar (mousedown on header)
- **Collapsible**: `[▼]` button top-left toggles body visibility (header stays visible)
- **Closeable**: `[×]` button top-right removes from DOM
- **Tabs**: "残差" and "温度" (温度 tab only if server temperature monitoring is enabled)
- **z-index**: 1050 (above colorbar dialog at 1100? No, below. Use 900 to stay below colorbar.)

**Canvas 2D chart**:
- X axis: iteration number (time step)
- Y axis residual tab: log10 scale (1e0 to 1e-8 typical range), auto-range
- Y axis temperature tab: linear scale in Celsius (auto-range)
- Series: one per field (Ux, Uy, Uz, T, p_rgh, k, epsilon for residuals; per-patch for temperature)
- Colors: predefined palette (7 colors for residuals, rotating for temperature)
- Legend: small text below chart, clickable to toggle series visibility
- Update: append new data point per time step, auto-scroll X axis (show last 200 steps, scrollable)
- Grid lines: light dashed horizontal + vertical every N steps
- Anti-aliased lines via `ctx.lineWidth = 1.5`

### B.3: Residual Parsing

**Input**: SSE log lines from `/api/log-stream?job_id=xxx` (already streamed to terminal)

**Parsing strategy** — intercept lines in `_streamDctwinLog`'s `es.onmessage`:

1. **Time step marker**: `Time = (\d+)` → set `currentStep`
2. **Residual line**: `Solving for (\w+), Initial residual = ([\d.e+-]+), Final residual = ([\d.e+-]+)` → record `{step, field, initial, final}`
   - For fields with multiple solves per step (p_rgh), keep only the **first** initial residual per step (the one after the Time marker)
3. **Continuity**: `time step continuity errors : sum local = ([\d.e+-]+)` → record for continuity plot (optional)

**Data structure**:
```javascript
window._solverMonitor = {
  active: false,
  residuals: {}, // {fieldName: [{step, initial, final}, ...]}
  temperatures: {}, // {patchName: [{step, value}, ...]}
  currentStep: 0,
  seenFieldsThisStep: {}, // track first occurrence of each field per step
};
```

**Chart update**: After parsing each time step's data (on next `Time = N` or `ExecutionTime` line), redraw the canvas.

### B.4: Server Temperature Monitoring

#### Backend: Jinja2 Template Changes

**File**: `dctwin/dctwin/third_parties/foam/template/system/steady/controlDict.j2`

Add after the existing probes block:

```jinja2
{% for fo in surface_field_values %}
    {{ fo.name }}
    {
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        enabled         true;
        writeControl    timeStep;
        writeInterval   1;
        log             true;
        writeFields     false;
        regionType      patch;
        name            {{ fo.patch }};
        operation       areaAverage;
        fields          (T);
    }
{% endfor %}
```

**Template variable**: `surface_field_values` — list of `{name: "avgT_A00_inlet", patch: "A00_PTN3900_inlet_patch"}`

#### Backend: CaseRunner Changes

**File**: `dctwin/dctwin/runner/case.py`

In `_generate_control_dict()`:
- Accept `monitor_patches` parameter (list of patch names to monitor)
- Build `surface_field_values` list from patch names
- Pass to template render

In `run_solve_only()`:
- Read `monitoring` config from simulation config (passed from frontend via cfd_export.json)
- Pass to `_generate_control_dict()`

#### Backend: server.py Changes

1. **New endpoint** `GET /api/server-patches`:
   - Read `Mesh/constant/polyMesh/boundary` file
   - Parse patch names
   - Filter for server inlet/outlet patterns
   - Return grouped list

2. **Modified solve flow**:
   - `cfd_export.json` includes `monitoring.serverTemperature` list
   - Passed through to CaseRunner

#### Frontend: Temperature Log Parsing

**OpenFOAM surfaceFieldValue log output format** (two lines):
```
avgT_A00_inlet output:
    areaAverage(T) = 298.15
```

**Parsing state machine**:
- State `idle`: waiting for `(\w+) output:$` line → if matched, store FO name, transition to `expect_value`
- State `expect_value`: waiting for `areaAverage\(T\) = ([\d.e+-]+)` → record value, transition to `idle`

**Temperature conversion**: Raw value is Kelvin. Convert to Celsius for display: `T_C = T_K - 273.15`

---

## Right Panel Tab Switching Logic

Update `_applyLeftTabUI()`:
- **geometry tab**: Show property inspector (existing)
- **mesh tab**: Show property inspector. If mesh preview active, show mesh-preview-right
- **solver tab**: Show solver-monitoring-right panel (NEW)
- **postprocess tab**: If post-processing active, show dctwin-postprocess-right

The solver monitoring right panel (`solver-monitoring-right`) is a new `<div>` in the right panel HTML, similar to `dctwin-postprocess-right` and `mesh-preview-right`.

---

## Data Flow Summary

### Residual Monitoring Flow
```
User checks "监控残差" → clicks "执行求解"
→ POST /api/run-solve (cfd_export.json)
→ CaseRunner runs solver
→ SSE /api/log-stream streams log lines
→ Frontend parses: Time = N, Solving for X, Initial/Final residual
→ Canvas 2D chart updates in real-time
```

### Server Temperature Monitoring Flow
```
User switches to solver tab
→ GET /api/server-patches → populate right panel checkboxes
→ User checks servers to monitor → clicks "执行求解"
→ cfd_export.json includes monitoring.serverTemperature
→ CaseRunner generates controlDict with surfaceFieldValue FOs
→ Solver outputs FO results to log
→ SSE streams log lines including "avgT_xxx output: areaAverage(T) = Y"
→ Frontend parses with state machine
→ Canvas 2D temperature chart updates
```

---

## Files Modified

| File | Changes |
|------|---------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | A: patch tree, button rename. B: right panel, floating window, chart, parsing |
| `STLGeneratorForOpenFoam/server/server.py` | B: `/api/server-patches` endpoint |
| `dctwin/dctwin/third_parties/foam/template/system/steady/controlDict.j2` | B2: surfaceFieldValue FOs |
| `dctwin/dctwin/runner/case.py` | B2: pass monitoring config to controlDict |

---

## Out of Scope

- Transient solver monitoring (only steady-state for now)
- Resizable chart window (fixed size is sufficient)
- Chart zoom/pan (auto-scroll only)
- Exporting chart data to file
- ACU temperature monitoring (only server patches)
