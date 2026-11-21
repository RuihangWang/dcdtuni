"""Krylov subspace recycling for right-preconditioned GMRES, the slow modes of the preconditioned operator, and a
Broyden-updated preconditioner.

The iteration runs on ``K = A M`` (right preconditioning: ``x = M z``).  A recycled space is ``k`` directions
``U`` in ``z`` together with ``C = K U D`` with ``C^T C = I`` and ``D`` the diagonal that gives ``U`` unit columns.

* ``gcro_dr``: GCRO-DR(m, k) (Parks, de Sturler, Mackey, Johnson, Maiti, SIAM J. Sci. Comput. 28(5):1651-1674,
  2006, Algorithm 1).  With a recycled ``U`` the solve first removes the part of ``b`` that ``C`` spans and then runs
  cycles of ``m - k`` Arnoldi steps of ``(I - C C^T) K``; the Arnoldi vectors are orthogonalised against ``C`` and
  the earlier vectors in one Gram-Schmidt pass, so ``K V = C B + V_+ Hbar``.  ``update=True`` replaces ``U`` at every
  restart and at the end of the solve by the ``k`` harmonic Ritz vectors of ``K`` of smallest modulus on the
  augmented space ``[U, V]`` (``GcroResult.space``, the space handed to the next system); ``update=False`` keeps
  ``U`` fixed (deflation by a given space).  Without ``U`` the first cycle is GMRES(m) and, with ``update``, the space
  is formed from its harmonic Ritz vectors.  ``k = 0`` is restarted GMRES(m).
* ``RecycleSpace``: the space kept across a sequence of solves under one right preconditioner (a time march, the
  Newton steps of one preconditioner build), discarded when the preconditioner is rebuilt.
* ``slow_space``: the harmonic Ritz pairs of ``K`` of smallest modulus to a residual tolerance, by thick-restarted
  harmonic Arnoldi (Morgan and Zeng, Linear Algebra Appl. 415:96-113, 2006): each restart keeps the wanted harmonic
  Ritz vectors and the common direction of their residuals, so the kept block satisfies an Arnoldi relation exactly.
* ``BroydenInverse``: Broyden's update of an approximate inverse in product form,
  ``H_{j+1} = (I + u_j s_j^T / c_j) H_j`` with ``u_j = s_j - H_j y_j`` and ``c_j = s_j^T H_j y_j``, so that
  ``H_{j+1} y_j = s_j`` (Bergamaschi, Bru, Martinez, Putti, Electron. Trans. Numer. Anal. 23:76-87, 2006).  Every
  application is one application of the base preconditioner followed by one dot product and one update per pair.

Everything is float64; the small dense problems (Hessenberg least squares, harmonic Ritz problems) are solved on the
host.  No iteration here is differentiable.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import scipy.linalg as sla
import torch

_REORTHOGONALISE_BELOW = 0.7071067811865476
# On a singular operator (the free pressure level of A M), a harmonic Ritz value below this fraction of the median
# modulus is its null mode, which a consistent right-hand side never excites: not a slow mode, never kept (callers
# pass null_rel=NULL_REL for such operators, 0 otherwise).
NULL_REL = 1e-9


def _sync(t: torch.Tensor) -> None:
    if t.is_cuda:
        torch.cuda.synchronize(t.device)


@dataclass
class GcroResult:
    """The answer and the history of one ``gcro_dr`` solve.

    ``residuals``, ``converged``, ``cycle_lengths`` and ``cycle_residuals`` have the meaning of
    ``SparseSolver.gmres.GmresResult`` (recurrence residual per Arnoldi step relative to ``|b|``; the true residual at
    the end of each cycle), so ``camg_train.confirmed_its`` / ``true_metrics`` read both.  ``space`` is the recycled
    space at the end of the solve, ``(U, None)`` rows ``(k, n)`` with unit norm (``None`` when no space was formed).
    ``setup_products``: applications of ``K`` spent on ``C = K U`` before the first Arnoldi step; ``seconds``: wall
    time of the whole call, ``setup_seconds`` of the space set-up, ``update_seconds`` of the space updates."""

    x: torch.Tensor
    residuals: tuple
    converged: bool
    reorthogonalisations: int
    cycle_lengths: tuple
    cycle_residuals: tuple
    k: int = 0
    setup_products: int = 0
    seconds: float = 0.0
    setup_seconds: float = 0.0
    update_seconds: float = 0.0
    updates: int = 0
    space: torch.Tensor | None = None
    harmonic: list = field(default_factory=list)

    @property
    def iterations(self) -> int:
        return len(self.residuals)


def _givens(f: float, g: float):
    if g == 0.0:
        return 1.0, 0.0, f
    if f == 0.0:
        return 0.0, math.copysign(1.0, g), abs(g)
    length = math.hypot(f, g)
    r = math.copysign(length, f)
    return abs(f) / length, g / r, r


def _real_basis(values: np.ndarray, vectors: np.ndarray, order, wanted: int) -> tuple[np.ndarray, list]:
    """Real columns spanning the eigenvectors of ``order`` (indices sorted by preference) up to ``wanted`` columns;
    a complex pair contributes its real and imaginary part and is dropped whole when it does not fit."""
    cols, kept, spent = [], [], set()
    for i in order:
        if len(cols) >= wanted:
            break
        if i in spent:
            continue
        v, vec = values[i], vectors[:, i]
        real = abs(v.imag) <= 1e-12 * (1.0 + abs(v))
        if len(cols) + (1 if real else 2) > wanted:
            break
        spent.add(i)
        if real:
            cols.append(vec.real)
            kept.append(complex(v.real, 0.0))
        else:
            cols.append(vec.real)
            cols.append(vec.imag)
            kept.extend([complex(v), complex(v).conjugate()])
            partner = int(np.argmin(np.abs(values - np.conj(v)) + np.where(np.arange(values.size) == i, np.inf, 0)))
            spent.add(partner)
    if not cols:
        return np.zeros((vectors.shape[0], 0)), []
    return np.stack(cols, axis=1), kept


def _harmonic(G: np.ndarray, WtV: np.ndarray):
    """Harmonic Ritz pairs of ``K`` on a space ``Vh`` with ``K Vh = Wh G``: ``G^T G p = theta G^T Wh^T Vh p``.  Returns
    finite eigenvalues and eigenvectors sorted by modulus."""
    A = G.T @ G
    B = G.T @ WtV
    values, vectors = sla.eig(A, B)
    ok = np.isfinite(values)
    values, vectors = values[ok], vectors[:, ok]
    order = np.argsort(np.abs(values))
    return values[order], vectors[:, order]


def _cut(mods: np.ndarray, k: int) -> int:
    """How many of the moduli ``mods`` (ascending) to take for at most ``k`` without cutting through a tie (a complex
    pair has equal moduli)."""
    c = min(k, mods.size)
    while 0 < c < mods.size and abs(mods[c] - mods[c - 1]) <= 1e-10 * max(mods[c - 1], 1e-300):
        c -= 1
    return c


def _threshold(mods: np.ndarray, c: int) -> float:
    return float("inf") if c >= mods.size else 0.5 * (mods[c - 1] + mods[c])


def _ordered_schur(Hh: np.ndarray, k: int, floor: float = 0.0):
    """Orthonormal real basis of the invariant subspace of ``Hh`` for its ``c <= k`` eigenvalues of smallest modulus
    above ``floor`` (ordered real Schur form; no pair is split): ``(Z_c, values sorted by modulus)``.  Values at or
    below ``floor`` (the null mode of a singular operator) are never selected."""
    vals = np.linalg.eigvals(Hh)
    order = np.argsort(np.abs(vals))
    vals = vals[order]
    vals = vals[np.abs(vals) > floor]
    mods = np.abs(vals)
    c = _cut(mods, k)
    if c == 0:
        return np.zeros((Hh.shape[0], 0)), []
    t = _threshold(mods, c)

    def sel(re, im=0.0):              # real Schur form: the eigenvalue as (real part, imaginary part)
        m = abs(complex(re) + 1j * float(im))
        return floor < m < t

    try:
        _, Z, sdim = sla.schur(Hh, output="real", sort=sel)
        return Z[:, :sdim], [complex(v) for v in vals[:sdim]]
    except (np.linalg.LinAlgError, ValueError):
        # LAPACK refuses an ill-conditioned reordering (values close to the cut move across it): an orthonormal basis
        # of the eigenvectors of the same c values instead
        w, X = np.linalg.eig(Hh)
        order = np.argsort(np.abs(w))
        w, X = w[order], X[:, order]
        keep = np.abs(w) > floor
        P, kept = _real_basis(w[keep], X[:, keep], range(int(keep.sum())), c)
        if P.shape[1] == 0:
            return np.zeros((Hh.shape[0], 0)), []
        Q, _ = np.linalg.qr(P)
        return Q, kept


def _ordered_pencil(A: np.ndarray, B: np.ndarray, k: int, floor_rel: float = 0.0):
    """Orthonormal real basis of the right deflating subspace of the pencil ``(A, B)`` for its ``c <= k`` finite
    eigenvalues of smallest modulus above ``floor_rel`` times the median finite modulus (ordered QZ), or ``None``."""
    vals = sla.eigvals(A, B)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return None
    floor = floor_rel * float(np.median(np.abs(vals)))
    vals = vals[np.argsort(np.abs(vals))]
    vals = vals[np.abs(vals) > floor]
    mods = np.abs(vals)
    c = _cut(mods, k)
    if c == 0:
        return None
    t = _threshold(mods, c)

    def sel(alpha, beta):             # ordqz: arrays alpha (complex), beta; eigenvalue alpha / beta
        alpha, beta = np.asarray(alpha), np.abs(np.asarray(beta))
        m = np.abs(alpha)
        return (beta != 0) & (m < t * beta) & (m > floor * beta)

    _, _, _, _, _, Z = sla.ordqz(A, B, sort=sel, output="real")
    sdim = int(np.sum(mods < t))
    return Z[:, :sdim], [complex(v) for v in vals[:sdim]]


def _new_space(rows_W: torch.Tensor, rows_V: torch.Tensor, G: np.ndarray, WtV: np.ndarray, k: int,
               null_rel: float = 0.0):
    """The ``k`` harmonic Ritz vectors of smallest modulus on the space ``Vh`` (rows ``rows_V``) with
    ``K Vh = Wh G`` (``Wh`` orthonormal rows ``rows_W``): returns ``(U, C, d, values)`` with ``K U = C diag(1/d)``,
    ``U`` unit rows, ``C`` orthonormal rows, or ``None`` when the problem cannot be posed.  The space is the right
    deflating subspace of the harmonic pencil ``(G^T G, G^T Wh^T Vh)`` for those values (ordered QZ, an orthonormal
    basis); ``K U = C diag(1/d)`` holds for any basis by construction."""
    try:
        got = _ordered_pencil(G.T @ G, G.T @ WtV, k, null_rel)
    except (np.linalg.LinAlgError, ValueError):
        got = None
    if got is None:
        try:
            values, vectors = _harmonic(G, WtV)
        except (np.linalg.LinAlgError, ValueError):
            return None
        P, kept = _real_basis(values, vectors, range(values.size), k)
    else:
        P, kept = got
    if P.shape[1] == 0:
        return None
    GP = G @ P
    Q, R = np.linalg.qr(GP)
    diag = np.abs(np.diag(R))
    if not np.all(np.isfinite(R)) or diag.min() <= 1e-13 * diag.max():
        return None
    dev, dt = rows_V.device, rows_V.dtype
    C = torch.as_tensor(Q.T.copy(), device=dev, dtype=dt) @ rows_W
    Y = torch.as_tensor(P.T.copy(), device=dev, dtype=dt) @ rows_V
    Rinv_T = torch.as_tensor(np.linalg.inv(R).T.copy(), device=dev, dtype=dt)
    U = Rinv_T @ Y                    # rows of Y R^{-1}
    d = torch.linalg.vector_norm(U, dim=1)
    U = U / d[:, None]
    return U, C, d, [complex(v) for v in kept]


def recycle_setup(K: Callable, U: torch.Tensor):
    """``C`` and the scaling of a recycled space ``U`` (rows) for the operator ``K``: ``W = K U`` (one application of
    ``K`` per row), ``W^T = Q R``, ``U <- U R^{-1}`` scaled to unit rows, ``C = Q^T``; returns ``(U, C, d)`` with
    ``K U = C diag(1/d)``, or ``None`` when ``K U`` is rank deficient."""
    W = torch.stack([K(U[i]) for i in range(U.shape[0])])
    Q, R = torch.linalg.qr(W.T)
    Rh = R.detach().cpu().numpy()
    diag = np.abs(np.diag(Rh))
    if not np.all(np.isfinite(Rh)) or diag.min() <= 1e-10 * diag.max():
        return None
    Rinv_T = torch.as_tensor(np.linalg.inv(Rh).T.copy(), device=U.device, dtype=U.dtype)
    Un = Rinv_T @ U
    d = torch.linalg.vector_norm(Un, dim=1)
    return Un / d[:, None], Q.T.contiguous(), d


def gcro_dr(
    A: Callable[[torch.Tensor], torch.Tensor],
    b: torch.Tensor,
    *,
    M: Callable[[torch.Tensor], torch.Tensor] | None = None,
    U: torch.Tensor | None = None,
    k: int = 0,
    restart: int = 200,
    max_iterations: int = 600,
    rtol: float = 1e-8,
    update: bool = True,
    null_rel: float = 0.0,
) -> GcroResult:
    """Solve ``A x = b`` with right preconditioner ``M`` by GCRO-DR(``restart``, ``k``) (module docstring).

    ``U`` (rows, any scaling) is the recycled space; ``k`` the size of the space formed by ``update`` (and the
    maximum kept); ``max_iterations`` caps the Arnoldi steps over all cycles.  Each cycle runs ``restart - k``
    Arnoldi steps when a space is present (the cycle's subspace then has ``restart`` directions), ``restart``
    without.  Every cycle ends on the true residual ``b - A M z``."""
    t_start = time.perf_counter()
    b = b.detach().reshape(-1)
    if b.dtype != torch.float64:
        raise TypeError("gcro_dr works in float64")
    n = b.numel()
    device, dtype = b.device, b.dtype
    prec = (lambda v: v) if M is None else (lambda v: M(v).detach().reshape(-1))
    K = lambda v: A(prec(v)).detach().reshape(-1)       # noqa: E731
    beta_b = float(torch.linalg.vector_norm(b))
    if beta_b == 0.0:
        return GcroResult(torch.zeros_like(b), (), True, 0, (), ())
    threshold = rtol * beta_b
    eps = float(torch.finfo(dtype).eps)
    z = torch.zeros(n, device=device, dtype=dtype)
    r = b.clone()
    Uk = Ck = dk = None
    kk = 0
    setup_products = 0
    setup_seconds = 0.0
    if U is not None and U.shape[0] > 0:
        t0 = time.perf_counter()
        made = recycle_setup(K, U.to(device=device, dtype=dtype))
        setup_products = U.shape[0]
        _sync(b)
        setup_seconds = time.perf_counter() - t0
        if made is not None:
            Uk, Ck, dk = made
            kk = Uk.shape[0]
            alpha = Ck @ r
            z = z + (dk * alpha) @ Uk
            r = r - alpha @ Ck
    history: list[float] = []
    cycle_lengths: list[int] = []
    cycle_residuals: list[float] = []
    reorth = 0
    update_seconds = 0.0
    updates = 0
    harmonic: list = []
    r_norm = float(torch.linalg.vector_norm(r))
    its = 0
    last = None
    while r_norm > threshold and its < max_iterations:
        m_cycle = max(1, (restart - kk) if kk else restart)
        cap = min(m_cycle, max_iterations - its)
        basis = torch.empty((kk + cap + 1, n), device=device, dtype=dtype)
        if kk:
            basis[:kk] = Ck
        basis[kk] = r / r_norm
        hbar = np.zeros((cap + 1, cap))
        bmat = np.zeros((kk, cap))
        tri = np.zeros((cap + 1, cap))
        cs, sn = np.zeros(cap), np.zeros(cap)
        g = np.zeros(cap + 1)
        g[0] = r_norm
        breakdown = False
        j = -1
        for j in range(cap):
            w = K(basis[kk + j])
            nb = kk + j + 1
            length = float(torch.linalg.vector_norm(w))
            h = basis[:nb] @ w
            w = w - h @ basis[:nb]
            height = float(torch.linalg.vector_norm(w))
            if height <= _REORTHOGONALISE_BELOW * length:
                h2 = basis[:nb] @ w
                w = w - h2 @ basis[:nb]
                h = h + h2
                height = float(torch.linalg.vector_norm(w))
                reorth += 1
            hh = h.detach().cpu().numpy()
            if kk:
                bmat[:, j] = hh[:kk]
            hbar[: j + 1, j] = hh[kk:]
            hbar[j + 1, j] = height
            if height <= eps * length:
                hbar[j + 1, j] = 0.0
                basis[kk + j + 1].zero_()
                breakdown = True
            else:
                basis[kk + j + 1] = w / height
            col = hbar[: j + 2, j].copy()
            for i in range(j):
                a, c = col[i], col[i + 1]
                col[i] = cs[i] * a + sn[i] * c
                col[i + 1] = cs[i] * c - sn[i] * a
            c_, s_, mag = _givens(col[j], col[j + 1])
            cs[j], sn[j] = c_, s_
            col[j], col[j + 1] = mag, 0.0
            tri[: j + 2, j] = col
            g[j + 1] = -s_ * g[j]
            g[j] = c_ * g[j]
            est = abs(g[j + 1])
            history.append(est / beta_b)
            its += 1
            if est <= threshold or breakdown:
                break
        size = j + 1
        R = tri[:size, :size].copy()
        rhs = g[:size].copy()
        if R[size - 1, size - 1] == 0.0:
            rhs[size - 1] = 0.0
            R[size - 1, size - 1] = 1.0
        y = sla.solve_triangular(R, rhs, lower=False)
        yt = torch.as_tensor(y, device=device, dtype=dtype)
        dz = yt @ basis[kk: kk + size]
        if kk:
            a = -(dk * torch.as_tensor(bmat[:, :size] @ y, device=device, dtype=dtype))
            dz = dz + a @ Uk
        z = z + dz
        r = b - A(prec(z)).detach().reshape(-1)
        r_norm = float(torch.linalg.vector_norm(r))
        cycle_lengths.append(size)
        cycle_residuals.append(r_norm / beta_b)
        if update and k > 0:
            last = (basis, kk, size, hbar[: size + 1, :size].copy(), bmat[:, :size].copy(), Uk, Ck, dk)
        done = r_norm <= threshold or breakdown or its >= max_iterations
        if update and k > 0 and not done:
            t0 = time.perf_counter()
            made = _update_from(last, k, null_rel)
            _sync(b)
            update_seconds += time.perf_counter() - t0
            if made is not None:
                Uk, Ck, dk, vals = made
                kk = Uk.shape[0]
                updates += 1
                harmonic = vals
        del basis
        if kk and not done:
            alpha = Ck @ r
            z = z + (dk * alpha) @ Uk
            r = r - alpha @ Ck
            r_norm = float(torch.linalg.vector_norm(r))
        if breakdown:
            break
    space = Uk
    if update and k > 0 and last is not None:
        t0 = time.perf_counter()
        made = _update_from(last, k, null_rel)
        _sync(b)
        update_seconds += time.perf_counter() - t0
        if made is not None:
            space = made[0]
            harmonic = made[3]
            updates += 1
    x = prec(z).clone()           # a preconditioner replayed from a CUDA graph returns its static output buffer
    _sync(b)
    final = r_norm
    return GcroResult(x, tuple(history), final <= threshold, reorth, tuple(cycle_lengths), tuple(cycle_residuals),
                      k=kk, setup_products=setup_products, seconds=time.perf_counter() - t_start,
                      setup_seconds=setup_seconds, update_seconds=update_seconds, updates=updates, space=space,
                      harmonic=harmonic)


def _update_from(last, k: int, null_rel: float = 0.0):
    """The recycled space from the last cycle's augmented space ``[U, V]`` (``_new_space``)."""
    basis, kk, size, hbar, bmat, Uk, Ck, dk = last
    if size < 1:
        return None
    G = np.zeros((kk + size + 1, kk + size))
    WtV = np.zeros((kk + size + 1, kk + size))
    if kk:
        dh = dk.detach().cpu().numpy()
        G[:kk, :kk] = np.diag(1.0 / dh)
        G[:kk, kk:] = bmat
        WtV[:kk, :kk] = (Ck @ Uk.T).detach().cpu().numpy()
        WtV[kk:, :kk] = (basis[kk: kk + size + 1] @ Uk.T).detach().cpu().numpy()
    G[kk:, kk:] = hbar
    WtV[kk: kk + size, kk:] = np.eye(size)
    rows_W = basis[: kk + size + 1]
    rows_V = torch.cat([Uk, basis[kk: kk + size]]) if kk else basis[kk: kk + size]
    return _new_space(rows_W, rows_V, G, WtV, k, null_rel)


class RecycleSpace:
    """The recycled space of a sequence of linear solves ``A_i x_i = b_i`` under one right preconditioner ``M``
    (GCRO-DR across systems, Parks et al. 2006, section 2.4): the space each solve ends with (``GcroResult.space``) is
    re-orthonormalised against the next operator (``recycle_setup``, ``k`` applications of ``A M``) and deflates the
    next solve.  The caller keeps one ``RecycleSpace`` across the solves of a time march or of the Newton steps under
    one preconditioner build.

    ``token`` names the preconditioner build the space belongs to: the space lives in the right-preconditioned
    coordinates (``x = M z``), so a solve with another token discards it and forms a new one (``refreshes``).  Every
    solve has the stopping test of ``SparseSolver.gmres.gmres``, ``|b - A x| <= rtol |b|`` on the true residual at
    the end of every cycle, so a recycled space changes how many iterations a solve takes, never what it accepts."""

    def __init__(self, k: int = 16, *, null_rel: float = 0.0):
        if int(k) < 1:
            raise ValueError(f"RecycleSpace needs k >= 1, not {k}")
        self.k = int(k)
        self.null_rel = float(null_rel)
        self.U: torch.Tensor | None = None
        self.token = None
        self.stats = {"solves": 0, "recycled": 0, "refreshes": 0, "setup_products": 0, "iterations": 0,
                      "unconverged": 0, "setup_seconds": 0.0, "update_seconds": 0.0}

    def reset(self) -> None:
        """Forget the space (the next solve forms a new one)."""
        if self.U is not None:
            self.stats["refreshes"] += 1
        self.U = None

    def solve(self, A: Callable, b: torch.Tensor, *, M: Callable | None = None, rtol: float, restart: int = 200,
              max_iterations: int = 600, token=None) -> GcroResult:
        """One solve of the sequence (``gcro_dr`` with the kept space); the space is replaced by the solve's own."""
        if token != self.token:
            self.reset()
            self.token = token
        U = self.U
        if U is not None and (U.shape[1] != b.numel() or U.device != b.device):
            self.reset()
            U = None
        result = gcro_dr(A, b, M=M, U=U, k=self.k, restart=restart, max_iterations=max_iterations, rtol=rtol,
                         update=True, null_rel=self.null_rel)
        s = self.stats
        s["solves"] += 1
        s["recycled"] += int(U is not None)
        s["setup_products"] += int(result.setup_products)
        s["iterations"] += int(result.iterations)
        s["unconverged"] += int(not result.converged)
        s["setup_seconds"] += float(result.setup_seconds)
        s["update_seconds"] += float(result.update_seconds)
        if result.space is not None and result.space.shape[0] > 0:
            self.U = result.space.detach()
        return result

    def report(self) -> dict:
        return dict(self.stats, k=self.k, kept=0 if self.U is None else int(self.U.shape[0]))


@dataclass
class SlowSpace:
    """``slow_space``'s result: ``spaces[k]`` rows ``(c, n)``, orthonormal, spanning the harmonic Ritz invariant
    subspace of the ``c <= k`` values of smallest modulus (no complex pair split); ``vectors`` the largest of them;
    ``values`` the ``wanted`` (pair-complete) values of smallest modulus, by modulus; ``residuals``
    ``|K y - theta y| / |theta|`` per value; ``converged`` whether every one met ``tol``; ``products`` applications of
    ``K``; ``restarts``; ``spectrum`` every harmonic Ritz value of the final space (modulus order); ``ritz`` the
    (standard) Ritz values of the final space, which approximate the outer part of the spectrum; ``seconds``;
    ``history`` the largest relative residual of the wanted pairs after each restart."""

    vectors: torch.Tensor
    values: list
    residuals: list
    converged: bool
    products: int
    restarts: int
    spectrum: list
    seconds: float
    history: list
    ritz: list = field(default_factory=list)
    spaces: dict = field(default_factory=dict)
    nulls: list = field(default_factory=list)


def slow_space(K: Callable[[torch.Tensor], torch.Tensor], v0: torch.Tensor, *, wanted: int = 16, basis: int = 240,
               keep: int = 40, tol: float = 1e-5, max_products: int = 4000, spaces_for=None,
               null_rel: float = 0.0) -> SlowSpace:
    """The ``wanted`` harmonic Ritz pairs of ``K`` of smallest modulus, by thick-restarted harmonic Arnoldi.

    Cycles of ``basis`` Arnoldi steps; at each restart an orthonormal basis of the harmonic Ritz invariant subspace of
    the ``keep`` values of smallest modulus (ordered real Schur form of ``H + h^2 H^-T e_m e_m^T``) and the common
    direction of the harmonic residuals form the first ``keep + 1`` basis vectors, so ``K V_keep = V_{keep+1}
    Hbar_keep`` holds to rounding.  Stops when every wanted pair has ``|K y - theta y| <= tol |theta|`` or after
    ``max_products`` applications of ``K``.  ``spaces_for`` (default ``(wanted,)``): the sizes k for which the
    result carries the subspace of the k smallest values (``SlowSpace.spaces``)."""
    t_start = time.perf_counter()
    v0 = v0.detach().reshape(-1)
    n = v0.numel()
    device, dtype = v0.device, v0.dtype
    eps = float(torch.finfo(dtype).eps)
    m = min(basis, n - 1)
    V = torch.empty((m + 1, n), device=device, dtype=dtype)
    V[0] = v0 / torch.linalg.vector_norm(v0)
    H = np.zeros((m + 1, m))
    start = 0
    products = restarts = 0
    history: list = []
    while True:
        size = m
        for j in range(start, m):
            w = K(V[j]).detach().reshape(-1)
            products += 1
            length = float(torch.linalg.vector_norm(w))
            h = V[: j + 1] @ w
            w = w - h @ V[: j + 1]
            h2 = V[: j + 1] @ w
            w = w - h2 @ V[: j + 1]
            h = h + h2
            height = float(torch.linalg.vector_norm(w))
            H[: j + 1, j] = h.detach().cpu().numpy()
            H[j + 1, j] = height
            if height <= eps * length:
                size = j + 1
                H[j + 1, j] = 0.0
                break
            V[j + 1] = w / height
        Hc = H[: size + 1, :size]
        Hs = Hc[:size, :size]
        hlast = float(Hc[size, size - 1])
        em = np.zeros(size)
        em[-1] = 1.0
        try:
            f = np.linalg.solve(Hs.T, em)
        except np.linalg.LinAlgError:
            f = np.linalg.lstsq(Hs.T, em, rcond=None)[0]
        # the harmonic Ritz problem as a standard one (Morgan 2002, eq. 2.3): (H + h^2 f e_m^T) g = theta g; every
        # residual H_bar g - theta [g; 0] is a multiple of [-h f; 1]
        Hh = Hs + hlast * hlast * np.outer(f, em)
        wdir = np.concatenate([-hlast * f, [1.0]])
        wnorm = float(np.linalg.norm(wdir))
        values, vectors = np.linalg.eig(Hh)
        order = np.argsort(np.abs(values))
        values, vectors = values[order], vectors[:, order]
        vectors = vectors / np.linalg.norm(vectors, axis=0, keepdims=True)
        rel = abs(hlast) * np.abs(vectors[-1, :]) * wnorm / np.maximum(np.abs(values), 1e-300)
        floor = null_rel * float(np.median(np.abs(values))) if values.size else 0.0
        live = np.abs(values) > floor
        nulls = [complex(v) for v in values[~live]]
        values, rel = values[live], rel[live]
        nw = _cut(np.abs(values), wanted)
        worst = float(np.max(rel[:nw])) if nw else float("inf")
        history.append(worst)
        if worst <= tol or products >= max_products or size < m:
            return _slow_result(V, size, Hc, Hh, values, rel, nw, worst <= tol, products, restarts, t_start, history,
                                spaces_for or (wanted,), device, dtype, floor, nulls)
        P_k, _ = _ordered_schur(Hh, keep, floor)
        kk = P_k.shape[1]
        aug = np.zeros((size + 1, kk + 1))
        aug[:size, :kk] = P_k
        aug[:, -1] = wdir / wnorm
        Qa, _ = np.linalg.qr(aug)
        block = Qa.T @ Hc @ Qa[:size, :kk]
        closure = np.linalg.norm(Hc @ Qa[:size, :kk] - Qa @ block) / max(np.linalg.norm(Hc @ Qa[:size, :kk]), 1e-300)
        if closure > 1e-8:
            return _slow_result(V, size, Hc, Hh, values, rel, nw, False, products, restarts, t_start,
                                history + ["closure"], spaces_for or (wanted,), device, dtype, floor, nulls)
        Vn = torch.as_tensor(Qa.T.copy(), device=device, dtype=dtype) @ V[: size + 1]
        V[: kk + 1] = Vn
        del Vn
        H = np.zeros((m + 1, m))
        H[: kk + 1, :kk] = block
        start = kk
        restarts += 1


def _slow_result(V, size, Hc, Hh, values, rel, nw, converged, products, restarts, t_start, history, spaces_for,
                 device, dtype, floor=0.0, nulls=()) -> "SlowSpace":
    """The result of ``slow_space``: for every k of ``spaces_for`` the orthonormal basis (rows) of the harmonic Ritz
    invariant subspace of the k smallest values (ordered Schur form, pairs not split); ``vectors`` the largest."""
    spaces = {}
    for k in sorted(set(int(v) for v in spaces_for)):
        Z, _ = _ordered_schur(Hh, k, floor)
        spaces[k] = torch.as_tensor(Z.T.copy(), device=device, dtype=dtype) @ V[:size]
    top = spaces[max(spaces)] if spaces else torch.zeros((0, V.shape[1]), device=device, dtype=dtype)
    return SlowSpace(top, [complex(v) for v in values[:nw]], [float(v) for v in rel[:nw]], bool(converged), products,
                     restarts, [complex(v) for v in values], time.perf_counter() - t_start, history, _ritz(Hc, size),
                     spaces, list(nulls))


def _ritz(Hc: np.ndarray, size: int) -> list:
    """Ritz values of the square part of a cycle's coefficient matrix, by decreasing modulus."""
    try:
        values = np.linalg.eigvals(Hc[:size, :size])
    except np.linalg.LinAlgError:
        return []
    return [complex(v) for v in values[np.argsort(-np.abs(values))]]


class BroydenInverse:
    """``H_j`` of the module docstring around a base preconditioner ``M`` (a linear callable on flat vectors).

    ``update(s, y)`` adds the secant pair (``H y = s`` afterwards); ``project`` (optional) is applied to ``s`` first
    (the gauge projection of the system, so that the corrections stay in the range of ``M``).  A pair with
    ``|s^T H y| <= 1e-12 |s| |H y|`` is refused (returns False).  ``__call__(v)`` applies the product form."""

    def __init__(self, M: Callable[[torch.Tensor], torch.Tensor], project: Callable | None = None):
        self.M, self.project = M, project
        self.u: list = []
        self.s: list = []
        self.c: list = []

    @property
    def rank(self) -> int:
        return len(self.u)

    def __call__(self, v: torch.Tensor) -> torch.Tensor:
        z = self.M(v).detach().reshape(-1)
        for u, s, c in zip(self.u, self.s, self.c):
            z = z + u * (torch.dot(s, z) / c)
        return z

    def update(self, s: torch.Tensor, y: torch.Tensor) -> bool:
        s = s.detach().reshape(-1)
        if self.project is not None:
            s = self.project(s).reshape(-1)
        Hy = self(y.detach().reshape(-1))
        c = float(torch.dot(s, Hy))
        if not math.isfinite(c) or abs(c) <= 1e-12 * float(torch.linalg.vector_norm(s) * torch.linalg.vector_norm(Hy)):
            return False
        self.u.append(s - Hy)
        self.s.append(s)
        self.c.append(c)
        return True
