"""read_result and the typed views: the raw result.json stays as it is; views only read it."""
from __future__ import annotations

import json

import pytest

from agent.tools.results import AirView, HybridView, LiquidView, ResultView, read_result, view

AIR = {
    "format": "dcdtuni.api.result/v1", "kind": "air", "status": "succeeded", "failure": None,
    "runDir": "/x/run", "elapsedSeconds": 12.5,
    "inputs": {"cfdprojSha256": "a" * 64, "effectiveProjectSha256": "b" * 64, "stages": ["mesh", "solve"]},
    "identity": {"repository": {"commit": "c" * 40}, "host": {"hostname": "h"}},
    "stages": {"export": {"seconds": 1.5}, "mesh": {"seconds": 9.0, "cells": 65584, "checkMeshOk": True,
                                                     "qualityReceipt": {"status": "accepted"}},
               "solve": {"seconds": 40.0}, "metrics": {"seconds": 0.5}, "server": {"startupSeconds": .6}},
    "metrics": {"convergence": {"verdict": "CONVERGED"},
                "fields": {"rackInlet": {"summary": {"maxAreaMeanC": 24.1}},
                           "acus": [{"returnC": 30.0}], "energyBalance": {"acuHeatRemovedW": 1000.}}},
    "artifacts": {"resultJson": "/x/run/result.json"},
}
LIQUID = {
    "format": "dcdtuni.api.result/v1", "kind": "liquid", "status": "succeeded", "failure": None,
    "inputs": {"mode": "steady"}, "identity": {}, "stages": {"export": {"seconds": .3}, "solve": {"seconds": 2.}},
    "metrics": {"format": "dcdtuni.api.liquid-steady/v1",
                "objectives": {"max_chip_temperature_K": 330.0},
                "gradients": {"max_chip_temperature_K": {"heat_W": {"cp_1": 0.01}, "pump_rpm_ratio": {"p": -3.0}}},
                "finiteDifferenceCheck": [{"objective": "max_chip_temperature_K", "parameter": "heat_W",
                                           "component": "cp_1", "adjoint": 0.01, "finiteDifference": 0.0100001}],
                "metrics": {"maxChipTemperatureK": 330.0, "edges": [{"id": "e"}], "nodes": [{"id": "n"}]}},
}


def test_view_dispatches_by_kind_and_keeps_the_raw_dict():
    air = view(AIR)
    assert isinstance(air, AirView) and air.raw is AIR
    assert air["status"] == "succeeded" and dict(air) == AIR and len(air) == len(AIR)
    assert air.succeeded and air.kind == "air" and air.failure is None
    assert air.convergence_verdict == "CONVERGED" and air.mesh_cells == 65584 and air.check_mesh_ok is True
    assert air.mesh_receipt_status == "accepted"
    assert air.stage_seconds() == {"export": 1.5, "mesh": 9.0, "solve": 40.0, "metrics": 0.5}
    assert air.rack_inlet_summary["maxAreaMeanC"] == 24.1 and air.energy_balance["acuHeatRemovedW"] == 1000.
    assert air.design_identity() == {"cfdprojSha256": "a" * 64, "effectiveProjectSha256": "b" * 64}
    liquid = view(LIQUID)
    assert isinstance(liquid, LiquidView)
    assert liquid.objectives["max_chip_temperature_K"] == 330.0
    assert liquid.gradient("max_chip_temperature_K", ["heat_W"]) == {"heat_W": {"cp_1": 0.01}}
    assert liquid.finite_difference_checks[0]["component"] == "cp_1"
    assert liquid.edges == [{"id": "e"}] and liquid.max_chip_temperature_K == 330.0
    hybrid = view({**AIR, "kind": "hybrid", "metrics": {**AIR["metrics"], "heatLedger": {"totals": {}},
                                                       "liquid": {"edges": []}}})
    assert isinstance(hybrid, HybridView) and hybrid.heat_ledger == {"totals": {}}
    assert isinstance(view({"format": "dcdtuni.api.result/v1", "kind": "other"}), ResultView)


def test_failed_result_reports_its_stage():
    failed = view({**AIR, "status": "failed", "failure": {"stage": "mesh", "reason": "timeout"}})
    assert not failed.succeeded and failed.failure_stage == "mesh"


def test_read_result_accepts_the_file_or_its_run_directory_and_checks_the_format(tmp_path):
    run = tmp_path / "2026-10-22_120000_SGT__air"
    run.mkdir()
    (run / "result.json").write_text(json.dumps(AIR))
    for target in (run, run / "result.json"):
        loaded = read_result(target)
        assert isinstance(loaded, AirView) and loaded.path == run / "result.json"
        assert loaded.raw == AIR
    (tmp_path / "bad.json").write_text(json.dumps({**AIR, "format": "dcdtuni.api.build/v1"}))
    with pytest.raises(ValueError, match="dcdtuni.api.result/v1"):
        read_result(tmp_path / "bad.json")
    with pytest.raises(FileNotFoundError):
        read_result(tmp_path / "missing")
