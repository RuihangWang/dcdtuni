"""Sparse Newton/adjoint linear algebra for the DAE: structural pattern, column colouring, sparse LU.

The dense path builds the whole n x n Jacobian with ``jacfwd``: O(n^2) memory
and n directional derivatives per Newton step, which is why the runtime refused
networks above 4000 unknowns (liquid report S3; about 33 Shinaide racks). The
residual of a pipe network is sparse -- an unknown couples only to its edge's
and its nodes' neighbours -- so the Jacobian is recovered from a few
directional derivatives instead:

- the structural pattern is probed once per equation graph with chunked JVPs
  at the current state (memory O(nnz + n * chunk)); other residuals on the
  same graph start from it plus the diagonal;
- columns that share no row get one colour, so one JVP per colour gives every
  nonzero (Curtis-Powell-Reid compression; typically tens of colours);
- every Newton step checks the assembled matrix against one exact JVP in a
  random direction, row by row against each row's own magnitude. A row that
  disagrees has a dependency the pattern lacks -- upwind transport changes
  which neighbour a cell depends on when a flow reverses, and an advection
  term's flow derivative is exactly zero at a steady state with equal inlet
  and outlet temperatures and small but nonzero once they differ -- and its
  exact nonzeros come from one reverse-mode product; the pattern is
  recoloured. A stale pattern is never used silently;
- the matrix is factored once per step with SuperLU (scipy). The adjoint
  solves the transpose with the same factorisation and is checked against the
  exact transpose product.

Assembly stays in PyTorch on the state's device; the factorisation runs on the
host for every device (see ``SparseLU``).
"""
from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import torch
from torch import Tensor

Residual = Callable[[Tensor], Tensor]
PROBE_CHUNK = 256
# A row disagrees with the exact JVP when the gap exceeds ROW_RTOL of that row's
# own magnitude (sum |J_ij p_j| + |(J p)_i|): any missing entry above round-off
# shows up in its row, however small the row is next to the largest one.
ROW_RTOL = 1e-9
FLOOR_RTOL = 1e-13
# Once a check fails, every row whose gap is above round-off is repaired, not
# only the rows past ROW_RTOL: the dependencies that appear with a new state
# come in all sizes, and one probe would otherwise expose them a few at a time.
REPAIR_RTOL = 1e-13
# Once the pattern holds every dependency, what is left is round-off in the
# JVPs themselves; it is accepted below this fraction of the largest product.
CHECK_RTOL = 1e-7


@dataclass
class SparsePattern:
    n: int
    rows: np.ndarray          # int64, nonzero row indices
    cols: np.ndarray          # int64, nonzero column indices
    colour: np.ndarray        # int64, colour of every column
    n_colours: int
    seeds: Tensor             # (n_colours, n) 0/1 seed directions on the state's device
    nnz_colour: Tensor        # colour of every nonzero's column, on the device
    nnz_rows: Tensor          # row of every nonzero, on the device
    refreshes: int = 0


_PATTERNS: "weakref.WeakKeyDictionary[object, Dict[Tuple, SparsePattern]]" = weakref.WeakKeyDictionary()


def _jvp_columns(scaled: Residual, z: Tensor, directions: Tensor) -> Tensor:
    """J @ d for each row d of ``directions`` (shape (k, n)); returns (k, n)."""
    def one(direction):
        return torch.func.jvp(scaled, (z,), (direction,))[1]
    return torch.vmap(one)(directions)


def _probe(scaled: Residual, z: Tensor) -> Tuple[np.ndarray, np.ndarray]:
    """Nonzeros of d scaled / d z at ``z``, column chunk by column chunk (NaN counts as nonzero)."""
    n = z.numel()
    rows, cols = [], []
    eye_dtype = dict(dtype=z.dtype, device=z.device)
    for start in range(0, n, PROBE_CHUNK):
        stop = min(n, start + PROBE_CHUNK)
        block = torch.zeros(stop - start, n, **eye_dtype)
        block[torch.arange(stop - start), torch.arange(start, stop)] = 1
        with torch.no_grad():
            columns = _jvp_columns(scaled, z, block)          # columns[k] = J[:, start + k]
        hit = (columns != 0).nonzero().cpu().numpy()         # (local column, row)
        rows.append(hit[:, 1])
        cols.append(hit[:, 0] + start)
    return np.concatenate(rows).astype(np.int64), np.concatenate(cols).astype(np.int64)


def _rows_by_vjp(scaled: Residual, z: Tensor, rows: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Nonzeros of the given Jacobian rows, exactly: one reverse-mode product per row."""
    if rows.size == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    out_rows, out_cols = [], []
    _value, pullback = torch.func.vjp(scaled, z.detach())
    for start in range(0, rows.size, PROBE_CHUNK):
        chunk = rows[start:start + PROBE_CHUNK]
        selectors = torch.zeros(chunk.size, z.numel(), dtype=z.dtype, device=z.device)
        selectors[torch.arange(chunk.size), torch.as_tensor(chunk, device=z.device)] = 1
        with torch.no_grad():
            grads = torch.vmap(lambda v: pullback(v)[0])(selectors)     # grads[k] = J[chunk[k], :]
        hit = (grads != 0).nonzero().cpu().numpy()
        out_rows.append(chunk[hit[:, 0]])
        out_cols.append(hit[:, 1])
    return np.concatenate(out_rows).astype(np.int64), np.concatenate(out_cols).astype(np.int64)


def _colour(n: int, rows: np.ndarray, cols: np.ndarray) -> Tuple[np.ndarray, int]:
    """Greedy column colouring: two columns with a common row never share a colour."""
    order = np.lexsort((rows, cols))
    rows_by_col = np.split(rows[order], np.searchsorted(cols[order], np.arange(1, n)))
    order = np.lexsort((cols, rows))
    cols_by_row = np.split(cols[order], np.searchsorted(rows[order], np.arange(1, n)))
    colour = np.full(n, -1, dtype=np.int64)
    for j in range(n):
        taken = set()
        for i in rows_by_col[j]:
            taken.update(int(colour[k]) for k in cols_by_row[i] if colour[k] >= 0)
        c = 0
        while c in taken:
            c += 1
        colour[j] = c
    return colour, int(colour.max()) + 1 if n else 0


def _build(z: Tensor, rows: np.ndarray, cols: np.ndarray, refreshes: int) -> SparsePattern:
    n = z.numel()
    keys = np.unique(rows * n + cols)
    rows, cols = keys // n, keys % n
    colour, n_colours = _colour(n, rows, cols)
    seeds = torch.zeros(n_colours, n, dtype=z.dtype, device=z.device)
    seeds[torch.as_tensor(colour, device=z.device), torch.arange(n, device=z.device)] = 1
    return SparsePattern(n=n, rows=rows, cols=cols, colour=colour, n_colours=n_colours, seeds=seeds,
                         nnz_colour=torch.as_tensor(colour[cols], device=z.device),
                         nnz_rows=torch.as_tensor(rows, device=z.device), refreshes=refreshes)


def _probe_full(scaled: Residual, z: Tensor, refreshes: int = 0) -> SparsePattern:
    rows, cols = _probe(scaled, z.detach())
    return _build(z, rows, cols, refreshes)


def _repaired(scaled: Residual, z: Tensor, pattern: SparsePattern, rows: np.ndarray) -> SparsePattern:
    """``pattern`` plus the exact nonzeros of ``rows`` (a dependency it missed: a reversed flow, an opened valve)."""
    extra_rows, extra_cols = _rows_by_vjp(scaled, z, np.unique(rows))
    return _build(z, np.concatenate([pattern.rows, extra_rows]), np.concatenate([pattern.cols, extra_cols]),
                  pattern.refreshes + 1)


def pattern_for(owner: object, scaled: Residual, z: Tensor, kind: str = "") -> SparsePattern:
    """The cached pattern of ``owner``'s residual of this ``kind`` (probed on first use).

    ``owner`` is the equation graph (stable across steps); ``kind`` names the
    residual built on it -- steady, time step, constrained steady -- since
    each has its own structure.
    """
    key = (kind, z.numel(), z.dtype, str(z.device))
    if owner is not None:
        try:
            cached = _PATTERNS.setdefault(owner, {})
        except TypeError:  # an owner that cannot be weakly referenced is not cached
            cached = {}
    else:
        cached = {}
    pattern = cached.get(key)
    if pattern is None:
        # Another residual on the same graph (steady beside a time step, a
        # constrained steady) shares nearly all of its structure: start from it
        # plus the diagonal, and let the row repairs add the rest. Only the
        # first residual of a graph pays the full O(n^2) probe.
        sibling = next((p for k, p in cached.items() if k[1:] == key[1:]), None)
        if sibling is not None:
            n = z.numel()
            pattern = _build(z, np.concatenate([sibling.rows, np.arange(n)]),
                             np.concatenate([sibling.cols, np.arange(n)]), 0)
        else:
            pattern = _probe_full(scaled, z)
        cached[key] = pattern
    return pattern


def values(scaled: Residual, z: Tensor, pattern: SparsePattern) -> Tensor:
    """The pattern's nonzero values at ``z``: one JVP per colour."""
    with torch.no_grad():
        compressed = _jvp_columns(scaled, z.detach(), pattern.seeds)   # (n_colours, n)
    return compressed[pattern.nnz_colour, pattern.nnz_rows]


def _sparse_matvec(pattern: SparsePattern, data: Tensor, vector: Tensor, transpose: bool = False) -> Tensor:
    rows = torch.as_tensor(pattern.cols if transpose else pattern.rows, device=vector.device)
    cols = torch.as_tensor(pattern.rows if transpose else pattern.cols, device=vector.device)
    out = torch.zeros_like(vector)
    out.index_add_(0, rows, data * vector[cols])
    return out


def checked_values(owner: object, scaled: Residual, z: Tensor, kind: str = "") -> Tuple[SparsePattern, Tensor]:
    """Values at ``z`` from a pattern that matches one exact JVP in a random direction.

    Rows where the assembled matrix disagrees with the exact product (relative
    to the row's own magnitude) are the rows with a dependency the pattern
    lacks; their exact nonzeros come from one reverse-mode product each, and
    the pattern is recoloured. A disagreement that survives a repair adding no
    entry is round-off in the JVPs, accepted below ``CHECK_RTOL``.
    """
    pattern = pattern_for(owner, scaled, z, kind)
    n = z.numel()
    for attempt in range(6):
        generator = torch.Generator(device="cpu").manual_seed(pattern.refreshes + 7 + attempt)
        probe = torch.randn(n, generator=generator, dtype=z.dtype).to(z.device)
        data = values(scaled, z, pattern)
        with torch.no_grad():
            exact = torch.func.jvp(scaled, (z.detach(),), (probe,))[1]
        mismatch = (_sparse_matvec(pattern, data, probe) - exact).abs()
        scale = _sparse_matvec(pattern, data.abs(), probe.abs()) + exact.abs()
        bad = (mismatch > ROW_RTOL * scale + FLOOR_RTOL * scale.max()) | ~torch.isfinite(mismatch)
        if not bool(bad.any()):
            return pattern, data
        repair = (mismatch > REPAIR_RTOL * scale + FLOOR_RTOL * scale.max()) | ~torch.isfinite(mismatch)
        rows = repair.nonzero().flatten().cpu().numpy()
        grown = (_probe_full(scaled, z, pattern.refreshes + 1) if rows.size > n // 4
                 else _repaired(scaled, z, pattern, rows))
        added = np.setdiff1d(grown.rows * n + grown.cols, pattern.rows * n + pattern.cols).size
        if not added:
            if bool(torch.isfinite(mismatch).all()) and float(mismatch.max()) <= CHECK_RTOL * (1 + float(exact.abs().max())):
                return pattern, data
            break
        pattern = grown
        if owner is not None:
            try:
                _PATTERNS.setdefault(owner, {})[(kind, n, z.dtype, str(z.device))] = pattern
            except TypeError:
                pass
    raise RuntimeError("sparse DAE Jacobian does not match the exact directional derivative")


@dataclass
class SparseLU:
    """One factorisation of J, solved for J x = b and J^T x = b.

    SuperLU on the host for every device: ``torch.sparse.spsolve`` on CUDA
    needs a cuDSS build of PyTorch, which the supported environment is not.
    The Jacobian values (the JVPs, the expensive part) stay on the device;
    only the n-vector right-hand side and solution cross.
    """
    pattern: SparsePattern
    data: Tensor
    _lu: object = field(default=None, repr=False)

    def __post_init__(self):
        from scipy.sparse import csc_matrix
        from scipy.sparse.linalg import splu
        matrix = csc_matrix((self.data.detach().cpu().numpy(), (self.pattern.rows, self.pattern.cols)),
                            shape=(self.pattern.n, self.pattern.n))
        try:
            self._lu = splu(matrix)
        except RuntimeError as exc:
            raise RuntimeError("singular sparse DAE Newton Jacobian; inspect constraints and reference states") from exc

    def solve(self, rhs: Tensor, transpose: bool = False) -> Tensor:
        solution = self._lu.solve(rhs.detach().cpu().numpy(), trans="T" if transpose else "N")
        solution = torch.from_numpy(solution).to(rhs.device, rhs.dtype)
        if not bool(torch.isfinite(solution).all()):
            raise RuntimeError("non-finite sparse DAE linear solve")
        return solution


def direction(owner: object, scaled: Residual, z: Tensor, rhs: Tensor, kind: str = "") -> Tensor:
    """Newton direction: J(z) d = rhs."""
    pattern, data = checked_values(owner, scaled, z, kind)
    return SparseLU(pattern, data).solve(rhs).detach()


def adjoint(owner: object, scaled: Residual, z: Tensor, residual: Tensor, rhs: Tensor,
            kind: str = "") -> Tuple[Tensor, Tensor]:
    """Dual of the implicit root: J(z)^T dual = rhs, with the exact-transpose residual of that solve."""
    pattern, data = checked_values(owner, scaled, z, kind)
    dual = SparseLU(pattern, data).solve(rhs, transpose=True)
    exact = torch.autograd.grad(residual, z, grad_outputs=dual, retain_graph=True)[0].detach()
    return dual.detach(), exact - rhs


def stats(owner: object) -> Dict[str, int]:
    """Pattern sizes cached for ``owner`` (for receipts)."""
    try:
        cached = _PATTERNS.get(owner, {})
    except TypeError:
        cached = {}
    return {f"{k[0]}:{k[1]}": {"nnz": int(p.rows.size), "colours": p.n_colours, "refreshes": p.refreshes}
            for k, p in cached.items()}


def cache_identity(residual) -> Tuple[object, str]:
    """(owner, kind) the Newton root attached to ``residual`` (``None`` owner: not cached)."""
    return getattr(residual, "_dae_sparse_owner", None), getattr(residual, "__qualname__", type(residual).__name__)
