"""
Base visualization classes for mesh data.

Provides common visualization functionality for different mesh formats.
"""

import os
import logging
import numpy as np
from abc import ABC, abstractmethod


class BaseVisualization(ABC):
    """Base class for mesh visualization"""
    
    def __init__(self, mesh_manager):
        """
        Initialize visualization with mesh manager.
        
        Args:
            mesh_manager: Mesh manager instance containing mesh data
        """
        self.mesh_manager = mesh_manager
        
    @abstractmethod
    def create_visualization(self, output_dir: str, case_name: str, **kwargs):
        """Create visualization output"""


class PyVistaVisualization(BaseVisualization):
    """PyVista-based visualization for ParaView compatibility"""
    
    def create_visualization(self, output_dir: str, case_name: str, **kwargs):
        """Create PyVista visualization files"""
        
        logging.info("Creating PyVista visualization...")
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Prepare data
        cpd_centroid = self.mesh_manager.cpd_centroid.detach().cpu().numpy()
        cpd_neighbor_cell_x = self.mesh_manager.neighbor_cpd_cell_x.detach().cpu().numpy()
        
        # Create point cloud
        self._create_point_cloud(cpd_centroid, case_name, output_dir)
        
        # Create edge visualization
        self._create_edge_visualization(cpd_centroid, cpd_neighbor_cell_x, case_name, output_dir)
        
        logging.info("VTU files created in: %s", output_dir)
    
    def _create_point_cloud(self, positions, case_name, output_dir):
        """Create point cloud visualization for mesh nodes"""
        import pyvista as pv

        points = pv.PolyData(positions)

        # Add node type information using self.mesh_manager.node_type
        if hasattr(self.mesh_manager, 'cpd_cell_type') and self.mesh_manager.cpd_cell_type is not None:
            # Get node types and convert to numpy
            cpd_cell_type = self.mesh_manager.cpd_cell_type.detach().cpu().numpy()

            # Ensure we have the right number of node types
            if len(cpd_cell_type) == len(positions):
                points['CpdCellType'] = cpd_cell_type
            else:
                logging.warning("Node type array length (%d) doesn't match positions length (%d)",
                              len(cpd_cell_type), len(positions))
                # Create default node types
                points['CpdCellType'] = np.zeros(len(positions))
        else:
            logging.warning("node_type not available, using default values")
            points['CpdCellType'] = np.zeros(len(positions))

        # Add RGB colors based on node types
        rgb_colors = self._generate_colors(points['CpdCellType'])
        points['RGB'] = rgb_colors

        # Add size information (uniform size for all nodes)
        sizes = np.ones(len(positions)) * 0.8
        points['Size'] = sizes

        # Save points
        points_save_path = os.path.join(output_dir, f"{case_name}_nodes.vtk")
        points.save(points_save_path)
        
        return points
    
    def _create_edge_visualization(self, positions, neighbor_cell, case_name, output_dir):
        """Create edge visualization"""
        import pyvista as pv
        
        # Filter valid edges
        valid_edge_mask = neighbor_cell[0] != neighbor_cell[1]
        valid_edges = neighbor_cell[:, valid_edge_mask]
        
        if valid_edges.shape[1] > 0:
            lines = []
            for i in range(valid_edges.shape[1]):
                start_idx = valid_edges[0, i]
                end_idx = valid_edges[1, i]
                lines.extend([2, start_idx, end_idx])
            
            lines = np.array(lines)
            line_mesh = pv.PolyData(positions, lines=lines)
            line_mesh['EdgeIndex'] = np.arange(valid_edges.shape[1])
            
            edges_save_path = os.path.join(output_dir, f"{case_name}_edge_visualization.vtk")
            line_mesh.save(edges_save_path)
    
    def _generate_colors(self, node_types):
        """Generate RGB colors for different node types based on CpdCellType enum"""
        # CpdCellType enum values:
        # NORMAL = 0, INFLOW = 1, OUTFLOW = 2, WALL_BOUNDARY = 3,
        # PRESS_POINT = 4, IN_WALL = 5, EMPTY = 6, ZERO_GRADIENT = 7, CYCLIC = 8
        color_map = {
            0: [0.7, 0.9, 1.0],  # Light blue for NORMAL nodes
            1: [0.0, 1.0, 0.0],  # Green for INFLOW
            2: [1.0, 0.0, 0.0],  # Red for OUTFLOW
            3: [0.4, 0.4, 0.4],  # Dark gray for WALL_BOUNDARY
            4: [1.0, 1.0, 0.0],  # Yellow for PRESS_POINT
            5: [0.6, 0.3, 0.0],  # Brown for IN_WALL
            6: [1.0, 1.0, 1.0],  # White for EMPTY
            7: [1.0, 0.5, 0.0],  # Orange for ZERO_GRADIENT
            8: [1.0, 0.0, 1.0],  # Magenta for CYCLIC
        }

        rgb_colors = np.zeros((len(node_types), 3))
        for i, node_type in enumerate(node_types):
            if int(node_type) in color_map:
                rgb_colors[i] = color_map[int(node_type)]
            else:
                rgb_colors[i] = [0.5, 0.5, 0.5]  # Default gray for unknown types

        return rgb_colors
