"""The Shinaide hybrid LF oracle: binding and refusals (fast), and the whole chain through ``Fidelity.LF`` (opt-in).

The fixtures in ``fixtures/hybrid_lf/`` are frozen from oracle runs on the
current platform (``freeze_fixture`` below; regenerate with
``DCDTUNI_AGENT_REGEN_LF_FIXTURES=<scratch dir>``, see
:func:`test_regenerate_fixtures`). The end-to-end test compiles the reference
OPEN design and runs the oracle (``DCDTUNI_AGENT_SMOKE=1``; about a minute on CPU).
"""
from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path

import pytest

from agent.scenarios.hybrid import task as T
from agent.scenarios.hybrid.air_network import NETWORKS
from agent.scenarios.hybrid.geometry import load_solids, reduced_solids
from agent.scenarios.hybrid.oracle import (INVALID_I0, RESULT_FORMAT, assess, design_binding, lf_design, lf_oracle,
                                           plant_energy, registered_it_budget, scenario, solve_air, summary)
from agent.tools.fidelity import Fidelity, evaluate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hybrid_lf"
SMOKE = os.environ.get("DCDTUNI_AGENT_SMOKE") == "1"
STAMP = "2026-10-25T00:00:00.000Z"
REPO = Path(__file__).resolve().parents[3]
# The legacy oracle-v3 runs the contained fixtures repeat (their actions, and their outcomes for the comparisons).
LEGACY_V3 = REPO / "case/agent/shinaide-design-compile-20261011/evidence/contained14-combined-actual-native-LF-v1"
LEGACY_POINTS = {"cac-normal": LEGACY_V3 / "point-00", "hac-normal": LEGACY_V3 / "point-01",
                 "cac-single-off-m_1001": LEGACY_V3 / "point-02"}


def legacy_action(name):
    ref = json.loads((LEGACY_POINTS[name] / "candidate" / "paid_information_v1.json").read_text())["candidate_ref"]
    return ref["action"], ref["fault_id"]


@pytest.fixture(scope="module")
def task():
    return T.REGISTERED.load()


def read_fixture(name):
    return json.loads(gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes()))


# -- fast: scenario, budget, binding, refusals ------------------------------------------------------

def test_scenario_and_budget_come_from_the_task_not_the_candidate(task):
    action = T.reference_action(task)
    normal = scenario(action)
    assert normal["active_acu_uids"] == normal["all_acu_uids"] and len(normal["all_acu_uids"]) == 6
    fault = scenario(action, fault_id="single-off/m_14")
    assert fault["failed_acu_uid"] == "m_14" and "m_14" not in fault["active_acu_uids"]
    assert fault["acu_states"]["m_14"] == {**fault["acu_states"]["m_14"], "state": "failed_closed",
                                           "supply_flow_m3s": 0., "capacity_kw": 0.}
    assert {s["supply_flow_m3s"] for u, s in fault["acu_states"].items() if u != "m_14"} == {T.RATED_FLOW_M3S}
    budget = registered_it_budget(task)
    assert len(budget) == 56 and sum(budget.values()) == task["inventory"]["IT_W"]


def test_the_reference_project_is_the_reference_actions_build_and_a_moved_design_is_not(task):
    reference = json.loads(T.REGISTERED.project.read_text())
    action = T.reference_action(task)
    binding = design_binding(reference, action)
    assert binding["max_location_error_m"] == 0. and binding["moved_bodies"] == 22
    moved = copy.deepcopy(action)
    moved["layout"]["rows"][0]["center_m"] += .3
    with pytest.raises(ValueError, match="not this action's build"):
        design_binding(reference, moved)
    contained = copy.deepcopy(action)
    contained["architecture"], contained["containment"]["pairs"] = "SLAB_INROW_CAC", [["A", "B"]]
    with pytest.raises(ValueError, match="containment sheets"):
        design_binding(reference, contained)


def test_an_unbound_candidate_fails_at_binding_through_fidelity_lf(task, tmp_path):
    moved = T.reference_action(task)
    moved["layout"]["rows"][0]["center_m"] += .3
    record = evaluate(T.REGISTERED.project, Fidelity.LF, lf=lf_oracle, action=moved, out_dir=tmp_path / "lf")
    assert record.status == "failed" and record.validity == "failed:binding"
    assert record.evaluator["identity"]["version"] == "agent.hybrid.lf-oracle/v1"
    saved = json.loads((tmp_path / "lf" / "result.json").read_text())
    assert saved["format"] == RESULT_FORMAT and saved["I0"] == {"reward": INVALID_I0}
    assert sorted(p.name for p in (tmp_path / "lf").iterdir()) == ["result.json"]  # nothing paid for


def test_designs_without_a_compiler_are_pending_not_evaluated(task, tmp_path):
    permuted = T.reference_action(task)
    slots = permuted["layout"]["rows"][0]["slots"]
    slots[0], slots[1] = slots[1], slots[0]
    result = lf_oracle(T.REGISTERED.project, action=permuted, out_dir=tmp_path / "a")
    assert result["failure"]["stage"] == "dependency_pending"
    assert any("permutation" in d for d in result["failure"]["dependencies"])
    compiled = lf_design(permuted, out_dir=tmp_path / "b")
    assert compiled["status"] == "failed" and compiled["failure"]["stage"] == "dependency_pending"
    assert not (tmp_path / "b" / "candidate").exists()


# -- fast: the steps after the platform calls, on a frozen run --------------------------------------

def test_energy_and_assessment_of_the_frozen_reference_run():
    frozen = read_fixture("open-normal")
    inputs, contract, expected = frozen["inputs"], frozen["contract"], frozen["expected"]
    context = scenario(frozen["action"])
    _, fields = solve_air(contract, frozen["solids"])
    energy, pumps = plant_energy(context, contract, fields, inputs["liquidrun"], inputs["steady"])
    # The pump runs on the product law, read back from the solved pressures and heights.
    assert len(pumps) == 1 and pumps[0]["relative_error"] <= 1e-9 and pumps[0]["speed_ratio"] == 1.
    assert float(energy["air_heat_closure_kw"]) == pytest.approx(0., abs=1e-8)
    assessment = assess(context, contract, fields, energy, inputs["steady"], pumps)
    assert assessment["valid"] and assessment["feasible"] and all(assessment["numerical_gates"].values())
    assert float(energy["total_pue_design"]) == pytest.approx(expected["energy"]["total_pue_design"], rel=1e-12)
    assert assessment["I0"]["reward"] == pytest.approx(expected["I0"]["reward"], rel=1e-12)
    metrics = summary(context, contract, fields, energy, inputs["steady"], pumps)
    for key, value in expected["metrics"].items():
        assert metrics[key] == pytest.approx(value, rel=1e-12, abs=1e-12), key
    # A chip over the registered limit makes the design infeasible; the evaluation stays valid.
    hot = copy.deepcopy(inputs["steady"])
    first = next(iter(hot["metrics"]["chipTemperatureK"]))
    hot["metrics"]["chipTemperatureK"][first] = 273.15 + task_limit("chip_max_C") + 1.
    over = assess(context, contract, fields, energy, hot, pumps)
    assert over["valid"] and not over["feasible"] and max(over["violations"]["chip_hi_k"]) > 0


def task_limit(name):
    return T.REGISTERED.load()["thresholds"][name]


def test_a_closed_acu_is_explicit_in_the_plant_energy():
    """An air fault leaves the liquid side alone: the fault run's energy from the reference run's liquid answer."""
    normal, fault = read_fixture("open-normal"), read_fixture("open-single-off-m_14")
    liquidrun = normal["inputs"]["liquidrun"]
    context = scenario(fault["action"], fault_id=fault["fault_id"])
    _, fields = solve_air(fault["contract"], fault["solids"])
    energy, pumps = plant_energy(context, fault["contract"], fields, liquidrun, normal["inputs"]["steady"])
    assert energy["closed_ACU_UIDs"] == ("m_14",) and float(energy["fan_kw_by_UID"]["m_14"]) == 0.
    assert float(energy["air_coil_kw_by_UID"]["m_14"]) == 0. and len(energy["air_fan_kw"]) == 5
    assert float(energy["total_pue_design"]) == pytest.approx(fault["expected"]["energy"]["total_pue_design"],
                                                              rel=1e-12)
    assessment = assess(context, fault["contract"], fields, energy, normal["inputs"]["steady"], pumps)
    assert assessment["I0"]["reward"] == pytest.approx(fault["expected"]["I0"]["reward"], rel=1e-12)
    with pytest.raises(ValueError, match="closed unit"):
        shifted = {**fields, "ACU_return_c_by_uid": {**fields["ACU_return_c_by_uid"], "m_14": 25.}}
        plant_energy(context, fault["contract"], shifted, liquidrun, normal["inputs"]["steady"])


# -- end to end (opt-in) ---------------------------------------------------------------------------

@pytest.mark.skipif(not SMOKE, reason="compiles and solves on the platform; set DCDTUNI_AGENT_SMOKE=1")
def test_reference_open_design_through_fidelity_lf_reproduces_the_frozen_answer(task, tmp_path):
    from agent.scenarios.hybrid.compile import compile_design
    action = T.reference_action(task)
    candidate = tmp_path / "candidate" / "candidate.cfdproj"
    assert compile_design(action, out=candidate)["status"] == "compiled"
    record = evaluate(candidate, Fidelity.LF, lf=lf_oracle, information=("I0", "I1"), action=action,
                      out_dir=tmp_path / "lf", timestamp=STAMP, threads=1)
    assert record.status == "succeeded" and record.validity == "valid", record.result.get("failure")
    result = record.result
    frozen = read_fixture("open-normal")
    assert result["inputs"]["cfdrunSha256"] == frozen["provenance"]["cfdrunSha256"]
    assert result["inputs"]["liquidrunSha256"] == frozen["provenance"]["liquidrunSha256"]
    expected = frozen["expected"]["metrics"]
    for key in ("PUE", "air_coil_removed_kw", "server_inlet_max_c", "server_exhaust_max_c",
                "max_chip_temperature_K", "pump_hydraulic_power_W"):
        assert result["metrics"][key] == pytest.approx(expected[key], rel=1e-9, abs=1e-9), key
    assert result["valid"] and result["feasible"] and all(result["numerical_gates"].values())
    assert result["I0"]["reward"] == pytest.approx(frozen["expected"]["I0"]["reward"], rel=1e-9)


# -- fixture freezing ------------------------------------------------------------------------------

def contract_inputs(project, manifest, liquidrun, steady):
    """The parts of a run's project, manifest, ``.liquidrun`` and steady answer the fan contract reads (the
    rest -- pipes, interface walls, the other boundary bindings, the network away from the pump -- is dropped)."""
    keep = ("racks", "acus", "floors", "baffles")
    project = {"uid": project["uid"], "geometry": project["geometry"],
               "constructions": {k: project["constructions"][k] for k in keep}}
    acu_paths = tuple("constructions.acus." + uid for uid in project["constructions"]["acus"])
    manifest = {"sourceProject": manifest["sourceProject"], "inputs": {"acus": manifest["inputs"]["acus"]},
                "geometryExport": {"stlFiles": manifest["geometryExport"]["stlFiles"],
                                   "solids": {k: v for k, v in manifest["geometryExport"]["solids"].items()
                                              if ".servers." in (v.get("objectPath") or "")}},
                "boundaryBindings": {k: v for k, v in manifest["boundaryBindings"].items()
                                     if v.get("objectPath") in acu_paths},
                "porousBindings": manifest["porousBindings"],
                "meshGeometry": {"floors": manifest["meshGeometry"]["floors"],
                                 "baffles": manifest["meshGeometry"]["baffles"],
                                 "racks": {k: {"servers": [{f: s[f] for f in ("sourceRackUid", "sourceUid", "physics")}
                                                           for s in r["servers"]]}
                                           for k, r in manifest["meshGeometry"]["racks"].items()}}}
    pumps = [e for e in liquidrun["edges"] if e["type"] == "pump"]
    ends = {e[k] for e in pumps for k in ("from", "to")}
    liquidrun = {**{k: liquidrun[k] for k in ("source_project", "fluid", "gravity", "parameter_pack")},
                 "edges": [e for e in liquidrun["edges"] if e["type"] in ("pump", "cold_plate")]}
    network = steady["metrics"]
    steady = {"diagnostics": steady["diagnostics"], "controls": steady["controls"], "objectives": steady["objectives"],
              "metrics": {**{k: network[k] for k in ("racks", "primaryReturnK", "primaryRejectionW",
                                                     "chipTemperatureK", "maxChipTemperatureK")},
                          "edges": [e for e in network["edges"] if e["kind"] == "pump"],
                          "nodes": [n for n in network["nodes"] if n["id"] in ends]}}
    return {"project": project, "manifest": manifest, "liquidrun": liquidrun, "steady": steady}


def freeze_fixture(oracle_dir: Path, name: str, *, action, fault_id, with_inputs: bool = False) -> Path:
    """Freeze one oracle run into ``fixtures/hybrid_lf/<name>.json.gz``: its contract, the solids the network reads,
    the platform identities and the answer; with ``with_inputs`` also the contract's inputs (project, manifest,
    ``.liquidrun`` and the steady answer's network parts)."""
    oracle_dir = Path(oracle_dir)
    result = json.loads((oracle_dir / "result.json").read_text())
    if result["status"] != "succeeded":
        raise ValueError(f"{oracle_dir} did not succeed")
    contract = json.loads((oracle_dir / "fan_air_contract.json").read_text())
    contract["liquid_run"] = {**contract["liquid_run"], "resultJson": "frozen: see provenance"}
    solids = load_solids(oracle_dir / "air", contract["source_STL_hashes"])
    model = NETWORKS[contract["operating_scenario"]["architecture"]]
    names = {s[k]["original_solid"] for s in contract["servers"] for k in ("intake", "exhaust")}
    names |= {b["solidName"] for a in contract["acus"] for b in a["source_bound_BC_bindings"]}
    names |= {s["uid"] for s in (contract["containment_geometry"].get("sheets") or [])}
    names |= {n for n in solids if "_inlet_u" in n and n.endswith(("_u1_u3", "_u8_u12", "_u21_u24", "_u29_u32",
                                                                    "_u41_u44"))}
    fields = json.loads((oracle_dir / "air_fields.json").read_text())
    energy = json.loads((oracle_dir / "energy.json").read_text())
    liquid = json.loads(Path(result["artifacts"]["liquidResultJson"]).read_text())
    payload = {"format": "agent.tests.hybrid-lf-fixture/v1", "case": name, "action": action, "fault_id": fault_id,
               "network": model.__name__,
               "provenance": {"oracle": result["evaluator"]["version"], "inputs": result["inputs"],
                              "cfdrunSha256": result["inputs"]["cfdrunSha256"],
                              "liquidrunSha256": result["inputs"]["liquidrunSha256"],
                              "stlSha256": contract["source_STL_hashes"],
                              "repository": liquid["identity"]["repository"], "liquidSolver": liquid["identity"]["solver"],
                              "exportedAt": STAMP},
               "contract": contract, "solids": reduced_solids(solids, [n for n in solids if n in names]),
               "expected": {"metrics": result["metrics"], "I0": result["I0"], "valid": result["valid"],
                            "feasible": result["feasible"],
                            "air": {k: fields[k] for k in ("ACU_return_c_by_uid", "server_inlet_c", "server_exhaust_c",
                                                           "air_coil_removed_kw", "original_full_residual_inf")},
                            "energy": {k: energy[k] for k in ("total_pue_design", "total_kw", "primary_kw",
                                                              "air_fan_total_kw", "secondary_liquid_pump_total_kw")}}}
    if with_inputs:
        payload["inputs"] = contract_inputs(json.loads(Path(result["inputs"]["cfdproj"]).read_text()),
                                            json.loads((oracle_dir / "air" / "air.cfdrun").read_text()),
                                            json.loads(Path(liquid["artifacts"]["liquidrun"]).read_text()),
                                            liquid["metrics"])
    path = FIXTURES / f"{name}.json.gz"
    # Key order is kept: the contract lists bindings in the manifest's order, the network solids in the STL's.
    path.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))
    return path


@pytest.mark.skipif(not os.environ.get("DCDTUNI_AGENT_REGEN_LF_FIXTURES"),
                    reason="set DCDTUNI_AGENT_REGEN_LF_FIXTURES=<scratch dir> to re-run the oracles and refreeze")
def test_regenerate_fixtures(task):
    scratch = Path(os.environ["DCDTUNI_AGENT_REGEN_LF_FIXTURES"])
    reference = T.reference_action(task)
    for name, action, fault, inputs in (("open-normal", reference, "normal", True),
                                        ("open-single-off-m_14", reference, "single-off/m_14", False),
                                        ("cac-normal", *legacy_action("cac-normal"), False),
                                        ("hac-normal", *legacy_action("hac-normal"), False)):
        run = lf_design(action, out_dir=scratch / name, fault_id=fault, timestamp=STAMP, threads=1)
        assert run["status"] == "succeeded", run.get("failure")
        freeze_fixture(scratch / name / "oracle", name, action=action, fault_id=fault, with_inputs=inputs)
