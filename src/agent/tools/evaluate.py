"""Evaluation tools: every physics call goes through ``agent.scripted_api``; each returns the raw result and a view.

=====================  =========================================================
``admit_air``          headless export + pre-flight + blockers, no server
                       (:func:`agent.scripted_api.export_air_case`)
``mesh_air``           ``run_air_case(stages=("mesh",))`` -- mesh only
``solve_air``          ``run_air_case(stages=("solve",))`` -- reuses the accepted mesh
``evaluate_air``       ``run_air_case(stages=("mesh", "solve"))``
``evaluate_hybrid``    ``run_hybrid_case`` (liquid session, mesh, solve with its heat)
``evaluate_liquid``    ``run_liquid_case`` (DAE steady state + implicit-adjoint
                       gradients, or the transient march)
``liquid_steady``      ``solve_liquid_steady`` in process on a ``.liquidrun`` object:
                       the same steady answer with no run directory (inner loops)
``liquid_gradient``    ``{parameter: {edge/node id: d objective / d theta}}`` of a liquid run
``evaluate_many``      ``run_batch``
``identity``           the API's repository/host/python identity + the agent version
=====================  =========================================================

Each ``run_*`` result is the API's ``result.json`` dictionary, returned as a
:mod:`~agent.tools.results` view (``view.raw`` is that dictionary; the API has
already written it to ``<case>/log/api/<stamp>__<kind>/``). The agent keeps the
API's guards: process counts are explicit and positive, a solve never asks
for ``allow_serial_solve``, ``max_processes`` is always passed, every call
uses a private server, and the liquid side runs on ``device="cpu"`` unless the
caller says otherwise.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union

import agent.scripted_api as api
from agent.scripted_api import air as api_air
from agent.scripted_api import identity as api_identity
from agent.scripted_api.headless import HeadlessCompileError

from agent.tools.project import project_identity
from agent.tools.results import (AdmissionView, AirView, HybridView, LiquidView, ResultView, view)

PathLike = Union[str, os.PathLike]
ADMISSION_FORMAT = "agent.tools.admission/v1"
LIQUID_STEADY_FORMAT = "agent.tools.liquid-steady/v1"
DEFAULT_GRADIENT_PARAMETERS = api.DEFAULT_GRADIENT_PARAMETERS
OBJECTIVES = api.OBJECTIVES

__all__ = ["admit_air", "mesh_air", "solve_air", "evaluate_air", "evaluate_hybrid", "evaluate_liquid",
           "liquid_steady", "liquid_gradient", "evaluate_many", "evaluate_dcrans_l0", "evaluate_dcrans_l1",
           "identity", "project_identity"]


def _count(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def _merge(base: Optional[Dict[str, Any]], extra: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not base:
        return extra
    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in base.items()}
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _settings(run_settings: Optional[Dict[str, Any]], **options: Any) -> Optional[Dict[str, Any]]:
    return _merge(run_settings, api_air.run_settings_from_options(**options))


def admit_air(cfdproj: PathLike, *, run_settings: Optional[Dict[str, Any]] = None,
              solver_type: Optional[str] = None, keep_export: bool = False) -> AdmissionView:
    """Fidelity ADMISSION: would this project start an air run as exported? No server, no mesh.

    ``status`` is ``succeeded`` when the export ran (``failed`` with
    ``failure.stage == "export"`` when the editor's export refused it);
    ``admitted`` is ``True`` when it ran with no blocker
    (``agent.scripted_api.air.export_blockers``: schema errors, pre-flight stops, a
    hybrid project needing a liquid session). STL files are reported by hash
    and size; ``keep_export=True`` also keeps the export itself under
    ``export`` (large, and not written anywhere).
    """
    started = time.perf_counter()
    started_wall = api_identity.now_sgt()
    source = project_identity(cfdproj)
    effective = api_air.apply_run_settings(api_air.load_project(Path(cfdproj))[1], run_settings)
    raw: Dict[str, Any] = {
        "format": ADMISSION_FORMAT, "kind": "admission", "status": "failed", "failure": None, "admitted": False,
        "startedAt": api_identity.iso(started_wall),
        "inputs": {"cfdproj": source["path"], "cfdprojSha256": source["sha256"],
                   "effectiveProjectSha256": api_identity.canonical_sha256(effective),
                   "runSettings": run_settings or {}, "solverType": solver_type},
        "identity": {"repository": api_identity.repository_identity(), "host": api_identity.host_identity(),
                     "python": api_identity.python_identity()},
        "blockers": [], "stages": {},
    }
    try:
        export = api.export_air_case(cfdproj, run_settings=run_settings, solver_type=solver_type)
    except HeadlessCompileError as exc:
        raw["failure"] = {"stage": "export", "reason": str(exc)}
        raw["stages"]["export"] = {"seconds": time.perf_counter() - started}
        raw["elapsedSeconds"] = time.perf_counter() - started
        return AdmissionView(raw)
    blockers = api_air.export_blockers(export)
    stl = export.get("stlFiles") or {}
    manifest = export.get("cfdrunJson")
    # The export returns the manifest TEXT; a run's stages.export.cfdrunSha256 hashes those bytes.
    manifest_sha = (api_identity.sha256_bytes(manifest.encode("utf-8")) if isinstance(manifest, str)
                    else api_identity.canonical_sha256(manifest))
    raw.update(status="succeeded", admitted=not blockers, blockers=blockers,
               cfdrunFilename=export.get("cfdrunFilename"), cfdrunSha256=manifest_sha,
               manifestErrors=export.get("manifestErrors") or [], preflight=export.get("preflight") or {},
               preflightStops=export.get("preflightStops") or [], hybrid=export.get("hybrid"),
               simulationMode=export.get("simulationMode"),
               stlFiles={name: {"sha256": api_identity.sha256_bytes(text.encode("utf-8")),
                                "bytes": len(text.encode("utf-8"))} for name, text in sorted(stl.items())})
    raw["stages"]["export"] = {"seconds": time.perf_counter() - started}
    raw["elapsedSeconds"] = time.perf_counter() - started
    if keep_export:
        raw["export"] = export
    return AdmissionView(raw)


def mesh_air(cfdproj: PathLike, *, case_dir: PathLike, mesh_processes: int, max_processes: int,
             mesh_engine: Optional[str] = None, mesh_timeout: float = 1800.0, label: Optional[str] = None,
             run_settings: Optional[Dict[str, Any]] = None) -> AirView:
    """Fidelity AIR_MESH: mesh only, in ``case_dir`` (each candidate owns its case directory)."""
    settings = _settings(run_settings, mesh_engine=mesh_engine,
                         mesh_processes=_count("mesh_processes", mesh_processes))
    raw = api.run_air_case(cfdproj, case_dir=case_dir, run_settings=settings, stages=("mesh",),
                           max_processes=_count("max_processes", max_processes), mesh_timeout=mesh_timeout,
                           label=label)
    return view(raw)


def solve_air(cfdproj: PathLike, *, case_dir: PathLike, solve_processes: int, max_processes: int,
              end_time: Optional[float] = None, solver_type: Optional[str] = None, solve_timeout: float = 14400.0,
              label: Optional[str] = None, run_settings: Optional[Dict[str, Any]] = None) -> AirView:
    """Solve on the case's accepted mesh (the API checks the quality receipt and the STL batch)."""
    settings = _settings(run_settings, solve_processes=_count("solve_processes", solve_processes), end_time=end_time)
    raw = api.run_air_case(cfdproj, case_dir=case_dir, run_settings=settings, stages=("solve",),
                           solver_type=solver_type, max_processes=_count("max_processes", max_processes),
                           solve_timeout=solve_timeout, label=label)
    return view(raw)


def evaluate_air(cfdproj: PathLike, *, case_dir: PathLike, mesh_processes: int, solve_processes: int,
                 max_processes: int, end_time: Optional[float] = None, mesh_engine: Optional[str] = None,
                 solver_type: Optional[str] = None, mesh_timeout: float = 1800.0, solve_timeout: float = 14400.0,
                 label: Optional[str] = None, run_settings: Optional[Dict[str, Any]] = None) -> AirView:
    """Fidelity AIR_FULL: export, mesh, solve and field metrics through one private server."""
    settings = _settings(run_settings, mesh_engine=mesh_engine,
                         mesh_processes=_count("mesh_processes", mesh_processes),
                         solve_processes=_count("solve_processes", solve_processes), end_time=end_time)
    raw = api.run_air_case(cfdproj, case_dir=case_dir, run_settings=settings, stages=("mesh", "solve"),
                           solver_type=solver_type, max_processes=_count("max_processes", max_processes),
                           mesh_timeout=mesh_timeout, solve_timeout=solve_timeout, label=label,
                           liquid_device="cpu")
    return view(raw)


def evaluate_hybrid(cfdproj: PathLike, *, case_dir: PathLike, mesh_processes: int, solve_processes: int,
                    max_processes: int, sample_index: Optional[int] = None, liquid_threads: Optional[int] = None,
                    stages: Sequence[str] = ("mesh", "solve"), end_time: Optional[float] = None,
                    solver_type: Optional[str] = None, liquid_timeout: float = 3600.0,
                    mesh_timeout: float = 1800.0, solve_timeout: float = 14400.0, label: Optional[str] = None,
                    run_settings: Optional[Dict[str, Any]] = None) -> HybridView:
    """Fidelity HYBRID: liquid session on CPU, then mesh and solve with its heat (``metrics.heatLedger``)."""
    settings = _settings(run_settings, mesh_processes=_count("mesh_processes", mesh_processes),
                         solve_processes=_count("solve_processes", solve_processes), end_time=end_time)
    raw = api.run_hybrid_case(cfdproj, case_dir=case_dir, run_settings=settings, stages=tuple(stages),
                              sample_index=sample_index, liquid_device="cpu", liquid_threads=liquid_threads,
                              solver_type=solver_type, max_processes=_count("max_processes", max_processes),
                              liquid_timeout=liquid_timeout, mesh_timeout=mesh_timeout,
                              solve_timeout=solve_timeout, label=label)
    return view(raw)


def evaluate_liquid(source: PathLike, *, mode: str = "steady", inputs: Optional[Dict[str, Any]] = None,
                    duration_s: Optional[float] = None, objectives: Optional[Sequence[str]] = None,
                    gradient_parameters: Sequence[str] = DEFAULT_GRADIENT_PARAMETERS, fd_check: int = 0,
                    fd_relative_step: float = 1e-4, heat_multiplier: float = 1.0, threads: Optional[int] = None,
                    device: str = "cpu", output_root: Optional[PathLike] = None,
                    label: Optional[str] = None) -> LiquidView:
    """Fidelities LIQUID_STEADY / LIQUID_TRANSIENT: ``run_liquid_case`` (``.cfdproj`` or ``.liquidrun``)."""
    raw = api.run_liquid_case(source, output_root=output_root, label=label, mode=mode, inputs=inputs,
                              duration_s=duration_s, objectives=None if objectives is None else list(objectives),
                              gradient_parameters=list(gradient_parameters), heat_multiplier=heat_multiplier,
                              device=device, threads=threads, fd_check=fd_check, fd_relative_step=fd_relative_step)
    return view(raw)


def liquid_steady(liquidrun: Mapping[str, Any], *, objectives: Optional[Sequence[str]] = None,
                  gradient_parameters: Sequence[str] = DEFAULT_GRADIENT_PARAMETERS, inputs: Optional[Dict[str, Any]] = None,
                  fd_check: int = 0, fd_relative_step: float = 1e-4, heat_multiplier: float = 1.0,
                  threads: Optional[int] = None, device: str = "cpu") -> LiquidView:
    """Fidelity LIQUID_STEADY without a run directory: :func:`agent.scripted_api.solve_liquid_steady` in process.

    ``liquidrun`` is a ``.liquidrun`` object (for example
    ``agent.tools.project.export_liquid(...)["liquidrun"]``). The view reads
    like an ``evaluate_liquid`` result: ``metrics`` is the API's steady answer
    unchanged (objectives, gradients, finite-difference rows, network), plus
    ``inputs``/``identity``/``stages.steady.seconds``. Nothing is written, so
    ``result_json`` is ``None``. A manifest the DAE admission refuses gives
    ``status == "failed"`` with ``failure.stage == "input"``.
    """
    started_wall = api_identity.now_sgt()
    started = time.perf_counter()
    raw: Dict[str, Any] = {
        "format": LIQUID_STEADY_FORMAT, "kind": "liquid", "status": "failed", "failure": None,
        "startedAt": api_identity.iso(started_wall),
        "inputs": {"mode": "steady", "liquidrunSha256": api_identity.canonical_sha256(dict(liquidrun)),
                   "objectives": None if objectives is None else list(objectives),
                   "gradientParameters": list(gradient_parameters), "inputs": inputs, "fdCheck": fd_check,
                   "heatMultiplier": heat_multiplier, "device": device, "threads": threads},
        "identity": {"repository": api_identity.repository_identity(), "host": api_identity.host_identity(),
                     "python": api_identity.python_identity()},
        "stages": {}, "metrics": {}, "runDir": None,
    }
    try:
        steady = api.solve_liquid_steady(dict(liquidrun), objectives=None if objectives is None else list(objectives),
                                         gradient_parameters=list(gradient_parameters), heat_multiplier=heat_multiplier,
                                         device=device, threads=threads, fd_check=fd_check,
                                         fd_relative_step=fd_relative_step, inputs=inputs)
    except ValueError as exc:  # the DAE admission's refusal, as run_liquid_case reports it
        raw["failure"] = {"stage": "input", "reason": str(exc)}
    else:
        raw.update(status="succeeded", metrics=steady)
    raw["stages"]["steady"] = {"seconds": time.perf_counter() - started}
    raw["elapsedSeconds"] = time.perf_counter() - started
    raw["finishedAt"] = api_identity.iso(api_identity.now_sgt())
    return LiquidView(raw)


def liquid_gradient(source: Union[PathLike, Mapping[str, Any]], objective: str,
                    parameters: Optional[Sequence[str]] = None, **options: Any) -> Dict[str, Dict[str, float]]:
    """``{parameter: {edge/node id: d objective / d theta}}``.

    ``source`` is a liquid result (view or raw dict, i.e. a mapping with
    ``metrics``) to read; a ``.liquidrun`` object to solve in process with
    :func:`liquid_steady`; or a ``.cfdproj``/``.liquidrun`` path to run with
    :func:`evaluate_liquid`. A solve uses ``objectives=[objective]`` and
    ``gradient_parameters=parameters``; ``options`` go to the solving tool.
    ``KeyError`` for an objective or parameter the result does not report;
    ``RuntimeError`` when the solve failed.
    """
    if isinstance(source, Mapping) and "metrics" in source:
        result = source if isinstance(source, LiquidView) else LiquidView(dict(source))
    else:
        kwargs = dict(options)
        if parameters is not None:
            kwargs["gradient_parameters"] = list(parameters)
        solver = liquid_steady if isinstance(source, Mapping) else evaluate_liquid
        result = solver(source, objectives=[objective], **kwargs)
        if not result.succeeded:
            raise RuntimeError(f"liquid run failed at {result.failure_stage}: {(result.failure or {}).get('reason')}")
    return result.gradient(objective, None if parameters is None else list(parameters))


def evaluate_many(items: Sequence[Dict[str, Any]], *, out_root: PathLike, max_parallel: int, max_processes: int,
                  label: Optional[str] = None) -> Dict[str, Any]:
    """``run_batch``; a liquid item without ``device`` runs on ``cpu``. Returns the batch result."""
    rows = []
    for item in items:
        row = dict(item)
        if row.get("kind") == "liquid":
            row.setdefault("device", "cpu")
        rows.append(row)
    return api.run_batch(rows, out_root=out_root, max_parallel=_count("max_parallel", max_parallel),
                         max_processes=_count("max_processes", max_processes), label=label)


def identity() -> Dict[str, Any]:
    """The code identity a run would record (``agent.scripted_api.identity``) plus the agent's versions."""
    from agent import AGENT_API_VERSION, __version__
    return {"agent": {"version": __version__, "apiVersion": AGENT_API_VERSION},
            "repository": api_identity.repository_identity(), "host": api_identity.host_identity(),
            "python": api_identity.python_identity()}
def evaluate_dcrans_l0(source: Union[PathLike, Mapping[str, Any]], **options: Any) -> ResultView:
    """Fidelity DCRANS_L0: the zonal network of the L0 mesh (an L0 case directory or L0 project; steady or BDF1
    transient, the window solved at once)."""
    from agent.dcrans.service import solve_dcrans

    return view(solve_dcrans(source, level="L0", **options))


def evaluate_dcrans_l1(source: Union[PathLike, Mapping[str, Any]], **options: Any) -> ResultView:
    """Fidelity DCRANS_L1: unified JFNK full-patch coarse dcRANS CFD (steady or BDF1 transient)."""
    from agent.dcrans.service import solve_dcrans

    return view(solve_dcrans(source, level="L1", **options))
