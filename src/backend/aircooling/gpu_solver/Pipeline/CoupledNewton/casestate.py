"""The flat unknown ``x`` as the compound-cell state ``forward`` consumes, and back.

These are the ONLY functions in the package that call the discretisation's
``forward`` or read the residual attributes named in ``COLLOCATED_UNKNOWNS``;
everything downstream of here sees a pure function ``x -> r`` and a Jacobian
built from it.  Keeping that in one file is what makes the purity claim in the
package docstring checkable by reading rather than by grep.

Read off the ``CollocatedSolver`` passed in: ``solver`` (the discretisation),
``unknowns``, ``interior``, ``n_cell``, ``num_fields``, ``_zero_state``,
``_flat_axes``, and ``_old_state`` (``None`` unless ``set_old_state`` armed a
time level; read with ``getattr`` so a solver object without it is steady).
"""
from __future__ import annotations

import contextlib
import os

import torch

from Pipeline.CoupledNewton import logvars as _logvars
_STANCE = getattr(getattr(torch, "compiler", None), "set_stance", None)
if _STANCE is None:
    # torch < 2.6 has no compiler stances to stand ``torch.compile`` down for a scope (``_eager``): it is stood
    # down for the process instead, so the lazily compiled functions of ``FVgrad`` stay plain functions
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")


def promote_case_to_double(case) -> int:
    """Re-register the case's geometry in float64, and report how many tensors moved.

    The H5 files store float32 and nothing downstream of them asks for anything
    else, because a GNN never needed it.  A Newton solve does: the acceptance
    threshold is below the float32 roundoff of the residual itself, and the WLSQ
    solve inside the gradient reconstruction refuses a float32 operator against a
    float64 right-hand side outright.

    Re-registration is not optional after the cast.  ``register_geometrics``
    short-circuits on a mesh signature built from tensor SHAPES and a few sampled
    coordinate VALUES, neither of which a dtype change moves, so the solver would
    otherwise keep serving the float32 geometry it cached.
    """
    moved = 0
    for store in case.fv_graph._hetero_data.stores:
        for key, value in list(store.items()):
            if torch.is_tensor(value) and value.dtype == torch.float32:
                store[key] = value.double()
                moved += 1
    solver = case.integrator._solver
    solver.registered = False
    solver._geom_mesh_sig = None
    solver._facecv_geom_sig = None
    solver.register_properties(case.fv_graph, force_register=True)
    return moved


@contextlib.contextmanager
def _eager():
    """Run with ``torch.compile`` stood down for the duration.

    ``FVgrad.gradient_reconstruction`` carries ``@torch.compile``, and
    aot_autograd raises "does not currently support double backward" on the
    second differentiation -- which is precisely how the Jacobian-vector product
    below is formed.  Standing the compiler down is scoped to this context rather
    than set globally, because the GNN paths in the same process want it.
    """
    if _STANCE is None:
        yield
        return
    with _STANCE("force_eager"):
        yield


def close_the_out_of_plane_rows(self, momentum: torch.Tensor,
                                x: torch.Tensor) -> torch.Tensor:
    """Put ``w = 0`` where a 2D discretisation writes no momentum equation.

    **Without this a ``dim = 2`` case is exactly singular and the run reports
    convergence anyway.**  Measured on the 128^2 cavity, the first
    two-dimensional case to reach this solver: every one of the 16384
    z-momentum rows AND all 16384 w columns of the assembled Jacobian had
    ``|row|_1 = 0.0``, because ``buoyantBoussinesqpicoext`` assembles momentum
    for ``0:dim`` only.  The consequences all pointed away from the cause:

    * GMRES divided by a zero diagonal and returned NaN in the w block, which
      then sat in the state for the rest of the run.
    * Every reported number stayed finite, because nothing reads w when the
      case is 2D -- so the line search accepted, the residual fell, and the
      gate passed with a NaN in the answer.
    * The reuse rule ``|x - anchor| / |anchor|`` became NaN, ``NaN > 0.2`` is
      False, and the Jacobian was therefore NEVER reassembled after step 1.
      What looked like slow Newton convergence was a chord iteration on the
      cold linearisation: Ra = 1e4 spent 40 steps to reach 1.4e-11 with
      ``lambda = 0.5`` throughout, and Ra = 1e5 stalled outright at 1.6e-05.
    * The exported VTM carried NaN in w.

    The substituted row is the equation the 2D discretisation implies rather
    than an invention: it makes the out-of-plane velocity zero, which is what
    ``residual_flat`` hard-codes on the ODIL path (``phi_new_full[:, 2] = 0``).
    Written through the residual so the identity block reaches the Jacobian by
    autograd, with no second spelling in ``assemble`` that could disagree.
    """
    blocks = list(momentum.split(self.n_cell))
    flat = x.reshape(-1)
    for axis in self._flat_axes:
        blocks[axis] = flat[axis * self.n_cell:(axis + 1) * self.n_cell]
    return torch.cat(blocks)


def scatter_state(self, x: torch.Tensor) -> torch.Tensor:
    """The flat unknown as the compound-cell field ``picoext`` consumes.

    Ghost rows are left at zero: every one of them is overwritten by its
    boundary condition inside ``forward`` before anything reads it, and
    writing a guess there instead would be a value no equation constrains.
    """
    cells = x.reshape(self.num_fields, self.n_cell).transpose(0, 1)
    return self._zero_state.index_copy(0, self.interior, cells)


def set_old_state(self, old_state) -> None:
    """Arm (or, with ``None``, disarm) the old time level every later ``residual`` reads.

    ``old_state`` is a COMPOUND-cell tensor ``[N_cpd, F]`` in the same channel
    layout ``scatter_state`` writes (``state_with_boundaries`` returns exactly
    that), detached, on the solver's device and dtype.  It is an attribute and
    not only an argument because ``solve_newton``, ``assemble`` and
    ``linearise`` all call ``residual(x)`` with the state alone; the old level
    is a CONSTANT of the time step, so the Jacobian they build is
    ``J_steady + diag(unsteady_coeff V / dt)`` on the rows whose discretisation
    carries a time term, and nothing else moves.

    Used by ``entries/solve_transient_with_coupled_newton.py``.
    ``None`` -- the state of every steady caller, set in
    ``CollocatedSolver.__init__`` -- is the zero old state below, bit for bit.
    """
    if old_state is not None:
        reference = self._zero_state
        if tuple(old_state.shape) != tuple(reference.shape):
            raise ValueError(
                f"old state has shape {tuple(old_state.shape)}; this case's compound "
                f"state is {tuple(reference.shape)} (state_with_boundaries' layout)")
        if old_state.dtype != reference.dtype or old_state.device != reference.device:
            raise ValueError(
                f"old state is {old_state.dtype} on {old_state.device}; the solver runs "
                f"{reference.dtype} on {reference.device}")
        old_state = old_state.detach()
    self._old_state = old_state


def residual(self, x: torch.Tensor, old_state=None) -> torch.Tensor:
    """``[mom_x | mom_y | mom_z | cont | ...]``, one block of ``N`` rows each.

    The blocks and their order come from the discretisation's own
    ``COLLOCATED_UNKNOWNS``, so the energy row arrives here by the same route
    the momentum rows do and nothing in this function knows which case it is.
    A width-1 block goes through the same transpose as a width-3 one, which is
    a no-op on ``[N, 1]`` and keeps the two spellings from drifting apart.

    No time-derivative term is subtracted off here or switched off by hand:
    the case declares ``ddtSchemes = "steadyState"`` / ``unsteady = false``,
    which puts a zero in ``theta_PDE[..., 0]``, and ``time_scheme``
    multiplies BOTH the new-time and old-time contributions by it -- in the
    Boussinesq solver, the energy equation's pair as well.  The old state
    passed below is therefore multiplied by exactly zero, and passing zeros
    makes that visible rather than merely true.

    THE OLD TIME LEVEL (opt-in).  ``old_state`` -- or, when it is
    ``None``, the one ``set_old_state`` armed -- is handed to ``forward`` as
    ``phi_old_cpd_cell``; with neither, the old state is ``torch.zeros_like``
    and every steady caller is unchanged to the bit.  On a
    case that declares ``unsteady = true`` the momentum rows then read
    ``unsteady_coeff V (u - u_old) / dt_cell + (steady terms)``, which is one
    backward-Euler step from ``u_old`` -- or a BDF2 step when the caller hands
    over the effective old level ``(4 u^n - u^{n-1}) / 3`` and sets
    ``dt_cell = 2 dt / 3`` (``Pipeline.CoupledNewton.timestepping``).
    """
    if old_state is None:
        old_state = getattr(self, "_old_state", None)
    # Logarithmic turbulence unknowns (opt-in, ``Pipeline.CoupledNewton.logvars``): inside a ``solve_newton`` call the
    # iterate holds ln k / ln epsilon and the forward pass (and an operator correction) reads the primal state ``exp``
    # maps it to; the pinned rows and the PTC term below stay in the iterate's own variables.  Off, ``physical`` is
    # ``x`` itself.
    logs = getattr(self, "_newton_logs", None)
    physical = _logvars.to_primal(self, x) if (logs and logs["active"]) else x
    # Warp backend (opt-in, docs/research/2026-10-14-warp-backend.md): the fused residual of the same
    # discretisation, built from this case's own geometry, ghost maps and coefficients; ``None`` -- every solver no
    # backend was attached to, and ``state_with_boundaries``, which needs the torch forward's boundary stashes --
    # runs the torch forward below, unchanged.
    warp = getattr(self, "_warp", None)
    if warp is not None and not getattr(self, "_warp_bypass", False):
        out = warp.rows(x, old_state)
    else:
        state = self.scatter_state(physical)
        with _eager():
            self.solver.forward(phi_old_cpd_cell=(torch.zeros_like(state) if old_state is None
                                                  else old_state),
                                phi_new_cpd_cell=state)
        rows = [getattr(self.solver, attribute).transpose(-1, -2).reshape(-1)
                for _, _, _, attribute in self.unknowns]
        if self._flat_axes:
            rows[0] = self.close_the_out_of_plane_rows(rows[0], physical)
        out = torch.cat(rows)
    # Learned operator correction (opt-in, ``Pipeline.CoupledNewton.operator_correction``):
    # F_H + C_phi, with C_phi a sum of conservative face fluxes of the boundary-filled state the forward above left on
    # the discretisation -- part of the OPERATOR, so it sits before the pin, the PTC shift and the FAS right-hand side,
    # and reaches the Jacobian through this function like every other term.  ``None`` -- the state of every solver no
    # correction was attached to -- changes nothing, to the bit.
    correction = getattr(self, "_operator_correction", None)
    if correction is not None:
        out = out + correction(self, physical)
    # Staged cold start (opt-in, ``Pipeline.CoupledNewton.coldstart``): while ``_pin`` is armed,
    # the rows of the PINNED unknowns read ``x - anchor`` instead of their equations -- the identity-row
    # substitution ``close_the_out_of_plane_rows`` uses for w, so the frozen-nu_t stage (turbulence pinned)
    # and the turbulence stage (mean flow pinned) are the same square system with some rows exchanged, and
    # ``linearise`` / ``assemble`` see the identity rows through autograd like every other term.  The forward
    # above still runs with the full state, so the free rows are bit for bit what they are unpinned.  ``None``
    # -- the permanent state of every other caller -- changes nothing, to the bit.
    pin = getattr(self, "_pin", None)
    if pin is not None:
        mask, anchor = pin
        out = torch.where(mask, x.reshape(-1) - anchor, out)
    # Pseudo-transient continuation, when ``solve_newton`` has armed it: the
    # backward-Euler term ``D (x - x_ref)`` on the DIFFERENTIAL rows (``D`` is
    # zero on continuity -- Coffey, Kelley & Keyes 2003's index-1 DAE form).
    # It is added HERE, inside the one function the linearisation and the
    # assembly both differentiate, so the shifted diagonal reaches ``J``, the
    # F block and the R block through the same path as every other term --
    # never as a bolt-on to the assembled matrix.  At the committed iterate
    # (``x == x_ref``) the term is exactly zero, so this function still
    # reports the steady residual there.  ``None`` -- the permanent state of
    # every non-PTC solve -- changes nothing, to the bit.
    shift = self._ptc_shift
    if shift is not None:
        d, ref = shift
        out = out + d * (x.reshape(-1) - ref)
    # Full-approximation-scheme coarse equation F_H(q) = tau (Pipeline/CoupledNewton/
    # fas.py): a constant right-hand side, so the Jacobian, the assembly and every
    # preconditioner see exactly the operator they see without it.  ``None`` -- the
    # state of every non-FAS solve -- changes nothing, to the bit.
    tau = getattr(self, "_fas_tau", None)
    if tau is not None:
        out = out - tau
    return out


def state_with_boundaries(self, x: torch.Tensor) -> torch.Tensor:
    """``[N_cpd, F]``: the solved interior, and ghosts holding their BC values.

    ``scatter_state`` leaves the ghost rows at zero because no equation
    constrains them; ``forward`` then overwrites every one of them from the
    boundary condition itself -- Dirichlet patches write the prescribed value,
    zero-gradient patches mirror their owner.  Exporting the scattered vector
    instead would put a zero on the lid, which reads as a no-slip wall that
    was solved for, and on a Boussinesq case a zero on a hot wall reads as a
    wall held at the reference temperature.  One residual evaluation is what
    runs the enforcement, so this costs one forward.
    """
    bypass = getattr(self, "_warp_bypass", False)
    self._warp_bypass = True                 # the export reads the torch forward's boundary stashes
    try:
        self.residual(x)
    finally:
        self._warp_bypass = bypass
    return torch.cat([
        getattr(self.solver, attribute).reshape(-1, width)
        for _, width, attribute, _ in self.unknowns], dim=-1)
