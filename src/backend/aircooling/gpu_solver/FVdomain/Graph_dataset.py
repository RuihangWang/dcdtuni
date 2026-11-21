"""
HeteroData-based dataset for FV domain.
Defines the heterogeneous graph data structure and dataset.
"""

import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)

import numpy as np
import torch
from torch_geometric.data import Dataset, InMemoryDataset, HeteroData
from FVdomain.Graph_data import HeteroGraph
from FVdomain import region_physics
from Utils import geometry_precision

class HeteroGraphDataset(InMemoryDataset):
    """
    Dataset that creates HeteroData objects from the base dataset.
    """

    def __init__(self, base_dataset):
        super().__init__()
        self.base_dataset = base_dataset

    @property
    def meta_pool(self):
        return self.base_dataset.meta_pool

    @property
    def phi_cpd_cell_pool(self):
        return self.base_dataset.phi_cpd_cell_pool
    
    @property
    def time_step_pool(self):
        return self.base_dataset.time_step_pool
    
    def len(self):
        return len(self.meta_pool)

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
        Supports both legacy HeteroDataPool and mmap-based MmapDataPool.
        """
        # Detect pool type: MmapDataPool has 'slots' attribute
        if hasattr(self.base_dataset, 'slots'):
            return self._get_mmap(idx)
        return self._get_legacy(idx)

    def _get_legacy(self, idx):
        """Legacy code path for HeteroDataPool (in-memory tensors)."""
        minibatch_data = self.meta_pool[idx]
        hetero_graph = HeteroGraph()
        # floating type of the geometry and its WLSQ matrices: float64 only for a file parsed with float64 geometry
        gdt = geometry_precision.torch_dtype(minibatch_data)

        # === NODE DATA ===
        node_pos = minibatch_data["node|node_pos"].to(gdt)
        hetero_graph['node'].pos = node_pos
        hetero_graph['node'].num_nodes = node_pos.shape[0]

        # === FACE DATA ===
        face_area = minibatch_data["face|face_area"].to(gdt)
        face_type = minibatch_data["face|face_type"].long().squeeze()
        face_pos = minibatch_data["face|face_pos"].to(gdt)
        face_normal = minibatch_data["face|face_normal"].to(gdt)

        hetero_graph['face'].pos = face_pos
        hetero_graph['face'].face_area = face_area
        hetero_graph['face'].face_type = face_type
        hetero_graph['face'].face_normal = face_normal
        hetero_graph['face'].num_nodes = face_pos.shape[0]

        # === CELL DATA ===
        cell_volume = minibatch_data["cell|cell_volume"].to(gdt)
        cells_face_unv = minibatch_data['cells_face_normal'].to(gdt)
        hetero_graph['cell'].cell_volume = cell_volume
        hetero_graph['cell'].num_nodes = cell_volume.shape[0]
        hetero_graph['cell'].cells_face_unv = cells_face_unv

        # === CPD CELL DATA ===
        cpd_cell_pos = minibatch_data["cpd|cell_pos"].to(gdt)
        cell_type = minibatch_data["cpd|cell_type"].long()
        global_idx = minibatch_data["global_idx"].long()
        phi_cpd_cell = self.phi_cpd_cell_pool[global_idx]
        init_phi_cpd_cell = minibatch_data["init_phi_cpd_cell"].to(torch.float32)

        hetero_graph['cpd_cell'].pos = cpd_cell_pos
        hetero_graph['cpd_cell'].cell_type = cell_type
        hetero_graph['cpd_cell'].x = phi_cpd_cell
        hetero_graph['cpd_cell'].init_x = init_phi_cpd_cell
        hetero_graph['cpd_cell'].global_idx = global_idx
        hetero_graph['cpd_cell'].num_nodes = cpd_cell_pos.shape[0]

        # === GRAPH-LEVEL DATA ===
        case_name = minibatch_data["case_name"]
        theta_PDE = minibatch_data["theta_PDE"].to(torch.float32)
        pRefCell_valid_mask = torch.tensor([minibatch_data["fvconfig"]["fvSolution"]["pRefCell_valid_mask"]]).bool()
        # When pRefCell is invalid (-1), set to 0 to avoid offset issues; mask controls whether it's used
        raw_pRefCell = minibatch_data["fvconfig"]["fvSolution"]["pRefCell"]
        pRefCell = torch.tensor([max(0, raw_pRefCell)]).long()  # Use 0 as placeholder for invalid
        pRefValue = torch.tensor([minibatch_data["fvconfig"]["fvSolution"]["pRefValue"]]).to(torch.float32)
        dt = torch.tensor(minibatch_data["fvconfig"]["theta_pde"]["dt"]["val"]).to(torch.float32)
        time_step = self.time_step_pool[idx]

        hetero_graph['graph'].theta_PDE = theta_PDE
        hetero_graph['graph'].case_name = torch.tensor([ord(char) for char in case_name], dtype=torch.long)
        hetero_graph['graph'].graph_index = torch.tensor([idx], dtype=torch.long)
        hetero_graph['graph'].num_nodes = 1
        hetero_graph['graph'].pRefCell_valid_mask = pRefCell_valid_mask
        hetero_graph['graph'].pRefCell = pRefCell
        hetero_graph['graph'].pRefValue = pRefValue
        hetero_graph['graph'].time_step = time_step
        hetero_graph['graph'].dt = dt

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
        neighbor_cpd_cell = minibatch_data["cpd|neighbor_cell"].long()
        hetero_graph['cpd_cell', 'neighbors', 'cpd_cell'].neighbor_cpd_cell = neighbor_cpd_cell # 用来做面插值，

        # Cyclic face connectivity
        cyclic_face = minibatch_data["cyclic_face"].long()
        hetero_graph['face'].cyclic_face = cyclic_face

        # WLSQ connectivity and matrices
        neighbor_cpd_cell_x = minibatch_data["cpd|neighbor_cell_x"].long()
        A_cell_to_cell = minibatch_data["A_cell_to_cell"].to(gdt)
        single_B_cell_to_cell = minibatch_data["single_B_cell_to_cell"].to(gdt)

        hetero_graph['cpd_cell', 'wlsq_neighbors', 'cpd_cell'].neighbor_cpd_cell_x = neighbor_cpd_cell_x
        hetero_graph['cpd_cell'].A_cell_to_cell = A_cell_to_cell
        hetero_graph['cpd_cell'].single_B_cell_to_cell = single_B_cell_to_cell

        # CSR format for GNN message passing (used by segment_reduce)
        # NOTE: We store counts instead of indptr for easier batching
        # At runtime: indptr = F.pad(counts.cumsum(0), (1, 0))
        gnn_edge_counts = minibatch_data["gnn|edge_counts"].long()
        gnn_edge_indices = minibatch_data["gnn|edge_indices"].long()
        gnn_edge_sort_idx = minibatch_data["gnn|edge_sort_idx"].long()

        hetero_graph['cpd_cell'].gnn_edge_counts = gnn_edge_counts
        hetero_graph['cpd_cell'].gnn_edge_indices = gnn_edge_indices
        hetero_graph['cpd_cell'].gnn_edge_sort_idx = gnn_edge_sort_idx

        # Set the count attributes for proper batching offset calculation
        # Region physics (mesh schema v2): materials and heat sources; neutral tables for one-region cases
        region_physics.attach(hetero_graph, minibatch_data.get, cell_volume.shape[0])

        hetero_graph._num_nodes = node_pos.shape[0]
        hetero_graph._num_faces = face_pos.shape[0]
        hetero_graph._num_cells = cell_volume.shape[0]
        hetero_graph._num_cpd_cells = cpd_cell_pos.shape[0]
        hetero_graph._num_patches = len(minibatch_data['patch_dict'])
        # Number of two-way edges for CSR batching offset
        hetero_graph._num_twoway_edges = gnn_edge_sort_idx.shape[0]

        # retrieve fvpatch and boundary condition list
        fvpatch = minibatch_data['fvpatch']
        bcpatch = minibatch_data['bcpatch']

        return hetero_graph, fvpatch, bcpatch

    def _decompose_data_driven_idx(self, flat_idx):
        """Decompose flat index into (slot_id, t) for data-driven full-coverage."""
        cumulative = 0
        for i, slot in enumerate(self.base_dataset.slots):
            if not slot.has_labels:
                continue
            n = max(slot.n_timesteps - 1, 1)
            if flat_idx < cumulative + n:
                return i, flat_idx - cumulative
            cumulative += n
        raise IndexError(f"flat_idx {flat_idx} out of range (total {cumulative})")

    def _get_mmap(self, idx):
        """Mmap code path for MmapDataPool (slot-based access)."""
        pool_loss_mode = getattr(self.base_dataset, "loss_mode", "fvm")
        slot = self.base_dataset.slots[idx]
        meta = slot.meta_objects
        hetero_graph = HeteroGraph()
        # floating type of the geometry and its WLSQ matrices: float64 only for a file parsed with float64 geometry
        gdt = geometry_precision.torch_dtype(meta)

        # Helper: read a static tensor from shared mesh (clone to make writable)
        # or fall back to meta_objects for non-static keys.
        def _t(key, dtype=torch.float32):
            if slot._shared_mesh.has_tensor(key):
                return slot.get_mesh_tensor(key).clone().to(dtype)
            return meta[key].to(dtype)

        # === NODE DATA ===
        node_pos = _t("node|node_pos", gdt)
        hetero_graph['node'].pos = node_pos
        hetero_graph['node'].num_nodes = node_pos.shape[0]

        # === FACE DATA ===
        face_area = _t("face|face_area", gdt)
        face_type = _t("face|face_type", torch.int64).squeeze()
        face_pos = _t("face|face_pos", gdt)
        face_normal = _t("face|face_normal", gdt)

        hetero_graph['face'].pos = face_pos
        hetero_graph['face'].face_area = face_area
        hetero_graph['face'].face_type = face_type
        hetero_graph['face'].face_normal = face_normal
        hetero_graph['face'].num_nodes = face_pos.shape[0]

        # === CELL DATA ===
        cell_volume = _t("cell|cell_volume", gdt)
        cells_face_unv = _t("cells_face_normal", gdt)
        hetero_graph['cell'].cell_volume = cell_volume
        hetero_graph['cell'].num_nodes = cell_volume.shape[0]
        hetero_graph['cell'].cells_face_unv = cells_face_unv

        # === CPD CELL DATA ===
        cpd_cell_pos = _t("cpd|cell_pos", gdt)
        cell_type = _t("cpd|cell_type", torch.int64)

        # Both FVM-loss and data-driven rollout read phi from the live
        # mmap buffer (the model's own prediction from the previous step).
        phi_cpd_cell = slot.phi_tensor.clone()
        init_phi_cpd_cell = meta["init_phi_cpd_cell"].to(torch.float32)

        # Data-driven rollout: load GT label at the CURRENT timestep index.
        # Labels exclude t=0 (label[0] = t_dt, label[1] = t_2dt, ...),
        # so at time_step=0 the model predicts from init(t=0) toward label[0](t=dt).
        is_data_driven = (pool_loss_mode == "data_driven" and slot.has_labels)
        phi_GT_cpd_cell = None
        if is_data_driven:
            t_target = min(slot.time_step, slot.n_timesteps - 1)
            phi_GT_np = np.asarray(slot.get_label_at(t_target)).copy()
            t_offset = getattr(slot, '_label_T_offset', None)
            if t_offset is not None:
                phi_GT_np[:, -1] += t_offset
            phi_GT_cpd_cell = torch.from_numpy(phi_GT_np).to(torch.float32)

        hetero_graph['cpd_cell'].pos = cpd_cell_pos
        hetero_graph['cpd_cell'].cell_type = cell_type
        hetero_graph['cpd_cell'].x = phi_cpd_cell
        hetero_graph['cpd_cell'].init_x = init_phi_cpd_cell
        hetero_graph['cpd_cell'].slot_idx = torch.tensor([idx], dtype=torch.long)
        hetero_graph['cpd_cell'].num_nodes = cpd_cell_pos.shape[0]

        # === GRAPH-LEVEL DATA ===
        case_name = meta["case_name"]
        theta_PDE = meta["theta_PDE"].to(torch.float32)
        pRefCell_valid_mask = torch.tensor(
            [meta["fvconfig"]["fvSolution"]["pRefCell_valid_mask"]]
        ).bool()
        raw_pRefCell = meta["fvconfig"]["fvSolution"]["pRefCell"]
        pRefCell = torch.tensor([max(0, raw_pRefCell)]).long()
        pRefValue = torch.tensor(
            [meta["fvconfig"]["fvSolution"]["pRefValue"]]
        ).to(torch.float32)
        dt = torch.tensor(
            meta["fvconfig"]["theta_pde"]["dt"]["val"]
        ).to(torch.float32)
        time_step = torch.tensor(slot.time_step, dtype=torch.float32)

        hetero_graph['graph'].theta_PDE = theta_PDE
        hetero_graph['graph'].case_name = torch.tensor(
            [ord(c) for c in case_name], dtype=torch.long
        )
        hetero_graph['graph'].graph_index = torch.tensor([idx], dtype=torch.long)
        hetero_graph['graph'].num_nodes = 1
        hetero_graph['graph'].pRefCell_valid_mask = pRefCell_valid_mask
        hetero_graph['graph'].pRefCell = pRefCell
        hetero_graph['graph'].pRefValue = pRefValue
        hetero_graph['graph'].time_step = time_step
        hetero_graph['graph'].dt = dt

        # === CONNECTIVITY DATA ===
        hetero_graph['face', 'contains', 'node'].face_node = _t("face_node", torch.int64)

        cells_node = _t("cells_node", torch.int64)
        cells_node_ptr = _t("cells_node_ptr", torch.int64)
        hetero_graph['cell', 'contains', 'node'].cells_node = cells_node
        hetero_graph['cell', 'contains', 'node'].cells_node_ptr = cells_node_ptr

        cells_face = _t("cells_face", torch.int64)
        cells_face_ptr = _t("cells_face_ptr", torch.int64)
        face_node_ptr = _t("face_node_ptr", torch.int64)
        hetero_graph['cell', 'bounded_by', 'face'].cells_face = cells_face
        hetero_graph['cell', 'bounded_by', 'face'].cells_face_ptr = cells_face_ptr
        hetero_graph['face', 'bounds', 'cell'].face_node_ptr = face_node_ptr

        neighbor_cpd_cell = _t("cpd|neighbor_cell", torch.int64)
        hetero_graph['cpd_cell', 'neighbors', 'cpd_cell'].neighbor_cpd_cell = neighbor_cpd_cell

        cyclic_face = _t("cyclic_face", torch.int64)
        hetero_graph['face'].cyclic_face = cyclic_face

        neighbor_cpd_cell_x = _t("cpd|neighbor_cell_x", torch.int64)
        A_cell_to_cell = _t("A_cell_to_cell", gdt)
        single_B_cell_to_cell = _t("single_B_cell_to_cell", gdt)

        hetero_graph['cpd_cell', 'wlsq_neighbors', 'cpd_cell'].neighbor_cpd_cell_x = neighbor_cpd_cell_x
        hetero_graph['cpd_cell'].A_cell_to_cell = A_cell_to_cell
        hetero_graph['cpd_cell'].single_B_cell_to_cell = single_B_cell_to_cell

        gnn_edge_counts = _t("gnn|edge_counts", torch.int64)
        gnn_edge_indices = _t("gnn|edge_indices", torch.int64)
        gnn_edge_sort_idx = _t("gnn|edge_sort_idx", torch.int64)

        hetero_graph['cpd_cell'].gnn_edge_counts = gnn_edge_counts
        hetero_graph['cpd_cell'].gnn_edge_indices = gnn_edge_indices
        hetero_graph['cpd_cell'].gnn_edge_sort_idx = gnn_edge_sort_idx

        # Region physics (mesh schema v2): per-query, so in the slot's meta; neutral tables for one-region cases
        region_physics.attach(hetero_graph, meta.get, cell_volume.shape[0])

        # Count attributes for batching offset
        hetero_graph._num_nodes = node_pos.shape[0]
        hetero_graph._num_faces = face_pos.shape[0]
        hetero_graph._num_cells = cell_volume.shape[0]
        hetero_graph._num_cpd_cells = cpd_cell_pos.shape[0]
        hetero_graph._num_patches = len(meta['patch_dict'])
        hetero_graph._num_twoway_edges = gnn_edge_sort_idx.shape[0]

        # === DATA-DRIVEN EXTRAS (rollout mode: GT label for loss + BC) ===
        if phi_GT_cpd_cell is not None:
            hetero_graph['cpd_cell'].phi_GT_cpd_cell = phi_GT_cpd_cell

        fvpatch = meta['fvpatch']
        bcpatch = meta['bcpatch']

        return hetero_graph, fvpatch, bcpatch


class HeteroFVGraph:
    """
    Compatibility wrapper that provides the same interface as original FVGraph.
    """
    
    def __init__(self, hetero_data, fvpatch_batch, bcpatch_batch):
        self._hetero_data = hetero_data
        self.fvpatch_batch = fvpatch_batch
        self.bcpatch_batch = bcpatch_batch
        self._create_compatibility_views()
    
    def _create_compatibility_views(self):
        """Create compatibility views that mimic original graph structure."""
        self.graph_node = self._NodeView(self._hetero_data)
        self.graph_face = self._FaceView(self._hetero_data)
        self.graph_cell = self._CellView(self._hetero_data)
        self.graph_cell_x = self._CellXView(self._hetero_data)
        self.graph_Index = self._IndexView(self._hetero_data) # graph_level view
    
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
        def face_normal(self):
            return self._hetero_data['face'].face_normal
        
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
        def init_x(self):
            return self._hetero_data['cpd_cell'].init_x
        
        @init_x.setter
        def init_x(self, value):
            self._hetero_data['cpd_cell'].init_x = value
                
        @property
        def y(self):
            return self._hetero_data['cpd_cell'].y
            
        @property
        def pos(self):
            return self._hetero_data['cpd_cell'].pos

        @property
        def neighbor_cpd_cell(self):
            return self._hetero_data['cpd_cell', 'neighbors', 'cpd_cell'].neighbor_cpd_cell

        @neighbor_cpd_cell.setter
        def neighbor_cpd_cell(self, value):
            self._hetero_data['cpd_cell', 'neighbors', 'cpd_cell'].neighbor_cpd_cell = value

        @property
        def cell_volume(self):
            return self._hetero_data['cell'].cell_volume
            
        @property
        def cell_type(self):
            return self._hetero_data['cpd_cell'].cell_type
            
        @property
        def global_idx(self):
            return self._hetero_data['cpd_cell'].global_idx

        @property
        def slot_idx(self):
            si = getattr(self._hetero_data['cpd_cell'], 'slot_idx', None)
            if si is not None:
                return si
            return self._hetero_data['cpd_cell'].global_idx

        @property
        def cells_face_unv(self):
            return self._hetero_data['cell'].cells_face_unv

        @property
        def cpd_cell_pos(self):
            return self._hetero_data['cpd_cell'].pos

        @property
        def fourier_features(self):
            return getattr(self._hetero_data['cpd_cell'], 'fourier_features', None)

        @property
        def cells_node_ptr(self):
            return self._hetero_data['cell', 'contains', 'node'].cells_node_ptr

        @property
        def cells_face_ptr(self):
            return self._hetero_data['cell', 'bounded_by', 'face'].cells_face_ptr

        @property
        def _num_graphs(self):
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
        
        # CSR format properties for GNN message passing (used by segment_reduce)
        # NOTE: We store counts instead of indptr for easier batching
        # At runtime: indptr = F.pad(counts.cumsum(0), (1, 0))
        @property
        def gnn_edge_counts(self):
            return self._hetero_data['cpd_cell'].gnn_edge_counts
        
        @property
        def gnn_edge_indices(self):
            return self._hetero_data['cpd_cell'].gnn_edge_indices
        
        @property
        def gnn_edge_sort_idx(self):
            return self._hetero_data['cpd_cell'].gnn_edge_sort_idx
            
        # Norm attributes for compatibility
        @property
        def norm_uvwp(self):
            return getattr(self._hetero_data['cpd_cell'], 'norm_uvwp', None)
            
        @norm_uvwp.setter
        def norm_uvwp(self, value):
            self._hetero_data['cpd_cell'].norm_uvwp = value

        @property
        def reference_phi(self):
            val = getattr(self._hetero_data['cpd_cell'], 'reference_phi', None)
            if val is None:
                raise AttributeError("reference_phi not set")
            return val

        @property
        def reference_delta(self):
            val = getattr(self._hetero_data['cpd_cell'], 'reference_delta', None)
            if val is None:
                raise AttributeError("reference_delta not set")
            return val

        @property
        def phi_GT_cpd_cell(self):
            val = getattr(self._hetero_data['cpd_cell'], 'phi_GT_cpd_cell', None)
            if val is None:
                raise AttributeError("phi_GT_cpd_cell not set")
            return val

    class _CellXView:
        def __init__(self, hetero_data):
            self._hetero_data = hetero_data
            
        @property
        def neighbor_cpd_cell_x(self):
            return self._hetero_data['cpd_cell', 'wlsq_neighbors', 'cpd_cell'].neighbor_cpd_cell_x
            
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
        def time_step(self):
            return self._hetero_data['graph'].time_step
        
        @property
        def dt(self):
            return self._hetero_data['graph'].dt
        
        @property
        def case_name(self):
            return self._hetero_data['graph'].case_name

        @property
        def pRefCell_valid_mask(self):
            return self._hetero_data['graph'].pRefCell_valid_mask
        
        @property
        def pRefCell(self):
            return self._hetero_data['graph'].pRefCell
        
        @property
        def pRefValue(self):
            return self._hetero_data['graph'].pRefValue
        
        @property
        def graph_index(self):
            return self._hetero_data['graph'].graph_index

        # region physics (mesh schema v2; FVdomain/region_physics.py): None on graphs built before it
        @property
        def theta_material(self):
            return getattr(self._hetero_data['graph'], 'theta_material', None)

        @property
        def theta_source(self):
            return getattr(self._hetero_data['graph'], 'theta_source', None)

        @property
        def material_id(self):
            return getattr(self._hetero_data['cell'], 'material_id', None)

        @property
        def source_id(self):
            return getattr(self._hetero_data['cell'], 'source_id', None)
            
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


''' >>> for data-driven method compatibility >>> '''
from torch.utils.data import Dataset
from FVdomain.Graph_data import HomoGraph
import h5py
from torch_geometric.utils import k_hop_subgraph
from Utils.utilities import FaceType


class GraphDataset(Dataset):
    def __init__(self, params, subsampling=True):
        
        self.params = params
        root_dir = self.params.dataset_dir

        self.h5_file_list = []
        # 从root_dit中收集每一个h5文件的绝对路径

        for root, dirs, files in os.walk(root_dir):
            for file in files:
                if file.endswith(".h5"):
                    self.h5_file_list.append(os.path.join(root, file))

        # 在训练的时候要采样，推理的时候不采样
        self.subsampling = subsampling

    def __len__(self):
        return len(self.h5_file_list)
    
    def __getitem__(self, idx):
        h5_file = self.h5_file_list[idx]
        h5_file_handle = h5py.file(h5_file, "r")

        cpd_cell_pos = h5_file_handle["cpd|cell_pos"][()]
        neighbor_cpd_cell = h5_file_handle["cpd|neighbor_cell"][()]

        # 定义维度
        num_cpd_cells = cpd_cell_pos.shape[0]
        num_faces = neighbor_cpd_cell.shape[1]
        
        init_uvw = h5_file_handle["uvw_cpd_cell"][0,:,:]
        init_p = h5_file_handle["pres_cpd_cell"][0,:,:]
        init_T = h5_file_handle["temp_cpd_cell"][0,:,:]
        input_phi = torch.cat((init_uvw, init_p, init_T), dim=1)

        ''' >>> 
        尝试输入面法矢，但是由于h5里面存储的是cells_face维度的unit_norm_vector(unv),
        因而需要将其变换到face维度，然后将边界处的法矢拼接到cpd_cell上 
        >>> '''
        
        # 首先将cells_face_unv变换到face维度
        cells_face_unv = h5_file_handle["cells_face_normal"][()]
        cell_face = h5_file_handle["cell_face"][()]
        unv_face = torch.zeros((num_faces,3), type=torch.float32)
        unv_face[cell_face] = cells_face_unv
        
        # 然后生成cpd_cell和face维度的边界面元的mask
        cell_type = h5_file_handle["cpd|cell_type"][()].long().squeeze()
        face_type = h5_file_handle["face|face_type"][()].long().squeeze()
        mask_boundary_cell = cell_type!=FaceType.NORMAL
        mask_boundary_face = face_type!=FaceType.NORMAL
        
        # 将face维度的unv变换到cpd_cell维度的边界元上
        unv_cpd_cell = torch.zeros((num_cpd_cells),3)
        unv_cpd_cell[mask_boundary_cell] = unv_face[mask_boundary_face]
        
        ''' <<< 
        尝试输入面法矢，但是由于h5里面存储的是cells_face维度的unit_norm_vector(unv),
        因而需要将其变换到face维度，然后将边界处的法矢拼接到cpd_cell上 
        <<< '''
        
        target_uvw = h5_file_handle["uvw_cpd_cell"][-1,:,:]
        target_p = h5_file_handle["pres_cpd_cell"][-1,:,:]
        target_T = h5_file_handle["temp_cpd_cell"][-1,:,:]
        target_phi = torch.cat((target_uvw, target_p, target_T), dim=1)

        if self.subsampling:
            
            # self.params.num_samples = 50 表示采样百分比，这个参数一定小于100%
            sampled_nodes = torch.randint(
                0, num_cpd_cells, [self.params.num_samples]
            )
            subgraph_nodes, subgraph_neighbor_cpd_cell, _, _ = k_hop_subgraph(
                sampled_nodes, self.params.k_hop, neighbor_cpd_cell, relabel_nodes=True
            )
            init_phi = input_phi[subgraph_nodes]
            target_phi = target_phi[subgraph_nodes]
            cpd_cell_pos = cpd_cell_pos[subgraph_nodes]
            unv_cpd_cell = unv_cpd_cell[subgraph_nodes]
        else:
            subgraph_neighbor_cpd_cell = neighbor_cpd_cell

        homograph = HomoGraph(
            x = init_phi,
            y = target_phi,
            edge_index = subgraph_neighbor_cpd_cell,
            pos = cpd_cell_pos,
            unv_cpd_cell = unv_cpd_cell,
        )

        return homograph

''' <<< for data-driven method compatibility <<< '''