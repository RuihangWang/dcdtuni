"""Baseline sampling: sample internal cpd_cells, retain ALL boundary faces, re-induce edges."""
import torch
from torch_geometric.utils import subgraph


def build_full_boundary_subgraph(sampled_nodes, mask_internal_cell, neighbor_cpd_cell, num_cpd_cell):
    """Combine k-hop-sampled INTERNAL cpd_cells with ALL boundary faces (cpd_cell style).

    Args:
        sampled_nodes: LongTensor [S] global cpd_cell ids from the internal k-hop sample
            (may already include some boundary; those are dropped and replaced by the full boundary set).
        mask_internal_cell: BoolTensor [num_cpd_cell], True == internal.
        neighbor_cpd_cell: LongTensor [2, E] global cpd_cell adjacency.
        num_cpd_cell: int, total cpd_cell count.

    Returns:
        combined_nodes: LongTensor [N] = [internal_from_sample ... , all_boundary ...] (order preserved).
        edge_index: LongTensor [2, E'] induced on combined_nodes, relabeled to [0, N).
    """
    mask_internal_cell = mask_internal_cell.reshape(-1).bool()
    sampled_nodes = sampled_nodes.reshape(-1).long()
    internal_from_sample = sampled_nodes[mask_internal_cell[sampled_nodes]]
    boundary_all = (~mask_internal_cell).nonzero(as_tuple=True)[0]
    combined_nodes = torch.cat([internal_from_sample, boundary_all], dim=0)
    edge_index, _ = subgraph(
        combined_nodes, neighbor_cpd_cell, relabel_nodes=True, num_nodes=num_cpd_cell
    )
    return combined_nodes, edge_index
