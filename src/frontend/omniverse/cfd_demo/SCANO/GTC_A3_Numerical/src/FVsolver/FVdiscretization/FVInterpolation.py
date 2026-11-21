import sys
import os
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

import torch
from Utils.utilities import FaceType
from torch_scatter import scatter_mean, scatter_add
from FVsolver.FVdiscretization.FVbase import FV_base

class FV_Interpolation(FV_base):
    """
    Comprehensive interpolation module for finite volume methods.
    
    This class provides various interpolation schemes for transferring values between
    different locations in a computational mesh (nodes, faces, and cell centers).
    It supports high-order interpolation schemes and RBF-based interpolation.
    
    The interpolation methods are based on finite volume discretization techniques
    described in Moukalled et al., "The Finite Volume Method in Computational Fluid 
    Dynamics" (2016).
    
    Implements caching mechanisms to avoid repeated computation of geometric coefficients
    such as interpolation weights (gC, gF) and geometric vectors (CF, eCF).
    """
    
    def __init__(self):
        """
        Initialize the interpolation module.
        
        Sets up caching structures for geometric coefficients to optimize
        repeated interpolation operations.
        """
        super().__init__()
        
        # Cached interpolation coefficients (computed once in calc_geometry_coeff)
        self.gC = None              # Interpolation weight for C (sender) cells
        self.gF = None              # Interpolation weight for F (receiver) cells  
        self.CF = None              # Vector from C to F cell centers
        self.dCF = None             # Distance between C and F cell centers
        self.eCF = None             # Unit vector from C to F
        self.eCf = None             # Vector from C center to face center
        self.eFf = None             # Vector from F center to face center
        
        # Cached cell indices (computed once to avoid repeated index operations)
        self.C_senders = None       # Sender cell indices for interior faces
        self.F_receivers = None     # Receiver cell indices for interior faces
        self.registered = False
        
    def register_geometrics(self, graph_node, graph_face, graph_cell, graph_cell_x, graph_Index, force_register=False):
        """
        Register and cache geometric quantities and PDE coefficients.
        
        Extracts geometry information from graph structures and caches frequently
        used quantities to avoid repeated computation during interpolation.
        
        Args:
            graph_cell: Cell-based graph containing cell geometry and properties
            graph_face: Face-based graph containing face geometry and connectivity  
            graph_Index: Index graph containing PDE coefficients and batch information
            graph_cell_x: Extended cell graph containing WLSQ connectivity and matrices
            force_register: Whether to force register even if already registered
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        
        if self.registered and not force_register:
            return True
        
        self.num_nodes = graph_node._num_nodes
        self.num_faces = graph_face._num_faces
        self.num_cells = graph_cell._num_cells
        self.num_cpd_cells = graph_cell._num_cpd_cells
        self.num_graphs = graph_cell.num_graphs
        
        # Geometry quantities - extract and reshape for efficient computation
        self.mesh_pos = graph_node.pos
        self.cells_node = graph_node.cells_node.view(-1)
        self.cells_node_ptr = graph_cell.cells_node_ptr.view(-1)
        self.cells_face = graph_face.cells_face.view(-1)
        self.cells_face_ptr = graph_cell.cells_face_ptr.view(-1)
        self.cells_volume = graph_cell.cells_volume.view(-1, 1)
        self.cells_face_unv = graph_cell.cells_face_unv.view(-1, 3)
        self.face_type = graph_face.face_type.view(-1)
        self.face_pos = graph_face.pos.view(-1, 3)
        self.face_area = graph_face.face_area.view(-1, 1)
        self.face_node = graph_node.face_node.view(-1)
        self.face_node_ptr = graph_face.face_node_ptr.view(-1)
        self.cell_type = graph_cell.cell_type.view(-1)
        self.cpd_centroid = graph_cell.cpd_centroid.view(-1, 3)
        self.dirichlet_y = graph_cell.y
        
        # WLSQ precomputed matrices
        self.cpd_neighbor_cell_x = graph_cell_x.cpd_neighbor_cell_x
        self.A_cell_to_cell = graph_cell_x.A_cell_to_cell
        self.single_B_cell_to_cell = graph_cell_x.single_B_cell_to_cell
        
        # Compute logical masks for different cell/face types
        self.mask_interior_face = (self.face_type == FaceType.NORMAL)  # NORMAL
        self.mask_boundary_face = ~self.mask_interior_face
        self.mask_boundary_non_cyclic_face = self.mask_boundary_face & (self.face_type != FaceType.CYCLIC)  # CYCLIC
        self.mask_dirichlet_face = (
            (self.face_type == FaceType.WALL) |  # WALL_BOUNDARY
            (self.face_type == FaceType.INFLOW)    # INFLOW
        ).squeeze()

        self.mask_interior_cell = (self.cell_type == FaceType.NORMAL)  # NORMAL
        self.mask_boundary_cell = ~self.mask_interior_cell
        self.mask_non_cyclic_cell = ~(self.cell_type == FaceType.CYCLIC)  # CYCLIC
        self.mask_dirichlet_cell = (
            (graph_cell.cell_type == FaceType.WALL) |  # WALL_BOUNDARY
            (graph_cell.cell_type == FaceType.INFLOW)    # INFLOW
        ).squeeze()
        self.mask_outlet_face = self.face_type == FaceType.OUTFLOW  # OUTFLOW
        # self.mask_inlet_face = self.face_type == 1  # INFLOW
        # self.mask_wall_face = self.face_type == 3   # WALL_BOUNDARY
        # self.mask_in_wall_face = (self.mask_inlet_face) | (self.mask_wall_face)
        
        # only consider interior face for interpolation
        self.cpd_neighbor_cell = graph_cell.cpd_neighbor_cell
        self.cpd_owner = self.C_senders = self.cpd_neighbor_cell[0]
        self.cpd_neighbor = self.F_receivers = self.cpd_neighbor_cell[1]
        
        # cyclic face info
        self.mask_valid_cyclic_face = graph_face.cyclic_face[0] >= 0
        self.patch1_faces, self.patch2_faces = graph_face.cyclic_face[:, self.mask_valid_cyclic_face]
        self.patch1_cells, self.patch1_cpd_cells = self.cpd_neighbor_cell[:,self.patch1_faces]
        self.patch2_cells, self.patch2_cpd_cells = self.cpd_neighbor_cell[:,self.patch2_faces]
        
        # Compute surface vectors for flux calculations
        self.cells_face_surface_vec = self.cells_face_unv * (self.face_area[self.cells_face])
        self.face_unv = torch.zeros((self.num_faces,3), device = self.cells_face_surface_vec.device)
        self.face_unv[self.cells_face] = self.cells_face_unv
        
        # Batch information for scatter operations
        self.batch_face = graph_face.batch
        self.batch_cpd_cell = graph_cell.batch
        self.batch_cell = graph_cell.batch[self.mask_interior_cell]
        
        # Extract PDE coefficients for interior cells only
        self.theta_PDE = graph_Index.theta_PDE
        self.unsteady_coeff = graph_Index.theta_PDE[self.batch_cell, 0:1]
        self.continuity_eq_coeff = graph_Index.theta_PDE[:, 1:2] # [num_graphs, 1]
        self.convection_coeff = graph_Index.theta_PDE[self.batch_face, 2:3]
        self.grad_p_coeff = graph_Index.theta_PDE[self.batch_face, 3:4]
        self.nu_coeff = graph_Index.theta_PDE[self.batch_face, 4:5]
        self.source_term = graph_Index.theta_PDE[self.batch_cell, 5:6] * self.cells_volume
        self.dt_cell = graph_Index.theta_PDE[self.batch_cell, 6:7]
        
        # reference pressure cell and value
        self.pRefCell = graph_Index.pRefCell
        self.pRefValue = graph_Index.pRefValue
        
        # Compute and cache interpolation geometric coefficients
        self.pressure_correction=None
        self.calc_geometry_coeff()
        self.registered = True

    def calc_geometry_coeff(self):
        """
        Compute and cache geometric coefficients for face interpolation.
        
        Calculates interpolation weights (gC, gF) and geometric vectors (CF, eCF)
        based on the method described in Moukalled et al. (2016), Section 9.2.
        These coefficients are cached to avoid repeated computation during 
        multiple interpolation operations.
        
        The coefficients are computed only for interior faces to avoid 
        unnecessary calculations for boundary faces.
        """

        # Compute geometric vectors and distances
        # CF: vector from cell C center to cell F center
        self.CF = (self.cpd_centroid[self.F_receivers] - 
                    self.cpd_centroid[self.C_senders])[:, :, None]
        self.dCF = self.CF.norm(dim=1, keepdim=True)
        self.eCF = self.CF / self.dCF
        
        # Compute interpolation weights using distance-based weighting
        # gC: weight for upstream cell C, gF: weight for downstream cell F
        dfF = torch.norm(
            self.cpd_centroid[self.F_receivers] - self.face_pos,
            dim=1, keepdim=True
        )[:, :, None]
        self.gC = (dfF / self.dCF)
        self.gF = 1.0 - self.gC
        
        # Vectors from cell centers to face center (for gradient correction)
        self.Cf = (self.face_pos - self.cpd_centroid[self.C_senders])[:, :, None]
        self.Ff = (self.face_pos - self.cpd_centroid[self.F_receivers])[:, :, None]

        # # over-relaxation factor for diffusion term
        # self.Ef = (((self.surface_vec_face[:,None,:]@self.surface_vec_face[:,:,None]) / \
        #     (self.surface_vec_face[:,None,:]@self.eCF))*self.eCF).squeeze(2)
        # self.Tf = self.surface_vec_face - self.Ef
   
    def interpolating_with_grad(
        self,
        src:torch.Tensor=None,
        dst_index:torch.Tensor=None,
        dst_size:int=None,
        src_grad:torch.Tensor=None,
        src_pos:torch.Tensor=None,
        dst_pos:torch.Tensor=None,
    ):
        """
        Interpolate values from source points to destination points with optional gradient correction.

        This function performs interpolation using scatter_mean operation. When gradients are provided,
        it applies first-order correction to improve interpolation accuracy by considering the spatial
        displacement between source and destination positions.

        Args:
            src (torch.Tensor): Source values to be interpolated. Shape: (N, ...) where N is the number of source points.
            dst_index (torch.Tensor): Indices mapping source points to destination points. Shape: (N,).
            dst_size (int): Number of destination points.
            src_grad (torch.Tensor, optional): Gradients at source points for first-order correction.
                                             Shape should be compatible with src for gradient computation.
            src_pos (torch.Tensor, optional): Positions of source points. Required when src_grad is provided.
                                            Shape: (N, spatial_dim).
            dst_pos (torch.Tensor, optional): Positions of destination points. Required when src_grad is provided.
                                            Shape: (dst_size, spatial_dim).

        Returns:
            torch.Tensor: Interpolated values at destination points. Shape: (dst_size, ...).

        Raises:
            ValueError: If dimension 0 of input tensors are inconsistent.
            ValueError: If src_grad is provided but src_pos or dst_pos is None.
        """
        # Check dimension consistency
        if src.dim() <2:
            raise ValueError("src and dst_index must have at least 2 dimension: [N_elem, Channel]")

        if src.size(0) != dst_index.size(0):
            raise ValueError(f"src and dst_index must have the same size in dimension 0. "
                           f"Got src.size(0)={src.size(0)}, dst_index.size(0)={dst_index.size(0)}")

        if src_grad is None:
            return scatter_mean(src, dst_index, dim=0, dim_size=dst_size)
        else:
            r_n_2_c = (dst_pos - src_pos).unsqueeze(1)

            if len(src_grad.size()) > 3: # scr_grad: [N_element, Channel, spatial_dim]
                first_order_correction = (
                    torch.matmul(
                        r_n_2_c.unsqueeze(1).unsqueeze(1),
                        src_grad.unsqueeze(-1),
                    )
                    .squeeze()
                )
            else: # scr_grad: [N_element, spatial_dim]
                first_order_correction = (
                    torch.matmul(r_n_2_c.unsqueeze(1),src_grad.unsqueeze(-1))
                    .squeeze()
                )
                
            if len(first_order_correction.shape)<2:
                first_order_correction=first_order_correction[:,None]

            dst_value = scatter_mean(
                src=(src + first_order_correction), 
                index=dst_index, 
                dim=0, 
                dim_size=dst_size
            )

            return dst_value
        
    def interpolating_gradients_to_faces(
        self,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor,
        presssure_location_dim=None,
    ) -> torch.Tensor:
        """
        Interpolate gradients from cell centers to face centers using high-order interpolation.
        
        Implements the gradient interpolation scheme from Moukalled et al.,
        "The Finite Volume Method in Computational Fluid Dynamics", Section 9.4, page 289.
        
        Uses pre-computed geometric coefficients to avoid repeated calculations.
        Applies gradient correction to maintain high-order accuracy.
        
        Args:
            phi_cpd_cell (torch.Tensor): Cell-centered and boundary face scalar values [N_cells, N_variables]
            grad_phi_cpd_cell (torch.Tensor): Cell-centered and boundary face gradients [N_cells, N_variables, 3]
                                        3 spatial dimensions for gradient components
            
        Returns:
            torch.Tensor: Face-centered gradients [N_interior_faces, N_variables, 3]
                         High-order accurate gradients at interior face centers
            
        Note:
            Requires register_geometrics() to be called first to compute geometric coefficients.
            
        References:
            Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method 
            in computational fluid dynamics. Springer, Section 9.4.
        """
        
        # Expand phi_cell for broadcasting with geometric coefficients
        phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]

        # Compute interpolated gradient using distance-weighted averaging
        grad_f_hat = (grad_phi_cpd_cell[self.C_senders] * self.gC + 
                     grad_phi_cpd_cell[self.F_receivers] * self.gF) # dim=[N_interior_face, N_variables, 3]

        # Apply correction term to maintain high-order accuracy
        # Correction ensures consistency between interpolated gradient and cell values
        gradient_dot_eCF = grad_f_hat @ self.eCF
        phi_difference_normalized = ((phi_cpd_cell_expanded[self.F_receivers] - 
                                    phi_cpd_cell_expanded[self.C_senders]) / self.dCF)
        
        correction = ((phi_difference_normalized - gradient_dot_eCF) * 
                     (self.eCF.transpose(1, 2)))
        grad_f_hat[self.mask_interior_face] += correction[self.mask_interior_face]
        
        if presssure_location_dim is not None:
            self.pressure_correction = correction[:,presssure_location_dim] # for Rhie-Chow interpolation
        
        return grad_f_hat # [interior_cells_face, N_variables, 3]

    def interpolating_phic_to_faces(
        self,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor = None,
        scheme: str = "Linear",
    ) -> torch.Tensor:
        """
        Interpolate scalar values from cell centers to face centers using high-order interpolation.
        
        Implements the scalar interpolation scheme from Moukalled et al.,
        "The Finite Volume Method in Computational Fluid Dynamics", Section 9.2, page 276.
        
        Uses distance-weighted linear interpolation with gradient-based correction
        to achieve high-order accuracy. Pre-computed geometric coefficients are used
        to optimize performance.
        
        Args:   
            phi_cpd_cell (torch.Tensor): Cell-centered and boundary face scalar values [N_cells, N_variables]
            grad_phi_cpd_cell (torch.Tensor): Cell-centered gradients [N_cells, N_variables, 3]
            scheme (str): Interpolation scheme to use. Options: "Linear", "Linear corrected", "Upwind", "LinearUpwind", "limitedLinearV"
            
        Returns:    
            torch.Tensor: Face-centered scalar values [N_faces, N_variables]
                         High-order accurate values at all face centers
            
        Note:
            Requires register_geometrics() to be called first to compute geometric coefficients.
            
        References:
            Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method 
            in computational fluid dynamics. Springer, Section 9.2.
        """
        # Expand phi_cell for broadcasting with geometric coefficients  
        phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]
        
        if scheme == "Linear":
            
            # Compute interpolated value using distance-weighted averaging at interior face
            phi_face = (phi_cpd_cell_expanded[self.C_senders] * self.gC + 
                        phi_cpd_cell_expanded[self.F_receivers] * self.gF).squeeze(2)

            return phi_face
        
        elif scheme == "Linear corrected":
            
            phi_face = (phi_cpd_cell_expanded[self.C_senders] * self.gC + 
                        phi_cpd_cell_expanded[self.F_receivers] * self.gF).squeeze(2)
            
            grad_linear_face = (self.gC*grad_phi_cpd_cell[self.C_senders] + 
                                self.gF*grad_phi_cpd_cell[self.F_receivers])
            
            r_hat_face = (self.cpd_centroid[self.C_senders,:,None]*self.gC + \
                self.cpd_centroid[self.F_receivers,:,None]*self.gF).squeeze(2)
                
            correction = (grad_linear_face @ ((self.face_pos-r_hat_face)[:,:,None])).squeeze(2)

            return phi_face + correction
        
        elif scheme == "Linear grad":
            
            phi_face = (phi_cpd_cell_expanded[self.C_senders] * self.gC + 
                        phi_cpd_cell_expanded[self.F_receivers] * self.gF).squeeze(2)
            
            grad_linear_face = (self.gC*(grad_phi_cpd_cell[self.C_senders]@self.Cf) + 
                                self.gF*(grad_phi_cpd_cell[self.F_receivers]@self.Ff)).squeeze(2)
            
            phi_face[self.mask_interior_face, :] += grad_linear_face[self.mask_interior_face, :]
            return phi_face
        
        else:
            raise ValueError("No support for this scheme at interpolating_phic_to_faces")
    
    def rhie_chow_interpolation(
        self,
        uvw_face: torch.Tensor,
    ) -> torch.Tensor:
        """
        Rhie-Chow interpolation for pressure-velocity coupling.
        
        Implements the Rhie-Chow interpolation scheme to ensure continuity of
        the pressure gradient across cell faces. This is crucial for maintaining
        the accuracy of the pressure-velocity coupling in incompressible flow
        simulations.
        
        Args:
            phi_cell (torch.Tensor): Cell-centered scalar values [N_cells, 1]
            grad_p_cpd_cell (torch.Tensor): Cell-centered pressure gradients [N_cells, 3]
            grad_p_face (torch.Tensor): Face-centered pressure gradients [N_faces, 3]
            u_face_hat (torch.Tensor): Face-centered velocity values [N_faces, 3]
            rho (float): Fluid density (default: 1.0 for incompressible flow)
            
        Returns:
            torch.Tensor: Interpolated velocity values at face centers [N_interior_faces, 3]
            
        References:
            Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method 
            in computational fluid dynamics. Springer, Section 9.5.
        """
        #TODO: rho
        # diagonal coefficient： Unsteady term, convection term, diffusion term and source term contribution
        # first is unsteady term contribution
        a_P = (self.cells_volume/self.dt_cell)*self.unsteady_coeff
        
        # convection term contribution
        mass_flux_hat = torch.matmul(
            uvw_face[self.cells_face, None, 0:3], 
            self.cells_face_surface_vec[:, :, None]
        ).squeeze(1)
        a_P += scatter_add(src=mass_flux_hat, index=self.cells_face_ptr, dim=0, dim_size=self.num_cells)
        
        # diffusion term contribution
        Ef = (
            (self.cells_face_surface_vec[:,None,:]@(self.cells_face_surface_vec[:,:,None])) / \
            (self.cells_face_surface_vec[:,None,:]@self.eCF[self.cells_face])
        ).squeeze(2)
        Flux_diff = (self.nu_coeff[self.cells_face_ptr]*Ef)/(self.dCF[self.cells_face].squeeze(1))
        a_P += scatter_add(src=Flux_diff, index=self.cells_face_ptr, dim=0, dim_size=self.num_cells)
        
        # average to face
        D_f = scatter_mean(src=(self.cells_volume/a_P)[self.cells_face_ptr], index=self.cells_face, dim=0, dim_size=self.num_faces)
        
        if self.pressure_correction is not None:
            uvw_face_star = uvw_face - D_f*self.pressure_correction
            uvw_face_star[self.mask_boundary_face] = uvw_face[self.mask_boundary_face]
        else:
            raise ValueError("No pressure correction is calculated")
        
        return uvw_face_star
        
    def rbf_interpolate(
        self,
        phi_values: torch.Tensor,
        source_pos: torch.Tensor,
        target_pos: torch.Tensor,
        source_indices: torch.Tensor,
        target_indices: torch.Tensor,
        k: int = 4,
        shape_param: float = 0.23
    ) -> torch.Tensor:
        """
        High-performance Radial Basis Function (RBF) interpolation for arbitrary source-to-target mapping.
        
        Implements an optimized RBF interpolation scheme using vectorized operations
        for maximum computational efficiency. Suitable for complex interpolation
        scenarios not covered by standard finite volume interpolation methods.
        
        Args:
            phi_values (torch.Tensor): Source point values [N_source, N_variables]
            source_pos (torch.Tensor): Source point positions [N_source, N_spatial_dims]
            target_pos (torch.Tensor): Target point positions [N_target, N_spatial_dims]
            source_indices (torch.Tensor): Source point indices [N_connections]
            target_indices (torch.Tensor): Target point indices [N_connections]
            k (int, optional): Number of neighbors for each target point. Default: 4
            shape_param (float, optional): RBF shape parameter. Default: 0.23
            
        Returns:
            torch.Tensor: Interpolated values at target points [N_target, N_variables]
            
        Examples:
            Node-to-cell interpolation:
            >>> cell_values = self.rbf_interpolate(
            ...     phi_values=node_phi, source_pos=mesh_pos, target_pos=centroid,
            ...     source_indices=cells_node, target_indices=cells_index
            ... )
            
            Cell-to-node interpolation:
            >>> node_values = self.rbf_interpolate(
            ...     phi_values=cell_phi, source_pos=centroid, target_pos=mesh_pos,
            ...     source_indices=cells_index, target_indices=cells_node
            ... )
            
        Note:
            Uses multiquadric RBF kernel: φ(r) = √(r² + c²) where c is shape_param.
            Optimized for batch processing with vectorized distance calculations.
        """
        n_target = target_pos.size(0)
        n_features = phi_values.size(1)
        
        # Reorganize data for efficient batch processing
        source_pos_neighbors = source_pos[source_indices].view(n_target, k, -1)
        source_phi_neighbors = phi_values[source_indices].view(n_target, k, n_features)
        
        # Compute inter-source distance matrix using optimized vectorization
        neighbors_diff = (source_pos_neighbors.unsqueeze(2) - 
                         source_pos_neighbors.unsqueeze(1))
        distances_squared = torch.sum(neighbors_diff * neighbors_diff, dim=-1)
        
        # Compute RBF kernel matrix using multiquadric basis function
        shape_param_sq = shape_param * shape_param
        kernel = torch.sqrt(distances_squared + shape_param_sq)
        
        # Solve RBF system using batch linear solver
        coeffs = torch.linalg.solve(kernel, source_phi_neighbors)
        
        # Compute target-to-source distances in single operation
        target_pos_expanded = target_pos[target_indices].view(n_target, k, -1)
        target_diff = target_pos_expanded - source_pos_neighbors
        target_distances_squared = torch.sum(target_diff * target_diff, dim=-1)
        
        # Evaluate RBF at target points
        kernel_target = torch.sqrt(target_distances_squared + shape_param_sq).unsqueeze(-1)
        
        # Compute final interpolated values using optimized matrix operations
        result = torch.sum(kernel_target * coeffs, dim=1)
        
        return result

    def phi_jump(
        self,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor,
    ):
        """
        Compute the jump in scalar values across cell faces.
                
        Args:
            phi_cpd_cell (torch.Tensor): Cell-centered scalar values [N_cells, N_variables]
            grad_phi_cpd_cell (torch.Tensor): Cell-centered gradients [N_cells, N_variables, 3]
            
        Returns:
            torch.Tensor: Jump in scalar values across cell faces [N_interior_faces, N_variables]
        """

        # Expand phi_cell for broadcasting with geometric coefficients
        phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]
        
        phi_jump_loss_face = (
            (phi_cpd_cell_expanded[self.C_senders]).squeeze(2) + \
            (grad_phi_cpd_cell[self.C_senders] @ self.Cf).squeeze(2)
            -
            (phi_cpd_cell_expanded[self.F_receivers]).squeeze(2) - \
            (grad_phi_cpd_cell[self.F_receivers] @ self.Ff).squeeze(2)
        ) # [N_faces, N_variables]
        phi_jump_loss_face[self.mask_boundary_non_cyclic_face] = 0.
        
        phi_jump_loss_cell = scatter_mean(
            phi_jump_loss_face[self.cells_face],
            self.cells_face_ptr,
            dim=0,
            dim_size=self.num_cells,
        )
        
        return phi_jump_loss_cell
    
    def div_phic_to_faces(
        self,
        uvw_face: torch.Tensor,
        phi_cpd_cell: torch.Tensor,
        grad_phi_cpd_cell: torch.Tensor,
        grad_phi_face: torch.Tensor,
        scheme: str = "Linear",
    ) -> torch.Tensor:
        """
        Interpolate scalar values from cell centers to face centers using high-order interpolation.
        
        Implements the scalar interpolation scheme from Moukalled et al.,
        "The Finite Volume Method in Computational Fluid Dynamics", Section 9.2, page 276.
        
        Uses distance-weighted linear interpolation with gradient-based correction
        to achieve high-order accuracy. Pre-computed geometric coefficients are used
        to optimize performance.
        
        Args:
            phi_cpd_cell (torch.Tensor): Cell-centered and boundary face scalar values [N_cells, N_variables]
            uvw_face (torch.Tensor): Face-centered velocity values [N_faces, 3]
            grad_phi_cpd_cell (torch.Tensor): Cell-centered gradients [N_cells, N_variables, 3]
            grad_phi_face (torch.Tensor): Face-centered gradients [N_faces, N_variables, 3]
            scheme (str): Interpolation scheme to use. Options: "Linear", "Linear corrected", "Upwind", "LinearUpwind", "limitedLinearV"
            
        Returns:
            torch.Tensor: Face-centered scalar values [N_interior_faces, N_variables]
                         High-order accurate values at interior face centers
            
        Note:
            Requires register_geometrics() to be called first to compute geometric coefficients.
            
        References:
            Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method 
            in computational fluid dynamics. Springer, Section 9.2. 
        """
        
        if (scheme == "Linear") or (scheme == "Linear corrected") or (scheme == "Linear grad"):
            
            return self.interpolating_phic_to_faces(
                phi_cpd_cell=phi_cpd_cell,
                grad_phi_cpd_cell=grad_phi_cpd_cell,
                scheme=scheme,
            )

        elif scheme == "Upwind":

            C_phi = phi_cpd_cell[self.C_senders]
            F_phi = phi_cpd_cell[self.F_receivers]

            # detect upwind and downwind cell
            upwind_mask = ((uvw_face[:, None, 0:3] @ self.CF) > 0).squeeze()

            # apply upwind scheme
            upwind_phi_f = F_phi
            upwind_phi_f[upwind_mask] = C_phi[upwind_mask]
            upwind_phi_f[~upwind_mask] = F_phi[~upwind_mask]
            
            # keep dirichlet boundary value unchange
            upwind_phi_f[self.mask_dirichlet_face] = phi_cpd_cell[self.mask_dirichlet_cell]
            phi_f_hat = upwind_phi_f
            
        elif scheme == "LinearUpwind": # Openfoam LinearUpwind

            C_phi = phi_cpd_cell[self.C_senders]
            F_phi = phi_cpd_cell[self.F_receivers]
            
            C_grad_phi = grad_phi_cpd_cell[self.C_senders]
            F_grad_phi = grad_phi_cpd_cell[self.F_receivers]

            # detect upwind and downwind cell
            upwind_mask = ((uvw_face[:, None, 0:3] @ self.CF) > 0).squeeze()
            
            # apply linear upwind scheme
            upwind_phi_f = F_phi
            upwind_phi_f[upwind_mask] = C_phi[upwind_mask] + \
                ((C_grad_phi[upwind_mask]) @ (self.Cf[upwind_mask])).squeeze()
            upwind_phi_f[~upwind_mask] = F_phi[~upwind_mask] + \
                ((F_grad_phi[~upwind_mask]) @ (self.Ff[~upwind_mask])).squeeze()
                
            # keep dirichlet boundary value unchange
            upwind_phi_f[self.mask_dirichlet_face] = phi_cpd_cell[self.mask_dirichlet_cell]
            phi_f_hat = upwind_phi_f

        elif scheme == "SOU": # SOU scheme

            C_phi = phi_cpd_cell[self.C_senders]
            F_phi = phi_cpd_cell[self.F_receivers]
            
            C_grad_phi = grad_phi_cpd_cell[self.C_senders]
            F_grad_phi = grad_phi_cpd_cell[self.F_receivers]

            # detect upwind and downwind cell
            upwind_mask = ((uvw_face[:, None, 0:3] @ self.CF) > 0).squeeze()
            
            # apply linear upwind scheme
            upwind_phi_f = F_phi
            upwind_phi_f[upwind_mask] = C_phi[upwind_mask] + \
                ((2*C_grad_phi[upwind_mask] - grad_phi_face[upwind_mask]) @ (self.Cf[upwind_mask])).squeeze()
            upwind_phi_f[~upwind_mask] = F_phi[~upwind_mask] + \
                ((2*F_grad_phi[~upwind_mask] - grad_phi_face[~upwind_mask]) @ (self.Ff[~upwind_mask])).squeeze()
                
            # keep dirichlet boundary value unchange
            upwind_phi_f[self.mask_dirichlet_face] = phi_cpd_cell[self.mask_dirichlet_cell]
            phi_f_hat = upwind_phi_f

        elif scheme == "Quick": # Quick scheme

            C_phi = phi_cpd_cell[self.C_senders]
            F_phi = phi_cpd_cell[self.F_receivers]
            
            C_grad_phi = grad_phi_cpd_cell[self.C_senders]
            F_grad_phi = grad_phi_cpd_cell[self.F_receivers]

            # detect upwind and downwind cell
            upwind_mask = ((uvw_face[:, None, 0:3] @ self.CF) > 0).squeeze()
            
            # apply Quick scheme
            upwind_phi_f = F_phi
            upwind_phi_f[upwind_mask] = C_phi[upwind_mask] + \
                (0.5*(C_grad_phi[upwind_mask] + grad_phi_face[upwind_mask]) @ (self.Cf[upwind_mask])).squeeze()
            upwind_phi_f[~upwind_mask] = F_phi[~upwind_mask] + \
                (0.5*(F_grad_phi[~upwind_mask] + grad_phi_face[~upwind_mask]) @ (self.Ff[~upwind_mask])).squeeze()
                
            # keep dirichlet boundary value unchange
            upwind_phi_f[self.mask_dirichlet_face] = phi_cpd_cell[self.mask_dirichlet_cell]
            phi_f_hat = upwind_phi_f
            
        elif scheme.startswith("LimitedLinearV"):

            try:
                k = float(scheme.split(" ")[1])
            except:
                raise ValueError("limitedLinearV scheme requires a parameter eg. 'scheme = limitedLinearV 1' ")

            # Step 1: Compute linear interpolation (central differencing) as baseline
            phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]
            phi_linear = (phi_cpd_cell_expanded[self.C_senders] * self.gC +
                         phi_cpd_cell_expanded[self.F_receivers] * self.gF).squeeze(2)

            # Step 2: Extract values following OpenFOAM convention
            # In OpenFOAM: phiP = owner, phiN = neighbour (NOT upwind/downwind)
            phi_P = phi_cpd_cell[self.C_senders]  # owner (P)
            phi_N = phi_cpd_cell[self.F_receivers]  # neighbour (N)
            grad_phi_P = grad_phi_cpd_cell[self.C_senders]  # owner gradient
            grad_phi_N = grad_phi_cpd_cell[self.F_receivers]  # neighbour gradient

            # Step 3: Calculate face flux direction (positive: owner->neighbour)
            face_flux = (uvw_face[:, None, 0:3] @ self.CF).squeeze(2)  # φ·n

            # Step 4: Calculate slope ratio r following OpenFOAM NVDTVD::r formula exactly
            # gradf = phiN - phiP (always neighbour - owner)
            gradf = phi_N - phi_P

            # d = C[neighbour] - C[owner] (always this direction)
            d_vector = self.cpd_centroid[self.F_receivers] - self.cpd_centroid[self.C_senders]

            # gradcf depends on flux direction (OpenFOAM NVDTVD::r logic)
            # if (faceFlux > 0): gradcf = d & gradcP
            # else: gradcf = d & gradcN
            gradcf_P = (d_vector[:,:,None] * grad_phi_P).sum(dim=2)  # d & gradcP
            gradcf_N = (d_vector[:,:,None] * grad_phi_N).sum(dim=2)  # d & gradcN

            # Select gradient based on flux direction
            flux_positive = face_flux > 0
            gradcf = torch.where(flux_positive, gradcf_P, gradcf_N)

            # OpenFOAM formula: r = 2*(gradcf/gradf) - 1
            # Handle division by zero with large number check (following OpenFOAM logic)
            mask_large_gradcf = torch.abs(gradcf) >= 1000 * torch.abs(gradf)

            rf = torch.zeros_like(gradf)
            # For normal cases: r = 2*(gradcf/gradf) - 1
            mask_normal = ~mask_large_gradcf & (torch.abs(gradf) > 1e-15)
            rf[mask_normal] = 2 * (gradcf[mask_normal] / gradf[mask_normal]) - 1

            # For large gradcf cases: r = 2*1000*sign(gradcf)*sign(gradf) - 1
            rf[mask_large_gradcf] = 2 * 1000 * torch.sign(gradcf[mask_large_gradcf]) * torch.sign(gradf[mask_large_gradcf]) - 1

            # For zero gradf cases: set r = 1 (no limiting)
            mask_zero_gradf = torch.abs(gradf) <= 1e-15
            rf[mask_zero_gradf] = 1.0

            # Step 5: Apply limitedLinear limiter function: ψ = clamp(2r/k, 0, 1)
            # twoByk = 2.0/max(k, SMALL) in OpenFOAM
            twoByk = 2.0 / max(k, 1e-15)
            pusai_f = torch.clamp(twoByk * rf, 0.0, 1.0)

            # Step 6: Final interpolation using upwind + limiter*(linear - upwind)
            # Determine upwind value based on flux direction
            phi_upwind = torch.where(flux_positive, phi_P, phi_N)
            upwind_phi_f = phi_upwind + pusai_f * (phi_linear - phi_upwind)

            # keep dirichlet boundary value unchange
            upwind_phi_f[self.mask_dirichlet_face] = phi_cpd_cell[self.mask_dirichlet_cell]
            phi_f_hat = upwind_phi_f
            
        else:
            raise ValueError("No support for this scheme")
        
        return phi_f_hat
    
    def cfc_smooth(
        self,
        uvwp_face: torch.Tensor=None,
        uvwp_cpd_cell: torch.Tensor=None,
    ) -> torch.Tensor:
        """
        Cell-face-cell (CFC) smoothing to improve solution accuracy.
        
        Applies a simple arithmetic mean filter to smooth the solution values
        at face centers. This helps to reduce oscillations and improve stability.
        
        Args:
            uvwp_hat_face (torch.Tensor): Face-centered solution values [N_faces, N_variables]
            
        Returns:
            torch.Tensor: Smoothed solution values at face centers [N_faces, N_variables]
        """
        
        # twoway_cpd_neigbor_cell = torch.cat((self.cpd_neighbor_cell,self.cpd_neighbor_cell.flip(0)),dim=1)
        # uvwp_new_smoothed_cpd_cell = (uvwp_cpd_cell + scatter_add(
        #     src = uvwp_cpd_cell[twoway_cpd_neigbor_cell[1]],
        #     index = twoway_cpd_neigbor_cell[0],
        #     dim = 0,
        # ))/(1+scatter_add(
        #     src = torch.ones_like(twoway_cpd_neigbor_cell[1]),
        #     index = twoway_cpd_neigbor_cell[0],
        #     dim = 0,
        # )[:,None])
        mask_non_cyclic_face = (self.face_type != FaceType.CYCLIC)  # CYCLIC
        twoway_cpd_neigbor_cell = torch.cat((self.cpd_neighbor_cell[:,mask_non_cyclic_face],self.cpd_neighbor_cell.flip(0)[:,mask_non_cyclic_face]),dim=1)
        uvwp_new_smoothed_cpd_cell = scatter_mean(
            src = torch.cat((uvwp_face[mask_non_cyclic_face],uvwp_face[mask_non_cyclic_face]),dim=0),
            index = twoway_cpd_neigbor_cell[0],
            dim = 0,
            dim_size = self.num_cpd_cells,
        )
        uvwp_new_smoothed_cpd_cell = self.enforce_boundary_condition(
            uvwp_new_smoothed_cpd_cell
        )
        return uvwp_new_smoothed_cpd_cell