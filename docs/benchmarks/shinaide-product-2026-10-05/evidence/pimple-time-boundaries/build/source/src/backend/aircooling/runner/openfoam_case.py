"""
Shared OpenFOAM case setup helpers used by both legacy and STLGen runners.
"""
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from backend.aircooling.utils import template_env


def _cfg_value(config: Dict[str, Any], snake_key: str, camel_key: str, default: Any) -> Any:
    """Read either snake_case or camelCase config keys."""
    if snake_key in config:
        return config.get(snake_key)
    if camel_key in config:
        return config.get(camel_key)
    return default


def read_boundary_patches(boundary_path: Path) -> List[Tuple[str, str]]:
    """Read boundary patches from an OpenFOAM boundary file."""
    if not boundary_path.exists():
        return []

    content = boundary_path.read_text(encoding="utf-8")
    patches = []
    pattern = r"([A-Za-z0-9_]+)\s*\{\s*type\s+([A-Za-z0-9_]+)\s*;"
    for name, patch_type in re.findall(pattern, content):
        patches.append((name, patch_type))
    return patches


_CYCLIC_TYPE = re.compile(r"^\s*type\s+cyclic\s*;")
_MATCH_TOLERANCE = "        matchTolerance  0.01;"


def _normalize_cyclic_block(block: List[str]) -> List[str]:
    """One cyclic patch dict: coincidentFullMatch, no wall group, exactly one
    matchTolerance 0.01 right after neighbourPatch (or before the brace)."""
    out: List[str] = []
    for line in block:
        if "matchTolerance" in line:
            continue
        if "inGroups" in line and "wall" in line:
            continue
        out.append(line.replace("transform       unknown;", "transform       coincidentFullMatch;"))
    at = next((i + 1 for i, line in enumerate(out) if "neighbourPatch" in line), len(out) - 1)
    out.insert(at, _MATCH_TOLERANCE)
    return out


def fix_cyclic_match_tolerance(boundary_path: Path) -> None:
    """Normalize cyclic patch metadata created by createBaffles or dcCart.

    Idempotent by construction: mesh-only normalises and then fingerprints the
    mesh, solve-only normalises again and checks that fingerprint. Each cyclic
    patch ends with exactly one ``matchTolerance 0.01`` whatever the mesher
    wrote (dcCart puts its own value before neighbourPatch, createBaffles after
    it); the old line-by-line version added and deleted a second entry on
    alternate calls, so every case with cyclic panels failed solve-after-mesh.
    """
    if not boundary_path.exists():
        return

    content = boundary_path.read_text(encoding="utf-8")
    if not re.search(r"type\s+cyclic\s*;", content):
        return

    lines = content.split("\n")
    new_lines: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "{" and i > 0 and new_lines and new_lines[-1].strip() and \
                not new_lines[-1].strip().startswith(("FoamFile", "(")):
            depth, j = 0, i
            while j < len(lines):
                stripped = lines[j].strip()
                depth += stripped == "{"
                depth -= stripped == "}"
                if depth == 0:
                    break
                j += 1
            block = lines[i:j + 1]
            if any(_CYCLIC_TYPE.match(b) for b in block):
                block = _normalize_cyclic_block(block)
            new_lines.extend(block)
            i = j + 1
            continue
        new_lines.append(line)
        i += 1

    updated = "\n".join(new_lines)
    if updated != content:
        boundary_path.write_text(updated, encoding="utf-8")
        logger.info("Fixed cyclic patches: transform→coincidentFullMatch, matchTolerance=0.01")


def generate_control_dict(
    run_dir: Path,
    simulation_config: Dict[str, Any],
    probes: Optional[List[Dict[str, float]]] = None,
    monitor_patches: Optional[List[str]] = None,
    transient_events: Optional[List[float]] = None,
) -> None:
    """Generate controlDict with probes and optional surface monitoring."""
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

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
    adjust_time_step = True
    start_from = "latestTime"
    if solver_type == "transient":
        end_time = float(_cfg_value(simulation_config, "end_time_seconds", "endTimeSeconds", 300.0) or 300.0)
        delta_t = float(_cfg_value(simulation_config, "delta_t", "deltaT", 0.1) or 0.1)
        max_delta_t = float(_cfg_value(simulation_config, "max_delta_t", "maxDeltaT", 1.0) or 1.0)
        max_co = float(_cfg_value(simulation_config, "max_co", "maxCo", 1.0) or 1.0)
        adjust_val = _cfg_value(simulation_config, "adjust_time_step", "adjustTimeStep", True)
        adjust_time_step = adjust_val is not False and adjust_val != False
        sf_val = _cfg_value(simulation_config, "start_from", "startFrom", "latestTime")
        if sf_val in ("latestTime", "startTime"):
            start_from = sf_val
        if adjust_time_step:
            write_interval = float(_cfg_value(simulation_config, "write_interval_seconds", "writeIntervalSeconds", 10.0) or 10.0)
        else:
            write_interval = int(_cfg_value(simulation_config, "write_interval", "writeInterval", 100) or 100)
    else:
        end_time = int(_cfg_value(simulation_config, "end_time", "endTime", 500) or 500)
        write_interval = int(_cfg_value(simulation_config, "write_interval", "writeInterval", 100) or 100)
        delta_t = 1
        max_delta_t = 1.0
        max_co = 1.0

    # Clamp only when both values share the same units.
    # Fixed-dt transient: writeInterval=steps but endTime=seconds — skip.
    same_units = not (solver_type == "transient" and not adjust_time_step)
    if same_units and write_interval > end_time:
        logger.warning(
            f"write_interval ({write_interval}) > end_time ({end_time}), "
            f"adjusting write_interval to {end_time}"
        )
        write_interval = end_time

    from .transient_time import normalize_time_events
    events = (normalize_time_events(transient_events, end_time=end_time)
              if solver_type == "transient" else [])

    probes = probes or []
    monitor_patches = monitor_patches or []

    surface_field_values = []
    for patch_name in monitor_patches:
        fo_name = "avgT_" + patch_name.replace(" ", "_")
        surface_field_values.append({"name": fo_name, "patch": patch_name})

    template_path = (
        "foam/template/system/transient/controlDict.j2"
        if solver_type == "transient"
        else "foam/template/system/steady/controlDict.j2"
    )
    # FO writeInterval: write every step for real-time monitoring.
    # adjustableRunTime → deltaT (physical time); timeStep → 1 (every step).
    fo_write_interval = delta_t if adjust_time_step else 1

    template = template_env.get_template(template_path)
    content = template.render(
        delta_t=delta_t,
        write_interval=write_interval,
        write_precision=write_precision,
        time_precision=time_precision,
        end_time=end_time,
        transient_events=[repr(t) for t in events if t > 0],
        max_delta_t=max_delta_t,
        max_co=max_co,
        adjust_time_step=adjust_time_step,
        start_from=start_from,
        fo_write_interval=fo_write_interval,
        probes=probes,
        surface_field_values=surface_field_values,
    )
    # Dataset builds set DCDTUNI_RUNTIME_MODIFIABLE=false so OpenFOAM does NOT
    # re-read controlDict mid-solve, preventing the spurious early-stop
    # (controlDict re-read -> End) seen in long batch solves. Default unchanged.
    if os.environ.get("DCDTUNI_RUNTIME_MODIFIABLE") == "false":
        content = re.sub(r"runTimeModifiable\s+\w+\s*;", "runTimeModifiable false;", content)
    (system_dir / "controlDict").write_text(content, encoding="utf-8")
    logger.info(
        f"Generated controlDict ({solver_type}): endTime={end_time}, writeInterval={write_interval}, probes={len(probes)}"
    )


# Per-time-step outer-loop tolerances of a transient run when the project has
# none (E8c). p_rgh is normalised an order higher than the others in every
# healthy log; relTol is 0, so each step converges to an absolute level.
TRANSIENT_RESIDUAL_DEFAULTS: Dict[str, float] = {"p_rgh": 1e-3, "U": 1e-4, "T": 1e-4, "k_epsilon": 1e-4}


def generate_fv_solution(
    system_dir: Path,
    residual_control: Optional[Dict[str, Any]] = None,
    relaxation_factors: Optional[Dict[str, Any]] = None,
    n_non_ortho_correctors: int = 0,
    pref_point: Optional[Tuple[float, float, float]] = None,
    solver_type: str = "steady",
    n_outer_correctors: int = 1,
    n_correctors: int = 2,
    transient_residual_control: Optional[Dict[str, Any]] = None,
    pressure_linear_solver: Optional[Dict[str, Any]] = None,
) -> None:
    """Generate fvSolution with configurable residual thresholds and pRefPoint.

    A transient run with more than one outer corrector converges each time step:
    ``residualControl`` inside ``PIMPLE`` (OpenFOAM v2412) checks the fields
    actually present in solverPerformance. With momentumPredictor=no there is
    no separate U solve; turbulence residuals update when turbulence corrects.
    ``nOuterCorrectors`` is the per-step maximum.
    """
    system_dir.mkdir(parents=True, exist_ok=True)
    from .cfdrun_schema import PressureLinearSolverConfig
    pressure = PressureLinearSolverConfig.model_validate(pressure_linear_solver or {}).model_dump(exclude_none=True)
    pressure_settings = {
        "pressure_preconditioner": pressure.get("preconditioner", "DIC" if solver_type == "transient" else "GAMG"),
        "pressure_tolerance": pressure.get("tolerance", 1e-8 if solver_type == "transient" else 1e-6),
        "pressure_rel_tol": pressure.get("relTol", .01),
        "pressure_max_iter": pressure.get("maxIter", None if solver_type == "transient" else 200),
    }

    if solver_type == "transient":
        relaxation_factors = relaxation_factors or {}
        template = template_env.get_template("foam/template/system/transient/fvSolution.j2")
        step_tolerance = {**TRANSIENT_RESIDUAL_DEFAULTS,
                          **{k: float(v) for k, v in (transient_residual_control or {}).items() if v is not None}}
        content = template.render(
            **pressure_settings,
            n_outer_correctors=n_outer_correctors,
            n_correctors=n_correctors,
            n_non_ortho=n_non_ortho_correctors,
            step_tolerance=step_tolerance,
            relax_p_rgh=relaxation_factors.get("p_rgh", 0.7),
            relax_U=relaxation_factors.get("U", 0.3),
            relax_T=relaxation_factors.get("T", 0.3),
            relax_k_epsilon=relaxation_factors.get("k_epsilon", 0.3),
        )
        (system_dir / "fvSolution").write_text(content, encoding="utf-8")
        logger.info(
            f"Generated fvSolution (transient/PIMPLE): nOuterCorrectors={n_outer_correctors}, "
            f"nCorrectors={n_correctors}, nNonOrtho={n_non_ortho_correctors}"
            + (f", per-step residualControl={step_tolerance}" if n_outer_correctors > 1 else "")
        )
        return

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
        **pressure_settings,
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
        f"relaxation p_rgh={relax_p}, U={relax_u}, T={relax_t}, k/eps={relax_ke}, "
        f"nNonOrthCorr={n_non_ortho_correctors}, pRefPoint=({px}, {py}, {pz})"
    )


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
        f"Generated fvSchemes ({solver_type}): div(U)={schemes_config.get('divSchemeU', 'linearUpwind')}, "
        f"laplacian={schemes_config.get('laplacianScheme', 'limited 0.667')}"
    )


def generate_fv_options(run_dir: Path, leakage_zones: Dict[str, Any]) -> None:
    """Generate fvOptions with porous leakage zones."""
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    template = template_env.get_template("foam/template/system/share/fvOptions.j2")
    with open(system_dir / "fvOptions", "w", encoding="utf-8") as f:
        f.write(template.render(perforated_openings={}, leakage_zones=leakage_zones))

    if leakage_zones:
        logger.info(f"Generated fvOptions with {len(leakage_zones)} leakage zones")


def generate_thermophysical_properties(constant_dir: Path, physical_properties: Optional[Dict[str, Any]]) -> None:
    """Generate thermophysicalProperties using the legacy template."""
    physical_properties = physical_properties or {}
    template = template_env.get_template("foam/template/constant/thermophysicalProperties.j2")
    content = template.render(
        mol_weight=physical_properties.get("mol_weight", 28.9),
        cp=physical_properties.get("cp", 1005),
        hf=physical_properties.get("hf", 0),
        mu=physical_properties.get("mu", "1.8e-05"),
        pr=physical_properties.get("pr", 0.696),
    )
    (constant_dir / "thermophysicalProperties").write_text(content, encoding="utf-8")


def _write_gravity_file(constant_dir: Path, gravity: Dict[str, Any]) -> None:
    """Write custom gravity file for STLGen-driven runs.

    Default is Y-down (post-2026-04-18 Y-up refactor): (0, -9.81, 0).
    """
    gx = gravity.get("x", 0)
    gy = gravity.get("y", -9.81)
    gz = gravity.get("z", 0)
    g_content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       uniformDimensionedVectorField;
    location    "constant";
    object      g;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 1 -2 0 0 0 0];
value           ({gx} {gy} {gz});

// ************************************************************************* //
"""
    (constant_dir / "g").write_text(g_content, encoding="utf-8")
    logger.info(f"Gravity: ({gx}, {gy}, {gz})")


def _write_transport_properties(constant_dir: Path, room_temperature_c: float) -> None:
    """Write transportProperties with TRef derived from room temperature."""
    t_ref_k = room_temperature_c + 273.15
    beta = 1.0 / t_ref_k
    tp_content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v1912                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      transportProperties;
}}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

transportModel Newtonian;

// Laminar kinematic viscosity (air, mu/rho = 1.8e-5/1.19)
nu              1.513e-05;

// Thermal expansion coefficient
beta            {beta:.6e};

// Reference temperature ({room_temperature_c}°C)
TRef            {t_ref_k};

// Laminar Prandtl number (mu*Cp/k = 1.8e-5*1005/0.026)
Pr              0.696;

// Turbulent Prandtl number
Prt             0.85;

// ************************************************************************* //
"""
    (constant_dir / "transportProperties").write_text(tp_content, encoding="utf-8")
    logger.info(f"transportProperties: TRef={t_ref_k} K ({room_temperature_c}°C), beta={beta:.6e}")


def copy_system_files_for_solver(
    run_dir: Path,
    *,
    physical_properties: Optional[Dict[str, Any]] = None,
    gravity: Optional[Dict[str, Any]] = None,
    room_temperature_c: Optional[float] = None,
    schemes_config: Optional[Dict[str, Any]] = None,
    solver_type: str = "steady",
) -> None:
    """Copy/generate constant and system files needed by the solver."""
    template_root = Path(__file__).resolve().parents[1] / "third_parties"
    constant_dir = run_dir / "constant"
    constant_dir.mkdir(parents=True, exist_ok=True)
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    template = template_env.get_template("foam/template/constant/turbulenceProperties.j2")
    turb_content = template.render(turbulence="on")
    (constant_dir / "turbulenceProperties").write_text(turb_content, encoding="utf-8")

    if room_temperature_c is None:
        trans_props = template_root / "foam" / "template" / "constant" / "transportProperties"
        if trans_props.exists():
            shutil.copy(trans_props, constant_dir / "transportProperties")
    else:
        _write_transport_properties(constant_dir, room_temperature_c)

    if gravity is None:
        g_file = template_root / "foam" / "template" / "constant" / "g"
        if g_file.exists():
            shutil.copy(g_file, constant_dir / "g")
    else:
        _write_gravity_file(constant_dir, gravity)

    generate_fv_schemes(system_dir, schemes_config, solver_type=solver_type)

    generate_thermophysical_properties(constant_dir, physical_properties)


def _hierarchical_split(n_procs: int) -> str:
    """Factor *n_procs* into an (nx ny nz) triple for hierarchical decomposition.

    Splits the largest remaining factor of 2 onto the currently smallest axis, so
    e.g. 8 -> (2 2 2), 16 -> (4 2 2), 6 -> (3 2 1). Any leftover odd factor lands
    on x. Only used for the pre-snappy decomposition of a box-shaped background
    grid, where slab-shaped subdomains are as good as a graph partition.
    """
    dims = [1, 1, 1]
    remaining = n_procs
    for factor in (2, 3, 5, 7):
        while remaining % factor == 0:
            dims[dims.index(min(dims))] *= factor
            remaining //= factor
    if remaining > 1:
        dims[dims.index(min(dims))] *= remaining
    dims.sort(reverse=True)
    return " ".join(str(d) for d in dims)


def generate_decompose_par_dict(
    run_dir: Path,
    n_procs: int,
    preserve_face_zones: bool = False,
    method: str = "scotch",
) -> None:
    """Generate decomposeParDict for parallel execution.

    When *preserve_face_zones* is set, add a ``preserveFaceZones`` constraint
    that keeps the owner and neighbour of each zone face on the same rank. This is
    REQUIRED before a parallel ``createBaffles`` run: otherwise faceZone faces
    that straddle a processor boundary are silently dropped from the resulting
    baffles (verified: a raised-floor / wall baffle loses ~3 % of its faces),
    which punches holes in the porous/wall jump and corrupts the airflow. The
    pre-snappy decomposition leaves this off (the zones do not exist yet); the
    pipeline re-decomposes via ``redistributePar`` with this flag once snappy
    has created the zones.
    """
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    # `scotch` builds a graph partition. On the uniform background grid that the
    # pre-snappy decomposition sees that is wasted effort — measured on
    # Base_leakage it reported "Finished decomposition in 5.51 s" for a 29,000
    # cell block, while `hierarchical` does the same split in well under a second
    # and hands snappy contiguous slabs. Solver-stage decomposition keeps scotch.
    if method == "hierarchical":
        method_block = (
            f"method          hierarchical;\n\n"
            f"coeffs\n{{\n    n           ({_hierarchical_split(n_procs)});\n}}\n"
        )
    else:
        method_block = f"method          {method};\n"

    constraints = (
        """
constraints
{
    preserveBaffleFaceZones
    {
        type    preserveFaceZones;
        zones   ( ".*" );
    }
}
"""
        if preserve_face_zones
        else ""
    )

    content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2406                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      decomposeParDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

numberOfSubdomains {n_procs};

{method_block}{constraints}
// ************************************************************************* //
"""
    (system_dir / "decomposeParDict").write_text(content, encoding="utf-8")
