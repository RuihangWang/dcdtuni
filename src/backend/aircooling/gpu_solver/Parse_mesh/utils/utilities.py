# 2 -*- encoding: utf-8 -*-
"""
@File    :   utilities.py
@Version :   2.0
@Contact :   lty1040808318@163.com
"""
import sys
import os

file_dir = os.path.dirname(os.path.dirname(__file__))
sys.path.append(file_dir)
import numpy as np
import torch
from torch_geometric.utils import scatter
from torch_geometric import utils as pyg_utils


def compose_edge_node_x(cells_type, cells_node):
    """
    Composes the unique connections between nodes that form the faces of each cell.

    Parameters:
    - cells_type (int): The number of nodes per cell (e.g., 3 for triangles, 4 for quadrilaterals).
    - cells_node (torch.Tensor): Tensor containing the indices of nodes for each cell, flattened.

    Returns:
    - torch.Tensor: A tensor of shape [2, num_faces], where each column represents a unique face defined by two node indices.
    """
    
    edge_node_x=[]
    origin_cells_node = cells_node.clone()
    for _ in range(cells_type-1):
        cells_node = torch.roll(cells_node.reshape(-1,cells_type), shifts=1, dims=1).reshape(-1)
        edge_node_x.append(torch.stack((origin_cells_node, cells_node), dim=0))
    edge_node_x = torch.cat(edge_node_x, dim=1)
    return torch.unique(edge_node_x[:,~(edge_node_x[0]==edge_node_x[1])].sort(dim=0)[0],dim=1)

def compose_support_edge_to_node(cells_type, cells_face, cells_node, offset=None):
    """
    Constructs the mapping between faces and nodes, indicating which nodes belong to each face.

    Parameters:
    - cells_type (int): The number of nodes per cell.
    - cells_face (torch.Tensor): Tensor containing the indices of faces for each cell.
    - cells_node (torch.Tensor): Tensor containing the indices of nodes for each face.
    - offset (int, optional): An optional offset to be added to the face indices.

    Returns:
    - torch.Tensor: A tensor of shape [2, num_edges], representing the unique connections between faces and nodes.
    """
    if offset is not None:
        cells_face += offset
        
    support_edge_to_node=[]
    for _ in range(cells_type):
        support_edge_to_node.append(torch.stack((cells_face, cells_node), dim=0))
        cells_node = torch.roll(cells_node.reshape(-1,cells_type), shifts=1, dims=1).reshape(-1)
    return torch.unique(torch.cat(support_edge_to_node, dim=1).sort(dim=0)[0],dim=1)

def compose_support_cell_to_node(cells_type, cells_index, cells_node, offset=None):
    """
    Constructs the mapping between cells and nodes, indicating which nodes belong to each cell.

    Parameters:
    - cells_type (int): The number of nodes per cell.
    - cells_index (torch.Tensor): Tensor containing the indices of cells.
    - cells_node (torch.Tensor): Tensor containing the indices of nodes for each cell.
    - offset (int, optional): An optional offset to be added to the cell indices.

    Returns:
    - torch.Tensor: A tensor of shape [2, num_edges], representing the unique connections between cells and nodes.
    """
    if offset is not None:
        cells_index += offset
        
    support_cell_to_node=[]
    for _ in range(cells_type):
        support_cell_to_node.append(torch.stack((cells_index, cells_node), dim=0))
        cells_node = torch.roll(cells_node.reshape(-1,cells_type), shifts=1, dims=1).reshape(-1)
    return torch.unique(torch.cat(support_cell_to_node, dim=1).sort(dim=0)[0],dim=1)

def seperate_domain(cells_ptr, cells_node=None, cells_face=None):
    """
    Separates the domain into different regions based on cell types (e.g., triangular, quadrilateral and polygons).

    Parameters:
    - cells_node (torch.Tensor): Tensor containing the node indices for each cell.
    - cells_face (torch.Tensor): Tensor containing the face indices for each cell.
    - cells_index (torch.Tensor): Tensor containing the cell indices.

    Returns:
    - list: A list of tuples, each containing:
        - ct (int): The cell type (number of nodes per cell).
        - cells_node_sub (torch.Tensor): Subset of cells_node for the cell type.
        - cells_face_sub (torch.Tensor): Subset of cells_face for the cell type.
        - cells_index_sub (torch.Tensor): Subset of cells_index for the cell type.
        - original_indices (torch.Tensor): 原始位置索引，用于恢复顺序
    """
    if cells_node is None and cells_face is None:
        raise ValueError("cells_node and cells_face cannot both be None")
    
    cells_type_ex = scatter(src=torch.ones_like(cells_ptr), 
        index=cells_ptr, 
        dim=0, 
    )
    
    cells_type = torch.unique(cells_type_ex, dim=0)
    
    domain_list = []
    for ct in cells_type:
        mask = (cells_type_ex==ct)[cells_ptr]
        original_indices = torch.where(mask)[0]  # 记录原始位置
        if cells_node is not None and cells_face is None:
            domain_list.append((ct, cells_node[mask], None, cells_ptr[mask], original_indices))
        elif cells_node is None and cells_face is not None:
            domain_list.append((ct, None, cells_face[mask], cells_ptr[mask], original_indices))
        else:
            domain_list.append((ct, cells_node[mask], cells_face[mask], cells_ptr[mask], original_indices))

    return domain_list

def build_k_hop_edge_index(edge_index, k):
    """
    用PyG的to_torch_coo_tensor和稀疏矩阵运算计算k跳邻居的连接关系。

    Parameters:
    - edge_index: [2, E] 原始的边索引，两个节点之间的连通关系。
    - k: 跳数，表示距离多少跳的邻居。

    Returns:
    - new_edge_index: 新的边索引数组, 包含距离当前节点k跳以外的邻居的连通关系。
    """
    # 将edge_index转换为稀疏矩阵 (COO 格式)
    sparse_adj = pyg_utils.to_torch_coo_tensor(edge_index)

    # 初始化邻接矩阵为一跳邻居
    adj_k = sparse_adj

    # 进行k-1次邻接矩阵自乘，得到k跳邻居
    for _ in range(k - 1):
        adj_k = torch.sparse.mm(adj_k, sparse_adj)

    # 从稀疏矩阵中提取新的edge_index (两跳或k跳邻居)
    new_edge_index = adj_k.coalesce().indices()

    return new_edge_index
