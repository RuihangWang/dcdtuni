# Design: DCTwin Transient CFD (PIMPLE) Support

**Date:** 2026-04-25
**Scope:** Core transient solving — templates, dispatch, schema, frontend UI
**Not in scope:** Event timeline, time-dependent BCs, co-simulation, steady→transient auto-chaining

---

## Goal

Enable DCTwin to run transient CFD simulations using `buoyantBoussinesqPimpleFoam` with proper PIMPLE configuration, user-configurable time discretization, and a frontend panel that expands when the user selects "非定常" mode.

## Architecture

Four layers of change, bottom-up:

1. **OpenFOAM Jinja2 templates** — convert static transient files to `.j2`, add missing parameters
2. **Python generation logic** — add solver_type dispatch to `generate_fv_schemes()` and `generate_fv_solution()`
3. **Pydantic schema** — add transient-specific fields to `SimulationConfig`
4. **Frontend UI** — conditional panel expansion with transient parameters + i18n

---

## 1. OpenFOAM Templates

### 1.1 transient/fvSchemes.j2 (convert from static file)

Key difference from steady: `ddtSchemes` is user-configurable.

```jinja2
ddtSchemes
{
{% if ddt_scheme == "CrankNicolson" %}
    default         CrankNicolson {{ cn_coeff }};
{% elif ddt_scheme == "backward" %}
    default         backward;
{% else %}
    default         Euler;
{% endif %}
}
```

Supported time discretization schemes:

| Scheme | Order | Params | Notes |
|--------|-------|--------|-------|
| `Euler` | 1st | none | Unconditionally stable, safest default |
| `backward` | 2nd | none | Unconditionally stable, needs 2 prior time steps |
| `CrankNicolson` | 2nd | coeff (0~1) | 0=pure Euler, 1=pure CN, recommend 0.9 |

Spatial schemes (gradSchemes, divSchemes, laplacianSchemes) use the same user-configurable structure as steady, passed via `schemes_config`. The template receives the same Jinja2 variables as the steady version plus `ddt_scheme` and `cn_coeff`.

### 1.2 transient/fvSolution.j2 (convert from static file)

PIMPLE dict with template variables:

```jinja2
PIMPLE
{
    momentumPredictor   no;
    nOuterCorrectors    {{ n_outer_correctors }};
    nCorrectors         {{ n_correctors }};
    nNonOrthogonalCorrectors {{ n_non_ortho }};
    pRefCell            0;
    pRefValue           0;
}
```

Solver entries: PCG+DIC for p_rgh (tighter tolerance 1e-8), PBiCGStab+DILU for U/T/k/epsilon.

Relaxation factor strategy:
- `nOuterCorrectors == 1` (PISO mode): no relaxation factors block
- `nOuterCorrectors > 1` (PIMPLE mode): write relaxation factors (same structure as steady)

Final-iteration solvers (`p_rghFinal`, `UFinal`, etc.) always present with `relTol 0`.

### 1.3 transient/controlDict.j2 (existing, add maxDeltaT)

Add one template variable:

```jinja2
maxDeltaT   {{ max_delta_t }};
```

Already has: `adjustTimeStep yes`, `maxCo {{ max_co }}`, `writeControl adjustableRunTime`.

### 1.4 fvOptions

Reuse steady's `fvOptions.j2` — porous sources and heat sources are solver-independent. The `generate_fv_options()` function does not need a solver_type parameter.

---

## 2. Python Generation Logic (openfoam_case.py)

### 2.1 generate_fv_schemes()

Current signature: `generate_fv_schemes(system_dir, schemes_config=None)`

New signature: `generate_fv_schemes(system_dir, schemes_config=None, solver_type="steady")`

```python
def generate_fv_schemes(system_dir, schemes_config=None, solver_type="steady"):
    if solver_type == "transient":
        template_path = "foam/template/system/transient/fvSchemes.j2"
    else:
        template_path = "foam/template/system/steady/fvSchemes.j2"
    
    # Extract ddt scheme params from schemes_config
    ddt_scheme = (schemes_config or {}).get("ddtScheme", "Euler")
    cn_coeff = float((schemes_config or {}).get("crankNicolsonCoeff", 0.9))
    
    # Pass to template along with existing spatial scheme variables
    template.render(..., ddt_scheme=ddt_scheme, cn_coeff=cn_coeff)
```

### 2.2 generate_fv_solution()

Current signature: `generate_fv_solution(system_dir, residual_control=None, relaxation_factors=None, ...)`

New signature: add `solver_type="steady"`, plus PIMPLE params:

```python
def generate_fv_solution(system_dir, ..., solver_type="steady",
                         n_outer_correctors=1, n_correctors=2, n_non_ortho=1):
    if solver_type == "transient":
        template_path = "foam/template/system/transient/fvSolution.j2"
    else:
        template_path = "foam/template/system/steady/fvSolution_cpu.j2"
```

### 2.3 generate_control_dict()

Add extraction of transient-specific params:

```python
if solver_type == "transient":
    end_time = float(_cfg_value(sc, "end_time_seconds", "endTimeSeconds", 300.0) or 300.0)
    delta_t = float(_cfg_value(sc, "delta_t", "deltaT", 0.1) or 0.1)
    write_interval = float(_cfg_value(sc, "write_interval_seconds", "writeIntervalSeconds", 10.0) or 10.0)
    max_delta_t = float(_cfg_value(sc, "max_delta_t", "maxDeltaT", 1.0) or 1.0)
    max_co = float(_cfg_value(sc, "max_co", "maxCo", 1.0) or 1.0)
else:
    end_time = int(...)  # existing integer logic
    delta_t = 1
    write_interval = int(...)
```

Remove the WARNING log at lines 145-149 (the one saying "Full PIMPLE support is not yet implemented").

### 2.4 Caller: _setup_solver_dicts() in stlgen_runner.py

Pass `solver_type` to `generate_fv_schemes()` and `generate_fv_solution()`. Currently only passed to `generate_control_dict()`.

```python
solver_type = sc.get("solverType", "steady") or "steady"

generate_control_dict(run_dir, sc, ...)
generate_fv_solution(system_dir, ..., solver_type=solver_type,
                     n_outer_correctors=sc.get("nOuterCorrectors", 1),
                     n_correctors=sc.get("nCorrectors", 2))
generate_fv_schemes(system_dir, schemes_config=sc.get("schemes"),
                    solver_type=solver_type)
```

---

## 3. Pydantic Schema (cfdproj_schema.py)

New fields in `SimulationConfig`:

```python
class SimulationConfig(CFDBaseModel):
    # Existing fields (unchanged)
    endTime: int = 500
    writeInterval: int = 100
    solverType: str = "steady"
    # ...

    # Transient-specific (only used when solverType == "transient")
    endTimeSeconds: float = 300.0
    deltaT: float = 0.1
    maxDeltaT: float = 1.0
    maxCo: float = 1.0
    writeIntervalSeconds: float = 10.0
    nOuterCorrectors: int = 1
    nCorrectors: int = 2
    ddtScheme: str = "Euler"
    crankNicolsonCoeff: float = 0.9
```

Steady fields (`endTime`, `writeInterval`) and transient fields (`endTimeSeconds`, `writeIntervalSeconds`, etc.) coexist. `generate_control_dict()` selects the appropriate set based on `solverType`. Old cfdproj files without the new fields get schema defaults automatically.

---

## 4. Frontend UI (STL_generate.html)

### 4.1 HTML Structure

When `solverType` switches to `"transient"`, hide the steady-only rows (迭代步数, 写入间隔) and show the transient parameter panel:

```html
<!-- 瞬态参数面板 (id="transient-params", display:none by default) -->
<div id="transient-params" style="display:none;">
  <div class="prop-row">
    <label data-i18n="label.end_time_seconds">总物理时间 (s)</label>
    <input type="number" id="slv-end-time-s" step="10" min="1" value="300"
           oninput="setSolverConfig('endTimeSeconds', parseFloat(this.value))">
  </div>
  <div class="prop-row">
    <label data-i18n="label.delta_t">初始时间步 (s)</label>
    <input type="number" id="slv-delta-t" step="0.01" min="0.001" value="0.1"
           oninput="setSolverConfig('deltaT', parseFloat(this.value))">
  </div>
  <div class="prop-row">
    <label data-i18n="label.max_delta_t">最大时间步 (s)</label>
    <input type="number" id="slv-max-delta-t" step="0.1" min="0.01" value="1.0"
           oninput="setSolverConfig('maxDeltaT', parseFloat(this.value))">
  </div>
  <div class="prop-row">
    <label data-i18n="label.max_courant">最大Courant数</label>
    <input type="number" id="slv-max-co" step="0.1" min="0.1" value="1.0"
           oninput="setSolverConfig('maxCo', parseFloat(this.value))">
  </div>
  <div class="prop-row">
    <label data-i18n="label.write_interval_seconds">写入间隔 (s)</label>
    <input type="number" id="slv-write-interval-s" step="1" min="1" value="10"
           oninput="setSolverConfig('writeIntervalSeconds', parseFloat(this.value))">
  </div>
  <hr style="border-color:var(--border); margin:4px 0;">
  <div class="prop-row">
    <label data-i18n="label.ddt_scheme">时间离散格式</label>
    <select id="slv-ddt-scheme"
            onchange="setSolverConfig('ddtScheme', this.value); toggleCNCoeff()">
      <option value="Euler" data-i18n="option.euler">Euler (一阶)</option>
      <option value="backward" data-i18n="option.backward">backward (二阶)</option>
      <option value="CrankNicolson" data-i18n="option.crank_nicolson">CrankNicolson (二阶)</option>
    </select>
  </div>
  <div class="prop-row" id="cn-coeff-row" style="display:none;">
    <label data-i18n="label.cn_coeff">CN系数</label>
    <input type="number" id="slv-cn-coeff" step="0.05" min="0" max="1" value="0.9"
           oninput="setSolverConfig('crankNicolsonCoeff', parseFloat(this.value))">
  </div>
  <hr style="border-color:var(--border); margin:4px 0;">
  <div class="prop-row">
    <label data-i18n="label.n_outer_correctors">PIMPLE外迭代</label>
    <select id="slv-n-outer" onchange="setSolverConfig('nOuterCorrectors', parseInt(this.value))">
      <option value="1">1 (PISO)</option>
      <option value="2">2</option>
      <option value="3">3</option>
      <option value="5">5</option>
      <option value="10">10</option>
      <option value="50">50</option>
    </select>
  </div>
  <div class="prop-row">
    <label data-i18n="label.n_correctors">压力修正步数</label>
    <select id="slv-n-correctors" onchange="setSolverConfig('nCorrectors', parseInt(this.value))">
      <option value="1">1</option>
      <option value="2" selected>2</option>
      <option value="3">3</option>
    </select>
  </div>
</div>
```

### 4.2 Toggle Logic

```javascript
function toggleTransientParams() {
    const isTransient = state.cfdRoom.solverType === 'transient';
    document.getElementById('transient-params').style.display = isTransient ? '' : 'none';
    // Hide steady-only rows
    document.getElementById('slv-endtime').closest('.prop-row').style.display = isTransient ? 'none' : '';
    document.getElementById('slv-writeinterval').closest('.prop-row').style.display = isTransient ? 'none' : '';
}

function toggleCNCoeff() {
    const show = document.getElementById('slv-ddt-scheme').value === 'CrankNicolson';
    document.getElementById('cn-coeff-row').style.display = show ? '' : 'none';
}
```

Called from the existing `solverType` dropdown's onchange handler.

### 4.3 Load/Save

Load from cfdproj (in the solver config load block):
```javascript
// Transient params
set('slv-end-time-s', cr.endTimeSeconds || 300);
set('slv-delta-t', cr.deltaT || 0.1);
set('slv-max-delta-t', cr.maxDeltaT || 1.0);
set('slv-max-co', cr.maxCo || 1.0);
set('slv-write-interval-s', cr.writeIntervalSeconds || 10);
setSelect('slv-ddt-scheme', cr.ddtScheme || 'Euler');
set('slv-cn-coeff', cr.crankNicolsonCoeff || 0.9);
setSelect('slv-n-outer', cr.nOuterCorrectors || 1);
setSelect('slv-n-correctors', cr.nCorrectors || 2);
toggleTransientParams();
toggleCNCoeff();
```

Export to cfdrun (in simulationConfig block):
```javascript
endTimeSeconds: state.cfdRoom.endTimeSeconds || 300,
deltaT: state.cfdRoom.deltaT || 0.1,
maxDeltaT: state.cfdRoom.maxDeltaT || 1.0,
maxCo: state.cfdRoom.maxCo || 1.0,
writeIntervalSeconds: state.cfdRoom.writeIntervalSeconds || 10,
nOuterCorrectors: state.cfdRoom.nOuterCorrectors || 1,
nCorrectors: state.cfdRoom.nCorrectors || 2,
ddtScheme: state.cfdRoom.ddtScheme || 'Euler',
crankNicolsonCoeff: state.cfdRoom.crankNicolsonCoeff || 0.9,
```

### 4.4 i18n

**zh-CN.json additions:**
```json
"label.end_time_seconds": "总物理时间 (s)",
"label.delta_t": "初始时间步 (s)",
"label.max_delta_t": "最大时间步 (s)",
"label.max_courant": "最大Courant数",
"label.write_interval_seconds": "写入间隔 (s)",
"label.ddt_scheme": "时间离散格式",
"label.cn_coeff": "CN系数",
"label.n_outer_correctors": "PIMPLE外迭代",
"label.n_correctors": "压力修正步数",
"option.euler": "Euler (一阶)",
"option.backward": "backward (二阶)",
"option.crank_nicolson": "CrankNicolson (二阶)"
```

**en-US.json additions:**
```json
"label.end_time_seconds": "Total Physical Time (s)",
"label.delta_t": "Initial Time Step (s)",
"label.max_delta_t": "Max Time Step (s)",
"label.max_courant": "Max Courant Number",
"label.write_interval_seconds": "Write Interval (s)",
"label.ddt_scheme": "Time Discretization",
"label.cn_coeff": "CN Coefficient",
"label.n_outer_correctors": "PIMPLE Outer Iterations",
"label.n_correctors": "Pressure Correctors",
"option.euler": "Euler (1st order)",
"option.backward": "backward (2nd order)",
"option.crank_nicolson": "CrankNicolson (2nd order)"
```

---

## 5. Backward Compatibility

- Old cfdproj files without transient fields: Schema defaults apply (`endTimeSeconds=300`, `deltaT=0.1`, etc.). Frontend shows defaults when switching to transient.
- Old cfdproj files with `solverType: "steady"` (or missing): entire transient path is skipped, zero impact on existing behavior.
- `startFrom latestTime` in transient controlDict: if a steady solution exists in the case directory, PIMPLE automatically uses it as initial conditions. No special chaining logic needed.

---

## 6. Test Plan

| Test | Validates |
|------|-----------|
| `test_transient_fv_solution_uses_pimple` | Generated fvSolution has PIMPLE dict, not SIMPLE |
| `test_transient_fv_schemes_euler` | ddtSchemes = `Euler` |
| `test_transient_fv_schemes_backward` | ddtSchemes = `backward` |
| `test_transient_fv_schemes_crank_nicolson` | ddtSchemes = `CrankNicolson 0.9` |
| `test_transient_cn_custom_coeff` | ddtSchemes = `CrankNicolson 0.5` with custom coefficient |
| `test_transient_control_dict_seconds` | endTime/writeInterval/deltaT are floats in seconds |
| `test_transient_max_delta_t` | maxDeltaT present in controlDict |
| `test_transient_adjust_time_step` | adjustTimeStep = yes, maxCo present |
| `test_steady_unchanged` | Steady path produces identical output as before |
| `test_legacy_cfdproj_defaults` | Missing transient fields get correct defaults |
| `test_pimple_no_relaxation_piso_mode` | nOuterCorrectors=1 → no relaxation block |
| `test_pimple_relaxation_with_outer` | nOuterCorrectors>1 → relaxation factors written |
| `test_schema_transient_fields` | All new fields parse and validate correctly |

---

## 7. Files Modified

| File | Change |
|------|--------|
| `third_parties/foam/template/system/transient/fvSchemes` | Rename to `.j2`, add ddtSchemes template logic |
| `third_parties/foam/template/system/transient/fvSolution` | Rename to `.j2`, parameterize PIMPLE dict |
| `third_parties/foam/template/system/transient/controlDict.j2` | Add `maxDeltaT` variable |
| `src/backend/aircooling/runner/openfoam_case.py` | Add solver_type dispatch to fvSchemes/fvSolution, extract transient params |
| `src/backend/aircooling/runner/stlgen_runner.py` | Pass solver_type to fvSchemes/fvSolution generators |
| `src/backend/aircooling/runner/cfdproj_schema.py` | Add 9 transient fields to SimulationConfig |
| `src/backend/tests/test_template_params.py` | Add 13 transient tests |
| `src/backend/tests/test_schema_completeness.py` | Add transient fields to full config + defaults |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Transient params panel, toggle logic, load/save |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | 12 new keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | 12 new keys |

---

## 8. References

- Barestrand et al. (2023), "Modeling Convective Heat Transfer of Air in a Data Center Using OpenFOAM," OpenFOAM Journal, DOI: 10.51560/ofj.v3.59 — transient provides better convergence for buoyant flows
- OpenFOAM PIMPLE: nOuterCorrectors=1 is pure PISO, >1 enables outer iteration with relaxation
- CrankNicolson coefficient: 0 = pure Euler, 1 = pure CN, 0.9 recommended for stability
- `backward` scheme: implicit 2nd order, uses BDF2 stencil (current + 2 prior time levels)
