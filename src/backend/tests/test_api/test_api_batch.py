"""Batch air runs: one private server per item, a shared MPI-rank budget, a summary that never overwrites.

``run_air_case`` is replaced by a recorder that sleeps, so these tests pin the
scheduling, the per-job rank cap, the refusals and ``batch-result.json``
without starting servers (the real two-item batch is run by hand, see
docs/reference/scripted-api.md).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from agent.scripted_api import air, batch
from agent.scripted_api import __main__ as cli


class Recorder:
    def __init__(self, fail_index=None):
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.calls = []
        self.fail_index = fail_index

    def __call__(self, cfdproj, **kwargs):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.calls.append({"cfdproj": str(cfdproj), **kwargs})
        time.sleep(0.2)
        with self.lock:
            self.running -= 1
        run_dir = Path(kwargs["case_dir"]) / "log" / "api" / f"run_{kwargs['label']}"
        run_dir.mkdir(parents=True, exist_ok=True)
        failed = self.fail_index is not None and kwargs["label"].endswith(f"{self.fail_index:02d}")
        return {"status": "failed" if failed else "succeeded",
                "failure": {"stage": "mesh", "reason": "x"} if failed else None,
                "runDir": str(run_dir), "artifacts": {"resultJson": str(run_dir / "result.json")},
                "inputs": {"stages": list(kwargs["stages"])}, "elapsedSeconds": 0.2}


def _cases(tmp_path, count):
    items = []
    for index in range(count):
        case = tmp_path / f"case{index}"
        case.mkdir()
        (case / "Room.cfdproj").write_text("{}")
        items.append({"cfdproj": str(case / "Room.cfdproj")})
    return items


def test_batch_caps_concurrency_and_ranks_and_keeps_old_directories(monkeypatch, tmp_path):
    recorder = Recorder()
    monkeypatch.setattr(air, "run_air_case", recorder)
    out = tmp_path / "out"
    old = out / "earlier-batch"
    old.mkdir(parents=True)
    (old / "batch-result.json").write_text("keep me")
    items = _cases(tmp_path, 5)
    items[1]["stages"] = ["mesh"]
    items[2].update(solveProcesses=2, endTime=3, solverType="steady")
    result = batch.run_batch(items, out_root=out, max_parallel=2, max_processes=8)
    assert result["status"] == "succeeded"
    assert recorder.peak == 2
    assert result["processesPerJob"] == 4
    assert all(call["max_processes"] == 4 for call in recorder.calls)
    by_case = {Path(call["case_dir"]).name: call for call in recorder.calls}
    assert by_case["case1"]["stages"] == ("mesh",)
    assert by_case["case0"]["stages"] == ("mesh", "solve")
    assert by_case["case2"]["run_settings"] == {"simulationConfig": {"parallelSolve": True, "solveProcess": 2,
                                                                     "endTime": 3}}
    assert by_case["case2"]["solver_type"] == "steady"
    assert (old / "batch-result.json").read_text() == "keep me"
    written = json.loads((Path(result["batchDir"]) / "batch-result.json").read_text())
    assert written["format"] == batch.BATCH_FORMAT
    assert [row["index"] for row in written["items"]] == [0, 1, 2, 3, 4]
    assert all(row["status"] == "succeeded" and row["resultJson"] for row in written["items"])
    assert Path(result["batchDir"]).parent == out and Path(result["batchDir"]) != old


def test_one_failed_item_fails_the_batch_but_not_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(air, "run_air_case", Recorder(fail_index=1))
    result = batch.run_batch(_cases(tmp_path, 3), out_root=tmp_path / "out", max_parallel=3, max_processes=6)
    assert result["status"] == "failed"
    assert [row["status"] for row in result["items"]] == ["succeeded", "failed", "succeeded"]
    assert result["items"][1]["failure"] == {"stage": "mesh", "reason": "x"}


def test_batch_refuses_what_it_cannot_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(air, "run_air_case", Recorder())
    items = _cases(tmp_path, 2)
    with pytest.raises(ValueError, match="max_parallel"):
        batch.run_batch(items, out_root=tmp_path / "out", max_parallel=4, max_processes=2)
    with pytest.raises(ValueError, match="same case"):
        batch.run_batch([items[0], dict(items[0])], out_root=tmp_path / "out", max_parallel=1, max_processes=2)
    with pytest.raises(ValueError, match="kind"):
        batch.run_batch([{**items[0], "kind": "thermal"}], out_root=tmp_path / "out", max_parallel=1, max_processes=2)
    with pytest.raises(ValueError, match="unknown"):
        batch.run_batch([{**items[0], "meshEngin": "dcCart"}], out_root=tmp_path / "out", max_parallel=1,
                        max_processes=2)
    with pytest.raises(ValueError, match="maxProcesses"):
        batch.run_batch([{**items[0], "maxProcesses": 8}], out_root=tmp_path / "out", max_parallel=1,
                        max_processes=4)
    assert not (tmp_path / "out").exists()


def test_cli_batch_resolves_paths_against_the_spec(monkeypatch, tmp_path, capsys):
    recorder = Recorder()
    monkeypatch.setattr(air, "run_air_case", recorder)
    _cases(tmp_path, 2)
    spec = tmp_path / "batch.json"
    spec.write_text(json.dumps({"maxParallel": 2, "maxProcesses": 8,
                                "items": [{"cfdproj": "case0/Room.cfdproj", "stages": ["mesh"]},
                                          {"cfdproj": "case1/Room.cfdproj", "label": "second"}]}))
    assert cli.main(["batch", str(spec), "--out-root", str(tmp_path / "out")]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "succeeded" and summary["processesPerJob"] == 4
    assert {Path(call["cfdproj"]) for call in recorder.calls} == {tmp_path / "case0" / "Room.cfdproj",
                                                                  tmp_path / "case1" / "Room.cfdproj"}
    assert sorted(call["label"] for call in recorder.calls) == ["batch00", "second"]


def test_batch_paths_are_absolute_even_from_a_relative_out_root(monkeypatch, tmp_path):
    monkeypatch.setattr(air, "run_air_case", Recorder())
    items = _cases(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    result = batch.run_batch(items, out_root="out", max_parallel=1, max_processes=2)
    assert Path(result["batchDir"]).is_absolute() and Path(result["resultJson"]).is_absolute()
    assert Path(result["batchDir"]).parent == tmp_path / "out"


# -- hybrid and liquid items (2026-10-21) --------------------------------------------------------------
def test_hybrid_and_liquid_items_reach_their_own_api(monkeypatch, tmp_path):
    from agent.scripted_api import liquid
    air_calls, hybrid_calls, liquid_calls = Recorder(), Recorder(), []

    def fake_liquid(source, **kwargs):
        liquid_calls.append({"source": str(source), **kwargs})
        return {"status": "succeeded", "failure": None, "runDir": str(tmp_path / "l"),
                "artifacts": {"resultJson": str(tmp_path / "l" / "result.json")}, "elapsedSeconds": 0.1}
    monkeypatch.setattr(air, "run_air_case", air_calls)
    monkeypatch.setattr(air, "run_hybrid_case", hybrid_calls)
    monkeypatch.setattr(liquid, "run_liquid_case", fake_liquid)
    items = _cases(tmp_path, 3)
    items[1].update(kind="hybrid", sampleIndex=2, liquidDevice="cpu", stages=["solve"], meshProcesses=2)
    items[2].update(kind="liquid", mode="transient", durationS=10, inputs={"pump_rpm_ratios": {"p": 0.8}},
                    threads=2)
    # a liquid run needs no server, so it may share a case with an air item
    items.append({"cfdproj": items[0]["cfdproj"], "kind": "liquid", "label": "steady-on-case0"})
    result = batch.run_batch(items, out_root=tmp_path / "out", max_parallel=2, max_processes=8)
    assert result["status"] == "succeeded", result["items"]
    assert [row["kind"] for row in result["items"]] == ["air", "hybrid", "liquid", "liquid"]
    hybrid_call, = hybrid_calls.calls
    assert hybrid_call["sample_index"] == 2 and hybrid_call["liquid_device"] == "cpu"
    assert hybrid_call["stages"] == ("solve",) and hybrid_call["max_processes"] == 4
    assert hybrid_call["run_settings"] == {"meshConfig": {"parallelMesh": True, "meshProcess": 2}}
    transient = next(call for call in liquid_calls if call.get("mode") == "transient")
    assert transient["duration_s"] == 10 and transient["inputs"] == {"pump_rpm_ratios": {"p": 0.8}}
    assert transient["threads"] == 2 and transient["device"] == "cpu"
    steady = next(call for call in liquid_calls if call["label"] == "steady-on-case0")
    assert steady["mode"] == "steady" and steady["threads"] is None


def test_kind_specific_keys_and_budgets_are_checked_before_anything_runs(monkeypatch, tmp_path):
    items = _cases(tmp_path, 2)
    out = tmp_path / "out"
    for bad, message in (({**items[0], "mode": "transient"}, "unknown"),                      # a liquid key on air
                         ({**items[0], "kind": "liquid", "meshProcesses": 2}, "unknown"),     # an air key on liquid
                         ({**items[0], "kind": "hybrid", "stages": ["mesh"]}, "solve"),
                         ({**items[0], "kind": "liquid", "threads": 8}, "threads"),
                         ({**items[0], "kind": "liquid", "mode": "implicit"}, "mode")):
        with pytest.raises(ValueError, match=message):
            batch.run_batch([bad], out_root=out, max_parallel=1, max_processes=4)
    with pytest.raises(ValueError, match="same case"):
        batch.run_batch([items[0], {**items[0], "kind": "hybrid"}], out_root=out, max_parallel=1, max_processes=4)
    assert not out.exists()


# -- an item whose source is missing or not JSON fails at input, as `air` reports it (2026-10-22) ------
@pytest.mark.parametrize("content", [None, "{not json"], ids=["missing", "not-json"])
def test_an_item_whose_source_is_missing_or_not_json_fails_at_input(tmp_path, content):
    items = []
    for index, kind in enumerate(("air", "hybrid", "liquid")):
        source = tmp_path / f"case{index}" / "Room.cfdproj"
        if content is not None:
            source.parent.mkdir()
            source.write_text(content)
        items.append({"cfdproj": str(source), "kind": kind})
    result = batch.run_batch(items, out_root=tmp_path / "out", max_parallel=1, max_processes=2)
    assert result["status"] == "failed"
    for row, item in zip(result["items"], items):
        assert row["status"] == "failed" and row["runDir"] is None, row
        assert row["failure"]["stage"] == "input", row
        assert item["cfdproj"] in row["failure"]["reason"] and "traceback" not in row["failure"]
        # refused before anything was made for it
        case = Path(item["cfdproj"]).parent
        assert not case.exists() if content is None else sorted(p.name for p in case.iterdir()) == ["Room.cfdproj"]
    written = json.loads((Path(result["batchDir"]) / "batch-result.json").read_text())
    assert [row["failure"]["stage"] for row in written["items"]] == ["input"] * 3
