import copy
import json
import math
from pathlib import Path

import pytest
import torch

from backend.liquidcooling.control import PIDConfig, initial_pid_state, pid_step
from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.graphcompiling.time_slice import time_slice
from backend.liquidcooling.runtime import LiquidSimulation
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step
from backend.liquidcooling.solver.rollout import differentiable_rollout

DTYPE = torch.float64


def loop_config(n=7, valve=False):
    raw = json.loads(Path(__file__).with_name('cdu_loop.liquidrun').read_text())
    raw['time'].update(N_t=n, dt_s=1.)
    raw['nodes'][0]['bc'].pop('T_K')
    raw['nodes'][-1] = {'id':'return', 'type':'junction', 'position_m':[2,0,0]}
    raw['edges'].append({'id':'pump', 'type':'pump', 'from':'return', 'to':'supply',
                         'params':{'H0_m':10., 'a_coef':5e5, 'rpm_ratio':.82}})
    if valve:
        raw['nodes'].append({'id':'after-valve','type':'junction','position_m':[.1,0,0]})
        raw['edges'][0]['from'] = 'after-valve'
        raw['edges'].append({'id':'valve','type':'valve','from':'supply','to':'after-valve',
                             'params':{'Kv_max':1.5,'opening':.8}})
    return LiquidrunConfig.model_validate(raw)


def test_tensor_rollout_matches_actual_session_and_does_not_mutate_graph():
    cfg = loop_config(valve=True)
    g = compile_graph(cfg)
    before = copy.deepcopy(g)
    trajectory = differentiable_rollout(g)
    sim = LiquidSimulation(cfg)
    frames = [sim.latest_frame]
    while not sim.done:
        frames.append(sim.step())
    for name, wire in [('pressure','node_pressure_Pa'), ('flow','edge_flow_kg_s'),
                       ('node_temperature','node_temperature_K'), ('solid_temperature','chip_temp_K'),
                       ('edge_outlet','edge_outlet_temp_K')]:
        expected = torch.tensor([f[wire] for f in frames], dtype=DTYPE).T
        torch.testing.assert_close(getattr(trajectory,name), expected, rtol=1e-8, atol=1e-6)
    torch.testing.assert_close(g.pump_params.rpm_ratio, before.pump_params.rpm_ratio, rtol=0, atol=0)
    torch.testing.assert_close(g.cp_params.Q_W, before.cp_params.Q_W, rtol=0, atol=0)
    assert g.N_t == cfg.time.N_t
    # Solved flow has finite Newton continuity tolerance, unlike a prescribed
    # exactly balanced flow. Scale the energy criterion to the 500 W input.
    assert max(float(d['energy_balance_W'].abs()) for d in trajectory.diagnostics) < 1e-8 * 500.


@pytest.mark.parametrize('parameter,value,h',[
    ('pump',.82,1e-5), ('valve',.8,1e-5), ('heat',500.,.01),
    ('ua',150.,.01), ('diameter',.025,1e-6), ('pressure',200000.,1.),
])
def test_multistep_parameter_gradients_match_finite_difference(parameter,value,h):
    def objective(x):
        g = compile_graph(loop_config(valve=True))
        kw = {}
        if parameter=='pump': kw['pump_rpm_ratio']=x.reshape(1)
        elif parameter=='valve': kw['valve_opening']=x.reshape(1)
        elif parameter=='heat': kw['heat_W']=x.reshape(1)
        elif parameter=='ua': g.cp_params.UA=x.reshape(1)
        elif parameter=='diameter':
            ratio=x/g.pipe_params.D[0]
            g.pipe_params.D=g.pipe_params.D*ratio
            g.pipe_params.A=math.pi/4*g.pipe_params.D.square()
            g.cell_volume=g.cell_volume*ratio.square()
        elif parameter=='pressure':
            g.bc_P=torch.where(g.bc_mask,x,g.bc_P)
        result=differentiable_rollout(g,**kw)
        return result.solid_temperature[0,-1] + .2*result.flow[0,-1] + 1e-5*result.pressure[:,-1].mean()
    x=torch.tensor(value,dtype=DTYPE,requires_grad=True)
    actual=torch.autograd.grad(objective(x),x)[0]
    expected=(objective(x.detach()+h)-objective(x.detach()-h))/(2*h)
    assert torch.isfinite(actual)
    assert actual.abs()>1e-8
    torch.testing.assert_close(actual,expected,rtol=3e-4,atol=1e-7)


def test_schedule_sensitivity_is_causal_and_initial_heat_is_not_integrated():
    g=compile_graph(loop_config())
    heat=torch.full((1,g.N_t),500.,dtype=DTYPE,requires_grad=True)
    result=differentiable_rollout(g,heat_W=heat)
    gradient=torch.autograd.grad(result.solid_temperature[0,3],heat)[0]
    assert gradient[0,0]==0
    assert torch.all(gradient[0,1:4]>0)
    assert torch.all(gradient[0,4:]==0)


def test_closed_loop_pid_gain_gradient_crosses_measurement_and_plant_steps():
    config=PIDConfig(kp=.003,ki=.0002,setpoint=330.,bias=.8,action='direct',output_min=.2,output_max=1.)
    def objective(kp):
        g=compile_graph(loop_config(n=9))
        gains=torch.stack([kp,kp.new_tensor(.0002),kp.new_zeros(())])
        def controller(index,previous,state):
            result=pid_step(config,state,previous.solid_temperature[0],g.dt,gains=gains)
            return {'pump_rpm_ratio':result.output.reshape(1)},result.state
        result=differentiable_rollout(g,controller=controller,controller_state=initial_pid_state(config))
        return result.solid_temperature[0,-1]
    kp=torch.tensor(.003,dtype=DTYPE,requires_grad=True)
    gradient=torch.autograd.grad(objective(kp),kp)[0]
    h=1e-6
    expected=(objective(kp.detach()+h)-objective(kp.detach()-h))/(2*h)
    assert gradient.abs()>1e-6
    torch.testing.assert_close(gradient,expected,rtol=3e-4,atol=1e-6)


def test_pressure_reference_gradient_reaches_dead_end_and_closed_valve_has_zero_sensitivity():
    raw=json.loads(Path(__file__).with_name('minimal.liquidrun').read_text())
    raw['time']['N_t']=1
    raw['nodes'].append({'id':'dead','type':'junction','position_m':[0,0,0]})
    raw['edges'].extend([
      {'id':'dead-edge','type':'cold_plate','from':'supply','to':'dead',
       'params':{'dp_a':500.,'dp_b':50.,'UA_W_per_K':150.,'C_solid_J_per_K':50.,'Q_W':0}},
      {'id':'closed','type':'valve','from':'supply','to':'return','params':{'Kv_max':1.5,'opening':0.}},
    ])
    g=compile_graph(LiquidrunConfig.model_validate(raw))
    p=torch.tensor(200000.,dtype=DTYPE,requires_grad=True)
    opening=torch.zeros(1,1,dtype=DTYPE,requires_grad=True)
    g.bc_P=torch.stack([p,p.new_tensor(100000.),p.new_zeros(())])
    g.valve_params.opening=opening
    pressure,flow=hydraulic_step(g)
    dp,do=torch.autograd.grad(pressure[-1]+flow[-1],(p,opening))
    torch.testing.assert_close(dp,torch.ones_like(dp))
    assert flow[-1]==0 and do.abs().max()==0


def test_time_slice_owns_controls_even_for_constant_vector_schedule():
    g=compile_graph(loop_config())
    g.pump_params.rpm_ratio=g.pump_params.rpm_ratio[:,0]
    selected=time_slice(g,2)
    selected.pump_params.rpm_ratio[0]=.1
    assert g.pump_params.rpm_ratio[0]==.82
    with pytest.raises(ValueError,match='index'): time_slice(g,True)
    with pytest.raises(ValueError,match='shape'): differentiable_rollout(g,pump_rpm_ratio=torch.ones(2,7))

@pytest.mark.parametrize('target', ['dt', 'initial'])
def test_time_step_and_initial_state_gradients(target):
    def objective(x):
        g=compile_graph(loop_config(n=5))
        kw={'dt':x} if target=='dt' else {'initial_state':(
            torch.full((g.N_nodes,),g.T_fluid_init,dtype=DTYPE),
            torch.full((g.N_cells,),g.T_fluid_init,dtype=DTYPE),x.expand(g.N_cp))}
        return differentiable_rollout(g,**kw).solid_temperature.sum()
    x=torch.tensor(1. if target=='dt' else 340.,dtype=DTYPE,requires_grad=True)
    h=1e-4
    gradient=torch.autograd.grad(objective(x),x)[0]
    expected=(objective(x.detach()+h)-objective(x.detach()-h))/(2*h)
    torch.testing.assert_close(gradient,expected,rtol=3e-4,atol=1e-6)


def test_forward_nonconvergence_is_not_given_an_implicit_gradient():
    g=time_slice(compile_graph(loop_config()),0)
    g.pump_params.rpm_ratio.requires_grad_(True)
    with pytest.raises(RuntimeError,match='forward failed'):
        hydraulic_step(g,iterations=0)

@pytest.mark.parametrize('reverse', [False, True])
def test_boundary_pressure_driven_flow_gradient_in_both_directions(reverse):
    raw=json.loads(Path(__file__).with_name('minimal.liquidrun').read_text())
    raw['time']['N_t']=1
    def objective(x):
        g=compile_graph(LiquidrunConfig.model_validate(raw))
        g.bc_P=torch.stack([x,x.new_tensor(200000. if reverse else 100000.)])
        return hydraulic_step(g)[1][0]
    x=torch.tensor(100000. if reverse else 200000.,dtype=DTYPE,requires_grad=True)
    gradient=torch.autograd.grad(objective(x),x)[0]
    expected=(objective(x.detach()+1.)-objective(x.detach()-1.))/2
    assert gradient>0
    torch.testing.assert_close(gradient,expected,rtol=1e-5,atol=1e-10)


def test_nonconstant_pump_schedule_gradient_and_forward_time_alignment():
    cfg=loop_config(n=5)
    schedule=torch.tensor([[.9,.7,.8,.75,.85]],dtype=DTYPE,requires_grad=True)
    cfg.edges[-1].params['rpm_ratio']=schedule.detach().flatten().tolist()
    g=compile_graph(cfg)
    def objective(value):return differentiable_rollout(g,pump_rpm_ratio=value).solid_temperature[0,-1]
    gradient=torch.autograd.grad(objective(schedule),schedule)[0]
    for column in range(5):
        shift=torch.zeros_like(schedule);shift[0,column]=1e-5
        expected=(objective(schedule.detach()+shift)-objective(schedule.detach()-shift))/2e-5
        torch.testing.assert_close(gradient[0,column],expected,rtol=3e-4,atol=1e-7)
    sim=LiquidSimulation(cfg);flows=[sim.latest_frame['edge_flow_kg_s']]
    while not sim.done:flows.append(sim.step()['edge_flow_kg_s'])
    result=differentiable_rollout(g,pump_rpm_ratio=schedule)
    torch.testing.assert_close(result.flow,torch.tensor(flows,dtype=DTYPE).T,rtol=1e-8,atol=1e-8)


def test_gradient_based_parameter_fit_reduces_mock_trajectory_error():
    g=compile_graph(loop_config(n=6))
    target=differentiable_rollout(g,pump_rpm_ratio=torch.tensor([.82],dtype=DTYPE)).solid_temperature[0,-1].detach()
    theta=torch.tensor(-1.,dtype=DTYPE,requires_grad=True)
    optimizer=torch.optim.Adam([theta],lr=.3)
    def loss():
        speed=.2+.8*theta.sigmoid()
        result=differentiable_rollout(g,pump_rpm_ratio=speed.reshape(1))
        return (result.solid_temperature[0,-1]-target).square()
    before=float(loss().detach())
    for _ in range(12):
        optimizer.zero_grad();objective=loss();objective.backward();optimizer.step()
    after=float(loss().detach())
    assert after < before*.25
