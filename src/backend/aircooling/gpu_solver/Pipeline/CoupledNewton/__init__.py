"""Coupled Newton — the steady square solve, as one paradigm package.

The unknowns are the cell-centred fields a case's discretisation declares in
``BaseSolver.COLLOCATED_UNKNOWNS`` — ``[u, v, w, p_rgh]`` for the isothermal
system, plus ``T`` for the Boussinesq one — and the system is SQUARE: as many
equations as unknowns, solved by Newton with an assembled Jacobian, GMRES and
AMG rather than by optimising a residual norm.

This is the fourth paradigm package, alongside ``src/Pipeline/ODIL/``. It consumes the
shared substrate through a declared seam and imports nothing from another
paradigm; its own non-stdlib imports are ``torch`` and four ``SparseSolver``
modules, and nothing from ``FVsolver``, ``FVdomain``, ``Utils``,
``Post_process`` or ``Diagnostics``.

WHAT IT ASKS OF A DISCRETISATION, and the whole list is public API since
callers rely on it: ``COLLOCATED_UNKNOWNS``; ``forward``; the momentum-interpolation
seam (``momentum_residual_has_face_rows``, ``momentum_interpolation_is_mwi``,
``configure_momentum_interpolation``); ``face_pressure_coefficient`` and
``face_volumetric_flux``; and paradigm-neutral mesh geometry (``cell_volume``,
``mask_interior_cell``, ``neighbor_cpd_cell``, ``num_cpd_cells``, ``num_faces``,
``face_area``, ``grad_p_coeff``, ``nonOrthDeltaCoeffs``, ``C_senders``,
``F_receivers``).

The name is deliberately not ``NewtonKrylov``: this solver ASSEMBLES its
Jacobian, and that name is conventionally reserved for matrix-free JFNK, which
is what ``src/Pipeline/ODIL/trainer_jfnk.py`` does.

Entry point: ``src/entries/solve_with_coupled_newton.py``.

Time marching: ``src/entries/solve_transient_with_coupled_newton.py`` runs one such
square solve per implicit BDF1/BDF2 step.  ``timestepping`` holds the levels and the BDF
weights; the residual takes the old level through ``CollocatedSolver.set_old_state`` (``None``,
the steady default, is the zero old state of every steady caller).  It adds ``dt_cell`` and
``unsteady_coeff`` to the seam above, and for its Courant number the face-to-cell geometry
``momentum_diagonal`` already reads.  Its LINEAR schemes (``--scheme sbdf1/sbdf2/oseen1/oseen2``,
one GMRES solve per step, ``timestepping.solve_linear_step``) add, and only they call:
``set_convection_treatment``, ``set_explicit_source``, ``explicit_convection``,
``mwi_face_flux``, ``set_ap_mode`` and ``set_bc_old_state`` (the last also under
``--bc-old-level true`` on a BDF march).  Every one of them is inert until called.

JFNK mode (``jfnk``): the Krylov operator exact at every Newton step (the assembled
matrix or the JVP of the residual) and the preconditioner rebuilt by a policy.  Its forward-mode
JVP (``jacobian.linearise_forward``, ``--jvp forward``) reads one OPTIONAL seam method,
``forward_mode_derivatives()`` -- a context manager under which ``forward`` is differentiable by
forward-mode AD (``BaseSolver``'s switches the WLSQ gradient off its custom autograd Function);
a discretisation without it is evaluated as is.
"""

from Pipeline.CoupledNewton.collocated_newton import (  # noqa: F401
    CollocatedSolver,
    promote_case_to_double,
)

__all__ = ["CollocatedSolver", "promote_case_to_double"]
