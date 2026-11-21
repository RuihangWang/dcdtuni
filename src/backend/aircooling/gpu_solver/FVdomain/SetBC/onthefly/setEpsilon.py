"""Boundary conditions for the dissipation rate epsilon (primal variable).

Two wall treatments live here, and they are different physics, not two
spellings of one:

``type = "wall"``   the LOW-Re condition, ``eps~ = 0``.  The 1974
                    Launder-Sharma split ``eps = eps~ + D`` is constructed so
                    the transported variable vanishes at a wall, so this is a
                    plain uniform Dirichlet through the shared factory.
``type = "epsilonWallFunction"``  the HIGH-Re condition
                    (``Epsilonwallfunction`` below, also reachable as
                    ``type = "wallFunction"``).  epsilon has no finite wall
                    value at all here -- it is PINNED IN THE WALL-ADJACENT
                    CELL at the log-layer estimate.

Everything else (fixedValue / zeroGradient / cyclic / empty / symmetry /
calculated) comes from ``setTurbScalar.make_scalar_bc_classes`` with no value
transform: epsilon is stored primal in both models (no ``LOG_TRANSFORMED_BC_FIELDS``
entry), so bc/epsilon.toml values land in the ghost rows unchanged.
"""

import torch

from FVdomain.SetBC.onthefly.BCbase import BoundaryCondition, BCType, VARType
from FVdomain.SetBC.onthefly.Parafunction import ParametricManager
from FVdomain.SetBC.onthefly.setTurbScalar import make_scalar_bc_classes

# wall_value 0.0: the 1974 Launder-Sharma split eps = eps_tilde + D is
# constructed so eps_tilde vanishes at walls (phase-3b verifier's readiness
# verdict, commit 114eafd) -- bc tomls say ``type = "wall"``.
_classes = make_scalar_bc_classes(
    "epsilon", VARType.TURB_EPSILON, value_transform=None, wall_value=0.0)

Fixedvalue = _classes["Fixedvalue"]
Zerogradient = _classes["Zerogradient"]
Cyclic = _classes["Cyclic"]
Empty = _classes["Empty"]
Calculated = _classes["Calculated"]
Symmetry = _classes["Symmetry"]
Wall = _classes["Wall"]


class Epsilonwallfunction(BoundaryCondition):
    """v2412's ``epsilonWallFunction`` at its shipped defaults.

    The class name is what ``Load_mesh._get_bc_class`` derives from
    ``type = "epsilonWallFunction"`` (``bc_type[0].upper() + bc_type[1:].lower()``,
    Load_mesh.py:240), so a bc toml keeps OpenFOAM vocabulary.

    **What it imposes.**  For every wall-adjacent cell ``c`` with
    ``epsilonWallFunction`` faces ``F(c)``::

        eps_wall(c) = mean over f in F(c) of  Cmu^0.75 k_c^1.5 / (kappa y_f)

    written into the ghost rows AND -- through ``constrains_adjacent_cell``
    -- substituted for the CELL's entire epsilon transport row.

    **Trap 1, the constraint's LOCATION.**  ``updateCoeffs`` writes
    ``epsilon[celli] = epsilon0[celli]`` into the INTERNAL field
    (epsilonWallFunctionFvPatchScalarField.C:542) and ``manipulateMatrix``
    then calls ``matrix.setValues(patch().faceCells(), patchInternalField())``
    (ibid.:616), which cuts the cell's couplings and leaves
    ``psi[celli] = value; source_[celli] = value*Diag[celli]``
    (fvMatrix.C:310-317) -- the wall-adjacent cell's epsilon equation is
    DISCARDED, not merely supplied with a boundary value.  This is the same
    defect the omega work paid for: writing only the ghost left our first-cell
    omega 79.2% below OpenFOAM's on the TMR plate while our own row was
    satisfied to 1.3e-09.  ``constrains_adjacent_cell`` is what
    replaces the row; the substitution happens inside the differentiated
    residual (``BaseSolver._apply_cell_constraints``), so the Jacobian picks
    it up by autograd.

    The patch FACE value v2412 also sets (``epf == scalarField(epsilon0,
    faceCells)``, ibid.:175, under its comment "Apply zero-gradient condition
    for epsilon" at :168) falls out of the same write: ghost == cell at any
    root drives this face's snGrad to zero.

    **Trap 3, which the omega condition does NOT have: the value is
    STATE-DEPENDENT.**  omega's low-Re wall value ``6 nu/(beta1 y^2)`` is pure
    geometry, so ``setOmega.Wall``'s constraint row has derivative
    ``d/d(eps_c) = 1`` and nothing else.  This value reads ``k`` in the SAME
    cell (epsWF.C:227-234 evaluates ``k[faceCells[facei]]``), so the
    constraint row carries an epsilon-row/k-column entry.  That entry is
    inside the cell, hence already inside the assembled sparsity pattern's
    block diagonal -- but it must be verified, not assumed, and it must not be
    dropped: ``eval`` therefore stays differentiable in ``k`` and
    ``_apply_cell_constraints`` reads the value back from the ghost this
    method wrote, so autograd carries the derivative on the path the residual
    already travels.

    **Corner cells.**  ``cornerWeights_ = 1/(epsilonWallFunction faces on this
    cell)`` (epsWF.C:107-121) makes every accumulation an arithmetic mean of
    EPSILON ITSELF (epsWF.C:263).  epsilon is stored primal, so
    ``combine_cell_constraints`` is a plain scatter-mean -- copying
    ``setOmega.Wall``'s exp/log round trip would be a bug, since that exists
    only because omega is stored as ln omega.

    **What is dead at the shipped defaults.**  ``lowReCorrection`` defaults to
    false (epsWF.C:414), and the STEPWISE branch's low-Re test is
    ``lowReCorrection_ && yPlus < yPlusLam`` (epsWF.C:242), so the viscous
    branch ``2 k nu/y^2`` (epsWF.C:217-224) is unreachable and the log-layer
    form applies at EVERY y+.  No y+ test is implemented here, and none
    belongs.  Consequently this BC also does not silently protect a
    wall-RESOLVED mesh: OpenFOAM does not either (it imposes the log-layer
    epsilon at y+ ~ 0.2 without a warning), so "it ran and converged" is not
    evidence the mesh suits wall functions.
    """

    constrains_adjacent_cell = True

    #: Set from ``constraintForm`` in the patch's bc toml entry; read by
    #: ``BaseSolver._cell_constrained_rows``.  "difference" (default) is
    #: ``eps_c - eps_wall``, v2412's row.  "relative" is
    #: ``eps_c/eps_wall - 1``, the same root with the ``1/y`` growth divided
    #: out of the Jacobian row.
    constraint_form = "difference"

    @staticmethod
    def combine_cell_constraints(values, slot, n_cells):
        """Per-face wall values -> one value per constrained CELL: a plain
        scatter-MEAN, in the PRIMAL variable.

        ``values`` is face-ordered and ``slot`` says which cell each face
        belongs to, so this is a permutation on every mesh (wall faces are not
        in cell order) and additionally a mean where a cell owns several wall
        faces -- v2412's ``cornerWeights_`` (epsWF.C:107-121, applied :244-263).
        The scatter runs on the ENTITY axis (-2), leaving any leading
        time/batch axes untouched, per the ``BoundaryCondition`` tensor
        contract.
        """
        lead = values.shape[:-2]
        summed = torch.zeros(*lead, n_cells, values.shape[-1],
                             dtype=values.dtype, device=values.device
                             ).index_add(-2, slot, values)
        count = torch.zeros(*lead, n_cells, 1, dtype=values.dtype,
                            device=values.device).index_add(
                                -2, slot, torch.ones_like(values[..., :1]))
        return summed / count

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: dict,
                 parametric_manager: ParametricManager = None):
        # wallFunctionCoefficients are read from the PATCH dict in v2412
        # (epsWF.C:417 -> wallCoeffs.C:73-77), NOT from kEpsilonCoeffs -- a
        # case can legally give the model and the wall different Cmu with no
        # warning.  Same defaults (wallCoeffs.C:61-62).  ``E`` is not read:
        # only nutkWallFunction's log law uses it, and no y+ test survives
        # here at lowReCorrection = false.
        self.Cmu = float(boundary_info.get("Cmu", 0.09))
        self.kappa = float(boundary_info.get("kappa", 0.41))
        if self.Cmu <= 0.0 or self.kappa <= 0.0:
            raise ValueError(
                f"[setEpsilon.Epsilonwallfunction] Cmu and kappa must be > 0, "
                f"got Cmu={self.Cmu}, kappa={self.kappa}")
        # Smooth floor on the k this value reads.  v2412's protection is
        # ``bound(k, kMin)`` on the volume field (kEpsilon.C:290), which
        # clips the STATE; this package never clips the state, so the floor
        # sits at the evaluation site instead -- and it must, because k^1.5 is
        # NaN for k < 0, which a cold start visits.  Defaults deliberately
        # equal KEpsilon's kMin/kMinWidth; a case that moves the model's floor
        # must move this one too, which is exactly the hazard v2412 has with
        # its two Cmu (recorded, and gated by
        # test_kepsilon_wall_functions.py::test_bc_floor_matches_model_floor).
        self.kMin = float(boundary_info.get("kMin", 1e-10))
        self.kMinWidth = float(boundary_info.get("kMinWidth", 1e-8))
        # Which ALGEBRAIC form the substituted cell row takes.  Default
        # "difference" is v2412's own row and is bit-identical to the code
        # that shipped before this option existed.  "relative" divides it by
        # eps_wall; see BaseSolver._CELL_CONSTRAINT_FORMS for the measurement
        # that motivates it and section 5.2 of
        # docs/research/2026-08-18-kepsilon-wall-function-validation.md for
        # the number it was built to close.
        # Which wall distance y the log-layer value reads.  "normal" (default) is the owner-centre normal
        # projection ``wall_distance_face`` every case used so far; "nearWallDist" is OpenFOAM's
        # ``turbulenceModel::y()`` (nearest point of this face and its point neighbours on the same patch,
        # ``Turbulence.near_wall_dist``), written into this patch's slice of ``wall_distance_face`` so the nutk,
        # G and alphat wall functions read the same y.
        self.wall_distance = str(boundary_info.get("wallDistance", "normal"))
        if self.wall_distance not in ("normal", "nearWallDist"):
            raise ValueError(
                f"[setEpsilon.Epsilonwallfunction] wallDistance must be 'normal' or 'nearWallDist', got "
                f"{self.wall_distance!r}")
        self.constraint_form = str(
            boundary_info.get("constraintForm", "difference"))
        if self.constraint_form not in ("difference", "relative"):
            raise ValueError(
                f"[setEpsilon.Epsilonwallfunction] constraintForm must be "
                f"'difference' or 'relative', got "
                f"{self.constraint_form!r}")
        # The sublayer blending, the patch dict's ``blending`` entry (v2506
        # epsWF.C:413, default stepwise).  "exponential" blends the viscous
        # 2 k nu/y^2 and the log-layer value smoothly in y+
        # (wall_functions.epsilon_wall_blend); a case that sets it sets the
        # same blending for nut ([turbulence] wallBlender), as the reference
        # code's case sets it on both patch entries.
        self.blending = str(boundary_info.get("blending", "stepwise"))
        if self.blending not in ("stepwise", "exponential"):
            raise ValueError(
                f"[setEpsilon.Epsilonwallfunction] blending must be "
                f"'stepwise' or 'exponential', got {self.blending!r}")
        super().__init__(patch_name, patch_idx, VARType.TURB_EPSILON,
                         BCType.FIRST)

    def init_field(self, phi_cpd_cell, fvpatch, **kwargs):
        # The value needs geometry (wall distance) and the live k field,
        # neither reachable here; mirror the owner as a placeholder.  ``eval``
        # writes the true per-face value at the top of every forward, before
        # any residual is assembled, so the placeholder never enters a flux.
        start = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_f = fvpatch.start_idx_face[self.patch_idx]
        end_f = fvpatch.end_idx_face[self.patch_idx]
        neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
        if neighbor_cpd_cell is not None:
            owner = neighbor_cpd_cell[0, start_f:end_f]
            phi_cpd_cell[start:end, 0:1] = phi_cpd_cell[owner, 0:1]
        return phi_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        from FVsolver.Turbulence.smooth import smooth_floor

        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        start = fvpatch_batch.start_idx_cpd_cell[global_patch_idx]
        end = fvpatch_batch.end_idx_cpd_cell[global_patch_idx]
        start_f = fvpatch_batch.start_idx_face[global_patch_idx]
        end_f = fvpatch_batch.end_idx_face[global_patch_idx]

        # y is read per WALL FACE; ghost rows start:end are face-ordered (the
        # alignment every mirror BC in this package relies on).  This is the
        # owner-cell-centre-to-face NORMAL PROJECTION, where v2412's
        # ``turbModel.y()[patchi]`` is the true nearest-point distance to the
        # wall polygon (nearWallDist.C:101-107); the two coincide when the
        # cell centre projects inside its own wall face.  Same tensor
        # setOmega.Wall reads -- recorded in kepsilon.py's deviation list.
        if self.wall_distance == "nearWallDist":
            from FVsolver.Turbulence.near_wall_dist import apply_near_wall_dist
            apply_near_wall_dist(integrator, int(start_f), int(end_f))
        y1 = integrator.wall_distance_face[start_f:end_f]

        # k at the wall-adjacent CELL, never at the face: epsWF.C:231 indexes
        # the VOLUME field at faceCells[facei].  The owner is an interior
        # cell, so this read does not depend on any BC having run first --
        # which matters, because k's own wall BC (kqRWallFunction =
        # zeroGradient) is a MIXED-type condition and runs in a later pass.
        owner = integrator.neighbor_cpd_cell[0, start_f:end_f]
        k_owner = fvfield["k_cpd_cell"][..., owner, 0:1]
        k_f = smooth_floor(k_owner, self.kMin, self.kMinWidth)

        # epsilonLog, epsWF.C:227-234.  Under the default stepwise blending the
        # viscous branch is dead code at lowReCorrection = false (class
        # docstring); the exponential blending weighs it in at every y+.
        eps_wall = ((self.Cmu ** 0.75) * k_f * torch.sqrt(k_f)
                    / (self.kappa * y1))
        if self.blending != "stepwise":
            from FVsolver.Turbulence.wall_functions import epsilon_wall_blend
            nu_w = integrator.nu_coeff[start_f:end_f]
            yplus = (self.Cmu ** 0.25) * y1 * torch.sqrt(k_f) / nu_w
            eps_vis = 2.0 * k_f * nu_w / (y1 * y1)
            eps_wall = epsilon_wall_blend(eps_vis, eps_wall, yplus, self.blending)

        phi = fvfield["epsilon_cpd_cell"]
        phi[..., start:end, 0:1] = eps_wall
        fvfield["epsilon_cpd_cell"] = phi
        return fvfield


# v2412 spells the high-Re epsilon wall condition ``epsilonWallFunction``;
# ``Wallfunction`` is the same class under the shorter ``type = "wallFunction"``
# a case may prefer.  One class, two names -- not a parallel implementation.
Wallfunction = Epsilonwallfunction
