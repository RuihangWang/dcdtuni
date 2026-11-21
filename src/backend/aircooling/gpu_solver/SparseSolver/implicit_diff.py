"""Implicit differentiation of a solution defined by ``r(x, theta) = 0``.

For a residual ``r`` whose root ``x*(theta)`` is what a solver returns, and a scalar
loss ``L(x*)``, the implicit function theorem gives::

    J^T v = dL/dx        (one transposed linear solve)
    dL/dtheta = -(dr/dtheta)^T v

with ``J = dr/dx`` at the converged point.  The cost is ONE transposed solve and one
vector-Jacobian product, both independent of how many Newton or Krylov iterations the
forward solve consumed, and nothing about the forward solve is taped.  That property
is why a coupled Newton solver can be differentiated at all: unrolling a segregated
loop would tape every one of its iterations.

Neither ``dr/dtheta`` nor ``J`` is ever formed.  ``(dr/dtheta)^T v`` and ``J^T v`` are
both vector-Jacobian products of the same residual closure, so a caller supplying a
matrix-free residual keeps the whole adjoint matrix-free.  A sparse-matrix gradient
(``dL/dA = -v x^T`` restricted to the sparsity pattern) is deliberately absent: the
VJP formulation subsumes it, and it is the piece that invites a per-row Python loop.

The module knows nothing about the equations being solved.  It takes closures, and
``x`` is one flat vector.

DOUBLE BACKWARD IS NOT SUPPORTED.  The backward pass evaluates its VJP without
``create_graph``, so the returned gradients are leaves.

Attribution: the adjoint identity here is the one implemented for the fixed-matrix
case by ``module_c/cudss_solver.py`` in the user's pytorch-sparse-linalg
(Apache-2.0, Copyright 2025 Litianyu141), whose docstring also states the
implicit-differentiation contract for ``cg``.  No code was copied.  What changed:
the residual is a general nonlinear closure rather than a fixed matrix, the
parameter gradient is a VJP through that closure instead of an assembled sparse
matrix gradient, and the reference's row-by-row Python loop over the CSR rows
(minutes per backward pass at this campaign's 435,456 rows) therefore has no
counterpart here.
"""
from __future__ import annotations

from typing import Callable, Sequence, Tuple

import torch


def transpose_jacobian_matvec(
    residual_fn: Callable[..., torch.Tensor],
    x_star: torch.Tensor,
    params: Sequence[torch.Tensor],
) -> Callable[[torch.Tensor], torch.Tensor]:
    """Build ``v -> J^T v`` at ``(x_star, *params)``, where ``J = dr/dx``.

    This is a vector-Jacobian product, i.e. ``grad_x (r . v)``, so it applies the
    TRANSPOSE.  Feeding it to a Krylov solver is what turns the adjoint equation
    into a matrix-free solve; ``J`` itself is never assembled.

    The returned callable re-evaluates the residual on every application, which is
    the price of not assembling.  A caller that would rather pay once can assemble
    ``J^T`` by applying this to the basis vectors.
    """
    frozen = [p.detach() for p in params]

    def op(v: torch.Tensor) -> torch.Tensor:
        with torch.enable_grad():
            x = x_star.detach().requires_grad_(True)
            r = residual_fn(x, *frozen)
            (out,) = torch.autograd.grad(r, x, grad_outputs=v)
        return out

    return op


class _ImplicitSolution(torch.autograd.Function):
    """Identity in the forward direction, implicit-function-theorem adjoint backward."""

    @staticmethod
    def forward(ctx, residual_fn, transpose_solve, strict, x_star, *params):
        ctx.residual_fn = residual_fn
        ctx.transpose_solve = transpose_solve
        ctx.strict = strict
        ctx.save_for_backward(x_star, *params)
        return x_star.detach().clone()

    @staticmethod
    def backward(ctx, grad_out):
        x_star, *params = ctx.saved_tensors
        wanted = [i for i, need in enumerate(ctx.needs_input_grad[4:]) if need]
        if not wanted:
            return (None, None, None, None) + (None,) * len(params)

        v = ctx.transpose_solve(grad_out)

        with torch.enable_grad():
            x = x_star.detach().requires_grad_(True)
            leaves = [params[i].detach().requires_grad_(True) for i in wanted]
            args = list(params)
            for slot, leaf in zip(wanted, leaves):
                args[slot] = leaf
            r = ctx.residual_fn(x, *args)
            vjp = torch.autograd.grad(r, leaves, grad_outputs=v, allow_unused=True)

        grads = [None] * len(params)
        for slot, g in zip(wanted, vjp):
            if g is None and ctx.strict:
                # ``allow_unused`` above is what makes a DISCONNECTED parameter
                # return None rather than raise, and a caller that asked for
                # this gradient reads that None as "the derivative is zero" or
                # dies on ``float(None)`` several frames away from the cause.
                # A residual closure that accepts ``*params`` and then reads the
                # coefficient off some object instead is the way this happens,
                # and it is how ``CoupledNewton.newton.differentiable_solution``
                # returned None for four days without a test noticing (fixed
                # by the strict default).  ``strict=False`` restores the silent None for a
                # caller that genuinely wants it.
                raise RuntimeError(
                    f"parameter {slot} requires grad but never reached the "
                    "residual: autograd found no path from it to r(x, *params), "
                    "so its gradient is None rather than a number.  The residual "
                    "closure must USE the parameters it is handed -- reading the "
                    "value off another object instead leaves the fresh leaves "
                    "this backward pass differentiates against untouched")
            grads[slot] = None if g is None else -g
        return (None, None, None, None) + tuple(grads)


def implicit_solution(
    x_star: torch.Tensor,
    residual_fn: Callable[..., torch.Tensor],
    transpose_solve: Callable[[torch.Tensor], torch.Tensor],
    *params: torch.Tensor,
    strict: bool = True,
) -> torch.Tensor:
    """Attach implicit-function-theorem gradients to an already-converged solution.

    Parameters
    ----------
    x_star
        The converged solution, computed by any solver under ``torch.no_grad``.
        Must NOT require grad: the forward solve is not part of the graph, and
        accepting a grad-tracking tensor here would silently drop its gradient.
    residual_fn
        ``r(x, *params) -> Tensor``, differentiable in both arguments.  Evaluated
        once in the backward pass; the forward pass never calls it.
    transpose_solve
        ``w -> v`` solving ``J^T v = w`` at ``(x_star, *params)``, exactly or
        approximately.  Build the operator with :func:`transpose_jacobian_matvec`
        and hand it to any solver, or assemble ``J^T`` if that is cheaper.
    *params
        Tensors the gradient flows to.  Any that do not require grad receive
        ``None`` and cost nothing.
    strict
        Raise when a parameter that DOES require grad comes back with no
        gradient, which means the residual closure ignored it.  ``False``
        returns the silent ``None`` instead.

    Returns
    -------
    A tensor equal to ``x_star`` that carries ``dL/dtheta`` back to ``params``.

    An INEXACT forward solve contaminates the gradient to first order in the
    forward residual, because the adjoint is evaluated at the point it was handed
    rather than at the true root.  Measured on the asymmetric fixture in
    ``tests/test_implicit_diff.py``, relative gradient error stayed
    BELOW ``||r(x)||`` at every truncation tested: ``||r|| = 6.2e-1 -> 7.5e-2``,
    ``6.8e-4 -> 2.3e-5``, ``3.8e-9 -> 2.0e-10``, i.e. a ratio of 0.03 to 0.13 over
    eight orders of magnitude.  A forward solve stopped at tolerance ``eps``
    therefore buys a gradient good to roughly ``eps``, and there is no benefit in
    solving the adjoint tighter than the forward solve was.
    """
    if x_star.requires_grad:
        raise ValueError(
            "x_star.requires_grad is True; the forward solve must run under "
            "torch.no_grad and x_star must be detached, otherwise its gradient "
            "would be silently discarded."
        )
    return _ImplicitSolution.apply(
        residual_fn, transpose_solve, strict, x_star, *params)
