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
            "cpd_neighbor_cell": safe_get_attr('_num_cpd_cells'),
            "cpd_neighbor_cell_x": safe_get_attr('_num_cpd_cells'),
            "cpd_neighbor_cell_non_cyclic": safe_get_attr('_num_cpd_cells_cyclic'),
            
            "pRefCell": safe_get_attr('_num_cpd_cells'),
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
            "cells_volume": 0,
            "node_type": 0,
            "graph_index": 0,
            "theta_PDE": 0,
            "x": 0,
            "y": 0,
            "m_ids": 0,
            "m_gs": 0,
            "global_idx": 0,
            "cell_type": 0,
            "cpd_centroid": 0,
            "face_area": 0,
            "face_type": 0,
            "cells_face_unv": 0,
            "_num_nodes": 0,
            "_num_faces": 0,
            "_num_cells": 0,
            "_num_cpd_cells": 0,
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
            "cpd_neighbor_cell_non_cyclic": 1, #[2, num_neighbor_cell_non_cyclic_edges]
            "cpd_neighbor_cell_x": 1,  # [2, num_neighbor_cell_x_edges]
            "cpd_neighbor_cell": 1,  # [2, num_cpd_neighbor_edges] - edge_index format
            "graph_index": 0,  # [batch_size] or [1]
            "global_idx": 0,  # [N]
            "pRefCell": 0,
            "pRefValue": 0,
            "cell_type": 0,
            "cpd_centroid": 0,
            "cells_index": 0,
            "cells_volume": 0,
            "face_area": 0,
            "face_type": 0,
            "cells_face_unv": 0,
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _safe_tensor_size(self, attr_name, dim=0, default=0):
        """Safely get tensor size, returning default if attr is None or missing."""
        val = getattr(self, attr_name, None)
        if val is None or not isinstance(val, torch.Tensor):
            return default
        return val.size(dim)

    def __inc__(self, key, value, *args, **kwargs):
        offset_rules = {
            "edge_index": self.num_nodes,
            "face": self.num_nodes,
            "cells_node": self.num_nodes,
            "face_node": self.num_nodes,
            "cells_face": self.num_nodes,
            "neighbour_cell": self.num_nodes,
            "face_node_x": self.num_nodes,
            "init_patch_phi_cpd_cell":0,
            "init_patch_phi_batch_cpd_cell":1, # 图batch级别的offset，因此设置为1
            "pos": 0,
            "cell_area": 0,
            "node_type": 0,
            "graph_index": 0,
            "pde_theta": 0,
            "uvp_dim": 0,
            "dt_graph": 0,
            "x": 0,
            "y": 0,
            "m_ids": 0,
            "m_gs": 0,
            "case_global_index": 0,
            "pv_cells_node": 0,
            "pv_cells_type": 0,
            "mask_internal_cell": 0,
            "mask_output_cell": 0,
            # 采样相关属性
            "original_node_indices": 0,
            "case_idx": 0,
            "sample_idx": 0,
            "num_cpd_cells_total": 0,
            "boundary_pos": 0,
            "boundary_unv": 0,
            "boundary_batch": 1,
            "boundary_patch_indices": self._safe_tensor_size("patch_input_ids", 0, 0),
            "patch_input_ids": 0,
            "patch_attention_mask": 0,
            "patch_unique_input_ids": 0,
            "patch_unique_attention_mask": 0,
            "patch_text_inverse_indices": self._safe_tensor_size("patch_unique_input_ids", 0, 0),
            "patch_ref_edge_index": self._safe_tensor_size("patch_input_ids", 0, 0),
            "patch_ref_edge_type": 0,
            "global_input_ids": 0,
            "global_attention_mask": 0,
            # Additional debug/sampling attributes
            "sampled_pivot_count": 0,
            "sampled_nodes_before_trunc": 0,
            "sampled_nodes_after_trunc": 0,
            "sampled_edge_count": 0,
            "sample_was_truncated": 0,
            "sampled_pivot_nodes": 0,
            "cpd_centroid": 0,
            "unv_cpd_cell": 0,
            "mesh_pos": 0,
            "patch_numerical_values": 0,
            "precomputed_patch_embeddings": 0,
            "precomputed_global_embeddings": 0,
            # AirfRANS single-stream baseline: per-case freestream [1,2] global (no node offset).
            "freestream_vec": 0,
        }
        if key in offset_rules:
            return offset_rules[key]
        # Fallback: guard against empty tensors that would crash PyG default __inc__
        if isinstance(value, torch.Tensor) and value.numel() == 0:
            return 0
        return super().__inc__(key, value, *args, **kwargs)

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
            "boundary_pos": 0,
            "boundary_unv": 0,
            "boundary_batch": 0,
            "boundary_patch_indices": 0,
            "patch_input_ids": 0,
            "patch_attention_mask": 0,
            "patch_unique_input_ids": 0,
            "patch_unique_attention_mask": 0,
            "patch_text_inverse_indices": 0,
            "patch_ref_edge_index": 1,
            "patch_ref_edge_type": 0,
            "global_input_ids": 0,
            "global_attention_mask": 0,
            # [1,2] per graph -> [B,2] after collation; importer broadcasts via data.batch.
            "freestream_vec": 0,
        }
        return cat_dim_rules.get(key, super().__cat_dim__(key, value, *args, **kwargs))


# ============================================================================
# HeteroFVGraph: 兼容性包装器
# ============================================================================

class HeteroFVGraph:
    """
    Compatibility wrapper that provides the same interface as original FVGraph.
    """

    def __init__(self, hetero_data):
        self._hetero_data = hetero_data
        self._create_compatibility_views()

    def _create_compatibility_views(self):
        """Create compatibility views that mimic original graph structure."""
        self.graph_node = self._NodeView(self._hetero_data)
        self.graph_face = self._FaceView(self._hetero_data)
        self.graph_cell = self._CellView(self._hetero_data)
        self.graph_cell_x = self._CellXView(self._hetero_data)
        self.graph_Index = self._IndexView(self._hetero_data)

    class _NodeView:
        def __init__(self, hetero_data):
            self._hetero_data = hetero_data

        @property
        def pos(self):
            return self._hetero_data['node'].pos

        @property
        def num_nodes(self):
            return self._hetero_data['node'].num_nodes

        @property
        def face_node(self):
            return self._hetero_data['face', 'contains', 'node'].face_node

        @property
        def cells_node(self):
            return self._hetero_data['cell', 'contains', 'node'].cells_node

        @property
        def _num_nodes(self):
            return self._hetero_data['node'].num_nodes

        @property
        def batch(self):
            return getattr(self._hetero_data['node'], 'batch', None)

    class _FaceView:
        def __init__(self, hetero_data):
            self._hetero_data = hetero_data

        @property
        def pos(self):
            return self._hetero_data['face'].pos

        @property
        def face_area(self):
            return self._hetero_data['face'].face_area

        @property
        def face_type(self):
            return self._hetero_data['face'].face_type

        @property
        def cyclic_face(self):
            return self._hetero_data['face'].cyclic_face

        @property
        def cells_face(self):
            return self._hetero_data['cell', 'bounded_by', 'face'].cells_face

        @property
        def face_node_ptr(self):
            return self._hetero_data['face', 'bounds', 'cell'].face_node_ptr

        @property
        def _num_faces(self):
            return self._hetero_data['face'].num_nodes

        @property
        def batch(self):
            return getattr(self._hetero_data['face'], 'batch', None)

    class _CellView:
        def __init__(self, hetero_data):
            self._hetero_data = hetero_data

        @property
        def x(self):
            return self._hetero_data['cpd_cell'].x

        @x.setter
        def x(self, value):
            self._hetero_data['cpd_cell'].x = value

        @property
        def y(self):
            return self._hetero_data['cpd_cell'].y

        @property
        def pos(self):
            return self._hetero_data['cpd_cell'].pos

        @property
        def cpd_neighbor_cell(self):
            return self._hetero_data['cpd_cell', 'neighbors', 'cpd_cell'].cpd_neighbor_cell

        @property
        def cells_volume(self):
            return self._hetero_data['cell'].cells_volume

        @property
        def cell_type(self):
            return self._hetero_data['cpd_cell'].cell_type

        @property
        def global_idx(self):
            return self._hetero_data['cpd_cell'].global_idx

        @property
        def cells_face_unv(self):
            return self._hetero_data['cell'].cells_face_unv

        @property
        def cpd_centroid(self):
            return self._hetero_data['cpd_cell'].pos

        @property
        def cells_node_ptr(self):
            return self._hetero_data['cell', 'contains', 'node'].cells_node_ptr

        @property
        def cells_face_ptr(self):
            return self._hetero_data['cell', 'bounded_by', 'face'].cells_face_ptr

        @property
        def num_graphs(self):
            # Get number of graphs from batch
            batch = getattr(self._hetero_data['cpd_cell'], 'batch', None)
            if batch is not None:
                return batch.max().item() + 1
            else:
                return 1

        @property
        def _num_cells(self):
            return self._hetero_data['cell'].num_nodes

        @property
        def _num_cpd_cells(self):
            return self._hetero_data['cpd_cell'].num_nodes

        @property
        def batch(self):
            return getattr(self._hetero_data['cpd_cell'], 'batch', None)

        # Norm attributes for compatibility
        @property
        def norm_uvwp(self):
            return getattr(self._hetero_data['cpd_cell'], 'norm_uvwp', None)

        @norm_uvwp.setter
        def norm_uvwp(self, value):
            self._hetero_data['cpd_cell'].norm_uvwp = value

    class _CellXView:
        def __init__(self, hetero_data):
            self._hetero_data = hetero_data

        @property
        def cpd_neighbor_cell_x(self):
            return self._hetero_data['cpd_cell', 'wlsq_neighbors', 'cpd_cell'].cpd_neighbor_cell_x

        @property
        def A_cell_to_cell(self):
            return self._hetero_data['cpd_cell'].A_cell_to_cell

        @property
        def single_B_cell_to_cell(self):
            return self._hetero_data['cpd_cell'].single_B_cell_to_cell

        @property
        def batch(self):
            return getattr(self._hetero_data['cpd_cell'], 'batch', None)

    class _IndexView:
        def __init__(self, hetero_data):
            self._hetero_data = hetero_data

        @property
        def theta_PDE(self):
            return self._hetero_data['graph'].theta_PDE

        @property
        def case_name(self):
            return self._hetero_data['graph'].case_name

        @property
        def pRefCell(self):
            return self._hetero_data['graph'].pRefCell

        @property
        def pRefValue(self):
            return self._hetero_data['graph'].pRefValue

        @property
        def graph_index(self):
            return self._hetero_data['graph'].graph_index

        @property
        def batch(self):
            return getattr(self._hetero_data['graph'], 'batch', None)

    def to(self, *args, exclude_keys=None, **kwargs):
        """Move the underlying hetero data to device/dtype."""
        self._hetero_data = self._hetero_data.to(*args, exclude_keys=exclude_keys, **kwargs)
        self._create_compatibility_views()
        return self

    def cuda(self, device=None, exclude_keys=None):
        """Move the underlying hetero data to CUDA device."""
        return self.to(device or 'cuda', exclude_keys=exclude_keys)


# ============================================================================
# 别名：向后兼容
# ============================================================================
HeteroGraphBase = HeteroGraph
HomoGraphData = HomoGraph


# ============================================================================
# 导出列表
# ============================================================================
__all__ = [
    'HeteroGraph',
    'HomoGraph',
    'HeteroFVGraph',
    'HeteroGraphBase',  # 别名
    'HomoGraphData',    # 别名
]
