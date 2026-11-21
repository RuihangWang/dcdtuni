# Rack Detail P2 — Inner Width & U-slot Depth Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Rack's inner channel width (slotWidthRatio) and U-slot depth (inlet/outlet offsets) user-configurable via the Rack Detail panel, and generate the corresponding new inner wall geometry + STL solids.

**Architecture:** Two new `model.rack` properties (`inletOffset`, `outletOffset`) drive geometry changes in `renderRackModel` and STL export. The existing `slotWidthRatio` is exposed via a new UI control. All controls live in the Rack Detail right panel (replacing P1 placeholders).

**Tech Stack:** Three.js r128, vanilla JS (STL_generate.html).

---

## Scope

Phase 2 of the 4-phase Rack Detail Editor:

| Phase | Status |
|-------|--------|
| P1 View mode shell | Done |
| **P2 Inner width + depth** | **This spec** |
| P3 Perforated door | Future |
| P4 Leakage interaction polish | Future |

## Non-goals

- Perforated door (P3).
- Backend runner/fields changes (new solids auto-get default wall BC).
- Changes to leakage zone logic.
- Changes to non-Rack model types.

---

## 1. State Properties

### 1.1 Existing (now exposed to UI)

```javascript
model.rack.slotWidthRatio  // default 0.8, range [0.2, 0.95]
```

Already exists in `getDefaultRackState()` and clamped in `syncRackModelState()`. P2 adds a UI control in the Detail panel.

### 1.2 New

```javascript
model.rack.inletOffset = 0;   // meters, default 0 (no offset = legacy)
model.rack.outletOffset = 0;  // meters, default 0
```

**Validation (in `syncRackModelState`):**
- `inletOffset >= 0`
- `outletOffset >= 0`
- `inletOffset <= 0.5 * model.base.D`
- `outletOffset <= 0.5 * model.base.D`
- `inletOffset + outletOffset < model.base.D` (U-slot must have non-zero depth)

**Default 0** ensures full backward compatibility — existing projects have no offset, U-slot fills the entire rack depth as before.

## 2. UI Controls (Rack Detail Panel)

Replace the P1 placeholder divs (`#rack-detail-inner-width`, `#rack-detail-slot-depth`) with live controls.

### 2.1 Inner Width Section

```
─── 内径宽度 / Inner Width ───
[精确值(m)] [=====0.48=====]  [====%滑条====] 80%
```

- **Input box** (left): precise value in meters. Bound to `model.rack.slotWidthRatio * model.base.W`.
  - Validation: > 0, ≤ model.base.W
  - On change: compute new ratio = value / model.base.W, clamp [0.2, 0.95], update state, rebuild
- **Percentage slider** (right): [20%, 95%]. Bound to `slotWidthRatio * 100`.
  - On input: update ratio, update input box, rebuild geometry in real-time
- **Percentage label**: shows current % value

Both are bidirectionally bound — editing one updates the other.

### 2.2 U-slot Depth Section

```
─── U-slot 进深 / Slot Depth ───
距 inlet 面:  [===0.05m===]
距 outlet 面: [===0.05m===]

[===inlet %===]  10%
[===outlet %===] 10%
[===同时控制===]  10%
```

- **Two input boxes**: precise values in meters.
  - Validation: ≥ 0, ≤ 0.5 × model.base.D
  - Value 0 means "no offset" (U-slot flush with rack face)
- **Three percentage sliders**: [0%, 50%] of model.base.D
  - Inlet slider: controls `inletOffset` only
  - Outlet slider: controls `outletOffset` only
  - **Linked slider** ("同时控制"): when dragged, sets BOTH inlet and outlet to the SAME percentage value. Moving the linked slider to 10% sets both offsets to 10% of D.
- All 5 controls (2 inputs + 3 sliders) are bidirectionally bound.
- `pushUndoCheckpoint` on first interaction (flag pattern, same as opacity sliders).

## 3. Geometry Changes

### 3.1 U-slot Depth Computation

Current: U-slot spans full rack depth D along the depth axis.

New: U-slot spans from `inletOffset` to `D - outletOffset`.

```
effectiveSlotDepth = model.base.D - inletOffset - outletOffset
slotDepthStart = inletOffset        // local depth coordinate
slotDepthEnd = model.base.D - outletOffset
```

### 3.2 Existing Geometry Adjustments

| Element | Current | New (when offset > 0) |
|---------|---------|----------------------|
| `{rName}_inner_left` | Full depth D | Shortened to `effectiveSlotDepth` |
| `{rName}_inner_right` | Full depth D | Shortened to `effectiveSlotDepth` |
| Fan-server inlet patch | At inlet face (depth=0) | At `slotDepthStart` |
| Fan-server outlet patch | At outlet face (depth=D) | At `slotDepthEnd` |
| Equipment top/bottom plates | Full depth D | Shortened to `effectiveSlotDepth` |
| Merged empty slot faces | At inlet/outlet face | At `slotDepthStart` / `slotDepthEnd` |
| Leakage zone box | Full depth D | Shortened to `effectiveSlotDepth` |

### 3.3 New Geometry Elements

When `inletOffset > 0`:

| New STL Solid | Description |
|---------------|-------------|
| `{rName}_inlet_cavity_left` | Vertical wall from inlet face to U-slot front, left side of channel |
| `{rName}_inlet_cavity_right` | Same, right side |

When `outletOffset > 0`:

| New STL Solid | Description |
|---------------|-------------|
| `{rName}_outlet_cavity_left` | Vertical wall from outlet face to U-slot back, left side |
| `{rName}_outlet_cavity_right` | Same, right side |

These are rectangular faces (width = `inletOffset` or `outletOffset`, height = rack internal height). They seal the offset cavity on the left/right sides.

**CFD boundary type**: wall (default — no special BC needed).

### 3.4 Rendering

In `renderRackModel()`:
- When offsets are 0 (default): behavior unchanged, zero code path difference
- When offset > 0: all depth-dependent geometry uses `slotDepthStart` / `slotDepthEnd` instead of `0` / `D`, and new cavity wall meshes are added

Color for cavity walls: same as channel walls (`#707783`).

## 4. STL Export Changes

`exportBoxModel()` already groups meshes by `userData.exportName`. The new cavity wall meshes just need correct `exportName` in their `userData`:

```javascript
mesh.userData = { exportName: `${rName}_inlet_cavity_left`, faceId: 'inlet_cavity_left' };
```

No changes to the export pipeline itself.

## 5. cfd_export.json Changes

In the rack geometry object:

```json
"racks": {
    "Rack_1": {
        "geometry": {
            "anchor": {...},
            "size": {"W": 0.6, "D": 1.0, "H": 1.867},
            "orientation": 0,
            "unitCount": 42,
            "unitHeight": 0.04445,
            "slotWidthRatio": 0.8,
            "inletOffset": 0.05,
            "outletOffset": 0.05
        },
        ...
    }
}
```

Backend reads these values but P2 does NOT require backend changes — the new wall solids automatically get default wall BC treatment.

## 6. Backward Compatibility

- `inletOffset` and `outletOffset` default to 0
- Existing `.cfdproj` files without these fields load fine (defaults kick in)
- Existing rack geometry with offset=0 renders identically to before
- `syncRackModelState()` initializes missing fields to 0

## 7. File Changes

| File | Change |
|------|--------|
| `STL_generate.html` | State defaults, validation, UI controls in detail panel, geometry rendering changes in `renderRackModel`, new cavity wall meshes, faceLayouts adjustments, STL export userData |

Single-file change.

## Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Default rack (no offsets) | Renders identically to before |
| 2 | Set slotWidthRatio to 60% via slider | Inner channel narrows, inner walls move inward |
| 3 | Set slotWidthRatio via input box (0.36m on 0.6m rack) | Slider updates to 60% |
| 4 | Set inlet offset to 0.1m | U-slot front face moves inward, inlet cavity walls appear |
| 5 | Set outlet offset to 0.1m | U-slot back face moves inward, outlet cavity walls appear |
| 6 | Use "同时控制" slider at 10% | Both offsets set to 10% of D simultaneously |
| 7 | Fan-server in rack with offsets | Inlet/outlet patches at correct offset position |
| 8 | Leakage zone with offsets | Zone depth matches effective slot depth |
| 9 | STL export with offsets | New cavity solids appear in export |
| 10 | Load old project (no offset fields) | Defaults to 0, renders correctly |
| 11 | Offset validation: try > 0.5×D | Clamped to max |
| 12 | Offset validation: inlet+outlet ≥ D | Rejected, total must be < D |

## Risks

- **Geometry complexity**: `renderRackModel` is already ~300 lines. Adding offset logic will make it longer. Mitigated by isolating the cavity wall rendering into a helper function.
- **Edge case**: offset set very large (close to 0.5×D) may produce very thin U-slots. Validation clamp prevents this.
