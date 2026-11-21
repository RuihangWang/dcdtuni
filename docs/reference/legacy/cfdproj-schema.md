# .cfdproj Schema Reference (v3)

> **Status (2026-06-14 — superseded):** This page documents schemaVersion ≤ 3. The
> editor now reads and writes **schemaVersion 4** natively (cfdproj V4 / cfdrun V2
> refactor). The canonical V4 model shape is
> `model.geometry.{location,size,quaternion,scale,faces,cutouts}`, physics lives at
> `geometry.faces[*].patches[*].physics`, and models are bucketed under
> `constructions.{racks,acus,baffles,floors,walls,…}`. v3 (and v2) files migrate to V4
> on load (`src/frontend/shared/js/cfdproj_migrate.js` / `src/frontend/scripts/cfdproj_migrate.py`);
> saves always emit V4. See **[.cfdproj V4 Schema](../cfdproj-v4-schema.md)** for the
> current field reference. The tables below remain accurate for the pre-migration shape.
>
> **Update (2026-10-21):** the React editor now saves schemaVersion 5 (v4 plus
> `transient` keyframe tracks; see [.cfdproj v5](../cfdproj-v5-schema.md)). v3
> files still migrate on load, first to v4 and then to v5.

Complete field specification for the `.cfdproj` project file format, schema version 3.

!!! info
    For a conceptual overview, see [.cfdproj Format](../../user-guide/cfdproj-format.md).

!!! warning
    The `.cfdproj` format is client-side only. It is NOT validated by the backend Pydantic schema — only `.cfdrun` files are validated. The schema documented here is derived from `getInitialState()`, `normalizeState()`, and `createModelFromRaw()` in `src/frontend/web/STL_generate.html`.

---

## Overview

A `.cfdproj` file is a UTF-8 JSON document. The top level is an object with the keys described below. It is written by `JSON.stringify(state, null, 2)` — every field that exists in memory is written to disk. On load, `normalizeState()` applies defaults for any missing or invalid fields, so the format is tolerant of omitted optional keys.

Files saved in schema v2 (the previous flat layout) are automatically migrated to v3 on load by `migrateCfdprojV2toV3` (JS: `src/frontend/shared/js/cfdproj_migrate.js`; Python: `src/frontend/scripts/cfdproj_migrate.py`). The migration is a no-op for files that already carry `schemaVersion: 3`.

---

## 1. Top-Level Fields

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `schemaVersion` | `integer` | `3` | Schema version; `3` = current |
| `meta` | `Meta` | see §2 | Project identity, room geometry, case path |
| `model` | `ModelLibrary` | `{racks:{}, acus:{}}` | Template library (saved rack/ACU prototypes) |
| `construction` | `ModelObject[]` | `[]` | Ordered list of all model instances in the scene |
| `probeData` | `ProbePoint[]` | `[]` | CFD virtual probe locations (formerly `sensorPoints`) |
| `sensorData` | `SensorRecord[]` | `[]` | Real-world measurement records (new in v3) |
| `simulation` | `Simulation` | see §5 | All mesh and solver settings (formerly `cfdRoom.*` except height/temperature) |
| `liquidCooling` | `LiquidCooling` | see §6 | Liquid cooling topology, fluid properties, and solver config |
| `ui` | `UI` | see §7 | All display, selection, snap, and session state |

---

## 2. `meta`

Project identity, room geometry, and server-side case path.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `meta.projectName` | `string` | `""` | Human-readable project title |
| `meta.exportFileName` | `string` | `"default"` | Base filename for STL/cfdrun export |
| `meta.inputUnit` | `"mm" \| "cm" \| "m"` | `"mm"` | Length unit for all dimension inputs in the UI |
| `meta.description` | `string` | `""` | Free-text project description (new in v3) |
| `meta.room` | `RoomConfig` | see below | Room geometry |
| `meta.case` | `{path: string}` | `{path: ""}` | Server-side OpenFOAM case directory path |

### `meta.room`

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `meta.room.bounds` | `BoundsObject` | see §8 | Optional per-axis STL export clipping planes |
| `meta.room.polygon` | `RoomPolygon \| null` | `null` | Non-rectangular room boundary polygon; `null` = rectangular |
| `meta.room.height` | `number` | `3.6` | Room ceiling height (meters) |
| `meta.room.temperature` | `number` | `22` | Ambient / initial temperature (°C) for CFD |

---

## 3. `model` — Template Library

The template library stores re-usable rack and ACU prototypes. Only `rack` and `acu` kinds can be saved to the library.

```json
{
  "model": {
    "racks": {
      "t_abc1234": { /* rack template object */ }
    },
    "acus": {
      "t_xyz5678": { /* acu template object */ }
    }
  }
}
```

A template is a deep copy of a model object with the following fields **removed**: `id`, `name`, `anchor`, `transform`, `moveTool`, `rotateTool`, `enabled`, `showAnchorMarker`, `fromModel`. A `label` field (string) is added as the user-supplied template name.

Template keys use the format `"t_" + 7 random alphanumeric chars` (e.g. `"t_abc1234"`), generated at save time (`STL_generate.html:15857`).

When an instance is created from a template, its `fromModel` field is set to the template key.

---

## 4. `construction` — Model Objects

The `construction` array holds all scene model instances. The `kind` field determines which extra fields are present.

### 4.1 Common Fields (all kinds)

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `id` | `string` | auto `"m_N"` | Unique model identifier within this project |
| `fromModel` | `string \| null` | `null` | Template key (`"t_..."`) if created from library; `null` otherwise |
| `kind` | `string` | `"box"` | Model type; see §4.2 for valid values |
| `name` | `string` | kind-based | User-visible display name |
| `enabled` | `boolean` | `true` | Whether the model is included in STL export and CFD |
| `showAnchorMarker` | `boolean` | `true` | Show the anchor sphere in the 3D viewport |
| `opacity` | `number` | `1` | Viewport opacity `[0, 1]` |
| `anchor` | `Point3D` | `{x:0,y:0,z:0}` | World-space origin of the model (meters). Bounding box grows in +X, +Y, +Z |
| `base` | `{W, D, H}` | kind-default | External dimensions in meters: W = width (X), D = depth (Z), H = height (Y) |
| `transform` | `Transform` | identity | Quaternion rotation and non-uniform scale around `anchor` |
| `moveTool` | `MoveTool` | disabled | Axis-constrained drag gizmo state |
| `rotateTool` | `RotateTool \| null` | `null` | Euler rotation gizmo state |
| `faces` | `FaceMap` | 6 empty faces | Per-face patch definitions; see §10 |
| `solidRegions` | `string[]` | `[]` | Named solid region overrides (custom STL only) |
| `customSTLBase64` | `string \| null` | `null` | Base64-encoded ASCII STL content (custom only; `null` for all other kinds) |
| `customOffset` | `Point3D` | `{x:0,y:0,z:0}` | Translation applied to imported geometry in its local frame |
| `cutouts` | `object[]` | `[]` | CSG cutout definitions subtracted from parent geometry |
| `polygon` | `[number,number][] \| null` | `null` | 2-D vertex list `[[x,z],...]` for floor outline (floor only) |
| `isRoomBoundary` | `boolean` | `false` | True if auto-generated as the room-boundary wall |
| `cfd` | `CFDObject` | kind-default | CFD boundary condition config; see §4.3 |
| `timeVarying` | `object \| undefined` | `undefined` | Time-varying parameter overrides for transient simulations |

**`Point3D`:** `{ "x": 0.0, "y": 0.0, "z": 0.0 }`. All coordinates in meters. Y-up convention (Y = vertical, X = width, Z = depth).

### 4.2 Valid `kind` Values

| Kind | Description |
| ------ | ------------- |
| `box` | Parametric cuboid (generic obstacle or heat source); also the implicit fallback for an unknown/absent kind |
| `custom` | Imported ASCII STL mesh with optional projected patches (carries `customSTLBase64`) |
| `rack` | Parametric server rack with U-slot equipment system |
| `wall` | Thin solid wall (D = 0) |
| `partitionWall` | Partition panel; thin solid with D ≈ 0.05 m default |
| `baffle` | Zero-thickness baffle; can be perforated |
| `floor` | Horizontal floor slab (H = 0); can have raised-floor openings |
| `acu` | Precision air-conditioning unit |
| `cdu` | Coolant Distribution Unit (liquid cooling) |
| `crh` | Rear-Door Heat Exchanger (liquid cooling) |
| `cold_plate` | Chip-level cold plate (liquid cooling) |
| `heat_source` | Chip heat source (liquid cooling) |
| `pipe_segment` | Coolant pipe between ports |
| `pipe_junction` | T-branch pipe junction |

Unknown values default to `box` on load (`createModelFromRaw`, `STL_generate.html:3538`).

### 4.3 Per-Kind CFD Objects

**`kind: "box"` (default role `"wall"`):**

| Field | Type | Default |
| ------- | ------ | --------- |
| `role` | `"wall" \| "heatSource"` | `"wall"` |
| `heatSource.heatPower` | `number` | `0` |
| `heatSource.surfaceTemperature` | `number \| null` | `null` |
| `heatSource.convectionCoeff` | `number` | `10` |

**`kind: "rack"`:**

| Field | Type | Default |
| ------- | ------ | --------- |
| `role` | `"rack"` | `"rack"` |
| `servers` | `ServerCfd[]` | `[]` |
| `leakage.enabled` | `boolean` | `false` |
| `leakage.sigma` | `number` | `0.05` |

`ServerCfd`: `{ equipmentId, volumeFlowRate, power, fixedTemperature }`.

**`kind: "acu"`:**

| Field | Type | Default |
| ------- | ------ | --------- |
| `role` | `"acu"` | `"acu"` |
| `patchPhysics` | `object` | `{}` — map of `patchId → AcuPatchPhysics` |

`AcuPatchPhysics`: `{ role: "supply"|"return"|"wall", velocity, temperature, volumeFlowRate, setPointTemperature, coolingCapacity, coolantTemperature }`.

**`kind: "baffle"`:**

| Field | Type | Default |
| ------- | ------ | --------- |
| `role` | `"baffle"` | `"baffle"` |
| `baffleType` | `"wall" \| "perforated"` | `"wall"` |
| `perforated` | `PerforatedPlate` | `{shape:"circle",holeDiameterMm:3.0,openAreaPct:64.0}` |
| `patchPhysics` | `object` | `{}` — map of `patchId → PatchOverride` |

**`PerforatedPlate`** (used in baffles, rack doors, floor openings):

| Field | Type | Default |
| ------- | ------ | --------- |
| `shape` | `"circle" \| "rect"` | `"circle"` |
| `holeDiameterMm` | `number` | `3.0` |
| `openAreaPct` | `number` | `64.0` — clamped to `(0.1, 99.9)` |

**`kind: "partitionWall"`:**

| Field | Type | Default |
| ------- | ------ | --------- |
| `role` | `"partitionWall"` | `"partitionWall"` |
| `patchPhysics` | `object` | `{}` |

**`kind: "floor"`:**

| Field | Type | Default |
| ------- | ------ | --------- |
| `role` | `"floor"` | `"floor"` |
| `roomBoundaryType` | `"none" \| "top" \| "bottom"` | `"none"` |
| `openings` | `FloorOpening[]` | `[]` |
| `defaultBC` | `{type, perforated?}` | `{type:"wallBaffle"}` |

`FloorOpening`: `{ patchId, type: "wall"|"wallBaffle"|"perforated", perforated?: PerforatedPlate, seedConfig? }`.

Note: `roomBoundaryType:"none"` forces `defaultBC.type` to `"wallBaffle"` (never `"wall"`) on load.

**`kind: "wall"`:**

`{ role: "wall" }`

**`kind: "cdu"`:**

`{ role: "cdu" }`. CDU physics are in `model.liquidCooling` (see §4.4).

**`kind: "crh"`, `"cold_plate"`, `"heat_source"`, `"pipe_segment"`, `"pipe_junction"`:**

These kinds fall through `getDefaultCfd()`'s default case (`STL_generate.html:2964`), so freshly-created models carry `cfd: { role: "wall" }` — not `{ role: "<kind>" }`. A non-default role stored in an existing project file is preserved verbatim on load. Their physics live in kind-specific top-level fields (see §4.4).

### 4.4 Kind-Specific Top-Level Fields

#### `kind: "rack"` — extra field `rack`

See §9 (RackModel) for the full `rack` object.

#### `kind: "cdu"` — extra field `liquidCooling`

| Field | Type | Default |
| ------- | ------ | --------- |
| `liquidCooling.supplyTemperature` | `number` | `35` |
| `liquidCooling.supplyPressure` | `number` | `250` |
| `liquidCooling.totalFlowRate` | `number` | `60` |
| `liquidCooling.servedRacks` | `string[]` | `[]` |
| `liquidCooling.advancedMode` | `boolean` | `false` |
| `liquidCooling.advanced.pump.specName` | `string` | `"pump_default"` |
| `liquidCooling.advanced.pump.rpm` | `number` | `3000` |
| `liquidCooling.advanced.heatExchanger.specName` | `string` | `"hx_default"` |
| `liquidCooling.advanced.valve.specName` | `string` | `"valve_default"` |
| `liquidCooling.advanced.valve.opening` | `number` | `1.0` |

#### `kind: "crh"` — extra fields `crh`, `parentRackId`

| Field | Type | Default |
| ------- | ------ | --------- |
| `parentRackId` | `string \| null` | `null` |
| `crh.effectiveness` | `number` | `0.5` |
| `crh.waterFlowRate` | `number` | `20` |
| `crh.waterInletTemp` | `number` | `25` |
| `crh.heatRejectionCapacity` | `number` | `30000` |

#### `kind: "cold_plate"` — extra fields `coldPlate`, `parentRackId`, `parentServerId`

| Field | Type | Default |
| ------- | ------ | --------- |
| `parentRackId` | `string \| null` | `null` |
| `parentServerId` | `string \| null` | `null` |
| `coldPlate.specName` | `string` | `"cp_default"` |
| `coldPlate.chipTDP` | `number` | `350` |
| `coldPlate.thermalModel.chipArea` | `number` | `0.004` |
| `coldPlate.thermalModel.chipThickness` | `number` | `0.001` |
| `coldPlate.thermalModel.thermalConductivity` | `number` | `148` |
| `coldPlate.inletPortId` | `string \| null` | `null` |
| `coldPlate.outletPortId` | `string \| null` | `null` |

#### `kind: "heat_source"` — extra fields `heatSource`, `parentRackId`, `parentServerId`

| Field | Type | Default |
| ------- | ------ | --------- |
| `parentRackId` | `string \| null` | `null` |
| `parentServerId` | `string \| null` | `null` |
| `heatSource.power` | `number` | `350` |
| `heatSource.chipArea` | `number` | `0.004` |
| `heatSource.thermalMass` | `number` | `60` |

#### `kind: "pipe_segment"` — extra field `pipe`

| Field | Type | Default |
| ------- | ------ | --------- |
| `pipe.spec` | `string` | `"pipe_25mm"` |
| `pipe.diameter` | `number` | `0.025` |
| `pipe.startPortId` | `string` | `""` |
| `pipe.endPortId` | `string` | `""` |
| `pipe.waypoints` | `Point3D[]` | `[]` |
| `pipe.color` | `"supply" \| "return" \| "unconnected"` | `"supply"` |
| `pipe.junctions` | `string[]` | `[]` |
| `pipe.valves` | `object[]` | (optional) per-pipe valve fittings; each valve gets `type` defaulted to `"flow_control"` on load |
| `pipe.blindEnds` | `{start: boolean, end: boolean}` | `{start: false, end: false}` — added on load if absent |

#### `kind: "pipe_junction"` — extra fields `junction`, `parentPipeId`

| Field | Type | Default |
| ------- | ------ | --------- |
| `parentPipeId` | `string \| null` | `null` |
| `junction.type` | `"T_branch"` | `"T_branch"` |
| `junction.quaternion` | `Quaternion` | `{x:0,y:0,z:0,w:1}` |
| `junction.connectedPipes` | `string[]` | `[]` |

---

## 5. `simulation`

All former `cfdRoom.*` keys **except** `height` and `roomTemperature` (those moved to `meta.room`). All mesh generation and solver settings live here.

### 5.1 Mesh Seed Point

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `locationInMesh` | `Point3D` | `{x:5,y:4,z:1.5}` | snappyHexMesh `locationInMesh` seed; must be inside fluid domain |
| `locInMeshVisible` | `boolean` | `true` | Whether the seed-point sphere is visible in the viewport |
| `extraLocations` | `ExtraLocation[]` | `[]` (added by `normalizeState` if absent) | Additional seed points for multi-region meshing |
| `_nextExtraLocId` | `integer` | `1` (added by `normalizeState` if absent) | Counter for generating extra location IDs |

**`ExtraLocation`:** `{ id, name, x, y, z, visible }`.

### 5.2 Mesh Global Settings

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `meshBaseSize` | `number` | `0.34` | Base cell size for blockMesh / snappyHexMesh (meters) |
| `meshDetectBaseSize` | `number \| null` | `null` | Cell size for geometry detection pass; `null` → inherit `meshBaseSize` |
| `maxGlobalCells` | `integer` | `5000000` | Maximum total cell count for snappyHexMesh |
| `parallelMesh` | `boolean` | `true` | Run snappyHexMesh in parallel via MPI |
| `meshProcess` | `integer` | `16` | Number of MPI processes for meshing |
| `blockMeshBounds` | `object \| null` | `null` | Custom blockMesh bounding box override; when non-null: `{xMin,xMax,yMin,yMax,zMin,zMax}` |
| `blockMeshEditActive` | `boolean` | `false` | Internal: blockMesh bound-edit gizmo active |
| `blockMeshMoveMode` | `boolean` | `false` | Internal: gizmo move mode |
| `refinementZones` | `RefinementZone[]` | `[]` | Explicit box refinement zones |

**`RefinementZone`:** `{ id, name, level, min: Point3D, max: Point3D, visible }`.

### 5.3 Refinement Levels

Refinement level fields are `[minLevel, maxLevel]` integer tuples (snappyHexMesh `refinementSurfaces`).

| Field | Type | Default |
| ------- | ------ | --------- |
| `refinementLevel` | `[int, int]` | `[1, 1]` |
| `rackRegionLevel` | `integer` | `3` — scalar; applied to the rack-interior region |
| `acuRefinementLevel` | `[int, int]` | `[1, 1]` |
| `boxRefinementLevel` | `[int, int]` | `[1, 1]` |
| `wallBafflesRefinementLevel` | `[int, int]` | `[1, 1]` |
| `floorRefinementLevel` | `[int, int]` | `[1, 1]` |

!!! note "Old per-component levels removed"
    v2 projects stored many additional refinement level keys (e.g. `rackRefinementLevel`, `serverFaceRefinementLevel`, `leakageRefinementLevel`, `rackDoorRefinementLevel`, `partitionWallRefinementLevel`, etc.). These are **no longer written** to new projects. The backend `MeshConfig` schema accepts them via `extra="allow"` for backward compatibility but ignores them in favour of `rackRegionLevel`. Remove any v2-era per-component override keys when migrating stored files.

### 5.4 Feature Edge Levels

| Field | Type | Default |
| ------- | ------ | --------- |
| `defaultFeatureLevel` | `integer` | `2` |
| `acuFeatureLevel` | `integer` | `2` |
| `boxFeatureLevel` | `integer` | `2` |
| `baffleFeatureLevel` | `integer` | `2` |
| `floorFeatureLevel` | `integer` | `2` |

Other feature levels (rack, server, door, leakage, partition, etc.) may still exist in older saved files but are not part of the canonical `getInitialState()` defaults in v3.

### 5.5 Solver Settings

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `solverType` | `"steady" \| "transient"` | `"steady"` | Solver type |
| `endTime` | `integer` | `500` | Steady: number of iterations; transient: unused (use `endTimeSeconds`) |
| `writeInterval` | `integer` | `100` | Steady: write every N iterations |
| `endTimeSeconds` | `number` | `300` | Transient: end time (s) |
| `deltaT` | `number` | `0.1` | Transient: fixed time step (s) |
| `maxDeltaT` | `number` | `1.0` | Transient adaptive: max time step (s) |
| `maxCo` | `number` | `1.0` | Transient adaptive: max Courant number |
| `writeIntervalSeconds` | `number` | `10` | Transient: write every N seconds |
| `adjustTimeStep` | `boolean` | `true` | Transient: use adaptive time stepping |
| `startFrom` | `string` | `"latestTime"` | OpenFOAM `startFrom` directive |
| `nOuterCorrectors` | `integer` | `1` | PIMPLE outer correctors |
| `nCorrectors` | `integer` | `2` | PIMPLE inner correctors |
| `ddtScheme` | `string` | `"Euler"` | Transient time discretization scheme |
| `crankNicolsonCoeff` | `number` | `0.9` | Crank-Nicolson blending coefficient |
| `doublePrecisionWrite` | `boolean` | `false` | Write field data in double precision |
| `writePrecision` | `"single" \| "double"` | `"single"` | Field output precision |
| `gravityX` | `number` | `0` | Gravity X component (m/s²) |
| `gravityY` | `number` | `-9.81` | Gravity Y component (m/s²) |
| `gravityZ` | `number` | `0` | Gravity Z component (m/s²) |
| `parallelSolve` | `boolean` | `false` | Run solver in parallel via MPI |
| `solveProcess` | `integer` | `16` | Number of MPI processes for solving |
| `nNonOrthogonalCorrectors` | `integer` | `0` | SIMPLE non-orthogonal corrector iterations |
| `nonOrthoCorrection` | `boolean` | `false` | Enable non-orthogonal correction in linear solvers |
| `solverPreset` | `"fast" \| "balanced" \| "accurate"` | `"fast"` | Named preset controlling scheme/relaxation defaults |

### 5.6 Residual Control

Stored under `simulation.residualControl`:

| Field | Type | Default |
| ------- | ------ | --------- |
| `p_rgh` | `number` | `1e-4` |
| `U` | `number` | `1e-5` |
| `T` | `number` | `1e-5` |
| `k_epsilon` | `number` | `1e-4` |

### 5.7 Discretization Schemes

Stored under `simulation.schemes`:

| Field | Type | Default |
| ------- | ------ | --------- |
| `divSchemeU` | `string` | `"bounded Gauss upwind"` |
| `divSchemeT` | `string` | `"bounded Gauss upwind"` |
| `divSchemeK` | `string` | `"bounded Gauss upwind"` |
| `divSchemeEpsilon` | `string` | `"bounded Gauss upwind"` |
| `gradSchemeDefault` | `string` | `"Gauss linear"` |
| `gradSchemeU` | `string` | `""` |
| `gradSchemeK` | `string` | `""` |
| `gradSchemeEpsilon` | `string` | `""` |
| `laplacianScheme` | `string` | `"Gauss linear limited 0.5"` |
| `snGradScheme` | `string` | `"limited 0.5"` |

!!! note "Preset overrides"
    When a `solverPreset` is applied (e.g., "balanced" or "accurate"), these scheme values are overwritten in memory. The file always stores the actual current values.

### 5.8 Relaxation Factors

Stored under `simulation.relaxationFactors`:

| Field | Type | Default |
| ------- | ------ | --------- |
| `p_rgh` | `number` | `0.3` |
| `U` | `number` | `0.7` |
| `T` | `number` | `0.7` |
| `k_epsilon` | `number` | `0.7` |

---

## 6. `liquidCooling`

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `enabled` | `boolean` | `false` | Whether liquid cooling mode is active |
| `fluid.type` | `string` | `"water"` | Working fluid identifier |
| `fluid.rho` | `number` | `992.2` | Fluid density (kg/m³) |
| `fluid.mu` | `number` | `0.000653` | Dynamic viscosity (Pa·s) |
| `fluid.cp` | `number` | `4180` | Specific heat capacity (J/kg·K) |
| `fluid.k` | `number` | `0.632` | Thermal conductivity (W/m·K) |
| `specifications.coldPlateSpec` | `object[]` | `[{name:"cp_default",coefA:0.724,coefB:0.991,coefC:-0.155}]` | Cold plate hydraulic resistance specs |
| `specifications.pipeSpec` | `object[]` | `[{name:"pipe_25mm",diameter:0.025,roughness:1e-5,length:2.0}]` | Pipe hydraulic specs |
| `specifications.pumpSpec` | `object[]` | `[]` | Pump specs |
| `specifications.hxSpec` | `object[]` | `[]` | Heat exchanger specs |
| `specifications.valveSpec` | `object[]` | `[]` | Valve specs |
| `piping.autoGenerated` | `boolean` | `true` | Piping topology was auto-generated |
| `piping.mainPipeSpec` | `string` | `"pipe_25mm"` | Spec for main supply/return pipes |
| `piping.branchPipeSpec` | `string \| null` | `null` | Spec for branch pipes; `null` = inherit main |
| `piping.chains` | `array` | `[]` | Legacy piping chain definitions (migrated to pipe_segment models on load) |
| `results` | `object \| null` | `null` | Last liquid solve results |
| `solver.dt_s` | `number` | `1.0` | ODIL solver: time step (s) |
| `solver.N_t` | `integer` | `10` | ODIL solver: number of time steps |
| `solver.bdf_order` | `integer` | `1` | ODIL solver: BDF order |
| `solver.lr` | `number` | `0.1` | ODIL solver: Adam learning rate |
| `solver.max_epochs` | `integer` | `10000` | ODIL solver: max training epochs |
| `solver.convergence_tol` | `number` | `1e-6` | ODIL solver: convergence tolerance |
| `solver.device` | `string` | `"cpu"` | ODIL solver: torch device (`"cpu"` or `"cuda"`) |
| `solver.weights` | `object` | `{mass:1.0,momentum:1e-6,energy:1.0,bc:100.0,ic:10.0}` | ODIL solver: residual loss weights |

---

## 7. `ui`

Display, selection, and session state. Most session fields are **reset on load** regardless of saved values.

| Field | Type | Default | Load-reset? |
| ------- | ------ | --------- | ------------- |
| `displayMode` | `"air" \| "liquid"` | `"air"` | No |
| `view` | `ViewSettings` | `{showGrid:false,gridSpacing:0.1}` | No |
| `snapFace` | `boolean` | `true` | Always reset to `true` |
| `snapEdge` | `boolean` | `true` | Always reset to `true` |
| `snapGrid` | `boolean` | `false` | No |
| `treeCollapsed` | `object` | `{}` | No |
| `previewVisibility` | `object` | `{}` | No — `modelId → boolean` |
| `previewRegionVisibility` | `object` | `{}` | No — `modelId → {regionName: boolean}` |
| `previewRegionReverse` | `object` | `{}` | No — `modelId → {regionName: boolean}` |
| `groups` | `GroupObject[]` | `[]` | No — members with invalid model IDs are pruned |
| `rackDetail` | `RackDetail` | `{target:null,returnMode:"3d",showContext:false}` | No |
| `_selectedCategories` | `string[]` | `[]` | No |
| `_anchorCategory` | `string \| null` | `null` | No |
| `_lastClickedCategory` | `string \| null` | `null` | No |
| `selection` | `SelectionState` | see §13 | Reset if `modelId` no longer exists in `construction` |
| `session` | `SessionState` | see below | Most fields reset |

**`ui.view` (ViewSettings):**

| Field | Type | Default |
| ------- | ------ | --------- |
| `showGrid` | `boolean` | `false` |
| `gridSpacing` | `number` | `0.1` — clamped to `[0.01, 1]` |

**`ui.rackDetail`:**

| Field | Type | Default |
| ------- | ------ | --------- |
| `target` | `string \| null` | `null` — model ID of rack currently open in rack-detail editor |
| `returnMode` | `"3d" \| "2d"` | `"3d"` |
| `showContext` | `boolean` | `false` |

**`ui.session`** (formerly `dctwinConfig`; mostly reset on load):

| Field | Type | Default | Load-reset? |
| ------- | ------ | --------- | ------------- |
| `validated` | `boolean` | `false` | Always reset to `false` |
| `activeTab` | `string` | `"geometry"` | Always reset to `"geometry"` |
| `activeJobId` | `string \| null` | `null` | Always reset to `null` |
| `_serverInfo` | `object \| null` | `null` | Runtime-only; **not** part of `getInitialState()` — injected and reset to `null` by `normalizeState` on load |
| `showSeedHelper` | `boolean` | `true` | No |
| `meshReady` | `boolean` | `false` | Always reset to `false` |
| `solveComplete` | `boolean` | `false` | Always reset to `false` |
| `logRuns` | `LogRun[]` | `[]` | Always reset to `[]` |
| `liquidRuns` | `array` | `[]` | Always reset to `[]` |

**`LogRun` object:**

| Field | Type | Description |
| ------- | ------ | ------------- |
| `name` | `string` | Run label (format `YYYY-MM-DD-HH-MM-SS_<caseName>`) |
| `path` | `string` | Absolute path to the run log directory |
| `timesteps` | `integer[]` | Available timestep directories |
| `latestTimestep` | `integer` | Highest available timestep |
| `hasResults` | `boolean` | `true` if timestep > 0 |

---

## 8. BoundsObject

Optional axis-aligned clipping planes, now stored at `meta.room.bounds`.

| Field | Type | Default |
| ------- | ------ | --------- |
| `xMin.enabled` | `boolean` | `false` |
| `xMin.value` | `number` | `0` |
| `xMax.enabled` | `boolean` | `false` |
| `xMax.value` | `number` | `1` |
| `yMin.enabled` | `boolean` | `false` |
| `yMin.value` | `number` | `0` |
| `yMax.enabled` | `boolean` | `false` |
| `yMax.value` | `number` | `1` |
| `zMin.enabled` | `boolean` | `false` |
| `zMin.value` | `number` | `0` |
| `zMax.enabled` | `boolean` | `false` |
| `zMax.value` | `number` | `1` |

---

## 9. RackModel

The `rack` field is present only on `kind: "rack"` models. It is managed by `getDefaultRackState()` and `cloneRackState()`.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `heightMode` | `"totalHeight" \| "unitHeight"` | `"totalHeight"` | `totalHeight`: user sets `base.H`; `unitHeight`: `base.H` = `unitCount × unitHeight + margins` |
| `unitCount` | `integer` | `42` | Number of rack units |
| `unitHeight` | `number` | `0.04445` | Height per rack unit (m; standard 1U = 44.45 mm) |
| `slotWidthRatio` | `number` | `0.8` | Fraction of rack width for 19" mounting strips `[0.2, 0.95]` |
| `inletOffset` | `number` | `0` | Inlet plenum depth from front face (m) |
| `outletOffset` | `number` | `0` | Outlet plenum depth from back face (m) |
| `inletDoor` | `RackDoor \| null` | `null` | Perforated front door; `null` = no door |
| `outletDoor` | `RackDoor \| null` | `null` | Perforated rear door; `null` = no door |
| `safeMarginTop` | `number` | `0.04445` | Dead space above slot area (m); default = 1U |
| `safeMarginBottom` | `number` | `0.04445` | Dead space below slot area (m); default = 1U |
| `defaultSpanUnits` | `integer` | `1` | Default equipment height when adding new slots |
| `deletedFaces.front` | `boolean[]` | `[]` — expanded to `unitCount` bools on sync | `true` at index `i` hides front inlet slot face for unit `i` |
| `deletedFaces.back` | `boolean[]` | `[]` — expanded to `unitCount` bools on sync; back defaults to `true` | `true` at index `i` hides rear outlet slot face for unit `i` |
| `throughSlotSigma` | `object` | `{}` | Map of `equipmentId → sigma` for through-zone leakage fraction override |
| `partitions` | `RackPartition[]` | `[]` — expanded to `unitCount-1` entries on sync | Horizontal partition plates between slots |
| `equipments` | `Equipment[]` | `[]` | Equipment items installed in this rack |
| `blankingPanelType` | `string` | `"typicalSmallGap"` | Blanking panel model for unoccupied slots |
| `coolingType` | `"air" \| "liquid"` | `"air"` | Rack cooling mode |
| `liquidManifold` | `object` | see below | Liquid cooling manifold geometry |

**`liquidManifold` defaults:** `{ layout: "split", supplyPipe: { diameter: 0.025, offsetFromInlet: 0.10 }, returnPipe: { diameter: 0.025, offsetFromInlet: 0.10 }, pipeSpacing: 0.015 }`.

**`RackDoor`:**

| Field | Type | Default |
| ------- | ------ | --------- |
| `perforated` | `PerforatedPlate` | `{shape:"circle",holeDiameterMm:3.0,openAreaPct:64.0}` |
| `renderDoor` | `boolean` | `true` |

**`RackPartition`:**

| Field | Type | Description |
| ------- | ------ | ------------- |
| `index` | `integer` | 0-based slot index; partition sits between unit `index` and `index+1` |
| `name` | `string` | User display name |
| `deleted` | `boolean` | `true` = hidden (default); `false` = visible (must be explicitly enabled by user) |

---

## 10. FaceDefinition

Each of the six faces (`front`, `back`, `left`, `right`, `top`, `bottom`) of a model.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `id` | face key | — | Face identifier |
| `label` | `string` | built-in | Built-in face label (not user-editable) |
| `wType` | `"W" \| "D"` | — | Which base dimension spans face width |
| `hType` | `"H" \| "D"` | — | Which base dimension spans face height |
| `displayName` | `string` | `label` | User-editable face name; used as STL solid name prefix |
| `prefixEnabled` | `boolean` | `true` | If `false`, patches exported without model-name prefix |
| `opacity` | `number` | `1` | Per-face viewport opacity |
| `wallNames` | `string[]` | `[]` | Named sub-wall regions composing this face |
| `wallOpacities` | `number[]` | `[]` | Per-wall opacity (parallel array to `wallNames`) |
| `patches` | `PatchDefinition[]` | `[]` | Rectangular or circular patches on this face |
| `reverseSolidNormal` | `boolean` | `false` | If `true`, exported STL normals for this face are flipped |

| Face | wType | hType | Physical plane |
| ------ | ------- | ------- | ---------------- |
| `front` | `W` | `H` | +Z face |
| `back` | `W` | `H` | -Z face |
| `left` | `D` | `H` | -X face |
| `right` | `D` | `H` | +X face |
| `top` | `W` | `D` | +Y face |
| `bottom` | `W` | `D` | -Y face |

---

## 11. PatchDefinition

A named region on a face, exported as a separate `solid`/`endsolid` STL block.

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `id` | `string` | yes | Unique patch ID; format `p_<integer>` |
| `type` | `"rect" \| "circle"` | yes | Patch shape |
| `name` | `string` | no | User-visible patch name (also the STL solid name) |
| `color` | `string` | no | CSS hex color for viewport rendering |
| `showVector` | `boolean` | no | Whether to render the velocity direction arrow |
| `vx`, `vy`, `vz` | `number` | no | Unit vector components for velocity direction indicator |
| `applied` | `boolean` | no | Internal: patch geometry has been processed |

**`type: "rect"` additional fields:** `x, y` (bottom-left corner as fractions of face width/height), `w, h` (size as fractions). All in `[0, 1]`.

**`type: "circle"` additional fields:** `cx, cy` (center fractions), `r` (radius as fraction of face width, clamped to `[0.05, 0.5]`).

All coordinates are normalized fractions, not meters. `x=0, y=0` is the bottom-left of the face.

---

## 12. Equipment

An item installed in a rack occupying one or more U-slots.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `id` | `string` | auto `"e_N"` | Unique equipment identifier |
| `kind` | `"fanServer" \| "nonFan" \| "liquidServer"` | `"fanServer"` | Equipment type |
| `name` | `string` | kind-based | Display name |
| `startUnit` | `integer` | `0` | 0-based slot index of the bottom U |
| `spanUnits` | `integer` | `1` | How many U-slots this equipment occupies |
| `extraInletOffset` | `number` | `0` | Additional inlet depth offset (m) |
| `extraOutletOffset` | `number` | `0` | Additional outlet depth offset (m) |
| `faces` | `EquipmentFaces` | — | Per-face patch lists (inlet/outlet/top/bottom/left/right) |
| `liquidCooling` | `object \| null` | `null` | Preserved for migration; standalone models preferred in v3 |

`fanServer`: creates an airflow through-channel across rack depth. `nonFan`: wall-bounded, no through-channel. `liquidServer`: liquid-cooled variant.

`EquipmentFaces` has keys `inlet`, `outlet`, `top`, `bottom`, `left`, `right`; each has `patches: PatchDefinition[]` and an optional `color` string.

---

## 13. SelectionState

Tracks what the user has selected. Stored under `ui.selection`.

| Field | Type | Default |
| ------- | ------ | --------- |
| `type` | `"project" \| "model" \| "face" \| "patch" \| "equipment"` | `"project"` |
| `modelId` | `string \| null` | `null` |
| `modelIds` | `string[]` | `[]` |
| `anchorModelId` | `string \| null` | `null` |
| `faceId` | `string \| null` | `null` |
| `patchId` | `string \| null` | `null` |
| `regionName` | `string \| null` | `null` |
| `wallIndex` | `integer \| null` | `null` |
| `slotIndex` | `integer \| null` | `null` |
| `slotIndices` | `integer[] \| null` | `null` |
| `anchorSlotIndex` | `integer \| null` | `null` |
| `equipmentId` | `string \| null` | `null` |
| `side` | `"front" \| "back" \| null` | `null` |
| `partitionIndex` | `integer \| null` | `null` |

On load: if `modelId` is not found in `construction`, the entire selection is reset to the project-level default.

---

## 14. `probeData` — Probe Points

CFD virtual probe locations (formerly `sensorPoints` in v2).

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `id` | `string` | auto `"probe_<random>"` | Unique probe ID |
| `name` | `string` | same as `id` | Display name |
| `x` | `number` | `0` | World-space X position (meters) |
| `y` | `number` | `0` | World-space Y position (meters) |
| `z` | `number` | `0` | World-space Z position (meters) |
| `enabled` | `boolean` | `true` | Whether this probe is included in solver monitoring output |

---

## 15. `sensorData` — Sensor Records

Real-world measurement records. New in schema v3. The array is passed through as-is by `normalizeState` (no per-field normalization). Each element represents a reference measurement bound to a physical location.

Fields observed in practice: `id, name, quantity, unit, location{x,y,z}, bind{instanceId}, series[], source`.

---

## 16. GroupObject

Named groups linking models for coordinated selection. Stored under `ui.groups`.

| Field | Type | Description |
| ------- | ------ | ------------- |
| `id` | `string` | Unique group ID |
| `name` | `string` | Display name |
| `memberIds` | `string[]` | Model IDs in the group |

Groups with fewer than 2 valid members after load validation are discarded.

---

## 17. RoomPolygon

Optional non-rectangular room boundary. Stored at `meta.room.polygon`.

| Field | Type | Description |
| ------- | ------ | ------------- |
| `vertices` | `[number, number][]` | Ordered 2-D vertex list `[[x, z], ...]` in meters; minimum 3 vertices |
| `height` | `number` | Room height (meters); must be > 0 |

When the polygon is invalid (fewer than 3 vertices, non-finite coordinates, or `height ≤ 0`), `normalizeState` resets it to `null`.

---

## 18. Transform

Applied to a model's geometry around `anchor`.

| Field | Type | Default |
| ------- | ------ | --------- |
| `quaternion` | `Quaternion` | `{x:0,y:0,z:0,w:1}` (identity) |
| `scale` | `{x, y, z}` | `{x:1,y:1,z:1}` |

Euler decomposition uses Y-up order `YXZ` (yaw around Y, pitch around X, roll around Z).

---

## 19. MoveTool and RotateTool

**MoveTool** (stored in `model.moveTool`):

| Field | Type | Default |
| ------- | ------ | --------- |
| `enabled` | `boolean` | `false` |
| `axes.x` | `boolean` | `true` |
| `axes.y` | `boolean` | `true` |
| `axes.z` | `boolean` | `true` |

**RotateTool** (stored in `model.rotateTool`, may be `null`):

| Field | Type | Default |
| ------- | ------ | --------- |
| `enabled` | `boolean` | `false` |
| `axes.yaw` | `boolean` | `true` |
| `axes.pitch` | `boolean` | `false` |
| `axes.roll` | `boolean` | `false` |

---

## 20. v2 → v3 Migration Map

Files with `schemaVersion < 3` (or absent) are auto-migrated on load by `migrateCfdprojV2toV3`. Migration is a no-op for `schemaVersion >= 3`.

| v2 key | v3 location | Notes |
| -------- | ------------- | ------- |
| `projectName` | `meta.projectName` | |
| `exportFileName` | `meta.exportFileName` | |
| `inputUnit` | `meta.inputUnit` | |
| `bounds` | `meta.room.bounds` | |
| `roomPolygon` | `meta.room.polygon` | Object `{vertices,height}`, not an array |
| `cfdRoom.height` | `meta.room.height` | Removed from `cfdRoom` |
| `cfdRoom.roomTemperature` | `meta.room.temperature` | Removed from `cfdRoom` |
| `cfdRoom.*` (remaining) | `simulation.*` | All other cfdRoom keys become simulation keys verbatim |
| `dctwinConfig.casePath` | `meta.case.path` | Removed from dctwinConfig |
| `dctwinConfig.*` (remaining) | `ui.session.*` | Session sub-object |
| `models` | `construction` | Each element gets `fromModel: null` if absent |
| `sensorPoints` | `probeData` | |
| `displayMode` | `ui.displayMode` | |
| `view` | `ui.view` | |
| `snapFace` | `ui.snapFace` | |
| `snapEdge` | `ui.snapEdge` | |
| `snapGrid` | `ui.snapGrid` | |
| `treeCollapsed` | `ui.treeCollapsed` | |
| `selection` | `ui.selection` | |
| `groups` | `ui.groups` | |
| `previewVisibility` | `ui.previewVisibility` | |
| `previewRegionVisibility` | `ui.previewRegionVisibility` | |
| `previewRegionReverse` | `ui.previewRegionReverse` | |
| `rackDetailTarget` | `ui.rackDetail.target` | |
| `rackDetailReturnMode` | `ui.rackDetail.returnMode` | |
| `rackDetailShowContext` | `ui.rackDetail.showContext` | |
| `_selectedCategories` | `ui._selectedCategories` | |
| `_anchorCategory` | `ui._anchorCategory` | |
| `_lastClickedCategory` | `ui._lastClickedCategory` | |
| `liquidCooling` | `liquidCooling` | Passed through verbatim |
| *(absent)* | `meta.description` | Always `""` in migrated files |
| *(absent)* | `model` | Always `{racks:{}, acus:{}}` in migrated files |
| *(absent)* | `sensorData` | Always `[]` in migrated files |

Unrecognized top-level keys not in the above map are carried forward verbatim for forward-compatibility.

Python mirror: `src/frontend/scripts/cfdproj_migrate.py:migrate_v2_to_v3` (called by `load_cfdproj`).

---

## Appendix: Complete Type Tree

```
.cfdproj
├── schemaVersion: 3
├── meta
│   ├── projectName, exportFileName, inputUnit, description
│   ├── room: { bounds (BoundsObject), polygon (RoomPolygon|null),
│   │           height, temperature }
│   └── case: { path }
├── model
│   ├── racks: { "t_<7chars>": templateObject, ... }
│   └── acus:  { "t_<7chars>": templateObject, ... }
├── construction: ModelObject[]
│   ├── id, fromModel, kind, name, enabled, showAnchorMarker, opacity
│   ├── anchor: Point3D          // Y-up, meters
│   ├── base: { W, D, H }        // meters
│   ├── transform: { quaternion, scale }
│   ├── moveTool, rotateTool
│   ├── faces: { front, back, left, right, top, bottom }: FaceDefinition
│   │   └── patches: PatchDefinition[]
│   ├── cfd: CFDObject           // varies by kind
│   ├── rack: RackModel          // rack only
│   │   └── equipments: Equipment[]
│   ├── liquidCooling: object    // cdu only
│   ├── crh: object              // crh only
│   ├── coldPlate: object        // cold_plate only
│   ├── heatSource: object       // heat_source only
│   ├── pipe: object             // pipe_segment only
│   ├── junction: object         // pipe_junction only
│   ├── customSTLBase64: string|null
│   ├── solidRegions: string[]
│   ├── polygon: [[x,z]][]|null  // floor only
│   ├── cutouts: object[]
│   ├── isRoomBoundary: boolean
│   └── timeVarying: object      // optional; transient events
├── probeData: [ { id, name, x, y, z, enabled } ]
├── sensorData: [ { id, name, quantity, unit, location, bind, series, source } ]
├── simulation
│   ├── locationInMesh, locInMeshVisible, extraLocations, _nextExtraLocId
│   ├── meshBaseSize, meshDetectBaseSize, maxGlobalCells
│   ├── parallelMesh, meshProcess, blockMeshBounds, blockMeshEditActive, blockMeshMoveMode
│   ├── refinementZones: RefinementZone[]
│   ├── refinementLevel, rackRegionLevel, acuRefinementLevel, boxRefinementLevel,
│   │   wallBafflesRefinementLevel, floorRefinementLevel
│   ├── defaultFeatureLevel, acuFeatureLevel, boxFeatureLevel,
│   │   baffleFeatureLevel, floorFeatureLevel
│   ├── solverType, endTime, writeInterval, endTimeSeconds, deltaT, maxDeltaT,
│   │   maxCo, writeIntervalSeconds, adjustTimeStep, startFrom,
│   │   nOuterCorrectors, nCorrectors, ddtScheme, crankNicolsonCoeff,
│   │   doublePrecisionWrite, writePrecision
│   ├── gravityX/Y/Z, parallelSolve, solveProcess
│   ├── nNonOrthogonalCorrectors, nonOrthoCorrection, solverPreset
│   ├── residualControl: { p_rgh, U, T, k_epsilon }
│   ├── schemes: { divScheme*, gradScheme*, laplacianScheme, snGradScheme }
│   └── relaxationFactors: { p_rgh, U, T, k_epsilon }
├── liquidCooling
│   ├── enabled, fluid, specifications, piping, results
│   └── solver: { dt_s, N_t, bdf_order, lr, max_epochs, convergence_tol, device, weights }
└── ui
    ├── displayMode, view, snapFace, snapEdge, snapGrid
    ├── treeCollapsed, previewVisibility, previewRegionVisibility, previewRegionReverse
    ├── groups: GroupObject[]
    ├── rackDetail: { target, returnMode, showContext }
    ├── _selectedCategories, _anchorCategory, _lastClickedCategory
    ├── selection: SelectionState
    └── session: { validated, activeTab, activeJobId, _serverInfo, showSeedHelper,
                   meshReady, solveComplete, logRuns, liquidRuns }
```
