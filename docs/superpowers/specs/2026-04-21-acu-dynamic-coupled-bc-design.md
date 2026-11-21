# ACU Dynamic Coupled Boundary Condition (Set Point Control)

**Date:** 2026-04-21
**Status:** Approved (revised)

## Problem

Current ACU supply temperature is a static `fixedValue` BC — every ACU blows the same user-specified temperature regardless of return air conditions. Real cooling units (and 6Sigma's model) use **Set Point control**: they modulate cooling output to maintain a target temperature, subject to capacity constraints.

Using a fixed Q (always at max capacity) is also wrong — real CRAC/CRAH units operate at 40-65% partial load (DOE, Stulz). The supply temperature error vs Set Point control can reach **7.5°C** at 50% load.

## Design

### Set Point Control Model (Steady-State)

For each ACU, at every SIMPLE iteration:

```
Q_needed  = m_dot × Cp × (T_return_avg - T_setpoint)
Q_actual  = clamp(Q_needed, 0, Q_max)
T_supply  = max(T_return_avg - Q_actual / (m_dot × Cp), T_supply_min)
```

Two operating regimes:
- **Capacity sufficient (normal):** `Q_actual = Q_needed`, so `T_supply = T_setpoint`
- **Capacity exceeded (overload):** `Q_actual = Q_max`, so `T_supply = T_return - Q_max/(m_dot×Cp) > T_setpoint`

Parameters (matching 6Sigma's ACU Property Sheet):
- `T_setpoint` = target supply temperature (°C) — 6Sigma "Set Point" field
- `Q_max` = maximum sensible cooling capacity (W) — 6Sigma "Maximum Sensible Cooling"
- `T_supply_min` = minimum achievable supply temperature (°C) — 6Sigma "Minimum Supply Temperature"
- `m_dot` = ρ × volumeFlowRate (kg/s)

### OpenFOAM Implementation: `exprFixedValue`

Available in OpenFOAM v2406 (ESI fork). No compilation needed. Supports `min`/`max`, chained variables, and cross-patch `weightAverage`.

Generated T-field BC for each ACU supply patch:

```
ACU_A1_Back_patch
{
    type            exprFixedValue;
    value           uniform 295.15;

    variables
    (
        "mdot = 2.04"
        "Tsetpoint = 295.15"
        "Qmax = 35000.0"
        "Tmin = 293.35"
        "Treturn{ACU_A1_Front_patch} = weightAverage(T)"
        "Qneeded = mdot * 1005.0 * (Treturn - Tsetpoint)"
        "Qactual = min(max(Qneeded, 0), Qmax)"
    );
    valueExpr       "max(Treturn - Qactual / (mdot * 1005.0), Tmin)";
}
```

### Backward Compatibility

The new BC activates **only when `coolingCapacity` is provided** in the ACU physics. When absent, the existing `fixedValue` BC is generated (no behavior change for old cfdproj files).

### Data Flow

```
cfdproj/cfdrun ACU config
  → ACUFacePhysics (schema: + coolingCapacity, minSupplyTemperature, setPointTemperature)
  → _build_bridge_bc() (BCSpec: + cooling_capacity, min_supply_temp, set_point_temp, return_patch_name)
  → _write_t_field() (exprFixedValue with Set Point control expression)
```

### Changes Required

#### 1. `src/backend/aircooling/runner/bridge_bc.py` — BCSpec

Add four optional fields:

```python
cooling_capacity: Optional[float] = None      # W (max sensible cooling)
min_supply_temp: Optional[float] = None        # Celsius
set_point_temp: Optional[float] = None         # Celsius (supply air set point)
return_patch_name: Optional[str] = None        # paired return patch name
```

#### 2. `src/backend/aircooling/runner/cfdproj_schema.py` — ACUFacePhysics

Add three optional fields:

```python
coolingCapacity: Optional[float] = None        # kW (frontend unit)
minSupplyTemperature: Optional[float] = None   # Celsius
setPointTemperature: Optional[float] = None    # Celsius (supply air set point)
```

#### 3. `src/backend/aircooling/runner/stlgen_runner.py` — _build_bridge_bc()

When registering ACU supply faces, also pass:
- `cooling_capacity` = coolingCapacity × 1000 (kW → W)
- `min_supply_temp` from physics
- `set_point_temp` from physics (defaults to `temperature` if not set)
- `return_patch_name` = sanitized solidName of the first return face of the same ACU

#### 4. `src/backend/aircooling/runner/fields.py` — _write_t_field()

In the `acu_supply` branch:
- If `cooling_capacity` is set and `return_patch_name` is set → generate `exprFixedValue` with Set Point logic
- Otherwise → existing `fixedValue` (backward compat)

Expression generation:

```python
if cooling_capacity and return_patch_name:
    mdot = 1.2 * flow_rate
    t_setpoint_k = (set_point_temp or temperature) + 273.15
    t_min_k = (min_supply_temp or temperature) + 273.15
    lines.extend([
        f"    {patch_name}",
        "    {",
        "        type            exprFixedValue;",
        f"        value           uniform {t_setpoint_k};",
        "        variables",
        "        (",
        f'            "mdot = {mdot}"',
        f'            "Tsetpoint = {t_setpoint_k}"',
        f'            "Qmax = {cooling_capacity}"',
        f'            "Tmin = {t_min_k}"',
        f'            "Treturn{{{return_patch_name}}} = weightAverage(T)"',
        '            "Qneeded = mdot * 1005.0 * (Treturn - Tsetpoint)"',
        '            "Qactual = min(max(Qneeded, 0), Qmax)"',
        "        );",
        '        valueExpr       "max(Treturn - Qactual / (mdot * 1005.0), Tmin)";',
        "    }",
    ])
```

#### 5. Frontend (deferred)

ACU property panel: add "Cooling Capacity (kW)", "Set Point (°C)", and "Min Supply Temperature (°C)" inputs. These get saved into `supplyFaces[].physics`. This is a separate task.

### What This Does NOT Include

- **Dead Band**: Only meaningful for transient simulations with on/off cycling; steady-state converges to a single operating point
- **Performance curves Q=f(T_return)**: Would require lookup tables; can be added later as a `codedFixedValue` variant
- **"Average Supply Air" sensor mode**: All ACUs use their own return patch, not a group average
- **Return Air Set Point mode**: Only supply air set point is implemented (user specifies target T_supply, not target T_return)
- **Frontend UI changes**: Deferred to a separate task

### Testing Strategy

1. Unit test: `exprFixedValue` BC is generated when `cooling_capacity` is set
2. Unit test: `fixedValue` BC is generated when `cooling_capacity` is None (backward compat)
3. Unit test: Set Point control variables appear correctly (Tsetpoint, Qmax, Qneeded, Qactual)
4. Unit test: `return_patch_name` correctly references the paired return patch
5. Unit test: `T_supply_min` clamp appears in expression
6. Integration: run a case with the new BC and verify solver accepts it

### References

- [CoolSim WP105: CRAC Thermal Boundary Condition Options](https://coolsimsoftware.com/whitepapers/wp105-crac-thermal-boundary-condition-options-in-coolsim/)
- [TileFlow: Supply Air Temperature Control](https://www.tileflow.com/features.html)
- [Stulz: Data Center Temperature Control](https://www.stulz.com/newsroom/detail/data-center-temperature-control/)
- [DOE CRAC NOPR TSD: 65% average load](https://www1.eere.energy.gov/buildings/appliance_standards/pdfs/crac_nopr_tsd_ch_04_01_11_2012.pdf)
- [OpenFOAM: Expression Syntax Guide](https://www.openfoam.com/documentation/guides/latest/doc/openfoam-guide-expression-syntax.html)
