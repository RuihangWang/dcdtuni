# Solver Accuracy Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 3 solver accuracy presets (Fast/Balanced/Accurate) with non-orthogonal correction toggle, configurable fvSchemes/relaxation factors via Jinja2 templates, and validate convergence on 2 test cases.

**Architecture:** Convert static fvSchemes to Jinja2 template. Add relaxation + nNonOrthogonalCorrectors variables to fvSolution template. Create preset definitions in frontend that populate advanced settings. Pass schemes/relaxation config through cfd_export.json → Python generators → templates. Validate with 8 CFD runs (2 cases × 4 presets).

**Tech Stack:** Jinja2 templates, Python (openfoam_case.py), JavaScript (STL_generate.html), OpenFOAM v2406, buoyantBoussinesqSimpleFoam

**Spec:** `docs/superpowers/specs/2026-04-06-solver-accuracy-presets-design.md`

---

### File Structure

| File | Responsibility |
|------|---------------|
| `dctwin/dctwin/third_parties/foam/template/system/steady/fvSchemes.j2` | **New** — Jinja2 template replacing static `fvSchemes` |
| `dctwin/dctwin/third_parties/foam/template/system/steady/fvSolution_cpu.j2` | **Modify** — Add relaxation + nNonOrtho variables |
| `dctwin/dctwin/third_parties/foam/template/system/steady/fvSchemes` | **Delete** — Replaced by `.j2` |
| `dctwin/dctwin/runner/openfoam_case.py` | **Modify** — Add `generate_fv_schemes()`, update `generate_fv_solution()`, update `copy_system_files_for_solver()` |
| `dctwin/dctwin/runner/stlgen_runner.py` | **Modify** — Pass schemes config to generators |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | **Modify** — Add preset UI, advanced panel, export config |

---

### Task 1: Create fvSchemes.j2 Jinja2 Template

**Files:**
- Create: `dctwin/dctwin/third_parties/foam/template/system/steady/fvSchemes.j2`
- Delete: `dctwin/dctwin/third_parties/foam/template/system/steady/fvSchemes`

- [ ] **Step 1: Create the Jinja2 template**

Based on the reference files in `Ref_code/fvScheme/`, create a template that supports all scheme combinations:

```
/*--------------------------------*- C++ -*----------------------------------*\
| =========                 |                                                 |
| \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\    /   O peration     | Version:  v2406                                 |
|   \\  /    A nd           | Website:  www.openfoam.com                      |
|    \\/     M anipulation  |                                                 |
\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      fvSchemes;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

ddtSchemes
{
    default         steadyState;
}

gradSchemes
{
    default         {{ grad_scheme_default }};
{% if grad_scheme_U %}
    grad(U)         {{ grad_scheme_U }};
{% endif %}
{% if grad_scheme_k %}
    grad(k)         {{ grad_scheme_k }};
{% endif %}
{% if grad_scheme_epsilon %}
    grad(epsilon)   {{ grad_scheme_epsilon }};
{% endif %}
}

divSchemes
{
    default         none;

    div(phi,U)      {{ div_scheme_U }};
    div(phi,T)      {{ div_scheme_T }};
    div(phi,k)      {{ div_scheme_k }};
    div(phi,epsilon) {{ div_scheme_epsilon }};
    div(phi,R)      bounded Gauss upwind;
    div(R)          Gauss linear;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         {{ laplacian_scheme }};
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         {{ sn_grad_scheme }};
}


// ************************************************************************* //
```

- [ ] **Step 2: Delete the old static fvSchemes file**

```bash
git rm dctwin/dctwin/third_parties/foam/template/system/steady/fvSchemes
```

- [ ] **Step 3: Commit**

```bash
git add dctwin/dctwin/third_parties/foam/template/system/steady/fvSchemes.j2
git commit -m "feat: convert fvSchemes to Jinja2 template with per-field scheme variables"
```

---

### Task 2: Update fvSolution Template with Relaxation + nNonOrtho Variables

**Files:**
- Modify: `dctwin/dctwin/third_parties/foam/template/system/steady/fvSolution_cpu.j2`

- [ ] **Step 1: Replace hardcoded relaxation factors and nNonOrthogonalCorrectors with Jinja2 variables**

In `fvSolution_cpu.j2`, replace:

```
SIMPLE
{
    nNonOrthogonalCorrectors 0;
```

with:

```
SIMPLE
{
    nNonOrthogonalCorrectors {{ n_non_ortho_correctors | default(0) }};
```

And replace:

```
relaxationFactors
{
    fields
    {
        p_rgh           0.5;
    }
    equations
    {
        U               0.5;
        T               0.5;
        "(k|epsilon)"   0.5;
    }
}
```

with:

```
relaxationFactors
{
    fields
    {
        p_rgh           {{ relax_p_rgh | default(0.3) }};
    }
    equations
    {
        U               {{ relax_U | default(0.7) }};
        T               {{ relax_T | default(0.7) }};
        "(k|epsilon)"   {{ relax_k_epsilon | default(0.5) }};
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add dctwin/dctwin/third_parties/foam/template/system/steady/fvSolution_cpu.j2
git commit -m "feat: add relaxation + nNonOrtho Jinja2 variables to fvSolution template"
```

---

### Task 3: Add `generate_fv_schemes()` and Update Python Generators

**Files:**
- Modify: `dctwin/dctwin/runner/openfoam_case.py`

- [ ] **Step 1: Add `generate_fv_schemes()` function after `generate_fv_solution()`**

```python
def generate_fv_schemes(
    system_dir: Path,
    schemes_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Generate fvSchemes from Jinja2 template with configurable discretization schemes."""
    system_dir.mkdir(parents=True, exist_ok=True)
    schemes_config = schemes_config or {}

    template = template_env.get_template("foam/template/system/steady/fvSchemes.j2")
    content = template.render(
        grad_scheme_default=schemes_config.get("gradSchemeDefault", "Gauss linear"),
        grad_scheme_U=schemes_config.get("gradSchemeU", ""),
        grad_scheme_k=schemes_config.get("gradSchemeK", ""),
        grad_scheme_epsilon=schemes_config.get("gradSchemeEpsilon", ""),
        div_scheme_U=schemes_config.get("divSchemeU", "bounded Gauss upwind"),
        div_scheme_T=schemes_config.get("divSchemeT", "bounded Gauss upwind"),
        div_scheme_k=schemes_config.get("divSchemeK", "bounded Gauss upwind"),
        div_scheme_epsilon=schemes_config.get("divSchemeEpsilon", "bounded Gauss upwind"),
        laplacian_scheme=schemes_config.get("laplacianScheme", "Gauss linear limited 0.5"),
        sn_grad_scheme=schemes_config.get("snGradScheme", "limited 0.5"),
    )
    (system_dir / "fvSchemes").write_text(content, encoding="utf-8")
    logger.info(f"Generated fvSchemes: divU={schemes_config.get('divSchemeU', 'upwind')}")
```

- [ ] **Step 2: Update `generate_fv_solution()` to accept relaxation factors and nNonOrthoCorrectorsOverride**

Add parameters and pass to template:

```python
def generate_fv_solution(
    system_dir: Path,
    residual_control: Optional[Dict[str, Any]] = None,
    relaxation_factors: Optional[Dict[str, Any]] = None,
    n_non_ortho_correctors: int = 0,
    pref_point: Optional[Tuple[float, float, float]] = None,
) -> None:
    """Generate fvSolution with configurable residual thresholds, relaxation, and pRefPoint."""
    system_dir.mkdir(parents=True, exist_ok=True)

    residual_control = residual_control or {}
    res_p_rgh = residual_control.get("p_rgh", 1e-4)
    res_u = residual_control.get("U", 1e-5)
    res_t = residual_control.get("T", 1e-5)
    res_k_epsilon = residual_control.get("k_epsilon", 1e-4)

    relaxation_factors = relaxation_factors or {}
    relax_p = relaxation_factors.get("p_rgh", 0.3)
    relax_u = relaxation_factors.get("U", 0.7)
    relax_t = relaxation_factors.get("T", 0.7)
    relax_ke = relaxation_factors.get("k_epsilon", 0.5)

    use_pref_point = pref_point is not None
    if pref_point is None:
        pref_point = (0.0, 0.0, 0.0)
    px, py, pz = pref_point

    template = template_env.get_template("foam/template/system/steady/fvSolution_cpu.j2")
    content = template.render(
        res_p_rgh=res_p_rgh,
        res_U=res_u,
        res_T=res_t,
        res_k_epsilon=res_k_epsilon,
        relax_p_rgh=relax_p,
        relax_U=relax_u,
        relax_T=relax_t,
        relax_k_epsilon=relax_ke,
        n_non_ortho_correctors=n_non_ortho_correctors,
        use_pref_point=use_pref_point,
        pref_x=px,
        pref_y=py,
        pref_z=pz,
    )
    (system_dir / "fvSolution").write_text(content, encoding="utf-8")
    logger.info(
        f"Generated fvSolution: residualControl p_rgh={res_p_rgh}, "
        f"U={res_u}, T={res_t}, k/epsilon={res_k_epsilon}, "
        f"relaxation p={relax_p} U={relax_u} T={relax_t} ke={relax_ke}, "
        f"nNonOrthoCorr={n_non_ortho_correctors}, "
        f"pRefPoint=({px}, {py}, {pz})"
    )
```

- [ ] **Step 3: Update `copy_system_files_for_solver()` to accept and pass schemes_config**

Replace the `shutil.copy(fvSchemes)` block with `generate_fv_schemes()`:

```python
def copy_system_files_for_solver(
    run_dir: Path,
    *,
    physical_properties: Optional[Dict[str, Any]] = None,
    gravity: Optional[Dict[str, Any]] = None,
    room_temperature_c: Optional[float] = None,
    schemes_config: Optional[Dict[str, Any]] = None,
) -> None:
    # ... existing code ...
    
    # Replace shutil.copy(fvSchemes) with:
    generate_fv_schemes(system_dir, schemes_config)
    
    # ... rest of function ...
```

- [ ] **Step 4: Commit**

```bash
git add dctwin/dctwin/runner/openfoam_case.py
git commit -m "feat: add generate_fv_schemes() + relaxation/nNonOrtho params to fvSolution"
```

---

### Task 4: Update STLGenRunner to Pass Schemes Config

**Files:**
- Modify: `dctwin/dctwin/runner/stlgen_runner.py`

- [ ] **Step 1: Update `_setup_complete_case_in_mesh()` to pass schemes_config**

At line ~584, update the `copy_system_files_for_solver()` call:

```python
sc = self._data.get("simulationConfig", {})
copy_system_files_for_solver(
    mesh_dir,
    physical_properties=sc.get("physical_properties", {}),
    gravity=sc.get("gravity", {}),
    room_temperature_c=float(sc.get("roomTemperature", None) or 22.0),
    schemes_config=sc.get("schemes", {}),
)
```

- [ ] **Step 2: Update `_generate_solver_dicts()` to pass relaxation factors and nNonOrthoCorrectorsOverride**

At line ~639, update the `generate_fv_solution()` call:

```python
generate_fv_solution(
    system_dir,
    residual_control=residual_control,
    relaxation_factors=sc.get("relaxationFactors", {}),
    n_non_ortho_correctors=int(sc.get("nNonOrthogonalCorrectors", 0)),
)
```

Also regenerate fvSchemes at solve-time (user may have changed preset between mesh and solve):

```python
generate_fv_schemes(system_dir, sc.get("schemes", {}))
```

Add the import:

```python
from dctwin.runner.openfoam_case import (
    ...existing imports...,
    generate_fv_schemes,
)
```

- [ ] **Step 3: Commit**

```bash
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "feat: pass schemes + relaxation config from cfd_export to OpenFOAM generators"
```

---

### Task 5: Frontend — Preset Definitions + State + Export

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add preset definitions as a global constant**

After the existing solver-related constants (near the `setSolverConfig` function, ~line 20158), add:

```javascript
var _SOLVER_PRESETS = {
    fast: {
        divSchemeU: 'bounded Gauss upwind',
        divSchemeT: 'bounded Gauss upwind',
        divSchemeK: 'bounded Gauss upwind',
        divSchemeEpsilon: 'bounded Gauss upwind',
        gradSchemeDefault: 'Gauss linear',
        gradSchemeU: '',
        gradSchemeK: '',
        gradSchemeEpsilon: '',
        laplacianScheme: 'Gauss linear limited 0.5',
        snGradScheme: 'limited 0.5',
        nNonOrthogonalCorrectors: 0,
        relaxP: 0.3, relaxU: 0.7, relaxT: 0.7, relaxKEpsilon: 0.7
    },
    balanced: {
        divSchemeU: 'bounded Gauss linearUpwind grad(U)',
        divSchemeT: 'bounded Gauss linearUpwind grad(U)',
        divSchemeK: 'bounded Gauss limitedLinear 1',
        divSchemeEpsilon: 'bounded Gauss limitedLinear 1',
        gradSchemeDefault: 'Gauss linear',
        gradSchemeU: 'cellLimited Gauss linear 1',
        gradSchemeK: '',
        gradSchemeEpsilon: '',
        laplacianScheme: 'Gauss linear limited 0.667',
        snGradScheme: 'limited 0.667',
        nNonOrthogonalCorrectors: 0,
        relaxP: 0.3, relaxU: 0.7, relaxT: 0.7, relaxKEpsilon: 0.5
    },
    accurate: {
        divSchemeU: 'bounded Gauss limitedCubicV 1',
        divSchemeT: 'bounded Gauss limitedCubic 1',
        divSchemeK: 'bounded Gauss linearUpwind grad(k)',
        divSchemeEpsilon: 'bounded Gauss linearUpwind grad(epsilon)',
        gradSchemeDefault: 'cellLimited Gauss linear 1',
        gradSchemeU: '',
        gradSchemeK: '',
        gradSchemeEpsilon: '',
        laplacianScheme: 'Gauss linear corrected',
        snGradScheme: 'corrected',
        nNonOrthogonalCorrectors: 1,
        relaxP: 0.3, relaxU: 0.5, relaxT: 0.5, relaxKEpsilon: 0.3
    }
};
```

- [ ] **Step 2: Add state defaults in initial cfdRoom state**

Find the `state.cfdRoom` initialization (search for `residualControl:`) and add after it:

```javascript
solverPreset: 'balanced',
nonOrthoCorrection: false,
schemes: {
    divSchemeU: 'bounded Gauss linearUpwind grad(U)',
    divSchemeT: 'bounded Gauss linearUpwind grad(U)',
    divSchemeK: 'bounded Gauss limitedLinear 1',
    divSchemeEpsilon: 'bounded Gauss limitedLinear 1',
    gradSchemeDefault: 'Gauss linear',
    gradSchemeU: 'cellLimited Gauss linear 1',
    gradSchemeK: '',
    gradSchemeEpsilon: '',
    laplacianScheme: 'Gauss linear limited 0.667',
    snGradScheme: 'limited 0.667'
},
relaxationFactors: { p_rgh: 0.3, U: 0.7, T: 0.7, k_epsilon: 0.5 },
nNonOrthogonalCorrectors: 0,
```

- [ ] **Step 3: Add `applySolverPreset()` and `setSolverScheme()` functions**

```javascript
window.applySolverPreset = function(presetName) {
    pushUndoCheckpoint('applySolverPreset');
    var preset = _SOLVER_PRESETS[presetName];
    if (!preset) return;
    state.cfdRoom.solverPreset = presetName;
    state.cfdRoom.schemes = {
        divSchemeU: preset.divSchemeU,
        divSchemeT: preset.divSchemeT,
        divSchemeK: preset.divSchemeK,
        divSchemeEpsilon: preset.divSchemeEpsilon,
        gradSchemeDefault: preset.gradSchemeDefault,
        gradSchemeU: preset.gradSchemeU,
        gradSchemeK: preset.gradSchemeK,
        gradSchemeEpsilon: preset.gradSchemeEpsilon,
        laplacianScheme: preset.laplacianScheme,
        snGradScheme: preset.snGradScheme
    };
    state.cfdRoom.relaxationFactors = {
        p_rgh: preset.relaxP, U: preset.relaxU,
        T: preset.relaxT, k_epsilon: preset.relaxKEpsilon
    };
    state.cfdRoom.nNonOrthogonalCorrectors = preset.nNonOrthogonalCorrectors;
    // Apply non-ortho correction if checked
    if (state.cfdRoom.nonOrthoCorrection) {
        state.cfdRoom.schemes.laplacianScheme = 'Gauss linear corrected';
        state.cfdRoom.schemes.snGradScheme = 'corrected';
        state.cfdRoom.nNonOrthogonalCorrectors = Math.max(1, state.cfdRoom.nNonOrthogonalCorrectors);
    }
    updateUI();
    logOperation('applySolverPreset', 'Apply solver preset: ' + presetName, {preset: presetName}, 'modify');
};

window.toggleNonOrthoCorrection = function(checked) {
    pushUndoCheckpoint('toggleNonOrthoCorrection');
    state.cfdRoom.nonOrthoCorrection = !!checked;
    if (checked) {
        state.cfdRoom.schemes.laplacianScheme = 'Gauss linear corrected';
        state.cfdRoom.schemes.snGradScheme = 'corrected';
        state.cfdRoom.nNonOrthogonalCorrectors = Math.max(1, state.cfdRoom.nNonOrthogonalCorrectors);
    } else {
        // Restore preset values
        var preset = _SOLVER_PRESETS[state.cfdRoom.solverPreset || 'balanced'];
        if (preset) {
            state.cfdRoom.schemes.laplacianScheme = preset.laplacianScheme;
            state.cfdRoom.schemes.snGradScheme = preset.snGradScheme;
            state.cfdRoom.nNonOrthogonalCorrectors = preset.nNonOrthogonalCorrectors;
        }
    }
    updateUI();
    logOperation('toggleNonOrthoCorrection', 'Toggle non-ortho correction: ' + checked, {checked}, 'modify');
};
```

- [ ] **Step 4: Update cfd_export.json builder to include schemes + relaxation**

In the `simulationConfig` export block (~line 22415), add after `residualControl`:

```javascript
schemes: state.cfdRoom.schemes || {},
relaxationFactors: state.cfdRoom.relaxationFactors || { p_rgh: 0.3, U: 0.7, T: 0.7, k_epsilon: 0.5 },
nNonOrthogonalCorrectors: state.cfdRoom.nNonOrthogonalCorrectors || 0,
solverPreset: state.cfdRoom.solverPreset || 'balanced',
nonOrthoCorrection: state.cfdRoom.nonOrthoCorrection || false,
```

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/STL_generate.html && git commit -m "feat: add solver preset definitions + state + export"
cd .. && git add STLGeneratorForOpenFoam && git commit -m "chore: update submodule (solver preset data model)"
```

---

### Task 6: Frontend — Preset UI Panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add preset buttons + non-ortho checkbox + collapsible advanced panel**

Insert before the residual control section (~line 181, before `<div class="prop-group"...📉 收敛残差阈值设置`):

```html
<div class="prop-group" style="border-color:#ff9f43; margin:8px;">
    <div class="prop-title" style="color:#ff9f43">⚙️ 求解精度预设</div>
    <div class="prop-row" style="gap:4px;">
        <button id="preset-fast" class="btn" style="flex:1; font-size:0.75rem;" onclick="window.applySolverPreset('fast')">🚀 快速</button>
        <button id="preset-balanced" class="btn primary" style="flex:1; font-size:0.75rem;" onclick="window.applySolverPreset('balanced')">⚖️ 平衡</button>
        <button id="preset-accurate" class="btn" style="flex:1; font-size:0.75rem;" onclick="window.applySolverPreset('accurate')">🎯 高精度</button>
    </div>
    <div class="prop-row" style="margin-top:4px;">
        <label style="font-size:0.78rem;">非正交修正</label>
        <input type="checkbox" id="slv-nonortho" onchange="window.toggleNonOrthoCorrection(this.checked)" style="width:auto;">
    </div>
    <div style="font-size:0.68rem; color:var(--text-dim); line-height:1.4; margin-top:4px;">
        快速=一阶 | 平衡=二阶(推荐) | 高精度=三阶TVD
    </div>
    <div style="margin-top:6px;">
        <div class="prop-row" style="cursor:pointer; user-select:none;" onclick="var adv=document.getElementById('slv-advanced'); var arr=this.querySelector('span'); if(adv.style.display==='none'){adv.style.display='';arr.textContent='▼';}else{adv.style.display='none';arr.textContent='▶';}">
            <span style="font-size:8px; width:10px; display:inline-block;">▶</span>
            <label style="cursor:pointer; font-size:0.75rem; color:var(--text-dim);">高级设置</label>
        </div>
        <div id="slv-advanced" style="display:none; padding-left:8px; margin-top:4px;">
            <div style="font-size:0.68rem; color:#ff9f43; margin-bottom:4px;">离散格式</div>
            <div class="prop-row"><label style="font-size:0.72rem;">div(U)</label><select id="slv-div-U" style="font-size:0.7rem; flex:1;" onchange="window.setSolverScheme('divSchemeU', this.value)">
                <option value="bounded Gauss upwind">upwind (1st)</option>
                <option value="bounded Gauss linearUpwind grad(U)">linearUpwind (2nd)</option>
                <option value="bounded Gauss limitedCubicV 1">limitedCubicV (3rd TVD)</option>
            </select></div>
            <div class="prop-row"><label style="font-size:0.72rem;">div(T)</label><select id="slv-div-T" style="font-size:0.7rem; flex:1;" onchange="window.setSolverScheme('divSchemeT', this.value)">
                <option value="bounded Gauss upwind">upwind (1st)</option>
                <option value="bounded Gauss linearUpwind grad(U)">linearUpwind (2nd)</option>
                <option value="bounded Gauss limitedCubic 1">limitedCubic (3rd TVD)</option>
                <option value="bounded Gauss MUSCL">MUSCL (2nd TVD)</option>
            </select></div>
            <div class="prop-row"><label style="font-size:0.72rem;">div(k,ε)</label><select id="slv-div-ke" style="font-size:0.7rem; flex:1;" onchange="window.setSolverScheme('divSchemeK', this.value); window.setSolverScheme('divSchemeEpsilon', this.value);">
                <option value="bounded Gauss upwind">upwind (1st)</option>
                <option value="bounded Gauss limitedLinear 1">limitedLinear (2nd TVD)</option>
                <option value="bounded Gauss linearUpwind grad(k)">linearUpwind (2nd)</option>
            </select></div>
            <div class="prop-row"><label style="font-size:0.72rem;">laplacian</label><select id="slv-laplacian" style="font-size:0.7rem; flex:1;" onchange="window.setSolverScheme('laplacianScheme', this.value)">
                <option value="Gauss linear limited 0.5">limited 0.5</option>
                <option value="Gauss linear limited 0.667">limited 0.667</option>
                <option value="Gauss linear limited 1">limited 1</option>
                <option value="Gauss linear corrected">corrected</option>
                <option value="Gauss linear limited corrected 0.5">limited corrected 0.5</option>
            </select></div>
            <div class="prop-row"><label style="font-size:0.72rem;">snGrad</label><select id="slv-sngrad" style="font-size:0.7rem; flex:1;" onchange="window.setSolverScheme('snGradScheme', this.value)">
                <option value="limited 0.5">limited 0.5</option>
                <option value="limited 0.667">limited 0.667</option>
                <option value="limited 1">limited 1</option>
                <option value="corrected">corrected</option>
                <option value="limited corrected 0.5">limited corrected 0.5</option>
            </select></div>
            <div style="font-size:0.68rem; color:#ff9f43; margin:6px 0 4px;">松弛因子</div>
            <div class="prop-row"><label style="font-size:0.72rem;">p_rgh</label><input type="number" id="slv-relax-p" step="0.05" min="0.1" max="1" value="0.3" style="width:60px;" onchange="window.setSolverScheme('relaxP', parseFloat(this.value))"></div>
            <div class="prop-row"><label style="font-size:0.72rem;">U</label><input type="number" id="slv-relax-u" step="0.05" min="0.1" max="1" value="0.7" style="width:60px;" onchange="window.setSolverScheme('relaxU', parseFloat(this.value))"></div>
            <div class="prop-row"><label style="font-size:0.72rem;">T</label><input type="number" id="slv-relax-t" step="0.05" min="0.1" max="1" value="0.7" style="width:60px;" onchange="window.setSolverScheme('relaxT', parseFloat(this.value))"></div>
            <div class="prop-row"><label style="font-size:0.72rem;">k/ε</label><input type="number" id="slv-relax-ke" step="0.05" min="0.1" max="1" value="0.5" style="width:60px;" onchange="window.setSolverScheme('relaxKEpsilon', parseFloat(this.value))"></div>
            <div style="font-size:0.68rem; color:#ff9f43; margin:6px 0 4px;">SIMPLE</div>
            <div class="prop-row"><label style="font-size:0.72rem;">nNonOrthoCorr</label><input type="number" id="slv-nnonortho" step="1" min="0" max="3" value="0" style="width:60px;" onchange="window.setSolverScheme('nNonOrthogonalCorrectors', parseInt(this.value))"></div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Add `setSolverScheme()` function and UI sync**

```javascript
window.setSolverScheme = function(field, value) {
    pushUndoCheckpoint('setSolverScheme');
    if (field === 'relaxP') { state.cfdRoom.relaxationFactors.p_rgh = value; }
    else if (field === 'relaxU') { state.cfdRoom.relaxationFactors.U = value; }
    else if (field === 'relaxT') { state.cfdRoom.relaxationFactors.T = value; }
    else if (field === 'relaxKEpsilon') { state.cfdRoom.relaxationFactors.k_epsilon = value; }
    else if (field === 'nNonOrthogonalCorrectors') { state.cfdRoom.nNonOrthogonalCorrectors = value; }
    else { state.cfdRoom.schemes[field] = value; }
    // Mark as custom if doesn't match any preset
    state.cfdRoom.solverPreset = _detectCurrentPreset();
    updateUI();
    logOperation('setSolverScheme', 'Set solver scheme: ' + field, {field, value}, 'modify');
};

function _detectCurrentPreset() {
    var s = state.cfdRoom.schemes || {};
    var r = state.cfdRoom.relaxationFactors || {};
    for (var name in _SOLVER_PRESETS) {
        var p = _SOLVER_PRESETS[name];
        if (s.divSchemeU === p.divSchemeU && s.divSchemeK === p.divSchemeK &&
            s.laplacianScheme === p.laplacianScheme && r.U === p.relaxU && r.k_epsilon === p.relaxKEpsilon) {
            return name;
        }
    }
    return 'custom';
}
```

- [ ] **Step 3: Add UI sync in `updateUI()` or dedicated `_syncSolverPresetUI()`**

Find where solver UI is synced (near `setSolverConfig`) and add a function that updates button active states and dropdown values:

```javascript
function _syncSolverPresetUI() {
    var preset = state.cfdRoom.solverPreset || 'balanced';
    ['fast', 'balanced', 'accurate'].forEach(function(p) {
        var btn = document.getElementById('preset-' + p);
        if (btn) btn.className = 'btn' + (preset === p ? ' primary' : '');
    });
    var cb = document.getElementById('slv-nonortho');
    if (cb) cb.checked = !!state.cfdRoom.nonOrthoCorrection;
    var s = state.cfdRoom.schemes || {};
    var r = state.cfdRoom.relaxationFactors || {};
    _setSelectVal('slv-div-U', s.divSchemeU);
    _setSelectVal('slv-div-T', s.divSchemeT);
    _setSelectVal('slv-div-ke', s.divSchemeK);
    _setSelectVal('slv-laplacian', s.laplacianScheme);
    _setSelectVal('slv-sngrad', s.snGradScheme);
    _setInputVal('slv-relax-p', r.p_rgh);
    _setInputVal('slv-relax-u', r.U);
    _setInputVal('slv-relax-t', r.T);
    _setInputVal('slv-relax-ke', r.k_epsilon);
    _setInputVal('slv-nnonortho', state.cfdRoom.nNonOrthogonalCorrectors);
}

function _setSelectVal(id, val) {
    var el = document.getElementById(id);
    if (el && val != null) el.value = val;
}
function _setInputVal(id, val) {
    var el = document.getElementById(id);
    if (el && val != null) el.value = val;
}
```

Call `_syncSolverPresetUI()` at the end of `updateUI()` or from `applySolverPreset`/`toggleNonOrthoCorrection`.

- [ ] **Step 4: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/STL_generate.html && git commit -m "feat: solver preset UI with advanced panel dropdowns"
cd .. && git add STLGeneratorForOpenFoam && git commit -m "chore: update submodule (solver preset UI)"
```

---

### Task 7: CFD Convergence Validation — Tri_angle (4 runs)

**Prerequisite:** Tasks 1-6 complete, server restarted.

Run 4 solver configurations on the Tri_angle case (205K cells, 500 iterations, parallel):

- [ ] **Step 1: Start server**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8001/tcp 2>/dev/null; sleep 2
conda run -n dcdtuni --no-capture-output python STLGeneratorForOpenFoam/server/server.py \
  --case-dir /mnt/lv/litianyu/mycode/dcdtuni/dctwin/case/Tri_angle --port 8005 &
```

- [ ] **Step 2: Run Fast preset**

Modify cfd_export.json to set Fast preset schemes/relaxation, trigger solve via API, monitor logs for divergence.

- [ ] **Step 3: Run Balanced preset**

Same process with Balanced preset values.

- [ ] **Step 4: Run Accurate preset**

Same process with Accurate preset values.

- [ ] **Step 5: Run Balanced + NonOrtho correction**

Same process with Balanced preset + `laplacianScheme: corrected`, `snGradScheme: corrected`, `nNonOrthogonalCorrectors: 1`.

- [ ] **Step 6: Verify all 4 runs completed without divergence**

Check server logs for each run:
- No `FOAM FATAL ERROR`
- Final residuals visible in log
- returncode = 0

---

### Task 8: CFD Convergence Validation — base/Base_leakage (4 runs)

Same as Task 7 but for the `base` case (85K cells).

- [ ] **Step 1-6: Repeat the 4 configurations on base case**

Same 4 presets: Fast, Balanced, Accurate, Balanced+NonOrtho.

- [ ] **Step 7: Summary comparison table**

Create a summary of all 8 runs with timing, final residuals, and convergence status.

---

## Summary of Changes

| Component | What Changes | Why |
|-----------|-------------|-----|
| `fvSchemes` → `fvSchemes.j2` | Static file → Jinja2 template | Enable per-field scheme configuration |
| `fvSolution_cpu.j2` | Add relaxation + nNonOrtho variables | Enable configurable relaxation factors |
| `openfoam_case.py` | Add `generate_fv_schemes()`, update `generate_fv_solution()` | Python-side generation with config |
| `stlgen_runner.py` | Pass schemes + relaxation config | Wire cfd_export.json to generators |
| `STL_generate.html` | Preset buttons, advanced panel, state/export | User-facing configuration |
| 8 CFD runs | Convergence validation | Ensure all presets work on real cases |
