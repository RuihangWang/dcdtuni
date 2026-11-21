"""Two-way edge topology helper shared by the compressible JST kernels."""
import torch


def create_two_way_boundary_mask(boundary_mask, sources, targets):
    """
    Create boundary masks for two-way edges following SU2 rule:
    if (boundary_i && !boundary_j) continue;

    Args:
        boundary_mask: [N_cells] boolean tensor indicating boundary cells
        sources: [N_edges] source cell indices
        targets: [N_edges] target cell indices

    Returns:
        two_way_keep: [2*N_edges] boolean mask for which edges to keep
    """
    boundary_sources = boundary_mask[sources]
    boundary_targets = boundary_mask[targets]

    # SU2 rule: drop boundary <- interior contributions
    # keep_ij = ~(boundary_i && !boundary_j)
    keep_forward = ~(boundary_sources & ~boundary_targets)
    keep_backward = ~(boundary_targets & ~boundary_sources)

    # Concatenate for two-way edges
    two_way_keep = torch.cat([keep_forward, keep_backward], dim=0)

    return two_way_keep
