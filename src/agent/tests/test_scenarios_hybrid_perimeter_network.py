"""The raised-floor perimeter LF air networks (RF_PERIM_OPEN / _CAC / _HAC) on the legacy perimeter-21 runs.

The 2026-10-11 legacy runs (``case/agent/shinaide-design-compile-20261011/evidence/
perimeter21-actual-native-LF-v1``: the normal context and every single-unit outage
of the three families) froze each point's fan contract, the network's identity and
its solved fields; the exported STL batch they read stays under the design
compiler's ``agent_outputs``. The port runs on that contract (re-labelled to the
current contract format, with the porous bindings read from the same manifest)
and those solids, and must give the legacy's answer.

Registered before the comparison (2026-10-26): temperatures within 1e-8 °C, the
same node and passage counts, full residual <= 1e-10.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from agent.scenarios.hybrid.air_network import NETWORKS, network_for
from agent.scenarios.hybrid.air_network_perimeter import (PerimeterColdContainedAirNetwork,
                                                          PerimeterHotContainedAirNetwork, PerimeterOpenAirNetwork)
from agent.scenarios.hybrid.derivatives import single_return_alias
from agent.scenarios.hybrid.fan_contract import FORMAT
from agent.scenarios.hybrid.geometry import load_solids

REPO = Path(__file__).resolve().parents[3]
EVIDENCE = REPO / "case/agent/shinaide-design-compile-20261011/evidence/perimeter21-actual-native-LF-v1"
POINTS = sorted(p.name for p in EVIDENCE.iterdir() if (p / "native" / "fan_air_contract.json").is_file())
TEMPERATURE_TOLERANCE_C = 1e-8


def legacy_contract(contract, manifest):
    """A legacy ``ispo-liquid-server-fan-air-contract/v4-closed-ACU`` contract in the current format.

    The two formats carry the same records; the current one also lists each
    perforated surface's sigma and baffle patches, which the legacy network read
    from the manifest directly.
    """
    result = copy.deepcopy(contract)
    result["format"] = FORMAT
    sigma, patches = {}, {}
    for binding in manifest["porousBindings"].values():
        if binding["type"] == "perforatedBaffle":
            sigma.setdefault(binding["surfaceUid"], binding["parameters"]["sigma"])
            patches.setdefault(binding["surfaceUid"], []).append(binding["patchName"])
    result["porous_blanking_sigma"] = sigma
    result["porous_baffle_patches"] = {k: sorted(v) for k, v in patches.items()}
    return result


def legacy_point(name):
    native = EVIDENCE / name / "native"
    contract = json.loads((native / "fan_air_contract.json").read_text())
    geometry = REPO / json.loads((native / "air" / "task_config.json").read_text())["geometry"]
    manifest = json.loads((geometry / "air.cfdrun").read_text())
    solids = load_solids(geometry, contract["source_STL_hashes"])
    return (legacy_contract(contract, manifest), solids, json.loads((native / "air" / "fields.json").read_text()),
            json.loads((native / "air" / "model_identity.json").read_text()))


def test_all_twenty_one_legacy_points_are_present():
    assert len(POINTS) == 21
    assert {p.split("--")[0] for p in POINTS} == {"RF_PERIM_OPEN", "RF_PERIM_CAC", "RF_PERIM_HAC"}


@pytest.mark.parametrize("name", POINTS)
def test_the_legacy_perimeter_answer_is_reproduced(name):
    contract, solids, legacy, identity = legacy_point(name)
    model = network_for(contract["operating_scenario"]["architecture"])(contract, solids)
    assert (model.identity["nodes"], model.identity["passages"]) == (identity["nodes"], identity["passages"])
    assert model.identity["state_size"] == identity["state_size"]
    fields = model.steady_fields()
    assert fields["original_full_residual_inf"] <= 1e-10
    air_W = np.array([s["samples"][0]["Q_air_W"] for s in contract["servers"]])
    assert float(fields["air_coil_removed_kw"].sum()) == pytest.approx(air_W.sum() / 1000, abs=1e-8)
    exhaust, inlet = fields["server_exhaust_c"].numpy(), fields["server_inlet_c"].numpy()
    assert np.abs(fields["server_1D_reference_exhaust_c"].numpy() - exhaust).max() <= 1e-7
    assert np.abs(inlet - np.array(legacy["server_inlet_c"])).max() <= TEMPERATURE_TOLERANCE_C
    assert np.abs(exhaust - np.array(legacy["server_exhaust_c"])).max() <= TEMPERATURE_TOLERANCE_C
    for uid, value in legacy["ACU_return_c_by_uid"].items():
        got = fields["ACU_return_c_by_uid"][uid]
        assert (got is None) == (value is None)
        assert value is None or abs(float(got) - value) <= TEMPERATURE_TOLERANCE_C


def test_the_families_are_registered_with_their_plena():
    assert set(NETWORKS) == {"SLAB_INROW_OPEN", "SLAB_INROW_CAC", "SLAB_INROW_HAC",
                             "RF_PERIM_OPEN", "RF_PERIM_CAC", "RF_PERIM_HAC"}
    assert network_for("RF_PERIM_HAC") is PerimeterHotContainedAirNetwork
    contract, solids, _, _ = legacy_point("RF_PERIM_HAC--normal")
    model = PerimeterHotContainedAirNetwork(contract, solids)
    names = model.network.node_names
    assert names[5] == "supply-plenum" and names[6] == "ceiling-return-plenum"
    # Every active unit blows into the supply plenum and returns from the ceiling plenum only.
    for ret, supply in model.network.unit_pairs:
        into = [b for (a, b), oneway in zip(model.network.passive_pairs, model.network.directed) if a == supply]
        out_of = [a for (a, b) in model.network.passive_pairs if b == ret]
        assert into == [5] and out_of == [6]
    assert len(model.identity["tile_boundary_witness"]) == 16
    # The ducted returns are one function of the controls: the PUE's common-return minimum has a derivative.
    alias = single_return_alias(model)
    assert alias["verified"] is True and alias["common_upstream_node"] == "ceiling-return-plenum"
    contract, solids, _, _ = legacy_point("RF_PERIM_OPEN--normal")
    open_model = PerimeterOpenAirNetwork(contract, solids)
    assert "ceiling-return-plenum" not in open_model.network.node_names
    # The perimeter units' back returns sit wholly above the equipment mid-height: one inflow each, the same layer.
    assert single_return_alias(open_model)["common_upstream_node"] == "hot-upper"


def test_refusals():
    contract, solids, _, _ = legacy_point("RF_PERIM_CAC--normal")
    with pytest.raises(ValueError, match="RF_PERIM_OPEN network, not RF_PERIM_CAC"):
        PerimeterOpenAirNetwork(contract, solids)
    wrong = copy.deepcopy(contract)
    wrong["containment_geometry"]["kind"] = "hot"
    with pytest.raises(ValueError, match="cold containment"):
        PerimeterColdContainedAirNetwork(wrong, solids)
    tile_solid = contract["partitions"]["compiled_floor_definitions"]["Raised_Floor"]["openings"][0]["solidName"]
    for change in ("sigma", "pair"):
        bad = copy.deepcopy(contract)
        if change == "sigma":
            bad["porous_blanking_sigma"][tile_solid] = .3
        else:
            bad["porous_baffle_patches"][tile_solid] = bad["porous_baffle_patches"][tile_solid][:1]
        with pytest.raises(ValueError, match="registered one"):
            PerimeterColdContainedAirNetwork(bad, solids)
    moved = copy.deepcopy(contract)
    moved["containment_geometry"]["tiles"][0]["center_xyz_m"][0] += .01
    with pytest.raises(ValueError, match="tile world centre"):
        PerimeterColdContainedAirNetwork(moved, solids)
    fault, fault_solids, _, _ = legacy_point("RF_PERIM_CAC--ispo_perim_02-off")
    model = PerimeterColdContainedAirNetwork(fault, fault_solids)
    assert "ispo_perim_02" not in model.active_uids and len(model.network.unit_pairs) == 5
    opened = copy.deepcopy(fault)
    next(a for a in opened["acus"] if a["uid"] == "ispo_perim_02")["effective_controls"]["compiled_supply_flow_m3s"] = .5
    with pytest.raises(ValueError, match="strictly closed"):
        PerimeterColdContainedAirNetwork(opened, fault_solids)
