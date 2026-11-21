# .cfdrun Format

A `.cfdrun` file is the run manifest that connects the editor to the CFD backend. It describes everything the backend needs for one air-cooling run: the exported STL files and the boundary condition bound to every patch, source terms, mesh settings and solver settings.

!!! info "Current format: schemaVersion 4 (as of 2026-10-21)"
    The runner accepts **schemaVersion 2, 3 and 4**; anything else (including the unversioned v1 files) is refused with a "re-export from the editor" error. The React editor exports **v4**; the legacy HTML editor exports v2.

    - **v2** — uid-keyed `boundaryBindings` / `porousBindings` / `sourceTerms`, root `meshConfig` / `simulationConfig`, and the transitional `meshGeometry` blob.
    - **v3** — v2 plus a required `transient` timeline and per-binding time schedules (the first transient format; no longer produced).
    - **v4** — one `simulationConfig`, the root one (the blob no longer carries its own copy); `transient` and schedules only for transient air runs.

    Field tables: [.cfdrun reference](../reference/cfdrun-v2-schema.md) (its §15 covers v3 and v4). The backend validates with the Pydantic model `CfdrunV2` in `src/backend/aircooling/runner/cfdrun_schema.py` and materialises the bindings with `cfdrun_reader.resolve_run`.

!!! note
    The legacy [v1 schema reference](../reference/legacy/cfdrun-schema.md) documents the old manifest shape. That shape lives on as the `meshGeometry` block described below.

---

## What is a .cfdrun File?

Whenever a mesh or solve starts — from the editor, the REST API or the [scripted API](../reference/scripted-api.md) — the editor's export code builds the manifest from the **live** project (materialised at t = 0 for a v5 project; a transient air run also gets schedules sampled from the timeline), exports the STL files, and the server writes both into the case:

```
case/<name>/Mesh/constant/triSurface/*.stl     # one multi-solid ASCII STL per group
case/<name>/model/<name>.cfdrun                # the manifest
```

The backend then:

1. Validates the manifest against `CfdrunV2` (strict: undeclared fields are errors).
2. Resolves the bindings into an in-memory boundary-condition registry (`cfdrun_reader.resolve_run`).
3. Reads the geometry blob (`meshGeometry`) for the mesh pipeline and writes the mesh dictionaries.
4. Meshes with the selected engine (`dcCart` by default, see [Mesh engines](../reference/mesh-engines.md)), checks the mesh strictly, audits the boundary conditions and solves.

Each solve archives the manifest it executed under `case/<name>/log/<timestamp>_<name>/model/`.

The `.cfdrun` contains no camera, UI or undo state; those belong to the editor and its `.cfdproj` project file (see [.cfdproj Format](cfdproj-format.md)).

---

## How It Is Generated

```
User edits the project in the editor (React)
        │
        ▼
User starts Mesh / Solve
        │
        ▼
buildManifestV2()              ← src/frontend/react/src/legacy/cfdrunBuild.ts
  ├─ cfdrunExport.buildManifest()   bindings, source terms, configs (shared module)
  ├─ buildCfdrunJson()              the meshGeometry blob (shared module cfdrun_build.js)
  └─ schemaVersion = 4
        │
        ▼
POST /api/run-mesh  →  server writes model/<name>.cfdrun + triSurface/*.stl  →  STLGenRunner  →  OpenFOAM
```

The same export modules run headless for the scripted API (`src/frontend/react/src/headless/`), so a scripted run and a click in the editor produce the same manifest.

!!! tip
    When debugging a run, read the manifest the run executed (`log/<timestamp>_<name>/model/*.cfdrun`), not the `.cfdproj`: the project is only written when the user saves.

---

## The `meshGeometry` Block

The mesh pipeline still reads the room and equipment geometry from a transitional block, `meshGeometry`, in the shape of the old v1 manifest. The rest of this page describes that block: its keys are what the runner uses for the room enclosure, rack and ACU refinement, baffles, floors and partition walls. The authoritative boundary conditions are the manifest's `boundaryBindings`; the `physics` values inside this block mirror them for the mesh pipeline. In a v4 manifest the block has no `simulationConfig` (the root one is used).

A minimal annotated example of the block with one rack and one ACU:

```json
{
  "version": "1.0",
  "source": "frontend",
  "projectName": "MyCFDProject",

  "room": {
    "height": 3.6,
    "plane": [
      {"x": 0, "y": 0, "z": 0},
      {"x": 10, "y": 0, "z": 0},
      {"x": 10, "y": 0, "z": 6},
      {"x": 0, "y": 0, "z": 6}
    ],
    "locationInMesh": {"x": 5, "y": 1.5, "z": 3}
  },

  "racks": {
    "Rack_01": {
      "stlFile": "Rack_01.stl",
      "geometry": {
        "anchor": {"x": 1.0, "y": 0.0, "z": 1.0},
        "size": {"W": 0.6, "D": 1.0, "H": 1.867},
        "orientation": 0.0,
        "unitCount": 42
      },
      "servers": [
        {
          "id": "srv_001",
          "type": "fanServer",
          "startSlot": 0,
          "spanSlots": 2,
          "inletSolidName": "srv_001_inlet",
          "outletSolidName": "srv_001_outlet",
          "physics": {
            "volumeFlowRate": 0.05,
            "power": 500
          }
        }
      ],
      "leakage": {"enabled": false}
    }
  },

  "acus": {
    "ACU_01": {
      "stlFile": "ACU_01.stl",
      "geometry": {
        "anchor": {"x": 0, "y": 0, "z": 2.5},
        "size": {"W": 0.6, "D": 0.3, "H": 2.0}
      },
      "supplyFaces": [
        {
          "solidName": "ACU_01_supply",
          "physics": {"temperature": 15.0, "volumeFlowRate": 2.0}
        }
      ],
      "returnFaces": [
        {
          "solidName": "ACU_01_return",
          "physics": {"volumeFlowRate": 2.0}
        }
      ]
    }
  },

  "baffles": {},
  "floors": {},
  "walls": {},
  "partitionWalls": {},
  "wallObjects": {},
  "heatSources": {},
  "sensorPoints": [],

  "meshConfig": {
    "meshEngine": "dcCart",
    "baseSize": 0.25,
    "refinementLevel": [1, 1],
    "rackRegionLevel": 3,
    "parallelMesh": true,
    "meshProcess": 16,
    "maxGlobalCells": 5000000
  },

  "simulationConfig": {
    "endTime": 500,
    "writeInterval": 100,
    "solverType": "steady",
    "roomTemperature": 22.0,
    "residualControl": {
      "p_rgh": "1e-4",
      "U": "1e-5",
      "T": "1e-5",
      "k_epsilon": "1e-4"
    }
  }
}
```

The keys of the block are:

| Key | Description |
|-----|-------------|
| `version`, `source`, `projectName` | Informational metadata |
| `room` | Room envelope geometry |
| `racks` | Map of rack ID → rack definition |
| `acus` | Map of ACU ID → ACU definition |
| `baffles` | Interior baffle panels |
| `floors` | Raised-floor panels |
| `walls` | Room perimeter wall surfaces |
| `partitionWalls` | Interior partition walls |
| `wallObjects` | Custom objects with heat patches |
| `heatSources` | Standalone volumetric heat sources |
| `sensorPoints` | Temperature monitoring probes |
| `meshConfig` | Mesh settings (the runner reads this copy) |
| `simulationConfig` | Solver settings (v2/v3 only; v4 derives it from the root) |

See [Top-Level: CFDExport](../reference/legacy/cfdrun-schema.md#cfdexport) for the full field table.

---

## Room

The `room` object defines the fluid domain envelope that the mesher fills with cells.

```json
"room": {
  "height": 3.6,
  "plane": [
    {"x": 0, "y": 0, "z": 0},
    {"x": 20, "y": 0, "z": 0},
    {"x": 20, "y": 0, "z": 10},
    {"x": 0, "y": 0, "z": 10}
  ],
  "locationInMesh": {"x": 10, "y": 1.5, "z": 5},
  "extraLocationsInMesh": []
}
```

**Key concepts:**

- **`height`** — Room height in metres along the Y-axis (Y-up convention). The floor is at Y=0; the ceiling is at Y=height.
- **`plane`** — The floor polygon vertices as a list of XZ-plane points. The polygon is automatically closed. For a rectangular room, provide four corners in order. Non-rectangular rooms require three or more vertices.
- **`locationInMesh`** — A point guaranteed to lie strictly *inside* the fluid domain (inside the room, not inside any equipment). The mesher (dcCartMesh or snappyHexMesh) uses this seed point to determine which cells to keep.
- **`extraLocationsInMesh`** — Additional seed points for isolated sub-regions (e.g., a raised-floor plenum disconnected from the main room volume).

!!! tip
    Place `locationInMesh` near the center of the room at mid-height (Y ≈ height/2). Never place it inside a rack, ACU, or wall — the mesher would keep the wrong region and produce an empty mesh.

**Coordinate convention:** The editor uses Y-up. X = width, Y = vertical (height), Z = depth. All coordinates are in metres.

See [Room: CFDExportRoom](../reference/legacy/cfdrun-schema.md#room) for the full field table.

---

## Racks

Each rack in the `racks` dict represents one physical server cabinet. The key is a unique rack identifier that becomes a prefix for OpenFOAM patch names.

```json
"Rack_A1": {
  "stlFile": "Rack_A1.stl",
  "geometry": {
    "anchor": {"x": 2.0, "y": 0.0, "z": 1.5},
    "size": {"W": 0.6, "D": 1.0, "H": 1.867},
    "orientation": 0.0,
    "unitCount": 42,
    "unitHeight": 0.04445,
    "slotWidthRatio": 0.8
  },
  "servers": [...],
  "leakage": {"enabled": false},
  "wallSolidNames": ["Rack_A1_frame"],
  "inletDoor": null,
  "outletDoor": null
}
```

### Geometry Fields

- **`anchor`** — World position of the rack's front-bottom-left corner (metres).
- **`size`** — Outer dimensions: `W` = width, `D` = depth, `H` = height.
- **`orientation`** — Yaw rotation around the Y-axis in degrees. 0° means the front face points toward +Z (cold aisle typically faces +Z or -Z).
- **`unitCount`** — Total U capacity of the rack (typically 42U).
- **`unitHeight`** — Height of one rack unit in metres (1U = 44.45 mm = 0.04445 m).
- **`slotWidthRatio`** — Fraction of rack width used by the active slot area (standard 19-inch mounting strips).

### Servers

The `servers` array lists all equipment entries installed in the rack. Each entry spans one or more rack units. There are two types:

#### fanServer

Active equipment with internal fans. Creates an airflow through-channel across the rack depth.

```json
{
  "id": "srv_A1_001",
  "type": "fanServer",
  "startSlot": 0,
  "spanSlots": 2,
  "inletSolidName": "srv_A1_001_inlet",
  "outletSolidName": "srv_A1_001_outlet",
  "physics": {
    "volumeFlowRate": 0.05,
    "power": 500.0
  }
}
```

- **`volumeFlowRate`** — Volumetric airflow through the server in m³/s.
- **`power`** — Heat dissipated by the server in watts.
- **`serverBCMode`** — Selects which physics fields are active: `"powerAndFlow"` (default), `"tempRiseAndFlow"`, or `"powerAndTempRise"`. The `temperatureRise` and `fixedTemperature` fields apply only in the latter two modes.

#### nonFan

Passive equipment without internal fans (storage arrays, patch panels, UPS modules). Bounded by the rack walls; no through-channel.

```json
{
  "id": "pdu_A1_001",
  "type": "nonFan",
  "startSlot": 38,
  "spanSlots": 2,
  "inletSolidName": null,
  "outletSolidName": null,
  "topSolidName": "pdu_A1_001_top",
  "physics": {
    "fixedTemperature": 40.0,
    "fixedTempFace": "inlet"
  }
}
```

- **`fixedTemperature`** — Surface temperature in °C applied as a Dirichlet boundary condition.
- **`fixedTempFace`** — Which face receives the fixed temperature (`"inlet"` or `"outlet"`).

!!! note
    `startSlot` and `spanSlots` are zero-based rack unit indices. Slot 0 is the bottom of the rack.

### Through-Zones (Leakage)

Through-zones model airflow that bypasses server inlets and leaks through blanking-panel gaps in the rack.

```json
"leakage": {
  "enabled": true,
  "groups": [
    {
      "startUnit": 10,
      "endUnit": 14,
      "sigma": 0.05,
      "stlSolidName": "Rack_A1_tz_10_14"
    }
  ]
}
```

- **`sigma`** — Porous-zone porosity coefficient (0–1). Smaller values = more resistance to flow through the gap.
- **`groups`** — Per-group through-zone definitions allow different leakage rates at different rack heights.

### Inlet and Outlet Doors

Racks can have perforated front (cold aisle) and rear (hot aisle) doors that create a pressure drop.

```json
"inletDoor": {
  "enabled": true,
  "perforated": {
    "shape": "circle",
    "holeDiameterMm": 3.0,
    "openAreaPct": 64.0
  },
  "solidName": "Rack_A1_inlet_door"
}
```

The `openAreaPct` and `holeDiameterMm` values are inputs to the Idelchik §8.2 pressure-loss formula.

See [Rack: CFDExportRack](../reference/legacy/cfdrun-schema.md#rack) for all rack fields.

---

## ACUs

Air Cooling Units use arrays of supply and return face entries (v1.1+ format). Each face is a named STL solid with associated physics.

```json
"ACU_North": {
  "stlFile": "ACU_North.stl",
  "geometry": {
    "anchor": {"x": 0.0, "y": 0.0, "z": 5.0},
    "size": {"W": 0.6, "D": 0.3, "H": 2.0},
    "orientation": 90.0
  },
  "supplyFaces": [
    {
      "solidName": "ACU_North_supply",
      "face": "front",
      "physics": {
        "temperature": 15.0,
        "volumeFlowRate": 4.0,
        "velocity": 2.0
      }
    }
  ],
  "returnFaces": [
    {
      "solidName": "ACU_North_return",
      "face": "back",
      "physics": {
        "volumeFlowRate": 4.0
      }
    }
  ],
  "wallSolidNames": ["ACU_North_casing"]
}
```

**Supply face physics:**

| Field | Meaning |
|-------|---------|
| `temperature` | Supply air temperature in °C |
| `volumeFlowRate` | Volume flow rate in m³/s |
| `velocity` | Face-normal velocity in m/s (alternative to flow rate) |
| `coolingCapacity` | Total cooling capacity in kW (for ACU control model) |
| `coolantTemperature` | Chilled-water inlet temperature in °C |
| `setPointTemperature` | Return-air temperature setpoint in °C |

**Return face physics:** Only `volumeFlowRate` and `velocity` are meaningful for return faces; `temperature` is ignored.

!!! tip
    An ACU can have multiple supply and/or return faces. This is useful for ACUs with multiple grilles on different faces. Each face entry references a different STL solid name.

!!! note
    The `face` field (`"front"`, `"back"`, etc.) is an orientation hint used by some downstream consumers but is not required for basic operation.

See [ACU: CFDExportACU](../reference/legacy/cfdrun-schema.md#acu) for all ACU fields.

---

## Baffles

Baffles are thin interior panels that divide the fluid domain. They can be solid walls or perforated plates.

```json
"ColdAisle_Baffle": {
  "stlFile": "ColdAisle_Baffle.stl",
  "geometry": {
    "anchor": {"x": 2.0, "y": 0.0, "z": 0.5},
    "size": {"W": 3.0, "H": 2.5},
    "orientation": 0.0
  },
  "physics": {
    "baffleType": "perforated",
    "perforated": {
      "shape": "circle",
      "holeDiameterMm": 4.0,
      "openAreaPct": 50.0
    }
  }
}
```

**`baffleType` values:**

- `"wall"` — Solid baffle, no flow passes through. Used for containment panels, end-of-row panels, ceiling tiles.
- `"perforated"` — Porous baffle with a pressure-drop model. Used for perforated tiles, mesh doors.

**`PerforatedConfig` fields:**

| Field | Description |
|-------|-------------|
| `shape` | Hole shape: `"circle"`, `"rect"`, or `"diamond"` |
| `holeDiameterMm` | Hole diameter in mm (inscribed-circle for non-circular shapes) |
| `openAreaPct` | Open area percentage (0–100) — determines the free-area ratio φ = openAreaPct/100 |

The perforated model uses the Idelchik §8.2 equation 8-4 to compute the pressure-loss coefficient from these three inputs.

!!! tip
    When `baffleType` is `"perforated"`, always supply the `perforated` block. The legacy `porosity` scalar (0–100 %) is retained for backward compatibility but provides less information than the full `PerforatedConfig`.

Multi-region baffles (baffles with patches having different physics) use `patchPhysics`:

```json
"physics": {"baffleType": "wall"},
"patchPhysics": {
  "solid_region_name": {"baffleType": "perforated", "perforated": {...}}
}
```

See [Baffle: CFDExportBaffle](../reference/legacy/cfdrun-schema.md#baffle) for all baffle fields.

---

## Floors

Floor panels represent raised-floor systems with optional perforated tile openings that allow under-floor airflow to enter the data center.

```json
"Floor_Row1": {
  "stlFile": "Floor_Row1.stl",
  "geometry": {
    "anchor": {"x": 1.0, "y": 0.0, "z": 1.0},
    "size": {"W": 4.0, "D": 3.0}
  },
  "roomBoundaryType": "none",
  "defaultBC": "wall",
  "openings": [
    {
      "solidName": "Floor_Row1_tile_A",
      "physics": {
        "type": "perforated",
        "perforated": {
          "shape": "circle",
          "holeDiameterMm": 3.0,
          "openAreaPct": 56.0
        }
      }
    },
    {
      "solidName": "Floor_Row1_tile_B",
      "physics": {"type": "wallBaffle"}
    }
  ]
}
```

**`roomBoundaryType`** — Whether this floor closes the room domain: `"none"` (default, an interior raised floor), `"top"` (ceiling boundary), or `"bottom"` (room floor boundary).

**`defaultBC`** — The boundary condition applied to floor area *not* covered by any opening:

| Value | Description |
|-------|-------------|
| `"wall"` | Solid floor — no airflow through |
| `"wallBaffle"` | Internal face pair (thin solid) — creates internal face regions |
| `"perforated"` | Uniformly perforated floor remaining area — uses `defaultBCPerforated` for plate description |

**Opening `type` values:**

Same three options as `defaultBC`. A `"perforated"` opening models an individual perforated tile with its own hole geometry and open-area percentage.

!!! tip
    Use `seedConfig` on floor openings if the mesher has trouble identifying the under-floor plenum region. The seed point should be placed just below the perforated tile.

See [Floor: CFDExportFloor](../reference/legacy/cfdrun-schema.md#floor) for all floor fields.

---

## Walls and Partition Walls

### Room Perimeter Walls

The `walls` dict contains the room boundary surfaces. These are typically adiabatic (no heat transfer) walls. The room envelope STL is generated procedurally by the backend from the `room.plane` polygon, but individual wall panels can be listed here for boundary condition overrides.

```json
"walls": {
  "RoomWall_North": {
    "stlFile": "RoomWall_North.stl",
    "geometry": {
      "anchor": {"x": 0, "y": 0, "z": 0},
      "size": {"W": 20.0, "H": 3.6}
    },
    "solidNames": ["RoomWall_North_surface"]
  }
}
```

### Interior Partition Walls

Partition walls divide the fluid domain into sub-regions. They may contain holes (cable pass-throughs, vent openings) with optional flow resistance.

```json
"partitionWalls": {
  "HotAisleWall": {
    "stlFile": "HotAisleWall.stl",
    "geometry": {
      "anchor": {"x": 5.0, "y": 0.0, "z": 0.0},
      "size": {"W": 3.0, "H": 3.0},
      "orientation": 90.0,
      "transformQuaternion": {"x": 0, "y": 0, "z": 0, "w": 1}
    },
    "wallSolidNames": ["HotAisleWall_solid"],
    "holes": [
      {
        "patchId": "p_001",
        "shape": "rectangle",
        "name": "CableCutout",
        "zoneName": "cutout_zone",
        "zoneStlFile": "HotAisleWall_cutout.stl",
        "physics": {
          "leakageRate": 100.0
        }
      }
    ]
  }
}
```

**Hole physics:**

- **`leakageRate`** — Percentage of nominal flow that passes through this hole (0–100). `100` = fully open. Values below 100 enable a porous source term that creates flow resistance.

!!! note
    The `transformQuaternion` field on partition walls allows arbitrary 3-D rotation (not just yaw). The unit quaternion `{x:0, y:0, z:0, w:1}` is the identity (no rotation). When non-identity, it overrides the scalar `orientation` field.

See [Partition Wall: CFDExportPartitionWall](../reference/legacy/cfdrun-schema.md#partition-wall) for all partition wall fields.

---

## Wall Objects and Heat Sources

### Wall Objects

Wall-mounted equipment (PDUs, UPS units, in-row coolers) with one or more heat-emitting surfaces.

```json
"wallObjects": {
  "PDU_Row1": {
    "stlFile": "PDU_Row1.stl",
    "geometry": {
      "anchor": {"x": 0.0, "y": 0.5, "z": 1.0},
      "size": {"W": 0.2, "D": 0.1, "H": 1.0}
    },
    "heatPatches": [
      {
        "solidName": "PDU_Row1_surface",
        "physics": {"heatPower": 200.0}
      }
    ],
    "wallSolidNames": ["PDU_Row1_casing"]
  }
}
```

- **`heatPower`** — Total heat flux in watts applied to this patch surface.
- **`fixedTemperature`** — If set, applies a Dirichlet temperature boundary condition (in °C) instead of a heat flux.

### Heat Sources

Standalone volumetric or surface heat sources for equipment not modelled as a rack or wall object:

```json
"heatSources": {
  "Transformer_01": {
    "stlFile": "Transformer_01.stl",
    "heatMode": "power",
    "totalPower": 5000.0,
    "wallSolidNames": ["Transformer_01_body"]
  }
}
```

- **`heatMode`**: `"power"` applies `totalPower` (W) as a uniform heat flux; `"temperature"` applies `fixedTemperature` (°C) as a Dirichlet BC.

See [Wall Object](../reference/legacy/cfdrun-schema.md#wall-object) and [Heat Source](../reference/legacy/cfdrun-schema.md#heat-source) for full field tables.

---

## Mesh Configuration

The `meshConfig` object controls the mesh pipeline. The editor writes it from `runDefaults.meshConfig` in both places (root and `meshGeometry`); the runner executes the `meshGeometry` copy. A typical dcCart configuration as the editor's product preset writes it:

```json
"meshConfig": {
  "meshEngine": "dcCart",
  "baseSize": 0.25,
  "refinementLevel": [1, 1],
  "rackRegionLevel": 3,
  "parallelMesh": true,
  "meshProcess": 8,
  "maxGlobalCells": 5000000,
  "weldTolerance": 0.0012,
  "firstLayerMinFraction": 0.3,
  "firstLayerMax": 0.005,
  "firstLayerSealGap": -1,
  "rackCellMinSize": 0.0175,
  "meshTimeLimitSeconds": 300
}
```

### Key Parameters

**`meshEngine`** — the program that builds the mesh: `dcCart` (default; the repository's cut-cell Cartesian mesher, MPI-native), or `snappy` (body-fitted snappyHexMesh). Absent or unknown values mean `dcCart`; `meshgen` (MeshGenerator) was retired on 2026-10-26 and is refused with that message. See [Mesh engines](../reference/mesh-engines.md). The older `meshMode` / `cartesianMode` / `structuredGrid` keys are ignored.

**`baseSize`** — background cell edge length in metres (`meshBaseSize` in the project). The product preset is 0.25 m, in line with the [6Sigma cell-size rule](../reference/sixsigma-cell-size-rule.md); smaller values increase the cell count roughly with the cube of the ratio, so check `maxGlobalCells` before a fine run.

**Refinement levels** — `refinementLevel` is a `[min, max]` integer pair for the room envelope; each level halves the cell size. `rackRegionLevel` is a single level for the region enclosing each rack. Per-class pairs exist for floors, ACUs, boxes, baffles and partition walls.

| Field | Default | What it controls |
|-------|---------|------------------|
| `refinementLevel` | `[1, 1]` | Room envelope walls |
| `rackRegionLevel` | `3` | Region enclosing each rack (single integer) |
| `partitionWallRefinementLevel` | `[1, 1]` | Interior partition walls |
| `floorRefinementLevel` | `[1, 1]` | Raised-floor surfaces |

**dcCart sizing** — `weldTolerance` (near-coincident vertex weld), `firstLayerMinFraction` / `firstLayerMax` / `firstLayerSealGap` (the minimum thickness of the first cell off a surface), and `rackCellMinSize` (the rack-region cell is never auto-refined below it). Blank values use the runner defaults shown above.

**Parallelism** — with `parallelMesh: true` the mesher runs with `meshProcess` MPI ranks. dcCart produces the same cells for any rank count, keeps the processor partitions for the solver, and treats a failed MPI run as an error rather than rerunning it on one rank.

**Cell count limit** — `maxGlobalCells` stops further refinement once the mesh reaches that size (the current refinement pass and later processing can exceed it slightly).

**Time** — `meshTimeLimitSeconds` is a soft target: a mesh that takes longer is logged with a warning and runs to completion.

Every mesh ends with a strict `checkMesh` whose result is recorded in `case/<name>/mesh-quality-receipt.json`; the solver refuses a mesh without an accepted receipt.

See §8 of the [.cfdrun reference](../reference/cfdrun-v2-schema.md) for every field.

---

## Simulation Configuration

The `simulationConfig` object controls the OpenFOAM solver. In a v4 manifest it is the **root** object; in v2/v3 the runner uses the copy inside `meshGeometry`.

```json
"simulationConfig": {
  "solverType": "steady",
  "endTime": 1000,
  "writeInterval": 100,
  "parallelSolve": true,
  "solveProcess": 8,
  "roomTemperatureC": 22.0,
  "gravity": { "x": 0, "y": -9.81, "z": 0 },
  "residualControl": { "p_rgh": "1e-4", "U": "1e-5", "T": "1e-5", "k_epsilon": "1e-4" }
}
```

| Field | Description |
|-------|-------------|
| `solverType` | `"steady"` (default) runs `buoyantBoussinesqSimpleFoam_new`; `"transient"` runs `buoyantBoussinesqPimpleFoam_new`. Both are built from this repository (`third_parties/foam`); `solverName` overrides the choice. |
| `endTime` | Maximum number of iterations for a steady run |
| `endTimeSeconds`, `deltaT`, `writeIntervalSeconds`, `maxCo`, … | Transient time stepping; for a transient v4 run `endTimeSeconds` equals the timeline's `transient.durationS` |
| `writeInterval` | Write results every N iterations |
| `parallelSolve`, `solveProcess` | Run the solver as `mpirun -np N … -parallel` (serial solving is not used in practice) |
| `roomTemperatureC` | Initial air temperature, °C (required at the root of a v4 manifest) |
| `gravity` | Gravity vector, default `{"x": 0, "y": -9.81, "z": 0}` (Y-up) |
| `solverPreset` | Named solver preset. The editor has one, `fast` (first-order upwind, the set both solver engines use); `custom` marks hand-edited schemes. `balanced` and `accurate` were withdrawn on 2026-11-12: a project saved with either is rewritten to `fast` (schemes, relaxation factors, corrector count) when the editor or the scripted API loads it, with a line in the operation log; saving the project keeps the change |
| `transientResidualControl` | Per-time-step outer-iteration thresholds for transient runs |

**When does a steady run stop?** Since 2026-10-20 a steady run is judged **converged when every ACU return-air temperature has changed and then stays within 0.5 K over the final window**. The residuals in `residualControl` are still computed and written to `convergence.json` as diagnostics, but they do not decide the verdict. While a steady run with return-air probes is solving, a watcher applies the same rule and stops the solver early once the verdict passes over a long enough window; otherwise it runs to `endTime`. The verdict (`CONVERGED`, `NOT_CONVERGED` or `INCONCLUSIVE` when there are no return-air probes) is written to `log/<timestamp>_<name>/convergence.json`.

!!! tip
    For a quick check set `endTime` to a few hundred iterations; production runs usually stop on the return-air rule well before a large `endTime`.

See §9 of the [.cfdrun reference](../reference/cfdrun-v2-schema.md) for all fields.

---

## Sensor Points

Sensor points define virtual temperature probes. The solver samples them at every write (`postProcessing/probes/`), and after the solve the runner writes their final values to `log/<timestamp>_<name>/sensorResults/<name>_sensor_<HH-MM-SS>.json`. Probes above the ACU returns are what the convergence rule reads.

```json
"sensorPoints": [
  {"id": "sensor_inlet_row1",  "x": 1.5, "y": 1.8, "z": 0.5},
  {"id": "sensor_outlet_row1", "x": 1.5, "y": 1.8, "z": 2.5},
  {"id": "sensor_center",      "x": 5.0, "y": 2.0, "z": 3.0}
]
```

Place sensors at locations of interest — typically 1.8 m above floor (ear height for server inlets) or in hot-spot regions.

---

## Backward Compatibility

### Python Class Rename: `LeakageConfig` → `ThroughZoneConfig` (2026-04-17)

The Python Pydantic classes were renamed from `LeakageConfig`/`LeakageGroup` to `ThroughZoneConfig`/`ThroughZoneGroup`. Backward-compat aliases are provided so existing runner code that imports the old names continues to work:

```python
LeakageConfig = ThroughZoneConfig
LeakageGroup  = ThroughZoneGroup
```

The **JSON key in `.cfdrun`** is still `"leakage"` (the `CFDExportRack.leakage` field name did not change). Existing exports and saved projects are unaffected.

### Singular → Array ACU Faces (v1.0 → v1.1)

Old exports used singular face objects:

```json
"supplyFace": {"solidName": "supply", "physics": {"temperature": 15.0, "volumeFlowRate": 4.0}},
"returnFace": {"solidName": "return", "physics": {"volumeFlowRate": 4.0}}
```

Current exports use arrays:

```json
"supplyFaces": [{"solidName": "supply", "physics": {"temperature": 15.0, "volumeFlowRate": 4.0}}],
"returnFaces": [{"solidName": "return", "physics": {"volumeFlowRate": 4.0}}]
```

The helper methods `get_supply_faces()` and `get_return_faces()` on `CFDExportACU` normalize both formats. Always use these methods in runner code.

### `porosity` → `perforated` Object (2026-04-16)

Three classes retain a legacy `porosity` scalar (0–100 %) alongside the modern `PerforatedConfig` object:

| Class | Legacy field | Modern field |
|-------|------------|--------------|
| `BafflePhysics` | `porosity` | `perforated.openAreaPct` |
| `FloorOpeningPhysics` | `porosity` | `perforated.openAreaPct` |
| `CFDExportFloor` | `defaultBCPorosity` | `defaultBCPerforated.openAreaPct` |

When the `perforated` object is present it supersedes the legacy scalar. The legacy scalar is still read by the classifier that decides "wall vs perforated" at the `createBaffles` stage for old exports.

### Forward Compatibility

The classes of the `meshGeometry` block use `extra="allow"` (via `CFDBaseModel`), so unknown fields in that block are preserved. The rest of the manifest is strict (`extra="forbid"`): an undeclared field anywhere else is a validation error, which is why a manifest must be re-exported by the editor rather than edited by hand.

See [Backward Compatibility](../reference/legacy/cfdrun-schema.md#backward-compatibility) in the schema reference for full details.
