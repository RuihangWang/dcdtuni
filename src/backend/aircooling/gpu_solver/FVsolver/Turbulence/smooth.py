"""Smooth positivity helpers -- the ONLY place these are spelled.

Design: docs/superpowers/specs/2026-08-16-turbulence-framework-design.md,
section 2.3.  Every helper here is C-infinity with a strictly positive
derivative on the states a cold start visits, which is the property
``torch.clamp`` lacks (its zero-gradient region is exactly where a cold
start lives, and it kills the adjoint the GNN/ODIL paradigms backprop
through).  Gate: fp64 gradcheck INCLUDING below-floor probes
(tests/test_smooth_gradcheck.py).

Floor/width/sharpness values are config keys under ``[turbulence]`` with
model defaults -- no case identity here.
"""

import torch
import torch.nn.functional as F


def smooth_floor(x: torch.Tensor, floor: float, width: float) -> torch.Tensor:
    """``floor + width * softplus((x - floor) / width)``.

    Softplus-shifted floor (design section 2.3.2): always > ``floor``,
    asymptotically the identity for ``x >> floor`` (softplus's linear branch
    is exact past its internal threshold), and its derivative
    ``sigmoid((x - floor)/width)`` is strictly positive everywhere -- the
    discriminating property the gradcheck gate probes below the floor.
    """
    return floor + F.softplus((x - floor) / width) * width


def smooth_max(a: torch.Tensor, b: torch.Tensor, sharpness: float) -> torch.Tensor:
    """Logsumexp blend of ``max(a, b)``: ``logaddexp(s*a, s*b) / s``.

    Overshoots the hard max by at most ``ln(2)/sharpness`` -- the sharpness
    is a config knob (design section 2.3.3, for limiters DESIGNED to be
    active in the operating region; Piotrowski-Zingg 2020 precedent).
    """
    return torch.logaddexp(a * sharpness, b * sharpness) / sharpness


def smooth_min(a: torch.Tensor, b: torch.Tensor, sharpness: float) -> torch.Tensor:
    """Logsumexp blend of ``min(a, b)``; undershoots by <= ``ln(2)/sharpness``."""
    return -smooth_max(-a, -b, sharpness)


def sqrt_floored(x: torch.Tensor, eps: float) -> torch.Tensor:
    """``sqrt(x + eps^2)`` -- finite slope ``1/(2 eps)`` at ``x = 0``.

    For nonnegative invariants entering a square root (e.g. ``S2`` at
    ``S2 -> 0``, design section 2.3.2), where a bare ``sqrt`` has an
    infinite derivative and gradcheck fails.
    """
    return torch.sqrt(x + eps * eps)


def pnorm_max(a: torch.Tensor, b: torch.Tensor, p: float) -> torch.Tensor:
    """p-norm blend of ``max(a, b)`` for NONNEGATIVE operands whose larger
    side is strictly positive: ``(a^p + b^p)^(1/p)``.

    The scale-invariant sibling of ``smooth_max`` (design section 2.3.3
    names both): relative overshoot <= ``2^(1/p) - 1`` at a tie and exact
    where one side dominates, at EVERY magnitude -- where the logsumexp
    blend's ln2/sharpness error is absolute and swamps operands smaller
    than it (the SST nut denominator spans decades, which is why it takes
    this form and not smooth_max).  Spelled through the min/max ratio
    ``t = min/max`` in [0, 1] so ``t^p`` can never overflow; the branchy
    spelling equals the smooth closed form everywhere, so autograd's
    derivative is the true derivative (torch.maximum splits the gradient
    at the measure-zero tie, where symmetry makes the split exact --
    gradcheck-gated at the tie).  Both operands zero is OUTSIDE the
    contract (0/0); callers guarantee a positive side (SST: a1 e^wt > 0).
    """
    big = torch.maximum(a, b)
    small = torch.minimum(a, b)
    t = small / big
    return big * (1.0 + t ** p) ** (1.0 / p)
