# Transient CFD (PIMPLE) Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable DCTwin to run transient CFD using `buoyantBoussinesqPimpleFoam` with proper PIMPLE fvSolution/fvSchemes templates, user-configurable time discretization (Euler/backward/CrankNicolson), and a frontend panel that expands when the user selects "非定常" mode.

**Architecture:** Bottom-up: (1) convert static transient OpenFOAM template files to Jinja2, (2) add solver_type dispatch in Python generation functions, (3) add transient fields to Pydantic schema, (4) add frontend UI panel with i18n. Each task is independently testable.

**Tech Stack:** Python 3.10, Jinja2 templates, Pydantic, OpenFOAM v2406, HTML/JS (Three.js frontend)

**Spec:** `docs/superpowers/specs/2026-04-25-transient-cfd-support-design.md`

---

### Task 1: Add transient schema fields to SimulationConfig

**Files:**
- Modify: `src/backend/aircooling/runner/cfdproj_schema.py:470-487`
- Test: `src/backend/tests/test_schema_completeness.py`

- [ ] **Step 1: Write the failing tests**

Add transient fields to `FULL_FRONTEND_SIMULATION_CONFIG` and default assertions in `test_schema_completeness.py`:

```python
# In FULL_FRONTEND_SIMULATION_CONFIG dict (after "writePrecision": "double",):
    "endTimeSeconds": 300.0,
    "deltaT": 0.1,
    "maxDeltaT": 1.0,
    "maxCo": 1.0,
    "writeIntervalSeconds": 10.0,
    "nOuterCorrectors": 1,
    "nCorrectors": 2,
    "ddtScheme": "Euler",
    "crankNicolsonCoeff": 0.9,

# In test_defaults_when_empty (after assert sc.writePrecision == "single"):
        assert sc.endTimeSeconds == 300.0
        assert sc.deltaT == 0.1
        assert sc.maxDeltaT == 1.0
        assert sc.maxCo == 1.0
        assert sc.writeIntervalSeconds == 10.0
        assert sc.nOuterCorrectors == 1
        assert sc.nCorrectors == 2
        assert sc.ddtScheme == "Euler"
        assert sc.crankNicolsonCoeff == 0.9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_schema_completeness.py -v --tb=short`
Expected: FAIL — `ValidationError` on unknown fields and `AttributeError` on missing defaults.

- [ ] **Step 3: Add fields to SimulationConfig**

In `src/backend/aircooling/runner/cfdproj_schema.py`, add after `writePrecision: str = "single"` (line 474):

```python
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_schema_completeness.py -v --tb=short`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/cfdproj_schema.py src/backend/tests/test_schema_completeness.py
git commit -m "feat(schema): add 9 transient PIMPLE fields to SimulationConfig"
```

---

### Task 2: Convert transient fvSchemes to Jinja2 template with ddtScheme support

**Files:**
- Modify: `src/backend/aircooling/third_parties/foam/template/system/transient/fvSchemes` → rename to `fvSchemes.j2`
- Modify: `src/backend/aircooling/runner/openfoam_case.py:207-232`
- Test: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Write the failing tests**

Add to `TestFvSchemes` class in `src/backend/tests/test_template_params.py`:

```python
    def test_transient_fv_schemes_euler(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "Euler" in text
        assert "steadyState" not in text

    def test_transient_fv_schemes_backward(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, schemes_config={"ddtScheme": "backward"},
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "backward" in text
        assert "Euler" not in text
        assert "steadyState" not in text

    def test_transient_fv_schemes_crank_nicolson(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir,
                            schemes_config={"ddtScheme": "CrankNicolson", "crankNicolsonCoeff": 0.9},
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "CrankNicolson 0.9" in text

    def test_transient_fv_schemes_cn_custom_coeff(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir,
                            schemes_config={"ddtScheme": "CrankNicolson", "crankNicolsonCoeff": 0.5},
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "CrankNicolson 0.5" in text

    def test_steady_fv_schemes_unchanged(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, SLAB_SIMULATION_CONFIG["schemes"])
        text = (system_dir / "fvSchemes").read_text()
        assert "steadyState" in text

    def test_transient_fv_schemes_spatial_schemes_propagated(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_schemes(system_dir, SLAB_SIMULATION_CONFIG["schemes"],
                            solver_type="transient")
        text = (system_dir / "fvSchemes").read_text()
        assert "bounded Gauss linearUpwind grad(U)" in text
        assert "steadyState" not in text
        assert "Euler" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestFvSchemes -v --tb=short`
Expected: FAIL — `generate_fv_schemes()` does not accept `solver_type` parameter.

- [ ] **Step 3: Rename the transient fvSchemes to .j2 and rewrite as template**

```bash
mv src/backend/aircooling/third_parties/foam/template/system/transient/fvSchemes \
   src/backend/aircooling/third_parties/foam/template/system/transient/fvSchemes.j2
```

Write `src/backend/aircooling/third_parties/foam/template/system/transient/fvSchemes.j2`:

```jinja2
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
{% if ddt_scheme == "CrankNicolson" %}
    default         CrankNicolson {{ cn_coeff }};
{% elif ddt_scheme == "backward" %}
    default         backward;
{% else %}
    default         Euler;
{% endif %}
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

- [ ] **Step 4: Update generate_fv_schemes() in openfoam_case.py**

Replace the current `generate_fv_schemes` function (lines 207-232) with:

```python
def generate_fv_schemes(
    system_dir: Path,
    schemes_config: Optional[Dict[str, Any]] = None,
    solver_type: str = "steady",
) -> None:
    """Generate fvSchemes from Jinja2 template with configurable discretization schemes."""
    system_dir.mkdir(parents=True, exist_ok=True)
    schemes_config = schemes_config or {}

    if solver_type == "transient":
        template_path = "foam/template/system/transient/fvSchemes.j2"
    else:
        template_path = "foam/template/system/steady/fvSchemes.j2"

    ddt_scheme = schemes_config.get("ddtScheme", "Euler")
    cn_coeff = float(schemes_config.get("crankNicolsonCoeff", 0.9))

    template = template_env.get_template(template_path)
    content = template.render(
        ddt_scheme=ddt_scheme,
        cn_coeff=cn_coeff,
        grad_scheme_default=schemes_config.get("gradSchemeDefault", "Gauss linear"),
        grad_scheme_U=schemes_config.get("gradSchemeU", "cellLimited Gauss linear 1"),
        grad_scheme_k=schemes_config.get("gradSchemeK", ""),
        grad_scheme_epsilon=schemes_config.get("gradSchemeEpsilon", ""),
        div_scheme_U=schemes_config.get("divSchemeU", "bounded Gauss linearUpwind grad(U)"),
        div_scheme_T=schemes_config.get("divSchemeT", "bounded Gauss linearUpwind grad(U)"),
        div_scheme_k=schemes_config.get("divSchemeK", "bounded Gauss limitedLinear 1"),
        div_scheme_epsilon=schemes_config.get("divSchemeEpsilon", "bounded Gauss limitedLinear 1"),
        laplacian_scheme=schemes_config.get("laplacianScheme", "Gauss linear limited 0.667"),
        sn_grad_scheme=schemes_config.get("snGradScheme", "limited 0.667"),
    )
    (system_dir / "fvSchemes").write_text(content, encoding="utf-8")
    logger.info(
        f"Generated fvSchemes ({solver_type}): ddt={ddt_scheme}, "
        f"div(U)={schemes_config.get('divSchemeU', 'linearUpwind')}"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestFvSchemes -v --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/backend/aircooling/third_parties/foam/template/system/transient/fvSchemes.j2
git add src/backend/aircooling/runner/openfoam_case.py
git add src/backend/tests/test_template_params.py
git commit -m "feat(templates): convert transient fvSchemes to .j2 with ddtScheme dispatch"
```

Note: also `git rm` the old static `fvSchemes` file if git doesn't auto-detect the rename.

---

### Task 3: Convert transient fvSolution to Jinja2 template with PIMPLE params

**Files:**
- Modify: `src/backend/aircooling/third_parties/foam/template/system/transient/fvSolution` → rename to `fvSolution.j2`
- Modify: `src/backend/aircooling/runner/openfoam_case.py:155-204`
- Test: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Write the failing tests**

Add a new `TestTransientFvSolution` class in `src/backend/tests/test_template_params.py`:

```python
class TestTransientFvSolution:
    def test_transient_fv_solution_uses_pimple(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient")
        text = (system_dir / "fvSolution").read_text()
        assert "PIMPLE" in text
        assert "SIMPLE" not in text

    def test_pimple_default_params(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient")
        text = (system_dir / "fvSolution").read_text()
        assert "nOuterCorrectors    1;" in text
        assert "nCorrectors         2;" in text

    def test_pimple_custom_outer_correctors(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient",
                             n_outer_correctors=5, n_correctors=3)
        text = (system_dir / "fvSolution").read_text()
        assert "nOuterCorrectors    5;" in text
        assert "nCorrectors         3;" in text

    def test_pimple_no_relaxation_piso_mode(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient",
                             n_outer_correctors=1)
        text = (system_dir / "fvSolution").read_text()
        assert "relaxationFactors" not in text

    def test_pimple_relaxation_with_outer(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient",
                             n_outer_correctors=5,
                             relaxation_factors={"p_rgh": 0.7, "U": 0.3, "T": 0.3, "k_epsilon": 0.3})
        text = (system_dir / "fvSolution").read_text()
        assert "relaxationFactors" in text
        assert "0.7" in text

    def test_pimple_has_final_solvers(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir, solver_type="transient")
        text = (system_dir / "fvSolution").read_text()
        assert "p_rghFinal" in text
        assert "relTol          0;" in text

    def test_steady_fv_solution_unchanged(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_solution(system_dir,
                             residual_control={"p_rgh": 0.001, "U": 0.001, "T": 0.001, "k_epsilon": 0.001},
                             relaxation_factors={"p_rgh": 0.3, "U": 0.7, "T": 0.7, "k_epsilon": 0.3})
        text = (system_dir / "fvSolution").read_text()
        assert "SIMPLE" in text
        assert "PIMPLE" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTransientFvSolution -v --tb=short`
Expected: FAIL — `generate_fv_solution()` does not accept `solver_type` parameter.

- [ ] **Step 3: Rename the transient fvSolution to .j2 and write template**

```bash
mv src/backend/aircooling/third_parties/foam/template/system/transient/fvSolution \
   src/backend/aircooling/third_parties/foam/template/system/transient/fvSolution.j2
```

Write `src/backend/aircooling/third_parties/foam/template/system/transient/fvSolution.j2`:

```jinja2
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
    object      fvSolution;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

solvers
{
    p_rgh
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-8;
        relTol          0.01;
    }

    p_rghFinal
    {
        $p_rgh;
        relTol          0;
    }

    "(U|T|k|epsilon|R)"
    {
        solver          PBiCGStab;
        preconditioner  DILU;
        tolerance       1e-6;
        relTol          0.01;
    }

    "(U|T|k|epsilon|R)Final"
    {
        $U;
        relTol          0;
    }
}

PIMPLE
{
    momentumPredictor   no;
    nOuterCorrectors    {{ n_outer_correctors }};
    nCorrectors         {{ n_correctors }};
    nNonOrthogonalCorrectors {{ n_non_ortho }};
    pRefCell            0;
    pRefValue           0;
}

{% if n_outer_correctors > 1 %}
relaxationFactors
{
    fields
    {
        p_rgh           {{ relax_p_rgh | default(0.7) }};
    }
    equations
    {
        U               {{ relax_U | default(0.3) }};
        T               {{ relax_T | default(0.3) }};
        "(k|epsilon)"   {{ relax_k_epsilon | default(0.3) }};
    }
}
{% endif %}

// ************************************************************************* //
```

- [ ] **Step 4: Update generate_fv_solution() in openfoam_case.py**

Replace the current `generate_fv_solution` function (lines 155-204) with:

```python
def generate_fv_solution(
    system_dir: Path,
    residual_control: Optional[Dict[str, Any]] = None,
    relaxation_factors: Optional[Dict[str, Any]] = None,
    n_non_ortho_correctors: int = 0,
    pref_point: Optional[Tuple[float, float, float]] = None,
    solver_type: str = "steady",
    n_outer_correctors: int = 1,
    n_correctors: int = 2,
) -> None:
    """Generate fvSolution. Dispatches to SIMPLE (steady) or PIMPLE (transient) template."""
    system_dir.mkdir(parents=True, exist_ok=True)

    if solver_type == "transient":
        relaxation_factors = relaxation_factors or {}
        template = template_env.get_template("foam/template/system/transient/fvSolution.j2")
        content = template.render(
            n_outer_correctors=n_outer_correctors,
            n_correctors=n_correctors,
            n_non_ortho=n_non_ortho_correctors,
            relax_p_rgh=relaxation_factors.get("p_rgh", 0.7),
            relax_U=relaxation_factors.get("U", 0.3),
            relax_T=relaxation_factors.get("T", 0.3),
            relax_k_epsilon=relaxation_factors.get("k_epsilon", 0.3),
        )
        (system_dir / "fvSolution").write_text(content, encoding="utf-8")
        logger.info(
            f"Generated fvSolution (transient/PIMPLE): nOuterCorrectors={n_outer_correctors}, "
            f"nCorrectors={n_correctors}, nNonOrtho={n_non_ortho_correctors}"
        )
        return

    # --- Steady path (unchanged) ---
    residual_control = residual_control or {}
    res_p_rgh = residual_control.get("p_rgh", 1e-4)
    res_u = residual_control.get("U", 1e-5)
    res_t = residual_control.get("T", 1e-5)
    res_k_epsilon = residual_control.get("k_epsilon", 1e-4)

    relaxation_factors = relaxation_factors or {}
    relax_p = relaxation_factors.get("p_rgh", 0.3)
    relax_u = relaxation_factors.get("U", 0.7)
    relax_t = relaxation_factors.get("T", 0.7)
    relax_ke = relaxation_factors.get("k_epsilon", 0.3)

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
        f"Generated fvSolution (steady/SIMPLE): residualControl p_rgh={res_p_rgh}, "
        f"U={res_u}, T={res_t}, k/epsilon={res_k_epsilon}, "
        f"relaxation p_rgh={relax_p}, U={relax_u}, T={relax_t}, k/eps={relax_ke}, "
        f"nNonOrthCorr={n_non_ortho_correctors}, pRefPoint=({px}, {py}, {pz})"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestTransientFvSolution src/backend/tests/test_template_params.py::TestFvSolution -v --tb=short`
Expected: ALL PASS (both transient and existing steady tests)

- [ ] **Step 6: Commit**

```bash
git add src/backend/aircooling/third_parties/foam/template/system/transient/fvSolution.j2
git add src/backend/aircooling/runner/openfoam_case.py
git add src/backend/tests/test_template_params.py
git commit -m "feat(templates): convert transient fvSolution to .j2 with PIMPLE params"
```

Note: also `git rm` the old static `fvSolution` file if git doesn't auto-detect the rename.

---

### Task 4: Update controlDict for transient-specific parameters

**Files:**
- Modify: `src/backend/aircooling/third_parties/foam/template/system/transient/controlDict.j2:48-50`
- Modify: `src/backend/aircooling/runner/openfoam_case.py:90-152`
- Test: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Write the failing tests**

Add to `TestControlDict` class in `test_template_params.py`:

```python
    def test_transient_control_dict_seconds(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "endTimeSeconds": 120.0,
            "deltaT": 0.05,
            "writeIntervalSeconds": 5.0,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "endTime         120" in text
        assert "deltaT          0.05" in text
        assert "writeInterval   5" in text

    def test_transient_max_delta_t(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "maxDeltaT": 2.5,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "maxDeltaT" in text
        assert "2.5" in text

    def test_transient_max_co(self, tmp_path):
        generate_control_dict(tmp_path, {
            "solverType": "transient",
            "maxCo": 0.5,
        })
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "maxCo" in text
        assert "0.5" in text

    def test_transient_no_warning_log(self, tmp_path, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            generate_control_dict(tmp_path, {"solverType": "transient"})
        assert "not yet implemented" not in caplog.text

    def test_steady_endtime_still_integer(self, tmp_path):
        generate_control_dict(tmp_path, {"endTime": 2000, "writeInterval": 500})
        text = (tmp_path / "system" / "controlDict").read_text()
        assert "endTime         2000;" in text
        assert "writeInterval   500;" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestControlDict::test_transient_control_dict_seconds src/backend/tests/test_template_params.py::TestControlDict::test_transient_max_delta_t -v --tb=short`
Expected: FAIL — controlDict still uses integer endTime and has no maxDeltaT.

- [ ] **Step 3: Update transient controlDict template**

In `src/backend/aircooling/third_parties/foam/template/system/transient/controlDict.j2`, replace the hardcoded `maxCo` line (line 50) and add `maxDeltaT`:

Replace:
```
maxCo           1;
```
With:
```jinja2
maxCo           {{ max_co }};

maxDeltaT       {{ max_delta_t }};
```

- [ ] **Step 4: Update generate_control_dict() in openfoam_case.py**

Replace lines 100-112 and 135-149 with transient-aware parameter extraction:

```python
    solver_type = _cfg_value(simulation_config, "solver_type", "solverType", "steady") or "steady"

    # --- Precision (shared) ---
    wp_str = _cfg_value(simulation_config, "write_precision", "writePrecision", None)
    if wp_str in ("single", "double"):
        double_prec = wp_str == "double"
    else:
        double_prec = bool(
            _cfg_value(simulation_config, "double_precision_write", "doublePrecisionWrite", False)
        )
    write_precision = 10 if double_prec else 6
    time_precision = 10 if double_prec else 6

    # --- Time/write params (solver-type dependent) ---
    if solver_type == "transient":
        end_time = float(_cfg_value(simulation_config, "end_time_seconds", "endTimeSeconds", 300.0) or 300.0)
        delta_t = float(_cfg_value(simulation_config, "delta_t", "deltaT", 0.1) or 0.1)
        write_interval = float(_cfg_value(simulation_config, "write_interval_seconds", "writeIntervalSeconds", 10.0) or 10.0)
        max_delta_t = float(_cfg_value(simulation_config, "max_delta_t", "maxDeltaT", 1.0) or 1.0)
        max_co = float(_cfg_value(simulation_config, "max_co", "maxCo", 1.0) or 1.0)
    else:
        end_time = int(_cfg_value(simulation_config, "end_time", "endTime", 500) or 500)
        write_interval = int(_cfg_value(simulation_config, "write_interval", "writeInterval", 100) or 100)
        delta_t = 1
        max_delta_t = 1.0
        max_co = 1.0
```

Update the template render call to pass the new variables:

```python
    content = template.render(
        delta_t=delta_t,
        write_interval=write_interval,
        write_precision=write_precision,
        time_precision=time_precision,
        end_time=end_time,
        max_delta_t=max_delta_t,
        max_co=max_co,
        probes=probes,
        surface_field_values=surface_field_values,
    )
    (system_dir / "controlDict").write_text(content, encoding="utf-8")
```

Remove the WARNING log block (lines 145-149 that say "Full PIMPLE support is not yet implemented").

- [ ] **Step 5: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py::TestControlDict -v --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/backend/aircooling/third_parties/foam/template/system/transient/controlDict.j2
git add src/backend/aircooling/runner/openfoam_case.py
git add src/backend/tests/test_template_params.py
git commit -m "feat(controlDict): add transient params (endTimeSeconds, maxDeltaT, maxCo)"
```

---

### Task 5: Wire up solver_type dispatch in stlgen_runner.py

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py:1159-1191`

- [ ] **Step 1: Update _setup_solver_dicts() to pass solver_type**

Replace the `_setup_solver_dicts` method body (lines 1159-1191) to pass `solver_type` to `generate_fv_solution` and `generate_fv_schemes`:

```python
        """Generate controlDict and fvSolution in the given run directory."""
        sc = self._data.get("simulationConfig", {})
        solver_type = sc.get("solverType", "steady") or "steady"

        monitoring = sc.get("monitoring", {})
        monitor_patches = [p["patch"] for p in monitoring.get("serverTemperature", [])]
        generate_control_dict(
            run_dir,
            sc,
            probes=self._extract_sensor_points(),
            monitor_patches=monitor_patches,
        )

        residual_control = sc.get("residualControl", {})
        system_dir = run_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        generate_fv_solution(
            system_dir,
            residual_control=residual_control,
            relaxation_factors=sc.get("relaxationFactors", {}),
            n_non_ortho_correctors=int(sc.get("nNonOrthogonalCorrectors", 0)),
            solver_type=solver_type,
            n_outer_correctors=int(sc.get("nOuterCorrectors", 1)),
            n_correctors=int(sc.get("nCorrectors", 2)),
        )

        schemes_config = dict(sc.get("schemes", {}))
        schemes_config.setdefault("ddtScheme", sc.get("ddtScheme", "Euler"))
        schemes_config.setdefault("crankNicolsonCoeff", sc.get("crankNicolsonCoeff", 0.9))
        generate_fv_schemes(system_dir, schemes_config, solver_type=solver_type)

        logger.info(f"Generated solver dicts (controlDict, fvSolution, fvSchemes) in {run_dir / 'system'}")
```

- [ ] **Step 2: Run full test suite to verify nothing is broken**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -v --tb=short`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(runner): pass solver_type to fvSolution/fvSchemes generators"
```

---

### Task 6: Frontend — transient parameter panel with i18n

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (lines ~251, ~2990, ~26980, ~29727)
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add i18n keys to both locale files**

In `web/i18n/zh-CN.json`, after `"label.write_precision":`:

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
  "option.crank_nicolson": "CrankNicolson (二阶)",
```

In `web/i18n/en-US.json`, after `"label.write_precision":`:

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
  "option.crank_nicolson": "CrankNicolson (2nd order)",
```

- [ ] **Step 2: Add default transient fields to getInitialState()**

In `web/STL_generate.html`, in the `cfdRoom` defaults block (around line 2990), after `writePrecision: 'single',`:

```javascript
                endTimeSeconds: 300,
                deltaT: 0.1,
                maxDeltaT: 1.0,
                maxCo: 1.0,
                writeIntervalSeconds: 10,
                nOuterCorrectors: 1,
                nCorrectors: 2,
                ddtScheme: 'Euler',
                crankNicolsonCoeff: 0.9,
```

- [ ] **Step 3: Add transient parameter panel HTML**

In `web/STL_generate.html`, after the write precision `<div class="prop-row">` (around line 256), before the parallel solve row, insert:

```html
                <div id="transient-params" style="display:none;">
                <div class="prop-row"><label data-i18n="label.end_time_seconds">总物理时间 (s)</label><input type="number" id="slv-end-time-s" step="10" min="1" value="300" oninput="window.setSolverConfig('endTimeSeconds', parseFloat(this.value))"></div>
                <div class="prop-row"><label data-i18n="label.delta_t">初始时间步 (s)</label><input type="number" id="slv-delta-t" step="0.01" min="0.001" value="0.1" oninput="window.setSolverConfig('deltaT', parseFloat(this.value))"></div>
                <div class="prop-row"><label data-i18n="label.max_delta_t">最大时间步 (s)</label><input type="number" id="slv-max-delta-t" step="0.1" min="0.01" value="1.0" oninput="window.setSolverConfig('maxDeltaT', parseFloat(this.value))"></div>
                <div class="prop-row"><label data-i18n="label.max_courant">最大Courant数</label><input type="number" id="slv-max-co" step="0.1" min="0.1" value="1.0" oninput="window.setSolverConfig('maxCo', parseFloat(this.value))"></div>
                <div class="prop-row"><label data-i18n="label.write_interval_seconds">写入间隔 (s)</label><input type="number" id="slv-write-interval-s" step="1" min="1" value="10" oninput="window.setSolverConfig('writeIntervalSeconds', parseFloat(this.value))"></div>
                <div style="border-top:1px solid var(--border); margin:4px 0;"></div>
                <div class="prop-row"><label data-i18n="label.ddt_scheme">时间离散格式</label>
                    <select id="slv-ddt-scheme" onchange="window.setSolverConfig('ddtScheme', this.value); window._toggleCNCoeff()" style="flex:1; max-width:160px;">
                        <option value="Euler" data-i18n="option.euler">Euler (一阶)</option>
                        <option value="backward" data-i18n="option.backward">backward (二阶)</option>
                        <option value="CrankNicolson" data-i18n="option.crank_nicolson">CrankNicolson (二阶)</option>
                    </select>
                </div>
                <div class="prop-row" id="cn-coeff-row" style="display:none;"><label data-i18n="label.cn_coeff">CN系数</label><input type="number" id="slv-cn-coeff" step="0.05" min="0" max="1" value="0.9" oninput="window.setSolverConfig('crankNicolsonCoeff', parseFloat(this.value))"></div>
                <div style="border-top:1px solid var(--border); margin:4px 0;"></div>
                <div class="prop-row"><label data-i18n="label.n_outer_correctors">PIMPLE外迭代</label>
                    <select id="slv-n-outer" onchange="window.setSolverConfig('nOuterCorrectors', parseInt(this.value))" style="flex:1; max-width:80px;">
                        <option value="1">1 (PISO)</option><option value="2">2</option><option value="3">3</option><option value="5">5</option><option value="10">10</option><option value="50">50</option>
                    </select>
                </div>
                <div class="prop-row"><label data-i18n="label.n_correctors">压力修正步数</label>
                    <select id="slv-n-correctors" onchange="window.setSolverConfig('nCorrectors', parseInt(this.value))" style="flex:1; max-width:80px;">
                        <option value="1">1</option><option value="2" selected>2</option><option value="3">3</option>
                    </select>
                </div>
                </div>
```

- [ ] **Step 4: Add toggle functions and update setSolverType**

In `web/STL_generate.html`, after the existing `setSolverType` function (around line 26984), add:

```javascript
    window._toggleTransientParams = function() {
        const isTransient = state.cfdRoom.solverType === 'transient';
        const tp = document.getElementById('transient-params');
        if (tp) tp.style.display = isTransient ? '' : 'none';
        const et = document.getElementById('slv-endtime');
        if (et) et.closest('.prop-row').style.display = isTransient ? 'none' : '';
        const wi = document.getElementById('slv-writeinterval');
        if (wi) wi.closest('.prop-row').style.display = isTransient ? 'none' : '';
    };
    window._toggleCNCoeff = function() {
        const row = document.getElementById('cn-coeff-row');
        if (row) row.style.display = (document.getElementById('slv-ddt-scheme').value === 'CrankNicolson') ? '' : 'none';
    };
```

Update the existing `setSolverType` function to call `_toggleTransientParams`:

```javascript
    window.setSolverType = function(type) {
        pushUndoCheckpoint('setSolverType');
        state.cfdRoom.solverType = type;
        window._toggleTransientParams();
        logOperation('setSolverType', t('log.solver_type', { type }), { type }, 'modify');
    };
```

- [ ] **Step 5: Update _syncSolverPanelInputs for transient fields**

In the `_syncSolverPanelInputs` function (around line 27008, after the solver type radio buttons), add:

```javascript
        // Transient params
        set('slv-end-time-s', cr.endTimeSeconds || 300);
        set('slv-delta-t', cr.deltaT || 0.1);
        set('slv-max-delta-t', cr.maxDeltaT || 1.0);
        set('slv-max-co', cr.maxCo || 1.0);
        set('slv-write-interval-s', cr.writeIntervalSeconds || 10);
        { const ddtEl = document.getElementById('slv-ddt-scheme');
          if (ddtEl) ddtEl.value = cr.ddtScheme || 'Euler'; }
        set('slv-cn-coeff', cr.crankNicolsonCoeff || 0.9);
        { const noEl = document.getElementById('slv-n-outer');
          if (noEl) noEl.value = String(cr.nOuterCorrectors || 1); }
        { const ncEl = document.getElementById('slv-n-correctors');
          if (ncEl) ncEl.value = String(cr.nCorrectors || 2); }
        window._toggleTransientParams();
        window._toggleCNCoeff();
```

- [ ] **Step 6: Update export (simulationConfig block)**

In the `simulationConfig` export block (around line 29731), after `writePrecision:`, add:

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

- [ ] **Step 7: Commit (submodule)**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat(ui): add transient parameter panel with i18n (Euler/backward/CN)"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "chore: update STLGeneratorForOpenFoam submodule (transient UI)"
```

---

### Task 7: Full integration test and cleanup

**Files:**
- Test: `src/backend/tests/test_template_params.py`

- [ ] **Step 1: Run the complete test suite**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/ -v --tb=short 2>&1 | tail -30`
Expected: ALL PASS, zero failures

- [ ] **Step 2: Verify old static transient files are removed**

```bash
ls src/backend/aircooling/third_parties/foam/template/system/transient/
```

Expected: Only `.j2` files remain: `controlDict.j2`, `fvSchemes.j2`, `fvSolution.j2`

If old non-.j2 files still exist:
```bash
git rm src/backend/aircooling/third_parties/foam/template/system/transient/fvSchemes
git rm src/backend/aircooling/third_parties/foam/template/system/transient/fvSolution
git commit -m "chore: remove old static transient template files"
```

- [ ] **Step 3: Verify steady path is completely unchanged**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_template_params.py -k "steady or fallback or default" -v --tb=short`
Expected: ALL PASS — steady path produces bit-identical output as before.

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git add -A
git status  # verify only expected files
git commit -m "chore: final cleanup for transient CFD support"
```
