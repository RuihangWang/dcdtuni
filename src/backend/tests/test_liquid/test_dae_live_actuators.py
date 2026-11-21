"""Accepted v3 controls use edge IDs and replay through the same DAE inputs."""
from __future__ import annotations

import copy

import pytest
import torch

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.dae_experiment import DAEExperiment
from backend.liquidcooling.liquidrun_dae_schema import LiquidrunDAEV3, load_dae_input
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest


def controllable_case():
    raw = manifest()
    raw['events'] = []
    raw['edges'].append({
        'id': 'branch_valve', 'type': 'valve', 'from': 'supply', 'to': 'return',
        'params': {'D_m': .025, 'Kv_max': 1.5, 'opening': [1., .9, .8, .7],
                   'characteristic': 'linear'},
    })
    return raw


def test_parameter_overrides_retain_pump_and_valve_autograd():
    loaded = load_dae_input(controllable_case())
    pump = torch.tensor([.82], dtype=torch.float64, requires_grad=True)
    valve = torch.tensor([.73], dtype=torch.float64, requires_grad=True)
    theta = loaded.parameters(1, pump_rpm_ratio=pump, valve_opening=valve)
    layout = loaded.equation_graph.parameter_layout
    torch.testing.assert_close(theta[layout['pump_rpm_ratio']], pump)
    torch.testing.assert_close(theta[layout['valve_opening']], valve)
    gp, gv = torch.autograd.grad(
        theta[layout['pump_rpm_ratio']].sum() + 2*theta[layout['valve_opening']].sum(),
        (pump, valve),
    )
    assert gp.item() == 1. and gv.item() == 2.


def test_tensor_commands_differentiate_through_the_accepted_newton_step():
    run = DAEExperiment(load_dae_input(controllable_case()), record_frames=False)
    pump = torch.tensor(.88, dtype=torch.float64, requires_grad=True)
    valve = torch.tensor(.75, dtype=torch.float64, requires_grad=True)
    run.set_inputs(pump_rpm_ratios={'pump': pump},
                   valve_openings={'branch_valve': valve})
    assert run.step() is None
    flow = run.tensor_series('flow')[:, -1].sum()
    gp, gv = torch.autograd.grad(flow, (pump, valve))
    assert torch.isfinite(gp) and torch.isfinite(gv)
    assert abs(gp.item()) > 1e-6 and abs(gv.item()) > 1e-6


def test_live_commands_accept_next_step_and_export_exact_replayable_trace():
    raw = controllable_case()
    run = DAESimulation(raw, run_id='controlled')
    initial = copy.deepcopy(run.latest_frame)
    run.set_inputs(heat_multiplier=1.2, pump_rpm_ratios={'pump': .88},
                   valve_openings={'branch_valve': .75})
    assert run.latest_frame == initial
    assert run.metadata['pump_edge_ids'] == ['pump']
    assert run.metadata['valve_edge_ids'] == ['branch_valve']
    assert run.metadata['pending_pump_rpm_ratios'] == {'pump': .88}
    assert run.metadata['pending_valve_openings'] == {'branch_valve': .75}
    assert run.executed_manifest()['time']['N_t'] == 1
    assert next(e for e in run.executed_manifest()['edges'] if e['id'] == 'pump')['params']['rpm_ratio'] == [1.]
    first = run.step()
    assert first['seq'] == 1 and first['heat_multiplier'] == 1.2
    assert first['controls'] == {'pump_rpm_ratios': {'pump': .88},
                                 'valve_openings': {'branch_valve': .75},
                                 'primary_inlet_K': {'cdu_hx': 291.}}
    assert run.metadata['pending_pump_rpm_ratios'] == {}
    assert run.metadata['pending_valve_openings'] == {}
    assert first['edge_flow_kg_s'] != initial['edge_flow_kg_s']
    run.set_inputs(pump_rpm_ratios={'pump': .82}, valve_openings={'branch_valve': .65})
    partial = run.executed_manifest()
    edges = {edge['id']: edge for edge in partial['edges']}
    assert edges['pump']['params']['rpm_ratio'] == [1., .88]
    assert edges['branch_valve']['params']['opening'] == [1., .75]
    assert edges['cold_plate_1']['params']['Q_W'] == [500., 600.]
    assert partial['time']['N_t'] == 2
    LiquidrunDAEV3.model_validate(partial)
    replay = DAESimulation(partial)
    replay.step()
    for field in ('edge_flow_kg_s', 'primary_return_K', 'chip_temp_K'):
        assert replay.result()[field] == run.result()[field]
    second = run.step()
    assert second['seq'] == 2
    accepted = run.executed_manifest()
    edges = {edge['id']: edge for edge in accepted['edges']}
    assert edges['pump']['params']['rpm_ratio'] == [1., .88, .82]
    assert edges['branch_valve']['params']['opening'] == [1., .75, .65]


def test_checkpoint_restores_accepted_and_pending_actuators_exactly():
    raw = controllable_case()
    run = DAESimulation(raw, run_id='pending-controls')
    run.set_inputs(heat_multiplier=1.2, pump_rpm_ratios={'pump': .88},
                   valve_openings={'branch_valve': .75})
    run.step()
    run.set_inputs(heat_multiplier=.9, pump_rpm_ratios={'pump': .82},
                   valve_openings={'branch_valve': .65})
    checkpoint = run.checkpoint()
    resumed = DAESimulation.from_checkpoint(raw, checkpoint)
    assert resumed.checkpoint() == checkpoint
    assert resumed.executed_manifest() == run.executed_manifest()
    while not run.done:
        expected = run.step()
        actual = resumed.step()
        assert actual == expected
    assert resumed.result() == run.result()
    assert resumed.executed_manifest() == run.executed_manifest()


def test_equilibrium_previews_pending_controls_without_accepting_a_step():
    run = DAESimulation(controllable_case())
    initial = run.latest_frame
    baseline = run.equilibrium()
    run.set_inputs(heat_multiplier=1.2, pump_rpm_ratios={'pump': .88},
                   valve_openings={'branch_valve': .75})
    pending = run.checkpoint()
    candidate = run.equilibrium()
    assert candidate['input_basis'] == 'current_accepted_sample_with_pending_controls'
    assert candidate['evaluated_at_s'] == initial['time_s']
    assert candidate['heat_multiplier'] == 1.2
    assert candidate['controls'] == {'pump_rpm_ratios': {'pump': .88},
                                     'valve_openings': {'branch_valve': .75},
                                     'primary_inlet_K': {'cdu_hx': 290.}}
    assert candidate['edge_flow_kg_s'] != baseline['edge_flow_kg_s']
    assert run.latest_frame == initial
    assert run.checkpoint() == pending
    assert run.executed_manifest()['time']['N_t'] == 1


def test_version_one_heat_only_checkpoint_still_restores():
    raw = controllable_case()
    run = DAESimulation(raw, run_id='legacy-heat')
    run.set_heat_multiplier(1.2)
    run.step()
    run.set_heat_multiplier(.9)
    checkpoint = run.checkpoint()
    checkpoint['version'] = 1
    for key in ('pump_rpm_ratios', 'valve_openings',
                'pending_pump_rpm_ratios', 'pending_valve_openings',
                'primary_inlet_K', 'pending_primary_inlet_K'):
        checkpoint.pop(key)
    resumed = DAESimulation.from_checkpoint(raw, checkpoint)
    assert resumed.step() == run.step()


def test_tampered_accepted_actuator_trace_cannot_be_restored():
    raw = controllable_case()
    run = DAESimulation(raw)
    run.set_inputs(pump_rpm_ratios={'pump': .88})
    run.step()
    checkpoint = run.checkpoint()
    checkpoint['pump_rpm_ratios'][1]['pump'] = .99
    with pytest.raises(ValueError, match='actuator traces'):
        DAESimulation.from_checkpoint(raw, checkpoint)


def test_opening_a_closed_valve_requires_a_new_active_set():
    raw = controllable_case()
    raw['edges'][-1]['params']['opening'] = [0., 0., 0., 0.]
    run = DAESimulation(raw)
    before = run.checkpoint()
    with pytest.raises(ValueError, match='active set'):
        run.set_inputs(valve_openings={'branch_valve': .5})
    assert run.checkpoint() == before


@pytest.mark.parametrize('command', [
    {'pump_rpm_ratios': {'missing': .8}},
    {'pump_rpm_ratios': {'branch_valve': .8}},
    {'valve_openings': {'pump': .8}},
    {'pump_rpm_ratios': {'pump': True}},
    {'pump_rpm_ratios': {'pump': -.1}},
    {'pump_rpm_ratios': {'pump': float('nan')}},
    {'valve_openings': {'branch_valve': 0.}},
    {'valve_openings': {'branch_valve': 1.1}},
    {'valve_openings': {'branch_valve': '0.8'}},
    {'valve_openings': [('branch_valve', .8)]},
    {'pump_rpm_ratios': {}},
    {},
])
def test_invalid_controls_are_atomic_and_do_not_change_pending_state(command):
    run = DAESimulation(controllable_case())
    before = run.checkpoint()
    with pytest.raises((TypeError, ValueError)):
        run.set_inputs(**command)
    assert run.checkpoint() == before


def test_invalid_combined_command_does_not_replace_previous_pending_heat():
    run = DAESimulation(controllable_case())
    run.set_heat_multiplier(1.2)
    before = run.checkpoint()
    with pytest.raises((TypeError, ValueError)):
        run.set_inputs(heat_multiplier=.9, valve_openings={'branch_valve': 0.})
    assert run.checkpoint() == before
    assert run.step()['heat_multiplier'] == 1.2


def test_failed_newton_step_keeps_pending_actuators_unaccepted(monkeypatch):
    run = DAESimulation(controllable_case())
    run.set_inputs(pump_rpm_ratios={'pump': .88},
                   valve_openings={'branch_valve': .75})
    before = run.checkpoint()
    def fail_step(*args, **kwargs):
        raise RuntimeError('forced nonlinear failure')
    monkeypatch.setattr('backend.liquidcooling.dae_experiment.backward_euler_step', fail_step)
    with pytest.raises(RuntimeError, match='forced nonlinear failure'):
        run.step()
    assert run.checkpoint() == before
    assert run.executed_manifest()['time']['N_t'] == 1


def test_multi_edge_mapping_uses_stable_ids_not_component_order():
    raw = controllable_case()
    raw['edges'].append({
        'id': 'pump_2', 'type': 'pump', 'from': 'return', 'to': 'supply',
        'params': {'H0_m': 10., 'a_coef': 500000., 'rpm_ratio': [1., 1., 1., 1.]},
    })
    raw['edges'].append({
        'id': 'valve_2', 'type': 'valve', 'from': 'supply', 'to': 'return',
        'params': {'D_m': .025, 'Kv_max': 1.5, 'opening': [1., 1., 1., 1.],
                   'characteristic': 'linear'},
    })
    loaded = load_dae_input(raw)
    pump_ids = [loaded.equation_graph.graph.edge_str_ids[i] for i in loaded.equation_graph.graph.pump_indices.edge_ids.tolist()]
    valve_ids = [loaded.equation_graph.graph.edge_str_ids[i] for i in loaded.equation_graph.graph.valve_indices.edge_ids.tolist()]
    pump = torch.tensor([{'pump': .85, 'pump_2': .75}[edge_id] for edge_id in pump_ids], dtype=torch.float64)
    valve = torch.tensor([{'branch_valve': .65, 'valve_2': .55}[edge_id] for edge_id in valve_ids], dtype=torch.float64)
    theta = loaded.parameters(1, pump_rpm_ratio=pump, valve_opening=valve)
    layout = loaded.equation_graph.parameter_layout
    torch.testing.assert_close(theta[layout['pump_rpm_ratio']], pump)
    torch.testing.assert_close(theta[layout['valve_opening']], valve)
    run = DAESimulation(raw)
    run.set_inputs(pump_rpm_ratios={'pump_2': .75, 'pump': .85},
                   valve_openings={'valve_2': .55, 'branch_valve': .65})
    assert run.metadata['pending_pump_rpm_ratios'] == {'pump_2': .75, 'pump': .85}
    assert run.metadata['pending_valve_openings'] == {'valve_2': .55, 'branch_valve': .65}
    accepted = run.step()
    assert accepted['pump_rpm_ratios'] == {'pump': .85, 'pump_2': .75}
    assert accepted['valve_openings'] == {'branch_valve': .65, 'valve_2': .55}
    edges = {edge['id']: edge for edge in run.executed_manifest()['edges']}
    assert edges['pump_2']['params']['rpm_ratio'] == [1., .75]
    assert edges['valve_2']['params']['opening'] == [1., .55]
