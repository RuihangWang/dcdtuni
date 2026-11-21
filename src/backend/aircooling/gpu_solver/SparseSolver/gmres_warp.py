"""Fused Gram-Schmidt kernels for ``SparseSolver.gmres`` (Warp backend).

``docs/research/2026-10-14-warp-backend.md`` section 5 (the Arnoldi step on the device, fused projections).
``gmres`` orthogonalises each new Krylov vector by classical Gram-Schmidt with one DGKS re-orthogonalisation
(``_REORTHOGONALISE_BELOW``): ``h = V w``, ``w -= h V``, ``|w|``, and when the projection cancelled most of ``w`` the
same again -- four GEMV passes over the basis through cuBLAS, three norms, and four host read-backs (three
``float(norm)`` and the Hessenberg column) per iteration.  On the W01 Jacobian (5.6M unknowns) the re-orthogonalisation
runs in 98 % of the iterations (W1b log: "reorth 246" of 248).

``FusedOrth`` computes the same step with four bandwidth kernels and ONE read-back:

* ``mdot``: the ``k`` inner products ``V[:k] w`` and ``w . w`` in one pass over ``V[:k]`` and ``w`` (per column chunk a
  partial per row; the partials are then summed in a fixed order by ``_reduce_rows``: deterministic);
* ``combine``: ``w - (h V[:k])`` and the partial sums of its square in one pass;
* the DGKS test on the device (``|w'| <= 1/sqrt(2) |w|``, the same comparison of the same two square roots), and the
  second projection / combination masked by its outcome (a skipped pass returns ``w'`` exactly: ``w' - 0``);
* the Hessenberg column ``h1 + flag h2``, the length, the height and the flag in one small buffer, read back once;
  the normalised vector written straight into the basis row.
The arithmetic is the iteration's: the same projections in the same order of operations per entry, with sums taken in
another (fixed) order than cuBLAS's -- rounding-level differences, so the iteration counts are those of the default
path (the W2 gates check it).  Off by default: ``gmres(fused_orth=True)``.

Two implementations of the same kernels, chosen by ``BACKEND["impl"]``
(entry flag ``--krylov-orth-impl``) when a workspace is built:

* ``"triton"`` (default; the W2 kernels, unchanged): Triton JIT kernels.  Every new process pays Triton's first use at
  its first fused step -- ``triton_key()`` hashing the Triton install (~0.3 s), the first launch from the cache
  (~0.3 s) and a cache lookup per kernel.  Triton is imported, and its kernels defined,
  on the first use of this implementation only (``_load_triton``).
* ``"warp"``: NVIDIA Warp kernels (``_warp_kernels``), compiled once into the Warp kernel cache (one module, loaded from
  the warm cache in milliseconds).  The same arithmetic per entry (every basis entry read into fp64, fp64 products,
  the projections summed first and the difference taken after; the DGKS test on the same square roots; the division
  by the height), deterministic fixed-order reductions (per-chunk partials written by one block each, then one block
  per reduced row summing them in a fixed order; Warp's block reductions ``tile_dot`` / ``tile_sum`` are fixed
  trees -- no atomics in any sum), the same single read-back per iteration.  The last two reductions of a step also
  carry the DGKS test and the Hessenberg column (8 launches per step instead of 10).  Every launch of a workspace is
  recorded once (``wp.launch(record_cmd=True)``) and replayed; per call only the input vector's address and the column
  count change (ctypes fields shared by the recorded launches).  With this implementation selected the module never
  imports Triton.
"""
from __future__ import annotations

import ctypes
import importlib.util
import math
import sys
from types import SimpleNamespace
from typing import Any  # noqa: F401 -- resolved by the Warp kernels' (string) annotations

import torch

# W10b-A: availability without importing.  Triton is imported (and its kernels defined) on the first use of the Triton
# implementation (``_load_triton``), Warp on the first use of the Warp one (``_warp_kernels``).
HAVE_TRITON = importlib.util.find_spec("triton") is not None
HAVE_WARP = importlib.util.find_spec("warp") is not None
triton = tl = None
wp = None                          # the Warp module once ``_warp_kernels`` ran (its kernels' annotations read it)

DGKS = 0.7071067811865476          # gmres._REORTHOGONALISE_BELOW
TUNE = {"block": 2048, "rows": 4, "warps": 8, "rblock": 1024}
# W10b-A: the implementation of the next workspace ("triton" | "warp"; ``--krylov-orth-impl``), and the Warp kernels'
# shape: entries per block of a vector pass (tile), threads per block, partials per step of a reduction (rtile)
BACKEND = {"impl": "triton"}
WTUNE = {"tile": 2048, "threads": 256, "rtile": 1024}

_mdot_kernel = _reduce_rows = _reduce_one = _combine_kernel = _dgks_kernel = _column_kernel = _store_kernel = None
_lincomb_kernel = _gather_row_kernel = _store_row_kernel = None


def _load_triton() -> bool:
    """Import Triton and define the Triton kernels, once (the first use of the Triton implementation).  False when
    Triton cannot be imported (the caller then keeps the default path, as an import failure did before W10b-A)."""
    global triton, tl, HAVE_TRITON, _mdot_kernel, _reduce_rows, _reduce_one, _combine_kernel, _dgks_kernel
    global _column_kernel, _store_kernel, _lincomb_kernel, _gather_row_kernel, _store_row_kernel
    if _mdot_kernel is not None:
        return True
    if not HAVE_TRITON:
        return False
    try:                           # pragma: no cover - availability depends on the build
        import triton as _triton
        import triton.language as _tl
    except Exception:              # noqa: BLE001
        HAVE_TRITON = False
        return False
    triton, tl = _triton, _tl

    @triton.jit
    def _mdot_kernel(V, stride, w, k_ptr, part, NP, n, M, skip_ptr,
                     BLOCK: tl.constexpr, ROWS: tl.constexpr, SKIP: tl.constexpr):
        """part[i, pid] = sum_{j in chunk pid} V[i, j] w[j] for i < k, and part[M, pid] = sum_j w[j]^2 (row M is the
        workspace's last row, whatever k).  With SKIP and skip_ptr[0] == 0 nothing is read (the masked second
        pass: its partials are never used)."""
        pid = tl.program_id(0)
        offs = pid.to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
        mask = offs < n
        k = tl.load(k_ptr)
        if SKIP:
            k = tl.where(tl.load(skip_ptr) != 0, k, 0)
        x = tl.load(w + offs, mask=mask, other=0.0)
        r = tl.arange(0, ROWS)
        for i0 in range(0, k, ROWS):
            rows = i0 + r
            rmask = rows < k
            v = tl.load(V + rows[:, None].to(tl.int64) * stride + offs[None, :],
                        mask=rmask[:, None] & mask[None, :], other=0.0).to(tl.float64)
            tl.store(part + rows.to(tl.int64) * NP + pid, tl.sum(v * x[None, :], axis=1), mask=rmask)
        tl.store(part + M.to(tl.int64) * NP + pid, tl.sum(x * x, axis=0))

    @triton.jit
    def _reduce_rows(part, out, k_ptr, NP, M, RBLOCK: tl.constexpr):
        """out[i] = sum_p part[i, p] for i < k and for i = M, in a fixed order."""
        i = tl.program_id(0)
        k = tl.load(k_ptr)
        if (i < k) | (i == M):
            acc = tl.zeros([RBLOCK], dtype=tl.float64)
            for s in range(0, NP, RBLOCK):
                o = s + tl.arange(0, RBLOCK)
                acc += tl.load(part + i.to(tl.int64) * NP + o, mask=o < NP, other=0.0)
            tl.store(out + i, tl.sum(acc, axis=0))

    @triton.jit
    def _reduce_one(part, out, NP, RBLOCK: tl.constexpr):
        """out[0] = sum_p part[p], in a fixed order."""
        acc = tl.zeros([RBLOCK], dtype=tl.float64)
        for s in range(0, NP, RBLOCK):
            o = s + tl.arange(0, RBLOCK)
            acc += tl.load(part + o, mask=o < NP, other=0.0)
        tl.store(out, tl.sum(acc, axis=0))

    @triton.jit
    def _combine_kernel(V, stride, h, w, out, k_ptr, npart, n, skip_ptr,
                        BLOCK: tl.constexpr, ROWS: tl.constexpr, SKIP: tl.constexpr):
        """out = w - sum_{i < k} h[i] V[i] (the sum first, then the difference: gmres's ``w - combine``), and
        npart[pid] = the chunk's sum of out^2.  With SKIP and skip_ptr[0] == 0: out = w."""
        pid = tl.program_id(0)
        offs = pid.to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
        mask = offs < n
        k = tl.load(k_ptr)
        if SKIP:
            k = tl.where(tl.load(skip_ptr) != 0, k, 0)
        x = tl.load(w + offs, mask=mask, other=0.0)
        r = tl.arange(0, ROWS)
        s = tl.zeros([BLOCK], dtype=tl.float64)
        for i0 in range(0, k, ROWS):
            rows = i0 + r
            rmask = rows < k
            c = tl.load(h + rows, mask=rmask, other=0.0)
            v = tl.load(V + rows[:, None].to(tl.int64) * stride + offs[None, :],
                        mask=rmask[:, None] & mask[None, :], other=0.0).to(tl.float64)
            s += tl.sum(c[:, None] * v, axis=0)
        y = x - s
        tl.store(out + offs, y, mask=mask)
        tl.store(npart + pid, tl.sum(y * y, axis=0))

    @triton.jit
    def _dgks_kernel(h1, ss_w, ss1, flag, k_ptr, dgks_ptr):
        """flag = |w'| <= DGKS |w| (the iteration's comparison of the two square roots; the constant is read as
        float64 from memory: a Python float would be a float32 literal here)."""
        length = tl.sqrt(tl.load(ss_w))
        height = tl.sqrt(tl.load(ss1))
        tl.store(flag, (height <= tl.load(dgks_ptr) * length).to(tl.int32))

    @triton.jit
    def _column_kernel(h1, h2, ss_w, ss1, ss2, flag, k_ptr, col, KMAX, RB: tl.constexpr):
        """col[:k] = h1 + flag h2 (h1 alone without the second pass), col[KMAX] = |w|, col[KMAX + 1] = height,
        col[KMAX + 2] = flag."""
        k = tl.load(k_ptr)
        f = tl.load(flag)
        for s in range(0, k, RB):
            o = s + tl.arange(0, RB)
            m = o < k
            a = tl.load(h1 + o, mask=m, other=0.0)
            b = tl.load(h2 + o, mask=m & (f != 0), other=0.0)
            tl.store(col + o, tl.where(f != 0, a + b, a), mask=m)
        tl.store(col + KMAX, tl.sqrt(tl.load(ss_w)))
        hh = tl.where(f != 0, tl.sqrt(tl.load(ss2)), tl.sqrt(tl.load(ss1)))
        tl.store(col + KMAX + 1, hh)
        tl.store(col + KMAX + 2, f.to(tl.float64))

    @triton.jit
    def _store_kernel(y, dst, col, KMAX, n, BLOCK: tl.constexpr):
        """dst = y / height (gmres's ``basis[column + 1] = w / height``)."""
        pid = tl.program_id(0)
        offs = pid.to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
        mask = offs < n
        hh = tl.load(col + KMAX + 1)
        tl.store(dst + offs, tl.load(y + offs, mask=mask, other=0.0) / hh, mask=mask)

    @triton.jit
    def _lincomb_kernel(V, stride, c, out, k, n, BLOCK: tl.constexpr, ROWS: tl.constexpr):
        """out = sum_{i < k} c[i] V[i] in fp64 (the cycle's update ``coordinates @ V[:k]`` for an fp32 basis)."""
        pid = tl.program_id(0)
        offs = pid.to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
        mask = offs < n
        r = tl.arange(0, ROWS)
        s = tl.zeros([BLOCK], dtype=tl.float64)
        for i0 in range(0, k, ROWS):
            rows = i0 + r
            rmask = rows < k
            cc = tl.load(c + rows, mask=rmask, other=0.0)
            v = tl.load(V + rows[:, None].to(tl.int64) * stride + offs[None, :],
                        mask=rmask[:, None] & mask[None, :], other=0.0).to(tl.float64)
            s += tl.sum(cc[:, None] * v, axis=0)
        tl.store(out + offs, s, mask=mask)

    # ---------------------------------------------------------------------------------------------- device loop
    @triton.jit
    def _gather_row_kernel(V, stride, st, dst, n, BLOCK: tl.constexpr):
        """dst = V[st[0]] (the Arnoldi vector of the current column)."""
        pid = tl.program_id(0)
        offs = pid.to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
        mask = offs < n
        j = tl.load(st).to(tl.int64)
        tl.store(dst + offs, tl.load(V + j * stride + offs, mask=mask, other=0.0).to(tl.float64), mask=mask)

    @triton.jit
    def _store_row_kernel(y, V, stride, st, col, KMAX, n, BLOCK: tl.constexpr):
        """V[st[0] + 1] = y / height (gmres's ``basis[column + 1] = w / height``)."""
        pid = tl.program_id(0)
        offs = pid.to(tl.int64) * BLOCK + tl.arange(0, BLOCK)
        mask = offs < n
        j = tl.load(st).to(tl.int64) + 1
        hh = tl.load(col + KMAX + 1)
        tl.store(V + j * stride + offs, tl.load(y + offs, mask=mask, other=0.0) / hh, mask=mask)

    return True


# ======================================================================================== Warp kernels (W10b-A)
_WK: dict = {}


def _warp_kernels(tile: int | None = None, threads: int | None = None, rtile: int | None = None):
    """The Warp kernels of one shape (``WTUNE`` by default), defined once per process into ONE named Warp module
    (``SparseSolver.gmres_warp.orth_t<tile>_b<threads>_r<rtile>``: one cache entry, one module load; every launch uses
    ``threads`` per block so the module is built for that block size only).  ``tile``, ``rtile`` and ``threads`` are
    compile-time constants of the module (tile shapes); every other size is a kernel argument.

    * ``mdot``: block ``p`` loads its ``tile`` entries of ``w`` once and, for each row ``i < k``, writes
      ``part[i, p] = V[i, chunk] . w[chunk]`` (``tile_dot``: per-thread fp64 sums of its entries, then a fixed tree
      over the threads), and ``part[M, p] = w[chunk] . w[chunk]``;
    * ``reduce_rows``: block ``i`` sums ``part[i, :]`` (a ``rtile``-wide accumulator over the partials in order, then
      ``tile_sum``) for ``i < k`` and ``i = M``;
    * ``combine``: ``s = sum_{i < k} h[i] V[i, chunk]`` in fp64 (rows in order), ``out = w - s``, the chunk's ``out . out``;
    * ``reduce_dgks``: the first pass's squared height, and the DGKS flag ``sqrt(ss1) <= DGKS sqrt(|w|^2)``;
    * ``reduce_column``: the second pass's squared height and the Hessenberg column ``h1 + flag h2``, the length, the
      height and the flag (the Triton ``_reduce_one`` + ``_dgks_kernel`` / ``_column_kernel`` pairs, one launch each);
    * ``store`` / ``lincomb`` / ``gather_row`` / ``store_row``: as the Triton kernels of the same names.
    ``k`` is the host's column count (``kh >= 0``) or read on the device from ``kk[0]`` (``kh < 0``: the device loop);
    the second pass reads ``k = 0`` when the DGKS flag is 0 (``use_skip``).  The basis kernels are generic in the
    basis' dtype (fp64 / fp32: every entry read into fp64); both overloads are declared here, before any launch, so
    the module is hashed and loaded once."""
    global wp
    tile = int(WTUNE["tile"] if tile is None else tile)
    threads = int(WTUNE["threads"] if threads is None else threads)
    rtile = int(WTUNE["rtile"] if rtile is None else rtile)
    key = (tile, threads, rtile)
    K = _WK.get(key)
    if K is not None:
        return K
    import warp
    wp = warp
    wp.config.quiet = True
    wp.init()
    mod = f"{__name__}.orth_t{tile}_b{threads}_r{rtile}"
    wp.set_module_options({"enable_backward": False}, module=mod)
    TILE, RT, NT = tile, rtile, threads

    @wp.func(module=mod)
    def _count(kk: wp.array(dtype=wp.int32), kh: wp.int32, skip: wp.array(dtype=wp.int32), use_skip: wp.int32):
        k = kh
        if kh < 0:
            k = kk[0]
        if use_skip != 0:
            if skip[0] == 0:
                k = wp.int32(0)
        return k

    @wp.kernel(enable_backward=False, module=mod)
    def mdot(V: wp.array2d(dtype=Any), w: wp.array(dtype=wp.float64), kk: wp.array(dtype=wp.int32), kh: wp.int32,
             skip: wp.array(dtype=wp.int32), use_skip: wp.int32, part: wp.array2d(dtype=wp.float64), M: wp.int32):
        p = wp.tid()
        k = _count(kk, kh, skip, use_skip)
        off = p * TILE
        x = wp.tile_load(w, shape=TILE, offset=off)
        for i in range(k):
            v = wp.tile_astype(wp.tile_load(V[i], shape=TILE, offset=off), dtype=wp.float64)
            wp.tile_store(part[i], wp.tile_dot(v, x), offset=p)
        wp.tile_store(part[M], wp.tile_dot(x, x), offset=p)

    @wp.kernel(enable_backward=False, module=mod)
    def reduce_rows(part: wp.array2d(dtype=wp.float64), out: wp.array(dtype=wp.float64), kk: wp.array(dtype=wp.int32),
                    kh: wp.int32, skip: wp.array(dtype=wp.int32), use_skip: wp.int32, M: wp.int32, NP: wp.int32):
        i = wp.tid()
        k = _count(kk, kh, skip, use_skip)
        if i < k or i == M:
            acc = wp.tile_zeros(shape=RT, dtype=wp.float64)
            for s in range(0, NP, RT):
                acc += wp.tile_load(part[i], shape=RT, offset=s)
            wp.tile_store(out, wp.tile_sum(acc), offset=i)

    @wp.kernel(enable_backward=False, module=mod)
    def combine(V: wp.array2d(dtype=Any), h: wp.array(dtype=wp.float64), w: wp.array(dtype=wp.float64),
                kk: wp.array(dtype=wp.int32), kh: wp.int32, skip: wp.array(dtype=wp.int32), use_skip: wp.int32,
                out: wp.array(dtype=wp.float64), npart: wp.array(dtype=wp.float64)):
        p = wp.tid()
        k = _count(kk, kh, skip, use_skip)
        off = p * TILE
        s = wp.tile_zeros(shape=TILE, dtype=wp.float64)
        for i in range(k):
            v = wp.tile_astype(wp.tile_load(V[i], shape=TILE, offset=off), dtype=wp.float64)
            wp.tile_axpy(h[i], v, s)
        y = wp.tile_load(w, shape=TILE, offset=off) - s
        wp.tile_store(out, y, offset=off)
        wp.tile_store(npart, wp.tile_dot(y, y), offset=p)

    @wp.kernel(enable_backward=False, module=mod)
    def reduce_dgks(npart: wp.array(dtype=wp.float64), NP: wp.int32, ss: wp.array(dtype=wp.float64),
                    hw: wp.array(dtype=wp.float64), M: wp.int32, dgks: wp.float64, flag: wp.array(dtype=wp.int32)):
        b, lane = wp.tid()
        acc = wp.tile_zeros(shape=RT, dtype=wp.float64)
        for s in range(0, NP, RT):
            acc += wp.tile_load(npart, shape=RT, offset=s)
        t = wp.tile_sum(acc)
        wp.tile_store(ss, t)
        height = wp.sqrt(wp.tile_extract(t, 0))
        if lane == 0:
            length = wp.sqrt(hw[M])
            f = wp.int32(0)
            if height <= dgks * length:
                f = wp.int32(1)
            flag[0] = f

    @wp.kernel(enable_backward=False, module=mod)
    def reduce_column(npart: wp.array(dtype=wp.float64), NP: wp.int32, ss2: wp.array(dtype=wp.float64),
                      h1: wp.array(dtype=wp.float64), h2: wp.array(dtype=wp.float64), ss1: wp.array(dtype=wp.float64),
                      flag: wp.array(dtype=wp.int32), kk: wp.array(dtype=wp.int32), kh: wp.int32,
                      col: wp.array(dtype=wp.float64), KMAX: wp.int32):
        b, lane = wp.tid()
        acc = wp.tile_zeros(shape=RT, dtype=wp.float64)
        for s in range(0, NP, RT):
            acc += wp.tile_load(npart, shape=RT, offset=s)
        t = wp.tile_sum(acc)
        wp.tile_store(ss2, t)
        sq2 = wp.tile_extract(t, 0)
        k = kh
        if kh < 0:
            k = kk[0]
        f = flag[0]
        for j in range(lane, k, NT):
            hj = h1[j]
            if f != 0:
                hj = hj + h2[j]
            col[j] = hj
        if lane == 0:
            col[KMAX] = wp.sqrt(h1[KMAX])
            hh = wp.sqrt(ss1[0])
            if f != 0:
                hh = wp.sqrt(sq2)
            col[KMAX + 1] = hh
            col[KMAX + 2] = wp.float64(f)

    @wp.kernel(enable_backward=False, module=mod)
    def store(y: wp.array(dtype=wp.float64), dst: wp.array(dtype=Any), col: wp.array(dtype=wp.float64),
              KMAX: wp.int32):
        j = wp.tid()
        dst[j] = dst.dtype(y[j] / col[KMAX + 1])

    @wp.kernel(enable_backward=False, module=mod)
    def lincomb(V: wp.array2d(dtype=Any), c: wp.array(dtype=wp.float64), k: wp.int32,
                out: wp.array(dtype=wp.float64)):
        p = wp.tid()
        off = p * TILE
        s = wp.tile_zeros(shape=TILE, dtype=wp.float64)
        for i in range(k):
            v = wp.tile_astype(wp.tile_load(V[i], shape=TILE, offset=off), dtype=wp.float64)
            wp.tile_axpy(c[i], v, s)
        wp.tile_store(out, s, offset=off)

    @wp.kernel(enable_backward=False, module=mod)
    def gather_row(V: wp.array2d(dtype=Any), st: wp.array(dtype=wp.int32), dst: wp.array(dtype=wp.float64)):
        j = wp.tid()
        dst[j] = wp.float64(V[st[0], j])

    @wp.kernel(enable_backward=False, module=mod)
    def store_row(y: wp.array(dtype=wp.float64), V: wp.array2d(dtype=Any), st: wp.array(dtype=wp.int32),
                  col: wp.array(dtype=wp.float64), KMAX: wp.int32):
        j = wp.tid()
        V[st[0] + 1, j] = V.dtype(y[j] / col[KMAX + 1])

    for vt in (wp.float64, wp.float32):
        for kern in (mdot, combine, lincomb, gather_row, store_row):
            wp.overload(kern, {"V": wp.array2d(dtype=vt)})
        wp.overload(store, {"dst": wp.array(dtype=vt)})
    K = SimpleNamespace(wp=wp, tile=tile, threads=threads, rtile=rtile, module=mod, mdot=mdot,
                        reduce_rows=reduce_rows, combine=combine, reduce_dgks=reduce_dgks, reduce_column=reduce_column,
                        store=store, lincomb=lincomb, gather_row=gather_row, store_row=store_row)
    _WK[key] = K
    return K


class _I32(ctypes.Structure):
    """One int32 kernel argument shared by several recorded launches (set per call, nothing re-packed)."""
    _fields_ = (("value", ctypes.c_int32),)


def _slot(arr):
    """A private copy of a Warp array's kernel descriptor: bound into recorded launches once, re-pointed per call."""
    c = arr.__ctype__()
    return type(c).from_buffer_copy(c)


def _warp_stream(wpm, wdev, handle: int):
    """The Warp stream for a torch stream handle: the device's null stream for torch's legacy default stream (0), else
    the wrapper of that torch stream shared with ``_wrap_stream`` (the device loop's side stream; a graph capture
    records its launches)."""
    if not handle:
        return wdev.null_stream
    ws = _WSTREAMS.get(handle)
    if ws is None:
        ws = _WSTREAMS[handle] = wpm.Stream(wdev, cuda_stream=handle)
    return ws


class _WarpOrth:
    """The Warp implementation's recorded launches of one ``FusedOrth`` workspace (W10b-A)."""

    def __init__(self, fo: "FusedOrth"):
        K = _warp_kernels()
        self.K = K
        w_ = K.wp
        self.device = fo.V.device
        self.wdev = w_.device_from_torch(self.device)
        f = w_.from_torch
        m, n, NP = fo.m, fo.n, fo.np
        self.m, self.n = m, n
        a = self.a = SimpleNamespace(V=f(fo.V), part=f(fo.part.view(m + 1, NP)), npart=f(fo.npart), h1=f(fo.h1),
                                     h2=f(fo.h2), ss1=f(fo.ss1), ss2=f(fo.ss2), flag=f(fo.flag), k=f(fo.k),
                                     col=f(fo.col), w1=f(fo.w1), w2=f(fo.w2))
        # per call: the input vector (s_w), the host column count (s_kh; -1 = read it from s_kk on the device)
        self.s_w, self.s_kk, self.s_kh = _slot(a.w2), _slot(a.k), _I32(0)
        self.kk_own = fo.k.data_ptr()
        rec, bind = self._rec, self._bind
        cmds = [
            rec(K.mdot, NP, [a.V, a.w2, a.k, 0, a.flag, 0, a.part, m], True),
            rec(K.reduce_rows, m + 1, [a.part, a.h1, a.k, 0, a.flag, 0, m, NP], True),
            rec(K.combine, NP, [a.V, a.h1, a.w2, a.k, 0, a.flag, 0, a.w1, a.npart], True),
            rec(K.reduce_dgks, 1, [a.npart, NP, a.ss1, a.h1, m, DGKS, a.flag], True),
            rec(K.mdot, NP, [a.V, a.w1, a.k, 0, a.flag, 1, a.part, m], True),
            rec(K.reduce_rows, m + 1, [a.part, a.h2, a.k, 0, a.flag, 1, m, NP], True),
            rec(K.combine, NP, [a.V, a.h2, a.w1, a.k, 0, a.flag, 1, a.w2, a.npart], True),
            rec(K.reduce_column, 1, [a.npart, NP, a.ss2, a.h1, a.h2, a.ss1, a.flag, a.k, 0, a.col, m], True),
        ]
        for c in cmds:
            bind(c, kk=self.s_kk, kh=self.s_kh)
        for c in (cmds[0], cmds[2]):                    # the first pass reads the caller's vector
            bind(c, w=self.s_w)
        self.cmds = cmds
        self._store = {}
        self._lin = None
        self._h = None
        self._ws = None

    def _rec(self, kernel, dim, inputs, tiled):
        w_ = self.K.wp
        if tiled:
            return w_.launch_tiled(kernel, dim=[int(dim)], inputs=inputs, device=self.wdev, block_dim=self.K.threads,
                                   record_cmd=True)
        return w_.launch(kernel, dim=int(dim), inputs=inputs, device=self.wdev, block_dim=self.K.threads,
                         record_cmd=True)

    @staticmethod
    def _bind(cmd, **slots):
        for name, value in slots.items():
            cmd.set_param_by_name_from_ctype(name, value)

    def stream(self):
        """The Warp stream of the caller's current torch stream (cached for the last handle)."""
        h = torch.cuda.current_stream(self.device).cuda_stream
        if h != self._h or self._ws is None:
            self._ws = _warp_stream(self.K.wp, self.wdev, int(h))
            self._h = h
        return self._ws

    def step(self, w: torch.Tensor, kh: int, k_ptr: torch.Tensor | None):
        self.s_w.data = w.data_ptr()
        self.s_kh.value = kh
        self.s_kk.data = self.kk_own if k_ptr is None else k_ptr.data_ptr()
        ws = self.stream()
        for c in self.cmds:
            c.launch(ws)

    def store(self, dst: torch.Tensor):
        rec = self._store.get(dst.dtype)
        if rec is None:
            wa = self.K.wp.from_torch(dst)
            cmd = self._rec(self.K.store, self.n, [self.a.w2, wa, self.a.col, self.m], False)
            s = _slot(wa)
            self._bind(cmd, dst=s)
            rec = self._store[dst.dtype] = (cmd, s)
        cmd, s = rec
        s.data = dst.data_ptr()
        cmd.launch(self.stream())

    def lincomb(self, c: torch.Tensor, k: int, out: torch.Tensor):
        if self._lin is None:
            f = self.K.wp.from_torch
            wc, wo = f(c), f(out)
            cmd = self._rec(self.K.lincomb, self.a.part.shape[1], [self.a.V, wc, 0, wo], True)
            sc, so, sk = _slot(wc), _slot(wo), _I32(0)
            self._bind(cmd, c=sc, out=so, k=sk)
            self._lin = (cmd, sc, so, sk)
        cmd, sc, so, sk = self._lin
        sc.data, so.data, sk.value = c.data_ptr(), out.data_ptr(), int(k)
        sc.shape[0] = c.numel()
        cmd.launch(self.stream())

    def rows(self, st: torch.Tensor, vin: torch.Tensor):
        """The device loop's (gather, store) launches: ``vin = V[st[0]]`` and ``V[st[0] + 1] = w2 / height``."""
        f = self.K.wp.from_torch
        self._st, self._vin = f(st), f(vin)
        gather = self._rec(self.K.gather_row, self.n, [self.a.V, self._st, self._vin], False)
        put = self._rec(self.K.store_row, self.n, [self.a.w2, self.a.V, self._st, self.a.col, self.m], False)
        return gather, put


def available(device) -> bool:
    """Whether the selected implementation (``BACKEND["impl"]``) runs on ``device`` (the Triton one imports Triton
    here, on its first use)."""
    if torch.device(device).type != "cuda":
        return False
    if BACKEND["impl"] == "warp":
        return HAVE_WARP
    return _load_triton()


class FusedOrth:
    """Workspace and launches of the fused DGKS-CGS2 step for one basis ``V`` (``[m, n]``, row-contiguous).  ``V`` is
    float64, or float32 (``gmres(basis_fp32=True)``: compressed-basis GMRES -- every basis entry is read into fp64,
    every product, sum and norm is fp64, the new vector is rounded to fp32 once when it is stored).  ``impl``
    (``"triton"`` / ``"warp"``) defaults to ``BACKEND["impl"]``."""

    def __init__(self, basis: torch.Tensor, impl: str | None = None):
        if basis.dim() != 2 or basis.stride(1) != 1:
            raise ValueError("FusedOrth needs one [m, n] basis with contiguous rows")
        if basis.dtype not in (torch.float64, torch.float32):
            raise ValueError(f"FusedOrth needs a float64 or float32 basis, not {basis.dtype}")
        self.impl = str(BACKEND["impl"] if impl is None else impl)
        if self.impl not in ("triton", "warp"):
            raise ValueError(f"FusedOrth: impl must be 'triton' or 'warp', not {self.impl!r}")
        if self.impl == "triton" and not _load_triton():
            raise RuntimeError("FusedOrth(impl='triton') needs Triton")
        self.V = basis
        self.m, self.n = int(basis.shape[0]), int(basis.shape[1])
        dev, f64 = basis.device, torch.float64
        if self.impl == "warp":
            self.block = int(WTUNE["tile"])
            self.rows = 1
        else:
            self.block = int(TUNE["block"])
            self.rows = int(TUNE["rows"])
        self.np = (self.n + self.block - 1) // self.block
        self.part = torch.empty((self.m + 1) * self.np, dtype=f64, device=dev)
        self.npart = torch.empty(self.np, dtype=f64, device=dev)
        # reduced rows: h1 / h2 (m each) followed by the squared norm at index k (written by _reduce_rows)
        self.h1 = torch.zeros(self.m + 1, dtype=f64, device=dev)
        self.h2 = torch.zeros(self.m + 1, dtype=f64, device=dev)
        self.ss1 = torch.zeros(1, dtype=f64, device=dev)
        self.ss2 = torch.zeros(1, dtype=f64, device=dev)
        self.flag = torch.zeros(1, dtype=torch.int32, device=dev)
        self.k = torch.zeros(1, dtype=torch.int32, device=dev)
        self.dgks = torch.full((1,), DGKS, dtype=f64, device=dev)
        self.col = torch.zeros(self.m + 3, dtype=f64, device=dev)
        self.host = torch.zeros(self.m + 3, dtype=f64, pin_memory=True)
        self.w1 = torch.empty(self.n, dtype=f64, device=dev)
        self.w2 = torch.empty(self.n, dtype=f64, device=dev)
        self._k_host = None
        self._w = _WarpOrth(self) if self.impl == "warp" else None

    # ------------------------------------------------------------------ launches
    def _mdot(self, w, k_ptr, out, skip):
        _mdot_kernel[(self.np,)](self.V, self.V.stride(0), w, k_ptr, self.part, self.np, self.n, self.m,
                                 skip if skip is not None else self.flag, BLOCK=self.block, ROWS=self.rows,
                                 SKIP=skip is not None, num_warps=int(TUNE["warps"]))
        _reduce_rows[(self.m + 1,)](self.part, out, k_ptr, self.np, self.m, RBLOCK=int(TUNE["rblock"]),
                                    num_warps=4)

    def _combine(self, h, w, out, k_ptr, ss, skip):
        _combine_kernel[(self.np,)](self.V, self.V.stride(0), h, w, out, k_ptr, self.npart, self.n,
                                    skip if skip is not None else self.flag, BLOCK=self.block, ROWS=self.rows,
                                    SKIP=skip is not None, num_warps=int(TUNE["warps"]))
        _reduce_one[(1,)](self.npart, ss, self.np, RBLOCK=int(TUNE["rblock"]), num_warps=4)

    def step(self, w: torch.Tensor, k: int | None = None, k_ptr: torch.Tensor | None = None):
        """Orthogonalise ``w`` against ``V[:k]`` (DGKS-CGS2) on the device; ``k`` from the host, or ``k_ptr`` (an
        int32 device scalar: the device loop).  After it ``self.col[:k]`` is the Hessenberg column, ``col[m]`` the
        length, ``col[m + 1]`` the height, ``col[m + 2]`` the flag, and ``self.w2`` the orthogonalised
        (unnormalised) vector.  No host read-back, no sync."""
        if self._w is not None:
            if w.dtype != torch.float64 or not w.is_contiguous():
                w = w.to(torch.float64).contiguous()
            self._w.step(w, -1 if k_ptr is not None else int(k), k_ptr)
            return
        if k_ptr is None:
            if self._k_host != k:
                self.k.fill_(int(k))
                self._k_host = k
            k_ptr = self.k
        if not w.is_contiguous():
            w = w.contiguous()
        ss_w = self.h1[self.m:]                       # |w|^2 lands in row m of the reduction
        self._mdot(w, k_ptr, self.h1, None)
        self._combine(self.h1, w, self.w1, k_ptr, self.ss1, None)
        _dgks_kernel[(1,)](self.h1, ss_w, self.ss1, self.flag, k_ptr, self.dgks, num_warps=1)
        self._mdot(self.w1, k_ptr, self.h2, self.flag)
        self._combine(self.h2, self.w1, self.w2, k_ptr, self.ss2, self.flag)
        _column_kernel[(1,)](self.h1, self.h2, ss_w, self.ss1, self.ss2, self.flag, k_ptr, self.col, self.m,
                             RB=256, num_warps=4)

    def store(self, dst: torch.Tensor):
        """``dst = w2 / height`` (the next basis vector)."""
        if self._w is not None:
            if not dst.is_contiguous() or dst.numel() != self.n:
                raise ValueError("FusedOrth.store needs one contiguous vector of the basis' length")
            self._w.store(dst)
            return
        _store_kernel[(self.np,)](self.w2, dst, self.col, self.m, self.n, BLOCK=self.block, num_warps=4)

    def lincomb(self, c: torch.Tensor, k: int) -> torch.Tensor:
        """``c[:k] @ V[:k]`` as a float64 vector (``c``: float64 on the device), summed in fp64 whatever ``V`` holds."""
        out = torch.empty(self.n, dtype=torch.float64, device=self.V.device)
        if self._w is not None:
            self._w.lincomb(c.to(torch.float64).contiguous(), int(k), out)
            return out
        _lincomb_kernel[(self.np,)](self.V, self.V.stride(0), c.contiguous(), out, int(k), self.n, BLOCK=self.block,
                                    ROWS=self.rows, num_warps=int(TUNE["warps"]))
        return out

    def read(self, k: int):
        """One read-back (one blocking copy of the column buffer): ``(coefficients, length, height, reorthogonalised)``."""
        host = self.col.cpu()
        entries = host[: k].tolist()
        length, height, flag = host[self.m: self.m + 3].tolist()
        return entries, length, height, bool(flag)


# =================================================================================================== device loop
_TAIL = {}
LAST: dict = {}
# process-wide counters of the device loop (the entry logs them): solves, captures, graph launches, eager steps, the
# reason of the last capture failure
STATS = {"solves": 0, "captures": 0, "launches": 0, "eager_steps": 0, "fallbacks": 0, "last_error": None,
         "disabled": False}


def _tail_kernel():
    """The Hessenberg column, its Givens rotations and the convergence test of one Arnoldi step, on one device thread
    (the host loop of ``gmres`` statement for statement; Warp)."""
    if "k" in _TAIL:
        return _TAIL["k"]
    import warp as wp
    wp.config.quiet = True
    wp.init()

    @wp.func
    def _hypot(a: wp.float64, b: wp.float64):
        x = wp.abs(a)
        y = wp.abs(b)
        hi = wp.max(x, y)
        lo = wp.min(x, y)
        if hi == wp.float64(0.0):
            return wp.float64(0.0)
        q = lo / hi
        return hi * wp.sqrt(wp.float64(1.0) + q * q)

    @wp.kernel(enable_backward=False, module="unique")
    def arnoldi_tail(col: wp.array(dtype=wp.float64), KMAX: wp.int32, R: wp.array2d(dtype=wp.float64),
                     H: wp.array2d(dtype=wp.float64), Q: wp.array2d(dtype=wp.float64), g: wp.array(dtype=wp.float64),
                     cs: wp.array(dtype=wp.float64), sn: wp.array(dtype=wp.float64),
                     hist: wp.array(dtype=wp.float64), st: wp.array(dtype=wp.int32), sc: wp.array(dtype=wp.float64),
                     cond: wp.array(dtype=wp.int32), wanted: wp.int32):
        # st: 0 column, 1 carried, 2 last column + 1, 3 done, 4 breakdown, 5 re-orthogonalisations, 6 k, 7 steps
        # sc: 0 threshold, 1 beta, 2 outside, 3 eps
        if st[3] != 0:
            cond[0] = 0
            return
        j = st[0]
        carried = st[1]
        k = j + 1
        length = col[KMAX]
        height = col[KMAX + 1]
        hk = height
        brk = 0
        if height <= sc[3] * length:
            hk = wp.float64(0.0)
            brk = 1
        if wanted != 0:
            for i in range(k):
                H[i, j] = col[i]
            H[k, j] = hk
        if carried > 0:
            for i in range(carried + 1):
                s = wp.float64(0.0)
                for l in range(carried + 1):
                    s = s + Q[i, l] * col[l]
                R[i, j] = s
            for i in range(carried + 1, k):
                R[i, j] = col[i]
        else:
            for i in range(k):
                R[i, j] = col[i]
        R[k, j] = hk
        carry = R[carried, j]
        for i in range(carried, j):
            c = cs[i]
            s = sn[i]
            lo = R[i + 1, j]
            R[i, j] = c * carry + s * lo
            carry = c * lo - s * carry
        f = carry
        gg = R[k, j]
        c = wp.float64(1.0)
        s = wp.float64(0.0)
        r = f
        if gg != wp.float64(0.0):
            if f == wp.float64(0.0):
                c = wp.float64(0.0)
                s = wp.float64(1.0)
                if gg < wp.float64(0.0):
                    s = wp.float64(-1.0)
                r = wp.abs(gg)
            else:
                ln = _hypot(f, gg)
                r = ln
                if f < wp.float64(0.0):
                    r = -ln
                c = wp.abs(f) / ln
                s = gg / r
        cs[j] = c
        sn[j] = s
        R[j, j] = r
        R[k, j] = wp.float64(0.0)
        gj = g[j]
        g[k] = -s * gj
        g[j] = c * gj
        est = wp.abs(g[k])
        if sc[2] != wp.float64(0.0):
            est = _hypot(est, sc[2])
        hist[j] = est / sc[1]
        st[5] = st[5] + wp.int32(col[KMAX + 2])
        st[7] = st[7] + 1
        if est <= sc[0] or brk != 0 or k >= st[2]:
            st[3] = 1
            st[4] = brk
            cond[0] = 0
        else:
            st[0] = k
            st[6] = k + 1
            cond[0] = 1
    _TAIL["k"] = arnoldi_tail
    return arnoldi_tail


def _block_amg(impl: str):
    """The coupled-AMG module whose V-cycle graph switch the device loop turns off while it captures.  The Triton
    implementation imports it; the Warp one only uses it when it is already loaded (a coupled-AMG
    preconditioner imported it; without one there is no V-cycle graph to turn off) -- importing it would import
    Triton through its kernels."""
    if impl == "warp":
        return sys.modules.get("Pipeline.CoupledNewton.block_amg")
    from Pipeline.CoupledNewton import block_amg as _BA
    return _BA


class DeviceCycle:
    """One GMRES cycle's Arnoldi loop entirely on the device (``gmres(device_loop=True)``).

    The body of one Arnoldi step -- the current basis row gathered, the preconditioner and the operator applied, the
    fused DGKS-CGS2 step (``FusedOrth``), the next basis row stored, and the Hessenberg column with its Givens rotations
    and the convergence test (``_tail_kernel``, the host loop's statements on one device thread) -- is captured once
    per (preconditioner, operator, basis) as a torch CUDA graph and run by a CUDA conditional WHILE node (Warp
    ``capture_while``) until the test or the cycle's last column ends it: no host involvement between iterations.  The
    host reads the cycle's triangle, rotated right-hand side, history and counters once at the end.  The first step of
    a new body runs eagerly (it loads every kernel; capture follows).  If a capture fails, the body is replayed
    eagerly step by step with one read-back per step (``fallback``; same results).  The row gather / store and the
    fused step use the implementation ``FusedOrth`` was built with (``BACKEND["impl"]``)."""

    def __init__(self, basis: torch.Tensor, wanted: int, eps: float):
        import warp as wp
        # the cycle's buffers are Warp views of torch tensors: the Warp runtime must be up even when the fused step
        # uses the Triton kernels and nothing else in the process has started Warp yet
        wp.config.quiet = True
        wp.init()
        self.wp = wp
        self.V = basis
        self.m, self.n = int(basis.shape[0]), int(basis.shape[1])
        dev, f64 = basis.device, torch.float64
        self.device = dev
        self.orth = FusedOrth(basis)
        m = self.m
        self.R = torch.zeros(m, max(m - 1, 1), dtype=f64, device=dev)
        self.H = torch.zeros(m, max(m - 1, 1), dtype=f64, device=dev) if wanted else torch.zeros(1, 1, dtype=f64,
                                                                                                 device=dev)
        self.Q = torch.zeros(m, m, dtype=f64, device=dev)
        self.g = torch.zeros(m, dtype=f64, device=dev)
        self.cs = torch.zeros(m, dtype=f64, device=dev)
        self.sn = torch.zeros(m, dtype=f64, device=dev)
        self.hist = torch.zeros(m, dtype=f64, device=dev)
        self.st = torch.zeros(8, dtype=torch.int32, device=dev)
        self.sc = torch.zeros(4, dtype=f64, device=dev)
        self.sc_host = torch.zeros(4, dtype=f64, pin_memory=True)
        self.st_host = torch.zeros(8, dtype=torch.int32, pin_memory=True)
        self.cond = torch.zeros(1, dtype=torch.int32, device=dev)
        self.vin = torch.zeros(self.n, dtype=f64, device=dev)
        self.wanted = int(bool(wanted))
        self.eps = float(eps)
        self.key = None
        self.graph = None
        self.torch_graph = None
        self.fallback = None
        self.captures = 0
        self.launches = 0
        self._ws = None
        # W10b-A: the Warp implementation's row gather / store, recorded once for this cycle's buffers
        self._rows = self.orth._w.rows(self.st, self.vin) if self.orth._w is not None else None
        wdev = wp.device_from_torch(dev)
        self._w = dict(col=wp.from_torch(self.orth.col), R=wp.from_torch(self.R), H=wp.from_torch(self.H),
                       Q=wp.from_torch(self.Q), g=wp.from_torch(self.g), cs=wp.from_torch(self.cs),
                       sn=wp.from_torch(self.sn), hist=wp.from_torch(self.hist), st=wp.from_torch(self.st),
                       sc=wp.from_torch(self.sc), cond=wp.from_torch(self.cond))
        self._wdev = wdev
        LAST["cycle"] = self                         # the latest device loop (tests, reports)
        STATS["solves"] += 1

    # ------------------------------------------------------------------ one step (eager, or recorded by a capture)
    def _step(self, precondition, operator):
        wp = self.wp
        if self._rows is not None:
            # W10b-A: the Warp row gather / store around the Warp fused step (the caller's stream: the side stream)
            gather, put = self._rows
            gather.launch(self.orth._w.stream())
            w = operator(precondition(self.vin))
            self.orth.step(w, k_ptr=self.st[6:7])
            put.launch(self.orth._w.stream())
        else:
            blk = self.orth.block
            grid = (self.orth.np,)
            _gather_row_kernel[grid](self.V, self.V.stride(0), self.st, self.vin, self.n, BLOCK=blk, num_warps=4)
            w = operator(precondition(self.vin))
            self.orth.step(w, k_ptr=self.st[6:7])
            _store_row_kernel[grid](self.orth.w2, self.V, self.V.stride(0), self.st, self.orth.col, self.orth.m,
                                    self.n, BLOCK=blk, num_warps=4)
        ts = torch.cuda.current_stream(self.device)
        ws = _wrap_stream(wp, self._wdev, ts)
        W = self._w
        wp.launch(_tail_kernel(), dim=1, inputs=[W["col"], self.orth.m, W["R"], W["H"], W["Q"], W["g"], W["cs"],
                                                W["sn"], W["hist"], W["st"], W["sc"], W["cond"], self.wanted],
                  stream=ws, device=self._wdev)

    # ------------------------------------------------------------------ capture
    def _capture(self, precondition, operator):
        """Capture the step as a torch graph (keep_graph) and wrap it in a Warp graph with a WHILE node on ``cond``."""
        import os
        wp = self.wp
        _BA = _block_amg(self.orth.impl)
        side = _side_stream(self.device)
        saved_env = os.environ.get("LPCNS_WARP_GRAPH")
        saved_graph = _BA.GRAPH_STATS["enabled"] if _BA is not None else None
        os.environ["LPCNS_WARP_GRAPH"] = "0"         # the Warp residual launches eagerly (capturable) inside
        if _BA is not None:
            _BA.GRAPH_STATS["enabled"] = False       # the V-cycle runs its eager kernels (capturable) inside
        try:
            tg = torch.cuda.CUDAGraph(keep_graph=True)
            side.wait_stream(torch.cuda.current_stream(self.device))
            # capture_begin / capture_end directly (as block_amg's V-cycle capture): ``torch.cuda.graph`` would empty
            # the allocator's cache at every capture
            with torch.cuda.stream(side):
                tg.capture_begin(capture_error_mode="thread_local")
                try:
                    self._step(precondition, operator)
                finally:
                    tg.capture_end()
            torch.cuda.current_stream(self.device).wait_stream(side)
            body = wp.Graph(self._wdev)
            body.graph = __import__("ctypes").c_void_p(tg.raw_cuda_graph())
            ws = self._ws or wp.Stream(self._wdev)
            cond = self._w["cond"]
            try:
                wp.capture_begin(stream=ws, force_module_load=False)
                try:
                    wp.capture_while(cond, body, stream=ws)
                finally:
                    outer = wp.capture_end(stream=ws)
            finally:
                body.graph = None                    # the torch graph stays owned by torch (tg), never by Warp
            self.graph, self.torch_graph, self._ws = outer, tg, ws
            self._wts = torch.cuda.ExternalStream(ws.cuda_stream, device=self.device)
            self.captures += 1
            STATS["captures"] += 1
        finally:
            if saved_env is None:
                os.environ.pop("LPCNS_WARP_GRAPH", None)
            else:
                os.environ["LPCNS_WARP_GRAPH"] = saved_env
            if _BA is not None:
                _BA.GRAPH_STATS["enabled"] = saved_graph

    def _eager(self, precondition, operator):
        import os
        _BA = _block_amg(self.orth.impl)
        side = _side_stream(self.device)
        saved_env = os.environ.get("LPCNS_WARP_GRAPH")
        saved_graph = _BA.GRAPH_STATS["enabled"] if _BA is not None else None
        os.environ["LPCNS_WARP_GRAPH"] = "0"
        if _BA is not None:
            _BA.GRAPH_STATS["enabled"] = False
        try:
            side.wait_stream(torch.cuda.current_stream(self.device))
            with torch.cuda.stream(side):
                self._step(precondition, operator)
            torch.cuda.current_stream(self.device).wait_stream(side)
            STATS["eager_steps"] += 1
        finally:
            if saved_env is None:
                os.environ.pop("LPCNS_WARP_GRAPH", None)
            else:
                os.environ["LPCNS_WARP_GRAPH"] = saved_env
            if _BA is not None:
                _BA.GRAPH_STATS["enabled"] = saved_graph

    # ------------------------------------------------------------------ one cycle
    def run(self, key, precondition, operator, *, carried, cap, rotated, triangle, leading, outside, threshold,
            beta):
        """Run columns ``carried .. cap - 1`` (until convergence / breakdown).  Returns ``(column, breakdown,
        history, reorthogonalisations, rotated)`` and fills ``triangle`` (host) like the host loop."""
        dev = self.device
        last = max(int(cap), int(carried) + 1)
        self.st_host.copy_(torch.tensor([carried, carried, last, 0, 0, 0, carried + 1, 0], dtype=torch.int32))
        self.st.copy_(self.st_host, non_blocking=True)
        self.sc_host.copy_(torch.tensor([threshold, beta, outside, self.eps], dtype=torch.float64))
        self.sc.copy_(self.sc_host, non_blocking=True)
        self.g.zero_()
        self.g[: carried + 1].copy_(torch.tensor(rotated[: carried + 1], dtype=torch.float64))
        if self.wanted:
            self.H[:, carried:].zero_()              # the host loop's columns start from a zeroed hessenberg
        if carried:
            self.R[: carried + 1, :carried].copy_(triangle[: carried + 1, :carried])
            self.Q[: carried + 1, : carried + 1].copy_(leading)
        self.cond.fill_(1)
        if self.fallback is None and (self.graph is None or self.key != key):
            self.graph = self.torch_graph = None
            self._eager(precondition, operator)          # the first step (loads every kernel), then the capture
            try:
                try:
                    self._capture(precondition, operator)
                except Exception as exc:                 # noqa: BLE001 -- retried once below, else eager
                    # a workspace sized lazily on first use can be allocated inside the capture, which a child graph
                    # of the WHILE node cannot hold ("unsupported operation (memory allocation)"): one more eager
                    # step (a step of this cycle, as the eager path takes it) allocates it, then the capture again
                    if "memory allocation" not in str(exc):
                        raise
                    self.st_host.copy_(self.st)
                    if int(self.st_host[3]):
                        raise                             # the cycle already ended: nothing left to capture
                    self._eager(precondition, operator)
                    STATS["capture_retries"] = STATS.get("capture_retries", 0) + 1
                    self._capture(precondition, operator)
                self.key = key
            except Exception as exc:                     # noqa: BLE001 -- the step stays eager
                # this cycle finishes step by step; later solves take the host loop (``STATS["disabled"]``)
                self.fallback = STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:400]
                STATS["fallbacks"] += 1
                STATS["disabled"] = True
                self.graph = self.torch_graph = None
        if self.graph is not None:
            ts = torch.cuda.current_stream(dev)
            self._wts.wait_stream(ts)
            self.wp.capture_launch(self.graph, stream=self._ws)
            ts.wait_stream(self._wts)
            self.launches += 1
            STATS["launches"] += 1
        else:
            while True:
                self.st_host.copy_(self.st)
                if int(self.st_host[3]):
                    break
                self._eager(precondition, operator)
        # the cycle's only read-back: the counters (8 ints) and the residual history (one float per column), two
        # asynchronous copies into pinned memory and ONE synchronisation
        hist_host = getattr(self, "_hist_host", None)
        if hist_host is None:
            hist_host = self._hist_host = torch.zeros(self.m, dtype=torch.float64, pin_memory=True)
        self.st_host.copy_(self.st, non_blocking=True)
        hist_host.copy_(self.hist, non_blocking=True)
        torch.cuda.current_stream(dev).synchronize()
        STATS["syncs"] = STATS.get("syncs", 0) + 1
        st = self.st_host.tolist()
        column = st[0]
        size = column + 1
        history = hist_host[carried:size].tolist()
        return column, bool(st[4]), history, int(st[5])

    def solve(self, size: int) -> torch.Tensor:
        """The cycle's least-squares coordinates on the device: ``R[:size, :size] y = g[:size]`` (the host path's
        pseudo-solve when the last pivot is an exact zero: that component dropped)."""
        R = self.R[:size, :size].clone()
        t = self.g[:size].clone()
        piv = R[size - 1, size - 1]
        zero = piv == 0.0
        t[size - 1] = torch.where(zero, torch.zeros_like(piv), t[size - 1])
        R[size - 1, size - 1] = torch.where(zero, torch.ones_like(piv), piv)
        return torch.linalg.solve_triangular(R, t.reshape(size, 1), upper=True).reshape(size)


_SIDE: dict = {}


def _side_stream(device):
    key = torch.device(device)
    if key not in _SIDE:
        _SIDE[key] = torch.cuda.Stream(device=key)
    return _SIDE[key]


_WSTREAMS: dict = {}


def _wrap_stream(wp, wdev, ts):
    """A Warp stream object for torch stream ``ts`` (a real stream: the side stream of the device loop)."""
    handle = int(ts.cuda_stream)
    ws = _WSTREAMS.get(handle)
    if ws is None:
        ws = _WSTREAMS[handle] = wp.Stream(wdev, cuda_stream=handle)
    return ws


def reference_step(V: torch.Tensor, w: torch.Tensor, k: int):
    """The default path's arithmetic (``gmres``), for tests: ``(coefficients, length, height, reorth, w_final)``."""
    length = float(torch.linalg.vector_norm(w))
    c = V[:k] @ w
    w = w - c @ V[:k]
    height = float(torch.linalg.vector_norm(w))
    reorth = False
    if height <= DGKS * length:
        c2 = V[:k] @ w
        w = w - c2 @ V[:k]
        c = c + c2
        height = float(torch.linalg.vector_norm(w))
        reorth = True
    return c, length, height, reorth, w


def _check_math():           # pragma: no cover
    assert abs(DGKS - 1.0 / math.sqrt(2.0)) < 1e-16
