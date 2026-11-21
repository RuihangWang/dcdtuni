"""Server-owned liquid run lifecycle and artifact persistence."""
from __future__ import annotations

import json
import threading
import time

import pytest

from backend.liquidcooling.run_manager import (
    LiquidRunManager,
    RunConflictError,
    RunNotFoundError,
)
from backend.tests.test_liquid.test_runtime import controller, runtime_config


def test_step_and_completion_persist_atomic_checkpoint_and_result(tmp_path):
    """Dropping synchronous step persistence must leave these artifacts stale."""
    manager = LiquidRunManager()
    created = manager.create(
        runtime_config(samples=3).model_dump(by_alias=True), case_dir=tmp_path
    )
    run_id = created["run_id"]
    assert created["status"] == "ready"
    assert created["frame"]["seq"] == 0

    assert manager.operate(run_id, "step")["frame"]["seq"] == 1
    completed = manager.operate(run_id, "step")
    assert completed["status"] == "completed"

    run_dir = tmp_path / "Liquid" / "log" / f"run-{run_id}"
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    result = json.loads((run_dir / "result.json").read_text())
    assert checkpoint["seq"] == 2
    assert result["status"] == "completed"
    assert result["time_s"] == [0.0, 1.0, 2.0]


def test_pause_resume_and_cancel_acknowledge_only_after_worker_stops(tmp_path):
    """Returning pause/cancel while a worker is alive must fail this boundary test."""
    manager = LiquidRunManager()
    run_id = manager.create(
        runtime_config(samples=40).model_dump(by_alias=True), case_dir=tmp_path
    )["run_id"]

    manager.operate(run_id, "start", speed=1.0)
    paused = manager.operate(run_id, "pause")
    assert paused["status"] in {"paused", "completed"}
    assert not manager.worker_alive(run_id)

    if paused["status"] == "paused":
        manager.operate(run_id, "resume", speed=1.0)
        cancelled = manager.operate(run_id, "cancel")
        assert cancelled["status"] in {"cancelled", "completed"}
        assert not manager.worker_alive(run_id)


def test_invalid_transitions_and_unknown_run_are_explicit_conflicts(tmp_path):
    """Accepting duplicate workers or unknown IDs would violate session ownership."""
    manager = LiquidRunManager()
    run_id = manager.create(
        runtime_config(samples=6).model_dump(by_alias=True), case_dir=tmp_path
    )["run_id"]

    with pytest.raises(RunConflictError):
        manager.operate(run_id, "resume")
    manager.operate(run_id, "start", speed=1.0)
    with pytest.raises(RunConflictError):
        manager.operate(run_id, "start", speed=1.0)
    manager.operate(run_id, "cancel")
    with pytest.raises(RunConflictError):
        manager.operate(run_id, "step")
    with pytest.raises(RunNotFoundError):
        manager.get("missing")


def test_case_path_is_captured_at_creation(tmp_path):
    """A later global CASE_DIR change must not relocate this run's files."""
    manager = LiquidRunManager()
    case_a, case_b = tmp_path / "a", tmp_path / "b"
    case_a.mkdir()
    case_b.mkdir()
    run_id = manager.create(
        runtime_config(samples=2).model_dump(by_alias=True), case_dir=case_a
    )["run_id"]
    manager.operate(run_id, "step")

    assert (case_a / "Liquid" / "log" / f"run-{run_id}" / "result.json").is_file()
    assert not (case_b / "Liquid" / "log" / f"run-{run_id}").exists()


def test_concurrent_step_requests_commit_at_most_one_sample_each(tmp_path):
    """Unsynchronised operations would duplicate or skip committed sequence numbers."""
    manager = LiquidRunManager()
    run_id = manager.create(
        runtime_config(samples=8).model_dump(by_alias=True), case_dir=tmp_path
    )["run_id"]
    outcomes = []

    def advance():
        try:
            outcomes.append(("ok", manager.operate(run_id, "step")["frame"]["seq"]))
        except RunConflictError:
            outcomes.append(("conflict", None))

    threads = [threading.Thread(target=advance) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert manager.get(run_id)["frame"]["seq"] in {1, 2}
    assert len(outcomes) == 2
    assert [f["seq"] for f in manager.frames(run_id, after=-1)["frames"]] == list(
        range(manager.get(run_id)["frame"]["seq"] + 1)
    )


def test_numerical_failure_is_recorded_without_committing_a_frame(tmp_path):
    """A kernel exception must become failed status while retaining the last frame."""
    manager = LiquidRunManager()
    run_id = manager.create(
        runtime_config(samples=3).model_dump(by_alias=True), case_dir=tmp_path
    )["run_id"]
    record = manager._records[run_id]
    record.simulation._graph.pump_params.rpm_ratio[0, 1] = float("nan")

    failed = manager.operate(run_id, "step")
    assert failed["status"] == "failed"
    assert failed["frame"]["seq"] == 0
    assert "hydraulic" in failed["error"]["message"]
    assert not manager.worker_alive(run_id)


def test_checkpoint_create_validates_public_controller_hash_and_captures_input(tmp_path):
    """Hashing internal controller fields or omitting input artifacts must fail."""
    config = runtime_config(samples=4)
    controllers = [controller()]
    first_case = tmp_path / "first"
    second_case = tmp_path / "second"
    first_case.mkdir()
    second_case.mkdir()
    first = LiquidRunManager()
    run_id = first.create(
        config.model_dump(by_alias=True),
        controllers=controllers,
        case_dir=first_case,
    )["run_id"]
    first.operate(run_id, "step")
    checkpoint = first.checkpoint(run_id)

    restored = LiquidRunManager()
    created = restored.create(
        config.model_dump(by_alias=True),
        controllers=controllers,
        checkpoint=checkpoint,
        case_dir=second_case,
    )
    assert created["frame"]["seq"] == 1
    captured = json.loads((second_case / "Liquid" / "log" / f"run-{run_id}" / "input.json").read_text())
    assert captured["liquidrun"]["projectName"] == config.projectName
    assert captured["controllers"] == controllers


def test_step_at_existing_horizon_is_a_lifecycle_conflict(tmp_path):
    """Treating an already-complete horizon as numerical failure must fail."""
    manager = LiquidRunManager()
    run_id = manager.create(
        runtime_config(samples=1).model_dump(by_alias=True), case_dir=tmp_path
    )["run_id"]

    with pytest.raises(RunConflictError, match="horizon"):
        manager.operate(run_id, "step")
    assert manager.get(run_id)["status"] == "ready"
