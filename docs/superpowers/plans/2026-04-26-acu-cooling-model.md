# ACU Dynamic Cooling Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add selectable cooling performance modes (fixed temp, linear capacity, performance table) to ACU supply temperature BC generation, with frontend dropdown and full expression BC backend.

**Architecture:** Three cooling model modes generate different OpenFOAM BCs via pure expression Function1. Frontend dropdown controls mode selection; cfdrun JSON carries `coolingModel` object; `fields.py` dispatches to appropriate expression builder. Backward compatible — old cfdrun files work unchanged.

**Tech Stack:** Python (Pydantic, Jinja2), OpenFOAM expression BCs, HTML/JS (Three.js frontend), pytest

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `src/backend/aircooling/runner/bridge_bc.py` | BCSpec data model | Modify: add 3 new Optional fields |
| `src/backend/aircooling/runner/cfdproj_schema.py` | Pydantic schema for cfdrun validation | Modify: add 2 new Optional fields to ACUFacePhysics |
| `src/backend/aircooling/runner/stlgen_runner.py` | Reads cfdrun → builds BCSpec registry | Modify: read `coolingModel` and pass new fields |
| `src/backend/aircooling/runner/fields.py` | Generates OpenFOAM 0/T field file | Modify: add linearCapacity + tableCapacity expression builders |
| `src/backend/tests/test_template_params.py` | Unit tests for BC generation | Modify: add 7 new test methods |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Frontend UI + cfdrun export | Modify: add cooling model dropdown + conditional panels + export logic |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | Chinese translations | Modify: add 9 new keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | English translations | Modify: add 9 new keys |

---

### Task 1: Schema — Add Cooling Model Fields to BCSpec and ACUFacePhysics

**Files:**
- Modify: `src/backend/aircooling/runner/bridge_bc.py:44-60`
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py:154-162`

- [ ] **Step 1: Add new fields to BCSpec**

In `src/backend/aircooling/runner/bridge_bc.py`, after the `return_patch_name` field (line 58), add three new fields:

```python
    cooling_model_mode: Optional[str] = None           # "fixedTemp" | "linearCapacity" | "tableCapacity"
    rated_return_temp: Optional[float] = None           # Celsius (rated return air temperature for linear model)
    cooling_performance_table: Optional[list] = None    # [{returnTemp: float, capacity: float}, ...] for table model
```

The full BCSpec class fields section (lines 44-61) should look like:

```python
class BCSpec(BaseModel):
    """Boundary condition specification for a single STL solid patch."""
    bc_type: str = "wall"
    temperature: Optional[float] = None   # Celsius; used for supply/fixedTemp patches
    flow_rate: Optional[float] = None     # m³/s; used for supply/return/fan patches
    power: Optional[float] = None         # W; used for fan_outlet temperature computation
    sigma: Optional[float] = None         # porosity coefficient: rack leakage_zone cellZones, and derived-from-openAreaPct for perforated_baffle wall/cyclic/porous classifier
    perforated: Optional["PerforatedSpec"] = None  # replaces sigma for perforated baffles
    heat_flux: Optional[float] = None     # W/m²; used for heat_source_flux BC
    physical_flow_area: Optional[float] = None  # m²; real server-face area for U_phys derivation (fan_outlet/fan_inlet only)
    cooling_capacity: Optional[float] = None      # W (max sensible cooling)
    min_supply_temp: Optional[float] = None        # Celsius (derived: coolant_temp + 6°C)
    set_point_temp: Optional[float] = None         # Celsius (supply air set point)
    coolant_temp: Optional[float] = None           # Celsius (chilled water inlet temperature)
    return_patch_name: Optional[str] = None        # paired return patch for exprFixedValue
    cooling_model_mode: Optional[str] = None       # "fixedTemp" | "linearCapacity" | "tableCapacity"
    rated_return_temp: Optional[float] = None      # Celsius (rated return air temp for linear model)
    cooling_performance_table: Optional[list] = None  # [{returnTemp, capacity}] for table model
    timeVarying: Optional[dict] = None             # time-varying schedule from .cfdrun JSON
    surface_area: Optional[float] = None           # m²; total surface area (heat sources: W→W/m²)
```

- [ ] **Step 2: Add new fields to ACUFacePhysics**

In `src/backend/aircooling/runner/cfdproj_schema.py`, after the `minSupplyTemperature` field (line 161), add:

```python
    ratedReturnTemp: Optional[float] = None            # Celsius (rated return air temp)
    coolingPerformanceTable: Optional[List[dict]] = None  # [{returnTemp, capacity}]
```

The full ACUFacePhysics class (lines 154-162) should look like:

```python
class ACUFacePhysics(CFDBaseModel):
    temperature: Optional[float] = None   # supply face only
    volumeFlowRate: float = 4.0
    velocity: float = 2.0
    coolingCapacity: Optional[float] = None        # kW
    coolantTemperature: Optional[float] = None     # Celsius (chilled water inlet)
    setPointTemperature: Optional[float] = None    # Celsius
    minSupplyTemperature: Optional[float] = None   # Celsius (legacy, prefer coolantTemperature)
    ratedReturnTemp: Optional[float] = None        # Celsius (rated return air temp)
    coolingPerformanceTable: Optional[List[dict]] = None  # [{returnTemp, capacity}]
    timeVarying: Dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -x --tb=short`
Expected: All 72 tests pass (new Optional fields with None defaults don't break anything)

- [ ] **Step 4: Commit**

```bash
git add src/backend/aircooling/runner/bridge_bc.py src/backend/aircooling/runner/cfdproj_schema.py
git commit -m "feat(schema): add cooling model fields to BCSpec and ACUFacePhysics"
```

---

### Task 2: Runner — Read coolingModel from cfdrun and Pass to BCSpec

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:3083-3101`

- [ ] **Step 1: Update `_build_bridge_bc` ACU supply section**

In `src/backend/aircooling/runner/stlgen_runner.py`, replace the ACU supply face registration block (lines 3083-3101) with:

```python
            for sf in acu.get("supplyFaces", []):
                solid = _sanitize(sf["solidName"])
                phys = sf.get("physics", {})
                cooling_kw = phys.get("coolingCapacity")
                coolant_c = phys.get("coolantTemperature")
                min_sup = phys.get("minSupplyTemperature")
                if min_sup is None and coolant_c is not None:
                    min_sup = coolant_c + 6.3
                cooling_model = phys.get("coolingModel", {})
                cm_mode = cooling_model.get("mode")
                rated_return = cooling_model.get("ratedReturnTemp")
                perf_table = cooling_model.get("performanceTable")
                registry.register(solid, BCSpec(
                    bc_type="acu_supply",
                    temperature=phys.get("temperature", 15.0),
                    flow_rate=phys.get("volumeFlowRate", 4.0),
                    cooling_capacity=cooling_kw * 1000 if cooling_kw else None,
                    set_point_temp=phys.get("setPointTemperature"),
                    coolant_temp=coolant_c,
                    min_supply_temp=min_sup,
                    return_patch_name=return_patch,
                    cooling_model_mode=cm_mode,
                    rated_return_temp=rated_return,
                    cooling_performance_table=perf_table,
                    timeVarying=phys.get("timeVarying", {}),
                ))
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -x --tb=short`
Expected: All 72 tests pass

- [ ] **Step 3: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(runner): read coolingModel from cfdrun and pass to BCSpec"
```

---

### Task 3: Backend — fixedTemp Mode Test and Verification

**Files:**
- Test: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Write the failing test for fixedTemp mode**

Add to `TestTimeVaryingBC` class in `src/backend/tests/test_template_params.py`:

```python
    def test_acu_supply_fixed_temp_mode(self, tmp_path):
        """fixedTemp mode should generate fixedValue regardless of solver type."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "fixedTemp",
            }
        }
        # Even with transient solver AND cooling_capacity, fixedTemp mode → fixedValue
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "fixedValue" in t_text
        assert "uniform 288.15" in t_text  # 15 + 273.15
        assert "expression" not in t_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_fixed_temp_mode -xvs`
Expected: FAIL — currently `cooling_model_mode` is ignored; the code falls through to the existing Level 0 expression BC because `cooling_capacity` exists and solver is transient.

- [ ] **Step 3: Implement fixedTemp mode gate in `_write_t_field`**

In `src/backend/aircooling/runner/fields.py`, modify the `acu_supply` branch (lines 780-834). Replace the current condition block:

```python
                elif bc_type == "acu_supply" and temperature is not None:
                    cooling_capacity = bc_info.get("cooling_capacity")
                    return_patch = bc_info.get("return_patch_name")
                    _solver_type = (self._simulation_config or {}).get("solverType",
                                   (self._simulation_config or {}).get("solver_type", "steady"))
                    _use_expression = (cooling_capacity and return_patch
                                       and _solver_type == "transient")
                    if _use_expression:
```

with:

```python
                elif bc_type == "acu_supply" and temperature is not None:
                    cooling_capacity = bc_info.get("cooling_capacity")
                    return_patch = bc_info.get("return_patch_name")
                    cm_mode = bc_info.get("cooling_model_mode")
                    _solver_type = (self._simulation_config or {}).get("solverType",
                                   (self._simulation_config or {}).get("solver_type", "steady"))
                    _is_transient = _solver_type == "transient"
                    # Determine which expression path to use:
                    # - explicit fixedTemp → always fixedValue
                    # - linearCapacity / tableCapacity → new expression paths (Task 4/5)
                    # - no mode but cooling_capacity + transient → legacy Level 0
                    _use_expression = (
                        cm_mode not in (None, "fixedTemp")
                        or (cm_mode is None and cooling_capacity and return_patch and _is_transient)
                    ) and cm_mode != "fixedTemp"
                    if _use_expression:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_fixed_temp_mode -xvs`
Expected: PASS

- [ ] **Step 5: Write and run legacy backward-compat tests**

Add to `TestTimeVaryingBC`:

```python
    def test_acu_supply_legacy_no_model_transient(self, tmp_path):
        """Old cfdrun (cooling_capacity but no coolingModel) in transient → Level 0 expression."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "expression" in t_text
        assert "Qmax_ACU_1_supply = 35000" in t_text
        assert "weightAverage(T)" in t_text

    def test_acu_supply_legacy_no_model_steady(self, tmp_path):
        """Old cfdrun steady-state → fixedValue even with cooling_capacity."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "set_point_temp": 22.0,
            }
        }
        fg = _make_field_gen(tmp_path, bc_data, simulation_config={"solverType": "steady"})
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "fixedValue" in t_text
        assert "uniform 295.15" in t_text  # 22 + 273.15 (set_point)
        assert "expression" not in t_text
```

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_legacy_no_model_transient src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_legacy_no_model_steady -xvs`
Expected: Both PASS (backward compatibility preserved)

- [ ] **Step 6: Run full test suite**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -x --tb=short`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/tests/test_template_params.py
git commit -m "feat(fields): add fixedTemp mode gate for ACU cooling model dispatch"
```

---

### Task 4: Backend — Linear Capacity Expression BC

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py:780-823`
- Test: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Write the failing test for linearCapacity**

Add to `TestTimeVaryingBC`:

```python
    def test_acu_supply_linear_capacity_expression(self, tmp_path):
        """linearCapacity mode generates expression with Q_max = f(T_return)."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 1.7,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "linearCapacity",
                "rated_return_temp": 35.0,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "uniformFixedValue" in t_text
        assert "expression" in t_text
        # Verify linear model variables are present
        assert "Qrated_ACU_1_supply = 35000" in t_text
        assert "Trated_ACU_1_supply = 308.15" in t_text  # 35 + 273.15
        assert "Tcool_ACU_1_supply = 287.15" in t_text   # 14 + 273.15
        assert "weightAverage(T)" in t_text
        # Verify the expression contains the linear Q_max formula
        assert "Qrated_ACU_1_supply" in t_text
        assert "Trated_ACU_1_supply" in t_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_linear_capacity_expression -xvs`
Expected: FAIL — `linearCapacity` mode not yet handled in `_write_t_field`

- [ ] **Step 3: Implement linearCapacity expression in `_write_t_field`**

In `src/backend/aircooling/runner/fields.py`, inside the `if _use_expression:` block, replace the current single expression path with mode dispatch. The full block (after the `if _use_expression:` line) should be:

```python
                    if _use_expression:
                        flow_rate = bc_info.get("flow_rate") or 4.0
                        mdot = 1.2 * flow_rate
                        set_point = bc_info.get("set_point_temp")
                        t_setpoint_k = (set_point if set_point is not None else temperature) + 273.15
                        min_sup = bc_info.get("min_supply_temp")
                        coolant = bc_info.get("coolant_temp")
                        if min_sup is not None:
                            t_min_k = min_sup + 273.15
                        elif coolant is not None:
                            t_min_k = (coolant + 6.0) + 273.15
                        else:
                            sp = set_point if set_point is not None else temperature
                            t_min_k = (sp - 5.0) + 273.15
                        safe_id = patch_name.replace("-", "_").replace(" ", "_")

                        if cm_mode == "linearCapacity":
                            rated_return = bc_info.get("rated_return_temp")
                            cooling_cap = bc_info.get("cooling_capacity") or 35000
                            t_rated_k = (rated_return if rated_return is not None else 35.0) + 273.15
                            t_cool_k = (coolant if coolant is not None else 14.0) + 273.15
                            lines.extend([
                                f"    {patch_name}",
                                "    {",
                                "        type            uniformFixedValue;",
                                f"        value           uniform {t_setpoint_k};",
                                "        uniformValue",
                                "        {",
                                "            type            expression;",
                                f'            expression      "max(Tret_{safe_id} - min(max(mdot_{safe_id}*1005.0*(Tret_{safe_id}-Tsp_{safe_id}),0), max(Qrated_{safe_id}*(Tret_{safe_id}-Tcool_{safe_id})/(Trated_{safe_id}-Tcool_{safe_id}),0)) / (mdot_{safe_id}*1005.0), Tmin_{safe_id})";',
                                "            variables",
                                "            (",
                                f'                "mdot_{safe_id} = {mdot}"',
                                f'                "Tsp_{safe_id} = {t_setpoint_k}"',
                                f'                "Qrated_{safe_id} = {cooling_cap}"',
                                f'                "Trated_{safe_id} = {t_rated_k}"',
                                f'                "Tcool_{safe_id} = {t_cool_k}"',
                                f'                "Tmin_{safe_id} = {t_min_k}"',
                                f'                "Tret_{safe_id}{{{return_patch}}} = weightAverage(T)"',
                                "            );",
                                "        };",
                                "    }",
                            ])
                        elif cm_mode == "tableCapacity":
                            # Placeholder — implemented in Task 5
                            pass
                        else:
                            # Legacy Level 0: constant Q_max
                            lines.extend([
                                f"    {patch_name}",
                                "    {",
                                "        type            uniformFixedValue;",
                                f"        value           uniform {t_setpoint_k};",
                                "        uniformValue",
                                "        {",
                                "            type            expression;",
                                f'            expression      "max(Treturn_{safe_id} - Qactual_{safe_id} / (mdot_{safe_id} * 1005.0), Tmin_{safe_id})";',
                                "            variables",
                                "            (",
                                f'                "mdot_{safe_id} = {mdot}"',
                                f'                "Tsetpoint_{safe_id} = {t_setpoint_k}"',
                                f'                "Qmax_{safe_id} = {cooling_capacity}"',
                                f'                "Tmin_{safe_id} = {t_min_k}"',
                                f'                "Treturn_{safe_id}{{{return_patch}}} = weightAverage(T)"',
                                f'                "Qneeded_{safe_id} = mdot_{safe_id} * 1005.0 * (Treturn_{safe_id} - Tsetpoint_{safe_id})"',
                                f'                "Qactual_{safe_id} = min(max(Qneeded_{safe_id}, 0), Qmax_{safe_id})"',
                                "            );",
                                "        };",
                                "    }",
                            ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_linear_capacity_expression -xvs`
Expected: PASS

- [ ] **Step 5: Write and run cold-return edge case test**

Add to `TestTimeVaryingBC`:

```python
    def test_acu_supply_linear_capacity_cold_return(self, tmp_path):
        """When T_return < T_coolant, Q_max should floor to 0 (no negative cooling)."""
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "cooling_capacity": 35000,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "linearCapacity",
                "rated_return_temp": 35.0,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        # The expression uses max(..., 0) to prevent negative Q_max
        # Verify the outer max(Qrated*(Tret-Tcool)/(Trated-Tcool), 0) structure
        assert "max(Qrated_ACU_1_supply" in t_text
        assert ",0)" in t_text.replace(" ", "")
```

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_linear_capacity_cold_return -xvs`
Expected: PASS (the `max(..., 0)` is already in the expression from Step 3)

- [ ] **Step 6: Run full test suite**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -x --tb=short`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/tests/test_template_params.py
git commit -m "feat(fields): linearCapacity expression BC for ACU supply temperature"
```

---

### Task 5: Backend — Table Capacity Expression BC

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py`
- Test: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Write the failing test for tableCapacity**

Add to `TestTimeVaryingBC`:

```python
    def test_acu_supply_table_capacity_expression(self, tmp_path):
        """tableCapacity generates piecewise ternary interpolation for Q_max = f(T_return)."""
        perf_table = [
            {"returnTemp": 25, "capacity": 20},
            {"returnTemp": 30, "capacity": 30},
            {"returnTemp": 35, "capacity": 35},
        ]
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "tableCapacity",
                "cooling_performance_table": perf_table,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        assert "uniformFixedValue" in t_text
        assert "expression" in t_text
        # Verify piecewise interpolation breakpoints in Kelvin
        assert "298.15" in t_text  # 25 + 273.15
        assert "303.15" in t_text  # 30 + 273.15
        assert "308.15" in t_text  # 35 + 273.15
        # Verify capacity values in Watts
        assert "20000" in t_text   # 20 kW → 20000 W
        assert "30000" in t_text   # 30 kW → 30000 W
        assert "35000" in t_text   # 35 kW → 35000 W
        assert "weightAverage(T)" in t_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_table_capacity_expression -xvs`
Expected: FAIL — `tableCapacity` branch is a `pass` placeholder

- [ ] **Step 3: Add `_build_piecewise_capacity_expression` helper**

In `src/backend/aircooling/runner/fields.py`, after the existing `_build_piecewise_dt_expression` function (line 64), add:

```python
def _build_piecewise_capacity_expression(table_entries, tret_var):
    """Build a piecewise-linear interpolation expression for Q_max = f(T_return).

    Each entry is {"returnTemp": float (°C), "capacity": float (kW)}.
    Returns an expression string using nested ternary operators that
    linearly interpolates between data points (Kelvin, Watts) and does
    constant extrapolation beyond the endpoints.
    """
    points = sorted(table_entries, key=lambda e: e["returnTemp"])
    pts = [(e["returnTemp"] + 273.15, e["capacity"] * 1000) for e in points]
    if len(pts) == 1:
        return str(pts[0][1])
    expr = str(pts[-1][1])
    for i in range(len(pts) - 2, -1, -1):
        t_lo, q_lo = pts[i]
        t_hi, q_hi = pts[i + 1]
        slope = f"({q_lo}+({tret_var}-{t_lo})*{(q_hi - q_lo) / (t_hi - t_lo):.6f})"
        expr = f"({tret_var}<{t_hi} ? {slope} : {expr})"
    t_first = pts[0][0]
    expr = f"({tret_var}<{t_first} ? {pts[0][1]} : {expr})"
    return expr
```

- [ ] **Step 4: Implement tableCapacity branch in `_write_t_field`**

In the `if _use_expression:` block in `_write_t_field`, replace the `elif cm_mode == "tableCapacity": pass` placeholder with:

```python
                        elif cm_mode == "tableCapacity":
                            perf_table = bc_info.get("cooling_performance_table") or []
                            if not perf_table:
                                perf_table = [{"returnTemp": 25, "capacity": 20},
                                              {"returnTemp": 30, "capacity": 30},
                                              {"returnTemp": 35, "capacity": 35}]
                            t_cool_k = (coolant if coolant is not None else 14.0) + 273.15
                            qmax_expr = _build_piecewise_capacity_expression(
                                perf_table, f"Tret_{safe_id}")
                            lines.extend([
                                f"    {patch_name}",
                                "    {",
                                "        type            uniformFixedValue;",
                                f"        value           uniform {t_setpoint_k};",
                                "        uniformValue",
                                "        {",
                                "            type            expression;",
                                f'            expression      "max(Tret_{safe_id} - min(max(mdot_{safe_id}*1005.0*(Tret_{safe_id}-Tsp_{safe_id}),0), max({qmax_expr},0)) / (mdot_{safe_id}*1005.0), Tmin_{safe_id})";',
                                "            variables",
                                "            (",
                                f'                "mdot_{safe_id} = {mdot}"',
                                f'                "Tsp_{safe_id} = {t_setpoint_k}"',
                                f'                "Tmin_{safe_id} = {t_min_k}"',
                                f'                "Tret_{safe_id}{{{return_patch}}} = weightAverage(T)"',
                                "            );",
                                "        };",
                                "    }",
                            ])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_table_capacity_expression -xvs`
Expected: PASS

- [ ] **Step 6: Write and run extrapolation boundary test**

Add to `TestTimeVaryingBC`:

```python
    def test_acu_supply_table_capacity_extrapolation(self, tmp_path):
        """Table endpoints should use constant extrapolation (first/last Q values)."""
        perf_table = [
            {"returnTemp": 28, "capacity": 25},
            {"returnTemp": 33, "capacity": 32},
        ]
        bc_data = {
            "ACU_1_supply": {
                "bc_type": "acu_supply",
                "temperature": 15.0,
                "flow_rate": 4.0,
                "coolant_temp": 14.0,
                "set_point_temp": 22.0,
                "return_patch_name": "ACU_1_return",
                "cooling_model_mode": "tableCapacity",
                "cooling_performance_table": perf_table,
            }
        }
        sim_config = {"solverType": "transient"}
        fg = _make_field_gen(tmp_path, bc_data, simulation_config=sim_config)
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        fg._write_t_field(t_path, [("ACU_1_supply", "wall")])
        t_text = t_path.read_text()
        # First point: 28°C = 301.15K, 25kW = 25000W → constant below
        assert "301.15" in t_text
        assert "25000" in t_text
        # Last point: 33°C = 306.15K, 32kW = 32000W → constant above
        assert "306.15" in t_text
        assert "32000" in t_text
```

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC::test_acu_supply_table_capacity_extrapolation -xvs`
Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -x --tb=short`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/tests/test_template_params.py
git commit -m "feat(fields): tableCapacity piecewise expression BC for ACU supply temperature"
```

---

### Task 6: Frontend — i18n Keys

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add Chinese translations**

Before the closing `}` in `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`, add:

```json
  "label.cooling_model": "制冷模型",
  "option.fixed_temp": "固定送风温度",
  "option.linear_capacity": "线性制冷模型",
  "option.table_capacity": "性能曲线表格",
  "label.rated_return_temp": "额定回风温度 (°C)",
  "label.rated_cooling": "额定制冷量 (kW)",
  "hint.requires_transient": "需切换为瞬态模式",
  "label.perf_table_return": "回风温度 (°C)",
  "label.perf_table_capacity": "制冷量 (kW)"
```

- [ ] **Step 2: Add English translations**

Before the closing `}` in `STLGeneratorForOpenFoam/web/i18n/en-US.json`, add:

```json
  "label.cooling_model": "Cooling Model",
  "option.fixed_temp": "Fixed Supply Temp",
  "option.linear_capacity": "Linear Capacity",
  "option.table_capacity": "Performance Table",
  "label.rated_return_temp": "Rated Return Temp (°C)",
  "label.rated_cooling": "Rated Cooling (kW)",
  "hint.requires_transient": "Requires transient mode",
  "label.perf_table_return": "Return Temp (°C)",
  "label.perf_table_capacity": "Capacity (kW)"
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat(i18n): add cooling model translations"
cd ..
```

---

### Task 7: Frontend — Cooling Model Dropdown and Conditional Panels

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (two locations: ~line 9994 and ~line 11606)

This task modifies the ACU supply face property panel in both locations (multi-select panel and single-select panel). Both need the same cooling model dropdown and conditional parameter panels.

- [ ] **Step 1: Add `updateAcuCoolingModel` function**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, near the existing `updateAcuPatchPhysics` function (search for `window.updateAcuPatchPhysics`), add a new function:

```javascript
    window.updateAcuCoolingModel = function(modelId, mode) {
        pushUndoCheckpoint('updateAcuCoolingModel');
        var model = getModelById(modelId);
        if (!model) return;
        model.acuPerformanceMode = mode;
        updateUI();
        logOperation('updateAcuCoolingModel', 'Set ACU cooling model to ' + mode, {modelId, mode}, 'modify');
    };
```

- [ ] **Step 2: Add `updateAcuPerfTable` helper**

Right after the previous function, add:

```javascript
    window.updateAcuPerfTable = function(modelId) {
        pushUndoCheckpoint('updateAcuPerfTable');
        var model = getModelById(modelId);
        if (!model) return;
        if (!model.coolingPerformanceTable) model.coolingPerformanceTable = [
            {returnTemp: 25, capacity: 20},
            {returnTemp: 30, capacity: 30},
            {returnTemp: 35, capacity: 35}
        ];
        logOperation('updateAcuPerfTable', 'Update ACU performance table', {modelId}, 'modify');
    };

    window.addAcuPerfTableRow = function(modelId) {
        pushUndoCheckpoint('addAcuPerfTableRow');
        var model = getModelById(modelId);
        if (!model) return;
        if (!model.coolingPerformanceTable) model.coolingPerformanceTable = [];
        var last = model.coolingPerformanceTable[model.coolingPerformanceTable.length - 1];
        model.coolingPerformanceTable.push({
            returnTemp: last ? last.returnTemp + 5 : 30,
            capacity: last ? last.capacity + 5 : 30
        });
        updateUI();
        logOperation('addAcuPerfTableRow', 'Add ACU performance table row', {modelId}, 'modify');
    };

    window.removeAcuPerfTableRow = function(modelId, idx) {
        pushUndoCheckpoint('removeAcuPerfTableRow');
        var model = getModelById(modelId);
        if (!model || !model.coolingPerformanceTable) return;
        if (model.coolingPerformanceTable.length <= 2) return;
        model.coolingPerformanceTable.splice(idx, 1);
        updateUI();
        logOperation('removeAcuPerfTableRow', 'Remove ACU performance table row', {modelId, idx}, 'modify');
    };

    window.updateAcuPerfTableCell = function(modelId, idx, field, value) {
        pushUndoCheckpoint('updateAcuPerfTableCell');
        var model = getModelById(modelId);
        if (!model || !model.coolingPerformanceTable) return;
        model.coolingPerformanceTable[idx][field] = value;
        logOperation('updateAcuPerfTableCell', 'Update ACU perf table cell', {modelId, idx, field, value}, 'modify');
    };
```

- [ ] **Step 3: Create `_buildAcuCoolingModelHtml` helper**

Add a helper function that generates the cooling model dropdown and conditional panel HTML. This is called by both panel locations:

```javascript
    function _buildAcuCoolingModelHtml(model, patch, pp) {
        var mode = model.acuPerformanceMode || 'fixedTemp';
        var isTransient = (state.cfdRoom && state.cfdRoom.solverType === 'transient');
        var html = '';

        // Dropdown
        html += '<div class="prop-row"><label>' + t('label.cooling_model') + '</label>';
        html += '<select onchange="updateAcuCoolingModel(\'' + model.id + '\', this.value)">';
        html += '<option value="fixedTemp"' + (mode === 'fixedTemp' ? ' selected' : '') + '>' + t('option.fixed_temp') + '</option>';
        html += '<option value="linearCapacity"' + (mode === 'linearCapacity' ? ' selected' : '') + (!isTransient ? ' disabled title="' + t('hint.requires_transient') + '"' : '') + '>' + t('option.linear_capacity') + (!isTransient ? ' 🔒' : '') + '</option>';
        html += '<option value="tableCapacity"' + (mode === 'tableCapacity' ? ' selected' : '') + (!isTransient ? ' disabled title="' + t('hint.requires_transient') + '"' : '') + '>' + t('option.table_capacity') + (!isTransient ? ' 🔒' : '') + '</option>';
        html += '</select></div>';

        // Mode-specific parameters
        if (mode === 'fixedTemp') {
            html += '<div class="prop-row"><label>' + t('label.supply_temp') + ' (°C)</label><input type="number" step="0.5" value="' + (pp.temperature || 15) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'temperature\', parseFloat(this.value))"></div>';
        } else if (mode === 'linearCapacity') {
            html += '<div class="prop-row"><label>Set Point (°C)</label><input type="number" step="0.5" value="' + (pp.setPointTemperature != null ? pp.setPointTemperature : (pp.temperature || 22)) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'setPointTemperature\', parseFloat(this.value))"></div>';
            html += '<div class="prop-row"><label>' + t('label.rated_cooling') + '</label><input type="number" step="1" value="' + (pp.coolingCapacity != null ? pp.coolingCapacity : 35) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'coolingCapacity\', parseFloat(this.value))"></div>';
            html += '<div class="prop-row"><label>' + t('label.rated_return_temp') + '</label><input type="number" step="0.5" value="' + (pp.ratedReturnTemp != null ? pp.ratedReturnTemp : 35) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'ratedReturnTemp\', parseFloat(this.value))"></div>';
            html += '<div class="prop-row"><label>Coolant T (°C)</label><input type="number" step="0.5" value="' + (pp.coolantTemperature != null ? pp.coolantTemperature : 14) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'coolantTemperature\', parseFloat(this.value))"></div>';
        } else if (mode === 'tableCapacity') {
            html += '<div class="prop-row"><label>Set Point (°C)</label><input type="number" step="0.5" value="' + (pp.setPointTemperature != null ? pp.setPointTemperature : (pp.temperature || 22)) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'setPointTemperature\', parseFloat(this.value))"></div>';
            html += '<div class="prop-row"><label>Coolant T (°C)</label><input type="number" step="0.5" value="' + (pp.coolantTemperature != null ? pp.coolantTemperature : 14) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'coolantTemperature\', parseFloat(this.value))"></div>';
            // Performance table
            var tbl = model.coolingPerformanceTable || [
                {returnTemp: 25, capacity: 20},
                {returnTemp: 30, capacity: 30},
                {returnTemp: 35, capacity: 35}
            ];
            if (!model.coolingPerformanceTable) model.coolingPerformanceTable = tbl;
            html += '<div style="margin-top:6px;"><label style="font-size:0.8rem;color:#8be9fd;">' + t('option.table_capacity') + '</label>';
            html += '<table style="width:100%;font-size:0.8rem;border-collapse:collapse;margin-top:4px;">';
            html += '<tr><th style="text-align:left;color:#888;padding:2px 4px;">' + t('label.perf_table_return') + '</th><th style="text-align:left;color:#888;padding:2px 4px;">' + t('label.perf_table_capacity') + '</th><th></th></tr>';
            tbl.forEach(function(row, idx) {
                html += '<tr>';
                html += '<td><input type="number" step="1" value="' + row.returnTemp + '" style="width:70px;" onchange="updateAcuPerfTableCell(\'' + model.id + '\', ' + idx + ', \'returnTemp\', parseFloat(this.value))"></td>';
                html += '<td><input type="number" step="1" value="' + row.capacity + '" style="width:70px;" onchange="updateAcuPerfTableCell(\'' + model.id + '\', ' + idx + ', \'capacity\', parseFloat(this.value))"></td>';
                html += '<td><button onclick="removeAcuPerfTableRow(\'' + model.id + '\', ' + idx + ')" style="background:none;border:none;color:#e06c75;cursor:pointer;font-size:12px;" title="Remove">✕</button></td>';
                html += '</tr>';
            });
            html += '</table>';
            html += '<button onclick="addAcuPerfTableRow(\'' + model.id + '\')" style="font-size:0.75rem;background:#252540;border:1px solid #444;color:#8be9fd;padding:2px 8px;border-radius:3px;cursor:pointer;margin-top:4px;">+ Add Row</button></div>';
        }

        // Flow rate (shown for all modes)
        html += '<div class="prop-row"><label>' + t('label.volume_flow_rate') + '</label><input type="number" step="0.1" value="' + (pp.volumeFlowRate || 4) + '" onchange="updateAcuPatchPhysics(\'' + model.id + '\', \'' + patch.id + '\', \'volumeFlowRate\', parseFloat(this.value))"></div>';

        return html;
    }
```

- [ ] **Step 4: Replace the first supply panel location (~line 9994)**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find the block at ~line 9994:

```javascript
                    ${pp.role === 'supply' ? `
                    <div class="prop-row"><label>Set Point (°C)</label>...
                    <div class="prop-row"><label>Cooling (kW)</label>...
                    <div class="prop-row"><label>Coolant T (°C)</label>...
                    <div class="prop-row"><label>${t('label.volume_flow_rate')}</label>...
                    ` : ''}
```

Replace the entire `${pp.role === 'supply' ? ...}` template literal block with:

```javascript
                    ${pp.role === 'supply' ? _buildAcuCoolingModelHtml(model, patch, pp) : ''}
```

- [ ] **Step 5: Replace the second supply panel location (~line 11606)**

Find the identical block at ~line 11606 and replace similarly:

```javascript
                    ${pp.role === 'supply' ? _buildAcuCoolingModelHtml(model, patch, pp) : ''}
```

- [ ] **Step 6: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(ui): cooling model dropdown with conditional parameter panels"
cd ..
```

---

### Task 8: Frontend — cfdrun Export with coolingModel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (~line 30569-30573)

- [ ] **Step 1: Update cfdrun export to include coolingModel**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find the supply face export section (~line 30569-30573):

```javascript
                    if (pp[p.id].coolingCapacity != null) phys.coolingCapacity = pp[p.id].coolingCapacity;
                    if (pp[p.id].setPointTemperature != null) phys.setPointTemperature = pp[p.id].setPointTemperature;
                    if (pp[p.id].coolantTemperature != null) phys.coolantTemperature = pp[p.id].coolantTemperature;
```

Replace with:

```javascript
                    if (pp[p.id].coolingCapacity != null) phys.coolingCapacity = pp[p.id].coolingCapacity;
                    if (pp[p.id].setPointTemperature != null) phys.setPointTemperature = pp[p.id].setPointTemperature;
                    if (pp[p.id].coolantTemperature != null) phys.coolantTemperature = pp[p.id].coolantTemperature;
                    if (pp[p.id].ratedReturnTemp != null) phys.ratedReturnTemp = pp[p.id].ratedReturnTemp;
                    var acuMode = model.acuPerformanceMode || 'fixedTemp';
                    if (acuMode !== 'fixedTemp') {
                        var cm = {mode: acuMode};
                        if (acuMode === 'linearCapacity') {
                            cm.ratedReturnTemp = pp[p.id].ratedReturnTemp || 35;
                        } else if (acuMode === 'tableCapacity') {
                            cm.performanceTable = model.coolingPerformanceTable || [
                                {returnTemp: 25, capacity: 20},
                                {returnTemp: 30, capacity: 30},
                                {returnTemp: 35, capacity: 35}
                            ];
                        }
                        phys.coolingModel = cm;
                    }
```

- [ ] **Step 2: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(export): include coolingModel in cfdrun ACU supply physics"
cd ..
```

---

### Task 9: Submodule Update and Full Commit

**Files:**
- Update submodule pointer in parent repo

- [ ] **Step 1: Update submodule pointer**

```bash
git add STLGeneratorForOpenFoam
git commit -m "chore: update STLGeneratorForOpenFoam submodule (ACU cooling model UI)"
```

- [ ] **Step 2: Run full test suite one final time**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -x --tb=short`
Expected: All tests pass (should be 79 tests: original 72 + 7 new)

---

### Task 10: End-to-End Validation via browser-use on Slab

**Files:** None (testing only)

This task must be done using `browser-use` to interact with the real UI, per the debugging rules in CLAUDE.md.

- [ ] **Step 1: Start server**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --port 8005 --ws-port 8006 --case-dir case/Slab &
```

- [ ] **Step 2: Open browser and load Slab project**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# Login
curl -s -c /tmp/stlgen_cookie.txt -X POST http://localhost:8005/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"WIN#@desk"}'
# Load project
browser-use eval "window.loadProject && window.loadProject('V5_CFD_Station_1773892919284.cfdproj')"
browser-use screenshot /tmp/slab_loaded.png
```

- [ ] **Step 3: Select ACU and configure linearCapacity**

Use `browser-use state` to find the ACU in the model tree, click to select it. Then:

```bash
browser-use state
# Find and click the ACU model in the tree
browser-use click <acu_element_index>
browser-use screenshot /tmp/slab_acu_panel.png
```

Via `browser-use eval`, set the cooling model and parameters:

```bash
browser-use eval "var acu = state.models.find(m => m.kind === 'acu'); if(acu) { acu.acuPerformanceMode = 'linearCapacity'; updateUI(); }"
browser-use eval "var acu = state.models.find(m => m.kind === 'acu'); if(acu) { var pp = acu.cfd.patchPhysics; var supKey = Object.keys(pp).find(k => pp[k].role === 'supply'); if(supKey) { pp[supKey].setPointTemperature = 22; pp[supKey].coolingCapacity = 35; pp[supKey].ratedReturnTemp = 35; pp[supKey].coolantTemperature = 14; } updateUI(); }"
browser-use screenshot /tmp/slab_acu_linear.png
```

- [ ] **Step 4: Configure transient solver**

```bash
browser-use eval "state.cfdRoom.solverType = 'transient'; state.cfdRoom.endTime = 3; state.cfdRoom.deltaT = 0.01; state.cfdRoom.writeInterval = 1; updateUI();"
```

- [ ] **Step 5: Export, mesh, and solve**

Trigger export via the frontend buttons (use `browser-use state` to find them):

```bash
browser-use state
# Click "Export STL + cfdrun" button
browser-use click <export_button_index>
# Wait for export
# Click "Generate Mesh" button
browser-use click <mesh_button_index>
# Wait for mesh completion
# Click "Solve" button
browser-use click <solve_button_index>
```

- [ ] **Step 6: Verify generated BC file**

Read the generated `0/T` file and check for the expression BC:

```bash
cat case/Slab/Mesh/0/T | grep -A 20 "ACU\|supply"
```

Verify:
- `uniformFixedValue` type
- `expression` keyword
- `Qrated_` variable
- `Trated_` variable
- `weightAverage(T)` cross-patch reference

- [ ] **Step 7: Verify solver output**

Check the Operation Log (via browser screenshot) for:
- 0 FATAL errors
- 0 NaN values
- Solver reaches t=3s

```bash
browser-use screenshot /tmp/slab_solve_done.png
```

- [ ] **Step 8: Clean up**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
browser-use close
```
