"""The liquid-to-air handoff must account for each cold plate's stored heat."""
from __future__ import annotations

import copy
import importlib.util

import pytest

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.liquidrun_dae_schema import LiquidrunDAEV3
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest
from backend.liquidcooling.runtime import _canonical_hash


def audit_api():
    name = "backend.liquidcooling.hybrid_heat_audit"
    assert importlib.util.find_spec(name), "the hybrid heat audit module is missing"
    return __import__(name, fromlist=["audit_hybrid_heat"])


def coupled_case() -> dict:
    raw = manifest()
    raw["racks"] = [{
        "id": "rack-uid",
        "servers": [{
            "id": "server-uid",
            "cp_cu_pairs": [{"id": "plate-uid", "edge_id": "cold_plate_1", "Q_W": 500.0}],
        }],
    }]
    raw["object_bindings"].update({
        "rack-uid": {"label": "Rack", "kind": "rack", "node_ids": [],
                     "edge_ids": ["cold_plate_1"]},
        "rack-uid/server-uid": {"label": "Server", "kind": "server",
                                "parent_uid": "rack-uid", "node_ids": [],
                                "edge_ids": ["cold_plate_1"]},
    })
    return raw


@pytest.fixture(scope="module")
def accepted_run() -> tuple[dict, dict]:
    import torch

    previous_threads = torch.get_num_threads()
    try:
        run = DAESimulation(coupled_case())
        run.set_heat_multiplier(1.2)
        run.step()
        run.set_heat_multiplier(0.8)
        run.step()
        yield run.executed_manifest(), run.result()
    finally:
        torch.set_num_threads(previous_threads)


def test_real_dae_accepted_prefix_closes_each_server_heat_balance(accepted_run):
    executed, result = accepted_run
    report = audit_api().audit_hybrid_heat(executed, result, "project-stable-uid")

    assert report["project_uid"] == "project-stable-uid"
    assert report["time_s"] == [0.0, 1.0, 2.0]
    assert report["heat_multipliers"] == [1.0, 1.2, 0.8]
    assert report["units"]["heat_rate"] == "W"
    assert report["checked_server_samples"] == 3
    server = report["servers"][0]
    assert server["rack_uid"] == "rack-uid"
    assert server["server_uid"] == "server-uid"
    assert server["cp_edge_ids"] == ["cold_plate_1"]
    assert [row["cp_input_W"] for row in server["samples"]] == [500.0, 600.0, 400.0]
    assert server["samples"][1]["solid_storage_rate_W"] != pytest.approx(0.0, abs=1e-6)
    assert max(abs(row["closure_residual_W"]) for row in server["samples"]) < 1e-6
    assert report["identity_verified"] is False
    assert report["handoff_ready"] is False


def provenance_for(executed, result, original):
    canonical = LiquidrunDAEV3.model_validate(original).model_dump(by_alias=True,mode="json",exclude_none=True)
    return {
        "format": "liquid-accepted-inputs/v1",
        "run_id": result["run_id"],
        "original_config_hash": _canonical_hash(canonical, []),
        "executed_config_hash": _canonical_hash(executed, []),
        "accepted_sample_count": executed["time"]["N_t"],
        "original_sample_count": original["time"]["N_t"],
        "accepted_time_s": result["time_s"][-1],
        "complete_original_horizon": False,
        "input_file": "executed.liquidrun",
        "scope": "accepted inputs only; pending commands and independent equilibrium are excluded",
    }


def test_original_input_and_runtime_receipt_prove_run_identity(accepted_run):
    executed, result = accepted_run
    original = coupled_case()
    receipt = provenance_for(executed, result, original)
    report = audit_api().audit_hybrid_heat(
        executed, result, "project-stable-uid",
        original_liquidrun=original, accepted_provenance=receipt,
    )
    assert report["identity_verified"] is True
    assert report["handoff_ready"] is False  # only an accepted prefix

    wrong = copy.deepcopy(result)
    wrong["run_id"] = "another-run"
    with pytest.raises(ValueError, match="run_id"):
        audit_api().audit_hybrid_heat(executed, wrong, "project-stable-uid",
            original_liquidrun=original, accepted_provenance=receipt)
    wrong = copy.deepcopy(result)
    wrong["config_hash"] = "different-original-input"
    with pytest.raises(ValueError, match="config_hash"):
        audit_api().audit_hybrid_heat(executed, wrong, "project-stable-uid",
            original_liquidrun=original, accepted_provenance=receipt)
    wrong_receipt = {**receipt,"executed_config_hash":"wrong-accepted-input"}
    with pytest.raises(ValueError, match="executed_config_hash"):
        audit_api().audit_hybrid_heat(executed, result, "project-stable-uid",
            original_liquidrun=original, accepted_provenance=wrong_receipt)


def test_rejects_result_heat_history_that_cannot_be_an_accepted_run(accepted_run):
    executed, result = accepted_run
    corrupted = copy.deepcopy(result)
    corrupted["heat_multipliers"][1] = -1.0
    with pytest.raises(ValueError, match="heat_multipliers.*nonnegative"):
        audit_api().audit_hybrid_heat(executed, corrupted, "project-stable-uid")


def test_consistent_initial_state_is_unchecked_and_subsequent_be_step_is_checked():
    raw = coupled_case()
    raw["analysis"]["initialization"] = "consistent"
    run = DAESimulation(raw)
    run.step()

    report = audit_api().audit_hybrid_heat(
        run.executed_manifest(), run.result(), "project-stable-uid")
    samples = report["servers"][0]["samples"]
    assert report["checked_server_samples"] == 1
    assert report["unchecked_server_samples"] == 1
    assert samples[0]["checked"] is False
    assert samples[0]["solid_storage_rate_W"] is None
    assert samples[0]["closure_residual_W"] is None
    assert samples[0]["reason"] == "consistent_initial_derivative_not_exported"
    assert samples[1]["checked"] is True
    assert abs(samples[1]["closure_residual_W"]) < 1e-6


def test_single_consistent_initial_sample_cannot_claim_zero_error_or_handoff():
    raw = coupled_case()
    raw["analysis"]["initialization"] = "consistent"
    raw["time"]["N_t"] = 1
    raw["events"] = []
    raw["edges"][-1]["params"]["rpm_ratio"] = 1.0
    raw["primary_circuits"][0].update(flow_kg_s=1.0, inlet_K=290.0)
    run = DAESimulation(raw)
    report = audit_api().audit_hybrid_heat(
        run.executed_manifest(), run.result(), "project-stable-uid")
    assert report["checked_server_samples"] == 0
    assert report["auditable"] is False
    assert report["max_abs_closure_residual_W"] is None
    assert report["handoff_ready"] is False


def test_rejects_second_server_binding_claiming_an_existing_cold_plate(accepted_run):
    executed, result = copy.deepcopy(accepted_run)
    duplicate = {"label":"Other server","kind":"server","parent_uid":"rack-uid",
                 "node_ids":[],"edge_ids":["cold_plate_1"]}
    executed["object_bindings"]["rack-uid/other-server"] = duplicate
    result["object_bindings"]["rack-uid/other-server"] = copy.deepcopy(duplicate)
    with pytest.raises(ValueError, match="duplicate|owner|binding"):
        audit_api().audit_hybrid_heat(executed, result, "project-stable-uid")


def test_direct_steady_solution_checks_the_first_and_only_sample():
    raw = coupled_case()
    raw["analysis"] = {"mode": "steady", "initialization": "steady"}
    raw["time"]["N_t"] = 1
    raw["events"] = []
    raw["edges"][-1]["params"]["rpm_ratio"] = 1.0
    raw["primary_circuits"][0].update(flow_kg_s=1.0, inlet_K=290.0)
    run = DAESimulation(raw)

    report = audit_api().audit_hybrid_heat(
        run.executed_manifest(), run.result(), "project-stable-uid")
    sample = report["servers"][0]["samples"][0]
    assert report["checked_server_samples"] == 1
    assert sample["checked"] is True
    assert sample["solid_storage_rate_W"] == 0.0
    assert sample["cp_input_W"] == pytest.approx(sample["liquid_removed_W"], abs=1e-6)


def test_rejects_finite_source_fields_whose_derived_storage_overflows(accepted_run):
    executed, result = copy.deepcopy(accepted_run)
    result["cp_details"][0]["chip_temp_K"][1] = 1e308
    result["chip_temp_K"][0][1] = 1e308
    with pytest.raises(ValueError, match="non-finite.*storage"):
        audit_api().audit_hybrid_heat(executed, result, "project-stable-uid")


def test_rejects_nonincreasing_times_even_when_grid_tolerance_would_hide_them(accepted_run):
    executed, result = copy.deepcopy(accepted_run)
    executed["time"]["dt_s"] = 1e-10
    result["time_s"] = [0.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="strictly increasing"):
        audit_api().audit_hybrid_heat(executed, result, "project-stable-uid")


@pytest.mark.parametrize(("change", "message"), [
    (lambda source, result: source["source_project"].update(project_uid="other"), "project_uid"),
    (lambda source, result: source.update(source="not-an-accepted-trace"), "accepted"),
    (lambda source, result: result["time_s"].pop(), "time_s"),
    (lambda source, result: result["time_s"].__setitem__(1, 1.5), "grid"),
    (lambda source, result: result["cp_details"][0].update(edge_id="wrong"), "cold-plate edge UIDs"),
    (lambda source, result: result["rack_summaries"][0]["servers"][0].update(server_id="wrong"), "UIDs"),
    (lambda source, result: result["object_bindings"].pop("rack-uid/server-uid"), "object_bindings"),
    (lambda source, result: result["rack_summaries"][0]["servers"][0]["heat_removed_W"].__setitem__(1, 605.0), "energy closure"),
    (lambda source, result: result["cp_details"][0]["chip_temp_K"].__setitem__(1, float("nan")), "finite"),
    (lambda source, result: result["diagnostics"][1].update(energy_balance_W=1000.0), "global energy balance"),
    (lambda source, result: result["chip_temp_K"][0].__setitem__(1, 100.0), "disagrees"),
])
def test_rejects_inconsistent_accepted_input_or_dae_result(accepted_run, change, message):
    executed, result = copy.deepcopy(accepted_run)
    change(executed, result)
    with pytest.raises(ValueError, match=message):
        audit_api().audit_hybrid_heat(executed, result, "project-stable-uid")
