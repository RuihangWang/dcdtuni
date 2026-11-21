# Thick Partition Wall Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a new `partitionWall` object type to STLGeneratorForOpenFoam and the direct STL CFD pipeline. A partition wall is a real 3D wall with non-zero thickness, free 3D transform, through-hole patches on its front/back large faces, automatic dual-side mesh-retention seeds, and optional Darcy-Forchheimer resistance inside each through-hole thickness volume.

**Architecture:** Introduce a dedicated `partitionWall` model kind instead of overloading `baffle`, `wall`, or `floor`. The visible wall geometry is exported as `merged_partition_wall.stl`. Each resistance-enabled through-hole additionally exports a hidden helper STL `partition_hole_<zone>.stl` so snappyHexMesh can create a dedicated cellZone, exactly like rack leakage zones. The wall body remains a watertight solid obstacle with no mesh inside. The hole channel remains fluid and may receive a volumetric source term.

**Tech Stack:** Three.js r128, vanilla JS (`STLGeneratorForOpenFoam/web/STL_generate.html`), Python (`src/backend/aircooling/runner/cfdproj_schema.py`, `src/backend/aircooling/runner/stlgen_runner.py`), existing OpenFOAM templates.

---

## Scope

| Feature | Description |
|---------|-------------|
| New model kind | `partitionWall` appears beside wall/baffle/floor in the editor |
| Non-zero thickness | Default `100 mm`, forbidden to be `<= 0` |
| Free 3D transform | Default vertical on creation, later supports yaw/pitch/roll |
| Shared front/back holes | Rectangular and circular through-holes only; front/back do not have independent patch sets |
| Dual-side retention seeds | Automatic `locationsInMesh` seeds on both sides of every partition wall |
| Optional hole resistance | Per-hole toggle + `leakageRate` 0–100% mapped to Darcy-Forchheimer source in hole thickness volume |
| Direct STL pipeline support | `cfd_export.json`, helper STLs, snappyHexMesh, fvOptions |

## Non-goals

- Replacing existing zero-thickness `baffle` / `floor` / room-wall workflows.
- Arbitrary polygon hole shapes in this phase.
- Allowing independent front-face and back-face patch definitions.
- Turning partition-wall holes into `createBaffles` / `porousBafflePressure` zero-thickness boundaries.
- Auto-solving every possible disconnected-room seed topology; existing manual `extraLocationsInMesh` remains the fallback.

---

## Frozen Decisions

These were explicitly chosen during brainstorming and should be treated as spec, not open questions:

- The object is a **new type**, not a thickened `baffle`.
- The wall is a **true 3D solid with thickness**. Wall body interior must not be meshed.
- The object is **fully free in 3D** after creation; new instances start vertical.
- Only **rectangular** and **circular** through-holes are supported in this phase.
- A patch means a **through-hole**. The user does not define separate front/back patches.
- Resistance is split from geometry:
  - Patch exists = geometry is open.
  - Resistance toggle decides whether the hole thickness volume gets a source term.
- `leakageRate = 0%` means **maximum resistance**, not “no source”.
- `leakageRate = 100%` means **zero resistance**.
- Automatic retention seeds are not user-configurable in this phase.
- Export uses one main merged STL: `merged_partition_wall.stl`.

---

## Alternative Approaches Considered

### Approach A — Overload `baffle` with thickness

This looks attractive because drag behavior and patch UI already exist, but it is the wrong abstraction. Current `baffle` semantics are based on zero-thickness internal faces, `createBaffles`, and optional `porousBafflePressure`. A thick wall is a real solid obstacle with a fluid channel cut through it. Reusing `baffle` would blur two physically different behaviors and make both export and backend logic harder to reason about.

**Rejected.**

### Approach B — Reuse `box` or `wall` and bolt on custom patch logic

This minimizes model-kind count but produces a poor fit:

- `wall` currently assumes `D=0`.
- `box` patch semantics are generic surface patches, not shared front/back through-holes.
- Automatic dual-side retention seeds and hole cellZone export do not belong on generic boxes.

This would create many special cases inside an object type that already has other meanings.

**Rejected.**

### Approach C — New dedicated `partitionWall` kind with helper hole-zone STLs

This keeps geometry semantics, patch semantics, and CFD semantics aligned:

- visible geometry is a thick wall obstacle,
- holes are first-class through-features,
- resistance-enabled holes get dedicated helper STLs and fvOptions,
- mesh-retention seeds are automatic and object-specific.

**Chosen.**

---

## 1. Object Model

### 1.1 Core Semantics

`partitionWall` is a thin rectangular cuboid defined in local box axes:

- local `W` = wall width
- local `D` = wall thickness
- local `H` = wall height
- local `front/back` = the two large faces whose normals point along the local thickness axis

The object starts vertical when created, but uses the existing quaternion transform system after creation. The local face identity remains stable even after arbitrary 3D rotation.

### 1.2 Default Size

```javascript
base: {
    W: 1.0,   // meters
    D: 0.1,   // 100 mm default thickness
    H: 3.0
}
```

Validation:

```javascript
W > 0
D > 0
H > 0
```

`D <= 0` is invalid and must be clamped back to the previous valid value or to `0.1`.

### 1.3 New Model Factory

Add:

```javascript
function createPartitionWallModel(name = 'Partition_Wall') { ... }
```

The new model follows the same high-level shape as `createBaffleModel()` and `createWallModel()`, but with non-zero `base.D`.

### 1.4 CFD State

Add a dedicated CFD role:

```javascript
case 'partitionWall':
    return {
        role: 'partitionWall',
        patchPhysics: {}
    };
```

Per-hole CFD state is keyed by `patch.id`:

```javascript
model.cfd.patchPhysics[patchId] = {
    resistanceEnabled: false,
    leakageRate: 100
};
```

Semantics:

- `resistanceEnabled = false` → open hole, no source term
- `resistanceEnabled = true` + `leakageRate = 0` → open hole, maximum resistance
- `resistanceEnabled = true` + `0 < leakageRate < 100` → open hole, Darcy-Forchheimer source
- `resistanceEnabled = true` + `leakageRate = 100` → open hole, zero resistance

If the user wants a sealed wall, they should **delete the patch**, not set `leakageRate = 0`.

---

## 2. Editing Model and UI

### 2.1 Creation Workflow

Partition-wall creation should feel like `baffle` creation:

- appears as a separate create button and tree category
- drag-create on the current construction plane creates a vertical wall
- drag length defines `W`
- `H` defaults to room height when available, otherwise `3.0 m`
- `D` defaults to `0.1 m`

After creation, the existing transform controls allow arbitrary translation, yaw, pitch, and roll.

### 2.2 Editable Faces

Only local `front` and `back` are patchable. The other four faces are always ordinary wall surfaces.

Because front and back must share one through-hole definition, the implementation should use **one authoritative patch set** and mirror it visually:

- selecting either face opens the same hole list
- adding/editing/removing a patch from either face edits the same underlying hole
- the opposite face preview updates immediately

The simplest implementation is to treat `front` as the authoritative patch storage and route `back` face editing through that same list.

### 2.3 Hole Shapes

Phase 1 supports:

- rectangle
- circle

Each hole cuts fully through the wall thickness.

### 2.4 Hole Validation

For every hole:

- it must fit fully inside the front/back face bounds
- it must not extend outside the face after unit conversion
- holes may not overlap in face-local UV space

Overlap is explicitly forbidden in this phase because overlapping hole volumes make resistance metadata ambiguous.

### 2.5 Inspector

The model inspector must add:

```text
Partition Wall
- Width
- Height
- Thickness
- Anchor
- Yaw / Pitch / Roll
```

The patch inspector must add:

```text
Through-Hole CFD
- Apply Resistance [checkbox]
- Leakage Rate (%) [0..100]
- Help text:
  0%   = maximum resistance
  100% = zero resistance
  Delete patch if you want a sealed wall
```

`Thickness` is shown in the normal dimension section, not inside CFD settings.

### 2.6 Visual Rendering

The viewport should render:

- the full wall body as a solid thin cuboid
- holes as real cut-through voids
- the mirrored hole on both front and back
- optional highlight color for selected holes, matching existing patch selection behavior

The easiest implementation path is to reuse the existing boolean-subtraction pipeline already used for cavity-like patch operations, with one subtractor prism/cylinder per through-hole.

---

## 3. Geometry Export

### 3.1 Main STL

Add a new merged export group:

```text
merged_partition_wall.stl
```

This file contains every visible partition-wall obstacle surface:

- front-face remainder
- back-face remainder
- the four outer side faces
- tunnel side walls for every through-hole

The file is a normal obstacle STL, not a faceZone helper.

### 3.2 Solid Naming

Use stable, per-model, per-region names, for example:

```text
Partition_1_front_wall
Partition_1_back_wall
Partition_1_left_edge
Partition_1_right_edge
Partition_1_top_edge
Partition_1_bottom_edge
Partition_1_hole_1_tunnel
Partition_1_hole_2_tunnel
```

Exact suffix names can differ, but they must remain deterministic and sanitized.

All of these solids receive wall BCs.

### 3.3 Helper Hole-Zone STLs

If a hole has `resistanceEnabled = true` and `leakageRate < 100`, export an additional hidden helper STL:

```text
partition_hole_<zone>.stl
```

Rules:

- one STL file per resistance-enabled hole
- STL `solid` name must exactly equal the surface name
- the helper surface must be a closed prism/cylinder matching the fluid hole volume
- these files are not user-visible geometry; they exist only for snappy cellZone extraction

This mirrors the proven rack leakage design:

- single-zone helper surface
- single named seed point
- fvOptions source on a cellZone

### 3.4 Why Not Use `porousBafflePressure`

`porousBafflePressure` is the correct tool for zero-thickness baffles. A partition-wall hole is not zero-thickness. It is a finite channel through a solid body. The physically consistent model is therefore:

- hole exists as a true fluid passage
- optional resistance is applied as a **volumetric Darcy-Forchheimer source** in that passage

So the partition-wall feature reuses the **user-facing 0–100 semantics**, but not the zero-thickness baffle BC implementation.

---

## 4. CFD Export Schema

### 4.1 New Top-Level Section

Add:

```json
"partitionWalls": {
  "Partition_1": { ... }
}
```

### 4.2 Entry Shape

```json
{
  "stlFile": "merged_partition_wall.stl",
  "geometry": {
    "anchor": { "x": 1.0, "y": 2.0, "z": 0.0 },
    "size": { "W": 2.4, "D": 0.1, "H": 3.0 },
    "orientation": 90.0,
    "transformQuaternion": { "x": 0, "y": 0, "z": 0, "w": 1 }
  },
  "wallSolidNames": [
    "Partition_1_front_wall",
    "Partition_1_back_wall",
    "Partition_1_left_edge",
    "Partition_1_right_edge",
    "Partition_1_top_edge",
    "Partition_1_bottom_edge",
    "Partition_1_hole_1_tunnel"
  ],
  "holes": [
    {
      "patchId": "abc123",
      "shape": "rectangle",
      "name": "hole_1",
      "zoneName": "Partition_1_hole_1_zone",
      "zoneStlFile": "partition_hole_Partition_1_hole_1_zone.stl",
      "physics": {
        "resistanceEnabled": true,
        "leakageRate": 40.0
      }
    }
  ]
}
```

Notes:

- `orientation` stays for backward compatibility and quick summaries.
- `transformQuaternion` is added because scalar yaw-only orientation is not enough for this object.
- `zoneStlFile` is absent or `null` when `resistanceEnabled = false` or `leakageRate >= 100`.

### 4.3 Schema Changes

In `cfdproj_schema.py`, add:

- `Quaternion4`
- `PartitionHolePhysics`
- `PartitionHole`
- `CFDExportPartitionWall`
- root field `partitionWalls: Dict[str, CFDExportPartitionWall]`

Also extend `SimpleGeometry` or add a partition-wall-specific geometry model so quaternion export is valid.

---

## 5. Backend Mesh and Seed Behavior

### 5.1 Mesh Category

Partition walls are obstacle surfaces, not internal baffles. They therefore belong in the normal snappy geometry / refinement flow, alongside racks, ACUs, boxes, and room walls.

Add a new refinement bucket:

```json
partitionWallRefinementLevel
partitionWallFeatureLevel
```

Default them close to `boxRefinementLevel`, but allow them to be tuned independently because partition walls are intentionally thin.

### 5.2 Automatic Dual-Side Fluid Seeds

For every partition wall, always append two `locationsInMesh` entries with zone name `none`.

These seeds are computed from the partition wall local frame, not from triangulated face centroids:

- front seed = front-face center offset outward by `2 mm`
- back seed = back-face center offset outward by `2 mm`

Formally:

```text
frontCenter = local(W/2, 0,   H/2)
backCenter  = local(W/2, D,   H/2)
frontSeed   = frontCenter - n_thickness * 0.002
backSeed    = backCenter  + n_thickness * 0.002
```

then transformed to world space by anchor + quaternion.

This is more robust than deriving seed positions from STL triangles because a large central hole can remove the face centroid from the remaining wall surface.

### 5.3 Seed Limitations

The automatic pair-per-wall rule is the baseline, not a full disconnected-domain solver. If multiple partition walls create more fluid compartments than these seeds cover, the existing manual `room.extraLocationsInMesh` remains the explicit fallback.

### 5.4 Hole CellZone Seeds

For each helper hole-zone STL, add a named `locationsInMesh` entry at the zone centroid:

```text
((x y z) Partition_1_hole_1_zone)
```

This follows the same pattern already used for rack leakage cellZones.

---

## 6. Resistance Mapping

### 6.1 User-Facing Semantics

The patch UI exposes:

```text
Apply Resistance [checkbox]
Leakage Rate [%]
```

Mapping:

- resistance disabled → no helper zone, no fvOptions source
- enabled + `0%` → maximum resistance
- enabled + `100%` → zero resistance
- enabled + `0% < x < 100%` → interpolate through the existing rack-style Darcy-Forchheimer mapping

### 6.2 Formula Choice

Partition-wall holes use the **rack leakage** volumetric-source formula, not the zero-thickness `porousBafflePressure` coefficient:

```text
f = (1/L) * ((1/(Cd*sigma)) - 1)^2
```

Where:

- `L = wall thickness`
- `Cd = 0.6`
- `sigma = leakageRate / 100`

This is already implemented in:

```python
_darcy_forchheimer(sigma, depth)
```

### 6.3 Edge Cases

Apply the following behavior:

- if `resistanceEnabled = false`: do not create a source
- if `leakageRate >= 100`: do not create a source
- if `leakageRate <= 0`: use the existing finite clamp path (`sigma -> max(0.01, ...)`) so the resistance is extremely large but not infinite

This keeps the implementation aligned with current rack leakage behavior and avoids singular coefficients.

### 6.4 fvOptions

Partition-wall hole zones should be appended to the same `fvOptions` generation path already used for rack leakage zones. The template does not need a new OpenFOAM model; it needs a new source-data producer.

Each enabled hole contributes:

```json
{
  "cellzone_name": "Partition_1_hole_1_zone",
  "sigma": 0.40,
  "d": 0.0,
  "f": ...,
  "origin": "(...)",
  "e1": "(...)",
  "e2": "(...)"
}
```

The hole local axis should align with the wall thickness direction so the resistance is applied across the tunnel, not across an unrelated world axis.

---

## 7. Boundary Conditions

Partition walls do not need a new boundary BC type.

### 7.1 Wall Surfaces

All partition-wall visible solids register as:

```python
BCSpec(bc_type="wall")
```

This includes:

- front/back remainders
- outer edge faces
- hole tunnel side walls

### 7.2 Hole Openings

The hole openings themselves are not boundary patches. They are simply missing obstacle surfaces, so the two rooms connect through a true 3D fluid passage.

### 7.3 Resistance Zones

Hole resistance is not represented in `BridgeBCRegistry`. It is represented in `fvOptions` only.

---

## 8. Export and Backend Data Flow

```text
Editor partitionWall model
    -> visible wall mesh with through-holes
    -> merged_partition_wall.stl
    -> optional partition_hole_<zone>.stl per resistance-enabled hole
    -> cfd_export.json.partitionWalls[]
    -> STLGenRunner:
         - copy main/helper STLs
         - register wall solids as wall BCs
         - add 2 none-seeds per wall
         - add cellZone seeds per enabled hole
         - include helper STLs in snappy geometry
         - generate fvOptions entries for enabled holes
```

---

## 9. Validation and Error Handling

### 9.1 Frontend Validation

- thickness must remain `> 0`
- hole shapes must stay inside bounds
- hole shapes may not overlap
- if a hole is deleted, its `patchPhysics` entry must be removed
- if resistance is disabled, `leakageRate` may remain stored but is ignored

### 9.2 Export Validation

During export:

- skip helper STL generation for holes without resistance
- skip helper STL generation for `leakageRate >= 100`
- ensure every helper STL file contains exactly one closed solid with matching name
- ensure wall solid names are sanitized and unique

### 9.3 Backend Validation

During import:

- reject malformed partition-wall hole entries missing `zoneName` when `zoneStlFile` is present
- warn and skip any hole helper STL missing on disk
- warn and skip any helper STL whose centroid cannot be computed
- continue meshing if one hole source is skipped; do not fail the whole case unless the main partition-wall STL is missing

---

## 10. File Changes

| File | Change |
|------|--------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | new model kind, creation, inspector, render, export groups, `cfd_export.json` serialization |
| `src/backend/aircooling/runner/cfdproj_schema.py` | schema for `partitionWalls`, hole physics, optional quaternion |
| `src/backend/aircooling/runner/stlgen_runner.py` | copy helper STLs, wall BC registration, fluid seeds, hole cellZone seeds, snappy geometry, fvOptions data |
| `src/backend/aircooling/third_parties/foam/template/mesh/surfaceFeatureExtractDict.j2` | include partition-wall STL and helper zone STLs for feature extraction if needed |
| `src/backend/aircooling/third_parties/foam/template/system/steady/fvOptions.j2` | likely unchanged; reuse existing leakage-zone structure unless a separate comment block is desired |

---

## 11. Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Create partition wall | New object exists, default thickness = `0.1 m`, vertical by default |
| 2 | Set thickness to `0` | UI rejects/clamps; model remains valid |
| 3 | Rotate wall with yaw/pitch/roll | Geometry, selection, and export remain stable |
| 4 | Add rectangular through-hole | Hole appears on both front and back faces |
| 5 | Add circular through-hole | Circular tunnel cuts through thickness |
| 6 | Add overlapping holes | UI rejects overlap |
| 7 | Export wall without resistance | `merged_partition_wall.stl` present; no helper hole-zone STL |
| 8 | Export hole with resistance enabled at `40%` | helper STL exists; `cfd_export.partitionWalls[].holes[]` contains zone metadata |
| 9 | Export hole with `100%` | no source term generated |
| 10 | Export hole with `0%` | source term generated with maximal finite resistance |
| 11 | Mesh a room split by one full-height partition wall | Both sides retain volume mesh because two `none` seeds are added |
| 12 | Mesh a partition wall with a resistance-enabled hole | hole channel cells exist; hole cellZone is non-empty |
| 13 | Run old project without partition walls | behavior unchanged |

---

## 12. Risks and Follow-ups

### 12.1 Thin-Feature Meshing

If users reduce thickness far below the default `100 mm`, hole cellZones may disappear under coarse mesh settings. This is why partition walls should get their own refinement settings instead of silently inheriting generic `box` values.

### 12.2 Disconnected Fluid Regions

Two seeds per wall is the required baseline, but not a universal proof that every disconnected component is covered. Complex wall networks may still need manual `extraLocationsInMesh`.

### 12.3 Arbitrary Rotation + Face Editing

Allowing full 3D rotation makes front/back editing more complex than for vertical baffles. The implementation must keep hole coordinates in wall-local UV space so edits remain stable under transform changes.

### 12.4 Future Extensions

Out of scope for this phase but natural follow-ups:

- polygon hole shapes
- batch editing of hole resistance
- auto-generated extra fluid seeds for multi-wall compartment graphs
- direct hole-flow monitoring in postprocess

