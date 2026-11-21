import sys
import os
from typing import Tuple, Optional, Any
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

import logging
logger = logging.getLogger(__name__)

import torch
from torch_geometric.utils import scatter
from FVdomain.SetBC.onthefly.BCbase import VARType, VAR_TYPE_BY_FIELD
from FVsolver.applications.base_solver import BaseSolver
from FVsolver.FVdiscretization.FVgrad import gradient_reconstruction
from FVsolver.FVdiscretization.ddt_schemes import parse_ddt_scheme
from FVsolver.FVdiscretization.FVstress import dev2_transpose_face_flux
from FVsolver.Turbulence import create_turbulence_model
from torch_geometric.nn import global_add_pool, global_mean_pool
class Solver(BaseSolver):
    """
    Finite Volume integrator class for assembling and solving discretized equations.

    Inherits from FV_flux to provide flux calculation capabilities.
    Only the conserved form of the Navier-Stokes equations is implemented;
    the non-conserved form has been removed.
    """

    # Labels for each component returned by get_separated_losses(), in order.
    LOSS_COMPONENT_NAMES = ("cont", "mom", "energy", "phi_jump", "p_rghRef", "checker")

    # See BaseSolver.COLLOCATED_UNKNOWNS.  The pressure unknown is ``p_rgh``, not
    # ``p``, and that is the one thing about this table that can be got wrong
    # silently: this solver assembles momentum WITHOUT a pressure term and then
    # adds grad(p_rgh)*V, with the hydrostatic part carried by the buoyancy source
    # gh*beta*grad(T).  Naming the unknown ``p`` would export a p_rgh field under
    # the name of the full pressure and double-count the hydrostatic component
    # against anything that reads it back.
    COLLOCATED_UNKNOWNS = (
        ("U", 3, "bc_uvw_cpd_cell", "residual_momentum"),
        ("p_rgh", 1, "bc_p_rgh_cpd_cell", "residual_continuity"),
        ("T", 1, "bc_t_cpd_cell", "residual_energy"),
    )

    # The forward reads BaseSolver's IMEX / Oseen hooks, the energy row
    # following the momentum row's convection switch (div(u T) shares the flux).
    TIME_INTEGRATOR_HOOKS = True

    def __init__(self, params: Any) -> None:
        """
        Initialize the finite volume integrator.

        Args:
            params: Configuration object containing discretization parameters,
                   time integration scheme, and equation form settings.
        """
        super(Solver, self).__init__()
        self.params = params

        # Cache solver config to avoid tomlkit objects in forward pass (torch.compile compatibility)
        solver_config = params.fvconfig.get("solver", {})
        self.dim = int(solver_config.get("dim", 3))  # Convert to native Python int

        # Turbulence hook (turbulence design doc section 4).  Same composition
        # as picoext: Laminar for every live config, instance
        # COLLOCATED_UNKNOWNS shadowing so collocated_newton.py:127 picks up
        # any TRANSPORTED rows with no driver change.  Phase-1 hash gate
        # (heated-cylinder buoyant case): residual and Jacobian
        # unchanged within the measured same-code nondeterminism floor.
        self.turbulence = create_turbulence_model(
            solver_config.get("turbulence", "laminar"), params, self)
        self.COLLOCATED_UNKNOWNS = (
            type(self).COLLOCATED_UNKNOWNS + self.turbulence.TRANSPORTED)
        # Transported turbulence rows (phase 5b, design A.2/A.8): the same
        # host-owned generic scalar-transport assembly picoext landed in
        # phase 3, with the buoyant packed layout -- the channels appear
        # after T, matching how COLLOCATED_UNKNOWNS composes.  The phase-3
        # NotImplementedError guard is gone; this solver's extra seams over
        # picoext are the alphat energy diffusivity (A.3) and the Gb
        # closure-state operands (grad T, g, beta, U) the buoyant
        # _assemble_transported_residuals supplies.
        self._turb_transported = self.turbulence.TRANSPORTED
        self._turb_width = sum(e[1] for e in self._turb_transported)
        # dev2 assembly gate, same semantics as picoext (design section 2.5):
        # only a model declaring non-uniform nu_eff assembles the term;
        # Laminar keeps the buoyant path at its measured nondeterminism
        # floor (heated-cylinder hash gate).
        self._dev2_active = False
        # Opt-in: assemble OpenFOAM's explicit transpose half
        # div(nu dev2((grad U)^T)) for constant nu too, so a laminar case can be
        # compared term-for-term with simpleFoam's Stokes model. Default "off"
        # keeps every landed laminar benchmark bit-identical.
        _vt = str(params.fvconfig.get("fvSchemes", {}).get(
            "laminarViscousTranspose", "off")).strip().lower()
        if _vt not in ("off", "on"):
            raise ValueError("fvSchemes.laminarViscousTranspose must be \"off\" or \"on\", "
                             f"got {_vt!r}")
        self._laminar_viscous_transpose = _vt == "on"
        # alphat seam (phase 5b, design A.3 -- the THIRD nut consumer the
        # isothermal census missed): alphaEff = alpha + nut/Prt on the
        # T-row diffusion, TEqn.H:2/5/9-10.  Prt is a solver-level constant
        # from constant/transportProperties in OpenFOAM
        # (readTransportProperties.H:18), hence a [transport] case-toml key
        # here, NOT a [turbulence] one; 0.85 is the OpenFOAM heat-transfer
        # default (alphatJayatillekeWallFunction Prt default, and the value
        # its tutorials' transportProperties carry).  The stash is refreshed
        # per forward beside _nu_eff_face; None covers direct
        # conserved_form calls (probes), which fall back to alpha_coeff.
        self.Prt = float(
            params.fvconfig.get("transport", {}).get("Prt", 0.85))
        self._alpha_eff_face = None
        self._mms_turb_source = None
        # The WLSQ family this solver reconstructs/interpolates: nine wide
        # laminar (class constants below), plus the transported turbulence
        # channels appended after T_old -- turb_new lives at 9:9+W of the
        # family and gets a gradient; turb_old trails OUTSIDE the family
        # (ddt only), exactly as picoext keeps its turb_old past 7+W.  The
        # inert-column narrowing keeps its two laminar live runs and gains
        # the turb run; with W = 0 both attributes equal the class
        # constants and the laminar path is unchanged.
        self._wlsq_width = self._WLSQ_WIDTH + self._turb_width
        self._wlsq_live_runs = self._WLSQ_LIVE_RUNS + (
            ((9, 9 + self._turb_width),) if self._turb_width else ())

        # Loss weight parameters (read from fvconfig.toml)
        fvsolution = params.fvconfig.get("fvSolution", {})
        self.loss_cont_weight = fvsolution.get('loss_cont', 1)
        self.loss_mom_weight = fvsolution.get('loss_mom', 1)
        self.loss_energy_weight = fvsolution.get('loss_energy', 1)
        # Weight for the transported turbulence residuals (k, omega-tilde /
        # epsilon); inert while TRANSPORTED is empty (laminar).
        self.loss_turb_weight = fvsolution.get('loss_turb', 1.0)
        self.loss_phi_jump_weight = fvsolution.get('loss_phi_jump', 0.0)
        self.loss_p_Ref_weight = fvsolution.get('loss_pRef', 0.01)  # Weight for pressure reference constraint
        # Global inflow/outflow balance; 0 (default) turns it off.
        self.loss_mass_balance_weight = fvsolution.get('loss_massBalance', 0.0)
        self.loss_mom_adaptive_balance = bool(fvsolution.get('loss_mom_adaptive_balance', True))
        print(f"[buoyantBoussinesqpicoext] loss_mom_adaptive_balance={self.loss_mom_adaptive_balance}")
        self.loss_checker_weight = float(fvsolution.get('loss_checker', 0.0))
        # NOTE: the Dirichlet-p owner consistency loss (loss_p_bc_owner, user
        # idea) is NOT implemented: it failed (an arm
        # under fvm_loss_pbc had no effect on the
        # outlet u/p oscillation). Do not re-introduce.
        self._wide_stencil_built = False
        # Parse convection schemes once (for bounded detection)
        self._parse_schemes()

    def _parse_schemes(self):
        """Parse and cache scheme configuration for efficient forward pass."""
        div_schemes = self.params.fvschemes.get("divSchemes", {})

        # Parse U/T schemes. An OpenFOAM-style trailing gradient token is still
        # accepted per field (OF: "Gauss linearUpwind grad(U)") but is inert
        # here: "LinearUpwind leastSquares" and "LinearUpwind"
        # behave identically, because the solver computes ONE unified WLSQ
        # gradient per forward and the token does not select a second, compact
        # reconstruction gradient. Under the default [stencil] khops=1 that
        # unified gradient IS the compact 1-hop reconstruction, giving OpenFOAM
        # linearUpwind parity to ~1e-13 (MMS:
        # experiments/MMS_SchemeAccuracy, convection/linearUpwind_prod; a wide
        # khops>=2 stencil low-passes the gradient and carries ~1.6x OF's
        # dissipation at marginal k*dx).
        self.U_scheme, _u_grad_token = self._split_grad_token(
            div_schemes.get("U", "Linear")
        )
        self._recon_compact_U = _u_grad_token == "leastSquares"

        self.T_scheme, _t_grad_token = self._split_grad_token(
            div_schemes.get("T", "Linear")
        )
        self._recon_compact_T = _t_grad_token == "leastSquares"

        # Per-field convection schemes for the transported turbulence
        # scalars (OpenFOAM: div(phi,k), div(phi,omega)); grad tokens are
        # accepted and inert exactly as for U/T.  Default "Linear" -- the
        # MMS order gates run central; production cases override.
        self._turb_schemes = {}
        for _fname, *_rest in self._turb_transported:
            _s, _tok = self._split_grad_token(div_schemes.get(_fname, "Linear"))
            self._turb_schemes[_fname] = _s

        # The trailing token on "corrected leastSquares" is likewise inert since
        # the solver keeps one gradient (the snGrad non-orthogonal correction reads the same unified
        # WLSQ gradient as every other consumer). It only records the OpenFOAM
        # convention that correctedSnGrad looks its correction gradient up from
        # gradSchemes, where every OF gradScheme is compact 1-hop
        # (correctedSnGrad.C: fullGradCorrection -> mesh.gradScheme("grad(vf)")).
        sngrad = self.params.fvschemes.get("snGradSchemes", None)
        if sngrad is not None:
            self._snGrad_scheme, _sg_grad_token = self._split_grad_token(sngrad)
            self._sngrad_corr_compact = _sg_grad_token == "leastSquares"
        else:
            self._snGrad_scheme = "corrected" if "corrected" in self.params.fvschemes.get("laplacianSchemes", "").lower() else "uncorrected"
            self._sngrad_corr_compact = False

        # Boundary-face diffusion treatment (momentum + energy):
        #   "snGrad" (the default, and the only supported value since
        #             there is one gradient): physical boundary (non-cyclic) faces use the
        #             compact OpenFOAM-style surface-normal gradient, while interior
        #             faces keep the WLSQ reconstruction. On boundary faces
        #             correctionVectors == 0 and nonOrthDeltaCoeffs == deltaCoeffs, so
        #             the boundary snGrad reduces to (phi_ghost - phi_owner) * deltaCoeffs
        #             — exactly OpenFOAM's boundary-face snGrad.
        # The legacy "wlsq" boundary flux now raises at init: ghost cpd cells no
        # longer receive a WLSQ gradient (interior-only unified reconstruction),
        # and that flux was the sole consumer of physical-ghost gradients — it
        # would silently read zeros.
        _bds = str(self.params.fvschemes.get("boundaryDiffusionScheme", "snGrad")).lower()
        if _bds != "sngrad":
            raise ValueError(
                '[buoyantBoussinesqpicoext] boundaryDiffusionScheme="wlsq" is '
                "no longer supported (ghost WLSQ gradients abandoned "
                "2026-07-18; the unified gradient is interior-only). Remove "
                'the key or set it to "snGrad".'
            )
        self._boundary_diffusion_sngrad = True

        # Cyclic-face diffusion treatment:
        #   "wlsq"   (default): cyclic faces keep the WLSQ-interpolated face
        #                       gradient (interior-like, original ext behavior).
        #   "sngrad": cyclic faces also use the compact snGrad — OpenFOAM's
        #             coupled-patch treatment: (phi_partner - phi_owner) *
        #             nonOrthDeltaCoeffs (+ non-orth correction, ~0 on the
        #             orthogonal extruded seam). The compact term couples the two
        #             seam owners through the diffusion flux and thereby fills the
        #             anti-periodic cyclic-seam null mode of the WLSQ face
        #             gradient (outlet∩cyclic w columns).
        self._cyclic_diffusion_sngrad = (
            str(self.params.fvschemes.get("cyclicDiffusionScheme", "wlsq")).lower() == "sngrad"
        )
        if self._cyclic_diffusion_sngrad:
            print(
                "[buoyantBoussinesqpicoext] cyclicDiffusionScheme=snGrad ACTIVE — "
                "cyclic faces use compact coupled-patch snGrad diffusion"
            )
        # Either switch activates the snGrad face override in conserved_form.
        self._diffusion_sngrad_face = (
            self._boundary_diffusion_sngrad or self._cyclic_diffusion_sngrad
        )

        # Momentum pressure discretization follows momentumResidual (the single
        # knob): "cell" -> unified WLSQ grad(p_rgh) at interior
        # cells times V_cell; "face_cv" -> the compact face snGrad(p_rgh)
        # consumed directly inside the face-CV loss. The former pressureScheme
        # key (p_flux/p_grad) is gone.
        for _legacy in ("pressureScheme", "pressureSchemes"):
            if _legacy in self.params.fvschemes:
                print(f"[buoyantBoussinesqpicoext] WARNING: fvSchemes '{_legacy}' "
                      "is IGNORED (removed 2026-07-18; pressure form follows "
                      "momentumResidual)")
        if "cellPressureGradient" in self.params.fvschemes:
            print("[buoyantBoussinesqpicoext] WARNING: cellPressureGradient is "
                  "retired (2026-08-23): cell rows use the WLSQ gradient; key ignored")

        # Momentum residual TEST POSITION:
        #   "cell" (default): 3-component momentum residual on cell CVs (collocated).
        #   "face_cv": Perot/MAC-style face-normal momentum residual on staggered
        #             face CVs. The cell residual is assembled WITHOUT the pressure
        #             term, interpolated to faces and projected on n_f; pressure
        #             enters via the compact two-point snGrad:
        #                 r_f = [n_f . 0.5*(R_C + R_F) + theta_p * snGrad(p_rgh)_f] * V_f
        #             on internal faces only; physical/empty boundaries and
        #             cyclic faces without paired-owner residuals are masked.
        #             V_f = 0.5*(V_C + V_F) restores volume-integrated units so
        #             loss_mom keeps its usual scale. A 2-delta checkerboard p mode
        #             hits every face snGrad at full +-2A/dx strength with NO
        #             subsequent cell averaging, giving the momentum loss the
        #             first-order restoring force that the cell-centered
        #             pressure discretizations (the former p_grad/p_flux forms
        #             and the WLSQ grad(p_rgh)) structurally annihilate
        #             (probe: cos(g, mode)= +0.47 vs below-null for
        #             cell; Nyquist-peaked response spectrum; w cross-talk 1.4%).
        #             The cell pressure term is dropped in this mode. Face count ~3*N_cell
        #             on hex meshes, matching the 3-component cell equation budget.
        # "cell+face_cv" (as in picoext): COMBINED test space.
        # The two forms have complementary parity nulls — the cell branch's
        # WLSQ grad(p_rgh) is blind to the 2Δx cell-p mode; the face_cv half
        # average is blind to cell-alternating velocity-residual modes.
        # Concatenating both leaves neither null unpenalized.
        _mom_res = str(self.params.fvschemes.get("momentumResidual", "cell")).lower()
        assert _mom_res in ("cell", "face_cv", "cell+face_cv", "face_cv+cell"), _mom_res
        self._momentum_residual_face = _mom_res in ("face_cv", "cell+face_cv", "face_cv+cell")
        self._momentum_residual_cell = _mom_res in ("cell", "cell+face_cv", "face_cv+cell")
        # face_cv averaging of the (pressure-free) residual density onto faces:
        #   "half" (default): arithmetic 0.5*(R_C+R_F). Annihilates streamwise
        #             2-delta VELOCITY modes — their cell-alternating residual
        #             images cancel in the average — leaving a u-side
        #             quasi-null, the dual of the cell-centered pressure null
        #             (u-mode probe: 2dx u response 2.64x below 4dx).
        #   "upwind": flux-sign pick of the upwind cell's residual density —
        #             no cancellation. Probe: 2dx u visibility x3.0 (spectrum
        #             flat like the cell form), observed-u-mode restoring
        #             cos 0.064 -> 0.314, p part (snGrad) bit-identical,
        #             outlet-layer w gradient +1.4% (frozen-field u-mode probe).
        self._facecv_avg = str(self.params.fvschemes.get("faceCvAvg", "half")).lower()
        assert self._facecv_avg in ("half", "upwind"), self._facecv_avg
        # Which faces get a face-CV row.  "all" is the validated
        # objective; "none" (an earlier default) had no pressure-level
        # anchor at all -- see facecv_active_face_mask.  Runs from that window
        # that do not name the key change numbers under this default.
        self._facecv_boundary_rows = str(
            self.params.fvschemes.get("faceCvBoundaryRows", "all"))
        assert self._facecv_boundary_rows in ("none", "pressureDirichlet", "all"), \
            self._facecv_boundary_rows
        if self._momentum_residual_face and self._momentum_residual_cell:
            print("[buoyantBoussinesqpicoext] momentumResidual=cell+face_cv ACTIVE — "
                  "face-normal momentum residual on staggered face CVs "
                  f"(compact snGrad pressure coupling; faceCvAvg={self._facecv_avg}) "
                  "PLUS full cell-CV residual incl. WLSQ grad(p_rgh)*V")
        elif self._momentum_residual_face:
            print("[buoyantBoussinesqpicoext] momentumResidual=face_cv ACTIVE — "
                  "face-normal momentum residual on staggered face CVs "
                  "(cell pressure term bypassed; compact snGrad pressure coupling; "
                  f"faceCvAvg={self._facecv_avg})")

        # "leastSquares" gradient tokens are still parsed (the token must be
        # stripped so scheme dispatch sees the bare scheme name) but since
        # there is one gradient, they trigger no second gradient computation:
        # the solver computes ONE unified WLSQ gradient per forward. Under
        # the default [stencil] khops=1 that gradient is itself the compact
        # 1-hop reconstruction (OpenFOAM-parity for the token consumers); on
        # legacy khops>=2 meshes the token consumers share the extended
        # gradient (single-computation policy).
        if self._recon_compact_U or self._recon_compact_T or self._sngrad_corr_compact:
            print("[buoyantBoussinesqpicoext] leastSquares gradient token "
                  "accepted — unified single WLSQ gradient (compact when "
                  "[stencil] khops=1)")

        # Shared with picoext through BaseSolver, not restated: the square
        # collocated Newton system built on THIS solver needs the same
        # grid-parity coupling the isothermal one does, and gets it from the same
        # lines.  Default off, so every GNN/ODIL case here is untouched.
        self._parse_momentum_interpolation("buoyantBoussinesqpicoext")
        self._parse_convection_time_weight("buoyantBoussinesqpicoext")

        # MMS solve-level verification (experiments/MMS_SchemeAccuracy/PCNO):
        # per-cell manufactured sources from an npz ("source" [N_cell, 4] =
        # Sx,Sy,Sz,ST). Single-case runs only.
        self._mms_source_path = self.params.fvschemes.get("mmsSource", None)
        # A bare filename resolves against the case's own h5 dir (the directory
        # --dataset_dir named, which is where this fvconfig.toml was read from),
        # so a variant keeps working when the case tree is renamed.  Every one of
        # the 88 tracked mmsSource entries was an ABSOLUTE path and every one of
        # them was dead: 78 named another machine's root, and the rest named
        # pre-restructure directories.
        if self._mms_source_path and not os.path.isabs(self._mms_source_path):
            self._mms_source_path = os.path.join(
                getattr(self.params, "dataset_dir", "."), self._mms_source_path)
        if self._mms_source_path:
            print(f"[buoyantBoussinesqpicoext] mmsSource ACTIVE — per-cell "
                  f"manufactured source from {self._mms_source_path}")
        # Conjugate heat transfer (docs/research/2026-10-15-cht-heatsink.md; opt-in): the fluid
        # and the solid region are two disconnected components of the case mesh. "chtRegions" names an npz with a
        # per-cell bool "solid" and the scalar "alpha_ratio" = k_solid / k_fluid; every face owned by a solid cell
        # then diffuses with alpha * alpha_ratio (the solid conduction, in the fluid's rho c_p units). The two
        # interface patches carry setConjugate.Conjugatetemperature; the solid's U / p are inert (no-slip walls,
        # pressure pinned on its interface patch); a volumetric heat source comes through mmsSource's T column.
        self._cht_regions_path = self.params.fvschemes.get("chtRegions", None)
        if self._cht_regions_path and not os.path.isabs(self._cht_regions_path):
            self._cht_regions_path = os.path.join(getattr(self.params, "dataset_dir", "."), self._cht_regions_path)
        self._cht_solid_cell = None
        self.alpha_material_face = None
        self._buoyancy_cell = self._buoyancy_face = None

    def _register_region_physics(self, fv_graph):
        """Materials and heat sources of a multi-region case (mesh schema v2, docs/development/mesh-schema-v2.md;
        tables from FVdomain/region_physics.py). Every face diffuses with the fluid's alpha times the factor of its
        material -- the owner's; across an interior face between two materials the distance-weighted harmonic mean
        -- folded into alpha_coeff (so the conjugate interface, the Warp backend and every continuation that scales
        alpha_coeff see it); ``alpha_material_face`` keeps the factor for code that rewrites alpha uniformly. A source
        adds its rate x cell volume to heat_source_term. One-region graphs carry the neutral tables: nothing changes."""
        gi = fv_graph.graph_Index
        tm, ts = gi.theta_material, gi.theta_source
        self._region_material_id = self._region_material_kind = None
        if tm is None or (not bool((tm[:, 0] != 1).any()) and not bool((ts != 0).any())):
            return
        if self._cht_regions_path:
            raise ValueError("chtRegions npz and schema-v2 region physics both active; use one")
        dev = self.alpha_coeff.device
        mid = gi.material_id.to(dev).long()
        sid = gi.source_id.to(dev).long()
        factor = tm[:, 0:1].to(device=dev, dtype=self.alpha_coeff.dtype)
        cell_of_cpd = torch.cumsum(self.mask_interior_cell.long(), 0) - 1
        own = self.neighbor_cpd_cell[0].long()
        nb = self.neighbor_cpd_cell[1].long()
        f_own = factor[mid[cell_of_cpd[own]]]
        nb_interior = self.mask_interior_cell[nb].reshape(-1, 1)
        f_nb = torch.where(nb_interior, factor[mid[cell_of_cpd[nb].clamp_min(0)]], f_own)
        differ = f_nb != f_own
        if bool(differ.any()):
            d_own = (self.face_pos - self.cpd_cell_pos[own]).norm(dim=-1, keepdim=True).to(f_own.dtype)
            d_nb = (self.face_pos - self.cpd_cell_pos[nb]).norm(dim=-1, keepdim=True).to(f_own.dtype)
            f_own = torch.where(differ, (d_own + d_nb) / (d_own / f_own + d_nb / f_nb), f_own)
        self.alpha_material_face = f_own.detach()
        self.alpha_coeff = (self.alpha_coeff * self.alpha_material_face).detach()
        rate = ts.to(device=dev, dtype=self.heat_source_term.dtype)[sid]
        self.heat_source_term = (self.heat_source_term + rate * self.cell_volume).detach()
        self._cht_solid_cell = tm[:, 1].to(dev)[mid] == 1
        # for the region coarse correction (Pipeline/CoupledNewton/region_deflation.py): materials and their kinds;
        # the conductivity factors for the networks' region features (mesh_graph.cell_regions, mesh schema S6)
        self._region_material_id = mid
        self._region_material_kind = tm[:, 1].to(dev)
        self._region_material_factor = tm[:, 0].to(dev)
        # Buoyancy acts on the fluid only: a solid's momentum rows are inert
        # (no-slip all round, pressure pinned), and a body force gh beta grad(T) on them would drive a velocity inside
        # the solid wherever its temperature varies across gravity. The factor multiplies beta on the cell rows and
        # on the faces (a face belongs to its owner's region: the regions share no face, their interfaces are patches).
        fluid = (~self._cht_solid_cell).to(self.cell_volume.dtype).reshape(-1, 1)
        self._buoyancy_cell = fluid.detach()
        self._buoyancy_face = fluid[cell_of_cpd[own]].reshape(-1, 1).detach()
        print(f"[buoyantBoussinesqpicoext] region physics ACTIVE — {tm.shape[0]} materials, "
              f"{int(self._cht_solid_cell.sum())} solid cells of {mid.shape[0]}, {int((sid > 0).sum())} source cells")

    def register_properties(self, fv_graph, force_register=False):

        # allow registering geometry after initialization
        self.register_geometrics(fv_graph, force_register=force_register)

        # face_cv momentum-residual geometry is mesh-only; build eagerly at
        # (re-)registration so the compiled forward never hits a lazy branch.
        # Built unconditionally: _facecv_interior_ids is also what the cell
        # pressure term indexes with.
        self._build_facecv_geom()

        graph_Index = fv_graph.graph_Index

        # Extract PDE coefficients for interior cells only， 这里由于每种solver，会导致不同的theta_PDE的维度，其需要与src/FVdomain/Load_mesh.py中的theta_PDE的定义对应
        self.theta_PDE = graph_Index.theta_PDE
        self.unsteady_coeff = graph_Index.theta_PDE[self.batch_cell, 0:1]
        self.continuity_eq_coeff = graph_Index.theta_PDE[:, 1:2] # [num_graphs, 1]
        self.convection_coeff = graph_Index.theta_PDE[self.batch_face, 2:3]
        self.grad_p_coeff = graph_Index.theta_PDE[self.batch_face, 3:4]
        self.nu_coeff = graph_Index.theta_PDE[self.batch_face, 4:5]
        self.source_term = (graph_Index.theta_PDE[self.batch_cell, 5:6] * self.cell_volume).detach()
        self.dt_cell = graph_Index.theta_PDE[self.batch_cell, 6:7]

        # Boussinesq-specific coefficients
        self.alpha_coeff = graph_Index.theta_PDE[self.batch_face, 7:8]  # Thermal diffusivity
        self.heat_source_term = (graph_Index.theta_PDE[self.batch_cell, 8:9] * self.cell_volume).detach()  # Heat source term
        if self._mms_source_path:
            # Manufactured per-cell sources (Sx,Sy,Sz,ST); the residuals
            # subtract source_term / heat_source_term, so these must equal the
            # continuous operators applied to the manufactured fields (see
            # experiments/MMS_SchemeAccuracy/mms_lib/mms_fields.py).
            import numpy as _np
            _mms = torch.as_tensor(
                _np.load(self._mms_source_path)["source"],
                dtype=self.cell_volume.dtype, device=self.cell_volume.device,
            )
            _need_cols = 4 + self._turb_width
            if (_mms.shape[0] != self.cell_volume.shape[0]
                    or _mms.shape[1] < _need_cols):
                raise ValueError(
                    f"mmsSource shape {tuple(_mms.shape)} incompatible with "
                    f"{self.cell_volume.shape[0]} interior cells (needs "
                    f"[N_cell, {_need_cols}] = Sx,Sy,Sz,ST"
                    + "".join(f",S_{e[0]}" for e in self._turb_transported)
                    + "; single-case runs only)")
            self.source_term = (
                self.source_term.expand(-1, 3) + _mms[:, 0:3] * self.cell_volume
            ).detach()
            self.heat_source_term = (
                self.heat_source_term + _mms[:, 3:4] * self.cell_volume
            ).detach()
            if self._turb_width:
                # Manufactured per-cell sources for the transported rows, in
                # TRANSPORTED order after ST; consumed (volume-integrated,
                # detached) by _assemble_transported_residuals.
                self._mms_turb_source = (
                    _mms[:, 4:4 + self._turb_width] * self.cell_volume
                ).detach()
        if self._cht_regions_path:
            import numpy as _np
            _blob = _np.load(self._cht_regions_path)
            solid = torch.as_tensor(_blob["solid"].astype(bool), device=self.cell_volume.device)
            if solid.shape[0] != self.cell_volume.shape[0]:
                raise ValueError(f"chtRegions: {solid.shape[0]} cells, the case has {self.cell_volume.shape[0]}")
            ratio = float(_blob["alpha_ratio"])
            owner = self.neighbor_cpd_cell[0].long()           # every face's owner is an interior cell
            face_solid = solid[owner].reshape(-1, 1)
            self.alpha_coeff = torch.where(face_solid, self.alpha_coeff * ratio, self.alpha_coeff).detach()
            self._cht_solid_cell = solid
            self._cht_alpha_ratio = ratio                    # mesh_graph.cell_regions (S6)
            fluid = (~solid).to(self.cell_volume.dtype).reshape(-1, 1)       # buoyancy on the fluid only, as above
            self._buoyancy_cell = fluid.detach()
            self._buoyancy_face = fluid[owner].reshape(-1, 1).detach()
            print(f"[buoyantBoussinesqpicoext] chtRegions ACTIVE — {int(solid.sum())} solid cells of "
                  f"{solid.shape[0]}, k_solid / k_fluid = {ratio:g}, {int(face_solid.sum())} solid faces")
        self._register_region_physics(fv_graph)
        self.TRef = graph_Index.theta_PDE[:, 9:10]  # Reference temperature
        self.beta = graph_Index.theta_PDE[:, 10:11]  # Thermal expansion coefficient
        self.gravity = graph_Index.theta_PDE[:, 11:14]  # Gravity vector [gx, gy, gz]
        self.hRef = graph_Index.theta_PDE[:, 14:15]  # Reference height for gh calculation
        
        # reference pressure cell and value
        self.pRefCell_valid_mask = graph_Index.pRefCell_valid_mask
        self.pRefCell = graph_Index.pRefCell[self.pRefCell_valid_mask]
        self.pRefValue = graph_Index.pRefValue[self.pRefCell_valid_mask]

        self._old_field_channels_are_inert = self._decide_old_channels_are_inert()

    def _decide_old_channels_are_inert(self) -> bool:
        """Whether ``uvw_old`` and ``T_old`` reach no equation, read off the CASE.

        The WLSQ family this solver reconstructs and interpolates is nine wide
        -- ``uvw_new(3) + uvw_old(3) + p_rgh(1) + T_new(1) + T_old(1)`` -- and
        four of those nine are dead weight whenever the ddt term is annihilated.
        The old fields enter ``conserved_form`` at exactly two points, both
        inside ``time_scheme``: ``source_term_total`` and
        ``heat_source_term_total``, and both multiply by ``unsteady_coeff``.
        Their gradients, their face values and their face gradient fluxes are
        carried through ``gradient_reconstruction`` and both face interpolations
        and then read by nothing -- ``conserved_form`` takes ``0:1`` of the flux
        pair, ``0:3`` and ``7:8`` of the face gradient flux, and the BC modules
        WRITE the old columns (``setU.py:909``) without ever reading them.
        Measured on the heated Ri=0.5 cylinder: over all 46 forward
        passes of a 34-step Newton solve, channels 3,4,5 and 8 held exactly zero
        nonzero elements out of 172,640 cells in the cell values, the cell
        gradients, the face values, the face gradients and the face gradient
        fluxes -- max|.| = 0.000000e+00 at every stage and every step, against
        3.66e+00 / 7.84e+00 / 1.00e+00 on the live u / p_rgh / T channels.

        TWO independent case-file facts are required, and the caller supplies
        neither -- a paradigm that forgets a flag cannot reach the narrow path:

        * ``[fvSchemes] ddtSchemes = "steadyState"`` is the case DECLARING that
          it has no time derivative.
        * ``[theta_pde] unsteady = false`` puts a zero in ``theta_PDE[..., 0]``,
          and that zero is the MECHANISM: it is the factor multiplying both
          old-field terms in ``time_scheme``, so with it identically zero the
          old columns cannot influence any residual row whatever they contain.

        Requiring both means an inconsistent case (steadyState declared beside
        ``unsteady = true``, or a marching caller feeding real old fields under
        a steadyState label) falls back to the nine-wide path rather than
        silently zeroing a term that is live.  GNN+FVM and ODIL run unsteady
        cases and take the wide path unchanged.
        """
        try:
            scheme = parse_ddt_scheme(self.params.fvschemes["ddtSchemes"])
        except (KeyError, ValueError, TypeError):
            return False
        if not scheme.is_steady_state:
            return False
        return bool((self.unsteady_coeff == 0).all())

    # The nine-wide WLSQ family and the five columns that survive when the old
    # fields are inert.  ``uvw_new`` is 0:3, ``uvw_old`` 3:6, ``p_rgh`` 6,
    # ``T_new`` 7, ``T_old`` 8; the live set is therefore two contiguous runs,
    # which is why narrowing and widening are each one ``cat`` of slices rather
    # than a gather.
    _WLSQ_WIDTH = 9
    _WLSQ_LIVE_RUNS = ((0, 3), (6, 8))

    def _narrow_inert_columns(self, family: torch.Tensor, dim: int) -> torch.Tensor:
        """The live columns of the WLSQ family, in family order.

        Instance runs rather than the class constants since phase 5b: a
        transported turbulence model appends its live run (9, 9+W); with
        W = 0 the instance attributes equal the class constants and this is
        the pre-5b five-of-nine narrowing unchanged.
        """
        return torch.cat(
            [family.narrow(dim, lo, hi - lo)
             for lo, hi in self._wlsq_live_runs], dim=dim)

    def _widen_inert_columns(self, narrow: torch.Tensor, dim: int) -> torch.Tensor:
        """Put the inert columns back as zeros, restoring family order.

        Zeros rather than the old fields themselves: this widens a GRADIENT or a
        FACE quantity, never a cell value, and the old fields' gradients and face
        values are read by nothing (see ``_decide_old_channels_are_inert``).  The
        cell family ``uvwpt_hat_cpd_cell`` is left nine-wide throughout, because
        ``time_scheme`` does read its old columns.
        """
        pieces, wide_at, narrow_at = [], 0, 0
        for lo, hi in self._wlsq_live_runs:
            if lo > wide_at:
                pieces.append(torch.zeros_like(narrow.narrow(dim, 0, lo - wide_at)))
            pieces.append(narrow.narrow(dim, narrow_at, hi - lo))
            wide_at, narrow_at = hi, narrow_at + hi - lo
        if wide_at < self._wlsq_width:
            pieces.append(torch.zeros_like(
                narrow.narrow(dim, 0, self._wlsq_width - wide_at)))
        return torch.cat(pieces, dim=dim)

    def enforce_bc_1st(self, fvfield: dict) -> dict:
        """
        Apply first-type (Dirichlet) boundary conditions.

        Args:
            fvfield: Dictionary containing field variables (uvw_cpd_cell, p_rgh_cpd_cell, t_cpd_cell)

        Returns:
            Updated fvfield with first-type boundary conditions applied
        """
        for bcpatch_i, bcpatch in enumerate(self.bcpatch_batch.first):
            for bc_func in bcpatch:
                fvfield = bc_func.eval(fvfield, self, patch_idx_offset=self.bcpatch_batch.first_patch_idx_offset[bcpatch_i])


        fvfield = self.enforce_empty(fvfield)
        return fvfield

    def enforce_cyclic(self, fvfield: dict) -> dict:
        """
        Apply cyclic (periodic) boundary conditions.

        Args:
            fvfield: Dictionary containing field variables

        Returns:
            Updated fvfield with cyclic boundary conditions applied
        """

        for bcpatch_i, bcpatch in enumerate(self.bcpatch_batch.cyclic):
            for bc_func in bcpatch:
                fvfield = bc_func.eval(fvfield, self, patch_idx_offset=self.bcpatch_batch.cyclic_patch_idx_offset[bcpatch_i])

        return fvfield

    def enforce_mixed(self, fvfield: dict) -> dict:
        """
        Apply mixed boundary conditions.

        Args:
            fvfield: Dictionary containing field variables

        Returns:
            Updated fvfield with mixed boundary conditions applied
        """

        for bcpatch_i, bcpatch in enumerate(self.bcpatch_batch.mixed):
            for bc_func in bcpatch:
                fvfield = bc_func.eval(fvfield, self, patch_idx_offset=self.bcpatch_batch.mixed_patch_idx_offset[bcpatch_i])

        return fvfield

    def enforce_empty(self, fvfield: dict) -> dict:
        """
        Apply empty boundary conditions (for 2D simulations in 3D framework).

        Empty boundary condition copies interior cell values to ghost cells,
        ensuring proper GNN message passing without contributing to flux calculations.

        Args:
            fvfield: Dictionary containing field variables

        Returns:
            Updated fvfield with empty BC applied
        """
        for bcpatch_i, bcpatch in enumerate(self.bcpatch_batch.empty):
            for bc_func in bcpatch:
                fvfield = bc_func.eval(fvfield, self, patch_idx_offset=self.bcpatch_batch.empty_patch_idx_offset[bcpatch_i])

        return fvfield

    def loss_setp_rghRef(self, p_rgh_cpd_cell: torch.Tensor) -> torch.Tensor:
        """
        Volume-mean p_rgh gauge for ODIL+Adam.

        Constrains: mean(p_rgh over interior cells) = pRefValue
        with volume weighting: `Σ(p_rgh · V) / Σ(V) == pRefValue`.

        **Not the OpenFOAM `p = p_rgh + rhok·gh` gauge.** OpenFOAM's matrix
        solver can broadcast a single-point `p[pRefCell] = pRefValue` pin to
        the whole field via linear iteration; ODIL+Adam's backprop only
        reaches the stencil neighbourhood of the pin, producing a localised
        |U| spike around the pin cell rather than a global DC fix. Volume-
        mean is the only gauge consistent with Adam's null-space semantics.

        Since `rhok` and `gh` have zero volume-mean contribution at steady
        state (the buoyancy force `gh·β·∇T` is already balanced by `∇p_rgh`
        in the momentum equation), constraining `mean(p_rgh)` instead of
        `mean(p_rgh + rhok·gh)` differs by a known uniform constant and does
        not affect the gradient null space Adam actually sees.

        Args:
            p_rgh_cpd_cell: Modified pressure p_rgh ``[..., N_cpd_cells, 1]``.

        Returns:
            Pressure-reference residual ``[..., num_graphs, 1]``.
        """
        eps_sqrt = 1e-16
        device = p_rgh_cpd_cell.device

        leading_shape = p_rgh_cpd_cell.shape[:-2]
        loss_p_rghRef = p_rgh_cpd_cell.new_zeros(
            (*leading_shape, self.num_graphs, 1)
        )

        if self.pRefCell.numel() == 0:
            return loss_p_rghRef

        # Volume-mean gauge: constrain mean(p_rgh) over interior cells to pRefValue.
        # Single-point pin (OpenFOAM style) does NOT work for ODIL+Adam: each
        # backprop only propagates the constraint to the pin cell's stencil
        # neighbourhood (O(khops) radius). OpenFOAM's linear solver broadcasts
        # the single-cell constraint to the whole field via matrix iteration;
        # Adam does not have that mechanism, so single-point pin creates a
        # local |U| spike around pRefCell without pinning the global DC.
        #
        # Volume-mean is the only gauge consistent with how Adam sees the
        # pressure null-space.
        p_total_interior = p_rgh_cpd_cell.index_select(
            -2, self._interior_cell_indices
        )[..., 0:1]
        cell_volume = self.cell_volume
        sum_p_w = scatter(p_total_interior * cell_volume, self.batch_cell,
                          dim=-2, dim_size=self.num_graphs, reduce='sum')
        sum_w = scatter(cell_volume, self.batch_cell,
                        dim=-2, dim_size=self.num_graphs, reduce='sum')
        mean_p = sum_p_w / (sum_w + eps_sqrt)                              # [num_graphs, 1]

        # pRefCell indexes the COMPOUND cells (Graph_data.py offsets it by
        # _num_cpd_cells), so it must be read through batch_cpd_cell.
        # batch_cell is the interior-only subset and is shorter, which at
        # batch_size>1 either lands on the wrong graph or goes out of bounds
        # (measured: IndexError, index 197632 into size 81920).
        batch_pRef = self.batch_cpd_cell[self.pRefCell]
        pRefValue_expanded = p_rgh_cpd_cell.new_zeros((self.num_graphs, 1))
        pRefValue_expanded[batch_pRef] = self.pRefValue.view(-1, 1).to(device)

        p_Ref_error_sq = (mean_p - pRefValue_expanded) ** 2
        valid_graph_mask = torch.zeros(self.num_graphs, dtype=torch.bool, device=device)
        valid_graph_mask[batch_pRef] = True
        # Keep the zero-gauge state differentiable.  sqrt(x^2) has an
        # undefined derivative at x=0; multiplying that NaN by a disabled
        # graph mask or zero loss weight does not remove it in autograd.
        loss_p_rghRef = torch.sqrt(
            p_Ref_error_sq + eps_sqrt
        ) * valid_graph_mask.view(-1, 1).float()
        return loss_p_rghRef

    def time_scheme(self, uvw_old_cell: torch.Tensor, uvw_new_cell: torch.Tensor,
                    t_old_cell: torch.Tensor, t_new_cell: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Apply temporal discretization scheme for time integration.

        Computes the unsteady terms for both momentum and energy equations.

        Args:
            uvw_old_cell: Previous velocity ``[..., N_cpd_cells, 3]``.
            uvw_new_cell: Current velocity ``[..., N_cpd_cells, 3]``.
            t_old_cell: Previous temperature ``[..., N_cpd_cells, 1]``.
            t_new_cell: Current temperature ``[..., N_cpd_cells, 1]``.

        Returns:
            Tuple containing:
                - unsteady_uvw_cell: Unsteady term for momentum equation [N_interior_cells, 3].
                - unsteady_t_cell: Unsteady term for energy equation [N_interior_cells, 1].
                - source_term_total: Total source term including old time step contribution [N_interior_cells, 3].
                - heat_source_term_total: Total heat source term including old time step contribution [N_interior_cells, 1].
        """
        # Compute unsteady term for momentum: ∂u/∂t * V * coefficient
        unsteady_uvw_cell = (
            (uvw_new_cell.index_select(-2, self._interior_cell_indices)[..., 0:3] / self.dt_cell)
        ) * self.cell_volume * self.unsteady_coeff

        # Compute source term with old time step contribution (no accumulation!)
        # This is computed fresh each forward() call
        # Note: self.source_term is [num_cells, 1], need to expand to [num_cells, 3] for momentum
        source_term_3d = self.source_term.expand(-1, 3)  # Expand to 3D for momentum equation
        source_term_total = source_term_3d + (
            (uvw_old_cell.index_select(-2, self._interior_cell_indices)[..., 0:3] / self.dt_cell)
        ) * self.cell_volume * self.unsteady_coeff

        # Compute unsteady term for temperature: ∂T/∂t * V * coefficient
        unsteady_t_cell = (
            (t_new_cell.index_select(-2, self._interior_cell_indices)[..., 0:1] / self.dt_cell)
        ) * self.cell_volume * self.unsteady_coeff

        # Compute heat source term with old time step contribution (no accumulation!)
        heat_source_term_total = self.heat_source_term + (
            (t_old_cell.index_select(-2, self._interior_cell_indices)[..., 0:1] / self.dt_cell)
        ) * self.cell_volume * self.unsteady_coeff

        return unsteady_uvw_cell, unsteady_t_cell, source_term_total, heat_source_term_total

    def gh_face(self) -> torch.Tensor:
        """``ghf = g . Cf - ghRef``, one per FACE, exactly as OpenFOAM builds it.

        ``src/finiteVolume/cfdTools/general/include/gh.H`` writes::

            ghRef = (mag(g) > SMALL) ? g & (cmptMag(g)/mag(g))*hRef : 0
            gh    = (g & mesh.C())  - ghRef
            ghf   = (g & mesh.Cf()) - ghRef

        i.e. the face value is evaluated AT THE FACE CENTRE, not interpolated
        from the two cells, and the reference is the same constant for both.
        Both properties are load-bearing here: everything below pairs ``ghf``
        with a compact face operator, and the constant is what a change of
        ``hRef`` moves.

        **This does not reproduce OpenFOAM's SIGN of ``ghRef``, and the
        difference is deliberate rather than overlooked.**  ``g & cmptMag(g)/
        mag(g)`` evaluates to ``-|g|`` for gravity pointing down a coordinate
        axis, so OpenFOAM's ``ghRef`` is ``-|g| hRef`` where the line below
        computes ``+|g| hRef``; the case's ``hRef`` therefore has the opposite
        sense to OpenFOAM's.  It is left alone because ``ghRef`` enters every
        expression in this file as an additive constant on ``gh``, so flipping
        it renames ``hRef = -0.1`` to ``hRef = +0.1`` and changes no equation --
        and because changing it would silently redefine the one knob the gauge
        measurement below is expressed in.  Recorded here so the next reader
        does not "fix" it into a discrepancy with the case files.
        """
        g_face = self.gravity[self.batch_face]                    # [N_face, 3]
        ghRef = (torch.norm(g_face, dim=-1, keepdim=True)
                 * self.hRef[self.batch_face])                    # [N_face, 1]
        return (self.face_pos * g_face).sum(dim=-1, keepdim=True) - ghRef

    def _beta_cell(self) -> torch.Tensor:
        """beta on the interior cells [N_cell, 1]; zero in the solid materials of a multi-region case
        (``_buoyancy_cell``), the plain per-graph value otherwise (the single-region tensor, bit for bit)."""
        b = self.beta[self.batch_cell]
        m = getattr(self, "_buoyancy_cell", None)
        return b if m is None else b * m

    def _beta_face(self) -> torch.Tensor:
        """beta on the faces [N_face, 1], zero on the faces of solid materials (see ``_beta_cell``)."""
        b = self.beta[self.batch_face]
        m = getattr(self, "_buoyancy_face", None)
        return b if m is None else b * m

    def _compute_buoyancy_source(self, grad_T_cell: torch.Tensor) -> torch.Tensor:
        """Boussinesq body force on the cell rows, volume-integrated: gh beta grad(T) V
        with gh = g . x_c - |g| hRef at the cell centre and grad(T) the unified WLSQ
        gradient at interior cells -- the same operator as the pressure term, so a
        shift of hRef is absorbed by p_rgh -> p_rgh - c rhok entry by entry
        (the LDC-validated form)."""
        beta_cell = self._beta_cell()
        g_cell = self.gravity[self.batch_cell]
        ghRef_cell = torch.norm(g_cell, dim=1, keepdim=True) * self.hRef[self.batch_cell]
        cell_pos_interior = self.cpd_cell_pos.index_select(-2, self._facecv_interior_ids)
        gh_cell = (cell_pos_interior * g_cell).sum(dim=1, keepdim=True) - ghRef_cell
        return gh_cell * beta_cell * grad_T_cell * self.cell_volume

    def _bc_diffusion_face_mask(self) -> torch.Tensor:
        """Boolean mask [N_face, 1] of faces where the compact snGrad diffusion
        replaces the WLSQ face gradient.

        boundaryDiffusionScheme == "sngrad": non-cyclic boundary faces
        (inlet/outlet/wall/symmetry), excluding empty faces (2D extrusion; they
        are dropped from the residual scatter anyway).
        cyclicDiffusionScheme == "sngrad": cyclic faces too — OpenFOAM's
        coupled-patch compact snGrad. phi_F on a cyclic face is the paired
        patch's owner value (attached copy), so the compact term reads the seam
        jump directly and couples the two seam owners in backprop.
        """
        m = None
        if self._diffusion_sngrad_face:
            m = self.mask_boundary_non_cyclic_face
            if getattr(self, "mask_empty_face", None) is not None:
                m = m & (~self.mask_empty_face)
        if self._cyclic_diffusion_sngrad:
            cyc = self.mask_boundary_face & (~self.mask_boundary_non_cyclic_face)
            m = cyc if m is None else (m | cyc)
        return m.view(-1, 1)

    def _derived_cpd_gradient(self, phi_cpd_cell: torch.Tensor) -> torch.Tensor:
        """One extra WLSQ (or GG) pass over DERIVED cell fields (phase 3b).

        Same dispatch as picoext._derived_cpd_gradient: mirrors the primary
        unified-gradient call -- order, stencil, moments, interior-only mask
        -- WITHOUT the optional cell limiter (no derived operand is
        advected).  Runs ONLY when the active turbulence model returns
        operands from ``derived_gradient_operands``.
        """
        _grad_order = self.params.fvschemes["gradSchemes"]
        if _grad_order == "GG":
            return self._green_gauss_cpd_gradient(phi_cpd_cell)[..., 0:3]
        return gradient_reconstruction(
            order=_grad_order,
            phi_node=phi_cpd_cell,
            edge_index=self.neighbor_cpd_cell_x,
            mask_valid_node=self.mask_interior_cell,
            node_pos=self.cpd_cell_pos,
            precompute_Moments=[self.A_cell_to_cell, self.single_B_cell_to_cell],
            dim=self.dim,
        )[..., 0:3]

    def _assemble_transported_residuals(
        self,
        uvwpt_hat_cpd_cell: torch.Tensor,
        uvw_flux_face: torch.Tensor,
        grad_uvwpt_hat_cpd_cell: torch.Tensor,
        grad_uvwpt_hat_face: torch.Tensor,
        grad_uvwpt_flux_hat_face: torch.Tensor,
        sngrad_corr_grad_uvwpt_hat_cpd_cell: torch.Tensor,
        eps_sqrt: float,
    ) -> torch.Tensor:
        """Generic scalar-transport assembly for the turbulence model's
        TRANSPORTED fields -- the picoext phase-3 assembly with this
        solver's packed layout (phase 5b): cell tensor carries turb_new at
        9:9+W and turb_old at 9+W:9+2W; the gradient family and the
        face-gradient-flux tensor carry the turb columns at 9:9+W (after
        the nine-wide laminar family).

        Beyond picoext, the closure state carries the BUOYANT operands Gb
        needs (design A.2): ``grad_t_int`` (the T column of the same
        unified WLSQ gradient every consumer shares), ``gravity_int`` /
        ``beta_int`` (per-cell case constants), and ``uvw_int`` (for
        C3_eff = tanh(|v|/u)).  An isothermal host never supplies these, so
        the models' Gb term is structurally tied to this solver.

        Sets ``self.<residual_attr>`` per TRANSPORTED entry and returns the
        per-graph per-channel L2 norms ``[.., num_graphs, W]`` for the loss.
        """
        W = self._turb_width
        base = self._WLSQ_WIDTH  # 9: turb channels sit after the laminar family
        mask_int = self.mask_interior_cell

        turb_new_int = uvwpt_hat_cpd_cell[..., mask_int, base:base + W]
        turb_old_int = uvwpt_hat_cpd_cell[..., mask_int,
                                          base + W:base + 2 * W]
        unsteady_turb = ((turb_new_int - turb_old_int) / self.dt_cell
                         * self.cell_volume * self.unsteady_coeff)

        closure_state = {
            "grad_u_int": grad_uvwpt_hat_cpd_cell[..., mask_int, 0:3, :],
            "nu_cell_int": self.theta_PDE[self.batch_cell, 4:5],
            # cpd velocity (view, no copy): a wall-function closure needs the
            # wall-face snGrad of U, which is a ghost-minus-owner difference and
            # therefore not carveable from any interior tensor here.  Same key
            # picoext supplies -- the second assemble function this package
            # carries, and the one a turbulent buoyant case would silently
            # diverge on.
            "u_cpd": uvwpt_hat_cpd_cell[..., 0:3],
            # Buoyant operands for Gb (design A.2), handed to the closures
            # the way grad U reaches them -- through the state dict.
            "grad_t_int": grad_uvwpt_hat_cpd_cell[..., mask_int, 7:8, :],
            "gravity_int": self.gravity[self.batch_cell],
            "beta_int": self._beta_cell(),
            "uvw_int": uvwpt_hat_cpd_cell[..., mask_int, 0:3],
        }
        _off = 0
        for _fname, _w, *_rest in self._turb_transported:
            closure_state[f"{_fname}_int"] = turb_new_int[..., _off:_off + _w]
            closure_state[f"grad_{_fname}_int"] = (
                grad_uvwpt_hat_cpd_cell[..., mask_int,
                                        base + _off:base + _off + _w, :])
            _off += _w

        # Derived-gradient pass (phase 3b; Launder-Sharma's D and E terms).
        operand_state = {
            "grad_u_cpd": grad_uvwpt_hat_cpd_cell[..., 0:3, :],
        }
        _off = 0
        for _fname, _w, *_rest in self._turb_transported:
            operand_state[f"{_fname}_cpd"] = (
                uvwpt_hat_cpd_cell[..., base + _off:base + _off + _w])
            _off += _w
        operands = self.turbulence.derived_gradient_operands(
            self, operand_state)
        if operands:
            packed = torch.cat(list(operands.values()), dim=-1)
            grad_packed = self._derived_cpd_gradient(packed)
            _c = 0
            for _name, _t in operands.items():
                _w = _t.shape[-1]
                closure_state[f"grad_{_name}_int"] = (
                    grad_packed[..., mask_int, _c:_c + _w, :])
                _c += _w

        closures = self.turbulence.transport_closures(self, closure_state)

        bc_face_mask = (self._bc_diffusion_face_mask()
                        if self._diffusion_sngrad_face else None)

        loss_cols = []
        _off = 0
        for _fname, _w, _bc_attr, _res_attr in self._turb_transported:
            sl_cell = slice(base + _off, base + _off + _w)
            conv_flux_face, bounded_source = self.scalar_convective_flux(
                uvw_flux_face=uvw_flux_face,
                phi_cpd_cell=uvwpt_hat_cpd_cell[..., sl_cell],
                grad_phi_cpd_cell=grad_uvwpt_hat_cpd_cell[..., sl_cell, :],
                grad_phi_face=grad_uvwpt_hat_face[..., sl_cell, :],
                scheme=self._turb_schemes[_fname],
            )
            grad_flux_face = grad_uvwpt_flux_hat_face[..., base + _off:
                                                      base + _off + _w]
            if bc_face_mask is not None:
                snGrad_face = self.compute_snGrad(
                    phi_cpd_cell=uvwpt_hat_cpd_cell[..., sl_cell],
                    grad_phi_cpd_cell=(
                        sngrad_corr_grad_uvwpt_hat_cpd_cell[..., sl_cell, :]),
                    scheme=self._snGrad_scheme,
                    var_type=VAR_TYPE_BY_FIELD.get(_fname),
                )
                grad_flux_face = torch.where(
                    bc_face_mask, snGrad_face, grad_flux_face)
            closure = closures[_fname]
            diff_flux_face = self.diffusion_flux(
                grad_uvw_flux_face=grad_flux_face,
                diffusivity=closure["diffusivity_face"],
            )
            flux_cells_face = (conv_flux_face - diff_flux_face).index_select(
                -2, self.cells_face)
            rhs = scatter(
                (flux_cells_face * self.directed_area_cells_face
                 ).index_select(-2, self._non_empty_cells_face_indices),
                self.cells_face_ptr.index_select(
                    0, self._non_empty_cells_face_indices),
                dim=-2, dim_size=self.num_cells, reduce='sum',
            )
            bounded_int = (
                bounded_source.index_select(-2, self._interior_cell_indices)
                if bounded_source is not None else 0.0)
            residual = (unsteady_turb[..., _off:_off + _w] + rhs + bounded_int
                        - closure["source_cell"] * self.cell_volume)
            if self._mms_turb_source is not None:
                residual = residual - self._mms_turb_source[..., _off:_off + _w]
            # Wall-adjacent rows a BC constrains (the low-Re omega wall) are
            # replaced by that constraint, exactly as in picoext -- ONE
            # implementation, on BaseSolver.  Applied after the MMS source so a
            # manufactured run cannot inject a term into a row that carries no
            # transport equation.
            residual = self._apply_cell_constraints(
                residual, _fname, uvwpt_hat_cpd_cell[..., sl_cell])
            setattr(self, _res_attr, residual)
            loss_cols.append(torch.sqrt(
                global_add_pool(residual**2, batch=self.batch_cell,
                                size=self.num_graphs) + eps_sqrt))
            _off += _w

        return torch.cat(loss_cols, dim=-1)

    def conserved_form(
        self,
        uvwpt_hat_cpd_cell: torch.Tensor,
        uvwpt_hat_face: torch.Tensor,
        uvwpt_flux_hat_face: torch.Tensor,
        grad_uvwpt_hat_cpd_cell: torch.Tensor,
        grad_uvwpt_hat_face: torch.Tensor,
        grad_uvwpt_flux_hat_face: torch.Tensor,
        recon_grad_uvwpt_hat_cpd_cell: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Evaluate the conserved form of the Boussinesq equations.

        Computes the residuals for continuity, momentum, and energy equations.
        Includes buoyancy coupling term in momentum equation.

        Args:
            uvwpt_hat_cpd_cell: Interpolated values at cell centers [N_cpd_cells, 9].
                                (uvw_new[0:3], uvw_old[3:6], p_rgh[6:7], T_new[7:8], T_old[8:9])
            uvwpt_hat_face: Interpolated values at face centers [N_faces, 9].
            uvwpt_flux_hat_face: Flux values at face centers [N_faces, 8]
                                (uvw_new_flux[0:1], uvw_old_flux[1:2], p_rgh_flux[2:5], t_new_flux[5:8]).
            grad_uvwpt_hat_cpd_cell: Gradients at cell centers [N_cpd_cells, 9, 3].
            grad_uvwpt_hat_face: Gradients at face centers [N_faces, 9, 3].
            grad_uvwpt_flux_hat_face: Gradient fluxes at face centers [N_faces, 9].

        Returns:
            Per-graph combined loss. Rank-2 fields return ``[B]`` and
            rank-3 time windows return ``[T,B]``; neither axis is reduced.
        """

        # recon_grad_uvwpt_hat_cpd_cell IS
        # grad_uvwpt_hat_cpd_cell (the second, compact 1-hop
        # "leastSquares"-token gradient was removed), so all three selections
        # below resolve to the same unified WLSQ gradient. The per-consumer
        # branches are kept only so the tokens stay config-compatible.
        if recon_grad_uvwpt_hat_cpd_cell is None:
            recon_grad_uvwpt_hat_cpd_cell = grad_uvwpt_hat_cpd_cell
        conv_grad_U_hat_cpd_cell = (
            recon_grad_uvwpt_hat_cpd_cell if self._recon_compact_U
            else grad_uvwpt_hat_cpd_cell
        )
        conv_grad_T_hat_cpd_cell = (
            recon_grad_uvwpt_hat_cpd_cell if self._recon_compact_T
            else grad_uvwpt_hat_cpd_cell
        )
        sngrad_corr_grad_uvwpt_hat_cpd_cell = (
            recon_grad_uvwpt_hat_cpd_cell if self._sngrad_corr_compact
            else grad_uvwpt_hat_cpd_cell
        )

        ''' >>> Apply temporal discretization >>> '''
        # Extract velocity and temperature from uvwpt_hat_cpd_cell
        # uvwpt_hat_cpd_cell: [uvw_new(0:3), uvw_old(3:6), p_rgh(6:7), T_new(7:8), T_old(8:9)]
        unsteady_uvw_cell, unsteady_t_cell, source_term_total, heat_source_term_total = self.time_scheme(
            uvw_old_cell=uvwpt_hat_cpd_cell[..., 3:6],
            uvw_new_cell=uvwpt_hat_cpd_cell[..., 0:3],
            t_old_cell=uvwpt_hat_cpd_cell[..., 8:9],
            t_new_cell=uvwpt_hat_cpd_cell[..., 7:8],
        )
        ''' <<< Apply temporal discretization <<< '''

        ''' >>> Compute mass flux for continuity equation >>> '''
        # continuity_flux expects uvw_flux_face (scalar flux), which is uvwpt_flux_hat_face[:,0:1]
        mass_flux_cells_face = self.continuity_flux(
            uvw_flux_face=uvwpt_flux_hat_face[..., 0:1]
        )
        ''' <<< Compute mass flux for continuity equation <<< '''

        ''' >>> Compute momentum convection and diffusion fluxes >>> '''
        # Every spatial flux is evaluated at the NEW time level, unconditionally. The
        # time scheme is not this solver's business: the caller supplies an EFFECTIVE
        # old state chosen so that (phi_new - phi_old_eff)/dt is the derivative that
        # fvSchemes.ddtSchemes asks for. See FVdiscretization/ddt_schemes.py. OpenFOAM
        # factors it the same way, which is why its fvm::div(phi,U) is unconditionally
        # implicit while ddtSchemes still selects Euler, CrankNicolson or backward.
        #
        # This replaced an "implicit"/"imex" branch that averaged ONLY the convection
        # flux over old and new while diffusion, pressure and continuity stayed at the
        # new time -- second order in one term and first order in the others, which is
        # not a scheme. Diffusion and pressure were never inside the branch.
        #
        # The advecting flux is the time integrator's choice
        # (BaseSolver.set_convection_treatment): the state's own flux (implicit,
        # the default -- the very tensor this call always received), none
        # (explicit, IMEX: N(u^n) arrives through set_explicit_source), or a fixed
        # F* (linearised, Oseen).  The ENERGY row below follows the same switch,
        # because div(u T) and div(u u) share the advecting flux; continuity keeps
        # the state's own flux in every mode.
        advecting_flux_face = self._convection_advecting_flux(uvwpt_flux_hat_face[..., 0:1])
        if advecting_flux_face is None:
            convection_flux_uvw_face, bounded_source_uvw = 0.0, None
        else:
            convection_flux_uvw_face, bounded_source_uvw = self.convective_flux(
                uvw_flux_face=advecting_flux_face,  # Scalar flux
                phi_cpd_cell=uvwpt_hat_cpd_cell[..., 0:3],  # [..., N_cpd_cell, 3]
                grad_phi_cpd_cell=conv_grad_U_hat_cpd_cell[..., 0:3, :],
                grad_phi_face=grad_uvwpt_hat_face[..., 0:3, :],
                scheme=self.U_scheme
            )
        capture = getattr(self, "_conv_capture", None)
        if capture is not None:
            # explicit_convection / mwi_face_flux (BaseSolver._capture_convection).
            capture["flux"] = uvwpt_flux_hat_face[..., 0:1].detach().clone()
            capture["U"] = self._convection_cell_rows(
                convection_flux_uvw_face, bounded_source_uvw)
        if self._conv_theta != 1.0:
            # Old-time convection, on the SAME operator and scheme, from the
            # old columns: uvw_old is 3:6 and the old face flux is 1:2.
            _c_old, _b_old = self.convective_flux(
                uvw_flux_face=uvwpt_flux_hat_face[..., 1:2],
                phi_cpd_cell=uvwpt_hat_cpd_cell[..., 3:6],
                grad_phi_cpd_cell=conv_grad_U_hat_cpd_cell[..., 3:6, :],
                grad_phi_face=grad_uvwpt_hat_face[..., 3:6, :],
                scheme=self.U_scheme
            )
            convection_flux_uvw_face, bounded_source_uvw = self.blend_convection_in_time(
                convection_flux_uvw_face, _c_old, bounded_source_uvw, _b_old)

        # Compact face snGrad(p_rgh), consumed by the face_cv rows below.
        snGrad_p_face = self.compute_snGrad(
            phi_cpd_cell=uvwpt_hat_cpd_cell[..., 6:7],
            grad_phi_cpd_cell=sngrad_corr_grad_uvwpt_hat_cpd_cell[..., 6:7, :],
            scheme=self._snGrad_scheme, var_type=VARType.PRESSURE_RGH,
        )  # [..., N_face, 1]

        # Pressure term follows momentumResidual: "cell" -> unified WLSQ
        # grad(p_rgh) at interior cells times V_cell, the
        # LDC-validated form (a Perot reconstruction of the face snGrad stood
        # here; removed after a code
        # comparison found it paired a Perot buoyancy with a WLSQ pressure in
        # one row); "face_cv" -> pressure enters at FACE level via the compact
        # snGrad in the loss block below, so residual_momentum stays
        # pressure-free.
        P_rgh_flux_face = 0.0
        if self._momentum_residual_cell:
            grad_p_rgh_cell = grad_uvwpt_hat_cpd_cell[..., 6, :].index_select(
                -2, self._facecv_interior_ids)
            P_rgh_grad_volume = (
                grad_p_rgh_cell
                * self.theta_PDE[self.batch_cell, 3:4]
                * self.cell_volume
            )
        else:
            P_rgh_grad_volume = 0.0

        # ext solver: interior faces use the WLSQ-interpolated face gradient.
        # With boundaryDiffusionScheme == "sngrad", physical boundary (non-cyclic)
        # faces instead use the compact OpenFOAM surface-normal gradient (computed
        # from the BC-enforced ghost cells), while interior faces keep WLSQ.
        grad_uvw_face = grad_uvwpt_flux_hat_face[..., 0:3]
        if self._diffusion_sngrad_face:
            bc_face_mask = self._bc_diffusion_face_mask()  # [N_face, 1]
            snGrad_uvw_face = self.compute_snGrad(
                phi_cpd_cell=uvwpt_hat_cpd_cell[..., 0:3],
                grad_phi_cpd_cell=sngrad_corr_grad_uvwpt_hat_cpd_cell[..., 0:3, :],
                scheme=self._snGrad_scheme, var_type=VARType.VELOCITY,
            )  # [N_face, 3]
            grad_uvw_face = torch.where(bc_face_mask, snGrad_uvw_face, grad_uvw_face)
        vis_flux_uvw_face = self.diffusion_flux(
            grad_uvw_flux_face=grad_uvw_face,
            # The per-forward nu_eff stash; None (direct conserved_form calls
            # outside forward) falls back to nu_coeff inside.
            diffusivity=self._nu_eff_face,
        )
        if self._dev2_active:
            # div(nu_eff dev2((grad U)^T)) -- same term, gate, boundary
            # closure and sign as picoext (see the comment there; design
            # section 2.5): interpolated WLSQ face gradient
            # tensor on interior/cyclic faces, OWNER cell gradient on
            # physical boundary faces (the interpolated tensor there is the
            # ghost cell's zero WLSQ gradient), added into the viscous flux
            # that conserved_form subtracts from the momentum flux.
            dev2_grad_face = grad_uvwpt_hat_face[..., 0:3, :]
            dev2_bc_mask = self.mask_boundary_non_cyclic_face
            if getattr(self, "mask_empty_face", None) is not None:
                dev2_bc_mask = dev2_bc_mask & (~self.mask_empty_face)
            dev2_grad_face = torch.where(
                dev2_bc_mask.view(-1, 1, 1),
                grad_uvwpt_hat_cpd_cell[..., 0:3, :].index_select(
                    -3, self.C_senders),
                dev2_grad_face,
            )
            vis_flux_uvw_face = vis_flux_uvw_face + self.diffusion_flux(
                grad_uvw_flux_face=dev2_transpose_face_flux(
                    dev2_grad_face, self.unv_face),
                diffusivity=self._nu_eff_face,
            )
        ''' <<< Compute momentum convection and diffusion fluxes <<< '''

        ''' >>> Compute temperature convection and diffusion fluxes >>> '''
        # Compute convective flux for temperature using scalar_convective_flux
        # This method supports bounded scheme and returns (flux, bounded_source)
        # New time level only, as for momentum above, and carried by the SAME
        # advecting flux the momentum convection used (none when explicit).
        if advecting_flux_face is None:
            convection_flux_t_face, bounded_source_t = 0.0, None
        else:
            convection_flux_t_face, bounded_source_t = self.scalar_convective_flux(
                uvw_flux_face=advecting_flux_face,
                phi_cpd_cell=uvwpt_hat_cpd_cell[..., 7:8],  # Temperature (new)
                grad_phi_cpd_cell=conv_grad_T_hat_cpd_cell[..., 7:8, :],
                grad_phi_face=grad_uvwpt_hat_face[..., 7:8, :],
                scheme=self.T_scheme
            )
        if capture is not None:
            capture["T"] = self._convection_cell_rows(
                convection_flux_t_face, bounded_source_t)
        if self._conv_theta != 1.0:
            # div(u T) must use the SAME time weighting as div(u u): the two share
            # the advecting flux, so weighting one and not the other would make the
            # scheme non-conservative.  T_old is column 8:9.
            _ct_old, _bt_old = self.scalar_convective_flux(
                uvw_flux_face=uvwpt_flux_hat_face[..., 1:2],
                phi_cpd_cell=uvwpt_hat_cpd_cell[..., 8:9],
                grad_phi_cpd_cell=conv_grad_T_hat_cpd_cell[..., 8:9, :],
                grad_phi_face=grad_uvwpt_hat_face[..., 8:9, :],
                scheme=self.T_scheme
            )
            convection_flux_t_face, bounded_source_t = self.blend_convection_in_time(
                convection_flux_t_face, _ct_old, bounded_source_t, _bt_old)

        # Compute thermal diffusion flux: α∇²T (use new temperature gradient).
        # Interior faces use WLSQ; with boundaryDiffusionScheme == "sngrad" the
        # physical boundary (non-cyclic) faces use the compact snGrad instead.
        grad_t_face = grad_uvwpt_flux_hat_face[..., 7:8]
        if self._diffusion_sngrad_face:
            bc_face_mask = self._bc_diffusion_face_mask()  # [N_face, 1]
            snGrad_t_face = self.compute_snGrad(
                phi_cpd_cell=uvwpt_hat_cpd_cell[..., 7:8],
                grad_phi_cpd_cell=sngrad_corr_grad_uvwpt_hat_cpd_cell[..., 7:8, :],
                scheme=self._snGrad_scheme, var_type=VARType.TEMPERATURE,
            )  # [N_face, 1]
            grad_t_face = torch.where(bc_face_mask, snGrad_t_face, grad_t_face)
        # alphaEff = alpha + alphat, alphat = nut/Prt (phase 5b, design A.3;
        # TEqn.H:2/5/9-10).  The per-forward stash is alpha_coeff ITSELF on
        # the laminar path (same tensor object -- the structural
        # bit-identity convention _nu_eff_face set); None covers direct
        # conserved_form calls outside forward (probes), which see the
        # molecular alpha unchanged.
        _alpha_eff = (self._alpha_eff_face if self._alpha_eff_face is not None
                      else self.alpha_coeff)
        thermal_diff_flux_face = _alpha_eff * grad_t_face
        ''' <<< Compute temperature convection and diffusion fluxes <<< '''

        ''' >>> Compute buoyancy force as VOLUME source (OpenFOAM rhok formulation) >>> '''
        # gh beta grad(T) V on the same unified WLSQ gradient, indexed the same
        # way, as the cell pressure term above.
        buoyancy_volume_source = self._compute_buoyancy_source(
            grad_uvwpt_hat_cpd_cell[..., 7, :].index_select(
                -2, self._facecv_interior_ids))
        ''' <<< Compute buoyancy force as VOLUME source <<< '''

        ''' >>> Total flux assembly >>> '''
        if getattr(self, "_stash_face_fluxes", False):
            # Bounded-multiplier operator correction (opt-in; Pipeline.CoupledNewton.operator_correction
            # .FluxMultiplierCorrection): the convective + diffusive face fluxes per face (no pressure flux), kept as
            # references in the autograd graph.  Unset -- every other caller -- this block never runs.
            self.face_flux_mom = convection_flux_uvw_face - vis_flux_uvw_face
            self.face_flux_T = convection_flux_t_face - thermal_diff_flux_face
        # Following picoext.py pattern: use directed_area_cells_face instead of matmul
        J_flux_mom_cells_face = (
            convection_flux_uvw_face + P_rgh_flux_face - vis_flux_uvw_face
        ).index_select(-2, self.cells_face)
        J_flux_energy_cells_face = (
            convection_flux_t_face - thermal_diff_flux_face
        ).index_select(-2, self.cells_face)

        # Assemble total RHS by scattering face fluxes to cells
        # total_RHS shape: [N_cells, 5] where 5 = continuity(1) + momentum(3) + energy(1)
        total_RHS = scatter(
            (
                torch.cat(
                    (mass_flux_cells_face, J_flux_mom_cells_face, J_flux_energy_cells_face),
                    dim=-1,
                )
                * self.directed_area_cells_face
            ).index_select(-2, self._non_empty_cells_face_indices),
            self.cells_face_ptr.index_select(
                0, self._non_empty_cells_face_indices
            ),
            dim=-2,
            dim_size=self.num_cells,
            reduce='sum',
        )

        # Extract individual RHS components
        continuity_RHS = total_RHS[..., 0:1]
        momentum_RHS = total_RHS[..., 1:4]
        temperature_RHS = total_RHS[..., 4:5]

        # The time step's explicit source (BaseSolver.set_explicit_source, H2),
        # ADDED to the rows it covers: under IMEX it carries the extrapolated
        # convection of momentum AND energy.  None -- every caller that never set
        # one -- leaves each row the very tensor it was.
        explicit_U = self._explicit_source_rows("U")
        explicit_p_rgh = self._explicit_source_rows("p_rgh")
        explicit_T = self._explicit_source_rows("T")
        if explicit_p_rgh is not None:
            continuity_RHS = continuity_RHS + explicit_p_rgh

        # OpenFOAM-style equation scale (``lduMatrix::solver::normFactor``): the
        # sum of term MAGNITUDES rather than their largely cancelling signed sum.
        # Dividing a residual by it yields a dimensionless number that is
        # comparable across time steps and flow regimes, which is what makes an
        # absolute tolerance transferable.
        #
        # DELIBERATELY GATE-ONLY, AND DETACHED.  In OpenFOAM the normalised
        # residual is a convergence READOUT: PIMPLE's Picard iteration minimises
        # nothing, so the normalisation cannot distort an objective.  Here an
        # objective does exist, and dividing the optimised loss by this factor
        # both re-weights the equations dynamically and (if the denominator
        # carried gradient) would let the optimiser lower the loss by inflating
        # flux magnitudes.  Measured: doing so degraded field quality
        # across the board.  The scale is therefore computed under no_grad and
        # consumed only by the residualControl gate.
        with torch.no_grad():
            scale_faces = (
                torch.cat(
                    (mass_flux_cells_face, J_flux_mom_cells_face,
                     J_flux_energy_cells_face),
                    dim=-1,
                ) * self.directed_area_cells_face
            ).abs()
            total_RHS_scale = scatter(
                scale_faces.index_select(-2, self._non_empty_cells_face_indices),
                self.cells_face_ptr.index_select(
                    0, self._non_empty_cells_face_indices
                ),
                dim=-2,
                dim_size=self.num_cells,
                reduce='sum',
            )

            def _mag(term):
                """|term|, or 0 for the scalar placeholders some modes pass."""
                return term.abs() if torch.is_tensor(term) else 0.0

            self._norm_scale_cont = total_RHS_scale[..., 0:1]
            self._norm_scale_mom = (
                total_RHS_scale[..., 1:4]
                + _mag(unsteady_uvw_cell)
                + _mag(source_term_total)
                + _mag(buoyancy_volume_source)
            )
            self._norm_scale_energy = (
                total_RHS_scale[..., 4:5]
                + _mag(unsteady_t_cell)
                + _mag(heat_source_term_total)
            )
            # The explicit source is a term of the row like any other; under IMEX
            # it is the convection the flux scale above does not contain.
            if explicit_U is not None:
                self._norm_scale_mom = self._norm_scale_mom + _mag(explicit_U)
            if explicit_T is not None:
                self._norm_scale_energy = self._norm_scale_energy + _mag(explicit_T)
            if explicit_p_rgh is not None:
                self._norm_scale_cont = self._norm_scale_cont + _mag(explicit_p_rgh)
        ''' <<< Total flux assembly <<< '''

        ''' >>> Compute jump in scalar values across cell faces >>> '''
        # Every RMS norm needs a strictly positive radicand.  At an exact
        # solution ``sqrt(sum(r**2))`` has an undefined derivative at zero,
        # which otherwise contaminates unrelated fields with NaNs during the
        # windowed backward pass (even when a component has zero weight).
        eps_sqrt = 1e-16
        if self.loss_phi_jump_weight == 0.:
            loss_phi_jump = uvwpt_hat_cpd_cell.new_zeros(
                (*uvwpt_hat_cpd_cell.shape[:-2], self.num_graphs, 1)
            )
            # Store zero residual for Newton method
            # NOTE: Use num_cells (interior cells) to match phi_jump output size
            self.residual_phi_jump = uvwpt_hat_cpd_cell.new_zeros(
                (*uvwpt_hat_cpd_cell.shape[:-2],
                 self.num_cells, 4 if self.dim == 2 else 5)
            )
        else:
            # Compute jump in scalar values across cell faces (U, p_rgh, T_new)
            # Store raw phi_jump residual for Newton method (before squaring)
            self.residual_phi_jump = self.phi_jump(
                # phi_cpd_cell=uvwpt_hat_cpd_cell[:,6:7],
                # grad_phi_cpd_cell=grad_uvwpt_hat_cpd_cell[:,6:7],
                phi_cpd_cell=torch.cat(
                    (
                        uvwpt_hat_cpd_cell[..., 0:3] if self.dim == 3
                        else uvwpt_hat_cpd_cell[..., 0:2],
                        uvwpt_hat_cpd_cell[..., 6:8],
                    ),
                    dim=-1,
                ),
                grad_phi_cpd_cell=torch.cat(
                    (
                        grad_uvwpt_hat_cpd_cell[..., 0:3, :] if self.dim == 3
                        else grad_uvwpt_hat_cpd_cell[..., 0:2, :],
                        grad_uvwpt_hat_cpd_cell[..., 6:8, :],
                    ),
                    dim=-2,
                ),
            )  # [num_cpd_cells, 4 for 2D or 5 for 3D]
            loss_phi_jump = torch.sqrt(
                global_add_pool(
                    self.residual_phi_jump**2,
                    batch=self.batch_cell,
                    size=self.num_graphs,
                ) + eps_sqrt
            )
        ''' <<< Compute jump in scalar values across cell faces <<< '''

        ''' >>> Compute RMS residual for each graph in batch >>> '''
        # Compute RMS residual for continuity equation
        self.residual_continuity = continuity_RHS  # [num_interior_cells, 1]
        loss_cont = torch.sqrt(
            global_add_pool(
                (continuity_RHS)**2, batch=self.batch_cell, size=self.num_graphs
            ) + eps_sqrt
        )  # [num_graphs, 1]

        # Pooled equation scales for the residualControl gate only (see the
        # normFactor note above).  Same L2-type pooling as the losses they
        # normalise, so the ratio is dimensionless.  No autograd path.
        with torch.no_grad():
            def _pooled_scale(scale_cell):
                return torch.sqrt(
                    global_add_pool(
                        scale_cell**2, batch=self.batch_cell, size=self.num_graphs
                    ) + eps_sqrt
                )

            self.norm_factor_cont = _pooled_scale(self._norm_scale_cont)
            self.norm_factor_mom = _pooled_scale(self._norm_scale_mom)
            self.norm_factor_energy = _pooled_scale(self._norm_scale_energy)

        # Compute RMS residual for momentum equations
        # all have shape [num_interior_cells, 3]
        # For bounded convection schemes, add the bounded source term: -div(phi)*U
        if bounded_source_uvw is not None:
            bounded_source_uvw_interior = bounded_source_uvw.index_select(
                -2, self._interior_cell_indices
            )
        else:
            bounded_source_uvw_interior = 0.0
        # Pressure-FREE cell residual: the face_cv rhat averaging consumes this
        # (p_rgh couples to the face test ONLY via the compact snGrad, never
        # through the cell pressure term). Buoyancy stays in pfree — it
        # is a physical source, not a pressure representation.
        residual_momentum_pfree = (
            unsteady_uvw_cell + momentum_RHS
            - source_term_total - buoyancy_volume_source
            + bounded_source_uvw_interior  # Add bounded correction: -div(phi)*U
        )
        if explicit_U is not None:
            # A residual-density term like the source, so the face_cv rhat sees it too.
            residual_momentum_pfree = residual_momentum_pfree + explicit_U
        self.residual_momentum = residual_momentum_pfree + P_rgh_grad_volume
        loss_mom_parts = []
        if self._momentum_residual_cell:
            loss_mom_parts.append(torch.sqrt(
                global_add_pool(
                    self.residual_momentum**2, batch=self.batch_cell, size=self.num_graphs
                ) + eps_sqrt
            )[..., 0:self.dim])  # [..., num_graphs, dim]
        if self._momentum_residual_face:
            # Perot/MAC staggered test position: face-normal momentum residual.
            # residual_momentum above was assembled WITHOUT the pressure term,
            # so p reaches the loss ONLY through the compact face snGrad — the
            # 2-delta mode is first-order visible on every face with no
            # cell-averaging cancellation. Non-internal faces are discarded by
            # the cached active mask and do not add boundary momentum equations.
            # NOTE: uses the pressure-FREE residual — in combined mode the full
            # residual carries the cell p_rgh term, which must never leak into
            # the face rhat.
            rhat_int = residual_momentum_pfree / self.cell_volume
            rhat_cpd = rhat_int.new_zeros(
                (*rhat_int.shape[:-2], self.num_cpd_cells, 3)
            ).index_copy(-2, self._facecv_interior_ids, rhat_int)
            rhat_cpd = self._fill_cyclic_ghosts(rhat_cpd)
            r_C = rhat_cpd.index_select(-2, self._facecv_C)
            r_F = rhat_cpd.index_select(-2, self._facecv_F)
            if self._facecv_avg == "upwind":
                # flux-sign upwind pick: keeps the alternating residual images
                # of streamwise 2-delta velocity modes visible (the half
                # average cancels them). Sign from the production face
                # velocity; the selector carries no gradient.
                with torch.no_grad():
                    pick_C = (
                        (uvwpt_hat_face[..., 0:3] * self.unv_face).sum(
                            dim=-1, keepdim=True
                        )
                        >= 0.0
                    )
                rhat_face = torch.where(
                    self._facecv_face_twosided,
                    torch.where(pick_C, r_C, r_F),
                    r_C,
                )
            else:
                rhat_face = torch.where(
                    self._facecv_face_twosided, 0.5 * (r_C + r_F), r_C
                )
            residual_momentum_face = (
                (self.unv_face * rhat_face).sum(dim=-1, keepdim=True)
                + self.grad_p_coeff * snGrad_p_face
            ) * self._facecv_Vface
            self.residual_momentum_face = self._mask_facecv_residual(
                residual_momentum_face
            )
            loss_mom_parts.append(torch.sqrt(
                global_add_pool(
                    self.residual_momentum_face**2,
                    batch=self.batch_face,
                    size=self.num_graphs,
                ) + eps_sqrt
            ))  # [num_graphs, 1] — single face-normal momentum channel
        # Pure modes keep their legacy shapes ([G,dim] cell / [G,1] face_cv);
        # "cell+face_cv" concatenates to [G, dim+1].
        loss_momentum_raw = (
            loss_mom_parts[0] if len(loss_mom_parts) == 1
            else torch.cat(loss_mom_parts, dim=-1)
        )

        # Compute RMS residual for energy equation
        # all have shape [num_interior_cells, 1]
        # For bounded convection schemes, add the bounded source term: -div(phi)*T
        if bounded_source_t is not None:
            bounded_source_t_interior = bounded_source_t.index_select(
                -2, self._interior_cell_indices
            )
        else:
            bounded_source_t_interior = 0.0
        self.residual_energy = (
            unsteady_t_cell + temperature_RHS - heat_source_term_total
            + bounded_source_t_interior  # Add bounded correction: -div(phi)*T
        )
        if explicit_T is not None:
            self.residual_energy = self.residual_energy + explicit_T
        loss_energy = torch.sqrt(
            global_add_pool(
                self.residual_energy**2, batch=self.batch_cell, size=self.num_graphs
            ) + eps_sqrt
        )

        # Transported turbulence rows (k, omega-tilde / epsilon): assembled
        # by the generic scalar-transport loop; their residual attributes
        # land after the existing ones per COLLOCATED_UNKNOWNS order.
        # Laminar: skipped entirely (W = 0), keeping the laminar forward at
        # its measured nondeterminism floor.
        if self._turb_width:
            loss_turb = self._assemble_transported_residuals(
                uvwpt_hat_cpd_cell=uvwpt_hat_cpd_cell,
                uvw_flux_face=uvwpt_flux_hat_face[..., 0:1],
                grad_uvwpt_hat_cpd_cell=grad_uvwpt_hat_cpd_cell,
                grad_uvwpt_hat_face=grad_uvwpt_hat_face,
                grad_uvwpt_flux_hat_face=grad_uvwpt_flux_hat_face,
                sngrad_corr_grad_uvwpt_hat_cpd_cell=(
                    sngrad_corr_grad_uvwpt_hat_cpd_cell),
                eps_sqrt=eps_sqrt,
            )
        else:
            loss_turb = None
        self.loss_turb_component = loss_turb
        ''' <<< Compute RMS residual for each graph in batch <<< '''

        ''' >>> Compute pressure reference loss >>> '''
        # Compute loss for pRefCell constraint (soft constraint instead of hard assignment)
        # uvwpt_hat_cpd_cell: [uvw_new(0:3), uvw_old(3:6), p_rgh(6:7), T_new(7:8), T_old(8:9)]
        loss_p_rghRef = self.loss_setp_rghRef(
            p_rgh_cpd_cell=uvwpt_hat_cpd_cell[..., 6:7],
        )  # [num_graphs, 1]
        ''' <<< Compute pressure reference loss <<< '''

        self.loss_cont_component = loss_cont
        self.loss_momentum_component = loss_momentum_raw
        self.loss_energy_component = loss_energy
        self.loss_phi_jump_component = loss_phi_jump
        self.loss_p_rghRef_component = loss_p_rghRef  # [num_graphs, 1]

        ''' >>> Global mass balance >>> '''
        # continuity_RHS is the volume-integrated divergence per cell, so a per-graph
        # SUM telescopes: interior faces appear twice with opposite sign and cancel,
        # leaving the net flux through the domain boundary, i.e. inflow - outflow.
        #
        # loss_cont above is an L2 over cells, in which this single linear combination
        # of ~1.5e5 rows costs essentially nothing -- and a per-cell residualControl gate
        # cannot represent it at all. Measured on the Cylinder cold start at t=0.30:
        # every cell sat at 5.3e-7, passing a 3.0e-5 gate with 50x margin, while all of
        # them carried the SAME SIGN and summed to -7.8e-2 against OpenFOAM's 1.4e-7.
        # The accumulated mass showed up as a streamwise pressure ramp that oscillated
        # with a ~24-step period and grew ~2.4x per half period until it wrecked the
        # wake.
        #
        # A fixedValue inlet with a zeroGradient outlet does not conserve mass by
        # construction; OpenFOAM enforces it in adjustPhi before each pressure solve.
        # A segregated method gets that structurally, a coupled least squares has to be
        # charged for it. Default weight 0 keeps every existing configuration
        # bit-identical.
        loss_mass_balance = torch.abs(
            global_add_pool(
                continuity_RHS, batch=self.batch_cell, size=self.num_graphs
            )
        )  # [num_graphs, 1]
        self.loss_mass_balance_component = loss_mass_balance
        ''' <<< Global mass balance <<< '''

        if self.loss_mom_adaptive_balance:
            mom_detached = loss_momentum_raw.detach()
            mom_max = torch.amax(mom_detached, dim=-1, keepdim=True)
            momentum_balance_weight = mom_max / (mom_detached + 1e-12)
            loss_momentum = loss_momentum_raw * momentum_balance_weight
        else:
            loss_momentum = loss_momentum_raw

        checker_contrib = self.loss_checker_weight * self._checker_loss if self._checker_loss is not None else 0.0
        # Preserve both leading time and graph axes.  The caller owns the
        # explicit reduction over ``[T, B]``.
        loss_inner = (
            self.loss_cont_weight*loss_cont.sum(dim=-1,keepdim=True) +
            self.loss_mom_weight*loss_momentum.sum(dim=-1,keepdim=True) +
            self.loss_energy_weight*loss_energy.sum(dim=-1,keepdim=True) +
            self.loss_phi_jump_weight*loss_phi_jump.sum(dim=-1,keepdim=True) +
            self.loss_p_Ref_weight*loss_p_rghRef.sum(dim=-1,keepdim=True) +
            self.loss_mass_balance_weight*loss_mass_balance.sum(dim=-1,keepdim=True) +
            checker_contrib
        )
        if loss_turb is not None:
            loss_inner = loss_inner + self.loss_turb_weight * loss_turb.sum(
                dim=-1, keepdim=True)
        loss_total = torch.log(loss_inner).squeeze(-1)  # [num_graphs]

        return loss_total

    def get_separated_losses(self):
        """
        Returns the individual loss components for continuity, momentum,
        energy, phi_jump, p_rghRef, and checker.

        Slot ordering matches LOSS_COMPONENT_NAMES:
            (cont, mom, energy, phi_jump, p_rghRef, checker)

        Returns:
            Tuple of six tensors (or None if forward has not run yet):
                - loss_cont: [num_graphs, 1] continuity equation residual
                - loss_momentum: [num_graphs, dim] momentum equation residuals
                - loss_energy: [num_graphs, 1] energy equation residual
                - loss_phi_jump: [num_graphs, ...] phi jump loss
                - loss_p_rghRef: [num_graphs, 1] p_rgh reference constraint loss
                - checker: [num_graphs, 1] checkerboard loss
        """
        if hasattr(self, 'loss_cont_component') and hasattr(self, 'loss_momentum_component') and hasattr(self, 'loss_energy_component'):
            return (
                self.loss_cont_component,
                self.loss_momentum_component,
                self.loss_energy_component,
                self.loss_phi_jump_component if hasattr(self, 'loss_phi_jump_component') and self.loss_phi_jump_component is not None else None,
                self.loss_p_rghRef_component if hasattr(self, 'loss_p_rghRef_component') and self.loss_p_rghRef_component is not None else None,
                self._checker_loss.detach() if self._checker_loss is not None else None,
            )
        else:
            return None, None, None, None, None, None

    def get_normalization_factors(self):
        """OpenFOAM-style equation scales for the convergence gate.

        Returns ``(cont, mom, energy)`` pooled the same way as the matching loss
        components, or ``None`` values before a forward pass.  These are
        detached by construction and must never divide an optimised loss; see
        the normFactor note in the forward pass.
        """
        if hasattr(self, "norm_factor_cont"):
            return (
                self.norm_factor_cont,
                self.norm_factor_mom,
                self.norm_factor_energy,
            )
        return None, None, None

    def get_residual_vector(self, dim=None, include_phi_jump=True):
        """
        Returns the cell-wise residual vector for Newton method (Gauss-Newton).

        The residual vector F is concatenated from:
        - continuity: [num_cells, 1]
        - momentum: [num_cells, dim] (2 for 2D, 3 for 3D)
        - energy: [num_cells, 1]
        - phi_jump: [num_cells, dim+2] (4 for 2D, 5 for 3D) - optional

        Args:
            dim: Dimension (2 or 3). If None, uses self.dim.
            include_phi_jump: Whether to include phi_jump residual in the output.

        Returns:
            residual_vec: [num_cells, n_eqs] where n_eqs depends on include_phi_jump
                - Without phi_jump: 2D: 4, 3D: 5
                - With phi_jump: 2D: 8, 3D: 10
        """
        if not hasattr(self, 'residual_continuity'):
            return None

        dim = dim or self.dim

        # For 2D, only use u,v components of momentum residual
        if dim == 2:
            mom_residual = self.residual_momentum[..., 0:2]
        else:
            mom_residual = self.residual_momentum  # [num_cells, 3]

        # Base residuals: continuity + momentum + energy
        residual_components = [
            self.residual_continuity,  # [num_cells, 1]
            mom_residual,               # [num_cells, dim]
            self.residual_energy        # [num_cells, 1]
        ]

        # Optionally include phi_jump residual
        if include_phi_jump and hasattr(self, 'residual_phi_jump') and self.loss_phi_jump_weight > 0:
            # phi_jump residual: [num_cells, dim+2] (u,v,p,T for 2D or u,v,w,p,T for 3D)
            residual_components.append(self.residual_phi_jump)

        # Concatenate all residuals
        residual_vec = torch.cat(residual_components, dim=-1)

        return residual_vec

    def compute_pure_residual(self, phi_new_flat: torch.Tensor, phi_old_flat: torch.Tensor) -> torch.Tensor:
        """
        [JFNK专用] 纯函数接口：
        1. 输入输出都是扁平 Tensor
        2. 不算 Loss (Log/Mean)，不存 self
        3. 必须返回方阵系统 (Eqs = Vars)，不包含phi_jump
        4. 硬编码压力参考点约束

        Args:
            phi_new_flat: Flattened new solution [N_cpd_cells * N_vars]
            phi_old_flat: Flattened old solution [N_cpd_cells * N_vars]

        Returns:
            residual_flat: Flattened residual vector [N_interior_cells * N_eqs]
                          For 2D: N_vars=4 (u,v,p,T), N_eqs=4
                          For 3D: N_vars=5 (u,v,w,p,T), N_eqs=5
        """
        if self._turb_width:
            raise NotImplementedError(
                "[buoyantBoussinesqpicoext] compute_pure_residual (legacy "
                "JFNK path) hard-codes the 4/5-variable laminar layout; the "
                "coupled Newton path (CoupledNewton.collocated_newton) is "
                "the supported route for transported turbulence fields")
        # Determine number of variables based on dimension
        n_vars = 4 if self.dim == 2 else 5  # 2D: u,v,p,T; 3D: u,v,w,p,T

        # Unpack flat tensors to cell-wise format
        phi_new = phi_new_flat.view(self.num_cpd_cells, n_vars)
        phi_old = phi_old_flat.view(self.num_cpd_cells, n_vars)

        # Convert packed format to full 5-var format for integrator
        if self.dim == 2:
            # [u, v, p, T] -> [u, v, w=0, p, T]
            phi_new_full = torch.zeros((self.num_cpd_cells, 5), device=phi_new.device, dtype=phi_new.dtype)
            phi_new_full[:, 0:2] = phi_new[:, 0:2]  # u, v
            phi_new_full[:, 2] = 0.0  # w = 0
            phi_new_full[:, 3:5] = phi_new[:, 2:4]  # p, T

            phi_old_full = torch.zeros((self.num_cpd_cells, 5), device=phi_old.device, dtype=phi_old.dtype)
            phi_old_full[:, 0:2] = phi_old[:, 0:2]
            phi_old_full[:, 2] = 0.0
            phi_old_full[:, 3:5] = phi_old[:, 2:4]
        else:
            phi_new_full = phi_new
            phi_old_full = phi_old

        # Run forward pass to compute residuals (this populates self.residual_*)
        # Note: We call forward but ignore the loss_total return value
        _, phi_bced = self.forward(
            phi_old_cpd_cell=phi_old_full,
            phi_new_cpd_cell=phi_new_full
        )

        # Get residuals (interior cells only, no phi_jump)
        # residual_continuity: [N_interior_cells, 1]
        # residual_momentum: [N_interior_cells, 3]
        # residual_energy: [N_interior_cells, 1]
        res_cont = self.residual_continuity.clone()  # [N_interior_cells, 1]
        res_mom = self.residual_momentum[:, 0:self.dim].clone()  # [N_interior_cells, dim]
        res_eng = self.residual_energy.clone()  # [N_interior_cells, 1]

        # --- 压力参考点约束 ---
        # 将参考点的连续性方程残差替换为压力约束: p_i - p_ref = 0
        if self.pRefCell.numel() > 0:
            # pRefCell是相对于interior cells的索引
            # 需要找到pRefCell在interior cells中的位置
            # 注意：self.pRefCell已经是interior cell的全局索引
            # 我们需要将其映射到interior cells的局部索引

            # 获取interior cell的全局索引
            interior_indices = torch.nonzero(self.mask_interior_cell, as_tuple=True)[0]

            # 对于每个pRefCell，找到它在interior_indices中的位置
            for i, pref_global_idx in enumerate(self.pRefCell):
                # 找到pref_global_idx在interior_indices中的位置
                local_idx_mask = (interior_indices == pref_global_idx)
                if local_idx_mask.any():
                    local_idx = torch.nonzero(local_idx_mask, as_tuple=True)[0][0]
                    # 获取当前压力值 (p_rgh)
                    # phi_new_full的压力在索引3
                    p_current = phi_new_full[pref_global_idx, 3]
                    p_ref = self.pRefValue[i]
                    # 替换连续性方程残差为压力约束
                    # 注意量纲缩放：连续性方程残差是质量流量，压力是Pa
                    # 使用较小的缩放因子使其与其他残差量级相当
                    res_cont[local_idx, 0] = (p_current - p_ref) * 1e-6

        # Concatenate residuals: [continuity, momentum, energy]
        # For 2D: [1, 2, 1] = 4 equations
        # For 3D: [1, 3, 1] = 5 equations
        all_res = torch.cat([res_cont, res_mom, res_eng], dim=1)

        return all_res.flatten()

    def get_diagonal_preconditioner(self, dt: torch.Tensor) -> torch.Tensor:
        """
        返回 Jacobi 预条件子的倒数 (M^-1)

        对于FVM离散，主对角线元素近似为：
        - 动量方程: a_P ≈ Vol/dt (非稳态项主导)
        - 连续性方程: 设为1.0 (约束方程，对角线为0)
        - 能量方程: a_P ≈ Vol/dt

        Args:
            dt: Time step tensor [N_interior_cells, 1] or scalar

        Returns:
            diag_inv: Inverse of diagonal preconditioner [N_interior_cells * N_eqs]
                     For 2D: N_eqs=4 (continuity, u, v, T)
                     For 3D: N_eqs=5 (continuity, u, v, w, T)
        """
        # Get cell volumes for interior cells
        cell_vol = self.cell_volume  # [N_interior_cells, 1]

        # Handle dt as tensor or scalar
        if isinstance(dt, torch.Tensor):
            if dt.dim() == 0:
                dt_val = dt.item()
            else:
                dt_val = dt
        else:
            dt_val = dt

        # Diagonal for momentum equations: Vol/dt
        diag_mom = cell_vol / dt_val  # [N_interior_cells, 1]

        # Diagonal for continuity equation: set to 1.0 (constraint equation)
        diag_cont = torch.ones_like(cell_vol)  # [N_interior_cells, 1]

        # Diagonal for energy equation: Vol/dt
        diag_eng = cell_vol / dt_val  # [N_interior_cells, 1]

        # Assemble diagonal based on dimension
        # Order: [continuity, momentum (u,v or u,v,w), energy]
        if self.dim == 2:
            # 2D: [cont(1), u(1), v(1), T(1)] = 4
            diag_all = torch.cat([diag_cont, diag_mom, diag_mom, diag_eng], dim=1)
        else:
            # 3D: [cont(1), u(1), v(1), w(1), T(1)] = 5
            diag_all = torch.cat([diag_cont, diag_mom, diag_mom, diag_mom, diag_eng], dim=1)

        # Return inverse (add small epsilon to avoid division by zero)
        eps = 1e-10
        return 1.0 / (diag_all.flatten() + eps)

    def _build_facecv_geom(self):
        """Precompute static geometry for the face_cv momentum residual.

        All tensors are mesh-only (no dependence on phi/theta), so they are
        built once per geometry registration:
          - _facecv_C/_facecv_F: owner/neighbour cpd indices per face
          - _facecv_face_interior: [N_face,1] bool, both sides interior
          - _facecv_face_twosided: interior OR cyclic -- the faces whose
            residual density and volume are averaged over both sides
          - _facecv_active_face: [N_face,1] bool, valid face-CV equations
          - _facecv_interior_ids: interior cpd indices (for scatter of R_hat)
          - _facecv_Vface: [N_face,1] staggered CV volume 0.5*(V_C+V_F) on
            two-sided faces (interior and cyclic: the
            ghost volume is filled from the periodic partner), V_C on
            physical boundary faces (matching the validated run)

        Which faces carry a row is [fvSchemes] faceCvBoundaryRows (see
        facecv_active_face_mask); cyclic rows are two-sided.

        CACHED on the mesh signature register_geometrics already computes
        (`self._geom_mesh_sig`). Simulator.forward (src/NNmodels/importer.py)
        calls register_properties(force_register=True) on EVERY forward, so
        this function would rerun in full every step even though its
        inputs are mesh-only; skipping the rebuild when the mesh identity is
        unchanged (the same signature register_geometrics already trusts for
        its own idempotency) takes it to ~0 ms after the first call.
        """
        # Read the mesh signature the way base_solver.py:167,520 and
        # FVInterpolation.py:146 read it -- defensively.  A bare
        # `self._geom_mesh_sig` here raised AttributeError on any path that
        # reaches this method before register_geometrics has run.  The
        # `is not None` guard is load-bearing: without it two absent
        # signatures compare equal and the geometry is silently NOT built.
        _sig = getattr(self, "_geom_mesh_sig", None)
        if _sig is not None and getattr(self, "_facecv_geom_sig", None) == _sig:
            return
        C = self.C_senders.long().view(-1)
        F = self.F_receivers.long().view(-1)
        mask_int = self.mask_interior_cell.view(-1)
        face_interior = (mask_int[C] & mask_int[F]).view(-1, 1)
        interior_ids = mask_int.nonzero(as_tuple=True)[0]
        # register_geometrics() normally creates this alias. Keep synthetic
        # solver fixtures and manually registered meshes on the same static
        # integer-index path.
        self._interior_cell_indices = interior_ids
        if not hasattr(self, "_non_empty_cells_face_indices"):
            self._non_empty_cells_face_indices = (
                self.mask_non_empty_cells_face.nonzero(
                    as_tuple=False
                ).squeeze(-1)
            )
        vol_cpd = torch.zeros(
            self.num_cpd_cells, 1,
            device=self.cell_volume.device, dtype=self.cell_volume.dtype,
        )
        vol_cpd = vol_cpd.index_copy(0, interior_ids, self.cell_volume)
        vol_cpd = self._fill_cyclic_ghosts(vol_cpd)
        V_C, V_F = vol_cpd[C], vol_cpd[F]
        self._facecv_C = C
        self._facecv_F = F
        self._facecv_face_interior = face_interior
        # A cyclic face is two-sided once its ghost carries the partner's values.
        face_cyclic = (self.mask_boundary_face & ~self.mask_boundary_non_cyclic_face).view(-1, 1)
        face_twosided = face_interior | face_cyclic
        self._facecv_face_twosided = face_twosided
        self._facecv_active_face = self.facecv_active_face_mask(
            face_interior, VARType.PRESSURE_RGH, self._facecv_boundary_rows)
        self._facecv_interior_ids = interior_ids
        self._facecv_Vface = torch.where(face_twosided, 0.5 * (V_C + V_F), V_C)

        self._facecv_geom_sig = _sig

    def _mask_facecv_residual(self, residual: torch.Tensor) -> torch.Tensor:
        """Keep only valid internal-face CV equations, preserving all axes."""
        return torch.where(
            self._facecv_active_face,
            residual,
            torch.zeros_like(residual),
        )

    # @torch.compile(mode="default",dynamic=True)
    def enforce_bcs_only(
        self,
        phi_old_cpd_cell: torch.Tensor,
        phi_new_cpd_cell: torch.Tensor,
    ) -> torch.Tensor:
        """Apply BCs to ``phi_new`` without residual computation.

        Mirrors the cell-value BC enforcement block inside :meth:`forward`
        (the ">>> Apply boundary conditions >>>" section). Only the
        cell-value passes of enforce_bc_1st / enforce_cyclic / enforce_mixed
        are replayed; face-flux BCs are intentionally skipped because no
        fluxes are constructed in this path.

        The Boussinesq solver uses the ``p_rgh`` (modified pressure)
        formulation, so channel 3 of ``phi`` carries ``p_rgh`` and channel 4
        carries temperature. The returned tensor preserves that layout.

        Args:
            phi_old_cpd_cell: Previous time step solution [N_cpd_cells, 5].
                Unused by the current BC set but kept for interface parity.
            phi_new_cpd_cell: Current time step solution [N_cpd_cells, 5]
                where channels are ``[u, v, w, p_rgh, T]``.

        Returns:
            phi_bc_enforced: BC-enforced solution [N_cpd_cells, 5] with the
                same channel order as ``phi_new_cpd_cell``.
        """
        if phi_new_cpd_cell.ndim not in (2, 3):
            raise ValueError(
                "phi_new_cpd_cell must have shape [N_cpd, 5] or [T, N_cpd, 5]"
            )
        if phi_new_cpd_cell.shape[-2] != self.num_cpd_cells:
            raise ValueError(
                "phi_new_cpd_cell must be CPD-sized [..., num_cpd_cells, n_vars]; "
                "expand interior values before calling enforce_bcs_only"
            )

        uvw_new_cpd_cell = phi_new_cpd_cell[..., 0:3]
        p_rgh_new_cpd_cell = phi_new_cpd_cell[..., 3:4]
        t_new_cpd_cell = phi_new_cpd_cell[..., 4:5]
        if phi_new_cpd_cell.requires_grad:
            # BC implementations update ghost rows in place.  Never expose a
            # view of a leaf (or a view reused by autograd) to those writes.
            uvw_new_cpd_cell = uvw_new_cpd_cell.clone()
            p_rgh_new_cpd_cell = p_rgh_new_cpd_cell.clone()
            t_new_cpd_cell = t_new_cpd_cell.clone()

        if self.dim == 2:
            # Mirror forward()'s 2D handling: zero out the w component
            uvw_new_cpd_cell = torch.cat(
                (
                    uvw_new_cpd_cell[..., 0:2],
                    torch.zeros_like(uvw_new_cpd_cell[..., 0:1]),
                ),
                dim=-1,
            )

        fvfield = {
            "uvw_cpd_cell": uvw_new_cpd_cell,
            "p_rgh_cpd_cell": p_rgh_new_cpd_cell,
            "t_cpd_cell": t_new_cpd_cell,
        }
        _turb_off = 5
        for _fname, _w, *_rest in self._turb_transported:
            _t = phi_new_cpd_cell[..., _turb_off:_turb_off + _w]
            fvfield[f"{_fname}_cpd_cell"] = (
                _t.clone() if phi_new_cpd_cell.requires_grad else _t)
            _turb_off += _w

        # Same sequence as forward() cell-value BC phase
        fvfield = self.enforce_bc_1st(fvfield)
        fvfield = self.enforce_cyclic(fvfield)
        fvfield = self.enforce_mixed(fvfield)
        # COUPLED closes the sequence.  Without it, every path here stopped at
        # mixed and `enforce_coupled` had ONE caller in the whole repo (the
        # data-driven adapter), so a cross-patch BC -- the DC rack's Eq. 17 -- was
        # never applied under FVM-loss, ODIL or coupled Newton while the assembler
        # had already been taught to carry its column.  A case with no COUPLED BC
        # iterates an empty group, which is why ldc16 stays bit-identical.
        fvfield = self.enforce_coupled(fvfield)

        phi_bc_enforced = torch.cat(
            (
                fvfield["uvw_cpd_cell"],
                fvfield["p_rgh_cpd_cell"],
                fvfield["t_cpd_cell"],
                *(fvfield[f"{e[0]}_cpd_cell"]
                  for e in self._turb_transported),
            ),
            dim=-1,
        )
        return phi_bc_enforced

    def forward(
        self,
        phi_old_cpd_cell: Optional[torch.Tensor] = None,
        phi_new_cpd_cell: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass for the buoyantBoussinesq finite volume solver.

        Solves the coupled Boussinesq equations with p_rgh formulation:
        - Continuity: ∇·u = 0
        - Momentum: ∂u/∂t + ∇·(u⊗u) = -∇p_rgh/ρ₀ + ν∇²u + gh·β·∇T
        - Energy: ∂T/∂t + ∇·(uT) = α∇²T

        where gh = g·r - ghRef is the gravitational potential.

        Args:
            phi_old_cpd_cell: Previous time step solution [N_cpd_cells, 5] (uvw, p_rgh, T).
            phi_new_cpd_cell: Current time step solution [N_cpd_cells, 5] (uvw, p_rgh, T).

        Returns:
            Tuple containing:
                - loss_total: ``[B]`` for rank-2 input or ``[T,B]`` for rank-3.
                - phi_new_smoothed_cpd_cell: BC-enforced state with the same
                  shape as ``phi_new_cpd_cell``.
        """

        if phi_old_cpd_cell is None or phi_new_cpd_cell is None:
            raise ValueError("both phi_old_cpd_cell and phi_new_cpd_cell are required")
        # Channel layout [u, v, w, p_rgh, T] plus the turbulence model's
        # TRANSPORTED channels after T (phase 5b) -- 5 exactly for laminar.
        _n_vars = 5 + self._turb_width
        if phi_old_cpd_cell.ndim not in (2, 3) or phi_new_cpd_cell.ndim not in (2, 3):
            raise ValueError(
                f"buoyant solver inputs must be [N_cpd, {_n_vars}] or "
                f"[T, N_cpd, {_n_vars}]"
            )
        if phi_old_cpd_cell.shape != phi_new_cpd_cell.shape:
            raise ValueError(
                "old/new solution shapes must match, got "
                f"{tuple(phi_old_cpd_cell.shape)} and {tuple(phi_new_cpd_cell.shape)}"
            )
        if phi_new_cpd_cell.shape[-2:] != (self.num_cpd_cells, _n_vars):
            raise ValueError(
                f"buoyant solver inputs must end in [num_cpd_cells, {_n_vars}] "
                f"([u, v, w, p_rgh, T"
                + "".join(f", {e[0]}" for e in self._turb_transported)
                + f"] for turbulence model {type(self.turbulence).__name__}), "
                f"got {tuple(phi_new_cpd_cell.shape)}"
            )

        # Extract velocity, modified pressure (p_rgh), and temperature from solution vectors
        # Field order: [U(3), p_rgh(1), T(1)] = 5 channels total
        # Old-step fields are taken as read-only views (BCs are not re-applied to them).
        uvw_old_cpd_cell = phi_old_cpd_cell[..., 0:3]
        p_rgh_old_cpd_cell = phi_old_cpd_cell[..., 3:4]
        t_old_cpd_cell = phi_old_cpd_cell[..., 4:5]

        # Slicing creates views; BC enforcement does in-place writes on ghost cells.
        # When phi_new requires grad AND is a leaf (direct-Parameter ODIL mode),
        # in-place on a view errors; the GNN path feeds a non-leaf phi_new for
        # which the write is permitted.  Clone only when autograd is active —
        # zero overhead in GNN mode, safe in both ODIL usages.
        uvw_new_cpd_cell = phi_new_cpd_cell[..., 0:3]
        p_rgh_new_cpd_cell = phi_new_cpd_cell[..., 3:4]
        t_new_cpd_cell = phi_new_cpd_cell[..., 4:5]
        if phi_new_cpd_cell.requires_grad:
            uvw_new_cpd_cell = uvw_new_cpd_cell.clone()
            p_rgh_new_cpd_cell = p_rgh_new_cpd_cell.clone()
            t_new_cpd_cell = t_new_cpd_cell.clone()

        if self.dim == 2:  # Use cached dim value
            # For 2D cases, zero out the w component
            uvw_new_cpd_cell = torch.cat(
                (uvw_new_cpd_cell[..., 0:2], torch.zeros_like(uvw_new_cpd_cell[..., 0:1])),
                dim=-1,
            )
            # Use non-in-place operation for torch.func.jvp compatibility
            uvw_old_cpd_cell = torch.cat(
                (uvw_old_cpd_cell[..., 0:2], torch.zeros_like(uvw_old_cpd_cell[..., 0:1])),
                dim=-1,
            )

        # The old velocity the time-aware BCs read: the TRUE u^n when a transient
        # stepper armed one (set_bc_old_state, H9; w zeroed like the old field above
        # for dim = 2), else uvw_old_cpd_cell itself -- the time term keeps reading
        # the (possibly effective) old level either way.
        bc_uvw_old_cpd_cell = self._bc_old_velocity(uvw_old_cpd_cell)
        if bc_uvw_old_cpd_cell is not uvw_old_cpd_cell and self.dim == 2:
            bc_uvw_old_cpd_cell = torch.cat(
                (bc_uvw_old_cpd_cell[..., 0:2],
                 torch.zeros_like(bc_uvw_old_cpd_cell[..., 0:1])),
                dim=-1,
            )

        ''' >>> Apply boundary conditions >>> '''
        # Prepare fvfield dictionary for BC enforcement
        fvfield = {
            "uvw_cpd_cell": uvw_new_cpd_cell,
            "p_rgh_cpd_cell": p_rgh_new_cpd_cell,
            "t_cpd_cell": t_new_cpd_cell,
            # Read-only previous-step field for time-aware BCs (e.g. advectiveOutlet).
            "uvw_old_cpd_cell": bc_uvw_old_cpd_cell,
        }
        # Transported turbulence scalars ride the same fvfield/BC machinery
        # under their own keys ("k_cpd_cell", ...); the loader instantiated
        # their BC objects from bc/<field>.toml and each eval touches only
        # its own key, so the enforcement passes need no per-field branching.
        _turb_off = 5
        for _fname, _w, _bc_attr, _res_attr in self._turb_transported:
            _t = phi_new_cpd_cell[..., _turb_off:_turb_off + _w]
            fvfield[f"{_fname}_cpd_cell"] = (
                _t.clone() if phi_new_cpd_cell.requires_grad else _t)
            _turb_off += _w

        # Apply first-type (Dirichlet) boundary conditions
        fvfield = self.enforce_bc_1st(fvfield)

        # Apply cyclic (periodic) boundary conditions
        fvfield = self.enforce_cyclic(fvfield)

        # Apply mixed boundary conditions (zeroGradient, fixedFluxPressure, etc.)
        # Note: At this stage, only cell values are updated, not flux values
        fvfield = self.enforce_mixed(fvfield)
        fvfield = self.enforce_coupled(fvfield)

        # Extract updated fields
        uvw_new_cpd_cell = fvfield["uvw_cpd_cell"]
        p_rgh_new_cpd_cell = fvfield["p_rgh_cpd_cell"]
        t_new_cpd_cell = fvfield["t_cpd_cell"]
        # The same tensors under names an exporter can find: after the three
        # enforcement passes above, the ghost rows carry their PRESCRIBED boundary
        # values rather than whatever the caller passed in.  Kept as references,
        # not a concatenated copy, so this costs no allocation and adds nothing to
        # the autograd tape.  Consumed by collocated_newton.state_with_boundaries,
        # which is what lets a converged run write patch values instead of zeros
        # -- and for this solver a zero on a hot wall would read as a wall held at
        # the reference temperature, which is a different physical problem.
        self.bc_uvw_cpd_cell = uvw_new_cpd_cell
        self.bc_p_rgh_cpd_cell = p_rgh_new_cpd_cell
        self.bc_t_cpd_cell = t_new_cpd_cell
        # Transported turbulence fields: BC-enforced tensors under the
        # attribute names TRANSPORTED declared (bc_k_cpd_cell, ...), the
        # same references-not-copies convention as bc_uvw/bc_t above.
        turb_new_cpd_cell = []
        for _fname, _w, _bc_attr, _res_attr in self._turb_transported:
            _t = fvfield[f"{_fname}_cpd_cell"]
            setattr(self, _bc_attr, _t)
            turb_new_cpd_cell.append(_t)
        ''' <<< Apply boundary conditions <<< '''

        # C_cb checkerboard loss (Hopman et al. 2025): penalizes the disparity
        # between compact-stencil (1-hop) and wide-stencil (2-hop) Laplacians.
        # Only the checkerboard mode produces a non-zero (L - L_wide) signal;
        # physical oscillations (vortex shedding) are transparent to this operator.
        # Wide stencil built lazily on first use to avoid torch.compile graph break.
        if self.loss_checker_weight > 0:
            if not self._wide_stencil_built:
                from FVdomain.Load_mesh import build_wide_stencil_neighbor
                _wide_uni = build_wide_stencil_neighbor(
                    self.neighbor_cpd_cell, self._neighbor_cpd_cell_x_for_wide,
                )
                self.neighbor_cpd_cell_wide = torch.cat(
                    [_wide_uni, _wide_uni.flip(0)], dim=1
                ).to(self.neighbor_cpd_cell.device)
                self._wide_stencil_built = True
        if self.loss_checker_weight > 0 and self._wide_stencil_built and self.neighbor_cpd_cell_wide.shape[1] > 0:
            phi_bc = torch.cat(
                (uvw_new_cpd_cell, p_rgh_new_cpd_cell, t_new_cpd_cell), dim=-1
            )  # [N_cpd, 5]
            num_cpd = phi_bc.shape[-2]

            # Compact-stencil Laplacian (1-hop, bidirectional)
            _bidir1 = torch.cat([self.neighbor_cpd_cell, self.neighbor_cpd_cell.flip(0)], dim=1)
            L1, R1 = _bidir1
            nbr_mean_compact = scatter(
                phi_bc.index_select(-2, R1), L1,
                dim=-2, dim_size=num_cpd, reduce="mean",
            )
            lap_compact = phi_bc - nbr_mean_compact

            # Wide-stencil Laplacian (pure 2-hop, already bidirectional)
            L2, R2 = self.neighbor_cpd_cell_wide
            nbr_mean_wide = scatter(
                phi_bc.index_select(-2, R2), L2,
                dim=-2, dim_size=num_cpd, reduce="mean",
            )
            lap_wide = phi_bc - nbr_mean_wide

            # Checkerboard signal: only the 2Δx mode survives this difference
            cb_signal = (lap_compact - lap_wide)[..., self.mask_interior_cell, :]
            # Pool cells independently for each graph, then reduce only the
            # physical-variable channels. For a single graph this has the
            # same value as the former global scalar mean.
            self._checker_loss = global_mean_pool(
                cb_signal ** 2,
                self.batch_cell,
                size=self.num_graphs,
            ).mean(dim=-1, keepdim=True)
        else:
            self._checker_loss = None

        ''' >>> Gradient Reconstruction >>> '''
        # Prepare field for gradient reconstruction: [uvw_new, uvw_old, p_rgh, T_new, T_old]
        uvwpt_hat_cpd_cell = torch.cat(
            (uvw_new_cpd_cell, uvw_old_cpd_cell, p_rgh_new_cpd_cell, t_new_cpd_cell, t_old_cpd_cell),
            dim=-1,
        )

        # Get gradient limiter settings from fvSchemes (if configured)
        grad_limiter_config = self.params.fvschemes.get("gradLimiter", {})
        cell_limited = grad_limiter_config.get("enabled", False)
        limiter_type = grad_limiter_config.get("type", "minmod")
        limiter_k = grad_limiter_config.get("k", 1.0)
        limiter_rt = grad_limiter_config.get("rt", 1.5)

        # The four inert columns are dropped from the RECONSTRUCTION and put back
        # as zeros, so everything downstream still indexes a nine-wide family.
        # ``_decide_old_channels_are_inert`` carries the evidence and the gate;
        # what makes the substitution exact rather than approximate is that WLSQ
        # is per-column linear -- ``A`` is geometry only and each RHS column is
        # scattered from that column's own differences (FVgrad.weighted_lstsq),
        # so an all-zero input column produces an all-zero gradient column and
        # the surviving columns see an identical system.  Same for both face
        # interpolations below, which gather and weight each column alone.
        _inert = self._old_field_channels_are_inert
        # The transported turbulence channels (turb_new) join the WLSQ
        # family AFTER the nine laminar columns (phase 5b); turb_old never
        # enters it (ddt only), mirroring picoext's packed layout.  W = 0
        # reproduces the pre-5b expressions exactly.
        if self._turb_width:
            wlsq_hat_cpd_cell = (
                torch.cat((uvw_new_cpd_cell, p_rgh_new_cpd_cell,
                           t_new_cpd_cell, *turb_new_cpd_cell), dim=-1)
                if _inert else
                torch.cat((uvwpt_hat_cpd_cell, *turb_new_cpd_cell), dim=-1)
            )
        else:
            wlsq_hat_cpd_cell = (
                torch.cat((uvw_new_cpd_cell, p_rgh_new_cpd_cell, t_new_cpd_cell), dim=-1)
                if _inert else uvwpt_hat_cpd_cell
            )

        # Compute WLSQ gradients for INTERIOR cpd cells only (ghost cells get none)
        grad_uvwpt_hat_cpd_cell = gradient_reconstruction(
            order=self.params.fvschemes["gradSchemes"],
            phi_node=wlsq_hat_cpd_cell,
            edge_index=self.neighbor_cpd_cell_x,
            mask_valid_node=self.mask_interior_cell,  # no ghost WLSQ gradients: interior-only solve avoids rank-deficient LU at khops=1; boundary diffusion is snGrad, cyclic ghosts refilled by enforce_cyclic
            node_pos=self.cpd_cell_pos,
            precompute_Moments=[self.A_cell_to_cell, self.single_B_cell_to_cell],
            dim=self.dim,  # Use cached dim value
            # Cell limiting parameters (from fvconfig.toml [fvSchemes] gradLimiter)
            cell_limited=cell_limited,
            limiter_type=limiter_type,
            limiter_k=limiter_k,
            limiter_rt=limiter_rt,
            neighbor_cell=self.neighbor_cpd_cell,
            face_pos=self.face_pos,
            mask_interior_face=self.mask_interior_face,
            mask_interior_cell=self.mask_interior_cell,  # Only limit interior cell gradients
        )[..., 0:3]  # [..., N_cpd_cells, 9_variables, 3_spatial_dimensions]

        if _inert:
            grad_uvwpt_hat_cpd_cell = self._widen_inert_columns(
                grad_uvwpt_hat_cpd_cell, dim=-2)

        # Enforce cyclic boundary conditions on gradients
        # grad_uvwpt_hat_cpd_cell shape: [N_cpd_cells, 9, 3]
        # where 9 = uvw_new(3) + uvw_old(3) + p_rgh(1) + T_new(1) + T_old(1)
        fvfield["grad_uvw_cpd_cell"] = grad_uvwpt_hat_cpd_cell[..., 0:6, :]
        fvfield["grad_p_rgh_cpd_cell"] = grad_uvwpt_hat_cpd_cell[..., 6:7, :]
        fvfield["grad_t_cpd_cell"] = grad_uvwpt_hat_cpd_cell[..., 7:9, :]
        _turb_ch = 9
        for _fname, _w, *_rest in self._turb_transported:
            fvfield[f"grad_{_fname}_cpd_cell"] = (
                grad_uvwpt_hat_cpd_cell[..., _turb_ch:_turb_ch + _w, :])
            _turb_ch += _w
        fvfield = self.enforce_cyclic(fvfield)
        grad_uvwpt_hat_cpd_cell = torch.cat((
            fvfield["grad_uvw_cpd_cell"],
            fvfield["grad_p_rgh_cpd_cell"],
            fvfield["grad_t_cpd_cell"],
            *(fvfield[f"grad_{e[0]}_cpd_cell"] for e in self._turb_transported)
        ), dim=-2)  # [..., N, 9+W, 3]

        # Single unified WLSQ gradient: the former second compact
        # 1-hop gradient for the "leastSquares" tokens was removed. Under the
        # default [stencil] khops=1 the gradient above IS the compact 1-hop
        # reconstruction, so every consumer (diffusion, pressure, value
        # interpolation, LinearUpwind reconstruction, snGrad correction)
        # shares this one computation.
        recon_grad_uvwpt_hat_cpd_cell = grad_uvwpt_hat_cpd_cell
        ''' <<< Gradient Reconstruction <<< '''

        # ONE effective-viscosity read per forward (Turbulence design
        # section 4): diffusion_flux in conserved_form and
        # momentum_diagonal (hence d_f / MWI / preconditioner pressure
        # operator) both read this stash.  Laminar returns self.nu_coeff
        # ITSELF -- the laminar path is structurally bit-identical (hash
        # gate).  Sits AFTER the unified gradient
        # because SST's nu_t reads grad U / grad k /
        # grad wt from the fvfield, which only carries them from here on;
        # position-independent for every other model (no gradient key in
        # their closures), so the heated-cylinder hash gate pins the move.
        self._nu_eff_face = self.turbulence.nu_eff_face(self, fvfield)
        # Refreshed HERE, beside the diffusivity stash, so a swapped-in model
        # (the tests do this) can never desync the gate from the nu_eff the
        # residual consumes.  Laminar: False, dev2 skipped (design 2.5).
        self._dev2_active = (not self.turbulence.is_uniform()
                             or self._laminar_viscous_transpose)
        # THIRD nut consumer (phase 5b, design A.3): the energy equation's
        # effective diffusivity alphaEff = alpha + nut/Prt, from the SAME
        # per-forward nut evaluation as nu_eff (nut_face returns the stash
        # nu_eff_face just filled -- single-computation contract).  Laminar
        # carries no eddy viscosity (nut_face is None), so the stash IS
        # self.alpha_coeff, the same tensor object: the laminar energy
        # equation is structurally unchanged, not merely numerically.
        # Wall faces: the model's alphat wall function, if any (TEqn.H:3's
        # alphat.correctBoundaryConditions(); TurbulenceModel.
        # alphat_wall_face returns its argument itself by default, so this
        # stays alpha + nut_face/Prt bit for bit).
        _nut_face = self.turbulence.nut_face(self, fvfield)
        self._alpha_eff_face = (
            self.alpha_coeff if _nut_face is None
            else self.alpha_coeff + self.turbulence.alphat_wall_face(
                self, fvfield, _nut_face / self.Prt))

        ''' >>> Interpolation to face centers >>> '''
        # Interpolate cell values to face centers
        # uvwpt_hat_cpd_cell: [N_cpd_cells, 9] where 9 = uvw_new(3) + uvw_old(3) + p_rgh(1) + T_new(1) + T_old(1)
        # grad_uvwpt_hat_cpd_cell: [N_cpd_cells, 9, 3]
        wlsq_grad_hat_cpd_cell = (
            self._narrow_inert_columns(grad_uvwpt_hat_cpd_cell, dim=-2)
            if _inert else grad_uvwpt_hat_cpd_cell[..., 0:self._wlsq_width, :]
        )
        uvwpt_hat_face = self.interpolating_phic_to_faces(
            phi_cpd_cell=wlsq_hat_cpd_cell,
            grad_phi_cpd_cell=wlsq_grad_hat_cpd_cell,
            scheme=self.params.fvschemes["interpolationSchemes"],
        )

        # Interpolate gradients to face centers.
        # gradInterpolationSchemes (optional) selects the FACE-GRADIENT
        # interpolation independently of the value interpolation; e.g.
        # "Linear overRelaxed" replaces the face-normal component with the
        # compact two-point difference (OpenFOAM-parity diffusion, MMS-verified
        # in experiments/MMS_SchemeAccuracy). Default: interpolationSchemes.
        grad_uvwpt_hat_face = self.interpolating_gradients_to_faces(
            phi_cpd_cell=wlsq_hat_cpd_cell,
            grad_phi_cpd_cell=wlsq_grad_hat_cpd_cell,
            scheme=self.params.fvschemes.get(
                "gradInterpolationSchemes",
                self.params.fvschemes["interpolationSchemes"],
            ),
        )
        if _inert:
            # Widened here rather than at each consumer: ``conserved_form`` reads
            # this family at fixed nine-wide offsets (``0:3`` and ``7:8``), and a
            # second index convention living beside the first is how a bookkeeping
            # change stops being one.
            uvwpt_hat_face = self._widen_inert_columns(uvwpt_hat_face, dim=-1)
            grad_uvwpt_hat_face = self._widen_inert_columns(
                grad_uvwpt_hat_face, dim=-2)

        # Compute flux values (normal components)
        # uvwpt_hat_face: [N_face, 9] where 9 = uvw_new(3) + uvw_old(3) + p_rgh(1) + T_new(1) + T_old(1)
        uvw_flux_face_new = (uvwpt_hat_face[..., 0:3] * self.unv_face).sum(
            dim=-1, keepdim=True
        )
        if self._momentum_interpolation_mwi:
            # Corrects the ADVECTING flux, so continuity, momentum convection AND
            # the energy equation's convection all see the same face velocity.
            # The energy row is the one place this differs from picoext: div(u T)
            # is built from this same flux, so a correction applied to continuity
            # alone would make the two disagree and the scheme non-conservative.
            # The pressure here is p_rgh, which is why the VARType is passed
            # explicitly -- the boundary-aware snGrad reads a different patch
            # treatment for p and for p_rgh.
            #
            # THE BUOYANCY TERM IS OPENFOAM'S phig AND IT IS NOT OPTIONAL.
            # pEqn.H puts -rAUf*ghf*snGrad(rhok)*magSf into phiHbyA before the
            # pressure solve and takes p_rghEqn.flux() back out after it, so the
            # flux continuity is written on carries both forces.  Ours carried
            # only the pressure at first, and the omission was not
            # cosmetic: dR_continuity/dT was 0.000000e+00 EXACTLY at the
            # converged heated state (dR_momentum/dT = 1.990011e-01 for scale),
            # i.e. the continuity row was blind to temperature.
            #
            # The bracket the base class adds this into is
            # (interp grad - compact snGrad), so what goes in is MINUS the same
            # difference taken on the buoyancy potential: the compact half is
            # phig itself, and the interpolated half plays the role HbyA plays
            # in OpenFOAM, adding back the part of the force already inside the
            # interpolated cell velocities.
            #
            # "Linear" is passed EXPLICITLY for the same reason the pressure
            # half passes it (see momentum_weighted_flux): this case family runs
            # gradInterpolationSchemes = "Linear overRelaxed", whose face-normal
            # component IS the compact snGrad, which would make the bracket
            # cancel identically and the correction silently vanish.
            compact_t, interpolated_t = self._mwi_bracket_gradients(
                uvwpt_hat_cpd_cell[..., 7:8], grad_uvwpt_hat_cpd_cell[..., 7:8, :], VARType.TEMPERATURE)
            buoyancy_bracket = self.gh_face() * self._beta_face() * (compact_t - interpolated_t)
            uvw_flux_face_new = self.momentum_weighted_flux(
                uvw_flux_face=uvw_flux_face_new,
                p_cpd_cell=uvwpt_hat_cpd_cell[..., 6:7],
                grad_p_cpd_cell=grad_uvwpt_hat_cpd_cell[..., 6:7, :],
                var_type=VARType.PRESSURE_RGH,
                body_force_face=buoyancy_bracket,
            )
        if getattr(self, "_conservative_cyclic_flux", False):
            # opt-in: one volumetric flux per periodic face pair -- the continuity rows then
            # sum to zero for every state (BaseSolver.conservative_cyclic_flux); off: the flux tensor unchanged
            uvw_flux_face_new = self.conservative_cyclic_flux(uvw_flux_face_new)
        # The advecting flux this forward used, kept for the preconditioner, which
        # needs the coefficient the RESIDUAL was built from rather than a guess at
        # it.  Detached: it is read by a linear operator, never differentiated.
        self.face_volumetric_flux = uvw_flux_face_new.detach()
        uvw_hat_flux_face = torch.cat(
            (
                uvw_flux_face_new,
                (uvwpt_hat_face[..., 3:6] * self.unv_face).sum(dim=-1, keepdim=True),
            ),
            dim=-1,
        )
        p_rgh_new_flux_face = uvwpt_hat_face[..., 6:7] * self.unv_face
        t_new_flux_face = uvwpt_hat_face[..., 7:8] * self.unv_face
        
        # grad_uvwpt_hat_face: [N_face, 9+W, 3]
        grad_uvwpt_flux_hat_face = (
            grad_uvwpt_hat_face[..., 0:self._wlsq_width, :]
            * self.unv_face.unsqueeze(-2)
        ).sum(dim=-1)
        ''' <<< Interpolation to face centers <<< '''

        ''' >>> Apply mixed boundary conditions >>> '''
        fvfield["uvw_cpd_cell"] = uvw_new_cpd_cell
        fvfield["uvw_old_cpd_cell"] = bc_uvw_old_cpd_cell
        fvfield["p_rgh_cpd_cell"] = p_rgh_new_cpd_cell
        fvfield["t_cpd_cell"] = t_new_cpd_cell
        fvfield["uvw_flux_face"] = uvw_hat_flux_face
        fvfield["p_rgh_flux_face"] = p_rgh_new_flux_face
        fvfield["t_flux_face"] = t_new_flux_face
        fvfield["grad_uvw_flux_face"] = grad_uvwpt_flux_hat_face[..., 0:6]
        fvfield["grad_p_rgh_flux_face"] = grad_uvwpt_flux_hat_face[..., 6:7]
        fvfield["grad_t_flux_face"] = grad_uvwpt_flux_hat_face[..., 7:9]
        # Per-field face-normal gradient fluxes for the transported scalars,
        # so their Neumann-type BCs can hard-zero the boundary diffusion
        # flux exactly as grad_t_flux_face is handled.
        _turb_ch = 9
        for _fname, _w, *_rest in self._turb_transported:
            fvfield[f"grad_{_fname}_flux_face"] = (
                grad_uvwpt_flux_hat_face[..., _turb_ch:_turb_ch + _w])
            _turb_ch += _w

        # Re-apply first-type BC after interpolation so Dirichlet BC can
        # directly overwrite face fields (e.g., p_rgh_flux_face on outlet).
        fvfield = self.enforce_bc_1st(fvfield)

        # Apply mixed boundary conditions (zeroGradient, fixedFluxPressure, etc.)
        # Note: At this stage, flux values will be updated
        fvfield = self.enforce_mixed(fvfield)
        fvfield = self.enforce_coupled(fvfield)

        # Extract updated fields after mixed BC
        uvw_new_cpd_cell = fvfield["uvw_cpd_cell"]
        p_rgh_new_cpd_cell = fvfield["p_rgh_cpd_cell"]
        t_new_cpd_cell = fvfield["t_cpd_cell"]
        uvw_hat_flux_face = fvfield["uvw_flux_face"]
        p_rgh_new_flux_face = fvfield["p_rgh_flux_face"]
        t_new_flux_face = fvfield["t_flux_face"]
        grad_uvwpt_flux_hat_face = torch.cat((
            fvfield["grad_uvw_flux_face"],
            fvfield["grad_p_rgh_flux_face"],
            fvfield["grad_t_flux_face"],
            *(fvfield[f"grad_{e[0]}_flux_face"]
              for e in self._turb_transported)
        ), dim=-1)
        ''' <<< Apply mixed boundary conditions <<< '''

        ''' >>> Apply conserved form of FVM >>> '''
        if self.params.conserved_form:
            loss_total = self.conserved_form(
                # Rebuilt (not the pre-BC locals) because the mixed pass
                # above may have modified cell values; with a transported
                # model the packed layout appends turb_new at 9:9+W and
                # turb_old (ddt only) trailing at 9+W:9+2W.
                uvwpt_hat_cpd_cell=torch.cat(
                    (uvw_new_cpd_cell, uvw_old_cpd_cell, p_rgh_new_cpd_cell,
                     t_new_cpd_cell, t_old_cpd_cell,
                     *(fvfield[f"{e[0]}_cpd_cell"]
                       for e in self._turb_transported),
                     phi_old_cpd_cell[..., 5:5 + self._turb_width])
                    if self._turb_width else
                    (uvw_new_cpd_cell, uvw_old_cpd_cell, p_rgh_new_cpd_cell,
                     t_new_cpd_cell, t_old_cpd_cell),
                    dim=-1
                ),
                uvwpt_hat_face=uvwpt_hat_face,
                uvwpt_flux_hat_face=torch.cat(
                    (uvw_hat_flux_face, p_rgh_new_flux_face, t_new_flux_face),
                    dim=-1,
                ),
                grad_uvwpt_hat_cpd_cell=grad_uvwpt_hat_cpd_cell, # [N_cpd_cells, 9, 3]
                grad_uvwpt_hat_face=grad_uvwpt_hat_face, # [N_face, 9, 3]
                grad_uvwpt_flux_hat_face=grad_uvwpt_flux_hat_face, # [N_face, 9]
                recon_grad_uvwpt_hat_cpd_cell=recon_grad_uvwpt_hat_cpd_cell, # [N_cpd_cells, 9, 3]
            )
        else:
            raise NotImplementedError("Non-conserved form has been removed")
        ''' <<< Apply conserved form of FVM <<< '''

        # 组装应用了边界条件后的解向量
        phi_new_bced_cpd_cell = torch.cat(
            (uvw_new_cpd_cell, p_rgh_new_cpd_cell, t_new_cpd_cell,
             *(fvfield[f"{e[0]}_cpd_cell"]
               for e in self._turb_transported)), dim=-1
        )

        return (
            loss_total,
            phi_new_bced_cpd_cell,
        )
