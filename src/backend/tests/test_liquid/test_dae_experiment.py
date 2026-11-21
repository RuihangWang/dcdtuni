"""Numerical mock-platform controls, using the real compiled equation graph."""
import importlib
from types import SimpleNamespace
import torch
import pytest
from backend.tests.test_liquid.test_equation_graph import adapter
from backend.liquidcooling.solver.dae_newton import NewtonOptions


def definition(mode='transient'):
    eq=adapter()
    return SimpleNamespace(equation_graph=eq,mode=mode,initialization='steady',dt=1.,
        sample_count=4 if mode=='transient' else 1,newton_options=NewtonOptions(),
        parameters=lambda index,heat_multiplier=1.:eq.parameters(heat_W=torch.tensor([100.],dtype=torch.float64)*heat_multiplier))


def session(mode='transient'):
    name='backend.liquidcooling.dae_experiment'
    assert importlib.util.find_spec(name),'the DAE numerical experiment host is missing'
    return importlib.import_module(name).DAEExperiment(definition(mode))


def test_power_commands_affect_next_step_and_cannot_rewrite_history():
    run=session();before=run.frames[0]
    run.set_heat_multiplier(2.)
    assert run.frames[0]==before
    frame=run.step()
    assert frame['time_s']==1.
    assert frame['heat_multiplier']==2.
    assert frame['primary_return_K'][0]>before['primary_return_K'][0]
    assert abs(frame['diagnostics']['energy_balance_W'])<1e-7
    assert run.applied_inputs==[{'seq':1,'time_s':1.,'heat_multiplier':2.}]
    # Caller-owned snapshots cannot mutate the experiment.
    before['primary_return_K'][0]=-999
    assert run.frames[0]['primary_return_K'][0]>0


def test_equilibrium_query_does_not_turn_a_transient_into_an_instantaneous_jump():
    run=session();before=run.tensor_state.clone()
    answer=run.equilibrium(heat_multiplier=2.)
    assert answer['kind']=='equilibrium'
    assert answer['primary_return_K'][0]==pytest.approx(290.5,abs=1e-7)
    torch.testing.assert_close(run.tensor_state,before)
    assert len(run.frames)==1
    assert session('steady').done


def test_tensor_power_input_keeps_a_gradient_through_accepted_steps():
    run=session();scale=torch.tensor(1.2,dtype=torch.float64,requires_grad=True)
    run.set_heat_multiplier(scale)
    run.step();run.step()
    target=run.equation_graph.unpack(run.tensor_state)['solid_temperature'].sum()
    gradient,=torch.autograd.grad(target,scale)
    assert bool(torch.isfinite(gradient)) and gradient.item()>0
    with pytest.raises(ValueError):run.set_heat_multiplier(-1.)


def test_tensor_only_training_path_does_not_materialize_presentation_state_arrays():
    from backend.liquidcooling.dae_experiment import DAEExperiment
    run=DAEExperiment(definition(),record_frames=False)
    scale=torch.tensor(1.2,dtype=torch.float64,requires_grad=True)
    run.set_heat_multiplier(scale)
    assert run.step() is None
    assert run.frames==[]
    values=run.tensor_series('solid_temperature')
    gradient,=torch.autograd.grad(values[:,-1].sum(),scale)
    assert gradient>0 and values.device==run.tensor_state.device


def test_accepted_input_history_and_latest_snapshot_cannot_be_mutated_by_callers():
    run=session()
    run.set_heat_multiplier(1.2)
    assert run.accepted_heat_multipliers==[1.]
    run.step()
    run.set_heat_multiplier(.8)  # remains pending
    assert run.accepted_heat_multipliers==[1.,1.2]
    latest=run.latest_frame;latest['primary_return_K'][0]=-999.
    history=run.accepted_heat_multipliers;history.append(99.)
    assert run.latest_frame['primary_return_K'][0]>0
    assert run.accepted_heat_multipliers==[1.,1.2]
