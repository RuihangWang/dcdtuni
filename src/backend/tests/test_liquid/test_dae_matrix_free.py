"""Opt-in matrix-free Newton/adjoint contract on an independently solvable DAE."""
from __future__ import annotations

import math
import pytest
import torch

from backend.liquidcooling.solver.dae_newton import (
    DAEProblem, NewtonOptions, backward_euler_step, solve_steady,
)


def _problem(dtype=torch.float64) -> DAEProblem:
    def residual(time, state, rate, theta):
        del time
        k, heat = theta.unbind()
        return torch.stack((rate[0] + k * state[0] - heat,
                            state[1] - state[0].square()))
    return DAEProblem(
        residual=residual,
        differential_mask=torch.tensor([True, False]),
        state_scale=torch.ones(2, dtype=dtype),
        residual_scale=torch.ones(2, dtype=dtype),
    )


def _matrix_free_options() -> NewtonOptions:
    return NewtonOptions(
        linear_solver="gmres",
        linear_max_iterations=16,
        linear_atol=1e-12,
        linear_rtol=1e-12,
    )


def test_matrix_free_steady_and_backward_euler_use_no_full_jacobian(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("matrix-free mode built a full Jacobian")
    monkeypatch.setattr(torch.autograd.functional, "jacobian", forbidden)
    problem = _problem()
    theta = torch.tensor([2.0, 6.0], dtype=torch.float64, requires_grad=True)
    steady, steady_report = solve_steady(
        problem, torch.ones(2, dtype=torch.float64), theta,
        options=_matrix_free_options(),
    )
    torch.testing.assert_close(steady, torch.tensor([3.0, 9.0], dtype=torch.float64))
    assert steady_report.scaled_residual_inf < 1e-10
    (steady_grad,) = torch.autograd.grad(steady[1], theta)
    torch.testing.assert_close(
        steady_grad, torch.tensor([-9.0, 3.0], dtype=torch.float64),
        rtol=1e-9, atol=1e-10,
    )

    previous = torch.tensor([1.0, 99.0], dtype=torch.float64, requires_grad=True)
    stepped, report = backward_euler_step(
        problem, previous, theta, time=0.1, dt=0.1,
        options=_matrix_free_options(),
    )
    x = (1.0 + 0.1 * 6.0) / (1.0 + 0.1 * 2.0)
    torch.testing.assert_close(stepped, torch.tensor([x, x*x], dtype=torch.float64))
    assert report.scaled_residual_inf < 1e-10
    grad_theta, grad_previous = torch.autograd.grad(
        stepped[0], (theta, previous),
    )
    torch.testing.assert_close(
        grad_theta, torch.tensor([-0.1*x/1.2, 0.1/1.2], dtype=torch.float64),
        rtol=1e-9, atol=1e-10,
    )
    torch.testing.assert_close(
        grad_previous, torch.tensor([1.0/1.2, 0.0], dtype=torch.float64),
        rtol=1e-9, atol=1e-10,
    )


def test_matrix_free_physical_cp_hx_pipe_pump_valve_matches_dense_and_conserves():
    from backend.tests.test_liquid.test_equation_graph_cuda import _physical_graph

    equation = _physical_graph(torch.device("cpu"))
    options = NewtonOptions(
        linear_solver="gmres", linear_max_iterations=equation.size,
        linear_atol=1e-10, linear_rtol=1e-10,
    )
    theta = equation.parameters()
    guess = equation.initial_guess(theta)
    dense, _ = solve_steady(equation.problem, guess, theta)
    matrix_free, steady_report = solve_steady(
        equation.problem, guess, theta, options=options,
    )
    torch.testing.assert_close(matrix_free, dense, rtol=1e-9, atol=1e-7)
    assert steady_report.scaled_residual_inf < 1e-8

    changed = equation.parameters(heat_W=torch.tensor([200.0], dtype=torch.float64))
    dense_step, _ = backward_euler_step(
        equation.problem, dense, changed, time=1.0, dt=1.0,
    )
    matrix_free_step, report = backward_euler_step(
        equation.problem, matrix_free, changed, time=1.0, dt=1.0,
        options=options,
    )
    torch.testing.assert_close(matrix_free_step, dense_step, rtol=1e-9, atol=1e-7)
    assert report.scaled_residual_inf < 1e-8
    rate = torch.where(
        equation.differential_mask, matrix_free_step - matrix_free,
        torch.zeros_like(matrix_free_step),
    )
    diagnostic = equation.diagnostics(
        matrix_free_step.new_tensor(1.0), matrix_free_step, rate, changed,
    )
    assert abs(float(diagnostic["energy_balance_W"])) < 1e-5


def test_matrix_free_physical_control_trajectory_gradient_matches_dense_finite_difference():
    from backend.tests.test_liquid.test_equation_graph_cuda import _physical_graph

    equation = _physical_graph(torch.device("cpu"))
    baseline = equation.parameters()
    options = NewtonOptions(
        linear_solver="gmres", linear_max_iterations=equation.size,
        linear_atol=1e-10, linear_rtol=1e-10,
    )
    selected = {"heat_W": 0, "pump_rpm_ratio": 1, "valve_opening": 2}

    def parameters(controls, factors):
        return torch.cat([
            baseline[part] * controls[selected[name]] * factors[selected[name]]
            if name in selected else baseline[part]
            for name, part in equation.parameter_layout.items()
        ])

    def objective(controls, *, matrix_free):
        chosen = options if matrix_free else NewtonOptions()
        first = parameters(controls, (1.0, 1.0, 1.0))
        state, _ = solve_steady(
            equation.problem, equation.initial_guess(first), first, options=chosen,
        )
        second = parameters(controls, (1.1, 0.95, 0.9))
        state, _ = backward_euler_step(
            equation.problem, state, second, time=5.0, dt=5.0, options=chosen,
        )
        fields = equation.unpack(state, second)
        return fields["primary_return_temperature"].mean() + 0.1 * fields["solid_temperature"].mean()

    controls = torch.tensor([1.2, 0.9, 0.8], dtype=torch.float64, requires_grad=True)
    loss = objective(controls, matrix_free=True)
    (gradient,) = torch.autograd.grad(loss, controls)
    assert bool((gradient.abs() > 1e-7).all())
    torch.testing.assert_close(
        loss.detach(), objective(controls.detach(), matrix_free=False),
        rtol=1e-9, atol=1e-8,
    )
    step = 1e-4
    finite_difference = []
    for axis in range(3):
        offset = torch.zeros(3, dtype=torch.float64)
        offset[axis] = step
        plus = objective(controls.detach() + offset, matrix_free=False)
        minus = objective(controls.detach() - offset, matrix_free=False)
        finite_difference.append((plus - minus) / (2*step))
    torch.testing.assert_close(
        gradient, torch.stack(finite_difference), rtol=2e-4, atol=2e-6,
    )


def test_matrix_free_coupled_cp_hx_mms_has_first_order_time_convergence():
    from backend.tests.test_liquid.test_dae_physical_mms import manufactured
    from backend.tests.test_liquid.test_equation_graph import adapter

    equation = adapter()
    options = NewtonOptions(
        linear_solver="gmres", linear_max_iterations=equation.size,
        linear_atol=1e-10, linear_rtol=1e-10,
    )
    errors = []
    for steps in (6, 12, 24):
        _, state = manufactured(equation, 0.0)
        for index in range(steps):
            time = 3.0 * (index + 1) / steps
            theta, expected = manufactured(equation, time)
            state, report = backward_euler_step(
                equation.problem, state, theta, time=time, dt=3.0/steps,
                options=options,
            )
            assert report.scaled_residual_inf < 1e-8
        errors.append(float((state[equation.differential_mask]
                             - expected[equation.differential_mask]).abs().max()))
    orders = [math.log(before/after, 2) for before, after in zip(errors, errors[1:])]
    assert all(0.85 < order < 1.15 for order in orders), (errors, orders)


def test_matrix_free_float32_uses_the_same_tensor_device_and_dtype():
    problem = _problem(torch.float32)
    options = NewtonOptions(
        linear_solver="gmres", atol=1e-6, rtol=1e-6,
        linear_max_iterations=8, linear_atol=1e-5, linear_rtol=1e-6,
    )
    state, report = solve_steady(
        problem, torch.ones(2, dtype=torch.float32),
        torch.tensor([2.0, 6.0], dtype=torch.float32), options=options,
    )
    assert state.device.type == "cpu" and state.dtype == torch.float32
    torch.testing.assert_close(
        state, torch.tensor([3.0, 9.0], dtype=torch.float32),
        rtol=1e-5, atol=1e-5,
    )
    assert report.scaled_residual_inf < 1e-5


def test_matrix_free_does_not_return_an_unconverged_linear_root():
    options = NewtonOptions(
        linear_solver="gmres", linear_max_iterations=1,
        linear_atol=1e-14, linear_rtol=0.0,
    )
    with pytest.raises(RuntimeError, match="GMRES did not converge"):
        solve_steady(
            _problem(), torch.ones(2, dtype=torch.float64),
            torch.tensor([2.0, 6.0], dtype=torch.float64),
            options=options,
        )
