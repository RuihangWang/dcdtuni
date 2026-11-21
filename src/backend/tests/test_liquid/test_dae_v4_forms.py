"""The four .liquidrun v4 endpoint forms (transient design §3, plan T3a).

Each form is checked on the small cold-plate + two-volume HX + pump loop of
test_equation_graph against a solution derived here, not by the production
residual: the HX secondary set point and the cold-plate temperature rise by
backward-Euler time refinement of the coupled manufactured trajectory of
test_dae_physical_mms, the flow-controlled pump and the pressure-boundary series
by exact hydraulic states. The energy balance is asserted on steps where the
prescribed input is CHANGING, where a sign error in a storage term would show.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
import torch

from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.tests.test_liquid.test_equation_graph import api, exact_state, primary, small_graph
from backend.tests.test_liquid.test_liquidrun_dae_schema import manifest as v3_manifest

F64 = torch.float64
W = .2


def one(value):
    return torch.tensor([value], dtype=F64)


def equation(**fields):
    graph = small_graph()
    for name, value in fields.items():
        setattr(graph, name, value)
    return api().EquationGraph(graph, primary())


def trajectory(t):
    """The coupled manufactured solution of test_dae_physical_mms, by hand."""
    s, p = 300 + 2 * math.sin(W * t), 290 + math.cos(W * t)
    ds, dds, dp = 2 * W * math.cos(W * t), -2 * W * W * math.sin(W * t), -W * math.sin(W * t)
    conductance = .2 * 4000 * (1 - math.exp(-100 / (.2 * 4000)))
    qcp = 4000 * ds + 500 * (s - p)
    solid = s + qcp / conductance
    dsolid = ds + (4000 * dds + 500 * (ds - dp)) / conductance
    return {'s': s, 'p': p, 'solid': solid, 'power': 1000 * dsolid + qcp,
            'inlet': p + (8000 * dp - 500 * (s - p)) / (.1 * 4000)}


def balance(eq, y, old, theta, dt):
    ydot = torch.where(eq.differential_mask, (y - old) / dt, torch.zeros_like(y))
    return eq.diagnostics(0., y, ydot, theta)


def refine(eq, theta_at):
    errors = []
    for n in [6, 12, 24]:
        dt = 3. / n
        start = trajectory(0.)
        y = exact_state(eq, theta_at(0., dt), secondary=start['s'], primary_return=start['p'], solid=start['solid'])
        for step in range(n):
            t = dt * (step + 1)
            theta = theta_at(t, dt)
            old = y
            y, report = backward_euler_step(eq.problem, y, theta, time=t, dt=dt)
            assert report.scaled_residual_inf < 1e-8
            yield t, y, old, theta, dt
        end = trajectory(3.)
        expected = exact_state(eq, theta, secondary=end['s'], primary_return=end['p'], solid=end['solid'])
        errors.append(float((y[eq.differential_mask] - expected[eq.differential_mask]).abs().max()))
    orders = [math.log(a / b, 2) for a, b in zip(errors, errors[1:])]
    print('V4_MMS ' + json.dumps({'errors_K': errors, 'orders': orders}))
    assert all(.85 < value < 1.15 for value in orders), {'errors_K': errors, 'orders': orders}


def test_setpoint_hx_refines_at_backward_euler_order_and_balances_energy_on_the_ramp():
    eq = equation(hx_setpoint_mode=torch.tensor([True]))
    assert not bool(eq.differential_mask[eq.state_layout['hx_secondary_temperature']].any())

    def theta_at(t, dt):
        now, before = trajectory(t), trajectory(max(t - dt, 0.))
        rate = (now['s'] - before['s']) / dt if t > 0 else 0.
        return eq.parameters(heat_W=one(now['power']), primary_inlet_K=one(now['inlet']),
                             hx_T_set=one(now['s']), hx_T_set_rate=one(rate))
    for t, y, old, theta, dt in refine(eq, theta_at):
        assert eq.unpack(y)['hx_secondary_temperature'].item() == pytest.approx(trajectory(t)['s'], abs=1e-9)
        assert abs(float(balance(eq, y, old, theta, dt)['energy_balance_W'])) < 1e-6


def test_temperature_rise_plate_refines_at_backward_euler_order_and_derives_its_heat():
    eq = equation(cp_rise_mode=torch.tensor([True]))

    def theta_at(t, dt):
        now = trajectory(t)
        # The loop flow is exactly .2 kg/s, so Q = m cp dT reproduces the power.
        return eq.parameters(heat_W=one(0.), primary_inlet_K=one(now['inlet']),
                             cp_dT_set=one(now['power'] / (.2 * 4000)))
    for t, y, old, theta, dt in refine(eq, theta_at):
        report = balance(eq, y, old, theta, dt)
        assert float(report['heat_input_W']) == pytest.approx(trajectory(t)['power'], rel=1e-9)
        assert abs(float(report['energy_balance_W'])) < 1e-6


def test_flow_controlled_pump_holds_its_set_point_and_the_network_sets_the_rise():
    eq = equation(pump_flow_mode=torch.tensor([True]))
    assert eq.residual_scale[eq.state_layout['flow']][2].item() == 1.  # edges: cp, hx, pump
    before = eq.parameters(heat_W=one(100.), pump_flow_set=one(.2))
    y, report = solve_steady(eq.problem, eq.initial_guess(before), before)
    assert report.scaled_residual_inf < 1e-8
    theta = eq.parameters(heat_W=one(100.), pump_flow_set=one(.1))
    step, report = backward_euler_step(eq.problem, y, theta, time=1., dt=1.)
    assert report.scaled_residual_inf < 1e-8
    fields = eq.unpack(step, theta)
    assert fields['flow'].tolist() == pytest.approx([.1, .1, .1], abs=1e-12)
    # Linear losses: cold plate 1000 Pa/(kg/s), HX 2000 Pa/(kg/s); supply held at 150 kPa.
    assert fields['pressure'].tolist() == pytest.approx([150000., 149900., 149700.], abs=1e-6)
    assert abs(float(balance(eq, step, y, theta, 1.)['energy_balance_W'])) < 1e-6


def test_pressure_boundary_series_shifts_every_pressure_and_leaves_the_flows():
    graph = small_graph()
    series = graph.bc_P.unsqueeze(1).repeat(1, graph.N_t).clone()
    series[0, 1:] += 10000.
    graph.bc_P_series = series
    eq = api().EquationGraph(graph, primary())
    fields = []
    for index in (0, 1):
        theta = eq.parameters(index, heat_W=one(100.))
        y, report = solve_steady(eq.problem, eq.initial_guess(theta), theta)
        assert report.scaled_residual_inf < 1e-8
        fields.append(eq.unpack(y, theta))
    assert torch.allclose(fields[1]['flow'], fields[0]['flow'], atol=1e-12)
    assert torch.allclose(fields[1]['pressure'] - fields[0]['pressure'],
                          torch.full_like(fields[0]['pressure'], 10000.), atol=1e-6)


def test_setpoint_hx_has_a_steady_root_that_rejects_exactly_the_plate_heat():
    eq = equation(hx_setpoint_mode=torch.tensor([True]))
    theta = eq.parameters(heat_W=one(100.), hx_T_set=one(295.), hx_T_set_rate=one(0.))
    y, report = solve_steady(eq.problem, eq.initial_guess(theta), theta)
    assert report.scaled_residual_inf < 1e-8
    fields = eq.unpack(y, theta)
    assert fields['hx_secondary_temperature'].item() == pytest.approx(295., abs=1e-9)
    # All 100 W leave through the primary: 290 K + 100 W / (0.1 kg/s * 4000 J/kgK).
    assert fields['primary_return_temperature'].item() == pytest.approx(290.25, abs=1e-9)


# --------------------------------------------------------------------------
# The v4 manifest
# --------------------------------------------------------------------------

def schema():
    return __import__('backend.liquidcooling.liquidrun_dae_schema', fromlist=['LiquidrunDAEV4'])


def v4_manifest():
    raw = v3_manifest()
    raw.update(schemaVersion=4, version='4.0', events=[])
    raw['source_project']['schema_version'] = 5
    raw['transient'] = {'durationS': 3., 'start': {'kind': 'steadyConverged'},
                        'tracks': [{'modelUid': 'cdu-uid', 'param': 'cdu.secondarySupplyT', 'target': ''}]}
    edges = {edge['id']: edge for edge in raw['edges']}
    edges['cdu_hx']['params'].update(mode='setpoint', T_set_K=[300., 300., 302., 302.])
    pump = edges['pump']['params']
    pump.pop('rpm_ratio')
    pump.update(mode='flow', flow_set_kg_s=[.2, .2, .1, .1])
    plate = edges['cold_plate_1']['params']
    plate.pop('Q_W')
    plate.update(heat_mode='temperature_rise', dT_set_K=[5., 5., 5., 8.])
    raw['nodes'][0]['bc']['P_Pa'] = [200000., 200000., 210000., 210000.]
    return raw


def test_v4_manifest_loads_every_form_and_steps_through_its_series():
    from backend.liquidcooling.dae_experiment import DAEExperiment
    raw = v4_manifest()
    before = copy.deepcopy(raw)
    loaded = schema().load_dae_input(raw)
    assert raw == before
    graph = loaded.equation_graph.graph
    assert graph.hx_setpoint_mode.tolist() == [True]
    assert graph.pump_flow_mode.tolist() == [True]
    assert graph.cp_rise_mode.tolist() == [True]
    assert graph.bc_P_series[0].tolist() == [200000., 200000., 210000., 210000.]
    run = DAEExperiment(loaded)
    eq = loaded.equation_graph
    states = [run.tensor_state]
    for _ in range(3):
        frame = run.step()
        assert abs(frame['diagnostics']['energy_balance_W']) < 1e-6
        states.append(run.tensor_state)
    for index, y in enumerate(states):
        theta = loaded.parameters(index)
        fields = eq.unpack(y, theta)
        edge = {name: i for i, name in enumerate(graph.edge_str_ids)}
        node = {name: i for i, name in enumerate(graph.node_ids)}
        assert fields['flow'][edge['pump']].item() == pytest.approx([.2, .2, .1, .1][index], abs=1e-10)
        assert fields['hx_secondary_temperature'].item() == pytest.approx([300., 300., 302., 302.][index], abs=1e-9)
        assert fields['pressure'][node['supply']].item() == pytest.approx([200000., 200000., 210000., 210000.][index])
        heat = theta[eq.parameter_layout['cp_dT_set']].item()
        assert heat == pytest.approx([5., 5., 5., 8.][index])


def test_heat_multiplier_scales_a_temperature_rise_too():
    loaded = schema().load_dae_input(v4_manifest())
    theta = loaded.parameters(3, heat_multiplier=1.5)
    assert theta[loaded.equation_graph.parameter_layout['cp_dT_set']].item() == pytest.approx(12.)


@pytest.mark.parametrize('mutate,message', [
    (lambda d: d.update(events=[{'time_s': 1., 'target': 'cold_plate_1', 'field': 'Q_W', 'value': 1.}]), 'events'),
    (lambda d: d.pop('transient'), 'transient'),
    (lambda d: d['edges'][3]['params'].pop('flow_set_kg_s'), 'flow_set_kg_s'),
    (lambda d: d['edges'][3]['params'].update(mode='curve'), 'flow_set_kg_s'),
    (lambda d: d['edges'][0]['params'].update(Q_W=100.), 'Q_W'),
    (lambda d: d['edges'][0]['params'].pop('dT_set_K'), 'dT_set_K'),
    (lambda d: d['edges'][1]['params'].pop('T_set_K'), 'T_set_K'),
    (lambda d: d['edges'][1]['params'].update(T_set_K=[300., 301.]), 'N_t'),
    (lambda d: d['nodes'][0]['bc'].update(P_Pa=[1., 2.]), 'N_t'),
    (lambda d: d['edges'][1]['params'].update(mode='other'), 'mode'),
])
def test_invalid_v4_is_rejected_by_name(mutate, message):
    raw = v4_manifest()
    mutate(raw)
    with pytest.raises(Exception, match=message):
        schema().load_dae_input(raw)


def test_v3_stays_strict_about_v4_fields():
    raw = v3_manifest()
    raw['edges'][1]['params']['mode'] = 'setpoint'
    with pytest.raises(Exception, match='mode'):
        schema().load_dae_input(raw)


def test_v4_json_schema_file_is_current_and_v3_is_untouched():
    module = schema()
    folder = Path(module.__file__).parent
    assert json.loads((folder / 'liquidrun_dae_v4.schema.json').read_text()) == module.dae_json_schema(4)
    assert json.loads((folder / 'liquidrun_dae.schema.json').read_text()) == module.dae_json_schema()


# --------------------------------------------------------------------------
# The runtime accepts v4 (plan T3a.5)
# --------------------------------------------------------------------------

def coupled_v4():
    raw = v4_manifest()
    raw['racks'] = [{'id': 'rack-uid', 'servers': [{
        'id': 'server-uid', 'cp_cu_pairs': [{'id': 'plate-uid', 'edge_id': 'cold_plate_1', 'Q_W': 500.0}]}]}]
    raw['object_bindings'].update({
        'rack-uid': {'label': 'Rack', 'kind': 'rack', 'node_ids': [], 'edge_ids': ['cold_plate_1']},
        'rack-uid/server-uid': {'label': 'Server', 'kind': 'server', 'parent_uid': 'rack-uid',
                                'node_ids': [], 'edge_ids': ['cold_plate_1']},
    })
    return raw


def test_run_manager_runs_v4_and_records_its_input(tmp_path):
    from backend.liquidcooling.run_manager import LiquidRunManager
    manager = LiquidRunManager()
    created = manager.create(v4_manifest(), case_dir=tmp_path)
    run_id = created['run_id']
    assert created['metadata']['solver'] == 'differentiable_newton_dae'
    manager.operate(run_id, 'step')
    manager.operate(run_id, 'step')
    result = manager.result(run_id)
    assert abs(result['diagnostics'][-1]['energy_balance_W']) < 1e-6
    saved = json.loads((tmp_path / 'Liquid' / 'log' / f'run-{run_id}' / 'input.liquidrun').read_text())
    assert saved['schemaVersion'] == 4 and saved['transient']['durationS'] == 3.


def test_accepted_v4_prefix_keeps_the_forms_and_scales_the_rise():
    from backend.liquidcooling.accepted_run_input import build_accepted_manifest
    loaded = schema().load_dae_input(v4_manifest())
    out = build_accepted_manifest(loaded, [1., 1.2, .8])
    edges = {edge['id']: edge['params'] for edge in out['edges']}
    assert out['schemaVersion'] == 4 and out['time']['N_t'] == 3 and out['events'] == []
    assert 'Q_W' not in edges['cold_plate_1']
    assert edges['cold_plate_1']['dT_set_K'] == pytest.approx([5., 6., 4.])
    assert 'rpm_ratio' not in edges['pump'] and edges['pump']['flow_set_kg_s'] == [.2, .2, .1]
    assert edges['cdu_hx']['T_set_K'] == [300., 300., 302.]
    assert out['nodes'][0]['bc']['P_Pa'] == [200000., 200000., 210000.]
    schema().load_dae_input(out)


def test_heat_audit_takes_a_temperature_rise_plate_input_from_the_solved_flow():
    from backend.liquidcooling.dae_runtime import DAESimulation
    from backend.liquidcooling.hybrid_heat_audit import audit_hybrid_heat
    raw = coupled_v4()
    run = DAESimulation(raw)
    run.set_heat_multiplier(1.2)
    run.step()
    run.set_heat_multiplier(.8)
    run.step()
    executed, result = run.executed_manifest(), run.result()
    assert executed['schemaVersion'] == 4
    report = audit_hybrid_heat(executed, result, 'project-stable-uid')
    cp = raw['fluid']['cp_J_kgK']
    samples = report['servers'][0]['samples']
    assert [row['cp_input_W'] for row in samples] == pytest.approx([.2 * cp * 5, .2 * cp * 6, .1 * cp * 4], rel=1e-9)
    assert max(abs(row['closure_residual_W']) for row in samples) < 1e-6


def test_the_air_handoff_and_runner_recognise_v4():
    module = schema()
    assert module.is_dae_manifest(v4_manifest()) and module.is_dae_manifest(v3_manifest())
    assert isinstance(module.parse_dae_manifest(v4_manifest()), module.LiquidrunDAEV4)


def test_v4_forms_are_edge_overrides_on_profile_backed_equipment_never_profile_fields():
    raw = v4_manifest()
    pump = next(edge for edge in raw['edges'] if edge['id'] == 'pump')
    curve = {key: pump['params'].pop(key) for key in ('H0_m', 'a_coef')}
    raw['parameter_pack'] = {'schema_version': '1.0', 'id': 'v4-pack', 'revision': '1', 'source': 'mock', 'description': 'test',
                             'profiles': {'pump-profile': {'type': 'pump', 'params': curve,
                                                           'units': {'H0_m': 'm', 'a_coef': 'm/(m3/s)^2'}}}}
    pump['parameter_ref'] = 'pump-profile'
    loaded = schema().load_dae_input(raw)
    assert loaded.equation_graph.graph.pump_flow_mode.tolist() == [True]
    bad = copy.deepcopy(raw)
    bad['parameter_pack']['profiles']['pump-profile']['params']['mode'] = 'flow'
    with pytest.raises(Exception, match='mode'):
        schema().load_dae_input(bad)


def test_flow_held_pump_rejects_online_rpm_without_queueing_other_fields():
    from backend.liquidcooling.dae_runtime import DAESimulation
    raw = v4_manifest()
    run = DAESimulation(raw)
    before = run.checkpoint()
    assert run.metadata['pump_edge_ids'] == ['pump']
    assert run.metadata['pump_rpm_control_edge_ids'] == []
    with pytest.raises(ValueError, match='flow-held pump.*flow setpoint track'):
        run.set_inputs(heat_multiplier=1.2, pump_rpm_ratios={'pump': .9})
    assert run.checkpoint() == before
    assert run.metadata['pending_pump_rpm_ratios'] == {}
    frame = run.step()
    assert frame['heat_multiplier'] == 1.


def test_curve_pump_is_advertised_as_an_online_rpm_control():
    from backend.liquidcooling.dae_runtime import DAESimulation
    run = DAESimulation(v3_manifest())
    assert run.metadata['pump_rpm_control_edge_ids'] == run.metadata['pump_edge_ids'] == ['pump']
    run.set_inputs(pump_rpm_ratios={'pump': .9})
    assert run.metadata['pending_pump_rpm_ratios'] == {'pump': .9}


def test_equilibrium_freezes_the_current_v4_hx_setpoint_rate():
    from backend.liquidcooling.dae_runtime import DAESimulation
    run = DAESimulation(v4_manifest())
    run.step()
    changing = run.step()  # held supply rose 300 -> 302 K at this sample
    assert abs(changing['diagnostics']['storage_rate_W']) > 1.
    before = run.checkpoint()
    answer = run.equilibrium()
    assert answer['secondary_supply_K'] == pytest.approx([302.], abs=1e-9)
    assert answer['diagnostics']['storage_rate_W'] == pytest.approx(0., abs=1e-9)
    assert answer['diagnostics']['primary_rejection_W'] == pytest.approx(answer['diagnostics']['heat_input_W'], abs=1e-6)
    assert run.checkpoint() == before
