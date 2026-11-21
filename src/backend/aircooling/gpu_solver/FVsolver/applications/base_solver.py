"""
Base solver abstract class defining the common interface for all FV solvers.

This module provides the abstract base class that all concrete solver implementations
must inherit from, ensuring a consistent interface and enabling proper IDE type inference.
"""

from abc import ABC, abstractmethod
import contextlib
import math
import os
from typing import Tuple, Optional, Any, Dict

import logging
logger = logging.getLogger(__name__)

import torch
from FVsolver.FVdiscretization.ddt_schemes import parse_ddt_scheme
from FVsolver.FVdiscretization.FVflux import FV_flux
from FVsolver.FVdiscretization.FVgrad import (
    gradient_reconstruction,
    compute_normal_matrix,
)
from FVdomain.SetBC.onthefly.BCbase import VAR_TYPE_BY_FIELD
from Utils.utilities import FaceType

#: The two algebraic forms a ``constrains_adjacent_cell`` BC may ask for.
#:
#: ``"difference"``  ``phi_c - phi_wall``.  v2412's own row (``setValues``
#:                   leaves ``psi[celli] = value``), and the default.
#: ``"relative"``    ``phi_c/phi_wall - 1``.  The same root, divided by the
#:                   wall value, so the row is dimensionless.  It exists
#:                   because the difference form's row is NOT comparable with
#:                   the volume-integrated transport rows it sits beside:
#:                   measured on the TMR flat-plate wall-function
#:                   rung at OpenFOAM's own converged field, the epsilon
#:                   block's constraint rows carry median Jacobian row-norm
#:                   1.21e+04 against the transport rows' 4.40e-02 -- a
#:                   2.75e+05 split INSIDE one block, which the per-block
#:                   ``--row-scaling`` cannot see by construction.  Dividing
#:                   by ``phi_wall`` puts the dominant entry at
#:                   ``d/d(k_c) = -1.5/k_c`` instead of
#:                   ``-1.5 Cmu^0.75 sqrt(k_c)/(kappa y)``, i.e. it removes
#:                   the ``1/y`` that makes the row grow as the mesh
#:                   approaches the wall.
#:
#: Only ``setEpsilon.Epsilonwallfunction`` reads a toml entry for this;
#: ``setOmega.Wall`` has no reason to (omega is stored as ``ln omega``, which
#: already puts its constraint row at O(1)).
_CELL_CONSTRAINT_FORMS = ("difference", "relative")

#: How a time step treats the convection term (``BaseSolver.set_convection_treatment``).
#:
#: ``"implicit"``    ``N(u)`` at the new level on the state's own advecting flux --
#:                   the default, and BDF-N.
#: ``"explicit"``    no convection in the residual at all; the step supplies it as a
#:                   constant (``set_explicit_source``), e.g. ``2 N(u^n) - N(u^{n-1})``
#:                   for SBDF2.
#: ``"linearised"``  ``C(F*) u``: the new-level field carried by a FIXED advecting
#:                   flux ``F*`` (Oseen, ``F* = 2 F^n - F^{n-1}``), kept separate from
#:                   the new MWI flux continuity is written on.
CONVECTION_TREATMENTS = ("implicit", "explicit", "linearised")

#: What ``momentum_diagonal`` returns (``BaseSolver.set_ap_mode``); see its docstring.
AP_MODES = ("default", "sbdf", "oseen")


@contextlib.contextmanager
def _eager_forward():
    """``torch.compile`` stood down for one forward, as ``casestate._eager`` does.

    ``gradient_reconstruction`` carries ``@torch.compile``; the evaluation below
    is a one-off forward of the Newton path, which runs eager throughout.
    """
    stance = getattr(getattr(torch, "compiler", None), "set_stance", None)
    if stance is None:
        yield
        return
    with stance("force_eager"):
        yield


class BaseSolver(FV_flux, ABC):
    """
    Abstract base class for finite volume solvers.

    All concrete solver implementations (pico, buoyantBoussinesqpico, etc.)
    must inherit from this class and implement all abstract methods.

    This design ensures:
    1. IDE can properly infer types and provide autocomplete
    2. All solvers implement the same interface
    3. Code navigation (e.g., "Go to Definition") works correctly
    """

    # Concrete solvers override this with a tuple of per-component names,
    # one per slot returned by ``get_separated_losses()``. A ``None`` entry
    # means "do not log this slot" (used for duplicated/summary tensors).
    # If a subclass forgets to set this, ``iter_named_residuals`` below will
    # fall back to generic "loss_i" labels.
    LOSS_COMPONENT_NAMES: Tuple[Optional[str], ...] = ()

    # The square collocated system this solver defines, ONE row block and ONE
    # column block per entry, in the order both appear in the flat unknown:
    #
    #     (field, width, BC-enforced cpd attribute, residual attribute)
    #
    # ``field`` is the name the case's own ``bc/`` directory and ``VAR_CONFIG``
    # use, so an exporter can lay the solved state back out in the case's channel
    # order without being told what the case is.  ``width`` sums to the number of
    # unknowns per cell -- 4 for isothermal momentum + continuity, 5 once an
    # energy row joins them -- which is why ``collocated_newton`` reads this instead
    # of carrying a constant.  Empty means the solver has no square form and the
    # collocated driver refuses it by name rather than by AttributeError.
    COLLOCATED_UNKNOWNS: Tuple[Tuple[str, int, str, str], ...] = ()

    def __init__(self) -> None:
        """Initialize the base solver."""
        super(BaseSolver, self).__init__()
        self.params: Any = None
        self.fvfield: Dict[str, torch.Tensor] = {}
        self._smoothness_metrics: Optional[Dict[str, float]] = None

    @staticmethod
    def _parse_monitor_smoothness(fvsolution: dict) -> bool:
        return bool(fvsolution.get("monitor_smoothness", True))

    @staticmethod
    def compute_smoothness_metrics(
        phi_cpd_cell: torch.Tensor,
        face_edge_index: torch.Tensor,
        mask_interior: torch.Tensor,
        var_names: list,
    ) -> Dict[str, float]:
        try:
            from Diagnostics.field_smoothness import all_metrics
        except ImportError:  # optional diagnostics module; metrics are skipped without it
            return {}
        return all_metrics(phi_cpd_cell, face_edge_index, mask_interior, var_names)

    # OpenFOAM-style trailing gradient token accepted inside scheme entries,
    # mirroring OF's habit of naming the gradient in the scheme line
    # ("div(phi,U) Gauss linearUpwind grad(U);" — grad(U) resolved through
    # gradSchemes, where every OF gradScheme is a COMPACT 1-hop stencil).
    # Supported spellings:
    #   divSchemes   = {U = "LinearUpwind leastSquares"}
    #   snGradSchemes = "corrected leastSquares"
    # The token is accepted for config compatibility but no
    # longer triggers a second gradient computation: solvers compute ONE
    # unified WLSQ gradient per forward, and under the default [stencil]
    # khops=1 that gradient is itself the compact 1-hop reconstruction the
    # token requests. The token must still be parsed so the scheme
    # dispatch sees the bare scheme name ("LinearUpwind", "corrected").
    _GRAD_TOKENS: Tuple[str, ...] = ("leastSquares",)

    @staticmethod
    def _split_grad_token(scheme) -> Tuple[str, Optional[str]]:
        """Split an OpenFOAM-style trailing gradient token off a scheme entry.

        "LinearUpwind leastSquares"   -> ("LinearUpwind", "leastSquares")
        "LimitedLinear 1 leastSquares"-> ("LimitedLinear 1", "leastSquares")
        "corrected leastSquares"      -> ("corrected", "leastSquares")
        "LinearUpwind" / "LimitedLinear 1" -> (unchanged, None)
        """
        parts = str(scheme).split()
        if len(parts) >= 2 and parts[-1] in BaseSolver._GRAD_TOKENS:
            return " ".join(parts[:-1]), parts[-1]
        return str(scheme), None

    def _build_recon_compact_moments(self):
        """Precompute compact (1-hop face-adjacency) WLSQ moments.

        RETAINED FOR PROBES ONLY: production solver forwards
        compute a single unified WLSQ gradient and do not call this —
        the MMS parity probe (experiments/MMS_SchemeAccuracy/MMS/convection/
        linearUpwind_prod/run_prod_parity.py) still borrows it.

        Original purpose: compact moments for the "leastSquares" gradient
        token (divSchemes upwind reconstruction and/or snGradSchemes
        corrected non-orthogonal correction).

        Motivation (MMS-verified, experiments/MMS_SchemeAccuracy,
        case convection/linearUpwind_compactgrad): LinearUpwind reconstructed
        with a wide (khops>=2) EXTENDED WLSQ gradient carries ~1.6-1.9x
        the numerical dissipation of OpenFOAM's linearUpwind at marginally
        resolved wavenumbers (the wide stencil low-passes the gradient, so the
        upwind correction under-corrects). With the compact 1-hop gradient the
        dissipation/dispersion curves match OpenFOAM to ~1e-13 at every k*dx.

        All tensors are mesh-only (no dependence on phi/theta). Unlike the
        cheap _build_facecv_geom, assembling the normal matrices scatters over
        every face, so the build is keyed on the register_geometrics mesh
        signature and reruns only when the registered geometry changes:
          - _recon_compact_edge_index: [2, E] face-adjacency cpd edges,
            CYCLIC/EMPTY faces excluded (cyclic ghost gradients are filled by
            the enforce_cyclic gradient pass; EMPTY faces never contribute)
          - _recon_compact_A / _recon_compact_B: WLSQ normal matrices on that
            compact stencil (same compute_normal_matrix as the extended ones)
        """
        sig = getattr(self, "_geom_mesh_sig", None)
        if (
            getattr(self, "_recon_compact_sig", None) == sig
            and getattr(self, "_recon_compact_A", None) is not None
        ):
            return
        mask_edge = (self.face_type != FaceType.CYCLIC) & \
                    (self.face_type != FaceType.EMPTY)
        edge_index = self.neighbor_cpd_cell[:, mask_edge]
        # gradSchemes drives the moment order; Load_mesh maps "GG" -> "1st"
        # for the extended moments, mirror that here.
        order = self.params.fvschemes["gradSchemes"]
        if order == "GG":
            order = "1st"
        (A_compact, two_way_B_compact) = compute_normal_matrix(
            order=order,
            node_pos=self.cpd_cell_pos,
            edge_index=edge_index,
        )
        self._recon_compact_edge_index = edge_index
        self._recon_compact_A = A_compact
        self._recon_compact_B = torch.chunk(two_way_B_compact, 2, dim=0)[0]
        self._recon_compact_sig = sig

    def _compute_recon_gradient(
        self,
        phi_hat_cpd_cell: torch.Tensor,
        cell_limited: bool = False,
        limiter_type: str = "minmod",
        limiter_k: float = 1.0,
        limiter_rt: float = 1.5,
    ) -> torch.Tensor:
        """Compact-stencil WLSQ gradient (PROBE-ONLY — see
        _build_recon_compact_moments; solver forwards use one unified
        gradient).

        Autograd follows the phi tensor the caller passes: ext solvers pass the
        attached field (gradients flow to the NN), cmpt solvers pass phi.data
        (OpenFOAM deferred-correction semantics).

        Ghost cpd cells are EXCLUDED from the valid mask: their single-edge
        compact stencil is rank-deficient, so their gradient returns zero.
        That is safe for the reconstruction use-case — boundary faces take the
        Dirichlet ghost VALUE directly in div_phic_to_faces, and cyclic ghost
        gradients must be refilled by the caller's enforce_cyclic gradient
        pass (same as the extended gradient).

        Args:
            phi_hat_cpd_cell: [N_cpd_cells, C] stacked fields (same layout the
                caller feeds gradient_reconstruction with).
            cell_limited/limiter_*: same gradLimiter options as the main
                gradient call — the limiter targets exactly this
                reconstruction gradient.

        Returns:
            [N_cpd_cells, C, 3] compact reconstruction gradients.
        """
        order = self.params.fvschemes["gradSchemes"]
        if order == "GG":
            order = "1st"
        return gradient_reconstruction(
            order=order,
            phi_node=phi_hat_cpd_cell,
            edge_index=self._recon_compact_edge_index,
            mask_valid_node=self.mask_interior_cell,
            node_pos=self.cpd_cell_pos,
            precompute_Moments=[self._recon_compact_A, self._recon_compact_B],
            dim=self.dim,
            cell_limited=cell_limited,
            limiter_type=limiter_type,
            limiter_k=limiter_k,
            limiter_rt=limiter_rt,
            neighbor_cell=self.neighbor_cpd_cell,
            face_pos=self.face_pos,
            mask_interior_face=self.mask_interior_face,
            mask_interior_cell=self.mask_interior_cell,
        )[..., 0:3]

    def _green_gauss_cpd_gradient(
        self, phi_hat_cpd_cell: torch.Tensor
    ) -> torch.Tensor:
        """Green-Gauss gradient, returned in the cpd-cell layout WLSQ uses.

        ``green_gauss_gradient`` returns interior-cell rows only, while every
        solver forward consumes a ``[N_cpd, C, 3]`` tensor whose ghost rows are
        zero -- which is also what the WLSQ path produces, since
        ``mask_valid_node = mask_interior_cell`` zeroes them there.  Scattering
        back here keeps that one convention instead of teaching each caller two.

        This exists for the MMS control arm.  Syrakos et al. (2017), Table 5,
        measure the divergence-theorem gradient at order 0 on arbitrary meshes
        against least squares at order 1, and report that the finite-volume
        solver inherits the gradient's order -- so ``gradSchemes = "GG"`` is the
        arm that is SUPPOSED to fail, and it has to be reachable for the failure
        to be a measurement rather than an assumption.
        """
        if phi_hat_cpd_cell.dim() != 2:
            raise ValueError(
                f'gradSchemes = "GG" takes a [N_cpd, C] field; got shape '
                f"{tuple(phi_hat_cpd_cell.shape)}.  The batched time-window "
                "layout is not wired for it, and the control arm this exists "
                "for is a single steady case")
        from FVsolver.FVdiscretization.FVgrad import green_gauss_gradient

        grad_interior = green_gauss_gradient(
            phi_cpd_cell=phi_hat_cpd_cell,
            neighbor_cpd_cell=self.neighbor_cpd_cell,
            cell_volume=self.cell_volume,
            cells_face_surface_vec=self.cells_face_surface_vec,
            cells_face=self.cells_face,
            cells_face_ptr=self.cells_face_ptr,
            mask_interior_cell=self.mask_interior_cell,
            mask_boundary_face=self.mask_boundary_face,
            gC=self.gC, gF=self.gF, Cf=self.Cf, Ff=self.Ff,
            # No skewness sweeps, for two reasons that agree.  This is the
            # "common variant" Syrakos et al. put at order 0 on arbitrary meshes,
            # so it is the variant the control arm is supposed to run; and each
            # sweep widens the residual's stencil past the 2-hop ball the
            # collocated Jacobian's colouring assumes, which showed up as an
            # assembled J reproducing the matrix-free product to only 6.9e-2
            # relative against 3.2e-5 for WLSQ (perturbed 8^3).
            corrections=0,
        )
        return torch.zeros(
            (phi_hat_cpd_cell.shape[0], phi_hat_cpd_cell.shape[1], 3),
            device=grad_interior.device, dtype=grad_interior.dtype,
        ).index_copy(0, self._interior_cell_indices, grad_interior)

    def ghost_fill_owner(self, phi_cpd_cell: torch.Tensor) -> torch.Tensor:
        """Copy owner-cell values into non-cyclic ghost rows (incl. empty).

        Boundary closure for DERIVED-field gradient operands (turbulence
        design phase 3b): a derived cell field built from the unified WLSQ
        gradient carries ZEROS at physical ghost rows (interior-only
        reconstruction), and feeding those zeros into a
        second gradient pass would corrupt the first interior layer.  The
        owner copy is the same first-order patch extrapolation the dev2
        boundary substitution uses (picoext conserved_form); cyclic ghosts
        are NOT touched -- enforce_cyclic already refilled them with the
        correct (rotation-aware) values.

        Out-of-place (``index_copy``), autograd-clean; ``[.., N_cpd, C]``.
        """
        ids = getattr(self, "_ghost_fill_owner_ids", None)
        if ids is None:
            faces = self.mask_boundary_non_cyclic_face.view(-1).nonzero(
                as_tuple=True)[0]
            # One ghost cpd cell per boundary face: indices are unique.
            ids = (self.neighbor_cpd_cell[0, faces],
                   self.neighbor_cpd_cell[1, faces])
            self._ghost_fill_owner_ids = ids
        owner, ghost = ids
        return phi_cpd_cell.index_copy(
            -2, ghost, phi_cpd_cell.index_select(-2, owner))

    # ------------------------------------------------- momentum interpolation
    # Rhie-Chow lives HERE rather than in one application because it is the one
    # thing a SQUARE collocated Newton system cannot do without, and there is
    # now more than one square system: the isothermal one and the Boussinesq
    # one.  A second copy under another file name would be the parallel
    # implementation CLAUDE.md bans, and the copy would be the one that drifts
    # -- a sign error (momentum_weighted_flux) is exactly
    # the kind of thing that gets fixed in one copy and not the other.
    # ------------------------------------------------------- the square seam
    # Three members a square Newton paradigm needs to READ or SET on whichever
    # discretisation a case selected.  They exist as public API because
    # collocated_newton was reaching for the private attributes directly -- reading
    # ``_momentum_residual_face`` and WRITING ``_momentum_interpolation_mwi`` and
    # ``_mwi_frozen_coefficient`` -- and moving that paradigm into its own
    # package (src/Pipeline/CoupledNewton/) would otherwise have frozen private coupling
    # into a package boundary.

    @property
    def momentum_residual_has_face_rows(self) -> bool:
        """True when ``[fvSchemes] momentumResidual`` adds face-control-volume rows.

        Those rows make the system taller than it is wide on purpose, which is
        what the ODIL/GNN objective wants and what a square Newton solve cannot
        have.  Defaults False for a discretisation that does not parse the key at
        all, which is the honest answer for one that writes no face rows.
        """
        return bool(getattr(self, "_momentum_residual_face", False))

    @property
    def momentum_interpolation_is_mwi(self) -> bool:
        """True when the face flux carries the momentum-weighted correction."""
        return bool(getattr(self, "_momentum_interpolation_mwi", False))

    def configure_momentum_interpolation(self, scheme=None, coefficient=None) -> None:
        """Override what ``_parse_momentum_interpolation`` read from fvSchemes.

        ``scheme`` is truthy for MWI, ``coefficient`` is ``"frozen"`` or
        ``"implicit"``; ``None`` leaves that half as the config set it.  A
        paradigm that offers these as CLI flags calls this instead of assigning
        the two private attributes.
        """
        if scheme is not None:
            self._momentum_interpolation_mwi = bool(scheme)
        if coefficient is not None:
            self._mwi_frozen_coefficient = coefficient == "frozen"

    def forward_mode_derivatives(self):
        """Context manager: ``forward`` in ops forward-mode AD can differentiate, same values.

        For the coupled Newton's JFNK operator (``Pipeline.CoupledNewton.jacobian.linearise_forward``),
        which evaluates ``forward`` on dual numbers.  The one op on this discretisation's path
        without a forward-mode formula is the WLSQ gradient's custom ``autograd.Function``
        (``FVgrad._WlsqSolve``); ``FVgrad.forward_mode_wlsq`` makes the gradient take the same product
        in plain ops while the context is open.  A seam method rather than an import so that the
        Newton package keeps importing nothing from ``FVsolver``; outside the context nothing
        changes, so every other caller runs exactly what it ran before.
        """
        from FVsolver.FVdiscretization.FVgrad import forward_mode_wlsq
        return forward_mode_wlsq()

    def _parse_convection_time_weight(self, tag: str) -> None:
        """Set ``self._conv_theta``: the weight the NEW time level gets in convection.

        ``ddtSchemes`` selects how the CONVECTION flux is sampled and nothing
        else.  Diffusion, pressure and continuity stay at the new time level
        whatever it says, and that asymmetry is the DESIGN for the GNN+FVM
        objective rather than a defect: those are elliptic STIFF terms, so
        averaging them over old and new would put half of a stiff operator on
        old data the optimiser does not control.  Only convection may be
        trapezoidal.

            theta = 1 - oc/2      oc=1 ("imex")            -> 0.5, trapezoidal
                                  oc=0 ("Euler"/"implicit") -> 1.0, new time only

        A past edit deleted the branch and named a
        replacement -- a caller-supplied effective old state -- that was never
        wired into the PCNO path, and the ValueError parse_ddt_scheme raises on
        the legacy spellings was swallowed by a bare except, so eight shipped
        Cylinder configs ran first-order time with nothing in any log to say so.
        Measured on that case: ||r_imex - r_Euler|| / ||r_Euler|| = 1.068e-01.
        """
        try:
            scheme = parse_ddt_scheme(self.params.fvschemes["ddtSchemes"])
            self._conv_theta = 1.0 - 0.5 * scheme.off_centering
        except (KeyError, ValueError, TypeError):
            self._conv_theta = 1.0
        if self._conv_theta != 1.0:
            print(f"[{tag}] convection time weighting theta={self._conv_theta:g} "
                  f"(new) / {1.0 - self._conv_theta:g} (old) from "
                  f"ddtSchemes={self.params.fvschemes.get('ddtSchemes')!r}")

    def blend_convection_in_time(self, new_flux, old_flux,
                                 new_source=None, old_source=None):
        """``theta * new + (1 - theta) * old`` for a convection flux and its source.

        Momentum and energy must both go through this: div(u T) and div(u u)
        share the advecting flux, so weighting one and not the other makes the
        scheme non-conservative.
        """
        th = self._conv_theta
        if th == 1.0:
            return new_flux, new_source
        flux = th * new_flux + (1.0 - th) * old_flux
        source = new_source
        if new_source is not None and old_source is not None:
            source = th * new_source + (1.0 - th) * old_source
        return flux, source

    def _parse_momentum_interpolation(self, tag: str) -> None:
        """Read ``momentumInterpolation`` and its coefficient flavour.

        DEFAULT OFF, and off is a real choice rather than an absence: a case that
        runs ``momentumResidual = "cell+face_cv"`` already has rows that see the
        grid-parity pressure mode, so it needs no second suppressor.  A SQUARE
        Newton system has no face rows, hence nothing that sees that mode, and
        this is the only parameter-free thing that supplies the coupling
        (survey Sec. 3.1).

        ANY paradigm may turn it on.  The two knobs are independent and compose:
        ``"cell+face_cv"`` with ``"mwi"`` loads with both face rows and the
        correction active, and nothing here guards against it.  What refuses face
        rows is ``CoupledNewton.refuse_the_overdetermining_face_rows``, a
        constraint of the SQUARE SOLVE and not of this discretisation.  Until
        recently this docstring said CLAUDE.md banned Rhie-Chow for the loss
        paradigms; the user lifted that, MWI being a Rhie-Chow interpolation
        that the square path already depended on.  The combination that is still
        wrong is dropping a momentum-residual term and supplying nothing in its
        place.

        ``test_picoext_mwi_default.py`` pins the off-by-default flux
        bit-identical to the pre-MWI code.
        """
        scheme = str(self.params.fvschemes.get(
            "momentumInterpolation", "none")).lower()
        if scheme not in ("none", "mwi"):
            raise ValueError(
                f'[{tag}] momentumInterpolation="{scheme}" is not a scheme this '
                'solver has; "none" (default) is the linearly interpolated face '
                'flux and "mwi" adds the momentum-weighted correction')
        self._momentum_interpolation_mwi = scheme == "mwi"
        # Whether d_f itself is differentiated.  Every pressure-based code in the
        # literature FREEZES it, for one reason: assembling those couplings into a
        # sparse matrix by hand is expensive.  That objection does not apply to a
        # matrix-free torch/JVP solver, and no source measures what the freezing
        # costs, so both arms exist and the default is the one that measured
        # better here (collocated_newton, MOMENTUM_INTERPOLATION_COEFFICIENT).
        #
        # The freezing is a SOLVER choice and not a discretisation one, because
        # it moves no residual value.  A SENSITIVITY is the one consumer that
        # cannot take it: measured, a frozen d_f gives dL/dnu the
        # wrong sign and 209% error on the 8^3 cavity, so
        # CoupledNewton.newton.differentiable_solution overrides this to
        # "implicit" for the duration of an adjoint whatever the case declares.
        flavour = str(self.params.fvschemes.get(
            "momentumInterpolationCoefficient", "implicit")).lower()
        if flavour not in ("implicit", "frozen"):
            raise ValueError(
                f'[{tag}] momentumInterpolationCoefficient="{flavour}" is neither '
                '"implicit" (d_f differentiated) nor "frozen" (d_f detached)')
        self._mwi_frozen_coefficient = flavour == "frozen"
        # RANS (docs/research/2026-10-02-rans-r0-analysis.md): the correction is OFF on every
        # non-cyclic boundary face, so continuity in the cells next to a FIXED-PRESSURE outlet does not see the
        # outlet pressure (99.7% of J e_p lands in momentum there), which leaves a dense pressure near-null
        # cluster on the flat plate.  "true" applies OpenFOAM's boundary form on first-type pressure faces:
        # u_f = u_P - d_P (snGrad p - grad p_P . n), snGrad p = (p_b - p_P)/delta.  Default false (all recorded runs).
        self._mwi_dirichlet_pressure = bool(self.params.fvschemes.get(
            "momentumInterpolationDirichletPressure", False)) or os.environ.get("LPCNS_MWI_DIRICHLET_P", "0") == "1"
        # [fvSchemes] limiterSmoothing = [eps, sharpness]: the C-infinity LimitedLinear for SCALAR fields (see
        # limited_linear.py).  Absent (default): the OpenFOAM limiter, every recorded run.
        sm = self.params.fvschemes.get("limiterSmoothing", None)
        self.limiter_smoothing = None if sm is None else (float(sm[0]), float(sm[1]))
        if self.limiter_smoothing is not None:
            print(f"[{tag}] limiterSmoothing ACTIVE — LimitedLinear (scalar) with eps={sm[0]}, sharpness={sm[1]}")
        if self._momentum_interpolation_mwi:
            print(f"[{tag}] momentumInterpolation=MWI ACTIVE — face flux carries "
                  "the momentum-weighted (Rhie-Chow) pressure correction"
                  + (" (also on first-type pressure boundary faces)" if self._mwi_dirichlet_pressure else ""))

    def _mwi_face_mask(self) -> torch.Tensor:
        """Boolean ``[N_face, 1]``: faces the momentum-weighted correction acts on.

        Every face except a physical (non-cyclic) boundary and an empty one.  A
        wall/inlet/outlet face carries a flux the boundary condition prescribes and
        must not be perturbed; a cyclic face is a genuine cell-to-cell coupling
        whose ghost values ``enforce_cyclic`` has already filled, so the compact
        difference across it is as valid as across any internal face.
        """
        mask = ~self.mask_boundary_non_cyclic_face
        if getattr(self, "mask_empty_face", None) is not None:
            mask = mask & (~self.mask_empty_face)
        return mask.view(-1, 1)

    def _fill_cyclic_ghosts(self, x_cpd):
        """Copy every cyclic ghost row (axis -2) from its periodic partner.

        One-sided face-CV rows on a cyclic face were wrong: the
        ghost carried no residual density and no volume, so the seam row tested
        the owner alone with V_C.  A cyclic face is an interior face whose
        neighbour happens to live across the seam; after this fill the same
        0.5*(x_C + x_F) average the interior faces use is exact there too.
        Same pairing as face_pressure_coefficient's d_cpd fill.
        """
        ghost, partner = self._cyclic_ghost_partner_ids()
        if ghost.numel() == 0:
            return x_cpd
        return x_cpd.index_copy(-2, ghost, x_cpd.index_select(-2, partner))

    def _cyclic_ghost_partner_ids(self):
        """``(ghost, partner)`` cpd indices for every cyclic face of the mesh.

        ``ghost`` is the boundary cpd cell a cyclic face carries, ``partner`` the
        INTERIOR owner on the other side of the periodic pair -- i.e. exactly the
        (destination, source) pairing ``enforce_cyclic`` uses to refill cyclic
        ghost field values and gradients.  It is read through
        ``get_cyclic_patch_pair_indices``, the same function every cyclic
        boundary condition calls, so a cell-quantity fill built on it cannot
        disagree with ``enforce_cyclic`` about who is paired with whom; the
        pairing was additionally cross-checked geometrically (partner centres sit
        at a constant translation from the owner's -- hills streamwise
        (+0.24874, 0, 0) spread 1.1e-04, Cylinder (0, 0, -0.75) spread 0.0,
        measured).

        Mesh-only, so it is cached on the same ``register_geometrics`` signature
        ``_build_recon_compact_moments`` keys on.  Empty tensors on a mesh with no
        cyclic patch, which makes every caller a no-op there.
        """
        sig = getattr(self, "_geom_mesh_sig", None)
        cached = getattr(self, "_cyclic_ghost_partner", None)
        if (cached is not None
                and getattr(self, "_cyclic_ghost_partner_sig", None) == sig):
            return cached
        from FVdomain.SetBC.onthefly.setCyclic import (
            get_cyclic_patch_pair_indices)

        fvpatch = self.fvpatch_batch
        pairing = getattr(fvpatch, "neighbor_patch_idx", None)
        if pairing is None:
            pairing = getattr(fvpatch, "neighbour_patch_idx", None)
        ghosts, partners = [], []
        if pairing is not None:
            for patch in range(len(pairing)):
                if int(pairing[patch]) < 0:   # -1 marks a non-cyclic patch
                    continue
                _, source_boundary, target_owner, _ = (
                    get_cyclic_patch_pair_indices(
                        fvpatch, self.neighbor_cpd_cell, patch))
                ghosts.append(source_boundary.reshape(-1))
                partners.append(target_owner.reshape(-1))
        if ghosts:
            pair = (torch.cat(ghosts).long(), torch.cat(partners).long())
        else:
            empty = torch.zeros(0, dtype=torch.long,
                                device=self.cell_volume.device)
            pair = (empty, empty)
        self._cyclic_ghost_partner = pair
        self._cyclic_ghost_partner_sig = sig
        return pair

    def _cyclic_face_pairs(self):
        """``(face_a, face_b)`` global face ids of every cyclic face pair, each pair once.

        Face ``i`` of a cyclic patch is paired with face ``i`` of its neighbour patch -- the alignment
        ``get_cyclic_patch_pair_indices`` (and hence ``enforce_cyclic`` and ``_cyclic_ghost_partner_ids``)
        uses.  Mesh-only, cached on the geometry signature; empty on a mesh without cyclic patches.
        """
        sig = getattr(self, "_geom_mesh_sig", None)
        cached = getattr(self, "_cyclic_face_pair", None)
        if cached is not None and getattr(self, "_cyclic_face_pair_sig", None) == sig:
            return cached
        fvpatch = self.fvpatch_batch
        pairing = getattr(fvpatch, "neighbor_patch_idx", None)
        if pairing is None:
            pairing = getattr(fvpatch, "neighbour_patch_idx", None)
        device = self.cell_volume.device
        a_list, b_list = [], []
        if pairing is not None:
            for patch in range(len(pairing)):
                other = int(pairing[patch])
                if other <= patch:          # -1 = not cyclic; each pair once (from its lower patch index)
                    continue
                s0, s1 = int(fvpatch.start_idx_face[patch]), int(fvpatch.end_idx_face[patch])
                t0, t1 = int(fvpatch.start_idx_face[other]), int(fvpatch.end_idx_face[other])
                if s1 - s0 != t1 - t0:
                    raise ValueError(f"cyclic patches {patch} / {other} have {s1 - s0} / {t1 - t0} faces")
                a_list.append(torch.arange(s0, s1, device=device))
                b_list.append(torch.arange(t0, t1, device=device))
        if a_list:
            pair = (torch.cat(a_list), torch.cat(b_list))
        else:
            empty = torch.zeros(0, dtype=torch.long, device=device)
            pair = (empty, empty)
        self._cyclic_face_pair, self._cyclic_face_pair_sig = pair, sig
        return pair

    def conservative_cyclic_flux(self, flux_face: torch.Tensor) -> torch.Tensor:
        """The face-normal velocity ``flux_face`` [..., n_face, 1] with every cyclic pair made conservative.

        OPT-IN (``_conservative_cyclic_flux``, entry ``--conservative-cyclic-flux``).  The two
        boundary faces of one periodic face are evaluated from their own side (owner + ghost geometry, the
        momentum-weighted correction with the side's own compact and interpolated pressure gradients), so their
        outward volumetric fluxes need not cancel: measured on the 2-layer hills roots, the sum of all continuity
        rows -- the net mass flux through the periodic planes -- is 2.98e-9 (80x60x2), 3.8e-11 (160x120x2),
        1.3e-12 (320x240x2), with ``J^T 1_cont`` ~ 1e-9 relative, i.e. a component of the residual no Newton step
        can remove: the floor of every 80x60x2 solve (README, job F1).  Here the pair's two outward volumetric
        fluxes ``F_a = phi_a A_a``, ``F_b = phi_b A_b`` are replaced by ``+-(F_a - F_b) / 2`` -- one flux per
        periodic face, as OpenFOAM's cyclic computes it -- so the continuity rows sum to zero for every state.  The
        same advecting flux feeds the convection terms, so convection stays consistent with continuity.
        """
        a, b = self._cyclic_face_pairs()
        if a.numel() == 0:
            return flux_face
        area = self.face_area.reshape(-1, 1)
        area_a, area_b = area.index_select(0, a), area.index_select(0, b)
        half = 0.5 * (flux_face.index_select(-2, a) * area_a - flux_face.index_select(-2, b) * area_b)
        out = flux_face.index_copy(-2, a, half / area_a)
        return out.index_copy(-2, b, -half / area_b)

    def momentum_diagonal(self, uvw_flux_face: torch.Tensor) -> torch.Tensor:
        """``a_P``, the momentum equation's diagonal coefficient, per interior cell.

        This is not an approximation of OpenFOAM's ``UEqn.A()``; it is the same
        quantity, written as the local sum over the cell's faces that ``A()``
        computes::

            a_P = sum_f [ max(F_f, 0) + nu |S_f| / |d_f| ],   F_f = u_f . S_f outward

        i.e. the upwind convective out-flux plus the orthogonal viscous
        conductance.  Extracting a true diagonal from the assembled Jacobian would
        cost one matrix-vector product per unknown and is not on the table; a purely
        geometric substitute (``h^2/nu`` and relatives) would be a tuned constant
        under another name, which is what disqualified PSPG here.

        The face set is the one the residual itself scatters over
        (``mask_non_empty_cells_face``), so boundary faces contribute exactly as
        they do to the momentum row.  Units are volumetric flux, so ``V_P / a_P`` is
        a time and the correction below has the units of a velocity.

        ``nu_coeff`` is the momentum diffusivity in both solvers that use this;
        the ENERGY equation's ``alpha`` never enters, because what is being
        approximated is the momentum row's diagonal and nothing else.

        The diffusivity is the SAME per-forward ``_nu_eff_face`` the residual's
        ``diffusion_flux`` used (Turbulence design section 4: one
        source, so ``d_f`` and the preconditioner's pressure operator follow a
        variable nu_eff automatically).  ``None`` falls back to ``nu_coeff``
        for solvers without the hook; for Laminar the hook stores ``nu_coeff``
        itself, so this line computes exactly what it did before the hook.

        THE a_P MODE (``set_ap_mode``).
        ``"default"`` -- the attribute absent, every caller before that date -- is
        the formula above, operation for operation.  The two time-integrator modes
        make ``a_P``, hence ``d_f``, independent of the unknowns, which is what
        makes an IMEX step LINEAR and an Oseen step affine:

          ``"sbdf"``   ``a_P = unsteady_coeff V / dt_cell + sum_f nu |S_f| / |d_f|``:
                       no convective half (convection is explicit, so the row the
                       step solves has none) and the time term FORCED on, whatever
                       ``--ap-unsteady`` says.  OpenFOAM's ``UEqn.A()`` when the
                       convection is ``fvc::``.  Dropping the convective half
                       WITHOUT adding ``V/dt`` would leave the viscous conductance
                       alone and inflate ``d_f = V/a_P`` by the cell Peclet number.
                       ``dt_cell`` is the effective step, so the term is ``a0 V/dt``.
          ``"oseen"``  the formula above with ``max(F*, 0)`` read off the FIXED
                       advecting flux handed to ``set_ap_mode`` -- the argument
                       ``uvw_flux_face`` is ignored -- plus the time term exactly
                       when ``--ap-unsteady`` asks for it, as in the default mode.

        Both modes reach the preconditioner too: it calls
        ``face_pressure_coefficient(face_volumetric_flux)``, which lands here.
        """
        from torch_geometric.utils import scatter

        mode = getattr(self, "_ap_mode", "default")
        if mode == "oseen":
            uvw_flux_face = self._ap_flux
        nu_face = self._nu_eff_face if self._nu_eff_face is not None else self.nu_coeff
        viscous = (nu_face.index_select(-2, self.cells_face)
                   * self.directed_area_cells_face.abs()
                   * self.nonOrthDeltaCoeffs.index_select(-2, self.cells_face))
        if mode == "sbdf":
            per_face = viscous
        else:
            outward = (uvw_flux_face.index_select(-2, self.cells_face)
                       * self.directed_area_cells_face)
            per_face = outward.clamp_min(0.0) + viscous
        keep = self.mask_non_empty_cells_face
        a_P = scatter(
            per_face[..., keep, :],
            self.cells_face_ptr[keep], dim=-2, dim_size=self.num_cells,
            reduce='sum',
        )
        if mode == "sbdf" or getattr(self, "_ap_unsteady", False):
            # OPT-IN (transient entry ``--ap-unsteady``): the time
            # term's own diagonal, ``unsteady_coeff V / dt_cell``, joins a_P, so
            # d_f = V / (a_P + V/dt) is the coefficient of the momentum row the
            # implicit step actually solves -- the transient Rhie-Chow choice of
            # Choi (1999) and of OpenFOAM's ``rAU = 1/UEqn.A()`` with fvm::ddt in
            # UEqn.  It is OFF by default for two reasons, both properties of the
            # formula rather than of this code: (1) with it the stabilisation, and
            # hence the converged answer, depends on dt (d_f -> dt as dt -> 0, so
            # a small step weakens the pressure-velocity coupling, Yu et al. 2002,
            # Pascau 2011), and this solver has no Choi ddtCorr term to remove that
            # dependence; (2) without it d_f is the STEADY solver's coefficient, so
            # a time march that settles reaches exactly the steady entry's
            # discrete root.  ``dt_cell`` is the EFFECTIVE step the caller set
            # (2 dt / 3 under BDF2), i.e. the momentum diagonal's true time part.
            # Absent attribute or False: the return value is the tensor above,
            # bit for bit.  FORCED on in the "sbdf" a_P mode (see the docstring).
            a_P = a_P + self.unsteady_coeff * self.cell_volume / self.dt_cell
        return a_P

    def face_pressure_coefficient(self, uvw_flux_face: torch.Tensor) -> torch.Tensor:
        """``d_f = 1/2 (V_P/a_P + V_Q/a_Q)``, one per face, in seconds.

        The coefficient the pressure gradient is multiplied by wherever pressure and
        velocity are coupled -- inside the momentum-weighted flux below, and in the
        pressure equation a segregated code solves.  It is public because a
        preconditioner wants the SAME coefficient the residual used: building the
        pressure operator from a separately guessed one is how a preconditioner
        stops being spectrally equivalent to the block it stands in for.

        THE CYCLIC GHOST FILL IS LOAD-BEARING, and its absence was a residual bug
        (docs/research/2026-08-16-df-cyclic-halving.md).  ``momentum_diagonal``
        returns interior rows only, so without the second ``index_copy`` every
        ghost row of ``d_cpd`` stays zero and a cyclic face -- whose neighbour IS
        a ghost -- gets ``0.5 d_P`` instead of ``0.5 (d_P + d_Q)``.  The
        conservation argument, which needs no appeal to this docstring: the two
        half-faces of one periodic face feed the continuity rows of ``P`` and
        ``Q``, so their advecting fluxes must sum to zero, and ``a_P`` is a
        function of the STATE, so ``d_P != d_Q`` even where the geometry is
        symmetric.  The unfilled code therefore handed the two sides of one
        physical face different coefficients.  Measured on the periodic hills at
        a +-1e-1 state, ``||phi_src + phi_tgt|| / ||phi_src||`` on the spanwise
        pair: 1.68e-03 unfilled, 1.85e-06 filled (906x).  It moves the ANSWER --
        the hills root shifts 1.18e-05 relative L2 against a 7.89e-10
        two-shipped-runs path floor -- and is exactly zero on a 2D extrusion,
        where the cyclic faces are orthogonal and the bracket ``d_f`` multiplies
        vanishes whatever ``d_f`` is.

        NON-CYCLIC boundary faces keep the unfilled ``0.5 d_P`` deliberately, and
        the fill above cannot reach them: their ghost is a genuine boundary with
        no partner cell, and the value is UNREACHABLE rather than intended --
        ``_mwi_face_mask`` zeroes the correction on exactly those faces, and
        ``compact_pressure_laplacian`` drops them with its ``internal`` mask, so
        no consumer reads it.
        """
        d_cell = self.cell_volume / self.momentum_diagonal(uvw_flux_face)
        # Opt-in (DC-RANS S0-D part 3, deviation 1): the under-relaxed rAU of SIMPLE, d_f = alpha_U V / a_P, the face
        # coefficient a segregated code carries at its converged state.  None (the default) leaves d_f unchanged.
        _d_scale = getattr(self, "_mwi_d_scale", None)
        if _d_scale is not None:
            d_cell = d_cell * float(_d_scale)
        d_cpd = d_cell.new_zeros(
            (*d_cell.shape[:-2], self.num_cpd_cells, 1)
        ).index_copy(-2, self._facecv_interior_ids, d_cell)
        ghost, partner = self._cyclic_ghost_partner_ids()
        if ghost.numel():
            # Ghost rows and interior rows are disjoint (verified: 0
            # of the ghosts are interior, 0 of the partners are ghosts, on both
            # the Cylinder and the hills), so reading ``partner`` after the
            # interior fill is well defined and needs no second buffer.
            d_cpd = d_cpd.index_copy(-2, ghost, d_cpd.index_select(-2, partner))
        return 0.5 * (d_cpd.index_select(-2, self._facecv_C)
                      + d_cpd.index_select(-2, self._facecv_F))

    def _mwi_bracket_gradients(self, phi_cpd_cell, grad_phi_cpd_cell, var_type):
        """``(snGrad(phi)_f, (grad phi)_f . n_f)``, the two operands of the flux bracket of
        ``momentum_weighted_flux`` (the pressure's, and the buoyancy potential's in the Boussinesq application).

        The cell gradients are averaged with the linear weights unless the Warp backend was built with
        ``LPCNS_WARP_DC_MWI_GRADW=volume``: it then leaves ``_mwi_bracket_volume = (interior faces, owner weight)``
        on the application, and both operands take the cell-volume weights on those faces, as its kernels do
        (``DCFaceGeom.bgo``/``bow_p``/``bow_t``).  The torch forward is the residual the adjoint differentiates,
        so it has to be the residual the forward solve converged."""
        volume = getattr(self, "_mwi_bracket_volume", None)
        if volume is None:
            compact = self.compute_snGrad(
                phi_cpd_cell=phi_cpd_cell, grad_phi_cpd_cell=grad_phi_cpd_cell,
                scheme=self._snGrad_scheme, var_type=var_type,
            )
            grad_face = self.interpolating_gradients_to_faces(
                phi_cpd_cell=phi_cpd_cell, grad_phi_cpd_cell=grad_phi_cpd_cell,
                scheme="Linear")
            return compact, (grad_face * self.unv_face.unsqueeze(-2)).sum(dim=-1)
        inner, owner_w = volume
        inner = inner.view(-1, 1, 1)
        owner_w = owner_w.view(-1, 1, 1).to(self.gC.dtype)
        ow, nw, vectors = self._snGrad_operands(var_type)
        compact = self.compute_snGrad(
            phi_cpd_cell=phi_cpd_cell, grad_phi_cpd_cell=grad_phi_cpd_cell,
            scheme=self._snGrad_scheme, var_type=var_type,
            operands=(torch.where(inner, owner_w, ow), torch.where(inner, 1.0 - owner_w, nw), vectors),
        )
        grad_face = (grad_phi_cpd_cell.index_select(-3, self.C_senders) * torch.where(inner, owner_w, self.gC)
                     + grad_phi_cpd_cell.index_select(-3, self.F_receivers)
                     * torch.where(inner, 1.0 - owner_w, self.gF))
        return compact, (grad_face * self.unv_face.unsqueeze(-2)).sum(dim=-1)

    def momentum_weighted_flux(self, uvw_flux_face, p_cpd_cell, grad_p_cpd_cell,
                               var_type=None, body_force_face=None):
        """Bartholomew/Denner's consistent momentum-weighted face flux.

            d_f = 1/2 ( V_P / a_P + V_Q / a_Q )
            u_f = ubar_f . n_f  +  d_f [ (grad p)_f . n_f  -  snGrad(p)_f ]

        NOTE THE SIGN, which is fixed by two
        independent arguments.  Expand the bracket: the interpolated term ADDS BACK
        the pressure gradient already inside ``ubar_f`` (which interpolates cell
        velocities that carry it), and what is left acting on the pressure is
        ``- d_f snGrad(p)`` -- exactly the term a segregated code's flux carries,
        ``phi = phiHbyA - rAUf*snGrad(p)*magSf``.  On a checkerboard, where the
        interpolated cell gradient is zero, this reduces to precisely that.  And
        measured on the 16^3 cavity: with this sign the pressure Schur
        ``C - D F^-1 G`` is DEFINITE (4095 of 4095 one sign, condition number 326,
        against 201 for the uncorrected system); with the other sign it is
        indefinite, 2987 of 4095 the wrong way, condition number 1.635e4, and
        neither GMRES nor Newton converges on it.

        The bracket is an interpolated face gradient minus a COMPACT one, so it
        vanishes on any pressure field the two operators agree on and is a low-pass
        filter on the third pressure derivative otherwise -- which is exactly the
        term a collocated arrangement is missing.  Note what it is NOT: it does not
        vanish as the solve converges on a fixed mesh (Mangani 2016), so a residual
        driven to 1e-14 here certifies the STABILISED equations, not the bare ones.

        Both operands are the general-mesh forms already in this file.  The compact
        term is ``compute_snGrad`` under the case's own ``snGradSchemes``, which
        carries the non-orthogonal correction and degenerates to
        ``(p_Q - p_P)/|d_PQ|`` when the mesh is orthogonal; no separate
        orthogonal-only branch exists.

        The interpolated term asks for ``"Linear"`` EXPLICITLY rather than for the
        case's ``gradInterpolationSchemes``, and that is a correctness requirement
        rather than a style choice: this case family runs ``"Linear overRelaxed"``,
        which REPLACES the face-normal component of the interpolated gradient with
        the compact two-point difference for OpenFOAM-parity diffusion.  Passing
        that scheme in makes the bracket cancel identically and the whole
        correction silently vanish -- measured,
        ``||r_MWI - r_linear|| / ||r_linear||`` came out at exactly 0.0 before this
        was fixed.

        ``a_P`` is built from the UNCORRECTED linear flux, which keeps the whole
        expression closed-form (no inner fixed point) while leaving it an ordinary
        differentiable function of ``(u, p)``: autograd reaches the advecting
        velocity through it, which is the coupling Denner (2018) measured as worth
        up to 12x wall clock against a frozen one.

        ``var_type`` names WHICH pressure this is to the boundary-aware snGrad --
        ``PRESSURE`` for ``picoext``'s ``p``, ``PRESSURE_RGH`` for the Boussinesq
        ``p_rgh``.  It is a required argument in practice and defaults to
        ``PRESSURE`` only so the isothermal call site reads as it did before.

        ``body_force_face`` IS OPENFOAM'S ``phig``, and it is the whole reason
        this argument exists.  ``buoyantBoussinesqPimpleFoam/pEqn.H`` does not
        build its face flux from the pressure alone::

            phig     = -rAUf*ghf*fvc::snGrad(rhok)*mesh.magSf()
            phiHbyA  = fvc::flux(HbyA) + rAUf*ddtCorr + phig
            phi      = phiHbyA - p_rghEqn.flux()

        so the flux the continuity equation is written on carries the buoyancy
        body force through EXACTLY the operator, and with EXACTLY the coefficient
        (``rAUf``, this file's ``d_f``), that carries the pressure.  ``UEqn.H``
        pairs the same two terms again inside one ``fvc::reconstruct``.  Omitting
        it here left temperature able to reach the momentum rows and unable to
        reach the continuity row at all: measured by JVP at the converged heated
        state, ``dR_momentum/dT = 1.990011e-01`` against ``dR_continuity/dT =
        0.000000e+00 EXACTLY``.

        It enters INSIDE the bracket, not beside it, because the bracket is the
        one place a face-normal acceleration belongs: everything the momentum row
        writes as ``-grad(psi)`` for some potential ``psi`` must appear here as
        ``(grad psi)_f . n - snGrad(psi)_f``, and buoyancy is such a term with
        ``psi = gh rhok``.  A caller supplies the already-assembled scalar
        ``[..., N_face, 1]`` rather than a field, because WHICH potential it is
        is the caller's physics and not this function's: ``picoext`` has none and
        passes nothing, which leaves the isothermal flux bit-identical.  That is
        gated twice -- ``tests/test_momentum_weighted_body_force.py`` pins the
        contract, and ``experiments/LDC/CoupledNewton/hash_four_field.py``
        measured the whole 4-unknown residual AND the assembled Jacobian
        BIT-IDENTICAL on the orthogonal 16^3 cavity, and equal to the same-code
        nondeterminism floor (2.168e-19 residual, 8.674e-19 on the Jacobian
        values) on the 147,200-cell cylinder O-grid, where a byte hash is not a
        valid gate.
        """
        from FVdomain.SetBC.onthefly.BCbase import VARType

        if var_type is None:
            var_type = VARType.PRESSURE
        d_face = self.face_pressure_coefficient(uvw_flux_face)
        if self._mwi_frozen_coefficient:
            d_face = d_face.detach()

        compact, interpolated = BaseSolver._mwi_bracket_gradients(self, p_cpd_cell, grad_p_cpd_cell, var_type)
        bracket = interpolated - compact
        if body_force_face is not None:
            bracket = bracket + body_force_face
        correction = d_face * self.grad_p_coeff * bracket
        mask = self._mwi_face_mask()
        if getattr(self, "_mwi_dirichlet_pressure", False):
            # first-type pressure faces: the owner's gradient (the ghost carries none) and the owner's full d_P
            # (d_face there is 0.5 (d_P + 0) because the ghost's d is zero)
            dmask = self.dirichlet_boundary_face_mask(var_type).view(-1, 1)
            owner_grad = grad_p_cpd_cell.index_select(-3, self.C_senders)
            owner_n = (owner_grad * self.unv_face.unsqueeze(-2)).sum(dim=-1)
            bracket_b = owner_n - compact
            if body_force_face is not None:
                bracket_b = bracket_b + body_force_face
            correction = torch.where(dmask, 2.0 * d_face * self.grad_p_coeff * bracket_b, correction)
            mask = mask | dmask
        return uvw_flux_face + torch.where(mask, correction, torch.zeros_like(correction))

    # ------------------------------------------ IMEX / Oseen time-integrator hooks
    # Used by the transient runs (docs/research/2026-10-04-transient-
    # experiment-matrix.md, section 2.2: H1-H4 and H9).  They live HERE for the same
    # reason the momentum interpolation does: both square systems -- picoext and the
    # Boussinesq one -- need one contract, and the energy row follows the momentum
    # row's convection switch.  Every hook is INERT until called: the forward reads
    # each setting with ``getattr`` and its default, so a solver nobody configured
    # runs the operations it ran before, to the bit
    # (``FVsolver/applications/tests/test_imex_oseen_hooks.py`` measures that in
    # process and against the live tree).
    #
    # The contract the time stepper relies on, in one place:
    #
    #   set_convection_treatment(mode, advecting_flux=None)  CONVECTION_TREATMENTS
    #   set_explicit_source(src or None)    [N_cell, F], ADDED to the residual rows
    #   explicit_convection(state)          [N_cell, F], N(u) as it enters the rows
    #   mwi_face_flux(state)                [N_face, 1], the flux continuity reads
    #   set_ap_mode(mode, flux=None)        AP_MODES, see momentum_diagonal
    #   set_bc_old_state(state or None, dt=None)   what old-level BCs read (H9)
    #
    # ``F`` is the unknown count per cell in ``COLLOCATED_UNKNOWNS`` order (4 for
    # picoext, 5 for the Boussinesq solver) and ``state`` the compound-cell layout
    # ``forward`` consumes, i.e. ``casestate.state_with_boundaries``.

    # Readouts a forward leaves behind for whoever reads "the last evaluation"
    # (the preconditioner's flux, an exporter's BC-enforced fields, the Courant
    # rate).  ``_capture_convection`` puts them back, so asking for ``N(u^n)``
    # between a residual evaluation and its readers moves none of them.
    _EVALUATION_READOUTS = ("face_volumetric_flux", "source_term", "_nu_eff_face",
                            "_alpha_eff_face", "_dev2_active", "_checker_loss")
    _EVALUATION_READOUT_PREFIXES = ("residual_", "bc_", "_raw_res_", "loss_",
                                    "norm_factor_", "_norm_scale_")

    #: True on a discretisation whose ``forward`` reads the hooks below (``picoext``,
    #: ``buoyantBoussinesqpicoext``).  Every other solver inherits the setters, so a
    #: non-default setting on one of them is REFUSED rather than silently ignored;
    #: the defaults stay accepted everywhere, so a stepper can always disarm.
    TIME_INTEGRATOR_HOOKS = False

    def _refuse_unwired(self, what: str) -> None:
        if not getattr(type(self), "TIME_INTEGRATOR_HOOKS", False):
            raise ValueError(
                f"{type(self).__module__}.{type(self).__name__} does not read {what}: its "
                "forward has no time-integrator hooks (picoext and buoyantBoussinesqpicoext do)")

    def _collocated_width(self) -> int:
        """Unknowns per cell, read off ``COLLOCATED_UNKNOWNS`` (never restated)."""
        return sum(width for _, width, _, _ in self.COLLOCATED_UNKNOWNS)

    def _refuse_transported_rows(self, what: str) -> None:
        """Transported turbulence rows convect with the same flux; not wired here."""
        if getattr(self, "_turb_width", 0):
            raise ValueError(
                f"{what} is not wired for transported turbulence rows: they share the "
                "advecting flux and would stay implicit beside an explicit or "
                "linearised momentum row -- run a laminar case")

    def _checked_tensor(self, value, shape, what: str) -> torch.Tensor:
        """``value`` detached, after checking its shape, dtype, device and finiteness."""
        if not torch.is_tensor(value):
            raise ValueError(f"{what} needs a tensor of shape {shape}, got "
                             f"{type(value).__name__}")
        if tuple(value.shape) != tuple(shape):
            raise ValueError(f"{what}: shape {tuple(value.shape)}, this case needs "
                             f"{tuple(shape)}")
        reference = self.cell_volume
        if value.dtype != reference.dtype or value.device != reference.device:
            raise ValueError(f"{what}: {value.dtype} on {value.device}, the solver runs "
                             f"{reference.dtype} on {reference.device}")
        if not bool(torch.isfinite(value).all()):
            raise ValueError(f"{what}: not finite")
        return value.detach()

    def _checked_face_flux(self, flux, what: str) -> torch.Tensor:
        return self._checked_tensor(flux, (int(self.num_faces), 1), what)

    def set_convection_treatment(self, mode: str, advecting_flux=None) -> None:
        """Select how the residual carries convection (transient matrix H3).

        ``"implicit"`` is the shipped residual.  ``"explicit"`` skips the
        convective flux AND the bounded source ``-div(F) u`` -- both, because the
        bounded source does not read ``convection_coeff``, so zeroing that
        coefficient would leave half of the term behind -- and the step adds the
        convection back as a constant through ``set_explicit_source``.
        ``"linearised"`` evaluates the case's own convection scheme on the NEW
        field with ``advecting_flux`` (``[N_face, 1]``, the layout
        ``mwi_face_flux`` returns) instead of the state's flux, so the term is
        ``C(F*) u``: linear in the unknowns for every scheme whose face value is
        linear in the field once the flux sign is fixed (Linear, LinearUpwind;
        not a limited scheme).  Continuity keeps the NEW momentum-weighted flux
        either way: the advecting and the conserved fluxes are separate objects.

        A Boussinesq solver applies the same switch to the energy row, because
        ``div(u T)`` and ``div(u u)`` share the advecting flux; with convection
        explicit its T rows then carry no velocity, and the IMEX matrix is block
        triangular (spec 2.1).  The same holds LINEARISED: the T rows carry
        ``C(F*) T`` on the lagged flux and no new velocity either, so an Oseen
        step is block lower-triangular too (T, then ``(u, p)`` given T through
        buoyancy and ``phig``); two-way u-T coupling is the implicit treatment's
        alone, whatever spec 2.1 lists beside it.

        Refused for a case whose ``ddtSchemes`` blends convection over two levels
        (``_conv_theta != 1``): that blend reads the old level's flux and would
        sit on top of the treatment chosen here.
        """
        if mode not in CONVECTION_TREATMENTS:
            raise ValueError(f"unknown convection treatment {mode!r}; valid: "
                             f"{', '.join(CONVECTION_TREATMENTS)}")
        if mode != "linearised" and advecting_flux is not None:
            raise ValueError(f"convection treatment {mode!r} takes no advecting flux")
        if mode == "implicit":
            self._conv_mode, self._conv_advecting_flux = "implicit", None
            return
        self._refuse_unwired(f"convection treatment {mode!r}")
        theta = float(getattr(self, "_conv_theta", 1.0))
        if theta != 1.0:
            raise ValueError(
                f"convection treatment {mode!r} on a case whose ddtSchemes blends "
                f"convection over two levels (theta = {theta:g}); declare \"Euler\" or "
                "\"backward\"")
        self._refuse_transported_rows(f"convection treatment {mode!r}")
        flux = None
        if mode == "linearised":
            flux = self._checked_face_flux(
                advecting_flux, "set_convection_treatment('linearised')")
        self._conv_mode, self._conv_advecting_flux = mode, flux

    def _convection_advecting_flux(self, own_flux: torch.Tensor):
        """The flux this forward's convection is carried by; ``None``: no convection.

        ``own_flux`` is the state's own (BC-enforced, momentum-weighted) face
        flux, which the implicit treatment returns unchanged -- the same tensor,
        so the default residual is the default residual.
        """
        mode = getattr(self, "_conv_mode", "implicit")
        if mode == "implicit":
            return own_flux
        if mode == "explicit":
            return None
        return self._conv_advecting_flux

    def set_explicit_source(self, src) -> None:
        """A constant ``[N_cell, F]`` ADDED to every residual row it covers (H2).

        The residual becomes ``r(x) + src``: column ``j`` lands on the row block
        of the ``j``-th unknown in ``COLLOCATED_UNKNOWNS`` order -- momentum
        ``0:3``, continuity ``3``, energy ``4`` on the Boussinesq solver -- per
        interior cell, volume-integrated like every other term there.  It is the
        time step's KNOWN data (the "right-hand side" in that sense), but its sign
        is the residual's, not the equation's right-hand side's: for SBDF2 pass
        ``2 N(u^n) - N(u^{n-1})`` built from ``explicit_convection``, which
        returns ``N`` with the sign it has inside the implicit residual.  Then
        ``set_explicit_source(explicit_convection(s))`` under the explicit
        treatment reproduces the implicit residual at ``s`` -- the identity the
        tests pin.

        Detached on entry (a constant of the step, never differentiated).
        ``None`` removes it; a solver that never set one adds nothing.
        """
        if src is None:
            self._explicit_source = None
            return
        self._refuse_unwired("an explicit source")
        self._refuse_transported_rows("an explicit source")
        self._explicit_source = self._checked_tensor(
            src, (int(self.num_cells), self._collocated_width()), "set_explicit_source")

    def _explicit_source_rows(self, field: str):
        """The explicit source's columns for unknown ``field``, or ``None``."""
        src = getattr(self, "_explicit_source", None)
        if src is None:
            return None
        offset = 0
        for name, width, _, _ in self.COLLOCATED_UNKNOWNS:
            if name == field:
                return src[..., offset:offset + width]
            offset += width
        raise KeyError(f"{field!r} is not an unknown of {type(self).__name__}")

    def _convection_cell_rows(self, flux_face, bounded_source) -> torch.Tensor:
        """``sum_f flux_f S_f`` per interior cell plus the bounded source, detached.

        The same face set and cell scatter the residual assembles over
        (``mask_non_empty_cells_face``), so the rows are what the convection
        contributes to the momentum (or energy) row of each cell.
        """
        from torch_geometric.utils import scatter

        keep = self.mask_non_empty_cells_face
        rows = scatter(
            (flux_face.index_select(-2, self.cells_face)
             * self.directed_area_cells_face)[..., keep, :],
            self.cells_face_ptr[keep], dim=-2, dim_size=self.num_cells, reduce='sum')
        if bounded_source is not None:
            rows = rows + bounded_source.index_select(-2, self._interior_cell_indices)
        return rows.detach()

    def _evaluation_readouts(self) -> Dict[str, Any]:
        names = set(self._EVALUATION_READOUTS)
        for _, _, bc_attribute, residual_attribute in self.COLLOCATED_UNKNOWNS:
            names.update((bc_attribute, residual_attribute))
        return {name: value for name, value in vars(self).items()
                if name in names or name.startswith(self._EVALUATION_READOUT_PREFIXES)}

    def _capture_convection(self, state: torch.Tensor, advecting_flux=None) -> Dict[str, Any]:
        """One no-grad forward at ``state`` that records its convection and flux.

        ``state`` is a compound-cell state ``[N_cpd, F]`` (``state_with_boundaries``'
        layout); it is cloned, because the boundary passes write ghost rows in
        place.  Convection is evaluated IMPLICITLY on the state's own flux (or,
        with ``advecting_flux``, linearised on that flux) whatever treatment the
        step has armed, and everything else that defines the discrete operator
        stays as the step set it: the momentum-weighted flux with the armed
        ``a_P`` mode and ``dt_cell``, and boundary conditions that read an old
        level read the armed ``set_bc_old_state`` (else ``state`` itself).  Called
        right after a step is certified, with that step still armed, the ghosts
        and the flux are therefore exactly the ones its certificate saw.

        The ``else`` is the one way to miss that: a step whose old-level boundary
        conditions (an advective outlet) read the time term's old level because
        NOTHING was armed reads another old level here, and the outlet ghosts --
        hence ``F`` and ``N`` there -- are not the certified ones.  A stepper that
        wants the effective level at its boundaries arms it explicitly,
        ``set_bc_old_state(old_eff)`` without ``dt`` (which is what an unarmed
        forward reads, bit for bit), so this capture reads it too.
        """
        state = self._checked_tensor(
            state, (int(self.num_cpd_cells), self._collocated_width()),
            "state (a compound-cell tensor, state_with_boundaries' layout)")
        flux = None
        if advecting_flux is not None:
            flux = self._checked_face_flux(advecting_flux, "advecting_flux")
        saved = (getattr(self, "_conv_mode", "implicit"),
                 getattr(self, "_conv_advecting_flux", None))
        readouts = self._evaluation_readouts()
        capture: Dict[str, Any] = {}
        try:
            self._conv_mode = "implicit" if flux is None else "linearised"
            self._conv_advecting_flux = flux
            self._conv_capture = capture
            with torch.no_grad(), _eager_forward():
                self.forward(phi_old_cpd_cell=state.clone(), phi_new_cpd_cell=state.clone())
        finally:
            self._conv_capture = None
            self._conv_mode, self._conv_advecting_flux = saved
            for name, value in readouts.items():
                setattr(self, name, value)
        if "flux" not in capture:
            raise RuntimeError(f"{type(self).__name__}.conserved_form recorded no convection; "
                               "the capture hook is missing from this discretisation")
        return capture

    def explicit_convection(self, state: torch.Tensor, advecting_flux=None) -> torch.Tensor:
        """``N(u)`` of ``state`` per interior cell, ``[N_cell, F]`` (transient matrix H4).

        The convection term exactly as the implicit residual carries it at
        ``state`` -- the case's scheme, ``convection_coeff``, the bounded source
        when the scheme is bounded -- on the state's conservative (BC-enforced,
        momentum-weighted) face flux, i.e. the flux ``mwi_face_flux`` returns and
        the continuity rows of the step that produced ``state`` are written on.
        Columns follow ``COLLOCATED_UNKNOWNS``: momentum ``0:3``, zero on
        continuity, and on the Boussinesq solver ``N_T = div(u T)`` in column 4.
        ``advecting_flux`` (optional) carries the field with a STORED flux
        instead, e.g. ``F^n`` kept from the step that certified ``u^n``; with the
        state's own flux the two spellings agree.

        One forward evaluation (see ``_capture_convection`` for what it reads and
        what it leaves untouched).  Refused for transported turbulence rows and
        for a Crank-Nicolson convection blend, where "the" convection of a state
        is not one term.
        """
        self._refuse_unwired("explicit_convection")
        self._refuse_transported_rows("explicit_convection")
        theta = float(getattr(self, "_conv_theta", 1.0))
        if theta != 1.0:
            raise ValueError(f"explicit_convection on a case whose convection is blended "
                             f"over two levels (theta = {theta:g})")
        capture = self._capture_convection(state, advecting_flux)
        reference = capture["flux"]
        columns = []
        for name, width, _, _ in self.COLLOCATED_UNKNOWNS:
            block = capture.get(name)
            if block is None:
                block = reference.new_zeros((int(self.num_cells), width))
            columns.append(block)
        return torch.cat(columns, dim=-1)

    def mwi_face_flux(self, state: torch.Tensor) -> torch.Tensor:
        """The conservative face flux of ``state``, ``[N_face, 1]`` (for ``F^n``).

        The face-normal velocity continuity is written on -- momentum-weighted
        when the case runs MWI, after the flux-level boundary pass -- which is
        also the flux implicit convection is carried by.  ``F* = 2 F^n - F^{n-1}``
        built from two of these is therefore discretely divergence-free to the
        tolerance the two steps were certified at.  One forward evaluation, as
        ``explicit_convection``.
        """
        self._refuse_unwired("mwi_face_flux")
        return self._capture_convection(state)["flux"]

    def set_ap_mode(self, mode: str, flux=None) -> None:
        """Select what ``momentum_diagonal`` returns, hence ``d_f`` (transient matrix H1).

        ``"default"`` (no flux) is the shipped coefficient.  ``"sbdf"`` (no flux)
        is ``a0 V/dt + viscous`` and needs a live time term: without it the
        coefficient would be the bare viscous conductance.  ``"oseen"`` takes the
        fixed advecting flux ``F*`` (``[N_face, 1]``) and builds the convective
        half from it.  Both non-default modes make ``d_f`` independent of the
        unknowns, which makes the step's residual affine and its assembled
        Jacobian exact (an implicit ``d_f`` reaches three hops; the colouring
        covers two).
        """
        if mode not in AP_MODES:
            raise ValueError(f"unknown a_P mode {mode!r}; valid: {', '.join(AP_MODES)}")
        if mode != "oseen" and flux is not None:
            raise ValueError(f"a_P mode {mode!r} takes no flux (only \"oseen\" reads one)")
        if mode == "default":
            self._ap_mode, self._ap_flux = "default", None
            return
        self._refuse_unwired(f"a_P mode {mode!r}")
        if mode == "sbdf":
            coefficient = getattr(self, "unsteady_coeff", None)
            if coefficient is None or not bool((coefficient != 0).all()):
                raise ValueError(
                    "a_P mode \"sbdf\" is a0 V/dt + viscous and this case's time term is "
                    "off somewhere (theta_PDE[..., 0] == 0): d_f would be V over the bare "
                    "viscous conductance")
            self._ap_mode, self._ap_flux = "sbdf", None
            return
        checked = self._checked_face_flux(flux, "set_ap_mode('oseen')")   # before any assignment
        self._ap_mode, self._ap_flux = "oseen", checked

    def set_bc_old_state(self, state, dt=None) -> None:
        """What boundary conditions that read an OLD level read (transient matrix H9).

        ``forward`` hands its ``phi_old_cpd_cell`` to two consumers: the time term
        and the time-aware boundary conditions (``fvfield["uvw_old_cpd_cell"]``,
        read by ``setU.Advectiveoutlet``).  Under a two-level scheme the stepper
        passes the EFFECTIVE old level ``(4 u^n - u^{n-1}) / 3``, which is right
        for the time term and is not a state the outlet ever had.  Armed with the
        true ``u^n`` (a compound-cell state ``[N_cpd, F]``, detached), the
        boundary conditions read it and the time term keeps reading
        ``phi_old_cpd_cell``.

        ``dt`` is the TRUE step ``t^{n+1} - t^n`` for those conditions (the
        outlet's ``alpha = U_c dt / delta``).  Without it they keep reading
        ``dt_cell``, which is the effective step ``dt / a0`` -- equal to ``dt``
        under first-order schemes and ``2 dt / 3`` under BDF2/SBDF2 at a constant
        step, so an order-2 stepper must pass it.  ``None`` disarms both.
        """
        if state is None:
            if dt is not None:
                raise ValueError("set_bc_old_state(None) takes no dt")
            self._bc_old_state, self._bc_dt_cell = None, None
            return
        self._refuse_unwired("an armed boundary-condition old level")
        armed = self._checked_tensor(
            state, (int(self.num_cpd_cells), self._collocated_width()), "set_bc_old_state")
        bc_dt = None
        if dt is not None:
            dt = float(dt)
            if not (math.isfinite(dt) and dt > 0.0):
                raise ValueError(f"set_bc_old_state: dt must be finite and positive, got {dt!r}")
            bc_dt = torch.full_like(self.dt_cell, dt)
        self._bc_old_state, self._bc_dt_cell = armed, bc_dt

    def _bc_old_velocity(self, uvw_old_cpd_cell: torch.Tensor) -> torch.Tensor:
        """The velocity old-level BCs read: the armed true ``u^n``, else the old state."""
        armed = getattr(self, "_bc_old_state", None)
        return uvw_old_cpd_cell if armed is None else armed[..., 0:3]

    def iter_named_residuals(self):
        """Yield (name, tensor) pairs for the current separated losses.

        Uses ``LOSS_COMPONENT_NAMES`` to label each slot of
        ``get_separated_losses()``. Slots labeled ``None`` are skipped.
        Slots where the tensor itself is ``None`` (e.g. disabled
        regularization terms) are also skipped.

        Training scripts use this to log per-equation residuals in a
        solver-agnostic way, avoiding the positional-unpacking bug that
        mis-labeled picoext's ``loss_phi_jump`` as ``T``.
        """
        try:
            components = self.get_separated_losses()
        except Exception:  # pragma: no cover — solver not fully initialized yet
            return
        if components is None:
            return

        labels = getattr(self, "LOSS_COMPONENT_NAMES", ())
        if not labels or len(labels) != len(components):
            # Fallback to generic names so we never silently mis-label.
            labels = tuple(f"loss_{i}" for i in range(len(components)))

        for name, tensor in zip(labels, components):
            if name is None or tensor is None:
                continue
            yield name, tensor

    @abstractmethod
    def register_properties(self, fv_graph: Any, force_register: bool = False) -> None:
        """
        Register geometric and physical properties from the FV graph.

        Args:
            fv_graph: Finite volume graph containing mesh geometry and physics parameters
            force_register: If True, force re-registration even if already registered
        """
        pass

    @abstractmethod
    def enforce_bc_1st(self, fvfield: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """
        Apply first-type (Dirichlet) boundary conditions.

        Args:
            fvfield: Dictionary containing field variables (uvw_cpd_cell, p_cpd_cell, etc.)

        Returns:
            Updated fvfield dictionary with boundary conditions applied
        """
        pass

    @abstractmethod
    def enforce_cyclic(self, fvfield: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """
        Apply cyclic boundary conditions.

        Args:
            fvfield: Dictionary containing field variables

        Returns:
            Updated fvfield dictionary with cyclic BC applied
        """
        pass

    @abstractmethod
    def enforce_mixed(self, fvfield: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """
        Apply mixed boundary conditions (e.g., Robin BC).

        Args:
            fvfield: Dictionary containing field variables

        Returns:
            Updated fvfield dictionary with mixed BC applied
        """
        pass

    def _cell_constrained_rows(self, var_type):
        """(cell_row, cell_cpd, ghost_cpd, corner_slot, combine) for BCs
        constraining their adjacent CELL.

        OpenFOAM's wall-function machinery does two things at a wall, and only
        one of them is a ghost write.  ``omegaWallFunctionFvPatchScalarField::
        manipulateMatrix`` calls ``matrix.setValues(patch().faceCells(),
        patchInternalField())`` (v2412 line 606), and ``setValues`` cuts the
        cell's couplings and leaves ``psi[celli] = value`` (fvMatrix.C:310-317):
        **the wall-adjacent cell's transport equation is discarded and replaced
        by a Dirichlet constraint.**  A BC that declares
        ``constrains_adjacent_cell`` is asking for exactly that row.

        Returns None when no BC on this variable declares the capability --
        which is every case except a low-Re omega wall, so the caller skips the
        mechanism entirely rather than paying for an empty gather.  Otherwise
        the tuple addresses ONE row per constrained cell: ``cell_row`` indexes
        the residual, ``cell_cpd`` the state, ``ghost_cpd`` the ghost rows
        contributing to it, and ``corner_slot`` says which cell each of those
        ghosts belongs to.

        A cell adjacent to TWO constrained wall faces is a corner, and
        OpenFOAM averages the per-face values there with
        ``cornerWeights_ = 1/(faces on this cell)``
        (omegaWallFunctionFvPatchScalarField.C:115-120, applied at :244-322).
        ``corner_slot`` is what lets the caller reproduce that mean instead of
        writing the row twice and silently keeping whichever came last.
        ``combine`` is the declaring BC's own averaging rule, because the mean
        must be taken in the variable OpenFOAM averages (omega) and not
        necessarily in the one we store (ln omega).

        Cached per variable: the index sets are pure connectivity, so they move
        only when the geometry is re-registered.
        """
        cache = getattr(self, "_constrained_rows_cache", None)
        if cache is None:
            cache = self._constrained_rows_cache = {}
        if var_type in cache:
            return cache[var_type]

        cells, ghosts, declaring, forms = [], [], set(), set()
        for bcpatch_i, bcpatch in enumerate(self.bcpatch_batch.first):
            offset = self.bcpatch_batch.first_patch_idx_offset[bcpatch_i]
            for bc in bcpatch:
                if not getattr(bc, "constrains_adjacent_cell", False):
                    continue
                if bc.varType != var_type:
                    continue
                gp = bc.patch_idx + offset
                s_f = int(self.fvpatch_batch.start_idx_face[gp])
                e_f = int(self.fvpatch_batch.end_idx_face[gp])
                s_c = int(self.fvpatch_batch.start_idx_cpd_cell[gp])
                e_c = int(self.fvpatch_batch.end_idx_cpd_cell[gp])
                # Ghost rows are face-ordered over [s_f, e_f) -- the alignment
                # every mirror BC in the package already relies on, and the one
                # setOmega.Wall's own ghost write uses.
                cells.append(self.neighbor_cpd_cell[0, s_f:e_f])
                ghosts.append(torch.arange(s_c, e_c,
                                           device=self.cell_volume.device))
                declaring.add(type(bc))
                # The row's ALGEBRAIC form, per instance, because it is a bc
                # toml entry and not a property of the class.  Collected as a
                # set for the same reason ``declaring`` is: two patches of one
                # variable that disagree would give a corner two different
                # rows, and the disagreement has to be refused rather than
                # resolved by iteration order.
                forms.add(str(getattr(bc, "constraint_form", "difference")))

        if not cells:
            cache[var_type] = None
            return None
        if len(declaring) > 1:
            raise ValueError(
                f"{len(declaring)} different BC classes constrain adjacent "
                f"cells for {var_type!r} ({sorted(c.__name__ for c in declaring)}"
                "); a corner shared by two of them has no single averaging rule")
        if len(forms) > 1:
            raise ValueError(
                f"BCs constraining adjacent cells for {var_type!r} disagree on "
                f"constraint_form ({sorted(forms)}); a cell owning faces from "
                "both would get two different rows")

        face_cell_cpd = torch.cat(cells)
        ghost_cpd = torch.cat(ghosts)
        # One row per CELL, and which ghosts feed it (OpenFOAM's cornerWeights_).
        cell_cpd, corner_slot = torch.unique(face_cell_cpd, return_inverse=True)
        # cpd row -> interior row.  Built rather than assumed: the residual
        # tensors are ``[..., mask_interior_cell, :]``, which preserves order
        # but is not required to start at 0 on a batched graph.
        lookup = torch.full((int(self.num_cpd_cells),), -1, dtype=torch.long,
                            device=cell_cpd.device)
        interior = self.mask_interior_cell.reshape(-1).nonzero(as_tuple=True)[0]
        lookup[interior] = torch.arange(interior.numel(), device=cell_cpd.device)
        cell_row = lookup[cell_cpd]
        if int(cell_row.min()) < 0:
            raise ValueError(
                f"a BC constraining its adjacent cell for {var_type!r} names a "
                "wall-face owner that is not an interior cell; the constraint "
                "would replace a row that does not exist")
        combine = getattr(next(iter(declaring)), "combine_cell_constraints",
                          None)
        if combine is None:
            raise ValueError(
                f"{next(iter(declaring)).__name__} declares "
                "constrains_adjacent_cell but no combine_cell_constraints; the "
                "assembly needs it to scatter per-face wall values onto cells "
                "(and to average a corner in the right variable)")
        form = next(iter(forms))
        if form not in _CELL_CONSTRAINT_FORMS:
            raise ValueError(
                f"unknown constraint_form {form!r} for {var_type!r}; "
                f"known: {sorted(_CELL_CONSTRAINT_FORMS)}")
        cache[var_type] = (cell_row, cell_cpd, ghost_cpd, corner_slot, combine,
                           form)
        return cache[var_type]

    def _apply_cell_constraints(self, residual: torch.Tensor, field_name: str,
                                phi_cpd_cell: torch.Tensor) -> torch.Tensor:
        """Replace the wall-adjacent rows a BC constrains by the BC's own row.

        Two forms exist, selected per patch by ``constraint_form``
        (``_CELL_CONSTRAINT_FORMS`` above): the default ``phi_c - phi_ghost``,
        which is v2412's, and ``phi_c/phi_ghost - 1``, which is the same root
        with the wall value divided out.  Everything the rest of this
        docstring says holds for both -- the target is still read back from
        the ghost, the row still reaches the Jacobian by autograd, and the
        sparsity pattern is unchanged (a quotient reads exactly the operands a
        difference does).  Only the row's SCALE differs, and that is the point.

        The substituted row is the boundary condition the BC already wrote, not
        a second spelling of it: the target is READ BACK from the ghost the
        BC's own ``eval`` filled earlier in this same forward, so the formula
        lives in exactly one place (``setOmega.Wall``) and the two cannot
        drift.  It also makes ghost == cell at any root, which zeroes this
        face's snGrad -- v2412's "apply zero-gradient condition for omega"
        (omegaWallFunctionFvPatchScalarField.C:167-176) falls out rather than
        being imposed separately.

        Newton-legality, which is the whole reason this is written HERE and not
        as a post-hoc edit of an assembled matrix.  The substitution goes
        through the residual, so the constraint reaches the Jacobian by
        autograd along the same path as every other term -- exactly the
        pattern ``CoupledNewton.casestate.close_the_out_of_plane_rows`` uses
        for the out-of-plane velocity, and ``run_phase5``'s frozen-turbulence
        stage uses for the whole tail block.  Nothing is pinned by index
        outside the differentiated function, and no state is overwritten.

        The row's sparsity depends on what the declaring BC's ghost value
        READS, and the two live cases differ:

          setOmega.Wall           ``6 nu/(beta1 y^2)`` is registration-time
                                  geometry, so the derivative is
                                  ``d/d(phi_c) = 1`` and nothing else -- an
                                  exact identity row.
          setEpsilon.
          Epsilonwallfunction     ``Cmu^0.75 k_c^1.5/(kappa y)`` reads k in
                                  the SAME cell (epsWF.C:227-234), so the row
                                  also carries an epsilon-row/k-column entry.

        Both patterns are SUBSETS of the assembled one -- the second because
        its extra entry is same-cell, hence already in the block diagonal --
        so the colouring and the probe plan are unchanged.  That is measured,
        not assumed (the campaign has one shipped Jacobian that failed its own
        gate by three orders on exactly this kind of reasoning):
        ``test_kepsilon_wall_functions.py::test_assembled_jacobian_reproduces_
        its_own_jvp`` reads ``||Jv - J.v||/||Jv|| = 4.27e-16`` with the
        epsilon<-k diagonal populated and its off-diagonal exactly zero.
        """
        var_type = VAR_TYPE_BY_FIELD.get(field_name)
        if var_type is None:
            return residual
        rows = self._cell_constrained_rows(var_type)
        if rows is None:
            return residual
        cell_row, cell_cpd, ghost_cpd, corner_slot, combine, form = rows
        # ALWAYS through ``combine``, never a bare gather, even where every cell
        # has exactly one wall face.  ``ghost_cpd`` is in patch-face order while
        # ``cell_cpd`` is sorted (torch.unique), and the wall-face owners are
        # NOT ascending -- the sheared fixture's first faces own cells
        # [0, 24, 1, 25, ...].  Pairing ghost i with cell i would therefore
        # constrain cells to OTHER cells' wall values, and would do it silently
        # on exactly the meshes with no corner to give the mismatch away (every
        # flat-plate rung).  The scatter by ``corner_slot`` is the permutation
        # and the corner mean at once.
        target = combine(phi_cpd_cell.index_select(-2, ghost_cpd),
                         corner_slot, int(cell_cpd.numel()))
        cell = phi_cpd_cell.index_select(-2, cell_cpd)
        if form == "difference":
            constraint = cell - target
        else:                                   # "relative", see the docstring
            constraint = cell / target - 1.0
        return residual.index_copy(-2, cell_row, constraint)

    def enforce_coupled(self, fvfield: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """Apply coupled boundary conditions (Phase 4).

        Coupled BCs depend on other patches being already populated
        (e.g., exprFixedValue where T_outlet = avg(T@inlet) + deltaT).
        Runs AFTER enforce_bc_1st, enforce_cyclic, and enforce_mixed.

        Default implementation iterates bcpatch_batch.coupled.
        Solvers without coupled BCs get a no-op (empty list).
        """
        for bcpatch_i, bcpatch in enumerate(self.bcpatch_batch.coupled):
            for bc_func in bcpatch:
                fvfield = bc_func.eval(
                    fvfield, self,
                    patch_idx_offset=self.bcpatch_batch.coupled_patch_idx_offset[bcpatch_i],
                )
        return fvfield

    def enforce_bcs_only(
        self,
        phi_old_cpd_cell: torch.Tensor,
        phi_new_cpd_cell: torch.Tensor,
    ) -> torch.Tensor:
        """Apply boundary conditions to ``phi_new`` without computing residuals.

        Concrete solvers must override this to mirror the BC enforcement
        sequence used in the cell-value phase of their ``forward()``
        (typically ``enforce_bc_1st`` -> ``enforce_cyclic`` -> ``enforce_mixed``,
        with ``enforce_empty`` folded into ``enforce_bc_1st``). The returned
        tensor must have the same shape and channel order as ``phi_new_cpd_cell``.

        This method is primarily used by the supervised / data-driven
        training path, which needs to project a predicted state onto the
        boundary-condition manifold without triggering any residual
        computation or loss bookkeeping.

        Args:
            phi_old_cpd_cell: Previous time step solution [N_cpd_cells, n_vars].
                Kept in the signature for symmetry with ``forward()`` and for
                future BCs that may depend on the old field.
            phi_new_cpd_cell: Current time step solution [N_cpd_cells, n_vars].

        Returns:
            phi_bc_enforced: BC-enforced solution with the same shape as
                ``phi_new_cpd_cell``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement enforce_bcs_only()"
        )

    @abstractmethod
    def time_scheme(
        self,
        uvw_old_cell: torch.Tensor,
        uvw_new_cell: torch.Tensor
    ) -> torch.Tensor:
        """
        Apply temporal discretization scheme.

        Args:
            uvw_old_cell: Velocity at previous time step [N_cells, 3]
            uvw_new_cell: Velocity at current time step [N_cells, 3]

        Returns:
            Unsteady term contribution [N_interior_cells, 3]
        """
        pass

    @abstractmethod
    def conserved_form(
        self,
        unsteady_cell: torch.Tensor,
        uvwp_new_cpd_cell: torch.Tensor,
        uvwp_hat_cpd_cell: Optional[torch.Tensor] = None,
        grad_uvwp_hat_cpd_cell: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Evaluate the conserved form of governing equations.

        Args:
            unsteady_cell: Unsteady term [N_interior_cells, 3]
            uvwp_new_cpd_cell: Current solution [N_cpd_cells, 4]
            uvwp_hat_cpd_cell: High-order interpolated values [N_cpd_cells, 7]
            grad_uvwp_hat_cpd_cell: High-order gradients [N_cpd_cells, 7, 3]

        Returns:
            Tuple of ``loss_per_graph`` and the updated solution tensor. The
            loss is ``[B]`` for legacy ``[N,C]`` state input and ``[T,B]`` for
            transient ``[T,N,C]`` input. Concrete solvers must preserve both
            axes; callers choose the graph reduction and sum physical time.
        """
        pass

    def get_separated_losses(self) -> Tuple[Optional[torch.Tensor], ...]:
        """
        Get individual loss components for analysis.

        Returns:
            Tuple of loss components (implementation-specific)
        """
        raise NotImplementedError(
            "Subclasses must override get_separated_losses(). "
            "Base-class fallback removed (contained typo 'self.loss_phi_jum'). "
            "Each concrete solver already provides its own implementation."
        )

    @abstractmethod
    def forward(
        self,
        phi_old_cpd_cell: Optional[torch.Tensor] = None,
        phi_new_cpd_cell: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass for the solver.

        Args:
            phi_old_cpd_cell: Previous time step solution [N_cpd_cells, n_vars]
            phi_new_cpd_cell: Current time step solution [N_cpd_cells, n_vars]

        Returns:
            Tuple of ``loss_per_graph`` and the updated solution tensor. The
            loss is ``[B]`` for legacy ``[N,C]`` state input and ``[T,B]`` for
            transient ``[T,N,C]`` input. The graph and time axes remain
            explicit so the caller can apply its requested reduction.
        """
        pass

    def __call__(
        self,
        phi_old_cpd_cell: Optional[torch.Tensor] = None,
        phi_new_cpd_cell: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Make the solver callable, delegating to forward method.

        Args:
            phi_old_cpd_cell: Previous time step solution
            phi_new_cpd_cell: Current time step solution

        Returns:
            Output from forward method
        """
        return self.forward(phi_old_cpd_cell, phi_new_cpd_cell)
