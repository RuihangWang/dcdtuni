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
        Initialize the finite volume integrator.

        Args:
            params: Configuration object containing discretization parameters,
                   time integration scheme, and equation form settings.
        """
        super(Intergrator, self).__init__()
        self.params = params
        
        # allow registering geometry after initialization
        if fv_graph is not None:
            self.register_geometrics(fv_graph.graph_node, fv_graph.graph_face, fv_graph.graph_cell, fv_graph.graph_cell_x, fv_graph.graph_Index)

    def enforce_boundary_condition(self, uvwp_new_non_cyclic_cell: torch.Tensor) -> torch.Tensor:
        """
        Apply boundary conditions to the new solution vector.

        Args:
            uvwp_new_non_cyclic_cell: New solution vector at cell centers [N_cpd_cells, 4].
            graph_cell: Graph object containing mesh cell information and boundary conditions.
        Returns:
            uvwp_new_with_dim_cpd_cell: New solution vector with boundary conditions applied [N_cpd_cells, 4].
        """

        if uvwp_new_non_cyclic_cell.shape[0] != self.num_cpd_cells:
            uvwp_new_cpd_cell = torch.zeros((self.num_cpd_cells, uvwp_new_non_cyclic_cell.shape[1]), device=uvwp_new_non_cyclic_cell.device)
            uvwp_new_cpd_cell[self.mask_non_cyclic_cell] = uvwp_new_non_cyclic_cell
        else:
            uvwp_new_cpd_cell = uvwp_new_non_cyclic_cell

        # 最先施加周期性边界条件
        if self.mask_valid_cyclic_face.any():
            uvwp_new_cpd_cell[self.patch1_cpd_cells] = uvwp_new_cpd_cell[self.patch2_cells]
            uvwp_new_cpd_cell[self.patch2_cpd_cells] = uvwp_new_cpd_cell[self.patch1_cells]

        # 然后施加速度的第一类边界条件（入口和壁面）
        uvwp_new_cpd_cell[self.mask_dirichlet_cell, 0:3] = self.dirichlet_y[:, 0:3]
    
        # # 然后施加出口处的压力为0的第一类边界条件
        # outlet_cell_idx, outlet_cpd_cell_idx = self.cpd_neighbor_cell[:,self.mask_outlet_face]
        # uvwp_new_cpd_cell[outlet_cpd_cell_idx, 3:4] = 0.
    
        # # 然后施加出口处速度梯度为0，因此可以直接外推出出口面上的速度
        # uvwp_new_cpd_cell[outlet_cpd_cell_idx,0:3] = uvwp_new_cpd_cell[outlet_cell_idx,0:3]
    
        # # 最后施加入口/壁面处压力梯度等于0边界条件
        # in_wall_cell_idx, in_wall_cpd_cell_idx = self.cpd_neighbor_cell[:,self.mask_in_wall_face]
        # uvwp_new_cpd_cell[in_wall_cpd_cell_idx,3:4] = uvwp_new_cpd_cell[in_wall_cell_idx,3:4]
        
        return uvwp_new_cpd_cell
    
    def time_scheme(self, uvw_old_cell: torch.Tensor, uvw_new_cell: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Apply temporal discretization scheme for time integration.

        Computes the convective velocity field and unsteady term based on the
        selected time integration scheme (explicit, implicit, or IMEX).

        Args:
            uvw_cell_old: Previous time step velocity [N_cells, 3].
            uvw_cell_new: Current time step solution [N_cells, 4].

        Returns:
            Tuple containing:
                - unsteady_cell: Unsteady term contribution [N_interior_cells, 3].
                - uvw_hat_cell: Convective velocity field [N_cells, 3].
        """
        # Compute unsteady term: ∂u/∂t * V * coefficient
        # Note: Apply masks to ensure dimensional consistency
        unsteady_cell = (
            (uvw_new_cell[self.mask_interior_cell, 0:3] / self.dt_cell)
        ) * self.cells_volume * self.unsteady_coeff

        self.source_term = self.source_term + (
            (uvw_old_cell[self.mask_interior_cell, 0:3] / self.dt_cell)
        ) * self.cells_volume * self.unsteady_coeff

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
        uvwp_new_cpd_cell: torch.Tensor,
        uvwp_hat_cpd_cell: Optional[torch.Tensor] = None,
        grad_uvwp_hat_cpd_cell: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Evaluate the conserved form of the governing equations.

        Computes the residuals for the continuity and momentum equations using
        the cell-centered and face-centered values. Applies boundary conditions
        and computes the total right-hand side for the linear system.

        Args:
            unsteady_cell: Unsteady term contribution [N_interior_cells, 3].
            uvwp_hat_cpd_cell: High-order interpolated values at cell centers [N_cpd_cells, 7].
            grad_uvwp_hat_cpd_cell: High-order gradients at cell centers [N_cpd_cells, 7, 3].

        Returns:
            Tuple containing:
                - loss_cont: Continuity equation residual for each graph in batch.
                - loss_momentum: Momentum equation residual for each graph in batch.
                - uvwp_new_cpd_cell: Updated and smoothed solution vector.
        """

        ''' >>> Interpolation >>> '''
        # Interpolate cell values to face centers using central difference + skewness correction
        uvwp_hat_face = self.interpolating_phic_to_faces(
            phi_cpd_cell=uvwp_hat_cpd_cell[:,0:7], # [N_cells, N_channels] where N_cells is number of cells, N_channels is number of variables
            grad_phi_cpd_cell=grad_uvwp_hat_cpd_cell[:,0:7], # [N_cells, N_channels, 3] where 3 represents gradient components in 3D
            scheme="Linear corrected",
        )
        # Interpolate gradients to face centers
        grad_uvwp_hat_face = self.interpolating_gradients_to_faces(
            phi_cpd_cell=uvwp_hat_cpd_cell[:,0:7], # [N_cells, N_channels] where N_cells is number of cells, N_channels is number of variables
            grad_phi_cpd_cell=grad_uvwp_hat_cpd_cell[:,0:7], # [N_cells, N_channels, 3] where 3 represents gradient components in 3D
            presssure_location_dim=6,
        )
        ''' <<< Interpolation <<< '''

        # Compute mass flux for continuity equation
        mass_flux_cells_face = self.continuity_flux(uvw_face=uvwp_hat_face[:,0:3])
        
        if self.params.integrator == "implicit":
            # Compute convective flux using specified scheme
            convection_flux_hat_face = self.convective_flux(
                uvw_face=uvwp_hat_face[:,0:3], # [N_face,3]中心差分的面速度值，用于迎风方向判断
                phi_cpd_cell=uvwp_hat_cpd_cell[:,0:3], # [N_cpd_cell,3]这里是计算对流项，因此把速度设置为phi_cpd_cell
                grad_phi_cpd_cell=grad_uvwp_hat_cpd_cell[:,0:3],
                grad_phi_face=grad_uvwp_hat_face[:,0:3],
                scheme="LimitedLinearV 1" # Linear/Linear corrected/LinearUpwind/SOU/Quick/LimitedLinearV 1
            )
        elif self.params.integrator == "imex":
            # Compute momentum fluxes for momentum equations
            convection_flux_hat_face = 0.5*(self.convective_flux(
                uvw_face=uvwp_hat_face[:,0:3], # [N_face,3]中心差分的面速度值，用于迎风方向判断
                phi_cpd_cell=uvwp_hat_cpd_cell[:,0:3], # [N_cpd_cell,3]这里是计算对流项，因此把速度设置为phi_cpd_cell
                grad_phi_cpd_cell=grad_uvwp_hat_cpd_cell[:,0:3], 
                grad_phi_face=grad_uvwp_hat_face[:,0:3], 
                scheme="LimitedLinearV 1" # Linear/Linear corrected/LinearUpwind/SOU/Quick/LimitedLinearV 1
            ) + self.convective_flux(
                uvw_face=uvwp_hat_face[:,3:6], # [N_face,3]中心差分的面速度值，用于迎风方向判断
                phi_cpd_cell=uvwp_hat_cpd_cell[:,3:6], # [N_cpd_cell,3]这里是计算对流项，因此把速度设置为phi_cpd_cell
                grad_phi_cpd_cell=grad_uvwp_hat_cpd_cell[:,3:6], 
                grad_phi_face=grad_uvwp_hat_face[:,3:6], 
                scheme="LimitedLinearV 1" # Linear/Linear corrected/LinearUpwind/SOU/Quick/LimitedLinearV 1
            ))
        else:
            raise ValueError(f"Unknown integrator type: {self.params.integrator}")

        P_flux_face = self.pressure_flux(p_new_face=uvwp_hat_face[:,6:7])
        P_flux_face = self.apply_pressure_outletBC(P_flux_face, grad_uvwp_hat_face[:,0:3])
        # P_flux_face = 0.
        # P_grad_volume = grad_uvwp_hat_cpd_cell[self.mask_interior_cell,6] * self.theta_PDE[self.batch_cell, 3:4] * self.cells_volume
        P_grad_volume = 0.
        
        # Compute viscous diffusion flux
        vis_flux_face = self.diffusion_flux(grad_uvw_face=grad_uvwp_hat_face[:,0:3])
        
        ''' >>> Total momentum flux assembly >>> '''
        J_flux_cells_face = torch.matmul(
            (convection_flux_hat_face + P_flux_face - vis_flux_face)[self.cells_face],
            self.cells_face_surface_vec.unsqueeze(-1)
        ).squeeze()

        # Assemble total right-hand side by scattering face fluxes to cells
        total_RHS = scatter_add(
            torch.cat((mass_flux_cells_face, J_flux_cells_face), dim=1),
            self.cells_face_ptr,
            dim=0,
            dim_size=self.num_cells,
        )
        ''' <<< Total momentum flux assembly <<< '''
        
        ''' >>> Compute jump in scalar values across cell faces >>> '''
        if self.params.loss_phi_jump == 0.:
            loss_phi_jump = torch.zeros((self.num_graphs,1), device=uvwp_hat_cpd_cell.device)
        else:
            # Compute jump in scalar values across cell faces
            loss_phi_jump = torch.sqrt(
                global_add_pool(
                    self.phi_jump(
                        phi_cpd_cell=torch.cat((uvwp_hat_cpd_cell[:,0:3], uvwp_hat_cpd_cell[:,6:7]),dim=1), 
                        grad_phi_cpd_cell=torch.cat((grad_uvwp_hat_cpd_cell[:,0:3], grad_uvwp_hat_cpd_cell[:,6:7]),dim=1),
                    )**2, 
                    batch=self.batch_cell
                )
            ).sum(dim=1,keepdim=True)
            # loss_phi_jump = torch.zeros((self.num_graphs,1), device=uvwp_hat_cpd_cell.device)
        ''' <<< Compute jump in scalar values across cell faces <<< '''
        
        ''' >>> Compute RMS residual for each graph in batch >>> '''
        # Compute RMS residual for continuity equation for each graph in batch
        loss_cont = torch.sqrt(
            global_add_pool( 
                (total_RHS[:,0:1])**2, batch=self.batch_cell
            )
        ).sum(dim=1,keepdim=True)
        
        # Compute RMS residual for momentum equations for each graph in batch
        loss_momentum = torch.sqrt(
            global_add_pool(
                (
                    unsteady_cell
                    + total_RHS[:,1:4]
                    + P_grad_volume
                    - self.source_term
                )**2, batch=self.batch_cell
            )
        ).sum(dim=1,keepdim=True)
        ''' <<< Compute RMS residual for each graph in batch <<< '''

        self.loss_cont_component = loss_cont
        self.loss_momentum_component = loss_momentum
        self.loss_phi_jum = loss_phi_jump
        
        loss_total = torch.mean(torch.log(
            self.params.loss_cont*loss_cont +\
            self.params.loss_mom*loss_momentum +\
            self.params.loss_phi_jump*self.loss_phi_jum
        ))

        if self.params.cfc_smooth:
            uvwp_new_smoothed_cpd_cell = self.cfc_smooth(
                uvwp_face=torch.cat((uvwp_hat_face[:,0:3], uvwp_hat_face[:,6:7]), dim=1),
            )
        else:
            uvwp_new_smoothed_cpd_cell = uvwp_new_cpd_cell
            
        return loss_total, uvwp_new_smoothed_cpd_cell

    def get_separated_losses(self):
        """
        Returns the individual continuity and momentum loss components
        """
        if hasattr(self, 'loss_cont_component') and hasattr(self, 'loss_momentum_component'):
            return self.loss_cont_component, self.loss_momentum_component, self.loss_phi_jum
        else:
            # Fallback: return None if components not available
            return None, None, None

    def forward(
        self,
        phi_old_cpd_cell: Optional[torch.Tensor] = None,
        phi_new_non_cyclic_cell: Optional[torch.Tensor] = None,
        vel_dim: Tuple[int, int] = (0, 3),
        temp_dim: int = 3,
        press_dim: int = 4,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Forward pass for the finite volume integrator.

        Performs gradient reconstruction, applies boundary conditions, computes temporal
        discretization, and evaluates the conserved form of the governing equations.

        Args:
            uvwp_old_cpd_cell: Previous time step solution vector [N_cpd_cells, 4].
            uvwp_new_non_cyclic_cell: Current time step solution vector [N_cpd_cells, 4].
        Returns:
            Tuple containing:
                - loss_cont: Continuity equation residual for each graph in batch.
                - loss_momentum: Momentum equation residual for each graph in batch.
                - uvwp_cell_new: Updated and smoothed solution vector.
        """

        # Apply boundary conditions to the new solution vector.
        uvwp_new_cpd_cell = self.enforce_boundary_condition(uvwp_new_non_cyclic_cell)

        # Apply temporal discretization scheme at cell centers
        unsteady_cell = self.time_scheme(
            uvw_old_cell=uvwp_old_cpd_cell[:,0:3], uvw_new_cell=uvwp_new_cpd_cell[:,0:3]
        )
        
        ''' >>> Gradient Reconstruction >>> '''
        uvwp_hat_cpd_cell = torch.cat(
            (uvwp_new_cpd_cell[:, 0:3], uvwp_old_cpd_cell[:, 0:3], uvwp_new_cpd_cell[:, 3:4]),
            dim=-1,
        )
        # 内存优化：直接创建所需大小的张量，避免重复分配
        grad_uvwp_hat_cpd_cell = gradient_reconstruction(
            order=self.params.order,
            phi_node=uvwp_hat_cpd_cell,
            edge_index=self.cpd_neighbor_cell_x,
            # mask_valid_node=self.mask_interior_cell,
            mesh_pos=self.cpd_centroid,
            precompute_Moments=[self.A_cell_to_cell, self.single_B_cell_to_cell],
        )[:, :, 0:3]  # Extract gradients only: [N_cells, 7_variables, 3_spatial_dimensions]
        
        # 检查周期边界条件
        if self.mask_valid_cyclic_face.any():
            grad_uvwp_hat_cpd_cell[self.patch1_cpd_cells,0:7] = grad_uvwp_hat_cpd_cell[self.patch2_cells,0:7]
            grad_uvwp_hat_cpd_cell[self.patch2_cpd_cells,0:7] = grad_uvwp_hat_cpd_cell[self.patch1_cells,0:7]
        ''' <<< Gradient Reconstruction <<< '''

        # Apply conserved form of the finite volume method
        if self.params.conserved_form:
            loss_total, uvwp_new_smoothed_cpd_cell = self.conserved_form(
                unsteady_cell=unsteady_cell,
                uvwp_new_cpd_cell=uvwp_new_cpd_cell,
                uvwp_hat_cpd_cell=uvwp_hat_cpd_cell,
                grad_uvwp_hat_cpd_cell=grad_uvwp_hat_cpd_cell,
            )
        else:
            raise NotImplementedError("Non-conserved form has been removed")
        
        return (
            loss_total,
            uvwp_new_smoothed_cpd_cell,
        )
