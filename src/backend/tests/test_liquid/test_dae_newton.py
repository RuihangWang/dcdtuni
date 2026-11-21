"""Independent analytic tests of the common steady/transient DAE root path."""
import importlib
import math
import pytest
import torch


def api():
    name='backend.liquidcooling.solver.dae_newton'
    assert importlib.util.find_spec(name), 'the unified differentiable DAE root solver is missing'
    return importlib.import_module(name)


def problem(manufactured=False):
    def residual(t,y,ydot,p):
        k,q=p.unbind()
        # Independently prescribed solution x*=1+.2*sin(t), z*=x*^2.
        source=.2*torch.cos(t)+k*(1+.2*torch.sin(t)) if manufactured else q
        return torch.stack((ydot[0]+k*y[0]-source,y[1]-y[0]**2))
    return api().DAEProblem(residual,torch.tensor([True,False]),torch.ones(2,dtype=torch.float64),torch.ones(2,dtype=torch.float64))


def test_consistent_initialization_keeps_storage_and_solves_algebraic_and_derivative_values():
    module=api();p=torch.tensor([2.,6.],dtype=torch.float64)
    y,dy,info=module.consistent_initialize(problem(),torch.tensor([2.,999.],dtype=torch.float64),p)
    torch.testing.assert_close(y,torch.tensor([2.,4.],dtype=torch.float64))
    torch.testing.assert_close(dy,torch.tensor([2.,0.],dtype=torch.float64))
    assert info.scaled_residual_inf<1e-9


def test_steady_transient_and_load_change_use_the_same_residual():
    module=api();prob=problem();p=torch.tensor([2.,6.],dtype=torch.float64)
    steady,_=module.solve_steady(prob,torch.tensor([1.,1.],dtype=torch.float64),p)
    torch.testing.assert_close(steady,torch.tensor([3.,9.],dtype=torch.float64))
    next_y,_=module.backward_euler_step(prob,steady,p,time=.1,dt=.1)
    torch.testing.assert_close(next_y,steady)
    changed,_=module.backward_euler_step(prob,next_y,torch.tensor([2.,8.],dtype=torch.float64),time=.2,dt=.1)
    expected=(3+.1*8)/(1+.1*2)
    assert changed[0].item()==pytest.approx(expected,abs=1e-10)
    assert changed[1].item()==pytest.approx(expected**2,abs=1e-10)


def test_backward_reaches_parameters_and_history_with_independent_discrete_analytic_gradient():
    module=api();prob=problem()
    p=torch.tensor([2.,6.],dtype=torch.float64,requires_grad=True)
    initial=torch.tensor([1.,1.],dtype=torch.float64,requires_grad=True)
    y=initial;dt=.1;steps=8
    for i in range(steps):y,_=module.backward_euler_step(prob,y,p,time=(i+1)*dt,dt=dt)
    gp,g0=torch.autograd.grad(y[0],(p,initial))
    ratio=(1+2*dt)**(-steps)
    assert gp[1].item()==pytest.approx((1-ratio)/2,rel=1e-9)
    assert g0[0].item()==pytest.approx(ratio,rel=1e-9)
    assert g0[1].item()==pytest.approx(0,abs=1e-12)


def test_smooth_mms_has_first_order_time_convergence_and_satisfies_algebraic_constraint():
    module=api();prob=problem(manufactured=True);p=torch.tensor([2.,0.],dtype=torch.float64)
    errors=[]
    for n in [10,20,40]:
        y=torch.ones(2,dtype=torch.float64)
        for i in range(n):
            y,info=module.backward_euler_step(prob,y,p,time=(i+1)/n,dt=1/n)
            assert abs((y[1]-y[0]**2).item())<1e-9
            assert info.scaled_residual_inf<1e-9
        errors.append(abs(y[0].item()-(1+.2*math.sin(1))))
    orders=[math.log(a/b,2) for a,b in zip(errors,errors[1:])]
    assert all(.9<order<1.1 for order in orders), (errors,orders)


def test_singular_root_and_invalid_timestep_fail_explicitly():
    module=api()
    singular=module.DAEProblem(lambda t,y,d,p:torch.stack([y[0]+1,y[0]+1]),torch.tensor([False,False]),torch.ones(2,dtype=torch.float64),torch.ones(2,dtype=torch.float64))
    with pytest.raises(RuntimeError,match='singular'):
        module.solve_steady(singular,torch.zeros(2,dtype=torch.float64),torch.zeros(1,dtype=torch.float64))
    with pytest.raises(ValueError,match='dt'):
        module.backward_euler_step(problem(),torch.ones(2,dtype=torch.float64),torch.tensor([2.,6.],dtype=torch.float64),time=1.,dt=0.)


def test_steady_conserved_storage_is_selected_without_inventing_a_heat_sink():
    module=api()
    neutral=module.DAEProblem(lambda t,y,d,p:torch.stack((d[0]-p[1],d[1]+y[1]-p[0])),
        torch.tensor([True,True]),torch.ones(2,dtype=torch.float64),torch.ones(2,dtype=torch.float64))
    reference=torch.tensor([17.,1.],dtype=torch.float64,requires_grad=True)
    mask=torch.tensor([True,False])
    p=torch.tensor([3.,0.],dtype=torch.float64)
    state,_=module.solve_steady(neutral,reference,p,conserved=(mask,reference))
    torch.testing.assert_close(state,torch.tensor([17.,3.],dtype=torch.float64))
    grad,=torch.autograd.grad(state[0],(reference,))
    torch.testing.assert_close(grad,torch.tensor([1.,0.],dtype=torch.float64))
    # Nonzero heat into an insulated storage has no steady state. The explicit
    # invariant must not erase this physical impossibility.
    with pytest.raises(RuntimeError,match='original DAE'):
        module.solve_steady(neutral,reference,torch.tensor([3.,1.],dtype=torch.float64),conserved=(mask,reference))
