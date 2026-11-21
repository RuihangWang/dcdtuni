"""Thin JSON adapter for liquid runtime sessions.

This module deliberately imports the torch-backed manager only after a liquid
runtime request arrives, keeping ordinary air-only server startup lightweight.
"""
from __future__ import annotations

import json
import re
import threading


_MANAGER = None
_MANAGER_LOCK = threading.Lock()
_RUN_PATH = re.compile(
    r"^/api/liquid/runs/([A-Za-z0-9_-]{1,64})(?:/(frames|result|checkpoint|executed-input|heat-audit|saved|start|pause|resume|cancel|step|inputs|equilibrium|restore))?$"
)


def _loaded_runs() -> dict:
    """Runs this process holds in memory, without starting the manager."""
    manager = _MANAGER
    return manager.loaded_runs() if manager is not None else {}


def _manager():
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                from backend.liquidcooling.run_manager import LiquidRunManager
                _MANAGER = LiquidRunManager()
    return _MANAGER


def _send(handler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _body(handler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ValueError("invalid Content-Length") from exc
    if length < 0 or length > 20 * 1024 * 1024:
        raise ValueError("request body is too large")
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


def _error(handler, exc: Exception) -> None:
    from backend.liquidcooling.run_manager import (
        RunConflictError,
        RunInputError,
        RunNotFoundError,
    )
    if isinstance(exc, RunNotFoundError):
        status = 404
        message = f"liquid run {exc.args[0]!r} was not found"
    elif isinstance(exc, RunConflictError):
        status = 409
        message = str(exc)
    elif isinstance(exc, (RunInputError, ValueError, TypeError, KeyError)):
        status = 400
        message = str(exc)
    else:
        status = 500
        message = str(exc) or "liquid runtime request failed"
    _send(handler, status, {"error": message})


def handle_get(handler, path: str, query: dict[str, list[str]], case_dir=None) -> None:
    import liquid_run_catalog as catalog
    if path == "/api/liquid/runs":
        try:
            runs = catalog.list_liquid_runs(case_dir, loaded=_loaded_runs())
        except catalog.CatalogError as exc:
            _send(handler, exc.status, {"code": exc.code, "error": str(exc)})
            return
        _send(handler, 200, {"case": case_dir.name, "runs": runs})
        return
    match = _RUN_PATH.fullmatch(path)
    if match is None:
        _send(handler, 404, {"error": "unknown liquid runtime endpoint"})
        return
    run_id, action = match.groups()
    if action == "saved":
        try:
            _send(handler, 200, catalog.saved_run_metadata(case_dir, run_id))
        except catalog.CatalogError as exc:
            _send(handler, exc.status, {"code": exc.code, "error": str(exc)})
        return
    try:
        manager = _manager()
        if action is None:
            payload = manager.get(run_id)
        elif action == "frames":
            raw_after = query.get("after", ["-1"])[0]
            try:
                after = int(raw_after)
            except (TypeError, ValueError) as exc:
                raise ValueError("after must be an integer") from exc
            payload = manager.frames(run_id, after=after)
        elif action == "result":
            payload = manager.result(run_id)
        elif action == "checkpoint":
            payload = manager.checkpoint(run_id)
        elif action == 'executed-input':
            payload = manager.executed_input(run_id)
        elif action == 'heat-audit':
            if set(query) != {'project_uid'} or len(query['project_uid']) != 1:
                raise ValueError('heat-audit requires one project_uid query parameter')
            payload = manager.heat_audit(run_id, project_uid=query['project_uid'][0])
        else:
            _send(handler, 404, {"error": "unknown liquid runtime GET endpoint"})
            return
        _send(handler, 200, payload)
    except Exception as exc:
        from backend.liquidcooling.run_manager import RunNotFoundError
        if isinstance(exc, RunNotFoundError) and catalog.saved_run_exists(case_dir, run_id):
            # Not in memory (server restart), but saved in this case.
            _send(handler, 404, {
                "error": f"liquid run {run_id!r} is not loaded in this server; it is saved in this "
                         f"case and restores read-only with POST /api/liquid/runs/{run_id}/restore",
                "saved": True, "restore": f"/api/liquid/runs/{run_id}/restore"})
            return
        _error(handler, exc)


def handle_post(handler, path: str, *, case_dir) -> None:
    try:
        body = _body(handler)
        manager = _manager()
        if path == "/api/liquid/runs":
            if case_dir is None:
                raise ValueError("No case directory set")
            checkpoint = body.get("checkpoint")
            liquidrun = body.get("liquidrun")
            # A checkpoint carries the exact liquidrun it was made from; a
            # resume may send only the checkpoint (its bytes verbatim, so the
            # canonical config hash still matches).
            if liquidrun is None and isinstance(checkpoint, dict):
                liquidrun = checkpoint.get("liquidrun")
            if not isinstance(liquidrun, dict):
                raise ValueError("liquidrun must be a JSON object")
            controllers = body.get("controllers")
            if (controllers is None and "controllers" not in liquidrun
                    and isinstance(checkpoint, dict)):
                controllers = checkpoint.get("controllers", [])
            if controllers is not None and not isinstance(controllers, list):
                raise ValueError("controllers must be an array")
            payload = manager.create(
                liquidrun,
                case_dir=case_dir,
                controllers=controllers,
                project_revision=body.get("project_revision"),
                checkpoint=checkpoint,
            )
            _send(handler, 201, payload)
            return

        match = _RUN_PATH.fullmatch(path)
        if match is None:
            _send(handler, 404, {"error": "unknown liquid runtime endpoint"})
            return
        run_id, operation = match.groups()
        if operation == 'restore':
            if body:
                raise ValueError('restore takes an empty JSON object')
            _send(handler, 200, manager.restore(run_id, case_dir=case_dir))
            return
        if operation == 'inputs':
            allowed = {'heat_multiplier', 'pump_rpm_ratios', 'valve_openings', 'primary_inlet_K'}
            if not body or set(body) - allowed:
                raise ValueError('inputs accepts heat_multiplier, pump_rpm_ratios, valve_openings and primary_inlet_K only')
            _send(handler, 200, manager.set_inputs(run_id, **body))
            return
        if operation == 'equilibrium':
            if set(body) - {'heat_multiplier'}:
                raise ValueError('equilibrium accepts only optional heat_multiplier')
            _send(handler, 200, manager.equilibrium(run_id, heat_multiplier=body.get('heat_multiplier')))
            return
        if operation not in {"start", "pause", "resume", "cancel", "step"}:
            _send(handler, 404, {"error": "unknown liquid runtime POST endpoint"})
            return
        speed = body.get("speed") if operation in {"start", "resume"} else None
        payload = manager.operate(run_id, operation, speed=speed)
        _send(handler, 200, payload)
    except Exception as exc:
        _error(handler, exc)
