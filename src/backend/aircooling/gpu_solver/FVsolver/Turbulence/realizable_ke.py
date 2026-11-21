"""RealizableKE -- the realizable k-epsilon model (Shih, Liou, Shabbir, Yang &
Zhu 1995, Computers & Fluids 24(3):227-238) with high-Re wall functions.

Source of truth for every formula below: OpenFOAM's ``realizableKE``,
``src/TurbulenceModels/turbulenceModels/RAS/realizableKE/realizableKE.{C,H}``
(cited ``rKE.C:<line>`` / ``rKE.H:<line>``), and for the thermal wall function
the INCOMPRESSIBLE
``src/TurbulenceModels/incompressible/turbulentTransportModels/
derivedFvPatchFields/wallFunctions/alphatWallFunctions/
alphatJayatillekeWallFunction/alphatJayatillekeWallFunctionFvPatchScalarField.C``
(cited ``aJWF.C:<line>``), plus ``nutkWF.C`` / ``epsWF.C`` as in kepsilon.py.
Registry name is the TypeName, ``realizableKE``.

**Version of the cited lines.**  The data-centre cases this model was added for
(the dctwin pipeline) run v2412 ``buoyantBoussinesqSimpleFoam`` with
``RASModel realizableKE``.  The only OpenFOAM source tree on the machine this was
written on is v2406 (``/usr/lib/openfoam/openfoam2406/src``), so every line
number in THIS module is v2406's.  kepsilon.py cites v2412's wall functions; the
two disagree by a constant 9 lines in ``epsWF.C`` (``G[celli] = G0[celli]`` is
v2406 :532, v2412 :541; the ``lowReCorrection`` default v2406 :405, v2412 :414),
which is the only cross-version difference observable from here.  realizableKE.C
and aJWF.C could not be diffed against v2412.

The model as coded (incompressible, alpha = rho = 1; grad U in OpenFOAM's
layout -- ours is its transpose, and every quantity below is invariant under the
transpose, see ``strain_invariants``)::

    S     = devSymm(grad U)                                    (rKE.C:50)
    S2    = 2 S:S,   magS = sqrt(S2)                           (:261-262, :95-96)
    W     = 2 sqrt(2) ((S.S):S) / (magS S2 + SMALL)            (:53-60)
    phis  = acos(clamp(sqrt(6) W, -1, 1)) / 3                  (:64-67)
    As    = sqrt(6) cos(phis)                                  (:68)
    Us    = sqrt(S2/2 + |skew(grad U)|^2)                      (:69)
    rCmu  = 1 / (A0 + As Us k/eps)                             (:71)
    nut   = rCmu k^2/eps                                       (:83)
    eta   = magS k/eps,   C1 = max(eta/(5 + eta), 0.43)        (:264-265)
    G     = nut (grad U : devTwoSymm(grad U))                  (:267)
    DkEff = nut/sigmak + nu,  DepsEff = nut/sigmaEps + nu      (rKE.H:164-187)
    eps row  ddt + div(u eps) - div(DepsEff grad eps)
               = C1 magS eps - Sp(C2 eps/(k + sqrt(nu eps)), eps)   (:286-300)
    k row    ddt + div(u k) - div(DkEff grad k)
               = G - SuSp((2/3) divU, k) - Sp(eps/k, k)        (:312-323)

with A0 4.0, C2 1.9, sigmak 1.0, sigmaEps 1.2 (rKE.C:149-184, rKE.H:52-58).  The
0.43 and the 5 in C1 are code literals (:265), not coefficients, so they are not
knobs here either.  ``kSource()``/``epsilonSource()`` construct EMPTY matrices
(:101-120) and contribute exactly zero.

What is shared with ``KEpsilon`` and what is not.  The transported pair, the k
and epsilon BC families and the whole wall treatment are the same, because v2412
attaches them to the PATCHES, not to the model: ``epsilonWallFunction`` pins the
wall-adjacent cell's epsilon row (the BC layer, setEpsilon.Epsilonwallfunction,
reached from rKE.C:270 and :304), overwrites that cell's G with the log-layer
estimate (epsWF.C:331-336 -> :532, ``EddyViscosityModel.wall_function_production``)
and ``nutkWallFunction`` sets nut on the wall faces (``nut_wall_face``).  Both
wall functions use the WALL Cmu (0.09, wallCoeffs), never rCmu -- OpenFOAM's
arrangement, kept.  Three things differ in kind:

1. Cmu is the FIELD rCmu, a function of the velocity gradient, so nut (hence
   the momentum stress, d_f and the MWI flux) carries a Jacobian column into
   grad U that kEpsilon's constant Cmu does not have.
2. The epsilon production is ``C1 magS eps``: it reads neither G nor GbyNu, so
   the wall-cell G override cannot reach the epsilon row by construction
   (kEpsilon needs its ``C1 GbyNu Cmu k`` spelling for the same effect).
3. The epsilon sink's denominator is ``k + sqrt(nu eps)``, finite as k -> 0, so
   the k floor is no longer load-bearing there.

Deviations from OpenFOAM, each with its reason:

- The k row's ``SuSp((2/3) divU, k)`` (rKE.C:319) is OMITTED, as kEpsilon and
  LaunderSharmaKE omit their divU terms and for the same measured reason
  (kepsilon.py's deviation list: 0.22% of the k row's L2 residual at OpenFOAM's
  own converged field, max|r| unchanged in five digits).  In this incompressible
  host divU is the discrete continuity residual, which the same Newton system
  drives to zero, so the term vanishes at every root; keeping it would only add
  a k <- (u, p) Jacobian coupling.  realizableKE.C has no divU term in the
  epsilon row at all (unlike kEpsilon.C:259), so there is no C3 to omit.
- ``bound(k, kMin)`` / ``bound(epsilon, epsilonMin)`` (rKE.C:211-212, :307,
  :329) clip the state; here every site that reads k or epsilon reads it
  through the same SMOOTH floors as kepsilon.py, and the state is never clipped.
- Zero strain.  ``magS`` and ``Us`` are square roots of invariants that vanish
  at U = 0 (the cold start, and every no-slip wall ghost), where a bare sqrt has
  an infinite slope; both take ``sqrt(x + s2SqrtEps^2)`` (value change below
  fp64 at O(1) strain).  ``W`` keeps OpenFOAM's denominator ``magS S2 + SMALL``
  with SMALL = 1e-15 (``rCmuWSmall``, OpenFOAM's double SMALL) and the floored
  magS, so W is C-infinity and exactly 0 at S = 0 (the cubic numerator vanishes
  faster than the denominator).  The acos argument is clamped to
  ``[-1 + rCmuAcosEps, 1 - rCmuAcosEps]`` instead of [-1, 1] (rKE.C:66): acos'
  is infinite at +-1, and at +1 autograd would form 0*inf = NaN even though
  cos(acos(x)/3) has the finite slope 1/9 there.  |sqrt(6) W| <= 1 holds
  analytically for a traceless symmetric S (it is 1 exactly for axisymmetric
  strain), so the clamp only moves states within 1e-12 of the axisymmetric
  limit; the largest value change it can make is sqrt(1e-12/6) ~ 4e-7 in
  cos(phis), at axisymmetric compression.  Every branch is evaluated on safe
  arguments (the SA lesson in spalart_allmaras.py), so value and gradient are
  finite at zero strain, at zero rotation and at the axisymmetric limits.
- ``nuLimited = max(nu, 0)`` (rKE.C:276-283) is the compressible SAF guard
  against a transiently negative thermo nu; here nu is a positive registration
  constant and the max is the identity, so it is not spelled.
- ``C1 = max(eta/(5 + eta), 0.43)`` is transcribed as the exact hard max by
  default.  It is a limiter that IS active in the operating region (equilibrium
  shear sits at eta ~ 3.3, where eta/(5+eta) = 0.40 < 0.43), which design 2.3.3
  says should be a smooth blend for deep Newton convergence; ``C1Sharpness`` > 0
  switches to ``smooth_max`` (overshoot <= ln2/C1Sharpness at the tie).  The
  default stays exact so that the closure is the same as OpenFOAM's for parity.
- No buoyancy production: rKE.C has none, and v2412's
  ``buoyantBoussinesqSimpleFoam`` adds none without an fvOption.  ``Cg``
  defaults to 0.0 and at 0.0 the Gb term is skipped STRUCTURALLY (the k row is
  bit-identical to a host that supplies no buoyant operands); a non-zero ``Cg``
  adds the package's SGDH Gb to the k row only (EddyViscosityModel.
  buoyancy_production), exactly as for the other models.
- Lagged nut: rKE.C:267 forms G with the nut of the PREVIOUS ``correct()`` and
  :331 updates it last; a Newton residual evaluates nut at the current state.
  The two coincide at a converged state, the only one where the codes are
  compared (same as kEpsilon).
- Ghost rows of nut.  rCmu needs grad U, which the host reconstructs for
  interior cells only (ghost rows of ``grad_uvw_cpd_cell`` are zero).  Ghost
  rows take the OWNER cell's strain invariants (``solver.ghost_fill_owner``, the
  first-order closure kOmegaSST uses for sqrt S2) with the ghost's own k and
  epsilon.  OpenFOAM evaluates rCmu on the boundary field of fvc::grad(U)
  (rKE.C:94-97); the two differ by the patch gradient extrapolation.  Only
  NON-wall boundary faces read this value (wall faces are overwritten by
  ``nut_wall_face``).
- The kEpsilon wall-function deviations apply unchanged (``y`` is the normal
  projection, the nut wall patches are the epsilonWallFunction patches, only
  the stepwise/tanh nutk blenders exist); see kepsilon.py.

Thermal wall function (``alphat_wall_face``, config ``[turbulence] wallAlphat``):
``"nutByPrt"`` (default) leaves the host's ``alphaEff = alpha + nut_face/Prt``
untouched on every face, wall faces included, so every existing case is
bit-for-bit unchanged; ``"jayatilleke"`` replaces alphat on the wall faces of
the epsilonWallFunction patches by ``alphatJayatillekeWallFunction``
(wall_functions.alphat_jayatilleke, aJWF.C:222-286), whose y+ is
``nutkWallFunction::yPlus()`` (nutkWF.C:243-292, via aJWF.C:73-82) and whose
molecular Prandtl number is the host's own ``nu/alpha`` on that face -- the Pr
that defines alpha in TEqn.H:5 (``alphaEff = nu/Pr + alphat``) and that aJWF.C
reads from transportProperties (:248-255).  ``wallAlphatPrt`` is the PATCH Prt
(aJWF.C:181, a forced read, 0.85 in the dctwin cases), deliberately separate
from the solver-level ``[transport] Prt`` of the interior ``alphat = nut/Prt``
(TEqn.H:2) -- OpenFOAM keeps the two apart and so does this.

Verification: tests/test_realizable_ke_gradcheck.py (closures incl. zero-strain
and below-floor probes, assembled residual channels on a wall-function box),
tests/test_realizable_ke_closure.py (rCmu against a numpy transcription and its
limits), tests/test_alphat_jayatilleke.py (aJWF against a numpy transcription
over y+ 1..1000), tests/test_realizable_ke_case_smoke.py (registry -> buoyant
host -> coupled-Newton residual and JVP, wall hooks).
"""
import math

import torch

from FVsolver.FVdiscretization.FVstress import stress_double_inner
from FVsolver.Turbulence.eddy_viscosity import EddyViscosityModel
from FVsolver.Turbulence.smooth import smooth_floor, smooth_max, sqrt_floored
from FVsolver.Turbulence.wall_functions import (
    alphat_jayatilleke,
    jayatilleke_p,
    jayatilleke_y_plus_therm,
    nutk_wall_nut,
    nutk_y_plus,
    y_plus_lam,
)

_SQRT2 = math.sqrt(2.0)
_SQRT6 = math.sqrt(6.0)
# rKE.C:265 literals -- part of the model's definition, not coefficients.
_C1_MIN = 0.43
_C1_ETA_OFFSET = 5.0

_WALL_ALPHAT_CHOICES = ("nutByPrt", "jayatilleke")

_DEFAULTS = {
    # realizableKE.C constructor defaults (:149-184), read again in read()
    # (:224-237); rKE.H:52-58 documents the same four.
    "A0": 4.0,          # rKE.C:149-157
    "C2": 1.9,          # rKE.C:158-166
    "sigmak": 1.0,      # rKE.C:167-175
    "sigmaEps": 1.2,    # rKE.C:176-184
    # Smooth floors replacing bound() -- sized exactly like KEpsilon's (and the
    # epsilonWallFunction BC's own kMin/kMinWidth, which must match).
    "kMin": 1e-10,
    "kMinWidth": 1e-8,
    "epsilonMin": 1e-10,
    "epsilonMinWidth": 1e-8,
    # sqrt(x + eps^2) for magS and Us (module docstring, "Zero strain").  Same
    # value and sizing as kOmegaSST's s2SqrtEps: a value change ~eps^2/(2x)
    # below fp64 at O(1) strain, a finite slope at x = 0.
    "s2SqrtEps": 1e-8,
    # OpenFOAM's SMALL in W's denominator (rKE.C:58, dimensionedScalar
    # "small" [s^-3] = doubleScalarSMALL = 1e-15).  Must be > 0: it is what
    # makes W smooth at S = 0.
    "rCmuWSmall": 1e-15,
    # Margin of the acos clamp (module docstring).  0 reproduces rKE.C:66's
    # [-1, 1] and gives NaN gradients at the axisymmetric limits.
    "rCmuAcosEps": 1e-12,
    # 0 = OpenFOAM's exact hard max in C1 (rKE.C:265); > 0 = smooth_max with
    # this sharpness (design 2.3.3 blend).
    "C1Sharpness": 0.0,
    # Buoyancy production coefficient.  0.0: realizableKE.C has no buoyancy
    # term (module docstring); the Gb term is then skipped structurally.
    "Cg": 0.0,
    # wallFunctionCoefficients (wallCoeffs.C defaults), a separate set from
    # the model's, exactly as in KEpsilon; yPlusLam is derived, not a knob.
    "wallCmu": 0.09,
    "wallKappa": 0.41,
    "wallE": 9.8,
    # nutkWallFunction blender; see KEpsilon's DEFAULTS for the measured cost
    # of anything but v2412's per-BC default.
    "wallBlender": "stepwise",
    # sqrt(|snGrad U|^2 + eps^2) at the wall face (G override and the viscous
    # y+ of the thermal wall function).
    "wallGradUEps": 1e-8,
    # Thermal wall function (module docstring).  "nutByPrt" keeps the host's
    # alpha + nut_face/Prt on wall faces (every existing case unchanged).
    "wallAlphat": "nutByPrt",
    # alphatJayatillekeWallFunction's PATCH coefficients (aJWF.C:146-148 /
    # :181-183): Prt is a forced read in OpenFOAM, 0.85 in the dctwin cases;
    # kappa and E default to the same numbers as the nut wall function's but
    # belong to a different patch field, so they are kept separate.
    "wallAlphatPrt": 0.85,
    "wallAlphatKappa": 0.41,
    "wallAlphatE": 9.8,
}


class RealizableKE(EddyViscosityModel):
    """Realizable k-epsilon with wall functions, primal (k, epsilon)."""

    # The same pair, BC attributes and residual names as KEpsilon: the BC
    # layer's kqRWallFunction / epsilonWallFunction classes (and their
    # epsilon cell constraint) work unchanged.
    TRANSPORTED: tuple = (
        ("k", 1, "bc_k_cpd_cell", "residual_k"),
        ("epsilon", 1, "bc_epsilon_cpd_cell", "residual_epsilon"),
    )
    LOG_TRANSFORMED_BC_FIELDS: tuple = ()
    DEFAULTS = _DEFAULTS
    # epsilonWallFunction pins EPSILON in the wall cell (wall_cell_map family).
    WALL_CONSTRAINT_VAR: str = "TURB_EPSILON"

    def __init__(self, params, solver):
        super().__init__(params, solver)
        name = type(self).__name__
        if self.wallAlphat not in _WALL_ALPHAT_CHOICES:
            raise ValueError(
                f"[{name}] [turbulence] wallAlphat must be one of "
                f"{list(_WALL_ALPHAT_CHOICES)}, got {self.wallAlphat!r}")
        for knob in ("s2SqrtEps", "rCmuWSmall", "wallAlphatPrt",
                     "wallAlphatKappa", "wallAlphatE"):
            if not getattr(self, knob) > 0.0:
                raise ValueError(
                    f"[{name}] [turbulence] {knob} must be > 0, got "
                    f"{getattr(self, knob)}")
        if not 0.0 <= self.rCmuAcosEps < 1.0:
            raise ValueError(
                f"[{name}] [turbulence] rCmuAcosEps must be in [0, 1), got "
                f"{self.rCmuAcosEps}")
        if self.C1Sharpness < 0.0:
            raise ValueError(
                f"[{name}] [turbulence] C1Sharpness must be >= 0 (0 = exact "
                f"max), got {self.C1Sharpness}")
        # Derived, never configured (wallCoeffs.C), as in KEpsilon.
        self.wallYPlusLam = y_plus_lam(self.wallKappa, self.wallE)
        # Per-forward stash of the cpd rCmu (diagnostics and tests).
        self._r_cmu_cpd = None
        # Jayatilleke per-face constants (P, yPlusTherm, Pr), keyed on the
        # bcpatch_batch object like the wall-cell-map cache.
        self._alphat_constants_cache = None

    # ------------------------------------------------------------------
    # closure algebra -- pure tensor functions, gradcheck-gated directly
    # ------------------------------------------------------------------
    def k_floored(self, k: torch.Tensor) -> torch.Tensor:
        return smooth_floor(k, self.kMin, self.kMinWidth)

    def epsilon_floored(self, epsilon: torch.Tensor) -> torch.Tensor:
        return smooth_floor(epsilon, self.epsilonMin, self.epsilonMinWidth)

    def sqrt_k(self, k: torch.Tensor) -> torch.Tensor:
        """sqrt of the FLOORED k -- the wall-function operand (same
        floor-then-sqrt composition and reason as KEpsilon.sqrt_k)."""
        return torch.sqrt(self.k_floored(k))

    @staticmethod
    def strain_invariants(grad_u: torch.Tensor):
        """``(S2, SSS, Om2)``, each ``[.., 1]``, from ``grad_u [.., 3, 3]``.

        ``S = devSymm(grad U)`` (rKE.C:50; devSymm = symm - tr/3 I,
        TensorI.H:773-783), ``S2 = 2 S:S`` (rKE.C:261 with SymmTensor magSqr =
        the full double-dot, SymmTensorI.H:599-607), ``SSS = (S.S):S``
        (rKE.C:55) and ``Om2 = |skew(grad U)|^2`` (rKE.C:69).  All three are
        invariant under transposing ``grad_u``, so the FVstress layout (the
        transpose of OpenFOAM's ``fvc::grad(U)``) needs no conversion.
        """
        trace = grad_u.diagonal(dim1=-2, dim2=-1).sum(dim=-1)
        eye = torch.eye(3, dtype=grad_u.dtype, device=grad_u.device)
        s = (0.5 * (grad_u + grad_u.transpose(-2, -1))
             - (trace / 3.0)[..., None, None] * eye)
        s2 = 2.0 * (s * s).sum(dim=(-2, -1))[..., None]
        sss = (torch.matmul(s, s) * s).sum(dim=(-2, -1))[..., None]
        om = 0.5 * (grad_u - grad_u.transpose(-2, -1))
        om2 = (om * om).sum(dim=(-2, -1))[..., None]
        return s2, sss, om2

    def mag_s(self, s2: torch.Tensor) -> torch.Tensor:
        """magS = sqrt(S2) (rKE.C:262), floored: sqrt(S2 + s2SqrtEps^2)."""
        return sqrt_floored(s2, self.s2SqrtEps)

    def r_cmu_from_invariants(self, s2, sss, om2, k, epsilon) -> torch.Tensor:
        """rCmu (rKE.C:43-72) from the strain invariants and FLOORED k, eps.

        Safe branches: W's denominator is >= rCmuWSmall > 0; the acos
        argument is clamped strictly inside (-1, 1); Us is a floored sqrt.
        """
        mag_s = self.mag_s(s2)
        w = (2.0 * _SQRT2) * sss / (mag_s * s2 + self.rCmuWSmall)
        bound = 1.0 - self.rCmuAcosEps
        phis = torch.acos(torch.clamp(_SQRT6 * w, -bound, bound)) / 3.0
        a_s = _SQRT6 * torch.cos(phis)
        u_s = sqrt_floored(0.5 * s2 + om2, self.s2SqrtEps)
        return 1.0 / (self.A0 + a_s * u_s * self.k_floored(k)
                      / self.epsilon_floored(epsilon))

    def r_cmu(self, grad_u, k, epsilon) -> torch.Tensor:
        """rCmu straight from ``grad_u [.., 3, 3]`` (convenience/tests)."""
        s2, sss, om2 = self.strain_invariants(grad_u)
        return self.r_cmu_from_invariants(s2, sss, om2, k, epsilon)

    def nut_cell(self, r_cmu, k, epsilon) -> torch.Tensor:
        """nut = rCmu k^2/eps (rKE.C:83), floored fields, no limiter."""
        kf = self.k_floored(k)
        return r_cmu * kf * kf / self.epsilon_floored(epsilon)

    def c1(self, eta: torch.Tensor) -> torch.Tensor:
        """C1 = max(eta/(5 + eta), 0.43) (rKE.C:265); exact unless
        ``C1Sharpness`` > 0 (module docstring)."""
        a = eta / (_C1_ETA_OFFSET + eta)
        floor = torch.full_like(a, _C1_MIN)
        if self.C1Sharpness > 0.0:
            return smooth_max(a, floor, self.C1Sharpness)
        return torch.maximum(a, floor)

    def k_source(self, nut, gbynu, k, epsilon, pk=None) -> torch.Tensor:
        """k-row source density [m^2/s^3]: G - Sp(eps/k, k) (rKE.C:318-320).

        Floored coefficient times the PRIMAL k (exactly -epsilon off the
        floor); ``pk`` substitutes the wall-function G in wall cells.  No Pk
        limiter (rKE.C:318 puts G in bare).  The divU SuSp (:319) is omitted
        (module docstring).
        """
        if pk is None:
            pk = nut * gbynu
        return pk - (self.epsilon_floored(epsilon) / self.k_floored(k)) * k

    def epsilon_source(self, mag_s, k, epsilon, nu) -> torch.Tensor:
        """epsilon-row source density [m^2/s^4] (rKE.C:292-297)::

            C1(eta) magS eps_f  -  C2 eps_f/(k_f + sqrt(nu eps_f)) eps

        The production is an explicit field at the (bounded, here floored)
        epsilon; the destruction keeps OpenFOAM's Sp structure (floored
        coefficient times the primal epsilon).  ``eta = magS k_f/eps_f``
        (rKE.C:264).  Neither G nor GbyNu appears, so the wall-cell G
        override never reaches this row (module docstring, point 2).
        """
        kf = self.k_floored(k)
        eps_f = self.epsilon_floored(epsilon)
        c1 = self.c1(mag_s * kf / eps_f)
        return (c1 * mag_s * eps_f
                - self.C2 * eps_f / (kf + torch.sqrt(nu * eps_f)) * epsilon)

    # ------------------------------------------------------------------
    # wall functions
    # ------------------------------------------------------------------
    def nut_wall_face(self, solver, state, nut_face):
        """``nutkWallFunction`` on the wall faces of the epsilonWallFunction
        patches -- identical to ``KEpsilon.nut_wall_face`` (the wall function
        belongs to the patch, not to the model; see its docstring for the
        patch-set proxy and the k-based y+)."""
        rows = self.wall_cell_map(solver)
        if rows is None:
            return nut_face
        face_idx, _ghost_cpd, cell_cpd, _cell_row, corner_slot = rows
        nu_w = solver.nu_coeff.index_select(-2, face_idx)
        y_w = solver.wall_distance_face.index_select(-2, face_idx)
        k_c = state["k_cpd_cell"].index_select(
            -2, cell_cpd.index_select(0, corner_slot))
        nut_w = nutk_wall_nut(nu_w, y_w, self.sqrt_k(k_c), self.wallCmu,
                              self.wallKappa, self.wallE, self.wallYPlusLam,
                              blender=self.wallBlender)
        return nut_face.index_copy(-2, face_idx, nut_w)

    def _alphat_constants(self, solver, face_idx):
        """Per-wall-face ``(Pr, P, yPlusTherm)`` for aJWF, cached.

        Pr = nu/alpha on the face: the molecular Prandtl number the host's
        energy row actually uses (alpha = nu/Pr at load, Load_mesh), i.e. the
        transportProperties Pr of aJWF.C:252-255.  P and yPlusTherm depend on
        Pr/Prt only (aJWF.C:264-268) and are computed per DISTINCT Pr in
        Python floats with the exact OpenFOAM iteration, then scattered back.
        Registration-time constants: no gradient flows through them.
        """
        # Keyed on the registration AND the patch coefficients, so a knob
        # changed on a live model cannot read stale constants.
        batch = getattr(solver, "bcpatch_batch", None)
        coeffs = (self.wallAlphatPrt, self.wallAlphatKappa, self.wallAlphatE)
        cache = self._alphat_constants_cache
        if cache is not None and cache[0] is batch and cache[1] == coeffs:
            return cache[2]
        with torch.no_grad():
            nu_w = solver.nu_coeff.index_select(-2, face_idx).detach()
            alpha_w = solver.alpha_coeff.index_select(-2, face_idx).detach()
            pr = nu_w / alpha_w
            distinct, inverse = torch.unique(pr, return_inverse=True)
            p_vals, ypt_vals = [], []
            for value in distinct.reshape(-1).tolist():
                prat = value / self.wallAlphatPrt
                p = jayatilleke_p(prat)
                p_vals.append(p)
                ypt_vals.append(jayatilleke_y_plus_therm(
                    p, prat, self.wallAlphatKappa, self.wallAlphatE))
            p_t = torch.tensor(p_vals, dtype=pr.dtype, device=pr.device)
            ypt_t = torch.tensor(ypt_vals, dtype=pr.dtype, device=pr.device)
            constants = (pr, p_t[inverse].reshape(pr.shape),
                         ypt_t[inverse].reshape(pr.shape))
        self._alphat_constants_cache = (batch, coeffs, constants)
        return constants

    def alphat_wall_face(self, solver, state, alphat_face):
        """Turbulent thermal diffusivity on the wall faces (base.py hook).

        ``wallAlphat = "nutByPrt"`` returns ``alphat_face`` itself (the same
        tensor object), so the host's ``alpha + nut_face/Prt`` is bit-for-bit
        what it was.  ``"jayatilleke"`` overwrites the wall faces of the
        epsilonWallFunction patches with ``alphatJayatillekeWallFunction``
        (aJWF.C:222-286):

            y+     = nutkWallFunction::yPlus()      (aJWF.C:73-82 ->
                     nutkWF.C:279-290: k-based, or u_tau-based below yPlusLam,
                     with nuEff_w = nu_w + this forward's wall nut)
            alphat_w = max(0, nu (y+/(Prt (log(E y+)/kappa + P)) - 1/Pr))
                     if y+ > yPlusTherm else 0          (aJWF.C:271-282)

        Patch selection is the epsilon wall-function set, the same proxy
        ``nut_wall_face`` uses (no alphat field exists here to carry a BC).
        ``state`` is the host's BC-enforced fvfield (``k_cpd_cell`` and
        ``uvw_cpd_cell``, or a closure state with ``u_cpd``), read after
        ``nu_eff_face`` filled the nut stash this forward.
        """
        if self.wallAlphat == "nutByPrt":
            return alphat_face
        rows = self.wall_cell_map(solver)
        if rows is None:
            return alphat_face
        face_idx, ghost_cpd, cell_cpd, _cell_row, corner_slot = rows
        owner_cpd = cell_cpd.index_select(0, corner_slot)
        nu_w = solver.nu_coeff.index_select(-2, face_idx)
        y_w = solver.wall_distance_face.index_select(-2, face_idx)
        k_c = state["k_cpd_cell"].index_select(-2, owner_cpd)
        u_cpd = state["u_cpd"] if "u_cpd" in state else (
            state["uvw_cpd_cell"][..., 0:3])
        # |snGrad U| at the wall face: the expression and floor of
        # EddyViscosityModel.wall_function_production.
        d_u = ((u_cpd.index_select(-2, ghost_cpd)
                - u_cpd.index_select(-2, owner_cpd))
               * solver.nonOrthDeltaCoeffs.index_select(-2, face_idx))
        mag_grad_uw = sqrt_floored((d_u * d_u).sum(dim=-1, keepdim=True),
                                   self.wallGradUEps)
        nu_eff_w = nu_w + self.nut_face(solver, state).index_select(
            -2, face_idx)
        yplus = nutk_y_plus(nu_w, y_w, self.sqrt_k(k_c), nu_eff_w,
                            mag_grad_uw, self.wallCmu, self.wallYPlusLam)
        pr, p, ypt = self._alphat_constants(solver, face_idx)
        alphat_w = alphat_jayatilleke(nu_w, yplus, pr, p, ypt,
                                      self.wallAlphatPrt,
                                      self.wallAlphatKappa, self.wallAlphatE)
        return alphat_face.index_copy(-2, face_idx,
                                      alphat_w.to(alphat_face.dtype))

    # ------------------------------------------------------------------
    # contract
    # ------------------------------------------------------------------
    def _nut_cell_from_state(self, solver, state) -> torch.Tensor:
        """cpd-level nut, once per forward from ``nu_eff_face``.

        Reads the unified WLSQ velocity gradient the hosts stash in the
        fvfield before calling ``nu_eff_face`` (``grad_uvw_cpd_cell``, the
        same entry kOmegaSST reads).  Ghost rows: owner strain invariants,
        ghost k/epsilon (module docstring).
        """
        k_cpd = state["k_cpd_cell"]
        eps_cpd = state["epsilon_cpd_cell"]
        s2, sss, om2 = self.strain_invariants(
            state["grad_uvw_cpd_cell"][..., 0:3, :])
        invariants = torch.cat((s2, sss, om2), dim=-1)
        fill = getattr(solver, "ghost_fill_owner", None)
        if fill is not None:
            invariants = fill(invariants)
        r_cmu = self.r_cmu_from_invariants(
            invariants[..., 0:1], invariants[..., 1:2], invariants[..., 2:3],
            k_cpd, eps_cpd)
        self._r_cmu_cpd = r_cmu
        return self.nut_cell(r_cmu, k_cpd, eps_cpd)

    def transport_closures(self, solver, state) -> dict:
        """Face diffusivities and per-cell source DENSITIES for k and epsilon.

        Required ``state`` keys (host-built, no stencil math here):
        ``k_int``, ``epsilon_int``, ``grad_u_int``, ``u_cpd``,
        ``nu_cell_int``.  No derived gradients are requested.
        """
        nut_face = self.nut_face(solver, state)
        nut_int = self._nut_cell[..., solver.mask_interior_cell, :]
        grad_u = state["grad_u_int"]
        gbynu = stress_double_inner(grad_u)
        s2, _sss, _om2 = self.strain_invariants(grad_u)
        mag_s = self.mag_s(s2)
        k_int = state["k_int"]
        eps_int = state["epsilon_int"]

        # Wall-adjacent cells take the wall-function G (epsWF.C:331-336 ->
        # :532, reached from rKE.C:270); uncapped, as rKE.C:318.
        wall = self.wall_function_production(solver, state, k_int)
        pk = nut_int * gbynu
        if wall is not None:
            pk = pk.index_copy(-2, wall[0], wall[1])
        k_source = self.k_source(nut_int, gbynu, k_int, eps_int, pk=pk)
        eps_source = self.epsilon_source(mag_s, k_int, eps_int,
                                         state["nu_cell_int"])

        # No Gb in realizableKE.C: skipped structurally at the default Cg = 0.
        if self.Cg != 0.0:
            gb = self.buoyancy_production(nut_int, state)
            if gb is not None:
                k_source = k_source + gb
        return {
            "k": {
                # DkEff = nut/sigmak + nu (rKE.H:164-174)
                "diffusivity_face": solver.nu_coeff + nut_face / self.sigmak,
                "source_cell": k_source,
            },
            "epsilon": {
                # DepsilonEff = nut/sigmaEps + nu (rKE.H:177-187)
                "diffusivity_face": solver.nu_coeff + nut_face / self.sigmaEps,
                "source_cell": eps_source,
            },
        }

    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        """Nothing here, as for KEpsilon: the epsilon cell constraint is a BC
        (setEpsilon.Epsilonwallfunction), the G override and the wall nut /
        alphat are closures, and k's wall BC is kqRWallFunction
        (zeroGradient) through the SetBC machinery."""
        return {}
