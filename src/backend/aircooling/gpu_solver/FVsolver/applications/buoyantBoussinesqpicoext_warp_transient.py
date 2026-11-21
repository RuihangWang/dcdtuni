"""The transient kernels of the Warp residual (docs/research/2026-10-14-warp-backend.md): what the
time integrators and the time march need beyond the W1 kernels of ``buoyantBoussinesqpicoext_warp`` -- the
state-independent a_P modes (Oseen, SBDF), the face and cell kernels with the step's convection treatment, explicit
source and dim-2 closure, the level-quantity capture (conservative flux, convection rows) and the device update of
advectiveOutlet ghosts.  A separate Warp module on purpose: the W1 module then compiles exactly as the W1d one did,
so the steady path stays bit for bit (``buoyantBoussinesqpicoext_warp._declare_overloads``).  Launched by
``BuoyantWarpResidual._launch`` / ``capture`` / ``_build_advective``; tested by ``tests/test_transient_warp.py``.
"""
from __future__ import annotations

from typing import Any

import warp as wp

from FVsolver.applications.buoyantBoussinesqpicoext_warp import (P, T, FaceGeom, grad_dot, lin_corrected,
                                                                 overrelaxed_flux, sn_grad)
from FVsolver.warp_dual import dconst, dmul, dual_type, dzero

wp.config.quiet = True
wp.set_module_options({"enable_backward": False})   # as the W1 module: tangents ride in the dual vectors


@wp.kernel
def ap_fixed_kernel(cpd_of: wp.array(dtype=wp.int32), off: wp.array(dtype=wp.int32), face: wp.array(dtype=wp.int32),
                    sarea: wp.array(dtype=wp.float64), visc: wp.array(dtype=wp.float64),
                    vol: wp.array(dtype=wp.float64), ap_time: wp.array(dtype=wp.float64),
                    fap: wp.array(dtype=wp.float64), oseen: int, use_time: int, dcpd: wp.array(dtype=Any)):
    """The state-independent a_P modes of ``BaseSolver.momentum_diagonal``: "oseen" sum_f (max(F*_f s_f, 0) + nu |s_f|
    delta_f) (+ the time term under --ap-unsteady), "sbdf" sum_f nu |s_f| delta_f + unsteady_coeff V / dt_cell.  d_P =
    V_P / a_P carries no tangent: it does not depend on the unknowns."""
    i = wp.tid()
    a = wp.float64(0.0)
    for k in range(off[i], off[i + 1]):
        v = visc[k]
        if oseen == 1:
            v = wp.max(fap[face[k]] * sarea[k], wp.float64(0.0)) + v
        a = a + v
    if use_time == 1:
        a = a + ap_time[i]
    ci = cpd_of[i]
    dcpd[ci] = dconst(dcpd[ci], vol[i] / a)


@wp.kernel
def face_main_t_kernel(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), uf: wp.array2d(dtype=Any),
                     f0: wp.array(dtype=Any), dcpd: wp.array(dtype=Any), g: FaceGeom, energy: int, upwind: int,
                     conv_mode: int, fadv: wp.array(dtype=wp.float64), capture: int,
                     fmass: wp.array(dtype=Any), fmom: wp.array2d(dtype=Any), fen: wp.array(dtype=Any),
                     fvol: wp.array(dtype=wp.float64), fcons: wp.array(dtype=wp.float64),
                     fconv: wp.array2d(dtype=wp.float64)):
    """face_main_kernel with the time integrator's convection treatment (conv_mode 0 implicit, 1 explicit, 2
    linearised about fadv) and the level-quantity capture outputs (capture = 1: fcons, fconv); W3."""
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
    if capture == 1:
        fcons[f] = flux[0]             # the conservative, BC-enforced flux (BaseSolver.mwi_face_flux)
    # ---- convection, by the step's treatment: the state's own flux (implicit), a fixed F* (linearised: Oseen),
    # none (explicit: SBDF, the step adds N* through the explicit source).  "Linear corrected" face values, or
    # LinearUpwind for U (upwind cell value + its gradient times the offset to the face; strictly positive advecting
    # flux picks the owner; the ghost value on physical boundaries)
    cf = g.cc[f] * flux
    sg = flux[0]
    if conv_mode == 2:
        cf = dconst(flux, g.cc[f] * fadv[f])
        sg = fadv[f]
    elif conv_mode == 1:
        cf = dzero(flux)
    # ---- diffusion: compact snGrad on the physical boundary faces, overRelaxed face gradient elsewhere
    bcd = g.bcdiff[f]
    for c in range(3):
        uc = uf[f, c]
        if upwind == 1 or upwind == 3:
            if g.bnc[f] == 1:
                uc = q[n, c]
            elif sg > wp.float64(0.0):
                uc = q[o, c] + grad_dot(grad, o, o, c, wp.float64(1.0), wp.float64(0.0), g.rC[f])
            else:
                uc = q[n, c] + grad_dot(grad, n, n, c, wp.float64(1.0), wp.float64(0.0), g.rF[f])
        conv = dmul(cf, uc)
        if capture == 1:
            fconv[f, c] = conv[0]
        gfl = overrelaxed_flux(q, grad, o, n, c, gc, gf, g.eCF[f], g.inv_dCF[f], g.overrelax[f], nv)
        if bcd == 1:
            gfl = sn_grad(q, grad, o, n, c, g.nonorth[f], g.ow_u[f], g.nw_u[f], g.cv_u[f])
        fmom[f, c] = conv - g.nu[f] * gfl
    if energy == 1:
        tf = lin_corrected(q, grad, o, n, T, gc, gf, g.rcorr[f])
        if upwind >= 2:                # LinearUpwind for T: as U above, the same advecting sign
            if g.bnc[f] == 1:
                tf = q[n, T]
            elif sg > wp.float64(0.0):
                tf = q[o, T] + grad_dot(grad, o, o, T, wp.float64(1.0), wp.float64(0.0), g.rC[f])
            else:
                tf = q[n, T] + grad_dot(grad, n, n, T, wp.float64(1.0), wp.float64(0.0), g.rF[f])
        gft = overrelaxed_flux(q, grad, o, n, T, gc, gf, g.eCF[f], g.inv_dCF[f], g.overrelax[f], nv)
        if bcd == 1:
            gft = sn_grad(q, grad, o, n, T, g.nonorth[f], g.ow_t[f], g.nw_t[f], g.cv_t[f])
        convt = dmul(cf, tf)
        if capture == 1:
            fconv[f, 3] = convt[0]
        fen[f] = convt - g.alpha[f] * gft
    fmass[f] = g.ccont[f] * flux


@wp.kernel
def cell_t_kernel(q: wp.array2d(dtype=Any), grad: wp.array3d(dtype=Any), qold: wp.array2d(dtype=wp.float64),
                fmass: wp.array(dtype=Any), fmom: wp.array2d(dtype=Any), fen: wp.array(dtype=Any),
                cpd_of: wp.array(dtype=wp.int32), off: wp.array(dtype=wp.int32), face: wp.array(dtype=wp.int32),
                sarea: wp.array(dtype=wp.float64), vol: wp.array(dtype=wp.float64),
                tcoef: wp.array(dtype=wp.float64), ghb_cell: wp.array(dtype=wp.float64),
                pcoef: wp.array(dtype=wp.float64), src: wp.array2d(dtype=wp.float64), n: int, kdir: int,
                energy: int, xsrc: wp.array2d(dtype=wp.float64), has_xsrc: int, closew: int,
                x: wp.array(dtype=wp.float64), dirs: wp.array2d(dtype=wp.float64),
                res: wp.array(dtype=wp.float64), tan: wp.array2d(dtype=wp.float64)):
    """cell_kernel with the step's explicit source (has_xsrc) and the dim = 2 closure of the w rows (closew); W3."""
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
    r0 = r0 + tc * q[ci, 0] - dconst(r0, tc * qold[i, 0] + src[i, 0])
    r1 = r1 + tc * q[ci, 1] - dconst(r1, tc * qold[i, 1] + src[i, 1])
    r2 = r2 + tc * q[ci, 2] - dconst(r2, tc * qold[i, 2] + src[i, 2])
    if has_xsrc == 1:                   # the step's explicit source (BaseSolver.set_explicit_source: SBDF's N*)
        r0 = r0 + dconst(r0, xsrc[i, 0])
        r1 = r1 + dconst(r1, xsrc[i, 1])
        r2 = r2 + dconst(r2, xsrc[i, 2])
        rc = rc + dconst(rc, xsrc[i, 3])
    r0 = r0 + pc * grad[ci, P, 0]
    r1 = r1 + pc * grad[ci, P, 1]
    r2 = r2 + pc * grad[ci, P, 2]
    if energy == 1:
        r0 = r0 - gb * grad[ci, T, 0]
        r1 = r1 - gb * grad[ci, T, 1]
        r2 = r2 - gb * grad[ci, T, 2]
        re = re + tc * q[ci, T] - dconst(re, tc * qold[i, 4] + src[i, 3])
        if has_xsrc == 1:
            re = re + dconst(re, xsrc[i, 4])
    res[0 * n + i] = r0[0]
    res[1 * n + i] = r1[0]
    if closew == 1:                     # dim = 2: the z-momentum row is w itself (close_the_out_of_plane_rows)
        res[2 * n + i] = x[2 * n + i]
    else:
        res[2 * n + i] = r2[0]
    res[3 * n + i] = rc[0]
    if energy == 1:
        res[4 * n + i] = re[0]
    for k in range(kdir):
        tan[k, 0 * n + i] = r0[k + 1]
        tan[k, 1 * n + i] = r1[k + 1]
        if closew == 1:
            tan[k, 2 * n + i] = dirs[k, 2 * n + i]
        else:
            tan[k, 2 * n + i] = r2[k + 1]
        tan[k, 3 * n + i] = rc[k + 1]
        if energy == 1:
            tan[k, 4 * n + i] = re[k + 1]


@wp.kernel
def conv_rows_kernel(fconv: wp.array2d(dtype=wp.float64), off: wp.array(dtype=wp.int32),
                     face: wp.array(dtype=wp.int32), sarea: wp.array(dtype=wp.float64), energy: int,
                     out: wp.array2d(dtype=wp.float64)):
    """N(u) per interior cell, sum_f conv_f s_f (``BaseSolver._convection_cell_rows``): momentum in columns 0:3, zero
    on continuity, the energy convection in column 4."""
    i = wp.tid()
    s0 = wp.float64(0.0)
    s1 = wp.float64(0.0)
    s2 = wp.float64(0.0)
    s3 = wp.float64(0.0)
    for k in range(off[i], off[i + 1]):
        a = sarea[k]
        f = face[k]
        s0 = s0 + fconv[f, 0] * a
        s1 = s1 + fconv[f, 1] * a
        s2 = s2 + fconv[f, 2] * a
        if energy == 1:
            s3 = s3 + fconv[f, 3] * a
    out[i, 0] = s0
    out[i, 1] = s1
    out[i, 2] = s2
    out[i, 3] = wp.float64(0.0)
    if energy == 1:
        out[i, 4] = s3


@wp.kernel
def advective_kernel(old: wp.array2d(dtype=wp.float64), rows: wp.array(dtype=wp.int32),
                     ghost: wp.array(dtype=wp.int32), owner: wp.array(dtype=wp.int32),
                     nhat: wp.array(dtype=wp.vec3d), delta: wp.array(dtype=wp.float64),
                     dt: wp.array(dtype=wp.float64), floor: wp.float64, wzero: int, b: wp.array2d(dtype=wp.float64)):
    """advectiveOutlet ghosts on the device (``setU.Advectiveoutlet``): u_g = u_g^o - alpha (u_g^o - u_P^o), alpha =
    clamp(max(u_P^o . n, 0) dt / delta, alpha_floor, 1), all from the OLD level the conditions read; the new state does
    not enter (A = 0 on these rows), so the update is the offset b of the ghost map."""
    j = wp.tid()
    o = owner[j]
    g = ghost[j]
    nv = nhat[j]
    ox = old[o, 0]
    oy = old[o, 1]
    oz = old[o, 2]
    gx = old[g, 0]
    gy = old[g, 1]
    gz = old[g, 2]
    if wzero == 1:
        oz = wp.float64(0.0)
        gz = wp.float64(0.0)
    uc = wp.max(ox * nv[0] + oy * nv[1] + oz * nv[2], wp.float64(0.0))
    a = wp.min(wp.max(uc * dt[o] / delta[j], floor), wp.float64(1.0))
    r = rows[j]
    b[r, 0] = gx - a * (gx - ox)
    b[r, 1] = gy - a * (gy - oy)
    b[r, 2] = gz - a * (gz - oz)


def _declare_overloads(ks=(0, 1, 16)):
    """The dual-number overloads launched by the solver, declared up front (one module build)."""
    for k in ks:
        D = dual_type(k)
        A1, A2, A3 = wp.array(dtype=D), wp.array2d(dtype=D), wp.array3d(dtype=D)
        wp.overload(ap_fixed_kernel, {"dcpd": A1})
        wp.overload(face_main_t_kernel, {"q": A2, "grad": A3, "uf": A2, "f0": A1, "dcpd": A1, "fmass": A1, "fmom": A2,
                                         "fen": A1})
        wp.overload(cell_t_kernel, {"q": A2, "grad": A3, "fmass": A1, "fmom": A2, "fen": A1})


_declare_overloads()
