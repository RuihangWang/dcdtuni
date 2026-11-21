"""The inner solve of CuHGGGR ODIL3D on our residual and exact JVP (opt-in ``--driver odil``; registration
``docs/research/2026-11-10-odil-style-ldc-speed.md``).

Ported from ODIL3D (``~/mycode/CuHGGGR/src/Solve/ODIL3D``, branch ``solve`` @ 929a82e: ``solver.py`` steady inner
iterations, ``linalg.py``, ``kernels.k_jac_off`` / ``k_jac_diag``), everything except its finite-difference JVP:

* pseudo-time continuation: solve (J + D/CFL) dq = -R per step, D = diag(lam, lam, lam, lam/beta, lam) with
  lam_P = sum_f (|F_f|/2 + nu |S_f| delta_f) -- the pseudo-time term on the continuity row too (artificial
  compressibility, beta = U_ref^2), in the Krylov operator and in the preconditioner; SER law
  CFL = CFL_ref (r_ref / r), growth <= ``growth`` per step; reject (restore, CFL / 4) when r > ``reject`` x r_prev; a
  step whose GMRES ends above 0.5 relative residual is still taken, with the CFL halved for the next one; the velocity
  change of a step limited to ``max_du`` U_ref; the closed-domain pressure level removed;
* inexact GMRES (restart 40, at most 80 iterations, rtol 0.03), Krylov vectors in float32, right-preconditioned by
  one V-cycle; the operator is OUR exact JVP (float64, the Warp dual numbers) plus the pseudo-time diagonal;
* the preconditioner matrix: the first-order coupled Jacobian of our rows -- upwind convection at the frozen face
  flux (Picard), compact diffusion, compact (Gauss, face-weighted) cell pressure gradient, the MWI pressure Laplacian
  of the continuity row and the velocity-to-flux coupling, boundary faces through the backend's affine ghost map --
  nb x nb blocks (one per cell, two per interior face), float32;
* the AMG: pairwise aggregation (two passes per level) on the geometric weights |S| delta_f, built ONCE per mesh;
  each step redoes only the Galerkin block sums, the block inverses and the dense LU of the coarsest level (~800
  cells); damped block Jacobi (omega 0.7, 2 + 2 sweeps).

Residual norm of the CFL law and the stop rule (ODIL3D's): per cell, the momentum rows scaled by 1 / (U^2 h^2) and the
continuity row by 1 / (U h^2), h = V^(1/3); r = max(|rms(mom)|, rms(cont)); stop when r <= atol.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any

import numpy as np
import torch
import warp as wp

wp.config.quiet = True


# ------------------------------------------------------------------------------------------------ Warp kernels (any nb)
@wp.kernel
def k_matvec(x: wp.array(dtype=Any), Dg: wp.array(dtype=Any), Aon: wp.array(dtype=Any), Ano: wp.array(dtype=Any),
             f_o: wp.array(dtype=wp.int32), f_n: wp.array(dtype=wp.int32), cf_ptr: wp.array(dtype=wp.int32),
             cf_code: wp.array(dtype=wp.int32), y: wp.array(dtype=Any)):
    c = wp.tid()
    s = Dg[c] * x[c]
    for k in range(cf_ptr[c], cf_ptr[c + 1]):
        code = cf_code[k]
        idx = code // 4
        kind = code - 4 * idx
        if kind == 0:
            s += Aon[idx] * x[f_n[idx]]
        elif kind == 1:
            s += Ano[idx] * x[f_o[idx]]
    y[c] = s


@wp.kernel
def k_jacobi(x: wp.array(dtype=Any), b: wp.array(dtype=Any), Dg: wp.array(dtype=Any), Dinv: wp.array(dtype=Any),
             Aon: wp.array(dtype=Any), Ano: wp.array(dtype=Any), f_o: wp.array(dtype=wp.int32),
             f_n: wp.array(dtype=wp.int32), cf_ptr: wp.array(dtype=wp.int32), cf_code: wp.array(dtype=wp.int32),
             omega: wp.float32, xnew: wp.array(dtype=Any)):
    c = wp.tid()
    s = b[c] - Dg[c] * x[c]
    for k in range(cf_ptr[c], cf_ptr[c + 1]):
        code = cf_code[k]
        idx = code // 4
        kind = code - 4 * idx
        if kind == 0:
            s -= Aon[idx] * x[f_n[idx]]
        elif kind == 1:
            s -= Ano[idx] * x[f_o[idx]]
    xnew[c] = x[c] + omega * (Dinv[c] * s)


@wp.kernel
def k_jacobi0(b: wp.array(dtype=Any), Dinv: wp.array(dtype=Any), omega: wp.float32, x: wp.array(dtype=Any)):
    c = wp.tid()
    x[c] = omega * (Dinv[c] * b[c])


@wp.kernel
def k_lres(x: wp.array(dtype=Any), b: wp.array(dtype=Any), Dg: wp.array(dtype=Any), Aon: wp.array(dtype=Any),
           Ano: wp.array(dtype=Any), f_o: wp.array(dtype=wp.int32), f_n: wp.array(dtype=wp.int32),
           cf_ptr: wp.array(dtype=wp.int32), cf_code: wp.array(dtype=wp.int32), r: wp.array(dtype=Any)):
    c = wp.tid()
    s = b[c] - Dg[c] * x[c]
    for k in range(cf_ptr[c], cf_ptr[c + 1]):
        code = cf_code[k]
        idx = code // 4
        kind = code - 4 * idx
        if kind == 0:
            s -= Aon[idx] * x[f_n[idx]]
        elif kind == 1:
            s -= Ano[idx] * x[f_o[idx]]
    r[c] = s


@wp.kernel
def k_restrict(r: wp.array(dtype=Any), agg: wp.array(dtype=wp.int32), rc: wp.array(dtype=Any)):
    c = wp.tid()
    wp.atomic_add(rc, agg[c], r[c])


@wp.kernel
def k_prolong(xc: wp.array(dtype=Any), agg: wp.array(dtype=wp.int32), scale: wp.float32, x: wp.array(dtype=Any)):
    c = wp.tid()
    x[c] = x[c] + scale * xc[agg[c]]


@wp.kernel
def k_gal_diag(Dg: wp.array(dtype=Any), agg: wp.array(dtype=wp.int32), Dgc: wp.array(dtype=Any)):
    c = wp.tid()
    wp.atomic_add(Dgc, agg[c], Dg[c])


@wp.kernel
def k_gal_face(Aon: wp.array(dtype=Any), Ano: wp.array(dtype=Any), f_o: wp.array(dtype=wp.int32),
               cmap: wp.array(dtype=wp.int32), cflip: wp.array(dtype=wp.int32), agg: wp.array(dtype=wp.int32),
               Dgc: wp.array(dtype=Any), Aonc: wp.array(dtype=Any), Anoc: wp.array(dtype=Any)):
    f = wp.tid()
    cf = cmap[f]
    if cf == -1:
        wp.atomic_add(Dgc, agg[f_o[f]], Aon[f] + Ano[f])
    elif cf >= 0:
        if cflip[f] == 0:
            wp.atomic_add(Aonc, cf, Aon[f])
            wp.atomic_add(Anoc, cf, Ano[f])
        else:
            wp.atomic_add(Aonc, cf, Ano[f])
            wp.atomic_add(Anoc, cf, Aon[f])


@wp.kernel
def k_copy(src: wp.array(dtype=Any), dst: wp.array(dtype=Any)):
    c = wp.tid()
    dst[c] = src[c]


@wp.kernel
def k_zero(x: wp.array(dtype=Any)):
    c = wp.tid()
    x[c] = x[c] - x[c]


def _dinv_check(Dinv, Dg, tag):
    """LPCNS_ODIL_BLOCK_FINITE=1 diagnostics: non-finite float32 block inverses of one level and their blocks."""
    if os.environ.get("LPCNS_ODIL_BLOCK_FINITE", "0") != "1":
        return
    bad = ~torch.isfinite(Dinv).reshape(Dinv.shape[0], -1).all(1)
    msg = f"odil-dc {tag} inverses: n {Dinv.shape[0]}, non-finite {int(bad.sum())}, max |Dinv| " \
          f"{float(torch.where(torch.isfinite(Dinv), Dinv.abs(), torch.zeros_like(Dinv)).max()):.2e}"
    if bad.any():
        i = int(torch.nonzero(bad)[0])
        D = Dg[i].double()
        msg += f"; first cell {i}: |D| diag {[f'{float(v):.2e}' for v in D.diagonal()]}, cond " \
               f"{float(torch.linalg.cond(D)):.2e}"
    print(msg, flush=True)


@wp.kernel
def k_invert44(Dg: wp.array(dtype=wp.mat44f), Dinv: wp.array(dtype=wp.mat44f)):
    c = wp.tid()
    Dinv[c] = wp.mat44f(wp.inverse(wp.mat44d(Dg[c])))


# ------------------------------------------------------------------------------------------------ first-order blocks (Warp)
@wp.struct
class FaceData:
    o: wp.array(dtype=wp.int32)          # owner (Newton cell)
    nn: wp.array(dtype=wp.int32)         # neighbour (Newton cell), -1 on a boundary face
    bidx: wp.array(dtype=wp.int32)       # boundary-face index into gA, -1 inside
    s: wp.array(dtype=wp.float64)        # owner-row signed area
    nrm: wp.array(dtype=wp.vec3d)
    gC: wp.array(dtype=wp.float64)
    gF: wp.array(dtype=wp.float64)
    delta: wp.array(dtype=wp.float64)
    nu: wp.array(dtype=wp.float64)
    alpha: wp.array(dtype=wp.float64)
    gpc: wp.array(dtype=wp.float64)
    cc: wp.array(dtype=wp.float64)
    ccont: wp.array(dtype=wp.float64)
    ghb: wp.array(dtype=wp.float64)
    bnc: wp.array(dtype=wp.int32)
    mwi: wp.array(dtype=wp.int32)
    zf: wp.array(dtype=wp.int32)
    gA: wp.array(dtype=wp.float64)       # [nbnd * nb * nb] ghost = A q_owner + b


@wp.func
def _dq(F: FaceData, f: int, side: int, r: int, c: int, flux: wp.float64, df: wp.float64, energy: int):
    """d(face quantity of row r) / d(state c of the owner (side 0) or neighbour / ghost (side 1)), per unit of the
    owner's signed area: momentum cc F u_f - nu delta (u_n - u_o), mass ccont flux, energy cc F T_f - alpha delta dT."""
    zero = wp.float64(0.0)
    fl = flux
    live = wp.float64(1.0)
    if F.zf[f] == 1:
        fl = zero
        live = zero
    cc = F.cc[f]
    up = wp.max(fl, zero) * cc
    dn = wp.min(fl, zero) * cc
    up_o = up
    dn_n = dn
    if F.bnc[f] == 1:
        up_o = zero
        dn_n = cc * fl
    mw = zero
    if F.mwi[f] == 1 and F.zf[f] == 0:
        mw = wp.float64(1.0)
    kp = F.gpc[f] * df * F.delta[f] * mw
    w = F.gC[f]
    sg = wp.float64(1.0)
    if side == 1:
        w = F.gF[f]
        sg = wp.float64(-1.0)
    if r < 3:
        if c == r:
            if side == 0:
                return up_o + F.nu[f] * F.delta[f]
            return dn_n - F.nu[f] * F.delta[f]
        return zero
    if r == 3:
        if c < 3:
            return F.ccont[f] * live * w * F.nrm[f][c]
        if c == 3:
            return sg * F.ccont[f] * kp
        if energy == 1 and c == 4:
            return -sg * F.ccont[f] * kp * F.ghb[f]
        return zero
    if energy == 1 and r == 4 and c == 4:
        if side == 0:
            return up_o + F.alpha[f] * F.delta[f]
        return dn_n - F.alpha[f] * F.delta[f]
    return zero


@wp.kernel
def k_ap_lam(c_off: wp.array(dtype=wp.int32), c_face: wp.array(dtype=wp.int32), c_sarea: wp.array(dtype=wp.float64),
             F: FaceData, flux: wp.array(dtype=wp.float64), vol: wp.array(dtype=wp.float64),
             dcell: wp.array(dtype=wp.float64), lam: wp.array(dtype=wp.float64)):
    i = wp.tid()
    ap = wp.float64(0.0)
    lm = wp.float64(0.0)
    for k in range(c_off[i], c_off[i + 1]):
        f = c_face[k]
        s = c_sarea[k]
        fl = flux[f]
        if F.zf[f] == 1:
            fl = wp.float64(0.0)
        Fv = fl * s
        visc = F.nu[f] * wp.abs(s) * F.delta[f]
        ap += wp.max(Fv, wp.float64(0.0)) + visc
        lm += wp.float64(0.5) * wp.abs(Fv) + visc
    dcell[i] = vol[i] / ap
    lam[i] = lm


@wp.func
def _df(F: FaceData, f: int, dcell: wp.array(dtype=wp.float64)):
    n = F.nn[f]
    if n < 0:
        return wp.float64(0.5) * dcell[F.o[f]]
    return wp.float64(0.5) * (dcell[F.o[f]] + dcell[n])


@wp.kernel
def k_off_blocks(inner: wp.array(dtype=wp.int32), F: FaceData, flux: wp.array(dtype=wp.float64),
                 dcell: wp.array(dtype=wp.float64), pcoef: wp.array(dtype=wp.float64),
                 ghbc: wp.array(dtype=wp.float64), nb: int, energy: int,
                 Aon: wp.array(dtype=wp.float32), Ano: wp.array(dtype=wp.float32)):
    fi = wp.tid()
    f = inner[fi]
    o = F.o[f]
    n = F.nn[f]
    s = F.s[f]
    df = _df(F, f, dcell)
    fl = flux[f]
    base = fi * nb * nb
    for r in range(nb):
        for c in range(nb):
            a = s * _dq(F, f, 1, r, c, fl, df, energy)          # owner row, neighbour column
            b = -s * _dq(F, f, 0, r, c, fl, df, energy)         # neighbour row, owner column
            if r < 3:
                if c == 3:
                    a += pcoef[o] * s * F.nrm[f][r] * F.gF[f]
                    b += -pcoef[n] * s * F.nrm[f][r] * F.gC[f]
                if energy == 1 and c == 4:
                    a += -ghbc[o] * s * F.nrm[f][r] * F.gF[f]
                    b += ghbc[n] * s * F.nrm[f][r] * F.gC[f]
            Aon[base + r * nb + c] = wp.float32(a)
            Ano[base + r * nb + c] = wp.float32(b)


@wp.kernel
def k_diag_blocks(c_off: wp.array(dtype=wp.int32), c_face: wp.array(dtype=wp.int32),
                  c_sarea: wp.array(dtype=wp.float64), F: FaceData, flux: wp.array(dtype=wp.float64),
                  dcell: wp.array(dtype=wp.float64), pcoef: wp.array(dtype=wp.float64),
                  ghbc: wp.array(dtype=wp.float64), shift: wp.array(dtype=wp.float64), nb: int, energy: int,
                  Dg: wp.array(dtype=wp.float32)):
    """Diagonal block of cell i: sum over its faces of s_i dQ(self), boundary ghosts through A, the compact Gauss
    pressure (and buoyancy) gradient of the momentum rows, plus the pseudo-time shift [nb * n] (field-major)."""
    i = wp.tid()
    n_cells = dcell.shape[0]
    base = i * nb * nb
    for r in range(nb):
        for c in range(nb):
            acc = wp.float64(0.0)
            for k in range(c_off[i], c_off[i + 1]):
                f = c_face[k]
                si = c_sarea[k]
                fl = flux[f]
                df = _df(F, f, dcell)
                own = F.o[f] == i
                if own:
                    acc += si * _dq(F, f, 0, r, c, fl, df, energy)
                    if r < 3 and c == 3:
                        acc += pcoef[i] * si * F.nrm[f][r] * F.gC[f]
                    if energy == 1 and r < 3 and c == 4:
                        acc += -ghbc[i] * si * F.nrm[f][r] * F.gC[f]
                    bi = F.bidx[f]
                    if bi >= 0:
                        gb = bi * nb * nb
                        for m in range(nb):
                            g = F.gA[gb + m * nb + c]
                            acc += si * _dq(F, f, 1, r, m, fl, df, energy) * g
                        if r < 3:
                            acc += pcoef[i] * si * F.nrm[f][r] * F.gA[gb + 3 * nb + c]
                            if energy == 1:
                                acc += -ghbc[i] * si * F.nrm[f][r] * F.gA[gb + 4 * nb + c]
                else:
                    acc += si * _dq(F, f, 1, r, c, fl, df, energy)
                    if r < 3 and c == 3:
                        acc += pcoef[i] * si * F.nrm[f][r] * F.gF[f]
                    if energy == 1 and r < 3 and c == 4:
                        acc += -ghbc[i] * si * F.nrm[f][r] * F.gF[f]
            if r == c:
                acc += shift[r * n_cells + i]
            Dg[base + r * nb + c] = wp.float32(acc)


# ------------------------------------------------------------------------------------------------ setup (CPU, ODIL3D)
def _pairwise(n, eo, en, w, rounds=24):
    """Pairwise matching by strongest-neighbour handshakes; leftovers join their strongest neighbour."""
    mate = np.full(n, -1, np.int64)
    src = np.concatenate([eo, en])
    dst = np.concatenate([en, eo])
    ww = np.concatenate([w, w])
    for _ in range(rounds):
        m = (mate[src] < 0) & (mate[dst] < 0)
        if not np.any(m):
            break
        s, d, wt = src[m], dst[m], ww[m]
        order = np.lexsort((-wt, s))
        s, d = s[order], d[order]
        first = np.r_[True, s[1:] != s[:-1]]
        best = np.full(n, -1, np.int64)
        best[s[first]] = d[first]
        cand = np.nonzero(best >= 0)[0]
        mutual = cand[best[best[cand]] == cand]
        i = mutual[mutual < best[mutual]]
        if len(i) == 0:
            break
        j = best[i]
        mate[i] = j
        mate[j] = i
    agg = np.full(n, -1, np.int64)
    lead = np.nonzero((mate < 0) | (np.arange(n) < mate))[0]
    agg[lead] = np.arange(len(lead))
    paired = np.nonzero(mate >= 0)[0]
    agg[paired] = agg[np.minimum(paired, mate[paired])]
    single = np.nonzero(mate < 0)[0]
    if len(single):
        is_single = np.zeros(n, bool)
        is_single[single] = True
        m = is_single[src] & ~is_single[dst]
        s, d, wt = src[m], dst[m], ww[m]
        if len(s):
            order = np.lexsort((-wt, s))
            s, d = s[order], d[order]
            first = np.r_[True, s[1:] != s[:-1]]
            agg[s[first]] = agg[d[first]]
    uniq, agg = np.unique(agg, return_inverse=True)
    return agg, len(uniq)


def build_hierarchy(n, f_o, f_n, weight, coarse_target=800, max_levels=20, passes=2):
    """Aggregation hierarchy on a graph (n nodes, edges f_o-f_n with weights).  Returns a list of levels."""
    levels = []
    co, cn, cw, nc_cur = np.asarray(f_o), np.asarray(f_n), np.asarray(weight, float), n
    while nc_cur > coarse_target and len(levels) < max_levels:
        agg = np.arange(nc_cur)
        eo, en, ew, m = co, cn, cw, nc_cur
        for _ in range(passes):
            a, na = _pairwise(m, eo, en, ew)
            agg = a[agg]
            I, J = a[eo], a[en]
            x = I != J
            lo, hi = np.minimum(I[x], J[x]), np.maximum(I[x], J[x])
            uniq, inv = np.unique(lo * na + hi, return_inverse=True)
            eo, en = uniq // na, uniq % na
            ew = np.bincount(inv, weights=ew[x], minlength=len(uniq))
            m = na
        if m > 0.85 * nc_cur:
            break
        I, J = agg[co], agg[cn]
        cross = I != J
        lo, hi = np.minimum(I[cross], J[cross]), np.maximum(I[cross], J[cross])
        uniq, inv = np.unique(lo * m + hi, return_inverse=True)
        cmap = np.full(len(co), -1, np.int64)
        cmap[cross] = inv
        cflip = np.zeros(len(co), np.int64)
        cflip[cross] = (I[cross] > J[cross]).astype(np.int64)
        levels.append(dict(agg=agg, nc=m, cmap=cmap, cflip=cflip, f_o=uniq // m, f_n=uniq % m))
        co, cn, nc_cur = uniq // m, uniq % m, m
        cw = np.bincount(inv, weights=cw[cross], minlength=len(uniq))
    return levels


def _lexsort_t(wt, s):
    """torch twin of np.lexsort((-wt, s)): by s, then by descending wt, ties in input order."""
    o1 = torch.sort(-wt, stable=True).indices
    o2 = torch.sort(s[o1], stable=True).indices
    return o1[o2]


def _pairwise_t(n, eo, en, w, rounds=24):
    """``_pairwise`` on the GPU (torch), the same handshakes, tie-breaks and numbering."""
    dev = eo.device
    mate = torch.full((n,), -1, dtype=torch.int64, device=dev)
    src = torch.cat([eo, en])
    dst = torch.cat([en, eo])
    ww = torch.cat([w, w])
    ar = torch.arange(n, device=dev)
    for _ in range(rounds):
        m = (mate[src] < 0) & (mate[dst] < 0)
        if not bool(m.any()):
            break
        s, d, wt = src[m], dst[m], ww[m]
        order = _lexsort_t(wt, s)
        s, d = s[order], d[order]
        first = torch.ones_like(s, dtype=torch.bool)
        first[1:] = s[1:] != s[:-1]
        best = torch.full((n,), -1, dtype=torch.int64, device=dev)
        best[s[first]] = d[first]
        cand = torch.nonzero(best >= 0).squeeze(1)
        mutual = cand[best[best[cand]] == cand]
        i = mutual[mutual < best[mutual]]
        if len(i) == 0:
            break
        j = best[i]
        mate[i] = j
        mate[j] = i
    agg = torch.full((n,), -1, dtype=torch.int64, device=dev)
    lead = torch.nonzero((mate < 0) | (ar < mate)).squeeze(1)
    agg[lead] = torch.arange(len(lead), device=dev)
    paired = torch.nonzero(mate >= 0).squeeze(1)
    agg[paired] = agg[torch.minimum(paired, mate[paired])]
    single = torch.nonzero(mate < 0).squeeze(1)
    if len(single):
        is_single = torch.zeros(n, dtype=torch.bool, device=dev)
        is_single[single] = True
        m = is_single[src] & ~is_single[dst]
        s, d, wt = src[m], dst[m], ww[m]
        if len(s):
            order = _lexsort_t(wt, s)
            s, d = s[order], d[order]
            first = torch.ones_like(s, dtype=torch.bool)
            first[1:] = s[1:] != s[:-1]
            agg[s[first]] = agg[d[first]]
    uniq, agg = torch.unique(agg, sorted=True, return_inverse=True)
    return agg, len(uniq)


def build_hierarchy_t(n, f_o, f_n, weight, coarse_target=800, max_levels=20, passes=2, device="cuda"):
    """``build_hierarchy`` on the GPU (torch): the same levels (returned as numpy arrays), ~10x faster on 7e5 cells."""
    levels = []
    t_ = lambda a: torch.as_tensor(np.asarray(a), device=device)        # noqa: E731
    co, cn, cw, nc_cur = t_(f_o).long(), t_(f_n).long(), t_(weight).double(), n
    while nc_cur > coarse_target and len(levels) < max_levels:
        agg = torch.arange(nc_cur, device=device)
        eo, en, ew, m = co, cn, cw, nc_cur
        for _ in range(passes):
            a, na = _pairwise_t(m, eo, en, ew)
            agg = a[agg]
            I, J = a[eo], a[en]
            x = I != J
            lo, hi = torch.minimum(I[x], J[x]), torch.maximum(I[x], J[x])
            uniq, inv = torch.unique(lo * na + hi, sorted=True, return_inverse=True)
            eo, en = uniq // na, uniq % na
            ew = torch.zeros(len(uniq), dtype=torch.float64, device=device).index_add_(0, inv, ew[x])
            m = na
        if m > 0.85 * nc_cur:
            break
        I, J = agg[co], agg[cn]
        cross = I != J
        lo, hi = torch.minimum(I[cross], J[cross]), torch.maximum(I[cross], J[cross])
        uniq, inv = torch.unique(lo * m + hi, sorted=True, return_inverse=True)
        cmap = torch.full((len(co),), -1, dtype=torch.int64, device=device)
        cmap[cross] = inv
        cflip = torch.zeros(len(co), dtype=torch.int64, device=device)
        cflip[cross] = (I[cross] > J[cross]).long()
        np_ = lambda a: a.cpu().numpy()                                    # noqa: E731
        levels.append(dict(agg=np_(agg), nc=m, cmap=np_(cmap), cflip=np_(cflip), f_o=np_(uniq // m),
                           f_n=np_(uniq % m)))
        co, cn, nc_cur = uniq // m, uniq % m, m
        cw = torch.zeros(len(uniq), dtype=torch.float64, device=device).index_add_(0, inv, cw[cross])
    return levels


def face_csr(n, f_o, f_n):
    """cell -> face codes (4 * face + kind, kind 0 owner / 1 neighbour) for a face list on n cells."""
    cells = np.concatenate([f_o, f_n])
    codes = np.concatenate([4 * np.arange(len(f_o)), 4 * np.arange(len(f_o)) + 1])
    order = np.argsort(cells, kind="stable")
    ptr = np.zeros(n + 1, np.int64)
    np.cumsum(np.bincount(cells, minlength=n), out=ptr[1:])
    return ptr, codes[order]


# ------------------------------------------------------------------------------------------------ block levels / AMG
_HALF_SCALE = 1024.0          # half-precision off-diagonal blocks are stored x 2^10 (exact): the FV coefficients
_HALF_KERNELS = {}            # (~1e-6..7e-2) then sit in float16's normal range


def _half_kernels(nb):
    """Block Jacobi sweep and residual with the off-diagonal blocks in float16 (x _HALF_SCALE), everything else in
    float32; the sweep as (1 - omega) x + omega D^-1 (b - O x), which equals x + omega D^-1 (b - D x - O x) when
    D^-1 is the exact inverse (no l1 term) and saves reading D."""
    if nb in _HALF_KERNELS:
        return _HALF_KERNELS[nb]
    MF = wp.types.matrix(shape=(nb, nb), dtype=wp.float32)
    MH = wp.types.matrix(shape=(nb, nb), dtype=wp.float16)
    VF = wp.types.vector(length=nb, dtype=wp.float32)

    @wp.kernel(module="unique")
    def kj(x: wp.array(dtype=VF), b: wp.array(dtype=VF), Dinv: wp.array(dtype=MF), Aon: wp.array(dtype=Any),
           Ano: wp.array(dtype=Any), f_o: wp.array(dtype=wp.int32), f_n: wp.array(dtype=wp.int32),
           cf_ptr: wp.array(dtype=wp.int32), cf_code: wp.array(dtype=wp.int32), omega: wp.float32,
           xnew: wp.array(dtype=VF)):
        c = wp.tid()
        s = VF()
        for k in range(cf_ptr[c], cf_ptr[c + 1]):
            code = cf_code[k]
            idx = code // 4
            kind = code - 4 * idx
            if kind == 0:
                s += MF(Aon[idx]) * x[f_n[idx]]
            elif kind == 1:
                s += MF(Ano[idx]) * x[f_o[idx]]
        xnew[c] = (wp.float32(1.0) - omega) * x[c] + omega * (Dinv[c] * (b[c] - s * wp.float32(1.0 / 1024.0)))

    @wp.kernel(module="unique")
    def kr(x: wp.array(dtype=VF), b: wp.array(dtype=VF), Dg: wp.array(dtype=MF), Aon: wp.array(dtype=Any),
           Ano: wp.array(dtype=Any), f_o: wp.array(dtype=wp.int32), f_n: wp.array(dtype=wp.int32),
           cf_ptr: wp.array(dtype=wp.int32), cf_code: wp.array(dtype=wp.int32), r: wp.array(dtype=VF)):
        c = wp.tid()
        s = VF()
        for k in range(cf_ptr[c], cf_ptr[c + 1]):
            code = cf_code[k]
            idx = code // 4
            kind = code - 4 * idx
            if kind == 0:
                s += MF(Aon[idx]) * x[f_n[idx]]
            elif kind == 1:
                s += MF(Ano[idx]) * x[f_o[idx]]
        r[c] = b[c] - Dg[c] * x[c] - s * wp.float32(1.0 / 1024.0)

    @wp.kernel(module="unique")
    def kj1(x: wp.array(dtype=VF), b: wp.array(dtype=VF), Dg: wp.array(dtype=MF), Dinv: wp.array(dtype=MF),
            Aon: wp.array(dtype=Any), Ano: wp.array(dtype=Any), f_o: wp.array(dtype=wp.int32),
            f_n: wp.array(dtype=wp.int32), cf_ptr: wp.array(dtype=wp.int32), cf_code: wp.array(dtype=wp.int32),
            omega: wp.float32, xnew: wp.array(dtype=VF)):
        # the l1 sweep: D^-1 inverts D + l1 terms, so D x is computed (x + omega D^-1 (b - D x - O x))
        c = wp.tid()
        s = VF()
        for k in range(cf_ptr[c], cf_ptr[c + 1]):
            code = cf_code[k]
            idx = code // 4
            kind = code - 4 * idx
            if kind == 0:
                s += MF(Aon[idx]) * x[f_n[idx]]
            elif kind == 1:
                s += MF(Ano[idx]) * x[f_o[idx]]
        xnew[c] = x[c] + omega * (Dinv[c] * (b[c] - Dg[c] * x[c] - s * wp.float32(1.0 / 1024.0)))

    _HALF_KERNELS[nb] = (MH, kj, kr, kj1)
    return _HALF_KERNELS[nb]


class BlockLevel:
    """One level: n cells, faces (f_o, f_n), the cell -> face CSR, block arrays as torch tensors [*, nb, nb] (float32)
    with Warp views of the block types."""

    def __init__(self, n, f_o, f_n, nb, device):
        self.n, self.nf, self.nb = int(n), int(len(f_o)), nb
        self.tdev = device
        self.wdev = wp.device_from_torch(device)
        self.mat = wp.types.matrix(shape=(nb, nb), dtype=wp.float32)
        self.vec = wp.types.vector(length=nb, dtype=wp.float32)
        i32 = lambda a: wp.from_torch(torch.as_tensor(np.asarray(a, np.int32), device=device))   # noqa: E731
        self.f_o, self.f_n = i32(f_o), i32(f_n)
        ptr, code = face_csr(self.n, np.asarray(f_o), np.asarray(f_n))
        self.cf_ptr, self.cf_code = i32(ptr), i32(code)
        z = lambda m: torch.zeros(max(m, 1), nb, nb, dtype=torch.float32, device=device)   # noqa: E731
        self.Dg_t, self.Dinv_t, self.Aon_t, self.Ano_t = z(self.n), z(self.n), z(self.nf), z(self.nf)
        self.Dg, self.Dinv = self.wm(self.Dg_t), self.wm(self.Dinv_t)
        self.Aon, self.Ano = self.wm(self.Aon_t), self.wm(self.Ano_t)
        self.tmp_t = [torch.zeros(self.n, nb, dtype=torch.float32, device=device) for _ in range(3)]
        self.tmp = [self.wv(t) for t in self.tmp_t]
        self.rec = None                                    # a list while a V-cycle is being recorded

    def wm(self, t):
        return wp.from_torch(t, dtype=self.mat)

    def wv(self, t):
        return wp.from_torch(t, dtype=self.vec)

    def launch(self, k, dim, inputs):
        if dim > 0:
            if self.rec is not None:
                self.rec.append(wp.launch(k, dim=dim, inputs=inputs, device=self.wdev, record_cmd=True))
            else:
                wp.launch(k, dim=dim, inputs=inputs, device=self.wdev)

    def invert(self, l1=0.0):
        self.l1_on = l1 > 0.0                              # the float16 sweep then reads D (kj1)
        if l1 > 0.0:
            # l1 block Jacobi: invert D + l1 * diag(sum_j |A_ij| over the off-diagonal blocks of the row); the
            # residual keeps the true D, so only the smoother changes (stable where plain block Jacobi diverges)
            n, nf = self.n, self.nf
            rs = torch.zeros(n, self.nb, dtype=torch.float32, device=self.tdev)
            if nf:
                fo = wp.to_torch(self.f_o).long()
                fn = wp.to_torch(self.f_n).long()
                rs.index_add_(0, fo, self.Aon_t[:nf].abs().sum(2))
                rs.index_add_(0, fn, self.Ano_t[:nf].abs().sum(2))
            M = self.Dg_t[:n].double().clone()
            d = M.diagonal(dim1=1, dim2=2)
            sgn = torch.where(d < 0, -1.0, 1.0).to(d.dtype)
            add = l1 * sgn * rs.double()
            sel = float(os.environ.get("LPCNS_ODIL_L1_SEL", "0"))
            if sel > 0.0:
                # LPCNS_ODIL_L1_SEL=theta: the l1 term only in the rows without diagonal dominance (off-diagonal row
                # sum > theta |D_rr|); the dominant rows keep the exact block inverse (plain l1 everywhere weakens
                # the smoother where block Jacobi was stable: INROW 2-2.5x slower)
                weak = rs.double() > sel * d.abs()
                add = add * weak
                if os.environ.get("LPCNS_ODIL_L1_LOG", "0") == "1":
                    print(f"odil-dc l1 rows (n={n}, nb={self.nb}): " + " ".join(
                        f"{float(weak[:, r].double().mean()):.4f}" for r in range(self.nb)), flush=True)
            rows = getattr(self, "l1_rows", None)
            if rows is not None:
                # partitioned driver (LPCNS_MGPU_L1_ROWS): the l1 term only in the listed rows of this level
                add = add * rows.to(add.dtype)[:, None]
            d.add_(add)
            self.Dinv_t[:n].copy_(torch.linalg.inv(M).float())
            _dinv_check(self.Dinv_t[:n], self.Dg_t[:n], "l1")
            return
        if self.nb == 4:
            wp.launch(k_invert44, dim=self.n, inputs=[wp.from_torch(self.Dg_t, dtype=wp.mat44f),
                                                      wp.from_torch(self.Dinv_t, dtype=wp.mat44f)], device=self.wdev)
        else:
            self.Dinv_t[: self.n].copy_(torch.linalg.inv(self.Dg_t[: self.n].double()).float())
        _dinv_check(self.Dinv_t[: self.n], self.Dg_t[: self.n], "block")

    def matvec(self, x, y):
        self.launch(k_matvec, self.n, [x, self.Dg, self.Aon, self.Ano, self.f_o, self.f_n, self.cf_ptr, self.cf_code, y])

    def enable_half(self):
        """The off-diagonal blocks also kept in float16 (x _HALF_SCALE) for the sweeps and residuals of this level;
        ``refresh_half`` after every change of Aon / Ano (the float32 copies stay for the Galerkin sums)."""
        MH, self._kj_h, self._kr_h, self._kj1_h = _half_kernels(self.nb)
        z = lambda: torch.zeros(max(self.nf, 1), self.nb, self.nb, dtype=torch.float16, device=self.tdev)  # noqa: E731
        self.Aon_ht, self.Ano_ht = z(), z()
        self.Aon_h, self.Ano_h = wp.from_torch(self.Aon_ht, dtype=MH), wp.from_torch(self.Ano_ht, dtype=MH)
        self.half = True

    def refresh_half(self):
        # the scaled float16 copy holds |a| x 2^10 < 65504: a level whose off-diagonal coefficients reach that range
        # (the agglomerated coarse level of a raised-floor hall, 2026-11-13: k / epsilon rows, inf -> NaN in every
        # V-cycle) sweeps in float32 until a rebuild brings them back into range
        amax = float(torch.maximum(self.Aon_t[: self.nf].abs().max(), self.Ano_t[: self.nf].abs().max())) \
            if self.nf else 0.0
        fits = amax * _HALF_SCALE < 6.0e4
        if fits != getattr(self, "half", False):
            print(f"odil-dc {self.nb}x{self.nb} level: off-diagonal max {amax:.3e}, sweeps in "
                  f"{'float16' if fits else 'float32 (float16 range exceeded)'}", flush=True)
        self.half = fits
        if fits:
            self.Aon_ht.copy_(self.Aon_t * _HALF_SCALE)
            self.Ano_ht.copy_(self.Ano_t * _HALF_SCALE)

    def jacobi(self, x, b, omega, sweeps, zero_start):
        t = self.tmp[2]
        if zero_start:
            self.launch(k_jacobi0, self.n, [b, self.Dinv, float(omega), x])
            sweeps -= 1
        for _ in range(sweeps):
            if getattr(self, "half", False) and getattr(self, "l1_on", False):
                self.launch(self._kj1_h, self.n, [x, b, self.Dg, self.Dinv, self.Aon_h, self.Ano_h, self.f_o,
                                                  self.f_n, self.cf_ptr, self.cf_code, float(omega), t])
            elif getattr(self, "half", False):
                self.launch(self._kj_h, self.n, [x, b, self.Dinv, self.Aon_h, self.Ano_h, self.f_o, self.f_n,
                                                 self.cf_ptr, self.cf_code, float(omega), t])
            else:
                self.launch(k_jacobi, self.n, [x, b, self.Dg, self.Dinv, self.Aon, self.Ano, self.f_o, self.f_n,
                                               self.cf_ptr, self.cf_code, float(omega), t])
            self.launch(k_copy, self.n, [t, x])

    def residual(self, x, b, r):
        if getattr(self, "half", False):
            self.launch(self._kr_h, self.n, [x, b, self.Dg, self.Aon_h, self.Ano_h, self.f_o, self.f_n, self.cf_ptr,
                                             self.cf_code, r])
            return
        self.launch(k_lres, self.n, [x, b, self.Dg, self.Aon, self.Ano, self.f_o, self.f_n, self.cf_ptr, self.cf_code, r])


class _Lazy:
    """An object built on its first attribute access (``make()``), then delegated to."""

    def __init__(self, make):
        self.__dict__["_make"], self.__dict__["_obj"] = make, None

    def _get(self):
        if self.__dict__["_obj"] is None:
            self.__dict__["_obj"] = self.__dict__["_make"]()
        return self.__dict__["_obj"]

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def __setattr__(self, name, value):
        setattr(self._get(), name, value)


class AMG:
    """Aggregation AMG V-cycle (ODIL3D ``linalg.AMG``) for nb x nb blocks; the hierarchy is fixed at construction,
    ``update()`` redoes the Galerkin block sums, the block inverses and the coarsest dense LU."""

    def __init__(self, fine: BlockLevel, hierarchy, omega=0.7, pre=2, post=2, coarse_scale=1.0, p_index=None, l1=0.0):
        self.levels = [fine]
        self.l1 = float(l1)
        self.pp_scale, self.p_field = 1.0, None        # coarse-level scaling of the pressure-pressure entries
        self.maps = []
        self.omega, self.pre, self.post, self.coarse_scale = omega, pre, post, coarse_scale
        self.p_index = p_index
        dev = fine.tdev
        i32 = lambda a: wp.from_torch(torch.as_tensor(np.asarray(a, np.int32), device=dev))   # noqa: E731
        for L in hierarchy:
            self.maps.append(dict(agg=i32(L["agg"]), cmap=i32(L["cmap"]), cflip=i32(L["cflip"])))
            self.levels.append(BlockLevel(L["nc"], L["f_o"], L["f_n"], fine.nb, dev))
        self.coarse_lu = None

    def describe(self):
        return " -> ".join(str(L.n) for L in self.levels)

    def update(self):
        fine = self.levels[0]
        fine.invert(self.l1)
        for il, mp in enumerate(self.maps):
            F, C = self.levels[il], self.levels[il + 1]
            C.Dg_t.zero_()
            C.Aon_t.zero_()
            C.Ano_t.zero_()
            F.launch(k_gal_diag, F.n, [F.Dg, mp["agg"], C.Dg])
            F.launch(k_gal_face, F.nf, [F.Aon, F.Ano, F.f_o, mp["cmap"], mp["cflip"], mp["agg"], C.Dg, C.Aon, C.Ano])
            if self.pp_scale != 1.0 and self.p_field is not None:
                q = self.p_field
                C.Dg_t[: C.n, q, q] *= self.pp_scale
                C.Aon_t[: C.nf, q, q] *= self.pp_scale
                C.Ano_t[: C.nf, q, q] *= self.pp_scale
            C.invert(self.l1)
        L = self.levels[-1]
        nb = L.nb
        A = torch.zeros(L.n, nb, L.n, nb, dtype=torch.float64, device=L.tdev)
        idx = torch.arange(L.n, device=L.tdev)
        A[idx, :, idx, :] = L.Dg_t[: L.n].double()
        if L.nf:
            fo = wp.to_torch(L.f_o).long()
            fn = wp.to_torch(L.f_n).long()
            A[fo, :, fn, :] += L.Aon_t[: L.nf].double()
            A[fn, :, fo, :] += L.Ano_t[: L.nf].double()
        A = A.reshape(L.n * nb, L.n * nb)
        if self.p_index is not None:
            # closed domain: the constant pressure is a null vector of the steady rows; pin it softly
            d = torch.diagonal(A)
            ii = torch.arange(self.p_index, A.shape[0], nb, device=A.device)
            A[ii, ii] += 1e-6 * d[ii].abs().max()
        # the coarsest solve as a dense inverse (float64, kept in place so a recorded V-cycle reads the new one)
        Ainv = torch.linalg.inv(A)
        if os.environ.get("LPCNS_ODIL_BLOCK_FINITE", "0") == "1":
            print(f"odil-dc coarsest inverse: n {A.shape[0]}, non-finite {int((~torch.isfinite(Ainv)).sum())}, "
                  f"max |A| {float(A.abs().max()):.2e}, max |A^-1| {float(Ainv.abs().max()):.2e}", flush=True)
        if getattr(self, "coarse_inv", None) is None or self.coarse_inv.shape != Ainv.shape:
            self.coarse_inv = Ainv
        else:
            self.coarse_inv.copy_(Ainv)

    def _coarse_solve(self, b, x):
        bt = wp.to_torch(b).reshape(-1).double()
        wp.to_torch(x).reshape(-1).copy_(self.coarse_inv @ bt)

    def _vcycle(self, b, x, level, cmds):
        L = self.levels[level]
        if level == len(self.levels) - 1:
            cmds.append(lambda b=b, x=x: self._coarse_solve(b, x))
            return
        L.rec = cmds
        L.jacobi(x, b, self.omega, self.pre, True)
        r = L.tmp[0]
        L.residual(x, b, r)
        C = self.levels[level + 1]
        bc, xc = C.tmp[0], C.tmp[1]
        C.rec = cmds
        C.launch(k_zero, C.n, [bc])
        L.launch(k_restrict, L.n, [r, self.maps[level]["agg"], bc])
        self._vcycle(bc, xc, level + 1, cmds)
        L.rec = cmds
        L.launch(k_prolong, L.n, [xc, self.maps[level]["agg"], float(self.coarse_scale), x])
        L.jacobi(x, b, self.omega, self.post, False)
        L.rec = None
        C.rec = None

    def vcycle(self, b, x):
        """One V-cycle b -> x; the launches of a (b, x) pair are recorded once and replayed."""
        key = (b.ptr, x.ptr)
        cache = self.__dict__.setdefault("_cmds", {})
        if key not in cache:
            cmds = []
            self._vcycle(b, x, 0, cmds)
            cache[key] = cmds
        for c in cache[key]:
            if isinstance(c, wp.Launch):
                c.launch()
            else:
                c()


# ------------------------------------------------------------------------------------------------ GMRES (ODIL3D)
_GMRES_BASIS = {}


def gmres(matvec, precond, b, restart=30, maxiter=60, rtol=1e-2, atol=0.0):
    """Right-preconditioned restarted GMRES (CGS2) on flat torch vectors."""
    dot = torch.matmul
    x = torch.zeros_like(b)
    beta0 = torch.sqrt(dot(b, b)).item()
    if beta0 == 0.0:
        return x, dict(it=0, rel=0.0)
    tol = max(rtol * beta0, atol)
    total = 0
    r = b.clone()
    beta = beta0
    # LPCNS_ODIL_GMRES_Z=1: keep z_j = M^-1 v_j (the preconditioner's own outputs, as FGMRES) so the update is
    # x += sum y_j z_j without a further preconditioner application, and reuse one uninitialised Krylov basis
    keep_z = os.environ.get("LPCNS_ODIL_GMRES_Z", "0") == "1"
    while True:
        m = restart
        if keep_z:
            key = ((m + 1,) + tuple(b.shape), b.dtype, b.device)
            V = _GMRES_BASIS.get(key)
            if V is None:
                _GMRES_BASIS.clear()
                V = _GMRES_BASIS[key] = torch.empty(key[0], dtype=b.dtype, device=b.device)
            Z = []
        else:
            V = torch.zeros((m + 1,) + b.shape, dtype=b.dtype, device=b.device)
        H = np.zeros((m + 1, m))
        V[0] = r / beta
        cs, sn = np.zeros(m), np.zeros(m)
        g = np.zeros(m + 1)
        g[0] = beta
        j_done = 0
        for j in range(m):
            z = precond(V[j])
            if keep_z:
                Z.append(z)
            w = matvec(z)
            h = dot(V[: j + 1], w)
            w = w - torch.tensordot(h, V[: j + 1], dims=1)
            h2 = dot(V[: j + 1], w)
            w = w - torch.tensordot(h2, V[: j + 1], dims=1)
            nw_t = torch.sqrt(dot(w, w))
            if nw_t.dtype != w.dtype:
                nw_t = nw_t.to(w.dtype)
            # one device -> host transfer per iteration (the Arnoldi column and the new norm together); the basis
            # vector is normalised on the device before the host has the norm
            V[j + 1] = w / torch.where(nw_t > 0, nw_t, torch.ones_like(nw_t))
            hn = torch.cat([(h + h2).double(), nw_t.double().reshape(1)]).cpu().numpy()
            hh, nw = hn[:-1], float(hn[-1])
            H[: j + 1, j] = hh
            H[j + 1, j] = nw
            for i in range(j):
                t = cs[i] * H[i, j] + sn[i] * H[i + 1, j]
                H[i + 1, j] = -sn[i] * H[i, j] + cs[i] * H[i + 1, j]
                H[i, j] = t
            den = np.hypot(H[j, j], H[j + 1, j])
            cs[j], sn[j] = (H[j, j] / den, H[j + 1, j] / den) if den > 0 else (1.0, 0.0)
            H[j, j] = cs[j] * H[j, j] + sn[j] * H[j + 1, j]
            H[j + 1, j] = 0.0
            g[j + 1] = -sn[j] * g[j]
            g[j] = cs[j] * g[j]
            total += 1
            j_done = j + 1
            if abs(g[j + 1]) <= tol or total >= maxiter or nw == 0:
                break
        y = np.linalg.solve(np.triu(H[:j_done, :j_done]) + 1e-300 * np.eye(j_done), g[:j_done])
        yt = torch.as_tensor(y, dtype=b.dtype, device=b.device)
        if keep_z:
            for j_, z_ in enumerate(Z[:j_done]):
                x.add_(z_, alpha=float(y[j_]))
            del Z
        else:
            x = x + precond(torch.tensordot(yt, V[:j_done], dims=1))
        res = abs(g[j_done])
        if res <= tol or total >= maxiter:
            return x, dict(it=total, rel=res / beta0)
        r = b - matvec(x)
        beta = torch.sqrt(dot(r, r)).item()
        if beta <= tol:
            return x, dict(it=total, rel=beta / beta0)


# ------------------------------------------------------------------------------------------------ first-order blocks
class CompactBlocks:
    """First-order coupled Jacobian of the laminar (Boussinesq) Warp residual, rows ``[u, v, w, p(_rgh), (T)]``,
    on the cell-blocked layout; ``assemble(flux, cfl)`` fills the fine level of the AMG."""

    def __init__(self, cs, beta=1.0):
        W = cs._warp.implicit
        app = W.app
        dev = cs.device
        self.W, self.app, self.dev = W, app, dev
        n, nb = int(W.n), int(W.nf)
        self.n, self.nb, self.energy = n, nb, bool(W.energy)
        cpd_cell = torch.full((W.n_cpd,), -1, dtype=torch.int64, device=dev)
        cpd_cell[W.cpd_of] = torch.arange(n, device=dev)
        C, F = app.C_senders.long(), app.F_receivers.long()
        o, nn = cpd_cell[C], cpd_cell[F]
        if bool((o < 0).any()):
            raise NotImplementedError("odil blocks: a face whose owner is not an interior cell")
        # the signed area of each face in the row of its owner (the cell-face CSR of the residual)
        rows = torch.repeat_interleave(torch.arange(n, device=dev), torch.diff(wp.to_torch(W.c_off).long()))
        faces, sarea = W._cfaces_t.long(), W._csarea_t
        s_own = torch.zeros(C.numel(), dtype=torch.float64, device=dev)
        is_own = o[faces] == rows
        s_own[faces[is_own]] = sarea[is_own]
        area = app.face_area.reshape(-1).to(torch.float64)
        if not torch.allclose(s_own.abs(), area, rtol=1e-10, atol=0.0):
            raise RuntimeError("odil blocks: owner-side cell-face area differs from the face area")
        ghosts, src, gA = W._keep_g[0], W._keep_g[1], W._gA_t
        gpos = torch.full((W.n_cpd,), -1, dtype=torch.int64, device=dev)
        gpos[ghosts] = torch.arange(ghosts.numel(), device=dev)
        bnd = nn < 0
        if bool((src[F[bnd]] != C[bnd]).any()):
            raise NotImplementedError("odil blocks: cyclic ghosts (a ghost whose source is not its face owner)")
        self.inner = torch.nonzero(~bnd).reshape(-1)
        self.bfaces = torch.nonzero(bnd).reshape(-1)
        self.o, self.nn = o, nn
        self.s = s_own                                     # owner-row signed area (neighbour row: -s)
        self.nrm = app.unv_face.to(torch.float64)
        self.gC, self.gF = app.gC.reshape(-1).to(torch.float64), app.gF.reshape(-1).to(torch.float64)
        self.delta = app.nonOrthDeltaCoeffs.reshape(-1).to(torch.float64)
        self.gA_b = gA[gpos[F[bnd]]]                       # [nbnd, nb, nb] ghost = A q_owner + b
        g = W.geom
        self.bnc = wp.to_torch(g.bnc).bool()
        self.mwi = wp.to_torch(g.mwi).bool()
        self.zeroflux = wp.to_torch(g.zeroflux).bool()
        self.vol = W._vol_t
        self.beta = float(beta)
        self.p = 3
        # the AMG graph: interior faces, weights |S| delta
        self.fo = o[self.inner].cpu().numpy()
        self.fn = nn[self.inner].cpu().numpy()
        self.fine = BlockLevel(n, self.fo, self.fn, nb, dev)
        self.weight = (area * self.delta)[self.inner].cpu().numpy()
        # Warp face data (the coefficient arrays alias the backend's persistent buffers)
        i32 = lambda t: wp.from_torch(t.to(torch.int32).contiguous())   # noqa: E731
        f64 = lambda t: wp.from_torch(t.to(torch.float64).contiguous())  # noqa: E731
        bidx = torch.full((C.numel(),), -1, dtype=torch.int64, device=dev)
        bidx[self.bfaces] = torch.arange(self.bfaces.numel(), device=dev)
        c = W._coef_t
        Fd = FaceData()
        self._keep_fd = [o.to(torch.int32), nn.to(torch.int32), bidx.to(torch.int32), self.s, self.nrm, self.gC,
                         self.gF, self.delta, self.gA_b.reshape(-1).contiguous()]
        Fd.o, Fd.nn, Fd.bidx = (wp.from_torch(t) for t in self._keep_fd[:3])
        Fd.s = f64(self.s)
        Fd.nrm = wp.from_torch(self.nrm.contiguous(), dtype=wp.vec3d)
        Fd.gC, Fd.gF, Fd.delta = f64(self.gC), f64(self.gF), f64(self.delta)
        Fd.nu, Fd.alpha, Fd.gpc = wp.from_torch(c["nu"]), wp.from_torch(c["alpha"]), wp.from_torch(c["gpc"])
        Fd.cc, Fd.ccont, Fd.ghb = wp.from_torch(c["cc"]), wp.from_torch(c["ccont"]), wp.from_torch(c["ghb"])
        Fd.bnc, Fd.mwi, Fd.zf = i32(self.bnc), i32(self.mwi), i32(self.zeroflux)
        Fd.gA = wp.from_torch(self._keep_fd[8])
        self.Fd = Fd
        self.inner_w = i32(self.inner)
        self.dcell_t = torch.zeros(n, dtype=torch.float64, device=dev)
        self.lam_t = torch.zeros(n, dtype=torch.float64, device=dev)
        self.wdev = wp.device_from_torch(dev)

    def coefficients(self):
        c = self.W._coef_t
        return c["nu"], c["alpha"], c["gpc"], c["cc"], c["ccont"], c["pcoef"], c["ghb"], c["ghbc"]

    def assemble(self, flux, cfl, uref=1.0):
        """Fine-level blocks of J1 + D / CFL (Warp kernels, deterministic); returns lam [n]."""
        W, Fd, n, nb, L = self.W, self.Fd, self.n, self.nb, self.fine
        flux = flux.contiguous()
        wf = wp.from_torch(flux)
        dcell, lam = wp.from_torch(self.dcell_t), wp.from_torch(self.lam_t)
        c = W._coef_t
        pcoef, ghbc = wp.from_torch(c["pcoef"]), wp.from_torch(c["ghbc"])
        wp.launch(k_ap_lam, dim=n, inputs=[W.c_off, W.c_face, W.c_sarea, Fd, wf, wp.from_torch(self.vol)],
                  outputs=[dcell, lam], device=self.wdev)
        shift = self.shift(self.lam_t, cfl, uref).reshape(-1).contiguous()
        e = 1 if self.energy else 0
        if L.nf:
            wp.launch(k_off_blocks, dim=L.nf, inputs=[self.inner_w, Fd, wf, dcell, pcoef, ghbc, nb, e],
                      outputs=[wp.from_torch(L.Aon_t.view(-1)), wp.from_torch(L.Ano_t.view(-1))], device=self.wdev)
        wp.launch(k_diag_blocks, dim=n, inputs=[W.c_off, W.c_face, W.c_sarea, Fd, wf, dcell, pcoef, ghbc,
                                                wp.from_torch(shift), nb, e],
                  outputs=[wp.from_torch(L.Dg_t.view(-1))], device=self.wdev)
        return self.lam_t

    def assemble_torch(self, flux, cfl, uref=1.0):
        """Reference (torch, float64 temporaries) of ``assemble``: fine-level blocks of J1 + D / CFL at the face normal velocity ``flux`` [n_face] (the residual's advecting
        flux); returns lam [n] (the pseudo-time weights)."""
        n, nb, dev, p = self.n, self.nb, self.dev, self.p
        nu, alpha, gpc, cc, ccont, pcoef, ghb, ghbc = self.coefficients()
        s, A = self.s, self.s.abs()
        o, nn = self.o, self.nn
        fl = torch.where(self.zeroflux, torch.zeros_like(flux), flux)
        Fv = fl * A                                        # volumetric face flux, owner -> neighbour
        # a_P and d = V / a_P (the MWI coefficient), lam (pseudo time)
        ap = torch.zeros(n, dtype=torch.float64, device=dev)
        lam = torch.zeros(n, dtype=torch.float64, device=dev)
        visc = nu * A * self.delta
        ap.index_add_(0, o, torch.clamp(Fv, min=0) + visc)
        lam.index_add_(0, o, 0.5 * Fv.abs() + visc)
        ii = self.inner
        ap.index_add_(0, nn[ii], torch.clamp(-Fv[ii], min=0) + visc[ii])
        lam.index_add_(0, nn[ii], 0.5 * Fv[ii].abs() + visc[ii])
        dcell = self.vol / ap
        dg = torch.zeros(n, nb, nb, dtype=torch.float64, device=dev)
        # ---- face derivatives Q_f(q_o, q_n) for the face quantities, per unit of the owner's signed area
        # momentum c: cc F u_f - nu delta (u_n - u_o); energy: cc F T_f - alpha delta (T_n - T_o); mass: ccont flux
        # flux = gC u_o.n + gF u_n.n - gpc d_f delta (p_n - p_o) + gpc d_f ghb delta (T_n - T_o)
        nf_all = flux.numel()
        dQo = torch.zeros(nf_all, nb, nb, dtype=torch.float64, device=dev)
        dQn = torch.zeros(nf_all, nb, nb, dtype=torch.float64, device=dev)
        up = torch.clamp(fl, min=0) * cc
        dn = torch.clamp(fl, max=0) * cc
        bnc = self.bnc
        up_o = torch.where(bnc, torch.zeros_like(up), up)
        dn_n = torch.where(bnc, cc * fl, dn)
        dnu = nu * self.delta
        for c in range(3):
            dQo[:, c, c] = up_o + dnu
            dQn[:, c, c] = dn_n - dnu
        df = torch.zeros(nf_all, dtype=torch.float64, device=dev)
        df[ii] = 0.5 * (dcell[o[ii]] + dcell[nn[ii]])
        bf = self.bfaces
        df[bf] = 0.5 * dcell[o[bf]]                        # the ghost's d is zero (dghost_kernel, non-cyclic)
        mw = (self.mwi & ~self.zeroflux).to(torch.float64)
        live = (~self.zeroflux).to(torch.float64)
        kp = gpc * df * self.delta * mw
        for c in range(3):
            dQo[:, p, c] = ccont * live * self.gC * self.nrm[:, c]
            dQn[:, p, c] = ccont * live * self.gF * self.nrm[:, c]
        dQo[:, p, p] = ccont * kp
        dQn[:, p, p] = -ccont * kp
        if self.energy:
            T = 4
            dQo[:, p, T] = -ccont * kp * ghb
            dQn[:, p, T] = ccont * kp * ghb
            dQo[:, T, T] = up_o + alpha * self.delta
            dQn[:, T, T] = dn_n - alpha * self.delta
        # ---- cell terms: pc V grad p and -ghbc V grad T as Gauss sums of the face values (gC, gF weights)
        # the cell gradient belongs to the ROW's cell, not the face: row i gets sum_f s_if n_f (w_o p_o + w_n p_n)
        sg = s[:, None] * self.nrm                                         # owner-outward S_f [nf, 3]
        # ---- boundary faces: the ghost column through the affine map, into the owner's diagonal
        gAb = self.gA_b
        dg.index_add_(0, o, s[:, None, None] * dQo)
        dg.index_add_(0, o[bf], s[bf, None, None] * torch.bmm(dQn[bf], gAb))
        # interior faces: owner row (o, n) and neighbour row (n, o) with the neighbour's signed area -s
        Aon = s[ii, None, None] * dQn[ii]
        Ano = -s[ii, None, None] * dQo[ii]
        dg.index_add_(0, nn[ii], -s[ii, None, None] * dQn[ii])
        # pressure / temperature gradient of the momentum rows (compact Gauss form)
        for c in range(3):
            # owner row: + pc_o S_c (gC p_o + gF p_n); neighbour row: - pc_n S_c (gC p_o + gF p_n)
            dg[:, c, p].index_add_(0, o[ii], pcoef[o[ii]] * sg[ii, c] * self.gC[ii])
            Aon[:, c, p] += pcoef[o[ii]] * sg[ii, c] * self.gF[ii]
            dg[:, c, p].index_add_(0, nn[ii], -pcoef[nn[ii]] * sg[ii, c] * self.gF[ii])
            Ano[:, c, p] += -pcoef[nn[ii]] * sg[ii, c] * self.gC[ii]
            # boundary: p_f = ghost p = (A q_o)_p
            dg[:, c, :].index_add_(0, o[bf], pcoef[o[bf], None] * sg[bf, c, None] * gAb[:, p, :])
            if self.energy and bool((ghbc != 0).any()):
                T = 4
                dg[:, c, T].index_add_(0, o[ii], -ghbc[o[ii]] * sg[ii, c] * self.gC[ii])
                Aon[:, c, T] += -ghbc[o[ii]] * sg[ii, c] * self.gF[ii]
                dg[:, c, T].index_add_(0, nn[ii], ghbc[nn[ii]] * sg[ii, c] * self.gF[ii])
                Ano[:, c, T] += ghbc[nn[ii]] * sg[ii, c] * self.gC[ii]
                dg[:, c, :].index_add_(0, o[bf], -ghbc[o[bf], None] * sg[bf, c, None] * gAb[:, T, :])
        # ---- pseudo time: lam / CFL on momentum (and T), lam / (CFL beta U^2) on continuity
        shift = self.shift(lam, cfl, uref)
        dg += torch.diag_embed(shift.T)
        L = self.fine
        L.Dg_t[:n].copy_(dg.float())
        L.Aon_t[: L.nf].copy_(Aon.float())
        L.Ano_t[: L.nf].copy_(Ano.float())
        return lam

    def shift(self, lam, cfl, uref=1.0):
        """[nb, n] pseudo-time diagonal (field-major)."""
        rows = [lam / cfl] * self.nb
        rows[self.p] = lam / (cfl * self.beta * uref * uref)
        return torch.stack(rows)


# ------------------------------------------------------------------------------------------------ driver
def solve_odil(cs, x0, *, maxit=300, atol=1e-9, cfl0=5.0, cfl_min=1.0, cfl_max=1e6, growth=2.0, reject=3.0,
               ser_exponent=1.0, max_du=0.5, uref=1.0, beta=1.0, restart=40, krylov=80, rtol=0.03, omega=0.7,
               pre=2, post=2, coarse_cells=800, passes=2, krylov_dtype=torch.float32, stop_rule=None, log=None,
               progress=None):
    """ODIL3D's steady inner iterations on ``cs`` (a CollocatedSolver on the laminar Warp backend) from ``x0``
    (field-blocked).  Returns ``(x, history)`` with the entry's history rows."""
    log = log or (lambda *a: None)
    dev = cs.device
    t_setup = time.perf_counter()
    blocks = CompactBlocks(cs, beta=beta)
    n, nb = blocks.n, blocks.nb
    hier = build_hierarchy(n, blocks.fo, blocks.fn, blocks.weight, coarse_target=coarse_cells, passes=passes)
    free = getattr(cs, "_gauge_free", None)
    closed = bool(cs.resolve_gauge(x0) if free is None and hasattr(cs, "resolve_gauge") else free)
    amg = AMG(blocks.fine, hier, omega=omega, pre=pre, post=post, p_index=blocks.p if closed else None)
    setup_s = time.perf_counter() - t_setup
    log(f"odil: {n} cells x {nb} fields, AMG {amg.describe()} (setup {setup_s:.2f} s), CFL {cfl0:g} (SER x{growth:g}, "
        f"max {cfl_max:g}), GMRES({restart}) <= {krylov} to {rtol:g} in {str(krylov_dtype).split('.')[-1]}")
    vol = blocks.vol
    h2 = vol ** (2.0 / 3.0)
    wmom = 1.0 / (uref ** 2 * h2)
    wcon = 1.0 / (uref * h2)
    W = cs._warp
    app = cs.solver

    def residual(x):
        R = cs.residual(x).detach()
        return R, app.face_volumetric_flux.reshape(-1).to(torch.float64)

    def norms(R):
        Rv = R.view(nb, n)
        rm = torch.sqrt((((Rv[:3] * wmom) ** 2).sum(1)) / n).norm().item()
        rc = math.sqrt(float(((Rv[3] * wcon) ** 2).sum()) / n)
        return rm, rc

    def remove_mean_p(x):
        if closed:
            xv = x.view(nb, n)
            xv[3] -= (xv[3] * vol).sum() / vol.sum()

    x = x0.detach().clone().to(torch.float64)
    remove_mean_p(x)
    R, flux = residual(x)
    rm, rc = norms(R)
    r0 = max(rm, rc)
    rprev = r0
    cfl = float(cfl0)
    cfl_ref = (cfl, r0)
    hist = []
    rejected = 0
    lin_total = 0
    t0 = time.perf_counter()
    prof = dict(assemble=0.0, amg=0.0, krylov=0.0, residual=0.0)
    fine_prof = os.environ.get("LPCNS_ODIL_PROFILE", "0") == "1"
    xb = torch.zeros(n, nb, dtype=torch.float32, device=dev)
    yb = torch.zeros(n, nb, dtype=torch.float32, device=dev)
    wxb, wyb = blocks.fine.wv(xb), blocks.fine.wv(yb)
    it = 0
    for it in range(1, maxit + 1):
        if r0 == 0 or max(rm, rc) <= atol:
            it -= 1
            break
        if stop_rule is not None and hist and stop_rule(hist[-1]):
            it -= 1
            break
        ta = time.perf_counter()
        if it == int(os.environ.get("LPCNS_ODIL_CHECK_STEP", "5")) and os.environ.get("LPCNS_ODIL_CHECK", "0") == "1":
            blocks.assemble_torch(flux, cfl, uref)
            ref = [t.clone() for t in (blocks.fine.Dg_t, blocks.fine.Aon_t, blocks.fine.Ano_t)]
            for t in (blocks.fine.Dg_t, blocks.fine.Aon_t, blocks.fine.Ano_t):
                t.zero_()
            blocks.assemble(flux, cfl, uref)
            for name, a, b in zip(("Dg", "Aon", "Ano"), (blocks.fine.Dg_t, blocks.fine.Aon_t, blocks.fine.Ano_t), ref):
                log(f"odil check {name}: max |warp - torch| {float((a - b).abs().max()):.3e} of max {float(b.abs().max()):.3e}")
        lam = blocks.assemble(flux, cfl, uref)
        tb = time.perf_counter()
        amg.update()
        torch.cuda.synchronize(dev)
        tc = time.perf_counter()
        prof["assemble"] += tb - ta
        prof["amg"] += tc - tb
        shift = blocks.shift(lam, cfl, uref).reshape(-1)          # field-major, as x
        P = W.product(cs, x, "implicit")

        def op(v):
            if fine_prof:
                torch.cuda.synchronize(dev)
                t_ = time.perf_counter()
            vd = v.to(torch.float64)
            out = (P(vd) + shift * vd).to(v.dtype)
            if fine_prof:
                torch.cuda.synchronize(dev)
                prof["jvp"] = prof.get("jvp", 0.0) + time.perf_counter() - t_
            return out

        def precond(v):
            if fine_prof:
                torch.cuda.synchronize(dev)
                t_ = time.perf_counter()
            xb.copy_(v.view(nb, n).T)
            amg.vcycle(wxb, wyb)
            z = yb.T.reshape(-1).to(v.dtype).clone()
            if closed:
                z.view(nb, n)[3] -= z.view(nb, n)[3].mean()
            if fine_prof:
                torch.cuda.synchronize(dev)
                prof["vcycle"] = prof.get("vcycle", 0.0) + time.perf_counter() - t_
            return z

        b = (-R).to(krylov_dtype)
        dq, info = gmres(op, precond, b, restart=restart, maxiter=krylov, rtol=rtol)
        dq = dq.to(torch.float64)
        torch.cuda.synchronize(dev)
        td = time.perf_counter()
        prof["krylov"] += td - tc
        lin_total += info["it"]
        if info["rel"] > 0.5:
            cfl = max(cfl * 0.5, cfl_min)
            cfl_ref = (cfl, rprev)
        du = dq.view(nb, n)[:3].abs().max().item()
        lim = max_du * uref
        if du > lim:
            dq = dq * (lim / du)
        x_old = x.clone()
        x = x + dq
        remove_mean_p(x)
        R, flux_new = residual(x)
        rm, rc = norms(R)
        r = max(rm, rc)
        prof["residual"] += time.perf_counter() - td
        event = ""
        if not math.isfinite(r) or r > reject * rprev:
            x = x_old
            R, flux = residual(x)
            rm, rc = norms(R)
            r = max(rm, rc)
            cfl = max(cfl * 0.25, cfl_min)
            cfl_ref = (cfl, r)
            rejected += 1
            event = "reject"
            if rejected > 20:
                log("odil: 20 rejections, stopping")
                break
        else:
            flux = flux_new
            c_ref, r_ref = cfl_ref
            target = c_ref * (r_ref / max(r, 1e-300)) ** ser_exponent
            target = min(target, cfl * growth)
            cfl = min(max(target, cfl_min), cfl_max)
        rprev = r
        el = time.perf_counter() - t0
        row = dict(it=it, max_abs=float(R.abs().max()), l2=float(R.norm()), merit=r, lambda_=1.0,
                   inner_iterations=info["it"], inner_rtol=info["rel"], reassembled=True, stalled=False,
                   ptc_theta=1.0 / cfl, ptc_event=event, cfl=cfl, seconds=el, r_mom=rm, r_cont=rc,
                   rejected=bool(event))
        row["lambda"] = row.pop("lambda_")
        hist.append(row)
        log(f"odil {it:3d}  r_mom {rm:.3e}  r_cont {rc:.3e}  CFL {cfl:.3g}  gmres {info['it']:3d} "
            f"(rel {info['rel']:.2e})  max|R| {row['max_abs']:.2e}  wall {el:6.2f}s {event}")
        if progress is not None:
            try:
                progress(row)
            except Exception:                                    # noqa: BLE001
                pass
    el = time.perf_counter() - t0
    log(f"odil: {it} steps, {lin_total} GMRES iterations, {rejected} rejected, {el:.2f} s (setup {setup_s:.2f} s); "
        f"r_mom {rm:.3e} r_cont {rc:.3e}; profile assemble {prof['assemble']:.2f} amg {prof['amg']:.2f} "
        f"krylov {prof['krylov']:.2f} residual {prof['residual']:.2f}"
        + (f" (jvp {prof.get('jvp', 0):.2f} vcycle {prof.get('vcycle', 0):.2f})" if fine_prof else ""))
    return x, hist


# ------------------------------------------------------------------------------------------------ data-centre (k-eps)
def _sub_amg_diag(blocks, hier, spc, log, tag, omega=0.7, pre=2, post=2, cycles=20):
    """Diagnostics: for each field group's diagonal block of the current (shifted) compact matrix, the stationary
    V-cycle contraction of its own AMG and GMRES(30) iterations to 1e-3 -- which block's AMG fails at large CFL."""
    n, dev = blocks.n, blocks.dev
    F = blocks.fine
    nf = F.nf
    l1 = float(os.environ.get("LPCNS_ODIL_L1", "0"))
    groups = {g_: v_ for g_, v_ in {"U": [0, 1, 2], "u": [0], "T": [4], "k-eps": [5, 6], "U-oseen": "oseen"}.items()
              if g_ in os.environ.get("LPCNS_ODIL_SUB_GROUPS", "U,u,T,k-eps,U-oseen").split(",")}
    levels = []
    D0, On0, No0 = F.Dg_t[:n], F.Aon_t[:nf], F.Ano_t[:nf]
    # M-matrix indicators of the u-u block: rows not weakly diagonally dominant, positive off-diagonals
    duu = D0[:, 0, 0].double()
    offs = torch.zeros(n, dtype=torch.float64, device=dev)
    fo_t = torch.as_tensor(np.asarray(blocks.fo), device=dev).long()
    fn_t = torch.as_tensor(np.asarray(blocks.fn), device=dev).long()
    offs.index_add_(0, fo_t, On0[:, 0, 0].double().abs())
    offs.index_add_(0, fn_t, No0[:, 0, 0].double().abs())
    offs += D0[:, 0, 1].double().abs() + D0[:, 0, 2].double().abs()
    pos = float(((On0[:, 0, 0] > 0).sum() + (No0[:, 0, 0] > 0).sum()).item()) / (2 * nf)
    log(f"odil-dc subdiag {tag} u-u block: rows not diagonally dominant {float((duu < offs).double().mean()):.3f}, "
        f"rows with u-u diag <= 0 {float((duu <= 0).double().mean()):.4f}, positive off-diagonals {pos:.3f}; "
        f"|D_uv|/|D_uu| mean {float((D0[:, 0, 1].double().abs() / duu.abs().clamp_min(1e-30)).mean()):.3f}")
    for name, g in groups.items():
        if g == "oseen":
            # the frozen-flux (Oseen) proxy: the temperature row's own convection-diffusion block for every velocity
            # component, scaled to the u-u diagonal, no component coupling
            L = BlockLevel(n, blocks.fo, blocks.fn, 3, dev)
            sc = (D0[:, 0, 0] / D0[:, 4, 4].clamp_min(1e-30)).clamp(0.1, 10.0)
            for c in range(3):
                L.Dg_t[:n, c, c] = D0[:, 4, 4] * sc
                L.Aon_t[:nf, c, c] = On0[:, 4, 4] * sc[fo_t]
                L.Ano_t[:nf, c, c] = No0[:, 4, 4] * sc[fn_t]
        else:
            gi = torch.tensor(g, device=dev)
            L = BlockLevel(n, blocks.fo, blocks.fn, len(g), dev)
            L.Dg_t[:n].copy_(D0[:, gi][:, :, gi])
            L.Aon_t[:nf].copy_(On0[:, gi][:, :, gi])
            L.Ano_t[:nf].copy_(No0[:, gi][:, :, gi])
        A = AMG(L, hier, omega=omega, pre=pre, post=post, l1=l1)
        A.update()
        levels.append((name, L, A))
    if spc is not None:
        spc.update()
        levels.append(("p-Schur", spc.Lp, spc.amg_p))
    for name, L, A in levels:
        k = L.nb
        b = torch.randn(n, k, dtype=torch.float32, device=dev)
        x = torch.zeros_like(b)
        y = torch.zeros_like(b)
        r_ = torch.zeros_like(b)
        z_ = torch.zeros_like(b)
        wx, wy, wr, wz = L.wv(x), L.wv(y), L.wv(r_), L.wv(z_)
        nr0 = float(b.norm())
        hist = []
        for _ in range(cycles):
            L.matvec(wx, wy)
            r_.copy_(b - y)
            hist.append(float(r_.norm()) / nr0)
            A.vcycle(wr, wz)
            x.add_(z_)
        rate = (hist[-1] / max(hist[0], 1e-30)) ** (1.0 / max(cycles - 1, 1))

        def mv(v, L=L, k=k):
            xx = v.view(n, k).float().contiguous()
            yy = torch.zeros_like(xx)
            L.matvec(L.wv(xx), L.wv(yy))
            return yy.reshape(-1).to(v.dtype)

        def pc(v, L=L, A=A, k=k):
            bb = v.view(n, k).float().contiguous()
            zz = torch.zeros_like(bb)
            A.vcycle(L.wv(bb), L.wv(zz))
            return zz.reshape(-1).to(v.dtype)
        _, inf_ = gmres(mv, pc, b.reshape(-1), restart=30, maxiter=150, rtol=1e-3)
        log(f"odil-dc subdiag {tag} {name:8s}: V-cycle contraction {rate:.3f} (|r| after {cycles}: {hist[-1]:.1e}), "
            f"GMRES(30) {inf_['it']} its rel {inf_['rel']:.1e}")


def _t_only_solve(cs, W, blocks, hier, x, sT, log, qoi=None, iters=3, rtol=1e-3, maxit=200, omega=0.7, pre=2, post=2):
    """Diagnostic / accelerator: the temperature rows alone (U, p, k, eps held), Newton on R_T(x) with the exact JVP
    restricted to the T block, preconditioned by a scalar AMG of the compact T-T block plus the shift sT ([n]).
    Returns the new x."""
    n, dev, F = blocks.n, blocks.dev, blocks.fine
    nf, nb = F.nf, blocks.nb
    L = BlockLevel(n, blocks.fo, blocks.fn, 1, dev)
    L.Dg_t[:n, 0, 0] = blocks.Dbase[:, 4, 4] + sT.float()
    L.Aon_t[:nf, 0, 0] = F.Aon_t[:nf, 4, 4]
    L.Ano_t[:nf, 0, 0] = F.Ano_t[:nf, 4, 4]
    A = AMG(L, hier, omega=omega, pre=pre, post=post, l1=float(os.environ.get("LPCNS_ODIL_L1", "0")))
    A.update()
    sl = slice(4 * n, 5 * n)
    for k_ in range(iters):
        R = cs.residual(x).detach()
        rT = R[sl]
        msg = f"|R_T| {float(rT.norm()):.3e} |R_rest| {float(torch.cat([R[:4 * n], R[5 * n:]]).norm()):.3e}"
        if qoi is not None:
            out, _ = qoi(x)
            msg += f" heat {float(out['heat_W:total']):.0f} W"
        log(f"odil-dc T-solve {k_}: {msg}")
        P = W.product(cs, x, "implicit")

        def opT(v, P=P):
            e = torch.zeros(nb * n, dtype=torch.float64, device=dev)
            e[sl] = v.to(torch.float64)
            return (P(e)[sl] + sT * v.to(torch.float64)).to(v.dtype)

        def pcT(v):
            bb = v.view(n, 1).float().contiguous()
            zz = torch.zeros_like(bb)
            A.vcycle(L.wv(bb), L.wv(zz))
            return zz.reshape(-1).to(v.dtype)
        t_ = time.perf_counter()
        dT, inf_ = gmres(opT, pcT, -rT, restart=int(os.environ.get("LPCNS_ODIL_TSOLVE_RESTART", "30")), maxiter=maxit,
                          rtol=rtol)
        log(f"odil-dc T-solve {k_}: GMRES {inf_['it']} rel {inf_['rel']:.1e} max|dT| {float(dT.abs().max()):.3f} "
            f"({time.perf_counter() - t_:.2f} s)")
        x = x.clone()
        x[sl] += dT.to(x.dtype)
    R = cs.residual(x).detach()
    msg = f"|R_T| {float(R[sl].norm()):.3e} |R_rest| {float(torch.cat([R[:4 * n], R[5 * n:]]).norm()):.3e}"
    if qoi is not None:
        out, _ = qoi(x)
        msg += f" heat {float(out['heat_W:total']):.0f} W"
    log(f"odil-dc T-solve end: {msg}")
    return x


class SimplePC:
    """SIMPLE-type field-split preconditioner on the 7 x 7 compact blocks (Elman et al. 2008; Klaij & Vuik 2013):
    velocity block AMG (3 x 3), pressure Schur complement S = A_pp - A_pu D_u^-1 A_up (D_u the velocity diagonal
    block; the product kept on the 1-hop pattern) by a scalar AMG, the SIMPLE velocity correction, then the
    remaining rows (T, k, eps) by a 3 x 3 AMG on the lower block-triangular residual.  All AMGs share the hierarchy."""

    def __init__(self, blocks, hier, omega=0.7, pre=2, post=2, l1=0.0):
        self.b = blocks
        n, dev = blocks.n, blocks.dev
        self.n = n
        F = blocks.fine
        self.Lu = BlockLevel(n, blocks.fo, blocks.fn, 3, dev)
        self.Lp = BlockLevel(n, blocks.fo, blocks.fn, 1, dev)
        self.Lr = BlockLevel(n, blocks.fo, blocks.fn, 3, dev)
        self.amg_u = AMG(self.Lu, hier, omega=omega, pre=pre, post=post, l1=l1)
        self.amg_p = AMG(self.Lp, hier, omega=omega, pre=pre, post=post, l1=l1)
        self.amg_r = AMG(self.Lr, hier, omega=omega, pre=pre, post=post, l1=l1)
        self.fo = torch.as_tensor(np.asarray(blocks.fo), device=dev).long()
        self.fn = torch.as_tensor(np.asarray(blocks.fn), device=dev).long()
        self.nf = F.nf
        z = lambda k: torch.zeros(n, k, dtype=torch.float32, device=dev)   # noqa: E731
        self.bu, self.xu, self.bp, self.xp, self.br, self.xr = z(3), z(3), z(1), z(1), z(3), z(3)
        self.w = {k: (self.Lu.wv(t) if t.shape[1] == 3 and k[1] == "u" else
                      self.Lp.wv(t) if t.shape[1] == 1 else self.Lr.wv(t))
                  for k, t in (("bu", self.bu), ("xu", self.xu), ("bp", self.bp), ("xp", self.xp),
                               ("br", self.br), ("xr", self.xr))}
        self.v7 = z(7)
        self.y7 = z(7)
        self.wv7, self.wy7 = F.wv(self.v7), F.wv(self.y7)

    def update(self):
        F, n, nf = self.b.fine, self.n, self.nf
        D, Aon, Ano = F.Dg_t[:n], F.Aon_t[:nf], F.Ano_t[:nf]
        u, r = slice(0, 3), slice(4, 7)
        self.Lu.Dg_t[:n].copy_(D[:, u, u]); self.Lu.Aon_t[:nf].copy_(Aon[:, u, u]); self.Lu.Ano_t[:nf].copy_(Ano[:, u, u])
        self.Lr.Dg_t[:n].copy_(D[:, r, r]); self.Lr.Aon_t[:nf].copy_(Aon[:, r, r]); self.Lr.Ano_t[:nf].copy_(Ano[:, r, r])
        Dd = D.double()
        self.Duinv = torch.linalg.inv(Dd[:, u, u])                           # [n,3,3]
        Gd, Dvd = Dd[:, u, 3:4], Dd[:, 3:4, u]                               # u rows p col / p row u cols (diag)
        Gon, Gno = Aon[:, u, 3:4].double(), Ano[:, u, 3:4].double()
        Don, Dno = Aon[:, 3:4, u].double(), Ano[:, 3:4, u].double()
        fo, fn = self.fo, self.fn
        Sd = Dd[:, 3, 3] - (Dvd @ self.Duinv @ Gd)[:, 0, 0]
        Sd = Sd.index_add(0, fo, -(Don @ self.Duinv[fn] @ Gno)[:, 0, 0])
        Sd = Sd.index_add(0, fn, -(Dno @ self.Duinv[fo] @ Gon)[:, 0, 0])
        Son = Aon[:, 3, 3].double() - (Dvd[fo] @ self.Duinv[fo] @ Gon)[:, 0, 0] - (Don @ self.Duinv[fn] @ Gd[fn])[:, 0, 0]
        Sno = Ano[:, 3, 3].double() - (Dvd[fn] @ self.Duinv[fn] @ Gno)[:, 0, 0] - (Dno @ self.Duinv[fo] @ Gd[fo])[:, 0, 0]
        self.Lp.Dg_t[:n, 0, 0] = Sd.float()
        self.Lp.Aon_t[:nf, 0, 0] = Son.float()
        self.Lp.Ano_t[:nf, 0, 0] = Sno.float()
        self.Duinv_f = self.Duinv.float()
        self.amg_u.update()
        self.amg_p.update()
        self.amg_r.update()

    def _mv7(self, zu=None, zp=None):
        """A (7 x 7 compact, shifted) times a vector with only the velocity / pressure parts set."""
        self.v7.zero_()
        if zu is not None:
            self.v7[:, 0:3] = zu
        if zp is not None:
            self.v7[:, 3:4] = zp
        self.b.fine.matvec(self.wv7, self.wy7)
        return self.y7

    def apply(self, v):
        """v: [n, 7] float32 -> z [n, 7]."""
        z = torch.empty_like(v)
        self.bu.copy_(v[:, 0:3])
        self.amg_u.vcycle(self.w["bu"], self.w["xu"])                       # u* = A_uu^-1 r_u
        us = self.xu.clone()
        y = self._mv7(zu=us)
        self.bp.copy_(v[:, 3:4] - y[:, 3:4])                                # r_p - A_pu u*
        self.amg_p.vcycle(self.w["bp"], self.w["xp"])                       # S dp = ...
        dp = self.xp.clone()
        y = self._mv7(zp=dp)
        z[:, 0:3] = us - torch.bmm(self.Duinv_f, y[:, 0:3].unsqueeze(-1)).squeeze(-1)   # u = u* - D_u^-1 A_up dp
        z[:, 3:4] = dp
        y = self._mv7(zu=z[:, 0:3].contiguous(), zp=dp)
        self.br.copy_(v[:, 4:7] - y[:, 4:7])                                # lower triangular: r_rest - A_r,(u,p) z
        self.amg_r.vcycle(self.w["br"], self.w["xr"])
        z[:, 4:7] = self.xr
        return z


class PicardBlocks:
    """The preconditioner matrix of the data-centre backend: the backend's exact 1-hop Picard Jacobian
    (``compact_jacobian(x, "picard")``: cell gradients and d_f frozen; every boundary condition, cyclic partner, rack
    pair and k-epsilon source in it), re-laid onto the AMG's edge list once (each unordered coupled pair one edge),
    stored in float32; the pseudo-time diagonal is added per step (``set_shift``) without a new assembly."""

    def __init__(self, cs, x0, mode="picard"):
        self.W = cs._warp
        self.cs = cs
        self.mode = mode
        dev = cs.device
        cj = self._build(x0)
        A = cj.A
        n, nb = int(cj.n), int(A.val.shape[1])
        self.n, self.nb, self.dev = n, nb, dev
        row, col = A.row.long(), A.col.long()
        off = row != col
        pos = torch.nonzero(off).reshape(-1)
        i, j = row[off], col[off]
        lo, hi = torch.minimum(i, j), torch.maximum(i, j)
        uk, inv = torch.unique(lo * n + hi, return_inverse=True)
        E = int(uk.numel())
        self.ij = torch.full((E,), -1, dtype=torch.long, device=dev)      # block (lo, hi): owner row, neighbour col
        self.ji = torch.full((E,), -1, dtype=torch.long, device=dev)      # block (hi, lo)
        fwd = i < j
        self.ij[inv[fwd]] = pos[fwd]
        self.ji[inv[~fwd]] = pos[~fwd]
        self.fo = (uk // n).cpu().numpy()
        self.fn = (uk % n).cpu().numpy()
        self.diag = cj.diag.long()
        self.fine = BlockLevel(n, self.fo, self.fn, nb, dev)
        v = A.val
        p = 3
        w = torch.zeros(E, dtype=torch.float64, device=dev)
        m = self.ij >= 0
        w[m] += v[self.ij[m], p, p].abs()
        m = self.ji >= 0
        w[m] += v[self.ji[m], p, p].abs()
        med = w[w > 0].median() if bool((w > 0).any()) else torch.tensor(1.0, device=dev)
        w = torch.where(w > 0, w, med)
        self.weight = w.cpu().numpy()                     # the pressure-Laplacian strength of each coupled pair
        self.Dbase = torch.zeros(n, nb, nb, dtype=torch.float32, device=dev)
        self.dsig = torch.zeros(nb, n, dtype=torch.float64, device=dev)
        self.seconds = 0.0
        self.colours = int(cj.colours)
        self.load(cj)
        if os.environ.get("LPCNS_ODIL_BLOCKNORMS", "0") == "1":
            names = ["u", "v", "w", "p", "T", "k", "e"]
            D = self.Dbase.abs().double().mean(0)
            O = self.fine.Aon_t[:E].abs().double().mean(0)
            print("odil-dc block |.| mean, diagonal blocks (row x col):", flush=True)
            for r in range(nb):
                print("  " + names[r] + " " + " ".join(f"{float(D[r, c]):9.2e}" for c in range(nb)), flush=True)
            print("odil-dc block |.| mean, off-diagonal blocks (row x col):", flush=True)
            for r in range(nb):
                print("  " + names[r] + " " + " ".join(f"{float(O[r, c]):9.2e}" for c in range(nb)), flush=True)

    def load(self, cj):
        v = cj.A.val
        L, E = self.fine, int(self.ij.numel())
        if os.environ.get("LPCNS_ODIL_BLOCK_FINITE", "0") == "1":
            # diagnostics: which (row field, column field) entries of the built Jacobian are not finite in float64,
            # and which overflow the float32 copy the preconditioner keeps
            names = ["u", "v", "w", "p", "T", "k", "e"][:self.nb]
            bad64 = (~torch.isfinite(v)).sum(0)
            bad32 = (~torch.isfinite(v.float())).sum(0) - bad64
            amax = torch.where(torch.isfinite(v), v.abs(), torch.zeros_like(v)).amax(0)
            msg = [f"{names[r]}{names[c]}: {int(bad64[r, c])}/{int(bad32[r, c])} max {float(amax[r, c]):.2e}"
                   for r in range(self.nb) for c in range(self.nb) if bad64[r, c] or bad32[r, c] or amax[r, c] > 1e30]
            print("odil-dc block finiteness (non-finite f64 / extra f32 overflow): " + ("; ".join(msg) or "all finite"),
                  flush=True)
        self.Dbase.copy_(v[self.diag].float())
        idx = torch.arange(self.nb, device=self.dev)
        self.dsig.copy_(v[self.diag[:, None], idx[None, :], idx[None, :]].T)
        for dst, sel in ((L.Aon_t, self.ij), (L.Ano_t, self.ji)):
            m = sel >= 0
            dst[:E].zero_()
            dst[:E][m] = v[sel[m]].float()
        if os.environ.get("LPCNS_ODIL_TURB_DECOUPLE", "0") == "1" and self.nb == 7:
            # preconditioner only (the JVP stays exact): k and epsilon rows keep their own transport + destruction
            # (k-k, eps-eps), the k<->eps source couplings and the mean-flow <-> turbulence couplings are dropped
            # (Patankar-type positive linearisation, loosely coupled turbulence in the preconditioner)
            for T_ in (self.Dbase, L.Aon_t[:E], L.Ano_t[:E]):
                T_[:, 5:, :5] = 0.0
                T_[:, :5, 5:] = 0.0
                T_[:, 5, 6] = 0.0
                T_[:, 6, 5] = 0.0
        self.seconds = float(cj.seconds)

    def _build(self, x):
        """mode picard | frozen | hybrid (Picard values with the momentum rows' pressure columns of the frozen
        Jacobian: the pressure-gradient coupling the frozen cell gradients drop from the Picard one)."""
        if self.mode != "hybrid":
            return self.W.compact_jacobian(x, mode=self.mode)
        # LPCNS_ODIL_HYBRID_BASE=oseen: the Picard values with the convecting face flux frozen as well
        cp = self.W.compact_jacobian(x, mode=os.environ.get("LPCNS_ODIL_HYBRID_BASE", "picard"))
        # the momentum rows' pressure columns are linear in p with fixed geometric weights (the frozen-gradient
        # pressure force): built once from the frozen Jacobian and reused (LPCNS_ODIL_UP_CACHE=0 rebuilds them)
        cache = os.environ.get("LPCNS_ODIL_UP_CACHE", "0") == "1"   # off: they change with the state (UP_CHECK 1.15)
        if not cache or getattr(self, "_up", None) is None or os.environ.get("LPCNS_ODIL_UP_CHECK", "0") == "1":
            # LPCNS_ODIL_UP_PCOL=1: probe only the pressure column field (distance-3 colours x 1 instead of x 7);
            # LPCNS_ODIL_UP_PCOL_CHECK=1 also builds the full frozen Jacobian once and logs the difference
            pcol = os.environ.get("LPCNS_ODIL_UP_PCOL", "0") == "1"
            cf = self.W.compact_jacobian(x, mode="frozen", **({"fields": (3,)} if pcol else {}))
            assert torch.equal(cp.A.row, cf.A.row) and torch.equal(cp.A.col, cf.A.col)
            up = cf.A.val[:, 0:3, 3].clone()
            if pcol and os.environ.get("LPCNS_ODIL_UP_PCOL_CHECK", "0") == "1":
                cfull = self.W.compact_jacobian(x, mode="frozen")
                ref = cfull.A.val[:, 0:3, 3]
                print(f"odil-dc u-p columns, pressure-only probes against the full frozen build: max abs diff "
                      f"{float((up - ref).abs().max()):.3e} (max |ref| {float(ref.abs().max()):.3e}); "
                      f"{float(cf.seconds):.2f} s against {float(cfull.seconds):.2f} s", flush=True)
                del cfull
            if getattr(self, "_up", None) is not None and os.environ.get("LPCNS_ODIL_UP_CHECK", "0") == "1":
                d = float((up - self._up).abs().max() / up.abs().max().clamp_min(1e-300))
                print(f"odil-dc u-p columns: max rel change since setup {d:.3e}", flush=True)
            self._up = up
            cp.seconds = float(cp.seconds) + float(cf.seconds)
            del cf
        cp.A.val[:, 0:3, 3] = self._up
        ptan = os.environ.get("LPCNS_ODIL_POROUS_TAN", "0")
        if ptan in ("1", "2"):
            dpp = None
            if ptan == "2":
                # the continuity rows too: their coefficient of the partner's pressure through the cell gradients (the
                # face flux's interpolated-gradient part) is the frozen-coefficient value minus the Picard one
                cf2 = self.W.compact_jacobian(x, mode="frozen", fields=(3,))
                dpp = (cf2.A.val[:, 3, 3] - cp.A.val[:, 3, 3]).clone()
                cp.seconds = float(cp.seconds) + float(cf2.seconds)
                del cf2
            self._porous_tangent(cp, x, dpp)
        return cp

    def _porous_tangent(self, cp, x, dpp=None):
        """LPCNS_ODIL_POROUS_TAN=1: the resistance of the porous baffles in the momentum rows.  The ghost pressure
        across a ``porousBafflePressure`` face is the partner's pressure plus ``sign * J(Un)``,
        ``J = -(c0 + c1 |Un|) Un length``, ``Un`` the normal component of the mean of the two cells' velocities
        (``dc_porous_kernel``).  The momentum rows read it through the cell pressure gradient, whose tangent the
        Picard values drop; the hybrid matrix restores the pressure columns only.  Chain rule on those columns: the
        row cell's coefficient of the partner's pressure times d p_ghost / dU of either cell, added to the velocity
        columns of the row's diagonal block and of its block to the partner."""
        import warp as wp
        W, A = getattr(self.W, "W", self.W), cp.A               # the residual object behind the backend
        if not int(getattr(W, "n_porous", 0)):
            if not getattr(self, "_porous_logged", False):
                self._porous_logged = True
                print(f"odil-dc porous tangent: no porous baffle face in the backend ({type(W).__name__})", flush=True)
            return
        n, dev = int(cp.n), A.val.device
        interior = self.cs.interior.long()
        local = torch.full((int(self.cs.solver.cpd_cell_pos.shape[0]),), -1, dtype=torch.long, device=dev)
        local[interior] = torch.arange(n, device=dev)
        ca, cb, src = (wp.to_torch(t).long() for t in (W.pz_a, W.pz_b, W.pz_src))
        nv = wp.to_torch(W.pz_n).to(torch.float64).reshape(-1, 3)
        sign, c0, c1, ln = (wp.to_torch(t).to(torch.float64) for t in (W.pz_sign, W.pz_c0, W.pz_c1, W.pz_len))
        ia, ib, isrc = local[ca], local[cb], local[src]
        ic = torch.where(ia == isrc, ib, ia)
        ok = (ia >= 0) & (ib >= 0) & (ic != isrc)
        U = x.detach().to(torch.float64).view(-1, n)[:3].T
        un = ((0.5 * (U[ia.clamp_min(0)] + U[ib.clamp_min(0)])) * nv).sum(1)
        dpg = -(c0 + 2.0 * c1 * un.abs()) * ln * sign                    # d p_ghost / d Un
        key = A.row.long() * n + A.col.long()
        e = torch.searchsorted(key, ic * n + isrc).clamp_max(key.numel() - 1)
        ok &= key[e] == ic * n + isrc
        e, ic_, w = e[ok], ic[ok], (0.5 * dpg[:, None] * nv)[ok]         # d p_ghost / dU_a = d p_ghost / dU_b
        cnt = torch.zeros(key.numel(), dtype=torch.float64, device=dev).index_add_(
            0, e, torch.ones(e.numel(), dtype=torch.float64, device=dev))
        coef = A.val[e, 0:3, 3].to(torch.float64) / cnt[e][:, None]      # the pair's faces share its pressure column
        add = (coef[:, :, None] * w[:, None, :]).to(A.val.dtype)
        blk = A.val[:, 0:3, 0:3]
        blk.index_add_(0, cp.diag.long()[ic_], add)
        blk.index_add_(0, e, add)
        if dpp is not None:
            add3 = ((dpp[e].to(torch.float64) / cnt[e])[:, None] * w).to(A.val.dtype)
            row3 = A.val[:, 3, 0:3]
            row3.index_add_(0, cp.diag.long()[ic_], add3)
            row3.index_add_(0, e, add3)
        if not getattr(self, "_porous_logged", False):
            self._porous_logged = True
            print(f"odil-dc porous tangent: {int(ok.sum())} of {int(ok.numel())} baffle faces, |Un| up to "
                  f"{float(un.abs().max()):.2f} m/s, added |block| up to {float(add.abs().max()):.3e} against "
                  f"diagonal momentum |block| median {float(A.val[cp.diag.long()[ic_], 0:3, 0:3].abs().amax((1, 2)).median()):.3e}",
                  flush=True)

    def rebuild(self, x):
        cj = self._build(x)
        self.load(cj)
        del cj

    def set_shift(self, shift):
        """Dg = J_ii + diag(shift) (shift [nb, n], field-major)."""
        self.fine.Dg_t[: self.n].copy_(self.Dbase)
        self.fine.Dg_t[: self.n].diagonal(dim1=1, dim2=2).add_(shift.T.float())
        dom = os.environ.get("LPCNS_ODIL_DOMFIX")
        if dom:
            # preconditioner only (the operator stays the exact JVP): every listed row's diagonal raised to at least
            # its shift + the sum of |same-field| off-diagonals (face neighbours) + |intra-cell couplings to the other
            # listed rows of its group| -- the M-matrix (Patankar) fix; large-CFL rows of the momentum block lose
            # diagonal dominance (non-solenoidal iterate fluxes, Newton convection terms) and aggregation AMG diverges
            F, n, nf = self.fine, self.n, self.fine.nf
            if not hasattr(self, "_fo_t"):
                self._fo_t = torch.as_tensor(np.asarray(self.fo), device=self.dev).long()
                self._fn_t = torch.as_tensor(np.asarray(self.fn), device=self.dev).long()
            rows = [int(r) for r in dom.split(",")]
            grp = {r: [c for c in rows if (c < 3) == (r < 3) and (c >= 5) == (r >= 5)] for r in rows}
            D = F.Dg_t[:n]
            for r in rows:
                off = torch.zeros(n, dtype=torch.float32, device=self.dev)
                off.index_add_(0, self._fo_t, F.Aon_t[:nf, r, r].abs())
                off.index_add_(0, self._fn_t, F.Ano_t[:nf, r, r].abs())
                for c in grp[r]:
                    if c != r:
                        off += D[:, r, c].abs()
                need = shift[r].float() + off
                D[:, r, r] = torch.maximum(D[:, r, r], need)


def _odil_dc_diag(blocks, amg, op, precond, R, nb, n, names, cfl, xb, yb, wxb, wyb, kd, restart, rtol, _rs=1.0):
    """LPCNS_ODIL_DIAG=step[,step]: how far the compact matrix (+ shift) is from the exact shifted operator, per row
    field, for unit random vectors in each column field and for the residual direction; then GMRES with no, block
    Jacobi and AMG preconditioning on -R."""
    L = blocks.fine
    names = ["u", "v", "w", "p", "T", "k", "e"][:nb]
    torch.manual_seed(0)
    print(f"odil-dc diag at CFL {cfl:g}: rel |(A_c - J) v|_rowfield / |J v|_rowfield, v nonzero in one column field",
          flush=True)

    def comp(v):
        xb.copy_(v.float().view(nb, n).T)
        L.matvec(wxb, wyb)
        return yb.T.reshape(-1).double().clone() * _rs

    cols = [(names[c], c) for c in range(nb)] + [("R", -1)]
    for name, c in cols:
        if c >= 0:
            v = torch.zeros(nb * n, dtype=torch.float64, device=R.device)
            v[c * n:(c + 1) * n] = torch.randn(n, dtype=torch.float64, device=R.device)
        else:
            v = R.double().clone()
        ye = op(v.to(torch.float64)).double()
        yc = comp(v)
        e = (yc - ye).view(nb, n).norm(dim=1)
        ne = ye.view(nb, n).norm(dim=1)
        print(f"  col {name:8s} " + " ".join(f"{names[r]}:{float(e[r] / max(ne[r], 1e-300)):.2e}/{float(ne[r]):.1e}"
                                            for r in range(nb)), flush=True)

    def bj(v):
        xb.copy_((v / (_rs if isinstance(_rs, float) else _rs.to(v.dtype))).view(nb, n).T)
        z = torch.bmm(L.Dinv_t[:n], xb.unsqueeze(-1)).squeeze(-1)
        return z.T.reshape(-1).to(v.dtype).clone()

    for nm, pc in (("none", lambda v: v.clone()), ("block-Jacobi", bj), ("AMG", precond)):
        _, info = gmres(op, pc, (-R).to(kd), restart=restart, maxiter=80, rtol=rtol)
        print(f"  GMRES precond {nm:13s} it {info['it']:3d} rel {info['rel']:.2e}", flush=True)
    for il, Lv in enumerate(amg.levels):
        Dg, Di = Lv.Dg_t[: Lv.n].double(), Lv.Dinv_t[: Lv.n].double()
        err = (torch.bmm(Dg, Di) - torch.eye(nb, device=Dg.device, dtype=Dg.dtype)).abs().amax(dim=(1, 2))
        sv = torch.linalg.svdvals(Dg)
        cond = sv[:, 0] / sv[:, -1].clamp_min(1e-300)
        # Jacobi iteration matrix norm proxy: sum_j |Dinv A_ij| row-wise (block 1-norm of D^-1 offdiag), max over cells
        print(f"  level {il} n {Lv.n}: |Dinv| max {float(Di.abs().max()):.2e}, |D Dinv - I| max {float(err.max()):.2e}, "
              f"block cond median {float(cond.median()):.2e} max {float(cond.max()):.2e} "
              f"(> 1e6: {int((cond > 1e6).sum())})", flush=True)
        bt = torch.randn(Lv.n, nb, dtype=torch.float32, device=Dg.device)
        xt, rt = torch.zeros_like(bt), torch.zeros_like(bt)
        out = []
        for sweeps in (2, 4, 8, 16):
            xt.zero_()
            Lv.jacobi(Lv.wv(xt), Lv.wv(bt), amg.omega, sweeps, True)
            Lv.residual(Lv.wv(xt), Lv.wv(bt), Lv.wv(rt))
            out.append(f"{float(rt.norm() / bt.norm()):.2e}")
        print(f"    block-Jacobi on level {il} (random b), |r|/|b| after 2/4/8/16 sweeps: {' '.join(out)}", flush=True)
    for il, mp in enumerate(amg.maps):
        F, C = amg.levels[il], amg.levels[il + 1]
        agg = wp.to_torch(mp["agg"]).long()
        vc = torch.randn(C.n, nb, dtype=torch.float32, device=agg.device)
        yc = torch.zeros_like(vc)
        C.matvec(C.wv(vc), C.wv(yc))
        vf = vc[agg].contiguous()
        yf = torch.zeros_like(vf)
        F.matvec(F.wv(vf), F.wv(yf))
        y2 = torch.zeros_like(vc).index_add_(0, agg, yf)
        e = (yc - y2).norm(dim=0) / y2.norm(dim=0).clamp_min(1e-30)
        print(f"  Galerkin check level {il}->{il + 1}: rel |A_c v - R A P v| per field " +
              " ".join(f"{float(t):.1e}" for t in e), flush=True)
    if hasattr(amg, "coarse_inv"):
        sv = torch.linalg.svdvals(amg.coarse_inv)
        print(f"  coarse inverse: |.| max {float(amg.coarse_inv.abs().max()):.2e}, cond {float(sv[0] / sv[-1]):.2e}",
              flush=True)
    # the AMG as a stationary solver on the compact system itself (does the V-cycle converge on its own matrix?)
    def stationary(tag):
        b = (-R).float()
        z = torch.zeros_like(b)
        out = []
        for k in range(6):
            r = b - comp(z.double()).float()
            out.append(f"{float(r.norm() / b.norm()):.2e}")
            z = z + precond(r)
        print(f"  AMG-on-compact {tag:28s} |r|/|b| per cycle: {' '.join(out)}", flush=True)
    stationary("as configured")
    save = (amg.coarse_scale, amg.pp_scale, amg.p_field, amg.pre, amg.post, amg.l1)
    sweeps = [dict(pp_scale=v_, p_field=3) for v_ in (2.0, 4.0, 8.0)] + [dict(pp_scale=4.0, p_field=3, l1=0.0),
              dict(pp_scale=4.0, p_field=3, coarse_scale=0.5)]
    for kw in sweeps:
        for k_, v_ in kw.items():
            setattr(amg, k_, v_)
        amg.__dict__["_cmds"] = {}
        amg.update()
        stationary(",".join(f"{k_}={v_}" for k_, v_ in kw.items()))
        _, info = gmres(op, precond, (-R).to(kd), restart=restart, maxiter=80, rtol=rtol)
        print(f"    GMRES with it: it {info['it']} rel {info['rel']:.2e}", flush=True)
        amg.coarse_scale, amg.pp_scale, amg.p_field, amg.pre, amg.post, amg.l1 = save
    amg.__dict__["_cmds"] = {}
    amg.update()


def solve_odil_dc(cs, x0, **kwargs):
    _saved_cs_residual = cs.residual
    try:
        return _solve_odil_dc_impl(cs, x0, **kwargs)
    finally:
        cs.residual = _saved_cs_residual


def _solve_odil_dc_impl(cs, x0, *, maxit=1000, cfl0=5.0, cfl_min=1.0, cfl_max=1e6, growth=2.0, reject=3.0,
                        max_du=0.5, beta=1.0, turb_cfl=1.0, pos_diag=1.0, clip=0.2, restart=40, krylov=80, rtol=0.03,
                        omega=0.7, pre=2, post=2, coarse_cells=800, passes=2, rebuild_every=1, uref=None,
                        krylov_dtype=torch.float32, stop_rule=None, tol=0.0, law="ser", lam_floor=None, max_linear_rel=None,
                        log=None, progress=None, init_state=None, phys_dt=None, phys_old=None, phys_t_mult=None):
    """ODIL3D's steady inner iterations on the data-centre k-epsilon Warp backend.  As ``solve_odil`` except: the
    preconditioner matrix is the backend's Picard compact Jacobian (``PicardBlocks``, rebuilt every
    ``rebuild_every`` steps, the pseudo-time diagonal refreshed every step); lam is the convective + viscous spectral
    radius of the pseudo-time driver (face speed floored at U_ref, the largest boundary normal speed); the shift adds
    ``pos_diag`` x max(0, -J_ii) (k-epsilon and buoyancy rows); the merit of the CFL law is the RMS of the per-field
    residual norms relative to the start; k and epsilon stay >= ``clip`` x their old value; the run ends on
    ``stop_rule(x, R, it)`` (the entry's data-centre rule) or max|R| < tol."""
    from Pipeline.CoupledNewton.pseudo_time import _Geometry, boundary_reference_velocity
    from Pipeline.CoupledNewton.stop_rules import field_slices
    log = log or (lambda *a: None)
    dev = cs.device
    n = int(cs.n_cell)
    # AMG smoothing overrides (LPCNS_ODIL_PRE / _POST / _OMEGA / _PASSES)
    pre = int(os.environ.get("LPCNS_ODIL_PRE", pre))
    post = int(os.environ.get("LPCNS_ODIL_POST", post))
    omega = float(os.environ.get("LPCNS_ODIL_OMEGA", omega))
    passes = int(os.environ.get("LPCNS_ODIL_PASSES", passes))
    _turb_min = float(os.environ.get("LPCNS_ODIL_TURB_MIN", "1e-12"))
    x = x0.detach().clone().to(device=dev, dtype=torch.float64)
    _has_init_state = (init_state is not None) or bool(os.environ.get("LPCNS_ODIL_INIT_STATE"))
    if _has_init_state:
        # diagnostics / warm start: start from a saved iterate (the cold-start residual stays the merit reference)
        R_cold = cs.residual(x).detach()
        if init_state is not None:
            x = init_state.detach().to(device=dev, dtype=torch.float64).reshape(x.shape).clone()
        else:
            x = torch.load(os.environ["LPCNS_ODIL_INIT_STATE"], map_location=dev).to(torch.float64).reshape(x.shape)
        if _turb_min > 0.0 and x.numel() // n >= 6:
            x.view(-1, n)[5:].clamp_min_(_turb_min)
    _phys_dt = float(phys_dt) if phys_dt is not None else float(os.environ.get("LPCNS_ODIL_PHYS_DT", "0"))
    _phys_D = None
    _saved_cs_residual = cs.residual
    if _phys_dt > 0.0:
        if os.environ.get("LPCNS_ODIL_FDJVP", "0") != "1":
            raise NotImplementedError("LPCNS_ODIL_PHYS_DT needs the finite-difference Krylov product (LPCNS_ODIL_FDJVP=1)")
        _nf_p = x.numel() // n
        _Wr = getattr(cs._warp, "W", None)                # DCWarpBackend -> DCWarpResidual.c_vol
        _vol = (wp.to_torch(_Wr.c_vol) if _Wr is not None else cs._warp.implicit._vol_t).to(torch.float64).reshape(-1)[:n]
        _phys_D = (_vol / _phys_dt).repeat(_nf_p, 1)
        _phys_D[3] = 0.0                                   # continuity (p_rgh) has no time derivative
        _phys_t_mult = float(phys_t_mult) if phys_t_mult is not None else float(os.environ.get("LPCNS_ODIL_PHYS_T_MULT", "1.0"))
        if _phys_t_mult != 1.0 and _nf_p > 4:
            _phys_D[4] = _phys_D[4] * _phys_t_mult         # effective thermal capacitance multiplier on T
        if phys_old is not None:
            _x_old = phys_old.detach().to(device=dev, dtype=torch.float64).reshape(x.shape).clone()
        else:
            _po = os.environ.get("LPCNS_ODIL_PHYS_OLD")
            _x_old = (torch.load(_po, map_location=dev, weights_only=False) if _po else x).to(torch.float64)
            _x_old = (_x_old["x"] if isinstance(_x_old, dict) else _x_old).reshape(x.shape).clone()
        _phys_Df = _phys_D.reshape(-1)
        _res_steady = _saved_cs_residual

        def _res_step(z, _r=_res_steady, _D=_phys_Df, _xo=_x_old):
            r = _r(z)
            return r + (_D * (z.reshape(-1) - _xo.reshape(-1))).reshape(r.shape).to(r.dtype)
        cs.residual = _res_step
        log(f"odil-dc: physical time step dt {_phys_dt:g} s (dual time stepping)")
    _phys_tol = float(os.environ.get("LPCNS_ODIL_PHYS_TOL", "1e-6"))
    _phys_steps = int(os.environ.get("LPCNS_ODIL_PHYS_STEPS", "1"))
    _phys_save = os.environ.get("LPCNS_ODIL_PHYS_SAVE", "")
    _phys_done = 0
    # LPCNS_ODIL_PHYS_RTOL=r: a step is also solved once its merit is below r times the merit it started from (the
    # absolute tolerance alone is met at once when the steady residual of the march is already below it)
    _phys_rtol = float(os.environ.get("LPCNS_ODIL_PHYS_RTOL", "0"))
    _phys_m0 = None
    _phys_rel0 = None
    _phys_turb_rtol = float(os.environ.get("LPCNS_ODIL_PHYS_TURB_RTOL", "1.0"))
    # absolute floor of the turbulence rows' step criterion (default: LPCNS_ODIL_PHYS_TOL). On L1 the k residual sits
    # at its steady plateau and drifts with the flow, so "no larger than at the start of the step" can stay unmet
    _phys_turb_tol = float(os.environ.get("LPCNS_ODIL_PHYS_TURB_TOL", "0") or 0.0)
    _phys_flow_tol = float(os.environ.get("LPCNS_ODIL_PHYS_FLOW_TOL", "0") or 0.0)
    _phys_t_tol = float(os.environ.get("LPCNS_ODIL_PHYS_T_TOL", str(_phys_flow_tol)) or 0.0)
    _phys_turb_ref_min = float(os.environ.get("LPCNS_ODIL_PHYS_TURB_REF_MIN", "0") or 0.0)
    # LPCNS_ODIL_PHYS_MASS_TOL=f: a physical step is solved only once the hall's mass balance closes to f of the
    # boundary inflow as well: |sum of the continuity rows| (the net boundary flux, m3/s) <= f * inflow. The per-field
    # relative norms do not see this global mode: on Base201 L1 with measured unit flows the step met its norm
    # criterion with the only pressure opening carrying a third of its share (net 0.84 of 74.6 m3/s, the 1 % C0 mass
    # check failed). The inflow is the boundary inflow of the starting state (fixed-flow patches: the prescribed one)
    _phys_mass_tol = float(os.environ.get("LPCNS_ODIL_PHYS_MASS_TOL", "0") or 0.0)
    R = cs.residual(x).detach()
    # LPCNS_ODIL_PHYS_REF_OLD=1: with a warm start (init_state) the physical step's relative criterion is measured
    # against the residual at the old state (the step's own cold start), not at the warm iterate.  Measured against
    # the warm iterate, a re-solve that starts near the solution must fall a further factor r below it: on Base201 L1
    # the inversion's re-solve of a 120 s step started at merit 1.2e-2 and was still running after 650 pseudo-time
    # steps, where the first solve had stopped at merit 1.1e-2 after 165 (2026-11-17)
    _R_phys_ref = None
    if _phys_D is not None and _has_init_state and os.environ.get("LPCNS_ODIL_PHYS_REF_OLD", "0") == "1":
        _R_phys_ref = cs.residual(_x_old.clone()).detach()
        R = cs.residual(x).detach()
    if os.environ.get("LPCNS_ODIL_DUMP_COLS"):
        # diagnostics only: the Jacobian columns of the listed cells' unknowns at the start state (central
        # differences of the raw residual), saved sparse as {(cell, field): (rows, values)} with the state's values
        _cols, _nf = {}, x.numel() // n
        for _c in [int(s_) for s_ in os.environ["LPCNS_ODIL_DUMP_COLS"].split(",")]:
            for _f in range(_nf):
                _i = _f * n + _c
                _h = 1e-6 * max(1.0, abs(float(x.reshape(-1)[_i])))
                _xp, _xm = x.clone(), x.clone()
                _xp.reshape(-1)[_i] += _h
                _xm.reshape(-1)[_i] -= _h
                _d = ((cs.residual(_xp) - cs.residual(_xm)).detach().reshape(-1) / (2.0 * _h)).cpu()
                _nz = torch.nonzero(_d.abs() > 1e-14 * float(_d.abs().max())).reshape(-1)
                _cols[(_c, _f)] = (_nz, _d[_nz], float(x.reshape(-1)[_i]))
        torch.save({"n": n, "cols": _cols, "R": R.reshape(-1).cpu()},
                   os.environ.get("LPCNS_ODIL_SAVE_PREFIX", ".work/odil_state") + "_cols.pt")
    u_ref = float(uref) if uref is not None else max(boundary_reference_velocity(cs), 1e-3)
    # LPCNS_ODIL_LAM_FLOOR_CAP=c: the shift's face-speed floor is min(U_ref, c).  U_ref is the largest boundary normal
    # speed; where one small outlet sets it (0.5 m3/s through 0.017 m2: 29 m/s) it shortens the pseudo-time step of
    # every slow cell of the hall, which then needs thousands of steps.  Cells faster than the floor keep their own
    # Courant number.  An explicit lam_floor wins.
    _floor_cap = float(os.environ.get("LPCNS_ODIL_LAM_FLOOR_CAP", "0"))
    if lam_floor is None and 0.0 < _floor_cap < u_ref:
        lam_floor = _floor_cap
    geom = _Geometry(cs)
    t_setup = time.perf_counter()
    blocks = PicardBlocks(cs, x, mode=os.environ.get("LPCNS_ODIL_BLOCKS", "picard"))
    nb = blocks.nb
    torch.cuda.synchronize(dev)
    _ts = [time.perf_counter()]
    # LPCNS_ODIL_HIER_GPU=1: the same aggregation on the GPU (8.4 s -> <1 s on 7.3e5 cells)
    hier = (build_hierarchy_t if os.environ.get("LPCNS_ODIL_HIER_GPU", "0") == "1" else build_hierarchy)(
        n, blocks.fo, blocks.fn, blocks.weight, coarse_target=coarse_cells, passes=passes,
        **({"device": dev} if os.environ.get("LPCNS_ODIL_HIER_GPU", "0") == "1" else {}))
    torch.cuda.synchronize(dev)
    _ts.append(time.perf_counter())
    free = getattr(cs, "_gauge_free", None)
    closed = bool(cs.resolve_gauge(x) if free is None and hasattr(cs, "resolve_gauge") else free)
    coarse_pin = closed or os.environ.get("LPCNS_ODIL_COARSE_PIN", "0") == "1"
    _pp_scale = float(os.environ.get("LPCNS_ODIL_PP", "1"))
    _coarse_scale = float(os.environ.get("LPCNS_ODIL_CSCALE", "1"))

    def _make_amg():
        a_ = AMG(blocks.fine, hier, omega=omega, pre=pre, post=post, p_index=3 if coarse_pin else None,
                 l1=float(os.environ.get("LPCNS_ODIL_L1", "0")))
        a_.pp_scale, a_.p_field, a_.coarse_scale = _pp_scale, 3, _coarse_scale
        return a_

    spc = (SimplePC(blocks, hier, omega=omega, pre=pre, post=post, l1=float(os.environ.get("LPCNS_ODIL_L1", "0")))
           if os.environ.get("LPCNS_ODIL_PC", "coupled") == "simple" else None)
    # LPCNS_ODIL_SPLIT=1 (7 fields, turbulence decoupled in the preconditioner): two AMGs on the same hierarchy, 5 x 5
    # (U, p, T) and 2 x 2 (k, eps), instead of the 7 x 7 one whose cross blocks are zero
    _split = os.environ.get("LPCNS_ODIL_SPLIT", "0") == "1" and nb == 7
    # with the split the 7 x 7 AMG serves only the diagnostics: its coarse levels (~0.5 GB at 1.1M cells) are built on
    # first use, so the large meshes keep room for the Picard rebuild
    amg = _Lazy(_make_amg) if _split else _make_amg()

    def _l1_switch(info_):                             # LPCNS_ODIL_L1_AUTO acts on the split AMGs only (below)
        return None
    if _split:
        _l1 = float(os.environ.get("LPCNS_ODIL_L1", "0"))
        L5 = BlockLevel(n, blocks.fo, blocks.fn, 5, dev)
        L2 = BlockLevel(n, blocks.fo, blocks.fn, 2, dev)
        # LPCNS_ODIL_HALF=1: the fine level's off-diagonal blocks in float16 for the smoother and residual (the
        # V-cycle is bandwidth-bound; with l1 smoothing D^-1 is not the inverse of D, so the sweep also reads D)
        _half = os.environ.get("LPCNS_ODIL_HALF", "0") == "1"
        if _half:
            L5.enable_half()
            L2.enable_half()
        # LPCNS_ODIL_SWEEPS="pre,post": the smoothing sweeps of the split AMGs (default 2,2)
        _pre, _post = (int(v_) for v_ in os.environ.get("LPCNS_ODIL_SWEEPS", f"{pre},{post}").split(","))
        amg5 = AMG(L5, hier, omega=omega, pre=_pre, post=_post, p_index=3 if coarse_pin else None, l1=_l1)
        amg5.pp_scale, amg5.p_field, amg5.coarse_scale = _pp_scale, 3, _coarse_scale
        amg2 = AMG(L2, hier, omega=omega, pre=_pre, post=_post, l1=_l1)
        x5, y5 = (torch.zeros(n, 5, dtype=torch.float32, device=dev) for _ in range(2))
        x2, y2 = (torch.zeros(n, 2, dtype=torch.float32, device=dev) for _ in range(2))
        wx5, wy5, wx2, wy2 = L5.wv(x5), L5.wv(y5), L2.wv(x2), L2.wv(y2)
        _ncyc5 = int(os.environ.get("LPCNS_ODIL_NCYC5", "1"))
        r5, c5 = (torch.zeros(n, 5, dtype=torch.float32, device=dev) for _ in range(2))
        wr5, wc5 = L5.wv(r5), L5.wv(c5)
        # LPCNS_ODIL_CPR=1: two-stage preconditioner of the 5 x 5 block (CPR type): a scalar AMG V-cycle on the
        # pressure-pressure block for the continuity rows, the coupled residual updated with that correction, then the
        # 5 x 5 V-cycle on the remainder (the continuity equation is what the coupled V-cycle leaves: LPCNS_ODIL_LINRES)
        _cpr = os.environ.get("LPCNS_ODIL_CPR", "0") == "1"
        if _cpr:
            L1p = BlockLevel(n, blocks.fo, blocks.fn, 1, dev)
            amgp = AMG(L1p, hier, omega=omega, pre=_pre, post=_post, l1=_l1)
            xp_, yp_ = (torch.zeros(n, 1, dtype=torch.float32, device=dev) for _ in range(2))
            wxp, wyp = L1p.wv(xp_), L1p.wv(yp_)
            e5 = torch.zeros(n, 5, dtype=torch.float32, device=dev)
            we5 = L5.wv(e5)

        def _split_update():
            F_, nf_ = blocks.fine, blocks.fine.nf
            for L_, sl_ in ((L5, slice(0, 5)), (L2, slice(5, 7))):
                L_.Dg_t[:n].copy_(F_.Dg_t[:n, sl_, sl_])
                L_.Aon_t[:nf_].copy_(F_.Aon_t[:nf_, sl_, sl_])
                L_.Ano_t[:nf_].copy_(F_.Ano_t[:nf_, sl_, sl_])
            amg5.update()
            amg2.update()
            if _cpr:
                L1p.Dg_t[:n].copy_(F_.Dg_t[:n, 3:4, 3:4])
                L1p.Aon_t[:nf_].copy_(F_.Aon_t[:nf_, 3:4, 3:4])
                L1p.Ano_t[:nf_].copy_(F_.Ano_t[:nf_, 3:4, 3:4])
                amgp.update()
            if _half:
                L5.refresh_half()
                L2.refresh_half()
            if _rackw:
                _rackw_update()
        # LPCNS_ODIL_L1_AUTO=w: plain block Jacobi until a solve stalls (GMRES at its cap with rel > 0.9 -- the
        # signature of a diverging smoother: none in ten INROW runs, from step ~46 on PERIM_OPEN); then l1 smoothing
        # of weight w for the rest of the run (with LPCNS_ODIL_L1_SEL only on the rows without diagonal dominance)
        _l1_auto = float(os.environ.get("LPCNS_ODIL_L1_AUTO", "0"))

        _l1_auto_cap = float(os.environ.get("LPCNS_ODIL_L1_AUTO_CAP", "0"))
        _l1_probe = os.environ.get("LPCNS_ODIL_L1_PROBE", "0") == "1"

        def _l1_switch(info_):
            # LPCNS_ODIL_L1_AUTO_CAP=c: the switch also lowers the CFL cap to c (the PERIM cases, which stall, run
            # fastest at cap ~12 with l1; INROW_OPEN / CAC, which do not, keep their cap)
            nonlocal cfl_max
            if _l1_auto > 0.0 and amg5.l1 == 0.0 and info_["it"] >= krylov and info_["rel"] > 0.9:
                amg5.l1 = amg2.l1 = _l1_auto
                _split_update()
                if _l1_auto_cap > 0.0:
                    cfl_max = min(cfl_max, _l1_auto_cap)
                log(f"odil-dc l1 smoothing switched on at step {it} (weight {_l1_auto:g}"
                    + (f", CFL cap {cfl_max:g}" if _l1_auto_cap > 0.0 else "") + f"): GMRES ended at rel "
                    f"{info_['rel']:.2f} after {info_['it']} iterations")
        # LPCNS_ODIL_RACKW=1: the CBBM rack mean in the preconditioner's temperature rows.  The exact Jacobian is the
        # compact one plus U V^T (U_r = dR_T / d(discharge ghost of rack r), V_r = the area weights of its intake
        # cells); the compact blocks drop it (dense per rack), and the rack-to-rack recirculation it carries is a
        # near-singular temperature mode a restarted GMRES cannot rebuild.  Woodbury on the temperature rows:
        # z_T -= B_T U (I + V^T B_T U)^-1 V^T z_T, with B_T a scalar AMG of the compact T-T block (+ shift).
        _rackw = os.environ.get("LPCNS_ODIL_RACKW", "0") == "1"
        if _rackw:
            core = cs._warp.W
            rkt = core._rack_t
            nr = int(core.n_rack)
            odt = wp.to_torch(core.o_dt)
            live = rkt["remote"] == -1
            UT = torch.zeros(n, nr, dtype=torch.float32, device=dev)
            for r_ in range(nr):
                # R_T is linear in a discharge ghost (upwind inflow + diffusion): a unit change of the rack's dT
                # gives U_r exactly
                msk = live & (rkt["rack"] == r_)
                odt[msk] += 1.0
                Rp = cs.residual(x).detach()
                odt[msk] -= 1.0
                UT[:, r_] = (Rp[4 * n:5 * n] - R[4 * n:5 * n]).float()
            cs.residual(x)
            cnt = rkt["off"][1:] - rkt["off"][:-1]
            rk_of = torch.repeat_interleave(torch.arange(nr, device=dev), cnt)
            rk_src = rkt["src"].long()
            rk_w = (rkt["area"] / rkt["asum"][rk_of]).float()
            LT = BlockLevel(n, blocks.fo, blocks.fn, 1, dev)
            amgT = AMG(LT, hier, omega=omega, pre=_pre, post=_post, l1=_l1)
            bT, zT = (torch.zeros(n, 1, dtype=torch.float32, device=dev) for _ in range(2))
            wbT, wzT = LT.wv(bT), LT.wv(zT)
            WT = torch.zeros(n, nr, dtype=torch.float32, device=dev)
            rk_S = {}

            def _vT(zt):
                """V^T z: the area-weighted intake mean of z per rack ([.., nr])."""
                g_ = torch.zeros((nr,) + tuple(zt.shape[1:]), dtype=zt.dtype, device=dev)
                return g_.index_add_(0, rk_of, rk_w.view(-1, *([1] * (zt.dim() - 1))).to(zt.dtype) * zt[rk_src])

            def _rackw_update():
                torch.cuda.synchronize(dev)
                t_ = time.perf_counter()
                _rackw_update_()
                torch.cuda.synchronize(dev)
                rk_S["t_upd"] = rk_S.get("t_upd", 0.0) + time.perf_counter() - t_
                rk_S["n_upd"] = rk_S.get("n_upd", 0) + 1

            def _rackw_update_():
                F_, nf_ = blocks.fine, blocks.fine.nf
                LT.Dg_t[:n, 0, 0].copy_(F_.Dg_t[:n, 4, 4])
                LT.Aon_t[:nf_, 0, 0].copy_(F_.Aon_t[:nf_, 4, 4])
                LT.Ano_t[:nf_, 0, 0].copy_(F_.Ano_t[:nf_, 4, 4])
                amgT.update()
                for r_ in range(nr):
                    bT[:, 0].copy_(UT[:, r_])
                    zT.zero_()
                    amgT.vcycle(wbT, wzT)
                    WT[:, r_].copy_(zT[:, 0])
                S_ = torch.eye(nr, dtype=torch.float64, device=dev) + _vT(WT).double()
                rk_S["inv"] = torch.linalg.inv(S_).float()

            def _rackw_apply(z):
                if os.environ.get("LPCNS_ODIL_PROF2") == "1":
                    torch.cuda.synchronize(dev)
                    t_ = time.perf_counter()
                zt = z.view(nb, n)[4]
                c_ = rk_S["inv"] @ _vT(zt.float())
                zt -= (WT @ c_).to(zt.dtype)
                if os.environ.get("LPCNS_ODIL_PROF2") == "1":
                    torch.cuda.synchronize(dev)
                    rk_S["t_apply"] = rk_S.get("t_apply", 0.0) + time.perf_counter() - t_
                    rk_S["n_apply"] = rk_S.get("n_apply", 0) + 1
                    if rk_S["n_apply"] % 200 == 0:
                        print(f"rackw apply {1e3 * rk_S['t_apply'] / rk_S['n_apply']:.2f} ms, update "
                              f"{1e3 * rk_S.get('t_upd', 0.0) / max(rk_S.get('n_upd', 1), 1):.1f} ms", flush=True)
                return z
    if os.environ.get("LPCNS_ODIL_BLKSTATS") == "1":
        # diagnostics: magnitudes of the compact blocks per row field (for a reduced-precision storage decision)
        F_, nf_ = blocks.fine, blocks.fine.nf
        A_ = torch.cat([F_.Aon_t[:nf_], F_.Ano_t[:nf_]]).abs()
        D_ = F_.Dg_t[:n].abs()
        for r_ in range(nb):
            row = A_[:, r_, :].reshape(-1)
            nz = row[row > 0]
            dg = D_[:, r_, r_]
            q = torch.quantile(nz[torch.randperm(nz.numel(), device=dev)[:1000000]].float(),
                               torch.tensor([0.01, 0.5, 0.99], device=dev)) if nz.numel() else None
            frac = float((nz < 6.1e-5).float().mean()) if nz.numel() else 0.0
            rel = (A_[:, r_, :].amax(1) / torch.cat([dg[blocks.fo], dg[blocks.fn]] if False else [dg[torch.as_tensor(
                np.asarray(blocks.fo), device=dev).long()], dg[torch.as_tensor(np.asarray(blocks.fn), device=dev).long()]]
                ).clamp_min(1e-300)).float()
            log(f"blkstats row {r_}: |A| q01/q50/q99 {q.tolist() if q is not None else None}, max {float(nz.max()):.3e}, "
                f"frac < 6.1e-5 {frac:.3f}; diag q50 {float(dg.median()):.3e} min {float(dg.min()):.3e}; "
                f"max|offdiag row|/diag q50 {float(rel.median()):.3e}")
    torch.cuda.synchronize(dev)
    setup_s = time.perf_counter() - t_setup
    log(f"odil-dc setup: Picard blocks {_ts[0] - t_setup:.2f} s, hierarchy {_ts[1] - _ts[0]:.2f} s, AMG "
        f"{setup_s - (_ts[1] - t_setup):.2f} s")
    slices = field_slices(cs.unknowns, n)
    names = list(slices)
    turb_rows = [f for f in range(nb) if f >= 5]
    _use_cold_r0 = _has_init_state and (
        _phys_D is None or os.environ.get("LPCNS_ODIL_PHYS_COLD_REF") == "1"
    )
    R0 = {f: max(float((R_cold if _use_cold_r0 else R)[sl].norm()), 1e-300)
          for f, sl in slices.items()}
    # GMRES works on the rows scaled by 1 / |R0_field| (the merit's own weights): unscaled, the epsilon rows
    # (|R| ~ 1e3) swamp momentum and continuity (|R| ~ 1e-2..1e-1) and rtol is met by solving epsilon alone
    turb_cfl_max = float(os.environ.get("LPCNS_ODIL_TURB_CFL_MAX", "inf"))
    serp_tol = float(os.environ.get("LPCNS_ODIL_SERP_TOL", "1.0"))   # ser+: grow while merit <= tol x previous
    heat_defl = os.environ.get("LPCNS_ODIL_HEAT_DEFL", "0") == "1"
    tcorr = os.environ.get("LPCNS_ODIL_TCORR", "0") == "1"      # global mean-T correction after each step
    # BoostConv (Citro et al., JCP 2017): residual recombination over the last boost_m accepted steps, active once
    # merit < boost_below; it removes the components the pseudo-time iteration amplifies (an unstable steady state)
    boost_m = int(os.environ.get("LPCNS_ODIL_BOOST", "0"))
    boost_below = float(os.environ.get("LPCNS_ODIL_BOOST_BELOW", "0.05"))
    boost_cfl = float(os.environ.get("LPCNS_ODIL_BOOST_CFL", "0"))   # >0: hold the CFL there while active
    bX, bR = [], []
    boost_used = 0
    tcorr_hist = []
    ls_on = os.environ.get("LPCNS_ODIL_LS", "0") == "1"
    ls_max = int(os.environ.get("LPCNS_ODIL_LS_MAX", "3"))
    ls_tol = float(os.environ.get("LPCNS_ODIL_LS_TOL", "1.0"))
    ls_steps = []
    t_cfl_max = float(os.environ.get("LPCNS_ODIL_T_CFL_MAX", "inf"))
    t_cfl_mult = float(os.environ.get("LPCNS_ODIL_T_CFL_MULT", "1"))   # the temperature row's CFL = mult x CFL
    _t_relax = float(os.environ.get("LPCNS_ODIL_T_RELAX", "0"))        # > 0: relaxation-form shift of the T rows
    _t_relax_from = int(os.environ.get("LPCNS_ODIL_T_RELAX_FROM", "0"))
    # LPCNS_ODIL_T_MULT_LATE="step,mult": the multiplier switches to mult at the given step (a slow temperature while the
    # flow develops keeps the room from storing heat it then drains over hundreds of steps)
    _t_late = (tuple(float(v_) for v_ in os.environ["LPCNS_ODIL_T_MULT_LATE"].split(","))
               if os.environ.get("LPCNS_ODIL_T_MULT_LATE") else None)
    _tclip = float(os.environ.get("LPCNS_ODIL_TCLIP", "0"))
    _uclip = float(os.environ.get("LPCNS_ODIL_UCLIP", "0"))
    _nut_max = float(os.environ.get("LPCNS_ODIL_NUT_MAX", "0"))
    _tbox = ([float(v_) for v_ in os.environ["LPCNS_ODIL_TBOX"].split(",")]
             if os.environ.get("LPCNS_ODIL_TBOX") else None)
    # LPCNS_ODIL_TBOX_WIDEN="after,step,max" (default "20,10,150"): the box guards the start-up, whose excursions sit
    # in a few dozen cells for a step or two. A ceiling that cells still touch for `after` accepted steps in a row
    # (past the first 100) is too low for the steady field -- a cabinet whose exhaust loops back through leaky blanking
    # panels runs 50-60 K above the supply (dcdtuni Base201, typicalSmallGap: the OpenFOAM field reaches 76 C) -- and
    # clamping it destroys heat: the units then never remove the declared load (heat closure stuck at -10 %). The
    # ceiling is then raised by max(`step`, half its excess) up to `max` K and the step logged. A hall that never
    # touches it is unchanged.
    _tbox_widen = [float(v_) for v_ in os.environ.get("LPCNS_ODIL_TBOX_WIDEN", "20,10,150").split(",")]
    _tbox_top_run = 0
    if _tbox is not None and (os.environ.get("LPCNS_ODIL_INIT_STATE") or _phys_D is not None):
        _t_lo = float(x[4 * n:5 * n].min())
        _t_hi = float(x[4 * n:5 * n].max())
        if _phys_D is not None:
            _t_lo = min(_t_lo, float(_x_old[4 * n:5 * n].min()))
            _t_hi = max(_t_hi, float(_x_old[4 * n:5 * n].max()))
        _tbox[0] = min(_tbox[0], _t_lo - 2.0)
        _tbox[1] = min(max(_tbox[1], _t_hi + 5.0), _tbox_widen[2])
    # LPCNS_ODIL_CAP_LATE="m,cap": the CFL cap is raised to cap once the merit falls below m (an early cap that guards
    # the startup transient otherwise also limits the slow late decay)
    _cap_late = (tuple(float(v_) for v_ in os.environ["LPCNS_ODIL_CAP_LATE"].split(","))
                 if os.environ.get("LPCNS_ODIL_CAP_LATE") else None)
    rowscale = torch.ones(nb * n, dtype=torch.float64, device=dev)
    if os.environ.get("LPCNS_ODIL_ROWSCALE", "1") == "1":
        for f, sl in slices.items():
            rowscale[sl] = 1.0 / R0[f]

    _phys_inflow = None
    _cont_key = "p_rgh" if "p_rgh" in slices else ("p" if "p" in slices else None)
    if _phys_D is not None and _phys_mass_tol > 0.0 and _cont_key is not None:
        try:
            cs.residual(x)
            _Wm = cs._warp.W
            _mbf = getattr(cs.solver, "mask_boundary_non_cyclic_face", None)    # the C0 check's faces (no cyclics)
            _bf = (_mbf.reshape(-1).nonzero(as_tuple=True)[0].to(dev) if _mbf is not None
                   else getattr(blocks, "bfaces", None))
            if _bf is None:
                raise AttributeError("no boundary face list on the application or the preconditioner blocks")
            _phi0 = (_Wm.face_volumetric_flux.reshape(-1).to(torch.float64)
                     * cs.solver.face_area.reshape(-1).to(device=dev, dtype=torch.float64))[_bf]
            _phys_inflow = float((-_phi0[_phi0 < 0]).sum())
            if not _phys_inflow > 0.0:
                _phys_inflow = None
        except Exception as e_:                               # noqa: BLE001
            log(f"odil-dc: mass criterion off (no boundary inflow: {type(e_).__name__}: {e_})")
            _phys_inflow = None
        if _phys_inflow is not None:
            log(f"odil-dc: step criterion includes the mass balance, |net boundary flux| <= {_phys_mass_tol:g} x "
                f"inflow {_phys_inflow:.4g} m3/s")

    def _mass_rel(r):
        return abs(float(r[slices[_cont_key]].sum())) / _phys_inflow if _phys_inflow else 0.0

    def merit_of(r):
        norms = torch.stack([r[slices[f]].norm() for f in names]).tolist()
        rel = {f: v / R0[f] for f, v in zip(names, norms)}
        return math.sqrt(sum(v * v for v in rel.values()) / len(rel)), rel

    log(f"odil-dc: {n} cells x {nb} fields, {int(blocks.ij.numel())} coupled pairs, Picard blocks "
        f"({blocks.colours} colours, {blocks.seconds:.2f} s), AMG {(amg5 if _split else amg).describe()} (setup {setup_s:.2f} s), U_ref "
        f"{u_ref:.3g} (lam floor {u_ref if lam_floor is None else lam_floor:.3g}), CFL {cfl0:g} ({law} x{growth:g}, max {cfl_max:g}), GMRES({restart}) <= {krylov} to {rtol:g}, "
        f"{blocks.mode} blocks, rebuild every {rebuild_every}, clip {clip:g}, beta {beta:g}, pressure level {'free' if closed else 'fixed'}")
    W = cs._warp
    m, rel = merit_of(R)
    mprev = m
    # LPCNS_ODIL_EW="gamma,alpha,eta_max": Eisenstat-Walker forcing for the step's GMRES (off: the fixed rtol)
    ew = tuple(float(v_) for v_ in os.environ["LPCNS_ODIL_EW"].split(",")) if os.environ.get("LPCNS_ODIL_EW") else None
    ew_eta = rtol
    ew_up = float(os.environ.get("LPCNS_ODIL_EW_UP", "inf"))      # e.g. 1.0: tighten after any merit increase
    ew_redo = float(os.environ.get("LPCNS_ODIL_EW_REDO", "0"))     # e.g. 1.3: redo a loose step that raised the merit
    ew_redone = 0
    ew_redo_eta = float(os.environ.get("LPCNS_ODIL_EW_REDO_ETA", rtol))   # the redo's forcing (default: rtol)
    ew_redo_best = os.environ.get("LPCNS_ODIL_EW_REDO_BEST", "0") == "1"   # the redo competes with the loose step
    fd_jvp = os.environ.get("LPCNS_ODIL_FDJVP", "0") == "1"
    cfl = float(cfl0)
    cfl_ref = (cfl, m)
    hist = [{"it": 0, "max_abs": float(R.abs().max()), "l2": float(R.norm()), "lambda": float("nan"),
             "inner_iterations": 0, "inner_rtol": 0.0, "reassembled": False, "stalled": False, "cfl": cfl,
             "merit": m, "clipped": 0, "rejected": 0, "seconds": 0.0}]
    prof = dict(rebuild=0.0, amg=0.0, krylov=0.0, residual=0.0)
    fine_prof = os.environ.get("LPCNS_ODIL_PROFILE", "0") == "1"
    xb = torch.zeros(n, nb, dtype=torch.float32, device=dev)
    yb = torch.zeros(n, nb, dtype=torch.float32, device=dev)
    wxb, wyb = blocks.fine.wv(xb), blocks.fine.wv(yb)
    _op_compact = os.environ.get("LPCNS_ODIL_OP", "jvp") == "compact"
    ob = torch.zeros(n, nb, dtype=torch.float32, device=dev)
    pb = torch.zeros(n, nb, dtype=torch.float32, device=dev)
    wob, wpb = blocks.fine.wv(ob), blocks.fine.wv(pb)
    rejected = lin_total = builds = 0
    it = 0
    t0 = time.perf_counter()
    last_build = 0
    rej_run = 0                                                   # consecutive rejected steps
    rej_max = int(os.environ.get("LPCNS_ODIL_REJECT_MAX", "10"))  # ... after which the solve raises
    du_scaled = []                                    # factors of the steps the max_du limiter scaled down
    amg_state = {"it": 0, "cfl": float(cfl0)}
    # LPCNS_ODIL_QOI_LOG=<dir>: the dctwin quantities and their comparison with LPCNS_ODIL_QOI_REF every
    # LPCNS_ODIL_QOI_EVERY (5) accepted steps -> <dir>/qoi_log.jsonl, and at the end <dir>/qoi_compare.json
    # (the format of launch/time_to_accuracy.py, as the partitioned driver writes it)
    qoi_dir = os.environ.get("LPCNS_ODIL_QOI_LOG")
    accepted = 0
    if qoi_dir:
        import json as _json
        from pathlib import Path as _Path
        from Post_process.dc_qoi import DCQuantities
        _dq = DCQuantities(".", device=dev, dtype=torch.float64)
        _ref = os.environ["LPCNS_ODIL_QOI_REF"]
        _every = int(os.environ.get("LPCNS_ODIL_QOI_EVERY", "5"))
        _qd = _Path(qoi_dir)
        _qd.mkdir(parents=True, exist_ok=True)
        (_qd / "qoi_log.jsonl").write_text("")

        def _qoi(xx):
            out = _dq(xx[0:3 * n].reshape(3, n).T, xx[4 * n:5 * n])
            return out, _dq.compare(out, _ref)
    _last_eval = [None]                       # the iterate of the application's last residual evaluation
    rb_dx = float(os.environ.get("LPCNS_ODIL_REBUILD_DX", "0"))
    rb_skipped = 0
    x_build = x

    def _field_change(xa, xb):
        """max over fields of |xa_f - xb_f| / |xb_f - mean(xb_f)| (the change against the field's own spread)."""
        va, vb = xa.view(nb, n), xb.view(nb, n)
        num = (va - vb).norm(dim=1)
        den = (vb - vb.mean(dim=1, keepdim=True)).norm(dim=1).clamp_min(1e-300)
        return float((num / den).max())
    _prof2 = os.environ.get("LPCNS_ODIL_PROF2", "0") == "1"     # finer per-step buckets (stop rule, lam)
    _beta_target = beta
    _beta_ramp = int(os.environ.get("LPCNS_ODIL_BETA_RAMP", "0"))
    while it < maxit:
        if _beta_ramp > 0:
            # LPCNS_ODIL_BETA_RAMP=N: the continuity stiffness beta grows geometrically from 1 to its target over the
            # first N steps (a stiff continuity row from the cold start rejects every step)
            beta = _beta_target ** min(1.0, it / _beta_ramp) if _beta_target > 1.0 else _beta_target
        if hist[-1]["max_abs"] < tol:
            break
        # registration 4d: an iterate whose linear solve ended above max_linear_rel is not shown to the stop rule
        # (it does not count towards the quantity window); the rule itself (thresholds) belongs to the caller
        lin_ok = max_linear_rel is None or it == 0 or hist[-1]["inner_rtol"] <= max_linear_rel
        _t_sr = time.perf_counter()
        if stop_rule is not None and getattr(stop_rule, "needs_flux", False):
            # a rule that reads the application's face flux / ghosts of x (Post_process.foam_bridge): the residual of
            # x is re-evaluated when the last evaluation was another state (the loop does the same further down)
            if _last_eval[0] is not x:
                cs.residual(x)
                _last_eval[0] = x
            stop_rule.step_info = hist[-1]
        _sr_hit = stop_rule is not None and lin_ok and stop_rule(x, R, it)
        if _prof2:
            torch.cuda.synchronize(dev)
            prof["stop"] = prof.get("stop", 0.0) + time.perf_counter() - _t_sr
        if _sr_hit:
            hist[-1]["stop_rule"] = True
            break
        if _phys_D is not None and _phys_m0 is None:
            if _R_phys_ref is not None:
                _phys_m0, _rel_ref = merit_of(_R_phys_ref)
                _phys_rel0 = dict(_rel_ref)
                log(f"odil-dc: step criterion relative to the old state (merit {_phys_m0:.3e}; warm start {m:.3e})")
            else:
                _phys_m0, _phys_rel0 = m, dict(rel)
        # with LPCNS_ODIL_PHYS_RTOL the flow and energy rows must each fall to r times their start value; the
        # turbulence rows (cells held at the k / epsilon clip bound keep a residual there) only must not exceed
        # LPCNS_ODIL_PHYS_TURB_RTOL times theirs (default 1)
        def _phys_ok():
            if _phys_rtol <= 0.0:
                return m < _phys_tol
            for f_ in rel:
                turb_ = f_ in ("k", "epsilon", "nuTilda", "omega")
                if turb_ and _turb_min > 0.0:
                    sl_ = slices[f_]
                    pinned_ = (x[sl_] <= _turb_min * (1.0 + 1e-6)) & (R[sl_] > 0.0)
                    r_val_ = float(torch.where(pinned_, torch.zeros_like(R[sl_]), R[sl_]).norm()) / R0[f_]
                else:
                    r_val_ = rel[f_]
                ref_ = max(_phys_rel0[f_], _phys_turb_ref_min) if turb_ else _phys_rel0[f_]
                lim_ = (_phys_turb_rtol if turb_ else _phys_rtol) * ref_
                abs_fl_ = _phys_turb_tol if turb_ else (_phys_t_tol if f_ == "T" else _phys_flow_tol)
                if r_val_ > max(lim_, _phys_tol, abs_fl_):
                    return False
            if _phys_inflow is not None and _mass_rel(R) > _phys_mass_tol:
                return False
            return True
        if _phys_D is not None and it > 0 and _phys_ok():
            # the physical time step is solved. LPCNS_ODIL_PHYS_STEPS=N marches N implicit-Euler steps in this one
            # solve (the state of step j saved to LPCNS_ODIL_PHYS_SAVE + f"_{j}.pt"); the old state moves on in place
            _phys_done += 1
            log(f"odil-dc: physical step {_phys_done}/{_phys_steps} (t = {_phys_done * _phys_dt:g} s) solved at "
                f"pseudo-time step {it}, merit {m:.3e}")
            if _phys_save:
                torch.save(x.detach().cpu(), f"{_phys_save}_{_phys_done}.pt")
            if _phys_done >= _phys_steps:
                if hist:
                    hist[-1]["phys_converged"] = True
                if stop_rule is not None:
                    stop_rule.stop_reason = f"physical time step(s) {_phys_done}/{_phys_steps} (dt={_phys_dt:g} s) converged"
                break
            _x_old.copy_(x.reshape(_x_old.shape))
            R = cs.residual(x).detach()
            _last_eval[0] = x
            m, rel = merit_of(R)
            mprev = m                                     # the next step is judged against its own start merit
            _phys_m0, _phys_rel0 = m, dict(rel)
        if stop_rule is not None and it % 10 == 0 and getattr(stop_rule, "last", None):
            def _fmt(v):
                if isinstance(v, dict):
                    return "{" + ", ".join(f"{k}: {_fmt(w)}" for k, w in v.items()) + "}"
                if isinstance(v, float):
                    return f"{v:.3g}"
                if isinstance(v, list):
                    return "[" + ", ".join(_fmt(w) for w in v) + "]"
                return str(v)
            log(f"odil-dc stop rule at {it}: {_fmt(stop_rule.last)}")
        it += 1
        ta = time.perf_counter()
        _adapt = int(os.environ.get("LPCNS_ODIL_REBUILD_ADAPT", "0"))
        _timer = it - last_build >= rebuild_every
        _gm = bool(_adapt) and hist[-1]["inner_iterations"] > _adapt and it - last_build >= 2
        if it > 1 and _timer and not _gm and rb_dx > 0.0 and x_build is not None:
            # LPCNS_ODIL_REBUILD_DX=d: a timed rebuild is postponed (another rebuild_every steps) while no field has
            # moved by more than d of its own spread since the last build -- the matrix then hardly changed (the
            # GMRES-triggered rebuild of REBUILD_ADAPT stays)
            dx_ = _field_change(x, x_build)
            log(f"odil-dc rebuild check at {it}: field change {dx_:.3e} ({'postponed' if dx_ < rb_dx else 'rebuild'})")
            if dx_ < rb_dx:
                rb_skipped += 1
                last_build = it
                _timer = False
        if it > 1 and (_timer or _gm):
            # LPCNS_ODIL_REBUILD_ADAPT=N: rebuild when the last GMRES took > N iterations (and at most every
            # rebuild_every steps otherwise) -- the Picard build is the largest per-step cost
            if os.environ.get("LPCNS_ODIL_MEMLOG", "0") == "1":
                # diagnostics: the live CUDA tensors above 100 MB before the rebuild
                import gc
                big = sorted(((o.numel() * o.element_size(), tuple(o.shape), str(o.dtype)) for o in gc.get_objects()
                              if torch.is_tensor(o) and o.is_cuda and o.numel() * o.element_size() > 1e8), reverse=True)
                log(f"odil-dc memory before rebuild at {it}: allocated {torch.cuda.memory_allocated(dev) / 2**30:.2f} GB; "
                    f"{len(big)} tensors > 100 MB, {sum(b_[0] for b_ in big) / 2**30:.2f} GB: "
                    + "; ".join(f"{b_[1]} {b_[2]} {b_[0] / 2**30:.2f}" for b_ in big[:25]))
            _GMRES_BASIS.clear()                       # the cached Krylov basis (1.2 GB at 1.1M cells) makes room
            torch.cuda.empty_cache()                   # for the build (torch's unused cache back to the device, where
            try:                                       # Warp allocates the probe buffers); the next GMRES re-creates it
                blocks.rebuild(x)
            except (torch.OutOfMemoryError, RuntimeError) as e_:
                # near the 24 GB limit (PERIM, 1.1M cells) a build can fail to allocate: free what can be freed and
                # try once more, else keep the current preconditioner (the build fails before it touches the blocks)
                if not isinstance(e_, torch.OutOfMemoryError) and "allocate" not in str(e_):
                    raise
                import gc
                gc.collect()
                torch.cuda.empty_cache()
                try:
                    blocks.rebuild(x)
                except (torch.OutOfMemoryError, RuntimeError) as e2_:
                    if not isinstance(e2_, torch.OutOfMemoryError) and "allocate" not in str(e2_):
                        raise
                    gc.collect()
                    torch.cuda.empty_cache()
                    log(f"odil-dc rebuild at {it} skipped (out of memory twice); the previous preconditioner is kept")
            _last_eval[0] = None                       # the build drives the backend: re-evaluate x below
            builds += 1
            last_build = it
            x_build = x
        if _t_late is not None and it == int(_t_late[0]):
            t_cfl_mult = _t_late[1]
            log(f"odil-dc temperature CFL multiplier -> {t_cfl_mult:g} at step {it}")
        torch.cuda.synchronize(dev)
        tb = time.perf_counter()
        if _last_eval[0] is not x:
            cs.residual(x)                             # the face flux / nu_eff of x on the application
        lam = geom.spectral_radius(cs, u_ref if lam_floor is None else float(lam_floor))
        if _prof2:
            torch.cuda.synchronize(dev)
            prof["lam"] = prof.get("lam", 0.0) + time.perf_counter() - tb

        def make_shift(cfl):
            shift = torch.empty(nb, n, dtype=torch.float64, device=dev)
            for f in range(nb):
                c = cfl * (turb_cfl if f in turb_rows else 1.0)
                if f in turb_rows:
                    c = min(c, turb_cfl_max)
                elif f == 4:
                    c = min(c * t_cfl_mult, t_cfl_max)
                if f == 3:
                    shift[f] = lam / (cfl * beta * u_ref * u_ref)
                else:
                    shift[f] = torch.maximum(lam, blocks.dsig[f].abs()) / c + pos_diag * torch.clamp(-blocks.dsig[f], min=0)
                if f == 4 and _t_relax > 0.0 and it >= _t_relax_from:
                    shift[f] = ((1.0 / _t_relax - 1.0) * max(cfl_max / cfl, 1.0) * blocks.dsig[f].abs()
                                + pos_diag * torch.clamp(-blocks.dsig[f], min=0))
            return shift if _phys_D is None else shift + _phys_D
        shift = torch.empty(nb, n, dtype=torch.float64, device=dev)
        for f in range(nb):
            c = cfl * (turb_cfl if f in turb_rows else 1.0)
            if f in turb_rows:
                c = min(c, turb_cfl_max)
            elif f == 4:
                c = min(c * t_cfl_mult, t_cfl_max)
            if f == 3:
                shift[f] = lam / (cfl * beta * u_ref * u_ref)
            else:
                shift[f] = torch.maximum(lam, blocks.dsig[f].abs()) / c + pos_diag * torch.clamp(-blocks.dsig[f], min=0)
            if f == 4 and _t_relax > 0.0 and it >= _t_relax_from:
                # LPCNS_ODIL_T_RELAX=a: the temperature rows advance like an implicitly relaxed equation (OpenFOAM's
                # relaxation factor a: diagonal x 1/a), i.e. with each cell's own coefficient instead of the floored
                # face speed -- slow cells no longer wait for the fan-face time step.  The factor holds at the CFL cap;
                # below it the shift grows with cap / CFL, so a CFL cut shortens the temperature step as well (without
                # it tf's PERIM_OPEN could not recover: the linear solves stayed at their cap at CFL 1)
                shift[f] = ((1.0 / _t_relax - 1.0) * max(cfl_max / cfl, 1.0) * blocks.dsig[f].abs()
                            + pos_diag * torch.clamp(-blocks.dsig[f], min=0))
        if _phys_D is not None:
            shift = shift + _phys_D                       # the physical time term of the step (LPCNS_ODIL_PHYS_DT)
        # LPCNS_ODIL_AMG_EVERY=k: refresh the preconditioner's shift and Galerkin sums only every k steps, after a
        # Picard rebuild, or when the CFL moved by more than 25 % since the last refresh (the operator always carries
        # the current shift; a stale preconditioner costs GMRES iterations, not accuracy)
        _amg_every = int(os.environ.get("LPCNS_ODIL_AMG_EVERY", "1"))
        if (_amg_every <= 1 or it == 1 or last_build == it or it - amg_state["it"] >= _amg_every
                or abs(cfl / amg_state["cfl"] - 1.0) > 0.25):
            blocks.set_shift(shift)
            if spc is not None:
                spc.update()
            elif _split:
                _split_update()
            else:
                amg.update()
            amg_state.update(it=it, cfl=cfl)
        if it == 1 and _split and _l1_probe:
            # LPCNS_ODIL_L1_PROBE=1: is the 5 x 5 block-Jacobi AMG stable at the CFL cap? Ten V-cycles as a
            # stationary iteration on a random right-hand side; a growing residual is the diverging smoother of the
            # PERIM meshes (pressure rows without diagonal dominance), seen there only ~45 steps later as a stalled
            # solve. With LPCNS_ODIL_L1_AUTO the l1 smoothing (and LPCNS_ODIL_L1_AUTO_CAP) then start at step 1.
            blocks.set_shift(make_shift(cfl_max))
            _split_update()
            x5.normal_()
            y5.zero_()
            L5.residual(wy5, wx5, wr5)
            r_first = float(r5.norm())
            for _ in range(10):
                amg5.vcycle(wr5, wc5)
                y5.add_(c5)
                L5.residual(wy5, wx5, wr5)
            ratio = float(r5.norm()) / max(r_first, 1e-300)
            unstable = not math.isfinite(ratio) or ratio > 1.0
            log(f"odil-dc smoother probe at CFL {cfl_max:g}: residual x{ratio:.3g} after 10 V-cycles "
                f"({'diverging' if unstable else 'stable'})")
            if unstable and _l1_auto > 0.0:
                amg5.l1 = amg2.l1 = _l1_auto
                if _l1_auto_cap > 0.0:
                    cfl_max = min(cfl_max, _l1_auto_cap)
                log(f"odil-dc l1 smoothing on from step 1 (weight {_l1_auto:g}, CFL cap {cfl_max:g})")
            blocks.set_shift(shift)
            _split_update()
        torch.cuda.synchronize(dev)
        tc = time.perf_counter()
        prof["rebuild"] += tb - ta
        prof["amg"] += tc - tb
        sflat = (shift - _phys_D).reshape(-1) if (fd_jvp and _phys_D is not None) else shift.reshape(-1)
        if fd_jvp:
            # LPCNS_ODIL_FDJVP=1: the Krylov product by a forward difference of the residual (one residual per
            # product against the taped JVP's ~2), step eps = sqrt((1 + |x|) eps_mach) / |v| (Pernice & Walker 1998)
            _xn = float(x.norm())

            def P(v, x=x, R=R, _xn=_xn):
                # the step on the device (no host sync per product); a zero v gives a zero product
                vn = v.norm().to(torch.float64)
                e_ = math.sqrt((1.0 + _xn) * 2.2e-16) / torch.where(vn > 0, vn, torch.ones_like(vn))
                return (cs.residual(x + e_ * v).detach() - R) / e_
        else:
            P = W.product(cs, x, "implicit")

        # P7b heat deflation (as the partitioned driver's --pt-heat-deflation): once merit < 0.25 the temperature rows'
        # pseudo-time term is made mean-free (the rank-1 projection on the shift weights removed), so the uniform-T
        # mode -- the room's energy balance -- is not damped by the pseudo time and is solved Newton-like
        defl = heat_defl and (_phys_D is None) and m < 0.25
        sT = sflat[4 * n:5 * n]
        sT_sum = float(sT.sum()) if defl else 0.0

        def op(v):
            if fine_prof:
                torch.cuda.synchronize(dev)
                t_op = time.perf_counter()
            if _op_compact:
                # LPCNS_ODIL_OP=compact: the step's operator is the compact (Picard / hybrid) matrix with its
                # pseudo-time diagonal -- a defect correction (the residual stays exact; only the step changes)
                ob.copy_((v.to(torch.float32)).view(nb, n).T)
                blocks.fine.matvec(wob, wpb)
                out = (pb.T.reshape(-1).to(torch.float64) * rowscale).to(v.dtype)
                if fine_prof:
                    torch.cuda.synchronize(dev)
                    prof["jvp"] = prof.get("jvp", 0.0) + time.perf_counter() - t_op
                return out
            vd = v.to(torch.float64)
            sv = sflat * vd
            if defl and sT_sum > 0.0:
                sv[4 * n:5 * n] -= (torch.dot(sT, vd[4 * n:5 * n]) / sT_sum) * sT
            out = ((P(vd) + sv) * rowscale).to(v.dtype)
            if fine_prof:
                torch.cuda.synchronize(dev)
                prof["jvp"] = prof.get("jvp", 0.0) + time.perf_counter() - t_op
            return out

        def precond(v):
            if fine_prof:
                torch.cuda.synchronize(dev)
                t_pc = time.perf_counter()
            z = _precond(v)
            if fine_prof:
                torch.cuda.synchronize(dev)
                prof["vcycle"] = prof.get("vcycle", 0.0) + time.perf_counter() - t_pc
            return z

        def _precond(v):
            if spc is not None:
                z = spc.apply((v / rowscale.to(v.dtype)).view(nb, n).T.float().contiguous()).T.reshape(-1).to(v.dtype)
            elif _split:
                vv = (v / rowscale.to(v.dtype)).view(nb, n)
                x5.copy_(vv[:5].T)
                x2.copy_(vv[5:].T)
                if _cpr:
                    xp_.copy_(vv[3:4].T)
                    amgp.vcycle(wxp, wyp)                  # stage 1: the pressure correction
                    e5.zero_()
                    e5[:, 3:4].copy_(yp_)
                    L5.matvec(we5, wc5)                    # A_c (0, 0, 0, dp, 0)
                    x5.sub_(c5)
                amg5.vcycle(wx5, wy5)
                if _cpr:
                    y5[:, 3:4].add_(yp_)                   # stage 2 on the remainder, plus the pressure correction
                for _ in range(_ncyc5 - 1):
                    # LPCNS_ODIL_NCYC5=k: k V-cycles of the 5 x 5 AMG per application (stationary correction)
                    L5.residual(wy5, wx5, wr5)
                    amg5.vcycle(wr5, wc5)
                    y5.add_(c5)
                amg2.vcycle(wx2, wy2)
                z = torch.cat([y5.T.reshape(-1), y2.T.reshape(-1)]).to(v.dtype)
                if _rackw:
                    z = _rackw_apply(z)
            else:
                xb.copy_((v / rowscale.to(v.dtype)).view(nb, n).T)
                amg.vcycle(wxb, wyb)
                z = yb.T.reshape(-1).to(v.dtype).clone()
            if closed:
                z.view(nb, n)[3] -= z.view(nb, n)[3].mean()
            return z

        if os.environ.get("LPCNS_ODIL_DIAG_CFLS") and it == 1:
            for cfl_t in [float(c_) for c_ in os.environ["LPCNS_ODIL_DIAG_CFLS"].split(",")]:
                sh = make_shift(cfl_t)
                blocks.set_shift(sh)
                (spc.update() if spc is not None else amg.update())
                sf = (sh - _phys_D).reshape(-1) if (fd_jvp and _phys_D is not None) else sh.reshape(-1)

                def op_t(v, sf=sf):
                    vd = v.to(torch.float64)
                    return ((P(vd) + sf * vd) * rowscale).to(v.dtype)

                for ncyc in (1, 3, 10):
                    def pc_t(v, ncyc=ncyc):
                        b = v / rowscale.to(v.dtype)
                        z = torch.zeros_like(b)
                        for _ in range(ncyc):
                            # stationary AMG on the compact system: z += M (b - A_c z)
                            xb.copy_(z.float().view(nb, n).T)
                            blocks.fine.matvec(wxb, wyb)
                            r = b - yb.T.reshape(-1).to(v.dtype)
                            if spc is not None:
                                z = z + spc.apply(r.float().view(nb, n).T.contiguous()).T.reshape(-1).to(v.dtype)
                            else:
                                xb.copy_(r.float().view(nb, n).T)
                                amg.vcycle(wxb, wyb)
                                z = z + yb.T.reshape(-1).to(v.dtype)
                        return z
                    t_ = time.perf_counter()
                    _, inf_ = gmres(op_t, pc_t, (-R * rowscale).to(krylov_dtype), restart=restart, maxiter=80, rtol=rtol)
                    log(f"odil-dc diag CFL {cfl_t:g}: {ncyc} V-cycle(s) per preconditioner -> GMRES {inf_['it']} "
                        f"rel {inf_['rel']:.2e} ({time.perf_counter() - t_:.2f} s)")
            blocks.set_shift(shift)
            (spc.update() if spc is not None else amg.update())
        if os.environ.get("LPCNS_ODIL_VBENCH") and it == 1:
            # diagnostics: wall and GPU-kernel time of one V-cycle, 7 x 7 against 5 x 5 + 2 x 2 (LPCNS_ODIL_SPLIT=1)
            from torch.profiler import profile as _prof, ProfilerActivity as _PA

            def _bench(tag, fn, reps=50):
                for _ in range(5):
                    fn()
                torch.cuda.synchronize(dev)
                t_ = time.perf_counter()
                for _ in range(reps):
                    fn()
                torch.cuda.synchronize(dev)
                wall = (time.perf_counter() - t_) / reps * 1e3
                with _prof(activities=[_PA.CUDA]) as pr_:
                    for _ in range(10):
                        fn()
                    torch.cuda.synchronize(dev)
                ev = [e for e in pr_.key_averages() if e.device_type is not None and "CUDA" in str(e.device_type)]
                gpu = sum(e.self_device_time_total for e in ev) / 10 / 1e3
                top = sorted(ev, key=lambda e: -e.self_device_time_total)[:6]
                log(f"odil-dc vbench {tag}: wall {wall:.2f} ms, GPU kernels {gpu:.2f} ms; top: " + "; ".join(
                    f"{e.key[:40]} {e.self_device_time_total / 10 / 1e3:.2f} ms x{e.count // 10}" for e in top))
            amg.update()
            xb.normal_()
            _bench("7x7 V-cycle", lambda: amg.vcycle(wxb, wyb))
            _bench("7x7 fine matvec", lambda: blocks.fine.matvec(wxb, wyb))
            if _split:
                _split_update()
                x5.normal_()
                x2.normal_()
                _bench("5x5 V-cycle", lambda: amg5.vcycle(wx5, wy5))
                _bench("2x2 V-cycle", lambda: amg2.vcycle(wx2, wy2))
                _bench("5x5 fine matvec", lambda: L5.matvec(wx5, wy5))
            v32 = torch.randn(nb * n, dtype=krylov_dtype, device=dev)
            if _split:
                _bench("precond split", lambda: _precond(v32))
                _split = False
                _bench("precond 7x7", lambda: _precond(v32))
                _split = True
            vt = torch.randn(nb * n, dtype=torch.float64, device=dev)
            _bench("exact JVP", lambda: P(vt), reps=20)
            _bench("residual", lambda: cs.residual(x), reps=20)
            raise SystemExit("odil-dc vbench done")
        if os.environ.get("LPCNS_ODIL_PROBE_SUB") and it == 1:
            for c_ in [float(s_) for s_ in os.environ["LPCNS_ODIL_PROBE_SUB"].split(",")]:
                blocks.set_shift(make_shift(c_))
                _sub_amg_diag(blocks, hier, spc, log, f"CFL {c_:g}", omega=omega, pre=pre, post=post)
            raise SystemExit("odil-dc subdiag done")
        def _probe_ne(xs, Rs, Rb, sf):
            # LPCNS_ODIL_PROBE_NE="ncore,halo,iters[,shifted]": after the full probe step, a nonlinear elimination
            # trial -- the ncore cells where the step raised |R_U|^2 most, grown by halo layers, solved alone (every
            # other unknown frozen) by masked FD-JFNK with the masked global preconditioner and a line search on the
            # subset norm; logs the subset norm and the global merit per local iteration (is the overshoot local?)
            nc_, hl_, it_ = (int(v_) for v_ in os.environ["LPCNS_ODIL_PROBE_NE"].split(",")[:3])
            shifted = len(os.environ["LPCNS_ODIL_PROBE_NE"].split(",")) > 3
            rsv = (Rs * rowscale).view(nb, n)
            inc = (rsv[:3] ** 2).sum(0) - ((Rb * rowscale).view(nb, n)[:3] ** 2).sum(0)
            mk = torch.zeros(n, dtype=torch.bool, device=dev)
            mk[torch.topk(inc, nc_).indices] = True
            fo_t = torch.as_tensor(blocks.fo, device=dev, dtype=torch.long)
            fn_t = torch.as_tensor(blocks.fn, device=dev, dtype=torch.long)
            for _ in range(hl_):
                m2 = mk.clone()
                m2[fn_t[mk[fo_t]]] = True
                m2[fo_t[mk[fn_t]]] = True
                mk = m2
            M = mk.to(torch.float64).repeat(nb)
            Mk = M.to(krylov_dtype)
            xs = xs.clone()
            Rk = Rs
            log(f"odil-dc probe NE: {int(mk.sum())} cells ({nc_} core, halo {hl_}), subset |R| "
                f"{float((M * Rk * rowscale).norm()):.3e}, merit {merit_of(Rk)[0]:.3e}")
            for k_ in range(it_):
                xn0 = float(xs.norm())

                def Pk(v, xs=xs, Rk=Rk):
                    vn = v.norm().to(torch.float64)
                    e_ = math.sqrt((1.0 + xn0) * 2.2e-16) / torch.where(vn > 0, vn, torch.ones_like(vn))
                    return (cs.residual(xs + e_ * v).detach() - Rk) / e_

                def opS(v):
                    vd = M * v.to(torch.float64)
                    out = Pk(vd) + (sf * vd if shifted else 0.0)
                    return (M * out * rowscale).to(v.dtype)

                def pcS(v):
                    return Mk * precond(v * Mk)

                dS, infS = gmres(opS, pcS, (-(M * Rk) * rowscale).to(krylov_dtype), restart=60, maxiter=120, rtol=1e-2)
                dS = M * dS.to(torch.float64)
                n0 = float((M * Rk * rowscale).norm())
                best = None
                for al in (1.0, 0.5, 0.25, 0.1):
                    xt = xs + al * dS
                    for f in turb_rows:
                        sl = slice(f * n, (f + 1) * n)
                        xt[sl] = torch.maximum(xt[sl], 1e-12 * torch.ones_like(xt[sl]))
                    Rt = cs.residual(xt).detach()
                    nt = float((M * Rt * rowscale).norm())
                    if best is None or nt < best[0]:
                        best = (nt, al, xt, Rt)
                    if nt < n0:
                        break
                nt, al, xt, Rt = best
                if nt < n0:
                    xs, Rk = xt, Rt
                mg, rl = merit_of(Rk)
                log(f"odil-dc probe NE it {k_ + 1}: GMRES {infS['it']} rel {infS['rel']:.1e}, alpha {al:g}, subset |R| "
                    f"{n0:.3e} -> {nt:.3e}{'' if nt < n0 else ' (rejected)'}, merit {mg:.3e} ("
                    + " ".join(f"{k} {v:.1e}" for k, v in rl.items()) + ")")

        if os.environ.get("LPCNS_ODIL_PROBE") and it == 1:
            # diagnostics: tight linear solves at several CFLs ("newton" = no shift at all, "inf" = only the
            # pos_diag part) and the merit along the step -- is the tail plateau the linear solve or the residual?
            pre_cfl = float(os.environ.get("LPCNS_ODIL_PROBE_PC_CFL", str(cfl)))
            blocks.set_shift(make_shift(pre_cfl))
            (spc.update() if spc is not None else amg.update())
            for tag in os.environ["LPCNS_ODIL_PROBE"].split(","):
                if tag == "newton":
                    sf = torch.zeros(nb * n, dtype=torch.float64, device=dev)
                else:
                    sh_p = make_shift(float(tag))
                    sf = (sh_p - _phys_D).reshape(-1) if (fd_jvp and _phys_D is not None) else sh_p.reshape(-1)

                def op_p(v, sf=sf):
                    vd = v.to(torch.float64)
                    return ((P(vd) + sf * vd) * rowscale).to(v.dtype)
                t_ = time.perf_counter()
                d_, inf_ = gmres(op_p, precond, (-R * rowscale).to(krylov_dtype), restart=100, maxiter=int(
                    os.environ.get("LPCNS_ODIL_PROBE_IT", "600")), rtol=float(os.environ.get("LPCNS_ODIL_PROBE_RTOL", "1e-3")))
                d_ = d_.to(torch.float64)
                if os.environ.get("LPCNS_ODIL_PROBE_FD", "0") == "1":
                    # consistency of the JVP with the residual along d: per field |FD - Jd| / |Jd|
                    Rx = cs.residual(x).detach()
                    Jd = P(d_)
                    for eps in (1e-2, 1e-3, 1e-4, 1e-5, 1e-6):
                        Re = cs.residual(x + eps * d_).detach()
                        Rm = cs.residual(x - eps * d_).detach()
                        fd = (Re - Rm) / (2 * eps)
                        log(f"odil-dc probe FD eps {eps:g}: " + " ".join(
                            f"{k} {float((fd[sl] - Jd[sl]).norm() / max(float(Jd[sl].norm()), 1e-300)):.1e}"
                            f"(|Jd| {float(Jd[sl].norm()):.1e})" for k, sl in slices.items()))
                    cs.residual(x)
                    Jd2 = W.product(cs, x, "implicit")(d_)
                    log(f"odil-dc probe JVP repeat: rel diff {float((Jd2 - Jd).norm() / Jd.norm()):.1e}")
                # true linear residual in float64
                rr_ = (((P(d_) + sf * d_) + R) * rowscale).view(nb, n)
                lr = float(rr_.norm() / (R * rowscale).norm())
                r0_ = (R * rowscale).view(nb, n)
                log(f"odil-dc probe {tag}: linear residual left per field (|r_f| / |b_f|, share of |r|^2): " + " ".join(
                    f"{'uvwpTke'[f]} {float(rr_[f].norm() / r0_[f].norm().clamp_min(1e-300)):.2e}/"
                    f"{float(rr_[f].norm() ** 2 / rr_.norm() ** 2):.2f}" for f in range(nb)))
                du_ = float(d_.view(nb, n)[:3].abs().max())
                msg = []
                for a_ in (1.0, 0.5, 0.25, 0.1, 0.03):
                    xn_ = x + a_ * d_
                    for f in turb_rows:
                        sl = slice(f * n, (f + 1) * n)
                        xn_[sl] = torch.maximum(xn_[sl], 1e-12 * torch.ones_like(xn_[sl]))
                    Rn_ = cs.residual(xn_).detach()
                    if a_ == 1.0 and os.environ.get("LPCNS_ODIL_PROBE_SAVE"):
                        # the step and both residuals, for a look at where a full step raises the residual
                        torch.save({"it": 0, "x0": x.detach().cpu(), "x1": xn_.detach().cpu(), "R0": R.detach().cpu(),
                                    "R1": Rn_.cpu(), "shift": sf.detach().cpu(), "cfl": float(tag), "rel0": rel,
                                    "rel1": merit_of(Rn_)[1], "gmres": inf_},
                                   os.environ["LPCNS_ODIL_PROBE_SAVE"] + f"_{tag}.pt")
                    if a_ == 1.0 and os.environ.get("LPCNS_ODIL_PROBE_NE"):
                        _probe_ne(xn_, Rn_, R, sf)
                    mn_, rl_ = merit_of(Rn_)
                    msg.append(f"a={a_:g}: {mn_:.3e} (" + " ".join(f"{k} {v:.1e}" for k, v in rl_.items()) + ")")
                log(f"odil-dc probe {tag}: GMRES {inf_['it']} rel {inf_['rel']:.1e} (true {lr:.1e}), max|du| {du_:.3g}, "
                    f"{time.perf_counter() - t_:.1f} s; merit now {m:.3e}; " + " | ".join(msg))
            raise SystemExit("odil-dc probe done")
        if os.environ.get("LPCNS_ODIL_DIAG") and it in [int(s_) for s_ in os.environ["LPCNS_ODIL_DIAG"].split(",")]:
            _odil_dc_diag(blocks, amg, op, precond, R * rowscale, nb, n, names, cfl, xb, yb, wxb, wyb, krylov_dtype, restart, rtol, rowscale)
        xshift = None
        Rin = R
        if boost_m and m < boost_below and bX:
            # min_gamma |rowscale (R - dR gamma)|; solve from the recombined residual, shift x by -dX gamma
            Y = torch.stack([r_ * rowscale for r_ in bR], dim=1)
            Q_, T_ = torch.linalg.qr(Y)
            gam = torch.linalg.solve_triangular(T_, (Q_.T @ (R * rowscale)).unsqueeze(1), upper=True).squeeze(1)
            if torch.isfinite(gam).all():
                Rin = R - torch.stack(bR, dim=1) @ gam
                xshift = -(torch.stack(bX, dim=1) @ gam)
                boost_used += 1
        if _split and os.environ.get("LPCNS_ODIL_ROWDIAG") and it in [int(s_) for s_ in os.environ["LPCNS_ODIL_ROWDIAG"].split(",")]:
            # diagnostics only: how far the compact flow block (the matrix under the preconditioner, with its shift)
            # is from the exact operator, per row field, inside and outside a slab of cells
            # (LPCNS_ODIL_ROWDIAG_BAND="axis,lo,hi"), for a random vector in one column field at a time and for the
            # preconditioned right-hand side
            ax_, lo_, hi_ = os.environ.get("LPCNS_ODIL_ROWDIAG_BAND", "1,0.7,0.9").split(",")
            pos_ = cs.solver.cpd_cell_pos[cs.interior][:, int(ax_)]
            in_ = (pos_ >= float(lo_)) & (pos_ <= float(hi_))
            rs_ = rowscale.to(torch.float64) if torch.is_tensor(rowscale) else rowscale
            torch.manual_seed(0)
            fn_ = ("Ux", "Uy", "Uz", "p", "T")
            log(f"odil-dc {it:4d}  rowdiag: {int(in_.sum())} of {n} cells in the slab; |(A_c - J) v| / |J v| per row "
                f"field, slab | rest (|J v| share of the slab)")
            for c_ in list(range(5)) + [-1]:
                v_ = torch.zeros(nb * n, dtype=torch.float64, device=dev)
                if c_ >= 0:
                    v_[c_ * n:(c_ + 1) * n] = torch.randn(n, dtype=torch.float64, device=dev)
                else:
                    v_.view(nb, n)[:5] = precond((-Rin * rowscale).to(krylov_dtype)).to(torch.float64).view(nb, n)[:5]
                ye_ = (op(v_.to(krylov_dtype)).to(torch.float64) / rs_).view(nb, n)[:5]
                x5.copy_(v_.view(nb, n)[:5].T)
                L5.matvec(wx5, wy5)
                e_ = y5.T.to(torch.float64) - ye_
                log(f"odil-dc {it:4d}  rowdiag col {'M^-1 b' if c_ < 0 else fn_[c_]:6s} " + "  ".join(
                    f"{fn_[r_]} {float(e_[r_][in_].norm() / max(float(ye_[r_][in_].norm()), 1e-300)):.2f} | "
                    f"{float(e_[r_][~in_].norm() / max(float(ye_[r_][~in_].norm()), 1e-300)):.2f} "
                    f"({float(ye_[r_][in_].norm() ** 2 / max(float(ye_[r_].norm() ** 2), 1e-300)):.2f})"
                    for r_ in range(5)))
        if os.environ.get("LPCNS_ODIL_LINRES") and it in [int(s_) for s_ in os.environ["LPCNS_ODIL_LINRES"].split(",")]:
            # diagnostics only: what the preconditioned Krylov solve leaves of this step's linear system after 5, 20
            # and 80 iterations, per equation (relative to that equation's right-hand side), and the residual
            # vectors with the cell positions for a split by region (LPCNS_ODIL_LINRES_PREFIX_<step>.pt)
            b_ = (-Rin * rowscale).to(krylov_dtype)
            keep_ = {"b": b_.view(nb, n).float().cpu(), "x": x.detach().view(nb, n).float().cpu(), "cfl": cfl,
                     "names": list(names), "pos": cs.solver.cpd_cell_pos[cs.interior].float().cpu()}
            for k_ in (5, 20, 80):
                d_, inf_ = gmres(op, precond, b_, restart=80, maxiter=k_, rtol=1e-10)
                r_ = (b_ - op(d_)).view(nb, n)
                keep_[f"r{k_}"] = r_.float().cpu()
                log(f"odil-dc {it:4d}  linres after {inf_['it']:3d}: total {float(r_.norm() / b_.norm()):.3f}  " + "  ".join(
                    f"{f_} {float(r_[j_].norm() / max(float(b_.view(nb, n)[j_].norm()), 1e-300)):.3f}"
                    f" ({float(b_.view(nb, n)[j_].norm() / b_.norm()):.2f})" for j_, f_ in enumerate(
                        ("Ux", "Uy", "Uz", "p", "T", "k", "eps")[:nb])))
            torch.save(keep_, os.environ.get("LPCNS_ODIL_LINRES_PREFIX", ".work/odil_linres") + f"_{it}.pt")
        dq, info = gmres(op, precond, (-Rin * rowscale).to(krylov_dtype), restart=restart, maxiter=krylov,
                         rtol=ew_eta if ew else rtol)
        if os.environ.get("LPCNS_ODIL_BLOCK_FINITE", "0") == "1" and not bool(torch.isfinite(dq).all()):
            # diagnostics: a non-finite step -- which of the right-hand side, the operator and the preconditioner
            # gives non-finite values (per field)
            b_ = (-Rin * rowscale).to(krylov_dtype)
            pb_, ob_ = precond(b_), op(b_)
            nf_ = lambda t_: (~torch.isfinite(t_.view(nb, n))).sum(1).tolist()  # noqa: E731
            log(f"odil-dc {it:4d}  non-finite step: rhs {nf_(b_)} (max {float(b_.abs().max()):.2e}), "
                f"op(rhs) {nf_(ob_)}, precond(rhs) {nf_(pb_)}, shift max {float(sflat.max()):.2e} "
                f"min {float(sflat.min()):.2e}, rowscale max {float(rowscale.max()) if torch.is_tensor(rowscale) else rowscale:.2e}")
        # a linear solve that missed its tolerance (with the forcing above 0.5: missed it by 1 %)
        lin_bad = info["rel"] > max(0.5, 1.01 * (ew_eta if ew else rtol))
        _l1_switch(info)
        dq = dq.to(torch.float64)
        if xshift is not None:
            dq = dq + xshift
        torch.cuda.synchronize(dev)
        td = time.perf_counter()
        prof["krylov"] += td - tc
        lin_total += info["it"]
        if lin_bad:
            cfl = max(cfl * 0.5, cfl_min)
            cfl_ref = (cfl, mprev)
        dqv = dq.view(nb, n)
        du = dqv[:3].abs().max().item()
        if du > max_du * u_ref:
            dq = dq * (max_du * u_ref / du)
            du_scaled.append(max_du * u_ref / du)
        x_old = x.clone()

        def trial(alpha):
            xn_ = x + alpha * dq
            cl_ = 0
            for f in turb_rows:
                sl = slice(f * n, (f + 1) * n)
                lo = clip * x_old[sl]
                if _turb_min > 0.0:
                    lo = torch.maximum(lo, torch.full_like(lo, _turb_min))
                if _nut_max > 0.0 and f == 6 and nb == 7:
                    # LPCNS_ODIL_NUT_MAX=nu_max (k-epsilon): the iterate keeps eps >= Cmu k^2 / nu_max, so a cell
                    # whose eps decays under the multiplicative clip towards the smooth floor (nu_t ~ 1e5 m2/s) is
                    # held at a bounded turbulent viscosity; the residual is unchanged (only the update path)
                    lo = torch.maximum(lo, 0.09 * xn_[5 * n:6 * n] ** 2 / _nut_max)
                cl_ += int((xn_[sl] < lo).sum())
                xn_[sl] = torch.maximum(xn_[sl], lo)
            if _uclip > 0.0:
                # LPCNS_ODIL_UCLIP=L: the velocity change of a step limited to L m/s per cell (the vector scaled), in
                # place of the global max_du scaling by the worst cell: the overshoot of a full step sits in ~0.1 % of the
                # cells (the tile jets in front of the rack rows on PERIM, |du| up to 5 m/s; probe pbs30)
                du_ = (xn_ - x_old).view(nb, n)[:3]
                dn_ = du_.norm(dim=0)
                fac_ = torch.clamp(_uclip / dn_.clamp_min(1e-300), max=1.0)
                cl_ += int((fac_ < 1.0).sum())
                xn_.view(nb, n)[:3] = x_old.view(nb, n)[:3] + du_ * fac_
            if _tclip > 0.0 or _tbox is not None:
                # LPCNS_ODIL_TCLIP=L: the temperature change of a step limited to L per cell; LPCNS_ODIL_TBOX="lo,hi":
                # the temperature kept inside the physical range (supply air .. a ceiling). The excursions start in a
                # few dozen cells in the gaps at rack frames / server tops, where one step moved T by 75-354 K
                # (TJUMP captures); the steady field satisfies both, so only the path changes
                sT_ = slice(4 * n, 5 * n)
                tn_ = xn_[sT_]
                if _tclip > 0.0:
                    tn_ = torch.clamp(tn_, x_old[sT_] - _tclip, x_old[sT_] + _tclip)
                if _tbox is not None:
                    tn_ = torch.clamp(tn_, _tbox[0], _tbox[1])
                cl_ += int((tn_ != xn_[sT_]).sum())
                xn_[sT_] = tn_
            Rn_ = cs.residual(xn_).detach()
            _last_eval[0] = xn_                       # the application now holds xn_'s face flux / nu_eff
            mn_, reln_ = merit_of(Rn_)
            return xn_, Rn_, mn_, reln_, cl_

        xn, Rn, mn, reln, clipped = trial(1.0)
        redo_alpha = None
        if ew and ew_redo and ew_eta > 1.01 * rtol and (not math.isfinite(mn) or mn > ew_redo * m):
            # LPCNS_ODIL_EW_REDO=r: a loosely solved step that raised the merit by more than r is solved again to the
            # tight tolerance at the same CFL (the loose solve can leave the near-singular rack-loop temperature mode
            # -- the CBBM mean the preconditioner omits -- excited; tightening only the next step comes too late)
            tr_ = time.perf_counter()
            loose_ = (xn, Rn, mn, reln, clipped, dq, info, lin_bad)
            dq, info = gmres(op, precond, (-Rin * rowscale).to(krylov_dtype), restart=restart, maxiter=krylov,
                             rtol=ew_redo_eta)
            lin_bad = info["rel"] > max(0.5, 1.01 * ew_redo_eta)
            _l1_switch(info)
            lin_total += info["it"]
            dq = dq.to(torch.float64)
            if xshift is not None:
                dq = dq + xshift
            du = dq.view(nb, n)[:3].abs().max().item()
            if du > max_du * u_ref:
                dq = dq * (max_du * u_ref / du)
            ew_redone += 1
            ew_eta = ew_redo_eta
            prof["krylov"] += time.perf_counter() - tr_
            td += time.perf_counter() - tr_                   # keep the redo's Krylov time out of "residual"
            xn, Rn, mn, reln, clipped = trial(1.0)
            if ew_redo_best:
                # LPCNS_ODIL_EW_REDO_BEST=1: the tightly solved step replaces the loose one only where it is the
                # better of the two; where it raises the merit as well (the full Newton step of the pseudo-time system
                # overshoots: |R_U| up, |R_p| down) it is shortened to 1/2 and 1/4 first
                cands_ = [(1.0, (xn, Rn, mn, reln, clipped))]
                while not any(math.isfinite(c_[1][2]) and c_[1][2] <= m for c_ in cands_) and cands_[-1][0] > 0.25:
                    cands_.append((cands_[-1][0] * 0.5, trial(cands_[-1][0] * 0.5)))
                fin_ = [c_ for c_ in cands_ if math.isfinite(c_[1][2])]
                pick_ = min(fin_, key=lambda c_: c_[1][2]) if fin_ else cands_[0]
                use_loose = math.isfinite(loose_[2]) and loose_[2] <= pick_[1][2]
                log(f"odil-dc {it:4d}  redo: merit {m:.3e}, loose step {loose_[2]:.3e}, tight "
                    + ", ".join(f"x{a_:g} {c_[2]:.3e}" for a_, c_ in cands_)
                    + f" (GMRES {info['it']} rel {info['rel']:.2e}); kept " + ("loose" if use_loose else f"tight x{pick_[0]:g}"))
                if use_loose:
                    xn, Rn, mn, reln, clipped, dq, info, lin_bad = loose_
                else:
                    xn, Rn, mn, reln, clipped = pick_[1]
                    redo_alpha = pick_[0]
        step_alpha = 1.0 if redo_alpha is None else redo_alpha
        if ls_on and (not math.isfinite(mn) or mn > ls_tol * mprev):
            # backtracking on the merit (the step is a pseudo-time step; a merit increase means the nonlinearity
            # took over): halve up to ls_max times, keep the best
            best = (xn, Rn, mn, reln, clipped)
            a = 1.0
            for _ in range(ls_max):
                a *= 0.5
                cand = trial(a)
                if math.isfinite(cand[2]) and (not math.isfinite(best[2]) or cand[2] < best[2]):
                    best = cand
                if math.isfinite(cand[2]) and cand[2] <= mprev:
                    break
            xn, Rn, mn, reln, clipped = best
            step_alpha = a
            ls_steps.append(a)
        prof["residual"] += time.perf_counter() - td
        event = ""
        gm_fail = lin_bad and os.environ.get("LPCNS_ODIL_REJECT_GMRES", "0") == "1"
        _mj = float(os.environ.get("LPCNS_ODIL_MJUMP", "0"))
        if (_mj > 0.0 and it > 20 and (not math.isfinite(mn) or mn > _mj * m)
                and getattr(solve_odil_dc, "_mj_n", 0) < int(os.environ.get("LPCNS_ODIL_MJUMP_N", "3"))):
            # diagnostics (LPCNS_ODIL_MJUMP=f): the first steps (accepted or not) whose trial raises the merit by more
            # than f -- the states before / after, both residuals, the step and its pseudo-time shift are saved
            solve_odil_dc._mj_n = getattr(solve_odil_dc, "_mj_n", 0) + 1
            _p = os.environ.get("LPCNS_ODIL_SAVE_PREFIX", ".work/odil_state") + f"_mjump_{it}.pt"
            torch.save({"it": it, "x0": x.detach().cpu(), "x1": xn.detach().cpu(), "R0": R.detach().cpu(),
                        "R1": Rn.detach().cpu(), "dq": dq.detach().cpu(), "shift": shift.detach().cpu(), "cfl": cfl,
                        "rowscale": rowscale.detach().cpu() if torch.is_tensor(rowscale) else rowscale,
                        "rel0": rel, "rel1": reln, "m0": m, "m1": mn, "gmres": info}, _p)
            log(f"odil-dc merit jump at {it}: {m:.3e} -> {mn:.3e}, saved {_p}")
        if not math.isfinite(mn) or mn > reject * mprev or gm_fail:
            rej_run += 1
            if rej_run > rej_max:
                _stall = float(os.environ.get("LPCNS_ODIL_STALL_STOP", "0"))
                if _stall > 0.0 and math.isfinite(m) and m < _stall:
                    # LPCNS_ODIL_STALL_STOP=m*: a march that stalls below merit m* (past the caller's plateau, on a
                    # flow with a persistent unsteady part) ends with the last accepted state instead of an error
                    log(f"odil-dc: stalled at step {it} (merit {m:.3e} < {_stall:g}); the last accepted state is kept")
                    if stop_rule is not None and hasattr(stop_rule, "stop_reason"):
                        stop_rule.stop_reason = f"stalled at step {it} below merit {_stall:g}"
                    break
                raise RuntimeError(f"odil-dc: {rej_run} consecutive pseudo-time steps rejected at step {it} (CFL {cfl:.3g}, "
                                   f"merit {m:.3e}, trial merit {mn:.3e}); the solve cannot proceed from this state")
            # the same state, residual and CFL give the same refused step again, so the floor cfl_min holds for the
            # first rejection of a run only: the second rebuilds the blocks at this state, and each further one
            # lowers the floor by 4 (accepted steps bring the CFL back above cfl_min)
            if rej_run == 2:
                last_build = -rebuild_every
            cfl = max(cfl * 0.25, cfl_min * 0.25 ** max(rej_run - 1, 0))
            cfl_ref = (cfl, mprev)
            rejected += 1
            event = "reject"
            log(f"odil-dc {it:4d}  rejected: trial merit {mn:.3e} against {mprev:.3e} (x{reject:g}), "
                f"{rej_run} in a row, next CFL {cfl:.3g}")
            bX.clear()
            bR.clear()
        else:
            rej_run = 0
            m_before = m
            if boost_m and m < boost_below:
                bX.append(xn - x)
                bR.append(Rn - R)
                if len(bX) > boost_m:
                    bX.pop(0)
                    bR.pop(0)
            if ew:
                # Eisenstat-Walker choice 2 (1996): eta = gamma (|F_k| / |F_k-1|)^alpha with the safeguard
                # max(., gamma eta_old^alpha) when that exceeds 0.1, kept in [rtol, eta_max]; a pseudo-time step that
                # lowers the merit by 1 % is not worth a linear solve to 3 %
                e2 = ew[0] * (mn / m) ** ew[1]
                sg = ew[0] * ew_eta ** ew[1]
                if sg > 0.1:
                    e2 = max(e2, sg)
                ew_eta = min(max(e2, rtol), ew[2])
                if mn > m * ew_up:
                    # the merit rose: choice 2 assumes a falling residual and would keep the loosest solve exactly
                    # when the step went wrong -- the next step is solved to the tight tolerance (LPCNS_ODIL_EW_UP)
                    ew_eta = rtol
            _tj = float(os.environ.get("LPCNS_ODIL_TJUMP", "0"))
            if _tj > 0.0 and it > 20 and "T" in rel and reln["T"] > _tj * rel["T"] and not getattr(solve_odil_dc, "_tj_done", 0):
                # diagnostics (LPCNS_ODIL_TJUMP=f): the first accepted step that raises |R_T| by more than f -- the
                # states before / after, both residuals, the step and its pseudo-time shift are saved
                solve_odil_dc._tj_done = 1
                _p = os.environ.get("LPCNS_ODIL_SAVE_PREFIX", ".work/odil_state") + f"_tjump_{it}.pt"
                torch.save({"it": it, "x0": x.detach().cpu(), "x1": xn.detach().cpu(), "R0": R.detach().cpu(),
                            "R1": Rn.detach().cpu(), "shift": shift.detach().cpu(), "cfl": cfl, "rel0": rel,
                            "rel1": reln, "gmres": info}, _p)
                log(f"odil-dc T jump at {it}: |R_T| {rel['T']:.2e} -> {reln['T']:.2e}, saved {_p}")
            x, R, m, rel = xn, Rn, mn, reln
            accepted += 1
            if _tbox is not None and _tbox_widen[0] > 0 and it > 100:
                top_ = int((x[4 * n:5 * n] >= _tbox[1] - 1e-9).sum())
                _tbox_top_run = _tbox_top_run + 1 if top_ else 0
                if _tbox_top_run >= _tbox_widen[0] and _tbox[1] < _tbox_widen[2]:
                    _tbox_old = _tbox[1]
                    _tbox[1] = min(_tbox_widen[2], _tbox[1] + max(_tbox_widen[1], 0.5 * _tbox[1]))
                    _tbox_top_run = 0
                    log(f"odil-dc temperature box raised {_tbox_old:g} -> {_tbox[1]:g} K at step {it}: "
                        f"{top_} cells held at the ceiling for {int(_tbox_widen[0])} accepted steps")
            if tcorr:
                # global energy mode: one-vector Galerkin correction on e = (T = 1 in every cell), least squares on
                # the scaled residual with the JVP at the step's linearisation point; kept only if the merit drops
                e = torch.zeros(nb * n, dtype=torch.float64, device=dev)
                e[4 * n:5 * n] = 1.0
                Je = P(e) * rowscale
                Rs = R * rowscale
                den = float(torch.dot(Je, Je))
                if den > 0.0:
                    c_ = -float(torch.dot(Je, Rs)) / den
                    xc = x + c_ * e
                    Rc = cs.residual(xc).detach()
                    _last_eval[0] = xc
                    mc, relc = merit_of(Rc)
                    if math.isfinite(mc) and mc < m:
                        x, R, m, rel = xc, Rc, mc, relc
                        tcorr_hist.append(c_)
            _ts_at = os.environ.get("LPCNS_ODIL_TSOLVE_AT")
            _ts_ev = int(os.environ.get("LPCNS_ODIL_TSOLVE_EVERY", "0"))
            if (_ts_at and it in [int(s_) for s_ in _ts_at.split(",")]) or (
                    _ts_ev and it % _ts_ev == 0 and it >= int(os.environ.get("LPCNS_ODIL_TSOLVE_FROM", "0"))):
                # LPCNS_ODIL_TSOLVE_*: the temperature rows solved alone (flow held) with shift
                # sT x LPCNS_ODIL_TSOLVE_SHIFT (0 = steady T); _EXIT=1 ends the run after it (diagnostic)
                _sT = sflat[4 * n:5 * n] * float(os.environ.get("LPCNS_ODIL_TSOLVE_SHIFT", "0"))
                x = _t_only_solve(cs, W, blocks, hier, x, _sT, log, qoi=_qoi if qoi_dir else None,
                                  iters=int(os.environ.get("LPCNS_ODIL_TSOLVE_ITERS", "3")),
                                  rtol=float(os.environ.get("LPCNS_ODIL_TSOLVE_RTOL", "1e-3")),
                                  maxit=int(os.environ.get("LPCNS_ODIL_TSOLVE_MAXIT", "200")),
                                  omega=omega, pre=pre, post=post)
                if _tbox is not None:
                    x[4 * n:5 * n] = torch.clamp(x[4 * n:5 * n], _tbox[0], _tbox[1])
                R = cs.residual(x).detach()
                _last_eval[0] = x
                m, rel = merit_of(R)
                log(f"odil-dc T-solve: merit after {m:.3e}")
                if os.environ.get("LPCNS_ODIL_TSOLVE_EXIT", "0") == "1":
                    raise SystemExit("odil-dc T-solve done")
            if qoi_dir and accepted % _every == 0:
                tq = time.perf_counter()
                out, cmp_ = _qoi(x)
                rec = {"step": it, "accepted": accepted, "wall_s": time.perf_counter() - t0,
                       "quantities": {k: float(v) for k, v in out.items()}, "compare": cmp_,
                       "cost_s": time.perf_counter() - tq}
                with open(_qd / "qoi_log.jsonl", "a") as fh:
                    fh.write(_json.dumps(rec) + "\n")
                t0 += time.perf_counter() - tq                 # the logging is not solve time
            if os.environ.get("LPCNS_ODIL_SAVE_AT") and it in [int(s_) for s_ in os.environ["LPCNS_ODIL_SAVE_AT"].split(",")]:
                torch.save(x.detach().cpu(), os.environ.get("LPCNS_ODIL_SAVE_PREFIX", ".work/odil_state") + f"_{it}.pt")
            if law == "ramp":
                # CFL x growth per accepted step (a rejection or a missed GMRES cuts it back above)
                target = cfl * growth if not (lin_bad) else cfl
            else:
                c_ref, m_ref = cfl_ref
                target = min(c_ref * (m_ref / max(m, 1e-300)), cfl * growth)
                if law == "ser+" and m < m_before * serp_tol and not (lin_bad):
                    # SER with a floor: an accepted step that lowered the merit grows the CFL by >= 1.25 (plain SER
                    # stalls after a rejection when the merit then moves slowly)
                    target = max(target, cfl * float(os.environ.get("LPCNS_ODIL_SERP_FLOOR", "1.25")))
            if step_alpha < 1.0:
                # a backtracked step: the CFL was too large for the nonlinearity -> scale it with the step taken
                target = min(target, cfl * step_alpha)
                cfl_ref = (target, m)
            if lin_bad:
                target = min(target, cfl)                     # a missed linear solve never grows the CFL
            _gt = int(os.environ.get("LPCNS_ODIL_GMRES_TARGET", "0"))
            if _gt > 0 and info["it"] > _gt:
                # LPCNS_ODIL_GMRES_TARGET=N: the CFL also tracks the linear-solve cost -- a step that took more than N
                # GMRES iterations scales the next CFL by N / its (the cost, not the merit, then sets the map)
                target = min(target, cfl * _gt / info["it"])
                cfl_ref = (target, m)
            if boost_m and boost_cfl > 0 and m < boost_below:
                target = boost_cfl                            # a fixed map while the recombination is active
            if _cap_late is not None and m < _cap_late[0] and cfl_max < _cap_late[1]:
                cfl_max = _cap_late[1]
                if len(_cap_late) > 2 and _cap_late[2] > 0.0:
                    last_build = -rebuild_every                # a third entry 1: rebuild the blocks at the raise
                log(f"odil-dc CFL cap raised to {cfl_max:g} at step {it} (merit {m:.3e})")
            cfl = min(max(target, cfl_min), cfl_max)
            mprev = m
        el = time.perf_counter() - t0
        row = {"it": it, "max_abs": float(R.abs().max()), "l2": float(R.norm()), "lambda": 1.0,
               "inner_iterations": info["it"], "inner_rtol": info["rel"], "reassembled": it == last_build or it == 1,
               "stalled": False, "cfl": cfl, "ptc_theta": 1.0 / cfl, "ptc_event": event, "merit": m,
               "clipped": clipped, "rejected": int(bool(event)), "seconds": el}
        row.update({f"rel_{k}": v for k, v in rel.items()})
        hist.append(row)
        log(f"odil-dc {it:4d}  merit {m:.3e}  CFL {cfl:9.3g}  gmres {info['it']:3d} (rel {info['rel']:.2e})  "
            + " ".join(f"{k} {v:.1e}" for k, v in rel.items())
            + (f"  mass {_mass_rel(R):.2e}" if _phys_inflow is not None else "") + f"  clip {clipped}  wall {el:7.2f}s {event}")
        if progress is not None:
            try:
                progress(row)
            except Exception:                                    # noqa: BLE001
                pass
    el = time.perf_counter() - t0
    if qoi_dir:
        out, cmp_ = _qoi(x)
        (_qd / "qoi_compare.json").write_text(_json.dumps({"compare": cmp_, "quantities": {k: float(v) for k, v in out.items()}}, indent=1))
        log(f"odil-dc S0-A at the end: inlet RMS {cmp_['inlet_rms_K']:.3f} K, max {cmp_['inlet_max_K']:.3f} K, heat "
            f"{100 * cmp_['heat_total_rel']:+.2f} %, returns " + ", ".join(
                f"{k.split(':')[1]} {v:+.3f}" for k, v in cmp_["returns"].items() if k.startswith("return_T")))
    log(f"odil-dc: {it} steps, {lin_total} GMRES iterations, {rejected} rejected, {builds + 1} Picard builds, "
        f"{el:.2f} s (setup {setup_s:.2f} s); merit {m:.3e}; " + (f"{ew_redone} redone; " if ew_redo else "")
        + (f"{rb_skipped} rebuilds postponed; " if rb_dx > 0.0 else "")
        + "".join(f"{k_} {prof[k_]:.2f} " for k_ in ("stop", "lam", "qoi") if k_ in prof)
        + (f"du-limited {len(du_scaled)} (median x{sorted(du_scaled)[len(du_scaled) // 2]:.2g}) " if du_scaled else "")
        + f"profile rebuild {prof['rebuild']:.2f} amg {prof['amg']:.2f} "
        f"krylov {prof['krylov']:.2f} residual {prof['residual']:.2f}"
        + (f" (jvp {prof.get('jvp', 0):.2f} vcycle {prof.get('vcycle', 0):.2f})" if fine_prof else ""))
    return x, hist
