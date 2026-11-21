"""MLZE -- the mixing-length zero-equation model, Bai et al. (2024) Eq. (4).

    nu_t = l^2 S,   l = min(kappa y, Clim y_max),   S = sqrt(2 Sij Sij)

with ``y`` the wall distance of the cell centre, ``y_max`` the largest wall
distance in the case, ``kappa = 0.419`` and ``Clim = 0.09`` (his Table 1 and
Eq. 4).  The outer cap is the classical Escudier form; the whole closure is
Prandtl's mixing length with that cap.

**Why this model is here.**  Bai F., Gong X.-M., Li H.-W., Guo H.-B., Tao
W.-Q., *Int. Comm. Heat Mass Transfer* **158** (2024) 107916 is the
DataCenter comparison target, and MLZE is what he actually runs: every
headline result -- his Tables 2 and 3, Figs. 5-12 -- is MLZE, while standard
k-epsilon appears only in his Sec. 4.6 as a comparator he measures and
rejects (his Table 5: k-epsilon limit-cycles 0.086-0.332 C on a 1e-3..3e-2
residual plateau, MLZE reads 0.000 at 2e-5).  Running anything else against
those tables is a labelled column, not a comparison.

Three properties matter beyond the paper match, and they are the reason this
is worth carrying in a repo that already has four transport models:

* **No transported field.**  ``TRANSPORTED = ()``: no extra unknowns, no
  extra residual rows, no wall functions, nothing for the assembler to
  colour.  A sibling line measured the coupled Newton stall to be driven by
  wall-normal cell stretching rather than by the closure (6653x separation
  from one blockMeshDict token, with the model exonerated three ways
  including deleting the turbulence rows entirely).  MLZE **removes those
  rows**, so it is a different system rather than a retuning of the same one.
* **Algebraic**, so it cannot run away: ``nu_t`` is bounded above by
  ``(Clim y_max)^2 max(S)`` by construction.  It therefore needs no viscosity
  limiter, and any limiter placed on it is a silent edit to the model rather
  than a guard -- an inherited ``limitTurbulenceViscosity c 1000`` was found
  clipping 26.43% of cells on the OpenFOAM side of exactly this closure.
* **Differentiable** everywhere the state can reach; see the note on the
  ``min`` below, which is not the hazard it looks like.

Smoothness, term by term (design section 2.3 conventions):

``min(kappa y, Clim y_max)`` is **pure geometry**.  Both branches are
functions of the MESH alone, so ``dl/dstate`` is identically zero on either
side of the kink and the non-differentiable point cannot enter the Jacobian
or the flow adjoint at all.  The mixing length is therefore built once under
``no_grad``, cached beside the wall distance, and spelled with a hard
``torch.minimum`` -- a smooth blend here would buy nothing and would perturb
the value of a quantity that has an exact closed form.  **If a mesh or shape
adjoint ever lands, this becomes a real kink** in ``d/dx_node``, and
``smooth.smooth_min`` is the drop-in; that is the only circumstance in which
it should be changed.

``S = sqrt(2 Sij Sij)`` **is** state-dependent, and a bare ``sqrt`` has an
infinite derivative at ``S = 0`` -- which is exactly a cold start from a
uniform field.  It goes through ``sqrt_floored`` with the ``s2SqrtEps`` knob
every other model in this package already uses for the same invariant.

``y_max`` is per CASE, scattered over ``batch_cpd_cell``, so a batched
mixed-mesh run can never let one case's geometry set another's cap -- the
same per-case discipline ``nearest_wall_distance_cpd`` applies to ``y``
itself.

**What ``y`` means, and the one ambiguity in Eq. (4), measured.**  This
package's ``nearest_wall_distance_cpd`` is the minimum EUCLIDEAN distance to
a wall-patch face centre, which ignores blockage: a cell shadowed by an
interior obstacle sees the wall through it.  OpenFOAM's default ``meshWave``
propagates through the mesh instead and travels around such an obstacle.
On the DataCenter room, whose rack block shadows a large region, the two
differ in TWO ways and only one of them has been measured end to end:

  the CAP    ``y_max`` = 1.629 m (Euclidean) against 2.076 m (meshWave), 27%
             apart, binding on 39.3% of cells.  **Measured at 0.085 C** across
             every quantity of Bai's Tables 2 and 3 by an arm that changes
             only this (docs/research/2026-08-18-dc-bai-likeforlike.md 8.1) --
             about the paper's own grid-independence margin.
  the FIELD  ``l_meshWave / (kappa y_Euclid)`` has median exactly 1.00000000,
             which hides a tail: **15.3% of uncapped cells differ by more than
             1e-6, up to a factor 1.52** where the rack shadows the straight
             line.  **NOT measured end to end**, and deliberately not folded
             into the 0.085 C.  Closing it needs an exact-distance wallDist,
             which v2412 does not ship, or a wave solve here.

The Euclidean reading is what this package computes and therefore what this
model uses.  **Whether that biases a comparison against the paper was settled
by measurement, not by argument** (same doc, 8.2): both readings were run to
convergence as separate OpenFOAM arms and scored against Bai's Tables 2 and 3,
and they are indistinguishable -- mean absolute error over his eleven
temperature rows 0.2400 C (meshWave) against 0.2373 C (Euclidean), a 6-5 split
on which row is closer.  His tables cannot arbitrate the choice, and the two
arms differ from EACH OTHER by at most 0.085 C while both differ from HIM by
~0.24 C, so the disagreement with the paper is three times larger and is not
explained by this.

Bai states no method -- only "d is the distance between the cell central node
and the wall" -- so the choice is unstated in the source as well; that is
recorded as a caveat rather than resolved.

The physical argument for the geodesic reading is untouched by any of this: a
cell in an obstacle's shadow really is far from a wall in the sense that
governs eddy size, and the Euclidean search really does understate it there.
It is simply not worth 0.085 C on THIS geometry.  On one with deeper shadows
it could be, and closing it would need a wave solve here, since OpenFOAM
v2412 ships no exact-distance wallDist either.
"""

import torch

from FVsolver.Turbulence.eddy_viscosity import EddyViscosityModel, _wall_face_spans
from FVsolver.Turbulence.smooth import sqrt_floored

_DEFAULTS = {
    # Bai Table 1 (kappa) and Eq. (4) (Clim).  Named as OpenFOAM would name
    # them in a coeffDict, and overridable through [turbulence] like every
    # other model's constants.
    "kappa": 0.419,
    "Clim": 0.09,
    # sqrt(S2 + eps^2): the same knob and the same reasoning as KOmegaSST's
    # -- a value perturbation far below fp64 at S2 = O(1) in exchange for a
    # finite slope at S2 = 0, which is the cold-start state.
    "s2SqrtEps": 1e-10,
}


class MLZE(EddyViscosityModel):
    """Bai Eq. (4).  No transported field, no wall function, no limiter."""

    DEFAULTS = _DEFAULTS

    # Nothing is transported, so nothing is appended to COLLOCATED_UNKNOWNS
    # and the host's transported-field loop runs zero times.
    TRANSPORTED: tuple = ()

    # No wall function of any kind, so no BC constrains an adjacent cell for
    # this model.  Left at the base value only because ``wall_cell_map`` needs
    # a name to look for; it will find no constrained BC and return None,
    # which is the correct answer here rather than a fallback.
    WALL_CONSTRAINT_VAR = "TURB_OMEGA"

    def __init__(self, params, solver):
        super().__init__(params, solver)
        # Mixing length: pure geometry, so it is cached on the same
        # bcpatch_batch identity key the wall distance uses and rebuilt only
        # when the case re-registers.
        self._length_cache = None
        # Wall-face mask for the exact nu_t = 0 at the wall; same key.
        self._wall_mask_cache = None

    # -- geometry ---------------------------------------------------------
    def mixing_length_cpd(self, solver) -> torch.Tensor:
        """``l = min(kappa y, Clim y_max)`` per cpd cell, ``[N_cpd, 1]``.

        Built under ``no_grad``: every input is mesh geometry, so this tensor
        is a constant of the run and must not appear in any graph.
        """
        key = getattr(solver, "bcpatch_batch", None)
        cache = self._length_cache
        if cache is not None and cache[0] is key:
            return cache[1]

        y_cpd, has_wall = self.wall_distance_cells(solver)
        with torch.no_grad():
            batch = solver.batch_cpd_cell.reshape(-1)
            n_cases = int(getattr(solver, "num_graphs", 1))
            # Per-case max over cells that actually have a wall.  A case with
            # NO wall patch keeps y_max = 0, hence l = 0 and nu_t = 0: a
            # mixing-length model has no length scale without a wall, and
            # reporting laminar there is the honest degenerate answer rather
            # than letting the _Y_NO_WALL sentinel become a cap.
            valid = has_wall.reshape(-1)
            y_max = torch.zeros(n_cases, dtype=y_cpd.dtype, device=y_cpd.device)
            if bool(valid.any()):
                y_max = y_max.scatter_reduce(
                    0, batch[valid], y_cpd.reshape(-1)[valid],
                    reduce="amax", include_self=True)
            cap = (self.Clim * y_max).index_select(0, batch)[:, None]
            length = torch.minimum(self.kappa * y_cpd, cap)
        self._length_cache = (key, length)
        return length

    def wall_face_mask(self, solver):
        """Cached ``[N_face, 1]`` bool: True on a wall-patch face, or None.

        Built from ``_wall_face_spans`` -- the same "any BC class named Wall"
        identification the rest of this package uses -- and cached on the
        bcpatch_batch identity like every other connectivity here.  ``None``
        when the case has no wall patch, so the caller returns its input
        tensor OBJECT unchanged and a wall-free case is structurally, not
        numerically, untouched.
        """
        key = getattr(solver, "bcpatch_batch", None)
        cache = self._wall_mask_cache
        if cache is not None and cache[0] is key:
            return cache[1]
        mask = None
        if key is not None:
            spans = _wall_face_spans(solver)
            if spans:
                n_face = int(solver.face_area.shape[-2])
                mask = torch.zeros(n_face, 1, dtype=torch.bool,
                                   device=solver.face_area.device)
                for start, end in spans:
                    mask[start:end] = True
        self._wall_mask_cache = (key, mask)
        return mask

    # -- closure ----------------------------------------------------------
    def strain_rate(self, grad_u: torch.Tensor) -> torch.Tensor:
        """``S = sqrt(2 Sij Sij)`` with ``Sij = symm(grad U)`` -- Bai Eq. (5).

        ``2 Sij Sij`` is the same invariant KOmegaSST calls ``S2``, and the
        Frobenius contraction below counts the three off-diagonals twice
        exactly as ``Sij Sij`` requires.  ``grad_u`` is ``[.., N, 3, 3]``.
        """
        sym = 0.5 * (grad_u + grad_u.transpose(-2, -1))
        s2 = 2.0 * (sym * sym).sum(dim=(-2, -1))[..., None]
        return sqrt_floored(s2, self.s2SqrtEps)

    def _nut_cell_from_state(self, solver, state) -> torch.Tensor:
        """``nu_t = l^2 S`` per cpd cell, run once per forward.

        Ghost rows take their owner's strain rate (``ghost_fill_owner``, the
        first-order patch closure every derived cpd field in this package
        uses) and their owner's mixing length, which is what
        ``nearest_wall_distance_cpd`` already fills.  The wall FACE value is
        not this interpolation -- see ``nut_wall_face``.
        """
        grad_u_cpd = state["grad_uvw_cpd_cell"][..., 0:3, :]
        s_cpd = solver.ghost_fill_owner(self.strain_rate(grad_u_cpd))
        length = self.mixing_length_cpd(solver)
        return length * length * s_cpd

    def nut_wall_face(self, solver, state, nut_face):
        """``nu_t = 0`` on every wall face -- the exact value of Eq. (4) there.

        At a wall, ``y = 0`` hence ``l = 0`` hence ``nu_t = 0``; the OpenFOAM
        transcription of this model gets that for free, because assigning
        ``sqr(l())*S`` to a ``volScalarField`` assigns its boundary field too
        and ``wallDist`` is zero on a wall patch.  Here the default is a
        LINEAR interpolation between owner and ghost, and
        ``nearest_wall_distance_cpd`` owner-fills the ghost, so without this
        override a wall face would carry ``l(y_owner)^2 S`` -- a finite
        turbulent viscosity at the wall, which would put a spurious turbulent
        stress into the momentum row through ``nu_eff = nu + nu_t``.

        This is not a wall FUNCTION: nothing is pinned and no log law is
        imposed.  It is the model's own algebraic value at ``y = 0``, and it
        is exact rather than modelled.
        """
        mask = self.wall_face_mask(solver)
        if mask is None:
            return nut_face
        # Static topology mask, so the where is autograd-clean: the wall rows
        # get an exact structural zero and every other row keeps its value
        # and its graph.
        return torch.where(mask, torch.zeros_like(nut_face), nut_face)

    # -- contract ---------------------------------------------------------
    def transport_closures(self, solver, state) -> dict:
        """Empty: the model transports nothing.

        Not a stub.  This is the structural content of a zero-equation model
        and the reason it is worth having -- the host's transported-field
        loop iterates over ``TRANSPORTED``, which is empty, so no scalar row
        is assembled, no diffusivity is built and no source is evaluated.
        """
        return {}

    def enforce_wall_bcs(self, solver, fvfield) -> dict:
        """Empty: the model owns no field, so it writes no ghost.

        The wall treatment MLZE does have is the exact ``nu_t = 0`` at wall
        faces in ``nut_wall_face``, which is a diffusivity, not a ghost.
        """
        return {}
