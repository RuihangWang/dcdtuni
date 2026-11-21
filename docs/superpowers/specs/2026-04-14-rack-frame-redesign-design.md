# Rack Frame Redesign — Watertight Frame + Configurable Margins + Partition Depth

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the rack shell into a proper watertight "picture frame" solid, change safety margins from integer U-count to continuous meter values (not occupying U-slots), and make partitions follow U-slot depth.

**Architecture:** Merge all rack shell faces (outer + inner cavity + margin strips + frame strips) into a single watertight solid `{rackName}_frame`. Change `safeMarginTopUnits`/`safeMarginBottomUnits` (int) to `safeMarginTop`/`safeMarginBottom` (float, meters). Rack total height = slot area height + margins. Partitions compute depth from the intersection of adjacent slot depth ranges.

**Tech Stack:** Three.js r128, vanilla JS (STL_generate.html), Python (stlgen_runner.py).

---

## Scope

| Feature | Description |
|---------|-------------|
| Watertight rack frame | All shell faces merged into 1 solid `{rackName}_frame` |
| Continuous margin | `safeMarginTop`/`safeMarginBottom` in meters, default = 1U height |
| Full U availability | 42U = 42 usable slots, margins outside slot area |
| Partition depth | Follows slot cavity depth, takes shallower of adjacent slots |
| Partition BC | Always `wall` (automatic, not user-configurable) |

## Non-goals

- Changing blind plate logic (kept as-is, including `deletedFaces` per-side per-slot control)
- Changing server geometry (already completed in Server Geometry Redesign)
- Changing door logic (kept as-is)
- Changing leakage zone logic (kept as-is)

---

## 1. State Changes

### 1.1 New Fields

```javascript
rack.safeMarginTop = 0.04445;    // meters, default = 1U height (RACK_DEFAULT_UNIT_HEIGHT)
rack.safeMarginBottom = 0.04445; // meters, default = 1U height
```

### 1.2 Deprecated Fields

```javascript
// REMOVED — migrated in cloneRackState()
rack.safeMarginTopUnits    // int → safeMarginTop = safeMarginTopUnits × unitHeight
rack.safeMarginBottomUnits // int → safeMarginBottom = safeMarginBottomUnits × unitHeight
```

### 1.3 Validation (syncRackModelState)

```
safeMarginTop >= 0
safeMarginBottom >= 0
safeMarginTop + safeMarginBottom < unitCount × unitHeight  (frame must not consume all height)
```

### 1.4 Backward Compatibility (cloneRackState)

```javascript
if (Number.isFinite(rawRack.safeMarginTop)) {
    rack.safeMarginTop = rawRack.safeMarginTop;
} else if (Number.isFinite(rawRack.safeMarginTopUnits)) {
    rack.safeMarginTop = rawRack.safeMarginTopUnits * rack.unitHeight;
} else {
    rack.safeMarginTop = rack.unitHeight;  // default 1U
}
// Same pattern for safeMarginBottom
```

---

## 2. Dimension Calculations

### 2.1 Rack Total Height

```
slotAreaHeight = unitCount × unitHeight
H = slotAreaHeight + safeMarginTop + safeMarginBottom
```

Previously: `H = unitCount × unitHeight` (margins stolen from slot area).
Now: `H = unitCount × unitHeight + marginTop + marginBottom` (margins additive).

### 2.2 Slot Z Positions

```
slotZStart = safeMarginBottom                           // bottom of slot area
slotZEnd   = safeMarginBottom + unitCount × unitHeight  // top of slot area
slotZ(i)   = safeMarginBottom + i × unitHeight          // bottom of slot i
```

### 2.3 Selectable Slot Range

All slots are selectable: `[0, unitCount)`. The functions `getRackSelectableSlotRange()` and `isRackSlotSelectable()` are removed.

### 2.4 getRackSlotBounds() Update

```javascript
function getRackSlotBounds(model, slotIndex) {
    var rack = model.rack;
    var slotWidth = model.base.W * rack.slotWidthRatio;
    var slotX = (model.base.W - slotWidth) / 2;
    var y = rack.safeMarginBottom + slotIndex * rack.unitHeight;  // was: slotIndex * unitHeight
    return { x: slotX, y: y, w: slotWidth, h: rack.unitHeight };
}
```

---

## 3. Frame Geometry

### 3.1 Frame as Watertight Body

The rack frame is a hollow box: outer shell minus inner cavity (the slot opening). All faces belong to a single solid `{rackName}_frame`.

**Outer faces (6):**
- Top: W × D at Z = H
- Bottom: W × D at Z = 0
- Left: D × H at X = 0
- Right: D × H at X = W
- Front: composite (margin strips + frame strips around slot opening)
- Back: composite (same structure as front)

**Front/Back face decomposition:**
Each front/back face has a rectangular opening (the slot area). The frame around the opening consists of:
- Bottom margin strip: W × marginBottom (below slot opening)
- Top margin strip: W × marginTop (above slot opening)
- Left frame strip: frameWidth × slotAreaHeight
- Right frame strip: frameWidth × slotAreaHeight

Where `frameWidth = (W - slotWidth) / 2`.

**Inner cavity faces (4+):**
- Inner left wall: slotDepth × slotAreaHeight
- Inner right wall: slotDepth × slotAreaHeight
- Inner ceiling: slotWidth × slotDepth (between slot top and margin bottom)
- Inner floor: slotWidth × slotDepth (between margin top and slot bottom)
- Cavity offset walls (if inletOffset or outletOffset > 0): same as current `_inlet_cavity_*` / `_outlet_cavity_*`

**All faces share vertices at edges → watertight.**

### 3.2 Frame Rendering

Create a new function `buildRackFrameGeometry()` that generates all frame faces. Replace the current inline rendering code in `renderRackModel()` for:
- Outer shell faces (lines ~6721–6736)
- Safe margin walls (lines ~6738–6770)
- Inner cavity walls (lines ~6860–6931)

All meshes get `userData.exportName = '{rackName}_frame'`.

### 3.3 STL Export

All frame meshes are grouped by `exportName` (same mechanism as `exportBoxModel`). Since they all share `{rackName}_frame`, they merge into a single `solid {rackName}_frame ... endsolid` block.

---

## 4. Partition Depth

### 4.1 Depth Calculation

```javascript
function getPartitionDepthRange(model, partitionIndex) {
    // Partition sits between slot[partitionIndex] and slot[partitionIndex + 1]
    var slotBelow = getSlotDepthRange(model, partitionIndex);
    var slotAbove = getSlotDepthRange(model, partitionIndex + 1);

    // Intersection of the two depth ranges (take shallower)
    var y0 = Math.max(slotBelow.y0, slotAbove.y0);
    var y1 = Math.min(slotBelow.y1, slotAbove.y1);

    return { y0: y0, y1: y1, depth: Math.max(0, y1 - y0) };
}
```

Where `getSlotDepthRange()` returns `{ y0: slotDepthStart, y1: slotDepthEnd }` accounting for rack-level `inletOffset`/`outletOffset` and per-equipment `extraInletOffset`/`extraOutletOffset`.

### 4.2 getSlotDepthRange()

```javascript
function getSlotDepthRange(model, slotIndex) {
    var rack = model.rack;
    var baseStart = rack.inletOffset || 0;
    var baseEnd = model.base.D - (rack.outletOffset || 0);

    // Check if this slot is occupied by equipment with extra offset
    var eq = getRackEquipmentForSlot(model, slotIndex);
    if (eq) {
        baseStart += (eq.extraInletOffset || 0);
        baseEnd -= (eq.extraOutletOffset || 0);
    }

    return { y0: baseStart, y1: baseEnd };
}
```

### 4.3 Rendering

In `renderRackModel()`, partition rendering changes from using full `model.base.D` to using the computed depth range:

```javascript
var pRange = getPartitionDepthRange(model, partition.index);
if (pRange.depth < 1e-6) return; // zero depth = skip

spawnPlanarRect(modelGroup,
    new THREE.Vector3(slotX, pRange.y0, partZ),
    new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0),
    slotWidth, pRange.depth,
    { ... }
);
```

### 4.4 Partition BC

All partitions register as `wall` BC. No user configuration needed.

```python
# In _build_bridge_bc():
for part in rack.get("partitions", []):
    solid = part.get("solidName")
    if solid:
        registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
```

---

## 5. Inspector UI

### 5.1 Margin Inputs

Replace the integer U-count inputs with continuous value inputs using the project's global unit system:

```
顶部 Margin: [input with unit] (default: 44.45mm)
底部 Margin: [input with unit] (default: 44.45mm)
总高度 (计算值): X.XXX m  (read-only)
```

Input uses `fromDisplayLength()` for unit conversion.

### 5.2 Removed Controls

- Remove `safeMarginTopUnits` / `safeMarginBottomUnits` integer inputs

---

## 6. Mesh Refinement Integration

| Setting | Target | Behavior |
|---------|--------|----------|
| Rack surface refinement level | `{rackName}_frame` solid region | Applied as surface level in snappyHexMeshDict. Same as before but now 1 region instead of many. |
| Rack feature edge refinement | Frame STL geometric edges | Unchanged — snappy extracts edges from STL geometry regardless of solid count |
| Server face refinement | Server face solids (separate from frame) | Unchanged |

---

## 7. CFD Export

### 7.1 Rack Export Object

```json
{
    "frameSolidName": "{rackName}_frame",
    "safeMarginTop": 0.04445,
    "safeMarginBottom": 0.04445,
    "partitions": [
        { "index": 4, "solidName": "{rackName}_u5_partition" },
        { "index": 9, "solidName": "{rackName}_u10_partition" }
    ],
    "servers": [...],
    "leakage": {...},
    ...
}
```

### 7.2 Backend Changes

In `_build_bridge_bc()`:
```python
# Frame: single wall BC
frame_solid = rack.get("frameSolidName")
if frame_solid:
    registry.register(_sanitize(frame_solid), BCSpec(bc_type="wall"))

# Partitions: all wall
for part in rack.get("partitions", []):
    solid = part.get("solidName")
    if solid:
        registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
```

In `_generate_snappy_hex_mesh_dict()`: The `{rackName}_frame` region uses rack surface refinement level. Old shell solid names (`_Left`, `_Right`, etc.) are no longer emitted.

---

## 8. File Changes

| File | Change |
|------|--------|
| `STL_generate.html` | State migration, dimension calc, frame geometry builder, partition depth, inspector UI, export naming, CFD export |
| `stlgen_runner.py` | Frame BC registration, partition BC, snappyHexMesh region update, remove old shell solid handling |

## 9. Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | New rack (42U, default margins) | H = 42×0.04445 + 2×0.04445 = 1.956m, all 42 slots usable |
| 2 | Set margin top = 0 | No top frame strip, H decreases by 0.04445m |
| 3 | Set margin bottom = 100mm | Bottom frame 100mm thick, H increases accordingly |
| 4 | Export STL | Single `{rackName}_frame` solid containing all shell faces |
| 5 | Frame watertight check | No gaps between outer and inner faces (shared vertices) |
| 6 | Partition between same-depth slots | Partition depth = full slot depth |
| 7 | Partition between different-depth slots | Partition depth = shallower slot |
| 8 | Partition BC | Always wall in backend |
| 9 | Load old project (safeMarginTopUnits=2) | Auto-migrated to safeMarginTop = 2×unitHeight |
| 10 | Mesh refinement | Rack surface level applied to `_frame` region |

## 10. Risks

- **Frame watertight complexity**: The front/back faces have rectangular openings (slot area). The geometry around the opening (margin strips + frame strips) must share exact vertices with the inner cavity walls. This requires careful vertex computation.
- **Partition depth with empty slots**: When a slot is empty (no equipment), it uses the rack-level offset. When occupied, it adds per-equipment offset. The partition must handle both cases.
- **Old solid name references**: Backend code that references old solid names (`_Left`, `_Right`, `_inner_left`, etc.) must be updated to use `_frame`. Search for all occurrences.
