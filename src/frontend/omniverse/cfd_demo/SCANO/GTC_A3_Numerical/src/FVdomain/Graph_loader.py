"""
HeteroData-based loader for FV domain.
Defines loaders and compatibility wrapper for existing FVGraph interface.
"""

import sys
import os
file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)
from datetime import datetime

import torch
from torch_geometric.data import Batch as HeteroBatch

from .HeteroGraph_dataset import HeteroGraphDataset
from .Graph_data import HeteroFVGraph
from Post_process.to_vtk import export_full_mesh_vtu


class HeteroDataPool:
    """
    Data pool for heterogeneous graphs.
    """
    
    def __init__(self, params=None, device=None, state_save_dir=None):
        self.params = params
        self.device = device
        self.state_save_dir = state_save_dir

        self._plot_env = True
        self.reset_env_flag = False  # 初始化reset_env_flag
        self.rst_time = 1  # 初始化rst_time
        
    def load_mesh_to_cpu(self, dataset_dir=None):
        """Loads the mesh dataset to CPU."""
        from FVdomain.Load_mesh import H5CFDdataset
        
        valid_h5file_paths = []
        if dataset_dir.endswith(".h5"):
            valid_h5file_paths.append(dataset_dir)
        else:
            for subdir, _, files in os.walk(dataset_dir):
                for data_name in files:
                    if data_name.endswith(".h5"):
                        valid_h5file_paths.append(os.path.join(subdir, data_name))
                    
        if not valid_h5file_paths:
            raise ValueError(".h5 file not found in dataset directory")
        
        print("Loading whole dataset to cpu")
        
        mesh_dataset = H5CFDdataset(params=self.params, file_list=valid_h5file_paths)
        self.meta_pool = []
        self.phi_cpd_cell_pool = []
        start_idx = 0
        
        while True:
            for i_data in range(len(valid_h5file_paths)):
                meta_data, phi_cpd_cell = mesh_dataset[i_data]
                meta_data["global_idx"] = torch.arange(start_idx, start_idx + phi_cpd_cell.shape[0])
                self.meta_pool.append(meta_data)
                self.phi_cpd_cell_pool.append(phi_cpd_cell)
                start_idx += phi_cpd_cell.shape[0]

                if len(self.meta_pool) >= self.params.dataset_size:
                    break
                print(f" Number of loaded meshes: {len(self.meta_pool)}")

            if len(self.meta_pool) >= self.params.dataset_size:
                break
            
        print("Successfully loaded whole dataset to cpu")
        
        self.phi_cpd_cell_pool = torch.cat(self.phi_cpd_cell_pool, dim=0)

        # Control the folder grouping for the number of plots
        self.plot_count = 0
        return self.params
    
    @staticmethod
    def datapreprocessing_hetero(fv_graph):
        """Preprocesses the HeteroFVGraph object (compatibility wrapper)."""
        # Access through the compatibility interface
        fv_graph.graph_cell.x = torch.cat(
            (fv_graph.graph_cell.x, fv_graph.graph_Index.theta_PDE[fv_graph.graph_cell.batch]),
            dim=1
        )
        return fv_graph
    
    def _set_reset_env_flag(self, flag=False, rst_time=1):
        """
        Sets the flag for resetting the environment.

        Args:
            flag (bool): The flag to indicate whether to reset the environment.
            rst_time (int): The number of times to reset.
        """
        self.reset_env_flag = flag
        self.rst_time = rst_time

    def refresh_pool(self, uvwp_new, global_idx):
        """Updates the phi_cpd_cell_pool with new uvwp values."""
        self.phi_cpd_cell_pool[global_idx] = uvwp_new.data

        if self.reset_env_flag:
            for _ in range(getattr(self, 'rst_time', 1)):
                self.reset_env(plot=self._plot_env)
            self.reset_env_flag = False
            self._plot_env = True

    def reset_env(self, plot=False):
        """
        Resets the environment by removing the oldest mesh and adding a new one.
        This is a simplified version - you may need to implement the full logic
        from the original Data_Pool.reset_env method if needed.
        """
        # Import here to avoid circular imports
        from FVdomain.Load_mesh import CFDdatasetBase
        from Utils.utilities import FaceType

        # Pop the mesh data of the 0-th grid
        old_mesh = self.meta_pool.pop(0)
        old_global_idx = old_mesh["global_idx"]
        
        # Plotting
        if plot:

            if not (self.state_save_dir.find("traing_results") != -1):
                os.makedirs(f"{self.state_save_dir}/traing_results", exist_ok=True)
                self.state_save_dir = f"{self.state_save_dir}/traing_results"

            uvwp_cell = self.phi_cpd_cell_pool[old_global_idx] # [num_nodes_in_old_mesh, C]
            mask_interior_cell = (old_mesh["cpd|cell_type"].long()==FaceType.NORMAL).squeeze()

            ''' >>> plot at cell-center >>> '''
            self.export_to_tecplot(old_mesh, uvwp_cell[mask_interior_cell])
            ''' <<< plot at cell-center <<< '''

            self._plot_env = False
            
        # Remove uvwp data belonging to the 0-th grid
        self.phi_cpd_cell_pool = self.phi_cpd_cell_pool[old_global_idx.shape[0]:]

        for iidx in range(len(self.meta_pool)):
            cur_meta_data = self.meta_pool[iidx]
            cur_meta_data["global_idx"] -= old_global_idx.shape[0]

        # Generate new mesh data
        new_mesh, phi_cpd_cell = CFDdatasetBase.transform_mesh(
            old_mesh,
            self.params
        )
        new_mesh["global_idx"] = torch.arange(
            self.phi_cpd_cell_pool.shape[0], self.phi_cpd_cell_pool.shape[0]+phi_cpd_cell.shape[0]
        )
        self.phi_cpd_cell_pool = torch.cat((self.phi_cpd_cell_pool, phi_cpd_cell), dim=0)
        self.meta_pool.append(new_mesh)

    def export_to_tecplot(self, mesh, uvwp, file_name=None):
        """
        Exports data to Tecplot format, supports dynamic variable identification.

        Args:
            mesh (dict): Mesh data.
                         Contains 'node|pos': [N, D_pos], 'case_name', 'cells_node': [num_cells, max_nodes_per_cell],
                         'cells_face': [num_cells, max_faces_per_cell], 'cells_index': [num_cells], 'dt', 'source', 'aoa',
                         'face|neighbor_cell': [num_faces, 2], 'rho', 'mu'.
            uvwp (torch.Tensor): Main physical variable data, typically U, V, P. Shape: [num_elements, C] where num_elements
                                depends on datalocation (N for nodes, num_cells for cells).
            datalocation (str): Data location ("node" or "cell").
            file_name (str, optional): Output file name. Defaults to None.
        """
        
        # write vtk for visualization
        mesh_pos = mesh["node|pos"] # [N, D_pos]
        case_name = mesh["case_name"]
        dt = mesh["dt"].squeeze().item()
        source = mesh["source"].squeeze().item()
        aoa = mesh["aoa"].squeeze().item()

        if file_name is None:
            save_dir_num = self.plot_count//50
            saving_dir = f"{self.state_save_dir}/{save_dir_num*50}-{(save_dir_num+1)*50}"
            os.makedirs(saving_dir, exist_ok=True)
            saving_path = f"{saving_dir}/NO.{self.plot_count}_{case_name}_dt={dt:.3f}"
        else:
            saving_path = file_name

        export_full_mesh_vtu(
            mesh_pos=mesh_pos.cpu(), # [N, D_pos]
            pv_cells_node=mesh["pv_cells_node"],
            pv_cells_type=mesh["pv_cells_type"],
            data_dict={
                f"cell|Velocity":uvwp[:,0:3].cpu(), # [num_elements]
                f"cell|Pressure":uvwp[:,3:4].cpu(), # [num_elements]
            },
            save_file_path=f"{saving_path}.vtu",
        )

        self.plot_count+=1

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Exported training results to: {saving_path}")


def hetero_collate_fn(batch):
    """Custom collate function for heterogeneous graphs."""
    return HeteroBatch.from_data_list(batch)

class HeteroCompatibilityLoader:
    """Optimized compatibility loader that returns HeteroFVGraph objects without DataLoader overhead."""

    def __init__(self, base_dataset, batch_size=1, num_workers=0, pin_memory=True, shuffle=True):
        self.base_dataset = base_dataset
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.pin_memory = pin_memory

        # 创建HeteroGraphDataset用于数据转换
        self.hetero_dataset = HeteroGraphDataset(base_dataset)

        # 预计算数据集大小和索引
        self.dataset_size = len(self.base_dataset.meta_pool)
        self.indices = list(range(self.dataset_size))

        print(f"✅ Optimized loader initialized: {self.dataset_size} samples, batch_size={batch_size}")
        print(f"   - Direct memory access (no DataLoader overhead)")
        print(f"   - Shuffle: {shuffle}, Pin memory: {pin_memory}")

    def __iter__(self):
        """Returns an iterator over HeteroFVGraph objects using direct memory access."""
        # 每个epoch重新shuffle索引
        if self.shuffle:
            import random
            indices = self.indices.copy()
            random.shuffle(indices)
        else:
            indices = self.indices

        # 按batch_size分组生成batch
        for i in range(0, len(indices), self.batch_size):
            batch_indices = indices[i:i + self.batch_size]

            # 直接从内存获取数据，避免DataLoader开销
            batch_data = []
            for idx in batch_indices:
                hetero_data = self.hetero_dataset[idx]
                batch_data.append(hetero_data)

            # 使用原有的collate_fn逻辑
            hetero_batch = hetero_collate_fn(batch_data)

            # 返回兼容的HeteroFVGraph对象
            yield HeteroFVGraph(hetero_batch)

    def __len__(self):
        """返回batch数量"""
        return (self.dataset_size + self.batch_size - 1) // self.batch_size

    def __getitem__(self, indices):
        """Support direct indexing: loader[indices] - returns HeteroFVGraph"""
        hetero_graph = self.hetero_dataset[indices]
        return HeteroFVGraph(hetero_graph)


class HeteroDatasetFactory:
    """Factory class to create heterogeneous graph datasets and loaders."""
    
    def __init__(self, params=None, dataset_dir=None, state_save_dir=None, device=None):
        self.base_dataset = HeteroDataPool(
            params=params,
            device=device,
            state_save_dir=state_save_dir,
        )

        self.params = self.base_dataset.load_mesh_to_cpu(
            dataset_dir=dataset_dir,
        )

    def create_loader(self, batch_size=1, num_workers=0, pin_memory=True, shuffle=True):
        """Creates compatibility loader (default for backward compatibility)."""
        compatibility_loader = HeteroCompatibilityLoader(
            base_dataset=self.base_dataset,
            batch_size=batch_size,
            num_workers=num_workers,
            pin_memory=pin_memory,
            shuffle=shuffle
        )
        return self.base_dataset, compatibility_loader
