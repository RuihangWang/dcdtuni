import sys
import os
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

import torch
from FVsolver.FVdiscretization.FVInterpolation import FV_Interpolation
from torch_geometric.nn import global_add_pool

class FV_flux(FV_Interpolation):
    def __init__(self, conserved=True):
        """
        Finite Volume flux calculation class for computing convective, diffusive, and pressure fluxes.
        
        Inherits from Interplot to provide interpolation functionality for flux calculations.
        Used as a base class for flux-related operations in finite volume discretization.
        
        Args:
            conserved (bool): Flag to indicate if conserved form of equations is used.
                            Default is True.
        """
        super(FV_flux, self).__init__()
        
        self.conserved = conserved

    def convective_flux(
        self, 
        uvw_face=None, 
        phi_cpd_cell=None, 
        grad_phi_cpd_cell=None, 
        grad_phi_face=None, 
        scheme="Linear"
    ):
        """ 
        Compute convective flux for momentum equations.
        
        Calculates the convective flux using velocity values at face centers,
        weighted by convection coefficients. The convection scheme can be
        specified to control the interpolation of velocity across faces.
        
        Args:
            uvw_face (torch.Tensor): Velocity values at face centers [N_faces, 3].
                                    Contains u, v, w velocity components.
            phi_cpd_cell (torch.Tensor): Cell-centered values for phi (e.g., velocity) [N_cells, 3].
            grad_phi_cpd_cell (torch.Tensor): Gradient of phi at cell centers [N_cells, 3, 3].
            grad_phi_face (torch.Tensor): Gradient of phi at face centers [N_faces, 3, 3].
            scheme (str): Convection scheme to use for flux calculation.
                        Options: "Linear", "LinearUpwind", "LimitedLinearV", etc.
        Returns:
            torch.Tensor: Convective flux tensor [N_cells_faces, 3, 3].
                         Represents the convective momentum flux through each cell face.    
        """
        
        # Compute the convective flux using the specified scheme
        U_face = self.div_phic_to_faces(
            uvw_face=uvw_face,
            phi_cpd_cell=phi_cpd_cell,
            grad_phi_cpd_cell=grad_phi_cpd_cell,  
            grad_phi_face=grad_phi_face,
            scheme=scheme,
        )
        
        # Compute the convective flux tensor
        UU_face = torch.matmul(
            uvw_face[:,:,None], U_face[:,None,:])
        
        convection_flux_face = UU_face * (self.convection_coeff.unsqueeze(1))

        return convection_flux_face
        
    def diffusion_flux(self,grad_uvw_face):
        """
        Compute viscous (diffusion) flux for momentum equations.
        
        Calculates the diffusive flux using velocity gradients at face centers,
        weighted by diffusion (viscosity) coefficients.
        
        Args:
            grad_uvw_face (torch.Tensor): Velocity gradients at face centers 
                                            [N_faces, 3, 3]. Shape is [face, velocity_component, spatial_derivative].
        
        Returns:
            torch.Tensor: Viscous flux tensor [N_cells_faces, 3, 3].
                         Represents the diffusive momentum flux through each cell face.
        """

        vis_flux_face = (grad_uvw_face+grad_uvw_face.transpose(1,2)) * \
                self.nu_coeff[:, None]
        
        # vis_flux_face = grad_uvw_face * \
        #         self.nu_coeff[:, None]
        
        return vis_flux_face
    
    def pressure_flux(self, p_new_face):
        """
        Compute pressure flux for momentum equations.
        
        Calculates the pressure flux using pressure values at face centers,
        weighted by pressure gradient coefficients. The pressure acts normal
        to each face surface.
        
        Args:
            p_face_new (torch.Tensor): Pressure values at face centers [N_faces, 1].
        
        Returns:
            torch.Tensor: Pressure flux tensor [N_cells_faces, 3, 3].
                         Diagonal tensor with pressure contribution to momentum flux.
        """
        P_flux_face = torch.diag_embed(p_new_face.expand(-1, 3)) * \
            self.grad_p_coeff[:, None]
        
        return P_flux_face
    
    # def apply_pressure_outletBC(self, p_flux_face, grad_uvw_new_face):
    #     """
    #     Apply pressure outlet boundary condition (Legacy - Hard enforcement).

    #     Modifies the pressure flux tensor to enforce the pressure outlet condition.
    #     The viscous force at the outlet should balance the pressure force.

    #     Args:
    #         p_flux_cells_face (torch.Tensor): Pressure flux tensor [N_cells_faces, 3, 3].
    #         grad_uvw_new_cells_face (torch.Tensor): Velocity gradients at face centers [N_faces, 3, 3].

    #     Returns:
    #         torch.Tensor: Modified pressure flux tensor [N_cells_faces, 3, 3].
    #     """

    #     if self.mask_outlet_face.any():
    #         # Compute viscous force at pressure outlet
    #         vis_grad = self.nu_coeff[self.mask_outlet_face,None] * self.grad_p_coeff[self.mask_outlet_face,None] * (
    #             grad_uvw_new_face[self.mask_outlet_face, 0:3] + grad_uvw_new_face[self.mask_outlet_face, 0:3].transpose(1,2)
    #         )

    #         # Compute pressure force at outlet
    #         p_flux_face[self.mask_outlet_face, 0:3] = vis_grad[:,0:3]

    #     return p_flux_face

    def apply_pressure_outletBC(self, P_flux_face, grad_uvw_face, scheme="hard"):
        """
        Compute natural outflow boundary condition loss based on stress balance.
        Natural outflow BC enforces: σ·n = 0 at outlet boundaries
        where σ = -pI + μ(∇u + ∇u^T) is the stress tensor
        
        Args:   
            p_flux_face (torch.Tensor): Pressure flux tensor [N_faces, 3, 3].
            grad_uvw_new_face (torch.Tensor): Velocity gradients at face centers [N_faces, 3, 3].
            scheme (str): Enforcement scheme. Options: 
                "hard": directly modify p_cpd_cell, 
                "soft": compute loss only
        Returns:
            torch.Tensor: L2 norm of outlet boundary condition residual for each graph in batch
        """
        
        if self.mask_outlet_face.any():
            
            # Compute viscous force at pressure outlet face(in cpd_cell array)
            vis_grad = self.nu_coeff[self.mask_outlet_face,None] * self.grad_p_coeff[self.mask_outlet_face,None] * (
                grad_uvw_face[self.mask_outlet_face]+grad_uvw_face[self.mask_outlet_face].transpose(1,2)
            )

            if scheme == "hard":
                P_flux_face[self.mask_outlet_face] = vis_grad
                
                loss_outlet_bc = torch.zeros((self.num_graphs,1), device=P_flux_face.device)

            elif scheme == "soft":
                loss_outlet_bc = ((P_flux_face[self.mask_outlet_face] - vis_grad) @ \
                    (self.face_unv[self.mask_outlet_face, :, None])).squeeze(2)

                loss_outlet_bc = torch.sqrt(
                    global_add_pool(
                        loss_outlet_bc**2, batch=self.batch_face[self.mask_outlet_face]
                    )
                ).sum(dim=1,keepdim=True)
            else:
                raise ValueError(f"Unknown enforcement scheme: {scheme}")
        else:
            loss_outlet_bc = torch.zeros((self.num_graphs,1), device=P_flux_face.device)
            
        return P_flux_face, loss_outlet_bc

    # def apply_zeroGrad_BC(self, grad_uvwp_cpd_cell):
    #     """
    #     Apply zero-gradient boundary condition to velocity and pressure gradients.
    #     Ensures that velocity gradients are zero normal to boundary faces and
    #     pressure gradients are tangential to boundary faces.
    #     Args:

    #         grad_uvwp_new_hat_face (torch.Tensor): Velocity gradients at face centers [N_faces, 7, 3].
    #         grad_uvwp_new_hat_cpd_cell (torch.Tensor): Velocity gradients at cell centers [N_cells, 7, 3].
    #         p_face (torch.Tensor): Pressure values at face centers [N_faces, 1].
    #         p_cpd_cell (torch.Tensor): Pressure values at cell centers [N_cells, 1].
        
    #     Returns:
    #         Tuple containing:
    #             - grad_uvwp_new_hat_face: Velocity gradients with BC applied [N_faces, 7, 3].
    #             - p_face: Pressure values with BC applied [N_faces, 1].
    #     """

    #     ''' vel grad '''
    #     # 首先处理出口面元处的速度梯度张量，保证其为0法相梯度
    #     _, outlet_cpd_cell_idx = self.cpd_neighbor_cell[:,self.mask_outlet_face]
    #     Loss_uvw_zeroGrad = (grad_uvwp_cpd_cell[outlet_cpd_cell_idx, 0:3]@\
    #         self.surface_vec_face[self.mask_outlet_face,:,None]
    #     ).squeeze(2)
    #     Loss_uvw_zeroGrad = torch.sqrt(
    #         global_add_pool(
    #             Loss_uvw_zeroGrad**2, batch=self.batch_cpd_cell[outlet_cpd_cell_idx]
    #         )
    #     )
    #     ''' vel grad '''
        
    #     ''' p grad '''
    #     # 然后处理入口，壁面处面元的压力pf，以保证0法相梯度。 注意：在enforce_boundary_condition🀄️出口处压力直接设为0了
    #     mask_in_wall_face = (self.face_type == FaceType.INFLOW) | (self.face_type == FaceType.WALL_BOUNDARY)  # INFLOW | WALL_BOUNDARY
    #     _, in_wall_cpd_cell_idx = self.cpd_neighbor_cell[:,mask_in_wall_face]
    #     Loss_P_zeroGrad = (grad_uvwp_cpd_cell[in_wall_cpd_cell_idx, 3:4]@\
    #         self.surface_vec_face[mask_in_wall_face,:,None]
    #     ).squeeze(2)
    #     Loss_P_zeroGrad = torch.sqrt(
    #         global_add_pool(
    #             Loss_P_zeroGrad**2, batch=self.batch_cpd_cell[in_wall_cpd_cell_idx]
    #         )
    #     )
    #     ''' p grad '''
        
    #     return Loss_uvw_zeroGrad, Loss_P_zeroGrad
                
    def continuity_flux(self, uvw_face):
        """
        Compute continuity equation residual using conserved form.
        
        Calculates the mass flux through cell faces to evaluate the continuity equation.
        Uses the divergence theorem to compute the net mass flux for each cell.
        
        Args:
            uvw_face_new (torch.Tensor): Velocity values at face centers [N_faces, 3].
                                       Contains u, v, w velocity components.
        
        Returns:
            torch.Tensor: Continuity equation residual [N_graphs, 1].
                         RMS of mass conservation violations for each graph in the batch.
        """
        # Compute mass flux through each face: velocity · surface_vector
        mass_flux = torch.matmul(
            uvw_face[self.cells_face, None, 0:3], 
            self.cells_face_surface_vec[:, :, None]
        ).squeeze().view(-1, 1)
        
        return mass_flux

            