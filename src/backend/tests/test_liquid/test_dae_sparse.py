"""Sparse Newton/adjoint (solver/dae_sparse.py): the same roots and gradients as dense, past 4000 unknowns."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import torch

from backend.liquidcooling.solver import dae_sparse
from backend.liquidcooling.solver.dae_newton import (
    DAEProblem, NewtonOptions, backward_euler_step, solve_steady,
)

SHINAIDE = Path(__file__).resolve().parent / "fixtures" / "shinaide_liquid.liquidrun"
SPARSE = NewtonOptions(linear_solver="sparse")


def _problem(dtype=torch.float64) -> DAEProblem:
    def residual(time, state, rate, theta):
        del time
        k, heat = theta.unbind()
        return torch.stack((rate[0] + k * state[0] - heat, state[1] - state[0].square()))
    return DAEProblem(residual=residual, differential_mask=torch.tensor([True, False]),
                      state_scale=torch.ones(2, dtype=dtype), residual_scale=torch.ones(2, dtype=dtype))


def test_sparse_steady_and_backward_euler_values_and_gradients_are_exact():
    problem = _problem()
    theta = torch.tensor([2.0, 6.0], dtype=torch.float64, requires_grad=True)
    steady, report = solve_steady(problem, torch.ones(2, dtype=torch.float64), theta, options=SPARSE)
    torch.testing.assert_close(steady, torch.tensor([3.0, 9.0], dtype=torch.float64))
    assert report.scaled_residual_inf < 1e-10
    (grad,) = torch.autograd.grad(steady[1], theta)
    torch.testing.assert_close(grad, torch.tensor([-9.0, 3.0], dtype=torch.float64), rtol=1e-9, atol=1e-10)

    previous = torch.tensor([1.0, 99.0], dtype=torch.float64, requires_grad=True)
    stepped, _ = backward_euler_step(problem, previous, theta, time=0.1, dt=0.1, options=SPARSE)
    x = (1.0 + 0.1 * 6.0) / (1.0 + 0.1 * 2.0)
    torch.testing.assert_close(stepped, torch.tensor([x, x * x], dtype=torch.float64))
    grad_theta, grad_previous = torch.autograd.grad(stepped[0], (theta, previous))
    torch.testing.assert_close(grad_theta, torch.tensor([-0.1 * x / 1.2, 0.1 / 1.2], dtype=torch.float64),
                               rtol=1e-9, atol=1e-10)
    torch.testing.assert_close(grad_previous, torch.tensor([1.0 / 1.2, 0.0], dtype=torch.float64),
                               rtol=1e-9, atol=1e-10)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="needs CUDA")
def test_sparse_on_cuda_assembles_on_the_device_and_matches_the_host():
    device = torch.device("cuda")
    problem = _problem()
    problem.differential_mask = problem.differential_mask.to(device)
    problem.state_scale = problem.state_scale.to(device)
    problem.residual_scale = problem.residual_scale.to(device)
    theta = torch.tensor([2.0, 6.0], dtype=torch.float64, device=device, requires_grad=True)
    steady, _ = solve_steady(problem, torch.ones(2, dtype=torch.float64, device=device), theta, options=SPARSE)
    assert steady.device.type == "cuda"
    (grad,) = torch.autograd.grad(steady[1], theta)
    torch.testing.assert_close(steady.cpu(), torch.tensor([3.0, 9.0], dtype=torch.float64))
    torch.testing.assert_close(grad.cpu(), torch.tensor([-9.0, 3.0], dtype=torch.float64), rtol=1e-9, atol=1e-10)


def _physical_run(device, options):
    from backend.tests.test_liquid.test_equation_graph_cuda import _physical_graph

    equation = _physical_graph(torch.device(device))
    theta = equation.parameters().clone().requires_grad_(True)
    state, _ = solve_steady(equation.problem, equation.initial_guess(theta.detach()), theta, options=options)
    for step in range(3):
        state, _ = backward_euler_step(equation.problem, state, theta, time=0.1 * (step + 1), dt=0.1,
                                       options=options)
    (grad,) = torch.autograd.grad(state.square().sum(), theta)
    return equation, state.detach().cpu(), grad.cpu()


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda:0", marks=pytest.mark.skipif(
    not torch.cuda.is_available(), reason="needs CUDA"))])
def test_sparse_matches_dense_on_the_physical_graph_through_steady_and_steps(device):
    _, dense_state, dense_grad = _physical_run("cpu", None)
    equation, state, grad = _physical_run(device, SPARSE)
    torch.testing.assert_close(state, dense_state, rtol=1e-9, atol=1e-9)
    torch.testing.assert_close(grad, dense_grad, rtol=1e-7, atol=1e-9)
    assert dae_sparse.stats(equation)  # the pattern is cached on the equation graph


def test_a_dependency_that_appears_later_is_found_and_added():
    """Upwind transport: which neighbour a cell depends on follows the sign of its flow."""
    n = 8

    def upwind(z):
        own = 2.0 * z + 0.1 * z.square()
        neighbour = torch.where(z[0] > 0, z[1], z[2])
        return torch.cat((own[:1] - neighbour, own[1:] - 0.5 * z[:-1]))

    class Owner:
        pass

    owner = Owner()
    positive = torch.linspace(1.0, 2.0, n, dtype=torch.float64)
    pattern, _ = dae_sparse.checked_values(owner, upwind, positive, "toy")
    assert (0, 2) not in set(zip(pattern.rows.tolist(), pattern.cols.tolist()))
    negative = -positive
    repaired, data = dae_sparse.checked_values(owner, upwind, negative, "toy")
    assert repaired.refreshes == pattern.refreshes + 1  # row 0 repaired by one reverse-mode product
    assert (0, 2) in set(zip(repaired.rows.tolist(), repaired.cols.tolist()))
    assembled = torch.zeros(n, n, dtype=torch.float64)
    assembled[torch.as_tensor(repaired.rows), torch.as_tensor(repaired.cols)] = data
    torch.testing.assert_close(assembled, torch.autograd.functional.jacobian(upwind, negative))


def test_small_dependencies_that_appear_later_are_all_found_in_one_check():
    """An advection term's flow derivative is zero while inlet and outlet agree, then small but nonzero."""
    n = 64

    def advection(z):
        flow, temperature = z[0], z[1:]
        inlet = torch.cat((temperature[-1:], temperature[:-1]))
        return torch.cat(((flow - 1.0).reshape(1) * 1e3, 1e3 * temperature + flow * (inlet - temperature)))

    class Owner:
        pass

    owner = Owner()
    uniform = torch.cat((torch.ones(1), torch.full((n - 1,), 300.0))).double()
    before, _ = dae_sparse.checked_values(owner, advection, uniform, "toy")
    stepped = uniform.clone()
    # inlet - outlet differences, hence the new entries: a few large (1e-2) that any
    # probe exposes, most tiny (1e-7, 1e-8) and below the acceptance gap of their row
    index = torch.arange(n - 1)
    rise = torch.where(index % 8 == 0, torch.tensor(1e-2), 10.0 ** -(7 + index % 2).double())
    stepped[1:] += torch.cumsum(rise.double(), 0)
    pattern, data = dae_sparse.checked_values(owner, advection, stepped, "toy")
    assert pattern.refreshes == before.refreshes + 1  # one repair pass found all of them
    assembled = torch.zeros(n, n, dtype=torch.float64)
    assembled[torch.as_tensor(pattern.rows), torch.as_tensor(pattern.cols)] = data
    torch.testing.assert_close(assembled, torch.autograd.functional.jacobian(advection, stepped), rtol=1e-12, atol=0)


def _refined(cells):
    raw = json.loads(SHINAIDE.read_text())
    profiles = raw["parameter_pack"].get("profiles", {}).values()
    for edge in [*raw["edges"], *profiles]:
        if edge.get("type") == "pipe" and "n_cells" in edge.get("params", {}):
            edge["params"]["n_cells"] = cells
    return raw


def test_admission_picks_sparse_above_the_dense_limit_and_refuses_explicit_dense():
    from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
    from backend.liquidcooling.liquidrun_dae_schema import DENSE_UNKNOWNS_LIMIT

    small = admit_dae_runtime_input(_refined(3))
    assert small.equation_graph.size <= DENSE_UNKNOWNS_LIMIT
    assert small.newton_options.linear_solver == "dense"  # unchanged for every saved project
    large = _refined(30)
    definition = admit_dae_runtime_input(copy.deepcopy(large))
    assert definition.equation_graph.size > DENSE_UNKNOWNS_LIMIT
    assert definition.newton_options.linear_solver == "sparse"
    large["newton"]["linear_solver"] = "dense"
    with pytest.raises(ValueError, match="dense DAE linear solver supports at most"):
        admit_dae_runtime_input(large)
