"""Gradient-averaged linear (central) face interpolation."""
import torch


def linear_grad_face_value(
    phi_cpd_cell,
    grad_phi_cpd_cell,
    *,
    face_owner,
    face_neighbor,
    owner_weight,
    neighbor_weight,
    owner_to_face,
    neighbor_to_face,
    interior_face_mask,
) -> torch.Tensor:
    """
    "Linear grad": distance-weighted linear interpolation plus the
    owner/neighbour gradients projected onto their own cell-to-face vectors
    and blended with the same weights as the value interpolation.

    Implements the scalar interpolation scheme from Moukalled et al.,
    "The Finite Volume Method in Computational Fluid Dynamics", Section 9.2, page 276:
        phi_face = gC * phi_C + gF * phi_F
                 + gC * (grad_C . Cf) + gF * (grad_F . Ff)
    where Cf, Ff are the owner- and neighbour-cell-center-to-face vectors.
    The gradient term is zeroed on non-interior (boundary/cyclic) faces,
    where Cf/Ff do not carry the same interior-stencil meaning.

    Args:
        phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
        grad_phi_cpd_cell: Cell-centered gradients [N_cpd_cells, N_vars, 3]
        face_owner: Owner-cell index per face [N_faces]
        face_neighbor: Neighbour-cell index per face [N_faces]
        owner_weight: Linear-interpolation weight for the owner cell [N_faces, 1, 1]
        neighbor_weight: Linear-interpolation weight for the neighbour cell [N_faces, 1, 1]
        owner_to_face: Vector from owner cell center to face center [N_faces, 3, 1]
        neighbor_to_face: Vector from neighbour cell center to face center [N_faces, 3, 1]
        interior_face_mask: True on interior (non-boundary, non-cyclic) faces [N_faces]

    Returns:
        Face-centered field values [N_faces, N_vars]

    Reference:
        Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method
        in computational fluid dynamics. Springer, Section 9.2.
    """
    # Expand phi_cell for broadcasting with geometric coefficients
    phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]

    phi_face = (phi_cpd_cell_expanded[face_owner] * owner_weight +
                phi_cpd_cell_expanded[face_neighbor] * neighbor_weight).squeeze(2)

    grad_linear_face = (owner_weight*(grad_phi_cpd_cell[face_owner]@owner_to_face) +
                        neighbor_weight*(grad_phi_cpd_cell[face_neighbor]@neighbor_to_face)).squeeze(2)

    if torch.all(interior_face_mask):
        phi_face = phi_face + grad_linear_face
    else:
        mask_interior = interior_face_mask[:, None]
        phi_face = phi_face + torch.where(
            mask_interior,
            grad_linear_face,
            torch.zeros_like(grad_linear_face),
        )

    return phi_face
