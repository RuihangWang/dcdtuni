# Auto-CFD Workflow Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create 5 Claude Code skills that drive end-to-end automated data center CFD: user sketch → structured parameters → cfdproj → mesh+solve → analysis, plus reverse dataset generation from existing cases.

**Architecture:** Skills are `.md` files in `.claude/commands/` with YAML frontmatter (`description`, `allowed-tools`). Each skill provides step-by-step instructions for Claude Code, including exact JS API calls via `/api/browser/exec-js`, server API calls via curl, and validation checks. The agent reads `dc-params.json` (intermediate format) and drives the STLGeneratorForOpenFoam headless browser to build scenes programmatically.

**Tech Stack:** Claude Code skills (.md), STLGeneratorForOpenFoam server API (port 8005), headless Playwright browser, Three.js frontend JS API, OpenFOAM

**Spec:** `docs/superpowers/specs/2026-05-12-auto-cfd-workflow-skills-design.md`

---

## File Structure

```
.claude/commands/                    # Claude Code skills (invoked as /dc-interpret etc.)
├── dc-interpret.md                  # Skill 1: image+prompt → dc-params.json
├── dc-build-cfdproj.md              # Skill 2: dc-params.json → .cfdproj via JS API
├── dc-run-cfd.md                    # Skill 3: .cfdproj → mesh+solve via server API
├── dc-analyze.md                    # Skill 4: case → convergence + thermal report
└── dc-dataset-reverse.md            # Skill 5: existing cfdproj → dataset pairs

scripts/
└── validate_dc_case.py              # Validation helper for generated cfdproj

demo_case/paintToDC1/input/
└── prompt.md                        # Test prompt (currently empty, needs content)
```

---

## Shared Constants

These values are referenced across multiple skills and tasks. Defined once here to avoid drift.

**Server auth:**
```bash
# Login and store cookie
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}'
```

**exec-js helper pattern** (used in all JS API calls):
```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/exec-js \
  -H "Content-Type: application/json" \
  -d '{"js": "YOUR_JS_CODE_HERE"}'
```

**JS API key formats** (verified from source):
- `updateModel(id, 'anchorX', val)` → sets `model.anchor.x` (key is `'anchorX'`/`'anchorY'`/`'anchorZ'`)
- `updateModel(id, 'W', val)` → sets `model.base.W` (key is `'W'`/`'D'`/`'H'`)
- `updateModelRotation(id, axis, degrees)` — axis is a property of the euler object
- `setMeshConfig(key, val)` → sets `state.cfdRoom[key]` directly
- `updateAcuPatchPhysics(id, patchId, field, val)` — field: `role`, `velocity`, `temperature`, `volumeFlowRate`
- `updateRackServerCfd(id, equipmentId, field, val)` — field: `power`, `volumeFlowRate`

**Direct state manipulation for batched construction** (more reliable than individual updateModel calls which do undo+bounds checks):
```javascript
(() => {
  window.createDefaultRackModel();
  const m = state.models[state.models.length - 1];
  m.name = 'Rack_Col1_1';
  m.anchor = {x: 2.5, y: 0, z: 1.3};
  m.base = {W: 0.8, D: 1.2, H: 2.2};
  // 90° around Y: quaternion = (0, sin45, 0, cos45)
  m.transform.quaternion = {x: 0, y: 0.7071067811865476, z: 0, w: 0.7071067811865476};
  syncRackModelState(m);
  rebuildGeometrySingle(m.id);
  return {id: m.id, name: m.name};
})()
```

**Common rotation quaternions:**
| Degrees (Y-axis) | Quaternion {x, y, z, w} |
|-------------------|-------------------------|
| 0° | {x:0, y:0, z:0, w:1} |
| 90° | {x:0, y:0.7071067811865476, z:0, w:0.7071067811865476} |
| -90° (= 270°) | {x:0, y:-0.7071067811865476, z:0, w:0.7071067811865476} |
| 180° | {x:0, y:1, z:0, w:0} |

---

### Task 1: Write paintToDC1 test prompt

**Files:**
- Modify: `demo_case/paintToDC1/input/prompt.md` (currently empty)

This provides the test input for validating dc-interpret.

- [ ] **Step 1: Write the prompt content**

Write this to `demo_case/paintToDC1/input/prompt.md`:

```markdown
数据中心机房，14米宽7米深3米高。

4列服务器机柜（每列6个），左侧2列出风朝右，右侧2列出风朝左，形成2条热通道。
每柜功耗约5kW。

左墙和右墙各放2台ACU（共4台），送风温度18度，顶部回风。
热通道两端有挡板，没有顶盖。
```

- [ ] **Step 2: Verify**

```bash
cat demo_case/paintToDC1/input/prompt.md
```

Expected: the Chinese text above, ~6 lines.

- [ ] **Step 3: Commit**

```bash
git add demo_case/paintToDC1/input/prompt.md
git commit -m "docs: add paintToDC1 test prompt for dc-interpret skill"
```

---

### Task 2: Create dc-interpret skill

**Files:**
- Create: `.claude/commands/dc-interpret.md`

- [ ] **Step 1: Write the skill file**

Write this to `.claude/commands/dc-interpret.md`:

````markdown
---
description: "Analyze a user's 2D data center floor plan image + text prompt → extract layout parameters → check completeness → ask user or mock defaults → output dc-params.json. Use when user provides a hand-drawn/rough data center layout for automated CFD."
allowed-tools: [Bash, Read, Write, Edit]
---

# /dc-interpret — Data Center Layout Interpretation

Analyze the user's 2D floor plan image and text prompt to produce a structured `dc-params.json` file.

## Input

The user provides:
1. **A 2D image** of a data center floor plan (hand-drawn or rough sketch) — may show rack rows, ACU positions, hot/cold aisle labels, airflow arrows
2. **A text prompt** describing the data center — may include dimensions, power, temperatures, or may be very brief

Both are typically in a case directory: `{case_dir}/input/prompt.md` and `{case_dir}/input/*.png`

## Procedure

### Step 1: Read the inputs

1. Read the image file using the Read tool (Claude is multimodal — it can see PNG/JPG directly)
2. Read the `prompt.md` text file

### Step 2: Extract visible information from the image

From the image, extract **topology only** (not precise numbers):
- How many rack columns? How many racks per column?
- Rack airflow direction (inlet → outlet arrows)
- How many ACUs? Which walls are they on?
- Are there hot aisle / cold aisle labels?
- Are there baffles/containment panels?
- Rough room aspect ratio (is it long-narrow or square?)

**CRITICAL: Never guess precise numerical values from the image.** The image provides layout topology only. Precise dimensions, temperatures, and flow rates must come from the text prompt or user confirmation.

### Step 3: Parse the text prompt

Extract any explicit values mentioned:
- Room dimensions (length × width × height)
- Rack count, rack power (kW)
- ACU supply temperature, flow rate
- Solver preferences

### Step 4: Check parameter completeness

Compare extracted parameters against this required checklist:

| Parameter | Required? | Mock Default |
|-----------|-----------|-------------|
| Room width (X, m) | YES | 10 |
| Room depth (Z, m) | YES | 6 |
| Room height (Y, m) | YES | 3 |
| Rack columns count | YES (from image) | — |
| Racks per column | YES (from image) | — |
| Rack orientation per column | YES (from image) | — |
| Rack size W×D×H (m) | preferred | 0.6 × 1.2 × 2.2 |
| Rack spacing (m) | preferred | 0.8 |
| Per-rack power (W) | preferred | 5000 |
| Server equipment per rack | optional | 1× fanServer, 5U at slot 29 |
| ACU count | YES (from image) | — |
| ACU position (which wall) | YES (from image) | — |
| ACU size W×D×H (m) | optional | 1.8 × 1.0 × 2.0 |
| ACU supply temperature (°C) | preferred | 18 |
| ACU supply flow rate (m³/s) | preferred | 2.5 |
| ACU return face | preferred | top |
| Baffles (hot aisle containment) | from image | none |
| Containment top lid | preferred | no lid |
| Solver type | optional | steady |
| Mesh base size (m) | optional | 0.34 |
| Parallel cores | optional | 8 |
| End time (iterations) | optional | 1000 |
| Room temperature (°C) | optional | 22 |

### Step 5: Present summary and ask for missing info

Show the user a consolidated summary in this format:

```
从您的图纸和描述中，我提取到以下信息：
- 房间：14m × 7m × 3m ✅
- 4 列 rack，每列 6 个 ✅
- Rack 朝向：左2列朝右(+X)，右2列朝左(-X) ✅
- 4 台 ACU（左墙2台，右墙2台）✅
- 热通道隔离：2条热通道，有南北挡板，无顶盖 ✅

以下信息缺失，请补充（或回复"用默认值"）：
- 每 rack 功耗？（默认 5kW）
- ACU 送风温度？（默认 18°C）
- ACU 送风流量？（默认 2.5 m³/s）
- Rack 尺寸 W×D×H？（默认 0.6×1.2×2.2 m）
```

If the user replies "用默认值" or similar, apply all defaults at once.

### Step 6: Generate dc-params.json

After filling in all parameters, compute the derived values:
- `startPosition` for each rack column (distribute evenly based on room dimensions and column count)
- ACU positions (along the specified wall, spaced evenly)
- Baffle positions (at the ends of hot aisles, between rack column pairs)

Write the complete JSON to `{case_dir}/dc-params.json`:

```json
{
  "projectName": "...",
  "room": {
    "width": 14,
    "depth": 7,
    "height": 3,
    "polygon": [[0,0],[14,0],[14,7],[0,7]]
  },
  "rackColumns": [
    {
      "name": "Col1",
      "count": 6,
      "startPosition": [2.5, 0, 1.3],
      "spacing": 0.8,
      "orientation": 90,
      "rackSize": {"W": 0.8, "D": 1.2, "H": 2.2},
      "equipments": [
        {
          "kind": "fanServer",
          "name": "fan-server_1",
          "startUnit": 29,
          "spanUnits": 5,
          "power_W": 5000,
          "flowRate_m3s": null
        }
      ]
    }
  ],
  "acus": [
    {
      "name": "ACU_Left_1",
      "position": [1.3, 0, 1.5],
      "size": {"W": 1.8, "D": 1.0, "H": 2.0},
      "orientation": -90,
      "supply": {
        "face": "front",
        "temperature_C": 18,
        "flowRate_m3s": 2.5,
        "velocity_ms": null,
        "direction": [0, 0, -1]
      },
      "return": {
        "face": "top",
        "direction": [0, 0, -1]
      }
    }
  ],
  "baffles": [
    {
      "name": "Baffle_HA1_South",
      "position": [5.0, 0, 2.5],
      "size": {"W": 4.0, "D": 0.01, "H": 2.2},
      "orientation": 0
    }
  ],
  "cfdSettings": {
    "solverType": "steady",
    "meshBaseSize": 0.34,
    "maxGlobalCells": 1000000,
    "parallelCores": 8,
    "endTime": 1000,
    "roomTemperature_C": 22
  }
}
```

### Step 7: Confirm with user

Print a brief summary of the generated dc-params.json:
- Total models: N racks + M ACUs + K baffles + walls + floor + ceiling
- Room: W × D × H
- Total rack power: X kW
- Total ACU cooling: Y m³/s at Z°C

Ask: "参数确认无误？确认后我将调用 /dc-build-cfdproj 开始构建。"

## Coordinate System

- X = room width direction (left → right when viewed from above)
- Y = height (floor = 0, ceiling = room height)
- Z = room depth direction (front → back)
- Rack orientation: 0° = front (inlet) faces -Z; 90° = front faces +X; -90° = front faces -X

## Layout Computation Hints

For a symmetric 4-column layout with 2 hot aisles:
- Columns are in pairs: (Col1, Col2) face each other forming Hot Aisle 1, (Col3, Col4) forming Hot Aisle 2
- Col1 orientation=90° (inlet faces +X), Col2 orientation=-90° (inlet faces -X)
- Baffles placed at Z_min and Z_max of each rack column pair
- ACUs on left wall: orientation=-90° (supply faces +X into room)
- ACUs on right wall: orientation=90° (supply faces -X into room)
````

- [ ] **Step 2: Verify the file is valid**

```bash
head -3 .claude/commands/dc-interpret.md
```

Expected: YAML frontmatter starting with `---`.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/dc-interpret.md
git commit -m "feat: add dc-interpret skill for data center layout analysis"
```

---

### Task 3: Create dc-build-cfdproj skill

**Files:**
- Create: `.claude/commands/dc-build-cfdproj.md`

This is the core skill — it drives the headless browser to programmatically construct a cfdproj.

- [ ] **Step 1: Write the skill file**

Write this to `.claude/commands/dc-build-cfdproj.md`:

````markdown
---
description: "Build a .cfdproj file from dc-params.json by programmatically calling frontend JS API via the headless browser. Use after /dc-interpret has produced dc-params.json."
allowed-tools: [Bash, Read, Write]
---

# /dc-build-cfdproj — Programmatic CFD Project Builder

Build a complete `.cfdproj` file by driving the STLGeneratorForOpenFoam headless browser via `/api/browser/exec-js`.

## Input

- `{case_dir}/dc-params.json` — structured parameters from /dc-interpret

## Prerequisites

The STLGeneratorForOpenFoam server must be running. Check and start if needed:

```bash
# Check server
curl -s http://localhost:8005/api/server-info 2>/dev/null | head -1
# If no response, start it:
fuser -k 8005/tcp 8006/tcp 2>/dev/null
conda run -n dcdtuni --no-banner python STLGeneratorForOpenFoam/server/server.py \
  --case-dir {case_dir} &
sleep 5
```

Then login:
```bash
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"WIN#@desk"}'
```

## Procedure

Read `dc-params.json` first, then execute Steps 1-9 in order. Each step is an exec-js call.

**All JS is executed via:**
```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/exec-js \
  -H "Content-Type: application/json" \
  -d '{"js": "ESCAPED_JS_HERE"}'
```

### Step 1: Initialize empty scene

```javascript
(() => {
  state.models = [];
  state.groups = [];
  state.sensorPoints = [];
  state.projectName = 'PROJECT_NAME';
  state.exportFileName = 'PROJECT_NAME';
  state.inputUnit = 'm';
  state.schemaVersion = 2;
  rebuildGeometry();
  return {success: true, modelCount: state.models.length};
})()
```

Replace `PROJECT_NAME` with `params.projectName`.

### Step 2: Configure room polygon and bounds

```javascript
(() => {
  // Set room polygon
  state.roomPolygon = {
    vertices: POLYGON_VERTICES,
    height: ROOM_HEIGHT
  };
  // Set project bounds from room dimensions
  state.bounds = {
    xMin: {enabled: false, value: 0},
    xMax: {enabled: false, value: ROOM_WIDTH},
    yMin: {enabled: true, value: 0},
    yMax: {enabled: false, value: ROOM_HEIGHT + 0.6},
    zMin: {enabled: false, value: 0},
    zMax: {enabled: false, value: ROOM_DEPTH}
  };
  // Set cfdRoom height and basic settings
  state.cfdRoom = state.cfdRoom || {};
  state.cfdRoom.height = ROOM_HEIGHT;
  return {bounds: state.bounds, polygon: state.roomPolygon};
})()
```

Replace: `POLYGON_VERTICES` = `params.room.polygon`, `ROOM_WIDTH/DEPTH/HEIGHT` from `params.room`.

### Step 3: Create Floor and Ceiling

```javascript
(() => {
  const W = ROOM_WIDTH;
  const D = ROOM_DEPTH;
  const H = ROOM_HEIGHT;
  const results = [];

  // Floor (y=0, flat on ground)
  window.createDefaultFloorModel();
  const floor = state.models[state.models.length - 1];
  floor.name = 'Room_Floor';
  floor.anchor = {x: 0, y: 0, z: 0};
  floor.base = {W: W, D: D, H: 0};
  rebuildGeometrySingle(floor.id);
  results.push({id: floor.id, name: floor.name});

  // Ceiling (y=H)
  window.createDefaultFloorModel();
  const ceil = state.models[state.models.length - 1];
  ceil.name = 'Room_Ceiling';
  ceil.anchor = {x: 0, y: H, z: 0};
  ceil.base = {W: W, D: D, H: 0};
  rebuildGeometrySingle(ceil.id);
  results.push({id: ceil.id, name: ceil.name});

  return results;
})()
```

### Step 4: Create 4 Walls (rectangular room)

```javascript
(() => {
  const W = ROOM_WIDTH;   // X extent
  const D = ROOM_DEPTH;   // Z extent
  const H = ROOM_HEIGHT;
  const results = [];

  // Wall definitions: [name, anchorX, anchorZ, wallW, wallH, yawDeg]
  // Wall is a thin surface. Its "W" dimension runs along its width, "H" is height.
  // Wall kind has base.D forced to 0 by the frontend.
  const walls = [
    ['Room_Wall_S', 0, 0, W, H, 0],            // South wall at z=0, facing +Z
    ['Room_Wall_N', 0, D, W, H, 0],             // North wall at z=D, facing +Z
    ['Room_Wall_W', 0, 0, D, H, 90],            // West wall at x=0, facing +X
    ['Room_Wall_E', W, 0, D, H, 90],            // East wall at x=W, facing +X
  ];

  for (const [name, ax, az, ww, wh, yaw] of walls) {
    window.createDefaultWallModel();
    const m = state.models[state.models.length - 1];
    m.name = name;
    m.anchor = {x: ax, y: 0, z: az};
    m.base = {W: ww, D: 0, H: wh};
    if (yaw !== 0) {
      const rad = yaw * Math.PI / 180;
      const s = Math.sin(rad / 2), c = Math.cos(rad / 2);
      m.transform.quaternion = {x: 0, y: s, z: 0, w: c};
    }
    rebuildGeometrySingle(m.id);
    results.push({id: m.id, name: m.name});
  }
  return results;
})()
```

### Step 5: Create Rack Columns (core loop)

For **each** rack column in `params.rackColumns`, execute one exec-js call that creates all racks in that column:

```javascript
(() => {
  const col = {
    name: 'COL_NAME',
    count: COUNT,
    startX: START_X, startY: 0, startZ: START_Z,
    spacing: SPACING,
    rackW: RACK_W, rackD: RACK_D, rackH: RACK_H,
    qx: Q_X, qy: Q_Y, qz: Q_Z, qw: Q_W
  };
  const ids = [];

  for (let i = 0; i < col.count; i++) {
    window.createDefaultRackModel();
    const m = state.models[state.models.length - 1];
    m.name = `Rack_${col.name}_${i + 1}`;
    m.anchor = {x: col.startX, y: col.startY, z: col.startZ + i * col.spacing};
    m.base = {W: col.rackW, D: col.rackD, H: col.rackH};
    m.transform.quaternion = {x: col.qx, y: col.qy, z: col.qz, w: col.qw};
    syncRackModelState(m);
    rebuildGeometrySingle(m.id);
    ids.push(m.id);
  }
  return {column: col.name, created: ids.length, ids: ids};
})()
```

Replace placeholders from `params.rackColumns[i]`. Use the quaternion lookup table:
- orientation 0° → qx=0, qy=0, qz=0, qw=1
- orientation 90° → qx=0, qy=0.7071067811865476, qz=0, qw=0.7071067811865476
- orientation -90° → qx=0, qy=-0.7071067811865476, qz=0, qw=0.7071067811865476

### Step 6: Add Equipment to Racks

For **each** rack that was created, add its equipment. Execute one call per rack:

```javascript
(() => {
  const rackId = 'RACK_MODEL_ID';
  const model = state.models.find(m => m.id === rackId);
  if (!model) return {error: 'rack not found'};

  // Create equipment at slot
  window.createRackEquipmentAtSlot(rackId, 'front', START_UNIT, 'EQUIP_KIND');
  const eq = model.rack.equipments[model.rack.equipments.length - 1];

  // Set CFD properties (power in watts, flow rate in m³/s)
  window.updateRackServerCfd(rackId, eq.id, 'power', POWER_W);
  if (FLOW_RATE !== null) {
    window.updateRackServerCfd(rackId, eq.id, 'volumeFlowRate', FLOW_RATE);
  }

  return {rackId: rackId, equipmentId: eq.id, kind: eq.kind, power: POWER_W};
})()
```

Replace: `RACK_MODEL_ID` from Step 5 results, `START_UNIT`/`EQUIP_KIND`/`POWER_W`/`FLOW_RATE` from `params.rackColumns[].equipments[]`.

**Note:** If a rack has multiple equipments, call `createRackEquipmentAtSlot` once per equipment in the same exec-js block.

### Step 7: Create ACUs

For **each** ACU in `params.acus`:

```javascript
(() => {
  // Create ACU
  window.createDefaultAcuModel();
  const m = state.models[state.models.length - 1];
  m.name = 'ACU_NAME';
  m.anchor = {x: POS_X, y: POS_Y, z: POS_Z};
  m.base = {W: SIZE_W, D: SIZE_D, H: SIZE_H};
  const rad = ORIENTATION_DEG * Math.PI / 180;
  const s = Math.sin(rad / 2), c = Math.cos(rad / 2);
  m.transform.quaternion = {x: 0, y: s, z: 0, w: c};
  rebuildGeometrySingle(m.id);

  // Add supply patch on the specified face
  window.addPatch(m.id, 'SUPPLY_FACE', 'rect');
  const supplyPatch = m.faces['SUPPLY_FACE'].patches[m.faces['SUPPLY_FACE'].patches.length - 1];
  // Make patch cover the full face
  supplyPatch.x = 0;
  supplyPatch.y = 0;
  supplyPatch.w = m.base[m.faces['SUPPLY_FACE'].wType];
  supplyPatch.h = m.base[m.faces['SUPPLY_FACE'].hType];
  supplyPatch.name = 'Supply';
  // Set supply velocity direction vector
  supplyPatch.showVector = true;
  supplyPatch.vx = DIR_X;
  supplyPatch.vy = DIR_Y;
  supplyPatch.vz = DIR_Z;

  // Set ACU physics: role=supply, temperature, flow rate
  window.updateAcuPatchPhysics(m.id, supplyPatch.id, 'role', 'supply');
  window.updateAcuPatchPhysics(m.id, supplyPatch.id, 'temperature', SUPPLY_TEMP_C);
  window.updateAcuPatchPhysics(m.id, supplyPatch.id, 'volumeFlowRate', SUPPLY_FLOW_M3S);

  // Add return patch on the specified face
  window.addPatch(m.id, 'RETURN_FACE', 'rect');
  const returnPatch = m.faces['RETURN_FACE'].patches[m.faces['RETURN_FACE'].patches.length - 1];
  returnPatch.x = 0;
  returnPatch.y = 0;
  returnPatch.w = m.base[m.faces['RETURN_FACE'].wType];
  returnPatch.h = m.base[m.faces['RETURN_FACE'].hType];
  returnPatch.name = 'Return';
  returnPatch.showVector = true;
  returnPatch.vx = RET_DIR_X;
  returnPatch.vy = RET_DIR_Y;
  returnPatch.vz = RET_DIR_Z;

  window.updateAcuPatchPhysics(m.id, returnPatch.id, 'role', 'return');
  // Return flow rate is auto-synced by updateAcuPatchPhysics when role is set

  rebuildGeometrySingle(m.id);
  return {id: m.id, name: m.name, supplyPatchId: supplyPatch.id, returnPatchId: returnPatch.id};
})()
```

Replace all CAPS placeholders from `params.acus[i]`.

### Step 8: Create Baffles

For **each** baffle in `params.baffles`:

```javascript
(() => {
  window.createDefaultBaffleModel();
  const m = state.models[state.models.length - 1];
  m.name = 'BAFFLE_NAME';
  m.anchor = {x: POS_X, y: POS_Y, z: POS_Z};
  m.base = {W: SIZE_W, D: 0, H: SIZE_H};  // baffles have D=0 (thin surface)
  if (ORIENTATION_DEG !== 0) {
    const rad = ORIENTATION_DEG * Math.PI / 180;
    const s = Math.sin(rad / 2), c = Math.cos(rad / 2);
    m.transform.quaternion = {x: 0, y: s, z: 0, w: c};
  }
  rebuildGeometrySingle(m.id);
  return {id: m.id, name: m.name};
})()
```

### Step 9: Configure CFD settings

```javascript
(() => {
  const cr = state.cfdRoom;
  cr.solverType = 'SOLVER_TYPE';
  cr.meshBaseSize = MESH_BASE_SIZE;
  cr.maxGlobalCells = MAX_GLOBAL_CELLS;
  cr.endTime = END_TIME;
  cr.roomTemperature = ROOM_TEMP_C;
  cr.parallelSolve = true;
  cr.solveProcess = PARALLEL_CORES;
  cr.writeInterval = END_TIME;
  cr.locationInMesh = {x: LOC_X, y: 0.5, z: LOC_Z};

  // Set reasonable defaults for schemes and relaxation
  cr.solverPreset = 'fast';
  cr.residualControl = {p_rgh: 0.0001, U: 0.00001, T: 0.00001, k_epsilon: 0.0001};
  cr.relaxationFactors = {p_rgh: 0.3, U: 0.7, T: 0.7, k_epsilon: 0.7};

  return {solverType: cr.solverType, meshBase: cr.meshBaseSize, cores: cr.solveProcess};
})()
```

`locationInMesh` should be a point inside the fluid domain (not inside any rack/ACU). A safe default: `{x: room.width/2, y: 0.5, z: room.depth/2}` — adjust if that point lands inside a model.

### Step 10: Save project

```javascript
(() => {
  const data = JSON.parse(JSON.stringify(state));
  return JSON.stringify(data);
})()
```

Save the returned JSON string to `{case_dir}/{projectName}.cfdproj` using the Write tool.

### Step 11: Verify

1. Check model count:
```bash
curl -s -b /tmp/stlgen_cookie.txt http://localhost:8005/api/browser/state | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = d.get('models', [])
by_kind = {}
for m in models:
    by_kind[m['kind']] = by_kind.get(m['kind'], 0) + 1
print(f'Total models: {len(models)}')
for k, v in sorted(by_kind.items()):
    print(f'  {k}: {v}')
"
```

2. Take a screenshot for visual verification:
```bash
curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/browser/screenshot?name=build_verify"
```
Then read the screenshot: `scripts/screenshots/build_verify.png`

3. Show the screenshot to the user and ask for confirmation before proceeding.

## Error Recovery

- If an exec-js call returns an error, check `/api/browser/logs?clear=false` for console errors
- If a model ends up out of bounds, adjust project bounds first (Step 2), then retry
- If `createRackEquipmentAtSlot` fails with "slot in safety zone", adjust `startUnit` (valid range: `safeMarginBottom` to `unitCount - safeMarginTop - spanUnits`)
````

- [ ] **Step 2: Verify**

```bash
wc -l .claude/commands/dc-build-cfdproj.md
```

Expected: ~200+ lines.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/dc-build-cfdproj.md
git commit -m "feat: add dc-build-cfdproj skill for programmatic scene construction"
```

---

### Task 4: Create dc-run-cfd skill

**Files:**
- Create: `.claude/commands/dc-run-cfd.md`

- [ ] **Step 1: Write the skill file**

Write this to `.claude/commands/dc-run-cfd.md`:

````markdown
---
description: "Run the full CFD pipeline (STL export → mesh generation → solve) for a .cfdproj file via server API. Use after /dc-build-cfdproj has produced a .cfdproj."
allowed-tools: [Bash, Read]
---

# /dc-run-cfd — Execute CFD Pipeline

Drive STL export, mesh generation, and CFD solve through the server API.

## Input

- A `.cfdproj` file path
- A case directory (where Mesh/ output goes)

## Prerequisites

Server must be running and authenticated (same as dc-build-cfdproj).

## CRITICAL RULES (from CLAUDE.md)

- **NEVER** run serial solver — always parallel via server API
- **NEVER** use `conda run -n dcdtuni python -m dcdtuni.runner.run_cfd` — always via server API
- **NEVER** use `kill`/`pkill`/`killall` — only `fuser -k <port>/tcp`
- Multiple mesh regions are NOT errors when hot/cold aisle containment exists

## Procedure

### Step 0: Set case directory

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/set-case-dir \
  -H "Content-Type: application/json" \
  -d '{"caseDir":"CASE_DIR_PATH"}'
```

### Step 1: Export + Mesh generation

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/full-pipeline \
  -H "Content-Type: application/json" \
  -d '{"projPath":"CFDPROJ_PATH","meshOnly":true}'
```

Response: `{"success": true, "jobId": "JOB_ID", ...}`

Save the `jobId`.

### Step 2: Poll for mesh completion

Poll every 10 seconds:

```bash
curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/job-status?jobId=JOB_ID"
```

Response: `{"done": bool, "returncode": int, "lines": [...]}`

- Print the last 3-5 new log lines each poll to show progress
- When `done=true` and `returncode=0` → mesh succeeded, continue
- When `done=true` and `returncode!=0` → mesh failed:
  - Print the last 20 lines of log output
  - Report the error and stop
  - Common causes: STL geometry issues, insufficient memory for maxGlobalCells

### Step 3: Mesh quality check

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/run-check-mesh
```

Parse the response and report:
- Cell count (total)
- Max non-orthogonality
- Max skewness
- Number of regions (multiple regions are OK with aisle containment!)
- Whether checkMesh reports "Mesh OK" or warnings

If there are severe issues (non-orthogonality > 80°, skewness > 4), warn the user but do NOT auto-abort.

### Step 4: Start solver

```bash
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/full-pipeline \
  -H "Content-Type: application/json" \
  -d '{"projPath":"CFDPROJ_PATH","solveOnly":true}'
```

Save the new `jobId`.

### Step 5: Poll for solve completion

Poll every 15 seconds:

```bash
curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/job-status?jobId=JOB_ID"
```

- Print residual values from the latest log lines (look for lines containing "Solving for Ux", "Solving for T", etc.)
- When `done=true` and `returncode=0` → solve succeeded
- When `done=true` and `returncode!=0` → solve failed:
  - Print last 20 lines
  - If NaN detected: suggest reducing relaxation factors
  - If segfault: suggest checking mesh quality

### Step 6: Report completion

Print a summary:
- Total mesh cells
- Solver iterations completed
- Final residual values
- Time elapsed

Suggest running `/dc-analyze` for detailed thermal analysis.

## Error Handling

| Error | Recovery |
|-------|----------|
| Server not running | Start with `conda run -n dcdtuni --no-banner python STLGeneratorForOpenFoam/server/server.py --case-dir CASE_DIR &`, wait 5s |
| Auth expired | Re-login with the curl cookie command |
| STL export failed | Check `/api/browser/logs?clear=false`, verify cfdproj is valid |
| Mesh OOM | Reduce `maxGlobalCells` in cfdproj, regenerate |
| Solver NaN | Check boundary conditions, reduce relaxation factors |
| Poll timeout (>30min) | Warn user, do NOT auto-abort — user may want to continue waiting |
````

- [ ] **Step 2: Commit**

```bash
git add .claude/commands/dc-run-cfd.md
git commit -m "feat: add dc-run-cfd skill for CFD pipeline execution"
```

---

### Task 5: Create dc-analyze skill

**Files:**
- Create: `.claude/commands/dc-analyze.md`

- [ ] **Step 1: Write the skill file**

Write this to `.claude/commands/dc-analyze.md`:

````markdown
---
description: "Analyze CFD results: check residual convergence and ACU return temperatures for heat balance. Use after /dc-run-cfd has completed a solve."
allowed-tools: [Bash, Read, Write]
---

# /dc-analyze — CFD Results Analysis

Check convergence and compute ACU return temperature heat balance.

## Input

- A completed CFD case directory (with solver results)
- The corresponding `dc-params.json` (for reference values: supply temperature, flow rate, rack power)

## Scope

This skill ONLY checks:
1. Residual convergence
2. ACU return face temperatures
3. Overall heat balance

It does NOT produce flow visualizations, temperature contours, or local hotspot analysis.

## Procedure

### Step 1: Confirm solve is complete

```bash
curl -s -b /tmp/stlgen_cookie.txt http://localhost:8005/api/scan-case-status
```

Check `solveComplete` in the response. If not complete, tell user to run `/dc-run-cfd` first.

### Step 2: Extract residual history

Read the solver log from the latest run directory:

```bash
# Find the latest run directory
RUN_DIR=$(ls -td CASE_DIR/Mesh/run_* 2>/dev/null | head -1)
# Extract final residuals
grep "Solving for" "$RUN_DIR/log.solve" | tail -20
```

Parse the final residual values for each field:
- `Ux`, `Uy`, `Uz` → report as `U`
- `p_rgh`
- `T`
- `k`, `epsilon`

Compare against targets:

| Field | Target | Pass condition |
|-------|--------|---------------|
| p_rgh | 1e-4 | final < target |
| U | 1e-5 | final < target |
| T | 1e-5 | final < target |
| k | 1e-4 | final < target |
| epsilon | 1e-4 | final < target |

### Step 3: Extract ACU return temperatures

Try the probes API first:

```bash
curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/probes-latest"
```

If probes data is not available, use OpenFOAM postProcess as fallback:

```bash
cd CASE_DIR/Mesh
conda run -n dcdtuni --no-banner postProcess -func 'patchAverage(name="ACU_*_Return*", T)' -latestTime
```

For each ACU return patch, extract the area-averaged temperature T_return (in Kelvin, convert to °C: T_C = T_K - 273.15).

### Step 4: Compute heat balance

Read `dc-params.json` for reference values:

```
For each ACU:
  Q_supply = flowRate_m3s × ρ_air × Cp_air × T_supply_K
  Q_return = flowRate_m3s × ρ_air × Cp_air × T_return_K
  Q_cooling = Q_return - Q_supply  (heat removed by this ACU)

Total_cooling = Σ Q_cooling for all ACUs
Total_rack_power = Σ power_W for all racks

Balance_error = |Total_cooling - Total_rack_power| / Total_rack_power × 100%
```

Constants: ρ_air = 1.225 kg/m³, Cp_air = 1005 J/(kg·K)

Steady-state criterion: balance_error < 5%

### Step 5: Generate report

Output to conversation AND save to `{case_dir}/analysis_report.md`:

```markdown
## CFD Analysis Report

### Convergence Status
| Field | Final Residual | Target | Status |
|-------|---------------|--------|--------|
| p_rgh | 3.2e-5 | 1e-4 | PASS |
| U | 8.1e-6 | 1e-5 | PASS |
| T | 4.5e-6 | 1e-5 | PASS |
| k | 7.3e-5 | 1e-4 | PASS |
| epsilon | 5.1e-5 | 1e-4 | PASS |

### ACU Return Temperatures
| ACU | T_supply (°C) | T_return (°C) | ΔT (°C) |
|-----|--------------|--------------|---------|
| ACU_Left_1 | 18.0 | 28.3 | 10.3 |
| ACU_Left_2 | 18.0 | 27.8 | 9.8 |
| ACU_Right_1 | 18.0 | 29.1 | 11.1 |
| ACU_Right_2 | 18.0 | 28.5 | 10.5 |

### Heat Balance
- Total rack power: 120.0 kW
- Total ACU cooling: 118.5 kW
- Balance error: 1.25%
- Status: PASS (< 5%)
```
````

- [ ] **Step 2: Commit**

```bash
git add .claude/commands/dc-analyze.md
git commit -m "feat: add dc-analyze skill for CFD results analysis"
```

---

### Task 6: Create dc-dataset-reverse skill

**Files:**
- Create: `.claude/commands/dc-dataset-reverse.md`

- [ ] **Step 1: Write the skill file**

Write this to `.claude/commands/dc-dataset-reverse.md`:

````markdown
---
description: "Reverse-generate dataset from existing .cfdproj files: load project → capture 2D top-down screenshot → extract parameters → generate natural-language prompt → organize as input/label pairs in demo_case/. Use for building evaluation dataset."
allowed-tools: [Bash, Read, Write]
---

# /dc-dataset-reverse — Reverse Dataset Generation

Generate training/evaluation dataset from existing cfdproj files.

## Input

- Directory of existing cfdproj files: `STLGeneratorForOpenFoam/projects/`
- Output directory: `demo_case/`

## Case Selection

**Include these cfdproj files:**
Base604, Base2-9, Base_coldAisleIso, Base_leakage, CMI_China, CMI-Guichou, Alibaba_hangchou, Alibaba-sige, GTC_2025, STDCT, DMAL_Testbed, Shinaide, Slab

**Skip these:**
- paintToDC1 (already has manual annotations)
- Liquid, Shinaide_liquid (liquid cooling only — out of scope)
- SingleRack (too simple, not a data center)

**Optional (include if demonstrating generalization):**
- Polygon_room, Tri_angle (non-rectangular rooms)

## Prerequisites

Server must be running with headless browser. Same startup as dc-build-cfdproj.

## Procedure (for each case)

### Step 1: Load project into headless browser

```bash
PROJ_PATH="STLGeneratorForOpenFoam/projects/CASE_NAME.cfdproj"
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/load-proj \
  -H "Content-Type: application/json" \
  -d "{\"path\":\"$(realpath $PROJ_PATH)\"}"
```

### Step 2: Capture 2D top-down screenshot

```bash
# Switch to top-down view
curl -s -b /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/browser/exec-js \
  -H "Content-Type: application/json" \
  -d '{"js": "window.applyViewPreset(\"top\"); typeof requestRender === \"function\" && requestRender(); \"done\""}'

# Wait for render
sleep 1

# Take screenshot
curl -s -b /tmp/stlgen_cookie.txt "http://localhost:8005/api/browser/screenshot?name=CASE_NAME_topview"
```

Copy the screenshot to the dataset:
```bash
mkdir -p demo_case/CASE_NAME/input
cp scripts/screenshots/CASE_NAME_topview.png demo_case/CASE_NAME/input/topview.png
```

### Step 3: Extract parameters from cfdproj JSON

Use Python to parse the cfdproj and extract key parameters:

```bash
python3 -c "
import json, sys
with open('STLGeneratorForOpenFoam/projects/CASE_NAME.cfdproj') as f:
    data = json.load(f)

models = data.get('models', [])
room = data.get('roomPolygon', {})
cfd = data.get('cfdRoom', {})

# Count by kind
by_kind = {}
for m in models:
    k = m.get('kind', '?')
    by_kind[k] = by_kind.get(k, 0) + 1

# Room dimensions from polygon
verts = room.get('vertices', [])
if verts:
    xs = [v[0] for v in verts]
    zs = [v[1] for v in verts]
    width = max(xs) - min(xs)
    depth = max(zs) - min(zs)
else:
    width = depth = 0
height = room.get('height', cfd.get('height', 3))

# Rack details
racks = [m for m in models if m['kind'] == 'rack']
acus = [m for m in models if m['kind'] == 'acu']
baffles = [m for m in models if m['kind'] == 'baffle']

# Print summary
print(f'Room: {width}m x {depth}m x {height}m')
print(f'Models: {by_kind}')
print(f'Racks: {len(racks)}, ACUs: {len(acus)}, Baffles: {len(baffles)}')

# Extract rack power
for r in racks[:2]:
    cfd_data = r.get('cfd', {})
    servers = cfd_data.get('servers', [])
    for s in servers:
        print(f'  {r[\"name\"]}: power={s.get(\"power\",\"?\")}W, flow={s.get(\"volumeFlowRate\",\"?\")}m3/s')

# Extract ACU settings
for a in acus[:2]:
    cfd_data = a.get('cfd', {})
    pp = cfd_data.get('patchPhysics', {})
    for pid, phys in pp.items():
        if phys.get('role') == 'supply':
            print(f'  {a[\"name\"]}: T={phys.get(\"temperature\",\"?\")}C, Q={phys.get(\"volumeFlowRate\",\"?\")}m3/s')
"
```

### Step 4: Generate natural-language prompt.md

Based on the extracted parameters, write a realistic user description in Chinese. **Vary the style across cases:**

- Some cases: detailed, specifying all parameters
- Some cases: brief, omitting power/temperature (to test dc-interpret's mock defaults)
- Some cases: mention only the image and ask the agent to figure it out

Write the prompt to `demo_case/CASE_NAME/input/prompt.md`.

Example for a detailed case:
```markdown
一个 10m×6m×3m 的数据中心机房。
2 列服务器机柜，每列 6 个，面对面排列形成 1 条热通道。
每个机柜功耗 8kW，43U 高。
左墙 1 台 ACU，送风温度 16°C，流量 3.0 m³/s，顶部回风。
无热通道隔离。
```

Example for a brief case:
```markdown
参考图纸布局，帮我建一个 CFD 模型。ACU 送风温度 18 度。
```

### Step 5: Copy cfdproj as label

```bash
mkdir -p demo_case/CASE_NAME/label
cp STLGeneratorForOpenFoam/projects/CASE_NAME.cfdproj demo_case/CASE_NAME/label/CASE_NAME.cfdproj
```

### Step 6: Verify the dataset entry

```bash
ls -la demo_case/CASE_NAME/input/
ls -la demo_case/CASE_NAME/label/
cat demo_case/CASE_NAME/input/prompt.md
```

Read the topview.png screenshot to visually confirm it shows a clear top-down view.

## Batch Execution

Process all cases in order. After each case, print progress:
```
[3/13] CMI_China — done (24 racks, 4 ACUs)
```

After all cases, commit:
```bash
git add demo_case/
git commit -m "dataset: generate 13 reverse-engineered cases from existing cfdproj files"
```
````

- [ ] **Step 2: Commit**

```bash
git add .claude/commands/dc-dataset-reverse.md
git commit -m "feat: add dc-dataset-reverse skill for dataset generation"
```

---

### Task 7: Create validation script

**Files:**
- Create: `scripts/validate_dc_case.py`

This script validates that a generated cfdproj matches dc-params.json expectations.

- [ ] **Step 1: Write the validation script**

Write this to `scripts/validate_dc_case.py`:

```python
#!/usr/bin/env python3
"""Validate a generated .cfdproj against its dc-params.json source."""

import json
import sys
from pathlib import Path


def load_json(path):
    with open(path) as f:
        return json.load(f)


def validate(params_path, cfdproj_path):
    params = load_json(params_path)
    proj = load_json(cfdproj_path)
    errors = []
    warnings = []

    models = proj.get("models", [])
    by_kind = {}
    for m in models:
        k = m.get("kind", "unknown")
        by_kind.setdefault(k, []).append(m)

    # --- Room dimensions ---
    poly = proj.get("roomPolygon", {})
    verts = poly.get("vertices", [])
    if verts:
        xs = [v[0] for v in verts]
        zs = [v[1] for v in verts]
        actual_w = max(xs) - min(xs)
        actual_d = max(zs) - min(zs)
        expected_w = params["room"]["width"]
        expected_d = params["room"]["depth"]
        if abs(actual_w - expected_w) > 0.5:
            errors.append(f"Room width: expected {expected_w}, got {actual_w}")
        if abs(actual_d - expected_d) > 0.5:
            errors.append(f"Room depth: expected {expected_d}, got {actual_d}")

    # --- Rack count ---
    expected_racks = sum(col["count"] for col in params.get("rackColumns", []))
    actual_racks = len(by_kind.get("rack", []))
    if actual_racks != expected_racks:
        errors.append(f"Rack count: expected {expected_racks}, got {actual_racks}")

    # --- ACU count ---
    expected_acus = len(params.get("acus", []))
    actual_acus = len(by_kind.get("acu", []))
    if actual_acus != expected_acus:
        errors.append(f"ACU count: expected {expected_acus}, got {actual_acus}")

    # --- Baffle count ---
    expected_baffles = len(params.get("baffles", []))
    actual_baffles = len(by_kind.get("baffle", []))
    if actual_baffles != expected_baffles:
        warnings.append(f"Baffle count: expected {expected_baffles}, got {actual_baffles}")

    # --- Wall + Floor count ---
    actual_walls = len(by_kind.get("wall", []))
    actual_floors = len(by_kind.get("floor", []))
    if actual_walls < 4:
        errors.append(f"Wall count: expected >= 4, got {actual_walls}")
    if actual_floors < 2:
        errors.append(f"Floor count: expected >= 2 (floor+ceiling), got {actual_floors}")

    # --- ACU patches ---
    for acu_model in by_kind.get("acu", []):
        has_supply = False
        has_return = False
        cfd = acu_model.get("cfd", {})
        pp = cfd.get("patchPhysics", {})
        for pid, phys in pp.items():
            if phys.get("role") == "supply":
                has_supply = True
            if phys.get("role") == "return":
                has_return = True
        if not has_supply:
            errors.append(f"ACU {acu_model['name']}: missing supply patch physics")
        if not has_return:
            errors.append(f"ACU {acu_model['name']}: missing return patch physics")

    # --- Rack equipment ---
    for rack_model in by_kind.get("rack", []):
        equips = rack_model.get("rack", {}).get("equipments", [])
        if not equips:
            warnings.append(f"Rack {rack_model['name']}: no equipment installed")

    # --- Report ---
    print(f"\n{'='*60}")
    print(f"Validation: {cfdproj_path}")
    print(f"{'='*60}")
    print(f"Models: {len(models)} total ({', '.join(f'{k}:{len(v)}' for k, v in sorted(by_kind.items()))})")

    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")
    if not errors and not warnings:
        print("\n  ✓ All checks passed!")

    print()
    return len(errors) == 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <dc-params.json> <generated.cfdproj>")
        sys.exit(1)
    ok = validate(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
```

- [ ] **Step 2: Make executable and test**

```bash
chmod +x scripts/validate_dc_case.py
python3 scripts/validate_dc_case.py --help 2>&1 || true
```

Expected: usage message.

- [ ] **Step 3: Commit**

```bash
git add scripts/validate_dc_case.py
git commit -m "feat: add validation script for generated cfdproj files"
```

---

### Task 8: End-to-end test — dc-interpret with paintToDC1

**Files:** (none created — this is a test task)

- [ ] **Step 1: Invoke dc-interpret**

In a Claude Code session, run:
```
/dc-interpret
```

When prompted, point it to:
- Image: `demo_case/paintToDC1/input/simplecase1.png`
- Prompt: `demo_case/paintToDC1/input/prompt.md`
- Output dir: `demo_case/paintToDC1/`

- [ ] **Step 2: Review the generated dc-params.json**

```bash
cat demo_case/paintToDC1/dc-params.json | python3 -m json.tool
```

Verify:
- `room.width` = 14, `room.depth` = 7, `room.height` = 3
- 4 rack columns, each with count=6
- Orientation: Col1/Col2 = 90°, Col3/Col4 = -90° (or vice versa, matching the image)
- 4 ACUs (2 left, 2 right)
- Baffles present for hot aisles

- [ ] **Step 3: Compare against reference**

```bash
python3 -c "
import json
with open('demo_case/paintToDC1/label/paintToDC1.cfdproj') as f:
    ref = json.load(f)
by_kind = {}
for m in ref['models']:
    by_kind[m['kind']] = by_kind.get(m['kind'], 0) + 1
print('Reference cfdproj model counts:', by_kind)
print('Expected: rack:24, acu:4, wall:4, floor:2, baffle:4')
"
```

If dc-interpret produces significantly wrong parameters, adjust the skill and re-run.

---

### Task 9: End-to-end test — dc-build-cfdproj with paintToDC1

- [ ] **Step 1: Ensure server is running**

```bash
curl -s http://localhost:8005/api/server-info | head -1
# If not running:
# fuser -k 8005/tcp 8006/tcp 2>/dev/null
# conda run -n dcdtuni --no-banner python STLGeneratorForOpenFoam/server/server.py --case-dir demo_case/paintToDC1 &
# sleep 5
```

- [ ] **Step 2: Invoke dc-build-cfdproj**

```
/dc-build-cfdproj
```

Point it to `demo_case/paintToDC1/dc-params.json`.

- [ ] **Step 3: Validate the generated cfdproj**

```bash
python3 scripts/validate_dc_case.py \
  demo_case/paintToDC1/dc-params.json \
  demo_case/paintToDC1/paintToDC1.cfdproj
```

Expected: all checks pass (or only warnings).

- [ ] **Step 4: Visual verification**

Read the screenshot produced by dc-build-cfdproj's verification step. Compare the layout against the original `simplecase1.png`.

If the layout is wrong (racks in wrong positions, ACUs missing, etc.), identify the issue, fix the skill, and re-run.

---

### Task 10: End-to-end test — dc-run-cfd + dc-analyze

- [ ] **Step 1: Invoke dc-run-cfd**

```
/dc-run-cfd
```

Point it to the cfdproj generated in Task 9.

Wait for mesh generation (~5-15 min depending on cell count) and solver (~10-30 min).

- [ ] **Step 2: Monitor progress**

The skill should print log lines during polling. Watch for:
- "Mesh OK" after mesh generation
- Residual values decreasing during solve
- No NaN or divergence

- [ ] **Step 3: Invoke dc-analyze**

```
/dc-analyze
```

Point it to the case directory.

- [ ] **Step 4: Review the analysis report**

Read `demo_case/paintToDC1/analysis_report.md`. Verify:
- All residuals converged below targets
- ACU return temperatures are physically reasonable (25-35°C for typical DC)
- Heat balance error < 5%

If the solve fails or results are unreasonable, debug and iterate on the skills.

---

### Task 11: Generate dataset batch

- [ ] **Step 1: Invoke dc-dataset-reverse**

```
/dc-dataset-reverse
```

This will process all 13 eligible cases from `STLGeneratorForOpenFoam/projects/`.

- [ ] **Step 2: Monitor progress**

The skill prints progress after each case. Total time estimate: ~15-30 minutes (most time is spent on browser screenshot rendering).

- [ ] **Step 3: Verify dataset completeness**

```bash
for d in demo_case/*/; do
  name=$(basename "$d")
  has_img="✗"; has_prompt="✗"; has_label="✗"
  [ -f "$d/input/topview.png" ] || [ -f "$d/input/simplecase1.png" ] && has_img="✓"
  [ -s "$d/input/prompt.md" ] && has_prompt="✓"
  ls "$d/label/"*.cfdproj >/dev/null 2>&1 && has_label="✓"
  echo "$name: img=$has_img prompt=$has_prompt label=$has_label"
done
```

Expected: all cases show ✓ for all three fields.

- [ ] **Step 4: Spot-check 3 random cases**

Pick 3 cases, read their `topview.png` and `prompt.md`. Verify:
- Screenshot shows a clear top-down view of the data center
- Prompt is a realistic natural-language description
- Prompt varies in verbosity across cases

- [ ] **Step 5: Commit the dataset**

```bash
git add demo_case/
git commit -m "dataset: add 13 reverse-engineered cases for auto-CFD evaluation"
```

---

### Task 12: Final cleanup and commit

- [ ] **Step 1: Verify all skills are registered**

```bash
ls -la .claude/commands/dc-*.md
```

Expected: 5 files (dc-interpret, dc-build-cfdproj, dc-run-cfd, dc-analyze, dc-dataset-reverse).

- [ ] **Step 2: Verify dataset completeness**

```bash
echo "Total cases in demo_case/:"
ls -d demo_case/*/ | wc -l
echo "Expected: 14-16 (paintToDC1 + 13 reverse-generated)"
```

- [ ] **Step 3: Final commit if any uncommitted changes**

```bash
git status
# If any changes:
git add -A
git commit -m "chore: finalize auto-CFD skills and dataset"
```
