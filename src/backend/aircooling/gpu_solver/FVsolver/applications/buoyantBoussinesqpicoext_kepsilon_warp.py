"""Fused Warp residual of ``buoyantBoussinesqpicoext`` with high-Re k-epsilon (``kEpsilon``, ``realizableKE``) on the
collocated Newton path: the data-centre RANS configuration (docs/research/2026-10-14-warp-backend.md, the W4 line).

What it computes.  The seven square Newton rows ``CaseState.residual`` reads -- ``[mom_x | mom_y | mom_z | cont | T |
k | epsilon]`` over ``(u, v, w, p_rgh, T, k, epsilon)`` -- of ``buoyantBoussinesqpicoext.Solver.forward`` /
``conserved_form`` / ``_assemble_transported_residuals`` with ``Turbulence.kepsilon.KEpsilon`` (or
``Turbulence.realizable_ke.RealizableKE``), for the configuration the data-centre cases run:

* WLSQ "1st" cell gradients of all seven fields (interior rows; cyclic ghosts take the partner's gradient);
* boundary ghosts: the affine map the torch boundary passes apply, probed field by field (fixedValue, zeroGradient,
  symmetry, noSlip, flowRateInletVelocity, rackInletVelocity / rackOutletVelocity, fixedFluxPressure, kqRWallFunction,
  cyclic), plus the conditions that are NOT a per-face affine map of their owner, evaluated exactly in their own
  kernels: ``epsilonWallFunction`` (``Cmu^0.75 k_f^1.5 / (kappa y)`` of the owner's floored k), ``rackOutletTemperature``
  (CBBM: area mean of the paired intake owners' T + dT, one reduction per rack; CBBM ``lagMean``: the condition's
  frozen mean + dT, re-read on every call and seeded as the torch eval seeds it; IBBM: the paired intake owner's T +
  dT) and ``porousBafflePressure`` (partner p -/+ J, ``J = -(D nu + 0.5 I |Un|) Un length``, Un from the mean of the
  two owner velocities);
* nu_t = Cmu k_f^2 / eps_f (realizableKE: rCmu k_f^2 / eps_f with the strain / rotation invariants of the cell
  gradient, the owner's on non-cyclic ghosts) at every compound cell, its linear face value with nutkWallFunction
  (stepwise) on the epsilonWallFunction faces; nu_eff = nu + nu_t in the momentum diffusion, in a_P (hence d_f and the
  MWI flux) and in the explicit transpose ``div(nu_eff dev2((grad U)^T))``; alpha_eff = alpha + nu_t / Prt (wall faces:
  alphatJayatillekeWallFunction when realizableKE's ``wallAlphat = "jayatilleke"``); k / eps diffusivity
  nu + nu_t / sigma;
* the momentum-weighted face flux with OpenFOAM's phig inside the bracket (implicit or frozen d_f), the optional
  conservative cyclic flux, Symmetry / Slip zero fluxes;
* convection per field: Upwind, LinearUpwind, Linear corrected (U, T), LimitedLinear k (T, k, eps), Linear (k, eps),
  each with the "bounded" prefix (``- div(phi) phi_P``);
* the compact snGrad on physical boundary faces and the "Linear overRelaxed" (or "Linear") face gradient elsewhere;
* the cell pressure gradient, the Boussinesq body force, the k source ``G - (eps_f / k_f) k + Gb`` with the wall-cell
  production override (corner mean), the eps source (``C1 Cmu k_f GbyNu - C2 (eps_f / k_f) eps``; realizableKE
  ``C1(eta) magS eps_f - C2 eps_f / (k_f + sqrt(nu eps_f)) eps``) and the epsilonWallFunction cell constraint
  (``eps_c - mean(wall ghost)`` or ``eps_c / mean - 1``);
* the backward-Euler time term of an unsteady step (implicit convection, default a_P only).

Everything else is refused by name (``_refuse_unsupported``), never run silently on torch.

How.  Every state quantity is a dual vector ``(value, t_1..t_C)`` (``FVsolver.warp_dual``); a launch carries the tangent
CHUNK as a second grid dimension (as ``picoext_komega_warp``), so K = 0 is the residual, K = 1 the exact JVP, K = 8..64
colouring probes per launch.  No atomics (cell sums are gathers over a CSR of the cell's faces), so results are bitwise
reproducible and the launch sequence is CUDA-graph captured once per (chunk width, chunk count, mode).

Compact Jacobian (``DCWarpResidual.compact_jacobian``): the diagonal 7 x 7 blocks and the face-neighbour blocks (the
1-hop pattern: interior faces, cyclic partners, IBBM rack pairs) of either

* ``mode = "frozen"`` -- the Jacobian with d_f frozen (the residual reads two faces deep): exact 1-hop blocks from
  forward-mode probes seeded on a distance-3 colouring (no two cells of a colour within three faces), or
* ``mode = "picard"`` -- the Jacobian with the cell gradients and d_f frozen (a 1-hop operator), exact from a
  distance-2 colouring;

the CBBM mean's dense rank-1 block is dropped from both (``drop_global``: a preconditioner; the matrix-free JVP keeps
it).  The probes are seeded inside the load kernel from the colour table and their tangents written straight into the
block values by the cell kernel, so no N x P direction or image array exists.  Returned as a
``Pipeline.CoupledNewton.block_amg.BlockCSR`` (what ``CoupledAMG`` takes) with ``field_csr()`` for the field-blocked
scalar CSR.

Exactness is tested against the torch application (``tests/test_kepsilon_warp.py``).
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np
import torch
import warp as wp

from FVsolver.warp_dual import dmax0, dmul, ddiv, dual_type, dvalue_only

wp.config.quiet = True
# fuse_fp off for the same reason as picoext_komega_warp: torch rounds every multiply and add separately.
wp.set_module_options({"enable_backward": False, "fuse_fp": False})

NF = wp.constant(7)       # u, v, w, p_rgh, T, k, epsilon
PC = wp.constant(3)
TC = wp.constant(4)
KC = wp.constant(5)
EC = wp.constant(6)
SMALL = 1e-10             # limited_linear.py's SMALL
SMALL64 = wp.constant(wp.float64(SMALL))
TWO_THIRDS = wp.constant(wp.float64(2.0 / 3.0))
# LPCNS_WARP_DC_FRO_BAND=d (default 0: OpenFOAM's switch): flowRateOutletVelocity blends its two corrections (rescale
# above Q/2, shift below) smoothly over estimate/Q in [0.5 - d, 0.5 + d]; the hard switch is a jump of the residual
# where the owner profile is not uniform (L1 server fronts, INROW_CAC 2026-11-13). Outside the band, and on a patch
# without flow (Q = 0), unchanged.
FRO_BAND = wp.constant(wp.float64(float(os.environ.get("LPCNS_WARP_DC_FRO_BAND", "0"))))
S_LINEAR = wp.constant(0)          # "Linear" (plain)
S_LINEAR_CORR = wp.constant(1)     # "Linear corrected"
S_UPWIND = wp.constant(2)
S_LUPWIND = wp.constant(3)         # "LinearUpwind"
S_LIMITED = wp.constant(4)         # "LimitedLinear k"
FIELDS = ("U", "p_rgh", "T", "k", "epsilon")
ROW_NAMES = ("mom_x", "mom_y", "mom_z", "cont", "T", "k", "epsilon")


# ------------------------------------------------------------------------------------------------ dual helpers
@wp.func
def dset(z: Any, v: wp.float64):
    r = z
    r[0] = v
    return r


@wp.func
def dsqrt(a: Any):
    """sqrt with torch's derivative (grad / (2 result))."""
    v = wp.sqrt(a[0])
    r = a / (wp.float64(2.0) * v)
    r[0] = v
    return r


@wp.func
def dlog(a: Any):
    """log with torch's derivative (grad / x)."""
    r = a / a[0]
    r[0] = wp.log(a[0])
    return r


@wp.func
def log1p_(y: wp.float64):
    u = wp.float64(1.0) + y
    if u == wp.float64(1.0):
        return y
    return wp.log(u) * (y / (u - wp.float64(1.0)))


@wp.func
def dsoftplus(z: Any):
    """torch softplus (beta 1, threshold 20)."""
    z0 = z[0]
    if z0 > wp.float64(20.0):
        return z
    e = wp.exp(z0)
    r = z * e / (e + wp.float64(1.0))
    r[0] = log1p_(e)
    return r


@wp.func
def dsmooth_floor(x: Any, floor: wp.float64, width: wp.float64):
    """Turbulence.smooth.smooth_floor: floor + width softplus((x - floor) / width)."""
    t = x
    t[0] = x[0] - floor
    sp = dsoftplus(t / width)
    r = sp * width
    r[0] = floor + sp[0] * width
    return r


@wp.func
def sgn(x: wp.float64):
    if x > wp.float64(0.0):
        return wp.float64(1.0)
    if x < wp.float64(0.0):
        return wp.float64(-1.0)
    return wp.float64(0.0)


@wp.func
def dabs(a: Any):
    """abs with torch's derivative (sign(x), 0 at 0)."""
    r = a * sgn(a[0])
    r[0] = wp.abs(a[0])
    return r


@wp.func
def clamp01(x: wp.float64):
    return wp.min(wp.max(x, wp.float64(0.0)), wp.float64(1.0))


# ------------------------------------------------------------------------------------------------ structs
@wp.struct
class DCConst:
    kmin: wp.float64
    kwidth: wp.float64
    emin: wp.float64
    ewidth: wp.float64
    cmu: wp.float64
    c1cmu: wp.float64          # C1 * Cmu (kEpsilon's production coefficient, one Python product as torch forms it)
    c2: wp.float64
    sigk: wp.float64
    sige: wp.float64
    cg: wp.float64
    gb: int                    # Gb term present (kEpsilon; realizableKE with Cg != 0)
    cmu25: wp.float64          # wallCmu ** 0.25
    wkappa: wp.float64
    wE: wp.float64
    ypl: wp.float64            # yPlusLam
    eps2: wp.float64           # wallGradUEps ** 2
    prt: wp.float64
    scheme_u: int
    scheme_t: int
    scheme_k: int
    scheme_e: int
    bnd_u: int
    bnd_t: int
    bnd_k: int
    bnd_e: int
    kk_t: wp.float64
    kk_k: wp.float64
    kk_e: wp.float64
    model: int                 # 0 kEpsilon, 1 realizableKE
    a0: wp.float64             # realizableKE
    s2eps2: wp.float64         # s2SqrtEps ** 2
    wsmall: wp.float64
    acos_b: wp.float64         # 1 - rCmuAcosEps
    jaya: int                  # realizableKE wallAlphat = "jayatilleke"
    jprt: wp.float64
    jkappa: wp.float64
    jE: wp.float64


@wp.struct
class DCFaceGeom:
    fo: wp.array(dtype=wp.int32)
    fn: wp.array(dtype=wp.int32)
    gC: wp.array(dtype=wp.float64)
    gF: wp.array(dtype=wp.float64)
    # the gradient weights of the momentum-weighted bracket (= gC, gF and the snGrad operands unless
    # LPCNS_WARP_DC_MWI_GRADW=volume, see _build_geometry)
    bgo: wp.array(dtype=wp.float64)
    bgn: wp.array(dtype=wp.float64)
    bow_p: wp.array(dtype=wp.float64)
    bnw_p: wp.array(dtype=wp.float64)
    bow_t: wp.array(dtype=wp.float64)
    bnw_t: wp.array(dtype=wp.float64)
    nrm: wp.array(dtype=wp.vec3d)
    rcorr: wp.array(dtype=wp.vec3d)
    eCF: wp.array(dtype=wp.vec3d)
    dCF: wp.array(dtype=wp.float64)
    nonorth: wp.array(dtype=wp.float64)
    rC: wp.array(dtype=wp.vec3d)
    rF: wp.array(dtype=wp.vec3d)
    dpos: wp.array(dtype=wp.vec3d)
    ow_u: wp.array(dtype=wp.float64)
    nw_u: wp.array(dtype=wp.float64)
    cv_u: wp.array(dtype=wp.vec3d)
    ow_p: wp.array(dtype=wp.float64)
    nw_p: wp.array(dtype=wp.float64)
    cv_p: wp.array(dtype=wp.vec3d)
    ow_t: wp.array(dtype=wp.float64)
    nw_t: wp.array(dtype=wp.float64)
    cv_t: wp.array(dtype=wp.vec3d)
    ow_k: wp.array(dtype=wp.float64)
    nw_k: wp.array(dtype=wp.float64)
    cv_k: wp.array(dtype=wp.vec3d)
    ow_e: wp.array(dtype=wp.float64)
    nw_e: wp.array(dtype=wp.float64)
    cv_e: wp.array(dtype=wp.vec3d)
    bnc: wp.array(dtype=wp.int32)
    overrelax: wp.array(dtype=wp.int32)
    mwi: wp.array(dtype=wp.int32)
    bcdiff: wp.array(dtype=wp.int32)
    dev2own: wp.array(dtype=wp.int32)
    zeroflux: wp.array(dtype=wp.int32)
    pairof: wp.array(dtype=wp.int32)
    area: wp.array(dtype=wp.float64)
    gpc: wp.array(dtype=wp.float64)
    cc: wp.array(dtype=wp.float64)
    ccont: wp.array(dtype=wp.float64)
    ghb: wp.array(dtype=wp.float64)        # gh_f beta_f (phig)
    nu: wp.array(dtype=wp.float64)         # molecular nu per face (live)
    alpha: wp.array(dtype=wp.float64)      # molecular alpha per face (live)
    wslot: wp.array(dtype=wp.int32)        # nutkWallFunction face -> its slot in the wall-face tables, else -1
    wcy: wp.array(dtype=wp.float64)        # wallCmu^0.25 y_w (per wall slot)
    wy: wp.array(dtype=wp.float64)         # y_w (per wall slot)
    wpr: wp.array(dtype=wp.float64)        # Jayatilleke: Pr, P, yPlusTherm per wall slot
    wpp: wp.array(dtype=wp.float64)
    wypt: wp.array(dtype=wp.float64)


# ------------------------------------------------------------------------------------------------ kernels
@wp.kernel
def dc_load_kernel(x: wp.array2d(dtype=wp.float64), dirs: wp.array2d(dtype=wp.float64), seeded: int, p0: int,
                   seed_col: wp.array(dtype=wp.int32), seed_k: wp.array(dtype=wp.int32),
                   seed_c: wp.array(dtype=wp.int32),
                   cpd_of: wp.array(dtype=wp.int32), n: int, kdir: int, cw: int, sb: int,
                   dep_off: wp.array(dtype=wp.int32), dep_slot: wp.array(dtype=wp.int32),
                   ghost: wp.array(dtype=wp.int32), A: wp.array3d(dtype=wp.float64), b: wp.array3d(dtype=wp.float64),
                   ew_c: wp.array(dtype=wp.float64), ew_den: wp.array(dtype=wp.float64),
                   ew_kmin: wp.array(dtype=wp.float64), ew_kw: wp.array(dtype=wp.float64),
                   zt: wp.array(dtype=Any), q: wp.array3d(dtype=Any)):
    """Interior rows of the compound state (value + this chunk's tangents: the given directions, or the colouring seeds
    of probes p0 + kk), then every ghost whose map reads this cell alone: the probed affine rows and the
    epsilonWallFunction value of the owner's floored k.  ``sb = 1`` (a time window): chunk j is step j and reads row j of
    the state and of the ghost values; ``sb = 0``: every chunk reads row 0."""
    i, j = wp.tid()
    ci = cpd_of[i]
    row = j * sb
    for c in range(NF):
        r = dset(zt[0], x[row, c * n + i])
        for s in range(cw):
            kk = j * cw + s
            if kk < kdir:
                if seeded == 1:
                    p = p0 + kk
                    if seed_c[p] == c and seed_col[i] == seed_k[p]:
                        r[s + 1] = wp.float64(1.0)
                else:
                    r[s + 1] = dirs[kk, c * n + i]
        q[j, ci, c] = r
    for m in range(dep_off[i], dep_off[i + 1]):
        gs = dep_slot[m]
        gi = ghost[gs]
        for c in range(NF):
            acc = dset(zt[0], b[row, gs, c])
            for c2 in range(NF):
                a = A[gs, c, c2]
                if a != wp.float64(0.0):
                    acc = acc + q[j, ci, c2] * a
            q[j, gi, c] = acc
        cwall = ew_c[gs]
        if cwall != wp.float64(0.0):
            kf = dsmooth_floor(q[j, ci, KC], ew_kmin[gs], ew_kw[gs])
            v = dmul(kf * cwall, dsqrt(kf))
            q[j, gi, EC] = v / ew_den[gs]


@wp.kernel
def dc_porous_kernel(pg: wp.array(dtype=wp.int32), psrc: wp.array(dtype=wp.int32), pa: wp.array(dtype=wp.int32),
                     pb: wp.array(dtype=wp.int32), pn: wp.array(dtype=wp.vec3d), psign: wp.array(dtype=wp.float64),
                     pc0: wp.array(dtype=wp.float64), pc1: wp.array2d(dtype=wp.float64),
                     plen: wp.array(dtype=wp.float64), sb: int, q: wp.array3d(dtype=Any)):
    """porousBafflePressure ghosts: p_ghost = p_partner -/+ J (setP_rgh.Porousbafflepressure)."""
    g, j = wp.tid()
    a = pa[g]
    b = pb[g]
    nv = pn[g]
    u0 = wp.float64(0.5) * (q[j, a, 0] + q[j, b, 0])
    u1 = wp.float64(0.5) * (q[j, a, 1] + q[j, b, 1])
    u2 = wp.float64(0.5) * (q[j, a, 2] + q[j, b, 2])
    un = u0 * nv[0] + u1 * nv[1] + u2 * nv[2]
    au = dabs(un)
    c1 = pc1[j * sb, g]
    inner = au * c1
    inner[0] = pc0[g] + c1 * au[0]
    jj = dmul(-inner, un) * plen[g]
    q[j, pg[g], PC] = q[j, psrc[g], PC] + jj * psign[g]


@wp.kernel
def dc_fro_est_kernel(f_off: wp.array(dtype=wp.int32), f_owner: wp.array(dtype=wp.int32),
                      f_n: wp.array(dtype=wp.vec3d), f_rhoa: wp.array(dtype=wp.float64), drop: int,
                      zt: wp.array(dtype=Any), q: wp.array3d(dtype=Any), fest: wp.array2d(dtype=Any)):
    """flowRateOutletVelocity, the patch reduction: the flow-rate estimate sum(rho A max(U_owner . n, 0)), one patch
    per thread (``drop``: its tangent dropped -- the compact Jacobian omits the patch-global part)."""
    p, j = wp.tid()
    acc = zt[0]
    for m in range(f_off[p], f_off[p + 1]):
        o = f_owner[m]
        nv = f_n[m]
        nup = q[j, o, 0] * nv[0] + q[j, o, 1] * nv[1] + q[j, o, 2] * nv[2]
        acc = acc + dmax0(nup) * f_rhoa[m]
    if drop == 1:
        acc = dvalue_only(acc)
    fest[j, p] = acc


@wp.kernel
def dc_fro_write_kernel(f_patch: wp.array(dtype=wp.int32), f_owner: wp.array(dtype=wp.int32),
                        f_ghost: wp.array(dtype=wp.int32), f_n: wp.array(dtype=wp.vec3d),
                        f_target: wp.array2d(dtype=wp.float64), f_asum: wp.array(dtype=wp.float64), sb: int,
                        fest: wp.array2d(dtype=Any), q: wp.array3d(dtype=Any)):
    """flowRateOutletVelocity ghosts (setU.Flowrateoutletvelocity): the owner velocity's tangential part plus its normal
    part clamped >= 0 and rescaled by |Q / (estimate + 1e-12)| -- or, when the estimate is at most Q / 2, shifted by
    (Q - estimate) / (sum(rho A) + 1e-12)."""
    m, j = wp.tid()
    p = f_patch[m]
    o = f_owner[m]
    nv = f_n[m]
    u0 = q[j, o, 0]
    u1 = q[j, o, 1]
    u2 = q[j, o, 2]
    nup = u0 * nv[0] + u1 * nv[1] + u2 * nv[2]
    t0 = u0 - nup * nv[0]
    t1 = u1 - nup * nv[1]
    t2 = u2 - nup * nv[2]
    nup = dmax0(nup)
    est = fest[j, p]
    tq = f_target[j * sb, p]
    nnew = nup
    if FRO_BAND > wp.float64(0.0) and tq != wp.float64(0.0):
        # the smooth blend: w = smoothstep((est / Q - (0.5 - d)) / (2 d)), nnew = w rescaled + (1 - w) shifted; a
        # patch without flow (Q = 0) has no ratio est / Q and keeps the switch below (both branches: no outflow)
        adj = -est / (f_asum[p] + wp.float64(1e-12))
        adj[0] = (tq - est[0]) / (f_asum[p] + wp.float64(1e-12))
        sh = nup + adj
        t = est / (tq * wp.float64(2.0) * FRO_BAND)          # dual: only the value component is shifted
        t[0] = t[0] - (wp.float64(0.5) - FRO_BAND) / (wp.float64(2.0) * FRO_BAND)
        if t[0] <= wp.float64(0.0):
            nnew = sh
        else:
            den = est
            den[0] = est[0] + wp.float64(1e-12)
            num = est * wp.float64(0.0)
            num[0] = tq
            sc = dmul(nup, dabs(ddiv(num, den)))
            if t[0] >= wp.float64(1.0):
                nnew = sc
            else:
                c3 = t * wp.float64(-2.0)
                c3[0] = c3[0] + wp.float64(3.0)
                w = dmul(dmul(t, t), c3)
                om = -w
                om[0] = om[0] + wp.float64(1.0)
                nnew = dmul(w, sc) + dmul(om, sh)
    elif est[0] > wp.float64(0.5) * tq:
        den = est
        den[0] = est[0] + wp.float64(1e-12)
        num = est * wp.float64(0.0)
        num[0] = tq
        nnew = dmul(nup, dabs(ddiv(num, den)))
    else:
        adj = -est / (f_asum[p] + wp.float64(1e-12))
        adj[0] = (tq - est[0]) / (f_asum[p] + wp.float64(1e-12))
        nnew = nup + adj
    g = f_ghost[m]
    q[j, g, 0] = t0 + nnew * nv[0]
    q[j, g, 1] = t1 + nnew * nv[1]
    q[j, g, 2] = t2 + nnew * nv[2]


@wp.kernel
def dc_rack_mean_kernel(r_off: wp.array(dtype=wp.int32), r_src: wp.array(dtype=wp.int32),
                        r_area: wp.array(dtype=wp.float64), r_asum: wp.array(dtype=wp.float64),
                        r_flux: wp.array(dtype=wp.int32), r_gh: wp.array(dtype=wp.int32),
                        r_n: wp.array(dtype=wp.vec3d), drop: int,
                        zt: wp.array(dtype=Any), q: wp.array3d(dtype=Any), rmean: wp.array2d(dtype=Any)):
    """CBBM (Bai Eq. 15): the area mean of the intake owners' T, one rack per thread; ``meanWeight = "flux"``:
    sum(T phi) / sum(phi) with phi = (u_ghost . n) A on the intake faces, the area mean where the intake carries no
    flow (a stopped unit, a server without heat: the flux weights are undefined, 0 / 0)."""
    r, j = wp.tid()
    acc = zt[0]
    mean = zt[0]
    area = 1
    if r_flux[r] == 1:
        den = zt[0]
        for m in range(r_off[r], r_off[r + 1]):
            gh = r_gh[m]
            nv = r_n[m]
            phi = (q[j, gh, 0] * nv[0] + q[j, gh, 1] * nv[1] + q[j, gh, 2] * nv[2]) * r_area[m]
            acc = acc + dmul(q[j, r_src[m], TC], phi)
            den = den + phi
        if den[0] != wp.float64(0.0):
            mean = ddiv(acc, den)
            area = 0
    if area == 1:
        acc = zt[0]
        for m in range(r_off[r], r_off[r + 1]):
            acc = acc + q[j, r_src[m], TC] * r_area[m]
        mean = acc / r_asum[r]
    if drop == 1:
        mean = dvalue_only(mean)
    rmean[j, r] = mean


@wp.kernel
def dc_rack_write_kernel(o_ghost: wp.array(dtype=wp.int32), o_rack: wp.array(dtype=wp.int32),
                         o_remote: wp.array(dtype=wp.int32), o_dt: wp.array2d(dtype=wp.float64),
                         o_lag: wp.array(dtype=wp.float64), sb: int, rmean: wp.array2d(dtype=Any),
                         q: wp.array3d(dtype=Any)):
    """rackOutletTemperature ghosts: CBBM live mean + dT; (IBBM) the paired intake owner's T + dT; CBBM lagMean: the
    constant lagged mean + dT (``o_remote = -2``, ``o_lag`` the host-side sum, a leaf)."""
    g, j = wp.tid()
    rem = o_remote[g]
    v = rmean[j, 0]
    if rem >= 0:
        v = q[j, rem, TC]
        v[0] = v[0] + o_dt[j * sb, g]
    elif rem == -1:
        v = rmean[j, o_rack[g]]
        v[0] = v[0] + o_dt[j * sb, g]
    else:
        v = v * wp.float64(0.0)
        v[0] = o_lag[g]
    q[j, o_ghost[g], TC] = v


@wp.kernel
def dc_rack_sums_kernel(r_off: wp.array(dtype=wp.int32), r_src: wp.array(dtype=wp.int32),
                        r_area: wp.array(dtype=wp.float64), r_flux: wp.array(dtype=wp.int32),
                        r_gh: wp.array(dtype=wp.int32), r_n: wp.array(dtype=wp.vec3d),
                        zt: wp.array(dtype=Any), q: wp.array3d(dtype=Any), racc: wp.array2d(dtype=Any),
                        rden: wp.array2d(dtype=Any)):
    """Multi-GPU (``_preduce`` set): ``dc_rack_mean_kernel``'s numerator and (flux weighting) denominator, kept
    apart so that both can be all-reduced over the parts before ``dc_rack_div_kernel`` divides."""
    r, j = wp.tid()
    acc = zt[0]
    den = zt[0]
    if r_flux[r] == 1:
        for m in range(r_off[r], r_off[r + 1]):
            gh = r_gh[m]
            nv = r_n[m]
            phi = (q[j, gh, 0] * nv[0] + q[j, gh, 1] * nv[1] + q[j, gh, 2] * nv[2]) * r_area[m]
            acc = acc + dmul(q[j, r_src[m], TC], phi)
            den = den + phi
    else:
        for m in range(r_off[r], r_off[r + 1]):
            acc = acc + q[j, r_src[m], TC] * r_area[m]
    racc[j, r] = acc
    rden[j, r] = den


@wp.kernel
def dc_rack_div_kernel(r_flux: wp.array(dtype=wp.int32), r_asum: wp.array(dtype=wp.float64), drop: int,
                       racc: wp.array2d(dtype=Any), rden: wp.array2d(dtype=Any), rmean: wp.array2d(dtype=Any)):
    """Multi-GPU: the CBBM mean from the all-reduced sums of ``dc_rack_sums_kernel``."""
    r, j = wp.tid()
    mean = racc[j, r]
    if r_flux[r] == 1:
        mean = ddiv(racc[j, r], rden[j, r])
    else:
        mean = racc[j, r] / r_asum[r]
    if drop == 1:
        mean = dvalue_only(mean)
    rmean[j, r] = mean


@wp.kernel
def dc_wlsq_kernel(q: wp.array3d(dtype=Any), cpd_of: wp.array(dtype=wp.int32), off: wp.array(dtype=wp.int32),
                   other: wp.array(dtype=wp.int32), w: wp.array(dtype=wp.vec3d), mir_off: wp.array(dtype=wp.int32),
                   mir_cpd: wp.array(dtype=wp.int32), freeze: int, zt: wp.array(dtype=Any),
                   gsel: wp.array(dtype=wp.int32), goff: wp.array(dtype=wp.int32), gother: wp.array(dtype=wp.int32),
                   gw: wp.array(dtype=wp.vec3d), grad: wp.array4d(dtype=Any)):
    """WLSQ "1st" gradient sum_k w_k (q_other - q_i) of every field at the interior cells, copied to the cyclic ghosts
    this cell is the partner of; ``freeze`` drops the tangents (the Picard compact Jacobian).  A field with
    ``gsel`` = 1 takes the Green-Gauss weights instead (``_build_gauss``; the same sum over the cell's faces)."""
    i, c, j = wp.tid()
    ci = cpd_of[i]
    qi = q[j, ci, c]
    gx = zt[0]
    gy = zt[0]
    gz = zt[0]
    if gsel[c] == 1:
        for k in range(goff[i], goff[i + 1]):
            d = q[j, gother[k], c] - qi
            wk = gw[k]
            gx = gx + wk[0] * d
            gy = gy + wk[1] * d
            gz = gz + wk[2] * d
    else:
        for k in range(off[i], off[i + 1]):
            d = q[j, other[k], c] - qi
            wk = w[k]
            gx = gx + wk[0] * d
            gy = gy + wk[1] * d
            gz = gz + wk[2] * d
    if freeze == 1:
        gx = dvalue_only(gx)
        gy = dvalue_only(gy)
        gz = dvalue_only(gz)
    grad[j, ci, c, 0] = gx
    grad[j, ci, c, 1] = gy
    grad[j, ci, c, 2] = gz
    for m in range(mir_off[i], mir_off[i + 1]):
        gi = mir_cpd[m]
        grad[j, gi, c, 0] = gx
        grad[j, gi, c, 1] = gy
        grad[j, gi, c, 2] = gz


@wp.func
def strain_inv(grad: wp.array4d(dtype=Any), j: int, c: int):
    """realizableKE.strain_invariants of the velocity gradient of cell c: (S2, SSS, Om2)."""
    g00 = grad[j, c, 0, 0]
    g01 = grad[j, c, 0, 1]
    g02 = grad[j, c, 0, 2]
    g10 = grad[j, c, 1, 0]
    g11 = grad[j, c, 1, 1]
    g12 = grad[j, c, 1, 2]
    g20 = grad[j, c, 2, 0]
    g21 = grad[j, c, 2, 1]
    g22 = grad[j, c, 2, 2]
    tr3 = (g00 + g11 + g22) / wp.float64(3.0)
    half = wp.float64(0.5)
    s00 = half * (g00 + g00) - tr3
    s11 = half * (g11 + g11) - tr3
    s22 = half * (g22 + g22) - tr3
    s01 = half * (g01 + g10)
    s02 = half * (g02 + g20)
    s12 = half * (g12 + g21)
    ss = dmul(s00, s00) + dmul(s01, s01) + dmul(s02, s02) + dmul(s01, s01) + dmul(s11, s11) + dmul(s12, s12) \
        + dmul(s02, s02) + dmul(s12, s12) + dmul(s22, s22)
    s2 = wp.float64(2.0) * ss
    # (S.S) : S with S symmetric
    p00 = dmul(s00, s00) + dmul(s01, s01) + dmul(s02, s02)
    p01 = dmul(s00, s01) + dmul(s01, s11) + dmul(s02, s12)
    p02 = dmul(s00, s02) + dmul(s01, s12) + dmul(s02, s22)
    p11 = dmul(s01, s01) + dmul(s11, s11) + dmul(s12, s12)
    p12 = dmul(s01, s02) + dmul(s11, s12) + dmul(s12, s22)
    p22 = dmul(s02, s02) + dmul(s12, s12) + dmul(s22, s22)
    sss = dmul(p00, s00) + dmul(p01, s01) + dmul(p02, s02) + dmul(p01, s01) + dmul(p11, s11) + dmul(p12, s12) \
        + dmul(p02, s02) + dmul(p12, s12) + dmul(p22, s22)
    w01 = half * (g01 - g10)
    w02 = half * (g02 - g20)
    w12 = half * (g12 - g21)
    om2 = wp.float64(2.0) * (dmul(w01, w01) + dmul(w02, w02) + dmul(w12, w12))
    return s2, sss, om2


@wp.func
def dacos_clamped(x: Any, bnd: wp.float64):
    """acos(clamp(x, -b, b)) with torch's derivatives (clamp passes the tangent on [-b, b] inclusive)."""
    v = x[0]
    r = x * wp.float64(0.0)
    if v >= -bnd and v <= bnd:
        r = x * (-wp.float64(1.0) / wp.sqrt(wp.float64(1.0) - v * v))
        r[0] = wp.acos(v)
    else:
        r[0] = wp.acos(wp.min(wp.max(v, -bnd), bnd))
    return r


@wp.func
def r_cmu(s2: Any, sss: Any, om2: Any, kf: Any, ef: Any, kc: DCConst):
    """RealizableKE.r_cmu_from_invariants."""
    ms = s2
    ms[0] = s2[0] + kc.s2eps2
    mag_s = dsqrt(ms)
    den = dmul(mag_s, s2)
    den[0] = den[0] + kc.wsmall
    w = ddiv((wp.float64(2.0) * wp.sqrt(wp.float64(2.0))) * sss, den)
    phis = dacos_clamped(wp.sqrt(wp.float64(6.0)) * w, kc.acos_b) / wp.float64(3.0)
    cs = phis * (-wp.sin(phis[0]))
    cs[0] = wp.cos(phis[0])
    a_s = wp.sqrt(wp.float64(6.0)) * cs
    us2 = wp.float64(0.5) * s2 + om2
    us2[0] = us2[0] + kc.s2eps2
    u_s = dsqrt(us2)
    t = ddiv(dmul(dmul(a_s, u_s), kf), ef)
    t[0] = kc.a0 + t[0]
    one = t * wp.float64(0.0)
    one[0] = wp.float64(1.0)
    return ddiv(one, t)


@wp.kernel
def dc_nut_kernel(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), inv_src: wp.array(dtype=wp.int32),
                  kc: DCConst, zt: wp.array(dtype=Any), nut: wp.array2d(dtype=Any)):
    """nu_t at every compound cell: kEpsilon Cmu k_f^2 / eps_f; realizableKE rCmu k_f^2 / eps_f with the invariants of
    the gradient of ``inv_src`` (the cell itself, a cyclic ghost's own (partner) gradient, the owner's on a physical
    ghost: ghost_fill_owner)."""
    g, j = wp.tid()
    kf = dsmooth_floor(q[j, g, KC], kc.kmin, kc.kwidth)
    ef = dsmooth_floor(q[j, g, EC], kc.emin, kc.ewidth)
    if kc.model == 0:
        nut[j, g] = ddiv(dmul(kf * kc.cmu, kf), ef)
    else:
        s2, sss, om2 = strain_inv(grad, j, inv_src[g])
        rc = r_cmu(s2, sss, om2, kf, ef, kc)
        nut[j, g] = ddiv(dmul(dmul(rc, kf), kf), ef)


@wp.func
def igrad(grad: wp.array4d(dtype=Any), j: int, o: int, n: int, c: int, d: int, gc: wp.float64, gf: wp.float64):
    return gc * grad[j, o, c, d] + gf * grad[j, n, c, d]


@wp.func
def orcorr(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), j: int, o: int, n: int, c: int,
           gc: wp.float64, gf: wp.float64, e: wp.vec3d, dcf: wp.float64):
    gdot = igrad(grad, j, o, n, c, 0, gc, gf) * e[0] + igrad(grad, j, o, n, c, 1, gc, gf) * e[1] \
        + igrad(grad, j, o, n, c, 2, gc, gf) * e[2]
    return (q[j, n, c] - q[j, o, c]) / dcf - gdot


@wp.func
def sn_grad(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), j: int, o: int, n: int, c: int,
            nonorth: wp.float64, ow: wp.float64, nw: wp.float64, cv: wp.vec3d):
    s = (q[j, n, c] - q[j, o, c]) * nonorth
    for d in range(3):
        s = s + (ow * grad[j, o, c, d] + nw * grad[j, n, c, d]) * cv[d]
    return s


@wp.func
def fgrad(grad: wp.array4d(dtype=Any), j: int, o: int, n: int, c: int, d: int, gc: wp.float64, gf: wp.float64,
          corr: Any, e: wp.vec3d, apply: int):
    v = igrad(grad, j, o, n, c, d, gc, gf)
    if apply == 1:
        v = v + corr * e[d]
    return v


@wp.func
def normal_grad(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), j: int, o: int, n: int, c: int,
                gc: wp.float64, gf: wp.float64, g: DCFaceGeom, f: int, zero: Any):
    """("Linear overRelaxed" or "Linear") face gradient . n of channel c."""
    apply = g.overrelax[f]
    e = g.eCF[f]
    nv = g.nrm[f]
    ct = zero
    if apply == 1:
        ct = orcorr(q, grad, j, o, n, c, gc, gf, e, g.dCF[f])
    return fgrad(grad, j, o, n, c, 0, gc, gf, ct, e, apply) * nv[0] \
        + fgrad(grad, j, o, n, c, 1, gc, gf, ct, e, apply) * nv[1] \
        + fgrad(grad, j, o, n, c, 2, gc, gf, ct, e, apply) * nv[2]


@wp.func
def limited_face(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), j: int, o: int, n: int, c: int,
                 gc: wp.float64, gf: wp.float64, dv: wp.vec3d, up_owner: int, kk: wp.float64):
    """limited_linear_face_value (hard NVDTVD limiter), torch's derivative branches (picoext_komega_warp)."""
    phic = q[j, o, c]
    phin = q[j, n, c]
    phiu = phin
    up = n
    if up_owner == 1:
        phiu = phic
        up = o
    gradf = phin - phic
    gradcf = grad[j, up, c, 0] * dv[0] + grad[j, up, c, 1] * dv[1] + grad[j, up, c, 2] * dv[2]
    plin = phic * gc + phin * gf
    small = SMALL64
    mgf = wp.abs(gradf[0])
    mgcf = wp.abs(gradcf[0])
    psi = phic * wp.float64(0.0)
    if mgf < small:
        psi[0] = clamp01(wp.float64(2.0) * wp.float64(1.0) / kk)
    elif mgcf >= wp.float64(1000.0) * mgf:
        rl = wp.float64(2000.0) * sgn(gradcf[0]) * sgn(gradf[0]) - wp.float64(1.0)
        psi[0] = clamp01(wp.float64(2.0) * rl / kk)
    else:
        gs = gradf
        gs[0] = gradf[0] + sgn(gradf[0]) * small
        r = ddiv(wp.float64(2.0) * gradcf, gs)
        r[0] = r[0] - wp.float64(1.0)
        xx = (wp.float64(2.0) * r) / kk
        if xx[0] >= wp.float64(0.0) and xx[0] <= wp.float64(1.0):
            psi = xx
        else:
            psi[0] = clamp01(xx[0])
    return phiu + dmul(psi, plin - phiu)


@wp.func
def face_value(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), j: int, o: int, n: int, c: int,
               gc: wp.float64, gf: wp.float64, g: DCFaceGeom, f: int, up_owner: int, scheme: int, kk: wp.float64):
    """div_phic_to_faces of one channel: the central schemes on every face; the upwind-based ones take the ghost value
    on physical boundary faces (apply_boundary_face_values) and the strictly positive flux test elsewhere."""
    if scheme == S_LINEAR:
        return q[j, o, c] * gc + q[j, n, c] * gf
    if scheme == S_LINEAR_CORR:
        r = g.rcorr[f]
        v = gc * q[j, o, c] + gf * q[j, n, c]
        for d in range(3):
            v = v + r[d] * (gc * grad[j, o, c, d] + gf * grad[j, n, c, d])
        return v
    if g.bnc[f] == 1:
        return q[j, n, c]
    if scheme == S_LIMITED:
        return limited_face(q, grad, j, o, n, c, gc, gf, g.dpos[f], up_owner, kk)
    if scheme == S_LUPWIND:
        if up_owner == 1:
            r = g.rC[f]
            return q[j, o, c] + grad[j, o, c, 0] * r[0] + grad[j, o, c, 1] * r[1] + grad[j, o, c, 2] * r[2]
        r = g.rF[f]
        return q[j, n, c] + grad[j, n, c, 0] * r[0] + grad[j, n, c, 1] * r[1] + grad[j, n, c, 2] * r[2]
    if up_owner == 1:
        return q[j, o, c]
    return q[j, n, c]


@wp.kernel
def dc_face_pre_kernel(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), nut: wp.array2d(dtype=Any),
                       g: DCFaceGeom, kc: DCConst, zt: wp.array(dtype=Any), f0: wp.array2d(dtype=Any),
                       nutf: wp.array2d(dtype=Any)):
    """The uncorrected advecting flux u_f . n ("Linear corrected" face velocity; a_P is built from it) and the face
    nu_t: linear, nutkWallFunction (stepwise) on the epsilonWallFunction faces (KEpsilon.nut_wall_face)."""
    f, j = wp.tid()
    o = g.fo[f]
    n = g.fn[f]
    gc = g.gC[f]
    gf = g.gF[f]
    r = g.rcorr[f]
    nv = g.nrm[f]
    v0 = gc * q[j, o, 0] + gf * q[j, n, 0]
    v1 = gc * q[j, o, 1] + gf * q[j, n, 1]
    v2 = gc * q[j, o, 2] + gf * q[j, n, 2]
    for d in range(3):
        v0 = v0 + r[d] * (gc * grad[j, o, 0, d] + gf * grad[j, n, 0, d])
        v1 = v1 + r[d] * (gc * grad[j, o, 1, d] + gf * grad[j, n, 1, d])
        v2 = v2 + r[d] * (gc * grad[j, o, 2, d] + gf * grad[j, n, 2, d])
    f0[j, f] = v0 * nv[0] + v1 * nv[1] + v2 * nv[2]
    nt = gc * nut[j, o] + gf * nut[j, n]
    ws = g.wslot[f]
    if ws >= 0:
        nuw = g.nu[f]
        sk = dsqrt(dsmooth_floor(q[j, o, KC], kc.kmin, kc.kwidth))
        yp = (sk * g.wcy[ws]) / nuw
        ey = yp * kc.wE
        lg = zt[0]
        if ey[0] >= wp.float64(1.0001):
            lg = dlog(ey)
        else:
            lg = dset(zt[0], wp.log(wp.float64(1.0001)))
        tot = dset(zt[0], nuw)
        if yp[0] > kc.ypl:
            tot = ddiv((yp * nuw) * kc.wkappa, lg)
        tot[0] = tot[0] - nuw
        nt = tot
    nutf[j, f] = nt


@wp.kernel
def dc_ap_kernel(f0: wp.array2d(dtype=Any), nutf: wp.array2d(dtype=Any), cpd_of: wp.array(dtype=wp.int32),
                 off: wp.array(dtype=wp.int32), face: wp.array(dtype=wp.int32), sarea: wp.array(dtype=wp.float64),
                 abss: wp.array(dtype=wp.float64), delta: wp.array(dtype=wp.float64), nuf: wp.array(dtype=wp.float64),
                 vol: wp.array(dtype=wp.float64), frozen: int, mir_off: wp.array(dtype=wp.int32),
                 mir_cpd: wp.array(dtype=wp.int32), zt: wp.array(dtype=Any), dcell: wp.array2d(dtype=Any)):
    """a_P = sum_f [max(F_f s_f, 0) + nu_eff,f |s_f| delta_f], d_P = V_P / a_P (frozen: tangents dropped), copied to
    the cyclic ghosts this cell is the partner of."""
    i, j = wp.tid()
    ap = zt[0]
    for k in range(off[i], off[i + 1]):
        f = face[k]
        nue = nutf[j, f]
        nue[0] = nuf[f] + nue[0]
        ap = ap + (dmax0(f0[j, f] * sarea[k]) + (nue * abss[k]) * delta[k])
    d = ddiv(dset(zt[0], vol[i]), ap)
    if frozen == 1:
        d = dvalue_only(d)
    dcell[j, cpd_of[i]] = d
    for m in range(mir_off[i], mir_off[i + 1]):
        dcell[j, mir_cpd[m]] = d


@wp.func
def mwi_flux(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), f0: wp.array2d(dtype=Any),
             dcell: wp.array2d(dtype=Any), g: DCFaceGeom, j: int, f: int):
    """u_f . n + d_f gpc [(grad p)_f . n - snGrad(p) + gh beta (snGrad(T) - (grad T)_f . n)] on the MWI faces."""
    fl = f0[j, f]
    if g.mwi[f] == 1:
        o = g.fo[f]
        n = g.fn[f]
        gc = g.bgo[f]
        gf = g.bgn[f]
        nv = g.nrm[f]
        snp = sn_grad(q, grad, j, o, n, PC, g.nonorth[f], g.bow_p[f], g.bnw_p[f], g.cv_p[f])
        interp = igrad(grad, j, o, n, PC, 0, gc, gf) * nv[0] + igrad(grad, j, o, n, PC, 1, gc, gf) * nv[1] \
            + igrad(grad, j, o, n, PC, 2, gc, gf) * nv[2]
        snt = sn_grad(q, grad, j, o, n, TC, g.nonorth[f], g.bow_t[f], g.bnw_t[f], g.cv_t[f])
        intt = igrad(grad, j, o, n, TC, 0, gc, gf) * nv[0] + igrad(grad, j, o, n, TC, 1, gc, gf) * nv[1] \
            + igrad(grad, j, o, n, TC, 2, gc, gf) * nv[2]
        br = (interp - snp) + g.ghb[f] * (snt - intt)
        df = wp.float64(0.5) * (dcell[j, o] + dcell[j, n])
        fl = fl + dmul(df * g.gpc[f], br)
    elif g.mwi[f] == 2:
        # a first-type pressure boundary face (momentumInterpolationDirichletPressure): OpenFOAM's boundary form
        # u_f = u_P - d_P (snGrad p - grad p_P . n), the owner's gradient and the owner's whole d_P; the buoyancy
        # part of the bracket as on the other faces (BaseSolver.momentum_weighted_flux)
        o = g.fo[f]
        n = g.fn[f]
        gc = g.bgo[f]
        gf = g.bgn[f]
        nv = g.nrm[f]
        snp = sn_grad(q, grad, j, o, n, PC, g.nonorth[f], g.bow_p[f], g.bnw_p[f], g.cv_p[f])
        own = grad[j, o, PC, 0] * nv[0] + grad[j, o, PC, 1] * nv[1] + grad[j, o, PC, 2] * nv[2]
        snt = sn_grad(q, grad, j, o, n, TC, g.nonorth[f], g.bow_t[f], g.bnw_t[f], g.cv_t[f])
        intt = igrad(grad, j, o, n, TC, 0, gc, gf) * nv[0] + igrad(grad, j, o, n, TC, 1, gc, gf) * nv[1] \
            + igrad(grad, j, o, n, TC, 2, gc, gf) * nv[2]
        br = (own - snp) + g.ghb[f] * (snt - intt)
        fl = fl + dmul(dcell[j, o] * g.gpc[f], br)
    return fl


@wp.kernel
def dc_face_kernel(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), f0: wp.array2d(dtype=Any),
                   dcell: wp.array2d(dtype=Any), nutf: wp.array2d(dtype=Any), g: DCFaceGeom, kc: DCConst,
                   zt: wp.array(dtype=Any), ffreeze: int, flux_out: wp.array2d(dtype=Any), fmom: wp.array3d(dtype=Any),
                   fsc: wp.array3d(dtype=Any), fvol: wp.array(dtype=wp.float64),
                   nueff_out: wp.array(dtype=wp.float64), alphaeff_out: wp.array(dtype=wp.float64)):
    """Per face: the advecting flux (MWI, conservative cyclic pair, Symmetry / Slip zero); the momentum fluxes
    convection - nu_eff [face-gradient . n or snGrad + dev2 transpose]; the T, k and eps fluxes convection - diffusivity
    x (face-gradient . n or snGrad)."""
    f, j = wp.tid()
    o = g.fo[f]
    n = g.fn[f]
    nv = g.nrm[f]
    gc = g.gC[f]
    gf = g.gF[f]
    flux = mwi_flux(q, grad, f0, dcell, g, j, f)
    p = g.pairof[f]
    if p >= 0:
        fp = mwi_flux(q, grad, f0, dcell, g, j, p)
        half = wp.float64(0.5) * (flux * g.area[f] - fp * g.area[p])
        flux = half / g.area[f]
    zero = zt[0]
    nut = nutf[j, f]
    nue = nut
    nue[0] = g.nu[f] + nut[0]
    # alphaEff = alpha + nut / Prt; alphatJayatillekeWallFunction on the wall faces (realizableKE, opt-in)
    ae = nut / kc.prt
    ae[0] = g.alpha[f] + ae[0]
    ws = g.wslot[f]
    if kc.jaya == 1 and ws >= 0:
        nuw = g.nu[f]
        sk = dsqrt(dsmooth_floor(q[j, o, KC], kc.kmin, kc.kwidth))
        dl = g.nonorth[f]
        du0 = (q[j, n, 0] - q[j, o, 0]) * dl
        du1 = (q[j, n, 1] - q[j, o, 1]) * dl
        du2 = (q[j, n, 2] - q[j, o, 2]) * dl
        m2 = dmul(du0, du0) + dmul(du1, du1) + dmul(du2, du2)
        m2[0] = m2[0] + kc.eps2
        mag = dsqrt(m2)
        ylog = (sk * g.wcy[ws]) / nuw
        yplus = ylog
        if ylog[0] < kc.ypl:
            yplus = (dsqrt(dmul(nue, mag)) * g.wy[ws]) / nuw
        ypt = g.wypt[ws]
        at = zero
        if yplus[0] > ypt:
            lg = dlog(yplus * kc.jE)
            den = (lg / kc.jkappa)
            den[0] = den[0] + g.wpp[ws]
            kt = ddiv(yplus, den * kc.jprt)
            kt[0] = kt[0] - wp.float64(1.0) / g.wpr[ws]
            kt = kt * nuw
            if kt[0] >= wp.float64(0.0):
                at = kt
        ae = at
        ae[0] = g.alpha[f] + at[0]
    if j == 0:
        fvol[f] = flux[0]
        nueff_out[f] = nue[0]
        alphaeff_out[f] = ae[0]
    if g.zeroflux[f] == 1:
        flux = zero
    flux_out[j, f] = flux
    up_owner = 0
    if flux[0] > wp.float64(0.0):
        up_owner = 1
    # ffreeze = 1 (the "oseen" compact Jacobian of the preconditioner): the advecting flux of every convection term is
    # a frozen coefficient (its tangent dropped); the continuity row keeps the full flux_out above
    if ffreeze == 1:
        flux = dvalue_only(flux)
    cf = g.cc[f] * flux
    apply = g.overrelax[f]
    e = g.eCF[f]
    dcf = g.dCF[f]
    bcd = g.bcdiff[f]
    own = g.dev2own[f]
    c0 = zero
    c1 = zero
    c2 = zero
    if apply == 1:
        c0 = orcorr(q, grad, j, o, n, 0, gc, gf, e, dcf)
        c1 = orcorr(q, grad, j, o, n, 1, gc, gf, e, dcf)
        c2 = orcorr(q, grad, j, o, n, 2, gc, gf, e, dcf)
    tr = zero
    if own == 1:
        tr = grad[j, o, 0, 0] + grad[j, o, 1, 1] + grad[j, o, 2, 2]
    else:
        tr = fgrad(grad, j, o, n, 0, 0, gc, gf, c0, e, apply) + fgrad(grad, j, o, n, 1, 1, gc, gf, c1, e, apply) \
            + fgrad(grad, j, o, n, 2, 2, gc, gf, c2, e, apply)
    for i in range(3):
        uc = face_value(q, grad, j, o, n, i, gc, gf, g, f, up_owner, kc.scheme_u, wp.float64(1.0))
        ci = c0
        if i == 1:
            ci = c1
        if i == 2:
            ci = c2
        gfl = zero
        if bcd == 1:
            gfl = sn_grad(q, grad, j, o, n, i, g.nonorth[f], g.ow_u[f], g.nw_u[f], g.cv_u[f])
        else:
            gfl = fgrad(grad, j, o, n, i, 0, gc, gf, ci, e, apply) * nv[0] \
                + fgrad(grad, j, o, n, i, 1, gc, gf, ci, e, apply) * nv[1] \
                + fgrad(grad, j, o, n, i, 2, gc, gf, ci, e, apply) * nv[2]
        col = zero
        if own == 1:
            col = grad[j, o, 0, i] * nv[0] + grad[j, o, 1, i] * nv[1] + grad[j, o, 2, i] * nv[2]
        else:
            col = fgrad(grad, j, o, n, 0, i, gc, gf, c0, e, apply) * nv[0] \
                + fgrad(grad, j, o, n, 1, i, gc, gf, c1, e, apply) * nv[1] \
                + fgrad(grad, j, o, n, 2, i, gc, gf, c2, e, apply) * nv[2]
        col = col - (TWO_THIRDS * tr) * nv[i]
        vis = dmul(gfl, nue) + dmul(col, nue)
        fmom[j, f, i] = dmul(cf, uc) - vis
    # temperature
    tf = face_value(q, grad, j, o, n, TC, gc, gf, g, f, up_owner, kc.scheme_t, kc.kk_t)
    gft = zero
    if bcd == 1:
        gft = sn_grad(q, grad, j, o, n, TC, g.nonorth[f], g.ow_t[f], g.nw_t[f], g.cv_t[f])
    else:
        gft = normal_grad(q, grad, j, o, n, TC, gc, gf, g, f, zero)
    fsc[j, f, 0] = dmul(cf, tf) - dmul(ae, gft)
    # k and epsilon
    for t in range(2):
        c = KC + t
        scheme = kc.scheme_k
        kk = kc.kk_k
        sig = kc.sigk
        if t == 1:
            scheme = kc.scheme_e
            kk = kc.kk_e
            sig = kc.sige
        phif = face_value(q, grad, j, o, n, c, gc, gf, g, f, up_owner, scheme, kk)
        gflt = zero
        if bcd == 1:
            if t == 0:
                gflt = sn_grad(q, grad, j, o, n, c, g.nonorth[f], g.ow_k[f], g.nw_k[f], g.cv_k[f])
            else:
                gflt = sn_grad(q, grad, j, o, n, c, g.nonorth[f], g.ow_e[f], g.nw_e[f], g.cv_e[f])
        else:
            gflt = normal_grad(q, grad, j, o, n, c, gc, gf, g, f, zero)
        diffus = nut / sig
        diffus[0] = g.nu[f] + diffus[0]
        fsc[j, f, 1 + t] = dmul(cf, phif) - dmul(gflt, diffus)


@wp.kernel
def dc_cell_kernel(q: wp.array3d(dtype=Any), grad: wp.array4d(dtype=Any), nut: wp.array2d(dtype=Any),
                   nutf: wp.array2d(dtype=Any), qold: wp.array3d(dtype=wp.float64), flux: wp.array2d(dtype=Any),
                   fmom: wp.array3d(dtype=Any), fsc: wp.array3d(dtype=Any), ccont: wp.array(dtype=wp.float64),
                   nuf: wp.array(dtype=wp.float64),
                   cpd_of: wp.array(dtype=wp.int32), off: wp.array(dtype=wp.int32), face: wp.array(dtype=wp.int32),
                   sarea: wp.array(dtype=wp.float64), vol: wp.array(dtype=wp.float64),
                   tcoef: wp.array(dtype=wp.float64), pcoef: wp.array(dtype=wp.float64),
                   ghbc: wp.array(dtype=wp.float64), betac: wp.array(dtype=wp.float64),
                   gvec: wp.array(dtype=wp.vec3d), src: wp.array2d(dtype=wp.float64),
                   nucell: wp.array(dtype=wp.float64),
                   woff: wp.array(dtype=wp.int32), wface: wp.array(dtype=wp.int32), wghost: wp.array(dtype=wp.int32),
                   wdelta: wp.array(dtype=wp.float64), wky: wp.array(dtype=wp.float64),
                   coff: wp.array(dtype=wp.int32), cghost: wp.array(dtype=wp.int32), cform: wp.array(dtype=wp.int32),
                   kc: DCConst, n: int, kdir: int, cw: int, want_scale: int, sb: int,
                   extract: int, p0: int, seed_k: wp.array(dtype=wp.int32), seed_c: wp.array(dtype=wp.int32),
                   e_off: wp.array(dtype=wp.int32), e_colour: wp.array(dtype=wp.int32), zt: wp.array(dtype=Any),
                   res: wp.array2d(dtype=wp.float64), tan: wp.array2d(dtype=wp.float64),
                   scale: wp.array(dtype=wp.float64), vals: wp.array3d(dtype=Any)):
    """Cell rows: sum over the cell's faces of (face flux) x (signed area), the cell terms, the bounded corrections,
    the k / eps sources and the constrained eps rows; the residual (chunk 0; ``sb = 1``: every chunk, the residual of
    step j in row j), and this chunk's tangents (directions) or -- ``extract`` -- the compact-Jacobian block columns
    of the probes they seed."""
    i, j = wp.tid()
    ci = cpd_of[i]
    row = j * sb
    z = zt[0]
    rc = z
    r0 = z
    r1 = z
    r2 = z
    rt = z
    rk = z
    re = z
    dphi = z
    a0 = wp.float64(0.0)
    a1 = wp.float64(0.0)
    a2 = wp.float64(0.0)
    a3 = wp.float64(0.0)
    a4 = wp.float64(0.0)
    a5 = wp.float64(0.0)
    a6 = wp.float64(0.0)
    for k in range(off[i], off[i + 1]):
        s = sarea[k]
        f = face[k]
        fl = flux[j, f]
        mf = s * (ccont[f] * fl)
        m0 = s * fmom[j, f, 0]
        m1 = s * fmom[j, f, 1]
        m2 = s * fmom[j, f, 2]
        mt = s * fsc[j, f, 0]
        mk = s * fsc[j, f, 1]
        me = s * fsc[j, f, 2]
        rc = rc + mf
        r0 = r0 + m0
        r1 = r1 + m1
        r2 = r2 + m2
        rt = rt + mt
        rk = rk + mk
        re = re + me
        dphi = dphi + fl * s
        if want_scale == 1:
            a0 = a0 + wp.abs(m0[0])
            a1 = a1 + wp.abs(m1[0])
            a2 = a2 + wp.abs(m2[0])
            a3 = a3 + wp.abs(mf[0])
            a4 = a4 + wp.abs(mt[0])
            a5 = a5 + wp.abs(mk[0])
            a6 = a6 + wp.abs(me[0])
    v = vol[i]
    tc = tcoef[i]
    pc = pcoef[i]
    gb = ghbc[i]
    ndphi = -dphi
    # momentum: time term, sources, buoyancy, bounded correction, cell pressure gradient
    t0 = tc * q[j, ci, 0] + (grad[j, ci, PC, 0] * pc) * v - (grad[j, ci, TC, 0] * gb) * v
    t1 = tc * q[j, ci, 1] + (grad[j, ci, PC, 1] * pc) * v - (grad[j, ci, TC, 1] * gb) * v
    t2 = tc * q[j, ci, 2] + (grad[j, ci, PC, 2] * pc) * v - (grad[j, ci, TC, 2] * gb) * v
    if kc.bnd_u == 1:
        t0 = t0 + dmul(ndphi, q[j, ci, 0])
        t1 = t1 + dmul(ndphi, q[j, ci, 1])
        t2 = t2 + dmul(ndphi, q[j, ci, 2])
    r0 = r0 + t0
    r1 = r1 + t1
    r2 = r2 + t2
    r0[0] = r0[0] - (tc * qold[row, i, 0] + src[i, 0])
    r1[0] = r1[0] - (tc * qold[row, i, 1] + src[i, 1])
    r2[0] = r2[0] - (tc * qold[row, i, 2] + src[i, 2])
    # energy
    tq = q[j, ci, TC]
    tt = tc * tq
    if kc.bnd_t == 1:
        tt = tt + dmul(ndphi, tq)
    rt = rt + tt
    rt[0] = rt[0] - (tc * qold[row, i, 4] + src[i, 3])
    # k and epsilon sources
    kq = q[j, ci, KC]
    eq = q[j, ci, EC]
    nutc = nut[j, ci]
    kf = dsmooth_floor(kq, kc.kmin, kc.kwidth)
    ef = dsmooth_floor(eq, kc.emin, kc.ewidth)
    gbynu = z
    trg = grad[j, ci, 0, 0] + grad[j, ci, 1, 1] + grad[j, ci, 2, 2]
    for a in range(3):
        for bb in range(3):
            gab = grad[j, ci, a, bb]
            gbynu = gbynu + dmul(gab + grad[j, ci, bb, a], gab)
    gbynu = gbynu - dmul(TWO_THIRDS * trg, trg)
    pk = dmul(nutc, gbynu)
    w0 = woff[i]
    w1 = woff[i + 1]
    if w1 > w0:
        # the wall-function production G0 (EddyViscosityModel.wall_function_production), corner mean
        sqk = dsqrt(kf)
        acc = z
        for m in range(w0, w1):
            fw = wface[m]
            gh = wghost[m]
            dl = wdelta[m]
            du0 = (q[j, gh, 0] - q[j, ci, 0]) * dl
            du1 = (q[j, gh, 1] - q[j, ci, 1]) * dl
            du2 = (q[j, gh, 2] - q[j, ci, 2]) * dl
            m2s = dmul(du0, du0) + dmul(du1, du1) + dmul(du2, du2)
            m2s[0] = m2s[0] + kc.eps2
            mag = dsqrt(m2s)
            nuw = nutf[j, fw]
            nuw[0] = nuw[0] + nuf[fw]
            g0 = dmul(dmul(nuw, mag) * kc.cmu25, sqk) / wky[m]
            acc = acc + g0
        pk = acc / wp.float64(w1 - w0)
    srck = pk - dmul(ddiv(ef, kf), kq)
    if kc.gb == 1:
        gv = gvec[i]
        gdt = grad[j, ci, TC, 0] * gv[0] + grad[j, ci, TC, 1] * gv[1] + grad[j, ci, TC, 2] * gv[2]
        srck = srck + dmul((nutc * kc.cg) * betac[i], gdt)
    srce = z
    if kc.model == 0:
        srce = dmul(kf * kc.c1cmu, gbynu) - dmul(ddiv(ef, kf) * kc.c2, eq)
    else:
        s2, sss, om2 = strain_inv(grad, j, ci)
        ms = s2
        ms[0] = s2[0] + kc.s2eps2
        mag_s = dsqrt(ms)
        eta = ddiv(dmul(mag_s, kf), ef)
        e5 = eta
        e5[0] = wp.float64(5.0) + eta[0]
        ra = ddiv(eta, e5)
        c1 = dset(z, wp.float64(0.43))
        if ra[0] > wp.float64(0.43):
            c1 = ra
        sq = dsqrt(ef * nucell[i])
        den = kf + sq
        srce = dmul(dmul(c1, mag_s), ef) - dmul(ddiv(ef * kc.c2, den), eq)
    tk = tc * kq - srck * v
    te = tc * eq - srce * v
    if kc.bnd_k == 1:
        tk = tk + dmul(ndphi, kq)
    if kc.bnd_e == 1:
        te = te + dmul(ndphi, eq)
    rk = rk + tk
    rk[0] = rk[0] - tc * qold[row, i, 5]
    re = re + te
    re[0] = re[0] - tc * qold[row, i, 6]
    form = cform[i]
    if form != 0:
        # epsilonWallFunction: the row replaced by eps_c - mean(wall ghosts) (BaseSolver._apply_cell_constraints)
        tg = z
        for m in range(coff[i], coff[i + 1]):
            tg = tg + q[j, cghost[m], EC]
        tg = tg / wp.float64(coff[i + 1] - coff[i])
        if form == 1:
            re = eq - tg
        else:
            re = ddiv(eq, tg)
            re[0] = re[0] - wp.float64(1.0)
    if j == 0 or sb == 1:
        res[row, 0 * n + i] = r0[0]
        res[row, 1 * n + i] = r1[0]
        res[row, 2 * n + i] = r2[0]
        res[row, 3 * n + i] = rc[0]
        res[row, 4 * n + i] = rt[0]
        res[row, 5 * n + i] = rk[0]
        res[row, 6 * n + i] = re[0]
    if j == 0:
        if want_scale == 1:
            scale[0 * n + i] = a0 + wp.abs(t0[0]) + wp.abs(src[i, 0])
            scale[1 * n + i] = a1 + wp.abs(t1[0]) + wp.abs(src[i, 1])
            scale[2 * n + i] = a2 + wp.abs(t2[0]) + wp.abs(src[i, 2])
            scale[3 * n + i] = a3
            scale[4 * n + i] = a4 + wp.abs(tt[0]) + wp.abs(src[i, 3])
            scale[5 * n + i] = a5 + wp.abs(tk[0])
            scale[6 * n + i] = a6 + wp.abs(te[0])
            if form != 0:
                scale[6 * n + i] = wp.abs(eq[0]) + wp.abs(re[0] - eq[0])
    for s in range(cw):
        kk = j * cw + s
        if kk < kdir:
            if extract == 1:
                p = p0 + kk
                kcol = seed_k[p]
                cf = seed_c[p]
                for e in range(e_off[i], e_off[i + 1]):
                    if e_colour[e] == kcol:
                        vals[e, 0, cf] = vals.dtype(r0[s + 1])
                        vals[e, 1, cf] = vals.dtype(r1[s + 1])
                        vals[e, 2, cf] = vals.dtype(r2[s + 1])
                        vals[e, 3, cf] = vals.dtype(rc[s + 1])
                        vals[e, 4, cf] = vals.dtype(rt[s + 1])
                        vals[e, 5, cf] = vals.dtype(rk[s + 1])
                        vals[e, 6, cf] = vals.dtype(re[s + 1])
            else:
                tan[kk, 0 * n + i] = r0[s + 1]
                tan[kk, 1 * n + i] = r1[s + 1]
                tan[kk, 2 * n + i] = r2[s + 1]
                tan[kk, 3 * n + i] = rc[s + 1]
                tan[kk, 4 * n + i] = rt[s + 1]
                tan[kk, 5 * n + i] = rk[s + 1]
                tan[kk, 6 * n + i] = re[s + 1]


# ------------------------------------------------------------------------------------------------ overloads
def _declare_overloads(cw: int):
    D = dual_type(cw)
    a1, a2, a3, a4 = wp.array(dtype=D), wp.array2d(dtype=D), wp.array3d(dtype=D), wp.array4d(dtype=D)
    wp.overload(dc_load_kernel, {"zt": a1, "q": a3})
    wp.overload(dc_porous_kernel, {"q": a3})
    wp.overload(dc_fro_est_kernel, {"zt": a1, "q": a3, "fest": a2})
    wp.overload(dc_fro_write_kernel, {"fest": a2, "q": a3})
    wp.overload(dc_rack_mean_kernel, {"zt": a1, "q": a3, "rmean": a2})
    wp.overload(dc_rack_write_kernel, {"rmean": a2, "q": a3})
    wp.overload(dc_rack_sums_kernel, {"zt": a1, "q": a3, "racc": a2, "rden": a2})
    wp.overload(dc_rack_div_kernel, {"racc": a2, "rden": a2, "rmean": a2})
    wp.overload(dc_wlsq_kernel, {"q": a3, "zt": a1, "grad": a4})
    wp.overload(dc_nut_kernel, {"q": a3, "grad": a4, "zt": a1, "nut": a2})
    wp.overload(dc_face_pre_kernel, {"q": a3, "grad": a4, "nut": a2, "zt": a1, "f0": a2, "nutf": a2})
    wp.overload(dc_ap_kernel, {"f0": a2, "nutf": a2, "zt": a1, "dcell": a2})
    wp.overload(dc_face_kernel, {"q": a3, "grad": a4, "f0": a2, "dcell": a2, "nutf": a2, "zt": a1, "flux_out": a2,
                                 "fmom": a3, "fsc": a3})
    for vt in (wp.float64, wp.float32):        # the compact blocks in float64, or float32 (LPCNS_WARP_DC_VALS32)
        wp.overload(dc_cell_kernel, {"q": a3, "grad": a4, "nut": a2, "nutf": a2, "flux": a2, "fmom": a3, "fsc": a3,
                                     "zt": a1, "vals": wp.array3d(dtype=vt)})


def probe_chunk() -> int:
    """Tangents per chunk of a batched launch (LPCNS_WARP_DC_CHUNK, default 8, picoext_komega_warp's W4a optimum)."""
    return int(os.environ.get("LPCNS_WARP_DC_CHUNK", "8"))


def chunk_widths():
    ws = {0, 1, probe_chunk()}
    for tok in os.environ.get("LPCNS_WARP_DC_CHUNKS", "").split(","):
        if tok.strip():
            ws.add(int(tok))
    return sorted(ws)


for _cw in chunk_widths():
    _declare_overloads(_cw)


# ------------------------------------------------------------------------------------------------ host side
def _i32(t):
    t = t.to(dtype=torch.int32).contiguous().reshape(-1)
    return wp.from_torch(t if t.numel() else torch.zeros(1, dtype=torch.int32, device=t.device))


def _f64(t):
    t = t.to(dtype=torch.float64).contiguous().reshape(-1)
    return wp.from_torch(t if t.numel() else torch.zeros(1, dtype=torch.float64, device=t.device))


def _v3(t):
    t = t.to(dtype=torch.float64).reshape(-1, 3).contiguous()
    return wp.from_torch(t if t.numel() else torch.zeros(1, 3, dtype=torch.float64, device=t.device), dtype=wp.vec3d)


def _csr(rows, n):
    order = torch.argsort(rows, stable=True)
    counts = torch.bincount(rows, minlength=n)
    off = torch.zeros(n + 1, dtype=torch.int64, device=rows.device)
    off[1:] = torch.cumsum(counts, 0)
    return order, off


def _stamp(t):
    return (None, -1) if t is None else (t, t._version)


def parse_scheme(name: str, what: str, vector: bool):
    """``(code, bounded, k)`` of a divSchemes entry (the torch convective_flux / div_phic_to_faces spelling)."""
    s = str(name).strip()
    bounded = s.lower().startswith("bounded")
    if bounded:
        parts = s.split(None, 1)
        s = parts[1].strip() if len(parts) > 1 else "Linear"
    if s == "Linear":
        return S_LINEAR, bounded, 1.0
    if s == "Linear corrected":
        return S_LINEAR_CORR, bounded, 1.0
    if s == "Upwind":
        return S_UPWIND, bounded, 1.0
    if s == "LinearUpwind":
        return S_LUPWIND, bounded, 1.0
    if not vector and s.startswith("LimitedLinear") and not s.startswith("LimitedLinearV"):
        parts = s.split()
        if len(parts) != 2:
            raise NotImplementedError(f"warp backend (DC RANS): {what} scheme {name!r}")
        return S_LIMITED, bounded, max(float(parts[1]), SMALL)
    raise NotImplementedError(f"warp backend (DC RANS): {what} convection scheme {name!r} (supported: [bounded] Upwind, "
                              "LinearUpwind, Linear corrected, Linear" + ("" if vector else ", LimitedLinear k") + ")")


# boundary conditions whose cell-side write is affine in the owner / partner value (probed) or evaluated by a dedicated
# kernel (epsilonWallFunction, rackOutletTemperature, porousBafflePressure), and whose face-side writes are either a
# zero advecting flux (Symmetry / Slip velocity) or land on a face the compact snGrad overrides
_PAIR_STREAMS: dict = {}           # {torch.device: (Warp stream, the same stream for torch)}, never released
ALLOWED_BCS = {
    "VELOCITY": {"Noslip", "Fixedvalue", "Uniformfixedvalue", "Zerogradient", "Cyclic", "Symmetry", "Slip",
                 "Flowrateinletvelocity", "Flowrateoutletvelocity", "Rackinletvelocity", "Rackoutletvelocity"},
    "PRESSURE": {"Fixedvalue", "Zerogradient", "Fixedfluxpressure", "Cyclic", "Symmetry", "Porousbafflepressure"},
    "PRESSURE_RGH": {"Fixedvalue", "Zerogradient", "Fixedfluxpressure", "Cyclic", "Symmetry", "Porousbafflepressure"},
    "TEMPERATURE": {"Fixedvalue", "Zerogradient", "Cyclic", "Symmetry", "Rackoutlettemperature"},
    "TURB_K": {"Fixedvalue", "Zerogradient", "Cyclic", "Symmetry", "Kqrwallfunction"},
    "TURB_EPSILON": {"Fixedvalue", "Zerogradient", "Cyclic", "Symmetry", "Epsilonwallfunction"},
}


@wp.kernel
def _dc_jp_product_greedy_step(
    ptr_a: wp.array(dtype=wp.int32),
    col_a: wp.array(dtype=wp.int32),
    ptr_b: wp.array(dtype=wp.int32),
    col_b: wp.array(dtype=wp.int32),
    prio: wp.array(dtype=wp.int64),
    colour_in: wp.array(dtype=wp.int32),
    colour_out: wp.array(dtype=wp.int32),
    remaining: wp.array(dtype=wp.int32),
):
    u = wp.tid()
    c_u = colour_in[u]
    if c_u >= 0:
        colour_out[u] = c_u
        return
    pu = prio[u]
    m0 = wp.uint64(0)
    m1 = wp.uint64(0)
    m2 = wp.uint64(0)
    m3 = wp.uint64(0)
    one = wp.uint64(1)
    p0 = ptr_a[u]
    p1 = ptr_a[u + 1]
    for p in range(p0, p1):
        r = col_a[p]
        q0 = ptr_b[r]
        q1 = ptr_b[r + 1]
        for q in range(q0, q1):
            v = col_b[q]
            if v != u:
                cv = colour_in[v]
                if cv < 0:
                    if prio[v] > pu:
                        colour_out[u] = -1
                        wp.atomic_add(remaining, 0, 1)
                        return
                else:
                    if cv < 64:
                        m0 = m0 | (one << wp.uint64(cv))
                    elif cv < 128:
                        m1 = m1 | (one << wp.uint64(cv - 64))
                    elif cv < 192:
                        m2 = m2 | (one << wp.uint64(cv - 128))
                    elif cv < 256:
                        m3 = m3 | (one << wp.uint64(cv - 192))
    chosen = int(256)
    for b in range(64):
        if (m0 & (one << wp.uint64(b))) == wp.uint64(0):
            chosen = b
            break
    if chosen == 256:
        for b in range(64):
            if (m1 & (one << wp.uint64(b))) == wp.uint64(0):
                chosen = 64 + b
                break
    if chosen == 256:
        for b in range(64):
            if (m2 & (one << wp.uint64(b))) == wp.uint64(0):
                chosen = 128 + b
                break
    if chosen == 256:
        for b in range(64):
            if (m3 & (one << wp.uint64(b))) == wp.uint64(0):
                chosen = 192 + b
                break
    colour_out[u] = chosen


class CompactJacobian:
    """The compact (1-hop) Jacobian blocks of one assembly.

    ``A`` is a ``block_amg.BlockCSR`` over the interior cells (``row``, ``col`` sorted by row then column, ``val``
    ``[nnzb, 7, 7]`` float64 with ``val[e, row field, column field]``), field order ``(u, v, w, p_rgh, T, k, eps)`` --
    the order of the Newton unknowns' blocks.  ``diag`` is the index of each row's diagonal block.  ``field_csr()``
    returns the same matrix as a field-blocked scalar CSR (row ``f * n + i``), the layout ``jacobian.assemble`` returns
    as ``"J"``; ``blocks()`` the dict ``assemble`` returns (``J, F, G, D, C, R``)."""

    def __init__(self, A, diag, n, mode, colours, probes, seconds):
        self.A, self.diag, self.n = A, diag, int(n)
        self.mode, self.colours, self.probes, self.seconds = mode, int(colours), int(probes), float(seconds)

    def diagonal_vector(self) -> torch.Tensor:
        """Signed diagonal of ``A`` in field-blocked layout ``[7 N]`` (``d[f * n + i] = A_ii[f, f]``)."""
        F = self.A.val.shape[1]
        idx = torch.arange(F, device=self.A.val.device)
        return self.A.val[self.diag[:, None], idx[None, :], idx[None, :]].T.reshape(-1).contiguous()

    def add_diagonal(self, d: torch.Tensor) -> "CompactJacobian":
        """Add ``diag(d)`` (a field-blocked ``[7 N]`` vector, e.g. a pseudo-time ``V / dt`` per row) to the diagonal
        blocks in place; the blocks hold F's Jacobian only (no pin, PTC shift or time term of a driver)."""
        F = self.A.val.shape[1]
        dd = d.reshape(F, self.n).T.to(self.A.val.dtype)
        idx = torch.arange(F, device=dd.device)
        self.A.val[self.diag[:, None], idx[None, :], idx[None, :]] += dd
        return self

    def ilu0(self):
        """``SparseSolver.block_ilu.BlockILU0`` factored on these blocks; ``.apply_field_blocked(v)`` applies it."""
        from SparseSolver.block_ilu import BlockILU0
        A = self.A
        plan = getattr(self, "_ilu_plan", None)
        if plan is None:
            plan = self._ilu_plan = BlockILU0(A.row.cpu().numpy(), A.col.cpu().numpy(), self.n, A.val.shape[1],
                                              A.val.device)
        return plan.factor(A.val)

    def field_csr(self) -> torch.Tensor:
        A, n = self.A, self.n
        F = A.val.shape[1]
        dev = A.val.device
        f = torch.arange(F, device=dev)
        rows = (f[None, :, None] * n + A.row[:, None, None]).expand(-1, F, F)
        cols = (f[None, None, :] * n + A.col[:, None, None]).expand(-1, F, F)
        val = A.val
        keep = val != 0
        coo = torch.sparse_coo_tensor(torch.stack([rows[keep], cols[keep]]), val[keep], size=(n * F, n * F))
        return coo.coalesce().to_sparse_csr()

    def blocks(self) -> dict:
        J = self.field_csr()
        n = self.n
        u, p = 3 * n, 4 * n
        d = J.to_sparse_coo().coalesce()
        i, v = d.indices(), d.values()

        def cut(r0, r1, c0, c1):
            m = (i[0] >= r0) & (i[0] < r1) & (i[1] >= c0) & (i[1] < c1)
            return torch.sparse_coo_tensor(torch.stack([i[0][m] - r0, i[1][m] - c0]), v[m],
                                           size=(r1 - r0, c1 - c0)).coalesce().to_sparse_csr()
        N = J.shape[0]
        return {"J": J, "F": cut(0, u, 0, u), "G": cut(0, u, u, p), "D": cut(u, p, 0, u), "C": cut(u, p, u, p),
                "R": cut(p, N, p, N)}


class DCWarpResidual:
    """The Warp residual of one ``CollocatedSolver`` over a ``buoyantBoussinesqpicoext`` + ``kEpsilon`` /
    ``realizableKE`` case.

    API (all vectors are field-blocked ``[7 N]`` float64 on the case's device, the Newton unknown's layout):

    * ``residual(x)`` -> ``F(x)``: the rows of ``CaseState.residual`` before the operator correction, the pin, the PTC
      shift and the FAS right-hand side;
    * ``jvp(x, D, frozen=False)`` -> ``(F(x), J D^T)`` for a ``[K, 7N]`` block of directions (``frozen``: d_f frozen);
    * ``compact_jacobian(x, mode="frozen" | "picard", drop_global=True)`` -> ``CompactJacobian`` (module docstring);
    * ``residual_and_scale(x)`` -> ``(F, per-row sum of |terms|)`` (a diagnostic launch);
    * ``face_volumetric_flux``, ``nu_eff_face``, ``alpha_eff_face``: ``[N_face, 1]`` of the last chunk-0 launch.

    Built once per case: geometry, WLSQ weights, ghost maps, wall tables and constants are read off the torch
    application after one forward; the coefficients the entry replaces between solves (nu, alpha -- every continuation
    rung --, theta_PDE, gravity, beta, sources, the time coefficient) are re-read on every call when they changed."""

    def __init__(self, cs):
        # multi-GPU (opt-in): the patch-sum all-reduce of a partitioned part, None otherwise (Parallel.part)
        self._preduce = (getattr(cs.solver, "_partition", None) or {}).get("reduce")
        # the construction runs torch forwards (the caches the extraction reads, the ghost probes) and a check on a
        # random state; none of them may seed a lagged CBBM mean the first real residual evaluation should seed
        lag = [bc for group in (getattr(cs.solver.bcpatch_batch, "coupled", None) or []) for bc in group
               if type(bc).__name__ == "Rackoutlettemperature" and getattr(bc, "lag_mean", False)]
        saved = [bc._lagged_mean for bc in lag]
        try:
            self._init(cs)
        finally:
            for bc, v in zip(lag, saved):
                bc._lagged_mean = v
            self._lag_values = [None] * len(getattr(self, "_lagged", ()))

    def _init(self, cs):
        self.cs = cs
        app = cs.solver
        self.app = app
        self.dev = wp.device_from_torch(cs.device)
        self._refuse_unsupported(app)
        self.n = n = int(cs.n_cell)
        if int(cs.num_fields) != 7:
            raise NotImplementedError(f"warp backend (DC RANS): {cs.num_fields} fields, expected 7")
        x0 = torch.zeros(n * 7, dtype=torch.float64, device=cs.device)
        bypass = getattr(cs, "_warp_bypass", False)
        cs._warp_bypass = True
        try:
            cs.residual(x0)
        finally:
            cs._warp_bypass = bypass
        tdev = cs.device
        self.cpd_of = cs.interior.to(torch.int64)
        self.n_cpd = int(app.num_cpd_cells)
        self._row_of_cpd = torch.full((self.n_cpd,), -1, dtype=torch.int64, device=tdev)
        self._row_of_cpd[self.cpd_of] = torch.arange(n, device=tdev)
        self._w_cpd = _i32(self.cpd_of)
        self._build_constants(app)
        self._build_geometry(app, tdev)
        self._build_wlsq(app, tdev)
        self._build_cells(app, tdev)
        self._build_gauss(app, tdev)
        self._build_ghosts(app, tdev)
        self._build_wall(app, tdev)
        self._build_coefficients(app, tdev)
        self.face_volumetric_flux = torch.zeros(self.n_face, 1, dtype=torch.float64, device=tdev)
        self.nu_eff_face = torch.zeros(self.n_face, 1, dtype=torch.float64, device=tdev)
        self.alpha_eff_face = torch.zeros(self.n_face, 1, dtype=torch.float64, device=tdev)
        self._scale = torch.zeros(n * 7, dtype=torch.float64, device=tdev)
        self._bufs = {}
        self._streams = {}
        self._compact = {}
        self._check_ghosts()

    # ------------------------------------------------------------------ refusal
    def _refuse_unsupported(self, app):
        from FVsolver.Turbulence.kepsilon import KEpsilon
        try:
            from FVsolver.Turbulence.realizable_ke import RealizableKE
        except ImportError:                                    # pragma: no cover (the module is new)
            RealizableKE = None
        fvs = app.params.fvschemes
        if not (hasattr(app, "alpha_coeff") and hasattr(app, "gh_face")):
            raise NotImplementedError("warp backend (DC RANS): a buoyantBoussinesqpicoext case only")
        tm = app.turbulence
        if type(tm) is KEpsilon:
            self.model = 0
        elif RealizableKE is not None and type(tm) is RealizableKE:
            self.model = 1
        else:
            raise NotImplementedError(f"warp backend (DC RANS): turbulence model {type(tm).__name__} (kEpsilon, "
                                      "realizableKE)")
        if tuple(e[0] for e in app._turb_transported) != ("k", "epsilon"):
            raise NotImplementedError("warp backend (DC RANS): transported fields other than (k, epsilon)")
        want = {"gradSchemes": "1st", "interpolationSchemes": "Linear corrected", "snGradSchemes": "corrected"}
        for key, val in want.items():
            if str(fvs.get(key, "")) != val:
                raise NotImplementedError(f"warp backend (DC RANS): fvSchemes {key} = {fvs.get(key)!r}, supported "
                                          f"{val!r}" + (" (the buoyant forward does not route \"GG\" through its unified "
                                                        "gradient)" if key == "gradSchemes" else ""))
        gis = str(fvs.get("gradInterpolationSchemes", fvs.get("interpolationSchemes")))
        if gis not in ("Linear", "Linear overRelaxed"):
            raise NotImplementedError(f"warp backend (DC RANS): gradInterpolationSchemes = {gis!r}")
        self.grad_interp = gis
        khops = int((app.params.fvconfig.get("stencil", {}) or {}).get("khops", 1))
        if khops != 1:
            raise NotImplementedError(f"warp backend (DC RANS): [stencil] khops = {khops} (the 1-hop WLSQ stencil only)")
        if not app._momentum_interpolation_mwi or not app._momentum_residual_cell or app._momentum_residual_face:
            raise NotImplementedError("warp backend: momentumResidual = cell with momentumInterpolation = MWI only")
        if not app._diffusion_sngrad_face or getattr(app, "_cyclic_diffusion_sngrad", False):
            raise NotImplementedError("warp backend: boundaryDiffusionScheme = snGrad, cyclic faces face-gradient only")
        for flag in ("_stash_face_fluxes",):
            if getattr(app, flag, False):
                raise NotImplementedError(f"warp backend (DC RANS): {flag} is set")
        if getattr(app, "limiter_smoothing", None) is not None:
            raise NotImplementedError("warp backend (DC RANS): limiterSmoothing (the smooth LimitedLinear)")
        if (fvs.get("gradLimiter", {}) or {}).get("enabled", False):
            raise NotImplementedError("warp backend (DC RANS): gradLimiter")
        if getattr(app, "_mms_source_path", None) or getattr(app, "_mms_turb_source", None) is not None:
            raise NotImplementedError("warp backend (DC RANS): mmsSource")
        if getattr(app, "_cht_regions_path", None) or getattr(app, "_buoyancy_cell", None) is not None:
            raise NotImplementedError("warp backend (DC RANS): multi-region / CHT cases")
        if int(getattr(app, "dim", 3)) != 3:
            raise NotImplementedError("warp backend (DC RANS): dim = 2")
        empty = getattr(app, "mask_empty_face", None)
        if empty is not None and bool(empty.any()):
            raise NotImplementedError("warp backend (DC RANS): empty faces (3D cases only)")
        if getattr(app, "loss_checker_weight", 0) > 0:
            raise NotImplementedError("warp backend: checkerboard loss")
        self._refuse_dynamic(app)
        if tm.wallBlender != "stepwise":
            raise NotImplementedError(f"warp backend (DC RANS): nutkWallFunction blender {tm.wallBlender!r} (stepwise)")
        if self.model == 1 and float(tm.C1Sharpness) != 0.0:
            raise NotImplementedError("warp backend (DC RANS): realizableKE C1Sharpness > 0 (the smooth C1 max)")
        batch = app.bcpatch_batch
        for group in ("first", "mixed", "cyclic", "empty", "coupled"):
            for bcs in (getattr(batch, group, None) or []):
                for bc in bcs:
                    vt = getattr(getattr(bc, "varType", None), "name", None)
                    name = type(bc).__name__
                    if vt not in ALLOWED_BCS or name not in ALLOWED_BCS[vt]:
                        raise NotImplementedError(f"warp backend (DC RANS): boundary condition {name} on {vt} (patch "
                                                  f"{getattr(bc, 'patch_name', '?')})")

    def _refuse_dynamic(self, app):
        if getattr(app, "_conv_theta", 1.0) != 1.0 or getattr(app, "_ap_mode", "default") != "default" \
                or getattr(app, "_ap_unsteady", False):
            raise NotImplementedError("warp backend (DC RANS): implicit convection and the default a_P only")
        if getattr(app, "_conv_mode", "implicit") != "implicit" or getattr(app, "_explicit_source", None) is not None:
            raise NotImplementedError("warp backend (DC RANS): implicit convection only, no explicit source")
        if getattr(app, "_bc_old_uvw", None) is not None or getattr(app, "_bc_old_state", None) is not None:
            raise NotImplementedError("warp backend (DC RANS): an armed boundary-condition old level")

    # ------------------------------------------------------------------ constants
    def _build_constants(self, app):
        tm = app.turbulence
        c = DCConst()
        c.kmin, c.kwidth = float(tm.kMin), float(tm.kMinWidth)
        c.emin, c.ewidth = float(tm.epsilonMin), float(tm.epsilonMinWidth)
        c.sigk, c.sige = float(tm.sigmak), float(tm.sigmaEps)
        c.c2 = float(tm.C2)
        c.cg = float(tm.Cg)
        if self.model == 0:
            c.cmu = float(tm.Cmu)
            c.c1cmu = float(tm.C1) * float(tm.Cmu)
            c.gb = 1
        else:
            c.cmu = 0.0
            c.c1cmu = 0.0
            c.gb = 1 if float(tm.Cg) != 0.0 else 0
            c.a0 = float(tm.A0)
            c.s2eps2 = float(tm.s2SqrtEps) * float(tm.s2SqrtEps)
            c.wsmall = float(tm.rCmuWSmall)
            c.acos_b = 1.0 - float(tm.rCmuAcosEps)
        c.cmu25 = float(tm.wallCmu) ** 0.25
        c.wkappa = float(tm.wallKappa)
        c.wE = float(tm.wallE)
        c.ypl = float(tm.wallYPlusLam)
        c.eps2 = float(tm.wallGradUEps) * float(tm.wallGradUEps)
        c.prt = float(app.Prt)
        c.jaya = 1 if (self.model == 1 and str(getattr(tm, "wallAlphat", "nutByPrt")) == "jayatilleke") else 0
        if c.jaya:
            c.jprt, c.jkappa, c.jE = float(tm.wallAlphatPrt), float(tm.wallAlphatKappa), float(tm.wallAlphatE)
        su, bu, _ = parse_scheme(app.U_scheme, "U", vector=True)
        st, bt, kt = parse_scheme(app.T_scheme, "T", vector=False)
        sk, bk, kk = parse_scheme(app._turb_schemes["k"], "k", vector=False)
        se, be, ke = parse_scheme(app._turb_schemes["epsilon"], "epsilon", vector=False)
        c.scheme_u, c.scheme_t, c.scheme_k, c.scheme_e = int(su), int(st), int(sk), int(se)
        c.bnd_u, c.bnd_t, c.bnd_k, c.bnd_e = int(bu), int(bt), int(bk), int(be)
        c.kk_t, c.kk_k, c.kk_e = float(kt), float(kk), float(ke)
        c.model = int(self.model)
        self.const = c

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
        _skew = os.environ.get("LPCNS_WARP_DC_SKEW", "1")
        if _skew == "0":
            # diagnostics: no skewness correction of the face interpolation (OpenFOAM's plain `linear`)
            rcorr = torch.zeros_like(rcorr)
        ops = {}
        for key, vt in (("u", VARType.VELOCITY), ("p", VARType.PRESSURE_RGH), ("t", VARType.TEMPERATURE),
                        ("k", VARType.TURB_K), ("e", VARType.TURB_EPSILON)):
            ow, nw, cv = app._snGrad_operands(vt)
            ops[key] = (ow.reshape(-1), nw.reshape(-1), cv.reshape(-1, 3))
        if self.grad_interp == "Linear overRelaxed":
            overrelax = ((app.face_type != FT.CYCLIC) & (app.face_type != FT.EMPTY)).reshape(-1).to(torch.int32)
        else:
            overrelax = torch.zeros(nfc, dtype=torch.int32, device=tdev)
        mwi = app._mwi_face_mask().reshape(-1).to(torch.int32)
        if getattr(app, "_mwi_dirichlet_pressure", False):
            from FVdomain.SetBC.onthefly.BCbase import VARType as _VT
            dmask = app.dirichlet_boundary_face_mask(_VT.PRESSURE_RGH).reshape(-1)
            mwi = torch.where(dmask & (mwi == 0), torch.full_like(mwi, 2), mwi)
        bcdiff = app._bc_diffusion_face_mask().reshape(-1).to(torch.int32)
        bnc = app.mask_boundary_non_cyclic_face.reshape(-1)
        dev2own = bnc
        zeroflux = self._zero_flux_faces(app, nfc, tdev)
        g = DCFaceGeom()
        g.fo, g.fn = _i32(C), _i32(F)
        g.gC, g.gF = _f64(gC), _f64(gF)
        # The bracket of the momentum-weighted flux is delta (grad p_f . d - (p_N - p_P)) with grad p_f interpolated
        # from the two cell gradients.  With the linear weights a thin cell next to a large one carries almost all of
        # the weight, and the gradient of a thin wall cell is one-sided: it reproduces the compact difference, the
        # bracket loses its damping for that cell's pressure and lateral neighbours enter with the wrong sign (slab
        # hall, 2026-11-11).  LPCNS_WARP_DC_MWI_GRADW=volume weights the two gradients by cell volume on interior
        # faces instead: the gradient of the larger cell, which has a two-sided stencil, decides.  Any weights that
        # sum to one keep the bracket consistent (it vanishes for a linear pressure).
        bio, bin_ = gC.to(torch.float64).clone(), gF.to(torch.float64).clone()
        bow_p, bnw_p = ops["p"][0].to(torch.float64).clone(), ops["p"][1].to(torch.float64).clone()
        bow_t, bnw_t = ops["t"][0].to(torch.float64).clone(), ops["t"][1].to(torch.float64).clone()
        self.mwi_gradw = os.environ.get("LPCNS_WARP_DC_MWI_GRADW", "linear")
        if self.mwi_gradw == "volume":
            vol = torch.zeros(self.n_cpd, dtype=torch.float64, device=tdev)
            vol[self.cpd_of] = app.cell_volume.reshape(-1).to(torch.float64)
            inner = (self._row_of_cpd[C] >= 0) & (self._row_of_cpd[F] >= 0)
            wo = vol[C] / (vol[C] + vol[F]).clamp_min(1e-300)
            for a, b in ((bio, bin_), (bow_p, bnw_p), (bow_t, bnw_t)):
                a[inner] = wo[inner]
                b[inner] = 1.0 - wo[inner]
        elif self.mwi_gradw != "linear":
            raise ValueError(f"LPCNS_WARP_DC_MWI_GRADW = {self.mwi_gradw!r} (linear, volume)")
        # the same weights for the torch forward (BaseSolver._mwi_bracket_gradients), which the adjoint tapes
        app._mwi_bracket_volume = (inner, wo) if self.mwi_gradw == "volume" else None
        g.bgo, g.bgn = _f64(bio), _f64(bin_)
        g.bow_p, g.bnw_p, g.bow_t, g.bnw_t = _f64(bow_p), _f64(bnw_p), _f64(bow_t), _f64(bnw_t)
        g.nrm, g.rcorr, g.eCF = _v3(app.unv_face), _v3(rcorr), _v3(app.eCF)
        g.dCF = _f64(app.dCF.reshape(-1))
        g.nonorth = _f64(app.nonOrthDeltaCoeffs)
        g.rC, g.rF = _v3(app.Cf), _v3(app.Ff)
        g.dpos = _v3(xF - xC)
        g.ow_u, g.nw_u, g.cv_u = _f64(ops["u"][0]), _f64(ops["u"][1]), _v3(ops["u"][2])
        g.ow_p, g.nw_p, g.cv_p = _f64(ops["p"][0]), _f64(ops["p"][1]), _v3(ops["p"][2])
        g.ow_t, g.nw_t, g.cv_t = _f64(ops["t"][0]), _f64(ops["t"][1]), _v3(ops["t"][2])
        g.ow_k, g.nw_k, g.cv_k = _f64(ops["k"][0]), _f64(ops["k"][1]), _v3(ops["k"][2])
        g.ow_e, g.nw_e, g.cv_e = _f64(ops["e"][0]), _f64(ops["e"][1]), _v3(ops["e"][2])
        g.bnc = _i32(bnc.to(torch.int32))
        g.overrelax, g.mwi, g.bcdiff = _i32(overrelax), _i32(mwi), _i32(bcdiff)
        g.dev2own, g.zeroflux = _i32(dev2own.to(torch.int32)), _i32(zeroflux)
        if getattr(app, "_conservative_cyclic_flux", False):
            fa, fb = app._cyclic_face_pairs()
        else:
            fa = fb = torch.zeros(0, dtype=torch.long, device=tdev)
        self.n_pair = int(fa.numel())
        pairof = torch.full((nfc,), -1, dtype=torch.int64, device=tdev)
        pairof[fa], pairof[fb] = fb, fa
        g.pairof = _i32(pairof)
        g.area = _f64(app.face_area.reshape(-1))
        self.geom = g
        self._keep = (C, F, gC, gF, rcorr, ops, overrelax, mwi, bcdiff, bnc, zeroflux, xF - xC, pairof)

    @staticmethod
    def _zero_flux_faces(app, nfc, tdev):
        """Faces whose advecting flux a velocity boundary condition overwrites with zero (Symmetry, Slip)."""
        from FVdomain.SetBC.onthefly.BCbase import VARType
        out = torch.zeros(nfc, dtype=torch.int32, device=tdev)
        batch = app.bcpatch_batch
        for group in ("first", "mixed", "cyclic", "empty", "coupled"):
            lists = getattr(batch, group, None) or []
            offsets = getattr(batch, f"{group}_patch_idx_offset", None) or [0]
            for ci, bcs in enumerate(lists):
                for bc in bcs:
                    if getattr(bc, "varType", None) == VARType.VELOCITY and type(bc).__name__ in ("Symmetry", "Slip"):
                        gp = int(bc.patch_idx) + int(offsets[ci])
                        out[int(app.fvpatch_batch.start_idx_face[gp]):int(app.fvpatch_batch.end_idx_face[gp])] = 1
        return out

    # ------------------------------------------------------------------ WLSQ weights
    def _build_wlsq(self, app, tdev):
        A = app.A_cell_to_cell.to(torch.float64)
        Bone = app.single_B_cell_to_cell.to(torch.float64)
        edges = app.neighbor_cpd_cell_x.long()
        a, b = edges[0], edges[1]
        valid = app.mask_interior_cell
        Ad = A[:, :3, :3]
        eye = torch.eye(3, dtype=A.dtype, device=A.device).expand_as(Ad)
        Ainv = torch.linalg.inv_ex(torch.where(valid[:, None, None], Ad, eye))[0]
        Be = Bone[:, :3, 0]
        rows = torch.cat([b, a])
        other = torch.cat([a, b])
        coef = torch.cat([Be, -Be])
        w = torch.einsum("eij,ej->ei", Ainv[rows], coef)
        inv_map = torch.full((self.n_cpd,), -1, dtype=torch.int64, device=tdev)
        inv_map[self.cpd_of] = torch.arange(self.n, device=tdev)
        irow = inv_map[rows]
        keep = irow >= 0
        irow, other, w = irow[keep], other[keep], w[keep]
        order, off = _csr(irow, self.n)
        self.w_off = _i32(off)
        self.w_other = _i32(other[order])
        self.w_w = _v3(w[order])
        self._keep_w = (off, other[order], w[order])

    # ------------------------------------------------------------------ cells
    def _build_cells(self, app, tdev):
        keep = app._non_empty_cells_face_indices.long()
        rows = app.cells_face_ptr.long()[keep]
        faces = app.cells_face.long()[keep]
        sarea = app.directed_area_cells_face.reshape(-1)[keep].to(torch.float64)
        order, off = _csr(rows, self.n)
        faces, sarea = faces[order], sarea[order]
        self.c_off, self.c_face, self.c_sarea = _i32(off), _i32(faces), _f64(sarea)
        self.c_abss = _f64(sarea.abs())
        self.c_delta = _f64(app.nonOrthDeltaCoeffs.reshape(-1)[faces])
        self.c_vol = _f64(app.cell_volume.reshape(-1))
        # the volume in d_P = V_P / a_P only, scaled by the opt-in _mwi_d_scale (base_solver.momentum_weighted_flux)
        _d_scale = getattr(app, "_mwi_d_scale", None)
        self.c_dvol = self.c_vol if _d_scale is None else _f64(app.cell_volume.reshape(-1) * float(_d_scale))
        if not torch.equal(app._facecv_interior_ids.long(), self.cpd_of):
            raise RuntimeError("warp backend: the Newton cell order differs from the residual's interior order")
        self._keep_c = (off, faces, sarea)

    # ------------------------------------------------------------------ Green-Gauss weights
    def _build_gauss(self, app, tdev):
        """LPCNS_WARP_DC_GAUSS = field indices (0..6, comma separated; default none): those fields' cell gradient is
        Green-Gauss with the linear face value, grad_i = sum_f (q_f - q_i) S_f / V_i, q_f = gC q_owner + gF q_neighbour
        (the ghost on a boundary face) -- OpenFOAM's ``Gauss linear``.  Written in the WLSQ kernel's form: the face
        towards cell ``other`` contributes (weight of ``other`` in q_f) S_f / V_i times (q_other - q_i).  A face's
        contribution then involves that face's two cells only, whatever the neighbours' offsets."""
        fields = [int(v) for v in os.environ.get("LPCNS_WARP_DC_GAUSS", "").split(",") if v.strip() != ""]
        sel = torch.zeros(7, dtype=torch.int32, device=tdev)
        for f in fields:
            sel[f] = 1
        self.gauss_fields = tuple(fields)
        self.g_sel = _i32(sel)
        if not fields:
            self.g_off = _i32(torch.zeros(self.n + 1, dtype=torch.int64, device=tdev))
            self.g_other = _i32(torch.zeros(1, dtype=torch.int64, device=tdev))
            self.g_w = _v3(torch.zeros(1, 3, dtype=torch.float64, device=tdev))
            return
        off, faces, sarea = self._keep_c
        rows = torch.repeat_interleave(torch.arange(self.n, device=tdev), (off[1:] - off[:-1]).long())
        C, F = app.C_senders.long()[faces], app.F_receivers.long()[faces]
        own = C == self.cpd_of[rows]
        if not bool((own | (F == self.cpd_of[rows])).all()):
            raise RuntimeError("warp backend: a cell's face does not have the cell as owner or neighbour")
        other = torch.where(own, F, C)
        gC, gF = app.gC.reshape(-1).to(torch.float64)[faces], app.gF.reshape(-1).to(torch.float64)[faces]
        vol = app.cell_volume.reshape(-1).to(torch.float64)[rows]
        w = (torch.where(own, gF, gC) * sarea / vol)[:, None] * app.unv_face.to(torch.float64)[faces]
        self.g_off, self.g_other, self.g_w = _i32(off), _i32(other), _v3(w)
        self._keep_g_gauss = (off, other, w)
        # the weights reproduce a linear field's gradient on an orthogonal cell: report how far they are from it
        x = app.cpd_cell_pos.to(torch.float64)
        dx = x[other] - x[self.cpd_of[rows]]
        G = torch.zeros(self.n, 3, 3, dtype=torch.float64, device=tdev).index_add_(0, rows, w[:, :, None] * dx[:, None, :])
        err = (G - torch.eye(3, dtype=torch.float64, device=tdev)).abs().amax(dim=(1, 2))
        self.gauss_check = {"fields": fields, "median": float(err.median()), "p99": float(err.quantile(0.99) if
                            err.numel() < 16_000_000 else err.max()), "max": float(err.max())}
        print(f"warp backend: Green-Gauss gradient for fields {fields}; |grad(x) - I| median "
              f"{self.gauss_check['median']:.2e} p99 {self.gauss_check['p99']:.2e} max {self.gauss_check['max']:.2e}",
              flush=True)

    # ------------------------------------------------------------------ boundary ghosts
    def _bc_pass(self, app, state):
        """The torch forward's first cell-side boundary passes on a compound state [N_cpd, 7] (clone in, filled out)."""
        uvw = state[:, 0:3].clone()
        fv = {"uvw_cpd_cell": uvw, "p_rgh_cpd_cell": state[:, 3:4].clone(), "t_cpd_cell": state[:, 4:5].clone(),
              "k_cpd_cell": state[:, 5:6].clone(), "epsilon_cpd_cell": state[:, 6:7].clone(),
              "uvw_old_cpd_cell": torch.zeros_like(uvw)}
        fv = app.enforce_bc_1st(fv)
        fv = app.enforce_cyclic(fv)
        fv = app.enforce_mixed(fv)
        fv = app.enforce_coupled(fv)
        return torch.cat([fv["uvw_cpd_cell"], fv["p_rgh_cpd_cell"], fv["t_cpd_cell"], fv["k_cpd_cell"],
                          fv["epsilon_cpd_cell"]], dim=1)

    def _special_ghosts(self, app, tdev):
        """The ghosts whose value is not an affine map of their owner / partner alone: epsilonWallFunction (eps),
        rackOutletTemperature (T), porousBafflePressure (p_rgh).  Returns their tables and the (ghost, channel) rows
        the probed affine map must leave to them."""
        from FVdomain.SetBC.onthefly.setCyclic import get_cyclic_patch_pair_indices, _patch_scalar_to_int
        fvp = app.fvpatch_batch
        ncc = app.neighbor_cpd_cell
        batch = app.bcpatch_batch
        masked = []                       # (ghost cpd ids, channel)
        ew = {"ghost": [], "owner": [], "c75": [], "den": [], "kmin": [], "kw": []}
        self._ew_bcs = []                 # (ghost cpd ids, face ids, kappa) per epsilonWallFunction patch
        for gi_, bcs in enumerate(batch.first):
            off = batch.first_patch_idx_offset[gi_]
            for bc in bcs:
                if type(bc).__name__ != "Epsilonwallfunction":
                    continue
                gp = int(bc.patch_idx) + int(off)
                s_f, e_f = int(fvp.start_idx_face[gp]), int(fvp.end_idx_face[gp])
                s_c, e_c = int(fvp.start_idx_cpd_cell[gp]), int(fvp.end_idx_cpd_cell[gp])
                gh = torch.arange(s_c, e_c, device=tdev)
                y1 = app.wall_distance_face[s_f:e_f]
                ew["ghost"].append(gh)
                ew["owner"].append(ncc[0, s_f:e_f].long())
                ew["c75"].append(torch.full((e_c - s_c,), float(bc.Cmu) ** 0.75, dtype=torch.float64, device=tdev))
                ew["den"].append((bc.kappa * y1).reshape(-1).to(torch.float64))
                self._ew_bcs.append((gh, torch.arange(s_f, e_f, device=tdev), float(bc.kappa)))
                ew["kmin"].append(torch.full((e_c - s_c,), float(bc.kMin), dtype=torch.float64, device=tdev))
                ew["kw"].append(torch.full((e_c - s_c,), float(bc.kMinWidth), dtype=torch.float64, device=tdev))
                masked.append((gh, 6))
        fro = {"off": [0], "owner": [], "ghost": [], "n": [], "rhoa": [], "patch": [], "target": [], "asum": []}
        n_fro = 0
        for gi_, bcs in enumerate(batch.first):
            off = batch.first_patch_idx_offset[gi_]
            for bc in bcs:
                if type(bc).__name__ != "Flowrateoutletvelocity":
                    continue
                from FVdomain.SetBC.onthefly.Parafunction import Functionbase
                gp = int(bc.patch_idx) + int(off)
                s_f, e_f = int(fvp.start_idx_face[gp]), int(fvp.end_idx_face[gp])
                s_c, e_c = int(fvp.start_idx_cpd_cell[gp]), int(fvp.end_idx_cpd_cell[gp])
                if e_c - s_c != e_f - s_f:
                    raise RuntimeError("warp backend (DC RANS): a flowRateOutletVelocity patch whose ghosts do not "
                                       "follow its faces")
                spec, rho = ((bc.volumetricFlowRate, 1.0) if bc.volumetricFlowRate is not None
                             else (bc.massFlowRate, bc.rho))
                if spec is None:
                    raise ValueError("flowRateOutletVelocity needs volumetricFlowRate or massFlowRate")
                # the target exactly as the torch eval forms it (Functionbase's tensor, then promoted)
                tq = Functionbase(value_type=spec["value_type"], value=spec["val"], num_element=1,
                                  channel_dim=1).eval(device=tdev).squeeze()
                areas = app.face_area[s_f:e_f].to(torch.float64).reshape(-1, 1)
                rhoa = rho * areas
                fro["owner"].append(ncc[0, s_f:e_f].long())
                fro["ghost"].append(torch.arange(s_c, e_c, device=tdev))
                fro["n"].append(app.unv_face[s_f:e_f].to(torch.float64).reshape(-1, 3))
                fro["rhoa"].append(rhoa.reshape(-1))
                fro["patch"].append(torch.full((e_f - s_f,), n_fro, dtype=torch.int64, device=tdev))
                fro["target"].append(tq.to(torch.float64).reshape(1))
                fro["asum"].append(torch.sum(rho * areas, dim=-2, keepdim=True).reshape(1))
                fro["off"].append(fro["off"][-1] + e_f - s_f)
                n_fro += 1
                for ch in (0, 1, 2):
                    masked.append((torch.arange(s_c, e_c, device=tdev), ch))
        self._fro = fro
        self.n_fro = n_fro
        rk = {"src": [], "area": [], "off": [0], "asum": [], "ghost": [], "rack": [], "remote": [], "dt": [],
              "gh": [], "n": [], "flux": []}
        lagged = []                       # (condition, index of its ghost block)
        n_rack = 0
        for gi_, bcs in enumerate(batch.coupled):
            off = batch.coupled_patch_idx_offset[gi_]
            for bc in bcs:
                name = type(bc).__name__
                if name != "Rackoutlettemperature":
                    raise NotImplementedError(f"warp backend (DC RANS): coupled boundary condition {name}")
                if bc.model == "cbbm" and hasattr(bc, "_cbbm_indices"):
                    # no face pairing (the two patches may differ in face count): the discharge ghosts in this
                    # patch's order, the intake owners / areas in the partner's
                    s_ghost, t_owner, cb_areas = bc._cbbm_indices(app, off)
                else:
                    _so, s_ghost, t_owner, _tg, _perm = bc._pairing(app, off)
                    cb_areas = None
                s_ghost, t_owner = s_ghost.long().reshape(-1), t_owner.long().reshape(-1)
                weight = str(getattr(bc, "mean_weight", "area"))
                rk["ghost"].append(s_ghost)
                rk["dt"].append(torch.full((s_ghost.numel(),), float(bc.deltaT), dtype=torch.float64, device=tdev))
                if bc.model == "ibbm":
                    rk["remote"].append(t_owner)
                    rk["rack"].append(torch.zeros_like(s_ghost))
                elif bc.lag_mean:
                    lagged.append((bc, len(rk["ghost"]) - 1))
                    rk["remote"].append(torch.full_like(s_ghost, -2))
                    rk["rack"].append(torch.zeros_like(s_ghost))
                else:
                    areas = (bc._intake_areas(app, off) if cb_areas is None else cb_areas).to(torch.float64)
                    if weight not in ("area", "flux"):
                        raise NotImplementedError(f"warp backend (DC RANS): CBBM meanWeight {weight!r}")
                    gp_in = int(bc.partner_patch_idx) + int(off)
                    p0, p1 = int(fvp.start_idx_face[gp_in]), int(fvp.end_idx_face[gp_in])
                    if p1 - p0 != int(t_owner.numel()):
                        raise RuntimeError("warp backend (DC RANS): CBBM intake owners do not follow the intake faces")
                    rk["gh"].append(ncc[1, p0:p1].long())
                    rk["n"].append(app.unv_face[p0:p1].to(torch.float64).reshape(-1, 3))
                    rk["flux"].append(1 if weight == "flux" else 0)
                    rk["src"].append(t_owner)
                    rk["area"].append(areas.reshape(-1))
                    rk["off"].append(rk["off"][-1] + int(t_owner.numel()))
                    rk["asum"].append(areas.sum(dim=-2, keepdim=True).reshape(-1))
                    rk["remote"].append(torch.full_like(s_ghost, -1))
                    rk["rack"].append(torch.full_like(s_ghost, n_rack))
                    n_rack += 1
                masked.append((s_ghost, 4))
        pz = {"ghost": [], "src": [], "a": [], "b": [], "n": [], "sign": [], "c0": [], "c1": [], "len": []}
        pairing = getattr(fvp, "neighbor_patch_idx", None)
        if pairing is None:
            pairing = getattr(fvp, "neighbour_patch_idx", None)
        for gi_, bcs in enumerate(batch.cyclic):
            off = batch.cyclic_patch_idx_offset[gi_]
            for bc in bcs:
                if type(bc).__name__ != "Porousbafflepressure":
                    continue
                gp = int(bc.patch_idx) + int(off)
                partner_idx = _patch_scalar_to_int(pairing[gp])
                is_owner = gp < partner_idx
                own = gp if is_owner else partner_idx
                o_owner, _og, o_partner, _ = get_cyclic_patch_pair_indices(fvp, ncc, own)
                s0, s1 = _patch_scalar_to_int(fvp.start_idx_face[own]), _patch_scalar_to_int(fvp.end_idx_face[own])
                _so, ghost, t_owner, _ = get_cyclic_patch_pair_indices(fvp, ncc, gp)
                m = int(ghost.numel())
                pz["ghost"].append(ghost.long().reshape(-1))
                pz["src"].append(t_owner.long().reshape(-1))
                pz["a"].append(o_owner.long().reshape(-1))
                pz["b"].append(o_partner.long().reshape(-1))
                pz["n"].append(app.unv_face[s0:s1].to(torch.float64).reshape(-1, 3))
                pz["sign"].append(torch.full((m,), -1.0 if is_owner else 1.0, dtype=torch.float64, device=tdev))
                pz["c0"].append(torch.full((m,), bc.D * bc.nu, dtype=torch.float64, device=tdev))
                pz["c1"].append(torch.full((m,), 0.5 * bc.I, dtype=torch.float64, device=tdev))
                pz["len"].append(torch.full((m,), bc.length, dtype=torch.float64, device=tdev))
                masked.append((ghost.long().reshape(-1), 3))
        part = getattr(app, "_partition", None)
        if part is not None:
            # multi-GPU (opt-in, docs/development/multi-gpu.md): every patch sum runs over the faces this part OWNS
            # (owner cell own) and is all-reduced in _launch (``_preduce``); the static denominators are the whole
            # mesh's (one all-reduce here, on every rank: each part holds every patch, possibly with no face)
            own = part["is_own_cell"]
            fro["rhoa"] = [w * own[o].to(torch.float64) for w, o in zip(fro["rhoa"], fro["owner"])]
            rk["area"] = [w * own[s].to(torch.float64) for w, s in zip(rk["area"], rk["src"])]
            if fro["asum"]:
                tot = part["allreduce"](torch.stack([w.sum() for w in fro["rhoa"]]))
                fro["asum"] = [tot[k].reshape(1) for k in range(tot.numel())]
            if rk["asum"]:
                tot = part["allreduce"](torch.stack([w.sum() for w in rk["area"]]))
                rk["asum"] = [tot[k].reshape(1) for k in range(tot.numel())]
        cat = lambda xs, dt=torch.int64: torch.cat(xs) if xs else torch.zeros(0, dtype=dt, device=tdev)  # noqa: E731
        ewt = {k: cat(v, torch.int64 if k in ("ghost", "owner") else torch.float64) for k, v in ew.items()}
        rkt = {k: cat(v, torch.float64 if k in ("area", "asum", "dt") else torch.int64)
               for k, v in rk.items() if k not in ("off", "n", "flux")}
        rkt["off"] = torch.tensor(rk["off"], dtype=torch.int64, device=tdev)
        rkt["n"] = torch.cat(rk["n"]) if rk["n"] else torch.zeros(0, 3, dtype=torch.float64, device=tdev)
        rkt["flux"] = torch.tensor(rk["flux"], dtype=torch.int64, device=tdev)
        sizes = [int(t.numel()) for t in rk["ghost"]]
        starts = [sum(sizes[:k]) for k in range(len(sizes))]
        self._lagged = [(bc, starts[k], starts[k] + sizes[k]) for bc, k in lagged]
        pzt = {k: (torch.cat(v) if v else torch.zeros((0, 3) if k == "n" else (0,), device=tdev,
                                                       dtype=torch.int64 if k in ("ghost", "src", "a", "b")
                                                       else torch.float64)) for k, v in pz.items()}
        return masked, ewt, rkt, n_rack, pzt

    def _build_ghosts(self, app, tdev):
        n_cpd = self.n_cpd
        interior = torch.zeros(n_cpd, dtype=torch.bool, device=tdev)
        interior[self.cpd_of] = True
        ghosts = torch.nonzero(~interior).reshape(-1)
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
        masked, ewt, rkt, n_rack, pzt = self._special_ghosts(app, tdev)
        slot = torch.full((n_cpd,), -1, dtype=torch.int64, device=tdev)
        slot[ghosts] = torch.arange(ghosts.numel(), device=tdev)
        with torch.no_grad():
            zero = torch.zeros(n_cpd, 7, dtype=torch.float64, device=tdev)
            b = self._bc_pass(app, zero)[ghosts]
            A = torch.zeros(ghosts.numel(), 7, 7, dtype=torch.float64, device=tdev)
            for c in range(7):
                s = zero.clone()
                s[self.cpd_of, c] = 1.0
                A[:, :, c] = self._bc_pass(app, s)[ghosts] - b
        if masked:
            # one scatter for all special rows (one entry per patch: a loop with a device check each cost ~1 min on
            # a hall with thousands of patches)
            sl = slot[torch.cat([g.reshape(-1) for g, _ in masked])]
            chs = torch.cat([torch.full((g.numel(),), int(c), dtype=torch.int64, device=tdev) for g, c in masked])
            if bool((sl < 0).any()):
                raise RuntimeError("warp backend (DC RANS): a special boundary row is not a ghost")
            A[sl, chs, :] = 0.0
            b[sl, chs] = 0.0
        # a cyclic ghost's velocity block must be the identity (translational cyclic: the gradient copy is exact)
        cyc = partner[ghosts] >= 0
        if bool(cyc.any()):
            blk = A[cyc][:, 0:3, 0:3]
            if (blk - torch.eye(3, dtype=blk.dtype, device=tdev)).abs().max().item() > 1e-14:
                raise NotImplementedError("warp backend (DC RANS): a rotational cyclic patch")
        self.ghost_ids = _i32(ghosts)
        self.ghost_A = wp.from_torch(A.contiguous())
        self.ghost_b = wp.from_torch(b.contiguous())
        self._gb_rows = wp.from_torch(wp.to_torch(self.ghost_b).unsqueeze(0))
        self._ghosts_t, self._src_t, self._partner_t = ghosts, src, partner
        self.n_ghost = int(ghosts.numel())
        # epsilonWallFunction per ghost slot (zero coefficient elsewhere)
        z = lambda: torch.zeros(ghosts.numel(), dtype=torch.float64, device=tdev)  # noqa: E731
        ewc, ewd, ewm, eww = z(), z().fill_(1.0), z(), z().fill_(1.0)
        if ewt["ghost"].numel():
            sl = slot[ewt["ghost"]]
            if not torch.equal(src[ewt["ghost"]], ewt["owner"]):
                raise RuntimeError("warp backend (DC RANS): an epsilonWallFunction ghost whose owner is not its source")
            ewc[sl], ewd[sl], ewm[sl], eww[sl] = ewt["c75"], ewt["den"], ewt["kmin"], ewt["kw"]
        self.n_ewall = int(ewt["ghost"].numel())
        self._ew_faces = [(slot[gh], fids, kappa) for gh, fids, kappa in self._ew_bcs]
        self._ew_t = (ewc, ewd, ewm, eww)
        self.ew_c, self.ew_den, self.ew_kmin, self.ew_kw = (wp.from_torch(t.contiguous()) for t in self._ew_t)
        # every ghost written by the thread of its source cell
        row = torch.full((n_cpd,), -1, dtype=torch.int64, device=tdev)
        row[self.cpd_of] = torch.arange(self.n, device=tdev)
        order, off = _csr(row[src[ghosts]], self.n)
        self.dep_off, self.dep_slot = _i32(off), _i32(torch.arange(ghosts.numel(), device=tdev)[order])
        cg, cp = ghosts[cyc], partner[ghosts][cyc]
        order, off = _csr(row[cp], self.n)
        self.mir_off, self.mir_cpd = _i32(off), _i32(cg[order])
        self.n_cyc = int(cyc.sum())
        # racks
        self.n_rack = n_rack
        self.n_rack_ghost = int(rkt["ghost"].numel())
        self.r_off, self.r_src, self.r_area, self.r_asum = (_i32(rkt["off"]), _i32(rkt["src"]), _f64(rkt["area"]),
                                                            _f64(rkt["asum"]))
        self.r_flux, self.r_gh, self.r_n = _i32(rkt["flux"]), _i32(rkt["gh"]), _v3(rkt["n"])
        self.o_ghost, self.o_rack, self.o_remote, self.o_dt = (_i32(rkt["ghost"]), _i32(rkt["rack"]),
                                                               _i32(rkt["remote"]), _f64(rkt["dt"]))
        self._odt_rows = wp.from_torch(wp.to_torch(self.o_dt).unsqueeze(0))
        self._o_lag_t = torch.zeros(max(int(rkt["ghost"].numel()), 1), dtype=torch.float64, device=tdev)
        self.o_lag = wp.from_torch(self._o_lag_t)
        self._lag_values = [None] * len(self._lagged)
        self._rack_t = rkt
        # flowRateOutletVelocity patches
        fr = self._fro
        catf = lambda k, dt: torch.cat(fr[k]).to(dt) if fr[k] else torch.zeros(0, dtype=dt, device=tdev)  # noqa
        self.n_fro_face = int(sum(int(t.numel()) for t in fr["owner"]))
        self.f_off = _i32(torch.tensor(fr["off"], dtype=torch.int64, device=tdev))
        self.f_owner, self.f_ghost, self.f_patch = (_i32(catf("owner", torch.int64)), _i32(catf("ghost", torch.int64)),
                                                    _i32(catf("patch", torch.int64)))
        self.f_n = _v3(torch.cat(fr["n"]) if fr["n"] else torch.zeros(0, 3, dtype=torch.float64, device=tdev))
        self.f_rhoa, self.f_target, self.f_asum = (_f64(catf("rhoa", torch.float64)), _f64(catf("target", torch.float64)),
                                                   _f64(catf("asum", torch.float64)))
        self._ft_rows = wp.from_torch(wp.to_torch(self.f_target).unsqueeze(0))
        # porous baffles
        self.n_porous = int(pzt["ghost"].numel())
        self.pz_g, self.pz_src, self.pz_a, self.pz_b = (_i32(pzt["ghost"]), _i32(pzt["src"]), _i32(pzt["a"]),
                                                        _i32(pzt["b"]))
        self.pz_n, self.pz_sign, self.pz_c0, self.pz_c1, self.pz_len = (_v3(pzt["n"]), _f64(pzt["sign"]),
                                                                        _f64(pzt["c0"]), _f64(pzt["c1"]),
                                                                        _f64(pzt["len"]))
        self._pz_t = pzt
        self._pz_rows = wp.from_torch(wp.to_torch(self.pz_c1).unsqueeze(0))
        # nut invariant source (realizableKE ghost_fill_owner: owner on non-cyclic boundary ghosts)
        inv = torch.arange(n_cpd, dtype=torch.int64, device=tdev)
        bfaces = app.mask_boundary_non_cyclic_face.view(-1).nonzero(as_tuple=True)[0]
        inv[ncc_g(app)[1][bfaces]] = ncc_g(app)[0][bfaces]
        self.inv_src = _i32(inv)
        self._keep_g = (ghosts, src, A, b, partner, inv)

    def _check_ghosts(self, seed=20261106):
        """The whole ghost fill (affine map + the special kernels) against the torch boundary passes on a random state:
        max |warp - torch| / (max |torch| + 1) <= 1e-13, else the configuration is refused."""
        gen = torch.Generator(device="cpu").manual_seed(seed)
        x = torch.randn(7, self.n, generator=gen, dtype=torch.float64)
        x[5] = x[5].abs() * 1e-2
        x[6] = x[6].abs() * 1e-2
        x = x.reshape(-1).to(self.cs.device)
        self.residual(x)
        q = wp.to_torch(self._buffers(0, 1)["q"]).reshape(self.n_cpd, 7)
        s = torch.zeros(self.n_cpd, 7, dtype=torch.float64, device=self.cs.device)
        s[self.cpd_of] = x.reshape(7, self.n).T
        with torch.no_grad():
            want = self._bc_pass(self.app, s)[self._ghosts_t]
        got = q[self._ghosts_t]
        err = float((got - want).abs().max()) / (float(want.abs().max()) + 1.0)
        self.ghost_check = err
        if err > 1e-13:
            worst = int((got - want).abs().amax(dim=1).argmax())
            raise RuntimeError(f"warp backend (DC RANS): the ghost fill misses the torch boundary passes ({err:.3e}, "
                               f"ghost {int(self._ghosts_t[worst])}: warp {got[worst].tolist()} torch "
                               f"{want[worst].tolist()})")

    # ------------------------------------------------------------------ wall tables
    def _build_wall(self, app, tdev):
        from FVdomain.SetBC.onthefly.BCbase import VARType
        n = self.n
        tm = app.turbulence
        nfc = self.n_face
        wmap = tm.wall_cell_map(app)
        wslot = torch.full((nfc,), -1, dtype=torch.int64, device=tdev)
        if wmap is None:
            woff = torch.zeros(n + 1, dtype=torch.int64, device=tdev)
            wf = wg = torch.zeros(0, dtype=torch.int64, device=tdev)
            wd = wky = wcy = wy = torch.zeros(0, dtype=torch.float64, device=tdev)
            face_idx = wf
        else:
            face_idx, ghost_cpd, cell_cpd, cell_row, corner_slot = wmap
            if not torch.equal(cell_cpd[corner_slot], app.C_senders.long()[face_idx]):
                raise RuntimeError("warp backend (DC RANS): a wall face whose wall cell is not its owner")
            order, woff = _csr(cell_row[corner_slot], n)
            wf, wg = face_idx[order], ghost_cpd[order]
            wd = app.nonOrthDeltaCoeffs.reshape(-1)[wf].to(torch.float64)
            y_all = app.wall_distance_face.reshape(-1, 1)
            wky = (tm.wallKappa * y_all.index_select(0, wf)).reshape(-1).to(torch.float64)
            wslot[face_idx] = torch.arange(face_idx.numel(), device=tdev)
            y_w = y_all.index_select(0, face_idx)
            wcy = ((tm.wallCmu ** 0.25) * y_w).reshape(-1).to(torch.float64)
            wy = y_w.reshape(-1).to(torch.float64)
        self.n_wallface = int(face_idx.numel())
        self.w_off_c, self.w_face, self.w_ghost = _i32(woff), _i32(wf), _i32(wg)
        self._wky_t = wky.contiguous().clone() if wky.numel() else torch.zeros(1, dtype=torch.float64, device=tdev)
        self.w_delta, self.w_ky = _f64(wd), wp.from_torch(self._wky_t)
        g = self.geom
        self._wcy_t = wcy.contiguous().clone() if wcy.numel() else torch.zeros(1, dtype=torch.float64, device=tdev)
        self._wy_t = wy.contiguous().clone() if wy.numel() else torch.zeros(1, dtype=torch.float64, device=tdev)
        g.wslot, g.wcy, g.wy = _i32(wslot), wp.from_torch(self._wcy_t), wp.from_torch(self._wy_t)
        self._wd_ref = app.wall_distance_face
        if self.const.jaya:
            pr, p, ypt = tm._alphat_constants(app, face_idx)
            g.wpr, g.wpp, g.wypt = _f64(pr), _f64(p), _f64(ypt)
        else:
            g.wpr = g.wpp = g.wypt = _f64(torch.zeros(1, dtype=torch.float64, device=tdev))
        # the epsilonWallFunction cell constraints
        cform = torch.zeros(n, dtype=torch.int32, device=tdev)
        coff = torch.zeros(n + 1, dtype=torch.int64, device=tdev)
        cgh = torch.zeros(0, dtype=torch.int64, device=tdev)
        rows = app._cell_constrained_rows(VARType.TURB_EPSILON)
        self.n_constrained = 0
        if rows is not None:
            cell_row, cell_cpd, ghost_cpd, corner_slot, combine, form = rows
            from FVdomain.SetBC.onthefly.setEpsilon import Epsilonwallfunction
            if getattr(combine, "__func__", combine) is not Epsilonwallfunction.combine_cell_constraints:
                raise NotImplementedError("warp backend (DC RANS): an epsilon cell constraint other than "
                                          "epsilonWallFunction's scatter mean")
            order, coff = _csr(cell_row[corner_slot], n)
            cgh = ghost_cpd[order]
            cform[cell_row] = 1 if form == "difference" else 2
            self.n_constrained = int(cell_row.numel())
            if wmap is not None and not torch.equal(torch.sort(wmap[3]).values, torch.sort(cell_row).values):
                raise RuntimeError("warp backend (DC RANS): the wall-production cells differ from the constrained cells")
        self.c_cform, self.c_coff, self.c_cghost = _i32(cform), _i32(coff), _i32(cgh)
        self._keep_wall = (woff, wf, wg, wd, wky, coff, cgh, cform, wslot)

    # ------------------------------------------------------------------ coefficients (re-read on change)
    _COEF_SOURCES = ("nu_coeff", "alpha_coeff", "gravity", "hRef", "beta", "theta_PDE", "grad_p_coeff",
                     "convection_coeff", "continuity_eq_coeff", "source_term", "heat_source_term", "unsteady_coeff",
                     "dt_cell")

    def _coefficient_values(self, app):
        nfc, n = self.n_face, self.n
        bf, bc_ = app.batch_face, app.batch_cell

        def face(t):
            t = t.reshape(-1).to(torch.float64)
            return t.expand(nfc) if t.numel() == 1 else t

        def cell(t):
            t = t.reshape(-1).to(torch.float64)
            return t.expand(n) if t.numel() == 1 else t

        v = {"nu": face(app.nu_coeff), "alpha": face(app.alpha_coeff), "gpc": face(app.grad_p_coeff),
             "cc": face(app.convection_coeff), "ccont": app.continuity_eq_coeff[bf].reshape(-1).to(torch.float64),
             "ghb": (app.gh_face() * app._beta_face()).reshape(-1).to(torch.float64),
             "pcoef": app.theta_PDE[bc_, 3:4].reshape(-1).to(torch.float64),
             "nucell": app.theta_PDE[bc_, 4:5].reshape(-1).to(torch.float64)}
        beta_cell = app._beta_cell()
        g_cell = app.gravity[bc_]
        ghRef = torch.norm(g_cell, dim=1, keepdim=True) * app.hRef[bc_]
        pos = app.cpd_cell_pos.index_select(0, app._facecv_interior_ids)
        gh = ((pos * g_cell).sum(dim=1, keepdim=True) - ghRef)
        v["ghbc"] = (gh * beta_cell).reshape(-1).to(torch.float64)
        v["betac"] = cell(beta_cell)
        v["gvec"] = g_cell.to(torch.float64).expand(n, 3).contiguous()
        src = torch.zeros(n, 4, dtype=torch.float64, device=self.cs.device)
        mom = app.source_term.reshape(n, -1).to(torch.float64)
        src[:, 0:3] = mom.expand(n, 3) if mom.shape[1] == 1 else mom[:, 0:3]
        src[:, 3] = app.heat_source_term.reshape(n).to(torch.float64)
        v["src"] = src
        v["tcoef"] = cell(app.unsteady_coeff) * cell(app.cell_volume) / cell(app.dt_cell)
        return v

    def _build_coefficients(self, app, tdev):
        self._coef_t = {k: t.contiguous().clone() for k, t in self._coefficient_values(app).items()}
        c = self._coef_t
        g = self.geom
        g.nu, g.alpha, g.gpc, g.cc, g.ccont, g.ghb = (wp.from_torch(c[k]) for k in ("nu", "alpha", "gpc", "cc",
                                                                                      "ccont", "ghb"))
        self.c_pcoef, self.c_nucell, self.c_ghbc, self.c_betac = (wp.from_torch(c[k]) for k in
                                                                  ("pcoef", "nucell", "ghbc", "betac"))
        self.c_gvec = wp.from_torch(c["gvec"], dtype=wp.vec3d)
        self.c_src = wp.from_torch(c["src"])
        self.c_tcoef = wp.from_torch(c["tcoef"])
        self._coef_keys = tuple(k for k in self._COEF_SOURCES if torch.is_tensor(getattr(app, k, None)))
        self._coef_stamps = [_stamp(getattr(app, k)) for k in self._coef_keys]
        self.coef_refreshes = 0
        self.qold = torch.zeros(self.n, 7, dtype=torch.float64, device=tdev)
        self._qold_w = wp.from_torch(self.qold.unsqueeze(0))
        # the tables a launch reads, by row: one step (row views of the single tables -- what CSPatchBinding writes),
        # or a time window (``window``: K rows, chunk j = step j)
        self._rows_active = None
        self._rows_single = None

    def sync_wall_distance(self) -> bool:
        """Re-read ``wall_distance_face`` when it was rebound after the build (epsilonWallFunction's
        ``wallDistance = "nearWallDist"`` rebinds it once per registration, at the first eval -- before the build
        reads it, since the build runs a torch forward first; a later re-registration rebinds it again): the
        epsilon wall value, nutk's y+, the wall-cell G and the Jayatilleke y+ all read it."""
        app = self.app
        wd = app.wall_distance_face
        if wd is self._wd_ref:
            return False
        tm = app.turbulence
        y_all = wd.reshape(-1, 1)
        if self.n_wallface:
            wf = self._keep_wall[1]
            face_idx = torch.nonzero(self._keep_wall[8] >= 0).reshape(-1)
            face_idx = face_idx[torch.argsort(self._keep_wall[8][face_idx])]
            self._wky_t.copy_((tm.wallKappa * y_all.index_select(0, wf)).reshape(-1).to(torch.float64))
            y_w = y_all.index_select(0, face_idx)
            self._wcy_t.copy_(((tm.wallCmu ** 0.25) * y_w).reshape(-1).to(torch.float64))
            self._wy_t.copy_(y_w.reshape(-1).to(torch.float64))
        if self.n_ewall:
            ewd = self._ew_t[1]
            for gh_slots, owner_faces, kappa in self._ew_faces:
                ewd[gh_slots] = (kappa * y_all.index_select(0, owner_faces)).reshape(-1).to(torch.float64)
        self._wd_ref = wd
        return True

    def sync_coefficients(self) -> bool:
        """Re-read the coefficient tensors whose identity or version changed (nu / alpha at every continuation rung)
        into the same buffers, so the captured graphs stay valid."""
        app = self.app
        self._refuse_dynamic(app)
        self.sync_wall_distance()
        for k, (ref, ver) in zip(self._coef_keys, self._coef_stamps):
            t = getattr(app, k)
            if t is not ref or t._version != ver:
                break
        else:
            return False
        for k, t in self._coefficient_values(app).items():
            self._coef_t[k].copy_(t)
        self._coef_stamps = [_stamp(getattr(app, k)) for k in self._coef_keys]
        self.coef_refreshes += 1
        return True

    def sync_lagged(self, x=None):
        """CBBM ``lagMean = true``: the discharge ghosts hold the condition's frozen intake mean + dT, which the entry's
        outer pass (``setRack.cbbm_outer_pass``) replaces between solves; re-read here on every call (host floats, no
        device sync).  An unseeded mean is seeded from the state ``x`` exactly as the torch eval seeds it (the live
        area mean of the intake owners' T), on the condition object itself."""
        if any(bc._lagged_mean is None for bc, _a, _b in self._lagged):
            if x is None:
                raise RuntimeError("warp backend (DC RANS): an unseeded lagged CBBM mean and no state to seed it")
            # the torch boundary passes on this state: the condition's own eval seeds its mean (any weighting)
            s = torch.zeros(self.n_cpd, 7, dtype=torch.float64, device=self.cs.device)
            s[self.cpd_of] = x.detach().reshape(7, self.n).T.to(torch.float64)
            with torch.no_grad():
                self._bc_pass(self.app, s)
        for k, (bc, a, b) in enumerate(self._lagged):
            value = bc._lagged_mean + bc.deltaT
            if self._lag_values[k] != value:
                self._o_lag_t[a:b] = value
                self._lag_values[k] = value

    def set_old_state(self, old_state):
        if old_state is None:
            self.qold.zero_()
        else:
            self.qold.copy_(old_state.detach().to(torch.float64)[self.cpd_of])

    # ------------------------------------------------------------------ time window
    @property
    def _rows(self) -> dict:
        """The row-indexed tables of the next launch: ``sb = 0`` and the single step's tables (row views of
        ``ghost_b``, ``f_target``, ``o_dt``, ``pz_c1`` and ``qold``), or, inside ``window``, the window's K rows."""
        if self._rows_active is not None:
            return self._rows_active
        if self._rows_single is None:
            self._rows_single = {"sb": 0, "gb": self._gb_rows, "ft": self._ft_rows, "odt": self._odt_rows,
                                 "pz": self._pz_rows, "qold": self._qold_w}
        return self._rows_single

    def step_tables(self) -> dict:
        """The boundary values the kernels read for one step, as torch views of the device tables (what
        ``Pipeline.CoupledNewton.patch_binding.CSPatchBinding.bind`` writes): ``gb`` the ghost values of the affine
        boundary maps (n_ghost, 7), ``ft`` the flowRateOutletVelocity targets, ``odt`` the rack outlet dT per ghost,
        ``pz`` the porous-baffle ``I / 2`` per ghost.  ``window`` takes K stacked rows of these."""
        return {"gb": wp.to_torch(self.ghost_b), "ft": wp.to_torch(self.f_target), "odt": wp.to_torch(self.o_dt),
                "pz": wp.to_torch(self.pz_c1)}

    def _window_buffers(self, K: int, kpad: int):
        key = ("win", K, kpad)
        if key not in self._bufs:
            dev, N = self.cs.device, self.n * 7
            z = lambda *s: torch.zeros(*s, dtype=torch.float64, device=dev)  # noqa: E731
            tables = {k: z(K, *v.shape) for k, v in self.step_tables().items()}
            tables["qold"] = z(K, self.n, 7)
            io = {"x": z(K * N), "dirs": z(max(kpad, 1), N), "res": z(K * N), "tan": z(max(kpad, 1), N)}
            io["x_rows"], io["res_rows"] = io["x"].view(K, N), io["res"].view(K, N)
            rows = {"sb": 1, **{k: wp.from_torch(v) for k, v in tables.items()}}
            self._bufs[key] = (io, tables, rows)
        return self._bufs[key]

    def window(self, X: torch.Tensor, V: torch.Tensor | None = None, *, old: torch.Tensor | None = None,
               tables: dict | None = None, frozen: bool = False):
        """The residuals of K time steps -- and the JVPs ``J_k(X_k) V_k`` -- in ONE launch sequence: chunk j of every
        kernel is step j, reading row j of the state, of the old state and of the boundary values (the kernels'
        ``sb = 1``), so the K steps cost one pass over the mesh with K-wide parallelism (CUDA-graph captured per
        (K, directions per step, frozen)).

        ``X`` (K, 7n) the states (the unknown layout of ``residual``); ``V`` None, (K, 7n) or (K, m, 7n): m
        directions per step (m a chunk width of ``chunk_widths``); ``old`` (K, 7n): the old state of the
        application's own backward-Euler term (None: the current ``qold`` for every step); ``tables``: the boundary
        values of every step in the layout of ``step_tables`` with a leading K (an entry without it, or None: the
        current step's for all K).  Returns ``(R (K, 7n), JV (K, m, 7n) or None)``; equal to K calls of
        ``residual`` / ``jvp`` with each step's tables bound.  Not available with a lagged CBBM mean (a host-side
        value per solve) or a partitioned reduction."""
        if self._lagged:
            raise NotImplementedError("warp backend (DC RANS) window: a lagged CBBM mean is one host value per solve")
        if getattr(self, "_preduce", None) is not None:
            raise NotImplementedError("warp backend (DC RANS) window: partitioned reductions")
        K = int(X.shape[0])
        N = self.n * 7
        X = X.detach().reshape(K, N).to(torch.float64)
        m = 0 if V is None else (1 if V.dim() == 2 else int(V.shape[1]))
        if m > 1 and m not in chunk_widths():
            raise ValueError(f"window: {m} directions per step is not a chunk width {chunk_widths()}")
        kpad = K * m
        io, tt, rows = self._window_buffers(K, kpad)
        self.sync_coefficients()
        ts = torch.cuda.current_stream(self.cs.device)
        ws, wts = self._stream_pair()
        wts.wait_stream(ts)
        with torch.cuda.stream(wts):
            io["x_rows"].copy_(X)
            if m:
                io["dirs"][:kpad].copy_(V.detach().reshape(kpad, N).to(torch.float64))
            src = self.step_tables() if tables is None else {**self.step_tables(), **tables}
            for key in ("gb", "ft", "odt", "pz"):
                v = src[key].to(device=tt[key].device, dtype=torch.float64)
                tt[key].copy_(v if v.dim() == tt[key].dim() else v.unsqueeze(0).expand_as(tt[key]))
            if old is None:
                tt["qold"].copy_(self.qold.unsqueeze(0).expand_as(tt["qold"]))
            else:
                tt["qold"].copy_(old.detach().reshape(K, 7, self.n).transpose(1, 2).to(torch.float64))
            self._rows_active = rows
            try:
                if os.environ.get("LPCNS_WARP_GRAPH", "1") == "0" or getattr(self, "_in_torch_capture", False):
                    with wp.ScopedStream(ws, sync_enter=False):
                        self._launch(io, kpad, m, K, frozen)
                else:
                    gk = ("graph_win", K, m, bool(frozen))
                    graph = self._bufs.get(gk)
                    if graph is None:
                        with wp.ScopedStream(ws, sync_enter=False):
                            self._launch(io, kpad, m, K, frozen)
                            with wp.ScopedCapture(stream=ws) as cap:
                                self._launch(io, kpad, m, K, frozen)
                        graph = self._bufs[gk] = cap.graph
                    wp.capture_launch(graph, stream=ws)
            finally:
                self._rows_active = None
        ts.wait_stream(wts)
        R = io["res_rows"].clone()
        return R, (io["tan"][:kpad].reshape(K, m, N).clone() if m else None)

    # ------------------------------------------------------------------ buffers and launches
    def _buffers(self, cw, nch):
        key = ("b", cw, nch)
        if key in self._bufs:
            return self._bufs[key]
        D = dual_type(cw)
        dev = self.dev
        if cw > 1 and os.environ.get("LPCNS_WARP_DC_FREE", "0") == "1":
            # the probe buffers that are released after a build live in torch's allocator (viewed as Warp arrays):
            # one pool for the solver and the build, so returning torch's cache before a rebuild frees what the
            # build needs (with two pools a late re-allocation failed on PERIM_HAC)
            keep = []

            def zeros(shape):
                t = torch.zeros(*shape, cw + 1, dtype=torch.float64, device=self.cs.device)
                keep.append(t)
                return wp.from_torch(t, dtype=D)
        else:
            keep = None

            def zeros(shape):
                return wp.zeros(shape, dtype=D, device=dev)
        b = {"zt": wp.zeros(1, dtype=D, device=dev),
             "q": zeros((nch, self.n_cpd, 7)),
             "nut": zeros((nch, self.n_cpd)),
             "grad": zeros((nch, self.n_cpd, 7, 3)),
             "f0": zeros((nch, self.n_face)),
             "nutf": zeros((nch, self.n_face)),
             "d": zeros((nch, self.n_cpd)),
             "flux": zeros((nch, self.n_face)),
             "fmom": zeros((nch, self.n_face, 3)),
             "fsc": zeros((nch, self.n_face, 3)),
             "rmean": zeros((nch, max(self.n_rack, 1)))}
        if keep is not None:
            b["_torch"] = keep
        nfro, nrack = max(self.n_fro, 1), max(self.n_rack, 1)
        if nch == 1:
            red_t = torch.zeros(1, nfro + 2 * nrack, 1 + cw, dtype=torch.float64, device=self.cs.device)
            b["red_t"] = red_t
            b["fest"] = wp.from_torch(red_t[:, :nfro, :], dtype=D)
            b["racc"] = wp.from_torch(red_t[:, nfro:nfro + nrack, :], dtype=D)
            b["rden"] = wp.from_torch(red_t[:, nfro + nrack:, :], dtype=D)
        else:
            b["fest"] = wp.zeros((nch, nfro), dtype=D, device=dev)
            b["racc"] = wp.zeros((nch, nrack), dtype=D, device=dev)
            b["rden"] = wp.zeros((nch, nrack), dtype=D, device=dev)
        self._bufs[key] = b
        return b

    def release(self):
        """Drop every buffer set and captured graph (the next call allocates and captures again)."""
        self._bufs.clear()
        for k in [k for k in self._compact if isinstance(k, tuple) and k and str(k[0]).startswith("graph")]:
            del self._compact[k]

    def memory_bytes(self) -> dict:
        """Device bytes this residual holds, by kind: the dual-number work buffers (Warp's allocator -- invisible to
        ``torch.cuda.memory_allocated``), the torch-side io buffers, and the compact-Jacobian block values."""
        out = {"warp_buffers": 0, "io": 0, "compact": 0}
        for key, b in self._bufs.items():
            if not isinstance(b, dict):
                continue
            for v in b.values():
                if isinstance(v, wp.array):
                    out["warp_buffers"] += int(v.capacity)
                elif torch.is_tensor(v):
                    out["io"] += v.numel() * v.element_size()
        for k, v in self._compact.items():
            if isinstance(v, dict) and torch.is_tensor(v.get("vals")):
                out["compact"] += v["vals"].numel() * 8
        return out

    def compound_state(self) -> torch.Tensor:
        """``[N_cpd, 7]`` of the last residual evaluation: the interior and the boundary-filled ghosts (a view of the
        residual's buffer, rewritten by the next residual)."""
        return wp.to_torch(self._buffers(0, 1)["q"]).reshape(self.n_cpd, 7)

    def _io(self, kpad):
        key = ("io", kpad)
        if key not in self._bufs:
            N, dev = self.n * 7, self.cs.device
            io = {"x": torch.zeros(N, dtype=torch.float64, device=dev),
                  "dirs": torch.zeros(max(kpad, 1), N, dtype=torch.float64, device=dev),
                  "res": torch.zeros(N, dtype=torch.float64, device=dev),
                  "tan": torch.zeros(max(kpad, 1), N, dtype=torch.float64, device=dev)}
            io["x_rows"], io["res_rows"] = io["x"].view(1, N), io["res"].view(1, N)
            self._bufs[key] = io
        return self._bufs[key]

    def _stream_pair(self):
        # one pair per device for the life of the process: torch keeps blocks it allocated on this stream, so the
        # stream must outlive this backend (a collected backend used to destroy it, and torch's next call on the
        # device then failed with "context is destroyed" -- a solver service runs many solves in one process)
        key = torch.device(self.cs.device)
        if key not in _PAIR_STREAMS:
            ws = wp.Stream(self.dev)
            _PAIR_STREAMS[key] = (ws, torch.cuda.ExternalStream(ws.cuda_stream, device=key))
        return _PAIR_STREAMS[key]

    def _dummy(self):
        if "dummy" not in self._bufs:
            dev = self.cs.device
            self._bufs["dummy"] = {"i": wp.from_torch(torch.zeros(1, dtype=torch.int32, device=dev)),
                                   "v": wp.from_torch(torch.zeros(1, 1, 1, dtype=torch.float64, device=dev))}
        return self._bufs["dummy"]

    def _two_stage_red(self) -> bool:
        if "_two_stage" not in self._bufs:
            self._bufs["_two_stage"] = bool(
                self.n_fro and self.n_rack and bool((self._rack_t["flux"] == 1).any())
            )
        return self._bufs["_two_stage"]

    def _launch_pre(self, io, kdir, cw, nch, drop_global=0, seed=None, local_red=False, two_stage=False):
        b = self._buffers(cw, nch)
        dev = self.dev
        n = self.n
        cpd = self._w_cpd
        dm = self._dummy()
        if seed is None:
            seeded, p0, scol, sk, sc, eoff, ecol, vals = 0, 0, dm["i"], dm["i"], dm["i"], dm["i"], dm["i"], dm["v"]
        else:
            seeded = 1
            p0, scol, sk, sc, eoff, ecol, vals = seed
        rw = self._rows
        xw, dw = wp.from_torch(io["x_rows"]), wp.from_torch(io["dirs"])
        wp.launch(dc_load_kernel, dim=(n, nch),
                  inputs=[xw, dw, seeded, p0, scol, sk, sc, cpd, n, kdir, cw, rw["sb"], self.dep_off, self.dep_slot,
                          self.ghost_ids, self.ghost_A, rw["gb"], self.ew_c, self.ew_den, self.ew_kmin,
                          self.ew_kw, b["zt"]], outputs=[b["q"]], device=dev)
        if self.n_fro and not local_red:
            wp.launch(dc_fro_est_kernel, dim=(self.n_fro, nch),
                      inputs=[self.f_off, self.f_owner, self.f_n, self.f_rhoa, int(drop_global), b["zt"], b["q"]],
                      outputs=[b["fest"]], device=dev)
            if getattr(self, "_preduce", None) is None:
                wp.launch(dc_fro_write_kernel, dim=(self.n_fro_face, nch),
                          inputs=[self.f_patch, self.f_owner, self.f_ghost, self.f_n, self._rows["ft"], self.f_asum, self._rows["sb"],
                                  b["fest"]], outputs=[b["q"]], device=dev)
        if self.n_porous:
            wp.launch(dc_porous_kernel, dim=(self.n_porous, nch),
                      inputs=[self.pz_g, self.pz_src, self.pz_a, self.pz_b, self.pz_n, self.pz_sign, self.pz_c0,
                              self._rows["pz"], self.pz_len, self._rows["sb"]], outputs=[b["q"]], device=dev)
        if self.n_rack and getattr(self, "_preduce", None) is not None:
            if not local_red and not two_stage:
                wp.launch(dc_rack_sums_kernel, dim=(self.n_rack, nch),
                          inputs=[self.r_off, self.r_src, self.r_area, self.r_flux, self.r_gh, self.r_n, b["zt"], b["q"]],
                          outputs=[b["racc"], b["rden"]], device=dev)
        elif self.n_rack:
            wp.launch(dc_rack_mean_kernel, dim=(self.n_rack, nch),
                      inputs=[self.r_off, self.r_src, self.r_area, self.r_asum, self.r_flux, self.r_gh, self.r_n,
                              int(drop_global), b["zt"], b["q"]],
                      outputs=[b["rmean"]], device=dev)

    def _launch_mid(self, cw, nch):
        b = self._buffers(cw, nch)
        dev = self.dev
        if self.n_fro:
            wp.launch(dc_fro_write_kernel, dim=(self.n_fro_face, nch),
                      inputs=[self.f_patch, self.f_owner, self.f_ghost, self.f_n, self._rows["ft"], self.f_asum, self._rows["sb"],
                              b["fest"]], outputs=[b["q"]], device=dev)
        if self.n_rack:
            wp.launch(dc_rack_sums_kernel, dim=(self.n_rack, nch),
                      inputs=[self.r_off, self.r_src, self.r_area, self.r_flux, self.r_gh, self.r_n, b["zt"], b["q"]],
                      outputs=[b["racc"], b["rden"]], device=dev)

    def _launch_reduce(self, cw, nch, stage: str = "all"):
        red = getattr(self, "_preduce", None)
        if red is None or (not self.n_fro and not self.n_rack):
            return
        b = self._buffers(cw, nch)
        if nch == 1 and "red_t" in b and hasattr(red, "warp_reduce_coalesced"):
            red.warp_reduce_coalesced(b["red_t"], self.n_fro, self.n_rack, stage=stage)
            return
        if self.n_fro and stage in ("all", "fro"):
            red("fro", b["fest"])
        if self.n_rack and stage in ("all", "rack"):
            red("rack_num", b["racc"])
            red("rack_den", b["rden"])

    def _launch_post(self, io, kdir, cw, nch, frozen, want_scale=0, freeze_grad=0, drop_global=0, seed=None,
                     skip_fro_write=False, freeze_flux=0):
        b = self._buffers(cw, nch)
        dev = self.dev
        n = self.n
        g = self.geom
        kc = self.const
        cpd = self._w_cpd
        dm = self._dummy()
        if seed is None:
            seeded, p0, scol, sk, sc, eoff, ecol, vals = 0, 0, dm["i"], dm["i"], dm["i"], dm["i"], dm["i"], dm["v"]
        else:
            seeded = 1
            p0, scol, sk, sc, eoff, ecol, vals = seed
        if self.n_fro and getattr(self, "_preduce", None) is not None and not skip_fro_write:
            wp.launch(dc_fro_write_kernel, dim=(self.n_fro_face, nch),
                      inputs=[self.f_patch, self.f_owner, self.f_ghost, self.f_n, self._rows["ft"], self.f_asum, self._rows["sb"],
                              b["fest"]], outputs=[b["q"]], device=dev)
        if self.n_rack and getattr(self, "_preduce", None) is not None:
            wp.launch(dc_rack_div_kernel, dim=(self.n_rack, nch),
                      inputs=[self.r_flux, self.r_asum, int(drop_global), b["racc"], b["rden"]],
                      outputs=[b["rmean"]], device=dev)
        if self.n_rack_ghost:
            wp.launch(dc_rack_write_kernel, dim=(self.n_rack_ghost, nch),
                      inputs=[self.o_ghost, self.o_rack, self.o_remote, self._rows["odt"], self.o_lag, self._rows["sb"], b["rmean"]],
                      outputs=[b["q"]],
                      device=dev)
        wp.launch(dc_wlsq_kernel, dim=(n, 7, nch),
                  inputs=[b["q"], cpd, self.w_off, self.w_other, self.w_w, self.mir_off, self.mir_cpd,
                          int(freeze_grad), b["zt"], self.g_sel, self.g_off, self.g_other, self.g_w],
                  outputs=[b["grad"]], device=dev)
        wp.launch(dc_nut_kernel, dim=(self.n_cpd, nch), inputs=[b["q"], b["grad"], self.inv_src, kc, b["zt"]],
                  outputs=[b["nut"]], device=dev)
        wp.launch(dc_face_pre_kernel, dim=(self.n_face, nch), inputs=[b["q"], b["grad"], b["nut"], g, kc, b["zt"]],
                  outputs=[b["f0"], b["nutf"]], device=dev)
        wp.launch(dc_ap_kernel, dim=(n, nch),
                  inputs=[b["f0"], b["nutf"], cpd, self.c_off, self.c_face, self.c_sarea, self.c_abss, self.c_delta,
                          g.nu, self.c_dvol, int(frozen), self.mir_off, self.mir_cpd, b["zt"]], outputs=[b["d"]],
                  device=dev)
        wp.launch(dc_face_kernel, dim=(self.n_face, nch),
                  inputs=[b["q"], b["grad"], b["f0"], b["d"], b["nutf"], g, kc, b["zt"], int(freeze_flux)],
                  outputs=[b["flux"], b["fmom"], b["fsc"], wp.from_torch(self.face_volumetric_flux.reshape(-1)),
                           wp.from_torch(self.nu_eff_face.reshape(-1)), wp.from_torch(self.alpha_eff_face.reshape(-1))],
                  device=dev)
        wp.launch(dc_cell_kernel, dim=(n, nch),
                  inputs=[b["q"], b["grad"], b["nut"], b["nutf"], self._rows["qold"], b["flux"], b["fmom"], b["fsc"],
                          g.ccont, g.nu, cpd, self.c_off, self.c_face, self.c_sarea, self.c_vol, self.c_tcoef,
                          self.c_pcoef, self.c_ghbc, self.c_betac, self.c_gvec, self.c_src, self.c_nucell,
                          self.w_off_c, self.w_face, self.w_ghost, self.w_delta, self.w_ky, self.c_coff,
                          self.c_cghost, self.c_cform, kc, n, kdir, cw, int(want_scale), self._rows["sb"], seeded, p0, sk, sc, eoff,
                          ecol, b["zt"]],
                  outputs=[wp.from_torch(io["res_rows"]), wp.from_torch(io["tan"]), wp.from_torch(self._scale), vals],
                  device=dev)

    def _launch(self, io, kdir, cw, nch, frozen, want_scale=0, freeze_grad=0, drop_global=0, seed=None):
        """The launch sequence: load + local ghosts, the special ghosts (porous baffles, racks), WLSQ (+ cyclic ghost
        gradients), nu_t, face values, a_P / d, face fluxes, cell rows.  ``seed`` (compact assembly): ``(p0, colour of
        each cell, probe colours, probe fields, entry offsets, entry colours, block values)``."""
        red = getattr(self, "_preduce", None)
        two = red is not None and not getattr(red, "local_only", False) and self._two_stage_red()
        if two:
            self._launch_pre(io, kdir, cw, nch, drop_global=drop_global, seed=seed, two_stage=True)
            self._launch_reduce(cw, nch, stage="fro")
            self._launch_mid(cw, nch)
            self._launch_reduce(cw, nch, stage="rack")
            self._launch_post(io, kdir, cw, nch, frozen, want_scale=want_scale, freeze_grad=freeze_grad,
                              drop_global=drop_global, seed=seed, skip_fro_write=True)
        else:
            self._launch_pre(io, kdir, cw, nch, drop_global=drop_global, seed=seed)
            self._launch_reduce(cw, nch, stage="all")
            self._launch_post(io, kdir, cw, nch, frozen, want_scale=want_scale, freeze_grad=freeze_grad,
                              drop_global=drop_global, seed=seed)

    def _run(self, x, dirs, k, frozen=False, cw=None, freeze_grad=False, drop_global=False):
        if cw is None:
            cw = 0 if k == 0 else (1 if k == 1 else probe_chunk())
        cw = max(cw, 0)
        self.sync_coefficients()
        if self._lagged:
            self.sync_lagged(x)
        nch = 1 if k == 0 else -(-k // cw)
        kpad = nch * cw if k else 0
        io = self._io(kpad)
        ts = torch.cuda.current_stream(self.cs.device)
        ws, wts = self._stream_pair()
        wts.wait_stream(ts)
        with torch.cuda.stream(wts):
            io["x"].copy_(x)
            if k:
                io["dirs"][:k].copy_(dirs)
                if kpad > k:
                    io["dirs"][k:kpad].zero_()
            fl = {"freeze_grad": int(freeze_grad), "drop_global": int(drop_global)}
            red = getattr(self, "_preduce", None)
            if os.environ.get("LPCNS_WARP_GRAPH", "1") == "0" or getattr(self, "_in_torch_capture", False):
                with wp.ScopedStream(ws, sync_enter=False):
                    self._launch(io, kpad, cw, nch, frozen, **fl)
            elif red is None or getattr(red, "capturable", False):
                gk = ("graph", cw, nch, bool(frozen), bool(freeze_grad), bool(drop_global),
                      None if red is None else red.mode())
                graph = self._bufs.get(gk)
                if graph is None:
                    with wp.ScopedStream(ws, sync_enter=False):
                        self._launch(io, kpad, cw, nch, frozen, **fl)
                        with wp.ScopedCapture(stream=ws) as cap:
                            self._launch(io, kpad, cw, nch, frozen, **fl)
                    graph = self._bufs[gk] = cap.graph
                wp.capture_launch(graph, stream=ws)
            elif getattr(red, "local_only", False):
                self._launch_reduce(cw, nch, stage="all")
                gk = ("graph_loc", cw, nch, bool(frozen), bool(freeze_grad), bool(drop_global))
                graph = self._bufs.get(gk)
                if graph is None:
                    with wp.ScopedStream(ws, sync_enter=False):
                        self._launch_pre(io, kpad, cw, nch, drop_global=fl["drop_global"], local_red=True)
                        self._launch_post(io, kpad, cw, nch, frozen, **fl)
                        with wp.ScopedCapture(stream=ws) as cap:
                            self._launch_pre(io, kpad, cw, nch, drop_global=fl["drop_global"], local_red=True)
                            self._launch_post(io, kpad, cw, nch, frozen, **fl)
                    graph = self._bufs[gk] = cap.graph
                wp.capture_launch(graph, stream=ws)
            elif self._two_stage_red():
                gk_pre = ("graph_pre2", cw, nch, bool(drop_global))
                gk_mid = ("graph_mid2", cw, nch)
                gk_post = ("graph_post2", cw, nch, bool(frozen), bool(freeze_grad), bool(drop_global))
                g_pre, g_mid, g_post = self._bufs.get(gk_pre), self._bufs.get(gk_mid), self._bufs.get(gk_post)
                if g_pre is None or g_mid is None or g_post is None:
                    with wp.ScopedStream(ws, sync_enter=False):
                        self._launch_pre(io, kpad, cw, nch, drop_global=fl["drop_global"], two_stage=True)
                        with wp.ScopedCapture(stream=ws) as cap_pre:
                            self._launch_pre(io, kpad, cw, nch, drop_global=fl["drop_global"], two_stage=True)
                        self._bufs[gk_pre] = g_pre = cap_pre.graph
                        self._launch_reduce(cw, nch, stage="fro")
                        self._launch_mid(cw, nch)
                        with wp.ScopedCapture(stream=ws) as cap_mid:
                            self._launch_mid(cw, nch)
                        self._bufs[gk_mid] = g_mid = cap_mid.graph
                        self._launch_reduce(cw, nch, stage="rack")
                        self._launch_post(io, kpad, cw, nch, frozen, skip_fro_write=True, **fl)
                        with wp.ScopedCapture(stream=ws) as cap_post:
                            self._launch_post(io, kpad, cw, nch, frozen, skip_fro_write=True, **fl)
                        self._bufs[gk_post] = g_post = cap_post.graph
                else:
                    wp.capture_launch(g_pre, stream=ws)
                    self._launch_reduce(cw, nch, stage="fro")
                    wp.capture_launch(g_mid, stream=ws)
                    self._launch_reduce(cw, nch, stage="rack")
                    wp.capture_launch(g_post, stream=ws)
            else:
                gk_pre = ("graph_pre", cw, nch, bool(drop_global))
                gk_post = ("graph_post", cw, nch, bool(frozen), bool(freeze_grad), bool(drop_global))
                g_pre, g_post = self._bufs.get(gk_pre), self._bufs.get(gk_post)
                if g_pre is None or g_post is None:
                    with wp.ScopedStream(ws, sync_enter=False):
                        self._launch_pre(io, kpad, cw, nch, drop_global=fl["drop_global"])
                        with wp.ScopedCapture(stream=ws) as cap_pre:
                            self._launch_pre(io, kpad, cw, nch, drop_global=fl["drop_global"])
                        self._bufs[gk_pre] = g_pre = cap_pre.graph
                        self._launch_reduce(cw, nch, stage="all")
                        self._launch_post(io, kpad, cw, nch, frozen, **fl)
                        with wp.ScopedCapture(stream=ws) as cap_post:
                            self._launch_post(io, kpad, cw, nch, frozen, **fl)
                        self._bufs[gk_post] = g_post = cap_post.graph
                else:
                    wp.capture_launch(g_pre, stream=ws)
                    self._launch_reduce(cw, nch, stage="all")
                    wp.capture_launch(g_post, stream=ws)
        ts.wait_stream(wts)
        return io

    def residual(self, x: torch.Tensor) -> torch.Tensor:
        io = self._run(x.detach().reshape(-1).to(torch.float64), None, 0)
        return io["res"].clone()

    def residual_and_scale(self, x: torch.Tensor):
        self.sync_coefficients()
        if self._lagged:
            self.sync_lagged(x)
        io = self._io(0)
        ts = torch.cuda.current_stream(self.cs.device)
        ws, wts = self._stream_pair()
        wts.wait_stream(ts)
        with torch.cuda.stream(wts):
            io["x"].copy_(x.detach().reshape(-1).to(torch.float64))
            with wp.ScopedStream(ws, sync_enter=False):
                self._launch(io, 0, 0, 1, False, want_scale=1)
        ts.wait_stream(wts)
        return io["res"].clone(), self._scale.clone()

    def jvp(self, x: torch.Tensor, directions: torch.Tensor, frozen: bool = False, cw=None, picard: bool = False,
            drop_global: bool = False):
        """``(F(x), J d_k for each row d_k of directions [K, N])`` in one pass.  ``frozen``: d_f frozen; ``picard``: the
        cell gradients frozen too (with ``frozen``, the operator ``compact_jacobian(mode="picard")`` assembles);
        ``drop_global``: the CBBM mean's tangent dropped."""
        x = x.detach().reshape(-1).to(torch.float64)
        dirs = directions.detach().reshape(-1, x.numel()).to(torch.float64)
        k = int(dirs.shape[0])
        io = self._run(x, dirs, k, frozen, cw, freeze_grad=picard, drop_global=drop_global)
        return io["res"].clone(), io["tan"][:k].clone()

    def warm(self, ks=(0, 1, 16), frozen=(False,)):
        x = torch.zeros(self.n * 7, dtype=torch.float64, device=self.cs.device)
        for fz in frozen:
            for k in ks:
                if k:
                    self.jvp(x, torch.zeros(k, x.numel(), dtype=torch.float64, device=x.device), frozen=fz)
                else:
                    self.residual(x)

    # ------------------------------------------------------------------ compact Jacobian
    def adjacency(self):
        """The 1-hop cell graph ``(row, col)`` of the interior cells (both directions, no self loops): interior faces,
        cyclic partners and IBBM rack pairs (the couplings a face-neighbour block can hold)."""
        if "adj" in self._compact:
            return self._compact["adj"]
        app = self.app
        tdev = self.cs.device
        row = torch.full((self.n_cpd,), -1, dtype=torch.int64, device=tdev)
        row[self.cpd_of] = torch.arange(self.n, device=tdev)
        C, F = app.C_senders.long(), app.F_receivers.long()
        Fm = torch.where(self._partner_t[F] >= 0, self._partner_t[F], F)
        a, b = row[C], row[Fm]
        keep = (a >= 0) & (b >= 0) & (a != b)
        pairs = [torch.stack([a[keep], b[keep]])]
        rk = self._rack_t
        if rk["ghost"].numel():
            rem = rk["remote"]
            sel = rem >= 0
            if bool(sel.any()):
                owner = self._src_t[rk["ghost"][sel]]
                pairs.append(torch.stack([row[owner], row[rem[sel]]]))
        e = torch.cat(pairs, dim=1)
        e = torch.cat([e, e.flip(0)], dim=1)
        key = torch.unique(e[0] * self.n + e[1])
        out = (key // self.n, key % self.n)
        self._compact["adj"] = out
        return out

    def _two_hop_csr(self):
        if "csr_2hop" in self._compact:
            return self._compact["csr_2hop"]
        from SparseSolver.sparsity_device import two_hop_pattern as dev_two_hop
        r1, c1 = self.adjacency()
        csr2 = dev_two_hop(r1, c1, self.n)
        ptr = csr2.indptr
        col = csr2.indices
        row = torch.repeat_interleave(torch.arange(self.n, device=col.device), ptr[1:] - ptr[:-1])
        diag = torch.nonzero(row == col).reshape(-1)
        out = (row, col, ptr, diag)
        self._compact["csr_2hop"] = out
        return out

    def colouring(self, hops: int):
        """Greedy colouring of the interior cells with no two cells of a colour within ``hops`` faces of the 1-hop
        graph; ``(colour of each cell [n], number of colours)``, cached per ``hops``."""
        hops = int(hops)
        key = ("colour", hops)
        if key in self._compact:
            return self._compact[key]
        tdev = self.cs.device
        if tdev.type == "cuda" and hops in (2, 3, 4) and self.n > 0:
            _, col1, ptr1, _ = self.compact_pattern()
            if hops == 2:
                ptr_a, col_a, ptr_b, col_b = ptr1, col1, ptr1, col1
            elif hops == 3:
                _, col2, ptr2, _ = self._two_hop_csr()
                ptr_a, col_a, ptr_b, col_b = ptr2, col2, ptr1, col1
            else:
                _, col2, ptr2, _ = self._two_hop_csr()
                ptr_a, col_a, ptr_b, col_b = ptr2, col2, ptr2, col2
            pa32 = ptr_a.to(torch.int32).contiguous()
            ca32 = col_a.to(torch.int32).contiguous()
            pb32 = ptr_b.to(torch.int32).contiguous()
            cb32 = col_b.to(torch.int32).contiguous()
            c_in = torch.full((self.n,), -1, dtype=torch.int32, device=tdev)
            c_out = torch.full((self.n,), -1, dtype=torch.int32, device=tdev)
            rem = torch.zeros(1, dtype=torch.int32, device=tdev)
            idx = torch.arange(self.n, dtype=torch.int64, device=tdev)
            h = ((idx ^ 61) ^ (idx >> 16)) * 9
            h = h ^ (h >> 4)
            h = (h * 0x27d4eb2d) & 0x7FFFFFFF
            prio = ((h << 32) | (self.n - 1 - idx)).contiguous()
            wpa, wca = wp.from_torch(pa32), wp.from_torch(ca32)
            wpb, wcb = wp.from_torch(pb32), wp.from_torch(cb32)
            wpr, wrem = wp.from_torch(prio), wp.from_torch(rem)
            rounds = 0
            while rounds < 2048:
                rem.zero_()
                wp.launch(
                    _dc_jp_product_greedy_step, dim=self.n,
                    inputs=[wpa, wca, wpb, wcb, wpr, wp.from_torch(c_in), wp.from_torch(c_out), wrem],
                    device=self.dev,
                )
                c_in, c_out = c_out, c_in
                rounds += 1
                if rounds >= 15 and rounds % 5 == 0 and int(rem.item()) == 0:
                    break
            if int(rem.item()) == 0:
                ncol = int(c_in.max().item()) + 1
                if ncol <= 256:
                    out = (c_in.to(torch.int64), ncol)
                    self._compact[key] = out
                    return out
        import scipy.sparse as sp
        r, c = (t.cpu().numpy() for t in self.adjacency())
        n = self.n
        a = sp.csr_matrix((np.ones(r.size, dtype=np.float32), (r, c)), shape=(n, n))
        ak, power = a.copy(), a.copy()
        for _ in range(int(hops) - 1):
            power = (power @ a).tocsr()
            power.data[:] = 1.0
            ak = (ak + power).tocsr()
        ak.setdiag(0)
        ak.eliminate_zeros()
        ptr, ind = ak.indptr, ak.indices
        colour = np.full(n, -1, dtype=np.int64)
        mark = np.full(n + 1, -1, dtype=np.int64)
        for i in range(n):
            nb = colour[ind[ptr[i]:ptr[i + 1]]]
            nb = nb[nb >= 0]
            mark[nb] = i
            k = 0
            while mark[k] == i:
                k += 1
            colour[i] = k
        out = (torch.as_tensor(colour, device=self.cs.device), int(colour.max()) + 1)
        self._compact[key] = out
        return out

    def compact_pattern(self):
        """``(row, col, ptr, diag)`` of the 1-hop block pattern (self + adjacency), sorted by row then column."""
        if "pattern" in self._compact:
            return self._compact["pattern"]
        r, c = self.adjacency()
        n = self.n
        ar = torch.arange(n, device=r.device)
        key = torch.unique(torch.cat([r * n + c, ar * n + ar]))
        row, col = key // n, key % n
        ptr = torch.zeros(n + 1, dtype=torch.int64, device=r.device)
        ptr[1:] = torch.cumsum(torch.bincount(row, minlength=n), 0)
        diag = torch.nonzero(row == col).reshape(-1)
        out = (row, col, ptr, diag)
        self._compact["pattern"] = out
        return out

    def compact_jacobian(self, x: torch.Tensor, mode: str = "frozen", drop_global: bool = True,
                         probes_per_launch: int | None = None, fields: tuple | None = None) -> CompactJacobian:
        """The diagonal and face-neighbour 7 x 7 blocks at ``x`` (module docstring).  ``mode = "frozen"``: d_f frozen,
        exact 1-hop blocks of that Jacobian (distance-3 colouring); ``"picard"``: the cell gradients and d_f frozen
        too (distance-2 colouring).  ``drop_global``: the CBBM mean's tangent dropped (its dense block omitted).
        ``probes_per_launch`` (default LPCNS_WARP_DC_PROBES, 32: the buffers of the batched JVP): tangents per launch, in chunks of ``probe_chunk()``."""
        from Pipeline.CoupledNewton.block_amg import BlockCSR
        if mode not in ("frozen", "picard", "oseen"):
            raise ValueError(f"compact_jacobian mode {mode!r}: 'frozen', 'picard' or 'oseen'")
        t0 = torch.cuda.Event(enable_timing=True)
        t1 = torch.cuda.Event(enable_timing=True)
        t0.record()
        hops = 3 if mode == "frozen" else 2
        colour, ncol = self.colouring(hops)
        row, col, ptr, diag = self.compact_pattern()
        nnzb = int(row.numel())
        # fields: probe only these column fields (the other block columns stay zero), e.g. (3,) the pressure columns
        fsel = tuple(range(7)) if fields is None else tuple(int(f) for f in fields)
        pkey = ("plan", hops) if fields is None else ("plan", hops, fsel)
        # LPCNS_WARP_DC_VALS32=1: the blocks accumulated in float32 (a preconditioner's use; the tangents stay float64)
        # -- halves the build's transient (2.8 -> 1.4 GB at 1.1M cells, where the 24 GB cards otherwise run out)
        vdt = torch.float32 if os.environ.get("LPCNS_WARP_DC_VALS32", "0") == "1" else torch.float64
        if pkey not in self._compact:
            nfs = len(fsel)
            P = ncol * nfs
            pk = torch.arange(P, device=colour.device) // nfs
            pc = torch.as_tensor(fsel, device=colour.device)[torch.arange(P, device=colour.device) % nfs]
            self._compact[pkey] = {"scol": _i32(colour), "sk": _i32(pk), "sc": _i32(pc), "P": P,
                                   "eoff": _i32(ptr), "ecol": _i32(colour[col]),
                                   "vals": torch.zeros(nnzb, 7, 7, dtype=vdt, device=colour.device)}
        plan = self._compact[pkey]
        if "vals" not in plan:
            plan["vals"] = torch.zeros(nnzb, 7, 7, dtype=vdt, device=colour.device)
        vals = plan["vals"]
        vals.zero_()
        cw = probe_chunk()
        default_probes = "8" if self.n > 500_000 else "32"
        per = int(probes_per_launch or os.environ.get("LPCNS_WARP_DC_PROBES", default_probes))
        nch = max(1, -(-per // cw))
        kpad = nch * cw
        self.sync_coefficients()
        if self._lagged:
            self.sync_lagged(x)
        io = self._io(0)
        io["x"].copy_(x.detach().reshape(-1).to(torch.float64))
        P = plan["P"]
        fgrad = 1 if mode in ("picard", "oseen") else 0
        fflux = 1 if mode == "oseen" else 0          # "oseen": picard + the convecting flux frozen
        dglob = 1 if drop_global else 0
        red = getattr(self, "_preduce", None)
        loc_red = red is not None
        ts = torch.cuda.current_stream(self.cs.device)
        ws, wts = self._stream_pair()
        wts.wait_stream(ts)
        vw = wp.from_torch(vals)
        with torch.cuda.stream(wts):
            if loc_red:
                prev_loc = red.local_only
                red.local_only = True
                try:
                    self._launch_reduce(cw, nch)
                finally:
                    red.local_only = prev_loc
            use_graph = (os.environ.get("LPCNS_WARP_GRAPH", "1") != "0"
                         and not getattr(self, "_in_torch_capture", False)
                         and self.n <= 500_000)
            gk = ("graph_1hop", hops, cw, nch, bool(fgrad), bool(fflux), bool(dglob), bool(loc_red), fsel)
            graph = self._compact.get(gk) if use_graph else None
            if graph is not None:
                wp.capture_launch(graph, stream=ws)
            else:
                with wp.ScopedStream(ws, sync_enter=False):
                    for p0 in range(0, P, kpad):
                        kdir = min(kpad, P - p0)
                        sd = (p0, plan["scol"], plan["sk"], plan["sc"], plan["eoff"], plan["ecol"], vw)
                        self._launch_pre(io, kdir, cw, nch, drop_global=dglob, seed=sd, local_red=loc_red)
                        self._launch_post(io, kdir, cw, nch, True, freeze_grad=fgrad, drop_global=dglob, seed=sd,
                                          freeze_flux=fflux)
                    if use_graph:
                        vals.zero_()
                        with wp.ScopedCapture(stream=ws) as cap:
                            for p0 in range(0, P, kpad):
                                kdir = min(kpad, P - p0)
                                sd = (p0, plan["scol"], plan["sk"], plan["sc"], plan["eoff"], plan["ecol"], vw)
                                self._launch_pre(io, kdir, cw, nch, drop_global=dglob, seed=sd, local_red=loc_red)
                                self._launch_post(io, kdir, cw, nch, True, freeze_grad=fgrad, drop_global=dglob,
                                                  seed=sd, freeze_flux=fflux)
                        self._compact[gk] = graph = cap.graph
                if graph is not None:
                    wp.capture_launch(graph, stream=ws)
        ts.wait_stream(wts)
        out_vals = vals.clone() if use_graph else plan.pop("vals")
        t1.record()
        t1.synchronize()
        if not use_graph and os.environ.get("LPCNS_WARP_DC_FREE", "0") == "1":
            # LPCNS_WARP_DC_FREE=1: the probe chunk's dual buffers (~4.7 GB at 1.1M cells for 8 tangents) and the
            # direction / tangent arrays are released after the build; no graph holds their addresses (n > 500k),
            # and the next build (every tens of steps) allocates them again
            self._bufs.pop(("b", cw, nch), None)
            if kpad > 1:
                self._bufs.pop(("io", kpad), None)     # the build's 8-wide direction / tangent arrays (~1 GB)
            if not getattr(self, "_pool_released", False):
                # Warp's pool then hands freed blocks back to the device at each synchronisation, so the next build's
                # buffers and torch see one free pool (PERIM_HAC failed a late re-allocation without it)
                if wp.is_mempool_enabled(self.dev):
                    wp.set_mempool_release_threshold(self.dev, 0)
                self._pool_released = True
            wp.synchronize_device(self.dev)
            if os.environ.get("LPCNS_WARP_DC_FREE_LOG", "0") == "1":
                fr, tot = torch.cuda.mem_get_info(self.cs.device)
                print(f"compact_jacobian: probe buffers released, device free {fr / 2**30:.2f} of "
                      f"{tot / 2**30:.2f} GB", flush=True)
        A = BlockCSR(row, col, out_vals, (self.n, self.n))
        A._ptr = ptr
        A._diag = diag
        return CompactJacobian(A, diag, self.n, mode, ncol, P, t0.elapsed_time(t1) / 1e3)

    def two_hop_pattern(self):
        """``(row, col, ptr, diag, colour, ncol)`` of the 2-hop structural block pattern, sorted by row then column."""
        if "pattern_2hop" in self._compact:
            return self._compact["pattern_2hop"]
        row, col, ptr, diag = self._two_hop_csr()
        colour, ncol = self.colouring(4)
        from Pipeline.CoupledNewton import block_amg
        block_amg.register_root_order(self.n, colour)
        out = (row, col, ptr, diag, colour, int(ncol))
        self._compact["pattern_2hop"] = out
        return out

    def two_hop_jacobian(self, x: torch.Tensor, mode: str = "implicit", drop_global: bool = True,
                         probes_per_launch: int | None = None) -> CompactJacobian:
        """The 2-hop 7 x 7 blocks at ``x`` assembled directly in-kernel via distance-4 colouring.
        ``mode = "implicit"`` (default): full Jacobian (d_f and WLSQ gradients active);
        ``"frozen"``: d_f frozen; ``"picard"``: d_f and cell gradients frozen."""
        from Pipeline.CoupledNewton.block_amg import BlockCSR
        if mode not in ("implicit", "frozen", "picard"):
            raise ValueError(f"two_hop_jacobian mode {mode!r}: 'implicit', 'frozen' or 'picard'")
        t0 = torch.cuda.Event(enable_timing=True)
        t1 = torch.cuda.Event(enable_timing=True)
        t0.record()
        row, col, ptr, diag, colour, ncol = self.two_hop_pattern()
        nnzb = int(row.numel())
        pkey = "plan_2hop"
        if pkey not in self._compact:
            P = ncol * 7
            pk = torch.arange(P, device=colour.device) // 7
            pc = torch.arange(P, device=colour.device) % 7
            self._compact[pkey] = {"scol": _i32(colour), "sk": _i32(pk), "sc": _i32(pc), "P": P,
                                   "eoff": _i32(ptr), "ecol": _i32(colour[col]),
                                   "vals": torch.zeros(nnzb, 7, 7, dtype=torch.float64, device=colour.device)}
        plan = self._compact[pkey]
        if "vals" not in plan:
            plan["vals"] = torch.zeros(nnzb, 7, 7, dtype=torch.float64, device=colour.device)
        vals = plan["vals"]
        vals.zero_()
        cw = probe_chunk()
        default_probes = "8" if self.n > 500_000 else "32"
        per = int(probes_per_launch or os.environ.get("LPCNS_WARP_DC_PROBES", default_probes))
        nch = max(1, -(-per // cw))
        kpad = nch * cw
        self.sync_coefficients()
        if self._lagged:
            self.sync_lagged(x)
        io = self._io(0)
        io["x"].copy_(x.detach().reshape(-1).to(torch.float64))
        P = plan["P"]
        frozen = mode != "implicit"
        fgrad = 1 if mode == "picard" else 0
        dglob = 1 if drop_global else 0
        red = getattr(self, "_preduce", None)
        loc_red = red is not None
        ts = torch.cuda.current_stream(self.cs.device)
        ws, wts = self._stream_pair()
        wts.wait_stream(ts)
        vw = wp.from_torch(vals)
        with torch.cuda.stream(wts):
            if loc_red:
                prev_loc = red.local_only
                red.local_only = True
                try:
                    self._launch_reduce(cw, nch)
                finally:
                    red.local_only = prev_loc
            use_graph = (os.environ.get("LPCNS_WARP_GRAPH", "1") != "0"
                         and not getattr(self, "_in_torch_capture", False)
                         and nnzb <= 8_000_000)
            gk = ("graph_2hop", cw, nch, bool(frozen), bool(fgrad), bool(dglob), bool(loc_red))
            graph = self._compact.get(gk) if use_graph else None
            if graph is not None:
                wp.capture_launch(graph, stream=ws)
            else:
                with wp.ScopedStream(ws, sync_enter=False):
                    for p0 in range(0, P, kpad):
                        kdir = min(kpad, P - p0)
                        sd = (p0, plan["scol"], plan["sk"], plan["sc"], plan["eoff"], plan["ecol"], vw)
                        self._launch_pre(io, kdir, cw, nch, drop_global=dglob, seed=sd, local_red=loc_red)
                        self._launch_post(io, kdir, cw, nch, frozen, freeze_grad=fgrad, drop_global=dglob, seed=sd)
                    if use_graph:
                        vals.zero_()
                        with wp.ScopedCapture(stream=ws) as cap:
                            for p0 in range(0, P, kpad):
                                kdir = min(kpad, P - p0)
                                sd = (p0, plan["scol"], plan["sk"], plan["sc"], plan["eoff"], plan["ecol"], vw)
                                self._launch_pre(io, kdir, cw, nch, drop_global=dglob, seed=sd, local_red=loc_red)
                                self._launch_post(io, kdir, cw, nch, frozen, freeze_grad=fgrad, drop_global=dglob,
                                                  seed=sd)
                        self._compact[gk] = graph = cap.graph
                if graph is not None:
                    wp.capture_launch(graph, stream=ws)
        ts.wait_stream(wts)
        out_vals = vals.clone() if use_graph else plan.pop("vals")
        pin = getattr(self.cs, "_pin", None)
        if pin is not None:
            pmask = pin[0].reshape(7, self.n).t()
            prow, pf = torch.nonzero(pmask, as_tuple=True)
            if prow.numel():
                out_vals[pmask[row]] = 0.0
                out_vals[diag[prow], pf, pf] = 1.0
        shift = self.cs._ptc_shift
        if shift is not None:
            s = shift[0].reshape(7, self.n).t()
            ar7 = torch.arange(7, device=out_vals.device)
            out_vals[diag[:, None], ar7[None, :], ar7[None, :]] += s
        t1.record()
        t1.synchronize()
        A = BlockCSR(row, col, out_vals, (self.n, self.n))
        A._ptr = ptr
        A._diag = diag
        return CompactJacobian(A, diag, self.n, mode, ncol, P, t0.elapsed_time(t1) / 1e3)


def ncc_g(app):
    """``(owner, neighbour)`` compound-cell ids per face."""
    return app.neighbor_cpd_cell[0].long(), app.neighbor_cpd_cell[1].long()


class DCWarpBackend:
    """What ``CaseState.residual`` and ``jacobian.linearise`` call when a buoyant k-epsilon case runs on Warp (the
    contract of ``buoyantBoussinesqpicoext_warp.WarpBackend``): ``rows(x, old_state)`` (and the face flux / nu_eff /
    alpha_eff stashes the preconditioner reads), ``product(cs, x, coefficient)`` with ``.batched``, and
    ``compact_jacobian(x, ...)`` (``DCWarpResidual.compact_jacobian``)."""

    def __init__(self, cs, warm: bool = True, residual=None):
        self.cs = cs
        self.W = DCWarpResidual(cs) if residual is None else residual
        default_blk = "8" if int(getattr(cs, "n_cell", 0)) > 500_000 else "32"
        self.block = int(os.environ.get("LPCNS_WARP_DC_BLOCK", default_blk))
        if warm:
            frozen = (False, True) if cs.solver._mwi_frozen_coefficient else (False,)
            self.W.warm((0, 1, self.block), frozen)

    def _refuse(self, cs):
        if getattr(cs, "_operator_correction", None) is not None:
            raise NotImplementedError("warp backend: the learned operator correction reads the torch forward's state")

    def _publish(self):
        """The per-forward stashes the Newton driver reads off the torch application: the advecting flux and nu_eff
        (the preconditioner), alpha_eff, and the boundary-filled fields (``bc_t_cpd_cell`` is what the outer CBBM
        pass reads), as views of the last residual's state."""
        app = self.cs.solver
        app.face_volumetric_flux = self.W.face_volumetric_flux
        app._nu_eff_face = self.W.nu_eff_face
        app._alpha_eff_face = self.W.alpha_eff_face
        q = self.W.compound_state()
        app.bc_uvw_cpd_cell, app.bc_p_rgh_cpd_cell, app.bc_t_cpd_cell = q[:, 0:3], q[:, 3:4], q[:, 4:5]
        app.bc_k_cpd_cell, app.bc_epsilon_cpd_cell = q[:, 5:6], q[:, 6:7]

    def rows(self, x, old_state=None):
        self._refuse(self.cs)
        self.W.set_old_state(old_state)
        out = self.W.residual(x)
        self._publish()
        return out

    def compact_jacobian(self, x, mode="frozen", drop_global=True, fields=None):
        self.W.set_old_state(getattr(self.cs, "_old_state", None))
        return self.W.compact_jacobian(x, mode=mode, drop_global=drop_global, fields=fields)

    def two_hop_jacobian(self, x, mode=None, drop_global=True):
        if mode is None:
            mode = "frozen" if self.cs.solver._mwi_frozen_coefficient else "implicit"
        self.W.set_old_state(getattr(self.cs, "_old_state", None))
        return self.W.two_hop_jacobian(x, mode=mode, drop_global=drop_global)

    def product(self, cs, x, coefficient=None, differentiable=False):
        if differentiable:
            raise NotImplementedError("warp backend: differentiable linearisations (training) stay on torch")
        self._refuse(cs)
        if coefficient is None:
            coefficient = "frozen" if cs.solver._mwi_frozen_coefficient else "implicit"
        frozen = coefficient == "frozen"
        W = self.W
        W.set_old_state(getattr(cs, "_old_state", None))
        point = x.detach().reshape(-1).to(torch.float64).contiguous()
        pin = getattr(cs, "_pin", None)
        shift = cs._ptc_shift
        blk = self.block

        def finish(Jd, d):
            if pin is not None:
                Jd = torch.where(pin[0], d, Jd)
            if shift is not None:
                Jd = Jd + shift[0] * d
            return Jd

        def product(direction):
            d = direction.detach().reshape(1, -1).to(torch.float64)
            _, Jd = W.jvp(point, d, frozen=frozen)
            return finish(Jd[0], d[0])

        def batched(directions):
            d = directions.detach().reshape(-1, point.numel()).to(torch.float64)
            out = []
            for s in range(0, d.shape[0], blk):
                b = d[s:s + blk]
                m = b.shape[0]
                if m < blk:
                    b = torch.cat([b, b.new_zeros(blk - m, b.shape[1])], 0)
                _, Jd = W.jvp(point, b, frozen=frozen)
                out.append(finish(Jd[:m], d[s:s + m]))
            return torch.cat(out, 0)

        def set_point(x_new):
            W.set_old_state(getattr(cs, "_old_state", None))
            point.copy_(x_new.detach().reshape(-1).to(torch.float64))
            W.sync_coefficients()
            if W._lagged:
                W.sync_lagged(point)

        product.point = point
        product.set_point = set_point
        product.batched = batched
        return product


def supports(app) -> bool:
    """True when ``app`` is a buoyantBoussinesqpicoext case with a k-epsilon closure this module implements (the
    entry's dispatch; the full configuration is checked, and refused by name, when the backend is built)."""
    tm = getattr(app, "turbulence", None)
    return (hasattr(app, "alpha_coeff") and hasattr(app, "gh_face")
            and type(tm).__name__ in ("KEpsilon", "RealizableKE"))


def attach_warp_dc(cs):
    """Put ``cs`` (a CollocatedSolver over a buoyant k-epsilon case) on the Warp backend; returns the backend."""
    cs._warp = DCWarpBackend(cs)
    if int(getattr(cs, "assembly_batch", 1) or 1) <= 1:
        cs.assembly_batch = cs._warp.block
    return cs._warp
