"""Front door of the data-centre design evaluator: one call from a project file to physical metrics.

    from dceval import evaluate
    result = evaluate("Room.cfdproj", "runs/room-001", solver="gpuNewton", gpus=[0], processes=8)
    result["status"], result["metrics"]["convergence"]["verdict"], result["metrics"]["fields"]

Everything runs in the calling process and its child processes: the headless project compiler, the cut-cell
mesher, the air solver (``openfoam`` or ``gpuNewton``) and, for a project with liquid-cooled servers, the liquid
network solve whose heat the air solve takes. There is no server, port or login. The command line is
``python -m dceval evaluate <cfdproj> --case-dir <dir> --solver ... --gpus ... --processes ...``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Union

from backend.aircooling.runner import gpu_newton

SOLVERS = gpu_newton.ENGINES                 # ("openfoam", "gpuNewton")
STAGES = ("mesh", "solve")

__all__ = ["evaluate", "summary", "check", "SOLVERS", "STAGES"]
FOLDER = Path(__file__).resolve().parents[2]  # the evaluator folder: EXPORT.json and the exported tree


def evaluate(cfdproj: Union[str, Path], case_dir: Union[str, Path, None] = None, *,
             solver: str = "gpuNewton", gpus: Union[str, int, Iterable[Union[str, int]], None] = None,
             processes: int = 8, stages: Sequence[str] = STAGES, mesh_engine: Optional[str] = None,
             end_time: Optional[float] = None, run_settings: Optional[Dict[str, Any]] = None,
             mesh_timeout: float = 1800.0, solve_timeout: float = 4 * 3600.0, liquid_timeout: float = 3600.0,
             liquid_device: Optional[str] = "cpu", allow_serial_solve: bool = False,
             require_check_mesh: bool = False, label: Optional[str] = None,
             raise_on_failure: bool = False) -> Dict[str, Any]:
    """Mesh and solve one ``.cfdproj`` and return its ``result.json`` as a dict, with ``resultPath`` added.

    ``case_dir``: the working directory of this evaluation (mesh, solve directories, logs); default: the
    directory of the project file. One evaluation at a time per case directory; evaluations in different case
    directories can run side by side.

    ``solver``: ``"gpuNewton"`` (default: the differentiable GPU Newton-Krylov solver, steady solves) or
    ``"openfoam"`` (the CPU reference).
    ``gpus``: the cards the GPU solver may use, as ``nvidia-smi`` indices or ``GPU-...`` UUIDs (a list, or a comma
    separated string); one solve runs on one of them, the idle one with the most free memory. None: the cards
    already named in the environment, else any card.

    ``processes``: MPI ranks of the mesher and of the OpenFOAM solve, and the number of mesh partitions the GPU
    solver reads. ``stages``: ``("mesh", "solve")``, ``("mesh",)``, or ``("solve",)`` to solve again on the
    case's accepted mesh. ``mesh_engine``, ``end_time`` (iterations of a steady solve) and ``run_settings``
    (``{"meshConfig": {...}, "simulationConfig": {...}}``) override what the project stores.

    ``mesh_timeout`` / ``solve_timeout`` / ``liquid_timeout`` (seconds) are hard limits: past one, the job and
    all its processes are stopped and the result is ``failed`` at that stage.

    The result: ``status`` (``succeeded`` when every requested stage ran to its end; convergence is reported
    in ``metrics.convergence``, never folded into the status), ``failure`` (``{stage, reason}`` or None),
    ``stages`` (timings, cell count, return codes, the solve directory), ``metrics`` (``convergence``,
    ``fields`` -- per cooling unit and per server -- ``sensors`` and, for a hybrid project, ``liquid`` and
    ``heatLedger``), ``identity`` (input hashes, tool versions), ``artifacts`` (paths) and ``resultPath``.
    Invalid arguments raise ``ValueError``; a failed run is returned, not raised, unless ``raise_on_failure``.
    """
    from backend.api import run_air_case
    from backend.api.air import run_settings_from_options
    from backend.api.direct_session import GPU_ENV, resolve_gpus

    engine = gpu_newton.resolve_engine(solver)
    if isinstance(processes, bool) or not isinstance(processes, int) or processes < 1:
        raise ValueError(f"processes must be a positive integer, got {processes!r}")
    if gpus is not None and engine != gpu_newton.GPU_NEWTON:
        raise ValueError("gpus names the cards of solver='gpuNewton'; the openfoam solver runs on CPU ranks")
    uuids = resolve_gpus(gpus)
    if gpus is not None and not uuids:
        raise ValueError("gpus names no card; pass None to use any card")
    settings = run_settings_from_options(run_settings, mesh_engine=mesh_engine, mesh_processes=processes,
                                         solve_processes=processes, end_time=end_time)
    # The cards travel in the variable gpu_newton.configuration() reads, for this call only.
    before = os.environ.get(GPU_ENV)
    if uuids:
        os.environ[GPU_ENV] = ",".join(uuids)
    try:
        result = run_air_case(cfdproj, case_dir=case_dir, run_settings=settings, stages=tuple(stages),
                              solver_engine=engine, max_processes=processes, mesh_timeout=mesh_timeout,
                              solve_timeout=solve_timeout, liquid_timeout=liquid_timeout,
                              liquid_device=liquid_device, allow_serial_solve=allow_serial_solve,
                              require_check_mesh=require_check_mesh, label=label,
                              raise_on_failure=raise_on_failure)
    finally:
        if uuids:
            if before is None:
                os.environ.pop(GPU_ENV, None)
            else:
                os.environ[GPU_ENV] = before
    return {**result, "resultPath": (result.get("artifacts") or {}).get("resultJson")}


def summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """The few fields a caller usually reads first, from a result of :func:`evaluate`."""
    stages = result.get("stages") or {}
    metrics = result.get("metrics") or {}
    fields = metrics.get("fields") or {}
    convergence = metrics.get("convergence") or {}
    return {
        "status": result.get("status"), "failure": result.get("failure"),
        "resultPath": result.get("resultPath") or (result.get("artifacts") or {}).get("resultJson"),
        "caseDir": result.get("caseDir"), "elapsedSeconds": result.get("elapsedSeconds"),
        "solver": (result.get("inputs") or {}).get("solverEngine"),
        "stages": (result.get("inputs") or {}).get("stages"),
        "cells": (stages.get("mesh") or {}).get("cells"),
        "checkMeshOk": (stages.get("mesh") or {}).get("checkMeshOk"),
        "solveRunDir": (stages.get("solve") or {}).get("runDir"),
        "convergence": convergence.get("verdict"), "convergenceReason": convergence.get("reason"),
        "rackInlet": (fields.get("rackInlet") or {}).get("summary"),
        "energyBalance": fields.get("energyBalance"),
    }


def check(folder: Union[str, Path, None] = None) -> Dict[str, Any]:
    """Compare the evaluator folder with the ``EXPORT.json`` its export wrote: every exported file must be present
    with the SHA-256 listed there. Returns ``{"ok", "source_commit", "gpu_solver", "files", "changed", "missing"}``.
    The folder is refreshed by a new export, never by hand; a changed file means a hand edit to move upstream."""
    import hashlib
    import json

    root = Path(folder) if folder is not None else FOLDER
    manifest = json.loads((root / "EXPORT.json").read_text(encoding="utf-8"))
    digests = manifest.get("sha256")
    if not isinstance(digests, dict):
        raise ValueError(f"{root / 'EXPORT.json'} lists no SHA-256 (an export older than the check)")
    changed, missing = [], []
    for name, digest in sorted(digests.items()):
        path = root / name
        if not path.is_file():
            missing.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    return {"ok": not changed and not missing, "source_commit": manifest.get("source_commit"),
            "gpu_solver": manifest.get("gpu_solver"), "files": len(digests), "changed": changed, "missing": missing}
