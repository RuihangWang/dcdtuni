"""python -m agent {identity,edit,evaluate,optimize}: thin commands over agent.tools (fakes for the platform)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import agent.scripted_api as api
from agent import cli

REPO = Path(__file__).resolve().parents[3]
SINGLE_RACK = REPO / "case/SingleRack/SingleRack.cfdproj"


def run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def test_identity_command_runs_as_a_module():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-m", "agent", "identity"], cwd=REPO / "src", capture_output=True,
                          text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stderr
    identity = json.loads(done.stdout)
    assert identity["agent"]["apiVersion"] == "1.0" and identity["repository"]["commit"]


def test_edit_command_reads_edits_and_builds(monkeypatch, tmp_path, capsys):
    seen = {}

    def build_project(steps, *, out, base=None, overwrite=False, timeout=600.0):
        seen.update(steps=steps, out=Path(out), base=base, overwrite=overwrite)
        return {"format": "dcdtuni.api.build/v1", "status": "succeeded", "failure": None, "steps": []}
    monkeypatch.setattr(api, "build_project", build_project)
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps([{"op": "MoveModel", "uid": "m_7", "x": 1, "y": 0, "z": 2},
                                 {"op": "RemoveModels", "uids": ["m_14"]}]))
    code, report = run(["edit", str(SINGLE_RACK), "--edits", str(edits), "--out", str(tmp_path / "o.cfdproj")], capsys)
    assert code == 0 and report["status"] == "succeeded"
    assert [s["verb"] for s in seen["steps"]] == ["setModelLocation", "removeModels"]
    assert seen["base"] == str(SINGLE_RACK.resolve()) and seen["overwrite"] is False


def test_evaluate_command_logs_one_physics_call(monkeypatch, tmp_path, capsys):
    calls = []

    def run_air_case(cfdproj, **kwargs):
        calls.append(kwargs)
        return {"format": "dcdtuni.api.result/v1", "kind": "air", "status": "succeeded", "failure": None,
                "stages": {"mesh": {"seconds": 2.}}, "identity": {"repository": {"commit": "c"}}, "inputs": {},
                "metrics": {}}
    monkeypatch.setattr(api, "run_air_case", run_air_case)
    log_root = tmp_path / "runs"
    code, out = run(["evaluate", "air_mesh", str(SINGLE_RACK), "--case-dir", str(tmp_path / "case"),
                     "--mesh-processes", "2", "--max-processes", "2", "--log-root", str(log_root)], capsys)
    assert code == 0 and out["record"]["fidelity"] == "air_mesh" and out["record"]["status"] == "succeeded"
    assert calls[0]["stages"] == ("mesh",) and calls[0]["max_processes"] == 2
    (run_dir,) = (log_root / "logger").iterdir()
    assert len((run_dir / "physics_calls.jsonl").read_text().splitlines()) == 1
    assert json.loads((run_dir / "final_status.json").read_text())["status"] == "completed"


def test_evaluate_command_exit_code_follows_the_result(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(api, "run_liquid_case", lambda source, **kw: {
        "format": "dcdtuni.api.result/v1", "kind": "liquid", "status": "failed",
        "failure": {"stage": "solve", "reason": "no steady state"}, "stages": {}, "identity": {}, "inputs": {}})
    code, out = run(["evaluate", "liquid_steady", str(SINGLE_RACK), "--fd-check", "1"], capsys)
    assert code == 1 and out["record"]["validity"] == "failed:solve"


def test_optimize_is_not_wired_before_the_scenarios(capsys):
    code = cli.main(["optimize", "--scenario", "air-v1"])
    captured = capsys.readouterr()
    assert code == 2 and "scenario" in captured.err


def test_unknown_fidelity_is_refused(capsys):
    with pytest.raises(SystemExit):
        cli.main(["evaluate", "warp", str(SINGLE_RACK)])
