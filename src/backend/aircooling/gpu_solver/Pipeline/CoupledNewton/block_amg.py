"""Coupled (whole-cell) algebraic multigrid on the assembled Jacobian, in torch.

The shipped preconditioner splits the Jacobian by field (SIMPLE shape: momentum sweep, pressure
V-cycle, transported tail).  On the TRUE buoyant Jacobian that split is what fails (provenance
``docs/provenance/*-openfoam-20261002`` section 7).  This module is the other classical
family -- the "coupled solver" of commercial finite-volume codes: every level keeps all ``F``
fields of a cell together as one ``F x F`` block, aggregates whole cells, and smooths with
point-block relaxation, so the momentum-pressure-energy coupling is carried on every level.

It is written so that the same object is (a) a classical GPU baseline and (b) the skeleton of the
learned preconditioner: every numeric quantity (prolongation blocks, restriction blocks, smoother
blocks, Galerkin products, the dense coarsest solve) is a differentiable torch expression of the
fine matrix values and of optional learned corrections, while everything combinatorial
(block pattern, aggregates, colouring, SpGEMM index plans) is computed once per pattern.

Conventions: the solver's vectors are FIELD-BLOCKED (``[f0 cells | f1 cells | ...]``); here they
are reshaped to ``[n_cell, F]`` (cell-interleaved), which is a view change, not a reordering of the
mesh.  ``BlockCSR`` stores blocks row-major sorted.

Classical choices (one V-cycle per application): strength from block Frobenius norms
``|A_ij| / sqrt(|A_ii| |A_jj|)``, pyamg standard aggregation, tentative prolongation ``I_F`` per
aggregate (opt-in ``near_null=RigidBodyModes(...)``: the translations and rotations of a displacement field,
smoothed aggregation for elasticity, with ``F + r`` coarse unknowns per aggregate), Jacobi-smoothed prolongation ``P = (I - w D^-1 A) P0`` with ``w = 4/3 / rho(D^-1 A)``,
restriction ``P^T`` or the Petrov-Galerkin ``P0^T (I - w A D^-1)``, Galerkin ``A_c = R A P``,
point-block Jacobi or multicolour point-block Gauss-Seidel smoothing, dense LU on the coarsest
level with a free constant-pressure mode pinned there.  The result of an application is projected
onto the gauge complement by the caller-supplied ``gauge`` (if any).
"""
from __future__ import annotations

import contextlib
import math
import contextlib
import os
import time
import warnings
import weakref
from dataclasses import dataclass, field, replace as _replace

import numpy as np
import scipy.sparse as sp
import torch


# Opt-in aids of the measured stagnation rescue's reading (``l1_rescue_measurement``).  MEMORY_TRACE (on with
# ``--camg-memory-trace``) logs the device memory at named points of the rescue, line by line as it happens, so a run
# that ends out of memory keeps every line before the failure.  MATVEC_CHUNK > 0 makes ``BlockCSR.matvec`` add its block
# products in slices of that many blocks; LEAN_READING (on with ``--camg-rescue-lean-reading``) sets it for the
# reading's products only, so they never hold two temporaries of the matrix's full block count.
MEMORY_TRACE = {"on": False, "log": None}
MATVEC_CHUNK = {"blocks": 0}
LEAN_READING = {"on": False, "blocks": 1 << 22}


def memory_note(at: str, **extra) -> None:
    """MEMORY_TRACE: the device's allocated and reserved memory and the run's peak so far (GiB) at ``at``.  The peak
    counter is never reset here, so the entry's closing peak line stays the peak of the whole run."""
    if not MEMORY_TRACE["on"] or not torch.cuda.is_available():
        return
    gib = float(1 << 30)
    row = {"at": at, "allocated": round(torch.cuda.memory_allocated() / gib, 3),
           "reserved": round(torch.cuda.memory_reserved() / gib, 3),
           "run_peak": round(torch.cuda.max_memory_allocated() / gib, 3), **extra}
    (MEMORY_TRACE["log"] or print)("camg memory: " + " ".join(f"{k}={v}" for k, v in row.items()))

# =============================================================================== BlockCSR
class BlockCSR:
    """Block-sparse matrix: ``val[e]`` is the ``F x G`` block at ``(row[e], col[e])``."""

    def __init__(self, row: torch.Tensor, col: torch.Tensor, val: torch.Tensor, shape):
        self.row, self.col, self.val = row, col, val
        self.shape = (int(shape[0]), int(shape[1]))
        self._ptr = None

    @property
    def F(self):
        return int(self.val.shape[1])

    @property
    def G(self):
        return int(self.val.shape[2])

    @property
    def nnzb(self):
        return int(self.row.numel())

    def ptr(self) -> torch.Tensor:
        if self._ptr is None:
            counts = torch.bincount(self.row, minlength=self.shape[0])
            self._ptr = torch.zeros(self.shape[0] + 1, dtype=torch.long, device=self.row.device)
            self._ptr[1:] = torch.cumsum(counts, 0)
        return self._ptr

    def with_values(self, val: torch.Tensor) -> "BlockCSR":
        out = BlockCSR(self.row, self.col, val, self.shape)
        out._ptr = self._ptr
        return out

    def matvec(self, x: torch.Tensor) -> torch.Tensor:
        """``x`` is ``[n_cols, G]``; returns ``[n_rows, F]``."""
        chunk = MATVEC_CHUNK["blocks"]
        if chunk and self.row.numel() > chunk:         # opt-in (MATVEC_CHUNK): the block products slice by slice
            out = torch.zeros(self.shape[0], self.F, dtype=torch.result_type(self.val, x), device=x.device)
            for s in range(0, self.row.numel(), chunk):
                e = slice(s, s + chunk)
                out.index_add_(0, self.row[e], torch.einsum("efg,eg->ef", self.val[e], x[self.col[e]]))
            return out
        y = torch.einsum("efg,eg->ef", self.val, x[self.col])
        return torch.zeros(self.shape[0], self.F, dtype=y.dtype, device=y.device).index_add(0, self.row, y)

    def transpose(self, *, release_self: bool = False) -> "BlockCSR":
        order = torch.argsort(self.col.to(torch.int64) * self.shape[0] + self.row.to(torch.int64))
        row_t = self.col[order]
        col_t = self.row[order]
        if release_self and self.val.is_cuda and self.val.numel() * self.val.element_size() > (1 << 30):
            dev = self.val.device
            order_cpu = order.cpu()
            del order
            val_cpu = self.val.cpu()[order_cpu].transpose(1, 2).contiguous()
            del order_cpu
            self.val = None
            torch.cuda.empty_cache()
            val = val_cpu.to(dev)
            del val_cpu
        else:
            val = self.val[order]
            if self.F == self.G:
                for r in range(self.F):
                    for c in range(r + 1, self.G):
                        tmp = val[:, r, c].clone()
                        val[:, r, c] = val[:, c, r]
                        val[:, c, r] = tmp
            else:
                val = val.transpose(1, 2).contiguous()
        return BlockCSR(row_t, col_t, val, (self.shape[1], self.shape[0]))

    def diagonal_index(self) -> torch.Tensor:
        """Index of the diagonal block of every row (square matrices; -1 where absent)."""
        idx = torch.full((self.shape[0],), -1, dtype=torch.long, device=self.row.device)
        hit = torch.nonzero(self.row == self.col).reshape(-1)
        idx[self.row[hit]] = hit
        return idx

    def diagonal(self) -> torch.Tensor:
        idx = self.diagonal_index()
        D = torch.zeros(self.shape[0], self.F, self.G, dtype=self.val.dtype, device=self.val.device)
        ok = idx >= 0
        return D.index_put((torch.nonzero(ok).reshape(-1),), self.val[idx[ok]])

    def rows_subset(self, rows: torch.Tensor) -> "BlockCSR":
        """The block rows ``rows`` (kept in the original numbering)."""
        mask = torch.zeros(self.shape[0], dtype=torch.bool, device=self.row.device)
        mask[rows] = True
        keep = torch.nonzero(mask[self.row]).reshape(-1)
        out = BlockCSR(self.row[keep], self.col[keep], self.val[keep], self.shape)
        out.source = keep
        return out

    def to_dense(self) -> torch.Tensor:
        n, m, F, G = self.shape[0], self.shape[1], self.F, self.G
        f = torch.arange(F, device=self.row.device)
        g = torch.arange(G, device=self.row.device)
        rows = (self.row[:, None, None] * F + f[None, :, None]).expand(-1, F, G)
        cols = (self.col[:, None, None] * G + g[None, None, :]).expand(-1, F, G)
        D = torch.zeros(n * F, m * G, dtype=self.val.dtype, device=self.val.device)
        return D.index_put((rows.reshape(-1), cols.reshape(-1)), self.val.reshape(-1), accumulate=True)

    def scalar_csr(self) -> sp.csr_matrix:
        """Cell-interleaved scalar CSR (host), for setup-only algorithms."""
        F, G = self.F, self.G
        f = np.arange(F); g = np.arange(G)
        r = (self.row.cpu().numpy()[:, None, None] * F + f[None, :, None]).repeat(G, 2)
        c = (self.col.cpu().numpy()[:, None, None] * G + g[None, None, :]).repeat(F, 1)
        v = self.val.detach().cpu().numpy()
        return sp.csr_matrix((v.reshape(-1), (r.reshape(-1), c.reshape(-1))),
                             shape=(self.shape[0] * F, self.shape[1] * G))


_FBC_CHUNK = 1 << 25        # scalar entries per slab of from_field_blocked_csr


def from_field_blocked_csr(crow: torch.Tensor, col: torch.Tensor, val: torch.Tensor,
                           n_cell: int, num_fields: int, *,
                           drop_zero_blocks: bool = False, chunk: int | None = None) -> BlockCSR:
    """Field-blocked scalar CSR (the solver's ``J``) -> cell ``F x F`` blocks.

    The block pattern is the union over field pairs; ``val`` keeps its autograd graph.

    ``drop_zero_blocks`` (opt-in) removes the
    blocks whose ``F x F`` values are all zero.  ``jacobian.assemble(fixed_pattern="structural")``
    stores the exact zeros the default assembly filters out, and a block holding only those would
    enter the aggregation graph, the colouring and the Galerkin pattern as a connection.  Every
    scalar slot receives one value at most, so a kept block's values are ``0 + v`` -- bit for bit
    the default assembly's -- and the kept blocks are exactly its blocks: the AMG built from a
    structural ``J`` with this flag is the AMG built from today's ``J``.  Default False: the
    pattern is whatever ``crow``/``col`` store.

    ``chunk`` (default ``_FBC_CHUNK`` = 32M entries): the
    one-pass conversion below holds six nnz-length int64 arrays (the row, ``i``, ``f``, ``j``,
    ``g``, the unique inverse), the blocks and ``index_put``'s sort buffers at once -- about 37 GiB
    at the 418M stored entries of the DHC88 structural ``J``, where the learned camg run died
    asking for 6.3 GiB more.  A matrix with more stored entries than ``chunk`` is converted in
    row slabs of about ``chunk`` entries instead (``_field_blocked_slabs``) when no autograd graph
    is recorded; a few GiB of temporaries at that size.  The output is the same, bit for bit:
    ``uniq`` is the same sorted set of block keys, every entry lands in the same scalar slot, and
    each slot receives the same values in the same order, added to 0.0 in the same way (a slab is a
    range of whole rows, so even duplicate (row, col) entries of a non-canonical CSR meet in one
    ``index_put`` in their original order).  Up to ``chunk`` entries, with ``chunk <= 0``, or under
    a recorded graph (training), the one-pass code runs unchanged."""
    n, F = int(n_cell), int(num_fields)
    device = crow.device
    step = _FBC_CHUNK if chunk is None else int(chunk)
    if 0 < step < int(col.numel()) and not (torch.is_grad_enabled() and val.requires_grad):
        uniq, blocks = _field_blocked_slabs(crow, col, val, n, F, step)
    else:
        counts = crow[1:] - crow[:-1]
        r = torch.repeat_interleave(torch.arange(n * F, device=device), counts)
        c = col.long()
        i, f = r % n, r // n
        j, g = c % n, c // n
        uniq, inv = torch.unique(i * n + j, return_inverse=True)
        blocks = torch.zeros(uniq.numel(), F, F, dtype=val.dtype, device=device)
        blocks = blocks.index_put((inv, f, g), val, accumulate=True)
    if drop_zero_blocks:
        keep = (blocks != 0).flatten(1).any(1).nonzero(as_tuple=True)[0]
        uniq, blocks = uniq.index_select(0, keep), blocks.index_select(0, keep)
    return BlockCSR(uniq // n, uniq % n, blocks, (n, n))


def _field_blocked_slabs(crow: torch.Tensor, col: torch.Tensor, val: torch.Tensor, n: int, F: int, step: int):
    """``(uniq, blocks)`` of ``from_field_blocked_csr`` in row slabs of about ``step`` stored entries.

    Pass 1 merges the slabs' sorted unique block keys ``i n + j`` into ``uniq`` (``torch.unique`` of the union is
    the one-pass ``uniq``); pass 2 recomputes each slab's keys, finds their blocks by ``searchsorted`` in ``uniq``
    (the one-pass inverse: every key is in ``uniq``, which is strictly increasing) and accumulates the slab into the
    zero blocks in place.  The keys are recomputed rather than kept, so the temporaries are a few slab-length int64
    arrays and ``index_put``'s sort buffers for one slab."""
    device = crow.device
    rows = int(crow.numel()) - 1
    nnz = int(col.numel())
    # slab boundaries: the first row whose entries start at or after each multiple of ``step`` (whole rows only)
    marks = torch.searchsorted(crow, torch.arange(0, nnz, step, device=device, dtype=crow.dtype))
    bounds = sorted(set(marks.tolist()) | {0, rows})
    offsets = crow[torch.as_tensor(bounds, dtype=torch.long, device=device)].tolist()
    slabs = [(ra, rb, ea, eb) for ra, rb, ea, eb in zip(bounds[:-1], bounds[1:], offsets[:-1], offsets[1:])
             if eb > ea]

    def keys(ra, rb, ea, eb, fields=True):
        """The slab's block keys ``i n + j`` (and its row / column fields ``f``, ``g``), as the one-pass code."""
        counts = (crow[ra + 1:rb + 1] - crow[ra:rb]).long()
        r = torch.repeat_interleave(torch.arange(ra, rb, device=device), counts, output_size=eb - ea)
        c = col[ea:eb].long()
        key = (r % n) * n + c % n
        return (key, r // n, c // n) if fields else key

    uniq = None
    for slab in slabs:
        part = torch.unique(keys(*slab, fields=False))
        uniq = part if uniq is None else torch.unique(torch.cat([uniq, part]))
    if uniq is None:
        uniq = torch.zeros(0, dtype=torch.long, device=device)
    blocks = torch.zeros(uniq.numel(), F, F, dtype=val.dtype, device=device)
    for ra, rb, ea, eb in slabs:
        key, f, g = keys(ra, rb, ea, eb)
        inv = torch.searchsorted(uniq, key)
        del key
        blocks.index_put_((inv, f, g), val[ea:eb], accumulate=True)
        del inv, f, g
    return uniq, blocks


def from_scipy_field_blocked(A: sp.spmatrix, n_cell: int, num_fields: int, *, device="cpu",
                             dtype=torch.float64) -> BlockCSR:
    A = sp.csr_matrix(A)
    return from_field_blocked_csr(torch.as_tensor(A.indptr, dtype=torch.long, device=device),
                                  torch.as_tensor(A.indices, dtype=torch.long, device=device),
                                  torch.as_tensor(A.data, dtype=dtype, device=device),
                                  n_cell, num_fields)


# ============================================================================== SpGEMM
class SpGEMM:
    """``C = A B`` for two BlockCSR with fixed patterns: symbolic once, numeric differentiable."""

    def __init__(self, A: BlockCSR, B: BlockCSR, chunk: int = 1 << 22):
        if A.shape[1] != B.shape[0]:
            raise ValueError(f"shapes {A.shape} x {B.shape}")
        device = A.row.device
        b_ptr = B.ptr()
        start = b_ptr[A.col]
        cnt = b_ptr[A.col + 1] - start
        eA = torch.repeat_interleave(torch.arange(A.nnzb, device=device), cnt)
        offset = torch.cumsum(cnt, 0) - cnt
        within = torch.arange(eA.numel(), device=device) - torch.repeat_interleave(offset, cnt)
        eB = start[eA] + within
        key = A.row[eA] * B.shape[1] + B.col[eB]
        uniq, out = torch.unique(key, return_inverse=True)
        self.eA, self.eB, self.out = eA, eB, out
        self.row, self.col = uniq // B.shape[1], uniq % B.shape[1]
        self.shape = (A.shape[0], B.shape[1])
        self.chunk = int(chunk)
        # Lean plans (``CoupledAMGPlan(lean=True)``): the triple index arrays live in pinned host memory
        # (``to_host``) and every numeric pass streams one chunk at a time; accumulation is in place.  False: the default plan.
        self.lean = False
        self.identity_a = False         # M5: eA is arange (A x a one-block-per-row B): dropped, chunks are slices
        self._triples = int(eA.numel())

    @property
    def triples(self):
        return self._triples

    def to_host(self) -> "SpGEMM":
        """Move the triple index arrays ``eA``, ``eB``, ``out`` to pinned host memory (lean plans).

        They are the plan's largest arrays -- 24 bytes per triple, about 3 GiB for the fine-level products of DHC88
        -- and only a numeric pass reads them, one chunk at a time, so they need not occupy device memory between
        (or during) builds; ``__call__`` and ``fused`` copy each chunk to the device as they reach it.  The output
        pattern (``row``, ``col``) stays where it is.  Values are not touched: the products are the same."""
        if self.lean:                  # already moved: idempotent
            return self
        # A x P0 (one block per row of P0, the tentative prolongation) visits A's blocks in order: eA is arange and its
        # chunks are slices -- nothing to store, and a slice of A's values is a view, not a gathered copy
        # (the end values first: a product whose B rows hold several blocks has eA[-1] < triples - 1, and is decided
        # without the full comparison's arange -- 3.8 GB for the 477M triples of A P at 152^3)
        n_t = self._triples
        self.identity_a = bool(n_t > 0 and int(self.eA[0]) == 0 and int(self.eA[-1]) == n_t - 1 and torch.equal(
            self.eA, torch.arange(n_t, device=self.eA.device, dtype=self.eA.dtype)))
        self.eA = None if self.identity_a else _to_pinned(self.eA)
        self.eB, self.out = _to_pinned(self.eB), _to_pinned(self.out)
        self.lean = True
        return self

    def to_warp(self) -> "SpGEMM":
        """Opt-in (``WARP_NUMERIC["on"]``): instead of ``to_host``, sort the triples by output once
        into the Warp segments (``block_amg_warp.SegmentedTriples``: int32 operand indices and segment offsets on the
        device, 8 bytes per triple instead of 24) and drop ``eA`` / ``eB`` / ``out``.  Every later numeric pass of
        this product runs ``block_amg_warp.spgemm`` / ``block_sums``; a pass the Warp path cannot take (autograd, CPU,
        not float64) raises instead of silently reading nothing (``warp_only``)."""
        if getattr(self, "warp_only", False):
            return self
        from Pipeline.CoupledNewton import block_amg_warp as _bw
        n_t = self._triples
        dev = self.out.device
        self.identity_a = bool(n_t > 0 and self.eA is not None and int(self.eA[0]) == 0
                               and int(self.eA[-1]) == n_t - 1 and torch.equal(
                                   self.eA, torch.arange(n_t, device=self.eA.device, dtype=self.eA.dtype)))
        self._warp_triples = _bw.SegmentedTriples(self, dev)
        self.eA = self.eB = self.out = None
        self.lean = True
        self.warp_only = True
        return self

    def _warp_ok(self, *vals) -> bool:
        """W6: this product runs on the Warp segments (flag on, CUDA float64 values, no autograd graph wanted)."""
        if not (WARP_NUMERIC["on"] or getattr(self, "warp_only", False)):
            return False
        ok = all(v.is_cuda and v.dtype == torch.float64 for v in vals) and not (
            torch.is_grad_enabled() and any(v.requires_grad for v in vals))
        if not ok and getattr(self, "warp_only", False):
            raise RuntimeError("SpGEMM: the triples live in the Warp segments only (--camg-warp-numeric); this product "
                               "(autograd, CPU or not float64) needs the torch path")
        return ok

    def _triple(self, e: slice, device):
        """Chunk ``e`` of ``(eA, eB, out)`` on ``device`` (copied from pinned host memory after ``to_host``); the eA
        chunk is the slice ``e`` itself when ``identity_a``."""
        ib, io = self.eB[e], self.out[e]
        if ib.device != torch.device(device):
            ib, io = ib.to(device, non_blocking=True), io.to(device, non_blocking=True)
        if self.identity_a:
            return slice(e.start, min(e.stop, self._triples)), ib, io
        ia = self.eA[e]
        if ia.device != torch.device(device):
            ia = ia.to(device, non_blocking=True)
        return ia, ib, io

    def __call__(self, A_val: torch.Tensor, B_val: torch.Tensor) -> BlockCSR:
        if self._warp_ok(A_val, B_val):
            # W6 (opt-in): one Warp kernel over the output blocks, triples sorted by output once (block_amg_warp)
            from Pipeline.CoupledNewton import block_amg_warp as _bw
            return BlockCSR(self.row, self.col, _bw.spgemm(self, A_val, B_val), self.shape)
        F, H = A_val.shape[1], B_val.shape[2]
        C = torch.zeros(self.row.numel(), F, H, dtype=A_val.dtype, device=A_val.device)
        if not self.lean:
            for s in range(0, self.triples, self.chunk):
                e = slice(s, s + self.chunk)
                C = C.index_add(0, self.out[e], torch.bmm(A_val[self.eA[e]], B_val[self.eB[e]]))
            return BlockCSR(self.row, self.col, C, self.shape)
        # Lean plans: the same products and the same index_add per chunk; in place when no autograd graph is
        # recorded (the out-of-place form held a second C per chunk), streamed indices
        inplace = not (torch.is_grad_enabled() and (A_val.requires_grad or B_val.requires_grad))
        for s in range(0, self.triples, self.chunk):
            ia, ib, io = self._triple(slice(s, s + self.chunk), A_val.device)
            prod = torch.bmm(A_val[ia], B_val[ib])
            C = C.index_add_(0, io, prod) if inplace else C.index_add(0, io, prod)
        return BlockCSR(self.row, self.col, C, self.shape)

    def fused(self, a_of, B_val: torch.Tensor, rows: int) -> BlockCSR:
        """``self(A_val, B_val)`` without ``A_val``: ``a_of(idx)`` returns ``A_val[idx]`` for one chunk's A-block
        indices (lean numeric).  ``CoupledAMGPlan._level_numeric`` computes ``D^-1 A`` and
        ``D^-T A^T`` this way chunk by chunk instead of materialising them (an nnzb x F x F array each, built from
        two more of that size: 9.3 GiB of transients on DHC88).  ``rows`` is the A blocks' row count F.  The chunk
        products are the bmm the full array would have been sliced from, so C is the same (exactly, if the batched
        product of a block does not depend on its batch: checked in tests/test_m5_memory.py).  No autograd."""
        C = torch.zeros(self.row.numel(), int(rows), B_val.shape[2], dtype=B_val.dtype, device=B_val.device)
        for s in range(0, self.triples, self.chunk):
            ia, ib, io = self._triple(slice(s, s + self.chunk), B_val.device)
            C.index_add_(0, io, torch.bmm(a_of(ia), B_val[ib]))
        return BlockCSR(self.row, self.col, C, self.shape)


# ======================================================================= setup helpers
def block_strength(A: BlockCSR, theta: float = 0.0) -> sp.csr_matrix:
    """Symmetric cell-graph strength ``|A_ij|_F / sqrt(|A_ii|_F |A_jj|_F)`` (host CSR)."""
    norms = torch.linalg.matrix_norm(A.val.detach()).cpu().numpy()
    row, col = A.row.cpu().numpy(), A.col.cpu().numpy()
    n = A.shape[0]
    diag = np.zeros(n)
    on = row == col
    diag[row[on]] = norms[on]
    diag = np.where(diag > 0, diag, norms.max() if norms.size else 1.0)
    s = norms / np.sqrt(diag[row] * diag[col])
    off = ~on
    S = sp.csr_matrix((s[off], (row[off], col[off])), shape=(n, n))
    S = S.maximum(S.T).tocsr()
    if theta > 0:
        # classical "strong if s_ij >= theta * max_k s_ik"
        rmax = np.maximum.reduceat(S.data, S.indptr[:-1]) if S.nnz else np.zeros(n)
        rmax = np.repeat(np.where(np.diff(S.indptr) > 0, rmax, 0), np.diff(S.indptr))
        S.data = np.where(S.data >= theta * rmax, S.data, 0.0)
        S.eliminate_zeros()
    return S


def aggregate(S: sp.csr_matrix, method: str = "standard") -> np.ndarray:
    """Cell -> aggregate map; cells pyamg leaves unaggregated become singletons."""
    import pyamg.aggregation.aggregate as agg_mod
    n = S.shape[0]
    if method == "standard":
        AggOp, _ = agg_mod.standard_aggregation(S)
    elif method == "naive":
        AggOp, _ = agg_mod.naive_aggregation(S)
    else:
        raise ValueError(method)
    AggOp = AggOp.tocsr()
    out = np.full(n, -1, dtype=np.int64)
    rows = np.repeat(np.arange(n), np.diff(AggOp.indptr))
    out[rows] = AggOp.indices
    free = np.flatnonzero(out < 0)
    n_agg = int(AggOp.shape[1])
    out[free] = n_agg + np.arange(free.size)
    # compact numbering
    _, out = np.unique(out, return_inverse=True)
    return out


def greedy_colouring(S: sp.csr_matrix) -> np.ndarray:
    """Greedy distance-1 colouring of the (symmetric) cell graph, largest-degree first: every node in that order
    takes the smallest colour none of its already coloured neighbours has.  With ``COLOURING["compiled"]`` the same
    sequential loop runs as one compiled CPU kernel (``_greedy_colouring_compiled``, the same colours); otherwise, or
    where the kernel is not available, the loop below."""
    if COLOURING["compiled"] and S.shape[0] >= COLOURING["min_nodes"]:
        out = _greedy_colouring_compiled(S)
        if out is not None:
            return out
    n = S.shape[0]
    order = np.argsort(-np.diff(S.indptr), kind="stable")
    colour = np.full(n, -1, dtype=np.int64)
    indptr, indices = S.indptr, S.indices
    for v in order:
        used = colour[indices[indptr[v]:indptr[v + 1]]]
        used = set(used[used >= 0].tolist())
        c = 0
        while c in used:
            c += 1
        colour[v] = c
    return colour


# ``greedy_colouring``'s compiled loop (opt-in): ``compiled`` switches it on for graphs of at least ``min_nodes`` nodes;
# ``failed`` holds the reason when the kernel could not be built (the loop in Python then runs, with the same result)
COLOURING = {"compiled": False, "min_nodes": 4096, "failed": None, "calls": 0}
_GREEDY_KERNEL = None


def _greedy_colouring_compiled(S: sp.csr_matrix):
    """``greedy_colouring`` as one sequential Warp kernel on the CPU (the same order, the same smallest-free-colour
    rule, so the same colours): a node's neighbours stamp the colours they use with the node's index, and the node
    takes the first colour not stamped with it.  None when Warp's CPU kernels are not available."""
    # the kernel's annotations are read as strings (postponed evaluation) in this module's namespace: ``wp`` is
    # made a module name, as ``SparseSolver.gmres_warp`` does
    global _GREEDY_KERNEL, wp
    if COLOURING["failed"] is not None:
        return None
    try:
        import warp
        wp = warp
        if _GREEDY_KERNEL is None:
            wp.init()

            @wp.kernel
            def greedy(order: wp.array(dtype=wp.int32), indptr: wp.array(dtype=wp.int32),
                       indices: wp.array(dtype=wp.int32), colour: wp.array(dtype=wp.int32),
                       stamp: wp.array(dtype=wp.int32)):
                for k in range(order.shape[0]):
                    v = order[k]
                    for j in range(indptr[v], indptr[v + 1]):
                        cu = colour[indices[j]]
                        if cu >= 0:
                            stamp[cu] = v
                    c = int(0)
                    while stamp[c] == v:
                        c = c + 1
                    colour[v] = c

            _GREEDY_KERNEL = greedy
        n = int(S.shape[0])
        order = np.argsort(-np.diff(S.indptr), kind="stable").astype(np.int32)
        degree = int(np.diff(S.indptr).max()) if n else 0
        cpu = "cpu"
        colour = wp.array(np.full(n, -1, dtype=np.int32), dtype=wp.int32, device=cpu)
        stamp = wp.array(np.full(degree + 2, -1, dtype=np.int32), dtype=wp.int32, device=cpu)
        wp.launch(_GREEDY_KERNEL, dim=1, device=cpu,
                  inputs=[wp.array(order, dtype=wp.int32, device=cpu),
                          wp.array(S.indptr.astype(np.int32), dtype=wp.int32, device=cpu),
                          wp.array(S.indices.astype(np.int32), dtype=wp.int32, device=cpu), colour, stamp])
        wp.synchronize_device(cpu)
        COLOURING["calls"] += 1
        return colour.numpy().astype(np.int64)
    except Exception as exc:                  # noqa: BLE001 -- the Python loop gives the same colours
        COLOURING["failed"] = f"{type(exc).__name__}: {exc}"[:300]
        return None


# ================================================================= GPU setup on the device
# docs/research/2026-10-07-gpu-optimisation-roadmap.md B3.  Opt-in through ``CoupledAMGPlan(strength="pattern")``
# (M2a, one value-free plan per run), ``gpu_setup=True`` (M2b, strength graph and smoother colouring on the device) and
# ``fused_numeric=True`` (M2c, fewer block products in the numeric phase); without them the plan makes the calls it
# made before.
def _edge_hash(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """A symmetric pseudo-random number in [0, 1) per edge {a, b} (both orientations see the same value): the tie-break of
    the pairwise matching, as AmgX's ``random_weight2`` (a hash of the two indices, no state)."""
    lo, hi = torch.minimum(a, b).to(torch.float64), torch.maximum(a, b).to(torch.float64)
    x = torch.sin(lo * 12.9898 + hi * 78.233) * 43758.5453
    return x - torch.floor(x)


def _pair_once(r: torch.Tensor, c: torch.Tensor, w: torch.Tensor, n: int, rounds: int = 16) -> torch.Tensor:
    """One pass of handshake pairing on the graph ``(r, c, w)`` (no diagonal, both orientations stored): every
    unmatched node proposes to its strongest unmatched neighbour (weight, then the edge hash, then the larger index); two
    nodes that propose to each other form a pair; repeated until no proposal is mutual.  Nodes left over join the
    aggregate of their strongest matched neighbour, isolated ones stay single.  Returns the compact node -> aggregate
    map (int64, on the graph's device).  After AmgX 2.4 ``Size2Selector`` (findStrongestNeighbour / matchEdges /
    mergeWithExistingAggregates; BSD-3, NVIDIA), written with torch scatter reductions."""
    dev = r.device
    agg = torch.full((n,), -1, dtype=torch.long, device=dev)
    if r.numel() == 0:
        return torch.arange(n, device=dev)
    score = w.to(torch.float64) * (1.0 + 1e-3 * _edge_hash(r, c))
    ninf = torch.tensor(float("-inf"), dtype=torch.float64, device=dev)
    idx = torch.arange(n, device=dev)

    def strongest(mask):
        s = torch.where(mask, score, ninf)
        rowmax = torch.full((n,), float("-inf"), dtype=torch.float64, device=dev).scatter_reduce_(
            0, r, s, "amax", include_self=True)
        cand = mask & (s == rowmax[r])
        return torch.full((n,), -1, dtype=torch.long, device=dev).scatter_reduce_(
            0, r[cand], c[cand], "amax", include_self=True)

    for _ in range(int(rounds)):
        free = agg < 0
        mask = free[r] & free[c]
        if not bool(mask.any()):
            break
        best = strongest(mask)
        ok = best >= 0
        mutual = ok & free & (best[best.clamp(min=0)] == idx)
        if not bool(mutual.any()):
            break
        i = idx[mutual]
        agg[i] = torch.minimum(i, best[i])
    free = agg < 0
    if bool(free.any()):
        best = strongest(free[r] & ~free[c])
        j = idx[free & (best >= 0)]
        agg[j] = agg[best[j]]
        still = agg < 0
        agg[still] = idx[still]
    return torch.unique(agg, return_inverse=True)[1]


def aggregate_pairwise_device(r: torch.Tensor, c: torch.Tensor, w: torch.Tensor, n: int, passes: int = 3,
                              beta: float = 0.0) -> torch.Tensor:
    """Cell -> aggregate map (int64 on the device) by ``passes`` rounds of pairwise matching: pass 1 pairs cells on the
    strength graph ``(r, c, w)`` (``strength_on_device``'s COO, no diagonal), each further pass pairs the aggregates on the
    graph between them (edge weights summed), so aggregates hold about ``2**passes`` cells -- AmgX's SIZE_2 / SIZE_4 /
    SIZE_8 selectors.  Everything stays on the device: no host strength graph, no pyamg.

    ``beta`` > 0 (0 = off, bit for bit): from the second pass on, two aggregates may pair
    only along a coarse edge whose weight is at least ``beta`` x the smaller of their internal weights (the couplings
    inside an aggregate, accumulated over the passes, each pair counted once) -- the aggregates keep growing where the
    couplings are isotropic and stay pairs / lines along a strong anisotropy (the aggregate-quality idea of Napov and
    Notay's double pairwise aggregation, here as a strength ratio)."""
    n = int(n)
    total = torch.arange(n, device=r.device)
    cr, cc, cw, cn = r, c, w.to(torch.float64), n
    inner = torch.zeros(n, dtype=torch.float64, device=r.device)
    for p in range(int(passes)):
        a = _pair_once(cr, cc, cw, cn)
        total = a[total]
        m = int(a.max()) + 1 if a.numel() else 0
        if p + 1 == int(passes) or m >= cn:
            break
        ar, ac = a[cr], a[cc]
        keep = ar != ac
        if beta > 0:
            # both orientations of an edge are stored: half their sum is the coupling counted once
            inner = (torch.zeros(m, dtype=torch.float64, device=r.device).scatter_add_(0, a, inner)
                     .scatter_add_(0, ar[~keep], 0.5 * cw[~keep]))
        key = ar[keep] * m + ac[keep]
        uk, inv = torch.unique(key, return_inverse=True)
        cw = torch.zeros(uk.numel(), dtype=torch.float64, device=r.device).scatter_add_(0, inv, cw[keep])
        cr, cc, cn = uk // m, uk % m, m
        if beta > 0:
            ok = cw >= float(beta) * torch.minimum(inner[cr], inner[cc])
            cr, cc, cw = cr[ok], cc[ok], cw[ok]
    return total


def _int_hash(i: torch.Tensor) -> torch.Tensor:
    """A pseudo-random 31-bit integer per index (two rounds of a linear-congruential mix; int64 products below 2**62,
    so the CPU and every device give the same numbers): the priorities of ``mis2_device``."""
    h = ((i & 0x7FFFFFFF) * 1103515245 + 12345) & 0x7FFFFFFF
    h = ((h ^ (h >> 13)) * 1103515245 + 12345) & 0x7FFFFFFF
    return h ^ (h >> 16)


_IDX_BITS = 36                               # index field of an MIS-2 key: n < 2**36
_PRI_BITS = 20                               # priority field above it; the state above both (bits 58-59)


def _within2(r: torch.Tensor, c: torch.Tensor, value: torch.Tensor) -> torch.Tensor:
    """Per node, the largest ``value`` within graph distance 2 (itself included): two scatter-max sweeps."""
    m = value
    for _hop in range(2):
        m = m.clone().scatter_reduce_(0, r, m[c], "amax", include_self=True)
    return m


# Per fine-level size, the order in which ``aggregation="mis2o"`` picks its MIS-2
# roots -- the Jacobian's probe colouring (``jacobian.sparsity`` registers it after every build; a cell's colour class)
_ROOT_ORDER: dict = {}


def register_root_order(n: int, order) -> None:
    """Record the class of every node of an ``n``-node fine level (an int tensor or array of length ``n``) for the
    class-ordered MIS-2 roots of ``aggregation="mis2o"``; the latest registration for ``n`` wins."""
    _ROOT_ORDER[int(n)] = torch.as_tensor(order).reshape(-1).long()


def _mis2_ordered(r: torch.Tensor, c: torch.Tensor, n: int, order: torch.Tensor, base: torch.Tensor) -> torch.Tensor:
    """The MIS-2 roots picked class by class: the classes of ``order`` are visited in increasing order, and every
    undecided node of the class with no root within distance 2 becomes a root -- sequential greedy selection with one
    class per step.  When the classes are distance-2 independent sets (a Jacobian probe colouring of this pattern)
    all candidates of a class are compatible; otherwise a Luby tie-break by ``base`` inside the class keeps the set
    independent.  Maximal: every node is either a root or had one within distance 2 when its class was visited."""
    dev = r.device
    order = order.to(dev).long()
    state = torch.zeros(n, dtype=torch.long, device=dev)          # 0 undecided, 1 root, -1 out
    for k in torch.unique(order).tolist():
        in_class = order == k
        while True:
            cand = in_class & (state == 0)
            if not bool(cand.any()):
                break
            near = _within2(r, c, (state == 1).to(torch.long)) > 0
            state = torch.where(cand & near, -torch.ones_like(state), state)
            cand = cand & ~near
            if not bool(cand.any()):
                break
            pri = torch.where(cand, base, torch.full_like(base, -1))
            win = cand & (_within2(r, c, pri) == base)
            state = torch.where(win, torch.ones_like(state), state)
    return state == 1


def mis2_device(r: torch.Tensor, c: torch.Tensor, n: int, rounds: int = 200, order=None) -> torch.Tensor:
    """A distance-2 maximal independent set of the graph ``(r, c)`` (both orientations stored), as a bool mask, by
    the parallel Luby-type rule of Bell, Dalton & Olson (SIAM J. Sci. Comput. 34, 2012; CUSP): every node carries the
    key (state, random priority, index) with in > undecided > out; an undecided node whose key is the largest within
    distance 2 joins the set, one that sees a member within distance 2 leaves; repeated until nothing is undecided.
    Two scatter-max sweeps per round, everything on the graph's device.
    ``order`` (stage 3 R29): the roots picked class by class in this order instead (``_mis2_ordered``)."""
    dev = r.device
    n = int(n)
    if n >= 1 << _IDX_BITS:
        raise ValueError(f"mis2_device: {n} nodes exceed the 2**{_IDX_BITS} index field")
    idx = torch.arange(n, device=dev)
    base = ((_int_hash(idx) & ((1 << _PRI_BITS) - 1)) << _IDX_BITS) | idx
    if order is not None:
        return _mis2_ordered(r, c, n, order, base)
    state = torch.zeros(n, dtype=torch.long, device=dev)          # 0 undecided, 1 in, -1 out
    shift = _IDX_BITS + _PRI_BITS + 2
    for _ in range(int(rounds)):
        und = state == 0
        if not bool(und.any()):
            break
        key = ((state + 1) << shift) | base
        m = key
        for _hop in range(2):
            m = m.clone().scatter_reduce_(0, r, m[c], "amax", include_self=True)
        state = torch.where(und & (m == key), torch.ones_like(state), state)
        state = torch.where(und & (state == 0) & ((m >> shift) == 2), -torch.ones_like(state), state)
    if bool((state == 0).any()):
        raise RuntimeError(f"mis2_device: {int((state == 0).sum())} nodes undecided after {rounds} rounds")
    return state == 1


def _join_best(r: torch.Tensor, c: torch.Tensor, agg: torch.Tensor, base: torch.Tensor, cap: int | None = None,
               only: torch.Tensor | None = None) -> torch.Tensor:
    """One join sweep of the MIS-2 aggregation: every free node (``agg < 0``) with an assigned neighbour joins the
    aggregate of its assigned neighbour of largest priority ``base`` (``only``: the edges allowed).  ``cap``: an
    aggregate accepts joiners in priority order while its size stays below ``cap``; the rest stay free."""
    n = agg.numel()
    dev = agg.device
    assigned = agg >= 0
    mask = assigned[c] & ~assigned[r]
    if only is not None:
        mask &= only
    if not bool(mask.any()):
        return agg
    best = torch.full((n,), -1, dtype=torch.long, device=dev).scatter_reduce_(
        0, r[mask], base[c[mask]], "amax", include_self=True)
    j = torch.nonzero((~assigned) & (best >= 0)).reshape(-1)
    target = agg[best[j] & ((1 << _IDX_BITS) - 1)]
    if cap is not None and j.numel():
        size = torch.bincount(agg[assigned], minlength=int(agg.max()) + 1)
        by_pri = torch.argsort(-base[j], stable=True)
        order = by_pri[torch.argsort(target[by_pri], stable=True)]       # grouped by target, priority descending
        t = target[order]
        first = torch.ones_like(t, dtype=torch.bool)
        first[1:] = t[1:] != t[:-1]
        k = torch.arange(t.numel(), device=dev)
        pos = k - torch.cummax(torch.where(first, k, torch.zeros_like(k)), 0).values
        keep = size[t] + pos < int(cap)
        j, target = j[order[keep]], t[keep]
    agg = agg.clone()
    agg[j] = target
    return agg


def aggregate_mis2_device(r: torch.Tensor, c: torch.Tensor, n: int, cap: int | None = None, order=None) -> torch.Tensor:
    """Cell -> aggregate map (int64 on the device) by MIS-2 aggregation (Bell, Dalton & Olson 2012, the GPU form of
    standard aggregation): the roots are a distance-2 maximal independent set of the strength graph (``mis2_device``);
    every neighbour of a root joins it (the root of largest priority), then every node still free joins the aggregate
    of its assigned neighbour of largest priority.  By maximality every node is within distance 2 of a root, so two
    sweeps cover the graph (an isolated node is its own root).  pyamg's standard aggregation picks its roots by the
    same distance-2 rule, sequentially; this one picks them in parallel, so the aggregates are of the same kind
    (compact, a root and its neighbourhood) but not the same map.  No host stage.

    ``cap`` (aggregation ``"mis2c<cap>"``): random-priority roots are sparser than
    pyamg's sequential ones (aggregates of 47 cells against 35 on the 104^3 block pattern, a weaker coarse level on
    diffusion-dominated states), so the second sweep fills an aggregate only up to ``cap`` cells; the cells it leaves
    free get roots of their own -- an MIS-2 of the free cells' subgraph, each new root taking its free neighbours --
    and whatever is still free then joins its best assigned neighbour without a cap.  None: the default map.

    ``order`` (stage 3 R29; aggregation ``"mis2o"``): the roots are picked class by class in this order
    (``_mis2_ordered``; the Jacobian's lattice probe colouring gives the regular root lattice of pyamg's sequential
    pass); the joins as above."""
    dev = r.device
    n = int(n)
    root = mis2_device(r, c, n, order=order)
    agg = torch.full((n,), -1, dtype=torch.long, device=dev)
    agg[root] = torch.cumsum(root.to(torch.long), 0)[root] - 1
    idx = torch.arange(n, device=dev)
    base = ((_int_hash(idx) & ((1 << _PRI_BITS) - 1)) << _IDX_BITS) | idx
    agg = _join_best(r, c, agg, base)                        # the roots' neighbours
    agg = _join_best(r, c, agg, base, cap=cap)               # distance 2 (up to cap cells per aggregate)
    if cap is not None and bool((agg < 0).any()):
        free = agg < 0
        sub = free[r] & free[c]
        linked = torch.zeros(n, dtype=torch.bool, device=dev)
        linked[r[sub]] = True                                # free cells with a free neighbour
        roots2 = mis2_device(r[sub], c[sub], n) & linked
        agg[roots2] = int(agg.max()) + 1 + torch.cumsum(roots2.to(torch.long), 0)[roots2] - 1
        only = free[r] & roots2[c]
        agg = _join_best(r, c, agg, base, only=only)         # the new roots' free neighbours
        agg = _join_best(r, c, agg, base)                    # the rest: the best assigned neighbour, no cap
    left = agg < 0
    if bool(left.any()):                    # not reachable on a symmetric graph; kept as singletons
        agg[left] = int(agg.max()) + 1 + torch.arange(int(left.sum()), device=dev)
    return agg


def strength_on_device(row: torch.Tensor, col: torch.Tensor, n: int, val: torch.Tensor | None = None,
                       theta: float = 0.0):
    """``(r, c, s)``: the symmetric strength graph of ``block_strength`` on the device of ``row`` (M2b), as sorted
    COO arrays (row-major, no diagonal).

    ``val`` given: the same graph as ``block_strength(BlockCSR(row, col, val), theta)`` -- the same entries and the same
    values bit for bit: the block Frobenius norms are those ``block_strength`` copies to the host (computed by the
    same torch call), and ``|A_ij| / sqrt(|A_ii| |A_jj|)``, the max with the transposed entry and the ``theta`` row test
    are single IEEE operations, rounded alike on the host and the device.  An entry is kept iff
    ``max(s_ij, s_ji) != 0`` (scipy's ``maximum`` stores no zero result), and with ``theta > 0`` iff it is at least
    ``theta`` times its row's largest entry.  ``val`` None: the STRUCTURAL strength of M2a -- every stored
    off-diagonal block (or its transpose) is a connection of strength 1, whatever its values.  Assumes unique blocks
    (a BlockCSR); a pattern sorted row-major and structurally symmetric (every FV Jacobian here) takes the
    searchsorted path, any other the union path, with the same result."""
    device = row.device
    n = int(n)
    off = row != col
    if val is None:
        s = torch.ones(row.numel(), dtype=torch.float64, device=device)
    else:
        norms = torch.linalg.matrix_norm(val.detach())
        diag = torch.zeros(n, dtype=norms.dtype, device=device)
        on = ~off
        diag[row[on]] = norms[on]
        fill = norms.max() if norms.numel() else torch.ones((), dtype=norms.dtype, device=device)
        diag = torch.where(diag > 0, diag, fill)
        s = norms / torch.sqrt(diag[row] * diag[col])
    key = row * n + col
    tkey = col * n + row
    symmetric = False
    if key.numel() > 1 and bool((key[1:] > key[:-1]).all()):
        pos = torch.searchsorted(key, tkey).clamp_(max=key.numel() - 1)
        symmetric = bool(torch.equal(key[pos], tkey))
    if symmetric:
        r, c, sv = row[off], col[off], torch.maximum(s, s[pos])[off]
        del pos
    else:
        k2 = torch.cat([key[off], tkey[off]])
        v2 = torch.cat([s[off], s[off]])
        uk, inv = torch.unique(k2, return_inverse=True)
        sv = torch.zeros(uk.numel(), dtype=s.dtype, device=device).scatter_reduce_(0, inv, v2, "amax",
                                                                                  include_self=True)
        r, c = uk // n, uk % n
        del k2, v2, uk, inv
    keep = sv != 0
    if theta > 0 and bool(keep.any()):
        rmax = torch.zeros(n, dtype=sv.dtype, device=device).scatter_reduce_(0, r[keep], sv[keep], "amax",
                                                                             include_self=True)
        keep &= sv >= theta * rmax[r]
    if not bool(keep.all()):
        r, c, sv = r[keep], c[keep], sv[keep]
    return r, c, sv


def component_strength(row: torch.Tensor, col: torch.Tensor, n: int, val: torch.Tensor, weights, frob: float = 0.0,
                       floor: float = 1e-12):
    """``(r, c, s)`` on the edges of ``strength_on_device(row, col, n, val)`` with the weights of a coarsening RULE
    ``s = frob * s_F + sum_f weights[f] * 0.5 (|a_ij^ff| + |a_ji^ff|) / max(|a_ii^ff|, |a_jj^ff|)``,
    where ``s_F`` is the block-norm strength of ``strength_on_device`` and the per-field term is AmgX's Size2Selector
    edge weight of one component (``computeEdgeWeightsBlockDiaCsr_V2``).  ``weights[f] = 1`` for one field and
    ``frob = 0`` is AmgX's rule on that field; ``frob = 1`` and no weights is the default value strength.  Every edge
    keeps a positive weight (at least ``floor`` times the largest), so the graph -- which the smoother colouring also
    reads -- is the full off-diagonal pattern."""
    n = int(n)
    r, c, sF = strength_on_device(row, col, n, val, 0.0)
    s = float(frob) * sF
    w = [float(x) for x in (weights or [])]
    if any(w):
        key = row * n + col
        order = None
        if key.numel() > 1 and not bool((key[1:] > key[:-1]).all()):
            order = torch.argsort(key)
            key = key[order]

        def find(i, j):
            t = i * n + j
            p = torch.searchsorted(key, t).clamp_(max=key.numel() - 1)
            hit = key[p] == t
            return (p if order is None else order[p]), hit

        pij, hij = find(r, c)
        pji, hji = find(c, r)
        di, hi = find(r, r)
        dj, hj = find(c, c)
        a = val.detach().abs()
        for f, wf in enumerate(w):
            if not wf:
                continue
            aij = torch.where(hij, a[pij, f, f], torch.zeros((), dtype=a.dtype, device=a.device))
            aji = torch.where(hji, a[pji, f, f], torch.zeros((), dtype=a.dtype, device=a.device))
            dd = torch.maximum(torch.where(hi, a[di, f, f], torch.ones((), dtype=a.dtype, device=a.device)),
                               torch.where(hj, a[dj, f, f], torch.ones((), dtype=a.dtype, device=a.device)))
            dd = torch.where(dd > 0, dd, torch.ones((), dtype=a.dtype, device=a.device))
            s = s + wf * (0.5 * (aij + aji) / dd).to(s.dtype)
    if s.numel():
        smax = s.max()
        s = torch.where(s > 0, s, floor * (smax if bool(smax > 0) else torch.ones((), dtype=s.dtype, device=s.device)))
    return r, c, s


def _jacobi_rho(A: "BlockCSR", Sinv: torch.Tensor, omega: float, iters: int = 10) -> float:
    """Power-iteration estimate of the spectral radius of damped block Jacobi
    ``I - omega Sinv A`` on one level (fixed seed, ``iters`` steps; a non-finite growth reads as infinity)."""
    if Sinv is None or A.shape[0] < 2:
        return 0.0
    gen = torch.Generator(device=A.val.device).manual_seed(7)
    e = torch.randn(A.shape[0], A.F, dtype=A.val.dtype, device=A.val.device, generator=gen)
    e = e / e.norm()
    rho = 0.0
    with torch.no_grad():
        for _ in range(int(iters)):
            y = e - omega * torch.bmm(Sinv, A.matvec(e).unsqueeze(-1)).squeeze(-1)
            nrm = y.norm()
            if not bool(torch.isfinite(nrm)):
                return float("inf")
            rho = float(nrm)
            if rho == 0.0:
                break
            e = y / nrm
    return rho


def _galerkin_target(row: torch.Tensor, col: torch.Tensor, agg: torch.Tensor, n_c: int, crow: torch.Tensor,
                     ccol: torch.Tensor) -> torch.Tensor:
    """For every fine block (row, col), the position of (agg[row], agg[col]) in the coarse
    pattern (crow, ccol) -- the Galerkin pattern of an unsmoothed aggregation, which holds every such pair."""
    agg = agg.to(row.device)
    key = agg[row] * int(n_c) + agg[col]
    ckey = crow.to(row.device) * int(n_c) + ccol.to(row.device)
    order = torch.argsort(ckey)
    sk = ckey[order]
    pos = torch.searchsorted(sk, key).clamp_(max=max(sk.numel() - 1, 0))
    if not bool((sk[pos] == key).all()):
        raise RuntimeError("unsmoothed Galerkin pattern misses a coarse pair of the aggregation")
    return order[pos]


def strength_to_host(r: torch.Tensor, c: torch.Tensor, s: torch.Tensor, n: int) -> sp.csr_matrix:
    """The host CSR of ``strength_on_device``'s graph (int32 indices where they fit, as scipy's own), for pyamg."""
    n = int(n)
    idt = np.int32 if max(n, int(r.numel())) < 2 ** 31 - 1 else np.int64
    indptr = np.zeros(n + 1, dtype=idt)
    indptr[1:] = np.cumsum(torch.bincount(r, minlength=n).cpu().numpy())
    return sp.csr_matrix((s.cpu().numpy(), c.cpu().numpy().astype(idt), indptr), shape=(n, n))


def jones_plassmann_colouring(r: torch.Tensor, c: torch.Tensor, n: int, *, seed: int = 0) -> torch.Tensor:
    """A distance-1 colouring of the symmetric graph ``(r, c)`` (no diagonal) on its device.

    Jones-Plassmann with largest-degree-first priorities (degree, then a seeded random permutation, so every priority
    is distinct): each round colours, at once, every uncoloured vertex whose priority exceeds that of all its
    uncoloured neighbours -- an independent set -- with the smallest colour none of its coloured neighbours has.  The
    result is a valid colouring (adjacent vertices differ; ``colouring_conflicts``) and depends only on the graph and
    ``seed`` (the priorities come from a CPU generator; the rounds use no atomics whose order could matter), so the
    CPU and CUDA give the same colours.  It is not ``greedy_colouring``'s colouring: the colour count and the GS order
    differ (documented, not bit for bit).  Returns int64 colours on the graph's device."""
    device = r.device
    n = int(n)
    colour = torch.full((n,), -1, dtype=torch.long, device=device)
    if n == 0:
        return colour
    if r.numel() > 1 and not bool(((r[1:] > r[:-1]) | ((r[1:] == r[:-1]) & (c[1:] > c[:-1]))).all()):
        order = torch.argsort(r * n + c)
        r, c = r[order], c[order]
    deg = torch.bincount(r, minlength=n)
    ptr = torch.zeros(n + 1, dtype=torch.long, device=device)
    ptr[1:] = torch.cumsum(deg, 0)
    perm = torch.randperm(n, generator=torch.Generator(device="cpu").manual_seed(int(seed))).to(device)
    prio = deg * n + perm                                   # distinct: largest degree first, random tie-break
    ra, ca = r, c                                           # edges between uncoloured vertices
    top = 0                                                 # colours in use so far
    while True:
        unc = colour < 0
        if not bool(unc.any()):
            break
        live = unc[ra] & unc[ca]
        ra, ca = ra[live], ca[live]
        best = torch.full((n,), -1, dtype=torch.long, device=device).scatter_reduce_(0, ra, prio[ca], "amax",
                                                                                    include_self=True)
        sel = torch.nonzero(unc & (prio > best)).reshape(-1)
        cnt = deg[sel]
        m = int(cnt.sum())
        width = top + 1
        used = torch.zeros(sel.numel(), width + 1, dtype=torch.bool, device=device)
        if m:
            owner = torch.repeat_interleave(torch.arange(sel.numel(), device=device), cnt, output_size=m)
            start = torch.repeat_interleave(ptr[sel], cnt, output_size=m)
            within = torch.arange(m, device=device) - torch.repeat_interleave(torch.cumsum(cnt, 0) - cnt, cnt,
                                                                              output_size=m)
            nc = colour[c[start + within]]
            hit = nc >= 0
            used[owner[hit], nc[hit]] = True
        first = torch.argmin(used.to(torch.uint8), dim=1)   # the first False: smallest free colour
        colour[sel] = first
        top = max(top, int(first.max()) + 1 if first.numel() else top)
    return colour


def colouring_conflicts(row: torch.Tensor, col: torch.Tensor, colour: torch.Tensor) -> int:
    """Off-diagonal blocks ``(i, j)`` with ``colour[i] == colour[j]`` (0 for a valid colouring of the pattern)."""
    colour = torch.as_tensor(colour, device=row.device)
    return int(((colour[row] == colour[col]) & (row != col)).sum())


def spectral_radius_estimate(apply, n: int, F: int, *, iters: int = 20, dtype=torch.float64,
                             device="cpu", seed: int = 0, device_norm: bool = False) -> float:
    g = torch.Generator(device="cpu").manual_seed(seed)
    v = torch.randn(n, F, generator=g, dtype=dtype).to(device)
    v = v / v.norm()
    if device_norm:
        # W6 (opt-in, ``WARP_NUMERIC["omega_device"]``): the same iteration with the norms kept on the device and ONE
        # read-back at the end (the host loop reads every norm); a zero / non-finite norm freezes the iterate and the
        # value as the host loop's ``break`` does.  Bit for bit the host loop's result.
        lam = torch.zeros((), dtype=dtype, device=v.device)
        stopped = torch.zeros((), dtype=torch.bool, device=v.device)
        for _ in range(iters):
            w = apply(v).detach()
            nrm = w.norm()                                   # the host loop's ``w.norm()``
            bad = (nrm == 0) | ~torch.isfinite(nrm)
            lam = torch.where(stopped, lam, nrm)
            stopped = stopped | bad
            # ``w / lam`` with a host float divides by multiplying with its reciprocal (torch's CUDA division by a
            # CPU scalar): the same product here, with the reciprocal formed on the device
            v = torch.where(stopped, v, w * (1.0 / nrm))
        return float(lam)
    lam = 0.0
    for _ in range(iters):
        w = apply(v)
        lam = float(w.detach().norm())
        if lam == 0 or not math.isfinite(lam):
            break
        v = (w / lam).detach()
    return lam


def _safe_block_inverse(D: torch.Tensor) -> torch.Tensor:
    """Batched ``F x F`` inverse; singular or badly conditioned blocks fall back to pinv."""
    inv = torch.linalg.inv_ex(D)
    out = inv.inverse
    bad = (inv.info != 0) | ~torch.isfinite(out).all(dim=(1, 2))
    if bool(bad.any()):
        out = out.clone()
        out[bad] = torch.linalg.pinv(D[bad])
    return out


# ===================================================================== equilibration
def ruiz_blockcsr(A: BlockCSR, sweeps: int = 5):
    """Ruiz infinity-norm equilibration of the scalar matrix behind a BlockCSR.

    Returns ``(r, c, A_hat)`` with ``r, c`` of shape ``[n, F]`` and ``A_hat = diag(r) A diag(c)``
    (every scalar row and column of ``A_hat`` has max-abs ~1).  RANS Jacobians mix omega ~ 1e7 at
    walls with k ~ 1e-4; without this the point-block inverses of the coupled AMG are useless."""
    val = A.val
    n, F = A.shape[0], A.F
    r = torch.ones(n, F, dtype=val.dtype, device=val.device)
    c = torch.ones(n, F, dtype=val.dtype, device=val.device)
    for _ in range(sweeps):
        V = (r[A.row][:, :, None] * val * c[A.col][:, None, :]).abs()
        rmax = torch.zeros(n, F, dtype=val.dtype, device=val.device).index_reduce(
            0, A.row, V.amax(dim=2), "amax", include_self=True)
        cmax = torch.zeros(n, F, dtype=val.dtype, device=val.device).index_reduce(
            0, A.col, V.amax(dim=1), "amax", include_self=True)
        r = r / torch.sqrt(torch.where(rmax > 0, rmax, torch.ones_like(rmax)))
        c = c / torch.sqrt(torch.where(cmax > 0, cmax, torch.ones_like(cmax)))
    A_hat = A.with_values(r[A.row][:, :, None] * val * c[A.col][:, None, :])
    return r, c, A_hat


class ScaledCoupledAMG:
    """``M = diag(c) AMG(diag(r) A diag(c)) diag(r)``: coupled AMG built on the Ruiz-equilibrated
    matrix, applied in the original variables (a fixed linear operator).  Same call interface as
    ``CoupledAMG``."""

    def __init__(self, A: BlockCSR, *, ruiz_sweeps: int = 5, gauge=None, **kw):
        self.r, self.c, A_hat = ruiz_blockcsr(A, ruiz_sweeps)
        self.inner = CoupledAMG(A_hat, gauge=None, **kw)
        self.F, self.gauge = A.F, gauge

    def apply_blocks(self, b):
        return self.c * self.inner.apply_blocks(self.r * b)

    def __call__(self, v):
        n = self.r.shape[0]
        z = self.apply_blocks(v.reshape(self.F, n).t()).t().reshape(-1)
        return z if self.gauge is None else self.gauge(z)

    def report(self):
        rep = self.inner.report()
        rep["ruiz"] = True
        return rep


class _FreshEscalation:
    """The current-matrix escalation of a kept preconditioner's fallback arm.

    Under a preconditioner policy that keeps the preconditioner across Newton steps (``jfnk.PrecondPolicy``, e.g.
    ``rung+growth:2``) the lazily built classical / rescue arms of ``BudgetedLearned`` / ``StagnationRescue`` are built
    from the matrix of the step that BUILT the preconditioner.  On a hard step that matrix can be the wrong one: on the
    147k-cell cylinder at Re 40 the l1 rescue built on Newton step 1's matrix needs 1,230 GMRES iterations for step 2's
    loose solve, where the l1 cycle on step 2's own matrix needs 43 (rebuilt at every step).
    So: when the Newton loop hands the step's own assembled block matrix (``set_current_blocks``) and the fallback arm
    has run a whole GMRES cycle since the switch without the residual falling by ``factor``, the solve continues with
    ``fresh_of(blocks)`` -- the l1 cycle built on that matrix -- once per solve.  Without ``fresh_of`` (the default),
    without current blocks (a rebuild step, a JVP operator) or when the arm progresses, nothing changes, bit for bit."""

    def _init_fresh(self, fresh_of):
        self.fresh_of, self.current_blocks, self._fresh, self._r_switch = fresh_of, None, None, None

    def set_current_blocks(self, blocks) -> None:
        """The Newton loop's hand-over of this step's assembled blocks (``None`` after the solve: drops the arm)."""
        self.current_blocks = blocks
        if blocks is None:
            self._fresh = None

    def _escalate(self, residual, current, factor: float, loose: bool):
        """The fresh arm when ``current`` (a fallback arm, not ``self``) stalled over its last cycle, else None."""
        if (self.fresh_of is None or self.current_blocks is None or current is self or not loose
                or self._r_switch is None or self.log[-1].get("fresh")):
            return None
        if float(residual.norm()) <= factor * self._r_switch:
            return None
        if self._fresh is None:
            self._fresh = self.fresh_of(self.current_blocks)
        self.log[-1]["fresh"] = True
        self._r_switch = float(residual.norm())
        return self._fresh


def l1_rescue_measurement(amg, A: BlockCSR, gauge_field) -> dict:
    """The measured rescue's reading on system ``A``: the reduction of the l1 rule's reference cycle on ``amg``'s
    plan as it stands (``plan``) and with l1 on every aggregated level (``l1``), each ``L1_SELECT_STEPS`` GMRES steps
    from the random vector of ``l1_select_seed`` (``CoupledAMG._reference_reduction``, the rule's own reading);
    ``switch`` when l1 reduces more (a non-finite reading never wins; a plan already l1 everywhere has nothing to
    switch to).  The plan's levels are restored, and the plan as it stands is measured last, so its prolongation
    weights end refreshed for its own diagonals."""
    plan = amg.plan
    agg = [k for k, L in enumerate(plan.levels) if L.agg is not None]
    before = {k: bool(getattr(plan.levels[k], "l1", False)) for k in agg}
    seed, began = l1_select_seed(A), time.perf_counter()
    memory_note("reading start", blocks=int(A.row.numel()))
    sliced = MATVEC_CHUNK["blocks"]
    if LEAN_READING["on"]:
        # this reading's products only (not the build-time l1 selection, which also calls _reference_reduction)
        MATVEC_CHUNK["blocks"] = int(LEAN_READING["blocks"])
    try:
        try:
            for k in agg:
                plan.levels[k].l1 = True
            red_l1 = float(amg._reference_reduction(A, gauge_field, seed))
        finally:
            for k in agg:
                plan.levels[k].l1 = before[k]
        red_plan = float(amg._reference_reduction(A, gauge_field, seed))
    finally:
        MATVEC_CHUNK["blocks"] = sliced
    memory_note("reading end", plan=red_plan, l1=red_l1)
    switch = (not all(before.values()) and not math.isnan(red_l1)
              and (math.isnan(red_plan) or red_l1 < red_plan))
    return {"plan": red_plan, "l1": red_l1, "switch": bool(switch), "seed": seed,
            "seconds": round(time.perf_counter() - began, 4)}


# Opt-in (``--camg-rescue-escalate R``): the measured rescue's escalation on Newton stagnation.  The steady entry's
# progress hook sets ``on`` for the rest of a Newton call (one continuation rung) once a Newton step lowers ||r||_2
# by less than the fraction R; from then on the measured rescue neither refuses a stalled solve on its reading nor
# hands it back after the rescue's first cycle, so every stalled solve is rescued to its budget, as the plain rescue
# does.  Off (``on`` False) the measured rescue is unchanged.
# The switch is process-wide: the steady entry clears it at step 0 of every Newton call and after its solve; any other
# in-process caller that drives the measured rescue clears it itself (``RESCUE_ESCALATION["on"] = False``).
RESCUE_ESCALATION = {"on": False}
# Opt-in (``--camg-rescue-hand-back-once``): the measured rescue hands back at most once per Newton call.  The first
# hand-back of a call sets RESCUE_ESCALATION, so every later stalled solve of the call is rescued to its budget; the
# steady entry clears it at step 0 of every call, as for the escalation.
HAND_BACK_ONCE = {"on": False}


def escalation_step(state: dict, it: int, l2: float, fraction: float):
    """``--camg-rescue-escalate``: RESCUE_ESCALATION after one Newton step (``state`` keeps the previous ||r||_2).  Step 0
    of a Newton call clears it; a later step that lowers ||r||_2 by less than ``fraction`` sets it for the rest of the
    call.  Returns that step's ratio when it set it, else None."""
    fired = None
    if int(it) == 0:
        RESCUE_ESCALATION["on"] = False
    elif not RESCUE_ESCALATION["on"] and state.get("l2") and float(l2) > (1.0 - fraction) * state["l2"]:
        RESCUE_ESCALATION["on"] = True
        fired = float(l2) / state["l2"]
    state["l2"] = float(l2)
    return fired


class _MeasuredRescue:
    """The measured rescue of a cycle with an l1 rescue (opt-in; absent = the rescue as before): ``measure`` (a
    zero-argument callable returning ``l1_rescue_measurement``'s reading of the build's matrix, taken once per build
    at its first trigger) decides whether a stalled solve switches to the rescue -- only when l1 reduces more than the
    plan that runs -- and ``hand_back`` returns the solve from the rescue to the cycle it left (the classical cycle
    of a learned arm) when the rescue's first GMRES cycle does not lower the residual by the rescue factor; a solve
    handed back is not rescued again."""

    def _init_measured(self, measure=None, hand_back: bool = False):
        self.measure, self.hand_back, self._measured = measure, bool(hand_back), None
        self._handed = self._checked = False

    def _new_solve(self):
        """Per solve: no hand-back yet, the rescue's first cycle not yet checked."""
        self._handed = self._checked = False

    def _measured_refuses(self) -> bool:
        """True when the build's reading keeps the plan that runs (recorded in the solve's log entry)."""
        if getattr(self, "measure", None) is None:
            return False
        if RESCUE_ESCALATION["on"]:                    # escalated: no reading, the stalled solve is rescued
            self.log[-1]["escalated"] = True
            return False
        if self._measured is None:
            self._measured = dict(self.measure())
        self.log[-1]["measured"] = {k: self._measured.get(k) for k in ("plan", "l1", "switch")}
        if not self._measured.get("switch"):
            self.log[-1]["refused"] = True
            return True
        return False

    def _hand_back_due(self, residual, current, factor: float) -> bool:
        """True at the boundary after the rescue's first cycle when it did not lower the residual by ``factor``."""
        if (not getattr(self, "hand_back", False) or self._handed or self._checked or self._r_switch is None
                or current is None or current is not self._rescue) or RESCUE_ESCALATION["on"]:
            return False
        self._checked = True
        if float(residual.norm()) > factor * self._r_switch:
            self._handed = True
            self.log[-1]["handed_back"] = True
            if HAND_BACK_ONCE["on"]:
                RESCUE_ESCALATION["on"] = True       # the call's only hand-back: later stalls are rescued
                self.log[-1]["escalated_by_hand_back"] = True
            return True
        return False


class BudgetedLearned(_FreshEscalation, _MeasuredRescue):
    """Learned cycle first, classical cycle after a budget: the cost safeguard of the learned smoother
    cycle.  Acts as the learned preconditioner and carries a restart-boundary ``cycle_policy``
    (honoured by ``SparseSolver.gmres`` through ``CoupledNewton.newton``): GMRES cycle 0 runs with the
    learned cycle for at most ``budget`` iterations; any later cycle uses the classical one (gmres folds
    the learned cycle's progress into its guess first).  So a linear solve costs at most the classical
    solve from a better start plus ``budget`` learned iterations, whatever the network does; a learned
    cycle that converges within the budget never pays the classical one.  ``log`` collects one
    entry per solve: whether the hand-over happened.  ``classical`` may be a zero-argument callable: the
    classical cycle is then built LAZILY, at the first hand-over (no build cost when the learned cycle
    converges within its budget; the race-free mode of the camg entry)."""

    def __init__(self, learned, classical, budget: int, log: list | None = None, rescue=None,
                 rescue_factor: float = 0.1, rescue_min_rtol: float | None = None, continue_learned: bool = False,
                 fresh_of=None, late_stagnation: bool = False, measure=None, hand_back: bool = False):
        self.learned, self._classical, self.budget = learned, classical, int(budget)
        # measure / hand_back (opt-in, _MeasuredRescue): the rescue only where l1 reduces more than the plan that runs,
        # and back to the classical cycle when the rescue's first cycle misses the rescue factor
        self._init_measured(measure, hand_back)
        # late_stagnation (with continue_learned; off by default, the rule above unchanged): after the budget test the
        # learned cycle keeps the solve only while each of its later GMRES cycles lowers the residual by
        # ``rescue_factor`` from that cycle's start; a cycle that does not hands the solve to the classical cycle at
        # that restart boundary (lazy, from the current iterate), whatever the solve's tolerance
        self.late_stagnation, self._r_cycle = bool(late_stagnation), None
        self._init_fresh(fresh_of)          # _FreshEscalation: off unless fresh_of is given
        self.log = log if log is not None else []
        # stagnation rescue: if the learned cycle reduced the residual by less than
        # ``rescue_factor`` within its budget, hand over to ``rescue`` (e.g. the l1-smoothed classical cycle,
        # lazy like ``classical``) instead of the classical cycle -- see StagnationRescue
        self._rescue, self.rescue_factor, self._r0 = rescue, float(rescue_factor), None
        # only solves asked for a relative tolerance >= rescue_min_rtol may be rescued (None: any); the Newton
        # loop sets ``target_rtol`` per solve (CoupledNewton.newton).  A late, tight solve starts slowly on the
        # slow modes and is NOT a stagnation (cylinder Re20: rescued tight solves take 2.5x the its)
        self.rescue_min_rtol, self.target_rtol = rescue_min_rtol, None
        # continue_learned: a learned cycle that reduced the residual by at least
        # ``rescue_factor`` within its budget keeps the solve (full-length cycles) instead of handing it over
        self.continue_learned = bool(continue_learned)

    @property
    def classical(self):
        if callable(self._classical) and not hasattr(self._classical, "apply_blocks"):
            self._classical = self._classical()          # lazy build at the first hand-over
        return self._classical

    @property
    def rescue(self):
        if callable(self._rescue) and not hasattr(self._rescue, "apply_blocks"):
            self._rescue = self._rescue()
        return self._rescue

    def __call__(self, v):
        return self.learned(v)

    def apply_blocks(self, b):
        return self.learned.apply_blocks(b)

    def cycle_policy(self, cycle, residual, current):
        if cycle == 0:
            self._r0, self._r_switch, self._r_cycle = float(residual.norm()), None, None
            self._new_solve()
            self.log.append({"handover": False})
            return (self, self.budget)
        loose = (self.rescue_min_rtol is None or self.target_rtol is None
                 or self.target_rtol >= self.rescue_min_rtol)
        if self._hand_back_due(residual, current, self.rescue_factor):
            return self.classical                        # the measured rescue's hand-back: the classical cycle
        if current is self:
            r = float(residual.norm())
            stalled = r > self.rescue_factor * self._r0
            if self.continue_learned and not stalled:
                if not (self.late_stagnation and self._r_cycle is not None and r > self.rescue_factor * self._r_cycle):
                    self._r_cycle = r
                    return self
                # a later cycle of the learned arm that did not lower the residual by the factor
                self.log[-1].update(handover=True, late=True, at_cycle=int(cycle))
                self._r_switch = r
                return self.classical
            self.log[-1]["handover"] = True
            self._r_switch = float(residual.norm())
            if self._rescue is not None and loose and stalled and not self._measured_refuses():
                self.log[-1]["rescue"] = True
                return self.rescue
            return self.classical
        fresh = self._escalate(residual, current, self.rescue_factor, loose)
        return current if fresh is None else fresh

    def report(self):
        rep = self.learned.report()
        rep["budget"] = self.budget
        return rep


class StagnationRescue(_FreshEscalation, _MeasuredRescue):
    """A cycle with a RESCUE cycle for linear solves on which it stagnates.

    GMRES cycle 0 runs the primary cycle for at most ``budget`` iterations; at every restart boundary after
    that, if the residual is still above ``factor`` x the initial one, the solve continues with ``rescue``
    (a zero-argument callable is built lazily, at the first rescue), otherwise with the primary in full-length
    cycles.  Measured motivation: on the cylinder 589k Newton step 2 the GS 2+2 cycle is numerically unstable
    (1600 GMRES its, the budget cap, for a 1e-1 solve) while its l1-smoothed version reaches 1e-2 in 15 its;
    on the easy states the l1 cycle needs ~2x the iterations at tight tolerances -- hence a switch on
    stagnation instead of l1 everywhere.  ``log`` gets one entry per solve.

    Two opt-in variants (``experiments/NaturalConvection/CoupledNewton/RobustLinear/README.md``), both off by default (the
    rule above, unchanged, bit for bit):

    * ``rule="rate"``: a solve above ``factor`` x its start after the probe is rescued only if the primary's OBSERVED
      probe rate cannot reach the solve's tolerance within the iterations left after the probe (``left``): with
      ``rho = (r / r0) ** (1 / budget)`` the primary needs ``log(rtol r0 / r) / log(rho)`` more iterations.  A
      primary that makes no progress in the probe is rescued.
    * ``maxit=K``: a rescued solve gets at most ``K`` iterations of the rescue cycle after the probe and then ENDS
      (the GMRES answer so far, reported unconverged; ``K = 0`` ends it at the probe).  Measured reason: after the
      l1 -> production hand-over of the learned starts at Ra 2e7 (InitialGuess 12.30 / 12.31) the rescued solves run
      the l1 cycle into the full GMRES budget (1,190 iterations at 80^3) while their steps are taken at lambda 1 with
      the solve stalled at rel 0.05-0.4; the cap bounds what such a solve costs."""

    def __init__(self, primary, rescue, budget: int, factor: float = 0.1, log: list | None = None,
                 min_rtol: float | None = None, rule: str = "factor", left: int | None = None,
                 maxit: int | None = None, fresh_of=None, measure=None, hand_back: bool = False):
        self.primary, self._rescue, self.budget, self.factor = primary, rescue, int(budget), float(factor)
        self._init_measured(measure, hand_back)     # opt-in: the measured rescue (_MeasuredRescue)
        self._init_fresh(fresh_of)          # _FreshEscalation: off unless fresh_of is given
        self.log = log if log is not None else []
        self._r0 = None
        # as BudgetedLearned.rescue_min_rtol: rescue only solves asked for rtol >= min_rtol (set per solve by
        # the Newton loop through ``target_rtol``); None = any solve
        self.min_rtol, self.target_rtol = min_rtol, None
        if rule not in ("factor", "rate"):
            raise ValueError(f"StagnationRescue rule must be 'factor' or 'rate', not {rule!r}")
        if rule == "rate" and (left is None or int(left) < 1):
            raise ValueError("StagnationRescue rule 'rate' needs left >= 1 (the GMRES iterations after the probe)")
        if maxit is not None and int(maxit) < 0:
            raise ValueError(f"StagnationRescue maxit must be >= 0 (or None = no cap), not {maxit}")
        self.rule, self.left = rule, (None if left is None else int(left))
        self.maxit = None if maxit is None else int(maxit)
        self._capped = False

    def _rate_keeps_primary(self, r: float) -> tuple[bool, dict]:
        """``rule="rate"``: True when the primary's probe rate reaches ``target_rtol`` within ``left`` iterations."""
        rtol = self.target_rtol
        if rtol is None or not (self._r0 and self._r0 > 0.0) or not (r > 0.0):
            return False, {"rho": None, "needed": None}
        rho = (r / self._r0) ** (1.0 / max(self.budget, 1))
        if not (rho < 1.0):
            return False, {"rho": rho, "needed": None}
        need = math.log(rtol * self._r0 / r) / math.log(rho)
        return need <= self.left, {"rho": rho, "needed": need}

    @property
    def rescue(self):
        if callable(self._rescue) and not hasattr(self._rescue, "apply_blocks"):
            self._rescue = self._rescue()
        return self._rescue

    def __call__(self, v):
        return self.primary(v)

    def apply_blocks(self, b):
        return self.primary.apply_blocks(b)

    def cycle_policy(self, cycle, residual, current):
        if cycle == 0:
            self._r0, self._r_switch = float(residual.norm()), None
            self._capped = False
            self._new_solve()
            self.log.append({"rescue": False})
            return (self, self.budget)
        if self._capped:                                 # maxit: the capped rescue cycle has run
            self.log[-1]["stopped"] = True
            return "stop"                                # SparseSolver.gmres.STOP
        if self._hand_back_due(residual, current, self.factor):
            memory_note("hand back")
            return self                                  # the measured rescue's hand-back: the primary to the end
        loose = self.min_rtol is None or self.target_rtol is None or self.target_rtol >= self.min_rtol
        if current is self and loose and float(residual.norm()) > self.factor * self._r0:
            if self.rule == "rate":
                keep, info = self._rate_keeps_primary(float(residual.norm()))
                self.log[-1].update(rate=info["rho"], needed=info["needed"], left=self.left)
                if keep:
                    self.log[-1]["kept_by_rate"] = True
                    return current
            if self._handed or self._measured_refuses():
                return current                           # handed back, or the reading keeps the primary's plan
            self.log[-1]["rescue"] = True
            self._r_switch = float(residual.norm())
            memory_note("rescue switch")
            if self.maxit is not None:
                if self.maxit == 0:
                    self.log[-1]["stopped"] = True
                    return "stop"
                self._capped = True
                return (self.rescue, self.maxit)
            return self.rescue
        fresh = self._escalate(residual, current, self.factor, loose)
        return current if fresh is None else fresh

    def lift_maxit(self) -> bool:
        """II-4 job 5 (``--forcing-cap-fallback``): from the next solve on, a rescued solve runs the rescue cycle to the
        GMRES budget again (``maxit = None``, the production rule).  True when a cap was lifted."""
        lifted, self.maxit, self._capped = self.maxit is not None, None, False
        return lifted

    def report(self):
        rep = self.primary.report()
        rep["rescue_budget"] = self.budget
        if self.rule != "factor":
            rep["rescue_rule"] = self.rule
        if self.maxit is not None:
            rep["rescue_maxit"] = self.maxit
        return rep


# ================================================================ block SPAI smoother
def block_spai(A: BlockCSR, *, omega: float = 1.0, max_width: int = 64) -> torch.Tensor:
    """Frobenius-optimal one-hop block sparse approximate inverse on A's own pattern.

    Row block i of ``W`` (blocks at the columns ``P_i`` of row i of ``A``) minimises
    ``|| sum_j W_ij A_j,: - E_i ||_F``, i.e. ``W_i = A[P_i, i]^T ((A A^T)[P_i, P_i])^-1`` -- one small
    dense SPD solve per cell, batched.  Returns values aligned with ``A``'s blocks, scaled by
    ``omega``.  The classical, parallel counterpart of a learned one-hop smoother."""
    n, F = A.shape[0], A.F
    device, dtype = A.val.device, A.val.dtype
    ptr = A.ptr()
    width = (ptr[1:] - ptr[:-1])
    p = int(width.max())
    if p > max_width:
        raise ValueError(f"row pattern of {p} blocks is too wide for a dense per-cell SPAI")
    At = A.transpose()
    G = SpGEMM(A, At)(A.val, At.val)                     # A A^T, blocks
    keyG = G.row * n + G.col
    keyA = A.row * n + A.col
    slot = torch.arange(p, device=device)
    idx = ptr[:-1, None] + slot[None, :]                 # [n, p] block index of row i, slot a
    valid = slot[None, :] < width[:, None]
    cols = torch.where(valid, A.col[idx.clamp_max(A.nnzb - 1)], torch.zeros_like(idx))   # P_i
    # Gram blocks (A A^T)[P_i[a], P_i[b]]
    qa = cols[:, :, None] * n + cols[:, None, :]
    pos = torch.searchsorted(keyG, qa.reshape(-1)).clamp_max(keyG.numel() - 1)
    hit = (keyG[pos] == qa.reshape(-1)).reshape(n, p, p) & valid[:, :, None] & valid[:, None, :]
    Gb = torch.where(hit[..., None, None], G.val[pos].reshape(n, p, p, F, F), torch.zeros((), dtype=dtype, device=device))
    eye = torch.eye(F, dtype=dtype, device=device)
    pad = (~valid)[:, :, None] & (~valid)[:, None, :] & torch.eye(p, dtype=torch.bool, device=device)[None]
    Gb = Gb + pad[..., None, None].to(dtype) * eye
    Gd = Gb.permute(0, 1, 3, 2, 4).reshape(n, p * F, p * F)
    # right-hand side A[P_i[a], i]^T
    qr = cols * n + torch.arange(n, device=device)[:, None]
    posr = torch.searchsorted(keyA, qr.reshape(-1)).clamp_max(keyA.numel() - 1)
    hitr = (keyA[posr] == qr.reshape(-1)).reshape(n, p) & valid
    Rb = torch.where(hitr[..., None, None], A.val[posr].reshape(n, p, F, F), torch.zeros((), dtype=dtype, device=device))
    R = Rb.transpose(2, 3).permute(0, 2, 1, 3).reshape(n, F, p * F)       # [n, F, pF] = A[P_i,i]^T
    X = torch.linalg.solve(Gd, R.transpose(1, 2))                        # G X = R^T
    Wi = X.transpose(1, 2).reshape(n, F, p, F).permute(0, 2, 1, 3)       # [n, p, F, F]
    return omega * Wi[valid]                                             # row-major = A's block order


# =========================================================================== hierarchy
def _eye(F, dtype, device):
    return torch.eye(F, dtype=dtype, device=device)


def _sync_clock(device) -> float:
    """``time.perf_counter()`` after the device's queued work (M2 setup timers; only the M2 paths call it)."""
    if torch.device(device).type == "cuda":
        torch.cuda.synchronize(device)
    return time.perf_counter()


def _block_sums(plan: "SpGEMM", val: torch.Tensor, order=None) -> torch.Tensor:
    """``C[out[e]] += val[e]`` (``val[order[e]]`` with ``order``) over the triples of a product with the tentative
    prolongation P0 (one identity block per row, so triple e is block e of the left operand): the block sums
    ``sum_{j in a} A_ij`` per output block ``(i, a)`` without a single block product (M2c ``fused_numeric``).
    Streams the chunks of a lean (pinned-host) plan."""
    if plan.triples != int(val.shape[0]):
        raise ValueError(f"a P0 product has one triple per block: {plan.triples} triples for {val.shape[0]} blocks")
    if plan._warp_ok(val):
        # W6 (opt-in): the segmented block sum on the Warp segments (block_amg_warp.block_sums)
        from Pipeline.CoupledNewton import block_amg_warp as _bw
        return _bw.block_sums(plan, val, order)
    device = val.device
    C = torch.zeros(plan.row.numel(), val.shape[1], val.shape[2], dtype=val.dtype, device=device)
    for s in range(0, plan.triples, plan.chunk):
        e = slice(s, min(s + plan.chunk, plan.triples))
        io = _on_device(plan.out[e], device)
        src = val[e] if order is None else val[_on_device(order[e], device)]
        C.index_add_(0, io, src)
    return C


_BSR_STATE = {"ok": None, "error": None}     # torch's CUDA BSR mat-vec: untested / works / its failure (M2c)
# Time every numeric pass by part (synced), also of plans without an M2 flag; the camg entry sets it under
# --component-timers.  Off: no plan is timed unless it carries an M2 flag.
NUMERIC_TIMERS = {"on": False}
# Warp numeric pass (``solve_with_coupled_newton --camg-warp-numeric`` / ``--camg-warp-omega``; off = the
# default numeric pass, call for call): "on" -- every SpGEMM product and P0 block sum of a CUDA float64 numeric pass
# runs one Warp kernel over its output blocks (``block_amg_warp``), and a lean ``fused_numeric`` plan keeps its triples
# as the Warp segments on the device instead of streaming them from pinned host memory (``SpGEMM.to_warp``); "omega" --
# the prolongation weight's power iteration applies D^-1 A with one Warp kernel (``block_amg_warp.dinv_a``).
# Rounding-level differences (sums in another order).
WARP_NUMERIC = {"on": False, "omega": False, "omega_device": False}
# The operator-decided l1 rule for every plan of the process whose own ``adaptive_l1`` is 0 (off): a positive
# threshold switches each aggregated level whose damped block Jacobi amplifies above it to the l1 diagonal, in the
# classical and in the learned cycle alike (``CoupledAMGPlan``).  Set by ``set_adaptive_l1`` (the steady entry's
# ``--camg-adaptive-l1``) or, for every process of a run, by the environment variable ``LPCNS_CAMG_ADAPTIVE_L1``.
ADAPTIVE_L1 = {"t": float(os.environ.get("LPCNS_CAMG_ADAPTIVE_L1", "0") or 0.0),
               "gate": float(os.environ.get("LPCNS_CAMG_ADAPTIVE_L1_GATE", "0") or 0.0)}
# the gate's classical reference cycle (one Gauss-Seidel sweep each way at this damping) and its power steps
L1_GATE_OMEGA, L1_GATE_STEPS = 0.7, 10
# The rule's decision kept for a whole time march (``set_l1_march``, the time-dependent entry's
# ``--camg-adaptive-l1-march``): the first gate decision on a hierarchy (its level sizes and block size) is applied to
# every later plan of that hierarchy in the process, which then runs no radius test and no gate
L1_MARCH = {"on": False, "decisions": {}}


def set_l1_march(on: bool) -> None:
    """The march-level l1 decision on or off (off: every build decides, as without it); clears the decisions."""
    L1_MARCH["on"], L1_MARCH["decisions"] = bool(on), {}


def l1_rule_on(kw: dict | None = None) -> bool:
    """Whether the operator-decided l1 rule decides the l1 levels of a cycle built from ``kw``: the process-wide rule
    (``ADAPTIVE_L1``) or the keywords' own ``adaptive_l1``."""
    return ADAPTIVE_L1["t"] > 0 or float((kw or {}).get("adaptive_l1") or 0.0) > 0


def l1_rescue_kw(kw: dict) -> dict | None:
    """The keywords of the l1 stagnation rescue's cycle for a cycle built from ``kw``: ``kw`` with l1 on every
    aggregated level -- or None while the operator-decided l1 rule is on (``l1_rule_on``).  Under the rule the l1
    levels are the selection's, and they are already those of the cycle that runs: the primary, or the hand-over
    cycle of a learned arm.  A rescue would rebuild that plan, so none is built, and no rescue ever switches a level
    the selection refused (where the l1 smoother itself amplifies, an always-l1 rescue spends its iterations without
    reducing the residual)."""
    return None if l1_rule_on(kw) else {**kw, "l1": True}


# Every decision of the operator-decided l1 rule, in build order (``record_l1_decision``): the Newton rows read the
# ones made since their previous row (``l1_rule_columns``)
L1_RULE_DECISIONS = []


def record_l1_decision(plan, gate_entry: dict | None) -> dict:
    """Records the rule's decision for one build of ``plan``: ``choice`` -- the selection's plan ("none",
    "flagged_and_coarser", "full") when the gate passed, "below-gate" when it did not, "no-flag" when the radius test
    flagged no level, "radius" without a gate (the radius test alone switches), "kept" on a plan whose decision is
    frozen -- the plan's l1 levels after it, and the gate's growth and reductions where it ran."""
    levels = [k for k, L in enumerate(plan.levels) if L.agg is not None and getattr(L, "l1", False)]
    if gate_entry is None:
        choice = ("kept" if getattr(plan, "_l1_decided", False)
                  else "no-flag" if getattr(plan, "adaptive_l1_gate", 0.0) > 0 else "radius")
        row = {"choice": choice, "levels": levels}
    else:
        sel = gate_entry.get("selection")
        row = {"choice": sel["chosen"] if sel else "below-gate", "levels": levels,
               "growth": gate_entry.get("growth"), "reductions": (sel or {}).get("reductions")}
    L1_RULE_DECISIONS.append(row)
    return row


def l1_rule_columns(since: int) -> tuple:
    """``(columns, count)``: the Newton-row columns of the rule's decisions made after the first ``since`` of
    ``L1_RULE_DECISIONS`` -- ``l1_rule`` (each decision as ``choice:levels``, ';'-joined; empty when no build decided)
    and ``l1_levels`` (the l1 levels of the latest decision, '-' for none) -- and the count to pass next time."""
    new = L1_RULE_DECISIONS[since:]
    last = L1_RULE_DECISIONS[-1] if L1_RULE_DECISIONS else None
    fmt = lambda lv: ",".join(str(k) for k in lv) if lv else "-"
    return ({"l1_rule": ";".join(f"{d['choice']}:{fmt(d['levels'])}" for d in new),
             "l1_levels": fmt(last["levels"]) if last else ""}, len(L1_RULE_DECISIONS))


# The rule's selection once the gate passes: GMRES steps of the reference cycle per candidate plan, and the base of the
# seed of its random right-hand side (``l1_select_seed``)
L1_SELECT_STEPS, L1_SELECT_SEED = 16, 1729


def l1_select_seed(A) -> int:
    """The seed of the selection's random vector for system ``A``: from the base seed, the number of rows and the
    number of blocks (never 0 or 6, the seeds of the readings and of the growth estimate)."""
    seed = (L1_SELECT_SEED * 1000003 + int(A.shape[0]) * 7919 + int(A.row.numel())) % 2147483647
    return seed + 1 if seed in (0, 6) else seed


@torch.no_grad()
def gmres_reduction(op, r0: torch.Tensor, steps: int) -> float:
    """ln(|r_k| / |r_0|) of ``steps`` GMRES steps on ``op`` from ``r0`` (Arnoldi with modified Gram-Schmidt, the
    least-squares residual of the Hessenberg system); -inf after an exact breakdown, inf on a non-finite step."""
    beta = float(r0.norm())
    if not math.isfinite(beta) or beta == 0.0:
        return 0.0
    V = [r0 / beta]
    H = torch.zeros(steps + 1, steps, dtype=torch.float64)
    k = steps
    for j in range(steps):
        w = op(V[j])
        for i in range(j + 1):
            H[i, j] = float((V[i] * w).sum())
            w = w - float(H[i, j]) * V[i]
        H[j + 1, j] = float(w.norm())
        if not math.isfinite(float(H[j + 1, j])):
            return float("inf")
        if float(H[j + 1, j]) <= 1e-14 * beta:
            k = j + 1
            break
        V.append(w / float(H[j + 1, j]))
    rhs = torch.zeros(k + 1, dtype=torch.float64)
    rhs[0] = beta
    y = torch.linalg.lstsq(H[:k + 1, :k], rhs[:, None]).solution[:, 0]
    res = float((rhs - H[:k + 1, :k] @ y).norm())
    return math.log(res / beta) if res > 0 else float("-inf")


def set_adaptive_l1(t: float, gate: float = 0.0) -> None:
    """The process-wide operator-decided l1 rule (``t`` 0 = off; ``gate`` 0 = no gate); plans built afterwards use
    it."""
    ADAPTIVE_L1["t"], ADAPTIVE_L1["gate"] = float(t), float(gate)


@contextlib.contextmanager
def adaptive_l1_rule(t: float, gate: float = 0.0):
    """The operator-decided l1 rule (radius threshold ``t``, gate ``gate``) for every plan built inside the block, in
    this process and in the processes it starts (``LPCNS_CAMG_ADAPTIVE_L1``, ``LPCNS_CAMG_ADAPTIVE_L1_GATE``); all
    restored on exit."""
    keys = ("LPCNS_CAMG_ADAPTIVE_L1", "LPCNS_CAMG_ADAPTIVE_L1_GATE")
    prev, prev_env = dict(ADAPTIVE_L1), {k: os.environ.get(k) for k in keys}
    set_adaptive_l1(t, gate)
    os.environ.update(dict(zip(keys, (str(float(t)), str(float(gate))))))
    try:
        yield
    finally:
        set_adaptive_l1(prev["t"], prev["gate"])
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
# ("omega_device", ``--camg-omega-device``: the power iteration keeps its norms on the device, one read-back per estimate
# instead of one per iteration -- bit for bit the same omega)
# Coarse inverse (``--camg-coarse-inverse``; off = the dense LU solve): the block cycle applies
# the coarsest level as one GEMV with the explicit inverse computed once per hierarchy from the same LU factors
# (rounding-level differences; W01: 0.40 ms of a 16 ms V-cycle in the triangular solves of 1,770 unknowns)
COARSE_INVERSE = {"on": False, "min_pivot": 0.0}
# ``min_pivot = t > 0`` (opt-in, ``--camg-coarse-inverse-min-pivot``): on a hierarchy whose gauge field is free, the
# explicit inverse only when the coarse LU's smallest pivot is at least ``t`` times its largest (``_coarse_by_lu``).
# Coarse gauge (opt-in, ``--camg-coarse-gauge``): on a hierarchy whose gauge field is free, the block cycle's coarse
# correction without the constant mode of that field (``_coarse_gauge``).
COARSE_GAUGE = {"on": False}


@contextlib.contextmanager
def coarse_by_lu():
    """The coarsest level of every block cycle by its LU (no explicit inverse) and every V-cycle eager (no graph,
    whose captured kernels would replay the inverse) for the duration: the redo of a refused solve
    (``solve_newton(true_residual_redo=...)``)."""
    was_inv, was_graph = COARSE_INVERSE["on"], GRAPH_STATS["enabled"]
    COARSE_INVERSE["on"], GRAPH_STATS["enabled"] = False, False
    try:
        yield
    finally:
        COARSE_INVERSE["on"], GRAPH_STATS["enabled"] = was_inv, was_graph


def _bsr_matvec(L: "LevelPlan", val: torch.Tensor):
    """``x -> A x`` for ``[n, F]`` cell vectors through torch's BSR mat-vec (cuSPARSE bsrmv) on the level's own block
    values -- no copy of them; int32 block indices made for the call.  None off CUDA or after a
    failure (the caller keeps ``BlockCSR.matvec``).  Sums in cuSPARSE's order: rounding-level differences."""
    if val.device.type != "cuda" or _BSR_STATE["ok"] is False or not val.is_contiguous():
        return None
    n, F = L.n, int(val.shape[1])
    if max(n * F, int(val.shape[0])) >= _INT32_LIMIT:
        return None
    try:
        crow = torch.zeros(n + 1, dtype=torch.int32, device=val.device)
        crow[1:] = torch.cumsum(torch.bincount(L.row, minlength=n), 0)
        M = torch.sparse_bsr_tensor(crow, L.col.to(torch.int32), val, size=(n * F, n * F), check_invariants=False)

        def mv(x: torch.Tensor) -> torch.Tensor:
            return (M @ x.reshape(-1, 1)).reshape(n, F)

        if _BSR_STATE["ok"] is None:           # first use in the process: checked against the block product once
            x = torch.ones(n, F, dtype=val.dtype, device=val.device)
            ref = BlockCSR(L.row, L.col, val, (n, n)).matvec(x)
            got = mv(x)
            if not bool(torch.allclose(got, ref, rtol=1e-10, atol=1e-12 * float(ref.abs().max().clamp_min(1)))):
                raise RuntimeError("BSR mat-vec disagrees with the block product")
            _BSR_STATE["ok"] = True
        return mv
    except Exception as failure:            # noqa: BLE001 -- any failure: the block product
        _BSR_STATE.update(ok=False, error=f"{type(failure).__name__}: {failure}")
        return None


LAZY_COLOURS = {"on": False}      # W6 (opt-in, --camg-lazy-colours): see the plan constructor's colour loop

# Work counter of the block path's classical GS sweeps (off by default; tests and cost accounting): per level, the
# sweeps run, their sequential passes and the matrix blocks they multiplied, as executed
SWEEP_WORK = {"on": False, "levels": {}}


def _count_sweep(k: int, passes: int, products: int) -> None:
    d = SWEEP_WORK["levels"].setdefault(int(k), {"sweeps": 0, "passes": 0, "products": 0})
    d["sweeps"] += 1
    d["passes"] += int(passes)
    d["products"] += int(products)


def hybrid_levels(row: torch.Tensor, col: torch.Tensor, n: int, same: torch.Tensor) -> torch.Tensor:
    """Level schedule of the forward triangular solve in the natural row order inside subdomains.

    ``same[e]``: block ``e`` couples two rows of one subdomain.  A row's level is one more than the largest level of
    the rows of its subdomain with a smaller index it is coupled to (``A_ij`` or ``A_ji`` nonzero), 0 without one.
    So the rows of one level are mutually uncoupled and every coupled pair of a subdomain is ordered by its index:
    the levels in increasing order are the exact forward sweep, in decreasing order the exact backward sweep."""
    off = same & (row != col)
    hi, lo = torch.maximum(row[off], col[off]), torch.minimum(row[off], col[off])
    level = torch.zeros(n, dtype=torch.long, device=row.device)
    if hi.numel() == 0:
        return level
    while True:
        new = level
        for _ in range(16):              # longest-path relaxation; the levels only grow, so extra passes are exact
            new = new.scatter_reduce(0, hi, new[lo] + 1, reduce="amax", include_self=True)
        if torch.equal(new, level):
            return level
        level = new


class _LazyColour:
    """One GS colour of a plan level, ``(rows, edges, local)`` like the tuples it replaces (W6 ``LAZY_COLOURS``):
    ``rows`` exists at once; the block-GS edge list and local indices -- read only by the block (non-fast) GS sweep --
    are formed, exactly as the eager loop forms them, at their first access."""

    __slots__ = ("rows", "_row", "_n", "_lean", "_rest")

    def __init__(self, rows, row, n, lean):
        self.rows, self._row, self._n, self._lean, self._rest = rows, row, int(n), bool(lean), None

    def _built(self):
        if self._rest is None:
            rows, row, n = self.rows, self._row, self._n
            mask = torch.zeros(n, dtype=torch.bool, device=rows.device)
            mask[rows] = True
            edges = torch.nonzero(mask[row]).reshape(-1)
            local = torch.full((n,), -1, dtype=torch.long, device=rows.device)
            local[rows] = torch.arange(rows.numel(), device=rows.device)
            loc = local[row[edges]]
            self._rest = (_to_pinned(edges), _to_pinned(loc)) if self._lean else (edges, loc)
        return self._rest

    def __getitem__(self, i):
        if isinstance(i, int) and i in (0, -3):
            return self.rows
        return (self.rows, *self._built())[i]

    def __iter__(self):
        return iter((self.rows, *self._built()))

    def __len__(self):
        return 3


def _to_pinned(t):
    """A pinned host copy of a CUDA tensor (lean plans); anything else as it is."""
    if t is None or not torch.is_tensor(t) or not t.is_cuda:
        return t
    out = torch.empty(t.shape, dtype=t.dtype, pin_memory=True)
    out.copy_(t)
    return out


def _on_device(t, device):
    """``t`` on ``device`` (a copy of a host tensor, the tensor itself when it is there already)."""
    if t is None or not torch.is_tensor(t) or t.device == torch.device(device):
        return t
    return t.to(device, non_blocking=True)


@dataclass
class LevelPlan:
    """What one level needs that depends on the block PATTERN only (built once per matrix)."""
    n: int
    row: torch.Tensor
    col: torch.Tensor
    diag: torch.Tensor                     # block index of every row's diagonal block
    t_order: torch.Tensor                  # A^T blocks = val[t_order].transpose(1, 2)
    t_row: torch.Tensor
    t_col: torch.Tensor
    colours: list = field(default_factory=list)   # (rows, edges, local row index) per colour
    agg: torch.Tensor = None               # None on the coarsest level
    n_c: int = 0
    omega: float = 0.0                     # classical prolongation-smoothing weight
    plan_AP0: "SpGEMM" = None              # (D^-1 A) P0   -> pattern of P
    plan_AtP0: "SpGEMM" = None             # (D^-T A^T) P0 -> pattern of R^T
    p_tent: torch.Tensor = None            # P blocks that are the tentative identity
    rt_tent: torch.Tensor = None
    # the tentative prolongation blocks [n, F, F_c] of a plan with a near-null space (``RigidBodyModes``); None: I_F
    p0: torch.Tensor = None
    r_order: torch.Tensor = None           # R blocks from R^T blocks
    r_row: torch.Tensor = None
    r_col: torch.Tensor = None
    plan_AP: "SpGEMM" = None
    plan_RAP: "SpGEMM" = None
    # With unsmoothed aggregation, the position in the Galerkin pattern of every fine block's
    # (agg(i), agg(j)) -- the numeric pass is then one scatter-add (``_level_numeric``); None otherwise
    ua_target: torch.Tensor = None
    # the diagonal the smoothers of this level invert: False = the raw block D, True = l1,
    # "signed" = signed l1 (``smoother_diagonal``). Copied from CoupledAMGPlan.l1 on every level, so a modulator --
    # which gets only (k, A, LevelPlan) -- builds its W on the same diagonal as the cycle's smoothers
    l1: bool | str = False
    # singular-value floor of the smoother diagonal (0 = off): copied from
    # CoupledAMGPlan.diag_eps on every level, see ``guarded_diagonal``
    diag_eps: float = 0.0
    # whether this level's P / R are smoothed, set by a plan with ``coarse_split``; None = the
    # plan's ``smooth_prolongation`` (every plan without a split)
    smooth: bool | None = None
    # symbolic data of the fast kernels (``CoupledAMG(fast_kernels=True)``): the level's vector layout
    # and the scalar-CSR patterns of A, the smoother inverses, P and R, built at the first fast cycle on this plan and
    # shared by every cycle on it (``FastPattern``).  Empty unless a fast cycle used the plan.  Also weak references to
    # the last fine-matrix fill (``_shared_fill``): they keep nothing alive.
    fast: dict = field(default_factory=dict, repr=False, compare=False)


def smoother_diagonal(val: torch.Tensor, L: LevelPlan) -> torch.Tensor:
    """The ``(n, F, F)`` diagonal blocks the smoothers of level plan ``L`` invert.

    ``L.l1`` False: the raw diagonal ``val[L.diag]``.  True: the l1 diagonal ``D + diag(r)`` with ``r_f`` the
    sum of ``|A_ij,fg|`` over the OFF-diagonal blocks of the row and all ``g`` (Baker, Falgout, Kolev & Yang
    2011).  ``"signed"``: rows whose own diagonal entry is negative get ``-r_f``.  ``L.diag_eps`` > 0 then lifts the
    near-singular blocks (``guarded_diagonal``).  Shared by the cycle (``CoupledAMGPlan._smoother_diagonal``: Sinv,
    GS / Jacobi sweeps, P/R smoothing, the stabilize reference) and the learned one-hop W
    (``NNmodels.coupled_amg_net``), so all of them invert ONE diagonal."""
    D = val[L.diag]
    l1 = getattr(L, "l1", False)
    eps = getattr(L, "diag_eps", 0.0)
    if not l1 and not eps > 0:
        return D
    if l1:
        rowsum = offdiag_l1(val, L)
        if l1 == "signed":
            # rows whose own diagonal entry is NEGATIVE (the k rows of the 56 leading-edge cells where the certified
            # SST root has k < 0) get the row sum with that sign: |d| grows instead of being pulled through zero
            d = torch.diagonal(D, dim1=-2, dim2=-1)
            rowsum = torch.where(d < 0, -rowsum, rowsum)
        D = D + torch.diag_embed(rowsum)
    return guarded_diagonal(D, val, L, eps) if eps > 0 else D


# The largest slice of blocks (bytes of their values) the l1 row sums read at once.  A level with more blocks is summed
# over row-aligned slices, so the off-diagonal blocks are never copied out whole: that copy and its absolute value are
# two [nnzb, F, F] transients (15.5 GiB each on the finest level of the 152^3 cavity's coupled Jacobian).
L1_SLICE_BYTES = 1 << 28


def offdiag_l1(val: torch.Tensor, L: LevelPlan) -> torch.Tensor:
    """``(n, F)`` row sums ``r_f = sum_{j != i, g} |A_ij,fg|`` over the OFF-diagonal blocks of every scalar row: the
    amount ``smoother_diagonal``'s l1 adds to the diagonal.

    A level of at most ``L1_SLICE_BYTES`` of block values is summed in one pass; a larger one over slices of the block
    list that end at a row boundary (when the rows are sorted, as in a CSR level), so every row's sum is formed from
    the same blocks in the same order within one slice, and no transient larger than a slice is held."""
    out = torch.zeros(L.n, val.shape[1], dtype=val.dtype, device=val.device)
    nnz = int(L.row.numel())
    per = max(1, L1_SLICE_BYTES // max(1, int(val[0].numel()) * val.element_size())) if nnz else 1
    if nnz <= per:
        off = L.row != L.col
        return out.index_add_(0, L.row[off], val[off].abs().sum(-1))
    rows_sorted = bool((L.row[1:] >= L.row[:-1]).all())
    start = 0
    while start < nnz:
        end = min(start + per, nnz)
        if rows_sorted and end < nnz:
            first = int(torch.searchsorted(L.row, L.row[end:end + 1]))     # the first block of the row at ``end``
            end = first if first > start else end
        row, col = L.row[start:end], L.col[start:end]
        off = row != col
        out.index_add_(0, row[off], val[start:end][off].abs().sum(-1))
        del row, col, off
        start = end
    return out


def stabilized_diagonal(D: torch.Tensor, val: torch.Tensor, L: LevelPlan, lam: torch.Tensor) -> torch.Tensor:
    """``D + diag(lam * r)`` with ``r = offdiag_l1(val, L)`` and ``lam`` of shape ``(n, F)``.

    ``lam = 0`` is ``D`` (the smoother of the plan, bit for bit through ``_level_numeric``), ``lam = 1`` the l1
    diagonal; a per-row ``lam`` puts the l1 regularisation only where a row needs it.  Why: at Ra >= 1e7 on the
    under-resolved DHC g4 meshes the temperature (and momentum) rows of the true Jacobian have a diagonal of the
    diffusion size only, their off-diagonal convective couplings are 1e2-1e5 times larger (cell Peclet number; II-4 D1,
    O1-HR D1) and some diagonals are negative; every smoother that inverts that diagonal amplifies wall-layer T modes
    (GS 2+2 growth 4.6-140, II-4 D2) and full l1 is stable but over-damps every row (l1 GS 2+2 needs 600+ its).
    Differentiable in ``lam`` (the learned / oracle head of the stabilised GS smoother)."""
    return D + torch.diag_embed(lam.to(D.dtype) * offdiag_l1(val, L))


# per-row stabilisation rules of the smoother diagonal (``stabilizer_lambda``; O1-HR-2r)
STAB_RULES = ("uniform", "floor")
# ``CoupledAMG(sweep_guard=...)``: the power steps of a level's sweep probe and the most halvings of its damping
SWEEP_GUARD_STEPS = 8
SWEEP_GUARD_HALVINGS = 4
# field sets by name for an F = 5 Boussinesq system (u, v, w, p, T) and F = 4 (u, v, w, p); None = every field
STAB_FIELDS = {"all": None, "U": (0, 1, 2), "T": (4,), "UT": (0, 1, 2, 4), "p": (3,)}


def stabilizer_lambda(val: torch.Tensor, L: LevelPlan, rule: str, value: float, fields=None) -> torch.Tensor:
    """``(n, F)`` per-row ``lam`` of ``stabilized_diagonal`` by a classical rule:

    * ``"uniform"``: ``lam = value`` on every row of ``fields`` (a fixed share of l1 everywhere);
    * ``"floor"``: ``lam = max(0, value - d / r)`` with ``d`` the row's own entry of the plan's smoother diagonal and
      ``r = offdiag_l1``, i.e. the stabilised entry is ``max(d, value * r)`` -- rows already dominant at level
      ``value`` keep their diagonal (the strength of the classical smoother), a non-dominant or negative entry is
      lifted to ``value * r`` (``lam = 0`` where ``r = 0``).
    Rows outside ``fields`` (indices, or None = all) get ``lam = 0``.  No gradient."""
    if rule not in STAB_RULES:
        raise ValueError(f"rule must be one of {STAB_RULES}, not {rule!r}")
    with torch.no_grad():
        r = offdiag_l1(val, L)
        if rule == "uniform":
            lam = torch.full_like(r, float(value))
        else:
            d = torch.diagonal(smoother_diagonal(val, L), dim1=-2, dim2=-1)
            safe = torch.where(r > 0, r, torch.ones_like(r))
            lam = torch.where(r > 0, (float(value) - d / safe).clamp_min(0.0), torch.zeros_like(r))
        if fields is not None:
            keep = torch.zeros(r.shape[1], dtype=torch.bool, device=r.device)
            idx = [int(f) for f in fields if int(f) < r.shape[1]]
            if len(idx) != len(tuple(fields)):
                raise ValueError(f"fields {tuple(fields)} outside an F={r.shape[1]} system")
            keep[idx] = True
            lam = torch.where(keep[None, :], lam, torch.zeros_like(lam))
    return lam


class SinvStabilizer:
    """Modulator (``CoupledAMG(modulator=...)``) of the classical cycle's smoother diagonal by a rule of
    ``stabilizer_lambda`` on every smoothed level (``levels`` None) or the first ``levels`` levels, from level
    ``start`` on (default 0); the coarsest (exact) level is untouched.  The classical knob of the stabilised GS smoother
    and the Theta = 0 reference of its learned head (``start``: which
    levels of the deep value-paired hierarchy need the stabilisation)."""

    def __init__(self, rule: str, value: float, fields=None, levels: int | None = None, start: int = 0):
        if rule not in STAB_RULES:
            raise ValueError(f"rule must be one of {STAB_RULES}, not {rule!r}")
        self.rule, self.value = rule, float(value)
        self.fields = None if fields is None else tuple(int(f) for f in fields)
        self.levels = None if levels is None else int(levels)
        self.start = int(start)
        if self.start < 0 or (self.levels is not None and self.levels <= self.start):
            raise ValueError(f"empty level range [{self.start}, {self.levels})")

    def __call__(self, k: int, A: BlockCSR, L: LevelPlan) -> dict:
        if L.agg is None or k < self.start or (self.levels is not None and k >= self.levels):
            return {}
        return {"lam": stabilizer_lambda(A.val, L, self.rule, self.value, self.fields)}

    def describe(self) -> dict:
        return {"rule": self.rule, "value": self.value, "fields": self.fields, "levels": self.levels,
                "start": self.start}


class FieldDamping:
    """Modulator of the classical GS sweep: a constant factor ``phi[f]`` per field of the cell block on the update of
    every smoothed level (``psi`` = diag(phi - 1), so the sweep's damping is ``gs_omega * phi[f]``); the coarsest
    (exact) level is untouched and phi = 1 everywhere is the classical cycle."""

    def __init__(self, phi):
        self.phi = torch.as_tensor(phi, dtype=torch.float64).reshape(-1)

    @classmethod
    def from_dampings(cls, spec: str, field_names, base_omega: float) -> "FieldDamping":
        """``spec`` = ``w_U,w_p[,w_T]``: the damping of the velocity, pressure and temperature rows (``field_names``:
        one name per scalar row of the cell block, e.g. U, U, U, p, T); ``phi = w / base_omega``."""
        w = [float(v) for v in str(spec).split(",")]
        if len(w) not in (2, 3) or any(not (0.0 < v <= 2.0) for v in w):
            raise ValueError(f"field damping {spec!r}: two or three dampings w_U,w_p[,w_T] in (0, 2]")
        by = {"U": w[0], "p": w[1], "p_rgh": w[1]}
        if len(w) == 3:
            by["T"] = w[2]
        missing = sorted({str(n) for n in field_names} - set(by))
        if missing:
            raise ValueError(f"field damping {spec!r}: no damping for the fields {missing}")
        return cls([by[str(n)] / float(base_omega) for n in field_names])

    def __call__(self, k: int, A: BlockCSR, L: LevelPlan) -> dict:
        if L.agg is None:
            return {}
        p = self.phi.to(A.val.device, A.val.dtype)
        if p.numel() != A.F:
            raise ValueError(f"FieldDamping: {p.numel()} factors for a {A.F}-field system")
        return {"psi": torch.diag_embed((p - 1.0).expand(L.n, -1).contiguous())}

    def describe(self) -> dict:
        return {"phi": [round(float(v), 6) for v in self.phi]}


@torch.no_grad()
def cycle_contraction(amg, A: BlockCSR, gauge_field=None, steps: int = 12, seed: int = 6) -> float:
    """Growth per application of the cycle's error propagation I - M A on the homogeneous system: ``steps`` power
    iterations from a fixed random vector (CPU generator ``seed``), the mean of the free ``gauge_field`` removed at
    every step (None: no gauge).  > 1 means the stationary cycle amplifies some error."""
    n, F = A.shape[0], A.F

    def project(v):
        if gauge_field is None:
            return v
        v = v.clone()
        v[:, gauge_field] -= v[:, gauge_field].mean()
        return v
    g = torch.Generator(device="cpu").manual_seed(int(seed))
    x = project(torch.randn(n, F, dtype=torch.float64, generator=g).to(A.row.device))
    x = x / x.norm()
    rate = float("nan")
    for _ in range(int(steps)):
        y = project(x - amg.apply_blocks(A.matvec(x)))
        rate = float(y.norm())
        if not math.isfinite(rate) or rate == 0.0:
            break
        x = y / rate
    return rate


class LevelStabilizer:
    """``SinvStabilizer`` with one value per level: level k uses ``values[min(k, len - 1)]`` of the
    rule (0 = no stabilisation on that level); the coarsest (exact) level is untouched.  A per-level decision of the
    learned AMG configuration (docs/research/2026-10-18-o1c-learned-amg-configuration.md)."""

    def __init__(self, rule: str, values, fields=None):
        if rule not in STAB_RULES:
            raise ValueError(f"rule must be one of {STAB_RULES}, not {rule!r}")
        values = [float(v) for v in values]
        if not values or any(v < 0 for v in values):
            raise ValueError(f"per-level values must be a non-empty list of numbers >= 0, not {values!r}")
        self.rule, self.values = rule, values
        self.fields = None if fields is None else tuple(int(f) for f in fields)

    def __call__(self, k: int, A: BlockCSR, L: LevelPlan) -> dict:
        value = self.values[min(int(k), len(self.values) - 1)]
        if L.agg is None or value == 0.0:
            return {}
        return {"lam": stabilizer_lambda(A.val, L, self.rule, value, self.fields)}

    def describe(self) -> dict:
        return {"rule": self.rule, "values": list(self.values), "fields": self.fields}


def row_magnitude(val: torch.Tensor, L: LevelPlan) -> torch.Tensor:
    """``(n, F)`` l1 norm of every scalar row of the level matrix: ``sum_g |D_raw,fg| + sum_{j != i, g} |A_ij,fg|``
    (raw diagonal block plus off-diagonal blocks).  Positive on every non-empty row; an empty row gets 1."""
    m = offdiag_l1(val, L) + val[L.diag].abs().sum(-1)
    return torch.where(m > 0, m, torch.ones_like(m))


def guarded_diagonal(D: torch.Tensor, val: torch.Tensor, L: LevelPlan, eps: float) -> torch.Tensor:
    """``D`` with its near-singular blocks lifted by a singular-value floor in row-equilibrated form.

    With ``M = diag(m)`` the per-field row magnitudes (``row_magnitude``), ``B = M^-1 D = U S V^T``; a block whose
    smallest singular value is below ``eps`` becomes ``M U max(S, eps) V^T``, every other block is returned
    UNCHANGED (bit for bit).  So ``||B_g^-1|| <= 1 / eps``, i.e. ``|D_g^-1 r|_f`` is bounded relative to the row
    scales, whatever makes the block singular: a collapsed l1 entry (``d_kk + rowsum ~ 0`` where the certified SST
    root has k < 0), cross-field cancellation inside the block (the k-omega source terms) or both; lifting singular
    values never creates a new singularity, and the per-row equilibration keeps the floor scale-aware when the field
    scales differ by 1e5 (U 1e-3, p 2.5e-5, k/omega 3e-4, omega wall identity rows 1 on the SST plate).
    Non-finite blocks are left as they are (``_safe_block_inverse`` handles them)."""
    F = D.shape[-1]
    m = row_magnitude(val, L)
    B = D / m[:, :, None]
    fin = torch.isfinite(B).all(dim=(1, 2))
    eye = torch.eye(F, dtype=D.dtype, device=D.device).expand_as(B)
    U, S, Vh = torch.linalg.svd(torch.where(fin[:, None, None], B, eye))
    low = fin & (S[:, -1] < eps)
    if not bool(low.any()):
        return D
    lifted = m[:, :, None] * torch.bmm(U * S.clamp_min(eps)[:, None, :], Vh)
    return torch.where(low[:, None, None], lifted, D)


@dataclass
class LevelValues:
    A: BlockCSR
    Sinv: torch.Tensor = None              # smoother block inverses
    W: BlockCSR = None                     # one-hop smoother x += W (b - A x), when supplied
    rho: float = 0.0                       # spectral-radius estimate of D^-1 A (Chebyshev smoother)
    P: BlockCSR = None
    R: BlockCSR = None
    dense_lu: tuple = None


def _transpose_plan(row, col, n_rows, n_cols):
    order = torch.argsort(col * n_rows + row)
    return order, col[order], row[order]


DENSE_LU_MAX = 1e60


def dense_lu(M: torch.Tensor, what: str):
    """``torch.linalg.lu_factor`` of a small dense operator -- the coarsest level of a hierarchy, the local block of
    the trouble correction.  A non-finite ``M`` raises RuntimeError("non-finite <what> ...") first:
    finite fine-level values whose Galerkin products overflow, e.g. Darcy's exp(gamma u) far
    from the root, can make the CUDA LU never return (one hung 25 min, past every Python-level time limit); the
    build fails instead, and callers that treat a singular coarse LU as a failed build treat this one alike.  An
    EXACTLY singular ``M`` (zero pivot; met in training) is factored
    as M + 1e-12 max|diag| I instead -- the cycle stays a preconditioner, GMRES and the certificate are unaffected.
    The same refusal for entries above ``DENSE_LU_MAX`` (1e60): one query's coarsest operator was still
    finite when the CUDA LU hung (native stack), at a state with |F| ~ 5e90; measured --
    n 1728 (the whole 12^3 coarse grid), largest entry 4.0e92, while one build earlier 3.1e93 still returned -- so the
    hang follows what the elimination of such a matrix produces, not a sharp size, and the bound sits far below it and
    far above every linearisation the solver can use (flow and scalar operators stay below ~1e12).
    ``LPCNS_DEBUG_DENSE_LU=1`` prints the size and the largest entry of every factorisation."""
    peak = float(M.abs().max()) if M.numel() else 0.0
    if os.environ.get("LPCNS_DEBUG_DENSE_LU") == "1":
        print(f"[dense_lu] {what}: n {M.shape[0]}, max|entry| {peak:.3e}", flush=True)
    if not math.isfinite(peak) or not bool(torch.isfinite(M).all()):
        raise RuntimeError(f"non-finite {what}: the dense lu_factor would not return")
    if peak > DENSE_LU_MAX:
        raise RuntimeError(f"non-finite {what} in elimination (max |entry| {peak:.1e} > {DENSE_LU_MAX:.0e}): "
                           "the dense lu_factor would not return")
    try:
        return torch.linalg.lu_factor(M)
    except RuntimeError:
        eps = 1e-12 * M.diagonal().abs().max().clamp_min(1e-300)
        return torch.linalg.lu_factor(M + eps * torch.eye(M.shape[0], dtype=M.dtype, device=M.device))


# Infinitesimal rotation generators: rotation k moves the point y (relative to its centre) by G_k y.
_ROTATIONS = {2: ((((0.0, -1.0), (1.0, 0.0))),),
              3: (((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
                  ((0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
                  ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0)))}


class RigidBodyModes:
    """Near-null space of a displacement field for smoothed aggregation: the translations and the infinitesimal
    rotations (the rigid-body modes of linear elasticity; Vanek, Mandel & Brezina 1996).

    ``coords [n, d]`` (``d`` = 2 or 3) are the positions of the fine block rows, ``fields`` the ``d`` block components
    that hold the displacement; every other component keeps its constant mode only.  With ``F`` fine components and
    ``r`` rotations (1 in 2D, 3 in 3D), the first coarse level has ``F + r`` unknowns per aggregate -- the ``F``
    constants (the translations on the displacement components) and the ``r`` rotations, each scaled by the
    aggregate's size ``h`` -- and so has every deeper level.  The tentative prolongation reproduces every rigid-body
    mode exactly on every level:

    * fine level, node ``i`` of aggregate ``a`` (centroid ``c_a``, size ``h_a = max_i |x_i - c_a|``):
      ``P0_i = [I_F | E (G_k (x_i - c_a) / h_a)_k]``, ``E`` placing the ``d`` displacement rows;
    * coarse level, node ``a`` (centroid ``c_a``, size ``h_a``) of aggregate ``A`` (centroid ``C``, size
      ``H = max(max_a |c_a - C|, max_a h_a)``): ``P0_a = [[I_F, E (G_k (c_a - C) / H)_k], [0, (h_a / H) I_r]]``,

    so a rotation ``w`` about any point is ``(t_a, h_a w)`` on every coarse node.  The rotations of a fine aggregate
    are independent only when its nodes are not collinear; ``repair`` merges the aggregates that are (fewer than
    ``d`` nodes, or a rank-deficient second moment) into their strongest neighbour, so every coarse block is
    regular.  Coarse aggregates need no repair: their ``P0`` blocks hold ``(h_a / H) I_r`` and have full rank."""

    def __init__(self, coords, fields):
        coords = np.asarray(torch.as_tensor(coords).detach().cpu().numpy() if isinstance(coords, torch.Tensor)
                            else coords, dtype=np.float64)
        if coords.ndim != 2 or coords.shape[1] not in _ROTATIONS:
            raise ValueError(f"coords must be [n, 2] or [n, 3], not {coords.shape}")
        self.dim = int(coords.shape[1])
        self.fields = tuple(int(f) for f in fields)
        if len(self.fields) != self.dim or len(set(self.fields)) != self.dim:
            raise ValueError(f"the displacement needs {self.dim} distinct block components, not {self.fields}")
        self.coords = coords
        self.generators = np.asarray(_ROTATIONS[self.dim], dtype=np.float64)          # [r, d, d]
        self.rotations = int(self.generators.shape[0])
        self.merged = 0                                         # fine aggregates merged by ``repair``

    def describe(self) -> dict:
        return {"near_null": "rigid", "dim": self.dim, "fields": list(self.fields), "rotations": self.rotations,
                "merged_aggregates": self.merged}

    @staticmethod
    def _centroids(points: np.ndarray, agg: np.ndarray, n_c: int):
        count = np.bincount(agg, minlength=n_c).astype(np.float64)
        centre = np.stack([np.bincount(agg, weights=points[:, j], minlength=n_c) for j in range(points.shape[1])],
                          1) / np.maximum(count, 1.0)[:, None]
        return centre, count

    def _deficient(self, agg: np.ndarray, n_c: int) -> np.ndarray:
        """Aggregates whose nodes do not span the rotations."""
        x = self.coords
        centre, count = self._centroids(x, agg, n_c)
        y = x - centre[agg]
        if self.dim == 2:
            spread = np.bincount(agg, weights=(y * y).sum(1), minlength=n_c)
            return (count < 2) | (spread <= 0.0)
        second = np.zeros((n_c, 3, 3))
        for i in range(3):
            for j in range(3):
                second[:, i, j] = np.bincount(agg, weights=y[:, i] * y[:, j], minlength=n_c)
        inertia = np.trace(second, axis1=1, axis2=2)[:, None, None] * np.eye(3) - second
        eig = np.linalg.eigvalsh(inertia)
        return (count < 3) | (eig[:, 0] <= 1e-10 * np.maximum(eig[:, -1], 1e-300))

    def repair(self, agg: np.ndarray, S: sp.csr_matrix) -> np.ndarray:
        """``agg`` with every deficient fine aggregate merged into the neighbour aggregate it is most strongly
        connected to (by ``S``; the nearest centroid when it has no neighbour), compactly renumbered."""
        agg = np.asarray(agg, dtype=np.int64).copy()
        n = agg.size
        for _ in range(8):
            n_c = int(agg.max()) + 1
            bad = np.flatnonzero(self._deficient(agg, n_c))
            if bad.size == 0:
                break
            if n_c == 1:
                raise ValueError("rigid-body modes: the whole level is one aggregate whose nodes are collinear")
            ind = sp.csr_matrix((np.ones(n), (np.arange(n), agg)), shape=(n, n_c))
            between = (ind.T @ sp.csr_matrix(abs(S)) @ ind).tolil()
            centre, _ = self._centroids(self.coords, agg, n_c)
            target = np.arange(n_c)
            for a in bad:
                row = between.rows[a]
                vals = between.data[a]
                best, best_v = -1, -np.inf
                for b, v in zip(row, vals):
                    if b != a and v > best_v:
                        best, best_v = b, v
                if best < 0:
                    d2 = ((centre - centre[a]) ** 2).sum(1)
                    d2[a] = np.inf
                    best = int(np.argmin(d2))
                target[a] = best
                self.merged += 1
            # follow chains (a -> b -> c) to their end
            for _ in range(n_c):
                nxt = target[target]
                if np.array_equal(nxt, target):
                    break
                target = nxt
            _, agg = np.unique(target[agg], return_inverse=True)
        return agg

    def tentative(self, k: int, agg: np.ndarray, n_c: int, F: int, state, device, dtype):
        """``(P0 [n, F, F_c], state of the coarse level)``; ``state`` is None on the fine level, else
        ``(centroids [n, d], sizes [n])`` of the level's nodes."""
        r, d = self.rotations, self.dim
        fields = np.asarray(self.fields)
        agg = np.asarray(agg, dtype=np.int64)
        points = self.coords if state is None else state[0]
        centre, _ = self._centroids(points, agg, n_c)
        y = points - centre[agg]                                                  # [n, d]
        reach = np.sqrt((y * y).sum(1))
        if state is None:
            size = np.zeros(n_c)
            np.maximum.at(size, agg, reach)
            F_c = F + r
            P0 = np.zeros((agg.size, F, F_c))
            P0[:, np.arange(F), np.arange(F)] = 1.0
        else:
            child = state[1]
            size = np.zeros(n_c)
            np.maximum.at(size, agg, np.maximum(reach, child))
            F_c = F
            if F - r < d:
                raise ValueError(f"rigid-body modes: a coarse level of {F} unknowns per node cannot hold them")
            P0 = np.zeros((agg.size, F, F_c))
            base = F - r
            P0[:, np.arange(base), np.arange(base)] = 1.0
            P0[:, base + np.arange(r), base + np.arange(r)] = (child / size[agg])[:, None]
        if (size <= 0).any():
            raise ValueError("rigid-body modes: an aggregate has zero size")
        base = F if state is None else F - r
        moved = np.einsum("kij,nj->nki", self.generators, y) / size[agg][:, None, None]   # [n, r, d]
        for j in range(d):
            P0[:, fields[j], base + np.arange(r)] = moved[:, :, j]
        return (torch.as_tensor(P0, dtype=dtype, device=device), (centre, size))


class CoupledAMGPlan:
    """Symbolic hierarchy: aggregates, colourings and SpGEMM plans for one block pattern.

    Built from CLASSICAL values (aggregates and colourings are decided once, from the operator
    strengths of the classical hierarchy); afterwards ``numeric`` re-evaluates every level for
    new fine values and/or learned modulations without touching the combinatorics.

    Reuse across Newton linearisations (``solve_with_coupled_newton --camg-reuse-plan``):
    ``numeric(val, refresh_omega=True)`` on a plan built from an EARLIER matrix with the same block pattern
    recomputes everything that depends on values -- level matrices, Galerkin products, smoother inverses and the
    prolongation-smoothing weights ``omega`` -- and keeps the strength-based aggregates, colourings, transpose
    orders and SpGEMM index plans.  With ``theta = 0`` (the default; the camg entry never sets it) those depend on
    the block pattern alone -- ``block_strength`` drops only exactly-zero blocks and pyamg's standard / naive
    aggregation and ``greedy_colouring`` read only the sparsity of S -- so the reused plan is the plan a fresh build
    would make, unless a coarse Galerkin block is exactly zero at one linearisation and not at the other.  With
    ``theta > 0`` the strong-connection set moves with the values and frozen aggregates can coarsen worse than fresh
    ones: that is the convergence risk ``--camg-plan-refresh`` bounds.  The dense coarsest LU has no symbolic phase
    to keep (``torch.linalg.lu_factor`` pivots numerically); it is refactored at every ``numeric``.
    ``colour_coarsest=False`` (same flag) skips the colouring of the coarsest level, which the cycle never reads
    (that level is the dense LU solve); only ``report()['colours']`` shows the difference (0 there)."""

    def __init__(self, A: BlockCSR, decisions: dict | None = None, *, smoother="gs", restriction="pg",
                 smooth_prolongation=True, coarse_max=4000, max_levels=10, theta=0.0, aggregation="standard",
                 spai_omega=1.0, l1=False, diag_eps=0.0, colour_coarsest=True, lean=False, strength="values",
                 gpu_setup=False, fused_numeric=False, strength_fn=None, adaptive_l1=0.0, adaptive_l1_omega=0.75,
                 adaptive_l1_gate=0.0, coarse_split=None, level_plan=None, near_null=None, level0_from=None,
                 colour_fine: bool = False):
        self.smoother, self.restriction = smoother, restriction
        # Opt-in (None = off, every plan before): ``near_null`` -- a ``RigidBodyModes`` whose tentative prolongation
        # carries the rotations beside the constants (smoothed aggregation for elasticity).  The coarse levels then
        # hold F + r unknowns per aggregate.  The value-based host setup only (no M2, lean or fused numeric pass).
        if near_null is not None:
            if strength != "values" or gpu_setup or fused_numeric or lean:
                raise ValueError("near_null needs the value-based host setup: strength='values', no gpu_setup, "
                                 "fused_numeric or lean")
        self.near_null = near_null
        # Opt-in (None = off): ``level_plan`` -- a list of per-level dicts (entry k for level k,
        # the last entry for every deeper level) with any of "aggregation", "smooth" (P / R smoothed) and "coarse_max"
        # (level k is the coarsest once n F <= it); a key left out keeps the plan-wide setting.  Takes precedence over
        # ``coarse_split``.  The structural decisions of the learned AMG configuration.
        if level_plan is not None:
            level_plan = [dict(d) for d in level_plan]
            bad = [k for d in level_plan for k in d if k not in ("aggregation", "smooth", "coarse_max")]
            if not level_plan or bad:
                raise ValueError(f"level_plan must be a non-empty list of dicts of aggregation / smooth / coarse_max, "
                                 f"not keys {bad}")
        self.level_plan = level_plan
        # Opt-in (None = off): ``(k, aggregation, smooth)`` -- levels k and below aggregate
        # with ``aggregation`` and build their P / R smoothed (``smooth`` True) or as the tentative aggregation
        # (False); the levels above k keep ``aggregation`` / ``smooth_prolongation``.  E.g. ``(1, "pairwise1", False)``
        # on a structural smoothed plan: C's fine-level transfers over the deep cycle's value-paired coarse levels.
        if coarse_split is not None:
            k, agg_c, smooth_c = coarse_split
            if int(k) < 1:
                raise ValueError(f"coarse_split starts at level k >= 1, not {k}")
            coarse_split = (int(k), str(agg_c), bool(smooth_c))
        self.coarse_split = coarse_split
        # Opt-in (0 = off): ``adaptive_l1 = t > 0`` -- in every numeric pass, an aggregated
        # level whose damped block Jacobi ``I - w D^-1 A_k`` (w = ``adaptive_l1_omega``) has an estimated spectral
        # radius above t (10 power iterations, fixed seed) switches to the l1 smoother diagonal (``LevelPlan.l1``, kept
        # for the plan's later passes).  LearnedCoarseSpace README section 5: on the late Ra 1e7 state one coarse level
        # of the deep unsmoothed hierarchy has rho 75 and blows the cycle up; l1 everywhere costs 2-3 x the
        # iterations on the cylinders, where no level amplifies.
        if not float(adaptive_l1) > 0 and ADAPTIVE_L1["t"] > 0:       # the process-wide rule (``set_adaptive_l1``)
            adaptive_l1 = ADAPTIVE_L1["t"]
            if not float(adaptive_l1_gate) > 0:
                adaptive_l1_gate = ADAPTIVE_L1["gate"]
        self.adaptive_l1, self.adaptive_l1_omega = float(adaptive_l1), float(adaptive_l1_omega)
        # Opt-in (0 = no gate): ``adaptive_l1_gate = G > 0`` -- the levels the radius test flags (``_flagged``) switch
        # only when the classical reference cycle on this plan grows by more than G per application, the finest
        # flagged level together with every coarser aggregated level (``CoupledAMG._l1_gate``); ``freeze_l1`` (set by a
        # caller whose values never change, e.g. a dumped system) keeps the first decision and skips the radius test
        # afterwards
        self.adaptive_l1_gate = float(adaptive_l1_gate)
        self._flagged, self.freeze_l1, self._l1_decided = {}, False, False
        self._radii = {}                    # level -> the radius test's last reading (every tested level)
        self.adaptive_l1_gate_log = []      # {"flagged", "growth", "switched", "levels", "seconds"} per gate decision
        self.adaptive_l1_log = []           # (level, rho before) of every switch, for the factory's report
        # Opt-in (None = off): ``strength_fn(row, col, n, val, level) -> (r, c, s)`` replaces the
        # value strength graph that DEVICE aggregation (pairwise / mis2) reads on every level of a value-based plan
        # (gpu_setup): a learned or alternative coarsening rule.  It must return the edges of ``strength_on_device``
        # (the off-diagonal block pattern, both orientations; the smoother colouring reads them) with positive weights.
        self.strength_fn = strength_fn
        # GPU setup (opt-in; roadmap B3; defaults = the classical plan, call for call):
        #   strength="pattern"  M2a: the STRUCTURAL strength -- every stored off-diagonal block is a connection,
        #                       whatever its value (``strength_on_device(val=None)``) -- so the aggregates, colourings
        #                       and every SpGEMM / transpose index plan are functions of the block pattern alone.  The
        #                       plan is then VALUE-FREE: no classical numeric pass and no omega at build time
        #                       (``value_free``; ``numeric`` always computes omega for the values it is given, as
        #                       ``refresh_omega=True`` does).  One plan serves every linearisation of a run with a
        #                       fixed block pattern, and its decisions can be cached per mesh (``amg_plan_cache``).
        #   gpu_setup           M2b: the strength graph on the device (``strength_on_device``: the same graph as
        #                       ``block_strength`` bit for bit) and the smoother colouring by Jones-Plassmann on the
        #                       device (``jones_plassmann_colouring``: another valid colouring, not greedy_colouring's)
        #   fused_numeric       M2c: the numeric phase with fewer block products (``_level_numeric``, ``numeric``):
        #                       rounding-level differences
        #   decisions           the per-level aggregates / colourings of an earlier value-free plan of this pattern
        #                       (``self.decisions`` of that plan, e.g. read back by ``amg_plan_cache``): used instead
        #                       of recomputing them; checked (aggregate ranges, colour validity on the pattern).
        if strength not in ("values", "pattern"):
            raise ValueError(f"strength must be 'values' or 'pattern', not {strength!r}")
        if strength == "pattern" and float(theta) != 0.0:
            raise ValueError("strength='pattern' is the theta = 0 strength graph of the block pattern; theta must be 0")
        if decisions is not None and strength != "pattern":
            raise ValueError("decisions come from a value-free (strength='pattern') plan of the same pattern")
        self.strength, self.gpu_setup, self.fused_numeric = strength, bool(gpu_setup), bool(fused_numeric)
        self.value_free = strength == "pattern"
        self.colour_coarsest = bool(colour_coarsest)
        # ``colour_fine`` (opt-in): colour the fine level whatever the smoother, for a cycle that applies Gauss-Seidel
        # sweeps there besides its smoother (a learned W followed by its Gauss-Seidel sweep on a Chebyshev plan)
        self.colour_fine = bool(colour_fine)
        # Lean plan (opt-in; ``solve_with_coupled_newton --camg-lean-build``): the
        # SpGEMM triple index arrays, the block GS colour edges and the transpose columns are kept in pinned host
        # memory and streamed per chunk where read, and the numeric pass computes D^-1 A and D^-T A^T chunk by chunk
        # inside the tentative-prolongation products instead of materialising them (``_level_numeric``).  Same
        # values; only where they live and when they exist changes.  False (default): the plain plan.
        self.lean = bool(lean)
        # l1 smoother: the block diagonal the smoothers invert is D + diag(sum_j |A_ij|)
        # (off-diagonal blocks, row sums per field; Baker, Falgout, Kolev & Yang 2011), never near-singular.
        # On the certified SST flat plate a few omega diagonal blocks have cond 8e7 and the plain block-GS sweep
        # grows the omega error by ~1e10 per sweep. Default off: every recorded run is unchanged.
        self.l1 = l1 if l1 == "signed" else bool(l1)
        # singular-value floor of that diagonal (``guarded_diagonal``): D_l1 itself can be
        # near-singular where a negative k diagonal meets its row sum. 0 (default) = off, bit for bit
        self.diag_eps = float(diag_eps)
        self.spai_omega = float(spai_omega)
        self.smooth_prolongation = bool(smooth_prolongation)
        self.coarse_max, self.max_levels = int(coarse_max), int(max_levels)
        self.theta, self.aggregation = float(theta), aggregation
        self.F = A.F
        self.dtype, self.device = A.val.dtype, A.val.device
        self.levels: list[LevelPlan] = []
        m2 = self.value_free or self.gpu_setup
        aggs = [aggregation, (coarse_split or (0, ""))[1]] + [d.get("aggregation", "") for d in (level_plan or [])]
        if any(str(a).startswith(("pairwise", "mis2")) for a in aggs) and not m2:
            raise ValueError("device (pairwise / mis2) aggregation (PureGPU) needs the M2 setup: strength=\"pattern\" or "
                             "gpu_setup=True")
        if m2:
            # M2: the decisions this plan makes (or was given), per level: cells, aggregate map (int32, None on the
            # coarsest level), colouring (int32 or None); ``amg_plan_cache`` stores them.  Setup seconds by part.
            self.decisions = {"n": [], "agg": [], "colour": []}
            self.setup_seconds = {"strength": 0.0, "colouring": 0.0, "aggregation": 0.0, "symbolic": 0.0,
                                  "checks": 0.0}
            given = decisions
        # ``level0_from`` (None = off): a plan built from these very block values whose settings differ from this one's
        # only in the smoother diagonal (``l1`` / ``diag_eps``) lends its finest level's symbolic data -- colours,
        # aggregates, transposition, the SpGEMM plans: the value strength does not read the smoother diagonal, so this
        # plan would compute exactly these arrays -- and this plan computes that level's omega and numeric pass with
        # its own diagonal and plans the coarser levels as before (their values depend on the diagonal).  The caller
        # vouches for the values (the camg factory's stagnation rescue, built on its primary's matrix in the build
        # that made the primary's plan); the settings and the pattern are checked here.
        donor0 = self._level0_donor(level0_from, A)
        self.level0_shared = donor0 is not None
        row, col, n = A.row, A.col, A.shape[0]
        val = None if self.value_free else A.val.detach()
        nn_state = None                     # the near-null space's coarse coordinates and sizes (near_null only)
        while True:
            if donor0 is not None and not self.levels:
                # the lent finest level: its symbolic data with this plan's diagonal; omega and the numeric pass here
                L = _replace(donor0, l1=self.l1, diag_eps=self.diag_eps)
                self.levels.append(L)
                if L.agg is None:
                    break
                L.omega = self._prolongation_omega(L, val)
                values = self._level_numeric(L, val, {})
                row, col, n = L.plan_RAP.row, L.plan_RAP.col, L.n_c
                val = values["next"]
                continue
            L = self._pattern_level(row, col, n)
            k_level = len(self.levels)
            smooth = self._smooth_of(k_level)
            if self.coarse_split is not None or self.level_plan is not None:
                L.smooth = smooth
            self.levels.append(L)
            Ab = BlockCSR(row, col, val, (n, n))
            F_level = self.F if self.near_null is None else int(val.shape[1])
            last = n * F_level <= self._coarse_max_of(k_level) or len(self.levels) >= self.max_levels
            need_colour = ((self.smoother == "gs" or (self.colour_fine and len(self.levels) == 1))
                           and (self.colour_coarsest or not last))
            if m2:
                S, colour = self._m2_level_setup(len(self.levels) - 1, Ab, need_colour, last, given)
            else:
                S = block_strength(Ab, self.theta)
                colour = None
            # colour_coarsest False: the coarsest level is solved by dense LU and its colouring is
            # never read; greedy_colouring is a host loop over the cells, so it is skipped there
            if need_colour and LAZY_COLOURS["on"] and colour is not None and colour.size:
                # W6 (opt-in, --camg-lazy-colours): every colour's rows from ONE stable device sort (the rows of
                # np.flatnonzero(colour == c), in the same ascending order) with one read-back of the colour counts;
                # the block-GS edge lists are formed only if the block path ever reads them (``_LazyColour``)
                col_t = torch.as_tensor(np.asarray(colour), device=self.device)
                order_c = torch.argsort(col_t, stable=True)
                counts = torch.bincount(col_t, minlength=int(colour.max()) + 1).tolist()
                at = 0
                for cnt in counts:
                    L.colours.append(_LazyColour(order_c[at:at + cnt], row, n, self.lean))
                    at += cnt
            elif need_colour:
                if not m2:
                    colour = greedy_colouring(S)
                for c in range(int(colour.max()) + 1 if colour.size else 0):
                    rows = torch.as_tensor(np.flatnonzero(colour == c), device=self.device)
                    mask = torch.zeros(n, dtype=torch.bool, device=self.device)
                    mask[rows] = True
                    edges = torch.nonzero(mask[row]).reshape(-1)
                    local = torch.full((n,), -1, dtype=torch.long, device=self.device)
                    local[rows] = torch.arange(rows.numel(), device=self.device)
                    if self.lean:
                        # M5: only the block GS sweep reads edges / local (the fast cycle has its own layout)
                        L.colours.append((rows, _to_pinned(edges), _to_pinned(local[row[edges]])))
                    else:
                        L.colours.append((rows, edges, local[row[edges]]))
            if last:
                if m2:
                    self.decisions["agg"].append(None)
                break
            if m2:
                agg_np = self._m2_aggregate(len(self.levels) - 1, S, n, given)
            else:
                agg_np = aggregate(S, self._agg_of(k_level))
            if self.near_null is not None and k_level == 0:
                agg_np = self.near_null.repair(agg_np, S)
            n_c = int(agg_np.max()) + 1
            if n_c >= n:
                if not self.colour_coarsest:
                    L.colours = []          # the aggregation stalled: this level is the coarsest after all
                break
            t_sym = _sync_clock(self.device) if m2 else 0.0
            L.agg, L.n_c = torch.as_tensor(agg_np, device=self.device), n_c
            if self.near_null is not None:
                L.p0, nn_state = self.near_null.tentative(k_level, agg_np, n_c, F_level, nn_state, self.device,
                                                          self.dtype)
            # M2a: a value-free plan has no omega until ``numeric`` computes it for the values it is given
            L.omega = float("nan") if self.value_free else self._prolongation_omega(L, val)
            P0 = BlockCSR(torch.arange(n, device=self.device), L.agg,
                          _eye(self.F, self.dtype, self.device).expand(n, -1, -1), (n, n_c))
            if smooth:
                L.plan_AP0 = SpGEMM(Ab, P0)
                if self.lean:
                    self._offload(L.plan_AP0)
                p_row, p_col = L.plan_AP0.row, L.plan_AP0.col
                L.p_tent = (p_col == L.agg[p_row])
                # the symbolic SpGEMM reads the pattern only: a lean plan does not gather a transposed copy of the
                # values for it (M5; nnzb x F x F, 3.1 GiB on DHC88, alive to the end of the level)
                At_pattern = BlockCSR(L.t_row, L.t_col, None if (self.lean or m2) else val[L.t_order], (n, n))
                L.plan_AtP0 = SpGEMM(At_pattern, P0)
                if self.lean:
                    self._offload(L.plan_AtP0)
                rt_row, rt_col = L.plan_AtP0.row, L.plan_AtP0.col
                L.rt_tent = (rt_col == L.agg[rt_row])
            else:
                p_row, p_col = P0.row, P0.col
                L.p_tent = torch.ones_like(p_row, dtype=torch.bool)
                rt_row, rt_col = p_row, p_col
                L.rt_tent = L.p_tent
            if self.restriction == "transpose" or not smooth:
                rt_row, rt_col = p_row, p_col
            L.r_order, L.r_row, L.r_col = _transpose_plan(rt_row, rt_col, n, n_c)
            # (M2: the symbolic SpGEMM reads P's pattern only; no |P| x F x F block of zeros for it)
            P_pat = BlockCSR(p_row, p_col, None if m2 else torch.zeros(p_row.numel(), self.F, self.F,
                                                                       dtype=self.dtype, device=self.device), (n, n_c))
            L.plan_AP = SpGEMM(Ab, P_pat)
            if self.lean:
                self._offload(L.plan_AP)  # M5: each product's triples leave the device as soon as they exist
            AP_pat = BlockCSR(L.plan_AP.row, L.plan_AP.col, None, L.plan_AP.shape)
            R_pat = BlockCSR(L.r_row, L.r_col, None, (n_c, n))
            L.plan_RAP = SpGEMM(R_pat, AP_pat)
            if not smooth and L.p0 is None:
                L.ua_target = _galerkin_target(L.row, L.col, L.agg, n_c, L.plan_RAP.row, L.plan_RAP.col)
            if self.lean:
                self._offload(L.plan_RAP)
            if self.lean:
                # M5: the triples went to pinned host memory as each product was built (to_host is idempotent); the
                # transpose columns (read only above) follow, and the transpose order / rows when only slices read them
                for spgemm in (L.plan_AP0, L.plan_AtP0, L.plan_AP, L.plan_RAP):
                    if spgemm is not None:
                        self._offload(spgemm)
                L.t_col = _to_pinned(L.t_col)
                if L.plan_AtP0 is not None and L.plan_AtP0.identity_a:
                    # the transpose order / rows are read only by the fused D^-T A^T, in slices: host-resident too
                    L.t_row, L.t_order = _to_pinned(L.t_row), _to_pinned(L.t_order)
                del P_pat, AP_pat, R_pat
            if m2:
                self.setup_seconds["symbolic"] += _sync_clock(self.device) - t_sym
            if self.value_free:
                # M2a: the next level's pattern is the symbolic Galerkin product's; no values are needed for it
                row, col, n = L.plan_RAP.row, L.plan_RAP.col, n_c
                continue
            values = self._level_numeric(L, val, {})
            row, col, n = L.plan_RAP.row, L.plan_RAP.col, n_c
            val = values["next"]
        if m2 and given is not None and len(given["n"]) != len(self.decisions["n"]):
            raise ValueError(f"plan decisions: {len(given['n'])} levels stored, this pattern makes "
                             f"{len(self.decisions['n'])}")

    # ------------------------------------------------------------------ M2 setup
    def _m2_level_setup(self, k: int, Ab: BlockCSR, need_colour: bool, last: bool, given):
        """``(S, colour)`` of level ``k`` for the M2 plans: the host strength graph pyamg reads (None when the level's
        aggregate map is given or the level is the last) and the smoother colouring (int64 numpy, None when not
        needed).  Records the decisions and the seconds by part."""
        n = Ab.shape[0]
        dev = self.device
        self.decisions["n"].append(int(n))
        S = colour = None
        if given is not None:
            t0 = _sync_clock(dev)
            if k >= len(given["n"]) or int(given["n"][k]) != int(n):
                raise ValueError(f"plan decisions: level {k} has {n} cells, the decisions "
                                 f"{given['n'][k] if k < len(given['n']) else 'none'}")
            if need_colour:
                stored = given["colour"][k] if k < len(given["colour"]) else None
                if stored is None or np.asarray(stored).shape != (n,):
                    raise ValueError(f"plan decisions: no colouring of the {n} cells of level {k}")
                colour = np.asarray(stored).astype(np.int64)
                # a colouring is only used after it is checked on this very pattern (GS reads it as independent sets)
                bad = colouring_conflicts(Ab.row, Ab.col, torch.as_tensor(colour, device=dev))
                if bad or (colour.size and int(colour.min()) < 0):
                    raise ValueError(f"plan decisions: the stored colouring of level {k} is not valid on this "
                                     f"pattern ({bad} conflicting blocks)")
            self.setup_seconds["checks"] += _sync_clock(dev) - t0
            self.decisions["colour"].append(None if colour is None else colour.astype(np.int32))
            return None, colour
        t0 = _sync_clock(dev)
        r = c = s = None
        pairwise = str(self._agg_of(k)).startswith(("pairwise", "mis2"))   # device aggregation (PureGPU 1-2)
        if self.value_free or self.gpu_setup or pairwise:
            if getattr(self, "strength_fn", None) is not None and not self.value_free:
                r, c, s = self.strength_fn(Ab.row, Ab.col, n, Ab.val, k)          # O1-B: an alternative rule
            else:
                r, c, s = strength_on_device(Ab.row, Ab.col, n, None if self.value_free else Ab.val, self.theta)
        # Pairwise / MIS-2 aggregation reads the device graph -- no host copy for it
        need_host = (not last and not pairwise) or (need_colour and not self.gpu_setup)
        self._dev_strength = (r, c, s) if pairwise and not last else None
        if need_host:
            S = (strength_to_host(r, c, s, n) if r is not None else block_strength(Ab, self.theta))
        self.setup_seconds["strength"] += _sync_clock(dev) - t0
        if need_colour:
            t0 = _sync_clock(dev)
            if self.gpu_setup:
                colour = jones_plassmann_colouring(r, c, n).cpu().numpy()
            else:
                colour = greedy_colouring(S)
            self.setup_seconds["colouring"] += _sync_clock(dev) - t0
        self.decisions["colour"].append(None if colour is None else colour.astype(np.int32))
        return S, colour

    def _m2_aggregate(self, k: int, S, n: int, given) -> np.ndarray:
        """Level ``k``'s cell -> aggregate map (int64): the given decision (checked: every aggregate non-empty) or
        pyamg on ``S``."""
        t0 = time.perf_counter()
        if given is not None:
            stored = given["agg"][k] if k < len(given["agg"]) else None
            if stored is None or np.asarray(stored).shape != (n,):
                raise ValueError(f"plan decisions: no aggregate map for the {n} cells of level {k}")
            agg = np.asarray(stored).astype(np.int64)
            if agg.size and (int(agg.min()) < 0 or np.bincount(agg).min() == 0):
                raise ValueError(f"plan decisions: the aggregate map of level {k} is not a partition onto "
                                 f"0..{int(agg.max())}")
            self.setup_seconds["checks"] += time.perf_counter() - t0
        else:
            aggregation = str(self._agg_of(k))
            if aggregation.startswith("mis2"):          # mis2 / mis2c<cap> / mis2o (PureGPU stages 2-3)
                r, c, _ = self._dev_strength
                cap = int(aggregation[5:]) if aggregation.startswith("mis2c") else None
                order = None
                if aggregation == "mis2o":
                    # the probe colouring registered for this level size (the fine level); coarse levels: random roots
                    reg = _ROOT_ORDER.get(int(n))
                    order = reg if reg is not None and reg.numel() == int(n) else None
                    self.root_ordered = getattr(self, "root_ordered", []) + [order is not None]
                agg = aggregate_mis2_device(r, c, n, cap=cap, order=order).cpu().numpy().astype(np.int64)
                self._dev_strength = None
            elif aggregation.startswith("pairwisea"):
                # "pairwisea<passes>b<beta>" -- passes of pairing gated by the strength ratio
                passes, beta = aggregation[len("pairwisea"):].split("b")
                r, c, s = self._dev_strength
                agg = aggregate_pairwise_device(r, c, s, n, int(passes), beta=float(beta)).cpu().numpy().astype(np.int64)
                self._dev_strength = None
            elif aggregation.startswith("pairwise"):
                passes = int(aggregation[8:] or 3)
                r, c, s = self._dev_strength
                agg = aggregate_pairwise_device(r, c, s, n, passes).cpu().numpy().astype(np.int64)
                self._dev_strength = None
            else:
                agg = aggregate(S, aggregation)
            self.setup_seconds["aggregation"] += time.perf_counter() - t0
        self.decisions["agg"].append(agg.astype(np.int32))
        return agg

    # ------------------------------------------------------------------ helpers
    def _level_setting(self, k: int, key: str):
        """Level ``k``'s ``level_plan`` entry for ``key`` (the last entry for deeper levels), or None."""
        lp = getattr(self, "level_plan", None)
        if not lp:
            return None
        return lp[min(int(k), len(lp) - 1)].get(key)

    def _agg_of(self, k: int) -> str:
        """Level ``k``'s aggregation (``level_plan``'s, else ``coarse_split``'s from its level on)."""
        v = self._level_setting(k, "aggregation")
        if v is not None:
            return str(v)
        split = getattr(self, "coarse_split", None)
        return split[1] if split is not None and k >= split[0] else self.aggregation

    def _smooth_of(self, k: int) -> bool:
        """Whether level ``k``'s P / R are smoothed (``level_plan``'s, else ``coarse_split``'s from its level on)."""
        v = self._level_setting(k, "smooth")
        if v is not None:
            return bool(v)
        split = getattr(self, "coarse_split", None)
        return split[2] if split is not None and k >= split[0] else self.smooth_prolongation

    def _coarse_max_of(self, k: int) -> int:
        """The coarsest-level size test of level ``k`` (``level_plan``'s "coarse_max", else the plan's)."""
        v = self._level_setting(k, "coarse_max")
        return self.coarse_max if v is None else int(v)

    def _offload(self, spgemm: "SpGEMM") -> "SpGEMM":
        """Where a lean plan keeps a product's triples: pinned host memory (``SpGEMM.to_host``, M5), or -- W6, with
        ``WARP_NUMERIC["on"]`` on a ``fused_numeric`` plan, whose every numeric pass is a Warp product or block sum --
        the Warp segments on the device (``SpGEMM.to_warp``).  The choice is made when the plan is built."""
        if WARP_NUMERIC["on"] and getattr(self, "fused_numeric", False) and spgemm.out is not None \
                and spgemm.out.is_cuda:
            return spgemm.to_warp()
        return spgemm.to_host()

    def l1_march_key(self) -> tuple:
        """The hierarchy a march-level l1 decision belongs to (``L1_MARCH``): the block size and the level sizes."""
        return (int(getattr(self, "F", 0) or 0), tuple(int(L.n) for L in self.levels))

    # the plan settings that decide the symbolic data of a value-based plan's finest level (``level0_from``)
    _SYMBOLIC = ("smoother", "restriction", "smooth_prolongation", "coarse_max", "max_levels", "theta", "aggregation",
                 "strength", "gpu_setup", "fused_numeric", "lean", "colour_coarsest", "F")

    def _level0_donor(self, donor, A):
        """``donor``'s finest level when it may be lent to this plan (``level0_from``): a value-based host-setup plan
        of ``A``'s block pattern with the same symbolic settings and no per-level options; else None.  Under the
        operator-decided l1 rule (``adaptive_l1``) only an l1 plan borrows: the rule switches levels of a plan that is
        not l1 already, and an l1 plan's levels are all l1, so the rule decides nothing on the borrower; the lent
        level is the borrower's own copy (``_replace``), so the donor's decisions do not reach it either."""
        if not isinstance(donor, CoupledAMGPlan) or not donor.levels or donor is self:
            return None
        if self.value_free or self.gpu_setup or getattr(donor, "value_free", False) or getattr(donor, "gpu_setup", False):
            return None
        if any(getattr(self, k, None) != getattr(donor, k, None) for k in self._SYMBOLIC):
            return None
        if any(getattr(p, k, None) is not None for p in (self, donor)
               for k in ("coarse_split", "level_plan", "near_null", "strength_fn")):
            return None
        if (any(float(getattr(p, "adaptive_l1", 0.0) or 0.0) > 0 for p in (self, donor))
                and not getattr(self, "l1", False)):
            return None
        L0 = donor.levels[0]
        if int(L0.n) != int(A.shape[0]):
            return None
        same = ((L0.row is A.row or (L0.row.shape == A.row.shape and torch.equal(L0.row, A.row)))
                and (L0.col is A.col or (L0.col.shape == A.col.shape and torch.equal(L0.col, A.col))))
        return L0 if same else None

    def _pattern_level(self, row, col, n) -> LevelPlan:
        diag = torch.full((n,), -1, dtype=torch.long, device=row.device)
        hit = torch.nonzero(row == col).reshape(-1)
        diag[row[hit]] = hit
        if bool((diag < 0).any()):
            raise ValueError(f"{int((diag < 0).sum())} block rows have no diagonal block")
        t_order, t_row, t_col = _transpose_plan(row, col, n, n)
        return LevelPlan(n=n, row=row, col=col, diag=diag, t_order=t_order, t_row=t_row, t_col=t_col,
                         l1=getattr(self, "l1", False), diag_eps=getattr(self, "diag_eps", 0.0))

    def _smoother_diagonal(self, L: LevelPlan, val: torch.Tensor) -> torch.Tensor:
        # the body is the module-level ``smoother_diagonal``; the l1 flag and diag_eps
        # are the level's own copies of ``self.l1`` / ``self.diag_eps`` (set in ``_pattern_level``)
        return smoother_diagonal(val, L)

    def _prolongation_omega(self, L: LevelPlan, val: torch.Tensor, Dinv: torch.Tensor | None = None) -> float:
        """``4/3 / rho(D^-1 A)`` of level ``L`` at the classical level values ``val`` (power iteration, fixed seed).

        The body of the constructor's estimate lives here so that
        ``numeric(refresh_omega=True)`` on a reused plan computes exactly what a fresh build computes.
        ``fused_numeric``: ``Dinv`` -- the same inverse, computed once by ``numeric`` for this level
        and its numeric pass -- and the products through torch's BSR mat-vec on the GPU (``_bsr_matvec``: the 20
        einsum block products were ~1 s of a 128^3 numeric pass); rounding-level differences."""
        Ab = BlockCSR(L.row, L.col, val, (L.n, L.n))
        if Dinv is None:
            Dinv = _safe_block_inverse(self._smoother_diagonal(L, val))
        warp_da = None
        if WARP_NUMERIC["omega"] and val.is_cuda and val.dtype == torch.float64 and not (
                torch.is_grad_enabled() and val.requires_grad):
            # W6 (opt-in, --camg-warp-omega): D^-1 A in one Warp kernel per product (block_amg_warp.dinv_a)
            from Pipeline.CoupledNewton import block_amg_warp as _bw
            warp_da = _bw.dinv_a(L, val, Dinv)
        mv = _bsr_matvec(L, val) if (warp_da is None and getattr(self, "fused_numeric", False)) else None
        if warp_da is not None:
            DinvA = warp_da
        elif mv is not None:
            DinvA = lambda v: torch.einsum("nfg,ng->nf", Dinv, mv(v))
        else:
            DinvA = lambda v: torch.einsum("nfg,ng->nf", Dinv, Ab.matvec(v))
        extra = {"device_norm": True} if (WARP_NUMERIC.get("omega_device") and torch.device(self.device).type == "cuda"
                                          and not (torch.is_grad_enabled() and val.requires_grad)) else {}
        F_level = self.F if getattr(self, "near_null", None) is None else int(val.shape[1])
        return (4.0 / 3.0) / max(spectral_radius_estimate(DinvA, L.n, F_level, dtype=self.dtype,
                                                          device=self.device, **extra), 1e-12)

    def _level_numeric(self, L: LevelPlan, val: torch.Tensor, mods: dict, Dinv: torch.Tensor | None = None) -> dict:
        F, dtype, device = self.F, val.dtype, val.device
        p0 = getattr(L, "p0", None)          # near-null tentative blocks; None: the identity
        if getattr(self, "near_null", None) is not None:
            F = int(val.shape[1])
        I = _eye(F, dtype, device)
        if Dinv is None:
            Dinv = _safe_block_inverse(self._smoother_diagonal(L, val))
        if mods.get("lam") is not None:
            # Per-row stabilisation (absent = off): the SMOOTHER inverts the per-row stabilised diagonal
            # D_s + diag(lam * r) (``stabilized_diagonal``); P / R keep Dinv, so the hierarchy is the classical one
            Sbase = _safe_block_inverse(stabilized_diagonal(self._smoother_diagonal(L, val), val, L, mods["lam"]))
        else:
            Sbase = Dinv
        Sinv = Sbase if mods.get("psi") is None else torch.bmm(I + mods["psi"], Sbase)
        out = {"A": BlockCSR(L.row, L.col, val, (L.n, L.n)), "Sinv": Sinv}
        if mods.get("onehop") is None and self.smoother == "spai" and L.agg is not None:
            out["W"] = BlockCSR(L.row, L.col, block_spai(out["A"], omega=self.spai_omega), (L.n, L.n))
        if mods.get("onehop") is not None:
            # A one-hop block smoother on A's pattern (a sparse approximate inverse); fully parallel.
            out["W"] = BlockCSR(L.row, L.col, mods["onehop"], (L.n, L.n))
        if L.agg is None:
            return out
        if getattr(self, "_numeric_timer", None) is not None:
            self._numeric_timer["_t"] = _sync_clock(device)        # M2: the P / R smoothing starts here
        lean = (getattr(self, "lean", False) and mods.get("theta_p") is None and mods.get("theta_r") is None
                and not (torch.is_grad_enabled() and (val.requires_grad or Dinv.requires_grad)))
        fused = (getattr(self, "fused_numeric", False) and mods.get("theta_p") is None and mods.get("theta_r") is None
                 and not (torch.is_grad_enabled() and (val.requires_grad or Dinv.requires_grad)))
        Rval = None
        # A coarse_split plan says per level whether P / R are smoothed (LevelPlan.smooth)
        smooth = self.smooth_prolongation if getattr(L, "smooth", None) is None else bool(L.smooth)
        if smooth and fused:
            # Fused numeric: D^-1 A P0 = D^-1 (A P0) row by row, and A P0 is a pure block aggregation (P0 holds one
            # identity block per row): the A blocks are SUMMED into the P pattern first (``_block_sums``, no product)
            # and each P block takes ONE bmm with its row's D^-1 -- |P| products instead of 2 nnzb (D^-1 A_ij, then x I).
            # Likewise R: (D^-T A^T P0)^T has the blocks (sum_{j in a} A_ji) D_i^-1, formed directly in R's block
            # order (no transposed copies).  The sums run in another order: rounding-level differences.
            T = _block_sums(L.plan_AP0, val)
            Pval = L.p_tent[:, None, None].to(dtype) * I - L.omega * torch.bmm(Dinv[L.plan_AP0.row], T)
            del T
            if self.restriction == "pg":
                Tt = _block_sums(L.plan_AtP0, val, L.t_order)        # (i, a): sum_{j in a} A_ji, untransposed
                rt_row = L.plan_AtP0.row
                Rval = (L.rt_tent[:, None, None].to(dtype) * I
                        - L.omega * torch.bmm(Tt, Dinv[rt_row]))[L.r_order]
                del Tt
                RTval = None
            else:
                RTval = Pval
        elif smooth and lean:
            # Lean numeric: D^-1 A and D^-T A^T enter only the tentative-prolongation products, so
            # they are computed there chunk by chunk (SpGEMM.fused) -- the same bmm on the same blocks -- instead
            # of as whole nnzb x F x F arrays (each built from two gathered copies of that size); S and St go as
            # soon as P and R^T are formed.  No autograd here (an inference build), no learned theta_p / theta_r.
            ones = I.expand(L.n, F, F)
            Pval = L.p_tent[:, None, None].to(dtype) * I - L.omega * L.plan_AP0.fused(
                lambda idx: torch.bmm(Dinv[L.row[idx]], val[idx]), ones, F).val
            if self.restriction == "pg":
                DinvT = Dinv.transpose(1, 2)
                RTval = L.rt_tent[:, None, None].to(dtype) * I - L.omega * L.plan_AtP0.fused(
                    lambda idx: torch.bmm(DinvT[_on_device(L.t_row[idx], device)],
                                          val[_on_device(L.t_order[idx], device)].transpose(1, 2)), ones, F).val
            else:
                RTval = Pval
        elif smooth and p0 is not None:
            # Near-null tentative blocks P0_i (rigid-body modes): P = P0 - w D^-1 A P0 and R^T = P0 - w D^-T A^T P0;
            # the P blocks at (i, agg(i)) start from P0_i instead of the identity
            N = torch.bmm(Dinv[L.row], val)                                   # D_i^-1 A_ij
            if mods.get("theta_p") is not None:
                N = torch.bmm(I + mods["theta_p"], N)
            S = L.plan_AP0(N, p0).val
            Pval = L.p_tent[:, None, None].to(dtype) * p0[L.plan_AP0.row] - L.omega * S
            if self.restriction == "pg":
                Nt = torch.bmm(Dinv.transpose(1, 2)[L.t_row], val[L.t_order].transpose(1, 2))
                if mods.get("theta_r") is not None:
                    Nt = torch.bmm(I + mods["theta_r"], Nt)
                St = L.plan_AtP0(Nt, p0).val
                RTval = L.rt_tent[:, None, None].to(dtype) * p0[L.plan_AtP0.row] - L.omega * St
            else:
                RTval = Pval
        elif smooth:
            N = torch.bmm(Dinv[L.row], val)                                   # D_i^-1 A_ij
            if mods.get("theta_p") is not None:
                N = torch.bmm(I + mods["theta_p"], N)
            ones = I.expand(L.n, F, F)
            S = L.plan_AP0(N, ones).val
            Pval = L.p_tent[:, None, None].to(dtype) * I - L.omega * S
            if self.restriction == "pg":
                Nt = torch.bmm(Dinv.transpose(1, 2)[L.t_row], val[L.t_order].transpose(1, 2))
                if mods.get("theta_r") is not None:
                    Nt = torch.bmm(I + mods["theta_r"], Nt)
                St = L.plan_AtP0(Nt, ones).val
                RTval = L.rt_tent[:, None, None].to(dtype) * I - L.omega * St
            else:
                RTval = Pval
        elif p0 is not None:
            Pval = p0
            RTval = Pval
        else:
            Pval = I.expand(L.n, F, F)
            RTval = Pval
        if Rval is None:
            Rval = RTval[L.r_order].transpose(1, 2)
        if lean or fused:
            del RTval                      # M5: R's values are the gathered copy above; R^T's go now
        timer = getattr(self, "_numeric_timer", None)      # Set by a timed numeric pass
        if timer is not None:
            t0 = _sync_clock(device)
            timer["smooth_PR"] = timer.get("smooth_PR", 0.0) + t0 - timer.pop("_t", t0)
        ua = getattr(L, "ua_target", None)
        if ua is not None and not smooth and not (torch.is_grad_enabled() and val.requires_grad):
            # Unsmoothed aggregation -- P and R = P^T hold identity blocks, so the Galerkin
            # block (I, J) is the SUM of the fine blocks A_ij with agg(i) = I, agg(j) = J: one scatter-add along the
            # plan-time index ``ua_target`` instead of the two block SpGEMMs (same values, another summation order)
            next_val = torch.zeros((L.plan_RAP.row.numel(), F, F), dtype=val.dtype, device=val.device).index_add_(
                0, ua, val)
            if timer is not None:
                timer["RAP"] = timer.get("RAP", 0.0) + _sync_clock(device) - t0
        else:
            AP = L.plan_AP(val, Pval)
            if timer is not None:
                t1 = _sync_clock(device)
                timer["AP"] = timer.get("AP", 0.0) + t1 - t0
            next_val = L.plan_RAP(Rval, AP.val).val
            if timer is not None:
                timer["RAP"] = timer.get("RAP", 0.0) + _sync_clock(device) - t1
        out.update(P=BlockCSR(L.plan_AP0.row if smooth else torch.arange(L.n, device=device),
                              L.plan_AP0.col if smooth else L.agg, Pval, (L.n, L.n_c)),
                   R=BlockCSR(L.r_row, L.r_col, Rval, (L.n_c, L.n)), next=next_val)
        return out

    def numeric(self, val: torch.Tensor, modulator=None, gauge_field=None, refresh_omega: bool = False) -> list:
        """Level values for fine block values ``val``; ``modulator(k, A, plan) -> mods``.

        ``refresh_omega`` (default off): first set every aggregated
        level's ``omega`` for THESE values, as the constructor sets it for the values it was built from -- from the
        CLASSICAL level values (``_prolongation_omega``), whatever the modulator does to P and R.  A plan reused
        across linearisations needs it: ``omega`` scales the smoothed prolongation and the build-time value is
        stale for a new Jacobian.  The classical coarse values are the ones this pass computes as long as no level
        modulates P or R (``onehop`` / ``psi`` heads, or no modulator); a ``theta_p`` / ``theta_r`` level above an
        aggregated level costs one extra classical Galerkin pass for that level's ``omega``.  The plan's ``omega`` is
        overwritten in place: hierarchies built earlier keep their values (``omega`` enters them only through this
        pass), only their ``report()['omega_p']`` reads the plan's current one.

        ``gauge_field``: the field index (0 <= g < F) whose constant mode the coarsest solve pins, or None (note:
        an index outside the block, e.g. the legacy pressure index 3 on a scalar F = 1 system, is refused -- it would
        pin another cell)."""
        if gauge_field is not None and not 0 <= int(gauge_field) < self.F:
            raise ValueError(f"gauge_field {gauge_field} is not a field of an F={self.F} system")
        if (L1_MARCH["on"] and getattr(self, "adaptive_l1", 0.0) > 0 and not getattr(self, "_l1_decided", False)
                and self.l1_march_key() in L1_MARCH["decisions"]):
            for k in L1_MARCH["decisions"][self.l1_march_key()]:       # the march's first decision
                self.levels[k].l1 = True
            self._l1_decided = True
            self._flagged = {}
        # M2a: a value-free plan has no omega of its own; it is computed for these values, as a refresh does
        refresh_omega = bool(refresh_omega) or getattr(self, "value_free", False)
        fused = getattr(self, "fused_numeric", False)
        timed = (getattr(self, "value_free", False) or getattr(self, "gpu_setup", False) or fused
                 or NUMERIC_TIMERS["on"])
        if timed:
            # The numeric pass's seconds by part, read by the camg factory's log
            self.numeric_seconds = {"omega": 0.0, "levels": 0.0, "coarse_lu": 0.0}
            t_last = _sync_clock(val.device)
        self._numeric_timer = self.numeric_seconds if timed else None
        values = []
        cval = val                          # classical values of level k (refresh_omega): omega's input
        for k, L in enumerate(self.levels):
            dinv = None
            if (fused and refresh_omega and L.agg is not None and cval is val and modulator is None
                    and not (torch.is_grad_enabled() and val.requires_grad)):
                # M2c: one smoother-diagonal inverse for the omega estimate and the level's numeric pass (the same
                # function of the same values: identical)
                dinv = _safe_block_inverse(self._smoother_diagonal(L, val.detach()))
            if refresh_omega and L.agg is not None:
                L.omega = self._prolongation_omega(L, cval.detach(), Dinv=dinv)
            if timed:
                t_now = _sync_clock(val.device)
                self.numeric_seconds["omega"] += t_now - t_last
                t_last = t_now
            A = BlockCSR(L.row, L.col, val, (L.n, L.n))
            if (getattr(self, "adaptive_l1", 0.0) > 0 and modulator is not None and L.agg is not None
                    and not getattr(L, "l1", False) and not getattr(self, "_l1_decided", False)):
                # The operator-decided l1 rule with a learned cycle: decided from this level's values before the
                # network reads the level, by the same test as the classical pass below (the damped block Jacobi on the
                # raw diagonal).  With modulations of the smoother only (one-hop W, psi) the level values are the
                # classical ones, so both arms switch the same levels, and the network's W and features then sit on the
                # l1 diagonal the level's smoother inverts (``w_diagonal="smoother"``: Theta = 0 is the l1 block Jacobi)
                with torch.no_grad():
                    vd = val.detach()
                    rho = _jacobi_rho(BlockCSR(L.row, L.col, vd, (L.n, L.n)),
                                      _safe_block_inverse(self._smoother_diagonal(L, vd)), self.adaptive_l1_omega)
                if getattr(self, "_radii", None) is not None:
                    self._radii[k] = rho
                if rho > self.adaptive_l1:
                    if getattr(self, "adaptive_l1_gate", 0.0) > 0:
                        self._flagged[k] = rho                  # the gate decides (CoupledAMG._l1_gate)
                    else:
                        L.l1 = True
                        self.adaptive_l1_log.append((k, rho))
            if timed and modulator is not None:
                t_mod = _sync_clock(val.device)
            mods = modulator(k, A, L) if modulator is not None else {}
            if timed and modulator is not None:
                # The network / W-assembly share of "levels" (it stays inside "levels")
                self.numeric_seconds["modulator"] = (self.numeric_seconds.get("modulator", 0.0)
                                                     + _sync_clock(val.device) - t_mod)
            if "_theta" in mods:
                # the network's raw edge output (read by ThetaReuse, never by the cycle): a fresh dict without it, so
                # this pass does not hold another [nnzb, F, F] tensor (3.3 GB at DHC88) through the level's Galerkin
                # products; W, built from it, is unchanged
                mods = {key: value for key, value in mods.items() if key != "_theta"}
            out = self._level_numeric(L, val, mods) if dinv is None else self._level_numeric(L, val, mods, Dinv=dinv)
            del dinv
            if (getattr(self, "adaptive_l1", 0.0) > 0 and L.agg is not None and not getattr(L, "l1", False)
                    and not mods and not (torch.is_grad_enabled() and val.requires_grad)
                    and not getattr(self, "_l1_decided", False)):
                rho = _jacobi_rho(out["A"], out["Sinv"], self.adaptive_l1_omega)
                if getattr(self, "_radii", None) is not None:
                    self._radii[k] = rho
                if rho > self.adaptive_l1 and getattr(self, "adaptive_l1_gate", 0.0) > 0:
                    self._flagged[k] = rho                      # the gate decides (CoupledAMG._l1_gate)
                elif rho > self.adaptive_l1:
                    # O1-B R1d: this level's smoother amplifies -> the l1 diagonal from now on (the level's values again)
                    L.l1 = True
                    self.adaptive_l1_log.append((k, rho))
                    out = self._level_numeric(L, val, mods)
            if timed:
                t_now = _sync_clock(val.device)
                self.numeric_seconds["levels"] += t_now - t_last
                t_last = t_now
            if (refresh_omega and L.agg is not None and k + 1 < len(self.levels)
                    and self.levels[k + 1].agg is not None):          # the next level has an omega to refresh
                if cval is val and mods.get("theta_p") is None and mods.get("theta_r") is None:
                    cval = out["next"]
                else:
                    with torch.no_grad():
                        cval = self._level_numeric(L, cval.detach(), {})["next"]
            lv = LevelValues(A=out["A"], Sinv=out["Sinv"], P=out.get("P"), R=out.get("R"), W=out.get("W"))
            if L.agg is None:
                M = lv.A.to_dense()
                if gauge_field is not None:
                    pin = torch.zeros_like(M)
                    pin[gauge_field, gauge_field] = M.diagonal().abs().max().detach()
                    M = M + pin
                lv.dense_lu = dense_lu(M, "coarsest operator")
                if timed:
                    self.numeric_seconds["coarse_lu"] += _sync_clock(val.device) - t_last
                values.append(lv)
                break
            values.append(lv)
            val = out["next"]
        self._numeric_timer = None
        return values


# ======================================================================= fast kernels
# Fast kernels (docs/research/2026-10-04-gpu-vs-openfoam-feasibility.md),
# opt-in: ``CoupledAMG(fast_kernels=True)``, ``solve_with_coupled_newton --camg-fast-kernels``,
# gpu_bench ``+fk``.  Measured (experiments/NaturalConvection/CoupledNewton/PaperBench/Logger/round25/summary.md) on the DHC88 finest level
# (n 681472, F 5, 16.4M blocks): ``BlockCSR.matvec`` -- gather + einsum lowered to a batched 5 x 5 GEMM +
# index_add -- 15.3 ms where the same product as a scalar CSR (cuSPARSE) costs 1.65 ms and as BSR 2.67 ms; one
# multicolour GS sweep 19.9 ms where per-colour pre-gathered data cost 6.5 ms.  Every block matrix the cycle
# applies (level matrices, learned one-hop W, P, R, smoother inverses, the trouble-set rows) becomes a scalar CSR
# in a per-level vector layout that makes each GS colour one contiguous row slice; BSR is not used because torch's
# CPU kernel for it needs MKL, which the aarch64 build lacks.

_ALIGN = 4                  # scalar-CSR group chunks start at multiples of 4 entries: 16 bytes for int32 and float64
_CSR_INT32_OK: dict = {}    # device type -> torch's sparse CSR mat-vec accepts int32 indices there
_INT32_LIMIT = 2 ** 31 - 1  # an index or offset at or above this needs int64 (a module constant so tests can shrink it)


def _csr_index_dtype(device, *extents) -> torch.dtype:
    """int32 when every index and offset fits and torch's CSR mat-vec accepts int32 indices on this device type
    (probed once per process on a 2 x 2 matrix), else int64."""
    if max(extents) >= _INT32_LIMIT:
        return torch.int64
    kind = torch.device(device).type
    if kind not in _CSR_INT32_OK:
        try:
            crow = torch.tensor([0, 1, 2], dtype=torch.int32, device=device)
            col = torch.tensor([1, 0], dtype=torch.int32, device=device)
            M = torch.sparse_csr_tensor(crow, col, torch.tensor([2.0, 3.0], dtype=torch.float64, device=device),
                                        size=(2, 2))
            y = M @ torch.tensor([5.0, 7.0], dtype=torch.float64, device=device)
            _CSR_INT32_OK[kind] = y.cpu().tolist() == [14.0, 15.0]
        except (RuntimeError, NotImplementedError, TypeError):
            _CSR_INT32_OK[kind] = False
    return torch.int32 if _CSR_INT32_OK[kind] else torch.int64


class FastLayout:
    """Vector layout of one hierarchy level in the fast kernels (E3).

    The level's ``n`` cells are split into groups -- its multicolour GS colours, or ONE group of all cells in
    natural order -- that occupy consecutive ranges of the flat vector, field-major inside a group: the
    ``local(i)``-th cell ``i`` of group ``c``, field ``f``, sits at ``F off_c + f n_c + local(i)`` =
    ``base[i] + f stride[i]``.  A colour's unknowns are then ONE contiguous slice (a GS update is an in-place slice
    add, no scatter), and the one-group layout is the solver's field-blocked vector itself.  ``to_layout`` /
    ``from_layout`` convert from / to field-blocked (``"fb"``) or cell-interleaved ``[n, F]`` (``"ci"``) flat
    vectors: gathers with cached indices, or the vector itself / a transpose in the one-group layout."""

    def __init__(self, n: int, F: int, groups=None, device="cpu"):
        self.n, self.F = int(n), int(F)
        self.natural = groups is None
        if groups is None:
            groups = [torch.arange(self.n, device=device)]
        self.groups = [torch.as_tensor(g, device=device).long().reshape(-1) for g in groups]
        self.sizes = [int(g.numel()) for g in self.groups]
        self.offsets = [0]
        for s in self.sizes:
            self.offsets.append(self.offsets[-1] + s)
        if self.offsets[-1] != self.n:
            raise ValueError(f"layout groups hold {self.offsets[-1]} cells, the level has {self.n}")
        cells = torch.cat(self.groups)
        self.rank = torch.full((self.n,), -1, dtype=torch.long, device=device)      # cell -> position in group order
        self.rank[cells] = torch.arange(self.n, device=device)
        if self.n and bool((self.rank < 0).any()):
            raise ValueError("layout groups do not partition the cells")
        size_t = torch.as_tensor(self.sizes, dtype=torch.long, device=device)
        off_t = torch.as_tensor(self.offsets[:-1], dtype=torch.long, device=device)
        size_q, off_q = torch.repeat_interleave(size_t, size_t), torch.repeat_interleave(off_t, size_t)
        base_q = self.F * off_q + (torch.arange(self.n, device=device) - off_q)
        self.base, self.stride = base_q[self.rank], size_q[self.rank]                 # per cell
        self.slices = [(self.F * a, self.F * b) for a, b in zip(self.offsets[:-1], self.offsets[1:])]
        self._index = {}

    def positions(self, cells: torch.Tensor, order: str = "fb") -> torch.Tensor:
        """Flat layout positions of ``cells`` x fields, ``[F, len]`` (``"fb"``) or ``[len, F]`` (``"ci"``)."""
        f = torch.arange(self.F, device=self.base.device)
        b, s = self.base[cells], self.stride[cells]
        return b[None, :] + f[:, None] * s[None, :] if order == "fb" else b[:, None] + f[None, :] * s[:, None]

    def _gather(self, kind: str, direction: str) -> torch.Tensor:
        idx = self._index.get((kind, direction))
        if idx is None:
            pos = self.positions(torch.arange(self.n, device=self.base.device), kind).reshape(-1)
            if direction == "out":                          # vec_kind = layout[pos]
                idx = pos
            else:                                           # layout = vec_kind[inverse]
                idx = torch.empty_like(pos)
                idx[pos] = torch.arange(pos.numel(), device=pos.device)
            self._index[(kind, direction)] = idx
        return idx

    def to_layout(self, vec: torch.Tensor, kind: str = "fb") -> torch.Tensor:
        if self.natural:
            return vec if kind == "fb" else vec.view(self.n, self.F).t().reshape(-1)
        return vec.index_select(0, self._gather(kind, "in"))

    def from_layout(self, vec: torch.Tensor, kind: str = "fb") -> torch.Tensor:
        if self.natural:
            return vec if kind == "fb" else vec.view(self.F, self.n).t().reshape(-1)
        return vec.index_select(0, self._gather(kind, "out"))

    def tensors(self) -> list:
        """The tensors this layout allocated (memory report)."""
        own = [self.rank, self.base, self.stride] + list(self._index.values())
        return own + (self.groups if self.natural else [])


def _index_select_into(src: torch.Tensor, index: torch.Tensor, dst: torch.Tensor) -> None:
    """``dst = src.index_select(1, index)`` without a temporary when ``out=`` is accepted."""
    try:
        torch.index_select(src, 1, index, out=dst)
    except RuntimeError:
        dst.copy_(src.index_select(1, index))


class FastPattern:
    """Symbolic scalar CSR of a block pattern ``(row[e], col[e])`` with ``F x G`` blocks, rows in layout ``rows``
    and columns in layout ``cols`` (``FastLayout``).  Built once per pattern (cached on the plan level), filled by
    ``fill(val)`` once per numeric phase.

    Value order: row group c of ``rows`` owns one chunk of the arrays, laid out ``[F, nb_c, G]`` (row field, the
    group's blocks in (row rank, column rank) order, column field), so scalar row (c, f, l) holds field f of the
    blocks of the group's l-th row and a fill is one ``index_select`` of ``val.permute(1, 0, 2)`` per group: no
    per-entry index is stored.  Column indices are the column layout's positions; inside a row they follow the
    block order, not the position order (cuSPARSE SpMV and torch's CPU kernel accept unsorted rows; the tensors are
    built with ``check_invariants=False``).  A chunk is padded with explicit zeros to a multiple of ``_ALIGN``
    entries, so every row-group slice starts 16-byte aligned; a pad entry closes the group's last scalar row and
    repeats a column that row reads, so it adds an exact 0.  ``local_groups`` (the block-diagonal smoother
    inverses, ``rows is cols``) also keeps per-group column indices relative to the group's own slice."""

    def __init__(self, row: torch.Tensor, col: torch.Tensor, F: int, G: int, rows: FastLayout, cols: FastLayout, *,
                 local_groups: bool = False, int32_groups: bool = False, host_order: bool = False):
        device = row.device
        self.F, self.G = F, G = int(F), int(G)
        FG = F * G
        if rows.F != F or cols.F != G:
            # the row pointers below use F with the row layout's offsets, the columns G with the column layout's
            # strides: a block shape that is not the layouts' field count would scatter silently
            raise ValueError(f"{F}x{G} blocks on layouts of {rows.F} (rows) and {cols.F} (columns) fields")
        self.rows, self.cols = rows, cols
        rr, cr = rows.rank[row], cols.rank[col]
        order = torch.argsort(rr * max(cols.n, 1) + cr)
        counts = torch.bincount(rr, minlength=rows.n)
        ptr = torch.zeros(rows.n + 1, dtype=torch.long, device=device)
        ptr[1:] = torch.cumsum(counts, 0)
        ng = len(rows.sizes)
        E = ptr[torch.as_tensor(rows.offsets, dtype=torch.long, device=device)].tolist()
        self.block_ranges = [(E[c], E[c + 1]) for c in range(ng)]
        self.lens = [(E[c + 1] - E[c]) * FG for c in range(ng)]
        # no padding for block-diagonal local groups: with F = 5 a one-cell group is a dense
        # 5x5 matrix of 25 entries, and 3 pad entries made nnz (28) exceed rows x cols, which cusparseCreateCsr rejects;
        # the 16-byte alignment is a speed nicety, element alignment (all cuSPARSE needs) holds for any start
        self.pads = [0 if local_groups else (-m) % _ALIGN for m in self.lens]
        # nor where the padded matrix would hold more entries than its dense size: a tiny,
        # fully dense coarse R (25 x 65 = 1625 entries, padded to 1628) made cusparseCreateCsr refuse it ("nnz >
        # matrix size") on the first fast cycle.  Only such patterns -- ones the GPU refused -- lose their padding;
        # every other pattern keeps its layout.  (A dense GROUP of a pattern that fits is handled where
        # its group CSR is made, ``FastMatrix.group``.)
        if sum(self.lens) + sum(self.pads) > rows.n * F * cols.n * G:
            self.pads = [0] * len(self.lens)
        self.starts = [0]
        for m, p in zip(self.lens, self.pads):
            self.starts.append(self.starts[-1] + m + p)
        self.total = self.starts[-1]
        self.shape = (rows.n * F, cols.n * G)
        idt = _csr_index_dtype(device, self.total + 1, self.shape[0] + 1, self.shape[1] + 1)
        # ``int32_groups`` (opt-in): a pattern with more than 2^31 - 1 entries (the fine level of a 152^3
        # mesh: 2.17e9) cannot have an int32 WHOLE-matrix row pointer, and the int64 fallback above doubles its column
        # array (8.7 GB there).  Every group CSR (rows of one colour) still fits: keep int32 columns and per-group int32
        # row pointers, and no whole-matrix CSR (``split``; ``FastMatrix.mv`` concatenates the group products).  Only a
        # pattern that falls back to int64 changes; every other keeps its layout.
        self.split = False
        if int32_groups and idt == torch.int64 and _csr_index_dtype(device, 1) == torch.int32:
            widest = max((m + p for m, p in zip(self.lens, self.pads)), default=0)
            if max(widest + 1, self.shape[1] + 1) < _INT32_LIMIT:
                idt, self.split = torch.int32, True
        # row pointers: scalar row F off_c + f n_c + l (row rank q = off_c + l of group c) starts at
        # start_c + f nb_c G + (ptr[q] - E_c) G
        size_t = torch.as_tensor(rows.sizes, dtype=torch.long, device=device)
        grp = torch.repeat_interleave(torch.arange(ng, device=device), size_t)
        per = lambda xs: torch.as_tensor(xs, dtype=torch.long, device=device)[grp]
        off_q, n_q, E_q = per(rows.offsets[:-1]), per(rows.sizes), per(E[:-1])
        nb_q, s_q = per([b - a for a, b in self.block_ranges]), per(self.starts[:-1])
        q = torch.arange(rows.n, device=device)
        f = torch.arange(F, device=device)
        p = off_q[:, None] * F + f[None, :] * n_q[:, None] + (q - off_q)[:, None]
        start = s_q[:, None] + f[None, :] * (nb_q * G)[:, None] + ((ptr[:-1] - E_q) * G)[:, None]
        crow = torch.empty(self.shape[0] + 1, dtype=torch.long, device=device)
        crow[p.reshape(-1)] = start.reshape(-1)
        crow[-1] = self.total
        self.crow = None if self.split else crow.to(idt)
        # column indices: the same (block, G) positions for every row field f of a chunk; filled in slabs of
        # 4M blocks so the int64 temporary stays small
        self.col = torch.empty(self.total, dtype=idt, device=device)
        cc = col[order]
        g = torch.arange(G, device=device)
        slab = 1 << 22
        for c, (e0, e1) in enumerate(self.block_ranges):
            s0, m = self.starts[c], self.lens[c]
            if e1 == e0:
                continue
            dst = self.col[s0:s0 + m].view(F, e1 - e0, G)
            for a in range(e0, e1, slab):
                b = min(a + slab, e1)
                pos = cols.base[cc[a:b]][:, None] + g[None, :] * cols.stride[cc[a:b]][:, None]
                dst[:, a - e0:b - e0, :].copy_(pos.unsqueeze(0).expand(F, -1, -1))
            if self.pads[c]:
                self.col[s0 + m:s0 + m + self.pads[c]] = self.col[s0 + m - 1]
        # M5 lean plans (``host_order``): ``order`` (one int64 per block) is read only by ``fill``, once per build and
        # group; it waits in pinned host memory
        self.order = _to_pinned(order) if host_order else order
        if self.split:
            # group-relative row pointers computed from the int64 ones, then int32 (each group's entries fit)
            self.group_crow = [(crow[F * a:F * b + 1] - self.starts[c]).to(idt)
                               for c, (a, b) in enumerate(zip(rows.offsets[:-1], rows.offsets[1:]))]
        else:
            self.group_crow = [self.crow[F * a:F * b + 1] - self.starts[c]
                               for c, (a, b) in enumerate(zip(rows.offsets[:-1], rows.offsets[1:]))]
        del crow
        self.local_col = None
        if local_groups:
            if rows is not cols or F != G:
                raise ValueError("local_groups is for square block-diagonal patterns on one layout")
            self.local_col = [self.col[s:e] - G * a
                              for s, e, a in zip(self.starts[:-1], self.starts[1:], rows.offsets[:-1])]

    @property
    def nnzb(self) -> int:
        return int(self.order.numel())

    def tensors(self) -> list:
        return ([t for t in (self.crow, self.col, self.order) if t is not None] + self.group_crow
                + (self.local_col or []))

    def fill(self, val: torch.Tensor) -> "FastMatrix":
        """The scalar CSR of block values ``val`` (``[nnzb, F, G]``, in the block order the pattern was built from)."""
        if tuple(val.shape) != (self.nnzb, self.F, self.G):
            raise ValueError(f"values of shape {tuple(val.shape)} for a pattern of {self.nnzb} {self.F}x{self.G} "
                             "blocks")
        out = torch.empty(self.total, dtype=val.dtype, device=val.device)
        vT = val.permute(1, 0, 2)
        for c, (e0, e1) in enumerate(self.block_ranges):
            s0, m = self.starts[c], self.lens[c]
            if e1 > e0:
                _index_select_into(vT, _on_device(self.order[e0:e1], val.device),
                                   out[s0:s0 + m].view(self.F, e1 - e0, self.G))
            if self.pads[c]:
                out[s0 + m:s0 + m + self.pads[c]] = 0
        return FastMatrix(self, out)


class FastMatrix:
    """Numeric scalar CSR of a ``FastPattern``: ``mv`` applies it to a flat vector in the pattern's column layout
    and returns one in its row layout; ``group(c)`` is the CSR of row group c (a view of the same arrays; with
    ``local_groups`` it takes the group's own slice as input)."""

    def __init__(self, pattern: FastPattern, values: torch.Tensor):
        self.pattern, self.values = pattern, values
        # a split pattern (M5 int32_groups) has no whole-matrix row pointer: ``mv`` runs group by group
        self.csr = (None if getattr(pattern, "split", False) else
                    torch.sparse_csr_tensor(pattern.crow, pattern.col, values, size=pattern.shape,
                                            check_invariants=False))
        self._groups = {}

    def mv(self, x: torch.Tensor) -> torch.Tensor:
        if self.csr is None:
            # M5 split pattern: the row groups are consecutive row ranges, so the product is their concatenation
            return torch.cat([self.group(c) @ x for c in range(len(self.pattern.lens))])
        return self.csr @ x

    def group(self, c: int) -> torch.Tensor:
        M = self._groups.get(c)
        if M is None:
            P = self.pattern
            s, e = P.starts[c], P.starts[c + 1]
            a, b = P.rows.slices[c]
            if P.local_col is not None:
                M = torch.sparse_csr_tensor(P.group_crow[c], P.local_col[c], self.values[s:e],
                                            size=(b - a, P.rows.sizes[c] * P.G), check_invariants=False)
            elif P.pads[c] and P.lens[c] + P.pads[c] > (b - a) * P.shape[1]:
                # a fully dense group cannot carry its pad entries (cusparseCreateCsr: nnz > rows x cols):
                # this view leaves them out -- they close the group's last row and add exact zeros
                m = P.lens[c]
                crow = P.group_crow[c].clone()
                crow[-1] = m
                M = torch.sparse_csr_tensor(crow, P.col[s:s + m], self.values[s:s + m],
                                            size=(b - a, P.shape[1]), check_invariants=False)
            else:
                M = torch.sparse_csr_tensor(P.group_crow[c], P.col[s:e], self.values[s:e],
                                            size=(b - a, P.shape[1]), check_invariants=False)
            self._groups[c] = M
        return M


@dataclass
class _FastLevel:
    """One level of a fast cycle: its layout and the scalar CSRs of A, the smoother inverses, W, P and R."""
    layout: FastLayout
    sources: tuple
    dense: bool = False
    A: FastMatrix = None
    Sinv: FastMatrix = None
    W: FastMatrix = None
    P: FastMatrix = None
    R: FastMatrix = None
    trouble: dict = None
    blk: "_BlockLevel" = None              # block kernels: the level's sliced-ELL copies instead


@dataclass
class _BlockLevel:
    """One level of a fast cycle in the M3 block kernels (``block_kernels``; ``CoupledAMG(block_kernels=True)``):
    the cell-interleaved rank-order layout and the sliced-ELL copies of A (in the cycle's value dtype), of A in fp64
    (``A64``: the fine level's operator and exact block values; ``A`` itself with fp64 values), the smoother inverses
    (``sinv``, per slice), P and R, and the GS damping as a one-element fp64 tensor."""
    layout: object
    A: object = None
    A64: object = None
    sinv: torch.Tensor = None
    P: object = None
    R: object = None
    omega: torch.Tensor = None
    W: object = None                       # O1-HR-2r item B: the learned one-hop W on A's pattern (block_w)


def _fast_cache(L: LevelPlan) -> dict:
    cache = getattr(L, "fast", None)
    if cache is None:                       # a LevelPlan made before the field existed
        cache = L.fast = {}
    return cache


def _level_layout(plan: "CoupledAMGPlan", k: int) -> FastLayout:
    """Level k's ``FastLayout``: its GS colours as groups, or one natural group on a level without colouring and on
    the coarsest level (dense LU; its colouring is never read, so no per-colour data is built there)."""
    L = plan.levels[k]
    cache = _fast_cache(L)
    lay = cache.get("layout")
    if lay is None:
        # (entry[0]: the rows only, so a W6 lazy colour does not form its block-GS edge list here)
        groups = [entry[0] for entry in L.colours] if (L.colours and L.agg is not None) else None
        lay = cache["layout"] = FastLayout(L.n, plan.F, groups, device=L.row.device)
    return lay


def _cached_pattern(L: LevelPlan, key: str, row, col, build) -> FastPattern:
    """The plan level's pattern ``key`` if it was built from this (row, col), else ``build()`` (and cache it)."""
    cache = _fast_cache(L)
    hit = cache.get(key)
    if hit is not None:
        r0, c0, pat = hit
        if ((r0 is row or (r0.shape == row.shape and torch.equal(r0, row)))
                and (c0 is col or (c0.shape == col.shape and torch.equal(c0, col)))):
            return pat
    pat = build()
    cache[key] = (row, col, pat)
    return pat


def _tensor_version(t: torch.Tensor):
    """``t._version``, or None for a tensor without a version counter (inference-mode tensors)."""
    try:
        return t._version
    except RuntimeError:
        return None


def _shared_fill(L: LevelPlan, key: str, pattern: "FastPattern", val: torch.Tensor) -> "FastMatrix":
    """``pattern.fill(val)``, or the scalar CSR a LIVE cycle on this plan level already filled from the very same value
    tensor.

    The camg entry's classical hand-over arm is built on the learned cycle's fine matrix -- the same ``A.val`` object,
    on the same reused plan -- and each cycle filled its own scalar copy of it: 3.2 GiB at the DHC88 structural
    pattern (16.7M blocks).  A fill is a pure gather of ``val`` into the pattern's slots, so the shared copy holds
    exactly the values this call would write, and every product with it is the product with a fresh copy, bit for
    bit.  The cache holds weak references only (it keeps neither the values nor the copy alive) and matches only the
    same value tensor at the same version counter (never modified in place since) on the same pattern object."""
    cache = _fast_cache(L)
    version = _tensor_version(val)
    hit = cache.get(("fill", key))
    if hit is not None and version is not None:
        val_ref, val_version, fm_ref = hit
        fm = fm_ref()
        if fm is not None and fm.pattern is pattern and val_ref() is val and val_version == version:
            return fm
    fm = pattern.fill(val)
    if version is not None:
        cache[("fill", key)] = (weakref.ref(val), version, weakref.ref(fm))
    return fm


def _fast_sources(V: LevelValues) -> tuple:
    """The value tensors a fast level was filled from (identity-checked before every fast application)."""
    return (V.A.val, V.Sinv, None if V.W is None else V.W.val, None if V.P is None else V.P.val,
            None if V.R is None else V.R.val, None if V.dense_lu is None else V.dense_lu[0])


# ======================================================================= CUDA graph of the fast cycle
# CUDA graph (docs/research/2026-10-07-gpu-optimisation-roadmap.md), opt-in:
# ``CoupledAMG(fast_kernels=True, cuda_graph=True)``, ``solve_with_coupled_newton --camg-cuda-graph``, gpu_bench
# ``+fk+cg``.  ``CoupledAMG._graphed`` has the contract.  Process-wide counters (the entry logs them at the end).
GRAPH_REL_TOL = 1e-12        # a replay further than this from the eager cycle (relative 2-norm) leaves the cycle eager
GRAPH_STATS = {"enabled": True, "captures": 0, "replays": 0, "rejected": 0, "failures": 0, "bitwise": 0,
               "max_rel_diff": 0.0, "pool_mib": 0.0, "last_error": None, "oom_retries": 0, "oom_eager": 0}
_GRAPH_STREAMS: dict = {}    # device -> the persistent side stream every warm-up and capture on it runs on


def _out_of_memory(exc: BaseException) -> bool:
    """True for the device allocator's out-of-memory error (the one failure of a capture that is retried)."""
    return isinstance(exc, torch.cuda.OutOfMemoryError) or "out of memory" in str(exc).lower()


def _graph_stream(device) -> "torch.cuda.Stream":
    """One side stream per device for all captures (as ``torch.cuda.graph``'s default capture stream): the caching
    allocator's blocks freed on it are reused by the next capture's warm-up instead of piling up per stream."""
    key = torch.device(device)
    stream = _GRAPH_STREAMS.get(key)
    if stream is None:
        stream = _GRAPH_STREAMS[key] = torch.cuda.Stream(device=key)
    return stream


def graph_stats() -> dict:
    """A copy of ``GRAPH_STATS``: captures kept, replays, captures rejected by the eager check, capture failures,
    captures whose check was bitwise, the largest replay-vs-eager relative difference, the pools' reserved MiB."""
    return dict(GRAPH_STATS)


class CoupledAMG:
    """Coupled block AMG V-cycle; ``__call__`` takes and returns FIELD-BLOCKED vectors.

    ``gauge_field``: index of the field whose constant mode is free (pinned on the coarsest
    dense solve), or None.  ``gauge``: callable applied to every output (e.g. the solver's
    pressure-mean projection).  ``plan`` / ``modulator``: reuse a symbolic plan and apply
    learned modulations (``LearnedCoupledAMG``); without them this is the classical method.
    ``diag_eps`` (0 = off): singular-value floor of every level's smoother diagonal
    (``guarded_diagonal``); like ``l1`` it belongs to the plan, so a passed ``plan`` decides it.
    ``refresh_omega`` / ``colour_coarsest`` (opt-in): a passed ``plan`` built from an
    earlier matrix of the same block pattern gets its ``omega`` recomputed for ``A`` (``CoupledAMGPlan.numeric``);
    ``colour_coarsest=False`` builds the plan without the coarsest level's unused colouring.  ``plan_kwargs`` /
    ``plan_key`` name the plan these keywords build, for caches that share one plan between cycles.
    ``fast_kernels`` (default False = the block path, bit for bit): applications that record no
    autograd graph (``_fast_eligible``) run the same cycle on scalar CSR copies of every level matrix, W, P, R and
    smoother inverse (cuSPARSE on the GPU) in a per-level layout where each GS colour is one contiguous slice
    (``FastLayout``, ``FastPattern``): same arithmetic, rounding-level differences (sums in another order).  The
    copies are filled once per hierarchy (at the end of the constructor when no value requires grad, else at the
    first fast application) and cost about 1.5x the hierarchy's block values (``report()['fast_kernels']``; the
    fine matrix's copy is shared with a live cycle on the same plan built on the same value tensor, ``_shared_fill``,
    and then counted by both reports); the per-application block temporaries of the block path (gathered x[col], einsum outputs, each colour's copied
    blocks) are not allocated.  The stability check, the trouble setup, the Chebyshev spectral estimate and every
    differentiable application keep the block path.
    """

    def __init__(self, A: BlockCSR, *, smoother: str = "gs", pre: int = 1, post: int = 1,
                 jacobi_omega: float = 0.7, restriction: str = "transpose",
                 smooth_prolongation: bool = True, coarse_max: int = 4000, max_levels: int = 10,
                 theta: float = 0.0, aggregation: str = "standard", gauge_field: int | None = None,
                 gauge=None, plan: CoupledAMGPlan | None = None, modulator=None, gs_omega: float = 1.0,
                 spai_omega: float = 1.0, cheb_degree: int = 2, cheb_ratio: float = 30.0, hybrid: int = 0,
                 stabilize: bool = False, stab_iters: int = 8, stab_cached: list | None = None,
                 stab_composite: bool = False, l1: bool = False, stab_select: str = "first",
                 trouble_cond: float = 0.0, trouble_dom: float = 0.0, trouble_rings: int = 1,
                 trouble_max: int = 4000, trouble_where: str = "post", diag_eps: float = 0.0,
                 colour_coarsest: bool = True, refresh_omega: bool = False, fast_kernels: bool = False,
                 cuda_graph: bool = False, hybrid_pre: int = 0, lean: bool = False, drop_block_values: bool = False,
                 int32_groups: bool = False, strength: str = "values", gpu_setup: bool = False,
                 fused_numeric: bool = False, block_kernels: bool = False, block_values: str = "fp64",
                 block_w: bool = False, stab_fast: bool = False, adaptive_l1: float = 0.0,
                 adaptive_l1_omega: float = 0.75, adaptive_l1_gate: float = 0.0, coarse_split=None,
                 level_plan=None, level_cycle=None, gs_solve: str = "colour", gs_inner: int = 1,
                 gs_order: str = "colour", gs_subdomain: int = 0, gs_graph: bool = False,
                 gs_solve_w: bool = False, near_null=None, fine_donor=None, sweep_guard: bool | str = False,
                 skip_fine_fast: bool = False):
        self.smoother, self.pre, self.post = smoother, int(pre), int(post)
        self.skip_fine_fast = bool(skip_fine_fast)
        # ``fine_donor`` (None = off): a live cycle whose fine level this one may share -- the very sliced-ELL copies
        # of the fine operator, when this cycle is built on the same fine block values with the same layout and value
        # storage (``_fine_donor_level``); the stagnation rescue of the camg factory is built on its primary's matrix,
        # and only its diagonal differs.  A weak reference: it keeps the donor alive no longer than the caller does
        self._fine_donor = None if fine_donor is None else weakref.ref(fine_donor)
        # ``near_null`` (None = off): the plan this cycle builds carries the near-null space (``RigidBodyModes``;
        # ``CoupledAMGPlan``), so the coarse levels have more unknowns per node than the fine one.  The block path
        # serves it; the scalar-copy and block-kernel paths and the trouble-set solve assume one block size and
        # refuse it.
        if near_null is not None and (fast_kernels or block_kernels or cuda_graph or trouble_cond > 0
                                      or trouble_dom > 0):
            raise ValueError("near_null runs on the block path: no fast_kernels, block_kernels, cuda_graph or "
                             "trouble-set solve")
        # Triangular solve of the classical GS sweeps (block path only; ``_gs_variant_sweep``):
        #   "colour"    the exact multicolour sweep (``_gs_sweep``), the default
        #   "twostage"  two-stage GS: the sweep's triangular solve (D / omega + T) d = r is replaced by ``gs_inner``
        #               Jacobi-Richardson iterations d <- omega D^-1 (r - T d), T the strictly lower (forward) or upper
        #               (backward) part in the ``gs_order`` ordering: "colour" (the multicolour sweep's own order, so
        #               gs_inner >= colours - 1 is the exact sweep) or "natural" (the level's row order)
        #   "hybrid"    hybrid GS: exact block GS in the natural row order inside contiguous subdomains of
        #               ``gs_subdomain`` rows (0 = one subdomain per level), Jacobi across subdomains; the triangular
        #               solves are level-scheduled (the rows of one level are mutually uncoupled and update together)
        # Same plan, block diagonal and damping as the colour sweep; levels with a learned W keep their colour sweeps
        # unless ``gs_solve_w``, which applies the variant also to the GS sweeps appended to W (``hybrid``,
        # ``hybrid_pre``).
        # ``gs_graph`` (hybrid only, CUDA, no grad): every hybrid sweep is replayed from a CUDA graph
        # (``_hybrid_graphed``), the same arithmetic without the host dispatch of its many schedule levels.
        if gs_solve not in ("colour", "twostage", "hybrid"):
            raise ValueError(f"gs_solve must be 'colour', 'twostage' or 'hybrid', not {gs_solve!r}")
        if gs_order not in ("colour", "natural"):
            raise ValueError(f"gs_order must be 'colour' or 'natural', not {gs_order!r}")
        if int(gs_inner) < 0 or int(gs_subdomain) < 0:
            raise ValueError("gs_inner and gs_subdomain are non-negative")
        if gs_solve != "colour" and (smoother != "gs" or fast_kernels or block_kernels or cuda_graph):
            raise ValueError(f"gs_solve={gs_solve!r} replaces the block path's GS sweep: it needs smoother='gs' and "
                             "no fast kernels, block kernels or CUDA graph")
        self.gs_solve, self.gs_inner = gs_solve, int(gs_inner)
        self.gs_order, self.gs_subdomain = gs_order, int(gs_subdomain)
        self._gs_aux = None
        if gs_graph:
            self.gs_graph = True
        if gs_solve_w:
            if gs_solve == "colour":
                raise ValueError("gs_solve_w applies a two-stage or hybrid gs_solve to the W levels' GS sweeps")
            self.gs_solve_w = True
        # Block kernels (off by default): ``block_kernels`` applies the fast
        # cycle with fused sliced-ELL block kernels (``block_kernels.py``: one Triton kernel per GS colour pass, 4 bytes
        # of index per block instead of per scalar) in place of the scalar cuSPARSE copies; ``block_values`` "fp32"
        # stores the cycle's values (A, smoother inverses, P, R; not the dense coarsest LU) in fp32 with fp64
        # arithmetic -- a fixed linear map, the V-cycle of the once-rounded hierarchy.  A hierarchy the block cycle does
        # not cover (``_block_ineligible``) keeps the scalar copies (``report()['block_kernels']['fallback']``).  The
        # attributes exist only when asked for, so a default hierarchy carries nothing new.
        if block_kernels:
            if not fast_kernels:
                raise ValueError("block_kernels replaces the fast kernels' scalar copies: it needs fast_kernels=True")
            if block_values not in ("fp64", "fp32"):
                raise ValueError(f"block_values must be 'fp64' or 'fp32', not {block_values!r}")
            self.block_kernels, self.block_values = True, block_values
            self.block_fallback = None
            if block_w:
                # ``block_w`` (off = a learned W keeps the scalar copies): the learned one-hop
                # W of a level runs on the block kernels too -- W has A's block pattern, so A's sliced-ELL layout carries
                # it: x += W (b - A x) is one fused residual pass and one fused y += W r pass (``_blk_smooth``)
                self.block_w = True
        elif block_w:
            raise ValueError("block_w puts the learned W on the M3 block kernels: it needs block_kernels=True")
        # GPU setup (defaults off): ``strength`` / ``gpu_setup`` / ``fused_numeric`` belong
        # to the plan (``CoupledAMGPlan``; a passed plan decides), passed to the plan this cycle builds only when set
        # Lean options (all off by default):
        #   lean               the plan this cycle builds is a lean plan (``CoupledAMGPlan(lean=True)``; a passed plan
        #                      decides)
        #   drop_block_values  after the fast fill, every level's BLOCK values (A, the smoother inverses, W, P, R) are
        #                      released, level by level: the fast cycle reads only its scalar copies, and the fine
        #                      values can be rebuilt from their copy exactly (``fine_block_values``, ``_drop_level``)
        #   int32_groups       a fast scalar CSR whose entries outgrow int32 keeps int32 column indices and per-group row
        #                      pointers (no whole-matrix CSR; a product is the concatenation of the group products)
        #                      instead of falling back to int64 indices (``FastPattern(int32_groups=True)``)
        if (drop_block_values or int32_groups) and not fast_kernels:
            raise ValueError("drop_block_values / int32_groups act on the fast kernels' copies: they need "
                             "fast_kernels=True")
        self.drop_block_values, self.int32_groups = bool(drop_block_values), bool(int32_groups)
        # hybrid: on levels with a learned one-hop W, append this many multicolour GS sweeps
        # (damping gs_omega) to the post-smoothing, so the cycle keeps GS's robustness on the modes the
        # learned step leaves (the zero-shot W reduced fast and then stagnated on the cylinder)
        self.hybrid = int(hybrid)
        # hybrid_pre (0 = off, bit for bit): on levels with a learned W,
        # PREPEND this many forward multicolour GS sweeps to the pre-smoothing ("sandwich": GS^k W | coarse | W GS^h).
        # At Ra >= 1e7 the learned W and its damped-Jacobi stabilize reference both amplify on the fine level, and the
        # W-only pre-smoothing hands the amplified error to the coarse correction; GS first damps it.  The stability
        # check (rate of W, or of W + the post GS sweeps with stab_composite) does not include these sweeps.
        self.hybrid_pre = int(hybrid_pre)
        self.jacobi_omega, self.gs_omega = float(jacobi_omega), float(gs_omega)
        # Opt-in (None = off): ``level_cycle`` -- a list of per-level dicts (entry k for level
        # k, the last entry for every deeper level) with any of "pre", "post" (smoothing sweeps) and "omega" (GS
        # damping); a key left out keeps the cycle-wide setting.  Read by all three cycle paths (block, fast, M3).
        if level_cycle is not None:
            level_cycle = [dict(d) for d in level_cycle]
            bad = [key for d in level_cycle for key in d if key not in ("pre", "post", "omega")]
            if not level_cycle or bad:
                raise ValueError(f"level_cycle must be a non-empty list of dicts of pre / post / omega, not keys {bad}")
        self.level_cycle = level_cycle
        self.cheb_degree, self.cheb_ratio = int(cheb_degree), float(cheb_ratio)
        self.gauge_field, self.gauge = gauge_field, gauge
        self.F = A.F
        self.plan = plan or CoupledAMGPlan(A, smoother=smoother, restriction=restriction,
                                           smooth_prolongation=smooth_prolongation,
                                           coarse_max=coarse_max, max_levels=max_levels,
                                           theta=theta, aggregation=aggregation, spai_omega=spai_omega, l1=l1,
                                           diag_eps=diag_eps, colour_coarsest=colour_coarsest,
                                           # a learned W's Gauss-Seidel sweep on a plan of another smoother
                                           **({"colour_fine": True} if (modulator is not None and (hybrid or hybrid_pre)
                                                                         and smoother != "gs") else {}),
                                           **({"lean": True} if lean else {}),
                                           **({"strength": strength} if strength != "values" else {}),
                                           **({"gpu_setup": True} if gpu_setup else {}),
                                           **({"fused_numeric": True} if fused_numeric else {}),
                                           **({"adaptive_l1": float(adaptive_l1),
                                               "adaptive_l1_omega": float(adaptive_l1_omega),
                                               "adaptive_l1_gate": float(adaptive_l1_gate)} if adaptive_l1 else {}),
                                           **({"coarse_split": coarse_split} if coarse_split is not None else {}),
                                           **({"level_plan": level_plan} if level_plan is not None else {}),
                                           **({"near_null": near_null} if near_null is not None else {}))
        # refresh_omega only on a passed plan: a plan built just above already has A's omega
        self.values = (self.plan.numeric(A.val, modulator, gauge_field, refresh_omega=True)
                       if refresh_omega and plan is not None else self.plan.numeric(A.val, modulator, gauge_field))
        gated = getattr(self.plan, "_gating", False)
        n_gate = len(getattr(self.plan, "adaptive_l1_gate_log", ()))
        if getattr(self.plan, "_flagged", None) and not gated:
            # the operator-decided l1 rule's gate: the finest flagged level and the coarser ones switch only if the
            # classical reference cycle on this plan amplifies; both arms ask the same reference, so they switch the
            # same levels.  This cycle's values then on the switched diagonals (omega refreshed for them); the gate has
            # decided this build, so a level that pass flags waits for the next build's radius test and gate
            if self._l1_gate(A, gauge_field):
                self.values = self.plan.numeric(A.val, modulator, gauge_field, refresh_omega=True)
            self.plan._flagged = {}
        if getattr(self.plan, "adaptive_l1", 0.0) > 0 and not gated:
            # the rule's decision for this build, for the Newton rows (``l1_rule_columns``)
            log = getattr(self.plan, "adaptive_l1_gate_log", [])
            record_l1_decision(self.plan, log[-1] if len(log) > n_gate else None)
        # ``sweep_guard`` (opt-in; True or "coarse": the levels below the finest, "all": every level): each such
        # level's block GS sweep probed on its own operator and damped where it grows (``_sweep_guard``); a level
        # whose sweep contracts keeps its damping, bit for bit
        if sweep_guard not in (False, None, True, "coarse", "all"):
            raise ValueError(f"sweep_guard must be False, True, 'coarse' or 'all', not {sweep_guard!r}")
        self.sweep_guard = self._sweep_guard(sweep_guard == "all") if sweep_guard else []
        # stability check of learned one-hop smoothers: see _stabilize_level.  ``stab_cached`` (the
        # ``stab`` list of an earlier hierarchy) re-applies its blend factors without power iterations
        # (the check cost ~0.85 s per linearisation at 3.4M unknowns when it blends)
        # ``stab_composite``: False (default) the rate of W alone; True the rate of W followed by the hybrid GS
        # sweeps at ``gs_omega``; "cycle" (opt-in, 2026-11-02, FEM showcase R29) the same at the level's own damping
        # (``level_cycle``'s omega, the sweep the cycle runs after W) -- True keeps its recorded behaviour
        if stab_composite not in (False, True, "cycle"):
            raise ValueError(f"stab_composite must be False, True or 'cycle', not {stab_composite!r}")
        self.stab_composite = "cycle" if stab_composite == "cycle" else bool(stab_composite)
        if stab_select not in ("first", "argmin"):
            raise ValueError(f"stab_select must be 'first' or 'argmin', not {stab_select!r}")
        self.stab_select = stab_select
        if stabilize and stab_cached:
            # profile only (LPCNS_O1_TIMERS=1, synchronised): the seconds of re-applying the cached blend factors
            t_blend = _sync_clock(A.row.device) if os.environ.get("LPCNS_O1_TIMERS") == "1" else None
            theta_of = {d["level"]: d["theta"] for d in stab_cached}
            self.stab = []
            for k, V in enumerate(self.values):
                if V.W is None:
                    continue
                theta = theta_of.get(k, 1.0)
                if theta < 1.0:
                    L = self.plan.levels[k]
                    jac = torch.zeros_like(V.W.val)
                    jac[L.diag] = self.jacobi_omega * V.Sinv
                    V.W = BlockCSR(V.W.row, V.W.col, theta * V.W.val + (1.0 - theta) * jac, V.W.shape)
                self.stab.append({"level": k, "theta": theta, "rate": float("nan"), "rate_jacobi": float("nan"),
                                  "cached": True})
            if t_blend is not None:
                self.stab_blend_seconds = _sync_clock(A.row.device) - t_blend      # report()["stab_blend_s"]
        else:
            if stab_fast:
                self.stab_fast = True                # O1-HR-2r item B: the check on cuSPARSE BSR products
            t_stab = _sync_clock(A.row.device) if stabilize else None
            self.stab = [self._stabilize_level(k, stab_iters) for k, V in enumerate(self.values)
                         if V.W is not None] if stabilize else []
            if stabilize:
                self.stab_seconds = _sync_clock(A.row.device) - t_stab     # reported apart (report()["stab_s"])
        # trouble-set local solve: see _trouble_setup; off unless a threshold is given
        if trouble_where not in ("post", "both"):
            raise ValueError(f"trouble_where must be 'post' or 'both', not {trouble_where!r}")
        self.trouble_where = trouble_where
        self.trouble = (self._trouble_setup(trouble_cond, trouble_dom, int(trouble_rings), int(trouble_max))
                        if (trouble_cond > 0 or trouble_dom > 0) and len(self.values) > 1 else None)
        if smoother == "cheb":
            for V in self.values:
                if V.dense_lu is None:
                    DinvA = lambda v, V=V: torch.einsum("nfg,ng->nf", V.Sinv, V.A.matvec(v))
                    V.rho = 1.1 * spectral_radius_estimate(DinvA, V.A.shape[0], V.A.F, iters=15,
                                                           dtype=A.val.dtype, device=A.val.device)
        if self.gs_solve != "colour":
            self._gs_aux = [self._gs_variant_setup(k) for k in range(len(self.values))]
        # fast kernels: filled here, after everything above, when no value requires grad (the
        # Newton entry and gpu_bench build under no_grad), so the fill is part of the build time; otherwise at
        # the first application that may use them
        self.fast_kernels = bool(fast_kernels)
        self._fast = None
        if self.fast_kernels and not self._values_require_grad():
            if getattr(self.plan, "numeric_seconds", None) is not None:
                # An M2 plan's numeric pass is timed by part; the fast fill is timed here
                t0 = _sync_clock(A.row.device)
                self._fast_build()
                self.plan.numeric_seconds["fast_fill"] = _sync_clock(A.row.device) - t0
            else:
                self._fast_build()
        # CUDA graph of the fast cycle (see ``_graphed``): only with the fast kernels; the attribute
        # exists only when asked for, so a default hierarchy carries nothing new
        if cuda_graph:
            if not self.fast_kernels:
                raise ValueError("cuda_graph replays the fast V-cycle: it needs fast_kernels=True")
            self.cuda_graph = True
            self._graphs = {}
            self._graph_info = {"captured": [], "replays": 0, "rel_diff": None, "bitwise": None, "pool_mib": 0.0,
                                "error": None}

    @torch.no_grad()
    def _l1_gate(self, A: BlockCSR, gauge_field) -> bool:
        """The gate of the operator-decided l1 rule and its selection.  The gate: the classical reference cycle on
        this plan -- one Gauss-Seidel sweep each way at ``L1_GATE_OMEGA``, no modulator, whatever this cycle's own
        settings -- grows by more than ``adaptive_l1_gate`` per application (``_reference_growth``, measured once).
        Then the selection: of three plans -- no switch, the finest level the radius test flagged (above
        ``adaptive_l1``) with every coarser aggregated level, l1 on every aggregated level -- the one whose reference
        cycle reduces most in ``L1_SELECT_STEPS`` GMRES steps from its own random right-hand side
        (``_reference_reduction``, seed ``l1_select_seed``); on a tie the one with fewer l1 levels.  The reference is the
        same in both arms, so they take the same plan.  Recorded in ``plan.adaptive_l1_gate_log`` (the flagged levels,
        the growth, the switched levels, the selection's readings); a ``freeze_l1`` plan keeps the decision.  True
        when the gate passed: the selection changed the plan's values, which the caller recomputes."""
        plan = self.plan
        flagged, plan._flagged = dict(plan._flagged), {}
        began = time.perf_counter()
        growth = self._reference_growth(A, gauge_field)
        gate = not growth <= plan.adaptive_l1_gate              # a non-finite growth amplifies too
        levels, selection = [], None
        if gate:
            agg = [k for k, L in enumerate(plan.levels) if L.agg is not None]
            before = [k for k in agg if getattr(plan.levels[k], "l1", False)]
            finest = min(flagged)
            candidates = {"none": sorted(before), "flagged_and_coarser": sorted(set(before) | {k for k in agg
                                                                                         if k >= finest}),
                          "full": list(agg)}
            seed, reductions, measured = l1_select_seed(A), {}, {}
            for name, lv in candidates.items():
                if tuple(lv) not in measured:
                    for k in agg:
                        plan.levels[k].l1 = k in lv
                    measured[tuple(lv)] = self._reference_reduction(A, gauge_field, seed)
                reductions[name] = measured[tuple(lv)]
            score = lambda n: (reductions[n] if not math.isnan(reductions[n]) else float("inf"), len(candidates[n]))
            chosen = min(candidates, key=score)
            for k in agg:
                plan.levels[k].l1 = k in candidates[chosen]
            levels = [k for k in candidates[chosen] if k not in before]
            for k in levels:
                plan.adaptive_l1_log.append((k, flagged.get(k, plan._radii.get(k, float("nan")))))
            selection = {"seed": seed, "chosen": chosen, "reductions": {n: float(r) for n, r in reductions.items()},
                         "seconds": round(time.perf_counter() - began, 4)}
        plan.adaptive_l1_gate_log.append({"flagged": {int(k): round(float(r), 4) for k, r in flagged.items()},
                                          "growth": float(growth), "switched": bool(levels), "levels": levels,
                                          "selection": selection, "seconds": round(time.perf_counter() - began, 4)})
        if L1_MARCH["on"]:                                     # kept for the rest of the march
            L1_MARCH["decisions"].setdefault(plan.l1_march_key(), [k for k, L in enumerate(plan.levels)
                                                                   if L.agg is not None and getattr(L, "l1", False)])
            plan._l1_decided = True
        if getattr(plan, "freeze_l1", False):
            plan._l1_decided = True
        return gate

    @torch.no_grad()
    def _reference_reduction(self, A: BlockCSR, gauge_field, seed: int) -> float:
        """ln(|r_k| / |r_0|) of ``L1_SELECT_STEPS`` GMRES steps on A right-preconditioned by the gate's reference cycle
        on this plan as it stands (its levels' diagonals, the prolongation weights refreshed for them), from
        r_0 = A e for a random e of seed ``seed``."""
        plan = self.plan
        plan._gating = True
        try:
            ref = CoupledAMG(A, plan=plan, smoother="gs", pre=1, post=1, gs_omega=L1_GATE_OMEGA,
                             gauge_field=gauge_field, refresh_omega=True)
            memory_note("reference cycle built")
            gen = torch.Generator(device="cpu").manual_seed(int(seed))
            e = torch.randn(A.shape[0], A.F, dtype=A.val.dtype, generator=gen).to(A.val.device)
            red = gmres_reduction(lambda v: A.matvec(ref.apply_blocks(v)), A.matvec(e), L1_SELECT_STEPS)
            del ref
            memory_note("reference reduction", reduction=float(red))
        finally:
            plan._gating, plan._flagged = False, {}
        return red

    @torch.no_grad()
    def _reference_growth(self, A: BlockCSR, gauge_field) -> float:
        """The growth per application of the gate's classical reference cycle on this plan as it stands (its levels'
        current diagonals; ``_cycle_growth``, ``L1_GATE_STEPS`` power steps on the fine level).  Its numeric pass
        refreshes ``plan._radii`` on every aggregated level that is not l1; nothing it flags is kept."""
        plan = self.plan
        plan._gating, plan._radii = True, {}
        try:
            ref = CoupledAMG(A, plan=plan, smoother="gs", pre=1, post=1, gs_omega=L1_GATE_OMEGA,
                             gauge_field=gauge_field)
            growth = ref._cycle_growth(L1_GATE_STEPS)
            del ref
        finally:
            plan._gating, plan._flagged = False, {}
        return growth

    @torch.no_grad()
    def _cycle_growth(self, steps: int) -> float:
        """The growth per application of this cycle's error propagator I - M A on the fine level: power steps from a
        fixed random vector, the geometric mean of the last half (as the stability checks read a rate)."""
        A0 = self.values[0].A
        gen = torch.Generator(device="cpu").manual_seed(6)
        x = torch.randn(A0.shape[0], self.F, dtype=A0.val.dtype, generator=gen).to(A0.val.device)
        x = x / x.norm()
        logs = []
        for _ in range(int(steps)):
            y = x - self.apply_blocks(A0.matvec(x))
            nrm = float(y.norm())
            if not math.isfinite(nrm):
                return float("inf")
            if nrm == 0.0:
                return 0.0
            logs.append(math.log(nrm))
            x = y / nrm
        tail = logs[len(logs) // 2:]
        return math.exp(sum(tail) / len(tail))

    def _stabilize_level(self, k: int, iters: int) -> dict:
        """Blend a learned one-hop W towards damped block Jacobi until its smoothing iteration does not grow.

        Zero-shot on the developed cylinder flow the learned W made the cycle reduce nothing (GMRES residual
        0.8 after 600 iterations) and one appended GS sweep could not undo it: W amplified some modes.  A
        power iteration (``iters`` steps, fixed seed) on S = I - W A estimates its growth rate; the largest
        theta in (1, 1/2, 1/4, 0) with rate(theta W + (1 - theta) omega D^-1) <= max(1, rate of damped
        Jacobi) is kept.  Cost: a few matvecs per candidate, small next to the network's forward pass.
        """
        L, V = self.plan.levels[k], self.values[k]
        n, F = V.A.shape[0], V.A.F
        jac = torch.zeros_like(V.W.val)
        jac[L.diag] = self.jacobi_omega * V.Sinv
        learned = V.W.val
        gen = torch.Generator(device="cpu").manual_seed(4321 + k)
        e0 = torch.randn(n, F, dtype=V.A.val.dtype, generator=gen).to(V.A.val.device)

        # stab_composite: rate of the smoother the cycle actually runs -- W followed by the hybrid GS
        # sweeps -- instead of W alone. Measured on DHC88 Ra1e6 (small model): W alone is judged unstable on mid
        # states and blended although the hybrid cycle with the unblended W needs 41 its against 46 blended.
        gs_after = self.hybrid if (getattr(self, "stab_composite", False) and self.hybrid) else 0
        # "cycle": the sweep at the level's damping, as ``_smooth`` applies it after W (True: ``gs_omega``, recorded)
        gs_w = self._omega_of(k) if getattr(self, "stab_composite", False) == "cycle" else None
        zero = torch.zeros_like(e0)

        # stab_fast (off = the block products): the power iterations through
        # torch's BSR mat-vec on the level's own values (cuSPARSE; ``_bsr_matvec``, None off CUDA -> block products)
        amv = _bsr_matvec(L, V.A.val) if getattr(self, "stab_fast", False) else None

        def rate(Wval):
            W = BlockCSR(V.W.row, V.W.col, Wval, V.W.shape)
            wmv = _bsr_matvec(L, Wval.contiguous()) if amv is not None else None
            e = e0 / e0.norm()
            logs = []
            for _ in range(iters):
                Ae = amv(e) if amv is not None else V.A.matvec(e)
                e = e - (wmv(Ae) if wmv is not None else W.matvec(Ae))
                for _g in range(gs_after):
                    e = self._gs_sweep(L, V, zero, e, True) if gs_w is None else \
                        self._gs_sweep(L, V, zero, e, True, omega=gs_w)
                nrm = float(e.norm())
                if not math.isfinite(nrm) or nrm == 0.0:
                    return float("inf") if not math.isfinite(nrm) else 0.0
                logs.append(math.log(nrm))
                e = e / nrm
            tail = logs[iters // 2:]
            return math.exp(sum(tail) / len(tail))

        if getattr(self, "stab_select", "first") == "argmin":
            # Neither "first theta with rate <= 1" test ranks the candidates as GMRES sees them (W alone
            # over-blends mid Newton states, W + GS under-blends late ones, DHC88 Ra1e6). Keep the theta whose
            # smoother (W alone or W + hybrid GS, per stab_composite) has the SMALLEST measured rate.
            cands = []
            for theta in (1.0, 0.5, 0.25, 0.0):
                Wt = learned if theta == 1.0 else (jac if theta == 0.0 else theta * learned + (1.0 - theta) * jac)
                cands.append((rate(Wt), -theta, theta, Wt))
            r, _, chosen, Wt = min(cands, key=lambda c: (c[0], c[1]))
            if chosen < 1.0:
                V.W = BlockCSR(V.W.row, V.W.col, Wt, V.W.shape)
            return {"level": k, "theta": chosen, "rate": r, "rate_jacobi": cands[3][0]}
        # a W whose smoothing iteration does not grow is accepted as it is: one power iteration, no Jacobi
        # reference (the check cost ~2.5 s per linearisation at 3.4M unknowns when it always ran 2-4 of them)
        r_learned = rate(learned)
        if r_learned <= 1.0:
            return {"level": k, "theta": 1.0, "rate": r_learned, "rate_jacobi": float("nan")}
        r_jac = rate(jac)
        limit = max(1.0, r_jac)
        chosen, r = 0.0, r_jac
        if r_learned <= limit:
            chosen, r = 1.0, r_learned
        else:
            for theta in (0.5, 0.25):
                rt = rate(theta * learned + (1.0 - theta) * jac)
                if rt <= limit:
                    chosen, r = theta, rt
                    break
        if chosen < 1.0:
            V.W = BlockCSR(V.W.row, V.W.col, chosen * learned + (1.0 - chosen) * jac, V.W.shape)
        return {"level": k, "theta": chosen, "rate": r, "rate_jacobi": r_jac}

    # ------------------------------------------------------------------ cycle
    @torch.no_grad()
    def _trouble_setup(self, cond: float, dom: float, rings: int, max_cells: int):
        """Exact local solve on the fine-level TROUBLE SET.

        The set: cells whose diagonal block is near-singular (2-norm condition number > ``cond``) or weakly
        dominant (for some field f, |A_ii,ff| < ``dom`` * sum_{j != i} |A_ij,ff|, same field only), grown by
        ``rings`` rings of matrix-graph neighbours.  A_SS (dense) is LU-factored once; the cycle applies
        x_S += A_SS^-1 (b - A x)_S after the fine-level post-smoothing (and after the pre-smoothing with
        ``trouble_where="both"``) -- a multiplicative subdomain correction where the point-block smoother is
        unreliable (on the SST flat plate: the omega/k rows of a few leading-edge cells).  More than
        ``max_cells`` cells: no correction (recorded in ``report()``)."""
        A = self.values[0].A
        n, F = A.shape[0], self.F
        D = A.diagonal()
        bad = torch.zeros(n, dtype=torch.bool, device=A.val.device)
        if cond > 0:
            bad |= ~(torch.linalg.cond(D) <= cond)                    # inf / nan (singular) count as bad
        if dom > 0:                     # same-field dominance (the pressure rows' U coupling is excluded on purpose)
            off = A.row != A.col
            offsum = torch.zeros(n, F, dtype=A.val.dtype, device=A.val.device).index_add_(
                0, A.row[off], torch.diagonal(A.val[off], dim1=-2, dim2=-1).abs())
            dd = torch.diagonal(D, dim1=-2, dim2=-1).abs()
            bad |= (dd < dom * offsum).any(-1)
        seed = int(bad.sum())
        for _ in range(rings):
            grow = torch.zeros_like(bad)
            grow[A.row[bad[A.col]]] = True
            bad |= grow
        S = torch.nonzero(bad).reshape(-1)
        info = {"seed_cells": seed, "cells": int(S.numel())}
        if S.numel() == 0 or S.numel() > max_cells:
            return {**info, "S": None}
        m = S.numel()
        loc = torch.full((n,), -1, dtype=torch.long, device=A.val.device)
        loc[S] = torch.arange(m, device=A.val.device)
        e = (loc[A.row] >= 0) & (loc[A.col] >= 0)
        Ad = torch.zeros(m, F, m, F, dtype=A.val.dtype, device=A.val.device)
        Ad[loc[A.row[e]], :, loc[A.col[e]], :] = A.val[e]
        Ad = Ad.reshape(m * F, m * F)
        lu = dense_lu(Ad, "local block")
        return {**info, "S": S, "A_S": A.rows_subset(S), "lu": lu}

    def _trouble_correct(self, b: torch.Tensor, x):
        T = self.trouble
        if x is None:
            x = torch.zeros_like(b)
        A_S = T["A_S"]
        Ax = torch.zeros_like(b).index_add_(0, A_S.row, torch.einsum("efg,eg->ef", A_S.val, x[A_S.col]))
        r = b[T["S"]] - Ax[T["S"]]
        dx = torch.linalg.lu_solve(*T["lu"], r.reshape(-1, 1)).reshape(r.shape)
        return x.index_add(0, T["S"], dx)

    def _smooth(self, k: int, b: torch.Tensor, x, sweeps: int, reverse=False):
        L, V = self.plan.levels[k], self.values[k]
        # gs_solve_w: the GS sweeps appended to a learned W run the two-stage / hybrid variant
        w_variant = (V.W is not None and getattr(self, "gs_solve_w", False) and self._gs_aux is not None
                     and self._gs_aux[k] is not None)
        if not reverse and getattr(self, "hybrid_pre", 0) and V.W is not None and L.colours:
            for _ in range(self.hybrid_pre):                 # O1-HR sandwich: GS before the learned pre-smoothing
                x = (self._gs_variant_sweep(k, V, b, x, False, self.gs_omega) if w_variant
                     else self._gs_sweep(L, V, b, x, False))
        for _ in range(sweeps):
            if V.W is not None:
                r = b if x is None else b - V.A.matvec(x)
                dx = V.W.matvec(r)
                x = dx if x is None else x + dx
            elif self.smoother == "cheb":
                # Chebyshev polynomial of degree cheb_degree in D^-1 A on [rho/ratio, rho] (one sweep)
                hi, lo = V.rho, V.rho / self.cheb_ratio
                theta, delta = 0.5 * (hi + lo), 0.5 * (hi - lo)
                sigma = theta / delta
                r = b if x is None else b - V.A.matvec(x)
                d = torch.einsum("nfg,ng->nf", V.Sinv, r) / theta
                x = d if x is None else x + d
                rho0 = 1.0 / sigma
                for _ in range(self.cheb_degree - 1):
                    r = b - V.A.matvec(x)
                    rho1 = 1.0 / (2.0 * sigma - rho0)
                    d = rho1 * rho0 * d + (2.0 * rho1 / delta) * torch.einsum("nfg,ng->nf", V.Sinv, r)
                    x = x + d
                    rho0 = rho1
            elif self.smoother == "jacobi" or not L.colours:
                r = b if x is None else b - V.A.matvec(x)
                dx = self.jacobi_omega * torch.einsum("nfg,ng->nf", V.Sinv, r)
                x = dx if x is None else x + dx
            elif getattr(self, "_gs_aux", None) is not None:
                x = self._gs_variant_sweep(k, V, b, x, reverse, self._omega_of(k))
            else:
                if SWEEP_WORK["on"]:
                    _count_sweep(k, len(L.colours), sum(int(c[1].numel()) for c in L.colours))
                x = self._gs_sweep(L, V, b, x, reverse, omega=self._omega_of(k))
        if reverse and self.hybrid and V.W is not None and L.colours:
            for _ in range(self.hybrid):
                x = (self._gs_variant_sweep(k, V, b, x, reverse, self._omega_of(k)) if w_variant
                     else self._gs_sweep(L, V, b, x, reverse, omega=self._omega_of(k)))
        return x

    def _gs_sweep(self, L, V, b, x, reverse, omega=None):
        w = self.gs_omega if omega is None else float(omega)        # O1-C: the level's damping (level_cycle)
        if x is None:
            x = torch.zeros_like(b)
        order = range(len(L.colours))
        for c in (reversed(order) if reverse else order):
            rows, edges, local = L.colours[c]
            if edges.device != b.device:       # an M5 lean plan keeps these in host memory (the fast cycle never reads them)
                edges, local = edges.to(b.device), local.to(b.device)
            Ax = torch.zeros(rows.numel(), b.shape[1], dtype=b.dtype, device=b.device).index_add(
                0, local, torch.einsum("efg,eg->ef", V.A.val[edges], x[L.col[edges]]))
            dx = torch.einsum("nfg,ng->nf", V.Sinv[rows], b[rows] - Ax)
            x = x.index_add(0, rows, dx if w == 1.0 else w * dx)
        return x

    def _gs_variant_setup(self, k: int):
        """What level ``k``'s two-stage or hybrid sweep reads (``gs_solve``); None on a level that runs no classical
        GS sweep (dense coarsest level, learned W without ``gs_solve_w``, no colouring).

        two-stage: the strictly lower and strictly upper block parts of A in the ``gs_order`` ordering (BlockCSR,
        copies of A's blocks).  hybrid: the level schedule of the subdomain-local natural order as passes
        ``(rows, edges, local)`` shaped like the colour classes of ``_gs_sweep`` (``edges``: every block of those rows
        that couples inside their subdomain, the diagonal included), and the blocks that couple across subdomains
        (``inter``, None with one subdomain)."""
        L, V = self.plan.levels[k], self.values[k]
        if V.dense_lu is not None or (V.W is not None and not getattr(self, "gs_solve_w", False)) or not L.colours:
            return None
        A = V.A
        row, col, n = A.row, A.col, A.shape[0]
        dev = row.device
        if self.gs_solve == "twostage":
            if self.gs_order == "colour":
                key = torch.empty(n, dtype=torch.long, device=dev)
                for c, entry in enumerate(L.colours):
                    key[entry[0]] = c
            else:
                key = torch.arange(n, device=dev)
            lo = torch.nonzero(key[col] < key[row]).reshape(-1)
            up = torch.nonzero(key[col] > key[row]).reshape(-1)
            return {"lower": BlockCSR(row[lo], col[lo], A.val[lo], A.shape),
                    "upper": BlockCSR(row[up], col[up], A.val[up], A.shape)}
        m = self.gs_subdomain if 0 < self.gs_subdomain < n else n
        sub = torch.arange(n, device=dev) // m
        same = sub[row] == sub[col]
        level = hybrid_levels(row, col, n, same)
        nlev = int(level.max()) + 1
        order = torch.argsort(level, stable=True)
        counts = torch.bincount(level, minlength=nlev)
        start = torch.cumsum(counts, 0) - counts
        pos = torch.empty(n, dtype=torch.long, device=dev)
        pos[order] = torch.arange(n, device=dev) - start[level[order]]
        e_in = torch.nonzero(same).reshape(-1)
        e_lev = level[row[e_in]]
        e_in = e_in[torch.argsort(e_lev, stable=True)]
        passes, a, ea = [], 0, 0
        for cnt, ecnt in zip(counts.tolist(), torch.bincount(e_lev, minlength=nlev).tolist()):
            edges = e_in[ea:ea + ecnt]
            passes.append((order[a:a + cnt], edges, pos[row[edges]]))
            a, ea = a + cnt, ea + ecnt
        out = torch.nonzero(~same).reshape(-1)
        inter = BlockCSR(row[out], col[out], A.val[out], A.shape) if out.numel() else None
        return {"passes": passes, "inter": inter, "subdomains": -(-n // m), "rows_per_subdomain": m}

    def _gs_variant_sweep(self, k: int, V: "LevelValues", b: torch.Tensor, x, reverse: bool, omega: float):
        """One two-stage or hybrid GS sweep of level ``k`` (forward, or backward with ``reverse``), damping ``omega``
        and the block diagonal inverse ``V.Sinv`` of the colour sweep."""
        aux, w = self._gs_aux[k], float(omega)
        if self.gs_solve == "twostage":
            T = aux["upper"] if reverse else aux["lower"]
            r = b if x is None else b - V.A.matvec(x)
            d = w * torch.einsum("nfg,ng->nf", V.Sinv, r)
            for _ in range(self.gs_inner):         # Jacobi-Richardson on (D / omega + T) d = r
                d = w * torch.einsum("nfg,ng->nf", V.Sinv, r - T.matvec(d))
            if SWEEP_WORK["on"]:
                _count_sweep(k, 1 + self.gs_inner, (0 if x is None else V.A.nnzb) + self.gs_inner * T.nnzb)
            return d if x is None else x + d
        if SWEEP_WORK["on"]:
            inter = aux["inter"]
            _count_sweep(k, len(aux["passes"]) + (inter is not None),
                         sum(int(p[1].numel()) for p in aux["passes"]) + (0 if inter is None else inter.nnzb))
        if getattr(self, "gs_graph", False) and b.is_cuda and GRAPH_STATS["enabled"] and not torch.is_grad_enabled():
            return self._hybrid_graphed(k, V, b, x, reverse, w)
        return self._hybrid_sweep(k, V, b, x, reverse, w)

    def _hybrid_sweep(self, k: int, V: "LevelValues", b: torch.Tensor, x, reverse: bool, w: float) -> torch.Tensor:
        """The hybrid GS sweep: the blocks across subdomains read the iterate at the start of the sweep (Jacobi), the
        blocks inside a subdomain the current one (exact GS in the natural order, one schedule level per pass)."""
        aux = self._gs_aux[k]
        x = torch.zeros_like(b) if x is None else x.clone()
        inter = aux["inter"]
        s = b if inter is None else b - inter.matvec(x)
        passes = aux["passes"]
        for p in (reversed(range(len(passes))) if reverse else range(len(passes))):
            rows, edges, local = passes[p]
            Ax = torch.zeros(rows.numel(), b.shape[1], dtype=b.dtype, device=b.device).index_add(
                0, local, torch.einsum("efg,eg->ef", V.A.val[edges], x[V.A.col[edges]]))
            dx = torch.einsum("nfg,ng->nf", V.Sinv[rows], s[rows] - Ax)
            x.index_add_(0, rows, dx if w == 1.0 else w * dx)
        return x

    @torch.no_grad()
    def _hybrid_graphed(self, k: int, V: "LevelValues", b: torch.Tensor, x, reverse: bool, w: float) -> torch.Tensor:
        """``_hybrid_sweep`` replayed from a CUDA graph (``gs_graph``): the level-scheduled sweep launches about ten
        small kernels per schedule level, so its eager form is bound by the host dispatch.  Per (level, direction,
        zero start, shape, dtype, device, omega) the first sweep runs eagerly and is the answer; the sweep is then
        warmed up and captured on the side stream from private copies of b (and x), replayed once and compared with
        the eager answer (relative 2-norm <= ``GRAPH_REL_TOL`` keeps the graph, otherwise or on any capture error
        that key stays eager).  Later sweeps copy b and x into the static buffers, replay and return a clone."""
        graphs = self.__dict__.setdefault("_gs_graphs", {})
        key = (k, bool(reverse), x is None, tuple(b.shape), b.dtype, b.device, w)
        g = graphs.get(key)
        if isinstance(g, dict):
            g["b"].copy_(b)
            if g["x"] is not None:
                g["x"].copy_(x)
            g["graph"].replay()
            return g["out"].clone()
        eager = self._hybrid_sweep(k, V, b, x, reverse, w)
        if g is None:
            graphs[key] = self._hybrid_capture(k, V, b, x, reverse, w, eager)
        return eager

    @torch.no_grad()
    def _hybrid_capture(self, k, V, b, x, reverse, w, eager):
        device = b.device
        try:
            with torch.cuda.device(device):
                current = torch.cuda.current_stream(device)
                side = _graph_stream(device)
                bs = b.detach().clone()
                xs = None if x is None else x.detach().clone()
                side.wait_stream(current)
                with torch.cuda.stream(side):
                    self._hybrid_sweep(k, V, bs, xs, reverse, w)          # warm-up on the capture stream
                    graph = torch.cuda.CUDAGraph()
                    graph.capture_begin()
                    try:
                        out = self._hybrid_sweep(k, V, bs, xs, reverse, w)
                    finally:
                        graph.capture_end()
                current.wait_stream(side)
                graph.replay()
                rel = float((out - eager).norm() / eager.norm().clamp_min(1e-300))
        except Exception as exc:           # noqa: BLE001 -- any capture failure leaves this sweep eager
            self.gs_graph_error = f"{type(exc).__name__}: {exc}"[:400]
            return "eager"
        self.gs_graph_rel = max(getattr(self, "gs_graph_rel", 0.0), rel)
        if not rel <= GRAPH_REL_TOL:
            return "eager"
        return {"graph": graph, "b": bs, "x": xs, "out": out}

    def smoothing_cost(self) -> list:
        """Per level, the smoothing work of one V-cycle application: the sweeps, and per sweep from a nonzero iterate
        its sequential passes and the matrix blocks it multiplies (A, W or a triangular part; the products of the
        residual and the coarse correction outside the smoother are not counted).  Levels with a learned W list the
        W sweeps (residual + W) and the GS sweeps apart; the dense coarsest level is ``{"dense": True}``."""
        out = []
        for k, V in enumerate(self.values):
            L = self.plan.levels[k]
            if V.dense_lu is not None:
                out.append({"level": k, "dense": True})
                continue
            pre, post = self._pre_of(k), self._post_of(k)
            nnzb = V.A.nnzb
            gs = {"passes": len(L.colours), "products": nnzb}
            fw = bw = gs
            if getattr(self, "_gs_aux", None) is not None and self._gs_aux[k] is not None:
                aux = self._gs_aux[k]
                if self.gs_solve == "twostage":
                    fw = {"passes": 1 + self.gs_inner, "products": nnzb + self.gs_inner * aux["lower"].nnzb}
                    bw = {"passes": 1 + self.gs_inner, "products": nnzb + self.gs_inner * aux["upper"].nnzb}
                else:
                    p = len(aux["passes"]) + (aux["inter"] is not None)
                    fw = bw = {"passes": p, "products": nnzb}
            if V.W is not None:
                n_gs = (self.hybrid + getattr(self, "hybrid_pre", 0)) if L.colours else 0
                out.append({"level": k, "sweeps": pre + post + n_gs, "w_sweeps": pre + post, "gs_sweeps": n_gs,
                            "w": {"passes": 2, "products": nnzb + V.W.nnzb}, "gs": bw})
                continue
            if self.smoother != "gs" or not L.colours:
                out.append({"level": k, "sweeps": pre + post, "smoother": self.smoother if L.colours else "jacobi"})
                continue
            out.append({"level": k, "sweeps": pre + post, "pre": pre, "post": post, "forward": fw, "backward": bw})
        return out

    def _sweep_growth(self, k: int, omega: float, steps: int = SWEEP_GUARD_STEPS, seed: int = 17) -> float:
        """Growth per application of level ``k``'s forward block GS sweep at damping ``omega`` as an error propagation,
        ``e <- e - S_k(A_k e)``: the geometric mean of the norm ratios over the last half of ``steps`` power steps
        from a fixed random vector; a zero or non-finite norm ends the probe with that value."""
        L, V = self.plan.levels[k], self.values[k]
        A = V.A
        g = torch.Generator(device="cpu").manual_seed(seed + k)
        e = torch.randn(A.shape[0], A.F, generator=g, dtype=A.val.dtype).to(A.val.device)
        e = e / e.norm()
        ratios = []
        with torch.no_grad():
            for _ in range(steps):
                e = e - self._gs_sweep(L, V, A.matvec(e), None, False, omega=omega)
                r = float(e.norm())
                ratios.append(r)
                if not (r > 0.0 and math.isfinite(r)):
                    return r if r == r else float("inf")
                e = e / r
        half = ratios[len(ratios) // 2:]
        return math.exp(sum(math.log(v) for v in half) / len(half))

    def _sweep_guard(self, finest: bool = False) -> list:
        """Damp the block GS sweep of every level below the finest (with ``finest``: of every level) whose sweep grows
        on its own operator: the damping is halved, at most ``SWEEP_GUARD_HALVINGS`` times, until the probe
        (``_sweep_growth``) reads at most 1, else the tried damping with the smallest growth is kept; the level's
        damping is then set through ``level_cycle``.  Levels without a GS sweep (the exact
        coarsest, learned one-hop smoothers, no colouring) are skipped.  A level whose sweep contracts is left as it
        is, and with no level damped the cycle is the one without the guard, bit for bit.  Returns one record per
        probed level."""
        if self.smoother != "gs":
            return []
        n = len(self.values)
        lc = getattr(self, "level_cycle", None)
        entries = [dict(lc[min(k, len(lc) - 1)]) if lc else {} for k in range(n)]
        out, damped = [], False
        for k, V in enumerate(self.values):
            L = self.plan.levels[k]
            if (k == 0 and not finest) or V.dense_lu is not None or V.W is not None or not L.colours or V.Sinv is None:
                continue
            omega = float(entries[k].get("omega", self.gs_omega))
            growth = self._sweep_growth(k, omega)
            rec = {"level": k, "growth": growth, "omega": omega, "halvings": 0}
            h, w, gw = 0, omega, growth
            tried = [(growth if growth == growth else float("inf"), 0, omega)]
            while not gw <= 1.0 and h < SWEEP_GUARD_HALVINGS:
                h, w = h + 1, 0.5 * w
                gw = self._sweep_growth(k, w)
                tried.append((gw if gw == gw else float("inf"), h, w))
            if not gw <= 1.0:
                gw, h, w = min(tried)
            if h:
                entries[k]["omega"] = w
                damped = True
                rec.update(omega=w, halvings=h, growth_after=gw)
            out.append(rec)
        if damped:
            self.level_cycle = entries
        return out

    def _level_cycle_setting(self, k: int, key: str, default):
        """Level ``k``'s ``level_cycle`` entry for ``key`` (the last entry for deeper levels), else ``default``."""
        lc = getattr(self, "level_cycle", None)
        if not lc:
            return default
        return lc[min(int(k), len(lc) - 1)].get(key, default)

    def _pre_of(self, k: int) -> int:
        return int(self._level_cycle_setting(k, "pre", self.pre))

    def _post_of(self, k: int) -> int:
        return int(self._level_cycle_setting(k, "post", self.post))

    def _omega_of(self, k: int) -> float:
        return float(self._level_cycle_setting(k, "omega", self.gs_omega))

    def _cycle(self, k: int, b: torch.Tensor) -> torch.Tensor:
        V = self.values[k]
        if V.dense_lu is not None:
            return torch.linalg.lu_solve(*V.dense_lu, b.reshape(-1, 1)).reshape(b.shape)
        x = self._smooth(k, b, None, self._pre_of(k))
        trouble = k == 0 and self.trouble is not None and self.trouble["S"] is not None
        if trouble and self.trouble_where == "both":
            x = self._trouble_correct(b, x)
        r = b - V.A.matvec(x)
        e = self._cycle(k + 1, V.R.matvec(r))
        x = x + V.P.matvec(e)
        x = self._smooth(k, b, x, self._post_of(k), reverse=True)
        return self._trouble_correct(b, x) if trouble else x

    def apply_blocks(self, b: torch.Tensor) -> torch.Tensor:
        if getattr(self, "fast_kernels", False):
            if self._fast_eligible(b):
                n, F = b.shape
                if getattr(self, "cuda_graph", False):
                    return self._graphed(b.reshape(-1).contiguous(), "ci").view(n, F)
                return self._fast_apply(b.reshape(-1).contiguous(), "ci").view(n, F)
            self._note_block_path()
        return self._cycle(0, b)

    def __call__(self, v: torch.Tensor) -> torch.Tensor:
        if getattr(self, "fast_kernels", False):
            if self._fast_eligible(v):
                if getattr(self, "cuda_graph", False):
                    z = self._graphed(v.reshape(-1).contiguous(), "fb")
                else:
                    z = self._fast_apply(v.reshape(-1).contiguous(), "fb")
                return z if self.gauge is None else self.gauge(z)
            self._note_block_path()
        n = self.values[0].A.shape[0]
        z = self._cycle(0, v.reshape(self.F, n).t()).t().reshape(-1)
        return z if self.gauge is None else self.gauge(z)

    @property
    def levels(self):
        return self.values

    def report(self) -> dict:
        # each level's field count: its blocks' (a rigid-body hierarchy widens the coarse levels), or the cycle's
        # when the build dropped the block values (--camg-drop-block-values)
        fields = [V.A.F if V.A.val is not None else self.F for V in self.values]
        nnz = [V.A.nnzb * f * f for V, f in zip(self.values, fields)]
        extra = {"stab":[(d["level"], d["theta"], round(d["rate"], 3)) for d in self.stab]} if getattr(self, "stab", None) else {}
        if getattr(self, "trouble", None) is not None:
            extra["trouble"] = {"seed_cells": self.trouble["seed_cells"], "cells": self.trouble["cells"],
                                "active": self.trouble["S"] is not None}
        if getattr(self, "_fast", None) is not None:
            extra["fast_kernels"] = self._fast_memory()
        if getattr(self, "cuda_graph", False):
            extra["cuda_graph"] = dict(self._graph_info, captured=list(self._graph_info["captured"]))
        if getattr(self, "block_kernels", False):
            extra["block_kernels"] = self._block_report()
        if getattr(self, "stab_seconds", None) is not None:
            extra["stab_s"] = self.stab_seconds        # O1-HR-2r item B: the stability check's seconds of this build
        if getattr(self, "stab_blend_seconds", None) is not None:
            extra["stab_blend_s"] = self.stab_blend_seconds    # LPCNS_O1_TIMERS=1: the cached blend's seconds
        if getattr(self, "_gs_aux", None) is not None:
            # two-stage / hybrid GS: the setting and, per level, the passes of a sweep (hybrid: the level schedule)
            extra["gs_solve"] = {"solve": self.gs_solve, "inner": self.gs_inner, "order": self.gs_order,
                                 "subdomain": self.gs_subdomain, "cost": self.smoothing_cost()}
            if getattr(self, "gs_graph", False):
                graphs = getattr(self, "_gs_graphs", {})
                extra["gs_solve"].update(graph=True, graphs=sum(isinstance(g, dict) for g in graphs.values()),
                                         graphs_eager=sum(g == "eager" for g in graphs.values()),
                                         graph_rel=getattr(self, "gs_graph_rel", None),
                                         graph_error=getattr(self, "gs_graph_error", None))
        if getattr(self.plan, "near_null", None) is not None:
            extra["near_null"] = self.plan.near_null.describe()
        return {"levels": len(self.values), "rows": [V.A.shape[0] * f for V, f in zip(self.values, fields)],
                "operator_complexity": sum(nnz) / nnz[0],
                "colours": [len(L.colours) for L in self.plan.levels[:len(self.values)]],
                "omega_p": [L.omega for L in self.plan.levels[:len(self.values)]],
                **({"sweep_guard": self.sweep_guard} if getattr(self, "sweep_guard", None) else {}), **extra}

    # ------------------------------------------------------------------ fast kernels
    def _values_require_grad(self) -> bool:
        for V in self.values:
            ts = [V.A.val, V.Sinv] + [M.val for M in (V.W, V.P, V.R) if M is not None]
            if V.dense_lu is not None:
                ts += [t for t in V.dense_lu if torch.is_tensor(t)]
            if any(t is not None and t.requires_grad for t in ts):
                return True
        return False

    def _fast_eligible(self, v: torch.Tensor) -> bool:
        """The fast path runs only where no autograd graph would be recorded: under ``torch.no_grad()`` /
        inference mode, or when neither the input nor any level value requires grad -- the Newton entry's Krylov
        solve, which calls the preconditioner with grad mode on and detached vectors on a hierarchy built under
        ``no_grad``.  A differentiable hierarchy under grad mode (training: ``camg_train.LinearSystem``,
        ``experiments/NaturalConvection/CoupledNewton/LearnedCoupledAMG/train.py``) keeps the block path, autograd graph and all."""
        if not torch.is_grad_enabled():
            return True
        return not v.requires_grad and not self._values_require_grad()

    def _note_block_path(self) -> None:
        """Flag on, but this application records an autograd graph, so the block path ran.

        Counted (``block_path_applications``) and warned (once per call site, Python's default warning filter), so
        that a timing run whose Krylov vectors unexpectedly carry a graph -- which would make ``--camg-fast-kernels``
        a silent no-op -- shows it in its log.  Training never sets the flag; a test that differentiates through a
        fast-flagged hierarchy sees the warning."""
        self.block_path_applications = getattr(self, "block_path_applications", 0) + 1
        warnings.warn("CoupledAMG(fast_kernels=True) was applied while recording an autograd graph (grad mode on and "
                      "the input or a level value requires grad): that application used the block kernels",
                      RuntimeWarning, stacklevel=3)

    @torch.no_grad()
    def _fast_build(self):
        """Fill every level's scalar CSRs (the patterns come from the plan's cache, built at its first fast cycle).
        Uses the values as they are after the stability check and the trouble setup, which keep the block path."""
        plan, F = self.plan, self.F
        if getattr(self, "block_values_dropped", False):
            raise RuntimeError("CoupledAMG(drop_block_values=True): the block values were released after the fast fill, "
                               "the fast copies cannot be refilled from them (a level's values were replaced)")
        if getattr(self, "block_kernels", False):
            # M3 (opt-in): sliced-ELL block copies instead of the scalar ones, unless the cycle uses a branch the block
            # cycle does not implement (then the scalar copies below, recorded)
            reason = self._block_ineligible()
            if reason is None:
                return self._block_build()
            self.block_fallback = reason
        # M5 int32_groups / lean plan (opt-in): passed to the patterns only when set, so a default build makes the calls
        # it made
        pkw = {"int32_groups": True} if getattr(self, "int32_groups", False) else {}
        if getattr(plan, "lean", False):
            pkw["host_order"] = True
        levels = []
        for k, V in enumerate(self.values):
            if k == 0 and getattr(self, "skip_fine_fast", False):
                levels.append(_FastLevel(layout=None, sources=_fast_sources(V), blk=None))
                continue
            L = plan.levels[k]
            lay = _level_layout(plan, k)
            if V.dense_lu is not None:
                levels.append(_FastLevel(layout=lay, sources=_fast_sources(V), dense=True))
                continue
            nxt = _level_layout(plan, k + 1)
            Apat = _cached_pattern(L, "A", V.A.row, V.A.col,
                                   lambda: FastPattern(V.A.row, V.A.col, F, F, lay, lay, **pkw))
            Dpat = _fast_cache(L).get("D")
            if Dpat is None:
                ar = torch.arange(L.n, device=L.row.device)
                Dpat = _fast_cache(L)["D"] = FastPattern(ar, ar, F, F, lay, lay, local_groups=True)
            # A's scalar copy is shared with a live cycle on this plan built on the same values (_shared_fill)
            lv = _FastLevel(layout=lay, sources=_fast_sources(V), A=_shared_fill(L, "A", Apat, V.A.val),
                            Sinv=Dpat.fill(V.Sinv))
            if V.W is not None:
                same = ((V.W.row is V.A.row or torch.equal(V.W.row, V.A.row))
                        and (V.W.col is V.A.col or torch.equal(V.W.col, V.A.col)))
                Wpat = Apat if same else FastPattern(V.W.row, V.W.col, V.W.F, V.W.G, lay, lay, **pkw)
                lv.W = Wpat.fill(V.W.val)
            lv.P = _cached_pattern(L, "P", V.P.row, V.P.col,
                                   lambda: FastPattern(V.P.row, V.P.col, V.P.F, V.P.G, lay, nxt, **pkw)).fill(V.P.val)
            lv.R = _cached_pattern(L, "R", V.R.row, V.R.col,
                                   lambda: FastPattern(V.R.row, V.R.col, V.R.F, V.R.G, nxt, lay, **pkw)).fill(V.R.val)
            T = getattr(self, "trouble", None)
            if k == 0 and T is not None and T.get("S") is not None:
                S, A_S = T["S"], T["A_S"]
                m = int(S.numel())
                loc = torch.full((L.n,), -1, dtype=torch.long, device=S.device)
                loc[S] = torch.arange(m, device=S.device)
                pat = FastPattern(loc[A_S.row], A_S.col, F, A_S.G, FastLayout(m, F, None, device=S.device), lay)
                lv.trouble = {"A": pat.fill(A_S.val), "pos": lay.positions(S, "fb").reshape(-1), "m": m}
            levels.append(lv)
            if getattr(self, "drop_block_values", False):
                # M5: this level's block values are read by nothing from here on (the next level's fill reads the next
                # level's); released as soon as its copies exist, so a level's two copies never coexist with the next
                self._drop_level(k, lv)
        self._fast = levels
        if getattr(self, "drop_block_values", False):
            self.block_values_dropped = True

    def _drop_level(self, k: int, lv: "_FastLevel") -> None:
        """Release level ``k``'s block values (A, the smoother inverses, W, P, R) after the fast fill (M5
        ``drop_block_values``).  The fast cycle reads only the scalar copies ``lv`` holds; the BlockCSR
        objects keep their index arrays (``report``, the plan) with ``val`` None, and ``lv.sources`` is re-pointed so
        the identity check of ``_fast_levels`` holds.  The fine values are rebuilt from their scalar copy on request
        (``fine_block_values``: a lazily built rescue arm starts from them).  The fine ``A.val`` is usually also the
        caller's (the camg factory's BlockCSR): the device memory is freed once the caller drops it too."""
        V = self.values[k]
        V.A = V.A.with_values(None)
        V.Sinv = None
        if V.W is not None:
            V.W = V.W.with_values(None)
        if V.P is not None:
            V.P = V.P.with_values(None)
        if V.R is not None:
            V.R = V.R.with_values(None)
        lv.sources = _fast_sources(V)

    # ------------------------------------------------------------------ block kernels
    def _block_ineligible(self) -> str | None:
        """Why the block cycle cannot run this hierarchy (None = it can).  It implements the fast cycle's
        multicolour-GS branch on every non-coarsest level (``_fast_smooth`` with no learned W, smoother "gs", a
        coloured level) with the dense LU on the coarsest level -- the classical camg / camg2w cycles and their l1
        rescue arms -- and no trouble-set correction."""
        if self.smoother != "gs":
            return f"smoother {self.smoother!r} (block cycle: gs only)"
        T = getattr(self, "trouble", None)
        if T is not None and T.get("S") is not None:
            return "trouble-set correction"
        for k, V in enumerate(self.values):
            if V.dense_lu is not None:
                if k != len(self.values) - 1:
                    return f"dense level {k} above the coarsest"
                continue
            L = self.plan.levels[k]
            if V.W is not None:
                if not getattr(self, "block_w", False):
                    return f"learned one-hop W on level {k}"
                if not (V.W.row is V.A.row or torch.equal(V.W.row, V.A.row)) or not (
                        V.W.col is V.A.col or torch.equal(V.W.col, V.A.col)):
                    return f"learned W of level {k} not on A's block pattern"
            if not L.colours or L.agg is None:
                return f"level {k} has no GS colouring"
            if V.P is None or V.R is None:
                return f"level {k} has no P / R"
        if self.values[-1].dense_lu is None:
            return "no dense coarsest level"
        return None

    @torch.no_grad()
    def _fine_donor_level(self, V: "LevelValues", lay: "FastLayout", host_order: bool):
        """The fine block level of ``fine_donor`` when this cycle's fine copies would be equal to it: a live donor on
        the block kernels with the same value storage; fine block values that are the donor's own (the very tensor,
        unmodified since the donor's fill, or the tensor the donor's ``fine_block_values`` rebuilt from that copy,
        unmodified since); the same block pattern and the same rank layout.  None otherwise, and the fine level is
        filled as before."""
        ref = getattr(self, "_fine_donor", None)
        donor = ref() if ref is not None else None
        if (not isinstance(donor, CoupledAMG) or donor is self or not getattr(donor, "block_kernels", False)
                or getattr(donor, "block_values", "fp64") != self.block_values or donor.F != self.F):
            return None
        fl = getattr(donor, "_fast", None)
        if not fl or fl[0].dense or fl[0].blk is None or fl[0].blk.A64 is None or V.A.val is None:
            return None
        val, dV = V.A.val, donor.values[0]
        rebuilt = getattr(donor, "_rebuilt_fine", None)
        own = (dV.A.val is not None and val is dV.A.val
               and getattr(donor, "_fine_fill_version", None) == _tensor_version(val))
        from_copy = rebuilt is not None and rebuilt[0]() is val and rebuilt[1] == _tensor_version(val)
        if not (own or from_copy):
            return None
        dlay = _level_layout(donor.plan, 0)
        if bool(getattr(donor.plan, "lean", False)) != host_order or list(dlay.offsets) != list(lay.offsets):
            return None
        if not (torch.equal(dlay.rank, lay.rank) and (dV.A.row is V.A.row or torch.equal(dV.A.row, V.A.row))
                and (dV.A.col is V.A.col or torch.equal(dV.A.col, V.A.col))):
            return None
        return fl[0].blk

    @torch.no_grad()
    def _block_build(self):
        """``_fast_build`` for the M3 block kernels: every non-dense level gets sliced-ELL copies of A (in the cycle's
        value dtype; the fine level also in fp64 when that dtype is fp32: the step's Krylov operator and the exact
        block values), of its smoother inverses, P and R, in the cell-interleaved rank-order layout of its
        ``FastLayout`` (the same colour order).  The patterns are cached on the plan levels like the scalar ones."""
        from Pipeline.CoupledNewton import block_kernels as BK
        plan, F = self.plan, self.F
        dtype = torch.float32 if self.block_values == "fp32" else torch.float64
        host_order = bool(getattr(plan, "lean", False))
        levels = []
        for k, V in enumerate(self.values):
            L = plan.levels[k]
            lay = _level_layout(plan, k)
            cache = _fast_cache(L)
            blay = cache.get("blk-layout")
            if blay is None:
                blay = cache["blk-layout"] = BK.BlockLayout(lay.rank, lay.offsets, F)
            if V.dense_lu is not None:
                levels.append(_FastLevel(layout=lay, sources=_fast_sources(V), dense=True,
                                         blk=_BlockLevel(layout=blay)))
                continue
            nxt = _level_layout(plan, k + 1)
            n_next = nxt.n
            # the fine level of a donor built on the same values, pattern and layout: its copies, not a second fill
            shared = self._fine_donor_level(V, lay, host_order) if k == 0 else None
            Apat = shared.A64.pattern if shared is not None else _cached_pattern(
                L, "blk-A", V.A.row, V.A.col, lambda: BK.SellPattern(lay.rank[V.A.row], lay.rank[V.A.col], L.n, L.n,
                                                                     F, F, groups=lay.offsets, host_order=host_order))
            Ppat = _cached_pattern(L, "blk-P", V.P.row, V.P.col, lambda: BK.SellPattern(
                lay.rank[V.P.row], nxt.rank[V.P.col], L.n, n_next, V.P.F, V.P.G, groups=lay.offsets,
                host_order=host_order))
            Rpat = _cached_pattern(L, "blk-R", V.R.row, V.R.col, lambda: BK.SellPattern(
                nxt.rank[V.R.row], lay.rank[V.R.col], n_next, L.n, V.R.F, V.R.G, groups=nxt.offsets,
                host_order=host_order))
            if shared is not None:
                A64, A = shared.A64, shared.A
                self.fine_shared = True
            elif dtype == torch.float64 or (k == 0 and not getattr(self, "drop_block_values", False)):
                A64 = Apat.fill(V.A.val)
                if k == 0:
                    self._fine_fill_version = _tensor_version(V.A.val)
                A = A64 if dtype == torch.float64 else A64.astype(dtype)
                if k > 0:
                    A64 = None
            else:
                A64, A = None, Apat.fill(V.A.val, dtype)
            cells = torch.cat(lay.groups)                       # cells in rank order
            blk = _BlockLevel(layout=blay, A=A, A64=A64, sinv=Apat.diagonal_store(V.Sinv[cells], dtype),
                              P=Ppat.fill(V.P.val, dtype), R=Rpat.fill(V.R.val, dtype),
                              omega=torch.tensor([self._omega_of(k)], dtype=torch.float64, device=V.A.val.device))
            if V.W is not None:                                 # block_w (eligibility checked A's pattern)
                blk.W = Apat.fill(V.W.val, dtype)
            lv = _FastLevel(layout=lay, sources=_fast_sources(V), blk=blk)
            levels.append(lv)
            if getattr(self, "drop_block_values", False):
                self._drop_level(k, lv)
        self._fast = levels
        if getattr(self, "drop_block_values", False):
            self.block_values_dropped = True

    def _blk_cycle_from(self, fl: list, vec: torch.Tensor, kind: str) -> torch.Tensor:
        """``_fast_cycle_from`` on block levels."""
        lay = fl[0].blk.layout
        return lay.from_layout(self._blk_cycle(fl, 0, lay.to_layout(vec, kind)), kind)

    def _coarse_by_lu(self, V) -> bool:
        """``COARSE_INVERSE["min_pivot"] = t > 0`` (opt-in) on a hierarchy whose gauge field is free: True (the
        coarsest level by its LU, not the explicit inverse) when the coarse LU's smallest pivot is below ``t`` times
        its largest.  The ratio is read once per factorisation (at the eager application that precedes a capture).
        A hierarchy without a free gauge field, and ``t = 0``, keep the explicit inverse as before."""
        t = float(COARSE_INVERSE.get("min_pivot", 0.0) or 0.0)
        if not t > 0 or getattr(self, "gauge_field", None) is None:
            return False
        lu = V.dense_lu[0]
        if getattr(V, "dense_pivot_of", None) is not lu:
            piv = lu.diagonal().abs()
            V.dense_pivot_ratio = float(piv.min() / piv.max().clamp_min(1e-300))
            V.dense_pivot_of = lu
        return V.dense_pivot_ratio < t

    def _coarse_gauge(self, e: torch.Tensor) -> torch.Tensor:
        """``COARSE_GAUGE["on"]`` (opt-in) on a hierarchy whose gauge field is free: the coarse correction (block
        layout, cell-interleaved) with the mean of its gauge field removed, so that no coarse correction carries the
        constant mode of the free field; otherwise ``e`` itself."""
        g = getattr(self, "gauge_field", None)
        if not COARSE_GAUGE["on"] or g is None:
            return e
        part = e.view(-1, self.F)[:, int(g)]
        part.sub_(part.mean())
        return e

    def _blk_cycle(self, fl: list, k: int, b: torch.Tensor) -> torch.Tensor:
        """``_fast_cycle`` (GS branch) on level-k block-layout vectors: pre-smoothing from zero, residual, restriction,
        the next level, prolongation added in place, post-smoothing in reverse colour order."""
        lv = fl[k]
        if lv.dense:
            # the coarsest block layout is natural and cell-interleaved: the dense LU's own order
            if COARSE_INVERSE["on"] and not self._coarse_by_lu(self.values[k]):
                # W2 (opt-in, --camg-coarse-inverse): the explicit inverse from the same LU, one GEMV per application
                V = self.values[k]
                inv = getattr(V, "dense_inv", None)
                if inv is None or getattr(V, "dense_inv_of", None) is not V.dense_lu[0]:
                    eye = torch.eye(V.dense_lu[0].shape[0], dtype=V.dense_lu[0].dtype, device=V.dense_lu[0].device)
                    inv = V.dense_inv = torch.linalg.lu_solve(*V.dense_lu, eye)
                    V.dense_inv_of = V.dense_lu[0]
                return self._coarse_gauge(torch.mv(inv, b.reshape(-1)))
            return self._coarse_gauge(torch.linalg.lu_solve(*self.values[k].dense_lu, b.reshape(-1, 1)).reshape(-1))
        B = lv.blk
        x = torch.zeros_like(b)
        dx = torch.empty_like(b)
        if B.W is not None:
            self._blk_w_smooth(B, b, x, dx, self._pre_of(k), False)
        else:
            for sweep in range(self._pre_of(k)):
                # W2 zero skip (opt-in, block_kernels.ZERO_SKIP): the first sweep starts from x = 0
                self._blk_gs_sweep(B, b, x, dx, False, fresh=sweep == 0)
        r = B.A.residual(x, b)
        e = self._blk_cycle(fl, k + 1, B.R.mv(r))
        B.P.mv_add(e, x)
        if B.W is not None:
            self._blk_w_smooth(B, b, x, dx, self._post_of(k), True)
        else:
            for _ in range(self._post_of(k)):
                self._blk_gs_sweep(B, b, x, dx, True)
        return x

    def _blk_w_smooth(self, B: "_BlockLevel", b: torch.Tensor, x: torch.Tensor, dx: torch.Tensor, sweeps: int,
                      reverse: bool) -> None:
        """``_fast_smooth``'s learned-W branch on the block kernels, in place on ``x`` (O1-HR-2r item B, ``block_w``):
        pre-smoothing = ``hybrid_pre`` forward GS sweeps, then ``sweeps`` x ``x += W (b - A x)``; post-smoothing =
        the W sweeps, then ``hybrid`` reverse GS sweeps.  The first pre-sweep from x = 0 is ``x += W b`` (the scalar
        path's ``r = b``: no residual product)."""
        fresh = not reverse                       # the pre-smoothing starts from x = 0
        if not reverse and getattr(self, "hybrid_pre", 0):
            for sweep in range(self.hybrid_pre):
                self._blk_gs_sweep(B, b, x, dx, False, fresh=sweep == 0)
            fresh = False
        for _ in range(sweeps):
            B.W.mv_add(b if fresh else B.A.residual(x, b), x)
            fresh = False
        if reverse and self.hybrid:
            for _ in range(self.hybrid):
                self._blk_gs_sweep(B, b, x, dx, True)

    @staticmethod
    def _blk_gs_sweep(B: "_BlockLevel", b: torch.Tensor, x: torch.Tensor, dx: torch.Tensor, reverse: bool,
                      fresh: bool = False) -> None:
        """One multicolour block-GS sweep in place on ``x``: per colour one fused pass (``SellMatrix.gs_colour``).
        ``fresh``: x is zero before the sweep (a forward sweep may skip the exact-zero columns, W2 zero skip)."""
        order = range(len(B.A.pattern.group_bounds) - 1)
        if fresh and not reverse:
            for c in order:
                B.A.gs_colour(c, x, b, B.sinv, B.omega, dx, fresh=True)
            return
        for c in (reversed(order) if reverse else order):
            B.A.gs_colour(c, x, b, B.sinv, B.omega, dx)

    def _block_report(self) -> dict:
        from Pipeline.CoupledNewton import block_kernels as BK
        fl = getattr(self, "_fast", None)
        lv0 = fl[1] if (fl and fl[0].blk is None and len(fl) > 1) else (fl[0] if fl else None)
        active = lv0 is not None and lv0.blk is not None
        out = {"active": active, "values": self.block_values, "fallback": self.block_fallback}
        if getattr(self, "fine_shared", False):
            out["fine_shared"] = True              # the fine copies are a donor cycle's (``fine_donor``)
        if active:
            dev = lv0.blk.layout.rank.device
            out["triton"] = BK.triton_available(dev) if dev.type == "cuda" else False
            if BK.TRITON_STATE["error"]:
                out["triton_error"] = BK.TRITON_STATE["error"]
        return out

    @torch.no_grad()
    def fine_block_values(self) -> torch.Tensor:
        """The fine level's ``[nnzb, F, F]`` block values: the tensor itself, or -- after ``drop_block_values`` --
        rebuilt from the fine level's fast scalar copy, which holds every block value once (``FastPattern.fill``
        wrote ``out_c[f, j, g] = val[order[e0 + j], f, g]`` per row group c; this is that gather inverted, exact)."""
        val = self.values[0].A.val
        if val is not None:
            return val
        lv = self._fast[0]
        if lv.blk is not None:
            # M3 block copies: the fp64 sliced-ELL copy holds every block value once (``SellPattern.unfill``, exact)
            out = lv.blk.A64.pattern.unfill(lv.blk.A64.values)
            # the rebuilt tensor, unchanged since, is this fine copy's values: a cycle built on it may share the copy
            self._rebuilt_fine = (weakref.ref(out), _tensor_version(out))
            return out
        pattern, values = lv.A.pattern, lv.A.values
        F, G = pattern.F, pattern.G
        out = torch.empty(pattern.nnzb, F, G, dtype=values.dtype, device=values.device)
        for c, (e0, e1) in enumerate(pattern.block_ranges):
            if e1 > e0:
                s0, m = pattern.starts[c], pattern.lens[c]
                out.index_copy_(0, _on_device(pattern.order[e0:e1], values.device),
                                values[s0:s0 + m].view(F, e1 - e0, G).permute(1, 0, 2))
        return out

    @contextlib.contextmanager
    def fine_values_in_place(self):
        """The fine level's ``[nnzb, F, F]`` block values (as ``fine_block_values``) held, for the duration of the
        context, in the device memory of this cycle's own fp64 sliced-ELL copy of the fine matrix; the copy waits in
        host memory meanwhile and is put back, bit for bit, at the exit.  For a cycle built from these values while
        this one is idle -- the camg factory's stagnation rescue after ``drop_block_values``: its numeric pass then
        needs no second fine-sized device tensor (16.2 GiB at the 152^3 cavity).  Inside the context neither this cycle
        nor a cycle sharing its fine copy may be applied, and a cycle built inside must not keep the values (it drops
        its block values, ``drop_block_values``) and shares this fine copy (``fine_donor``).  Without an fp64 block
        copy of the fine level (no block kernels, values not dropped) the values are rebuilt as by
        ``fine_block_values`` and nothing is parked."""
        fl = getattr(self, "_fast", None)
        blk = fl[0].blk if fl and not fl[0].dense else None
        if (self.values[0].A.val is not None or blk is None or blk.A64 is None
                or blk.A64.values.dtype != torch.float64 or not blk.A64.values.is_contiguous()):
            yield self.fine_block_values()
            return
        from Pipeline.CoupledNewton import block_kernels as BK
        pat, store = blk.A64.pattern, blk.A64.values
        nnzb, FG = pat.nnzb, pat.F * pat.G
        if store.is_cuda:
            torch.cuda.synchronize(store.device)
        host = torch.empty(store.shape, dtype=store.dtype, pin_memory=store.is_cuda)
        host.copy_(store)
        flat = store[: nnzb * FG].view(nnzb, FG)          # the copy's own memory, holding the block values in place
        q = torch.arange(FG) * pat.C
        try:
            for a in range(0, nnzb, BK._FILL_CHUNK):
                b = min(a + BK._FILL_CHUNK, nnzb)
                pos = pat._positions(a, b).cpu()[:, None] + q[None, :]
                flat.index_copy_(0, pat.order[a:b].to(store.device, non_blocking=True),
                                 host[pos].to(store.device, non_blocking=store.is_cuda))
                del pos
            val = flat.view(nnzb, pat.F, pat.G)
            # these are the copy's values: a cycle built on them may share the copy (``fine_donor``)
            self._rebuilt_fine = (weakref.ref(val), _tensor_version(val))
            yield val
        finally:
            if store.is_cuda:
                torch.cuda.synchronize(store.device)
            store.copy_(host)
            if store.is_cuda:
                torch.cuda.synchronize(store.device)
            del host

    def fine_operator(self):
        """``v -> A_0 v`` on flat field-blocked vectors: the fine-level matrix this hierarchy was built on.

        With ``jacobian.assemble(fixed_pattern="blocks")`` there is no scalar CSR of J; the fine level's matrix IS that
        J (the BlockCSR the hierarchy was built on), and its fast scalar copy applies it at the cost of one cuSPARSE
        SpMV plus the layout gathers.  Summation order inside a row follows the copy's block order, so a product
        agrees with a field-blocked CSR's to rounding.  Without a fast fine level (fast kernels off, or a fine level
        that is the dense coarsest) the block matrix itself is applied (``BlockCSR.matvec``)."""
        F = self.F

        def apply(v: torch.Tensor) -> torch.Tensor:
            v = v.reshape(-1)
            if getattr(self, "fast_kernels", False) and self._fast_eligible(v):
                lv = self._fast_levels()[0]
                if not lv.dense and lv.blk is not None:
                    # M3 block copies: the fp64 sliced-ELL copy of the fine matrix (fp64 values whatever the cycle's)
                    lay = lv.blk.layout
                    return lay.from_layout(lv.blk.A64.mv(lay.to_layout(v.contiguous(), "fb")), "fb")
                if not lv.dense:
                    lay = lv.layout
                    return lay.from_layout(lv.A.mv(lay.to_layout(v.contiguous(), "fb")), "fb")
            A = self.values[0].A
            if A.val is None:                      # M5 drop_block_values: rebuilt from the fast copy (exact)
                A = A.with_values(self.fine_block_values())
            n = A.shape[0]
            return A.matvec(v.reshape(F, n).t()).t().reshape(-1)

        return apply

    def _fast_levels(self) -> list:
        fl = self._fast
        if (fl is None or len(fl) != len(self.values)
                or any(any(a is not b for a, b in zip(lv.sources, _fast_sources(V)))
                       for lv, V in zip(fl, self.values))):
            self._fast_build()                 # first use, or a level's values were replaced since the build
        return self._fast

    def _fast_apply(self, vec: torch.Tensor, kind: str) -> torch.Tensor:
        """One V-cycle on a flat field-blocked (``"fb"``) or cell-interleaved (``"ci"``) vector."""
        return self._fast_cycle_from(self._fast_levels(), vec, kind)

    def _fast_cycle_from(self, fl: list, vec: torch.Tensor, kind: str) -> torch.Tensor:
        """``_fast_apply`` on the already checked fast levels ``fl`` (also what a CUDA graph captures)."""
        if fl and fl[0].blk is not None:
            return self._blk_cycle_from(fl, vec, kind)              # M3 block kernels
        lay = fl[0].layout
        return lay.from_layout(self._fast_cycle(fl, 0, lay.to_layout(vec, kind)), kind)

    # ------------------------------------------------------------------ CUDA graph of the fast cycle
    def _graphed(self, vec: torch.Tensor, kind: str) -> torch.Tensor:
        """``_fast_apply(vec, kind)``, replayed from a CUDA graph (``cuda_graph=True``; GPU optimisation M1c,
        docs/research/2026-10-07-gpu-optimisation-roadmap.md).

        Why: the fast V-cycle is host-dispatch bound.  It launches ~1,000 small kernels per application (fine level
        15 colours x 4 sweeps x 5 kernels, level 1 33 colours x 4 x 5 with almost no work each), and the production
        cycle fits ~8.8 ms of mesh-independent overhead + 0.79 ms per million blocks (13.9 ms DHC64, 22.0 ms DHC88).
        A graph replays the same kernels in the same order on the same operands, so a replay equals the eager cycle
        up to cuSPARSE's own run-to-run rounding (its default CSR SpMV algorithm does not promise bitwise results).

        Per hierarchy and per (vector kind, length, dtype): the FIRST application runs eagerly and is the answer.
        Then the cycle is warmed up on a persistent side stream with a private copy of the input, captured from that
        static buffer into a private memory pool, replayed once and compared with the eager answer: a relative
        2-norm difference <= ``GRAPH_REL_TOL`` keeps the graph, a larger one leaves that kind eager (recorded).  An
        exception during the capture leaves it eager too and turns graphs off for the process (``GRAPH_STATS``),
        since a failed capture may leave allocator or stream state behind; running out of device memory is retried
        once after the allocator's cached blocks are released, and a second one leaves only that kind eager
        (``_capture``).  Every later application copies ``vec``
        into the static buffer, replays and returns a CLONE of the static output (the next replay overwrites it).
        The fast copies are identity-checked exactly as the eager path does (``_fast_levels``): when a level's values
        were replaced, the copies are refilled and a graph that read the old ones is dropped and recaptured.  Vectors
        not on a CUDA device run eagerly.  ``report()['cuda_graph']``: captured keys, replays, the check's relative
        difference and bitwise flag, the pool's reserved MiB, the last error."""
        fl = self._fast_levels()
        if not vec.is_cuda or not GRAPH_STATS["enabled"]:
            return self._fast_cycle_from(fl, vec, kind)
        key = (kind, int(vec.numel()), vec.dtype, vec.device)
        g = self._graphs.get(key)
        if g is not None:
            if g == "eager":
                return self._fast_cycle_from(fl, vec, kind)
            if g["levels"] is fl:
                g["static"].copy_(vec)
                g["graph"].replay()
                self._graph_info["replays"] += 1
                GRAPH_STATS["replays"] += 1
                return g["out"].clone()
            self._graphs.pop(key)          # the fast copies were refilled since the capture: it read the old ones
            g = None
        eager = self._fast_cycle_from(fl, vec, kind)
        self._capture(key, fl, vec, eager)
        return eager

    def _capture_attempt(self, fl: list, vec: torch.Tensor, eager: torch.Tensor, kind: str):
        """One capture of the fast cycle and one checked replay: ``(graph, static, out, rel, bitwise, pool_mib)``;
        raises what the capture raised."""
        device = vec.device
        with torch.cuda.device(device):
            current = torch.cuda.current_stream(device)
            side = _graph_stream(device)
            static = vec.detach().clone()
            side.wait_stream(current)
            reserved = torch.cuda.memory_reserved(device)
            with torch.cuda.stream(side):
                # warm-up on the capture stream: its library handles / workspaces exist before the capture
                self._fast_cycle_from(fl, static, kind)
                graph = torch.cuda.CUDAGraph()
                graph.capture_begin()
                try:
                    out = self._fast_cycle_from(fl, static, kind)
                finally:
                    graph.capture_end()
            current.wait_stream(side)
            pool_mib = (torch.cuda.memory_reserved(device) - reserved) / 2 ** 20
            graph.replay()
            rel = float((out - eager).norm() / eager.norm().clamp_min(1e-300))
            bitwise = bool(torch.equal(out, eager))
        return graph, static, out, rel, bitwise, pool_mib

    @torch.no_grad()
    def _capture(self, key, fl: list, vec: torch.Tensor, eager: torch.Tensor) -> None:
        """Capture the fast cycle for ``key`` and check one replay against ``eager`` (see ``_graphed``).

        Out of device memory during the capture: inside a capture the caching allocator cannot return its cached,
        unused blocks to the driver, so the graph's private pool can fail with many GiB reserved but unused.  The
        capture is then retried once after those blocks are released (``torch.cuda.empty_cache``); if it fails
        again, this key alone stays eager and graphs stay on for the rest of the process.  Any other failure turns
        graphs off for the process, as before.  The numerics are those of the eager cycle either way."""
        kind, info, device = key[0], self._graph_info, vec.device
        try:
            got = self._capture_attempt(fl, vec, eager, kind)
        except Exception as exc:           # noqa: BLE001 -- any capture failure leaves the cycle eager
            if not _out_of_memory(exc):
                self._capture_failed(key, exc)
                return
            GRAPH_STATS["oom_retries"] = GRAPH_STATS.get("oom_retries", 0) + 1
            info["oom_retries"] = info.get("oom_retries", 0) + 1
            exc = None
            try:
                torch.cuda.synchronize(device)
                torch.cuda.empty_cache()
                got = self._capture_attempt(fl, vec, eager, kind)
            except Exception as again:     # noqa: BLE001
                if not _out_of_memory(again):
                    self._capture_failed(key, again)
                    return
                GRAPH_STATS["failures"] += 1
                GRAPH_STATS["oom_eager"] = GRAPH_STATS.get("oom_eager", 0) + 1
                GRAPH_STATS["last_error"] = info["error"] = f"{type(again).__name__}: {again}"[:400]
                self._graphs[key] = "eager"
                warnings.warn(f"CoupledAMG(cuda_graph=True): capturing the fast V-cycle ran out of device memory "
                              f"twice ({info['error']}); this hierarchy applies {kind} eagerly, graphs stay on "
                              "elsewhere", RuntimeWarning, stacklevel=3)
                return
        graph, static, out, rel, bitwise, pool_mib = got
        info["rel_diff"] = rel if info["rel_diff"] is None else max(info["rel_diff"], rel)
        info["bitwise"] = bitwise if info["bitwise"] is None else (info["bitwise"] and bitwise)
        info["pool_mib"] = round(info["pool_mib"] + pool_mib, 1)
        GRAPH_STATS["max_rel_diff"] = max(GRAPH_STATS["max_rel_diff"], rel)
        if not rel <= GRAPH_REL_TOL:
            GRAPH_STATS["rejected"] += 1
            GRAPH_STATS["last_error"] = info["error"] = (f"replay differs from the eager cycle by {rel:.3e} "
                                                         f"(> {GRAPH_REL_TOL:.0e}): {kind} kept eager")
            self._graphs[key] = "eager"
            return
        GRAPH_STATS["captures"] += 1
        GRAPH_STATS["bitwise"] += int(bitwise)
        GRAPH_STATS["pool_mib"] = round(GRAPH_STATS["pool_mib"] + pool_mib, 1)
        info["captured"].append(kind)
        try:
            from Parallel.part import _register_captured_graph
            _register_captured_graph(graph)
        except Exception:
            pass
        self._graphs[key] = {"levels": fl, "graph": graph, "static": static, "out": out}

    def _capture_failed(self, key, exc: BaseException) -> None:
        """A capture failure other than running out of device memory: this key eager and graphs off for the process
        (a failed capture may leave allocator or stream state behind)."""
        info = self._graph_info
        GRAPH_STATS["failures"] += 1
        GRAPH_STATS["enabled"] = False
        GRAPH_STATS["last_error"] = info["error"] = f"{type(exc).__name__}: {exc}"[:400]
        self._graphs[key] = "eager"
        warnings.warn(f"CoupledAMG(cuda_graph=True): capturing the fast V-cycle failed ({info['error']}); this "
                      "process applies every V-cycle eagerly from now on", RuntimeWarning, stacklevel=4)

    def _fast_cycle(self, fl: list, k: int, b: torch.Tensor) -> torch.Tensor:
        """``_cycle`` on level-k layout vectors.  Every ``x`` here is a tensor this cycle allocated, so the updates
        ``x + dx`` happen in place; ``b`` is never written (on level 0 it may be the caller's vector)."""
        lv = fl[k]
        if lv.dense:
            # the coarsest layout is natural (field-major); the dense LU is cell-interleaved
            n, F = lv.layout.n, self.F
            rhs = b.view(F, n).t().reshape(-1, 1)
            return torch.linalg.lu_solve(*self.values[k].dense_lu, rhs).reshape(n, F).t().reshape(-1)
        x = self._fast_smooth(k, lv, b, None, self._pre_of(k), False)
        trouble = lv.trouble is not None
        if trouble and self.trouble_where == "both":
            x = self._fast_trouble_correct(lv, b, x)
        r = b if x is None else b - lv.A.mv(x)
        Pe = lv.P.mv(self._fast_cycle(fl, k + 1, lv.R.mv(r)))
        x = Pe if x is None else x.add_(Pe)
        x = self._fast_smooth(k, lv, b, x, self._post_of(k), True)
        return self._fast_trouble_correct(lv, b, x) if trouble else x

    def _fast_smooth(self, k: int, lv: _FastLevel, b: torch.Tensor, x, sweeps: int, reverse: bool):
        """``_smooth`` on layout vectors: the same branches in the same order."""
        L, V = self.plan.levels[k], self.values[k]
        if not reverse and getattr(self, "hybrid_pre", 0) and V.W is not None and L.colours:
            for _ in range(self.hybrid_pre):                 # O1-HR sandwich (see __init__)
                x = self._fast_gs_sweep(lv, b, x, False)
        for _ in range(sweeps):
            if V.W is not None:
                r = b if x is None else b - lv.A.mv(x)
                dx = lv.W.mv(r)
                x = dx if x is None else x.add_(dx)
            elif self.smoother == "cheb":
                hi, lo = V.rho, V.rho / self.cheb_ratio
                theta, delta = 0.5 * (hi + lo), 0.5 * (hi - lo)
                sigma = theta / delta
                r = b if x is None else b - lv.A.mv(x)
                d = lv.Sinv.mv(r) / theta
                x = d if x is None else x + d
                rho0 = 1.0 / sigma
                for _ in range(self.cheb_degree - 1):
                    r = b - lv.A.mv(x)
                    rho1 = 1.0 / (2.0 * sigma - rho0)
                    d = rho1 * rho0 * d + (2.0 * rho1 / delta) * lv.Sinv.mv(r)
                    x = x + d
                    rho0 = rho1
            elif self.smoother == "jacobi" or not L.colours:
                r = b if x is None else b - lv.A.mv(x)
                dx = self.jacobi_omega * lv.Sinv.mv(r)
                x = dx if x is None else x.add_(dx)
            else:
                x = self._fast_gs_sweep(lv, b, x, reverse, omega=self._omega_of(k))
        if reverse and self.hybrid and V.W is not None and L.colours:
            for _ in range(self.hybrid):
                x = self._fast_gs_sweep(lv, b, x, reverse, omega=self._omega_of(k))
        return x

    def _fast_gs_sweep(self, lv: _FastLevel, b: torch.Tensor, x, reverse: bool, omega=None) -> torch.Tensor:
        """One multicolour block GS sweep, in place on ``x``: per colour c (a contiguous slice of the layout)
        ``x_c += omega Sinv_c (b_c - A_c x)``, two cuSPARSE products and three elementwise kernels."""
        if x is None:
            x = torch.zeros_like(b)
        order = range(len(lv.layout.slices))
        for c in (reversed(order) if reverse else order):
            s0, s1 = lv.layout.slices[c]
            if s1 == s0:
                continue
            t = lv.A.group(c) @ x
            torch.sub(b[s0:s1], t, out=t)
            dx = lv.Sinv.group(c) @ t
            w = self.gs_omega if omega is None else float(omega)      # O1-C: the level's damping
            if w != 1.0:
                dx.mul_(w)
            x[s0:s1].add_(dx)
        return x

    def _fast_trouble_correct(self, lv: _FastLevel, b: torch.Tensor, x):
        """``_trouble_correct`` on level-0 layout vectors: x_S += A_SS^-1 (b - A x)_S."""
        T, tf = self.trouble, lv.trouble
        if x is None:
            x = torch.zeros_like(b)
        m, F = tf["m"], self.F
        r = b.index_select(0, tf["pos"]) - tf["A"].mv(x)                      # field-major over S
        dx = torch.linalg.lu_solve(*T["lu"], r.view(F, m).t().reshape(-1, 1)).reshape(m, F).t().reshape(-1)
        return x.index_add_(0, tf["pos"], dx)

    def _fast_memory(self) -> dict:
        """What the fast kernels hold beyond the block hierarchy (``report()['fast_kernels']``): scalar values,
        index arrays (patterns and layouts, shared with other cycles on the same plan), and for scale the
        hierarchy's own block values.  Views are counted once, by storage."""
        seen, block = {}, {}

        def add(store, t, kind):
            if torch.is_tensor(t) and t.numel():
                st = t.untyped_storage()
                store.setdefault((st.data_ptr(), str(t.device)), (kind, st.nbytes()))

        patterns, idt = [], None
        for lv in self._fast:
            for t in lv.layout.tensors():
                add(seen, t, "index")
            mats = [lv.A, lv.Sinv, lv.W, lv.P, lv.R] + ([lv.trouble["A"]] if lv.trouble else [])
            for M in mats:
                if M is not None:
                    add(seen, M.values, "values")
                    patterns.append(M.pattern)
                    idt = idt or str(M.pattern.col.dtype).replace("torch.", "")
            if lv.trouble:
                add(seen, lv.trouble["pos"], "index")
            if lv.blk is not None:
                # M3 block copies: sliced-ELL values (A in the cycle's dtype, the fp64 fine copy, smoother inverses,
                # P, R), their patterns and the block layout's gathers
                B = lv.blk
                for t in B.layout.tensors():
                    add(seen, t, "index")
                for M in (B.A, B.A64, B.P, B.R):
                    if M is not None:
                        add(seen, M.values, "values")
                        patterns.append(M.pattern)
                        idt = idt or "sell-" + str(M.values.dtype).replace("torch.", "")
                add(seen, B.sinv, "values")
        for P in patterns:
            for t in P.tensors():
                add(seen, t, "index")
        for V in self.values:
            for t in [V.A.val, V.Sinv] + [M.val for M in (V.W, V.P, V.R) if M is not None]:
                add(block, t, "block")
        mib = lambda store, kind: round(sum(b for k, b in store.values() if k == kind) / 2 ** 20, 2)
        values, index = mib(seen, "values"), mib(seen, "index")
        return {"extra_mib": round(values + index, 2), "values_mib": values, "index_mib": index,
                "hierarchy_block_mib": mib(block, "block"), "index_dtype": idt}


# ======================================================================= plan sharing
def plan_kwargs(**cycle_kw) -> dict:
    """The ``CoupledAMGPlan`` keywords ``CoupledAMG(A, **cycle_kw)`` builds its plan with.

    Every keyword the plan constructor takes, from ``cycle_kw`` or else ``CoupledAMG``'s default (its
    ``restriction`` default is ``"transpose"``, the plan's own is ``"pg"``: the cycle's wins, as in
    ``CoupledAMG.__init__``).  Read off the two signatures, so a keyword added to both is picked up."""
    import inspect
    # (``level0_from`` lends a level; it is not a plan setting)
    plan_params = [p for p in inspect.signature(CoupledAMGPlan.__init__).parameters.values()
                   if p.kind is inspect.Parameter.KEYWORD_ONLY and p.name != "level0_from"]
    cycle_defaults = inspect.signature(CoupledAMG.__init__).parameters
    return {p.name: cycle_kw.get(p.name, cycle_defaults[p.name].default if p.name in cycle_defaults else p.default)
            for p in plan_params}


def plan_key(**cycle_kw) -> tuple:
    """Hashable key of ``plan_kwargs(**cycle_kw)``: two cycles with equal keys build the same plan from the same
    matrix, so a cache may hand both one plan (``solve_with_coupled_newton --camg-reuse-plan``).  ``l1`` is
    normalised as the plan normalises it (True / False / "signed"); a per-level setting (``level_plan``,
    ``coarse_split``: lists / dicts) enters as its canonical JSON text."""
    import json
    kw = plan_kwargs(**cycle_kw)
    kw["l1"] = kw["l1"] if kw["l1"] == "signed" else bool(kw["l1"])
    kw["diag_eps"] = float(kw["diag_eps"])
    for k, v in kw.items():
        if isinstance(v, (list, tuple, dict)):
            kw[k] = json.dumps(v, sort_keys=True)
    return tuple(sorted(kw.items()))
