"""Semantic edits compile to automation-contract build steps (pure Python, no Node, no server).

The rules the frozen ``projcomp``/``pipeline`` learnt the hard way are pinned
here: "switch an object off" deletes it (``removeModels``), never
``setEnabled``; ACU supply edits address the supply patch through
``updateAcuPatchPhysics``; a step that needs an earlier step's return value
uses the ``"$N"`` reference of the whole build, and a literal string that
starts with ``$`` is escaped.
"""
from __future__ import annotations

import pytest

from agent.tools import edits as E
from agent.tools.edits import ContractVerbMissing, ProjectView, compile_edits

RACK_PROJECT = {
    "schemaVersion": 4,
    "constructions": {
        "racks": {"r1": {"constructions": {"servers": {
            "e_1": {"cooling": {"power": 1000.}}, "e_2": {"cooling": {"power": 3000.}}}}},
                  "r2": {"constructions": {"servers": {"e_3": {"cooling": {}}, "e_4": {}}}}},
        "acus": {"a1": {"geometry": {"faces": {
            "front": {"patches": [{"id": "p_3", "physics": {"role": "supply", "temperature": 15}}]},
            "top": {"patches": [{"id": "p_2", "physics": {"role": "return"}}]}}}},
                 "a2": {"geometry": {"faces": {
                     "front": {"patches": [{"id": "s1", "physics": {"role": "supply"}}]},
                     "back": {"patches": [{"id": "s2", "physics": {"role": "supply"}}]}}}},
                 "a3": {"geometry": {"faces": {"front": {"patches": []}}}}},
        "pipeSegments": {"pipe1": {"cooling": {"valves": [
            {"id": "tee_1", "type": "tee_junction"}, {"id": "v_1", "type": "flow_control"}]}}},
    },
}


def ctx(project=RACK_PROJECT, verbs=None):
    return ProjectView(document=project, materialized=project, build_verbs=verbs)


def steps(*edits, view=None):
    return compile_edits(edits, view or ctx())


def test_move_rotate_remove_compile_to_their_verbs():
    assert steps(E.MoveModel("r1", 1., 0., 2.5)) == [
        {"verb": "setModelLocation", "args": ["r1", {"x": 1., "y": 0., "z": 2.5}]}]
    assert steps(E.RotateModel("r1", yaw_deg=90.)) == [
        {"verb": "setRotationDegrees", "args": ["r1", {"x": 0., "y": 90., "z": 0.}]}]
    removed = steps(E.RemoveModels(["b1", "b2"]))
    assert removed == [{"verb": "removeModels", "args": [["b1", "b2"]]}]
    assert all(step["verb"] != "setEnabled" for step in removed)
    with pytest.raises(ValueError):
        E.RemoveModels([])
    with pytest.raises(ValueError):
        E.MoveModel("r1", float("nan"), 0., 0.)


def test_server_load_and_rack_power():
    assert steps(E.SetServerLoad("r1", "e_1", power_w=2500.)) == [
        {"verb": "updateRackServerCfd", "args": ["r1", "e_1", "power", 2500.]}]
    assert steps(E.SetServerLoad("r1", "e_1", power_w=10., flow_m3s=.05)) == [
        {"verb": "updateRackServerCfd", "args": ["r1", "e_1", "power", 10.]},
        {"verb": "updateRackServerCfd", "args": ["r1", "e_1", "volumeFlowRate", .05]}]
    with pytest.raises(ValueError):
        E.SetServerLoad("r1", "e_1")
    proportional = steps(E.SetRackPower("r1", 8000.))
    assert proportional == [{"verb": "updateRackServerCfd", "args": ["r1", "e_1", "power", 2000.]},
                            {"verb": "updateRackServerCfd", "args": ["r1", "e_2", "power", 6000.]}]
    equal = steps(E.SetRackPower("r2", 3000., split="equal"))
    assert [s["args"][3] for s in equal] == [1500., 1500.]
    with pytest.raises(ValueError, match="equal"):
        steps(E.SetRackPower("r2", 3000.))
    with pytest.raises(ValueError, match="no rack"):
        steps(E.SetRackPower("missing", 1.))


def test_acu_supply_addresses_supply_patches():
    assert steps(E.SetAcuSupply("a1", temperature_c=18., flow_m3s=3.)) == [
        {"verb": "updateAcuPatchPhysics", "args": ["a1", "p_3", "temperature", 18.]},
        {"verb": "updateAcuPatchPhysics", "args": ["a1", "p_3", "volumeFlowRate", 3.]}]
    both = steps(E.SetAcuSupply("a2", temperature_c=17.))
    assert [s["args"][1] for s in both] == ["s1", "s2"]
    with pytest.raises(ValueError, match="patch_id"):
        steps(E.SetAcuSupply("a2", flow_m3s=2.))
    assert steps(E.SetAcuSupply("a2", flow_m3s=2., patch_id="s2")) == [
        {"verb": "updateAcuPatchPhysics", "args": ["a2", "s2", "volumeFlowRate", 2.]}]
    with pytest.raises(ValueError, match="supply patch"):
        steps(E.SetAcuSupply("a3", temperature_c=18.))


def test_probe_uses_the_build_wide_reference_and_escapes_dollar_literals():
    compiled = steps(E.MoveModel("r1", 0., 0., 0.), E.AddProbe("$inlet", 1., 1.2, 2.))
    assert compiled[1] == {"verb": "addSensorProbe", "args": [1., 1.2, 2.]}
    assert compiled[2] == {"verb": "setSensorProbe", "args": ["$1", {"name": "$$inlet"}]}


def test_cdu_supply_uses_set_cdu_liquid_param():
    assert steps(E.SetCduSupply("c1", temperature_c=30., pressure_kpa=250., total_flow=60.)) == [
        {"verb": "setCduLiquidParam", "args": ["c1", "supplyTemperature", 30.]},
        {"verb": "setCduLiquidParam", "args": ["c1", "supplyPressure", 250.]},
        {"verb": "setCduLiquidParam", "args": ["c1", "totalFlowRate", 60.]}]
    with pytest.raises(ValueError):
        E.SetCduSupply("c1")


def test_liquid_network_edits_wait_for_their_contract_verbs():
    no_verbs = ctx(verbs=["setModelCooling"])
    with pytest.raises(ContractVerbMissing, match="setCduPumpSpeed") as missing:
        steps(E.SetPumpSpeed("c1", .8), view=no_verbs)
    # Without it the run-time path is the liquid run's own input override.
    assert "pump_rpm_ratios" in str(missing.value) and missing.value.verb == "setCduPumpSpeed"
    with pytest.raises(ContractVerbMissing, match="setPipeDiameter"):
        steps(E.SetPipeDiameter("pipe1", .05), view=no_verbs)
    with pytest.raises(ContractVerbMissing, match="setValveOpening"):
        steps(E.SetValveOpening("pipe1", "v_1", .5), view=ctx(verbs=[]))
    # Until setValveOpening is a build verb, the open setModelCooling reaches the same field
    # (cooling.valves[i].opening; projectStore.edit turns it into a v5 keyframe like any edit).
    assert steps(E.SetValveOpening("pipe1", "v_1", .5), view=no_verbs) == [
        {"verb": "setModelCooling", "args": ["pipe1", "valves.1.opening", .5]}]
    with pytest.raises(ValueError, match="adjustable"):
        steps(E.SetValveOpening("pipe1", "tee_1", .5), view=no_verbs)
    with pytest.raises(ValueError, match="no valve"):
        steps(E.SetValveOpening("pipe1", "v_9", .5), view=no_verbs)
    wired = ctx(verbs=["setCduPumpSpeed", "setPipeDiameter", "setValveOpening"])
    assert steps(E.SetPumpSpeed("c1", .8), view=wired) == [{"verb": "setCduPumpSpeed", "args": ["c1", .8]}]
    assert steps(E.SetPipeDiameter("pipe1", .05), view=wired) == [{"verb": "setPipeDiameter", "args": ["pipe1", .05]}]
    assert steps(E.SetValveOpening("pipe1", "v_1", .5), view=wired) == [
        {"verb": "setValveOpening", "args": ["pipe1", "v_1", .5]}]
    for bad in (lambda: E.SetValveOpening("pipe1", "v_1", 0.), lambda: E.SetPumpSpeed("c1", -1.),
                lambda: E.SetPipeDiameter("pipe1", 0.)):
        with pytest.raises(ValueError):
            bad()


def test_liquid_network_verbs_are_open_build_verbs_now():
    from agent.scripted_api.contract_verbs import build_verbs
    assert {"setCduPumpSpeed", "setPipeDiameter", "setValveOpening", "setPipeSegmentPoints"} <= set(build_verbs())


def test_pipe_route_sends_world_points_and_named_valves():
    route = E.SetPipeRoute("pipe1", ((0, .5, 1), (4, .5, 1)), (("v_1", (1, .5, 1.3)),))
    assert steps(route, view=ctx(verbs=["setPipeSegmentPoints"])) == [{"verb": "setPipeSegmentPoints", "args": [
        "pipe1", [{"x": 0., "y": .5, "z": 1.}, {"x": 4., "y": .5, "z": 1.}], {"v_1": {"x": 1., "y": .5, "z": 1.3}}]}]
    with pytest.raises(ContractVerbMissing, match="setPipeSegmentPoints"):
        steps(route, view=ctx(verbs=[]))
    for bad in (lambda: E.SetPipeRoute("pipe1", ((0, 0, 0),)), lambda: E.SetPipeRoute("pipe1", ((0, 0), (1, 1))),
                lambda: E.SetPipeRoute("pipe1", ((0, 0, 0), (1, float("nan"), 0)))):
        with pytest.raises(ValueError):
            bad()


def test_edits_roundtrip_through_plain_dicts():
    edits = [E.MoveModel("r1", 1., 2., 3.), E.RemoveModels(["b"]), E.SetAcuSupply("a1", temperature_c=18.),
             E.AddProbe("t1", 0., 1., 2.)]
    rows = [edit.to_dict() for edit in edits]
    assert rows[0] == {"op": "MoveModel", "uid": "r1", "x": 1., "y": 2., "z": 3.}
    assert [E.edit_from_dict(row) for row in rows] == edits
    with pytest.raises(ValueError, match="unknown edit"):
        E.edit_from_dict({"op": "Explode"})
    with pytest.raises(ValueError):
        E.edit_from_dict({"op": "MoveModel", "uid": "r1"})
    assert set(E.EDITS) >= {"MoveModel", "RotateModel", "RemoveModels", "SetServerLoad", "SetRackPower",
                            "SetAcuSupply", "AddProbe", "SetCduSupply", "SetPumpSpeed", "SetValveOpening",
                            "SetPipeDiameter"}


def test_project_view_reads_lazily_and_only_once():
    calls = []

    def loader():
        calls.append(1)
        return RACK_PROJECT
    view = ProjectView(document=RACK_PROJECT, materialize=loader, build_verbs=["x"])
    assert steps(E.MoveModel("r1", 0., 0., 0.), view=view) and calls == []
    steps(E.SetRackPower("r1", 1.), E.SetRackPower("r1", 2.), view=view)
    assert calls == [1]
