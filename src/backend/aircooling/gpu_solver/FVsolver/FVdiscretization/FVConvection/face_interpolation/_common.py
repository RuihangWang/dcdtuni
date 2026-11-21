"""Shared helpers for cell-to-face reconstruction kernels.

``batched_matvec`` is the perf-motivated matrix-vector contraction every
gradient-correction term uses; ``select_upwind_state`` and
``apply_boundary_face_values`` are the common setup/epilogue every
upwind-based scheme in ``upwind/`` and ``limited/`` shares.
"""
import torch


def batched_matvec(mat: torch.Tensor, vec: torch.Tensor) -> torch.Tensor:
    """Contract ``mat[..., M, 3]`` with ``vec[..., 3, 1]`` -> ``[..., M, 1]``.

    Written as a broadcast product plus a reduction rather than ``mat @ vec``.
    The two are mathematically identical, but ``@`` sends this to a cuBLAS batched
    GEMM, and a batch of hundreds of thousands of matrices whose inner dimension is 3
    is the pathological case for that kernel: measured on 454320 faces it ran 7x
    (M=9) to 33x (M=1) off the memory-bandwidth roofline, and accounted for roughly
    half the residual's GPU time. The broadcast form reaches ~1.2x roofline with no
    compilation, giving 5x to 26x depending on M, and its forward-mode derivative is
    likewise about 2x cheaper.
    """
    return (mat * vec.transpose(-1, -2)).sum(-1, keepdim=True)


def select_upwind_state(
    phi_cpd_cell,
    grad_phi_cpd_cell,
    face_flux,
    face_owner,
    face_neighbor,
    owner_to_face,
    neighbor_to_face,
):
    """Owner/neighbour upwind selection shared by every upwind-based scheme.

    Zero flux selects the neighbour cell (the repository's established
    convention: strictly-positive flux test); note that modern OpenFOAM's
    pos0 weights select the owner at exactly zero flux.
    ``flux_positive = face_flux > 0`` is False at exactly zero, so
    ``torch.where`` picks the F-side (neighbour) value.

    Args:
        phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
        grad_phi_cpd_cell: Cell-centered gradients [N_cpd_cells, N_vars, 3]
        face_flux: Normal velocity flux at faces [N_faces, 1]
        face_owner: Owner-cell index per face [N_faces]
        face_neighbor: Neighbour-cell index per face [N_faces]
        owner_to_face: Vector from owner cell center to face center [N_faces, 3, 1]
        neighbor_to_face: Vector from neighbour cell center to face center [N_faces, 3, 1]

    Returns:
        Tuple ``(phi_C, phi_F, phi_upwind, grad_upwind, r_upwind)``:
            phi_C: Owner-cell value [N_faces, N_vars]
            phi_F: Neighbour-cell value [N_faces, N_vars]
            phi_upwind: Upstream cell value based on flux direction [N_faces, N_vars]
            grad_upwind: Upstream cell gradient [N_faces, N_vars, 3]
            r_upwind: Vector from upwind cell center to face center [N_faces, 3, 1]
    """
    # Owner (C) and Neighbour (F) values: [N_faces, N_vars]
    phi_C = phi_cpd_cell.index_select(-2, face_owner)
    phi_F = phi_cpd_cell.index_select(-2, face_neighbor)

    # Owner and Neighbour gradients: [N_faces, N_vars, 3]
    grad_C = grad_phi_cpd_cell.index_select(-3, face_owner)
    grad_F = grad_phi_cpd_cell.index_select(-3, face_neighbor)

    # Flux direction indicator: [N_faces, 1] - no squeeze() to maintain broadcasting
    flux_positive = face_flux > 0

    # Select upwind values using torch.where (dimension-safe)
    phi_upwind = torch.where(flux_positive, phi_C, phi_F)  # [N_faces, N_vars]
    grad_upwind = torch.where(flux_positive.unsqueeze(-1), grad_C, grad_F)  # [N_faces, N_vars, 3]

    # Vector from upwind cell to face: [N_faces, 3, 1]
    r_upwind = torch.where(flux_positive.unsqueeze(-1), owner_to_face, neighbor_to_face)

    return phi_C, phi_F, phi_upwind, grad_upwind, r_upwind


def apply_boundary_face_values(
    phi_face,
    phi_cpd_cell,
    all_boundary_non_cyclic,
    boundary_cpd_indices,
    boundary_face_mask,
):
    """Overwrite non-cyclic boundary faces with their ghost-cell value.

    Args:
        phi_face: Interior-scheme face values [N_faces, N_vars]
        phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
        all_boundary_non_cyclic: True iff every face is a non-cyclic boundary face
        boundary_cpd_indices: Compound-cell indices feeding non-cyclic boundary faces [N_bnc]
        boundary_face_mask: Per-face non-cyclic-boundary mask [N_faces]

    Returns:
        Face-centered field values with boundary faces overwritten [N_faces, N_vars]
    """
    if all_boundary_non_cyclic:
        phi_face = phi_cpd_cell.index_select(
            -2, boundary_cpd_indices
        )
    else:
        mask = boundary_face_mask.reshape(
            (1,) * (phi_face.ndim - 2) + (-1, 1)
        )
        mask_expanded = mask.expand_as(phi_face)
        boundary_values = phi_cpd_cell.index_select(
            -2, boundary_cpd_indices
        )
        boundary_values_full = torch.zeros_like(phi_face).masked_scatter(
            mask_expanded, boundary_values.reshape(-1)
        )
        phi_face = torch.where(mask_expanded, boundary_values_full, phi_face)

    return phi_face
