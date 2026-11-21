"""KOmega (Wilcox 1988) in transformed log-omega variables -- physics only.

Design: docs/superpowers/specs/2026-08-16-turbulence-framework-design.md,
sections 2.1 (log-omega decision), 2.3 (smooth floors), 3.4 (this model's
spec).  The solved omega variable is omega-tilde = ln(omega); positivity of
omega is structural (exp), k stays primal with a smooth floor at the
EVALUATION sites only -- the state is never clipped.

The transformed omega-tilde transport equation (divide the Wilcox omega
equation by omega, with grad omega = omega grad omega-tilde):

    d(wt)/dt + div(u wt) - div(Gamma_w grad wt)
        = gamma * GbyNu * exp(-wt)          (production / omega)
        - beta * exp(wt)                    (destruction: beta omega^2 / omega)
        + Gamma_w |grad wt|^2               (the transformed-form extra term)

where GbyNu = grad U : dev2symm(grad U) (OpenFOAM's contraction; the omega
production gamma*GbyNu is independent of nu_t there, so dividing by omega
leaves gamma*GbyNu*exp(-wt)), and Gamma_w = nu + alphaOmega nu_t.  The k
equation keeps its primal form:

    d(k)/dt + div(u k) - div((nu + alphaK nu_t) grad k)
        = nu_t * GbyNu - betaStar * exp(wt) * k

with nu_t = smooth_floor(k) * exp(-wt)  (= k/omega away from the floor).
No limiter, no wall distance, no blending (the technical reason Wilcox 1988
is the first concrete model).  No divU compressibility term: the host
solvers are incompressible.

Division of labour (design 3.1): this class owns closures only -- nu_t, the
per-equation face diffusivities, and the per-cell source densities.  ALL
stencil math (WLSQ gradients, face interpolation of the fields, convection
and diffusion fluxes, the divergence scatter) stays in the host solver; the
one gradient-shaped input here, ``stress_double_inner``, is a pure per-entity
tensor contraction shared with the momentum stress so the two cannot
disagree (FVstress.py module docstring).

Constants are OpenFOAM kOmega defaults (kOmega.H), overridable through the
optional ``[turbulence]`` table of fvconfig the way OpenFOAM coeffDicts are;
smooth-floor knobs live in the same table.  Verification: gradcheck battery
incl. below-floor probes (tests/test_komega_gradcheck.py) and the MMS order
rows (tests/test_komega_source_mms_order.py, tests/test_komega_residual_mms.py).
"""

import torch

from FVsolver.FVdiscretization.FVstress import stress_double_inner
from FVsolver.Turbulence.eddy_viscosity import EddyViscosityModel
from FVsolver.Turbulence.smooth import smooth_floor

# OpenFOAM kOmega.C defaults (design section 3.4 / dissection A section 1.3).
_DEFAULTS = {
    "betaStar": 0.09,   # Cmu; k-row sink coefficient
    "beta": 0.072,      # omega-row destruction
    "gamma": 0.52,      # omega-row production (OpenFOAM "alpha")
    "alphaK": 0.5,      # k diffusivity blend: nu + alphaK nu_t
    "alphaOmega": 0.5,  # omega diffusivity blend
    # Smooth-floor knobs for k at the nu_t evaluation site (design 2.3.2).
    # kMin ~ OpenFOAM SMALL magnitudes; the width is sized so the fp64
    # sigmoid-underflow band (~745 widths below the floor, pinned by
    # test_fp64_underflow_band_documented) comfortably contains k = 0 cold
    # starts: (0 - 1e-10)/1e-8 = -0.01 widths.  For k >= O(1e-6) the
    # softplus linear branch is exact, so the floor is invisible in every
    # resolved-turbulence state.
    "kMin": 1e-10,
    "kMinWidth": 1e-8,
    # Buoyancy production coefficient (phase 5b, design A.2): Gb = Cg nu_t
    # beta (g . grad T) into the k row ONLY -- the DTR route (Devolder,
    # Troch & Rauwoens 2018, the reference buoyancyTurbSource.H:73-89 itself
    # names): SGDH Gb in k, omega untouched (v2412's own -(gamma/nu_t) B
    # omega source is commented by OpenCFD as a "heuristic approximation";
    # recorded as the losing side).  Cg replaces 1/Prt
    # (buoyantKEpsilon.H:45-49); default 1.0.  Inert on isothermal hosts
    # (buoyancy_production returns None there).
    "Cg": 1.0,
    # wallFunctionCoefficients defaults (wallFunctionCoefficients.C:61-63) and
    # the |snGrad U| square-root floor, all read by the wall-adjacent-cell
    # production override.  v2412's kOmega runs the SAME omegaWallFunction
    # (kOmega.C:212-213 calls updateCoeffs, which overwrites G[celli]), so the
    # override is not an SST-only behaviour.
    "wallCmu": 0.09,
    "wallKappa": 0.41,
    # sqrt(|snGrad U|^2 + eps^2): |snGrad U| ~ 5e5 1/s on the TMR rungs, so
    # eps^2 = 1e-16 perturbs the value by ~1e-33 relative, while the slope at
    # U = 0 (cold start, and the wall ghost itself) becomes the finite
    # 1/(2 eps) a Euclidean norm otherwise leaves undefined.  Same sizing
    # argument as KOmegaSST's s2SqrtEps.
    "wallGradUEps": 1e-8,
    # Ambient turbulence with the sustaining terms of Spalart and Rumsey
    # (2007, AIAA J. 45(10) 2544; Rumsey and Spalart 2009, AIAA J. 47(4)
    # 982, Eqs. 3-4): the k equation gains betaStar omegaAmb kAmb and the
    # omega equation beta omegaAmb^2, which cancel the destruction at
    # k = kAmb, omega = omegaAmb.  In the omega-tilde row (the omega
    # equation divided by omega) the second is beta omegaAmb^2 exp(-wt).
    # 0 = off.
    "kAmb": 0.0,
    "omegaAmb": 0.0,
}


class KOmega(EddyViscosityModel):
    """Wilcox (1988) k-omega, solved in (k, omega-tilde = ln omega).

    Coeff overrides, the per-forward nu_t stash discipline, and
    ``is_uniform`` live in EddyViscosityModel (phase-3b pure refactor,
    gated by this model's 15 tests + the laminar hash gate); this class
    keeps only the KOmega closure algebra.
    """

    # The omega entry stores the LOG variable; the field name stays "omega"
    # so bc/omega.toml keeps OpenFOAM meaning and the BC layer transforms at
    # the ghost write (design 3.1).
    TRANSPORTED: tuple = (
        ("k", 1, "bc_k_cpd_cell", "residual_k"),
        ("omega", 1, "bc_omega_cpd_cell", "residual_omega"),
    )
    LOG_TRANSFORMED_BC_FIELDS: tuple = ("omega",)
    DEFAULTS = _DEFAULTS

    def __init__(self, params, solver):
        super().__init__(params, solver)
        if self.kAmb < 0.0 or self.omegaAmb < 0.0 or ((self.kAmb > 0.0) != (self.omegaAmb > 0.0)):
            raise ValueError(f"[KOmega] kAmb and omegaAmb must both be 0 or both > 0; got kAmb={self.kAmb}, "
                             f"omegaAmb={self.omegaAmb}")

    def ambient_sources(self, k_source, omega_source, omega_tilde):
        """The sustaining sources of the ambient turbulence (``kAmb``, ``omegaAmb``; off at 0): the k row gains
        ``betaStar omegaAmb kAmb`` and the omega-tilde row ``beta omegaAmb^2 exp(-wt)``, the omega equation's
        ``beta omegaAmb^2`` divided by omega.  They cancel the destruction at the ambient state, and they make k = 0
        no solution of any k row."""
        if self.omegaAmb == 0.0:
            return k_source, omega_source
        return (k_source + self.betaStar * self.omegaAmb * self.kAmb,
                omega_source + self.beta * self.omegaAmb ** 2 * torch.exp(-omega_tilde))

    # ------------------------------------------------------------------
    # closure algebra -- pure tensor functions, gradcheck-gated directly
    # ------------------------------------------------------------------
    def nut_cell(self, k_cpd_cell: torch.Tensor,
                 omega_cpd_cell: torch.Tensor) -> torch.Tensor:
        """nu_t = smooth_floor(k) * exp(-omega_tilde)  (= k/omega off-floor).

        The floor keeps nu_t strictly positive with a strictly positive
        derivative in k even at k <= 0 states a cold start visits -- the
        property torch.clamp lacks (gradcheck battery, below-floor probes).
        """
        return (smooth_floor(k_cpd_cell, self.kMin, self.kMinWidth)
                * torch.exp(-omega_cpd_cell))

    def sqrt_k(self, k: torch.Tensor) -> torch.Tensor:
        """sqrt of the FLOORED k -- the same floor ``nut_cell`` applies, so
        the wall-function production and nu_t cannot disagree about where k
        stops being positive.  Consumed by
        ``EddyViscosityModel.wall_function_production``."""
        return torch.sqrt(smooth_floor(k, self.kMin, self.kMinWidth))

    def k_source(self, nut: torch.Tensor, gbynu: torch.Tensor,
                 k: torch.Tensor, omega_tilde: torch.Tensor,
                 pk: torch.Tensor = None) -> torch.Tensor:
        """k-row source density: nu_t GbyNu - betaStar exp(wt) k  [m^2/s^3].

        ``pk`` overrides the production for the wall-adjacent cells v2412's
        omegaWallFunction overwrites; left None it recomputes ``nut * gbynu``,
        so every existing caller keeps its exact previous value."""
        if pk is None:
            pk = nut * gbynu
        return pk - self.betaStar * torch.exp(omega_tilde) * k

    def omega_source(self, gbynu: torch.Tensor, omega_tilde: torch.Tensor,
                     gamma_omega: torch.Tensor,
                     grad_omega_tilde: torch.Tensor) -> torch.Tensor:
        """omega-tilde-row source density [1/s]:

        gamma GbyNu exp(-wt) - beta exp(wt) + Gamma_w |grad wt|^2,
        Gamma_w = the same nu + alphaOmega nu_t the row's diffusion uses
        (module docstring derivation), evaluated at the cell.
        ``grad_omega_tilde`` is ``[.., N, 3]``.
        """
        ew = torch.exp(omega_tilde)
        grad_sq = (grad_omega_tilde * grad_omega_tilde).sum(dim=-1, keepdim=True)
        return (self.gamma * gbynu / ew
                - self.beta * ew
                + gamma_omega * grad_sq)

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def _nut_cell_from_state(self, solver, state) -> torch.Tensor:
        """Face interpolation of this nu_t is linear (geometric
        interpolation of omega under the log variable -- design 2.1)."""
        return self.nut_cell(state["k_cpd_cell"], state["omega_cpd_cell"])

    def transport_closures(self, solver, state) -> dict:
        """Face diffusivities and per-cell source DENSITIES (host multiplies
        by V_cell) for the k and omega-tilde rows.

        Required ``state`` keys, all interior-cell tensors the HOST builds
        from its own unified WLSQ gradient (no stencil math here):
          - ``k_int`` ``[.., N_int, 1]``, ``omega_int`` ``[.., N_int, 1]``
          - ``grad_u_int`` ``[.., N_int, 3, 3]`` (component rows, derivative
            columns -- the FVstress layout)
          - ``grad_omega_int`` ``[.., N_int, 1, 3]``
          - ``nu_cell_int`` ``[.., N_int, 1]`` molecular viscosity per cell
        """
        nut_face = self.nut_face(solver, state)
        nut_int = self._nut_cell[..., solver.mask_interior_cell, :]
        gbynu = stress_double_inner(state["grad_u_int"])
        k_int = state["k_int"]
        w_int = state["omega_int"]
        gamma_omega_int = state["nu_cell_int"] + self.alphaOmega * nut_int
        # Wall-adjacent cells take v2412's wall-function production instead of
        # nu_t GbyNu (omegaWallFunctionFvPatchScalarField.C:531, reached from
        # kOmega.C:212).  Wilcox kOmega has NO Pk limiter -- kOmega.C:245 puts
        # the bare ``G`` in the k row where SST wraps it in ``Pk(G) = min(G,
        # c1 betaStar k omega)`` -- so the value is substituted uncapped.
        wall = self.wall_function_production(solver, state, k_int)
        pk = nut_int * gbynu
        if wall is not None:
            pk = pk.index_copy(-2, wall[0], wall[1])
        k_source = self.k_source(nut_int, gbynu, k_int, w_int, pk=pk)
        # Buoyancy production (phase 5b, DTR route): +Gb in k, omega
        # untouched; None (isothermal host) leaves the closure structurally
        # identical to the pre-5b form.
        gb = self.buoyancy_production(nut_int, state)
        if gb is not None:
            k_source = k_source + gb
        omega_source = self.omega_source(gbynu, w_int, gamma_omega_int, state["grad_omega_int"][..., 0, :])
        k_source, omega_source = self.ambient_sources(k_source, omega_source, w_int)
        return {
            "k": {
                "diffusivity_face": solver.nu_coeff + self.alphaK * nut_face,
                "source_cell": k_source,
            },
            "omega": {
                "diffusivity_face": solver.nu_coeff + self.alphaOmega * nut_face,
                "source_cell": omega_source,
            },
        }

    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        """Phase 3 is wall-free by design (section 11 phase 3: MMS boxes and
        periodic domains only); low-Re wall writes land in phase 4.  The
        plain Dirichlet/Neumann/cyclic BCs this phase supports run through
        the existing SetBC machinery (setK.py / setOmega.py), not here.
        """
        return {}
