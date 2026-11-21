# Rack Detail P3 — Perforated Door Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add optional perforated doors to rack inlet/outlet faces with hexagonal visual pattern, click-through rendering, and porousBafflePressure boundary conditions via the createBaffles CFD pipeline.

**Architecture:** Each rack face (inlet/outlet) optionally has a door object stored in `model.rack.inletDoor` / `outletDoor`. The door is a flat STL solid at the rack outer face, rendered with a semi-transparent hexagonal shader, exported as an internal face for snappyHexMesh → createBaffles → porousBafflePressure BC.

**Tech Stack:** Three.js r128 (ShaderMaterial for hex pattern), vanilla JS (STL_generate.html), Python (stlgen_runner.py, fields.py).

---

## Scope

Phase 3 of the 4-phase Rack Detail Editor. Includes P4 (leakage interaction polish).

| Phase | Status |
|-------|--------|
| P1 View mode shell | Done |
| P2 Inner width + depth | Done |
| **P3 Perforated door + P4 leakage interaction** | **This spec** |

## Non-goals

- Geometric hex holes in the mesh (CFD uses uniform porousBafflePressure, not geometric perforations).
- Per-equipment doors (one door per face, not per equipment).
- Door animation/opening.

---

## 1. State Properties

```javascript
model.rack.inletDoor = null;   // null = no door
model.rack.outletDoor = null;  // { porosity: 0.5 } = door with 50% open area
```

- `null` = no door (default, backward compatible)
- `{ porosity: N }` where N ∈ (0, 1]:
  - N close to 0 = nearly closed (high resistance)
  - N = 1.0 = fully open (pure cyclic, zero resistance)

**Validation in `syncRackModelState`:** If door exists, clamp porosity to [0.01, 1.0].

**In `cloneRackState`:** Deep-clone door objects if present, preserve `null` if absent.

## 2. UI Controls

### 2.1 Rack Detail Panel (right panel, replaces P1 placeholder)

Replace the `#rack-detail-door` placeholder with:

```
─── 穿孔门 / Perforated Door ───

Inlet 门:
  ☐ 启用 / Enable
  [展开后: 漏风率 input (1-100%) + slider]

Outlet 门:
  ☐ 启用 / Enable
  [展开后: 漏风率 input (1-100%) + slider]
```

- Checkbox toggles `inletDoor` between `null` and `{ porosity: 0.5 }` (default 50%)
- When enabled, porosity input (1-100%) + slider appear
- Changes trigger `rebuildGeometry()` for real-time preview

### 2.2 Main 3D Inspector (optional, lower priority)

When user selects a rack in normal 3D mode and clicks the inlet/outlet face:
- If no door exists: show "添加穿孔门" button
- If door exists: show porosity controls + "删除穿孔门" button

This is a convenience shortcut — the primary UI is in the Rack Detail panel (§2.1).

## 3. 3D Rendering

### 3.1 Hexagonal Pattern Material

Procedural hex pattern via GLSL fragment shader:

```glsl
// Fragment shader: hex grid pattern
uniform float uOpacity;
uniform vec3 uColor;
varying vec2 vUv;

// Hex grid function
float hexDist(vec2 p) {
    p = abs(p);
    return max(dot(p, vec2(1.0, 1.732) * 0.5), p.x);
}

void main() {
    vec2 uv = vUv * vec2(12.0, 20.0); // tile density
    // Hex grid tiling
    vec2 g = vec2(uv.x, uv.y / 1.732);
    vec2 id = floor(g);
    vec2 o = vec2(mod(id.y, 2.0) * 0.5, 0.0);
    vec2 f = fract(g + o) - 0.5;
    float d = hexDist(f);
    float hole = smoothstep(0.35, 0.38, d); // hex hole edge
    float frame = 1.0 - hole;
    gl_FragColor = vec4(uColor, frame * uOpacity);
}
```

Material properties:
- `transparent: true`
- `opacity: 0.35` (semi-transparent frame, holes fully transparent)
- `depthWrite: false`
- `side: THREE.DoubleSide`

### 3.2 Click-Through Rendering

```javascript
doorMesh.layers.set(2);  // raycaster only checks layer 0
doorMesh.renderOrder = 50;  // between rack walls (0) and equipment (default)
```

User clicks "through" the door and selects the U-slot/equipment behind it. The door is purely visual.

### 3.3 Rendering Scope

- Door meshes are rendered in **Rack Detail view** (always, when door is enabled)
- Door meshes are also rendered in **normal 3D view** (so user sees the door exists)
- In normal 3D, the door uses the same click-through rendering (layers.set(2))

### 3.4 Door Position and Size

```javascript
// Inlet door: on rack front face (Y=0), covering the inner channel
origin = (slotX, 0, safeBottomHeight)
width = slotWidth  // inner channel width
height = centralFrameHeight  // from safe bottom to safe top
normal = (0, -1, 0)  // facing outward (toward room)

// Outlet door: on rack back face (Y=D)
origin = (slotX, model.base.D, safeBottomHeight)
normal = (0, 1, 0)  // facing outward
```

Note: Door position is ALWAYS at the rack outer face, regardless of P2 depth offsets. The cavity space is between the door and the equipment.

## 4. STL Export

### 4.1 Door Solid Names

```
{rackName}_inlet_door
{rackName}_outlet_door
```

### 4.2 Export Geometry

Flat quad (2 triangles) at the door position. Same vertex coords as the rendering mesh.

### 4.3 Export Group

Add door solids to the rack's STL file (`merged_rack.stl`), alongside existing rack geometry.

## 5. cfd_export.json

```json
"racks": {
    "Rack_1": {
        "geometry": { ... },
        "servers": [ ... ],
        "inletDoor": {
            "enabled": true,
            "porosity": 0.5,
            "solidName": "Rack_1_inlet_door"
        },
        "outletDoor": {
            "enabled": true,
            "porosity": 0.7,
            "solidName": "Rack_1_outlet_door"
        },
        ...
    }
}
```

## 6. Backend: CFD Pipeline

### 6.1 createBafflesDict

In `stlgen_runner.py`, when generating `system/createBafflesDict`, add entries for each door faceZone:

```
{rackName}_inlet_door
{
    type    faceZone;
    selectionType   faceZone;
    faceZoneName    {rackName}_inlet_door;
    patchPairs
    (
        {
            master  {rackName}_inlet_door_master;
            slave   {rackName}_inlet_door_slave;
        }
    );
}
```

This is the same pattern used for existing perforated baffles.

### 6.2 Bridge BC Registry

In `_build_bridge_bc()`:

```python
for side in ("inlet", "outlet"):
    door_key = f"{side}Door"
    door = rack_data.get(door_key, {})
    if door.get("enabled"):
        solid = sanitize(door.get("solidName", f"{rack_name}_{side}_door"))
        porosity = float(door.get("porosity", 0.5))
        registry.register(f"{solid}_master",
                         BCSpec(bc_type="perforated_baffle", sigma=porosity))
        registry.register(f"{solid}_slave",
                         BCSpec(bc_type="perforated_baffle", sigma=porosity))
```

### 6.3 Field Generation (fields.py)

Existing `perforated_baffle` handling already covers this:
- `p_rgh`: `porousBafflePressure` with `D = (1 - σ²) / (2σ²)`, `I = 0`
- `U`: `cyclic`
- `T`: `cyclic`
- When σ ≥ 1.0: pure `cyclic` (no resistance)

No changes needed in fields.py.

### 6.4 snappyHexMeshDict

The door solid needs to be listed in `snappyHexMeshDict` as a surface with `faceZone` enabled. This is already the pattern for baffle solids — search for how existing baffles are added to `surfaceFeatureExtractDict` and `castellatedMeshControls.features`.

In `stlgen_runner.py`, the door solid should be treated as a baffle surface:
- Added to `refinementSurfaces` with `faceZone` type
- Added to `surfaceFeatureExtractDict` for edge snapping

## 7. P4: Leakage Zone Interaction

### 7.1 Leakage porosity = 100% → no source term

In `_build_leakage_fvoptions_config()`, when computing DarcyForchheimer coefficients:

```python
if sigma >= 1.0:
    # Skip this leakage zone entirely — no fvOptions entry needed
    continue
```

This means a leakage zone with σ=1.0 behaves as if it doesn't exist (pure open cavity).

### 7.2 Door + Leakage Coexistence

When a rack has BOTH a perforated door AND leakage zones:
- The door's `porousBafflePressure` BC handles the surface resistance
- The leakage zone's `explicitPorositySource` handles the volume resistance
- Both can coexist — they operate on different CFD mechanisms (surface BC vs volume source)
- If the user wants ONLY the door to provide resistance, they set the leakage σ to 1.0 (which disables the volume source per §7.1)

## 8. File Changes

| File | Change |
|------|--------|
| `STL_generate.html` | State, validation, UI controls, hex shader material, door rendering, click-through, STL export, cfd_export |
| `stlgen_runner.py` | createBafflesDict entries for doors, bridge BC registration, leakage σ=1.0 skip |
| `fields.py` | No changes (existing perforated_baffle handling) |
| `third_parties/foam/template/` | May need createBafflesDict template update |

## 9. Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Enable inlet door at 50% porosity | Hex pattern mesh appears on inlet face |
| 2 | Click on door mesh in viewport | Click passes through to U-slot behind |
| 3 | Disable door | Hex mesh disappears |
| 4 | Export STL with door | `{rackName}_inlet_door` solid in export |
| 5 | cfd_export includes door data | `inletDoor.enabled`, `porosity`, `solidName` |
| 6 | Run mesh with door | createBaffles creates master/slave pair |
| 7 | Run solve with door | porousBafflePressure BC on p_rgh |
| 8 | Porosity = 100% | Pure cyclic BC (no resistance) |
| 9 | Leakage σ = 100% | No fvOptions source term |
| 10 | Door + leakage zone coexist | Both BC types applied independently |
| 11 | Load old project (no door fields) | Defaults to null, no door |

## 10. Risks

- **Hex shader performance**: Fragment shader runs per-pixel. For a single flat quad per door, this is negligible.
- **createBafflesDict complexity**: The template may need refactoring to support dynamic baffle entries. Existing baffle handling should serve as a reference.
- **faceZone naming**: snappyHexMesh auto-names faceZones from STL solid names. Must ensure `{rackName}_inlet_door` matches exactly between STL export and createBafflesDict.
