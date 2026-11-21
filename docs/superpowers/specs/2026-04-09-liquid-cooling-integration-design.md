# Liquid Cooling Integration Design Spec

**Date**: 2026-04-09
**Status**: Approved
**Scope**: Integrate liquid cooling simulation into dcdtuni (CFD engine) and frontend (browser UI)

---

## 1. Design Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Cooling scenario | Hybrid cooling + pure liquid (pure as degenerate case) |
| 2 | Liquid cooling topology | D2C (Direct-to-Chip) + RDHx (Rear Door Heat Exchanger) |
| 3 | Solver type | Steady-state first, architecture reserves transient |
| 4 | Air-liquid coupling | One-way (liquid → air): liquid solver computes residual heat, feeds into CFD |
| 5 | 3D pipe visualization | Simplified 3D pipes (TubeGeometry), not part of CFD mesh |
| 6 | Modeling interaction | Auto-generate pipe network by default + advanced manual editing |
| 7 | Results display | 3D heatmap + table, integrated into existing post-processing tab |
| 8 | CDU configuration | Black-box default (supply temp + flow), advanced mode expands component-level |
| 9 | Server cold plate config | 2D "Equipment Level View" with drag-and-connect port wiring |

---

## 2. Architecture Overview

**Approach**: Embedded architecture (Option A) — liquid cooling as native extension of existing system, no separate microservice. Frontend JS logic extracted into independent files to avoid HTML bloat.

### System Diagram

```
STL_generate.html + js/*.js
├── Existing models: Box, Rack, ACU, Wall, Baffle, Floor
├── New model: CDU
├── Rack extension: per-server cold plate config, liquid cooling toggle
├── New 3D layer: pipe rendering (TubeGeometry, auto path calculation)
├── New 2D view: Equipment Level View (Canvas 2D, cold plate wiring)
└── Post-processing: liquid results panel in existing tab

cfd_export.json (extended)
├── New: liquidCooling { cdus, piping, specifications, fluid }
├── Extended: racks[].servers[].liquidCooling { coldPlates, routing }
└── New: racks[].rdhx { enabled, capacity, flowRate }

server.py (extended)
├── POST /api/liquid/solve       → standalone liquid network solve (sub-second)
├── POST /api/run-coupled-sim    → liquid solve + modify heat source + air CFD (serial)
└── GET  /api/liquid/results     → cached liquid results JSON

src/backend/liquidcooling/ (NEW module, written from scratch)
├── components/   → Pipe, Pump, ColdPlate, Valve, HeatExchanger, QuickConnect
├── devices/      → CDU, Manifold, RDHx
├── solver/       → steady-state (Newton-Raphson / Levenberg-Marquardt)
├── thermal/      → chip temperature model, hybrid cooling load distribution (eta)
├── topology.py   → cfd_export.json → NetworkX graph + auto topology generation
├── factory.py    → specification dict → component instances
└── results.py    → result data structures and serialization
```

### Coupling Pipeline

```
User clicks "Run Coupled Simulation"
        |
        v
Step 1: Liquid Network Solve (sub-second, pure Python)
  topology.py: cfd_export.json → NetworkX DiGraph
    ├── Auto mode: derive topology from CDU.servedRacks
    └── Manual mode: read piping.chains directly
  factory.py: instantiate nn.Module components from specifications
  solver/steady.py: solve_hydraulic_fast()
    → per-edge flow rate Q, per-node pressure P
  thermal/: for each liquid-cooled server:
    ├── eta = liquid heat capture ratio (HybridCoolingModel)
    ├── T_chip_max = max chip temperature
    ├── T_liquid_out = cold plate outlet water temperature
    └── Q_residual = (1 - eta) * P_server → heat source for air CFD
        |
        v
Step 2: Modify Air CFD Heat Sources
  stlgen_runner.py: _build_and_save_bridge_bc()
    ├── Liquid-cooled servers: power = Q_residual (not full power)
    ├── fan_outlet temp recalculated: T = T_room + Q_residual/(rho*Cp*Q_air)
    └── Non-liquid-cooled servers: unchanged
  bridge_bc.json: new BC types "liquid_cooled_fan_outlet", "liquid_cooled_nonfan"
        |
        v
Step 3: Air CFD (existing pipeline unchanged)
  blockMesh → snappyHexMesh → buoyantSimpleFoam
  (heat sources already reflect liquid cooling residual)
        |
        v
Step 4: Merge Results
  Liquid results JSON + Air CFD results → unified frontend display
```

---

## 3. Data Model: cfd_export.json Extension

### 3.1 New Top-Level Field: liquidCooling

```jsonc
{
  // ...existing fields (room, racks, acus, meshConfig, simulationConfig)...

  "liquidCooling": {
    "enabled": true,
    "fluid": {
      "type": "water",           // "water" | "glycol_25" | "glycol_50" | "custom"
      "rho": 992.2,              // kg/m3
      "mu": 0.000653,            // Pa*s
      "cp": 4180,                // J/(kg*K)
      "k": 0.632                 // W/(m*K)
    },
    "cdus": {
      "CDU_1": {
        "geometry": { "anchor": {"x":0,"y":0,"z":0}, "size": {"W":0.8,"D":0.6,"H":1.8} },
        "supplyTemperature": 35,           // degC (black-box mode)
        "totalFlowRate": 60,               // LPM (black-box mode)
        "advanced": {                      // optional, omit for black-box
          "pump": { "specName": "pump_default", "rpm": 3000 },
          "heatExchanger": { "specName": "hx_default" },
          "valve": { "specName": "valve_default", "opening": 1.0 }
        },
        "servedRacks": ["Rack_1", "Rack_2"]
      }
    },
    "specifications": {
      "pumpSpec": [{ "name": "pump_default", "headRef": 46.77, "quadCoef": 63454, "rpmRef": 3000 }],
      "hxSpec": [{ "name": "hx_default", "coefA": 0.5, "coefB": 0.3, "coefC": 0.0 }],
      "valveSpec": [{ "name": "valve_default", "kvMax": 947, "characteristic": "eqperc" }],
      "coldPlateSpec": [{ "name": "cp_default", "coefA": 0.724, "coefB": 0.991, "coefC": -0.155 }],
      "pipeSpec": [{ "name": "pipe_25mm", "diameter": 0.025, "roughness": 1e-5 }]
    },
    "piping": {
      "autoGenerated": true,
      "mainPipeSpec": "pipe_25mm",
      "branchPipeSpec": "pipe_25mm",
      "chains": []                       // manual mode: DiffLiquidTwin chain format
    }
  }
}
```

### 3.2 Extended: racks[].servers[].liquidCooling

```jsonc
{
  "servers": [{
    "id": "Rack_1_server_01",
    "type": "fanServer",
    // ...existing fields (inletSolidName, outletSolidName, physics)...

    "liquidCooling": {
      "enabled": true,
      "coldPlates": [
        {
          "id": "cp_cpu",
          "label": "CPU Cold Plate",
          "specName": "cp_default",
          "chipTDP": 350,                           // W
          "thermalModel": {
            "chipArea": 0.004,                      // m2
            "chipThickness": 0.001,                 // m
            "thermalConductivity": 148              // W/(m*K) silicon
          },
          "position": { "x": 0.25, "y": 0.5 }     // normalized 2D position in Equipment View
        },
        {
          "id": "cp_gpu",
          "label": "GPU Cold Plate",
          "specName": "cp_default",
          "chipTDP": 700,
          "thermalModel": { "chipArea": 0.006, "chipThickness": 0.001, "thermalConductivity": 148 },
          "position": { "x": 0.65, "y": 0.5 }
        }
      ],
      "routing": {
        "type": "series",                           // "series" | "parallel" | "custom"
        "connections": [
          // custom mode: user-drawn connections
          { "from": "manifold_in", "to": "cp_cpu.in" },
          { "from": "cp_cpu.out", "to": "cp_gpu.in" },
          { "from": "cp_gpu.out", "to": "manifold_out" }
        ]
      }
    }
  }]
}
```

### 3.3 Extended: racks[].rdhx (Phase 8)

```jsonc
{
  "Rack_1": {
    // ...existing fields...
    "rdhx": {
      "enabled": false,
      "heatRejectionCapacity": 30000,       // W
      "waterFlowRate": 20,                  // LPM
      "coilSpec": { "coefA": 0.5, "coefB": 0.2, "coefC": 0.0 }
    }
  }
}
```

### 3.4 Backward Compatibility

- `liquidCooling` field is entirely optional; absence means pure air cooling
- `racks[].servers[].liquidCooling` is optional per server; absence means air-only server
- `racks[].rdhx` is optional; absence means no rear door heat exchanger
- All existing cfd_export.json files work without modification

---

## 4. Backend: src/backend/liquidcooling/ Module

### 4.1 Module Structure

```
src/backend/liquidcooling/
├── __init__.py
├── components/                  # Hydraulic component models (PyTorch nn.Module)
│   ├── __init__.py
│   ├── pipe.py                  # Darcy-Weisbach pipe flow with elevation
│   ├── pump.py                  # Affinity-law pump model (speed-dependent)
│   ├── cold_plate.py            # Quadratic dP-Q cold plate model
│   ├── valve.py                 # Kv-based control valve (linear/eqperc/quick)
│   ├── heat_exchanger.py        # Quadratic dP-Q heat exchanger
│   └── quick_connect.py         # Quick connector pressure losses
├── devices/                     # Device-level assemblies
│   ├── __init__.py
│   ├── cdu.py                   # CDU: pump + heat exchanger + valve composite
│   ├── manifold.py              # Flow distribution across parallel branches
│   └── rdhx.py                  # Rear Door Heat Exchanger (Phase 8)
├── solver/
│   ├── __init__.py
│   ├── steady.py                # Steady-state solver (Newton-Raphson + Levenberg-Marquardt)
│   └── transient.py             # Transient solver (reserved, empty initially)
├── thermal/
│   ├── __init__.py
│   ├── chip_model.py            # Max chip temperature prediction
│   └── hybrid_cooling.py        # Liquid/air heat load distribution (eta)
├── topology.py                  # cfd_export.json → NetworkX DiGraph + auto topology
├── factory.py                   # Specification dict → component instances
└── results.py                   # Result data structures and serialization
```

### 4.2 Relationship to DiffLiquidTwin

DiffLiquidTwin (at `Ref_code/DiffLiquidTwin/`) serves as **reference only**. The `src/backend/liquidcooling/` module is written from scratch within dcdtuni's package structure, coding conventions, and test framework. DiffLiquidTwin is not imported or depended upon.

**What to reference from DiffLiquidTwin**:
- Physics equations (Darcy-Weisbach, affinity laws, quadratic dP-Q, Kv valve model)
- Solver algorithms (Newton-Raphson with autograd Jacobian, sparse LM with incidence matrix)
- JSON topology format (piping.chains structure for manual mode)

**What to reference from dctwin (at `Ref_code/extracted/dctwin/`)**:
- `D2CServerModel`: chip temperature model and hybrid cooling load distribution
- `HybridCoolingLoadDistributionModel`: thermal resistance network for eta calculation
- `HeatExchangerModel`: NTU-effectiveness model (for advanced CDU mode)

### 4.3 Component Model Interface

All components follow the same interface (consistent with DiffLiquidTwin pattern):

```python
class HydraulicComponent(nn.Module):
    """Base interface for all hydraulic components."""

    def forward(self, pressure_in: torch.Tensor, pressure_out: torch.Tensor) -> torch.Tensor:
        """Given inlet/outlet pressure (kPa), return volumetric flow rate (LPM).
        Positive = flow from pressure_in to pressure_out."""
        ...
```

### 4.4 Solver Algorithm

Primary solver: **Levenberg-Marquardt with sparse Jacobian** (reference: DiffLiquidTwin `manager/hydraulic_fast.py`)

```
Residual: r = B^T @ q   (mass balance at each variable-pressure node)
Jacobian: J = B^T @ diag(dq/dDP) @ B  (sparse, O(E) per iteration)
Update:   (J^T J + lambda*I) delta = -J^T r
Damping:  lambda decreases on accept, increases on reject
Convergence: ||r||_2 < 1e-8
Precision: torch.float64
```

Boundary conditions:
- Black-box CDU: fixed supply pressure + fixed return pressure (or fixed supply temperature + flow rate)
- Advanced CDU: pump, HX, valve as components in the graph with their own edges

### 4.5 Thermal Models

**Chip Temperature Model** (reference: dctwin `d2c_server.py:ChipThermoModel`):
```
T_chip_max = T_water_in + alpha * P/S * (1/h_w + L/k_s + S/(m_w * Cp_w))

Where:
  T_water_in = cold plate inlet water temperature (from hydraulic solver)
  P = chip TDP (W)
  S = chip heat transfer area (m2)
  h_w = water-side heat transfer coefficient (Dittus-Boelter)
  L = chip thickness (m)
  k_s = silicon thermal conductivity (148 W/m*K)
  m_w = water mass flow rate (kg/s, from hydraulic solver)
  Cp_w = water specific heat (J/kg*K)
```

**Hybrid Cooling Load Distribution** (reference: dctwin `d2c_server.py:HybridCoolingLoadDistributionModel`):
```
eta = N / (M + N)

Where:
  M = water-side thermal resistance (cold plate conduction + convection)
  N = air-side thermal resistance (conduction + natural/forced convection)
  
  Nu_water = 0.023 * Re^0.8 * Pr^(1/3)  (Dittus-Boelter)
  Nu_air = 0.664 * Re^0.6 * Pr^(1/3)    (flat plate correlation)

Liquid-cooled power: Q_liquid = eta * chip_TDP * cold_plate_count
Cold plate outlet temp: T_out = T_in + Q_liquid / (m_w * Cp_w)
Residual heat to air: Q_residual = server_power - Q_liquid
```

### 4.6 Topology Auto-Generation

When `piping.autoGenerated = true`, `topology.py` constructs the network graph automatically:

```
For each CDU:
  Create supply/return pressure boundary nodes

  For each servedRack:
    Create rack manifold inlet/outlet nodes
    Add supply pipe: CDU_supply → Rack_manifold_in (with mainPipeSpec)
    Add return pipe: Rack_manifold_out → CDU_return (with mainPipeSpec)

    For each liquid-cooled server in rack:
      Create server inlet/outlet nodes

      If routing.type == "series":
        manifold_in → pipe → cp[0] → pipe → cp[1] → ... → pipe → manifold_out
      elif routing.type == "parallel":
        manifold_in → T_junction → [pipe→cp[0]→pipe, pipe→cp[1]→pipe] → T_junction → manifold_out
      elif routing.type == "custom":
        Build edges directly from routing.connections[]

  If CDU has advanced config:
    Add pump, heat_exchanger, valve as edges in CDU internal subgraph
  Else (black-box):
    Fix supply node pressure based on supplyTemperature and totalFlowRate
```

### 4.7 Server API Endpoints

```python
# POST /api/liquid/solve
# Standalone liquid network solve (sub-second, no OpenFOAM dependency)
# Body: cfd_export.json (with liquidCooling field)
# Returns:
{
  "nodes": { "CDU_1_sup": {"pressure_kPa": 120.5}, ... },
  "edges": { "chain0:step0:pump": {"flowRate_LPM": 45.2}, ... },
  "servers": {
    "Rack_1_server_01": {
      "liquidFlowRate_LPM": 2.5,
      "coldPlateInletTemp_C": 35.0,
      "coldPlateOutletTemp_C": 42.3,
      "chipMaxTemp_C": 68.5,
      "liquidHeatCaptureRatio": 0.85,
      "residualHeatToAir_W": 105.0
    }
  },
  "cdus": {
    "CDU_1": { "returnTemp_C": 41.2, "totalFlow_LPM": 60.0, "pumpPower_W": 850 }
  }
}

# POST /api/run-coupled-sim
# Serial pipeline: liquid solve → modify heat sources → air CFD
# Body: cfd_export.json
# Returns: jobId (async, progress streamed via SSE)

# GET /api/liquid/results?caseDir=...
# Read cached liquid results from case directory
# Returns: same format as /api/liquid/solve
```

---

## 5. Frontend Architecture

### 5.1 New JS Files

```
src/frontend/web/js/
├── liquid_cooling.js              # Global state, enable/disable, export logic
├── liquid_cdu_model.js            # CDU 3D model creation, property panel, appearance
├── liquid_pipe_renderer.js        # 3D pipe rendering (TubeGeometry + auto path)
├── liquid_topology_editor.js      # Equipment Level View (Canvas 2D) + manual pipe editing
└── liquid_results_panel.js        # Post-processing: results table + 3D coloring
```

All files loaded via `<script src="js/liquid_*.js">` in STL_generate.html.

### 5.2 CDU Model Type

New model type in the left panel model tree (alongside Box, Rack, ACU, Wall, Baffle, Floor):

```javascript
// CDU data structure
{
  kind: 'cdu',
  name: 'CDU_1',
  base: { W: 0.8, D: 0.6, H: 1.8 },
  cfd: { role: 'cdu' },
  liquidCooling: {
    supplyTemperature: 35,        // degC
    totalFlowRate: 60,            // LPM
    servedRacks: [],
    advancedMode: false,
    advanced: {
      pump: { specName: 'pump_default', rpm: 3000 },
      heatExchanger: { specName: 'hx_default' },
      valve: { specName: 'valve_default', opening: 1.0 }
    }
  }
}
```

**3D appearance**: Parametric box in blue-teal color scheme, "CDU" label on top, blue/red cylinders marking supply/return ports.

**Property panel (right-side inspector)**:

```
┌─ CDU_1 Properties ────────────────────┐
│ Name:     [CDU_1            ]         │
│ Position: X[0.0] Y[0.0] Z[0.0]       │
│ Size:     W[0.8] D[0.6] H[1.8]       │
│                                        │
│ ── Liquid Cooling Parameters ──        │
│ Supply Temperature:  [35  ] °C         │
│ Total Flow Rate:     [60  ] LPM        │
│ Served Racks:  [☑Rack_1 ☑Rack_2]      │
│                                        │
│ ☐ Advanced Mode                        │
│ ┌─ (expanded when checked) ──────────┐ │
│ │ Pump Model:    [pump_default  ▼]   │ │
│ │ Pump Speed:    [3000] RPM          │ │
│ │ Heat Exchanger:[hx_default    ▼]   │ │
│ │ Valve Model:   [valve_default ▼]   │ │
│ │ Valve Opening: [━━━━━●━━] 100%     │ │
│ └────────────────────────────────────┘ │
└────────────────────────────────────────┘
```

### 5.3 Rack Property Panel Extension

Each equipment (fanServer/nonFan) gets a collapsible "Liquid Cooling" section in the right-side inspector:

```
┌─ Rack_1 / fan_server_1 ───────────────┐
│ Type: fanServer                        │
│ Start U: [29]  Span: [5] U            │
│ Air Flow: [0.05] m³/s  Power: [700] W │
│                                        │
│ ── Liquid Cooling ──                   │
│ ☑ Enable Liquid Cooling                │
│ Cold Plate Spec: [cp_default      ▼]  │
│ Cold Plate Count:[2] (CPU×1 + GPU×1)  │
│ Chip TDP:        [350] W (per plate)  │
│ ┌ Advanced Thermal Params (expand) ──┐ │
│ │ Chip Area:         [0.004] m²      │ │
│ │ Chip Thickness:    [0.001] m       │ │
│ │ Thermal Cond.:     [148] W/(m·K)   │ │
│ └────────────────────────────────────┘ │
└────────────────────────────────────────┘
```

### 5.4 3D Pipe Rendering (liquid_pipe_renderer.js)

```javascript
class LiquidPipeRenderer {
  constructor(scene) { ... }

  // Auto-generate pipe routes from CDU and Rack positions
  generateAutoRoutes(cdus, racks) {
    // For each CDU:
    //   1. Horizontal main pipe along rack row direction from CDU supply port
    //   2. T-branch down to each servedRack side
    //   3. Return pipe parallel to supply (offset)
    //   4. Smooth corners with CatmullRomCurve3
  }

  // Render pipes as TubeGeometry meshes
  renderPipes(routes) {
    // Supply: blue (0x2196F3), Return: red (0xF44336)
    // MeshStandardMaterial, metalness: 0.3, roughness: 0.6
    // Tube radius: 0.015m (visual, not physical diameter)
  }

  // Post-processing: color by temperature
  colorByTemperature(results) {
    // Supply: blue→cyan (30-40 degC)
    // Return: orange→red (40-55 degC)
  }

  // Update when CDU or Rack position changes
  update() { ... }

  // Clear all pipe meshes
  clearPipes() { ... }
}
```

**Auto path generation algorithm**:
```
CDU supply port → horizontal right to first Rack → T-branch down to Rack inlet
                                                 → main pipe continues to next Rack → T-branch...
                                                                                     → end cap
Each Rack internally (schematic lines only, not physical pipes):
  Rack inlet → Manifold → parallel Server cold plates → Manifold → Rack outlet

Return pipe: mirror supply pipe, Z-offset 0.05m
```

Pipes are **visual only** — they do not participate in STL export or CFD mesh generation (the 1D network solver handles hydraulics).

### 5.5 Equipment Level View (liquid_topology_editor.js)

A 2D Canvas-based editor that **replaces the central 3D viewport** when activated:

```
┌─ Equipment Level View ──────────────────────────────────────────┐
│ [<- Back to 3D]   Rack: [Rack_1 | Rack_2 | Rack_3]             │
│ ┌─ Toolbar ─────────────────────────────────────────────────┐   │
│ │ [Connect Mode] [Disconnect] [Undo] [Redo]                │   │
│ │ Presets: [Series] [Parallel] [Custom]                     │   │
│ └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Supply Manifold (blue) ═══╤══════╤══════╤═══                   │
│                             │      │      │                      │
│  ┌─ Server (U42-38) ──────────────────────────┐                 │
│  │  [CPU ColdPlate] →→ [GPU ColdPlate]        │  (series)       │
│  └────────────────────────────────────────────┘                 │
│  ┌─ Server (U37-33) ──────────────────────────┐                 │
│  │  [CPU ColdPlate]    [GPU ColdPlate]        │  (parallel)     │
│  └────────────────────────────────────────────┘                 │
│  ┌─ Server (U32-28) ──────────────────────────┐                 │
│  │  (no liquid cooling)                        │                 │
│  └────────────────────────────────────────────┘                 │
│                             │      │      │                      │
│  Return Manifold (red) ════╧══════╧══════╧═══                   │
│                                                                  │
│ ┌─ Selected Component Properties ──────────────────────────┐    │
│ │ CPU ColdPlate | Spec: [cp_default] | TDP: [350]W         │    │
│ └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

**Interaction: Port-based drag-and-connect**

Each cold plate has two ports:
- **Inlet** (left side, blue circle): water enters
- **Outlet** (right side, red circle): water exits

Manifold has:
- **Supply out** (downward blue circles): one per server branch
- **Return in** (upward red circles): one per server branch

**Drag-connect workflow**:
1. User clicks and holds on an outlet port (red circle)
2. Drags — a bezier curve follows the mouse
3. Nearby inlet ports (blue) highlight with snap attraction
4. Releases on an inlet port → connection created
5. Connection validates: no self-loops, no cycles, single-input per port

**Preset buttons**:
- **Series**: manifold_in → cp[0].in → cp[0].out → cp[1].in → cp[1].out → manifold_out
- **Parallel**: manifold_in → split → [cp[0], cp[1] independently] → merge → manifold_out
- **Custom**: clears connections, user draws freely

**Visual style**:
- Dark gray grid background (matches 3D viewport theme)
- Servers: rounded rectangles, height proportional to U-slots
- Cold plates: rectangular icons with gradient fill + label
- Connections: 2px solid bezier curves, blue (supply side) / red (return side)
- Ports: 8px circles, 12px on hover, hollow when unconnected, filled when connected

**Rendering**: Canvas 2D (not Three.js). Independent of 3D viewport.

### 5.6 Post-Processing Integration (liquid_results_panel.js)

Added as a sub-panel within the existing post-processing tab:

```
┌─ Post-processing ─────────────────────────────────────────────────────┐
│ [ParaView Temp Cloud] [Streamlines] [Slice] │ [Liquid Cooling]       │
│                                                                       │
│ ┌─ Liquid Cooling System Overview ───────────────────────────────────┐│
│ │ CDU_1: Supply 35.0°C → Return 41.2°C │ Flow: 60 LPM │ Pump: 850W ││
│ │ Total Liquid Heat Removal: 18,425 W   │ Avg η: 0.84               ││
│ └────────────────────────────────────────────────────────────────────┘│
│                                                                       │
│ ┌─ Server Liquid Cooling Details (click row → highlight 3D) ────────┐│
│ │ Server ID          │Flow  │T_in │T_out│T_chip│  η   │Residual    ││
│ │                    │(LPM) │(°C) │(°C) │(°C)  │      │(W)         ││
│ │────────────────────│──────│─────│─────│──────│──────│────────────││
│ │ Rack_1_server_01   │ 2.5  │35.0 │42.3 │ 68.5 │ 0.85 │  105  ✅   ││
│ │ Rack_1_server_02   │ 2.3  │35.0 │43.1 │ 71.2 │ 0.83 │  119  ✅   ││
│ │ Rack_1_server_03   │ 2.4  │35.0 │42.8 │ 69.8 │ 0.84 │  112  ✅   ││
│ │ Rack_2_server_01   │ 2.2  │35.0 │44.5 │ 78.3 │ 0.80 │  140  ⚠️   ││
│ │ Rack_2_server_02   │ 2.1  │35.0 │45.2 │ 82.1 │ 0.78 │  154  ⚠️   ││
│ │ Rack_2_server_03   │ 1.8  │35.0 │47.8 │ 91.5 │ 0.72 │  196  🔴   ││
│ └────────────────────────────────────────────────────────────────────┘│
│ Legend: ✅ T_chip < 75°C  ⚠️ 75-85°C  🔴 > 85°C (throttle risk)     │
│                                                                       │
│ ┌─ 3D Display Controls ─────────────────────────────────────────────┐│
│ │ ☑ Color pipes by water temperature    (blue 30°C → red 55°C)     ││
│ │ ☑ Color servers by chip temperature   (green 50°C → red 95°C)    ││
│ │ ☐ Show flow rate labels on pipes                                  ││
│ │ ☐ Show pressure labels at nodes                                   ││
│ └────────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────────────┘
```

**Click interactions (bidirectional)**:

1. **Click table row → 3D highlight**:
   - Corresponding server mesh highlighted (orange outline)
   - Connected pipe segments highlighted (thicker + glow)
   - Camera smoothly orbits to focus on the server
   - Detail popup appears next to server in 3D viewport

2. **Click server in 3D viewport → table scroll + popup**:
   - Table auto-scrolls to corresponding row, row highlighted
   - Floating detail card appears anchored to clicked server:

```
┌─ Rack_1_server_01 Liquid Cooling ───────┐
│                                          │
│ Chip Max Temperature:  68.5°C  ✅ Normal │
│                                          │
│ ── Cold Plate ──                         │
│ Inlet Water:     35.0°C                  │
│ Outlet Water:    42.3°C                  │
│ Flow Rate:       2.5 LPM                 │
│ Pressure Drop:   12.3 kPa                │
│                                          │
│ ── Heat Distribution ──                  │
│ Server Total:    700 W                   │
│ Liquid Removed:  595 W  (85%)  ████████░ │
│ Air Residual:    105 W  (15%)  █░░░░░░░░ │
│                                          │
│ ── Cold Plates (2) ──                    │
│ CPU (cp_cpu):  350W TDP → 42.3°C out     │
│ GPU (cp_gpu):  350W TDP → 48.1°C out     │
│                                          │
│                              [Close ×]   │
└──────────────────────────────────────────┘
```

### 5.7 Left Panel Integration

No new tabs. Liquid cooling controls added to existing "Solver" tab (left panel):

```
┌─ Solver Tab ──────────────────────────────┐
│                                            │
│ ── Air CFD ──                              │
│ Solver:     [buoyantSimpleFoam ▼]         │
│ Iterations: [2000]                         │
│ Parallel:   [4] cores                      │
│ ...existing params...                      │
│                                            │
│ ── Liquid Cooling ──────────────────────── │
│ (visible only when liquidCooling.enabled)  │
│                                            │
│ Coolant:    [Water 40°C         ▼]        │
│             [Water 20°C]                   │
│             [Glycol 25%]                   │
│             [Glycol 50%]                   │
│             [Custom...]                    │
│                                            │
│ ┌────────────────────────────────────────┐ │
│ │  ▶ Run Liquid Solve                    │ │
│ │    (standalone, sub-second response)   │ │
│ └────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────┐ │
│ │  ▶ Run Coupled Simulation              │ │
│ │    (liquid solve + CFD, minutes)       │ │
│ └────────────────────────────────────────┘ │
│ Status: ✅ Liquid solved                   │
│         ⏳ CFD running... (iter 342/2000)  │
│                                            │
│ ── Pipe Network ──                         │
│ Mode: (◉) Auto  ( ) Manual                │
│ ┌────────────────────────────────────────┐ │
│ │  🔧 Edit Pipe Network                  │ │
│ │  (enabled in Manual mode only)         │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ ── Equipment Config ──                     │
│ ┌────────────────────────────────────────┐ │
│ │  📐 Equipment Level View               │ │
│ │  (opens 2D view replacing 3D viewport) │ │
│ └────────────────────────────────────────┘ │
│                                            │
│ ── Specifications ──                       │
│ ┌────────────────────────────────────────┐ │
│ │  📋 Manage Component Specs...           │ │
│ │  (modal: pump/pipe/valve/coldplate)    │ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

### 5.8 Specification Manager Modal

Clicking "Manage Component Specs" opens a modal dialog for CRUD operations on component specifications:

```
┌─ Component Specifications Manager ─────────────────────────────┐
│                                                                 │
│ Tab: [Pumps] [Pipes] [Valves] [Cold Plates] [Heat Exchangers]  │
│                                                                 │
│ ┌─ Cold Plates ────────────────────────────────────────────┐   │
│ │ Name           │ Coef A │ Coef B │ Coef C │              │   │
│ │ cp_default     │ 0.724  │ 0.991  │ -0.155 │ [Edit] [Del] │   │
│ │ cp_gpu_high    │ 1.200  │ 1.500  │ -0.200 │ [Edit] [Del] │   │
│ │ cp_low_power   │ 0.400  │ 0.600  │ -0.100 │ [Edit] [Del] │   │
│ └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│ [+ Add New Cold Plate Spec]                                     │
│                                                                 │
│ ┌─ Preset Templates ──────────────────────────────────────┐    │
│ │ [Import Vertiv XDU]  [Import CoolIT]  [Import Asetek]   │    │
│ └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│                                    [Cancel]  [Save & Close]     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. RDHx Rear Door Heat Exchanger (Phase 8)

### Physics Model

```
Q_rdhx = epsilon * C_min * (T_air_hot - T_water_in)

Where:
  epsilon = heat exchanger effectiveness (0.3-0.7)
  C_min = min(m_air * Cp_air, m_water * Cp_water)
  T_air_hot = server exhaust air temperature
  T_water_in = RDHx water inlet temperature

Effect on CFD: reduces fan_outlet temperature
  T_outlet_with_rdhx = T_air_hot - Q_rdhx / (m_air * Cp_air)
```

### Implementation

- `src/backend/liquidcooling/devices/rdhx.py`: epsilon-NTU model
- `bridge_bc.py`: adjust fan_outlet temperature for RDHx-equipped racks
- 3D: semi-transparent water-cooling panel on rack back face
- RDHx can coexist with D2C (serial effect: D2C reduces chip heat, RDHx intercepts exhaust)

**Rack property panel — RDHx collapsible section**:

```
┌─ Rack_1 Properties ───────────────────────┐
│ ...existing rack properties...             │
│                                            │
│ ── Rear Door Heat Exchanger (RDHx) ──      │
│ ☐ Enable RDHx                              │
│ ┌─ (expanded when checked) ──────────────┐ │
│ │ Heat Rejection:  [30000] W             │ │
│ │ Water Flow Rate: [20   ] LPM           │ │
│ │ Water Inlet T:   [25   ] °C            │ │
│ │ Effectiveness ε: [0.50 ]               │ │
│ │ Coil Spec:       [rdhx_default    ▼]   │ │
│ │                                        │ │
│ │ CDU Connection:  [CDU_1           ▼]   │ │
│ │ (which CDU supplies water to RDHx)     │ │
│ └────────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

---

## 7. Implementation Phases

### Phase 1: Liquid Solver Core (backend)

Rewrite hydraulic components and solver from scratch in `src/backend/liquidcooling/`.

```
src/backend/liquidcooling/components/     All 6 components
src/backend/liquidcooling/devices/cdu.py  CDU black-box + component-level dual mode
src/backend/liquidcooling/solver/steady.py  LM steady-state solver
src/backend/liquidcooling/thermal/        Chip temp + eta load distribution
src/backend/liquidcooling/topology.py     JSON → graph auto topology
src/backend/liquidcooling/factory.py      Spec → component instance factory
src/backend/liquidcooling/results.py      Result data structures
cfd_export.json schema extension   Pydantic validation
Unit tests                         Per component + solver + topology
```

**Acceptance**: Test with parameters from liquid_dc_01.json and liquid_dc_two_racks.json. Solver output matches DiffLiquidTwin reference (pressure error <1%, flow error <1%).

### Phase 2: Liquid-Air Coupling Pipeline (backend)

```
src/backend/liquidcooling/liquid_runner.py    Liquid solve entry point
src/backend/aircooling/runner/stlgen_runner.py    Modified: liquid preprocessing step
src/backend/aircooling/runner/bridge_bc.py        New: liquid_cooled_* BC types
src/backend/aircooling/runner/fields.py           liquid-cooled server BC generation
server.py                              3 new API endpoints
```

**Acceptance**: 2-rack hybrid scenario (1 rack D2C + 1 rack air-only), coupled sim runs end-to-end, liquid-cooled rack fan_outlet temperature significantly lower.

### Phase 3: Frontend Basics — CDU Model + Rack Liquid Properties

```
js/liquid_cooling.js         Global state, enable/disable, export
js/liquid_cdu_model.js       CDU model creation, property panel, 3D appearance
STL_generate.html            Model tree CDU button, Rack liquid section, Solver tab liquid section
```

**Acceptance**: User creates CDU, configures parameters, enables server liquid cooling, exported cfd_export.json consumed correctly by Phase 1 solver.

### Phase 4: 3D Pipe Rendering

```
js/liquid_pipe_renderer.js    Auto path + TubeGeometry rendering
```

**Acceptance**: CDU placed + served racks selected → blue/red pipes appear in 3D, update on drag.

### Phase 5: Equipment Level View — 2D Cold Plate Wiring

```
js/liquid_topology_editor.js    Canvas 2D EquipmentLevelView class
```

**Acceptance**: User adds cold plates to servers, drag-connects ports or uses series/parallel presets, config persists when returning to 3D.

### Phase 6: Advanced Manual Pipe Editing

```
js/liquid_topology_editor.js    Extended: 3D pipe edit mode, waypoint drag, component insertion
```

**Acceptance**: User switches to manual mode, adjusts pipe paths, inserts valve, solver uses manual topology.

### Phase 7: Post-Processing Liquid Results

```
js/liquid_results_panel.js    Results table, 3D temperature coloring, click interaction
```

**Acceptance**: After coupled sim, post-processing tab shows air CFD cloud + liquid results table, pipes colored by temperature, server click shows liquid detail popup.

### Phase 8: RDHx Rear Door Heat Exchanger

```
src/backend/liquidcooling/devices/rdhx.py    RDHx thermal model
Rack property panel RDHx section
bridge_bc.py modification
3D: semi-transparent back panel
```

### Phase Dependencies

```
Phase 1 (solver) ──> Phase 2 (coupling) ──> Phase 7 (results display)
                                              ^
Phase 3 (CDU+props) ──> Phase 4 (3D pipes) ──┘
         |
         └──> Phase 5 (Equipment View) ──> Phase 6 (manual editing)

Phase 8 (RDHx): independent, can start after Phase 2
```

---

## 8. References

### Open-Source Projects Analyzed
- **ExaDigiT** (ORNL): Liquid-cooled supercomputer digital twin. Repos cloned to `Ref_code/ExaDigiT/` (RAPS, SimulationServer, SimulationDashboard, Documentation). Uses FMU-wrapped Modelica thermo-fluid models, FastAPI backend, React+Plotly dashboard. 2D-only visualization.
- **DiffLiquidTwin** (at `Ref_code/DiffLiquidTwin/`): PyTorch differentiable hydraulic network solver. Reference for component physics equations and solver algorithms. Not imported directly.
- **dctwin/dclib** (at `Ref_code/extracted/`): Differentiable thermal models for hybrid air+liquid cooling. Reference for D2CServerModel, HybridCoolingLoadDistributionModel, HeatExchangerModel.

### Key Academic References
- Yu, Ravi & Keefe (2025): "Validation of 1-D Thermo-Fluid Analysis Tool for Liquid Cooling Systems in Data Center Product" — confirms 1D network + 3D air CFD coupling accuracy
- Brewer et al. (SC'24): "A Digital Twin Framework for Liquid-cooled Supercomputers as Demonstrated at Exascale" — ExaDigiT architecture, arXiv:2410.05133
- Cho (2024): "Numerical analysis of rack-based data center cooling with rear door heat exchanger" — RDHx modeling approach
- Shalom Simon et al. (ASME InterPACK): D2C server thermal resistance network — referenced in dctwin's HybridCoolingLoadDistributionModel

### Industry Standard: 1D Liquid + 3D Air Coupling
The one-way coupling approach (1D liquid network solver → residual heat sources → 3D air CFD) is the industry standard used by Cadence 6SigmaDCX, Siemens Simcenter (Flomaster + FloEFD), and ExaDigiT. Fully meshing liquid channels in 3D CFD is only done for component-level cold plate design, not room-scale simulation.
