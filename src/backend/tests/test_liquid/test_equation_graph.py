"""Independent physical balances for the compiled-network DAE adapter."""
from __future__ import annotations

import importlib.util
import math

import pytest
import torch

from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.schema import LiquidrunConfig


def api():
    name = 'backend.liquidcooling.solver.equation_graph'
    assert importlib.util.find_spec(name), 'CompiledGraph requires the acausal equation adapter'
    return __import__(name, fromlist=['EquationGraph'])


def small_graph(*, blind=False, isolated=False, closed=False):
    nodes = [
        {'id':'supply','type':'pressure_bc','position_m':[0,0,0],'bc':{'P_Pa':150000.}},
        {'id':'hot','type':'junction','position_m':[1,0,0]},
        {'id':'pump_in','type':'junction','position_m':[2,0,0]},
    ]
    edges = [
        {'id':'cp','type':'cold_plate','from':'supply','to':'hot',
         'params':{'dp_a':0.,'dp_b':1000.,'UA_W_per_K':100.,'C_solid_J_per_K':1000.,'V_fluid_m3':1e-5,'Q_W':100.}},
        {'id':'hx','type':'heat_exchanger','from':'hot','to':'pump_in',
         'params':{'dp_a':0.,'dp_b':2000.,'UA_W_per_K':500.,'T_secondary_K':290.}},
        {'id':'pump','type':'pump','from':'pump_in','to':'supply',
         'params':{'H0_m':600./9810.,'a_coef':0.,'rpm_ratio':1.}},
    ]
    if blind:
        nodes.append({'id':'dead','type':'junction','position_m':[1,0,1]})
        edges.append({'id':'blind_pipe','type':'pipe','from':'hot','to':'dead',
                      'params':{'D_m':.01,'n_cells':1,'waypoints_m':[[1,0,0],[1,0,1]]}})
    if isolated:
        nodes += [{'id':'float_a','type':'junction','position_m':[3,0,0]},
                  {'id':'float_b','type':'junction','position_m':[4,0,0]}]
        edges.append({'id':'floating_pipe','type':'pipe','from':'float_a','to':'float_b',
                      'params':{'D_m':.01,'n_cells':1,'waypoints_m':[[3,0,0],[4,0,0]]}})
    if closed:
        edges.append({'id':'closed_valve','type':'valve','from':'supply','to':'hot',
                      'params':{'D_m':.01,'Kv_max':1.5,'opening':[0.,1.,0.],'characteristic':'linear'}})
    raw = {'version':'1.0','source':'independent-unit-test','projectName':'DAEBalance',
           'gravity':[0,-9.81,0],
           'fluid':{'rho_kg_m3':1000.,'cp_J_kgK':4000.,'mu_Pa_s':.001,'k_W_mK':.6},
           'time':{'dt_s':1.,'N_t':3,'bdf_order':1},'nodes':nodes,'edges':edges,
           'initial_conditions':{'T_fluid_K':300.,'T_solid_K':310.,'P_uniform_Pa':150000.}}
    return compile_graph(LiquidrunConfig.model_validate(raw), device='cpu')


def primary():
    t = lambda value: torch.tensor([value], dtype=torch.float64)
    return api().PrimaryCircuit(flow_kg_s=t(.1), inlet_K=t(290.), cp_J_kgK=t(4000.),
        rho_kg_m3=t(1000.), primary_volume_m3=t(.002), secondary_volume_m3=t(.001),
        source='mock:independently specified conservation fixture')


def adapter(*, blind=False):
    return api().EquationGraph(small_graph(blind=blind), primary())


def exact_state(eq, theta, *, secondary=290.45, primary_return=290.25, solid=None):
    # R_cp*m=200 Pa; R_hx*m=400 Pa; pump head=600 Pa at m=.2 kg/s.
    state = eq.initial_guess(theta)
    conductance = .2 * 4000 * (1 - math.exp(-100 / (.2 * 4000)))
    solid = secondary + 100 / conductance if solid is None else solid
    hot = secondary + conductance * (solid - secondary) / (.2 * 4000)
    layout = eq.state_layout
    state[layout['pressure_free']] = state.new_tensor([149800.,149400.] + ([149800.] if eq.graph.N_nodes == 4 else []))
    state[layout['flow']] = state.new_tensor([.2,.2,.2] + ([0.] if eq.graph.N_edges == 4 else []))
    state[layout['node_temperature']] = state.new_tensor([secondary,hot,secondary] + ([300.] if eq.graph.N_nodes == 4 else []))
    state[layout['solid_temperature']] = solid
    state[layout['hx_secondary_temperature']] = secondary
    state[layout['primary_return_temperature']] = primary_return
    return state


def test_closed_loop_steady_residual_predicts_primary_return_from_heat_balance():
    eq = adapter()
    theta = eq.parameters()
    state = exact_state(eq, theta)
    residual = eq.problem.residual(torch.tensor(0.), state, torch.zeros_like(state), theta)
    torch.testing.assert_close(residual, torch.zeros_like(residual), rtol=0, atol=1e-8)
    unpacked = eq.unpack(state, theta)
    assert unpacked['primary_return_temperature'].item() == pytest.approx(290 + 100 / (.1 * 4000))
    assert unpacked['hx_secondary_temperature'].item() == pytest.approx(290.25 + 100 / 500)
    assert int(eq.problem.differential_mask.sum()) == 3  # CP solid + two HX volumes


def test_global_storage_balance_cancels_internal_cp_and_hx_heat_exchange():
    eq = adapter()
    theta = eq.parameters()
    state = exact_state(eq, theta, secondary=305., primary_return=295., solid=310.)
    derivative = torch.zeros_like(state)
    derivative[eq.state_layout['solid_temperature']] = .01
    derivative[eq.state_layout['hx_secondary_temperature']] = .02
    derivative[eq.state_layout['primary_return_temperature']] = -.03
    residual = eq.problem.residual(torch.tensor(0.),state,derivative,theta)
    storage_rows = eq.problem.differential_mask
    # CP:1000 J/K; HX secondary:4000 J/K; HX primary:8000 J/K.
    expected = 1000*.01 + 4000*.02 - 8000*.03 - 100 + (.1*4000)*(295-290)
    assert residual[storage_rows].sum().item() == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize('time', [0., .3, 2.])
def test_analytic_manufactured_forcing_satisfies_continuous_network_residual(time):
    eq = adapter()
    w = .05
    secondary = 300 + 2*math.sin(w*time)
    primary_return = 290 + math.cos(w*time)
    ds, dds = 2*w*math.cos(w*time), -2*w*w*math.sin(w*time)
    dp = -w*math.sin(w*time)
    q_cp = 4000*ds + 500*(secondary-primary_return)
    conductance = .2*4000*(1-math.exp(-100/(.2*4000)))
    solid = secondary + q_cp/conductance
    dsolid = ds + (4000*dds + 500*(ds-dp))/conductance
    q_it = 1000*dsolid + q_cp
    inlet = primary_return + (8000*dp - 500*(secondary-primary_return))/(.1*4000)
    theta = eq.parameters(heat_W=torch.tensor([q_it], dtype=torch.float64),
                          primary_inlet_K=torch.tensor([inlet], dtype=torch.float64))
    state = exact_state(eq,theta,secondary=secondary,primary_return=primary_return,solid=solid)
    derivative = torch.zeros_like(state)
    derivative[eq.state_layout['solid_temperature']] = dsolid
    derivative[eq.state_layout['hx_secondary_temperature']] = ds
    derivative[eq.state_layout['primary_return_temperature']] = dp
    residual = eq.problem.residual(torch.tensor(time),state,derivative,theta)
    torch.testing.assert_close(residual,torch.zeros_like(residual),rtol=0,atol=2e-8)


def test_blind_end_keeps_thermal_storage_but_marks_dry_node_temperature_unobservable():
    eq = adapter(blind=True)
    theta = eq.parameters()
    state = exact_state(eq,theta)
    state[eq.state_layout['water_temperature']] = 330.
    residual = eq.problem.residual(torch.tensor(0.),state,torch.zeros_like(state),theta)
    torch.testing.assert_close(residual,torch.zeros_like(residual),rtol=0,atol=1e-8)
    assert int(eq.problem.differential_mask.sum()) == 4
    assert int(eq.steady_neutral_storage_mask.sum()) == 1
    assert eq.steady_neutral_storage_mask[eq.state_layout['water_temperature']].tolist() == [True]
    assert eq.unpack(state,theta)['node_temperature_observable'].tolist() == [True,True,True,False]


def test_disconnected_component_without_pressure_reference_is_explicitly_rejected():
    with pytest.raises(ValueError, match='pressure reference'):
        api().EquationGraph(small_graph(isolated=True),primary())


def test_theta_carries_heat_and_primary_boundary_gradients_without_hidden_closure_inputs():
    eq = adapter()
    heat = torch.tensor([100.],dtype=torch.float64,requires_grad=True)
    inlet = torch.tensor([290.],dtype=torch.float64,requires_grad=True)
    theta = eq.parameters(heat_W=heat,primary_inlet_K=inlet)
    state = exact_state(eq,theta).detach()
    residual = eq.problem.residual(torch.tensor(0.),state,torch.zeros_like(state),theta)
    selected = residual[eq.state_layout['solid_temperature']].sum() + residual[eq.state_layout['primary_return_temperature']].sum()
    grad_heat, grad_inlet = torch.autograd.grad(selected,(heat,inlet))
    torch.testing.assert_close(grad_heat,torch.tensor([-1.],dtype=torch.float64))
    torch.testing.assert_close(grad_inlet,torch.tensor([-400.],dtype=torch.float64))


def test_actual_newton_steady_primary_return_and_load_step_conserve_stored_energy():
    from backend.liquidcooling.solver.dae_newton import solve_steady, backward_euler_step
    eq = adapter()
    theta = eq.parameters()
    steady,report = solve_steady(eq.problem,eq.initial_guess(theta),theta)
    assert report.scaled_residual_inf < 1e-9
    torch.testing.assert_close(steady,exact_state(eq,theta),rtol=1e-9,atol=1e-7)
    unchanged,_ = backward_euler_step(eq.problem,steady,theta,time=1.,dt=1.)
    torch.testing.assert_close(unchanged,steady,rtol=1e-9,atol=1e-8)
    changed = eq.parameters(heat_W=torch.tensor([200.],dtype=torch.float64))
    advanced,report = backward_euler_step(eq.problem,steady,changed,time=1.,dt=1.)
    before,after = eq.unpack(steady,theta),eq.unpack(advanced,changed)
    stored = (1000*(after['solid_temperature']-before['solid_temperature'])
              +4000*(after['hx_secondary_temperature']-before['hx_secondary_temperature'])
              +8000*(after['primary_return_temperature']-before['primary_return_temperature'])).sum()
    rejected = (.1*4000*(after['primary_return_temperature']-290)).sum()
    assert (stored + rejected).item() == pytest.approx(200.,abs=1e-6)
    assert after['solid_temperature'].item() > before['solid_temperature'].item()
    assert after['primary_return_temperature'].item() > before['primary_return_temperature'].item()
    assert report.scaled_residual_inf < 1e-9


def test_actual_steady_adjoint_matches_primary_heat_balance_analytic_sensitivities():
    from backend.liquidcooling.solver.dae_newton import solve_steady
    eq = adapter()
    heat = torch.tensor([100.],dtype=torch.float64,requires_grad=True)
    inlet = torch.tensor([290.],dtype=torch.float64,requires_grad=True)
    theta = eq.parameters(heat_W=heat,primary_inlet_K=inlet)
    steady,_ = solve_steady(eq.problem,eq.initial_guess(theta),theta)
    objective = eq.unpack(steady)['primary_return_temperature'].sum()
    grad_heat,grad_inlet = torch.autograd.grad(objective,(heat,inlet))
    torch.testing.assert_close(grad_heat,torch.tensor([1/(.1*4000)],dtype=torch.float64),rtol=1e-8,atol=1e-10)
    torch.testing.assert_close(grad_inlet,torch.ones(1,dtype=torch.float64),rtol=1e-8,atol=1e-10)


def test_fixed_closed_valve_enforces_zero_flow_without_constraining_its_pressure_drop():
    from backend.liquidcooling.solver.dae_newton import solve_steady
    eq = api().EquationGraph(small_graph(closed=True),primary())
    theta = eq.parameters(0)
    state,report = solve_steady(eq.problem,eq.initial_guess(theta),theta)
    values = eq.unpack(state,theta)
    assert abs(values['flow'][-1].item()) < 1e-12
    assert (values['pressure'][0]-values['pressure'][1]).item() == pytest.approx(200.,abs=1e-6)
    assert report.scaled_residual_inf < 1e-9
    with pytest.raises(ValueError,match='active-set'):
        eq.parameters(1)
    changed = theta.clone()
    changed[eq.parameter_layout['valve_opening']] = 1.
    with pytest.raises(ValueError,match='active-set'):
        eq.validate_parameters(changed)


def test_pressure_only_external_source_does_not_invent_an_inlet_temperature():
    graph = small_graph()
    graph.bc_mask[2] = True
    graph.bc_P[2] = 149400.
    eq = api().EquationGraph(graph,primary())
    theta = eq.parameters()
    state = eq.initial_guess(theta)
    state[eq.state_layout['flow']] = state.new_tensor([.2,.2,0.])
    with pytest.raises(ValueError,match='inlet temperature'):
        eq.validate_state(state,theta)


def test_pressure_boundary_with_existing_inflow_cannot_supply_undeclared_extra_fluid():
    from backend.liquidcooling.solver.dae_newton import solve_steady
    graph=small_graph()
    graph.bc_mask[1]=True
    graph.bc_P[1]=149900.
    graph.pump_params.H0[:]=500./9810.
    # Independent hydraulic root: CP=.1, HX=pump=.2 kg/s. The hot pressure
    # boundary therefore supplies .1 kg/s in addition to the arriving CP flow.
    # Without its reservoir temperature, mixing cannot be determined.
    eq=api().EquationGraph(graph,primary());theta=eq.parameters()
    with pytest.raises(ValueError,match='inlet temperature'):
        solve_steady(eq.problem,eq.initial_guess(theta),theta)
    # Declaring that source's temperature makes it a valid open system.
    graph.bc_T_mask[1]=True;graph.bc_T[1]=291.15
    known=api().EquationGraph(graph,primary());parameters=known.parameters()
    state,report=solve_steady(known.problem,known.initial_guess(parameters),parameters)
    assert report.scaled_residual_inf<1e-9
    diagnostics=known.diagnostics(state.new_zeros(()),state,torch.zeros_like(state),parameters)
    assert abs(diagnostics['energy_balance_W'].item())<1e-7


def test_closed_loop_pressure_datum_does_not_treat_flow_roundoff_as_external_source():
    eq=adapter();theta=eq.parameters();state=exact_state(eq,theta)
    state[eq.state_layout['flow']][0]+=1e-13
    eq.validate_state(state,theta)
