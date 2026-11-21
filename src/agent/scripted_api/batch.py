"""Several scripted runs -- air, hybrid air/liquid, liquid -- each air or hybrid item through its own private server.

``run_batch(items, out_root=..., max_parallel=..., max_processes=...)`` runs at
most ``max_parallel`` items at once. An ``air`` item is one
:func:`agent.scripted_api.air.run_air_case` call and a ``hybrid`` item one
:func:`agent.scripted_api.air.run_hybrid_case` call, so each starts its own server
through ``start_server.sh --app api`` on its own port pair with its own
``--case-dir`` (one server holds one case; CLAUDE.md "Single-case server"),
and two of them may not share a case. A ``liquid`` item is one in-process
:func:`agent.scripted_api.liquid.run_liquid_case` call: no server, so it may share a
case with anything. The budget is split evenly: every item gets
``max_processes // max_parallel`` -- MPI ranks for air and hybrid items
(``max_processes`` of the call; an item whose project asks for more is refused
at its input stage, lower its ``meshProcesses`` / ``solveProcesses``), torch
threads for a liquid item (its ``threads`` may not exceed the share).

The batch writes ``<out_root>/<YYYY-MM-DD_HHMMSS>_SGT__batch[_label]/`` with the
normalised spec (``batch.json``) and ``batch-result.json``: per item its kind,
status, failure stage and the path of its own ``result.json`` (under the item's
case, ``log/api/``; a ``.liquidrun`` source writes under the batch directory).
Nothing that exists is deleted or overwritten -- not an earlier batch
directory, not an item's earlier runs.

Item keys (camelCase, as in batch.json):

* every kind: ``cfdproj`` (required; a liquid item may name a ``.liquidrun``),
  ``kind`` (``air`` default, ``hybrid``, ``liquid``), ``label`` (default
  ``batchNN``);
* ``air`` and ``hybrid``: ``caseDir`` (default: the .cfdproj's folder),
  ``stages`` (``["mesh", "solve"]`` default, ``["mesh"]``, ``["solve"]``; a
  hybrid item must solve), ``solverType``, ``runSettings``, and the CLI
  shortcuts ``meshEngine``, ``meshProcesses``, ``solveProcesses``,
  ``endTime``; ``allowSerialSolve``, ``acceptSeedCollisions``,
  ``requireCheckMesh``, ``meshTimeout``, ``solveTimeout``, ``maxProcesses``
  (at most the per-job share), ``app``;
* ``hybrid`` also: ``sampleIndex``, ``liquidTimeout``, ``liquidDevice``,
  ``liquidThreads``;
* ``liquid``: ``mode`` (``steady``/``transient``), ``inputs``, ``durationS``,
  ``objectives``, ``gradientParameters``, ``heatMultiplier``, ``device``
  (default ``cpu``), ``threads``, ``fdCheck``.
"""
from __future__ import annotations

import copy
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent.scripted_api import identity as ident
from agent.scripted_api.rundir import create_run_dir, write_json

BATCH_FORMAT = "dcdtuni.api.batch-result/v1"
KINDS = ("air", "hybrid", "liquid")
_COMMON_KEYS = {"cfdproj", "kind", "label"}
_AIR_KEYS = _COMMON_KEYS | {"caseDir", "stages", "solverType", "runSettings", "meshEngine", "meshProcesses",
                            "solveProcesses", "endTime", "allowSerialSolve", "acceptSeedCollisions",
                            "requireCheckMesh", "meshTimeout", "solveTimeout", "maxProcesses", "app"}
_ITEM_KEYS = {
    "air": _AIR_KEYS,
    "hybrid": _AIR_KEYS | {"sampleIndex", "liquidTimeout", "liquidDevice", "liquidThreads"},
    "liquid": _COMMON_KEYS | {"mode", "inputs", "durationS", "objectives", "gradientParameters", "heatMultiplier",
                              "device", "threads", "fdCheck"},
}


def load_batch_spec(path: Path | str) -> Dict[str, Any]:
    """Read batch.json (a list of items, or an object with ``items``); resolve paths against its folder."""
    from agent.scripted_api.rundir import read_json_source
    path = Path(path).resolve()
    _, spec = read_json_source(path)
    if isinstance(spec, list):
        spec = {"items": spec}
    if not isinstance(spec, dict) or not isinstance(spec.get("items"), list):
        raise ValueError(f"{path}: expected a list of items or an object with an 'items' list")
    items = []
    for item in spec["items"]:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: every item must be an object, got {item!r}")
        item = dict(item)
        for key in ("cfdproj", "caseDir"):
            if isinstance(item.get(key), str):
                item[key] = str((path.parent / item[key]).resolve())
        items.append(item)
    return {**spec, "items": items, "specPath": str(path)}


def _normalise(items: Sequence[Dict[str, Any]], per_job: int) -> List[Dict[str, Any]]:
    from agent.scripted_api.air import resolve_stages
    from agent.scripted_api.liquid import LIQUID_MODES
    rows: List[Dict[str, Any]] = []
    cases: Dict[Path, int] = {}
    if not items:
        raise ValueError("a batch needs at least one item")
    for index, raw in enumerate(items):
        if not isinstance(raw, dict) or not raw.get("cfdproj"):
            raise ValueError(f"item {index}: every item needs a 'cfdproj'")
        kind = raw.get("kind", "air")
        if kind not in KINDS:
            raise ValueError(f"item {index}: kind {kind!r} is not supported by run_batch (kinds: {KINDS})")
        unknown = sorted(set(raw) - _ITEM_KEYS[kind])
        if unknown:
            raise ValueError(f"item {index}: unknown key(s) {unknown} for kind {kind!r}; allowed: "
                             f"{sorted(_ITEM_KEYS[kind])}")
        cfdproj = Path(raw["cfdproj"]).resolve()
        row = {**copy.deepcopy(raw), "index": index, "kind": kind, "cfdproj": str(cfdproj),
               "label": raw.get("label") or f"batch{index:02d}"}
        if kind == "liquid":
            if raw.get("mode", "steady") not in LIQUID_MODES:
                raise ValueError(f"item {index}: mode must be one of {LIQUID_MODES}, got {raw.get('mode')!r}")
            threads = raw.get("threads")
            if threads is not None and int(threads) > per_job:
                raise ValueError(f"item {index}: threads={threads} is above the per-job share {per_job} "
                                 "(max_processes // max_parallel)")
            row.update(mode=raw.get("mode", "steady"), caseDir=str(cfdproj.parent))
            rows.append(row)
            continue
        case = Path(raw["caseDir"]).resolve() if raw.get("caseDir") else cfdproj.parent
        if case in cases:
            raise ValueError(f"items {cases[case]} and {index} use the same case directory {case}; one case "
                             "holds one run at a time -- give one of them its own caseDir")
        cases[case] = index
        limit = raw.get("maxProcesses")
        if limit is not None and int(limit) > per_job:
            raise ValueError(f"item {index}: maxProcesses={limit} is above the per-job share {per_job} "
                             "(max_processes // max_parallel)")
        stages = list(resolve_stages(raw.get("stages")))
        if kind == "hybrid" and "solve" not in stages:
            raise ValueError(f"item {index}: a hybrid item hands liquid heat to an air solve; its stages must "
                             "include 'solve' (use kind 'air' for a mesh alone)")
        row.update(caseDir=str(case), stages=stages, maxProcesses=int(limit) if limit is not None else per_job)
        rows.append(row)
    return rows


def _air_kwargs(row: Dict[str, Any]) -> Dict[str, Any]:
    from agent.scripted_api.air import run_settings_from_options
    settings = run_settings_from_options(row.get("runSettings"), mesh_engine=row.get("meshEngine"),
                                         mesh_processes=row.get("meshProcesses"),
                                         solve_processes=row.get("solveProcesses"), end_time=row.get("endTime"))
    kwargs: Dict[str, Any] = {"case_dir": row["caseDir"], "run_settings": settings, "stages": tuple(row["stages"]),
                              "solver_type": row.get("solverType"), "label": row["label"],
                              "max_processes": row["maxProcesses"],
                              "allow_serial_solve": bool(row.get("allowSerialSolve", False)),
                              "accept_seed_collisions": bool(row.get("acceptSeedCollisions", False)),
                              "require_check_mesh": bool(row.get("requireCheckMesh", False))}
    for key, name in (("meshTimeout", "mesh_timeout"), ("solveTimeout", "solve_timeout"), ("app", "app")):
        if row.get(key) is not None:
            kwargs[name] = row[key]
    return kwargs


def _hybrid_kwargs(row: Dict[str, Any]) -> Dict[str, Any]:
    kwargs = _air_kwargs(row)
    for key, name in (("sampleIndex", "sample_index"), ("liquidTimeout", "liquid_timeout"),
                      ("liquidThreads", "liquid_threads")):
        if row.get(key) is not None:
            kwargs[name] = row[key]
    if "liquidDevice" in row:
        kwargs["liquid_device"] = row["liquidDevice"]
    return kwargs


def _liquid_kwargs(row: Dict[str, Any], batch_dir: Path) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"label": row["label"], "mode": row["mode"], "inputs": row.get("inputs"),
                              "duration_s": row.get("durationS"), "device": row.get("device", "cpu"),
                              "threads": row.get("threads")}
    for key, name in (("objectives", "objectives"), ("gradientParameters", "gradient_parameters"),
                      ("heatMultiplier", "heat_multiplier"), ("fdCheck", "fd_check")):
        if row.get(key) is not None:
            kwargs[name] = row[key]
    if not row["cfdproj"].endswith(".cfdproj"):
        kwargs["output_root"] = str(batch_dir)
    return kwargs


def _run_item(row: Dict[str, Any], batch_dir: Path) -> Dict[str, Any]:
    from agent.scripted_api import air, liquid
    started = time.perf_counter()
    summary = {key: row.get(key) for key in ("index", "kind", "label", "cfdproj", "caseDir", "stages",
                                             "maxProcesses", "mode")}
    try:
        if row["kind"] == "liquid":
            result = liquid.run_liquid_case(row["cfdproj"], **_liquid_kwargs(row, batch_dir))
        elif row["kind"] == "hybrid":
            result = air.run_hybrid_case(row["cfdproj"], **_hybrid_kwargs(row))
        else:
            result = air.run_air_case(row["cfdproj"], **_air_kwargs(row))
    except ValueError as exc:  # refused before it began (a missing or non-JSON source): input, as `air` says
        summary.update(status="failed", failure={"stage": "input", "reason": str(exc)},
                       runDir=None, resultJson=None, elapsedSeconds=time.perf_counter() - started)
        return summary
    except Exception as exc:  # an item's crash is its own failure, never the batch's
        summary.update(status="failed", failure={"stage": "internal", "reason": f"{type(exc).__name__}: {exc}",
                                                 "traceback": traceback.format_exc()[-4000:]},
                       runDir=None, resultJson=None, elapsedSeconds=time.perf_counter() - started)
        return summary
    summary.update(status=result.get("status"), failure=result.get("failure"), runDir=result.get("runDir"),
                   resultJson=(result.get("artifacts") or {}).get("resultJson"),
                   elapsedSeconds=result.get("elapsedSeconds"))
    return summary


def run_batch(items: Sequence[Dict[str, Any]], *, out_root: Path | str, max_parallel: int = 1,
              max_processes: int, label: Optional[str] = None) -> Dict[str, Any]:
    """Run every item (see the module docstring); return and write ``batch-result.json``."""
    max_parallel, max_processes = int(max_parallel), int(max_processes)
    if max_parallel < 1 or max_processes < 1:
        raise ValueError("max_parallel and max_processes must be at least 1")
    if max_parallel > max_processes:
        raise ValueError(f"max_parallel={max_parallel} jobs cannot share max_processes={max_processes} ranks "
                         "(each job needs at least one)")
    per_job = max_processes // max_parallel
    rows = _normalise(items, per_job)
    started_wall, started = ident.now_sgt(), time.perf_counter()
    batch_dir = create_run_dir(Path(out_root).resolve(), "batch", label)
    write_json(batch_dir / "batch.json", {"maxParallel": max_parallel, "maxProcesses": max_processes,
                                          "processesPerJob": per_job, "items": rows})
    with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="api-batch") as pool:
        outcomes = list(pool.map(lambda row: _run_item(row, batch_dir), rows))
    result = {
        "format": BATCH_FORMAT, "status": "succeeded" if all(row["status"] == "succeeded" for row in outcomes)
        else "failed",
        "batchDir": str(batch_dir), "resultJson": str(batch_dir / "batch-result.json"),
        "startedAt": ident.iso(started_wall), "finishedAt": ident.iso(ident.now_sgt()),
        "elapsedSeconds": time.perf_counter() - started,
        "maxParallel": max_parallel, "maxProcesses": max_processes, "processesPerJob": per_job,
        "counts": {"items": len(outcomes), "succeeded": sum(row["status"] == "succeeded" for row in outcomes),
                   "byKind": {kind: sum(row["kind"] == kind for row in outcomes) for kind in KINDS}},
        "items": outcomes,
        "identity": {"repository": ident.repository_identity(), "host": ident.host_identity()},
    }
    write_json(batch_dir / "batch-result.json", result)
    return result
