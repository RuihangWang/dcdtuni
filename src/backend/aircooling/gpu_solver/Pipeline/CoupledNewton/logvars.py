"""Newton in logarithmic turbulence variables: a change of unknowns, not of discretisation (opt-in).

What it does.  For a turbulence model whose transported fields are stored primal (standard k-epsilon: k and
epsilon), ``enable`` makes every ``solve_newton`` call of the collocated solver iterate on ``ln k`` and
``ln epsilon`` instead of ``k`` and ``epsilon``.  The residual is still the primal discrete residual, evaluated at
``k = exp(ln k)``, ``epsilon = exp(ln epsilon)`` (``casestate.residual`` maps the iterate back before the forward
pass), so the discrete root is the same, every row keeps its value at a given primal state, and the certificate --
``max|F|`` of the unmodified residual at the returned primal state -- is the same quantity.  The Jacobian becomes
``J diag(k, epsilon)`` on those columns; ``linearise`` and ``assemble`` build it through the residual's own tape,
so the chain-rule factor comes from autograd and the sparsity pattern is unchanged.

The interface stays primal.  ``solve_newton`` takes and returns primal states; inside the call the iterate, the
pinned-row anchors of a staged cold start and the PTC reference live in the new variables, and the ``checkpoint``
callback receives primal states, so every saved state and every reader (wall readings, scores, exports, restarts)
sees the fields it always saw.  Outside a ``solve_newton`` call nothing changes.

Why.  On the high-Reynolds flat plate k spans six decades and epsilon eight, from the freestream to the
wall-function cells.  A primal Newton step that is a small correction at the wall is a sign flip in the freestream:
at a converged reference field the residual along the exact Newton direction rises as soon as cells go negative
(lambda 0.01), and once k sits on its floor the epsilon sink C2 epsilon^2 / k explodes.  In logarithmic variables a
step is a relative change in every cell, positivity holds by construction, and the per-trial bound of the staged
cold start acts on them as the cap on ``ln omega`` already does for the k-omega models (``BoundedTurbulenceTrial``
reads ``newton_log_fields``).  The epsilon wall-function row keeps its primal form,
``epsilon_c - Cmu^0.75 k_c^1.5 / (kappa y)``; in the new variables its linearisation is
``epsilon_c d(ln epsilon_c) - 1.5 (Cmu^0.75 k_c^1.5 / (kappa y)) d(ln k_c)``, the log-linear relation the constraint
satisfies at its root.
"""
from __future__ import annotations

import torch

# kwargs of ``solve_newton`` whose callbacks read or write the iterate in ways this wrapper does not translate
UNSUPPORTED = ("candidate_guard", "elimination", "carry", "accelerator", "restart_probe", "first_assembly_dump")


def newton_log_fields(solver) -> tuple:
    """The unknowns Newton iterates on as logarithms (empty when the change of unknowns is off)."""
    state = getattr(solver, "_newton_logs", None)
    return tuple(state["fields"]) if state else ()


def enable(solver, fields=None) -> dict:
    """Switch ``solver`` (a ``CollocatedSolver``) to logarithmic turbulence unknowns for every later ``solve_newton``.

    ``fields`` defaults to every transported turbulence field the model stores primal.  Refuses a case without such
    a field, a field the model already stores as a logarithm, and the fused residual backend (it does not read the
    mapping).  Returns ``{"fields": (...), "rows": n}``."""
    from Pipeline.CoupledNewton.coldstart import field_slices, log_fields, turbulence_fields
    if getattr(solver, "_warp", None) is not None:
        raise ValueError("logarithmic turbulence unknowns are not available with the fused residual backend")
    stored = set(log_fields(solver))
    available = [name for name in turbulence_fields(solver) if name not in stored]
    chosen = tuple(available) if fields is None else tuple(str(f) for f in fields)
    if not chosen:
        raise ValueError("this case has no transported turbulence field stored primal")
    for name in chosen:
        if name not in available:
            raise ValueError(f"{name!r} is not a primal transported turbulence field of this case ({available})")
    slices = field_slices(solver)
    index = torch.cat([torch.arange(slices[name].start, slices[name].stop) for name in chosen])
    solver._newton_logs = {"fields": chosen, "index": index.to(solver.interior.device), "active": False}
    return {"fields": chosen, "rows": int(index.numel())}


def active(solver) -> bool:
    state = getattr(solver, "_newton_logs", None)
    return bool(state and state["active"])


def to_primal(solver, x: torch.Tensor) -> torch.Tensor:
    """The primal state of an iterate in the new variables (``exp`` on the logarithmic rows); differentiable."""
    state = getattr(solver, "_newton_logs", None)
    if not state:
        return x
    flat = x.reshape(-1)
    index = state["index"].to(flat.device)
    return flat.index_copy(0, index, torch.exp(flat.index_select(0, index))).reshape(x.shape)


def from_primal(solver, x: torch.Tensor) -> tuple:
    """``(x in the new variables, number of non-positive entries raised to the smallest positive double)``."""
    state = getattr(solver, "_newton_logs", None)
    if not state:
        return x, 0
    flat = x.detach().reshape(-1)
    index = state["index"].to(flat.device)
    values = flat.index_select(0, index)
    tiny = torch.finfo(values.dtype).tiny
    raised = int((values <= 0).sum())
    return flat.index_copy(0, index, torch.log(values.clamp_min(tiny))).reshape(x.shape), raised


def solve(solver, inner, **kwargs):
    """``inner(solver, **kwargs)`` (``newton.solve_newton``) in the new variables, primal in and out.

    Maps ``x0`` and the anchors of pinned rows into the new variables, passes primal states to ``checkpoint``,
    marks the solver active so ``casestate.residual`` maps every iterate back before the forward pass, and returns
    ``(primal x, history)``.  The pin and the active flag are restored whatever happens."""
    state = solver._newton_logs
    for name in UNSUPPORTED:
        if kwargs.get(name) is not None:
            raise ValueError(f"solve_newton({name}=...) is not supported with logarithmic turbulence unknowns")
    x0 = kwargs.get("x0")
    if x0 is None:
        raise ValueError("logarithmic turbulence unknowns need a first iterate (x0)")
    kwargs["x0"], raised = from_primal(solver, x0)
    state["raised"] = raised
    inner_checkpoint = kwargs.get("checkpoint")
    if inner_checkpoint is not None:
        kwargs["checkpoint"] = lambda iteration, x: inner_checkpoint(iteration, to_primal(solver, x.detach()))
    pin = getattr(solver, "_pin", None)
    if pin is not None:
        mask, anchor = pin
        solver._pin = (mask, from_primal(solver, anchor)[0])
    state["active"] = True
    try:
        x, history = inner(solver, **kwargs)
    finally:
        state["active"] = False
        solver._pin = pin
    return to_primal(solver, x.detach()), history
