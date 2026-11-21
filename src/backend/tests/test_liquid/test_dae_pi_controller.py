"""PI loops solved inside the Newton DAE (PROJECT_STATE next step, 2026-10-25).

The controller law (CONTROLLER_LAW) is a continuous PI with back-calculation
anti-windup, integrated by the plant's own backward-Euler step:

    e = direction*(setpoint - measurement),  u_raw = bias + kp*e + I,
    u = clip(u_raw, output_min, output_max),  dI/dt = ki*e + (u - u_raw)/T_t.

So at an unsaturated steady state e = 0 exactly, and at a saturated one the
integral settles where ki*e balances the tracking term. Expected values below
come from open-loop solves of the same loop, never from the controller code.
"""
from __future__ import annotations

import copy

import pytest
import torch

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.solver.dae_newton import solve_steady
from backend.tests.test_liquid.dae_loop_fixture import loop_manifest


def chip_loop(**overrides):
    """The fixture loop with one PI loop: pump speed holds plate srv_a_cp0's solid temperature."""
    raw = loop_manifest(**{k: v for k, v in overrides.items() if k not in ('pid', 'controller')})
    pid = {'kp': .02, 'ki': .01, 'setpoint': 330., 'bias': .6, 'output_min': .2, 'output_max': 1.,
           'action': 'direct', **overrides.get('pid', {})}
    controller = {'id': 'chip_loop', 'measurement': {'kind': 'solid_temperature', 'target_id': 'srv_a_cp0'},
                  'actuator': {'edge_id': 'pump', 'field': 'rpm_ratio'}, 'pid': pid,
                  'sample_period_s': raw['time']['dt_s'], **overrides.get('controller', {})}
    raw['controllers'] = [controller]
    return raw


def open_loop_chip(rpm, **overrides):
    frame = DAESimulation(loop_manifest(rpm=rpm, **overrides)).latest_frame
    return frame['chip_temp_K'][0]


def test_open_loop_physics_is_unchanged_without_a_controller():
    definition = load_dae_input(loop_manifest())
    assert definition.equation_graph.controller_count == 0
    assert 'controller_law' not in definition.equation_graph.provenance
    assert definition.equation_graph.state_layout['controller_integral'] == slice(
        definition.equation_graph.size, definition.equation_graph.size)


def test_steady_loop_reaches_its_set_point_and_matches_the_open_loop_speed():
    # A set point between the chip temperatures at two speeds is reachable.
    hot, cool = open_loop_chip(.4), open_loop_chip(.9)
    assert hot > cool
    setpoint = .5 * (hot + cool)
    sim = DAESimulation(chip_loop(pid={'setpoint': setpoint}))
    frame = sim.latest_frame
    loop = frame['controllers']['chip_loop']
    assert frame['chip_temp_K'][0] == pytest.approx(setpoint, abs=1e-7)
    assert loop['error'] == pytest.approx(0., abs=1e-7)
    assert not loop['saturated']
    assert .4 < loop['output'] < .9
    # The pump reported is the one the plant ran: the loop output.
    assert frame['pump_rpm_ratios']['pump'] == pytest.approx(loop['output'], abs=1e-12)
    # The same speed applied open loop reproduces the same chip temperature.
    assert open_loop_chip(loop['output']) == pytest.approx(setpoint, abs=1e-6)
    assert sim.metadata['controller_ids'] == ['chip_loop']
    laws = sim.metadata['parameter_provenance']['solver_laws']
    assert laws['controller_law'] == 'pi_backward_euler_backcalc_antiwindup/v1'


def test_unreachable_set_point_saturates_without_windup():
    coolest = open_loop_chip(1.)
    sim = DAESimulation(chip_loop(pid={'setpoint': coolest - 5.}))
    loop = sim.latest_frame['controllers']['chip_loop']
    assert loop['saturated']
    assert loop['output'] == pytest.approx(1.)
    assert sim.latest_frame['chip_temp_K'][0] == pytest.approx(coolest, abs=1e-6)


def test_transient_load_step_is_rejected_by_the_loop():
    hot, cool = open_loop_chip(.4), open_loop_chip(.9)
    setpoint = .5 * (hot + cool)
    # Gains sized for this plant (about -0.5 K per unit speed, 3 s plate lag).
    raw = chip_loop(pid={'setpoint': setpoint, 'kp': .5, 'ki': .2}, n_t=181, dt=1.)
    # A load step inside the pump's authority (this loop's speed range moves
    # the plate by only ~0.25 K): larger ones saturate, as tested above.
    raw['events'] = [{'time_s': 20., 'target': 'srv_a_cp0', 'field': 'Q_W', 'value': 304.}]
    sim = DAESimulation(raw)
    while not sim.done:
        sim.step()
    result = sim.result()
    temperature = result['controllers']['chip_loop']['measurement']
    output = result['controllers']['chip_loop']['output']
    assert temperature[0] == pytest.approx(setpoint, abs=1e-6)
    assert max(temperature) > setpoint + .02          # the step disturbs the plate
    assert temperature[-1] == pytest.approx(setpoint, abs=1e-3)  # and the integral removes it
    assert not any(result['controllers']['chip_loop']['saturated'])
    assert output[-1] > output[0] + .01               # by running the pump faster
    # Every accepted step closed the global energy balance (checked per frame).
    assert all(abs(d['energy_balance_W']) < 1e-6 for d in result['diagnostics'])


def test_steady_adjoint_gives_the_set_point_sensitivity():
    hot, cool = open_loop_chip(.4), open_loop_chip(.9)
    definition = load_dae_input(chip_loop(pid={'setpoint': .5 * (hot + cool)}))
    eq = definition.equation_graph
    base = definition.parameters(0).detach()
    guess = eq.initial_guess(base)
    solid = eq.state_layout['solid_temperature']
    sp = eq.parameter_layout['controller_setpoint'].start

    def chip(theta):
        y, _ = solve_steady(eq.problem, guess, theta, conserved=(eq.steady_neutral_storage_mask, guess))
        return y[solid][0]

    theta = base.clone().requires_grad_(True)
    (gradient,) = torch.autograd.grad(chip(theta), theta)
    # Unsaturated PI holds the plate at the set point: dT/dsetpoint = 1.
    assert float(gradient[sp]) == pytest.approx(1., abs=1e-8)
    step = 1e-3
    plus, minus = base.clone(), base.clone()
    plus[sp] += step
    minus[sp] -= step
    assert float((chip(plus) - chip(minus)) / (2 * step)) == pytest.approx(1., abs=1e-6)


@pytest.mark.parametrize('pid,controller,message', [
    ({'kd': .1}, {}, 'kd and derivative_tau_s must be 0'),
    ({'rate_limit_per_s': .05}, {}, 'slew limit'),
    ({'ki': 0.}, {}, 'ki > 0'),
    ({}, {'sample_period_s': 2.}, 'sample_period_s must equal time.dt_s'),
])
def test_contract_refuses_what_the_dae_does_not_model(pid, controller, message):
    raw = chip_loop(pid=pid, controller=controller, n_t=5, dt=1.)
    with pytest.raises(ValueError, match=message):
        load_dae_input(raw)


def test_a_flow_held_pump_is_not_an_actuator():
    raw = chip_loop(version=4)
    pump = next(e for e in raw['edges'] if e['id'] == 'pump')
    pump['params'] = {'H0_m': 8., 'a_coef': 4e7, 'mode': 'flow', 'flow_set_kg_s': .2}
    with pytest.raises(ValueError, match='holds a flow set point'):
        load_dae_input(raw)


def test_a_valve_loop_needs_an_open_floor():
    raw = loop_manifest(extra_branch='parallel')
    raw['controllers'] = [{'id': 'v', 'measurement': {'kind': 'solid_temperature', 'target_id': 'srv_b_cp0'},
                           'actuator': {'edge_id': 'srv_b_valve', 'field': 'opening'},
                           'pid': {'kp': .01, 'ki': .01, 'setpoint': 320., 'bias': .5, 'output_min': 0.,
                                   'output_max': 1., 'action': 'direct'},
                           'sample_period_s': 1.}]
    with pytest.raises(ValueError, match='output_min > 0'):
        load_dae_input(raw)
    raw['controllers'][0]['pid']['output_min'] = .1
    frame = DAESimulation(raw).latest_frame
    assert frame['valve_openings']['srv_b_valve'] == pytest.approx(frame['controllers']['v']['output'])


def test_online_commands_cannot_fight_the_loop():
    sim = DAESimulation(chip_loop(n_t=5, dt=1.))
    with pytest.raises(ValueError, match='driven by a controller'):
        sim.set_inputs(pump_rpm_ratios={'pump': .5})
    sim.set_inputs(heat_multiplier=1.2)   # other inputs still work
    sim.step()


def test_checkpoint_replay_reproduces_the_closed_loop():
    raw = chip_loop(n_t=6, dt=1.)
    sim = DAESimulation(raw)
    sim.step()
    sim.set_inputs(heat_multiplier=1.3)
    sim.step()
    checkpoint = copy.deepcopy(sim.checkpoint())
    restored = DAESimulation.from_checkpoint(raw, checkpoint)
    assert restored.latest_frame['controllers'] == sim.latest_frame['controllers']
    assert restored.latest_frame['chip_temp_K'] == sim.latest_frame['chip_temp_K']
