"""Warp residual, exact sparse Jacobian and batched Newton solve of the zonal mesh network (``zonal_network``).

A batch is ``B`` states of one network, ``X: (B, N, 2)`` = (p_rgh, T) per zone, with boundary values per item
(``ZonalBoundaryValues`` rows): the time steps of a window or the snapshots of a quasi-steady collection are solved
and differentiated in one launch per kernel.

Kernels (float64, no atomics: every cell sum is a gather over a CSR of the cell's contributions, so the residual is
bitwise reproducible):

* ``_link_kernel`` (B x L): the flow ``Q`` and enthalpy flux ``E`` of every link (orifice or porous-baffle law,
  Boussinesq stack term, smoothed upwind, exchange flow) and their six partial derivatives;
* ``_rack_kernel`` (B x racks): the CBBM intake mean temperature of every server;
* ``_inflow_kernel`` / ``_outflow_kernel`` / ``_opening_kernel``: the boundary conditions per (zone, patch) item
  (imposed flows with fixed / CBBM / zero-gradient inflow temperature, the pressure openings);
* ``_cell_kernel`` (B x N): the volume and enthalpy rows (the gauge row ``p_0`` without an opening) plus the time
  term ``M (X - X_prev) / dt`` of a backward-Euler step.

The Jacobian ``dR/dX`` has a fixed pattern (16 entries per link, the boundary entries, the CBBM rows); its values are
written per batch item and summed into the unique pattern once, so ``jvp``/``vjp`` are sparse products of a
block-diagonal (B x 2N) matrix and the linear solves are one sparse LU per item (SciPy SuperLU, on the host: an L0
network has a few thousand zones). ``solve`` is the zonal.py strategy, batched: isothermal Picard pressures, damped
Newton with an Armijo backtrack per item, pseudo-transient continuation (the zones' heat capacity) for the items
Newton does not converge.

Exactness against the torch reference is tested in ``tests/test_zonal_network.py``.
"""
from __future__ import annotations

import math
import time

import numpy as np
import torch
import warp as wp

from FVsolver.applications.zonal_network import ZonalBoundaryValues, ZonalMesh

wp.config.quiet = True
wp.set_module_options({"enable_backward": False, "fuse_fp": False})

F64 = wp.float64
SQRT2 = wp.constant(wp.float64(math.sqrt(2.0)))


@wp.kernel
def _link_kernel(X: wp.array3d(dtype=F64), li: wp.array(dtype=wp.int32), lj: wp.array(dtype=wp.int32),
                 area: wp.array(dtype=F64), ghf: wp.array(dtype=F64), porous: wp.array(dtype=wp.int32),
                 pa: wp.array(dtype=F64), pb: wp.array2d(dtype=F64), mixon: wp.array(dtype=wp.int32),
                 beta: F64, cd: F64, delta: F64, eps: F64, mixc: F64,
                 Q: wp.array2d(dtype=F64), E: wp.array2d(dtype=F64), dJ: wp.array3d(dtype=F64)):
    b, l = wp.tid()
    i = li[l]
    j = lj[l]
    pi = X[b, i, 0]
    Ti = X[b, i, 1]
    pj = X[b, j, 0]
    Tj = X[b, j, 1]
    A = area[l]
    D = pi - pj - ghf[l] * beta * (Ti - Tj)
    dDdTi = -ghf[l] * beta
    dDdTj = ghf[l] * beta
    q = F64(0.0)
    dqdD = F64(0.0)
    if porous[l] != 0:
        m = wp.sqrt(D * D + delta * delta)
        a = pa[l]
        bb = pb[b, l]
        r = wp.sqrt(a * a + F64(4.0) * bb * m)
        den = a + r
        q = A * F64(2.0) * D / den
        dr = F64(2.0) * bb * (D / m) / r
        dqdD = A * (F64(2.0) / den - F64(2.0) * D * dr / (den * den))
    else:
        s = D * D + delta * delta
        c = cd * A * SQRT2
        q = c * D * wp.pow(s, F64(-0.25))
        dqdD = c * wp.pow(s, F64(-1.25)) * (F64(0.5) * D * D + delta * delta)
    sq = wp.sqrt(q * q + eps * eps)
    qp = F64(0.5) * (sq + q)
    qm = qp - q
    dqp = F64(0.5) * (q / sq + F64(1.0))
    dqm = dqp - F64(1.0)
    M = F64(0.0)
    if mixon[l] != 0:
        M = mixc * A
    Q[b, l] = q
    E[b, l] = qp * Ti - qm * Tj + M * (Ti - Tj)
    dEdQ = dqp * Ti - dqm * Tj
    dJ[b, l, 0] = dqdD                       # dQ/dp_i   (dQ/dp_j = -dQ/dp_i)
    dJ[b, l, 1] = dqdD * dDdTi               # dQ/dT_i
    dJ[b, l, 2] = dqdD * dDdTj               # dQ/dT_j
    dJ[b, l, 3] = dEdQ * dqdD                # dE/dp_i   (dE/dp_j = -dE/dp_i)
    dJ[b, l, 4] = qp + M + dEdQ * dqdD * dDdTi
    dJ[b, l, 5] = -qm - M + dEdQ * dqdD * dDdTj


@wp.kernel
def _rack_kernel(X: wp.array3d(dtype=F64), ptr: wp.array(dtype=wp.int32), cell: wp.array(dtype=wp.int32),
                 share: wp.array(dtype=F64), Tm: wp.array2d(dtype=F64)):
    b, r = wp.tid()
    s = F64(0.0)
    for k in range(ptr[r], ptr[r + 1]):
        s += share[k] * X[b, cell[k], 1]
    Tm[b, r] = s


@wp.kernel
def _inflow_kernel(X: wp.array3d(dtype=F64), cell: wp.array(dtype=wp.int32), patch: wp.array(dtype=wp.int32),
                   share: wp.array(dtype=F64), mode: wp.array(dtype=wp.int32), index: wp.array(dtype=wp.int32),
                   flow: wp.array2d(dtype=F64), t_fixed: wp.array2d(dtype=F64), delta_t: wp.array2d(dtype=F64),
                   Tm: wp.array2d(dtype=F64), qin: wp.array2d(dtype=F64), ein: wp.array2d(dtype=F64)):
    b, k = wp.tid()
    q = flow[b, patch[k]] * share[k]
    t = X[b, cell[k], 1]
    if mode[k] == 0:
        t = t_fixed[b, index[k]]
    elif mode[k] == 1:
        t = Tm[b, index[k]] + delta_t[b, index[k]]
    qin[b, k] = q
    ein[b, k] = q * t


@wp.kernel
def _outflow_kernel(X: wp.array3d(dtype=F64), cell: wp.array(dtype=wp.int32), patch: wp.array(dtype=wp.int32),
                    share: wp.array(dtype=F64), flow: wp.array2d(dtype=F64),
                    qout: wp.array2d(dtype=F64), eout: wp.array2d(dtype=F64)):
    b, k = wp.tid()
    q = flow[b, patch[k]] * share[k]
    qout[b, k] = q
    eout[b, k] = q * X[b, cell[k], 1]


@wp.kernel
def _opening_kernel(X: wp.array3d(dtype=F64), cell: wp.array(dtype=wp.int32), index: wp.array(dtype=wp.int32),
                    area: wp.array(dtype=F64), tcol: wp.array(dtype=wp.int32), p_ref: wp.array2d(dtype=F64),
                    t_fixed: wp.array2d(dtype=F64), cd: F64, delta: F64, eps: F64,
                    qo: wp.array2d(dtype=F64), eo: wp.array2d(dtype=F64), dJ: wp.array3d(dtype=F64)):
    b, k = wp.tid()
    c = cell[k]
    D = X[b, c, 0] - p_ref[b, index[k]]
    s = D * D + delta * delta
    co = cd * area[k] * SQRT2
    q = co * D * wp.pow(s, F64(-0.25))
    dqdD = co * wp.pow(s, F64(-1.25)) * (F64(0.5) * D * D + delta * delta)
    sq = wp.sqrt(q * q + eps * eps)
    qp = F64(0.5) * (sq + q)
    dqp = F64(0.5) * (q / sq + F64(1.0))
    Tc = X[b, c, 1]
    tb = Tc
    if tcol[k] >= 0:
        tb = t_fixed[b, tcol[k]]
    qo[b, k] = q
    eo[b, k] = qp * Tc - (qp - q) * tb
    dEdq = dqp * Tc - (dqp - F64(1.0)) * tb
    dJ[b, k, 0] = dqdD
    dJ[b, k, 1] = dEdq * dqdD
    if tcol[k] >= 0:
        dJ[b, k, 2] = qp
    else:
        dJ[b, k, 2] = q


@wp.kernel
def _cell_kernel(X: wp.array3d(dtype=F64), Xp: wp.array3d(dtype=F64), mcap: wp.array(dtype=F64),
                 inv_dt: wp.array(dtype=F64),
                 lptr: wp.array(dtype=wp.int32), lidx: wp.array(dtype=wp.int32), lsgn: wp.array(dtype=F64),
                 Q: wp.array2d(dtype=F64), E: wp.array2d(dtype=F64),
                 iptr: wp.array(dtype=wp.int32), iidx: wp.array(dtype=wp.int32),
                 qin: wp.array2d(dtype=F64), ein: wp.array2d(dtype=F64),
                 optr: wp.array(dtype=wp.int32), oidx: wp.array(dtype=wp.int32),
                 qout: wp.array2d(dtype=F64), eout: wp.array2d(dtype=F64),
                 pptr: wp.array(dtype=wp.int32), pidx: wp.array(dtype=wp.int32),
                 qo: wp.array2d(dtype=F64), eo: wp.array2d(dtype=F64),
                 gauge: wp.int32, R: wp.array3d(dtype=F64)):
    b, c = wp.tid()
    m = F64(0.0)
    h = F64(0.0)
    for k in range(lptr[c], lptr[c + 1]):
        m += lsgn[k] * Q[b, lidx[k]]
        h += lsgn[k] * E[b, lidx[k]]
    for k in range(iptr[c], iptr[c + 1]):
        m -= qin[b, iidx[k]]
        h -= ein[b, iidx[k]]
    for k in range(optr[c], optr[c + 1]):
        m += qout[b, oidx[k]]
        h += eout[b, oidx[k]]
    for k in range(pptr[c], pptr[c + 1]):
        m += qo[b, pidx[k]]
        h += eo[b, pidx[k]]
    idt = inv_dt[b]
    if idt > F64(0.0):
        h += mcap[c] * idt * (X[b, c, 1] - Xp[b, c, 1])
    if gauge != 0 and c == 0:
        m = X[b, 0, 0]
    R[b, c, 0] = m
    R[b, c, 1] = h


def _csr(rows: np.ndarray, n: int, sign: np.ndarray | None = None):
    """CSR (ptr, index into the item list, sign) of items grouped by row."""
    order = np.argsort(rows, kind="stable")
    ptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(ptr, rows + 1, 1)
    ptr = np.cumsum(ptr)
    return ptr.astype(np.int32), order.astype(np.int32), (None if sign is None else sign[order])


class WarpZonal:
    """The zonal network ``net`` on ``device`` (a CUDA device for the kernels; the LU solves run on the host)."""

    def __init__(self, net: ZonalMesh, device: str | torch.device | None = None):
        self.net = net
        wp.init()
        dev = torch.device(device) if device is not None else net.device
        if dev.type == "cuda" and dev.index is None:
            dev = torch.device("cuda", torch.cuda.current_device())
        self.device = dev
        self.wdev = wp.device_from_torch(dev)
        N, L = net.n_cells, net.n_links
        self.N, self.L = N, L
        np_ = lambda t: t.detach().cpu().numpy()  # noqa: E731
        li, lj = np_(net.link_i), np_(net.link_j)
        # CSR of the links of every cell, +1 on its i side, -1 on its j side
        rows = np.concatenate((li, lj))
        sgn = np.concatenate((np.ones(L), -np.ones(L)))
        item = np.concatenate((np.arange(L), np.arange(L)))
        lptr, order, lsg = _csr(rows, N, sgn)
        self._lptr, self._lidx, self._lsgn = lptr, item[order].astype(np.int32), lsg
        self._iptr, self._iidx, _ = _csr(np_(net.in_cell), N)
        self._optr, self._oidx, _ = _csr(np_(net.out_cell), N)
        self._pptr, self._pidx, _ = _csr(np_(net.pref_cell), N)
        rk = np_(net.rack_of)
        self._rptr, rorder, _ = _csr(rk, len(net.racks))
        self._rcell = np_(net.rack_cell)[rorder].astype(np.int32)
        self._rshare = np_(net.rack_share)[rorder]
        self._build_pattern()
        dev = self.device
        to_w = lambda a, dt: wp.from_torch(torch.as_tensor(a, device=dev).contiguous(), dtype=dt)  # noqa: E731
        self.w = {
            "li": to_w(li.astype(np.int32), wp.int32), "lj": to_w(lj.astype(np.int32), wp.int32),
            "area": to_w(np_(net.link_area), F64), "ghf": to_w(np_(net.link_ghf), F64),
            "porous": to_w(np_(net.link_porous).astype(np.int32), wp.int32),
            "pa": to_w(np_(net.link_a), F64), "pb": to_w(np_(net.link_b), F64),
            "mixon": to_w(np_(net.link_mix).astype(np.int32), wp.int32),
            "lptr": to_w(self._lptr, wp.int32), "lidx": to_w(self._lidx, wp.int32), "lsgn": to_w(self._lsgn, F64),
            "iptr": to_w(self._iptr, wp.int32), "iidx": to_w(self._iidx, wp.int32),
            "optr": to_w(self._optr, wp.int32), "oidx": to_w(self._oidx, wp.int32),
            "pptr": to_w(self._pptr, wp.int32), "pidx": to_w(self._pidx, wp.int32),
            "rptr": to_w(self._rptr, wp.int32), "rcell": to_w(self._rcell, wp.int32),
            "rshare": to_w(self._rshare, F64),
            "in_cell": to_w(np_(net.in_cell).astype(np.int32), wp.int32),
            "in_patch": to_w(np_(net.in_patch).astype(np.int32), wp.int32),
            "in_share": to_w(np_(net.in_share), F64), "in_mode": to_w(np_(net.in_mode).astype(np.int32), wp.int32),
            "in_index": to_w(np_(net.in_index).astype(np.int32), wp.int32),
            "out_cell": to_w(np_(net.out_cell).astype(np.int32), wp.int32),
            "out_patch": to_w(np_(net.out_patch).astype(np.int32), wp.int32),
            "out_share": to_w(np_(net.out_share), F64),
            "pref_cell": to_w(np_(net.pref_cell).astype(np.int32), wp.int32),
            "pref_index": to_w(np_(net.pref_index).astype(np.int32), wp.int32),
            "pref_area": to_w(np_(net.pref_area), F64),
            "pref_tcol": to_w(np_(net.pref_t_fixed).astype(np.int32), wp.int32),
            "mcap": to_w(np_(net.mass_vector()[:, 1]), F64),
        }

    # ------------------------------------------------------------------ the Jacobian pattern
    def _build_pattern(self):
        net = self.net
        N = self.N
        np_ = lambda t: t.detach().cpu().numpy()  # noqa: E731
        li, lj = np_(net.link_i), np_(net.link_j)
        P = lambda c: 2 * c          # noqa: E731
        T = lambda c: 2 * c + 1      # noqa: E731
        # links: rows (mass_i, mass_j, en_i, en_j) x cols (p_i, T_i, p_j, T_j)
        r_l = np.stack([P(li)] * 4 + [P(lj)] * 4 + [T(li)] * 4 + [T(lj)] * 4, 1)
        c4 = [P(li), T(li), P(lj), T(lj)]
        c_l = np.stack(c4 * 4, 1)
        rows, cols = [r_l.reshape(-1)], [c_l.reshape(-1)]
        # inflow energy rows: zero-gradient (own T) and CBBM (the rack's intake zones)
        ic, im, ii = np_(net.in_cell), np_(net.in_mode), np_(net.in_index)
        zg = np.nonzero(im == 2)[0]
        rows.append(T(ic[zg])); cols.append(T(ic[zg]))
        cb = np.nonzero(im == 1)[0]
        cnt = (self._rptr[ii[cb] + 1] - self._rptr[ii[cb]]).astype(np.int64)
        cb_item = np.repeat(cb, cnt)
        cb_member = np.concatenate([np.arange(self._rptr[r], self._rptr[r + 1]) for r in ii[cb]]) if len(cb) \
            else np.zeros(0, np.int64)
        rows.append(T(ic[cb_item])); cols.append(T(self._rcell[cb_member]))
        oc = np_(net.out_cell)
        rows.append(T(oc)); cols.append(T(oc))
        pc = np_(net.pref_cell)
        rows += [P(pc), T(pc), T(pc)]; cols += [P(pc), P(pc), T(pc)]
        # time term on the energy diagonal, and the gauge
        rows.append(T(np.arange(N))); cols.append(T(np.arange(N)))
        rows.append(np.array([0])); cols.append(np.array([0]))
        rows, cols = np.concatenate(rows).astype(np.int64), np.concatenate(cols).astype(np.int64)
        key = rows * (2 * N) + cols
        uniq, inv = np.unique(key, return_inverse=True)
        self._n_link_vals = 16 * self.L
        self._cb_item, self._cb_member, self._zg = cb_item, cb_member, zg
        self._inv = torch.as_tensor(inv.reshape(-1), device=self.device)
        self._nnz = len(uniq)
        ur, uc = uniq // (2 * N), uniq % (2 * N)
        self._ur, self._uc = ur, uc
        rowptr = np.zeros(2 * N + 1, dtype=np.int64)
        np.add.at(rowptr, ur + 1, 1)
        self._rowptr = np.cumsum(rowptr)
        # gauge: the volume row of zone 0 is p_0 (its link entries are dropped)
        self._gauge_drop = torch.as_tensor(rows == 0, device=self.device) if net.gauge else None
        self._raw_rows = rows

    # ------------------------------------------------------------------ evaluation
    def _bv(self, bv: ZonalBoundaryValues, B: int) -> ZonalBoundaryValues:
        bv = bv.to(self.device)
        if bv.flow.shape[0] != B:
            bv = bv.expand(B)
        return bv

    def _wp2(self, t: torch.Tensor):
        if t.shape[1] == 0:                  # warp needs a non-empty buffer to bind
            t = torch.zeros(t.shape[0], 1, dtype=torch.float64, device=self.device)
        return wp.from_torch(t.contiguous(), dtype=F64)

    def evaluate(self, X: torch.Tensor, bv: ZonalBoundaryValues, *, X_prev: torch.Tensor | None = None,
                 dt: float | None = None, cd: float | None = None, mixing: float | None = None,
                 jacobian: bool = False):
        """Residual (B, N, 2) of states X (B, N, 2); with ``jacobian`` also the values (B, nnz) of dR/dX on the
        pattern. ``X_prev``/``dt``: the backward-Euler time term (``X_prev`` (B, N, 2), the state before each;
        ``dt`` one step for all items or one per item (B,), ``inf`` for a steady item)."""
        c = self.net.coef
        X = X.to(self.device, torch.float64).contiguous()
        B = X.shape[0]
        bv = self._bv(bv, B)
        cd = c.discharge_coefficient if cd is None else float(cd)
        mix = c.mixing_m3s_per_m2 if mixing is None else float(mixing)
        delta = c.pressure_smoothing_pa / c.rho
        eps = c.thermal_flow_smoothing_m3s
        dev, wdev = self.device, self.wdev
        z = lambda *s: torch.zeros(*s, dtype=torch.float64, device=dev)  # noqa: E731
        L, N = self.L, self.N
        Ki, Ko, Kp, nr = (int(self.net.in_cell.numel()), int(self.net.out_cell.numel()),
                          int(self.net.pref_cell.numel()), len(self.net.racks))
        Q, E, dJ = z(B, max(L, 1)), z(B, max(L, 1)), z(B, max(L, 1), 6)
        Tm = z(B, max(nr, 1))
        qin, ein, qout, eout = z(B, max(Ki, 1)), z(B, max(Ki, 1)), z(B, max(Ko, 1)), z(B, max(Ko, 1))
        qo, eo, dJo = z(B, max(Kp, 1)), z(B, max(Kp, 1)), z(B, max(Kp, 1), 3)
        R = z(B, N, 2)
        wX = wp.from_torch(X, dtype=F64)
        w = self.w
        fw = lambda t: wp.from_torch(t, dtype=F64)  # noqa: E731
        if L:
            wp.launch(_link_kernel, dim=(B, L), device=wdev,
                      inputs=[wX, w["li"], w["lj"], w["area"], w["ghf"], w["porous"], w["pa"],
                              wp.from_torch(self.net.porous_b(bv, B).contiguous(), dtype=F64), w["mixon"],
                              F64(self.net.beta), F64(cd), F64(delta), F64(eps), F64(mix)],
                      outputs=[fw(Q), fw(E), fw(dJ)])
        if nr:
            wp.launch(_rack_kernel, dim=(B, nr), device=wdev, inputs=[wX, w["rptr"], w["rcell"], w["rshare"]],
                      outputs=[fw(Tm)])
        if Ki:
            wp.launch(_inflow_kernel, dim=(B, Ki), device=wdev,
                      inputs=[wX, w["in_cell"], w["in_patch"], w["in_share"], w["in_mode"], w["in_index"],
                              self._wp2(bv.flow), self._wp2(bv.t_fixed), self._wp2(bv.delta_t), fw(Tm)],
                      outputs=[fw(qin), fw(ein)])
        if Ko:
            wp.launch(_outflow_kernel, dim=(B, Ko), device=wdev,
                      inputs=[wX, w["out_cell"], w["out_patch"], w["out_share"], self._wp2(bv.flow)],
                      outputs=[fw(qout), fw(eout)])
        if Kp:
            wp.launch(_opening_kernel, dim=(B, Kp), device=wdev,
                      inputs=[wX, w["pref_cell"], w["pref_index"], w["pref_area"], w["pref_tcol"],
                              self._wp2(bv.p_ref), self._wp2(bv.t_fixed), F64(cd), F64(delta), F64(eps)],
                      outputs=[fw(qo), fw(eo), fw(dJo)])
        inv_dt = self._inv_dt(dt, X_prev, B)
        Xp = X if X_prev is None else X_prev.to(dev, torch.float64).expand_as(X).contiguous()
        wp.launch(_cell_kernel, dim=(B, N), device=wdev,
                  inputs=[wX, wp.from_torch(Xp, dtype=F64), w["mcap"], wp.from_torch(inv_dt, dtype=F64),
                          w["lptr"], w["lidx"], w["lsgn"], fw(Q), fw(E),
                          w["iptr"], w["iidx"], fw(qin), fw(ein), w["optr"], w["oidx"], fw(qout), fw(eout),
                          w["pptr"], w["pidx"], fw(qo), fw(eo), wp.int32(1 if self.net.gauge else 0)],
                  outputs=[wp.from_torch(R, dtype=F64)])
        if not jacobian:
            return R
        return R, self._values(B, dJ[:, :L], qin[:, :Ki], qout[:, :Ko], dJo[:, :Kp], bv, inv_dt)

    def _inv_dt(self, dt, X_prev, B) -> torch.Tensor:
        """1 / dt per item (B,): 0 without a time term (no ``X_prev``, ``dt`` None or inf)."""
        if dt is None or X_prev is None:
            return torch.zeros(B, dtype=torch.float64, device=self.device)
        d = torch.as_tensor(dt, dtype=torch.float64, device=self.device).reshape(-1).expand(B)
        return torch.where(torch.isfinite(d), 1.0 / d, torch.zeros_like(d)).contiguous()

    def _values(self, B, dJ, qin, qout, dJo, bv, inv_dt):
        net = self.net
        a, t_i, t_j, ep, eti, etj = (dJ[..., k] for k in range(6))
        # rows (mass_i, mass_j, en_i, en_j) x cols (p_i, T_i, p_j, T_j)
        link = torch.stack([a, t_i, -a, t_j,
                            -a, -t_i, a, -t_j,
                            ep, eti, -ep, etj,
                            -ep, -eti, ep, -etj], dim=-1).reshape(B, -1)
        parts = [link]
        parts.append(-qin[:, torch.as_tensor(self._zg, device=self.device)])
        share = torch.as_tensor(self._rshare[self._cb_member], device=self.device)
        parts.append(-qin[:, torch.as_tensor(self._cb_item, device=self.device)] * share)
        parts.append(qout)
        parts += [dJo[..., 0], dJo[..., 1], dJo[..., 2]]
        parts.append(net.mass_vector()[:, 1].to(self.device).expand(B, -1) * inv_dt[:, None])
        parts.append(torch.full((B, 1), 1.0 if net.gauge else 0.0, dtype=torch.float64, device=self.device))
        raw = torch.cat(parts, dim=1)
        if self._gauge_drop is not None:
            keep = ~self._gauge_drop
            keep[-1] = True                                   # the gauge entry itself (row 0, col 0)
            raw = raw * keep.to(raw.dtype)
        vals = torch.zeros(B, self._nnz, dtype=torch.float64, device=self.device)
        vals.index_add_(1, self._inv, raw)
        return vals

    # ------------------------------------------------------------------ linear algebra on the pattern
    def matrix(self, vals_b: torch.Tensor):
        """SciPy CSR of one batch item's Jacobian values."""
        import scipy.sparse as sp
        n = 2 * self.N
        return sp.csr_matrix((vals_b.detach().cpu().numpy(), self._uc, self._rowptr), shape=(n, n))

    def jvp(self, vals: torch.Tensor, V: torch.Tensor) -> torch.Tensor:
        """J V for every item: vals (B, nnz), V (B, N, 2)."""
        B = vals.shape[0]
        v = V.reshape(B, -1)
        r = torch.as_tensor(self._ur, device=self.device)
        cidx = torch.as_tensor(self._uc, device=self.device)
        out = torch.zeros_like(v).index_add_(1, r, vals * v[:, cidx])
        return out.reshape(V.shape)

    def vjp(self, vals: torch.Tensor, W: torch.Tensor) -> torch.Tensor:
        """J^T W for every item."""
        B = vals.shape[0]
        w = W.reshape(B, -1)
        r = torch.as_tensor(self._ur, device=self.device)
        cidx = torch.as_tensor(self._uc, device=self.device)
        out = torch.zeros_like(w).index_add_(1, cidx, vals * w[:, r])
        return out.reshape(W.shape)

    def factor(self, vals: torch.Tensor):
        """SuperLU factors of every item's Jacobian."""
        from scipy.sparse.linalg import splu
        return [splu(self.matrix(vals[b]).tocsc()) for b in range(vals.shape[0])]

    def lu_solve(self, lus, rhs: torch.Tensor, *, transpose: bool = False) -> torch.Tensor:
        B = rhs.shape[0]
        r = rhs.reshape(B, -1).detach().cpu().numpy()
        out = np.stack([lus[b].solve(r[b], trans="T" if transpose else "N") for b in range(B)])
        return torch.as_tensor(out, device=self.device).reshape(rhs.shape)

    # ------------------------------------------------------------------ solve
    def picard_pressure(self, bv: ZonalBoundaryValues, T0: torch.Tensor, *, iterations: int = 60,
                        cd: float | None = None) -> torch.Tensor:
        """(B, N) pressures of the isothermal network at the temperatures T0 (B,) (zonal.py's start): each pass
        solves the linear network Q = G D with G taken at the previous pass's pressure differences."""
        import scipy.sparse as sp
        from scipy.sparse.linalg import spsolve
        net, N = self.net, self.N
        c = net.coef
        cd = c.discharge_coefficient if cd is None else float(cd)
        delta = c.pressure_smoothing_pa / c.rho
        np_ = lambda t: t.detach().cpu().numpy()  # noqa: E731
        li, lj, A = np_(net.link_i), np_(net.link_j), np_(net.link_area)
        por = np_(net.link_porous)
        pa = np.where(por, np_(net.link_a), 0.0)
        pc, pi_, parea = np_(net.pref_cell), np_(net.pref_index), np_(net.pref_area)
        bv = self._bv(bv, T0.shape[0])
        pb_all = np_(net.porous_b(bv, T0.shape[0]))
        out = np.zeros((T0.shape[0], N))
        for b in range(T0.shape[0]):
            pb = np.where(por, pb_all[b], 1.0)              # harmless where the orifice law applies
            flow = np_(bv.flow[b])
            src = np.zeros(N)
            np.add.at(src, np_(net.in_cell), flow[np_(net.in_patch)] * np_(net.in_share))
            np.add.at(src, np_(net.out_cell), -flow[np_(net.out_patch)] * np_(net.out_share))
            pref = np_(bv.p_ref[b])[pi_] if len(pc) else np.zeros(0)
            # stack term of the uniform temperature: ghf (rk_i - rk_j) = 0; the pressure differences alone drive
            D = np.ones(len(li))
            Dp = np.ones(len(pc))
            p = np.zeros(N)
            for _ in range(iterations + 1):
                m = np.sqrt(D * D + delta * delta)
                G = np.where(por, A * 2.0 / (pa + np.sqrt(pa * pa + 4.0 * pb * m)), cd * A * math.sqrt(2.0) * m ** -0.5)
                Gp = cd * parea * math.sqrt(2.0) * (Dp * Dp + delta * delta) ** -0.25
                Lm = sp.coo_matrix((np.concatenate((G, G, -G, -G, Gp)),
                                    (np.concatenate((li, lj, li, lj, pc)), np.concatenate((li, lj, lj, li, pc)))),
                                   shape=(N, N)).tocsr()
                rhs = src.copy()
                np.add.at(rhs, pc, Gp * pref)
                if net.gauge:
                    Lm = Lm[1:, 1:]
                    pn = np.concatenate(([0.0], spsolve(Lm.tocsc(), rhs[1:])))
                else:
                    pn = spsolve(Lm.tocsc(), rhs)
                if not np.isfinite(pn).all():
                    break
                Dn = pn[li] - pn[lj]
                change = np.abs(np.log(np.maximum(np.abs(Dn), 1e-12) / np.maximum(np.abs(D), 1e-12))).max()
                p, D = pn, np.where(np.abs(Dn) > 0, np.sqrt(np.abs(D * Dn)) * np.sign(Dn), Dn)
                Dp = pn[pc] - pref if len(pc) else Dp
                if change < 1e-6:
                    break
            out[b] = p
        return torch.as_tensor(out, device=self.device)

    def solve(self, bv: ZonalBoundaryValues, *, X0: torch.Tensor | None = None, X_prev: torch.Tensor | None = None,
              dt: float | None = None, cd: float | None = None, mixing: float | None = None,
              rtol: float = 1e-10, max_iterations: int = 60, ptc_iterations: int = 400, picard: bool = True):
        """Converged states (B, N, 2) and a report. The tolerance is relative: the volume rows to ``rtol`` times
        the total imposed flow, the enthalpy rows to ``rtol`` times that flow times 10 K."""
        t0 = time.perf_counter()
        net = self.net
        B = (X0.shape[0] if X0 is not None else (X_prev.shape[0] if X_prev is not None else bv.flow.shape[0]))
        bv = self._bv(bv, B)
        qscale = float(bv.flow.abs().sum(dim=1).max()) or 1.0
        tol = torch.tensor([rtol * qscale, rtol * qscale * 10.0], dtype=torch.float64, device=self.device)
        kw = dict(X_prev=X_prev, dt=dt, cd=cd, mixing=mixing)
        if X0 is None:
            X = X_prev.clone() if X_prev is not None else net.initial_state(bv).to(self.device)
            if picard and X_prev is None:
                X[..., 0] = self.picard_pressure(bv, X[:, 0, 1], cd=cd)
        else:
            X = X0.to(self.device, torch.float64).clone()

        X, R, vals, done, its, history = self._newton(X, bv, kw, tol, max_iterations)
        method = "newton"
        if not bool(done.all()):
            method = "newton + pseudo-transient"
            X, R, vals, done, n_ptc, h_ptc = self._ptc(X, bv, kw, tol, ptc_iterations, done)
            its += n_ptc
            history += h_ptc
        rinf = (R.abs() / tol).amax(dim=(1, 2))
        return X, {"converged": bool(done.all()), "converged_items": int(done.sum()), "items": B,
                   "iterations": its, "method": method, "residual_rel_max": float(rinf.max()),
                   "history": history, "wall_s": time.perf_counter() - t0}

    def _newton(self, X, bv, kw, tol, max_iterations):
        """Damped Newton with an Armijo backtrack per item; stops when every item is converged or one finds no
        descent step. Returns (X, R, values, converged (B,), iterations, history)."""
        B = X.shape[0]

        def norm(R):
            return (R.abs() / tol).amax(dim=(1, 2))

        def merit(R):
            return ((R / tol) ** 2).sum(dim=(1, 2))

        R, vals = self.evaluate(X, bv, jacobian=True, **kw)
        done = norm(R) < 1.0
        its = 0
        history = []
        for its in range(1, max_iterations + 1):
            if bool(done.all()):
                its -= 1
                break
            lus = self.factor(vals)
            step = -self.lu_solve(lus, R)
            f0 = merit(R)
            scale = torch.where(done, torch.zeros(B, dtype=torch.float64, device=self.device),
                                torch.ones(B, dtype=torch.float64, device=self.device))
            accepted = done.clone()
            Xn = X.clone()
            for _ in range(30):
                cand = X + scale[:, None, None] * step
                Rc = self.evaluate(cand, bv, **kw)
                fc = merit(Rc)
                ok = (~accepted) & torch.isfinite(fc) & (fc <= (1.0 - 1e-4 * scale) * f0)
                Xn[ok] = cand[ok]
                accepted |= ok
                if bool(accepted.all()):
                    break
                scale = torch.where(accepted, scale, 0.5 * scale)
            X = Xn
            R, vals = self.evaluate(X, bv, jacobian=True, **kw)
            done = norm(R) < 1.0
            history.append(float(norm(R).max()))
            if not bool(accepted.all()):
                break
        return X, R, vals, done, its, history

    def _ptc(self, X, bv, kw, tol, iterations, done):
        """Pseudo-transient continuation for the items Newton leaves: backward-Euler pseudo-time steps with the
        zones' own heat capacity on the enthalpy rows (the network relaxing in time toward the state sought, added
        to the physical time term of a step), each solved by the damped Newton. An item's pseudo-step doubles
        after a solved step and halves after a failed one; it is done when its own residual meets the tolerance."""
        B = X.shape[0]
        dev = self.device
        inv_dt = self._inv_dt(kw.get("dt"), kw.get("X_prev"), B)
        Xp = X.clone() if kw.get("X_prev") is None else kw["X_prev"].to(dev, torch.float64).expand_as(X).contiguous()
        tau = torch.ones(B, dtype=torch.float64, device=dev)
        R, vals = self.evaluate(X, bv, jacobian=True, **kw)
        n, history = 0, []
        for n in range(1, iterations + 1):
            if bool(done.all()) or float(tau[~done].max()) < 1e-6:
                n -= 1
                break
            inv = inv_dt + 1.0 / tau
            ref = (inv_dt[:, None, None] * Xp + X / tau[:, None, None]) / inv[:, None, None]
            Xn, _, _, ok, _, _ = self._newton(X, bv, dict(kw, X_prev=ref, dt=1.0 / inv), tol, 15)
            move = ok & ~done
            X = torch.where(move[:, None, None], Xn, X)
            tau = torch.where(done, tau, torch.where(ok, 2.0 * tau, 0.5 * tau)).clamp(max=1e12)
            R, vals = self.evaluate(X, bv, jacobian=True, **kw)
            nrm = (R.abs() / tol).amax(dim=(1, 2))
            done = done | (nrm < 1.0)
            history.append(float(nrm.max()))
        return X, R, vals, done, n, history
