# ACU Dynamic Cooling Model — Design Spec

## Goal

Enable physically accurate ACU supply temperature modeling in transient simulations by introducing selectable cooling performance modes (fixed temperature, linear capacity model, performance curve table), replacing the current one-size-fits-all approach. All implementation uses OpenFOAM expression BCs — no codedFixedValue.

## Background

### Current State

The codebase already has a dynamic ACU supply temperature BC in `fields.py` (lines 780–823) that activates when **all three** conditions hold:
1. `solverType == "transient"`
2. `cooling_capacity` exists
3. `return_patch_name` exists

Formula: `T_supply = max(T_return - Q_actual / (mdot * Cp), T_min)` where `Q_actual = clamp(Q_needed, 0, Q_max)`.

This is functionally equivalent to CoolSim's "Cooling Capacity" mode (WP105). However:
- The frontend has the parameter inputs (Cooling kW, Coolant T, Set Point) but they may never have been end-to-end tested on a real case
- `Q_max` is a fixed constant — real CRAH units have capacity that varies with return air temperature
- There is no user-visible mode selection; the dynamic BC activates implicitly

### Research Findings

- **Reference papers**: bs2023_1197 (Midea/IBPSA 2023) used FMI co-simulation with PID, entropy-21-00098 used NTU effectiveness model, energies-12-02996 studied CRAC failure transients
- **CoolSim WP105** documents four CRAC BC modes: Supply Temperature, Temperature Drop, Cooling Capacity, Performance Data
- **OpenFOAM expression BCs** support cross-patch `weightAverage(T)` for return air feedback, arithmetic for energy balance, and nested ternary for piecewise interpolation — sufficient for all three modes without codedFixedValue
- **Thermal lag** (Level 2) requires stateful BCs (codedFixedValue) — explicitly out of scope for this iteration

## Architecture

### Approach: Pure Expression BC

All modes generate `uniformFixedValue` with `type expression` Function1. No codedFixedValue, no runtime C++ compilation.

- **fixedTemp**: simple `fixedValue` (steady or transient)
- **linearCapacity**: expression with `Q_max = Q_rated * (T_return - T_coolant) / (T_rated - T_coolant)`
- **tableCapacity**: expression with piecewise ternary interpolation of user-provided (T_return, Q_max) table

## Frontend UI

### ACU Property Panel — Cooling Model Dropdown

In the ACU supply face property section, add a dropdown **"制冷模型"** (Cooling Model):

| Mode Value | Display Text (zh) | Display Text (en) | Steady-State | Transient |
|------------|-------------------|-------------------|-------------|-----------|
| `fixedTemp` | 固定送风温度 | Fixed Supply Temp | ✅ enabled | ✅ enabled |
| `linearCapacity` | 线性制冷模型 | Linear Capacity | 🔒 disabled+tooltip | ✅ enabled |
| `tableCapacity` | 性能曲线表格 | Performance Table | 🔒 disabled+tooltip | ✅ enabled |

Disabled tooltip: "需切换为瞬态模式" / "Requires transient mode"

### Parameter Panels per Mode

**fixedTemp:**
- 送风温度 (°C) — `temperature` field, default 15

**linearCapacity:**
- Set Point (°C) — `setPointTemperature`, default 22
- 额定制冷量 (kW) — `coolingCapacity`, default 35
- 额定回风温度 (°C) — `ratedReturnTemp` (NEW), default 35
- 冷冻水温度 (°C) — `coolantTemperature`, default 14

**tableCapacity:**
- Set Point (°C) — `setPointTemperature`, default 22
- 冷冻水温度 (°C) — `coolantTemperature`, default 14
- 性能表格 — editable table with columns `回风温度 (°C)` and `制冷量 (kW)`, default rows: (25, 20), (30, 30), (35, 35)

### Interaction Rules

- `acuPerformanceMode` stored at model level (one ACU = one mode). During cfdrun export, all supply faces of that ACU inherit the same `coolingModel` from the model-level mode
- Switching modes preserves all parameters (no data loss on switch-back)
- Steady-state: linearCapacity/tableCapacity options visible but grayed out with tooltip
- The dropdown detects steady/transient from the solver tab's current `solverType` setting
- For `tableCapacity`, the performance table is stored at model level (`model.coolingPerformanceTable`) since it describes the whole ACU unit, not individual faces

## Data Model

### Frontend State

```javascript
model.acuPerformanceMode = "fixedTemp" | "linearCapacity" | "tableCapacity"  // default: "fixedTemp"

// Per supply-face physics (existing fields, reused):
pp.temperature            // °C — base supply temp
pp.setPointTemperature    // °C — target supply temp
pp.coolingCapacity        // kW — rated cooling capacity
pp.coolantTemperature     // °C — chilled water temp

// New fields:
pp.ratedReturnTemp        // °C — rated return air temp (linearCapacity)
pp.coolingPerformanceTable // [{returnTemp: number, capacity: number}, ...] (tableCapacity)
```

### cfdrun JSON Export

```json
{
  "supplyFaces": [{
    "solid": "PCU_1_supply",
    "physics": {
      "temperature": 15,
      "volumeFlowRate": 4.0,
      "setPointTemperature": 22,
      "coolingCapacity": 35,
      "coolantTemperature": 14,
      "coolingModel": {
        "mode": "linearCapacity",
        "ratedReturnTemp": 35
      }
    }
  }]
}
```

For `tableCapacity`:
```json
{
  "coolingModel": {
    "mode": "tableCapacity",
    "performanceTable": [
      {"returnTemp": 25, "capacity": 20},
      {"returnTemp": 30, "capacity": 30},
      {"returnTemp": 35, "capacity": 35}
    ]
  }
}
```

For `fixedTemp` or absent: `coolingModel` field absent or `{"mode": "fixedTemp"}`.

### Pydantic Schema Changes

**`cfdproj_schema.py` — `ACUFacePhysics`** add:
```python
ratedReturnTemp: Optional[float] = None
coolingPerformanceTable: Optional[List[dict]] = None
```

**`bridge_bc.py` — `BCSpec`** add:
```python
cooling_model_mode: Optional[str] = None
rated_return_temp: Optional[float] = None
cooling_performance_table: Optional[List[dict]] = None
```

### Backward Compatibility

- No `coolingModel` + no `coolingCapacity` → `fixedTemp` (fixedValue BC)
- No `coolingModel` + has `coolingCapacity` + transient → existing Level 0 energy balance (preserves current behavior exactly)
- Has `coolingModel` → use specified mode

## Backend BC Generation

### fields.py — `_write_t_field` ACU Supply Branch

Decision tree:
```
if coolingModel.mode == "fixedTemp" or (no coolingModel and no cooling_capacity):
    → fixedValue with temperature (+ timeVarying table if supplyTemp entries exist)

elif coolingModel.mode == "linearCapacity":
    → expression BC with Q_max = Q_rated * (T_return - T_coolant) / (T_rated - T_coolant)

elif coolingModel.mode == "tableCapacity":
    → expression BC with Q_max = piecewise_interpolation(T_return, table)

elif no coolingModel but has cooling_capacity and transient:
    → existing Level 0 expression BC (Q_max = constant)
```

### Linear Capacity Expression

```
expression "max(Tret_X - min(max(mdot_X*1005.0*(Tret_X-Tsp_X),0), max(Qr_X*(Tret_X-Tc_X)/(Tr_X-Tc_X),0)) / (mdot_X*1005.0), Tmin_X)";
variables
(
    "mdot_X = {1.2 * flowRate}"
    "Tsp_X = {setPoint + 273.15}"
    "Qr_X = {coolingCapacity * 1000}"
    "Tr_X = {ratedReturnTemp + 273.15}"
    "Tc_X = {coolantTemp + 273.15}"
    "Tmin_X = {coolantTemp + 6 + 273.15}"
    "Tret_X{return_patch} = weightAverage(T)"
);
```

### Table Capacity Expression

The `(T_return, Q_max)` table is compiled into a piecewise linear interpolation using nested ternary:

For table `[(T1, Q1), (T2, Q2), (T3, Q3)]`:
```
Q_max = (Tret < T1_K ? Q1*1000 : (Tret < T2_K ? Q1*1000+(Tret-T1_K)*(Q2*1000-Q1*1000)/(T2_K-T1_K) : (Tret < T3_K ? Q2*1000+(Tret-T2_K)*(Q3*1000-Q2*1000)/(T3_K-T2_K) : Q3*1000)))
```

A new helper `_build_piecewise_capacity_expression(table_entries)` generates this string. Reuses the pattern from `_build_piecewise_dt_expression`.

The rest of the expression (Q_needed → Q_actual → T_supply) is identical to linearCapacity.

### stlgen_runner.py Changes

In `_build_bridge_bc()` ACU supply section, read the new `coolingModel` field:

```python
cooling_model = phys.get("coolingModel", {})
cm_mode = cooling_model.get("mode")
rated_return = cooling_model.get("ratedReturnTemp")
perf_table = cooling_model.get("performanceTable")

registry.register(solid, BCSpec(
    ...,
    cooling_model_mode=cm_mode,
    rated_return_temp=rated_return,
    cooling_performance_table=perf_table,
))
```

## Testing

### Unit Tests (pytest, TDD)

| Test Name | What It Verifies |
|-----------|-----------------|
| `test_acu_supply_fixed_temp_mode` | fixedTemp generates fixedValue regardless of solver type |
| `test_acu_supply_linear_capacity_expression` | linearCapacity generates expression with Qrated, Trated, Tcool variables |
| `test_acu_supply_linear_capacity_cold_return` | Q_max floors to 0 when T_return < T_coolant (no negative cooling) |
| `test_acu_supply_table_capacity_expression` | tableCapacity generates piecewise ternary with linear interpolation |
| `test_acu_supply_table_capacity_extrapolation` | Table boundary: constant extrapolation beyond endpoints |
| `test_acu_supply_legacy_no_model_transient` | Old cfdrun with cooling_capacity but no coolingModel → Level 0 behavior preserved |
| `test_acu_supply_legacy_no_model_steady` | Old cfdrun steady-state → fixedValue |

### End-to-End Test (browser-use + Slab)

1. Start server, load Slab project via browser-use
2. Select ACU → dropdown "线性制冷模型" → fill: Set Point 22°C, Cooling 35kW, Rated Return 35°C, Coolant 14°C
3. Solver tab → transient, 3s physical time
4. Export + mesh + solve
5. Read generated `0/T` — verify expression BC with `weightAverage`, `Qr_`, `Tr_` variables
6. Check Operation Log — 0 FATAL, 0 NaN

## Out of Scope

- Level 2 thermal response lag (requires codedFixedValue — separate future spec)
- ACU return face BC changes (always zeroGradient T)
- Variable fan speed coupling with cooling model (fan speed affects mdot which affects Q — already handled since mdot is computed from current flowRate)
- Co-simulation with external refrigeration model (bs2023_1197 approach)

## i18n Keys

| Key | zh-CN | en-US |
|-----|-------|-------|
| `label.cooling_model` | 制冷模型 | Cooling Model |
| `option.fixed_temp` | 固定送风温度 | Fixed Supply Temp |
| `option.linear_capacity` | 线性制冷模型 | Linear Capacity |
| `option.table_capacity` | 性能曲线表格 | Performance Table |
| `label.rated_return_temp` | 额定回风温度 | Rated Return Temp |
| `label.rated_cooling` | 额定制冷量 | Rated Cooling |
| `hint.requires_transient` | 需切换为瞬态模式 | Requires transient mode |
| `label.perf_table_return` | 回风温度 (°C) | Return Temp (°C) |
| `label.perf_table_capacity` | 制冷量 (kW) | Capacity (kW) |
