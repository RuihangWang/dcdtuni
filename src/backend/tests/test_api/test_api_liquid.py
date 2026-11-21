"""Liquid API: product steady state, objectives and implicit-adjoint gradients vs finite differences."""
import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from agent.scripted_api.liquid import OBJECTIVES, run_liquid_case, solve_liquid_steady
from backend.tests.test_liquid.test_dae_runtime import case


def test_steady_metrics_match_the_product_equilibrium_and_conserve_energy():
    result = solve_liquid_steady(case(), device="cpu", threads=1)
    assert result["primalConsistencyMaxAbsK"] < 1e-9
    diagnostics = result["diagnostics"]
    assert abs(diagnostics["energy_balance_W"]) < 1e-6 * (1 + abs(diagnostics["heat_input_W"]))
    assert result["metrics"]["heatInputW"] == pytest.approx(result["metrics"]["primaryRejectionW"], rel=1e-8)
    assert set(result["objectives"]) <= set(OBJECTIVES) and "max_chip_temperature_K" in result["objectives"]
    assert result["objectives"]["max_chip_temperature_K"] == pytest.approx(result["metrics"]["maxChipTemperatureK"])


def test_adjoint_gradients_match_centred_differences():
    result = solve_liquid_steady(case(), device="cpu", threads=1, fd_check=2,
                                 gradient_parameters=("heat_W", "primary_inlet_K", "primary_flow_kg_s"))
    checks = [row for row in result["finiteDifferenceCheck"] if "error" not in row]
    assert checks, result["finiteDifferenceCheck"]
    nonzero = [row for row in checks if not row["negligibleAdjoint"]]
    assert len(nonzero) >= 4, checks
    for row in nonzero:
        assert row["relativeError"] < 1e-6, row
    for row in checks:
        if row["negligibleAdjoint"]:  # e.g. pump power does not depend on heat at constant properties
            assert abs(row["finiteDifference"]) < 1e-8, row
    # Heat into a plate raises its own temperature; colder primary supply cools the loop.
    chip = result["gradients"]["max_chip_temperature_K"]
    assert max(chip["heat_W"].values()) > 0 and max(chip["primary_inlet_K"].values()) > 0


def test_unknown_names_and_non_dae_inputs_are_refused():
    with pytest.raises(ValueError, match="unknown objectives"):
        solve_liquid_steady(case(), objectives=["lowest_cost"], device="cpu")
    with pytest.raises(ValueError, match="unknown gradient parameters"):
        solve_liquid_steady(case(), gradient_parameters=["not_a_field"], device="cpu")
    with pytest.raises(ValueError, match="DAE"):
        solve_liquid_steady({"version": "1.0", "nodes": [], "edges": []})


def test_run_liquid_case_writes_a_new_dated_result_each_time(tmp_path):
    source = tmp_path / "input.liquidrun"
    source.write_text(json.dumps(case()))
    first = run_liquid_case(source, output_root=tmp_path / "runs", device="cpu", threads=1)
    second = run_liquid_case(source, output_root=tmp_path / "runs", device="cpu", threads=1)
    assert first["status"] == second["status"] == "succeeded"
    assert first["runDir"] != second["runDir"]
    saved = json.loads((tmp_path / "runs" / first["runDir"].split("/")[-1] / "result.json").read_text())
    assert saved["metrics"]["objectives"] == first["metrics"]["objectives"]
    assert saved["identity"]["repository"]["commit"]


# -- network output, input overrides, transient (2026-10-21) ---------------------------------------
import math

from backend.liquidcooling.run_manager import LiquidRunManager


def _steady_manifest():
    raw = case()
    raw["analysis"] = {"mode": "steady", "initialization": "steady"}
    raw["time"]["N_t"] = 1
    for edge in raw["edges"]:
        if edge["type"] == "pump":
            edge["params"]["rpm_ratio"] = 1.0
    raw["primary_circuits"][0].update(flow_kg_s=1.0, inlet_K=290.0)
    return raw


def _check_network(metrics, *, edge_ids, flows, edge_from, edge_to, kinds, diameters, rho, stagnant, node_ids,
                   pressure, temperature, positions, observable):
    edges, nodes = metrics["edges"], metrics["nodes"]
    assert [row["id"] for row in edges] == list(edge_ids)
    assert [row["id"] for row in nodes] == list(node_ids)
    for i, row in enumerate(edges):
        assert row["from"] == edge_from[i] and row["to"] == edge_to[i] and row["kind"] == kinds[i]
        assert row["massFlowKgS"] == flows[i]
        assert row["observable"] is (not stagnant[i])
        if stagnant[i] or flows[i] == 0:
            assert row["direction"] == "none"
        else:
            # the actual flow direction, in node ids
            assert row["direction"] == (f"{edge_from[i]}->{edge_to[i]}" if flows[i] > 0 else f"{edge_to[i]}->{edge_from[i]}")
            assert row["upstream"] == (edge_from[i] if flows[i] > 0 else edge_to[i])
        if diameters[i] is None:
            assert row["velocityMS"] is None
        else:
            assert row["velocityMS"] == pytest.approx(abs(flows[i]) / (rho * math.pi * diameters[i] ** 2 / 4), rel=1e-12)
    for i, row in enumerate(nodes):
        assert row["pressurePa"] == pressure[i] and row["temperatureK"] == temperature[i]
        assert row["positionM"] == positions[i] and row["observable"] is observable[i]


def test_steady_network_lists_every_edge_with_its_direction_and_every_node():
    from backend.liquidcooling.dae_runtime import DAESimulation
    raw = _steady_manifest()
    result = solve_liquid_steady(raw, device="cpu", threads=1)
    reference = DAESimulation(raw).result()  # the product result with A1's topology fields
    metrics = result["metrics"]
    _check_network(metrics, edge_ids=reference["edge_ids"], flows=[row[0] for row in reference["edge_flow_kg_s"]],
                   edge_from=reference["edge_from"], edge_to=reference["edge_to"],
                   kinds=reference["edge_types"], diameters=reference["edge_D_m"], rho=raw["fluid"]["rho_kg_m3"],
                   stagnant=reference["edge_stagnant"],  # one bool per edge
                   node_ids=reference["node_ids"], pressure=[row[0] for row in reference["node_pressure_Pa"]],
                   temperature=[row[0] for row in reference["node_temperature_K"]],
                   positions=reference["node_positions_m"], observable=[row[0] for row in reference["node_observable"]])
    pump = next(row for row in metrics["edges"] if row["kind"] == "pump")
    assert pump["direction"] == "return->supply" and pump["massFlowKgS"] > 0
    pipe = next(row for row in metrics["edges"] if row["kind"] == "pipe")
    assert pipe["velocityMS"] > 0 and pipe["outletTemperatureK"] > 0


def test_inputs_are_queued_through_set_inputs_and_previewed_by_the_equilibrium(tmp_path):
    raw = case()  # transient manifest: a command is queued for the next step, as the server's inputs endpoint does
    base = solve_liquid_steady(raw, device="cpu", threads=1)
    slowed = solve_liquid_steady(raw, device="cpu", threads=1, inputs={"pump_rpm_ratios": {"pump": 0.8},
                                                                     "primary_inlet_K": {"cdu_hx": 285.0}})
    assert slowed["inputs"] == {"pump_rpm_ratios": {"pump": 0.8}, "primary_inlet_K": {"cdu_hx": 285.0}}
    assert slowed["evaluatedAs"].startswith("steady equilibrium") and "pending" in slowed["evaluatedAs"]
    # the differentiable repeat used the same overridden parameters as the product query
    assert slowed["primalConsistencyMaxAbsK"] < 1e-9
    flow = {row["id"]: row["massFlowKgS"] for row in slowed["metrics"]["edges"]}
    base_flow = {row["id"]: row["massFlowKgS"] for row in base["metrics"]["edges"]}
    assert 0 < flow["pump"] < base_flow["pump"]
    # the same command through the server's run manager (inputs, then equilibrium) gives the same state
    manager = LiquidRunManager()
    run_id = manager.create(raw, case_dir=tmp_path)["run_id"]
    manager.set_inputs(run_id, pump_rpm_ratios={"pump": 0.8}, primary_inlet_K={"cdu_hx": 285.0})
    served = manager.equilibrium(run_id)
    assert [row["massFlowKgS"] for row in slowed["metrics"]["edges"]] == served["edge_flow_kg_s"]
    assert slowed["metrics"]["chipTemperatureK"] == dict(zip(served["cp_edge_ids"], served["chip_temp_K"]))
    assert served["controls"]["pump_rpm_ratios"] == {"pump": 0.8}


def test_inputs_on_a_steady_only_manifest_are_refused_with_the_reason():
    with pytest.raises(ValueError, match="transient"):
        solve_liquid_steady(_steady_manifest(), device="cpu", threads=1, inputs={"pump_rpm_ratios": {"pump": 0.8}})
    with pytest.raises(ValueError, match="pump_rpm_ratios|unknown input"):
        solve_liquid_steady(case(), device="cpu", threads=1, inputs={"pump_rpm": {"pump": 0.8}})


def test_transient_run_follows_the_product_batch_path_and_keeps_the_hybrid_inputs(tmp_path):
    source = tmp_path / "input.liquidrun"
    source.write_text(json.dumps(case()))
    result = run_liquid_case(source, output_root=tmp_path / "runs", mode="transient", device="cpu", threads=1,
                             inputs={"pump_rpm_ratios": {"pump": 0.8}})
    assert result["status"] == "succeeded", result["failure"]
    assert result["inputs"]["mode"] == "transient"
    run = Path(result["artifacts"]["liquidRunDir"])
    # runtime_batch.run_dae_manifest's products: what hybrid_air_case_cli --liquid-run reads, plus the checkpoint
    for name in ("input.liquidrun", "executed.liquidrun", "result.json", "executed-inputs.json", "checkpoint.json"):
        assert (run / name).is_file(), name
    saved = json.loads((run / "result.json").read_text())
    assert saved["completed"] is True and len(saved["time_s"]) == 4
    # the command was queued for the next accepted step (seq 1), exactly as the server's inputs endpoint queues it
    assert saved["applied_inputs"][0]["seq"] == 1 and saved["applied_inputs"][0]["pump_rpm_ratios"] == {"pump": 0.8}
    metrics = result["metrics"]
    assert metrics["mode"] == "transient" and metrics["final"]["seq"] == 3 and metrics["final"]["timeS"] == 3.0
    _check_network(metrics, edge_ids=saved["edge_ids"], flows=[row[-1] for row in saved["edge_flow_kg_s"]],
                   edge_from=saved["edge_from"],
                   edge_to=saved["edge_to"], kinds=saved["edge_types"], diameters=saved["edge_D_m"],
                   rho=saved["fluid"]["rho_kg_m3"], stagnant=[False] * len(saved["edge_ids"]),
                   node_ids=saved["node_ids"], pressure=[row[-1] for row in saved["node_pressure_Pa"]],
                   temperature=[row[-1] for row in saved["node_temperature_K"]],
                   positions=saved["node_positions_m"], observable=[row[-1] for row in saved["node_observable"]])
    assert metrics["maxChipTemperatureK"] == max(row[-1] for row in saved["chip_temp_K"])
    assert metrics["peakChipTemperatureK"] == max(max(row) for row in saved["chip_temp_K"])
    # the same scalar fields as a steady result, at the final sample, plus the worst over the run
    assert metrics["energyBalanceW"] == saved["diagnostics"][-1]["energy_balance_W"]
    assert metrics["maxAbsEnergyBalanceW"] == max(abs(row["energy_balance_W"]) for row in saved["diagnostics"]) < 1e-6
    written = json.loads((Path(result["runDir"]) / "result.json").read_text())
    assert written["metrics"]["edges"] == metrics["edges"]


def test_transient_mode_needs_a_time_marching_manifest(tmp_path):
    source = tmp_path / "steady.liquidrun"
    source.write_text(json.dumps(_steady_manifest()))
    result = run_liquid_case(source, output_root=tmp_path / "runs", mode="transient", device="cpu", threads=1)
    assert result["status"] == "failed" and result["failure"]["stage"] == "input"
    assert "transient" in result["failure"]["reason"]
    with pytest.raises(ValueError, match="mode"):
        run_liquid_case(source, output_root=tmp_path / "runs", mode="implicit")
