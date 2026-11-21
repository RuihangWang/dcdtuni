import sys
import os
import logging
import math
from abc import ABC, abstractmethod
from typing import Tuple, List

file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

import numpy as np
import torch
import h5py
import pyvista as pv



# 将输出缓冲区设置为0
sys.stdout.flush()





class BaseMeshManager(ABC):
    """
    Abstract base class for mesh managers.

    Provides common functionality for mesh processing, validation, and visualization.
    Subclasses should implement specific mesh format parsing logic.
    """

    def __init__(self):
        """Initialize base mesh manager"""

        # ===== Geometry Data (exported to h5) =====
        self.node_pos = None                    # node|node_pos: [N_points, 3] - XYZ coordinates for each node
        self.face_pos = None                   # face|face_pos: [N_faces, 3] - XYZ coordinates for face centers
        self.cell_pos = None                    # cell|cell_pos: [N_cells, 3] - XYZ coordinates for cell centers

        # ===== Connectivity Data (exported to h5) =====
        self.face_node = None                   # face_node: [N_total_face_node] - node indices for each face
        self.face_node_ptr = None               # face_node_ptr: [N_total_face_node] - pointer array for face_node
        self.cells_node = None                  # cells_node: [N_total_cells_node] - node indices for each cell
        self.cells_node_ptr = None              # cells_node_ptr: [N_total_cells_node] - pointer array for cells_node
        self.cells_face = None                  # cells_face: [N_total_cells_face] - face indices for each cell
        self.cells_face_ptr = None              # cells_face_ptr: [N_total_cells_face] - pointer array for cells_face

        # ===== Face and Cell Properties (exported to h5) =====
        self.face_type = None                  # face|face_type: [N_faces] - face type information
        self.face_area = None                 # face|face_area: [N_faces] - area of each face
        self.face_normal= None               # face|face_normal: [N_faces, 3] - normal vectors for each face
        self.cell_volume = None                # cell|cell_volume: [N_cells] - volume of each cell

        # ===== Compound Attributes (exported to h5) =====
        self.cpd_cell_type = None               # cpd|cell_type: compound cell type information
        self.neighbor_cpd_cell = None           # cpd|neighbor_cell: compound neighbor cell relationships
        self.neighbor_cpd_cell_x = None         # cpd|neighbor_cell_x: extended neighbor relationships
        self.neighbor_cpd_cell_non_cyclic = None # cpd|neighbor_cell_non_cyclic: non-cyclic neighbor relationships
        self.cpd_cell_pos = None                # cpd|cell_pos: [N_compound_cells, 3] - compound cell centers

    @property
    def cpd_centroid(self):
        """Alias for cpd_cell_pos for backward compatibility with visualization code"""
        return self.cpd_cell_pos

        # ===== PyVista Format Data (exported to h5) =====
        self.pv_cells_node = None               # pv_cells_node: PyVista format cell-node connectivity
        self.pv_cells_type = None               # pv_cells_type: PyVista format cell types

        # ===== OpenFOAM-specific Data (exported to h5) =====
        self.boundary_type = None               # face|boundary_type: boundary condition types for faces
        self.face_type_channels = None          # metadata: face type channel information (JSON)
        self.boundary_type_channels = None      # metadata: boundary type channel information (JSON)
        self.boundary_type_codebook = None      # metadata: boundary type codebook (JSON)

        # ===== Edge Data (exported to h5) =====
        self.edge_node = None                   # edge_node: edge-node connectivity
        self.faces_edge = None                  # face_edge: face-edge connectivity
        self.face_edge_ptr = None              # face_edge_ptr: pointer array for face_edge
        self.cells_face_area = None             # cells_face_area: face areas for each cell
        self.cells_face_normal = None           # cells_face_normal: face normals for each cell
        self.cyclic_face = None                 # cyclic_face: cyclic face information

        # ===== field data attributes (exported to h5) =====
        self.field_data = None                  # 未来可能会有修改，这是个字典，且维度为dim=[num_steps,num_cpd_cell,C]

        # ===== Internal attributes (not exported to h5) =====
        self.neighbor_cell = None               # Internal neighbor relationships
        self.case_path = None
        self.patch_list = None


    @abstractmethod
    def _extract_base_mesh_data(self):
        """Extract basic mesh data from source format"""
        pass

    @abstractmethod
    def _calculate_connectivity_data(self):
        """Calculate mesh connectivity data"""
        pass

    def _calculate_face_geometric_properties_vectorized(self, faces_raw: List, node_pos: torch.Tensor, face_pos: torch.Tensor, face_node: torch.Tensor=None, face_node_ptr: torch.Tensor=None,) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Calculate face normals and areas using truly vectorized operations.
        Groups faces by number of vertices and processes each group in parallel.

        Args:
            faces_raw: List of face vertex indices
            node_pos: Node positions [N, 3]
            face_pos: Face center positions [F, 3] (initial estimate)

        Returns:
            Tuple of (face_normal [F, 3], face_area [F], face_pos [F, 3])
            - face_normal: unit normal vectors for each face
            - face_area: area of each face
            - face_pos: accurate face center positions (area-weighted centroids)
        """
        from Parse_mesh.utils.utilities import seperate_domain

        num_faces = len(faces_raw)
        face_normal = torch.zeros(num_faces, 3, dtype=torch.float64)
        face_area = torch.zeros(num_faces, dtype=torch.float64)
        face_pos_accurate = torch.zeros(num_faces, 3, dtype=torch.float64)

        # Use seperate_domain to group faces by number of vertices
        domain_list = seperate_domain(
            cells_ptr=face_node_ptr,
            cells_node=face_node,
            cells_face=None
        )

        # Process each group of faces with same number of vertices
        for n_vertices, local_face_node, _, local_faces_ptr, _ in domain_list:
            if local_face_node is None or len(local_face_node) == 0:
                continue

            # Calculate number of faces in this group
            num_faces_in_group = len(local_face_node) // n_vertices

            # Verify the calculation is correct
            if len(local_face_node) != num_faces_in_group * n_vertices:
                logging.warning(f"Face group size mismatch: {len(local_face_node)} nodes for {num_faces_in_group} faces with {n_vertices} vertices each")
                continue

            # Reshape to [num_faces_in_group, n_vertices]
            local_face_node_reshaped = local_face_node.reshape(num_faces_in_group, n_vertices)
            local_faces_ptr_reshaped = local_faces_ptr.reshape(num_faces_in_group, n_vertices)[:,0]

            # Get vertex positions: [num_faces_in_group, n_vertices, 3]
            vertices_positions = node_pos[local_face_node_reshaped]

            # Calculate normals, areas, and accurate centroids for this group using vectorized operations
            group_normals, group_areas, group_face_pos = self._calculate_polygon_properties_vectorized(
                vertices_positions,
            )

            # Store results back to the main arrays
            face_normal[local_faces_ptr_reshaped] = group_normals
            face_area[local_faces_ptr_reshaped] = group_areas
            face_pos_accurate[local_faces_ptr_reshaped] = group_face_pos

        return face_normal, face_area, face_pos_accurate



    def _calculate_polygon_properties_vectorized(self, vertices_positions: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Calculate normals, areas, and centroids for a batch of polygons with the same number of vertices.
        Uses fan triangulation from polygon centroid to compute properties.

        Args:
            vertices_positions: [num_faces, n_vertices, 3] - vertex positions for each face

        Returns:
            Tuple of (normals [num_faces, 3], areas [num_faces], centroids [num_faces, 3])
            - normals: unit normal vectors for each face
            - areas: area of each face
            - centroids: accurate centroid positions (area-weighted)
        """
        sorted_vertices, unit_face_normals = self._sort_polygon_vertices(vertices_positions)

        # Calculate accurate centroids and areas using triangle decomposition
        face_pos, face_area = self._calculate_polygon_centroids_and_areas(sorted_vertices, unit_face_normals)

        return unit_face_normals, face_area, face_pos
    
    def _sort_polygon_vertices(self, vertices_positions: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Sort vertices of a polygon in counterclockwise order around the face center and calculate areas.

        Args:
            vertices_positions: Tensor of shape [N, P, 3] where N is number of polygons,
                              P is number of vertices per polygon, 3 is spatial dimensions

        Returns:
            Tuple containing:
            - sorted_vertices: Sorted vertices_positions tensor with same shape [N, P, 3]
        """
        N, P, _ = vertices_positions.shape

        # Calculate polygon centroid: [N, 1, 3]
        poly_centroid = vertices_positions.mean(dim=1, keepdim=True)

        # Calculate vectors from centroid to each vertex: [N, P, 3]
        CP = vertices_positions - poly_centroid

        # Take the first vertex vector as pivot: [N, 1, 3]
        pCP = CP[:, 0:1, :]

        # Get vectors to next vertex (cyclic): [num_faces, n_vertices, 3]
        # For each vertex i, get vector to vertex (i+1) % n_vertices
        next_CP = torch.roll(CP, -1, dims=1)

        # Calculate cross products for each triangle formed by center and adjacent vertices
        # cross_products[i, j] = cross(center_to_vertex[i,j], center_to_vertex[i,j+1])
        cross_products = torch.cross(CP, next_CP, dim=2)  # [num_faces, n_vertices, 3]

        # Sum all cross products to get face normal (before normalization)
        # This works because the sum of cross products of a fan triangulation gives the face normal
        normal = torch.sum(cross_products, dim=1)  # [num_faces, 3]
        unit_normal = normal / torch.norm(normal, dim=1, keepdim=True)

        # Calculate angles between pCP and each CP vector using vectorized operations
        # pCP: [N, 1, 3], CP: [N, P, 3]
        pCP_expanded = pCP.expand(-1, P, -1)  # [N, P, 3]

        # Calculate dot products: [N, P]
        dot_products = torch.sum(pCP_expanded * CP, dim=2)  # [N, P]

        # Calculate cross products: [N, P, 3]
        cross_products = torch.cross(pCP_expanded, CP, dim=2)  # [N, P, 3]

        # Project cross products onto normal to get signed magnitudes: [N, P]
        # unit_normal: [N, 3] -> [N, 1, 3] for broadcasting
        unit_normal_expanded = unit_normal.unsqueeze(1)  # [N, 1, 3]
        cross_magnitudes = torch.sum(cross_products * unit_normal_expanded, dim=2)  # [N, P]

        # Calculate angles using atan2 for proper quadrant handling: [N, P]
        # atan2(cross_magnitudes, dot_products) gives angles in [-pi, pi]
        angles = torch.atan2(cross_magnitudes, dot_products)  # [N, P]

        # Convert to [0, 2*pi] range
        angles = torch.where(angles < 0, angles + 2 * math.pi, angles)  # [N, P]

        # Ensure the first angle (pivot vector) is exactly 0
        # This is guaranteed mathematically, but we set it explicitly for numerical stability
        # Check if first angle is close to 2π (360 degrees in radians) and set to 0
        round_check = torch.isclose(
            angles[:,0], 
            torch.full((angles.shape[0],), 2 * math.pi, dtype=angles.dtype, device=angles.device), 
            rtol=1e-5, 
            atol=1e-8
        )
        if round_check.any():
            angles[round_check.squeeze(), 0] = 0.0
            
        # Verify that the first angle is now 0 (this should always pass after the above assignment)
        if not torch.isclose(angles[:,0], torch.full((angles.shape[0],), 0., dtype=angles.dtype, device=angles.device), rtol=1e-5, atol=1e-8).all():
            raise ValueError("First angle is not 0!")
        
        # Handle the case where angles are very close to 2*pi (should be treated as 0)
        # This can happen due to numerical precision issues
        angles = torch.where(torch.abs(angles - 2 * math.pi) < 1e-10, 0.0, angles)

        # Sort vertices by angle
        sorted_indices = torch.argsort(angles, dim=1)  # [N, P]

        # Apply sorting to vertices_positions
        # Use advanced indexing to sort each polygon independently
        batch_indices = torch.arange(N, device=vertices_positions.device).unsqueeze(1).expand(-1, P)  # [N, P]
        sorted_vertices = vertices_positions[batch_indices, sorted_indices]  # [N, P, 3]

        return sorted_vertices, unit_normal

    def _calculate_polygon_areas_shoelace(self, vertices_positions: torch.Tensor, normals: torch.Tensor) -> torch.Tensor:
        """
        Calculate polygon areas using the Shoelace formula (vectorized).
        For 3D polygons, projects them onto their local 2D plane first.

        Args:
            vertices_positions: Tensor of shape [N, P, 3] where N is number of polygons,
                              P is number of vertices per polygon, 3 is spatial dimensions
            normals: Tensor of shape [N, 3] containing unit normal vectors for each polygon

        Returns:
            polygon_areas: Tensor of shape [N] containing area of each polygon
        """
        N, P, _ = vertices_positions.shape

        if P < 3:
            # Cannot form a polygon with less than 3 vertices
            return torch.zeros(N, device=vertices_positions.device, dtype=vertices_positions.dtype)

        # For 3D polygons, we need to project them onto their local 2D plane
        # We'll create a local coordinate system for each polygon

        # Step 1: Create two orthogonal vectors in the polygon plane
        # Use the first edge as one basis vector
        edge1 = vertices_positions[:, 1, :] - vertices_positions[:, 0, :]  # [N, 3]
        edge1_normalized = edge1 / (torch.norm(edge1, dim=1, keepdim=True) + 1e-8)  # [N, 3]

        # The second basis vector is orthogonal to both edge1 and normal
        edge2_normalized = torch.cross(normals, edge1_normalized, dim=1)  # [N, 3]
        edge2_normalized = edge2_normalized / (torch.norm(edge2_normalized, dim=1, keepdim=True) + 1e-8)  # [N, 3]

        # Step 2: Project all vertices onto the 2D plane defined by edge1_normalized and edge2_normalized
        # Choose the first vertex as the origin of the local coordinate system
        origin = vertices_positions[:, 0:1, :]  # [N, 1, 3]
        relative_vertices = vertices_positions - origin  # [N, P, 3]

        # Project onto the two basis vectors to get 2D coordinates
        u_coords = torch.sum(relative_vertices * edge1_normalized.unsqueeze(1), dim=2)  # [N, P]
        v_coords = torch.sum(relative_vertices * edge2_normalized.unsqueeze(1), dim=2)  # [N, P]

        # Step 3: Apply the Shoelace formula in 2D
        # Shoelace formula: Area = 0.5 * |sum(x_i * y_{i+1} - x_{i+1} * y_i)|

        # Get next coordinates (cyclic)
        u_next = torch.roll(u_coords, -1, dims=1)  # [N, P]
        v_next = torch.roll(v_coords, -1, dims=1)  # [N, P]

        # Calculate cross products: x_i * y_{i+1} - x_{i+1} * y_i
        cross_terms = u_coords * v_next - u_next * v_coords  # [N, P]

        # Sum all cross terms and take absolute value, then multiply by 0.5
        polygon_areas = 0.5 * torch.abs(torch.sum(cross_terms, dim=1))  # [N]

        return polygon_areas

    def _calculate_polygon_centroids_and_areas(self, vertices_positions: torch.Tensor, normals: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Calculate polygon centroids and areas using triangle decomposition method (vectorized).
        This implements the same algorithm as OpenFOAM and VTK for arbitrary 3D polygons.

        Algorithm:
        1. For triangles (3 vertices): Direct vertex average
        2. For polygons (4+ vertices): Area-weighted triangle decomposition
           - Estimate face center (vertex average)
           - Build triangles from center to each edge
           - Calculate centroid and area for each triangle
           - Area-weighted average of triangle centroids

        Args:
            vertices_positions: Tensor of shape [N, P, 3] where N is number of polygons,
                              P is number of vertices per polygon, 3 is spatial dimensions
            normals: Tensor of shape [N, 3] containing unit normal vectors for each polygon
                    (not used for triangles, but needed for consistency check)

        Returns:
            Tuple of (centroids, areas):
            - centroids: Tensor of shape [N, 3] containing centroid coordinates of each polygon
            - areas: Tensor of shape [N] containing area of each polygon

        Reference:
            See docs/OpenFOAM_PyVista_Geometry_Complete_Analysis.md for detailed algorithm
        """
        N, P, _ = vertices_positions.shape
        device = vertices_positions.device
        dtype = vertices_positions.dtype

        if P < 3:
            # Cannot form a polygon with less than 3 vertices
            raise ValueError(f"Cannot form a polygon with {P} vertices (minimum 3 required)")

        if P == 3:
            # Triangle: centroid = vertex average, area = 0.5 * ||cross product||
            centroids = torch.mean(vertices_positions, dim=1)  # [N, 3]

            # Calculate area using cross product
            edge1 = vertices_positions[:, 1, :] - vertices_positions[:, 0, :]  # [N, 3]
            edge2 = vertices_positions[:, 2, :] - vertices_positions[:, 0, :]  # [N, 3]
            cross = torch.cross(edge1, edge2, dim=1)  # [N, 3]
            areas = 0.5 * torch.norm(cross, dim=1)  # [N]

            return centroids, areas

        # Polygon (4+ vertices): Area-weighted triangle decomposition
        # Step 1: Estimate face center (vertex average)
        fc_est = torch.mean(vertices_positions, dim=1)  # [N, 3]

        # Step 2-3: Triangle decomposition + area-weighted centroid calculation
        # For each edge (v_i, v_{i+1}), build triangle (fc_est, v_i, v_{i+1})

        # Get current and next vertices
        v_curr = vertices_positions  # [N, P, 3]
        v_next = torch.roll(vertices_positions, -1, dims=1)  # [N, P, 3]

        # Triangle vertices: (fc_est, v_curr, v_next)
        # Triangle centroid = (fc_est + v_curr + v_next) / 3
        # To avoid division, we store 3 × centroid
        tri_centroid_times_3 = fc_est.unsqueeze(1) + v_curr + v_next  # [N, P, 3]

        # Triangle area vector (actually 2 × area vector)
        # OpenFOAM: n = (nextPoint - thisPoint) × (fCentre - thisPoint)
        # Triangle vertices: (v_curr, v_next, fc_est)
        # n = (v_next - v_curr) × (fc_est - v_curr)
        edge1 = v_next - v_curr  # [N, P, 3]
        edge2 = fc_est.unsqueeze(1) - v_curr  # [N, P, 3]
        tri_normal_times_2 = torch.cross(edge1, edge2, dim=2)  # [N, P, 3]

        # Triangle area (actually 2 × area)
        tri_area_times_2 = torch.norm(tri_normal_times_2, dim=2)  # [N, P]

        # Step 4: Area-weighted average
        # sum_a = Σ(2 × area_i)
        sum_a = torch.sum(tri_area_times_2, dim=1)  # [N]

        # sum_ac = Σ(2 × area_i × 3 × centroid_i)
        sum_ac = torch.sum(tri_area_times_2.unsqueeze(2) * tri_centroid_times_3, dim=1)  # [N, 3]

        # Handle degenerate case (area ≈ 0) following OpenFOAM's approach
        # OpenFOAM uses ROOTVSMALL threshold (typically ~1e-10)
        ROOTVSMALL = 1e-10

        # Initialize with estimated center (for degenerate cases)
        centroids = fc_est.clone()
        areas = torch.zeros(N, device=device, dtype=dtype)

        # Only compute for valid (non-degenerate) polygons
        valid_mask = sum_a >= ROOTVSMALL  # [N]

        if valid_mask.any():
            # face_centroid = sum_ac / (3 × sum_a)
            # sumAc is (2×area × 3×centroid), sumA is (2×area)
            # so sumAc/sumA = 3×centroid, divide by 3 to get centroid
            centroids[valid_mask] = sum_ac[valid_mask] / (3.0 * sum_a[valid_mask].unsqueeze(1))

            # Total area = sum_a / 2
            areas[valid_mask] = 0.5 * sum_a[valid_mask]

        return centroids, areas

    def _calculate_cell_geometric_properties_pyramid_decomposition(
        self,
        cells_face: torch.Tensor,
        cells_face_ptr: torch.Tensor,
        face_pos: torch.Tensor,
        face_area: torch.Tensor,
        face_normal: torch.Tensor,
        num_cells: int
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Calculate cell centers (centroids) and volumes using pyramid decomposition method.
        This implements the OpenFOAM algorithm from primitiveMeshTools.C.

        Algorithm:
        1. Estimate cell center using face centers average (scatter_mean)
        2. For each cell, decompose into pyramids with base = face, apex = estimated center
        3. Calculate pyramid volume and centroid for each pyramid
        4. Volume-weighted average of pyramid centroids gives final cell center

        Args:
            cells_face: [N_total_cells_face] - face indices for each cell
            cells_face_ptr: [N_total_cells_face] - pointer array for cells_face
            face_pos: [N_faces, 3] - face center positions
            face_area: [N_faces] - face areas
            face_normal: [N_faces, 3] - face normals
            num_cells: number of cells

        Returns:
            Tuple of (cell_pos, cell_volume):
            - cell_pos: [N_cells, 3] - cell center positions (centroids)
            - cell_volume: [N_cells, 1] - cell volumes

        Reference:
            OpenFOAM: src/OpenFOAM/meshes/primitiveMesh/primitiveMeshCheck/primitiveMeshTools.C
            Lines 284-377: makeCellCentresAndVols()
        """
        from Parse_mesh.utils.utilities import seperate_domain
        from torch_scatter import scatter

        logging.info("  Calculating cell centers and volumes using pyramid decomposition...")

        # Step 1: Estimate cell center using face centers average (scatter_mean)
        # This is equivalent to OpenFOAM's cEst calculation
        est_cell_pos = scatter(
            src=face_pos[cells_face],
            index=cells_face_ptr,
            dim=0,
            reduce='mean'
        )  # [num_cells, 3]

        logging.info("    Estimated cell centers using face centers average")

        # Initialize outputs
        cell_pos = torch.zeros(num_cells, 3, dtype=torch.float64)
        cell_volume = torch.zeros(num_cells, 1, dtype=torch.float64)

        # Use seperate_domain to group cells by number of faces
        domain_list = seperate_domain(
            cells_ptr=cells_face_ptr,
            cells_node=None,
            cells_face=cells_face
        )

        # Process each group of cells with same number of faces
        for n_faces, _, local_cells_face, local_cells_ptr, _ in domain_list:
            if local_cells_face is None or len(local_cells_face) == 0:
                continue

            # Calculate number of cells in this group
            num_cells_in_group = len(local_cells_face) // n_faces

            # Verify the calculation is correct
            if len(local_cells_face) != num_cells_in_group * n_faces:
                logging.warning(f"Cell group size mismatch: {len(local_cells_face)} faces for {num_cells_in_group} cells with {n_faces} faces each")
                continue

            # Reshape to [num_cells_in_group, n_faces]
            local_cells_face_reshaped = local_cells_face.reshape(num_cells_in_group, n_faces)
            local_cells_ptr_reshaped = local_cells_ptr.reshape(num_cells_in_group, n_faces)[:, 0]

            # Get face centers and areas for this group: [num_cells_in_group, n_faces, 3]
            face_centers = face_pos[local_cells_face_reshaped]  # [num_cells_in_group, n_faces, 3]
            face_areas = face_area[local_cells_face_reshaped]  # [num_cells_in_group, n_faces]
            face_normals = face_normal[local_cells_face_reshaped]  # [num_cells_in_group, n_faces, 3]

            # Get estimated cell centers for this group
            est_centers = est_cell_pos[local_cells_ptr_reshaped]  # [num_cells_in_group, 3]

            # Calculate cell centers and volumes using vectorized pyramid decomposition
            group_cell_pos, group_cell_volume = self._calculate_polyhedron_properties_vectorized(
                face_centers=face_centers,
                face_areas=face_areas,
                face_normals=face_normals,
                est_centers=est_centers
            )

            # Store results back to the main arrays
            cell_pos[local_cells_ptr_reshaped] = group_cell_pos
            cell_volume[local_cells_ptr_reshaped] = group_cell_volume

        logging.info("    ✓ Cell centers and volumes calculated using pyramid decomposition")
        logging.info("    Cell volume statistics:")
        logging.info("      Min volume: %.6e", torch.min(cell_volume).item())
        logging.info("      Max volume: %.6e", torch.max(cell_volume).item())
        logging.info("      Mean volume: %.6e", torch.mean(cell_volume).item())

        return cell_pos, cell_volume

    def _calculate_polyhedron_properties_vectorized(
        self,
        face_centers: torch.Tensor,
        face_areas: torch.Tensor,
        face_normals: torch.Tensor,
        est_centers: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Calculate cell centers (centroids) and volumes for polyhedra using pyramid decomposition.
        This is a vectorized implementation of OpenFOAM's algorithm.

        Algorithm (following OpenFOAM primitiveMeshTools.C lines 328-362):
        1. For each face of the cell, form a pyramid with:
           - Base: the face (with center fc and area fA)
           - Apex: estimated cell center (cEst)
        2. Orient face normal to point outward from cell center
        3. Calculate pyramid volume: V = (1/3) × (fA · (fc - cEst))
        4. Calculate pyramid centroid: pc = (3/4) × fc + (1/4) × cEst
        5. Volume-weighted average: cell_center = Σ(V × pc) / Σ(V)

        Args:
            face_centers: [num_cells, n_faces, 3] - face centers for each cell
            face_areas: [num_cells, n_faces] - face areas for each cell
            face_normals: [num_cells, n_faces, 3] - face normals for each cell
            est_centers: [num_cells, 3] - estimated cell centers

        Returns:
            Tuple of (cell_pos, cell_volume):
            - cell_pos: [num_cells, 3] - final cell centers (centroids)
            - cell_volume: [num_cells, 1] - cell volumes

        Reference:
            OpenFOAM: primitiveMeshTools.C lines 328-362
        """
        num_cells, n_faces, _ = face_centers.shape
        device = face_centers.device
        dtype = face_centers.dtype

        # Expand est_centers to match face dimensions: [num_cells, n_faces, 3]
        est_centers_expanded = est_centers.unsqueeze(1).expand(-1, n_faces, -1)

        # Calculate vectors from estimated center to face centers: [num_cells, n_faces, 3]
        fc_minus_cEst = face_centers - est_centers_expanded

        # Orient face normals to point outward from cell center
        # Check if face normal points away from cell center
        dot_products = torch.sum(fc_minus_cEst * face_normals, dim=2)  # [num_cells, n_faces]

        # If dot product < 0, face normal points inward, so flip it
        oriented_normals = face_normals.clone()
        flip_mask = dot_products < 0
        oriented_normals[flip_mask] = -oriented_normals[flip_mask]

        # Calculate face area vectors with oriented normals: [num_cells, n_faces, 3]
        # fA = face_area × oriented_normal
        face_area_vectors = face_areas.unsqueeze(2) * oriented_normals  # [num_cells, n_faces, 3]

        # Calculate pyramid volumes × 3: pyr3Vol = fA · (fc - cEst)
        # This is the dot product of face area vector and (fc - cEst)
        pyr3Vol = torch.sum(face_area_vectors * fc_minus_cEst, dim=2)  # [num_cells, n_faces]

        # Calculate pyramid centroids: pc = (3/4) × fc + (1/4) × cEst
        pyramid_centroids = 0.75 * face_centers + 0.25 * est_centers_expanded  # [num_cells, n_faces, 3]

        # Volume-weighted sum: Σ(pyr3Vol × pc)
        sum_vol_centroid = torch.sum(pyr3Vol.unsqueeze(2) * pyramid_centroids, dim=1)  # [num_cells, 3]

        # Total volume × 3: Σ(pyr3Vol)
        sum_vol_times_3 = torch.sum(pyr3Vol, dim=1)  # [num_cells]

        # Handle degenerate cases (volume ≈ 0)
        VSMALL = 1e-15
        valid_mask = torch.abs(sum_vol_times_3) > VSMALL

        # Initialize with estimated centers (for degenerate cases)
        cell_pos = est_centers.clone()
        cell_volume = torch.zeros(num_cells, 1, device=device, dtype=dtype)

        # Only compute for valid (non-degenerate) cells
        if valid_mask.any():
            # Cell center = Σ(pyr3Vol × pc) / Σ(pyr3Vol)
            cell_pos[valid_mask] = sum_vol_centroid[valid_mask] / sum_vol_times_3[valid_mask].unsqueeze(1)

            # Cell volume = |Σ(pyr3Vol)| / 3
            cell_volume[valid_mask] = (torch.abs(sum_vol_times_3[valid_mask]) / 3.0).unsqueeze(1)

        # Warn about degenerate cells
        if (~valid_mask).any():
            num_degenerate = (~valid_mask).sum().item()
            logging.warning(f"    Found {num_degenerate} degenerate cells (volume ≈ 0), using estimated centers")

        return cell_pos, cell_volume

    def create_edge_visualizations(self, vis_output_dir: str, **kwargs):
        """
        Create visualizations using available visualization backends.

        Args:
            vis_output_dir: Directory to save visualization files
            **kwargs: Additional arguments passed to visualization methods
        """
        from .utils.visualization_base import PyVistaVisualization
        
        os.makedirs(vis_output_dir, exist_ok=True)
        
        case_name = os.path.basename(self.case_path)
        
        pyvista_vis = PyVistaVisualization(self)
        pyvista_vis.create_visualization(vis_output_dir, case_name, **kwargs)

    def export_vtm_files(self, output_dir: str):
        """Export mesh data using VTM multiblock format with NO grouping.
        Each OpenFOAM patch becomes a top-level block so ParaView's MultiBlock Inspector
        can toggle visibility per patch directly, similar to the native OpenFOAM reader.
        VTU files are named by patch names for better identification.
        """
        import pyvista as pv

        os.makedirs(output_dir, exist_ok=True)

        case_name = os.path.basename(self.case_path)

        # Create main multiblock dataset
        main_multiblock = pv.MultiBlock()

        # Add full mesh as the first block
        full_mesh_grid = self._create_full_mesh_grid()
        main_multiblock.append(full_mesh_grid, "internal_mesh")

        # Append every patch as a top-level block (no semantic grouping)
        patch_names_in_order: List[str] = []
        for patch_name, patch_info in self.patch_list.items():
            face_indices = torch.arange(patch_info["start_idx_face"], patch_info["end_idx_face"])
            boundary_grid = self._create_boundary_grid(face_indices, patch_name)
            if boundary_grid is not None:
                main_multiblock.append(boundary_grid, patch_name)
                patch_names_in_order.append(patch_name)

        # Save VTM file - PyVista automatically handles patch-named VTU files
        vtm_path = os.path.join(output_dir, f"{case_name}_complete.vtm")
        main_multiblock.save(vtm_path)

        logging.info("✅ VTM multiblock exported to: %s", vtm_path)
        logging.info("   - Internal mesh: 1 block")
        logging.info("   - Boundary patches (no grouping): %d", len(patch_names_in_order))
        logging.info("   📁 Open in ParaView: %s", vtm_path)

    def _create_full_mesh_grid(self):
        """Create PyVista UnstructuredGrid for the full mesh"""
        import pyvista as pv

        # Calculate degrees based on neighbor_cpd_cell_x (following the existing logic)
        if hasattr(self, 'neighbor_cpd_cell_x') and self.neighbor_cpd_cell_x is not None:
            from torch_geometric import utils as pyg_utils
            edge_in_degree = pyg_utils.degree(self.neighbor_cpd_cell_x[1], num_nodes=self.cpd_cell_pos.shape[0])
            edge_out_degree = pyg_utils.degree(self.neighbor_cpd_cell_x[0], num_nodes=self.cpd_cell_pos.shape[0])
            edge_degree = edge_in_degree + edge_out_degree

            logging.info("cpd_neighbor_cell_x degree statistics:")
            logging.info("  In-degree  - max: %.1e, mean: %.1e, min: %.1e",
                        edge_in_degree.max().item(),
                        edge_in_degree.mean().item(),
                        edge_in_degree.min().item())
            logging.info("  Out-degree - max: %.1e, mean: %.1e, min: %.1e",
                        edge_out_degree.max().item(),
                        edge_out_degree.mean().item(),
                        edge_out_degree.min().item())
            logging.info("  Total degree - max: %.1e, mean: %.1e, min: %.1e",
                        edge_degree.max().item(),
                        edge_degree.mean().item(),
                        edge_degree.min().item())

        # Convert tensor data to numpy arrays
        points = self.node_pos.cpu().numpy() if isinstance(self.node_pos, torch.Tensor) else np.asarray(self.node_pos)
        cells = self.pv_cells_node.cpu().numpy() if isinstance(self.pv_cells_node, torch.Tensor) else np.asarray(self.pv_cells_node)
        cell_types = self.pv_cells_type.cpu().numpy() if isinstance(self.pv_cells_type, torch.Tensor) else np.asarray(self.pv_cells_type)

        # Ensure points are 3D
        if points.shape[1] == 2:
            points = np.c_[points, np.zeros(points.shape[0])]

        # Create PyVista UnstructuredGrid
        grid = pv.UnstructuredGrid(cells, cell_types, points)

        return grid

    def _create_boundary_grid(self, face_indices: torch.Tensor, patch_name: str):
        """Create PyVista UnstructuredGrid for a specific boundary patch"""
        if len(face_indices) == 0:
            logging.info("No faces found for boundary patch %s", patch_name)
            return None

        # Get basic data (following original implementation)
        face_node = self.face_node.long().squeeze()
        face_edge_ptr = self.face_edge_ptr.long().squeeze()

        # Get node indices for faces of current type (vectorized)
        cur_face_node_mask = torch.isin(face_edge_ptr, face_indices)
        cur_face_node = face_node[cur_face_node_mask]
        cur_face_edge_ptr_raw = face_edge_ptr[cur_face_node_mask]

        # Vectorized face index remapping
        max_face_idx = torch.max(face_indices).item() + 1
        face_global_to_local = torch.full((max_face_idx,), -1, dtype=torch.long)
        face_global_to_local[face_indices] = torch.arange(len(face_indices), dtype=torch.long)

        # Apply the mapping to get local face indices
        cur_face_edge_ptr = face_global_to_local[cur_face_edge_ptr_raw]

        # Get unique nodes for local mapping
        unique_nodes, node_inverse_indices = torch.unique(cur_face_node, return_inverse=True)

        # Extract positions for unique nodes
        cur_nodes_pos = self.node_pos[unique_nodes].cpu().numpy()

        # Remap face_node indices from global to local
        cur_face_node_local = node_inverse_indices.cpu().numpy()

        # Create face type data for current faces only
        face_type_value = int(self.face_type[face_indices[0]].item())
        face_type_data = torch.full((len(face_indices), 1), face_type_value, dtype=torch.long).cpu().numpy()

        # Extract face normals for current face type
        cur_face_normal= self.face_normal[face_indices].cpu().numpy()

        # Convert to PyVista format
        node_pos = cur_nodes_pos
        face_node = cur_face_node_local
        faces_ptr = cur_face_edge_ptr.cpu().numpy()

        # Calculate number of nodes per face using scatter
        face_node_counts = torch.bincount(torch.from_numpy(faces_ptr)).numpy()

        # Build PyVista cells array
        pv_cells = []
        pv_cell_types = []

        face_start_idx = 0
        for face_idx, num_nodes in enumerate(face_node_counts):
            if num_nodes == 0:
                continue

            # Get nodes for this face
            current_face_nodes = face_node[face_start_idx:face_start_idx + num_nodes]

            # Create PyVista cell format: [num_nodes, node1, node2, ...]
            pv_cell = [num_nodes] + current_face_nodes.tolist()
            pv_cells.extend(pv_cell)

            # Determine VTK cell type based on number of nodes
            if num_nodes == 3:
                pv_cell_types.append(5)  # VTK_TRIANGLE
            elif num_nodes == 4:
                pv_cell_types.append(9)  # VTK_QUAD
            else:
                pv_cell_types.append(7)  # VTK_POLYGON

            face_start_idx += num_nodes

        # Ensure points are 3D
        if node_pos.shape[1] == 2:
            node_pos = np.c_[node_pos, np.zeros(node_pos.shape[0])]

        # Create PyVista UnstructuredGrid
        pv_cells = np.array(pv_cells)
        pv_cell_types = np.array(pv_cell_types)

        grid = pv.UnstructuredGrid(pv_cells, pv_cell_types, node_pos)

        # Add data
        grid.cell_data["face_type"] = face_type_data.flatten()
        grid.cell_data["face_normal"] = cur_face_normal

        return grid

    def save_to_hdf5(self, output_path: str, case_name: str, **metadata):
        """
        Save mesh data to HDF5 format with structured naming convention.

        Args:
            output_path: Path where the HDF5 file will be saved
            case_name: Name of the case for metadata
            **metadata: Additional metadata to store
        """
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # Write to HDF5
        with h5py.File(output_path, 'w') as hf:
            # Add metadata
            hf.attrs['case_name'] = case_name
            hf.attrs['dimension'] = '3D'

            # Add custom metadata
            for key, value in metadata.items():
                hf.attrs[key] = value

            # Geometry data
            if self.node_pos is not None:
                hf.create_dataset('node|pos', data=self.node_pos.to(torch.float32).numpy())
            if self.face_pos is not None:
                hf.create_dataset('face|face_pos', data=self.face_pos.to(torch.float32).numpy())
            if self.cell_pos is not None:
                hf.create_dataset('cell|centroid', data=self.cell_pos.to(torch.float32).numpy())

            # Connectivity data
            if self.face_node is not None:
                hf.create_dataset('face_node', data=self.face_node.numpy())
            if self.face_node_ptr is not None:
                hf.create_dataset('face_node_ptr', data=self.face_node_ptr.numpy())
            if self.cells_node is not None:
                hf.create_dataset('cells_node', data=self.cells_node.numpy())
            if self.cells_node_ptr is not None:
                hf.create_dataset('cells_node_ptr', data=self.cells_node_ptr.numpy())
            if self.cells_face is not None:
                hf.create_dataset('cells_face', data=self.cells_face.numpy())
            if self.cells_face_ptr is not None:
                hf.create_dataset('cells_face_ptr', data=self.cells_face_ptr.numpy())

            # Face and cell properties
            if self.face_type is not None:
                hf.create_dataset('face|face_type', data=self.face_type.numpy())
            if self.face_area is not None:
                hf.create_dataset('face|face_area', data=self.face_area.to(torch.float32).numpy())
            if self.cell_volume is not None:
                hf.create_dataset('cell|cells_volume', data=self.cell_volume.to(torch.float32).numpy())

            # Compound attributes
            if self.cpd_cell_type is not None:
                hf.create_dataset('cpd|cell_type', data=self.cpd_cell_type.numpy())
            if self.neighbor_cpd_cell is not None:
                hf.create_dataset('cpd|neighbor_cell', data=self.neighbor_cpd_cell.numpy())
            if self.neighbor_cpd_cell_x is not None:
                hf.create_dataset('cpd|neighbor_cell_x', data=self.neighbor_cpd_cell_x.numpy())
            if self.neighbor_cpd_cell_non_cyclic is not None:
                hf.create_dataset('cpd|neighbor_cell_non_cyclic', data=self.neighbor_cpd_cell_non_cyclic.numpy())
            if self.cpd_cell_pos is not None:
                hf.create_dataset('cpd|centroid', data=self.cpd_cell_pos.to(torch.float32).numpy())

            # PyVista format data
            if self.pv_cells_node is not None:
                hf.create_dataset('pv_cells_node', data=self.pv_cells_node.numpy())
            if self.pv_cells_type is not None:
                hf.create_dataset('pv_cells_type', data=self.pv_cells_type.numpy())

            # Save channels metadata if available
            try:
                if hasattr(self, 'face_type_channels') and self.face_type_channels:
                    import json as _json
                    hf.attrs['face_type_channels'] = _json.dumps(self.face_type_channels)
            except Exception as _e:
                logging.warning("Failed to write face_type_channels metadata: %s", _e)

            # Write boundary_type channels metadata if available
            try:
                if hasattr(self, 'boundary_type_channels') and self.boundary_type_channels:
                    import json as _json
                    hf.attrs['boundary_type_channels'] = _json.dumps(self.boundary_type_channels)
            except Exception as _e:
                logging.warning("Failed to write boundary_type_channels metadata: %s", _e)

            # Write boundary_type codebook if available
            try:
                if hasattr(self, 'boundary_type_codebook') and self.boundary_type_codebook:
                    import json as _json
                    hf.attrs['boundary_type_codebook'] = _json.dumps(self.boundary_type_codebook)
            except Exception as _e:
                logging.warning("Failed to write boundary_type_codebook metadata: %s", _e)

            # Write patch_list if available
            try:
                if hasattr(self, 'patch_list') and self.patch_list:
                    import json as _json
                    hf.attrs['patch_list'] = _json.dumps(self.patch_list)
                    logging.info("Successfully saved patch_list to HDF5 metadata")
            except Exception as _e:
                logging.warning("Failed to write patch_list metadata: %s", _e)

            # Save boundary_type if present
            try:
                if hasattr(self, 'boundary_type') and self.boundary_type is not None:
                    hf.create_dataset('face|boundary_type', data=self.boundary_type.numpy())
            except Exception as _e:
                logging.warning("Failed to write face|boundary_type dataset: %s", _e)

            if self.edge_node is not None:
                hf.create_dataset('edge_node', data=self.edge_node.numpy())
            if self.faces_edge is not None:
                hf.create_dataset('face_edge', data=self.faces_edge.numpy())
            if self.face_edge_ptr is not None:
                hf.create_dataset('face_edge_ptr', data=self.face_edge_ptr.numpy())
            if self.cells_face_area is not None:
                hf.create_dataset('cells_face_area', data=self.cells_face_area.to(torch.float32).numpy())
            if self.cells_face_normal is not None:
                hf.create_dataset('cells_face_normal', data=self.cells_face_normal.to(torch.float32).numpy())
            if self.cyclic_face is not None:
                hf.create_dataset('cyclic_face', data=self.cyclic_face.numpy())

            # Save field data if available
            if hasattr(self, 'field_data') and self.field_data is not None:
                try:
                    for field_name, field_tensor in self.field_data.items():
                        if field_name == "time_list":
                            # Persist time_list both as a dataset and as an attribute for backward compatibility.
                            time_array = np.asarray(field_tensor, dtype=np.float32)
                            hf.create_dataset("time_list", data=time_array)
                            hf.attrs['time_list'] = time_array
                        else:
                            # Save field tensor data
                            if isinstance(field_tensor, torch.Tensor):
                                hf.create_dataset(field_name, data=field_tensor.to(torch.float32).numpy())
                            else:
                                logging.warning(f"Field {field_name} is not a tensor, skipping")
                    logging.info("Successfully saved field data to HDF5")
                except Exception as e:
                    logging.warning(f"Failed to save field data: {e}")

            # Save UDF field if available
            if hasattr(self, 'udf_cpd_cell') and self.udf_cpd_cell is not None:
                try:
                    hf.create_dataset('cpd|cell_udf', data=self.udf_cpd_cell.to(torch.float32).numpy())
                    logging.info("Successfully saved UDF field to HDF5")
                except Exception as e:
                    logging.warning(f"Failed to save UDF field: {e}")

            # Save case statistics if available (sum, sum_sq, min, max)
            if hasattr(self, 'case_statistics') and self.case_statistics is not None:
                try:
                    import json as _json

                    # Create a statistics group
                    stats_group = hf.create_group('statistics')

                    # Save scalar metadata
                    stats_group.attrs['sample_count'] = self.case_statistics['sample_count']
                    stats_group.attrs['final_time'] = self.case_statistics['final_time']
                    stats_group.attrs['field_names'] = _json.dumps(self.case_statistics['field_names'])

                    # Save statistics arrays for each field (sum, sum_sq, min, max)
                    for stat_type in ['sum', 'sum_sq', 'min', 'max']:
                        if stat_type in self.case_statistics:
                            stat_dict = self.case_statistics[stat_type]
                            if stat_dict:  # Only save if not empty
                                # Convert dict to arrays for HDF5 storage
                                field_names = self.case_statistics['field_names']
                                stat_values = [stat_dict[field_name] for field_name in field_names]
                                stats_group.create_dataset(stat_type, data=np.array(stat_values, dtype=np.float64))

                    logging.info("Successfully saved case statistics (sum, sum_sq, min, max) to HDF5")
                except Exception as e:
                    logging.warning(f"Failed to save case statistics: {e}")

        logging.info("Successfully saved mesh to HDF5: %s", output_path)

    def create_edge_visualizations(self, output_dir: str, case_name: str, enable_pyvista: bool = True, **kwargs):
        """
        Create visualizations using available visualization backends.

        Args:
            output_dir: Directory to save visualization files
            case_name: Name of the case for file naming
            enable_matplotlib: Whether to create matplotlib visualization
            enable_pyvista: Whether to create PyVista visualization
            **kwargs: Additional arguments passed to visualization methods
        """
        try:
            from .utils.visualization_base import PyVistaVisualization

            vis_output_dir = os.path.join(output_dir, 'Graph_visualization')
            os.makedirs(vis_output_dir, exist_ok=True)


            if enable_pyvista:
                pyvista_vis = PyVistaVisualization(self)
                pyvista_vis.create_visualization(vis_output_dir, case_name, **kwargs)

        except ImportError as e:
            logging.error("Visualization dependencies not available: %s", e)
        except Exception as e:
            logging.error("Visualization creation failed: %s", e)
