"""What a discretisation must declare before this solver will drive it.

All four refusals run once, from ``CollocatedSolver.__init__``, and each names
the failure it was measured against rather than a convention it enforces: a
block order the preconditioner reads by arithmetic, a Green-Gauss gradient that
is zeroth order on an irregular mesh, face-control-volume rows that make the
system overdetermined, and a 2D case whose out-of-plane rows must be empty
before ``casestate.close_the_out_of_plane_rows`` may replace them.

Read off the ``CollocatedSolver`` passed in: ``unknowns``, ``solver.params``,
``solver.momentum_residual_has_face_rows``, ``_flat_axes``, ``n_unknowns``,
``dtype``, ``device``, and ``scatter_state``.
"""
from __future__ import annotations

import torch

from Pipeline.CoupledNewton.casestate import _eager

# Gradient schemes whose SOLUTION error is second order on an irregular mesh.
# Green-Gauss is excluded on purpose and by measurement, not by taste.
SECOND_ORDER_GRADIENTS = ("1st", "2nd", "3rd", "4th")

# Rows and columns are FIELD-BLOCKED: x = [u | v | w | p | ...], r = [mom_x |
# mom_y | mom_z | cont | ...], each block N long.  The alternative (interleaved
# by cell) makes the Jacobian blocks a strided mess; this way F, G, D and C are
# contiguous index ranges and the block preconditioner needs no permutation
# anywhere.  HOW MANY blocks there are is the case's answer, not this module's:
# the discretisation declares it in ``BaseSolver.COLLOCATED_UNKNOWNS``, so the
# isothermal system is four and the Boussinesq one is five without a second
# constant here that could disagree with the first.
#
# The velocity block leads and the pressure block follows it, in every solver
# that has a square form.  The preconditioner below is written against exactly
# that layout -- F, G, D, C are its four corners -- and ``refuse_a_layout_the_
# preconditioner_cannot_read`` is what turns the assumption into a check.
VELOCITY_WIDTH = 3


def refuse_a_layout_the_preconditioner_cannot_read(self) -> None:
    """Refuse a square form whose first four blocks are not ``[u, v, w, p]``.

    Everything downstream of here reads the layout by ARITHMETIC -- the gauge
    projects ``x[3N:4N]``, the block preconditioner cuts F, G, D and C at
    ``3N`` and ``4N``, and the pressure Laplacian is built for the block at
    ``3N``.  A solver that declared, say, temperature before pressure would
    run all of it silently on the wrong block and converge something that is
    not the case's equations.  Nothing here forbids blocks AFTER the pressure;
    that is how the energy row joins.

    **The second block is identified by its RESIDUAL, not by its width and not
    by its name.**  A width-only predicate cannot see the one swap that
    matters: ``[U(3), p_rgh(1), T(1)]`` and ``[U(3), T(1), p_rgh(1)]`` are both
    ``[3, 1, 1]``, so a widths-only check would accept the second of them
    -- against exactly the failure its own paragraph above describes.  The name
    cannot do the job either, because it legitimately varies (``p`` here,
    ``p_rgh`` in the Boussinesq solver) and would become a second list to keep
    in sync.  What does not vary is which equation the block is tested
    against: the pressure column is the one continuity is written for.
    """
    if not self.unknowns:
        raise ValueError(
            f"{type(self.solver).__module__} declares no COLLOCATED_UNKNOWNS, "
            "i.e. it has no square cell-centred form.  The collocated Newton "
            "solver drives an existing discretisation and cannot invent one")
    widths = [width for _, width, _, _ in self.unknowns]
    if widths[0] != VELOCITY_WIDTH or len(widths) < 2 or widths[1] != 1:
        raise ValueError(
            f"COLLOCATED_UNKNOWNS starts {widths}; this solver reads the "
            f"layout arithmetically and needs a {VELOCITY_WIDTH}-wide "
            "velocity block first and a 1-wide pressure block second")
    if self.unknowns[1][3] != "residual_continuity":
        raise ValueError(
            f"COLLOCATED_UNKNOWNS puts {self.unknowns[1][0]!r} "
            f"({self.unknowns[1][3]}) in the second block, where this solver "
            "gauges the constant mode and cuts the F/G/D/C corners and the "
            "pressure Laplacian.  That block must be the pressure, i.e. the "
            "one paired with residual_continuity; the widths alone cannot "
            "tell a pressure from any other scalar")


def field_names(self) -> tuple:
    """The case fields this solver carries unknowns for, in block order."""
    return tuple(name for name, _, _, _ in self.unknowns)


def refuse_a_zeroth_order_gradient(self) -> None:
    """Refuse Green-Gauss, which is what the whole collocated argument rests on.

    Syrakos et al. (2017) measure Green-Gauss at zeroth order on general
    unstructured meshes and show the finite-volume method inherits that order.
    A collocated solver on a GG gradient is therefore strictly worse than the
    staggered scheme it replaces, so serving one silently would invert the
    reason this file exists.  It stays reachable through the case's own
    ``gradSchemes`` for the MMS control arm, which is why this is a method and
    not an unconditional raise.
    """
    scheme = str(self.solver.params.fvschemes.get("gradSchemes", "1st"))
    if scheme not in SECOND_ORDER_GRADIENTS:
        raise ValueError(
            f'[fvSchemes] gradSchemes = "{scheme}" is not a least-squares '
            "gradient.  A collocated cell-centred scheme keeps second-order "
            "SOLUTION error on an irregular mesh only through the WLSQ "
            "gradient; Green-Gauss is zeroth order there and the "
            "discretisation inherits it.  Valid: "
            + ", ".join(SECOND_ORDER_GRADIENTS))


def refuse_the_overdetermining_face_rows(self) -> None:
    """Refuse ``momentumResidual`` values that add face-control-volume rows.

    Those rows are how the ODIL/GNN objective sees the grid-parity pressure
    mode, and they make the system taller than it is wide on purpose.  This
    solver gets the same coupling from the momentum-weighted flux instead, and
    carrying both would give up the square Newton structure for nothing: the
    two are alternatives (survey Sec. 6.5).
    """
    if self.solver.momentum_residual_has_face_rows:
        raise ValueError(
            '[fvSchemes] momentumResidual adds face-CV rows, which makes the '
            'system overdetermined; a square Newton solve needs '
            'momentumResidual = "cell" and takes its pressure-velocity '
            'coupling from momentumInterpolation = "MWI" instead')


def refuse_a_flat_axis_that_still_carries_an_equation(self) -> None:
    """Check that a 2D case's out-of-plane momentum rows really are empty.

    ``close_the_out_of_plane_rows`` REPLACES those rows, so if the
    discretisation did write an equation there this solver would be silently
    discarding it and converging a different problem.  Two probes, one of them
    the zero state that a cold start actually begins from, and the rows must
    be exactly 0.0 at both -- not small, zero, because the claim is that the
    assembly never touched them.
    """
    if not self._flat_axes:
        return
    generator = torch.Generator(device="cpu").manual_seed(0)
    for scale in (0.0, 1.0):
        probe = (scale * torch.randn(self.n_unknowns, generator=generator,
                                     dtype=self.dtype)).to(self.device)
        state = self.scatter_state(probe)
        with _eager():
            self.solver.forward(phi_old_cpd_cell=torch.zeros_like(state),
                                phi_new_cpd_cell=state)
        for axis in self._flat_axes:
            # The momentum residual's attribute name is the fourth field of
            # the discretisation's own COLLOCATED_UNKNOWNS entry, so read it
            # from there rather than hard-coding "residual_momentum" -- the
            # seam already carries the name and spelling it twice is how the
            # two drift.
            momentum = getattr(self.solver, self.unknowns[0][3])
            worst = float(momentum[..., axis].abs().max())
            if worst != 0.0:
                raise ValueError(
                    f"[solver] dim = {VELOCITY_WIDTH - len(self._flat_axes)} "
                    f"says axis {axis} carries no momentum equation, but the "
                    f"assembled residual reads {worst:.3e} there at a probe of "
                    f"scale {scale}.  Substituting that row would throw the "
                    "equation away")
