# .cfdrun Schema Reference

> **Status (2026-06-14 — superseded):** The runner now consumes **cfdrun
> schemaVersion 2 only** (`CfdrunV2`, validated by
> `src/backend/aircooling/runner/cfdrun_schema.py` and materialised by
> `cfdrun_reader.py`). V2 carries explicit `boundaryBindings` / `porousBindings` /
> `sourceTerms` (uid-keyed) plus a transitional `meshGeometry` blob; the old
> in-runner BC derivation (`_build_bridge_bc`) is deleted. A non-V2 manifest is
> rejected at load with a "re-export from the editor" message (hard cutover, no dual
> format). See **[.cfdrun V2 Schema](../cfdrun-v2-schema.md)** for the current field
> reference. The tables below describe the legacy v1 manifest.
>
> **Update (2026-10-21):** the runner now accepts cfdrun schemaVersion 2, 3 and 4
> (the React editor exports 4); v1 is still refused. The v1 geometry domain
> described below lives on as the `meshGeometry` block of current manifests, so
> these tables remain the reference for that block.

Complete field specification for the `.cfdrun` simulation manifest format.

!!! info
    For a conceptual overview, see [.cfdrun Format](../../user-guide/cfdrun-format.md).

The `.cfdrun` file is a JSON document validated by the Pydantic v2 models in
`src/backend/aircooling/runner/cfdrun_schema.py`. Every class inherits from
`CFDBaseModel` which sets `extra="allow"`, so unknown fields are preserved for
forward compatibility and never cause a validation error.

---

## Top-Level: `CFDExport` {#cfdexport}

Root object of every `.cfdrun` file (`cfdrun_schema.py:488`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `version` | `string` | no | `"1.0"` | Schema version string. Informational — not enforced by the runner. |
| `source` | `string` | no | `"frontend"` | Tool that produced the file. |
| `projectName` | `string` | no | `"CFD_Project"` | Human-readable project label shown in the UI. |
| `room` | [`CFDExportRoom`](#room) | no | see Room defaults | Room envelope definition. |
| `racks` | `Dict[str, `[`CFDExportRack`](#rack)`]` | no | `{}` | Map of rack ID → rack object. Keys become OpenFOAM patch name prefixes. |
| `acus` | `Dict[str, `[`CFDExportACU`](#acu)`]` | no | `{}` | Map of ACU ID → ACU object. |
| `wallObjects` | `Dict[str, `[`CFDExportWallObject`](#wall-object)`]` | no | `{}` | Wall-mounted objects with heat patches (e.g., PDUs, UPSes). |
| `heatSources` | `Dict[str, `[`CFDExportHeatSource`](#heat-source)`]` | no | `{}` | Volumetric or surface heat sources. |
| `baffles` | `Dict[str, `[`CFDExportBaffle`](#baffle)`]` | no | `{}` | Interior baffle panels (solid or perforated). |
| `partitionWalls` | `Dict[str, `[`CFDExportPartitionWall`](#partition-wall)`]` | no | `{}` | Partition walls that may contain holes (e.g., blanking-panel zones). |
| `floors` | `Dict[str, `[`CFDExportFloor`](#floor)`]` | no | `{}` | Raised-floor panels with optional perforated openings. |
| `walls` | `Dict[str, `[`CFDExportWall`](#wall)`]` | no | `{}` | Outer room wall surfaces. |
| `sensorPoints` | `List[`[`SensorPoint`](#sensor-point)`]` | no | `[]` | Virtual temperature-sensor probe locations. |
| `meshConfig` | [`MeshConfig`](#mesh-configuration) | no | see MeshConfig defaults | snappyHexMesh refinement settings. |
| `simulationConfig` | [`SimulationConfig`](#simulation-configuration) | no | see SimulationConfig defaults | Solver settings (time, residuals, parallelism). |

**Validation:** After parsing, a `model_validator` checks that every
`RackServer.id` and every server patch name (`inletSolidName`, `outletSolidName`,
`topSolidName`, `bottomSolidName`) is unique across the whole export. Patch names
are first passed through `_sanitize_patch_name()` (non-`[A-Za-z0-9_]` → `_`,
`cfdrun_schema.py:13-15`), so two lexically different names that sanitize to the
same string (e.g. `rack-A` and `rack_A`) also count as a collision. Duplicate
IDs or patch names raise a `ValueError` (`cfdrun_schema.py:506-538`).

---

## Shared Types {#shared-types}

### `CFDBaseModel` {#cfdbasemodel}

Base class for all schema models (`cfdrun_schema.py:26`).

```python
class CFDBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")
```

Setting `extra="allow"` means any field not declared in the Pydantic model is
silently accepted and stored. This allows new UI-emitted fields to round-trip
through schema validation without errors, even before they are formally declared.

### `TimeVaryingEntry` {#timevaryingentry}

A single time-value pair for time-varying boundary conditions
(`cfdrun_schema.py:32`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `t` | `float` | **yes** | — | Time instant in seconds. |
| `value` | `float` | **yes** | — | Scalar value at time `t`. |

### `Point3D` {#point3d}

A position or direction vector in metres (Y-up coordinate system)
(`cfdrun_schema.py:37`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `x` | `float` | no | `0.0` | X-coordinate (metres). |
| `y` | `float` | no | `0.0` | Y-coordinate (metres, vertical). |
| `z` | `float` | no | `0.0` | Z-coordinate (metres). |

### `Quaternion4` {#quaternion4}

Unit quaternion encoding an arbitrary 3-D rotation (`cfdrun_schema.py:204`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `x` | `float` | no | `0.0` | Quaternion X component. |
| `y` | `float` | no | `0.0` | Quaternion Y component. |
| `z` | `float` | no | `0.0` | Quaternion Z component. |
| `w` | `float` | no | `1.0` | Quaternion W component (identity = `1.0`). |

### `SimpleGeometry` {#simplegeometry}

Bounding-box geometry shared by ACUs, wall objects, heat sources, and walls
(`cfdrun_schema.py:198`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `anchor` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:0}` | World-space position of the object's origin corner (metres). |
| `size` | `Dict[str, float]` | no | `{}` | Dimensions dictionary, e.g. `{"W": 0.6, "D": 1.0, "H": 2.0}` (metres). |
| `orientation` | `float` | no | `0.0` | Yaw rotation around the Y-axis in degrees. |

### `SeedConfig` {#seedconfig}

Specifies a `locationInMesh` seed point for an interior fluid region (used by
snappyHexMesh to identify the "inside" of a thin surface)
(`cfdrun_schema.py:43`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | `bool` | no | `true` | When `false`, the seed is ignored and the region uses the global `locationInMesh`. |
| `anchor` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:0}` | Base position of the seed point (metres). |
| `direction` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:1}` | Unit vector; the seed is placed at `anchor + direction * offset`. |
| `offset` | `float` | no | `0.0` | Scalar distance along `direction` to reach the final seed location. |

### `PerforatedConfig` {#perforatedconfig}

Physical description of a perforated plate (used as input to the Idelchik §8.2
pressure-loss formula). Shared by baffles, floor openings, and rack doors
(`cfdrun_schema.py:308`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `shape` | `"circle"` \| `"rect"` \| `"diamond"` | no | `"circle"` | Hole shape. |
| `holeDiameterMm` | `float` | no | `3.0` | Effective hole diameter in millimetres. For `rect` and `diamond`, this is the inscribed-circle diameter. |
| `openAreaPct` | `float` | no | `64.0` | Open area percentage (0–100). Determines the free-area ratio `φ = openAreaPct / 100`. |

---

## Room: `CFDExportRoom` {#room}

Defines the fluid domain envelope (`cfdrun_schema.py:57`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `height` | `float` | no | `3.6` | Room height in metres (vertical extent along Y; Y-up convention). |
| `plane` | `List[`[`Point3D`](#point3d)`]` | no | `[]` | Floor polygon vertices (XZ plane) that define the horizontal footprint of the room. Listed in order; the polygon is automatically closed. |
| `locationInMesh` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:0}` | Primary `locationInMesh` seed point for snappyHexMesh — must lie strictly inside the fluid domain. |
| `extraLocationsInMesh` | `List[`[`Point3D`](#point3d)`]` | no | `[]` | Additional seed points for isolated sub-regions of the fluid domain. |

---

## Rack: `CFDExportRack` {#rack}

Server rack with per-server physics, optional leakage through-zones, and
perforated door baffles (`cfdrun_schema.py:143`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` | **yes** | — | Path to the rack's STL file relative to the project directory. |
| `leakageStlFile` | `string` \| `null` | no | `null` | Legacy field for a separate leakage STL. Null in all new exports; superseded by the through-zone system. |
| `geometry` | [`RackGeometry`](#rackgeometry) | no | see sub-table | Physical dimensions and position of the rack. |
| `servers` | `List[`[`RackServer`](#rackserver)`]` | no | `[]` | Ordered list of server entries (fan servers and non-fan equipment). |
| `leakage` | [`ThroughZoneConfig`](#throughzoneconfig) | no | disabled | Through-zone (leakage) configuration. The JSON key is always `leakage`. |
| `wallSolidNames` | `List[string]` | no | `[]` | STL solid names mapped to adiabatic wall boundary conditions. |
| `inletDoor` | [`RackDoor`](#rackdoor) \| `null` | no | `null` | Front perforated door (cold aisle side). |
| `outletDoor` | [`RackDoor`](#rackdoor) \| `null` | no | `null` | Rear perforated door (hot aisle side). |

The following keys are accepted via `extra="allow"` and may appear in older
exports, but are not declared Pydantic fields:

| Extra key | Description |
|-----------|-------------|
| `wallBaffleSolids` | Deprecated slot for blanking-plate solid names. Replaced by per-bucket server entries in `servers`. |
| `frameSolidNames` | Solid names representing the rack frame structure (mapped to wall BC). |
| `safeMarginTop` | Empty U-space safety margin at the top of the rack (in rack units). |
| `safeMarginBottom` | Empty U-space safety margin at the bottom of the rack (in rack units). |
| `minUSpanForDoorBucket` | Frontend-computed minimum U-span across all server/blanking/through-zone entries; drives door-refinement level bucketing. `null` triggers the unified fallback. |

### `RackGeometry` {#rackgeometry}

(`cfdrun_schema.py:115`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `anchor` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:0}` | World-space position of the rack's origin (front-bottom-left corner, metres). |
| `size` | `Dict[str, float]` | no | `{"W":0.6,"D":1.0,"H":1.867}` | Outer dimensions of the rack enclosure in metres. Keys: `W` (width), `D` (depth), `H` (height). |
| `orientation` | `float` | no | `0.0` | Yaw rotation around the Y-axis in degrees. |
| `unitCount` | `int` | no | `42` | Total number of rack units (U) the rack can hold. |
| `unitHeight` | `float` | no | `0.04445` | Height of one rack unit in metres (1U = 44.45 mm). |
| `slotWidthRatio` | `float` | no | `0.8` | Fraction of rack width occupied by the active slot area (used for fan-server channel geometry). |

### `RackServer` {#rackserver}

One logical entry occupying one or more consecutive rack units
(`cfdrun_schema.py:73`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | `string` | **yes** | — | Unique server identifier across the entire export (validated by `CFDExport`). |
| `type` | `"fanServer"` \| `"nonFan"` | no | `"fanServer"` | `fanServer`: active fan creating a through-channel. `nonFan`: passive equipment bounded by rack walls. |
| `startSlot` | `int` | no | `0` | Zero-based index of the bottom-most rack unit this entry occupies. |
| `spanSlots` | `int` | no | `1` | Number of consecutive rack units this entry spans. |
| `inletSolidName` | `string` \| `null` | no | `""` | STL solid name for the inlet face. Empty or null when the face has been split into per-patch sub-solids (see `faces` extra key). |
| `outletSolidName` | `string` \| `null` | no | `""` | STL solid name for the outlet face. Empty or null when split into per-patch sub-solids. |
| `topSolidName` | `string` \| `null` | no | `null` | *nonFan only.* Solid name for the top plate (used for `fixedTemperature` BC). |
| `bottomSolidName` | `string` \| `null` | no | `null` | *nonFan only.* Solid name for the bottom plate (used for `fixedTemperature` BC). |
| `physics` | [`ServerPhysics`](#serverphysics) | no | see sub-table | Thermal and flow physics for this entry. |

The key `faces` (`Dict[str, str]`) is accepted via `extra="allow"`: per-patch
solid names when a face has been subdivided (key = patch role, value = solid
name). Supersedes `inletSolidName`/`outletSolidName` when non-empty.

### `ServerPhysics` {#serverphysics}

(`cfdrun_schema.py:64`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `volumeFlowRate` | `float` \| `null` | no | `0.5` | Volumetric airflow through the server in m³/s. |
| `power` | `float` \| `null` | no | `1000.0` | Server heat dissipation in watts. |
| `temperatureRise` | `float` \| `null` | no | `null` | Inlet-to-outlet temperature rise in K. Used when `serverBCMode == "tempRiseAndFlow"` or `"powerAndTempRise"`. |
| `serverBCMode` | `"powerAndFlow"` \| `"tempRiseAndFlow"` \| `"powerAndTempRise"` \| `null` | no | `"powerAndFlow"` | Which combination of physics quantities drives the BC. `powerAndFlow`: specify power + flow. `tempRiseAndFlow`: specify temperature rise + flow. `powerAndTempRise`: specify power + temperature rise (flow derived). |
| `fixedTemperature` | `float` \| `null` | no | `null` | Fixed surface temperature in °C applied to inlet or outlet face (nonFan only). When null the face uses an adiabatic wall BC. |
| `fixedTempFace` | `"inlet"` \| `"outlet"` \| `null` | no | `"inlet"` | Which face receives the `fixedTemperature` BC (nonFan only). |

### `RackDoor` {#rackdoor}

Perforated-baffle door fitted to a rack face (`cfdrun_schema.py:136`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | `bool` | no | `true` | When `false`, this door is not created in the mesh. |
| `perforated` | [`RackDoorPerforated`](#rackdoorperforated) | no | see sub-table | Physical description of the perforated plate. |
| `solidName` | `string` \| `null` | no | `null` | STL solid name for the door face. |

#### `RackDoorPerforated` {#rackdoorperforated}

Mirrors [`PerforatedConfig`](#perforatedconfig); kept as a separate class for
historical reasons but has identical semantics (`cfdrun_schema.py:124`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `shape` | `"circle"` \| `"rect"` \| `"diamond"` | no | `"circle"` | Hole shape. |
| `holeDiameterMm` | `float` | no | `3.0` | Effective hole diameter in millimetres. |
| `openAreaPct` | `float` | no | `64.0` | Open area percentage (0–100). |

### `ThroughZoneConfig` {#throughzoneconfig}

Controls whether airflow leaks through the rack frame between server bays
(formerly called `LeakageConfig` — see [Backward Compatibility](#backward-compatibility)).
(`cfdrun_schema.py:101`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | `bool` | no | `false` | When `false`, no through-zone geometry is generated for this rack. |
| `sigma` | `float` | no | `0.05` | Legacy global porosity value (v1.0 compatibility). Superseded by per-group `sigma` in `groups`. |
| `throughSlots` | `List[int]` | no | `[]` | Zero-based rack-unit indices where through-zone slots exist (used for geometry generation). |
| `groups` | `List[`[`ThroughZoneGroup`](#throughzonegroup)`]` | no | `[]` | Per-group through-zone definitions with individual `sigma` values (v1.1+). |

### `ThroughZoneGroup` {#throughzonegroup}

One contiguous zone of through-slot leakage with its own porosity
(`cfdrun_schema.py:87`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `startUnit` | `int` | no | `0` | Zero-based index of the bottom rack unit of this group. |
| `endUnit` | `int` | no | `0` | Zero-based index of the top rack unit of this group (inclusive). |
| `sigma` | `float` | no | `0.05` | Porous-zone porosity coefficient for this group (dimensionless, 0–1). |
| `stlSolidName` | `string` | no | `""` | STL solid name for this through-zone region. |

---

## ACU: `CFDExportACU` {#acu}

Air Cooling Unit with one or more supply and return face entries
(`cfdrun_schema.py:211`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` | **yes** | — | Path to the ACU's STL file relative to the project directory. |
| `geometry` | [`SimpleGeometry`](#simplegeometry) | no | see SimpleGeometry defaults | Bounding-box geometry of the ACU enclosure. |
| `supplyFaces` | `List[`[`ACUFaceEntry`](#acufaceentry)`]` | no | `[]` | Supply face array (v1.1+ format). If non-empty, takes precedence over legacy `supplyFace`. |
| `returnFaces` | `List[`[`ACUFaceEntry`](#acufaceentry)`]` | no | `[]` | Return face array (v1.1+ format). If non-empty, takes precedence over legacy `returnFace`. |
| `supplyFace` | [`ACUSupplyFace`](#acusupplyface) \| `null` | no | `null` | *Legacy v1.0.* Single supply face. Ignored when `supplyFaces` is non-empty. |
| `returnFace` | [`ACUReturnFace`](#acureturnface) \| `null` | no | `null` | *Legacy v1.0.* Single return face. Ignored when `returnFaces` is non-empty. |
| `wallSolidNames` | `List[string]` | no | `[]` | STL solid names mapped to adiabatic wall BC (ACU casing surfaces). |

**Helper methods** on `CFDExportACU` (available in Python code, not in JSON):
`get_supply_faces()`, `get_return_faces()`, `get_primary_supply_temperature()`,
`get_primary_supply_flow_rate()` — these normalise legacy and current formats
into a single `List[ACUFaceEntry]`. Always call these in runner code rather than
accessing the fields directly.

### `ACUFaceEntry` {#acufaceentry}

One supply or return face on an ACU (`cfdrun_schema.py:169`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `solidName` | `string` | no | `"supply"` | STL solid name for this face patch. |
| `patchId` | `string` \| `null` | no | `null` | Optional sub-patch identifier when the face has been split into regions. |
| `face` | `string` \| `null` | no | `null` | Orientation hint (e.g. `"front"`, `"top"`); used by some downstream consumers. |
| `physics` | [`ACUFacePhysics`](#acufacephysics) | no | see sub-table | Thermal and flow physics for this face. |

### `ACUFacePhysics` {#acufacephysics}

(`cfdrun_schema.py:156`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `temperature` | `float` \| `null` | no | `null` | Supply air temperature in °C (supply faces only). `null` means the temperature is computed by the ACU control model. |
| `volumeFlowRate` | `float` | no | `4.0` | Volumetric airflow in m³/s. |
| `velocity` | `float` | no | `2.0` | Face-normal velocity in m/s (alternative flow specification). |
| `coolingCapacity` | `float` \| `null` | no | `null` | Total cooling capacity in kW (used by the ACU control loop). |
| `coolantTemperature` | `float` \| `null` | no | `null` | Chilled-water inlet temperature in °C. |
| `setPointTemperature` | `float` \| `null` | no | `null` | Return-air temperature setpoint in °C (for the ACU control model). |
| `minSupplyTemperature` | `float` \| `null` | no | `null` | *Legacy.* Minimum supply temperature in °C. Prefer `coolantTemperature` in new exports. |
| `ratedReturnTemp` | `float` \| `null` | no | `null` | Rated return-air temperature in °C. Used together with `coolingPerformanceTable` to model ACU capacity derating. |
| `coolingPerformanceTable` | `List[dict]` \| `null` | no | `null` | Lookup table of `[{returnTemp, capacity}]` entries for ACU capacity as a function of return temperature. |
| `timeVarying` | `Dict[str, Any]` | no | `{}` | Time-varying overrides for physics fields. Keys are field names; values are lists of [`TimeVaryingEntry`](#timevaryingentry). |

### Legacy ACU Face Models {#acu-legacy}

These classes exist only for backward compatibility with v1.0 exports and are
never written by the current frontend.

#### `SupplyFacePhysics` {#supplyfacephysics}

(`cfdrun_schema.py:177`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `temperature` | `float` | no | `15.0` | Supply air temperature in °C. |
| `volumeFlowRate` | `float` | no | `4.0` | Volumetric airflow in m³/s. |

#### `ReturnFacePhysics` {#returnfacephysics}

(`cfdrun_schema.py:182`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `volumeFlowRate` | `float` | no | `4.0` | Volumetric airflow in m³/s. |

#### `ACUSupplyFace` {#acusupplyface}

(`cfdrun_schema.py:186`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `solidName` | `string` | no | `"supply"` | STL solid name for the supply face patch. |
| `patchId` | `string` \| `null` | no | `null` | Optional sub-patch identifier. |
| `physics` | [`SupplyFacePhysics`](#supplyfacephysics) | no | see sub-table | Legacy supply face physics. |

#### `ACUReturnFace` {#acureturnface}

(`cfdrun_schema.py:193`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `solidName` | `string` | no | `"return"` | STL solid name for the return face patch. |
| `patchId` | `string` \| `null` | no | `null` | Optional sub-patch identifier. |
| `physics` | [`ReturnFacePhysics`](#returnfacephysics) | no | see sub-table | Legacy return face physics. |

---

## Baffle: `CFDExportBaffle` {#baffle}

Interior panel — either a solid wall or a perforated baffle creating a
pressure drop (`cfdrun_schema.py:332`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` | **yes** | — | Path to the baffle STL file. |
| `geometry` | [`BaffleGeometry`](#bafflegeometry) | no | see sub-table | Geometric description of the baffle panel. |
| `physics` | [`BafflePhysics`](#bafflephysics) | no | `baffleType: "wall"` | Default physics applied to all regions of the baffle. |
| `seedConfig` | [`SeedConfig`](#seedconfig) \| `null` | no | `null` | Fluid seed point for the primary baffle region. |
| `wallRegionSeedConfigs` | `Dict[string, `[`SeedConfig`](#seedconfig)`]` | no | `{}` | Per-solid seed configurations (key = solid name) for multi-region baffles. |
| `patchPhysics` | `Dict[string, `[`BafflePhysics`](#bafflephysics)`]` | no | `{}` | Per-solid physics overrides (key = solid name). Entries override the top-level `physics` for that patch. |

### `BaffleGeometry` {#bafflegeometry}

(`cfdrun_schema.py:326`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `anchor` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:0}` | World-space origin of the baffle (metres). |
| `size` | `Dict[str, float]` | no | `{}` | Dimensions dictionary (metres). Typical keys: `W`, `H` for a vertical panel. |
| `orientation` | `float` | no | `0.0` | Yaw rotation around the Y-axis in degrees. |

### `BafflePhysics` {#bafflephysics}

(`cfdrun_schema.py:319`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `baffleType` | `"wall"` \| `"perforated"` | no | `"wall"` | `wall`: no flow passes through. `perforated`: porous baffle with Idelchik pressure-loss model. |
| `porosity` | `float` | no | `0.0` | *Legacy.* Open-area fraction (0–100 percent). Retained for backward compatibility; superseded by `perforated.openAreaPct`. |
| `perforated` | [`PerforatedConfig`](#perforatedconfig) \| `null` | no | `null` | Full perforated-plate description. When present, supersedes the legacy `porosity` scalar. Required when `baffleType == "perforated"`. |
| `seedConfig` | [`SeedConfig`](#seedconfig) \| `null` | no | `null` | Optional seed override stored inline with the physics (alternate location; prefer top-level `seedConfig`). |

---

## Floor: `CFDExportFloor` {#floor}

Raised-floor panel with a default boundary condition and optional individual
perforated openings (tiles) (`cfdrun_schema.py:399`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` | **yes** | — | Path to the floor panel STL file. |
| `geometry` | [`FloorGeometry`](#floorgeometry) | no | see sub-table | Bounding-box geometry of the floor panel. |
| `roomBoundaryType` | `"none"` \| `"top"` \| `"bottom"` | no | `"none"` | Whether this panel is the room top (`"top"`), room bottom (`"bottom"`), or an intermediate raised-floor panel (`"none"`). Used by the runner to assign outer-boundary vs internal-baffle BCs. |
| `defaultBC` | `"wall"` \| `"wallBaffle"` \| `"perforated"` | no | `"wall"` | Boundary condition applied to the remaining floor area not covered by `openings`. |
| `defaultBCPorosity` | `float` | no | `0.0` | *Legacy.* Open-area percentage for the default BC. Superseded by `defaultBCPerforated`. |
| `defaultBCPerforated` | [`FloorDefaultBCPerforated`](#floordefaultbcperforated) \| `null` | no | `null` | Full perforated-plate description for the default floor BC. When present, supersedes `defaultBCPorosity`. |
| `defaultBCSeedConfig` | [`SeedConfig`](#seedconfig) \| `null` | no | `null` | Single seed for the primary default-BC region. |
| `defaultBCSeedConfigs` | `Dict[string, `[`SeedConfig`](#seedconfig)`]` | no | `{}` | Per-solid seeds for multi-region default-BC areas (key = solid name). |
| `openings` | `List[`[`FloorOpening`](#flooropening)`]` | no | `[]` | Individual perforated tiles or other opening types on this floor panel. |
| `wallSolidNames` | `List[string]` | no | `[]` | Solid names mapped to adiabatic wall BC (e.g., supports, trim). |

### `FloorGeometry` {#floorgeometry}

(`cfdrun_schema.py:383`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `anchor` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:0}` | World-space origin of the floor panel (metres). |
| `size` | `Dict[str, float]` | no | `{}` | Dimensions dictionary (metres). Typical keys: `W`, `D`. |

### `FloorDefaultBCPerforated` {#floordefaultbcperforated}

Perforated-plate description for the remaining floor area. Mirrors
[`PerforatedConfig`](#perforatedconfig) (`cfdrun_schema.py:388`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `shape` | `"circle"` \| `"rect"` \| `"diamond"` | no | `"circle"` | Hole shape. |
| `holeDiameterMm` | `float` | no | `3.0` | Effective hole diameter in millimetres. |
| `openAreaPct` | `float` | no | `64.0` | Open area percentage (0–100). |

### `FloorOpening` {#flooropening}

One individual opening (tile) on the floor panel (`cfdrun_schema.py:376`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `solidName` | `string` | **yes** | — | STL solid name for this opening's patch. |
| `patchId` | `string` \| `null` | no | `null` | Optional sub-patch identifier. |
| `physics` | [`FloorOpeningPhysics`](#flooropeningphysics) | no | `type: "wall"` | Physical type and porosity of this opening. |
| `seedConfig` | [`SeedConfig`](#seedconfig) \| `null` | no | `null` | Fluid seed point for this opening's region. |

### `FloorOpeningPhysics` {#flooropeningphysics}

(`cfdrun_schema.py:370`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | `"wall"` \| `"wallBaffle"` \| `"perforated"` | no | `"wall"` | Boundary condition type. `wallBaffle`: thin internal baffle. `perforated`: porous tile with Idelchik model. |
| `porosity` | `float` | no | `0.0` | *Legacy.* Open-area percentage (0–100). Superseded by `perforated.openAreaPct`. |
| `perforated` | [`PerforatedConfig`](#perforatedconfig) \| `null` | no | `null` | Full perforated-plate description. Supersedes `porosity` when present. |

---

## Wall: `CFDExportWall` {#wall}

Outer room boundary surface (`cfdrun_schema.py:412`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` \| `null` | no | `null` | Path to the wall STL file. May be null when the room envelope is generated procedurally. |
| `geometry` | [`SimpleGeometry`](#simplegeometry) | no | see SimpleGeometry defaults | Bounding-box geometry of the wall surface. |
| `solidNames` | `List[string]` | no | `[]` | STL solid names that belong to this wall definition. |

---

## Partition Wall: `CFDExportPartitionWall` {#partition-wall}

Interior partition wall that divides the fluid domain. May contain holes
(e.g., cable pass-throughs, vent openings) with optional leakage physics
(`cfdrun_schema.py:363`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` | **yes** | — | Path to the partition wall STL file. |
| `geometry` | [`PartitionWallGeometry`](#partitionwallgeometry) | no | see sub-table | Geometry including quaternion rotation. |
| `wallSolidNames` | `List[string]` | no | `[]` | Solid names mapped to adiabatic wall BC (the solid wall regions). |
| `holes` | `List[`[`PartitionWallHole`](#partitionwallhole)`]` | no | `[]` | Holes / openings cut through the partition. |

### `PartitionWallGeometry` {#partitionwallgeometry}

Extends [`SimpleGeometry`](#simplegeometry) with a quaternion for arbitrary
3-D orientation (replaces the scalar `orientation` yaw angle for complex
rotations) (`cfdrun_schema.py:359`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `anchor` | [`Point3D`](#point3d) | no | `{x:0, y:0, z:0}` | World-space origin (metres). |
| `size` | `Dict[str, float]` | no | `{}` | Dimensions dictionary (metres). |
| `orientation` | `float` | no | `0.0` | Yaw rotation in degrees (used when `transformQuaternion` is identity). |
| `transformQuaternion` | [`Quaternion4`](#quaternion4) | no | identity `{x:0,y:0,z:0,w:1}` | Full 3-D rotation quaternion. When non-identity, overrides `orientation`. |

### `PartitionWallHole` {#partitionwallhole}

One opening in the partition wall (`cfdrun_schema.py:350`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `patchId` | `string` | **yes** | — | Unique patch identifier within this partition wall. |
| `shape` | `"rectangle"` \| `"circle"` | no | `"rectangle"` | Geometric shape of the hole. |
| `name` | `string` | no | `""` | Human-readable label for this hole. |
| `zoneName` | `string` | no | `""` | OpenFOAM zone name assigned to this hole region. |
| `zoneStlFile` | `string` \| `null` | no | `null` | Path to the STL file representing the hole zone geometry. |
| `physics` | [`PartitionWallHolePhysics`](#partitionwallholephysics) | no | see sub-table | Leakage physics for this hole. |

### `PartitionWallHolePhysics` {#partitionwallholephysics}

(`cfdrun_schema.py:341`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `resistanceEnabled` | `bool` | no | `false` | *Legacy.* Formerly controlled whether a source-term resistance was applied. No longer the source of truth — retained for backward compatibility with old `.cfdproj` files. |
| `leakageRate` | `float` | no | `100.0` | Percentage of the nominal flow that passes through this hole (0–100). Values below 100 enable a porous source term; 100 = fully open. |

---

## Wall Object: `CFDExportWallObject` {#wall-object}

Wall-mounted object with one or more heat-emitting surface patches (e.g., PDU,
UPS, in-row cooler casing) (`cfdrun_schema.py:277`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` | **yes** | — | Path to the wall object STL file. |
| `geometry` | [`SimpleGeometry`](#simplegeometry) | no | see SimpleGeometry defaults | Bounding-box geometry of the object. |
| `heatPatches` | `List[`[`HeatPatch`](#heatpatch)`]` | no | `[]` | Patches that emit heat or have a fixed temperature. |
| `wallSolidNames` | `List[string]` | no | `[]` | Solid names mapped to adiabatic wall BC (non-heating surfaces). |

### `HeatPatch` {#heatpatch}

One heat-emitting or fixed-temperature surface patch on a wall object
(`cfdrun_schema.py:271`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `solidName` | `string` | **yes** | — | STL solid name for this patch. |
| `patchId` | `string` \| `null` | no | `null` | Optional sub-patch identifier. |
| `physics` | [`HeatPatchPhysics`](#heatpatchphysics) | no | see sub-table | Thermal physics for this patch. |

### `HeatPatchPhysics` {#heatpatchphysics}

(`cfdrun_schema.py:266`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `heatPower` | `float` | no | `0.0` | Total heat flux in watts applied to this patch. |
| `fixedTemperature` | `float` \| `null` | no | `null` | Fixed surface temperature in °C. When set, overrides `heatPower` and applies a Dirichlet temperature BC. |

---

## Heat Source: `CFDExportHeatSource` {#heat-source}

Standalone volumetric or surface heat source (for equipment not modelled as
a rack or wall object) (`cfdrun_schema.py:284`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `stlFile` | `string` | **yes** | — | Path to the heat source STL file. |
| `geometry` | [`SimpleGeometry`](#simplegeometry) | no | see SimpleGeometry defaults | Bounding-box geometry of the heat source. |
| `heatMode` | `"power"` \| `"temperature"` | no | `"power"` | `power`: apply `totalPower` as a uniform heat flux. `temperature`: apply `fixedTemperature` as a Dirichlet BC. |
| `totalPower` | `float` | no | `100.0` | Total heat output in watts (used when `heatMode == "power"`). |
| `fixedTemperature` | `float` \| `null` | no | `null` | Fixed surface temperature in °C (used when `heatMode == "temperature"`). |
| `wallSolidNames` | `List[string]` | no | `[]` | Solid names mapped to adiabatic wall BC (non-heat surfaces). |
| `timeVarying` | `Dict[str, Any]` | no | `{}` | Time-varying overrides for physics fields (transient simulations). |

---

## Sensor Point: `SensorPoint` {#sensor-point}

Virtual temperature-sensor probe. Results are reported at these points in the
post-processing output (`cfdrun_schema.py:50`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `id` | `string` | no | `""` | Unique sensor identifier. |
| `x` | `float` | no | `0.0` | X-coordinate of the probe location (metres). |
| `y` | `float` | no | `0.0` | Y-coordinate of the probe location (metres). |
| `z` | `float` | no | `0.0` | Z-coordinate of the probe location (metres). |

---

## Mesh Configuration: `MeshConfig` {#mesh-configuration}

Controls all snappyHexMesh parameters (`cfdrun_schema.py:418`).

**Important — 2026-05-08 simplification:** The old per-component surface and
feature refinement levels — `rackRefinementLevel`, `serverFaceRefinementLevel`,
`baffleRefinementLevel`, `rackDoorRefinementLevel`, `serverFaceFeatureLevel`,
`blankingPlateFeatureLevel`, and the `rackPartition*` / `rackThroughZone*`
families (including their per-unit variants) — were all **removed as declared
fields** (`cfdrun_schema.py:428-433`). They are replaced by a single
`rackRegionLevel` integer applied as a `searchableBox` refinement region per
rack. Old `.cfdrun` files that still carry those removed keys are silently
accepted via `extra="allow"` and ignored by the runner.

### Background Mesh

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `baseSize` | `float` | no | `0.34` | Background block-mesh cell size in metres (`cfdrun_schema.py:419`). |
| `detectBaseSize` | `float` \| `null` | no | `null` | Reference scale used by the UI's auto-detect level formula (`ceil(log2(X / (H/2)))`). When null, `baseSize` is used. Decouples level selection from actual mesh resolution (`cfdrun_schema.py:425`). |
| `maxGlobalCells` | `int` | no | `5000000` | Global upper bound on cell count passed to snappyHexMesh `castellatedMeshControls` (`cfdrun_schema.py:446`). |
| `maxLocalCells` | `int` | no | `5000000` | Per-process upper bound on cell count during parallel mesh (`cfdrun_schema.py:447`). |

### Parallelism

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `parallelMesh` | `bool` | no | `true` | When `true`, snappyHexMesh runs in parallel using `mpirun` (`cfdrun_schema.py:440`). |
| `meshProcess` | `int` | no | `16` | Number of MPI ranks for parallel meshing (`cfdrun_schema.py:441`). |

### Room / Background Refinement

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `refinementLevel` | `List[int]` | no | `[1, 1]` | Global background refinement `[min, max]` applied to the room envelope STL (`cfdrun_schema.py:426`). |

### Rack Region Refinement

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `rackRegionLevel` | `int` | no | `3` | Single level applied as a `searchableBox` `refinementRegions` entry for each rack. Replaces all the old per-component surface+feature level fields (`cfdrun_schema.py:434`). |

### Partition Wall Refinement

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `partitionWallRefinementLevel` | `List[int]` | no | `[1, 1]` | Surface refinement `[min, max]` for interior partition-wall STLs (`cfdrun_schema.py:435`). |
| `partitionWallFeatureLevel` | `int` | no | `2` | Feature-line refinement level for partition-wall STLs (`cfdrun_schema.py:436`). |

### Floor Refinement

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `floorRefinementLevel` | `List[int]` | no | `[1, 1]` | Surface refinement `[min, max]` for floor panel STLs (`cfdrun_schema.py:438`). |
| `floorFeatureLevel` | `int` | no | `2` | Feature-line refinement level for floor panel STLs (`cfdrun_schema.py:439`). |

---

## Simulation Configuration: `SimulationConfig` {#simulation-configuration}

Controls the OpenFOAM solver run (`cfdrun_schema.py:457`).

### Steady-State Settings

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `endTime` | `int` | no | `500` | Number of solver iterations (`controlDict endTime`). |
| `writeInterval` | `int` | no | `100` | Write results every N iterations (`controlDict writeInterval`). |

### Transient Settings

These fields apply when `solverType == "transient"`.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `endTimeSeconds` | `float` | no | `300.0` | Physical end time in seconds for transient runs. |
| `deltaT` | `float` | no | `0.1` | Initial time-step size in seconds. |
| `maxDeltaT` | `float` | no | `1.0` | Maximum time-step size in seconds (used when `adjustTimeStep` is `true`). |
| `maxCo` | `float` | no | `1.0` | Maximum Courant number for automatic time-step adjustment. |
| `adjustTimeStep` | `bool` | no | `true` | When `true`, the solver adjusts `deltaT` to maintain `maxCo`. |
| `writeIntervalSeconds` | `float` | no | `10.0` | Write results every N seconds of physical time. |
| `nOuterCorrectors` | `int` | no | `1` | Number of outer PIMPLE corrector loops per time step. |
| `nCorrectors` | `int` | no | `2` | Number of inner pressure-velocity corrector iterations per outer loop. |
| `nNonOrthogonalCorrectors` | `int` | no | `0` | Number of non-orthogonal corrector passes for non-orthogonal meshes. |
| `nonOrthoCorrection` | `bool` | no | `false` | When `true`, enables non-orthogonal correction in pressure solve. |
| `ddtScheme` | `string` | no | `"Euler"` | Time-derivative discretisation scheme (e.g. `"Euler"`, `"CrankNicolson"`). |
| `crankNicolsonCoeff` | `float` | no | `0.9` | Blending coefficient for the Crank-Nicolson scheme (0 = Euler, 1 = pure CN). |

### Output Format

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `doublePrecisionWrite` | `bool` | no | `false` | When `true`, write field files in double precision. |
| `writePrecision` | `string` | no | `"single"` | OpenFOAM `writePrecision` keyword for field output precision. |

### Solver Selection and Physics

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `solverType` | `string` | no | `"steady"` | Solver variant. `"steady"` maps to `buoyantBoussinesqSimpleFoam`; `"transient"` maps to `buoyantBoussinesqPimpleFoam`. |
| `solverPreset` | `string` | no | `"balanced"` | Named preset controlling relaxation factors, schemes, and residual targets. Current values: `"fast"`, `"balanced"`, `"accurate"`. Note: the frontend `.cfdproj` may default to `"fast"`; the Pydantic schema default is `"balanced"` (`cfdrun_schema.py:483`). |
| `roomTemperature` | `float` | no | `22.0` | Ambient/initial room temperature in °C used as the fluid initialisation value. |
| `gravity` | `Dict[str, float]` | no | `{"x":0,"y":-9.81,"z":0}` | Gravitational acceleration vector in m/s² (Y-up convention). |
| `physical_properties` | `Dict[str, Any]` | no | `{}` | Advanced override of fluid physical properties (density, viscosity, thermal expansion, etc.). When non-empty, entries replace the runner's built-in Boussinesq defaults. |
| `unitTemperature` | `string` | no | `"C"` | Temperature unit for all temperature fields in this config. `"C"` = Celsius; `"K"` = Kelvin. |

### Parallelism

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `parallelSolve` | `bool` | no | `false` | When `true`, the runner decomposes the domain and runs the solver in parallel using `mpirun` with `solveProcess` ranks (`cfdrun_schema.py:475`). |
| `solveProcess` | `int` | no | `1` | Number of MPI ranks for the solver. `1` = serial solve. |

### Monitoring and Convergence

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `residualControl` | [`ResidualControl`](#residualcontrol) | no | see sub-table | Per-variable convergence thresholds. |
| `schemes` | `Dict[str, str]` | no | `{}` | Override map for OpenFOAM `fvSchemes` entries. Keys are scheme category names; values are scheme strings. |
| `relaxationFactors` | `Dict[str, float]` | no | `{}` | Override map for OpenFOAM `fvSolution.relaxationFactors`. Keys are field names. |
| `monitoring` | `Dict[str, Any]` | no | `{}` | Runner-side monitoring configuration (probe locations, field sampling, etc.). Exact sub-keys are runner-defined. |

### `ResidualControl` {#residualcontrol}

(`cfdrun_schema.py:450`)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `p_rgh` | `float` \| `string` | no | `"1e-4"` | Convergence residual for modified pressure `p_rgh`. |
| `U` | `float` \| `string` | no | `"1e-5"` | Convergence residual for velocity field `U`. |
| `T` | `float` \| `string` | no | `"1e-5"` | Convergence residual for temperature field `T`. |
| `k_epsilon` | `float` \| `string` | no | `"1e-4"` | Convergence residual for turbulence quantities (`k` and `epsilon`). |

---

## Backward Compatibility {#backward-compatibility}

### `LeakageConfig` / `LeakageGroup` Aliases

Prior to 2026-04-17, the through-zone classes were called `LeakageConfig` and
`LeakageGroup`. Python aliases are provided (`cfdrun_schema.py:98,112`):

```python
LeakageConfig = ThroughZoneConfig
LeakageGroup  = ThroughZoneGroup
```

Existing `.cfdproj` files that contain `leakage` objects serialised under the
old key names will silently fall back to schema defaults for the through-zone
fields — the runner reads them correctly, but users need to re-save from the UI
to migrate to the new `ThroughZoneConfig`/`ThroughZoneGroup` keys.

### ACU `supplyFace` / `returnFace` (Singular, Legacy v1.0)

Old exports contained single-face objects:

```json
"supplyFace": { "solidName": "supply", "physics": { "temperature": 15.0, "volumeFlowRate": 4.0 } },
"returnFace": { "solidName": "return", "physics": { "volumeFlowRate": 4.0 } }
```

Current exports use arrays:

```json
"supplyFaces": [{ "solidName": "supply", "physics": { "temperature": 15.0, "volumeFlowRate": 4.0 } }],
"returnFaces": [{ "solidName": "return", "physics": { "volumeFlowRate": 4.0 } }]
```

The helper methods `get_supply_faces()` and `get_return_faces()` on
`CFDExportACU` normalise both formats — always call these in runner code rather
than accessing the fields directly.

### Porosity Fields Alongside `perforated` Objects

Three classes retain a legacy `porosity` scalar field alongside the modern
[`PerforatedConfig`](#perforatedconfig) object:

| Class | Legacy field | Modern field |
|-------|-------------|--------------|
| `BafflePhysics` | `porosity` (0–100 %) | `perforated.openAreaPct` |
| `FloorOpeningPhysics` | `porosity` (0–100 %) | `perforated.openAreaPct` |
| `CFDExportFloor` | `defaultBCPorosity` (0–100 %) | `defaultBCPerforated.openAreaPct` |

When the `perforated` object is present it supersedes the legacy scalar.
The legacy scalar is still read by the classifier that decides "wall vs
perforated" at the `createBaffles` stage for old exports that lack the
`perforated` block.

### Old Per-Component Mesh Refinement Keys (Removed 2026-05-08)

The following keys were formerly declared Pydantic fields in `MeshConfig`. They
were removed on 2026-05-08 and replaced by `rackRegionLevel`. They are still
accepted silently via `extra="allow"` for old `.cfdrun` files, but are no
longer consumed by the runner:

The canonical list is recorded in the `MeshConfig` source comment
(`cfdrun_schema.py:428-433`): `rackRefinementLevel`, `serverFaceRefinementLevel`,
`baffleRefinementLevel`, `rackDoorRefinementLevel`, `serverFaceFeatureLevel`,
`blankingPlateFeatureLevel`, and the `rackPartition*` and `rackThroughZone*` field
families (which included surface-, feature-, and per-unit-level variants).

### `CFDBaseModel` and Forward Compatibility

All schema classes inherit from `CFDBaseModel`:

```python
class CFDBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")
```

Setting `extra="allow"` means any field not declared in the Pydantic model is
silently accepted and stored. This allows new UI-emitted fields to round-trip
through schema validation without errors, even before they are formally declared
in the Pydantic class.
