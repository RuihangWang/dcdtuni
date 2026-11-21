"""Warp thread-per-row kernels for the sliced-ELL block storage of ``block_kernels``.

The M3 Triton kernel (``block_kernels._sell_kernel``) processes a slice of ``C`` rows as a ``[C, KB x 32]`` tile: per
block slot it loads the column index once per entry lane (25 times per block) and the x entry five times per block,
and 7 of every 32 lanes are padding (25 entries per 5 x 5 block).  W2a measured it at 2.3-2.9 TB/s on the W01 fine level
(5.7 GB per pass), against 4.0 TB/s for cuBLAS's GEMV on the same card.

Here one thread owns one row: for each of its block slots it loads the column once, the G entries of x once and the
F G values of the block (the slot-major layout puts the C rows of a slice side by side, so a warp's 32 threads -- 32 rows
of one slice when C = 32 -- read 256 contiguous bytes per entry), and keeps the F row sums in registers.  Same storage,
same operations (``A x``, ``b - A x``, ``y += A x``, the multicolour GS colour pass ``x_c += omega Sinv (b - A x)_c``), sums
in another order: rounding-level differences.  Deterministic.

``zero_hi``: the first pre-smoothing sweep starts from x = 0, so a colour pass reads non-zero x only on the colours before
it; with the columns of a row stored in rank (= colour) order the row's loop stops at its first column at or past the
colour's first rank.  The skipped products are exact zeros, so the result equals the full pass bit for bit.

Used by ``block_kernels.SellMatrix._launch`` when ``block_kernels.ROWS_KERNEL["on"]`` (``--camg-rows-kernel``) and the
slice height is at least ``ROWS_KERNEL["min_c"]`` (fine levels; coarse levels keep the Triton tile, whose slots in
parallel suit their long rows and few cells).
"""
import torch
import warp as wp

wp.config.quiet = True
wp.init()

_KERNELS: dict = {}
_STREAMS: dict = {}
_DEDICATED: dict = {}


def _kernel(F: int, G: int, C: int, MODE: int, ZERO: bool, fp32: bool = False):
    key = (F, G, C, MODE, bool(ZERO), bool(fp32))
    if key in _KERNELS:
        return _KERNELS[key]
    FG, FF = F * G, F * F
    VT = wp.float32 if fp32 else wp.float64
    TV = wp.types.vector(length=F, dtype=wp.float64)
    TX = wp.types.vector(length=G, dtype=wp.float64)

    @wp.kernel(enable_backward=False, module="unique")
    def sell_rows(row0: wp.array(dtype=wp.int32), nrow: wp.array(dtype=wp.int32), vbase: wp.array(dtype=wp.int64),
                  cbase: wp.array(dtype=wp.int64), lens: wp.array(dtype=wp.int32), val: wp.array(dtype=VT),
                  col: wp.array(dtype=wp.int32), x: wp.array(dtype=wp.float64), b: wp.array(dtype=wp.float64),
                  y: wp.array(dtype=wp.float64), sinv: wp.array(dtype=VT), omega: wp.array(dtype=wp.float64),
                  slice0: wp.int32, nslices: wp.int32, lo_col: wp.int32, cc: wp.int32, c64: wp.int64,
                  fgc: wp.int64, ffc: wp.int64):
        # every memory index is an int64 (or int32) VARIABLE advanced by runtime strides: no arithmetic on the
        # unrolled loop constants inside an index (their integer type depends on how far a loop unrolls)
        tid = wp.tid()
        sl = tid // cc
        r = tid - sl * cc
        if sl >= nslices:
            return
        s = sl + slice0
        if r >= nrow[s]:
            return
        row = row0[s] + r
        nb = lens[row]
        vb = vbase[s] + wp.int64(r)
        cb = cbase[s] + wp.int64(r)
        acc = TV()
        for kk in range(nb):
            k64 = wp.int64(kk)
            c = col[cb + k64 * c64]
            if wp.static(ZERO):
                if c >= lo_col:
                    break
            xv = TX()
            xi = c * wp.static(G)
            for g in range(wp.static(G)):
                xv[g] = x[xi]
                xi = xi + 1
            off = vb + k64 * fgc
            for f in range(wp.static(F)):
                t = acc[f]
                for g in range(wp.static(G)):
                    t = t + wp.float64(val[off]) * xv[g]
                    off = off + c64
                acc[f] = t
        ri = row * wp.static(F)
        if wp.static(MODE == 0):
            for f in range(wp.static(F)):
                y[ri] = acc[f]
                ri = ri + 1
        elif wp.static(MODE == 1):
            for f in range(wp.static(F)):
                y[ri] = b[ri] - acc[f]
                ri = ri + 1
        elif wp.static(MODE == 2):
            for f in range(wp.static(F)):
                y[ri] = y[ri] + acc[f]
                ri = ri + 1
        else:
            res = TV()
            rj = ri
            for f in range(wp.static(F)):
                res[f] = b[rj] - acc[f]
                rj = rj + 1
            om = omega[0]
            soff = wp.int64(s) * ffc + wp.int64(r)
            for f in range(wp.static(F)):
                d = wp.float64(0.0)
                for g in range(wp.static(F)):
                    d = d + wp.float64(sinv[soff]) * res[g]
                    soff = soff + c64
                if wp.static(MODE == 3):
                    y[ri] = d * om
                else:
                    x[ri] = x[ri] + d * om
                ri = ri + 1
    _KERNELS[key] = sell_rows
    return sell_rows


_READY: dict = {}
LAST_ERROR: dict = {}


def ready(matrix, mode: int, zero: bool) -> bool:
    """Whether the rows-kernel variant of ``matrix`` / ``mode`` builds and loads (compiled once, then cached by Warp);
    a failure is recorded in ``LAST_ERROR`` and the caller keeps the Triton tile."""
    P = matrix.pattern
    key = (P.F, P.G, P.C, int(mode), bool(zero), matrix.values.dtype == torch.float32)
    ok = _READY.get(key)
    if ok is None:
        try:
            _kernel(*key).module.load(wp.device_from_torch(matrix.values.device))
            ok = True
        except Exception as exc:   # noqa: BLE001
            ok = False
            LAST_ERROR["error"] = f"{type(exc).__name__}: {exc}"[:400]
        _READY[key] = ok
    return ok


def _fill_kernel(FG: int, C: int, fp32: bool):
    key = ("fill", FG, C, bool(fp32))
    if key in _KERNELS:
        return _KERNELS[key]
    OT = wp.float32 if fp32 else wp.float64

    @wp.kernel(enable_backward=False, module="unique")
    def sell_fill(row0: wp.array(dtype=wp.int32), nrow: wp.array(dtype=wp.int32), vbase: wp.array(dtype=wp.int64),
                  lens: wp.array(dtype=wp.int32), ptr: wp.array(dtype=wp.int64), order: wp.array(dtype=wp.int64),
                  val: wp.array(dtype=wp.float64), out: wp.array(dtype=OT), nslices: wp.int32, cc: wp.int32,
                  fg: wp.int32, c64: wp.int64, fg64: wp.int64):
        # every index in int64 from int64 operands (runtime sizes, no unrolled constants: the q loop's 16 vs 25
        # trips unroll differently, and a mixed int32 / int64 sum does not compile)
        tid = wp.tid()
        s = tid // cc
        r = tid - s * cc
        if s >= nslices:
            return
        if r >= nrow[s]:
            return
        row = row0[s] + r
        e0 = ptr[row]
        vb = vbase[s] + wp.int64(r)
        for kk in range(lens[row]):
            k64 = wp.int64(kk)
            src = order[e0 + k64] * fg64
            base = vb + k64 * fg64 * c64
            for q in range(fg):
                q64 = wp.int64(q)
                out[base + q64 * c64] = OT(val[src + q64])
    _KERNELS[key] = sell_fill
    return sell_fill


def fill(pattern, val: torch.Tensor, dtype) -> torch.Tensor:
    """``SellPattern.fill``'s value array in one Warp kernel (a thread per row writes its slots: coalesced over the
    slice's rows), with the pattern's sorted-block order kept on the device (built once)."""
    P = pattern
    device = val.device
    out = torch.zeros(max(P.total_v, 1), dtype=dtype, device=device)
    if P.nnzb == 0 or P.n_slices == 0:
        return out
    order = getattr(P, "_warp_order", None)
    if order is None:
        order = P._warp_order = (P.order if P.order.device == device else P.order.to(device)).long().contiguous()
    t = tables(P)
    ptr = P.ptr if P.ptr.dtype == torch.int64 else P.ptr.long()
    keep = getattr(P, "_warp_fill_keep", None)
    if keep is None:
        keep = P._warp_fill_keep = (ptr.contiguous(), order)
    flat = val.detach().reshape(-1).contiguous()
    kern = _fill_kernel(P.F * P.G, P.C, dtype == torch.float32)
    ws, wts, ts = _launch_stream(device)
    if wts is not None:
        wts.wait_stream(ts)
    wp.launch(kern, dim=P.n_slices * P.C,
              inputs=[t[0], t[1], t[2], t[4], _wa(keep[0]), _wa(keep[1]), _wa(flat), _wa(out), int(P.n_slices),
                      int(P.C), int(P.F * P.G), int(P.C), int(P.F * P.G)],
              stream=ws, device=wp.device_from_torch(device))
    if wts is not None:
        ts.wait_stream(wts)
    return out


def _launch_stream(device):
    """(Warp stream, torch stream to order against or None): the caller's torch stream itself when it is a real
    stream (a CUDA-graph capture's side stream, for instance), else a dedicated stream ordered both ways against the
    legacy default stream (on which no graph can be captured)."""
    ts = torch.cuda.current_stream(device)
    handle = int(ts.cuda_stream)
    if handle != 0:
        ws = _STREAMS.get(handle)
        if ws is None:
            ws = _STREAMS[handle] = wp.Stream(wp.device_from_torch(device), cuda_stream=handle)
        return ws, None, ts
    key = torch.device(device)
    if key not in _DEDICATED:
        ws = wp.Stream(wp.device_from_torch(key))
        _DEDICATED[key] = (ws, torch.cuda.ExternalStream(ws.cuda_stream, device=key))
    ws, wts = _DEDICATED[key]
    return ws, wts, ts


def _wa(t, dtype=None):
    return wp.from_torch(t, dtype=dtype) if dtype is not None else wp.from_torch(t)


def tables(pattern):
    """Warp views of a ``SellPattern``'s tables (built once, kept on the pattern)."""
    tb = getattr(pattern, "_warp_tables", None)
    if tb is None:
        vb = pattern.vbase if pattern.vbase.dtype == torch.int64 else pattern.vbase.long()
        cb = pattern.cbase if pattern.cbase.dtype == torch.int64 else pattern.cbase.long()
        keep = (pattern.row0.contiguous(), pattern.nrow.contiguous(), vb.contiguous(), cb.contiguous(),
                pattern.lens.contiguous(), pattern.col.contiguous())
        tb = pattern._warp_tables = (keep, [_wa(t) for t in keep])
    return tb[1]


def launch(matrix, mode: int, x, b, y, sinv=None, omega=None, group=None, lo_col=None) -> None:
    """``SellMatrix._launch`` on the Warp rows kernel; ``lo_col`` (an int) = the first-sweep zero skip."""
    P = matrix.pattern
    s0, ns = (0, P.n_slices) if group is None else P.group_slices[group]
    if ns == 0:
        return
    device = x.device
    kern = _kernel(P.F, P.G, P.C, int(mode), lo_col is not None, matrix.values.dtype == torch.float32)
    vals = getattr(matrix, "_warp_values", None)
    if vals is None or vals[0] is not matrix.values:
        vals = matrix._warp_values = (matrix.values, _wa(matrix.values))
    t = tables(P)
    dummy = vals[1]
    args = t[:5] + [vals[1], t[5], _wa(x), _wa(b) if b is not None else _wa(x), _wa(y),
                    _wa(sinv) if sinv is not None else dummy, _wa(omega) if omega is not None else _wa(x),
                    int(s0), int(ns), int(lo_col if lo_col is not None else 0), int(P.C), int(P.C),
                    int(P.F * P.G * P.C), int(P.F * P.F * P.C)]
    ws, wts, ts = _launch_stream(device)
    if wts is not None:
        wts.wait_stream(ts)
    wp.launch(kern, dim=int(ns) * P.C, inputs=args, stream=ws, device=wp.device_from_torch(device))
    if wts is not None:
        ts.wait_stream(wts)


# A Warp array dimension is a signed 32-bit integer: the flat value and table arrays these kernels read must stay below
# it.  Above (the fine level at 152^3: 2.18e9 values) the matrix takes the Triton
# kernel / the torch fill; the coarser levels keep Warp.
WARP_DIM_MAX = 2 ** 31 - 1


def fits(*tensors) -> bool:
    """Whether every tensor's element count fits one Warp array dimension."""
    return all(t is None or t.numel() <= WARP_DIM_MAX for t in tensors)


def supported(matrix, min_c: int) -> bool:
    P = matrix.pattern
    return (matrix.values.dtype in (torch.float64, torch.float32) and P.C >= int(min_c) and P.F * P.G <= 64
            and matrix.values.is_cuda and fits(matrix.values, P.col))
