# DCTwin room.json Definition Guide

> ⚠️ **ARCHIVED — legacy `room.json` format. No longer used by the active CFD pipeline.**
>
> This page documents the **legacy `room.json` input format**, which has been **removed from the active CFD pipeline**. The classes that consumed it (`GeometryManager`, `CaseRunner`) no longer exist in the codebase, and this file is excluded from the built documentation site.
>
> **Active pipeline:** the browser editor saves a `.cfdproj`, exports a `.cfdrun` manifest plus multi-solid STL files, and the runner consumes the `.cfdrun` via `STLGenRunner` (`python -m backend.aircooling.runner.run_cfd --from-cfdrun <path>`). See the current `.cfdrun` and `.cfdproj` format guides under `docs/user-guide/`.
>
> The only remaining code that reads `room.json` is the standalone one-way converter `src/server/converter.py` (`room.json` → `.cfdproj`). This page is retained for historical reference and for understanding files produced by older tooling; the field descriptions below are **not maintained** and reflect the format as it existed before the `.cfdproj` / `.cfdrun` migration.

This document explains in detail how the `room.json` file was defined for the legacy DCTwin engine. `room.json` **was** the core input file of the old CFD simulation pipeline, defining the data center room geometry, equipment models, layout, and runtime parameters.

> **Reference cases**: This document was written using `dctwin/case/base` (simple case: 2 racks + 1 ACU) and `dctwin/case/base2-9` (real-world case: 138 racks + 8 ACUs) as templates. (These paths are from the synced `dctwin` repository; in `dcdtuni` the equivalent cases live under `case/`.)

---

## Table of Contents

1. [JSON Overall Structure](#1-json-overall-structure)
2. [meta - Metadata](#2-meta---metadata)
3. [geometry - Room Geometry Definition](#3-geometry---room-geometry-definition)
4. [models - Model Definitions (Template Library)](#4-models---model-definitions-template-library)
   - 4.1 [geometryModels - Geometry Models](#41-geometrymodels---geometry-models)
   - 4.2 [coolingModels - Cooling Models](#42-coolingmodels---cooling-models)
   - 4.3 [powerModels - Power Models](#43-powermodels---power-models)
5. [constructions - Physical Layout Definition](#5-constructions---physical-layout-definition)
   - 5.1 [racks - Rack Layout](#51-racks---rack-layout)
   - 5.2 [acus - ACU Layout](#52-acus---acu-layout)
   - 5.3 [boxes - Partition/Containment/Pillar](#53-boxes---partitioncontainmentpillar)
   - 5.4 [raised_floor - Raised Floor](#54-raised_floor---raised-floor)
   - 5.5 [external_objects - External STL Import](#55-external_objects---external-stl-import)
   - 5.6 [sensors - Sensors](#56-sensors---sensors)
6. [inputs - Runtime Input Parameters](#6-inputs---runtime-input-parameters)
7. [Coordinate System and Orientation](#7-coordinate-system-and-orientation)
8. [Relationship Between models and constructions](#8-relationship-between-models-and-constructions)
9. [Complete Example](#9-complete-example)

---

## 1. JSON Overall Structure

```json
{
    "meta": { ... },
    "geometry": { ... },
    "models": {
        "geometryModels": { ... },
        "coolingModels": { ... },
        "powerModels": { ... }
    },
    "constructions": {
        "racks": { ... },
        "acus": { ... },
        "boxes": { ... },
        "raised_floor": { ... },
        "external_objects": { ... },
        "sensors": { ... }
    },
    "inputs": {
        "acus": { ... },
        "servers": { ... }
    }
}
```

**Core design idea**: `models` defines reusable "templates" (for example, size parameters of a rack/server type), while `constructions` defines placed "instances" (by referencing a model and assigning location/orientation). This template-instance separation allows one model to be reused many times.

---

## 2. meta - Metadata

```json
"meta": {
    "name": "ID1-2-9",
    "version": "1.1.1"
}
```

| Field | Type | Description |
|------|------|------|
| `name` | string | Case name identifier |
| `version` | string | JSON schema/version tag |

---

## 3. geometry - Room Geometry Definition

Defines the external boundary of the whole room (shape and height).

```json
"geometry": {
    "height": 4.8,
    "plane": [
        {"x": 0, "y": 0, "z": 0},
        {"x": 17, "y": 0, "z": 0},
        {"x": 17, "y": 20, "z": 0},
        {"x": 0, "y": 20, "z": 0}
    ],
    "tile_size": 0.6
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `height` | float | m | Total room height (from z=0 to ceiling) |
| `plane` | array[Vertex] | m | Vertex list of the room floor footprint (clockwise or counterclockwise, z usually 0) |
| `tile_size` | float | m | Floor tile size (optional, default 0.6m, used as a mesh-alignment reference) |

**Notes**:
- `plane` defines the room footprint in the XY plane. The room extends from z=0 to z=height.
- Both rectangular and arbitrary polygonal footprints are supported, but rectangles (4 vertices) are most common.
- The example above defines a rectangular room of 17m × 20m × 4.8m.

---

## 4. models - Model Definitions (Template Library)

`models` is a "template library" that defines **equipment specifications** by type, without placement. `constructions` later instantiates equipment by referencing model names defined here.

### 4.1 geometryModels - Geometry Models

#### 4.1.1 racks - Rack Geometry Models

```json
"racks": {
    "RACK1": {
        "size": {"x": 0.6, "y": 1.2, "z": 2.2},
        "slot": 46,
        "firstSlotOffset": 0
    }
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `size.x` | float | m | Rack width (along X when orientation=0) |
| `size.y` | float | m | Rack depth (front-to-rear when orientation=0) |
| `size.z` | float | m | Rack height |
| `slot` | int | - | Number of available U slots in the rack (standard 1U = 0.05m) |
| `firstSlotOffset` | float | m | Offset from rack bottom to the first U slot |

**Rack size notes**:
- `size.x` is rack "width", typically 0.6m (standard 19-inch rack width)
- `size.y` is rack "depth" (airflow direction), typically 1.0~1.2m
- `size.z` is rack "height", typically 2.0~2.4m
- `slot=46` means there are 46 U slots in this rack; each U height is `size.z / slot` (or default 0.05m/U if unspecified)

**Important**: The meaning of `size.x/y` is based on the initial orientation=0 frame. After rotation, x/y are mapped to actual axes by the orientation transform.

#### 4.1.2 servers - Server Geometry Models

```json
"servers": {
    "NF5468A5": {
        "slotOccupation": 4,
        "depth": 1.05,
        "width": 0.6
    },
    "DH-S6500": {
        "slotOccupation": 1,
        "depth": 1.05,
        "width": 0.6
    },
    "DH-S8610": {
        "slotOccupation": 14,
        "depth": 1.05,
        "width": 0.6
    }
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `slotOccupation` | int | U | Number of U slots occupied by this server type (for example 1U, 2U, 4U, 14U) |
| `depth` | float | m | Server depth (usually same as or slightly smaller than rack depth) |
| `width` | float | m | Server width (usually same as or slightly smaller than rack width) |

**Notes**:
- Actual server height = `slotOccupation × slot_height` (default `slot_height = 0.05m/U`)
- Example: `slotOccupation=4` means height = 4 × 0.05 = 0.2m
- `depth` and `width` define server cross-section dimensions inside the rack

#### 4.1.3 acus - ACU Geometry Models

```json
"acus": {
    "CRAH": {
        "size": {"x": 1.8, "y": 0.99, "z": 1.38},
        "supplyFace": {
            "length": 0.99,
            "offset": {"x": 0, "y": 0, "z": 0},
            "side": "bottom",
            "width": 1.8
        },
        "returnFace": {
            "length": 0.99,
            "offset": {"x": 0, "y": 0, "z": 0},
            "side": "top",
            "width": 1.8
        }
    }
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `size` | Vertex | m | ACU outer dimensions `{x, y, z}` |
| `supplyFace` | object | - | Supply face (cold-air outlet) configuration |
| `supplyFace.side` | string | - | Which ACU face is the supply face: `"top"`, `"bottom"`, `"front"`, `"rear"`, `"left"`, `"right"` |
| `supplyFace.width` | float | m | Supply face width |
| `supplyFace.length` | float | m | Supply face length |
| `supplyFace.offset` | Vertex | m | Supply face offset relative to ACU face center |
| `returnFace` | object | - | Return face (hot-air inlet) config, same fields as `supplyFace` |

**Typical configurations**:
- Downflow ACU (underfloor supply): `supplyFace.side = "bottom"`, `returnFace.side = "top"`
- Front-supply ACU: `supplyFace.side = "front"`, `returnFace.side = "rear"`

**Optional field** (used in the base case):
```json
"coil_geometry": {
    "tube_diameter": 0.01,
    "tube_length": 0.01,
    "tube_wall_thickness": 0.002,
    "row_number": 20,
    "row_pitch": 0.03,
    "transverse_number": 20,
    "transverse_pitch": 0.03,
    "tube_roughness": 1e-6
}
```
This field defines micro-scale ACU coil geometry parameters for more detailed heat-exchanger modeling. It can be omitted in simplified mode.

#### 4.1.4 boxes - Box Geometry Models

Box models define face selection for generic box-like objects (such as containment, partition, pillar, duct).

```json
"boxes": {
    "partition": {
        "faces": {
            "top": false, "bottom": false,
            "rear": false, "front": false,
            "left": true, "right": false
        }
    },
    "pillar": {
        "faces": {
            "top": true, "bottom": true,
            "rear": true, "front": true,
            "left": true, "right": true
        }
    },
    "containment": {
        "faces": {
            "top": true, "bottom": false,
            "rear": false, "front": false,
            "left": true, "right": true
        }
    },
    "containment_partition": {
        "faces": {
            "top": false, "bottom": false,
            "rear": false, "front": true,
            "left": false, "right": false
        }
    }
}
```

| Field | Type | Description |
|------|------|------|
| `faces` | object | Defines which faces of this box participate in STL/baffle generation |
| `faces.top/bottom/front/rear/left/right` | bool | `true`=generate face, `false`=do not generate (open face) |

**Typical `faces` configurations by box type**:

| Type | Usage | Typical `faces` | Description |
|------|------|-----------------|------|
| `partition` | Divider wall | Only `left` or one single face is `true` | Used for room zoning; usually a thin plane (`size` in one axis = 0) |
| `pillar` | Column | All `true` | Solid pillar, all 6 faces generated |
| `containment` | Cold/hot aisle containment | `top=true`, `left=true`, `right=true`, others `false` | Top and sides enclosed, front/rear open for rack airflow |
| `containment_partition` | Internal containment divider | Only `front` or `left` is `true` | Divider inside containment |
| `duct` | Air duct | `front/rear/left/right=true`, `top/bottom=false` | Closed on four sides, open top/bottom |

---

### 4.2 coolingModels - Cooling Models

#### 4.2.1 ACU Cooling Models

```json
"coolingModels": {
    "acus": {
        "YMK_SCU1100DES_R_H": {
            "coolingType": "CW",
            "coolingCapacity": 115.5,
            "supplyAirVolumeFlowRate": 9.5,
            "supplyAirTemperature": 18.0
        }
    }
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `coolingType` | string | - | Cooling type, e.g. `"CW"` (chilled water) |
| `coolingCapacity` | float | kW | Rated cooling capacity |
| `supplyAirVolumeFlowRate` | float | m³/s | Design supply airflow |
| `supplyAirTemperature` | float | °C | Design supply air temperature |

#### 4.2.2 Server Cooling Models

```json
"servers": {
    "NF5468A5_cooling": {
        "fan_type": "Variable",
        "volume_flow_rate": 0.02,
        "volume_flow_rate_ratio": 0.000166,
        "liquid_cooling": false,
        "liquid_cooling_percentage": 1.0,
        "pipe_diameter": 0.01,
        "operating": {
            "air_volume_flow_rate": 0.02,
            "inlet_air_temperature": 25.0,
            "outlet_air_temperature": 35.0,
            "inlet_liquid_temperature": 25.0,
            "outlet_liquid_temperature": 40.0,
            "liquid_mass_flow_rate": 0.005
        }
    }
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `fan_type` | string | - | Fan type: `"Fixed"` (fixed speed) or `"Variable"` (variable speed) |
| `volume_flow_rate` | float | m³/s | Rated server airflow |
| `volume_flow_rate_ratio` | float | m³/s/W | Airflow-power ratio (used in `Variable` mode to compute airflow from power) |
| `liquid_cooling` | bool | - | Whether liquid cooling is enabled |
| `liquid_cooling_percentage` | float | - | Fraction of heat removed by liquid cooling (0~1) |
| `pipe_diameter` | float | m | Liquid-cooling pipe diameter |
| `operating` | object | - | Operating parameters at design condition |

**`volume_flow_rate_ratio` note**: When `fan_type = "Variable"`, actual server airflow = `volume_flow_rate_ratio × input_power`. For example, with `ratio=0.000166` and `input_power=1195W`, airflow is about 0.198 m³/s.

---

### 4.3 powerModels - Power Models

```json
"powerModels": {}
```

This is usually an empty object; power is typically provided directly at runtime via `inputs.servers`.

---

## 5. constructions - Physical Layout Definition

`constructions` defines the **actual placement and configuration** of all equipment/structures in the room. This is the most critical section of the JSON.

### 5.1 racks - Rack Layout

Each rack instance references a model in `models.geometryModels.racks` and is placed by location and orientation.

```json
"racks": {
    "A1": {
        "geometry": {
            "model": "RACK1",
            "location": {"x": 5.2, "y": 2.7, "z": 0.6},
            "orientation": 180,
            "hasBlankingPanel": true
        },
        "airLeakage": {
            "enabled": true,
            "sigma": 0.05
        },
        "constructions": {
            "servers": {
                "A1_NF5468A5_U4": {
                    "geometry": {
                        "model": "NF5468A5",
                        "slotPosition": 4,
                        "orientation": 180
                    },
                    "cooling": {
                        "model": "NF5468A5_cooling"
                    }
                },
                "A1_NF5468A5_U10": {
                    "geometry": {
                        "model": "NF5468A5",
                        "slotPosition": 10,
                        "orientation": 180
                    },
                    "cooling": {
                        "model": "NF5468A5_cooling"
                    }
                },
                "A1_DH-S6500_U45": {
                    "geometry": {
                        "model": "DH-S6500",
                        "slotPosition": 44,
                        "orientation": 180
                    },
                    "cooling": {
                        "model": "DH-S6500_cooling"
                    }
                }
            }
        }
    }
}
```

#### Rack field descriptions

| Field | Type | Description |
|------|------|------|
| `geometry.model` | string | References a model name in `models.geometryModels.racks` |
| `geometry.location` | Vertex | Rack placement location (front-left-bottom corner before rotation) |
| `geometry.orientation` | int | Orientation angle: 0, 90, 180, 270 (see [orientation notes](#7-coordinate-system-and-orientation)) |
| `geometry.hasBlankingPanel` | bool | Whether blanking panels are present (`true`: unused U slots are blocked) |
| `airLeakage` | object | (Optional) air leakage configuration |
| `airLeakage.enabled` | bool | Whether leakage modeling is enabled |
| `airLeakage.sigma` | float | Leakage ratio (0~1, e.g. 0.05 means 5% open-area leakage) |
| `constructions.servers` | object | Server instance list inside this rack |

#### Server instance field descriptions

| Field | Type | Description |
|------|------|------|
| `geometry.model` | string | References a server model in `models.geometryModels.servers` |
| `geometry.slotPosition` | int | Starting U slot index of server installation (counted from bottom, 0-indexed) |
| `geometry.orientation` | int | Server orientation (usually same as parent rack orientation) |
| `cooling.model` | string | References a cooling model in `models.coolingModels.servers` |

**`slotPosition` calculation examples**:
- Rack has 46 U slots (`slot=46`), default `slot_height=0.05m`
- `slotPosition=4` means server bottom is 4 × 0.05 = 0.2m above rack bottom
- `slotPosition=44` means server bottom is 44 × 0.05 = 2.2m above rack bottom
- A server with `slotOccupation=4` installed at `slotPosition=4` occupies U4~U7 (slots 4,5,6,7)
- **Important**: slot ranges cannot overlap, i.e. `slotPosition_A + slotOccupation_A ≤ slotPosition_B`

**`slotPosition` diagram** (rack side view):
```
slot 45 ┌─────────────────┐ ← rack top
slot 44 │  DH-S6500 (1U)  │ slotPosition=44, slotOccupation=1
        │      ...        │
slot 13 │                 │
        │ (empty/blanked) │
slot 10 │  NF5468A5 (4U)  │ slotPosition=10, slotOccupation=4
slot  9 │  occupies 10~13 │
        │      ...        │
slot  7 │                 │
slot  4 │  NF5468A5 (4U)  │ slotPosition=4, slotOccupation=4
slot  3 │   occupies 4~7  │
        │      ...        │
slot  0 ├─────────────────┤ ← rack bottom (starts at firstSlotOffset)
        └─────────────────┘
```

**Server naming convention**: Recommended format is `{RackName}_{ServerModel}_{UPosition}`, e.g. `A1_NF5468A5_U4` means an NF5468A5 server in rack A1 installed at U4.

#### Detailed Server definition in the base case (compatible style)

The base case uses a more detailed style by inlining complete server parameters under `constructions`:

```json
"Rack_1_Server_1": {
    "geometry": {
        "slot_occupation": 4,
        "depth": 1.2,
        "width": 0.75,
        "inlet": {
            "heightRatio": 0.8,
            "width": 0.75,
            "offsetX": 0.02,
            "offsetZ": 0.0
        },
        "outlet": {
            "heightRatio": 0.8,
            "width": 0.75,
            "offsetX": 0.02,
            "offsetZ": 0.0
        },
        "model": "1U_Server",
        "slot_position": 39,
        "orientation": 90.0,
        "slot_height": 0.05
    },
    "cooling": { ... },
    "power": {
        "input_power": 150.0
    },
    "computing": {
        "cpu_capacity": 64.0,
        "mem_capacity": 128.0,
        "disk_capacity": 1000.0
    }
}
```

| Additional Field | Type | Description |
|----------|------|------|
| `inlet.heightRatio` | float | Ratio of inlet opening height to full server height (0~1, e.g. 0.8 means 80% opening area) |
| `inlet.width` | float | Inlet opening width |
| `inlet.offsetX` | float | Horizontal offset of inlet opening |
| `inlet.offsetZ` | float | Vertical offset of inlet opening |
| `outlet` | object | Outlet opening config (same structure as `inlet`) |
| `slot_height` | float | Custom U-slot height (default 0.05m) |

**Note**: base2-9 uses the simplified model-reference style (recommended). The base case uses the inlined detailed style (compatible), suitable when fine-grained inlet/outlet face control is needed.

---

### 5.2 acus - ACU Layout

```json
"acus": {
    "1-AC-2-9-1": {
        "geometry": {
            "model": "CRAH",
            "location": {"x": 3.0, "y": 2.5, "z": 0.6},
            "orientation": 90.0
        },
        "cooling": {
            "model": "YMK_SCU1100DES_R_H"
        }
    }
}
```

| Field | Type | Description |
|------|------|------|
| `geometry.model` | string | References ACU geometry model in `models.geometryModels.acus` |
| `geometry.location` | Vertex | ACU placement location |
| `geometry.orientation` | int | Orientation angle (0/90/180/270) |
| `cooling.model` | string | References cooling model in `models.coolingModels.acus` |

**`location.z` note**: If raised floor exists, ACUs are usually placed on top of it, so `location.z` is typically the raised-floor height (for example 0.6m).

---

### 5.3 boxes - Partition/Containment/Pillar

`boxes` is a generic collection of box objects. Different functions are achieved by referencing different `model` values.

```json
"boxes": {
    "partition1": {
        "uid": "partition1",
        "geometry": {
            "model": "partition",
            "size": {"x": 0.0, "y": 20.0, "z": 4.2},
            "location": {"x": 3.0, "y": 0, "z": 0.6},
            "baffle_type": "wall",
            "porosity": 0.0,
            "faces": {
                "top": false, "bottom": false,
                "front": true, "rear": false,
                "left": false, "right": false
            },
            "openingsSide": "left",
            "openings": {
                "return_1": {
                    "location": {"x": 3.0, "y": 2.2, "z": 2.1},
                    "size": {"x": 0.0, "y": 17.4, "z": 1.3}
                }
            }
        }
    },
    "containment1": {
        "uid": "containment1",
        "geometry": {
            "model": "containment",
            "location": {"x": 4.6, "y": 2.7, "z": 0.6},
            "size": {"x": 10.8, "y": 1.2, "z": 2.2},
            "baffle_type": "wall",
            "porosity": 0.0,
            "faces": {
                "top": true, "bottom": false,
                "front": false, "rear": false,
                "left": true, "right": true
            }
        }
    }
}
```

#### Box field descriptions

| Field | Type | Description |
|------|------|------|
| `uid` | string | Unique identifier |
| `geometry.model` | string | References model in `models.geometryModels.boxes` (controls default `faces`) |
| `geometry.size` | Vertex | Box size. **Important**: if one dimension is 0, that axis becomes a zero-thickness plane (e.g., partition wall) |
| `geometry.location` | Vertex | Box placement location |
| `geometry.orientation` | int | (Optional) orientation angle, default 0 |
| `geometry.baffle_type` | string | Baffle type: `"wall"` (impermeable) or `"perforated"` (porous) |
| `geometry.porosity` | float | Porosity (0~1; valid only for `perforated`) |
| `geometry.faces` | object | Can override model-level default `faces` at instance level |
| `geometry.openingsSide` | string | (Optional) face containing openings: `"left"`, `"right"`, `"front"`, `"rear"`, `"top"`, `"bottom"` |
| `geometry.openings` | object | (Optional) opening definitions on that face |

#### Containment (cold aisle enclosure) details

Containment is used to enclose the cold or hot aisle between rack rows. Core logic:
- `top=true`: close the top to prevent cold air from escaping upward
- `left=true, right=true`: close both sides
- `front=false, rear=false`: leave front/rear open toward rack intake/exhaust
- `bottom=false`: keep bottom open to connect with underfloor supply

```
     Top enclosed (top=true)
    ┌────────────────────┐
    │                    │
Left│   Cold aisle zone  │Right
wall│   (rack intakes)   │wall
    │                    │
    └────────────────────┘
     Bottom open (bottom=false)
     ↑ Underfloor supply air
```

**Containment and rack layout relation** (top view):
```
      ← containment size.x →
   ┌──────────────────────────────┐
   │  Rack A1  A2  A3 ... A18    │ ← Rack row (front faces into containment)
   │  ┌──┐┌──┐┌──┐    ┌──┐      │
   │  │  ││  ││  │    │  │      │ ← containment size.y = rack depth
   │  └──┘└──┘└──┘    └──┘      │
   │  Rack B1  B2  B3 ... B18    │ ← Rack row (front faces into containment)
   └──────────────────────────────┘
```

#### Partition details

Partitions are usually used to divide the room into zones (e.g., ACU zone and IT equipment zone):
- `size.x=0` means the partition is a YZ-plane (zero thickness in X)
- Usually only one face is `true` (for example `front=true`) because it is essentially a plane
- `openings` can define return-air cutouts in the partition

```json
"partition1": {
    "geometry": {
        "model": "partition",
        "size": {"x": 0.0, "y": 20.0, "z": 4.2},
        "location": {"x": 3.0, "y": 0, "z": 0.6},
        "openingsSide": "left",
        "openings": {
            "return_1": {
                "location": {"x": 3.0, "y": 2.2, "z": 2.1},
                "size": {"x": 0.0, "y": 17.4, "z": 1.3}
            }
        }
    }
}
```

- This partition is at x=3.0, spanning y=0 to y=20 (full room depth), and z=0.6 to z=4.8
- A return opening is created at y=2.2~19.6, z=2.1~3.4

#### Containment Partition (internal divider inside containment)

A small divider can be placed inside containment for finer airflow separation:

```json
"containment2_partition_1": {
    "geometry": {
        "model": "containment_partition",
        "location": {"x": 7.6, "y": 8.7, "z": 0.6},
        "size": {"x": 1.2, "y": 0, "z": 2.2},
        "baffle_type": "wall",
        "porosity": 0.0,
        "faces": {
            "top": false, "bottom": false,
            "front": false, "rear": false,
            "left": true, "right": false
        }
    }
}
```

---

### 5.4 raised_floor - Raised Floor

Raised floor is a key component of downflow cooling in data centers.

```json
"raised_floor": {
    "uid": "",
    "geometry": {
        "height": 0.6,
        "baffle_type": "wall",
        "porosity": 0.0,
        "openings": {
            "containment1_opening": {
                "location": {"x": 4.6, "y": 2.7, "z": 0.6},
                "size": {"x": 10.8, "y": 1.2, "z": 0},
                "baffle_type": "perforated",
                "porosity": 0.8,
                "velocity": null
            },
            "containment2_opening": {
                "location": {"x": 4.6, "y": 7.5, "z": 0.6},
                "size": {"x": 10.8, "y": 1.2, "z": 0},
                "baffle_type": "perforated",
                "porosity": 0.8,
                "velocity": null
            }
        }
    },
    "meta": {}
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `geometry.height` | float | m | Raised-floor height (typically 0.2~0.8m) |
| `geometry.baffle_type` | string | - | Main floor type: `"wall"` (impermeable solid floor) |
| `geometry.porosity` | float | - | Main floor porosity (typically 0 for solid floor) |
| `geometry.openings` | object | - | Openings/perforated tiles in the floor |

**Optional component**:
- If a case has no raised floor, omit `raisedFloor` / `raised_floor` completely.
- Do not keep a placeholder such as `"height": 0` with empty openings. In the current implementation that still creates `raised_floor_panel.stl`.

#### Opening field descriptions

| Field | Type | Unit | Description |
|------|------|------|------|
| `location` | Vertex | m | Opening location (lower-left corner) |
| `size` | Vertex | m | Opening size (`z=0` indicates a horizontal plane opening) |
| `baffle_type` | string | - | `"perforated"` (perforated tile) or `"wall"` (solid, reserved structurally) |
| `porosity` | float | - | Effective open-area ratio (0~1; e.g., 0.8 means 80% open area for airflow) |
| `velocity` | float/null | m/s | Prescribed velocity (`null` means auto-computed) |

**Design logic**: The raised floor forms a horizontal baffle at z=`height`, splitting the room into an underfloor supply plenum and an upper equipment space. `openings` are perforated tile regions where cold air rises from the underfloor plenum into the cold aisle.

**Openings usually align with containment**: In the example above, `containment1_opening` has the same `location` and `size` as `containment1`, ensuring cold air is supplied exactly into the containment area.

**Raised-floor supply diagram** (side view):
```
z=4.8  ─────────────────────── Ceiling
       │      ← Return air →   │
       │  ACU(return)  Rack    │
z=2.8  │  ┌──┐     ┌───────┐  │
       │  │  │     │Server │  │
       │  │  │     │ (heat)│  │
z=0.6  │  │  │     └───────┘  │
       ═══╪══╪═══↑↑↑↑═════════ Raised floor (supply through perforated tiles ↑)
       │  │  │   Cold air      │
z=0.0  │  └──┘  ACU(supply)    │ Underfloor supply plenum
       ─────────────────────── Floor slab
```

---

### 5.5 external_objects - External STL Import

Supports importing STL files from external CAD tools as obstacles in the room.

```json
"external_objects": {
    "cad_pillar1": {
        "stl_file": "../Mesh/constant/triSurface/pillar.stl",
        "location": {"x": 8.8, "y": 0.8, "z": 0.0},
        "orientation": 0,
        "boundary_conditions": {
            "front": {"type": "fixedTemperature", "temperature": 50.0},
            "rear": {"type": "wall"},
            "left": {"type": "wall"},
            "right": {"type": "wall"},
            "top": {"type": "wall"},
            "bottom": {"type": "wall"}
        }
    }
}
```

| Field | Type | Description |
|------|------|------|
| `stl_file` | string | External STL file path (relative to the `room.json` directory) |
| `location` | Vertex | Placement offset |
| `orientation` | int | Rotation angle |
| `boundary_conditions` | object | Optional boundary conditions per STL solid region |
| `boundary_conditions.{solid}.type` | string | `"wall"` (default) or `"fixedTemperature"` |
| `boundary_conditions.{solid}.temperature` | float | Fixed temperature (°C, only for `fixedTemperature`) |

**Actual importer behavior**:
- The file must be **ASCII STL**. Binary STL is not supported by the importer.
- `stl_file` is resolved relative to the `room.json` directory unless it is already absolute.
- Each STL `solid <name>` block becomes one OpenFOAM patch named `{uid}_{solid_name}` after sanitization.
- If the STL repeats the same solid name multiple times, those triangles are merged into one patch.
- `location` and `orientation` are applied as a rigid transform. If the CAD file is already in room/world coordinates, keep `location = {0,0,0}` and `orientation = 0`.
- If `boundary_conditions` is omitted, all imported solids are treated as walls.

**Important limitation**:
- Imported STL currently supports only generic wall-like behavior and fixed-temperature walls.
- It does **not** automatically create ACU supply/return, server inlet/outlet, porous leakage, or other special semantic patches.
- If you need ACU airflow physics, keep using `constructions.acus` or extend the code so imported STL solids can map to ACU semantics.

**Recommended STL authoring rules**:
- Use unique, stable solid names for regions that need different treatment.
- Do not rely on repeated generic names like `wall_1` or `supply` if multiple devices are packed into one STL.
- Keep the CAD local coordinate system consistent: origin near the object and Z pointing up.

---

### 5.6 sensors - Sensors

Sensors are used to sample temperature and other physical quantities at specified points in simulation results.

```json
"sensors": {
    "Sensor_1": {
        "geometry": {
            "location": {"x": 7.4, "y": 3.0, "z": 2.6}
        },
        "meta": {
            "aisle": "cold"
        }
    },
    "sensor_2": {
        "uid": "sensor_2",
        "geometry": {
            "location": {"x": 5.5, "y": 3.5, "z": 1.2}
        },
        "meta": {}
    }
}
```

| Field | Type | Description |
|------|------|------|
| `geometry.location` | Vertex | 3D coordinates of the sensor in the room |
| `meta` | object | Extra metadata (for example `aisle: "cold"/"hot"`) |

---

## 6. inputs - Runtime Input Parameters

`inputs` defines equipment operating-state parameters at the current simulation condition (power, temperature, airflow, etc.).

### 6.1 ACU inputs

```json
"inputs": {
    "acus": {
        "1-AC-2-9-1": {
            "supplyAirTemperature": 20.0,
            "supplyAirVolumeFlowRate": 4
        }
    }
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `supplyAirTemperature` / `supply_air_temperature` | float | °C | Current supply air temperature |
| `supplyAirVolumeFlowRate` / `supply_air_volume_flow_rate` | float | m³/s | Current supply air volume flow rate |

### 6.2 Server inputs

```json
"inputs": {
    "servers": {
        "A1_NF5468A5_U4": {
            "inputPower": 1195
        }
    }
}
```

| Field | Type | Unit | Description |
|------|------|------|------|
| `inputPower` / `input_power` | float | W | Current server input power (heat generation) |

**Note**: Each server key must exactly match a server key under `constructions.racks.{rack}.constructions.servers`.

---

## 7. Coordinate System and Orientation

### 7.1 Coordinate system

```
        Y (room depth direction)
        ↑
        │
        │
        └────────→ X (room width direction)
       /
      ↙
     Z (height direction, positive upward)
```

- **X-axis**: usually room width direction
- **Y-axis**: usually room depth direction
- **Z-axis**: height direction (floor z=0, ceiling z=height)

### 7.2 Orientation definition

`orientation` determines which axis direction an object's "front face" points to. For racks, `front` is the server inlet face.

| orientation | Front direction | Rear direction | Description |
|-------------|-----------|-----------|------|
| **0** | -Y | +Y | Front faces negative Y |
| **90** | +X | -X | Front faces positive X |
| **180** | +Y | -Y | Front faces positive Y |
| **270** | -X | +X | Front faces negative X |

**Rack orientation diagrams** (top view):

```
orientation = 0:            orientation = 90:
     Rear (+Y)                   Rear (-X)
    ┌────────┐                  ┌────────┐
    │        │                  │        │
    │  Rack  │                  │  Rack  │
    │        │                  │        │
    └────────┘                  └────────┘
     Front (-Y)                  Front (+X)
     (inlet)                     (inlet)

orientation = 180:           orientation = 270:
     Rear (-Y)                   Rear (+X)
    ┌────────┐                  ┌────────┐
    │        │                  │        │
    │  Rack  │                  │  Rack  │
    │        │                  │        │
    └────────┘                  └────────┘
     Front (+Y)                  Front (-X)
     (inlet)                     (inlet)
```

### 7.3 Face-to-face cold aisle layout

In real data centers, two rack rows often face each other to form a cold aisle. Example from base2-9:

```
Y direction
↑
│   Row B (orientation=0, front faces -Y)
│   ┌──┐┌──┐┌──┐┌──┐
│   │B1││B2││B3││B4│  ← inlets face down (-Y)
│   └──┘└──┘└──┘└──┘
│   ↕ Cold aisle (covered by containment)
│   ┌──┐┌──┐┌──┐┌──┐
│   │A1││A2││A3││A4│  ← inlets face up (+Y)
│   └──┘└──┘└──┘└──┘
│   Row A (orientation=180, front faces +Y)
└─────────────────────→ X direction
```

### 7.4 Meaning of `location`

`location` is the equipment **reference point** (rotation center): specifically, the front-left-bottom corner `(min_x, min_y, min_z)` in orientation=0, then rotated around this point by `orientation` degrees.

Rotation logic: Use `location` as rotation origin, rotate the 4 corner points around Z axis counterclockwise by `orientation`, then take the rotated bounding box as final occupied volume.

### 7.5 Face/orientation consistency

**Important rule**: Server `orientation` inside a rack should match the parent rack `orientation`. If inconsistent, the engine auto-corrects and emits a warning.

---

## 8. Relationship Between models and constructions

```
models (template definitions)        constructions (instance deployment)
├── geometryModels                 ├── racks
│   ├── racks                      │   ├── A1 ──(ref)──→ racks.RACK1
│   │   └── RACK1                  │   │   └── servers
│   ├── servers                    │   │       └── A1_NF5468A5_U4 ──→ servers.NF5468A5
│   │   ├── NF5468A5               │   │                          ──→ coolingModels.NF5468A5_cooling
│   │   └── DH-S6500               │   └── A2 ──(ref)──→ racks.RACK1
│   ├── acus                       │
│   │   └── CRAH                   ├── acus
│   └── boxes                      │   └── 1-AC-2-9-1 ──→ acus.CRAH
│       ├── containment            │                   ──→ coolingModels.YMK_SCU1100DES_R_H
│       └── partition              │
│                                  ├── boxes
├── coolingModels                  │   ├── containment1 ──→ boxes.containment
│   ├── acus                       │   └── partition1 ──→ boxes.partition
│   │   └── YMK_SCU1100DES_R_H    │
│   └── servers                    ├── raised_floor
│       └── NF5468A5_cooling       │
└── powerModels                    ├── sensors
                                   │
                                   └── external_objects
```

**Workflow**:
1. Define device-type "spec parameters" in `models` first (size, U count, cooling parameters, etc.)
2. Create device instances in `constructions` by referencing model names and assigning `location` + `orientation`
3. Set runtime parameters per instance in `inputs` (power, airflow, etc.)

---

## 9. Complete Example

### 9.1 Minimal example (1 ACU + 1 rack + 1 server)

```json
{
    "meta": {
        "name": "minimal_example",
        "version": "1.0"
    },
    "geometry": {
        "height": 3.6,
        "plane": [
            {"x": 0, "y": 0, "z": 0},
            {"x": 10, "y": 0, "z": 0},
            {"x": 10, "y": 6, "z": 0},
            {"x": 0, "y": 6, "z": 0}
        ]
    },
    "models": {
        "geometryModels": {
            "racks": {
                "RACK42U": {
                    "size": {"x": 0.6, "y": 1.2, "z": 2.1},
                    "slot": 42,
                    "firstSlotOffset": 0
                }
            },
            "servers": {
                "2U_Server": {
                    "slotOccupation": 2,
                    "depth": 1.05,
                    "width": 0.6
                }
            },
            "acus": {
                "CRAH_1": {
                    "size": {"x": 1.8, "y": 0.9, "z": 2.0},
                    "supplyFace": {
                        "side": "bottom",
                        "width": 1.8,
                        "length": 0.9,
                        "offset": {"x": 0, "y": 0, "z": 0}
                    },
                    "returnFace": {
                        "side": "top",
                        "width": 1.8,
                        "length": 0.9,
                        "offset": {"x": 0, "y": 0, "z": 0}
                    }
                }
            },
            "boxes": {}
        },
        "coolingModels": {
            "acus": {
                "CRAH_cooling_1": {
                    "coolingType": "CW",
                    "coolingCapacity": 85.0,
                    "supplyAirVolumeFlowRate": 4.0,
                    "supplyAirTemperature": 15.0
                }
            },
            "servers": {
                "2U_cooling": {
                    "fan_type": "Fixed",
                    "volume_flow_rate": 0.02,
                    "volume_flow_rate_ratio": 0.0001,
                    "liquid_cooling": false,
                    "liquid_cooling_percentage": 1.0,
                    "pipe_diameter": 0.01,
                    "operating": {
                        "air_volume_flow_rate": 0.02,
                        "inlet_air_temperature": 25.0,
                        "outlet_air_temperature": 35.0,
                        "inlet_liquid_temperature": 25.0,
                        "outlet_liquid_temperature": 40.0,
                        "liquid_mass_flow_rate": 0.005
                    }
                }
            }
        },
        "powerModels": {}
    },
    "constructions": {
        "racks": {
            "Rack_A1": {
                "geometry": {
                    "model": "RACK42U",
                    "location": {"x": 5.0, "y": 3.0, "z": 0.0},
                    "orientation": 180,
                    "hasBlankingPanel": true
                },
                "constructions": {
                    "servers": {
                        "Rack_A1_Server_1": {
                            "geometry": {
                                "model": "2U_Server",
                                "slotPosition": 10,
                                "orientation": 180
                            },
                            "cooling": {
                                "model": "2U_cooling"
                            }
                        }
                    }
                }
            }
        },
        "acus": {
            "ACU_1": {
                "geometry": {
                    "model": "CRAH_1",
                    "location": {"x": 2.0, "y": 3.0, "z": 0.0},
                    "orientation": 90
                },
                "cooling": {
                    "model": "CRAH_cooling_1"
                }
            }
        },
        "boxes": {},
        "sensors": {
            "sensor_1": {
                "geometry": {
                    "location": {"x": 5.5, "y": 3.5, "z": 1.2}
                },
                "meta": {}
            }
        }
    },
    "inputs": {
        "acus": {
            "ACU_1": {
                "supplyAirTemperature": 15.0,
                "supplyAirVolumeFlowRate": 4.0
            }
        },
        "servers": {
            "Rack_A1_Server_1": {
                "inputPower": 500
            }
        }
    }
}
```

### 9.2 Comparison of two JSON naming styles

The engine supports both naming styles (camelCase and snake_case), and both are parsed correctly:

| camelCase (base2-9 style) | snake_case (base style) | Description |
|-------------------------|----------------------|------|
| `geometryModels` | `geometry_models` | Geometry model container |
| `coolingModels` | `cooling_models` | Cooling model container |
| `supplyFace` | `supply_face` | ACU supply face |
| `returnFace` | `return_face` | ACU return face |
| `firstSlotOffset` | `first_slot_offset` | U-slot offset |
| `slotOccupation` | `slot_occupation` | Server U occupation |
| `slotPosition` | `slot_position` | Server U slot position |
| `hasBlankingPanel` | `hasBlankingPanel` | Blanking panel (both forms supported) |
| `raisedFloor` | `raised_floor` | Raised floor |
| `inputPower` | `input_power` | Input power |
| `supplyAirTemperature` | `supply_air_temperature` | Supply air temperature |
| `openingsSide` | `openings_side` | Opening face |

**Recommended**: Use camelCase (base2-9 style) for new cases for better consistency and readability.

---

## Appendix A: Reference Values for Common Equipment Parameters

### Standard rack dimensions
| Parameter | Typical Value | Description |
|------|--------|------|
| Width (x) | 0.6m | Standard 19-inch rack |
| Depth (y) | 1.0~1.2m | Common server depth range |
| Height (z) | 2.0~2.4m | 42U~48U |
| U count | 42~48 | Standard range |
| U height | 0.05m | Industry standard 1U = 44.45mm ≈ 0.05m |

### Typical server U occupation
| Type | slotOccupation | Typical Power Range |
|------|----------------|-------------|
| Network device (switch) | 1U | 100~300W |
| Standard server | 2U | 300~800W |
| GPU server | 4U | 1000~3000W |
| High-density storage | 6U | 500~1500W |
| Large server/blade chassis | 14U | 2000~6000W |

### ACU reference parameters
| Parameter | Small CRAH | Large CRAH |
|------|----------|----------|
| Cooling capacity | 50~100 kW | 100~200 kW |
| Supply airflow | 2~5 m³/s | 5~15 m³/s |
| Supply air temperature | 15~22 °C | 15~22 °C |
| Physical size | 1.5×0.8×2.0 m | 2.0×1.0×2.0 m |

---

## Appendix B: Key Notes

1. **ID uniqueness**: Keys of all racks, servers, ACUs, boxes, and sensors must be globally unique
2. **inputs-constructions matching**: Keys in `inputs.servers` must exactly match server keys in `constructions.racks.{rack}.constructions.servers`
3. **Orientation consistency**: All server orientations inside one rack should match the rack orientation
4. **No slot overlap**: U ranges of different servers in the same rack must not overlap
5. **Containment-raised_floor opening alignment**: Perforated tile openings should align with containment cold-aisle positions
6. **Consider raised floor in `location.z`**: When raised floor exists, equipment `location.z` is usually `raised_floor.height`
