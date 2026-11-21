"""Stage 1 contracts: online primary supply-temperature (primary_inlet_K) commands.

Mirrors the accepted pump/valve machinery: stable HX edge IDs, wholesale
validation (all-or-nothing), queueing for the NEXT accepted step only,
executed-manifest materialization replacing the planned inlet_K schedule,
and checkpoint v3 carrying the accepted trace (v2 replay refuses the new
fields, following the v1->v2 actuator precedent). Solver math is frozen;
this extends only the runtime input surface.
"""
from __future__ import annotations

import copy

import pytest
import torch

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest

HX = "cdu_hx"


def supply_case():
    raw = manifest()
    raw["events"] = []
    return raw


def make_sim():
    return DAESimulation(supply_case())


def test_supply_temp_command_queues_and_applies_at_next_accepted_step():
    sim = make_sim()
    assert sim.metadata["hx_edge_ids"] == [HX]
    sim.set_inputs(primary_inlet_K={HX: 295.0})
    assert sim.metadata["pending_primary_inlet_K"] == {HX: 295.0}
    frame = sim.step()
    # frame + controls expose the accepted value; planned schedule for seq=1
    # was 291 K, the command must have replaced it in theta
    assert frame["primary_inlet_K"] == {HX: 295.0}
    assert frame["controls"]["primary_inlet_K"] == {HX: 295.0}
    assert sim.experiment.applied_inputs[-1]["primary_inlet_K"] == {HX: 295.0}
    assert sim.experiment.accepted_primary_inlet_K[-1] == {HX: 295.0}
    # physics responded: warmer supply raises the primary return vs control run
    control = make_sim()
    control_frame = control.step()
    assert frame["primary_return_K"][0] > control_frame["primary_return_K"][0]


def test_supply_temp_validation_is_all_or_nothing():
    sim = make_sim()
    before = copy.deepcopy(sim.experiment.applied_inputs)
    for bad in (
        {"nope": 295.0},          # unknown edge ID
        {HX: -1.0},               # negative Kelvin
        {HX: 0.0},                # non-positive
        {HX: float("nan")},       # non-finite
        {HX: "hot"},              # non-numeric
        [295.0],                  # not an edge-ID mapping
        {HX: True},               # bool is not a scalar command
    ):
        with pytest.raises((ValueError, TypeError)):
            sim.set_inputs(primary_inlet_K=bad)
    assert sim.experiment.pending_primary_inlet_K == {}
    frame = sim.step()
    # nothing queued: the planned schedule value for seq=1 applies
    assert frame["primary_inlet_K"] == {HX: 291.0}
    assert sim.experiment.applied_inputs == before


def test_supply_temp_rejected_after_horizon_done():
    sim = make_sim()
    while not sim.done:
        sim.step()
    with pytest.raises(ValueError):
        sim.set_inputs(primary_inlet_K={HX: 295.0})


def test_failed_newton_step_keeps_pending_supply_temp_unaccepted(monkeypatch):
    sim = make_sim()
    sim.set_inputs(primary_inlet_K={HX: 295.0})
    import backend.liquidcooling.dae_experiment as host

    calls = {"n": 0}
    real = host.backward_euler_step

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synthetic newton failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(host, "backward_euler_step", flaky)
    with pytest.raises(RuntimeError):
        sim.step()
    assert sim.experiment.applied_inputs == []
    assert sim.experiment.pending_primary_inlet_K == {HX: 295.0}
    frame = sim.step()
    assert frame["controls"]["primary_inlet_K"] == {HX: 295.0}


def test_executed_manifest_carries_accepted_inlet_trace():
    sim = make_sim()
    sim.set_inputs(primary_inlet_K={HX: 295.0})
    sim.step()
    executed = sim.executed_manifest()
    circuit = executed["primary_circuits"][0]
    # sample 0 keeps the planned 290; sample 1 carries the accepted command
    assert circuit["inlet_K"] == [290.0, 295.0]
    assert executed["source"].endswith(":accepted-trace")
    assert executed["time"]["N_t"] == 2
    # round-trip: the exported manifest is itself a valid v3 input whose
    # theta slot at sample 1 equals the accepted temperature
    reloaded = load_dae_input(executed)
    theta = reloaded.parameters(1)
    layout = reloaded.equation_graph.parameter_layout
    assert theta[layout["primary_inlet_K"]].tolist() == [295.0]


def test_checkpoint_v3_replays_supply_temp_value_for_value():
    sim = make_sim()
    sim.set_inputs(primary_inlet_K={HX: 295.0})
    sim.step()
    sim.step()  # command stays ACTIVE (like pump/valve) until replaced
    ckpt = sim.checkpoint()
    assert ckpt["version"] == 3
    assert ckpt["primary_inlet_K"] == [{HX: 290.0}, {HX: 295.0}, {HX: 295.0}]
    assert ckpt["applied_inputs"] == [
        {"seq": 1, "time_s": 1.0, "primary_inlet_K": {HX: 295.0}}
    ]
    replayed = DAESimulation.from_checkpoint(supply_case(), ckpt)
    torch.testing.assert_close(
        replayed.experiment.tensor_state, sim.experiment.tensor_state)
    assert replayed.experiment.accepted_primary_inlet_K == sim.experiment.accepted_primary_inlet_K
    assert replayed.experiment.applied_inputs == sim.experiment.applied_inputs


def test_legacy_checkpoints_reject_supply_temperature_fields():
    sim = make_sim()
    sim.step()
    ckpt = sim.checkpoint()
    assert ckpt["version"] == 3
    downgraded = copy.deepcopy(ckpt)
    downgraded["version"] = 2  # still carries primary_inlet_K keys -> refuse
    with pytest.raises(ValueError):
        DAESimulation.from_checkpoint(supply_case(), downgraded)
    clean = copy.deepcopy(ckpt)
    clean["version"] = 2
    clean.pop("primary_inlet_K")
    clean.pop("pending_primary_inlet_K")
    replayed = DAESimulation.from_checkpoint(supply_case(), clean)
    torch.testing.assert_close(
        replayed.experiment.tensor_state, sim.experiment.tensor_state)


def test_runtime_json_validation_rejects_malformed_supply_commands():
    sim = make_sim()
    with pytest.raises(ValueError):
        sim.set_inputs(primary_inlet_K={HX: "hot"})
    with pytest.raises(ValueError):
        sim.set_inputs(primary_inlet_K=[295.0])
    with pytest.raises(ValueError):
        sim.set_inputs()
