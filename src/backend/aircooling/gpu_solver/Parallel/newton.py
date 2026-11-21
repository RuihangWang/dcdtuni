"""Partitioned Newton-Krylov with pseudo-transient continuation and global norms (design sections 5-6).

``solve(problem, ...)`` mirrors the defaults of the serial ``Pipeline.CoupledNewton.newton.solve_newton`` that a
partitioned run needs, with every norm, dot product and decision reduced over all parts:

* stop on the global ``max|r| < tol``, or when a ``stop_rule(x, r, it)`` says so on EVERY rank (collective AND;
  a rule computes its global quantities with ``problem.field_norms`` / ``problem.dist``, e.g.
  ``residual_control``);
* Eisenstat-Walker choice 2 floored at ``rtol`` (``newton.inner_forcing``, the very function);
* Krylov operator ``assembled`` (own rows of the part's assembled Jacobian times the extended vector, the serial
  default) or ``jvp`` (forward-mode, exact);
* restricted additive Schwarz around a local preconditioner of the part's assembled Jacobian on own + halo:
  ``camg`` (coupled block AMG), ``simple`` (``simple_preconditioner``), ``dense`` (LU; tiny parts), ``none``;
* backtracking on the global ``||r||_2``; PTC on the differential rows (``d = theta |diag J_steady|``, zero on
  continuity, armed in each part's residual on own + halo), SER controller, ceiling ``max(1, theta0) (||r|| /
  ||r0||)^0.5``, cut-back on a refused line search, forced acceptance at the ceiling, handover at
  ``ptc_handover``; ``ptc_max_forced_growth`` refuses a forced step that grows the steady ``||r||`` by more than
  that factor (and then shrinks the pseudo-timestep past the ceiling);
* ``coarse=True``: a two-level preconditioner, the Nicolaides coarse space (one constant per part per field,
  ``CoarseSpace``) applied before the RAS.
"""
from __future__ import annotations

import math
import time

import torch

from Parallel.krylov import fgmres


def enable_warp_amg(dev):
    if dev is None or dev.type != "cuda":
        return
    from Pipeline.CoupledNewton.block_amg import WARP_NUMERIC, LAZY_COLOURS, COARSE_INVERSE
    from Pipeline.CoupledNewton.block_kernels import POLICY, ROWS_KERNEL, WARP_FILL, ZERO_SKIP
    _ = torch.linalg.lu_factor(torch.eye(4, dtype=torch.float64, device=dev))
    WARP_NUMERIC["on"] = WARP_NUMERIC["omega"] = WARP_NUMERIC["omega_device"] = True
    LAZY_COLOURS["on"] = True
    COARSE_INVERSE["on"] = True
    WARP_FILL["on"] = True
    ROWS_KERNEL["on"] = True
    ROWS_KERNEL["gs"] = False
    ROWS_KERNEL["min_c"] = 16
    ZERO_SKIP["on"] = True
    POLICY["tile4"] = 2


# ----------------------------------------------------------------------------------- preconditioners
def local_preconditioner(problem, blocks, kind: str = "camg", **kw):
    """``v_loc -> z_loc`` of the part's assembled Jacobian (own + halo), or None."""
    J = blocks["J"]
    if kind == "none":
        return None
    if kind == "dense":
        lu = torch.linalg.lu_factor(J.to_dense())
        return lambda v: torch.linalg.lu_solve(*lu, v.reshape(-1, 1)).reshape(-1)
    if kind == "camg":
        from Pipeline.CoupledNewton import block_amg
        A = block_amg.from_field_blocked_csr(J.crow_indices(), J.col_indices(), J.values(), problem.n_loc,
                                             problem.F)
        amg = block_amg.CoupledAMG(A, **{"smoother": "gs", "restriction": "pg", **kw})
        return lambda v: amg(v)
    if kind == "simple":
        return problem.local.simple_preconditioner(blocks, **kw)
    raise ValueError(f"unknown local preconditioner {kind!r}: camg | simple | dense | none")


def restricted_additive_schwarz(problem, local_apply):
    """``z_own = [M_p extend(v)]_own``: one exchange per application."""
    if local_apply is None:
        return None
    if problem._gauge_free:
        return lambda v: problem.gauge(problem.restrict(local_apply(problem.extend(v))))
    return lambda v: problem.restrict(local_apply(problem.extend(v)))


class CoarseSpace:
    """Nicolaides coarse space: one constant per part per field (``nparts * F`` columns, ``P``), Galerkin matrix
    ``A_c = P^T A P`` from ``nparts * F`` operator products, identical on every rank.  ``apply`` is the two-level
    hybrid ``z = z_c + M (v - A z_c)``, ``z_c = P A_c^+ P^T v``: one extra product per application, and the
    inter-part transport of every field's mean that a one-level Schwarz method can only diffuse."""

    def __init__(self, problem, A, *, cols: torch.Tensor | None = None):
        self.problem, self.A = problem, A
        self._A_init = A
        dist = problem.dist
        F, n = problem.F, problem.n_own
        k = dist.world * F
        Ac = torch.zeros(k, k, dtype=problem.dtype, device=problem.device)
        if cols is not None:
            self.AP = cols.T.contiguous()
            Ac[dist.rank * F:(dist.rank + 1) * F, :] = cols.reshape(k, F, n).sum(2).T
        elif hasattr(A, "batched") and hasattr(problem, "coarse_basis"):
            E_own, E_loc = problem.coarse_basis()
            cols = A.batched(E_own, E_loc)
            self.AP = cols.T.contiguous()
            Ac[dist.rank * F:(dist.rank + 1) * F, :] = cols.reshape(k, F, n).sum(2).T
        else:
            self.AP = torch.empty(F * n, k, dtype=problem.dtype, device=problem.device)
            for q in range(dist.world):
                for f in range(F):
                    j = q * F + f
                    e = torch.zeros(F, n, dtype=problem.dtype, device=problem.device)
                    if dist.rank == q:
                        e[f] = 1.0
                    col = A(e.reshape(-1))
                    self.AP[:, j] = col
                    Ac[dist.rank * F:(dist.rank + 1) * F, j] = col.reshape(F, n).sum(1)
        self.Ac = dist.sum_inplace(Ac) if hasattr(dist, "sum_inplace") else dist.sum(Ac)
        self.pinv = torch.linalg.pinv(self.Ac)
        self._c_buf = torch.zeros(k, dtype=problem.dtype, device=problem.device)

    def restrict(self, v):
        p = self.problem
        c = self._c_buf
        c.zero_()
        c[p.dist.rank * p.F:(p.dist.rank + 1) * p.F] = v.reshape(p.F, p.n_own).sum(1)
        return p.dist.sum_inplace(c) if hasattr(p.dist, "sum_inplace") else p.dist.sum(c)

    def prolong(self, y):
        p = self.problem
        block = y[p.dist.rank * p.F:(p.dist.rank + 1) * p.F]
        return block[:, None].expand(p.F, p.n_own).reshape(-1).clone()

    def correction(self, v):
        return self.prolong(self.pinv @ self.restrict(v))

    def wrap(self, M):
        def apply(v):
            y = self.pinv @ self.restrict(v)
            zc = self.prolong(y)
            if M is None:
                return zc
            Azc = torch.mv(self.AP, y) if self.A is self._A_init else self.A(zc)
            return zc + M(v - Azc)
        return apply


class AggregationCoarse:
    """Two-level coarse space for the restricted additive Schwarz: ``m`` geometric aggregates per part (recursive
    coordinate bisection of the own cell centres), piecewise constant per aggregate on the chosen fields (``p_rgh``
    only, or all).  Galerkin ``A_c = Z^T (J + S) Z`` from each part's ASSEMBLED own rows (halo columns carry the
    owners' aggregate ids), summed by one all-reduce, scaled per field like the Krylov operator and LU-factored on
    every rank (small: ``nparts * m * len(fields)``).  ``wrap(M, A)``: ``z = z_c + M(v - A z_c)``."""

    def __init__(self, problem, m: int, fields):
        import numpy as np
        from Parallel.partition import rcb
        P = self.p = problem
        self.m, self.fields = int(m), list(fields)
        app = P.local.solver
        pos = app.cpd_cell_pos[P.local.interior][P.own].detach().cpu().numpy().astype(np.float64)
        m_eff = max(1, min(self.m, P.n_own))
        local = torch.as_tensor(rcb(pos, m_eff), device=P.device)
        self.NA = P.dist.world * self.m
        agg = torch.full((1, P.n_loc), -1.0, dtype=torch.float64, device=P.device)
        agg[0, P.own] = (local + P.dist.rank * self.m).to(torch.float64)
        P.dist.exchange(agg)
        self.agg_loc = agg[0].round().long()
        self.agg_own = self.agg_loc[P.own]
        self.nc = len(self.fields) * self.NA
        self.Ac, self.lu = None, None

    def galerkin(self, J, vals):
        """``A_c`` from the part's local CSR ``J`` with the shifted values ``vals`` (own rows only)."""
        P = self.p
        n, NA = P.n_loc, self.NA
        crow, col = J.crow_indices(), J.col_indices()
        Ac = torch.zeros(self.nc, self.nc, dtype=torch.float64, device=P.device)
        fpos = {f: k for k, f in enumerate(self.fields)}
        for f in self.fields:
            lo, hi = int(crow[f * n]), int(crow[(f + 1) * n])
            cnt = crow[f * n + 1:(f + 1) * n + 1] - crow[f * n:(f + 1) * n]
            cell = torch.repeat_interleave(torch.arange(n, device=P.device), cnt)
            c = col[lo:hi]
            v = vals[lo:hi]
            g, cc = c // n, c % n
            keep = P.is_own[cell] & (self.agg_loc[cc] >= 0)
            I = fpos[f] * NA + self.agg_loc[cell]
            for gf, k in fpos.items():
                sel = keep & (g == gf)
                Ac.index_put_((I[sel], k * NA + self.agg_loc[cc[sel]]), v[sel], accumulate=True)
        self.Ac = P.dist.sum(Ac)
        self.lu = None

    def factor(self, field_scale=None):
        A = self.Ac
        if field_scale is not None:
            d = torch.cat([torch.full((self.NA,), float(field_scale[f]), dtype=A.dtype, device=A.device)
                           for f in self.fields])
            A = d[:, None] * A
        diag = A.diagonal().abs()
        A = A + torch.diag(torch.where(diag > 0, torch.zeros_like(diag), torch.ones_like(diag)))  # empty aggregates
        self.lu = torch.linalg.lu_factor(A)

    def restrict(self, v):
        P = self.p
        c = torch.zeros(self.nc, dtype=v.dtype, device=v.device)
        vf = v.reshape(P.F, P.n_own)
        for k, f in enumerate(self.fields):
            c.index_add_(0, k * self.NA + self.agg_own, vf[f])
        return P.dist.sum(c)

    def prolong(self, y):
        P = self.p
        z = torch.zeros(P.F, P.n_own, dtype=y.dtype, device=y.device)
        for k, f in enumerate(self.fields):
            z[f] = y[k * self.NA + self.agg_own]
        return z.reshape(-1)

    def wrap(self, M, A):
        def apply(v):
            zc = self.prolong(torch.linalg.lu_solve(*self.lu, self.restrict(v).reshape(-1, 1)).reshape(-1))
            return zc if M is None else zc + M(v - A(zc))
        return apply


class HybridCompactAMG:
    """camg whose FINE-level smoothing reads the compact 1-hop blocks (P4-mgpu gate 1): ``z = M b`` with
    ``sweeps`` damped block-Jacobi pre-sweeps on ``A1`` (the Warp compact frozen Jacobian + the same shift, fp32),
    ONE residual with the 2-hop matrix ``A2``, the coarse correction of ``amg2`` (camg built on ``A2``: its own
    Galerkin levels 1..), and ``sweeps`` post-sweeps on ``A1``.  The fine level then reads A2 once per application
    instead of about three times.  Field-blocked flat vectors in and out, like ``CoupledAMG.__call__``."""

    def __init__(self, amg2, A1, A2, n: int, F: int, sweeps: int = 2, omega: float = 0.7):
        from Pipeline.CoupledNewton.block_amg import BlockCSR
        self.amg2, self.n, self.F, self.sweeps, self.omega = amg2, int(n), int(F), int(sweeps), float(omega)
        f32 = torch.float32
        self.A1 = BlockCSR(A1.row, A1.col, A1.val.to(f32), A1.shape)
        self.A2 = BlockCSR(A2.row, A2.col, A2.val.to(f32), A2.shape)
        on = A1.row == A1.col
        D = torch.zeros(self.n, F, F, dtype=torch.float64, device=A1.val.device)
        D[A1.row[on]] = A1.val[on].to(torch.float64)
        self.Sinv = torch.linalg.inv(D).to(f32)
        self.V0 = amg2.values[0]

    def _jacobi(self, b, x):
        r = b if x is None else b - self.A1.matvec(x)
        dx = self.omega * torch.einsum("nfg,ng->nf", self.Sinv, r)
        return dx if x is None else x + dx

    def __call__(self, v):
        b = v.reshape(self.F, self.n).t().to(torch.float32).contiguous()
        x = None
        for _ in range(self.sweeps):
            x = self._jacobi(b, x)
        r = b - self.A2.matvec(x)
        V0 = self.V0
        rc = V0.R.matvec(r.to(V0.R.val.dtype))
        e = self.amg2._cycle(1, rc)
        x = x + V0.P.matvec(e).to(torch.float32)
        for _ in range(self.sweeps):
            x = self._jacobi(b, x)
        return x.t().reshape(-1).to(v.dtype)


def _bsr(A, dtype=torch.float32):
    """A ``block_amg.BlockCSR`` as a torch BSR matrix (cuSPARSE bsrmv), rows sorted, int32 indices."""
    order = torch.argsort(A.row * A.shape[1] + A.col)
    row, col, val = A.row[order], A.col[order], A.val[order].to(dtype).contiguous()
    crow = torch.zeros(A.shape[0] + 1, dtype=torch.int64, device=row.device)
    crow[1:] = torch.cumsum(torch.bincount(row, minlength=A.shape[0]), 0)
    F, G = int(val.shape[1]), int(val.shape[2])
    return torch.sparse_bsr_tensor(crow.to(torch.int32), col.to(torch.int32), val,
                                   size=(A.shape[0] * F, A.shape[1] * G))


class HybridCompactFast:
    """``HybridCompactAMG`` on fused fp32 Warp kernels (P4-mgpu gate 1, port): block-Jacobi sweeps on the compact blocks
    and the one 2-hop residual as Warp BSR kernels in fp32, restriction / prolongation as Warp BSR products, and the
    coarse cycle directly on ``amg2`` level-1..K fast block hierarchy (or a standalone ``CoupledAMG`` fallback),
    replayed from a captured CUDA graph."""

    _SIDE_STREAMS: dict = {}

    def __init__(self, amg2, A1, A2, n: int, F: int, sweeps: int = 2, omega: float = 0.7, coarse_kwargs=None,
                 coarse: str = "fast"):
        from Pipeline.CoupledNewton.block_amg import CoupledAMG, COARSE_INVERSE
        self.n, self.F, self.sweeps, self.omega = int(n), int(F), int(sweeps), float(omega)
        f32 = torch.float32
        dev = A1.val.device
        self.dev = dev
        diag_idx = getattr(A1, "_diag", None)
        if diag_idx is not None:
            D = A1.val[diag_idx.long()].to(torch.float64)
        else:
            on = A1.row == A1.col
            D = torch.zeros(self.n, F, F, dtype=torch.float64, device=dev)
            D[A1.row[on]] = A1.val[on].to(torch.float64)
        self.Sinv = torch.linalg.inv(D).to(f32).contiguous()
        V0 = amg2.values[0]
        self.nc = int(V0.R.shape[0])
        self.amg2 = amg2
        self.coarse = coarse
        self._use_warp = dev.type == "cuda"
        self._graph = None
        if self._use_warp:
            import warp as wp
            from Pipeline.CoupledNewton import block_amg_warp as BAW
            COARSE_INVERSE["on"] = True
            self._wp = wp
            self._BAW = BAW
            self._A1_w = BAW.bsr_prepare(A1, f32)
            self._A2_w = BAW.bsr_prepare(A2, f32)
            self._R_w = BAW.bsr_prepare(V0.R, f32)
            self._P_w = BAW.bsr_prepare(V0.P, f32)
            wp_dt = wp.float32
            TM, TV = BAW._mat_t(F, F, wp_dt), BAW._vec_t(F, wp_dt)
            self._TV = TV
            self._Sinv_w = wp.from_torch(self.Sinv, dtype=TM)
            self._k_jac0 = BAW._bsr_jac0_kernel(F, wp_dt)
            self._k_jac = BAW._bsr_jac_kernel(F, wp_dt)
            self._k_res = BAW._bsr_res_kernel(F, wp_dt)
            self._k_mv = BAW._bsr_mv_kernel(F, F, wp_dt)
            self._k_mv_add = BAW._bsr_mv_add_kernel(F, F, wp_dt)
            self._b = torch.zeros(self.n, self.F, dtype=f32, device=dev)
            self._x0 = torch.zeros(self.n, self.F, dtype=f32, device=dev)
            self._x1 = torch.zeros(self.n, self.F, dtype=f32, device=dev)
            self._r = torch.zeros(self.n, self.F, dtype=f32, device=dev)
            self._rc = torch.zeros(self.nc, self.F, dtype=f32, device=dev)
            self._e32 = torch.zeros(self.nc, self.F, dtype=f32, device=dev)
            self._b_w = wp.from_torch(self._b, dtype=TV)
            self._x0_w = wp.from_torch(self._x0, dtype=TV)
            self._x1_w = wp.from_torch(self._x1, dtype=TV)
            self._r_w = wp.from_torch(self._r, dtype=TV)
            self._rc_w = wp.from_torch(self._rc, dtype=TV)
            self._e32_w = wp.from_torch(self._e32, dtype=TV)
        else:
            self.A1, self.A2 = _bsr(A1), _bsr(A2)
            self.R, self.P = _bsr(V0.R), _bsr(V0.P)
        fl = getattr(amg2, "_fast", None)
        self._lay1 = None
        self.amgc = None
        if coarse == "fast":
            if fl is not None and len(fl) > 1 and fl[1].blk is not None:
                self._lay1 = fl[1].blk.layout
            else:
                kw = dict(coarse_kwargs or {})
                kw.pop("skip_fine_fast", None)
                self.amgc = CoupledAMG(amg2.values[1].A, **kw)

    def _jac(self, b, x):
        r = b if x is None else b - (self.A1 @ x.reshape(-1, 1)).reshape(self.n, self.F)
        dx = self.omega * torch.einsum("nfg,ng->nf", self.Sinv, r)
        return dx if x is None else x + dx

    def _warp_step(self):
        wp = self._wp
        BAW = self._BAW
        dev = self.dev
        bw = self._b_w
        om = wp.float32(self.omega)
        x_cur, x_nxt = self._x0, self._x1
        xw_cur, xw_nxt = self._x0_w, self._x1_w
        if self.sweeps > 0:
            BAW._launch(dev, self._k_jac0, self.n, [self._Sinv_w, bw, om, xw_cur])
            for _ in range(self.sweeps - 1):
                BAW._launch(dev, self._k_jac, self.n,
                            [self._A1_w[0], self._A1_w[1], self._A1_w[2], self._Sinv_w, bw, xw_cur, om, xw_nxt])
                x_cur, x_nxt = x_nxt, x_cur
                xw_cur, xw_nxt = xw_nxt, xw_cur
        else:
            x_cur.zero_()
        BAW._launch(dev, self._k_res, self.n,
                    [self._A2_w[0], self._A2_w[1], self._A2_w[2], bw, xw_cur, self._r_w])
        BAW._launch(dev, self._k_mv, self.nc,
                    [self._R_w[0], self._R_w[1], self._R_w[2], self._r_w, self._rc_w])
        rc = self._rc
        if self.coarse == "fast":
            if self._lay1 is not None:
                e = self._lay1.from_layout(
                    self.amg2._blk_cycle(self.amg2._fast, 1,
                                         self._lay1.to_layout(rc.to(torch.float64).reshape(-1), "ci")),
                    "ci").reshape(self.nc, self.F)
            else:
                e = self.amgc(rc.t().reshape(-1).to(torch.float64)).reshape(self.F, self.nc).t()
        else:
            e = self.amg2._cycle(1, rc.to(torch.float64))
        self._e32.copy_(e)
        BAW._launch(dev, self._k_mv_add, self.n,
                    [self._P_w[0], self._P_w[1], self._P_w[2], self._e32_w, xw_cur])
        for _ in range(self.sweeps):
            BAW._launch(dev, self._k_jac, self.n,
                        [self._A1_w[0], self._A1_w[1], self._A1_w[2], self._Sinv_w, bw, xw_cur, om, xw_nxt])
            x_cur, x_nxt = x_nxt, x_cur
            xw_cur, xw_nxt = xw_nxt, xw_cur
        self._out = x_cur

    def __call__(self, v):
        if self._use_warp:
            self._b.copy_(v.reshape(self.F, self.n).t())
            if self._graph is None and self.coarse == "fast" and self._lay1 is not None:
                dev = self.dev
                ss = self._SIDE_STREAMS.get(dev)
                if ss is None:
                    ss = self._SIDE_STREAMS[dev] = torch.cuda.Stream(device=dev)
                ts = torch.cuda.current_stream(dev)
                ss.wait_stream(ts)
                with torch.cuda.stream(ss):
                    self._warp_step()
                    g = torch.cuda.CUDAGraph()
                    with torch.cuda.graph(g, stream=ss):
                        self._warp_step()
                ts.wait_stream(ss)
                from Parallel.part import _register_captured_graph
                self._graph = _register_captured_graph(g)
            elif self._graph is not None:
                self._graph.replay()
            else:
                self._warp_step()
            return self._out.t().reshape(-1).to(v.dtype)
        b = v.reshape(self.F, self.n).t().to(torch.float32).contiguous()
        x = None
        for _ in range(self.sweeps):
            x = self._jac(b, x)
        r = b - (self.A2 @ x.reshape(-1, 1)).reshape(self.n, self.F)
        rc = (self.R @ r.reshape(-1, 1)).reshape(self.nc, self.F)
        if self.coarse == "fast":                     # CoupledAMG.__call__ takes field-blocked flat vectors
            e = self.amgc(rc.t().reshape(-1).to(torch.float64)).reshape(self.F, self.nc).t()
        else:
            e = self.amg2._cycle(1, rc.to(torch.float64))
        x = x + (self.P @ e.to(torch.float32).contiguous().reshape(-1, 1)).reshape(self.n, self.F)
        for _ in range(self.sweeps):
            x = self._jac(b, x)
        return x.t().reshape(-1).to(v.dtype)


def _diag_slots(J):
    from Pipeline.CoupledNewton.newton import _csr_diagonal_slots
    return _csr_diagonal_slots(J)


def _own_diagonal(problem, J_own):
    """``diag(J)`` on the own rows (field-blocked own layout) from the own-row CSR (columns are local)."""
    crow, col, val = J_own.crow_indices(), J_own.col_indices(), J_own.values()
    rows = torch.repeat_interleave(torch.arange(J_own.shape[0], device=val.device), crow[1:] - crow[:-1])
    target = problem.own_rows.index_select(0, rows)
    hit = col == target
    out = torch.zeros(J_own.shape[0], dtype=val.dtype, device=val.device)
    out.index_add_(0, rows[hit], val[hit])
    return out


def _shift_blocks(problem, blocks, J_own, d_own):
    """``blocks`` and ``J_own`` with ``diag(d)`` added (``newton._reshift`` on the part, the own-row copy too);
    None if a needed diagonal slot is missing."""
    from Pipeline.CoupledNewton.newton import _reshift
    d_loc = problem.extend(d_own)
    shifted = _reshift(problem.local, blocks, d_loc)
    if shifted is None:
        return None, None
    crow, col, val = J_own.crow_indices(), J_own.col_indices(), J_own.values()
    rows = torch.repeat_interleave(torch.arange(J_own.shape[0], device=val.device), crow[1:] - crow[:-1])
    hit = (col == problem.own_rows.index_select(0, rows)).nonzero(as_tuple=True)[0]
    val = val.clone()
    val.index_add_(0, hit, d_own.index_select(0, rows.index_select(0, hit)))
    return shifted, torch.sparse_csr_tensor(crow, col, val, size=J_own.shape)


# ----------------------------------------------------------------------------------------- stop rules
def residual_control(problem, thresholds: dict, norm: str = "max"):
    """An OpenFOAM ``residualControl``-like stop rule: stop when every listed field's global residual (``max``
    or ``l2`` over all parts) is below its threshold.  Collective by construction."""
    def rule(x, r, it):
        norms = problem.field_norms(r)
        k = 0 if norm == "max" else 1
        return all(norms[name][k] < float(t) for name, t in thresholds.items())
    return rule


# ------------------------------------------------------------------------------------------------ Newton
def solve(problem, *, x0=None, tol=1e-12, maxit=30, rtol=1e-8, restart=200, max_cycles=4, forcing=True,
          max_backtracks=8, operator="assembled", coefficient=None, precond="camg", precond_kw=None,
          ptc=False, ptc_theta0=0.2, ptc_growth=2.0, ptc_handover=1e-4, ptc_max_forced_growth=None,
          coarse=False, stop_rule=None, progress=None, log=None):
    """Damped partitioned Newton to ``max|r| < tol``; returns ``(x_own, history)`` (identical on every rank,
    apart from timings)."""
    from Pipeline.CoupledNewton.newton import inner_forcing
    say = (lambda s: None) if log is None else log
    precond_kw = dict(precond_kw or {})
    x = torch.zeros(problem.n_unknowns, dtype=problem.dtype, device=problem.device) if x0 is None else x0.clone()
    if problem._gauge_free is None:
        problem.resolve_gauge(x)
    x = problem.gauge(x)
    problem.arm_shift(None, None)
    t0 = time.perf_counter()
    residual = problem.residual(x)
    previous = problem.norm(residual)
    history = [{"it": 0, "max_abs": problem.max_abs(residual), "l2": previous, "seconds": 0.0}]
    if progress is not None:
        progress(history[-1])
    ptc_active = bool(ptc)
    theta = theta_target = float(ptc_theta0) if ptc_active else 0.0
    base = None
    l2_start = max(previous, 1e-300)
    forced = 0
    try:
        for iteration in range(1, int(maxit) + 1):
            if history[-1]["max_abs"] < tol:
                break
            if stop_rule is not None and problem.dist.all_true(bool(stop_rule(x, residual, iteration - 1))):
                history[-1]["stop_rule"] = True
                break
            event = None
            if ptc_active and previous / l2_start < ptc_handover:
                ptc_active, theta, theta_target = False, 0.0, 0.0
                problem.arm_shift(None, None)
                event = "handover"
            ts = time.perf_counter()
            # --- linearise at x (the shift, when armed, is in the residual and hence in J)
            if ptc_active and base is not None:
                theta = theta_target
                problem.arm_shift(theta * base, x)
            blocks, J_own, x_loc = problem.assemble(x, coefficient=coefficient)
            if ptc_active and base is None:
                diag = _own_diagonal(problem, J_own)
                base = diag.abs()
                base[problem.velocity:problem.pressure] = 0.0       # continuity stays algebraic
                theta = theta_target
                d = theta * base
                blocks, J_own = _shift_blocks(problem, blocks, J_own, d)
                if blocks is None:
                    raise RuntimeError("PTC: a differential row has no stored diagonal")
                problem.arm_shift(d, x)
            t_asm = time.perf_counter() - ts
            ts = time.perf_counter()
            M = restricted_additive_schwarz(problem, local_preconditioner(problem, blocks, precond, **precond_kw))
            t_pc = time.perf_counter() - ts
            if operator == "assembled":
                A = problem.assembled_operator(J_own)
            elif operator == "jvp":
                A = problem.linearise_forward(x, coefficient=coefficient)
            else:
                raise ValueError(f"operator must be 'assembled' or 'jvp', not {operator!r}")
            del blocks
            if coarse and problem.dist.world > 1:
                ts = time.perf_counter()
                M = CoarseSpace(problem, A).wrap(M)
                t_pc += time.perf_counter() - ts
            requested, _ = inner_forcing(history, rtol, forcing=forcing)
            ts = time.perf_counter()
            step, kinfo = fgmres(A, -residual, problem.dist, M=M, rtol=requested, restart=restart,
                                 max_cycles=max_cycles)
            t_kry = time.perf_counter() - ts
            if problem._gauge_free:
                step = problem.gauge(step) - problem.gauge(torch.zeros_like(step))
            # --- line search on the (shifted) merit; at x the shift term is zero, so ``previous`` is its value
            lam, accepted, trial = 1.0, False, None
            ts = time.perf_counter()
            for _ in range(int(max_backtracks)):
                candidate = problem.gauge(x + lam * step)
                trial = problem.residual(candidate)
                trial_norm = problem.norm(trial)
                if trial_norm < previous:
                    accepted = True
                    break
                lam *= 0.5
            if not accepted and ptc_active:
                cap = max(1.0, ptc_theta0) * (previous / l2_start) ** 0.5
                if theta_target < cap * 0.999:
                    theta_target = min(theta_target * ptc_growth ** 2, cap)
                    event = "cutback"
                elif math.isfinite(trial_norm):
                    lam *= 2.0
                    steady_norm = problem.norm(trial - problem.restrict(problem.local._ptc_shift[0])
                                               * (candidate - x))
                    if ptc_max_forced_growth is not None and steady_norm > ptc_max_forced_growth * previous:
                        # the serial ``ptc_max_forced_growth`` guard; here the refused step also shrinks the
                        # pseudo-timestep past the ceiling, so the next attempt is more damped
                        theta_target = theta_target * ptc_growth ** 2
                        event = "forced_rejected"
                    else:
                        accepted, forced, event = True, forced + 1, "forced"
            t_ls = time.perf_counter() - ts
            if accepted:
                if ptc_active:
                    old = previous
                    steady = trial - (problem.local._ptc_shift[0].reshape(problem.F, problem.n_loc)
                                      [:, problem.own].reshape(-1) * (candidate - x))
                    x, residual, previous = candidate, steady, problem.norm(steady)
                    if event != "forced":
                        forced = 0
                    ratio = previous / max(old, 1e-300)
                    factor = min(max(ratio, 1.0 / ptc_growth), ptc_growth)
                    cap = max(1.0, ptc_theta0) * (previous / l2_start) ** 0.5
                    theta_target = min(theta_target * factor, cap)
                    problem.arm_shift(None, None)
                else:
                    x, residual, previous = candidate, trial, trial_norm
            else:
                problem.arm_shift(None, None)
            row = {"it": iteration, "max_abs": problem.max_abs(residual), "l2": previous, "lambda": lam,
                   "accepted": accepted, "krylov": kinfo["iterations"], "inner_rel": kinfo["rel"],
                   "inner_rtol": requested, "theta": theta if ptc_active or event == "handover" else 0.0,
                   "event": event, "t_assemble": t_asm, "t_precond": t_pc, "t_krylov": t_kry,
                   "t_line_search": t_ls, "seconds": time.perf_counter() - t0}
            history.append(row)
            say(f"newton {iteration:3d}: max|r| {row['max_abs']:.3e}  ||r|| {row['l2']:.3e}  lam {lam:.3g}  "
                f"krylov {kinfo['iterations']} (rel {kinfo['rel']:.1e} / {requested:.1e})"
                + (f"  theta {theta:.3g}" if ptc_active else "") + (f"  [{event}]" if event else "")
                + f"  asm {t_asm:.2f}s pc {t_pc:.2f}s kry {t_kry:.2f}s ls {t_ls:.2f}s")
            if progress is not None:
                progress(row)
            if not accepted and not ptc_active:
                history[-1]["refused"] = True
                break
            if forced > 10:
                history[-1]["refused"] = True
                break
    finally:
        problem.arm_shift(None, None)
    return x, history
