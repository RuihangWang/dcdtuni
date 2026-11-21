"""Scripted air run: ``.cfdproj`` -> headless export -> server mesh -> server solve -> metrics.

Stages, all on the product path:

1. **export** -- the React app's own export modules, run headless
   (:mod:`agent.scripted_api.headless`), produce the ``.cfdrun`` manifest and the
   grouped ASCII STL bytes the editor would upload for this project. A
   steady/transient choice goes through the editor's one writer of that choice
   (``setSimulationMode``), so ``transient.airMode`` and the manifest agree.
2. **mesh** -- a private server (:mod:`agent.scripted_api.server_session`, started as
   ``start_server.sh --app api``) receives the editor's ``/api/run-mesh`` body
   with ``meshOnly``; the server publishes the inputs into the case and runs
   ``run_cfd --from-cfdrun`` (STLGenRunner). ``meshConfig.meshEngine``
   (``dcCart`` or ``snappy``) picks the mesher; ``meshgen``, retired on
   2026-10-26, is refused as an ``input`` failure before any server starts.
3. **solve** -- the same body with ``solveOnly`` and no STL, as the editor's
   Solve button sends; results land in ``<case>/log/<timestamp>_<case>/``.
4. **metrics** -- the runner's own receipts (execution receipt,
   ``convergence.json``, ``sensorResults/``) plus field read-back
   (:mod:`agent.scripted_api.air_metrics`).

``stages`` selects mesh and/or solve: ``("mesh", "solve")`` (default),
``("mesh",)``, or ``("solve",)``, which reuses the case's existing mesh after
checking that its quality receipt is ``accepted`` and that it was built from
the STL batch this project exports now. A solve on one MPI rank is refused
unless ``allow_serial_solve`` (CLAUDE.md debugging rule 7).

**Hybrid air/liquid** (:func:`run_hybrid_case`, and :func:`run_air_case` for a
project with liquid servers that carry fan flow or a v2 fan hybrid budget): a
solve needs the heat of a completed liquid session, as in the editor. In the
same private server, before any air job, the API posts the ``.liquidrun`` the
liquid panel would submit (``POST /api/liquid/runs``, ``start``, poll until
completed), then meshes without ``hybridHeat`` and solves with
``hybridHeat={project, liquidRunId, sampleIndex}`` -- the body
``buildRunRequest('solve')`` builds (run/runSubmit.ts): ``project`` is the
headless ``canonicalProjectForHash``, ``sampleIndex`` the run's latest accepted
sample unless given. The server compiles the liquid heat into the air manifest
and writes ``model/heat-ledger.json``, which ``result.json`` records. The
editor's own gates are refused at input: a v2 fan budget must be declared and
``runDefaults.liquid.inheritResults`` must be true.

Every call writes ``<case>/log/api/<YYYY-MM-DD_HHMMSS>_SGT__{air,hybrid}[_label]/``
containing ``result.json`` (also returned), the exact manifest sent, both job
logs and the server log. Nothing in an earlier API directory is touched.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
import shutil
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.scripted_api import identity as ident
from agent.scripted_api.headless import HeadlessCompileError, compile_project
from agent.scripted_api.rundir import case_api_root, create_run_dir, read_json_source, read_source, write_json, write_text

#: Seconds between liquid-run status polls.
LIQUID_POLL_SECONDS = 0.5
RESULT_FORMAT = "dcdtuni.api.result/v1"
_CELLS = re.compile(r"^\s*cells:\s+(\d+)\s*$")
_CREATED_RUN = re.compile(r"Created run directory:\s*(\S+)")
_USING_BINARY = re.compile(r"Using repo-built (mesher|solver):\s*(\S+)")
_EXEC = re.compile(r"Exec\s*:\s*(\S+)")
_FOAM_VERSION = re.compile(r"Version:\s*(v?\d[\w.]*)")
_FOAM_BUILD = re.compile(r"Build\s*:\s*(\S+.*)$")


class PipelineFailure(RuntimeError):
    def __init__(self, stage: str, reason: str):
        self.stage, self.reason = stage, reason
        super().__init__(f"{stage}: {reason}")


def deep_merge(target: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def load_project(cfdproj: Path) -> tuple[bytes, Dict[str, Any]]:
    raw, project = read_json_source(cfdproj)
    if not isinstance(project, dict):
        raise ValueError(f"{cfdproj} is not a .cfdproj object")
    return raw, project


#: Stages a caller can select; ``export`` always runs and ``metrics`` follows ``solve``.
AIR_STAGES = ("mesh", "solve")
SOLVER_TYPES = ("steady", "transient")
#: The executable each ``meshConfig.meshEngine`` runs (identity when the job log names none).
MESHER_BINARIES = {"dcCart": "dcCartMesh", "snappy": "snappyHexMesh"}


def mesh_engines() -> Tuple[str, ...]:
    """The mesh engines the cfdrun schema admits (``MeshConfig.meshEngine``)."""
    from typing import get_args
    from backend.aircooling.runner.cfdrun_schema import MeshConfig

    found: List[str] = []

    def walk(annotation: Any) -> None:
        for arg in get_args(annotation):
            if isinstance(arg, str):
                found.append(arg)
            else:
                walk(arg)
    walk(MeshConfig.model_fields["meshEngine"].annotation)
    return tuple(dict.fromkeys(found))


def mesher_binary_name(engine: Optional[str]) -> str:
    """The mesher an engine runs; no engine means the runner's default, dcCart."""
    return MESHER_BINARIES.get(engine or "dcCart", MESHER_BINARIES["dcCart"])


def resolve_stages(stages: Optional[Sequence[str]], *, solve: bool = True) -> Tuple[str, ...]:
    """Normalise a stage selection to ``("mesh", "solve")``, ``("mesh",)`` or ``("solve",)``."""
    if stages is None:
        return AIR_STAGES if solve else ("mesh",)
    chosen = tuple(stages)
    unknown = sorted(set(chosen) - set(AIR_STAGES))
    if unknown or not chosen:
        raise ValueError(f"stages must be a non-empty selection of {AIR_STAGES}, got {list(chosen)}")
    if not solve and "solve" in chosen:
        raise ValueError("solve=False contradicts a stage selection that includes 'solve'")
    return tuple(stage for stage in AIR_STAGES if stage in chosen)


def split_solver_type(run_settings: Optional[Dict[str, Any]],
                      solver_type: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Take ``simulationConfig.solverType`` out of the overrides.

    The steady/transient choice is not a plain field: the editor writes it
    through ``setSimulationMode`` (transient/simulationMode.ts), which also sets
    ``transient.airMode`` and the physical end time. The headless compiler calls
    that same function, so the choice travels separately.
    """
    if solver_type is not None and solver_type not in SOLVER_TYPES:
        raise ValueError(f"solver_type must be one of {SOLVER_TYPES}, got {solver_type!r}")
    if not run_settings or "solverType" not in (run_settings.get("simulationConfig") or {}):
        return run_settings, solver_type
    settings = copy.deepcopy(run_settings)
    requested = settings["simulationConfig"].pop("solverType")
    if requested not in SOLVER_TYPES:
        raise ValueError(f"simulationConfig.solverType must be one of {SOLVER_TYPES}, got {requested!r}")
    if solver_type is not None and solver_type != requested:
        raise ValueError(f"solver_type={solver_type!r} contradicts run_settings simulationConfig.solverType="
                         f"{requested!r}")
    return settings, requested


#: The solve monitoring panel (求解监控) as an API run sets it: the editor's residuals and the ACU return list's
#: select-all. The runner judges a steady run CONVERGED on these return-air temperatures (convergence.py), so a
#: manifest without them is INCONCLUSIVE at best; every editor export in case/ since 2026-10-20 carries exactly this.
DEFAULT_MONITORING: Dict[str, Any] = {"residuals": True, "acuReturnTemperature": "all"}
MONITORING_KEYS = ("residuals", "serverTemperature", "acuReturnTemperature")
_BOUNDARY_NAME = re.compile(r"^\s+(\S+)\s*$", re.MULTILINE)


def split_monitoring(run_settings: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Take ``simulationConfig.monitoring`` out of the overrides: the panel's ticks for the headless compiler.

    The editor's export reads the monitoring panel through ``window.smGetMonitoringConfig``
    (cfdrun_build.js), never from the project, so the ticks travel as the
    compiler's ``monitoring`` option. Keys: ``residuals`` (bool),
    ``serverTemperature`` (patch names) and ``acuReturnTemperature`` (patch
    names, or ``"all"``: the list's select-all); a key left out keeps
    :data:`DEFAULT_MONITORING` (``serverTemperature`` then stays empty, as in the editor).
    """
    request = dict(DEFAULT_MONITORING)
    if not run_settings or "monitoring" not in (run_settings.get("simulationConfig") or {}):
        return run_settings, request
    settings = copy.deepcopy(run_settings)
    asked = settings["simulationConfig"].pop("monitoring")
    if not isinstance(asked, dict):
        raise ValueError(f"simulationConfig.monitoring must be an object, got {asked!r}")
    unknown = sorted(set(asked) - set(MONITORING_KEYS))
    if unknown:
        raise ValueError(f"simulationConfig.monitoring accepts {list(MONITORING_KEYS)}, got {unknown}")
    request.update(asked)
    return settings, request


def mesh_patch_names(mesh_dir: Path) -> Optional[List[str]]:
    """The patch names of the mesh in ``mesh_dir`` (``None`` without a complete mesh).

    Read as ``/api/server-patches`` reads them (server.py ``handle_server_patches``):
    every rank's ``boundary``, each name once, in order. These are the lists the
    monitoring panel offers.
    """
    from backend.aircooling.runner.mesh_layout import mesh_boundary_paths
    paths = mesh_boundary_paths(mesh_dir)
    if not paths:
        return None
    names = dict.fromkeys(name for path in paths for name in _BOUNDARY_NAME.findall(
        path.read_text(encoding="utf-8", errors="replace")))
    return [name for name in names if name not in ("(", ")", "{", "}")]


def monitored_patches(manifest: Dict[str, Any]) -> List[str]:
    """Every patch the manifest's ``simulationConfig.monitoring`` asks the solver to probe."""
    monitoring = (manifest.get("simulationConfig") or {}).get("monitoring") or {}
    return [row.get("patch") for group in ("serverTemperature", "acuReturnTemperature")
            for row in monitoring.get(group) or [] if isinstance(row, dict)]


def apply_run_settings(project: Dict[str, Any], run_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Overrides for ``project.runDefaults`` -- what the editor's mesh/solve panels store."""
    effective = copy.deepcopy(project)
    if run_settings:
        unknown = set(run_settings) - {"meshConfig", "simulationConfig"}
        if unknown:
            raise ValueError(f"run_settings accepts meshConfig and simulationConfig only, got {sorted(unknown)}")
        deep_merge(effective.setdefault("runDefaults", {}), run_settings)
    reject_retired_engine(((effective.get("runDefaults") or {}).get("meshConfig") or {}).get("meshEngine"))
    return effective


def reject_retired_engine(engine: Optional[str]) -> None:
    """``meshgen`` was retired on 2026-10-26: refuse it rather than mesh with another engine.

    The editor's load turns a saved ``meshgen`` into ``dcCart`` with a warning in
    its operation log; a script asking for it (``run_settings``, ``--mesh-engine``,
    a batch row) or a saved project run through the API gets this error instead.
    """
    if engine == "meshgen":
        from backend.aircooling.runner.cfdrun_schema import RETIRED_MESHGEN_MESSAGE
        raise ValueError(RETIRED_MESHGEN_MESSAGE)


def run_settings_from_options(base: Optional[Dict[str, Any]] = None, *, mesh_engine: Optional[str] = None,
                              mesh_processes: Optional[int] = None, solve_processes: Optional[int] = None,
                              end_time: Optional[float] = None,
                              solver_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The CLI's convenience options folded into ``run_settings`` (shared by ``air`` and ``batch``)."""
    settings: Dict[str, Any] = copy.deepcopy(base) if base else {}
    mesh = settings.setdefault("meshConfig", {})
    sim = settings.setdefault("simulationConfig", {})
    if mesh_engine:
        reject_retired_engine(mesh_engine)
        mesh["meshEngine"] = mesh_engine
    if mesh_processes:
        mesh.update(parallelMesh=mesh_processes > 1, meshProcess=mesh_processes)
    if solve_processes:
        sim.update(parallelSolve=solve_processes > 1, solveProcess=solve_processes)
    if end_time is not None:
        sim["endTime"] = end_time
    if solver_type:
        sim["solverType"] = solver_type
    return {key: value for key, value in settings.items() if value} or None


def process_counts(manifest: Dict[str, Any]) -> Dict[str, Any]:
    mesh = {**((manifest.get("meshGeometry") or {}).get("meshConfig") or {}), **(manifest.get("meshConfig") or {})}
    sim = manifest.get("simulationConfig") or {}
    return {
        "meshEngine": mesh.get("meshEngine"),
        "parallelMesh": mesh.get("parallelMesh"), "meshProcess": mesh.get("meshProcess"),
        "parallelSolve": sim.get("parallelSolve"), "solveProcess": sim.get("solveProcess"),
        "meshRanks": int(mesh.get("meshProcess") or 1) if mesh.get("parallelMesh") else 1,
        "solveRanks": int(sim.get("solveProcess") or 1) if sim.get("parallelSolve") else 1,
        "solverType": sim.get("solverType"), "endTime": sim.get("endTime"),
    }


def export_air_case(cfdproj: Path | str, *, run_settings: Optional[Dict[str, Any]] = None,
                    solver_type: Optional[str] = None, timestamp: Optional[str] = None,
                    mesh_patches: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Headless export only.

    The monitoring panel is set as :func:`split_monitoring` says (``run_settings``
    ``simulationConfig.monitoring``, else :data:`DEFAULT_MONITORING`);
    ``mesh_patches`` are the case mesh's patch names, which limit what the
    panel offers (none: every bound ACU return). Returns ``{cfdrunFilename,
    cfdrunJson, stlFiles, manifestErrors, preflight, preflightStops, hybrid,
    simulationMode, implementation}``.
    """
    cfdproj = Path(cfdproj).resolve()
    _, project = load_project(cfdproj)
    settings, solver_type = split_solver_type(run_settings, solver_type)
    settings, monitoring = split_monitoring(settings)
    effective = apply_run_settings(project, settings)
    stamp = timestamp or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return compile_project("air-case", effective, str(cfdproj), timestamp=stamp,
                           options=_compile_options(solver_type, monitoring, mesh_patches))


def _compile_options(solver_type: Optional[str], monitoring: Optional[Dict[str, Any]] = None,
                     mesh_patches: Optional[Sequence[str]] = None) -> Optional[Dict[str, Any]]:
    """The compiler's options; ``monitoring`` (air case only) with the mesh's patch names when known."""
    options: Dict[str, Any] = {"solverType": solver_type} if solver_type else {}
    if monitoring is not None:
        options["monitoring"] = {**monitoring, **({"meshPatches": list(mesh_patches)}
                                                   if mesh_patches is not None else {})}
    return options or None


def export_blockers(export: Dict[str, Any]) -> List[str]:
    """Why an exported case would not run end to end as exported (empty when it would).

    ``manifestErrors``: the schema rejected the manifest. ``preflight:<check>``:
    the editor stops the mesh run on that dialog. ``hybrid``: hybrid fan servers
    need a completed liquid session before an air solve, which the API does not
    provide. Seed collisions are a question in the editor, not a stop.
    """
    blockers = ["manifestErrors"] if export.get("manifestErrors") else []
    blockers += [f"preflight:{name}" for name in export.get("preflightStops") or []]
    hybrid = export.get("hybrid") or {}
    if hybrid.get("hasHybridFanServer") or hybrid.get("budgetVersion") == 2:
        blockers.append("hybrid")
    return blockers


def _stl_body(files: Dict[str, str]) -> Dict[str, str]:
    """``btoa(unescape(encodeURIComponent(text)))``: UTF-8 bytes, then base64 (runPayload.ts)."""
    return {name: base64.b64encode(text.encode("utf-8")).decode("ascii") for name, text in files.items()}


def _scan_log(lines: Iterable[str]) -> Dict[str, Any]:
    found: Dict[str, Any] = {"cells": None, "binaries": {}, "applications": [], "openfoamVersion": None,
                             "openfoamBuild": None, "runDir": None}
    for line in lines:
        match = _CELLS.match(line.split("| ", 2)[-1] if "| " in line else line)
        if match:
            found["cells"] = int(match.group(1))
        match = _CREATED_RUN.search(line)
        if match:
            found["runDir"] = match.group(1)
        match = _USING_BINARY.search(line)
        if match:
            # The runner logs every repo-built binary it resolves (the solver, then
            # utilities such as writeExplicitBC); keep the solver application itself.
            kind, path = match.group(1), match.group(2)
            found.setdefault("allBinaries", []).append(path)
            if kind == "mesher" or re.search(r"Foam(_\w+)?$", path):
                found["binaries"][kind] = path
        match = _EXEC.search(line)
        if match and match.group(1) not in found["applications"]:
            found["applications"].append(match.group(1))
        if "O peration" in line or "Version:" in line:
            match = _FOAM_VERSION.search(line)
            if match and found["openfoamVersion"] is None:
                found["openfoamVersion"] = match.group(1)
        match = _FOAM_BUILD.search(line)
        if match and found["openfoamBuild"] is None:
            found["openfoamBuild"] = match.group(1).strip()
    return found


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _convergence_summary(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    # reason: why a steady run is not CONVERGED (convergence.json verdictReason, 2026-10-22 on)
    out = {"verdict": report.get("verdict"), "reason": report.get("verdictReason"), "rule": report.get("rule")}
    residuals = report.get("residuals") or {}
    sensors = report.get("sensors") or {}
    if residuals:
        out["residuals"] = {"passed": residuals.get("passed"), "reasons": residuals.get("reasons"),
                            "iterations": residuals.get("iterations"), "convergedAt": residuals.get("converged_at"),
                            "cumulativeContinuity": residuals.get("cumulative_continuity")}
        fields = residuals.get("fields") or {}
        out["residuals"]["finalInitialResidual"] = {
            name: (row.get("final") if isinstance(row, dict) else None) for name, row in fields.items()}
    if sensors:
        out["sensors"] = {"passed": sensors.get("passed"), "status": sensors.get("status"),
                          "reasons": sensors.get("reasons"), "iterations": sensors.get("iterations"),
                          "minimumIterations": sensors.get("minimumIterations"),
                          "samples": sensors.get("samples"), "windowSamples": sensors.get("window_samples")}
    energy = report.get("energy")
    if isinstance(energy, dict):
        # the energy leg (2026-10-30): the heat the ACUs remove against the declared heat
        out["energy"] = {k: energy.get(k) for k in (
            "passed", "status", "reasons", "toleranceFraction", "declaredHeatW", "acuHeatRemovedW",
            "relativeError", "windowMaxRelativeError", "windowRelativeDrift", "closureUnavailable")}
    if "transient" in report:
        out["transient"] = report["transient"]
    return out


def _job_tail(lines: List[str], count: int = 5) -> str:
    return " | ".join(lines[-count:])


def archived_stl_batch(case: Path, manifest: Dict[str, Any]) -> Optional[Path]:
    """The archived upload holding exactly the STL bytes ``manifest`` declares, newest first, or ``None``.

    The server's rule (server.py ``_archived_stl_batch_dir``): a mesh job
    rewrites ``Mesh/constant/triSurface`` and archives the uploaded batch
    unchanged under ``log/air-inputs/<id>/Mesh/constant/triSurface``; a hybrid
    solve, which uploads no STL, measures the liquid servers' faces on that batch.
    """
    declared = (manifest.get("geometryExport") or {}).get("stlFiles") or {}
    expected = {row.get("path"): row.get("contentHash") for row in declared.values() if isinstance(row, dict)}
    root = case / "log" / "air-inputs"
    if not expected or not all(isinstance(name, str) for name in expected) or not root.is_dir():
        return None
    for entry in sorted(root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        tri = entry / "Mesh" / "constant" / "triSurface"
        if not tri.is_dir():
            continue
        if {path.name for path in tri.iterdir() if path.is_file() and path.suffix.lower() == ".stl"} != set(expected):
            continue
        if all(ident.sha256_file(tri / name) == digest for name, digest in expected.items()):
            return tri
    return None


def check_mesh_reuse(case: Path, manifest: Dict[str, Any], *, hybrid: bool = False) -> Dict[str, Any]:
    """Proof that ``<case>``'s existing mesh may be solved for this export, or ``PipelineFailure("input")``.

    The mesh must carry an ``accepted`` quality receipt, and the STL batch it was
    built from must be the batch this project exports now: the content hashes
    the manifest binds (``geometryExport.stlFiles[*].contentHash``) against the
    ``exported_stl_hashes`` of the mesh source receipt the runner wrote, or, for
    a manifest without source surfaces (no such receipt is written), against the
    files in ``Mesh/constant/triSurface/``. Which receipt is the runner's own
    choice (``_verify_source_stl_snapshot_before_solve``): a hybrid solve
    (``hybrid``: it carries liquid heat; or the runner's
    ``requires_hybrid_air_gate`` holds for the manifest) reads
    ``hybrid-mesh-source.json`` (``hybrid-mesh-source/v2``), any other
    ``source-surface-mesh-source.json`` (``source-surface-mesh-source/v2``).
    A hybrid solve also needs the uploaded batch itself, which the server finds
    archived by the mesh job (:func:`archived_stl_batch`) or, unchanged, in the
    mesh's ``triSurface``. The runner repeats its own, stricter check before
    solving; this one refuses before a server is started and says what to do.
    """
    receipt_path = case / "mesh-quality-receipt.json"
    receipt = _read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise PipelineFailure("input", f"solve-only needs an existing mesh with its quality record, but {receipt_path} "
                                       "is missing or unreadable; mesh first (stages=('mesh',) / --mesh-only, or a full "
                                       "run)")
    # A rejected verdict is reported (the runner logs it and the result keeps the
    # receipt); it does not stop the solve (owner 2026-10-22).
    mesh_dir = case / "Mesh"
    if not (mesh_dir / "constant").is_dir():
        raise PipelineFailure("input", f"solve-only found no mesh in {mesh_dir}; mesh first")
    geometry = manifest.get("geometryExport") or {}
    files = geometry.get("stlFiles") or {}
    exported = {key: (row or {}).get("contentHash") for key, row in sorted(files.items())}
    from backend.aircooling.runner.stlgen_runner import requires_hybrid_air_gate
    receipt_name = ("hybrid-mesh-source" if hybrid or requires_hybrid_air_gate(manifest)
                    else "source-surface-mesh-source")
    source_path = case / f"{receipt_name}.json"
    source = _read_json(source_path)
    if isinstance(source, dict) and source.get("format") == f"{receipt_name}/v2":
        basis, recorded = source_path.name, source.get("exported_stl_hashes") or {}
        if source.get("mesh_sha256") and receipt.get("meshSha256") and source["mesh_sha256"] != receipt["meshSha256"]:
            raise PipelineFailure("input", "the mesh-quality receipt and the mesh source receipt describe different "
                                           "meshes; remesh")
    elif geometry.get("sourceSurfaces") or receipt_name == "hybrid-mesh-source":
        raise PipelineFailure("input", f"solve-only needs {source_path} (the STL batch the mesh was built from); "
                                       f"it is missing or not {receipt_name}/v2; remesh")
    else:
        basis = "Mesh/constant/triSurface"
        tri = mesh_dir / "constant" / "triSurface"
        recorded = {}
        for key, row in sorted(files.items()):
            path = tri / str((row or {}).get("path") or "")
            recorded[key] = ident.sha256_file(path) if path.is_file() else None
    if recorded != exported:
        differing = sorted(key for key in set(recorded) | set(exported) if recorded.get(key) != exported.get(key))
        raise PipelineFailure("input", f"the STL batch this project exports differs from the one the mesh was built "
                                       f"from ({basis}): {len(differing)} file(s) differ, e.g. {differing[:5]}; remesh")
    batch: Dict[str, Any] = {"basis": basis, "files": len(exported), "match": True}
    if receipt_name == "hybrid-mesh-source":
        archive = archived_stl_batch(case, manifest)
        tri = mesh_dir / "constant" / "triSurface"
        if archive is None and not all(
                (tri / str(row.get("path"))).is_file() and ident.sha256_file(tri / str(row.get("path"))) ==
                row.get("contentHash") for row in files.values() if isinstance(row, dict)):
            raise PipelineFailure("input", f"the hybrid solve measures the liquid servers' faces on the STL batch the "
                                           f"mesh was built from, and no copy of it is left: the mesh job archives it "
                                           f"under {case / 'log' / 'air-inputs'}/<id>/Mesh/constant/triSurface (a "
                                           "case copied without log/air-inputs has none); remesh")
        batch["uploadedBatch"] = str(archive or tri)
    return {"qualityReceipt": {k: receipt.get(k) for k in ("status", "failedChecks", "mode", "ranks", "meshSha256")},
            "stlBatch": batch, "meshDir": str(mesh_dir)}


#: The module that provides the run session: its session class (:data:`_SESSION_CLASSES`) and the ``ServerError``
#: its calls raise. Here it is the private server's client, and nothing at run time changes it. The evaluator
#: export (tools/dctwin_export) rewrites this one line for a distribution that ships its own session module.
SESSION_MODULE = "agent.scripted_api.server_session"
_SESSION_CLASSES = {"agent.scripted_api.server_session": "ServerSession", "agent.scripted_api.direct_session": "DirectSession"}


def _session_api() -> Tuple[Any, Any]:
    """``(session class, refusal error class)`` of :data:`SESSION_MODULE`, looked up at call time."""
    import importlib
    module = importlib.import_module(SESSION_MODULE)
    return getattr(module, _SESSION_CLASSES[SESSION_MODULE]), module.ServerError


def _submit(session: Any, stage: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """``POST /api/run-mesh``; a refusal (HTTP 4xx/5xx) fails ``stage`` with the server's reason."""
    _, ServerError = _session_api()
    try:
        return session.submit_run(body)
    except ServerError as exc:
        raise PipelineFailure(stage, f"the server refused the {stage} job: {exc}") from exc


class _AirRun:
    """One scripted air run: its result document and the stage steps that fill it."""

    def __init__(self, cfdproj: Path, raw: bytes, case: Path, run_dir: Path, stages: Tuple[str, ...]):
        self.cfdproj, self.raw, self.case, self.run_dir, self.stages = cfdproj, raw, case, run_dir, stages
        self.result: Dict[str, Any] = {}
        self.manifest: Dict[str, Any] = {}
        self.manifest_text = ""
        self.export: Dict[str, Any] = {}
        self.counts: Dict[str, Any] = {}
        self.tools: Dict[str, Any] = {}
        # hybrid only: the liquid run to start and the hybridHeat the solve carries
        self.liquid: Dict[str, Any] = {}
        self.hybrid_project: Dict[str, Any] = {}
        self.hybrid_heat: Optional[Dict[str, Any]] = None
        # AI-initialised CFD: the SCANO checkpoint whose prediction the solve starts from (None: uniform fields)
        self.ai_init: Optional[str] = None
        # the solve's solver engine (runner/gpu_newton.py): "openfoam" or "gpuNewton"
        self.solver_engine: str = "openfoam"
        # the monitoring panel's ticks (split_monitoring) and the export's timestamp, for a re-export
        self.monitoring: Dict[str, Any] = dict(DEFAULT_MONITORING)
        self.stamp: Optional[str] = None

    def is_hybrid(self) -> bool:
        """The editor's hybrid test (run/runSubmit.ts): liquid servers with fan flow, or a v2 fan budget."""
        flags = self.export.get("hybrid") or {}
        return bool(flags.get("hasHybridFanServer")) or flags.get("budgetVersion") == 2

    # -- 1. export ----------------------------------------------------------
    def _compile_air(self, effective: Dict[str, Any], solver_type: Optional[str],
                     mesh_patches: Optional[Sequence[str]], *, prefix: str = "") -> Dict[str, Any]:
        try:
            export = compile_project("air-case", effective, str(self.case / self.cfdproj.name), timestamp=self.stamp,
                                     options=_compile_options(solver_type, self.monitoring, mesh_patches))
        except HeadlessCompileError as exc:
            raise PipelineFailure("export", str(exc)) from exc
        self.export = export
        self.manifest_text = export["cfdrunJson"]
        self.manifest = json.loads(self.manifest_text)
        write_text(self.run_dir / f"{prefix}{export['cfdrunFilename']}", self.manifest_text)
        return export

    def export_stage(self, effective: Dict[str, Any], solver_type: Optional[str],
                     mesh_patches: Optional[Sequence[str]] = None) -> None:
        """``mesh_patches``: the patch names of the mesh this run solves, when it exists before the export."""
        started = time.perf_counter()
        self.stamp = stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        export = self._compile_air(effective, solver_type, mesh_patches)
        stl_index = {name: {"bytes": len(text.encode("utf-8")), "sha256": ident.sha256_bytes(text.encode("utf-8"))}
                     for name, text in sorted(export["stlFiles"].items())}
        write_json(self.run_dir / "stl-index.json", stl_index)
        self.counts = process_counts(self.manifest)
        self.result["stages"]["export"] = {
            "seconds": time.perf_counter() - started, "timestamp": stamp,
            "cfdrunFilename": export["cfdrunFilename"],
            "cfdrunSha256": ident.sha256_bytes(self.manifest_text.encode("utf-8")),
            "manifestErrors": export.get("manifestErrors"), "modelCount": export.get("modelCount"),
            "stlFileCount": len(stl_index), "processes": self.counts,
            "simulationMode": export.get("simulationMode"),
            "preflight": export.get("preflight"), "preflightStops": export.get("preflightStops"),
            "hybrid": export.get("hybrid"),
            "monitoring": self.monitoring_summary(),
        }
        self.result["identity"]["frontend"] = {k: v for k, v in export.get("implementation", {}).items()
                                               if k != "compilerStderrTail"}
        self.result["artifacts"].update(manifest=str(self.run_dir / export["cfdrunFilename"]),
                                        stlIndex=str(self.run_dir / "stl-index.json"))
        if export.get("manifestErrors"):
            raise PipelineFailure("export", f"cfdrun schema rejected the manifest: {export['manifestErrors'][:5]}")

    def monitoring_summary(self) -> Dict[str, Any]:
        """What the exported manifest asks the solver to probe (``simulationConfig.monitoring``)."""
        monitoring = (self.manifest.get("simulationConfig") or {}).get("monitoring") or {}
        return {"request": self.monitoring, "residuals": monitoring.get("residuals"),
                **{group: [row.get("patch") for row in monitoring.get(group) or [] if isinstance(row, dict)]
                   for group in ("serverTemperature", "acuReturnTemperature")}}

    def align_monitors_with_mesh(self, effective: Dict[str, Any], solver_type: Optional[str]) -> None:
        """After a mesh job: re-export if a monitored patch is not in the new mesh.

        The panel offers only the patches the case's mesh has, and the editor's
        solve is a fresh export; a probe on a patch the mesh lacks would stop
        the solver. Nothing changes when every monitored patch is meshed (the
        common case): the solve then sends the mesh job's manifest bytes.
        Otherwise the solve's manifest is ``solve-<name>.cfdrun`` beside the
        mesh job's (``artifacts.solveManifest``).
        """
        names = mesh_patch_names(self.case / "Mesh")
        if names is None:
            return
        present = set(names)
        missing = [patch for patch in monitored_patches(self.manifest) if patch not in present]
        if not missing:
            return
        started = time.perf_counter()
        export = self._compile_air(effective, solver_type, names, prefix="solve-")
        self.result["artifacts"]["solveManifest"] = str(self.run_dir / f"solve-{export['cfdrunFilename']}")
        self.result["stages"]["monitoring"] = {
            "seconds": time.perf_counter() - started, "notInMesh": missing,
            "cfdrunSha256": ident.sha256_bytes(self.manifest_text.encode("utf-8")),
            "monitoring": self.monitoring_summary(),
        }

    # -- input gates (after export, before any server) ------------------------
    def check_inputs(self, *, accept_seed_collisions: bool, allow_serial_solve: bool,
                     max_processes: Optional[int]) -> None:
        export, counts = self.export, self.counts
        if "mesh" in self.stages:
            # The editor runs its pre-flight dialogs before a mesh upload only (run/runSubmit.ts).
            if export.get("preflightStops"):
                raise PipelineFailure("preflight", f"the editor would stop the mesh run: "
                                                   f"{ {k: export['preflight'][k] for k in export['preflightStops']} }")
            seeds = (export.get("preflight") or {}).get("seedCollisions") or []
            if seeds and not accept_seed_collisions:
                raise PipelineFailure("preflight", f"mesh seed point collides with geometry ({seeds[:3]}); the editor "
                                                   "asks whether to continue -- pass accept_seed_collisions=True to "
                                                   "continue")
        if "solve" in self.stages:
            if counts["solveRanks"] <= 1 and not allow_serial_solve:
                raise PipelineFailure("input", f"the solve would run serially (parallelSolve={counts['parallelSolve']}, "
                                               f"solveProcess={counts['solveProcess']}); serial solving is refused "
                                               "(CLAUDE.md debugging rule 7) -- ask for 2 or more solve processes "
                                               "(--solve-processes N, or run_settings simulationConfig parallelSolve/"
                                               "solveProcess), or pass allow_serial_solve=True "
                                               "(--allow-serial-solve) to run it anyway")
        if max_processes is not None:
            mesh_ranks = counts["meshRanks"] if "mesh" in self.stages else 1
            solve_ranks = counts["solveRanks"] if "solve" in self.stages else 1
            if max(mesh_ranks, solve_ranks) > max_processes:
                raise PipelineFailure("input", f"project asks for {max(mesh_ranks, solve_ranks)} MPI ranks (mesh "
                                               f"{mesh_ranks}, solve {solve_ranks}) above max_processes="
                                               f"{max_processes}; pass run_settings to lower meshProcess/solveProcess")
        if self.stages == ("solve",):
            started = time.perf_counter()
            reuse = check_mesh_reuse(self.case, self.manifest, hybrid=self.is_hybrid())
            reuse["seconds"] = time.perf_counter() - started
            self.result["stages"]["meshReuse"] = reuse
            self.result["artifacts"]["meshDir"] = reuse["meshDir"]

    # -- hybrid inputs (after export, before any server) ----------------------------------------
    def hybrid_inputs(self, effective: Dict[str, Any], solver_type: Optional[str], *,
                      liquid_device: Optional[str] = "cpu", liquid_threads: Optional[int] = None) -> None:
        """The editor's hybrid gates, the liquid run to start and the project ``hybridHeat`` carries.

        All three compiles (this one, the liquid run and the air case) see the
        same document, so the server's content-hash checks agree.
        """
        started = time.perf_counter()
        path = str(self.case / self.cfdproj.name)
        options = _compile_options(solver_type)
        try:
            hybrid = compile_project("hybrid-project", effective, path, options=options)
        except HeadlessCompileError as exc:
            raise PipelineFailure("export", f"hybrid project: {exc}") from exc
        if hybrid.get("hasHybridFanServer") and hybrid.get("budgetVersion") != 2:
            raise PipelineFailure("input", "Declare a fan hybrid IT budget in the liquid experiment panel, save, then "
                                           "complete its liquid experiment (run/runSubmit.ts): the project has liquid "
                                           "servers with fan flow but no runDefaults.hybridHeatBudget version 2")
        if hybrid.get("budgetVersion") == 2 and not hybrid.get("inheritResults"):
            raise PipelineFailure("input", "Complete a liquid experiment from this saved hybrid project before the air "
                                           "run (run/runSubmit.ts): runDefaults.liquid.inheritResults is not true, so "
                                           "the editor hands no liquid heat to the air run -- set it in the project")
        try:
            liquid = compile_project("liquidrun", effective, path, options=options)
        except HeadlessCompileError as exc:
            raise PipelineFailure("export", f"liquid export refused the project: {exc}") from exc
        # Where the liquid session runs is an execution choice, not physics: the server's hash checks bind
        # the run's source_project, never its execution block. CPU unless asked (scripted-api.md: CUDA).
        overrides: Dict[str, Any] = {}
        execution = liquid["liquidrun"].setdefault("execution", {})
        if liquid_device is not None and execution.get("device") != liquid_device:
            execution["device"] = overrides["device"] = liquid_device
        if liquid_threads is not None and execution.get("max_threads") != int(liquid_threads):
            execution["max_threads"] = overrides["max_threads"] = int(liquid_threads)
        self.hybrid_project = hybrid
        self.liquid = liquid
        write_json(self.run_dir / "input.liquidrun", liquid["liquidrun"])
        self.result["artifacts"]["liquidrun"] = str(self.run_dir / "input.liquidrun")
        run = liquid["liquidrun"]
        self.result["stages"]["hybridExport"] = {
            "seconds": time.perf_counter() - started, "projectContentHash": hybrid.get("contentHash"),
            "budgetVersion": hybrid.get("budgetVersion"), "hasHybridFanServer": hybrid.get("hasHybridFanServer"),
            "inheritResults": hybrid.get("inheritResults"),
            "liquidrunSha256": ident.canonical_sha256(run),
            "liquidVersion": run.get("schemaVersion") or run.get("version"), "liquidAnalysis": run.get("analysis"),
            "liquidSamples": (run.get("time") or {}).get("N_t"), "liquidDtS": (run.get("time") or {}).get("dt_s"),
            "simulationMode": liquid.get("simulationMode"), "liquidOverrides": overrides,
        }

    # -- 1b. liquid session (hybrid, same server, before the air jobs) ----------------------------
    def liquid_stage(self, session: Any, *, timeout: float, sample_index: Optional[int]) -> None:
        _, ServerError = _session_api()
        try:
            self._liquid_session(session, timeout=timeout, sample_index=sample_index)
        except ServerError as exc:
            raise PipelineFailure("liquid", f"the server refused the liquid session: {exc}") from exc

    def _liquid_session(self, session: Any, *, timeout: float, sample_index: Optional[int]) -> None:
        from agent.scripted_api.liquid import network_at
        started = time.perf_counter()
        body = {"liquidrun": self.liquid["liquidrun"], "controllers": self.liquid.get("controllers") or []}
        created = session.call("POST", "/api/liquid/runs", body, timeout=900, expect=(201,))
        run_id = created["run_id"]
        stage: Dict[str, Any] = {"runId": run_id, "runDir": str(self.case / "Liquid" / "log" / f"run-{run_id}"),
                                 "created": created.get("status")}
        self.result["stages"]["liquid"] = stage
        self.result["artifacts"]["liquidRunDir"] = stage["runDir"]
        status = created
        if created.get("status") == "ready":
            status = session.call("POST", f"/api/liquid/runs/{run_id}/start", {}, timeout=120)
        deadline = time.monotonic() + timeout
        while status.get("status") in ("ready", "running"):
            if time.monotonic() > deadline:
                try:
                    session.call("POST", f"/api/liquid/runs/{run_id}/cancel", {}, timeout=120)
                except Exception as exc:  # cancel is best effort; the failure below says why
                    stage["cancelError"] = str(exc)
                stage.update(status="timedOut", seconds=time.perf_counter() - started)
                raise PipelineFailure("liquid", f"liquid run {run_id} did not complete within {timeout:.0f}s "
                                                "and was cancelled")
            time.sleep(LIQUID_POLL_SECONDS)
            status = session.call("GET", f"/api/liquid/runs/{run_id}", timeout=120)
        frame = status.get("frame") or {}
        stage.update(status=status.get("status"), error=status.get("error"), seconds=time.perf_counter() - started)
        if status.get("status") != "completed":
            reason = (status.get("error") or {}).get("message") if isinstance(status.get("error"), dict) else None
            raise PipelineFailure("liquid", f"liquid run {run_id} ended {status.get('status')}"
                                            + (f": {reason}" if reason else ""))
        latest = int(frame.get("seq", 0))
        index = latest if sample_index is None else int(sample_index)
        stage.update(samples=latest + 1, latestSample=latest)
        if not 0 <= index <= latest:
            raise PipelineFailure("liquid", f"sample_index {index} is outside the run's accepted samples 0..{latest}")
        liquid_result = session.call("GET", f"/api/liquid/runs/{run_id}/result", timeout=600)
        network = network_at(liquid_result, index)
        stage.update(sampleIndex=index, timeS=network["timeS"])
        self.result["metrics"]["liquid"] = {"runId": run_id, **network}
        self.hybrid_heat = {"project": self.hybrid_project["project"], "liquidRunId": run_id, "sampleIndex": index}

    def body(self) -> Dict[str, Any]:
        return {"cfdrunJson": self.manifest_text, "cfdrunFilename": self.export["cfdrunFilename"]}

    # -- 2. mesh ------------------------------------------------------------
    def mesh_stage(self, session: Any, *, timeout: float, require_check_mesh: bool) -> None:
        started = time.perf_counter()
        # As run/session.ts: the export time counts against the mesh budget.
        reply = _submit(session, "mesh", {**self.body(), "stlFiles": _stl_body(self.export["stlFiles"]),
                                          "meshOnly": True, "solveOnly": False,
                                          "meshPreparationElapsedSeconds": self.result["stages"]["export"]["seconds"]})
        status = session.wait_job(reply["jobId"], timeout=timeout)
        lines, _ = session.job_log(reply["jobId"])
        write_text(self.run_dir / "mesh-job.log", "\n".join(lines) + "\n")
        self.result["artifacts"]["meshJobLog"] = str(self.run_dir / "mesh-job.log")
        scan = _scan_log(lines)
        engine = self.counts.get("meshEngine") or "dcCart"
        stage = {
            "seconds": time.perf_counter() - started, "jobId": reply["jobId"],
            "cfdrunSha256": reply.get("cfdrunSha256"), "returncode": status.get("returncode"),
            "timedOut": status.get("timedOut"), "checkMeshOk": status.get("checkMeshOk"),
            "failedChecks": status.get("failedChecks"), "meshWarnings": status.get("meshWarnings"),
            "cells": scan["cells"], "meshEngine": engine, "mesher": scan["binaries"].get("mesher"),
            "applications": scan["applications"], "log": str(self.run_dir / "mesh-job.log"),
        }
        self.result["stages"]["mesh"] = stage
        self.result["artifacts"]["meshDir"] = str(self.case / "Mesh")
        receipt = _read_json(self.case / "mesh-quality-receipt.json")
        if isinstance(receipt, dict):
            stage["qualityReceipt"] = {k: receipt.get(k) for k in ("status", "failedChecks", "mode", "ranks",
                                                                   "meshSha256")}
        self.tools["mesher"] = ident.binary_identity(scan["binaries"].get("mesher") or mesher_binary_name(engine))
        if status.get("timedOut"):
            raise PipelineFailure("mesh", f"mesh job exceeded the API's hard limit of {timeout:.0f}s and was aborted")
        if status.get("returncode") != 0:
            raise PipelineFailure("mesh", f"mesh job exited {status.get('returncode')}; see mesh-job.log tail: "
                                          + _job_tail(lines))
        if require_check_mesh and status.get("checkMeshOk") is not True:
            raise PipelineFailure("mesh", f"final checkMesh did not pass (failedChecks={status.get('failedChecks')}, "
                                          f"warnings={status.get('meshWarnings')})")

    # -- 3. solve -----------------------------------------------------------
    def solve_stage(self, session: Any, *, timeout: float) -> Path:
        started = time.perf_counter()
        # runRequestBody('solve'): the same manifest bytes, no STL upload (+ hybridHeat for a hybrid project).
        body = {**self.body(), "stlFiles": {}, "meshOnly": False, "solveOnly": True}
        if self.hybrid_heat is not None:
            body["hybridHeat"] = self.hybrid_heat
        if self.ai_init:
            # the editor's 初始化 = AI 推理初始化 (run/solveInit.ts): the server runs the AI solve
            # of this mesh first and starts the solver from its U, p_rgh and T
            body["aiInit"] = {"model": self.ai_init}
        if self.solver_engine != "openfoam":
            body["solverEngine"] = self.solver_engine
        reply = _submit(session, "solve", body)
        status = session.wait_job(reply["jobId"], timeout=timeout)
        lines, _ = session.job_log(reply["jobId"])
        write_text(self.run_dir / "solve-job.log", "\n".join(lines) + "\n")
        self.result["artifacts"]["solveJobLog"] = str(self.run_dir / "solve-job.log")
        scan = _scan_log(lines)
        assessment = status.get("solveAssessment") or {}
        solve_dir = assessment.get("runDir") or scan["runDir"]
        self.result["stages"]["solve"] = {
            "seconds": time.perf_counter() - started, "jobId": reply["jobId"],
            "cfdrunSha256": reply.get("cfdrunSha256"), "returncode": status.get("returncode"),
            "timedOut": status.get("timedOut"), "solveAssessment": assessment or None,
            "runDir": solve_dir, "solver": scan["binaries"].get("solver"), "ranks": self.counts.get("solveRanks"),
            "solverEngine": self.solver_engine,
            "applications": scan["applications"], "log": str(self.run_dir / "solve-job.log"),
        }
        self.tools["solver"] = ident.binary_identity(scan["binaries"].get("solver"))
        if self.solver_engine != "openfoam":
            from backend.aircooling.runner import gpu_newton
            self.tools["solverEngine"] = gpu_newton.identity()
        self.tools["repoBuiltBinaries"] = {path: ident.binary_identity(path)["sha256"]
                                           for path in dict.fromkeys(scan.get("allBinaries", []))}
        self.tools["openfoam"] = {"version": scan["openfoamVersion"], "build": scan["openfoamBuild"]}
        self.result["artifacts"]["solveRunDir"] = solve_dir
        if status.get("timedOut"):
            raise PipelineFailure("solve", f"solve job exceeded {timeout:.0f}s and was aborted")
        if status.get("returncode") != 0:
            raise PipelineFailure("solve", f"solve job exited {status.get('returncode')}; solve-job.log tail: "
                                           + _job_tail(lines))
        if not solve_dir or not Path(solve_dir).is_dir():
            raise PipelineFailure("solve", "solve finished but published no run directory")
        if self.hybrid_heat is not None:
            ledger_path = self.case / "model" / "heat-ledger.json"
            ledger = _read_json(ledger_path)
            if not isinstance(ledger, dict):
                raise PipelineFailure("solve", f"the hybrid solve left no readable {ledger_path}")
            sent = (self.hybrid_heat["liquidRunId"], self.hybrid_heat["sampleIndex"])
            if (ledger.get("liquid_run_id"), ledger.get("sample_index")) != sent:
                raise PipelineFailure("solve", f"{ledger_path} is for liquid run {ledger.get('liquid_run_id')} sample "
                                               f"{ledger.get('sample_index')}, not the {sent[0]} sample {sent[1]} "
                                               "this solve sent")
            write_json(self.run_dir / "heat-ledger.json", ledger)
            self.result["metrics"]["heatLedger"] = ledger
            self.result["artifacts"]["heatLedger"] = str(self.run_dir / "heat-ledger.json")
        return Path(solve_dir)

    def executed_manifest(self, solve_path: Path) -> Dict[str, Any]:
        """The manifest the solver ran: for a hybrid run, the server's compile carrying the liquid heat."""
        if self.hybrid_heat is None:
            return self.manifest
        for candidate in (solve_path / "model" / self.export["cfdrunFilename"],
                          self.case / "model" / self.export["cfdrunFilename"]):
            executed = _read_json(candidate)
            if isinstance(executed, dict):
                return executed
        raise PipelineFailure("metrics", "the hybrid solve's executed manifest was not found")

    # -- 4. metrics ---------------------------------------------------------
    def metrics_stage(self, solve_path: Path, effective: Dict[str, Any]) -> None:
        started = time.perf_counter()
        metrics = self.result["metrics"]
        convergence = _read_json(solve_path / "convergence.json")
        execution = _read_json(solve_path / "air-run-execution-receipt.json")
        metrics["convergence"] = _convergence_summary(convergence)
        metrics["sensors"] = {path.name: _read_json(path) for path in sorted((solve_path / "sensorResults").glob("*.json"))}
        metrics["executionReceipt"] = ({k: execution.get(k) for k in ("format", "status")}
                                       if isinstance(execution, dict) else None)
        try:
            from agent.scripted_api.air_metrics import compute_air_metrics
            metrics["fields"] = compute_air_metrics(solve_path, self.executed_manifest(solve_path), effective)
        except Exception as exc:  # metrics are read-back; their failure is reported, not hidden
            metrics["fields"] = {"error": f"{type(exc).__name__}: {exc}"}
            raise PipelineFailure("metrics", f"field read-back failed: {exc}") from exc
        finally:
            self.result["stages"]["metrics"] = {"seconds": time.perf_counter() - started}


def run_air_case(cfdproj: Path | str, *, case_dir: Path | str | None = None,
                 run_settings: Optional[Dict[str, Any]] = None, solve: bool = True,
                 stages: Optional[Sequence[str]] = None, solver_type: Optional[str] = None,
                 require_check_mesh: bool = False, accept_seed_collisions: bool = False,
                 allow_serial_solve: bool = False, max_processes: Optional[int] = None,
                 mesh_timeout: float = 1800.0, solve_timeout: float = 4 * 3600.0,
                 label: Optional[str] = None, app: str = "api",
                 raise_on_failure: bool = False, sample_index: Optional[int] = None,
                 liquid_timeout: float = 3600.0, liquid_device: Optional[str] = "cpu",
                 liquid_threads: Optional[int] = None, ai_init: Optional[str] = None,
                 solver_engine: Optional[str] = None) -> Dict[str, Any]:
    """Mesh and/or solve one ``.cfdproj`` through a private server.

    ``solver_engine``: ``"openfoam"`` (the default, also ``None``) or
    ``"gpuNewton"``, this project's GPU Newton-Krylov solver for steady solves
    (``runner/gpu_newton.py``; the private server is then started with
    ``--gpu``, on the cards of ``DCDTUNI_GPU_SOLVER_GPU`` when that is set). Export,
    mesh, run directory, receipts, convergence verdict and metrics are the
    same for both; ``stages.solve.solverEngine`` records which one solved.

    ``ai_init``: a SCANO checkpoint id; the steady solve then starts from that
    model's prediction on the same mesh and boundary conditions (the editor's
    AI 推理初始化; docs/developer/scano-ai.md "AI-initialised CFD") instead of
    uniform fields.

    ``stages``: ``("mesh", "solve")`` (default; ``solve=False`` means
    ``("mesh",)``), ``("mesh",)`` or ``("solve",)`` -- the last reuses the
    case's mesh, see :func:`check_mesh_reuse`. ``solver_type`` (``steady`` or
    ``transient``, also accepted as ``run_settings`` ``simulationConfig.solverType``)
    goes through the editor's ``setSimulationMode``. ``mesh_timeout`` is the API's
    own hard limit on the mesh job (the runner's mesh budget,
    ``meshConfig.meshTimeLimitSeconds``, is a soft target that never stops a mesh).

    A hybrid project's solve takes the hybrid path of :func:`run_hybrid_case`
    (``sample_index``, ``liquid_timeout``, ``liquid_device``, ``liquid_threads``);
    its mesh needs no liquid session.

    Returns the ``result.json`` dict. ``status`` is ``succeeded`` when every
    requested stage exited 0; a failed final checkMesh is reported in the mesh
    stage and stops the run only with ``require_check_mesh`` (default False:
    mesh quality is reported, never required);
    otherwise ``failed`` with ``failure = {stage, reason}``. Solver convergence
    is reported (``metrics.convergence.verdict``), never folded into status.
    """
    return _run_case(cfdproj, require_hybrid=False, case_dir=case_dir, run_settings=run_settings, solve=solve,
                     stages=stages, solver_type=solver_type, require_check_mesh=require_check_mesh,
                     accept_seed_collisions=accept_seed_collisions, allow_serial_solve=allow_serial_solve,
                     max_processes=max_processes, mesh_timeout=mesh_timeout, solve_timeout=solve_timeout,
                     label=label, app=app, raise_on_failure=raise_on_failure, sample_index=sample_index,
                     liquid_timeout=liquid_timeout, liquid_device=liquid_device, liquid_threads=liquid_threads,
                     ai_init=ai_init, solver_engine=solver_engine)


def run_hybrid_case(cfdproj: Path | str, *, sample_index: Optional[int] = None, liquid_timeout: float = 3600.0,
                    liquid_device: Optional[str] = "cpu", liquid_threads: Optional[int] = None,
                    case_dir: Path | str | None = None, run_settings: Optional[Dict[str, Any]] = None,
                    stages: Optional[Sequence[str]] = None, solver_type: Optional[str] = None,
                    require_check_mesh: bool = False, accept_seed_collisions: bool = False,
                    allow_serial_solve: bool = False, max_processes: Optional[int] = None,
                    mesh_timeout: float = 1800.0, solve_timeout: float = 4 * 3600.0,
                    label: Optional[str] = None, app: str = "api", raise_on_failure: bool = False,
                    solver_engine: Optional[str] = None) -> Dict[str, Any]:
    """Hybrid air/liquid run of one ``.cfdproj``: liquid session, mesh, solve with its heat, one server.

    See the module docstring. ``sample_index`` picks the accepted liquid
    sample whose heat goes to the air side (default: the latest, as the
    editor sends); ``liquid_timeout`` bounds the liquid session (it is
    cancelled past it); ``liquid_device`` (default ``cpu``; ``None`` keeps the
    project's) and ``liquid_threads`` set the posted run's ``execution``.
    ``stages`` must include ``solve`` (``("solve",)``
    reuses the case's accepted mesh). Refused at input unless the project is
    hybrid and passes the editor's gates. ``result["kind"]`` is ``hybrid``;
    ``metrics.liquid`` is the liquid network at the handed-over sample and
    ``metrics.heatLedger`` the server's heat ledger. ``solver_engine``: as
    :func:`run_air_case`.
    """
    chosen = resolve_stages(stages)
    if "solve" not in chosen:
        raise ValueError("a hybrid run hands liquid heat to an air solve; stages must include 'solve' "
                         "(mesh alone needs no liquid session: run_air_case(stages=('mesh',)))")
    return _run_case(cfdproj, require_hybrid=True, case_dir=case_dir, run_settings=run_settings, solve=True,
                     stages=chosen, solver_type=solver_type, require_check_mesh=require_check_mesh,
                     accept_seed_collisions=accept_seed_collisions, allow_serial_solve=allow_serial_solve,
                     max_processes=max_processes, mesh_timeout=mesh_timeout, solve_timeout=solve_timeout,
                     label=label, app=app, raise_on_failure=raise_on_failure, sample_index=sample_index,
                     liquid_timeout=liquid_timeout, liquid_device=liquid_device, liquid_threads=liquid_threads,
                     solver_engine=solver_engine)


def _run_case(cfdproj: Path | str, *, require_hybrid: bool, case_dir: Path | str | None,
              run_settings: Optional[Dict[str, Any]], solve: bool, stages: Optional[Sequence[str]],
              solver_type: Optional[str], require_check_mesh: bool, accept_seed_collisions: bool,
              allow_serial_solve: bool, max_processes: Optional[int], mesh_timeout: float, solve_timeout: float,
              label: Optional[str], app: str, raise_on_failure: bool, sample_index: Optional[int],
              liquid_timeout: float, liquid_device: Optional[str] = "cpu",
              liquid_threads: Optional[int] = None, ai_init: Optional[str] = None,
              solver_engine: Optional[str] = None) -> Dict[str, Any]:
    chosen = resolve_stages(stages, solve=solve)
    from backend.aircooling.runner import gpu_newton
    engine = gpu_newton.resolve_engine(solver_engine)
    if engine != gpu_newton.OPENFOAM:
        if ai_init is not None:
            raise ValueError("solver_engine 'gpuNewton' starts from the uniform initial field; ai_init is for the "
                             "OpenFOAM solver")
        if "solve" in chosen:
            gpu_newton.configuration()
    if ai_init is not None and (require_hybrid or not isinstance(ai_init, str) or not ai_init):
        raise ValueError("ai_init is a SCANO checkpoint id, for an air (not hybrid) solve")
    if solver_type is not None and solver_type not in SOLVER_TYPES:
        raise ValueError(f"solver_type must be one of {SOLVER_TYPES}, got {solver_type!r}")
    if sample_index is not None and (isinstance(sample_index, bool) or not isinstance(sample_index, int)):
        raise ValueError(f"sample_index must be an integer, got {sample_index!r}")
    started_wall, started = ident.now_sgt(), time.perf_counter()
    cfdproj = Path(cfdproj).resolve()
    raw, project = load_project(cfdproj)
    case = Path(case_dir).resolve() if case_dir else cfdproj.parent
    case.mkdir(parents=True, exist_ok=True)
    kind = "hybrid" if require_hybrid else "air"
    run_dir = create_run_dir(case_api_root(case), kind, label)
    run = _AirRun(cfdproj, raw, case, run_dir, chosen)
    run.ai_init = ai_init
    run.solver_engine = engine
    result = run.result
    result.update({
        "format": RESULT_FORMAT, "kind": kind, "status": "failed", "failure": None,
        "runDir": str(run_dir), "caseDir": str(case), "startedAt": ident.iso(started_wall),
        "inputs": {"cfdproj": str(cfdproj), "cfdprojSha256": ident.sha256_bytes(raw),
                   "runSettings": run_settings or {}, "stages": list(chosen), "solve": "solve" in chosen,
                   "solverType": solver_type, "requireCheckMesh": require_check_mesh,
                   "allowSerialSolve": allow_serial_solve, "maxProcesses": max_processes,
                   "meshTimeoutSeconds": mesh_timeout, "solveTimeoutSeconds": solve_timeout, "app": app,
                   "hybrid": require_hybrid, "sampleIndex": sample_index,
                   "liquidTimeoutSeconds": liquid_timeout, "liquidDevice": liquid_device,
                   "liquidThreads": liquid_threads, "aiInit": ai_init, "solverEngine": engine},
        "identity": {"repository": ident.repository_identity(), "host": ident.host_identity()},
        "stages": {}, "metrics": {}, "artifacts": {"resultJson": str(run_dir / "result.json")},
    })
    write_text(run_dir / "input.cfdproj", raw.decode("utf-8"))
    session = None
    auth_parent = None
    try:
        # -- case placement ---------------------------------------------------
        if case != cfdproj.parent:
            placed = case / cfdproj.name
            if placed.exists() and placed.read_bytes() != raw:
                raise PipelineFailure("input", f"{placed} exists with different content; refusing to overwrite")
            if not placed.exists():
                placed.write_bytes(raw)
        try:
            settings, effective_solver = split_solver_type(run_settings, solver_type)
            settings, run.monitoring = split_monitoring(settings)
            effective = apply_run_settings(project, settings)
        except ValueError as exc:
            raise PipelineFailure("input", str(exc)) from exc
        result["inputs"]["solverType"] = effective_solver
        result["inputs"]["monitoring"] = run.monitoring
        result["inputs"]["effectiveProjectSha256"] = ident.canonical_sha256(effective)

        # solve-only solves the case's mesh: the panel offers what that mesh has
        run.export_stage(effective, effective_solver,
                         mesh_patches=mesh_patch_names(case / "Mesh") if chosen == ("solve",) else None)
        hybrid = run.is_hybrid()
        if require_hybrid and not hybrid:
            raise PipelineFailure("input", "not a hybrid project: no liquid server carries fan flow and "
                                           "runDefaults.hybridHeatBudget is not version 2 -- use run_air_case "
                                           "(air) or run_liquid_case (liquid)")
        run.check_inputs(accept_seed_collisions=accept_seed_collisions, allow_serial_solve=allow_serial_solve,
                         max_processes=max_processes)
        if hybrid and "solve" in chosen:
            result["kind"] = "hybrid"
            run.hybrid_inputs(effective, effective_solver, liquid_device=liquid_device,
                              liquid_threads=liquid_threads)

        # -- server -----------------------------------------------------------
        auth_parent = Path(tempfile.mkdtemp(prefix=".server-", dir=run_dir))
        ServerSession, _ = _session_api()
        # the GPU air solver is offered only by a server started with --gpu; its cards are those of
        # DCDTUNI_GPU_SOLVER_GPU (unset: any card)
        session = ServerSession(case, auth_parent, app=app,
                                gpu=os.environ.get("DCDTUNI_GPU_SOLVER_GPU", "")
                                if engine != gpu_newton.OPENFOAM and "solve" in chosen else None)
        session.log_path = run_dir / "server.log"
        session.start()
        result["stages"]["server"] = {"port": session.port, "wsPort": session.port + 1, "app": app,
                                      "startupSeconds": session.startup_seconds,
                                      "command": [c if not c.startswith(str(auth_parent)) else "<private auth dir>"
                                                  for c in session.command]}
        if run.liquid:
            run.liquid_stage(session, timeout=liquid_timeout, sample_index=sample_index)
        if "mesh" in chosen:
            run.mesh_stage(session, timeout=mesh_timeout, require_check_mesh=require_check_mesh)
        if "solve" in chosen:
            if "mesh" in chosen:
                run.align_monitors_with_mesh(effective, effective_solver)
            solve_path = run.solve_stage(session, timeout=solve_timeout)
            run.metrics_stage(solve_path, effective)
        result["identity"]["tools"] = run.tools
        result["status"] = "succeeded"
    except PipelineFailure as failure:
        result["failure"] = {"stage": failure.stage, "reason": failure.reason}
        result["identity"].setdefault("tools", run.tools)
    except Exception as exc:
        result["failure"] = {"stage": "internal", "reason": f"{type(exc).__name__}: {exc}",
                             "traceback": traceback.format_exc()[-6000:]}
        result["identity"].setdefault("tools", run.tools)
    finally:
        if session is not None:
            session.stop()
            result["stages"].setdefault("server", {})["stop"] = session.stop_report
        if auth_parent is not None:
            shutil.rmtree(auth_parent, ignore_errors=True)  # this run's private credentials only
        if (run_dir / "server.log").is_file():
            result["artifacts"]["serverLog"] = str(run_dir / "server.log")
        result["identity"]["python"] = ident.python_identity()
        result["finishedAt"] = ident.iso(ident.now_sgt())
        result["elapsedSeconds"] = time.perf_counter() - started
        write_json(run_dir / "result.json", result)
    if raise_on_failure and result["status"] != "succeeded":
        raise PipelineFailure(result["failure"]["stage"], result["failure"]["reason"])
    return result
