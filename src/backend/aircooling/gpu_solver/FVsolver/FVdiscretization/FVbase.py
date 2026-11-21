import sys
import os
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

import torch.nn as nn

class FVfield:
    
    def __init__(self,):
        
        self.rho_cpd_cell = None
        self.uvw_cpd_cell = None
        self.p_cpd_cell = None
        self.p_rgh_cpd_cell = None
        self.T_cpd_cell = None
        
        self.rho_face = None
        self.uvw_face = None
        self.p_face = None
        self.p_rgh_face = None
        self.T_face = None
        
        self.grad_rho_cpd_cell = None
        self.grad_uvw_cpd_cell = None
        self.grad_p_cpd_cell = None
        self.grad_p_rgh_cpd_cell = None
        self.grad_T_cpd_cell = None
        
        self.grad_rho_face = None
        self.grad_uvw_face = None
        self.grad_p_face = None
        self.grad_p_rgh_face = None
        self.grad_T_face = None
        
        
        

class FV_base(nn.Module):
    def __init__(self):
        """
        Base class for finite volume discretization.
        
        Provides a common interface for caching geometric quantities, PDE coefficients,
        and boundary conditions. Subclasses should implement specific discretization
        and boundary enforcement logic.
        """
        super(FV_base, self).__init__()
        
        # Cache for geometry and coefficients to avoid repeated calculations
        self.fvpatch_batch = None
        self.bcpatch_batch = None
        
        # Cached geometry quantities
        self.cells_face = None
        self.cells_face_ptr = None
        self.owner = None
        self.cpd_neighbor = None
        self.cell_volume = None
        self.cells_face_unv = None
        self.face_type = None
        self.face_pos = None
        self.face_area = None
        self.cell_type = None
        self.cpd_cell_pos = None
        self.cells_face_surface_vec = None
        
        # # cyclic face info
        # self.patch1_faces = None
        # self.patch2_faces = None
        # self.patch1_cells = None
        # self.patch1_cpd_cells = None
        # self.patch2_cells = None
        # self.patch2_cpd_cells = None
        
        # Cached masks
        self.mask_interior_face = None
        self.mask_boundary_face = None
        self.mask_interior_cell = None
        self.mask_boundary_cell = None
        
        # Cached PDE coefficients
        self.theta_PDE = None
        self.unsteady_coeff = None
        self.continuity_eq_coeff = None
        self.convection_coeff = None
        self.grad_p_coeff = None
        self.nu_coeff = None
        # Per-forward effective momentum diffusivity set by the turbulence
        # hook (Turbulence design section 4); None means "use
        # nu_coeff" so solvers without the hook are untouched.  For the
        # Laminar model the hook stores nu_coeff ITSELF (same object), which
        # is what keeps the laminar path bit-identical.
        self._nu_eff_face = None
        self.source_term = None
        self.dt_cell = None
        
        # idea gas constants
        self.gamma = None
        self.R = None
        
        # for Euler equations
        self.conv_num_method = "JST"
        
        # Cached face interpolation geometry (for avoiding repeated calculations in interpolation)
        self._face_interpolation_cache = {}
        
    def register_geometrics(self,):
        raise NotImplementedError("Subclasses must implement this method")
    
    def enforce_bc_1st(self,):
        raise NotImplementedError("Subclasses must implement this method")
    
    def enforce_cyclic(self,):
        raise NotImplementedError("Subclasses must implement this method")
    
    def enforce_bc_mixed(self,):
        raise NotImplementedError("Subclasses must implement this method")