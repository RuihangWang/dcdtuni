"""The command handoff and the whole CFD run share one per-case input lock."""
from __future__ import annotations

import hashlib
import sys

import pytest

from backend.aircooling.runner import run_cfd, stlgen_runner


@pytest.mark.parametrize("mode", ["run_mesh_only", "run_solve_only", "run_simulation"])
def test_runner_holds_case_lock_before_touching_inputs(tmp_path, monkeypatch, mode):
    from backend.aircooling.runner.case_execution_lock import (
        CaseExecutionBusyError,
        case_execution_lock,
    )

    model = tmp_path / "model"
    model.mkdir()
    manifest = model / "input.cfdrun"
    manifest.write_bytes(b"{}")
    runner = stlgen_runner.STLGenRunner(tmp_path, manifest)
    reached = []

    class StopAtPreflight(Exception):
        pass

    def inspect_lock():
        with pytest.raises(CaseExecutionBusyError):
            with case_execution_lock(tmp_path, blocking=False):
                pass
        reached.append(True)
        raise StopAtPreflight

    monkeypatch.setattr(runner, "_preflight_case_path", inspect_lock)
    with pytest.raises(StopAtPreflight):
        getattr(runner, mode)()
    assert reached == [True]
    # Failure releases the lock for the next upload or run.
    with case_execution_lock(tmp_path, blocking=False):
        pass


@pytest.mark.parametrize("mode", ["run_mesh_only", "run_solve_only", "run_simulation"])
def test_stale_expected_manifest_rejected_under_case_lock(tmp_path, monkeypatch, mode):
    model = tmp_path / "model"
    model.mkdir()
    manifest = model / "input.cfdrun"
    manifest.write_bytes(b"new upload")
    runner = stlgen_runner.STLGenRunner(
        tmp_path, manifest, expected_cfdrun_sha256=hashlib.sha256(b"old upload").hexdigest()
    )
    monkeypatch.setattr(runner, "_preflight_case_path",
                        lambda: pytest.fail("stale input reached preflight"))
    with pytest.raises(RuntimeError, match="cfdrun.*(?:hash|changed|identity|SHA)"):
        getattr(runner, mode)()


def test_run_cfd_cli_passes_expected_manifest_hash_to_runner(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    manifest = model / "input.cfdrun"
    manifest.write_bytes(b"current")
    expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
    seen = {}

    class FakeRunner:
        def __init__(self, case_dir, cfdrun_path, expected_cfdrun_sha256=None, **_kwargs):
            seen["init"] = (case_dir, cfdrun_path, expected_cfdrun_sha256)

        def run_mesh_only(self):
            seen["mode"] = "mesh"

    monkeypatch.setattr(stlgen_runner, "STLGenRunner", FakeRunner)
    monkeypatch.setattr(sys, "argv", [
        "run_cfd", "--from-cfdrun", str(manifest), "--mesh-only",
        "--expected-cfdrun-sha256", expected,
    ])
    run_cfd.main()
    assert seen == {"init": (tmp_path, manifest, expected), "mode": "mesh"}
