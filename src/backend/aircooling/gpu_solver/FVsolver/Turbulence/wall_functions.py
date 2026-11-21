"""OpenFOAM v2412 wall-function coefficients and the nutkWallFunction algebra.

One concern: the numbers and the wall-face eddy-viscosity law that every
high-Re wall function in v2412 shares, transcribed from
``Ref_code/openfoam/src/TurbulenceModels/turbulenceModels/derivedFvPatchFields/
wallFunctions/``.  The transported-equation wall conditions themselves live
where they belong -- the epsilon constraint in the BC layer
(FVdomain/SetBC/onthefly/setEpsilon.py), the wall-cell production override in
EddyViscosityModel.wall_function_production.

Of the reference code's five viscous/inertial sublayer blenders
(wallFunctionBlenders.H), three are transcribed: STEPWISE (the per-BC default),
TANH (nut only) and EXPONENTIAL (Popovac and Hanjalic 2007), in both the
nut and the epsilon wall functions (``nutk_wall_nut``, ``epsilon_wall_blend``).
EXPONENTIAL is the blending of the wall-function flat plates, chosen because it
is the one that is smooth in y+ everywhere: its log-layer branch carries the
weight ``exp(-1/Gamma)``, which vanishes with all its derivatives below
y+ ~ 1, so the clamp ``max(E y+, 1 + 1e-4)`` inside ``nutLog`` (a kink and a
spike near y+ = 0.102) never reaches the residual.  The binomial blend carries
``nutLog`` unweighted and inherits that kink, and its default exponent differs
between the two wall functions (4 in nutkWallFunction, 2 in
epsilonWallFunction).  The stepwise switch of nut_w at yPlusLam is a kink in
y+, and at a leading edge it leaves the viscous side without a finite root for
Newton's method.  The low-Re ``lowReCorrection`` branch of the epsilon wall
function stays off, its default (kepsilon.py's module docstring).

Also here (added with RealizableKE): ``nutkWallFunction::yPlus()`` and the
incompressible ``alphatJayatillekeWallFunction`` algebra (``nutk_y_plus``,
``jayatilleke_p``, ``jayatilleke_y_plus_therm``, ``alphat_jayatilleke``).
"""

import math

import torch

# OpenFOAM's ROOTVSMALL in double precision (sqrt of VSMALL = 1e-300): the
# guard the EXPONENTIAL blender adds to Gamma before inverting it
# (v2506 nutkWF.C:131-144, epsWF.C:286-302).
ROOTVSMALL = 1.0e-150


def y_plus_lam(kappa: float, e: float) -> float:
    """y+ at the intersection of the linear and log sublayers.

    v2412 does NOT solve this: it runs a fixed-point recursion EXACTLY ten
    times with no convergence test
    (wallFunctionCoefficients.C:38-52, ``calcYPlusLam``)::

        ypl = 11;  for (iter = 0; iter < 10; ++iter)
            ypl = log(max(E*ypl, 1))/kappa;

    The recursion is reproduced rather than the number it converges to at the
    defaults (11.5301073043 at kappa = 0.41, E = 9.8), so a non-default
    ``kappa``/``E`` in a bc toml lands on the same value OpenFOAM would use.
    Ten sweeps is also not converged in general -- at defaults it is, to 11
    digits, but at a small ``E`` it is not, and matching the truncation is the
    point of transcribing the loop.
    """
    if kappa <= 0.0:
        raise ValueError(f"[wall_functions] kappa must be > 0, got {kappa}")
    if e <= 0.0:
        raise ValueError(f"[wall_functions] E must be > 0, got {e}")
    ypl = 11.0
    for _ in range(10):
        ypl = math.log(max(e * ypl, 1.0)) / kappa
    return ypl


def nutk_wall_nut(nu_w: torch.Tensor, y_w: torch.Tensor,
                  sqrt_k_c: torch.Tensor, cmu: float, kappa: float,
                  e: float, ypluslam: float,
                  blender: str = "stepwise") -> torch.Tensor:
    """``nutkWallFunction``'s wall-face eddy viscosity (nutkWF.C:38-170).

    ::

        yPlus  = Cmu^0.25 * y * sqrt(k_cell) / nu          (nutkWF.C:69-72)
        nutLog = nu * yPlus * kappa / log(max(E*yPlus, 1 + 1e-4))
                                                           (nutkWF.C:75-83)
        STEPWISE: nutw = (yPlus > yPlusLam) ? nutLog : nu  (nutkWF.C:90-104)
        nutw -= nu                                         (nutkWF.C:167)

    That last line is the one implementations drop: **the wall-face nut is the
    EXCESS over the molecular viscosity**, so it is exactly 0 below the
    switch, and ``nu + nut_w`` (which is what the momentum row and the G
    override consume) is the total effective wall viscosity.

    ``sqrt_k_c`` is the caller's FLOORED sqrt of k in the wall-adjacent CELL
    -- v2412 reads ``k[faceCells[facei]]`` (nutkWF.C:71), never a face value,
    and its own protection is ``bound(k, kMin)`` on the volume field
    (kEpsilon.C:290).

    ``blender`` selects among v2412's five (nutkWF.C:88-167).  STEPWISE is the
    per-BC default (nutkWF.C:216) and the default here.  Its switch is a KINK,
    not a jump: ``yPlusLam`` is DEFINED as the root of
    ``yPlus*kappa/log(E*yPlus) = 1`` (y_plus_lam above), so the two branches
    agree to 9 digits there and only the slope steps (from 0 to
    ``nu*kappa*(log(E y+) - 1)/log^2(E y+)`` ~ 0.068 nu per unit y+ at the
    defaults).  **What STEPWISE really imposes is not a discontinuity but a
    FLAT ZERO over the entire viscous branch.**

    ``"tanh"`` (nutkWF.C:146-165, KAS Eqs. 33-34) is smooth, positive and
    non-negligible at the y+ a quiet freestream starts from; measured (tabulated
    in section 13.1 of the wall-function validation note): at ``y+`` = 0.315
    the five give ``nut_w/nu`` = 0 (STEPWISE), 0 (MAX -- identically STEPWISE
    through the viscous branch, since ``nutLog < nutVis`` there and :167
    subtracts it), 4.31e-05 (BINOMIAL at v2412's shipped ``n`` = 4), -3.82e-05
    (EXPONENTIAL) and 6.15e-02 (TANH).  Its ``b2`` term carries ``nutLog``
    unweighted, so it keeps the kink of the clamp below.

    ``"exponential"`` (v2506 nutkWF.C:131-144, Popovac and Hanjalic 2007,
    Eq. 31)::

        Gamma = 0.01 y+^4 / (1 + 5 y+)
        nutw  = nu exp(-Gamma) + nutLog exp(-1 / (Gamma + ROOTVSMALL))

    is smooth in y+ everywhere: ``exp(-1/Gamma)`` is zero in double precision,
    with all its derivatives, below y+ ~ 1, where the clamp sits.  Its excess
    ``nut_w`` is negative between y+ ~ 0 and ~ 14 (at least -0.36 nu, near
    y+ = 8); ``nu + nut_w``, the only quantity the momentum row and the G
    override consume, stays positive.  MAX and BINOMIAL are not implemented,
    and a case naming them is refused rather than silently given STEPWISE.

    The ``max(E*yPlus, 1 + 1e-4)`` floor (nutkWF.C:81) is what keeps the
    logarithm positive; it also keeps ``nutLog`` finite on the branch
    ``torch.where`` does not select, which a NaN there would otherwise
    poison through the masked backward.
    """
    yplus = (cmu ** 0.25) * y_w * sqrt_k_c / nu_w
    nut_log = (nu_w * yplus * kappa
               / torch.log(torch.clamp(e * yplus, min=1.0 + 1e-4)))
    if blender == "stepwise":
        nut_total = torch.where(yplus > ypluslam, nut_log, nu_w)
    elif blender == "tanh":
        # KAS Eqs. 33-34, nutkWF.C:146-165.  b2's 1.2 exponent and the
        # (0.1 y+)^4 inside the tanh are v2412's literals, not choices here.
        b1 = nu_w + nut_log
        b2 = (nu_w ** 1.2 + nut_log ** 1.2) ** (1.0 / 1.2)
        phi = torch.tanh((0.1 * yplus) ** 4)
        nut_total = phi * b1 + (1.0 - phi) * b2
    elif blender == "exponential":
        gamma = 0.01 * yplus ** 4 / (1.0 + 5.0 * yplus)
        nut_total = (nu_w * torch.exp(-gamma)
                     + nut_log * torch.exp(-1.0 / (gamma + ROOTVSMALL)))
    else:
        raise ValueError(
            f"unknown nut_w blender {blender!r}; implemented: 'stepwise' "
            "(the per-BC default), 'tanh' and 'exponential'.  The reference "
            "code also offers max and binomial; max is identically stepwise "
            "through the viscous branch, and binomial carries nutLog's clamp "
            "unweighted -- see this function's docstring")
    return nut_total - nu_w


def epsilon_wall_blend(eps_vis: torch.Tensor, eps_log: torch.Tensor,
                       yplus: torch.Tensor, blender: str = "stepwise") -> torch.Tensor:
    """``epsilonWallFunction``'s wall-cell epsilon from its two sublayer values.

    ::

        epsilonVis = 2 k nu / y^2                          (v2506 epsWF.C:217-224)
        epsilonLog = Cmu^0.75 k^1.5 / (kappa y)            (epsWF.C:227-234)
        STEPWISE (lowReCorrection off, the default): epsilonLog at every y+
                                                           (epsWF.C:238-256)
        EXPONENTIAL: Gamma = 0.001 y+^4 / (1 + y+)
                     epsilonVis exp(-Gamma) + epsilonLog exp(-1 / (Gamma + ROOTVSMALL))
                                                           (epsWF.C:286-302)

    with ``y+ = Cmu^0.25 y sqrt(k) / nu`` (epsWF.C:208-214).  The EXPONENTIAL
    Gamma is the epsilon form of Popovac and Hanjalic (2007, p. 193), not the
    nut form of ``nutk_wall_nut``; both are the reference code's literals.
    """
    if blender == "stepwise":
        return eps_log
    if blender == "exponential":
        gamma = 0.001 * yplus ** 4 / (1.0 + yplus)
        return eps_vis * torch.exp(-gamma) + eps_log * torch.exp(-1.0 / (gamma + ROOTVSMALL))
    raise ValueError(f"unknown epsilon wall blender {blender!r}; implemented: 'stepwise' (the per-BC default, "
                     "lowReCorrection off) and 'exponential'")


# ----------------------------------------------------------------------------
# nutkWallFunction::yPlus() and the incompressible alphatJayatillekeWallFunction
# (added with RealizableKE).  Line numbers are OpenFOAM v2406's; ``aJWF.C`` is
# src/TurbulenceModels/incompressible/turbulentTransportModels/
# derivedFvPatchFields/wallFunctions/alphatWallFunctions/
# alphatJayatillekeWallFunction/alphatJayatillekeWallFunctionFvPatchScalarField.C
# ----------------------------------------------------------------------------

# aJWF.C:45-46 -- static members of the patch class, not dictionary entries.
JAYATILLEKE_TOLERANCE = 0.01
JAYATILLEKE_MAX_ITERS = 10
# doubleScalarVSMALL (doubleScalar.H:64), the yPlusTherm underflow guard.
_VSMALL = 1.0e-300


def nutk_y_plus(nu_w: torch.Tensor, y_w: torch.Tensor,
                sqrt_k_c: torch.Tensor, nu_eff_w: torch.Tensor,
                mag_grad_uw: torch.Tensor, cmu: float,
                ypluslam: float) -> torch.Tensor:
    """``nutkWallFunctionFvPatchScalarField::yPlus()`` (nutkWF.C:243-292).

    This is the y+ every consumer of a nutkWallFunction patch reads through
    ``nutPf.yPlus()`` -- in particular alphatJayatillekeWallFunction
    (aJWF.C:73-82).  It is NOT the y+ ``calcNut`` uses (always the k-based
    one, nutkWF.C:69-72)::

        yPlus = Cmu^0.25 y sqrt(k_cell)/nu                 (nutkWF.C:282)
        if (yPlusLam > yPlus)                              (:284)
            yPlus = y sqrt(nuEff_w |snGrad U|_w)/nu        (:287-288)

    i.e. below the log-layer switch the k-based estimate is replaced by the
    friction-velocity one, ``u_tau = sqrt(nuEff_w |dU/dn|)``.  The switch is a
    JUMP in y+ (the two estimates agree only at equilibrium), transcribed as
    such.  ``sqrt_k_c`` is the caller's floored sqrt of the wall-CELL k,
    ``nu_eff_w = nu_w + nut_w`` with the wall-face nut of this forward
    (``turbModel.nuEff(patchi)``, :267-268), and ``mag_grad_uw`` the caller's
    floored ``|snGrad U|`` (strictly positive), so both branches are finite
    with finite slopes before ``torch.where`` picks one.
    """
    yplus_log = (cmu ** 0.25) * y_w * sqrt_k_c / nu_w
    yplus_vis = y_w * torch.sqrt(nu_eff_w * mag_grad_uw) / nu_w
    return torch.where(yplus_log < ypluslam, yplus_vis, yplus_log)


def jayatilleke_p(prat: float) -> float:
    """Jayatilleke's thermal sublayer resistance P (aJWF.C:95-101)::

        P = 9.24 (Prat^0.75 - 1) (1 + 0.28 exp(-0.007 Prat)),  Prat = Pr/Prt
    """
    return 9.24 * (prat ** 0.75 - 1.0) * (1.0 + 0.28 * math.exp(-0.007 * prat))


def jayatilleke_y_plus_therm(p: float, prat: float, kappa: float,
                             e: float) -> float:
    """Thermal sublayer thickness yPlusTherm (aJWF.C:104-133), exactly.

    Unlike ``y_plus_lam``'s fixed ten sweeps, this is a NEWTON iteration on
    ``f(y) = y - (log(E y)/kappa + P)/Prat`` from y = 11 with an EARLY EXIT:
    it returns the new iterate as soon as two iterates differ by less than
    ``tolerance_ = 0.01``, returns 0 if an iterate drops below VSMALL, and
    otherwise returns the last iterate after ``maxIters_ = 10`` steps
    (aJWF.C:45-46).  The root is where the two branches of the alphat formula
    meet (``y Pr = Prt T+``, so ``kt = 0`` there), and the 0.01 tolerance is
    why the meeting is only approximate.
    """
    ypt = 11.0
    for _ in range(JAYATILLEKE_MAX_ITERS):
        f = ypt - (math.log(e * ypt) / kappa + p) / prat
        df = 1.0 - 1.0 / (ypt * kappa * prat)
        ypt_new = ypt - f / df
        if ypt_new < _VSMALL:
            return 0.0
        if abs(ypt_new - ypt) < JAYATILLEKE_TOLERANCE:
            return ypt_new
        ypt = ypt_new
    return ypt


def alphat_jayatilleke(nu_w: torch.Tensor, yplus: torch.Tensor, pr, p, ypt,
                       prt: float, kappa: float, e: float) -> torch.Tensor:
    """Incompressible ``alphatJayatillekeWallFunction`` wall value
    (aJWF.C:257-283), kinematic [m^2/s]::

        if (yPlus > yPlusTherm)
            alphat_w = max(0, nu (yPlus/(Prt (log(E yPlus)/kappa + P)) - 1/Pr))
        else
            alphat_w = 0

    ``pr``/``p``/``ypt`` may be floats or per-face tensors (constants: Pr is
    the molecular Prandtl number, P and yPlusTherm come from
    ``jayatilleke_p``/``jayatilleke_y_plus_therm``).  The unselected branch is
    evaluated at ``max(yPlusTherm, 1)`` instead of the live y+, so ``log`` never
    sees y+ = 0 and the masked backward stays finite (the safe-branch rule).
    The ``max(0, .)`` is a hard clamp, as in OpenFOAM: just above yPlusTherm
    the Newton tolerance can leave ``kt`` marginally negative.
    """
    ypt_t = torch.as_tensor(ypt, dtype=yplus.dtype, device=yplus.device)
    selected = yplus > ypt_t
    y_safe = torch.where(selected, yplus, torch.clamp(ypt_t, min=1.0))
    kt = nu_w * (y_safe / (prt * (torch.log(e * y_safe) / kappa + p))
                 - 1.0 / pr)
    return torch.where(selected, torch.clamp(kt, min=0.0),
                       torch.zeros_like(kt))
