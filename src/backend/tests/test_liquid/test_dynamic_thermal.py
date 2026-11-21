"""Conservative thermal trajectories: independent energy and sensitivity oracles."""
import json
from pathlib import Path

import pytest
import torch

from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.solver.model import LiquidODILModel
from backend.liquidcooling.solver.hybrid import _solve_thermal_direct, hybrid_solve

DTYPE = torch.float64


def loop_graph(n=120, dt=2.):
    raw = json.loads((Path(__file__).parent / "cdu_loop.liquidrun").read_text())
    raw['time'].update(N_t=n, dt_s=dt)
    raw['nodes'][0]['bc'].pop('T_K')
    raw['nodes'][-1] = {'id': 'return', 'type': 'junction', 'position_m': [2, 0, 0]}
    raw['edges'].append({'id': 'pump', 'type': 'pump', 'from': 'return', 'to': 'supply',
                         'params': {'H0_m': 10., 'a_coef': 5e5, 'rpm_ratio': 1.}})
    return compile_graph(LiquidrunConfig.model_validate(raw))


def initial(g):
    return (torch.full((g.N_nodes,), g.T_fluid_init, dtype=DTYPE),
            torch.full((g.N_cells,), g.T_fluid_init, dtype=DTYPE),
            torch.full((g.N_cp,), g.T_solid_init, dtype=DTYPE))


def step(g, m, previous=None, **kw):
    from backend.liquidcooling.solver.thermal_step import thermal_step
    return thermal_step(g, m, *(initial(g) if previous is None else previous), **kw)


def test_production_closed_loop_rejects_heat_without_supply_temperature():
    g = loop_graph()
    model = LiquidODILModel(g)
    hybrid_solve(model, g.solver_config, callback=lambda _: None)
    T = _solve_thermal_direct(model, g)
    e = int(g.hx_indices.edge_ids[0])
    rejected = model.state['m'][e, -1].abs() * g.fluid_cp * (T[g.edge_from[e], -1] - T[g.edge_to[e], -1])
    assert rejected > 490.  # 500 W IT input, independently measured HX enthalpy drop
    assert rejected < 510.


def test_production_uses_each_flow_sample():
    g = loop_graph(n=8)
    fixed, changed = LiquidODILModel(g), LiquidODILModel(g)
    with torch.no_grad():
        fixed.state['m'].fill_(.4)
        changed.state['m'].fill_(.4)
        changed.state['m'][:, 2:] = .08
    _solve_thermal_direct(fixed, g)
    _solve_thermal_direct(changed, g)
    assert abs(fixed.state['T_s'][0, -1] - changed.state['T_s'][0, -1]) > .01


def test_loop_energy_and_mass_conservation_every_step():
    g = loop_graph(n=8)
    previous = initial(g)
    for t in range(1, g.N_t):
        r = step(g, torch.full((g.N_edges,), .3, dtype=DTYPE), previous, time_index=t)
        storage = ((r.T_w - previous[1]) * g.cell_volume * g.fluid_rho * g.fluid_cp).sum() / g.dt
        storage += ((r.T_s - previous[2]) * g.cp_params.C_solid).sum() / g.dt
        e = int(g.hx_indices.edge_ids[0])
        rejection = .3 * g.fluid_cp * (r.T_node[g.edge_from[e]] - r.edge_outlet[e])
        torch.testing.assert_close(storage + rejection, g.cp_params.Q_W[:, t].sum(), atol=1e-7, rtol=1e-10)
        assert r.diagnostics['energy_balance_W'].abs() < 1e-7
        assert r.diagnostics['mass_residual_kg_s'].abs().max() < 1e-12
        previous = (r.T_node, r.T_w, r.T_s)


def pipe_graph():
    raw = json.loads((Path(__file__).parent / 'minimal.liquidrun').read_text())
    raw['edges'][0]['params']['n_cells'] = 1
    return compile_graph(LiquidrunConfig.model_validate(raw))


@pytest.mark.parametrize('reverse', [False, True])
def test_pipe_implicit_step_actual_upstream_and_boundary_energy(reverse):
    g = pipe_graph()
    g.bc_T_mask[:] = True
    g.bc_T[:] = torch.tensor([310., 330.], dtype=DTYPE)
    m = torch.tensor([-.2 if reverse else .2], dtype=DTYPE)
    prev = initial(g)
    r = step(g, m, prev)
    capacity = g.cell_volume[0] * g.fluid_rho * g.fluid_cp
    expected = (capacity / g.dt * prev[1][0] + .2 * g.fluid_cp * (330. if reverse else 310.)) / (capacity / g.dt + .2 * g.fluid_cp)
    torch.testing.assert_close(r.T_w[0], expected)
    assert r.diagnostics['energy_balance_W'].abs() < 1e-7


def test_zero_flow_stores_heat_without_advective_leakage():
    g = loop_graph(n=2)
    prev = initial(g)
    r = step(g, torch.zeros(g.N_edges, dtype=DTYPE), prev)
    torch.testing.assert_close(r.T_s, prev[2] + g.cp_params.Q_W[:, 1] * g.dt / g.cp_params.C_solid)
    torch.testing.assert_close(r.T_w, prev[1], atol=0, rtol=0)
    torch.testing.assert_close(r.T_node, prev[0], atol=0, rtol=0)
    assert r.diagnostics['hx_rejection_W'] == 0
    assert r.diagnostics['energy_balance_W'].abs() < 1e-8


@pytest.mark.parametrize('parameter', ['flow', 'ua', 'heat'])
def test_multistep_gradients_match_centered_difference(parameter):
    def objective(x):
        g = loop_graph(n=5)
        m = torch.ones(g.N_edges, dtype=DTYPE) * (x if parameter == 'flow' else .3)
        if parameter == 'ua':
            g.cp_params.UA = x.reshape(1)
        if parameter == 'heat':
            g.cp_params.Q_W = x.expand(1, g.N_t)
        prev = initial(g)
        for t in range(1, g.N_t):
            r = step(g, m, prev, time_index=t)
            prev = r.T_node, r.T_w, r.T_s
        return r.T_s.sum()
    value, h = {'flow': (.3, 1e-5), 'ua': (150., 1e-2), 'heat': (500., 1e-2)}[parameter]
    x = torch.tensor(value, dtype=DTYPE, requires_grad=True)
    grad, = torch.autograd.grad(objective(x), x)
    fd = (objective(x.detach() + h) - objective(x.detach() - h)) / (2*h)
    assert grad.abs() > 1e-8
    torch.testing.assert_close(grad, fd, rtol=1e-5, atol=1e-7)


@pytest.mark.parametrize('dt', [0., -1., float('nan')])
def test_reject_invalid_dt(dt):
    g = loop_graph(n=2)
    with pytest.raises(ValueError, match='dt'):
        step(g, torch.ones(g.N_edges, dtype=DTYPE), dt=dt)


def test_reject_nonfinite_state_and_singular_loop():
    g = loop_graph(n=2)
    prev = list(initial(g))
    prev[1][0] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        step(g, torch.ones(g.N_edges, dtype=DTYPE), prev)
    # No thermal storage/anchors: a pure adiabatic circulation has no temperature datum.
    g.pipe_indices = g.cp_indices = g.hx_indices = None
    g.N_cells = g.N_cp = 0
    g.cell_volume = torch.empty(0, dtype=DTYPE)
    g.cell_heat_W = None
    g.cp_params = None
    with pytest.raises(RuntimeError, match='thermal'):
        step(g, torch.ones(g.N_edges, dtype=DTYPE))


def test_hydraulic_failure_stops_before_thermal_publication():
    g = loop_graph(n=3)
    g.pump_params.rpm_ratio[0, 1] = float('nan')
    model = LiquidODILModel(g)
    with pytest.raises(RuntimeError, match='hydraulic solve failed'):
        hybrid_solve(model, g.solver_config, callback=lambda _: None)
    assert not getattr(model, 'hybrid_converged', True)
    assert not hasattr(model, 'thermal_node_history')


def test_runner_reports_solved_reverse_inlet_and_actual_convergence():
    from backend.liquidcooling.runner import _extract_results
    from backend.liquidcooling.solver.common import SolveLog
    g = loop_graph(n=3)
    model = LiquidODILModel(g)
    with torch.no_grad():
        model.state['m'].fill_(-.2)
    node = _solve_thermal_direct(model, g)
    model.hybrid_converged = False
    logs = [SolveLog(epoch=1, total=0., losses={}, wall_time_s=0.)]
    result = _extract_results(model, g, logs)
    assert not result.converged
    e = int(g.cp_indices.edge_ids[0])
    expected = node[g.edge_to[e]].detach().numpy()
    import numpy as np
    np.testing.assert_allclose(result.cp_details[0].water_inlet_temp_K, expected)


def test_pipe_distributed_source_and_initial_field_preserved():
    g = pipe_graph()
    g.cell_T_init = torch.tensor([319.], dtype=DTYPE)
    g.cell_heat_W = torch.full((g.N_cells, g.N_t), 27., dtype=DTYPE)
    model = LiquidODILModel(g)
    with torch.no_grad():
        model.state['m'].zero_()
    _solve_thermal_direct(model, g)
    expected = 319. + torch.arange(g.N_t, dtype=DTYPE) * g.dt * 27. / (g.cell_volume[0] * g.fluid_rho * g.fluid_cp)
    torch.testing.assert_close(model.state['T_w'][0], expected)


def test_unequal_incoming_streams_mix_enthalpy_without_storage():
    raw = json.loads((Path(__file__).parent / 'minimal.liquidrun').read_text())
    raw['nodes'] = [
        {'id':'a','type':'pressure_bc','position_m':[0,0,0],'bc':{'P_Pa':2e5,'T_K':300.}},
        {'id':'b','type':'pressure_bc','position_m':[0,1,0],'bc':{'P_Pa':2e5,'T_K':340.}},
        {'id':'mix','type':'junction','position_m':[1,0,0]},
        {'id':'out','type':'pressure_bc','position_m':[2,0,0],'bc':{'P_Pa':1e5}},
    ]
    raw['edges'] = [{'id':name,'type':'valve','from':u,'to':v,'params':{'Kv_max':2.5,'opening':1.}}
                    for name,u,v in [('a-m','a','mix'),('b-m','b','mix'),('m-o','mix','out')]]
    g = compile_graph(LiquidrunConfig.model_validate(raw))
    r = step(g, torch.tensor([.2,.6,.8],dtype=DTYPE))
    assert abs(r.T_node[2] - 330.) < 1e-10
    assert r.T_w.numel() == r.T_s.numel() == 0
    assert r.diagnostics['energy_balance_W'].abs() < 1e-8


def test_production_reverses_multicell_order_at_later_step():
    raw = json.loads((Path(__file__).parent / 'minimal.liquidrun').read_text())
    raw['time']['N_t'] = 4
    raw['edges'][0]['params']['n_cells'] = 3
    g = compile_graph(LiquidrunConfig.model_validate(raw))
    g.bc_T_mask[:] = True
    g.bc_T[:] = torch.tensor([310., 330.], dtype=DTYPE)
    g.cell_T_init = torch.tensor([300., 305., 308.], dtype=DTYPE)
    model = LiquidODILModel(g)
    flow = torch.tensor([.2, .2, -.1, -.3], dtype=DTYPE)
    with torch.no_grad():
        model.state['m'][0].copy_(flow)
    _solve_thermal_direct(model, g)
    old = g.cell_T_init.clone()
    capacity = g.cell_volume * g.fluid_rho * g.fluid_cp
    for t in range(1, g.N_t):
        expected = old.clone()
        forward = flow[t] > 0
        up = 310. if forward else 330.
        for c in (range(3) if forward else range(2, -1, -1)):
            expected[c] = (capacity[c] / g.dt * old[c] + abs(flow[t]) * g.fluid_cp * up) / (capacity[c] / g.dt + abs(flow[t]) * g.fluid_cp)
            up = expected[c]
        torch.testing.assert_close(model.state['T_w'][:, t], expected)
        old = expected


def test_heat_exchanger_ua_gradient_and_no_pipe_storage():
    g = loop_graph(n=2)
    # Replace pipe with an adiabatic link, leaving CP storage and HX boundary.
    g.pipe_indices = g.pipe_params = None
    g.N_cells = 0
    g.cell_volume = torch.empty(0, dtype=DTYPE)
    ua = torch.tensor([5000.], dtype=DTYPE, requires_grad=True)
    g.hx_params.UA = ua
    flow = torch.full((g.N_edges,), .3, dtype=DTYPE)
    r = step(g, flow)
    grad, = torch.autograd.grad(r.T_s.sum(), ua)
    values = []
    for offset in [-.1, .1]:
        g.hx_params.UA = ua.detach() + offset
        values.append(step(g, flow).T_s.sum())
    fd = (values[1] - values[0]) / .2
    torch.testing.assert_close(grad[0], fd, rtol=1e-5, atol=1e-9)
    assert grad.abs().sum() > 1e-8
    assert r.diagnostics['energy_balance_W'].abs() < 1e-7


def test_finite_inputs_cannot_publish_overflowed_storage_state():
    g = pipe_graph()
    previous = list(initial(g))
    previous[1].fill_(torch.finfo(DTYPE).max)
    with pytest.raises(RuntimeError, match='nonfinite'):
        step(g, torch.zeros(g.N_edges, dtype=DTYPE), previous, dt=1e-10)
