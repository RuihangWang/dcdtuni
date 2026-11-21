"""LaunderSharmaKE (Launder & Sharma 1974 low-Re k-epsilon) -- physics only.

Design: docs/superpowers/specs/2026-08-16-turbulence-framework-design.md,
addendum A.4 (promotion to concrete second target, and the variable
decision: PRIMAL epsilon-tilde with smooth floors, NOT log -- the solved
epsilon is the homogeneous dissipation, zero at walls by construction, so a
log variable would be ill-posed exactly where the BC is imposed).  Source of
truth for every formula: Ref_code/OpenFOAM-v2412/src/TurbulenceModels/
turbulenceModels/RAS/LaunderSharmaKE/LaunderSharmaKE.C (cited by line
below).

File name: ``launder_sharma.py`` rather than the design tree's reserved
``kepsilon.py`` -- one file per CONCRETE model, matching komega.py; the
standard-kEpsilon + wall-function variant A.4 records as the industrial
follow-on keeps ``kepsilon.py`` free for when (if) it lands.  Registry name
follows the exact OpenFOAM spelling: ``LaunderSharmaKE``.

The model (LaunderSharmaKE.C, incompressible specialisation):

    ReT = k^2 / (nu epsilon)                                     (:45)
    fMu = exp(-3.4 / (1 + ReT/50)^2)                             (:45)
    f2  = 1 - 0.3 exp(-min(ReT^2, 50))                           (:52-54)
    nut = Cmu fMu k^2 / epsilon                                  (:61)
    D   = 2 nu |grad sqrt(k)|^2                                  (:254)
    E   = 2 nu nut |grad grad U|^2   (fvc::magSqrGradGrad)       (:253)
    G   = nut (grad U : dev2symm(grad U))                        (:257)

    k row:    ddt + div(u k) - div((nu + nut/sigmak) grad k)
                  = G - Sp((epsilon + D)/k, k)                   (:285-295)
    eps row:  ddt + div(u eps) - div((nu + nut/sigmaEps) grad eps)
                  = C1 G eps/k - Sp(C2 f2 eps/k, eps) + E        (:262-274)

Deviations from the coded source, each recorded here:
- The divU terms (:269 SuSp((2/3 C1 - C3) divU, eps), :291 SuSp(2/3 divU,
  k)) and the C3 coefficient are OMITTED.  "The host solvers are
  incompressible" is NOT the reason and was never sufficient: on a
  COLLOCATED solver div u vanishes only to the continuity row's own
  residual, never pointwise.  The reason is the measured size of what it
  leaves out, evaluated at OpenFOAM's own converged LaunderSharma field on
  the TMR plate (docs/research/2026-08-17-epsilon-row-parity.md,
  where div u * V IS the continuity block): the omitted epsilon term is
  0.044% of the epsilon row's residual in L2 (max 3.7e-02 against a row
  reading 9.138) and the omitted k term 0.22% of the k row's, and restoring
  both leaves either block's max|r| unchanged in its first five digits.
  Carrying them would add an epsilon<-continuity Jacobian coupling for that.
- OpenFOAM ``bound(k, kMin)`` / ``bound(epsilon, epsilonMin)`` (:199-200,
  :281, :301) hard-clip the STATE; here the state is never clipped and
  every evaluation site that OpenFOAM protects with the bound reads the
  field through a SMOOTH floor instead (design 2.3/A.4): k_f =
  smooth_floor(k), eps_f = smooth_floor(epsilon).  The Sp-form sinks keep
  their linearised structure -- coefficient at the FLOORED state times the
  PRIMAL state -- so off the floor they reduce exactly to the coded terms
  (softplus's linear branch is exact there), while below the floor both the
  value and the derivative stay finite and smooth.  Both floors are
  load-bearing: 1/k_f in the epsilon-row fractions and the k-row sink,
  1/eps_f in ReT and nut (A.4).
- OpenFOAM's hard ``min(ReT^2, 50)`` in f2 becomes ``smooth_min`` (task
  rule: smooth clamp, never a hard one).  The blend error is <=
  0.3 exp(-50) ln2 ~ 1e-22 in f2 -- below fp64 resolution of f2 ~ 1.

Safe domain of the exponentials (gradcheck-gated, extreme-ReT probes):
- fMu's exponent is confined to (-3.4, 0) for ALL ReT >= 0 -- no
  overflow/underflow at any state the floors admit.
- f2's exponent is capped near -50 by the smooth min; the cap side of
  ``logaddexp`` is overflow-safe, so the only failure mode is ReT^2
  overflowing fp64 itself, i.e. ReT > ~1.3e154.  The floors bound
  ReT <= k_f^2/(nu epsilonMin), which stays ~1e130 below that for any
  |k| <= 1e60 at the production floors -- states no solve visits.

D and E consume gradients of DERIVED fields (grad sqrt(k), grad grad U);
stencil math stays in the host, so this model requests them through
``derived_gradient_operands`` (base.py) and the host's one extra WLSQ pass
hands back ``grad_sqrt_k_int`` / ``grad_grad_u_int``.  The E-term
contraction feeds the FVstress entry-point layout ([component, derivative]
rows flattened), which phase 2 deliberately left input-agnostic.

Verification: gradcheck battery incl. below-floor probes on BOTH floors and
extreme-ReT probes (tests/test_launder_sharma_gradcheck.py); MMS operator
rows for the damped nut production, D, E, the full epsilon source and both
diffusivities (tests/test_launder_sharma_source_mms_order.py); full
6-channel residual truncation (tests/test_launder_sharma_residual_mms.py).
"""

import torch

from FVsolver.FVdiscretization.FVstress import stress_double_inner
from FVsolver.Turbulence.eddy_viscosity import EddyViscosityModel
from FVsolver.Turbulence.smooth import smooth_floor, smooth_min, sqrt_floored

# OpenFOAM LaunderSharmaKE.C constructor defaults (:118-171); C3 omitted
# with the divU term (module docstring).  Floor knobs sized exactly like
# KOmega's k floor (komega.py cites the fp64 underflow-band measurement);
# epsilon's floor is load-bearing through 1/eps in ReT and nut (A.4) and
# gets the same tiny production scale.
_DEFAULTS = {
    "Cmu": 0.09,
    "C1": 1.44,
    "C2": 1.92,
    "sigmak": 1.0,
    "sigmaEps": 1.3,
    "kMin": 1e-10,
    "kMinWidth": 1e-8,
    "epsilonMin": 1e-10,
    "epsilonMinWidth": 1e-8,
    # smooth_min sharpness for f2's ReT^2 cap: undershoot <= ln2/s on a cap
    # of 50, i.e. <= 1e-22 in f2 at s = 1 -- no reason to sharpen further.
    "f2MinSharpness": 1.0,
    # Buoyancy coupling (phase 5b, design A.2): Gb = Cg nu_t beta (g.grad T)
    # into k, and + C1 tanh(|v|/u) (eps/k) Gb into epsilon --
    # C3_eff = tanh(|v|/u) is NOT a constant in v2412
    # (buoyantKEpsilon.C:130-152; Henkes, Van Der Vlugt & Hoogendoorn 1991,
    # itself a low-Re NC study, so it transcribes to the LS interior
    # unchanged).  We take the buoyantKEpsilon.C:148 argument tanh(|v|/u)
    # (C3_eff -> 1 for gravity-aligned plumes); buoyancyTurbSource.C:87 has
    # it INVERTED -- the A.2 discrepancy, recorded, not followed.  Cg
    # replaces 1/Prt (buoyantKEpsilon.H:45-49); default 1.0.
    "Cg": 1.0,
    # Smooth floors for the tanh argument (task rule: |v|/u needs a smooth
    # floor on u at zero-velocity states).  |v| and u are both spelled
    # sqrt_floored (sqrt(x^2 + eps^2)): smooth through v = 0 (where bare
    # |v| has a kink and gradcheck fails) and strictly positive u (OpenFOAM
    # adds SMALL).  gbVMin << gbUMin so a zero-velocity state reads
    # C3_eff = tanh(gbVMin/gbUMin) ~ 0.01 -- the Henkes limit tanh(0) ~ 0
    # rather than the tanh(1) an equal pair of floors would fabricate.
    "gbUMin": 1e-8,
    "gbVMin": 1e-10,
}


class LaunderSharmaKE(EddyViscosityModel):
    """Launder-Sharma low-Re k-epsilon, solved in primal (k, epsilon)."""

    # Both variables PRIMAL (A.4): no entry in LOG_TRANSFORMED_BC_FIELDS,
    # and setEpsilon binds setTurbScalar with no value transform.
    TRANSPORTED: tuple = (
        ("k", 1, "bc_k_cpd_cell", "residual_k"),
        ("epsilon", 1, "bc_epsilon_cpd_cell", "residual_epsilon"),
    )
    LOG_TRANSFORMED_BC_FIELDS: tuple = ()
    DEFAULTS = _DEFAULTS

    # ------------------------------------------------------------------
    # closure algebra -- pure tensor functions, gradcheck-gated directly
    # ------------------------------------------------------------------
    def k_floored(self, k: torch.Tensor) -> torch.Tensor:
        return smooth_floor(k, self.kMin, self.kMinWidth)

    def epsilon_floored(self, epsilon: torch.Tensor) -> torch.Tensor:
        return smooth_floor(epsilon, self.epsilonMin, self.epsilonMinWidth)

    def re_t(self, k: torch.Tensor, epsilon: torch.Tensor,
             nu: torch.Tensor) -> torch.Tensor:
        """Turbulence Reynolds number ReT = k^2/(nu epsilon), floored fields.

        The epsilon floor is what bounds ReT away from the fp64 overflow
        domain (module docstring); the k floor keeps ReT smooth through
        k <= 0 cold-start states.
        """
        kf = self.k_floored(k)
        return kf * kf / (nu * self.epsilon_floored(epsilon))

    def f_mu(self, re_t: torch.Tensor) -> torch.Tensor:
        """fMu = exp(-3.4/(1 + ReT/50)^2)  (LaunderSharmaKE.C:45).

        Exponent in (-3.4, 0) for all ReT >= 0: overflow-free by algebra.
        """
        s = 1.0 + re_t / 50.0
        return torch.exp(-3.4 / (s * s))

    def f2(self, re_t: torch.Tensor) -> torch.Tensor:
        """f2 = 1 - 0.3 exp(-min(ReT^2, 50))  (:52-54), smooth min."""
        capped = smooth_min(re_t * re_t,
                            torch.full_like(re_t, 50.0),
                            self.f2MinSharpness)
        return 1.0 - 0.3 * torch.exp(-capped)

    def nut_cell(self, k: torch.Tensor, epsilon: torch.Tensor,
                 nu: torch.Tensor) -> torch.Tensor:
        """nut = Cmu fMu k^2/epsilon (:61), floored fields.

        Strictly positive with strictly positive d(nut)/dk even at k <= 0
        states (the property the below-floor gradcheck probes assert).
        """
        kf = self.k_floored(k)
        eps_f = self.epsilon_floored(epsilon)
        return (self.Cmu * self.f_mu(self.re_t(k, epsilon, nu))
                * kf * kf / eps_f)

    def sqrt_k(self, k: torch.Tensor) -> torch.Tensor:
        """sqrt of the FLOORED k -- the D-term gradient operand.

        Composed floor-then-sqrt rather than smooth.py's ``sqrt_floored``:
        sqrt(x + eps^2) is NaN for x < -eps^2, and the below-floor k states
        a cold start visits sit exactly there; sqrt(smooth_floor(k)) has a
        strictly positive argument >= kMin everywhere, with slope bounded by
        sigmoid/(2 sqrt(kMin)) -- finite, smooth, gradcheck-clean.
        """
        return torch.sqrt(self.k_floored(k))

    def d_term(self, nu: torch.Tensor,
               grad_sqrt_k: torch.Tensor) -> torch.Tensor:
        """D = 2 nu |grad sqrt(k)|^2  (:254); grad_sqrt_k ``[.., N, 1, 3]``."""
        return 2.0 * nu * (grad_sqrt_k * grad_sqrt_k).sum(dim=(-2, -1))[..., None]

    def e_term(self, nu: torch.Tensor, nut: torch.Tensor,
               grad_grad_u: torch.Tensor) -> torch.Tensor:
        """E = 2 nu nut |grad grad U|^2  (:253, fvc::magSqrGradGrad).

        ``grad_grad_u`` is ``[.., N, 9, 3]`` -- the host's WLSQ gradient of
        the 9 flattened velocity-gradient components; magSqrGradGrad is the
        sum of squares of all 27 entries, layout-invariant.
        """
        return (2.0 * nu * nut
                * (grad_grad_u * grad_grad_u).sum(dim=(-2, -1))[..., None])

    def c3_eff(self, uvw: torch.Tensor, gravity: torch.Tensor) -> torch.Tensor:
        """``C3_eff = tanh(|v|/u)`` -- v the velocity component ALONG gravity,
        u the perpendicular magnitude (buoyantKEpsilon.C:130-152).

        Smooth everywhere a cold start visits: |v| and u are sqrt_floored
        (see the _DEFAULTS gbUMin/gbVMin note), so the value and derivative
        are finite and smooth at U = 0, where the bare |v| has a kink and
        the bare u a division by zero.  ``gravity`` is a per-case constant;
        the 1e-30 clamp on |g| only guards a zero-gravity case, where Gb
        itself is identically zero and this factor multiplies nothing.
        """
        g_hat = gravity / gravity.norm(dim=-1, keepdim=True).clamp_min(1e-30)
        v = (uvw * g_hat).sum(dim=-1, keepdim=True)
        u_perp = uvw - g_hat * v
        u = sqrt_floored((u_perp * u_perp).sum(dim=-1, keepdim=True),
                         self.gbUMin)
        return torch.tanh(sqrt_floored(v * v, self.gbVMin) / u)

    def k_source(self, nut: torch.Tensor, gbynu: torch.Tensor,
                 k: torch.Tensor, epsilon: torch.Tensor,
                 d: torch.Tensor) -> torch.Tensor:
        """k-row source density [m^2/s^3]: G - ((eps_f + D)/k_f) k.

        The sink keeps OpenFOAM's Sp structure (:292): coefficient at the
        floored state times the PRIMAL k -- off the floor this is exactly
        -(epsilon + D), and the 1/k_f is the load-bearing k-floor site the
        below-floor probes exercise.
        """
        sink_coeff = (self.epsilon_floored(epsilon) + d) / self.k_floored(k)
        return nut * gbynu - sink_coeff * k

    def epsilon_source(self, nut: torch.Tensor, gbynu: torch.Tensor,
                       k: torch.Tensor, epsilon: torch.Tensor,
                       nu: torch.Tensor, e: torch.Tensor) -> torch.Tensor:
        """epsilon-row source density [m^2/s^4]:

        C1 G eps_f/k_f  -  C2 f2(ReT) (eps_f/k_f) eps  +  E     (:268-271)

        Production explicit at the floored fields (OpenFOAM evaluates it on
        the bounded fields, :268); destruction in Sp form (floored
        coefficient x primal epsilon, :270).
        """
        kf = self.k_floored(k)
        eps_f = self.epsilon_floored(epsilon)
        f2 = self.f2(self.re_t(k, epsilon, nu))
        return (self.C1 * nut * gbynu * eps_f / kf
                - self.C2 * f2 * (eps_f / kf) * epsilon
                + e)

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def _nut_cell_from_state(self, solver, state) -> torch.Tensor:
        # Molecular nu per cpd cell (ghost rows included -- the boundary-face
        # nut interpolation reads them); theta_PDE is per graph.
        nu_cpd = solver.theta_PDE[solver.batch_cpd_cell, 4:5]
        return self.nut_cell(state["k_cpd_cell"], state["epsilon_cpd_cell"],
                             nu_cpd)

    def derived_gradient_operands(self, solver, state) -> dict:
        """sqrt(k) for D, and the 9 velocity-gradient components for E.

        grad_u's non-cyclic ghost rows are zero (interior-only WLSQ), so
        they get the owner-cell first-order fill before the second pass --
        the same patch extrapolation the dev2 boundary substitution uses;
        the wall-accurate closure belongs to phase 4.  k's ghost rows are
        BC-enforced, so sqrt_k needs no fill.
        """
        return {
            "sqrt_k": self.sqrt_k(state["k_cpd"]),
            "grad_u": solver.ghost_fill_owner(state["grad_u_cpd"].flatten(-2)),
        }

    def transport_closures(self, solver, state) -> dict:
        """Face diffusivities and per-cell source DENSITIES (host multiplies
        by V_cell) for the k and epsilon rows.

        Required ``state`` keys beyond KOmega's set: ``grad_sqrt_k_int``
        ``[.., N_int, 1, 3]`` and ``grad_grad_u_int`` ``[.., N_int, 9, 3]``
        -- the host's derived-gradient pass supplies both.
        """
        nut_face = self.nut_face(solver, state)
        nut_int = self._nut_cell[..., solver.mask_interior_cell, :]
        gbynu = stress_double_inner(state["grad_u_int"])
        k_int = state["k_int"]
        eps_int = state["epsilon_int"]
        nu_int = state["nu_cell_int"]
        d = self.d_term(nu_int, state["grad_sqrt_k_int"])
        e = self.e_term(nu_int, nut_int, state["grad_grad_u_int"])
        k_source = self.k_source(nut_int, gbynu, k_int, eps_int, d)
        eps_source = self.epsilon_source(
            nut_int, gbynu, k_int, eps_int, nu_int, e)
        # Buoyancy coupling (phase 5b, design A.2): +Gb in k and
        # + C1 C3_eff (eps_f/k_f) Gb in epsilon, C3_eff = tanh(|v|/u).
        # The epsilon scaling reads the FLOORED fields like the row's other
        # eps/k fractions (buoyantKEpsilon evaluates on the bounded fields).
        # None (isothermal host): both closures structurally unchanged.
        gb = self.buoyancy_production(nut_int, state)
        if gb is not None:
            k_source = k_source + gb
            eps_source = eps_source + (
                self.C1 * self.c3_eff(state["uvw_int"], state["gravity_int"])
                * (self.epsilon_floored(eps_int) / self.k_floored(k_int))
                * gb)
        return {
            "k": {
                # DkEff = nut/sigmak + nu (kEpsilon.H via LaunderSharmaKE.H)
                "diffusivity_face": solver.nu_coeff + nut_face / self.sigmak,
                "source_cell": k_source,
            },
            "epsilon": {
                "diffusivity_face": solver.nu_coeff + nut_face / self.sigmaEps,
                "source_cell": eps_source,
            },
        }

    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        """Phase 3b is wall-free (walls are phase 4, where the k = 0,
        epsilon-tilde = 0 homogeneous Dirichlet pair lands); the plain
        Dirichlet/Neumann/cyclic BCs this phase supports run through the
        existing SetBC machinery (setK.py / setEpsilon.py), not here."""
        return {}
