"""Warp kernels for the coupled AMG's numeric build.

See ``docs/research/2026-10-14-warp-backend.md`` section 5.  The
numeric pass of a structural plan (``block_amg.CoupledAMGPlan.numeric``) spends most of its time in two block
products per level, ``A P`` and ``R (A P)``, evaluated by ``block_amg.SpGEMM.__call__`` as: per chunk of 4M index
triples ``(e_A, e_B, out)``, two gathered copies of the operand blocks, one batched GEMM and an ``index_add_`` into the
output (deterministic, i.e. sort-based, while the entry runs with torch's deterministic algorithms).  That is ~1.6 KB
of memory traffic per triple and a sort per chunk for 125 flops of work.

Here a product is one kernel over its OUTPUT blocks: the triples are sorted once by output (stable, so each output
keeps the order in which the torch path accumulates it) into int32 operand indices and int32 segment offsets
(``SegmentedTriples``, built once per SpGEMM plan and kept on it), and thread ``o`` sums ``A[ia[t]] @ B[ib[t]]`` over its
segment in registers and writes block ``o`` once.  No atomics, no temporaries: deterministic, and the result equals
the torch path's to rounding (the 5 x 5 products may contract differently: FMA).  ``block_sums`` is the same
segmented reduction without the product (the tentative-prolongation products of ``fused_numeric``).

Opt-in: ``block_amg.WARP_NUMERIC["on"]`` (``solve_with_coupled_newton --camg-warp-numeric``); off, nothing here is
imported.  Autograd (training builds) keeps the torch path.  Launches run on a dedicated Warp stream ordered against
the caller's torch stream both ways (torch's default stream is the legacy NULL stream).
"""
import weakref

import torch
import warp as wp

wp.config.quiet = True
wp.init()

_INT32_MAX = 2 ** 31 - 1
_KERNELS: dict = {}
_STREAMS: dict = {}


def _mat(F: int, G: int):
    return wp.types.matrix(shape=(int(F), int(G)), dtype=wp.float64)


def _spgemm_kernel(F: int, G: int, H: int):
    key = ("spgemm", F, G, H)
    if key not in _KERNELS:
        TA, TB, TC = _mat(F, G), _mat(G, H), _mat(F, H)

        @wp.kernel(enable_backward=False, module="unique")
        def segmented_product(A: wp.array(dtype=TA), B: wp.array(dtype=TB), ia: wp.array(dtype=wp.int32),
                              ib: wp.array(dtype=wp.int32), seg: wp.array(dtype=wp.int32), C: wp.array(dtype=TC)):
            o = wp.tid()
            acc = TC()
            for t in range(seg[o], seg[o + 1]):
                acc += A[ia[t]] @ B[ib[t]]
            C[o] = acc
        _KERNELS[key] = segmented_product
    return _KERNELS[key]


def _blocksum_kernel(F: int, G: int):
    key = ("blocksum", F, G)
    if key not in _KERNELS:
        TA = _mat(F, G)

        @wp.kernel(enable_backward=False, module="unique")
        def segmented_sum(A: wp.array(dtype=TA), ia: wp.array(dtype=wp.int32), seg: wp.array(dtype=wp.int32),
                          C: wp.array(dtype=TA)):
            o = wp.tid()
            acc = TA()
            for t in range(seg[o], seg[o + 1]):
                acc += A[ia[t]]
            C[o] = acc
        _KERNELS[key] = segmented_sum
    return _KERNELS[key]


def _dinva_kernel(F: int):
    key = ("dinva", F)
    if key not in _KERNELS:
        TA, TV = _mat(F, F), wp.types.vector(length=int(F), dtype=wp.float64)

        @wp.kernel(enable_backward=False, module="unique")
        def dinv_a(A: wp.array(dtype=TA), col: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int32),
                   Dinv: wp.array(dtype=TA), v: wp.array(dtype=TV), y: wp.array(dtype=TV)):
            i = wp.tid()
            acc = TV()
            for b in range(ptr[i], ptr[i + 1]):
                acc += A[b] @ v[col[b]]
            y[i] = Dinv[i] @ acc
        _KERNELS[key] = dinv_a
    return _KERNELS[key]


_LANES = 32
_TILED = {"on": True}              # the omega product: a 32-thread block per row (False: a thread per row)


def _dinva_tiled_kernel(F: int):
    """``y_i = Dinv_i sum_j A_ij v_j`` with one 32-thread block per row: lane l takes the row's blocks l, l + 32, ...
    (32 consecutive blocks per pass: coalesced 200-byte reads), then a block reduction (fixed order)."""
    key = ("dinva_tiled", F)
    if key not in _KERNELS:
        TA, TV = _mat(F, F), wp.types.vector(length=int(F), dtype=wp.float64)

        @wp.kernel(enable_backward=False, module="unique")
        def dinv_a_tiled(A: wp.array(dtype=TA), col: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int32),
                         Dinv: wp.array(dtype=TA), v: wp.array(dtype=TV), y: wp.array(dtype=TV)):
            i, lane = wp.tid()
            acc = TV()
            for b in range(ptr[i] + lane, ptr[i + 1], 32):
                acc += A[b] @ v[col[b]]
            s = wp.tile_sum(wp.tile(acc, preserve_type=True))
            tot = wp.tile_extract(s, 0)
            if lane == 0:
                y[i] = Dinv[i] @ tot
        _KERNELS[key] = dinv_a_tiled
    return _KERNELS[key]


def dinv_a(L, val: torch.Tensor, Dinv: torch.Tensor):
    """``v -> D^-1 A v`` on ``[n, F]`` cell vectors of plan level ``L`` (values ``val``, block-row sorted, and the
    smoother-diagonal inverses ``Dinv``): one Warp kernel per product, a thread per block row -- the operator of the
    prolongation weight's power iteration (``CoupledAMGPlan._prolongation_omega``), in place of cuSPARSE's BSR
    product plus an einsum.  None when the level's rows are not sorted (the caller keeps its product)."""
    cache = getattr(L, "fast", None)
    key = "_warp_dinva_index"
    idx = cache.get(key) if isinstance(cache, dict) else None
    n, F = int(L.n), int(val.shape[1])
    if idx is None:
        row = L.row
        if row.numel() and bool((row[1:] < row[:-1]).any()):
            return None
        ptr = torch.zeros(n + 1, dtype=torch.int64, device=row.device)
        ptr[1:] = torch.cumsum(torch.bincount(row, minlength=n), 0)
        if int(ptr[-1]) >= _INT32_MAX:
            return None
        idx = (L.col.to(torch.int32), ptr.to(torch.int32))
        if isinstance(cache, dict):
            cache[key] = idx
    col, ptr = idx
    Ac, Dc = val.detach().contiguous(), Dinv.detach().contiguous()
    A = wp.from_torch(Ac, dtype=_mat(F, F))
    D = wp.from_torch(Dc, dtype=_mat(F, F))
    device = val.device
    TV = wp.types.vector(length=F, dtype=wp.float64)

    def apply(v: torch.Tensor) -> torch.Tensor:
        vv = v.detach().reshape(n, F).contiguous()
        y = torch.empty(n, F, dtype=torch.float64, device=device)
        args = [A, wp.from_torch(col), wp.from_torch(ptr), D, wp.from_torch(vv, dtype=TV), wp.from_torch(y, dtype=TV)]
        if _TILED["on"]:
            try:
                _launch(device, _dinva_tiled_kernel(F), n, args, block_dim=_LANES)
                return y
            except Exception as exc:              # noqa: BLE001 -- the tiled variant did not build: thread per row
                _TILED.update(on=False, error=f"{type(exc).__name__}: {exc}"[:400])
        _launch(device, _dinva_kernel(F), n, args)
        return y
    apply._keep = (Ac, Dc)                    # the Warp views above alias these tensors
    return apply


_EXT_STREAMS: dict = {}


def _streams(device: torch.device):
    key = torch.device(device)
    if key not in _STREAMS:
        ws = wp.Stream(wp.device_from_torch(key))
        _STREAMS[key] = (ws, torch.cuda.ExternalStream(ws.cuda_stream, device=key))
    return _STREAMS[key]


def _launch(device, kernel, dim, inputs, block_dim=None):
    """``wp.launch`` (``wp.launch_tiled`` with ``block_dim``) on the caller's stream when non-default (CUDA graph
    capture safe), else on the dedicated stream ordered both ways."""
    ts = torch.cuda.current_stream(device)
    handle = int(ts.cuda_stream)
    if handle != 0:
        ws = _EXT_STREAMS.get(handle)
        if ws is None:
            ws = _EXT_STREAMS[handle] = wp.Stream(wp.device_from_torch(device), cuda_stream=handle)
        wts = None
    else:
        ws, wts = _streams(device)
        wts.wait_stream(ts)
    if dim > 0:
        if block_dim is None:
            wp.launch(kernel, dim=dim, inputs=inputs, stream=ws, device=wp.device_from_torch(device))
        else:
            wp.launch_tiled(kernel, dim=[dim], inputs=inputs, block_dim=int(block_dim), stream=ws,
                            device=wp.device_from_torch(device))
    if wts is not None:
        ts.wait_stream(wts)


def _host_or_device(t, device, a=None, b=None):
    """``t[a:b]`` on ``device`` (copied from pinned host memory in chunks by the caller)."""
    s = t if a is None else t[a:b]
    return s if s.device == torch.device(device) else s.to(device, non_blocking=True)


class SegmentedTriples:
    """The triples of one ``block_amg.SpGEMM`` sorted by output block (stable): ``ia`` / ``ib`` the operand block
    indices (int32), ``seg`` the ``n_out + 1`` segment offsets (int32).  Built from the plan's own (possibly lean,
    pinned-host) arrays once; ``plan._warp_triples`` keeps it.  ``order`` maps a sorted position to the triple."""

    def __init__(self, plan, device):
        device = torch.device(device)
        n_t, n_out = int(plan.triples), int(plan.row.numel())
        if max(n_t, n_out) >= _INT32_MAX:
            raise ValueError(f"{n_t} triples / {n_out} outputs exceed int32 indexing")
        out = _host_or_device(plan.out, device)
        order = torch.argsort(out, stable=True)
        counts = torch.bincount(out, minlength=n_out)
        del out
        seg = torch.zeros(n_out + 1, dtype=torch.int64, device=device)
        seg[1:] = torch.cumsum(counts, 0)
        del counts
        self.seg = seg.to(torch.int32)
        del seg
        if getattr(plan, "identity_a", False) or plan.eA is None:
            self.ia = order.to(torch.int32)
        else:
            self.ia = _host_or_device(plan.eA, device)[order].to(torch.int32)
        self.ib = _host_or_device(plan.eB, device)[order].to(torch.int32) if plan.eB is not None else None
        del order
        self.n_out, self.n_t = n_out, n_t
        self.spilled, self._host, self._device = False, {}, device
        _LIVE.add(self)

    _FIELDS = ("ia", "ib", "seg", "_warp_sum_index")

    @property
    def nbytes(self) -> int:
        return sum(t.numel() * t.element_size() for t in (self.ia, self.ib, self.seg) if t is not None)

    def spill(self) -> int:
        """Move the device tables to pinned host buffers (allocated once, reused) and
        free them on the device; ``of`` brings them back before the next product.  Returns the bytes moved."""
        if self.spilled:
            return 0
        moved = 0
        for k in self._FIELDS:
            t = getattr(self, k, None)
            if t is None:
                continue
            h = self._host.get(k)
            if h is None or h.shape != t.shape or h.dtype != t.dtype:
                h = self._host[k] = torch.empty(t.shape, dtype=t.dtype, pin_memory=True)
            h.copy_(t, non_blocking=True)
            moved += t.numel() * t.element_size()
        torch.cuda.current_stream(self._device).synchronize()      # the copies are done before the blocks are reused
        for k in self._FIELDS:
            if k in self._host and getattr(self, k, None) is not None:
                setattr(self, k, None)
        self.spilled = True
        return moved

    def restore(self) -> None:
        if not self.spilled:
            return
        for k, h in self._host.items():
            setattr(self, k, h.to(self._device, non_blocking=True))
        self.spilled = False

    @classmethod
    def of(cls, plan, device) -> "SegmentedTriples":
        cached = getattr(plan, "_warp_triples", None)
        if cached is not None and cached.spilled and cached._device == torch.device(device):
            cached.restore()
        if cached is None or cached.ia.device != torch.device(device):
            cached = plan._warp_triples = cls(plan, device)
        return cached


_LIVE = weakref.WeakSet()       # every SegmentedTriples alive (spill_all)


def spill_all() -> int:
    """W12g: ``spill`` every live segment table (the caller's memory policy: large meshes, after each hierarchy
    build -- the tables are read only by the next build's products).  Returns the bytes moved to the host."""
    return sum(t.spill() for t in list(_LIVE))


def spgemm(plan, A_val: torch.Tensor, B_val: torch.Tensor) -> torch.Tensor:
    """The output block values of ``plan(A_val, B_val)`` (``[n_out, F, H]``), one Warp kernel over the outputs."""
    device = A_val.device
    seg = SegmentedTriples.of(plan, device)
    F, G, H = int(A_val.shape[1]), int(A_val.shape[2]), int(B_val.shape[2])
    A = A_val.detach().contiguous()
    B = B_val.detach().contiguous()
    C = torch.empty(seg.n_out, F, H, dtype=torch.float64, device=device)
    if A.dtype != torch.float64 or B.dtype != torch.float64:
        raise TypeError("block_amg_warp.spgemm works in float64")
    _launch(device, _spgemm_kernel(F, G, H), seg.n_out,
            [wp.from_torch(A, dtype=_mat(F, G)), wp.from_torch(B, dtype=_mat(G, H)), wp.from_torch(seg.ia),
             wp.from_torch(seg.ib), wp.from_torch(seg.seg), wp.from_torch(C, dtype=_mat(F, H))])
    return C


def block_sums(plan, val: torch.Tensor, order=None) -> torch.Tensor:
    """``block_amg._block_sums``: ``C[out[e]] += val[e]`` (``val[order[e]]``) over a P0 product's triples."""
    device = val.device
    seg = SegmentedTriples.of(plan, device)
    F, G = int(val.shape[1]), int(val.shape[2])
    V = val.detach().contiguous()
    if order is not None:
        # triple e reads block order[e]: compose once with the sorted triple index (cached beside the triples)
        key = "_warp_sum_index"
        idx = getattr(seg, key, None)
        if idx is None:
            ordd = _host_or_device(order, device)
            idx = ordd[seg.ia.long()].to(torch.int32)
            setattr(seg, key, idx)
    else:
        idx = seg.ia
    C = torch.empty(seg.n_out, F, G, dtype=torch.float64, device=device)
    _launch(device, _blocksum_kernel(F, G), seg.n_out,
            [wp.from_torch(V, dtype=_mat(F, G)), wp.from_torch(idx), wp.from_torch(seg.seg),
             wp.from_torch(C, dtype=_mat(F, G))])
    return C


def _onehop_kernel(F: int):
    """The learned one-hop smoother's blocks from the network's edge output Theta and the current matrix, one thread per
    block: ``W_ii = D_i^-1 (omega I + Th_ii)``, ``W_ij = D_i^-1 (Th_ij (A_ij D_j^-1))`` (``onehop_from_theta``, form v1)."""
    key = ("onehop", F)
    if key not in _KERNELS:
        TA = _mat(F, F)

        @wp.kernel(enable_backward=False, module="unique")
        def onehop(Th: wp.array(dtype=TA), A: wp.array(dtype=TA), Dinv: wp.array(dtype=TA),
                   row: wp.array(dtype=wp.int64), col: wp.array(dtype=wp.int64), omega: wp.float64,
                   W: wp.array(dtype=TA)):
            e = wp.tid()
            i = row[e]
            j = col[e]
            if i == j:
                W[e] = omega * Dinv[i] + Dinv[i] @ Th[e]
            else:
                W[e] = Dinv[i] @ (Th[e] @ (A[e] @ Dinv[j]))
        _KERNELS[key] = onehop
    return _KERNELS[key]


def onehop_w(Th: torch.Tensor, val: torch.Tensor, Dinv: torch.Tensor, row: torch.Tensor, col: torch.Tensor,
             omega: float) -> torch.Tensor:
    """``onehop_from_theta``'s blocks (form v1) in one kernel over the blocks: no gathered copies of the diagonal
    inverses and no intermediate products.  The same arithmetic to rounding (the diagonal blocks as
    ``omega D^-1 + D^-1 Th``, the small products contracted in another order)."""
    F = int(val.shape[1])
    T, M = _mat(F, F), val.detach().contiguous()
    out = torch.empty_like(M)
    inputs = [wp.from_torch(Th.detach().contiguous(), dtype=T), wp.from_torch(M, dtype=T),
              wp.from_torch(Dinv.detach().contiguous(), dtype=T), wp.from_torch(row.contiguous(), dtype=wp.int64),
              wp.from_torch(col.contiguous(), dtype=wp.int64), float(omega), wp.from_torch(out, dtype=T)]
    _launch(val.device, _onehop_kernel(F), int(M.shape[0]), inputs)
    return out
# ------------------------------------------------------------------ Warp BSR kernels for HybridCompactFast
def _mat_t(F: int, G: int, wp_dtype):
    return wp.types.matrix(shape=(int(F), int(G)), dtype=wp_dtype)


def _vec_t(F: int, wp_dtype):
    return wp.types.vector(length=int(F), dtype=wp_dtype)


def _bsr_jac0_kernel(F: int, wp_dtype):
    key = ("bsr_jac0", F, wp_dtype)
    if key not in _KERNELS:
        TM, TV = _mat_t(F, F, wp_dtype), _vec_t(F, wp_dtype)

        @wp.kernel(enable_backward=False, module="unique")
        def bsr_jac0(Sinv: wp.array(dtype=TM), b: wp.array(dtype=TV), omega: wp_dtype, x_out: wp.array(dtype=TV)):
            i = wp.tid()
            x_out[i] = omega * (Sinv[i] @ b[i])
        _KERNELS[key] = bsr_jac0
    return _KERNELS[key]


def _bsr_jac_kernel(F: int, wp_dtype):
    key = ("bsr_jac", F, wp_dtype)
    if key not in _KERNELS:
        TM, TV = _mat_t(F, F, wp_dtype), _vec_t(F, wp_dtype)

        @wp.kernel(enable_backward=False, module="unique")
        def bsr_jac(A: wp.array(dtype=TM), col: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int32),
                    Sinv: wp.array(dtype=TM), b: wp.array(dtype=TV), x_in: wp.array(dtype=TV),
                    omega: wp_dtype, x_out: wp.array(dtype=TV)):
            i = wp.tid()
            acc = TV()
            for k in range(ptr[i], ptr[i + 1]):
                acc += A[k] @ x_in[col[k]]
            x_out[i] = x_in[i] + omega * (Sinv[i] @ (b[i] - acc))
        _KERNELS[key] = bsr_jac
    return _KERNELS[key]


def _bsr_res_kernel(F: int, wp_dtype):
    key = ("bsr_res", F, wp_dtype)
    if key not in _KERNELS:
        TM, TV = _mat_t(F, F, wp_dtype), _vec_t(F, wp_dtype)

        @wp.kernel(enable_backward=False, module="unique")
        def bsr_res(A: wp.array(dtype=TM), col: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int32),
                    b: wp.array(dtype=TV), x: wp.array(dtype=TV), r_out: wp.array(dtype=TV)):
            i = wp.tid()
            acc = TV()
            for k in range(ptr[i], ptr[i + 1]):
                acc += A[k] @ x[col[k]]
            r_out[i] = b[i] - acc
        _KERNELS[key] = bsr_res
    return _KERNELS[key]


def _bsr_mv_kernel(F: int, G: int, wp_dtype):
    key = ("bsr_mv", F, G, wp_dtype)
    if key not in _KERNELS:
        TM, TX, TY = _mat_t(F, G, wp_dtype), _vec_t(G, wp_dtype), _vec_t(F, wp_dtype)

        @wp.kernel(enable_backward=False, module="unique")
        def bsr_mv(A: wp.array(dtype=TM), col: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int32),
                   x: wp.array(dtype=TX), y_out: wp.array(dtype=TY)):
            i = wp.tid()
            acc = TY()
            for k in range(ptr[i], ptr[i + 1]):
                acc += A[k] @ x[col[k]]
            y_out[i] = acc
        _KERNELS[key] = bsr_mv
    return _KERNELS[key]


def _bsr_mv_add_kernel(F: int, G: int, wp_dtype):
    key = ("bsr_mv_add", F, G, wp_dtype)
    if key not in _KERNELS:
        TM, TX, TY = _mat_t(F, G, wp_dtype), _vec_t(G, wp_dtype), _vec_t(F, wp_dtype)

        @wp.kernel(enable_backward=False, module="unique")
        def bsr_mv_add(A: wp.array(dtype=TM), col: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int32),
                       x: wp.array(dtype=TX), y_inout: wp.array(dtype=TY)):
            i = wp.tid()
            acc = TY()
            for k in range(ptr[i], ptr[i + 1]):
                acc += A[k] @ x[col[k]]
            y_inout[i] = y_inout[i] + acc
        _KERNELS[key] = bsr_mv_add
    return _KERNELS[key]


def bsr_prepare(A, dtype=torch.float32):
    """Prepare Warp BSR arrays ``(val_wp, col_wp, ptr_wp, n_rows, F, G, wp_dtype, keep)`` from a ``BlockCSR``."""
    wp_dtype = wp.float32 if dtype == torch.float32 else wp.float64
    n_rows, n_cols = int(A.shape[0]), int(A.shape[1])
    F, G = int(A.val.shape[1]), int(A.val.shape[2])
    ptr = getattr(A, "_ptr", None)
    if ptr is not None:
        col32 = A.col.to(torch.int32).contiguous()
        ptr32 = ptr.to(torch.int32).contiguous()
        val_t = A.val.to(dtype).contiguous()
    else:
        row, col = A.row, A.col
        if row.numel() > 1 and bool((row[1:] < row[:-1]).any()):
            order = torch.argsort(row * n_cols + col)
            row, col, val_t = row[order], col[order], A.val[order].to(dtype).contiguous()
        else:
            val_t = A.val.to(dtype).contiguous()
        ptr = torch.zeros(n_rows + 1, dtype=torch.int64, device=row.device)
        ptr[1:] = torch.cumsum(torch.bincount(row, minlength=n_rows), 0)
        col32 = col.to(torch.int32).contiguous()
        ptr32 = ptr.to(torch.int32).contiguous()
    TM = _mat_t(F, G, wp_dtype)
    keep = (val_t, col32, ptr32)
    return (wp.from_torch(val_t, dtype=TM), wp.from_torch(col32), wp.from_torch(ptr32),
            n_rows, F, G, wp_dtype, keep)


def _bsr_mv_fb_kernel(F: int, G: int, wp_dtype):
    key = ("bsr_mv_fb", F, G, wp_dtype)
    if key not in _KERNELS:
        TM, TX, TY = _mat_t(F, G, wp_dtype), _vec_t(G, wp_dtype), _vec_t(F, wp_dtype)

        @wp.kernel(enable_backward=False, module="unique")
        def bsr_mv_fb(A: wp.array(dtype=TM), col: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int32),
                      n_rows: int, n_cols: int, x: wp.array(dtype=wp_dtype), y_out: wp.array(dtype=wp_dtype)):
            i = wp.tid()
            acc = TY()
            for k in range(ptr[i], ptr[i + 1]):
                c = col[k]
                xc = TX()
                for g in range(wp.static(G)):
                    xc[g] = x[g * n_cols + c]
                acc += A[k] @ xc
            for f in range(wp.static(F)):
                y_out[f * n_rows + i] = acc[f]
        _KERNELS[key] = bsr_mv_fb
    return _KERNELS[key]


def bsr_mv_field_blocked(A, dtype=torch.float64):
    """Fast Warp callable ``v -> A @ v`` for field-blocked flat ``v`` of length ``G * n_cols``."""
    device = A.val.device
    val_wp, col_wp, ptr_wp, n_rows, F, G, wp_dtype, keep = bsr_prepare(A, dtype=dtype)
    n_cols = int(A.shape[1])
    kern = _bsr_mv_fb_kernel(F, G, wp_dtype)

    def apply(v: torch.Tensor) -> torch.Tensor:
        x = v if v.dtype == dtype and v.is_contiguous() else v.to(dtype).contiguous()
        y = torch.empty(F * n_rows, dtype=dtype, device=device)
        _launch(device, kern, n_rows, [val_wp, col_wp, ptr_wp, n_rows, n_cols, wp.from_torch(x), wp.from_torch(y)])
        return y if v.dtype == dtype else y.to(v.dtype)

    apply._keep = keep
    return apply
