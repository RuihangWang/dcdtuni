"""The zonal LF air networks (OPEN, CAC, HAC) on frozen fan contracts and exported solids.

Each fixture in ``fixtures/hybrid_lf/`` is one oracle run on the current
platform (``test_scenarios_hybrid_lf_oracle.freeze_fixture``). The networks must
solve to the registered full residual, close the air heat, keep the per-server
1D enthalpy, reproduce the frozen answer -- and the legacy oracle's air answer
on the same designs, whose evidence stays under ``case/agent/``.
"""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from agent.scenarios.hybrid.air_network import (ColdContainedAirNetwork, HotContainedAirNetwork, NETWORKS,
                                                OpenAirNetwork, network_for)
from agent.scenarios.hybrid.compile import DependencyPending

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hybrid_lf"
REPO = Path(__file__).resolve().parents[3]
CASES = ("open-normal", "open-single-off-m_14", "cac-normal", "hac-normal")
# The legacy runs of the same designs: seven-case OPEN reference (oracle v2 chain, same OPEN network) and the
# contained candidates of oracle v3.
SEVEN = REPO / "case/agent/shinaide-six-acu-open-lf-20261008/evidence/seven-accepted-LF-readback-v1"
V3 = REPO / "case/agent/shinaide-design-compile-20261011/evidence/contained14-combined-actual-native-LF-v1"
LEGACY = {"open-normal": (SEVEN / "normal/air_fields.json", SEVEN / "normal/fan_air_contract.json"),
          "open-single-off-m_14": (SEVEN / "fault-m_14/air_fields.json", SEVEN / "fault-m_14/fan_air_contract.json"),
          "cac-normal": (V3 / "point-00/actual-native-oracle/air/fields.json",
                         V3 / "point-00/actual-native-oracle/fan_air_contract.json"),
          "hac-normal": (V3 / "point-01/actual-native-oracle/air/fields.json",
                         V3 / "point-01/actual-native-oracle/fan_air_contract.json")}


def read_fixture(name):
    return json.loads(gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes()))


@pytest.fixture(scope="module", params=CASES)
def solved(request):
    fixture = read_fixture(request.param)
    model = network_for(fixture["contract"]["operating_scenario"]["architecture"])(fixture["contract"],
                                                                                   fixture["solids"])
    return request.param, fixture, model, model.steady_fields()


def test_each_family_solves_to_the_registered_residual_and_closes_the_air_heat(solved):
    name, fixture, model, fields = solved
    assert type(model).__name__ == fixture["network"]
    assert fields["original_full_residual_inf"] <= 1e-10
    assert fields["solve_receipt"]["stable_vs_original_max_error"] <= 1e-11
    air_W = np.array([s["samples"][0]["Q_air_W"] for s in fixture["contract"]["servers"]])
    assert float(fields["air_coil_removed_kw"].sum()) == pytest.approx(air_W.sum() / 1000, abs=1e-8)
    exhaust, inlet = fields["server_exhaust_c"].numpy(), fields["server_inlet_c"].numpy()
    assert np.abs(fields["server_1D_reference_exhaust_c"].numpy() - exhaust).max() <= 1e-7
    flow = np.array([s["fixed_flow_m3s"] for s in fixture["contract"]["servers"]])
    assert np.abs(model.rho * model.cp * flow * (exhaust - inlet) - air_W).max() <= 1e-6


def test_the_answer_is_the_frozen_platform_runs(solved):
    name, fixture, model, fields = solved
    expected = fixture["expected"]["air"]
    for uid, value in expected["ACU_return_c_by_uid"].items():
        got = fields["ACU_return_c_by_uid"][uid]
        assert (got is None) == (value is None) and (value is None or float(got) == pytest.approx(value, abs=1e-9))
    assert fields["server_inlet_c"].numpy() == pytest.approx(np.array(expected["server_inlet_c"]), abs=1e-9)
    assert fields["server_exhaust_c"].numpy() == pytest.approx(np.array(expected["server_exhaust_c"]), abs=1e-9)


def test_the_legacy_oracle_air_answer_is_reproduced_on_the_same_design(solved):
    """Same geometry, same server air heat: the port's network gives the legacy's fields (2026-10-08/11 evidence)."""
    name, fixture, model, fields = solved
    legacy_fields, legacy_contract = (json.loads(path.read_text()) for path in LEGACY[name])
    heat = {s["uid"]: s["samples"][0]["Q_air_W"] for s in legacy_contract["servers"]}
    for server in fixture["contract"]["servers"]:  # the platform liquid heat is the legacy's to its residual
        assert server["samples"][0]["Q_air_W"] == pytest.approx(heat[server["uid"]], abs=1e-6)
    for uid, value in legacy_fields["ACU_return_c_by_uid"].items():
        got = fields["ACU_return_c_by_uid"][uid]
        assert (got is None) == (value is None) and (value is None or float(got) == pytest.approx(value, abs=1e-8))
    assert fields["server_inlet_c"].numpy() == pytest.approx(np.array(legacy_fields["server_inlet_c"]), abs=1e-8)
    assert fields["server_exhaust_c"].numpy() == pytest.approx(np.array(legacy_fields["server_exhaust_c"]), abs=1e-8)


def test_a_closed_acu_keeps_its_body_but_has_no_port_in_the_equations():
    fixture = read_fixture("open-single-off-m_14")
    model = OpenAirNetwork(fixture["contract"], fixture["solids"])
    assert model.active_uids == tuple(u for u in model.acu_uids if u != "m_14") and len(model.acu_uids) == 6
    assert len(model.network.unit_pairs) == 5 and not any(n.startswith("m_14/") for n in model.network.node_names)
    assert {r["uid"]: r["active"] for r in model.identity["unit_geometry_and_mask"]}["m_14"] is False
    fields = model.steady_fields()
    assert fields["ACU_return_c_by_uid"]["m_14"] is None and float(fields["ACU_flow_by_uid"]["m_14"]) == 0.
    # A closed unit that still carries flow is refused.
    contract = copy.deepcopy(fixture["contract"])
    next(a for a in contract["acus"] if a["uid"] == "m_14")["effective_controls"]["compiled_supply_flow_m3s"] = .5
    with pytest.raises(ValueError, match="strictly closed"):
        OpenAirNetwork(contract, fixture["solids"])


def test_family_registry_and_refusals():
    assert {"SLAB_INROW_OPEN", "SLAB_INROW_CAC", "SLAB_INROW_HAC"} <= set(NETWORKS)  # perimeter: its own test
    with pytest.raises(DependencyPending, match="SLAB_INROW_MULTI"):
        network_for("SLAB_INROW_MULTI")
    cac = read_fixture("cac-normal")
    with pytest.raises(ValueError, match="OPEN network"):
        OpenAirNetwork(cac["contract"], cac["solids"])
    unpaid = copy.deepcopy(cac["contract"])
    unpaid["accepted_liquid_identity_verified"] = False
    with pytest.raises(ValueError, match="accepted same-source liquid"):
        ColdContainedAirNetwork(unpaid, cac["solids"])
    structural = ColdContainedAirNetwork(unpaid, cac["solids"], structural_fixture=True)
    with pytest.raises(RuntimeError, match="cannot solve"):
        structural.fields()
    # A sheet whose exported triangles lost its declared leak cutout is refused.
    leaky = copy.deepcopy(cac["contract"])
    leaky["containment_geometry"]["sheets"][0]["one_sided_STL_material_area_m2_expected"] += 1e-3
    with pytest.raises(ValueError, match="leak cutout"):
        ColdContainedAirNetwork(leaky, cac["solids"])
    hac = read_fixture("hac-normal")
    model = HotContainedAirNetwork(hac["contract"], hac["solids"])
    assert model.hot_bounds == model.cold_bounds  # exhausts and returns see only the contained layers
    assert all(v > 0 for v in model.identity["source_sheet_open_areas_m2"].values())
    opened = copy.deepcopy(read_fixture("open-normal")["contract"])
    opened["partitions"]["baffles"] = {"ispo_cac_roof": {}}
    with pytest.raises(ValueError, match="physical removal"):
        OpenAirNetwork(opened, cac["solids"])


def test_the_implicit_state_gradient_matches_a_finite_difference():
    """``fields(differentiable=True)`` (the implicit adjoint of the full-state numerics) against re-solved states."""
    fixture = read_fixture("cac-normal")
    model = ColdContainedAirNetwork(fixture["contract"], fixture["solids"])
    controls = model.nominal_controls.clone().requires_grad_(True)
    mean_return = model.fields(controls, differentiable=True)["unit_return_c"].mean()
    adjoint, = torch.autograd.grad(mean_return, controls)
    for index, step in ((0, 1e-3), (len(model.active_uids), 1e-4)):  # one supply temperature, one flow
        def at(shift):
            shifted = model.nominal_controls.clone()
            shifted[index] += shift
            return float(model.fields(shifted)["unit_return_c"].mean())
        difference = (at(step) - at(-step)) / (2 * step)
        assert float(adjoint[index]) == pytest.approx(difference, rel=1e-5, abs=1e-8)
