"""Selectable HX model (owner decision 2026-10-21): counterflow-ntu | mixed-volumes.

counterflow-ntu: duty Q = eps*C_min*(T_s,in - T_p,in) with the counterflow
effectiveness, plus UA on the volumes' departures from their steady outlets;
both fluid volumes keep their storage, so the steady state is exactly the
counterflow epsilon-NTU exchanger. mixed-volumes: the previous
two-mixed-volume conductance UA*(T_s - T_p). A manifest without hxModel replays
as mixed-volumes. Expected values are computed here from the manifest numbers.
"""
from __future__ import annotations

import copy
import math

import pytest
import torch

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.liquidrun_dae_schema import LiquidrunDAEV3, load_dae_input
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.tests.test_liquid.dae_loop_fixture import CP, RHO as RHO_SEC, loop_manifest

CP_P = 4180.


def counterflow_eps(ntu, cr):
    """Textbook counterflow effectiveness, including the C_r -> 1 limit."""
    if abs(1 - cr) < 1e-12:
        return ntu / (1 + ntu)
    x = math.exp(-ntu * (1 - cr))
    return (1 - x) / (1 - cr * x)


def solved(raw):
    sim = DAESimulation(raw)
    frame = sim.latest_frame
    edges = {e: i for i, e in enumerate(sim.metadata['edge_ids'])}
    nodes = {n: i for i, n in enumerate(sim.metadata['node_ids'])}
    return sim, frame, edges, nodes


@pytest.mark.parametrize('UA,primary_flow', [(800., .3), (2500., .3), (800., .05)])
def test_counterflow_steady_matches_the_closed_form_epsilon_ntu(UA, primary_flow):
    raw = loop_manifest(hx_model='counterflow-ntu', UA_hx=UA, primary_flow=primary_flow)
    _, frame, edges, nodes = solved(raw)
    m = abs(frame['edge_flow_kg_s'][edges['hx']])
    c_s, c_p = m * CP, primary_flow * CP_P
    c_min, c_max = min(c_s, c_p), max(c_s, c_p)
    eps = counterflow_eps(UA / c_min, c_min / c_max)
    q = 300.  # the one plate's power; the HX must reject it at steady state
    t_in = 293.15 + q / (eps * c_min)
    assert frame['node_temperature_K'][nodes['dis']] == pytest.approx(t_in, abs=1e-8)
    assert frame['secondary_supply_K'][0] == pytest.approx(t_in - q / c_s, abs=1e-8)
    assert frame['primary_return_K'][0] == pytest.approx(293.15 + q / c_p, abs=1e-8)
    assert frame['diagnostics']['primary_rejection_W'] == pytest.approx(q, rel=1e-9)


def test_counterflow_balanced_streams_use_the_cr_equal_one_limit():
    raw = loop_manifest(version=4, hx_model='counterflow-ntu', UA_hx=900.)
    pump = next(e for e in raw['edges'] if e['id'] == 'pump')
    pump['params'] = {'H0_m': 8., 'a_coef': 4e7, 'mode': 'flow', 'flow_set_kg_s': .2}
    raw['primary_circuits'][0]['flow_kg_s'] = .2 * CP / CP_P  # C_p == C_s exactly
    _, frame, edges, nodes = solved(raw)
    c = .2 * CP
    eps = (900. / c) / (1 + 900. / c)
    t_in = 293.15 + 300. / (eps * c)
    assert frame['node_temperature_K'][nodes['dis']] == pytest.approx(t_in, abs=1e-8)
    assert frame['secondary_supply_K'][0] == pytest.approx(t_in - 300. / c, abs=1e-8)


def test_counterflow_runs_cooler_than_mixed_volumes_for_the_same_ua():
    """Report S1: the same UA read as two mixed volumes gives a lower duty."""
    _, cf, _, _ = solved(loop_manifest(hx_model='counterflow-ntu'))
    _, mixed, _, _ = solved(loop_manifest(hx_model='mixed-volumes'))
    assert cf['secondary_supply_K'][0] < mixed['secondary_supply_K'][0] - .05
    assert max(cf['chip_temp_K']) < max(mixed['chip_temp_K'])


def test_missing_hx_model_replays_as_mixed_volumes_bit_for_bit():
    _, absent, _, _ = solved(loop_manifest())
    sim, explicit, _, _ = solved(loop_manifest(hx_model='mixed-volumes'))
    for key in ('edge_flow_kg_s', 'node_pressure_Pa', 'node_temperature_K', 'chip_temp_K',
                'secondary_supply_K', 'primary_return_K', 'cell_temperature_K'):
        assert absent[key] == explicit[key]
    assert sim.metadata['parameter_provenance']['solver_laws']['hx_models'] == {'hx': 'mixed-volumes'}


def test_hx_model_is_recorded_per_edge_in_provenance():
    sim, _, _, _ = solved(loop_manifest(hx_model='counterflow-ntu'))
    assert sim.metadata['parameter_provenance']['solver_laws']['hx_models'] == {'hx': 'counterflow-ntu'}
    assert sim.experiment.equation_graph.provenance['hx_models'] == {'hx': 'counterflow-ntu'}


@pytest.mark.parametrize('value', ['counterflow', 'NTU', 1])
def test_unknown_hx_model_is_rejected_by_the_schema(value):
    raw = loop_manifest()
    raw['edges'][1]['params']['hxModel'] = value
    with pytest.raises(ValueError, match='hxModel'):
        LiquidrunDAEV3.model_validate(raw)


def test_counterflow_transient_keeps_storage_balances_energy_and_settles_to_the_closed_form():
    raw = loop_manifest(hx_model='counterflow-ntu', n_t=2, dt=20.)
    definition = load_dae_input(raw)
    eq = definition.equation_graph
    theta = definition.parameters(0)
    steady, _ = solve_steady(eq.problem, eq.initial_guess(theta), theta,
                             conserved=(eq.steady_neutral_storage_mask, eq.initial_guess(theta)))
    # Both HX volumes stay storage states in this model.
    layout = eq.state_layout
    assert bool(eq.differential_mask[layout['hx_secondary_temperature']].all())
    assert bool(eq.differential_mask[layout['primary_return_temperature']].all())
    # Start 5 K warmer everywhere thermal, march with constant inputs.
    y = steady.clone()
    for name in ('node_temperature', 'water_temperature', 'solid_temperature',
                 'hx_secondary_temperature', 'primary_return_temperature'):
        y[layout[name]] += 5.
    for step in range(200):
        old = y
        y, report = backward_euler_step(eq.problem, old, theta, time=20. * (step + 1), dt=20.)
        assert report.scaled_residual_inf < 1e-8
        ydot = torch.where(eq.differential_mask, (y - old) / 20., torch.zeros_like(y))
        report = {k: float(v) for k, v in eq.diagnostics(0., y, ydot, theta).items()}
        # The product's accepted-step gate (DAEExperiment._frame): Newton
        # accepts thermal rows at 1e-10 of a 1e4 W scale, ~1e-6 W per row.
        assert abs(report['energy_balance_W']) < 1e-7 * (1 + abs(report['heat_input_W']))
    thermal = eq.differential_mask
    assert float((y[thermal] - steady[thermal]).abs().max()) < 1e-6


def test_counterflow_adjoint_gradient_matches_finite_differences():
    raw = loop_manifest(hx_model='counterflow-ntu', plates=(150., 250.))
    definition = load_dae_input(raw)
    eq = definition.equation_graph
    base = definition.parameters(0).detach()
    guess = eq.initial_guess(base)
    solid = eq.state_layout['solid_temperature']

    def objective(theta):
        y, _ = solve_steady(eq.problem, guess, theta, conserved=(eq.steady_neutral_storage_mask, guess))
        return y[solid].max() + .5 * y[eq.state_layout['hx_secondary_temperature']].sum()

    theta = base.clone().requires_grad_(True)
    (gradient,) = torch.autograd.grad(objective(theta), theta)
    layout = eq.parameter_layout
    for name in ('hx_UA', 'primary_flow_kg_s', 'primary_inlet_K', 'pump_rpm_ratio', 'heat_W'):
        for index in range(layout[name].start, layout[name].stop):
            h = 1e-6 * max(1., abs(float(base[index])))
            plus, minus = base.clone(), base.clone()
            plus[index] += h
            minus[index] -= h
            fd = (float(objective(plus)) - float(objective(minus))) / (2 * h)
            assert float(gradient[index]) == pytest.approx(fd, rel=2e-5, abs=1e-9), name


def test_manufactured_benchmark_requires_the_mixed_volume_hx_it_was_derived_for():
    from types import SimpleNamespace
    from backend.liquidcooling.manufactured_benchmark import validate_contract
    from backend.liquidcooling.liquidrun_dae_schema import LiquidrunDAEV4, _v2_graph_view
    raw = loop_manifest(version=4, hx_model='counterflow-ntu', n_t=3,
                        analysis={'mode': 'transient', 'initialization': 'consistent'})
    manifest = LiquidrunDAEV4.model_validate(raw)
    with pytest.raises(ValueError, match='mixed-volumes'):
        validate_contract(SimpleNamespace(), manifest, _v2_graph_view(manifest))


# ---- a stagnant side (stream-A verification follow-up, 2026-10-21) ----------
# The inlet-based duty eps*C_min*dT_in vanishes with C_min, so a pump trip left
# the counterflow HX's secondary volume frozen while the primary kept flowing,
# and a zero primary flow gave a singular steady Jacobian. The volumes'
# departures from their steady counterflow outlets now exchange through UA, so
# a stagnant side reduces exactly to the two-mixed-volume exchange.

def _be_two_volumes(ts, tp, *, dt, ua, c_sec, c_pri, primary_rate, inlet):
    """One backward-Euler step of C_s dTs/dt = -UA(Ts-Tp),
    C_p dTp/dt = m_p c_p (T_in - Tp) + UA(Ts-Tp), solved by hand (2x2)."""
    a11, a12 = c_sec / dt + ua, -ua
    a21, a22 = -ua, c_pri / dt + primary_rate + ua
    r1, r2 = c_sec / dt * ts, c_pri / dt * tp + primary_rate * inlet
    det = a11 * a22 - a12 * a21
    return (r1 * a22 - a12 * r2) / det, (a11 * r2 - a21 * r1) / det


def test_counterflow_pump_trip_keeps_the_stagnant_secondary_exchanging():
    raw = loop_manifest(hx_model='counterflow-ntu', n_t=6, dt=5.)
    sim = DAESimulation(raw)
    sim.set_inputs(pump_rpm_ratios={'pump': 0.})
    frames = [sim.latest_frame] + [sim.step() for _ in range(5)]
    hx = sim.metadata['edge_ids'].index('hx')
    ts, tp = frames[0]['secondary_supply_K'][0], frames[0]['primary_return_K'][0]
    for frame in frames[1:]:
        assert abs(frame['edge_flow_kg_s'][hx]) < 1e-12  # the loop has stopped
        ts, tp = _be_two_volumes(ts, tp, dt=5., ua=800., c_sec=RHO_SEC * CP * .005, c_pri=998. * CP_P * .01,
                                 primary_rate=.3 * CP_P, inlet=293.15)
        assert frame['secondary_supply_K'][0] == pytest.approx(ts, abs=1e-7)
        assert frame['primary_return_K'][0] == pytest.approx(tp, abs=1e-7)
    assert frames[-1]['secondary_supply_K'][0] < frames[0]['secondary_supply_K'][0] - .05


def test_counterflow_zero_primary_flow_transient_keeps_the_primary_volume_exchanging():
    raw = loop_manifest(hx_model='counterflow-ntu', primary_flow=0., n_t=5, dt=5.,
                        analysis={'mode': 'transient', 'initialization': 'consistent'})
    sim = DAESimulation(raw)
    frames = [sim.latest_frame] + [sim.step() for _ in range(4)]
    c_pri = 998. * CP_P * .01
    tp = frames[0]['primary_return_K'][0]
    for frame in frames[1:]:
        # No primary flow: C_p dTp/dt = UA (Ts - Tp) with the solved secondary volume.
        ts = frame['secondary_supply_K'][0]
        tp = (c_pri / 5. * tp + 800. * ts) / (c_pri / 5. + 800.)
        assert frame['primary_return_K'][0] == pytest.approx(tp, abs=1e-8)
    assert frames[-1]['primary_return_K'][0] > frames[0]['primary_return_K'][0] + .01


@pytest.mark.parametrize('hx_model', [None, 'counterflow-ntu'])
@pytest.mark.parametrize('heated', [True, False])
def test_steady_state_without_a_heat_sink_is_explained_for_both_models(hx_model, heated):
    """Primary flow 0 removes the only sink: heated there is no steady state,
    unheated every uniform temperature is one. mixed-volumes used to return an
    arbitrary level; counterflow-ntu failed on a singular Jacobian."""
    raw = loop_manifest(hx_model=hx_model, primary_flow=0., plates=(300.,) if heated else (0.,))
    with pytest.raises(ValueError) as error:
        DAESimulation(raw)
    message = str(error.value)
    assert "'hx'" in message and 'no heat sink' in message and 'primary flow' in message
    assert ('no steady state exists' if heated else 'not unique') in message
    assert "initialization='consistent'" in message
