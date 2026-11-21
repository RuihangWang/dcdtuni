"""Distributed restarted FGMRES for a partitioned problem (docs/development/multi-gpu.md section 5).

Right preconditioned and flexible (the preconditioned directions are stored), classical Gram-Schmidt with one
re-orthogonalisation (CGS2): each pass is one local V^T w and ONE all_reduce of the whole projection
vector, so an Arnoldi step costs two or three reductions whatever the restart length.  When basis_dtype=torch.float32
on CUDA with a float64 state vector, _WarpDistOrth fuses the vector cast/subtraction/normalization in Warp
kernels around cuBLAS fp32 GEMVs, combines the second-pass projection and squared norm into a single in-place
all_reduce (two reductions per step), and overlaps the second-pass basis subtraction/store with the host Givens
rotation update.
"""
from __future__ import annotations

import importlib.util
import math
from types import SimpleNamespace

import torch

_HAVE_WARP = importlib.util.find_spec("warp") is not None
_WK_DIST = None


def _dist_warp_kernels():
    global _WK_DIST
    if _WK_DIST is not None:
        return _WK_DIST
    import warp as wp
    wp.config.quiet = True
    wp.init()
    mod = "Parallel.krylov_warp"
    wp.set_module_options({"enable_backward": False}, module=mod)

    @wp.kernel(enable_backward=False, module=mod)
    def cast_f64_f32(w: wp.array(dtype=wp.float64), w32: wp.array(dtype=wp.float32)):
        i = wp.tid()
        w32[i] = wp.float32(w[i])

    @wp.kernel(enable_backward=False, module=mod)
    def sub_cast_f32(w: wp.array(dtype=wp.float64), sub32: wp.array(dtype=wp.float32), w32: wp.array(dtype=wp.float32)):
        i = wp.tid()
        wi = w[i] - wp.float64(sub32[i])
        w[i] = wi
        w32[i] = wp.float32(wi)

    @wp.kernel(enable_backward=False, module=mod)
    def finish_h_kernel(hw1: wp.array(dtype=wp.float64), hw2: wp.array(dtype=wp.float64), k: wp.int32):
        tid = wp.tid()
        if tid == 0:
            s2 = wp.float64(0.0)
            for i in range(k):
                h2i = hw2[i]
                hw1[i] = hw1[i] + h2i
                s2 = s2 + h2i * h2i
            w1_sq = hw2[k]
            rem = w1_sq - s2
            if rem > wp.float64(1e-6) * w1_sq:
                hw1[k] = wp.sqrt(rem)
            else:
                hw1[k] = wp.float64(-1.0)

    @wp.kernel(enable_backward=False, module=mod)
    def sub_norm_store(w: wp.array(dtype=wp.float64), sub32: wp.array(dtype=wp.float32),
                       hw1: wp.array(dtype=wp.float64), k: wp.int32,
                       vj_f64: wp.array(dtype=wp.float64), v_next: wp.array(dtype=wp.float32)):
        i = wp.tid()
        wi = w[i] - wp.float64(sub32[i])
        hn = hw1[k]
        if hn > wp.float64(0.0):
            vi = wi / hn
            vj_f64[i] = vi
            v_next[i] = wp.float32(vi)
        else:
            w[i] = wi

    _WK_DIST = SimpleNamespace(wp=wp, cast_f64_f32=cast_f64_f32, sub_cast_f32=sub_cast_f32,
                               finish_h_kernel=finish_h_kernel, sub_norm_store=sub_norm_store)
    return _WK_DIST


class _WarpDistOrth:
    """Overlapped Warp+cuBLAS fp32-basis CGS2 orthogonaliser for distributed FGMRES (2 all-reduces/step)."""

    def __init__(self, V: torch.Tensor, m: int, n: int):
        K = _dist_warp_kernels()
        self.K = K
        self.wp = K.wp
        self.V = V
        self.m, self.n = int(m), int(n)
        dev = V.device
        self.dev = dev
        self.wdev = self.wp.device_from_torch(dev)
        self.w32 = torch.empty(n, device=dev, dtype=torch.float32)
        self.sub32 = torch.empty(n, device=dev, dtype=torch.float32)
        self.vj_f64 = torch.empty(n, device=dev, dtype=torch.float64)
        self.hw1 = torch.empty(m + 2, device=dev, dtype=torch.float64)
        self.hw2 = torch.empty(m + 2, device=dev, dtype=torch.float64)
        self.h32 = torch.empty(m + 1, device=dev, dtype=torch.float32)
        self.host_buf = torch.empty(m + 2, dtype=torch.float64, pin_memory=True)
        self.ev = torch.cuda.Event()
        f = self.wp.from_torch
        self.w32_w = f(self.w32)
        self.sub32_w = f(self.sub32)
        self.vj_f64_w = f(self.vj_f64)
        self.hw1_w = f(self.hw1)
        self.hw2_w = f(self.hw2)
        self.V_rows_w = [f(V[j]) for j in range(m + 1)]

    def start_cycle(self, r: torch.Tensor, beta: float):
        torch.mul(r, 1.0 / beta, out=self.vj_f64)
        self.V[0].copy_(self.vj_f64)

    def step(self, w: torch.Tensor, j: int, dist, gnorm):
        k = j + 1
        Vj = self.V[:k]
        wp = self.wp
        K = self.K
        ts = torch.cuda.current_stream(self.dev)
        ws = wp.stream_from_torch(ts)
        if not w.is_contiguous():
            w = w.contiguous()
        w_w = wp.from_torch(w)
        wp.launch(K.cast_f64_f32, dim=self.n, inputs=[w_w, self.w32_w], device=self.wdev, stream=ws)
        torch.mv(Vj, self.w32, out=self.h32[:k])
        self.hw1[:k].copy_(self.h32[:k])
        dist.sum_inplace(self.hw1[:k])
        self.h32[:k].copy_(self.hw1[:k])
        torch.mv(Vj.t(), self.h32[:k], out=self.sub32)
        wp.launch(K.sub_cast_f32, dim=self.n, inputs=[w_w, self.sub32_w, self.w32_w], device=self.wdev, stream=ws)
        torch.mv(Vj, self.w32, out=self.h32[:k])
        self.hw2[:k].copy_(self.h32[:k])
        self.hw2[k] = torch.dot(w, w)
        dist.sum_inplace(self.hw2[:k + 1])
        self.h32[:k].copy_(self.hw2[:k])
        wp.launch(K.finish_h_kernel, dim=1, inputs=[self.hw1_w, self.hw2_w, k], device=self.wdev, stream=ws)
        self.host_buf[:k + 1].copy_(self.hw1[:k + 1], non_blocking=True)
        self.ev.record(ts)
        torch.mv(Vj.t(), self.h32[:k], out=self.sub32)
        wp.launch(K.sub_norm_store, dim=self.n,
                  inputs=[w_w, self.sub32_w, self.hw1_w, k, self.vj_f64_w, self.V_rows_w[k]],
                  device=self.wdev, stream=ws)
        self.ev.synchronize()
        hv = self.host_buf[:k + 1].tolist()
        hn = hv[k]
        if hn < 0.0:
            hn = gnorm(w)
            if hn > 0.0:
                torch.mul(w, 1.0 / hn, out=self.vj_f64)
                self.V[k].copy_(self.vj_f64)
        return hv[:k], hn


def _givens(f: float, g: float):
    if g == 0.0:
        return 1.0, 0.0, f
    if f == 0.0:
        return 0.0, 1.0, g
    r = math.hypot(f, g)
    return f / r, g / r, r


def fgmres(apply, b, dist, *, M=None, x0=None, rtol=1e-8, atol=0.0, restart=200, max_cycles=4,
           max_iterations=None, log=None, flexible=True, orth="cgs2", basis_dtype=None):
    """Solve apply(x) = b over all parts.  dist: Parallel.comm.Distributed (sum reduces a tensor).
    Returns (x, info), info with iterations, cycles, rel (true relative residual),
    converged."""
    n = b.numel()
    dev, dt = b.device, b.dtype

    def gnorm(v):
        return math.sqrt(max(float(dist.sum(torch.dot(v, v))), 0.0))

    bnorm = gnorm(b)
    info = {"iterations": 0, "cycles": 0, "rel": 0.0, "converged": True, "bnorm": bnorm}
    x = torch.zeros_like(b) if x0 is None else x0.clone()
    if bnorm == 0.0:
        return x, info
    target = max(rtol * bnorm, atol)
    m = int(restart)
    bdt = dt if basis_dtype is None else basis_dtype
    V = torch.empty(m + 1, n, device=dev, dtype=bdt)
    Z = torch.empty(m, n, device=dev, dtype=dt) if (flexible and M is not None) else None
    budget = int(max_iterations) if max_iterations is not None else m * int(max_cycles)
    r = b - apply(x) if x0 is not None else b.clone()
    beta = gnorm(r)
    use_warp_orth = (_HAVE_WARP and dev.type == "cuda" and dt == torch.float64 and bdt == torch.float32
                     and hasattr(dist, "sum_inplace"))
    worth = _WarpDistOrth(V, m, n) if use_warp_orth else None
    while True:
        if beta <= target or info["cycles"] >= max_cycles or info["iterations"] >= budget:
            break
        info["cycles"] += 1
        if worth is not None:
            worth.start_cycle(r, beta)
        else:
            V[0] = r / beta
        H = torch.zeros(m + 1, m, dtype=torch.float64)
        cs, sn = [0.0] * m, [0.0] * m
        g = [0.0] * (m + 1)
        g[0] = beta
        k = 0
        for j in range(m):
            if worth is not None:
                vj = worth.vj_f64
            else:
                vj = V[j] if bdt == dt else V[j].to(dt)
            z = vj if M is None else M(vj)
            if Z is not None:
                Z[j] = z
            w = apply(z)
            if worth is not None:
                h_col, hn = worth.step(w, j, dist, gnorm)
                col = h_col + [hn]
            else:
                Vj = V[:j + 1]

                def proj(u):
                    return (Vj @ u if bdt == dt else (Vj @ u.to(bdt)).to(dt))

                def sub(u, hh):
                    return u - (Vj.t() @ hh if bdt == dt else (Vj.t() @ hh.to(bdt)).to(dt))
                if orth == "dgks":
                    hw = dist.sum(torch.cat([proj(w), torch.dot(w, w).reshape(1)]))
                    h, w_norm2 = hw[:j + 1], hw[j + 1]
                    w = sub(w, h)
                    nn_ = dist.sum(torch.dot(w, w))
                    if float(nn_) < 0.5 * float(w_norm2):
                        h2 = dist.sum(proj(w))
                        w = sub(w, h2)
                        nn = dist.sum(torch.dot(w, w))
                    else:
                        h2 = torch.zeros_like(h)
                        nn = nn_
                else:
                    h = dist.sum(proj(w))
                    w = sub(w, h)
                    h2 = dist.sum(proj(w))
                    w = sub(w, h2)
                    nn = dist.sum(torch.dot(w, w))
                hv = torch.cat([(h + h2).double(), nn.double().reshape(1)]).cpu().tolist()
                hn = math.sqrt(max(hv[j + 1], 0.0))
                col = hv[:j + 1] + [hn]
                if hn > 0.0:
                    V[j + 1] = w / hn
            for i in range(j):
                a_, b_ = col[i], col[i + 1]
                col[i] = cs[i] * a_ + sn[i] * b_
                col[i + 1] = -sn[i] * a_ + cs[i] * b_
            c_, s_, rr = _givens(col[j], col[j + 1])
            cs[j], sn[j] = c_, s_
            col[j], col[j + 1] = rr, 0.0
            H[:j + 2, j] = torch.tensor(col, dtype=torch.float64)
            g[j + 1] = -s_ * g[j]
            g[j] = c_ * g[j]
            k = j + 1
            info["iterations"] += 1
            if abs(g[j + 1]) <= target or hn == 0.0 or info["iterations"] >= budget:
                break
        y = torch.linalg.solve_triangular(H[:k, :k], torch.tensor(g[:k], dtype=torch.float64).reshape(-1, 1),
                                          upper=True).reshape(-1)
        if Z is not None:
            x = x + Z[:k].t() @ y.to(device=dev, dtype=dt)
        else:
            u = (V[:k].t() @ y.to(device=dev, dtype=bdt)).to(dt)
            x = x + (u if M is None else M(u))
        r = b - apply(x)
        beta = gnorm(r)
        if log is not None:
            log(f"fgmres cycle {info['cycles']}: {k} steps, recurrence {abs(g[k]) / bnorm:.3e}, true {beta / bnorm:.3e}")
    info["rel"] = beta / bnorm
    info["converged"] = beta <= target
    return x, info
