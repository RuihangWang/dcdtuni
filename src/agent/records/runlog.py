"""RunLogger: one exclusive SGT run directory with append-only streams and a physics-call ledger.

The contract of the frozen ``harness/runlog.py``, on an injected
:class:`~agent.records.store.RunStore`:

- ``<root>/logger/<YYYY-MM-DD_HHMMSS_SGT>__<label>-<8 hex>/`` is created
  exclusively and never reused; ``config.json`` and ``identity.json`` are
  written once; ``events``/``metrics``/``physics_calls``/``checkpoints`` are
  JSON-lines files that only grow; ``final_status.json`` closes the run.
- The caller's identity must declare ``model``, ``data``, ``seed`` and
  ``purpose`` (``"not used"`` is a valid answer); the generated
  ``case``/``run_id``/``source`` fields cannot be overridden by it.
- ``physics_call`` needs an evaluator identity and measured costs; it records
  the project file and, for a ``agent.scripted_api`` run, the ``result.json`` path,
  each with its SHA-256.
- As in the frozen logger, a file inside the repository is recorded by its
  repository-relative path (an absolute path only outside it), and the
  identity names the store by ``case_id`` (the root's name) and ``store_root``.

``code_identity()`` records the Git state of the checkout the trunk runs from
and the SHA-256 of this module and :mod:`agent.records.store` keyed by package
path (``agent/records/...``), so it works from any root, installed or not.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from agent.records.store import (PathLike, RunStore, SGT, checked_id, checked_run_id, json_bytes,
                                 repo_relative_or_absolute, sha256_bytes, timestamp_sgt)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # src/agent
CONTRACT_MODULES = ("agent/records/store.py", "agent/records/runlog.py")
STREAMS = ("events.jsonl", "metrics.jsonl", "physics_calls.jsonl", "checkpoints.jsonl", "stdout.log", "stderr.log")
FINAL_STATUSES = ("completed", "failed", "cancelled")


def new_run_id(label: str, *, now: Optional[datetime] = None) -> str:
    """``YYYY-MM-DD_HHMMSS_SGT__<label>-<8 hex>``; ``now`` must be timezone-aware."""
    label = checked_id(label, "run label")
    now = now or datetime.now(SGT)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    prefix = now.astimezone(SGT).strftime("%Y-%m-%d_%H%M%S_SGT")
    return checked_run_id(f"{prefix}__{label}-{uuid.uuid4().hex[:8]}")


def code_identity(repo: Optional[PathLike] = None) -> Dict[str, Any]:
    """Git commit/branch/status of ``repo`` (default: the checkout holding ``agent``) and record-module hashes.

    Read-only. A missing Git or a directory that is not a checkout gives
    ``None`` values and ``git_error`` instead of an exception.
    """
    from agent import AGENT_API_VERSION, __version__

    root = Path(repo) if repo is not None else PACKAGE_ROOT.parents[1]
    identity: Dict[str, Any] = {"agent_version": __version__, "agent_api_version": AGENT_API_VERSION,
                                "git_commit": None, "git_branch": None, "git_status_porcelain": None}
    try:
        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True,
                                  timeout=60).stdout.strip()
        identity.update(git_commit=git("rev-parse", "HEAD"), git_branch=git("branch", "--show-current"),
                        git_status_porcelain=git("status", "--porcelain", "--untracked-files=normal"))
    except (OSError, subprocess.SubprocessError) as exc:
        identity["git_error"] = f"{type(exc).__name__}: {exc}"
    identity["contract_modules"] = {name: sha256_bytes((PACKAGE_ROOT.parent / name).read_bytes())
                                    for name in CONTRACT_MODULES}
    return identity


def _file_identity(path: PathLike) -> Dict[str, str]:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"symlink paths are forbidden: {path}")
    return {"path": repo_relative_or_absolute(path), "sha256": sha256_bytes(path.read_bytes())}


class RunLogger:
    """An exclusive run directory. Finish once; never overwrite a previous run.

    Identity must explicitly declare model/data/seed/purpose, including
    'not used' for a non-training smoke. Checkpoints are registered by path and
    SHA-256; this logger does not start a process or a GPU run.
    """

    def __init__(self, store: RunStore, label: str, *, config: Dict[str, Any], identity: Dict[str, Any],
                 run_id: Optional[str] = None, source_identity: Optional[Dict[str, Any]] = None):
        if not isinstance(config, dict) or not isinstance(identity, dict):
            raise ValueError("config and identity must be objects")
        for key in ("model", "data", "seed", "purpose"):
            if key not in identity:
                raise ValueError(f"run identity requires {key}")
        self.store = store
        self.run_id = checked_run_id(run_id) if run_id else new_run_id(label)
        self.relative = Path("logger") / self.run_id
        self.path = store.root / self.relative
        self._finished = False
        source = code_identity() if source_identity is None else dict(source_identity)
        generated = {"case_id": store.root.name, "store_root": repo_relative_or_absolute(store.root),
                     "run_id": self.run_id, "source": source}
        saved_identity = {"format": "agent-run-identity/v1", **generated, "created_at_sgt": timestamp_sgt(),
                          **identity}
        # Generated identities cannot be replaced by caller metadata.
        saved_identity.update(generated)
        json_bytes(config)
        json_bytes(saved_identity)
        store.make_directory("logger", exclusive=False)
        store.make_directory(self.relative)
        store.write_json(self.relative / "config.json", config)
        store.write_json(self.relative / "identity.json", saved_identity)
        for name in STREAMS:
            store.write_bytes(self.relative / name, b"")
        store.make_directory(self.relative / "checkpoints")
        self.event("run_started")

    @classmethod
    def for_case(cls, case_id: str, label: str, *, base: Optional[PathLike] = None, **kwargs: Any) -> "RunLogger":
        """A run under ``<base>/<case_id>/logger/`` (default base ``case/agent``)."""
        return cls(RunStore.for_case(case_id, base=base), label, **kwargs)

    @property
    def finished(self) -> bool:
        return self._finished

    def _active(self) -> None:
        if self._finished or (self.path / "final_status.json").exists():
            raise RuntimeError("run is finalized; append to a new run instead")

    def _record(self, filename: str, record: Dict[str, Any]) -> None:
        self._active()
        data = {**record, "timestamp_sgt": timestamp_sgt()}
        payload = (json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        self.store.append_bytes(self.relative / filename, payload)

    def event(self, event: str, **fields: object) -> None:
        self._record("events.jsonl", {"event": event, **fields})

    def metrics(self, *, step: int, values: Dict[str, Any]) -> None:
        if not isinstance(step, int) or step < 0 or not isinstance(values, dict):
            raise ValueError("metrics require a nonnegative step and values object")
        self._record("metrics.jsonl", {"step": step, "values": values})

    def physics_call(self, *, evaluator: Dict[str, Any], project: Optional[PathLike], fidelity: str,
                     information: Sequence[str], costs: Dict[str, Any], status: str,
                     result: Optional[PathLike] = None, **fields: object) -> Dict[str, Any]:
        """Append one physics call; returns the record written."""
        if not evaluator or not isinstance(evaluator, dict):
            raise ValueError("physics call requires evaluator identity")
        if not costs or not isinstance(costs, dict):
            raise ValueError("physics call requires explicit measured costs")
        record: Dict[str, Any] = {"evaluator": evaluator, "fidelity": str(fidelity),
                                  "information": list(information), "costs": costs, "status": status, **fields}
        if project is not None:
            identity = _file_identity(project)
            record.update(project=identity["path"], project_sha256=identity["sha256"])
        else:
            record.update(project=None, project_sha256=None)
        record["result"] = None if result is None else _file_identity(result)
        self._record("physics_calls.jsonl", record)
        return record

    def checkpoint(self, path: PathLike, **fields: object) -> None:
        relative = self.store.relative(path)
        try:
            relative.relative_to(self.relative / "checkpoints")
        except ValueError as exc:
            raise ValueError("checkpoint must be saved inside this run's checkpoints/") from exc
        self._record("checkpoints.jsonl", {"path": relative.as_posix(),
                                          "sha256": sha256_bytes(self.store.read_bytes(relative)), **fields})

    def stdout(self, text: str) -> None:
        self._active()
        self.store.append_bytes(self.relative / "stdout.log", text.encode("utf-8"))

    def stderr(self, text: str) -> None:
        self._active()
        self.store.append_bytes(self.relative / "stderr.log", text.encode("utf-8"))

    def write_json(self, name: PathLike, value: object) -> Path:
        """A once-only JSON file inside this run (e.g. a receipt)."""
        self._active()
        return self.store.write_json(self.relative / Path(name), value)

    def finish(self, status: str, **fields: object) -> Path:
        self._active()
        if status not in FINAL_STATUSES:
            raise ValueError("final status must be completed, failed or cancelled")
        result = {"format": "agent-run-status/v1", "run_id": self.run_id, "status": status,
                  "finished_at_sgt": timestamp_sgt(), **fields}
        result.update(run_id=self.run_id, status=status)
        json_bytes(result)
        self.event("run_finished", status=status)
        path = self.store.write_json(self.relative / "final_status.json", result)
        self._finished = True
        return path
