import sys
import os
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)

import torch.nn as nn

class FV_base(nn.Module):
    def __init__(self):
        """

        """
        super(FV_base, self).__init__()
        
        # Cache for geometry and coefficients to avoid repeated calculations
        self._geometry_cached = False
        self._cache_graph_hash = None
        
        # Cached geometry quantities
        self.cells_face = None
        self.cells_face_ptr = None
        self.owner = None
        self.cpd_neighbor = None
        self.cells_volume = None
        self.cells_face_unv = None
        self.face_type = None
        self.face_pos = None
        self.face_area = None
        self.cell_type = None
        self.cpd_centroid = None
        self.cells_face_surface_vec = None
        
        # cyclic face info
        self.patch1_faces = None
        self.patch2_faces = None
        self.patch1_cells = None
        self.patch1_cpd_cells = None
        self.patch2_cells = None
        self.patch2_cpd_cells = None
        
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
        self.source_term = None
        self.dt_cell = None
        
        # Cached face interpolation geometry (for avoiding repeated calculations in interpolation)
        self._face_interpolation_cache = {}
        
    def register_geometrics(self, graph_node, graph_cell, graph_face, graph_Index):
        raise NotImplementedError("Subclasses must implement this method")
    
    def enforce_boundary_condition(self, uvwp_new, uvw_hat, uvw_old, graph_node, graph_cell, graph_face, graph_Index):
        raise NotImplementedError("Subclasses must implement this method")