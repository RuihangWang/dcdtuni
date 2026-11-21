# ACU Dynamic Coupled BC (Set Point Control) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static `fixedValue` temperature BC on ACU supply patches with an `exprFixedValue` Set Point control model that dynamically computes supply temperature from return air temperature, cooling capacity, and set point — matching 6Sigma's ACU thermal model. Validate by running full mesh+solve on base_leakage, CMI-China, and STDCT.

**Architecture:** Add `cooling_capacity`, `set_point_temp`, `min_supply_temp`, and `return_patch_name` fields to `BCSpec`. When these are present, `_write_t_field()` emits an `exprFixedValue` BC with chained expression variables implementing `Q_actual = clamp(Q_needed, 0, Q_max)`. When absent, the existing `fixedValue` BC is preserved for backward compatibility. The frontend ACU panel gets new inputs for Set Point, Cooling Capacity, and Min Supply Temperature.

**Tech Stack:** Python 3.10, Pydantic, Jinja2, OpenFOAM v2406 `exprFixedValue` expression BC, pytest, HTML/JS (STL_generate.html)

**Acceptance criteria:** base_leakage, CMI-China, and STDCT complete mesh+solve without divergence, with the new `exprFixedValue` BC active on all ACU supply patches.

---

### Task 1: Add new fields to BCSpec

**Files:**
- Modify: `src/backend/aircooling/runner/bridge_bc.py:44-53`
- Create: `src/backend/aircooling/runner/tests/test_acu_coupled_bc.py`

- [ ] **Step 1: Write the failing test**

Create `src/backend/aircooling/runner/tests/test_acu_coupled_bc.py`:

```python
"""Tests for ACU dynamic coupled boundary condition (Set Point control)."""
from dcdtuni.runner.bridge_bc import BCSpec


class TestBCSpecACUFields:
    """BCSpec should accept cooling_capacity, set_point_temp, min_supply_temp, return_patch_name."""

    def test_bcspec_accepts_cooling_capacity(self):
        spec = BCSpec(
            bc_type="acu_supply",
            temperature=22.0,
            flow_rate=2.69,
            cooling_capacity=35000.0,
            set_point_temp=22.0,
            min_supply_temp=20.3,
            return_patch_name="ACU_A1_Front_patch",
        )
        assert spec.cooling_capacity == 35000.0
        assert spec.set_point_temp == 22.0
        assert spec.min_supply_temp == 20.3
        assert spec.return_patch_name == "ACU_A1_Front_patch"

    def test_bcspec_acu_fields_default_none(self):
        spec = BCSpec(bc_type="acu_supply", temperature=15.0, flow_rate=4.0)
        assert spec.cooling_capacity is None
        assert spec.set_point_temp is None
        assert spec.min_supply_temp is None
        assert spec.return_patch_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_acu_coupled_bc.py -x -v`

- [ ] **Step 3: Add fields to BCSpec**

In `src/backend/aircooling/runner/bridge_bc.py`, add four fields after `physical_flow_area`:

```python
    cooling_capacity: Optional[float] = None      # W (max sensible cooling)
    min_supply_temp: Optional[float] = None        # Celsius
    set_point_temp: Optional[float] = None         # Celsius (supply air set point)
    return_patch_name: Optional[str] = None        # paired return patch for exprFixedValue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/test_acu_coupled_bc.py -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/bridge_bc.py src/backend/aircooling/runner/tests/test_acu_coupled_bc.py
git commit -m "feat(bridge_bc): add cooling_capacity, set_point_temp, min_supply_temp, return_patch_name to BCSpec"
```

---

### Task 2: Add schema fields to ACUFacePhysics

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py:149-152`
- Test: `src/backend/aircooling/runner/tests/test_acu_coupled_bc.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `src/backend/aircooling/runner/tests/test_acu_coupled_bc.py`:

```python
from dcdtuni.runner.cfdproj_schema import ACUFacePhysics


class TestACUFacePhysicsSchema:
    def test_accepts_cooling_fields(self):
        phys = ACUFacePhysics(
            temperature=22.0,
            volumeFlowRate=2.69,
            coolingCapacity=35.0,
            minSupplyTemperature=20.3,
            setPointTemperature=22.0,
        )
        assert phys.coolingCapacity == 35.0
        assert phys.minSupplyTemperature == 20.3
        assert phys.setPointTemperature == 22.0

    def test_cooling_fields_default_none(self):
        phys = ACUFacePhysics(temperature=15.0, volumeFlowRate=4.0)
        assert phys.coolingCapacity is None
        assert phys.minSupplyTemperature is None
        assert phys.setPointTemperature is None
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add fields to ACUFacePhysics**

In `src/backend/aircooling/runner/cfdproj_schema.py`:

```python
class ACUFacePhysics(CFDBaseModel):
    temperature: Optional[float] = None
    volumeFlowRate: float = 4.0
    velocity: float = 2.0
    coolingCapacity: Optional[float] = None        # kW
    minSupplyTemperature: Optional[float] = None   # Celsius
    setPointTemperature: Optional[float] = None    # Celsius
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

---

### Task 3: Pass ACU cooling params through _build_bridge_bc()

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:2975-2991`
- Test: `src/backend/aircooling/runner/tests/test_acu_coupled_bc.py` (append)

- [ ] **Step 1: Write test**

Append to test file — test that BCSpec gets cooling_capacity, set_point_temp, min_supply_temp, and return_patch_name when registering ACU supply faces.

- [ ] **Step 2: Modify _build_bridge_bc()**

In `src/backend/aircooling/runner/stlgen_runner.py`, replace the ACU supply/return block (lines ~2975-2991):

```python
        # ACU supply / return
        for acu_name, acu in data.get("acus", {}).items():
            return_patch = None
            rf_list = acu.get("returnFaces", [])
            if rf_list:
                return_patch = _sanitize(rf_list[0]["solidName"])

            for sf in acu.get("supplyFaces", []):
                solid = _sanitize(sf["solidName"])
                phys = sf.get("physics", {})
                cooling_kw = phys.get("coolingCapacity")
                registry.register(solid, BCSpec(
                    bc_type="acu_supply",
                    temperature=phys.get("temperature", 15.0),
                    flow_rate=phys.get("volumeFlowRate", 4.0),
                    cooling_capacity=cooling_kw * 1000 if cooling_kw else None,
                    set_point_temp=phys.get("setPointTemperature"),
                    min_supply_temp=phys.get("minSupplyTemperature"),
                    return_patch_name=return_patch,
                ))
            for rf in rf_list:
                solid = _sanitize(rf["solidName"])
                phys = rf.get("physics", {})
                registry.register(solid, BCSpec(
                    bc_type="acu_return",
                    flow_rate=phys.get("volumeFlowRate", 4.0),
                ))
```

- [ ] **Step 3: Run full test suite**

- [ ] **Step 4: Commit**

---

### Task 4: Generate exprFixedValue BC in _write_t_field()

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py:675-683`
- Test: `src/backend/aircooling/runner/tests/test_acu_coupled_bc.py` (append)

- [ ] **Step 1: Write failing tests**

Test that when `cooling_capacity` and `return_patch_name` are present in bc_info, `_write_t_field()` emits `exprFixedValue` with Set Point control variables (`Tsetpoint`, `Qmax`, `Qneeded`, `Qactual`, `weightAverage(T)`). Test that without `cooling_capacity`, the old `fixedValue` is emitted.

- [ ] **Step 2: Implement exprFixedValue generation**

In `src/backend/aircooling/runner/fields.py`, split the `acu_supply` case out of the combined `if bc_type in ("acu_supply", "fixedTemperature", "nonfan_fixed_temp")` block:

```python
                if bc_type == "acu_supply" and temperature is not None:
                    cooling_capacity = bc_info.get("cooling_capacity")
                    return_patch = bc_info.get("return_patch_name")
                    if cooling_capacity and return_patch:
                        flow_rate = bc_info.get("flow_rate") or 4.0
                        mdot = 1.2 * flow_rate
                        set_point = bc_info.get("set_point_temp")
                        t_setpoint_k = (set_point if set_point is not None else temperature) + 273.15
                        min_sup = bc_info.get("min_supply_temp")
                        t_min_k = (min_sup if min_sup is not None else temperature) + 273.15
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
                            f'            "Treturn{{{return_patch}}} = weightAverage(T)"',
                            '            "Qneeded = mdot * 1005.0 * (Treturn - Tsetpoint)"',
                            '            "Qactual = min(max(Qneeded, 0), Qmax)"',
                            "        );",
                            '        valueExpr       "max(Treturn - Qactual / (mdot * 1005.0), Tmin)";',
                            "    }",
                        ])
                    else:
                        temp_k = temperature + 273.15
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            fixedValue;",
                            f"        value           uniform {temp_k};",
                            "    }",
                        ])
                elif bc_type in ("fixedTemperature", "nonfan_fixed_temp") and temperature is not None:
                    temp_k = temperature + 273.15
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            fixedValue;",
                        f"        value           uniform {temp_k};",
                        "    }",
                    ])
```

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

---

### Task 5: Frontend — Add Set Point control inputs to ACU property panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

The ACU inspector panel (line ~9809) currently shows only `temperature` and `volumeFlowRate` for supply patches. Add three new fields:

- **Set Point Temperature (°C)** — `setPointTemperature` in patchPhysics (replaces/supplements `temperature` as the supply set point)
- **Cooling Capacity (kW)** — `coolingCapacity` in patchPhysics
- **Min Supply Temperature (°C)** — `minSupplyTemperature` in patchPhysics

- [ ] **Step 1: Update the supply patch section in the ACU inspector (line ~9809)**

Replace the supply-role section:
```javascript
${pp.role === 'supply' ? `
<div class="prop-row"><label>${t('label.supply_temp')}</label><input type="number" step="0.5" value="${pp.temperature || 15}" onchange="updateAcuPatchPhysics('${model.id}', '${patch.id}', 'temperature', parseFloat(this.value))"></div>
<div class="prop-row"><label>${t('label.volume_flow_rate')}</label><input type="number" step="0.1" value="${pp.volumeFlowRate || 4}" onchange="updateAcuPatchPhysics('${model.id}', '${patch.id}', 'volumeFlowRate', parseFloat(this.value))"></div>
` : ''}
```

With (keep existing fields, add 3 new ones):
```javascript
${pp.role === 'supply' ? `
<div class="prop-row"><label>Set Point (°C)</label><input type="number" step="0.5" value="${pp.setPointTemperature != null ? pp.setPointTemperature : (pp.temperature || 22)}" onchange="updateAcuPatchPhysics('${model.id}', '${patch.id}', 'setPointTemperature', parseFloat(this.value))"></div>
<div class="prop-row"><label>Cooling Capacity (kW)</label><input type="number" step="1" value="${pp.coolingCapacity != null ? pp.coolingCapacity : 35}" onchange="updateAcuPatchPhysics('${model.id}', '${patch.id}', 'coolingCapacity', parseFloat(this.value))"></div>
<div class="prop-row"><label>Min Supply T (°C)</label><input type="number" step="0.5" value="${pp.minSupplyTemperature != null ? pp.minSupplyTemperature : 15}" onchange="updateAcuPatchPhysics('${model.id}', '${patch.id}', 'minSupplyTemperature', parseFloat(this.value))"></div>
<div class="prop-row"><label>${t('label.volume_flow_rate')}</label><input type="number" step="0.1" value="${pp.volumeFlowRate || 4}" onchange="updateAcuPatchPhysics('${model.id}', '${patch.id}', 'volumeFlowRate', parseFloat(this.value))"></div>
` : ''}
```

Do the same for the SECOND occurrence of this template (line ~11420, the rack-embedded ACU panel if it exists).

- [ ] **Step 2: Update the cfdrun export to include new fields**

In the export function (line ~29602), update `supplyFaces` mapping to include the new physics fields:

```javascript
const supplyFaces = supplyPatches.map(p => ({
    solidName: patchSolidName(model, p.faceId, p.id),
    patchId: p.id,
    face: p.faceId,
    physics: {
        temperature: pp[p.id].temperature || 15.0,
        volumeFlowRate: pp[p.id].volumeFlowRate || 4.0,
        velocity: pp[p.id].velocity || 2.0,
        coolingCapacity: pp[p.id].coolingCapacity != null ? pp[p.id].coolingCapacity : undefined,
        setPointTemperature: pp[p.id].setPointTemperature != null ? pp[p.id].setPointTemperature : undefined,
        minSupplyTemperature: pp[p.id].minSupplyTemperature != null ? pp[p.id].minSupplyTemperature : undefined,
    }
}));
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(frontend): add Set Point, Cooling Capacity, Min Supply Temperature to ACU panel"
```

---

### Task 6: Update cfdproj files for base_leakage, CMI-China, STDCT

**Files:**
- Modify: `STLGeneratorForOpenFoam/projects/Base_leakage.cfdproj`
- Modify: `case/CMI-China/model/CMI_China.cfdrun`
- Modify: `case/STDCT/model/Full Panel.json` (or regenerate cfdrun from cfdproj)

Add `coolingCapacity`, `setPointTemperature`, and `minSupplyTemperature` to every ACU supply face physics.

Parameter derivation for each case:

**base_leakage** (2 ACUs, supply T=15°C, flow=4.0 m³/s):
- Small case with 2 generic ACUs. Estimate: 30 kW cooling each.
- `setPointTemperature`: 15.0 (use current supply temp as set point)
- `coolingCapacity`: 30 (kW)
- `minSupplyTemperature`: 12.0

**CMI-China** (4 ACUs, supply T=22°C, flow=4.0 m³/s supply / 3.2 m³/s return):
- Medium case. Estimate: 40 kW per ACU.
- `setPointTemperature`: 22.0
- `coolingCapacity`: 40 (kW)
- `minSupplyTemperature`: 18.0

**STDCT** (3 PCUs, supply T=21°C, flow=23 m³/s):
- Large perimeter cooling. Known from model.json: CANATAL_ME_Series_080, coolingCapacity=152 kW.
- `setPointTemperature`: 21.0
- `coolingCapacity`: 152 (kW)
- `minSupplyTemperature`: 15.0

- [ ] **Step 1: Write a Python script to patch all three case files**

The script should:
1. Read each cfdproj/cfdrun JSON
2. For each ACU's supplyFaces, add `coolingCapacity`, `setPointTemperature`, `minSupplyTemperature` to physics
3. Write back the modified JSON

- [ ] **Step 2: Run the script and verify the files are updated**

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/projects/Base_leakage.cfdproj case/CMI-China/model/CMI_China.cfdrun case/STDCT/model/*.json
git commit -m "feat(cases): add ACU Set Point control parameters to base_leakage, CMI-China, STDCT"
```

---

### Task 7: End-to-end validation — mesh+solve on 3 cases

**Files:** No code changes. Validation only.

Run full mesh generation + solve for each case and verify:
1. The generated `0/T` file contains `exprFixedValue` on ACU supply patches
2. The solver starts without errors
3. The solver converges (residuals reach 1e-3 thresholds or stabilize)

- [ ] **Step 1: Trigger base_leakage mesh+solve via API or CLI**

```bash
conda run -n dcdtuni python -m dcdtuni.runner.run_cfd --from-cfdrun case/base_leakage/model/Base_leakage.cfdrun
```

Verify: check `case/base_leakage/Mesh/0/T` contains `exprFixedValue` and `weightAverage`.

- [ ] **Step 2: Trigger CMI-China mesh+solve**

```bash
conda run -n dcdtuni python -m dcdtuni.runner.run_cfd --from-cfdrun case/CMI-China/model/CMI_China.cfdrun
```

- [ ] **Step 3: Trigger STDCT mesh+solve**

STDCT uses the old room-config pipeline (Full Panel.json), not cfdrun. Verify the correct invocation method.

- [ ] **Step 4: Monitor convergence for all 3 cases**

Check solver logs for:
- No `exprFixedValue` parsing errors
- No NaN at iteration 1
- Residuals decrease and converge or plateau at acceptable levels (< 1e-3)

- [ ] **Step 5: Report results**
