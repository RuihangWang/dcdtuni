"""Dual convergence verdict for a steady air-cooling solve (2026-09-22).

A steady ``buoyantBoussinesqSimpleFoam`` run is CONVERGED only when BOTH hold
(the user's acceptance rule for dcCart meshes, 5.6 of the 2026-09-21 brief):

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


@dataclass
class SolverLog:
    iterations: List[float] = field(default_factory=list)
    residuals: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)  # field -> [(iter, initial)]
    continuity: List[Tuple[float, float, float, float]] = field(default_factory=list)  # (iter, local, global, cumulative)
    bounding: Dict[str, List[float]] = field(default_factory=dict)  # field -> [iter]
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
) -> Dict[str, Any]:
    """Leg 1: residuals decrease smoothly. Returns a dict with ``passed``."""
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
    window_fraction: float = 0.2,
    min_window: int = 5,
    change_min: float = 0.05,
    plateau_tol: float = 0.1,
    plateau_fraction: float = 0.01,
) -> Dict[str, Any]:
    """Leg 2: every probe temperature changed, then plateaued. ``passed`` / ``status``."""
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
        rows, names=names, window_fraction=window_fraction, min_window=min_window,
        change_min=change_min, plateau_tol=plateau_tol, plateau_fraction=plateau_fraction,
    )


def temperature_series_verdict(
    rows: List[List[float]],
    *,
    names: Optional[List[str]] = None,
    window_fraction: float = 0.2,
    min_window: int = 5,
    change_min: float = 0.05,
    plateau_tol: float = 0.1,
    plateau_fraction: float = 0.01,
) -> Dict[str, Any]:
    """The same changed-then-plateau rule for explicit temperature rows."""
    out: Dict[str, Any] = {"passed": False, "status": "NO_SENSORS", "probes": {}, "reasons": []}
    if not rows:
        out["reasons"].append("temperature evidence holds no samples")
        return out
    n_probes = max(len(r) for r in rows)
    rows = [r for r in rows if len(r) == n_probes]
    n = len(rows)
    if n < 3:
        out["status"] = "TOO_SHORT"
        out["reasons"].append(f"only {n} probe samples")
        return out
    window = min(n, max(min_window, int(n * window_fraction)))
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
    out["passed"] = all_ok
    return out


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


def _read_return_patch_series(path: Path, run_dir: Path, patch: str) -> Tuple[List[float], List[float], Dict[str, Any]]:
    data = _monitor_bytes(path, run_dir)
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
    if len(headers.get("columns", [])) != 1 or headers["columns"][0].split() != ["Time", "areaAverage(T)"]:
        raise ValueError(f"{patch} native output does not select exactly areaAverage(T)")
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
                raise ValueError(f"{patch} has a malformed areaAverage(T) row")
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


def configured_return_patch_verdict(
    run_dir: Path, *, final_iteration: Optional[float],
    expected_iterations: Optional[List[float]] = None, **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Assess every configured true ACU return patch; None retains old probes."""
    receipt_path = Path(run_dir) / "air-run-execution-receipt.json"
    if not receipt_path.is_file():
        return None
    unknown: Dict[str, Any] = {
        "passed": False, "status": "NO_SENSORS", "probes": {}, "reasons": [],
        "evidenceSource": "configured-acu-return-areaAverage(T)", "metadataUnknown": True,
    }
    try:
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
        payload = _monitor_bytes(archive, owner)
        manifest = json.loads(payload)
        sha = hashlib.sha256(payload).hexdigest()
        if (sha != identity["cfdrunSha256"] or manifest.get("runId") != identity["runId"]
                or (manifest.get("sourceProject") or {}).get("contentHash") != identity["projectContentHash"]):
            raise ValueError("return monitor archive does not match executed source identity")
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
                    or not re.fullmatch(r"constructions\.acus\.[A-Za-z0-9_-]+", str(bindings[0].get("objectPath", "")))):
                raise ValueError(f"{patch} is not one bound true ACU return boundary")
            function = "avgT_" + patch.replace(" ", "_")
            _require_return_monitor(control, function, patch)
            times, values, source = _read_return_patch_series(
                _return_patch_file(run_dir, function), run_dir, patch)
            if axes is None:
                axes = times
            elif times != axes:
                raise ValueError("configured return patches do not share a complete time axis")
            source.update({"functionName": function, "objectPath": bindings[0]["objectPath"],
                           "surfaceUid": bindings[0].get("surfaceUid"), "solidName": patch})
            sources.append(source)
            columns.append(values)
            names.append(patch)
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
        if (_monitor_bytes(receipt_path, run_dir) != receipt_bytes
                or _monitor_bytes(archive, owner) != payload
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
    """All configured temperature routes pass; an absent route is explicit."""
    streams = {
        "pointProbes": ({"configured": True, "verdict": points} if points is not None
                        else {"configured": False, "skipped": True}),
        "acuReturnPatches": {"configured": True, "verdict": returns},
    }
    if points is None:
        out = dict(returns)
        out["streams"] = streams
        return out
    routes = [("point", points), ("return", returns)]
    failures = [result for _, result in routes if not result.get("passed", False)]
    unknown = {"NO_SENSORS", "TOO_SHORT"}
    known_failures = [result for result in failures if result.get("status") not in unknown]
    if known_failures:
        status = known_failures[0].get("status", "INVALID_DATA")
    elif failures:
        status = ("NO_SENSORS" if any(result.get("status") == "NO_SENSORS" for result in failures)
                  else "TOO_SHORT")
    else:
        status = "OK"
    return {
        "passed": not failures, "status": status, "streams": streams,
        "evidenceSource": "point-probes-and-configured-acu-return-areaAverage(T)",
        "metadataUnknown": any(result.get("metadataUnknown", False) for _, result in routes),
        "probes": {f"{route}:{name}": value for route, result in routes
                   for name, value in result.get("probes", {}).items()},
        "reasons": [f"{route}: {reason}" for route, result in routes
                    for reason in result.get("reasons", [])],
    }


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


def assess_run(
    run_dir: Path,
    *,
    log_path: Optional[Path] = None,
    sensor_names: Optional[List[str]] = None,
    write: bool = True,
    residual_kwargs: Optional[Dict[str, Any]] = None,
    sensor_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Both legs on one solve directory; writes ``convergence.json`` next to the log."""
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
        res = residual_verdict(parsed, **(residual_kwargs or {}))
    probe_file = find_probe_file(run_dir)
    returns = configured_return_patch_verdict(
        run_dir, final_iteration=(parsed.iterations[-1] if parsed and parsed.iterations else None),
        expected_iterations=(parsed.iterations if parsed else None), **(sensor_kwargs or {}),
    )
    if returns is None:
        # No configured return route: preserve the original point-only path.
        sens = sensor_verdict(probe_file, names=sensor_names, **(sensor_kwargs or {}))
    else:
        configured_names = returns.get("configuredPointProbeNames") or []
        point_required = bool(sensor_names) or bool(configured_names) or probe_file is not None
        points = (sensor_verdict(probe_file, names=(sensor_names or configured_names or None),
                                 **(sensor_kwargs or {})) if point_required else None)
        sens = _temperature_streams_verdict(points, returns)
    if res["passed"] and sens["passed"]:
        verdict = "CONVERGED"
    elif sens.get("status") in ("NO_SENSORS", "TOO_SHORT") and res["passed"]:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "NOT_CONVERGED"
    out = {
        "verdict": verdict,
        "rule": "residuals decrease smoothly AND every return-air sensor changed then plateaued",
        "log": str(log_file) if log_file else None,
        "probes": str(probe_file) if probe_file else None,
        "residuals": res,
        "sensors": sens,
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
