"""Prepared focused tests: accepted DAE provenance lowers into fan enthalpy."""
from copy import deepcopy
import json
import pytest
from backend.tests.test_hybrid_air_heat_handoff import _project, _air, _stls, _compile, _completed_run
from backend.tests.test_cfdrun_reader import _write_ascii_stl_rect
from backend.aircooling.runner.hybrid_air_heat_handoff import retarget_hybrid_air_heat_to_mesh
from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.stlgen_runner import requires_hybrid_air_gate


def project():
    p = _project()
    p["constructions"]["racks"]["rack-uid"]["constructions"]["servers"]["server-uid"]["cooling"] = {
        "power": 900., "volumeFlowRate": .25}
    p["runDefaults"]["hybridHeatBudget"] = {
        "version": 2, "source": "mock", "source_reference": "focused synthetic accepted DAE fixture",
        "load_mapping": "server_power_times_shared_heat_multiplier", "air_transfer": "fan_enthalpy",
        "servers": {"rack-uid/server-uid": {"total_it_power_W": 900.}}}
    return p


def air(p):
    a = _air(p)
    for key, bc in [("front", "fan_inlet"), ("rear", "fan_outlet")]:
        b = a["boundaryBindings"]["bb_"+key]
        b["role"] = b["bcType"] = bc
        b["parameters"] = {"flowRateM3s": .25, "powerW": 900., "timeVarying": {}}
        a["geometryExport"]["solids"]["sol_"+key]["role"] = bc
    return a


def test_owned_accepted_sample_becomes_fan_power_and_preserves_flow(tmp_path):
    p, a = project(), air(project())
    run = _completed_run(p)
    before = deepcopy((p, a))
    compiled, ledger = _compile(p, a, _stls(tmp_path), run)
    assert (p, a) == before
    CfdrunV2.model_validate(compiled)
    assert ledger["format"] == "hybrid-air-heat-handoff/v2"
    row = ledger["servers"][0]
    assert row["server_uid"] == "rack-uid/server-uid"
    assert row["total_it_W"] == pytest.approx(1080.)
    assert row["air_residual_W"] == pytest.approx(480., abs=1e-5)
    assert row["liquid_removed_W"] + row["solid_storage_rate_W"] + row["air_residual_W"] == pytest.approx(row["total_it_W"])
    for bc in compiled["boundaryBindings"].values():
        assert bc["parameters"]["flowRateM3s"] == .25
        assert bc["parameters"]["powerW"] == pytest.approx(480., abs=1e-5)
        assert bc["objectPath"].endswith("rack-uid.servers.server-uid")
    assert sum(f["heat_W"] for f in row["faces"]) == pytest.approx(480., abs=1e-5)
    areas = {f["patch_name"]:f["area_m2"]*1.5 for f in row["faces"]}
    adjusted, verified = retarget_hybrid_air_heat_to_mesh(compiled, ledger, areas, mesh_sha256="a"*64)
    assert adjusted == compiled  # fan Q and W do not rescale with mesh area
    assert verified["mesh_area_verified"] is True
    broken = deepcopy(compiled)
    broken["boundaryBindings"]["bb_rear"]["parameters"]["powerW"] += 1
    with pytest.raises(ValueError, match="power/flow changed"):
        retarget_hybrid_air_heat_to_mesh(broken, ledger, areas, mesh_sha256="a"*64)


def test_subdivided_outlets_keep_uid_flow_and_heat_sum(tmp_path):
    p = project()
    a = air(p)
    other = deepcopy(a["boundaryBindings"]["bb_rear"])
    other["patchName"] = other["solidName"] = "server-uid_outlet_p1"
    a["boundaryBindings"]["bb_rear1"] = other
    solid = deepcopy(a["geometryExport"]["solids"]["sol_rear"])
    solid["patchName"] = solid["solidName"] = other["solidName"]
    a["geometryExport"]["solids"]["sol_rear1"] = solid
    for key in ("bb_rear", "bb_rear1"):
        a["boundaryBindings"][key]["parameters"].update(flowRateM3s=.125, powerW=450.)
    tri = _stls(tmp_path)
    _write_ascii_stl_rect(tri/"rear1.stl", other["solidName"], .3, .2)
    compiled, ledger = _compile(p, a, tri, _completed_run(p))
    outlet = [b for b in compiled["boundaryBindings"].values() if b["bcType"]=="fan_outlet"]
    assert sum(b["parameters"]["flowRateM3s"] for b in outlet) == .25
    assert sum(b["parameters"]["powerW"] for b in outlet) == pytest.approx(ledger["servers"][0]["air_residual_W"])


def test_source_power_keyframes_are_distinct_from_cp_load_and_freeze_sample(tmp_path):
    p, a = project(), air(project())
    a["schemaVersion"] = 3
    a["transient"] = {"durationS":3., "start":{"kind":"uniform","uniform":{"airT_C":22.,"waterT_C":30.,"solidT_C":40.}}}
    for b in a["boundaryBindings"].values():
        b["parameters"]["schedules"]={"powerW":[[0.,900.],[1.,900.],[1.,1200.],[3.,1200.]],
                                       "flowRateM3s":[[0.,.25],[3.,.4]]}
    a["boundaryBindings"]["bb_front"]["parameters"]["temperatureC"] = 18.
    a["boundaryBindings"]["bb_front"]["parameters"]["schedules"]["temperatureC"] = [
        [0.,18.],[1.,18.],[1.,24.],[3.,24.]]
    compiled, ledger = _compile(p,a,_stls(tmp_path),_completed_run(p))
    row = ledger["servers"][0]
    assert row["source_total_it_W"] == 1200.
    assert row["total_it_W"] == 1440.  # accepted global multiplier, not CP loadStep inference
    assert row["flow_rate_m3_s"] == pytest.approx(.3)
    assert all(not b["parameters"].get("schedules") for b in compiled["boundaryBindings"].values())
    assert compiled["boundaryBindings"]["bb_front"]["parameters"]["temperatureC"] == 24.
    assert "temperatureC" not in compiled["boundaryBindings"]["bb_rear"]["parameters"]


@pytest.mark.parametrize("temperature", [None, 21.5])
def test_optional_inlet_temperature_keeps_absent_null_or_scalar(tmp_path, temperature):
    p, a = project(), air(project())
    if temperature is not None:
        a["boundaryBindings"]["bb_front"]["parameters"]["temperatureC"] = temperature
    compiled, _ = _compile(p,a,_stls(tmp_path),_completed_run(p))
    inlet = compiled["boundaryBindings"]["bb_front"]["parameters"]
    if temperature is None:
        assert inlet.get("temperatureC") is None
    else:
        assert inlet["temperatureC"] == temperature
    assert "temperatureC" not in compiled["boundaryBindings"]["bb_rear"]["parameters"]
    if temperature is None:
        a["boundaryBindings"]["bb_front"]["parameters"]["temperatureC"] = None
        nullable, _ = _compile(p,a,_stls(tmp_path),_completed_run(p))
        assert nullable["boundaryBindings"]["bb_front"]["parameters"]["temperatureC"] is None


@pytest.mark.parametrize("failure",["missing-outlet","mismatch-flow","wrong-budget","legacy-curve","negative-residual"])
def test_actual_contract_failures_are_not_silent_air_fallback(tmp_path,failure):
    p, a = project(), air(project())
    if failure=="missing-outlet": del a["boundaryBindings"]["bb_rear"]
    elif failure=="mismatch-flow": a["boundaryBindings"]["bb_rear"]["parameters"]["flowRateM3s"]=.3
    elif failure=="wrong-budget":
        for b in a["boundaryBindings"].values(): b["parameters"]["powerW"]=901.
    elif failure=="legacy-curve": a["boundaryBindings"]["bb_rear"]["parameters"]["timeVarying"]={"power":"t"}
    else:
        p["runDefaults"]["hybridHeatBudget"]["servers"]["rack-uid/server-uid"]["total_it_power_W"]=100.
        for b in a["boundaryBindings"].values(): b["parameters"]["powerW"]=100.
    with pytest.raises(ValueError):
        _compile(p,a,_stls(tmp_path),_completed_run(p))


def test_fan_transport_identity_requires_gate_even_without_inherit_flag():
    a={"runId":"raw","inputs":{"liquid":{"enabled":False}},
       "meshGeometry":{"racks":{"R":{"servers":[{"type":"fanServer","sourceType":"liquidServer"}]}}}}
    assert requires_hybrid_air_gate(a)
