# Copyright 2025 Litianyu141
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Restarted GMRES for a matrix-free operator on one flat float64 vector.

Replaces the vendored copy of ``pytorch-sparse-linalg``'s ``module_a`` that stood
here (988 lines, plus ``torch_tree_util.py``'s 403), which no file in ``src/`` or
``experiments/`` imported.  The Apache notice above is kept because this file
takes that one's place in the tree, but no line of it survives: that module was a
port of JAX's ``gmres``/``cg``/``bicgstab`` and carried JAX-style pytree support
so that the unknown could be an arbitrary nested structure.

Three things went, and each removal is a decision rather than an omission.

*Pytrees.*  Our unknown is one flat vector of face-normal velocities followed by
cell pressures, so every ``tree_map`` was a one-element map over a single tensor.
Dropping the abstraction removes the larger half of both files and is the
difference between an iteration you can hold in your head and one you cannot.
Nothing else in this repository imports the pytree module.

*``cg`` and ``bicgstab``.*  Unused, and the saddle-point Jacobian
``[[F, G], [D, 0]]`` is indefinite and non-symmetric, which rules out ``cg``
outright.  Git history is the archive.

*Differentiability.*  This iteration is NOT differentiable, by design.  Operator
and preconditioner images are detached and the loop reads scalars back to the
host for the Givens recurrence.  The adjoint of a linear solve does not need a
tape over the iteration -- it is another solve with the transposed operator, via
the implicit function theorem -- and that is where differentiability is
implemented.  A tape here would hold every Krylov vector alive for a backward
pass nothing performs.

The preconditioner is applied on the RIGHT: the iteration runs on ``A M`` and the
answer is ``M y``.  Two consequences worth stating, because both are relied upon
elsewhere in the file.  The Arnoldi recurrence residual is then the residual of
the ORIGINAL system, not of a preconditioned surrogate, so the stopping test
means what it says whatever ``M`` does.  And ``M`` may be any nonsingular linear
map -- no symmetry, no transpose -- which is what a segregated pressure-velocity
sweep needs, since such a sweep is not symmetric.  ``M`` is assumed LINEAR: it is
applied once to the accumulated coordinate vector per restart cycle rather than
once per Krylov vector, which is ``restart``-fold fewer applications.

Note that ``scipy.sparse.linalg.gmres`` preconditions on the LEFT.  The two
iterations therefore differ whenever a preconditioner is given; the equivalence
tested in ``tests/test_gmres.py`` is against scipy run unpreconditioned on the
composed operator ``v -> A(M(v))``, which is the definition of the right-
preconditioned iteration.

``deflate=k`` switches the restart from Morgan's GMRES(m) to his GMRES-DR(m, k)
(SIAM J. Sci. Comput. 24(1):20-37, 2002, DOI ``10.1137/S1064827599364659``): the
``k`` harmonic Ritz vectors of smallest modulus are carried across the restart,
so cycle ``i + 1`` starts from ``Span{y_1..y_k, r_i, A r_i, ...}`` instead of
from the bare residual.  It defaults to ``0``, which is the iteration described
above, unchanged instruction for instruction -- see ``tests/test_gmres_dr.py``,
which holds the two bit-identical over the whole scipy-equivalence battery.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import torch

# Reorthogonalise when the projection left less than 1/sqrt(2) of the vector's
# length, the criterion of Daniel, Gragg, Kaufman & Stewart, Math. Comp.
# 30:772-795 (1976).  Below this the subtraction has cancelled most of the
# vector and the classical projection's coefficients are no longer accurate to
# working precision; above it, one pass is enough.
_REORTHOGONALISE_BELOW = 0.7071067811865476

# Process-wide defaults of the opt-in Krylov variants: read by every call that does not
# pass the keyword; an entry flag sets them (``solve_with_coupled_newton --krylov-fused-orth``).  False = off.
KRYLOV = {"fused_orth": False, "device_loop": False, "basis_fp32": False, "best_cycle_return": False,
          "best_cycle_gain": 0.5}
# ``best_cycle_return`` (``--krylov-best-cycle-return``): the solve returns the cycle end with the smallest true
# residual when its last one is more than BEST_FACTOR times above it, and a cycle that breaks down without meeting the
# tolerance is followed by a fresh cycle instead of ending the solve
BEST_FACTOR = 10.0
# ``best_cycle_gain = g`` (``--krylov-best-cycle-gain``, default one half): with ``best_cycle_return`` the best cycle end
# is returned only when it lies below ``g`` times the solve's start residual; a best end that made no such progress (at a
# noise floor the primary's probe can be the best end without reducing anything) is not returned, the last end is, as
# without the switch; ``g = 0`` returns the best end whatever its level
# The process-wide variants a chunked basis turned off, each warned about once (``gmres(basis_chunk=k)``): a flag is
# never dropped without a word.
DROPPED: dict = {}

# What a ``cycle_policy`` returns to end a solve at a restart boundary (opt-in: no shipped
# policy returns it unless asked, e.g. ``block_amg.StagnationRescue(maxit=...)``).
STOP = "stop"

# A deflated restart can only work on the part of the residual its recycled
# basis spans; below this fraction more than 44% of the residual is out of
# reach for the whole cycle, and a plain restart -- whose capture is 1 by
# construction -- reduces more of it for the extra k matvecs it costs.
_CAPTURE_FLOOR = 0.9


@dataclass(frozen=True)
class GmresResult:
    """What the caller reads back: the answer, and how the iteration got there."""

    x: torch.Tensor
    residuals: tuple[float, ...]
    """Recurrence residual relative to ``|b|``, one entry per inner iteration.

    This is the quantity scipy reports through a ``pr_norm`` callback and the
    quantity the stopping test compares, so a caller plotting it sees exactly
    what the solver decided on.
    """
    converged: bool
    reorthogonalisations: int
    cycle_lengths: tuple[int, ...] = ()
    """How many of those entries each restart cycle contributed.

    Carried because the question a restarted solve has to answer is whether the
    gain PER CYCLE accelerates, and the cycle boundaries are not recoverable
    from the history alone once a cycle ends early or a deflated cycle spends
    ``restart - k`` matvecs instead of ``restart``.
    """
    cycle_residuals: tuple[float, ...] = ()
    """The TRUE ``|b - A x| / |b|`` at the end of each cycle.

    Already computed -- every cycle ends on a fresh residual, which is what the
    stopping test reads -- and carried because the recurrence is not a safe
    substitute for it under ``deflate``: measured optimistic by 1.04x to 14.7x
    on the non-normal fixture in ``tests/test_gmres_dr.py``, since a deflated
    cycle inherits its right-hand side across the restart instead of rebuilding
    it.  A per-cycle gain read off ``residuals`` therefore carries that drift as
    a spurious negative gain at the next cycle boundary; read off this, it does
    not.
    """
    breakdowns: int = 0
    """Cycles that ended on an Arnoldi breakdown (a new vector already in the span), solved or not."""

    @property
    def iterations(self) -> int:
        return len(self.residuals)


def _givens(f: float, g: float) -> tuple[float, float, float]:
    """LAPACK ``dlartg``: ``c, s, r`` with ``[[c, s], [-s, c]] @ [f, g] == [r, 0]``.

    Written out rather than called through LAPACK because the whole Hessenberg
    system lives on the host as scalars; ``math.hypot`` supplies the scaling that
    keeps ``f**2 + g**2`` from overflowing.
    """
    if g == 0.0:
        return 1.0, 0.0, f
    if f == 0.0:
        return 0.0, math.copysign(1.0, g), abs(g)
    length = math.hypot(f, g)
    r = math.copysign(length, f)
    return abs(f) / length, g / r, r


def _harmonic_directions(hbar: torch.Tensor, wanted: int) -> torch.Tensor:
    """The ``wanted`` harmonic Ritz vectors of smallest modulus, as real columns.

    ``hbar`` is the ``(m+1) x m`` coefficient matrix of ``A V_m = V_{m+1} Hbar``,
    with ``V_{m+1}`` orthonormal.  The harmonic Ritz pairs are the solutions of
    ``Hbar^T Hbar g = theta H^T g`` with ``H = Hbar[:m]``, which for a matrix
    whose last row is ``h e_m^T`` -- true of a plain Arnoldi run AND of the
    continuation after a deflated restart, since the recycled block never
    reaches the bottom row -- is the standard eigenproblem on
    ``H + h^2 H^-T e_m e_m^T`` (Morgan 2002, eq. 2.3).

    Complex pairs are returned as their real and imaginary parts, which span the
    same real subspace; a pair that would not fit inside ``wanted`` is dropped
    whole rather than halved, because ``A Re(y)`` reaches ``Im(y)`` and half a
    pair is not closed under ``A``, which is the property the restart needs.
    The count therefore comes back at or below ``wanted``.  If ``H`` is singular
    the harmonic problem is not posed, and the fallback is the smallest right
    singular vectors of ``Hbar`` -- the directions ``y = V_m g`` of smallest
    ``|A y| / |y|`` that the space contains at all, which is the quietest thing
    any extraction from it can name.
    """
    m = hbar.shape[1]
    square = hbar[:m, :m]
    last = torch.zeros(m, dtype=torch.float64)
    last[m - 1] = 1.0
    columns: list[torch.Tensor] = []
    try:
        weight = float(hbar[m, m - 1]) ** 2
        shifted = square + weight * torch.outer(
            torch.linalg.solve(square.transpose(0, 1), last), last
        )
        values, vectors = torch.linalg.eig(shifted)
        if not torch.isfinite(vectors).all():
            raise torch.linalg.LinAlgError("non-finite harmonic Ritz vectors")
        order = torch.argsort(values.abs()).tolist()
        spent: set[int] = set()
        for index in order:
            if len(columns) >= wanted:
                break
            if index in spent:
                continue
            value, vector = values[index], vectors[:, index]
            real = abs(float(value.imag)) <= 1.0e-14 * (1.0 + float(value.abs()))
            if len(columns) + (1 if real else 2) > wanted:
                break
            spent.add(index)
            if real:
                columns.append(vector.real.to(torch.float64))
                continue
            # the conjugate partner carries no direction the pair does not
            columns.append(vector.real.to(torch.float64))
            columns.append(vector.imag.to(torch.float64))
            partner = int(torch.argmin((values - value.conj()).abs()).item())
            spent.add(partner)
    except (torch.linalg.LinAlgError, RuntimeError):
        columns = []
    if not columns:
        right = torch.linalg.svd(hbar, full_matrices=False)[2]
        columns = [right[-1 - i] for i in range(min(wanted, m))]
    return torch.stack(columns, dim=1)


def _harmonic_values(hbar: torch.Tensor) -> torch.Tensor:
    """The harmonic Ritz VALUES of an Arnoldi run, i.e. the roots of its GMRES
    residual polynomial.

    Same eigenproblem ``_harmonic_directions`` poses -- ``H + h^2 H^-T e_m e_m^T``
    (Morgan 2002, eq. 2.3) -- but that function keeps only the eigenVECTORS and
    discards the values, which is why this exists rather than a return-tuple
    change there: the deflation path is on the production hot loop and does not
    want a second output threaded through it.
    """
    m = hbar.shape[1]
    square = hbar[:m, :m]
    last = torch.zeros(m, dtype=torch.float64)
    last[m - 1] = 1.0
    weight = float(hbar[m, m - 1]) ** 2
    shifted = square + weight * torch.outer(
        torch.linalg.solve(square.transpose(0, 1), last), last
    )
    return torch.linalg.eigvals(shifted)


def polynomial_preconditioner(
    operator: Callable[[torch.Tensor], torch.Tensor],
    size: int,
    degree: int,
    *,
    device: torch.device,
    dtype: torch.dtype = torch.float64,
    seed: int = 0,
) -> Callable[[torch.Tensor], torch.Tensor]:
    """``p(A)`` with ``A p(A) = I - phi_d(A)``, ``phi_d`` the GMRES(d) residual
    polynomial, applied in ``degree`` matvecs as a fixed linear map.

    It attacks the ONE term that dominates this solver.  Orthogonalisation is
    92-98% of an inner solve here and already runs at 98-99% of the card's
    bandwidth, so it can only be avoided, not tuned; a degree-``d`` Arnoldi step
    does ``d`` matvecs but orthogonalises ONCE, which cuts the Gram-Schmidt
    product ``restart x iterations`` without changing the matvec count.
    Measured on the periodic-hills Jacobian (921,600 unknowns, 71.2M nnz,
    Ruiz-equilibrated), at an equal 6,4xx-matvec budget and three
    interleaved repetitions: plain ``GMRES(1600)x4`` takes 56.48 s to reach true
    relative 1.086e-01, this takes 5.61 s to reach 4.003e-02 -- 10.07x in wall
    clock, and 2.71x better in residual.

    Three choices below are measurements, not preferences.

    *The generating vector is RANDOM, never ``b``.*  Built from ``b`` the
    polynomial stagnates: at ``degree`` 24 it is PINNED at 9.7026e-01 /
    9.7001e-01 / 9.6986e-01 across 9,671 / 19,271 / 38,471 matvecs, against
    7.39e-02 from a random vector at the same budget -- 13.1x for a one-line
    difference.  The mechanism is visible in the identity above: when degree-``d``
    GMRES on ``b`` buys 3%, ``I - phi_d(A)`` is near-zero in exactly the
    directions ``b`` needs.  Loe & Morgan (2022, NLAA, p. 20) reach the same
    rule.

    *Conjugate roots are composed into REAL quadratic factors.*  Applying the
    roots in complex arithmetic costs two real matvecs each, so a complex form
    silently spends twice its stated budget -- which is how a first measurement
    of this here reported a 2x-inflated speedup.  For a pair ``(t, conj(t))`` the
    two nested steps compose to ``y <- y + a r - b (A r)`` with ``r = v - A y``,
    ``a = 2 Re(1/t)`` and ``b = 1/|t|^2``, entirely real, two matvecs per pair.

    *Degree buys nothing past ~8 and is capped for stability.*  True residual at
    a fixed 6,400-matvec budget is flat from ``degree`` 8 (7.331e-02) to 200
    (7.533e-02), so the added-roots machinery Loe & Morgan need for high degree
    is unnecessary here -- and its absence is itself evidence that their
    "double the degree, halve the matvecs" mechanism is NOT present on this
    spectrum: the measured matvec reduction is 1.00x and the entire gain is the
    orthogonalisation term.  The naive product form loses ordering-invariance
    between ``degree`` 100 (8.3e-05) and 200 (3.2e+08), so 100 is refused.

    ``operator`` must be the SAME map the iteration will run on -- with a block
    preconditioner ``M`` in play that is ``v -> A(M(v))``, and the caller then
    passes ``lambda v: M(p(v))`` to :func:`gmres`, not ``p`` alone.
    """
    if degree < 1:
        raise ValueError(f"polynomial degree must be >= 1, got {degree}")
    if degree > 100:
        raise ValueError(
            f"polynomial degree {degree} exceeds the 100 this form is stable to; "
            "the product form loses ordering-invariance above it and degree is "
            "measured flat past 8 anyway"
        )
    generator = torch.Generator(device="cpu").manual_seed(seed)
    start = torch.randn(size, generator=generator, dtype=torch.float64).to(
        device=device, dtype=dtype
    )
    basis = torch.empty((degree + 1, size), device=device, dtype=dtype)
    hbar = torch.zeros((degree + 1, degree), dtype=torch.float64)
    basis[0] = start / torch.linalg.vector_norm(start)
    for column in range(degree):
        w = operator(basis[column]).detach()
        length = float(torch.linalg.vector_norm(w))
        stored = basis[: column + 1]
        for _ in range(2):  # two-pass classical Gram-Schmidt, as the loop uses
            coefficients = stored @ w
            w = w - coefficients @ stored
            hbar[: column + 1, column] += coefficients.to("cpu", torch.float64)
        height = float(torch.linalg.vector_norm(w))
        hbar[column + 1, column] = height
        if height <= float(torch.finfo(dtype).eps) * length:
            # the Krylov space closed early: the polynomial is exact on it, and
            # truncating here is the whole of it rather than a truncation
            hbar = hbar[: column + 2, : column + 1]
            break
        basis[column + 1] = w / height
    del basis
    roots = _harmonic_values(hbar)

    pairs: list[tuple[float, float]] = []
    singles: list[float] = []
    spent: set[int] = set()
    for index in range(roots.numel()):
        if index in spent:
            continue
        value = roots[index]
        spent.add(index)
        inverse = 1.0 / value
        if abs(float(value.imag)) <= 1.0e-14 * (1.0 + float(value.abs())):
            singles.append(float(inverse.real))
            continue
        partner = int(torch.argmin((roots - value.conj()).abs()).item())
        spent.add(partner)
        pairs.append((2.0 * float(inverse.real), float(abs(inverse) ** 2)))

    def apply(vector: torch.Tensor) -> torch.Tensor:
        image = torch.zeros_like(vector)
        for weight, square in pairs:
            residual = vector - operator(image)
            image = image + weight * residual - square * operator(residual)
        for inverse in singles:
            image = image + inverse * (vector - operator(image))
        return image

    apply.degree = 2 * len(pairs) + len(singles)  # type: ignore[attr-defined]
    apply.roots = roots  # type: ignore[attr-defined]
    # the generating vector, kept so the identity ``A p(A) = I - phi_d(A)`` can be
    # checked against GMRES(d) on that same vector -- an ALGEBRAIC gate, not one
    # read off this implementation, which is the only kind that catches a wrong
    # root set (a wrong set still gives a linear, exactly-d-matvec operator)
    apply.probe = start / torch.linalg.vector_norm(start)  # type: ignore[attr-defined]
    return apply


def _recycle(
    hessenberg: torch.Tensor,
    carried_rhs: torch.Tensor,
    basis: torch.Tensor,
    scratch: torch.Tensor,
    coordinates: torch.Tensor,
    residual: torch.Tensor,
    residual_norm: float,
    size: int,
    wanted: int,
) -> tuple[int, float]:
    """Replace the cycle's Krylov space with the subspace the next one inherits.

    Rewrites ``basis[:k+1]``, ``hessenberg[:k+1, :k]`` and ``carried_rhs[:k+1]``
    so that ``A V_k = V_{k+1} Hbar_k`` still holds for the recycled block, and
    returns ``k`` beside the norm of the part of the residual that block does not
    span.  Returns ``(0, 0.0)`` -- a plain restart -- whenever the subspace cannot
    be formed or fails one of the two checks below, which is the safe direction:
    a plain restart is the shipped iteration.

    The augmentation is Morgan's: ``Span{y_1..y_k, r}``, with the residual
    included because ``A y_i - theta_i y_i`` is a multiple of ``r`` for a
    harmonic Ritz pair, which is exactly what makes the recycled block close
    under ``A`` without another matvec.

    **Both checks below were put here by a measurement, not by caution.**
    Without them, on the non-normal clustered fixture in ``tests/
    test_gmres_dr.py`` (eigenvector condition ~350 inside the cluster) the
    recurrence residual read 1.35e-02 while the true residual was 2.72e+00, a
    factor of 200 apart, and the arm ended WORSE than its own starting point.
    The cause is that ``H`` goes numerically singular once the cycle stagnates,
    the harmonic Ritz solve returns directions that are not harmonic Ritz
    vectors, and the projected block then describes an operator the basis does
    not have.  A rank test on the augmented block does not catch it.

    *Closure.*  ``A V_k = V_{k+1} Hbar_k`` holds exactly iff ``Hbar_m Q_k``
    lies in the range of ``Qbar_{k+1}``.  That is the algorithm's own defining
    identity, it is checkable in the ``(m+1) x k`` small space for free, and it
    is what actually fails.

    *Capture.*  Re-projecting the TRUE residual, rather than carrying the
    recurrence one forward, is what keeps the two from drifting apart across
    restarts -- a plain restart gets that for free by rebuilding from
    ``b - A x``, and GMRES-DR does not.  Mathematically the residual lies in the
    recycled span, so the part that does not is pure round-off; it is returned
    beside ``k`` rather than discarded, because
    ``|r - A V d|^2 = |g - Hbar d|^2 + |r_out|^2`` exactly, and dropping the
    second term is what lets a deflated cycle report convergence it has not
    reached.  Measured on the fixture above: the captured fraction falls to
    0.9977 by the fifth cycle, i.e. 6.7% of the residual is unreachable inside
    that cycle, which caps its gain at 1.17 decades and is worth knowing rather
    than worth hiding.  Below ``_CAPTURE_FLOOR`` a plain restart, whose capture
    is 1 by construction, is taken instead.
    """
    hbar = hessenberg[: size + 1, :size]
    rhs = carried_rhs[: size + 1] - hbar @ coordinates
    room = min(wanted, size - 1)
    if room < 1:
        return 0, 0.0
    directions = _harmonic_directions(hbar, room)
    count = min(directions.shape[1], room)
    augmented = torch.zeros((size + 1, count + 1), dtype=torch.float64)
    augmented[:size, :count] = directions[:, :count]
    augmented[:, count] = rhs
    frame, upper = torch.linalg.qr(augmented)
    diagonal = upper.diagonal().abs()
    if not torch.isfinite(frame).all() or float(diagonal.min()) <= 1.0e-12 * float(
        diagonal.max()
    ):
        # the residual already lies in the span of the Ritz vectors, or two of
        # them coincide; either way the augmented block is rank deficient
        return 0, 0.0
    transpose = frame.transpose(0, 1)
    carried = hbar @ frame[:size, :count]
    block = transpose @ carried
    closure = float(torch.linalg.matrix_norm(carried - frame @ block))
    if closure > 1.0e-8 * float(torch.linalg.matrix_norm(carried)):
        return 0, 0.0
    frame_rows = transpose.to(device=basis.device, dtype=basis.dtype)
    if isinstance(basis, _ChunkedBasis):
        basis.rows_into(frame_rows, size + 1, scratch[: count + 1])
    else:
        torch.matmul(frame_rows, basis[: size + 1], out=scratch[: count + 1])
    moved = (scratch[: count + 1] @ residual).to("cpu", torch.float64)
    inside = float(torch.linalg.vector_norm(moved))
    if inside < _CAPTURE_FLOOR * residual_norm:
        return 0, 0.0
    if isinstance(basis, _ChunkedBasis):
        for j in range(count + 1):
            basis[j] = scratch[j]
    else:
        basis[: count + 1] = scratch[: count + 1]
    hessenberg.zero_()
    hessenberg[: count + 1, :count] = block
    carried_rhs.zero_()
    carried_rhs[: count + 1] = moved
    return count, math.sqrt(max(residual_norm * residual_norm - inside * inside, 0.0))


class _ChunkedBasis:
    """The Arnoldi basis held as blocks of ``chunk`` vectors, each allocated when the iteration first writes into
    it (opt-in, ``gmres(basis_chunk=k)``; see
    docs/research/2026-10-07-gpu-optimisation-roadmap.md).

    The default basis is ONE ``(restart + 1) x n`` buffer, allocated before the first Arnoldi step whatever the
    solve then needs: 401 vectors at the shipped ``restart`` 400, 10.2 GiB on DHC88 (3.4M unknowns) and 52 GiB on
    DHC152 (17.6M), while the production solves there end after 8-114 iterations (pilot 2 B1, DHC88 Ra 1e6 / 1e7).
    Here the memory follows the iterations actually run, ``ceil((k + 1) / chunk) * chunk`` vectors.

    The arithmetic is the same Gram-Schmidt, block by block: ``project`` is ``V[:k] w`` as the concatenation of
    one GEMV per block, ``combine`` is ``c V[:k]`` as a sum of one GEMV per block.  The second sums in a different
    order than the single GEMV of the default, so iterates agree to rounding only (bit for bit while
    ``k <= chunk``: one block, the default's very call).  Indexing with an integer reads / writes one vector, as
    on the tensor it replaces."""

    def __init__(self, n: int, chunk: int, device, dtype):
        if int(chunk) < 1:
            raise ValueError(f"basis_chunk must be >= 1, not {chunk}")
        self.n, self.chunk, self.device, self.dtype = int(n), int(chunk), device, dtype
        self.blocks: list[torch.Tensor] = []

    @property
    def allocated(self) -> int:
        """Vectors allocated so far (the memory this basis holds is that times ``n`` elements)."""
        return len(self.blocks) * self.chunk

    def __getitem__(self, j: int) -> torch.Tensor:
        return self.blocks[j // self.chunk][j % self.chunk]

    def __setitem__(self, j: int, vector: torch.Tensor) -> None:
        block = j // self.chunk
        while len(self.blocks) <= block:
            self.blocks.append(torch.empty((self.chunk, self.n), device=self.device, dtype=self.dtype))
        self.blocks[block][j % self.chunk] = vector

    def project(self, w: torch.Tensor, k: int) -> torch.Tensor:
        """``V[:k] @ w``: the ``k`` inner products with the first ``k`` basis vectors."""
        parts = []
        for index, block in enumerate(self.blocks):
            m = min(self.chunk, k - index * self.chunk)
            if m <= 0:
                break
            parts.append(block[:m] @ w)
        return parts[0] if len(parts) == 1 else torch.cat(parts)

    def rows_into(self, coefficients: torch.Tensor, k: int, out: torch.Tensor) -> torch.Tensor:
        """``coefficients @ V[:k]`` for a matrix of coefficients (one row per output vector), written into ``out``:
        the deflated restart's new leading basis, summed block by block."""
        out.zero_()
        for index, block in enumerate(self.blocks):
            low = index * self.chunk
            m = min(self.chunk, k - low)
            if m <= 0:
                break
            out.addmm_(coefficients[:, low:low + m], block[:m])
        return out

    def combine(self, coefficients: torch.Tensor, k: int) -> torch.Tensor:
        """``coefficients @ V[:k]``: the combination of the first ``k`` basis vectors."""
        out = None
        for index, block in enumerate(self.blocks):
            low = index * self.chunk
            m = min(self.chunk, k - low)
            if m <= 0:
                break
            part = coefficients[low:low + m] @ block[:m]
            out = part if out is None else out.add_(part)
        return out


def _dropped(name: str, chunk: int) -> None:
    """Count, and warn about once per process, a process-wide Krylov variant (``KRYLOV[name]``, set by an entry flag)
    that this call cannot use: the fused kernels and the device loop work on the single basis buffer, and the call
    asked for a basis allocated in blocks of ``chunk`` vectors."""
    import warnings

    first = name not in DROPPED
    DROPPED[name] = DROPPED.get(name, 0) + 1
    if first:
        flag = {"fused_orth": "--krylov-fused-orth", "device_loop": "--krylov-device-loop"}.get(name, name)
        warnings.warn(f"{flag} does not act on a chunked Arnoldi basis (--krylov-basis-chunk {int(chunk)}): this and "
                      f"every such GMRES call runs the plain orthogonalisation on the host loop", RuntimeWarning,
                      stacklevel=3)


def gmres(
    A: Callable[[torch.Tensor], torch.Tensor],
    b: torch.Tensor,
    *,
    rtol: float = 1.0e-5,
    atol: float = 0.0,
    x0: torch.Tensor | None = None,
    M: Callable[[torch.Tensor], torch.Tensor] | None = None,
    restart: int = 20,
    max_cycles: int | None = None,
    deflate: int = 0,
    restart_probe=None,
    cycle_policy=None,
    basis_chunk: int = 0,
    max_iterations: int | None = None,
    fused_orth: bool | None = None,
    device_loop: bool | None = None,
    basis_fp32: bool | None = None,
    deflate_after_switch: int = 0,
    best_cycle_return: bool | None = None,
) -> GmresResult:
    """Solve ``A x = b`` to ``|b - A x| <= max(atol, rtol |b|)``.

    ``deflate_after_switch=k`` (opt-in): GMRES-DR(restart, k) on the cycles that follow the first change of
    preconditioner made by ``cycle_policy`` at a restart boundary, and the plain iteration before it, so that a solve
    whose policy never changes the preconditioner is the plain iteration bit for bit.  It is the deflation of a
    rescue cycle only (``block_amg.StagnationRescue``): the recycled subspace starts with the rescue's first cycle,
    since a subspace built with the earlier preconditioner belongs to another operator.  Works on the single basis
    buffer and on ``basis_chunk``; not with ``deflate`` (which deflates every restart), the device loop or the fp32
    basis.

    ``best_cycle_return`` (opt-in; ``None`` reads ``KRYLOV["best_cycle_return"]``, ``--krylov-best-cycle-return``):
    the solve returns the cycle end with the smallest true residual when its last one is more than ``BEST_FACTOR``
    times above it.  In exact arithmetic a restarted cycle never ends above its start; on a singular operator whose
    Arnoldi vectors lose their relation (re-orthogonalisation at almost every step) a cycle's least-squares update can
    fit a Hessenberg matrix the vectors no longer satisfy, so that its recurrence reads 1e-8 while the cycle ends
    millions of times above where it began.  Also, a cycle that ends on a breakdown (the new Arnoldi vector already in
    the span) with its freshly computed residual above the tolerance is followed by a fresh cycle instead of ending the
    solve.  A solve whose cycle ends never rise tenfold and that never breaks down unsolved is the plain iteration bit
    for bit.

    ``max_iterations`` (opt-in): a cap on the TOTAL number of new Arnoldi steps over all cycles
    (the cycle about to run is shortened to what is left, and no cycle starts once none is left); ``None`` (default)
    is the uncapped iteration.  It is a budget, not a tolerance: the result reports ``converged=False``
    when the cap stops it above the requested residual.

    ``A`` and ``M`` are callables on a flat tensor; neither the operator nor the
    preconditioner is ever formed.  ``restart`` is the length of a Krylov cycle
    and ``max_cycles`` the number of cycles, which is what
    ``scipy.sparse.linalg.gmres`` calls ``maxiter``; the two are named apart here
    because reading ``maxiter`` as a count of Arnoldi steps is the error that
    name invites.  ``max_cycles=None`` allows ``n`` cycles.

    **``restart`` and ``max_cycles`` are two knobs and the matvec budget is
    their PRODUCT, so varying ``restart`` alone is not a single-variable
    experiment.**  A caller who raises ``restart`` without lowering
    ``max_cycles`` has raised the budget by the same factor, and any difference
    it makes is then unattributed between the two.  This has bitten the tree
    twice: ``CoupledNewton.newton.solve_newton`` records a DataCenter comparison
    whose "wider restart" arm spent 2764 and 5731 matvecs against a 1600 ceiling,
    and a flat-plate arm compared 6,400 against 25,600 the same way.

    Measured separation on the TMR flat plate 273x193 rung
    (626,688 unknowns, assembled Jacobian, Ruiz-equilibrated, no preconditioner)
    at two captured states where a ``restart=1600, max_cycles=4`` solve had
    failed.  True relative residual in the original variables, so all three are
    comparable:

    ================================  =========  ================  ================
    arm                               budget     at ||r||2 9.3e-07  at ||r||2 2.9e-07
    ================================  =========  ================  ================
    ``restart 1600, max_cycles 4``     6,400      1.072             0.972
    ``restart 4400, max_cycles 4``    17,600      **0.683**         **0.696**
    ``restart 1600, max_cycles 11``   17,600      1.000             0.919
    ================================  =========  ================  ================

    **The gain is the WINDOW, not the budget**: at an identical 17,600 matvecs
    the wide window returns 0.68-0.70 where the narrow one returns 0.92-1.00,
    and tripling the budget at fixed width buys almost nothing.  An arm that
    varies ``restart`` alone cannot show this, which is why the third row exists.

    **A wider window can be WORSE on an EQUILIBRATED system, and the reason is
    not in this module.**  Same fixture, ``restart 4400, max_cycles 1``
    returned a true relative residual of **6.05** in the ORIGINAL variables --
    larger than the zero guess -- while reporting 0.88.  As measured,
    that 0.88 is not a drift: ``cycle_residuals[-1]``, a freshly computed
    ``|b - A x| / |b|`` of the system actually iterated on, agrees with the
    recurrence to **5.8e-13 relative**, and to 3.2e-15 and 5.0e-15 on the two
    control arms.  **The recurrence this module reports is exact.**  The 6.86x
    gap is entirely the scaled-to-unscaled recovery of
    ``CoupledNewton.equilibration``, which is where that finding is written up;
    what a caller must take from it here is only that ``restart`` interacts with
    an outer equilibration, so a wider window on a Ruiz-scaled system minimises
    a norm that is not the one the caller is judged on.  Unequilibrated, no such
    effect is measured.

    ``deflate=k`` carries ``k`` harmonic Ritz vectors across each restart
    (GMRES-DR; see the module docstring).  It costs ``k + 1`` extra length-``n``
    vectors of scratch, spends ``restart - k`` rather than ``restart`` matvecs
    per cycle after the first, and does not change what the stopping test means:
    every cycle still ends on a freshly computed ``b - A x``, so a deflation
    that went wrong shows up as a solve that does not converge, never as one
    that converges to the wrong answer.  ``deflate=0`` is the plain iteration.

    ``basis_chunk=k`` (opt-in) allocates the
    Arnoldi basis in blocks of ``k`` vectors as the iteration reaches them
    (``_ChunkedBasis``) instead of all ``restart + 1`` vectors up front: the
    memory follows the iterations actually run.  Iterates agree with the
    default to rounding only (the second Gram-Schmidt product sums block by
    block).  Not with ``deflate`` (its recycling rewrites the basis as one
    matrix).  ``0``, the default, is the single buffer, unchanged.

    ``fused_orth`` (opt-in, Warp backend): each Arnoldi step's
    DGKS-CGS2 orthogonalisation runs as fused device kernels with the
    re-orthogonalisation decided on the device and ONE host read-back
    (``SparseSolver.gmres_warp.FusedOrth``) instead of four GEMV calls, three
    norms and four read-backs; the same arithmetic with sums in another fixed
    order (rounding-level differences).  CUDA, single basis buffer only.
    ``None`` (default) reads the process-wide ``KRYLOV["fused_orth"]`` (False
    unless an entry flag set it: ``--krylov-fused-orth``).

    ``device_loop`` (opt-in; ``KRYLOV["device_loop"]``,
    ``--krylov-device-loop``): each cycle's Arnoldi loop -- operator,
    preconditioner, the fused Gram-Schmidt step, the Hessenberg column, its
    Givens rotations and the convergence test -- runs on the device, captured
    once per (preconditioner, operator) and iterated by a CUDA conditional WHILE
    node (``SparseSolver.gmres_warp.DeviceCycle``); the least squares too.  The
    host reads the cycle's counters and history once per cycle (and, with
    ``deflate``, the Hessenberg and the coordinates for the recycling).  Same
    arithmetic as ``fused_orth``.

    ``basis_fp32`` (opt-in ALGORITHM VARIANT;
    ``KRYLOV["basis_fp32"]``, ``--krylov-basis-fp32``): with the fused kernels
    (``fused_orth`` or ``device_loop``) the Arnoldi basis is STORED in float32
    and every product, sum and norm is computed in float64 (compressed-basis
    GMRES, Aliaga, Anzt et al. 2022): half the basis traffic of the
    Gram-Schmidt passes and half the basis memory.  Each new vector is rounded
    to fp32 once when stored, so the Arnoldi relation holds to ~6e-8 per column
    and one cycle can reduce the residual by about seven orders; the true
    residual at every restart, the iterate and the answer stay float64.  Not
    with ``deflate`` or ``basis_chunk``; without the fused kernels (no CUDA /
    Triton) it is ignored.

    The returned ``x`` is detached; see the module docstring for why this
    iteration is not differentiable.
    """
    if fused_orth is None:
        fused_orth = bool(KRYLOV["fused_orth"]) and not basis_chunk
        if KRYLOV["fused_orth"] and basis_chunk:
            _dropped("fused_orth", basis_chunk)
    if device_loop is None:
        device_loop = bool(KRYLOV["device_loop"]) and not basis_chunk
        if KRYLOV["device_loop"] and basis_chunk:
            _dropped("device_loop", basis_chunk)
    if device_loop and basis_chunk:
        raise ValueError("device_loop works on the single basis buffer: not with basis_chunk")
    if fused_orth and basis_chunk:
        raise ValueError("fused_orth works on the single basis buffer: not with basis_chunk")
    if int(basis_chunk) < 0:
        raise ValueError(f"basis_chunk must be >= 0, not {basis_chunk}")
    if basis_chunk and deflate:
        raise ValueError("basis_chunk (a basis allocated block by block) and deflate (GMRES-DR, which rewrites the "
                         "basis as one matrix at every restart) are not combined")
    b = b.detach().reshape(-1)
    if b.dtype != torch.float64:
        raise TypeError(
            f"gmres works in float64 and was given {b.dtype}; the Givens "
            "recurrence and the Hessenberg least squares below are float64 "
            "whatever the basis is, so a narrower right-hand side would make "
            "this a silently mixed-precision solve"
        )
    n = b.numel()
    device, dtype = b.device, b.dtype
    # a cycle cannot hold more independent vectors than the space has
    # dimensions, the same clip scipy applies
    restart = max(1, min(int(restart), n))
    cycles = n if max_cycles is None else int(max_cycles)
    if max_iterations is not None and int(max_iterations) < 1:
        raise ValueError(f"max_iterations={max_iterations}: give a positive cap or None")
    wanted = max(0, int(deflate))
    if wanted >= restart:
        raise ValueError(
            f"deflate={deflate} carries at least as many vectors as a cycle of "
            f"{restart} holds, which leaves no room for an Arnoldi step; the "
            "recycled subspace has to be a proper subspace of the cycle"
        )
    if best_cycle_return is None:
        best_cycle_return = bool(KRYLOV["best_cycle_return"])
    breakdowns, best = 0, (math.inf, None)
    after_switch = max(0, int(deflate_after_switch))
    if after_switch:
        if wanted:
            raise ValueError("deflate (every restart) and deflate_after_switch (the cycles after a change of "
                             "preconditioner) are not combined")
        if after_switch >= restart:
            raise ValueError(f"deflate_after_switch={deflate_after_switch} carries at least as many vectors as a "
                             f"cycle of {restart} holds, which leaves no room for an Arnoldi step")
        if device_loop:
            raise ValueError("deflate_after_switch is not combined with the device loop")

    def operator(v: torch.Tensor) -> torch.Tensor:
        return A(v).detach().reshape(-1)

    active = [M]

    def precondition(v: torch.Tensor) -> torch.Tensor:
        cur = active[0]
        return v if cur is None else cur(v).detach().reshape(-1)

    beta = float(torch.linalg.vector_norm(b))
    if beta == 0.0:
        return GmresResult(torch.zeros_like(b), (), True, 0)
    threshold = max(float(atol), float(rtol) * beta)

    guess = None if x0 is None else x0.detach().reshape(-1)
    # coordinates in the preconditioned space; the answer is guess + M(y)
    y = torch.zeros(n, device=device, dtype=dtype)

    def unknown(coordinates: torch.Tensor) -> torch.Tensor:
        step = precondition(coordinates)
        return step if guess is None else guess + step

    residual = b if guess is None else b - operator(guess)
    history: list[float] = []
    cycle_lengths: list[int] = []
    cycle_residuals: list[float] = []
    reorthogonalisations = 0
    residual_norm = float(torch.linalg.vector_norm(residual))
    start_norm = residual_norm
    if restart_probe is not None:
        restart_probe(0, residual)
    if residual_norm <= threshold:
        return GmresResult(unknown(y), (), True, 0)

    eps = float(torch.finfo(dtype).eps)
    # W2 basis_fp32 (opt-in variant, docstring): the basis is stored in fp32 only where the fused kernels (which read
    # it into fp64) do every basis product of this solve
    store_dtype = dtype
    if basis_fp32 is None:
        basis_fp32 = bool(KRYLOV["basis_fp32"])
    if (basis_fp32 and not basis_chunk and not wanted and not after_switch and dtype == torch.float64
            and (fused_orth or device_loop)):
        from SparseSolver import gmres_warp as _gw
        if _gw.available(device) and (fused_orth or ((M is None or hasattr(M, "apply_blocks"))
                                                     and not _gw.STATS["disabled"])):
            store_dtype = torch.float32
    if basis_chunk:
        # M5 (opt-in): the basis grows block by block; ``project`` / ``combine`` are its two Gram-Schmidt products
        basis = _ChunkedBasis(n, basis_chunk, device, dtype)
        project, combine = basis.project, basis.combine
    else:
        basis = torch.empty((restart + 1, n), device=device, dtype=store_dtype)
        # the very expressions this loop always evaluated, ``V[:k] @ w`` and ``c @ V[:k]``
        project = lambda w, k: basis[:k] @ w             # noqa: E731
        combine = lambda c, k: c @ basis[:k]             # noqa: E731
    fused = None
    if fused_orth:
        # W2 (opt-in): the fused Gram-Schmidt step (SparseSolver.gmres_warp); without CUDA / Triton the default path
        from SparseSolver import gmres_warp as _gw
        if _gw.available(device) and dtype == torch.float64:
            fused = _gw.FusedOrth(basis)
    dcyc = counted = None
    # the device loop captures the preconditioner's kernels: it engages for none or a coupled-AMG-type cycle (a device
    # V-cycle, ``apply_blocks``), never for a callable that may compute on the host (a host value would be frozen into
    # the captured graph)
    capturable_m = M is None or hasattr(M, "apply_blocks")
    if device_loop and capturable_m:
        # W2 (opt-in): every cycle's Arnoldi loop on the device (SparseSolver.gmres_warp.DeviceCycle): the step captured
        # once per (preconditioner, operator) and run by a CUDA conditional WHILE node; the operator's own product
        # counter (CountedOperator, which syncs per product under component timers) is bypassed and updated per cycle
        from SparseSolver import gmres_warp as _gw
        if _gw.available(device) and dtype == torch.float64 and not _gw.STATS["disabled"]:
            dcyc = _gw.DeviceCycle(basis, wanted, float(torch.finfo(dtype).eps))
            fused = None
            raw = A
            if hasattr(A, "apply") and hasattr(A, "count"):
                counted, raw = A, A.apply

            def device_operator(v: torch.Tensor, raw=raw) -> torch.Tensor:
                return raw(v).detach().reshape(-1)
    if store_dtype != dtype:
        # basis_fp32: the cycle's update ``coordinates @ V[:k]`` summed in fp64 by the fused kernels
        if fused is None and dcyc is None:
            raise RuntimeError("gmres(basis_fp32=True): an fp32 basis needs the fused kernels, and none engaged")
        combine = (fused if fused is not None else dcyc.orth).lincomb
    # rotated Hessenberg, on the host and reused across cycles.  Only its upper
    # triangle is ever read, and each cycle rewrites column j down to row j + 1,
    # so whatever an earlier cycle left below the subdiagonal is unreachable.
    triangle = torch.zeros((restart + 1, restart), dtype=torch.float64)
    cosine = [0.0] * restart
    sine = [0.0] * restart

    # GMRES-DR state, allocated only when it is asked for.  ``hessenberg`` is
    # the UNrotated companion of ``triangle``: the Givens sweep destroys the
    # Arnoldi coefficients as it goes, and both the harmonic Ritz problem and
    # the projected restart need them intact.  ``target`` is its right-hand
    # side, likewise unrotated.  ``recycled`` is the only length-``n`` cost of
    # the whole scheme: the new leading basis cannot be written into ``basis``
    # while it is still being read out of it.
    hessenberg = carried_rhs = recycled = None
    if wanted:
        hessenberg = torch.zeros((restart + 1, restart), dtype=torch.float64)
        carried_rhs = torch.zeros(restart + 1, dtype=torch.float64)
        recycled = torch.empty((wanted + 2, n), device=device, dtype=dtype)
    carried, outside = 0, 0.0  # what the cycle about to run inherits
    leading = None  # host factor that triangularises the inherited full block

    for _cycle in range(cycles):
        cycle_cap = restart
        if cycle_policy is not None:
            chosen = cycle_policy(_cycle, residual, active[0])
            if isinstance(chosen, str):
                # Opt-in: a policy may end the solve at a restart boundary (``"stop"``,
                # ``STOP``); the answer is the one the cycles so far produced, reported unconverged
                if chosen != STOP:
                    raise ValueError(f"cycle_policy returned {chosen!r}; the only word it may return is {STOP!r}")
                break
            if isinstance(chosen, tuple):          # (preconditioner, length cap of this cycle)
                chosen, cap = chosen
                cycle_cap = max(1, min(int(cap), restart))
            if chosen is not active[0]:
                # The answer is guess + M(y) with ONE M, y accumulated over the cycles: before the
                # preconditioner changes, the cycles run so far are folded into the guess with the M
                # that produced them (applying the NEW M to the old
                # cycles' coordinates -- the residual stayed true, the earlier work was distorted).
                # A recycled subspace belongs to the old M and is dropped.
                if _cycle > 0:
                    guess = unknown(y)
                    y = torch.zeros_like(y)
                    carried, outside = 0, 0.0
                    if after_switch and not wanted:
                        # deflate_after_switch: GMRES-DR from this cycle on (its state allocated here, once)
                        wanted = after_switch
                        hessenberg = torch.zeros((restart + 1, restart), dtype=torch.float64)
                        carried_rhs = torch.zeros(restart + 1, dtype=torch.float64)
                        recycled = torch.empty((wanted + 2, n), device=device, dtype=dtype)
                active[0] = chosen
        if max_iterations is not None:
            # F1b-e (opt-in): the total budget of new Arnoldi steps (docstring)
            left = int(max_iterations) - sum(cycle_lengths)
            if left <= 0:
                break
            cycle_cap = min(cycle_cap, carried + left)
        if carried:
            # The inherited block is FULL, not Hessenberg (Morgan 2002, sec. 3),
            # so the one-Givens-per-column sweep below cannot reduce it.  One
            # dense QR does, once, and every later column then pays a single
            # (carried+1)-square apply before rejoining that sweep.
            factor, upper = torch.linalg.qr(
                hessenberg[: carried + 1, :carried], mode="complete"
            )
            leading = factor.transpose(0, 1).contiguous()
            triangle[: carried + 1, :carried] = upper
            rotated = [0.0] * (restart + 1)
            rotated[: carried + 1] = (leading @ carried_rhs[: carried + 1]).tolist()
        else:
            basis[0] = residual / residual_norm
            rotated = [0.0] * (restart + 1)
            rotated[0] = residual_norm
            leading = None
            if wanted:
                hessenberg.zero_()
                carried_rhs.zero_()
                carried_rhs[0] = residual_norm
        breakdown = False
        column = carried
        if dcyc is not None:
            # W2 device loop (opt-in): the whole cycle on the device; read back: its counters and history
            column, breakdown, cycle_history, again = dcyc.run(
                (id(active[0]), id(A)), precondition, device_operator, carried=carried, cap=cycle_cap,
                rotated=rotated, triangle=triangle, leading=leading, outside=outside, threshold=threshold, beta=beta)
            history.extend(cycle_history)
            reorthogonalisations += again
            if counted is not None:
                counted.count += column + 1 - carried
            if wanted:
                hessenberg[: column + 2, carried: column + 1] = dcyc.H[: column + 2, carried: column + 1].cpu()
        for column in (range(carried, max(cycle_cap, carried + 1)) if dcyc is None else ()):
            w = operator(precondition(basis[column] if store_dtype == dtype else basis[column].to(dtype)))
            stored = column + 1
            if fused is not None:
                # W2 (opt-in): the same DGKS-CGS2 step on the device, the next basis vector written in place, one
                # read-back (coefficients, length, height, whether it re-orthogonalised)
                fused.step(w, stored)
                fused.store(basis[column + 1])
                entries, length, height, again = fused.read(stored)
                del w
                reorthogonalisations += int(again)
                entries.append(height)
                if height <= eps * length:
                    entries[column + 1] = 0.0
                    breakdown = True
            else:
                length = float(torch.linalg.vector_norm(w))
                coefficients = project(w, stored)  # V^T w, one pass over the whole basis
                w = w - combine(coefficients, stored)  # V h, the second pass
                height = float(torch.linalg.vector_norm(w))
                if height <= _REORTHOGONALISE_BELOW * length:
                    correction = project(w, stored)
                    w = w - combine(correction, stored)
                    coefficients = coefficients + correction
                    height = float(torch.linalg.vector_norm(w))
                    reorthogonalisations += 1
                # the Hessenberg column, read back to the host in one transfer: the
                # rotation sweep that follows is O(restart) scalar operations, and on
                # CUDA each would otherwise be a kernel launch on a single element
                entries = coefficients.to("cpu", torch.float64).tolist()
                entries.append(height)
                if height <= eps * length:
                    # the new vector is already in the span, so this cycle holds the
                    # exact solution and the Hessenberg column ends here
                    entries[column + 1] = 0.0
                    breakdown = True
                else:
                    basis[column + 1] = w / height

            if wanted:
                hessenberg[: column + 2, column] = torch.tensor(
                    entries, dtype=torch.float64
                )
            if leading is not None:
                head = torch.tensor(entries[: carried + 1], dtype=torch.float64)
                entries[: carried + 1] = (leading @ head).tolist()

            for j in range(carried, column):
                c, s = cosine[j], sine[j]
                upper, lower = entries[j], entries[j + 1]
                entries[j] = c * upper + s * lower
                entries[j + 1] = c * lower - s * upper
            c, s, magnitude = _givens(entries[column], entries[column + 1])
            cosine[column], sine[column] = c, s
            entries[column], entries[column + 1] = magnitude, 0.0
            triangle[: column + 2, column] = torch.tensor(entries, dtype=torch.float64)
            rotated[column + 1] = -s * rotated[column]
            rotated[column] = c * rotated[column]
            estimate = abs(rotated[column + 1])
            if outside:
                # the part of the residual the recycled basis does not span is
                # untouchable inside this cycle and adds in quadrature
                estimate = math.hypot(estimate, outside)
            history.append(estimate / beta)
            if estimate <= threshold or breakdown:
                break

        size = column + 1
        cycle_lengths.append(size - carried)
        if dcyc is not None:
            # W2 device loop: the least squares on the device (same pseudo-solve rule); the coordinates go to the
            # host only for GMRES-DR's recycling
            coordinates = dcyc.solve(size)
            y = y + combine(coordinates.to(dtype=dtype), size)
            if wanted:
                coordinates = coordinates.cpu()
        else:
            upper_triangle = triangle[:size, :size]
            target = torch.tensor(rotated[:size], dtype=torch.float64)
            if float(upper_triangle[size - 1, size - 1]) == 0.0:
                # pseudo-solve, as scipy does: a zero pivot can only be the last one
                # (a breakdown), and dropping that component leaves the rest exact
                target[size - 1] = 0.0
                upper_triangle = upper_triangle.clone()
                upper_triangle[size - 1, size - 1] = 1.0
            coordinates = torch.linalg.solve_triangular(
                upper_triangle, target.reshape(size, 1), upper=True
            ).reshape(size)
            y = y + combine(coordinates.to(device=device, dtype=dtype), size)

        if best_cycle_return:
            # (opt-in) the cycle end kept by reference, so that the smallest true residual's iterate can be returned
            current = unknown(y)
            residual = b - operator(current)
        else:
            residual = b - operator(unknown(y))
        residual_norm = float(torch.linalg.vector_norm(residual))
        if restart_probe is not None:
            restart_probe(_cycle + 1, residual)
        cycle_residuals.append(residual_norm / beta)
        breakdowns += int(breakdown)
        if best_cycle_return and residual_norm < best[0]:
            best = (residual_norm, current)
        if residual_norm <= threshold or (breakdown and not best_cycle_return):
            break
        if breakdown:
            # best_cycle_return: the cycle broke down without solving the system (on a singular operator the
            # space can close to rounding with the true residual far above the tolerance): a fresh cycle from the
            # current residual, nothing of the broken-down cycle recycled
            carried, outside = 0, 0.0
            continue
        carried, outside = 0, 0.0
        if wanted:
            carried, outside = _recycle(
                hessenberg, carried_rhs, basis, recycled, coordinates,
                residual, residual_norm, size, wanted,
            )

    gain = float(KRYLOV.get("best_cycle_gain", 0.0) or 0.0)
    if (best_cycle_return and best[1] is not None and residual_norm > BEST_FACTOR * best[0]
            and (not gain > 0 or best[0] < gain * start_norm)):
        # the last cycle ended far above an earlier one (its Arnoldi vectors lost their relation): that one is returned
        cycle_residuals.append(best[0] / beta)
        return GmresResult(best[1], tuple(history), best[0] <= threshold, reorthogonalisations,
                           tuple(cycle_lengths), tuple(cycle_residuals), breakdowns)
    return GmresResult(
        unknown(y), tuple(history), residual_norm <= threshold,
        reorthogonalisations, tuple(cycle_lengths), tuple(cycle_residuals), breakdowns,
    )
