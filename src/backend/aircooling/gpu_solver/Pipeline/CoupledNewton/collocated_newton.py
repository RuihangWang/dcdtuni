"""Square COLLOCATED coupled Newton: ``[u, v, w, p, ...]`` per interior cell.

As many unknowns as residual rows per cell, so the Jacobian is square and one
Newton step is one linear solve.  HOW MANY is the discretisation's answer, given
by its ``COLLOCATED_UNKNOWNS``: four for isothermal momentum and continuity,
five once a Boussinesq energy row and its temperature join them.  Nothing below
carries a field count of its own, because two counts that must agree are one
count too many.  The unknowns live at cell centres, which is the
whole reason this file exists rather than a staggered arrangement (the
measurement that rules it out is in
``docs/research/2026-08-13-coupled-collocated-discretisation.md``
and the head-to-head in the MMS notes): on genuinely irregular meshes a
collocated cell-centred scheme with a
LEAST-SQUARES gradient converges its SOLUTION error at second order even though
its truncation error does not converge at all (Diskin & Thomas 2009), while the
staggered/covolume family is first order there by its own authors' analysis
(Zhang, Schmidt & Perot 2002, who call it "intolerably inaccurate for arbitrary
meshes").  The one way that advantage is lost is a Green-Gauss gradient, which is
zeroth order on general unstructured meshes and drags the discretisation down
with it (Syrakos 2017) -- so a case reaching this solver runs WLSQ, and
``refuse_a_zeroth_order_gradient`` below turns that from a convention into a
check.

**The discretisation is not written here.**  ``picoext`` already is a collocated
cell-centred discretisation with the repository's whole ``[fvSchemes]`` surface --
convection, diffusion, gradient, and every boundary condition in
``FVdomain/SetBC/onthefly`` -- and it already publishes ``residual_momentum`` and
``residual_continuity``.  ``buoyantBoussinesqpicoext`` is the same discretisation
with a Boussinesq energy equation, and it already publishes ``residual_energy``
next to those two.  This module drives either one as a pure function of a flat
state vector and adds the three things a square Newton solve needs and a loss
does not: the linearisation, the linear solve, and the pressure gauge.

The Boussinesq solver works in ``p_rgh``, not ``p``: it assembles momentum with
no pressure term and then adds ``grad(p_rgh) V``, with the hydrostatic part
carried by the buoyancy source ``gh beta grad(T) V``.  The unknown this solver
carries is therefore ``p_rgh``, and the case's ``bc/`` directory holds
``p_rgh.toml``.  Solving for ``p`` against a residual written in ``p_rgh`` would
count the hydrostatic component twice.

Three properties of the seam were measured rather than assumed, and each is a
gate in ``tests/test_collocated_newton.py``:

*Purity.*  Two evaluations at the same ``x`` agree to the last bit under
``torch.use_deterministic_algorithms``, and differ by ~1e-19 out of ~1e-3 without
it -- CUDA scatter atomics reordering, not hidden state.  The solver turns
determinism on for its own sake: a Newton line search that compares
``||r(x + lam dx)||`` against ``||r(x)||`` needs the comparison to mean something.

*Differentiability.*  With a smooth convection scheme the residual is exactly
QUADRATIC in ``x`` (convection is bilinear, everything else linear), so a central
difference reproduces the Jacobian-vector product to roundoff at EVERY step size
rather than converging at order 2 -- measured 1.2e-14 at h = 1e-1.  With
``Upwind`` or ``LinearUpwind`` it does not: those carry a ``sign(F_f)`` switch, the
residual is only piecewise quadratic, and a finite difference that steps across a
switch is wrong by O(1e-3) until h falls below ~1e-4.  That non-smoothness is a
property of the scheme, not of this seam.

*Squareness needs the momentum-weighted flux.*  ``picoext``'s continuity row is the
divergence of a LINEARLY interpolated face velocity, which is blind to the
grid-parity pressure mode -- measured directly: a checkerboard pressure field with
zero velocity produces a continuity residual of exactly 0.0.  The ODIL/GNN
paradigm answers that with extra face-control-volume rows, i.e. by making the
system OVERDETERMINED on purpose; a square system has no such rows and must get
the coupling from the flux itself.  Hence ``momentumInterpolation = "MWI"``, whose
correction takes that same checkerboard to 1.95e-1.  The face-CV rows are the
ALTERNATIVE to this, not a complement, and carrying both would re-overdetermine
the system; ``momentumResidual`` must therefore be ``"cell"`` here, and
``CLAUDE.md``'s ``"cell+face_cv"`` mandate governs the ODIL/GNN paradigm and is
untouched by that.

**What acceptance means changed with it.**  The Rhie-Chow term does not vanish at
convergence on a fixed mesh (Mangani 2016), so driving ``max|r|`` to 1e-14 here
certifies that the STABILISED system was solved, not that the answer is right.
The instrument that survives is solution-error order under mesh refinement.
"""
from __future__ import annotations

import functools
import os

import torch

from Pipeline.CoupledNewton import casestate as _casestate
from Pipeline.CoupledNewton import contract as _contract
from Pipeline.CoupledNewton import gauge as _gauge
from Pipeline.CoupledNewton import jacobian as _jacobian
from Pipeline.CoupledNewton import logvars as _logvars
from Pipeline.CoupledNewton import newton as _newton
from Pipeline.CoupledNewton import preconditioner as _preconditioner

# Re-exported because this module path IS the public one: 18 experiment probes,
# src/entries/solve_with_coupled_newton.py and two test modules import these
# names from ``CoupledNewton.collocated_newton``, the facade over the
# split into concerns.  A caller that wants the concern imports the concern; a
# caller that wants the solver keeps the one import it already had.
from Pipeline.CoupledNewton.casestate import promote_case_to_double  # noqa: F401
from Pipeline.CoupledNewton.checkerboard import (  # noqa: F401
    NotAParityLattice, grid_parity_amplitude,
)
from Pipeline.CoupledNewton.contract import (  # noqa: F401
    SECOND_ORDER_GRADIENTS,
    VELOCITY_WIDTH,
)
from Pipeline.CoupledNewton.gauge import (  # noqa: F401
    GAUGE_FREE_BELOW,
    GAUGE_PINNED_ABOVE,
)
from Pipeline.CoupledNewton.equilibration import EQUILIBRATIONS, RUIZ_SWEEPS  # noqa: F401
from Pipeline.CoupledNewton.preconditioner import (  # noqa: F401
    NEWTON_PRECONDITIONERS,
    PRECONDITIONER_SHAPES,
    PRESSURE_OPERATORS,
    RESTORE_ALPHA,
    _csr_diagonal,
)


class CollocatedSolver:
    """One steady incompressible case as a square nonlinear system ``r(x) = 0``."""

    def __init__(self, case_solver, *, device=None, momentum_interpolation=None,
                 coefficient=None, deterministic: bool = True):
        self.solver = case_solver
        self.device = device or case_solver.cell_volume.device
        self.dtype = case_solver.cell_volume.dtype
        if self.dtype != torch.float64:
            raise ValueError(
                f"the case's geometry is {self.dtype}; call "
                "promote_case_to_double(case) first -- a Newton residual "
                "measured against a 1e-10 gate cannot be carried in float32")

        interior = case_solver.mask_interior_cell.reshape(-1).nonzero(
            as_tuple=True)[0]
        self.interior = interior
        self.n_cell = int(interior.numel())
        self.unknowns = tuple(getattr(case_solver, "COLLOCATED_UNKNOWNS", ()))
        self.refuse_a_layout_the_preconditioner_cannot_read()
        self.num_fields = sum(width for _, width, _, _ in self.unknowns)
        self.n_unknowns = self.num_fields * self.n_cell
        self.velocity = VELOCITY_WIDTH * self.n_cell
        self.pressure = (VELOCITY_WIDTH + 1) * self.n_cell

        # Velocity components the DISCRETISATION writes no momentum equation for.
        # A ``dim = 2`` case assembles momentum for x and y only, so the z rows and
        # the z columns of the Jacobian are both identically empty and the square
        # system is singular with the whole w block in its null space.  See
        # ``close_the_out_of_plane_rows``.
        self._flat_axes = tuple(range(int(getattr(case_solver, "dim", 3)),
                                      VELOCITY_WIDTH))

        self.refuse_a_zeroth_order_gradient()
        self.refuse_the_overdetermining_face_rows()
        case_solver.configure_momentum_interpolation(
            scheme=momentum_interpolation, coefficient=coefficient)
        self.mwi = case_solver.momentum_interpolation_is_mwi

        if deterministic and os.environ.get("LPCNS_DEBUG_NONDETERMINISTIC") != "1":
            # A backtracking line search decides on ||r(x + lam dx)|| < ||r(x)||.
            # Without this the two sides of that comparison disagree at ~1e-16
            # relative from CUDA scatter atomics, which is invisible early and
            # decides the step once the residual is near its floor.
            # LPCNS_DEBUG_NONDETERMINISTIC=1 (a diagnostic, never
            # in a timed or reported solve) leaves the mode off: every CUDA scatter
            # then sums in the order its atomics land (CHT0 README section 14).
            torch.use_deterministic_algorithms(True, warn_only=True)

        self._sparsity = None
        # Whether the ASSEMBLED matrix is the Krylov operator.  True is the safe
        # answer and the one every caller but ``solve_newton(matrix_free=True)``
        # keeps: it makes ``build_sparsity`` refuse a coupling whose derivative
        # a mesh-hop colouring cannot represent, rather than drop it silently.
        self.assembly_is_operator = True
        self._plan = None
        self._gauge_free = None
        # Armed and disarmed by ``solve_newton(ptc=True)`` only; ``None`` is
        # the permanent state of every other caller (``residual`` documents
        # what a non-None value means).
        self._ptc_shift = None
        # Pinned rows of the staged cold start (``coldstart.pinned``, opt-in); ``None`` is the
        # permanent state of every other caller (``casestate.residual`` documents a non-None value).
        self._pin = None
        # Learned operator correction C_phi (``operator_correction.attach``, opt-in); ``None``
        # is the permanent state of every other caller (``casestate.residual`` documents a non-None value).
        self._operator_correction = None
        # The old time level ``residual`` hands ``forward``; ``None`` is the zero
        # old state every steady caller has always passed.  Armed only by
        # ``set_old_state`` (the transient entry).
        self._old_state = None
        self._zero_state = torch.zeros(
            case_solver.num_cpd_cells, self.num_fields, device=self.device,
            dtype=self.dtype)
        # Last, because it evaluates the residual and therefore needs everything
        # above it.
        self.refuse_a_flat_axis_that_still_carries_an_equation()

    # Every method below is the module-level function of the same name, bound
    # into the class rather than wrapped by a forwarding stub.  A stub would be
    # a second spelling of each signature and the two would drift; binding the
    # function is what keeps ``dir(CollocatedSolver)`` and every docstring
    # identical to the pre-split module while the bodies live with their
    # concern.  The functions take the solver as their first argument and are
    # callable directly -- ``jacobian.assemble(solver, x)`` -- which is what
    # makes each file's seam readable; none of them keeps state of its own.

    # ------------------------------------------------------------------ checks
    refuse_a_layout_the_preconditioner_cannot_read = (
        _contract.refuse_a_layout_the_preconditioner_cannot_read)
    field_names = _contract.field_names
    refuse_a_zeroth_order_gradient = _contract.refuse_a_zeroth_order_gradient
    refuse_the_overdetermining_face_rows = (
        _contract.refuse_the_overdetermining_face_rows)
    refuse_a_flat_axis_that_still_carries_an_equation = (
        _contract.refuse_a_flat_axis_that_still_carries_an_equation)

    # --------------------------------------------------------------- residual
    close_the_out_of_plane_rows = _casestate.close_the_out_of_plane_rows
    scatter_state = _casestate.scatter_state
    residual = _casestate.residual
    set_old_state = _casestate.set_old_state
    state_with_boundaries = _casestate.state_with_boundaries

    # --------------------------------------------------------------- assembly
    linearise = _jacobian.linearise
    linearise_forward = _jacobian.linearise_forward      # forward-mode JVP (JFNK)
    sparsity = property(_jacobian.sparsity)
    _probe_plan = _jacobian._probe_plan
    _fixed_pattern_map = _jacobian._fixed_pattern_map    # opt-in fixed-pattern map
    _block_assembly_map = _jacobian._block_assembly_map  # opt-in block assembly map
    assemble = _jacobian.assemble

    # -------------------------------------------------------- preconditioning
    compact_pressure_laplacian = _preconditioner.compact_pressure_laplacian
    simple_preconditioner = _preconditioner.simple_preconditioner

    # ---------------------------------------------------------------- gauging
    constant_pressure_defect = _gauge.constant_pressure_defect
    resolve_gauge = _gauge.resolve_gauge
    gauge = _gauge.gauge
    enable_spanwise_invariance = _gauge.enable_spanwise_invariance

    # ----------------------------------------------------------------- Newton
    @functools.wraps(_newton.solve_newton)
    def solve_newton(self, **kwargs):
        # Logarithmic turbulence unknowns (opt-in, ``logvars.enable``): the same solve in ln k / ln epsilon, primal
        # in and out.  Off -- every solver it was not enabled on -- this is the plain call.
        if getattr(self, "_newton_logs", None):
            return _logvars.solve(self, _newton.solve_newton, **kwargs)
        return _newton.solve_newton(self, **kwargs)

    def enable_log_turbulence(self, fields=None) -> dict:
        """Iterate on logarithmic turbulence unknowns in every later ``solve_newton`` (``logvars`` module)."""
        return _logvars.enable(self, fields)

    # -------------------------------------------------------- differentiation
    differentiable_solution = _newton.differentiable_solution
