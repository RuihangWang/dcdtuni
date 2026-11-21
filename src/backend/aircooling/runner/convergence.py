"""Convergence verdict for a steady air-cooling solve.

**Owner rule (2026-10-20):** a steady run is CONVERGED once the ACU return-air
temperatures are steady -- leg 2 below, with ``plateau_tol`` 0.5 K. Leg 1
(residuals) is still computed and written to ``convergence.json`` as a
diagnostic but no longer gates the verdict. The two-leg rule it replaces
(2026-09-22, 5.6 of the 2026-09-21 brief, ``plateau_tol`` 0.1 K) read:

1. **Residuals decrease smoothly.** Per field (Ux/Uy/Uz, p_rgh, T, k,
   epsilon) the initial residual of the first solve in every iteration is
   read from the solver log. Over the last window (20% of the iterations,
   at least 50) it must sit below a sanity level, be at least half a decade
   below its early level (or already tiny; p_rgh is exempt, it plateaus at a
   level the case sets), not be rising, and not scatter
   more than half a decade (10th to 90th percentile) around its own trend
   line. No field
   may be *bounded* inside that window (``bounding k`` in the log is the
   turbulence field collapsing, the Base604 failure signature), and the
   cumulative continuity error must stay small (the weld-off DAML failure
   pushed it past 7). ``SIMPLE solution converged`` (residualControl
   reached) passes outright.

2. **Return-air temperatures change, then plateau.** Every probe in
   ``postProcessing/probes/<t>/T`` must have moved by at least ``change_min``
   over the run (a sensor that never moved sits in a dead cell or the run
   never started transporting heat) AND its last window must be flat
   (peak-to-peak and end-to-window-start drift both within ``plateau_tol``
   or 1% of the probe's total excursion, whichever is larger).
   Without probes the verdict is INCONCLUSIVE, never CONVERGED: the rule
   requires both legs.

3. **The energy balance closes (owner rule 2026-10-30).** The heat the ACUs
   remove, from the ``heatT_``/``phi_`` enthalpy monitors on every ACU supply
   and return patch, stays within ``simulationConfig.energyBalanceTolerance``
   (1 %) of the declared heat over the last window
   (:func:`energy_balance_verdict`). A run that passes leg 2 with the balance
   open is NOT_CONVERGED: Alibaba-sige read CONVERGED at iteration 204 with
   the ACUs removing 2 % of the servers' heat.

**Run length (owner rule 2026-10-27, window 200 -> 100 on 2026-10-30, with the
energy leg now behind it).** The last window is a fixed
``MIN_WINDOW_SAMPLES`` (100) iterations, whatever the run's length, and there is
no fixed minimum run length beyond it: a steady solve stops as soon as every
return temperature is flat within the stop tolerance over its last 200
iterations, and otherwise runs to the iteration count the user set (the
tolerance is the project's ``simulationConfig.stopToleranceK``, default 0.5 K).
A run shorter than the window is INCONCLUSIVE with a ``too-short: N iterations
< minimum M`` reason (:func:`run_length_shortfall`), and none of its returns is
passed either (``sensors.probes.<name>.passed`` is null, its reasons start with
the same ``too-short`` reason). From 2026-10-22 to 10-27 the window was 20 % of
the run and had to span 200 iterations, so nothing under 1000 iterations was
CONVERGED; before that, the final verdict let the window shrink to 5 samples
and a 20-iteration SingleRack solve read CONVERGED 6.5 K above the temperature
the same mesh settles at. ``window_fraction`` still selects the old
fraction-of-run window when a caller passes it.

``assess_run(run_dir)`` writes ``<run_dir>/convergence.json`` and returns the
same dict; the runner calls it after every solve (non-fatal). Thresholds are
keyword arguments so a case can tighten them; the defaults were calibrated on
the 2026-09-21/22 np8 runs: Tri_angle (residualControl) PASS, DAML weld-on
PASS, DAML weld-off FAIL (cumulative continuity 7.4, k collapsed), Base604 on
the old dcCart mesh NOT_CONVERGED (k bounded 132x, residuals rising), and the
two Base604 controls that must PASS -- the snappyHexMesh mesh (Ux 5.8e-4,
p_rgh 3.2e-2, all seven return-air probes flat) and the fixed dcCart mesh
(Ux 2.8e-4, p_rgh 2.2e-2, probes flat).

**Transient runs (2026-09-30, transient keyframes T5c).** A PIMPLE run is not
held to that rule: its residuals restart every time step and its sensors are
meant to move. It is TRANSIENT_COMPLETED when the solver reached ``endTime`` and
printed ``End`` without a FOAM fatal error or a non-finite residual, else
TRANSIENT_INCOMPLETE. The time steps, the maximum Courant number against
``maxCo``, and each step's last-outer-corrector initial residuals (median / max
over the last window) are reported as diagnostics, with no threshold.

Only the job log's LAST solver stage is read: every OpenFOAM application prints
an ``Exec :`` banner, a solver (``...Foam``, ``...Foam_new``) starts a fresh
stage and a utility after it (reconstructPar, writeExplicitBC) is skipped. A
steady-converged transient start runs SIMPLE then PIMPLE in one job; before
this, its verdict mixed the two (1045 "iterations" on the T4 gate run).
"""
from __future__ import annotations

import json
import hashlib
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# loguru prefix the runner puts in front of every solver line it relays
_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| \w+\s*\| ")
_TIME = re.compile(r"^Time = (\S+)")
_RESID = re.compile(
    r"Solving for (\w+), Initial residual = ([0-9.eE+-]+), Final residual = ([0-9.eE+-]+), No Iterations (\d+)"
)
_CONT = re.compile(
    r"time step continuity errors : sum local = (\S+), global = (\S+), cumulative = (\S+)"
)
_BOUND = re.compile(r"^bounding (\w+), min: (\S+) max: (\S+) average: (\S+)")
_CONVERGED = re.compile(r"solution converged in (\d+) iterations")
_EXEC = re.compile(r"^Exec\s*:\s*(\S+)")
_SOLVER_APP = re.compile(r"Foam(_\w+)?$")          # buoyantBoussinesqPimpleFoam, ..._new; not foamToVTK
_TRANSIENT_APP = re.compile(r"(?i)pimple|piso")
_OUTER = re.compile(r"^PIMPLE: [Ii]teration (\d+)")
_COURANT = re.compile(r"^Courant Number mean: (\S+) max: (\S+)")
_NONFINITE = re.compile(r"(?i)residual = (nan|[-+]?inf)")
# PIMPLE with per-step residualControl (E8c): each step ends with one of these.
_OUTER_CONVERGED = re.compile(r"^PIMPLE: converged in (\d+) iterations")
_OUTER_LIMIT = re.compile(r"^PIMPLE: not converged within (\d+) iterations")

# sanity levels: a field whose last-window median sits above this has not
# settled whatever its trend (p_rgh is normalised differently and runs an
# order higher than U/T in every healthy log)
DEFAULT_LEVEL_MAX: Dict[str, float] = {"p_rgh": 5e-2, "default": 2e-3}
# p_rgh in SIMPLE plateaus at a level set by the case (Base604 on the snappy
# control mesh sits at 3.2e-2 after 1000 iterations with every sensor flat),
# so it is held to the level / trend / scatter rules but not to the decay rule
FIELDS_EXEMPT_FROM_DECAY = ("p_rgh",)

#: Default window: a fixed number of iterations, not a fraction of the run
#: (owner rule 2026-10-27; it was 0.2 from 10-22). A caller may still pass a
#: ``window_fraction`` to get the old fraction-of-run window.
WINDOW_FRACTION: Optional[float] = None
#: The return-air verdict's last window, in solver iterations (an ACU return
#: monitor writes one sample per iteration). The steady-stop watcher
#: (``STLGenRunner._watch_steady_stop``) stops a solve on the same window: at 50,
#: a patched DAML run stopped at iteration 515 while its returns still fell
#: 0.3-0.5 K per 100 iterations; 100 stands since the energy leg closes that
#: gap (owner 2026-10-30, it was 200). Both read it at call time, through
#: :func:`run_length_shortfall`, so they cannot disagree.
MIN_WINDOW_SAMPLES = 100
#: Default stop tolerance (K) on each return temperature over the window; a
#: project overrides it with ``simulationConfig.stopToleranceK``.
DEFAULT_STOP_TOLERANCE_K = 0.5


def minimum_run_iterations(*, window_fraction: Optional[float] = None,
                           min_window: Optional[int] = None) -> int:
    """The fewest iterations the verdict can judge: the window itself, or with a
    ``window_fraction`` the fewest whose last ``window_fraction`` spans ``min_window``."""
    fraction = WINDOW_FRACTION if window_fraction is None else window_fraction
    needed = MIN_WINDOW_SAMPLES if min_window is None else min_window
    if not fraction:
        return needed
    minimum = max(0, math.ceil(needed / fraction) - 1)
    while minimum * fraction < needed:
        minimum += 1
    return minimum


def run_length_shortfall(iterations: int, *, window_fraction: Optional[float] = None,
                         min_window: Optional[int] = None) -> Optional[str]:
    """None when a run of ``iterations`` solver iterations is long enough to judge, else the reason.

    The final verdict (:func:`temperature_series_verdict`) and the steady-stop
    watcher both ask this, so a solve is never called steady on a window the
    watcher would not have stopped on.
    """
    fraction = WINDOW_FRACTION if window_fraction is None else window_fraction
    needed = MIN_WINDOW_SAMPLES if min_window is None else min_window
    minimum = minimum_run_iterations(window_fraction=fraction, min_window=needed)
    if iterations >= minimum:
        return None
    if not fraction:
        return (f"too-short: {iterations} iterations < minimum {minimum} (the verdict's last window "
                f"spans {needed} iterations)")
    return (f"too-short: {iterations} iterations < minimum {minimum} (the verdict's last window, "
            f"{fraction * 100:g} % of the run, must span {needed} iterations)")


def steady_verdict(sensors: Dict[str, Any]) -> str:
    """The steady verdict the return-air leg gives (owner rule 2026-10-20; residuals are diagnostics)."""
    if sensors.get("passed"):
        return "CONVERGED"
    if sensors.get("status") in ("NO_SENSORS", "TOO_SHORT"):
        return "INCONCLUSIVE"
    return "NOT_CONVERGED"


@dataclass
class SolverLog:
    iterations: List[float] = field(default_factory=list)
    residuals: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)  # field -> [(iter, initial)]
    continuity: List[Tuple[float, float, float, float]] = field(default_factory=list)  # (iter, local, global, cumulative)
    bounding: Dict[str, List[float]] = field(default_factory=dict)  # field -> [iter]
    bounding_min: Dict[str, float] = field(default_factory=dict)  # field -> lowest pre-bound min
    converged_at: Optional[int] = None
    ended: bool = False
    application: Optional[str] = None  # the stage's solver, from its Exec banner
    last_outer: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)  # field -> [(time, initial)] of each step's last outer corrector
    courant_max: Optional[float] = None
    nonfinite: List[str] = field(default_factory=list)
    fatal: bool = False
    outer_converged: List[int] = field(default_factory=list)  # per step: outer iterations to the in-step tolerance
    outer_hit_limit: List[int] = field(default_factory=list)  # per step that reached nOuterCorrectors unconverged


def parse_solver_log(path: Path) -> SolverLog:
    """Read a runner-relayed (or raw) OpenFOAM solver log: its LAST solver stage.

    A solver's ``Exec :`` banner starts a fresh stage; a utility's banner after it
    stops reading until the next solver. A log with no banner is read whole.
    """
    log = SolverLog()
    collecting = True
    current: Optional[float] = None
    seen_this_step: set = set()
    seen_this_outer: set = set()
    outer_res: Dict[str, float] = {}

    def close_step() -> None:
        # the values the step's last outer corrector started from
        if current is not None:
            for name, value in outer_res.items():
                log.last_outer.setdefault(name, []).append((current, value))
        outer_res.clear()

    with Path(path).open(encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            line = _PREFIX.sub("", raw.rstrip("\n")).strip()
            if not line:
                continue
            m = _EXEC.match(line)
            if m:
                app = Path(m.group(1)).name
                if collecting:
                    close_step()
                if _SOLVER_APP.search(app):
                    log = SolverLog(application=app)
                    collecting, current = True, None
                    seen_this_step, seen_this_outer = set(), set()
                else:
                    collecting = False
                continue
            if not collecting:
                continue
            if "FOAM FATAL" in line:
                log.fatal = True
                continue
            m = _COURANT.match(line)
            if m:
                try:
                    co = float(m.group(2))
                    log.courant_max = co if log.courant_max is None else max(log.courant_max, co)
                except ValueError:
                    log.nonfinite.append(line[:160])
                continue
            m = _TIME.match(line)
            if m:
                try:
                    t = float(m.group(1))
                except ValueError:
                    continue
                close_step()
                current = t
                log.iterations.append(current)
                seen_this_step, seen_this_outer = set(), set()
                continue
            if current is None:
                continue
            if _OUTER.match(line):
                seen_this_outer = set()
                continue
            m = _OUTER_CONVERGED.match(line)
            if m:
                log.outer_converged.append(int(m.group(1)))
                continue
            m = _OUTER_LIMIT.match(line)
            if m:
                log.outer_hit_limit.append(int(m.group(1)))
                continue
            if _NONFINITE.search(line):
                log.nonfinite.append(line[:160])
                continue
            m = _RESID.search(line)
            if m:
                name = m.group(1)
                try:
                    initial = float(m.group(2))
                except ValueError:
                    continue
                if name not in seen_this_outer:
                    seen_this_outer.add(name)
                    outer_res[name] = initial
                if name in seen_this_step:
                    continue  # p_rgh's second corrector, non-orthogonal loops
                seen_this_step.add(name)
                log.residuals.setdefault(name, []).append((current, initial))
                continue
            m = _CONT.search(line)
            if m:
                try:
                    log.continuity.append(
                        (current, float(m.group(1)), float(m.group(2)), float(m.group(3)))
                    )
                except ValueError:
                    pass
                continue
            m = _BOUND.match(line)
            if m:
                log.bounding.setdefault(m.group(1), []).append(current)
                try:
                    low = float(m.group(2))
                except ValueError:
                    low = float("nan")
                prev = log.bounding_min.get(m.group(1))
                log.bounding_min[m.group(1)] = low if prev is None or not (low >= prev) else prev
                continue
            m = _CONVERGED.search(line)
            if m:
                log.converged_at = int(m.group(1))
                continue
            if line == "End":
                log.ended = True
    if collecting:
        close_step()
    return log


def _median(xs: List[float]) -> float:
    return statistics.median(xs) if xs else float("nan")


def _percentile(xs: List[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _slope_decades_per_100(points: List[Tuple[float, float]]) -> float:
    """Least-squares slope of log10(residual) vs iteration, in decades / 100 iterations."""
    pts = [(x, math.log10(y)) for x, y in points if y > 0]
    n = len(pts)
    if n < 3:
        return 0.0
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx <= 0:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    return 100.0 * sxy / sxx


def residual_verdict(
    log: SolverLog,
    *,
    window_fraction: float = 0.2,
    min_window: int = 50,
    level_max: Optional[Dict[str, float]] = None,
    decay_min_decades: float = 0.5,
    slope_max: float = 0.1,
    spread_max: float = 0.5,
    cumulative_max: float = 0.1,
    collapse_ratio: float = 1e-3,
    scatter_floor: float = 1e-5,
    sealed_bounding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Leg 1: residuals decrease smoothly. Returns a dict with ``passed``.

    ``sealed_bounding`` (from :func:`sealed_region_bounding`) is the evidence
    that every bounded cell is quiescent air in a region walled in on every
    side; its bounding is then reported, not failed.
    """
    levels = dict(DEFAULT_LEVEL_MAX)
    if level_max:
        levels.update(level_max)
    n_iter = len(log.iterations)
    out: Dict[str, Any] = {
        "passed": False,
        "iterations": n_iter,
        "converged_at": log.converged_at,
        "fields": {},
        "bounded_in_window": {},
        "cumulative_continuity": log.continuity[-1][3] if log.continuity else None,
        "reasons": [],
    }
    if n_iter == 0 or not log.residuals:
        out["reasons"].append("no residual lines in the log")
        return out
    window = max(min_window, int(n_iter * window_fraction))
    window = min(window, n_iter)
    window_start_iter = log.iterations[-window]
    out["window"] = {"iterations": window, "from": window_start_iter, "to": log.iterations[-1]}

    all_ok = True
    for name, series in sorted(log.residuals.items()):
        if len(series) < 3:
            continue
        last = [p for p in series if p[0] >= window_start_iter]
        # the early level: the first 10% of the run (at least 20 samples), so
        # a run that keeps sinking for 1500 iterations is measured against
        # where it started, not against a window as wide as the last one
        first = series[: max(min(20, len(series)), len(series) // 10)]
        vals_last = [v for _, v in last]
        vals_first = [v for _, v in first]
        med_last = _median(vals_last)
        med_first = _median(vals_first)
        limit = levels.get(name, levels["default"])
        slope = _slope_decades_per_100(last)
        # scatter AROUND the trend: a residual still sinking a decade inside
        # the window is smooth, not noisy (DAML with the first-layer floor
        # falls 5e-5 -> 3e-6 in its last 400 iterations), so the p10-p90
        # spread is taken on the detrended log residual
        pts = [(x, math.log10(v)) for x, v in last if v > 0]
        if len(pts) >= 3:
            mx = sum(x for x, _ in pts) / len(pts)
            my = sum(y for _, y in pts) / len(pts)
            detrended = [y - (my + slope / 100.0 * (x - mx)) for x, y in pts]
            spread = _percentile(detrended, 0.9) - _percentile(detrended, 0.1)
        else:
            spread = float("nan")
        level_ok = med_last <= limit
        decayed = (
            name in FIELDS_EXEMPT_FROM_DECAY
            or med_last <= med_first / (10 ** decay_min_decades)
            or med_last <= 0.1 * limit
        )
        not_rising = slope <= slope_max
        # a residual already below `scatter_floor` is converged whatever it
        # does from step to step (DAML's epsilon at 1e-6 wanders 0.6 decades)
        smooth = spread <= spread_max or med_last <= scatter_floor
        reasons = []
        if not level_ok:
            reasons.append(f"last-window median {med_last:.3g} above {limit:g}")
        if not decayed:
            reasons.append(f"decayed only {med_first / med_last if med_last > 0 else float('inf'):.2g}x from its early level")
        if not not_rising:
            reasons.append(f"rising at {slope:+.2f} decades / 100 iterations")
        if not smooth:
            reasons.append(f"scatters {spread:.2f} decades (p10-p90 about its trend) in the last window")
        ok = level_ok and decayed and not_rising and smooth
        out["fields"][name] = {
            "passed": ok,
            "samples": len(series),
            "first_window_median": med_first,
            "last_window_median": med_last,
            "final": series[-1][1],
            "slope_decades_per_100": slope,
            "spread_decades": spread,
            "level_max": limit,
            "reasons": reasons,
        }
        if not ok:
            all_ok = False
            out["reasons"].append(f"{name}: " + "; ".join(reasons))

    # collapsed turbulence: k / epsilon whose residual sits decades below the
    # momentum residual is a field that stopped moving (DAML weld-off: k at
    # 2e-7 against Ux at 4e-3 by iteration 20), not one that converged
    u_ref = [out["fields"][n]["last_window_median"] for n in ("Ux", "Uy", "Uz") if n in out["fields"]]
    if u_ref:
        u_med = _median(u_ref)
        for name in ("k", "epsilon"):
            st = out["fields"].get(name)
            if st and u_med > 0 and st["last_window_median"] < collapse_ratio * u_med:
                st["passed"] = False
                st["reasons"].append(
                    f"{st['last_window_median']:.2g} is {st['last_window_median'] / u_med:.1e} of the momentum residual: collapsed field"
                )
                all_ok = False
                out["reasons"].append(f"{name}: collapsed ({st['last_window_median']:.2g} vs U {u_med:.2g})")

    for name, its in sorted(log.bounding.items()):
        n_in = sum(1 for it in its if it >= window_start_iter)
        if n_in:
            out["bounded_in_window"][name] = n_in
            if sealed_bounding and name in sealed_bounding.get("fields", ()):
                out.setdefault("sealed_region_bounding", sealed_bounding)
                continue
            all_ok = False
            out["reasons"].append(f"{name} bounded in {n_in} of the last {window} iterations")

    if log.continuity:
        cum = abs(log.continuity[-1][3])
        if not (cum <= cumulative_max):
            all_ok = False
            out["reasons"].append(f"cumulative continuity error {cum:.3g} above {cumulative_max:g}")

    if log.converged_at is not None:
        out["passed"] = True
        out["reasons"] = [f"residualControl reached at iteration {log.converged_at}"] + (
            [f"(ignored: {r})" for r in out["reasons"]] if out["reasons"] else []
        )
        return out
    out["passed"] = all_ok
    return out


def find_probe_file(run_dir: Path, name: str = "T") -> Optional[Path]:
    """The ``postProcessing/probes/<t>/<name>`` with the most rows (a restart writes a second dir)."""
    root = Path(run_dir) / "postProcessing" / "probes"
    if not root.is_dir():
        return None
    best: Optional[Tuple[int, Path]] = None
    for d in sorted(root.iterdir()):
        f = d / name
        if not f.is_file():
            continue
        rows = sum(1 for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip() and not ln.lstrip().startswith("#"))
        if best is None or rows > best[0]:
            best = (rows, f)
    return best[1] if best else None


def sensor_verdict(
    probe_file: Optional[Path],
    *,
    names: Optional[List[str]] = None,
    window_fraction: Optional[float] = None,
    min_window: Optional[int] = None,
    iterations: Optional[int] = None,
    change_min: float = 0.05,
    plateau_tol: float = DEFAULT_STOP_TOLERANCE_K,
    plateau_fraction: float = 0.01,
) -> Dict[str, Any]:
    """Leg 2: every probe temperature changed, then plateaued. ``passed`` / ``status``.

    ``iterations``: the solver iterations the probe file covers (a steady
    ``probes`` object writes every 10th); see :func:`temperature_series_verdict`.
    """
    from backend.aircooling.runner.sensor_harvest import parse_probes_file

    out: Dict[str, Any] = {"passed": False, "status": "NO_SENSORS", "probes": {}, "reasons": []}
    if probe_file is None or not Path(probe_file).is_file():
        out["reasons"].append("no postProcessing/probes/<t>/T (the project carries no enabled sensor)")
        return out
    steps, rows = parse_probes_file(Path(probe_file), is_vector=False)
    if not rows:
        out["reasons"].append(f"{probe_file} holds no samples")
        return out
    return temperature_series_verdict(
        rows, names=names, window_fraction=window_fraction, min_window=min_window, iterations=iterations,
        change_min=change_min, plateau_tol=plateau_tol, plateau_fraction=plateau_fraction,
    )


def temperature_series_verdict(
    rows: List[List[float]],
    *,
    names: Optional[List[str]] = None,
    window_fraction: Optional[float] = None,
    min_window: Optional[int] = None,
    iterations: Optional[int] = None,
    change_min: float = 0.05,
    plateau_tol: float = DEFAULT_STOP_TOLERANCE_K,
    plateau_fraction: float = 0.01,
) -> Dict[str, Any]:
    """The same changed-then-plateau rule for explicit temperature rows.

    The last window spans :data:`MIN_WINDOW_SAMPLES` iterations (or, with a
    ``window_fraction``, that fraction of the rows if it is longer). ``iterations`` is the number of solver iterations the rows cover
    (default: one row per iteration, as an ACU return monitor writes); below
    :func:`minimum_run_iterations` the result is ``status`` ``TOO_SHORT``, never
    passed, with the ``too-short`` reason first and every probe's read-out kept
    as a diagnostic: each probe's ``passed`` is then ``None`` (not judged) and
    its ``reasons`` start with the same ``too-short`` reason.
    """
    fraction = WINDOW_FRACTION if window_fraction is None else window_fraction
    needed = MIN_WINDOW_SAMPLES if min_window is None else min_window
    out: Dict[str, Any] = {"passed": False, "status": "NO_SENSORS", "probes": {}, "reasons": []}
    if not rows:
        out["reasons"].append("temperature evidence holds no samples")
        return out
    n_probes = max(len(r) for r in rows)
    rows = [r for r in rows if len(r) == n_probes]
    n = len(rows)
    covered = n if iterations is None else int(iterations)
    shortfall = run_length_shortfall(covered, window_fraction=fraction, min_window=needed)
    out["iterations"] = covered
    out["minimumIterations"] = minimum_run_iterations(window_fraction=fraction, min_window=needed)
    if n < 3:
        out["status"] = "TOO_SHORT"
        out["reasons"].extend(([shortfall] if shortfall else []) + [f"only {n} probe samples"])
        return out
    # the window spans `needed` iterations at least: one row per iteration for
    # a return monitor, every 10th iteration for a steady point probe
    window = min(n, max(int(n * (fraction or 0)), math.ceil(needed * n / max(covered, 1))))
    out["status"] = "OK"
    out["samples"] = n
    out["window_samples"] = window
    all_ok = True
    for i in range(n_probes):
        series = [r[i] for r in rows]
        label = names[i] if names and i < len(names) else f"probe_{i}"
        total_range = max(series) - min(series)
        last = series[-window:]
        pp = max(last) - min(last)
        drift = abs(last[-1] - last[0])
        changed = total_range >= change_min
        # flat relative to what the probe went through: 1% of its total
        # excursion, never tighter than plateau_tol (Base604's return air
        # climbs 21 K and still creeps 0.11 K over the last 200 iterations)
        tol = max(plateau_tol, plateau_fraction * total_range)
        flat = pp <= tol and drift <= tol
        reasons = []
        if not changed:
            reasons.append(f"moved only {total_range:.3g} over the run (< {change_min:g})")
        if not flat:
            reasons.append(f"last window spans {pp:.3g} / drifts {drift:.3g} (> {tol:.3g})")
        ok = changed and flat
        out["probes"][label] = {
            "passed": ok,
            "first": series[0],
            "last": series[-1],
            "range": total_range,
            "window_peak_to_peak": pp,
            "window_drift": drift,
            "reasons": reasons,
        }
        if not ok:
            all_ok = False
            out["reasons"].append(f"{label}: " + "; ".join(reasons))
    if shortfall:
        out["status"] = "TOO_SHORT"
        out["reasons"].insert(0, shortfall)
        # A run too short to judge judges no single return either: its window
        # read-out stays as a diagnostic, its pass/fail is null and says why
        # (the 20-iteration SingleRack run read its return "passed" on 5 samples).
        for probe in out["probes"].values():
            probe["passed"] = None
            probe["reasons"].insert(0, shortfall)
        return out
    out["passed"] = all_ok
    return out


# The executed manifest carries every model of the room: Base604's is 12 MB
# (142 racks), so it gets its own bound instead of the 8 MiB one for logs,
# receipts and dictionaries.
_MANIFEST_READ_BOUND = 256 * 1024 * 1024


def _monitor_bytes(path: Path, run_dir: Path, maximum_bytes: int = 8 * 1024 * 1024) -> bytes:
    """Read one exact, stable owned evidence file; never choose by a glob."""
    path = Path(path)
    owned = Path(run_dir).resolve(strict=True)
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(owned) or not path.is_file():
        raise ValueError(f"monitor evidence is not an owned regular file: {path}")
    before = path.stat()
    if before.st_size > maximum_bytes:
        raise ValueError(f"monitor evidence exceeds its read bound: {path}")
    data = path.read_bytes()
    after = path.stat()
    identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if identity(before) != identity(after):
        raise ValueError(f"monitor evidence changed while reading: {path}")
    return data


def _growing_monitor_bytes(path: Path, run_dir: Path, attempts: int = 5) -> bytes:
    """A monitor file the running solver is still appending to: its complete lines.

    The steady-stop watcher reads while OpenFOAM writes, so a read can see the
    file grow (``_monitor_bytes`` refuses that) or end inside a half-written
    row (which would parse as a truncated temperature). Retry a few times for a
    stable read; failing that, take what was read up to its last newline -- an
    appended file's complete lines are a valid prefix of its final content.
    """
    import time as _time
    for attempt in range(attempts):
        try:
            data = _monitor_bytes(path, run_dir)
            break
        except ValueError as exc:
            if "changed while reading" not in str(exc):
                raise
            if attempt == attempts - 1:
                data = Path(path).read_bytes()
                break
            _time.sleep(0.05)
    end = data.rfind(b"\n")
    return data[:end + 1] if end >= 0 else b""


def _foam_monitor_block(text: str, name: str) -> str:
    """Extract one literal generated dictionary block, excluding comments."""
    text = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", text)
    matches = list(re.finditer(r"(?m)^\s*" + re.escape(name) + r"\s*\{", text))
    if len(matches) != 1:
        raise ValueError(f"expected one generated monitor block {name}")
    begin = matches[0].end() - 1
    depth = 1
    for position in range(begin + 1, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[begin + 1:position]
    raise ValueError(f"unclosed generated monitor block {name}")


def _require_return_monitor(control: str, function: str, patch: str) -> None:
    functions = _foam_monitor_block(control, "functions")
    body = _foam_monitor_block(functions, function)
    expected = {
        "type": "surfaceFieldValue", "enabled": "true", "regionType": "patch",
        "name": patch, "operation": "areaAverage", "fields": "(T)",
        "writeControl": "timeStep", "writeInterval": "1",
    }
    for key, value in expected.items():
        matches = re.findall(r"(?m)^\s*" + key + r"\s+([^;{}\n]+);", body)
        if len(matches) != 1 or re.sub(r"\s+", "", matches[0]) != value:
            raise ValueError(f"monitor {function} differs at {key}")


def _return_patch_file(run_dir: Path, function: str) -> Path:
    root = Path(run_dir) / "postProcessing" / function
    if not root.is_dir() or root.is_symlink() or not root.resolve().is_relative_to(Path(run_dir).resolve()):
        raise ValueError(f"missing owned function-object output {function}")
    selected: List[Path] = []
    for segment in root.iterdir():
        if not segment.is_dir() or segment.is_symlink():
            continue
        try:
            start = float(segment.name)
        except ValueError:
            continue
        if not math.isfinite(start) or start < 0:
            continue
        # These are the two names the native writeFile source can create.
        for filename in ("surfaceFieldValue.dat", f"surfaceFieldValue_{segment.name}.dat"):
            path = segment / filename
            if path.is_file():
                selected.append(path)
    if len(selected) != 1:
        raise ValueError(f"{function} has missing/ambiguous native surfaceFieldValue output")
    return selected[0]


def _read_return_patch_series(path: Path, run_dir: Path, patch: str,
                              live: bool = False, column: str = "areaAverage(T)",
                              ) -> Tuple[List[float], List[float], Dict[str, Any]]:
    data = _growing_monitor_bytes(path, run_dir) if live else _monitor_bytes(path, run_dir)
    headers: Dict[str, List[str]] = {}
    times: List[float] = []
    values: List[float] = []
    data_rows: List[str] = []
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            comment = line[1:].strip()
            if comment.startswith("Time"):
                headers.setdefault("columns", []).append(comment)
            else:
                match = re.fullmatch(r"(Region\s+type|Faces|Area|Scale\s+factor)\s*:\s*(.+)", comment)
                if match:
                    key = re.sub(r"\s+", " ", match.group(1))
                    headers.setdefault(key, []).append(match.group(2).strip())
            continue
        data_rows.append(line)
    if headers.get("Region type") != [f"patch {patch}"]:
        raise ValueError(f"{patch} native output lacks its exact patch identity")
    if len(headers.get("columns", [])) != 1 or headers["columns"][0].split() != ["Time", column]:
        raise ValueError(f"{patch} native output does not select exactly {column}")
    if len(headers.get("Faces", [])) != 1 or not re.fullmatch(r"[0-9]+", headers["Faces"][0]) or int(headers["Faces"][0]) <= 0:
        raise ValueError(f"{patch} native output has no positive face count")
    if len(headers.get("Area", [])) != 1:
        raise ValueError(f"{patch} native output has no unique area")
    area = float(headers["Area"][0])
    if len(headers.get("Scale factor", [])) != 1:
        raise ValueError(f"{patch} native output has no unique scale")
    scale = float(headers["Scale factor"][0])
    if not math.isfinite(area) or area <= 0 or not math.isfinite(scale) or scale != 1:
        raise ValueError(f"{patch} native output has an invalid area/scale")
    for line in data_rows:
            fields = line.split()
            if len(fields) != 2:
                raise ValueError(f"{patch} has a malformed {column} row")
            time, value = map(float, fields)
            if not math.isfinite(time) or not math.isfinite(value):
                raise ArithmeticError(f"{patch} has non-finite native temperature evidence")
            if time < 0 or (times and time <= times[-1]):
                raise ValueError(f"{patch} has an unordered/restarted native time axis")
            times.append(time)
            values.append(value)
    return times, values, {
        "patch": patch, "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
        "faces": int(headers["Faces"][0]), "area_m2": area, "samples": len(times),
    }


def _receipt_manifest(run_dir: Path, receipt_path: Path):
    """The executed manifest named by an accepted execution receipt, checked
    against it: (manifest, payload, sha, archive, owner, receipt_bytes)."""
    receipt_bytes = _monitor_bytes(receipt_path, run_dir)
    receipt = json.loads(receipt_bytes)
    if receipt.get("format") != "air-run-execution-receipt/v1" or receipt.get("status") != "accepted":
        raise ValueError("return monitor has no accepted execution identity")
    identity = receipt["result"]
    relative = Path(identity["cfdrunRelativePath"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("executed manifest identity has an unsafe relative path")
    archive = Path(run_dir) / "model" / relative.name
    owner = Path(run_dir)
    if not archive.is_file():
        if Path(run_dir).parent.name != "log":
            raise ValueError("unarchived monitor manifest lacks its case execution context")
        owner = Path(run_dir).resolve().parent.parent
        archive = owner / relative
    payload = _monitor_bytes(archive, owner, _MANIFEST_READ_BOUND)
    manifest = json.loads(payload)
    sha = hashlib.sha256(payload).hexdigest()
    if (sha != identity["cfdrunSha256"] or manifest.get("runId") != identity["runId"]
            or (manifest.get("sourceProject") or {}).get("contentHash") != identity["projectContentHash"]):
        raise ValueError("return monitor archive does not match executed source identity")
    return manifest, payload, sha, archive, owner, receipt_bytes


def configured_return_patch_verdict(
    run_dir: Path, *, final_iteration: Optional[float],
    expected_iterations: Optional[List[float]] = None,
    live_manifest: Optional[Path] = None, **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Assess every configured true ACU return patch; None retains old probes.

    ``live_manifest``: the manifest the calling runner is executing right now
    (the steady-stop watcher). The execution receipt is published only after
    the solver returns, so without this the watcher never saw the return-air
    monitors -- the owner's convergence rule -- and judged a running solve by
    its point probes alone, or not at all. Live mode reads that manifest
    directly and assesses the common prefix of the monitors' and the log's
    iteration axes; the finished solve is still judged through the receipt.
    """
    receipt_path = Path(run_dir) / "air-run-execution-receipt.json"
    live = live_manifest is not None and not receipt_path.is_file()
    if not receipt_path.is_file() and not live:
        return None
    unknown: Dict[str, Any] = {
        "passed": False, "status": "NO_SENSORS", "probes": {}, "reasons": [],
        "evidenceSource": "configured-acu-return-areaAverage(T)", "metadataUnknown": True,
    }
    try:
        if live:
            archive = Path(live_manifest)
            owner = archive.parent
            payload = archive.read_bytes()
            manifest = json.loads(payload)
            sha = hashlib.sha256(payload).hexdigest()
            receipt_bytes = b""
        else:
            manifest, payload, sha, archive, owner, receipt_bytes = _receipt_manifest(run_dir, receipt_path)
        config = (manifest.get("simulationConfig") if manifest.get("schemaVersion") == 4
                  else (manifest.get("meshGeometry") or {}).get("simulationConfig")) or {}
        monitoring = config.get("monitoring") or {}
        requested = monitoring.get("acuReturnTemperature") or []
        if not requested:
            return None
        if not isinstance(requested, list):
            raise ValueError("configured ACU return monitors must be a list")
        # Match the existing runner's enabled probe-name selection. This is
        # required even when the assessment caller omitted sensor_names.
        geometry = manifest.get("meshGeometry") or {}
        point_rows = geometry.get("sensorPoints", manifest.get("sensorPoints", [])) or []
        if not isinstance(point_rows, list):
            raise ValueError("configured point probes must be a list")
        point_names: List[str] = []
        for index, sensor in enumerate(point_rows):
            if (not isinstance(sensor, dict) or sensor.get("enabled", True) is False
                    or any(axis not in sensor for axis in ("x", "y", "z"))):
                continue
            point_names.append(str(sensor.get("name") or f"sensor_{index + 1}"))
        unknown["configuredPointProbeNames"] = point_names
        all_names: List[str] = []
        for group in ("serverTemperature", "acuReturnTemperature"):
            rows = monitoring.get(group) or []
            if not isinstance(rows, list):
                raise ValueError(f"configured {group} monitors must be a list")
            for row in rows:
                patch = row.get("patch") if isinstance(row, dict) else None
                if not isinstance(patch, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", patch):
                    raise ValueError("monitor patch is not a literal exported patch name")
                all_names.append("avgT_" + patch.replace(" ", "_"))
        if len(set(all_names)) != len(all_names):
            raise ValueError("generated temperature function names are duplicated")
        by_patch: Dict[str, List[Dict[str, Any]]] = {}
        for binding in (manifest.get("boundaryBindings") or {}).values():
            by_patch.setdefault(binding.get("patchName", ""), []).append(binding)
        control_bytes = _monitor_bytes(Path(run_dir) / "system" / "controlDict", run_dir)
        control = control_bytes.decode("utf-8")
        axes: Optional[List[float]] = None
        columns: List[List[float]] = []
        sources: List[Dict[str, Any]] = []
        names: List[str] = []
        for row in requested:
            patch = row["patch"]
            bindings = by_patch.get(patch, [])
            if (len(bindings) != 1 or bindings[0].get("bcType") != "acu_return"
                    or bindings[0].get("role") != "acu_return"
                    or bindings[0].get("solidName") != patch
                    # (a return grille may sit on the ceiling or on a return
                    # box: the binding's role decides, not the collection)
                    or not re.fullmatch(r"constructions\.[A-Za-z]+\.[A-Za-z0-9_-]+", str(bindings[0].get("objectPath", "")))):
                raise ValueError(f"{patch} is not one bound true ACU return boundary")
            function = "avgT_" + patch.replace(" ", "_")
            _require_return_monitor(control, function, patch)
            times, values, source = _read_return_patch_series(
                _return_patch_file(run_dir, function), run_dir, patch, live=live)
            if axes is None:
                axes = times
            elif times != axes:
                # a running solve writes the monitors one after another: one may
                # be a few rows ahead. Judge the iterations all of them reached.
                short, long_ = (times, axes) if len(times) < len(axes) else (axes, times)
                if not live or long_[:len(short)] != short:
                    raise ValueError("configured return patches do not share a complete time axis")
                axes = short
                columns = [c[:len(short)] for c in columns]
                values = values[:len(short)]
            source.update({"functionName": function, "objectPath": bindings[0]["objectPath"],
                           "surfaceUid": bindings[0].get("surfaceUid"), "solidName": patch})
            sources.append(source)
            columns.append(values)
            names.append(patch)
        if live and axes:
            # a running solve: the log and the monitor files are written in
            # turn, so assess the iterations both have reached
            reached = set(expected_iterations or [])
            keep = [k for k, t in enumerate(axes) if t in reached]
            if not keep or keep != list(range(len(keep))):
                raise ValueError("live return monitors do not follow the solver log")
            axes = axes[:len(keep)]
            columns = [c[:len(keep)] for c in columns]
            final_iteration = axes[-1]
            expected_iterations = axes
        if (not axes or final_iteration is None or not math.isfinite(final_iteration)
                or axes[-1] != final_iteration):
            raise ValueError("return monitor evidence does not reach the actual solver final iteration")
        # Native SIMPLE calls Time::loop before printing each body Time. The
        # first Time::run only reads functionObjects; subsequent/final run calls
        # execute/write them at each printed body time. timeStep/1 therefore
        # selects exactly the complete parsed body-time axis, with no guessed
        # zero prefix, lost interior samples or accepted restarted suffix.
        if (not expected_iterations or any(not math.isfinite(t) for t in expected_iterations)
                or any(b <= a for a, b in zip(expected_iterations, expected_iterations[1:]))
                or axes != expected_iterations):
            raise ValueError("return monitor evidence differs from the complete actual solver iteration axis")
        if not live and (_monitor_bytes(receipt_path, run_dir) != receipt_bytes
                or _monitor_bytes(archive, owner, _MANIFEST_READ_BOUND) != payload
                or _monitor_bytes(Path(run_dir) / "system" / "controlDict", run_dir) != control_bytes):
            raise ValueError("return monitor execution/config identity changed during assessment")
        result = temperature_series_verdict(
            [list(row) for row in zip(*columns)], names=names, **kwargs)
        result.update({
            "evidenceSource": "configured-acu-return-areaAverage(T)", "metadataUnknown": False,
            "units": "K", "monitors": sources,
            "time": {"first": axes[0], "last": axes[-1], "completeSolverAxisMatched": True},
            "configuredPointProbeNames": point_names,
            "executedManifest": str(archive), "executedManifestSha256": sha,
            "controlDictSha256": hashlib.sha256(control_bytes).hexdigest(),
            "receiptSha256": hashlib.sha256(receipt_bytes).hexdigest(),
        })
        return result
    except ArithmeticError as exc:
        unknown.update({"status": "INVALID_DATA", "metadataUnknown": False})
        unknown["reasons"].append(str(exc))
        return unknown
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        unknown["reasons"].append(str(exc))
        return unknown


def _temperature_streams_verdict(
    points: Optional[Dict[str, Any]], returns: Dict[str, Any],
) -> Dict[str, Any]:
    """The configured ACU return temperatures decide; point probes are reported.

    Owner rule (2026-10-20): the solve has converged once the return-air
    temperatures are steady. A point probe (a cold-aisle sensor, say) is
    measured and written next to them with its own pass/fail, like the
    residuals, but it does not decide the verdict -- CMI-China's returns
    were all within 0.5 K while two cold-aisle points swung 0.7-0.8 K.
    """
    streams = {
        "pointProbes": ({"configured": True, "diagnostic": True, "verdict": points}
                        if points is not None else {"configured": False, "skipped": True}),
        "acuReturnPatches": {"configured": True, "verdict": returns},
    }
    out = dict(returns)
    out["streams"] = streams
    if points is None:
        return out
    out["evidenceSource"] = returns.get("evidenceSource", "configured-acu-return-areaAverage(T)")
    out["probes"] = {**{f"return:{name}": value for name, value in returns.get("probes", {}).items()},
                     **{f"point:{name}": value for name, value in points.get("probes", {}).items()}}
    out["reasons"] = [f"return: {reason}" for reason in returns.get("reasons", [])]
    out["diagnostics"] = [f"point: {reason}" for reason in points.get("reasons", [])]
    return out


def _control_dict(run_dir: Path) -> Dict[str, str]:
    """The top-level ``key value;`` entries of ``system/controlDict`` (empty when absent)."""
    path = Path(run_dir) / "system" / "controlDict"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {k: v.strip() for k, v in re.findall(r"^(\w+)\s+([^;{}\n]+);", text, re.MULTILINE)}


def run_is_transient(run_dir: Path) -> bool:
    """Did this run directory solve in time (a PIMPLE/PISO ``application``)?"""
    application = _control_dict(run_dir).get("application")
    return bool(application and _TRANSIENT_APP.search(application))


def _as_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def transient_verdict(log: SolverLog, run_dir: Path, *, window_frac: float = 0.2) -> Dict[str, Any]:
    """Did the transient run finish? Everything else is a diagnostic, not a threshold."""
    control = _control_dict(run_dir)
    end_time = _as_float(control.get("endTime"))
    reasons: List[str] = []
    final = log.iterations[-1] if log.iterations else None
    if final is None:
        reasons.append("no time step in the solver log")
    elif end_time is not None and abs(final - end_time) > 1e-6 * max(abs(end_time), 1e-300):
        # timeName uses configurable significant digits. Match its precision,
        # but do not use a one-second floor for a millisecond-scale timeline.
        reasons.append(f"terminal time mismatch: stopped at t = {final:g} of {end_time:g} s")
    if not log.ended:
        reasons.append("the solver did not print End")
    if log.fatal:
        reasons.append("FOAM FATAL ERROR in the log")
    if log.nonfinite:
        reasons.append(f"non-finite residual or Courant number: {log.nonfinite[0]}")
    window = max(1, round(len(log.iterations) * window_frac))
    last_outer = {}
    for name, points in log.last_outer.items():
        values = [v for _, v in points[-window:]]
        last_outer[name] = {"median": _median(values), "max": max(values), "samples": len(points)}
    steps = [b - a for a, b in zip(log.iterations, log.iterations[1:])]
    outer = log.outer_converged + log.outer_hit_limit
    outer_loop = {
        "steps_converged": len(log.outer_converged),
        "steps_hit_limit": len(log.outer_hit_limit),
        "iterations_median": _median([float(n) for n in outer]),
        "iterations_max": max(outer),
    } if outer else None
    return {
        "passed": not reasons,
        "reasons": reasons,
        "application": log.application or control.get("application"),
        "steps": len(log.iterations),
        "final_time": final,
        "end_time": end_time,
        "delta_t": {"min": min(steps), "max": max(steps)} if steps else None,
        "courant_max": log.courant_max,
        "max_co": _as_float(control.get("maxCo")),
        "window_steps": window,
        "last_outer_initial_residual": last_outer,
        "cumulative_continuity": log.continuity[-1][3] if log.continuity else None,
        # E8c: per-step outer-loop convergence (None without residualControl in PIMPLE).
        "outer_loop": outer_loop,
    }


def _latest_log(run_dir: Path) -> Optional[Path]:
    logs = sorted(Path(run_dir).glob("*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


_SEALED_FLOOR = 1e-10   # a turbulence value this low is quiescent air


def _internal_field(path: Path) -> Optional[List[float]]:
    """A volScalarField's internalField as floats (ascii or binary), None when unreadable."""
    import struct
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    at = raw.find(b"internalField")
    if at < 0:
        return None
    rest = raw[at:at + 200]
    m = re.match(rb"internalField\s+uniform\s+([-+0-9.eE]+)", rest)
    if m:
        return None   # uniform: no per-cell evidence
    lst = raw.find(b"List<scalar>", at)
    if lst < 0:
        return None
    pos = lst + len(b"List<scalar>")
    m = re.match(rb"\s*(\d+)\s*\(", raw[pos:pos + 64])
    if not m:
        return None
    count = int(m.group(1))
    start = pos + m.end()
    binary = re.search(rb"format\s+binary", raw[:at]) is not None
    if binary:
        data = raw[start:start + 8 * count]
        if len(data) != 8 * count:
            return None
        return list(struct.unpack(f"<{count}d", data))
    body = raw[start:raw.find(b")", start)].split()
    return [float(x) for x in body[:count]] if len(body) >= count else None


def sealed_region_bounding(run_dir: Path, log: "SolverLog") -> Optional[Dict[str, Any]]:
    """Evidence that every bounded turbulence cell lies in a sealed region.

    The runner records the regions walled in on every side
    (``sealed-regions.json``, dcdtuni.sealed-regions/v1) that the mesh keeps.
    Their air never moves, so k and epsilon decay to the model floor and are
    bounded every iteration -- Shinaide_liquid's raised-floor plenum, 237k
    cells -- while the flow converges. That is not the collapse the bounding
    rule exists for (Base604: k bounded inside the flow), so it is accepted
    only on evidence: a sealed-region record, every bounding event clipping a
    non-negative value, and at the latest time every cell of each bounded
    field below the quiescent floor belonging to a sealed region's cellSet.
    Anything less returns None and the rule applies as before.
    """
    run_dir = Path(run_dir)
    record = run_dir / "sealed-regions.json"
    if not log.bounding or not record.is_file():
        return None
    try:
        info = json.loads(record.read_text(encoding="utf-8"))
        names = [r["name"] for r in info.get("regions", [])]
    except (ValueError, KeyError, TypeError):
        return None
    if info.get("format") != "dcdtuni.sealed-regions/v1" or not names:
        return None
    fields = sorted(log.bounding)
    if any(not (log.bounding_min.get(f, float("nan")) >= 0) for f in fields):
        return None
    roots = sorted(run_dir.glob("processor[0-9]*")) or [run_dir]

    def latest(root: Path) -> Optional[Path]:
        times = []
        for d in root.iterdir():
            try:
                value = float(d.name)
            except ValueError:
                continue
            if d.is_dir() and value > 0:
                times.append((value, d))
        return max(times)[1] if times else None

    from backend.aircooling.runner.polymesh_io import read_label_list
    below_total = 0
    for root in roots:
        time_dir = latest(root)
        if time_dir is None:
            return None
        sealed = set()
        for name in names:
            path = root / "constant" / "polyMesh" / "sets" / name
            if not path.is_file():
                return None
            raw = path.read_bytes()
            head = raw.find(b"FoamFile")
            start = raw.find(b"(", raw.find(b"}", head) if head >= 0 else 0)
            end = raw.rfind(b")")
            body = raw[start + 1:end]
            labels = (body.split() if re.fullmatch(rb"[\s0-9]*", body)
                      else [int(x) for x in read_label_list(path)])
            sealed.update(int(x) for x in labels)
        for f in fields:
            values = _internal_field(time_dir / f)
            if values is None:
                return None
            for cell, value in enumerate(values):
                if value < _SEALED_FLOOR:
                    if cell not in sealed:
                        return None
                    below_total += 1
    return {"regions": names, "fields": fields, "quiescent_cells": below_total,
            "rule": "every bounded cell is quiescent air in a region walled in on every side"}


#: Default tolerance of the energy leg, a fraction of the declared heat (or of
#: the mean ACU heat when the declared heat is incomplete); a project overrides
#: it with ``simulationConfig.energyBalanceTolerance``. 1 %: the global energy
#: balance a converged CFD solution closes to (Ansys Fluent User's Guide,
#: "Judging Convergence": net imbalance under 1 % of the smallest boundary
#: flux; Eolios data-centre CFD white paper ch. 11: mass and energy conserved
#: over the domain within 1 %, and a balance that does not close invalidates
#: the computation even with fine residuals).
DEFAULT_ENERGY_TOLERANCE = 0.01
#: Boundary kinds that carry no heat across the room boundary in the energy
#: leg: adiabatic walls and baffles, porous zones the air passes through, and
#: the server fans (their inlet/outlet pair adds the server's declared power,
#: counted from ``powerW``).
_HEAT_NEUTRAL_KINDS = frozenset({"wall", "wall_baffle", "perforated_baffle", "through_zone",
                                 "fan_inlet", "acu_supply", "acu_return"})


def _executed_manifest(run_dir: Path, live_manifest: Optional[Path]) -> Optional[Dict[str, Any]]:
    """The manifest a solve ran (its execution receipt) or is running (``live_manifest``)."""
    receipt_path = Path(run_dir) / "air-run-execution-receipt.json"
    if receipt_path.is_file():
        return _receipt_manifest(run_dir, receipt_path)[0]
    if live_manifest is not None:
        return json.loads(Path(live_manifest).read_bytes())
    return None


def declared_air_heat(manifest: Dict[str, Any]) -> Tuple[float, List[str]]:
    """(heat the manifest puts into the air, W; what it cannot count).

    Server fans add their ``powerW`` (in a hybrid run, the air share the liquid
    result handed over); heat-flux surfaces add flux times their exported
    area. A fixed-temperature surface, a volumetric source or a boundary kind
    this does not know exchanges heat the solution decides, so the declared
    total is then incomplete and the leg falls back to steadiness.
    """
    solids = {}
    for solid in ((manifest.get("geometryExport") or {}).get("solids") or {}).values():
        if isinstance(solid, dict) and solid.get("patchName"):
            solids[solid["patchName"]] = solid
    heat, uncounted = 0.0, []
    for binding in (manifest.get("boundaryBindings") or {}).values():
        if not isinstance(binding, dict):
            continue
        kind, parameters = binding.get("bcType"), binding.get("parameters") or {}
        patch = binding.get("patchName")
        if kind == "fan_outlet":
            heat += float(parameters.get("powerW") or 0.0)
        elif kind in ("nonfan_heat_flux", "heat_source_flux"):
            area = (solids.get(patch) or {}).get("areaM2")
            flux = parameters.get("heatFluxWm2", parameters.get("heat_flux"))
            if isinstance(area, (int, float)) and area > 0 and isinstance(flux, (int, float)):
                heat += float(flux) * float(area)
            else:
                uncounted.append(f"{patch} ({kind} without an exported area)")
        elif kind not in _HEAT_NEUTRAL_KINDS:
            uncounted.append(f"{patch} ({kind})")
    if manifest.get("sourceTerms"):
        uncounted.append(f"{len(manifest['sourceTerms'])} volumetric source term(s)")
    return heat, uncounted


def energy_balance_verdict(
    run_dir: Path, *, expected_iterations: Optional[List[float]] = None,
    live_manifest: Optional[Path] = None, min_window: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """The energy leg: the heat the ACUs remove closes on the heat put in.

    ACU heat per iteration is rho*cp*sum(phi*T - T_ref*phi) over every ACU
    supply and return patch, from the ``heatT_``/``phi_`` monitors
    (openfoam_case.generate_control_dict), with T_ref the flux-weighted supply
    temperature -- exactly what leaves through the returns, without the
    area-average error (on Alibaba-sige the area- and flux-weighted return
    temperatures differed by a tenth of the temperature rise). It passes when
    every iteration of the last window is within the tolerance of the
    declared heat. Without a complete declared heat (declared_air_heat) it
    passes when the ACU heat itself varies within the tolerance of its mean
    over the window, and says why it could not close the balance.
    None for a run without the monitors (solved before 2026-10-30).
    """
    from backend.aircooling.runner.openfoam_case import HEAT_FLOW_PREFIX, VOLUME_FLOW_PREFIX
    from backend.aircooling.utils.const import air_specific_heat, rho_air
    control = Path(run_dir) / "system" / "controlDict"
    if not control.is_file() or HEAT_FLOW_PREFIX not in control.read_text(encoding="utf-8", errors="replace"):
        return None
    out: Dict[str, Any] = {"passed": False, "status": "NO_EVIDENCE", "reasons": []}
    try:
        manifest = _executed_manifest(run_dir, live_manifest)
        if manifest is None:
            raise ValueError("no executed manifest for the energy balance")
        config = (manifest.get("simulationConfig") if manifest.get("schemaVersion") == 4
                  else (manifest.get("meshGeometry") or {}).get("simulationConfig")) or {}
        tol = config.get("energyBalanceTolerance", DEFAULT_ENERGY_TOLERANCE)
        if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not 0 < tol < 1:
            raise ValueError(f"simulationConfig.energyBalanceTolerance must be a fraction in (0, 1), got {tol!r}")
        supply, ret = [], []
        for binding in (manifest.get("boundaryBindings") or {}).values():
            if isinstance(binding, dict) and binding.get("bcType") in ("acu_supply", "acu_return"):
                (supply if binding["bcType"] == "acu_supply" else ret).append(binding["patchName"])
        if not supply or not ret:
            raise ValueError("no ACU supply and return patches to close the energy balance on")
        live = live_manifest is not None and not (Path(run_dir) / "air-run-execution-receipt.json").is_file()
        series: Dict[str, Tuple[List[float], List[float], List[float]]] = {}
        axis: Optional[List[float]] = None
        for patch in sorted(set(supply) | set(ret)):
            safe = patch.replace(" ", "_")
            t1, w, _ = _read_return_patch_series(_return_patch_file(run_dir, HEAT_FLOW_PREFIX + safe),
                                                 run_dir, patch, live=live, column="weightedSum(T)")
            t2, f, _ = _read_return_patch_series(_return_patch_file(run_dir, VOLUME_FLOW_PREFIX + safe),
                                                 run_dir, patch, live=live, column="sum(phi)")
            n = min(len(t1), len(t2))
            if t1[:n] != t2[:n]:
                raise ValueError(f"{patch} enthalpy and flow monitors do not share a time axis")
            series[patch] = (t1[:n], w[:n], f[:n])
            axis = t1[:n] if axis is None or n < len(axis) else axis
        if live and expected_iterations is not None:
            reached = set(expected_iterations)
            axis = [t for t in axis if t in reached]
        n = len(axis or [])
        needed = MIN_WINDOW_SAMPLES if min_window is None else min_window
        rho_cp = rho_air * air_specific_heat
        removed: List[float] = []
        for k in range(n):
            ws = sum(series[p][1][k] for p in supply)
            fs = sum(series[p][2][k] for p in supply)
            t_ref = ws / fs if abs(fs) > 1e-12 else 0.0
            removed.append(rho_cp * sum(series[p][1][k] - t_ref * series[p][2][k] for p in series))
        declared, uncounted = declared_air_heat(manifest)
        out.update({"toleranceFraction": float(tol), "declaredHeatW": declared, "notCounted": uncounted,
                    "samples": n, "windowSamples": needed,
                    "acuHeatRemovedW": removed[-1] if removed else None,
                    "rule": "the heat the ACUs remove (flux-weighted, every iteration) stays within the "
                            "tolerance of the declared heat over the last window"})
        if n < needed:
            out.update({"status": "TOO_SHORT", "reasons": [f"too-short: {n} iterations < minimum {needed}"]})
            return out
        window = removed[-needed:]
        if not math.isfinite(sum(window)):
            raise ArithmeticError("non-finite ACU heat in the last window")
        if declared > 0 and not uncounted:
            worst = max(abs(q - declared) for q in window) / declared
            out.update({"status": "CLOSED" if worst <= tol else "OPEN", "passed": worst <= tol,
                        "relativeError": (removed[-1] - declared) / declared, "windowMaxRelativeError": worst})
            if worst > tol:
                out["reasons"].append(
                    f"energy balance open: the ACUs remove {removed[-1] / 1e3:.1f} kW of {declared / 1e3:.1f} kW "
                    f"declared ({(removed[-1] - declared) / declared * 100:+.1f} %; worst {worst * 100:.1f} % "
                    f"over the last {needed} iterations, tolerance {tol * 100:g} %)")
        else:
            mean = sum(window) / len(window)
            drift = (max(window) - min(window)) / abs(mean) if mean else math.inf
            kinds: Dict[str, int] = {}
            for item in uncounted:
                kind = item[item.rfind("(") + 1:-1] if item.endswith(")") else item
                kinds[kind] = kinds.get(kind, 0) + 1
            out.update({"status": "STEADY" if drift <= tol else "DRIFTING", "passed": drift <= tol,
                        "windowRelativeDrift": drift,
                        "closureUnavailable": ", ".join(f"{n} x {kind}" for kind, n in sorted(kinds.items()))
                                              or "no declared heat",
                        "rule": "the declared heat is incomplete, so the heat the ACUs remove must vary within "
                                "the tolerance of its mean over the last window"})
            if drift > tol:
                out["reasons"].append(
                    f"ACU heat still drifting: {drift * 100:.1f} % over the last {needed} iterations "
                    f"(tolerance {tol * 100:g} %; balance not closable: {out['closureUnavailable']})")
        return out
    except ArithmeticError as exc:
        out.update({"status": "INVALID_DATA", "reasons": [str(exc)]})
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        out["reasons"].append(str(exc))
        return out


def assess_run(
    run_dir: Path,
    *,
    log_path: Optional[Path] = None,
    sensor_names: Optional[List[str]] = None,
    write: bool = True,
    residual_kwargs: Optional[Dict[str, Any]] = None,
    sensor_kwargs: Optional[Dict[str, Any]] = None,
    live_manifest: Optional[Path] = None,
) -> Dict[str, Any]:
    """Both legs on one solve directory; writes ``convergence.json`` next to the log.

    ``live_manifest``: see configured_return_patch_verdict (a running solve).
    """
    run_dir = Path(run_dir)
    log_file = Path(log_path) if log_path else _latest_log(run_dir)
    parsed = parse_solver_log(log_file) if log_file is not None and log_file.is_file() else None
    application = (parsed.application if parsed else None) or _control_dict(run_dir).get("application")
    if application and _TRANSIENT_APP.search(application):
        tr = transient_verdict(parsed or SolverLog(), run_dir)
        probe_file = find_probe_file(run_dir)
        out = {
            "verdict": "TRANSIENT_COMPLETED" if tr["passed"] else "TRANSIENT_INCOMPLETE",
            "rule": "transient: the solver reached endTime and printed End without a fatal error or a "
                    "non-finite residual; time steps, Courant and residuals are diagnostics",
            "log": str(log_file) if log_file else None,
            "probes": str(probe_file) if probe_file else None,
            "transient": tr,
        }
        if write:
            (run_dir / "convergence.json").write_text(json.dumps(out, indent=2, default=float))
        return out
    res: Dict[str, Any]
    if parsed is None:
        res = {"passed": False, "reasons": ["no solver log found"], "fields": {}}
    else:
        sealed = sealed_region_bounding(run_dir, parsed)
        res = residual_verdict(parsed, **{**(residual_kwargs or {}), "sealed_bounding": sealed})
    probe_file = find_probe_file(run_dir)
    returns = configured_return_patch_verdict(
        run_dir, final_iteration=(parsed.iterations[-1] if parsed and parsed.iterations else None),
        expected_iterations=(parsed.iterations if parsed else None),
        live_manifest=live_manifest, **(sensor_kwargs or {}),
    )
    # a point probe writes every 10th iteration: it covers the log's iterations
    point_kwargs = {"iterations": len(parsed.iterations) if parsed else None, **(sensor_kwargs or {})}
    if returns is None:
        # No configured return route: preserve the original point-only path.
        sens = sensor_verdict(probe_file, names=sensor_names, **point_kwargs)
    else:
        configured_names = returns.get("configuredPointProbeNames") or []
        point_required = bool(sensor_names) or bool(configured_names) or probe_file is not None
        points = (sensor_verdict(probe_file, names=(sensor_names or configured_names or None),
                                 **point_kwargs) if point_required else None)
        sens = _temperature_streams_verdict(points, returns)
    # Owner rule (2026-10-20): the solve has converged once the ACU return
    # temperatures are steady; the residual leg is reported, not required.
    # Owner rule (2026-10-30): and once the energy balance closes -- Alibaba-sige
    # read CONVERGED at iteration 204 with its returns 0.2 K above supply and
    # the ACUs removing 2 % of the 995 kW the servers put in.
    verdict = steady_verdict(sens)
    decisive = returns if returns is not None else sens
    energy = energy_balance_verdict(
        run_dir, expected_iterations=(parsed.iterations if parsed else None),
        live_manifest=live_manifest, min_window=(sensor_kwargs or {}).get("min_window"))
    if energy is not None and verdict == "CONVERGED" and not energy["passed"]:
        verdict = "INCONCLUSIVE" if energy["status"] in ("NO_EVIDENCE", "TOO_SHORT") else "NOT_CONVERGED"
        decisive = energy
    fraction = (sensor_kwargs or {}).get("window_fraction") or WINDOW_FRACTION
    needed = (sensor_kwargs or {}).get("min_window") or MIN_WINDOW_SAMPLES
    tol = (sensor_kwargs or {}).get("plateau_tol", DEFAULT_STOP_TOLERANCE_K)
    window_text = (f"the last {fraction * 100:g} % of a run of at least "
                   f"{minimum_run_iterations(window_fraction=fraction, min_window=needed)} iterations"
                   if fraction else f"the last {needed} iterations")
    out = {
        "verdict": verdict,
        # why it is not CONVERGED, e.g. "too-short: 20 iterations < minimum 1000 (...)"
        "verdictReason": None if verdict == "CONVERGED" else next(iter(decisive.get("reasons") or []), None),
        "rule": f"every return-air temperature changed then plateaued (within {tol:g} K over "
                f"{window_text})"
                + (", and the heat the ACUs remove closed on the declared heat" if energy is not None else "")
                + "; residuals are diagnostics",
        "stopToleranceK": tol,
        "log": str(log_file) if log_file else None,
        "probes": str(probe_file) if probe_file else None,
        "residuals": res,
        "sensors": sens,
        "energy": energy,
    }
    if write:
        (run_dir / "convergence.json").write_text(json.dumps(out, indent=2, default=float))
    return out


def summary_line(result: Dict[str, Any]) -> str:
    if "transient" in result:
        tr = result["transient"]
        parts = [f"convergence: {result.get('verdict')}",
                 f"t = {tr.get('final_time')} of {tr.get('end_time')} s in {tr.get('steps')} steps",
                 f"max Courant {tr.get('courant_max')} (maxCo {tr.get('max_co')})"]
        loop = tr.get("outer_loop")
        if loop:
            total = loop["steps_converged"] + loop["steps_hit_limit"]
            parts.append(f"{loop['steps_hit_limit']} of {total} steps hit the outer-corrector limit")
        if tr.get("reasons"):
            parts.append("(" + "; ".join(tr["reasons"][:4]) + ")")
        return " ".join(parts)
    res = result.get("residuals", {})
    sens = result.get("sensors", {})
    parts = [f"convergence: {result.get('verdict')}"]
    parts.append("residuals " + ("PASS" if res.get("passed") else "FAIL"))
    if res.get("reasons"):
        parts.append("(" + "; ".join(res["reasons"][:4]) + ")")
    parts.append("sensors " + ("PASS" if sens.get("passed") else sens.get("status", "FAIL")))
    if sens.get("reasons"):
        parts.append("(" + "; ".join(sens["reasons"][:4]) + ")")
    energy = result.get("energy")
    if energy is not None:
        parts.append("energy " + ("PASS" if energy.get("passed") else energy.get("status", "FAIL")))
        if energy.get("reasons"):
            parts.append("(" + "; ".join(energy["reasons"][:2]) + ")")
    return " ".join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Dual convergence verdict for a solve directory")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    out = assess_run(args.run_dir, log_path=args.log, write=not args.no_write)
    print(summary_line(out))
    print(json.dumps(out, indent=2, default=float))
    return 0 if out["verdict"] in ("CONVERGED", "TRANSIENT_COMPLETED") else 1


if __name__ == "__main__":
    sys.exit(main())
