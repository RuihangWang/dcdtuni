"""
OpenFOAM field file generation for CFD cases.
"""
import json
import os
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from backend.aircooling.runner.bridge_bc import BridgeBCRegistry
    from backend.aircooling.runner.stlgen_runner import PatchGeometry

from loguru import logger
import numpy as np

from backend.aircooling.runner.bridge_bc import BCSpec, perforated_to_porous_baffle
from backend.aircooling.utils.const import rho_air, air_specific_heat


# A step in a schedule is two rows at one time (cfdrun v3 corner rows, design
# §1.2). OpenFOAM v2412 refuses that ("out-of-order value", probed 2026-09-30),
# so the old value is written just before: (t − ε, v_old)(t, v_new). The table
# is linear and clamps by default, so at t it already reads v_new.
TABLE_STEP_EPSILON_S = 1e-6

# Receipt of the sealed-region ACU airflow balance, written next to 0/ and read
# by the boundary-condition audit (bc_audit.py).
ACU_BALANCE_FILE = "acu-airflow-balance.json"


def _strict_rows(entries):
    """[(t, value)] strictly increasing in t, a step's first row moved to t − ε."""
    rows = []
    for e in entries:
        t = e["t"] if isinstance(e, dict) else (e.t if hasattr(e, "t") else e[0])
        v = e["value"] if isinstance(e, dict) else (e.value if hasattr(e, "value") else e[1])
        rows.append([t, v])
    for i in range(len(rows) - 1):
        if rows[i + 1][0] == rows[i][0]:
            if i + 2 < len(rows) and rows[i + 2][0] == rows[i][0]:
                raise ValueError(f"a schedule has more than two rows at t={rows[i][0]}; a step is at most two rows")
            # Relative for long runs, so t − ε stays distinct at the 10
            # digits a table-carrying run writes with.
            rows[i][0] = rows[i][0] - max(TABLE_STEP_EPSILON_S, abs(rows[i][0]) * 1e-8)
    for i in range(1, len(rows)):
        if not rows[i][0] > rows[i - 1][0]:
            raise ValueError(f"table rows must increase in time (t={rows[i][0]} after {rows[i - 1][0]})")
    return [(t, v) for t, v in rows]


def _format_value(v):
    if isinstance(v, (list, tuple)):
        return "(" + " ".join(str(x) for x in v) + ")"
    return str(v)


def _format_time_table(entries, transform_fn=None, inline=False):
    """Convert [{t, value}, ...] to OpenFOAM Function1 table string.

    Each *entry* is either a dict with keys ``t`` and ``value`` or an object
    with corresponding attributes.  An optional *transform_fn* is applied to
    each value before formatting (e.g. Celsius → Kelvin). A value may be a
    3-vector. Two rows at one time (a step) are written in the ε form.

    When *inline* is True, produce a single-line format suitable for embedding
    inside OpenFOAM expression variable strings (no newlines).
    """
    pairs = []
    for t, v in _strict_rows(entries):
        if transform_fn:
            v = transform_fn(v)
        pairs.append(f"({t} {_format_value(v)})")
    if inline:
        return "table ((" + ") (".join(p[1:-1] for p in pairs) + "))"
    lines = ["table", "        ("]
    for p in pairs:
        lines.append(f"            {p}")
    lines.append("        )")
    return "\n".join(lines)


def _number(x):
    """A literal an expression can take; negatives parenthesised."""
    return repr(float(x)) if x >= 0 else f"({float(x)!r})"


def _build_piecewise_linear_expression(entries, scale=1.0):
    """A time() expression that follows corner rows LINEARLY, clamped at both
    ends — ramps included, steps via the ε form (cfdrun v3 schedules).
    For use inside OpenFOAM expression BCs, where a table Function1 cannot be
    used in a variable.
    """
    points = [(t, v * scale) for t, v in _strict_rows(entries)]
    if len(points) == 1:
        return _number(points[0][1])
    expr = _number(points[-1][1])
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        segment = _number(v0) if v0 == v1 else (
            f"({_number(v0)}+{_number(v1 - v0)}*(time()-{_number(t0)})/{_number(t1 - t0)})")
        expr = f"(time()<{_number(t1)} ? {segment} : {expr})"
    if points[0][0] > 0:
        expr = f"(time()<{_number(points[0][0])} ? {_number(points[0][1])} : {expr})"
    return expr


def strip_time_varying(imported_bc_data):
    """The same boundary data without schedules — what a steady stage (SIMPLE,
    where time() is the iteration count) must be generated from."""
    stripped = {}
    for patch, info in imported_bc_data.items():
        info = dict(info)
        info["timeVarying"] = {}
        stripped[patch] = info
    return stripped


def ensure_table_write_precision(simulation_config, *, has_schedules):
    """A run whose BCs carry tables writes them back into every time directory;
    at the default 6 significant digits a (t − ε) row collapses onto t and the
    table is refused on restart. Such runs write in double precision (10
    digits — generate_control_dict's only other setting)."""
    config = dict(simulation_config)
    if has_schedules:
        config["writePrecision"] = "double"
    return config


def start_simulation_config(simulation_config, transient):
    """A cfdrun v3 run from a UNIFORM start begins at rest at the timeline's
    air temperature (design §4); other runs keep the room temperature."""
    start = (transient or {}).get("start") or {}
    air = (start.get("uniform") or {}).get("airT_C")
    if start.get("kind") != "uniform" or air is None:
        return simulation_config
    return {**dict(simulation_config), "roomTemperature": float(air)}


def _build_piecewise_dt_expression(entries, mass_flow, air_cp):
    """Build a piecewise ternary expression for time-varying ΔT.

    Produces e.g. ``(time()<3 ? 1.66 : (time()<3.1 ? 3.31 : 3.31))``
    for use inside OpenFOAM expression BCs where table Function1 cannot
    be used in variables.
    """
    dts = []
    for e in entries:
        t = e["t"] if isinstance(e, dict) else e.t
        p = e["value"] if isinstance(e, dict) else e.value
        dt = round(p / (mass_flow * air_cp), 4)
        dts.append((t, dt))
    if len(dts) == 1:
        return str(dts[0][1])
    expr = str(dts[-1][1])
    for i in range(len(dts) - 2, -1, -1):
        t_next = dts[i + 1][0]
        expr = f"(time()<{t_next} ? {dts[i][1]} : {expr})"
    return expr


def _build_piecewise_capacity_expression(table_entries, tret_var):
    """Build piecewise-linear interpolation for Q_max = f(T_return).

    Each entry: {"returnTemp": float (°C), "capacity": float (kW)}.
    Returns expression string with nested ternary operators.
    Converts to Kelvin and Watts internally.
    Constant extrapolation beyond endpoints.
    """
    points = sorted(table_entries, key=lambda e: e["returnTemp"])
    pts = [(e["returnTemp"] + 273.15, e["capacity"] * 1000) for e in points]
    if len(pts) == 1:
        return str(pts[0][1])
    # Build from right to left (innermost = last segment)
    expr = str(pts[-1][1])
    for i in range(len(pts) - 2, -1, -1):
        t_lo, q_lo = pts[i]
        t_hi, q_hi = pts[i + 1]
        slope = (q_hi - q_lo) / (t_hi - t_lo)
        interp = f"({q_lo}+({tret_var}-{t_lo})*{slope:.6f})"
        expr = f"({tret_var}<{t_hi} ? {interp} : {expr})"
    # Constant extrapolation below first point
    t_first = pts[0][0]
    expr = f"({tret_var}<{t_first} ? {pts[0][1]} : {expr})"
    return expr


#: Under-relaxation of every porousBafflePressure jump (see the block below).
POROUS_JUMP_RELAX = 0.05


def _format_porous_baffle_block(patch_name: str, spec: BCSpec) -> tuple[list[str], float, float, float]:
    """Build p_rgh lines for a porousBafflePressure patch. Prefers the
    new PerforatedSpec (Idelchik formula); falls back to legacy sigma
    (D=(1-σ²)/(2σ²), I=0) for rack-leakage bridge specs that have never
    had perforated geometry."""
    if spec.perforated is not None:
        D, I, length = perforated_to_porous_baffle(spec.perforated)
    else:
        sigma = spec.sigma if spec.sigma is not None else 0.5
        D = (1.0 - sigma ** 2) / (2.0 * sigma ** 2) if sigma > 0 else 1e6
        I = 0.0
        length = 1.0
    lines = [
        f"    {patch_name}",
        "    {",
        "        type            porousBafflePressure;",
        "        patchType       cyclic;",
        f"        D               {D:.6f};",
        f"        I               {I:.6f};",
        f"        length          {length:.0f};",
        "        uniformJump     false;",
        # Under-relax the jump (fixedJumpFvPatchField::relax: jump0 + r*(jump
        # - jump0) once per iteration). The jump is explicit in the flux, and
        # unrelaxed it fed the start-up swings back into the flow: Shinaide's
        # 64 perforated doors/panels took the continuity error from 1e-4 to
        # 3e6 in ten iterations. At 0.3 Alibaba_hangchou still diverged by
        # iteration 26: its door faces include cut slivers next to the rack
        # frames whose cell centres sit millimetres from the door, and the
        # quadratic jump on them overshot every iteration (the same mesh with
        # the doors' I near 0, or at 0.05, ran clean). A steady state is
        # unchanged (jump = jump0).
        f"        relax           {POROUS_JUMP_RELAX};",
        "        jump            uniform 0;",
        "        value           uniform 0;",
        "    }",
    ]
    return lines, D, I, length

# Field files generated by FieldGenerator.generate_fields().
# Any other files in 0/ (e.g. Cx, Cy, Cz, C, V from postProcess) are stale
# and must be removed before createBaffles, which reads ALL files in 0/.
STANDARD_FIELD_NAMES = frozenset({"U", "T", "p", "p_rgh", "k", "epsilon", "omega", "nut", "alphat"})


# The fan-server outlet temperature-rise BC is a FEEDBACK loop: the outlet value
# is recomputed every iteration from the weightAverage of T over the paired
# inlet. In a SIMPLE march that loop has gain 1 plus the room's own recirculation
# gain, and on coupled cases (Shinaide: 64 servers, cyclic door baffles) it goes
# unstable around a few hundred iterations — continuity errors climb from 1e0 to
# 1e27, k collapses, GAMG returns nan and the job dies in an MPI abort.
#
# Damp it with a first-order lag on the patch's OWN previous value:
#
#     T_out = lam*T_in_avg + (1-lam)*T_out_prev + lam*dT
#
# whose fixed point is exactly T_out = T_in_avg + dT (multiply out: the
# (1-lam)*T_out terms cancel), so the converged physics is unchanged while the
# per-iteration loop gain drops from 1 to lam.
FEEDBACK_DAMPING_LAMBDA = 0.5

class FieldGenerator:
    """
    Generates OpenFOAM field files (U, T, p, p_rgh, etc.).
    
    Handles:
    - Velocity field (U)
    - Temperature field (T)  
    - Pressure fields (p, p_rgh)
    - Turbulence fields (k, epsilon, omega, nut, alphat)
    """
    
    # Turbulence inlet BC constants — grounded in literature per
    # docs/superpowers/specs/2026-04-18-turbulence-bc-physical-grounding-design.md
    # Keys MUST match the bc_type strings emitted by bridge_bc.BCSpec; the
    # only flow-patch bc_types that reach _compute_turbulence_bc are
    # acu_supply and fan_outlet (acu_return and fan_inlet are domain outlets
    # and keep inletOutlet semantics — they never invoke this lookup).
    TURBULENCE_INTENSITY_BY_BC_TYPE = {
        "acu_supply": 0.10,   # post-fan CRAH supply (Alkharabsheh Flovent default; Dogan 2022 uses 0.50)
        "fan_outlet": 0.10,   # server-fan exhaust — convention import (no DC measurement; Erden & Khalifa 2019)
    }
    TURBULENCE_INTENSITY_DEFAULT = 0.05  # fallback for unmapped bc_type
    C_MU = 0.09                          # k-epsilon model constant

    K_MIN_PHYSICAL   = 0.1    # m²/s²
    K_MAX_PHYSICAL   = 5.0    # m²/s²
    EPS_MIN_PHYSICAL = 0.01   # m²/s³
    EPS_MAX_PHYSICAL = 100.0  # m²/s³

    def __init__(
        self,
        *,
        case_dir: Path,
        simulation_config: Optional[Dict[str, Any]] = None,
        room_config: Optional[Dict[str, Any]] = None,
        patch_geometry: Optional[Dict[str, "PatchGeometry"]] = None,
        bridge_registry: Optional["BridgeBCRegistry"] = None,
    ):
        """
        Initialize field generator.

        Args:
            case_dir: Case directory path
            simulation_config: Simulation configuration dict from the .cfdrun manifest
            room_config: Room configuration dict override
            patch_geometry: Dict mapping patch name → PatchGeometry(area, perimeter, bbox_dims)
                (from STLGenRunner._compute_patch_geometry()). Used for
                physically-grounded k/ε inlet BC derivation.
            bridge_registry: In-memory BridgeBCRegistry built by
                STLGenRunner._build_bridge_bc(). If None, _imported_bc_data is empty.
        """
        self.case_dir = Path(case_dir).resolve()
        self._simulation_config = simulation_config
        self._room_config = room_config
        self._patch_geometry = patch_geometry or {}
        self.bridge_registry = bridge_registry

        # Collect boundary data. Fan-server patches are driven exclusively by
        # the bridge BC registry (_imported_bc_data); the old _server_data
        # room-config heuristic was removed 2026-04-18 along with its silent
        # zeroGradient fallbacks (see _write_t_field fan_outlet branch).
        self._acu_data: Dict[str, Dict[str, float]] = {}
        self._air_leak_data: Dict[str, Dict[str, float]] = {}
        self._imported_bc_data: Dict[str, Dict[str, Any]] = {}

        self._data_collected = False

    def _compute_turbulence_bc(
        self, patch_name: str
    ) -> Optional[Tuple[float, float, float, float, float, float]]:
        """Compute (k, ε, I, L, U_phys, U_face) for an inlet patch.

        Formula (inlet turbulence estimation; CFD standard):
            k  = 1.5·(I·U_phys)²
            L  = 0.07·D_h  where D_h = 4A/P (rectangular) or 2√(A/π) (fallback)
            ε  = C_μ^0.75 · k^1.5 / L

        U_phys is the physical flow velocity (derived from physical_flow_area when
        available for fan patches). U_face is the CFD face velocity (flow_rate / area).
        For fan_outlet/fan_inlet patches, the tiny CFD patch area causes U_face >> U_phys;
        k must be computed from U_phys to avoid seed pollution.

        I is selected per bc_type from TURBULENCE_INTENSITY_BY_BC_TYPE; unknown
        bc_types fall back to TURBULENCE_INTENSITY_DEFAULT with a WARNING log.

        Returns None when flow_rate ≤ 0, area ≤ 1e-8, or the patch has no
        bridge-registry entry.
        """
        bc_info = self._imported_bc_data.get(patch_name, {})
        flow_rate = bc_info.get("flow_rate") or 0.0
        if flow_rate <= 0:
            return None
        geom = self._patch_geometry.get(patch_name)
        if geom is None or geom.area <= 1e-8:
            return None
        U = flow_rate / geom.area

        bc_type = bc_info.get("bc_type", "")
        phys_area = bc_info.get("physical_flow_area")
        if bc_type in ("fan_outlet", "fan_inlet") and phys_area and phys_area > 1e-8:
            U_phys = flow_rate / phys_area
        elif bc_type in ("fan_outlet", "fan_inlet"):
            U_phys = min(U, 5.0)
            logger.warning(
                f"Patch {patch_name!r}: fan bc_type={bc_type!r} missing physical_flow_area; "
                f"fallback U_phys={U_phys:.2f} m/s (capped at 5.0)"
            )
        else:
            U_phys = U

        if bc_type in self.TURBULENCE_INTENSITY_BY_BC_TYPE:
            I = self.TURBULENCE_INTENSITY_BY_BC_TYPE[bc_type]
        else:
            I = self.TURBULENCE_INTENSITY_DEFAULT
            logger.warning(
                f"Unknown bc_type {bc_type!r} for patch {patch_name!r}; "
                f"falling back to default turbulence intensity I={I}"
            )

        k = 1.5 * (I * U_phys) ** 2

        if geom.perimeter > 1e-6:
            D_h = 4.0 * geom.area / geom.perimeter
        else:
            D_h = 2.0 * math.sqrt(geom.area / math.pi)
            logger.debug(
                f"Patch {patch_name!r}: perimeter=0 (non-planar or closed "
                f"topology); falling back to circular D_h={D_h:.4f} m"
            )
        length_scale = max(0.07 * D_h, 1e-6)

        epsilon = self.C_MU ** 0.75 * k ** 1.5 / length_scale

        k_raw, eps_raw = k, epsilon
        k = max(min(k, self.K_MAX_PHYSICAL), self.K_MIN_PHYSICAL)
        epsilon = max(min(epsilon, self.EPS_MAX_PHYSICAL), self.EPS_MIN_PHYSICAL)

        if k_raw > self.K_MAX_PHYSICAL:
            logger.warning(
                f"Patch {patch_name}: computed k={k_raw:.2f} > {self.K_MAX_PHYSICAL} — "
                f"clamped; check U_phys derivation for bc_type={bc_type!r}"
            )
        elif k_raw < self.K_MIN_PHYSICAL:
            # One line per porous patch per rank. Base604 has ~2,900 of them,
            # so this alone put 46,000 of that job's 68,000 log lines on the
            # frontend's stream. Raising k to the floor is the intended
            # behaviour for a thin slot, not news, and the debug summary just
            # below still carries every number for the patch that asks.
            logger.debug(
                f"Patch {patch_name}: computed k={k_raw:.4f} < {self.K_MIN_PHYSICAL} — "
                f"raised to floor"
            )

        logger.debug(
            f"Turbulence BC {patch_name}: U_face={U:.2f} m/s, U_phys={U_phys:.2f} m/s, "
            f"I={I}, L={length_scale:.4f} m, "
            f"k={k:.4f}, eps={epsilon:.4f} (A={geom.area:.4f} m², P={geom.perimeter:.4f} m, "
            f"Q={flow_rate:.4f} m³/s)"
        )
        return (k, epsilon, I, length_scale, U_phys, U)

    @property
    def simulation_config(self) -> Dict[str, Any]:
        """Return simulation config."""
        return self._simulation_config or {}

    @property
    def room_config(self) -> Dict[str, Any]:
        """Return room config."""
        return self._room_config or {}

    def _collect_data(self) -> None:
        """Collect server and ACU data from configuration."""
        if self._data_collected:
            return

        self._acu_data = self._collect_acu_data()
        self._air_leak_data = self._collect_air_leak_data()
        self._imported_bc_data = self._collect_imported_bc_data()
        self._rebalance_fan_outlets()

        self._data_collected = True

    def _rebalance_fan_outlets(self) -> None:
        """Split a fan server's airflow and power over its outlet patches by
        their exported area.

        The exporter divides a server's flow and power evenly by the NUMBER
        of outlet sub-patches the user drew, and binds every one of them even
        when overlapping sub-patches leave one with no surface at all.
        DAML-Testbed (patched): RTXpro6000's three outlet patches got 1/3 of
        0.5 m3/s each, but p0 was clipped away by p1/p2 in the STL (no face
        in the mesh) and p2 is 0.008 m2 against p1's 0.047 m2. A third of the
        server's air had nowhere to go and p2 blew 21 m/s; T ran unbounded
        to the 1.7 K clip within 500 iterations and the solve diverged. A fan
        pushes the same velocity through every opening of one face, so the
        flow (and the power, which keeps every patch's dT equal) follows the
        area. Patches without exported area get none. Schedules are scaled
        by the same factor.
        """
        geom = self._patch_geometry
        if not geom:
            return
        groups: Dict[str, List[str]] = {}
        for name, info in self._imported_bc_data.items():
            if info.get("bc_type") != "fan_outlet":
                continue
            i = name.rfind("_outlet_")
            if i > 0:
                groups.setdefault(name[:i], []).append(name)
        for stem, names in sorted(groups.items()):
            if len(names) < 2:
                continue
            areas = {n: (geom[n].area if n in geom else 0.0) for n in names}
            total_area = sum(areas.values())
            total_q = sum(self._imported_bc_data[n].get("flow_rate") or 0.0 for n in names)
            total_p = sum(self._imported_bc_data[n].get("power") or 0.0 for n in names)
            if total_area <= 0 or total_q <= 0:
                continue
            changed = []
            for n in sorted(names):
                info = self._imported_bc_data[n]
                share = areas[n] / total_area
                old_q = info.get("flow_rate") or 0.0
                scale = (share * total_q / old_q) if old_q > 0 else 0.0
                if abs(old_q - share * total_q) <= 1e-12 * total_q:
                    continue
                info["flow_rate"] = share * total_q
                info["power"] = share * total_p
                tv = dict(info.get("timeVarying") or {})
                for key in ("airflow", "powerRows", "power"):
                    rows = tv.get(key)
                    if isinstance(rows, list):
                        tv[key] = [
                            {**r, "value": r["value"] * scale} if isinstance(r, dict) else r
                            for r in rows
                        ]
                info["timeVarying"] = tv
                changed.append(f"{n} {old_q:.4g}->{share * total_q:.4g} m3/s")
            if changed:
                logger.info(f"Fan server {stem}: outlet flow split by exported area: "
                            + "; ".join(changed))

    def _collect_imported_bc_data(self) -> Dict[str, Dict[str, Any]]:
        """Collect boundary condition data from the in-memory bridge BC registry.

        The registry is built by STLGenRunner._build_bridge_bc() and injected
        via the constructor. Returns dict mapping sanitized patch_name ->
        {bc_type, temperature, flow_rate, power, sigma, heat_flux}.
        """
        imported_bc: Dict[str, Dict[str, Any]] = {}

        if self.bridge_registry is not None:
            for patch_name, spec in self.bridge_registry.items():
                imported_bc[patch_name] = {
                    "bc_type": spec.bc_type,
                    "temperature": spec.temperature,
                    "flow_rate": spec.flow_rate,
                    "power": spec.power,
                    "sigma": spec.sigma,
                    "perforated": spec.perforated,
                    "heat_flux": spec.heat_flux,
                    "physical_flow_area": getattr(spec, "physical_flow_area", None),
                    "direction": getattr(spec, "direction", None),
                    "patch_area": getattr(spec, "patch_area", None),
                    "cooling_capacity": getattr(spec, "cooling_capacity", None),
                    "min_supply_temp": getattr(spec, "min_supply_temp", None),
                    "set_point_temp": getattr(spec, "set_point_temp", None),
                    "coolant_temp": getattr(spec, "coolant_temp", None),
                    "return_patch_name": getattr(spec, "return_patch_name", None),
                    "timeVarying": getattr(spec, "timeVarying", None) or {},
                    "surface_area": getattr(spec, "surface_area", None),
                }
            logger.debug(
                f"Loaded {len(self.bridge_registry)} bridge BC entries from in-memory registry"
            )

        return imported_bc
    
    # _collect_server_data: removed 2026-04-18. Room-config-based fan-server
    # power/flow lookup was only used by the old T-field fallback branch that
    # produced silent zeroGradient outlets. Fan-server BCs now come exclusively
    # from the bridge BC registry (bc_type == "fan_outlet"), which carries
    # power and flow_rate alongside the patch name.

    def _collect_acu_data(self) -> Dict[str, Dict[str, float]]:
        """Collect ACU temperature and flow rate data."""
        acu_data = {}
        room_config = self.room_config
        if not room_config:
            return acu_data
        
        inputs = room_config.get("inputs", {})
        constructions = room_config.get("constructions", {})
        models = room_config.get("models", {})
        # Support both snake_case and camelCase
        cooling_models = models.get("cooling_models") or models.get("coolingModels", {})
        
        acus_inputs = inputs.get("acus", {})
        acus = constructions.get("acus", {})
        
        for acu_id, acu_def in acus.items():
            acu_input = acus_inputs.get(acu_id, {})
            
            # Get temperature (support both snake_case and camelCase)
            temperature = self._lookup_numeric(acu_input, ["supply_air_temperature"])
            if temperature is None:
                temperature = self._lookup_numeric(acu_input, ["supplyAirTemperature"])
            if temperature is None:
                cooling_model_name = acu_def.get("cooling_model") or acu_def.get("coolingModel")
                if cooling_model_name and cooling_model_name in cooling_models:
                    temperature = self._lookup_numeric(
                        cooling_models[cooling_model_name],
                        ["operating", "supply_air_temperature"]
                    )
                    if temperature is None:
                        temperature = self._lookup_numeric(
                            cooling_models[cooling_model_name],
                            ["supplyAirTemperature"]
                        )
            if temperature is None:
                temperature = self._lookup_numeric(acu_def,
                    ["cooling", "operating", "supply_air_temperature"])
            if temperature is None:
                temperature = 24.0
            
            # Get flow rate (support both snake_case and camelCase)
            flow_rate = self._lookup_numeric(acu_input, ["supply_air_volume_flow_rate"])
            if flow_rate is None:
                flow_rate = self._lookup_numeric(acu_input, ["supplyAirVolumeFlowRate"])
            if flow_rate is None:
                cooling_model_name = acu_def.get("cooling_model") or acu_def.get("coolingModel")
                if cooling_model_name and cooling_model_name in cooling_models:
                    flow_rate = self._lookup_numeric(
                        cooling_models[cooling_model_name],
                        ["operating", "supply_air_volume_flow_rate"]
                    )
                    if flow_rate is None:
                        flow_rate = self._lookup_numeric(
                            cooling_models[cooling_model_name],
                            ["supplyAirVolumeFlowRate"]
                        )
            if flow_rate is None:
                flow_rate = self._lookup_numeric(acu_def,
                    ["cooling", "operating", "supply_air_volume_flow_rate"])
            if flow_rate is None:
                flow_rate = 0.0
            
            # Normalize ACU ID: replace '-' with '_' to match patch naming
            normalized_acu_id = acu_id.replace("-", "_")
            acu_data[normalized_acu_id] = {
                "temperature": float(temperature),
                "flow_rate": float(flow_rate),
            }
        
        return acu_data
    
    def _collect_air_leak_data(self) -> Dict[str, Dict[str, float]]:
        """Collect air leakage data from rack configurations."""
        air_leak_data = {}
        room_config = self.room_config
        if not room_config:
            return air_leak_data
        
        constructions = room_config.get("constructions", {})
        racks = constructions.get("racks", {})
        
        for rack_id, rack_data in racks.items():
            air_leakage = rack_data.get("airLeakage", {})
            if air_leakage.get("enabled", False):
                sigma = air_leakage.get("sigma", 0.1)
                air_leak_data[rack_id] = {"sigma": float(sigma)}
        
        return air_leak_data
    
    def _lookup_numeric(self, data: Any, keys: List[str]) -> Optional[float]:
        """Lookup a numeric value in nested dictionary."""
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    
    _SPECIAL_PATCH_TYPES = frozenset({
        "cyclic", "cyclicami",
        "empty",
        "processor", "processorcyclic",
    })

    @staticmethod
    def _is_cyclic_patch_type(patch_type: str) -> bool:
        """Check if a boundary patch type requires a matching patchField type.

        OpenFOAM requires the field patchField type to match the boundary
        patch type for cyclic/cyclicAMI/empty/processor.  This MUST take
        priority over any bridge BC or pattern-based matching — otherwise
        the solver crashes with 'inconsistent patch and patchField types'.
        Covers oldInternalFaces (empty) left by createBaffles.
        """
        return patch_type.lower() in FieldGenerator._SPECIAL_PATCH_TYPES

    def generate_fields(
        self,
        case_dir: Path,
        patches: List[Tuple[str, str]],
        *,
        strict: bool = True,
        static_outlet_temperature: bool = False,
        global_patches: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        """
        Generate all field files for the case.

        Args:
            case_dir: Path to the case directory
            patches: List of (patch_name, patch_type) tuples
            global_patches: Physical patches across every partition. Used for
                paired-patch validation and a consistent turbulence seed when
                generating fields directly in processor directories.
            strict: When True (solve-path default), raise on BC conditions that
                cannot be built (e.g. fan_outlet's paired inlet missing from
                the mesh). When False (mesh-path), emit a placeholder BC
                instead and log a warning — the case is not solver-runnable
                but createBaffles / mesh inspection can proceed. The strict
                gate fires at solve time via the same code path.
        """
        self._collect_data()

        field_dir = case_dir / "0"
        field_dir.mkdir(parents=True, exist_ok=True)
        # U is regenerated below. READ_IF_PRESENT phi must not carry flux from
        # an older solve, including OpenFOAM's compressed-file fallback.
        # A genuine steady-start phi is seeded after all field generation.
        for name in ("phi", "phi.gz"):
            (field_dir / name).unlink(missing_ok=True)

        mesh_patch_names = {p[0] for p in (patches if global_patches is None else global_patches)}
        if not getattr(self, "_acu_balance_done", False):
            self._acu_balance_done = True
            self._acu_balance = self._balance_sealed_acu_airflow(mesh_patch_names)
        if getattr(self, "_acu_balance", None):
            (case_dir / ACU_BALANCE_FILE).write_text(
                json.dumps(self._acu_balance, indent=2), encoding="utf-8")
        self._pinned_returns = self._pinned_acu_returns(mesh_patch_names)
        self._free_supplies = self._sealed_region_supplies(mesh_patch_names)

        # Generate each field
        self._write_u_field(field_dir / "U", patches, global_patches=global_patches)
        self._write_t_field(field_dir / "T", patches, strict=strict,
                           static_outlet_temperature=static_outlet_temperature,
                           global_patches=global_patches)
        self._write_p_field(field_dir / "p", patches)
        self._write_p_rgh_field(field_dir / "p_rgh", patches)
        
        # Turbulence fields — compute physically-based k/epsilon from flow rates + patch areas
        k_avg, eps_avg = self._compute_internal_field_seed(
            patches if global_patches is None else global_patches
        )
        self._write_turbulence_field(field_dir / "k", patches, k_avg, "volScalarField", "[0 2 -2 0 0 0 0]", "k")
        self._write_turbulence_field(field_dir / "epsilon", patches, eps_avg, "volScalarField", "[0 2 -3 0 0 0 0]", "epsilon")
        self._write_simple_scalar_field(field_dir / "omega", patches, "1.0", "volScalarField", "[0 0 -1 0 0 0 0]")
        # nut and alphat internalField must be non-zero at solve time — the
        # turbulence model only `correct()`s at the END of each SIMPLE iter, so
        # at iteration 1 the T equation uses whatever is written here. A zero
        # initial alphat leaves alphaEff = nu/Pr ≈ 2.1e-5, which makes Peclet
        # ≈ 2e4 on 0.4 m cells and causes the T linear system to NaN on the
        # first solve.
        # Cmu=0.09; nut0 = Cmu * k^2 / eps; alphat0 = nut0 / Prt (Prt=0.85).
        nut0 = max(0.09 * (k_avg ** 2) / eps_avg, 1e-5) if eps_avg > 0 else 1e-4
        alphat0 = nut0 / 0.85
        self._write_simple_scalar_field(field_dir / "nut", patches, f"{nut0:.6g}", "volScalarField", "[0 2 -1 0 0 0 0]")
        self._write_simple_scalar_field(field_dir / "alphat", patches, f"{alphat0:.6g}", "volScalarField", "[0 2 -1 0 0 0 0]")
        
        logger.info(f"Generated field files in: {field_dir}")
    
    def _write_field_header(self, path: Path, field_class: str, dimensions: str, internal_field: str) -> List[str]:
        """Write OpenFOAM field file header."""
        return [
            "/*--------------------------------*- C++ -*----------------------------------*\\",
            "| =========                 |                                                 |",
            "| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |",
            "|  \\\\    /   O peration     | Version:  v2406                                 |",
            "|   \\\\  /    A nd           | Website:  www.openfoam.com                      |",
            "|    \\\\/     M anipulation  |                                                 |",
            "\\*---------------------------------------------------------------------------*/",
            "FoamFile",
            "{",
            "    version     2.0;",
            "    format      ascii;",
            f"    class       {field_class};",
            f"    object      {path.name};",
            "}",
            "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //",
            "",
            f"dimensions      {dimensions};",
            "",
            f"internalField   uniform {internal_field};",
            "",
            "boundaryField",
            "{",
        ]
    
    def _write_field_footer(self) -> List[str]:
        """Write OpenFOAM field file footer."""
        return [
            "}",
            "",
            "// ************************************************************************* //",
        ]
    
    # _extract_server_id: removed 2026-04-18. Used only by the old T-field
    # fallback branch for room-config-driven fan-server BC lookup, which has
    # been replaced by the bridge-BC-only fan_outlet path.

    def _extract_acu_id(self, patch_name: str) -> Optional[str]:
        """Extract ACU ID from patch name.
        
        Actual patch name format: acu_supply_ACU_1 or acu_return_ACU_1
        We need to extract: ACU_1
        """
        # Pattern for STL-based format: acu_supply_{ACU_ID} or acu_return_{ACU_ID}
        match = re.search(r'acu_(supply|return)_([A-Za-z0-9_]+)$', patch_name, re.IGNORECASE)
        if match:
            return match.group(2)
        
        # Fallback patterns
        patterns = [
            r'acu_supply_([A-Za-z0-9_]+)',
            r'acu_return_([A-Za-z0-9_]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, patch_name, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    def _pinned_acu_returns(self, mesh_patch_names: set) -> Dict[str, Any]:
        """ACU return patches that draw exactly their own supply's airflow.

        A CRAC fan pulls in what it blows out. With every return a pressure
        outlet the split between units is left to the flow field: Base2-9's
        eight returns carried 8.85-10.36 m3/s against 9.5 m3/s supplies and
        kept trading air (and heat) for a thousand iterations, so the return
        temperatures never settled. A return is pinned when exactly one
        supply names it (``return_patch_name``), both are meshed, and the
        supply blows a plain volumetric flow (constant or an airflow table;
        a louvre-vector supply keeps the pressure outlet). Every return left
        unpinned stays the p_rgh = 0 reference; when all could be pinned, the
        first by name stays the reference, and mass conservation makes it
        carry its own supply too. Value: the volumetric flow (number) or the
        airflow table string.
        """
        data = getattr(self, "_imported_bc_data", None) or {}
        returns = sorted(n for n, b in data.items()
                         if b.get("bc_type") == "acu_return" and n in mesh_patch_names)
        if os.environ.get("DCDTUNI_ACU_RETURN_PRESSURE") or len(returns) < 2:
            return {}
        suppliers: Dict[str, List[str]] = {}
        for name, info in data.items():
            if info.get("bc_type") == "acu_supply" and info.get("return_patch_name"):
                suppliers.setdefault(info["return_patch_name"], []).append(name)
        pinned: Dict[str, Any] = {}
        for ret in returns:
            supplies = suppliers.get(ret, [])
            if len(supplies) != 1 or supplies[0] not in mesh_patch_names:
                continue
            sup = data[supplies[0]]
            tv = sup.get("timeVarying") or {}
            if tv.get("velocity") or (sup.get("direction") and (sup.get("patch_area") or 0) > 0):
                continue
            if tv.get("airflow"):
                pinned[ret] = _format_time_table(tv["airflow"])
            elif sup.get("flow_rate"):
                pinned[ret] = float(sup["flow_rate"])
        if pinned and len(pinned) == len(returns):
            del pinned[returns[0]]
        regions = self._mesh_patch_regions()
        if regions:
            self._free_sealed_region_returns(pinned, returns, regions, data)
        if pinned:
            logger.info(f"ACU returns at their supply airflow: {len(pinned)} of {len(returns)}; "
                        f"pressure reference: {sorted(set(returns) - set(pinned))}")
        return pinned

    def _mesh_patch_regions(self) -> Optional[Dict[str, Set[int]]]:
        """Patch -> checkMesh region, read once from the case's Mesh/ (None: one region)."""
        if not hasattr(self, "_patch_regions_cache"):
            from backend.aircooling.runner.pressure_regions import patch_regions
            try:
                self._patch_regions_cache = patch_regions(self.case_dir / "Mesh")
            except (OSError, ValueError) as exc:
                logger.warning(f"Mesh region sets unreadable ({exc}); treating the mesh as one region")
                self._patch_regions_cache = None
        return self._patch_regions_cache

    def _free_sealed_region_returns(
        self, pinned: Dict[str, Any], returns: List[str],
        regions: Dict[str, Set[int]], data: Dict[str, Dict[str, Any]],
    ) -> None:
        """Give every sealed region a pressure reference of its own.

        Full-block panels and containment can close a region off from the
        room except through fans and ACUs. If all of its returns are pinned,
        its fixed flows must balance to round-off or the solve stops at
        "Incompatible assembled RHS in free pressure component" (CMI-Guichou:
        four contained aisles whose servers blow 10.8-27.1 m3/s into returns
        set to 6.8-10.2). There every return becomes a pressure outlet, so the
        returns carry what the region's fans deliver. Regions that already
        hold a free return keep the single-reference behaviour.
        """
        free = [r for r in returns if r not in pinned]
        covered: Set[int] = set()
        for r in free:
            covered |= regions.get(r, set())
        by_region: Dict[int, List[str]] = {}
        for r in returns:
            for rid in regions.get(r, ()):
                by_region.setdefault(rid, []).append(r)

        def flow(name: str) -> float:
            q = data.get(name, {}).get("flow_rate")
            return float(q) if isinstance(q, (int, float)) else 0.0

        in_region: Dict[int, List[str]] = {}
        for name, rids in regions.items():
            for rid in rids:
                in_region.setdefault(rid, []).append(name)
        for rid in sorted(set(in_region) - covered):
            names = in_region[rid]
            blown = sum(flow(n) for n in names
                        if data.get(n, {}).get("bc_type") in ("fan_outlet", "acu_supply"))
            drawn = sum(flow(n) for n in names
                        if data.get(n, {}).get("bc_type") == "fan_inlet")
            rets = sorted(by_region.get(rid, []))
            if not rets:
                if any(data.get(n, {}).get("bc_type") == "acu_supply" for n in names):
                    continue   # _sealed_region_supplies gives it a reference
                if abs(blown - drawn) > 1e-9:
                    logger.error(
                        f"Mesh region {rid} is sealed and has no ACU return: its fans blow "
                        f"{blown:.4g} m3/s and draw {drawn:.4g} m3/s, so the pressure "
                        f"equation has no solution there. Open a path to the room or add a return.")
                continue
            for r in rets:
                pinned.pop(r, None)
            configured = sum(flow(r) for r in rets)
            logger.warning(
                f"Mesh region {rid} is sealed from the room (full-block panels / containment): "
                f"its {len(rets)} ACU return(s) become pressure outlets so it has its own pressure "
                f"reference. Its fans and supplies blow {blown:.4g} m3/s and draw {drawn:.4g}; "
                f"the returns will carry {blown - drawn:.4g} m3/s against {configured:.4g} m3/s "
                f"configured ({', '.join(rets[:6])}{' ...' if len(rets) > 6 else ''}).")
            if blown - drawn <= 0:
                logger.warning(
                    f"Mesh region {rid}: the fans draw more than they blow, so its returns "
                    f"will have to blow air in. Check the server and ACU airflows.")
        # Every region with a free return: that return takes whatever the fixed
        # flows leave over. Say so when it would have to blow air INTO the room.
        for rid in sorted(covered):
            names = in_region.get(rid, [])
            blown = sum(flow(n) for n in names
                        if data.get(n, {}).get("bc_type") in ("fan_outlet", "acu_supply"))
            drawn = sum(flow(n) for n in names
                        if data.get(n, {}).get("bc_type") == "fan_inlet")
            held = sum(flow(r) for r in by_region.get(rid, []) if r in pinned)
            free_here = [r for r in by_region.get(rid, []) if r not in pinned]
            carry = blown - drawn - held
            if free_here and carry < 0:
                logger.warning(
                    f"Mesh region {rid}: fans and supplies blow {blown:.4g} m3/s, server inlets "
                    f"draw {drawn:.4g} and the fixed returns {held:.4g}, so the free return(s) "
                    f"{', '.join(free_here[:4])} must blow {-carry:.4g} m3/s INTO the room. "
                    f"The airflows do not balance; check the server and ACU flow rates.")

    def _balance_sealed_acu_airflow(self, mesh_patch_names: set) -> Optional[Dict[str, Any]]:
        """Match each ACU's airflow to what its sealed regions can pass.

        A CRAC moves the same air through its supply and its return. When
        containment seals the mesh into several regions, every region's air in
        must equal its air out, and the configured ACU flows rarely satisfy
        that: CMI-Guichou's sealed hot aisles took 21.9 and 33.7 m3/s from their
        servers against 10.2 m3/s of configured returns, so the returns ran at
        two to three times their supply and the room's own returns had to pull
        air backwards to make up the cold side. The owner's ruling (2026-11-10):
        the ACUs of sealed regions follow the servers.

        Unknowns are the paired units (one supply naming one return, both
        meshed, a plain volumetric flow). Each region gives one balance:
        supplies in it - returns in it + server outlets - server inlets = 0.
        The flows nearest the configured ones (least squares on the relative
        change) that meet every balance replace the supply flow; the return is
        then pinned to it as before. A region that holds an unpaired free
        return absorbs whatever it is given and imposes no balance. Nothing
        changes when the configured flows already balance (an open room), when
        the mesh is one region, or when the balance would need a unit to stop
        or reverse; those cases log why. ``DCDTUNI_ACU_NO_BALANCE`` turns it off.
        Returns the receipt (written next to ``0/``), or None.
        """
        if os.environ.get("DCDTUNI_ACU_NO_BALANCE"):
            return None
        regions = self._mesh_patch_regions()
        if not regions or len({r for s in regions.values() for r in s}) < 2:
            return None
        data = getattr(self, "_imported_bc_data", None) or {}

        def region_of(name: str) -> Optional[int]:
            rids = regions.get(name) or set()
            return next(iter(rids)) if len(rids) == 1 else None

        def q(name: str) -> float:
            v = data.get(name, {}).get("flow_rate")
            return float(v) if isinstance(v, (int, float)) else 0.0

        # Paired units that may be rescaled.
        returns_named: Dict[str, List[str]] = {}
        for name, info in data.items():
            if info.get("bc_type") == "acu_supply" and info.get("return_patch_name"):
                returns_named.setdefault(info["return_patch_name"], []).append(name)
        units: List[Tuple[str, str]] = []
        for ret, sups in sorted(returns_named.items()):
            if len(sups) != 1:
                continue
            sup = sups[0]
            if sup not in mesh_patch_names or ret not in mesh_patch_names:
                continue
            if data.get(ret, {}).get("bc_type") != "acu_return":
                continue
            tv = data[sup].get("timeVarying") or {}
            if tv.get("velocity") or tv.get("airflow") or q(sup) <= 0:
                continue
            if region_of(sup) is None or region_of(ret) is None:
                continue
            units.append((sup, ret))
        if not units:
            return None
        unit_patches = {n for u in units for n in u}

        rows: Dict[int, Dict[str, Any]] = {}
        absorbing: Set[int] = set()
        for name in mesh_patch_names:
            info = data.get(name)
            if not info or name in unit_patches:
                continue
            kind = info.get("bc_type")
            if kind not in ("fan_inlet", "fan_outlet", "acu_supply", "acu_return"):
                continue
            rid = region_of(name)
            if rid is None:
                logger.info(f"ACU airflow balance skipped: {name} spans several mesh regions")
                return None
            if kind in ("fan_inlet", "fan_outlet") and (info.get("timeVarying") or {}).get("airflow"):
                logger.info(f"ACU airflow balance skipped: {name} follows an airflow schedule")
                return None
            row = rows.setdefault(rid, {"fixed": 0.0})
            if kind == "acu_return":
                absorbing.add(rid)   # an unpaired return is a free pressure outlet
            elif kind == "acu_supply":
                row["fixed"] += q(name)
            elif kind == "fan_outlet":
                row["fixed"] += q(name)
            else:
                row["fixed"] -= q(name)
        rids = sorted({region_of(n) for u in units for n in u} | set(rows))
        rids = [r for r in rids if r not in absorbing]
        if not rids:
            return None
        A = np.zeros((len(rids), len(units)))
        b = np.zeros(len(rids))
        index = {r: i for i, r in enumerate(rids)}
        for r, row in rows.items():
            if r in index:
                b[index[r]] = -row["fixed"]
        for j, (sup, ret) in enumerate(units):
            if region_of(sup) in index:
                A[index[region_of(sup)], j] += 1.0
            if region_of(ret) in index:
                A[index[region_of(ret)], j] -= 1.0
        q0 = np.array([q(s) for s, _ in units])
        scale = max(float(np.abs(b).max(initial=0.0)), float(q0.max()))
        if float(np.abs(A @ q0 - b).max(initial=0.0)) <= 1e-6 * scale:
            return None   # the configured flows already balance
        W = np.diag(q0 ** 2)   # inverse weights: relative change
        M = A @ W @ A.T
        lam = np.linalg.lstsq(M, b - A @ q0, rcond=None)[0]
        qn = q0 + W @ A.T @ lam
        resid = float(np.abs(A @ qn - b).max(initial=0.0))
        if resid > 1e-6 * scale:
            logger.warning(
                f"ACU airflow balance: the sealed regions cannot be balanced by the ACUs alone "
                f"(residual {resid:.4g} m3/s); configured flows kept. A region whose servers "
                f"blow and draw unequal air with no ACU in it has no steady solution.")
            return None
        low = [(units[j][0], qn[j]) for j in range(len(units)) if qn[j] < 0.05 * q0[j]]
        if low:
            logger.warning(
                f"ACU airflow balance: {len(low)} unit(s) would have to stop or reverse to "
                f"balance the sealed regions ({', '.join(f'{n} {v:.3g}' for n, v in low[:4])}); "
                f"configured flows kept. Check the server and ACU airflows.")
            return None
        changed = []
        for j, (sup, ret) in enumerate(units):
            if abs(qn[j] - q0[j]) <= 1e-9 * max(q0[j], 1.0):
                continue
            data[sup]["flow_rate"] = float(qn[j])
            if isinstance(data.get(ret), dict):
                data[ret]["flow_rate"] = float(qn[j])
            changed.append({"supply": sup, "return": ret, "configured": float(q0[j]),
                            "balanced": float(qn[j]), "supplyRegion": region_of(sup),
                            "returnRegion": region_of(ret)})
        if not changed:
            return None
        biggest = sorted(changed, key=lambda c: -abs(c["balanced"] / c["configured"] - 1))
        logger.warning(
            f"ACU airflow balance: the mesh has {len(set(r for s in regions.values() for r in s))} "
            f"sealed regions and the configured ACU flows do not match what the servers in them "
            f"blow and draw, so {len(changed)} of {len(units)} ACUs follow the servers (supply = "
            f"return; total {sum(c['configured'] for c in changed):.4g} -> "
            f"{sum(c['balanced'] for c in changed):.4g} m3/s). Largest changes: "
            + "; ".join(f"{c['supply']} {c['configured']:.4g}->{c['balanced']:.4g}"
                        for c in biggest[:6]) + ". Set DCDTUNI_ACU_NO_BALANCE=1 to keep the "
            f"configured flows.")
        return {"format": "dcdtuni.acu-airflow-balance/v1", "units": changed}

    def _sealed_region_supplies(self, mesh_patch_names: set) -> Set[str]:
        """ACU supplies that must follow pressure: those of sealed supply regions.

        A contained cold aisle fed by in-row units, or a raised-floor plenum
        that feeds a contained aisle, holds ACU supplies and server inlets but
        no ACU return. With every flow fixed it has no pressure reference and
        the solve cannot start unless the flows balance to round-off. There
        the supplies become pressure inlets at their supply temperature and
        deliver what the region's servers draw.
        """
        regions = self._mesh_patch_regions()
        if not regions:
            return set()
        data = getattr(self, "_imported_bc_data", None) or {}
        in_region: Dict[int, List[str]] = {}
        for name, rids in regions.items():
            if name not in mesh_patch_names:
                continue
            for rid in rids:
                in_region.setdefault(rid, []).append(name)
        free: Set[str] = set()
        for rid in sorted(in_region):
            names = in_region[rid]
            kinds = {n: data.get(n, {}).get("bc_type") for n in names}
            if any(k == "acu_return" for k in kinds.values()):
                continue
            supplies = sorted(n for n, k in kinds.items() if k == "acu_supply")
            if not supplies:
                continue
            q = lambda n: float(data.get(n, {}).get("flow_rate") or 0.0)   # noqa: E731
            drawn = sum(q(n) for n, k in kinds.items() if k == "fan_inlet")
            blown = sum(q(n) for n, k in kinds.items() if k == "fan_outlet")
            configured = sum(q(n) for n in supplies)
            free.update(supplies)
            logger.warning(
                f"Mesh region {rid} is sealed and has ACU supplies but no return "
                f"(contained cold aisle / raised-floor plenum): its {len(supplies)} supply(ies) "
                f"become pressure inlets at their supply temperature, so they deliver what the "
                f"region's servers draw ({drawn - blown:.4g} m3/s net) against {configured:.4g} "
                f"m3/s configured ({', '.join(supplies[:6])}{' ...' if len(supplies) > 6 else ''}).")
            if drawn - blown <= 0:
                logger.warning(
                    f"Mesh region {rid}: its servers blow more than they draw, so the supplies "
                    f"will have to take air out. Check the server and ACU airflows.")
        return free

    def _write_u_field(
        self, path: Path, patches: List[Tuple[str, str]], *,
        global_patches: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        """Write velocity field (U).

        Boundary condition logic:
        - Server inlet: flowRateOutletVelocity (air flows OUT of room INTO server)
        - Server outlet: flowRateInletVelocity (hot air flows INTO room FROM server)
        - ACU supply: flowRateInletVelocity (cold air flows INTO room FROM ACU)
        - ACU return: pressureInletOutletVelocity (backflow-stable outlet;
          p_rgh=0 anchors the return). Plain zeroGradient U diverges when the
          flow reverses through the return in a confined corner.
        """
        # For volumetric flowRate*Velocity, omit `value`: OpenFOAM 2412 then
        # evaluates Q(t0) / the actual meshed patch area in its constructor.
        # An explicit zero value suppresses evaluation and incorrectly makes
        # the uniform-start initial Courant number zero despite prescribed flow.
        lines = self._write_field_header(path, "volVectorField", "[0 1 -1 0 0 0 0]", "(0 0 0)")

        mesh_patch_names = {p[0] for p in (
            patches if global_patches is None else global_patches
        )}

        for patch_name, patch_type in patches:
            # HARD CONSTRAINT: cyclic/cyclicAMI/empty/processor patches MUST have matching field type
            if self._is_cyclic_patch_type(patch_type):
                lines.extend([f"    {patch_name}", "    {", f"        type            {patch_type};", "    }"])
                continue

            patch_lower = patch_name.lower()

            # PRIORITY 1: Bridge BC registry (from frontend export)
            if patch_name in self._imported_bc_data:
                bc_info = self._imported_bc_data[patch_name]
                bc_type = bc_info.get("bc_type", "wall")
                flow_rate = bc_info.get("flow_rate") or 0.0
                direction = bc_info.get("direction")
                patch_area = bc_info.get("patch_area") or 0.0
                velocity_rows = (bc_info.get("timeVarying") or {}).get("velocity")
                if bc_type == "acu_supply" and patch_name in getattr(self, "_free_supplies", set()):
                    # Supply of a sealed supply region: a pressure inlet whose
                    # flow follows the region's server draw (see
                    # _sealed_region_supplies); paired with totalPressure p_rgh.
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            pressureInletOutletVelocity;",
                        "        value           uniform (0 0 0);",
                        "    }",
                    ])
                elif bc_type == "acu_supply" and velocity_rows:
                    # cfdrun v3: a wall-mounted jet whose flow and/or louvre
                    # angle follow a schedule — U(t) = Q(t)/A · dir(t).
                    first = velocity_rows[0]["value"]
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            uniformFixedValue;",
                        f"        uniformValue    {_format_time_table(velocity_rows)};",
                        f"        value           uniform ({first[0]:.6g} {first[1]:.6g} {first[2]:.6g});",
                        "    }",
                    ])
                elif bc_type == "acu_supply" and direction and patch_area > 0 and flow_rate:
                    # A louvred outlet (wall-mounted unit): the jet leaves along
                    # the louvre direction, not the face normal, so U is a fixed
                    # vector of magnitude Q/A. flowRateInletVelocity cannot tilt.
                    dx, dy, dz = (float(direction[0]), float(direction[1]), float(direction[2]))
                    mag = (dx * dx + dy * dy + dz * dz) ** 0.5 or 1.0
                    speed = float(flow_rate) / float(patch_area)
                    ux, uy, uz = (dx / mag * speed, dy / mag * speed, dz / mag * speed)
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            fixedValue;",
                        f"        value           uniform ({ux:.6g} {uy:.6g} {uz:.6g});",
                        "    }",
                    ])
                elif bc_type == "acu_supply":
                    tv = bc_info.get("timeVarying", {})
                    if tv.get("airflow"):
                        table_str = _format_time_table(tv["airflow"])
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            flowRateInletVelocity;",
                            f"        volumetricFlowRate {table_str};",
                            "    }",
                        ])
                    else:
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            flowRateInletVelocity;",
                            f"        volumetricFlowRate {flow_rate};",
                            "    }",
                        ])
                elif bc_type == "acu_return" and patch_name in getattr(self, "_pinned_returns", {}):
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            flowRateOutletVelocity;",
                        f"        volumetricFlowRate {self._pinned_returns[patch_name]};",
                        "    }",
                    ])
                elif bc_type == "acu_return":
                    # Backflow-stable outlet, paired with the fixedValue p_rgh=0
                    # anchor. Plain zeroGradient U diverges when the local flow
                    # reverses through the return (a recirculation cell in a
                    # confined corner drives inflow through the patch), growing
                    # unboundedly each SIMPLE iteration. pressureInletOutletVelocity
                    # behaves as zeroGradient on outflow but applies the
                    # flux-implied normal velocity on inflow, bounding backflow.
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            pressureInletOutletVelocity;",
                        "        value           uniform (0 0 0);",
                        "    }",
                    ])
                elif bc_type == "fan_inlet":
                    paired_outlet = self._find_server_outlet_patch(patch_name, mesh_patch_names)
                    if paired_outlet not in mesh_patch_names:
                        logger.warning(
                            f"fan_inlet {patch_name}: paired outlet {paired_outlet} "
                            f"not in mesh, using noSlip wall fallback"
                        )
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            noSlip;",
                            "    }",
                        ])
                    else:
                        tv = bc_info.get("timeVarying", {})
                        if tv.get("airflow"):
                            table_str = _format_time_table(tv["airflow"])
                            vol_flow = table_str
                        else:
                            vol_flow = flow_rate
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            flowRateOutletVelocity;",
                            f"        volumetricFlowRate {vol_flow};",
                            "    }",
                        ])
                elif bc_type == "fan_outlet":
                    paired_inlet = self._find_server_inlet_patch(patch_name)
                    if paired_inlet not in mesh_patch_names:
                        logger.warning(
                            f"fan_outlet {patch_name}: paired inlet {paired_inlet} "
                            f"not in mesh, using noSlip wall fallback"
                        )
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            noSlip;",
                            "    }",
                        ])
                    else:
                        tv = bc_info.get("timeVarying", {})
                        if tv.get("airflow"):
                            table_str = _format_time_table(tv["airflow"])
                            vol_flow = table_str
                        else:
                            vol_flow = flow_rate
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            flowRateInletVelocity;",
                            f"        volumetricFlowRate {vol_flow};",
                            "    }",
                        ])
                elif bc_type in ("perforated_baffle", "wall_baffle"):
                    eff = self._resolve_baffle_bc(bc_info)
                    if eff == "wall":
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            noSlip;",
                            "    }",
                        ])
                    else:  # "cyclic" or "porous" → both use cyclic for U
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            cyclic;",
                            "    }",
                        ])
                else:
                    # wall, nonfan_fixed_temp, fixedTemperature, heat_source_flux → noSlip
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            noSlip;",
                        "    }",
                    ])

            elif patch_type == "wall" or "wall" in patch_lower:
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            noSlip;",
                    "    }",
                ])

            else:
                lines.extend(self._default_velocity_patch(patch_name))
        
        lines.extend(self._write_field_footer())
        path.write_text('\n'.join(lines), encoding="utf-8")
    
    def _default_velocity_patch(self, patch_name: str) -> List[str]:
        """Generate default velocity boundary condition."""
        return [
            f"    {patch_name}",
            "    {",
            "        type            fixedValue;",
            "        value           uniform (0 0 0);",
            "    }",
        ]
    
    def _write_t_field(
        self,
        path: Path,
        patches: List[Tuple[str, str]],
        *,
        strict: bool = True,
        static_outlet_temperature: bool = False,
        global_patches: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        """Write temperature field (T).
        
        Boundary condition logic:
        - ACU supply: Fixed temperature (supply air temperature from config)
        - ACU return: zeroGradient
        - Server inlet: zeroGradient (inlet temperature comes from room)
        - Server outlet: Calculated based on inlet temperature + heat rise
        - Walls: zeroGradient (adiabatic)
        """
        # Use room_temperature from config (default 22°C → 295.15 K)
        sim_cfg = self.simulation_config
        room_temp_c = sim_cfg.get("room_temperature", sim_cfg.get("roomTemperature", 22.0))
        init_temperature = room_temp_c + 273.15
        lines = self._write_field_header(path, "volScalarField", "[0 0 0 1 0 0 0]", str(init_temperature))
        
        mesh_patch_names = {p[0] for p in (
            patches if global_patches is None else global_patches
        )}

        for patch_name, patch_type in patches:
            if self._is_cyclic_patch_type(patch_type):
                lines.extend([f"    {patch_name}", "    {", f"        type            {patch_type};", "    }"])
                continue

            patch_lower = patch_name.lower()

            # PRIORITY 1: Bridge BC registry (STLGen pipeline) — must come first
            if patch_name in self._imported_bc_data:
                bc_info = self._imported_bc_data[patch_name]
                bc_type = bc_info.get("bc_type", "wall")
                temperature = bc_info.get("temperature")

                tv = bc_info.get("timeVarying", {})
                if bc_type == "acu_supply" and tv.get("supplyTemp"):
                    # Explicit time-varying supply temperature schedule —
                    # overrides both the transient expression path and the
                    # steady fixedValue path.
                    table_str = _format_time_table(
                        tv["supplyTemp"],
                        transform_fn=lambda v: v + 273.15,
                    )
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            uniformFixedValue;",
                        f"        uniformValue    {table_str};",
                        f"        value           uniform {(temperature or 20) + 273.15};",
                        "    }",
                    ])
                elif bc_type == "acu_supply" and temperature is not None:
                    cooling_capacity = bc_info.get("cooling_capacity")
                    return_patch = bc_info.get("return_patch_name")
                    cm_mode = bc_info.get("cooling_model_mode")
                    _solver_type = (self._simulation_config or {}).get("solverType",
                                   (self._simulation_config or {}).get("solver_type", "steady"))
                    _is_transient = _solver_type == "transient"
                    _use_expression = (
                        cm_mode in ("linearCapacity", "tableCapacity")
                        or (cm_mode is None and cooling_capacity and return_patch and _is_transient)
                    )
                    if _use_expression:
                        flow_rate = bc_info.get("flow_rate") or 4.0
                        mdot = rho_air * flow_rate
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
                            # Linear capacity model:
                            # Q_max = Q_rated * (T_return - T_coolant) / (T_rated - T_coolant)
                            # max(..., 0) prevents negative Q_max when T_return < T_coolant
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
                            perf_table = bc_info.get("cooling_performance_table") or []
                            if not perf_table:
                                perf_table = [{"returnTemp": 25, "capacity": 20},
                                              {"returnTemp": 30, "capacity": 30},
                                              {"returnTemp": 35, "capacity": 35}]
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
                        else:
                            # Legacy Level 0 expression (existing code):
                            # Q_actual = min(max(Q_needed, 0), Q_max)
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
                    else:
                        if cm_mode == "fixedTemp":
                            _supply_c = temperature
                        else:
                            set_point = bc_info.get("set_point_temp")
                            _supply_c = set_point if set_point is not None else temperature
                        temp_k = _supply_c + 273.15
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
                elif bc_type == "nonfan_heat_flux":
                    heat_flux = bc_info.get("heat_flux", 0)
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
                elif bc_type == "heat_source_flux":
                    heat_flux = bc_info.get("heat_flux") or 0.0
                    tv = bc_info.get("timeVarying", {})
                    if tv.get("heatOutput"):
                        surface_area = bc_info.get("surface_area", 1.0)
                        table_str = _format_time_table(
                            tv["heatOutput"],
                            transform_fn=lambda w: w / surface_area,
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
                elif bc_type == "fan_outlet":
                    # Fan-server outlet has EXACTLY ONE supported form: the
                    # temperature-rise BC. No fallbacks. Any condition that
                    # prevents computing ΔT (missing power/flow-rate, paired
                    # inlet patch dropped by snappy) is a user-visible error,
                    # not a silent degradation — otherwise the server's heat
                    # load just disappears from the thermodynamic balance and
                    # the converged field is physically wrong without the user
                    # ever being told.
                    #
                    #     T_outlet = sum(phi*T on inlet)/sum(phi on inlet) + ΔT
                    #     ΔT = power / (rho · Cp · volumeFlowRate)
                    #
                    # Multi-patch splitting: when a server has N outlet patches,
                    # _rebalance_fan_outlets gives each its area share of Q and
                    # P, so per-patch ΔT equals whole-server ΔT.
                    # The paired inlet is resolved by matching server stem
                    # (before "_outlet_…") against registered fan_inlet patches
                    # — works for 1-inlet-N-outlet, N-inlet-1-outlet, symmetric.
                    power = bc_info.get("power") or 0.0
                    flow_rate = bc_info.get("flow_rate") or 0.0
                    air_cp = air_specific_heat
                    mass_flow = flow_rate * rho_air
                    if mass_flow <= 0:
                        msg = (
                            f"fan_outlet '{patch_name}': cannot apply temperature-rise "
                            f"BC because mass flow is zero (volumeFlowRate={flow_rate}, "
                            f"power={power}). Fan-server outlets have exactly one BC "
                            f"form — ΔT = power / (rho·Cp·Q) — and require a positive "
                            f"volumeFlowRate. Set the server's volumeFlowRate in the UI, "
                            f"or remove this server if it isn't meant to have heat load."
                        )
                        if strict:
                            raise RuntimeError(msg)
                        # Mesh-path placeholder: fixedValue at ambient. Clearly
                        # non-physical; the solve gate will raise the real
                        # error when the user actually tries to run solver.
                        logger.warning(f"[mesh-placeholder] {msg}")
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            fixedValue;",
                            f"        value           uniform {init_temperature};",
                            "    }",
                        ])
                        continue
                    inlet_ref = self._find_server_inlet_patch(patch_name)
                    if inlet_ref not in mesh_patch_names:
                        msg = (
                            f"fan_outlet '{patch_name}': paired inlet patch "
                            f"'{inlet_ref}' is missing from the final mesh. This "
                            f"typically means snappyHexMesh dropped a thin (1U-wide) "
                            f"inlet face below its cell-size threshold. The temperature"
                            f"-rise BC references the inlet face via its phi-weighted T, "
                            f"so without it the BC cannot be built and there is no "
                            f"supported fallback. Raise the server's refinement level "
                            f"(serverFaceRefinementLevel / per-U override) so snappy "
                            f"preserves the inlet face, or adjust the geometry."
                        )
                        if strict:
                            raise RuntimeError(msg)
                        logger.warning(f"[mesh-placeholder] {msg}")
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            fixedValue;",
                            f"        value           uniform {init_temperature};",
                            "    }",
                        ])
                        continue
                    delta_t = power / (mass_flow * air_cp)
                    lam = FEEDBACK_DAMPING_LAMBDA
                    if static_outlet_temperature:
                        static_t = init_temperature + delta_t
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            fixedValue;",
                            f"        value           uniform {static_t};",
                            "    }",
                        ])
                    else:
                        safe_id = patch_name.replace("-", "_").replace(" ", "_")
                        t_sink = f"tSink_{safe_id}"
                        tv = bc_info.get("timeVarying", {})
                        tv_power = tv.get("power") if tv else None
                        tv_power_rows = tv.get("powerRows") if tv else None
                        # Damped feedback, same fixed point — see
                        # FEEDBACK_DAMPING_LAMBDA. `tOutPrev` is the outlet
                        # patch's own weightAverage(T), i.e. last iteration's
                        # value, which is what makes the lag a lag.
                        t_out_prev = f"tOutPrev_{safe_id}"
                        # phi is volumetric face flux (m³/s), so an area average
                        # is not the transported inlet enthalpy when T and velocity
                        # vary across the inlet. sum() reduces across MPI ranks.
                        damped_vars = [
                            f"                \"{t_sink}{{{inlet_ref}}} = sum(phi*T)/sum(phi)\"",
                            f"                \"{t_out_prev}{{{patch_name}}} = weightAverage(T)\"",
                        ]
                        if tv_power_rows:
                            # cfdrun v3: the power schedule's corner rows,
                            # followed linearly (ramps included).
                            dt_expr = _build_piecewise_linear_expression(
                                tv_power_rows, 1.0 / (mass_flow * air_cp),
                            )
                            expr = (
                                f"{lam}*{t_sink}+{1.0 - lam}*{t_out_prev}"
                                f"+{lam}*({dt_expr})"
                            )
                        elif tv_power:
                            dt_expr = _build_piecewise_dt_expression(
                                tv_power, mass_flow, air_cp,
                            )
                            expr = (
                                f"{lam}*{t_sink}+{1.0 - lam}*{t_out_prev}"
                                f"+{lam}*({dt_expr})"
                            )
                        else:
                            expr = (
                                f"{lam}*{t_sink}+{1.0 - lam}*{t_out_prev}"
                                f"+{lam * delta_t:.16g}"
                            )
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            uniformFixedValue;",
                            f"        value           uniform {init_temperature};",
                            "        uniformValue",
                            "        {",
                            "            type            expression;",
                            f"            expression       \"{expr}\";",
                            "            variables",
                            "            (",
                            *damped_vars,
                            "            );",
                            "        };",
                            "    }",
                        ])
                elif bc_type in ("perforated_baffle", "wall_baffle"):
                    eff = self._resolve_baffle_bc(bc_info)
                    if eff == "wall":
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            zeroGradient;",
                            "    }",
                        ])
                    else:  # "cyclic" or "porous" → cyclic for T
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            cyclic;",
                            "    }",
                        ])
                else:
                    # fan_inlet, acu_return, leakage_zone, wall → zeroGradient.
                    # A fan inlet's flowRateOutletVelocity removes all reverse
                    # flow, so its no-flow faces carry phi = +-0; inletOutlet
                    # read -0 as backflow and pinned them to the initial
                    # temperature, conducting heat into the room (Tri_angle:
                    # ~118 W, docs/benchmarks/tri-angle-heat-gap-2026-10-25/).
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            zeroGradient;",
                        "    }",
                    ])

            elif "_server_" in patch_lower and ("_inlet" in patch_lower or "_outlet" in patch_lower):
                raise RuntimeError(
                    f"server-shaped patch '{patch_name}' reached the generic T BC "
                    f"fallthrough — it was NOT registered in the bridge BC "
                    f"registry (_imported_bc_data). Fan-server patches require "
                    f"a bridge BC entry (fan_inlet/fan_outlet) so the "
                    f"temperature-rise BC can be built with the correct power "
                    f"and flow-rate. Check _build_bridge_bc() in stlgen_runner.py "
                    f"— either the server is missing a patch registration, or "
                    f"this patch was renamed between registration and mesh build."
                )

            # NOTE: All bridge BC patches (including heat_source_flux) are handled
            # by the PRIORITY 1 block above (line 535). No duplicate elif needed here.

            elif patch_type == "wall" or "wall" in patch_lower:
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            zeroGradient;",
                    "    }",
                ])

            else:
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            zeroGradient;",
                    "    }",
                ])
        
        lines.extend(self._write_field_footer())
        path.write_text('\n'.join(lines), encoding="utf-8")
    
    def _write_p_field(self, path: Path, patches: List[Tuple[str, str]]) -> None:
        """Write pressure field (p)."""
        lines = self._write_field_header(path, "volScalarField", "[0 2 -2 0 0 0 0]", "0")

        for patch_name, patch_type in patches:
            if self._is_cyclic_patch_type(patch_type):
                lines.extend([f"    {patch_name}", "    {", f"        type            {patch_type};", "    }"])
                continue
            else:
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            calculated;",
                    "        value           uniform 0;",
                    "    }",
                ])
        
        lines.extend(self._write_field_footer())
        path.write_text('\n'.join(lines), encoding="utf-8")
    
    def _is_perforated_baffle_patch(self, patch_name: str) -> bool:
        """Check if a patch is a perforated baffle (opening_*_master or opening_*_slave)."""
        patch_lower = patch_name.lower()
        return ("opening_" in patch_lower and 
                (patch_lower.endswith("_master") or patch_lower.endswith("_slave")))
    
    def _write_p_rgh_field(self, path: Path, patches: List[Tuple[str, str]]) -> None:
        """Write pressure field (p_rgh).

        Boundary condition logic:
        - ACU supply / server inlet / fan inlet: fixedFluxPressure
        - ACU return: fixedValue 0 pressure reference
        - Perforated baffles (opening_*): porousBafflePressure (pressure drop)
        - Walls / others: fixedFluxPressure
        """
        lines = self._write_field_header(path, "volScalarField", "[0 2 -2 0 0 0 0]", "0")

        for patch_name, patch_type in patches:
            if self._is_cyclic_patch_type(patch_type):
                # A perforated baffle the mesher wrote as a cyclic pair keeps
                # its pressure drop: porousBafflePressure is a cyclic jump
                # (patchType cyclic), so the patch/patchField types still
                # agree. dcCart writes these pairs itself (no createBaffles);
                # taking every cyclic patch as pure cyclic made Base2-9's
                # perforated floor (sigma 0.64, 3 mm holes) and blanking
                # panels fully open.
                bc_info = self._imported_bc_data.get(patch_name) if patch_type.lower() == "cyclic" else None
                if (bc_info is not None and bc_info.get("bc_type") == "perforated_baffle"
                        and self._resolve_baffle_bc(bc_info) == "porous"):
                    spec = BCSpec(**bc_info) if not isinstance(bc_info, BCSpec) else bc_info
                    block_lines, D, I, length = _format_porous_baffle_block(patch_name, spec)
                    lines.extend(block_lines)
                    logger.info(f"porousBafflePressure for {patch_name} (mesher cyclic pair): "
                                f"sigma={spec.sigma}, D={D:.4f}, I={I:.4f}")
                    continue
                lines.extend([f"    {patch_name}", "    {", f"        type            {patch_type};", "    }"])
                continue

            patch_lower = patch_name.lower()

            # PRIORITY 1: Bridge BC registry (STLGen pipeline) — pressure BCs
            if patch_name in self._imported_bc_data:
                bc_info = self._imported_bc_data[patch_name]
                bc_type = bc_info.get("bc_type")
                if bc_type == "acu_supply" and patch_name in getattr(self, "_free_supplies", set()):
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            totalPressure;",
                        "        p0              uniform 0;",
                        "        value           uniform 0;",
                        "    }",
                    ])
                elif bc_type == "acu_supply":
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            fixedFluxPressure;",
                        "        value           uniform 0;",
                        "    }",
                    ])
                elif bc_type == "acu_return" and patch_name in getattr(self, "_pinned_returns", {}):
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            fixedFluxPressure;",
                        "        value           uniform 0;",
                        "    }",
                    ])
                elif bc_type == "acu_return":
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            fixedValue;",
                        "        value           uniform 0;",
                        "    }",
                    ])
                elif bc_type == "fan_inlet":
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            fixedFluxPressure;",
                        "        value           uniform 0;",
                        "    }",
                    ])
                elif bc_type in ("perforated_baffle", "wall_baffle"):
                    eff = self._resolve_baffle_bc(bc_info)
                    if eff == "wall":
                        # Wall baffle or perforated with 0% porosity → fixedFluxPressure
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            fixedFluxPressure;",
                            "        value           uniform 0;",
                            "    }",
                        ])
                    elif eff == "cyclic":
                        # 100% porosity → pure cyclic (no resistance)
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            cyclic;",
                            "    }",
                        ])
                    else:
                        # "porous": 0 < porosity < 1 → porousBafflePressure
                        # bc_info is a dict view of a BCSpec; reconstruct to
                        # access the perforated field.
                        spec = BCSpec(**bc_info) if not isinstance(bc_info, BCSpec) else bc_info
                        block_lines, D, I, length = _format_porous_baffle_block(patch_name, spec)
                        lines.extend(block_lines)
                        logger.info(f"porousBafflePressure for {patch_name}: "
                                    f"shape={getattr(spec.perforated, 'shape', None)}, "
                                    f"sigma={spec.sigma}, D={D:.4f}, I={I:.4f}")
                else:
                    # Other bridge BC types: fixedFluxPressure
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            fixedFluxPressure;",
                        "        value           uniform 0;",
                        "    }",
                    ])

            elif "_server_" in patch_lower and "_inlet" in patch_lower:
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            fixedFluxPressure;",
                    "        value           uniform 0;",
                    "    }",
                ])
            # Perforated baffle patches - use porousBafflePressure
            elif self._is_perforated_baffle_patch(patch_name):
                spec = self.bridge_registry.get(patch_name) if self.bridge_registry is not None else None
                if spec is None:
                    # Defensive: should not happen for perforated patches.
                    spec = BCSpec(bc_type="perforated_baffle", sigma=0.5)
                block_lines, D, I, length = _format_porous_baffle_block(patch_name, spec)
                lines.extend(block_lines)
                logger.info(f"porousBafflePressure for {patch_name}: "
                            f"shape={getattr(spec.perforated, 'shape', None)}, "
                            f"sigma={spec.sigma}, D={D:.4f}, I={I:.4f} (via Idelchik)")
            elif patch_type.lower() == "cyclicami":
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            cyclicAMI;",
                    "    }",
                ])
            elif patch_type.lower() == "cyclic":
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            cyclic;",
                    "    }",
                ])
            else:
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            fixedFluxPressure;",
                    "        value           uniform 0;",
                    "    }",
                ])

        lines.extend(self._write_field_footer())
        path.write_text('\n'.join(lines), encoding="utf-8")

    # BC types that represent flow patches (inlets/outlets), NOT walls.
    # Patches with these bridge BC types must NOT receive wall functions,
    # even if their name contains "wall" (e.g. "left_fan_wall_room_crac_1_Front_patch").
    _FLOW_BC_TYPES = frozenset({
        "acu_supply", "acu_return", "fan_inlet", "fan_outlet",
    })

    # Subset: domain-outlet flow types (air exits the CFD domain).
    # These need inletOutlet (not fixedValue) for k/epsilon to avoid
    # fighting the internal solution and causing turbulence blow-up.
    _DOMAIN_OUTLET_BC_TYPES = frozenset({
        "acu_return",   # hot air flows OUT of room to ACU
        "fan_inlet",    # air flows OUT of room into server
    })

    def _is_flow_patch(self, patch_name: str) -> bool:
        """Return True if patch_name is a flow patch per the bridge BC registry.

        Flow patches (inlets/outlets) must NOT receive wall-function BCs,
        even when the patch name happens to contain 'wall'.
        """
        if patch_name in self._imported_bc_data:
            bc_type = self._imported_bc_data[patch_name].get("bc_type", "")
            if bc_type in self._FLOW_BC_TYPES:
                return True
        return False

    def _find_server_inlet_patch(self, outlet_patch_name: str) -> str:
        """Resolve the inlet patch to reference in a fan_outlet temperature
        expression, robust to multi-outlet / multi-inlet naming variants.

        Outlet patch solid names follow one of:
          {stem}_outlet_patch        — single patch, whole face
          {stem}_outlet_p{idx}       — per-patch (isPatch=true in frontend)

        Inlet equivalents use `_inlet_patch` or `_inlet_p{idx}`. We strip the
        outlet suffix to get the server stem, then try an EXACT
        `{stem}_inlet_patch` lookup among the registered fan_inlet patches
        first: stacked servers produce stems in prefix relation (`X` vs
        `X_2`), and the prefix scan below could pair across servers or pick a
        per-patch sibling depending on registry order. Only when no exact
        whole-face inlet exists do we scan _imported_bc_data for any fan_inlet
        patch whose name starts with `{stem}_inlet`. This handles:
          - N outlets → 1 whole-face inlet (stem_inlet_patch)
          - N outlets → N inlet patches (picks the first; averaging across
            multiple would require expression-based multi-patch syntax)
          - Symmetric 1-1 legacy mapping
        Falls back to the legacy string replacement if no match found.
        """
        # Extract stem = portion before "_outlet_"
        idx = outlet_patch_name.rfind("_outlet_")
        if idx > 0:
            stem = outlet_patch_name[:idx]
            # Exact whole-face match wins over the prefix scan.
            exact_name = f"{stem}_inlet_patch"
            exact_info = self._imported_bc_data.get(exact_name)
            if exact_info is not None and exact_info.get("bc_type") == "fan_inlet":
                return exact_name
            prefix = f"{stem}_inlet"
            for other_name, other_info in self._imported_bc_data.items():
                if (other_info.get("bc_type") == "fan_inlet"
                        and other_name.startswith(prefix)):
                    return other_name
            # A liquid-cooled fan server's air inlet is its front face,
            # exported as `{stem}_front` with role fan_inlet.
            front_info = self._imported_bc_data.get(f"{stem}_front")
            if front_info is not None and front_info.get("bc_type") == "fan_inlet":
                return f"{stem}_front"
        # Fallback: legacy pattern substitution
        return outlet_patch_name.replace("_outlet_patch", "_inlet_patch") \
                                .replace("_outlet", "_inlet")

    def _find_server_outlet_patch(
        self, inlet_patch_name: str, mesh_patch_names: Optional[set] = None,
    ) -> str:
        """Mirror of _find_server_inlet_patch for the outlet direction.

        Given a fan_inlet patch, return the paired fan_outlet patch name:
        exact `{stem}_outlet_patch` among the registered fan_outlet patches
        first, prefix scan as fallback, then the legacy string-substitution
        fallback (see _find_server_inlet_patch for the stacked-server
        cross-pairing rationale).

        With ``mesh_patch_names`` the prefix scan takes, in name order, the
        first outlet patch that is meshed and carries air. A server whose
        outlet is split into sub-patches can lose one of them to the STL
        export (overlapping sub-patches clip each other): DAML-Testbed's
        RTXpro6000 lost p0, the scan returned p0 by registry order, the
        caller saw "outlet not in mesh" and turned the inlet into a wall --
        the outlets kept blowing 0.5 m3/s, their dT expression divided by
        the inlet's zero flux, and T ran to the 1.7 K clip within a few
        hundred iterations.
        """
        idx = inlet_patch_name.rfind("_inlet_")
        if idx <= 0 and inlet_patch_name.endswith("_front"):
            idx = len(inlet_patch_name) - len("_front")   # liquid-cooled fan server
        if idx > 0:
            stem = inlet_patch_name[:idx]
            # Exact whole-face match wins over the prefix scan.
            exact_name = f"{stem}_outlet_patch"
            exact_info = self._imported_bc_data.get(exact_name)
            if exact_info is not None and exact_info.get("bc_type") == "fan_outlet":
                return exact_name
            prefix = f"{stem}_outlet"
            candidates = [
                other_name for other_name, other_info in self._imported_bc_data.items()
                if other_info.get("bc_type") == "fan_outlet" and other_name.startswith(prefix)
            ]
            if mesh_patch_names is not None:
                live = sorted(
                    n for n in candidates
                    if n in mesh_patch_names and (self._imported_bc_data[n].get("flow_rate") or 0) > 0
                )
                if live:
                    return live[0]
            if candidates:
                return candidates[0]
        return inlet_patch_name.replace("_inlet_patch", "_outlet_patch") \
                               .replace("_inlet", "_outlet")

    def _resolve_baffle_bc(self, bc_info: Dict[str, Any]) -> str:
        """Resolve effective BC category for baffle patches considering porosity edge cases.

        Returns one of: "wall", "cyclic", "porous"
        - "wall": no-slip (wall_baffle, or perforated_baffle with sigma<=0)
        - "cyclic": pure cyclic (perforated_baffle with sigma>=1.0)
        - "porous": cyclic + porousBafflePressure (perforated_baffle with 0<sigma<1)
        """
        bc_type = bc_info.get("bc_type", "wall")
        if bc_type == "wall_baffle":
            return "wall"
        if bc_type == "perforated_baffle":
            sigma = bc_info.get("sigma", 0.5)
            if sigma is None:
                sigma = 0.5
            if sigma >= 1.0:
                return "cyclic"
            elif sigma <= 0:
                return "wall"
            else:
                return "porous"
        return "wall"

    def _compute_internal_field_seed(self, patches: List[Tuple[str, str]]) -> Tuple[float, float]:
        """Compute internalField k and epsilon from ACU supply patches only.

        Fan-server patches are domain-internal circulation and must not
        set the domain-average bulk seed (CFD-Wiki "free-stream BC",
        Dogan 2022 uniform internalField, every commercial tool surveyed).
        Fallback: OpenFOAM hotRoom canonical (k=0.1, eps=0.01).
        """
        k_values = []
        eps_values = []
        for patch_name, _ in patches:
            bc_type = self._imported_bc_data.get(patch_name, {}).get("bc_type", "")
            if bc_type != "acu_supply":
                continue
            result = self._compute_turbulence_bc(patch_name)
            if result:
                k_values.append(result[0])
                eps_values.append(result[1])
        if k_values:
            k_avg = max(sum(k_values) / len(k_values), self.K_MIN_PHYSICAL)
            eps_avg = max(sum(eps_values) / len(eps_values), self.EPS_MIN_PHYSICAL)
            logger.info(f"InternalField seed: k={k_avg:.4f}, eps={eps_avg:.4f} "
                        f"from {len(k_values)} ACU supply patches")
            return k_avg, eps_avg
        logger.info("No ACU supply patches — using hotRoom canonical seed (k=0.1, eps=0.01)")
        return 0.1, 0.01

    def _write_turbulence_field(self, path: Path, patches: List[Tuple[str, str]],
                                internal_value: float, field_class: str, dimensions: str,
                                field_name: str) -> None:
        """Write k or epsilon field with per-patch computed values for flow inlets."""
        lines = self._write_field_header(path, field_class, dimensions, f"{internal_value:.6g}")

        wall_type = "kqRWallFunction" if field_name == "k" else "epsilonWallFunction"

        for patch_name, patch_type in patches:
            if self._is_cyclic_patch_type(patch_type):
                lines.extend([f"    {patch_name}", "    {",
                              f"        type            {patch_type};", "    }"])
                continue

            # Flow patches: use OpenFOAM native turbulent-intensity /
            # mixing-length BCs for inlets. They auto-recompute k and ε
            # from live inlet velocity each iter and fall back to
            # zeroGradient on reverse flow.
            if self._is_flow_patch(patch_name):
                bc_type = self._imported_bc_data.get(patch_name, {}).get("bc_type", "")

                # Domain outlets: keep existing inletOutlet guard — value is
                # only used on backflow, per-patch, no native BC needed.
                # Check BEFORE _compute_turbulence_bc to avoid "Unknown
                # bc_type" warning noise for fan_inlet / acu_return.
                if bc_type in self._DOMAIN_OUTLET_BC_TYPES:
                    lines.extend([f"    {patch_name}", "    {",
                                  "        type            inletOutlet;",
                                  f"        inletValue      uniform {internal_value:.6g};",
                                  f"        value           uniform {internal_value:.6g};", "    }"])
                    continue

                result = self._compute_turbulence_bc(patch_name)

                # Domain inlets: render native turbulentIntensity / mixingLength BCs
                if result:
                    k_val, eps_val, I_phys, L, U_phys, U_face = result
                    I_eff = I_phys * U_phys / U_face if U_face > 1e-6 else I_phys
                    seed = k_val if field_name == "k" else eps_val
                    if field_name == "k":
                        lines.extend([f"    {patch_name}", "    {",
                                      "        type            turbulentIntensityKineticEnergyInlet;",
                                      f"        intensity       {I_eff:.6g};",
                                      f"        value           uniform {seed:.6g};", "    }"])
                    else:  # epsilon
                        lines.extend([f"    {patch_name}", "    {",
                                      "        type            turbulentMixingLengthDissipationRateInlet;",
                                      f"        mixingLength    {L:.6g};",
                                      f"        value           uniform {seed:.6g};", "    }"])
                else:
                    lines.extend([f"    {patch_name}", "    {",
                                  "        type            fixedValue;",
                                  f"        value           uniform {internal_value:.6g};", "    }"])
                continue

            # Baffle patches
            bc_info = self._imported_bc_data.get(patch_name, {})
            if bc_info.get("bc_type") in ("perforated_baffle", "wall_baffle"):
                eff = self._resolve_baffle_bc(bc_info)
                if eff == "wall":
                    lines.extend([f"    {patch_name}", "    {",
                                  f"        type            {wall_type};",
                                  f"        value           uniform {internal_value:.6g};", "    }"])
                else:
                    lines.extend([f"    {patch_name}", "    {",
                                  "        type            cyclic;", "    }"])
                continue

            # Wall patches (default)
            lines.extend([f"    {patch_name}", "    {",
                          f"        type            {wall_type};",
                          f"        value           uniform {internal_value:.6g};", "    }"])

        lines.extend(["}", "", "// ************************************************************************* //"])
        path.write_text("\n".join(lines), encoding="utf-8")

    def _write_simple_scalar_field(self, path: Path, patches: List[Tuple[str, str]],
                                   internal_value: str, field_class: str, dimensions: str) -> None:
        """Write a simple scalar field with uniform boundary conditions.

        IMPORTANT: Bridge BC flow patches (acu_supply, acu_return, fan_inlet,
        fan_outlet) are handled BEFORE the wall-name heuristic, so that patches
        whose names contain "wall" but are actually flow inlets/outlets do not
        receive wall functions. Derived nut/alphat use calculated boundaries so
        the turbulence and thermal models can update them; other scalar fields
        retain their fixed-value contract.
        """
        lines = self._write_field_header(path, field_class, dimensions, internal_value)

        for patch_name, patch_type in patches:
            if self._is_cyclic_patch_type(patch_type):
                lines.extend([f"    {patch_name}", "    {", f"        type            {patch_type};", "    }"])
                continue
            elif self._is_flow_patch(patch_name):
                # Model-derived diffusivities must remain assignable. A fixedValue
                # patch ignores nut=Cmu*k^2/epsilon and alphat=nut/Prt updates.
                bc_type = "calculated" if path.name in {"nut", "alphat"} else "fixedValue"
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    f"        type            {bc_type};",
                    f"        value           uniform {internal_value};",
                    "    }",
                ])
            elif patch_name in self._imported_bc_data and \
                    self._imported_bc_data[patch_name].get("bc_type") in ("perforated_baffle", "wall_baffle"):
                # PRIORITY 2: Baffle patches — resolve based on porosity
                eff = self._resolve_baffle_bc(self._imported_bc_data[patch_name])
                if eff == "wall":
                    # Wall baffle or 0% porosity → wall functions (alphat: the thermal one, as on walls below)
                    if path.name == "nut":
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            nutkWallFunction;",
                            f"        value           uniform {internal_value};",
                            "    }",
                        ])
                    elif path.name == "alphat":
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            alphatJayatillekeWallFunction;",
                            "        Prt             0.85;",
                            f"        value           uniform {internal_value};",
                            "    }",
                        ])
                    elif path.name == "k":
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            kqRWallFunction;",
                            f"        value           uniform {internal_value};",
                            "    }",
                        ])
                    elif path.name in ["epsilon", "omega"]:
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            epsilonWallFunction;",
                            f"        value           uniform {internal_value};",
                            "    }",
                        ])
                    else:
                        lines.extend([
                            f"    {patch_name}",
                            "    {",
                            "        type            zeroGradient;",
                            "    }",
                        ])
                else:
                    # cyclic or porous → cyclic for turbulence fields
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            cyclic;",
                        "    }",
                    ])
            elif patch_type == "wall" or "wall" in patch_name.lower():
                # Use appropriate wall function
                if path.name == "nut":
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            nutkWallFunction;",
                        f"        value           uniform {internal_value};",
                        "    }",
                    ])
                elif path.name == "alphat":
                    # Boussinesq thermal wall function: Jayatilleke.
                    # In Boussinesq (incompressible) solvers the wall function
                    # is registered WITHOUT a namespace prefix. Using
                    # nutkWallFunction on alphat is incorrect — it produces
                    # bogus thermal diffusivity near walls and causes T
                    # equation NaN at iteration 1 on meshes with many wall
                    # faces.
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            alphatJayatillekeWallFunction;",
                        "        Prt             0.85;",
                        f"        value           uniform {internal_value};",
                        "    }",
                    ])
                elif path.name == "k":
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            kqRWallFunction;",
                        f"        value           uniform {internal_value};",
                        "    }",
                    ])
                elif path.name in ["epsilon", "omega"]:
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            epsilonWallFunction;",
                        f"        value           uniform {internal_value};",
                        "    }",
                    ])
                else:
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            zeroGradient;",
                        "    }",
                    ])
            else:
                lines.extend([
                    f"    {patch_name}",
                    "    {",
                    "        type            fixedValue;",
                    f"        value           uniform {internal_value};",
                    "    }",
                ])
        
        lines.extend(self._write_field_footer())
        path.write_text('\n'.join(lines), encoding="utf-8")
