"""Opt-in exact hydraulic/thermal block Newton contract."""
from __future__ import annotations

import math
import pytest
import torch

from backend.liquidcooling.solver.dae_newton import (
    DAEProblem, NewtonOptions, backward_euler_step, consistent_initialize, solve_steady,
)
from backend.tests.test_liquid.test_equation_graph import adapter


def test_physical_graph_declares_exact_hydraulic_thermal_split_and_matches_dense():
    equation = adapter()
    split = equation.state_layout["flow"].stop
    assert equation.problem.linear_partition == split
    assert 0 < split < equation.size
    options = NewtonOptions(linear_solver="block")
    theta = equation.parameters()
    guess = equation.initial_guess(theta)
    baseline, _ = solve_steady(equation.problem, guess, theta)
    state, report = solve_steady(equation.problem, guess, theta, options=options)
    torch.testing.assert_close(state, baseline, rtol=1e-10, atol=1e-8)
    assert report.scaled_residual_inf < 1e-9
    changed = equation.parameters(heat_W=torch.tensor([200.0], dtype=torch.float64))
    expected, _ = backward_euler_step(equation.problem, baseline, changed, time=1.0, dt=1.0)
    result, step_report = backward_euler_step(
        equation.problem, state, changed, time=1.0, dt=1.0, options=options,
    )
    torch.testing.assert_close(result, expected, rtol=1e-10, atol=1e-8)
    assert step_report.scaled_residual_inf < 1e-9


def test_declared_block_partition_rejects_thermal_to_hydraulic_coupling():
    def coupled(time, state, rate, theta):
        del time, rate
        return torch.stack((state[0] + state[1] - theta[0], state[1] - theta[1]))
    problem = DAEProblem(
        residual=coupled,
        differential_mask=torch.tensor([False, False]),
        state_scale=torch.ones(2, dtype=torch.float64),
        residual_scale=torch.ones(2, dtype=torch.float64),
        linear_partition=1,
    )
    options = NewtonOptions(linear_solver="block")
    with pytest.raises(ValueError, match="block lower triangular"):
        solve_steady(
            problem, torch.zeros(2, dtype=torch.float64),
            torch.tensor([2.0, 1.0], dtype=torch.float64), options=options,
        )


def test_block_five_component_control_gradient_matches_dense_finite_difference():
    from backend.tests.test_liquid.test_equation_graph_cuda import _physical_graph

    equation = _physical_graph(torch.device("cpu"))
    base = equation.parameters()
    selected = {"heat_W": 0, "pump_rpm_ratio": 1, "valve_opening": 2}
    options = NewtonOptions(linear_solver="block")

    def parameters(controls, factors):
        return torch.cat([
            base[part] * controls[selected[name]] * factors[selected[name]]
            if name in selected else base[part]
            for name, part in equation.parameter_layout.items()
        ])

    def objective(controls, chosen):
        initial_theta = parameters(controls, (1.0, 1.0, 1.0))
        state, _ = solve_steady(
            equation.problem, equation.initial_guess(initial_theta),
            initial_theta, options=chosen,
        )
        next_theta = parameters(controls, (1.1, 0.95, 0.9))
        state, _ = backward_euler_step(
            equation.problem, state, next_theta,
            time=5.0, dt=5.0, options=chosen,
        )
        fields = equation.unpack(state, next_theta)
        return fields["primary_return_temperature"].mean() + 0.1 * fields["solid_temperature"].mean()

    controls = torch.tensor([1.2, 0.9, 0.8], dtype=torch.float64, requires_grad=True)
    value = objective(controls, options)
    (gradient,) = torch.autograd.grad(value, controls)
    assert bool((gradient.abs() > 1e-7).all())
    torch.testing.assert_close(
        value.detach(), objective(controls.detach(), NewtonOptions()),
        rtol=1e-9, atol=1e-8,
    )
    step = 1e-4
    differences = []
    for axis in range(3):
        offset = torch.zeros(3, dtype=torch.float64)
        offset[axis] = step
        plus = objective(controls.detach() + offset, NewtonOptions())
        minus = objective(controls.detach() - offset, NewtonOptions())
        differences.append((plus-minus)/(2*step))
    torch.testing.assert_close(
        gradient, torch.stack(differences), rtol=2e-4, atol=2e-6,
    )


def test_block_coupled_physical_mms_has_first_order_time_convergence():
    from backend.tests.test_liquid.test_dae_physical_mms import manufactured

    equation = adapter()
    options = NewtonOptions(linear_solver="block")
    errors = []
    for steps in (6, 12, 24):
        _, state = manufactured(equation, 0.0)
        for index in range(steps):
            time = 3.0*(index+1)/steps
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


def test_block_linearization_never_builds_the_full_square_jacobian(monkeypatch):
    equation = adapter()
    full_size = equation.size
    original = torch.autograd.functional.jacobian
    observed = []

    def watch(function, state, *args, **kwargs):
        result = original(function, state, *args, **kwargs)
        observed.append((state.numel(), result.shape[0]))
        assert state.numel() < full_size
        assert result.shape[0] < full_size
        return result

    monkeypatch.setattr(torch.autograd.functional, "jacobian", watch)
    theta = equation.parameters().detach().requires_grad_(True)
    state, _ = solve_steady(
        equation.problem, equation.initial_guess(theta), theta,
        options=NewtonOptions(linear_solver="block"),
    )
    fields = equation.unpack(state, theta)
    torch.autograd.grad(fields["primary_return_temperature"].sum(), theta)
    assert observed and all(width < full_size for width, _ in observed)


def test_block_consistent_initialization_matches_dense():
    equation = adapter()
    theta = equation.parameters()
    guess = equation.initial_guess(theta)
    dense, dense_rate, _ = consistent_initialize(equation.problem, guess, theta)
    blocked, block_rate, report = consistent_initialize(
        equation.problem, guess, theta, options=NewtonOptions(linear_solver="block"),
    )
    torch.testing.assert_close(blocked, dense, rtol=1e-10, atol=1e-8)
    torch.testing.assert_close(block_rate, dense_rate, rtol=1e-10, atol=1e-8)
    assert report.scaled_residual_inf < 1e-9


def test_block_steady_preserves_insulated_blind_storage_without_a_fake_heat_sink():
    equation = adapter(blind=True)
    theta = equation.parameters()
    guess = equation.initial_guess(theta)
    mask = equation.steady_neutral_storage_mask
    assert int(mask.sum()) == 1
    dense, _ = solve_steady(
        equation.problem, guess, theta, conserved=(mask, guess),
    )
    blocked, report = solve_steady(
        equation.problem, guess, theta, conserved=(mask, guess),
        options=NewtonOptions(linear_solver="block"),
    )
    torch.testing.assert_close(blocked, dense, rtol=1e-10, atol=1e-8)
    assert report.scaled_residual_inf < 1e-9
    residual = equation.problem.residual(
        blocked.new_tensor(0.0), blocked, torch.zeros_like(blocked), theta,
    )
    assert float((residual/equation.residual_scale).abs().max()) < 1e-9
