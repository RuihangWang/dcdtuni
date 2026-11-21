# Liquid Cooling Monitoring & Post-Processing Design

**Date:** 2026-05-06
**Status:** Approved
**Approach:** Minimal modification (Approach 1) — fix tab switching + add post-processing click interaction

## Problem

In liquid cooling mode, clicking the solver tab shows air-cooling monitoring controls (server inlet/outlet patch tick boxes and sensor points). This is wrong — liquid mode should display CP-CU pair monitoring and CDU monitoring instead. Additionally, the post-processing tab has no liquid-specific UI: the model tree is hidden and there's no way to view per-CP or CDU time-series results.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Solver tab right sidebar structure | Single panel "🌊 液冷求解监控" (no sub-tabs) | Liquid system is closed-loop, no spatial sensors needed; CP-CU + CDU items fit in one panel |
| Post-processing clickable objects | CP-CU pairs and CDU only | Core monitoring objects for v1; pipes/nodes/valves deferred |
| Floating window reuse | Same component for real-time and post-processing | Reduce code, consistent UX; switch data source via mode param |

## Section 1: Solver Tab — Liquid Monitoring Panel

### Modification: `_applyLeftTabUI()` liquid branch

In `web/STL_generate.html` (~Line 21143), when `isLiquidMode()` and `tab === 'solver'`:

- Hide `#solver-right-subtabs` (air-cooling sub-tab bar)
- Hide `#solver-monitoring-right` and `#solver-sensor-right`
- Show `#liquid-solver-monitoring-right`
- Call `lsmLoadCPList(graph)` to populate CP-CU tick boxes

When not in liquid mode: no change to existing behavior.

### Panel layout (`#liquid-solver-monitoring-right`)

Existing skeleton (Lines 1023-1042) needs expansion:

```
┌─────────────────────────────────┐
│  🌊 液冷求解监控                  │
├─────────────────────────────────┤
│  ☑ 监控 Loss 收敛                │
├─────────────────────────────────┤
│  📊 CP-CU 监控                   │
│  [全选] [全不选]                  │
│  ── Rack-1 ──                   │
│    ── Server-1 ──               │
│      ☑ CP-1 (200W)              │
│      ☑ CP-2 (150W)              │
│    ── Server-2 ──               │
│      ☑ CP-1 (300W)              │
│  ── Rack-2 ──                   │
│    ...                          │
├─────────────────────────────────┤
│  🏭 CDU 监控                     │
│  ☑ 回水温度 (Return Temp)         │
│  ☑ 供水压力 (Supply Pressure)     │
│  ☑ 总流量 (Total Flow)           │
└─────────────────────────────────┘
```

### Data sources

- CP-CU list: from current project's `.liquidrun` config via `lsmLoadCPList(graph)` (already reads `racks[].servers[].cp_cu_pairs[]`)
- CDU metrics: requires backend enhancement — `_extract_cp_monitor()` in `runner.py` must add `cdu_monitor` field to SSE callback

### Backend CDU data enhancement

Add to `runner.py` `_extract_cp_monitor()` return value:

```python
"cdu_monitor": {
    "return_temp_K": float,    # BC return node temperature
    "supply_P_Pa": float,      # BC supply node pressure
    "total_flow_kg_s": float   # sum of all edge flows at supply BC
}
```

Source: BC nodes in `CompiledGraph` — `bc_mask`, `bc_P`, `bc_T`, and edge flows adjacent to BC nodes.

## Section 2: Post-Processing Tab — Model Tree + Click Popup

### Modification: `_applyLeftTabUI()` post-processing branch

In `web/STL_generate.html` (~Line 21132), when `isLiquidMode()` and `tab === 'postprocess'`:

- **Show** `#tree-container` (restore model tree — currently hidden for all post-processing)
- Hide air-cooling post-processing panels
- Right sidebar: show brief hint "点击模型树中的 CP 或 CDU 查看结果"
- Bind click events on CP-CU and CDU tree nodes

When not in liquid mode: no change to existing behavior.

### Model tree click interaction

When user clicks a CP-CU pair in the model tree:
1. Call `GET /api/liquid/results` to load `liquid_result.json` (cache after first load)
2. Extract time-series data for that CP
3. Open floating window via `lsmShowPostProcessWindow('cp', edgeId, resultData)`

When user clicks CDU:
1. Same result loading (cached)
2. Open floating window via `lsmShowPostProcessWindow('cdu', 'cdu', resultData)`

### Floating window tab structure

**For CP-CU pair:**

| Tab | Series | Unit |
|-----|--------|------|
| 温度 | T_chip (芯片温度), T_water_out (出水温度) | K or °C |
| 流量 | m (质量流量) | kg/s |
| 水压 | P_inlet (入口压力), P_outlet (出口压力) | Pa |

**For CDU:**

| Tab | Series | Unit |
|-----|--------|------|
| 温度 | Supply Temp, Return Temp | K or °C |
| 流量 | Total Flow | kg/s |
| 水压 | Supply Pressure, Return Pressure | Pa |

### Data mapping from `liquid_result.json`

| UI field | JSON path | Notes |
|----------|-----------|-------|
| T_chip | `chip_temp_K[cp_idx, :]` | Cold plate solid temperature |
| T_water_out | `water_outlet_temp_K[cp_idx, :]` | NTU-computed outlet water temp |
| m (flow) | `edge_flow_kg_s[edge_idx, :]` | CP edge flow rate |
| P_inlet | `node_pressure_Pa[from_node_idx, :]` | CP inlet node pressure |
| P_outlet | `node_pressure_Pa[to_node_idx, :]` | CP outlet node pressure |
| CDU supply T | BC supply node temperature | From node BC config |
| CDU return T | Derived from rack_summaries | Return aggregation |
| Time axis | `time_s[:]` | Unified X axis |

### Reuse strategy

Extend `liquid_solve_monitor.js` with new entry function:

```javascript
window.lsmShowPostProcessWindow(objectType, objectId, resultData)
// objectType: 'cp' | 'cdu'
// objectId: CP edge ID string or 'cdu'
// resultData: cached liquid_result.json content
```

This function reuses the existing floating window creation, drag/resize/collapse mechanics, and Canvas 2D chart rendering. Only the data source differs (static JSON arrays instead of SSE stream).

## Section 3: End-to-End Flow

```
1. Open Liquid.cfdproj → liquid mode (state.displayMode === 'liquid')
2. Configure rack/server/CP-CU pairs
3. Click "液冷求解" tab
   → Right sidebar shows liquid monitoring panel (CP-CU + CDU tick boxes)
4. Click "Run Liquid Solve"
   → Floating window appears with real-time Loss/Temp/Flow curves
   → SSE streams epoch data + cp_monitor + cdu_monitor
5. Solve completes → floating window retains final curves
6. Click "液冷后处理" tab
   → Left sidebar restores model tree
   → Right sidebar shows hint: "点击模型树中的 CP 或 CDU 查看结果"
7. Click CP-CU pair in tree
   → GET /api/liquid/results (cached after first load)
   → Floating window with Temp/Flow/Pressure tabs
8. Click CDU in tree
   → Same, with CDU-level summary curves
```

## Files to Modify

| File | Changes |
|------|---------|
| `web/STL_generate.html` | `_applyLeftTabUI()` liquid branches for solver + postprocess; model tree click event binding; CDU rendering in model tree |
| `web/js/liquid_solve_monitor.js` | CDU tick box UI in panel; CDU SSE data parsing; `lsmShowPostProcessWindow()` entry; post-process data loading and chart rendering |
| `server/server.py` | `/api/liquid/solve` callback adds `cdu_monitor` field |
| `src/backend/liquidcooling/runner.py` | `_extract_cp_monitor()` adds CDU-level summary extraction |

## Files NOT Modified

- Air-cooling mode paths — zero changes
- `.liquidrun` schema — no new fields
- `liquid_result.json` structure — already contains all needed data
- Floating window drag/resize/collapse mechanics — direct reuse

## Test Plan

Using `Liquid.cfdproj` test case, verify via browser-use:

1. **Solver tab panel switch**: Load project → liquid mode → solver tab → screenshot confirms liquid panel (not air-cooling)
2. **CP-CU list population**: Export liquidrun → verify CP list grouped by rack/server
3. **Real-time monitoring**: Trigger solve → verify floating window with Loss/Temp/Flow curves updating
4. **CDU monitoring**: Verify CDU metrics in SSE stream and displayed in chart
5. **Post-process model tree**: Solve complete → post-process tab → verify model tree restored
6. **Post-process popup**: Click CP node → verify floating window with correct Temp/Flow/Pressure curves
7. **Visual verification**: Screenshot each step for layout correctness and data sanity
