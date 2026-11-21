"""In-process session of one scripted air run: no HTTP server, no ports, no credentials, no launcher.

``backend.api.air`` drives a session object: ``submit_run`` with the editor's run body, ``wait_job``, ``job_log``
and, for a hybrid project, ``call`` on the liquid run endpoints. :class:`DirectSession` gives that surface inside
the calling process. What reaches the numerical path is what the modelling tool's server produces for the same
body: the inputs published into the case (manifest with its ``artifacts`` paths, the STL batch, the original-input
archive, the liquid heat of a hybrid solve), the ``run_cfd`` command line fed the manifest on stdin, the
environment of that child, and the job's final status fields (mesh quality summary, solve assessment).

The functions between the two "ported" markers below are ports of the server's functions of the same name; the
exporter keeps a hash of each original and its tests fail when one changes, so the port is redone knowingly.
Differences from the originals are marked ``direct:``.

Not offered here (the server has them): AI initialisation, patch updates of an existing mesh, the saved liquid
run catalogue, and anything a browser needs.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from backend.aircooling.runner import gpu_newton
from backend.aircooling.runner.case_execution_lock import CaseExecutionBusyError, case_execution_lock
from backend.aircooling.runner.mesh_budget import MeshBudget, MeshTimeoutError
from backend.api.identity import REPO

# Names derived from the runner's own constants, so they follow whatever the distribution calls them.
GPU_ENV = gpu_newton.ENABLE_ENV + "_GPU"            # the cards the GPU solver may use (gpu_newton.configuration)
_TOOL = gpu_newton.ENABLE_ENV.split("_", 1)[0].lower()
ORIGINAL_INPUT_FORMAT = f"{_TOOL}.air.original-input/v1"
RUN_PATH = "/api/run-mesh"


class ServerError(RuntimeError):
    """A refused call, with the status code the server would have answered (the class ``air`` catches)."""

    def __init__(self, status: int, body: Any, path: str):
        self.status, self.body, self.path = status, body, path
        detail = body.get("error") if isinstance(body, dict) else body
        super().__init__(f"{path} -> {status}: {detail}")


# ── ported: publication of one uploaded input batch ─────────────────────────

# Allow only safe filename characters. Empty -> fallback applied below.
_CFDRUN_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _sanitize_cfdrun_filename(raw: str | None) -> str:
    """Return a safe `<name>.cfdrun` filename: no directory part, disallowed characters replaced with `_`,
    a non-empty stem and the `.cfdrun` suffix."""
    base = Path((raw or "")).name  # drop any directory components
    if not base:
        base = "project.cfdrun"
    if base.lower().endswith(".cfdrun"):
        stem = base[: -len(".cfdrun")]
    else:
        stem = base
    safe_stem = "".join(c if _CFDRUN_NAME_RE.match(c) else "_" for c in stem)
    # Collapse runs of underscores and strip leading dots (hidden-file safety).
    safe_stem = re.sub(r"_+", "_", safe_stem).strip("._") or "project"
    return f"{safe_stem}.cfdrun"


def _check_mesh_execution_budget(mesh_budget, step):
    if mesh_budget is not None:
        mesh_budget.check(step)


def _prepare_cfdrun_payload(published_model_dir: Path, content: str, *, mesh_budget=None) -> bytes:
    """Prepare one exact UTF-8 snapshot before the optional replay write."""
    try:
        manifest = json.loads(content)
    except (ValueError, TypeError):
        manifest = None
    if mesh_budget is not None:
        mesh_budget.tighten_manifest(manifest)
    if isinstance(manifest, dict) and manifest.get("schemaVersion") in (2, 3, 4):
        case_dir = published_model_dir.parent.resolve()
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            artifacts = {}
        artifacts["caseDir"] = str(case_dir)
        artifacts["modelDir"] = str(published_model_dir.resolve())
        artifacts["triSurfaceDir"] = str(
            (case_dir / "Mesh" / "constant" / "triSurface").resolve()
        )
        artifacts["openfoamCaseDir"] = str((case_dir / "Mesh").resolve())
        if not isinstance(artifacts.get("logs"), dict):
            artifacts["logs"] = {}
        manifest["artifacts"] = artifacts
        content = json.dumps(manifest, ensure_ascii=False, indent=2)

    return content.encode('utf-8')


def _write_active_cfdrun(
    model_dir: Path,
    raw_filename: str | None,
    content: str,
    *,
    published_model_dir: Path | None = None,
    _prepared_bytes: bytes | None = None,
) -> Path:
    """Persist `content` as the single active .cfdrun manifest in `model_dir`: sanitized filename, every other
    `*.cfdrun` sibling removed (readers that glob see exactly one), the legacy `bridge_bc.json` removed."""
    model_dir.mkdir(parents=True, exist_ok=True)
    filename = _sanitize_cfdrun_filename(raw_filename)
    cfdrun_path = model_dir / filename
    # Uploads first write to a staging directory. The artifacts must still
    # identify the published case, never the temporary staging path.
    published_model_dir = published_model_dir or model_dir
    if cfdrun_path.is_symlink():
        cfdrun_path.unlink()

    payload = (_prepared_bytes if _prepared_bytes is not None
               else _prepare_cfdrun_payload(published_model_dir, content))
    cfdrun_path.write_bytes(payload)

    for sibling in sorted(model_dir.glob("*.cfdrun")):
        if sibling != cfdrun_path:
            sibling.unlink()

    stale_bridge = model_dir / "bridge_bc.json"
    if stale_bridge.exists():
        try:
            stale_bridge.unlink()
        except OSError:
            pass

    return cfdrun_path


def _decode_stl_uploads(stl_files: dict | None, *, mesh_budget=None) -> dict[str, bytes]:
    """Validate the complete batch before any existing case input is moved."""
    if stl_files is None:
        return {}
    if not isinstance(stl_files, dict):
        raise ValueError("stlFiles must be an object")
    decoded = {}
    for filename, encoded in stl_files.items():
        _check_mesh_execution_budget(mesh_budget, "STL decode")
        if (not isinstance(filename, str) or not filename or
                Path(filename).name != filename or filename in (".", "..") or
                not filename.lower().endswith(".stl")):
            raise ValueError(f"Invalid STL filename: {filename!r}")
        if not isinstance(encoded, str):
            raise ValueError(f"STL content must be base64 text: {filename}")
        try:
            decoded[filename] = base64.b64decode(encoded, validate=True)
            _check_mesh_execution_budget(mesh_budget, "STL decode")
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"Invalid base64 STL content: {filename}") from exc
    return decoded


MESH_SOURCE_RECORD = "mesh-source-batch.json"


def _archived_stl_batch_dir(case_dir, manifest):
    """The original-input triSurface holding exactly the STL bytes `manifest` declares.

    A mesh job rewrites Mesh/constant/triSurface, and a solve uploads no STL. Its manifest still declares the
    uploaded batch by name and content hash, and every mesh job archived that batch unchanged under
    log/air-inputs/<id>/. Newest first; None when no archive matches exactly.
    """
    declared = (manifest.get("geometryExport") or {}).get("stlFiles") if isinstance(manifest, dict) else None
    if not isinstance(declared, dict) or not declared:
        return None
    expected = {}
    for row in declared.values():
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            return None
        expected[row["path"]] = row.get("contentHash")
    root = Path(case_dir) / "log" / "air-inputs"
    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        tri = entry / "Mesh" / "constant" / "triSurface"
        if not tri.is_dir():
            continue
        names = {p.name for p in tri.iterdir() if p.is_file() and p.suffix.lower() == ".stl"}
        if names != set(expected):
            continue
        if all(hashlib.sha256((tri / name).read_bytes()).hexdigest() == digest
               for name, digest in expected.items()):
            return tri
    return None


def _stage_original_air_inputs(stage, case_dir, filename, frontend_content,
                               published_payload, decoded_stls, input_id, *, original_request=None, mesh_budget=None):
    """Retain only this uploaded complete batch; never reconstruct live STL files."""
    if str(uuid.UUID(input_id)) != input_id:
        raise ValueError("Original input archive id is invalid")
    raw_payload = frontend_content.encode("utf-8")
    raw = json.loads(raw_payload)
    if not isinstance(raw, dict) or not isinstance(raw.get("geometryExport"), dict):
        raise ValueError("Original input archive needs a manifest geometry export")
    declarations = raw["geometryExport"].get("stlFiles")
    if not isinstance(declarations, dict) or not declarations:
        raise ValueError("Original input archive needs declared STL content hashes")
    expected = {}
    for row in declarations.values():
        if not isinstance(row, dict):
            raise ValueError("Original input archive has an invalid STL declaration")
        name, digest = row.get("path"), row.get("contentHash")
        if (not isinstance(name, str) or name not in decoded_stls or name in expected or
                not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Original input archive has an incomplete or ambiguous STL declaration")
        expected[name] = digest
    if set(expected) != set(decoded_stls):
        raise ValueError("Original input archive STL set differs from the fresh uploaded batch")
    published = json.loads(published_payload)
    if published.get("geometryExport") != raw.get("geometryExport"):
        raise ValueError("Published manifest changed the original geometry identity")
    destination = case_dir / "log" / "air-inputs" / input_id
    if not destination.resolve().is_relative_to(case_dir):
        raise ValueError("Original input archive path escapes its case directory")
    archive = stage / "original-air-input"
    files = {"frontend/" + filename: raw_payload, "model/" + filename: published_payload}
    files.update({"Mesh/constant/triSurface/" + name: data for name, data in decoded_stls.items()})
    request_identity = {"captured": False}
    if original_request is not None:
        request_bytes, request_data = original_request
        allowed = {"cfdrunJson", "cfdExportJson", "cfdrunFilename", "stlFiles",
                   "meshOnly", "solveOnly", "caseDir", "hybridHeat", "meshPreparationElapsedSeconds"}
        request_identity = {"captured": False, "bytes": len(request_bytes),
                            "sha256": hashlib.sha256(request_bytes).hexdigest()}
        if set(request_data).issubset(allowed):
            files["request.json"] = request_bytes
            request_identity.update(captured=True, path="request.json")
        else:
            # Preserve legacy unknown-field handling without archiving those fields.
            request_identity["reason"] = "extra-fields-not-retained"
    rows = []
    for relative, data in files.items():
        _check_mesh_execution_budget(mesh_budget, "original input archive")
        digest = hashlib.sha256(data).hexdigest()
        name = Path(relative).name
        if relative.startswith("Mesh/") and digest != expected[name]:
            raise ValueError("Original input archive STL differs from its export-time content hash")
        target = archive / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _check_mesh_execution_budget(mesh_budget, "original input archive write")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise OSError("Original input archive write did not preserve complete bytes")
        _check_mesh_execution_budget(mesh_budget, "original input archive verification")
        rows.append({"path": relative, "bytes": len(data), "sha256": digest})
    receipt = {"format": ORIGINAL_INPUT_FORMAT, "inputId": input_id,
               "jobId": input_id[:8], "sourceProject": raw.get("sourceProject"),
               "frontendManifest": "frontend/" + filename, "publishedManifest": "model/" + filename,
               "networkRequestBodyCaptured": request_identity["captured"],
               "requestBody": request_identity, "files": rows}
    (archive / "INPUT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    _check_mesh_execution_budget(mesh_budget, "original input archive seal")
    return destination, archive


def _publish_case_input_batch(
    case_dir: Path,
    cfdrun_filename: str | None,
    cfdrun_json: str | None,
    stl_files: dict | None,
    *,
    require_manifest: bool = True,
    replace_stls: bool = False,
    capture_snapshot: bool = False,
    mesh_budget=None,
    hybrid_heat=None,
    original_input_id=None,
    original_request=None,
    liquid_manager: Optional[Callable[[], Any]] = None,
):
    """Publish an entire uploaded input batch under the cross-process case lock.

    Staging and validation happen before moving existing data. The two affected
    directories are swapped while the lock is held; if any rename fails, the
    previous directories are restored before an error is returned. Readers
    participating in the case lock never observe a partial batch.

    direct: ``liquid_manager`` returns the liquid run manager that holds the run a ``hybrid_heat`` names (the
    session's own), where the server reaches for its process-wide one.
    """
    case_dir = Path(case_dir).expanduser().resolve()
    with case_execution_lock(case_dir):
        _check_mesh_execution_budget(mesh_budget, "input publication admission")
        decoded_stls = _decode_stl_uploads(stl_files, mesh_budget=mesh_budget)
        replace_stls = replace_stls or bool(decoded_stls)
        if cfdrun_json is not None and not isinstance(cfdrun_json, str):
            raise ValueError("cfdrunJson must be a JSON string")
        if hybrid_heat is not None and (not isinstance(hybrid_heat, dict) or
                set(hybrid_heat) != {"project", "liquidRunId", "sampleIndex"} or
                not isinstance(hybrid_heat["project"], dict) or
                not isinstance(hybrid_heat["liquidRunId"], str) or
                type(hybrid_heat["sampleIndex"]) is not int or not cfdrun_json or not capture_snapshot):
            raise ValueError("hybridHeat needs a fresh air export, project and owned liquid run/sample")
        model_dir = case_dir / "model"
        tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
        existing = sorted(model_dir.glob("*.cfdrun")) if model_dir.is_dir() else []
        if not cfdrun_json and require_manifest and not existing:
            raise ValueError("No cfdrun content provided or found in case/model directory")
        active_path = (model_dir / _sanitize_cfdrun_filename(cfdrun_filename)
                       if cfdrun_json else (existing[0] if existing else None))
        if capture_snapshot:
            from backend.aircooling.runner.cfdrun_snapshot import CfdrunSnapshot
            from backend.aircooling.runner.cfdrun_stl_identity import surface_batch_sha256
            if not cfdrun_json and len(existing) != 1:
                raise ValueError('RAM input requires exactly one existing .cfdrun or a fresh export')
        published_payload = (_prepare_cfdrun_payload(model_dir, cfdrun_json, mesh_budget=mesh_budget)
                             if cfdrun_json else (active_path.read_bytes() if active_path else None))
        if mesh_budget is not None and not cfdrun_json and published_payload is not None:
            try:
                manifest = json.loads(published_payload)
            except (ValueError, TypeError):
                manifest = None
            mesh_budget.tighten_manifest(manifest)
        sha = hashlib.sha256(published_payload).hexdigest() if published_payload is not None else None
        _check_mesh_execution_budget(mesh_budget, "input bytes and identity")
        if not cfdrun_json and not replace_stls:
            snapshot = (CfdrunSnapshot(published_payload, case_dir, active_path, expected_sha256=sha,
                                       stl_batch_sha256=surface_batch_sha256(case_dir))
                        if capture_snapshot else None)
            _check_mesh_execution_budget(mesh_budget, "existing input snapshot")
            return active_path, sha, snapshot

        scratch = Path(tempfile.mkdtemp(prefix=f".{_TOOL}-upload-", dir=case_dir))
        stage = scratch / "stage"
        backup = scratch / "backup"
        stage.mkdir()
        backup.mkdir()
        changes: list[tuple[Path, Path, Path]] = []
        moved_old: list[tuple[Path, Path]] = []
        moved_new: list[Path] = []
        restored = False
        original_archive = None
        try:
            if cfdrun_json:
                stage_model = stage / "model"
                if model_dir.is_dir():
                    shutil.copytree(model_dir, stage_model, symlinks=True)
                else:
                    stage_model.mkdir()
                # A fresh ordinary export cannot inherit a prior derived sidecar.
                (stage_model / "heat-ledger.json").unlink(missing_ok=True)
                _write_active_cfdrun(
                    stage_model, cfdrun_filename, cfdrun_json,
                    published_model_dir=model_dir, _prepared_bytes=published_payload,
                )
                if capture_snapshot:
                    # Validate before swapping either directory. This temporary
                    # context validates bytes; the published context is bound below.
                    CfdrunSnapshot(published_payload, stage, stage_model / active_path.name,
                                   expected_sha256=sha)
                changes.append((model_dir, stage_model, backup / "model"))
                _check_mesh_execution_budget(mesh_budget, "manifest staging")

            if replace_stls:
                stage_tri = stage / "triSurface"
                if tri_dir.is_dir():
                    shutil.copytree(tri_dir, stage_tri, symlinks=True)
                else:
                    stage_tri.mkdir()
                for path in stage_tri.iterdir():
                    _check_mesh_execution_budget(mesh_budget, "STL staging")
                    if (path.is_file() or path.is_symlink()) and path.suffix.lower() in (".stl", ".emesh"):
                        path.unlink()
                for filename, data in decoded_stls.items():
                    _check_mesh_execution_budget(mesh_budget, "STL staging")
                    (stage_tri / filename).write_bytes(data)
                    _check_mesh_execution_budget(mesh_budget, "STL staging")
                changes.append((tri_dir, stage_tri, backup / "triSurface"))

            if hybrid_heat is not None:
                _check_mesh_execution_budget(mesh_budget, "accepted liquid heat handoff")
                if liquid_manager is None:  # direct: the injected manager, see the docstring
                    raise ValueError("hybridHeat needs the liquid run manager of the session that ran the liquid run")
                compiled, ledger = liquid_manager().compile_hybrid_air_heat(
                    hybrid_heat["liquidRunId"], project=hybrid_heat["project"],
                    air_cfdrun=json.loads(published_payload),
                    tri_surface_dir=stage_tri if replace_stls else (
                        _archived_stl_batch_dir(case_dir, json.loads(published_payload)) or tri_dir),
                    sample_index=hybrid_heat["sampleIndex"], case_dir=case_dir)
                published_payload = (json.dumps(compiled, ensure_ascii=False, allow_nan=False,
                                                indent=2) + "\n").encode("utf-8")
                sha = hashlib.sha256(published_payload).hexdigest()
                (stage_model / active_path.name).write_bytes(published_payload)
                (stage_model / "heat-ledger.json").write_text(
                    json.dumps(ledger, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                    encoding="utf-8")
                CfdrunSnapshot(published_payload, stage, stage_model / active_path.name,
                               expected_sha256=sha)
                _check_mesh_execution_budget(mesh_budget, "accepted liquid heat handoff complete")

            if original_input_id is not None:
                if not capture_snapshot or not cfdrun_json or not decoded_stls:
                    raise ValueError("Original input archive needs one complete fresh uploaded batch")
                original_archive, archived_stage = _stage_original_air_inputs(
                    stage, case_dir, active_path.name, cfdrun_json, published_payload,
                    decoded_stls, original_input_id, original_request=original_request, mesh_budget=mesh_budget)
                changes.append((original_archive, archived_stage, backup / "original-air-input"))

            for live, staged, prior in changes:
                _check_mesh_execution_budget(mesh_budget, "input publication commit")
                live.parent.mkdir(parents=True, exist_ok=True)
                if live == original_archive and (live.exists() or live.is_symlink()):
                    raise ValueError("Original input archive id already exists")
                if live.exists():
                    live.rename(prior)
                    moved_old.append((prior, live))
                staged.rename(live)
                moved_new.append(live)
            snapshot = (CfdrunSnapshot(published_payload, case_dir, active_path, expected_sha256=sha,
                                       stl_batch_sha256=surface_batch_sha256(case_dir))
                        if capture_snapshot else None)
            _check_mesh_execution_budget(mesh_budget, "published input snapshot")
            restored = True
        except Exception:
            try:
                for live in reversed(moved_new):
                    shutil.rmtree(live)
                for prior, live in reversed(moved_old):
                    prior.rename(live)
                restored = True
            finally:
                if restored:
                    shutil.rmtree(scratch, ignore_errors=True)
            raise
        finally:
            if restored:
                shutil.rmtree(scratch, ignore_errors=True)

        return active_path, sha, snapshot


def _publish_case_input_snapshot(case_dir, cfdrun_filename, cfdrun_json, stl_files, *,
                                 replace_stls=False, mesh_budget=None, hybrid_heat=None, original_input_id=None,
                                 original_request=None, liquid_manager=None):
    """Own the actual batch bytes while its publication lock is held."""
    _, _, snapshot = _publish_case_input_batch(
        case_dir, cfdrun_filename, cfdrun_json, stl_files,
        replace_stls=replace_stls, capture_snapshot=True, mesh_budget=mesh_budget,
        hybrid_heat=hybrid_heat, original_input_id=original_input_id, original_request=original_request,
        liquid_manager=liquid_manager)
    return snapshot


# ── ported: job registry, cancellation and process-tree termination ─────────

# job_id -> state. Job ids are random, so one registry serves every session of this process.
JOBS: dict = {}
JOBS_LOCK = threading.Lock()


class _JobCancelledError(Exception):
    """A registered worker was cancelled before starting its next operation."""


def _new_job_state(lines: list[str], **fields) -> dict:
    return {
        "lines": lines, "done": False, "returncode": None, "proc": None,
        "cancelRequested": False, "cancelled": False,
        # Serializes tree termination with the worker's final cleanup. The
        # abort never owns the terminal state or a case lock.
        "_stop_lock": threading.Lock(), "_stop_started": False,
        **fields,
    }


def _raise_if_job_cancelled(job_id: str) -> None:
    with JOBS_LOCK:
        if JOBS[job_id].get("cancelRequested"):
            raise _JobCancelledError()


def _start_snapshot_input(proc, snapshot):
    from backend.aircooling.runner.cfdrun_transport import CfdrunInputFeeder
    feeder = CfdrunInputFeeder(proc.stdin.buffer, snapshot)
    try:
        feeder.start()
    except BaseException:
        proc.stdin.close()
        raise
    return feeder


def _join_snapshot_input(job_id: str, feeder) -> bool:
    if feeder is None:
        return False
    # Reap the child/tree before waiting for its writer to close the pipe.
    feeder.join()
    if feeder.error is not None:
        with JOBS_LOCK:
            JOBS[job_id]["lines"].append(f"[session] cfdrun input transfer failed: {feeder.error}")
        return True
    return False


def _spawn_job_process(job_id: str, command, **kwargs):
    # Cancellation and process publication are one transaction: an abort
    # accepted before the spawn cannot be followed by a late spawn.
    with JOBS_LOCK:
        job = JOBS[job_id]
        if job.get("cancelRequested"):
            raise _JobCancelledError()
        proc = subprocess.Popen(command, **kwargs)
        job["proc"] = proc
        return proc


def _finish_job(job_id: str, returncode: int, *, quality=None,
                cancelled_receipt: Path | None = None) -> None:
    """Publish terminal state only from the worker, after its own cleanup."""
    with JOBS_LOCK:
        job = JOBS[job_id]
        stop_lock = job["_stop_lock"]
    # This final fence also covers an abort arriving after stdout/receipt work.
    # The abort only try-acquires this lock while holding JOBS_LOCK.
    with stop_lock:
        with JOBS_LOCK:
            cancelled = job.get("cancelRequested", False)
            proc = job.get("proc")
            if not cancelled:
                if quality is not None:
                    job.update(quality)
                job.update(returncode=returncode, done=True)
                return
        if proc is not None:
            while _terminate_job_tree(proc) is False:
                time.sleep(0.1)
            proc.wait()
        if cancelled_receipt is not None:
            try:
                cancelled_receipt.unlink(missing_ok=True)
            except OSError as exc:
                with JOBS_LOCK:
                    job["lines"].append(f"[session] Cancelled receipt cleanup failed: {exc}")
        with JOBS_LOCK:
            job.pop("meshQualityReceipt", None)
            job.pop("solveAssessment", None)
            job.update(returncode=-15, cancelled=True, checkMeshOk=False, done=True)


def _join_job_cleanup(job_id: str, proc) -> None:
    if proc is None:
        return
    with JOBS_LOCK:
        job = JOBS[job_id]
        stop_lock = job["_stop_lock"]
    # A pipe may reach EOF while the abort is still stopping a
    # separately-sessioned MPI group. Wait for that cleanup before done.
    with stop_lock:
        with JOBS_LOCK:
            cancelled = job.get("cancelRequested", False)
        if cancelled:
            while _terminate_job_tree(proc) is False:
                time.sleep(0.1)
        elif proc.returncode is None:
            _terminate_job_tree(proc)
        proc.wait()


def _descendant_pgids(root_pid: int) -> set[int]:
    """Every process group under `root_pid`, the root's own included.

    The runner starts each mpirun step with `start_new_session=True`, so the
    solver ranks sit in a session of their own and a killpg on the runner's
    group never reaches them. /proc's children lists find them.
    """
    pgids: set[int] = set()
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            pgids.add(os.getpgid(pid))
        except (ProcessLookupError, PermissionError):
            continue
        task_dir = Path("/proc") / str(pid) / "task"
        try:
            for tid in task_dir.iterdir():
                try:
                    stack.extend(int(c) for c in (tid / "children").read_text().split())
                except (OSError, ValueError):
                    pass
        except OSError:
            pass
    return pgids


def _terminate_job_tree(proc: subprocess.Popen, grace: float = 8.0) -> bool:
    """SIGTERM every process group of the job, wait, SIGKILL what is left.

    SIGTERM first and a real wait, not SIGTERM-then-SIGKILL in the same
    breath: the runner terminates its own child group on SIGTERM, and a
    SIGKILL landing before it can do so is exactly what orphans the ranks.
    """
    # Retain known sessions if the launcher exits and children are reparented.
    pgids = set(getattr(proc, "_job_pgids", ()))
    pgids.update(_descendant_pgids(proc.pid))
    proc._job_pgids = pgids
    if proc.poll() is not None and not _any_alive(pgids):
        return True
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        if proc.poll() is None:
            pgids.update(_descendant_pgids(proc.pid))
        for pgid in pgids:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if proc.poll() is not None and not _any_alive(pgids):
                return True
            time.sleep(0.1)
    return proc.poll() is not None and not _any_alive(pgids)


def _any_alive(pgids: set[int]) -> bool:
    existing = set()
    for pgid in pgids:
        try:
            os.killpg(pgid, 0)
            existing.add(pgid)
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
    if not existing:
        return False
    if not sys.platform.startswith("linux"):
        return True
    # killpg(..., 0) also sees zombie ranks. They cannot compute or retain a
    # stdout pipe/case flock, and waiting for their unrelated reaper must not
    # permanently freeze a completed cancellation.
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                tail = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                state, group = tail[0], int(tail[2])
                if group in existing and state not in ("Z", "X"):
                    return True
            except FileNotFoundError:
                continue
            except (OSError, ValueError, IndexError):
                return True
    except OSError:
        return True
    return False


# ── ported: what a finished job reports ─────────────────────────────────────

_RUN_DIR_LINE_RE = re.compile(r"Created run directory:\s*(\S.*?)\s*$")


def _capture_solve_run_dir(line: str):
    """The run directory a job line names (the runner's own ``Created run directory:`` line), else None."""
    m = _RUN_DIR_LINE_RE.search(line or "")
    return Path(m.group(1)) if m else None


def _solve_job_assessment(*, case_dir: Path, run_dir: Path | None,
                          cfdrun_sha256: str) -> dict:
    """Relay this worker's existing verdict; never invent a convergence rule.

    The execution receipt binds the run to the immutable request. This is a
    report identity check, not a second native-field or physical acceptance.
    """
    case = case_dir.resolve()
    result = {"format": "air-solve-assessment/v1", "caseDir": str(case),
              "cfdrunSha256": cfdrun_sha256, "runDir": None,
              "reportSha256": None, "mode": None, "verdict": "UNKNOWN",
              "reasons": []}
    try:
        if run_dir is None:
            raise ValueError("this job did not publish a solve run directory")
        run = Path(run_dir)
        logs = case / "log"
        if logs.is_symlink() or run.is_symlink() or run.parent != logs:
            raise ValueError("solve run directory does not belong to this job's case")
        run = run.resolve(strict=True)
        if run.parent != logs.resolve(strict=True) or not run.is_dir():
            raise ValueError("solve run directory is not a direct case log directory")
        result["runDir"] = str(run)

        def read_object(path, limit):
            from stat import S_ISREG
            info = path.lstat()
            if not S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError(f"missing or invalid {path.name}")
            raw = path.read_bytes()
            after = path.lstat()
            identity = lambda value: (value.st_dev, value.st_ino, value.st_mode,
                                      value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if (not S_ISREG(after.st_mode) or identity(info) != identity(after)
                    or len(raw) != after.st_size):
                raise ValueError(f"{path.name} changed while reading")
            if len(raw) > limit:
                raise ValueError(f"oversized {path.name}")
            value = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite report")))
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} is not an object")
            return raw, value

        _, execution = read_object(run / "air-run-execution-receipt.json", 131072)
        source = execution.get("result", {})
        if (execution.get("format") != "air-run-execution-receipt/v1" or
                execution.get("status") != "accepted" or not isinstance(source, dict) or
                source.get("cfdrunSha256") != cfdrun_sha256 or
                source.get("runRelativePath") != run.relative_to(case).as_posix()):
            raise ValueError("execution receipt does not match this job's immutable input and run")
        raw, report = read_object(run / "convergence.json", 1048576)
        log = Path(report.get("log") or "")
        if log.parent != run or log.is_symlink() or not log.is_file():
            raise ValueError("convergence report log does not belong to this solve run")
        verdict = report.get("verdict")
        if verdict in ("TRANSIENT_COMPLETED", "TRANSIENT_INCOMPLETE"):
            leg = report.get("transient")
            if not isinstance(leg, dict) or type(leg.get("passed")) is not bool or leg["passed"] != (verdict == "TRANSIENT_COMPLETED"):
                raise ValueError("inconsistent transient completion report")
            mode, legs = "transient", [leg]
        elif verdict in ("CONVERGED", "NOT_CONVERGED", "INCONCLUSIVE"):
            residuals, sensors = report.get("residuals"), report.get("sensors")
            if not all(isinstance(leg, dict) and type(leg.get("passed")) is bool for leg in (residuals, sensors)):
                raise ValueError("missing steady convergence legs")
            # Each report is checked against the rule it states: steady ACU return air decides and the
            # residual leg is a diagnostic; reports written before that rule required both legs.
            if "residuals are diagnostics" in str(report.get("rule") or ""):
                expected = ("CONVERGED" if sensors["passed"] else
                            "INCONCLUSIVE" if sensors.get("status") in ("NO_SENSORS", "TOO_SHORT") else "NOT_CONVERGED")
                # the deciding leg's reasons first (e.g. "too-short: ..."), the diagnostics after
                legs = [sensors, residuals]
                # the energy balance closes too (a report from a solve with the ACU enthalpy monitors
                # carries the leg)
                energy = report.get("energy")
                if energy is not None:
                    if not isinstance(energy, dict) or type(energy.get("passed")) is not bool:
                        raise ValueError("malformed energy-balance leg")
                    if expected == "CONVERGED" and not energy["passed"]:
                        expected = ("INCONCLUSIVE" if energy.get("status") in ("NO_EVIDENCE", "TOO_SHORT")
                                    else "NOT_CONVERGED")
                    legs = [sensors, energy, residuals]
            else:
                expected = ("CONVERGED" if residuals["passed"] and sensors["passed"] else
                            "INCONCLUSIVE" if residuals["passed"] and sensors.get("status") in ("NO_SENSORS", "TOO_SHORT") else "NOT_CONVERGED")
                legs = [residuals, sensors]
            if verdict != expected:
                raise ValueError("inconsistent steady convergence report")
            mode = "steady"
        else:
            raise ValueError("unknown convergence verdict")
        result.update(mode=mode, verdict=verdict, reportSha256=hashlib.sha256(raw).hexdigest(),
                      reasons=[reason for leg in legs for reason in leg.get("reasons", []) if isinstance(reason, str)])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result["reasons"] = [str(exc)]
    return result


_CHECK_MESH_FAILED_RE = re.compile(r"Failed\s+(\d+)\s+mesh checks?\.", re.IGNORECASE)
_CHECK_MESH_CONCAVE_RE = re.compile(r"Concave cells \(using face planes\) found")


def _summarize_check_mesh_result(lines: list[str], returncode: int | None) -> dict:
    """Interpret checkMesh output into structured warnings: ``checkMeshOk``, ``failedChecks`` and a
    ``meshWarnings`` list with severity-levelled entries."""
    from backend.aircooling.runner.mesh_quality import parse_checkmesh_warnings

    failed_checks: int | None = None
    explicit_failure = False
    explicit_success = False

    for line in lines:
        match = _CHECK_MESH_FAILED_RE.search(line)
        if match:
            failed_checks = int(match.group(1))
            explicit_failure = failed_checks > 0
        if "Mesh OK." in line:
            explicit_success = True
        if "[server] checkMesh failed:" in line:
            explicit_failure = True

    # Concave cells are reported, never refused -- the runner's strict gate drops them from the failed
    # count the same way. Every cut-cell mesh has concave cut cells.
    if failed_checks and any(_CHECK_MESH_CONCAVE_RE.search(line) for line in lines):
        failed_checks -= 1
        if failed_checks == 0 and not any("[server] checkMesh failed:" in line for line in lines):
            explicit_failure = False
            explicit_success = True

    has_verdict = explicit_success or failed_checks is not None
    ok = returncode == 0 and has_verdict and not explicit_failure

    full_output = "\n".join(lines)
    warnings = parse_checkmesh_warnings(full_output)
    if not ok and not warnings:
        command_failed = returncode not in (None, 0) or "[server] checkMesh failed:" in full_output
        code = ("failed_checks" if failed_checks and failed_checks > 0
                else "checkmesh_command_failed" if command_failed
                else "incomplete_checkmesh")
        warnings = [{
            "level": 2,
            "code": code,
            "message": (f"checkMesh failed {failed_checks} quality check(s)."
                        if code == "failed_checks" else
                        "checkMesh command failed before a usable quality report."
                        if command_failed else "checkMesh did not produce a complete quality verdict."),
        }]

    return {
        "checkMeshOk": ok,
        "failedChecks": failed_checks,
        "hasExplicitSuccess": explicit_success,
        "meshWarnings": warnings,
    }


def _summarize_solve_mesh_quality_rejection(lines: list[str], returncode: int | None) -> dict | None:
    """Carry an explicit runner quality refusal into the solve job's verdict.

    A solver, boundary-condition or input error alone says nothing about mesh
    quality. This helper can report a refusal; it never authorizes a mesh.
    """
    if returncode in (None, 0):
        return None
    refusals = (
        "RuntimeError: air solve requires accepted strict mesh quality:",
        "RuntimeError: source-aware air solve requires accepted strict mesh quality:",
    )
    if not any(line.strip().startswith(refusals) for line in lines):
        return None
    result = _summarize_check_mesh_result(lines, returncode)
    result["checkMeshOk"] = False
    return result


def _summarize_mesh_run_quality(lines: list[str], returncode: int | None) -> dict:
    """Read the final checkMesh pass from a mesh job, after any repair/recheck.

    A mesher may print its own intermediate ``Mesh OK`` or failed checks.
    Only the runner's final quality step describes the mesh it delivers. A
    completed OpenFOAM command can exit zero despite ``Failed N mesh checks``.
    """
    steps = [i for i, line in enumerate(lines) if "checkMesh (final quality check)" in line]
    if not steps:
        return {
            "checkMeshOk": False,
            "failedChecks": None,
            "meshWarnings": [{"level": 2, "code": "missing_final_checkmesh",
                              "message": "Mesh job did not run a final checkMesh quality check."}],
        }
    quality_lines = lines[steps[-1] + 1:]
    verdicts = [i for i, line in enumerate(quality_lines)
                if _CHECK_MESH_FAILED_RE.search(line) or "Mesh OK." in line]
    if not verdicts:
        return {
            "checkMeshOk": False,
            "failedChecks": None,
            "meshWarnings": [{"level": 2, "code": "incomplete_checkmesh",
                              "message": "Final checkMesh did not produce a quality verdict."}],
        }

    last = verdicts[-1]
    previous = verdicts[-2] + 1 if len(verdicts) > 1 else 0
    report = quality_lines[previous:last + 1]
    starts = [i for i, line in enumerate(report) if "Checking topology..." in line]
    if not starts:
        starts = [i for i, line in enumerate(report) if "Checking geometry..." in line]
    if starts:
        report = report[starts[-1]:]
    summary = _summarize_check_mesh_result(report, returncode)
    if summary["failedChecks"] is None and summary["hasExplicitSuccess"]:
        summary["failedChecks"] = 0
    if not summary["checkMeshOk"] and not summary["meshWarnings"]:
        summary["meshWarnings"] = [{
            "level": 2,
            "code": "failed_checks",
            "message": f"Final checkMesh failed {summary['failedChecks']} quality check(s).",
        }]
    return summary


# ── end of the ported functions ─────────────────────────────────────────────

def job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """The status document of a job (the server's ``/api/job-status``), or None for an unknown id."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return None if job is None else {
            "jobId": job_id,
            "done": job["done"],
            "returncode": job["returncode"],
            "lineCount": len(job["lines"]),
            "checkMeshOk": job.get("checkMeshOk"),
            "failedChecks": job.get("failedChecks"),
            "meshWarnings": job.get("meshWarnings"),
            "solveAssessment": job.get("solveAssessment"),
            "solverFailure": job.get("solverFailure"),
            "scanoResult": job.get("scanoResult"),
            "cancelRequested": bool(job.get("cancelRequested")),
            "cancelled": bool(job.get("cancelled")),
        }


def abort_job(job_id: str, reason: str = "[session] Job aborted") -> Dict[str, Any]:
    """Cancel a job and stop its process tree (the server's ``/api/abort-job``); the worker publishes the
    terminal state. A finished job is left as it ended."""
    terminate = False
    proc = None
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return {"error": "job not found"}
        if job["done"]:
            # A late request cannot turn a natural completion into cancellation.
            return {"status": "aborted" if job.get("cancelled") else "already_done", "jobId": job_id, "done": True,
                    "cancelRequested": bool(job.get("cancelRequested")), "cancelled": bool(job.get("cancelled"))}
        if not job.get("cancelRequested"):
            job["cancelRequested"] = True
            job["checkMeshOk"] = False
            job["lines"].append(reason)
        proc = job.get("proc")
        if proc is not None and not job.get("_stop_started"):
            job["_stop_started"] = True
            # Acquire before releasing JOBS_LOCK so the worker cannot publish
            # done between accepting cancellation and starting tree cleanup.
            terminate = job["_stop_lock"].acquire(blocking=False)
    if terminate:
        stop_error = None
        try:
            if _terminate_job_tree(proc) is False:
                stop_error = "Job process tree has not stopped; cancellation remains pending."
        except Exception as exc:
            stop_error = f"Job process tree could not be stopped: {exc}"
        finally:
            job["_stop_lock"].release()
        if stop_error is not None:
            with JOBS_LOCK:
                job["_stop_started"] = False
                job["lines"].append(f"[session] {stop_error}")
            return {"error": stop_error, "jobId": job_id, "done": False, "cancelRequested": True, "cancelled": False}
    with JOBS_LOCK:
        done = job["done"]
        return {"status": "aborted" if done else "aborting", "jobId": job_id, "done": done,
                "cancelRequested": True, "cancelled": bool(job.get("cancelled"))}


def build_run_command(cfdrun_path: Path, cfdrun_sha256: str, stl_batch_sha256: str, *, mesh_only: bool,
                      solve_only: bool, solver_engine: str, mesh_budget_args: Iterable[str] = ()) -> List[str]:
    """The runner command of one job: this interpreter, the manifest on stdin, its identity on the command line."""
    flags: List[str] = []
    if mesh_only:
        flags.append("--mesh-only")
    elif solve_only:
        flags.append("--solve-only")
    if solver_engine != gpu_newton.OPENFOAM:
        flags += ["--solver-engine", solver_engine]
    return [sys.executable, "-m", "backend.aircooling.runner.run_cfd",
            "--from-cfdrun", str(cfdrun_path), "--cfdrun-stdin",
            "--expected-cfdrun-sha256", cfdrun_sha256,
            "--expected-stl-batch-sha256", stl_batch_sha256,
            *flags, *mesh_budget_args]


# ── the environment of a run ────────────────────────────────────────────────

_GPU_TOKEN = re.compile(r"^(GPU-[0-9a-fA-F-]+|[0-9]+)$")


def nvidia_smi_cards() -> List[Tuple[str, str]]:
    """``(index, uuid)`` of every card ``nvidia-smi`` lists (empty when it cannot be asked)."""
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in done.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit() and parts[1]:
            rows.append((parts[0], parts[1]))
    return rows


def resolve_gpus(gpus: Any, cards: Optional[List[Tuple[str, str]]] = None) -> List[str]:
    """Cards named by ``nvidia-smi`` index or ``GPU-...`` UUID (a comma separated string, a number, or a sequence
    of either) as UUIDs, each once, in the order given. An ``nvidia-smi`` index is not the index CUDA uses, so
    the solver is always handed UUIDs."""
    if gpus is None:
        return []
    if isinstance(gpus, bool):
        raise ValueError(f"gpus must name cards by index or GPU-... UUID, got {gpus!r}")
    tokens = gpus.split(",") if isinstance(gpus, str) else [gpus] if isinstance(gpus, int) else list(gpus)
    found: List[str] = []
    for token in tokens:
        text = str(token).strip()
        if isinstance(token, bool) or not _GPU_TOKEN.match(text):
            raise ValueError(f"gpus must name cards by index or GPU-... UUID, got {token!r}")
        if not text.startswith("GPU-"):
            if cards is None:
                cards = nvidia_smi_cards()
            uuid_text = next((card_uuid for index, card_uuid in cards if index == text), None)
            if uuid_text is None:
                raise ValueError(f"gpu {text}: nvidia-smi lists no such card")
            text = uuid_text
        if text not in found:
            found.append(text)
    return found


def missing_gpu_solver_modules(python: Optional[Path] = None) -> List[str]:
    """Packages the GPU solver imports that its interpreter (default: this one) lacks."""
    if python is None or Path(python) == Path(sys.executable):
        return [name for name in gpu_newton.REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    code = ("import importlib.util as u\n"
            f"print(' '.join(m for m in {tuple(gpu_newton.REQUIRED_MODULES)!r} if u.find_spec(m) is None))")
    try:
        done = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=120,
                              env={**os.environ, "PYTHONNOUSERSITE": "1"})
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"({python} could not be asked: {exc})"]
    return done.stdout.split()


def run_environment(base: Optional[Dict[str, str]] = None, *, gpu: Optional[str] = None) -> Dict[str, str]:
    """The environment of a run's child processes: what the server's launcher sets up before the server starts.

    OpenFOAM and MPI come entirely from this interpreter's environment (``sys.prefix``): its ``bin`` leads
    ``PATH``, every ``PATH`` / ``LD_LIBRARY_PATH`` entry of another OpenFOAM or of Open MPI is dropped, and the
    ``FOAM_*`` / ``WM_PROJECT_DIR`` / MPI variables point at it; each ``mpirun`` stays on this host
    (``UCX_TLS=self,sm`` unless the caller set it); this tree's ``src`` leads ``PYTHONPATH``.

    ``gpu``: None leaves the GPU solver's variables alone; ``""`` offers every card; a list of cards (indices or
    UUIDs) hands them over as UUIDs in :data:`GPU_ENV`.
    """
    env = dict(os.environ if base is None else base)
    prefix = sys.prefix

    def kept(text: str) -> List[str]:
        return [part for part in text.split(":") if part and "openfoam" not in part.lower()
                and "/openmpi/" not in part]

    env["PATH"] = ":".join([f"{prefix}/bin", *kept(env.get("PATH", ""))])
    libraries = kept(env.get("LD_LIBRARY_PATH", ""))
    if libraries:
        env["LD_LIBRARY_PATH"] = ":".join(libraries)
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env.update(WM_PROJECT_DIR=prefix, CONDA_PREFIX=prefix, FOAM_LIBBIN=f"{prefix}/lib", FOAM_APPBIN=f"{prefix}/bin",
               FOAM_ETC=f"{prefix}/etc", FOAM_MPI="sys-mpich", MPI_ARCH_PATH=prefix)
    if not env.get("UCX_TLS"):
        env["UCX_TLS"] = "self,sm"
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{REPO / 'src'}" + (f":{inherited}" if inherited else "")
    env["PYTHONUNBUFFERED"] = "1"
    if gpu is not None:
        env[gpu_newton.ENABLE_ENV] = "1"
        uuids = resolve_gpus(gpu) if gpu else []
        if uuids:
            env[GPU_ENV] = ",".join(uuids)
        else:
            env.pop(GPU_ENV, None)
    return env


# ── the run job ─────────────────────────────────────────────────────────────

def _refuse(status: int, error: str, *, code: Optional[str] = None, path: str = RUN_PATH) -> ServerError:
    return ServerError(status, {**({"code": code} if code else {}), "error": error}, path)


def _liquid_refusal(exc: Exception, path: str) -> Optional[ServerError]:
    """A liquid run manager's own error as the refusal the server answers with, else None."""
    from backend.liquidcooling.run_manager import RunConflictError, RunNotFoundError
    if isinstance(exc, RunNotFoundError):
        return _refuse(404, f"liquid run {exc.args[0]!r} was not found", path=path)
    if isinstance(exc, RunConflictError):
        return _refuse(409, str(exc), path=path)
    return None


def run_mesh(data: Dict[str, Any], request_bytes: bytes, *, environment: Dict[str, str],
             liquid_manager: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Publish one run body into its case and start the runner on it: the server's ``/api/run-mesh``.

    ``data`` is the editor's body with ``caseDir``; ``request_bytes`` its JSON text, archived with a mesh job's
    inputs. Returns ``{jobId, caseDir, cfdrunSha256}`` at once; the job runs in a worker thread of this process
    (:func:`job_status`, :func:`abort_job`). A refusal raises :class:`ServerError` with the server's status code.
    """
    request_budget = MeshBudget()
    cfdrun_json = data.get("cfdrunJson") if data.get("cfdrunJson") is not None else data.get("cfdExportJson")
    cfdrun_filename: str = data.get("cfdrunFilename", "project.cfdrun") or "project.cfdrun"
    stl_files: dict = data.get("stlFiles", {})   # {filename: base64_content}
    mesh_only: bool = data.get("meshOnly", False)
    solve_only: bool = data.get("solveOnly", False)
    mesh_budget = None if solve_only and not mesh_only else request_budget
    if data.get("aiInit"):
        raise _refuse(400, "AI initialisation is not part of this distribution", code="ai_init_unavailable")
    try:
        solver_engine = gpu_newton.resolve_engine(data.get("solverEngine")) if not mesh_only else gpu_newton.OPENFOAM
        if solver_engine == gpu_newton.GPU_NEWTON:
            # direct: no launcher offers the engine, so nothing is asked of its enable flag; what the launcher
            # checks before it offers the engine (the solver's packages) is checked here
            missing = missing_gpu_solver_modules(gpu_newton.configuration(environment)["python"])
            if missing:
                raise ValueError("the GPU air solver is not available, missing Python packages: "
                                 + " ".join(missing))
    except (OSError, ValueError) as exc:
        raise _refuse(400, str(exc), code="solver_engine_invalid") from exc
    if data.get("patchUpdate"):
        raise _refuse(400, "patch updates of an existing mesh are not part of the in-process session",
                      code="patch_update_unavailable")
    try:
        raw_case = data.get("caseDir")
        if not isinstance(raw_case, str) or not raw_case.strip():
            raise ValueError("caseDir must be a nonempty directory path.")
        case_dir = Path(raw_case).expanduser().resolve(strict=True)
        if not case_dir.is_dir():
            raise ValueError("caseDir must name an existing directory.")
    except (OSError, ValueError) as exc:
        raise _refuse(400, str(exc), code="case_dir_invalid") from exc

    input_id = str(uuid.uuid4())
    job_id = input_id[:8]
    original_input_id = input_id if not solve_only and cfdrun_json and stl_files else None
    try:
        if mesh_budget is not None and "meshPreparationElapsedSeconds" in data:
            mesh_budget.charge_preparation(data["meshPreparationElapsedSeconds"])
        input_snapshot = _publish_case_input_snapshot(
            case_dir, cfdrun_filename, cfdrun_json, stl_files, mesh_budget=mesh_budget,
            hybrid_heat=data.get("hybridHeat"), original_input_id=original_input_id,
            original_request=(request_bytes, data) if original_input_id is not None else None,
            liquid_manager=liquid_manager,
        )
        mesh_budget_args = mesh_budget.cli_args() if mesh_budget is not None else []
    except MeshTimeoutError as exc:
        raise _refuse(408, str(exc), code="mesh_time_budget_exhausted") from exc
    except CaseExecutionBusyError as exc:
        raise _refuse(409, str(exc), code="case_busy") from exc
    except ValueError as exc:
        raise _refuse(400, str(exc)) from exc
    except OSError as exc:
        raise _refuse(500, f"Case upload failed: {exc}") from exc
    except Exception as exc:
        refusal = _liquid_refusal(exc, RUN_PATH) if data.get("hybridHeat") is not None else None
        if refusal is None:
            raise
        raise refusal from exc

    cfdrun_path, cfdrun_sha = input_snapshot.logical_path, input_snapshot.sha256
    cmd = build_run_command(cfdrun_path, cfdrun_sha, input_snapshot.stl_batch_sha256, mesh_only=mesh_only,
                            solve_only=solve_only, solver_engine=solver_engine, mesh_budget_args=mesh_budget_args)
    cmd_str = " ".join(cmd)
    with JOBS_LOCK:
        JOBS[job_id] = _new_job_state([f"[session] Start command: {cmd_str}"],
                                      startedAt=dt.datetime.now().strftime("%Y-%m-%d-%H-%M"), caseDir=str(case_dir))
        if original_input_id is not None:
            JOBS[job_id]["lines"].append(
                f"[session] Original input archive: {case_dir / 'log' / 'air-inputs' / original_input_id}")

    def _run() -> None:
        proc = None
        feeder = None
        worker_failed = False
        solve_run_dir = None
        try:
            _raise_if_job_cancelled(job_id)
            _check_mesh_execution_budget(mesh_budget, "runner spawn")
            proc = _spawn_job_process(
                job_id, cmd,
                cwd=str(case_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**environment, "PYTHONUNBUFFERED": "1"},
                start_new_session=True,  # a session of its own: the abort signals its process groups
            )
            feeder = _start_snapshot_input(proc, input_snapshot)
            for line in iter(proc.stdout.readline, ""):
                stripped = line.rstrip()
                if stripped:
                    with JOBS_LOCK:
                        JOBS[job_id]["lines"].append(stripped)
                    if not mesh_only:
                        solve_run_dir = _capture_solve_run_dir(stripped) or solve_run_dir
            proc.wait()
        except _JobCancelledError:
            pass
        except Exception as exc:
            worker_failed = True
            with JOBS_LOCK:
                JOBS[job_id]["lines"].append(f"[session] Start failed: {exc}")
                JOBS[job_id]["lines"].append(f"[session] Command: {cmd_str}")
                JOBS[job_id]["lines"].append(f"[session] Working directory: {case_dir}")
        finally:
            _join_job_cleanup(job_id, proc)
            input_failed = _join_snapshot_input(job_id, feeder)
            rc = -1 if worker_failed or input_failed else (proc.returncode if proc else -1)
            if rc == 0 and mesh_only and mesh_budget is not None:
                try:
                    mesh_budget.check("session mesh completion")
                except MeshTimeoutError as exc:
                    rc = 124
                    with JOBS_LOCK:
                        JOBS[job_id]["lines"].append(str(exc))
            quality = None
            if not solve_only:
                with JOBS_LOCK:
                    quality_lines = list(JOBS[job_id]["lines"])
                quality = _summarize_mesh_run_quality(quality_lines, rc)
            if solve_only:
                with JOBS_LOCK:
                    quality_lines = list(JOBS[job_id]["lines"])
                quality = _summarize_solve_mesh_quality_rejection(quality_lines, rc)
            if not mesh_only:
                quality = {**(quality or {}), "solveAssessment": _solve_job_assessment(
                    case_dir=case_dir, run_dir=solve_run_dir, cfdrun_sha256=cfdrun_sha,
                )} if rc == 0 else quality
            if rc != 0 and solver_engine != gpu_newton.OPENFOAM and solve_run_dir is not None:
                # the GPU solver ran out of memory: the status says what to do (more cards)
                reason = gpu_newton.failure_reason(solve_run_dir)
                if reason:
                    quality = {**(quality or {}), "solverFailure": {"code": gpu_newton.OUT_OF_MEMORY,
                                                                    "error": reason}}
            if rc == 0 and not solve_only and original_input_id is not None:
                # the uploaded batch this mesh came from: what a later edit is compared with
                try:
                    (case_dir / "Mesh" / MESH_SOURCE_RECORD).write_text(
                        json.dumps({"inputId": original_input_id}) + "\n")
                except OSError:
                    pass
            if rc == 0:
                # the marker ParaView opens the Mesh directory with
                foam_file = case_dir / "Mesh" / "case.foam"
                if not foam_file.exists():
                    try:
                        foam_file.touch()
                    except Exception:
                        pass
                with JOBS_LOCK:
                    JOBS[job_id]["lines"].append(f"[session] case.foam written to {foam_file}")
            _finish_job(
                job_id, rc, quality=quality,
                cancelled_receipt=(case_dir / "mesh-quality-receipt.json")
                if proc is not None and not solve_only else None,
            )

    threading.Thread(target=_run, name=f"air-job-{job_id}", daemon=True).start()
    return {"jobId": job_id, "caseDir": str(case_dir), "cfdrunSha256": cfdrun_sha}


# ── liquid run endpoints ────────────────────────────────────────────────────

_LIQUID_RUNS = "/api/liquid/runs"
_RUN_PATH = re.compile(
    r"^/api/liquid/runs/([A-Za-z0-9_-]{1,64})(?:/(frames|result|checkpoint|executed-input|heat-audit|saved|start|pause|resume|cancel|step|inputs|equilibrium|restore))?$"
)


def _liquid_error(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    from backend.liquidcooling.run_manager import RunConflictError, RunInputError, RunNotFoundError
    if isinstance(exc, RunNotFoundError):
        return 404, {"error": f"liquid run {exc.args[0]!r} was not found"}
    if isinstance(exc, RunConflictError):
        return 409, {"error": str(exc)}
    if isinstance(exc, (RunInputError, ValueError, TypeError, KeyError)):
        return 400, {"error": str(exc)}
    return 500, {"error": str(exc) or "liquid runtime request failed"}


def _liquid_reply(status: int, payload: Any) -> Tuple[int, Any]:
    # the server's reply crosses HTTP as strict JSON: the caller gets the same plain, finite document
    return status, json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def liquid_request(manager: Callable[[], Any], method: str, path: str, body: Any, *,
                   case_dir: Optional[Path]) -> Tuple[int, Any]:
    """One liquid run request as ``(status, document)``: the server's liquid runtime endpoints, answered by
    ``manager()`` (a ``LiquidRunManager``). The listing of saved runs is not offered."""
    parsed = urlparse(path)
    route, query = parsed.path, parse_qs(parsed.query)
    if method == "GET":
        match = _RUN_PATH.fullmatch(route)
        if match is None or route == _LIQUID_RUNS:
            return 404, {"error": "unknown liquid runtime endpoint"}
        run_id, action = match.groups()
        try:
            runs = manager()
            if action is None:
                payload = runs.get(run_id)
            elif action == "frames":
                raw_after = query.get("after", ["-1"])[0]
                try:
                    after = int(raw_after)
                except (TypeError, ValueError) as exc:
                    raise ValueError("after must be an integer") from exc
                payload = runs.frames(run_id, after=after)
            elif action == "result":
                payload = runs.result(run_id)
            elif action == "checkpoint":
                payload = runs.checkpoint(run_id)
            elif action == "executed-input":
                payload = runs.executed_input(run_id)
            elif action == "heat-audit":
                if set(query) != {"project_uid"} or len(query["project_uid"]) != 1:
                    raise ValueError("heat-audit requires one project_uid query parameter")
                payload = runs.heat_audit(run_id, project_uid=query["project_uid"][0])
            else:
                return 404, {"error": "unknown liquid runtime GET endpoint"}
            return _liquid_reply(200, payload)
        except Exception as exc:
            return _liquid_error(exc)
    if method != "POST":
        return 405, {"error": f"liquid runtime endpoints take GET and POST, not {method}"}
    try:
        body = {} if body is None else body
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        runs = manager()
        if route == _LIQUID_RUNS:
            if case_dir is None:
                raise ValueError("No case directory set")
            checkpoint = body.get("checkpoint")
            liquidrun = body.get("liquidrun")
            # A checkpoint carries the exact liquidrun it was made from; a
            # resume may send only the checkpoint.
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
            return _liquid_reply(201, runs.create(
                liquidrun, case_dir=case_dir, controllers=controllers,
                project_revision=body.get("project_revision"), checkpoint=checkpoint))
        match = _RUN_PATH.fullmatch(route)
        if match is None:
            return 404, {"error": "unknown liquid runtime endpoint"}
        run_id, operation = match.groups()
        if operation == "restore":
            if body:
                raise ValueError("restore takes an empty JSON object")
            return _liquid_reply(200, runs.restore(run_id, case_dir=case_dir))
        if operation == "inputs":
            allowed = {"heat_multiplier", "pump_rpm_ratios", "valve_openings", "primary_inlet_K"}
            if not body or set(body) - allowed:
                raise ValueError("inputs accepts heat_multiplier, pump_rpm_ratios, valve_openings and "
                                 "primary_inlet_K only")
            return _liquid_reply(200, runs.set_inputs(run_id, **body))
        if operation == "equilibrium":
            if set(body) - {"heat_multiplier"}:
                raise ValueError("equilibrium accepts only optional heat_multiplier")
            return _liquid_reply(200, runs.equilibrium(run_id, heat_multiplier=body.get("heat_multiplier")))
        if operation not in {"start", "pause", "resume", "cancel", "step"}:
            return 404, {"error": "unknown liquid runtime POST endpoint"}
        speed = body.get("speed") if operation in {"start", "resume"} else None
        return _liquid_reply(200, runs.operate(run_id, operation, speed=speed))
    except Exception as exc:
        return _liquid_error(exc)


# ── the session ─────────────────────────────────────────────────────────────

class DirectSession:
    """One scripted run's session, in this process. Use as a context manager, or ``start()`` / ``stop()``.

    The constructor takes what the server's client takes (``work_dir`` is where that client kept its private
    credentials; nothing is written there). ``gpu``: the cards of the GPU air solver -- None: the engine is not
    asked for (its variables are left alone); ``""``: any card; else indices or UUIDs, comma separated.
    ``liquid_manager``: the liquid run manager to use (default: one of this session's own, created on the first
    liquid request, which is when torch is imported).
    """

    def __init__(self, case_dir: Path, work_dir: Path, *, app: str = "api", gpu: Optional[str] = None,
                 liquid_manager: Any = None, **_ignored: Any):
        self.case_dir = Path(case_dir).resolve()
        self.work_dir = Path(work_dir)
        self.app = app
        self.gpu = gpu
        self.port = 0                                   # no port: kept for the caller's record of the session
        self.command: List[str] = [sys.executable, "-m", "backend.aircooling.runner.run_cfd"]
        self.log_path = self.work_dir / "session.log"
        self.startup_seconds: Optional[float] = None
        self.stop_report: Dict[str, Any] = {}
        self.environment: Optional[Dict[str, str]] = None
        self.job_ids: List[str] = []
        self._liquid_manager = liquid_manager
        self._liquid_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "DirectSession":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        started = time.perf_counter()
        self.environment = run_environment(gpu=self.gpu)
        self._log(f"in-process session for {self.case_dir}; python {sys.executable}; "
                  f"gpu {'not asked' if self.gpu is None else self.environment.get(GPU_ENV) or 'any card'}")
        self.startup_seconds = time.perf_counter() - started

    def stop(self, grace: Optional[float] = None) -> None:
        """Stop what this session still runs: unfinished jobs (their whole process tree) and active liquid runs."""
        report: Dict[str, Any] = {"jobsAborted": [], "liquidRunsCancelled": []}
        for job_id in self.job_ids:
            status = job_status(job_id)
            if status is not None and not status["done"]:
                abort_job(job_id, "[session] Job aborted: the session stopped")
                self._await_done(job_id, 60.0)
                report["jobsAborted"].append(job_id)
        manager = self._liquid_manager
        if manager is not None:
            try:
                for run_id, row in manager.loaded_runs().items():
                    if row.get("status") in ("running", "paused") and not row.get("read_only"):
                        manager.operate(run_id, "cancel")
                        report["liquidRunsCancelled"].append(run_id)
            except Exception as exc:  # stopping is best effort; the report says what was left
                report["liquidError"] = f"{type(exc).__name__}: {exc}"
        self.stop_report = report
        self._log(f"session stopped: {json.dumps(report)}")

    def _log(self, text: str) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as stream:
                stream.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {text}\n")
        except OSError:
            pass

    # -- jobs --------------------------------------------------------------
    def submit_run(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.environment is None:
            self.start()
        data = {**body, "caseDir": str(self.case_dir)}
        reply = run_mesh(data, json.dumps(data).encode("utf-8"), environment=self.environment,
                         liquid_manager=self.liquid_manager)
        self.job_ids.append(reply["jobId"])
        kind = "mesh" if body.get("meshOnly") else "solve" if body.get("solveOnly") else "mesh+solve"
        self._log(f"job {reply['jobId']} ({kind}) started; manifest sha256 {reply['cfdrunSha256']}")
        return reply

    def job_status(self, job_id: str) -> Dict[str, Any]:
        status = job_status(job_id)
        if status is None:
            raise _refuse(404, "job not found", path="/api/job-status")
        return status

    def abort(self, job_id: str) -> Any:
        return abort_job(job_id)

    def _await_done(self, job_id: str, seconds: float) -> Dict[str, Any]:
        deadline = time.monotonic() + seconds
        status = self.job_status(job_id)
        while not status.get("done") and time.monotonic() < deadline:
            time.sleep(0.2)
            status = self.job_status(job_id)
        return status

    def wait_job(self, job_id: str, *, timeout: float, poll: float = 0.5) -> Dict[str, Any]:
        """Wait until the job is done. Past ``timeout`` seconds the job is aborted (its whole process tree is
        stopped) and its final status returned with ``timedOut``."""
        started = time.monotonic()
        while True:
            status = self.job_status(job_id)
            if status.get("done"):
                status["timedOut"] = False
                self._log(f"job {job_id} done, returncode {status.get('returncode')}")
                return status
            if time.monotonic() - started > timeout:
                abort = abort_job(job_id, f"[session] Job aborted: it exceeded {timeout:g} s")
                status = self._await_done(job_id, 60.0)
                status.update(timedOut=True, abortReply=abort)
                self._log(f"job {job_id} exceeded {timeout:g} s and was aborted")
                return status
            time.sleep(poll)

    def job_log(self, job_id: str, timeout: float = 120.0) -> Tuple[List[str], Optional[dict]]:
        """Every captured line of a job and, once it is done, its final document (else None)."""
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                raise _refuse(404, "job not found", path="/api/log-stream")
            lines = list(job["lines"])
            if not job["done"]:
                return lines, None
            final = {"done": True, "returncode": job["returncode"], "checkMeshOk": job.get("checkMeshOk"),
                     "failedChecks": job.get("failedChecks"), "cancelRequested": bool(job.get("cancelRequested")),
                     "cancelled": bool(job.get("cancelled"))}
            for key in ("solveAssessment", "meshWarnings", "solverFailure"):
                if job.get(key) is not None:
                    final[key] = job[key]
        return lines, final

    # -- liquid ------------------------------------------------------------
    def liquid_manager(self) -> Any:
        """This session's liquid run manager (created on first use)."""
        with self._liquid_lock:
            if self._liquid_manager is None:
                from backend.liquidcooling.run_manager import LiquidRunManager
                self._liquid_manager = LiquidRunManager()
            return self._liquid_manager

    def request(self, method: str, path: str, payload: Optional[dict] = None, *,
                timeout: float = 60.0) -> Tuple[int, Any]:
        if urlparse(path).path.startswith(_LIQUID_RUNS):
            return liquid_request(self.liquid_manager, method, path, payload, case_dir=self.case_dir)
        return 404, {"error": f"{path} is not available in the in-process session"}

    def call(self, method: str, path: str, payload: Optional[dict] = None, *, timeout: float = 60.0,
             expect: Tuple[int, ...] = (200,)) -> Any:
        """One liquid run request; a status outside ``expect`` raises :class:`ServerError` (a create answers 201)."""
        status, body = self.request(method, path, payload, timeout=timeout)
        if status not in expect:
            raise ServerError(status, body, path)
        return body
