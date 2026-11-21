"""Opt-in CPU/CUDA parity for the actual coupled physical equation graph.

The CPU helper runs in ordinary regression. CUDA execution additionally requires
DCDTUNI_RUN_CUDA_TESTS=1; default collection/execution never probes CUDA. Moving
completed CUDA outputs to CPU below is only the final comparison boundary.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
import os

import pytest
import torch

from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.solver.dae_newton import backward_euler_step, solve_steady
from backend.liquidcooling.solver.equation_graph import EquationGraph
from backend.tests.test_liquid.test_equation_graph import primary
from backend.tests.test_liquid.test_runtime import runtime_config


def _assert_resident(value, device):
    """Inspect all nested graph indices/coefficients, including sparse incidence."""
    if isinstance(value, torch.Tensor):
        assert value.device == device
        if value.is_floating_point():
            assert value.dtype == torch.float64
    elif is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_resident(getattr(value, field.name), device)
    elif isinstance(value, dict):
        for child in value.values():
            _assert_resident(child, device)
    elif isinstance(value, (tuple, list)):
        for child in value:
            _assert_resident(child, device)


def _physical_graph(device):
    # Reuse the circulating CP/HX/three-cell-pipe fixture and insert one series
    # valve. This exercises every component family and actual pipe advection.
    raw = runtime_config(samples=4).model_dump(by_alias=True, exclude_none=True)
    raw['events'] = []
    raw['edges'][-1]['params']['rpm_ratio'] = 1.
    raw['edges'][-1]['from'] = 'pump_in'
    raw['nodes'].append({'id': 'pump_in', 'type': 'junction', 'position_m': [2.1, 0., 0.]})
    raw['edges'].append({
        'id': 'series_valve', 'type': 'valve', 'from': 'return', 'to': 'pump_in',
        'params': {'D_m': .025, 'Kv_max': 1.5, 'opening': .8, 'characteristic': 'linear'},
    })
    graph = compile_graph(LiquidrunConfig.model_validate(raw), device='cpu')
    equation = EquationGraph(graph, primary(), device=device, dtype=torch.float64)
    _assert_resident(vars(equation), device)
    assert equation.graph.N_cells == 3 and equation.graph.N_edges == 5
    return equation


def _physical_trajectory(device, values=(1.2, .9, .8), *, gradients=True):
    """Independent device run: no CPU state, root, or gradient is reused on CUDA."""
    device = torch.device(device)
    eq = _physical_graph(device)
    controls = torch.tensor(values, device=device, dtype=torch.float64, requires_grad=gradients)
    base = eq.parameters()
    names = {'heat_W': 0, 'pump_rpm_ratio': 1, 'valve_opening': 2}

    def parameters(factors):
        factors = controls.new_tensor(factors)
        return torch.cat([
            base[part] * controls[names[name]] * factors[names[name]]
            if name in names else base[part]
            for name, part in eq.parameter_layout.items()
        ])

    theta = parameters((1., 1., 1.))
    state, report = solve_steady(eq.problem, eq.initial_guess(theta), theta)
    states, parameters_used, diagnostics = [state], [theta], []

    def check_root(y, rate, p, time, report):
        assert report.scaled_residual_inf < 1e-8
        residual = eq.residual(y.new_tensor(time), y, rate, p)
        assert residual.device == device
        assert float((residual / eq.residual_scale).detach().abs().max()) < 1e-8
        diag = eq.diagnostics(y.new_tensor(time), y, rate, p)
        _assert_resident(diag, device)
        assert abs(float(diag['energy_balance_W'].detach())) < 2e-5
        diagnostics.append(torch.stack([diag[name] for name in sorted(diag)]))

    check_root(state, torch.zeros_like(state), theta, 0., report)
    steady_fields = eq.unpack(state, theta)
    expected_return = theta[eq.parameter_layout['primary_inlet_K']] + (
        theta[eq.parameter_layout['heat_W']].sum()
        / (theta[eq.parameter_layout['primary_flow_kg_s']] * theta[eq.parameter_layout['primary_cp']])
    )
    torch.testing.assert_close(steady_fields['primary_return_temperature'], expected_return,
                               rtol=0., atol=1e-8)
    steady_objective = (steady_fields['primary_return_temperature'].mean()
                        + .1 * steady_fields['solid_temperature'].mean())
    # Prescribed factors produce real hydraulic and thermal changes. All valves
    # remain strictly positive, so both devices differentiate the same regime.
    dt = 5.
    for step, factors in enumerate(((1.1, .95, .9), (1.4, 1.05, .85), (1.2, .9, 1.)), 1):
        theta = parameters(factors)
        previous = state
        state, report = backward_euler_step(eq.problem, previous, theta, time=step * dt, dt=dt)
        rate = torch.where(eq.differential_mask, (state - previous) / dt, torch.zeros_like(state))
        check_root(state, rate, theta, step * dt, report)
        states.append(state)
        parameters_used.append(theta)
    final = eq.unpack(state, theta)
    objective = final['primary_return_temperature'].mean() + .1 * final['solid_temperature'].mean()
    result = {
        'states': torch.stack(states), 'parameters': torch.stack(parameters_used),
        'diagnostics': torch.stack(diagnostics),
        'objectives': torch.stack((steady_objective, objective)),
    }
    if gradients:
        steady_gradient, = torch.autograd.grad(steady_objective, controls, retain_graph=True)
        trajectory_gradient, = torch.autograd.grad(objective, controls)
        result['gradients'] = torch.stack((steady_gradient, trajectory_gradient))
        assert bool(torch.isfinite(result['gradients']).all())
        # Prevent a detached control path from passing a zero-versus-zero parity
        # comparison for any of heat, pump speed, and positive valve opening.
        assert bool((result['gradients'].abs() > 1e-7).all())
    _assert_resident(vars(eq), device)
    _assert_resident(result, device)
    assert bool((final['flow'] > 0).all())
    assert not torch.equal(result['states'][0], result['states'][-1])
    return {name: value.detach() for name, value in result.items()}


def _cuda_validation_device():
    if os.environ.get('DCDTUNI_RUN_CUDA_TESTS') != '1':
        pytest.skip('physical CUDA validation requires DCDTUNI_RUN_CUDA_TESTS=1')
    if not torch.cuda.is_available():
        pytest.skip('explicit CUDA validation requested, but no CUDA device is available')
    return torch.device('cuda:0')


def test_physical_cuda_guard_skips_without_even_probing_devices(monkeypatch):
    monkeypatch.delenv('DCDTUNI_RUN_CUDA_TESTS', raising=False)

    def forbidden_probe():
        raise AssertionError('default physical regression must not probe CUDA')

    monkeypatch.setattr(torch.cuda, 'is_available', forbidden_probe)
    with pytest.raises(pytest.skip.Exception, match='DCDTUNI_RUN_CUDA_TESTS=1'):
        _cuda_validation_device()


def test_physical_parity_helper_cpu_gradients_match_central_differences():
    actual = _physical_trajectory('cpu')
    values = [1.2, .9, .8]
    step = 1e-4
    differences = []
    for index in range(3):
        plus, minus = values.copy(), values.copy()
        plus[index] += step
        minus[index] -= step
        differences.append((
            _physical_trajectory('cpu', plus, gradients=False)['objectives']
            - _physical_trajectory('cpu', minus, gradients=False)['objectives']
        ) / (2 * step))
    torch.testing.assert_close(actual['gradients'], torch.stack(differences, dim=1),
                               rtol=2e-4, atol=2e-7)


def test_physical_equation_graph_steady_trajectory_and_gradients_match_cpu_cuda():
    device = _cuda_validation_device()  # Must precede every CUDA access.
    cpu = _physical_trajectory('cpu')
    cuda = _physical_trajectory(device)
    # Comparison-only transfer, after both complete independent root/adjoint
    # trajectories. No CPU solution feeds the CUDA residual or warm start.
    for name in ('states', 'parameters', 'objectives'):
        torch.testing.assert_close(cuda[name].cpu(), cpu[name], rtol=2e-8, atol=2e-7)
    torch.testing.assert_close(cuda['gradients'].cpu(), cpu['gradients'], rtol=2e-5, atol=2e-7)
    torch.testing.assert_close(cuda['diagnostics'].cpu(), cpu['diagnostics'], rtol=2e-7, atol=2e-5)
