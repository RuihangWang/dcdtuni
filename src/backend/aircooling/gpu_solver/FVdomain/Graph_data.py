"""
HeteroData-based dataset for FV domain.
Defines the heterogeneous graph data structure and dataset.
"""

import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

import torch
from torch_geometric.data import Data, HeteroData

class HeteroGraph(HeteroData):
    """
    Custom heterogeneous graph data structure with precise batching control.

    Node types:
    - 'node': Physical mesh nodes/vertices
    - 'face': Mesh faces/edges
    - 'cell': Mesh cells
    - 'cpd_cell': Compound cells (for WLSQ)
    - 'graph': Graph-level attributes
    """

    _skip_to_keys = {''}

    def __inc__(self, key, value, *args, **kwargs):
        """
        Specifies how to increment attributes when concatenating graphs in a batch.
        This follows the exact same logic as the original GraphBase implementation.
        """
        # Helper function to safely get attributes with default values
        def safe_get_attr(attr_name, default=0):
            return getattr(self, attr_name, default)

        offset_rules = {
            "edge_index": self.num_nodes ,
            "face": self.num_nodes , # keep pyg default value
            
            "face_node": safe_get_attr('_num_nodes'),
            
            "cells_node": safe_get_attr('_num_nodes'),
            "cells_node_ptr": safe_get_attr('_num_cells'),

            "cyclic_face": safe_get_attr('_num_faces'),
            "face_node_ptr": safe_get_attr('_num_faces'),

            "cells_face": safe_get_attr('_num_faces'),
            "cells_face_ptr": safe_get_attr('_num_cells'),
            
            "owner": safe_get_attr('_num_cells'),
            "neighbor_cell": safe_get_attr('_num_cpd_cells'),
            "neighbor_cpd_cell": safe_get_attr('_num_cpd_cells'),
            "neighbor_cpd_cell_x": safe_get_attr('_num_cpd_cells'),
            "neighbor_cpd_cell_non_cyclic": safe_get_attr('_num_cpd_cells_cyclic'),
            
            # CSR format for GNN message passing
            # gnn_edge_indices: source node indices, offset by num_cpd_cells
            "gnn_edge_indices": safe_get_attr('_num_cpd_cells'),
            # gnn_edge_sort_idx: edge indices into twoway_edge_attr, offset by num_twoway_edges
            "gnn_edge_sort_idx": safe_get_attr('_num_twoway_edges'),
            # gnn_edge_counts: edge counts per node, NO offset needed (simple concatenation)
            # At runtime: indptr = F.pad(counts.cumsum(0), (1, 0))
            "gnn_edge_counts": 0,
            
            # region physics (mesh schema v2): rows of the per-graph material / source tables
            "material_id": safe_get_attr('_num_materials'),
            "source_id": safe_get_attr('_num_sources'),
            "theta_material": 0,
            "theta_source": 0,

            "pRefCell": safe_get_attr('_num_cpd_cells'),
            "pRefCell_valid_mask": 0,  # Boolean mask, no offset
            "pRefValue": 0,
            
            # No offset for these attributes
            "case_name": 0,
            "query": 0,
            "grids": 0,
            "pos": 0,
            "A_cell_to_cell": 0,
            "A_cell_to_cell_x": 0,
            "B_cell_to_cell": 0,
            "B_cell_to_cell_x": 0,
            "single_B_cell_to_cell": 0,
            "extra_B_cell_to_cell": 0,
            "cell_volume": 0,
            "node_type": 0,
            "graph_index": 0,
            "theta_PDE": 0,
            "x": 0,
            "y": 0,
            "m_ids": 0,
            "m_gs": 0,
            "global_idx": 0,
            "cell_type": 0,
            "cpd_cell_pos": 0,
            "face_area": 0,
            "face_type": 0,
            "cells_face_unv": 0,
            "fourier_features": 0,  # [N, fourier_dim] - Fourier positional encoding
            "_num_nodes": 0,
            "_num_faces": 0,
            "_num_cells": 0,
            "_num_cpd_cells": 0,
            "_num_twoway_edges": 0,
        }

        # Extract attribute name from HeteroData key format
        if isinstance(key, tuple) and len(key) >= 2:
            attr_name = key[-1]  # Last element is the attribute name
        else:
            attr_name = key

        return offset_rules.get(attr_name, super().__inc__(key, value, *args, **kwargs))

    def __cat_dim__(self, key, value, *args, **kwargs):
        """
        Specifies the dimension along which attributes should be concatenated when creating a batch.
        This follows the exact same logic as the original GraphBase implementation.
        """
        # Extract attribute name from the key
        if isinstance(key, tuple) and len(key) >= 2:
            if len(key) == 4:
                node_type1, edge_type, node_type2, attr_name = key
            elif len(key) == 2:
                node_type, attr_name = key
            else:
                attr_name = key[-1]
        else:
            attr_name = key

        # Define concatenation dimension rules (same as original)
        cat_dim_rules = {
            "x": 0,  # Node features: [N, F_node]
            "pos": 0,  # Node positions: [N, D_pos]
            "y": 0,  # Target values: [N_target, F_target] or [F_target]
            "edge_index": 1,  # Edge indices: [2, num_edges], concatenate along dim 1
            "face_node": 0,  # Face-node connectivity: [num_faces*nodes_per_face] - CORRECTED!
            "face_node_ptr": 0,
            "cyclic_face": 1,
            "face": 0,  # Cell-node connectivity
            "owner": 0, # [num_faces]
            "neighbor_cell": 1,  # [2, num_neighbor_cell_edges]
            "neighbor_cpd_cell_non_cyclic": 1, #[2, num_neighbor_cell_non_cyclic_edges]
            "neighbor_cpd_cell_x": 1,  # [2, num_neighbor_cell_x_edges]
            "neighbor_cpd_cell": 1,  # [2, num_cpd_neighbor_edges] - edge_index format
            # CSR format for GNN message passing
            "gnn_edge_counts": 0,  # [num_cpd_cells] - concatenate along dim 0, no offset
            "gnn_edge_indices": 0,  # [num_two_way_edges] - concatenate along dim 0
            "gnn_edge_sort_idx": 0,  # [num_two_way_edges] - concatenate along dim 0
            "graph_index": 0,  # [batch_size] or [1]
            "global_idx": 0,  # [N]
            "pRefCell": 0,
            "pRefCell_valid_mask": 0,  # Boolean mask tensor
            "pRefValue": 0,
            "cell_type": 0,
            "cpd_cell_pos": 0,
            "cells_index": 0,
            "cell_volume": 0,
            "face_area": 0,
            "face_type": 0,
            "cells_face_unv": 0,
            "fourier_features": 0,  # [N, fourier_dim]
            # region physics (mesh schema v2): per-cell ids, per-graph tables stacked
            "material_id": 0,
            "source_id": 0,
            "theta_material": 0,
            "theta_source": 0,
            "cells_node": 0,
            "cells_node_ptr": 0,
            "cells_face": 0,
            "cells_face_ptr": 0,
            "A_cell_to_cell": 0,
            "single_B_cell_to_cell": 0,
            "_num_nodes": 0,
            "_num_faces": 0,
            "_num_cells": 0,
            "_num_cpd_cells": 0,
            "_num_twoway_edges": 0,
        }

        return cat_dim_rules.get(attr_name, super().__cat_dim__(key, value, *args, **kwargs))

    def to(self, *args, exclude_keys=None, **kwargs):
        """Move tensors to target device/dtype, excluding specified keys."""
        if exclude_keys is None:
            exclude_keys = []
        elif isinstance(exclude_keys, str):
            exclude_keys = [exclude_keys]

        all_exclude_keys = set(exclude_keys) | self._skip_to_keys

        for store in self.stores:
            for key, item in store.items():
                if isinstance(item, torch.Tensor) and key not in all_exclude_keys:
                    store[key] = item.to(*args, **kwargs)

        return self

    def cuda(self, device=None, exclude_keys=None):
        """Move to CUDA device with optional key exclusion."""
        return self.to(device or 'cuda', exclude_keys=exclude_keys)

class HomoGraph(Data):
    """
    Custom homo graph data structure with precise batching control.
    """

    def __inc__(self, key, value, *args, **kwargs):
        offset_rules = {
            "edge_index": self.num_nodes,
            "face": self.num_nodes,
            "cells_node": self.num_nodes,
            "face_node": self.num_nodes,
            "cells_face": self.num_nodes,
            "neighbour_cell": self.num_nodes,
            "face_node_x": self.num_nodes,
            "pos": 0,
            "cell_area": 0,
            "node_type": 0,
            "graph_index": 0,
            "theta_pde": 0,
            "uvp_dim": 0,
            "dt_graph": 0,
            "x": 0,
            "y": 0,
            "m_ids": 0,
            "m_gs": 0,
            "case_global_index": 0,
        }
        return offset_rules.get(key, super().__inc__(key, value, *args, **kwargs))

    def __cat_dim__(self, key, value, *args, **kwargs):
        cat_dim_rules = {
            "x": 0,
            "pos": 0,
            "y": 0,
            "norm_y": 0,
            "query": 0,  # 保持query为列表，不进行拼接
            "edge_index": 1,  # edge_index保持默认的offset拼接
            "voxel": 0,
            "graph_index": 0,
        }
        return cat_dim_rules.get(key, super().__cat_dim__(key, value, *args, **kwargs))
