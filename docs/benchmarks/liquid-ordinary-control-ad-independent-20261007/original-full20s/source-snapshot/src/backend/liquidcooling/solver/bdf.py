"""
BDF (Backward Differentiation Formula) time-derivative utilities.

Provides:
  - smooth_abs: differentiable |x| approximation
  - bdf_diff:   BDF1/2/3 finite-difference time derivatives
"""
from __future__ import annotations

import torch
from torch import Tensor


def smooth_abs(x: Tensor, eps: float = 1e-6) -> Tensor:
    """Differentiable absolute value: sqrt(x^2 + eps^2).

    Unlike torch.abs, this is smooth at x=0 so gradients are well-defined.
    The approximation error is at most eps (at x=0).

    Args:
        x:   Input tensor.
        eps: Smoothing parameter (default 1e-6).

    Returns:
        Tensor with same shape as x, values >= eps.
    """
    return torch.sqrt(x * x + eps * eps)


def bdf_diff(x: Tensor, dt: float, order: int) -> Tensor:
    """Compute BDF time derivative of x[..., N_t], returns [..., N_t - 1].

    All orders produce the same output length (N_t - 1) by using lower-order
    BDF fallback for the initial steps that lack sufficient history.

    Formulas
    --------
    BDF1 (1st-order accurate):
        dx/dt[t] ≈ (x[t] - x[t-1]) / dt

    BDF2 (2nd-order accurate):
        dx/dt[t] ≈ (3*x[t] - 4*x[t-1] + x[t-2]) / (2*dt)
        Fallback to BDF1 at step index 0 (t=1, only one previous point).

    BDF3 (3rd-order accurate):
        dx/dt[t] ≈ (11*x[t] - 18*x[t-1] + 9*x[t-2] - 2*x[t-3]) / (6*dt)
        Fallback to BDF2 at step index 1 (t=2), BDF1 at step index 0 (t=1).

    Args:
        x:     Tensor with time along the last axis, shape [..., N_t].
        dt:    Uniform time step size (scalar).
        order: BDF order, must be 1, 2, or 3.

    Returns:
        Tensor of shape [..., N_t - 1] containing time derivatives.

    Raises:
        ValueError: If order is not in {1, 2, 3}.
    """
    if order not in (1, 2, 3):
        raise ValueError(f"BDF order must be 1, 2, or 3; got {order}")

    # x has shape [..., N_t]; N_t >= 2 required
    N_t = x.shape[-1]
    if N_t < 2:
        raise ValueError(f"Need at least 2 time steps; got {N_t}")

    # We build a list of derivatives for output indices 0 .. N_t-2
    # (output index i corresponds to input time step i+1)
    parts: list[Tensor] = []

    for i in range(N_t - 1):
        t = i + 1  # current time index in x

        if order == 1 or t == 1:
            # BDF1 fallback (also the only option when t == 1)
            d = (x[..., t] - x[..., t - 1]) / dt

        elif order == 2 and t >= 2:
            # Full BDF2
            d = (3.0 * x[..., t] - 4.0 * x[..., t - 1] + x[..., t - 2]) / (2.0 * dt)

        elif order == 3:
            if t == 2:
                # BDF2 fallback
                d = (3.0 * x[..., t] - 4.0 * x[..., t - 1] + x[..., t - 2]) / (2.0 * dt)
            else:
                # Full BDF3 (t >= 3)
                d = (
                    11.0 * x[..., t]
                    - 18.0 * x[..., t - 1]
                    + 9.0 * x[..., t - 2]
                    - 2.0 * x[..., t - 3]
                ) / (6.0 * dt)

        parts.append(d.unsqueeze(-1))

    return torch.cat(parts, dim=-1)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running bdf self-test on x = t^2 ...")

    dt = 0.01
    N_t = 200
    t = torch.arange(N_t, dtype=torch.float64) * dt
    x = t * t  # shape [N_t]

    # Exact derivative: d(t^2)/dt = 2t, evaluated at t[1..N_t-1]
    exact = 2.0 * t[1:]  # shape [N_t - 1]

    for order in (1, 2, 3):
        dx = bdf_diff(x, dt=dt, order=order)
        # For BDF2/3, skip fallback steps at the start for the max-error check
        skip = order - 1  # 0 for BDF1, 1 for BDF2, 2 for BDF3
        max_err = (dx[skip:] - exact[skip:]).abs().max().item()
        print(f"  BDF{order}: max_error (main steps) = {max_err:.3e}")
        assert max_err < 0.1, f"BDF{order} max_error {max_err} >= 0.1"

    print("PASS: bdf self-test")
