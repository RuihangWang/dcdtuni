# Server Geometry Redesign — Watertight Body + Unified Face Patch System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade server (equipment) geometry from loose face fragments to a proper watertight 6-face closed body with the same face patch system used by Box/Baffle/Floor models. Replace P5's bespoke `outletPatch` with the general patch system.

**Architecture:** Extract `buildServerGeometry()` from `renderRackModel()`. Add `equipment.faces{}` dict with per-face `patches[]` array. Reuse `buildFaceIslands()` for patch rendering. Model tree gains Face→Patch hierarchy under each server. Backend reads new `faces` dict from cfd_export.json for BC registration.

**Tech Stack:** Three.js r128, vanilla JS (STL_generate.html), Python (stlgen_runner.py).

---

## Scope

| Feature | Description |
|---------|-------------|
| Watertight server geometry | 8 shared corner vertices, 6 faces from common vertex set |
| Unified face patch system | All 6 server faces support rect/circle patches (same as box) |
| P5 outletPatch replacement | `outletPatch` migrated to `faces.outlet.patches[0]`, old field deprecated |
| Model tree upgrade | Equipment → Face → Patch three-level hierarchy |
| Inspector UI | Face inspector with patch list + add/edit/delete (reuse box pattern) |
| STL export | Per-patch solid naming with wall remainder |
| CFD export | New `faces{}` dict in server export with backward-compat legacy fields |
| Backend BC | `_build_bridge_bc()` reads faces dict, registers per-patch BCs |

## Non-goals

- Extracting server STL into separate file (stays in `merged_rack.stl`)
- Making server an independent model type (stays as rack sub-object with U-slot positioning)
- Inlet face patches (geometry supported, but no special inlet patch UI in this phase)
- Per-patch physics editor for fan-server inlet/outlet patches (patches inherit face-level BC)

---

## 1. State & Data Model

### 1.1 Equipment State

Each equipment in `model.rack.equipments[]` gains a `faces` dict:

```javascript
equipment.faces = {
    inlet:  { patches: [], color: '#4488ff' },
    outlet: { patches: [], color: '#44ff88' },
    top:    { patches: [] },
    bottom: { patches: [] },
    left:   { patches: [] },
    right:  { patches: [] }
};
```

- `color` is optional — only inlet/outlet have user-settable colors. Other faces use a default wall color (`#888888`).
- `patches[]` uses the same structure as box face patches.

### 1.2 Patch Structure (identical to Box)

```javascript
// Rectangular patch
{
    id: 'uuid-xxx',
    type: 'rect',
    x: 0.2,    // left edge (0~1, fraction of face width)
    y: 0.1,    // bottom edge (0~1, fraction of face height)
    w: 0.6,    // width (0~1)
    h: 0.8,    // height (0~1)
    color: '#00cc66',
    applied: true
}

// Circular patch
{
    id: 'uuid-yyy',
    type: 'circle',
    cx: 0.5,   // center X (0~1)
    cy: 0.5,   // center Y (0~1)
    r: 0.3,    // radius (0~1, fraction of min(face width, face height))
    color: '#00cc66',
    applied: true
}
```

### 1.3 Deprecated Fields

These fields are deprecated and auto-migrated in `cloneRackState()`:

| Old field | Migrated to |
|-----------|-------------|
| `equipment.frontColor` | `equipment.faces.inlet.color` |
| `equipment.backColor` | `equipment.faces.outlet.color` |
| `equipment.outletPatch` | `equipment.faces.outlet.patches[0]` (converted to standard patch format) |

### 1.4 Backward Compatibility

In `cloneRackState()` / `cloneEquipment()`:

```javascript
// If equipment has no faces dict, create empty one
if (!equipment.faces) {
    equipment.faces = {
        inlet:  { patches: [], color: equipment.frontColor || '#4488ff' },
        outlet: { patches: [], color: equipment.backColor || '#44ff88' },
        top:    { patches: [] },
        bottom: { patches: [] },
        left:   { patches: [] },
        right:  { patches: [] }
    };

    // Migrate P5 outletPatch → standard patch
    if (equipment.outletPatch) {
        const op = equipment.outletPatch;
        const patch = {
            id: generateId(),
            type: op.type,
            color: '#00cc66',
            applied: true
        };
        if (op.type === 'rect') {
            patch.x = op.cx - op.width / 2;
            patch.y = op.cy - op.height / 2;
            patch.w = op.width;
            patch.h = op.height;
        } else {
            patch.cx = op.cx;
            patch.cy = op.cy;
            patch.r = op.radius;
        }
        equipment.faces.outlet.patches.push(patch);
    }

    // Clean up deprecated fields
    delete equipment.frontColor;
    delete equipment.backColor;
    delete equipment.outletPatch;
}
```

---

## 2. Geometry Builder

### 2.1 buildServerGeometry()

Extracted from `renderRackModel()` as an independent function:

```javascript
/**
 * Build watertight 6-face geometry for a single server (equipment).
 *
 * @param {Object} model       - The rack model
 * @param {Object} equipment   - The equipment object
 * @param {Object} serverBox   - { x0, x1, y0, y1, z0, z1 } bounding box
 * @param {Array}  renderMeshes - Output array, meshes appended here
 */
function buildServerGeometry(model, equipment, serverBox, renderMeshes) {
    const corners = computeServerCorners(serverBox);
    const faceIds = ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'];

    for (const faceId of faceIds) {
        const faceDef = equipment.faces?.[faceId] || { patches: [] };
        const faceRect = getServerFaceRect(corners, faceId);

        if (faceDef.patches?.length > 0) {
            // Has patches → reuse buildFaceIslands()
            renderServerFaceWithPatches(model, equipment, faceId, faceRect, faceDef, renderMeshes);
        } else {
            // No patches → single solid face
            renderServerSolidFace(model, equipment, faceId, faceRect, faceDef, renderMeshes);
        }
    }
}
```

### 2.2 Shared Corner Vertices (Watertight Guarantee)

```javascript
function computeServerCorners(box) {
    // Returns 8 vertices from { x0, x1, y0, y1, z0, z1 }
    // All 6 faces reference these same vertices → watertight
    return [
        [box.x0, box.y0, box.z0],  // 0: left-bottom-front
        [box.x1, box.y0, box.z0],  // 1: right-bottom-front
        [box.x1, box.y1, box.z0],  // 2: right-top-front
        [box.x0, box.y1, box.z0],  // 3: left-top-front
        [box.x0, box.y0, box.z1],  // 4: left-bottom-back
        [box.x1, box.y0, box.z1],  // 5: right-bottom-back
        [box.x1, box.y1, box.z1],  // 6: right-top-back
        [box.x0, box.y1, box.z1],  // 7: left-top-back
    ];
}
```

### 2.3 Face Rect Definition

Each face is defined by an origin point, two edge vectors (u, v), and its 2D dimensions (w, h):

```javascript
function getServerFaceRect(corners, faceId) {
    // Returns { origin, u, v, normal, w, h }
    // origin: bottom-left corner of the face
    // u: horizontal direction vector (face-local X)
    // v: vertical direction vector (face-local Y)
    // normal: outward-pointing normal
    // w, h: face width and height in meters
    // ...mapping for each faceId
}
```

Face normal directions follow the same convention as box models (outward normals toward fluid domain).

### 2.4 renderRackModel() Changes

The existing fan-server and non-fan equipment rendering code blocks (~lines 6590–6829) are replaced with a single call per equipment:

```javascript
// Inside renderRackModel(), for each equipment:
const serverBox = computeServerBoundingBox(model, equipment, rackGeom);
buildServerGeometry(model, equipment, serverBox, renderMeshes);
```

The rack shell, doors, blind plates, and leakage zone rendering remain in `renderRackModel()`.

---

## 3. Model Tree

### 3.1 Tree Structure

```
📦 Rack_1
├── ◻ front (rack shell face)
├── ◻ back (rack shell face)
├── ...
├── 🧰 server_1  (2U fanServer)
│   ├── ◻ inlet
│   │   ├── ■ Patch 1  (rect 60×80%)
│   │   └── ■ Patch 2  (circle r=30%)
│   ├── ◻ outlet
│   ├── ◻ top
│   ├── ◻ bottom
│   ├── ◻ left
│   └── ◻ right
├── 🧰 server_2  (1U nonFan)
│   ├── ◻ inlet
│   ├── ◻ outlet
│   └── ...
```

### 3.2 Selection

- Clicking a server face in the tree or 3D viewport selects that face
- Clicking a patch in the tree selects that patch (highlights in 3D)
- Each mesh has `userData`:
  ```javascript
  {
      modelId: model.id,
      isRackEquipment: true,
      equipmentId: equipment.id,
      faceId: 'inlet',        // which face
      isPatch: false,          // false = face/wall, true = patch
      patchId: null,           // patch UUID when isPatch=true
      exportName: '...'        // STL solid name
  }
  ```

---

## 4. Inspector UI

### 4.1 Server Face Selected

When the user selects a server face (e.g., clicking on outlet in 3D or tree):

```
─── Server Face: server_1 → outlet ───
面颜色: [color picker] #44ff88

─── Patches ───
  ■ Patch 1  rect 60×80%  [✕]
  ■ Patch 2  circle r=30% [✕]
  [+ 添加 Patch]

─── Patch 编辑 (选中 Patch 1) ───
类型: [rect ▼] [circle ▼]
X 位置: [input] [slider] 20%
Y 位置: [input] [slider] 10%
宽度:   [input] [slider] 60%
高度:   [input] [slider] 80%
颜色:   [color picker] #00cc66
```

This uses the same layout and interaction pattern as the box face patch editor.

### 4.2 Slider Behavior

- Sliders call `doRebuildRackDetail()` (NOT `rebuildGeometry()`) to avoid DOM rebuild during drag
- Checkpoint on first drag event only (flag pattern)
- Input boxes sync with slider values during drag

---

## 5. STL Export

### 5.1 Solid Naming

| Scenario | Solid name | Example |
|----------|-----------|---------|
| Face without patches (fan-server inlet) | `{stem}_inlet_patch` | `Rack_1_srv_1_inlet_patch` |
| Face without patches (fan-server outlet) | `{stem}_outlet_patch` | `Rack_1_srv_1_outlet_patch` |
| Face without patches (non-fan front) | `{stem}_front` | `Rack_1_srv_1_front` |
| Face without patches (common) | `{stem}_{faceId}` | `Rack_1_srv_1_top` |
| Patch solid | `{stem}_{faceId}_p{index}` | `Rack_1_srv_1_outlet_p0` |
| Wall remainder | `{stem}_{faceId}_wall` | `Rack_1_srv_1_outlet_wall` |

When a face has no patches, the naming is identical to the current scheme (backward compatible). When patches are added, the face splits into `_p{N}` patch solids and a `_wall` remainder solid.

### 5.2 Export Flow

All server solids remain in `merged_rack.stl`. The export function groups meshes by `userData.exportName` and writes each group as a `solid ... endsolid` block, same as current behavior.

---

## 6. CFD Export (buildCfdExportJson)

### 6.1 Server Export Object

```json
{
    "id": "Rack_1_srv_1",
    "type": "fanServer",
    "startSlot": 0,
    "spanSlots": 2,

    "inletSolidName": "Rack_1_srv_1_inlet_patch",
    "outletSolidName": null,

    "faces": {
        "inlet": {
            "solid": "Rack_1_srv_1_inlet_patch",
            "patches": [],
            "wallSolid": null
        },
        "outlet": {
            "solid": null,
            "patches": [
                { "solid": "Rack_1_srv_1_outlet_p0", "patchId": "uuid-xxx" }
            ],
            "wallSolid": "Rack_1_srv_1_outlet_wall"
        },
        "top":    { "solid": "Rack_1_srv_1_top",    "patches": [], "wallSolid": null },
        "bottom": { "solid": "Rack_1_srv_1_bottom", "patches": [], "wallSolid": null },
        "left":   { "solid": "Rack_1_srv_1_left",   "patches": [], "wallSolid": null },
        "right":  { "solid": "Rack_1_srv_1_right",  "patches": [], "wallSolid": null }
    },

    "physics": {
        "volumeFlowRate": 0.5,
        "power": 1000
    },
    "extraInletOffset": 0,
    "extraOutletOffset": 0,
    "liquidCooling": null
}
```

**Rules:**
- `faces[faceId].solid` = whole-face solid name when no patches (null when subdivided)
- `faces[faceId].patches[]` = array of `{ solid, patchId }` when patches exist
- `faces[faceId].wallSolid` = wall remainder solid name when patches exist (null otherwise)
- Legacy fields `inletSolidName`/`outletSolidName` preserved: set to whole-face solid name when no patches on that face, `null` when face is subdivided. Allows old backend to still work for simple cases.

---

## 7. Backend (stlgen_runner.py)

### 7.1 BC Registration

In `_build_bridge_bc()`, server section rewritten:

```python
def _resolve_server_face_bc(srv: dict, face_id: str) -> str:
    """Determine BC type for a server face."""
    srv_type = srv.get("type", "fanServer")
    if srv_type == "fanServer":
        if face_id == "inlet":
            return "fan_inlet"
        elif face_id == "outlet":
            return "fan_outlet"
        else:
            return "wall"
    else:  # nonFan
        phys = srv.get("physics", {})
        if face_id in ("inlet", "outlet"):
            face_phys = phys.get(face_id, {})
            bc = face_phys.get("bcType", "nonfan_fixed_temp")
            return bc
        else:
            return "wall"
```

Registration loop:

```python
faces = srv.get("faces")
if faces:
    for face_id, face_info in faces.items():
        bc_type = _resolve_server_face_bc(srv, face_id)
        bc_kwargs = _resolve_server_bc_kwargs(srv, face_id, bc_type)

        if face_info.get("patches"):
            for p in face_info["patches"]:
                registry.register(_sanitize(p["solid"]),
                    BCSpec(bc_type=bc_type, **bc_kwargs))
            if face_info.get("wallSolid"):
                registry.register(_sanitize(face_info["wallSolid"]),
                    BCSpec(bc_type="wall"))
        elif face_info.get("solid"):
            registry.register(_sanitize(face_info["solid"]),
                BCSpec(bc_type=bc_type, **bc_kwargs))
else:
    # Legacy fallback: read inletSolidName, outletSolidName, etc.
    _register_server_legacy(registry, srv)
```

### 7.2 snappyHexMesh

No structural changes. New solid names (e.g., `_outlet_p0`, `_outlet_wall`) are automatically picked up from `merged_rack.stl` region enumeration. The refinement level logic uses `solid_to_span` mapping which continues to work — new patch/wall solids get the same refinement as their parent face.

---

## 8. File Changes

| File | Change |
|------|--------|
| `STL_generate.html` | Equipment state (`faces{}`), `buildServerGeometry()`, model tree face→patch hierarchy, inspector patch UI, STL export naming, cfd_export faces dict, P5 migration |
| `stlgen_runner.py` | `_build_bridge_bc()` faces-dict reader, `_resolve_server_face_bc()`, legacy fallback |

## 9. Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Server with no patches (default) | Identical to current behavior, same solid names |
| 2 | Add rect patch to outlet face | Green patch visible, grey wall remainder, two solids in STL |
| 3 | Add circle patch to inlet face | Circular patch, wall remainder |
| 4 | Multiple patches on one face | All patches rendered, proper island merging |
| 5 | Patches on top/bottom/left/right | Visual only (wall BC for fan-server) |
| 6 | Model tree shows Face → Patch hierarchy | 6 faces under equipment, patches under faces |
| 7 | Select patch in tree → highlights in 3D | Correct mesh highlighted |
| 8 | STL export with patches | `_p0`, `_p1`, `_wall` solids present |
| 9 | cfd_export.json has faces dict | Per-face solid info with patch/wall breakdown |
| 10 | Load old project (P5 outletPatch) | Auto-migrated to `faces.outlet.patches[0]`, no errors |
| 11 | Load old project (no faces field) | Empty faces created, backward compatible |
| 12 | Backend reads faces dict | Correct BC registered per patch solid |
| 13 | Backend reads old format (no faces) | Legacy fallback works |
| 14 | Watertight verification | Adjacent face edges share exact vertices |

## 10. Risks

- **buildFaceIslands() assumptions**: Currently used for box/baffle/floor which are flat models. Server faces are also flat (planar quads), so this should work directly. Verify that the coordinate transform from face-local 2D to 3D works correctly for all 6 face orientations.
- **Model tree depth**: Three levels (equipment → face → patch) adds nesting. Keep faces collapsed by default to avoid clutter.
- **P5 outletPatch migration**: The P5 `outletPatch` uses `{cx, cy, width, height}` (center-based) while standard patches use `{x, y, w, h}` (corner-based). Migration must convert coordinates correctly.
