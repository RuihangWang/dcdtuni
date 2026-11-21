"""KOmegaSST (Menter SST 2003, v2412 form) in log-omega variables -- physics only.

Design: docs/superpowers/specs/2026-08-16-turbulence-framework-design.md,
sections 2.1 (log-omega), 2.3 (smooth floors and smooth blends -- the SST
limiters are the section 2.3.3 "designed to be active" class), 3.4 (this
model's named hard points).  Source of truth for every formula and constant:
Ref_code/OpenFOAM-v2412/src/TurbulenceModels/turbulenceModels/Base/kOmegaSST/
kOmegaSSTBase.{H,C} (cited by line below).  The solved omega variable is
omega-tilde = ln(omega); k stays primal with smooth floors at the evaluation
sites only -- the state is never clipped.

========================================================================
LOG-OMEGA TRANSFORM DERIVATION (re-derive blind before trusting an edit)
========================================================================

Primal v2412 SST omega equation (kOmegaSSTBase.C:570-588; incompressible
host: alpha = rho = 1, divU terms dropped, decayControl off, Qsas zero), as
the residual form LHS - RHS = 0:

  (w1)  dw/dt + div(u w) - div(Gamma_w grad w)
            - gamma(F1) P_w + beta(F1) w^2 - (1 - F1) CDkw = 0

  Gamma_w = nu + alphaOmega(F1) nu_t                    (DomegaEff, H:352-358)
  P_w     = GbyNu_limited                               (C:183-195; see below)
  CDkw    = 2 alphaOmega2 (grad k . grad w) / w         (C:555-558)
  the cross term enters the coded equation as
  ``- fvm::SuSp((F1 - 1) CDkw / w, w)`` (C:579-583), whose residual VALUE is
  - (F1 - 1) CDkw = + (1 - F1) CDkw on the RHS -- the sign above.

Substitute w = exp(wt) and use, term by term (chain rule; div u = 0 for
steps 2 and 3):

  (t1)  grad w = w grad wt
  (t2)  dw/dt + u . grad w = w (dwt/dt + u . grad wt)
        and with div u = 0:  div(u w) = w div(u wt)
  (t3)  div(Gamma grad w) = div(Gamma w grad wt)
                          = w div(Gamma grad wt) + grad w . (Gamma grad wt)
                          = w [ div(Gamma grad wt) + Gamma |grad wt|^2 ]
  (t4)  CDkw = 2 alphaOmega2 (grad k . w grad wt) / w
             = 2 alphaOmega2 (grad k . grad wt)
        -- the 1/w cancels; CDkw needs NO exp() in the log variable (the
        simplification design section 3.4 records).

Divide (w1) by w = exp(wt) > 0 (structural positivity):

  (w2)  dwt/dt + div(u wt) - div(Gamma_w grad wt)
            = gamma(F1) P_w exp(-wt) - beta(F1) exp(wt)
              + Gamma_w |grad wt|^2
              + (1 - F1) 2 alphaOmega2 (grad k . grad wt) exp(-wt)

The host assembles the LHS of (w2) (convection of the stored wt, diffusion
with the Gamma_w face diffusivity); the RHS is this model's omega source
density [1/s].  The k equation (C:600-612) keeps its primal form:

  (k1)  dk/dt + div(u k) - div(Gamma_k grad k) = Pk - betaStar exp(wt) k

  Gamma_k = nu + alphaK(F1) nu_t                        (DkEff, H:342-348)
  epsilonByk = betaStar w                               (C:157-164)

Blending (H:194-214): phi = F1 (phi1 - phi2) + phi2 for phi in {alphaK,
alphaOmega, beta, gamma}.  v2412 blends the alpha = 1/sigma DIFFUSION
coefficients, not sigma itself (H:72-76) -- a deliberate v2412-vs-Menter-
2003 deviation, followed here for parity.

F1 (C:43-69), with (t1)/(t4) substituted and every hard min/max/floor
replaced by its smooth spelling (design 2.3; widths documented at the
DEFAULTS):

  CDkwPlus = smooth_floor(2 alphaOmega2 grad k . grad wt, 1e-10, width)
  arg1 = smin(smin(smax( sqrt(k_f) e^-wt / (betaStar y),
                         500 nu e^-wt / y^2 ),
                   4 alphaOmega2 k_f / (CDkwPlus y^2)),
              10)
  F1 = tanh(arg1^4)

  (k_f = smooth_floor(k): the floor that makes sqrt legal at cold-start
  k <= 0; the same floored k is used in arg1's third entry for sign safety
  -- v2412 uses the bounded k there, C:63, and bounding is exactly what the
  floor replaces.)

F2 (C:73-86):  arg2 = smin(smax( 2 sqrt(k_f) e^-wt / (betaStar y),
                                 500 nu e^-wt / y^2 ), 100)
               F2 = tanh(arg2^2)
F23 = F2: the optional Hellsten F3 rough-wall correction (C:103-113)
defaults OFF (F3_ Switch, C:371-379) and is not implemented.

nu_t shear limiter (C:117-126):

  nu_t = a1 k / max(a1 w, b1 F23 sqrt(S2))
       = a1 k_f / pnorm_max(a1 e^wt, b1 F23 sqrt(S2 + eps^2), p)

  S2 = 2 |symm grad U|^2 (C:137-143); the sqrt is floored (design 2.3.2:
  infinite slope at S2 = 0 otherwise).  pnorm_max, not logsumexp: the
  denominator spans decades and only a scale-invariant blend keeps the
  relative overshoot bounded (smooth.py pnorm_max docstring); its a-side
  a1 e^wt > 0 satisfies the helper's positivity contract structurally.

omega-production limiter (C:183-195; Menter 2003 has NO such limiter --
a v2412 deviation, followed; note correct() passes F23, not F2, as the
"F2" argument at C:567):

  cap = (c1/a1) betaStar e^wt * D,   D = the SAME pnorm_max denominator
  GbyNu_lim = cap * smooth_min(GbyNu0 / cap, 1, s)

  -- blended in the DIMENSIONLESS ratio GbyNu0/cap (cap > 0 structurally),
  so one sharpness knob covers every operating magnitude.

Pk limiter (C:147-153): v2412 computes Pk = min(G, c1 betaStar k w) with
G = nu_t_lagged * GbyNu0 (nut is the previous outer iteration's field
there).  In a residual formulation nothing is lagged, and with the
simultaneous nu_t the two limiters coincide algebraically:

  nu_t * cap = [a1 k_f / D] [(c1/a1) betaStar e^wt D] = c1 betaStar k_f e^wt
  =>  nu_t * GbyNu_lim = min(nu_t GbyNu0, c1 betaStar k_f e^wt)  (smooth)

so the k row uses Pk = nu_t * GbyNu_lim: ONE blend serves both rows and
they cannot disagree about where the limiter engages.  Recorded deviation:
v2412's two limiters are independent only because its nut is lagged.

Wall-free far-field branch: a case with NO wall patch (MMS boxes, periodic
domains) has y undefined; the continuum far-field limit of the blends is
F1 -> 0, F2 -> 0 (all F arguments ~ 1/y -> 0), which this model takes
EXACTLY for such cases (per-case static topology mask, autograd-clean)
rather than through the blend chain, whose logsumexp floor would fabricate
F2 ~ tanh((ln2/s)^2) ~ 1e-3 out of two zero arguments.  With F23 = 0 the
nut limiter degenerates to nu_t = k_f exp(-wt) exactly (pnorm_max(a, 0) =
a), and the production cap to c1 betaStar exp(2 wt).

Buoyancy (design A.2, phase 5b): DTR route -- Gb = Cg nu_t beta (g . grad T)
into the k row ONLY, omega untouched.  v2412's SST itself carries NO
buoyancy term anywhere under turbulenceModels/ (the A.2 grep); the k-row Gb
here is the same buoyancyTurbSource-style coupling KOmega carries, inert on
isothermal hosts (buoyancy_production returns None).

Division of labour (design 3.1): closures only.  All stencil math -- WLSQ
gradients (including grad k and grad wt, which the host already
reconstructs for every transported field), face interpolation, convection /
diffusion fluxes -- stays in the host; the wall-distance mesh ingredient
lives in eddy_viscosity.nearest_wall_distance_cpd (design 3.3 declares the
access at the subfamily level).  SST requests NO derived-gradient pass:
unlike Launder-Sharma's D/E terms, every gradient it consumes is of a
transported state field.

Verification: constants table vs v2412 (tests/test_komega_sst_constants.py),
F1/F2 exact-function + limiting behaviour (tests/test_komega_sst_blending.py),
gradcheck battery incl. switch-point and below-floor probes
(tests/test_komega_sst_gradcheck.py), wall-distance correctness
(tests/test_wall_distance_cells.py), MMS operator rows
(tests/test_komega_sst_source_mms_order.py) and the full 6-field residual
truncation (tests/test_komega_sst_residual_mms.py).
"""

import torch

from FVsolver.FVdiscretization.FVstress import stress_double_inner
from FVsolver.Turbulence.eddy_viscosity import EddyViscosityModel
from FVsolver.Turbulence.smooth import (
    pnorm_max,
    smooth_floor,
    smooth_max,
    smooth_min,
    sqrt_floored,
)

# kOmegaSSTBase.C constructor defaults (:263-379), plus the smooth-spelling
# knobs this formulation adds (design 2.3; overridable through the optional
# [turbulence] table of fvconfig like every model's).
_DEFAULTS = {
    "alphaK1": 0.85,        # :263-271
    "alphaK2": 1.0,         # :272-280
    "alphaOmega1": 0.5,     # :281-289
    "alphaOmega2": 0.856,   # :290-298
    "gamma1": 5.0 / 9.0,    # :299-307
    "gamma2": 0.44,         # :308-316
    "beta1": 0.075,         # :317-325
    "beta2": 0.0828,        # :326-334
    "betaStar": 0.09,       # :335-343
    "a1": 0.31,             # :344-352
    "b1": 1.0,              # :353-361
    "c1": 10.0,             # :362-370
    # k floor at every evaluation site (sqrt(k) in the F args, nut's
    # numerator, the Pk cap).  Same sizing evidence as KOmega's
    # (komega.py cites the fp64 sigmoid-underflow band measurement):
    # cold-start k = 0 sits 0.01 widths below the floor, and for
    # k >= O(1e-6) the softplus linear branch is exact.
    "kMin": 1e-10,
    "kMinWidth": 1e-8,
    # CDkOmega floor: v2412's hard max(CDkOmega, 1e-10) (C:48-52).  This
    # floor is DESIGNED-ACTIVE (grad k . grad wt goes negative over large
    # regions); below ~745 widths (7.5e-6) the derivative underflows to
    # exactly 0 (the pinned fp64 band), which is acceptable HERE and only
    # here: the floored value saturates at 1e-10 there, F1's arg1_c
    # pathway saturates with it, and the min-chain has already deselected
    # it -- unlike the k floor, no state derivative is lost that the
    # limiter's own saturation had not already removed.
    "cdkOmegaMin": 1e-10,
    "cdkOmegaMinWidth": 1e-8,
    # sqrt(S2 + eps^2): value perturbation eps^2/(2 sqrt S2) ~ 5e-17
    # relative at S2 = O(1) -- below fp64 -- while the slope at S2 = 0
    # becomes the finite 1/(2 eps) gradcheck needs.
    "s2SqrtEps": 1e-8,
    # logsumexp sharpness for the DIMENSIONLESS blends (the F1/F2 arg
    # min/max chains and the GbyNu_lim ratio): absolute blend width
    # ln2/20 ~ 0.035 on arguments of O(1)-O(10), i.e. a few percent of
    # the switch value at the switch and exact away from it.  All blend
    # sites here are dimensionless by construction, so one knob is
    # dimensionally sound.
    "blendSharpness": 20.0,
    # pnorm exponent for the nut denominator: relative overshoot
    # 2^(1/p) - 1 at a tie, exact where either side dominates, at every
    # magnitude (smooth.py pnorm_max docstring).
    #
    # 200 is MEASURED, not chosen for taste.  The TMR flat
    # plate holds a1 omega and b1 F2 sqrt(S2) near a TIE right through the
    # boundary layer (min/max ratio, band median: 0.951 coarse / 0.981 fine),
    # which is where this blend is at its worst.  Evaluating OUR closure on
    # OpenFOAM v2412's OWN converged (U, k, omega) -- same state both sides,
    # so no convergence artifact is possible -- the nu_t band median came out
    # at p = 20 / 100 / 200 / sharp:
    #     coarse  -2.450% / -0.100% / -0.005% / +0.000%
    #     fine    -2.568% / -0.137% / -0.010% / +0.000%
    # i.e. p = 20 costs 2.5% of the eddy viscosity ON BOTH RUNGS, one-signed
    # (99.2% of band cells low), and refinement does not remove it.  p = 200
    # puts the smoother 500x below that and ~25x below the residual operator
    # difference that survives a SHARP max (+0.135% / +0.040% in the peak),
    # so it is not the term that sets our parity.
    # Cost, measured rather than assumed: gradcheck and gradgradcheck still
    # pass at p = 200 (the FD step 1e-6 is far inside the blend's own width
    # 1/p = 5e-3), the helper stays finite across 1e-150..1e150, and the
    # Newton arms this default was measured on are recorded in
    # docs/research/2026-08-17-nut-operator-parity.md.
    "nutMaxP": 200.0,
    # Buoyancy production coefficient (phase 5b, design A.2): Gb into k
    # only, DTR route; Cg replaces 1/Prt (buoyantKEpsilon.H:45-49).
    "Cg": 1.0,
    # wallFunctionCoefficients defaults (wallFunctionCoefficients.C:61-63),
    # read by the wall-adjacent-cell production override.  Separate names
    # from betaStar: OpenFOAM keeps them in the PATCH's own coeff dict, and
    # a case may retune the wall pair without touching the model's.
    "wallCmu": 0.09,
    "wallKappa": 0.41,
    # sqrt(|snGrad U|^2 + eps^2) in the same override: |snGrad U| ~ 5e5 1/s on
    # the TMR rungs, so eps^2 = 1e-16 perturbs the value by ~1e-33 relative
    # while the slope at U = 0 (cold start, and the wall ghost itself) becomes
    # the finite 1/(2 eps) a Euclidean norm otherwise leaves undefined.  Same
    # sizing argument as s2SqrtEps, separate knob because it floors a
    # different invariant.
    "wallGradUEps": 1e-8,
}


class KOmegaSST(EddyViscosityModel):
    """Menter SST (v2412 kOmegaSSTBase), solved in (k, omega-tilde)."""

    # Same transported pair and BC attribute names as KOmega: bc/k.toml and
    # bc/omega.toml keep their meaning, setK/setOmega/setTurbScalar work
    # unchanged, and the loader's omega -> ln(omega) transform applies
    # identically (transported_bc_fields / log_transformed_bc_fields read
    # these class attributes by model NAME).
    TRANSPORTED: tuple = (
        ("k", 1, "bc_k_cpd_cell", "residual_k"),
        ("omega", 1, "bc_omega_cpd_cell", "residual_omega"),
    )
    LOG_TRANSFORMED_BC_FIELDS: tuple = ("omega",)
    DEFAULTS = _DEFAULTS

    # ------------------------------------------------------------------
    # closure algebra -- pure tensor functions, gradcheck-gated directly
    # ------------------------------------------------------------------
    def k_floored(self, k: torch.Tensor) -> torch.Tensor:
        return smooth_floor(k, self.kMin, self.kMinWidth)

    def sqrt_k(self, k: torch.Tensor) -> torch.Tensor:
        """sqrt of the FLOORED k (the Launder-Sharma sqrt_k argument:
        sqrt(smooth_floor(k)) stays legal and smooth at below-floor k)."""
        return torch.sqrt(self.k_floored(k))

    def cdk_omega(self, grad_k: torch.Tensor,
                  grad_omega_tilde: torch.Tensor) -> torch.Tensor:
        """CDkOmega in the log variable: 2 alphaOmega2 (grad k . grad wt)
        [1/s^2] -- derivation step (t4), the 1/omega cancelled exactly.
        Gradients are ``[.., N, 3]``."""
        return (2.0 * self.alphaOmega2
                * (grad_k * grad_omega_tilde).sum(dim=-1, keepdim=True))

    def sqrt_s2(self, grad_u: torch.Tensor) -> torch.Tensor:
        """sqrt(S2 + eps^2), S2 = 2 |symm grad U|^2 (C:137-143) [1/s].
        ``grad_u`` is ``[.., N, 3, 3]`` in the FVstress layout."""
        sym = 0.5 * (grad_u + grad_u.transpose(-2, -1))
        s2 = 2.0 * (sym * sym).sum(dim=(-2, -1))[..., None]
        return sqrt_floored(s2, self.s2SqrtEps)

    def f1(self, k: torch.Tensor, omega_tilde: torch.Tensor,
           cdk_omega: torch.Tensor, y: torch.Tensor,
           nu: torch.Tensor) -> torch.Tensor:
        """F1 = tanh(arg1^4) (C:43-69), smooth spellings per the module
        derivation block."""
        s = self.blendSharpness
        ewt = torch.exp(-omega_tilde)
        kf = self.k_floored(k)
        cdk_plus = smooth_floor(cdk_omega, self.cdkOmegaMin,
                                self.cdkOmegaMinWidth)
        arg_a = torch.sqrt(kf) * ewt / (self.betaStar * y)
        arg_b = 500.0 * nu * ewt / (y * y)
        arg_c = 4.0 * self.alphaOmega2 * kf / (cdk_plus * y * y)
        arg1 = smooth_min(
            smooth_min(smooth_max(arg_a, arg_b, s), arg_c, s),
            torch.full_like(arg_a, 10.0), s)
        return torch.tanh(arg1 ** 4)

    def f2(self, k: torch.Tensor, omega_tilde: torch.Tensor,
           y: torch.Tensor, nu: torch.Tensor) -> torch.Tensor:
        """F2 = tanh(arg2^2) (C:73-86); F23 == F2 here (F3 off, C:371-379)."""
        s = self.blendSharpness
        ewt = torch.exp(-omega_tilde)
        arg_a = 2.0 * torch.sqrt(self.k_floored(k)) * ewt / (self.betaStar * y)
        arg_b = 500.0 * nu * ewt / (y * y)
        arg2 = smooth_min(smooth_max(arg_a, arg_b, s),
                          torch.full_like(arg_a, 100.0), s)
        return torch.tanh(arg2 * arg2)

    def blend(self, f1_field: torch.Tensor, phi1: float,
              phi2: float) -> torch.Tensor:
        """phi = F1 (phi1 - phi2) + phi2 (kOmegaSSTBase.H:194-214)."""
        return f1_field * (phi1 - phi2) + phi2

    def _nut_denominator(self, omega_tilde, f23, sqrt_s2):
        """max(a1 w, b1 F23 sqrt S2) as pnorm_max -- shared verbatim by
        nut_cell and gbynu_limited so the two limiters stay the algebraic
        pair the derivation block proves them to be."""
        return pnorm_max(self.a1 * torch.exp(omega_tilde),
                         self.b1 * f23 * sqrt_s2, self.nutMaxP)

    def nut_cell(self, k: torch.Tensor, omega_tilde: torch.Tensor,
                 f23: torch.Tensor, sqrt_s2: torch.Tensor) -> torch.Tensor:
        """nu_t = a1 k_f / pnorm_max(a1 e^wt, b1 F23 sqrt S2) (C:123)."""
        return (self.a1 * self.k_floored(k)
                / self._nut_denominator(omega_tilde, f23, sqrt_s2))

    def gbynu_limited(self, gbynu0: torch.Tensor, omega_tilde: torch.Tensor,
                      f23: torch.Tensor, sqrt_s2: torch.Tensor) -> torch.Tensor:
        """min(GbyNu0, (c1/a1) betaStar w max(a1 w, b1 F23 sqrt S2))
        (C:183-195), blended in the dimensionless ratio to the cap."""
        cap = ((self.c1 / self.a1) * self.betaStar * torch.exp(omega_tilde)
               * self._nut_denominator(omega_tilde, f23, sqrt_s2))
        return cap * smooth_min(gbynu0 / cap, torch.ones_like(cap),
                                self.blendSharpness)

    def k_source(self, nut: torch.Tensor, gbynu_lim: torch.Tensor,
                 k: torch.Tensor, omega_tilde: torch.Tensor,
                 pk: torch.Tensor = None) -> torch.Tensor:
        """k-row source density [m^2/s^3]: Pk - betaStar e^wt k, with
        Pk = nut * GbyNu_lim (== the smooth min(G, c1 betaStar k w),
        derivation block).  Sink in Sp form: coefficient times PRIMAL k.

        ``pk`` overrides the production for the caller that has already built
        it -- the wall-adjacent cells, where v2412 replaces G by its wall
        function's own value (``wall_production``).  Left None it recomputes
        ``nut * gbynu_lim``, so every existing caller and every gradcheck
        fixture keeps its exact previous value."""
        if pk is None:
            pk = nut * gbynu_lim
        return pk - self.betaStar * torch.exp(omega_tilde) * k

    def wall_production(self, solver, state, k_int, omega_tilde_int, pk):
        """Substitute v2412's wall-function G into Pk at wall-adjacent cells.

        ``EddyViscosityModel.wall_function_production`` supplies G0; the SST k
        row then applies ITS OWN limiter to it, because v2412 does:
        ``Pk(G) = min(G, c1 betaStar k omega)`` (kOmegaSSTBase.C:147-153) is
        evaluated AFTER ``updateCoeffs`` has overwritten ``G[celli]``, so the
        cap sees the wall-function value.  Written as ``cap * smooth_min(G0/cap,
        1)`` -- the identical dimensionless blend the interior rows already go
        through, since ``nut * gbynu_lim == cap * smooth_min(G/cap, 1)`` with
        ``cap = c1 betaStar k_f e^wt`` (derivation block).  One blend, both
        branches, so the cap cannot engage differently on the two.

        Measured on the TMR rungs the cap does NOT bind at the wall (G0/cap
        = 0.109 median, 0 of 224 faces above it), so it is carried for
        correctness rather than because it acts here.

        The omega row is deliberately untouched: v2412 overwrites the
        registered field ``G`` only, while the omega equation's production is
        the separate ``GbyNu0``/``GbyNu`` chain (kOmegaSSTBase.C:524-525,
        :567, :576) -- and that row is a pinned Dirichlet constraint here in
        any case.
        """
        wall = self.wall_function_production(solver, state, k_int)
        if wall is None:
            return pk
        cell_row, g0 = wall
        cap = (self.c1 * self.betaStar
               * self.k_floored(k_int.index_select(-2, cell_row))
               * torch.exp(omega_tilde_int.index_select(-2, cell_row)))
        return pk.index_copy(
            -2, cell_row,
            cap * smooth_min(g0 / cap, torch.ones_like(cap),
                             self.blendSharpness))

    def omega_source(self, gbynu_lim: torch.Tensor, omega_tilde: torch.Tensor,
                     f1: torch.Tensor, gamma_omega: torch.Tensor,
                     grad_omega_tilde: torch.Tensor,
                     cdk_omega: torch.Tensor) -> torch.Tensor:
        """omega-tilde source density [1/s] -- the RHS of (w2):

        gamma(F1) GbyNu_lim e^-wt - beta(F1) e^wt + Gamma_w |grad wt|^2
        + (1 - F1) CDkOmega e^-wt,   Gamma_w = the same nu + alphaOmega(F1)
        nu_t the row's diffusion uses, at the cell.
        """
        ew = torch.exp(omega_tilde)
        grad_sq = (grad_omega_tilde * grad_omega_tilde).sum(dim=-1,
                                                            keepdim=True)
        gamma = self.blend(f1, self.gamma1, self.gamma2)
        beta = self.blend(f1, self.beta1, self.beta2)
        return (gamma * gbynu_lim / ew
                - beta * ew
                + gamma_omega * grad_sq
                + (1.0 - f1) * cdk_omega / ew)

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def _nut_cell_from_state(self, solver, state) -> torch.Tensor:
        """cpd-level closure pass, run ONCE per forward from nu_eff_face.

        Needs the unified WLSQ gradient views the host stashes in the
        fvfield before calling nu_eff_face (grad_uvw / grad_k / grad_omega
        cpd entries; the hosts moved the nu_eff_face call after the
        gradient block for exactly this model -- picoext forward comment).
        Ghost rows: k/omega carry BC values; gradient-derived quantities
        (sqrt S2, CDkOmega) carry owner values / the floored zero, and y
        is owner-filled by nearest_wall_distance_cpd -- the F chain then
        reproduces the wall limit F1 = 1 at wall ghosts by itself.
        """
        k_cpd = state["k_cpd_cell"]
        wt_cpd = state["omega_cpd_cell"]
        grad_u_cpd = state["grad_uvw_cpd_cell"][..., 0:3, :]
        grad_k_cpd = state["grad_k_cpd_cell"][..., 0, :]
        grad_wt_cpd = state["grad_omega_cpd_cell"][..., 0, :]
        nu_cpd = solver.theta_PDE[solver.batch_cpd_cell, 4:5]

        sqrt_s2 = solver.ghost_fill_owner(self.sqrt_s2(grad_u_cpd))
        cdk = self.cdk_omega(grad_k_cpd, grad_wt_cpd)
        y_cpd, has_wall = self.wall_distance_cells(solver)

        if bool(has_wall.any()):
            f1 = self.f1(k_cpd, wt_cpd, cdk, y_cpd, nu_cpd)
            f2 = self.f2(k_cpd, wt_cpd, y_cpd, nu_cpd)
            if not bool(has_wall.all()):
                # Mixed batch: cells of wall-less cases take the exact
                # far-field branch (module docstring); static topology
                # mask, so the where is autograd-clean.
                zero = torch.zeros((), dtype=f1.dtype, device=f1.device)
                f1 = torch.where(has_wall, f1, zero)
                f2 = torch.where(has_wall, f2, zero)
        else:
            # No wall anywhere in the batch: skip the chain entirely.
            f1 = torch.zeros_like(k_cpd)
            f2 = torch.zeros_like(k_cpd)

        self._f1_cpd = f1
        self._f23_cpd = f2          # F23 = F2, F3 not implemented
        self._cdk_cpd = cdk
        self._sqrt_s2_cpd = sqrt_s2
        return self.nut_cell(k_cpd, wt_cpd, self._f23_cpd, sqrt_s2)

    def nu_eff_face(self, solver, state) -> torch.Tensor:
        """Single computation site (base.py contract): nu_t AND the two
        blended-alpha diffusivity products interpolated to faces in ONE
        call, so the momentum diffusivity and the k/omega row
        diffusivities can never come from different nu_t evaluations.
        Unlike KOmega's constant alphaK/alphaOmega, SST's are F1-blended
        CELL fields, so the products (not the factors) are what must be
        interpolated."""
        nut_cell = self._nut_cell_from_state(solver, state)
        alpha_k = self.blend(self._f1_cpd, self.alphaK1, self.alphaK2)
        alpha_w = self.blend(self._f1_cpd, self.alphaOmega1, self.alphaOmega2)
        packed = torch.cat(
            (nut_cell, alpha_k * nut_cell, alpha_w * nut_cell), dim=-1)
        faces = solver.interpolating_phic_to_faces(
            phi_cpd_cell=packed, scheme="Linear")
        self._nut_cell = nut_cell
        self._nut_face = faces[..., 0:1]
        self._ak_nut_face = faces[..., 1:2]
        self._aw_nut_face = faces[..., 2:3]
        self._alpha_omega_cpd = alpha_w
        return solver.nu_coeff + self._nut_face

    def transport_closures(self, solver, state) -> dict:
        """Face diffusivities and per-cell source DENSITIES (host multiplies
        by V_cell) for the k and omega-tilde rows.

        Required ``state`` keys are KOmega's set (k_int, omega_int,
        grad_u_int, grad_omega_int, nu_cell_int) plus grad_k_int -- all
        carved by the host from the same unified WLSQ gradient; the
        F1/F23/CDkOmega/sqrt-S2 fields come from this forward's
        _nut_cell_from_state stash (identical gradient tensors, single
        computation site).
        """
        self.nut_face(solver, state)   # ordering assert (single-site contract)
        mask_int = solver.mask_interior_cell
        nut_int = self._nut_cell[..., mask_int, :]
        f1_int = self._f1_cpd[..., mask_int, :]
        f23_int = self._f23_cpd[..., mask_int, :]
        cdk_int = self._cdk_cpd[..., mask_int, :]
        sqrt_s2_int = self._sqrt_s2_cpd[..., mask_int, :]

        gbynu0 = stress_double_inner(state["grad_u_int"])
        k_int = state["k_int"]
        wt_int = state["omega_int"]
        gbynu_lim = self.gbynu_limited(gbynu0, wt_int, f23_int, sqrt_s2_int)
        gamma_omega_int = (state["nu_cell_int"]
                           + self._alpha_omega_cpd[..., mask_int, :] * nut_int)

        # Wall-adjacent cells take v2412's wall-function production instead of
        # nu_t GbyNu (omegaWallFunctionFvPatchScalarField.C:531); every other
        # cell is untouched, and a case with no low-Re omega wall skips it.
        pk = self.wall_production(solver, state, k_int, wt_int,
                                  nut_int * gbynu_lim)
        k_source = self.k_source(nut_int, gbynu_lim, k_int, wt_int, pk=pk)
        # Buoyancy (phase 5b, DTR route): +Gb in k only, omega untouched
        # (module docstring; v2412 SST itself has no buoyancy term).
        gb = self.buoyancy_production(nut_int, state)
        if gb is not None:
            k_source = k_source + gb
        return {
            "k": {
                "diffusivity_face": solver.nu_coeff + self._ak_nut_face,
                "source_cell": k_source,
            },
            "omega": {
                "diffusivity_face": solver.nu_coeff + self._aw_nut_face,
                "source_cell": self.omega_source(
                    gbynu_lim, wt_int, f1_int, gamma_omega_int,
                    state["grad_omega_int"][..., 0, :], cdk_int),
            },
        }

    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        """SST's wall conditions are KOmega's (k = 0 Dirichlet, the
        setOmega.Wall viscous-asymptote omega write, both through the
        existing SetBC machinery -- design 2.4, no row pinning); nothing
        extra to write here."""
        return {}
