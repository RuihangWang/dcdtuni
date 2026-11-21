"""Which linear system the inner solve is posed on, and how its answer comes back.

A SCALING is not a preconditioner, and the distinction is the reason this is a
module and not a third entry in ``PRECONDITIONER_SHAPES``.  Right-preconditioned
GMRES (``SparseSolver/gmres.py``) runs on ``A M`` and its recurrence residual is
the residual of the ORIGINAL system, so a preconditioner changes how fast the
iteration falls and changes nothing about what "``inner_rel`` = 0.998" means.  A
scaling replaces the system: GMRES is handed ``(Dr J Dc) y = Dr b``, minimises a
residual measured in scaled units, and returns a ``y`` that is not the Newton
step until it is multiplied back by ``Dc``.  Two things therefore have to be
carried by whoever poses the scaled system -- the recovery, and the fact that
the reported inner residual is no longer comparable to an unscaled one -- and
both live here rather than being left to the call site to remember.

The failure mode this guards against is on the record: a run that looks
converged because the number being watched was measured in the scaled system.
``check_recovery`` is the guard, it is an exception and not a comment, and it
fires on any mismatch between the operator GMRES ran on and the step handed
back to the Newton loop.

Two scalings are implemented, both as a ``(row, col)`` pair of elementwise
MULTIPLIERS -- the scaled operator is ``diag(row) J diag(col)`` and the step is
``col * y``:

``turbulence_block_scale``  one scalar per post-pressure block, symmetric
``ruiz_scaling``            two full vectors, from Ruiz's infinity-norm sweeps
"""
from __future__ import annotations

import torch

# Every scaling this module builds, i.e. the valid ``equilibrate`` settings.
EQUILIBRATIONS = ("off", "ruiz")

# Ruiz sweeps.  Measured on the four captured true-regime flat-plate
# systems (docs/research/2026-08-17-inner-solve-true-regime.md section 6), true
# |b - Jx|/|b| after the 1600-matvec budget on ``turbregime_sst_late``:
#
#     sweeps      1        3        10       30
#     floor    9.79e-01  1.03e-01  1.26e-01  1.26e-01
#
# and 2.96e-04 / 4.96e-05 / 4.50e-05 / 4.51e-05 on ``turbregime_sst_early``.
# THREE IS ENOUGH AND ONE IS NOT; 30 buys nothing over 10.  The probe's own
# arms all ran at 10, so 3 is the cheaper end of a flat optimum rather than a
# setting anything was measured to need.
RUIZ_SWEEPS = 3

_TINY = 1.0e-300


def _column_amax(index: torch.Tensor, source: torch.Tensor,
                 size: int) -> torch.Tensor:
    """``max |source|`` per index, and 0 where an index received nothing."""
    out = torch.zeros(size, dtype=source.dtype, device=source.device)
    return out.scatter_reduce_(0, index, source, reduce="amax",
                               include_self=False)


def _row_index(matrix: torch.Tensor) -> torch.Tensor:
    crow = matrix.crow_indices()
    return torch.repeat_interleave(
        torch.arange(matrix.shape[0], device=matrix.device),
        crow[1:] - crow[:-1])


def ruiz_scaling(matrix: torch.Tensor, sweeps: int = RUIZ_SWEEPS):
    """Two-sided infinity-norm equilibration, as ``(row, col)`` multipliers.

    Ruiz's iteration (2001): repeatedly divide each row and each column by the
    square root of its own current infinity norm, so the scaled matrix's row
    and column infinity norms both converge to 1.  Convergence is geometric and
    the shipped default does NOT complete it -- measured through this function
    on the captured `turbregime_sst_late` Jacobian (9.22M nonzeros), whose raw
    row/column inf-norm spans are 1.3735e4 / 1.3722e4:

        sweeps        1            3           10
        spans      18.9 / 26.8   1.73 / 2.28   1.004 / 1.006

    Ten sweeps is what reaches 1; three is what the inner-solve floor wants
    (``RUIZ_SWEEPS``), and the two are different questions.  The 1.000-1.006
    figure on record is the ten-sweep one.

    **This was recorded as a dead arm and it is not.**  At the laminar iterate
    every earlier measurement was taken on, it loses (true floor 0.7703 against
    0.1734 for no preconditioner at all).  On four systems captured from the
    march that matters -- the R1 flat-plate arms, 34-37% of cells above
    ``nu_t > nu`` -- it is the best arm measured, by 8x to 227x, and it is the
    only one that reaches the Eisenstat-Walker forcing target at the state that
    march parks on (0.0712 at restart 1600, where the shipped shapes read
    0.92-1.00 and no preconditioner reads 0.997).

    Two things that are known and one that is not.  Known: the reversal is
    carried by the OPERATOR, not by the right-hand side (one shared
    field-balanced random ``b`` gives ``eq_ruiz/none`` = 9.19 on the laminar
    matrix against 0.39 / 0.31 on the turbulent ones), and most of the win is
    on the COLUMN side (column-only reaches 0.353 at the parked state where
    row-only is worse than doing nothing).  NOT known: why.  The obvious
    explanation is measured and false twice over -- the turbulent matrices are
    if anything BETTER scaled than the laminar one (row/column inf-norm spans
    1.4e4 against 1.9e4), and holding the state fixed while scaling ``nu_t``
    down to a peak of 0.35 nu leaves Ruiz winning 4.9x.  Do not repeat "because
    the eddy viscosity is large" as if it were established.

    ``sweeps`` is the only knob and the optimum is flat; see ``RUIZ_SWEEPS``.
    A row or column with no stored entry keeps its multiplier at 1 rather than
    being clamped to a floor and inverted into 1e150 -- on a matrix with no
    empty line the two are the same arithmetic.
    """
    rows, columns = matrix.shape
    row_index = _row_index(matrix)
    column_index = matrix.col_indices()
    magnitude = matrix.values().abs()
    dr = torch.ones(rows, dtype=magnitude.dtype, device=matrix.device)
    dc = torch.ones(columns, dtype=magnitude.dtype, device=matrix.device)
    # Swept in nonzero CHUNKS.  The one-pass spelling materialised FOUR
    # nnz-length float64 temporaries per sweep (``dr[row_index]``,
    # ``dc[column_index]``, their product and ``scaled``), which is 22.2 GiB per
    # sweep at the 746,382,257 nonzeros of the fine periodic-hills rung and made
    # this function -- 0.2 seconds of a 180-second Newton step -- the second
    # thing that would not fit on a 96 GB card (hills memory report).
    #
    # The arithmetic is unchanged.  ``amax`` is associative, so taking it per
    # chunk into a shared accumulator gives the same maxima as one pass; the
    # accumulator starts at zero and ``include_self=True`` is equivalent to the
    # one-pass ``include_self=False`` here because every contribution is
    # non-negative (``magnitude`` is an absolute value and ``dr``, ``dc`` stay
    # positive), so a line that receives nothing still reads 0.
    chunk = 1 << 26
    nonzeros = magnitude.numel()
    for _ in range(int(sweeps)):
        row_norm = torch.zeros(rows, dtype=magnitude.dtype,
                               device=magnitude.device)
        column_norm = torch.zeros(columns, dtype=magnitude.dtype,
                                  device=magnitude.device)
        for lo in range(0, nonzeros, chunk):
            hi = min(lo + chunk, nonzeros)
            piece_row = row_index[lo:hi]
            piece_column = column_index[lo:hi]
            scaled = dr.index_select(0, piece_row)
            scaled.mul_(dc.index_select(0, piece_column))
            torch.div(magnitude[lo:hi], scaled, out=scaled)
            row_norm.scatter_reduce_(0, piece_row, scaled, reduce="amax",
                                     include_self=True)
            column_norm.scatter_reduce_(0, piece_column, scaled, reduce="amax",
                                        include_self=True)
        dr = dr * torch.where(row_norm > 0, row_norm,
                              torch.ones_like(row_norm)).sqrt()
        dc = dc * torch.where(column_norm > 0, column_norm,
                              torch.ones_like(column_norm)).sqrt()
    return 1.0 / dr, 1.0 / dc


def turbulence_block_scale(self, blocks):
    """Per-block symmetric scaling of the rows/columns PAST the pressure.

    Why: the transported turbulence rows (k, and
    omega-tilde or epsilon) live on different scales from momentum -- on the
    flat plate at the freestream init the omega-row residual is 75x the
    momentum one and the epsilon-row 16000x -- and Osusky's and Yildirim's
    stated precondition for coupled-RANS conditioning is scaling them.  This
    equilibrates each post-pressure block's Jacobian diagonal to the momentum
    block's, symmetrically: block b gets one scalar s_b = sqrt(med|diag_F| /
    med|diag_b|) applied to both its rows and its columns, so the scaled
    diagonal lands on the momentum magnitude and the damped-Jacobi sweep on
    the R block is exactly equivariant (one scalar per block cancels in
    D^-1 R).  The momentum and pressure blocks are left at 1: their relative
    scale is what every shipped measurement of this solver was taken at.

    Measured a near-no-op on every system it has been run on (true floor 0.1748
    against 0.1734 unscaled at the laminar iterate, 1.0-1.3x on the four
    turbulent ones), which is what one scalar per block can be expected to do
    next to Ruiz's two full vectors.
    """
    from Pipeline.CoupledNewton.preconditioner import _csr_diagonal
    diagonal = _csr_diagonal(blocks["J"])
    scale = torch.ones_like(diagonal)
    reference = diagonal[:self.velocity].abs().median()
    offset = self.pressure
    for _, width, _, _ in self.unknowns[2:]:
        rows = width * self.n_cell
        block = diagonal[offset:offset + rows].abs().median()
        scale[offset:offset + rows] = (
            reference / block.clamp_min(_TINY)).sqrt()
        offset += rows
    return scale, scale


def scaled_matrix(matrix: torch.Tensor, row: torch.Tensor,
                  col: torch.Tensor) -> torch.Tensor:
    """``diag(row) J diag(col)``: the values rescaled, the pattern shared.

    Prescaling the values rather than wrapping the matvec as
    ``row * (J @ (col * v))`` costs one values-sized buffer per linearisation
    and nothing per Krylov vector, and it is the form every floor in the
    record was measured on.  It is also the form the argument is about: the
    point of equilibration is that the ENTRIES being summed are O(1), which
    a scaling applied outside the sum does not deliver.
    """
    return torch.sparse_csr_tensor(
        matrix.crow_indices(), matrix.col_indices(),
        matrix.values() * row[_row_index(matrix)] * col[matrix.col_indices()],
        size=matrix.shape)


def _apply(operator, vector: torch.Tensor) -> torch.Tensor:
    """``operator`` as a linear map, whether it is a matrix or a callable.

    A matrix-free Newton step is solved with a JVP closure in place of the CSR
    matrix, and the recovery check below has to be taken against the operator
    the inner solve ACTUALLY ran on -- checking it against an assembled stand-in
    would test the stand-in.  A sparse tensor is not callable, so the two cases
    separate without a flag.
    """
    return operator(vector) if callable(operator) else operator @ vector


def check_recovery(matrix, scaled, row: torch.Tensor, y: torch.Tensor,
                   step: torch.Tensor, rhs: torch.Tensor, *,
                   tol: float = 1.0e-4) -> float:
    """Refuse a step that is not the scaled solve's answer in ORIGINAL variables.

    ``matrix`` and ``scaled`` are the unscaled and scaled operators, each
    either a CSR matrix or a callable (see ``_apply``).

    ``scaled @ y`` is the product GMRES actually iterated on; ``row * (J @
    step)`` is the same product taken through the recovered step.  They are
    equal for every ``y`` if and only if ``step = col * y``, so this is a test
    of the recovery itself and not a restatement of it: recovering with the ROW
    multiplier, or with the two swapped, moves the two sides by O(1) while fp64
    rounding moves them by ~1e-14 of the right-hand side's norm.  The tolerance
    is four orders looser than that because the two sides sum the same terms in
    different units and do not share their cancellation.

    Returns the TRUE relative linear residual ``|b - J dx| / |b|``, in the
    original variables -- the number that is comparable with an unscaled run's
    ``inner_rel``, and the one a scaled run must be judged on.  GMRES's own
    recurrence residual is measured in the scaled system and on these systems
    is regularly the PESSIMISTIC one (4.50e-05 true against 2.42e-04 scaled),
    which is exactly why both are logged.

    **It can also be OPTIMISTIC, without bound, and that is a property of the
    scaling rather than of the solve.**  Measured on the TMR flat
    plate 273x193 rung (626,688 unknowns, no preconditioner) at a captured state
    with ``||r||2`` 2.93e-07, three inner solves of the SAME system:

    ==============================  ==================  ==============  =============
    solve                           recurrence, scaled  TRUE, scaled    TRUE, original
    ==============================  ==================  ==============  =============
    ``restart 1600, max_cycles 4``  0.9727904988282509  0.972790498828  0.9723
    ``restart 1600, max_cycles 1``  0.9924073518567142  0.992407351857  1.0040
    ``restart 4400, max_cycles 1``  0.8817736448879382  0.881773644887  **6.0497**
    ==============================  ==================  ==============  =============

    The middle column is ``GmresResult.cycle_residuals[-1]``, a freshly computed
    residual of the scaled system, and it agrees with the recurrence to 3.2e-15,
    5.0e-15 and 5.8e-13 relative -- **so GMRES is exact and the divergence is
    ours**.  The third row's step reduces the SCALED residual by 12% while
    multiplying the original-variable residual by **6**, and the caller's line
    search then accepts no step at all.

    Ordering the three by how far each minimises the scaled residual orders them
    by divergence exactly (0.9924 -> 1.01x, 0.9728 -> 1.00x, 0.8818 -> 6.86x),
    which is the best-supported reading: **the harder a solve minimises the
    scaled residual, the more freedom it has to exploit the misalignment between
    the two norms.**  Three points at one state, consistent with all three, not
    established across states.

    Practical consequence, and the reason this is written here rather than in a
    research note: **an equilibrated run must never be judged on the logged
    ``inner_rel``.**  That column is faithful to the scaled system and can be
    disconnected from the quantity the Newton step is judged on by any factor.
    Read the ``true`` value beside it, which is this return.
    """
    through_operator = _apply(scaled, y)
    true_product = _apply(matrix, step)
    reference = float(through_operator.norm())
    mismatch = float((through_operator - row * true_product).norm())
    if not mismatch <= tol * max(reference, _TINY):
        raise AssertionError(
            "the recovered Newton step is not the scaled solve's answer in "
            f"the original variables: |Ds J step - A_s y| / |A_s y| = "
            f"{mismatch / max(reference, _TINY):.3e} against a {tol:.0e} "
            "tolerance.  The step must be the COLUMN multiplier times the "
            "scaled solve's answer.")
    return float((rhs - true_product).norm()) / max(float(rhs.norm()), _TINY)
