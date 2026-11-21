"""Pump-law research gate v2: the product solver's own pump law is the nominal affinity law.

Supersedes ``agent_legacy`` ``harness/pump_affinity_math_v1.py`` and
``harness/pump_dae_nominal_affinity_v1.py`` (retired 2026-10-22). v1 laid an
affinity correction over a frozen native law ``N^2 (H0 - a Q^2)`` and pinned that
solver's source hashes. Since 2026-10-21 the product solver itself uses
``H = N^2 H0 - a Q|Q|`` (``PRODUCT_PUMP_LAW``, recorded in every result's
``parameter_provenance.solver_laws.pump_law``), so there is nothing left to
correct; v2 checks the product instead, through ``agent.scripted_api`` only.

Two parts:

- ``QuadraticPumpCurve``: scalar controls. For forward flow the product law is
  exactly the nominal affinity transform ``N^2 H_ref(Q/N)`` of the rated-speed
  curve ``H_ref(Q) = H0 - a Q^2``; for reverse flow it adds head (``N^2 H0 + a Q^2``),
  so a stopped or slowed pump dissipates reverse flow. v1's law is kept as
  ``legacy_v1_head_m`` for comparison only.
- ``measure_pump_heads``: solve the steady state of a DAE ``.liquidrun`` at given
  speed ratios (``agent.scripted_api.solve_liquid_steady``) and read each pump's head
  back from the solved node pressures and heights,
  ``H = (p_to - p_from) / (rho g) + (z_to - z_from)`` along ``from -> to``, beside
  the head both laws predict at the solved flow. ``solved_pump_heads`` reads the
  same rows from an answer already solved (the LF oracle's liquid step).

SI units throughout: m, m^3/s, kg/s, Pa. Curve parameters are the pump profile's
``H0_m`` and ``a_coef`` (m per (m^3/s)^2). No efficiency, power or measured pump
data is implied.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

GATE_VERSION = "research-pump-affinity/v2"
PRODUCT_PUMP_LAW = "affinity_N2H0_minus_aQabsQ/v3"


def _finite(name: str, value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite real scalar")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real scalar") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


@dataclass(frozen=True)
class QuadraticPumpCurve:
    """Rated-speed ``H_ref(Q) = H0 - a Q^2`` (Q >= 0) and the laws built on it."""
    shutoff_head_m: float
    head_flow_coefficient_m_per_m3s_squared: float

    def __post_init__(self):
        object.__setattr__(self, "shutoff_head_m", _finite("shutoff_head_m", self.shutoff_head_m, positive=True))
        object.__setattr__(self, "head_flow_coefficient_m_per_m3s_squared",
                           _finite("head_flow_coefficient_m_per_m3s_squared",
                                   self.head_flow_coefficient_m_per_m3s_squared, nonnegative=True))

    @property
    def a(self) -> float:
        return self.head_flow_coefficient_m_per_m3s_squared

    def reference_head_m(self, flow_m3s: float) -> float:
        flow = _finite("flow_m3s", flow_m3s, nonnegative=True)
        return self.shutoff_head_m - self.a * flow ** 2

    def product_head_m(self, flow_m3s: float, speed_ratio: float) -> float:
        """The product law ``N^2 H0 - a Q|Q|``; ``flow_m3s`` is signed (negative = reverse)."""
        flow = _finite("flow_m3s", flow_m3s)
        speed = _finite("speed_ratio", speed_ratio, nonnegative=True)
        return speed ** 2 * self.shutoff_head_m - self.a * flow * abs(flow)

    def nominal_affinity_head_m(self, flow_m3s: float, speed_ratio: float) -> float:
        """``N^2 H_ref(Q/N)`` for forward flow at a running pump."""
        flow = _finite("flow_m3s", flow_m3s, nonnegative=True)
        speed = _finite("speed_ratio", speed_ratio, positive=True)
        return speed ** 2 * self.reference_head_m(flow / speed)

    def legacy_v1_head_m(self, flow_m3s: float, speed_ratio: float) -> float:
        """The retired v1 native law ``N^2 (H0 - a Q^2)``, for comparison only."""
        speed = _finite("speed_ratio", speed_ratio, nonnegative=True)
        return speed ** 2 * self.reference_head_m(flow_m3s)

    def corresponding_point(self, nominal_flow_m3s: float, speed_ratio: float) -> Dict[str, float]:
        """Affinity: ``Q -> N Q`` and ``H -> N^2 H``; the product law meets it exactly."""
        flow = _finite("nominal_flow_m3s", nominal_flow_m3s, nonnegative=True)
        speed = _finite("speed_ratio", speed_ratio, positive=True)
        target = speed * flow
        return {"speed_ratio": speed, "nominal_flow_m3s": flow, "target_flow_m3s": target,
                "affinity_expected_head_m": speed ** 2 * self.reference_head_m(flow),
                "product_head_m": self.product_head_m(target, speed),
                "legacy_v1_head_m": self.legacy_v1_head_m(target, speed)}


def _pump_curve(liquidrun: Mapping[str, Any], edge: Mapping[str, Any]) -> QuadraticPumpCurve:
    profiles = ((liquidrun.get("parameter_pack") or {}).get("profiles") or {})
    params = dict((profiles.get(edge.get("parameter_ref")) or {}).get("params") or {})
    params.update(edge.get("params") or {})
    if "H0_m" not in params or "a_coef" not in params:
        raise ValueError(f"pump {edge.get('id')!r} has no H0_m / a_coef in its profile or params")
    return QuadraticPumpCurve(params["H0_m"], params["a_coef"])


def _height(position: Sequence[float], gravity: Sequence[float]) -> float:
    g = math.sqrt(sum(c * c for c in gravity))
    if not g:
        raise ValueError("the liquidrun gravity vector is zero")
    return -sum(p * c for p, c in zip(position, gravity)) / g


def _pumps(liquidrun: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    pumps = [e for e in liquidrun.get("edges") or [] if e.get("type") == "pump"]
    if not pumps:
        raise ValueError("this gate needs a liquidrun with at least one pump")
    return pumps


def solved_pump_heads(liquidrun: Mapping[str, Any], steady: Mapping[str, Any],
                      speeds: Optional[Mapping[str, float]] = None) -> List[Dict[str, Any]]:
    """One row per pump of an already solved steady answer (``solve_liquid_steady``'s dictionary).

    The speed of each pump is ``speeds[pump id]``, else the ratio the answer
    reports it ran at (``controls.pump_rpm_ratios``). Each row holds the signed
    flow, the head measured from the solved pressures and heights, the static
    pressure rise ``p_to - p_from`` and the head the product law gives at that flow.
    """
    rho = _finite("fluid.rho_kg_m3", (liquidrun.get("fluid") or {}).get("rho_kg_m3"), positive=True)
    gravity = liquidrun.get("gravity") or [0.0, -9.81, 0.0]
    g = math.sqrt(sum(c * c for c in gravity))
    metrics = steady["metrics"]
    nodes = {n["id"]: n for n in metrics["nodes"]}
    edges = {e["id"]: e for e in metrics["edges"]}
    reported = (steady.get("controls") or {}).get("pump_rpm_ratios") or {}
    rows: List[Dict[str, Any]] = []
    for pump in _pumps(liquidrun):
        speed = _finite("speed", (speeds or {}).get(pump["id"], reported.get(pump["id"])), positive=True)
        solved_edge = edges[pump["id"]]
        # Signed along the modelled from -> to: the solved direction says which way it ran.
        mass = float(solved_edge["massFlowKgS"])
        if solved_edge.get("upstream") == pump["to"]:
            mass = -abs(mass)
        flow = mass / rho
        p_from, p_to = nodes[pump["from"]], nodes[pump["to"]]
        rise = p_to["pressurePa"] - p_from["pressurePa"]
        measured = rise / (rho * g) + _height(p_to["positionM"], gravity) - _height(p_from["positionM"], gravity)
        curve = _pump_curve(liquidrun, pump)
        product = curve.product_head_m(flow, speed)
        rows.append({"speed_ratio": speed, "pump": pump["id"], "flow_m3s": flow, "mass_flow_kg_s": mass,
                     "static_pressure_rise_Pa": rise, "rho_kg_m3": rho, "gravity_m_s2": g,
                     "measured_head_m": measured, "product_head_m": product,
                     "legacy_v1_head_m": curve.legacy_v1_head_m(abs(flow), speed),
                     "relative_error": abs(measured - product) / max(abs(product), 1e-12)})
    return rows


def measure_pump_heads(liquidrun: Mapping[str, Any], speeds: Sequence[float], *, device: str = "cpu",
                       threads: Optional[int] = 1) -> List[Dict[str, Any]]:
    """One row per (speed, pump): the solved head beside the product and v1 laws at the solved flow.

    Each speed sets every pump's ``rpm_ratio`` in a copy of ``liquidrun`` and
    solves its steady state (``agent.scripted_api.solve_liquid_steady``, no gradients).
    """
    import agent.scripted_api as api  # the platform, through its public API only

    pumps = _pumps(liquidrun)
    rows: List[Dict[str, Any]] = []
    for speed in speeds:
        speed = _finite("speed", speed, positive=True)
        run = copy.deepcopy(dict(liquidrun))
        for edge in run["edges"]:
            if edge.get("type") == "pump":
                edge.setdefault("params", {})["rpm_ratio"] = speed
        solved = api.solve_liquid_steady(run, gradient_parameters=(), device=device, threads=threads)
        rows += solved_pump_heads(run, solved, {pump["id"]: speed for pump in pumps})
    return rows


def assess(rows: Sequence[Mapping[str, Any]], *, rtol: float = 1e-6) -> Dict[str, Any]:
    """The gate: every solved head equals the product law within ``rtol``."""
    worst = max(rows, key=lambda r: r["relative_error"]) if rows else None
    passed = bool(rows) and all(r["relative_error"] <= rtol for r in rows)
    return {"gate": GATE_VERSION, "law": PRODUCT_PUMP_LAW, "rtol": rtol, "passed": passed,
            "rows": len(rows), "worst": dict(worst) if worst else None}
