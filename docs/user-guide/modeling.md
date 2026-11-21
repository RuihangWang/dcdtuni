# STL Modeling

Guide to creating data center geometry with the browser-based 3D editor.

!!! info
    Start the editor with `./src/start_server.sh` and open the address the banner prints
    (default `http://localhost:8000/`). Log in with the launcher's administrator credentials:
    the environment pair `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD` if you set one, otherwise
    user `admin` and the password on the second line of `src/server/auth-local/admin_credentials`
    (see [Installation](installation.md#logging-in)).

The editor is a React + Three.js r184 application (built once by the launcher or the bootstrap)
that lets you place data center objects visually, assign CFD boundary conditions to surfaces,
and export a multi-solid ASCII STL package plus a `.cfdrun` manifest for the mesh engines.
The legacy single-file editor (`./src/start_server.sh --app html`, then `/STL_generate.html`)
offers the same modeling concepts and button names and is kept as a reference implementation;
the model logic both editors use lives once in `src/frontend/shared/js/`. Where the two differ,
this guide describes the React editor.

Besides air-cooling geometry, the React editor covers:

- **Liquid cooling** — switch the top bar to **💧 Liquid Cooling** to place CDUs, liquid pipes and
  T-branches, add cold plates, CRHs and heat sources to servers, and run the liquid DAE solver
  (see [Liquid DAE workbench](../developer/liquid-dae-workbench.md)).
- **Air pipes (ducts)** — **🧵 New Pipe** in air mode creates a duct object (`kind: pipe`,
  bucket `constructions.pipes`).
- **Stacked servers** — several servers can share one U range at different depths; the rack's
  slot inspector lists them, checks depth overlaps and offers an auto-fit into the largest free gap.
- **Transient timeline** — the top-bar steady/transient switch (separately for air and liquid)
  shows a timeline with keyframe tracks for time-variable values such as server power, ACU supply
  temperature and airflow, or cold-plate heat. Geometry is frozen away from t = 0; the project is
  saved as `.cfdproj` v5 (see [.cfdproj v5 reference](../reference/cfdproj-v5-schema.md)).

---

## UI Layout

```
┌──────────────────────────────────────────────────────────┐
│  Toolbar  (rotate / mirror / align / snap / grid density) │
├─────────────┬────────────────────────┬───────────────────┤
│  Left Panel │    Center Viewport     │   Right Panel     │
│  Model Tree │  (Three.js 3D scene)   │  Property         │
│             │                        │  Inspector        │
│  File ops   │  OrbitControls         │                   │
│  Model list │  Background grid       │  Model / Face /   │
│  Export     │  ViewCube              │  Patch / Device   │
│  buttons    │                        │  CFD parameters   │
├─────────────┴────────────────────────┴───────────────────┤
│  Operation Log  (timestamped action history, collapsible) │
└──────────────────────────────────────────────────────────┘
```

| Panel | Default size | Adjustable range |
|-------|-------------|------------------|
| Left (Model Tree) | 330 px | 260–680 px, collapsible |
| Right (Inspector) | 340 px | 280–760 px, collapsible |
| Bottom (Log) | 160 px | 60–400 px, collapsible |
| Top Toolbar | 50 px | fully collapsible |

**Left panel** — hierarchical list of all models; shows visibility toggle, model type icon,
and a context menu for single-model export or deletion.

**Center viewport** — Three.js 3D scene. Left-drag to orbit, right-drag to pan, scroll to
zoom. A background XZ floor grid is displayed at Y = 0 and a ViewCube shows orientation.

**Right panel** — context-sensitive inspector that changes depending on selection: model
parameters, face parameters, patch parameters, or equipment parameters.

**Operation Log** — every action is recorded with a timestamp and category tag (create /
delete / modify / select / view / file / drag). Use "Export .log" to save the full log.

---

## Model Types

The modeler supports the following model kinds for air-cooling geometry. Individual creation
buttons appear in the left panel: **New Box**, **New Rack**, **New ACU**, **New Wall**,
**New Partition Wall**, **New Baffle**, **New Floor** and **New Pipe** (air duct). In liquid mode
the same toolbar offers **New CDU**, **New Pipe** (liquid pipe segment) and **New T-Branch**, plus
contextual buttons for cold plates, CRHs and heat sources on the selected rack or server. Custom
(imported STL) models are created with the **Import Watertight STL for Projection** button in the
top file-bar of the left panel.

### Box

A parametric cuboid for representing generic equipment, heat sources, or enclosures.

| Parameter | Description | Default |
|-----------|-------------|---------|
| W / D / H | Width × Depth × Height (metres) | 0.6 × 0.4 × 1.8 |
| anchor | Origin of the box — bottom-front-left corner (x, y, z) | (0, 0, 0) |
| orientation | Horizontal rotation angle (degrees) | 0 |
| transform.quaternion | Precise orientation as unit quaternion | identity |
| opacity | Surface opacity (0 = opaque) | 0.0 |

A Box has **six faces** (front / back / left / right / top / bottom). Each face can carry one
or more rectangular or circular Patches with independent CFD parameters.

CFD role is `none` by default (not exported to the `.cfdrun` manifest). If any face patch has a
heat-power or fixed-temperature physics assignment the box is automatically classified as a
`wallObject`.

---

### Rack

A server rack with a full U-slot equipment management system. See [U-Slot System](#u-slot-system)
for details.

| Parameter | Description | Default |
|-----------|-------------|---------|
| W / D / H | Outer rack dimensions (metres) | 0.6 × 1.2 × 1.867 |
| unitCount | Total number of U-slots | 42 |
| unitHeight | Height of one U-slot (metres) | 0.04445 (1U) |
| slotWidthRatio | Slot opening as a fraction of rack width | 0.8 |
| safeMarginTop | Top clearance margin (metres) | 0.04445 |
| safeMarginBottom | Bottom clearance margin (metres) | 0.04445 |

The front and back faces are divided into `unitCount` horizontal rows. Each row can hold
equipment or be left open as a through-cavity (leakage zone).

---

### ACU

A precision air-conditioning unit. Supply and return patches can be placed on any face.

| Parameter | Description | Default |
|-----------|-------------|---------|
| W / D / H | Outer dimensions (metres) | 1.2 × 0.6 × 2.0 |
| anchor | Bottom-front-left corner | (0, 0, 0) |
| Supply patch physics | temperature (°C), volumeFlowRate (m³/s), velocity (m/s) | 15 °C, 4.0, 2.0 |
| Return patch physics | volumeFlowRate (m³/s), velocity (m/s) | 4.0, 2.0 |

An ACU supports **multiple supply patches and multiple return patches** on different faces.
Each patch has independent physics. All supply patches are exported as `supplyFaces[]` and
return patches as `returnFaces[]` in the `.cfdrun` manifest.

---

### Wall

A zero-thickness planar boundary for room perimeter walls.

| Parameter | Description |
|-----------|-------------|
| W / H | Width and height of the wall (metres) |
| anchor | Bottom-left corner of the wall face |
| orientation | Facing direction (degrees) |

Wall geometry is used only for visual reference; `stlFile` is `null` in the CFD JSON because
room boundaries are generated directly from the room polygon.

---

### Partition Wall

A thick solid internal partition wall. Through-holes cut fully through the wall body; no
mesh is kept inside the wall itself.

| Parameter | Description |
|-----------|-------------|
| W / H | Width and height of the partition (metres) |
| anchor | Bottom-left corner |
| orientation | Facing direction (degrees) |

Through-holes are added on the front or back face only. Each hole can be assigned an
independent leakage rate (0 % = maximum resistance, 100 % = zero resistance). Deleting a
hole patch makes that area fully sealed.

The partition wall exports to `merged_partition_wall.stl`.

---

### Baffle

A zero-thickness internal barrier used to model solid dividers or perforated panels.

| Parameter | Description | Default |
|-----------|-------------|---------|
| W / H | Baffle dimensions (metres) | — |
| baffleType | `'wall'` (solid) or `'perforated'` (open area) | `'wall'` |
| porosity | Open-area ratio, 0 = fully closed, 1 = fully open | 0.0 |

---

### Floor

A zero-thickness horizontal plane for raised floors, ceilings, or perforated tile arrays.

| Parameter | Description | Default |
|-----------|-------------|---------|
| W / D | Plane dimensions (metres) | — |
| defaultBC | Default boundary condition type for the solid surface | `'wall'` |
| openings | Array of patch openings, each with a solid name and porosity | [] |

---

### Custom

An imported STL geometry with support for surface patch projection (water transfer printing).

| Parameter | Description |
|-----------|-------------|
| customSTLBase64 | Base64-encoded STL file content — immutable after creation |
| customOffset | Offset of the geometry relative to its anchor |
| solidRegions | List of all named `solid` blocks detected in the imported STL |

**Water transfer printing** — Patches on a Custom model are projected as a 3D beam
(cylindrical or box-shaped) along a specified direction onto the curved surface.
CSG boolean subtraction then extracts the intersection as a separate STL solid.

| Patch parameter | Description |
|----------------|-------------|
| px / py / pz | Patch centre in world coordinates (metres) |
| rx / ry / rz | Projection direction rotation angles (degrees) |
| vx / vy / vz | Projection direction vector (default: face normal) |
| depth | Projection depth (metres) |
| applied | Whether the CSG operation has been executed |

---

## U-Slot System

The U-slot system manages equipment inside rack models. Equipment is assigned to a contiguous
range of U-slots. The slot opening width equals `rack.W × slotWidthRatio`, centred on the
rack face with vertical side frames on each side.

### Equipment Types

#### Fan-Server (active cooling)

The corresponding U-slots on both front and back faces are removed, creating a continuous
airflow channel through the rack.

Generates two STL solids:

- `{rackName}_{serverName}_inlet` — front opening
- `{rackName}_{serverName}_outlet` — rear opening

Physics parameters: `volumeFlowRate` (m³/s) and `power` (W).

```json
{
    "type": "fanServer",
    "inletSolidName": "Rack_A_Server_01_inlet",
    "outletSolidName": "Rack_A_Server_01_outlet",
    "physics": {
        "volumeFlowRate": 0.5,
        "power": 1000,
        "fixedTemperature": null,
        "fixedTempFace": null
    }
}
```

#### Non-Fan Equipment (passive / fixed temperature)

The equipment volume is enclosed — no through-channel. Generates four STL solids:

- `{rackName}_{serverName}_nonfan_front`
- `{rackName}_{serverName}_nonfan_back`
- `{rackName}_{serverName}_nonfan_top`
- `{rackName}_{serverName}_nonfan_bottom`

Physics parameters: `fixedTemperature` (°C) and `fixedTempFace` (`'inlet'` or `'outlet'`).

```json
{
    "type": "nonFan",
    "inletSolidName": "Rack_A_Device_A_nonfan_front",
    "outletSolidName": "Rack_A_Device_A_nonfan_back",
    "topSolidName": "Rack_A_Device_A_nonfan_top",
    "bottomSolidName": "Rack_A_Device_A_nonfan_bottom",
    "physics": {
        "volumeFlowRate": null,
        "power": null,
        "fixedTemperature": 35.0,
        "fixedTempFace": "inlet"
    }
}
```

#### Blanking Plate

Fills empty U-slots to prevent airflow short-circuiting. No physics parameters.

### Through-Cavity (Leakage Zone)

When a contiguous set of U-slots has **both** their front and back faces removed and no
equipment assigned, the modeler treats that run of slots as a through-cavity — an open
passage that models rack leakage.

**Creating a through-cavity:**

1. Select a U-slot row in the inspector.
2. Click **"Sync Delete Opposite (Form Through)"** — removes both the front and back faces of that row.
3. Repeat for adjacent rows. Consecutive open rows are automatically grouped.
4. Through-cavities are highlighted in the viewport as semi-transparent orange boxes.

**Sigma coefficient** — each group has an independent leakage coefficient σ (0–1, default
0.05) editable in the inspector. σ is stored per group under
`rack.throughSlotSigma[groupStartUnit]`.

To restore slots, select the through-cavity and click **"Dissolve Cavity (Restore Both Sides)"** in the
inspector (one undo checkpoint).

**STL naming:** `{rackName}_leakage_{startUnit}_{endUnit}` (e.g. `Rack_A_leakage_12_19`).

**CFD JSON excerpt:**

```json
"leakage": {
    "enabled": true,
    "throughSlots": [12, 13, 14, 15, 16, 17, 18, 19],
    "groups": [
        {
            "startUnit": 12,
            "endUnit": 19,
            "sigma": 0.05,
            "stlSolidName": "Rack_A_leakage_12_19"
        }
    ]
}
```

---

## Face and Patch Editing

Each model exposes up to six faces in the inspector (front / back / left / right / top /
bottom). Click a face in the inspector or in the 3D viewport to open face-edit mode.

### Patch Shapes

| Type | Parameters | Notes |
|------|-----------|-------|
| Rectangular (`rect`) | x, y (bottom-left), w, h (width, height) — all in metres | Applies to all model types |
| Circular (`circle`) | cx, cy (centre), r (radius) — all in metres | Parametric models only |

### Patch Properties

```javascript
{
    id: 'p_1',                // Internal unique ID
    type: 'rect' | 'circle', // Shape
    name: 'Intake_Port',      // Visible name — exported as STL solid name
    color: '#4daafc',         // Display colour

    // Rectangular fields
    x: 0.0, y: 0.0,           // Bottom-left (relative to face)
    w: 0.4, h: 0.3,           // Width × Height (metres)

    // Circular fields
    cx: 0.3, cy: 0.5,         // Centre (relative to face)
    r: 0.15,                  // Radius (metres)

    // CFD physics
    cfd: {
        role: 'supply' | 'return' | 'wall' | 'none',
        temperature: 15.0,        // ACU supply temperature (°C)
        volumeFlowRate: 4.0,      // Flow rate (m³/s)
        velocity: 2.0,            // Face velocity (m/s)
        fixedTemperature: 45.0,   // Fixed wall temperature (°C)
        heatPower: 0.0            // Heat power (W)
    }
}
```

### Patch Naming

Patches are exported as STL solids. The solid name depends on whether the face has a prefix
enabled (`face.prefixEnabled`):

- **Parametric models (Box / Wall / Baffle / Floor)** — `{faceName}_{patch.name}` when prefix
  is enabled; otherwise just `{patch.name}`.
- **ACU** — always `{patch.name}` (no face prefix, no model prefix).
- **Custom** — always `{patch.name}`.

All names are sanitised: any character that is not a letter, digit, or underscore is replaced
with `_`.

### Safety Constraints

- Patches are automatically kept within a 10 % inset margin from the face boundary.
- Patches that exceed the face boundary are clipped and visually flagged.
- Patches can be repositioned and resized by dragging handles in the viewport.

---

## Snapping

Three mutually exclusive snap modes are available from the toolbar:

| Mode | Behaviour |
|------|-----------|
| Face Snap | Aligns the dragged model to the nearest face of any other model |
| Edge Align | Aligns to the nearest edge |
| Grid Snap | Snaps to the closest point on the background grid |

Grid density is configurable (10–1000 mm, default 100 mm). The grid is the XZ floor plane
at Y = 0.

---

## Room 2D Floor Plan Editor

Click **Room 2D Floor Plan** (left panel file-bar, `btn.room_2d_editor`) to enter the
dedicated 2D floor-plan editor (`room2d.enterEditor()`). This is distinct from the 3D
top-down viewport preset — the 2D editor shows a clean XZ floor plan with color-coded
geometry, annotations, and precise polygon editing tools.

Features available in the 2D editor:

| Tool / control | Description |
|----------------|-------------|
| Draw mode | Click to add polygon vertices; close the polygon (≥ 3 vertices) to finish the room outline |
| Select mode | Click vertices or edges to inspect and edit them individually |
| Grid Snap | Snap to background grid points |
| Vertex Snap | Snap to existing polygon vertices |
| Edge Align | Align edges to nearby parallel edges |
| Rotation Snap | Snap rotation to align an edge parallel to the nearest object |
| Drag model in 2D | Reposition placed models in the floor plan (X/Z coordinates only) |
| Rotate 90° | Rotate the floor-plan view 90° for alternate orientation |
| Room Height | Set the vertical room height (used by the CFD mesh enclosure) |
| Apply to 3D Room | Commit the polygon and height back to the 3D scene |

Click **Return to 3D** to exit the 2D editor without applying changes.

---

## Model Library (Templates)

Rack and ACU models can be saved as reusable templates and re-instantiated from the
creation toolbar.

1. Select a Rack or ACU model in the 3D scene.
2. In the right inspector, open the **Model Library** panel and click
   **固定入库 (Save as template)**. Enter a template name when prompted.
3. The template is stored in the project's model library, `models.geometryModels.racks` or
   `models.geometryModels.acus`.
4. The creation toolbar shows a button for each saved template. Clicking it calls
   `addInstanceFromTemplate`, which places a new instance with `fromModel` set to the
   template ID.

Templates persist in the `.cfdproj` file across save/load cycles.

---

## First-Person Mode

Click **Enter FPS** (left panel file-bar, `btn.first_person`) to enter first-person camera
mode. It starts in free-fly mode. Controls: **WASD** move, mouse look,
**Shift** sprint, **Space** up/jump, **C** down/crouch, **F** toggle fly/walk, **ALT** free
the cursor to click UI, **Esc** exit. Click **Back to God View** to return to orbit mode.

---

## Import / Export

### Importing Files

| Action | How |
|--------|-----|
| Import Watertight STL for Projection | Left panel file-bar → **Import Watertight STL for Projection** — creates a new Custom model. Supports both ASCII and binary STL. All named `solid` regions are detected automatically. |
| Load Project | Left panel file-bar → **📂 Load Project** — opens a file dialog that browses the server's file system (starting at the repository's `case/`) or picks a file from your own disk, then restores the `.cfdproj` including all models and embedded STL geometry. Any older format version is migrated on load. |

### Exporting Files

| Button | Output |
|--------|--------|
| **Export CFD Manifold STL** | Opens a dialog to select models, then downloads one or more STL files. |
| **Export CFD Package (JSON+STL)** | Exports a ZIP of all models plus a `.cfdrun` manifest with no dialog. |
| Model tree right-click → "Export" | Downloads a single model as one STL file. |
| **💾 Save Project** | Writes the project as a `.cfdproj` (version 5) to the server path you choose (via `/api/write-file`) or downloads it. An asterisk on the button means there are unsaved changes. |

#### Grouped Export File Names

When **"Group by type"** is selected in the export dialog each file contains all models of
that type (multiple `solid...endsolid` blocks per file):

| File | Contents |
|------|----------|
| `merged_rack_frame.stl` | All rack frames (shell, partitions, blanking panels) |
| `merged_acu.stl` | All ACUs |
| `merged_baffle.stl` | All baffles |
| `merged_floor_ceiling.stl` | All floor / ceiling planes |
| `merged_perforated.stl` | Perforated floor-tile solids (only when a Floor has perforated openings) |
| `merged_box.stl` | All box and CDU models |
| `room_wall.stl` | All wall models |
| `merged_partition_wall.stl` | All partition walls |

In addition to these scene-merged files, per-rack buckets are written as their own `merged_*.stl` files when present — for example `merged_server_<N>U.stl` (fan-server channels), `merged_wallBlankingPanel_<N>U.stl`, `merged_rack_door.stl` / `merged_wall_baffle.stl`, and `merged_through_zone_*.stl`.

Disabled (hidden) models are still exported — they are temporarily rendered to extract
correct geometry and position information.

#### CFD Package ZIP Layout

```
{projectName}_cfd.zip
├── merged_rack_frame.stl
├── merged_acu.stl
├── merged_baffle.stl
├── merged_floor_ceiling.stl
├── merged_perforated.stl        # only when perforated floor openings exist
├── merged_box.stl
├── room_wall.stl
├── merged_partition_wall.stl
└── {projectName}.cfdrun
```

#### Multi-Solid ASCII STL Format

```text
solid {solid_name_1}
  facet normal nx ny nz
    outer loop
      vertex x1 y1 z1
      vertex x2 y2 z2
      vertex x3 y3 z3
    endloop
  endfacet
  ...
endsolid {solid_name_1}

solid {solid_name_2}
  ...
endsolid {solid_name_2}
```

Key requirements:

1. **ASCII format only** — binary STL cannot carry region names.
2. **Watertight** — adjacent regions share exact vertex coordinates (no gaps, no overlaps).
3. **Outward normals** — all triangles follow the right-hand rule with normals pointing into the
   fluid domain.
4. A single file may contain any number of `solid...endsolid` blocks.

---

## Undo / Redo

| Action | Shortcut |
|--------|---------|
| Undo | ++ctrl+z++ (macOS: ++cmd+z++) |
| Redo | ++ctrl+y++ or ++ctrl+shift+z++ |

Each undo step is a full snapshot of the project. The React editor keeps up to 200 snapshots,
fewer for large projects (100 above 50 objects, 60 above 100, 30 above 200). The redo stack is
cleared whenever a new edit happens. In React every edit goes through `projectStore.edit()`,
which takes the checkpoint itself, so an edit cannot forget it.

**Checkpoint rules for the legacy HTML editor** (for developers extending `STL_generate.html`):

| Rule | Detail |
|------|--------|
| Always call `pushUndoCheckpoint()` first | Must precede any state mutation |
| One checkpoint per drag operation | Do not push a checkpoint on every frame |
| Preview / slider | Push one checkpoint on first interaction only; subsequent slider moves do not push |
| Selection-only changes | Do not push a checkpoint |
| State must remain JSON-serialisable | No DOM references, Three.js objects, or functions in state |
| `customSTLBase64` is immutable | Never mutate after the model is created |

---

## Keyboard Shortcuts

| Shortcut | Action |
|---------|--------|
| ++ctrl+z++ | Undo |
| ++ctrl+y++ / ++ctrl+shift+z++ | Redo |
| ++delete++ / ++backspace++ | Delete selected model |
| ++escape++ | Deselect all |

**Global transforms (toolbar):**

| Action | Description |
|--------|-------------|
| Rotate 90° around X / Y / Z | Rotates all selected models |
| Mirror in X / Y | Mirrors all selected models |
| Reset transform | Clears all global transforms |

---

## Units

All internal calculations use **SI metres**. The UI display unit can be toggled in the toolbar:

| Unit | Conversion | Input step | Decimal places |
|------|-----------|-----------|----------------|
| mm | × 0.001 | 1 mm | 1 |
| cm | × 0.01 | 0.1 cm | 2 |
| m (default) | × 1 | 0.01 m | 3 |

One rack U = 44.45 mm (`RACK_DEFAULT_UNIT_HEIGHT = 0.04445 m`).

---

## STL Solid Naming Convention

All names are processed by `sanitizeName()` — any character that is not a letter, digit, or
underscore is replaced with `_`.

### Rack Models

| Surface | Name pattern | Example |
|---------|-------------|---------|
| Fan-server inlet | `{rackName}_{serverName}_inlet` | `Rack_A_Server_01_inlet` |
| Fan-server outlet | `{rackName}_{serverName}_outlet` | `Rack_A_Server_01_outlet` |
| Non-fan front panel | `{rackName}_{serverName}_nonfan_front` | `Rack_A_Device_A_nonfan_front` |
| Non-fan rear panel | `{rackName}_{serverName}_nonfan_back` | `Rack_A_Device_A_nonfan_back` |
| Non-fan top panel | `{rackName}_{serverName}_nonfan_top` | `Rack_A_Device_A_nonfan_top` |
| Non-fan bottom panel | `{rackName}_{serverName}_nonfan_bottom` | `Rack_A_Device_A_nonfan_bottom` |
| Through-cavity (leakage) | `{rackName}_leakage_{startUnit}_{endUnit}` | `Rack_A_leakage_12_19` |
| Frame wall panel | `{rackName}_wall_{side}_{slotIndex}` | `Rack_A_wall_front_3` |
| Channel inner wall | `{rackName}_wall_channel_{serverName}_{side}` | `Rack_A_wall_channel_Server_01_left` |
| Horizontal partition | `{rackName}_partition_{n}` | `Rack_A_partition_5` |

### ACU Models

| Surface | Name pattern | Example |
|---------|-------------|---------|
| Supply patch | `{patchName}` | `Supply_Front_Low` |
| Return patch | `{patchName}` | `Return_Back` |
| Wall face | `{patchName}` or face name | `ACU_01_top` |

### Box Models

| Surface | Name pattern | Example |
|---------|-------------|---------|
| Face with patch, prefix enabled | `{faceName}_{patchName}` | `front_heater_surface` |
| Face with patch, no prefix | `{patchName}` | `heater_surface` |
| Face without patch | `{modelName}_{faceName}` | `Box_01_back` |

### Baffle / Floor / Wall Models

| Surface | Name pattern | Example |
|---------|-------------|---------|
| Main body face | `{modelName}_{faceName}` | `Baffle_01_front` |
| Opening patch | `{faceName}_{patchName}` | `top_Perforated_A` |

### Custom Models

| Surface | Name pattern | Example |
|---------|-------------|---------|
| Imported solid region | `{modelName}` | `Imported_ACU` |
| Projected patch | `{patchName}` | `Supply_Port_1` |

---

## Integration with DCTwin

Mesh and solve start from the editor's Mesh and Solver tabs; the editor exports the STL files and
the `.cfdrun` manifest from the live project each time, so unsaved edits reach the run. The active
pipeline is:

```
Editor (browser)
    │  export STL files + .cfdrun (live project, t = 0 for steady runs)
    ▼
case/<name>/Mesh/constant/triSurface/*.stl  +  case/<name>/model/<name>.cfdrun
    │  STLGenRunner (validates the manifest, builds the in-memory BC registry)
    ▼
dcCartMesh (default) | snappyHexMesh  →  createBaffles  →  strict checkMesh
    ▼
buoyantBoussinesqSimpleFoam_new (steady) | buoyantBoussinesqPimpleFoam_new (transient)
```

The engine is chosen on the Mesh tab ([Mesh engines](../reference/mesh-engines.md)). The
manifest's bindings are described in the [.cfdrun reference](../reference/cfdrun-v2-schema.md);
the geometry block the mesh pipeline reads (`meshGeometry`) still uses the older field names
below.

Key field mappings in the `meshGeometry` block:

| `.cfdrun` field | DCTwin usage |
|------------------------|-------------|
| `racks[*].servers[*].inletSolidName` | OpenFOAM inlet boundary |
| `racks[*].leakage.groups[*].stlSolidName` | `snappyHexMesh` cell zone |
| `racks[*].leakage.groups[*].sigma` | Porous-media σ coefficient |
| `acus[*].supplyFaces[*]` | `fixedValue` inlet boundaries |
| `acus[*].returnFaces[*]` | Return boundaries: each return paired one-to-one with a plain supply draws that supply's airflow (`flowRateOutletVelocity`); the remaining one is the pressure reference (`p_rgh` fixed at 0) |
| `wallObjects[*].heatPatches[*].physics.fixedTemperature` | Fixed-temperature wall BC |

`leakageStlFile` and `stlFile` both point to `merged_rack_frame.stl` because through-cavity
solids are embedded in the rack STL file. DCTwin locates the specific solid using the
`stlSolidName` field.
