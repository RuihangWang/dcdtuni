"""Algebraic-multigrid V-cycles on an assembled matrix, as a preconditioner.

``SparseSolver.multigrid`` coarsens the pressure Schur complement by STRIDED
INDEXING, which exists only where the cells fill a logically Cartesian lattice.
This module coarsens by AGGREGATION instead, so it needs the matrix and nothing
else: no ``(i, j, k)``, no grid shape, no geometry.  That is the whole reason it
exists -- a locally refined, multi-block, stepped or otherwise unstructured
orthogonal mesh has a Schur complement but no lattice.

THE BACKEND IS A SEAM.  ``PressureHierarchy`` states the contract in two lines
-- build once from a matrix, apply a fixed number of cycles to a vector -- and
``build_hierarchy`` resolves which implementation provides it.  Today there is
one, ``PyamgHierarchy``, whose SETUP runs on the host in pyamg and whose APPLY
runs in torch on the device.  A GPU-native setup (NVIDIA AMGX) would be a second
class behind the same contract and would change nothing above this file.

**Setup and apply are separated and measured separately**, because they are used
at completely different rates: the caller is a Krylov loop that applies this
hundreds of times per Newton step and builds it once.  Every instance records
``setup_seconds`` and ``stage_seconds`` for exactly that reason.  No host
transfer and no scipy call sits inside ``__call__``.

**The matrix may be SINGULAR, and this module is written for that case.**  The
pressure Schur complement of an all-Neumann cavity has the constant vector in
its null space and in its transpose's, and the right-hand side lies in its
range.  Two consequences, both measured on the 16^3 cavity's own
Schur block rather than assumed:

* pyamg's setup completes and its default near-null-space candidate is the
  constant vector, which IS this operator's null space -- smoothed aggregation
  is being handed exactly the mode it was designed around.  Its default coarse
  solver is a pseudo-inverse, which is what makes a singular coarsest level
  well defined at all; a factorising coarse solver would fail outright.
* pyamg's own cycle DOES NOT project the null component out, and the iterate
  drifts along it: over 12 cycles on a zero-mean right-hand side the mean of the
  iterate walked 0.05, 0.18, 0.72, ... to -5.98 while the residual fell
  monotonically at 0.4363 per cycle.  The drift is invisible in the residual and
  fatal in a preconditioner, because a correction with a constant in it moves
  the outer iterate along a direction the outer operator cannot see.

Hence ``project_mean``: the mean is removed from the right-hand side on the way
in and from the correction on the way out.  Both are linear, so the cycle remains
ONE FIXED LINEAR OPERATOR, which is what plain GMRES's Arnoldi recurrence
requires of a preconditioner.

**It is read off the operator, not off a flag**, and that is the whole design of
``resolve_project_mean``.  The projection is right for a matrix whose constant
mode is a gauge and WRONG for one that pins its own level -- a pressure operator
with a Dirichlet patch anywhere, which is what an outlet or an open boundary
produces.  A caller-supplied default cannot carry that distinction, because the
next caller is a different case with a different boundary and the same code:
measured on a Dirichlet Poisson operator built by a caller that
inherited the old ``True``, GMRES stagnated at a TRUE relative residual of
1.17e+02 after 8697 iterations, a symptom indistinguishable from a bad
preconditioner or a broken non-orthogonal correction.  ``constant_mode_defect``
answers it from the row and column sums instead, and refuses to guess when the
matrix's own arithmetic cannot separate the two cases.
"""
from __future__ import annotations

import abc
import time

import torch

AMG_BACKENDS = ("auto", "pyamg", "amgx")

# Weighted Jacobi damps the upper part of the spectrum best around
# ``omega rho = 4/3`` (the classical choice: everything in ``[rho/3, rho]`` is
# then damped by at least 1/3).  Measured on the 16^3 cavity's Schur
# complement with 1 pre- and 2 post-sweeps, asymptotic reduction per V-cycle:
# 1.0 -> 0.6418, 4/3 -> 0.5848, 1.6 -> 0.5384, 1.8 -> 0.5133.  The larger values
# score better and are NOT taken: ``omega`` is set from an ESTIMATED spectral
# radius, and at 1.8 an estimate 4% low already puts ``omega rho`` past the 2.0
# where the smoother stops converging.  4/3 keeps that margin at 33%.
SMOOTHING_SCALE = 4.0 / 3.0

# Aggregation stops here.  pyamg's own default of 10 aggregates this operator
# all the way down to a 1 x 1 matrix, which for a SINGULAR operator is the null
# vector alone and whose pseudo-inverse is therefore zero -- the coarsest level
# then contributes nothing.  Measured on the 16^3 Schur block,
# asymptotic reduction per V-cycle: max_coarse 10 -> 0.5860, 50 -> 0.5845,
# 200 -> 0.5843, 500 -> 0.5847.
MAX_COARSE = 50

# Sweep counts, fixed rather than tuned per call so the action stays one linear
# map.  Measured at 16^3, reduction per V-cycle against sparse
# matvecs per level per cycle: (0,2) 0.6488 / 3 mv, (1,1) 0.6560 / 3,
# (1,2) 0.5842 / 4, (2,2) 0.5239 / 5, (1,3) 0.5240 / 5.  The last two buy 10%
# for 25% more work and are not taken: the outer GMRES count at (1,2) is
# already within 2% of the geometric cycle's (89 against 87 at 16^3).
PRE_SWEEPS = 1
POST_SWEEPS = 2

# Relative cutoff of the dense coarse-level pseudo-inverse, and it is NOT a
# cosmetic default.  The coarsest operator inherits the fine one's null space,
# so it is singular to round-off rather than exactly: measured on the
# 16^3 Schur block, its 17 singular values run 5.6488, ..., 2.4375, 2.2984e-14.
# numpy's default cutoff is 1e-15 * max(m, n) = 1.7e-14, which KEEPS that last
# one and returns a pseudo-inverse of norm 4.4e+13.  The whole preconditioner
# then loses nine digits to cancellation: its additivity defect measured
# 1.168e-07 on an image of order 8, the GMRES recurrence residual parted company
# with the true residual, and the inner solve stalled at a true 2.1e-06 while
# reporting 7.3e-11.  There are thirteen orders of magnitude between the null
# direction and the rest of the spectrum, so any cutoff in that gap works and
# this one sits in the middle of it.
COARSE_RCOND = 1.0e-10

# pyamg's prolongation smoothing calls ``approximate_spectral_radius``, which
# starts from ``np.random.rand`` -- the LEGACY global generator, not a seeded
# one -- so two hierarchies built from the same matrix are not the same
# hierarchy.  Measured on the 8^3 grid Laplacian: level-1 omegas
# 1.0242235435092262 against 1.0242234350346540 between two builds, i.e. 1e-7
# relative.  Inside one inner solve that costs nothing, since GMRES holds one
# instance; ACROSS runs it means the same case does not reproduce, and a
# CPU-against-CUDA gate on iteration counts cannot exist.  The global state is
# saved and restored around the setup, because a library that reseeds its
# caller's generator is a worse bug than the one it fixes.
HIERARCHY_SEED = 0

# Power iterations for the per-level ``rho(D^-1 A)``.  It feeds a damping
# factor, not an answer.  Measured on the 16^3 Schur block, level 0:
# 10 -> 1.6588, 25 -> 1.9319, 50 -> 1.9686, 100 -> 1.9840, against the value 2.0
# that Gershgorin bounds this quantity by for any matrix with zero row sums.
# The estimate is therefore LOW, which is the safe side: it makes ``omega``
# larger than the target, and the margin above keeps that harmless.
POWER_ITERATIONS = 50

# Where the constant-mode projection is DECIDED, and the band in which it refuses
# to guess.  ``project_mean`` is not a preference: it is a property of the
# operator, correct on one whose constant mode is a gauge and unconditionally
# wrong on one that pins its own level.  Measured: the scale-free
# defect ``max_i |sum_j A_ij| / max_i sum_j |A_ij|`` and its column twin, in
# float64, on every operator this repository hands to ``build_hierarchy``:
#
#     operator                                   rows      row       column
#     cavity Schur, "laplacian" (the default)     512   1.07e-16   1.07e-16
#     cavity Schur, "laplacian"                110592   1.35e-16   1.35e-16
#     cavity Schur, "simple"                      512   6.70e-17   5.22e-17
#     cavity Schur, "mwi_block"                   512   6.66e-17   6.22e-17
#     Dirichlet pressure Poisson, uniform         512   5.00e-01   5.00e-01
#     Dirichlet pressure Poisson, distorted      4096   3.09e-01   3.09e-01
#
# Fifteen orders separate the two populations and the singular column does not
# grow with the row count, so the edges below are nowhere near either.  What the
# gap does NOT cover is an operator assembled in float32, whose floor sits around
# 1e-7 by dtype alone; that lands between the edges and RAISES rather than being
# classified, which is the honest outcome for a matrix that cannot answer the
# question it is being asked.
SINGULAR_BELOW = 1.0e-12
NONSINGULAR_ABOVE = 1.0e-6


def constant_mode_defect(matrix: torch.Tensor) -> tuple[float, float]:
    """How far the constant vector is from ``null(A)`` and from ``null(A^T)``.

    Returned as ``(row, column)``, each scaled by the largest absolute row (resp.
    column) sum, so the answer does not move when the matrix is.

    BOTH are needed and they are not the same question.  Removing the mean from
    the CORRECTION is valid when ``A 1 = 0``, i.e. the row sums vanish, because
    ``x`` and ``x - mean(x)`` then solve the same system.  Removing it from the
    RIGHT-HAND SIDE is valid when ``A^T 1 = 0``, i.e. the column sums vanish,
    because the range of ``A`` is then orthogonal to the constant and the mean of
    ``b`` is unreachable anyway.  ``__call__`` does both, so a hierarchy that
    projects has to satisfy both.
    """
    coo = matrix.to_sparse_coo().coalesce() if matrix.layout != torch.sparse_coo \
        else matrix.coalesce()
    index, value = coo.indices(), coo.values()
    n = coo.shape[0]

    def defect(axis: int) -> float:
        signed = torch.zeros(n, dtype=value.dtype, device=value.device)
        signed.index_add_(0, index[axis], value)
        absolute = torch.zeros(n, dtype=value.dtype, device=value.device)
        absolute.index_add_(0, index[axis], value.abs())
        scale = float(absolute.max())
        return float(signed.abs().max()) / scale if scale > 0.0 else 0.0

    return defect(0), defect(1)


def resolve_project_mean(matrix: torch.Tensor, project_mean) -> bool:
    """Whether this hierarchy projects the constant mode out, decided or checked.

    ``"auto"`` reads the answer off the matrix.  An explicit ``True`` is CHECKED
    against the matrix rather than obeyed, because the failure it causes does not
    look like itself: projecting a mode a nonsingular operator does not have
    deletes part of every correction, and the outer Krylov solve then stalls at a
    residual that no preconditioner tuning moves.  Measured on the
    512-cell Dirichlet pressure Poisson, GMRES to a 1e-12 relative gate -- with
    the projection on it stagnated at a TRUE relative residual of 1.17e+02 after
    8697 iterations; with it off the same solve converged in 8.

    An explicit ``False`` is honoured without a check: not projecting is merely
    lossy on a singular operator (the iterate drifts along the null direction),
    it is not wrong, and a comparison arm exists that wants exactly that.
    """
    if project_mean is False:
        return False
    if project_mean not in (True, "auto"):
        raise ValueError(
            f"project_mean is True, False or 'auto'; got {project_mean!r}")

    row, column = constant_mode_defect(matrix)
    worst = max(row, column)
    if worst <= SINGULAR_BELOW:
        return True
    if worst >= NONSINGULAR_ABOVE:
        if project_mean is True:
            raise ValueError(
                f"project_mean=True was asked for on an operator whose constant "
                f"mode is not a null mode: relative row-sum defect {row:.3e}, "
                f"column-sum defect {column:.3e}, both far above "
                f"{SINGULAR_BELOW:.0e}. Projecting it out would delete part of "
                f"every correction and stall the outer solve. Pass "
                f"project_mean='auto', or False if that is really wanted.")
        return False
    raise ValueError(
        f"cannot tell whether this operator is singular in the constant mode: "
        f"relative row-sum defect {row:.3e}, column-sum defect {column:.3e}, "
        f"between {SINGULAR_BELOW:.0e} and {NONSINGULAR_ABOVE:.0e}. A matrix "
        f"assembled in {matrix.values().dtype} reads its own rounding here. "
        f"State project_mean=True or False.")


class PressureHierarchy(abc.ABC):
    """The contract every algebraic backend meets, and the whole of the seam.

    A hierarchy is BUILT once from a square matrix, in ``__init__``, and then
    APPLIED many times: calling the instance runs one V-cycle from a zero start
    on a flat right-hand side and returns the correction, on the device and in
    the dtype of the matrix it was built from.

    Two properties the caller relies on and a backend must not break:

    * the action is ONE FIXED LINEAR MAP.  A fixed number of fixed sweeps, no
      per-call scaling, no per-call eigenvalue estimate, no adaptive stopping.
      Plain GMRES's Arnoldi recurrence is invalid otherwise, and the failure is
      silent -- the recurrence residual keeps falling while the true one stops.
    * the correction has ZERO MEAN when ``project_mean`` is set, which is how
      the pressure gauge is fixed without pinning a row anywhere.

    ``setup_seconds`` and ``stage_seconds`` record what the build cost, split
    into hierarchy construction and moving it to the device, because that ratio
    is what decides how many Newton steps should share one hierarchy.
    """

    setup_seconds: float
    stage_seconds: float

    @abc.abstractmethod
    def __call__(self, rhs: torch.Tensor) -> torch.Tensor:
        """One V-cycle from a zero start: the preconditioner action."""

    @abc.abstractmethod
    def matvec(self, x: torch.Tensor) -> torch.Tensor:
        """The operator this hierarchy inverts, applied on the device."""

    @property
    @abc.abstractmethod
    def sizes(self) -> tuple[int, ...]:
        """Rows per level, finest first."""

    def solve(self, b: torch.Tensor, *, rtol: float = 1.0e-8,
              max_cycles: int = 50):
        """V-cycles in defect correction until the relative residual clears
        ``rtol``.

        Returns the iterate and the relative residual after every cycle starting
        with the initial one, so consecutive ratios ARE the measured convergence
        factor -- the only honest way to compare one hierarchy against another.
        """
        rhs = b.reshape(-1)
        x = torch.zeros_like(rhs)
        scale = float(torch.linalg.vector_norm(rhs))
        if scale == 0.0:
            return x, [0.0]
        history = [1.0]
        for _ in range(int(max_cycles)):
            x = x + self(rhs - self.matvec(x))
            history.append(
                float(torch.linalg.vector_norm(rhs - self.matvec(x))) / scale)
            if history[-1] < rtol:
                break
        return x, history


class PyamgHierarchy(PressureHierarchy):
    """Smoothed aggregation built by pyamg on the host, applied in torch.

    The hierarchy construction is scipy and single threaded; the level matrices,
    the smoother diagonals and the coarse pseudo-inverse are then moved onto the
    device once and every cycle after that is torch kernels alone.

    **Damped Jacobi, not Gauss-Seidel.**  pyamg's default smoother is a
    symmetric block Gauss-Seidel sweep, which is sequential and has no GPU form.
    The damping is per level, ``omega = SMOOTHING_SCALE / rho(D^-1 A)`` with
    ``rho`` estimated once at setup, because the spectral radius changes between
    the levels of an aggregation hierarchy and one scalar tuned on the finest
    level either under-smooths or diverges on the coarse ones.  The cost of the
    substitution, measured at 16^3: 0.5845 per cycle against pyamg's
    own 0.4310 with its sequential smoother.
    """

    def __init__(self, matrix: torch.Tensor, *, max_coarse: int = MAX_COARSE,
                 presweeps: int = PRE_SWEEPS, postsweeps: int = POST_SWEEPS,
                 smoothing_scale: float = SMOOTHING_SCALE,
                 project_mean="auto", device=None):
        import numpy as np
        import pyamg

        started = time.perf_counter()
        if matrix.shape[0] != matrix.shape[1]:
            raise ValueError(
                f"an algebraic hierarchy is built on a square matrix; this one "
                f"is {matrix.shape[0]} x {matrix.shape[1]}")
        if int(presweeps) < 0 or int(postsweeps) < 0:
            raise ValueError("sweep counts are non-negative")
        self.device = torch.device(device) if device is not None else matrix.device
        self.dtype = matrix.values().dtype
        self.presweeps, self.postsweeps = int(presweeps), int(postsweeps)
        self.project_mean = resolve_project_mean(matrix, project_mean)
        self.smoothing_scale = float(smoothing_scale)

        fine = _scipy_copy(matrix)
        # A structurally stored zero on the diagonal would divide the smoother by
        # zero; an ABSENT diagonal would do it silently, since scipy returns 0.
        diagonal = fine.diagonal()
        if not bool(np.all(diagonal != 0.0)) or not bool(np.all(np.isfinite(diagonal))):
            raise ValueError(
                f"{int((diagonal == 0.0).sum())} zero and "
                f"{int((~np.isfinite(diagonal)).sum())} non-finite diagonal "
                "entries; a damped-Jacobi smoother has no form on such a matrix")

        state = np.random.get_state()
        try:
            np.random.seed(HIERARCHY_SEED)          # see HIERARCHY_SEED
            # symmetry='symmetric' rather than the 'hermitian' default: the
            # Schur complement of a convecting momentum block is real and NEARLY
            # symmetric but not exactly, and the hermitian path assumes an inner
            # product this matrix does not define.
            self.hierarchy = pyamg.smoothed_aggregation_solver(
                fine, max_coarse=int(max_coarse), symmetry="symmetric")
        finally:
            np.random.set_state(state)
        self.setup_seconds = time.perf_counter() - started

        self.omegas: list[float] = []
        self._levels = []
        for level in self.hierarchy.levels[:-1]:
            inverse = 1.0 / level.A.diagonal()
            self.omegas.append(
                self.smoothing_scale / _spectral_radius(level.A, inverse))
            self._levels.append({
                "A": _to_torch_csr(level.A, self.device, self.dtype),
                "P": _to_torch_csr(level.P, self.device, self.dtype),
                "R": _to_torch_csr(level.R, self.device, self.dtype),
                "inverse_diagonal": torch.as_tensor(
                    inverse, dtype=self.dtype, device=self.device),
            })
        # A dense PSEUDO-inverse, formed once: it is what makes a singular
        # coarsest level well defined, ``max_coarse`` keeps it small enough that
        # a dense matmul beats any alternative, and a dense matmul is also the
        # only coarse solve involving no triangular substitution.  See
        # COARSE_RCOND for why the cutoff is not left at its default.
        coarse = self.hierarchy.levels[-1].A.toarray()
        self.coarse_singular_values = np.linalg.svd(coarse, compute_uv=False)
        self._coarse_inverse = torch.as_tensor(
            np.linalg.pinv(coarse, rcond=COARSE_RCOND), dtype=self.dtype,
            device=self.device)
        self.stage_seconds = time.perf_counter() - started - self.setup_seconds

    # ------------------------------------------------------------------
    @property
    def levels(self) -> int:
        return len(self.hierarchy.levels)

    @property
    def sizes(self) -> tuple[int, ...]:
        return tuple(int(level.A.shape[0]) for level in self.hierarchy.levels)

    def operator_complexity(self) -> float:
        return float(self.hierarchy.operator_complexity())

    def matvec(self, x: torch.Tensor) -> torch.Tensor:
        return self._levels[0]["A"] @ x.reshape(-1)

    # ------------------------------------------------------------------
    @staticmethod
    def _project(x: torch.Tensor) -> torch.Tensor:
        return x - x.mean()

    def _smooth(self, level: int, b: torch.Tensor, x: torch.Tensor, sweeps: int
                ) -> torch.Tensor:
        data = self._levels[level]
        omega = self.omegas[level]
        for _ in range(sweeps):
            x = x + omega * (b - data["A"] @ x) * data["inverse_diagonal"]
        return x

    def _cycle(self, level: int, b: torch.Tensor) -> torch.Tensor:
        """One V-cycle from a zero start on ``level``.

        Starting from zero on every level is what makes this a preconditioner
        rather than an iteration; the caller wraps it in its own defect
        correction.
        """
        if level == len(self._levels):
            return self._coarse_inverse @ b
        data = self._levels[level]
        x = self._smooth(level, b, torch.zeros_like(b), self.presweeps)
        residual = b - data["A"] @ x
        x = x + data["P"] @ self._cycle(level + 1, data["R"] @ residual)
        return self._smooth(level, b, x, self.postsweeps)

    def __call__(self, rhs: torch.Tensor) -> torch.Tensor:
        b = rhs.reshape(-1)
        if self.project_mean:
            b = self._project(b)
        x = self._cycle(0, b)
        return self._project(x) if self.project_mean else x


def amgx_is_available() -> bool:
    """Whether this machine can build an AMGX hierarchy.

    ``pyamgx`` is an optional dependency and its shared library has to be on
    ``LD_LIBRARY_PATH``, so the honest answer needs an import attempt rather
    than a version check.
    """
    try:
        import pyamgx  # noqa: F401
    except Exception:
        return False
    return torch.cuda.is_available()


# AMGX's own shipped classical config, read from
# ``Ref_code/amgx/src/configs/AMG_CLASSICAL_PMIS.json``, is where
# every key below comes from; the deviations are ``max_iters``/``cycle``/
# ``monitor_residual``, which turn a solver into one fixed V-cycle.
#
# CLASSICAL RATHER THAN AGGREGATION, and this is the whole difference between
# AMGX being worth building and not.  Plain aggregation's prolongation is
# PIECEWISE CONSTANT; classical (Ruge-Stuben) interpolation is not.  Measured
# on the 16^3 cavity's Schur block, residual reduction per V-cycle:
#
#     geometric multigrid (the incumbent)          0.2870
#     pyamg smoothed aggregation, in torch         0.5736
#     AMGX aggregation, SIZE_2, DILU 1/2           0.5661
#     AMGX aggregation, SIZE_4, DILU 1/2           0.6740
#     AMGX classical, PMIS, D2, JACOBI_L1 2/2      0.3817
#     AMGX classical, PMIS, D2, MULTICOLOR_GS 2/2  0.1957
#     AMGX classical, PMIS, D2, DILU 2/2           0.1728
#     AMGX classical, HMIS, D2, DILU 2/2           0.1566
#
# i.e. AMGX aggregation is no better than pyamg's smoothed aggregation, while
# AMGX classical is 1.8x better per cycle than the GEOMETRIC cycle it would
# replace.  This repo's own geometric module records the same effect from the
# other side: piecewise-constant transfers cost it 0.494 against 0.171.
AMGX_SELECTOR = "PMIS"
AMGX_INTERPOLATOR = "D2"
# MULTICOLOR_GS rather than MULTICOLOR_DILU, which measures 0.1728 against
# 0.1957.  DILU is an incomplete factorisation and its application is a
# forward/backward substitution, which this campaign rules out on the GPU path
# after measuring sparse triangular solve at 0.50 GB/s against 22.69 GB/s for a
# sparse matvec on this host.  AMGX's is colour-blocked rather than sequential,
# so the rule is not obviously binding, but 13% per cycle is not enough to
# reopen it.
AMGX_SMOOTHER = "MULTICOLOR_GS"
AMGX_PRESWEEPS = 2
AMGX_POSTSWEEPS = 2
# ``DENSE_LU_SOLVER`` on a coarsest level of a few hundred rows, NOT AMGX's own
# ``NOSOLVER`` down to 2 rows.  Measured on the 48^3 Schur block,
# per V-cycle and per apply:
#
#     min_coarse    2, NOSOLVER  (AMGX's shipped config)  0.3268   5.763 ms
#     min_coarse  200, NOSOLVER                           0.6904   4.506 ms
#     min_coarse 2000, NOSOLVER                           0.8782   2.790 ms
#     min_coarse  200, DENSE_LU                           0.2832   4.062 ms
#     min_coarse 2000, DENSE_LU                           0.2832   4.059 ms
#
# Coarsening to 2 rows over up to 50 levels, each with four smoother sweeps, is
# the same launch-bound failure this campaign already found in the geometric
# V-cycle; stopping while the levels are still worth a kernel is 1.4x cheaper
# per cycle AND better per cycle, provided the coarsest level is then actually
# SOLVED rather than smoothed twice.
#
# THE FACTORISATION IS SAFE HERE ONLY BECAUSE OF THE PROJECTION OUTSIDE IT.  On
# this SINGULAR coarsest level a dense LU returns an enormous null-space
# component: measured at 16^3, the same arm's projected rate is 0.1595 while its
# UNPROJECTED iterate's mean reaches -1.1e+06.  The rate is unaffected; the
# iterate is destroyed.  Compare the pyamg backend, where a pseudo-inverse that
# merely KEPT the null direction cost nine digits of linearity.
AMGX_COARSE_SOLVER = "DENSE_LU_SOLVER"
AMGX_COARSEST_SWEEPS = 2
AMGX_MIN_COARSE_ROWS = 200


def amgx_config(smoother: str = AMGX_SMOOTHER, presweeps: int = AMGX_PRESWEEPS,
                postsweeps: int = AMGX_POSTSWEEPS,
                selector: str = AMGX_SELECTOR,
                interpolator: str = AMGX_INTERPOLATOR,
                coarse_solver: str = AMGX_COARSE_SOLVER,
                coarsest_sweeps: int = AMGX_COARSEST_SWEEPS,
                min_coarse_rows: int = AMGX_MIN_COARSE_ROWS) -> dict:
    """One V-cycle as a fixed linear operator.

    ``max_iters: 1`` with ``monitor_residual: 0`` is the load-bearing pair: it
    makes AMGX apply exactly one cycle and forbids it from stopping early on a
    residual test, which would make the preconditioner a VARYING operator and
    invalidate plain GMRES's Arnoldi recurrence.
    """
    return {
        "config_version": 2,
        "determinism_flag": 1,
        "exception_handling": 1,
        "solver": {
            "scope": "main", "solver": "AMG",
            "selector": selector, "interpolator": interpolator,
            "smoother": {"scope": "smoother", "solver": smoother,
                         "relaxation_factor": 1},
            "presweeps": int(presweeps), "postsweeps": int(postsweeps),
            "cycle": "V", "max_iters": 1, "monitor_residual": 0,
            "coarse_solver": coarse_solver,
            "coarsest_sweeps": int(coarsest_sweeps),
            "max_row_sum": 0.9, "interp_max_elements": 4,
            "min_coarse_rows": int(min_coarse_rows), "max_levels": 50,
            "print_grid_stats": 0, "print_solve_stats": 0, "obtain_timings": 0,
        },
    }


class _AmgxSession:
    """One process-wide ``initialize`` and ONE ``Resources``, torn down in order.

    Two things here are not stylistic.

    *``Resources`` is created once per process, not once per hierarchy.*  AMGX's
    device memory pool hangs off it, and creating and destroying one per Newton
    step is what a hierarchy-reuse experiment does.  Measured: that
    aborts the process: after a few cycles of create/destroy AMGX printed a run
    of pinned-block pointers and threw ``Cuda failure: 'invalid argument'`` from
    inside its own allocator.  Sharing it also removes that cost from every
    rebuild.

    *``finalize`` runs last, after every live handle is destroyed.*  Finalising
    with a Config, Matrix, Vector or Solver still alive throws the same
    exception, so hierarchies register here and ``atexit`` closes them all
    first, whatever path the process leaves by -- including an exception inside
    a caller's Newton loop.
    """

    def __init__(self):
        self._started = False
        self._live: list = []
        self._config = None
        self._resources = None

    def start(self):
        import atexit

        import pyamgx

        if not self._started:
            pyamgx.initialize()
            atexit.register(self.stop)
            self._started = True
        return pyamgx

    def resources(self):
        """The shared ``Resources``, built on first use from a bare config.

        The configuration a SOLVER needs is per hierarchy and is created there;
        ``create_simple`` reads only the device and pool settings, so a config
        carrying nothing but a version is the right thing to hand it.
        """
        pyamgx = self.start()
        if self._resources is None:
            self._config = pyamgx.Config().create_from_dict(
                {"config_version": 2, "determinism_flag": 1,
                 "exception_handling": 1})
            self._resources = pyamgx.Resources().create_simple(self._config)
        return self._resources

    def track(self, hierarchy) -> None:
        self._live.append(hierarchy)

    def forget(self, hierarchy) -> None:
        if hierarchy in self._live:
            self._live.remove(hierarchy)

    def stop(self) -> None:
        for hierarchy in list(self._live):
            hierarchy.close()
        self._live.clear()
        for handle in (self._resources, self._config):
            if handle is not None:
                try:
                    handle.destroy()
                except Exception:
                    pass
        self._resources = self._config = None
        if self._started:
            import pyamgx

            pyamgx.finalize()
            self._started = False


AMGX_SESSION = _AmgxSession()


class AmgxHierarchy(PressureHierarchy):
    """A classical AMG hierarchy built and applied by NVIDIA AMGX on the device.

    Setup runs on the GPU, which is the point: pyamg's runs on one host core and
    was measured at 3.3 s per Newton step on the 48^3 Schur complement against a
    3.2 ms apply.  The apply hands AMGX the RAW DEVICE POINTERS of torch tensors
    (``Vector.upload_raw``/``download_raw``), so nothing crosses the bus inside
    the Krylov loop.

    ``refresh`` exists because the Schur complement's SPARSITY is fixed for a
    whole run while its values follow ``diag(F)``: ``Matrix.replace_coefficients``
    swaps the values without touching the structure, so one hierarchy can serve
    several Newton steps.

    **A CONFIGURATION AMGX REJECTS KILLS THE PROCESS, not just the call.**
    Measured: an unknown solver name raises a Python exception that
    can be caught and handled, the interpreter then runs to completion normally
    -- and the process still exits with status 1, after AMGX prints its own
    backtrace from a fatal handler.  So a caller must not probe configurations
    at runtime and expect to recover; ``amgx_config`` is built from constants
    that were measured, not searched.

    The API sequence here (Config, Resources, Matrix, Vector, Solver, mode
    ``dDDI``, ``upload_CSR``) follows ``Ref_code/pytorch-sparse-linalg``'s
    ``module_b/torch_amgx.py`` (Apache-2.0, Copyright 2025 Litianyu141), rewritten
    for this repo.  Its control flow is deliberately NOT reused: that module
    creates and destroys every handle around a single solve, which is the wrong
    split for a preconditioner applied hundreds of times per setup.
    """

    def __init__(self, matrix: torch.Tensor, *, project_mean="auto",
                 config: dict | None = None, device=None):
        # FIRST, so that ``__del__`` on a half-built instance has something to
        # iterate: the validation below raises before any handle exists.
        self._handles: list = []
        pyamgx = AMGX_SESSION.start()
        started = time.perf_counter()
        if matrix.shape[0] != matrix.shape[1]:
            raise ValueError(
                f"an algebraic hierarchy is built on a square matrix; this one "
                f"is {matrix.shape[0]} x {matrix.shape[1]}")
        if matrix.device.type != "cuda":
            raise ValueError(
                f"AMGX runs on the device and this matrix is on "
                f"{matrix.device}; build it on CUDA or ask for the pyamg backend")
        self.device = torch.device(device) if device is not None else matrix.device
        self.dtype = matrix.values().dtype
        self.project_mean = resolve_project_mean(matrix, project_mean)
        self.rows = int(matrix.shape[0])
        self._matrix = matrix

        host = _scipy_copy(matrix)
        resources = AMGX_SESSION.resources()
        try:
            self.config = self._track(
                pyamgx.Config().create_from_dict(config or amgx_config()))
            self.A = self._track(pyamgx.Matrix().create(resources, mode="dDDI"))
            self.rhs = self._track(pyamgx.Vector().create(resources, mode="dDDI"))
            self.sol = self._track(pyamgx.Vector().create(resources, mode="dDDI"))
            self.solver = self._track(
                pyamgx.Solver().create(resources, self.config))
            self.A.upload_CSR(host)
            self.stage_seconds = time.perf_counter() - started
            self.setup_seconds = self._setup()
        except BaseException:
            self.close()
            raise
        AMGX_SESSION.track(self)

    # ------------------------------------------------------------------
    def _track(self, handle):
        self._handles.append(handle)
        return handle

    def _setup(self) -> float:
        started = time.perf_counter()
        self.solver.setup(self.A)
        torch.cuda.synchronize(self.device)
        return time.perf_counter() - started

    def refresh(self, matrix: torch.Tensor, *, resetup: bool = True) -> float:
        """Install new VALUES for the same sparsity, and optionally re-setup.

        ``resetup=False`` keeps the coarse hierarchy that was built from the
        previous values and swaps only the fine-level operator, which is what
        "reuse one hierarchy across Newton steps" means.  Returns the seconds the
        call cost, so a caller can price reuse against rebuilding.
        """
        started = time.perf_counter()
        host = _scipy_copy(matrix)
        self.A.replace_coefficients(host.data)
        self._matrix = matrix
        if resetup:
            self.solver.setup(self.A)
        torch.cuda.synchronize(self.device)
        return time.perf_counter() - started

    @property
    def sizes(self) -> tuple[int, ...]:
        return (self.rows,)

    def matvec(self, x: torch.Tensor) -> torch.Tensor:
        return self._matrix @ x.reshape(-1)

    @staticmethod
    def _project(x: torch.Tensor) -> torch.Tensor:
        return x - x.mean()

    def __call__(self, rhs: torch.Tensor) -> torch.Tensor:
        b = rhs.reshape(-1)
        if self.project_mean:
            b = self._project(b)
        b = b.contiguous()
        out = torch.zeros_like(b)
        self.rhs.upload_raw(b.data_ptr(), b.numel())
        self.sol.upload_raw(out.data_ptr(), out.numel())
        self.solver.solve(self.rhs, self.sol, zero_initial_guess=True)
        self.sol.download_raw(out.data_ptr())
        return self._project(out) if self.project_mean else out

    def close(self) -> None:
        """Destroy every handle in the reverse of creation order.

        Idempotent, and safe on a half-built instance: the constructor calls it
        from its own exception path, where some of these do not exist yet.
        """
        for handle in reversed(self._handles):
            try:
                handle.destroy()
            except Exception:
                pass
        self._handles = []
        AMGX_SESSION.forget(self)

    def __del__(self):
        self.close()


def build_hierarchy(matrix: torch.Tensor, *, backend: str = "auto", **options
                    ) -> PressureHierarchy:
    """The hierarchy of ``matrix``, from whichever backend provides one.

    ``backend="auto"`` is PYAMG, on a device or not, and that is a measurement
    rather than a default-by-availability.  AMGX has the better cycle and by far
    the cheaper setup, and it still loses: what a Newton run spends is APPLIES,
    of which there are two thousand against nine setups.  Measured on
    the 48^3 cavity, one cold start each, same GPU, quiet host:

        backend   setup      one apply    per V-cycle   rate     GMRES   wall
        pyamg     445.5 ms    3.396 ms      0.527 ms    0.6811    2076   11.1 s
        amgx       70.8 ms   46.292 ms      5.755 ms    0.3268    2029  100.2 s

    Tuning AMGX's coarsest level (see ``AMGX_COARSE_SOLVER``) cuts its apply to
    about 32 ms and its rate to 0.2832, which is still an order of magnitude
    more expensive per apply than pyamg for a 2% saving in iterations.  The gap
    is largely a FIXED per-call cost: AMGX's V-cycle went 2.006 ms at 4096 rows
    to 4.06 ms at 110592, i.e. 27x the work for 2x the time, so a much larger
    problem would change this verdict and 48^3 does not.

    Naming a backend demands it and raises when it is unavailable, which is what
    a comparison arm needs.
    """
    resolved = str(backend).lower()
    if resolved not in AMG_BACKENDS:
        raise ValueError(f"unknown AMG backend {backend!r}; valid names: "
                         + ", ".join(AMG_BACKENDS))
    if resolved == "auto":
        resolved = "pyamg"
    if resolved == "amgx":
        return AmgxHierarchy(matrix, **options)
    return PyamgHierarchy(matrix, **options)


def _scipy_copy(matrix: torch.Tensor):
    """A host scipy CSR that OWNS its data.

    ``tensor.numpy()`` shares storage, ``.to("cpu")`` on a CPU tensor is a no-op
    and ``.to(float64)`` on a float64 tensor is another, so the naive spelling
    hands the backend a view of the CALLER'S matrix -- and the setup SORTS ITS
    INPUT'S COLUMN INDICES IN PLACE, which permutes ``data``.

    That bites here and not in a textbook because the Schur complement comes out
    of a torch sparse-sparse product and is NOT canonical, whereas a hand-built
    Laplacian is: as measured, the same aliasing is a no-op on a sorted
    matrix and destroys an unsorted one.  Without the copy, building a hierarchy
    on the 16^3 Schur complement left that Schur complement permuted, the
    geometric cycle sharing the tensor then diverged at 3.5x per cycle, the
    aggregation collapsed to a 1 x 1 coarsest level on half its attempts, and
    two hierarchies from the same tensor disagreed on rho(D^-1 A) by 3.6x.
    """
    import numpy as np
    import scipy.sparse as sp

    host = matrix.to("cpu")
    return sp.csr_matrix(
        (np.array(host.values().to(torch.float64).numpy(), copy=True),
         np.array(host.col_indices().numpy(), copy=True),
         np.array(host.crow_indices().numpy(), copy=True)),
        shape=tuple(int(n) for n in matrix.shape))


def _spectral_radius(matrix, inverse_diagonal, iterations: int = POWER_ITERATIONS
                     ) -> float:
    """``rho(D^-1 A)`` by power iteration, on the host at setup time.

    Deterministic rather than random: the damping factor this feeds is part of a
    preconditioner that has to be the SAME linear map on every application and
    on every device, and a seeded random start would still make it depend on the
    generator's state.
    """
    import numpy as np

    n = matrix.shape[0]
    v = np.cos(np.arange(n, dtype=np.float64))
    v /= np.linalg.norm(v)
    value = 1.0
    for _ in range(int(iterations)):
        w = inverse_diagonal * (matrix @ v)
        value = float(np.linalg.norm(w))
        if not value > 0.0:
            return 1.0
        v = w / value
    return value


def _to_torch_csr(matrix, device, dtype) -> torch.Tensor:
    import scipy.sparse as sp

    csr = sp.csr_matrix(matrix)
    return torch.sparse_csr_tensor(
        torch.as_tensor(csr.indptr.astype("int64"), device=device),
        torch.as_tensor(csr.indices.astype("int64"), device=device),
        torch.as_tensor(csr.data, dtype=dtype, device=device),
        size=csr.shape)
