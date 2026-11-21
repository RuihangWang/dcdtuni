"""The v4 endpoint forms retain gradients through an accepted Newton step."""
import pytest
import torch

from backend.liquidcooling.solver.dae_newton import solve_steady, backward_euler_step
from backend.tests.test_liquid.test_dae_v4_forms import equation, one, balance


@pytest.mark.parametrize('form,field,value', [
    ('pump_flow', 'pump_flow_set', .2),
    ('temperature_rise', 'cp_dT_set', .125),
    ('secondary_setpoint', 'hx_T_set', 295.),
    ('pressure_boundary', 'pressure_bc', 150000.),
])
def test_v4_endpoint_implicit_gradient_matches_centered_difference(form, field, value):
    shape = (1, 3)
    fields = {}
    if form == 'pump_flow':
        fields = {'pump_flow_mode': torch.tensor([True]), 'pump_flow_set': torch.full(shape, value, dtype=torch.float64)}
    elif form == 'temperature_rise':
        fields = {'cp_rise_mode': torch.tensor([True]), 'cp_dT_set': torch.full(shape, value, dtype=torch.float64)}
    elif form == 'secondary_setpoint':
        fields = {'hx_setpoint_mode': torch.tensor([True]), 'hx_T_set': torch.full(shape, value, dtype=torch.float64)}
    eq = equation(**fields)
    base = eq.parameters(heat_W=one(100.))
    initial, info = solve_steady(eq.problem, eq.initial_guess(base), base)
    assert info.scaled_residual_inf < 1e-8
    initial = initial.detach()
    sl = eq.parameter_layout[field]
    dt = 1.

    def objective(parameter):
        if field == 'pressure_bc':
            part = base[sl] + eq.graph.bc_mask.to(torch.float64) * (parameter - value)
        else:
            part = parameter.reshape(1)
        theta = torch.cat([base[:sl.start], part, base[sl.stop:]])
        if form == 'secondary_setpoint':
            rate_slice = eq.parameter_layout['hx_T_set_rate']
            theta = torch.cat([theta[:rate_slice.start], ((parameter - value) / dt).reshape(1), theta[rate_slice.stop:]])
        state, info = backward_euler_step(eq.problem, initial, theta, time=dt, dt=dt)
        assert info.scaled_residual_inf < 1e-8
        assert abs(float(balance(eq, state, initial, theta, dt)['energy_balance_W'].detach())) < 1e-6
        unpacked = eq.unpack(state, theta)
        key = {'pump_flow': 'flow', 'temperature_rise': 'solid_temperature',
               'secondary_setpoint': 'primary_return_temperature', 'pressure_boundary': 'pressure'}[form]
        return unpacked[key].mean()

    parameter = torch.tensor(value, dtype=torch.float64, requires_grad=True)
    derivative, = torch.autograd.grad(objective(parameter), parameter)
    epsilon = 1e-4 if form == 'pressure_boundary' else 1e-5
    plus, minus = objective(one(value + epsilon)[0]), objective(one(value - epsilon)[0])
    centered = (plus - minus) / (2 * epsilon)
    assert torch.isfinite(derivative)
    assert abs(float(derivative)) > 1e-8
    torch.testing.assert_close(derivative, centered, rtol=1e-5, atol=1e-6)
    if form in {'pump_flow', 'pressure_boundary'}:
        assert derivative.item() == pytest.approx(1., rel=1e-9, abs=1e-9)