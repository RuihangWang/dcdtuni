"""A native wind result is published only after a complete solver exit."""

import datetime
import hashlib
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner
from backend.aircooling.runner.air_run_execution_receipt import verify_air_run_execution_receipt


def _field(name: str) -> str:
    scalar = name == "T"
    klass = "volScalarField" if scalar else "volVectorField"
    dimensions = "[0 0 0 1 0 0 0]" if scalar else "[0 1 -1 0 0 0 0]"
    value = "300" if scalar else "(0.1 0 0)"
    return (f"FoamFile {{ class {klass}; object {name}; }}\n"
            f"dimensions {dimensions};\ninternalField uniform {value};\n")


def _mesh(root: Path) -> None:
    poly = root / "constant/polyMesh"
    poly.mkdir(parents=True)
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly / name).write_text(f"mesh {root.name} {name}\n")
    (poly / "owner").write_text(f'FoamFile {{ note "nCells:1"; object owner; }}\n// {root.name}\n')


def _runner(tmp_path, monkeypatch, *, parallel: bool = False) -> STLGenRunner:
    case = tmp_path / "Shinaide"
    model = case / "model"
    model.mkdir(parents=True)
    cfdrun = model / "input.cfdrun"
    cfdrun.write_text(json.dumps({
        "runId": "test-air-1", "sourceProject": {"contentHash": "a" * 64},
    }))
    runner = STLGenRunner(case, cfdrun)
    if parallel:
        for rank in (0, 1):
            _mesh(runner.mesh_dir / f"processor{rank}")
    else:
        _mesh(runner.mesh_dir)
    for poly in runner.mesh_dir.glob("**/constant/polyMesh"):
        (poly / "owner").write_text('FoamFile { note "nCells:1"; object owner; }\n')
    from backend.tests.test_air_mesh_quality_reuse import mock_completed_strict_quality
    mock_completed_strict_quality(runner, monkeypatch)
    runner._data = {
        "meshConfig": {"meshEngine": "dcCart" if parallel else "snappy"},
        "simulationConfig": {
            "solverType": "steady", "parallelSolve": parallel,
            "solveProcess": 2 if parallel else 1,
        },
    }
    monkeypatch.setattr(runner, "_clear_mesh_budget", lambda: None)
    monkeypatch.setattr(runner, "_generate_solver_dicts", lambda _: None)
    monkeypatch.setattr(runner, "_resolve_solver_binary", lambda command: command)
    monkeypatch.setattr(runner, "_auto_harvest_sensors", lambda: None)
    monkeypatch.setattr(runner, "_write_convergence_verdict", lambda *args: {})
    if parallel:
        monkeypatch.setattr(runner, "_decompose_for_solve", lambda _run, n: n)
    return runner


def _write_time(root: Path, time_name: str = "100", *, fields=("T", "U")) -> None:
    time_dir = root / time_name
    time_dir.mkdir(parents=True)
    for name in fields:
        (time_dir / name).write_text(_field(name))


def test_successful_solve_publishes_verified_positive_time_receipt(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)

    def solve(_command, run_dir):
        _write_time(run_dir)

    monkeypatch.setattr(runner, "_run_cmd", solve)
    run_dir = runner._run_solve_from_mesh()
    receipt_path = run_dir / "air-run-execution-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["format"] == "air-run-execution-receipt/v1"
    assert receipt["status"] == "accepted"
    assert receipt["result"]["timeValue"] == 100
    assert receipt["result"]["meshLayout"] == "serial"
    assert [item["field"] for item in receipt["result"]["fieldFiles"]] == ["T", "U"]
    verify_air_run_execution_receipt(
        receipt, case_dir=runner.case_dir,
        cfdrun_path=runner.cfdrun_path, run_dir=run_dir,
    )
    (run_dir / "100/T").write_text(_field("T") + "\nchanged")
    with pytest.raises(ValueError, match="receipt identity mismatch"):
        verify_air_run_execution_receipt(
            receipt, case_dir=runner.case_dir,
            cfdrun_path=runner.cfdrun_path, run_dir=run_dir,
        )


def test_successful_command_without_u_fails_without_accepted_receipt(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_run_cmd", lambda _command, run: _write_time(run, fields=("T",)))
    with pytest.raises(ValueError, match="field U is missing"):
        runner._run_solve_from_mesh()
    assert not list((runner.case_dir / "log").glob("*/air-run-execution-receipt.json"))


def test_solver_error_fails_without_accepted_receipt(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)

    def fail(_command, run_dir):
        _write_time(run_dir)
        raise RuntimeError("solver exited nonzero")

    monkeypatch.setattr(runner, "_run_cmd", fail)
    with pytest.raises(RuntimeError, match="solver exited nonzero"):
        runner._run_solve_from_mesh()
    assert not list((runner.case_dir / "log").glob("*/air-run-execution-receipt.json"))


def test_manifest_without_project_hash_is_rejected_before_solver(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    runner.cfdrun_path.write_text(json.dumps({"runId": "old-legal-manifest"}))
    commands = []
    monkeypatch.setattr(runner, "_run_cmd", lambda command, _run: commands.append(command))
    with pytest.raises(ValueError, match="sourceProject.contentHash"):
        runner._run_solve_from_mesh()
    assert commands == []
    assert not (runner.case_dir / "log").exists()


def test_zero_time_initial_fields_are_not_a_solved_frame(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_run_cmd", lambda _command, run: _write_time(run, "0"))
    with pytest.raises(ValueError, match="no positive"):
        runner._run_solve_from_mesh()
    assert not list((runner.case_dir / "log").glob("*/air-run-execution-receipt.json"))


def test_verifier_rejects_nonobject_result_as_invalid_receipt(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_run_cmd", lambda _command, run: _write_time(run))
    run_dir = runner._run_solve_from_mesh()
    receipt = json.loads((run_dir / "air-run-execution-receipt.json").read_text())
    receipt["result"] = None
    with pytest.raises(ValueError, match="result.*object"):
        verify_air_run_execution_receipt(
            receipt, case_dir=runner.case_dir,
            cfdrun_path=runner.cfdrun_path, run_dir=run_dir,
        )


def test_hybrid_receipt_binds_adjusted_executed_manifest(tmp_path, monkeypatch):
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256

    runner = _runner(tmp_path, monkeypatch)
    root = json.loads(runner.cfdrun_path.read_text())
    root["runId"] = "hybrid_test"
    runner.cfdrun_path.write_text(json.dumps(root))
    runner._verified_source_cfdrun_sha256 = hashlib.sha256(runner.cfdrun_path.read_bytes()).hexdigest()
    runner._accepted_air_mesh_sha = mesh_identity_sha256(runner.mesh_dir)
    runner._hybrid_mesh_sha = mesh_identity_sha256(runner.mesh_dir)
    runner._record_air_mesh_quality()
    runner._hybrid_final_manifest = runner.case_dir / "hybrid-mesh-adjusted.cfdrun"
    runner._hybrid_final_manifest.write_text(json.dumps({
        "runId": "hybrid-adjusted", "sourceProject": {"contentHash": "b" * 64},
    }))
    runner._hybrid_final_ledger = runner.case_dir / "hybrid-mesh-adjusted-ledger.json"
    runner._hybrid_final_ledger.write_text("{}")
    monkeypatch.setattr(runner, "_run_cmd", lambda _command, run: _write_time(run))

    run_dir = runner._run_solve_from_mesh()
    receipt = json.loads((run_dir / "air-run-execution-receipt.json").read_text())
    assert receipt["result"]["cfdrunRelativePath"] == "hybrid-mesh-adjusted.cfdrun"
    assert receipt["result"]["projectContentHash"] == "b" * 64
    assert receipt["result"]["runId"] == "hybrid-adjusted"


def test_partitioned_latest_partial_rank_does_not_accept_old_complete_time(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, parallel=True)

    def solve(_command, run_dir):
        for rank in (0, 1):
            _write_time(run_dir / f"processor{rank}", "100")
        _write_time(run_dir / "processor0", "200")

    monkeypatch.setattr(runner, "_run_cmd", solve)
    with pytest.raises(ValueError, match="incomplete.*time|time.*incomplete"):
        runner._run_solve_from_mesh()
    assert not list((runner.case_dir / "log").glob("*/air-run-execution-receipt.json"))


def test_existing_same_second_run_dir_is_never_reused(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 27, 12, 0, 0)
    monkeypatch.setattr(datetime, "datetime", FixedDateTime)
    existing = runner.case_dir / "log/2026-09-27-12-00-00_Shinaide"
    existing.mkdir(parents=True)
    _mesh(existing)
    _write_time(existing)
    called = []
    monkeypatch.setattr(runner, "_run_cmd", lambda *_: called.append(True))

    with pytest.raises(FileExistsError, match="run directory.*exists"):
        runner._run_solve_from_mesh()
    assert called == []
    assert not (existing / "air-run-execution-receipt.json").exists()
