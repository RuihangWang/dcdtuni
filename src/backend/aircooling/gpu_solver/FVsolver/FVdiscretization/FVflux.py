import sys
import os
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

from typing import Tuple, Optional
import torch
from Utils.utilities import FaceType
from FVsolver.FVdiscretization.FVInterpolation import FV_Interpolation
from torch_geometric.utils import scatter
from FVsolver.FVdiscretization.FVConvection.compressible import (
    compute_cell_lambda as _compute_cell_lambda,
    compute_pressure_sensor as _compute_pressure_sensor,
    compute_undivided_laplacian as _compute_undivided_laplacian,
    create_two_way_boundary_mask as _create_two_way_boundary_mask,
    hllc_riemann_flux,
    jst_flux as _jst_flux,
    primitive_to_conservative as _primitive_to_conservative,
    roe_riemann_flux,
)


def create_two_way_boundary_mask(boundary_mask, sources, targets):
    """Compatibility entry point (rhoco_segment_csr imports this from FVflux)."""
    return _create_two_way_boundary_mask(boundary_mask, sources, targets)

class FV_flux(FV_Interpolation):
    def __init__(self, conserved=True):
        """
        Finite Volume flux calculation class for computing convective, diffusive, and pressure fluxes.
        
        Inherits from FV_Interpolation to provide interpolation functionality for flux calculations.
        Used as a base class for flux-related operations in finite volume discretization.
        
        Args:
            conserved (bool): Flag to indicate if conserved form of equations is used.
                            Default is True.
        """
        super(FV_flux, self).__init__()
        
        self.conserved = conserved

    def convective_flux(
        self,
        uvw_flux_face=None,
        phi_cpd_cell=None,
        grad_phi_cpd_cell=None,
        grad_phi_face=None,
        scheme="Linear"
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        Compute convective flux for momentum equations.

        Calculates the convective flux using velocity values at face centers,
        weighted by convection coefficients. The convection scheme can be
        specified to control the interpolation of velocity across faces.

        Supports "bounded" prefix for improved stability (OpenFOAM-style):
            div(phi,U)_bounded = div(phi,U) - div(phi)*U

        This converts: ∇·(φ·U) → φ·∇U when div(phi) ≠ 0, which is more stable
        during iterative convergence when the velocity field is not yet divergence-free.

        Args:
            uvw_flux_face (torch.Tensor): Normal velocity flux at face centers [N_faces, 1].
            phi_cpd_cell (torch.Tensor): Cell-centered values for phi (e.g., velocity) [N_cells, N_vars].
            grad_phi_cpd_cell (torch.Tensor): Gradient of phi at cell centers [N_cells, N_vars, 3].
            grad_phi_face (torch.Tensor): Gradient of phi at face centers [N_faces, N_vars, 3].
            scheme (str): Convection scheme to use for flux calculation.
                        Options: "Linear", "LinearUpwind", "LimitedLinear k", etc.
                        Prefix with "bounded" for bounded form, e.g., "bounded LinearUpwind"

        Returns:
            Tuple[torch.Tensor, Optional[torch.Tensor]]:
                - convection_flux_face: Convective flux tensor [N_faces, N_vars]
                - bounded_source: Bounded source term [N_cpd_cells, N_vars] if using bounded scheme, else None
                  Note: bounded_source needs to be added to the RHS of the equation (interior cells only)
        """
        # Check for "bounded" prefix
        is_bounded = scheme.lower().startswith("bounded")
        if is_bounded:
            # Extract the underlying scheme (e.g., "bounded LinearUpwind" -> "LinearUpwind")
            underlying_scheme = scheme.split(None, 1)[1] if len(scheme.split()) > 1 else "Linear"
        else:
            underlying_scheme = scheme

        # Compute the convective flux using the specified scheme
        phi_face = self.div_phic_to_faces(
            uvw_flux_face=uvw_flux_face,
            phi_cpd_cell=phi_cpd_cell,
            grad_phi_cpd_cell=grad_phi_cpd_cell,
            grad_phi_face=grad_phi_face,
            scheme=underlying_scheme,
        )

        # Standard convective flux at faces: flux = rho * (u·n) * phi_face
        convection_flux_face = self.convection_coeff * uvw_flux_face * phi_face

        # Compute bounded source term if using bounded scheme
        if is_bounded:
            bounded_source = self.bounded_convection_source(
                uvw_flux_face=uvw_flux_face,
                phi_cpd_cell=phi_cpd_cell,
            )
        else:
            bounded_source = None

        return convection_flux_face, bounded_source

    def scalar_convective_flux(
        self,
        uvw_flux_face: torch.Tensor,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor,
        grad_phi_face: torch.Tensor,
        scheme: str = "Linear"
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        Compute convective flux for scalar fields (e.g., temperature, concentration).

        Similar to convective_flux() but designed for scalar transport equations.
        Supports "bounded" prefix for improved stability.

        Args:
            uvw_flux_face: Normal velocity flux at face centers [N_faces, 1]
            phi_cpd_cell: Cell-centered scalar values [N_cpd_cells, 1]
            grad_phi_cpd_cell: Gradient of scalar at cell centers [N_cpd_cells, 1, 3]
            grad_phi_face: Gradient of scalar at face centers [N_faces, 1, 3]
            scheme: Convection scheme. Prefix with "bounded" for bounded form.

        Returns:
            Tuple[torch.Tensor, Optional[torch.Tensor]]:
                - convection_flux_face: Convective flux [N_faces, 1]
                - bounded_source: Bounded source term [N_cpd_cells, 1] if bounded, else None
        """
        # Check for "bounded" prefix
        is_bounded = scheme.lower().startswith("bounded")
        if is_bounded:
            underlying_scheme = scheme.split(None, 1)[1] if len(scheme.split()) > 1 else "Linear"
        else:
            underlying_scheme = scheme

        # Interpolate scalar to faces using the specified scheme
        phi_face = self.div_phic_to_faces(
            uvw_flux_face=uvw_flux_face,
            phi_cpd_cell=phi_cpd_cell,
            grad_phi_cpd_cell=grad_phi_cpd_cell,
            grad_phi_face=grad_phi_face,
            scheme=underlying_scheme,
        )

        # Convective flux: rho * (u·n) * phi_face
        convection_flux_face = self.convection_coeff * uvw_flux_face * phi_face

        # Compute bounded source term if using bounded scheme
        if is_bounded:
            bounded_source = self.bounded_convection_source(
                uvw_flux_face=uvw_flux_face,
                phi_cpd_cell=phi_cpd_cell,
            )
        else:
            bounded_source = None

        return convection_flux_face, bounded_source

    def bounded_convection_source(
        self,
        uvw_flux_face: torch.Tensor,
        phi_cpd_cell: torch.Tensor,
    ) -> torch.Tensor:
        """
        Compute the bounded convection source term (OpenFOAM-style).

        OpenFOAM implementation (boundedConvectionScheme.C):
            fvcDiv_bounded = fvcDiv(faceFlux, vf) - surfaceIntegrate(faceFlux) * vf

        Where:
            - surfaceIntegrate(faceFlux) = ∑_f(phi_f * S_f) = div(phi) * V
            - faceFlux (phi) is the VOLUMETRIC flux, not mass flux

        This implements: -div(phi) * vf
        Which converts ∇·(φ·vf) → φ·∇vf when div(phi) ≠ 0

        IMPORTANT: Uses volumetric flux (U·S), not mass flux (rho*U·S)
        This matches OpenFOAM's bounded scheme exactly.

        Args:
            uvw_flux_face: Normal velocity at face centers [N_faces, 1] (U·n, m/s)
            phi_cpd_cell: Cell-centered values [N_cpd_cells, N_vars]

        Returns:
            Source term [N_cpd_cells, N_vars] representing -div(phi)*vf
        """
        # Compute volumetric flux at each cell face: phi_f * S_f = (U·n) * |S|
        # Note: directed_area_cells_face already includes the sign
        volumetric_flux_cells_face = (
            uvw_flux_face.index_select(-2, self.cells_face) *
            self.directed_area_cells_face
        )  # [N_cells_faces, 1] - this is phi_f * S_f

        # Sum over faces to get surfaceIntegrate(phi) = div(phi) * V
        # This matches OpenFOAM's fvc::surfaceIntegrate(faceFlux)
        div_phi_volume = scatter(
            volumetric_flux_cells_face,
            self.cells_face_ptr,
            dim=-2,
            dim_size=self.num_cells,
            reduce='sum',
        )  # [N_cells, 1]

        # Compute source term: -surfaceIntegrate(phi) * vf
        # Only apply to interior cells (boundary cells handled separately by BCs)
        # Use torch_geometric scatter (dynamo-compatible) instead of Tensor.scatter
        # which dynamo cannot trace. Pre-cached _interior_cell_indices avoids
        # nonzero() in forward pass (dynamic shapes cause recompilation).
        interior_source = -div_phi_volume * phi_cpd_cell[..., self.mask_interior_cell, :]
        source_term = scatter(
            interior_source,
            self._interior_cell_indices,
            dim=-2,
            dim_size=phi_cpd_cell.shape[-2],
            reduce='sum',
        )

        return source_term
        
    def diffusion_flux(self, grad_uvw_flux_face, diffusivity=None):
        """
        Compute viscous (diffusion) flux for momentum equations.

        Calculates the diffusive flux using velocity gradients at face centers,
        weighted by diffusion (viscosity) coefficients.

        Args:
            grad_uvw_flux_face (torch.Tensor): Surface-normal gradient already
                                            projected onto the face normal
                                            [N_faces, N_vars] (N_vars = 3 for
                                            the momentum components, 1 for a
                                            transported scalar).
            diffusivity (torch.Tensor, optional): Per-face diffusivity
                                            [N_faces, 1].  None (default)
                                            reads ``self.nu_coeff`` -- the
                                            laminar body verbatim.
                                            The turbulence hook passes
                                            ``TurbulenceModel.nu_eff_face``
                                            here; Laminar hands back
                                            ``self.nu_coeff`` ITSELF, so the
                                            laminar product is the identical
                                            op on the identical tensors
                                            (phase-1 hash gate, design doc
                                            section 11).

        Returns:
            torch.Tensor: Viscous flux tensor [N_faces, N_vars].
                         Represents the diffusive momentum flux through each cell face.
        """
        nu_face = self.nu_coeff if diffusivity is None else diffusivity
        vis_flux_face = grad_uvw_flux_face * nu_face

        return vis_flux_face

    def continuity_flux(self, uvw_flux_face):
        """
        Compute the per-face mass flux entering the continuity residual.

        Scales the face-normal volumetric flux by the continuity coefficient and
        expands it to the (cell, face) incidence layout, so the caller can scatter
        it per cell (divergence theorem) to form the continuity residual.

        Args:
            uvw_flux_face (torch.Tensor): Face-normal volumetric flux [N_faces, 1]
                                       (a scalar per face, NOT the u, v, w vector).

        Returns:
            torch.Tensor: Mass flux per (cell, face) incidence [N_cells_face, 1].
        """
        # Compute mass flux through each face: velocity · surface_vector
        mass_flux_cells_face = (
            self.continuity_eq_coeff[self.batch_face] * uvw_flux_face
        ).index_select(-2, self.cells_face)

        return mass_flux_cells_face

    def roe_riemann_solver(self, rho_L, uvw_L, p_L, rho_R, uvw_R, p_R, face_normal, gamma=1.4):
        """Delegates to FVConvection.compressible.roe_riemann_flux."""
        return roe_riemann_flux(rho_L, uvw_L, p_L, rho_R, uvw_R, p_R, face_normal, gamma=gamma)

    def hllc_riemann_solver(self, rho_L, uvw_L, p_L, rho_R, uvw_R, p_R, face_normal, R=287.0, gamma=1.4):
        """Delegates to FVConvection.compressible.hllc_riemann_flux."""
        return hllc_riemann_flux(rho_L, uvw_L, p_L, rho_R, uvw_R, p_R, face_normal, R=R, gamma=gamma)

    def jst_flux(self, rho_L, uvw_L, p_L, rho_R, uvw_R, p_R,
                 face_normal, sensor_L, sensor_R, laplacian_U_L, laplacian_U_R,
                 num_neighbors_L, num_neighbors_R,
                 lambda_L=None, lambda_R=None, stretch_param=0.3,
                 kappa_2=0.5, kappa_4=0.02, gamma=1.4, R=287.0,
                 face_area=None):
        """Delegates to FVConvection.compressible.jst_flux."""
        return _jst_flux(rho_L, uvw_L, p_L, rho_R, uvw_R, p_R,
                         face_normal, sensor_L, sensor_R, laplacian_U_L, laplacian_U_R,
                         num_neighbors_L, num_neighbors_R,
                         lambda_L=lambda_L, lambda_R=lambda_R, stretch_param=stretch_param,
                         kappa_2=kappa_2, kappa_4=kappa_4, gamma=gamma, R=R,
                         face_area=face_area)

    def compute_pressure_sensor(self, p_cpd_cell: torch.Tensor) -> torch.Tensor:
        """Delegates to FVConvection.compressible.compute_pressure_sensor."""
        return _compute_pressure_sensor(
            p_cpd_cell,
            neighbor_cpd_cell=self.neighbor_cpd_cell,
            mask_boundary_cell=self.mask_boundary_cell,
            num_cpd_cells=self.num_cpd_cells,
            mask_non_empty_face=getattr(self, "mask_non_empty_face", None),
        )

    def compute_undivided_laplacian(self, U_cpd_cell: torch.Tensor, rhoH_cpd_cell: torch.Tensor = None) -> torch.Tensor:
        """Delegates to FVConvection.compressible.compute_undivided_laplacian."""
        return _compute_undivided_laplacian(
            U_cpd_cell, rhoH_cpd_cell,
            neighbor_cpd_cell=self.neighbor_cpd_cell,
            mask_boundary_cell=self.mask_boundary_cell,
            num_cpd_cells=self.num_cpd_cells,
            mask_non_empty_face=getattr(self, "mask_non_empty_face", None),
        )

    def compute_cell_lambda(self, rho_cpd_cell: torch.Tensor, uvw_cpd_cell: torch.Tensor, p_cpd_cell: torch.Tensor) -> torch.Tensor:
        """Delegates to FVConvection.compressible.compute_cell_lambda."""
        return _compute_cell_lambda(
            rho_cpd_cell, uvw_cpd_cell, p_cpd_cell,
            neighbor_cpd_cell=self.neighbor_cpd_cell,
            num_cpd_cells=self.num_cpd_cells,
            face_area=self.face_area,
            unv_face=self.unv_face,
            sound_speed=self.sound_speed,
            mask_non_empty_face=getattr(self, "mask_non_empty_face", None),
        )

    def primitive_to_conservative(
            self,
            rho: torch.Tensor,
            uvw: torch.Tensor,
            p: torch.Tensor
        ) -> torch.Tensor:
        """Delegates to FVConvection.compressible.primitive_to_conservative."""
        return _primitive_to_conservative(rho, uvw, p, gamma=self.gamma)

    def compute_convective_flux(
        self,
        rho_uvw_p_cpd_cell: torch.Tensor,
        owner_cells: torch.Tensor,
        neighbor_cells: torch.Tensor,
    ) -> tuple:
        """
        Compute convective fluxes for compressible Euler equations using JST or HLLC schemes.

        This is a high-level interface that:
        1. Extracts primitive variables at cell neighbors
        2. Computes necessary auxiliary quantities (pressure sensor, Laplacian, etc.) for JST
        3. Calls the appropriate flux function (JST or HLLC)

        Args:
            rho_uvw_p_cpd_cell: Primitive variables at all cpd cells [N_cpd_cells, 5]
                               Contains [ρ, u, v, w, p]
            owner_cells: Owner cell indices for each face [N_faces]
            neighbor_cells: Neighbor cell indices for each face [N_faces]

        Returns:
            Tuple of (mass_flux, momentum_flux, energy_flux):
                - mass_flux: Mass flux at each face [N_faces, 1]
                - momentum_flux: Momentum flux at each face [N_faces, 3]
                - energy_flux: Energy flux at each face [N_faces, 1]
        """
        # Extract left (owner) and right (neighbor) states
        owner_state = rho_uvw_p_cpd_cell.index_select(-2, owner_cells)
        neighbor_state = rho_uvw_p_cpd_cell.index_select(-2, neighbor_cells)
        rho_L = owner_state[..., 0:1]
        uvw_L = owner_state[..., 1:4]
        p_L = owner_state[..., 4:5]

        rho_R = neighbor_state[..., 0:1]
        uvw_R = neighbor_state[..., 1:4]
        p_R = neighbor_state[..., 4:5]

        # JST scheme: central differencing with artificial dissipation
        if self.conv_num_method.upper() == "JST":
            # Compute pressure sensor for shock detection
            pressure_sensor = self.compute_pressure_sensor(
                p_cpd_cell=rho_uvw_p_cpd_cell[..., 4:5]
            )

            # Convert primitive to conservative variables
            U_cpd_cell = self.primitive_to_conservative(
                rho_uvw_p_cpd_cell[..., 0:1],
                rho_uvw_p_cpd_cell[..., 1:4],
                rho_uvw_p_cpd_cell[..., 4:5]
            )

            # Compute ρH for Laplacian (consistent with diffU in jst_flux)
            # H = E + p/ρ = γ/(γ-1) * p/ρ + 0.5*|u|^2
            # ρH = γ/(γ-1) * p + 0.5 * ρ * |u|^2
            rho_cpd = rho_uvw_p_cpd_cell[..., 0:1]
            uvw_cpd = rho_uvw_p_cpd_cell[..., 1:4]
            p_cpd = rho_uvw_p_cpd_cell[..., 4:5]
            # Use solver helper for safety clamps if available
            if hasattr(self, "sound_speed") and hasattr(self, "_safe_rho_p"):
                rho_safe, p_safe = self._safe_rho_p(rho_cpd, p_cpd)
            else:
                rho_safe = torch.clamp(torch.abs(rho_cpd), min=1e-12)
                p_safe = torch.clamp(p_cpd, min=1e-12)
            u_sq = torch.sum(uvw_cpd**2, dim=-1, keepdim=True)
            e_cpd = p_safe / ((self.gamma - 1.0) * rho_safe)  # Internal energy per unit mass
            E_cpd = e_cpd + 0.5 * u_sq  # Total energy per unit mass
            H_cpd = E_cpd + p_safe / rho_safe  # Enthalpy per unit mass
            rhoH_cpd_cell = rho_cpd * H_cpd  # ρH [N_cpd_cells, 1]

            # Compute cell-based lambda for correctedSpectralRadius (SU2: common.hpp:418-432)
            lambda_cpd_cell = self.compute_cell_lambda(rho_cpd, uvw_cpd, p_cpd)

            # Compute undivided Laplacian for 4th-order dissipation
            laplacian = self.compute_undivided_laplacian(
                U_cpd_cell,
                rhoH_cpd_cell=rhoH_cpd_cell
            )

            # Extract face-neighbor values
            sensor_L = pressure_sensor.index_select(-2, owner_cells)
            sensor_R = pressure_sensor.index_select(-2, neighbor_cells)
            laplacian_L = laplacian.index_select(-2, owner_cells)
            laplacian_R = laplacian.index_select(-2, neighbor_cells)
            num_neighbors_L = self.num_neighbors_cpd_cell[owner_cells]
            num_neighbors_R = self.num_neighbors_cpd_cell[neighbor_cells]
            lambda_L = lambda_cpd_cell.index_select(-2, owner_cells)
            lambda_R = lambda_cpd_cell.index_select(-2, neighbor_cells)

            # Compute JST flux
            mass_flux, momentum_flux, energy_flux = self.jst_flux(
                rho_L, uvw_L, p_L,
                rho_R, uvw_R, p_R,
                self.unv_face,
                sensor_L, sensor_R,
                laplacian_L, laplacian_R,
                num_neighbors_L, num_neighbors_R,
                lambda_L=lambda_L,
                lambda_R=lambda_R,
                stretch_param=self.jst_stretch_param,
                kappa_2=self.jst_kappa_2,
                kappa_4=self.jst_kappa_4,
                gamma=self.gamma,
                R=self.R,
                face_area=self.face_area  # Required for correctedSpectralRadius
            )

        # Roe Riemann solver: characteristic upwinding with entropy fix.
        elif self.conv_num_method.upper() == "ROE":
            mass_flux, momentum_flux, energy_flux = self.roe_riemann_solver(
                rho_L, uvw_L, p_L,
                rho_R, uvw_R, p_R,
                self.unv_face,
                gamma=self.gamma,
            )

        # HLLC Riemann solver: upwind scheme with contact wave resolution.
        # Keep the historical fallback to HLLC for unrecognized method names.
        else:
            mass_flux, momentum_flux, energy_flux = self.hllc_riemann_solver(
                rho_L, uvw_L, p_L,
                rho_R, uvw_R, p_R,
                self.unv_face,
                gamma=self.gamma,
                R=self.R
            )

        return mass_flux, momentum_flux, energy_flux
