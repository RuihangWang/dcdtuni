# Liquid Cooling ODIL Solver — Design Spec

**Date**: 2026-05-05
**Status**: Approved (brainstorming complete)
**Scope**: Complete rewrite of `src/backend/liquidcooling/`, new frontend `liquid_graphgen.js`, server API integration

---

## 1. Overview

Design a non-causal ODIL (Optimizing a Discrete Loss) liquid cooling solver for the dcdtuni data center digital twin. The solver treats the entire spacetime domain as unknowns and optimizes all physical variables simultaneously using Adam, rather than stepping forward in time.

### Key Decisions

| Decision | Choice |
|----------|--------|
| Relationship to existing liquid module | **Complete rewrite** of `src/backend/liquidcooling/` |
| Solving method | **ODIL non-causal** — all spacetime variables optimized simultaneously |
| Computation graph architecture | **Type-batched nn.ModuleDict** with compiled index tensors |
| Time discretization | **BDF1/BDF2/BDF3** — ODE residuals become algebraic constraints |
| V1 component scope | **Full single-cabinet loop** (CDU, Pump, Pipe, Valve, T-junction, CP-CU, Rack) |
| .liquidrun generation | **Frontend compilation** (JS compiles 3D → abstract graph JSON) in `liquid_graphgen.js` |
| Optimization strategy | **Single loss + L2 norm + manual weights + single Adam optimizer** |
| Compilation pipeline | **Two-stage**: .liquidrun JSON → CompiledGraph dataclass → PyTorch tensors |
| Reference repo | DiffLiquidTwin (component physics + module patterns, but NOT its forward solver) |
| File organization | `case/Liquid/model/*.liquidrun`, `case/Liquid/log/<timestamp>/` |
| Pipeline independence | Liquid pipeline is **fully independent** from air cooling (.cfdrun) pipeline |

### ODIL vs Forward Simulation

**DiffLiquidTwin** (forward/causal): quasi-steady hydraulic LM solve per timestep → torchdiffeq ODE advance → repeat. Sequential in time.

**This design** (ODIL/non-causal): All variables P[N_nodes, N_t], m[N_edges, N_t], T_w[N_cells, N_t], T_s[N_cp, N_t] are `nn.Parameter`. Loss = sum of squared physics residuals across all spacetime points. Adam minimizes this loss. No time-stepping loop.

ODE terms (e.g., `C_s · dT_s/dt = Q - UA·(T_s - T_w)`) are discretized via BDF into algebraic residuals (e.g., `C_s · (T_s[t] - T_s[t-1])/dt - Q[t] + UA·(T_s[t] - T_w[t]) = 0`), which are just tensor operations on the full `[N_cp, N_t]` array.

---

## 2. .liquidrun JSON Schema

The `.liquidrun` file is the core interface between frontend and backend. The frontend JS compiles the 3D scene into this abstract graph structure. All units are SI (meters, Kelvin, Pascals, kg/s). The frontend performs mm→m and °C→K conversion on export.

```json
{
  "version": "1.0",
  "source": "STLGeneratorForOpenFoam",
  "projectName": "MyLiquidProject",

  "gravity": [0, -9.81, 0],

  "fluid": {
    "rho_kg_m3": 997.0,
    "cp_J_kgK": 4182.0,
    "mu_Pa_s": 0.00089,
    "k_W_mK": 0.606
  },

  "time": {
    "dt_s": 1.0,
    "N_t": 300,
    "bdf_order": 1
  },

  "racks": [
    {
      "id": "rack1",
      "position_m": [2.0, 0.0, 0.0],
      "n_slots": 42,
      "manifold": {
        "supply_node": "rack1_mf_supply",
        "return_node": "rack1_mf_return",
        "layout": "both-left",
        "D_m": 0.025
      },
      "servers": [
        {
          "id": "rack1_srv1",
          "slot_range": [1, 2],
          "z_m": 0.044,
          "cp_cu_pairs": [
            {
              "id": "rack1_srv1_cp1",
              "edge_id": "rack1_srv1_cp1_edge",
              "Q_W": 350.0
            }
          ],
          "inlet_node": "rack1_srv1_in",
          "outlet_node": "rack1_srv1_out"
        }
      ]
    }
  ],

  "nodes": [
    {
      "id": "cdu1_supply",
      "type": "pressure_bc",
      "position_m": [1.0, 0.3, 0.5],
      "bc": { "P_Pa": 200000, "T_K": 308.15 }
    },
    {
      "id": "cdu1_return",
      "type": "pressure_bc",
      "position_m": [1.0, 0.3, 0.6],
      "bc": { "P_Pa": 100000 }
    },
    {
      "id": "rack1_mf_supply",
      "type": "junction",
      "position_m": [2.0, 1.8, 0.5]
    },
    {
      "id": "rack1_srv1_in",
      "type": "junction",
      "position_m": [2.0, 0.2, 0.5]
    },
    {
      "id": "rack1_srv1_out",
      "type": "junction",
      "position_m": [2.05, 0.2, 0.5]
    },
    {
      "id": "rack1_mf_return",
      "type": "junction",
      "position_m": [2.05, 1.8, 0.5]
    }
  ],

  "edges": [
    {
      "id": "pipe_main_supply",
      "type": "pipe",
      "from": "cdu1_supply",
      "to": "rack1_mf_supply",
      "params": {
        "D_m": 0.025,
        "roughness_m": 1.5e-6,
        "n_cells": 10,
        "waypoints_m": [
          [1.0, 0.3, 0.5],
          [1.0, 0.3, 1.5],
          [1.0, 1.2, 1.5],
          [2.0, 1.2, 1.5],
          [2.0, 1.8, 1.5]
        ],
        "K_minor_per_bend": 0.5
      }
    },
    {
      "id": "rack1_srv1_cp1_edge",
      "type": "cold_plate",
      "from": "rack1_srv1_in",
      "to": "rack1_srv1_out",
      "params": {
        "dp_a": 1e8,
        "dp_b": 1e5,
        "UA_W_per_K": 150.0,
        "C_solid_J_per_K": 50.0,
        "V_fluid_m3": 5e-6,
        "Q_W": 350.0
      }
    },
    {
      "id": "rack1_valve",
      "type": "valve",
      "from": "rack1_mf_supply",
      "to": "rack1_srv1_in",
      "params": {
        "D_m": 0.015,
        "Kv_max": 2.5,
        "characteristic": "equal_percentage",
        "opening": 0.8
      }
    },
    {
      "id": "cdu1_pump",
      "type": "pump",
      "from": "cdu1_return",
      "to": "cdu1_supply",
      "params": {
        "dp_Pa": 100000,
        "H0_m": 15.0,
        "a_coef": 1e6,
        "rpm_ratio": 1.0
      }
    }
  ],

  "events": [
    {
      "time_s": 60,
      "target": "rack1_srv1_cp1_edge",
      "field": "Q_W",
      "value": 500.0
    },
    {
      "time_s": 120,
      "target": "rack1_valve",
      "field": "opening",
      "value": 0.5
    }
  ],

  "initial_conditions": {
    "T_fluid_K": 308.15,
    "T_solid_K": 340.0,
    "P_uniform_Pa": 150000
  },

  "solver": {
    "optimizer": "adam",
    "lr": 1e-3,
    "max_epochs": 5000,
    "convergence_tol": 1e-6,
    "weights": {
      "mass": 1.0,
      "momentum": 1e-5,
      "energy": 1.0,
      "bc": 100.0,
      "ic": 10.0
    }
  }
}
```

### Schema Design Notes

- `nodes` have two types: `pressure_bc` (fixed boundary) and `junction` (free variable)
- `edges` correspond to physical components: `pipe`, `cold_plate`, `valve`, `pump`
- `racks` is a **container/metadata** section — no physics equations, used for result aggregation. The rack's physical structure (manifold pipes, server branch pipes, cold plates) is represented as individual nodes and edges.
- Each node has `position_m: [x, y, z]` in the frontend scene coordinate system (Y-up, gravity = -Y)
- `gravity: [0, -9.81, 0]` vector determines the hydrostatic pressure direction. The compiler derives elevation differences from node positions and this vector.
- Pipe edges use `waypoints_m` (actual 3D path from the frontend) instead of a simplified `L_m`. The compiler derives total arc length, per-segment elevation changes, bend count, and minor loss coefficients from the waypoints.
- `events` support time-varying parameters (power step changes, valve opening changes). The compiler expands these into `[N_t]` tensors.
- `Q_W` can be a scalar (constant) or an array of length N_t (pre-expanded time-varying).

### Pipe Waypoints → Physical Quantities (Compiler)

The compiler derives all pipe physical quantities from the waypoint polyline:

| Derived Quantity | Physics Role | Computation |
|-----------------|--------------|-------------|
| `L_total` | Friction loss `f·L/D` | Sum of segment vector lengths |
| `dh_total` | Hydrostatic `ρg·Δh` | Sum of segment projections onto anti-gravity direction |
| `n_bends` + `K_minor` | Local losses `K·m²/(2ρA²)` | Count direction changes > 18° threshold |
| `cell_ds[i]` | Per-cell arc length | Uniform subdivision along polyline |
| `cell_volume[i]` | Per-cell volume `π/4·D²·ds` | From cell_ds |
| `cell_dh[i]` | Per-cell elevation change | Interpolate along polyline |
| `cell_position[i]` | Visualization | Interpolate 3D position along polyline |

### Gravity Handling

The frontend scene uses Y-up coordinate system with gravity in -Y direction. The `gravity` vector in `.liquidrun` encodes both direction and magnitude. The compiler computes elevation differences as:

```python
g_vec = np.array(gravity)                    # [0, -9.81, 0]
g_unit = g_vec / np.linalg.norm(g_vec)       # [0, -1, 0]
g_mag = np.linalg.norm(g_vec)                # 9.81

# For each edge:
delta = pos_to - pos_from
dh = -np.dot(delta, g_unit)  # positive = upward (against gravity)

# In momentum residual:
# r = (P_from - P_to) - dp_friction(m) + rho * g_mag * dh
```

### Rack Manifold Gravity Effects

A standard 42U rack is ~1.8m tall. The hydrostatic pressure difference: `ρgh = 997 × 9.81 × 1.8 ≈ 17,600 Pa ≈ 17.6 kPa`, which is ~18% of a typical 100 kPa pump head. This is NOT negligible and directly affects flow distribution across servers at different U-slot heights. The design captures this through per-branch-pipe elevation differences computed from server slot positions.

---

## 3. Frontend: `liquid_graphgen.js`

**File path**: `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js`

**Responsibility**: Compile the 3D liquid cooling scene into the `.liquidrun` abstract graph JSON.

### Entry Point

```javascript
/**
 * Main entry: traverse scene models, compile to .liquidrun graph structure.
 * Called by liquid_cooling.js export flow.
 * @param {Array} models - state.models
 * @param {Object} liquidState - state.liquidCooling (global liquid cooling config)
 * @returns {Object} .liquidrun JSON
 */
window.buildLiquidrunJson = function(models, liquidState) { ... }
```

### Internal Module Structure

```
liquid_graphgen.js
│
├── buildLiquidrunJson(models, liquidState)    // Main entry
│   ├── _buildFluidConfig(liquidState)         // fluid section
│   ├── _buildTimeConfig(liquidState)          // time section
│   ├── _buildGravity()                        // gravity vector [0, -9.81, 0]
│   │
│   ├── _collectCDUs(models)                   // CDU → pressure_bc nodes + pump edge
│   ├── _expandRacks(models)                   // Rack → manifold nodes + branch edges + racks[]
│   ├── _collectPipes(models)                  // Pipe → pipe edges + waypoints
│   ├── _collectValves(models)                 // Valve → valve edges
│   ├── _collectTJunctions(models)             // T-junction → junction nodes
│   │
│   ├── _buildEvents(models, liquidState)      // Time-varying params → events[]
│   ├── _buildInitialConditions(liquidState)   // IC section
│   └── _buildSolverConfig(liquidState)        // solver section
│
├── _worldToMeters(vec3)                       // mm → m coordinate conversion
├── _celsiusToKelvin(T_C)                      // °C → K
├── _computeWaypoints(pipeModel)               // Extract pipe 3D path points (meters)
└── _validateGraph(graph)                      // Topology validation (connectivity, ref pressure node)
```

### Model Type Compilation Logic

**CDU** → 2 boundary nodes + 1 pump edge:
- `{cdu.name}_supply` node: `type: "pressure_bc"`, with P and T boundary
- `{cdu.name}_return` node: `type: "pressure_bc"`, with P boundary
- `{cdu.name}_pump` edge: `type: "pump"`, from return to supply

**Rack** → manifold nodes + branch pipes + cold plate edges + rack metadata:
- Manifold supply/return junction nodes at rack top
- Per-server: inlet/outlet junction nodes at server U-slot height
- Per-server: branch pipe edges from manifold to server (supply + return)
- Per-CP-CU-pair: cold_plate edge between server inlet/outlet
- Rack metadata in `racks[]` section for result aggregation
- Branch pipe `waypoints_m` computed from manifold position to server slot position
- U-slot height: `server.startSlot * 0.04445` meters

**Pipe** → pipe edge with real waypoints:
- `waypoints_m` from the pipe model's `waypoints[]` array, converted to meters
- `D_m` from pipe diameter property
- `K_minor_per_bend` default 0.5

**Valve** → valve edge (pure algebraic, no waypoints)

**T-junction** → junction node (mass conservation + temperature mixing)

### Topology Validation

Before export, `_validateGraph()` checks:
1. All edge `from`/`to` reference existing node IDs
2. At least one `pressure_bc` node exists (prevents singular Jacobian)
3. All junction nodes are connected to at least one pressure_bc via BFS (connectivity)
4. No duplicate node/edge IDs

### Integration Point

In liquid cooling mode, the export button triggers:
1. `buildLiquidrunJson()` → `.liquidrun` JSON object
2. POST to `/api/liquid/export-liquidrun` → saves to `case/Liquid/model/<projectName>.liquidrun`
3. User clicks "Solve" → POST to `/api/liquid/solve` → SSE log stream → results panel

The `.liquidrun` is **not** embedded in `.cfdrun`. The two pipelines are fully independent.

---

## 4. Backend Module Structure

Complete rewrite of `src/backend/liquidcooling/`. Directory layout:

```
src/backend/liquidcooling/
│
├── __init__.py                     # Public API: compile_graph, solve, LiquidResult
│
├── schema.py                      # Pydantic v2 — validate .liquidrun JSON
│
├── compiler.py                    # .liquidrun JSON → CompiledGraph
│
├── compiled_graph.py              # CompiledGraph dataclass (intermediate representation)
│
├── handlers/                      # Per-type batch residual handlers
│   ├── __init__.py
│   ├── base.py                    #   BaseHandler ABC
│   ├── pipe.py                    #   PipeBatchHandler (Darcy-Weisbach + 1D thermal transport)
│   ├── cold_plate.py              #   ColdPlateBatchHandler (quadratic dP + chip thermal ODE)
│   ├── valve.py                   #   ValveBatchHandler (Kv characteristic curve)
│   ├── pump.py                    #   PumpBatchHandler (affinity law)
│   └── junction.py                #   JunctionHandler (mass conservation + temperature mixing)
│
├── model.py                       # LiquidODILModel(nn.Module) — top-level model
│
├── solver.py                      # Adam optimization loop + convergence monitoring
│
├── bdf.py                         # BDF1/BDF2/BDF3 time discretization utilities
│
├── results.py                     # LiquidResult dataclass + JSON serialization
│
├── runner.py                      # Orchestrator: read liquidrun → compile → solve → save
│
└── cli.py                         # CLI entry: python -m dcdtuni.liquid.cli --from-liquidrun <path>
```

### `schema.py` — Pure Validation

```python
class LiquidrunConfig(CFDBaseModel):
    version: str
    projectName: str
    gravity: list[float]              # [gx, gy, gz]
    fluid: FluidConfig
    time: TimeConfig
    nodes: list[NodeConfig]
    edges: list[EdgeConfig]
    racks: list[RackConfig]
    events: list[EventConfig] = []
    initial_conditions: ICConfig
    solver: SolverConfig

class NodeConfig(CFDBaseModel):
    id: str
    type: Literal["pressure_bc", "junction"]
    position_m: list[float]           # [x, y, z]
    bc: dict | None = None            # only for pressure_bc

class EdgeConfig(CFDBaseModel):
    id: str
    type: Literal["pipe", "cold_plate", "valve", "pump"]
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    params: dict                      # type-specific, compiler parses
```

### `compiled_graph.py` — Intermediate Representation

```python
@dataclass
class CompiledGraph:
    # Topology
    N_nodes: int
    N_edges: int
    N_cells: int                      # total pipe thermal cells
    N_cp: int                         # total cold plates
    N_t: int                          # time steps

    # Nodes
    node_ids: list[str]
    node_position: Tensor             # [N_nodes, 3]
    bc_mask: Tensor                   # [N_nodes] bool
    bc_P: Tensor                      # [N_nodes] — fixed pressure at BC nodes
    bc_T: Tensor                      # [N_nodes] — fixed temperature (NaN = unconstrained)
    junction_mask: Tensor             # [N_nodes] bool — ~bc_mask

    # Edges (all types unified numbering)
    edge_from: Tensor                 # [N_edges] long
    edge_to: Tensor                   # [N_edges] long
    edge_type: Tensor                 # [N_edges] long (0=pipe, 1=cp, 2=valve, 3=pump)

    # Sparse incidence matrix
    incidence: Tensor                 # [N_nodes, N_edges] sparse

    # Per-type indices (for gather/scatter)
    pipe_indices: PipeIndices
    cp_indices: ColdPlateIndices
    valve_indices: ValveIndices
    pump_indices: PumpIndices

    # Per-type batched physical parameter tensors
    pipe_params: PipeBatchParams
    cp_params: ColdPlateBatchParams
    valve_params: ValveBatchParams
    pump_params: PumpBatchParams

    # Thermal cells
    cell_edge_id: Tensor              # [N_cells] — which pipe each cell belongs to
    cell_ds: Tensor                   # [N_cells] — arc length
    cell_volume: Tensor               # [N_cells] — volume
    cell_dh: Tensor                   # [N_cells] — elevation change
    cell_position: Tensor             # [N_cells, 3] — for visualization

    # Junction mixing (precompiled)
    node_in_edges: list[list[int]]    # per junction: list of incoming edge indices
    node_out_edges: list[list[int]]   # per junction: list of outgoing edge indices

    # Time
    dt: float
    bdf_order: int

    # Initial conditions
    T_fluid_init: float
    T_solid_init: float
    P_init: float

    # Solver config
    solver_config: SolverConfig

    # Metadata (result aggregation)
    rack_meta: list[RackMeta]

    # Fluid properties
    fluid: FluidConfig

    # Gravity
    g_mag: float

    def to_json(self, path: Path): ...    # serializable for debugging
```

### `compiler.py` — Compilation Pipeline

```python
def compile_graph(config: LiquidrunConfig, device: str = 'cpu') -> CompiledGraph:
    """
    .liquidrun JSON (Pydantic-validated) → CompiledGraph

    Steps:
    1. Build node_id → index mapping
    2. Group edges by type, compile parameters per type
    3. For pipes: compute L, dh, bends, K_minor, cells from waypoints
    4. For cold_plates: expand Q_W time-varying events into [N_cp, N_t] tensors
    5. Build sparse incidence matrix
    6. Precompute junction in/out edge lists
    7. Topology validation (connectivity, BC node existence)
    8. Pack into CompiledGraph
    """
```

---

## 5. Component Handlers — Physics Equations and Residuals

Each handler inherits `BaseHandler(nn.Module)` and computes batch residuals for all instances of its component type.

### Handler Base Class

```python
class BaseHandler(nn.Module, ABC):
    @abstractmethod
    def compute_residuals(
        self, state: dict[str, Tensor],
        indices: Any, dt: float, bdf_order: int
    ) -> dict[str, Tensor]:
        """
        Returns {'momentum': Tensor, 'energy': Tensor, 'ic': Tensor}
        Each shape: [N_instances, N_t] or [N_instances]
        Not all keys required (e.g., valve has no 'energy')
        """
```

### Self-Testing Convention

Every handler file has a `if __name__ == "__main__"` block that:
1. Constructs a minimal test case with known analytical solution
2. Runs the handler's residual computation
3. Asserts residuals are within tolerance
4. Prints PASS/FAIL

### 5.1 PipeBatchHandler

**Physics**: Darcy-Weisbach friction + hydrostatic head + 1D control volume thermal transport

**Held parameters**:
- `D [N_pipes]` — diameter
- `L [N_pipes]` — total arc length (from waypoints)
- `dh [N_pipes]` — total elevation change
- `K_minor [N_pipes]` — total minor loss coefficient
- `A [N_pipes]` — cross-section area (π/4·D²)
- `roughness [N_pipes]` — surface roughness
- `cell_ds [N_cells]` — per-cell arc length
- `cell_volume [N_cells]` — per-cell volume
- `cell_dh [N_cells]` — per-cell elevation change
- `cell_pipe_id [N_cells]` — which pipe each cell belongs to

**Algebraic residual (momentum)**:
```
r_momentum = (P_from - P_to) - dp_friction - dp_gravity
dp_friction = (f·L/D + K_minor) · m·|m|_ε / (2ρA²)
dp_gravity = ρ · g_mag · dh
```
Friction factor: smooth blend between laminar (f=64/Re) and turbulent (Blasius f=0.3164·Re^-0.25) using sigmoid at Re=2300.

**ODE residual (energy, per cell)**:
```
C_cell · dT_w/dt = ṁ·cp·(T_upstream - T_w)
```
Upwind scheme: T_upstream[i] = T_w[i-1] for i>0, T_upstream[0] = T_node_from (junction mixed temperature).
BDF discretization for dT_w/dt.

**IC residual**: `T_w[:, 0] - T_fluid_init`

### 5.2 ColdPlateBatchHandler

**Physics**: Quadratic pressure drop + dual ODE (water side + solid/chip side)

**Held parameters**:
- `dp_a, dp_b [N_cp]` — pressure drop coefficients: dp = a·m·|m| + b·m
- `UA [N_cp]` — convective heat transfer coefficient (W/K)
- `C_solid [N_cp]` — chip thermal capacity (J/K)
- `V_fluid [N_cp]` — water-side volume (m³)
- `Q_W [N_cp, N_t]` — IT power (may be time-varying, expanded from events)

**Algebraic residual (momentum)**:
```
r_hydraulic = (P_from - P_to) - (dp_a · m · |m|_ε + dp_b · m)
```

**ODE residual (energy, solid side)**:
```
C_s · dT_s/dt = Q_IT(t) - UA · (T_s - T_w_out)
T_w_out = T_w_in + UA · (T_s - T_w_in) / (|m|·cp + ε)
```
BDF discretization for dT_s/dt.

**IC residual**: `T_s[:, 0] - T_solid_init`

### 5.3 ValveBatchHandler

**Physics**: Kv characteristic curve, pure algebraic (no ODE)

**Held parameters**:
- `D [N_valves]` — pipe diameter
- `Kv_max [N_valves]` — fully-open Kv value
- `opening [N_valves, N_t]` — valve position 0~1 (may be time-varying)
- `characteristic [N_valves]` — type: 0=linear, 1=equal_percentage, 2=quick_opening

**Algebraic residual (momentum)**:
```
Kv = Kv_max · f(opening)
  linear:       f(α) = α
  equal_pct:    f(α) = R^(α-1),  R=50
  quick_open:   f(α) = √α
m_model = Kv · sign(dP) · √(|dP| / (ρ/1000))
r_momentum = m - m_model
```
Minimum opening clamped to 1e-3 to prevent graph disconnection.

### 5.4 PumpBatchHandler

**Physics**: Affinity law pump characteristic, pure algebraic (no ODE)

**Held parameters**:
- `H0 [N_pumps]` — rated head (m)
- `a_coef [N_pumps]` — quadratic curve coefficient
- `rpm_ratio [N_pumps, N_t]` — speed ratio (may be time-varying)

**Algebraic residual (momentum)**:
```
Q = m / ρ
H(Q, N) = N² · (H0 - a · Q²)
dp_pump = ρ · g · H
r_momentum = (P_to - P_from) - dp_pump
```

### 5.5 Junction Temperature Mixing

Not a separate handler file — computed in `model.py`.

**Physics**: Enthalpy mixing at junction nodes
```
T_mixed = Σ(ṁ_in · cp · T_out_upstream) / Σ(ṁ_in · cp + ε)
```
Residual: downstream edge inlet temperature must equal T_mixed.
Uses `softplus` for smooth positive-flow extraction (differentiable).

### Component Physical Parameters Summary

| Component | Algebraic Residual | ODE Residual | Key Parameters |
|-----------|-------------------|--------------|----------------|
| **Pipe** | `(P_from-P_to) - dp_fric - ρg·dh = 0` | `C·dT_w/dt - ṁcp(T_up-T_w) = 0` | D, waypoints_m, roughness, K_minor_per_bend, n_cells |
| **ColdPlate** | `(P_from-P_to) - (a·m|m|+b·m) = 0` | `C_s·dT_s/dt - Q_IT + UA(T_s-T_w) = 0` | dp_a, dp_b, UA, C_solid, V_fluid, Q_W(t) |
| **Valve** | `m - Kv(α)·sign(dP)·√(|dP|/ρ') = 0` | None | Kv_max, opening(t), characteristic |
| **Pump** | `(P_to-P_from) - ρgH(Q,N) = 0` | None | H0, a_coef, rpm_ratio(t) |
| **Junction** | `Σ(ṁ_in·T_out) - T_mix·Σ(ṁ_in) = 0` | None | No own parameters |

### BDF Time Discretization Utilities (`bdf.py`)

```python
def bdf_diff(x: Tensor, dt: float, order: int) -> Tensor:
    """
    Compute BDF time derivative of x [..., N_t], returns [..., N_t']

    BDF1: dx/dt ≈ (x[t] - x[t-1]) / dt                              → N_t' = N_t - 1
    BDF2: dx/dt ≈ (3x[t] - 4x[t-1] + x[t-2]) / (2dt)               → N_t' = N_t - 2
          Step 1 fallback to BDF1
    BDF3: dx/dt ≈ (11x[t] - 18x[t-1] + 9x[t-2] - 2x[t-3]) / (6dt) → N_t' = N_t - 3
          Steps 1-2 fallback
    """

def smooth_abs(x: Tensor, eps: float = 1e-6) -> Tensor:
    """Differentiable |x| = sqrt(x² + ε²)"""
```

---

## 6. LiquidODILModel + Solver

### 6.1 Top-Level Model (`model.py`)

```python
class LiquidODILModel(nn.Module):
    def __init__(self, graph: CompiledGraph):
        super().__init__()
        # State variables to optimize
        self.state = nn.ParameterDict({
            'P':   nn.Parameter(torch.full((graph.N_nodes, graph.N_t), graph.P_init)),
            'm':   nn.Parameter(torch.zeros(graph.N_edges, graph.N_t)),
            'T_w': nn.Parameter(torch.full((graph.N_cells, graph.N_t), graph.T_fluid_init)),
            'T_s': nn.Parameter(torch.full((graph.N_cp, graph.N_t), graph.T_solid_init)),
        })

        # Component handlers (only created if that type exists in the graph)
        self.handlers = nn.ModuleDict({...})

        # Precompiled topology tensors as buffers
        self.register_buffer('incidence', graph.incidence)
        self.register_buffer('bc_mask', graph.bc_mask)
        self.register_buffer('bc_P', graph.bc_P)
        self.register_buffer('bc_T', graph.bc_T)

    def forward(self) -> dict[str, Tensor]:
        """
        Compute all residuals, return per-term losses and total.

        Loss terms:
        1. Mass conservation at junction nodes: incidence^T @ m = 0
        2. Per-component residuals (momentum, energy, ic)
        3. Junction temperature mixing
        4. Boundary condition penalty (P and T at BC nodes)
        5. Initial condition penalty (P, m at t=0)

        Returns dict with keys like 'mass', 'pipe_momentum', 'cold_plate_energy',
        'bc_P', 'ic_global', 'total'.
        """
```

### 6.2 Solver (`solver.py`)

```python
def solve(
    model: LiquidODILModel,
    config: SolverConfig,
    log_dir: Path = None,
    callback: Callable = None,
) -> tuple[LiquidResult, list[SolveLog]]:
    """
    Adam optimization loop.

    Features:
    - ReduceLROnPlateau scheduler (patience=200, factor=0.5, min_lr=1e-6)
    - Gradient clipping (max_norm=1.0)
    - Logging every 100 epochs
    - Convergence check: total_loss < convergence_tol
    - Saves: loss_history.csv, model_state.pt, liquid_result.json
    """
```

### 6.3 Results (`results.py`)

```python
@dataclass
class LiquidResult:
    converged: bool
    final_loss: float
    epochs: int
    node_pressure_Pa: np.ndarray      # [N_nodes, N_t]
    node_ids: list[str]
    edge_flow_kg_s: np.ndarray        # [N_edges, N_t]
    edge_ids: list[str]
    cell_temp_K: np.ndarray           # [N_cells, N_t]
    cell_positions: np.ndarray        # [N_cells, 3]
    chip_temp_K: np.ndarray           # [N_cp, N_t]
    water_outlet_temp_K: np.ndarray   # [N_cp, N_t]
    rack_summaries: list[RackSummary]
    time_s: np.ndarray                # [N_t]

    def save_json(self, path: Path): ...

    @staticmethod
    def load_json(path: Path) -> 'LiquidResult': ...

@dataclass
class RackSummary:
    rack_id: str
    total_flow_kg_s: np.ndarray       # [N_t]
    supply_temp_K: np.ndarray         # [N_t]
    return_temp_K: np.ndarray         # [N_t]
    servers: list[ServerSummary]

@dataclass
class ServerSummary:
    server_id: str
    flow_kg_s: np.ndarray             # [N_t]
    max_chip_temp_K: np.ndarray       # [N_t]
    heat_removed_W: np.ndarray        # [N_t]
```

---

## 7. Runner & Server Integration

### 7.1 Runner (`runner.py`)

```python
def run_liquid_solve(
    liquidrun_path: Path,
    case_dir: Path = None,
    device: str = 'cpu',
    callback: Callable = None,
) -> LiquidResult:
    """
    Full pipeline:
    1. Read .liquidrun JSON
    2. Pydantic validate → LiquidrunConfig
    3. Compile → CompiledGraph
    4. Build LiquidODILModel
    5. Adam solve
    6. Save results to case/Liquid/log/<timestamp>/
    7. Copy .liquidrun + compiled_graph.json for reproducibility
    """
```

### 7.2 CLI Entry (`cli.py`)

```
Usage: conda run -n dcdtuni python -m dcdtuni.liquid.cli --from-liquidrun <path> [--device cpu|cuda]
```

### 7.3 Server API (3 endpoints, replaces old 4)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/liquid/export-liquidrun` | Frontend POSTs .liquidrun JSON, server saves to `case/Liquid/model/` |
| POST | `/api/liquid/solve` | Trigger ODIL solve, response is SSE stream with per-epoch logs |
| GET | `/api/liquid/results` | Return most recent `liquid_result.json` |

### 7.4 File Organization

```
case/Liquid/
├── model/
│   └── MyProject.liquidrun
└── log/
    └── 2026-05-05_14-30-22/
        ├── MyProject.liquidrun       # copy for reproducibility
        ├── compiled_graph.json       # intermediate representation (debug)
        ├── loss_history.csv          # epoch, total, mass, momentum, energy, bc, ic
        ├── model_state.pt            # PyTorch model weights (resumable)
        └── liquid_result.json        # final results
```

### 7.5 Pipeline Independence

```
Air cooling:  STL_generate.html → .cfdrun → STLGenRunner → OpenFOAM → results
Liquid cooling: STL_generate.html → .liquidrun → compile_graph → LiquidODILModel → Adam → results

Fully independent. No shared runtime state.
Future coupling (not V1): liquid results → residual_heat → air CFD modified server power
```

---

## 8. Implementation Phases

### Phase 0: Scaffolding
**Goal**: Module skeleton + compilation pipeline runs end-to-end (no physics)

| Task | Output |
|------|--------|
| Clear old `src/backend/liquidcooling/`, create new directory structure | All `__init__.py` |
| Pydantic schema definitions | `schema.py` |
| CompiledGraph dataclass | `compiled_graph.py` |
| Compiler skeleton (JSON → CompiledGraph, no physics compilation) | `compiler.py` |
| BDF utilities + smooth_abs | `bdf.py` |
| BaseHandler ABC | `handlers/base.py` |
| Minimal hand-written `.liquidrun` test file | `tests/test_liquid/minimal.liquidrun` |

**Acceptance**: `compile_graph(minimal.liquidrun)` returns valid CompiledGraph, `to_json()` works.

### Phase 1: Steady-State Hydraulics (Pipe + Pump + Junction)
**Goal**: Solve P and m only (no temperature), verify mass conservation + pressure drop

| Task | Output |
|------|--------|
| PipeBatchHandler (momentum residual only, no thermal) | `handlers/pipe.py` |
| PumpBatchHandler | `handlers/pump.py` |
| LiquidODILModel (P and m state variables only) | `model.py` |
| Solver (Adam loop) | `solver.py` |
| Test: CDU → pipe → pipe → CDU loop | `tests/test_liquid/test_hydraulic.py` |

**Acceptance**:
- Simple loop flow converges to Darcy-Weisbach analytical solution (error < 1%)
- Mass conservation residual < 1e-8
- Each handler's `__main__` self-test passes

### Phase 2: Valve + T-junction Branching
**Goal**: Verify flow distribution in branching networks

| Task | Output |
|------|--------|
| ValveBatchHandler | `handlers/valve.py` |
| Compiler support for valve type | `compiler.py` update |
| Test: CDU → main pipe → T-split → two branches (one with valve) → merge → CDU | `tests/test_liquid/test_branch.py` |

**Acceptance**:
- Valve at opening=0.5 gives lower flow than fully open (qualitative correctness)
- Two branch flows sum to main pipe flow (mass conservation)
- Varying valve opening changes flow distribution

### Phase 3: Transient Thermal (Pipe thermal + ColdPlate ODE)
**Goal**: Add T_w and T_s variables, BDF time discretization

| Task | Output |
|------|--------|
| PipeBatchHandler add 1D thermal transport residual + BDF | `handlers/pipe.py` update |
| ColdPlateBatchHandler (pressure drop + dual ODE) | `handlers/cold_plate.py` |
| LiquidODILModel add T_w, T_s state variables | `model.py` update |
| Junction temperature mixing residual | `model.py` update |
| Results extraction + RackSummary aggregation | `results.py` |
| Test: CDU → pipe → cold_plate(350W) → pipe → CDU, verify temperature rise | `tests/test_liquid/test_thermal.py` |

**Acceptance**:
- Steady-state ΔT = Q/(ṁ·cp) matches analytical solution (error < 2%)
- Both BDF1 and BDF2 converge
- Chip temperature T_s responds to power step change with reasonable transient (exponential decay shape)
- cold_plate.py `__main__` self-test passes

### Phase 4: Full Single-Cabinet + Rack Expansion
**Goal**: Complete single-cabinet loop, rack auto-expansion, result aggregation

| Task | Output |
|------|--------|
| Compiler rack metadata → result aggregation indices | `compiler.py` update |
| Runner orchestrator | `runner.py` |
| CLI entry point | `cli.py` |
| Test: CDU → main pipe → Rack(3 servers, 2 CP-CU pairs each) → main pipe → CDU | `tests/test_liquid/test_single_rack.py` |

**Acceptance**:
- 3 servers' flow distribution affected by height difference (dz), bottom server slightly more flow
- RackSummary correctly aggregates per-server results
- CLI `python -m dcdtuni.liquid.cli --from-liquidrun xxx.liquidrun` runs end-to-end
- Results saved to `case/Liquid/log/<timestamp>/` with correct directory structure

### Phase 5: Frontend `liquid_graphgen.js` + Server Integration
**Goal**: Frontend exports .liquidrun, server triggers solve

| Task | Output |
|------|--------|
| `buildLiquidrunJson()` main function | `web/js/liquid_graphgen.js` |
| CDU/Rack/Pipe/Valve/T-junction compilation functions | same |
| Waypoints → arc length / elevation / bends computation | same |
| Topology validation `_validateGraph()` | same |
| Server API: export-liquidrun / solve / results | `server.py` update |
| `liquid_cooling.js` integration | `web/js/liquid_cooling.js` update |

**Acceptance**:
- In liquid mode, user clicks "Export" → `.liquidrun` file generated in `case/Liquid/model/`
- User clicks "Solve" → SSE log stream → results panel displays

### Phase 6: Convergence Optimization + Production Hardening
**Goal**: Handle real-world convergence issues, monitoring

| Task | Description |
|------|-------------|
| Adaptive weights (GradNorm / uncertainty weighting) | Replace manual weights |
| Learning rate schedule tuning | Warmup + cosine decay |
| CUDA validation | All tests pass on GPU |
| Large-scale case testing | Multi-cabinet (4+ racks, 20+ servers) |
| Frontend results visualization | Temperature colormap, flow animation |
| Loss history chart export | Frontend convergence curve display |

### Phase Dependency Graph

```
Phase 0 (Scaffolding)
    │
    ▼
Phase 1 (Steady hydraulics: Pipe + Pump)
    │
    ├──────────────┐
    ▼              ▼
Phase 2          Phase 3
(Valve+Branch)  (Transient thermal+ColdPlate)
    │              │
    └──────┬───────┘
           ▼
    Phase 4 (Full single-cabinet + Rack)
           │
           ▼
    Phase 5 (Frontend + Server integration)
           │
           ▼
    Phase 6 (Convergence optimization + hardening)
```

Phase 2 and Phase 3 can be developed in parallel (hydraulic branching and thermal are orthogonal).

---

## 9. References

- **FromGPT.md**: `/mnt/lv/litianyu/mycode/dcdtuni/Ref_paper/LiquidCooling/FromGPT.md` — detailed physics equations, component parameters, JSON schema recommendations
- **DiffLiquidTwin**: `/mnt/lv/litianyu/mycode/DiffLiquidTwin` — reference for component physics (Pipe, ColdPlate, Valve, Pump nn.Modules), topology patterns, but NOT its forward solver
- **ODIL**: Optimizing a Discrete Loss — treats PDE solutions as optimization problems, all spacetime unknowns optimized simultaneously
