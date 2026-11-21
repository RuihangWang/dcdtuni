import sys
import os
from typing import Tuple, Optional, Any
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

import torch
from torch_scatter import scatter_add
from FVsolver.FVdiscretization.FVflux import FV_flux
from FVsolver.FVdiscretization.FVgrad import gradient_reconstruction
from torch_geometric.nn import global_add_pool

class Intergrator(FV_flux):
    """
    Finite Volume integrator class for assembling and solving discretized equations.

    Inherits from FV_flux to provide flux calculation capabilities.
    Handles both conserved and non-conserved forms of the Navier-Stokes equations.
    """

    def __init__(self, params: Any, fv_graph: Any = None) -> None:
        """
        Initialize the compressible finite volume integrator.

        Args:
            params: Configuration object containing discretization parameters,
                   time integration scheme, and equation form settings.
            fv_graph: Graph object containing mesh geometry and connectivity.
        """
        super(Intergrator, self).__init__()
        self.params = params
        
        # Set default gas properties for compressible flow
        self.gas_constant = getattr(params, 'gas_constant', 287.0)  # J/(kg·K) for air
        self.gamma = getattr(params, 'gamma', 1.4)  # specific heat ratio for air
        self.prandtl_number = getattr(params, 'prandtl_number', 0.72)  # Prandtl number for air
        
        # allow registering geometry after initialization
        if fv_graph is not None:
            self.register_geometrics(fv_graph.graph_node, fv_graph.graph_face, fv_graph.graph_cell, fv_graph.graph_cell_x, fv_graph.graph_Index)
    
    def compressible_convective_flux(self, rho_face, uvw_face, phi_cpd_cell, grad_phi_cpd_cell, grad_phi_face, scheme="LimitedLinearV 1"):
        """
        Compute convective flux for compressible flow: ρu⊗φ
        
        Args:
            rho_face: Density at faces [N_faces, 1]
            uvw_face: Velocity at faces [N_faces, 3]
            phi_cpd_cell: Scalar field at cells [N_cells, N_vars]
            grad_phi_cpd_cell: Gradient of scalar field at cells [N_cells, N_vars, 3]
            grad_phi_face: Gradient of scalar field at faces [N_faces, N_vars, 3]
            scheme: Discretization scheme
            
        Returns:
            Convective flux [N_faces, N_vars]
        """
        # For now, use the base class convective flux and multiply by density
        # This is a simplified implementation - may need more sophisticated treatment
        base_flux = self.convective_flux(
            uvw_face=uvw_face,
            phi_cpd_cell=phi_cpd_cell,
            grad_phi_cpd_cell=grad_phi_cpd_cell,
            grad_phi_face=grad_phi_face,
            scheme=scheme
        )
        
        # Multiply by density for compressible flow
        return rho_face * base_flux
    
    def energy_convective_flux(self, rho_face, uvw_face, t_face, phi_cpd_cell, grad_phi_cpd_cell, grad_phi_face, scheme="LimitedLinearV 1"):
        """
        Compute energy convective flux: ρu·T (simplified energy equation)
        
        Args:
            rho_face: Density at faces [N_faces, 1]
            uvw_face: Velocity at faces [N_faces, 3]  
            t_face: Temperature at faces [N_faces, 1]
            phi_cpd_cell: Temperature at cells [N_cells, 1]
            grad_phi_cpd_cell: Temperature gradient at cells [N_cells, 1, 3]
            grad_phi_face: Temperature gradient at faces [N_faces, 1, 3]
            scheme: Discretization scheme
            
        Returns:
            Energy convective flux [N_faces, 1]
        """
        # Compute convective flux for temperature/energy
        temp_flux = self.convective_flux(
            uvw_face=uvw_face,
            phi_cpd_cell=phi_cpd_cell,
            grad_phi_cpd_cell=grad_phi_cpd_cell,
            grad_phi_face=grad_phi_face,
            scheme=scheme
        )
        
        # Multiply by density for compressible energy transport
        return rho_face * temp_flux
    
    def heat_diffusion_flux(self, grad_t_face):
        """
        Compute heat conduction flux: -k∇T (Fourier's law)
        
        Args:
            grad_t_face: Temperature gradient at faces [N_faces, 1, 3]
            
        Returns:
            Heat diffusion flux [N_faces, 1]
        """
        # Simplified implementation - assumes constant thermal conductivity
        # In practice, thermal conductivity may depend on temperature
        thermal_conductivity = getattr(self.params, 'thermal_conductivity', 0.02624)  # W/(m·K) for air at 300K
        
        # Compute magnitude of temperature gradient
        grad_t_magnitude = torch.norm(grad_t_face.squeeze(-1), dim=-1, keepdim=True)
        
        # Apply Fourier's law: q = -k∇T
        return thermal_conductivity * grad_t_magnitude
        
    def continuity_flux(self, uvw_face, rho_face=None):
        """
        Override continuity flux for compressible flow: ρu·n
        
        Args:
            uvw_face: Velocity at faces [N_faces, 3]
            rho_face: Density at faces [N_faces, 1] (for compressible flow)
            
        Returns:
            Mass flux [N_faces, 1]
        """
        if rho_face is not None:
            # For compressible flow: mass flux = ρu·n
            return rho_face * super().continuity_flux(uvw_face)
        else:
            # Fall back to incompressible case
            return super().continuity_flux(uvw_face)

    def enforce_boundary_condition(self, rho_uvw_t_p_new_non_cyclic_cell: torch.Tensor) -> torch.Tensor:
        """
        Apply boundary conditions to the new solution vector for compressible flow.

        Args:
            rho_uvw_t_p_new_non_cyclic_cell: New solution vector at cell centers [N_non_cyclic_cells, 6]
                                            containing [ρ, u, v, w, T, p].
        Returns:
            rho_uvw_t_p_new_cpd_cell: New solution vector with boundary conditions applied [N_cpd_cells, 6].
        """

        if rho_uvw_t_p_new_non_cyclic_cell.shape[0] != self.num_cpd_cells:
            rho_uvw_t_p_new_cpd_cell = torch.zeros((self.num_cpd_cells, rho_uvw_t_p_new_non_cyclic_cell.shape[1]), 
                                                  device=rho_uvw_t_p_new_non_cyclic_cell.device)
            rho_uvw_t_p_new_cpd_cell[self.mask_non_cyclic_cell] = rho_uvw_t_p_new_non_cyclic_cell
        else:
            rho_uvw_t_p_new_cpd_cell = rho_uvw_t_p_new_non_cyclic_cell

        # 最先施加周期性边界条件
        if self.mask_valid_cyclic_face.any():
            rho_uvw_t_p_new_cpd_cell[self.patch1_cpd_cells] = rho_uvw_t_p_new_cpd_cell[self.patch2_cells]
            rho_uvw_t_p_new_cpd_cell[self.patch2_cpd_cells] = rho_uvw_t_p_new_cpd_cell[self.patch1_cells]

        # 然后施加密度的第一类边界条件（入口）
        if hasattr(self, 'mask_dirichlet_cell') and hasattr(self, 'dirichlet_y'):
            if self.dirichlet_y.shape[1] >= 6:  # Ensure boundary conditions include all 6 variables
                rho_uvw_t_p_new_cpd_cell[self.mask_dirichlet_cell, 0:1] = self.dirichlet_y[:, 0:1]  # ρ
                rho_uvw_t_p_new_cpd_cell[self.mask_dirichlet_cell, 1:4] = self.dirichlet_y[:, 1:4]  # u,v,w
                rho_uvw_t_p_new_cpd_cell[self.mask_dirichlet_cell, 4:5] = self.dirichlet_y[:, 4:5]  # T
                # 压力通常在出口边界条件中处理，或通过状态方程计算
    
        # 可压缩流动的压力可以通过状态方程计算: p = ρ * R * T
        # 这里假设理想气体状态方程，R是气体常数
        if hasattr(self, 'gas_constant'):
            # 对于理想气体: p = ρRT
            rho_uvw_t_p_new_cpd_cell[:, 5:6] = (rho_uvw_t_p_new_cpd_cell[:, 0:1] * 
                                              self.gas_constant * 
                                              rho_uvw_t_p_new_cpd_cell[:, 4:5])
    
        return rho_uvw_t_p_new_cpd_cell
    
    def time_scheme(self, rho_uvw_t_old_cell: torch.Tensor, rho_uvw_t_new_cell: torch.Tensor) -> torch.Tensor:
        """
        Apply temporal discretization scheme for compressible flow time integration.

        Computes the unsteady terms for the conserved variables in compressible flow:
        - Mass conservation: ∂ρ/∂t
        - Momentum conservation: ∂(ρu)/∂t, ∂(ρv)/∂t, ∂(ρw)/∂t  
        - Energy conservation: ∂(ρE)/∂t or ∂(ρT)/∂t

        Args:
            rho_uvw_t_old_cell: Previous time step solution [N_cells, 5] containing [ρ, u, v, w, T].
            rho_uvw_t_new_cell: Current time step solution [N_cells, 5] containing [ρ, u, v, w, T].

        Returns:
            unsteady_cell: Unsteady term contribution [N_interior_cells, 5] for [ρ, ρu, ρv, ρw, ρT].
        """
        # Validate input dimensions
        if rho_uvw_t_old_cell.shape != rho_uvw_t_new_cell.shape:
            raise ValueError(f"Shape mismatch: rho_uvw_t_old_cell {rho_uvw_t_old_cell.shape} vs rho_uvw_t_new_cell {rho_uvw_t_new_cell.shape}")
        
        if rho_uvw_t_new_cell.shape[1] != 5:
            raise ValueError(f"Expected 5 components [ρ, u, v, w, T], got {rho_uvw_t_new_cell.shape[1]}")

        # Extract variables for clarity
        rho_new = rho_uvw_t_new_cell[self.mask_interior_cell, 0:1]  # density
        uvw_new = rho_uvw_t_new_cell[self.mask_interior_cell, 1:4]  # velocities
        t_new = rho_uvw_t_new_cell[self.mask_interior_cell, 4:5]    # temperature
        
        rho_old = rho_uvw_t_old_cell[self.mask_interior_cell, 0:1]
        uvw_old = rho_uvw_t_old_cell[self.mask_interior_cell, 1:4]
        t_old = rho_uvw_t_old_cell[self.mask_interior_cell, 4:5]

        # Compute conserved variables for current and previous time steps
        # Mass: ρ
        # Momentum: ρu, ρv, ρw  
        # Energy (simplified as ρT): ρT
        
        conserved_new = torch.cat([
            rho_new,                    # Mass: ρ
            rho_new * uvw_new,         # Momentum: ρu, ρv, ρw
            rho_new * t_new            # Energy (simplified): ρT
        ], dim=1)
        
        conserved_old = torch.cat([
            rho_old,                    # Mass: ρ  
            rho_old * uvw_old,         # Momentum: ρu, ρv, ρw
            rho_old * t_old            # Energy (simplified): ρT
        ], dim=1)

        # Compute unsteady terms: (U_new - U_old)/dt * V
        # where U represents the conserved variables [ρ, ρu, ρv, ρw, ρT]
        unsteady_cell = (
            (conserved_new - conserved_old) / self.dt_cell
        ) * self.cells_volume * self.unsteady_coeff

        # Initialize source term if not exists
        if not hasattr(self, 'source_term'):
            self.source_term = torch.zeros_like(unsteady_cell)
        
        # Add source term contribution (explicit part)
        # For compressible flow, this might include body forces, heat sources, etc.
        # self.source_term += additional_source_terms

        return unsteady_cell
    
    def pressure_possion(
        self,
        uvwp_hat_face: Optional[torch.Tensor] = None,
        grad_uvwp_hat_face: Optional[torch.Tensor] = None,
    ):
        """
        Coumpute the residual of the pressure possion equation
        input: uvwp_hat_face, grad_uvwp_hat_face
        Return: Loss_possion
        """
        grad_p_flux = grad_uvwp_hat_face[:,6:7] * self.grad_p_coeff[:,None]
        u_dot_grad_u = uvwp_hat_face[:,None,0:3] @ grad_uvwp_hat_face[:,0:3,:]

        J_flux_cells_face = torch.matmul(
            (grad_p_flux+u_dot_grad_u)[self.cells_face], self.cells_face_surface_vec.unsqueeze(-1)
        ).squeeze(2)
        
        Loss_possion = torch.sqrt(
            global_add_pool(
                scatter_add(
                    src=J_flux_cells_face, 
                    index=self.cells_face_ptr, 
                    dim=0, 
                    dim_size=self.num_cells
                )**2, 
                batch=self.batch_cell
            )
        ).sum(dim=1,keepdim=True)
        
        return Loss_possion
        
    def conserved_form(
        self,
        unsteady_cell: torch.Tensor,
        rho_uvw_t_p_new_cpd_cell: torch.Tensor,
        rho_uvw_t_p_hat_cpd_cell: Optional[torch.Tensor] = None,
        grad_rho_uvw_t_p_hat_cpd_cell: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Evaluate the conserved form of the compressible flow governing equations.

        Computes the residuals for mass, momentum, and energy conservation equations
        using the cell-centered and face-centered values for compressible flow.

        Args:
            unsteady_cell: Unsteady term contribution [N_interior_cells, 5] for [ρ, ρu, ρv, ρw, ρT].
            rho_uvw_t_p_new_cpd_cell: Solution vector [N_cpd_cells, 6] containing [ρ, u, v, w, T, p].
            rho_uvw_t_p_hat_cpd_cell: High-order interpolated values [N_cpd_cells, N_vars].
            grad_rho_uvw_t_p_hat_cpd_cell: High-order gradients [N_cpd_cells, N_vars, 3].

        Returns:
            Tuple containing:
                - loss_total: Combined weighted loss from all conservation equations.
                - rho_uvw_t_p_new_smoothed_cpd_cell: Updated and smoothed solution vector.
        """

        ''' >>> Interpolation >>> '''
        # Interpolate cell values to face centers using central difference + skewness correction
        rho_uvw_t_p_hat_face = self.interpolating_phic_to_faces(
            phi_cpd_cell=rho_uvw_t_p_hat_cpd_cell, 
            grad_phi_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell, 
            scheme="Linear corrected",
        )
        
        # Interpolate gradients to face centers
        grad_rho_uvw_t_p_hat_face = self.interpolating_gradients_to_faces(
            phi_cpd_cell=rho_uvw_t_p_hat_cpd_cell, 
            grad_phi_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell,
            presssure_location_dim=5,  # pressure is at index 5 for compressible flow
        )
        ''' <<< Interpolation <<< '''

        # Extract face values for clarity
        rho_face = rho_uvw_t_p_hat_face[:, 0:1]      # density
        uvw_face = rho_uvw_t_p_hat_face[:, 1:4]      # velocities
        t_face = rho_uvw_t_p_hat_face[:, 4:5]        # temperature  
        p_face = rho_uvw_t_p_hat_face[:, 5:6]        # pressure

        ''' >>> Mass Conservation (Continuity) >>> '''
        # Mass flux: ρu·n (mass flow through faces)
        mass_flux_cells_face = self.continuity_flux(
            uvw_face=uvw_face, 
            rho_face=rho_face  # Include density for compressible flow
        )
        
        ''' >>> Momentum Conservation >>> '''
        if self.params.integrator == "implicit":
            # Convective flux for momentum: ρu⊗u (momentum transport)
            momentum_convection_flux_face = self.compressible_convective_flux(
                rho_face=rho_face,
                uvw_face=uvw_face,
                phi_cpd_cell=rho_uvw_t_p_new_cpd_cell[:, 1:4],  # momentum variables (velocities)
                grad_phi_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell[:, 1:4],
                grad_phi_face=grad_rho_uvw_t_p_hat_face[:, 1:4],
                scheme="LimitedLinearV 1"
            )
        elif self.params.integrator == "imex":
            # For IMEX scheme, blend current and previous momentum fluxes
            momentum_convection_flux_face = 0.5 * (
                self.compressible_convective_flux(
                    rho_face=rho_face,
                    uvw_face=uvw_face,
                    phi_cpd_cell=rho_uvw_t_p_new_cpd_cell[:, 1:4],
                    grad_phi_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell[:, 1:4],
                    grad_phi_face=grad_rho_uvw_t_p_hat_face[:, 1:4],
                    scheme="LimitedLinearV 1"
                ) +
                self.compressible_convective_flux(
                    rho_face=rho_uvw_t_p_hat_face[:, 6:7] if rho_uvw_t_p_hat_face.shape[1] > 6 else rho_face,
                    uvw_face=rho_uvw_t_p_hat_face[:, 7:10] if rho_uvw_t_p_hat_face.shape[1] > 9 else uvw_face,
                    phi_cpd_cell=rho_uvw_t_p_hat_cpd_cell[:, 7:10] if rho_uvw_t_p_hat_cpd_cell.shape[1] > 9 else rho_uvw_t_p_new_cpd_cell[:, 1:4],
                    grad_phi_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell[:, 7:10] if grad_rho_uvw_t_p_hat_cpd_cell.shape[1] > 9 else grad_rho_uvw_t_p_hat_cpd_cell[:, 1:4],
                    grad_phi_face=grad_rho_uvw_t_p_hat_face[:, 7:10] if grad_rho_uvw_t_p_hat_face.shape[1] > 9 else grad_rho_uvw_t_p_hat_face[:, 1:4],
                    scheme="LimitedLinearV 1"
                )
            )
        else:
            raise ValueError(f"Unknown integrator type: {self.params.integrator}")

        # Pressure flux for momentum equations: ∇p
        pressure_flux_face = self.pressure_flux(p_new_face=p_face)
        pressure_flux_face = self.apply_pressure_outletBC(pressure_flux_face, grad_rho_uvw_t_p_hat_face[:, 1:4])
        
        # Viscous flux for momentum equations: ∇·τ (viscous stress)
        viscous_flux_face = self.diffusion_flux(grad_uvw_face=grad_rho_uvw_t_p_hat_face[:, 1:4])
        
        ''' >>> Energy Conservation >>> '''
        # Energy convection: ρuh (where h is specific enthalpy or temperature)
        energy_convection_flux_face = self.energy_convective_flux(
            rho_face=rho_face,
            uvw_face=uvw_face, 
            t_face=t_face,
            phi_cpd_cell=rho_uvw_t_p_new_cpd_cell[:, 4:5],  # temperature
            grad_phi_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell[:, 4:5],
            grad_phi_face=grad_rho_uvw_t_p_hat_face[:, 4:5],
            scheme="LimitedLinearV 1"
        )
        
        # Heat conduction flux: -k∇T (Fourier's law)
        heat_conduction_flux_face = self.heat_diffusion_flux(grad_t_face=grad_rho_uvw_t_p_hat_face[:, 4:5])
        
        ''' >>> Total flux assembly >>> '''
        # Assemble fluxes for each conservation equation
        mass_flux_cells_face_assembled = torch.matmul(
            mass_flux_cells_face[self.cells_face],
            self.cells_face_surface_vec.unsqueeze(-1)
        ).squeeze()
        
        momentum_flux_cells_face_assembled = torch.matmul(
            (momentum_convection_flux_face + pressure_flux_face - viscous_flux_face)[self.cells_face],
            self.cells_face_surface_vec.unsqueeze(-1)
        ).squeeze()
        
        energy_flux_cells_face_assembled = torch.matmul(
            (energy_convection_flux_face - heat_conduction_flux_face)[self.cells_face],
            self.cells_face_surface_vec.unsqueeze(-1)
        ).squeeze()

        # Assemble total right-hand side by scattering face fluxes to cells
        total_RHS = scatter_add(
            torch.cat([
                mass_flux_cells_face_assembled,      # Mass conservation
                momentum_flux_cells_face_assembled,  # Momentum conservation (3 components)
                energy_flux_cells_face_assembled     # Energy conservation
            ], dim=1),
            self.cells_face_ptr,
            dim=0,
            dim_size=self.num_cells,
        )
        
        ''' >>> Compute residuals for each conservation equation >>> '''
        # Mass conservation residual
        loss_mass = torch.sqrt(
            global_add_pool( 
                (total_RHS[:, 0:1])**2, batch=self.batch_cell
            )
        ).sum(dim=1, keepdim=True)
        
        # Momentum conservation residual  
        loss_momentum = torch.sqrt(
            global_add_pool(
                (unsteady_cell[:, 1:4] + total_RHS[:, 1:4] - self.source_term[:, 1:4] if hasattr(self, 'source_term') else unsteady_cell[:, 1:4] + total_RHS[:, 1:4])**2, 
                batch=self.batch_cell
            )
        ).sum(dim=1, keepdim=True)
        
        # Energy conservation residual
        loss_energy = torch.sqrt(
            global_add_pool(
                (unsteady_cell[:, 4:5] + total_RHS[:, 4:5] - self.source_term[:, 4:5] if hasattr(self, 'source_term') and self.source_term.shape[1] > 4 else unsteady_cell[:, 4:5] + total_RHS[:, 4:5])**2,
                batch=self.batch_cell
            )
        ).sum(dim=1, keepdim=True)
        
        ''' >>> Jump penalty terms >>> '''
        if self.params.loss_phi_jump == 0.:
            loss_phi_jump = torch.zeros((self.num_graphs, 1), device=rho_uvw_t_p_hat_cpd_cell.device)
        else:
            # Compute jump in scalar values across cell faces for all variables
            loss_phi_jump = torch.sqrt(
                global_add_pool(
                    self.phi_jump(
                        phi_cpd_cell=rho_uvw_t_p_hat_cpd_cell, 
                        grad_phi_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell,
                    )**2, 
                    batch=self.batch_cell
                )
            ).sum(dim=1, keepdim=True)

        # Store individual loss components
        self.loss_mass_component = loss_mass
        self.loss_momentum_component = loss_momentum  
        self.loss_energy_component = loss_energy
        self.loss_phi_jump_component = loss_phi_jump
        
        # Compute combined weighted loss
        loss_total = torch.mean(torch.log(
            self.params.loss_cont * loss_mass +
            self.params.loss_mom * loss_momentum +
            getattr(self.params, 'loss_energy', 1.0) * loss_energy +
            self.params.loss_phi_jump * loss_phi_jump
        ))

        # Apply smoothing if enabled
        if self.params.cfc_smooth:
            rho_uvw_t_p_new_smoothed_cpd_cell = self.cfc_smooth(
                uvwp_face=rho_uvw_t_p_hat_face,
            )
        else:
            rho_uvw_t_p_new_smoothed_cpd_cell = rho_uvw_t_p_new_cpd_cell
            
        return loss_total, rho_uvw_t_p_new_smoothed_cpd_cell

    def get_separated_losses(self):
        """
        Returns the individual loss components for compressible flow
        """
        if (hasattr(self, 'loss_mass_component') and 
            hasattr(self, 'loss_momentum_component') and 
            hasattr(self, 'loss_energy_component')):
            return (self.loss_mass_component, self.loss_momentum_component, 
                   self.loss_energy_component, self.loss_phi_jump_component)
        else:
            # Fallback: return None if components not available
            return None, None, None, None

    def forward(
        self,
        phi_old_cpd_cell: Optional[torch.Tensor] = None,
        phi_new_non_cyclic_cell: Optional[torch.Tensor] = None,
        rho_dim: int = 0,
        vel_dim: Tuple[int, int] = (1, 4),
        press_dim: int = 5,
        temp_dim: int = 4,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass for the compressible finite volume integrator.

        Performs gradient reconstruction, applies boundary conditions, computes temporal
        discretization, and evaluates the conserved form of the compressible flow equations.

        Args:
            phi_old_cpd_cell: Previous time step solution vector [N_cpd_cells, 6].
                            Contains [ρ, u, v, w, T, p] from previous time step.
            phi_new_non_cyclic_cell: Current time step solution vector [N_non_cyclic_cells, 6].
                                   Contains [ρ, u, v, w, T, p] for current iteration.
            rho_dim: Dimension index for density (default: 0).
            vel_dim: Tuple indicating start and end indices for velocity components (default: (1,4) for u,v,w).
            temp_dim: Dimension index for temperature (default: 4).
            press_dim: Dimension index for pressure (default: 5).
            
        Returns:
            Tuple containing:
                - loss_total: Combined weighted loss from mass, momentum, energy conservation and jump penalty terms.
                - rho_uvw_t_p_new_smoothed_cpd_cell: Updated and smoothed solution vector with boundary conditions applied.
        """
        
        # Input validation
        if phi_old_cpd_cell is None or phi_new_non_cyclic_cell is None:
            raise ValueError("Both phi_old_cpd_cell and phi_new_non_cyclic_cell must be provided")
        
        # Validate that we have 6 variables for compressible flow [ρ, u, v, w, T, p]
        expected_vars = 6
        if phi_old_cpd_cell.shape[1] != expected_vars:
            raise ValueError(f"Expected {expected_vars} variables [ρ, u, v, w, T, p], got {phi_old_cpd_cell.shape[1]}")
        
        if phi_new_non_cyclic_cell.shape[1] != expected_vars:
            raise ValueError(f"Expected {expected_vars} variables [ρ, u, v, w, T, p], got {phi_new_non_cyclic_cell.shape[1]}")
        
        # Ensure proper naming convention for clarity (rho_uvw_t_p = ρ, u,v,w velocities, T temperature, p pressure)
        rho_uvw_t_p_old_cpd_cell = phi_old_cpd_cell
        rho_uvw_t_p_new_non_cyclic_cell = phi_new_non_cyclic_cell

        # Apply boundary conditions to the new solution vector
        rho_uvw_t_p_new_cpd_cell = self.enforce_boundary_condition(rho_uvw_t_p_new_non_cyclic_cell)

        # Apply temporal discretization scheme at cell centers
        # For compressible flow, we work with conserved variables [ρ, ρu, ρv, ρw, ρT]
        unsteady_cell = self.time_scheme(
            rho_uvw_t_old_cell=rho_uvw_t_p_old_cpd_cell[:, 0:5],  # [ρ, u, v, w, T]
            rho_uvw_t_new_cell=rho_uvw_t_p_new_cpd_cell[:, 0:5]   # [ρ, u, v, w, T]
        )
        
        ''' >>> Gradient Reconstruction >>> '''
        # Construct the solution vector for gradient reconstruction
        # For compressible flow, we need gradients of all variables: [ρ, u, v, w, T, p]
        # We also include previous time step values for temporal schemes
        rho_uvw_t_p_hat_cpd_cell = torch.cat(
            (
                rho_uvw_t_p_new_cpd_cell[:, 0:6],  # Current variables [ρ, u, v, w, T, p]
                rho_uvw_t_p_old_cpd_cell[:, 0:5],  # Previous variables [ρ_old, u_old, v_old, w_old, T_old]
            ),
            dim=-1,
        )
        
        # Perform gradient reconstruction using weighted least squares method
        # This computes gradients at cell centers using neighboring cell values
        try:
            grad_rho_uvw_t_p_hat_cpd_cell = gradient_reconstruction(
                order=self.params.order,
                phi_node=rho_uvw_t_p_hat_cpd_cell,
                edge_index=self.cpd_neighbor_cell_x,
                mesh_pos=self.cpd_centroid,
                precompute_Moments=[self.A_cell_to_cell, self.single_B_cell_to_cell],
            )[:, :, 0:3]  # Extract spatial gradients: [N_cells, N_variables, 3_spatial_dimensions]
        except Exception as e:
            raise RuntimeError(f"Gradient reconstruction failed: {str(e)}")

        # Apply periodic boundary conditions to gradients if present
        if hasattr(self, 'mask_valid_cyclic_face') and self.mask_valid_cyclic_face.any():
            # Copy gradients from master cells to shadow cells for periodic boundaries
            n_vars = grad_rho_uvw_t_p_hat_cpd_cell.shape[1]
            grad_rho_uvw_t_p_hat_cpd_cell[self.patch1_cpd_cells, 0:n_vars] = grad_rho_uvw_t_p_hat_cpd_cell[self.patch2_cells, 0:n_vars]
            grad_rho_uvw_t_p_hat_cpd_cell[self.patch2_cpd_cells, 0:n_vars] = grad_rho_uvw_t_p_hat_cpd_cell[self.patch1_cells, 0:n_vars]
        ''' <<< Gradient Reconstruction <<< '''

        # Apply conserved form of the finite volume method for compressible flow
        if self.params.conserved_form:
            try:
                loss_total, rho_uvw_t_p_new_smoothed_cpd_cell = self.conserved_form(
                    unsteady_cell=unsteady_cell,
                    rho_uvw_t_p_new_cpd_cell=rho_uvw_t_p_new_cpd_cell,
                    rho_uvw_t_p_hat_cpd_cell=rho_uvw_t_p_hat_cpd_cell,
                    grad_rho_uvw_t_p_hat_cpd_cell=grad_rho_uvw_t_p_hat_cpd_cell,
                )
            except Exception as e:
                raise RuntimeError(f"Conserved form evaluation failed: {str(e)}")
        else:
            raise NotImplementedError("Non-conserved form has been removed from this implementation")

        # Ensure output tensors have proper shape and are finite
        if not torch.isfinite(loss_total).all():
            raise ValueError("Loss contains non-finite values (NaN or Inf)")
        
        if not torch.isfinite(rho_uvw_t_p_new_smoothed_cpd_cell).all():
            raise ValueError("Solution contains non-finite values (NaN or Inf)")

        return (
            loss_total,
            rho_uvw_t_p_new_smoothed_cpd_cell,
        )
