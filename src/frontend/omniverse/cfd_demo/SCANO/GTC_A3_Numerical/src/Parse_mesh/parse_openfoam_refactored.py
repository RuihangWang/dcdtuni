"""
Refactored OpenFOAM mesh parser with improved efficiency and object-oriented design.

This module provides an optimized OpenFOAM mesh parser that inherits from BaseMeshManager
and implements efficient boundary processing with merged loops and separated concerns.
"""

import sys
import os
import logging
import time
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings("ignore", message=".*Sparse CSR tensor support is in beta state.*")

# 配置日志级别，确保 INFO 级别的消息能够显示
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

from Parse_mesh.openfoamparser.mesh_parser import FoamMesh
import numpy as np
import torch
from torch_scatter import scatter
import pyvista as pv

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm


from Utils.utilities import FaceType
from Parse_mesh.parse_base import BaseMeshManager
from Parse_mesh.utils.utilities import build_k_hop_edge_index

from Parse_mesh.parse_boundary import BoundaryParser


def _is_ignored_vtk_dir_name(name: str) -> bool:
    lowered = name.lower()
    return (
        "backup" in lowered
        or "pre_rebuild" in lowered
        or "pre_fix" in lowered
        or "disabled" in lowered
    )


class OpenFOAMManager(BaseMeshManager):
    """
    Optimized OpenFOAM mesh manager with unified boundary processing and improved efficiency.

    This class inherits from BaseMeshManager and implements OpenFOAM-specific mesh processing
    with the following optimizations:
    - Unified boundary processing in single loop
    - Merged connectivity calculations
    - Separated visualization concerns
    - Vectorized geometric calculations
    """

    def __init__(self, case_path: str, plt_edge_vis: bool = False,
                 valid_bc_file_list: List[str] = None):
        """
        Initialize OpenFOAM mesh manager.

        Args:
            case_path: Path to OpenFOAM case directory
            plt_edge_vis: Whether to enable visualization at cpd neighbor cell connection
        """
        super().__init__()

        self.case_path = case_path
        self.plt_edge_vis = plt_edge_vis

        # Store validation parameters
        self.valid_bc_file_list = valid_bc_file_list or ["rho", "U", "T", "p", "p_rgh"]

        # Load OpenFOAM mesh
        mesh = FoamMesh(self.case_path)

        # Note: VTK files are not needed during initialization
        # They will be checked and converted when reading field data (in read_field_data method)

        # Extract mesh properties
        self.num_nodes = mesh.num_point
        self.num_faces = mesh.num_face
        # Now that openfoamparser is fixed, both should give the same result
        self.num_cells = mesh.num_cell  # Should now be correct after fixing mesh_parser.py

        # Verify the fix worked
        assert self.num_cells == len(mesh.cell_faces), f"Bug still exists: mesh.num_cells={self.num_cells} != len(mesh.cell_faces)={len(mesh.cell_faces)}"
        self.num_internal_faces = mesh.num_internal_face

        logging.info(f"📊 OpenFOAM mesh properties:")
        logging.info(f"   Points: {self.num_nodes}")
        logging.info(f"   Faces: {self.num_faces}")
        logging.info(f"   Cells: {self.num_cells} (from openfoamparser)")
        logging.info(f"   Internal faces: {self.num_internal_faces}")

        # Raw data arrays
        self.node_pos = torch.from_numpy(np.asarray(mesh.points))
        self.faces_raw = mesh.faces
        self.owner_raw = torch.from_numpy(np.asarray(mesh.owner)).long()
        self.neighbour_raw = torch.from_numpy(np.asarray(mesh.neighbour)).long()
        self.boundary_patch = mesh.boundary
        self.cells_face_raw = mesh.cell_faces

        # Additional OpenFOAM-specific attributes
        self.edge_node = None
        self.faces_edge = None
        self.face_edge_ptr = None
        self.cells_face_area = None
        self.cells_face_normal = None
        self.cyclic_face = None
        self.neighbor_cpd_cell_x = None

    def _process_mesh_pipeline(self):
        """Execute the complete mesh processing pipeline"""

        # Step 1: Extract base mesh data and connectivity in unified manner
        start = time.time()
        self._extract_base_mesh_data()
        end = time.time()
        logging.info(f"Base mesh data extraction took {end - start:.2f} seconds")

        # Step 2: Calculate geometric properties
        start = time.time()
        self._calculate_derived_geometric_data()
        end = time.time()
        logging.info(f"Derived geometric data calculation took {end - start:.2f} seconds")

        # Step 3: Process connectivity data with merged loops
        start = time.time()
        self._calculate_connectivity_data()
        end = time.time()
        logging.info(f"Connectivity data calculation took {end - start:.2f} seconds")

        # Step 4: Unified boundary processing (replaces multiple separate methods)
        start = time.time()
        self._process_boundaries_unified()
        end = time.time()
        logging.info(f"Boundary processing took {end - start:.2f} seconds")

        # Step 4.5: Export boundary conditions to TOML format
        start = time.time()
        self._export_boundary_conditions_to_toml()
        end = time.time()
        logging.info(f"Boundary conditions export took {end - start:.2f} seconds")

        # Step 5: Calculate geometric properties (normals, areas, volumes)
        start = time.time()
        self._calculate_geometric_properties()
        end = time.time()
        logging.info(f"Geometric properties calculation took {end - start:.2f} seconds")

        # Step 6: Calculate compound attributes
        start = time.time()
        self._calculate_compound_attributes()
        end = time.time()
        logging.info(f"Compound attributes calculation took {end - start:.2f} seconds")

        # Step 7: Validate mesh
        # self._validate_mesh_consistency()

        # Step 8: Validate face areas and normals statistics
        # self._validate_face_statistics()

        # Step 9: Create visualizations if requested
        if self.plt_edge_vis:
            self.create_edge_visualizations(self.case_path+'/h5', os.path.basename(self.case_path), enable_pyvista=True)

        # Step 10: Read field data from time directories
        start = time.time()
        self._read_field_data(self.case_path, self.patch_list)
        end = time.time()
        logging.info(f"Field data reading took {end - start:.2f} seconds")

        # # Step 11: Calculate case statistics
        start = time.time()
        self._calculate_statistics()
        end = time.time()
        logging.info(f"Case statistics calculation took {end - start:.2f} seconds")

    def _calculate_statistics(self):
        """
        Calculate sum and sum² for final time step target data across all cpd_cells.

        This method computes raw statistics (sum and sum²) for each field component
        that will be aggregated across all cases to compute dataset-level mean and variance.

        The statistics are stored in self.case_statistics and will be saved to HDF5.

        Statistics computed per field component:
        - sum: Σ(x_i) across all cpd_cells in this case
        - sum_sq: Σ(x_i²) across all cpd_cells in this case
        - min: min(x_i) across all cpd_cells in this case
        - max: max(x_i) across all cpd_cells in this case
        - sample_count: Number of cpd_cells in this case

        Dataset-level statistics will be computed as:
        - mean = Σ(sum_i) / Σ(sample_count_i)
        - var = [Σ(sum_sq_i) - N*mean²] / (N-1)
        """
        logging.info("Computing sum and sum² for final time step target data...")

        if not hasattr(self, 'field_data') or self.field_data is None:
            logging.info("No field data available, skipping statistics calculation")
            self.case_statistics = None
            return

        # Check if we have any time steps
        time_list = self.field_data.get('time_list', [])
        if not time_list:
            logging.warning("No time steps found in field data, skipping statistics calculation")
            self.case_statistics = None
            return

        logging.info(f"Computing statistics for final time step (t={time_list[-1]})")

        # Initialize statistics dictionary
        self.case_statistics = {
            'sum': {},
            'sum_sq': {},
            'min': {},
            'max': {},
            'sample_count': 0,
            'field_names': [],
            'final_time': time_list[-1]
        }

        # Process each field (excluding time_list)
        field_names = []
        for field_name, field_tensor in self.field_data.items():
            if field_name == 'time_list':
                continue

            if not isinstance(field_tensor, torch.Tensor):
                logging.warning(f"Field {field_name} is not a tensor, skipping")
                continue

            if field_tensor.numel() == 0:
                logging.warning(f"Field {field_name} is empty, skipping")
                continue

            # Get final time step data: [n_cpd_cells, n_components]
            logging.info(f"Field {field_name} shape: {field_tensor.shape}")
            final_data = field_tensor[-1]  # Last time step: [n_cpd_cells, n_components]

            # Handle different field dimensions
            if field_name == 'uvw_cpd_cell':
                # Velocity field: [n_cpd_cells, 3] -> compute statistics for each component
                component_names = ['u', 'v', 'w']
                for i, comp_name in enumerate(component_names):
                    comp_data = final_data[:, i]  # [n_cpd_cells]
                    full_field_name = f"{field_name}_{comp_name}"
                    self._compute_field_sum_and_sumsq(comp_data, full_field_name)
                    field_names.append(full_field_name)
            else:
                # Scalar fields: [n_cpd_cells, 1] -> squeeze to [n_cpd_cells]
                scalar_data = final_data.squeeze()  # [n_cpd_cells]
                self._compute_field_sum_and_sumsq(scalar_data, field_name)
                field_names.append(field_name)

        # Store metadata
        self.case_statistics['field_names'] = field_names
        self.case_statistics['sample_count'] = self.cpd_cell_pos.shape[0]  # Number of cpd_cells

        # Log summary
        logging.info(f"Statistics computed for {len(field_names)} fields:")
        for field_name in field_names:
            sum_val = self.case_statistics['sum'][field_name]
            min_val = self.case_statistics['min'][field_name]
            max_val = self.case_statistics['max'][field_name]
            logging.info(f"  {field_name}: sum={sum_val:.6e}, min={min_val:.6e}, max={max_val:.6e}")

        logging.info(f"Sample count (cpd_cells): {self.case_statistics['sample_count']}")
        logging.info("Case statistics calculation completed")

    def _compute_field_sum_and_sumsq(self, field_data: torch.Tensor, field_name: str):
        """
        Compute sum and sum² for a single field component across all cpd_cells.

        Args:
            field_data: Tensor of shape [n_cpd_cells] containing field values
            field_name: Name of the field for storage
        """
        # Convert to float64 for numerical stability in summation
        data = field_data.double()

        # Compute sum and sum of squares across all cpd_cells
        sum_val = torch.sum(data).item()
        sum_sq_val = torch.sum(data ** 2).item()
        min_val = torch.min(data).item()
        max_val = torch.max(data).item()

        # Store in case_statistics
        self.case_statistics['sum'][field_name] = sum_val
        self.case_statistics['sum_sq'][field_name] = sum_sq_val
        self.case_statistics['min'][field_name] = min_val
        self.case_statistics['max'][field_name] = max_val

    def _extract_base_mesh_data(self):
        """Extract base mesh data with optimized edge processing"""
        # Note: self.node_pos is already set in __init__ from mesh.points

        # Process faces and edges in unified loop
        face_node_list = []
        face_node_ptr_list = []
        edge_dict = {}
        edge_node_list = []
        faces_edge_list = []
        face_edge_ptr_list = []

        for face_idx, face in enumerate(self.faces_raw):
            # Process face nodes
            face_node_list.extend(face)
            face_node_ptr_list.extend([face_idx] * len(face))

            # Process edges for this face
            current_face_edges = []
            num_nodes = len(face)

            for i in range(num_nodes):
                n1, n2 = face[i], face[(i+1) % num_nodes]
                edge = tuple(sorted([int(n1), int(n2)]))

                if edge not in edge_dict:
                    edge_idx = len(edge_node_list)
                    edge_dict[edge] = edge_idx
                    edge_node_list.append(edge)
                else:
                    edge_idx = edge_dict[edge]

                current_face_edges.append(edge_idx)
                face_edge_ptr_list.append(face_idx)

            faces_edge_list.extend(current_face_edges)

        # Convert to tensors
        self.face_node = torch.tensor(face_node_list, dtype=torch.long).squeeze()
        self.face_node_ptr = torch.tensor(face_node_ptr_list, dtype=torch.long).squeeze()
        self.edge_node = torch.tensor(edge_node_list, dtype=torch.long).squeeze().T # [2, E]
        self.faces_edge = torch.tensor(faces_edge_list, dtype=torch.long).squeeze()
        self.face_edge_ptr = torch.tensor(face_edge_ptr_list, dtype=torch.long).squeeze()

    def _calculate_derived_geometric_data(self):
        """Calculate face centers using vectorized operations"""
        self.face_pos = scatter(
            src=self.node_pos[self.face_node.squeeze()],
            index=self.face_node_ptr.squeeze(),
            dim=0,
            reduce='mean'
        ) # prior estimate, not accurate

    def _calculate_connectivity_data(self):
        """Calculate connectivity data with merged cell processing"""
        # Process cells_face data
        cells_face_list = []
        cells_face_ptr_list = []

        # Merged processing: cells_node, pv_cells_node, and centroid in single loop
        cells_node_list = []
        cells_node_ptr_list = []
        pv_cells_node_list = []
        pv_cells_type_list = []

        for cell_idx in range(self.num_cells):
            cell_face_indices = self.cells_face_raw[cell_idx]

            # Process cells_face
            cells_face_list.extend(cell_face_indices)
            cells_face_ptr_list.extend([cell_idx] * len(cell_face_indices))

            # Collect unique nodes from all faces
            nodes_set = set()
            for face_idx in cell_face_indices:
                face_nodes = self.faces_raw[face_idx]
                nodes_set.update(face_nodes)

            # Process cells_node
            cell_nodes = list(nodes_set)
            cells_node_list.extend(cell_nodes)
            cells_node_ptr_list.extend([cell_idx] * len(cell_nodes))

            # Process PyVista format in same loop
            pv_cells_type_list.append(pv.CellType.POLYHEDRON)
            face_connectivity = [len(cell_face_indices)]

            for face_idx in cell_face_indices:
                face_nodes = self.faces_raw[face_idx]
                face_connectivity.append(len(face_nodes))
                face_connectivity.extend(face_nodes)

            pv_cells_node_list.extend([len(face_connectivity)] + face_connectivity)

        # Convert to tensors
        self.cells_face = torch.tensor(cells_face_list, dtype=torch.long)
        self.cells_face_ptr = torch.tensor(cells_face_ptr_list, dtype=torch.long)
        self.cells_node = torch.tensor(cells_node_list, dtype=torch.long)
        self.cells_node_ptr = torch.tensor(cells_node_ptr_list, dtype=torch.long)
        self.pv_cells_node = torch.tensor(pv_cells_node_list, dtype=torch.long)
        self.pv_cells_type = torch.tensor(pv_cells_type_list, dtype=torch.long)
        # Note: cell_pos will be calculated later using pyramid decomposition method
        # No need to read from pyvista here

    def _process_boundaries_unified(self):
        """
        New unified boundary processing using BoundaryParser.
        - face_type: single-channel, derived solely from polyMesh/boundary (FaceType enum)
        - boundary_type: multi-channel per field from 0/ (variable-specific enums)
        - neighbor_cell and cyclic_face set accordingly
        """
        logging.info("Processing boundaries with BoundaryParser...")

        parser = BoundaryParser(
            case_path=self.case_path,
            faces_raw=self.faces_raw,
            owner_raw=self.owner_raw,
            neighbour_raw=self.neighbour_raw,
            boundary_patch=self.boundary_patch,
            valid_bc_file_list=self.valid_bc_file_list,
            num_cells=self.num_cells,  # Pass num_cells parameter
            num_internal_faces=self.num_internal_faces   # Pass num_internal_faces parameter
        )
        result = parser.process()

        # Set attributes
        self.neighbor_cell = result['neighbor_cell']
        self.cyclic_face = result['cyclic_face']
        self.face_type = result['face_type']            # [F, 1]
        self.boundary_type = result['boundary_type']    # [F, C]
        self.boundary_type_channels = result['fields_order']
        self.boundary_type_codebook = result.get('boundary_type_codebook', None)
        self.patch_list = result.get("patch_list", None) # 这部分代码要继续在src/Parse_mesh/parse_boundary.py实现，本质上就是把mesh_example/Cylinder_flow_3D_cyclic/constant/polyMesh/boundary这个里面的内容解析为字典并保存到h5中去

        # For HDF5 compatibility: BaseMeshManager.save_to_hdf5 writes self.faces_type to 'face|face_type'
        self.faces_type = self.face_type

    def _export_boundary_conditions_to_toml(self):
        """
        Export boundary conditions from 0/ directory to TOML format.
        Creates case_path/h5/bc/ directory and saves TOML files there.
        """
        logging.info("Exporting boundary conditions to TOML format...")

        # Import the export function from parse_boundary
        from Parse_mesh.parse_boundary import export_boundary_conditions_standalone

        try:
            export_boundary_conditions_standalone(self.case_path)
            logging.info("Boundary conditions successfully exported to TOML format")
        except Exception as e:
            logging.warning(f"Failed to export boundary conditions to TOML: {e}")

    def calc_cpd_neighbor_cell_x(
        self,
        khop:int=None,
    ):

        if khop is None:
            # Calculate cpd_neighbor_cell_x
            ''' 这里要临时读取config.toml，如果用户没有提前在h5文件夹下写好config.toml文件，那么这里尝试去读取src/Parse_mesh/default_config.toml这个相对路径下的默认配置 '''
            try:
                import tomlkit
                with open(os.path.join(self.case_path, "h5", "config.toml"), 'r', encoding='utf-8') as f:
                    self.fvschemes_config = tomlkit.load(f)
                    khop = self.fvschemes_config['stencil']['khops']
            except:
                with open(os.path.join(os.path.dirname(__file__), "default_config.toml"), 'r', encoding='utf-8') as f:
                    self.fvschemes_config = tomlkit.load(f)
                    khop = self.fvschemes_config['stencil']['khops']
                    logging.warning("No config.toml found in h5 folder, using default config.toml")

        # boundary face needs extra k-hop+1 edge for a wider stencil
        if khop < 2:
            khop = 2
            logging.warning(f"khop was set to {khop}, khop should be larger than 1 for boundary face, set to 2")

        neighbor_cpd_cell = self.neighbor_cpd_cell.long()

        ''' >>> 全局调用k-hop edge >>> '''
        extra_edge_index = []
        twoway_neighbor_cpd_cell = torch.cat((neighbor_cpd_cell,neighbor_cpd_cell.flip(0)),dim=1)
        for k in range(2, khop+1):
            extra_edge_index.append(build_k_hop_edge_index(twoway_neighbor_cpd_cell,k=k))
        extra_edge_index = torch.cat((extra_edge_index), dim=1) # 此时刚出k-hop是包含所有内部边的且dual edge的
        extra_edge_index_unique = torch.unique(
            extra_edge_index[:,~(extra_edge_index[0]==extra_edge_index[1])].sort(0)[0],
            dim=1
        ) # 排除自环然后收缩为单向边, 注意这里的extra edge是即包含boundary face也包含interior cell的
        self.neighbor_cpd_cell_x = torch.cat((neighbor_cpd_cell, extra_edge_index_unique), dim=1)
        ''' <<< 全局调用k-hop edge <<< '''

        # 注意后续在WLSQ梯度重构的时候，不要让边界面元与边界面元出现连接边: 这里是打补丁，后续需要优化
        cpd_cell_type = self.cpd_cell_type.long().squeeze()
        mask_cell_interior = (cpd_cell_type == FaceType.NORMAL)  # NORMAL
        mask_cell_boundary = ~mask_cell_interior
        mask_valid_nb_cell_x = ~(mask_cell_boundary[self.neighbor_cpd_cell_x[0]] & mask_cell_boundary[self.neighbor_cpd_cell_x[1]])
        self.neighbor_cpd_cell_x = self.neighbor_cpd_cell_x[:,mask_valid_nb_cell_x]

        ''' >>> 额外为边界层添加k-hop+1跳的邻居 >>> '''
        # cpd_cell_type = self.cpd_cell_type.long().squeeze()
        # BC_face_mask = ~(cpd_cell_type==FaceType.NORMAL)  # NORMAL
        # extra_boundary_edge = build_k_hop_edge_index(twoway_cpd_neighbor_cell,k=khop+1)
        # extra_boundary_edge = extra_boundary_edge[:,
        #     (BC_face_mask[extra_boundary_edge[0]]&(~BC_face_mask[extra_boundary_edge[1]])) |\
        #     (~BC_face_mask[extra_boundary_edge[0]]&(BC_face_mask[extra_boundary_edge[1]])) |\
        #     (BC_face_mask[extra_boundary_edge[0]]&(BC_face_mask[extra_boundary_edge[1]]))
        # ] # only those connected with boudary face`s edge will be saved
        # extra_boundary_edge_unique = torch.unique(
        #     extra_boundary_edge[:,~(extra_boundary_edge[0]==extra_boundary_edge[1])].sort(0)[0],
        #     dim=1
        # )
        # self.cpd_neighbor_cell_x = torch.cat((cpd_neighbor_cell, extra_edge_index_unique, extra_boundary_edge_unique), dim=1)
        ''' <<< 额外为边界层添加k-hop+1跳的邻居 <<< '''

    def _calculate_geometric_properties(self):
        """Calculate face normals, areas, and cell volumes using optimized methods"""
        logging.info("Calculating geometric properties...")

        # Use base class method for face normals and areas, also return area-weighted face centers
        self.face_normal, self.face_area, self.face_pos = self._calculate_face_geometric_properties_vectorized(
            self.faces_raw, self.node_pos, self.face_pos, face_node=self.face_node, face_node_ptr=self.face_node_ptr
        )

        # Calculate cell centers and volumes using pyramid decomposition (OpenFOAM method)
        # Call base class method
        self.cell_pos, self.cell_volume = self._calculate_cell_geometric_properties_pyramid_decomposition(
            cells_face=self.cells_face,
            cells_face_ptr=self.cells_face_ptr,
            face_pos=self.face_pos,
            face_area=self.face_area,
            face_normal=self.face_normal,
            num_cells=self.num_cells
        )

        # Calculate cell-face normals and areas
        self._calculate_cell_face_properties()

        # Validate face normals using flux closure test
        self._validate_face_normals()

        # Validate all volumes are positive
        if torch.any(self.cell_volume <= 0):
            raise ValueError("Found non-positive cell volumes!")

        logging.info("  ✓ All cell volumes are positive")

    def _calculate_cell_face_properties(self):
        """Calculate cell-face normals and areas using vectorized operations"""
        # Get cell centers for all cells that have faces
        cells_centers = self.cell_pos[self.cells_face_ptr]
        faces_centers_expanded = self.face_pos[self.cells_face]

        # Calculate vectors from cell centers to face centers
        cell_to_face_vectors = faces_centers_expanded - cells_centers

        # Get face normals for each cell-face pair
        face_normal_expanded = self.face_normal[self.cells_face]

        # Check orientation and flip if needed
        dot_products = torch.sum(cell_to_face_vectors * face_normal_expanded, dim=1)
        flip_mask = dot_products < 0

        cells_face_normal = face_normal_expanded.clone()
        cells_face_normal[flip_mask] = -cells_face_normal[flip_mask]

        # Get face areas for each cell-face pair
        cells_face_area = self.face_area[self.cells_face]

        self.cells_face_normal = cells_face_normal
        self.cells_face_area = cells_face_area.reshape(-1, 1)

        logging.info("  Calculated normals and areas for %d faces", len(self.face_normal))
        logging.info("  Cell-face pairs: %d", len(self.cells_face_normal))
        
    def _validate_face_normals(self):
        """
        Validate that cells-face normals and areas are correctly computed
        by checking that the sum of (face normal × face area) for each cell is approximately zero.
        This uses the flux closure principle.
        """
        logging.info("  Validating face normals using flux closure test...")

        # Calculate weighted normals (normal * area)
        surface_vector = self.cells_face_normal * self.cells_face_area  # [total_cell_faces, 3]

        # Sum for each cell using scatter
        flux_sum = scatter(
            src=surface_vector,
            index=self.cells_face_ptr,
            dim=0,
            reduce='sum'
        )  # [num_cells, 3]

        # Calculate magnitude of flux sum for each cell
        flux_sum_magnitude = torch.norm(flux_sum, dim=1)  # [num_cells]

        # logging.info statistics
        logging.info("    Flux closure test results (should be close to zero):")
        logging.info("    Min normalized flux: %.8e", torch.min(flux_sum_magnitude).item())
        logging.info("    Max normalized flux: %.8e", torch.max(flux_sum_magnitude).item())
        logging.info("    Mean normalized flux: %.8e", torch.mean(flux_sum_magnitude).item())
        logging.info("    Median normalized flux: %.8e", torch.median(flux_sum_magnitude).item())

        # Count cells with closure error above threshold
        threshold = 1e-5
        bad_cells = torch.sum(flux_sum_magnitude > threshold).item()

        if bad_cells > 0:
            logging.warning("    Cells with normalized flux > %s: %d out of %d (%.2e%%)",
                           threshold, bad_cells, len(flux_sum_magnitude), bad_cells/len(flux_sum_magnitude)*100)
            # Show worst cases
            worst_indices = torch.argsort(flux_sum_magnitude, descending=True)[:min(5, bad_cells)]
            logging.warning("    Worst cell flux closure values:")
            for idx in worst_indices:
                logging.warning("      Cell %d: %.8e", idx, flux_sum_magnitude[idx].item())
        else:
            logging.info("    ✓ All cells pass flux closure test (normalized flux < %s)", threshold)

        # Raise error if too many cells fail the test
        # if bad_cells > len(flux_sum_magnitude) * 0.01:  # More than 1% of cells fail
        #     raise ValueError(f"Flux closure test failed: {bad_cells}/{len(flux_sum_magnitude)} cells "
        #                    f"({bad_cells/len(flux_sum_magnitude)*100:.2f}%) have normalized flux > {threshold}")

    def _validate_face_statistics(self):
        """Validate face normal and area statistics"""
        logging.info("  Face normal statistics:")
        face_normal_lengths = torch.norm(self.face_normal, dim=1)
        logging.info("    Min length: %.6e", torch.min(face_normal_lengths).item())
        logging.info("    Max length: %.6e", torch.max(face_normal_lengths).item())
        logging.info("    Mean length: %.6e", torch.mean(face_normal_lengths).item())
        logging.info("    Std deviation: %.6e", torch.std(face_normal_lengths).item())

        # Check if normals are unit vectors (should be close to 1.0)
        non_unit_normals = torch.sum(torch.abs(face_normal_lengths - 1.0) > 1e-6).item()
        if non_unit_normals > 0:
            logging.warning("    ⚠️  WARNING: %d faces have non-unit normals (may indicate calculation issues)", non_unit_normals)
            # Show some examples
            non_unit_mask = torch.abs(face_normal_lengths - 1.0) > 1e-6
            non_unit_indices = torch.where(non_unit_mask)[0][:3]  # Show first 3
            for idx in non_unit_indices:
                logging.warning("      Face %d: normal length = %.6e", idx, face_normal_lengths[idx].item())
        else:
            logging.info("    ✓ All face normals are unit vectors")

        logging.info("  Face area statistics:")
        logging.info("    Min area: %.6e", torch.min(self.face_area).item())
        logging.info("    Max area: %.6e", torch.max(self.face_area).item())
        logging.info("    Mean area: %.6e", torch.mean(self.face_area).item())
        logging.info("    Total surface area: %.6e", torch.sum(self.face_area).item())

        # Check for zero or negative areas
        bad_areas = torch.sum(self.face_area <= 0).item()
        if bad_areas > 0:
            logging.error("❌ CRITICAL: Found %d faces with zero or negative areas!", bad_areas)
            raise ValueError(f"Found {bad_areas} faces with zero or negative areas!")
        else:
            logging.info("    ✓ All face areas are positive")

    def _calculate_cell_volumes(self):
        """Calculate cell volumes using divergence theorem"""
        # Get face centers for all cell-face pairs
        face_pos_cells_face = self.face_pos[self.cells_face]

        # Linear function: f(x) = x at each face center
        linear_function_values = face_pos_cells_face

        # Calculate f·n for each cell-face pair
        flux_values = (linear_function_values[:,None,:] @ self.cells_face_normal[:,:,None]).squeeze(2)

        # Multiply by face areas
        weighted_flux = flux_values*self.cells_face_area

        # Sum for each cell using scatter
        total_flux = scatter(
            src=weighted_flux,
            index=self.cells_face_ptr,
            dim=0,
            reduce='sum'
        ).abs()

        # Volume = (1/3) * total_flux (divergence theorem for f(x) = x)
        self.cell_volume = total_flux / 3.0

        # Validate volumes
        negative_volumes = torch.sum(self.cell_volume < 0).item()
        if negative_volumes > 0:
            logging.error("  ❌ CRITICAL: %d cells have negative volumes!", negative_volumes)
            # Show some examples of negative volumes
            negative_mask = self.cell_volume.squeeze() < 0
            negative_indices = torch.where(negative_mask)[0][:5]  # Show first 5
            for idx in negative_indices:
                logging.error("    Cell %d: volume = %.2e", idx, self.cell_volume[idx].item())
            raise ValueError(f"Found {negative_volumes} cells with negative volumes!")
        else:
            logging.info("  ✓ All cell volumes are positive")

    def _calculate_compound_attributes(self):
        """Calculate compound attributes for boundary faces as virtual cells (multi-channel),
        ordered strictly by patch_list to keep indices consistent with patches.
        """
        logging.info("Calculating compound attributes (patch-ordered)...")

        faces_type = self.faces_type.long()  # [F] or [F, C]
        faces_type_first = faces_type[:, 0] if faces_type.dim() == 2 else faces_type  # [F]
        neighbor_cell = self.neighbor_cell.long()  # [2, F]
        face_pos = self.face_pos  # [F, 3]
        cell_pos = self.cell_pos  # [Ncell, 3]
        num_channels = faces_type.shape[1] if faces_type.dim() == 2 else 1

        # First block: original internal cells
        first_block = torch.full((cell_pos.shape[0], num_channels), 0, dtype=torch.long)
        cpd_cell_type_parts = [first_block]
        cpd_cell_pos_parts = [cell_pos]
        neighbor_cpd_cell = neighbor_cell.clone()

        # Iterate patches to append boundary faces as virtual cells in patch order
        next_cpd_index = int(cell_pos.shape[0])
        total_added_noncyclic = 0
        total_added_cyclic = 0

        patch_names = list(self.patch_list.keys()) if isinstance(self.patch_list, dict) else []
        for patch_name in patch_names:
            info = self.patch_list[patch_name]
            # Face index range for this patch (inclusive-exclusive)
            s_face = info.get('start_idx_face', info.get('start_face_idx', None))
            e_face = info.get('end_idx_face', info.get('end_face_idx', None))
            if s_face is None or e_face is None or int(e_face) <= int(s_face):
                logging.warning("Patch '%s' has invalid face index range; skip in cpd build", patch_name)
                # still record empty range for cpd indices
                self.patch_list[patch_name]['start_idx_cpd_cell'] = next_cpd_index
                self.patch_list[patch_name]['end_idx_cpd_cell'] = next_cpd_index
                continue
            s_face = int(s_face); e_face = int(e_face)

            face_ids = torch.arange(s_face, e_face, dtype=torch.long)

            # Check patch type (cyclic or not)
            ptype = str(info.get('type', info.get('patchType', ''))).lower()
            is_cyclic = ('cyclic' in ptype) or bool(info.get('is_cyclic', False))

            # Append face centers as virtual cell centroids
            cur_face_pos = face_pos[s_face:e_face]
            cpd_cell_pos_parts.append(cur_face_pos)

            # Append face types as virtual cell types
            if faces_type.dim() == 2:
                cur_types = faces_type[face_ids]
            else:
                cur_types = faces_type_first[face_ids][:, None]
            cpd_cell_type_parts.append(cur_types)

            # Update neighbor mapping to point to newly created cpd indices for these faces
            n_added = e_face - s_face
            new_indices = torch.arange(next_cpd_index, next_cpd_index + n_added, dtype=torch.long)
            neighbor_cpd_cell[1, s_face:e_face] = new_indices

            # Record cpd index range back to patch_list for later field mapping
            self.patch_list[patch_name]['start_idx_cpd_cell'] = int(next_cpd_index)
            self.patch_list[patch_name]['end_idx_cpd_cell'] = int(next_cpd_index + n_added)

            if is_cyclic:
                total_added_cyclic += n_added
            else:
                total_added_noncyclic += n_added

            next_cpd_index += n_added

        # Concatenate parts
        cpd_cell_pos = torch.cat(cpd_cell_pos_parts, dim=0)
        cpd_cell_type = torch.cat(cpd_cell_type_parts, dim=0)

        # Build non-cyclic connectivity for message passing (replace cyclic neighbors with opposite internal cell)
        self.neighbor_cpd_cell_non_cyclic = neighbor_cpd_cell.clone()

        # Handle cyclic: compute ghost positions and non-cyclic connectivity using paired faces
        if (self.cyclic_face[0] > 0).all():
            patch1_faces, patch2_faces = self.cyclic_face
            # Owner/internal cell indices and the cpd indices we just wrote to row 1
            patch1_cells, patch1_cpd_cells = neighbor_cpd_cell[:, patch1_faces]
            patch2_cells, patch2_cpd_cells = neighbor_cpd_cell[:, patch2_faces]

            # For message passing, connect cyclic faces to opposite internal cells
            self.neighbor_cpd_cell_non_cyclic[1, patch1_faces] = patch2_cells
            self.neighbor_cpd_cell_non_cyclic[1, patch2_faces] = patch1_cells

            # Compute OpenFOAM-style ghost centroids for cyclic patches
            patch1_face_pos = face_pos[patch1_faces]
            patch2_face_pos = face_pos[patch2_faces]
            patch1_cell_centroids = cpd_cell_pos[patch1_cells]
            patch2_cell_centroids = cpd_cell_pos[patch2_cells]

            cpd_cell_pos[patch1_cpd_cells] = patch1_face_pos - (patch2_face_pos - patch2_cell_centroids)
            cpd_cell_pos[patch2_cpd_cells] = patch2_face_pos - (patch1_face_pos - patch1_cell_centroids)
        else:
            logging.info("  No cyclic boundaries found")

        # Assign outputs
        self.cpd_cell_pos = cpd_cell_pos
        self.cpd_cell_type = cpd_cell_type
        self.neighbor_cpd_cell = neighbor_cpd_cell

        logging.info("  Original cells: %d", cell_pos.shape[0])
        logging.info("  Boundary faces as cells (non-cyclic): %d; cyclic: %d", total_added_noncyclic, total_added_cyclic)
        logging.info("  Total compound cells: %d", cpd_cell_pos.shape[0])

        # Build extended neighbor edges (k-hop)
        self.calc_cpd_neighbor_cell_x()

    def export_to_hdf5(self, output_path: str, case_name: str):
        """Export mesh data to HDF5 format"""
        metadata = {
            'case_path': self.case_path,
            'num_points': self.num_nodes,
            'num_faces': self.num_faces,
            'num_cells': self.num_cells,
            'num_internal_faces': self.num_internal_faces
        }

        self.save_to_hdf5(output_path, case_name, **metadata)

    def _read_field_data(self, case_path: str, patch_list: Dict):
        """
        Read field data from VTK format (.vtm.series) using PyVista.

        Args:
            case_path: Path to OpenFOAM case directory (will look for VTK/ or vtk_output/ subdirectory)
            patch_list: Dictionary containing patch information with boundary names and indices

        Returns:
            dict: Field data dictionary with structure:
                {
                    "uvw_cpd_cell": torch.Tensor,  # [n_step, n_cpd_cell, 3]
                    "press_cpd_cell": torch.Tensor,  # [n_step, n_cpd_cell, 1]
                    "temp_cpd_cell": torch.Tensor,  # [n_step, n_cpd_cell, 1]
                    "rho_cpd_cell": torch.Tensor,  # [n_step, n_cpd_cell, 1]
                    "p_rgh_cpd_cell": torch.Tensor,  # [n_step, n_cpd_cell, 1]
                    "time_list": List[float]  # [n_step]
                }
        """
        import json
        import glob

        logging.info("Reading field data from VTK format using PyVista...")

        # Find VTK directory (standard is VTK/, but also check legacy vtk_output/)
        vtk_dir = None
        possible_vtk_dirs = [
            os.path.join(case_path, "VTK"),  # Standard foamToVTK output
            os.path.join(case_path, "vtk_output"),  # Legacy support
        ]

        # Also search for any directory containing .vtm.series files
        for item in os.listdir(case_path):
            item_path = os.path.join(case_path, item)
            if os.path.isdir(item_path):
                if _is_ignored_vtk_dir_name(item):
                    continue
                series_files = glob.glob(os.path.join(item_path, "*.vtm.series"))
                if series_files:
                    possible_vtk_dirs.insert(0, item_path)

        for vdir in possible_vtk_dirs:
            if os.path.exists(vdir):
                vtk_dir = vdir
                break

        if vtk_dir is None:
            logging.warning(f"No VTK directory found in {case_path}")
            logging.info("Attempting to preprocess field data (initial field + VTK conversion)...")

            # Use the new FieldDataPreprocessor to handle both initial field generation and VTK conversion
            import sys
            from pathlib import Path

            # Get the path to parse_fielddata.py (same directory as this file)
            script_dir = Path(__file__).parent
            sys.path.insert(0, str(script_dir))

            try:
                from parse_fielddata import FieldDataPreprocessor

                # Run preprocessing pipeline
                preprocessor = FieldDataPreprocessor(case_path)
                success = preprocessor.process(run_simulation=True)

                if not success:
                    logging.error("Failed to preprocess field data")
                    raise RuntimeError("Field data preprocessing failed")

            except Exception as e:
                logging.error(f"Error during field data preprocessing: {e}")
                raise

            # After successful preprocessing, try to find VTK directory again
            for vdir in possible_vtk_dirs:
                if os.path.exists(vdir):
                    vtk_dir = vdir
                    break

            if vtk_dir is None:
                # VTK not found even after preprocessing — skip_vtk=True is likely in effect.
                # Fall back to the native pyvista OpenFOAM reader (no foamToVTK needed).
                logging.info(
                    "VTK not available after preprocessing; falling back to native OpenFOAM reader."
                )
                self._read_field_data_native(case_path, patch_list)
                return

            logging.info(f"✅ Successfully preprocessed field data")

        logging.info(f"Found VTK directory: {vtk_dir}")

        # Find .vtm.series file
        series_files = glob.glob(os.path.join(vtk_dir, "*.vtm.series"))
        if not series_files:
            logging.error(f"No .vtm.series file found in {vtk_dir}")
            raise FileNotFoundError(f"No .vtm.series file found in {vtk_dir}")

        series_file = series_files[0]
        logging.info(f"Reading time series from: {series_file}")

        # Parse .vtm.series file to get time steps and corresponding .vtm files
        with open(series_file, 'r', encoding='utf-8') as f:
            series_data = json.load(f)

        # Extract time values and file names
        time_steps = []
        vtm_files = []

        # The JSON structure is {"file-series-version": "1.0", "files": [...]}
        files_list = series_data.get("files", [])

        for entry in files_list:
            time_val = float(entry.get("time", 0.0))  # Convert to float for consistency
            vtm_name = entry.get("name", "")

            if time_val != 0.0:
                time_steps.append(time_val)  # Only consider non-zero time steps
                vtm_files.append(os.path.join(vtk_dir, vtm_name))
            else:
                logging.info(f"Skipping time step 0.0 from file: {vtm_name}")
            
        if not time_steps:
            logging.warning("No time steps found in .vtm.series file, returning empty field data")
            return

        # CRITICAL: Sort time steps chronologically to ensure:
        # - Index 0 = first non-zero time step (input field, e.g., time=1)
        # - Index -1 = final time step (target field, e.g., time=1000)
        # This is required for correct input/target mapping in HomoGraph_dataset.py
        sorted_indices = sorted(range(len(time_steps)), key=lambda i: time_steps[i])
        time_steps = [time_steps[i] for i in sorted_indices]
        vtm_files = [vtm_files[i] for i in sorted_indices]

        logging.info(f"Found {len(time_steps)} time steps (sorted): {time_steps}")

        # Initialize field data storage
        field_data = {
            "uvw_cpd_cell": [],
            "press_cpd_cell": [],
            "temp_cpd_cell": [],
            "rho_cpd_cell": [],
            "p_rgh_cpd_cell": [],
            "time_list": []
        }

        # Process each time step
        udf_computed = False
        for time_val, vtm_file in zip(time_steps, vtm_files):
            logging.info(f"Processing time step: {time_val} from {os.path.basename(vtm_file)}")

            # Read VTM file (MultiBlock dataset)
            mesh_data = pv.read(vtm_file)

            # Compute UDF on the first time step
            if not udf_computed:
                self._compute_udf_field(mesh_data, patch_list)
                udf_computed = True

            # Extract field data for this time step
            time_step_data = self._extract_time_step_data_from_vtm(mesh_data, patch_list)

            # Append to field data
            for field_name, values in time_step_data.items():
                if field_name != "time":
                    field_data[field_name].append(values)

            field_data["time_list"].append(time_val)

        # Convert lists to tensors
        self.field_data = self._convert_field_data_to_tensors(field_data)
        logging.info("Field data reading completed successfully")

    def _read_field_data_native(self, case_path: str, patch_list: Dict):
        """
        Read field data using the native pyvista OpenFOAM reader.

        Called as fallback when VTK directory is unavailable (skip_vtk=True mode).
        Requires that boundary face values are already written in the field files
        (produced by buoyantBoussinesqSimpleFoam_new).

        Args:
            case_path: Path to OpenFOAM case directory.
            patch_list: Patch info dict (same format used elsewhere in this class).
        """
        from pathlib import Path as _Path

        foam_files = list(_Path(case_path).glob("*.foam"))
        if not foam_files:
            raise FileNotFoundError(f"No .foam file found in {case_path}")

        reader = pv.OpenFOAMReader(str(foam_files[0]))
        # Only non-zero time steps (t=0 is the initial condition, not a solver result)
        available_times = sorted(t for t in reader.time_values if t != 0.0)
        if not available_times:
            raise RuntimeError(f"No non-zero time steps found in {case_path} via native reader")

        logging.info(
            f"Native reader: reading {len(available_times)} time steps: {available_times}"
        )

        field_mapping = {
            'U': 'uvw_cpd_cell',
            'p': 'press_cpd_cell',
            'T': 'temp_cpd_cell',
            'rho': 'rho_cpd_cell',
            'p_rgh': 'p_rgh_cpd_cell',
        }
        n_cpd_cells = self.cpd_cell_pos.shape[0]

        field_data: Dict = {k: [] for k in list(field_mapping.values()) + ['time_list']}

        udf_computed = False
        for time_val in available_times:
            reader.set_active_time_value(time_val)
            mesh_data = reader.read()

            time_step_data = {
                'uvw_cpd_cell': np.zeros((n_cpd_cells, 3), dtype=np.float32),
                'press_cpd_cell': np.zeros((n_cpd_cells, 1), dtype=np.float32),
                'temp_cpd_cell': np.zeros((n_cpd_cells, 1), dtype=np.float32),
                'rho_cpd_cell': np.zeros((n_cpd_cells, 1), dtype=np.float32),
                'p_rgh_cpd_cell': np.zeros((n_cpd_cells, 1), dtype=np.float32),
            }

            internal_block = mesh_data.get("internalMesh")
            boundary_block = mesh_data.get("boundary")

            # --- internal cells ---
            if internal_block is not None:
                for foam_name, data_key in field_mapping.items():
                    if foam_name in internal_block.array_names:
                        arr = np.asarray(internal_block[foam_name])
                        if foam_name == 'U':
                            if arr.ndim == 2 and arr.shape[1] == 3:
                                time_step_data[data_key][:self.num_cells] = arr.astype(np.float32)
                        else:
                            if arr.ndim == 1:
                                time_step_data[data_key][:self.num_cells, 0] = arr.astype(np.float32)

            # --- boundary faces ---
            if boundary_block is not None and patch_list:
                for i in range(boundary_block.n_blocks):
                    block = boundary_block[i]
                    block_name = boundary_block.get_block_name(i)
                    if block_name not in patch_list:
                        continue
                    patch_info = patch_list[block_name]
                    start_idx = patch_info.get('start_idx_cpd_cell')
                    end_idx = patch_info.get('end_idx_cpd_cell')
                    if start_idx is None or end_idx is None:
                        continue
                    for foam_name, data_key in field_mapping.items():
                        if foam_name in block.array_names:
                            arr = np.asarray(block[foam_name])
                            if foam_name == 'U':
                                if arr.ndim == 2 and arr.shape[1] == 3:
                                    time_step_data[data_key][start_idx:end_idx] = arr.astype(np.float32)
                            else:
                                if arr.ndim == 1:
                                    time_step_data[data_key][start_idx:end_idx, 0] = arr.astype(np.float32)

            # Compute UDF once (first time step)
            if not udf_computed:
                try:
                    self._compute_udf_field(mesh_data, patch_list)
                    udf_computed = True
                except Exception as e:
                    logging.warning(f"Native reader: UDF computation failed: {e}")

            for k, v in time_step_data.items():
                field_data[k].append(torch.from_numpy(v))
            field_data['time_list'].append(time_val)

        self.field_data = self._convert_field_data_to_tensors(field_data)
        logging.info("Native reader: field data reading completed successfully")

    def _extract_time_step_data_from_vtm(self, mesh_data, patch_list):
        """
        Extract field data from VTK MultiBlock dataset (.vtm) for a single time step.

        Args:
            mesh_data: PyVista MultiBlock dataset from .vtm file
            patch_list: Dictionary containing patch information

        Returns:
            dict: Field data for this time step
        """
        # Initialize data arrays for compound cells
        n_cpd_cells = self.cpd_cell_pos.shape[0]

        time_step_data = {
            "uvw_cpd_cell": torch.zeros((n_cpd_cells, 3)),
            "press_cpd_cell": torch.zeros((n_cpd_cells, 1)),
            "temp_cpd_cell": torch.zeros((n_cpd_cells, 1)),
            "rho_cpd_cell": torch.zeros((n_cpd_cells, 1)),
            "p_rgh_cpd_cell": torch.zeros((n_cpd_cells, 1))
        }

        # VTK MultiBlock structure from foamToVTK:
        # - Block 0: "internal" (internal mesh)
        # - Block 1: "boundary" (MultiBlock containing all patches)

        internal_block = None
        boundary_block = None

        # Find internal and boundary blocks
        for i in range(mesh_data.n_blocks):
            block_name = mesh_data.get_block_name(i)
            if block_name == "internal":
                internal_block = mesh_data[i]
            elif block_name == "boundary":
                boundary_block = mesh_data[i]

        if internal_block is None:
            logging.warning("No 'internal' block found in VTM file")

        if boundary_block is None:
            logging.warning("No 'boundary' block found in VTM file")

        # Extract internal field data
        if internal_block is not None:
            self._extract_internal_field_data_from_vtk(internal_block, time_step_data)

        # Extract boundary field data using patch_list
        if boundary_block is not None:
            self._extract_boundary_field_data_from_vtk(boundary_block, patch_list, time_step_data)

        return time_step_data

    def _extract_internal_field_data_from_vtk(self, internal_block, time_step_data):
        """
        Extract field data from VTK internal mesh block.

        Args:
            internal_block: PyVista UnstructuredGrid from internal.vtu
            time_step_data: Dictionary to store extracted field data
        """
        if internal_block is None:
            return

        # Map field names to our data structure
        field_mapping = {
            'U': 'uvw_cpd_cell',
            'p': 'press_cpd_cell',
            'T': 'temp_cpd_cell',
            'rho': 'rho_cpd_cell',
            'p_rgh': 'p_rgh_cpd_cell'
        }

        # Extract each field
        for foam_name, data_key in field_mapping.items():
            if foam_name in internal_block.array_names:
                field_data = internal_block[foam_name]
                # Convert to tensor and handle dimensions
                if foam_name == 'U':  # Vector field
                    if field_data.ndim == 2 and field_data.shape[1] == 3:
                        time_step_data[data_key][:self.num_cells] = torch.from_numpy(field_data).float()
                    else:
                        logging.warning(f"Unexpected shape for velocity field: {field_data.shape}")
                else:  # Scalar field
                    if field_data.ndim == 1:
                        time_step_data[data_key][:self.num_cells, 0] = torch.from_numpy(field_data).float()
                    else:
                        logging.warning(f"Unexpected shape for {foam_name} field: {field_data.shape}")

        # Validate VTK internal cell ordering against self.cpd_cell_pos
        # try:
        #     centers_pd = internal_block.cell_centers()  # PolyData of cell centers
        #     pv_centroids_np = centers_pd.points         # (num_cells, 3) numpy
        #     pv_centroids = torch.from_numpy(np.asarray(pv_centroids_np)).to(self.cpd_cell_pos.dtype)

        #     if pv_centroids.shape[0] != self.num_cells:
        #         logging.error(
        #             "VTK internal cell count (%d) != self.num_cells (%d)",
        #             pv_centroids.shape[0], self.num_cells
        #         )
        #     else:
        #         ref_centroids = self.cpd_cell_pos[:self.num_cells].to(pv_centroids.dtype)
        #         diff = pv_centroids - ref_centroids
        #         max_err = torch.max(torch.abs(diff)).item()
        #         tol = 1e-6
        #         if max_err <= tol:
        #             logging.debug(f"Internal cell ordering validated (max_err={max_err:.3e})")
        #         else:
        #             logging.error("VTK internal cell order may differ (max_err=%.3e > %.1e)", max_err, tol)
        # except Exception as e:
        #     logging.error(f"Failed to validate internal cell ordering via centroids: {e}")

    def _extract_boundary_field_data_from_vtk(self, boundary_block, patch_list, time_step_data):
        """
        Extract field data from VTK boundary MultiBlock using patch_list information.

        Args:
            boundary_block: PyVista MultiBlock containing all boundary patches
            patch_list: Dictionary containing patch information
            time_step_data: Dictionary to store extracted field data
        """
        if boundary_block is None or not patch_list:
            return

        # Map field names to our data structure
        field_mapping = {
            'U': 'uvw_cpd_cell',
            'p': 'press_cpd_cell',
            'T': 'temp_cpd_cell',
            'rho': 'rho_cpd_cell',
            'p_rgh': 'p_rgh_cpd_cell'
        }

        # Iterate through all patches in the boundary MultiBlock
        for i in range(boundary_block.n_blocks):
            block = boundary_block[i]
            block_name = boundary_block.get_block_name(i)

            # Check if this patch is in our patch_list
            if block_name not in patch_list:
                logging.warning(f"Patch '{block_name}' found in VTK but not in patch_list, skipping")
                continue

            patch_info = patch_list[block_name]

            # Get patch indices for compound cells
            start_cpd_idx = patch_info.get('start_idx_cpd_cell', None)
            end_cpd_idx = patch_info.get('end_idx_cpd_cell', None)

            if start_cpd_idx is None or end_cpd_idx is None:
                logging.warning(f"Patch '{block_name}' missing cpd_cell indices, skipping")
                continue

            # Extract field data for this patch
            for foam_name, data_key in field_mapping.items():
                if foam_name in block.array_names:
                    if foam_name == 'U':  # Vector field
                        if block[foam_name].ndim == 2 and block[foam_name].shape[1] == 3:
                            time_step_data[data_key][start_cpd_idx:end_cpd_idx] = torch.from_numpy(block[foam_name]).float()
                    else:  # Scalar field
                        if block[foam_name].ndim == 1:
                            time_step_data[data_key][start_cpd_idx:end_cpd_idx, 0] = torch.from_numpy(block[foam_name]).float()

            # Validate patch ordering (VTK vs our internal ordering)
            # try:
            #     centers_pd = block.cell_centers()  # Get face centers for this patch
            #     pv_centers_np = centers_pd.points
            #     pv_centers = torch.from_numpy(np.asarray(pv_centers_np)).to(self.face_pos.dtype)
            #     n_pv = pv_centers.shape[0]

            #     # Get face index range (support both naming conventions)
            #     start_face_idx = patch_info.get('start_idx_face', patch_info.get('start_face_idx', None))
            #     end_face_idx = patch_info.get('end_idx_face', patch_info.get('end_face_idx', None))

            #     # Check if this is a cyclic patch
            #     patch_type_str = str(patch_info.get('geom_type', patch_info.get('patchType', ''))).lower()
            #     is_cyclic = bool(('cyclic' in patch_type_str))

            #     tol = 1e-5

            #     # Validate ordering based on patch type:
            #     # - cyclic: validate against face_pos
            #     # - non-cyclic: validate against cpd_centroid
            #     if is_cyclic:
            #         if start_face_idx is not None and end_face_idx is not None:
            #             if (end_face_idx - start_face_idx) == n_pv:
            #                 ref_faces = self.face_pos[start_face_idx:end_face_idx].to(pv_centers.dtype)
            #                 face_diff = pv_centers - ref_faces
            #                 face_max_err = torch.max(torch.abs(face_diff)).item()
            #                 if face_max_err > tol:
            #                     logging.error(
            #                         "Patch '%s' face_pos ordering may differ (max_err=%.3e > %.1e)",
            #                         block_name, face_max_err, tol
            #                     )
            #                 else:
            #                     logging.debug(f"Patch '{block_name}' face_pos validated (max_err={face_max_err:.3e})")
            #             else:
            #                 logging.error(
            #                     "Patch '%s' faces count mismatch: VTK=%d, face_pos=%d",
            #                     block_name, n_pv, (end_face_idx - start_face_idx)
            #                 )
            #         else:
            #             logging.warning("Patch '%s' missing face index range; skip face_pos validation", block_name)
            #     else:
            #         if (end_cpd_idx - start_cpd_idx) == n_pv:
            #             ref_cpd = self.cpd_cell_pos[start_cpd_idx:end_cpd_idx].to(pv_centers.dtype)
            #             cpd_diff = pv_centers - ref_cpd
            #             cpd_max_err = torch.max(torch.abs(cpd_diff)).item()
            #             if cpd_max_err > tol:
            #                 logging.error(
            #                     "Patch '%s' cpd ordering may differ (max_err=%.3e > %.1e)",
            #                     block_name, cpd_max_err, tol
            #                 )
            #             else:
            #                 logging.debug(f"Patch '{block_name}' cpd_centroid validated (max_err={cpd_max_err:.3e})")
            #         else:
            #             logging.error(
            #                 "Patch '%s' cpd count mismatch: VTK=%d, cpd_span=%d",
            #                 block_name, n_pv, (end_cpd_idx - start_cpd_idx)
            #             )

            # except Exception as e:
            #     logging.error("Failed to validate patch '%s' ordering via centers: %s", block_name, e)

    def _compute_udf_field(self, mesh_data, patch_list):
        """
        Compute unsigned distance field (UDF) from cpd_cell positions to surface mesh.

        Args:
            mesh_data: PyVista MultiBlock dataset from .vtm file
            patch_list: Dictionary containing patch information
        """
        try:
            from .utils.calc_udf import extract_surface_mesh_from_boundary, compute_udf_field
        except ImportError:
            # Fallback for when script is run directly (not as a module)
            from utils.calc_udf import extract_surface_mesh_from_boundary, compute_udf_field

        logging.info("Computing UDF field...")

        # Find boundary block
        boundary_block = None
        for i in range(mesh_data.n_blocks):
            block_name = mesh_data.get_block_name(i)
            if block_name == "boundary":
                boundary_block = mesh_data[i]
                break

        if boundary_block is None:
            logging.warning("No 'boundary' block found in VTM file, skipping UDF computation")
            self.udf_cpd_cell = torch.zeros((self.cpd_cell_pos.shape[0], 1), dtype=self.cpd_cell_pos.dtype)
            return

        # Extract surface mesh (excluding "room" patches)
        surface_mesh = extract_surface_mesh_from_boundary(
            boundary_block=boundary_block,
            patch_list=patch_list,
            exclude_prefix="room"
        )

        if surface_mesh is None:
            logging.warning("Failed to extract surface mesh, UDF will be zeros")
            self.udf_cpd_cell = torch.zeros((self.cpd_cell_pos.shape[0], 1), dtype=self.cpd_cell_pos.dtype)
            return

        # Compute UDF for all cpd_cell positions
        self.udf_cpd_cell = compute_udf_field(
            query_points=self.cpd_cell_pos,
            surface_mesh=surface_mesh
        )

        logging.info(f"✅ UDF field computed: shape={self.udf_cpd_cell.shape}")

    def _convert_field_data_to_tensors(self, field_data):
        """Convert field data lists to tensors"""
        final_data = {}

        for field_name, data_list in field_data.items():
            if field_name == "time_list":
                final_data[field_name] = data_list
            else:
                if data_list:
                    # Stack tensors along time dimension
                    final_data[field_name] = torch.stack(data_list, dim=0)
                else:
                    # Create empty tensor with correct shape
                    if field_name == "uvw_cpd_cell":
                        final_data[field_name] = torch.zeros((0, self.cpd_cell_pos.shape[0], 3))
                    else:
                        final_data[field_name] = torch.zeros((0, self.cpd_cell_pos.shape[0], 1))

        return final_data

    def export_vtm_files(self, output_dir: str):
        """Export VTU files for visualization"""
        # Create output directory
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

            logging.info("neighbor_cpd_cell_x degree statistics:")
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

        # Add UDF field if available (only for original cells, not boundary virtual cells)
        if hasattr(self, 'udf_cpd_cell') and self.udf_cpd_cell is not None:
            udf_data = self.udf_cpd_cell.cpu().numpy().squeeze()
            # Only take UDF values for original cells (first n_cells entries)
            n_cells = len(cell_types)
            if len(udf_data) >= n_cells:
                grid.cell_data['UDF'] = udf_data[:n_cells]
                logging.info(f"Added UDF field to visualization grid ({n_cells} cells)")
            else:
                logging.warning(f"UDF data length ({len(udf_data)}) < n_cells ({n_cells}), skipping UDF visualization")

        return grid

    def _create_boundary_grid(self, face_indices: torch.Tensor, patch_name: str):
        """Create PyVista UnstructuredGrid for a specific boundary patch"""
        import pyvista as pv

        try:
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
            faces_type_data = torch.full((len(face_indices), 1), face_type_value, dtype=torch.long).cpu().numpy()

            # Extract face normals for current face type
            cur_face_normal = self.face_normal[face_indices].cpu().numpy()

            # Convert to PyVista format
            mesh_pos = cur_nodes_pos
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
                face_nodes = face_node[face_start_idx:face_start_idx + num_nodes]

                # Create PyVista cell format: [num_nodes, node1, node2, ...]
                pv_cell = [num_nodes] + face_nodes.tolist()
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
            if mesh_pos.shape[1] == 2:
                mesh_pos = np.c_[mesh_pos, np.zeros(mesh_pos.shape[0])]

            # Create PyVista UnstructuredGrid
            pv_cells = np.array(pv_cells)
            pv_cell_types = np.array(pv_cell_types)

            grid = pv.UnstructuredGrid(pv_cells, pv_cell_types, mesh_pos)

            # Add data
            grid.cell_data["face_type"] = faces_type_data.flatten()
            grid.cell_data["face_normal"] = cur_face_normal

            return grid

        except Exception as e:
            logging.error("❌ Failed to create boundary grid for %s: %s", patch_name, e)
            return None

def validate_openfoam_case(case_path: str) -> bool:
    """
    Validate OpenFOAM case structure before processing.

    Args:
        case_path: Path to OpenFOAM case directory

    Raises:
        FileNotFoundError: If essential files or directories are missing
    """
    if not os.path.isdir(case_path):
        raise FileNotFoundError(f"Case directory not found: {case_path}")

    # Check for 0/ directory
    zero_dir = os.path.join(case_path, '0')
    if not os.path.isdir(zero_dir):
        raise FileNotFoundError(f"0/ directory not found: {zero_dir}")

    # Check for constant/polyMesh directory
    polymesh_dir = os.path.join(case_path, 'constant', 'polyMesh')
    if not os.path.isdir(polymesh_dir):
        raise FileNotFoundError(f"polyMesh directory not found: {polymesh_dir}")

    # Check for essential mesh files
    essential_files = ['boundary', 'points', 'faces', 'owner', 'neighbour']
    missing_files = []

    for filename in essential_files:
        filepath = os.path.join(polymesh_dir, filename)
        if not os.path.isfile(filepath):
            missing_files.append(filename)

    if missing_files:
        raise FileNotFoundError(f"Essential mesh files missing in {polymesh_dir}: {missing_files}")

    return True


def convert_openfoam_to_h5_optimized(
        case_path: str, 
        case_name: str = None, 
        output_path: str = None,
        enable_visualization: bool = True,
        valid_bc_file_list: List[str] = None
    ):
    """
    Convert OpenFOAM mesh to HDF5 format using the optimized refactored parser.

    Args:
        case_path: Path to OpenFOAM case directory
        case_name: Name of the case (auto-detected if None)
        output_path: Output path for HDF5 file (auto-generated if None)
        enable_visualization: Whether to create visualization files
    """
    # Validate case structure first
    validate_openfoam_case(case_path)

    # Auto-detect case name if not provided
    if case_name is None:
        case_name = os.path.basename(case_path)

    # Auto-generate output path following original convention: case_path/h5/case_name.h5
    if output_path is None:
        output_dir = os.path.join(case_path, 'h5')
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{case_name}.h5")
    try:
        # Initialize optimized manager
        manager = OpenFOAMManager(
            case_path,
            plt_edge_vis=enable_visualization,
            valid_bc_file_list=valid_bc_file_list,
        )

        # Process mesh data
        logging.info("Processing OpenFOAM mesh data with optimized pipeline...")
        manager._process_mesh_pipeline()

        # Export to HDF5
        logging.info("Exporting to HDF5...")
        manager.export_to_hdf5(output_path, case_name)

        # Export VTU files following original convention: case_path/h5/vtu_boundary_faces/
        if enable_visualization:
            vtu_output_dir = os.path.join(os.path.dirname(output_path), "vtu_boundary_faces")
            logging.info("Exporting VTU files...")
            manager.export_vtm_files(vtu_output_dir)

        logging.info("OpenFOAM conversion completed successfully!")
        return manager

    except Exception as e:
        logging.error("Error in OpenFOAM conversion: %s", str(e), exc_info=True)
        raise


def _is_valid_openfoam_case(case_dir: str) -> bool:
    """Return True if case_dir looks like a valid OpenFOAM case (non-raising check)."""
    try:
        validate_openfoam_case(case_dir)
        return True
    except Exception:
        return False


def find_valid_openfoam_cases(root_dir: str) -> List[str]:
    """Recursively scan root_dir to find valid OpenFOAM case directories.

    A directory is considered a case if it passes validate_openfoam_case().
    When a directory qualifies as a case, we do not descend further into it.
    """
    cases: List[str] = []
    for cur_root, dirnames, _ in os.walk(root_dir):
        if _is_valid_openfoam_case(cur_root):
            cases.append(cur_root)
            # Do not recurse inside a detected case to avoid nested duplicates
            dirnames[:] = []
    return cases


def _convert_worker(args: Tuple[str, bool, Optional[List[str]]]) -> Tuple[str, bool, Optional[str]]:
    """Worker wrapper for multiprocessing.

    Args:
        args: (case_path, enable_visualization, valid_bc_file_list)
    Returns:
        (case_path, success, error_message)
    """
    case_path, enable_visualization, valid_bc_file_list = args
    try:
        convert_openfoam_to_h5_optimized(
            case_path=case_path,
            enable_visualization=enable_visualization,
            valid_bc_file_list=valid_bc_file_list,
        )
        return (case_path, True, None)
    except Exception as e:
        return (case_path, False, str(e))


def _init_worker_logger(level: int = logging.WARNING):
    """Initializer for worker processes to reduce log verbosity in workers."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)


def batch_convert_openfoam_cases(
    root_dir: str,
    enable_visualization: bool = False,
    valid_bc_file_list: Optional[List[str]] = None,
    workers: Optional[int] = None,
) -> List[Tuple[str, bool, Optional[str]]]:
    """Traverse root_dir to find OpenFOAM cases and convert them to H5 in parallel.

    - Detects cases by validate_openfoam_case()
    - Uses up to half of available CPU cores by default
    - Each case calls convert_openfoam_to_h5_optimized()

    Returns a list of (case_path, success, error_message) for summary/inspection.
    """
    cases = find_valid_openfoam_cases(root_dir)
    if not cases:
        logging.warning("No valid OpenFOAM cases found under: %s", root_dir)
        return []

    import multiprocessing as mp

    max_workers = workers if workers is not None else max(1, os.cpu_count() // 8)
    max_workers = min(max_workers, len(cases))

    # max_workers = 1
    logging.info("Discovered %d case(s). Launching up to %d workers...", len(cases), max_workers)

    tasks = [(c, enable_visualization, valid_bc_file_list) for c in cases]
    results: List[Tuple[str, bool, Optional[str]]] = []

    with mp.Pool(processes=max_workers, initializer=_init_worker_logger, initargs=(logging.WARNING,)) as pool:
        iterator = pool.imap_unordered(_convert_worker, tasks)
        iterator = tqdm(iterator, total=len(tasks), desc="Converting cases", unit="case", dynamic_ncols=True, leave=True)
        with logging_redirect_tqdm():
            for case_path, ok, err in iterator:
                if ok:
                    logging.info("Converted: %s", case_path)
                else:
                    logging.error("Failed: %s | %s", case_path, err)
                results.append((case_path, ok, err))

    n_ok = sum(1 for _, ok, _ in results if ok)
    n_fail = len(results) - n_ok
    logging.info("Batch conversion finished. Success: %d, Fail: %d", n_ok, n_fail)
    return results


if __name__ == "__main__":
    import argparse

    torch.set_float32_matmul_precision('highest')

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Convert OpenFOAM mesh to H5 format")
    parser.add_argument(
        '--case', 
        type=str, 
        default='cases/src/Parse_mesh/parse_openfoam_refactored.pyHanggang/datasets/hanggang/hanggang_1000_cases',
        help='Path to OpenFOAM case directory'
    )
    parser.add_argument(
        '--valid-bc-files', 
        type=str, 
        nargs='+',
        default=["rho", "U", "T", "p", "p_rgh"],
        help='List of valid boundary condition files to parse'
    )
    parser.add_argument('--enable_visualization', action='store_true', help='Enable visualization',default=False)
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging', default=True)
    args = parser.parse_args()

    case_path = args.case
    valid_bc_file_list = args.valid_bc_files

    logging.info(f"Converting OpenFOAM case: {case_path}")
    logging.info(f"Valid BC files: {valid_bc_file_list}")

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    try:
        # Batch convert all valid OpenFOAM cases found under the provided path
        results = batch_convert_openfoam_cases(
            root_dir=case_path,
            enable_visualization=args.enable_visualization,
            valid_bc_file_list=valid_bc_file_list,
            workers=8,
        )

        if not results:
            logging.error("No valid OpenFOAM cases found under: %s", case_path)
            exit(1)

        n_ok = sum(1 for _, ok, _ in results if ok)
        n_fail = len(results) - n_ok

        if n_fail > 0:
            for cp, ok, err in results:
                if not ok:
                    logging.error("Failed: %s | %s", cp, err)
            logging.error("Batch conversion completed with failures. Success: %d, Fail: %d", n_ok, n_fail)
            exit(1)
        else:
            logging.info("✅ Batch conversion completed successfully. Cases: %d", n_ok)
            exit(0)

    except Exception as e:
        logging.error("❌ Batch conversion failed: %s", e, exc_info=True)
        exit(1)

# ==============================================================================
# HDF5 Data Shape Documentation
# ==============================================================================
#
# This section documents the shape of each dataset saved to the HDF5 file.
# Dataset names use the new HDF5 naming convention implemented in save_to_hdf5().
#
# Naming Conventions:
# - N_points: Total number of unique nodes (vertices) in the mesh.
# - N_faces: Total number of unique faces in the mesh.
# - N_cells: Total number of unique cells (polyhedra) in the mesh.
# - N_edges: Total number of unique edges in the mesh.
# - N_total_cell_nodes: Sum of the number of nodes for each cell.
# - N_total_cell_faces: Sum of the number of faces for each cell.
# - N_total_face_nodes: Sum of the number of nodes for each face.
# - N_total_face_edges: Sum of the number of edges for each face.
#
# ------------------------------------------------------------------------------
# HDF5 Dataset Name      | Shape                     | Description
# ------------------------------------------------------------------------------
#
# === Geometry Data ===
# node|pos               | (N_points, 3)             | XYZ coordinates for each node.
# face|face_pos   | (N_faces, 3)              | XYZ coordinates for the center of each face.
# cell|centroid          | (N_cells, 3)              | XYZ coordinates for the center of each cell.
#
# === Connectivity Data (Pointer-based) ===
# cells_node             | (N_total_cell_nodes, 1)   | Node indices belonging to cells.
# cells_node_ptr         | (N_total_cell_nodes, 1)   | Cell index for each entry in cells_node.
#
# cells_face             | (N_total_cell_faces, 1)   | Face indices belonging to cells.
# cells_face_ptr         | (N_total_cell_faces, 1)   | Cell index for each entry in cells_face.
#
# face_node              | (N_total_face_nodes, 1)   | Node indices belonging to faces.
# face_node_ptr          | (N_total_face_nodes, 1)   | Face index for each entry in face_node.
#
# face_edge              | (N_total_face_edges, 1)   | Edge indices belonging to faces.
# face_edge_ptr          | (N_total_face_edges, 1)   | Face index for each entry in face_edge.
#
# === Connectivity Data (Direct Mapping) ===
# edge_node              | (N_edges, 2)              | Node indices for each unique-dir edge.
# face|neighbor_cell     | (N_faces, 2)              | Cell indices for the two cells neighboring each face.
#                                                     | For boundary faces, both indices are the same.
#
# === Attribute Data ===
# face|face_type         | (N_faces, 1)              | Integer code for the type of each face (e.g., boundary, internal).
# face|face_area         | (N_faces, 1)              | Area of each face.
# cells_volume          | (N_cells, 1)              | Volume of each cell.
#
# === Cell-Face Pair Data ===
# cells_face_area       | (N_total_cell_faces, 1)   | Area of each face, repeated for each cell it belongs to.
# cells_face_normal     | (N_total_cell_faces, 3)   | Normal vector of each face, oriented outwards from its cell.
#
# === Compound Attributes (cpd|*) ===
# These attributes combine original cells with boundary faces treated as virtual cells
# N_compound_cells = N_cells + N_boundary_faces
#
# cpd|cell_type          | (N_compound_cells, 1)     | Type of each compound cell: NORMAL for original cells,
#                                                     | boundary condition type for boundary face centers.
# cpd|neighbor_cell      | (2, N_faces)              | Modified neighbor cell indices where boundary faces
#                                                     | point to their corresponding virtual boundary cells.
# cpd|centroid           | (N_compound_cells, 3)     | Coordinates combining original cell centers and
#                                                     | boundary face centers as virtual cells.
#
# ==============================================================================
