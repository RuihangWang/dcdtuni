"""Result metadata names the requested analysis, independent of initializer convergence."""
import copy
import json
import pytest
from backend.liquidcooling.run_manager import LiquidRunManager
from backend.tests.test_liquid.test_dae_runtime import case

@pytest.mark.parametrize("mode", ["steady", "transient"])
def test_result_preserves_analysis_and_requested_horizon_without_altering_fields(tmp_path, mode):
    raw = case()
    raw["analysis"] = {"mode": mode, "initialization": "steady"}
    if mode == "steady":
        raw["time"]["N_t"] = 1
        raw["edges"][-1]["params"]["rpm_ratio"] = 1.
    manager = LiquidRunManager()
    created = manager.create(raw, case_dir=tmp_path)
    run_id = created["run_id"]
    before = copy.deepcopy(created["frame"])
    if mode == "transient":
        manager.operate(run_id, "cancel")
    result = manager.result(run_id)
    assert result["analysis"] == raw["analysis"]
    assert result["expected_duration_s"] == (raw["time"]["N_t"]-1)*raw["time"]["dt_s"]
    assert result["time_s"] == [0.0]
    assert result["converged"] is True
    assert result["status"] == ("completed" if mode == "steady" else "cancelled")
    assert result["completed"] is (mode == "steady")
    assert manager.get(run_id)["frame"] == before
    saved = json.loads((tmp_path/"Liquid"/"log"/f"run-{run_id}"/"result.json").read_text())
    assert saved == result
    result["analysis"]["mode"] = "other"
    assert manager.result(run_id)["analysis"] == raw["analysis"]
