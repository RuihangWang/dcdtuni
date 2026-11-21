"""RunLogger and run identity: exclusive SGT run directories, append-only streams, physics calls.

Ported from the RunLogger half of the frozen ``tests/test_artifacts.py``. The
run directory lives in an injected ``RunStore`` root; ``code_identity`` hashes
the trunk's own record modules by package path, never an old ``src/agent``
path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.records import RunLogger, RunStore, code_identity, new_run_id
from agent.records import store as store_module
from agent.records.store import repo_relative_or_absolute

RUN = "2026-10-22_120001_SGT__contract-test"
SOURCE = {"git_commit": "unit-test", "scope": "mock Git identity only"}
IDENTITY = {"purpose": "artifact-only", "model": {"status": "not-used"}, "data": {"case": "x"}, "seed": None}


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "root", create=True)


def logger(store, run_id=RUN):
    return RunLogger(store, "test", config={"training": False}, identity=IDENTITY, run_id=run_id,
                     source_identity=SOURCE)


def test_identity_streams_physics_call_and_finish(store, tmp_path):
    log = logger(store)
    assert log.path == store.root / "logger" / RUN
    log.stdout("stdout evidence\n")
    log.stderr("stderr evidence\n")
    log.metrics(step=0, values={"artifact_files_saved": 1})
    project = tmp_path / "outside" / "candidate.cfdproj"
    project.parent.mkdir()
    project.write_text('{"schemaVersion": 4}')
    result = tmp_path / "outside" / "result.json"
    result.write_text('{"format": "dcdtuni.api.result/v1"}')
    log.physics_call(evaluator={"tool": "unit"}, project=project, fidelity="air_mesh",
                     information=["mesh"], costs={"wall_seconds": 0.0}, status="succeeded",
                     result=result, design={"canonicalSha256": "abc"})
    call = json.loads((log.path / "physics_calls.jsonl").read_text().splitlines()[0])
    assert call["project"] == repo_relative_or_absolute(project)
    assert call["project_sha256"] == RunStore.sha256(project.read_bytes())
    assert call["result"] == {"path": repo_relative_or_absolute(result),
                              "sha256": RunStore.sha256(result.read_bytes())}
    assert call["design"] == {"canonicalSha256": "abc"} and call["fidelity"] == "air_mesh"
    checkpoint = log.path / "checkpoints/not-a-model.txt"
    store.write_bytes(checkpoint, b"unit-test marker")
    log.checkpoint(checkpoint, purpose="unit-test API exercise")
    status = log.finish("completed", physics_run=False)
    saved = json.loads((log.path / "identity.json").read_text())
    assert saved["run_id"] == RUN and saved["source"] == SOURCE and saved["format"] == "agent-run-identity/v1"
    assert (log.path / "stdout.log").read_text() == "stdout evidence\n"
    assert (log.path / "stderr.log").read_text() == "stderr evidence\n"
    assert json.loads(status.read_text())["physics_run"] is False
    events = [json.loads(line)["event"] for line in (log.path / "events.jsonl").read_text().splitlines()]
    assert events == ["run_started", "run_finished"]
    with pytest.raises(RuntimeError, match="finalized"):
        log.stdout("must not change finalized log")
    with pytest.raises(RuntimeError, match="finalized"):
        log.finish("failed")


def test_recorded_paths_are_repository_relative_inside_the_repository(tmp_path, monkeypatch):
    """Like the frozen logger: a record names a file inside the checkout by its repository path."""
    repo = tmp_path / "repo"
    (repo / "case" / "agent" / "unit-case").mkdir(parents=True)
    monkeypatch.setattr(store_module, "REPO_ROOT", repo.resolve())
    store = RunStore(repo / "case" / "agent" / "unit-case")
    log = logger(store)
    saved = json.loads((log.path / "identity.json").read_text())
    assert saved["case_id"] == "unit-case" and saved["store_root"] == "case/agent/unit-case"
    inside = repo / "case" / "x" / "x.cfdproj"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}")
    outside = tmp_path / "elsewhere" / "result.json"
    outside.parent.mkdir()
    outside.write_text("{}")
    record = log.physics_call(evaluator={"tool": "unit"}, project=inside, fidelity="lf", information=[],
                              costs={"wall_seconds": 0.}, status="succeeded", result=outside)
    assert record["project"] == "case/x/x.cfdproj"
    assert record["result"]["path"] == str(outside.resolve())


def test_physics_call_requires_evaluator_and_costs(store):
    log = logger(store)
    with pytest.raises(ValueError, match="evaluator"):
        log.physics_call(evaluator={}, project=None, fidelity="lf", information=[], costs={"x": 1}, status="ok")
    with pytest.raises(ValueError, match="costs"):
        log.physics_call(evaluator={"a": 1}, project=None, fidelity="lf", information=[], costs={}, status="ok")


def test_identity_must_declare_model_data_seed_purpose(store):
    with pytest.raises(ValueError, match="model"):
        RunLogger(store, "x", config={}, identity={"data": 1, "seed": 1, "purpose": "x"}, source_identity=SOURCE)
    assert not (store.root / "logger").exists() or not any((store.root / "logger").iterdir())


def test_run_directory_never_reused(store):
    original = logger(store)
    original.stdout("preserve me\n")
    with pytest.raises(FileExistsError):
        logger(store)
    assert (original.path / "stdout.log").read_text() == "preserve me\n"


def test_logger_symlink_escape_rejected(store, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (store.root / "logger").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        logger(store)
    assert not any(outside.iterdir())


def test_checkpoint_outside_owned_run_rejected(store):
    log = logger(store)
    store.write_bytes("elsewhere.txt", b"x")
    with pytest.raises(ValueError, match="inside this run"):
        log.checkpoint("elsewhere.txt")


def test_sgt_unique_run_id():
    now = datetime(2026, 10, 8, 1, 2, 3, tzinfo=timezone.utc)
    first = new_run_id("smoke", now=now)
    second = new_run_id("smoke", now=now)
    assert first.startswith("2026-10-08_090203_SGT__smoke-")
    assert first != second
    with pytest.raises(ValueError):
        new_run_id("bad", now=datetime(2026, 10, 8))
    with pytest.raises(ValueError):
        new_run_id("../bad")


def test_code_identity_hashes_the_trunk_record_modules_not_old_paths():
    identity = code_identity()
    modules = identity["contract_modules"]
    assert set(modules) == {"agent/records/store.py", "agent/records/runlog.py"}
    trunk = Path(__file__).resolve().parents[1]
    for name, digest in modules.items():
        assert digest == RunStore.sha256((trunk.parent / name).read_bytes())
    assert not any("src/agent/harness" in key for key in modules)
    assert identity["agent_api_version"] == "1.0"
    assert len(identity["git_commit"] or "") == 40


def test_code_identity_reports_a_missing_repository_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repository"))
    identity = code_identity(repo=tmp_path)
    assert identity["git_commit"] is None and identity["git_error"]
    assert set(identity["contract_modules"]) == {"agent/records/store.py", "agent/records/runlog.py"}
