"""``python -m agent.scripted_api``: flags reach the Python API; export-air reports what blocks a run."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.scripted_api import __main__ as cli
from agent.scripted_api import air


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_run(cfdproj, **kwargs):
        calls.append({"cfdproj": cfdproj, **kwargs})
        return {"status": "succeeded", "failure": None, "runDir": "x", "artifacts": {}, "stages": {}, "metrics": {}}
    monkeypatch.setattr(air, "run_air_case", fake_run)
    return calls


def test_air_flags_reach_run_air_case(captured, capsys):
    assert cli.main(["air", "X.cfdproj", "--solve-only", "--solver-type", "transient", "--allow-serial-solve",
                     "--mesh-engine", "snappy", "--solve-processes", "4", "--end-time", "20",
                     "--app", "react"]) == 0
    call, = captured
    assert call["stages"] == ("solve",) and call["solver_type"] == "transient"
    assert call["allow_serial_solve"] is True and call["app"] == "react"
    assert call["run_settings"] == {"meshConfig": {"meshEngine": "snappy"},
                                    "simulationConfig": {"parallelSolve": True, "solveProcess": 4, "endTime": 20.0}}
    cli.main(["air", "X.cfdproj", "--mesh-only"])
    assert captured[-1]["stages"] == ("mesh",) and captured[-1]["app"] == "api"
    cli.main(["air", "X.cfdproj"])
    assert captured[-1]["stages"] == ("mesh", "solve") and captured[-1]["allow_serial_solve"] is False


def test_mesh_only_and_solve_only_exclude_each_other(captured):
    with pytest.raises(SystemExit):
        cli.main(["air", "X.cfdproj", "--mesh-only", "--solve-only"])
    assert captured == []


def test_mesh_engine_choices_are_the_schema_engines(captured):
    parser = cli.build_parser()
    air_parser = parser._subparsers._group_actions[0].choices["air"]
    engine = next(action for action in air_parser._actions if action.dest == "mesh_engine")
    assert tuple(engine.choices) == air.mesh_engines()
    assert set(engine.choices) == {"dcCart", "snappy"}
    for retired_or_unknown in ("meshgen", "gmsh"):
        with pytest.raises(SystemExit):
            cli.main(["air", "X.cfdproj", "--mesh-engine", retired_or_unknown])


def test_help_explains_the_hard_mesh_timeout_and_the_serial_guard():
    air_parser = cli.build_parser()._subparsers._group_actions[0].choices["air"]
    text = " ".join(air_parser.format_help().split())
    assert "hard limit" in text and "soft target" in text
    assert "--allow-serial-solve" in text and "rule 7" in text


def _fake_export(**extra):
    reply = {"cfdrunFilename": "Room.cfdrun", "cfdrunJson": "{}", "stlFiles": {"merged_acu.stl": "solid a\nendsolid a\n"},
             "manifestErrors": [], "preflight": {"seedCollisions": []}, "preflightStops": [],
             "hybrid": {"hasHybridFanServer": False, "budgetVersion": None}, "implementation": {}}
    reply.update(extra)
    return reply


@pytest.mark.parametrize("extra, blockers", [
    ({}, []),
    ({"preflightStops": ["roomWallGaps"], "preflight": {"roomWallGaps": [1]}}, ["preflight:roomWallGaps"]),
    ({"hybrid": {"hasHybridFanServer": True, "budgetVersion": 2}}, ["hybrid"]),
    ({"manifestErrors": ["bad"]}, ["manifestErrors"]),
])
def test_export_air_exit_code_counts_every_blocker(monkeypatch, tmp_path, capsys, extra, blockers):
    monkeypatch.setattr(air, "export_air_case", lambda cfdproj, **kwargs: _fake_export(**extra))
    code = cli.main(["export-air", "Room.cfdproj", "--out-root", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["blockers"] == blockers
    assert code == (0 if not blockers else 1)


def test_export_air_case_layout_is_what_the_server_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(air, "export_air_case", lambda cfdproj, **kwargs: _fake_export())
    assert cli.main(["export-air", "Room.cfdproj", "--out-root", str(tmp_path), "--case-layout"]) == 0
    out = Path(json.loads(capsys.readouterr().out)["outDir"])
    assert (out / "model" / "Room.cfdrun").read_text() == "{}"
    assert (out / "Mesh" / "constant" / "triSurface" / "merged_acu.stl").read_text().startswith("solid a")
    assert not (out / "triSurface").exists()
    # the flat layout stays the default
    assert cli.main(["export-air", "Room.cfdproj", "--out-root", str(tmp_path)]) == 0
    flat = Path(json.loads(capsys.readouterr().out)["outDir"])
    assert (flat / "Room.cfdrun").is_file() and (flat / "triSurface" / "merged_acu.stl").is_file()


# -- liquid modes and inputs, hybrid, build (2026-10-21) ---------------------------------------------
def test_liquid_mode_duration_and_inputs_reach_run_liquid_case(monkeypatch, capsys):
    from agent.scripted_api import liquid
    calls = []

    def fake_run(source, **kwargs):
        calls.append({"source": source, **kwargs})
        return {"status": "succeeded", "failure": None, "runDir": "x", "artifacts": {}, "metrics": {}}
    monkeypatch.setattr(liquid, "run_liquid_case", fake_run)
    assert cli.main(["liquid", "X.cfdproj", "--mode", "transient", "--duration", "12",
                     "--pump-rpm", "m_1_pump=0.8", "--valve-opening", "v1=0.5", "--valve-opening", "v2=1",
                     "--primary-inlet-k", "m_1_hx=290.5"]) == 0
    call, = calls
    assert call["mode"] == "transient" and call["duration_s"] == 12.0
    assert call["inputs"] == {"pump_rpm_ratios": {"m_1_pump": 0.8}, "valve_openings": {"v1": 0.5, "v2": 1.0},
                              "primary_inlet_K": {"m_1_hx": 290.5}}
    assert call["device"] == "cpu"  # CPU unless asked: the Blackwell sm_120 note in scripted-api.md
    cli.main(["liquid", "X.cfdproj"])
    assert calls[-1]["mode"] == "steady" and calls[-1]["inputs"] is None
    with pytest.raises(SystemExit):
        cli.main(["liquid", "X.cfdproj", "--pump-rpm", "no-equals-sign"])


def test_hybrid_flags_reach_run_hybrid_case(monkeypatch, capsys):
    calls = []

    def fake_run(cfdproj, **kwargs):
        calls.append({"cfdproj": cfdproj, **kwargs})
        return {"status": "succeeded", "failure": None, "runDir": "x", "artifacts": {}, "stages": {}, "metrics": {}}
    monkeypatch.setattr(air, "run_hybrid_case", fake_run)
    assert cli.main(["hybrid", "H.cfdproj", "--sample-index", "3", "--mesh-processes", "4",
                     "--solve-processes", "4", "--end-time", "30", "--max-processes", "8", "--solve-only"]) == 0
    call, = calls
    assert call["sample_index"] == 3 and call["stages"] == ("solve",) and call["max_processes"] == 8
    assert call["liquid_device"] == "cpu" and call["liquid_threads"] is None
    assert call["run_settings"] == {"meshConfig": {"parallelMesh": True, "meshProcess": 4},
                                    "simulationConfig": {"parallelSolve": True, "solveProcess": 4, "endTime": 30.0}}
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "succeeded"
    cli.main(["hybrid", "H.cfdproj", "--liquid-device", "project", "--liquid-threads", "2"])
    assert calls[-1]["sample_index"] is None and calls[-1]["stages"] == ("mesh", "solve")
    assert calls[-1]["liquid_device"] is None and calls[-1]["liquid_threads"] == 2


def test_build_reads_a_spec_and_reports_the_self_check(monkeypatch, tmp_path, capsys):
    from agent.scripted_api import build
    calls = []

    def fake_build(steps, **kwargs):
        calls.append({"steps": steps, **kwargs})
        return {"status": "succeeded", "out": str(kwargs["out"]), "selfCheck": {"ok": kwargs.get("base") is None}}
    monkeypatch.setattr(build, "build_project", fake_build)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([{"verb": "createDefaultRackModel"}]))
    assert cli.main(["build", str(spec), "--out", str(tmp_path / "X.cfdproj")]) == 0
    assert calls[-1]["steps"] == [{"verb": "createDefaultRackModel"}] and calls[-1]["base"] is None
    # {"steps": [...], "base": ...} with a base relative to the spec's folder; a failed self-check exits 1
    (tmp_path / "Y.cfdproj").write_text("{}")
    spec.write_text(json.dumps({"steps": [{"verb": "newProject"}], "base": "Y.cfdproj"}))
    assert cli.main(["build", str(spec), "--out", str(tmp_path / "Z.cfdproj")]) == 1
    assert calls[-1]["base"] == str((tmp_path / "Y.cfdproj").resolve())
    assert cli.main(["build", str(spec), "--out", str(tmp_path / "Z.cfdproj"), "--base",
                     str(tmp_path / "Y.cfdproj"), "--overwrite"]) == 1
    assert calls[-1]["overwrite"] is True


def test_a_liquidrun_without_an_output_root_is_an_input_failure_in_json(tmp_path, capsys):
    # stdout carries one JSON document (scripted-api.md), also when the input is refused
    source = tmp_path / "export.liquidrun"
    source.write_text("{}")
    assert cli.main(["liquid", str(source), "--device", "cpu"]) == 1
    reply = json.loads(capsys.readouterr().out)
    assert reply["status"] == "failed" and reply["kind"] == "liquid"
    assert reply["failure"]["stage"] == "input" and "--out-root" in reply["failure"]["reason"]


# -- refused input is one JSON document, never a traceback (2026-10-22, campaign B7) -------------------
@pytest.mark.parametrize("command", [["air"], ["hybrid"], ["liquid"], ["liquid", "--out-root", "OUT"],
                                     ["export-air", "--out-root", "OUT"], ["export-liquid", "--out-root", "OUT"]])
@pytest.mark.parametrize("suffix", [".cfdproj", ".liquidrun"])
def test_a_missing_source_is_an_input_failure_in_json(tmp_path, capsys, command, suffix):
    if suffix == ".liquidrun" and command[0] != "liquid":
        pytest.skip("only liquid takes a .liquidrun")
    missing = tmp_path / "case" / f"nope{suffix}"
    argv = [command[0], str(missing)] + [str(tmp_path / "out") if arg == "OUT" else arg for arg in command[1:]]
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    reply = json.loads(captured.out)
    assert reply["status"] == "failed" and reply["runDir"] is None
    assert reply["failure"]["stage"] == "input" and str(missing) in reply["failure"]["reason"]
    assert "Traceback" not in captured.err
    # nothing was created for a source that does not exist
    assert not (tmp_path / "case").exists() and not (tmp_path / "out").exists()


@pytest.mark.parametrize("command", ["export-air", "export-liquid"])
def test_an_export_the_editor_refuses_is_an_export_failure_in_json(monkeypatch, tmp_path, capsys, command):
    from agent.scripted_api import liquid
    from agent.scripted_api.headless import HeadlessCompileError

    def refuse(*_a, **_k):
        raise HeadlessCompileError("NOPE_patch is not an ACU return this project binds")
    monkeypatch.setattr(air, "export_air_case", refuse)
    monkeypatch.setattr(liquid, "export_liquidrun", refuse)
    project = tmp_path / "Room.cfdproj"
    project.write_text("{}")
    assert cli.main([command, str(project), "--out-root", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    reply = json.loads(captured.out)
    assert reply["status"] == "failed" and reply["kind"] == command
    assert reply["failure"] == {"stage": "export", "reason": "NOPE_patch is not an ACU return this project binds"}
    assert reply["outDir"] is None and "Traceback" not in captured.err
    assert not (tmp_path / "out").exists()


def test_the_air_summary_carries_why_a_run_is_not_converged(capsys, monkeypatch):
    reason = "too-short: 20 iterations < minimum 1000 (the verdict's last window, 20 % of the run, must span 200 iterations)"
    monkeypatch.setattr(air, "run_air_case", lambda cfdproj, **kwargs: {
        "status": "succeeded", "failure": None, "runDir": "x", "artifacts": {}, "stages": {},
        "metrics": {"convergence": {"verdict": "INCONCLUSIVE", "reason": reason}}})
    assert cli.main(["air", "X.cfdproj", "--solve-only", "--solve-processes", "2", "--end-time", "20"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["convergence"] == "INCONCLUSIVE" and summary["convergenceReason"] == reason


# -- a refused batch, and a source that is not JSON, name what failed (2026-10-22) ---------------------
@pytest.mark.parametrize("content", [None, "{not json", "[1, 2"], ids=["missing", "broken-object", "broken-list"])
def test_a_missing_or_unparsable_batch_spec_is_an_input_failure_in_json(tmp_path, capsys, content):
    spec = tmp_path / "batch.json"
    if content is not None:
        spec.write_text(content)
    assert cli.main(["batch", str(spec), "--out-root", str(tmp_path / "out"), "--max-processes", "2"]) == 1
    captured = capsys.readouterr()
    reply = json.loads(captured.out)
    assert reply["kind"] == "batch" and reply["status"] == "failed"
    assert reply["batchDir"] is None and reply["runDir"] is None
    assert reply["failure"]["stage"] == "input" and str(spec) in reply["failure"]["reason"]
    assert ("not valid JSON" in reply["failure"]["reason"]) == (content is not None)
    assert "Traceback" not in captured.err and "usage:" not in captured.err
    assert not (tmp_path / "out").exists()


def test_a_batch_the_api_cannot_schedule_is_an_input_failure_in_json(tmp_path, capsys):
    spec = tmp_path / "batch.json"
    spec.write_text(json.dumps([{"cfdproj": "a/A.cfdproj"}]))
    assert cli.main(["batch", str(spec), "--out-root", str(tmp_path / "out")]) == 1  # no rank budget
    reply = json.loads(capsys.readouterr().out)
    assert reply["failure"]["stage"] == "input" and "--max-processes" in reply["failure"]["reason"]
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("text, message", [("{bad", "--run-settings is not valid JSON"),
                                           ("[1]", "--run-settings must be a JSON object")])
@pytest.mark.parametrize("command", [["air"], ["hybrid"], ["export-air", "--out-root", "OUT"]])
def test_run_settings_that_are_not_a_json_object_name_the_flag(captured, tmp_path, capsys, command, text, message):
    argv = [command[0], "X.cfdproj", "--run-settings", text] + [str(tmp_path / "out") if arg == "OUT" else arg
                                                                 for arg in command[1:]]
    assert cli.main(argv) == 1
    reply = json.loads(capsys.readouterr().out)
    assert reply["failure"]["stage"] == "input" and reply["failure"]["reason"].startswith(message)
    assert captured == [] and not (tmp_path / "out").exists()


@pytest.mark.parametrize("command", [["air"], ["hybrid"], ["liquid"], ["liquid", "--out-root", "OUT"],
                                     ["export-air", "--out-root", "OUT"], ["export-liquid", "--out-root", "OUT"]])
@pytest.mark.parametrize("suffix", [".cfdproj", ".liquidrun"])
def test_a_source_that_is_not_json_is_an_input_failure_naming_the_file(tmp_path, capsys, command, suffix):
    if suffix == ".liquidrun" and command != ["liquid", "--out-root", "OUT"]:
        pytest.skip("only liquid --out-root takes a .liquidrun")
    case = tmp_path / "case"
    case.mkdir()
    source = case / f"Room{suffix}"
    source.write_text('{"schemaVersion": 4, "constructions": ')
    argv = [command[0], str(source)] + [str(tmp_path / "out") if arg == "OUT" else arg for arg in command[1:]]
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    reply = json.loads(captured.out)
    assert reply["status"] == "failed" and reply["runDir"] is None
    assert reply["failure"]["stage"] == "input"
    assert reply["failure"]["reason"].startswith(f"{source} is not valid JSON")
    assert "Traceback" not in captured.err
    # refused before any result directory was made
    assert sorted(path.name for path in case.iterdir()) == [source.name] and not (tmp_path / "out").exists()


def test_a_build_spec_that_is_not_json_names_the_file(tmp_path, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text("[{")
    with pytest.raises(SystemExit):
        cli.main(["build", str(spec), "--out", str(tmp_path / "X.cfdproj")])
    assert f"{spec.resolve()} is not valid JSON" in capsys.readouterr().err
