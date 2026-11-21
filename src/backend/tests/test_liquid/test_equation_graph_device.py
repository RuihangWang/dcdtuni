"""Device/dtype propagation and measured batched residual execution (CPU only)."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest
import torch

from backend.tests.test_liquid.test_equation_graph import small_graph, primary, exact_state
from backend.liquidcooling.solver.equation_graph import EquationGraph, PrimaryCircuit
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph


@pytest.mark.parametrize('dtype',[torch.float64,torch.float32])
def test_graph_and_primary_follow_requested_dtype_with_real_residual_and_parameter_gradients(dtype):
    original=small_graph(blind=True)
    eq=EquationGraph(original,primary(),device='cpu',dtype=dtype)
    assert original.bc_P.dtype==torch.float64  # construction must not mutate source graph
    assert eq.graph.bc_P.dtype==dtype and eq.primary.cp_J_kgK.dtype==dtype
    heat=torch.tensor([100.],dtype=dtype,requires_grad=True)
    inlet=torch.tensor([290.],dtype=dtype,requires_grad=True)
    theta=eq.parameters(heat_W=heat,primary_inlet_K=inlet)
    state=exact_state(eq,theta).detach()
    state[eq.state_layout['solid_temperature']]+=1.
    state[eq.state_layout['primary_return_temperature']]+=2.
    residual=eq.residual(state.new_zeros(()),state,torch.zeros_like(state),theta)
    assert residual.dtype==dtype and residual.device==state.device==theta.device
    assert bool(torch.isfinite(residual).all())
    eq64=EquationGraph(original,primary(),dtype=torch.float64)
    reference=eq64.residual(torch.tensor(0.,dtype=torch.float64),state.double(),torch.zeros_like(state,dtype=torch.float64),eq64.parameters())
    torch.testing.assert_close(residual.double(),reference,atol=.05 if dtype==torch.float32 else 1e-9,rtol=2e-5)
    objective=residual[eq.state_layout['solid_temperature']].sum()+residual[eq.state_layout['primary_return_temperature']].sum()
    d_heat,d_inlet=torch.autograd.grad(objective,(heat,inlet))
    torch.testing.assert_close(d_heat,heat.new_tensor([-1.]))
    torch.testing.assert_close(d_inlet,inlet.new_tensor([-400.]))
    for value in (eq._from,eq._to,eq._cells,eq._free,eq._closed_edges,eq.differential_mask,eq.observable_mask(state)):
        assert value.device==theta.device
    for value in (eq.state_scale,eq.residual_scale,eq.initial_guess(theta)):
        assert value.dtype==dtype and value.device==theta.device


def test_primary_to_preserves_outer_gradient_and_default_constructor_infers_graph_dtype():
    flow=torch.tensor([.1],dtype=torch.float64,requires_grad=True)
    source=replace(primary(),flow_kg_s=flow)
    converted=source.to(device='cpu',dtype=torch.float32)
    assert converted.source==source.source and source.flow_kg_s.dtype==torch.float64
    eq=EquationGraph(small_graph().to('cpu',torch.float32),converted)
    theta=eq.parameters()
    assert theta.dtype==torch.float32
    theta[eq.parameter_layout['primary_flow_kg_s']].sum().backward()
    torch.testing.assert_close(flow.grad,torch.ones_like(flow))
    with pytest.raises(ValueError,match='dtype'):
        eq.residual(torch.tensor(0.),eq.initial_guess(theta).double(),torch.zeros(eq.size),theta)


@pytest.mark.parametrize('dtype',[torch.float64,torch.float32])
def test_physical_steady_and_implicit_gradient_follow_dtype_with_reachable_tolerances(dtype):
    from backend.liquidcooling.solver.dae_newton import solve_steady,NewtonOptions
    eq=EquationGraph(small_graph(),primary(),device='cpu',dtype=dtype)
    heat=torch.tensor([100.],dtype=dtype,requires_grad=True)
    theta=eq.parameters(heat_W=heat)
    options=NewtonOptions(atol=5e-6,rtol=5e-6) if dtype==torch.float32 else NewtonOptions()
    state,report=solve_steady(eq.problem,eq.initial_guess(theta),theta,options=options)
    returned=eq.unpack(state,theta)['primary_return_temperature'][0]
    assert state.dtype==dtype
    assert returned.item()==pytest.approx(290.25,abs=2e-4 if dtype==torch.float32 else 1e-8)
    gradient=torch.autograd.grad(returned,heat)[0]
    torch.testing.assert_close(gradient,heat.new_tensor([1/400]),rtol=2e-4 if dtype==torch.float32 else 1e-8,atol=1e-8)
    assert report.scaled_residual_inf < (1e-5 if dtype==torch.float32 else 1e-9)


def pipe_batch(count:int,cells:int=4):
    nodes=[];edges=[]
    for index in range(count):
        nodes += [{'id':f'in{index}','type':'pressure_bc','position_m':[0,0,index], 'bc':{'P_Pa':150001.,'T_K':295.}},
                  {'id':f'out{index}','type':'pressure_bc','position_m':[1,0,index],'bc':{'P_Pa':150000.}}]
        edges.append({'id':f'pipe{index}','type':'pipe','from':f'in{index}','to':f'out{index}',
            'params':{'D_m':.025,'n_cells':cells,'waypoints_m':[[0,0,index],[1,0,index]]}})
    raw={'version':'1.0','source':'vectorization-test','projectName':'batched-pipes','gravity':[0,-9.81,0],
         'fluid':{'rho_kg_m3':1000.,'cp_J_kgK':4000.,'mu_Pa_s':.001,'k_W_mK':.6},
         'time':{'dt_s':1.,'N_t':2,'bdf_order':1},'nodes':nodes,'edges':edges}
    graph=compile_graph(LiquidrunConfig.model_validate(raw))
    empty=torch.empty(0,dtype=torch.float64)
    return EquationGraph(graph,PrimaryCircuit(empty,empty,empty,empty,empty,empty,'no HX; batching verification'))


def residual_profile(copies):
    eq=pipe_batch(copies)
    theta=eq.parameters();state=eq.initial_guess(theta);rate=torch.zeros_like(state)
    eq.validate_state(state,theta)
    eq.residual(state.new_zeros(()),state,rate,theta)  # unmeasured warmup
    with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU],record_shapes=True) as trace:
        eq.residual(state.new_zeros(()),state,rate,theta)
    events={entry.key:entry.count for entry in trace.key_averages() if entry.key.startswith('aten::')}
    forbidden={key:events.get(key,0) for key in ('aten::_local_scalar_dense','aten::item','aten::equal','aten::is_nonzero')}
    return {'edges':copies,'cells':4*copies,'device':str(state.device),'dtype':str(state.dtype),
            'aten_calls':sum(events.values()),'scalar_readbacks':forbidden,
            'index_add_calls':events.get('aten::index_add',0),'all_operator_counts':events}


def test_profiler_has_no_host_scalar_readbacks_and_operator_calls_do_not_scale_with_component_count():
    assert hasattr(EquationGraph,'validate_state'), 'admission checks must be outside the differentiable residual'
    reports=[residual_profile(1),residual_profile(32)]
    assert all(not any(report['scalar_readbacks'].values()) for report in reports),reports
    assert reports[0]['all_operator_counts']==reports[1]['all_operator_counts'],reports
    print('EQUATION_GRAPH_PROFILE '+json.dumps(reports))
    if os.environ.get('EQUATION_GRAPH_PROFILE_OUTPUT'):
        Path(os.environ['EQUATION_GRAPH_PROFILE_OUTPUT']).write_text(json.dumps({'activities':['CPU'],'residual_profiles':reports},indent=2)+'\n')
