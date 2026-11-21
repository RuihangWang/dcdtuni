"""Fused Warp residual of ``buoyantBoussinesqpicoext`` on the collocated Newton path (design:
docs/research/2026-10-14-warp-backend.md).

What it computes.  The square Newton residual ``CaseState.residual`` reads -- the momentum, continuity and energy rows
``[mom_x | mom_y | mom_z | cont | T]`` of the laminar Boussinesq equations -- for the configuration the paper's
convection, cavity and cylinder cases run: WLSQ "1st" cell gradients over the compact stencil, "Linear corrected"
face values and convection, "Linear overRelaxed" face gradients for diffusion, the compact corrected snGrad on
physical boundary faces (boundaryDiffusionScheme = "snGrad"), the cell-row pressure gradient, the momentum-weighted
face flux with OpenFOAM's phig inside the bracket, the Boussinesq body force at the cell centre, and the time term
of a transient step.  Nothing else: no pooled losses, scales or stashes (the torch forward builds them on every call
and Newton reads none of them).

Transient paths.  Every time-integrator hook of ``BaseSolver`` the transient entry arms is read on
each call, so one kernel source covers the paper's three integrators:
* BDF1 / BDF2 Newton (``implicit`` convection): the time term ``unsteady_coeff V / dt_cell (q - q_old)`` with the
  effective old level and ``dt_cell = dt_eff`` (re-read whenever ``set_time_step`` replaces it);
* Oseen (``set_convection_treatment("linearised", F*)``, ``set_ap_mode("oseen", F*)``): convection carried by the fixed
  advecting flux F* (upwind side from the sign of F*), a_P = sum max(F* S, 0) + nu |S| delta, no tangent through d_f;
* SBDF (``"explicit"`` convection, ``set_explicit_source(N*)``, ``set_ap_mode("sbdf")``): no convection in the rows,
  N* added to them, a_P = viscous + unsteady_coeff V / dt_cell;
* ``--ap-unsteady`` (the time term in a_P of the default mode) and ``dim = 2`` (w = 0 before the boundary passes and
  the out-of-plane rows closed to ``w``, as ``casestate.close_the_out_of_plane_rows`` does);
* time-aware boundary conditions (e.g. ``advectiveOutlet``): the affine ghost map is re-probed off the torch boundary
  passes whenever the old level they read (``set_bc_old_state`` or the time term's old level) or the step changes;
* the level quantities a linear scheme lags (``explicit_convection``, ``mwi_face_flux``) and the boundary-filled state
  the time march stores (``compound_state``), from the same kernels.
Each per-step input (old level, time coefficient, F*, N*, ghost offsets) lives in a persistent buffer that is
refreshed in place only when the tensor the solver holds changes (identity and version counter), so the captured CUDA
graphs stay valid across time steps; a new treatment captures a new graph (no recompilation).

How.  Every state quantity is a dual vector ``(value, t_1..t_K)`` (``FVsolver.warp_dual``), so the same kernels give
the residual (K = 0), the exact JVP (K = 1) and K colouring probes at once.  The d_f coefficient is differentiated
("implicit", the true Jacobian) unless ``frozen_df`` drops its tangents ("frozen", what the structural assembly
pattern covers).  No atomics (cell sums are gathers over a CSR of the cell's faces), so a result is bitwise
reproducible and the whole sequence is CUDA-graph capturable.

Replay path (W3).  The launch sequence is captured once per (tangent count, frozen, treatment) on a dedicated stream
(CUDA cannot capture on torch's legacy NULL stream) and replayed on the CALLER's torch stream -- cudaGraphLaunch accepts
the legacy stream -- so a call costs one input copy, one graph launch and one output clone, with no cross-stream event
waits.  A product keeps its linearisation point resident (copied once per linearisation, not per direction), and the
per-step inputs are copied only when they change.  LPCNS_WARP_REPLAY=side restores the W1d side-stream replay (for
A/B timing), LPCNS_WARP_GRAPH=0 launches eagerly on the caller's stream.

Exactness against the torch application, the reference this is tested against (``tests/test_buoyant_warp.py``,
``tests/test_transient_warp.py``): the geometry, the WLSQ weights, the boundary ghost maps and every coefficient are
read off the torch application after one forward -- the boundary ghosts as the affine map the torch boundary passes
apply (probed field by field, then checked on a random state), so no boundary rule is re-implemented by hand.
Configurations outside this set are refused, never silently run on torch.
"""
from __future__ import annotations

import os
from typing import Any

import torch
import warp as wp

from FVsolver.warp_dual import dconst, ddiv, dmax0, dmul, dual_type, dvalue_only, dzero

wp.config.quiet = True
wp.set_module_options({"enable_backward": False})   # tangents ride in the dual vectors; no Warp adjoint is used

U0, U1, U2, P, T = 0, 1, 2, 3, 4
NF = 5
# BaseSolver.CONVECTION_TREATMENTS / AP_MODES as kernel flags
CONV_MODES = {"implicit": 0, "explicit": 1, "linearised": 2}
AP_MODES = {"default": 0, "sbdf": 1, "oseen": 2}


# ------------------------------------------------------------------------------------------------ kernels
@wp.kernel
def load_kernel(x: wp.array(dtype=wp.float64), dirs: wp.array2d(dtype=wp.float64), cpd_of: wp.array(dtype=wp.int32),
                n: int, kdir: int, wzero: int, q: wp.array2d(dtype=Any)):
    """Interior rows of the compound state from the field-blocked Newton vector and its K tangent directions."""
    i, c = wp.tid()
    r = q[cpd_of[i], c] * wp.float64(0.0)
    if not (wzero == 1 and c == 2):
        r[0] = x[c * n + i]
        for k in range(kdir):
            r[k + 1] = dirs[k, c * n + i]
    q[cpd_of[i], c] = r


@wp.kernel
def ghost_kernel(ghost: wp.array(dtype=wp.int32), src: wp.array(dtype=wp.int32), A: wp.array3d(dtype=wp.float64),
                 b: wp.array2d(dtype=wp.float64), nf: int, q: wp.array2d(dtype=Any)):
    """Boundary ghosts: q[g] = A_g q[src_g] + b_g, the affine map of the torch boundary passes (b on the value only)."""
    g = wp.tid()
    gi = ghost[g]
    si = src[g]
    for c in range(nf):
        acc = q[si, 0] * A[g, c, 0]
        for c2 in range(1, nf):
            acc = acc + q[si, c2] * A[g, c, c2]
        q[gi, c] = acc + dconst(acc, b[g, c])


@wp.kernel
def wlsq_kernel(q: wp.array2d(dtype=Any), cpd_of: wp.array(dtype=wp.int32), off: wp.array(dtype=wp.int32),
                other: wp.array(dtype=wp.int32), w: wp.array(dtype=wp.vec3d), grad: wp.array3d(dtype=Any)):
    """WLSQ "1st" gradient of every field at the interior cells: sum_k w_k (q_other - q_i), w_k = A_i^-1 B_e."""
    i, c = wp.tid()
    ci = cpd_of[i]
    qi = q[ci, c]
    gx = dzero(qi)
    gy = dzero(qi)
    gz = dzero(qi)
    for k in range(off[i], off[i + 1]):
        d = q[other[k], c] - qi
        wk = w[k]
        gx = gx + wk[0] * d
        gy = gy + wk[1] * d
        gz = gz + wk[2] * d
    grad[ci, c, 0] = gx
    grad[ci, c, 1] = gy
    grad[ci, c, 2] = gz


@wp.kernel
def ghost_grad_kernel(ghost: wp.array(dtype=wp.int32), partner: wp.array(dtype=wp.int32), grad: wp.array3d(dtype=Any)):
    """Ghost gradients: the cyclic partner's (enforce_cyclic on gradients), zero on every other ghost."""
    g, c = wp.tid()
    gi = ghost[g]
    p = partner[g]
    for d in range(3):
        if p >= 0:
            grad[gi, c, d] = grad[p, c, d]
        else:
            grad[gi, c, d] = dzero(grad[gi, c, d])


@wp.func
def lin_corrected(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), o: int, n: int, c: int,
                  gc: wp.float64, gf: wp.float64, r: wp.vec3d):
    """gC q_o + gF q_n + (gC grad_o + gF grad_n) . r,  r = x_f - (gC x_o + gF x_n)."""
    v = gc * q[o, c] + gf * q[n, c]
    for d in range(3):
        v = v + r[d] * (gc * grad[o, c, d] + gf * grad[n, c, d])
    return v


@wp.func
def grad_dot(grad: wp.array3d(dtype=Any), o: int, n: int, c: int, wo: wp.float64, wn: wp.float64, v: wp.vec3d):
    """(wo grad_o + wn grad_n) . v"""
    s = (wo * grad[o, c, 0] + wn * grad[n, c, 0]) * v[0]
    s = s + (wo * grad[o, c, 1] + wn * grad[n, c, 1]) * v[1]
    s = s + (wo * grad[o, c, 2] + wn * grad[n, c, 2]) * v[2]
    return s


@wp.kernel
def face_pre_kernel(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), fo: wp.array(dtype=wp.int32),
                    fn: wp.array(dtype=wp.int32), gC: wp.array(dtype=wp.float64), gF: wp.array(dtype=wp.float64),
                    rcorr: wp.array(dtype=wp.vec3d), nrm: wp.array(dtype=wp.vec3d),
                    uf: wp.array2d(dtype=Any), f0: wp.array(dtype=Any)):
    """Face velocity ("Linear corrected") and the uncorrected advecting flux u_f . n, which a_P is built from."""
    f = wp.tid()
    o = fo[f]
    n = fn[f]
    r = rcorr[f]
    nv = nrm[f]
    u0 = lin_corrected(q, grad, o, n, 0, gC[f], gF[f], r)
    u1 = lin_corrected(q, grad, o, n, 1, gC[f], gF[f], r)
    u2 = lin_corrected(q, grad, o, n, 2, gC[f], gF[f], r)
    uf[f, 0] = u0
    uf[f, 1] = u1
    uf[f, 2] = u2
    f0[f] = u0 * nv[0] + u1 * nv[1] + u2 * nv[2]


@wp.kernel
def ap_kernel(f0: wp.array(dtype=Any), cpd_of: wp.array(dtype=wp.int32), off: wp.array(dtype=wp.int32),
              face: wp.array(dtype=wp.int32), sarea: wp.array(dtype=wp.float64), visc: wp.array(dtype=wp.float64),
              vol: wp.array(dtype=wp.float64), ap_time: wp.array(dtype=wp.float64), frozen: int,
              dcpd: wp.array(dtype=Any)):
    """a_P = sum_f max(F_f s_f, 0) + nu |s_f| delta_f (+ the time term), d_P = V_P / a_P on the interior cells."""
    i = wp.tid()
    k0 = off[i]
    ap = dmax0(f0[face[k0]] * sarea[k0]) + dconst(f0[face[k0]], visc[k0])
    for k in range(k0 + 1, off[i + 1]):
        ap = ap + dmax0(f0[face[k]] * sarea[k]) + dconst(ap, visc[k])
    ap = ap + dconst(ap, ap_time[i])
    d = ddiv(dconst(ap, vol[i]), ap)
    if frozen == 1:
        d = dvalue_only(d)
    dcpd[cpd_of[i]] = d


@wp.kernel
def dghost_kernel(ghost: wp.array(dtype=wp.int32), partner: wp.array(dtype=wp.int32), dcpd: wp.array(dtype=Any)):
    g = wp.tid()
    p = partner[g]
    if p >= 0:
        dcpd[ghost[g]] = dcpd[p]
    else:
        dcpd[ghost[g]] = dzero(dcpd[ghost[g]])


@wp.struct
class FaceGeom:
    fo: wp.array(dtype=wp.int32)
    fn: wp.array(dtype=wp.int32)
    gC: wp.array(dtype=wp.float64)
    gF: wp.array(dtype=wp.float64)
    nrm: wp.array(dtype=wp.vec3d)
    rcorr: wp.array(dtype=wp.vec3d)
    eCF: wp.array(dtype=wp.vec3d)
    inv_dCF: wp.array(dtype=wp.float64)
    nonorth: wp.array(dtype=wp.float64)
    # corrected-snGrad operands per variable type: owner / neighbour weights and correction vectors
    ow_u: wp.array(dtype=wp.float64)
    nw_u: wp.array(dtype=wp.float64)
    cv_u: wp.array(dtype=wp.vec3d)
    ow_p: wp.array(dtype=wp.float64)
    nw_p: wp.array(dtype=wp.float64)
    cv_p: wp.array(dtype=wp.vec3d)
    ow_t: wp.array(dtype=wp.float64)
    nw_t: wp.array(dtype=wp.float64)
    cv_t: wp.array(dtype=wp.vec3d)
    rC: wp.array(dtype=wp.vec3d)           # x_f - x_owner   (LinearUpwind)
    rF: wp.array(dtype=wp.vec3d)           # x_f - x_neighbour
    bnc: wp.array(dtype=wp.int32)          # physical (non-cyclic) boundary face: the face value is the ghost's
    overrelax: wp.array(dtype=wp.int32)
    mwi: wp.array(dtype=wp.int32)
    bcdiff: wp.array(dtype=wp.int32)
    zeroflux: wp.array(dtype=wp.int32)
    ghb: wp.array(dtype=wp.float64)        # gh_f * beta_f (phig)
    gpc: wp.array(dtype=wp.float64)        # grad_p_coeff
    cc: wp.array(dtype=wp.float64)         # convection_coeff
    ccont: wp.array(dtype=wp.float64)      # continuity_eq_coeff
    nu: wp.array(dtype=wp.float64)
    alpha: wp.array(dtype=wp.float64)


@wp.func
def sn_grad(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), o: int, n: int, c: int, nonorth: wp.float64,
            ow: wp.float64, nw: wp.float64, cv: wp.vec3d):
    """corrected_sn_grad: (q_n - q_o) delta_f + (ow grad_o + nw grad_n) . k_f"""
    return (q[n, c] - q[o, c]) * nonorth + grad_dot(grad, o, n, c, ow, nw, cv)


@wp.func
def overrelaxed_flux(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), o: int, n: int, c: int,
                     gc: wp.float64, gf: wp.float64, e: wp.vec3d, invd: wp.float64, apply: int, nv: wp.vec3d):
    """("Linear overRelaxed" face gradient) . n"""
    gx = gc * grad[o, c, 0] + gf * grad[n, c, 0]
    gy = gc * grad[o, c, 1] + gf * grad[n, c, 1]
    gz = gc * grad[o, c, 2] + gf * grad[n, c, 2]
    if apply == 1:
        corr = (q[n, c] - q[o, c]) * invd - (gx * e[0] + gy * e[1] + gz * e[2])
        gx = gx + corr * e[0]
        gy = gy + corr * e[1]
        gz = gz + corr * e[2]
    return gx * nv[0] + gy * nv[1] + gz * nv[2]


@wp.kernel
def face_main_kernel(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), uf: wp.array2d(dtype=Any),
                     f0: wp.array(dtype=Any), dcpd: wp.array(dtype=Any), g: FaceGeom, energy: int, upwind: int,
                     fmass: wp.array(dtype=Any), fmom: wp.array2d(dtype=Any), fen: wp.array(dtype=Any),
                     fvol: wp.array(dtype=wp.float64)):
    f = wp.tid()
    o = g.fo[f]
    n = g.fn[f]
    nv = g.nrm[f]
    gc = g.gC[f]
    gf = g.gF[f]
    # ---- advecting flux: the momentum-weighted correction with phig inside the bracket
    flux = f0[f]
    if g.mwi[f] == 1:
        snp = sn_grad(q, grad, o, n, P, g.nonorth[f], g.ow_p[f], g.nw_p[f], g.cv_p[f])
        interp = grad_dot(grad, o, n, P, gc, gf, nv)
        br = interp - snp
        if energy == 1:
            snt = sn_grad(q, grad, o, n, T, g.nonorth[f], g.ow_t[f], g.nw_t[f], g.cv_t[f])
            br = br + g.ghb[f] * (snt - grad_dot(grad, o, n, T, gc, gf, nv))
        df = wp.float64(0.5) * (dcpd[o] + dcpd[n])
        flux = flux + g.gpc[f] * dmul(df, br)
    fvol[f] = flux[0]                  # the advecting flux the preconditioner reads (torch: face_volumetric_flux)
    if g.zeroflux[f] == 1:
        flux = dzero(flux)
    # ---- convection: "Linear corrected" face values, or LinearUpwind for U / T (upwind cell value + its gradient
    # times the offset to the face; strictly positive flux picks the owner; the ghost value on physical boundaries)
    cf = g.cc[f] * flux
    # ---- diffusion: compact snGrad on the physical boundary faces, overRelaxed face gradient elsewhere
    bcd = g.bcdiff[f]
    for c in range(3):
        uc = uf[f, c]
        if upwind == 1 or upwind == 3:
            if g.bnc[f] == 1:
                uc = q[n, c]
            elif flux[0] > wp.float64(0.0):
                uc = q[o, c] + grad_dot(grad, o, o, c, wp.float64(1.0), wp.float64(0.0), g.rC[f])
            else:
                uc = q[n, c] + grad_dot(grad, n, n, c, wp.float64(1.0), wp.float64(0.0), g.rF[f])
        gfl = overrelaxed_flux(q, grad, o, n, c, gc, gf, g.eCF[f], g.inv_dCF[f], g.overrelax[f], nv)
        if bcd == 1:
            gfl = sn_grad(q, grad, o, n, c, g.nonorth[f], g.ow_u[f], g.nw_u[f], g.cv_u[f])
        fmom[f, c] = dmul(cf, uc) - g.nu[f] * gfl
    if energy == 1:
        tf = lin_corrected(q, grad, o, n, T, gc, gf, g.rcorr[f])
        if upwind >= 2:                # LinearUpwind for T (heated cylinder): as U above
            if g.bnc[f] == 1:
                tf = q[n, T]
            elif flux[0] > wp.float64(0.0):
                tf = q[o, T] + grad_dot(grad, o, o, T, wp.float64(1.0), wp.float64(0.0), g.rC[f])
            else:
                tf = q[n, T] + grad_dot(grad, n, n, T, wp.float64(1.0), wp.float64(0.0), g.rF[f])
        gft = overrelaxed_flux(q, grad, o, n, T, gc, gf, g.eCF[f], g.inv_dCF[f], g.overrelax[f], nv)
        if bcd == 1:
            gft = sn_grad(q, grad, o, n, T, g.nonorth[f], g.ow_t[f], g.nw_t[f], g.cv_t[f])
        fen[f] = dmul(cf, tf) - g.alpha[f] * gft
    fmass[f] = g.ccont[f] * flux


@wp.kernel
def cell_kernel(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), qold: wp.array2d(dtype=wp.float64),
                fmass: wp.array(dtype=Any), fmom: wp.array2d(dtype=Any), fen: wp.array(dtype=Any),
                cpd_of: wp.array(dtype=wp.int32), off: wp.array(dtype=wp.int32), face: wp.array(dtype=wp.int32),
                sarea: wp.array(dtype=wp.float64), vol: wp.array(dtype=wp.float64),
                tcoef: wp.array(dtype=wp.float64), ghb_cell: wp.array(dtype=wp.float64),
                pcoef: wp.array(dtype=wp.float64), src: wp.array2d(dtype=wp.float64), n: int, kdir: int,
                energy: int, res: wp.array(dtype=wp.float64), tan: wp.array2d(dtype=wp.float64)):
    """Cell rows: sum over the cell's faces of (face flux) x (signed area), plus the cell terms."""
    i = wp.tid()
    ci = cpd_of[i]
    k0 = off[i]
    s = sarea[k0]
    f = face[k0]
    rc = s * fmass[f]
    r0 = s * fmom[f, 0]
    r1 = s * fmom[f, 1]
    r2 = s * fmom[f, 2]
    re = s * fmass[f] * wp.float64(0.0)
    if energy == 1:
        re = s * fen[f]
    for k in range(k0 + 1, off[i + 1]):
        s = sarea[k]
        f = face[k]
        rc = rc + s * fmass[f]
        r0 = r0 + s * fmom[f, 0]
        r1 = r1 + s * fmom[f, 1]
        r2 = r2 + s * fmom[f, 2]
        if energy == 1:
            re = re + s * fen[f]
    v = vol[i]
    tc = tcoef[i]                       # unsteady_coeff V / dt_cell (zero on a steady case)
    gb = ghb_cell[i] * v                # gh beta V
    pc = pcoef[i] * v                   # theta_3 V
    r0 = r0 + tc * q[ci, 0] - dconst(r0, tc * qold[i, 0] + src[i, 0]) + pc * grad[ci, P, 0]
    r1 = r1 + tc * q[ci, 1] - dconst(r1, tc * qold[i, 1] + src[i, 1]) + pc * grad[ci, P, 1]
    r2 = r2 + tc * q[ci, 2] - dconst(r2, tc * qold[i, 2] + src[i, 2]) + pc * grad[ci, P, 2]
    if energy == 1:
        r0 = r0 - gb * grad[ci, T, 0]
        r1 = r1 - gb * grad[ci, T, 1]
        r2 = r2 - gb * grad[ci, T, 2]
        re = re + tc * q[ci, T] - dconst(re, tc * qold[i, 4] + src[i, 3])
    res[0 * n + i] = r0[0]
    res[1 * n + i] = r1[0]
    res[2 * n + i] = r2[0]
    res[3 * n + i] = rc[0]
    if energy == 1:
        res[4 * n + i] = re[0]
    for k in range(kdir):
        tan[k, 0 * n + i] = r0[k + 1]
        tan[k, 1 * n + i] = r1[k + 1]
        tan[k, 2 * n + i] = r2[k + 1]
        tan[k, 3 * n + i] = rc[k + 1]
        if energy == 1:
            tan[k, 4 * n + i] = re[k + 1]


def _declare_overloads(ks=(0, 1, 16)):
    """Every dual-number overload the solver launches, declared up front: the module is then built (and cached) ONCE
    with all tangent counts, instead of being rebuilt each time a launch meets a new count (W1d: nine builds on a cold
    cache, a module reload per count on a warm one).  A count outside ``ks`` still works (Warp adds the overload).

    This module holds the W1 kernels ONLY, so its compilation unit is the one of the W1d module after its warm-up
    (the same kernels, the same overloads): the steady path is bit for bit W1d's.  The W3 kernels (treatments,
    captures, dim 2, advectiveOutlet) live in ``buoyantBoussinesqpicoext_warp_transient`` -- their presence in this
    unit changed the CUDA compiler's inlining / FMA contraction of the shared functions (W3a: 3.5e-18 on cyl147k)."""
    for k in ks:
        D = dual_type(k)
        A1, A2, A3 = wp.array(dtype=D), wp.array2d(dtype=D), wp.array3d(dtype=D)
        wp.overload(load_kernel, {"q": A2})
        wp.overload(ghost_kernel, {"q": A2})
        wp.overload(wlsq_kernel, {"q": A2, "grad": A3})
        wp.overload(ghost_grad_kernel, {"grad": A3})
        wp.overload(face_pre_kernel, {"q": A2, "grad": A3, "uf": A2, "f0": A1})
        wp.overload(ap_kernel, {"f0": A1, "dcpd": A1})
        wp.overload(dghost_kernel, {"dcpd": A1})
        wp.overload(face_main_kernel, {"q": A2, "grad": A3, "uf": A2, "f0": A1, "dcpd": A1, "fmass": A1, "fmom": A2,
                                       "fen": A1})
        wp.overload(cell_kernel, {"q": A2, "grad": A3, "fmass": A1, "fmom": A2, "fen": A1})


if os.environ.get("LPCNS_WARP_OVERLOADS", "1") != "0":
    _declare_overloads()


# ------------------------------------------------------------------------------------------------ host side
def _wt():
    """The W3 kernels (a separate Warp module, see ``_declare_overloads``), imported on first use."""
    from FVsolver.applications import buoyantBoussinesqpicoext_warp_transient as module
    return module


def _wcht():
    """The CHT-0b kernels (a separate Warp module, see ``_declare_overloads``), imported on first use."""
    from FVsolver.applications import buoyantBoussinesqpicoext_warp_cht as module
    return module


def _i32(t, dev):
    return wp.from_torch(t.to(device=dev, dtype=torch.int32).contiguous())


def _f64(t, dev):
    return wp.from_torch(t.to(device=dev, dtype=torch.float64).contiguous().reshape(-1))


def _v3(t, dev):
    return wp.from_torch(t.to(device=dev, dtype=torch.float64).reshape(-1, 3).contiguous(), dtype=wp.vec3d)


def _csr(rows, n):
    """Order and offsets that group the entries by row (stable, so each row keeps the torch order)."""
    order = torch.argsort(rows, stable=True)
    counts = torch.bincount(rows, minlength=n)
    off = torch.zeros(n + 1, dtype=torch.int64, device=rows.device)
    off[1:] = torch.cumsum(counts, 0)
    return order, off


def _stamp(t):
    """What identifies the content of a solver attribute between two calls: the tensor object and its version."""
    return (None, -1) if t is None else (t, t._version)


def _same(a, b):
    return b is not None and a[0] is b[0] and a[1] == b[1]


_UNSET = object()


class _Shared:
    """Per-case mutable state shared by the implicit and the frozen view: the per-step inputs (persistent buffers the
    captured graphs read), their stamps, the work buffers, the graphs and the streams."""

    def __init__(self):
        self.stamps = {}
        self.bufs = {}
        self.wrappers = {}
        self.replay_side = os.environ.get("LPCNS_WARP_REPLAY", "caller") == "side"
        self.graphs_on = os.environ.get("LPCNS_WARP_GRAPH", "1") != "0"
        self.replay_path = None            # "caller" / "side" once the first graph replays (reported by the tests)
        self.ghost_probes = 0              # time-aware boundary conditions: re-probes of the ghost map
        self.last_mode = None


class BuoyantWarpResidual:
    """The Warp residual of one ``CollocatedSolver`` over a ``buoyantBoussinesqpicoext`` (or ``picoext``) case.

    ``residual(x)`` -> the ``out`` rows of ``CaseState.residual`` before the operator correction, the pin and the
    PTC shift (the caller adds those, as torch does); ``jvp(x, D)`` -> ``(F(x), J D^T)`` for a ``[K, N]`` block of
    directions.  Both read the old level the solver armed (``set_old_state``) unless one is passed, and every
    time-integrator setting of the discretisation at the time of the call.  Build once per case (geometry, weights and
    ghost maps are read off the torch application); ``share=`` builds a view that shares everything with another
    instance except ``frozen_df``.
    """

    SUPPORTED = {"gradSchemes": "1st", "interpolationSchemes": "Linear corrected",
                 "gradInterpolationSchemes": "Linear overRelaxed", "snGradSchemes": "corrected"}

    def __init__(self, cs, frozen_df: bool = False, share: "BuoyantWarpResidual | None" = None):
        if share is not None:
            self.__dict__.update(share.__dict__)
            self.frozen = 1 if frozen_df else 0
            return
        self.cs = cs
        app = cs.solver
        self.app = app
        self.frozen = 1 if frozen_df else 0
        self.dev = wp.device_from_torch(cs.device)
        # buoyantBoussinesqpicoext: [U, p_rgh, T], energy row and phig; picoext: [U, p]
        self.energy = 1 if hasattr(app, "alpha_coeff") and hasattr(app, "gh_face") else 0
        self.nf = 5 if self.energy else 4
        self._refuse_unsupported(app)
        # bit 1: LinearUpwind for U; bit 2: LinearUpwind for T -- one int, both face kernels unchanged in shape
        self.upwind = ((1 if str(app.U_scheme) == "LinearUpwind" else 0)
                       + (2 if self.energy and str(getattr(app, "T_scheme", "")) == "LinearUpwind" else 0))
        self.n = n = int(cs.n_cell)
        self.dim = int(getattr(app, "dim", 3))
        self.closew = self.wzero = 1 if self.dim == 2 else 0
        if int(cs.num_fields) != self.nf:
            raise NotImplementedError(f"warp backend: {cs.num_fields} fields, expected {self.nf}")
        bypass = getattr(cs, "_warp_bypass", False)
        cs._warp_bypass = True                            # a backend attached earlier must not answer for torch
        try:
            x0 = torch.zeros(n * self.nf, dtype=torch.float64, device=cs.device)
            cs.residual(x0)                               # every cache the extraction reads exists from here on
        finally:
            cs._warp_bypass = bypass
        tdev = cs.device
        self.cpd_of = cs.interior.to(torch.int64)
        self.n_cpd = int(app.num_cpd_cells)
        self._sh = _Shared()
        self._build_geometry(app, tdev)
        self._build_wlsq(app, tdev)
        self._build_ghosts(app, tdev)
        self._build_cells(app, tdev)
        self._build_coefficients(app, tdev)
        self._build_time_buffers(tdev)
        self._w_cpd = _i32(self.cpd_of, tdev)
        self._bufs = self._sh.bufs

    # ------------------------------------------------------------------ refusal
    def _refuse_unsupported(self, app):
        fvs = app.params.fvschemes
        for key, want in self.SUPPORTED.items():
            if str(fvs.get(key, "")) != want:
                raise NotImplementedError(f"warp backend: fvSchemes {key} = {fvs.get(key)!r}, supported {want!r}")
        if getattr(app, "_turb_width", 0):
            raise NotImplementedError("warp backend: turbulence transport (stage W4)")
        if not app._momentum_interpolation_mwi or not app._momentum_residual_cell or app._momentum_residual_face:
            raise NotImplementedError("warp backend: momentumResidual = cell with momentumInterpolation = MWI only")
        if not app._diffusion_sngrad_face or getattr(app, "_cyclic_diffusion_sngrad", False):
            raise NotImplementedError("warp backend: boundaryDiffusionScheme = snGrad, cyclic faces overRelaxed only")
        for flag in ("_conservative_cyclic_flux", "_mwi_dirichlet_pressure", "_dev2_active", "_stash_face_fluxes",
                     "_laminar_viscous_transpose"):
            if getattr(app, flag, False):
                raise NotImplementedError(f"warp backend: {flag} is set")
        if getattr(app, "_conv_theta", 1.0) != 1.0:
            raise NotImplementedError("warp backend: a Crank-Nicolson convection blend (_conv_theta != 1)")
        if getattr(app, "loss_checker_weight", 0) > 0:
            raise NotImplementedError("warp backend: checkerboard loss")
        if str(getattr(app, "U_scheme", "")) not in ("Linear corrected", "LinearUpwind"):
            raise NotImplementedError(f"warp backend: U_scheme = {getattr(app, 'U_scheme', None)!r}")
        if self.energy and str(getattr(app, "T_scheme", "")) not in ("Linear corrected", "LinearUpwind"):
            raise NotImplementedError(f"warp backend: T_scheme = {getattr(app, 'T_scheme', None)!r}")
        if int(getattr(app, "dim", 3)) not in (2, 3):
            raise NotImplementedError(f"warp backend: dim = {getattr(app, 'dim', None)}")

    def _refuse_dynamic(self, app):
        """Settings a caller can change after attach that the kernels do not cover (checked on every call)."""
        if getattr(app, "_conv_theta", 1.0) != 1.0:
            raise NotImplementedError("warp backend: a Crank-Nicolson convection blend (_conv_theta != 1)")
        if getattr(app, "_conservative_cyclic_flux", False):
            raise NotImplementedError("warp backend: _conservative_cyclic_flux is set")
        mode = getattr(app, "_conv_mode", "implicit")
        if mode not in CONV_MODES:
            raise NotImplementedError(f"warp backend: convection treatment {mode!r}")
        ap = getattr(app, "_ap_mode", "default")
        if ap not in AP_MODES:
            raise NotImplementedError(f"warp backend: a_P mode {ap!r}")

    # ------------------------------------------------------------------ geometry
    def _build_geometry(self, app, tdev):
        from FVdomain.SetBC.onthefly.BCbase import VARType
        from Utils.utilities import FaceType as FT
        nfc = int(app.unv_face.shape[0])
        self.n_face = nfc
        C, F = app.C_senders.long(), app.F_receivers.long()
        gC = app.gC.reshape(-1)
        gF = app.gF.reshape(-1)
        xC, xF = app.cpd_cell_pos[C], app.cpd_cell_pos[F]
        rcorr = app.face_pos - (gC[:, None] * xC + gF[:, None] * xF)
        ops = {}
        kinds = [("u", VARType.VELOCITY),
                 ("p", VARType.PRESSURE_RGH if self.energy else VARType.PRESSURE)]
        if self.energy:
            kinds.append(("t", VARType.TEMPERATURE))
        for key, vt in kinds:
            ow, nw, cv = app._snGrad_operands(vt)
            ops[key] = (ow.reshape(-1), nw.reshape(-1), cv.reshape(-1, 3))
        if not self.energy:
            ops["t"] = ops["u"]                       # unread placeholders (energy = 0)
        overrelax = ((app.face_type != FT.CYCLIC) & (app.face_type != FT.EMPTY)).to(torch.int32)
        mwi = app._mwi_face_mask().reshape(-1).to(torch.int32)
        bcdiff = app._bc_diffusion_face_mask().reshape(-1).to(torch.int32)
        zeroflux = self._zero_flux_faces(app, nfc, tdev)
        g = FaceGeom()
        g.fo, g.fn = _i32(C, tdev), _i32(F, tdev)
        g.gC, g.gF = _f64(gC, tdev), _f64(gF, tdev)
        g.nrm, g.rcorr, g.eCF = _v3(app.unv_face, tdev), _v3(rcorr, tdev), _v3(app.eCF, tdev)
        g.inv_dCF = _f64(1.0 / app.dCF.reshape(-1), tdev)
        g.nonorth = _f64(app.nonOrthDeltaCoeffs, tdev)
        g.ow_u, g.nw_u, g.cv_u = _f64(ops["u"][0], tdev), _f64(ops["u"][1], tdev), _v3(ops["u"][2], tdev)
        g.ow_p, g.nw_p, g.cv_p = _f64(ops["p"][0], tdev), _f64(ops["p"][1], tdev), _v3(ops["p"][2], tdev)
        g.ow_t, g.nw_t, g.cv_t = _f64(ops["t"][0], tdev), _f64(ops["t"][1], tdev), _v3(ops["t"][2], tdev)
        g.rC, g.rF = _v3(app.Cf, tdev), _v3(app.Ff, tdev)
        g.bnc = _i32(app.mask_boundary_non_cyclic_face.reshape(-1).to(torch.int32), tdev)
        g.overrelax, g.mwi, g.bcdiff, g.zeroflux = (_i32(overrelax, tdev), _i32(mwi, tdev), _i32(bcdiff, tdev),
                                                    _i32(zeroflux, tdev))
        self.geom = g          # nu, alpha, ghb, gpc, cc, ccont: _build_coefficients (they can change after attach)
        self._keep = (C, F, gC, gF, rcorr, ops, overrelax, mwi, bcdiff, zeroflux)

    @staticmethod
    def _zero_flux_faces(app, nfc, tdev):
        """Faces whose advecting flux a velocity boundary condition overwrites with zero (Symmetry, Slip)."""
        out = torch.zeros(nfc, dtype=torch.int32, device=tdev)
        bcs = getattr(app, "bc_uvw", None) or getattr(app, "uvw_bc", None)
        if bcs is None:
            return out
        fvp = app.fvpatch_batch
        for k, bc in enumerate(bcs):
            if type(bc).__name__ in ("Symmetry", "Slip"):
                out[int(fvp.start_idx_face[k]):int(fvp.end_idx_face[k])] = 1
        return out

    # ------------------------------------------------------------------ WLSQ weights
    def _build_wlsq(self, app, tdev):
        A = app.A_cell_to_cell.to(torch.float64)
        Bone = app.single_B_cell_to_cell.to(torch.float64)            # [E, 3(.. moments), 1]
        edges = app.neighbor_cpd_cell_x.long()                         # one-way [2, E]
        a, b = edges[0], edges[1]
        dim = self.dim
        valid = app.mask_interior_cell
        Ad = A[:, :dim, :dim]
        eye = torch.eye(dim, dtype=A.dtype, device=A.device).expand_as(Ad)
        Ainv = torch.linalg.inv_ex(torch.where(valid[:, None, None], Ad, eye))[0]
        Be = Bone[:, :dim, 0]                                          # [E, dim]
        # node b collects B_e (q_a - q_b); node a collects B_e (q_a - q_b) = -B_e (q_b - q_a)
        rows = torch.cat([b, a])
        other = torch.cat([a, b])
        coef = torch.cat([Be, -Be])
        w = torch.einsum("eij,ej->ei", Ainv[rows], coef)
        if dim == 2:
            w = torch.cat([w, torch.zeros_like(w[:, :1])], dim=1)
        # interior rows only, in the Newton cell order
        inv_map = torch.full((self.n_cpd,), -1, dtype=torch.int64, device=tdev)
        inv_map[self.cpd_of] = torch.arange(self.n, device=tdev)
        irow = inv_map[rows]
        keep = irow >= 0
        irow, other, w = irow[keep], other[keep], w[keep]
        order, off = _csr(irow, self.n)
        self.w_off = _i32(off, tdev)
        self.w_other = _i32(other[order], tdev)
        self.w_w = _v3(w[order], tdev)
        self._keep_w = (off, other[order], w[order])

    # ------------------------------------------------------------------ boundary ghosts (affine, probed)
    def _bc_pass(self, app, state, old_uvw=None):
        """The torch forward's cell-side boundary passes on a compound state [N_cpd, F] (clone in, filled out).
        ``old_uvw`` is the old velocity time-aware conditions read (``fvfield["uvw_old_cpd_cell"]``), zero if None."""
        uvw = state[:, 0:3].clone()
        old = torch.zeros_like(uvw) if old_uvw is None else old_uvw
        if self.energy:
            fv = {"uvw_cpd_cell": uvw, "p_rgh_cpd_cell": state[:, 3:4].clone(), "t_cpd_cell": state[:, 4:5].clone(),
                  "uvw_old_cpd_cell": old}
            keys = ("uvw_cpd_cell", "p_rgh_cpd_cell", "t_cpd_cell")
        else:
            fv = {"uvw_cpd_cell": uvw, "p_cpd_cell": state[:, 3:4].clone(), "uvw_old_cpd_cell": old}
            keys = ("uvw_cpd_cell", "p_cpd_cell")
        fv = app.enforce_bc_1st(fv)
        fv = app.enforce_cyclic(fv)
        fv = app.enforce_mixed(fv)
        fv = app.enforce_coupled(fv)
        return torch.cat([fv[k] for k in keys], dim=1)

    def _probe_map(self, app, old_uvw=None):
        """``(A, b)`` of the ghost map at the old level ``old_uvw``: nf + 1 torch boundary passes.  With two-source
        ghosts (conjugate interfaces, CHT-0b) ``A2`` of the second source is left in ``self._probed_A2``: the interior
        cells are coloured so that no ghost's two sources share a colour (``_ghost2_colour``), and one pass per (colour,
        field) separates the two parts, nf * colours + 1 passes in all."""
        ghosts = self._keep_g[0]
        colour = getattr(self, "_g2_colour", None)
        with torch.no_grad():
            zero = torch.zeros(self.n_cpd, self.nf, dtype=torch.float64, device=self.cs.device)
            b = self._bc_pass(app, zero, old_uvw)[ghosts]
            A = torch.zeros(ghosts.numel(), self.nf, self.nf, dtype=torch.float64, device=self.cs.device)
            if colour is None:
                for c in range(self.nf):
                    s = zero.clone()
                    s[self.cpd_of, c] = 1.0
                    A[:, :, c] = self._bc_pass(app, s, old_uvw)[ghosts] - b
                return A, b
            pos2, src2 = self._g2_pos, self._g2_src
            A2 = torch.zeros(pos2.numel(), self.nf, self.nf, dtype=torch.float64, device=self.cs.device)
            c_src = colour[self._keep_g[1][ghosts]]
            c_src2 = colour[src2]
            for k in range(self._g2_ncolour):
                members = self.cpd_of[colour[self.cpd_of] == k]
                for c in range(self.nf):
                    s = zero.clone()
                    s[members, c] = 1.0
                    m = self._bc_pass(app, s, old_uvw)[ghosts] - b
                    pick = c_src == k
                    A[pick, :, c] = m[pick]
                    pick2 = c_src2 == k
                    A2[pick2, :, c] = m[pos2][pick2]
        self._probed_A2 = A2
        return A, b

    def ghost_map_error(self, old_uvw=None, seed=20261014):
        """max |(A s_src [+ A2 s_src2] + b) - torch boundary passes(s)| / (max |want| + 1) on a random interior state,
        with the map LOADED now (the test's check of a re-probed time-aware map)."""
        ghosts, src = self._keep_g[0], self._keep_g[1]
        gen = torch.Generator(device="cpu").manual_seed(seed)
        with torch.no_grad():
            s = torch.zeros(self.n_cpd, self.nf, dtype=torch.float64, device=self.cs.device)
            s[self.cpd_of] = torch.randn(self.n, self.nf, generator=gen, dtype=torch.float64).to(s.device)
            want = self._bc_pass(self.app, s, old_uvw)[ghosts]
            got = torch.einsum("gij,gj->gi", self._gA_t, s[src[ghosts]]) + self._gb_t
            if getattr(self, "_g2_colour", None) is not None:
                got[self._g2_pos] += torch.einsum("gij,gj->gi", self._gA2_t, s[self._g2_src])
        return float((got - want).abs().max()) / (float(want.abs().max()) + 1.0)

    @staticmethod
    def _second_sources(app, tdev):
        """(ghost, second source) compound-cell ids of every ghost that reads a second interior cell: the conditions
        that declare ``ghost_second_source`` (the conjugate interface, ``setConjugate``).  Empty without them."""
        gh, s2 = [], []
        batch = getattr(app, "bcpatch_batch", None)
        for group_i, group in enumerate(getattr(batch, "coupled", []) if batch is not None else []):
            offset = batch.coupled_patch_idx_offset[group_i]
            for condition in group:
                # other coupled conditions keep the one-source map; one that is not affine in its owner is refused
                # by the ghost-map check
                if hasattr(condition, "ghost_second_source"):
                    g, s = condition.ghost_second_source(app, offset)
                    gh.append(g.reshape(-1).long().to(tdev))
                    s2.append(s.reshape(-1).long().to(tdev))
        if not gh:
            return None, None
        return torch.cat(gh), torch.cat(s2)

    @staticmethod
    def _ghost2_colour(first, second, n_cpd, tdev):
        """Greedy colouring of the interior cells with ``colour[first[j]] != colour[second[j]]`` for every two-source
        ghost j (a fluid / solid interface needs two colours); cells in no pair get colour 0."""
        import numpy as np
        a, b = first.cpu().numpy(), second.cpu().numpy()
        if (a == b).any():
            raise RuntimeError("warp backend: a two-source ghost reads the same cell twice")
        nodes = np.unique(np.concatenate([a, b]))
        idx = {int(v): i for i, v in enumerate(nodes)}
        adj = [[] for _ in nodes]
        for u, v in zip(a.tolist(), b.tolist()):
            adj[idx[u]].append(idx[v])
            adj[idx[v]].append(idx[u])
        col = np.full(len(nodes), -1, dtype=np.int64)
        for i in range(len(nodes)):
            used = {int(col[j]) for j in adj[i] if col[j] >= 0}
            k = 0
            while k in used:
                k += 1
            col[i] = k
        colour = torch.zeros(n_cpd, dtype=torch.int64)
        colour[torch.as_tensor(nodes)] = torch.as_tensor(col)
        return colour.to(tdev), int(col.max()) + 1 if col.size else 1

    def _build_ghosts(self, app, tdev):
        n_cpd = self.n_cpd
        interior = torch.zeros(n_cpd, dtype=torch.bool, device=tdev)
        interior[self.cpd_of] = True
        ghosts = torch.nonzero(~interior).reshape(-1)
        # source of each ghost: its cyclic partner if it has one, else the owner of its boundary face
        src = torch.full((n_cpd,), -1, dtype=torch.int64, device=tdev)
        C, F = app.C_senders.long(), app.F_receivers.long()
        bnd = ~interior[F]
        src[F[bnd]] = C[bnd]
        partner = torch.full((n_cpd,), -1, dtype=torch.int64, device=tdev)
        gh, pa = app._cyclic_ghost_partner_ids()
        if gh.numel():
            src[gh.long()] = pa.long()
            partner[gh.long()] = pa.long()
        if (src[ghosts] < 0).any():
            raise NotImplementedError("warp backend: a ghost cell with neither a boundary face nor a cyclic partner")
        self._keep_g = (ghosts, src, None, None, partner)
        # two-source ghosts (conjugate interfaces, CHT-0b): the second interior cell each of them reads
        self._g2_colour = None
        self.n_ghost2 = 0
        g2, s2 = self._second_sources(app, tdev)
        if g2 is not None:
            pos_of = torch.full((n_cpd,), -1, dtype=torch.int64, device=tdev)
            pos_of[ghosts] = torch.arange(ghosts.numel(), device=tdev)
            pos2 = pos_of[g2]
            if (pos2 < 0).any() or not bool(interior[s2].all()):
                raise RuntimeError("warp backend: a two-source ghost is not a ghost, or its second source not interior")
            if torch.unique(pos2).numel() != pos2.numel():
                raise NotImplementedError("warp backend: a ghost with more than one second source")
            self._g2_pos, self._g2_src = pos2, s2
            self._g2_colour, self._g2_ncolour = self._ghost2_colour(src[g2], s2, n_cpd, tdev)
        A, b = self._probe_map(app)
        self._gA_t, self._gb_t = A.contiguous(), b.contiguous()
        if self._g2_colour is not None:
            self._gA2_t = self._probed_A2.contiguous()
            self.n_ghost2 = int(self._g2_pos.numel())
            self.ghost2_ids = _i32(ghosts[self._g2_pos], tdev)
            self.ghost2_src = _i32(self._g2_src, tdev)
            self.ghost2_A = wp.from_torch(self._gA2_t)
        self._keep_g = (ghosts, src, self._gA_t, self._gb_t, partner)
        err = self.ghost_map_error()
        if err > 1e-13:
            raise RuntimeError(f"warp backend: ghost map is not affine in the owner/partner value ({err:.3e})")
        # time-aware boundary conditions (advectiveOutlet: the ghost is advanced from the OLD level): the map is
        # re-probed whenever that old level or the step changes (``_sync_ghosts``)
        self.time_aware = bool(getattr(getattr(app, "bcpatch_batch", None), "depends_on_old_state", False))
        if self.time_aware and self.n_ghost2:
            raise NotImplementedError("warp backend: time-aware boundary conditions next to a conjugate interface "
                                      "(the two-source ghost map is steady only, CHT-0b)")
        self._build_advective(app, tdev)
        self.ghost_ids = _i32(ghosts, tdev)
        self.ghost_src = _i32(src[ghosts], tdev)
        self.ghost_A = wp.from_torch(self._gA_t)
        self.ghost_b = wp.from_torch(self._gb_t)
        self.ghost_partner = _i32(partner[ghosts], tdev)
        self.n_ghost = int(ghosts.numel())

    def _build_advective(self, app, tdev):
        """The device path of time-aware ghosts when every such condition is an advectiveOutlet: the ghost rows whose
        offset moves with the old level are found by probing (two random old levels), their owner / boundary face /
        delta read off the mesh, and the kernel is checked against the torch passes at a third old level (else the
        per-level re-probe stays in charge).  ``adv_n`` = 0: no device path."""
        self.adv_n = 0
        self.adv_reason = "no time-aware boundary condition" if not self.time_aware else ""
        if not self.time_aware:
            return
        bcb = app.bcpatch_batch
        conds = list(bcb.old_state_dependent_conditions())
        if any(type(c).__name__ != "Advectiveoutlet" for c in conds):
            self.adv_reason = "a time-aware condition other than advectiveOutlet: re-probe per old level"
            return
        floors = {float(getattr(c, "alpha_floor", 0.02)) for c in conds}
        if len(floors) != 1:
            self.adv_reason = "advectiveOutlet patches with different alphaFloor: re-probe per old level"
            return
        ghosts, src = self._keep_g[0], self._keep_g[1]
        gen = torch.Generator(device="cpu").manual_seed(20261015)
        levels = [torch.randn(self.n_cpd, 3, generator=gen, dtype=torch.float64).to(tdev) for _ in range(3)]
        if self.dim == 2:
            for lv in levels:
                lv[:, 2] = 0.0
        A1, b1 = self._probe_map(app, levels[0])
        A2, b2 = self._probe_map(app, levels[1])
        if not (torch.equal(A1, self._gA_t) and torch.equal(A2, self._gA_t)):
            self.adv_reason = "the ghost map's A moves with the old level: re-probe per old level"
            return
        rows = ((b1 - b2).abs().amax(dim=1) > 0).nonzero().reshape(-1)
        if rows.numel() == 0 or not torch.equal(b1[rows, 3:], self._gb_t[rows, 3:]):
            self.adv_reason = "old-level dependence outside the velocity columns: re-probe per old level"
            return
        gcpd = ghosts[rows]
        owner = src[gcpd]
        C, F = app.C_senders.long(), app.F_receivers.long()
        face_of = torch.full((self.n_cpd,), -1, dtype=torch.int64, device=tdev)
        bnd = F >= 0
        face_of[F[bnd]] = torch.arange(F.numel(), device=tdev)[bnd]
        f = face_of[gcpd]
        pos = app.cpd_cell_pos
        self._adv_t = {"rows": rows, "ghost": gcpd, "owner": owner,
                       "nhat": app.unv_face[f].to(torch.float64).contiguous(),
                       "delta": (pos[gcpd] - pos[owner]).norm(dim=1).clamp_min(1e-12).to(torch.float64),
                       "old": torch.zeros(self.n_cpd, 3, dtype=torch.float64, device=tdev),
                       "dt": torch.zeros(app.dt_cell.numel(), dtype=torch.float64, device=tdev)}
        a = self._adv_t
        self.adv_floor = floors.pop()
        self.adv_rows, self.adv_ghost, self.adv_owner = _i32(rows, tdev), _i32(gcpd, tdev), _i32(owner, tdev)
        self.adv_nhat = wp.from_torch(a["nhat"], dtype=wp.vec3d)
        self.adv_delta = wp.from_torch(a["delta"])
        self.adv_old, self.adv_dt = wp.from_torch(a["old"]), wp.from_torch(a["dt"])
        # check against the torch passes at a third old level and the current step
        _, b3 = self._probe_map(app, levels[2])
        dt = getattr(app, "_bc_dt_cell", None)
        a["old"].copy_(levels[2])
        a["dt"].copy_((app.dt_cell if dt is None else dt).reshape(-1))
        got = self._gb_t.clone()
        wp.launch(_wt().advective_kernel, dim=int(rows.numel()),
                  inputs=[self.adv_old, self.adv_rows, self.adv_ghost, self.adv_owner, self.adv_nhat, self.adv_delta,
                          self.adv_dt, wp.float64(self.adv_floor), self.wzero], outputs=[wp.from_torch(got)],
                  device=self.dev, stream=self._caller_stream())
        err = float((got - b3).abs().max()) / (float(b3.abs().max()) + 1.0)
        self.adv_check = err
        if err > 1e-13:
            self.adv_reason = f"the device advectiveOutlet update misses the torch passes ({err:.2e}): re-probe"
            return
        self.adv_n = int(rows.numel())
        self.adv_reason = f"device advectiveOutlet update ({self.adv_n} ghosts, check {err:.1e})"

    # ------------------------------------------------------------------ cell incidences and cell terms
    def _build_cells(self, app, tdev):
        keep = app._non_empty_cells_face_indices.long()
        rows = app.cells_face_ptr.long()[keep]
        faces = app.cells_face.long()[keep]
        sarea = app.directed_area_cells_face.reshape(-1)[keep].to(torch.float64)
        order, off = _csr(rows, self.n)
        faces, sarea = faces[order], sarea[order]
        self._cfaces_t, self._csarea_t = faces, sarea
        self.c_off, self.c_face, self.c_sarea = _i32(off, tdev), _i32(faces, tdev), _f64(sarea, tdev)
        vol = app.cell_volume.reshape(-1).to(torch.float64)
        self._vol_t = vol.contiguous()
        self.c_vol = _f64(vol, tdev)
        self.c_aptime = _f64(torch.zeros_like(vol), tdev)          # the zero a_P time term of the default a_P
        interior_ids = app._facecv_interior_ids if self.energy else torch.nonzero(app.mask_interior_cell).reshape(-1)
        if not torch.equal(interior_ids.long(), self.cpd_of):
            raise RuntimeError("warp backend: the Newton cell order differs from the residual's interior order")

    # ------------------------------------------------------------------ physical coefficients (re-read on change)
    # What the entry may REPLACE after attach: nu_coeff / alpha_coeff (the exact float64 mu/rho of every continuation
    # rung, the Ra multipliers), gravity / hRef / beta (phig, the buoyancy), theta_PDE (the cell pressure coefficient),
    # grad_p / convection / continuity coefficients and the source terms.  The laminar forward's _nu_eff_face /
    # _alpha_eff_face ARE nu_coeff / alpha_coeff (the torch stashes go stale while Warp answers, so they are not read).
    _COEF_SOURCES = ("nu_coeff", "alpha_coeff", "gravity", "hRef", "beta", "theta_PDE", "grad_p_coeff",
                     "convection_coeff", "continuity_eq_coeff", "heat_source_term")

    def _coef_names(self):
        app = self.app
        names = [k for k in self._COEF_SOURCES if self.energy or k not in ("alpha_coeff", "gravity", "hRef", "beta",
                                                                           "heat_source_term")]
        # picoext refreshes source_term in every forward (base + old level): its constant part is source_term_base
        names.append("source_term" if self.energy else "source_term_base")
        return [k for k in names if torch.is_tensor(getattr(app, k, None))]

    def _coefficient_values(self, app):
        """Every coefficient buffer, computed from the discretisation's tensors now (float64)."""
        nfc, n, dev = self.n_face, self.n, self.cs.device
        bf, bc_ = app.batch_face, app.batch_cell

        def face(t):
            t = t.reshape(-1).to(torch.float64)
            return t.expand(nfc) if t.numel() == 1 else t

        nu = face(app.nu_coeff)
        v = {"nu": nu, "gpc": face(app.grad_p_coeff), "cc": face(app.convection_coeff),
             "ccont": app.continuity_eq_coeff[bf].reshape(-1).to(torch.float64),
             "visc": nu[self._cfaces_t] * self._csarea_t.abs() * app.nonOrthDeltaCoeffs.reshape(-1)[self._cfaces_t],
             "pcoef": app.theta_PDE[bc_, 3:4].reshape(-1).to(torch.float64)}
        src = torch.zeros(n, 4, dtype=torch.float64, device=dev)
        if self.energy:
            v["alpha"] = face(app.alpha_coeff)
            # beta as the torch forward reads it: on a multi-region case zero in the solid materials (the buoyancy
            # mask of _beta_face / _beta_cell, CHT-1 README section 5, ported in CHT-0b); the plain per-graph value,
            # the same tensor, otherwise
            beta_face = app._beta_face() if hasattr(app, "_beta_face") else app.beta[bf]
            beta_cell = app._beta_cell() if hasattr(app, "_beta_cell") else app.beta[bc_]
            v["ghb"] = (app.gh_face() * beta_face).reshape(-1).to(torch.float64)
            g_cell = app.gravity[bc_]
            ghRef = torch.norm(g_cell, dim=1, keepdim=True) * app.hRef[bc_]
            pos = app.cpd_cell_pos.index_select(0, app._facecv_interior_ids)
            gh = ((pos * g_cell).sum(dim=1, keepdim=True) - ghRef).reshape(-1)
            v["ghbc"] = (gh * beta_cell.reshape(-1)).to(torch.float64)
            # momentum source theta_5 V (three components, as time_scheme does) and heat source theta_8 V; with an
            # mmsSource file the momentum source is per component already ([n, 3], CHT-0a / CHT-0b)
            mom = app.source_term.reshape(n, -1).to(torch.float64)
            src[:, 0:3] = mom.expand(n, 3) if mom.shape[1] == 1 else mom[:, 0:3]
            src[:, 3] = app.heat_source_term.reshape(n).to(torch.float64)
        else:
            v["alpha"] = torch.zeros(nfc, dtype=torch.float64, device=dev)
            v["ghb"] = torch.zeros(nfc, dtype=torch.float64, device=dev)
            v["ghbc"] = torch.zeros(n, dtype=torch.float64, device=dev)
            # a manufactured-solution source is per component already ([n, 3]); otherwise one value per cell
            mom = app.source_term_base.reshape(n, -1).to(torch.float64)
            src[:, 0:3] = mom.expand(n, 3) if mom.shape[1] == 1 else mom[:, 0:3]
        v["src"] = src
        return v

    def _build_coefficients(self, app, tdev):
        """Persistent coefficient buffers (the struct fields and cell arrays the graphs read)."""
        self._coef_t = {k: t.contiguous().clone() for k, t in self._coefficient_values(app).items()}
        c = self._coef_t
        g = self.geom
        g.nu, g.alpha, g.ghb = wp.from_torch(c["nu"]), wp.from_torch(c["alpha"]), wp.from_torch(c["ghb"])
        g.gpc, g.cc, g.ccont = wp.from_torch(c["gpc"]), wp.from_torch(c["cc"]), wp.from_torch(c["ccont"])
        self.c_visc, self.c_pcoef, self.c_ghb = (wp.from_torch(c["visc"]), wp.from_torch(c["pcoef"]),
                                                 wp.from_torch(c["ghbc"]))
        self._src_t = c["src"]
        self.c_src = wp.from_torch(c["src"])
        self._coef_keys = tuple(self._coef_names())
        self._sh.coef_stamps = [_stamp(getattr(app, k)) for k in self._coef_keys]
        self._sh.coef_refreshes = 0

    def sync_coefficients(self) -> bool:
        """Re-read the coefficient tensors whose identity or version changed since they were loaded, into the same
        buffers (the captured graphs stay valid).  A few identity checks while nothing changed."""
        app, sh = self.app, self._sh
        for k, (ref, ver) in zip(self._coef_keys, sh.coef_stamps):
            t = getattr(app, k)
            if t is not ref or t._version != ver:
                break
        else:
            return False
        for k, t in self._coefficient_values(app).items():
            self._coef_t[k].copy_(t)
        sh.coef_stamps = [_stamp(getattr(app, k)) for k in self._coef_keys]
        sh.coef_refreshes += 1
        return True

    def _build_time_buffers(self, tdev):
        """The per-step inputs as persistent buffers (the graphs hold their pointers)."""
        sh = self._sh
        z = lambda *shape: torch.zeros(*shape, dtype=torch.float64, device=tdev)   # noqa: E731
        sh.tcoef_t = z(self.n)                   # unsteady_coeff V / dt_cell
        sh.qold_t = z(self.n, self.nf)           # the old level of the time term, interior rows
        sh.fadv_t = z(self.n_face)               # F* of the linearised convection
        sh.fadv_cap_t = z(self.n_face)           # the stored flux an explicit_convection capture is carried by
        sh.fap_t = z(self.n_face)                # F* of the oseen a_P
        sh.xsrc_t = z(self.n, self.nf)           # the explicit source
        sh.fvol_t = z(self.n_face, 1)            # face_volumetric_flux (what the preconditioner reads)
        sh.tcoef, sh.qold = wp.from_torch(sh.tcoef_t), wp.from_torch(sh.qold_t)
        sh.fadv, sh.fadv_cap, sh.fap = (wp.from_torch(sh.fadv_t), wp.from_torch(sh.fadv_cap_t),
                                        wp.from_torch(sh.fap_t))
        sh.xsrc = wp.from_torch(sh.xsrc_t)
        sh.fvol = wp.from_torch(sh.fvol_t.reshape(-1))
        sh.dummy1 = wp.from_torch(z(1))
        sh.dummy2 = wp.from_torch(z(1, 4))
        # W1 names, now views of the shared buffers
        self.qold = sh.qold_t
        self.c_tcoef = sh.tcoef
        self.face_volumetric_flux = sh.fvol_t

    # ------------------------------------------------------------------ per-step inputs
    def snapshot(self, old_state, bc_level=_UNSET):
        """The time-integrator inputs of the discretisation now: the tensors and settings ``load`` copies."""
        app = self.app
        self._refuse_dynamic(app)
        cm = CONV_MODES[getattr(app, "_conv_mode", "implicit")]
        am = AP_MODES[getattr(app, "_ap_mode", "default")]
        ut = 1 if (am == 1 or getattr(app, "_ap_unsteady", False)) else 0
        xs = getattr(app, "_explicit_source", None)
        snap = {"mode": (cm, am, ut, 0 if xs is None else 1),
                "uc": _stamp(app.unsteady_coeff), "dt": _stamp(app.dt_cell), "old": _stamp(old_state),
                "adv": _stamp(getattr(app, "_conv_advecting_flux", None) if cm == 2 else None),
                "apf": _stamp(getattr(app, "_ap_flux", None) if am == 2 else None), "xs": _stamp(xs)}
        if self.time_aware:
            armed = getattr(app, "_bc_old_state", None)
            level = armed if armed is not None else (old_state if bc_level is _UNSET else bc_level)
            snap["ghost"] = (_stamp(level), _stamp(getattr(app, "_bc_dt_cell", None)), snap["dt"])
        return snap

    def load(self, snap):
        """Copy the inputs of ``snap`` into the persistent buffers where they differ from what is loaded; returns
        the treatment flags (the graph key)."""
        sh = self._sh
        st = sh.stamps
        self.sync_coefficients()
        if not (_same(snap["uc"], st.get("uc")) and _same(snap["dt"], st.get("dt"))):
            uc, dt = snap["uc"][0], snap["dt"][0]
            sh.tcoef_t.copy_(uc.reshape(-1).to(torch.float64) * self._vol_t / dt.reshape(-1).to(torch.float64))
            st["uc"], st["dt"] = snap["uc"], snap["dt"]
        if not _same(snap["old"], st.get("old")):
            old = snap["old"][0]
            if old is None:
                sh.qold_t.zero_()
            else:
                sh.qold_t.copy_(old.detach().index_select(0, self.cpd_of))
            st["old"] = snap["old"]
        for key, buf in (("adv", sh.fadv_t), ("apf", sh.fap_t), ("xs", sh.xsrc_t)):
            t = snap[key][0]
            if t is not None and not _same(snap[key], st.get(key)):
                buf.copy_(t.reshape(buf.shape))
                st[key] = snap[key]
        if self.time_aware:
            self._sync_ghosts(snap["ghost"])
        sh.last_mode = snap["mode"]
        return snap["mode"]

    def sync(self, old_state, bc_level=_UNSET):
        return self.load(self.snapshot(old_state, bc_level))

    def _sync_ghosts(self, key):
        sh = self._sh
        cur = sh.stamps.get("ghost")
        if cur is not None and all(_same(a, b) for a, b in zip(key, cur)):
            return
        level = key[0][0]
        if self.adv_n:                     # device path: two device copies, no torch boundary pass, no host sync
            a = self._adv_t
            if level is None:
                a["old"].zero_()
            else:
                a["old"].copy_(level.detach()[:, 0:3])
            dt = key[1][0] if key[1][0] is not None else key[2][0]
            a["dt"].copy_(dt.reshape(-1))
            sh.stamps["ghost"] = key
            return
        old_uvw = None
        if level is not None:
            old_uvw = level.detach()[:, 0:3].to(torch.float64).clone()
            if self.dim == 2:
                old_uvw[:, 2] = 0.0
        A, b = self._probe_map(self.app, old_uvw)
        self._gA_t.copy_(A)
        self._gb_t.copy_(b)
        if self.n_ghost2:
            self._gA2_t.copy_(self._probed_A2)
        sh.stamps["ghost"] = key
        sh.ghost_probes += 1
        self._ghost_old_uvw = old_uvw

    # ------------------------------------------------------------------ buffers and streams
    def _buffers(self, k, tag="main"):
        key = (tag, k)
        b = self._sh.bufs.get(key)
        if b is not None:
            return b
        D = dual_type(k)
        dev = self.cs.device
        b = {}
        for name, shape in (("q", (self.n_cpd, self.nf)), ("grad", (self.n_cpd, self.nf, 3)),
                            ("uf", (self.n_face, 3)), ("f0", (self.n_face,)), ("d", (self.n_cpd,)),
                            ("fmass", (self.n_face,)), ("fmom", (self.n_face, 3)), ("fen", (self.n_face,))):
            t = torch.zeros(*shape, k + 1, dtype=torch.float64, device=dev)
            b[name + "_t"] = t
            b[name] = wp.from_torch(t, dtype=D)
        self._sh.bufs[key] = b
        return b

    def release(self, k):
        """Drop every buffer set, io set and captured graph of tangent count ``k``
        (all tags; the frozen view shares them); the next launch with ``k`` allocates and captures them again."""
        bufs = self._sh.bufs
        for key in [key for key in bufs if (len(key) == 2 and key[1] == k) or (len(key) == 3 and key[0] == "io"
                                                                           and key[2] == k)]:
            del bufs[key]

    def _io(self, k, tag="main"):
        """Persistent input / output buffers per tangent count: a captured graph holds raw pointers."""
        key = ("io", tag, k)
        io = self._sh.bufs.get(key)
        if io is None:
            N, dev = self.n * self.nf, self.cs.device
            io = {"x": torch.zeros(N, dtype=torch.float64, device=dev),
                  "dirs": torch.zeros(max(k, 1), N, dtype=torch.float64, device=dev),
                  "res": torch.zeros(N, dtype=torch.float64, device=dev),
                  "tan": torch.zeros(max(k, 1), N, dtype=torch.float64, device=dev), "graphs": {}, "pt": None}
            for name in ("x", "dirs", "res", "tan"):
                io[name + "w"] = wp.from_torch(io[name])
            self._sh.bufs[key] = io
        return io

    def _streams(self):
        """A dedicated (non-default) stream for graph capture, as a Warp stream and as a torch ExternalStream:
        torch's default stream is the legacy NULL stream, on which no CUDA graph can be captured."""
        sh = self._sh
        if getattr(sh, "wst", None) is None:
            sh.wst = wp.Stream(self.dev)
            sh.tst = torch.cuda.ExternalStream(sh.wst.cuda_stream, device=self.cs.device)
        return sh.wst, sh.tst

    def _caller_stream(self):
        """The caller's current torch stream as a Warp stream (the legacy stream included: replay and eager launches
        need no capture)."""
        h = torch.cuda.current_stream(self.cs.device).cuda_stream
        s = self._sh.wrappers.get(h)
        if s is None:
            s = self._sh.wrappers[h] = wp.Stream(self.dev, cuda_stream=h)
        return s

    # ------------------------------------------------------------------ evaluation
    def _launch(self, k, mode, io, b, stream, capture=None, fadv=None, profile=None, block_dim=None):
        """The launch sequence for ``k`` tangents and the treatment flags ``mode = (conv, a_P, a_P time term,
        explicit source)``.  ``capture``: (fcons, fconv, fvol) arrays of a level-quantity capture.  ``profile`` (a
        dict; eager launches on the caller's stream only): CUDA-event pairs per kernel, for the per-kernel GPU time;
        ``block_dim``: the threads per block of every launch (Warp's default 256 otherwise)."""
        cm, am, ut, hx = mode
        sh = self._sh
        n = self.n
        nf = self.nf
        cpd = self._w_cpd
        g = self.geom
        kw = {"device": self.dev, "stream": stream}
        if block_dim is not None:
            kw["block_dim"] = int(block_dim)

        def L(kernel, dim, inputs, outputs):
            if profile is None:
                wp.launch(kernel, dim=dim, inputs=inputs, outputs=outputs, **kw)
                return
            e0, e1 = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            e0.record()
            wp.launch(kernel, dim=dim, inputs=inputs, outputs=outputs, **kw)
            e1.record()
            profile.setdefault(kernel.key, []).append((e0, e1))

        q, grad = b["q"], b["grad"]
        wt = _wt()
        if self.adv_n:
            L(wt.advective_kernel, self.adv_n, [self.adv_old, self.adv_rows, self.adv_ghost, self.adv_owner,
                                                self.adv_nhat, self.adv_delta, self.adv_dt,
                                                wp.float64(self.adv_floor), self.wzero], [self.ghost_b])
        L(load_kernel, (n, nf), [io["xw"], io["dirsw"], cpd, n, k, self.wzero], [q])
        L(ghost_kernel, self.n_ghost, [self.ghost_ids, self.ghost_src, self.ghost_A, self.ghost_b, nf], [q])
        if self.n_ghost2:    # conjugate interfaces: the second source of the two-source ghosts (CHT-0b)
            L(_wcht().ghost2_kernel, self.n_ghost2, [self.ghost2_ids, self.ghost2_src, self.ghost2_A, nf], [q])
        L(wlsq_kernel, (n, nf), [q, cpd, self.w_off, self.w_other, self.w_w], [grad])
        L(ghost_grad_kernel, (self.n_ghost, nf), [self.ghost_ids, self.ghost_partner], [grad])
        L(face_pre_kernel, self.n_face, [q, grad, g.fo, g.fn, g.gC, g.gF, g.rcorr, g.nrm], [b["uf"], b["f0"]])
        if am == 0:          # the W1 kernel; --ap-unsteady hands it the time coefficient as its a_P time term
            L(ap_kernel, n, [b["f0"], cpd, self.c_off, self.c_face, self.c_sarea, self.c_visc, self.c_vol,
                             sh.tcoef if ut else self.c_aptime, self.frozen], [b["d"]])
        else:
            L(wt.ap_fixed_kernel, n, [cpd, self.c_off, self.c_face, self.c_sarea, self.c_visc, self.c_vol, sh.tcoef,
                                   sh.fap, 1 if am == 2 else 0, ut], [b["d"]])
        L(dghost_kernel, self.n_ghost, [self.ghost_ids, self.ghost_partner], [b["d"]])
        # the W1 face and cell kernels, bit for bit, on the implicit-convection path; their W3 variants when the
        # step's treatment, an explicit source, the dim = 2 closure or a capture asks for more
        if cm == 0 and capture is None:
            L(face_main_kernel, self.n_face, [q, grad, b["uf"], b["f0"], b["d"], g, self.energy, self.upwind],
              [b["fmass"], b["fmom"], b["fen"], sh.fvol])
        else:
            if capture is None:
                fcons, fconv, fvol, cap = sh.dummy1, sh.dummy2, sh.fvol, 0
            else:
                (fcons, fconv, fvol), cap = capture, 1
            L(wt.face_main_t_kernel, self.n_face,
              [q, grad, b["uf"], b["f0"], b["d"], g, self.energy, self.upwind, cm, sh.fadv if fadv is None else fadv,
               cap],
              [b["fmass"], b["fmom"], b["fen"], fvol, fcons, fconv])
        cell_in = [q, grad, sh.qold, b["fmass"], b["fmom"], b["fen"], cpd, self.c_off, self.c_face, self.c_sarea,
                   self.c_vol, sh.tcoef, self.c_ghb, self.c_pcoef, self.c_src, n, k, self.energy]
        if hx == 0 and self.closew == 0:
            L(cell_kernel, n, cell_in, [io["resw"], io["tanw"]])
        else:
            L(wt.cell_t_kernel, n, cell_in + [sh.xsrc, hx, self.closew, io["xw"], io["dirsw"]],
              [io["resw"], io["tanw"]])

    def _exec(self, k, mode):
        """Run the launch sequence of ``k`` tangents and treatment ``mode`` on the inputs already in ``_io(k)``:
        captured once per (k, frozen, mode) on the capture stream, then replayed on the caller's stream."""
        sh = self._sh
        io = self._io(k)
        b = self._buffers(k)
        # eager when asked (the test's switch, LPCNS_WARP_GRAPH=0 read on every call: the W2 device loop sets it while
        # it records the operator into its own graph) or whenever the caller's stream is being captured
        if (not sh.graphs_on or os.environ.get("LPCNS_WARP_GRAPH", "1") == "0"
                or torch.cuda.is_current_stream_capturing()):
            self._eager(lambda stream: self._launch(k, mode, io, b, stream))
            return io
        gkey = (self.frozen,) + tuple(mode)
        graph = io["graphs"].get(gkey)
        if graph is None or sh.replay_side:
            ts = torch.cuda.current_stream(self.cs.device)
            ws, wts = self._streams()
            wts.wait_stream(ts)                   # the inputs the caller produced (and copied) on its stream
            with wp.ScopedStream(ws, sync_enter=False):
                if graph is None:
                    self._launch(k, mode, io, b, ws)          # loads the module; these are this call's outputs
                    with wp.ScopedCapture(stream=ws) as cap:
                        self._launch(k, mode, io, b, ws)
                    io["graphs"][gkey] = cap.graph
                else:
                    wp.capture_launch(graph, stream=ws)
                    sh.replay_path = "side"
            ts.wait_stream(wts)                   # the caller reads the outputs on its stream
            return io
        try:
            wp.capture_launch(graph, stream=self._caller_stream())
            sh.replay_path = "caller"
        except Exception:                         # a driver that refuses the legacy stream: the W1d path from now on
            sh.replay_side = True
            return self._exec(k, mode)
        return io

    def _eager(self, launches):
        """``launches(stream)`` eagerly on the caller's stream, or (if Warp cannot wrap it) on the capture stream
        ordered against the caller's both ways."""
        if not self._sh.replay_side:
            try:
                return launches(self._caller_stream())
            except Exception:
                self._sh.replay_side = True
        ts = torch.cuda.current_stream(self.cs.device)
        ws, wts = self._streams()
        wts.wait_stream(ts)
        with wp.ScopedStream(ws, sync_enter=False):
            out = launches(ws)
        ts.wait_stream(wts)
        return out

    def _run(self, x, dirs, k):
        """(W1 API) copy the inputs into the persistent buffers and run the sequence; returns the io buffers."""
        mode = self.sync(getattr(self.cs, "_old_state", None))
        io = self._io(k)
        io["x"].copy_(x.reshape(-1))
        io["pt"] = None
        if k:
            io["dirs"][:k].copy_(dirs.reshape(k, -1))
        return self._exec(k, mode)

    def residual(self, x: torch.Tensor, old_state=_UNSET) -> torch.Tensor:
        old = getattr(self.cs, "_old_state", None) if old_state is _UNSET else old_state
        mode = self.sync(old)
        io = self._io(0)
        io["x"].copy_(x.detach().reshape(-1))
        self._exec(0, mode)
        return io["res"].clone()

    def jvp(self, x: torch.Tensor, directions: torch.Tensor, old_state=_UNSET):
        """``(F(x), J d_k for each row d_k of directions [K, N])`` in one pass."""
        old = getattr(self.cs, "_old_state", None) if old_state is _UNSET else old_state
        mode = self.sync(old)
        x = x.detach().reshape(-1)
        dirs = directions.detach().reshape(-1, x.numel())
        k = int(dirs.shape[0])
        io = self._io(k)
        io["x"].copy_(x)
        io["pt"] = None
        io["dirs"][:k].copy_(dirs)
        self._exec(k, mode)
        return io["res"].clone(), io["tan"][:k].clone()

    def warm(self, ks=(0, 1, 16)):
        """Load (compile once, then from the kernel cache) every tangent count the solver uses, at attach time."""
        x = torch.zeros(self.n * self.nf, dtype=torch.float64, device=self.cs.device)
        for k in ks:
            self.jvp(x, torch.zeros(max(k, 1), x.numel(), dtype=torch.float64, device=x.device)[:k]) if k \
                else self.residual(x)

    # ------------------------------------------------------------------ the time march's readouts
    def compound_state(self) -> torch.Tensor:
        """``[N_cpd, F]`` of the LAST residual evaluation (``casestate.state_with_boundaries``' layout): the interior
        and the boundary-filled ghosts, as ``timestepping.compound_state`` reads them off the torch forward."""
        return self._buffers(0)["q_t"][..., 0].clone()

    def capture(self, state: torch.Tensor, advecting_flux=None):
        """``(F, N)`` of a compound ``state`` (``BaseSolver._capture_convection``): its conservative face flux
        ``[N_face, 1]`` and its convection rows ``[N_cell, F]``, carried by its own flux or by ``advecting_flux``.  The
        a_P mode, ``dt_cell`` and an armed boundary-condition old level stay as the step set them; unarmed, the
        time-aware conditions read ``state`` itself, as the torch capture does.  Leaves the residual's buffers (and
        so the compound state and ``face_volumetric_flux``) untouched."""
        sh = self._sh
        snap = self.snapshot(getattr(self.cs, "_old_state", None), bc_level=state)
        mode = self.load(snap)
        cm = 0
        if advecting_flux is not None:
            sh.fadv_cap_t.copy_(advecting_flux.detach().reshape(-1))
            cm = 2
        io = self._io(0, "cap")
        b = self._buffers(0, "cap")
        if "fcons_t" not in b:
            dev = self.cs.device
            b["fcons_t"] = torch.zeros(self.n_face, dtype=torch.float64, device=dev)
            b["fconv_t"] = torch.zeros(self.n_face, 4, dtype=torch.float64, device=dev)
            b["fvol_t"] = torch.zeros(self.n_face, dtype=torch.float64, device=dev)
            b["conv_t"] = torch.zeros(self.n, self.nf, dtype=torch.float64, device=dev)
            for name in ("fcons", "fconv", "fvol", "conv"):
                b[name] = wp.from_torch(b[name + "_t"])
        io["x"].copy_(state.detach().index_select(0, self.cpd_of).transpose(0, 1).reshape(-1))

        def launches(stream):
            self._launch(0, (cm, mode[1], mode[2], 0), io, b, stream, capture=(b["fcons"], b["fconv"], b["fvol"]),
                         fadv=sh.fadv_cap if cm == 2 else None)
            wp.launch(_wt().conv_rows_kernel, dim=self.n, inputs=[b["fconv"], self.c_off, self.c_face, self.c_sarea,
                                                            self.energy], outputs=[b["conv"]], device=self.dev,
                      stream=stream)

        self._eager(launches)
        return b["fcons_t"].reshape(-1, 1).clone(), b["conv_t"].clone()


class WarpBackend:
    """What ``CaseState.residual`` and ``jacobian.linearise`` call when a case runs on the Warp backend.

    ``rows(x, old_state)`` is the residual before the operator correction, the pin, the PTC shift and the FAS
    right-hand side (``casestate.residual`` adds those); it also refreshes the advecting face flux the
    preconditioner reads (``solver.face_volumetric_flux``), which the torch forward leaves behind.
    ``product(cs, x, coefficient)`` is ``linearise``'s ``d -> J d`` with ``.batched`` for the colouring probes,
    including the pinned identity rows and the PTC diagonal, the derivative of exactly what ``residual`` returns; like
    the torch tape it is the operator of the step armed when it was built.  For the time march (W3):
    ``compound_state()`` (the boundary-filled state of the last residual), ``mwi_face_flux(state)`` and
    ``explicit_convection(state)`` (the level quantities of the linear schemes).
    """

    def __init__(self, cs):
        self.cs = cs
        self.implicit = BuoyantWarpResidual(cs, frozen_df=False)
        self.frozen = BuoyantWarpResidual(cs, frozen_df=True, share=self.implicit)
        self.interior = cs.interior.long()
        self.block = tangent_block(int(self.implicit.n))       # W12: tangents per probe launch
        self.implicit.warm((0, 1, self.block))
        self.frozen.warm((1, self.block))
        if self.block != 16:
            print(f"warp backend: {int(self.implicit.n)} cells, {self.block} tangents per probe launch "
                  "(W12; LPCNS_WARP_TANGENT_BLOCK)", flush=True)
        # the W3 kernels' module too (built once, then from the kernel cache): no load inside the first time step
        wp.load_module(_wt(), device=self.implicit.dev)

    def is_large(self) -> bool:
        """Above TANGENT_BLOCK_CELLS cells (152^3): the memory policies of W12 / W12c / W12g apply."""
        return int(self.implicit.n) > TANGENT_BLOCK_CELLS

    def release_probe_buffers(self):
        """Above TANGENT_BLOCK_CELLS cells, free the probe count's buffers (~1.3 GB per tangent
        at 152^3) before a preconditioner build -- the colouring probes need them only during the assembly, and the
        hierarchy build is the run's memory peak; the next assembly allocates and captures them again.  Below: kept
        (nothing changes for the meshes measured before)."""
        if self.is_large() and self.block not in (0, 1):
            self.implicit.release(self.block)

    def _set_old(self, W, old_state):
        W.sync(old_state)

    def _refuse(self, cs):
        if getattr(cs, "_operator_correction", None) is not None:
            raise NotImplementedError("warp backend: the learned operator correction reads the torch forward's state")

    def rows(self, x, old_state=None):
        cs = self.cs
        self._refuse(cs)
        W = self.implicit
        mode = W.sync(old_state)
        io = W._io(0)
        io["x"].copy_(x.detach().reshape(-1))
        W._exec(0, mode)
        cs.solver.face_volumetric_flux = W.face_volumetric_flux
        return io["res"].clone()

    def compound_state(self):
        return self.implicit.compound_state()

    def mwi_face_flux(self, state):
        return self.implicit.capture(state)[0]

    def explicit_convection(self, state, advecting_flux=None):
        return self.implicit.capture(state, advecting_flux)[1]

    def product(self, cs, x, coefficient=None, differentiable=False):
        if differentiable:
            raise NotImplementedError("warp backend: differentiable linearisations (training) stay on torch")
        self._refuse(cs)
        if coefficient is None:
            coefficient = "frozen" if cs.solver._mwi_frozen_coefficient else "implicit"
        W = self.frozen if coefficient == "frozen" else self.implicit
        snap = W.snapshot(getattr(cs, "_old_state", None))   # the step this operator belongs to (the tape's state)
        W.load(snap)
        point = x.detach().reshape(-1).to(torch.float64).contiguous()
        N = point.numel()
        pin = getattr(cs, "_pin", None)
        shift = cs._ptc_shift
        token = object()                                    # this linearisation's point, resident in the io buffers
        blk = self.block

        def finish(Jd, d, fresh):
            if pin is not None:
                Jd, fresh = torch.where(pin[0], d, Jd), True
            if shift is not None:
                Jd, fresh = Jd + shift[0] * d, True
            return Jd if fresh else Jd.clone()

        def prepare(k):
            mode = W.load(snap)
            io = W._io(k)
            if io["pt"] is not token:
                io["x"].copy_(point)
                io["pt"] = token
            return mode, io

        def product(direction):
            d = direction.detach().reshape(-1)
            mode, io = prepare(1)
            io["dirs"][0].copy_(d)
            W._exec(1, mode)
            return finish(io["tan"][0], d, False)

        def batched(directions):
            d = directions.detach().reshape(-1, N)
            P_ = int(d.shape[0])
            out = torch.empty(P_, N, dtype=torch.float64, device=point.device)
            mode, io = prepare(blk)
            for s in range(0, P_, blk):                 # blk tangents per launch, the last block zero-padded
                m = min(blk, P_ - s)
                io["dirs"][:m].copy_(d[s:s + m])
                if m < blk:
                    io["dirs"][m:].zero_()
                W._exec(blk, mode)
                out[s:s + m].copy_(io["tan"][:m])
            return finish(out, d, True)

        product.batched = batched
        return product


# Tangents per probe launch.  Every tangent count keeps one dual-number buffer set
# (q, grad, face fluxes: ~(21 cells + 9 faces) doubles per tangent, held by its captured graph); at 152^3 sixteen
# tangents hold ~23 GB, which puts the solve past the 95 GiB of the device where torch needs 72.  Above
# TANGENT_BLOCK_CELLS cells the colouring probes go four at a time (the count's overload is added by Warp, so the
# module of every smaller mesh is the one it always was); LPCNS_WARP_TANGENT_BLOCK=<k> sets it for any mesh.
TANGENT_BLOCK_CELLS = 2_500_000


def tangent_block(n_cells: int) -> int:
    forced = os.environ.get("LPCNS_WARP_TANGENT_BLOCK")
    if forced:
        return max(1, int(forced))
    return 16 if n_cells <= TANGENT_BLOCK_CELLS else 4


def attach_warp(cs):
    """Put ``cs`` (a CollocatedSolver over a buoyantBoussinesqpicoext case) on the Warp backend; returns it."""
    cs._warp = WarpBackend(cs)
    if int(getattr(cs, "assembly_batch", 1) or 1) <= 1:
        cs.assembly_batch = cs._warp.block   # the colouring probes go through the kernels a block at a time
    return cs._warp
