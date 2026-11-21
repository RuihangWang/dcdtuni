"""
HeteroGraph Dataset for FV domain.
Defines the heterogeneous graph data structure and dataset for physics-driven mode.
"""
import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

import torch
from torch_geometric.data import Dataset
import logging
logger = logging.getLogger(__name__)

# 从Graph_data导入
from FVdomain.Graph_data import HeteroGraphBase


# ============================================================================
# HeteroGraphDataset
# ============================================================================

class HeteroGraphDataset(Dataset):
    """
    Dataset that creates HeteroData objects from the base dataset.
    Used for physics-driven mode.
    """

    def __init__(self, base_dataset):
        super().__init__()
        self.base_dataset = base_dataset

    @property
    def pool(self):
        return self.base_dataset.meta_pool

    def len(self):
        return len(self.pool)

    def __getitem__(self, idx):
        """Support direct indexing: dataset[idx] or dataset[[1,2,3]]"""
        if isinstance(idx, (list, tuple)):
            # Batch indexing: return list of graphs
            return [self.get(i) for i in idx]
        else:
            # Single indexing: return single graph
            return self.get(idx)

    def get(self, idx):
        """
        Creates a heterogeneous graph from the base dataset.
        """
        minibatch_data = self.pool[idx]
        hetero_graph = HeteroGraphBase()

        # === NODE DATA ===
        mesh_pos = minibatch_data["node|pos"].to(torch.float32)
        hetero_graph['node'].pos = mesh_pos
        hetero_graph['node'].num_nodes = mesh_pos.shape[0]

        # === FACE DATA ===
        face_area = minibatch_data["face|face_area"].to(torch.float32)
        face_type = minibatch_data["face|face_type"].long().squeeze()
        face_pos = minibatch_data["face|face_pos"].to(torch.float32)

        hetero_graph['face'].pos = face_pos
        hetero_graph['face'].face_area = face_area
        hetero_graph['face'].face_type = face_type
        hetero_graph['face'].num_nodes = face_pos.shape[0]

        # === CELL DATA ===
        cells_volume = minibatch_data["cell|cells_volume"].to(torch.float32)
        hetero_graph['cell'].cells_volume = cells_volume
        hetero_graph['cell'].num_nodes = cells_volume.shape[0]

        # === CPD CELL DATA ===
        cpd_centroid = minibatch_data["cpd|centroid"].to(torch.float32)
        cell_type = minibatch_data["cpd|cell_type"].long()
        global_idx = minibatch_data["global_idx"].long()
        uvwp_cell = self.base_dataset.uvwp_cell_pool[global_idx]

        hetero_graph['cpd_cell'].pos = cpd_centroid
        hetero_graph['cpd_cell'].cell_type = cell_type
        hetero_graph['cpd_cell'].x = uvwp_cell
        hetero_graph['cpd_cell'].global_idx = global_idx
        hetero_graph['cpd_cell'].num_nodes = cpd_centroid.shape[0]

        # === GRAPH-LEVEL DATA ===
        case_name = minibatch_data["case_name"]
        theta_PDE = minibatch_data["theta_PDE"].to(torch.float32)
        pRefCell = minibatch_data["pRefCell"].long()
        pRefValue = minibatch_data["pRefValue"].to(torch.float32)

        hetero_graph['graph'].theta_PDE = theta_PDE
        hetero_graph['graph'].case_name = torch.tensor([ord(char) for char in case_name], dtype=torch.long)
        hetero_graph['graph'].graph_index = torch.tensor([idx], dtype=torch.long)
        hetero_graph['graph'].num_nodes = 1
        hetero_graph['graph'].pRefCell = pRefCell
        hetero_graph['graph'].pRefValue = pRefValue

        # === CONNECTIVITY DATA ===
        # Face-Node connectivity
        face_node = minibatch_data["face_node"].long()
        hetero_graph['face', 'contains', 'node'].face_node = face_node

        # Cell-Node connectivity
        cells_node = minibatch_data["cells_node"].long()
        cells_node_ptr = minibatch_data["cells_node_ptr"].long()
        hetero_graph['cell', 'contains', 'node'].cells_node = cells_node
        hetero_graph['cell', 'contains', 'node'].cells_node_ptr = cells_node_ptr

        # Cell-Face connectivity
        cells_face = minibatch_data["cells_face"].long()
        cells_face_ptr = minibatch_data["cells_face_ptr"].long()
        face_node_ptr = minibatch_data["face_node_ptr"].long()
        hetero_graph['cell', 'bounded_by', 'face'].cells_face = cells_face
        hetero_graph['cell', 'bounded_by', 'face'].cells_face_ptr = cells_face_ptr
        hetero_graph['face', 'bounds', 'cell'].face_node_ptr = face_node_ptr

        # Cell-Cell connectivity
        cpd_neighbor_cell = minibatch_data["cpd|neighbor_cell"].long()
        hetero_graph['cpd_cell', 'neighbors', 'cpd_cell'].cpd_neighbor_cell = cpd_neighbor_cell

        # Cyclic face connectivity
        cyclic_face = minibatch_data["cyclic_face"].long()
        hetero_graph['face'].cyclic_face = cyclic_face

        # WLSQ connectivity and matrices
        cpd_neighbor_cell_x = minibatch_data["cpd|neighbor_cell_x"].long()
        A_cell_to_cell = minibatch_data["A_cell_to_cell"].to(torch.float32)
        single_B_cell_to_cell = minibatch_data["single_B_cell_to_cell"].to(torch.float32)

        hetero_graph['cpd_cell', 'wlsq_neighbors', 'cpd_cell'].cpd_neighbor_cell_x = cpd_neighbor_cell_x
        hetero_graph['cpd_cell'].A_cell_to_cell = A_cell_to_cell
        hetero_graph['cpd_cell'].single_B_cell_to_cell = single_B_cell_to_cell

        # Additional geometric data
        cells_face_unv = minibatch_data['cells_face_normal'].to(torch.float32)
        hetero_graph['cell'].cells_face_unv = cells_face_unv

        # Set the count attributes for proper batching offset calculation
        hetero_graph._num_nodes = mesh_pos.shape[0]
        hetero_graph._num_faces = face_pos.shape[0]
        hetero_graph._num_cells = cells_volume.shape[0]
        hetero_graph._num_cpd_cells = cpd_centroid.shape[0]

        return hetero_graph
