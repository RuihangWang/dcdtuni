"""Fidelity dispatch: budget checked before the call, every attempt charged and logged as a physics call."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.records import CostLedger, RunLogger, RunStore
from agent.tools import fidelity as F
from agent.tools.fidelity import Budget, BudgetExhausted, EvaluationRecord, Fidelity, evaluate
from agent.tools.results import view

REPO = Path(__file__).resolve().parents[3]
SINGLE_RACK = REPO / "case/SingleRack/SingleRack.cfdproj"


@pytest.fixture
def logger(tmp_path):
    store = RunStore(tmp_path / "root", create=True)
    return RunLogger(store, "fidelity", config={}, identity={"model": "none", "data": "none", "seed": 0,
                                                            "purpose": "unit"}, source_identity={"git": "unit"})


def fake_result(tmp_path, status="succeeded", kind="air"):
    run = tmp_path / f"run-{status}-{kind}"
    run.mkdir(exist_ok=True)
    raw = {"format": "dcdtuni.api.result/v1", "kind": kind, "status": status,
           "failure": None if status == "succeeded" else {"stage": "mesh", "reason": "x"},
           "stages": {"export": {"seconds": 1.}, "mesh": {"seconds": 4.}}, "elapsedSeconds": 6.,
           "identity": {"repository": {"commit": "c" * 40}, "tools": {"mesher": "dcCartMesh"}},
           "inputs": {"cfdprojSha256": "a" * 64, "effectiveProjectSha256": "b" * 64},
           "metrics": {}, "artifacts": {"resultJson": str(run / "result.json")}}
    (run / "result.json").write_text(json.dumps(raw))
    return view(raw, run / "result.json")


def test_air_mesh_call_is_logged_with_result_identity_and_costs(logger, tmp_path):
    seen = []

    def tool(candidate, **options):
        seen.append((candidate, options))
        return fake_result(tmp_path)
    budget = Budget(max_calls=2)
    ledger = CostLedger()
    record = evaluate(SINGLE_RACK, Fidelity.AIR_MESH, budget=budget, logger=logger, ledger=ledger, tool=tool,
                      case_dir=tmp_path, mesh_processes=2, max_processes=2)
    assert isinstance(record, EvaluationRecord)
    assert seen == [(SINGLE_RACK, {"case_dir": tmp_path, "mesh_processes": 2, "max_processes": 2})]
    assert record.fidelity is Fidelity.AIR_MESH and record.status == "succeeded" and record.validity == "valid"
    assert record.information == ("mesh",) and record.derivatives == ()
    assert record.evaluator["identity"]["tools"]["mesher"] == "dcCartMesh"
    assert record.evaluator["tool"].endswith(".tool")  # the callable actually used
    assert record.evaluator["agentApiVersion"] == "1.0"
    assert record.costs["stage_seconds"] == {"export": 1., "mesh": 4.} and record.costs["elapsedSeconds"] == 6.
    assert record.costs["process"]["process_own_cpu_seconds"] >= 0
    assert record.design["cfdprojSha256"] == "a" * 64
    assert record.result_identity["format"] == "dcdtuni.api.result/v1"
    assert budget.calls == 1 and len(ledger.attempts) == 1
    (line,) = (logger.path / "physics_calls.jsonl").read_text().splitlines()
    call = json.loads(line)
    assert call["fidelity"] == "air_mesh" and call["status"] == "succeeded"
    assert call["evaluator"] == record.evaluator and call["costs"] == record.costs
    assert call["result"]["path"].endswith("result.json") and call["project_sha256"]
    json.dumps(record.as_record())


def test_budget_is_checked_before_the_call_and_failures_are_charged(logger, tmp_path):
    calls = []

    def tool(candidate, **options):
        calls.append(candidate)
        return fake_result(tmp_path, status="failed")
    budget = Budget(max_calls_by_fidelity={"air_mesh": 1})
    record = evaluate(SINGLE_RACK, Fidelity.AIR_MESH, budget=budget, logger=logger, tool=tool)
    assert record.status == "failed" and record.validity == "failed:mesh"
    with pytest.raises(BudgetExhausted):
        evaluate(SINGLE_RACK, Fidelity.AIR_MESH, budget=budget, logger=logger, tool=tool)
    assert len(calls) == 1 and budget.calls_by_fidelity == {"air_mesh": 1}
    assert len((logger.path / "physics_calls.jsonl").read_text().splitlines()) == 1


def test_cost_budget_stops_after_the_envelope_is_spent(tmp_path):
    budget = Budget(max_wall_seconds=5.)
    budget.check(Fidelity.LF)
    budget.charge(Fidelity.LF, {"process_wall_seconds": 6.})
    with pytest.raises(BudgetExhausted, match="wall"):
        budget.check(Fidelity.LF)
    assert budget.remaining()["wall_seconds"] == 0.


def test_a_raising_tool_is_charged_and_logged_then_re_raised(logger):
    budget = Budget(max_calls=3)
    ledger = CostLedger()

    def tool(candidate, **options):
        raise RuntimeError("server did not start")
    with pytest.raises(RuntimeError):
        evaluate(SINGLE_RACK, Fidelity.AIR_FULL, budget=budget, logger=logger, ledger=ledger, tool=tool)
    assert budget.calls == 1 and ledger.attempts[0]["status"] == "failed"
    call = json.loads((logger.path / "physics_calls.jsonl").read_text().splitlines()[0])
    assert call["status"] == "error" and "server did not start" in call["error"]


def test_lf_needs_its_model_and_liquid_steady_reports_gradients(logger, tmp_path):
    with pytest.raises(ValueError, match="lf="):
        evaluate(SINGLE_RACK, Fidelity.LF, logger=logger)
    record = evaluate(SINGLE_RACK, Fidelity.LF, logger=logger, information=("I0", "I3"),
                      lf=lambda candidate: {"status": "succeeded", "I0": -1., "evaluator": {"name": "l0", "version": "1"}})
    assert record.information == ("I0", "I3") and record.evaluator["identity"] == {"name": "l0", "version": "1"}
    liquid = {"format": "dcdtuni.api.result/v1", "kind": "liquid", "status": "succeeded", "failure": None,
              "stages": {"solve": {"seconds": 1.}}, "identity": {"solver": {"name": "dae"}},
              "inputs": {"sourceSha256": "d" * 64, "gradientParameters": ["heat_W", "pump_rpm_ratio"]},
              "metrics": {"gradients": {"max_chip_temperature_K": {}}}}
    record = evaluate(SINGLE_RACK, Fidelity.LIQUID_STEADY, logger=logger, tool=lambda c, **o: view(liquid))
    assert record.information == ("I0", "I1", "I3") and record.derivatives == ("heat_W", "pump_rpm_ratio")
    assert record.design["sourceSha256"] == "d" * 64


def test_dispatch_table_names_every_fidelity():
    from agent.tools import evaluate as tools
    assert set(F.DISPATCH) == set(Fidelity) - {Fidelity.LF}
    assert F.DISPATCH[Fidelity.AIR_MESH] is tools.mesh_air and F.DISPATCH[Fidelity.ADMISSION] is tools.admit_air
    assert Fidelity("air_full") is Fidelity.AIR_FULL
