"""Evaluation tools on stand-ins for agent.scripted_api: the right calls, CPU by default, the API's guards kept."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import agent.scripted_api as api
from agent.scripted_api.headless import HeadlessCompileError

from agent.tools import evaluate as tools
from agent.tools.results import AirView, HybridView, LiquidView

REPO = Path(__file__).resolve().parents[3]
SINGLE_RACK = REPO / "case/SingleRack/SingleRack.cfdproj"


def air_result(kind="air", **extra):
    return {"format": "dcdtuni.api.result/v1", "kind": kind, "status": "succeeded", "failure": None,
            "stages": {"mesh": {"seconds": 3.}}, "metrics": {}, "identity": {"repository": {"commit": "c"}},
            "inputs": {}, **extra}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def recorder(name, result):
        # Bind against the real agent.scripted_api signature: a misspelt or removed keyword fails here.
        signature = inspect.signature(getattr(api, name))

        def call(*args, **kwargs):
            signature.bind(*args, **kwargs)
            seen.append((name, args, kwargs))
            return result(kwargs) if callable(result) else result
        return call
    monkeypatch.setattr(api, "run_air_case", recorder("run_air_case", air_result()))
    monkeypatch.setattr(api, "run_hybrid_case", recorder("run_hybrid_case", air_result("hybrid")))
    liquid = {"format": "dcdtuni.api.result/v1", "kind": "liquid", "status": "succeeded", "failure": None,
              "stages": {"solve": {"seconds": 1.}}, "identity": {},
              "metrics": {"gradients": {"max_chip_temperature_K": {"heat_W": {"cp": .01}, "pump_rpm_ratio": {"p": -2.}},
                                        "pump_hydraulic_power_W": {"heat_W": {"cp": 0.}}}}}
    monkeypatch.setattr(api, "run_liquid_case", recorder("run_liquid_case", liquid))
    monkeypatch.setattr(api, "run_batch", recorder("run_batch", {"format": "dcdtuni.api.batch-result/v1",
                                                                 "status": "succeeded", "items": []}))
    return seen


def test_mesh_solve_and_full_air_runs_select_their_stages(calls, tmp_path):
    view = tools.mesh_air(SINGLE_RACK, case_dir=tmp_path, mesh_processes=2, max_processes=4, mesh_engine="dcCart")
    assert isinstance(view, AirView) and view["status"] == "succeeded"
    name, args, kwargs = calls[-1]
    assert name == "run_air_case" and args == (SINGLE_RACK,) and kwargs["stages"] == ("mesh",)
    assert kwargs["case_dir"] == tmp_path and kwargs["max_processes"] == 4
    assert kwargs["run_settings"] == {"meshConfig": {"meshEngine": "dcCart", "parallelMesh": True, "meshProcess": 2}}
    assert "allow_serial_solve" not in kwargs
    tools.solve_air(SINGLE_RACK, case_dir=tmp_path, solve_processes=4, max_processes=4, end_time=50,
                    solver_type="steady")
    kwargs = calls[-1][2]
    assert kwargs["stages"] == ("solve",) and kwargs["solver_type"] == "steady"
    assert kwargs["run_settings"] == {"simulationConfig": {"parallelSolve": True, "solveProcess": 4, "endTime": 50}}
    tools.evaluate_air(SINGLE_RACK, case_dir=tmp_path, mesh_processes=2, solve_processes=4, max_processes=6,
                       label="cand-1")
    kwargs = calls[-1][2]
    assert kwargs["stages"] == ("mesh", "solve") and kwargs["label"] == "cand-1"
    assert kwargs["run_settings"]["meshConfig"]["meshProcess"] == 2
    assert kwargs["run_settings"]["simulationConfig"]["solveProcess"] == 4
    assert kwargs["liquid_device"] == "cpu"


def test_process_counts_are_required_and_positive(calls, tmp_path):
    with pytest.raises(TypeError):
        tools.mesh_air(SINGLE_RACK, case_dir=tmp_path, mesh_processes=2)
    with pytest.raises(ValueError):
        tools.mesh_air(SINGLE_RACK, case_dir=tmp_path, mesh_processes=0, max_processes=2)


def test_hybrid_and_liquid_run_on_cpu(calls, tmp_path):
    view = tools.evaluate_hybrid(SINGLE_RACK, case_dir=tmp_path, mesh_processes=2, solve_processes=2,
                                 max_processes=4, sample_index=3, liquid_threads=2)
    assert isinstance(view, HybridView)
    kwargs = calls[-1][2]
    assert calls[-1][0] == "run_hybrid_case" and kwargs["liquid_device"] == "cpu" and kwargs["sample_index"] == 3
    assert kwargs["stages"] == ("mesh", "solve")
    liquid = tools.evaluate_liquid(SINGLE_RACK, fd_check=1, threads=2, label="g")
    assert isinstance(liquid, LiquidView)
    name, args, kwargs = calls[-1]
    assert name == "run_liquid_case" and kwargs["device"] == "cpu" and kwargs["fd_check"] == 1
    assert kwargs["mode"] == "steady" and kwargs["threads"] == 2 and kwargs["label"] == "g"


def test_liquid_gradient_reads_a_result_or_runs_one(calls):
    result = tools.evaluate_liquid(SINGLE_RACK)
    assert tools.liquid_gradient(result, "max_chip_temperature_K", ["heat_W"]) == {"heat_W": {"cp": .01}}
    assert tools.liquid_gradient(result.raw, "max_chip_temperature_K") == {"heat_W": {"cp": .01},
                                                                         "pump_rpm_ratio": {"p": -2.}}
    before = len(calls)
    assert tools.liquid_gradient(SINGLE_RACK, "max_chip_temperature_K", ["pump_rpm_ratio"], fd_check=1) == {
        "pump_rpm_ratio": {"p": -2.}}
    kwargs = calls[before][2]
    assert kwargs["objectives"] == ["max_chip_temperature_K"] and kwargs["gradient_parameters"] == ["pump_rpm_ratio"]
    with pytest.raises(KeyError):
        tools.liquid_gradient(result, "mean_primary_return_temperature_K")
    with pytest.raises(KeyError):
        tools.liquid_gradient(result, "max_chip_temperature_K", ["valve_opening"])


def test_evaluate_many_defaults_liquid_items_to_cpu(calls, tmp_path):
    tools.evaluate_many([{"cfdproj": "a.cfdproj", "kind": "liquid"}, {"cfdproj": "b.cfdproj"},
                         {"cfdproj": "c.cfdproj", "kind": "liquid", "device": "cuda:0"}],
                        out_root=tmp_path, max_parallel=2, max_processes=4)
    name, args, kwargs = calls[-1]
    items = args[0]
    assert items[0]["device"] == "cpu" and "device" not in items[1] and items[2]["device"] == "cuda:0"
    assert kwargs == {"out_root": tmp_path, "max_parallel": 2, "max_processes": 4, "label": None}


def test_admit_air_reports_blockers_without_a_server(monkeypatch):
    def export_air_case(cfdproj, *, run_settings=None, solver_type=None, timestamp=None, mesh_patches=None):
        return {"cfdrunFilename": "SingleRack.cfdrun", "cfdrunJson": '{"version": 2}',
                "stlFiles": {"a.stl": "solid a\nendsolid a\n"}, "manifestErrors": [],
                "preflight": {"roomWallGaps": [1]}, "preflightStops": ["roomWallGaps"],
                "hybrid": {"hasHybridFanServer": False, "budgetVersion": None}, "simulationMode": {"mode": "steady"}}
    monkeypatch.setattr(api, "export_air_case", export_air_case)
    monkeypatch.setattr(api, "run_air_case", lambda *a, **k: pytest.fail("admission must not start a server"))
    admitted = tools.admit_air(SINGLE_RACK)
    assert admitted["format"] == "agent.tools.admission/v1" and admitted["status"] == "succeeded"
    assert admitted["admitted"] is False and admitted["blockers"] == ["preflight:roomWallGaps"]
    assert admitted["stlFiles"]["a.stl"]["bytes"] == len("solid a\nendsolid a\n")
    # the manifest is text; its hash is the one a run records as stages.export.cfdrunSha256
    assert admitted["cfdrunSha256"] == __import__("hashlib").sha256(b'{"version": 2}').hexdigest()
    assert admitted["inputs"]["cfdprojSha256"] == tools.project_identity(SINGLE_RACK)["sha256"]
    assert admitted["inputs"]["effectiveProjectSha256"] == tools.project_identity(SINGLE_RACK)["canonicalSha256"]
    assert "export" not in admitted and admitted.stage_seconds()["export"] >= 0
    json.dumps(admitted.raw)
    assert "export" in tools.admit_air(SINGLE_RACK, keep_export=True).raw

    def refuse(*args, **kwargs):
        raise HeadlessCompileError("two entities share a name")
    monkeypatch.setattr(api, "export_air_case", refuse)
    refused = tools.admit_air(SINGLE_RACK)
    assert refused["status"] == "failed" and refused["failure"]["stage"] == "export"
    assert refused["admitted"] is False


def test_identity_reports_the_api_identity_and_agent_version():
    identity = tools.identity()
    assert identity["agent"]["apiVersion"] == "1.0"
    assert identity["repository"]["commit"] and identity["host"]["hostname"] and identity["python"]["python"]


STEADY = {"format": "dcdtuni.api.liquid-steady/v1", "liquidrunSha256": "a" * 64, "configHash": "h",
          "objectives": {"max_chip_temperature_K": 310.},
          "gradients": {"max_chip_temperature_K": {"heat_W": {"cp_1": .02}}},
          "gradientParameters": ["heat_W"], "finiteDifferenceCheck": [],
          "metrics": {"maxChipTemperatureK": 310., "edges": [{"id": "e1", "massFlowKgS": .1}], "nodes": []}}


def test_liquid_steady_solves_in_process_on_cpu_and_reads_like_a_result(monkeypatch):
    seen = []
    signature = inspect.signature(api.solve_liquid_steady)

    def solve_liquid_steady(*args, **kwargs):
        signature.bind(*args, **kwargs)
        seen.append(kwargs)
        return dict(STEADY)
    monkeypatch.setattr(api, "solve_liquid_steady", solve_liquid_steady)
    liquidrun = {"version": 4, "analysis": {"mode": "steady"}}
    result = tools.liquid_steady(liquidrun, objectives=["max_chip_temperature_K"], gradient_parameters=["heat_W"],
                                 fd_check=1, threads=2)
    assert isinstance(result, LiquidView) and result.succeeded and result.kind == "liquid"
    assert result.format == tools.LIQUID_STEADY_FORMAT and result.result_json is None
    assert seen[-1]["device"] == "cpu" and seen[-1]["fd_check"] == 1 and seen[-1]["threads"] == 2
    assert result.metrics is not STEADY and result.metrics["format"] == STEADY["format"]
    assert result.max_chip_temperature_K == 310. and result.edges == [{"id": "e1", "massFlowKgS": .1}]
    assert result.gradient("max_chip_temperature_K") == {"heat_W": {"cp_1": .02}}
    # Hashed before the solve (the API's own canonical_sha256), so a refused manifest carries it too.
    from agent.scripted_api import identity as api_identity
    assert result.inputs["liquidrunSha256"] == api_identity.canonical_sha256(liquidrun)
    assert result.stage_seconds()["steady"] >= 0
    assert result.identity["repository"]["commit"]
    assert tools.liquid_gradient(liquidrun, "max_chip_temperature_K", ["heat_W"]) == {"heat_W": {"cp_1": .02}}
    assert seen[-1]["objectives"] == ["max_chip_temperature_K"] and seen[-1]["gradient_parameters"] == ["heat_W"]

    def refused(*args, **kwargs):
        raise ValueError("unsupported liquidrun version None")
    monkeypatch.setattr(api, "solve_liquid_steady", refused)
    failed = tools.liquid_steady({"analysis": {}})
    assert failed.status == "failed" and failed.failure_stage == "input"
    assert "unsupported liquidrun version" in failed.failure["reason"]
    with pytest.raises(RuntimeError, match="input"):
        tools.liquid_gradient({"analysis": {}}, "max_chip_temperature_K")
