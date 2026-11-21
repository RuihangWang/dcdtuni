"""Behavioral control tests in output units, independent of a plant solver."""
import pytest
import torch


def api():
    from backend.liquidcooling.control import PIDConfig, initial_pid_state, pid_step
    return PIDConfig, initial_pid_state, pid_step


def test_proportional_action_bias_and_input_state_are_explicit():
    C, init, step = api()
    cfg = C(kp=0.1, ki=0.0, setpoint=10.0, bias=0.5)
    state = init(cfg)
    result = step(cfg, state, measurement=8.0, dt_s=1.0)
    assert result.output.item() == pytest.approx(0.7)
    assert state.integral.item() == 0.0
    assert state.last_measurement is None
    assert result.state.last_measurement.item() == 8.0


def test_integral_action_accumulates_when_unsaturated():
    C, init, step = api()
    cfg = C(kp=0.0, ki=0.05, setpoint=10.0)
    state = init(cfg)
    for _ in range(3):
        result = step(cfg, state, 8.0, 1.0)
        state = result.state
    assert result.output.item() == pytest.approx(0.3)


def test_integral_only_controller_can_reach_limit_without_getting_stuck_at_zero():
    C, init, step = api()
    cfg = C(kp=0.0, ki=1.0, setpoint=2.0)
    state = init(cfg)
    for _ in range(3):
        result = step(cfg, state, 0.0, 1.0)
        state = result.state
    assert result.output.item() == 1.0
    assert state.integral.item() == 1.0
    recovery = step(cfg, state, 2.5, 1.0)
    assert recovery.output.item() == pytest.approx(0.5)


def test_antiwindup_projection_keeps_requested_limit_diagnostics():
    C, init, step = api()
    cfg = C(kp=0.0, ki=1.0, setpoint=2.0, rate_limit_per_s=0.25)
    result = step(cfg, init(cfg), 0.0, 1.0)
    assert result.output.item() == pytest.approx(0.25)
    assert result.unclamped_output.item() == pytest.approx(2.0)
    assert result.saturated
    assert result.rate_limited


def test_saturation_does_not_wind_up_and_recovers_after_error_reverses():
    C, init, step = api()
    cfg = C(kp=0.2, ki=0.1, setpoint=10.0, bias=0.5)
    state = init(cfg)
    for _ in range(50):
        result = step(cfg, state, 0.0, 1.0)
        state = result.state
    assert result.output.item() == 1.0
    assert state.integral.item() == 0.0
    recovery = step(cfg, state, 11.0, 1.0)
    assert recovery.output.item() < 0.5


def test_direct_action_increases_cooling_when_temperature_is_high():
    C, init, step = api()
    cfg = C(kp=0.05, ki=0.0, setpoint=30.0, bias=0.5, action="direct")
    result = step(cfg, init(cfg), 35.0, 1.0)
    assert result.output.item() == pytest.approx(0.75)


def test_rate_limit_is_per_second_and_part_of_antiwindup():
    C, init, step = api()
    cfg = C(kp=0.2, ki=0.1, setpoint=10.0, bias=0.2, rate_limit_per_s=0.1)
    result = step(cfg, init(cfg), 0.0, 0.5)
    assert result.output.item() == pytest.approx(0.25)
    assert result.rate_limited
    assert result.state.integral.item() == 0.0


def test_derivative_is_on_measurement_without_setpoint_kick():
    C, init, step = api()
    cfg = C(kp=0.0, ki=0.0, kd=0.1, setpoint=10.0, bias=0.5)
    state = init(cfg, measurement=8.0)
    result = step(cfg, state, 8.0, 1.0, setpoint=20.0)
    assert result.output.item() == pytest.approx(0.5)


def test_derivative_filter_matches_first_order_discretisation():
    C, init, step = api()
    cfg = C(kp=0.0, ki=0.0, kd=0.1, bias=0.5, derivative_tau_s=1.0)
    state = init(cfg, measurement=0.0)
    result = step(cfg, state, 1.0, 1.0)
    assert result.state.derivative.item() == pytest.approx(-0.5)
    assert result.output.item() == pytest.approx(0.45)


def test_manual_tracking_produces_bumpless_first_automatic_output():
    C, init, step = api()
    cfg = C(kp=0.1, ki=0.0, setpoint=10.0, bias=0.5)
    state = init(cfg, measurement=8.0, output=0.4)
    result = step(cfg, state, 8.0, 1.0)
    assert result.output.item() == pytest.approx(0.4)


def test_manual_tracking_uses_effective_tensor_gains_on_first_step():
    C, init, step = api()
    cfg = C(kp=0.1, ki=0.0, setpoint=10.0, bias=0.5)
    state = init(cfg, measurement=8.0, output=0.4)
    result = step(cfg, state, 8.0, 1.0, gains=torch.tensor([0.2,0.0,0.0]))
    assert result.output.item() == pytest.approx(0.4)


@pytest.mark.parametrize("value", [True, torch.tensor(True), 1+2j, torch.tensor(1+2j)])
def test_nonreal_or_boolean_measurement_is_rejected_before_conversion(value):
    C, init, step = api()
    cfg = C(kp=0.1, ki=0.0)
    with pytest.raises(ValueError):
        step(cfg, init(cfg), value, 1.0)


def test_boolean_gain_cannot_be_coerced_inside_a_mixed_list():
    C, init, step = api()
    cfg = C(kp=0.1, ki=0.0)
    with pytest.raises(ValueError):
        step(cfg, init(cfg), 0.0, 1.0, gains=[0.1,True,0.0])


def test_finite_input_overflow_cannot_emit_nonfinite_requested_command():
    C, init, step = api()
    cfg = C(kp=0.0, ki=1e308, setpoint=2.0)
    with pytest.raises(ValueError):
        step(cfg, init(cfg), 0.0, 1.0)


@pytest.mark.parametrize("measurement,dt", [(float("nan"),1.0),(8.0,0.0),(8.0,-1.0),(8.0,float("inf"))])
def test_invalid_observation_or_timestep_cannot_emit_command(measurement, dt):
    C, init, step = api()
    cfg = C(kp=0.1, ki=0.0)
    with pytest.raises(ValueError):
        step(cfg, init(cfg), measurement, dt)


def test_multistep_gain_gradient_matches_finite_difference():
    C, init, step = api()
    cfg = C(kp=0.1, ki=0.02, setpoint=2.0, bias=0.1, output_max=2.0)
    def run(gains):
        state = init(cfg)
        total = torch.zeros((), dtype=torch.float64)
        for measurement in (1.0, 1.2, 1.4):
            result = step(cfg, state, measurement, 0.5, gains=gains)
            state = result.state
            total = total + result.output.square()
        return total
    gains = torch.tensor([0.1, 0.02, 0.0], dtype=torch.float64, requires_grad=True)
    loss = run(gains)
    grad = torch.autograd.grad(loss, gains)[0]
    h = 1e-6
    for index in (0,1):
        delta = torch.zeros_like(gains); delta[index] = h
        finite_difference = (run(gains.detach()+delta)-run(gains.detach()-delta))/(2*h)
        assert grad[index].item() == pytest.approx(finite_difference.item(), rel=1e-5, abs=1e-8)
