# Sub-D: Frontend Results UI for Liquid ODIL Solver

## Goal

Rewrite the liquid results panel to display ODIL solver output (rack summaries, per-server chip/water temperatures, flow rates), add click-to-expand transient charts (T_chip/T_out/m vs time), implement per-pipe and per-chip 3D temperature coloring, and connect CDU servedRacks in the results view.

## Background

The liquid ODIL solver backend is complete (Sub-A + Sub-B + Sub-C1 + Sub-C2, 148 tests passing). The `/api/liquid/results` endpoint returns a full `LiquidResult` JSON with `rack_summaries[]`, `chip_temp_K[]`, `water_outlet_temp_K[]`, `cell_temp_K[]`, `time_s[]`, etc. The existing `liquid_results_panel.js` (339 lines) was built for the old solver format — it needs a full rewrite to consume the ODIL output.

Sub-C2 added real-time convergence monitoring during solve (epoch-axis charts). Sub-D is about displaying the **final converged results** (time-axis physical quantities) after the solve completes.

## Scope

| In Scope | Out of Scope |
|----------|--------------|
| Results panel rewrite for ODIL format | Backend changes |
| Per-server transient charts (T/m vs time) | New solver physics |
| Per-pipe 3D temperature coloring (average T) | Per-cell gradient coloring (future upgrade) |
| Per-chip 3D temperature coloring | Volume rendering |
| CDU servedRacks display | New API endpoints |
| Color scale legend overlay | |

---

## Design

### 1. Results Panel Rewrite

**File:** `liquid_results_panel.js` — rewrite `buildResultsPanel()`

**Data source:** `state.liquidCooling.results` (fetched from `/api/liquid/results`)

**Panel structure:**

```
┌─ 🌊 Liquid Cooling Results ────────────────┐
│ Status: ✅ Converged | Loss: 0.05 | 3200 ep│
├─ System Overview ──────────────────────────│
│ CDU_01 → Rack_01, Rack_02                  │
│ Total Flow: 12.5 LPM  Max T_chip: 72.3°C  │
├─ 📊 Rack: rack1 ──────── [▼ expand] ──────│
│ Flow: 12.5 LPM  Supply: 35.0°C  Return: 42.1°C │
│ ┌─────────────────────────────────────────│
│ │ Server│ Flow │ T_chip │ T_out │ Q(W) │St│
│ │ srv1  │ 4.2  │ 68.5°C │ 39.2  │ 300  │✅│
│ │ srv2  │ 4.1  │ 72.3°C │ 41.5  │ 400  │✅│  ← click to expand chart
│ │ srv3  │ 4.2  │ 70.1°C │ 40.3  │ 350  │✅│
│ └─────────────────────────────────────────│
│ [── Transient chart for srv2 (expanded) ──]│
│ │  T_chip ── T_out ── m                   ││
│ │  72°C                         0.14 kg/s ││
│ │  ...canvas chart 200px...               ││
│ └─────────────────────────────────────────│
├─ 🎨 3D Display ───────────────────────────│
│ ☑ Pipe temperature coloring               │
│ ☑ Chip temperature coloring               │
└────────────────────────────────────────────┘
```

**Data mapping (ODIL → panel):**

All values are from the **last time step** (`[-1]` index) for the summary table:

| Panel field | Source |
|-------------|--------|
| Converged | `results.converged` |
| Loss | `results.final_loss` |
| Epochs | `results.epochs` |
| Flow (LPM) | `server.flow_kg_s[-1] / 997.0 * 60000` |
| T_chip (°C) | `server.max_chip_temp_K[-1] - 273.15` |
| T_out (°C) | `server.heat_removed_W[-1] / (server.flow_kg_s[-1] * 4182) + supply_T` (or from `water_outlet_temp_K`) |
| Q (W) | `server.heat_removed_W[-1]` |
| Supply T | `rack.supply_temp_K[-1] - 273.15` |
| Return T | `rack.return_temp_K[-1] - 273.15` |

**Chip temperature status indicators:**
- Green ✅: T_chip < 75°C
- Yellow ⚠️: 75°C ≤ T_chip < 85°C
- Red 🔴: T_chip ≥ 85°C

**CDU servedRacks:** Iterate CDU models in `state.models`, show `liquidCooling.servedRacks[]` in system overview.

### 2. Transient Chart (Click-to-Expand)

Click a server row → expand a 200px-height inline canvas chart below that row.

**Series:**
- T_chip (°C) — red line, left Y-axis
- T_water_out (°C) — blue line, left Y-axis
- Flow m (kg/s) — green line, right Y-axis

**X-axis:** `time_s[]` from results

**Data source:** `rack_summaries[].servers[].max_chip_temp_K[:]` (full time series), `flow_kg_s[:]`, `heat_removed_W[:]`. Water outlet temperature computed from heat balance: `T_out = T_supply + Q / (m * cp)`.

**Interaction:**
- Click same row again → collapse
- Click different row → collapse current, expand new
- Canvas chart rendered with same custom 2D pattern as `_lsm*` (dual Y-axis variant)

### 3. 3D Pipe Temperature Coloring

**File:** `liquid_pipe_renderer.js` — enhance `colorPipesByTemperature()`

**Algorithm:**
1. Read `cell_temp_K` from `state.liquidCooling.results`
2. For each pipe edge: find cells belonging to that pipe (via `cell_positions` matching pipe waypoints, or simpler: use edge ordering — cells are stored sequentially per pipe in the backend)
3. Compute mean temperature per pipe
4. Map to color via `_tempToColor(T, Tmin, Tmax)`: blue (0x2196F3) → red (0xF44336) linear interpolation
5. Set `material.color` on each pipe's Three.js tube mesh

**For non-pipe edges** (pump, valve, cold_plate): use the from-node pressure as a proxy or skip coloring.

**Color range:** `[min(all cell temps), max(all cell temps)]` auto-scaled from results.

### 4. 3D Chip Temperature Coloring

**File:** `liquid_pipe_renderer.js` or `STL_generate.html` — implement `_toggleServerTemperatureColoring()`

**Algorithm:**
1. Read `chip_temp_K` from results + `rack_summaries` to map CP → server → equipment model
2. For each liquid-cooled equipment: find its 3D mesh in `state.models`
3. Set mesh `material.color` via `_tempToColor(T_chip, Tmin_chip, Tmax_chip)`
4. When toggled off, restore original material color

**Chip color range:** `[min(chip_temp_K), max(chip_temp_K)]`

### 5. Color Scale Legend

**Location:** HTML overlay div in the 3D viewport area (fixed position, bottom-right corner)

**Appearance:** A horizontal gradient bar (120×16px) with Tmin/Tmax labels, visible only when temperature coloring is active.

```html
<div id="lc-colorbar" style="display:none; position:absolute; bottom:20px; right:20px; z-index:100;">
  <div style="background:linear-gradient(to right, #2196F3, #F44336); width:120px; height:16px; border-radius:3px;"></div>
  <div style="display:flex; justify-content:space-between; font-size:9px; color:#aaa;">
    <span id="lc-colorbar-min">35°C</span>
    <span id="lc-colorbar-max">72°C</span>
  </div>
</div>
```

Shown/hidden by the 3D display checkboxes. Updates Tmin/Tmax labels dynamically from results.

### 6. `_tempToColor()` Utility

```javascript
function _tempToColor(T, Tmin, Tmax) {
    var t = Math.max(0, Math.min(1, (T - Tmin) / (Tmax - Tmin + 1e-6)));
    // Blue (0x2196F3) → Red (0xF44336) linear interpolation
    var r = Math.round(0x21 + t * (0xF4 - 0x21));
    var g = Math.round(0x96 + t * (0x43 - 0x96));
    var b = Math.round(0xF3 + t * (0x36 - 0xF3));
    return (r << 16) | (g << 8) | b;
}
```

---

## Files Changed

| File | Change |
|------|--------|
| `STLGeneratorForOpenFoam/web/js/liquid_results_panel.js` | Full rewrite of `buildResultsPanel()` for ODIL format + transient chart + CDU info |
| `STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js` | Per-pipe average temp coloring + `_tempToColor()` + chip coloring |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Color scale legend overlay div (`lc-colorbar`) |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | Results panel i18n keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | Same |

## Testing Strategy

- **Manual via browser-use:** Load a project with liquid results, verify panel shows correct rack/server data, click server rows to see transient charts, toggle 3D coloring checkboxes
- **No new pytest tests:** Pure frontend UI changes
