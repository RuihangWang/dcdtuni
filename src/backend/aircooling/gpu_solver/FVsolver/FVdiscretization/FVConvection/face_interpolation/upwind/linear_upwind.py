"""Second-order (OpenFOAM-style) linear-upwind face interpolation."""
import torch

from .._common import apply_boundary_face_values, batched_matvec, select_upwind_state


def linear_upwind_face_value(
    phi_cpd_cell,
    grad_phi_cpd_cell,
    *,
    face_flux,
    face_owner,
    face_neighbor,
    owner_to_face,
    neighbor_to_face,
    all_boundary_non_cyclic,
    boundary_cpd_indices,
    boundary_face_mask,
) -> torch.Tensor:
    """
    "LinearUpwind": second-order upwind (OpenFOAM style).

    All upwind-based schemes follow the unified formula:
        phi_face = phi_upwind + weight * (grad . r)
    where:
        - phi_upwind: upstream cell value based on flux direction
        - grad: gradient used for reconstruction (scheme-dependent)
        - r: vector from upwind cell center to face center
        - weight: blending factor (0 for pure upwind, 1 for full reconstruction)
    LinearUpwind uses weight=1 with the upstream gradient: gradient extrapolation
    from the upwind cell center to the face.

    Zero flux selects the neighbour cell (the repository's established
    convention: strictly-positive flux test); note that modern OpenFOAM's
    pos0 weights select the owner at exactly zero flux. See
    select_upwind_state.

    Args:
        phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
        grad_phi_cpd_cell: Cell-centered gradients [N_cpd_cells, N_vars, 3]
        face_flux: Normal velocity flux at faces [N_faces, 1]
        face_owner: Owner-cell index per face [N_faces]
        face_neighbor: Neighbour-cell index per face [N_faces]
        owner_to_face: Vector from owner cell center to face center [N_faces, 3, 1]
        neighbor_to_face: Vector from neighbour cell center to face center [N_faces, 3, 1]
        all_boundary_non_cyclic: True iff every face is a non-cyclic boundary face
        boundary_cpd_indices: Compound-cell indices feeding non-cyclic boundary faces
        boundary_face_mask: Per-face non-cyclic-boundary mask [N_faces]

    Returns:
        Face-centered field values [N_faces, N_vars]
    """
    _, _, phi_upwind, grad_upwind, r_upwind = select_upwind_state(
        phi_cpd_cell, grad_phi_cpd_cell, face_flux, face_owner, face_neighbor,
        owner_to_face, neighbor_to_face,
    )

    # Second-order upwind with gradient extrapolation
    grad_correction = batched_matvec(grad_upwind, r_upwind).squeeze(-1)
    phi_face = phi_upwind + grad_correction

    return apply_boundary_face_values(
        phi_face, phi_cpd_cell, all_boundary_non_cyclic, boundary_cpd_indices,
        boundary_face_mask,
    )
