"""The outer solve: damped Newton, and the implicit-function re-attachment of its answer.

Jacobian REUSE lives inside ``solve_newton`` and not in a file of its own.  It
is three reads of the loop's own state -- the drift against ``anchor``, the
``assembly_seconds == 0.0`` staleness flag, and the one retry that flag permits
-- and lifting them out would export ``blocks``, ``anchor`` and ``precondition``
as cross-module mutable state to save nine lines.

Read off the ``CollocatedSolver`` passed in: ``n_unknowns``, ``device``,
``dtype``, and the methods ``residual``, ``resolve_gauge``, ``gauge``,
``assemble``, ``simple_preconditioner``.
"""
from __future__ import annotations

import contextlib
import math
import time

import torch

from SparseSolver.gmres import gmres, polynomial_preconditioner
from SparseSolver.implicit_diff import implicit_solution, transpose_jacobian_matvec

from Pipeline.CoupledNewton import jfnk as _jfnk
from Pipeline.CoupledNewton.jacobian import ASSEMBLY_PATTERNS, _coefficient, drop_explicit_zeros
from Pipeline.CoupledNewton.equilibration import (
    EQUILIBRATIONS, RUIZ_SWEEPS, check_recovery, ruiz_scaling, scaled_matrix,
    turbulence_block_scale)
from Pipeline.CoupledNewton.preconditioner import (
    NEWTON_PRECONDITIONERS, RESTORE_ALPHA, _csr_diagonal)
from Pipeline.CoupledNewton import krylov_audit as _krylov_audit


def true_residual_guard(result, step, operator, rhs, requested: float, factor: float, solve, redo, gauge):
    """The step's true linear residual ``|J d - rhs| / |rhs|`` against the solve's own report (``solve_newton``
    ``true_residual_check``).  When it exceeds both ``factor`` times the reported residual (the recurrence of GMRES,
    the value its stopping test read) and the requested tolerance, the step is flagged and printed.  Without a ``redo``
    (the default) the solve's own result and step are returned as they are (the same objects); with one, ``solve()``
    runs again inside ``redo()`` and the step with the smaller true residual is taken, the returned result counting
    the iterations of both solves, its last entries those of the solve that was taken.  Returns ``(result, step,
    row)``, ``row`` the readings."""
    import dataclasses
    nb = max(float(rhs.norm()), 1e-300)
    true = float((operator(step) - rhs).norm()) / nb
    reported = result.residuals[-1] if result.residuals else 0.0      # the recurrence: what the solve reports
    row = {"true": true, "reported": float(reported), "requested": float(requested), "redone": False}
    if not true > max(factor * float(reported), float(requested)):
        return result, step, row
    row["flagged"] = True
    if redo is None:
        # log only (the default): the solve's own step is taken, the reading is kept
        print(f"[true residual] the step's true residual {true:.3e} against the reported {float(reported):.3e} "
              f"(requested {float(requested):.1e}): flagged, the solve's step taken", flush=True)
        return result, step, row
    with redo():
        again = solve()
    step_again = gauge(again.x)
    true_again = float((operator(step_again) - rhs).norm()) / nb
    take = true_again < true
    first, last = (result, again) if take else (again, result)
    total = dataclasses.replace(
        last, residuals=tuple(first.residuals) + tuple(last.residuals),
        reorthogonalisations=int(first.reorthogonalisations) + int(last.reorthogonalisations),
        cycle_lengths=tuple(first.cycle_lengths) + tuple(last.cycle_lengths),
        cycle_residuals=tuple(first.cycle_residuals) + tuple(last.cycle_residuals))
    row.update(redone=True, true_redo=true_again, iterations=int(result.iterations),
               iterations_redo=int(again.iterations), taken="redo" if take else "first")
    print(f"[true residual] the step's true residual {true:.3e} against the reported {float(reported):.3e} "
          f"(requested {float(requested):.1e}): solved again by the coarse LU, true {true_again:.3e}; "
          f"{row['taken']} taken", flush=True)
    return total, (step_again if take else step), row


def _assembly_diagonal(blocks) -> torch.Tensor:
    """diag(J) of an assembly, for the PTC timestep scale: the scalar CSR's, or -- a block assembly
    (``fixed_pattern="blocks"``: no scalar J exists) -- the diagonal entries of the diagonal
    blocks of ``blocks["A"]``, in the field-blocked order of the unknowns (unknown ``f * n + i``).  Read before the
    preconditioner build releases the block matrix."""
    if hasattr(blocks, "attach_operator") and hasattr(blocks, "release_blocks"):     # jacobian.BlockJacobian
        A = blocks["A"]
        on = A.row == A.col
        out = torch.zeros(A.F, A.shape[0], dtype=A.val.dtype, device=A.val.device)
        out[:, A.row[on].long()] = torch.diagonal(A.val[on], dim1=1, dim2=2).t()
        return out.reshape(-1)
    return _csr_diagonal(blocks["J"])


def _ptc_base(self, diagonal, scaling: str):
    """The local pseudo-timestep scale ``|diag(J_steady)|``, zero on continuity.

    Coffey, Kelley & Keyes 2003 (SISC): for an index-1 DAE the pseudo-transient
    term goes on the DIFFERENTIAL rows only -- momentum, energy, and the
    transported turbulence rows here -- and the algebraic constraint
    (continuity) is left exact.  Phase 5's probe already measured the opposite
    corner: regularising continuity is not even representable in this form,
    and the unregularised |dx_p| ~ 53 at the SER collapse was the constraint
    doing its job, not a defect.

    The row's own Jacobian diagonal is the LOCAL timestep spelling: ADflow
    (Yildirim 2019 section 3.4) computes per-cell timesteps "to give the same
    CFL number in every cell", and ``theta * |a_P|`` is exactly that with
    theta = 1/CFL playing the dimensionless controller -- it is also
    OpenFOAM's implicit under-relaxation with alpha = 1/(1+theta), so one
    scalar is comparable across momentum, k, and the log-omega row.
    ``scaling="uniform"`` is the global-timestep control arm (one median scale
    for every differential row, i.e. a single clock); it converges too but
    slower (kOmega MMS box from zero, theta0 10: 41 steps against 32 for
    ``diag``) and it is not what the high-aspect RANS literature runs -- see
    the knob table in ``solve_newton``.
    """
    base = diagonal.abs()
    base[self.velocity:self.pressure] = 0.0     # continuity stays algebraic
    if scaling == "uniform":
        differential = torch.cat(
            (base[:self.velocity], base[self.pressure:]))
        base = torch.where(base > 0, differential.median(), base)
    elif scaling != "diag":
        raise ValueError(f"unknown ptc_scaling {scaling!r}; valid: diag, uniform")
    return base


def _csr_diagonal_slots(matrix: torch.Tensor) -> torch.Tensor:
    """Where each row's diagonal entry sits in ``values()``; ``-1`` if absent.

    ``_csr_diagonal`` reads those entries, and re-arming the pseudo-timestep
    below WRITES them, which needs the position rather than the value.  A row
    can genuinely have no diagonal slot: ``jacobian.assemble`` keeps only the
    entries a probe returned nonzero (``value != 0``), so a diagonal that
    cancelled to exactly zero is not in the pattern at all -- which is why the
    caller checks the returned indices instead of assuming a square matrix has
    a stored diagonal.
    """
    rows = matrix.shape[0]
    crow = matrix.crow_indices()
    col = matrix.col_indices()
    row = torch.repeat_interleave(
        torch.arange(rows, device=matrix.device), crow[1:] - crow[:-1])
    slot = torch.full((rows,), -1, device=matrix.device, dtype=torch.long)
    hit = (row == col).nonzero(as_tuple=True)[0]
    slot[row.index_select(0, hit)] = hit
    return slot


def _reshift(self, blocks, delta):
    """``blocks`` re-armed at a new theta, or ``None`` if the pattern forbids it.

    **PTC's entire contribution to the matrix is a diagonal.**  The shift
    enters as the residual term ``d * (x - x_ref)`` (``casestate.residual``),
    which is ELEMENTWISE, so its derivative is ``diag(d)`` and no other entry
    of the assembled operator depends on theta.  Moving theta therefore does
    not need the residual re-differentiated -- it needs one number per row
    changed, at ``O(rows)`` against an assembly's one probe per colour per
    field.

    Every square diagonal sub-block the preconditioner reads gets the same
    treatment; ``G`` and ``D`` are off-diagonal blocks that no diagonal can
    reach, and ``C`` is listed but is a no-op in practice because
    ``_ptc_base`` zeroes the continuity rows.  Values are cloned rather than
    written in place: a frozen preconditioner still holds the previous
    matrices, and mutating them under it would change what it applies without
    anything saying so.

    ``None`` means at least one row that needs a nonzero change has no stored
    diagonal, i.e. its shifted diagonal had cancelled to exactly zero (only
    reachable when ``theta`` passes exactly 1 on a negative diagonal).  There
    is nothing to write into, so the caller falls back to an assembly.
    """
    offsets = {"J": 0, "F": 0, "C": self.velocity, "R": self.pressure}
    out = dict(blocks)
    for name, offset in offsets.items():
        matrix = blocks.get(name)
        if matrix is None:
            continue
        part = delta[offset:offset + matrix.shape[0]]
        if not bool(part.any()):
            continue
        slot = _csr_diagonal_slots(matrix)
        if bool((slot[part != 0] < 0).any()):
            return None
        keep = slot >= 0
        values = matrix.values().clone()
        values.index_add_(0, slot[keep], part[keep])
        out[name] = torch.sparse_csr_tensor(
            matrix.crow_indices(), matrix.col_indices(), values,
            size=matrix.shape)
    return out


# ``BUILDS["deterministic"]`` (opt-in, ``--deterministic-builds``): the preconditioner builds keep torch's
# deterministic algorithms under ``fast_linear_kernels`` (only the Krylov solves drop them), so a build is a
# function of its matrix alone: a set-up scalar such as the smoother's omega no longer moves by the last bit
# from run to run.
BUILDS = {"deterministic": False}
# ``--save-first-correction PATH``: where the first computed Newton correction of the process is saved (None: off)
STEP_DUMP = {"path": None, "done": False}


def build_kernels(fast: bool):
    """``linear_kernels`` for a preconditioner build: fast unless ``BUILDS["deterministic"]``."""
    return linear_kernels(bool(fast) and not BUILDS["deterministic"])


@contextlib.contextmanager
def linear_kernels(fast: bool):
    """``fast=True`` (``solve_newton(fast_linear_kernels=True)``): the preconditioner build and the
    Krylov solve run with ``torch.use_deterministic_algorithms`` OFF, the residual, the Jacobian and the line search
    keep the deterministic mode ``CollocatedSolver(deterministic=True)`` sets for the whole process.

    That mode exists for the line search (``collocated_newton``): ``||r(x + lam dx)|| < ||r(x)||`` must not be
    decided by scatter-atomic noise.  Inside the linear solve it only costs time -- it routes every CUDA
    ``index_add`` through a sort-based deterministic kernel, about 0.2 ms per call whatever the size, and a
    multicolour GS 2+2 V-cycle makes ~380 such calls against ~107 for the learned W 1+1 + 1 GS cycle, so the
    classical arm paid 34-73 ms per GMRES iteration more than the same cycle in gpu_bench (time
    accounting).  GMRES iterates are then reproducible to rounding only; the
    certificate and the accepted state are unaffected.  A no-op when the mode is already off."""
    if not fast or not torch.are_deterministic_algorithms_enabled():
        yield
        return
    warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    torch.use_deterministic_algorithms(False)
    try:
        yield
    finally:
        torch.use_deterministic_algorithms(True, warn_only=warn_only)


def expur_theta(theta: float, lam: float, *, growth: float = 2.0, hold_lambda: float = 0.01, cut: float = 10.0,
                cfl_min: float = 1.0, cfl_max: float = 1.0e10) -> float:
    """The next theta = 1/CFL of the EXPur pseudo-time controller after an accepted step with line-search factor ``lam``.

    Ceze & Fidkowski (2013), AIAA 2013-2686, section III.A, Eq. 13: a full step raises the CFL by ``growth``
    (theta / growth), a damped step with ``lam >= hold_lambda`` holds it, and a step with ``lam < hold_lambda`` cuts it
    by ``cut`` (theta * cut); the CFL stays within ``[cfl_min, cfl_max]``.  A refused step is cut the same way by the
    caller, and there is no forced step.  The defaults are the ones Hoshyari et al. use
    (lambda_min 0.01, cut 0.1 of the CFL, cap 1e10, floor 1)."""
    if lam >= 1.0:
        theta = theta / growth
    elif lam < hold_lambda:
        theta = theta * cut
    return min(max(theta, 1.0 / cfl_max), 1.0 / cfl_min)


def forcing_stalled(history, rho: float) -> bool:
    """Whether the Newton loop is in the
    loop of a loose forcing and a truncated linear solve -- the PREVIOUS step's solve ended above the tolerance it
    asked for (``inner_rel > inner_rtol``: truncated by the stagnation rescue's ``maxit`` or by the GMRES budget) AND
    that step reduced ``||r||2`` by less than the factor ``rho`` (``l2_k > rho l2_k-1``).  Read from the third row on
    only, i.e. not for Newton steps 1 and 2 of a call (the learned start's l1 cycle and its hand-over are loose by
    design, 12.25-12.26).  Rows without a linear solve (a rung's row 0, an elimination-only row) never count.

    Measured motivation: Eisenstat-Walker keeps the forcing at its 0.1 ceiling when the residual ratio
    is ~1, every such request is rescued and, with ``--camg-rescue-maxit 0``, ended at the 30-iteration probe; on
    P20 Lg1 that fixed point sat at max|r| 8.7e-7 for 26 steps (ratio 0.979-1.000), where an absolute threshold
    (``forcing_cap``) cannot see it.  The stalled steps of job 4 read ratio >= 0.978, every truncated step of a
    certified run <= 0.81."""
    if len(history) < 3:
        return False
    last, before = history[-1], history[-2]
    requested, achieved = last.get("inner_rtol"), last.get("inner_rel")
    if requested is None or achieved is None or "l2" not in before:
        return False
    if not float(achieved) > float(requested):
        return False
    return float(last["l2"]) > float(rho) * float(before["l2"])


def inner_forcing(history, rtol: float, *, forcing: bool = True, first_forcing: float | None = None,
                  forcing_cap=None, stall_cap=None, retry_loose_stall: bool = False) -> tuple[float, bool]:
    """The relative tolerance a Newton step of ``solve_newton`` asks its GMRES for, and whether the endgame cap set it.

    ``history`` is the loop's history so far (its last row = the current state, ``max_abs`` and ``l2``).  Without
    ``forcing`` the request is the ``rtol`` floor; with it, Eisenstat & Walker choice 2 from the second row on
    (``min(0.9 (l2_k / l2_k-1)^2, 0.1)``, floored at ``rtol``), and ``max(first_forcing, rtol)`` on the first step
    when ``first_forcing`` is set -- the arithmetic the loop always ran, expression for expression.

    ``forcing_cap = (below, eta)`` (opt-in, experiments/NaturalConvection/CoupledNewton/RobustLinear/README.md): with
    ``forcing`` on, a step taken from a state whose ``max_abs`` (the certificate's norm) is below ``below`` asks for
    ``min(request, eta)``, still floored at ``rtol``.  Reason: with the stagnation rescue truncated at its probe
    (``--camg-rescue-maxit 0``) Eisenstat-Walker keeps the forcing at 0.1 when the residual ratio is ~1, and every
    request >= the rescue's ``min_rtol`` is rescued and ended at the 30-iteration probe; on the P22 Lg1 run
    that loop stalled at max|r| 9.5e-9 for 27 steps.  An ``eta`` below the rescue's ``min_rtol`` keeps
    those last solves on the primary cycle, solved accurately.  ``None`` (the default) returns exactly the request
    above and ``capped = False``.

    ``stall_cap = (rho, eta)`` (opt-in, II-4 job 5): with ``forcing`` on, a step taken while ``forcing_stalled(history,
    rho)`` holds asks for ``min(request, eta)``, floored at ``rtol`` -- the stagnation-triggered form of the cap, which
    restarts the forcing loop wherever the truncated iteration stalls (measured: one accurate solve from the
    stalled P20 state and the run certifies).  ``capped`` is True when either cap set the request."""
    requested = rtol
    if forcing and len(history) > 1:
        if retry_loose_stall and history[-1].get("stalled"):
            requested = rtol
        else:
            # Eisenstat & Walker choice 2: ask the inner solve only for the
            # accuracy the outer step can still use.
            ratio = history[-1]["l2"] / max(history[-2]["l2"], 1e-300)
            requested = min(0.9 * ratio * ratio, 1.0e-1)
            requested = max(requested, rtol)
    elif forcing and first_forcing is not None:
        # F1b-d (opt-in): a loose first solve instead of the floor (solve_newton docstring)
        requested = max(float(first_forcing), rtol)
    capped = False
    if forcing and forcing_cap is not None:
        below, eta = float(forcing_cap[0]), float(forcing_cap[1])
        if float(history[-1]["max_abs"]) < below and requested > eta:
            requested = max(eta, rtol)
            capped = True
    if forcing and stall_cap is not None:
        rho, eta = float(stall_cap[0]), float(stall_cap[1])
        if requested > eta and forcing_stalled(history, rho):
            requested = max(eta, rtol)
            capped = True
    return requested, capped


def check_stall_cap(stall_cap):
    """``stall_cap`` as ``(rho, eta)`` floats, or ``None``; refuses ``rho`` outside (0, 1] and ``eta`` outside (0, 1)."""
    if stall_cap is None:
        return None
    rho, eta = (float(v) for v in stall_cap)
    if not (0.0 < rho <= 1.0):
        raise ValueError(f"stall_cap needs a residual ratio 0 < RHO <= 1, not {rho}")
    if not (0.0 < eta < 1.0):
        raise ValueError(f"stall_cap needs a relative GMRES tolerance 0 < ETA < 1, not {eta}")
    return rho, eta


def check_forcing_cap(forcing_cap):
    """``forcing_cap`` as ``(below, eta)`` floats, or ``None``; refuses a threshold <= 0 or an ``eta`` outside (0, 1)."""
    if forcing_cap is None:
        return None
    below, eta = (float(v) for v in forcing_cap)
    if not (math.isfinite(below) and below > 0.0):
        raise ValueError(f"forcing_cap needs a residual threshold R > 0, not {below}")
    if not (0.0 < eta < 1.0):
        raise ValueError(f"forcing_cap needs a relative GMRES tolerance 0 < ETA < 1, not {eta}")
    return below, eta


def solve_newton(self, *, x0=None, tol: float = 1.0e-12, maxit: int = 30,
                 rtol: float = 1.0e-8, restart: int = 400,
                 max_cycles: int = 4, deflate: int = 0, deflate_rescued: int = 0, sweeps: int = 2,
                 max_backtracks: int = 8, candidate_guard=None, trial_map=None,
                 restart_probe=None, first_assembly_dump=None,
                 precond_wrapper=None, cycle_policy=None,
                 cycles: int = 1, forcing: bool = True,
                 freeze_preconditioner: bool = False,
                 reassemble_drift: float = 0.2, pressure: str = "auto",
                 precond_shape: str = "simple",
                 restore_alpha: float = RESTORE_ALPHA,
                 block_scaling: bool = False,
                 equilibrate: str = "off", ruiz_sweeps: int = RUIZ_SWEEPS,
                 poly_degree: int = 0,
                 matrix_free: bool = False,
                 matrix_free_coefficient: str = "implicit",
                 ptc: bool = False, ptc_theta0: float = 0.2,
                 ptc_growth: float = 2.0, ptc_handover: float = 1.0e-4,
                 ptc_controller: str = "ser", ptc_scaling: str = "diag",
                 ptc_max_forced_growth: float | None = None,
                 ptc_hold_lambda: float = 0.01, ptc_cut: float = 10.0, ptc_cfl_max: float = 1.0e10,
                 ptc_cfl_min: float = 1.0,
                 release_blocks: bool = False, capture_inner: bool = False,
                 checkpoint=None, progress=None, preconditioner_factory=None,
                 fast_linear_kernels: bool = False, component_timers: bool = False,
                 fixed_jacobian_pattern: str | None = None,
                 fast_assembly_kernels: bool = False,
                 carry=None, krylov_operator: str = "assembled",
                 precond_policy=None, jvp: str = "tape",
                 fast_line_search: bool = False, elimination=None,
                 first_forcing: float | None = None, first_maxit: int | None = None,
                 krylov_basis_chunk: int = 0, forcing_cap=None, forcing_stall_cap=None,
                 true_residual_check: float = 0.0, true_residual_redo=None,
                 cap_fallback: float | None = None, on_cap_fallback=None,
                 ptc_weights=None, trial_map_ptc_only: bool = False, merit: str = "l2",
                 ptc_handover_below: float | None = None,
                 ptc_on_stall: int = 0, ptc_stall_theta0: float | None = None,
                 ptc_stall_theta_min: float = 0.0, linear_cap=None,
                 contraction_probe: bool = False, step_norm_log: bool = False, first_restart=None,
                 retry_loose_stall: bool = False, accelerator=None, stop_rule=None):
    """Damped Newton to ``max|r| < tol``, and the history of how it got there.

    ``stop_rule`` (opt-in, data-centre line 2026-11-06): a callable ``stop_rule(x, residual, it) -> bool`` read at
    the head of every iteration next to the ``max|r| < tol`` test; True ends the loop as converged by the caller's
    engineering rule (OpenFOAM-style residualControl and quantity stagnation, ``Pipeline.CoupledNewton.stop_rules``).
    Default None: unchanged.

    ``cap_fallback = LMIN`` (opt-in): a CAPPED step (``forcing_cap`` / ``forcing_stall_cap``
    set its request) that the line search refuses, or accepts only with ``lambda < LMIN``, is not taken; the call
    continues from the same state with both caps off for good and ``on_cap_fallback(precondition)`` called once (the
    entry lifts the stagnation rescue's ``maxit`` there, i.e. returns to the production rescue path).  Measured reason
    (experiments/NaturalConvection/CoupledNewton/RobustLinear/README.md): on P22 Lg1 the accurately solved (capped) Newton
    step is dominated by a near-null mode of J (``|J s| / |s|`` 2.5e-8 against 3.8e-7 on a seed that converges, 2.8e-12
    at the refused state; the residual is smooth: second difference constant over four decades), so it overshoots
    10-5e4 x the error and the line search cuts it to 1/32 and then refuses; the l1-rescued truncated Krylov steps do
    not resolve that mode and certify from the same state.  The row of a fallback carries ``cap_fallback``.  ``None``
    (the default) is every call before.

    ``forcing_stall_cap = (rho, eta)`` (opt-in): with ``forcing`` on, a step taken after a
    truncated solve whose step cut ``||r||2`` by less than ``rho`` asks GMRES for ``min(forcing, eta)``
    (``forcing_stalled`` / ``inner_forcing``); its rows carry ``forcing_capped`` and ``forcing_cap_stall``.  ``None``
    (the default) is every call before, keyword for keyword and row for row.

    ``forcing_cap = (below, eta)`` (opt-in, experiments/NaturalConvection/CoupledNewton/RobustLinear/README.md): with
    ``forcing`` on, every step taken from a state with ``max|r| < below`` asks GMRES for ``min(forcing, eta)``
    (``inner_forcing``); such rows carry ``forcing_capped``.  ``None`` (the default) is every call before, keyword for
    keyword and row for row.

    ``first_forcing`` (opt-in): with ``forcing`` on,
    the FIRST Newton step of this call asks GMRES for ``max(first_forcing, rtol)`` instead of the ``rtol`` floor.
    Eisenstat-Walker choice 2 needs two residuals, so without it step 1 is solved to the floor (1e-8 in the
    steady entry) -- measured to cost ~30 s of 110-140 s on DHC64 g4 Ra 1e7 from a projected learned start, where
    the first solve runs into the 1,230-iteration cap.  ``None`` (the default) is the plain call,
    keyword for keyword; with ``forcing=False`` it is ignored.

    GPU MEMORY (opt-in,
    docs/research/2026-10-07-gpu-optimisation-roadmap.md).  ``krylov_basis_chunk``
    > 0 hands ``gmres(basis_chunk=...)`` to every inner solve: the Arnoldi basis
    is allocated in blocks of that many vectors as the iteration reaches them
    instead of ``restart + 1`` vectors up front (10.2 GiB at ``restart`` 400 on
    DHC88; the production solves there need 8-114 vectors); iterates agree to
    rounding.  ``fixed_jacobian_pattern="blocks"`` assembles J straight into the
    coupled AMG's 5 x 5 blocks (``jacobian._assemble_blocks``): there is no
    scalar CSR, so the Krylov operator of an assembling step is the product the
    preconditioner factory attaches (``jacobian.BlockJacobian``; the camg
    factory attaches its fine-level scalar copy).  It needs the JFNK mode with
    ``krylov_operator`` "auto" or "jvp" and a ``preconditioner_factory``, and
    refuses what reads a scalar J (PTC, the scalings, matrix-free, ``carry``,
    the first-assembly dump, the elimination).  Both 0 / unset (the defaults)
    make the very calls this loop always made.

    ``first_maxit`` (opt-in): the FIRST Newton step of this call stops its GMRES
    after at most this many Arnoldi steps over all cycles (``gmres(max_iterations=)``), whatever the tolerance asked:
    F1b-d measured the l1 first solve from a projected learned start capped at 1,230 iterations at rel 0.016-0.44,
    so a looser request alone does not shorten it.  The step then goes through the usual line search.  ``None``
    (default): every call before, keyword for keyword.

    ``first_restart`` (opt-in): a dict the caller (the progress hook) may fill between
    steps with ``{"offset": k, "maxit": N}``: the step after row ``k`` is taken as this call's first step again --
    ``first_forcing``, the budget ``N`` (else ``first_maxit``) and the forcing / stall rules read from row ``k`` on --
    for a first step that made no progress and is retaken on another cycle.  With ``"on_refusal": True`` (opt-in, CHT2
    README section 19) a REFUSED step ``k`` is retaken too (once) instead of ending the call on its fresh
    preconditioner.  ``None`` / ``{}`` (default): every call before.

    ``linear_cap`` (opt-in): a zero-argument callable asked before every
    Newton step's GMRES; an integer caps that solve's iterations (the smaller of it and ``first_maxit``), ``None``
    leaves it alone.  The entry passes the armed order-1 switch's threshold (``Order1Switch.armed_cap``), so a
    stalling classical solve stops where the switch would fire instead of at its own budget.  ``None`` (default):
    every call before.

    ``fast_line_search`` (opt-in,
    docs/research/2026-10-07-gpu-optimisation-roadmap.md): the residual this
    loop evaluates -- the one at the call's start and every line-search trial
    -- runs with torch's deterministic algorithms off (``linear_kernels``), as
    ``fast_assembly_kernels`` does for the tape and the probes.  The
    deterministic mode routes every scatter-sum of the residual through a
    sort-based kernel (DHC64: 288 ms per residual against ~34 ms for the
    non-deterministic forward + create_graph backward).  The trajectory is
    then reproducible to rounding only: ``||r(x + lam dx)|| < ||r(x)||`` can
    only flip on CUDA-atomic noise (~1e-16 relative), i.e. at the residual
    floor, far below any gate.  The loop's own stop (``max_abs < tol``) and
    the rung-advance test read these residuals too.  THE CERTIFICATE IS NOT
    AFFECTED: the entries re-evaluate the residual at the returned state
    after the Newton timer, outside this function, in the deterministic mode
    (``solve_with_coupled_newton``: ``residual = solver.residual(x)`` before
    "max|r| = ... against the gate"; the transient entry's ``certify``), and
    accept on that value alone.  Rows gain ``fast_line_search: True`` only
    when it is on.  Off (the default) makes the very calls it made before:
    ``linear_kernels(False)`` is a no-op.

    ``carry`` (opt-in, the transient entry's ``--reuse-across-steps``)
    is a dict that hands the linearisation from one call to the next: on entry
    its ``blocks``, ``precondition``, ``anchor`` and ``scale`` seed the loop's
    own (``None`` or missing = assemble as usual), and on exit the loop writes
    its final ones back.  Reuse is then ``reassemble_drift``'s decision exactly
    as within one call -- the drift of the new ``x0`` against the carried
    anchor -- and the stale-Jacobian retry below still rebuilds on a refused
    line search.  A linearisation that carries the PTC shift is never handed
    on, and a ``ptc=True`` call never takes one (its first iteration assembles
    to arm the shift).  The CALLER owns validity across calls: anything that
    moves ``J`` other than ``x`` (a new ``dt`` on the time term, a new
    viscosity) must clear the dict.  It is refused with ``matrix_free`` (the tape tracks the
    blocks and is not carried).  ``None``, the default, is the shipped loop.
    When the loop drops the previous linearisation
    before a new assembly (the memory release below), it drops the dict's
    references to it as well, so a carried preconditioner does not stay
    resident beside the new build; the exit writes the final ones back as
    before.

    ``preconditioner_factory(blocks, x)``, when given, REPLACES the block
    preconditioner: it receives the freshly assembled blocks and the state
    they were assembled at and returns a fixed linear action ``M(v)`` (or
    ``None``).  It is rebuilt exactly where the shipped shape is rebuilt
    (assembly and PTC re-arm) and is subject to the same
    ``freeze_preconditioner`` rule, scaling conjugation and polynomial
    composition.  The action must be LINEAR and fixed for one linearisation:
    the inference GMRES below assumes so.  This is how a cached learned
    action (``Pipeline.CoupledNewton.learned_action``) enters the production
    Newton path; the residual, JVP, gauge and globalization are untouched.

    ``max_backtracks`` is the number of trial step lengths ``1, 1/2, ...``
    allowed per line search.  The default eight preserves the historical work
    budget.  Increasing it does not change the descent test, PTC policy or
    residual tolerance, and does not guarantee acceptance or convergence.

    ``candidate_guard(x)`` optionally rejects an inadmissible gauged trial
    before evaluating its residual. It must be deterministic and side-effect
    free. The initial state is checked too, and PTC cannot force a rejected
    candidate. ``trial_map(x, dx, lambda)`` optionally reparameterizes the
    trial path while leaving F, J and the linear direction untouched; its
    derivative at zero should equal dx. Both defaults preserve history.

    ``restart`` and ``max_cycles`` ARE TWO DIFFERENT KNOBS AND THE TOTAL MATVEC
    BUDGET IS THEIR PRODUCT.  ``restart`` is the length of one Krylov cycle,
    ``max_cycles`` the number of cycles allowed (``gmres`` spells this out), so
    the shipped 400 x 4 is 1600 matvecs and ``restart=1600`` at the same
    ``max_cycles`` is 6400.  ``max_cycles`` was hardcoded to 4 here until
    the entry exposed it, which is how the
    DataCenter note of that date came to compare a ``restart`` 400 arm against a
    ``restart`` 1600 arm and read the difference as an effect of RESTARTING:
    the second arm's winning inner solves cost 2764 and 5731 matvecs, both more
    than the 1600 the first arm was ever allowed to spend, so restart width and
    total budget moved together and nothing in that experiment separates them.
    Changing ``restart`` alone changes both; hold the product fixed to vary one.

    ``deflate`` carries that many harmonic Ritz vectors across each restart
    (GMRES-DR).  It defaults to 0, which is the plain iteration bit for bit.
    Measured on the DataCenter handover: at ``deflate`` 8 the inner
    solve recovers 63.6% of what an unrestarted 1600-vector basis buys, for
    +0.2 GiB of peak against that basis's +22.3 GiB, and the useful value tracks
    the number of modes within 100x of the Krylov space's reachable floor rather
    than the near-null count (``docs/research/2026-08-18-deflated-restarting.md``
    sections 7.3a and 7.5).

    ``deflate_rescued`` (opt-in) carries that many harmonic Ritz vectors across the restarts that follow a
    preconditioner's own change of cycle within a solve (``gmres(deflate_after_switch=...)``: the cycles of a
    stagnation rescue), and none elsewhere; 0, the default, is the call it always was.

    ``true_residual_check = c > 0`` (opt-in): after each step's Krylov solve, the step's true linear residual (one
    extra product) against the recurrence the solve reported; above both ``c`` times that and the requested tolerance
    the step is flagged (``true_residual_guard``), and the history row carries the true residual GMRES ended on as
    ``inner_rel``, with ``inner_recurrence``, ``inner_step_true`` and ``inner_flagged`` beside it.  The step is the
    solve's own; ``true_residual_redo`` (a context manager, opt-in; the steady entry's ``--true-residual-redo``: the
    coarsest level by its LU, V-cycles eager) solves a flagged step again and takes the better.  0, the default, is
    the call it always was.

    ``cycles`` V-cycles and ``sweeps`` damped-Jacobi sweeps are paid on EVERY
    GMRES iteration, so a stronger apply has to buy enough iterations back to
    pay for itself.  Past two cycles it buys none at all.  Measured over
    two interleaved rounds, momentum-weighted flux on, wall clock and total
    GMRES to ``max|r| < 1e-12``:

        cycles/sweeps    48^3 wall   48^3 GMRES    32^3 wall   32^3 GMRES
        1 / 2            14.7, 14.1     1591        6.1, 5.9      1133
        2 / 2 (ship)     14.8, 14.8     1494        7.3, 7.3       976
        4 / 2            16.3, 16.0     1545        9.2, 8.3       998
        8 / 2 (was)      18.2, 18.2     1494        9.9, 9.8       997
        2 / 1            17.5, 17.1     2614        8.2, 8.5      1751

    The iteration count is FLAT from 2 cycles to 8 -- 1494, 1545, 1494 at
    48^3 -- while the cost per iteration is linear in it, so the four extra
    cycles of a larger setting are 3.4 s of a 18.2 s run buying nothing.
    The momentum sweeps are not interchangeable with them: dropping to one
    sweep costs 75% more GMRES iterations and is the worst arm at both sizes.
    One cycle measures 3% faster at 48^3 and 18% at 32^3 and is NOT taken --
    it is the only arm whose iteration count rises, i.e. the edge of the flat
    region, and the 32^3 part of its margin is an artifact of that arm
    happening to reassemble four times instead of five.

    THE DEFAULT IS NOW ONE, and the sentence above is why it took a cross-case
    sweep to get there: it rejected one cycle on ITERATION COUNT, and iteration
    count does not predict wall clock.  Interleaved A/B/A/B on a quiet box,
    three cases:

        case                        cycles=2        cycles=1        wall     GMRES
        LDC 48^3 (orth, isotherm)   14.6 / 14.7 s   14.2 / 14.3 s   -2.7%    +6.5%
        heated cylinder (43 deg)   113.0 / 112.4    96.0 / 95.3    -15.1%   -17.5%
        natural conv. Ra=1e5        37.4 / 37.1     24.0 / 24.1    -35.4%   -34.3%

    One cycle is FASTER IN WALL CLOCK ON ALL THREE, and the 48^3 arm -- the one
    the original decision rested on -- is the only one where the iteration count
    rises at all.  Its 6.5% more iterations still finish 2.7% sooner, because a
    cycle is paid on every Krylov vector while the count is not linear in it.
    The margin there is 0.4 s against a 0.1 s within-arm spread, so it is small
    but not noise.  Both Boussinesq cases move the other way and move hard.

    Anyone raising this back to 2 needs a case where cycles=2 wins on WALL
    CLOCK; there is currently none.

    ``reassemble_drift`` reuses a linearisation while the state has moved less
    than that fraction of its own norm.  Reusing it makes this a Shamanskii /
    chord iteration rather than Newton, and it does cost REAL Newton steps:
    with a fresh Jacobian at every step (``0``) the 48^3 cavity converges in 8
    rather than 23.  **It is still not worth paying for.**  This docstring
    predicted the opposite -- that once assembly got several times cheaper the
    optimum would flip toward fresh Jacobians -- and that prediction has now
    been measured and is FALSE.  Assembly got 2.7x cheaper
    (25.6 s to 9.6 s over five calls at 48^3), and re-running the sweep three
    times interleaved, momentum-weighted flux on, wall clock to the 1e-10
    gate and then to 1e-12:

        drift   Newton steps   GMRES   assemblies   to 1e-10   to 1e-12
        0            8           930       8         20.3 s     20.3 s
        0.2 (ship)  17/23       1494       5         16.6 s     18.1 s
        0.5         29/40       1735       3         15.0 s     16.6 s
        1.0         never       1722       2            --      1.0e-6, STALLED

    A chord step costs 0.29 s here and an assembly 1.9 s, i.e. one assembly
    buys seven chord steps, and a stale Jacobian still contracts the residual
    by 0.49 per step from step 6 on.  The objective is flat between 0.2 and
    0.5 (1.5 s over a 17 s run) and falls off a cliff just past it: at 1.0 the
    contraction degrades to 0.97 per step and the line search starts halving,
    so the run walks instead of converging.  0.2 ships because it sits on the
    flat part with the cliff two settings away, not because it is the minimum.
    The reuse rule is GEOMETRIC (how far the state moved) while what actually
    decides is the observed contraction; a rate-triggered rule would rescue
    the 1.0 arm and is the open improvement here.

    ``precond_shape`` selects which block preconditioner is built; it is passed
    straight through and ``preconditioner.simple_preconditioner`` holds all the
    evidence.  The one thing that belongs HERE is what it does to the OUTER
    loop: ``"restored"`` builds no multigrid hierarchy, so a preconditioner
    rebuild costs milliseconds rather than the 99.7%-host-side AMG setup the
    ``freeze_preconditioner`` note below is about, and ``freeze_preconditioner``
    buys nothing with it.  It also does not on its own reach the
    Eisenstat-Walker request below (0.1 asked, 0.116 delivered at ``restart``
    400 on the measured system), and the measured second half of the device is
    a LARGER ``restart`` -- 1600 crosses 0.1 at iteration 1257 within the same
    matvec budget.  Restart is already a knob; nothing new is added for it.
    ``"none"`` builds no preconditioner at all.  It is the control arm every
    preconditioner claim here has to beat -- on the four captured RANS systems
    it beats the shipped ``"simple"`` on 7 of 8 bundle/window cells -- and it
    is the measured partner for ``equilibrate="ruiz"`` below.

    ``equilibrate`` chooses which SYSTEM the inner solve is posed on, which is
    a different question from which preconditioner is applied to it, and
    ``equilibration.py`` opens with why the two are not interchangeable.
    ``"off"`` ships and leaves this function byte-identical to what it was
    before the knob existed.  ``"ruiz"`` hands GMRES the two-sided
    infinity-norm equilibrated system ``(Dr J Dc) y = Dr (-r)`` and steps
    ``Dc y``; the recovery is CHECKED by ``equilibration.check_recovery`` on
    every step rather than asserted in prose, and the history gains
    ``inner_rel_true``, the true relative linear residual in the original
    variables -- because the ``inner_rel`` a scaled run reports is measured in
    scaled units, and a run judged on that number can read as converged while
    it is not.  The outer loop is untouched by the scaling: the line search,
    the gate, the history and the PTC controller all read ``self.residual``,
    which is the unscaled steady residual and never sees ``Dr``.

    IT COMPOSES WITH ``precond_shape``, AND SHOULD NOT BE COMPOSED WITH IT.
    The composition is exact -- the preconditioner is conjugated to the scaled
    space as ``M_s = Dc M Dr``, which approximates ``(Dr J Dc)^-1`` whenever
    ``M`` approximates ``J^-1`` -- and it is measured worse than the scaling
    alone by an order of magnitude, on both shipped shapes and at both restart
    windows (``turbregime_sst_late``, true floor after 1600 matvecs: Ruiz alone
    0.1255, Ruiz + restored 1.2667, Ruiz + simple 1.0006).  The measured
    configuration is ``equilibrate="ruiz"`` with ``precond_shape="none"``.
    ``block_scaling`` is the other scaling in ``equilibration.py``; the two are
    refused together, because composing them is arithmetic nobody has measured.

    ``matrix_free`` hands the Krylov solve the Jacobian-vector product itself
    instead of the assembled matrix, which is a statement about ACCURACY and
    not about memory.  The assembled path colours a two-hop row pattern, so it
    can only carry a linearisation whose rows reach two hops -- i.e.
    ``coefficient="frozen"``, ``d_f`` detached.  Measured at the
    state the flat-plate march dies on (null-cluster report section 5.1), on a
    central difference of the solver's own residual:

        direction            frozen JVP   implicit JVP   assembled vs implicit
        random               9.40e-05     9.15e-05       1.2e-06
        near-null mode 0     0.2952       1.33e-04       0.0385
        the Newton step      0.9478       8.05e-04       0.2285

    Both shipped arms are accurate on a generic direction and 4-95% wrong
    along the smooth low-wavenumber pressure cluster the residual walks into
    as it falls -- frozen in the JVP, implicit in the ASSEMBLY that cannot
    colour the three-hop rows differentiating ``d_f`` creates.  A product that
    is never assembled has no colouring to outrun, so this mode takes the
    implicit JVP and leaves the assembly out of the operator entirely.

    WHAT IS EXACT AND WHAT IS NOT, because the mode mixes them on purpose.
    EXACT: the Krylov operator (the implicit-``d_f`` JVP of the same residual
    the line search and the gate read, taped at the linearisation point and
    re-taped whenever the assembled path would reassemble), and therefore the
    linear system being solved.  APPROXIMATE: the preconditioner, the Ruiz
    row/column multipliers and the PTC timestep base, all three still read off
    the FROZEN assembled matrix -- which is the standard JFNK arrangement, and
    is legitimate exactly because none of the three changes the fixed point.
    The assembly is therefore still paid; this mode buys accuracy, not
    assembly time.

    IT COSTS ONE DOUBLE-BACKWARD PASS PER KRYLOV VECTOR against a CSR matvec,
    so it is far more expensive per iteration and the report that lands it says
    so with numbers.  ``matrix_free_coefficient`` is the arm switch and exists
    so the frozen JVP can be run as the control that separates "matrix-free"
    from "implicit ``d_f``"; ``"implicit"`` is the only setting with a reason.

    ``ptc`` is pseudo-transient continuation, the cold-start mode for systems
    with no Newton basin from a uniform init -- the steady k-omega /
    Launder-Sharma rows, measured three independent ways in turbulence phase
    studies (docs/research/2026-08-16-turbulence-phase6-ptc-cold-start.md has
    the design sources and every number below).  The residual gains a
    backward-Euler term ``theta |diag(J_steady)| (x - x_n)`` on the
    DIFFERENTIAL rows only (continuity stays algebraic -- Coffey, Kelley &
    Keyes 2003's index-1 DAE form), added inside ``casestate.residual`` so
    the shifted diagonal reaches J, F and R through the one assemble path and
    the preconditioner sees it for free.  The line search reads the UNSTEADY
    residual (Yildirim 2019 eq. 16); the gate, the history and the controller
    read the steady one, recovered by subtracting the known shift.  theta
    follows SER (Kelley & Keyes 1998) under a ceiling
    ``(||r||/||r_0||)^0.5 max(1, theta0)`` -- ADflow's minimum-CFL rule,
    which is what the phase-5 probe lacked when its refusal handler escalated
    theta 12 -> 6.4e3 into a permanent park.  The HANDOVER to pure Newton is
    explicit: at ``ptc_handover`` relative ||r||2 drop the shift disarms, the
    shifted blocks are discarded, and the entry logs ``ptc_event =
    "handover"`` -- the leak every PTC implementation risks is reusing a
    shifted matrix as if it were the steady Jacobian.

    A theta change does NOT discard the linearisation: the shift is
    elementwise, so it moves the matrix by a diagonal and by nothing else, and
    ``_reshift`` writes that diagonal into the blocks already in hand.  It used
    to force an assembly, which made ``reassemble_drift`` dead letter for this
    mode -- SER moves theta every accepted step, and the R1 flat-plate arm
    reassembled 299 of 300 times at drift 3.3e-05 against the 0.2 threshold
    with 62.6% of its wall clock in assembly.

    Measured on the 7-field kOmega MMS box (n=8, zero start, steps to
    max|r| < 1e-11; plain Newton PARKS at 17.3 on this fixture),
    one knob moved per arm around (theta0 10, growth 2, ser, diag,
    handover 1e-4):

        theta0      0.02: 46   0.2: 28   2: 24   10: 32
        growth      1.2: 37    2: 32     10: 31
        controller  ser: 32    mrdm: 59  mrdm at growth 10: 33
        scaling     diag: 32   uniform (global timestep): 41
        handover    1e-2: 29   1e-4: 32  1e-6: 33

    Directions: theta0 has a shallow optimum near 1/CFL ~ 0.5-2 and only
    loses hard when over-conservative (0.02); SER needs only growth ~2 while
    mRDM (growth-only) needs its own alpha ~ 10 to keep up, exactly the
    defaults their sources use; the local (per-row |a_P|) timestep beats the
    single global clock; the handover threshold is flat over four decades.

    THE HANDOVER ROW OF THAT TABLE DOES NOT TRANSFER TO PRODUCTION MESHES.
    512 cells is a regime where the steady Jacobian is solvable by any Krylov
    method, so disarming the shift costs nothing there and the knob cannot
    bind -- "flat over four decades" is a measurement of that, not of the
    knob.  On DataCenter (498,337 cells, 2,491,685 unknowns) the same
    threshold takes GMRES from 426 iterations MEETING tolerance to 1600
    FAILING it, in a single step, and every one of the remaining 45 steps
    burns the full budget without meeting a requested 1e-1.
    The cliff is real at scale.  Removing it is NOT the cure, however, and
    that was measured rather than assumed: ``ptc_handover=0`` (shift never
    disarms) is 4.4% SLOWER in wall clock while doing 7.1% FEWER matvecs, and
    ends 3.6x worse -- it fails the 1e-10 gate where the default passes,
    because damping buys converged inner solves by taking smaller outer steps.
    Keep the default.  docs/research/2026-08-18-dc-ptc-handover.md has the
    A/B, the theta trajectory and the inner-solve stagnation curves.
    The default theta0 keeps ADflow's CFL0 = 5.  Two behaviours that are the
    mode working as designed, not defects: on a warm start or continuation
    rung PTC re-arms and marches instead of letting Newton jump (heated
    cylinder: 44 steps against 19 with the mode off -- leave ``ptc=False``
    where a basin exists), and on the flat plate from freestream the
    RELEASED-from-stage-A march reaches 4.3e-2 and keeps descending where
    the same budget without PTC parked at 2.15e-1, while the UNSTAGED cold
    release rides a diverging turbulence transient uphill and is stopped by
    the five-consecutive-forced-steps counter at 1.1e5 -- PTC follows the
    transient it is given, so staging still owns the startup ordering.

    THE LANDED ROOT IS THE TRANSIENT'S, NOT NECESSARILY THE NEAREST: the
    7-field MMS boxes turned out BISTABLE (two simultaneous roots of one
    residual function, both dynamically stable, gap not shrinking under
    refinement), and every uniform init lies in the basin of the root the
    manufactured solution is NOT.  PTC converges to machine zero there and
    is root-faithful inside a basin (exact-root + 1e-4 noise returns to it
    at 1.3e-10); no transient-respecting globalization can cross a basin
    boundary the physics draws.

    ``release_blocks`` drops the previous linearisation (the blocks and the
    matrix-free tape) BEFORE assembling the next one, instead of after.  It
    changes no arithmetic -- everything dropped is rebuilt from the fresh
    assembly at the same iterate before anything reads it -- and it exists
    because the reassembly instant, where both linearisations are resident at
    once, is the peak moment of the solve and the one that decides whether a
    mesh fits on a card.  Measured on the 1,228,800-cell periodic
    hills (n = 7,372,800), full production recipe, in
    docs/research/2026-08-17-hills-fine-memory.md.  It is OFF by default only
    because it costs a re-``rescale`` on the retry path that reuses a
    linearisation, and every mesh measured before this one already fit.
    The previous PRECONDITIONER, its scaling and every local that pins them or
    the previous ``J`` (the Krylov operator closure, the preconditioner's bound
    ``cycle_policy``, the scaled-system closures) are dropped at that point
    unconditionally (without it the learned coupled
    AMG of DHC88 ran out of memory in its third build with the second
    linearisation's whole hierarchy, its lazily built classical arm and its
    ``J`` still resident); a frozen preconditioner is kept.  Pure
    reference bookkeeping: nothing dropped is read before it is rebuilt.

    ``ptc_max_forced_growth`` optionally bounds the true steady L2 residual
    of a ceiling-forced candidate relative to the current iterate.  A nonfinite
    or excessive candidate is rejected and the solve stops at the last safe
    state with ``ptc_event="forced_rejected"``.  Ordinary accepted pseudo-time
    steps still use the unsteady merit function.  ``None`` preserves the legacy
    forced-step policy; this diagnostic guard does not certify convergence.

    ``ptc_controller="expur"`` (opt-in) replaces SER, the residual ceiling and the forced steps by the EXPur rule
    of Ceze & Fidkowski (2013), AIAA 2013-2686, Eq. 13 (``expur_theta``): after an accepted step a full step raises
    the CFL by ``ptc_growth``, a damped step with lambda >= ``ptc_hold_lambda`` holds it, and lambda below that, or a
    refused step, cuts it by ``ptc_cut``; the CFL stays within [``ptc_cfl_min``, ``ptc_cfl_max``].  A refused step is
    retried from the same state at the cut CFL on a fresh linearisation; a step refused at ``ptc_cfl_min`` on a fresh
    linearisation stops the march at the last accepted state (``ptc_event="refused"``).  The handover to Newton is
    unchanged.

    ``checkpoint(iteration, x)`` is called after every Newton step, accepted or
    not, and is how a solve survives being killed.  It is SEPARATE from
    ``progress`` because ``progress`` receives the history row, which is kept
    for the whole run, and an iterate parked on each row would cost 59 MB per
    step at this size.  A fine hills run lost an hour of
    compute and shipped no VTM and no state because the only write in the
    driver ran after the loop, and the loop raised (commit 02a3a8d).

    ``component_timers`` (opt-in, GPU-vs-OpenFOAM feasibility plan
    E0) splits the per-step cost.  Every history row then gains
    ``jacobian_seconds`` (the ``assemble`` calls of a reassembly, the extra
    one of the first PTC arming included), ``sparsity_seconds`` (the probe
    colouring and probe plan, which the first assembly of a solver instance
    builds and every later one reuses), ``precond_seconds`` (the
    preconditioner build, its wrapper and the Ruiz/block scaling) and
    ``krylov_seconds`` (the inner solve).  Each one is read after
    ``torch.cuda.synchronize``.  On a CUDA device the rows also carry the
    GPU memory of the step (GiB, read off
    the caching allocator, so no sync and no number moves):
    ``cuda_built_gib`` (allocated right after the preconditioner build, the
    resident set the Krylov solve starts from), ``cuda_build_peak_gib`` (the
    peak through the assembly and the build), ``cuda_peak_gib`` (the peak
    through the whole step) and ``cuda_allocated_gib`` (at its end); the build
    pair is NaN on a step without an assembly (a reused linearisation or a PTC
    re-arm, whose build is inside the step's peak).  The allocator's peak
    is reset at the start of every step, after folding it into
    ``self._cuda_peak_bytes`` (the run's peak, which the entry's final
    "peak CUDA memory" line reads).  Without the syncs an asynchronous kernel
    would be charged to whichever component happened to block next: round
    24 could only split its Newton timer by reading log timestamps.  The
    three setup timers add up to ``assembly_seconds`` except for the few
    bookkeeping lines between them (dump hook outside ``assemble``, PTC
    diagonal refresh, anchor copy), and ``assembly_seconds`` is synced too
    while the flag is on.  A PTC re-arm (``_reshift``) builds its
    preconditioner inside ``ptc_reshift_seconds`` and is not split.  The
    split is by WHERE the time is spent, not by what kind of work it is
    -- ``precond_seconds`` is the whole factory call (the
    camg entry's GMRES race or contraction probe and the network forward
    included), and an arm a preconditioner builds lazily INSIDE the Krylov
    solve -- the classical hand-over and l1 rescue cycles of
    ``block_amg.BudgetedLearned`` / ``StagnationRescue`` -- is charged to
    ``krylov_seconds``.  An AMG-setup total therefore needs the entry's
    "rebuilt" / hand-over log lines next to these columns.  The
    probe plan is built ahead of the first ``assemble`` only so that it can
    be timed on its own.  It is the same mesh-only call ``jacobian.assemble``
    makes before its first probe (there it follows the residual tape, which
    it does not read), so the matrices are unchanged.  With the flag off (the
    default) the loop runs exactly what it ran before and the rows carry
    none of these keys.

    ``fixed_jacobian_pattern`` and ``fast_assembly_kernels`` (opt-in,
    see the feasibility plan) change how ``J`` is ASSEMBLED and nothing
    the gate reads.  ``fixed_jacobian_pattern`` ("structural" or "compact")
    is passed to ``jacobian.assemble(fixed_pattern=...)``: the probes are
    unpacked through a probe-to-CSR map built once per probe plan instead of a
    per-probe ``value != 0`` filter (one device sync per probe) and a sort of
    every nonzero per assembly.  "compact" returns the default matrices bit
    for bit in deterministic mode; "structural" also keeps the exact zeros, so
    the pattern is the same at every assembly (for a once-per-run AMG plan),
    and the Krylov matvec then reads those stored zeros too.  The blocks dict
    becomes a ``jacobian.FixedPatternBlocks`` that cuts F/G/D/C/R only when
    read.  ``fast_assembly_kernels`` runs the residual tape and every probe
    with torch's deterministic algorithms off (``linear_kernels``), as
    ``fast_linear_kernels`` does for the preconditioner build and the Krylov
    solve: ``J`` is then reproducible to rounding only.  The residual the line
    search and the gate read is evaluated outside ``assemble`` and stays
    deterministic, so the certificate is unchanged; with PTC on, the shift
    scale is read off ``J``'s diagonal, so the unsteady merit function moves at
    rounding level too.  Both off (the default) make the very call they made
    before.

    JFNK MODE (opt-in; ``Pipeline.CoupledNewton.jfnk`` has the
    design, the cost model and the policy).  ``krylov_operator`` other than
    "assembled", or a ``precond_policy`` (a ``jfnk.PrecondPolicy``), switch the
    loop to: the Krylov operator is the EXACT Jacobian at every Newton step --
    the matrix assembled at this iterate ("assembled", and "auto" on the steps
    that assemble anyway) or the JVP of the residual at this iterate ("jvp",
    and "auto" on the others), computed by ``jvp`` ("tape": ``linearise``, one
    colouring probe per product; "forward": ``linearise_forward``) -- and the
    preconditioner is rebuilt where the policy says and kept otherwise; a step
    that keeps it under "jvp"/"auto" assembles nothing.  ``reassemble_drift``
    is not read (a chord step on a stale matrix is what this mode replaces).
    The policy object holds the kept preconditioner ACROSS calls (rungs, time
    steps) until it rebuilds or is released; ``carry``, ``matrix_free``,
    ``freeze_preconditioner`` and the scalings (``equilibrate``,
    ``block_scaling``) are refused with it.  PTC composes: its shift lives in
    the residual, so the JVP taped after the shift is armed is the shifted
    operator at the current theta; a kept preconditioner keeps the theta of its
    build, and the first arming, a handover and a cutback rebuild.  A refused
    line search on a kept preconditioner rebuilds once and retries, as a
    refused step on a reused Jacobian does on the default path.  Rows gain
    ``jfnk.JFNK_COLUMNS``.  ``precond_policy=None`` with "assembled" -- the
    default -- runs the loop above unchanged.

    NONLINEAR ELIMINATION (opt-in;
    ``Pipeline.CoupledNewton.elimination`` has the method and its sources).
    ``elimination`` is a callable ``(solver, x, residual, assemble) -> (x_hat,
    residual_hat, info)``, normally an ``elimination.NonlinearElimination``.
    While it is switched on it runs at the top of every Newton iteration,
    before the linearisation: it solves the residual rows of the cells with
    the largest residual for those cells' unknowns with every other unknown
    frozen, and the step -- linearisation, Krylov solve, line search, exactly
    as configured -- is then taken FROM ``x_hat`` (right nonlinear
    preconditioning, Luo et al. 2020).  Its ``config.switch`` hands over to
    plain Newton for the rest of the call once ``||r||2 <= switch * ||r(x0)||2``.
    A refused line search after a correction that lowered ``||r||2`` does not
    end the solve: the next iteration eliminates again from ``x_hat`` (the
    stale-preconditioner retry is unchanged).  Rows gain
    ``elimination.NE_COLUMNS``.  Refused with ``ptc`` (two globalisations)
    and ``carry``.  ``None`` -- the default -- runs nothing of this.

    STAGED COLD START (opt-in, ``Pipeline.CoupledNewton.coldstart``).
    ``ptc_weights`` (a nonnegative per-row tensor, ``None`` by default) multiplies the local pseudo-timestep
    scale ``_ptc_base`` row by row at every place it is formed (first arming and the refresh after each
    assembly), so a stage can put the pseudo-transient on some fields only -- zero on rows the stage pins,
    a smaller weight on the mean-flow rows than on k / ln omega in the released stage.  The shift stays an
    elementwise ``d (x - x_ref)`` term of the residual, so ``_reshift``, the unsteady line search and the
    STEADY gate, history and controller are unchanged; weights of exactly 1.0 give bit-identical numbers.
    ``trial_map_ptc_only`` applies ``trial_map`` only while the PTC shift is armed: the positivity map of the
    released stage (``coldstart.BoundedTurbulenceTrial``) bounds the pseudo-transient and is gone after the
    handover, so the terminal Newton phase is the plain linear path and the certificate is the unmodified
    residual's.  A map may expose ``telemetry()`` (a dict merged into the row of a step it ran on).
    ``merit="energy"`` (opt-in; solvers with ``energy(x)``, the potential whose gradient is the residual, e.g. a
    hyperelastic stored energy minus the work of dead loads): the line search also accepts a trial that satisfies the
    Armijo condition ``E(x + lam s) <= E(x) + 1e-4 lam F(x).s`` (``F(x).s < 0``) when ``||r||`` does not decrease --
    the energy line search of nonlinear solid mechanics, where the residual norm grows along a good Newton step under
    large rotations; ``energy(x)`` returning None (no potential, or an opt-in residual term armed) and PTC steps keep
    the plain test.  Rows gain ``energy_accepted``.

    ``merit="jacobi"`` replaces ||r|| by ||D^-1 r|| (D = |diag J_steady| assembled once at the start of the call,
    floored at 1e-6 of its median) in every DECISION -- the backtracking test, the SER ratio, the PTC ceiling and
    handover, the forced-step guard, the Eisenstat-Walker ratio -- and in the history's ``l2`` (``l2_true`` keeps
    ||r||).  Why: on the RANS residual the rows live on scales seven decades apart (periodic hills 160x120x2 from
    the internal field: the omega wall-cell constraint rows O(1), momentum 5e-6, k 2e-7), so
    ||r|| is the omega constraint rows alone and a step that sends k to 1e2 is invisible to the line search until
    it dominates (that run: lambda 1/64 from step 2, forced steps, k residual 1.3e2, nonfinite at step 23).
    ||D^-1 r|| is the size of the diagonal Newton correction in each unknown's own units.  The gate is untouched
    (``max_abs`` < ``tol`` on the residual itself).  In this mode GMRES runs on the row-scaled system
    ``D^-1 J d = -D^-1 r`` (right preconditioner ``M D``), so the inexact Newton condition and ``inner_rel`` are in
    the merit norm and an inexact step still descends it; a preconditioner's own restart policy is not used.
    All three defaults leave every number as it was.

    ``ptc_handover_below`` (opt-in): the PTC shift also disarms -- the same explicit,
    logged handover -- once max|r| of the steady residual is below this level.  For a call that STARTS near a root
    (a warm restart, the cold start's finishing stage) the relative 1e-4 drop of ||r||2 asks for a residual far
    below the gate while a parked 1/CFL stalls the slowest modes (job F2, e80: theta 0.04 for 18 steps at max|r|
    8e-10; cs160-f6: 9e-4 for 30 steps).  None (default): the relative rule alone.

    ``ptc_on_stall = K`` (opt-in): a GENUINE stall -- the line search refused
    a step computed on a fresh linearisation, where the call would otherwise stop, or a ``cap_fallback`` event, where the
    R4 frontier runs began to crawl -- arms pseudo-transient continuation from that state instead, at most K times
    per call (a cap fallback also turns the caps off, as without the option): theta ``ptc_stall_theta0`` (default ``ptc_theta0``), the relative
    handover measured from the stalled residual, the same SER controller, cutback ceiling and explicit handover as a
    ``ptc`` call.  Measured reason (three seeds and the frontier stalls): every failure there is a
    refused Newton direction near a nearly singular J (λ cut to 1/128 and refused with a fresh J and a 1e-8 solve);
    the diagonal shift regularises the near-null mode (Kelley & Keyes 1998).  A run that never stalls never reaches
    the arming point, so its history is the one it always was; rows after an arming carry ``ptc_theta`` /
    ``ptc_event`` (``stall_arm`` on the refused row).  0 (the default) is every call before.
    ``ptc_stall_theta_min`` (opt-in, 12.46 R29): while a stall arming is active the SER controller never takes theta
    below ``min(ptc_stall_theta_min, theta0)`` -- near a root at Ra 2e7 SER shrank theta faster than the residual and
    the near-null mode came back into the solves.  0 (default): SER as for ``ptc``.
    ``accelerator`` (opt-in; ``nonlinear_acceleration.NGMRESAccelerator``, the steady entry's ``--nl-accel``):
    nonlinear GMRES with this Newton step as its right preconditioner (Brune et al. 2015).  After every step the
    line search accepted outside PTC, the least-squares combination ``x_A`` of the stored states is gauged,
    passed through ``candidate_guard`` if any, evaluated once (``merit_residual``) and taken only if its
    ``||r||2`` is below the Newton step's -- the line search's own test -- so the stop test, the rows' ``max_abs``
    and the certificate read the problem's residual at the state taken, as on every other path.  The history
    starts at this call's start and is cleared after the accelerator's ``restart_after`` refusals in a row.
    Refused with ``ptc``, ``ptc_on_stall``, ``elimination``, ``carry``, ``trial_map`` and a merit other than
    "l2".  Rows gain ``nonlinear_acceleration.ACCEL_COLUMNS``.  None (the default) reads nothing.
    """
    if (not isinstance(max_backtracks, int) or isinstance(max_backtracks, bool)
            or max_backtracks <= 0):
        raise ValueError("max_backtracks must be a positive integer")
    if (not isinstance(ptc_on_stall, int) or isinstance(ptc_on_stall, bool) or ptc_on_stall < 0):
        raise ValueError("ptc_on_stall must be a non-negative integer")
    if ptc_stall_theta0 is not None and not (float(ptc_stall_theta0) > 0.0):
        raise ValueError("ptc_stall_theta0 must be positive")
    if not (float(ptc_stall_theta_min) >= 0.0):
        raise ValueError("ptc_stall_theta_min must be non-negative")
    forcing_cap = check_forcing_cap(forcing_cap)          # II-4 job 4 (opt-in); None stays None
    forcing_stall_cap = check_stall_cap(forcing_stall_cap)  # II-4 job 5 (opt-in); None stays None
    if cap_fallback is not None:
        cap_fallback = float(cap_fallback)
        if not (0.0 < cap_fallback <= 1.0):
            raise ValueError(f"cap_fallback needs a step length 0 < LMIN <= 1, not {cap_fallback}")
    if on_cap_fallback is not None and not callable(on_cap_fallback):
        raise ValueError("on_cap_fallback must be callable")
    caps_on = True                                          # II-4 job 5: False after a cap fallback (cap_fallback)
    if candidate_guard is not None and not callable(candidate_guard):
        raise ValueError("candidate_guard must be callable")
    if trial_map is not None and not callable(trial_map):
        raise ValueError("trial_map must be callable")
    if trial_map_ptc_only and trial_map is None:
        raise ValueError("trial_map_ptc_only needs a trial_map")
    if accelerator is not None:
        # Nonlinear GMRES (opt-in): see the docstring.  None -- the default -- reads nothing below.
        missing = [name for name in ("reset", "push", "propose", "outcome", "row")
                   if not callable(getattr(accelerator, name, None))]
        if missing:
            raise ValueError(f"accelerator lacks {missing} (nonlinear_acceleration.NGMRESAccelerator)")
        refused = [name for name, given in (
            ("ptc", ptc), ("ptc_on_stall", ptc_on_stall), ("elimination", elimination is not None),
            ("carry", carry is not None), ("trial_map", trial_map is not None),
            ("a merit other than 'l2'", merit != "l2")) if given]
        if refused:
            raise ValueError("accelerator (nonlinear GMRES) refuses " + "; ".join(refused))
    if merit not in ("l2", "jacobi", "energy"):
        raise ValueError(f"unknown merit {merit!r}; valid: l2, jacobi, energy")
    if merit == "energy" and not callable(getattr(self, "energy", None)):
        raise ValueError("merit='energy' needs a solver with energy(x) (the potential whose gradient is the residual)")
    if merit == "jacobi" and (equilibrate != "off" or block_scaling or cycle_policy is not None):
        raise ValueError("merit='jacobi' scales the Krylov system itself; it is not combined with equilibrate, "
                         "block_scaling or an explicit cycle_policy")
    if ptc_weights is not None:
        # Staged cold start: validated once, moved to the solve's device and dtype.
        if not isinstance(ptc_weights, torch.Tensor) or ptc_weights.numel() != self.n_unknowns:
            raise ValueError(f"ptc_weights must be a tensor of {self.n_unknowns} per-row weights")
        ptc_weights = ptc_weights.detach().reshape(-1).to(device=self.device, dtype=self.dtype)
        if not bool(torch.isfinite(ptc_weights).all()) or bool((ptc_weights < 0).any()):
            raise ValueError("ptc_weights must be finite and nonnegative")
    if ptc_max_forced_growth is not None:
        if not math.isfinite(ptc_max_forced_growth) or ptc_max_forced_growth < 1.0:
            raise ValueError("ptc_max_forced_growth must be finite and >= 1")
    if ptc_controller not in ("ser", "mrdm", "expur"):
        raise ValueError(f"unknown ptc_controller {ptc_controller!r}; valid: ser, mrdm, expur")
    if ptc_controller == "expur":
        if not (0.0 < ptc_hold_lambda <= 1.0 and ptc_cut > 1.0 and ptc_growth >= 1.0
                and 0.0 < ptc_cfl_min <= ptc_cfl_max and math.isfinite(ptc_cfl_max)):
            raise ValueError("expur needs 0 < ptc_hold_lambda <= 1, ptc_cut > 1, ptc_growth >= 1 and "
                             "0 < ptc_cfl_min <= ptc_cfl_max < inf")
    if equilibrate not in EQUILIBRATIONS:
        raise ValueError(f"unknown equilibrate {equilibrate!r}; valid: "
                         f"{', '.join(EQUILIBRATIONS)}")
    if precond_shape not in NEWTON_PRECONDITIONERS:
        raise ValueError(f"unknown preconditioner {precond_shape!r}; valid: "
                         f"{', '.join(NEWTON_PRECONDITIONERS)}")
    if equilibrate != "off" and block_scaling:
        raise ValueError(
            "block_scaling and equilibrate are both scalings of the same "
            "system; running them together is arithmetic nobody has measured")
    if matrix_free and matrix_free_coefficient not in ("implicit", "frozen"):
        raise ValueError(
            f"unknown matrix_free_coefficient {matrix_free_coefficient!r}; "
            "valid: implicit, frozen")
    if fixed_jacobian_pattern is not None and fixed_jacobian_pattern not in ASSEMBLY_PATTERNS:
        raise ValueError(f"unknown fixed_jacobian_pattern {fixed_jacobian_pattern!r}; "
                         f"valid: {', '.join(ASSEMBLY_PATTERNS)}")
    if carry is not None and (matrix_free or not isinstance(carry, dict)):
        raise ValueError("carry must be a dict, and it is not supported with matrix_free")
    if elimination is not None:
        # Nonlinear elimination: see the docstring.  None -- the default -- reads nothing below.
        if not callable(elimination):
            raise ValueError("elimination must be callable (elimination.NonlinearElimination)")
        if ptc or carry is not None:
            raise ValueError("elimination (B5 nonlinear elimination) refuses ptc (a second globalisation) "
                             "and carry")
    # JFNK mode: see the docstring.  Off -- the default -- nothing below reads these names.
    jfnk = krylov_operator != "assembled" or precond_policy is not None
    if jfnk:
        if krylov_operator not in _jfnk.OPERATORS:
            raise ValueError(f"unknown krylov_operator {krylov_operator!r}; valid: {', '.join(_jfnk.OPERATORS)}")
        if jvp not in _jfnk.JVP_KINDS:
            raise ValueError(f"unknown jvp {jvp!r}; valid: {', '.join(_jfnk.JVP_KINDS)}")
        refused = [name for name, given in (
            ("matrix_free (the JFNK operator is krylov_operator='jvp' or 'auto')", matrix_free),
            ("carry (the policy carries the preconditioner across calls)", carry is not None),
            ("freeze_preconditioner (the policy decides the rebuilds)", freeze_preconditioner),
            ("equilibrate / block_scaling (a scaling read off the assembled matrix, not measured stale)",
             equilibrate != "off" or block_scaling)) if given]
        if refused:
            raise ValueError("JFNK mode (krylov_operator / precond_policy) refuses " + "; ".join(refused))
        if precond_policy is None:
            precond_policy = _jfnk.PrecondPolicy("every")
    if int(krylov_basis_chunk) < 0:
        raise ValueError(f"krylov_basis_chunk must be >= 0, not {krylov_basis_chunk}")
    if fixed_jacobian_pattern == "blocks":
        # A block assembly has no scalar J; see the docstring.
        refused = [name for name, given in (
            ("the default (non-JFNK) loop and krylov_operator='assembled' (a step that assembles without "
             "rebuilding the preconditioner has no operator to attach)",
             not jfnk or krylov_operator == "assembled"),
            ("a missing preconditioner_factory (it attaches the operator)", preconditioner_factory is None),
            ("ptc (reads the assembled diagonal)", ptc),
            ("equilibrate / block_scaling (read the scalar J)", equilibrate != "off" or block_scaling),
            ("matrix_free", matrix_free), ("carry", carry is not None),
            ("first_assembly_dump (reads the scalar J)", first_assembly_dump is not None),
            ("elimination", elimination is not None)) if given]
        if refused:
            raise ValueError("fixed_jacobian_pattern='blocks' (M5 block assembly) refuses " + "; ".join(refused))
    # Matrix-free makes the assembled matrix a preconditioner and nothing else,
    # which is what lets a global-reduction BC (Bai Eq. 15's area mean) run at
    # all: its rank-1 block is exact in the JVP and merely absent from the
    # preconditioner.  Set before the first ``assemble``, since the sparsity is
    # built once and cached on the first one.  JFNK: the assembled matrix is an
    # operator unless the operator is always the JVP.
    self.assembly_is_operator = (not matrix_free) if not jfnk else krylov_operator != "jvp"
    self._sparsity = None
    x = (torch.zeros(self.n_unknowns, device=self.device, dtype=self.dtype)
         if x0 is None else x0.clone().reshape(-1))
    # Resolved BEFORE the first projection, not after: gauging a warm start
    # whose pressure level is pinned would move it off its own solution.
    self.resolve_gauge(x)
    x = self.gauge(x)
    if candidate_guard is not None and not candidate_guard(x):
        raise ValueError("candidate_guard rejects initial state")
    history, blocks, precondition, anchor = [], None, None, None
    true_residual_rows, check_row = [], None     # true_residual_check (opt-in): one reading per checked solve
    _e1_first = {"probe": False, "dump": False}
    scale = None
    # The matrix-free operator, ``None`` until it is (re)taped.  It tracks the
    # BLOCKS and not the iterate: reusing a linearisation is drift's decision
    # and it has to be the same decision for both operators, or an A/B between
    # them moves two variables.  Cleared wherever ``blocks`` changes -- an
    # assembly, a PTC re-arm, or a discard -- and rebuilt below at the point
    # where the PTC shift is armed, since the tape differentiates that too.
    product = None
    if carry is not None and not ptc and carry.get("blocks") is not None:
        # ``carry`` (opt-in): the previous call's linearisation; drift decides below.  Not
        # under PTC, whose first iteration must assemble to arm its shift (``ptc_d``).
        blocks, anchor = carry["blocks"], carry.get("anchor")
        precondition, scale = carry.get("precondition"), carry.get("scale")
        if anchor is None:
            blocks = precondition = scale = None

    def rescale(blocks):
        """``(row, col, diag(row) J diag(col))``, or ``None`` for no scaling.

        Rebuilt wherever the matrix moves -- an assembly OR a PTC re-arm --
        because a scaling read off a matrix the inner solve is no longer
        running on is a wrong scaling, not a stale one.
        """
        if equilibrate == "ruiz":
            row, col = ruiz_scaling(blocks["J"], sweeps=ruiz_sweeps)
        elif block_scaling and self.n_unknowns > self.pressure:
            row, col = turbulence_block_scale(self, blocks)
        else:
            return None
        return row, col, scaled_matrix(blocks["J"], row, col)

    def build_preconditioner(blocks):
        with build_kernels(fast_linear_kernels):
            return _build_preconditioner(blocks)

    def _build_preconditioner(blocks):
        if preconditioner_factory is not None:
            return preconditioner_factory(blocks, x)
        if precond_shape == "none":
            return None
        return self.simple_preconditioner(
            blocks, sweeps=sweeps, cycles=cycles, pressure=pressure,
            shape=precond_shape, restore_alpha=restore_alpha)

    # ``component_timers`` only.  The per-reassembly split is
    # held in a dict, not in loose locals: this function is long, and a
    # short local name here once clobbered a loop-carried one (see
    # ``with_polynomial``).
    split_seconds = {"jacobian": 0.0, "sparsity": 0.0, "precond": 0.0, "krylov": 0.0}
    # GPU memory of each step, ``component_timers`` on a CUDA device only.
    cuda_memory = component_timers and torch.device(self.device).type == "cuda"
    step_memory = {}

    def memory_step_begins():
        """Fold the allocator's peak into the run's peak and reset it: the next reading is this step's."""
        if cuda_memory:
            self._cuda_peak_bytes = max(getattr(self, "_cuda_peak_bytes", 0),
                                        torch.cuda.max_memory_allocated(self.device))
            torch.cuda.reset_peak_memory_stats(self.device)
            step_memory.clear()

    def memory_mark(allocated_key, peak_key=None):
        if cuda_memory:
            step_memory[allocated_key] = torch.cuda.memory_allocated(self.device) / 2 ** 30
            if peak_key is not None:
                step_memory[peak_key] = torch.cuda.max_memory_allocated(self.device) / 2 ** 30

    def synced_clock():
        """``time.perf_counter()`` read after the device has drained its queue."""
        if torch.device(self.device).type == "cuda":
            torch.cuda.synchronize(self.device)
        return time.perf_counter()

    def assemble(at):
        """``self.assemble(at)``; with ``component_timers`` its synced cost goes to ``jacobian_seconds``."""
        if not component_timers:
            return assemble_as_flagged(at)
        assemble_began = synced_clock()
        out = assemble_as_flagged(at)
        split_seconds["jacobian"] += synced_clock() - assemble_began
        return out

    def assemble_as_flagged(at):
        """``self.assemble(at)`` under the assembly flags; with both off, exactly that call.

        The keyword reaches ``self.assemble`` only when it is set, so a solver
        (or a wrapper such as the entry's linear-system dump) that never heard
        of it is called unchanged."""
        if fixed_jacobian_pattern is None and not fast_assembly_kernels:
            return self.assemble(at)
        options = {} if fixed_jacobian_pattern is None else {"fixed_pattern": fixed_jacobian_pattern}
        with linear_kernels(fast_assembly_kernels):
            return self.assemble(at, **options)

    def merit_residual(at):
        """``self.residual(at)``; with ``fast_line_search`` without deterministic algorithms.
        Off, ``linear_kernels(False)`` is a no-op and this is exactly that call."""
        with linear_kernels(fast_line_search):
            return self.residual(at)
    residual = merit_residual(x)
    # ``merit="jacobi"`` (opt-in, staged cold start): the line search, the PTC controller, its
    # ceiling and handover and the forcing term read ||D^-1 r|| with D = |diag J| of the steady Jacobian at
    # the start of this call (fixed for the call), i.e. the diagonal Newton correction in each unknown's own
    # units, instead of ||r||.  The gate still reads max|r|.  "l2" (default): the plain ||r||.
    merit_w = None
    if merit == "jacobi":
        diagonal = _csr_diagonal(assemble(x)["J"]).abs()
        merit_w = 1.0 / diagonal.clamp_min(max(1.0e-6 * float(diagonal.median()), 1.0e-300))
        diagonal = None

    def norm_of(vector):
        return float(vector.norm()) if merit_w is None else float((merit_w * vector).norm())

    previous = norm_of(residual)
    entry = {"it": 0, "max_abs": float(residual.abs().max()), "l2": previous}
    if merit_w is not None:
        entry["l2_true"] = float(residual.norm())
    history.append(entry)
    # the operator-decided l1 rule's decisions since this solve began (``block_amg.l1_rule_columns``)
    from Pipeline.CoupledNewton import block_amg as _block_amg
    l1_seen = len(_block_amg.L1_RULE_DECISIONS)
    if progress is not None:
        progress(entry)

    if accelerator is not None:
        # Nonlinear GMRES (opt-in): this call's history starts at its starting state.
        accelerator.reset()
        accelerator.push(x, residual)

    # --- pseudo-transient continuation state (all inert when ptc=False) ---
    # ``theta`` is the ACTIVE 1/CFL -- the one the armed residual shift and
    # the stored matrix both carry; ``theta_target`` is the controller's ask,
    # and the two are reconciled only where the matrix can be moved with them
    # (an assembly, or the diagonal re-arm ``_reshift``), so the matrix never
    # claims a diagonal the residual does not have.
    ptc_active = bool(ptc)
    theta = theta_target = float(ptc_theta0) if ptc_active else 0.0
    if ptc_active and ptc_controller == "expur":
        # the EXPur CFL range [cfl_min, cfl_max] holds from the first step on
        theta = theta_target = min(max(theta, 1.0 / ptc_cfl_max), 1.0 / ptc_cfl_min)
    ptc_d = None                 # the armed shift vector theta * base
    base = None                  # |diag(J_steady)| on the differential rows
    l2_start = max(previous, 1e-300)
    forced = 0                   # consecutive ceiling-forced acceptances
    event = None                 # "handover" / "cutback" / "forced", logged
    stall_arms = int(ptc_on_stall)   # 12.46 (opt-in): PTC armings a genuine stall may still make in this call
    theta_floor = 0.0                # 12.46 R29 (opt-in): the SER controller's floor while a stall arming is active
    ptc_logged = bool(ptc) or stall_arms > 0
    # JFNK mode only: this step's rebuild reason (None: the kept
    # preconditioner serves it), the counted JVP operator and its taping time.
    rebuild_reason = counted = None
    # Whether the elimination still runs in this call (the switch-over turns it off for good).
    eliminating = elimination is not None
    ne_switch = float(getattr(getattr(elimination, "config", None), "switch", 0.0) or 0.0)

    # try/finally: an exception mid-solve must not leave the shift armed --
    # ``differentiable_solution`` and every later residual call on this
    # instance assume the steady function.
    try:
        for iteration in range(1, int(maxit) + 1):
            if history[-1]["max_abs"] < tol:
                break
            if stop_rule is not None and stop_rule(x, residual, iteration - 1):
                history[-1]["stop_rule"] = True
                break
            memory_step_begins()
            if jfnk:
                # The JVP is taped at THIS iterate, so the previous one goes first, before anything is
                # assembled or taped beside it (the tape is the JFNK step's largest object besides J).
                product = counted = operator = None
            ne_info = None
            if eliminating:
                # The elimination, before anything is linearised at this iterate; the step
                # below is taken from its corrected state.  Off for the rest of the call past the switch-over.
                if ne_switch and previous <= ne_switch * l2_start:
                    eliminating = False
                else:
                    x, residual, ne_info = elimination(self, x, residual, assemble_as_flagged)
                    previous = norm_of(residual)
            if ptc_active and (previous / l2_start < ptc_handover or (
                    ptc_handover_below is not None and history[-1]["max_abs"] < ptc_handover_below)):
                # THE HANDOVER, explicit and logged: past this relative drop the
                # march is inside the Newton basin (Yildirim's ANK -> NK switch,
                # made at the same kind of relative threshold) and the remaining
                # theta would only slow the quadratic tail.  The stored blocks
                # carry the shifted diagonal, so they are discarded rather than
                # reused as if they were the steady Jacobian.
                ptc_active, theta, theta_target, ptc_d = False, 0.0, 0.0, None
                self._ptc_shift = None
                blocks = product = None
                event = "handover"
                if jfnk:
                    precond_policy.request("handover")   # nor is a preconditioner built on it kept
            drift = (float((x - anchor).norm() / max(float(anchor.norm()), 1e-30))
                     if anchor is not None else float("inf"))
            if jfnk:
                # The policy decides the rebuild; the operator decides whether this step assembles
                # anyway.  The first PTC arming of a call needs an assembled diagonal (``base``) and
                # changes the operator by a large shift, so it rebuilds as well.
                rebuild_reason = precond_policy.decide(first_of_call=iteration == 1)
                if rebuild_reason is None and ptc_active and base is None:
                    rebuild_reason = "ptc"
                reassemble = rebuild_reason is not None or krylov_operator == "assembled"
            else:
                reassemble = blocks is None or drift > reassemble_drift
            reshift_seconds = 0.0
            if jfnk and not reassemble and ptc_active and theta_target != theta:
                # A new theta on a step that assembles nothing: the shift lives in the residual, so
                # the JVP taped below (after the shift is armed) is the operator at the new theta.
                # No matrix to patch; the kept preconditioner keeps the theta of its build.
                theta, ptc_d = theta_target, theta_target * base
            elif not reassemble and ptc_active and theta_target != theta:
                # A theta change does not discard the linearisation outright:
                # SER moves theta on essentially every accepted step, so the
                # drift rule was being overruled rather than applied: on the R1
                # flat-plate arm 299 of 300 steps reassembled at a measured
                # drift of 3.3e-05 against the 0.2 threshold while 62.6% of the
                # 1278.5 s run was assembly (verification report, item
                # 5).  Re-differentiating the residual buys nothing there,
                # because theta moves the operator by a DIAGONAL and by nothing
                # else -- ``_reshift`` applies that diagonal to the matrix
                # already in hand, which is what the discarded assembly would
                # have produced at this same linearisation point.  Whether to
                # move the linearisation point is once again drift's decision
                # alone.
                started = time.perf_counter()
                patched = _reshift(self, blocks, (theta_target - theta) * base)
                if patched is None:
                    reassemble = True        # no stored diagonal to write into
                else:
                    blocks, theta = patched, theta_target
                    # ``blocks`` holds it now; kept under this name too, the
                    # re-armed J outlived the NEXT assembly and sat beside the
                    # build that follows it
                    patched = None
                    product = None      # the tape carries the OLD theta's shift
                    ptc_d = theta * base
                    # Same rule as an assembly: the preconditioner tracks the
                    # matrix unless the caller froze it.  It is built from the
                    # patched blocks, so it is exactly the preconditioner the
                    # discarded assembly would have handed to GMRES.  The old
                    # one, the old J and their closures go first (as at an
                    # assembly below).
                    matrix = operator = own_policy = None
                    conjugated = scaled_operator = scaled = scale = None
                    if not freeze_preconditioner:
                        precondition = None
                    if precondition is None:
                        precondition = build_preconditioner(blocks)
                        if precond_wrapper is not None:
                            precondition = precond_wrapper(precondition, blocks, x)
                    scale = rescale(blocks)
                    reshift_seconds = time.perf_counter() - started
            if component_timers:
                split_seconds.update(jacobian=0.0, sparsity=0.0, precond=0.0)
            if reassemble:
                started = time.perf_counter() if not component_timers else synced_clock()
                if (component_timers and getattr(self, "_plan", False) is None
                        and hasattr(self, "_probe_plan")):
                    # The probe colouring and plan the first ``assemble`` would
                    # build before its first probe (``jacobian._probe_plan``),
                    # built here instead so it gets its own timer.  It reads
                    # the mesh and ``assembly_is_operator`` alone, both set
                    # above, so the matrices that follow are the same.
                    self._probe_plan()
                    if fixed_jacobian_pattern == "blocks" and hasattr(self, "_block_assembly_map"):
                        # The block assembly's map instead; no scalar map is built
                        self._block_assembly_map()
                    elif fixed_jacobian_pattern is not None and hasattr(self, "_fixed_pattern_map"):
                        # The probe-to-CSR map is mesh-only too
                        # and belongs to this plan; timed with it, built once.
                        self._fixed_pattern_map()
                    split_seconds["sparsity"] = synced_clock() - started
                # THE PEAK MOMENT OF THE WHOLE SOLVE is the instant below, where
                # the OLD linearisation is still resident and the NEW one is
                # being built beside it.  Dropping the old references first
                # makes the assembly and the preconditioner build allocate into
                # the space the old ones were holding instead of on top of them.
                #
                # The preconditioner side goes unconditionally (without it
                # the learned coupled AMG of DHC88, 3.4M unknowns,
                # died in its third build with the previous hierarchy, its
                # lazily built classical hand-over arm and their scalar-CSR
                # copies -- about 23 GiB -- still resident).  Every name that
                # can pin it is cleared, not just ``precondition``: ``own_policy``
                # is its bound ``cycle_policy`` (a bound method holds its
                # object), ``matrix`` / ``operator`` hold the previous ``J``
                # through the next build even after ``blocks`` is replaced, and
                # ``conjugated`` / ``scaled_operator`` / ``scaled`` / ``scale``
                # hold the scaled system's second copy of J's values.
                #
                # This is memory bookkeeping and NOT numerics: every name
                # dropped here is unconditionally rebuilt from the fresh
                # assembly a few lines below, at the same iterate, before
                # anything reads it.  The one case where that is untrue is a
                # FROZEN preconditioner, which is deliberately older than the
                # matrix -- so it is kept, and the caller is told (in
                # ``solve_with_coupled_newton``) that the two levers fight.
                matrix = operator = own_policy = None
                conjugated = scaled_operator = scaled = scale = None
                if jfnk:
                    # JFNK: the previous J goes whatever happens (the operator is
                    # this step's), and the kept preconditioner goes only when it is rebuilt --
                    # until then the policy holds it on purpose.
                    blocks = precondition = None
                    if rebuild_reason is not None:
                        precond_policy.release()
                elif not freeze_preconditioner:
                    precondition = None
                if carry is not None:
                    # Memory release x transient carry: the
                    # dict's references to the previous call's linearisation go with
                    # the locals', or a carried preconditioner and J would stay
                    # resident beside this build (and through a whole --ptc call,
                    # which never takes them).  Nothing reads the dict before the
                    # exit, which writes the final linearisation back.
                    carry.update(blocks=None, anchor=None, precondition=None, scale=None)
                if release_blocks:
                    # The blocks themselves (and the matrix-free tape) too, so
                    # that the ASSEMBLY does not stack on the old ``J``: the
                    # preconditioner closure PINS its blocks -- on
                    # ``shape="restored"`` it captures F, G and D even though its
                    # apply takes the identity branch and can never read them --
                    # which is why the names above go as well.
                    blocks = product = None
                if ptc_active and base is None:
                    # First arming: the local timestep scale is the steady
                    # Jacobian's own diagonal, which does not exist before a first
                    # assembly -- so the first PTC step pays one extra assembly,
                    # once per solve, rather than bolting theta onto a matrix the
                    # residual never carried.
                    self._ptc_shift = None
                    base = _ptc_base(
                        self, _assembly_diagonal(assemble(x)), ptc_scaling)
                    if ptc_weights is not None:
                        base = base * ptc_weights       # staged cold start, opt-in
                if ptc_active:
                    theta = theta_target
                    ptc_d = theta * base
                    self._ptc_shift = (ptc_d, x.detach().clone())
                blocks = assemble(x)
                product = None
                if first_assembly_dump is not None and not _e1_first["dump"]:
                    _e1_first["dump"] = True
                    # The diagnostic dumps (--cell-cond-dump, --face-adj-dump) read
                    # the matrix PATTERN; a "structural" J's stored zeros would add adjacency the
                    # default J does not have, so they get today's matrix.  Default: the same call.
                    first_assembly_dump(blocks["J"] if fixed_jacobian_pattern != "structural"
                                        else drop_explicit_zeros(blocks["J"]))
                if ptc_active:
                    # Refresh the timestep scale for the NEXT arming from this
                    # assembly's own diagonal; the shift contributes exactly
                    # ``ptc_d`` to it, so the steady diagonal is recovered by
                    # subtraction instead of a second assembly.
                    base = _ptc_base(
                        self, _assembly_diagonal(blocks) - ptc_d, ptc_scaling)
                    if ptc_weights is not None:
                        base = base * ptc_weights       # staged cold start, opt-in
                # The PRECONDITIONER may be older than the matrix it preconditions.
                # Measured over four interleaved pairs on the heated cylinder:
                # building it once per continuation rung and never again is -9.1%
                # wall AND -2.4% Krylov iterations, gate passing.  It costs two
                # Newton steps and removes five AMG setups, and the setups are worth
                # more -- 99.7% of a preconditioner build is host-side AMG
                # construction that a fresher matrix would not make cheaper.
                # A stale preconditioner cannot move the fixed point: the residual
                # and the matvec both stay exact, so this is a change of
                # PRECONDITIONING, not of the equations.
                if component_timers:
                    precond_began = synced_clock()
                if jfnk:
                    # The policy's build (or, under "assembled", the kept preconditioner on a
                    # step that assembled only for the operator).
                    if rebuild_reason is not None:
                        precondition = build_preconditioner(blocks)
                        if precond_wrapper is not None:
                            precondition = precond_wrapper(precondition, blocks, x)
                        precond_policy.built(precondition)
                    else:
                        precondition = precond_policy.precondition
                elif precondition is None or not freeze_preconditioner:
                    precondition = build_preconditioner(blocks)
                    if precond_wrapper is not None:
                        precondition = precond_wrapper(precondition, blocks, x)
                scale = rescale(blocks)
                if component_timers:
                    split_seconds["precond"] = synced_clock() - precond_began
                memory_mark("cuda_built_gib", "cuda_build_peak_gib")
                anchor = x.clone()
                assembly_seconds = ((time.perf_counter() if not component_timers else synced_clock())
                                    - started)
            else:
                assembly_seconds = 0.0
                if jfnk:
                    # A step that keeps the preconditioner and assembles nothing: the previous J is
                    # no operator of this step (the JVP is), so nothing keeps it -- the preconditioner
                    # holds whatever it reads.
                    blocks = matrix = None
                    precondition = precond_policy.precondition
            if ptc_active:
                # The shift's reference must be THIS iterate even when the matrix
                # is reused: the term is ``D (x - x_ref)``, and a stale reference
                # would make the residual read the ddt of a step already taken.
                self._ptc_shift = (ptc_d, x.detach().clone())

            linearise_seconds = 0.0
            if matrix_free and product is None:
                # AFTER the shift is armed, because the tape differentiates the
                # residual the shift is part of; its contribution is diag(ptc_d)
                # whatever the reference is, so only theta has to be current.
                started = time.perf_counter()
                product = self.linearise(x, coefficient=matrix_free_coefficient)
                linearise_seconds = time.perf_counter() - started

            jvp_setup_seconds, jvp_used = 0.0, ""
            if not jfnk:
                matrix = blocks["J"]
                # The linear map the inner solve runs on, and the one every check of
                # that solve has to be taken against.  Assembled or matrix-free, it
                # is a callable from here down, so the two arms differ in ONE
                # binding and not in a branch through the solve.
                operator = (lambda v: matrix @ v) if product is None else product
            elif krylov_operator == "assembled" or (krylov_operator == "auto" and reassemble):
                # JFNK on a step that assembled at this iterate: that matrix IS the exact
                # operator, and a CSR matvec is the cheapest spelling of it.
                matrix = blocks["J"]
                operator = (lambda v: matrix @ v)
            else:
                # JFNK on the JVP: taped (or checked, for forward mode) AFTER the shift is
                # armed, at this iterate.  A J assembled at this step for the preconditioner
                # alone ("jvp") is dropped before the tape is built beside it.
                blocks = matrix = None
                jvp_began = synced_clock() if component_timers else time.perf_counter()
                with linear_kernels(fast_assembly_kernels):
                    product, jvp_used = _jfnk.exact_jvp(self, x, kind=jvp)
                jvp_setup_seconds = (synced_clock() if component_timers else time.perf_counter()) - jvp_began
                counted = _jfnk.CountedOperator(
                    product, synchronize=(self.device if component_timers
                                          and torch.device(self.device).type == "cuda" else None))
                operator = counted
            # first_restart (opt-in): the rows from the retaken first step on; the whole history otherwise
            restart_at = int((first_restart or {}).get("offset", 0))
            rows = history[restart_at:] if restart_at else history
            restart_maxit = (first_restart or {}).get("maxit") if restart_at else None
            # Eisenstat-Walker choice 2 / F1b-d's first forcing / II-4 job 4's endgame cap (opt-in): inner_forcing
            requested, forcing_capped = inner_forcing(rows, rtol, forcing=forcing, first_forcing=first_forcing,
                                                      forcing_cap=forcing_cap if caps_on else None,
                                                      stall_cap=forcing_stall_cap if caps_on else None,
                                                      retry_loose_stall=retry_loose_stall)
            # II-4 job 5 (opt-in): whether the stagnation trigger held for this step (logged with the row)
            stall_triggered = (forcing_stall_cap is not None and forcing and caps_on
                               and forcing_stalled(rows, forcing_stall_cap[0]))
            # Opt-in: an iteration budget for the first solve of this call; {} = the default call
            first_budget = restart_maxit if restart_maxit is not None else first_maxit
            first_cap = ({"max_iterations": int(first_budget)} if first_budget is not None and len(rows) == 1
                         else {})
            # linear_cap (opt-in): a per-step iteration budget the caller
            # decides (the armed order-1 switch's threshold); the smaller of it and first_maxit; None = no change
            dynamic_cap = linear_cap() if linear_cap is not None else None
            if dynamic_cap is not None:
                first_cap = {"max_iterations": min(int(dynamic_cap),
                                                   int(first_cap.get("max_iterations", dynamic_cap)))}
            inner_rel_true = None

            def with_polynomial(krylov_map, block):
                """``block``, then a minimum-residual polynomial on top of it.

                The polynomial is built for the map the Arnoldi recurrence will
                actually run on -- ``B = krylov_map . block`` -- so the composed
                preconditioner is ``block . p_B`` and the iteration sees
                ``B p_B``.  Shared by the scaled and unscaled branches because
                ``--poly-degree`` must not silently depend on ``--equilibrate``:
                the DataCenter arms run unequilibrated, and a version of this
                that lived only in the scaled branch was a no-op for them.

                Rebuilt every Newton step.  It costs ``poly_degree`` matvecs
                against the thousands a cycle spends, and a cache would buy 0.4%
                at the price of a staleness bug the residual history cannot show.

                NAMES HERE ARE DELIBERATELY LONG.  ``solve_newton`` is one long
                function whose PTC branch owns ``base`` ACROSS iterations; a
                local of that name here type-errored on Newton 2 of a 200-step
                march, having survived Newton 0 and 1 because the clobber is only
                read on the following step.
                """
                if not poly_degree:
                    return block
                composed_map = (krylov_map if block is None else
                                (lambda v: krylov_map(block(v))))
                polynomial = polynomial_preconditioner(
                    composed_map, residual.numel(), poly_degree,
                    device=residual.device, dtype=residual.dtype)
                if block is None:
                    return polynomial
                return lambda v, m=block, q=polynomial: m(q(v))

            if component_timers:
                krylov_began = synced_clock()
            step_probe = (restart_probe if (restart_probe is not None
                            and not _e1_first["probe"]) else None)
            _e1_first["probe"] = _e1_first["probe"] or step_probe is not None
            check_row = None               # true_residual_check: this step's reading
            if scale is None:
                # A preconditioner may carry its own restart-boundary policy (the budgeted learned
                # cycle of the camg entry); an explicit cycle_policy wins.
                own_policy = (getattr(precondition, "cycle_policy", None)
                              if cycle_policy is None and not poly_degree and merit_w is None else None)
                if hasattr(precondition, "target_rtol"):
                    precondition.target_rtol = requested    # the stagnation rescue rescues loose solves only
                if jfnk and hasattr(precondition, "set_current_blocks"):
                    # block_amg._FreshEscalation: a KEPT preconditioner's fallback arm may continue on
                    # this step's own assembled matrix; a rebuild step's arms are built on it already
                    precondition.set_current_blocks(blocks if rebuild_reason is None else None)
                if merit_w is None:
                    krylov_map, krylov_rhs, krylov_block = operator, -residual, precondition
                else:
                    # merit="jacobi": the inexact Newton condition must hold in the MERIT norm, or
                    # the step need not descend it -- a run (hills 80x60x2, frozen-nu_t stage, exact LU)
                    # stalled at max|r| 4.2e-9 with "no step reduced the residual" while GMRES met eta = 0.1 in
                    # the 2-norm.  So GMRES runs on the row-scaled system W J d = -W r (W = merit_w, rows only:
                    # the unknown d is unchanged) with the preconditioner M W^-1 on the right, and ``inner_rel``
                    # is that scaled residual.  An exact solve gives the same d either way.
                    krylov_map = (lambda v, op=operator, w=merit_w: w * op(v))
                    krylov_rhs = -(merit_w * residual)
                    krylov_block = (None if precondition is None else
                                    (lambda v, m=precondition, w=merit_w: m(v / w)))
                def main_solve():
                    with linear_kernels(fast_linear_kernels):
                        return gmres(krylov_map, krylov_rhs,
                                     M=with_polynomial(krylov_map, krylov_block),
                                     rtol=requested, restart=restart,
                                     max_cycles=max_cycles, deflate=deflate,
                                     restart_probe=step_probe,
                                     cycle_policy=cycle_policy if cycle_policy is not None else own_policy,
                                     **first_cap,
                                     # M5 (opt-in): only when set, so a default call is the call it always was
                                     **({"basis_chunk": int(krylov_basis_chunk)} if krylov_basis_chunk else {}),
                                     **({"deflate_after_switch": int(deflate_rescued)} if deflate_rescued else {}))
                result = main_solve()
                step = self.gauge(result.x)
                if true_residual_check:
                    # opt-in: the step's true linear residual against the solve's own report; a step that misses
                    # both by the factor is solved again under ``true_residual_redo`` (the entry: the coarsest
                    # level by its LU instead of the explicit inverse), and the better of the two is taken
                    result, step, check_row = true_residual_guard(
                        result, step, krylov_map, krylov_rhs, requested, float(true_residual_check),
                        main_solve, true_residual_redo, self.gauge)
                    true_residual_rows.append(check_row)
                if _krylov_audit.KRYLOV_AUDIT["on"]:
                    _krylov_audit.audit(krylov_map, krylov_rhs, result, step, merit_residual, x, precondition,
                                        self.gauge, pressure_slice=(slice(self.velocity, self.pressure)
                                                                    if hasattr(self, "pressure") else None))
                krylov_map = krylov_rhs = krylov_block = None
            else:
                # GMRES on the scaled system ``(Dr J Dc) y = Dr (-r)`` with the
                # SAME preconditioner conjugated to the scaled space
                # (M_s = Dc M Dr approximates (Dr J Dc)^-1 exactly when M
                # approximates J^-1), then step ``Dc y``.  The residual the
                # stopping test reads is therefore the SCALED one -- which is
                # the point of posing the scaled system at all, and which is
                # why the step is recovered in the original variables here and
                # the recovery is checked rather than trusted.
                row_scale, column_scale, scaled = scale
                inverse_row, inverse_column = 1.0 / row_scale, 1.0 / column_scale
                conjugated = (None if precondition is None else
                              (lambda v: inverse_column * precondition(
                                  inverse_row * v)))
                # Prescaled CSR values when the operator is a matrix (see
                # ``scaled_matrix``: free per Krylov vector, and the form every
                # recorded floor was measured on); the same product taken
                # outside the sum when it is a JVP, which is the only spelling
                # available there and costs two elementwise multiplies.
                scaled_operator = (
                    (lambda v: scaled @ v) if product is None else
                    (lambda v: row_scale * product(column_scale * v)))
                right_hand_side = -residual
                with linear_kernels(fast_linear_kernels):
                    result = gmres(scaled_operator,
                                   row_scale * right_hand_side,
                                   M=with_polynomial(scaled_operator, conjugated),
                                   rtol=requested, restart=restart,
                                   max_cycles=max_cycles, deflate=deflate,
                                   restart_probe=step_probe,
                                   cycle_policy=cycle_policy, **first_cap,
                                   **({"basis_chunk": int(krylov_basis_chunk)} if krylov_basis_chunk else {}),
                                   **({"deflate_after_switch": int(deflate_rescued)} if deflate_rescued else {}))
                recovered = column_scale * result.x     # ORIGINAL variables
                inner_rel_true = check_recovery(
                    operator, scaled_operator, row_scale, result.x, recovered,
                    right_hand_side)
                step = self.gauge(recovered)
            if component_timers:
                split_seconds["krylov"] = synced_clock() - krylov_began
            if jfnk and hasattr(precondition, "set_current_blocks"):
                precondition.set_current_blocks(None)        # drops the step's matrix and any arm built on it
            if jfnk:
                # The rule reads the solve that just ran: its iterations per decade against the
                # reference solve of this preconditioner, and whether it met its tolerance.
                achieved = (result.cycle_residuals[-1] if result.cycle_residuals else
                            result.residuals[-1] if result.residuals else 0.0)
                its_per_decade = precond_policy.observe(result.iterations, achieved, result.converged,
                                                        rebuilt=rebuild_reason is not None)

            # ``contraction_probe`` (opt-in): Deuflhard's a-priori
            # contraction estimate at the start of this call, Theta_0 = ||dbar_1|| / ||d_0||, with dbar_1 the
            # simplified Newton correction at the full trial point x + d_0, solved with the SAME operator,
            # preconditioner and tolerance as d_0 (Proposition 2's quantity: Theta_0 ~ h_0 / 2, scale-invariant per
            # field).  It reads one residual and runs one extra linear solve; x, the step and every later number of
            # the call are unchanged.  Plain (unscaled, l2-merit, non-PTC) steps only; ``False`` is every call before.
            probe_row = None
            if contraction_probe and iteration == 1:
                probe_row = {"contraction_theta": "", "contraction_theta_max": "",
                             "contraction_probe_inner_rel": "", "contraction_probe_iterations": ""}
                if scale is None and merit_w is None and not ptc_active:
                    probe_rhs = -merit_residual(self.gauge(x + step))
                    with linear_kernels(fast_linear_kernels):
                        probe = gmres(operator, probe_rhs, M=with_polynomial(operator, precondition),
                                      rtol=requested, restart=restart, max_cycles=max_cycles, deflate=deflate,
                                      **({"basis_chunk": int(krylov_basis_chunk)} if krylov_basis_chunk else {}))
                    simplified = self.gauge(probe.x)
                    probe_rhs = None
                    step_norm = float(step.norm())
                    probe_row["contraction_theta"] = float(simplified.norm()) / max(step_norm, 1.0e-300)
                    # per-field ratios over the component-major blocks of n_cell unknowns (the solver object carries
                    # n_cell; the field list lives on the entry, so the block count is read off the length)
                    n_cell = int(getattr(self, "n_cell", 0) or 0)
                    fields = step.numel() // n_cell if n_cell and step.numel() % n_cell == 0 else 0
                    if fields > 1:
                        d0 = step.reshape(fields, n_cell).norm(dim=1)
                        d1 = simplified.reshape(fields, n_cell).norm(dim=1)
                        ratios = [float(b) / float(a) for a, b in zip(d0, d1) if float(a) > 0.0]
                        probe_row["contraction_theta_max"] = max(ratios) if ratios else ""
                    probe_row["contraction_probe_inner_rel"] = probe.residuals[-1] if probe.residuals else 0.0
                    probe_row["contraction_probe_iterations"] = probe.iterations
                    simplified = None

            # ``step_norm_log`` (opt-in): the size of the computed Newton
            # correction of every step, ||d_k||_2 and per field ||d_k,f|| / ||x_f|| (component-major blocks of n_cell
            # unknowns, the per-field reading of probe_row above), before the line search.  At the first step of a
            # call it is Proposition 2's ||J(q0)^-1 F(q0)|| up to the solve's relative error, the quantity the
            # residual safeguard stands in for.  Logged only; x and the step are unchanged.
            step_row = None
            if step_norm_log:
                step_row = {"step_l2": float(step.norm()), "step_rel_max": "", "step_rel_fields": ""}
                n_cell = int(getattr(self, "n_cell", 0) or 0)
                fields = step.numel() // n_cell if n_cell and step.numel() % n_cell == 0 else 0
                if fields >= 1:
                    d_f = step.reshape(fields, n_cell).norm(dim=1)
                    x_f = x.reshape(fields, n_cell).norm(dim=1)
                    rel = [float(a) / max(float(b), 1.0e-300) for a, b in zip(d_f, x_f)]
                    step_row["step_rel_max"] = max(rel)
                    step_row["step_rel_fields"] = ";".join("%.4e" % r for r in rel)
            if STEP_DUMP.get("path") and not STEP_DUMP.get("done"):
                # ``STEP_DUMP`` (opt-in, the steady entry's --save-first-correction): the process's first computed
                # Newton correction, before the line search, saved once with the iterate it corrects
                torch.save({"step": step.detach().cpu(), "x": x.detach().cpu()}, STEP_DUMP["path"])
                STEP_DUMP["done"] = True

            # Backtrack until the step reduces the residual, and REFUSE it if none
            # of them does.  Committing the last candidate tried would let the
            # iterate wander uphill while the log still showed a lambda, which is
            # how a stalled run reads like a converging one.
            # During PTC the residual the line search reads is the UNSTEADY one
            # (the armed shift adds ``D (y - x)`` to every trial): a pseudo-step is
            # a backward-Euler step, and demanding a monotone STEADY residual of it
            # refuses legitimate transient physics -- which is exactly what the
            # phase-5 probe did, and its SER then escalated theta without bound
            # (12 -> 6.4e3) trying to satisfy a criterion PTC is not supposed to
            # meet.  Yildirim 2019 eq. 16 is the precedent: ADflow's backtracking
            # minimises the unsteady residual norm.  At lam = 0 the shift term
            # vanishes, so ``previous`` (the steady norm at x) is the correct
            # base of comparison for both regimes.
            lam, accepted = 1.0, False
            forced_rejected = False
            forced_diagnostics = None
            guard_rejections = 0
            last_guard_allowed = True
            trial = None
            trial_norm = float("inf")
            # ``trial_map_ptc_only`` (staged cold start): the map bounds the pseudo-transient only.
            mapping = trial_map if (trial_map is not None and (ptc_active or not trial_map_ptc_only)) else None
            # merit="energy" (opt-in): a trial whose residual norm does not decrease is still accepted when it
            # satisfies the Armijo condition on the solver's energy, E(x + lam s) <= E(x) + 1e-4 lam F(x).s with
            # F(x).s < 0 (the residual is dE/dx); outside PTC only.  Every other merit takes the plain test.
            energy_at = energy_slope = raw_energy = raw_slope = None
            if merit == "energy" and not ptc_active:
                raw_energy = self.energy(x)
                if raw_energy is not None:
                    raw_slope = float(torch.dot(residual.reshape(-1), step.reshape(-1)))
                    if raw_slope < 0.0:
                        energy_at, energy_slope = raw_energy, raw_slope
            energy_accepted = False
            bt_limit = max_backtracks
            bt = 0
            while bt < bt_limit:
                bt += 1
                trial_state = x + lam * step if mapping is None else mapping(x, step, lam)
                if (not isinstance(trial_state, torch.Tensor) or trial_state.shape != x.shape
                        or trial_state.dtype != x.dtype or trial_state.device != x.device):
                    raise ValueError("trial_map must preserve state shape, dtype and device")
                candidate = self.gauge(trial_state)
                last_guard_allowed = candidate_guard is None or bool(candidate_guard(candidate))
                if not last_guard_allowed:
                    guard_rejections += 1
                    if retry_loose_stall and bt_limit < max_backtracks + 6:
                        bt_limit += 1
                    trial = None
                    trial_norm = float("inf")
                    lam *= 0.5
                    continue
                trial = merit_residual(candidate)
                trial_norm = norm_of(trial)
                if trial_norm < previous:
                    accepted = True
                    break
                if energy_at is not None and math.isfinite(trial_norm):
                    energy_trial = self.energy(candidate)
                    if energy_trial is not None and energy_trial <= energy_at + 1.0e-4 * lam * energy_slope:
                        accepted = energy_accepted = True
                        break
                lam *= 0.5
            if (not accepted and retry_loose_stall and merit == "energy" and not ptc_active
                    and raw_energy is not None and raw_slope is not None and raw_slope > 0.0):
                lam_rev = 1.0
                for _ in range(max_backtracks + 4):
                    rev_state = x - lam_rev * step if mapping is None else mapping(x, -step, lam_rev)
                    cand_rev = self.gauge(rev_state)
                    if candidate_guard is not None and not bool(candidate_guard(cand_rev)):
                        lam_rev *= 0.5
                        continue
                    tr_rev = merit_residual(cand_rev)
                    tn_rev = norm_of(tr_rev)
                    if math.isfinite(tn_rev):
                        e_rev = self.energy(cand_rev)
                        if tn_rev < previous or (e_rev is not None and e_rev <= raw_energy - 1.0e-4 * lam_rev * raw_slope):
                            candidate, trial, trial_norm, lam = cand_rev, tr_rev, tn_rev, lam_rev
                            accepted = True
                            energy_accepted = not (tn_rev < previous)
                            break
                    lam_rev *= 0.5
            if not accepted and ptc_active and ptc_controller == "expur":
                # EXPur (Ceze & Fidkowski 2013): a refused pseudo-step cuts the CFL by ``ptc_cut``, never below its
                # minimum, and is retried from the same state on a fresh linearisation; there is no forced step.
                # A step refused AT the minimum CFL on a fresh linearisation would be retried unchanged, so the
                # march stops there, at the last accepted state.
                fresh = (rebuild_reason is not None) if jfnk else assembly_seconds > 0.0
                if theta_target >= (1.0 - 1.0e-12) / ptc_cfl_min and fresh:
                    event = "refused"
                    forced_rejected = True
                else:
                    theta_target = min(theta_target * ptc_cut, 1.0 / ptc_cfl_min)
                    event = "cutback"
                    blocks = product = None
                    if jfnk:
                        precond_policy.request("cutback")
            elif not accepted and ptc_active:
                cap = max(1.0, ptc_theta0) * (previous / l2_start) ** 0.5
                if theta_target < cap * 0.999:
                    # The pseudo-timestep was too large: cut it back (theta up)
                    # and retry from the SAME state with a fresh shifted matrix.
                    # The ceiling is ADflow's minimum-CFL rule inverted -- theta
                    # may never exceed ``(||r|| / ||r_0||)^0.5`` of its own start
                    # scale -- and it is the anti-collapse guard: phase 5's
                    # unbounded escalation parked the run permanently.
                    theta_target = min(theta_target * ptc_growth ** 2, cap)
                    event = "cutback"
                    blocks = product = None
                    if jfnk:
                        precond_policy.request("cutback")     # the retry gets a fresh shifted build
                else:
                    # Only the ceiling fallback is guarded.  Use the STEADY
                    # candidate, not the shifted merit norm, and leave x/r intact
                    # on rejection.  None performs no additional residual math.
                    if not last_guard_allowed:
                        # The PTC ceiling never overrides physical admissibility.
                        event = "candidate_guard_rejected"
                        forced = 99
                    else:
                        if ptc_max_forced_growth is not None:
                            candidate_steady = trial - ptc_d * (candidate - x).reshape(-1)
                            candidate_l2 = norm_of(candidate_steady)
                            candidate_ratio = candidate_l2 / max(previous, 1e-300)
                            forced_diagnostics = {
                                "ptc_forced_candidate_l2": candidate_l2,
                                "ptc_forced_growth_ratio": candidate_ratio,
                                "ptc_max_forced_growth": ptc_max_forced_growth,
                            }
                            forced_rejected = (
                                not math.isfinite(candidate_l2)
                                or candidate_l2 > ptc_max_forced_growth * previous)
                        if forced_rejected:
                            event = "forced_rejected"
                        elif math.isfinite(trial_norm):
                            # Force only the evaluated, admissible final trial.
                            lam *= 2.0
                            accepted = True
                            forced += 1
                            event = "forced"
                        else:
                            event = "nonfinite"
                            forced = 99
            # II-4 job 5 (opt-in): a capped step refused or cut below LMIN is not taken; the caps go for good
            fallback_now = (cap_fallback is not None and caps_on and forcing_capped and not ptc_active
                            and (not accepted or lam < cap_fallback))
            fallback_lambda = (lam if accepted else 0.0) if fallback_now else None
            if fallback_now:
                accepted = False
            if accepted:
                if ptc_active:
                    # The steady residual at the accepted iterate, recovered by
                    # subtracting the known shift term rather than paying a second
                    # forward.  The gate, the history and the SER controller all
                    # read the STEADY residual; only the line search reads the
                    # unsteady one.
                    old_norm = previous
                    steady = trial - ptc_d * (candidate - x).reshape(-1)
                    x, residual, previous = candidate, steady, norm_of(steady)
                    if event != "forced":
                        forced = 0
                    ratio = previous / max(old_norm, 1e-300)
                    if ptc_controller == "expur":
                        theta_target = expur_theta(theta_target, lam, growth=ptc_growth,
                                                   hold_lambda=ptc_hold_lambda, cut=ptc_cut,
                                                   cfl_min=ptc_cfl_min, cfl_max=ptc_cfl_max)
                        theta_target = max(theta_target, theta_floor)
                    else:
                        if ptc_controller == "ser":
                            # Kelley & Keyes 1998: the pseudo-timestep grows as the
                            # inverse residual ratio, with the growth per step capped.
                            factor = min(max(ratio, 1.0 / ptc_growth), ptc_growth)
                        else:
                            # mrdm -- Ceze & Fidkowski / Yildirim: growth only, by the
                            # RELATIVE residual decrease -- no shrink on a rise.
                            factor = ptc_growth ** -max(1.0 - ratio, 0.0)
                        cap = max(1.0, ptc_theta0) * (previous / l2_start) ** 0.5
                        theta_target = max(min(theta_target * factor, cap), theta_floor)
                else:
                    x, residual, previous = candidate, trial, trial_norm
                    if accelerator is not None:
                        # Nonlinear GMRES (opt-in; see the docstring): the combination x_A of the stored
                        # states, evaluated once and taken only below the Newton step's ||r||2 (the line
                        # search's test); the state taken joins the history.
                        proposal = accelerator.propose(x, residual)
                        accel_norm = None
                        if proposal is not None:
                            candidate = self.gauge(proposal)
                            if candidate_guard is None or bool(candidate_guard(candidate)):
                                trial = merit_residual(candidate)
                                accel_norm = norm_of(trial)
                        accel_taken = accel_norm is not None and accel_norm < previous
                        accelerator.outcome(proposal is not None, accel_taken,
                                            None if accel_norm is None else accel_norm / max(previous, 1e-300))
                        if accel_taken:
                            x, residual, previous = candidate, trial, accel_norm
                        proposal = None
                        accelerator.push(x, residual)
            # A failed line search on a STALE Jacobian is not evidence of a stall.
            # Reuse is drift-triggered, so the Jacobian is generally one step old
            # by step 2, and step 2 is where this solver's cold start is hardest
            # even when nothing is wrong -- a healthy cold start with a fresh
            # Jacobian every step was measured RAISING max|r| 3.36e-1 -> 4.79e-1
            # there while lowering ||r||2 3.528 -> 3.385, accepted at lambda = 1,
            # with quadratic convergence beginning only at step 3.  Breaking on
            # the first refusal therefore killed runs one step short of the basin:
            # before this, the cavity at Ra = 1e5 and Ra = 1e6 both stopped at
            # Newton step 2 with "NO STEP REDUCED THE RESIDUAL", and the entry
            # above them carried reassembled = True, i.e. the step that failed was
            # the first one taken on a reused matrix.  The same failure and the
            # same fix candidate are on record from the ODIL campaign.
            #
            # Retry costs one assembly and is taken at most once per state: the
            # retry itself sets assembly_seconds > 0, so a second refusal with a
            # matrix built AT THIS x is a real stall and breaks.
            # JFNK: the operator was exact either way, so what can be stale is the
            # PRECONDITIONER (an inexact direction under a loose forcing term); a step
            # that kept one is retried once on a fresh build.
            stale = ((rebuild_reason is None or (retry_loose_stall and requested > rtol and not history[-1].get("stalled")))
                     if jfnk else assembly_seconds == 0.0)
            entry = {"it": iteration, "max_abs": float(residual.abs().max()),
                     "l2": previous, "lambda": lam if accepted else 0.0,
                     "stalled": not accepted, "drift": drift,
                     "inner_rtol": requested, "inner_iterations": result.iterations,
                     # with true_residual_check: the true residual GMRES ended on (the recurrence beside it, below)
                     "inner_rel": (result.cycle_residuals[-1] if true_residual_check and result.cycle_residuals
                                   else result.residuals[-1] if result.residuals else 0.0),
                     # gmres has always RETURNED this and nothing has ever read it,
                     # which left the biggest cost in the solve unexplained: at the
                     # shipped configuration classical Gram-Schmidt is 37.2 s of a
                     # 113 s solve (profiled), and whether that is two
                     # passes over the basis per column or four depends entirely on
                     # how often the DGKS test at gmres.py:197 fires.
                     "reorthogonalisations": result.reorthogonalisations,
                     "assembly_seconds": assembly_seconds,
                     "reassembled": assembly_seconds > 0.0,
                     "retry_on_fresh_jacobian": (not accepted) and stale
                         and not forced_rejected,
                     # One factor per post-pressure block (empty string = scaling
                     # off), so the run's artifact records what the inner solve
                     # actually minimised.
                     "block_scale": ("" if scale is None or not block_scaling
                                     else "/".join(
                         f"{float(scale[1][self.pressure + i * self.n_cell]):.3g}"
                         for i in range(len(self.unknowns) - 2)))}
            if probe_row is not None:
                entry.update(probe_row)
            if step_row is not None:
                entry.update(step_row)
            if candidate_guard is not None:
                entry["candidate_guard_rejections"] = guard_rejections
            if accelerator is not None:
                # Nonlinear GMRES (opt-in), keys only when asked for (nonlinear_acceleration.ACCEL_COLUMNS):
                # whether this step's combination was evaluated and taken, its ||r||2 ratio, depth, a restart.
                entry.update(accelerator.row())
            if merit == "energy":
                entry["energy_accepted"] = energy_accepted
            if merit_w is not None:
                # ``l2`` above is the merit ||D^-1 r|| in this mode; the plain norm is kept beside it.
                entry["l2_true"] = float(residual.norm())
            if true_residual_check:
                # ``inner_rel`` is the true residual here; the recurrence GMRES's stopping test read, and the guard's
                # readings of the step (redone or not)
                entry["inner_recurrence"] = result.residuals[-1] if result.residuals else 0.0
                if check_row is not None:
                    entry["inner_step_true"] = (check_row["true_redo"] if check_row.get("taken") == "redo"
                                                else check_row["true"])
                    entry["inner_flagged"] = bool(check_row.get("flagged"))
                    entry["inner_redone"] = bool(check_row.get("redone"))
            if trial_map is not None:
                entry["trial_map"] = getattr(trial_map, "label", "custom")
                if trial_map_ptc_only:
                    # Which steps the map actually shaped (PTC armed), and its own telemetry if it has any.
                    entry["trial_map_active"] = mapping is not None
                    if mapping is not None and callable(getattr(mapping, "telemetry", None)):
                        entry.update(mapping.telemetry())
            if equilibrate != "off":
                # ``inner_rel`` above is the recurrence residual of the SCALED
                # system.  This is the same solve's residual in the original
                # variables, i.e. the number an unscaled run's ``inner_rel`` is
                # comparable with -- logged separately and never in place of it,
                # because a scaled residual quoted as if it were the true one is
                # how a run reads as converged without being it.
                entry["equilibrate"] = equilibrate
                entry["inner_rel_true"] = inner_rel_true
            if matrix_free:
                # Keys only when the mode was ASKED FOR, so a default run's
                # history rows stay byte-for-byte what they were.  The tape
                # cost is kept OUT of ``assembly_seconds``: that column is what
                # every before/after comparison of assembly cost reads, and the
                # tape is not an assembly -- the per-Krylov-vector cost, which
                # is where this mode actually spends, is not separable from
                # GMRES and is read off the wall clock.
                entry["matrix_free"] = matrix_free_coefficient
                entry["linearise_seconds"] = linearise_seconds
            if jfnk:
                # JFNK, keys only in the mode (``jfnk.JFNK_COLUMNS``): which operator the
                # Krylov solve ran on, whether the preconditioner was rebuilt and why, how many solves
                # it has served, the rule's measure, and the JVP products of this step (their seconds
                # are part of ``krylov_seconds``; synced per product under ``component_timers`` on a GPU).
                entry["operator"] = "assembled" if counted is None else "jvp"
                entry["jvp_kind"] = jvp_used
                entry["precond_rebuilt"] = rebuild_reason is not None
                entry["rebuild_reason"] = rebuild_reason or ""
                entry["precond_age"] = precond_policy.age
                entry["its_per_decade"] = its_per_decade          # None: not measurable (CSV: empty)
                entry["jvp_products"] = 0 if counted is None else counted.count
                entry["jvp_seconds"] = 0.0 if counted is None else counted.seconds
                entry["jvp_setup_seconds"] = jvp_setup_seconds
            if _block_amg.l1_rule_on():
                # the operator-decided l1 rule, keys only while it is on: the decisions of the builds of this step
                # (``l1_rule``, empty when none was built) and the l1 levels of the latest plan (``l1_levels``)
                l1_columns, l1_seen = _block_amg.l1_rule_columns(l1_seen)
                entry.update(l1_columns)
            if fast_line_search:
                # fast_line_search, keys only when asked for: this row's residual is the
                # non-deterministic one (the certificate is re-evaluated by the entry)
                entry["fast_line_search"] = True
            if forcing_cap is not None or forcing_stall_cap is not None:
                # forcing caps, key only when asked for: the endgame cap set this step's inner_rtol
                entry["forcing_capped"] = forcing_capped
            if forcing_stall_cap is not None:
                # forcing_stall_cap, key only when asked for: the stagnation trigger held for this step
                entry["forcing_cap_stall"] = bool(stall_triggered)
            if cap_fallback is not None:
                # cap_fallback, key only when asked for: this capped step was not taken (the caps went
                # off); the line search's lambda of the discarded step (0: refused) in cap_fallback_lambda
                entry["cap_fallback"] = bool(fallback_now)
                entry["cap_fallback_lambda"] = fallback_lambda if fallback_now else ""
                if fallback_now:
                    entry["stalled"] = fallback_lambda == 0.0      # a refusal only when the line search refused
            if component_timers:
                # Keys only when ASKED FOR, the rule of every opt-in column
                # above: a default row is byte for byte what it was.
                entry["jacobian_seconds"] = split_seconds["jacobian"]
                entry["sparsity_seconds"] = split_seconds["sparsity"]
                entry["precond_seconds"] = split_seconds["precond"]
                entry["krylov_seconds"] = split_seconds["krylov"]
            if cuda_memory:
                # GPU memory of this step (CUDA + component_timers only, so CPU
                # and default rows are unchanged): see the docstring.
                memory_mark("cuda_allocated_gib", "cuda_peak_gib")
                for key in ("cuda_built_gib", "cuda_build_peak_gib", "cuda_peak_gib",
                            "cuda_allocated_gib"):
                    entry[key] = step_memory.get(key, float("nan"))
            # 12.46 (opt-in): the two stall events -- a cap fallback (II-4: the capped step refused or cut below
            # LMIN), or a step refused on a fresh linearisation where the call would stop -- arm PTC instead
            arm_now = (stall_arms > 0 and not ptc_active and not forced_rejected and (fallback_now or (
                not accepted and not stale
                and not (ne_info is not None and ne_info["ne_accepted"]
                         and ne_info["ne_l2_after"] < ne_info["ne_l2_before"]))))
            if arm_now:
                event = "stall_arm"          # logged on the refused row; the arming itself is below
            if ptc_logged:
                # Keys only when the mode was ASKED FOR, so a default run's history
                # rows are byte-for-byte what they were before this mode existed.
                entry["ptc_theta"] = theta if ptc_active else 0.0
                entry["ptc_event"] = event or ""
                # Kept separate from ``assembly_seconds`` on purpose: a re-armed
                # matrix is still the ANCHOR's linearisation, so it must keep
                # reading as stale to the retry rule and to every before/after
                # comparison of assembly cost.
                entry["ptc_reshift_seconds"] = reshift_seconds
                event = None
            if forced_diagnostics is not None:
                entry.update(forced_diagnostics)
            if elimination is not None:
                # Elimination: keys only when the elimination is configured (``elimination.NE_COLUMNS``);
                # a step past the switch-over says so.
                entry.update(ne_info if ne_info is not None else {"ne_active": False})
            if capture_inner:
                # The WHOLE inner residual curve, not just its endpoint.  This
                # is free and it cannot move a number: ``gmres`` already builds
                # this tuple unconditionally (it is what ``iterations`` counts
                # and what the stopping test compares), and this line retains
                # the reference the caller was otherwise dropping on the floor
                # at ``residuals[-1]``.  No new arithmetic, no new allocation,
                # nothing read inside the Krylov loop.
                #
                # It exists because "at which iteration does the linear residual
                # stop improving?" is not answerable from ``inner_rel`` alone,
                # and on this case a step can spend 1600 matvecs failing to buy
                # one digit -- whether those matvecs are earned or wasted is the
                # difference between a large free speedup and no lever at all.
                entry["inner_residuals"] = result.residuals
                # The TRUE ``|b - A x| / |b|`` at each cycle end, in the SAME
                # (possibly scaled) system the recurrence above is measured in.
                # Carried beside it because the pair is the only way to tell a
                # recurrence that drifted from a scaling that flattered: with
                # ``equilibrate`` on, ``inner_rel`` is scaled and
                # ``inner_rel_true`` is unscaled, so those two alone cannot
                # separate the two effects.  Free -- ``gmres`` builds this
                # tuple unconditionally, one fresh residual per cycle, which
                # the stopping test already reads.
                entry["inner_cycle_residuals"] = result.cycle_residuals
            history.append(entry)
            if progress is not None:
                progress(entry)
            if checkpoint is not None:
                # ``x`` is handed over SEPARATELY from ``entry`` and never stored
                # on it: ``history`` outlives every iteration, and a 7.4M-unknown
                # iterate parked on each row would cost 59 MB per Newton step.
                # The callee decides how often to actually write.
                checkpoint(iteration, x)
            if arm_now:
                # 12.46 (opt-in): arm PTC from this state instead of stopping / crawling.  The relative handover
                # and the cutback ceiling are measured from here; the next step assembles (base is None) and
                # builds a fresh preconditioner on the shifted matrix.  A cap fallback also turns the caps off.
                stall_arms -= 1
                ptc_active = True
                ptc_theta0 = float(ptc_stall_theta0) if ptc_stall_theta0 is not None else float(ptc_theta0)
                theta_floor = min(float(ptc_stall_theta_min), ptc_theta0)
                theta = theta_target = ptc_theta0
                ptc_d = base = None
                self._ptc_shift = None
                l2_start = max(previous, 1e-300)
                forced = 0
                blocks = product = None
                if jfnk:
                    precond_policy.request("ptc")
                if fallback_now:
                    caps_on = False
                    if on_cap_fallback is not None:
                        on_cap_fallback(precondition)
                continue
            if fallback_now:
                # II-4 job 5: continue from the same (last good) state without the caps; the caller returns its
                # preconditioner to the production rescue path (the rescue's maxit lifted)
                caps_on = False
                if on_cap_fallback is not None:
                    on_cap_fallback(precondition)
                continue
            if forced_rejected:
                # Progress/checkpoint above report the rejected candidate while
                # retaining the last safe state and its unshifted residual.
                break
            if forced > 4:
                # Five consecutive ceiling-forced steps have not found descent:
                # this is a genuine PTC stall, reported as one instead of walking.
                break
            if not accepted:
                if ptc_active:
                    continue    # cutback path: retry this state, smaller pseudo-step
                if (first_restart and first_restart.get("on_refusal") and not first_restart.get("retaken")
                        and int(first_restart.get("offset", 0)) == iteration):
                    # opt-in (CHT2 README section 19, --first-fallback-l1-on-refusal): the progress hook armed the
                    # retaken first step at THIS refused step; take it from the same state on the cycle the hook
                    # handed over to, instead of stopping on the fresh preconditioner
                    first_restart["retaken"] = True
                    blocks = product = None
                    if jfnk:
                        precond_policy.request("retry")
                    continue
                if not stale:
                    if (ne_info is not None and ne_info["ne_accepted"]
                            and ne_info["ne_l2_after"] < ne_info["ne_l2_before"]):
                        # B5: the elimination lowered ||r||2 at this iteration, so the iterate moved; the next
                        # iteration eliminates again from it (the budget is maxit, as for every step).
                        continue
                    break
                blocks = product = None
                if jfnk:
                    precond_policy.request("retry")

    finally:
        self._ptc_shift = None
        if carry is not None:
            # Handed on only when it is the UNSHIFTED Jacobian; a PTC-shifted one
            # (still armed at exit) would claim a diagonal the next residual lacks.
            keep = blocks is not None and not ptc_active
            carry.update(blocks=blocks if keep else None,
                         anchor=anchor if keep else None,
                         precondition=precondition if keep else None,
                         scale=scale if keep else None)
    return x, history


def differentiable_solution(self, x_star: torch.Tensor, *params,
                            bind, rtol: float = 1.0e-8, restart: int = 400,
                            max_cycles: int = 12, precondition=None,
                            coefficient: str | None = "implicit",
                            matrix_free: bool = False,
                            accept_unconverged: bool = False, report=None):
    """``x*`` re-attached to ``params`` through the implicit function theorem.

    One transposed solve, independent of how many Newton or Krylov iterations
    the forward solve took, and nothing about that solve is taped.

    ``bind(*values)`` is how a parameter reaches the equations, and it has no
    default because there is no safe one.  ``self.residual`` is a function of
    ``x`` alone; every physical coefficient -- viscosity, buoyancy, a boundary
    flow rate -- is read off the case object it was registered on.  A closure
    that merely accepts ``*params`` and ignores them therefore differentiates
    against leaves the residual never touched, and ``autograd.grad``'s
    ``allow_unused`` turns that into a gradient of ``None`` rather than into an
    error.  That is not hypothetical: it is what this function shipped from
    an earlier revision, under ``lambda state, *rest: self.residual(state)``
    and an argument order that raised ``AttributeError`` before the ``None``
    could even be reached.  Neither defect was reachable by any test, because
    ``tests/test_implicit_diff.py`` gates the BUILDING BLOCK and passes its own
    parameter-carrying closure.

    ``bind`` is called once with detached values before the Jacobian is
    assembled, so ``J`` is the linearisation at the parameters the gradient is
    taken at rather than at whatever was last written to the case.

    The adjoint is not self-correcting and the defaults say so.  An inexact
    FORWARD solve costs Newton steps and lands on the same root -- seven orders
    of inner tolerance moved the gradient 4.9e-9 (sweep on LDC 8^3 and
    16^3).  An inexact ADJOINT solve is the gradient's error one-for-one,
    amplified 1 to 30x and growing with refinement, with nothing downstream to
    warn.  Hence ``accept_unconverged=False``: an adjoint that did not reach
    ``rtol`` raises instead of returning a number, and ``max_cycles`` is
    bounded, because the shipped ``None`` allowed ``n`` cycles -- 736,000 of
    them at ~3.6 s each on the heated cylinder, which is a hang, not a solve.
    Pass ``accept_unconverged=True`` only to CHARACTERISE a stall, never to
    report a gradient.

    ``coefficient`` defaults to ``"implicit"`` HERE while the forward Newton
    ships ``"frozen"``, and the two defaults disagree on purpose.  Freezing
    ``d_f = 1/2(V_P/a_P + V_Q/a_Q)`` detaches a term that depends on nu and on u
    through the momentum diagonal ``a_P``.  It changes no residual VALUE, so the
    forward solve lands on the same root and only its convergence rate moves --
    which is why frozen is the right forward default.  It changes two
    DERIVATIVES, and the implicit function theorem needs both of them.  Measured
    on the 8^3 cavity against a four-point central-difference
    plateau of ``dL/dnu = 9.445682235e+01``:

    | ``J``      | ``dr/dnu`` | ``dL/dnu``    | relative error |
    |------------|------------|---------------|----------------|
    | frozen     | frozen     | -1.031e+02    | **2.09**       |
    | frozen     | implicit   | +3.844e+01    | 0.593          |
    | implicit   | frozen     | -6.088e+01    | 1.645          |
    | implicit   | implicit   | +9.4456823e+01| **3.3e-9**     |

    The shipped forward default therefore returned a gradient with **the wrong
    sign and 209% error**, and nothing in the run said so.  Both halves have to
    be implicit; neither one alone recovers it.

    ``matrix_free=True`` applies ``J^T`` through
    :func:`transpose_jacobian_matvec` instead of assembling.  It is the EXACT
    operator: the assembled path colours a two-hop pattern and an implicit
    ``d_f`` reaches three, because ``a_P`` sums over a cell's faces.  Whether
    that costs anything is MESH-DEPENDENT and should not be assumed either way
    -- 2.16e-09 with 56 cells outside the pattern on the cylinder O-grid
    (CLAUDE.md), against 5.115e-15 on the 8^3 orthogonal cavity where the two
    gradients are the same number.  It costs 11.5x the assembled
    adjoint on that cavity and 225x per apply at 147k cells, so it
    is the instrument for checking the assembled path rather than the way to run
    one.

    **A functional that itself evaluates the discretisation must be SEEDED, not
    composed.**  ``forward`` writes its boundary values into the case's buffers
    in place, and the parameter VJP inside the backward pass runs a forward of
    its own.  So a loss like a wall Nusselt number -- which captures the
    production ``snGrad`` by running ``forward`` -- leaves a graph whose tensors
    the backward pass then mutates, and autograd stops with "one of the
    variables needed for gradient computation has been modified by an inplace
    operation ... is at version 4; expected version 2" (measured on
    the cavity).  Compute the cotangent first and hand it over::

        probe = x.detach().clone().requires_grad_(True)
        functional(probe).backward()
        attached = solver.differentiable_solution(x, p, bind=bind)
        attached.backward(gradient=probe.grad)

    which keeps one forward alive at a time and is a Nu evaluation cheaper.  A
    loss that is pure arithmetic on the state vector, like the one
    ``solve_with_coupled_newton.py --grad-check`` uses, can be composed directly.

    ``report``, when given, receives the solve's telemetry dict.
    """
    if not params:
        raise ValueError(
            "differentiable_solution was given no parameters to differentiate "
            "against; it would return x* with an empty graph")
    if not callable(bind):
        raise TypeError(
            "bind must be callable: self.residual is a function of x alone, so "
            "a parameter reaches the equations only by being written onto the "
            "case, and bind(*values) is where that happens")

    if (coefficient == "frozen" or (coefficient is None
                                     and self.solver._mwi_frozen_coefficient)):
        if self.mwi:
            raise ValueError(
                "the momentum-weighted flux is on and d_f is frozen, which "
                "detaches a term that depends on both the state and the "
                "parameters.  Measured on the 8^3 cavity, that returns dL/dnu "
                "= -1.031e+02 where the truth is +9.4457e+01 -- wrong sign, "
                "209% error, and no other symptom.  Pass coefficient="
                "'implicit' (the default here) or turn the flux off")

    frozen_values = tuple(p.detach() for p in params)
    bind(*frozen_values)

    def residual_fn(x, *values):
        # The coefficient context wraps every EVALUATION, not the construction
        # of anything: ``_coefficient`` decides what ``momentum_weighted_flux``
        # detaches while the tape is being built, and both the matrix-free
        # operator and the parameter VJP build a fresh tape on each call.
        with _coefficient(self, coefficient):
            bind(*values)
            out = self.residual(x)
        # Put the DETACHED values back on the case immediately.  ``values`` are
        # autograd leaves during the backward pass, so leaving them bound would
        # hand the next forward solve a graph-tracking viscosity -- taping a
        # Newton run that is supposed to be untaped, and accumulating into a
        # leaf nobody holds any more.  The tape built above keeps its own
        # references to the tensors it used, so replacing the ATTRIBUTE here
        # cannot disturb the VJP that is about to be taken through it; the
        # finite-difference gate in tests/test_adjoint.py is what says so.
        bind(*frozen_values)
        return out

    if matrix_free:
        apply = transpose_jacobian_matvec(residual_fn, x_star, frozen_values)
    else:
        blocks = self.assemble(x_star, coefficient=coefficient)
        transpose = blocks["J"].to_sparse_coo().t().coalesce().to_sparse_csr()
        del blocks

        def apply(v):
            return transpose @ v

    def solve(w):
        # The gauge is the SOLVABILITY condition here, not a preference.  With
        # the pressure level free, the constant pressure mode e spans null(J),
        # range(J^T) is its orthogonal complement, and J^T v = w has a solution
        # only for w perpendicular to e.  ``gauge`` removes exactly that
        # component and is a no-op on a case whose pressure a boundary pins.
        right = self.gauge(w.reshape(-1))
        result = gmres(apply, right, rtol=rtol, restart=restart,
                       max_cycles=max_cycles, M=precondition)
        # Judged on a freshly computed residual rather than on the recurrence's
        # own belief.  The two agree in float64 and disagree by four orders once
        # any part of the operator is narrower, and the believed
        # one is what ``converged`` is built from.
        achieved = float((apply(result.x) - right).norm()
                         / max(float(right.norm()), 1e-300))
        telemetry = {"iterations": int(result.iterations),
                     "converged": bool(result.converged),
                     "requested_rtol": float(rtol),
                     "achieved_rel_residual": achieved}
        if report is not None:
            report(telemetry)
        if achieved > rtol and not accept_unconverged:
            raise RuntimeError(
                f"the adjoint solve reached {achieved:.3e} against a requested "
                f"{rtol:.1e} in {result.iterations} iterations, and the "
                "gradient error tracks that residual one-for-one with no "
                "correction anywhere downstream.  Returning it would report a "
                "number that looks like a derivative and is not one; pass "
                "accept_unconverged=True to characterise the stall instead")
        return result.x

    return implicit_solution(x_star, residual_fn, solve, *params)
