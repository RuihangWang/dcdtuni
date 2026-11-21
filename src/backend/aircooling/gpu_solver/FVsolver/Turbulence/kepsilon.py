"""KEpsilon -- the standard HIGH-Re k-epsilon (Launder & Spalding 1974).

Source of truth for every formula below: OpenFOAM v2412,
``Ref_code/openfoam/src/TurbulenceModels/turbulenceModels/RAS/kEpsilon/
kEpsilon.C`` (cited as ``kEpsilon.C:<line>``) and the wall functions under
``derivedFvPatchFields/wallFunctions/`` (``epsWF.C``, ``nutkWF.C``,
``wallCoeffs.C``).  Registry name is v2412's TypeName, ``kEpsilon``
(kEpsilon.H:137).

This is NOT ``LaunderSharmaKE`` with the damping switched off, even though
the INTERIOR source algebra of the two coincides exactly in that limit.
Four things outside that algebra differ, and every one of them is
structural:

1. the transported variable.  Launder-Sharma transports the homogeneous
   dissipation ``eps~ = eps - D``, which the 1974 split makes vanish at a
   wall (hence ``setEpsilon``'s ``wall_value = 0.0``).  This model transports
   the FULL epsilon, which has no finite wall limit at all -- it is pinned in
   the first CELL instead (point 2).
2. the epsilon wall treatment.  ``kEpsilon.C:247`` calls
   ``epsilon_.boundaryFieldRef().updateCoeffs()`` before assembling the
   epsilon equation and ``:267`` calls ``boundaryManipulate``;
   ``LaunderSharmaKE.C`` calls neither.  Those two calls ARE the wall
   function: the wall-adjacent cell's epsilon row is replaced by
   ``eps_c = Cmu^0.75 k_c^1.5/(kappa y)`` (epsWF.C:616 -> fvMatrix.C:310-317)
   and its k-row production is overwritten by the log-layer estimate
   (epsWF.C:541).
3. the k wall BC.  High-Re k takes ``kqRWallFunction``, which is literally
   ``zeroGradient`` (kqRWallFunctionFvPatchField.H:81-85 derives from it and
   its own docs say "It is not a wall-function condition"), not the ``k = 0``
   Dirichlet the low-Re route uses.
4. ``nut`` at the wall FACE.  ``nutLowReWallFunction``'s exact zero becomes
   ``nutkWallFunction``'s log-law excess (``nut_wall_face`` below).

The model as coded (incompressible, alpha = rho = 1):

    nut   = Cmu k^2/eps                                       (kEpsilon.C:45)
    GbyNu = grad U : devTwoSymm(grad U)                        (:238-242)
    G     = nut * GbyNu                                        (:243)
    DkEff = nu + nut/sigmak,  DepsEff = nu + nut/sigmaEps      (kEpsilon.H:166-189)

    k row    ddt + div(u k) - div(DkEff grad k)
               = G - Sp(eps/k, k)                              (:279-281)
    eps row  ddt + div(u eps) - div(DepsEff grad eps)
               = C1 GbyNu Cmu k - Sp(C2 eps/k, eps)            (:258-260)

**The epsilon production is spelled ``C1 GbyNu Cmu k``, not ``C1 (eps/k) G``,
and the difference is not cosmetic.**  The two are equal only while
``nut == Cmu k^2/eps`` holds pointwise, and they are NOT equal in a
wall-adjacent cell: ``G`` there was overwritten by the wall function at
kEpsilon.C:247 while ``GbyNu`` was not.  The epsilon row therefore never sees
the wall-function production.  Transcribed in v2412's spelling.

``kSource()``/``epsilonSource()`` (kEpsilon.C:53-61, :64-72) construct EMPTY
matrices -- virtual hooks for derived models, contributing exactly zero here.
There is no ``Pk`` limiter (unlike kOmegaSST, ``G`` enters the k row bare,
kEpsilon.C:279) and no ``nut`` limiter or floor (kEpsilon.C:42-50).

Deviations from v2412, each recorded with its reason:

- The ``divU`` terms (kEpsilon.C:259 ``SuSp(((2/3)C1 - C3) divU, eps)`` and
  :280 ``SuSp((2/3) divU, k)``) and with them ``C3`` are OMITTED, exactly as
  ``LaunderSharmaKE`` omits them and for the same measured reason
  (launder_sharma.py:33-45, docs/research/2026-08-17-epsilon-row-parity.md):
  at OpenFOAM's own converged field on the TMR plate the omitted epsilon term
  is 0.044% of the epsilon row's residual in L2 and the omitted k term 0.22%
  of the k row's, and restoring both leaves either block's max|r| unchanged
  in its first five digits -- against an epsilon<-continuity Jacobian
  coupling it would add.  The kEpsilon parity spec argued for keeping them on
  the "general form is the default" house rule; the two sibling k-epsilon
  models differing on this term would be worse than either choice, and the
  measurement is the tiebreak.  Note ``C3 = 0`` in the CODE
  (kEpsilon.C:129-137) even though the class documentation block says
  ``C3 -0.33`` (kEpsilon.H:61) -- the code wins, and at C3 = 0 the omitted
  epsilon coefficient is just (2/3)C1.
- ``bound(k, kMin)`` / ``bound(epsilon, epsilonMin)`` (kEpsilon.C:270, :290)
  hard-clip the STATE, and are not a plain clamp either -- they rewrite every
  non-positive cell in the field with a face-area-weighted neighbour average
  whenever ANY cell drops below the floor (bound.C:38-63).  Here the state is
  never clipped: every site OpenFOAM protects with the bound reads the field
  through a SMOOTH floor instead (design 2.3), so the value and the derivative
  stay finite below zero, which a cold start visits.  Off the floor softplus's
  linear branch is exact, so the coded terms are reproduced identically.
- ``y`` is our ``wall_distance_face`` (the owner-cell-centre-to-face NORMAL
  PROJECTION), where v2412's ``turbModel.y()[patchi]`` is the true
  nearest-point distance from the cell centre to the nearest wall face
  POLYGON over the point-connected neighbourhood (nearWallDist.C:101-107 ->
  cellDistFuncsTemplates.C:54-82).  The two coincide when the cell centre
  projects inside its own wall face and differ on skewed near-wall cells.
  Chosen for consistency with ``setOmega.Wall``, which already reads that
  tensor; recorded rather than inherited silently.
- Blenders: STEPWISE (the per-BC default, nutkWF.C:216, epsWF.C:413), TANH
  for nut, and EXPONENTIAL for both nut (``[turbulence] wallBlender``) and
  epsilon (the patch's ``blending`` entry in bc/epsilon.toml).  Under
  STEPWISE with ``lowReCorrection = false`` (epsWF.C:414) the epsilon
  viscous branch is dead code and the constrained value is the log-layer
  form; every other epsilon blender weighs the viscous value ``2 k nu/y^2``
  in at every y+ (v2506 epsWF.C:258-325).  ``lowReCorrection`` itself is not
  implemented.

Load-bearing floors, which differ from LaunderSharmaKE's set because v2412
spells the production without a division (seams map section 2, point 5): the
k floor appears in the destruction denominators (``eps_f/k_f`` in both rows)
and in ``sqrt(k)`` at the wall; the epsilon floor appears only inside ``nut``.
Both are probed below the floor by tests/test_kepsilon_gradcheck.py.

Verification: tests/test_kepsilon_gradcheck.py (closures + assembled residual
channels, below-floor probes on both floors),
tests/test_kepsilon_source_mms_order.py (per-closure MMS orders),
tests/test_kepsilon_residual_mms.py (full 6-channel residual truncation),
tests/test_kepsilon_wall_functions.py (both traps, the nut override and the
OpenFOAM-field parity check).
"""

import torch

from FVsolver.FVdiscretization.FVstress import stress_double_inner
from FVsolver.Turbulence.eddy_viscosity import EddyViscosityModel
from FVsolver.Turbulence.smooth import smooth_floor
from FVsolver.Turbulence.wall_functions import nutk_wall_nut, y_plus_lam

# kEpsilon.C constructor defaults (:102-155), read again in read() (:199-204).
# The wall* set is SEPARATE and deliberately so: every wall-function patch
# field owns its own wallFunctionCoefficients built from the PATCH dictionary
# (epsWF.C:417, nutWF.C:124 -> wallCoeffs.C:73-79), so v2412 lets a case give
# the model and the wall a different Cmu with no warning.  Keeping two sets
# here reproduces that rather than quietly fusing them.
_DEFAULTS = {
    "Cmu": 0.09,        # kEpsilon.C:102-110
    "C1": 1.44,         # kEpsilon.C:111-119
    "C2": 1.92,         # kEpsilon.C:120-128
    "sigmak": 1.0,      # kEpsilon.C:138-146
    "sigmaEps": 1.3,    # kEpsilon.C:147-155
    # Smooth floors replacing bound() (module docstring).  Sized exactly like
    # KOmega's and LaunderSharmaKE's: kMin ~ OpenFOAM SMALL magnitudes, width
    # such that a k = 0 cold start sits 0.01 widths below the floor, far
    # inside the fp64 sigmoid-underflow band (~745 widths) that
    # test_fp64_underflow_band_documented pins.
    "kMin": 1e-10,
    "kMinWidth": 1e-8,
    "epsilonMin": 1e-10,
    "epsilonMinWidth": 1e-8,
    # Buoyancy production coefficient (design A.2), inert on isothermal hosts.
    "Cg": 1.0,
    # wallFunctionCoefficients defaults (wallCoeffs.C:61-63).  yPlusLam is NOT
    # a knob: it is DERIVED from kappa and E by v2412's 10-sweep recursion
    # (wallCoeffs.C:38-52), reproduced in wall_functions.y_plus_lam.
    "wallCmu": 0.09,
    "wallKappa": 0.41,
    "wallE": 9.8,
    # Which of the reference code's five nut_w blenders (nutkWF.C:88-167).
    # "stepwise" is its per-BC default (nutkWF.C:216) and the default here.
    # Implemented alternatives: "tanh" and "exponential" (see
    # wall_functions.nutk_wall_nut).  "exponential" is smooth in y+ everywhere
    # and is the blending of the wall-function flat plates; a case that sets it
    # sets the same ``blending`` on its epsilonWallFunction patch (bc toml),
    # as the reference case sets it on both patch entries, so that the two
    # codes solve the same equations.
    #
    # A blender changes the discrete problem, so a reference must be computed
    # with the same one: at OpenFOAM's stepwise field on the TMR
    # wall-function rung ||r||2 reads 2.888e+02 with "tanh" against 1.834e+01
    # with "stepwise" (docs/research/2026-08-18-kepsilon-wall-function-
    # validation.md 13.3).
    "wallBlender": "stepwise",
    # Ambient turbulence sustained at the case's inflow values (Spalart and
    # Rumsey 2007, AIAA J. 45(10) 2544; Rumsey and Spalart 2009, AIAA J.
    # 47(4) 982): the k row gains epsilonAmb, the epsilon row
    # C2 epsilonAmb^2 / kAmb -- the sources OpenFOAM's atmAmbientTurbSource
    # adds (v2506 atmAmbientTurbSourceTemplates.C:35-104), which cancel the
    # freestream destruction at k = kAmb, epsilon = epsilonAmb.  0 = off.
    "kAmb": 0.0,
    "epsilonAmb": 0.0,
    # sqrt(|snGrad U|^2 + eps^2) in the wall-cell production: the slope of a
    # Euclidean norm at U = 0 -- the cold start, and the no-slip wall ghost on
    # EVERY state -- is otherwise undefined.  Same sizing argument as KOmega's
    # identically-named knob.
    "wallGradUEps": 1e-8,
}


class KEpsilon(EddyViscosityModel):
    """Standard high-Re k-epsilon with wall functions, primal (k, epsilon)."""

    # Both variables PRIMAL: epsilon has no log transform here for the same
    # reason LaunderSharmaKE has none -- nothing in kEpsilon.C or epsWF.C
    # requires one, and the wall constraint is imposed on epsilon itself
    # (epsWF.C:263 accumulates the corner mean in epsilon, not its log), so a
    # log variable would force an exp/ln round trip in the corner average that
    # setOmega.Wall needs only because omega IS stored as ln omega.
    TRANSPORTED: tuple = (
        ("k", 1, "bc_k_cpd_cell", "residual_k"),
        ("epsilon", 1, "bc_epsilon_cpd_cell", "residual_epsilon"),
    )
    LOG_TRANSFORMED_BC_FIELDS: tuple = ()
    DEFAULTS = _DEFAULTS

    # This model's wall function pins EPSILON in the wall-adjacent cell
    # (epsWF.C:616), where the k-omega lineage pins omega.  Selects which BC
    # family ``wall_cell_map`` gathers.
    WALL_CONSTRAINT_VAR: str = "TURB_EPSILON"

    def __init__(self, params, solver):
        super().__init__(params, solver)
        # Derived, never configured: wallCoeffs.C:64/:79 build yPlusLam from
        # kappa and E at construction and expose no dict key for it.
        self.wallYPlusLam = y_plus_lam(self.wallKappa, self.wallE)
        if self.kAmb < 0.0 or self.epsilonAmb < 0.0 or (self.epsilonAmb > 0.0 and self.kAmb <= 0.0):
            raise ValueError(f"[KEpsilon] kAmb and epsilonAmb must be >= 0, kAmb > 0 when epsilonAmb > 0; got "
                             f"kAmb={self.kAmb}, epsilonAmb={self.epsilonAmb}")

    def ambient_sources(self, k_source, eps_source):
        """The sustaining sources of the ambient turbulence (``kAmb``, ``epsilonAmb``; off at 0).

        OpenFOAM's atmAmbientTurbSource adds ``fvm::Sp(epsilonAmb/k, k)`` to the k equation and
        ``fvm::Sp(C2 epsilonAmb^2/(kAmb epsilon), epsilon)`` to the epsilon equation (v2506
        atmAmbientTurbSourceTemplates.C:35-56, 86-104): as source densities of the steady rows these are
        ``+epsilonAmb`` and ``+C2 epsilonAmb^2 / kAmb``.  They cancel the freestream destruction at the ambient state,
        and they make k = 0 no solution of any k row."""
        if self.epsilonAmb == 0.0:
            return k_source, eps_source
        return k_source + self.epsilonAmb, eps_source + self.C2 * self.epsilonAmb ** 2 / self.kAmb

    # ------------------------------------------------------------------
    # closure algebra -- pure tensor functions, gradcheck-gated directly
    # ------------------------------------------------------------------
    def k_floored(self, k: torch.Tensor) -> torch.Tensor:
        return smooth_floor(k, self.kMin, self.kMinWidth)

    def epsilon_floored(self, epsilon: torch.Tensor) -> torch.Tensor:
        return smooth_floor(epsilon, self.epsilonMin, self.epsilonMinWidth)

    def nut_cell(self, k: torch.Tensor, epsilon: torch.Tensor) -> torch.Tensor:
        """nut = Cmu k^2/epsilon (kEpsilon.C:45), floored fields.

        No damping function and no limiter: v2412's ``correctNut`` is that one
        line plus the boundary-condition call.  The epsilon floor is the ONLY
        thing keeping k^2/eps finite -- v2412 relies on ``bound(epsilon,
        epsilonMin)`` having run at :270, with ``epsilonMin_ = SMALL``
        (RASModel.C:83-92), which is why a cold start with k = O(1) and
        epsilon at the floor produces an astronomically large nut in BOTH
        codes.  Initialise the two fields consistently.
        """
        kf = self.k_floored(k)
        return self.Cmu * kf * kf / self.epsilon_floored(epsilon)

    def sqrt_k(self, k: torch.Tensor) -> torch.Tensor:
        """sqrt of the FLOORED k -- the wall-function operand.

        Composed floor-then-sqrt, not ``sqrt_floored``: ``sqrt(x + eps^2)`` is
        NaN for ``x < -eps^2`` and the below-floor k states a cold start
        visits sit exactly there.  It is the SAME floor ``nut_cell`` applies,
        so the wall algebra and nu_t cannot disagree about where k stops being
        positive.  Consumed by ``wall_function_production`` and by
        ``nut_wall_face``.
        """
        return torch.sqrt(self.k_floored(k))

    def k_source(self, nut: torch.Tensor, gbynu: torch.Tensor,
                 k: torch.Tensor, epsilon: torch.Tensor,
                 pk: torch.Tensor = None) -> torch.Tensor:
        """k-row source density [m^2/s^3]: G - (eps_f/k_f) k (kEpsilon.C:279-281).

        The sink keeps v2412's Sp structure (``fvm::Sp(epsilon_()/k_(), k_)``,
        :281): coefficient at the FLOORED state times the PRIMAL k, which off
        the floor is exactly ``-epsilon``.  ``pk`` substitutes the
        wall-function production in the cells epsWF.C:541 overwrites; left
        None it recomputes ``nut * gbynu``.  There is NO Pk limiter -- kOmegaSST
        wraps G in ``min(G, c1 betaStar k omega)``, standard k-epsilon does
        not (kEpsilon.C:279 puts G in bare).
        """
        if pk is None:
            pk = nut * gbynu
        return pk - (self.epsilon_floored(epsilon) / self.k_floored(k)) * k

    def epsilon_source(self, gbynu: torch.Tensor, k: torch.Tensor,
                       epsilon: torch.Tensor) -> torch.Tensor:
        """epsilon-row source density [m^2/s^4] (kEpsilon.C:258-260)::

            C1 GbyNu Cmu k_f  -  C2 (eps_f/k_f) eps

        Production in v2412's DIVISION-FREE spelling and reading ``GbyNu``,
        not ``G`` -- see the module docstring: in a wall-adjacent cell ``G``
        has been overwritten and ``GbyNu`` has not, so ``C1 GbyNu Cmu k`` and
        ``C1 (eps/k) G`` are different numbers there.  Destruction in Sp form
        (floored coefficient x primal epsilon, :260).

        Note that the k floor is load-bearing in the destruction denominator
        only, and the epsilon floor does not appear here at all -- it enters
        this row through ``nut`` in the diffusivity.  One fewer floor site
        than the LaunderSharma spelling needs, which is what the gradcheck
        battery's probe set is sized to.
        """
        kf = self.k_floored(k)
        eps_f = self.epsilon_floored(epsilon)
        return (self.C1 * self.Cmu * kf * gbynu
                - self.C2 * (eps_f / kf) * epsilon)

    # ------------------------------------------------------------------
    # wall functions
    # ------------------------------------------------------------------
    def nut_wall_face(self, solver, state, nut_face):
        """``nutkWallFunction`` on the wall faces (nutkWF.C:38-170).

        v2412 pairs standard k-epsilon with ``nutkWallFunction``, whose y+ is
        the k-based ``Cmu^0.25 sqrt(k_cell) y/nu`` (nutkWF.C:69-72).  That
        makes ``nut_w`` a function of the wall-cell k, hence a real
        epsilon/k-column Jacobian entry rather than the momentum-block
        coupling and non-smooth inner iteration ``nutUWallFunction`` would
        introduce (nutUWF.C:202-207, a 10-iteration loop with a 1%
        convergence test).  ``nutUWallFunction`` is deliberately not
        implemented.

        Which faces: the wall faces of the patches whose EPSILON BC pins its
        adjacent cell, i.e. exactly v2412's ``epsilonWallFunction`` set.  A
        wall patch with a plain epsilon BC keeps the interpolated value, which
        is the same discipline ``wall_cell_map`` applies to the G override.
        (v2412 selects the nut patches independently, by the ``nut`` boundary
        entry; we have no nut field to carry a BC, so the epsilon patch set is
        the closest faithful proxy -- recorded as a deviation.)
        """
        rows = self.wall_cell_map(solver)
        if rows is None:
            return nut_face
        face_idx, _ghost_cpd, cell_cpd, _cell_row, corner_slot = rows
        nu_w = solver.nu_coeff.index_select(-2, face_idx)
        y_w = solver.wall_distance_face.index_select(-2, face_idx)
        # k[faceCells[facei]] -- the CELL value (nutkWF.C:71), never the face.
        k_c = state["k_cpd_cell"].index_select(
            -2, cell_cpd.index_select(0, corner_slot))
        nut_w = nutk_wall_nut(nu_w, y_w, self.sqrt_k(k_c), self.wallCmu,
                              self.wallKappa, self.wallE, self.wallYPlusLam,
                              blender=self.wallBlender)
        return nut_face.index_copy(-2, face_idx, nut_w)

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def _nut_cell_from_state(self, solver, state) -> torch.Tensor:
        return self.nut_cell(state["k_cpd_cell"], state["epsilon_cpd_cell"])

    def transport_closures(self, solver, state) -> dict:
        """Face diffusivities and per-cell source DENSITIES (host multiplies
        by V_cell) for the k and epsilon rows.

        Required ``state`` keys, all built by the HOST from its own unified
        WLSQ gradient (no stencil math here): ``k_int``, ``epsilon_int``,
        ``grad_u_int``, ``u_cpd``.  Unlike LaunderSharmaKE this model requests
        NO derived gradients, so the host's extra WLSQ pass is skipped
        entirely (picoext.py's gate, call-counted by
        tests/test_derived_gradient_gating.py).
        """
        nut_face = self.nut_face(solver, state)
        nut_int = self._nut_cell[..., solver.mask_interior_cell, :]
        gbynu = stress_double_inner(state["grad_u_int"])
        k_int = state["k_int"]
        eps_int = state["epsilon_int"]

        # Wall-adjacent cells take v2412's wall-function production instead of
        # nut GbyNu (epsWF.C:541, reached from kEpsilon.C:247).  Uncapped:
        # standard k-epsilon has no Pk limiter.
        wall = self.wall_function_production(solver, state, k_int)
        pk = nut_int * gbynu
        if wall is not None:
            pk = pk.index_copy(-2, wall[0], wall[1])
        k_source = self.k_source(nut_int, gbynu, k_int, eps_int, pk=pk)
        # The epsilon row deliberately does NOT take the override: v2412
        # writes its production as C1 GbyNu Cmu k (kEpsilon.C:258), which
        # reads GbyNu, not the overwritten G (module docstring).
        eps_source = self.epsilon_source(gbynu, k_int, eps_int)

        # Buoyancy production (design A.2, DTR route): +Gb in k only.  None on
        # an isothermal host, leaving both closures structurally unchanged.
        gb = self.buoyancy_production(nut_int, state)
        if gb is not None:
            k_source = k_source + gb
        k_source, eps_source = self.ambient_sources(k_source, eps_source)
        return {
            "k": {
                # DkEff = nut/sigmak + nu (kEpsilon.H:166-176)
                "diffusivity_face": solver.nu_coeff + nut_face / self.sigmak,
                "source_cell": k_source,
            },
            "epsilon": {
                # DepsilonEff = nut/sigmaEps + nu (kEpsilon.H:179-189)
                "diffusivity_face": solver.nu_coeff + nut_face / self.sigmaEps,
                "source_cell": eps_source,
            },
        }

    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        """Nothing here.  This model's wall treatment is TWO things, and
        neither is a plain ghost write from the model: the epsilon cell
        constraint is a BC that declares ``constrains_adjacent_cell``
        (setEpsilon.Epsilonwallfunction, applied by
        ``BaseSolver._apply_cell_constraints``), and the k-row production
        override is a closure, substituted in ``transport_closures`` above.
        k's own wall BC is plain zeroGradient (kqRWallFunction) and runs
        through the existing SetBC machinery."""
        return {}
