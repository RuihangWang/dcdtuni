"""Explicit-state PID for simulated actuators, with differentiable smooth regions.

This module does not communicate with plant equipment. Output units are chosen
by the binding (for example pump speed ratio or valve opening). Setpoint and
measurement must use the same engineering units; gains carry the corresponding
output/input units. Hard saturation and antiwindup are piecewise differentiable.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Literal

import torch
from torch import Tensor


@dataclass(frozen=True)
class PIDConfig:
    kp: float
    ki: float
    kd: float = 0.0
    setpoint: float = 0.0
    bias: float = 0.0
    output_min: float = 0.0
    output_max: float = 1.0
    action: Literal["direct", "reverse"] = "reverse"
    rate_limit_per_s: float | None = None
    derivative_tau_s: float = 0.0

    def __post_init__(self):
        fields = ("kp", "ki", "kd", "setpoint", "bias", "output_min", "output_max", "derivative_tau_s")
        for name in fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if min(self.kp, self.ki, self.kd, self.derivative_tau_s) < 0:
            raise ValueError("gains and derivative_tau_s must be non-negative; select action for direction")
        if self.output_min >= self.output_max:
            raise ValueError("output_min must be below output_max")
        if not self.output_min <= self.bias <= self.output_max:
            raise ValueError("bias must be within output limits")
        if self.action not in ("direct", "reverse"):
            raise ValueError("action must be direct or reverse")
        if self.rate_limit_per_s is not None:
            value = self.rate_limit_per_s
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("rate_limit_per_s must be positive and finite")


@dataclass(frozen=True)
class PIDState:
    integral: Tensor
    derivative: Tensor
    last_output: Tensor
    last_measurement: Tensor | None = None
    tracking_setpoint: Tensor | None = None


@dataclass(frozen=True)
class PIDStepResult:
    output: Tensor
    state: PIDState
    unclamped_output: Tensor
    saturated: bool
    rate_limited: bool


def _real_tensor(value, name: str, device=None) -> Tensor:
    def check(item):
        if isinstance(item, Tensor):
            if item.dtype == torch.bool or item.is_complex():
                raise ValueError(f"{name} must contain real numbers, not boolean or complex values")
        elif isinstance(item, (list, tuple)):
            for element in item:
                check(element)
        elif isinstance(item, bool) or not isinstance(item, Real):
            raise ValueError(f"{name} must contain real numbers")
    check(value)
    try:
        return torch.as_tensor(value, dtype=torch.float64, device=device)
    except (TypeError, RuntimeError, OverflowError) as exc:
        raise ValueError(f"{name} cannot be represented as real float64 values") from exc


def _scalar(value: float | Tensor, name: str, device=None) -> Tensor:
    result = _real_tensor(value, name, device)
    if result.numel() != 1 or not bool(torch.isfinite(result).all()):
        raise ValueError(f"{name} must be a finite scalar")
    return result.reshape(())


def initial_pid_state(
    config: PIDConfig, *, measurement: float | Tensor | None = None,
    output: float | Tensor | None = None, setpoint: float | Tensor | None = None,
) -> PIDState:
    """Initialize normally, or track an explicit manual output for transfer."""
    measured = None if measurement is None else _scalar(measurement, "measurement")
    device = measured.device if measured is not None else None
    applied = _scalar(config.bias if output is None else output, "output", device)
    if not bool((applied >= config.output_min) & (applied <= config.output_max)):
        raise ValueError("initial output must be within configured limits")
    integral = torch.zeros_like(applied)
    tracking_setpoint = None
    if output is not None:
        if measured is None:
            raise ValueError("manual tracking requires a measurement")
        target = _scalar(config.setpoint if setpoint is None else setpoint, "setpoint", device)
        direction = 1.0 if config.action == "reverse" else -1.0
        integral = applied - config.bias - config.kp * direction * (target - measured)
        tracking_setpoint = target
    return PIDState(integral, torch.zeros_like(applied), applied, measured, tracking_setpoint)


def pid_step(
    config: PIDConfig, state: PIDState, measurement: float | Tensor, dt_s: float,
    *, setpoint: float | Tensor | None = None, gains: Tensor | None = None,
) -> PIDStepResult:
    """Compute one update, returning new state without mutating the old state.

    Derivative acts on the measurement to avoid setpoint kick. Conditional
    integration blocks windup against both output and slew limits. Optional
    tensor gains [kp, ki, kd] retain autograd across a rollout for optimisation.
    The actuator's command is also its tracked output here; additional measured
    actuator lag belongs in the plant's explicit state.
    """
    if isinstance(dt_s, bool) or not isinstance(dt_s, (int, float)) or not math.isfinite(dt_s) or dt_s <= 0:
        raise ValueError("dt_s must be positive and finite")
    measured = _scalar(measurement, "measurement")
    device = measured.device
    target = _scalar(config.setpoint if setpoint is None else setpoint, "setpoint", device)
    old_integral = _scalar(state.integral, "state.integral", device)
    old_derivative = _scalar(state.derivative, "state.derivative", device)
    old_output = _scalar(state.last_output, "state.last_output", device)
    if not bool((old_output >= config.output_min) & (old_output <= config.output_max)):
        raise ValueError("state.last_output is outside configured limits")

    gain_values = _real_tensor(
        [config.kp, config.ki, config.kd] if gains is None else gains,
        "gains", device,
    )
    if gain_values.shape != (3,) or not bool(torch.isfinite(gain_values).all()) or bool((gain_values < 0).any()):
        raise ValueError("gains must contain three finite non-negative values")
    kp, ki, kd = gain_values.unbind()
    direction = 1.0 if config.action == "reverse" else -1.0
    if state.tracking_setpoint is not None:
        if state.last_measurement is None:
            raise ValueError("manual tracking state requires a previous measurement")
        tracking_target = _scalar(state.tracking_setpoint, "state.tracking_setpoint", device)
        tracking_measured = _scalar(state.last_measurement, "state.last_measurement", device)
        old_integral = old_output - config.bias - kp * direction * (tracking_target - tracking_measured) - kd * old_derivative
    error = direction * (target - measured)
    if state.last_measurement is None:
        raw_derivative = torch.zeros_like(measured)
    else:
        previous = _scalar(state.last_measurement, "state.last_measurement", device)
        raw_derivative = -direction * (measured - previous) / dt_s
    alpha = dt_s / (config.derivative_tau_s + dt_s)
    derivative = old_derivative + alpha * (raw_derivative - old_derivative)
    delta_integral = ki * error * dt_s
    integral = old_integral + delta_integral

    def bound(command: Tensor) -> tuple[Tensor, Tensor]:
        limited = torch.clamp(command, config.output_min, config.output_max)
        applied = limited
        if config.rate_limit_per_s is not None:
            distance = config.rate_limit_per_s * dt_s
            applied = torch.minimum(torch.maximum(limited, old_output - distance), old_output + distance)
        return limited, applied

    proportional_derivative = config.bias + kp * error + kd * derivative
    raw = proportional_derivative + integral
    requested = raw
    if not bool(torch.isfinite(requested)):
        raise ValueError("PID arithmetic produced a non-finite requested command")
    candidate_limited, candidate_output = bound(requested)
    pushes_limit = ((raw > candidate_output) & (delta_integral > 0)) | ((raw < candidate_output) & (delta_integral < 0))
    # Accept the portion of an integral update that reaches a limit, but none
    # beyond it. Rejecting the entire update would pin a pure-I controller at
    # zero forever when its first update overshoots the output bound.
    room = candidate_output - (proportional_derivative + old_integral)
    zero = torch.zeros_like(room)
    admissible_delta = torch.minimum(torch.maximum(delta_integral, torch.minimum(room, zero)), torch.maximum(room, zero))
    integral = old_integral + torch.where(pushes_limit, admissible_delta, delta_integral)
    raw = proportional_derivative + integral
    limited, applied = bound(raw)
    if not bool(torch.isfinite(torch.stack((applied, integral, derivative, raw))).all()):
        raise ValueError("PID arithmetic produced a non-finite state")
    next_state = PIDState(integral, derivative, applied, measured)
    return PIDStepResult(
        applied, next_state, requested,
        bool((requested != candidate_limited) | (raw != limited)),
        bool((candidate_limited != candidate_output) | (limited != applied)),
    )
