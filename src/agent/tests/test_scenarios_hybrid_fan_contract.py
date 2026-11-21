"""The fan/air contract: one source, the platform's liquid heat, conservative per-server partition, exact ACU states.

Inputs are the frozen parts of the OPEN reference run (``fixtures/hybrid_lf/open-normal``):
the candidate's project, its air manifest, the ``.liquidrun`` and the steady answer
``agent.scripted_api`` gave, each trimmed to what the contract reads.
"""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from agent.scenarios.hybrid import task as T
from agent.scenarios.hybrid.fan_contract import FORMAT, build_fan_contract
from agent.scenarios.hybrid.geometry import geometry_ledger
from agent.scenarios.hybrid.oracle import registered_it_budget, scenario
from agent.scenarios.hybrid.return_contract import HeatPartition

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hybrid_lf" / "open-normal.json.gz"


@pytest.fixture(scope="module")
def frozen():
    return json.loads(gzip.decompress(FIXTURE.read_bytes()))


@pytest.fixture
def inputs(frozen):
    data = copy.deepcopy(frozen["inputs"])
    task = T.REGISTERED.load()
    data["scenario"] = scenario(frozen["action"], fault_id=frozen["fault_id"])
    data["budget"] = registered_it_budget(task)
    data["ledger"] = geometry_ledger(data["project"], data["manifest"], frozen["solids"])
    return data


def build(data, **changes):
    data = {**data, **changes}
    return build_fan_contract(data["project"], data["manifest"], data["ledger"], data["liquidrun"], data["steady"],
                              data["scenario"], data["budget"], liquid_run={"resultJson": "frozen: see provenance",
                                                                            "liquidrunSha256": "x"})


def test_the_contract_is_rebuilt_from_its_frozen_inputs(frozen, inputs):
    contract = build(inputs)
    expected = frozen["contract"]
    assert contract["format"] == FORMAT and contract["accepted_liquid_identity_verified"] is True
    # The frozen project is trimmed to what the contract reads, so only its geometry digest differs.
    # The fixtures were frozen before the contract listed each porous surface's baffle patches (2026-10-26).
    skip = {"geometry_sha256", "liquid_run", "porous_baffle_patches"}
    assert {k: v for k, v in contract.items() if k not in skip} == {k: v for k, v in expected.items() if k not in skip}
    pairs = contract["porous_baffle_patches"]
    assert set(pairs) == set(contract["porous_blanking_sigma"])
    assert all(names == [uid + "_master", uid + "_slave"] for uid, names in pairs.items())


def test_the_heat_partition_is_conservative_per_server_and_in_total(inputs):
    contract = build(inputs)
    samples = [s["samples"][0] for s in contract["servers"]]
    for row in samples:
        assert row["Q_IT_W"] == row["Q_air_W"] + row["actual_Q_liquid_W"] + row["solid_storage_W"]
    summary = HeatPartition(tuple(r["Q_IT_W"] / 1000 for r in samples), tuple(r["Q_air_W"] / 1000 for r in samples),
                            tuple(r["actual_Q_liquid_W"] / 1000 for r in samples)).validate()
    assert summary["total_IT_heat_kW"] == pytest.approx(84.)
    # The liquid heat is the platform's: its total is the CDU's rejection, its servers the cold plates' input.
    assert summary["liquid_heat_kW"] * 1000 == pytest.approx(inputs["steady"]["metrics"]["primaryRejectionW"], abs=1e-6)
    assert contract["liquid_steady_closure_max_W"] <= 1e-6
    assert all(s["cold_plate_input_W"] == pytest.approx(s["samples"][0]["actual_Q_liquid_W"], abs=1e-6)
               for s in contract["servers"])


def test_air_liquid_and_ledger_must_come_from_one_project(inputs):
    other = copy.deepcopy(inputs["liquidrun"])
    other["source_project"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="different projects"):
        build(inputs, liquidrun=other)
    ledger = {**inputs["ledger"], "project_content_hash": "0" * 64}
    with pytest.raises(ValueError, match="different projects"):
        build(inputs, ledger=ledger)


def test_a_liquid_answer_that_is_not_steady_or_does_not_close_is_refused(inputs):
    moving = copy.deepcopy(inputs["steady"])
    moving["diagnostics"]["storage_rate_W"] = 1.
    with pytest.raises(ValueError, match="not a steady state"):
        build(inputs, steady=moving)
    leaking = copy.deepcopy(inputs["steady"])
    leaking["metrics"]["racks"][0]["servers"][0]["heat_removed_W"][0] += 1e-3
    with pytest.raises(ValueError, match="cold plates' input"):
        build(inputs, steady=leaking)


def test_the_budget_and_the_acu_states_must_be_the_registered_ones(inputs):
    budget = dict(inputs["budget"])
    budget["m_1/e_1"] += 1.
    with pytest.raises(ValueError, match="registered budget"):
        build(inputs, budget=budget)
    fault = scenario(T.reference_action(T.REGISTERED.load()), fault_id="single-off/m_14")
    with pytest.raises(ValueError, match="registered operating state"):
        build(inputs, scenario=fault)
    missing = dict(inputs["budget"])
    missing.pop("m_1/e_1")
    with pytest.raises(ValueError, match="UID coverage"):
        build(inputs, budget=missing)
