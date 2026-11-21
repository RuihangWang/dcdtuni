"""Assemble the 11D A2-style cpd_cell node feature for baseline models."""
import torch


def assemble_baseline_node_features(cpd_centroid, unv_cpd_cell, phi, mask_internal_cell,
                                    normalizer=None):
    """Build [pos(3), unv(3), bc(5)] = 11D. Internal BC-slots zeroed (unv is the implicit marker).

    Args:
        cpd_centroid: [N,3] positions. unv_cpd_cell: [N,3] (0 on internal).
        phi: [N,5] = [u,v,w,p,T] (unconverged t=1; boundary BC values, raw).
        mask_internal_cell: [N] bool, True == internal.
        normalizer: optional callable applied to phi (z-score) BEFORE zeroing internal. Default None (raw, A2-style).
    Returns:
        x_node: [N,11] float32.
    """
    mask_internal = mask_internal_cell.reshape(-1).bool()
    bc = phi[:, :5].clone().float()
    if normalizer is not None:
        bc = normalizer(bc)
    bc[mask_internal] = 0.0
    return torch.cat([cpd_centroid.float(), unv_cpd_cell.float(), bc], dim=-1)
