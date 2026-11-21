# Transient Time-Varying Boundary Conditions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable time-varying boundary conditions for ACU airflow/temperature and Server/Rack heat output in transient CFD simulations, with event-driven UI for scenario building.

**Architecture:** Data stored as `model.timeVarying` on each device → exported in `.cfdrun` JSON → `fields.py` generates `uniformFixedValue + table` OpenFOAM BCs. Frontend adds "⏱ 瞬态" Tab with event list + timeline, plus ⏱ inline editor in property panel.

**Tech Stack:** Python/Pydantic (schema), Python (fields.py BC generation), JavaScript/HTML (STL_generate.html frontend), OpenFOAM uniformFixedValue + table

**Design spec:** `docs/superpowers/specs/2026-04-26-transient-simulation-design.md`

**UI Mockups:** `docs/superpowers/mockups/transient-*.html`

---

### Task 1: Pydantic Schema — TimeVarying Data Types

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py`
- Modify: `src/backend/tests/test_schema_completeness.py`

- [ ] **Step 1: Write failing test for TimeVaryingEntry**

```python
# Add to test_schema_completeness.py at the end
from dcdtuni.runner.cfdproj_schema import TimeVaryingEntry

class TestTimeVaryingSchema:
    def test_time_varying_entry_roundtrip(self):
        e = TimeVaryingEntry(t=60.0, value=1.4)
        assert e.t == 60.0
        assert e.value == 1.4

    def test_time_varying_entry_from_dict(self):
        e = TimeVaryingEntry(**{"t": 0, "value": 5000})
        assert e.t == 0.0
        assert e.value == 5000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_schema_completeness.py::TestTimeVaryingSchema -v`
Expected: FAIL with `ImportError: cannot import name 'TimeVaryingEntry'`

- [ ] **Step 3: Implement TimeVaryingEntry in schema**

Add to `src/backend/aircooling/runner/cfdproj_schema.py` after the existing imports section (near line 30):

```python
class TimeVaryingEntry(CFDBaseModel):
    t: float
    value: float
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_schema_completeness.py::TestTimeVaryingSchema -v`
Expected: PASS

- [ ] **Step 5: Write failing test for timeVarying on ACU physics**

```python
# Add to TestTimeVaryingSchema in test_schema_completeness.py
from dcdtuni.runner.cfdproj_schema import ACUFacePhysics

    def test_acu_face_physics_has_time_varying(self):
        p = ACUFacePhysics(volumeFlowRate=1.4)
        assert hasattr(p, "timeVarying")
        assert p.timeVarying == {}

    def test_acu_face_physics_time_varying_roundtrip(self):
        p = ACUFacePhysics(
            volumeFlowRate=1.4,
            timeVarying={
                "airflow": [{"t": 0, "value": 1.4}, {"t": 60, "value": 0}],
                "supplyTemp": [{"t": 0, "value": 15}, {"t": 60, "value": 25}]
            }
        )
        assert len(p.timeVarying["airflow"]) == 2
        assert p.timeVarying["airflow"][0]["value"] == 1.4
```

- [ ] **Step 6: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_schema_completeness.py::TestTimeVaryingSchema::test_acu_face_physics_has_time_varying -v`
Expected: FAIL with `AssertionError`

- [ ] **Step 7: Add timeVarying field to ACUFacePhysics**

In `src/backend/aircooling/runner/cfdproj_schema.py`, find class `ACUFacePhysics` (around line 149) and add:

```python
class ACUFacePhysics(CFDBaseModel):
    temperature: Optional[float] = None
    volumeFlowRate: float = 4.0
    velocity: float = 2.0
    coolingCapacity: Optional[float] = None
    coolantTemperature: Optional[float] = None
    setPointTemperature: Optional[float] = None
    minSupplyTemperature: Optional[float] = None
    timeVarying: Dict[str, Any] = {}          # ← ADD THIS LINE
```

- [ ] **Step 8: Write failing test for heat source timeVarying**

```python
# Add to TestTimeVaryingSchema
from dcdtuni.runner.cfdproj_schema import HeatSourcePhysics

    def test_heat_source_has_time_varying(self):
        # HeatSourcePhysics or the model that holds totalPower
        # needs timeVarying for heatOutput changes
        from dcdtuni.runner.cfdproj_schema import CFDExportHeatSource
        hs = CFDExportHeatSource(totalPower=5000)
        assert hasattr(hs, "timeVarying")
        assert hs.timeVarying == {}

    def test_heat_source_time_varying_roundtrip(self):
        from dcdtuni.runner.cfdproj_schema import CFDExportHeatSource
        hs = CFDExportHeatSource(
            totalPower=5000,
            timeVarying={"heatOutput": [{"t": 0, "value": 5000}, {"t": 120, "value": 10000}]}
        )
        assert len(hs.timeVarying["heatOutput"]) == 2
```

- [ ] **Step 9: Run test to verify it fails, then add timeVarying to relevant model**

Find the heat source / rack export class in `cfdproj_schema.py` and add `timeVarying: Dict[str, Any] = {}`. Run test to pass.

- [ ] **Step 10: Run full test suite**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_schema_completeness.py src/backend/tests/test_template_params.py -v`
Expected: All pass (64+ tests)

- [ ] **Step 11: Commit**

```bash
git add src/backend/aircooling/runner/cfdproj_schema.py src/backend/tests/test_schema_completeness.py
git commit -m "feat(schema): add timeVarying fields for ACU and heat source models"
```

---

### Task 2: Backend — Time-Varying ACU Velocity BC (flowRate → uniformFixedValue + table)

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py`
- Modify: `src/backend/tests/test_template_params.py`

**Context:** Currently ACU supply uses `flowRateInletVelocity` with a constant `volumetricFlowRate` (fields.py:521-529). For time-varying, we switch to `uniformFixedValue` with a table of flow rates. Note: `flowRateInletVelocity` does NOT support Function1/table in standard OpenFOAM, so we must use `uniformFixedValue` with velocity vectors derived from flow rate / patch area, OR keep `flowRateInletVelocity` for steady and use `uniformFixedValue` for time-varying only.

**Approach:** Add a helper function `_format_time_table(entries, transform_fn=None)` that converts `[{t, value}, ...]` into OpenFOAM table format string. When `timeVarying` data exists for a parameter, use `uniformFixedValue + table` instead of the original BC type.

- [ ] **Step 1: Write failing test for _format_time_table helper**

```python
# Add to test_template_params.py
from dcdtuni.runner.fields import _format_time_table

class TestTimeVaryingBC:
    def test_format_time_table_scalar(self):
        entries = [{"t": 0, "value": 1.4}, {"t": 60, "value": 1.4}, {"t": 60.1, "value": 0}]
        result = _format_time_table(entries)
        assert "(0 1.4)" in result
        assert "(60 1.4)" in result
        assert "(60.1 0)" in result

    def test_format_time_table_with_transform(self):
        """Temperature C→K transform."""
        entries = [{"t": 0, "value": 15}, {"t": 60, "value": 25}]
        result = _format_time_table(entries, transform_fn=lambda v: v + 273.15)
        assert "288.15" in result
        assert "298.15" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement _format_time_table in fields.py**

Add near the top of `fields.py` (after imports, before class/function definitions):

```python
def _format_time_table(entries, transform_fn=None):
    """Convert [{t, value}, ...] to OpenFOAM table string.
    
    Returns: "table\\n    (\\n        (t0 v0)\\n        (t1 v1)\\n    )"
    """
    lines = ["table", "        ("]
    for e in entries:
        t = e["t"] if isinstance(e, dict) else e.t
        v = e["value"] if isinstance(e, dict) else e.value
        if transform_fn:
            v = transform_fn(v)
        lines.append(f"            ({t} {v})")
    lines.append("        )")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTimeVaryingBC -v`
Expected: PASS

- [ ] **Step 5: Write failing test for ACU supply velocity time-varying BC**

```python
    def test_acu_supply_time_varying_velocity(self, tmp_path):
        """When timeVarying airflow data exists, ACU supply should use
        uniformFixedValue + table instead of flowRateInletVelocity."""
        from dcdtuni.runner.fields import _write_u_field
        
        tv_data = [{"t": 0, "value": 1.4}, {"t": 60, "value": 1.4}, {"t": 60.1, "value": 0}]
        patches = [
            ("ACU_1_supply", {
                "bc_type": "acu_supply",
                "flow_rate": 1.4,
                "timeVarying": {"airflow": tv_data}
            })
        ]
        u_path = tmp_path / "0" / "U"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        _write_u_field(u_path, patches)
        text = u_path.read_text()
        assert "uniformFixedValue" in text
        assert "table" in text
        assert "(0 1.4)" in text
        assert "(60.1 0)" in text
        assert "flowRateInletVelocity" not in text  # replaced
```

- [ ] **Step 6: Run test to verify it fails**

Expected: FAIL (current code always writes `flowRateInletVelocity`)

- [ ] **Step 7: Modify _write_u_field in fields.py to support time-varying ACU supply**

In `fields.py`, at the `acu_supply` branch (around line 521), add a check:

```python
if bc_type == "acu_supply":
    tv = bc_info.get("timeVarying", {})
    if tv.get("airflow"):
        # Time-varying: use uniformFixedValue + table
        table_str = _format_time_table(tv["airflow"])
        lines.extend([
            f"    {patch_name}",
            "    {",
            "        type            uniformFixedValue;",
            f"        uniformValue    {table_str};",
            "        value           uniform (0 0 0);",
            "    }",
        ])
    else:
        # Steady: original flowRateInletVelocity
        lines.extend([
            f"    {patch_name}",
            "    {",
            "        type            flowRateInletVelocity;",
            f"        volumetricFlowRate {flow_rate};",
            "        value           uniform (0 0 0);",
            "    }",
        ])
```

**Important:** Repeat similar pattern for `acu_return` (line 530).

- [ ] **Step 8: Run test to verify it passes**

- [ ] **Step 9: Write test for ACU return time-varying**

Similar test for return patch with `flowRateOutletVelocity` → `uniformFixedValue + table`.

- [ ] **Step 10: Implement ACU return time-varying, run tests**

- [ ] **Step 11: Run full test suite, commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/tests/test_template_params.py
git commit -m "feat(fields): time-varying ACU velocity BC with uniformFixedValue + table"
```

---

### Task 3: Backend — Time-Varying ACU Temperature BC

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py`
- Modify: `src/backend/tests/test_template_params.py`

**Context:** ACU supply temperature BC is at fields.py:725-734 (steady: `fixedValue uniform {temp_k}`). For time-varying, use `uniformFixedValue + table` with C→K transform.

- [ ] **Step 1: Write failing test**

```python
    def test_acu_supply_time_varying_temperature(self, tmp_path):
        """When timeVarying supplyTemp exists, use uniformFixedValue + table with K."""
        from dcdtuni.runner.fields import _write_t_field
        
        tv_data = [{"t": 0, "value": 15}, {"t": 60, "value": 15}, {"t": 60.1, "value": 25}]
        patches = [
            ("ACU_1_supply", {
                "bc_type": "acu_supply",
                "temperature": 15,
                "timeVarying": {"supplyTemp": tv_data}
            })
        ]
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        _write_t_field(t_path, patches)
        text = t_path.read_text()
        assert "uniformFixedValue" in text
        assert "table" in text
        assert "288.15" in text     # 15°C + 273.15
        assert "298.15" in text     # 25°C + 273.15
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Modify _write_t_field ACU supply branch**

In `fields.py`, at the `acu_supply` temperature branch (around line 725), add:

```python
if bc_type == "acu_supply" and temperature is not None:
    tv = bc_info.get("timeVarying", {})
    if tv.get("supplyTemp"):
        table_str = _format_time_table(tv["supplyTemp"], transform_fn=lambda v: v + 273.15)
        lines.extend([
            f"    {patch_name}",
            "    {",
            "        type            uniformFixedValue;",
            f"        uniformValue    {table_str};",
            f"        value           uniform {temperature + 273.15};",
            "    }",
        ])
    else:
        # existing fixedValue code
        ...
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Run full test suite, commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/tests/test_template_params.py
git commit -m "feat(fields): time-varying ACU temperature BC with C→K transform"
```

---

### Task 4: Backend — Time-Varying Heat Source BC

**Files:**
- Modify: `src/backend/aircooling/runner/fields.py`
- Modify: `src/backend/tests/test_template_params.py`

**Context:** Heat sources currently use `externalWallHeatFluxTemperature` with `mode flux; q uniform {heat_flux}` (fields.py:756-767). The `q` parameter supports `uniform` or `nonuniform` but NOT `table` Function1 directly in this BC type. For time-varying heat flux, we use `uniformFixedValue` on the `q` sub-field, or switch to `codedFixedValue`. 

**Alternative approach:** Since the current implementation computes `heat_flux = totalPower / surfaceArea` and writes it as a constant, the simplest time-varying approach is to make `q` use `table` syntax. OpenFOAM v2406's `externalWallHeatFluxTemperature` supports `q` as a `Function1` (including `table`).

- [ ] **Step 1: Write failing test**

```python
    def test_heat_source_time_varying_flux(self, tmp_path):
        """Heat source with timeVarying heatOutput should use q as table."""
        from dcdtuni.runner.fields import _write_t_field
        
        tv_data = [{"t": 0, "value": 5000}, {"t": 120, "value": 5000}, {"t": 150, "value": 10000}]
        surface_area = 2.0  # m²
        patches = [
            ("R3_wall", {
                "bc_type": "heat_source_flux",
                "heat_flux": 2500,  # 5000W / 2m²
                "surface_area": surface_area,
                "timeVarying": {"heatOutput": tv_data}
            })
        ]
        t_path = tmp_path / "0" / "T"
        t_path.parent.mkdir(parents=True, exist_ok=True)
        _write_t_field(t_path, patches)
        text = t_path.read_text()
        assert "externalWallHeatFluxTemperature" in text
        assert "table" in text
        # 5000W / 2m² = 2500 W/m²
        assert "2500" in text
        # 10000W / 2m² = 5000 W/m²
        assert "5000" in text
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Modify heat_source_flux branch**

```python
elif bc_type == "heat_source_flux":
    heat_flux = bc_info.get("heat_flux") or 0.0
    tv = bc_info.get("timeVarying", {})
    if tv.get("heatOutput"):
        surface_area = bc_info.get("surface_area", 1.0)
        table_str = _format_time_table(
            tv["heatOutput"],
            transform_fn=lambda w: w / surface_area
        )
        lines.extend([
            f"    {patch_name}",
            "    {",
            "        type            externalWallHeatFluxTemperature;",
            "        mode            flux;",
            f"        q               {table_str};",
            "        kappaMethod     fluidThermo;",
            f"        value           uniform {init_temperature};",
            "    }",
        ])
    else:
        lines.extend([
            f"    {patch_name}",
            "    {",
            "        type            externalWallHeatFluxTemperature;",
            "        mode            flux;",
            f"        q               uniform {heat_flux};",
            "        kappaMethod     fluidThermo;",
            f"        value           uniform {init_temperature};",
            "    }",
        ])
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Write test for fan-server outlet time-varying power**

Fan-server outlets use expression BC (fields.py:768-867). The power parameter in the expression needs to become time-varying. This is more complex — the expression already references `power` as a variable. For time-varying, we can make the power variable a `table` Function1 within the expression.

Test and implement this variant.

- [ ] **Step 6: Run full test suite, commit**

```bash
git add src/backend/aircooling/runner/fields.py src/backend/tests/test_template_params.py
git commit -m "feat(fields): time-varying heat source flux BC with table Function1"
```

---

### Task 5: Backend — Wire timeVarying through stlgen_runner

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`

**Context:** stlgen_runner reads ACU data from .cfdrun at line 3077-3107 and registers BCSpec objects. Need to pass `timeVarying` data through to fields.py.

- [ ] **Step 1: Modify ACU extraction to pass timeVarying**

In `stlgen_runner.py`, where ACU supply faces are processed (around line 3083-3100), add `timeVarying` to the bc_info dict:

```python
# When building the BCSpec for acu_supply:
bc_info = {
    "bc_type": "acu_supply",
    "flow_rate": physics.get("volumeFlowRate", 4.0),
    "temperature": physics.get("temperature"),
    # ... existing fields ...
    "timeVarying": physics.get("timeVarying", {}),  # ← ADD
}
```

- [ ] **Step 2: Modify heat source extraction to pass timeVarying**

Similarly for heat sources (around line 3668-3680):

```python
bc_info = {
    "bc_type": "heat_source_flux",
    "heat_flux": q,
    "surface_area": area,  # ← ADD (needed for W→W/m² conversion)
    "timeVarying": hs.get("timeVarying", {}),  # ← ADD
}
```

- [ ] **Step 3: Run full test suite**

- [ ] **Step 4: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(runner): pass timeVarying data through to field generation"
```

---

### Task 6: Frontend — model.timeVarying Data Structure + Export

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

**Context:** Need to: (1) ensure `model.timeVarying` survives state serialization, (2) export it in `buildCfdrunJson()`.

- [ ] **Step 1: Add timeVarying to normalizeState (if needed)**

Check `normalizeState()` function — ensure it preserves `model.timeVarying` through state load/save cycles. Since it's a plain dict of arrays, JSON serialization should work automatically. Verify by checking `normalizeState` doesn't strip unknown fields.

- [ ] **Step 2: Add timeVarying to buildCfdrunJson ACU export**

In `buildCfdrunJson()` around line 30067-30103, where ACU supplyFaces physics are exported, include timeVarying:

```javascript
// In the supplyFaces physics export:
physics: {
    temperature: face.physics.temperature,
    volumeFlowRate: face.physics.volumeFlowRate,
    // ... existing fields ...
    timeVarying: face.physics.timeVarying || model.timeVarying || {}
}
```

Note: The spec says timeVarying lives on the model, but for ACU the physics are per-face. Since all supply faces of one ACU typically share the same time-varying behavior, store `timeVarying` on the model and export it to each face's physics.

- [ ] **Step 3: Add timeVarying to buildCfdrunJson heat source export**

Around line 30166-30181:

```javascript
cfdData.heatSources[name] = {
    // ... existing fields ...
    timeVarying: model.timeVarying || {}
};
```

- [ ] **Step 4: Commit submodule**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(export): include timeVarying data in cfdrun JSON export"
```

---

### Task 7: Frontend — ⏱ Inline Time-Varying Editor in Property Panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

**Context:** Add ⏱ button next to ACU airflow/supplyTemp and Rack power fields in the right-side property inspector. Clicking expands an inline time-value table editor.

- [ ] **Step 1: Add ⏱ button HTML next to ACU airflow input**

Find the ACU property panel section in the HTML. Add a ⏱ button after the airflow input field. Use `onclick="window.toggleTimeVaryingEditor(modelId, 'airflow')"`.

- [ ] **Step 2: Implement window.toggleTimeVaryingEditor function**

```javascript
window.toggleTimeVaryingEditor = function(modelId, paramName) {
    pushUndoCheckpoint('toggleTimeVaryingEditor');
    var model = getModelById(modelId);
    if (!model) return;
    var editorId = 'tv-editor-' + modelId + '-' + paramName;
    var existing = document.getElementById(editorId);
    if (existing) {
        existing.remove();
        return;
    }
    // Create inline editor
    if (!model.timeVarying) model.timeVarying = {};
    var entries = model.timeVarying[paramName] || [];
    _renderTimeVaryingEditor(modelId, paramName, entries, editorId);
    logOperation('toggleTimeVaryingEditor', 'Toggle time-varying editor', {modelId, paramName}, 'modify');
};
```

- [ ] **Step 3: Implement _renderTimeVaryingEditor**

Creates a table with time/value columns, add/delete row buttons, and a "clear" button. Each row is editable. Changes write directly to `model.timeVarying[paramName]`.

- [ ] **Step 4: Implement _updateTimeVaryingButton**

Updates the ⏱ button appearance: gray when no data, highlighted `⏱✓` when timeVarying data exists.

- [ ] **Step 5: Add i18n keys**

```json
// zh-CN.json
"label.time_varying": "时变设置",
"btn.add_tv_row": "+ 添加行",
"btn.clear_tv": "清除",
"hint.tv_time": "时间(s)",
"hint.tv_value": "值",

// en-US.json
"label.time_varying": "Time-Varying",
"btn.add_tv_row": "+ Add Row",
"btn.clear_tv": "Clear",
"hint.tv_time": "Time(s)",
"hint.tv_value": "Value",
```

- [ ] **Step 6: Commit submodule**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat(ui): inline time-varying editor with ⏱ button in property panel"
```

---

### Task 8: Frontend — ⏱ 瞬态场景 Tab (Event List + Timeline)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

**Context:** New left-panel tab "⏱ 瞬态" between solver and postprocess tabs. Contains: event list (aggregated from all models' timeVarying data), timeline gantt preview, "执行瞬态求解" button.

- [ ] **Step 1: Add tab button in HTML**

After the solver tab button (around line 115), add:

```html
<button id="tab-btn-transient" class="left-tab-btn" onclick="window.switchLeftTab('transient')" style="display:none;" data-i18n="nav.transient">⏱ 瞬态</button>
```

- [ ] **Step 2: Add panel container**

Add `transient-settings-panel` div (initially hidden) with the event list and timeline areas.

- [ ] **Step 3: Update switchLeftTab and _applyLeftTabUI**

Add `'transient'` case to both functions, showing/hiding `transient-settings-panel`.

- [ ] **Step 4: Implement _toggleTransientTab visibility**

Show/hide the transient tab button based on `state.cfdRoom.solverType === 'transient'`. Call from `_toggleTransientParams()`.

- [ ] **Step 5: Implement _buildTransientEventList**

Aggregates all models' `timeVarying` data into a sorted event list for display:

```javascript
window._buildTransientEventList = function() {
    var events = [];
    state.models.forEach(function(model) {
        if (!model.timeVarying) return;
        Object.keys(model.timeVarying).forEach(function(param) {
            var entries = model.timeVarying[param];
            if (!entries || !entries.length) return;
            // Find step changes in the table
            for (var i = 1; i < entries.length; i++) {
                if (entries[i].value !== entries[i-1].value) {
                    events.push({
                        t: entries[i-1].t,
                        modelId: model.id,
                        modelName: model.name,
                        param: param,
                        fromValue: entries[i-1].value,
                        toValue: entries[i].value,
                        duration: entries[i].t - entries[i-1].t
                    });
                }
            }
        });
    });
    events.sort(function(a, b) { return a.t - b.t; });
    return events;
};
```

- [ ] **Step 6: Implement _renderTransientTimeline**

Canvas-based gantt chart showing each device with timeVarying data as a horizontal bar, color-coded by state.

- [ ] **Step 7: Implement "添加事件" dialog**

Modal dialog with: device selector, event type, trigger time, target value, transition mode (step/ramp), ramp duration.

- [ ] **Step 8: Implement "执行瞬态求解" button**

Checks for steady-state results, prompts if missing, then calls `runSolveOnly()` with transient config.

- [ ] **Step 9: Add all i18n keys**

```json
// zh-CN.json
"nav.transient": "⏱ 瞬态",
"label.transient_events": "瞬态事件",
"label.transient_timeline": "时间线预览",
"btn.add_event": "+ 添加事件",
"btn.run_transient": "▶ 执行瞬态求解",
"hint.transient_init": "将自动检测定常结果作为初始场",
"dialog.add_event_title": "添加瞬态事件",
"label.target_device": "目标设备",
"label.event_type": "事件类型",
"label.trigger_time": "触发时间 (s)",
"label.target_value": "目标值",
"label.transition_mode": "过渡方式",
"option.step_change": "阶跃 (瞬时)",
"option.ramp_change": "爬坡",
"label.ramp_duration": "爬坡持续时间 (s)",
"event.shutdown": "关机",
"event.airflow_change": "风量变化",
"event.temp_change": "供冷温度变化",
"event.power_change": "功耗变化",
"hint.no_steady_result": "未检测到定常结果，是否先运行定常求解？",

// en-US.json (corresponding English translations)
```

- [ ] **Step 10: Commit submodule**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat(ui): transient scenario tab with event list, timeline, and add-event dialog"
```

---

### Task 9: Integration Test — End-to-End Transient with Time-Varying BCs

**Files:**
- Modify: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Write integration test**

```python
class TestTransientTimeVaryingIntegration:
    def test_acu_shutdown_scenario(self, tmp_path):
        """Simulate ACU shutdown: airflow 1.4→0 at t=60s, supplyTemp stays."""
        from dcdtuni.runner.fields import _write_u_field, _write_t_field
        
        tv = {
            "airflow": [{"t": 0, "value": 1.4}, {"t": 60, "value": 1.4}, {"t": 60.1, "value": 0}],
            "supplyTemp": [{"t": 0, "value": 15}, {"t": 60, "value": 15}, {"t": 60.1, "value": 15}]
        }
        supply_patch = ("ACU_1_supply", {
            "bc_type": "acu_supply", "flow_rate": 1.4, "temperature": 15,
            "timeVarying": tv
        })
        return_patch = ("ACU_1_return", {
            "bc_type": "acu_return", "flow_rate": 1.4,
            "timeVarying": tv
        })
        
        u_path = tmp_path / "0" / "U"
        t_path = tmp_path / "0" / "T"
        u_path.parent.mkdir(parents=True, exist_ok=True)
        
        _write_u_field(u_path, [supply_patch, return_patch])
        _write_t_field(t_path, [supply_patch])
        
        u_text = u_path.read_text()
        t_text = t_path.read_text()
        
        # U field: uniformFixedValue + table for airflow
        assert "uniformFixedValue" in u_text
        assert "(60.1 0)" in u_text
        
        # T field: uniformFixedValue + table for temperature (in K)
        assert "uniformFixedValue" in t_text
        assert "288.15" in t_text  # 15°C in K
```

- [ ] **Step 2: Run test to verify it passes (should pass if Tasks 2-3 done)**

- [ ] **Step 3: Run full test suite, commit**

```bash
git add src/backend/tests/test_template_params.py
git commit -m "test: end-to-end integration test for ACU shutdown time-varying scenario"
```

---

### Task 10: Browser-Use Verification — Slab Case Transient with Time-Varying ACU

**Files:** None (verification only)

- [ ] **Step 1: Restart server**

```bash
fuser -k 8005/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --port 8005 --ws-port 8006 --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case &
```

- [ ] **Step 2: Load Slab.cfdproj via browser-use**

- [ ] **Step 3: Add timeVarying data to an ACU model via JS eval**

```javascript
// Find an ACU model and add timeVarying
var acuModel = state.models.find(m => m.name.startsWith('PCU'));
acuModel.timeVarying = {
    airflow: [{t:0, value:1.4}, {t:60, value:1.4}, {t:60.1, value:0}]
};
```

- [ ] **Step 4: Switch to transient, configure, export, solve**

- [ ] **Step 5: Verify controlDict and U/T boundary conditions**

Check the generated `0/U` and `0/T` files contain `uniformFixedValue` + `table` for the ACU patches.

- [ ] **Step 6: Verify solve completes without errors**

- [ ] **Step 7: Screenshot monitoring charts**

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Section 1 (scenarios): Covered by Tasks 2-4 (ACU velocity, ACU temp, heat source)
- ✅ Section 2 (UI): Covered by Tasks 7-8 (property panel editor, transient tab)
- ✅ Section 3 (data structure): Covered by Tasks 1, 6 (schema, frontend state + export)
- ✅ Section 4 (backend): Covered by Tasks 2-5 (fields.py + runner wiring)
- ✅ Section 5 (already implemented): No action needed
- ✅ Section 6 (OpenFOAM): uniformFixedValue + table covered in Tasks 2-4
- ✅ Section 7 (constraints): Respected throughout

**Placeholder scan:** No TBD/TODO found. Task 4 Step 5 (fan-server time-varying) is flagged as complex — the task acknowledges this and asks to test+implement.

**Type consistency:** `timeVarying` dict format `{param: [{t, value}, ...]}` is consistent across schema (Task 1), fields.py (Tasks 2-4), runner (Task 5), frontend (Tasks 6-8), and tests (Task 9).
