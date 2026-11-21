"""Opt-in physical DAE GPU + torch.compile + implicit-adjoint acceptance.

Run with DCDTUNI_RUN_CUDA_TESTS=1 and one visible GPU. On hosts with only the
driver library's versioned libcuda.so.1, Triton also needs LIBRARY_PATH to
include the CUDA toolkit's stubs directory for its build-time -lcuda link.
"""
import os

import pytest
import torch

from backend.liquidcooling.solver.compile_support import compiled_problem
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.tests.test_liquid.test_equation_graph_cuda import _physical_graph


@pytest.mark.skipif(
    os.environ.get("DCDTUNI_RUN_CUDA_TESTS") != "1",
    reason="physical CUDA acceptance is opt-in",
)
def test_compiled_physical_steady_step_and_implicit_gradient_match_gpu_eager():
    if not torch.cuda.is_available():
        pytest.skip("CUDA is unavailable")
    device = torch.device("cuda:0")
    equation = _physical_graph(device)

    def run(problem):
        theta = equation.parameters().detach().clone().requires_grad_(True)
        steady, steady_report = solve_steady(
            problem, equation.initial_guess(theta), theta
        )
        stepped, step_report = backward_euler_step(
            problem, steady, theta, time=0.1, dt=0.1
        )
        fields = equation.unpack(stepped, theta)
        objective = (
            fields["primary_return_temperature"].mean()
            + 0.1 * fields["solid_temperature"].mean()
        )
        gradient = torch.autograd.grad(objective, theta)[0]
        return steady, stepped, objective, gradient, steady_report, step_report

    eager = run(equation.problem)
    compiled, router = compiled_problem(equation.problem)
    actual = run(compiled)

    for observed, expected in zip(actual[:4], eager[:4]):
        assert observed.device == device
        torch.testing.assert_close(observed, expected, rtol=0.0, atol=1e-8)
    assert actual[4].scaled_residual_inf < 1e-8
    assert actual[5].scaled_residual_inf < 1e-8
    route = router.routing_report()
    assert route["n_compiled"] > 0, route
    assert route["n_eager_fallback"] == 0, route
    assert route["broken_latch"] is False, route
