# Data Center Geometry Modeling and Mesh Generation Refactoring Plan

## Background

Current mesh generation process interleaves geometry construction with mesh partitioning (using CreatePatch after snappyHexMesh), which causes jagged patch boundaries due to numerical matching errors.

## New Solution: STL-Based Geometry Modeling

Build complete STL geometry models first, then use snappyHexMesh for mesh generation. Different patches are defined as separate Solid regions within STL files.

---

## 1. Architecture Design

### 1.1 Core Class Hierarchy

```
Entity (Base Class)
    |-- Has STL generation capability
    |-- Properties: uid, location, size, orientation
    |-- Methods: generate_stl(), get_solids()
    |
    +-- RackEntity (default patch type is Wall)
    |       |-- SubEntity: ServerInlet, ServerOutlet (patch type: patch)
    |       |-- SubEntity: AirLeakage (side panels) (patch type: cyclicAMI)
    |       |-- SubEntity: SidePanel (patch type: wall)
    |
    +-- ACUEntity (default patch type is Wall)
    |       |-- SubEntity: SupplyFace (patch type: patch)
    |       |-- SubEntity: ReturnFace (patch type: patch)
    |       |-- Other faces: Wall (patch type: wall)
    |
    +-- PillarEntity (default patch type is Wall)
    |       |-- 6 faces, all walls
    |
    +-- ContainmentEntity
            |-- Top, Left, Right walls
            |-- Front, Rear, Bottom: open

SubEntity (Base Class)
    |-- Attached to parent Entity's STL as separate Solid region
    |-- OR: CreateBaffle + CreatePatch method (for Room boundaries)
    |
    +-- ServerInlet / ServerOutlet (split subregion from RackEntity)
    +-- AirLeakage (split subregion from RackEntity)
    +-- SidePanel (split subregion from RackEntity)
    +-- SupplyFace / ReturnFace (split subregion from ACUEntity)
    +-- Floor / Ceiling / Wall (CreateBaffle + CreatePatch method)
```

### 1.2 Room Class (Special Case)

Room is **NOT** an Entity. It directly defines the blockMeshDict computational domain:
- 6 wall boundaries from blockMeshDict
- Raised floor / False ceiling: CreateBaffle + CreatePatch
- Openings in floor/ceiling: Cyclic patches via CreateBaffle

---

## 2. STL Generation Strategy

### 2.1 Single STL per Entity

Each Entity generates one STL file with multiple Solid regions:

```
# Example: rack_001.stl
solid rack_001_wall_front
  facet normal ...
  ...
endsolid rack_001_wall_front

solid rack_001_wall_rear
  ...
endsolid rack_001_wall_rear

solid rack_001_server_inlet_1
  ...
endsolid rack_001_server_inlet_1

solid rack_001_server_outlet_1
  ...
endsolid rack_001_server_outlet_1

solid rack_001_air_leak_left
  ...
endsolid rack_001_air_leak_left
```

### 2.2 STL Face Definition

For a 6-faced hexahedron, each face is defined by 4 vertices forming 2 triangles:

```python
# Face = 4 vertices -> 2 triangles
def rect_to_triangles(v00, v10, v11, v01) -> list[Triangle]:
    return [(v00, v10, v11), (v00, v11, v01)]
```

Normal vector calculation uses right-hand rule:
```python
def tri_normal(a, b, c):
    edge1 = b - a
    edge2 = c - a
    return normalize(cross(edge1, edge2))
```

---

## 3. Rack SubEntity Design

### 3.1 Rack Face Layout

For a rack with orientation 0 (front facing -Y):

```
        TOP (wall)
    +-------------+
    |             |   z_max
    |   REAR      |   (wall or air_leak)
    |   PANEL     |
    |             |
    +-LEFT---RIGHT+
    |             |
    |  SERVER     |
    |  ZONE       |
    |   +inlet/   |
    |   +outlet   |
    |             |
    +-------------+
        FRONT (servers inlet/outlet + air_leak)
        y_min

    LEFT (wall)          RIGHT (wall)
    x_min                x_max
```

### 3.2 SubEntity Parameters (JSON)

```json
{
  "rack": {
    "uid": "rack_001",
    "geometry": {
      "location": {"x": 0, "y": 0, "z": 0},
      "size": {"x": 0.6, "y": 1.2, "z": 2.2},
      "orientation": 0
    },
    "subEntities": {
      "airLeakage": {
        "enabled": true,
        "width": 0.15,  // width of air leak zone on inlet/outlet sides
        "position": "sides"  // "sides" or "top_bottom"
      },
      "servers": [
        {
          "uid": "server_001",
          "slotPosition": 0,
          "slotOccupation": 4,
          "inlet": {
            "width": 0.6,  // full rack width
            "heightRatio": 0.33  // 1/3 of server height for inlet
          },
          "outlet": {
            "width": 0.6,
            "heightRatio": 0.33
          }
        }
      ]
    }
  }
}
```

### 3.3 Validation Rules

```python
def validate_rack_geometry(rack):
    total_server_width = sum(s.inlet.width for s in rack.servers)
    air_leak_width = rack.airLeakage.width * 2  # left + right

    if total_server_width + air_leak_width > rack.size.x:
        raise ValueError(
            f"Server width ({total_server_width}) + "
            f"air leak width ({air_leak_width}) > "
            f"rack width ({rack.size.x})"
        )
```

---

## 4. snappyHexMesh Configuration

### 4.1 New snappyHexMeshDict Template

```
geometry
{
    // All STL files with their solids
    rack_001.stl
    {
        type triSurfaceMesh;
        name rack_001;
        regions
        {
            rack_001_wall_front    { name rack_001_wall_front; }
            rack_001_server_inlet_1 { name rack_001_server_inlet_1; }
            // ... other regions
        }
    }

    acu_001.stl
    {
        type triSurfaceMesh;
        name acu_001;
        regions { ... }
    }
}

refinementSurfaces
{
    rack_001
    {
        level (2 2);  // refinement level
        regions
        {
            rack_001_wall_front    { level (2 2); patchInfo { type wall; } }
            rack_001_server_inlet_1 { level (3 3); patchInfo { type patch; } }
            // inlet/outlet get higher refinement
        }
    }
}

refinementRegions
{
    rack_001
    {
        mode inside;
        levels ((1 2));  // refine inside rack region
    }
}
```

### 4.2 Patch Type Mapping

| SubEntity Type | OpenFOAM Patch Type |
|---------------|---------------------|
| Wall (top, left, right, bottom) | wall |
| Server inlet | patch (cyclic with outlet) |
| Server outlet | patch (cyclic with inlet) |
| Air leakage master | cyclic |
| Air leakage slave | cyclic |
| ACU supply | patch (inlet velocity) |
| ACU return | patch (outlet velocity) |
| Floor/Ceiling | wall (via CreateBaffle) |
| Opening | cyclic (via CreateBaffle) |

---

## 5. JSON Configuration Format

### 5.1 New room.json Structure

```json
{
  "meta": {
    "name": "base2_STL_mode",
    "version": "2.0.0"
  },
  "room": {
    "geometry": {
      "plane": [
        {"x": 0, "y": 0},
        {"x": 10, "y": 0},
        {"x": 10, "y": 8},
        {"x": 0, "y": 8}
      ],
      "height": 3.5
    },
    "raisedFloor": {
      "height": 0.6,
      "openings": {
        "opening_1": {
          "location": {"x": 1, "y": 2},
          "size": {"x": 0.6, "y": 0.3}
        }
      }
    },
    "falseCeiling": null
  },
  "entities": {
    "racks": {
      "rack_001": {
        "geometryModel": "RACK1",
        "location": {"x": 1, "y": 1, "z": 0.6},
        "orientation": 0,
        "airLeakage": {
          "enabled": true,
          "width": 0.1
        },
        "servers": [
          {
            "uid": "server_001",
            "model": "NF5468A5",
            "slotPosition": 0,
            "power": 500,
            "volumeFlowRate": 0.05,
            "inlet": {"heightRatio": 0.33},
            "outlet": {"heightRatio": 0.33}
          }
        ]
      }
    },
    "acus": {
      "acu_001": {
        "geometryModel": "CRAH",
        "coolingModel": "YMK_SCU1100DES",
        "location": {"x": 5, "y": 0, "z": 0.6},
        "orientation": 0
      }
    },
    "pillars": {
      "pillar_001": {
        "location": {"x": 3, "y": 4, "z": 0},
        "size": {"x": 0.4, "y": 0.4, "z": 3.5}
      }
    }
  },
  "models": {
    "geometryModels": {
      "racks": {
        "RACK1": {
          "size": {"x": 0.6, "y": 1.2, "z": 2.2},
          "slot": 46
        }
      },
      "servers": {
        "NF5468A5": {
          "slotOccupation": 4,
          "depth": 1.05,
          "width": 0.6
        }
      }
    }
  }
}
```

---

## 6. config.toml Extensions

```toml
# -----------------------------------------------------------------------------
# [geometry] - STL Generation Settings
# -----------------------------------------------------------------------------
[geometry]

# Force STL regeneration even if existing STL files exist
force_stl_generation = false

# STL output directory (relative to case directory)
stl_output_dir = "constant/triSurface"

# -----------------------------------------------------------------------------
# [mesh] - Mesh Generation Settings (extended)
# -----------------------------------------------------------------------------
[mesh]

# Force mesh regeneration
force_mesh_generation = false

# Use existing STL files (if false, regenerate STL)
use_existing_stl = true

# Base cell size
base_size = 0.18

# Refinement levels
rack_refinement_level = 2
server_face_refinement_level = 3
acu_refinement_level = 2

# locationInMesh point
location_in_mesh = [0.0, 0.0, 0.0]
```

---

## 7. Implementation Steps

### Step 1: Create Base Classes (2 files)

1. `dctwin/third_parties/foam/geometry/base.py`
   - `Entity` abstract base class
   - `SubEntity` abstract base class
   - `Vec3` dataclass for 3D coordinates
   - `Triangle` type alias
   - STL writing utilities

### Step 2: Implement Rack Entity (1 file)

2. `dctwin/third_parties/foam/geometry/rack.py`
   - `RackEntity` class
   - `ServerInletSubEntity`, `ServerOutletSubEntity`
   - `AirLeakageSubEntity`
   - `RackWallSubEntity`

### Step 3: Implement Other Entities (1 file)

3. `dctwin/third_parties/foam/geometry/entities.py`
   - `ACUEntity` class
   - `PillarEntity` class
   - `ContainmentEntity` class

### Step 4: Implement Room Boundaries (1 file)

4. `dctwin/third_parties/foam/geometry/room_boundary.py`
   - CreateBaffle configurations
   - Floor/Ceiling/Wall handling
   - Opening definitions

### Step 5: Geometry Manager (1 file)

5. `dctwin/third_parties/foam/geometry/manager.py`
   - `GeometryManager` class
   - Orchestrates STL generation
   - Writes snappyHexMeshDict
   - Handles mesh workflow

### Step 6: Update run_cfd.py

6. Modify `dctwin/case/base2_STL_mode/run_cfd.py`
   - Add STL generation step
   - Add geometry change detection
   - Integrate with new config.toml options

### Step 7: Create Test Case

7. Create test files in `dctwin/case/base2_STL_mode/`
   - `model/room.json` - new JSON format
   - Updated `config.toml`
   - Test scripts

---

## 8. File Structure After Implementation

```
dctwin/
  third_parties/
    foam/
      geometry/
        __init__.py
        base.py          # Entity, SubEntity base classes
        rack.py          # RackEntity and sub-entities
        entities.py      # ACU, Pillar, Containment
        room_boundary.py # Floor, ceiling, walls
        manager.py       # GeometryManager
        stl_writer.py    # STL file generation utilities
      mesh.py            # Updated MeshBuilder
      template/
        mesh/
          snappyHexMeshDict_stl.j2  # New template for STL-based meshing

case/
  base2_STL_mode/
    config.toml          # Updated with geometry options
    run_cfd.py           # Updated script
    model/
      room.json          # New JSON format
    constant/
      triSurface/        # Generated STL files
        rack_001.stl
        acu_001.stl
        pillar_001.stl
```

---

## 9. Key Design Decisions

1. **One STL per Entity**: Each Entity generates a single STL file with multiple Solid regions. This simplifies file management and snappyHexMesh configuration.

2. **SubEntity as Solid Regions**: SubEntities that belong to an Entity are represented as Solid regions within the parent's STL file, not separate files.

3. **Room via blockMesh**: Room boundaries (6 walls) are handled by blockMeshDict, not STL. This maintains the background mesh structure.

4. **CreateBaffle for Internal Surfaces**: Raised floor, false ceiling, and their openings use CreateBaffle after snappyHexMesh, as they are internal surfaces.

5. **Validation Before Generation**: All geometry parameters are validated before STL generation to catch configuration errors early.

6. **Caching with Change Detection**: STL files are regenerated only when geometry changes, detected via JSON hash comparison.

---

## 10. Expected Benefits

1. **No Jagged Boundaries**: Patch boundaries are defined precisely in STL geometry, eliminating numerical matching errors.

2. **Clean Separation**: Geometry definition is completely separated from mesh generation.

3. **Better Maintainability**: Entity/SubEntity hierarchy makes code more modular and extensible.

4. **Flexible Configuration**: JSON format allows fine-grained control over each patch's parameters.

5. **Efficient Workflow**: Caching prevents unnecessary regeneration of STL files and meshes.
