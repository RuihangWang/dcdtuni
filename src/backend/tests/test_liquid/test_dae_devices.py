"""One tensor algorithm across dtypes/devices, with no solver thread policy."""
import os
import pytest
import torch
from backend.liquidcooling.solver.dae_newton import DAEProblem,NewtonOptions,solve_steady,backward_euler_step


@pytest.mark.parametrize('dtype',[torch.float32,torch.float64])
def test_native_dtype_newton_and_discrete_adjoint_with_batched_components(dtype):
    # 32 independent cells are evaluated in one residual call, not a Python loop.
    count=32;scale=torch.ones(count,dtype=dtype)
    problem=DAEProblem(lambda t,y,d,p:d+p[0]*y-p[1],torch.ones(count,dtype=torch.bool),scale,scale)
    parameters=torch.tensor([2.,6.],dtype=dtype,requires_grad=True)
    previous=torch.ones(count,dtype=dtype,requires_grad=True)
    options=NewtonOptions(atol=2e-6,rtol=2e-6) if dtype==torch.float32 else NewtonOptions()
    steady,_=solve_steady(problem,previous,parameters,options=options)
    actual,_=backward_euler_step(problem,previous,parameters,time=.1,dt=.1,options=options)
    gp,gy=torch.autograd.grad(actual.mean(),(parameters,previous))
    assert actual.dtype==steady.dtype==dtype
    torch.testing.assert_close(steady,torch.full_like(steady,3.))
    torch.testing.assert_close(actual,torch.full_like(actual,4./3.))
    torch.testing.assert_close(gp[1],gp.new_tensor(1./12.))
    torch.testing.assert_close(gy,torch.full_like(gy,1./(1.2*count)))


def test_solver_preserves_the_callers_thread_budget():
    original=torch.get_num_threads()
    try:
        torch.set_num_threads(2)
        test_native_dtype_newton_and_discrete_adjoint_with_batched_components(torch.float64)
        assert torch.get_num_threads()==2
    finally:torch.set_num_threads(original)


@pytest.mark.skipif(os.environ.get('DCDTUNI_RUN_CUDA_TESTS')!='1',reason='CUDA execution requires explicit validation run')
def test_same_newton_and_adjoint_run_on_cuda_without_cpu_state_transfer():
    if not torch.cuda.is_available():pytest.skip('no CUDA device')
    device=torch.device('cuda');dtype=torch.float64
    scale=torch.ones(32,device=device,dtype=dtype)
    problem=DAEProblem(lambda t,y,d,p:d+p[0]*y-p[1],torch.ones(32,device=device,dtype=torch.bool),scale,scale)
    parameters=torch.tensor([2.,6.],device=device,dtype=dtype,requires_grad=True)
    previous=torch.ones(32,device=device,dtype=dtype,requires_grad=True)
    actual,_=backward_euler_step(problem,previous,parameters,time=.1,dt=.1)
    gradients=torch.autograd.grad(actual.mean(),(parameters,previous))
    assert actual.device==device or actual.device.type=='cuda'
    assert all(g.device.type=='cuda' for g in gradients)
    torch.testing.assert_close(actual,torch.full_like(actual,4./3.))
