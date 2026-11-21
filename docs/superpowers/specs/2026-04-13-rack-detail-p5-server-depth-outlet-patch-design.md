# Rack Detail P5 — Per-server Depth + Outlet Patch Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow each server (fan-server / non-fan equipment) to have its own depth offset that stacks on top of the rack-level offset, and allow adding a rectangular or circular patch on the server outlet face to serve as the actual airflow outlet (remaining area becomes wall).

**Architecture:** Per-equipment `extraInletOffset` / `extraOutletOffset` stacked on `rack.inletOffset` / `rack.outletOffset`. Outlet patch stored as `equipment.outletPatch` with type/position/size. STL export splits the outlet face into patch solid + wall solid. Backend registers patch as `fan_outlet`, wall remainder as `wall`.

**Tech Stack:** Three.js r128, vanilla JS (STL_generate.html), Python (stlgen_runner.py).

---

## Scope

| Feature | Source |
|---------|--------|
| Per-server depth adjustment | User issue #8 |
| Server outlet patch (rect/circle) | User issue #9 |

## Non-goals

- Inlet patch on server (only outlet for now).
- Per-server door (doors are per-rack-face, not per-server).
- Rotation gizmo bug (#4) and 2D projection bug (#6) — separate specs.

---

## 1. Per-server Depth (#8)

### 1.1 State

Each equipment in `model.rack.equipments[]` gets 2 new properties:

```javascript
equipment.extraInletOffset = 0;   // meters, stacked on rack.inletOffset
equipment.extraOutletOffset = 0;  // meters, stacked on rack.outletOffset
```

**Effective depth for a specific server:**
```
serverInletDepth  = rack.inletOffset + equipment.extraInletOffset
serverOutletDepth = rack.outletOffset + equipment.extraOutletOffset
serverSlotStart   = serverInletDepth
serverSlotEnd     = model.base.D - serverOutletDepth
serverSlotDepth   = serverSlotEnd - serverSlotStart
```

### 1.2 Validation (in `syncRackModelState`)

```
equipment.extraInletOffset >= 0
equipment.extraOutletOffset >= 0
serverInletDepth + serverOutletDepth < model.base.D  (server must have non-zero depth)
```

If validation fails, scale `extraInletOffset` and `extraOutletOffset` down proportionally.

### 1.3 Defaults + Backward Compatibility

Both default to 0. Existing projects without these fields load fine — `cloneRackState` falls back to 0. When both are 0, the server uses the rack-level offset (identical to current P2 behavior).

### 1.4 UI (Rack Detail panel, when server is selected)

When the user selects a server (equipment) in Rack Detail mode, the right panel shows:

```
─── Server 进深调整 / Server Depth ───
额外距 inlet: [input(m)] [==%slider==] X%
额外距 outlet: [input(m)] [==%slider==] X%
(有效进深: X.XXm = rack偏移 + server额外偏移)
```

- Slider range: [0%, 40%] of `model.base.D`
- Input uses `fromDisplayLength()` for unit conversion
- Slider handler uses `doRebuildRackDetail()` (not `rebuildGeometry()`) to avoid DOM rebuild

### 1.5 Geometry

When a server has `extraInletOffset > 0` or `extraOutletOffset > 0`:

- That server's top/bottom plates use `serverSlotStart` / `serverSlotEnd` instead of `slotDepthStart` / `slotDepthEnd`
- That server's inlet/outlet face patches use the server-specific depth positions
- Other servers in the same rack are unaffected (they use rack-level offset unless they have their own extra offset)

The rack-level inner walls and cavity walls remain at the rack-level offset. Per-server offset only affects the individual server's plates and faces.

### 1.6 cfd_export

In the server export object, add:

```json
{
    "id": "srv_1",
    "type": "fanServer",
    "extraInletOffset": 0.03,
    "extraOutletOffset": 0.05,
    ...
}
```

Backend reads these for geometry positioning but BC logic is unchanged.

---

## 2. Server Outlet Patch (#9)

### 2.1 Concept

Currently, the entire server outlet face is one solid (`{exportStem}_outlet_patch` for fan-servers). With this feature, the user can define a sub-region (rectangular or circular patch) within the outlet face:

- **Patch region** → becomes the actual airflow outlet (`fan_outlet` BC with `volumeFlowRate`)
- **Remaining region** → becomes wall (`wall` BC, no flow)

This models servers with focused exhaust vents rather than full-face exhaust.

### 2.2 State

```javascript
equipment.outletPatch = null;  // null = entire face is outlet (legacy)
// or:
equipment.outletPatch = {
    type: 'rect',       // 'rect' | 'circle'
    cx: 0.5,            // center X position (0~1, fraction of face width)
    cy: 0.5,            // center Y position (0~1, fraction of face height)
    width: 0.6,         // rect width (fraction of face width)
    height: 0.8,        // rect height (fraction of face height)
    // for circle: radius instead of width/height
    // radius: 0.3      // fraction of min(face width, face height)
}
```

### 2.3 Validation

- `cx`, `cy` ∈ [0, 1]
- `width`, `height` ∈ (0, 1]
- For rect: patch must fit within the face (cx ± width/2 within [0,1], cy ± height/2 within [0,1])
- For circle: center + radius must stay within face bounds

### 2.4 UI (Rack Detail panel, when server outlet face is selected)

```
─── Outlet 出风口 / Outlet Patch ───
☐ 添加 Patch / Add Patch
[展开后:]
类型: [rect ▼] [circle ▼]
宽度: [====slider====] 60%
高度: [====slider====] 80%
X 位置: [====slider====] 50%
Y 位置: [====slider====] 50%
```

- Checkbox toggles `outletPatch` between `null` and default `{type:'rect', cx:0.5, cy:0.5, width:0.6, height:0.8}`
- Sliders use `doRebuildRackDetail()` to avoid DOM rebuild

### 2.5 Rendering

- **Patch region**: rendered in bright green (`#00cc66`, opacity 0.8) to highlight the outlet area
- **Wall region**: rendered in dark grey (`#555555`, opacity 0.6) to show it's blocked
- Both are on the same outlet face plane, side by side

### 2.6 STL Export

When `outletPatch` is set, the outlet face splits into 2 solids:

```
{exportStem}_outlet_patch   → the patch region (rect/circle cutout)
{exportStem}_outlet_wall    → the remaining region (face minus patch)
```

When `outletPatch` is null (legacy): single solid `{exportStem}_outlet_patch` as before.

**Geometry export:** The patch is a subset of the face quad. For rectangular patches, this is straightforward (2 triangles for the patch, 8 triangles for the L-shaped remainder). For circular patches, the circle is approximated with N-gon triangulation.

### 2.7 cfd_export.json

```json
{
    "id": "srv_1",
    "type": "fanServer",
    "outletSolidName": "Rack_1_srv_1_outlet_patch",
    "outletWallSolidName": "Rack_1_srv_1_outlet_wall",
    "outletPatch": {
        "type": "rect",
        "cx": 0.5, "cy": 0.5,
        "width": 0.6, "height": 0.8
    },
    ...
}
```

When no patch: `outletWallSolidName` is `null`, `outletPatch` is `null`.

### 2.8 Backend BC

In `_build_bridge_bc()`:

```python
if server.get("outletPatch"):
    # Patch region = fan_outlet (existing BC)
    registry.register(outlet_solid, BCSpec(bc_type="fan_outlet", flow_rate=flow_rate))
    # Wall region = wall
    wall_solid = server.get("outletWallSolidName")
    if wall_solid:
        registry.register(_sanitize(wall_solid), BCSpec(bc_type="wall"))
else:
    # Legacy: entire face is fan_outlet
    registry.register(outlet_solid, BCSpec(bc_type="fan_outlet", flow_rate=flow_rate))
```

`fields.py`: no changes needed — `wall` and `fan_outlet` BC types already exist.

---

## 3. File Changes

| File | Change |
|------|--------|
| `STL_generate.html` | Equipment state, validation, UI, rendering, STL export split, cfd_export |
| `stlgen_runner.py` | Read per-server offsets + outlet patch BC registration |

## 4. Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Set server extra inlet offset to 5cm | Server inlet face moves 5cm deeper than rack offset |
| 2 | Two servers with different extra offsets | Each at its own depth |
| 3 | Extra offset = 0 (default) | Same as rack offset (backward compat) |
| 4 | Add rect outlet patch at 60%×80% | Green patch visible, grey wall remainder |
| 5 | Add circle outlet patch | Circular green region |
| 6 | Export STL with patch | Two solids: `_outlet_patch` + `_outlet_wall` |
| 7 | cfd_export with patch | `outletSolidName`, `outletWallSolidName`, `outletPatch` present |
| 8 | Remove patch (toggle off) | Single outlet solid, legacy behavior |
| 9 | Load old project | No errors, no patches, no extra offsets |

## 5. Risks

- **STL face splitting complexity**: Splitting a quad into patch + remainder for circle type requires triangulation. For rect it's straightforward. Start with rect only, add circle as enhancement.
- **Rendering in normal 3D view**: The outlet patch coloring should be visible in both Detail and normal 3D modes so the user knows which servers have patches.
