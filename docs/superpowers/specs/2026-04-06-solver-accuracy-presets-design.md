# Solver Accuracy Presets — Design Spec

**Date**: 2026-04-06
**Goal**: Add 3 solver accuracy presets (Fast/Balanced/Accurate) + non-orthogonal correction toggle to the frontend, replacing the hardcoded fvSchemes and relaxation factors with configurable Jinja2-templated values.

---

## 1. Presets Overview

Three presets that control both fvSchemes (discretization accuracy) and fvSolution (relaxation factors). Each preset is a named bundle of settings that populates the advanced panel.

### Primary Scheme (Aggressive — recommended)

| Setting | Fast | Balanced | Accurate |
|---------|------|----------|----------|
| **divSchemes U** | `bounded Gauss upwind` | `bounded Gauss linearUpwind grad(U)` | `bounded Gauss limitedCubicV 1` |
| **divSchemes T** | `bounded Gauss upwind` | `bounded Gauss linearUpwind grad(U)` | `bounded Gauss limitedCubic 1` |
| **divSchemes k,ε** | `bounded Gauss upwind` | `bounded Gauss limitedLinear 1` | `bounded Gauss linearUpwind grad(k)` |
| **gradSchemes default** | `Gauss linear` | `Gauss linear` | `cellLimited Gauss linear 1` |
| **gradSchemes grad(U)** | — | `cellLimited Gauss linear 1` | `cellLimited Gauss linear 1` |
| **gradSchemes grad(k)** | — | — | `cellLimited Gauss linear 1` |
| **gradSchemes grad(epsilon)** | — | — | `cellLimited Gauss linear 1` |
| **laplacianSchemes** | `Gauss linear limited 0.5` | `Gauss linear limited 0.667` | `Gauss linear corrected` |
| **snGradSchemes** | `limited 0.5` | `limited 0.667` | `corrected` |
| **nNonOrthCorr** | 0 | 0 | 1 |
| **relaxation p_rgh** | 0.3 | 0.3 | 0.3 |
| **relaxation U** | 0.7 | 0.7 | 0.5 |
| **relaxation T** | 0.7 | 0.7 | 0.5 |
| **relaxation k/ε** | 0.7 | 0.5 | 0.3 |

**Rationale**:
- **Fast**: First-order upwind everywhere, aggressive relaxation, limited 0.5 non-ortho treatment. Fastest convergence, lowest accuracy. Good for initial runs / debugging geometry.
- **Balanced**: Second-order `linearUpwind` for U and T with `cellLimited` gradient; **second-order TVD** (`limitedLinear 1`) for turbulence quantities (Kurbanaliev et al. 2025 showed this outperforms upwind for buoyancy flows). Limited 0.667 non-ortho treatment. Best for production data center simulations.
- **Accurate**: **Third-order TVD** (`limitedCubicV 1`) for U and T — the highest-order bounded scheme in OpenFOAM; second-order `linearUpwind` for turbulence with cellLimited gradients on all fields. Full `corrected` laplacian/snGrad with 1 non-orthogonal corrector. Conservative relaxation. For validation / high-fidelity studies.

### Fallback Scheme (Conservative — if Primary causes divergence)

| Setting | Fast | Balanced | Accurate |
|---------|------|----------|----------|
| **divSchemes U** | `bounded Gauss upwind` | `bounded Gauss linearUpwind grad(U)` | `bounded Gauss linearUpwind grad(U)` |
| **divSchemes T** | `bounded Gauss upwind` | `bounded Gauss linearUpwind grad(U)` | `bounded Gauss MUSCL` |
| **divSchemes k,ε** | `bounded Gauss upwind` | `bounded Gauss upwind` | `bounded Gauss limitedLinear 1` |
| **gradSchemes default** | `Gauss linear` | `Gauss linear` | `Gauss linear` |
| **gradSchemes grad(U)** | — | `cellLimited Gauss linear 1` | `cellLimited Gauss linear 1` |
| **gradSchemes grad(k)** | — | — | `cellLimited Gauss linear 1` |
| **gradSchemes grad(epsilon)** | — | — | `cellLimited Gauss linear 1` |
| **laplacianSchemes** | `Gauss linear limited 0.5` | `Gauss linear limited 0.5` | `Gauss linear limited corrected 0.5` |
| **snGradSchemes** | `limited 0.5` | `limited 0.5` | `limited corrected 0.5` |
| **nNonOrthCorr** | 0 | 0 | 1 |
| **relaxation p_rgh** | 0.3 | 0.3 | 0.3 |
| **relaxation U** | 0.7 | 0.7 | 0.5 |
| **relaxation T** | 0.7 | 0.7 | 0.5 |
| **relaxation k/ε** | 0.7 | 0.7 | 0.5 |

**Fallback rationale**:
- **Fast**: Identical to Primary — already maximally conservative.
- **Balanced**: Turbulence reverts to first-order upwind (original design); laplacian/snGrad stays at 0.5.
- **Accurate**: U stays at `linearUpwind` (2nd order, Ref3 validated); T uses `MUSCL` (2nd order TVD, Ref1 validated); k/ε uses `limitedLinear 1` (2nd order TVD); laplacian uses `limited corrected 0.5` instead of full `corrected`.

### Scheme selection rationale (detailed)

**Why `limitedCubicV 1` for Accurate U (Primary)?**
- Only 3rd-order TVD-bounded scheme in OpenFOAM (van Leer & Nishikawa 2021 confirmed MUSCL is only 2nd order)
- TVD property ensures no new extrema — safe on snappyHexMesh
- Paired with conservative relaxation (U=0.5) and cellLimited gradients for stability

**Why `limitedLinear 1` for Balanced k/ε (Primary)?**
- Kurbanaliev et al. (2025): TVD-limited scheme outperforms upwind for turbulent buoyancy flows
- TVD guarantee means k and ε stay positive — critical for turbulence model stability
- COST 732 / AIJ guidelines: "at minimum second-order for all spatial terms"
- van Hooff & Blocken (2017): first-order upwind deviations up to 100% even on fine grids

**Why `corrected` for Accurate laplacian (Primary)?**
- Full non-orthogonal correction restores 2nd-order accuracy on snappyHexMesh
- Paired with nNonOrthogonalCorrectors=1 for iterative correction
- Ishigaki et al. (2017): non-orthogonal correction critical for buoyant jet accuracy
- Typical snappyHexMesh max non-ortho (40°–75°) is within `corrected` stability range

**Why fallback exists?**
- `limitedCubicV` is less commonly validated for steady-state RANS than `linearUpwind`
- Complex data center geometries with poor local mesh quality may require more diffusive schemes
- Fallback Accurate uses only schemes validated in reference data center cases (Ref1: MUSCL; Ref3: linearUpwind)

---

## 2. Non-Orthogonal Correction Toggle

A separate checkbox: **"非正交修正 (Non-Orthogonal Correction)"**

When enabled:
- `laplacianSchemes default` → `Gauss linear corrected` (replaces `limited X`)
- `snGradSchemes default` → `corrected` (replaces `limited X`)
- `nNonOrthogonalCorrectors` → `1` (in SIMPLE block, was `0`)

When disabled (default):
- Uses the preset's `limited` values as shown in the table above
- `nNonOrthogonalCorrectors` → `0`

**Note**: The correction toggle overrides the preset's laplacian/snGrad settings. The preset dropdown shows the base values; the toggle adds correction on top.

---

## 3. Data Model

### 3.1 State (frontend)

Add to `state.cfdRoom`:

```javascript
solverPreset: 'balanced',           // 'fast' | 'balanced' | 'accurate'
nonOrthoCorrection: false,          // boolean
// Advanced (populated by preset, editable by user):
divSchemeU: 'bounded Gauss linearUpwind grad(U)',
divSchemeT: 'bounded Gauss linearUpwind grad(U)',
divSchemeK: 'bounded Gauss limitedLinear 1',
divSchemeEpsilon: 'bounded Gauss limitedLinear 1',
gradSchemeDefault: 'Gauss linear',
gradSchemeU: 'cellLimited Gauss linear 1',
gradSchemeK: '',                    // empty = use default
gradSchemeEpsilon: '',              // empty = use default
laplacianScheme: 'Gauss linear limited 0.667',
snGradScheme: 'limited 0.667',
relaxP: 0.3,
relaxU: 0.7,
relaxT: 0.7,
relaxKEpsilon: 0.5,
nNonOrthoCorrectorsOverride: 0,
```

### 3.2 cfd_export.json

Add `solverSchemes` to `simulationConfig`:

```json
"simulationConfig": {
    "solverPreset": "balanced",
    "nonOrthoCorrection": false,
    "schemes": {
        "divSchemeU": "bounded Gauss linearUpwind grad(U)",
        "divSchemeT": "bounded Gauss linearUpwind grad(U)",
        "divSchemeK": "bounded Gauss limitedLinear 1",
        "divSchemeEpsilon": "bounded Gauss limitedLinear 1",
        "gradSchemeDefault": "Gauss linear",
        "gradSchemeU": "cellLimited Gauss linear 1",
        "gradSchemeK": "",
        "gradSchemeEpsilon": "",
        "laplacianScheme": "Gauss linear limited 0.667",
        "snGradScheme": "limited 0.667"
    },
    "relaxationFactors": {
        "p_rgh": 0.3,
        "U": 0.7,
        "T": 0.7,
        "k_epsilon": 0.5
    },
    "nNonOrthogonalCorrectors": 0,
    ...existing fields...
}
```

---

## 4. Backend Changes

### 4.1 fvSchemes → Jinja2 Template

Convert `fvSchemes` from static file to `fvSchemes.j2`:

```
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
```

### 4.2 fvSolution Template Update

Add relaxation factor variables to `fvSolution_cpu.j2`:

```
relaxationFactors
{
    fields
    {
        p_rgh           {{ relax_p_rgh }};
    }
    equations
    {
        U               {{ relax_U }};
        T               {{ relax_T }};
        "(k|epsilon)"   {{ relax_k_epsilon }};
    }
}
```

And `nNonOrthogonalCorrectors`:

```
SIMPLE
{
    nNonOrthogonalCorrectors {{ n_non_ortho_correctors }};
    ...
}
```

### 4.3 Python Generator

Add `generate_fv_schemes()` in `openfoam_case.py`:

```python
def generate_fv_schemes(system_dir, schemes_config):
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
```

Update `generate_fv_solution()` to accept relaxation factors and nNonOrthogonalCorrectors.

Update `copy_system_files_for_solver()` to call `generate_fv_schemes()` instead of `shutil.copy()`.

---

## 5. Frontend UI

### 5.1 Location

In the solver settings section of the right panel (where endTime, writeInterval, residualControl already live).

### 5.2 Layout

```
┌─────────────────────────────────────────┐
│ ⚙️ 求解精度                              │
│                                         │
│ 预设: [快速▾] [平衡▾] [高精度▾]          │
│                                         │
│ ☐ 非正交修正 (Non-Orthogonal Correction) │
│                                         │
│ ▶ 高级设置                               │
│ ┌─────────────────────────────────────┐ │
│ │ 离散格式                             │ │
│ │ div(U,T):    [bounded Gauss linearUpwind grad(U)  ▾] │
│ │ div(k,ε):    [bounded Gauss upwind  ▾]             │ │
│ │ grad:        [Gauss linear  ▾]                     │ │
│ │ laplacian:   [Gauss linear limited 0.667  ▾]       │ │
│ │ snGrad:      [limited 0.667  ▾]                    │ │
│ │                                                     │ │
│ │ 松弛因子                             │ │
│ │ p_rgh:   [0.3 ]                      │ │
│ │ U:       [0.7 ]                      │ │
│ │ T:       [0.7 ]                      │ │
│ │ k/ε:     [0.5 ]                      │ │
│ │                                      │ │
│ │ SIMPLE                               │ │
│ │ nNonOrthoCorrectorsOverride: [0]     │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

### 5.3 Preset Buttons

Three buttons (radio-style, only one active). Clicking a preset fills the advanced fields with that preset's values. User can then tweak individual values in the advanced panel.

If user manually changes an advanced value that doesn't match any preset, the preset indicator shows "Custom".

### 5.4 Dropdown Options for Schemes

| Field | Options |
|-------|---------|
| div(U) | `bounded Gauss upwind`, `bounded Gauss linearUpwind grad(U)`, `bounded Gauss MUSCLV`, `bounded Gauss limitedLinearV 1`, `bounded Gauss limitedCubicV 1` |
| div(T) | `bounded Gauss upwind`, `bounded Gauss linearUpwind grad(U)`, `bounded Gauss MUSCL`, `bounded Gauss limitedLinear 1`, `bounded Gauss limitedCubic 1` |
| div(k,ε) | `bounded Gauss upwind`, `bounded Gauss limitedLinear 1`, `bounded Gauss linearUpwind grad(k)` |
| grad (default) | `Gauss linear`, `cellLimited Gauss linear 1` |
| grad(U) | *(empty = use default)*, `cellLimited Gauss linear 1` |
| grad(k), grad(ε) | *(empty = use default)*, `cellLimited Gauss linear 1` |
| laplacian | `Gauss linear limited 0.5`, `Gauss linear limited 0.667`, `Gauss linear limited corrected 0.5`, `Gauss linear corrected` |
| snGrad | `limited 0.5`, `limited 0.667`, `limited corrected 0.5`, `corrected` |

---

## 6. Convergence Validation

Test with **2 cases × 4 configs × 2 scheme sets = 16 runs**, all 500 iterations:

| Case | Cells | Primary Configs | Fallback Configs |
|------|-------|-----------------|------------------|
| Tri_angle | 205K | Fast, Balanced, Accurate, Balanced+NonOrtho | Balanced, Accurate |
| base (Base_leakage) | 85K | Fast, Balanced, Accurate, Balanced+NonOrtho | Balanced, Accurate |

**Strategy**: Run Primary first. If any Primary config diverges, run corresponding Fallback. Fast is identical between Primary/Fallback so only needs one run per case.

**Success criteria**:
- All runs complete without divergence (no FOAM FATAL ERROR)
- Residuals decrease monotonically or oscillate within bounds
- If Primary Accurate diverges but Fallback Accurate converges → ship Fallback as default Accurate, offer Primary as "Experimental" option

---

## 7. Non-Goals

- Transient solver settings (only steady-state for now)
- Per-field custom scheme overrides beyond the dropdowns
- Solver algorithm changes (stays on SIMPLE)
- Linear solver settings (PCG/PBiCGStab parameters stay hardcoded)
