"""Threaded ownership, lifecycle, and persistence for liquid runtime sessions."""
from __future__ import annotations

import copy
from collections import deque
import json
import math
import os
import re
import uuid
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.liquidcooling.runtime import (
    LiquidRuntimeError,
    LiquidSimulation,
    _canonical_hash,
)
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.liquidrun_schema import resolve_run_input


class RunNotFoundError(KeyError):
    pass


class RunConflictError(RuntimeError):
    pass


class RunInputError(ValueError):
    pass


class _RunLock:
    """FIFO reentrant lock for a solver step and its waiting HTTP operations.

    An ordinary RLock lets the fastest worker immediately acquire its released
    lock again, starving already-waiting commands until the horizon is over.
    Queue each new owner so a command/status/pause that arrives during a solve
    receives the lock at that step boundary, before the worker's next step.
    Reentrant calls by the current owner retain the existing nesting behavior.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._owner = None
        self._depth = 0
        self._waiters = deque()

    def acquire(self):
        owner = threading.get_ident()
        with self._condition:
            if self._owner == owner:
                self._depth += 1
                return True
            ticket = object()
            self._waiters.append(ticket)
            self._condition.notify_all()
            try:
                self._condition.wait_for(
                    lambda: self._owner is None and self._waiters[0] is ticket
                )
            except BaseException:
                self._waiters.remove(ticket)
                self._condition.notify_all()
                raise
            self._waiters.popleft()
            self._owner, self._depth = owner, 1
            return True

    def release(self):
        with self._condition:
            if self._owner != threading.get_ident():
                raise RuntimeError('cannot release a run lock owned by another thread')
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._condition.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


@dataclass
class _RunRecord:
    simulation: LiquidSimulation
    case_dir: Path
    run_dir: Path
    project_revision: int | None
    status: str = "ready"
    error: dict | None = None
    speed: float | None = None
    lock: _RunLock = field(default_factory=_RunLock)
    worker: threading.Thread | None = None
    pause_requested: bool = False
    cancel_requested: bool = False
    wake: threading.Event = field(default_factory=threading.Event)
    executed_seq: int = -1
    # A record rebuilt from saved files (restore): it serves results, the
    # accepted input and the heat audit, and never solves or rewrites files.
    read_only: bool = False


_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


class LiquidRunManager:
    """Own simulations beyond request lifetime and serialize each run's mutations."""

    def __init__(self):
        self._records: dict[str, _RunRecord] = {}
        self._records_lock = threading.RLock()

    def create(
        self, liquidrun, *, case_dir, controllers=None, project_revision=None, checkpoint=None,
    ) -> dict:
        # PyTorch intra-op threads are process-wide. Serialize admission and
        # forbid a new run from changing another live run's promised budget.
        with self._records_lock:
            requested = (liquidrun.get('execution', {}).get('max_threads', 1)
                         if isinstance(liquidrun, dict) else 1)
            for record in self._records.values():
                if record.status not in {'ready', 'running', 'paused'}:
                    continue
                existing = record.simulation.metadata.get('execution', {}).get('max_threads', 1)
                if requested != existing:
                    raise RunConflictError('active runs share one process thread budget; finish or cancel them before changing max_threads')
            return self._create(liquidrun, case_dir=case_dir, controllers=controllers,
                                project_revision=project_revision, checkpoint=checkpoint)

    def _create(
        self,
        liquidrun: dict | LiquidrunConfig,
        *,
        case_dir: str | Path,
        controllers: list[dict] | None = None,
        project_revision: int | None = None,
        checkpoint: dict | None = None,
    ) -> dict:
        if isinstance(project_revision, bool) or (
            project_revision is not None and not isinstance(project_revision, int)
        ):
            raise RunInputError("project_revision must be an integer")
        from backend.liquidcooling.liquidrun_dae_schema import is_dae_manifest
        is_dae = is_dae_manifest(liquidrun)
        try:
            if is_dae:
                from backend.liquidcooling.dae_runtime import DAESimulation
                if controllers not in (None, []):
                    raise RunInputError('DAE runs do not support external PID controllers')
                # A resumed run is a NEW run continuing the checkpointed one:
                # the original keeps its id and artifact directory.
                simulation = (DAESimulation(liquidrun) if checkpoint is None else
                              DAESimulation.from_checkpoint(liquidrun, checkpoint, run_id=uuid.uuid4().hex))
            else:
                config, controllers = resolve_run_input(liquidrun, controllers)
            if not is_dae and checkpoint is None:
                simulation = LiquidSimulation(config, controllers)
            elif not is_dae:
                simulation = LiquidSimulation.from_checkpoint(checkpoint)
                resolved = config.model_dump(by_alias=True, mode="json")
                expected_hash = _canonical_hash(resolved, controllers)
                if expected_hash != simulation.config_hash:
                    raise RunInputError(
                        "checkpoint hash does not match liquidrun and controllers"
                    )
        except RunInputError:
            raise
        except (TypeError, ValueError, LiquidRuntimeError, RuntimeError) as exc:
            raise RunInputError(str(exc)) from exc

        run_id = simulation.run_id
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id) is None:
            raise RunInputError("run_id contains unsafe characters")
        selected_case = Path(case_dir).expanduser().resolve()
        run_dir = selected_case / "Liquid" / "log" / f"run-{run_id}"
        with self._records_lock:
            if run_id in self._records:
                raise RunConflictError(f"run {run_id} already exists")
            if run_dir.exists() and any(run_dir.iterdir()):
                raise RunConflictError(f"run artifact directory already exists: {run_id}")
            record = _RunRecord(
                simulation=simulation,
                case_dir=selected_case,
                run_dir=run_dir,
                project_revision=project_revision,
                status='completed' if is_dae and simulation.done else 'ready',
            )
            self._records[run_id] = record
        try:
            with record.lock:
                self._persist(record)
        except BaseException:
            with self._records_lock:
                self._records.pop(run_id, None)
            raise
        return self._status(record)

    def _record(self, run_id: str) -> _RunRecord:
        with self._records_lock:
            record = self._records.get(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    def restore(self, run_id: str, *, case_dir) -> dict:
        """Own a finished DAE run again from its saved run directory.

        Session recovery after a server restart or page reload: the record is
        rebuilt from ``case_dir/Liquid/log/run-<id>`` by ``SavedDAERun.load``,
        which re-checks every identity link between the saved files, and it
        is read-only. A run this manager already owns is returned as it is.
        """
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise RunInputError("run_id contains unsafe characters")
        with self._records_lock:
            existing = self._records.get(run_id)
        if existing is not None:
            with existing.lock:
                return {**self._status(existing), "restored": False}
        if case_dir is None:
            raise RunInputError("No case directory set")
        from backend.liquidcooling.saved_run import (
            SavedDAERun, SavedRunInvalid, SavedRunNotTerminal,
        )
        selected_case = Path(case_dir).expanduser().resolve()
        run_dir = selected_case / "Liquid" / "log" / f"run-{run_id}"
        if not run_dir.is_dir():
            raise RunNotFoundError(run_id)
        try:
            saved = SavedDAERun.load(run_dir, run_id)
        except SavedRunNotTerminal as exc:
            raise RunConflictError(str(exc)) from exc
        except SavedRunInvalid as exc:
            raise RunInputError(str(exc)) from exc
        record = _RunRecord(
            simulation=saved, case_dir=selected_case, run_dir=run_dir,
            project_revision=saved.project_revision, status=saved.status,
            error=copy.deepcopy(saved.error), read_only=True,
        )
        with self._records_lock:
            existing = self._records.get(run_id)
            if existing is None:
                self._records[run_id] = record
                restored = True
            else:
                record, restored = existing, False
        with record.lock:
            return {**self._status(record), "restored": restored}

    def loaded_runs(self) -> dict[str, dict]:
        """Run ids held in memory with their status and read-only flag."""
        with self._records_lock:
            records = dict(self._records)
        return {run_id: {"status": record.status, "read_only": record.read_only}
                for run_id, record in records.items()}

    @staticmethod
    def _require_writable(record: _RunRecord) -> None:
        if record.read_only:
            raise RunConflictError(
                f"run {record.simulation.run_id} was restored from saved files and is read-only; "
                "start a new liquid experiment to solve again")

    def _status(self, record: _RunRecord) -> dict:
        return {
            "run_id": record.simulation.run_id,
            "status": record.status,
            "config_hash": record.simulation.config_hash,
            "metadata": record.simulation.metadata,
            "project_revision": record.project_revision,
            "frame": record.simulation.latest_frame,
            "error": copy.deepcopy(record.error),
            "read_only": record.read_only,
        }

    def get(self, run_id: str) -> dict:
        record = self._record(run_id)
        with record.lock:
            return self._status(record)

    def frames(self, run_id: str, *, after: int = -1) -> dict:
        if isinstance(after, bool) or not isinstance(after, int):
            raise RunInputError("after must be an integer")
        record = self._record(run_id)
        with record.lock:
            return {
                "run_id": run_id,
                "status": record.status,
                "frames": [
                    frame for frame in record.simulation.frames
                    if frame["seq"] > after
                ],
                "error": copy.deepcopy(record.error),
            }

    def result(self, run_id: str) -> dict:
        record = self._record(run_id)
        with record.lock:
            result = record.simulation.result()
            result["status"] = record.status
            result["completed"] = record.status == "completed"
            result["error"] = copy.deepcopy(record.error)
            return result

    def executed_input(self, run_id: str) -> dict:
        record=self._record(run_id)
        with record.lock:
            if not hasattr(record.simulation,'executed_manifest'):
                raise RunInputError('accepted input export requires a DAE run')
            return record.simulation.executed_manifest()

    def checkpoint(self, run_id: str) -> dict:
        record = self._record(run_id)
        with record.lock:
            checkpoint = record.simulation.checkpoint()
            checkpoint["run_status"] = record.status
            checkpoint["error"] = copy.deepcopy(record.error)
            return checkpoint

    def heat_audit(self, run_id: str, *, project_uid: str) -> dict:
        """Audit accepted liquid heat without assigning unproven air power."""
        record = self._record(run_id)
        with record.lock:
            if not hasattr(record.simulation, 'executed_manifest'):
                raise RunInputError('hybrid heat audit requires a DAE run')
            from backend.liquidcooling.hybrid_heat_audit import audit_hybrid_heat
            original_path = record.run_dir / 'input.liquidrun'
            receipt_path = record.run_dir / 'executed-inputs.json'
            if not original_path.is_file() or not receipt_path.is_file():
                raise RunInputError('hybrid heat audit requires both original input and accepted-input provenance')
            return audit_hybrid_heat(
                record.simulation.executed_manifest(),
                record.simulation.result(),
                project_uid,
                original_liquidrun=json.loads(original_path.read_text()),
                accepted_provenance=json.loads(receipt_path.read_text()),
            )

    def compile_hybrid_air_heat(self, run_id: str, *, project: dict, air_cfdrun: dict,
                                tri_surface_dir, sample_index: int, case_dir=None) -> tuple[dict, dict]:
        """Hand one accepted sample of a completed DAE run to the air side.

        A run this server no longer holds in memory (restart) is restored
        read-only from ``case_dir`` first; the audit below is the same for
        both, so restoring cannot bypass the status, accepted-input or
        project-hash checks.
        """
        try:
            record = self._record(run_id)
        except RunNotFoundError:
            if case_dir is None:
                raise RunInputError(
                    f"hybrid fan air: liquid run {run_id} is not loaded in this server") from None
            try:
                self.restore(run_id, case_dir=case_dir)
            except RunNotFoundError:
                raise RunInputError(
                    f"hybrid fan air: liquid run {run_id} is not loaded and has no saved run "
                    "in this case") from None
            except RunConflictError as exc:
                raise RunInputError(f"hybrid fan air requires a completed owned DAE run: {exc}") from exc
            record = self._record(run_id)
        with record.lock:
            if record.status != 'completed' or not hasattr(record.simulation, 'executed_manifest'):
                raise RunInputError('hybrid fan air requires a completed owned DAE run')
            original_path = record.run_dir / 'input.liquidrun'
            receipt_path = record.run_dir / 'executed-inputs.json'
            if not original_path.is_file() or not receipt_path.is_file():
                raise RunInputError('hybrid fan air requires original and accepted input provenance')
            from backend.aircooling.runner.hybrid_air_heat_handoff import compile_hybrid_air_heat
            return compile_hybrid_air_heat(
                project, air_cfdrun, tri_surface_dir,
                original_liquidrun=json.loads(original_path.read_text()),
                executed_liquidrun=record.simulation.executed_manifest(),
                liquid_result=record.simulation.result(),
                accepted_provenance=json.loads(receipt_path.read_text()),
                sample_index=sample_index)

    def set_inputs(self, run_id: str, *, heat_multiplier=None,
                   pump_rpm_ratios=None, valve_openings=None,
                   primary_inlet_K=None) -> dict:
        record = self._record(run_id)
        with record.lock:
            self._require_writable(record)
            if record.status in {'cancelled', 'failed'}:
                raise RunConflictError(f'cannot change inputs while status is {record.status}')
            if heat_multiplier is None and pump_rpm_ratios is None and valve_openings is None and primary_inlet_K is None:
                raise RunInputError('inputs requires at least one DAE control field')
            if not hasattr(record.simulation, 'set_heat_multiplier'):
                raise RunInputError('live control commands require a DAE run')
            if hasattr(record.simulation, 'set_inputs'):
                record.simulation.set_inputs(
                    heat_multiplier=heat_multiplier,
                    pump_rpm_ratios=pump_rpm_ratios,
                    valve_openings=valve_openings,
                    primary_inlet_K=primary_inlet_K,
                )
            elif pump_rpm_ratios is not None or valve_openings is not None or primary_inlet_K is not None:
                raise RunInputError('pump and valve commands require the DAE control runtime')
            else:
                record.simulation.set_heat_multiplier(heat_multiplier)
            self._persist(record)
            return self._status(record)

    def equilibrium(self, run_id: str, *, heat_multiplier=None) -> dict:
        record = self._record(run_id)
        with record.lock:
            self._require_writable(record)
            if not hasattr(record.simulation, 'equilibrium'):
                raise RunInputError('equilibrium queries require a DAE run')
            # A separate root query cannot overwrite the accepted trajectory.
            answer = record.simulation.equilibrium(heat_multiplier)
            _atomic_json(record.run_dir / 'equilibrium.json', answer)
            return answer

    def worker_alive(self, run_id: str) -> bool:
        record = self._record(run_id)
        with record.lock:
            return record.worker is not None and record.worker.is_alive()

    def operate(
        self, run_id: str, operation: str, *, speed: float | None = None
    ) -> dict:
        record = self._record(run_id)
        self._require_writable(record)  # fixed at record creation; no lock wait
        if operation in {"start", "resume"}:
            return self._start(record, operation, speed)
        if operation == "pause":
            return self._pause(record)
        if operation == "cancel":
            return self._cancel(record)
        if operation == "step":
            return self._single_step(record)
        raise RunInputError(f"unknown operation {operation!r}")

    def _validate_speed(self, speed: float | None) -> float | None:
        if speed is None:
            return None
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            raise RunInputError("speed must be null or a positive number")
        speed = float(speed)
        if not math.isfinite(speed) or speed <= 0:
            raise RunInputError("speed must be null or a positive number")
        return speed

    def _start(self, record: _RunRecord, operation: str, speed) -> dict:
        speed = self._validate_speed(speed)
        required = "ready" if operation == "start" else "paused"
        with record.lock:
            if record.status != required:
                raise RunConflictError(
                    f"cannot {operation} run while status is {record.status}"
                )
            record.speed = speed
            record.pause_requested = False
            record.cancel_requested = False
            record.wake.clear()
            record.status = "running"
            worker = threading.Thread(
                target=self._worker_loop,
                args=(record,),
                name=f"liquid-run-{record.simulation.run_id}",
                daemon=True,
            )
            record.worker = worker
            worker.start()
            return self._status(record)

    def _pause(self, record: _RunRecord) -> dict:
        with record.lock:
            if record.status != "running":
                raise RunConflictError(
                    f"cannot pause run while status is {record.status}"
                )
            record.pause_requested = True
            record.wake.set()
            worker = record.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        with record.lock:
            return self._status(record)

    def _cancel(self, record: _RunRecord) -> dict:
        with record.lock:
            if record.status not in {"ready", "running", "paused"}:
                raise RunConflictError(
                    f"cannot cancel run while status is {record.status}"
                )
            if record.status in {"ready", "paused"}:
                record.status = "cancelled"
                record.cancel_requested = True
                self._persist(record)
                return self._status(record)
            record.cancel_requested = True
            record.wake.set()
            worker = record.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        with record.lock:
            return self._status(record)

    def _single_step(self, record: _RunRecord) -> dict:
        with record.lock:
            if record.status not in {"ready", "paused"}:
                raise RunConflictError(
                    f"cannot step run while status is {record.status}"
                )
            if record.simulation.done:
                raise RunConflictError("cannot step run after the configured horizon")
            self._advance_locked(record)
            if record.status != "failed" and record.simulation.done:
                record.status = "completed"
            self._persist(record)
            return self._status(record)

    def _advance_locked(self, record: _RunRecord) -> None:
        before = record.simulation.latest_frame["seq"]
        try:
            record.simulation.step()
        except Exception as exc:
            record.status = "failed"
            record.error = {
                "type": type(exc).__name__,
                "message": str(exc) or "liquid simulation step failed",
                "last_successful_seq": before,
            }

    def _worker_loop(self, record: _RunRecord) -> None:
        while True:
            started = time.monotonic()
            with record.lock:
                if record.cancel_requested:
                    record.status = "cancelled"
                    self._persist(record)
                    record.worker = None
                    return
                if record.pause_requested:
                    record.status = "paused"
                    self._persist(record)
                    record.worker = None
                    return
                if record.simulation.done:
                    record.status = "completed"
                    self._persist(record)
                    record.worker = None
                    return
                self._advance_locked(record)
                if record.status == "failed":
                    self._persist(record)
                    record.worker = None
                    return
                if record.simulation.done:
                    record.status = "completed"
                    self._persist(record)
                    record.worker = None
                    return
                self._persist(record)
                speed = record.speed

            if speed is not None:
                target = record.simulation.metadata["dt_s"] / speed
                remaining = max(0.0, target - (time.monotonic() - started))
                record.wake.wait(remaining)
                record.wake.clear()

    def _persist(self, record: _RunRecord) -> None:
        if record.read_only:
            raise RunConflictError("a restored read-only run never rewrites its saved files")
        checkpoint = record.simulation.checkpoint()
        input_data = {
            "run_id": record.simulation.run_id,
            "config_hash": record.simulation.config_hash,
            "project_revision": record.project_revision,
            "metadata": record.simulation.metadata,
            "liquidrun": copy.deepcopy(checkpoint["liquidrun"]),
            "controllers": copy.deepcopy(checkpoint["controllers"]),
        }
        checkpoint["run_status"] = record.status
        checkpoint["error"] = copy.deepcopy(record.error)
        result = record.simulation.result()
        result["status"] = record.status
        result["completed"] = record.status == "completed"
        result["error"] = copy.deepcopy(record.error)
        _atomic_json(record.run_dir / "input.json", input_data)
        # The v2 file embeds controllers and resolved parameters. It can be
        # replayed without the request body, project file or equipment library.
        standalone = copy.deepcopy(checkpoint["liquidrun"])
        if standalone.get("version") in {"2.0", "3.0", "4.0"}:
            # The legacy numerical adapter has an empty-dict default, while
            # v2 represents absent provenance by omitting the optional field.
            if standalone.get("parameter_provenance") == {}:
                standalone.pop("parameter_provenance")
            _atomic_json(record.run_dir / "input.liquidrun", standalone)
        _atomic_json(record.run_dir / "checkpoint.json", checkpoint)
        _atomic_json(record.run_dir / "result.json", result)
        if hasattr(record.simulation,'executed_manifest') and record.executed_seq!=checkpoint['seq']:
            executed=record.simulation.executed_manifest()
            _atomic_json(record.run_dir/'executed.liquidrun',executed)
            _atomic_json(record.run_dir/'executed-inputs.json',{
                'format':'liquid-accepted-inputs/v1','run_id':record.simulation.run_id,
                'original_config_hash':record.simulation.config_hash,
                'executed_config_hash':_canonical_hash(executed,[]),
                'accepted_sample_count':executed['time']['N_t'],
                'original_sample_count':record.simulation.metadata['total_sample_count'],
                'accepted_time_s':record.simulation.latest_frame['time_s'],
                'complete_original_horizon':record.simulation.done,
                'input_file':'executed.liquidrun',
                'scope':'accepted inputs only; pending commands and independent equilibrium are excluded',
            })
            record.executed_seq=checkpoint['seq']
