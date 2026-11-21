"""Dual-number arithmetic for the Warp residuals (docs/research/2026-10-14-warp-backend.md).

Every state-dependent quantity in a Warp residual kernel is a float64 Warp vector ``(value, t_1, ..., t_K)``: the
value and K tangents carried together.  Sums, differences and products with geometry are linear and act on all
components at once, which is exactly dual arithmetic; only a product or quotient of two state-dependent quantities,
and the piecewise-linear ``max``, need the helpers below.  One kernel source therefore gives the residual (K = 0,
a length-1 vector), the exact Jacobian-vector product (K = 1) and a batch of K colouring probes (K = 8, 16) -- the
same seeds and the same compression as the torch assembly -- without a tape.

``dual_type(K)`` returns the vector type; kernels are written once with ``typing.Any`` and overloaded per type.
"""
from __future__ import annotations

from typing import Any

import warp as wp

wp.config.quiet = True
wp.init()

_TYPES: dict[int, type] = {}


def dual_type(k: int):
    """The Warp vector type carrying a value and ``k`` tangents (float64)."""
    if k not in _TYPES:
        _TYPES[k] = wp.types.vector(length=k + 1, dtype=wp.float64)
    return _TYPES[k]


@wp.func
def dmul(a: Any, b: Any):
    """(a0 + a' e)(b0 + b' e) = a0 b0 + (a0 b' + a' b0) e."""
    r = a[0] * b + b[0] * a
    r[0] = a[0] * b[0]
    return r


@wp.func
def ddiv(a: Any, b: Any):
    """a / b with (a/b)' = a'/b0 - a0 b'/b0^2."""
    inv = wp.float64(1.0) / b[0]
    r = inv * a - (a[0] * inv * inv) * b
    r[0] = a[0] * inv
    return r


@wp.func
def dmax0(a: Any):
    """max(a, 0) with torch.clamp_min's derivative: the tangent passes where a0 >= 0."""
    if a[0] >= wp.float64(0.0):
        return a
    return a * wp.float64(0.0)


@wp.func
def dconst(like: Any, c: wp.float64):
    """The constant c as a dual of the type of ``like`` (zero tangents)."""
    r = like * wp.float64(0.0)
    r[0] = c
    return r


@wp.func
def dzero(like: Any):
    return like * wp.float64(0.0)


@wp.func
def dvalue_only(a: Any):
    """a with its tangents dropped (torch's .detach())."""
    r = a * wp.float64(0.0)
    r[0] = a[0]
    return r
