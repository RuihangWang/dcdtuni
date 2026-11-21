"""Whether the pressure LEVEL is free here, and the projection that follows.

The decision is taken from the Jacobian rather than from a flag, because both
treatments are wrong for the other case and each fails by converging normally
onto a floor no tolerance moves.  ``resolve_gauge`` caches it for the run;
``gauge`` refuses to run before it.

Read off the ``CollocatedSolver`` passed in: ``velocity``, ``pressure``,
``_gauge_free``, and ``linearise``.
"""
from __future__ import annotations

import torch

# Edges on the constant pressure mode's relative Jacobian defect, deciding
# whether this case's pressure LEVEL is free (CollocatedSolver.resolve_gauge).
# The same pair ``SparseSolver.amg`` uses on the same question, and the measured
# populations are nowhere near them: exactly 0.0 for the all-Neumann cavity
# against 6.18e-01 for the cylinder's Dirichlet outlet.
GAUGE_FREE_BELOW = 1.0e-12
GAUGE_PINNED_ABOVE = 1.0e-6


def constant_pressure_defect(self, x: torch.Tensor) -> float:
    """How far the constant pressure mode is from ``null(J)`` at ``x``.

    ``J e_p`` with ``e_p`` the constant on the pressure unknowns and zero on
    the velocities is the residual's sensitivity to raising the WHOLE pressure
    field by one.  It vanishes iff that shift changes no equation, i.e. iff the
    level is free.  Reported relative to what the same Jacobian does to a
    velocity direction of the same norm, so the answer does not move with the
    scale of the operator and a "zero" is judged against something rather than
    against nothing.

    The probe direction stops at the PRESSURE block rather than running to the
    end of the vector.  On a Boussinesq case the tail is temperature, and a
    uniform temperature shift moves the buoyancy source and the wall
    conditions; folding it in here would read a large defect on a cavity whose
    pressure level is genuinely free, and the run would then be denied the
    projection it needs.
    """
    jvp = self.linearise(x)
    direction = torch.zeros_like(x)
    direction[self.velocity:self.pressure] = 1.0
    shifted = float(jvp(direction).norm())
    direction = torch.zeros_like(x)
    direction[:self.velocity] = 1.0
    return shifted / max(float(jvp(direction).norm()), 1e-300)


def resolve_gauge(self, x: torch.Tensor) -> bool:
    """Whether the pressure level is free here, DECIDED from the Jacobian.

    Projecting a mode the operator does not have is not a harmless extra
    safety: it deletes part of every Newton step, and the run then converges
    normally while the residual is large and floors at a level no tolerance,
    preconditioner or Reynolds number moves.  ``SparseSolver.amg`` refuses to
    take this decision from a caller for the same reason one level down; this
    is the same refusal on the whole Jacobian rather than on the pressure
    operator alone.

    The two populations are not close.  Measured on the cold state,
    momentum-weighted flux on: the 16^3 all-Neumann cavity reads EXACTLY 0.0
    (its pressure enters only through differences, so the cancellation is
    structural rather than lucky), while the cylinder O-grid, whose outlet
    carries ``p`` fixedValue, reads 6.180e-01 -- the constant pressure mode is
    62% as live as a same-norm velocity direction.  The edges below are the
    ones ``SparseSolver.amg`` uses on the same question and sit fifteen orders
    inside that gap.

    What the unconditional projection cost before this existed, same case, same
    flags: Newton fell cleanly to ``max|r| = 1.8e-07`` and then stopped, with
    GMRES exhausting 1600 iterations per step on a right-hand side whose
    deleted component it could not reach.
    """
    if self._gauge_free is not None:
        return self._gauge_free
    defect = self.constant_pressure_defect(x)
    if defect <= GAUGE_FREE_BELOW:
        self._gauge_free = True
    elif defect >= GAUGE_PINNED_ABOVE:
        self._gauge_free = False
    else:
        raise ValueError(
            f"cannot tell whether this case pins its own pressure level: the "
            f"constant pressure mode's relative Jacobian defect is "
            f"{defect:.3e}, between {GAUGE_FREE_BELOW:.0e} and "
            f"{GAUGE_PINNED_ABOVE:.0e}. Both treatments are wrong for the "
            f"other case, so neither is a safe default here")
    return self._gauge_free


def gauge(self, x: torch.Tensor) -> torch.Tensor:
    """Remove the constant-pressure mode WHEN no equation constrains it.

    With no Dirichlet pressure boundary the pressure LEVEL is free, so ``J`` is
    singular in that one direction and ``J^T`` is singular in the matching
    continuity mode.  Projecting the step rather than pinning a row is what the
    staggered solver measured as the better treatment: on a gauge-pinned
    Jacobian, GMRES preconditioned by one V-cycle diverged where the projected
    one converged.  A case with a pressure outlet has no such mode and is
    damaged by the same projection; ``resolve_gauge`` is what tells them apart.

    Only the pressure block is touched.  Temperature has a Dirichlet condition
    wherever a buoyant case has a wall, so its level is pinned by the case and
    removing its mean would delete a real part of every Newton step.
    """
    if self._gauge_free is None:
        raise RuntimeError(
            "gauge() before resolve_gauge(): whether the constant pressure "
            "mode is a null mode is a property of the case, and guessing it "
            "either way silently stalls the other case")
    if self._gauge_free:
        pressure = x[self.velocity:self.pressure]
        x = torch.cat((x[:self.velocity], pressure - pressure.mean(),
                       x[self.pressure:]))
    pairs = getattr(self, "_layer_pairs", None)
    if pairs is not None:
        x = layer_mean(x, pairs, self.n_cell)
    return x


def layer_mean(x: torch.Tensor, pairs, n_cell: int) -> torch.Tensor:
    """``x`` with every unknown replaced by its mean over the two cell layers (``pairs = (a, b)``, interior rows).

    The projection onto the spanwise-invariant subspace of a two-layer cyclic extrusion: in each field block of
    ``n_cell`` rows, row ``a[i]`` and row ``b[i]`` are both set to their mean.  Linear and idempotent; a vector that is
    already layer-invariant comes back unchanged up to rounding.  A tensor whose size is not a whole number of field
    blocks (not a state or a step) is returned as is."""
    a, b = pairs
    if x.numel() == 0 or x.numel() % int(n_cell):
        return x
    blocks = x.reshape(-1, int(n_cell))
    mean = 0.5 * (blocks.index_select(1, a) + blocks.index_select(1, b))
    out = blocks.clone()
    out[:, a] = mean
    out[:, b] = mean
    return out.reshape(x.shape)


def enable_spanwise_invariance(self) -> int:
    """Opt-in: restrict Newton to the spanwise-invariant subspace of a two-layer cyclic extrusion.

    What it does.  A 2D problem solved on a mesh extruded into two cell layers with cyclic front and back patches has
    the same root in both layers.  This pairs the interior cells of the two layers by equal (x, y) and makes ``gauge``
    (which projects the initial iterate, every Krylov step and every trial state) also replace every unknown by its
    two-layer mean.  The residual, the Jacobian and the certificate are the full ones; only the iterates are kept in
    the subspace that holds the root.

    Why.  On two cyclic layers both spanwise neighbours of a cell are the same cell, so the cell gradients of a
    layer-antisymmetric field vanish in z and the diffusion terms built from them barely act on that mode.  The
    Jacobian is then close to singular in the antisymmetric directions: one exact solve from a layer-invariant state
    returns a step whose layers differ at 1e-8, and the nonlinear turbulence transient grows that difference until
    the two layers carry unrelated fields.  The projection removes the mode, which a 2D discretisation never has.

    Refuses a mesh that is not exactly two cell layers paired cell by cell.  Returns the number of pairs."""
    host = self.solver
    pos = host.cpd_cell_pos.reshape(-1, host.cpd_cell_pos.shape[-1]).index_select(0, self.interior)
    pos = pos.detach().to(torch.float64)
    scale = float(pos.abs().max()) or 1.0
    key = torch.round(pos / (1e-9 * scale))
    levels = torch.unique(key[:, 2])
    if levels.numel() != 2:
        raise ValueError(f"spanwise invariance needs exactly two cell layers; this mesh has {levels.numel()}")
    rows = []
    for level in levels:
        layer = (key[:, 2] == level).nonzero(as_tuple=True)[0]
        layer = layer[torch.argsort(key[layer, 1], stable=True)]
        layer = layer[torch.argsort(key[layer, 0], stable=True)]
        rows.append(layer)
    a, b = rows
    if a.numel() != b.numel() or not torch.equal(key[a, :2], key[b, :2]):
        raise ValueError("spanwise invariance: the two cell layers do not pair cell by cell in (x, y)")
    self._layer_pairs = (a.to(self.interior.device), b.to(self.interior.device))
    return int(a.numel())
