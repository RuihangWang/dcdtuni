"""Steady-converged start for a transient air run (transient design §4).

A cfdrun v3 run whose timeline starts from the steady converged field is run in
two stages in ONE run directory, so both see the same mesh partitions: SIMPLE
with every boundary at its t = 0 value (no table, no time() — in SIMPLE time()
is the iteration count), then PIMPLE from that field. Between them the steady
result's internal fields are written into the transient 0/ files, whose
boundary conditions (tables, expressions) stay exactly as generated.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_INTERNAL = re.compile(r"^internalField\b", re.MULTILINE)
_FORMAT = re.compile(r"^\s*format\s+(\w+)\s*;", re.MULTILINE)


def _internal_field_span(text: str) -> Optional[Tuple[int, int]]:
    """[start, end) of the `internalField …;` statement, lists included."""
    match = _INTERNAL.search(text)
    if not match:
        return None
    depth = 0
    for index in range(match.end(), len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == ";" and depth == 0:
            return match.start(), index + 1
    return None


def seed_internal_fields(source_dir: Path, target_dir: Path) -> List[str]:
    """Replace each target field's internalField by the source field's.

    Fields present in both directories are seeded; the others are left as
    generated. ASCII only (the steady stage writes ASCII). At least T and U
    must be seeded, or the transient run would start cold. The pressure-
    corrected phi surface field is also required from the same partition.
    """
    if Path(source_dir).resolve().parent != Path(target_dir).resolve().parent:
        raise ValueError("steady fields and phi must belong to the same mesh partition")
    seeded: List[str] = []
    for target in sorted(Path(target_dir).iterdir()):
        source = Path(source_dir) / target.name
        if target.name == "phi" or not target.is_file() or not source.is_file():
            continue
        source_text = source.read_text(encoding="utf-8", errors="strict")
        form = _FORMAT.search(source_text)
        if form and form.group(1) != "ascii":
            raise ValueError(f"{source}: the steady start needs ASCII fields, found {form.group(1)}")
        source_span = _internal_field_span(source_text)
        target_text = target.read_text(encoding="utf-8")
        target_span = _internal_field_span(target_text)
        if source_span is None or target_span is None:
            continue
        target.write_text(target_text[:target_span[0]] + source_text[source_span[0]:source_span[1]]
                          + target_text[target_span[1]:], encoding="utf-8")
        seeded.append(target.name)
    if not {"T", "U"} <= set(seeded):
        raise ValueError(f"the steady start seeded {seeded}; T and U are required")
    # U interpolation is not the pressure-corrected conservative surface
    # flux. Preserve phi's entire surface field, including processor/cyclic
    # and physical boundary values, from this exact same mesh partition.
    source_phi = Path(source_dir) / "phi"
    if not source_phi.is_file():
        raise ValueError(f"{source_phi}: steady start requires pressure-corrected phi")
    try:
        phi_text = source_phi.read_text(encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{source_phi}: steady phi must be ASCII") from exc
    header = re.search(r"FoamFile\s*\{[^}]*\}", phi_text)
    if not header or not re.search(r"\bclass\s+surfaceScalarField\s*;", header[0]) or not re.search(r"\bobject\s+phi\s*;", header[0]):
        raise ValueError(f"{source_phi}: expected surfaceScalarField object phi")
    if not re.search(r"\bformat\s+ascii\s*;", header[0]):
        raise ValueError(f"{source_phi}: steady phi must be ASCII")
    normalized_header = re.sub(r'\blocation\s+"[^"]*"\s*;', 'location    "0";', header[0])
    phi_text = phi_text[:header.start()] + normalized_header + phi_text[header.end():]
    (Path(target_dir) / "phi").write_text(phi_text, encoding="utf-8")
    seeded.append("phi")
    return seeded


def latest_time_dir(case_dir: Path) -> Optional[Path]:
    """The case's latest positive time directory, or None."""
    times = []
    for entry in Path(case_dir).iterdir():
        if not entry.is_dir():
            continue
        try:
            value = float(entry.name)
        except ValueError:
            continue
        if value > 0:
            times.append((value, entry))
    return max(times)[1] if times else None


_NUMERIC_DIAGNOSTIC = re.compile(
    r"^(?:Time\s*=|deltaT\s*=|Courant Number\b|ExecutionTime\s*=|"
    r"time step continuity errors\b)|\bSolving for\b", re.IGNORECASE)
_NUMERIC_TOKEN = re.compile(
    r"(?<![\w.])[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|nan|inf(?:inity)?)(?![\w.])",
    re.IGNORECASE)


def _nonfinite_numeric_diagnostics(lines: list[str]) -> list[str]:
    """Inspect actual residual, continuity and time-step numeric output."""
    return [line.strip() for line in lines
            if _NUMERIC_DIAGNOSTIC.search(line.strip())
            and any(not math.isfinite(float(token[0]))
                    for token in _NUMERIC_TOKEN.finditer(line))]


def require_steady_convergence(log_path: Path) -> Dict[str, Any]:
    """Require this SIMPLE invocation's strict residualControl evidence.

    OpenFOAM 2412 simpleControl::loop checks the just-completed iteration
    BEFORE advancing time. criteriaSatisfied uses the FIRST solver performance
    initial residual (the maximum vector component), with strict < absTol.
    A second pressure/non-orthogonal solve is therefore never the pressure
    convergence test. Read the tolerances the solver actually loaded from its
    startup banner; neither a positive time directory nor the general smooth
    residual/temperature-plateau assessment proves this start condition.
    """
    from backend.aircooling.runner.convergence import parse_solver_log

    log_path = Path(log_path)
    if not log_path.is_file():
        raise RuntimeError(f"steady start did not converge: missing solver evidence {log_path}")
    log = parse_solver_log(log_path)
    criteria: Dict[str, float] = {}
    collecting = False
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for raw in lines:
        line = raw.strip()
        if line == "SIMPLE: convergence criteria":
            collecting = True
            criteria.clear()
            continue
        if collecting:
            match = re.fullmatch(r'field ("[^"]+"|\S+)\s+tolerance\s+(\S+)', line)
            if match:
                name = match.group(1).strip('"')
                try:
                    criteria[name] = float(match.group(2))
                except ValueError:
                    criteria[name] = float("nan")
            elif line:
                collecting = False

    reasons = []
    nonfinite_diagnostics = _nonfinite_numeric_diagnostics(lines)
    if nonfinite_diagnostics:
        reasons.append("solver has non-finite numeric diagnostics: " + nonfinite_diagnostics[0])
    if log.application != "buoyantBoussinesqSimpleFoam_new":
        reasons.append(f"unexpected solver {log.application!r}")
    if not log.ended or log.fatal or log.nonfinite:
        reasons.append("solver did not end cleanly with finite residuals")
    completed = log.iterations[-1] if log.iterations else None
    if log.converged_at is None:
        reasons.append("SIMPLE residualControl was not reached (iteration limit is not convergence)")
    elif completed is None or log.converged_at != completed:
        reasons.append(f"convergence marker {log.converged_at} does not match completed iteration {completed}")
    if not {"p_rgh", "U", "T"} <= set(criteria):
        reasons.append("solver startup is missing the required SIMPLE residualControl tolerances")

    first = {
        name: series[-1][1]
        for name, series in log.residuals.items()
        if series and series[-1][0] == completed
    }
    grouped = {name: value for name, value in first.items() if name not in {"Ux", "Uy", "Uz"}}
    if {"Ux", "Uy", "Uz"} <= set(first):
        grouped["U"] = max(first[name] for name in ("Ux", "Uy", "Uz"))
    for required in ("p_rgh", "U", "T"):
        if required not in grouped:
            reasons.append(f"missing first initial residual for {required} at completed iteration {completed}")
    matched = set()
    for pattern, tolerance in criteria.items():
        if not math.isfinite(tolerance) or tolerance <= 0:
            reasons.append(f"invalid residualControl tolerance {pattern}={tolerance}")
            continue
        try:
            fields = [name for name in grouped if re.fullmatch(pattern, name)]
        except re.error:
            reasons.append(f"invalid residualControl field pattern {pattern!r}")
            continue
        # As in SIMPLE, a regex for inactive turbulence fields is not checked.
        for name in fields:
            matched.add(name)
            residual = grouped[name]
            if not math.isfinite(residual) or residual < 0 or not residual < tolerance:
                reasons.append(f"{name} first initial residual {residual:g} is not below residualControl {tolerance:g}")
    if not {"p_rgh", "U", "T"} <= matched:
        reasons.append("required flow/temperature fields were not checked")
    if reasons:
        raise RuntimeError("steady start did not converge: " + "; ".join(reasons) + f". Evidence: {log_path}")
    return {
        "verdict": "STEADY_CONVERGED",
        "log": str(log_path),
        "converged_iteration": log.converged_at,
        "completed_iteration": completed,
        "residual_control": criteria,
        "first_initial_residuals": grouped,
    }


def require_steady_verdict(result: Dict[str, Any], log_path: Path) -> Dict[str, Any]:
    """Accept a monitored steady start on the steady convergence verdict.

    The same rule a monitored steady solve is judged and stopped by
    (``convergence.assess_run``): every monitored return-air temperature
    changed, then plateaued, over a window long enough to judge
    (``convergence.run_length_shortfall``); residuals are diagnostics. The
    solver must still have ended cleanly with finite residuals.
    """
    from backend.aircooling.runner.convergence import parse_solver_log, run_length_shortfall

    log_path = Path(log_path)
    reasons = []
    log = parse_solver_log(log_path) if log_path.is_file() else None
    if log is None:
        reasons.append(f"missing solver evidence {log_path}")
    else:
        if log.application != "buoyantBoussinesqSimpleFoam_new":
            reasons.append(f"unexpected solver {log.application!r}")
        if not log.ended or log.fatal or log.nonfinite:
            reasons.append("solver did not end cleanly with finite residuals")
        nonfinite = _nonfinite_numeric_diagnostics(
            log_path.read_text(encoding="utf-8", errors="replace").splitlines())
        if nonfinite:
            reasons.append("solver has non-finite numeric diagnostics: " + nonfinite[0])
    if result.get("verdict") != "CONVERGED":
        reasons.append(f"convergence verdict {result.get('verdict')}: {result.get('reason') or result.get('reasons')}")
    iterations = int((result.get("residuals") or {}).get("iterations") or 0)
    shortfall = run_length_shortfall(iterations)
    if shortfall:
        reasons.append(shortfall)
    if reasons:
        raise RuntimeError("steady start did not converge: " + "; ".join(str(r) for r in reasons)
                           + f". Evidence: {log_path}")
    return {
        "verdict": "STEADY_CONVERGED",
        "rule": result.get("rule"),
        "log": str(log_path),
        "completed_iteration": iterations,
        "first_initial_residuals": (result.get("residuals") or {}).get("finalInitialResidual"),
    }
