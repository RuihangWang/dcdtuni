"""Distance-weighted linear (central) face interpolation."""
import torch


def linear_face_value(
    phi_cpd_cell,
    *,
    face_owner,
    face_neighbor,
    owner_weight,
    neighbor_weight,
) -> torch.Tensor:
    """
    "Linear": distance-weighted linear interpolation from cell centers to face centers.

    Implements the scalar interpolation scheme from Moukalled et al.,
    "The Finite Volume Method in Computational Fluid Dynamics", Section 9.2, page 276:
        phi_face = gC * phi_C + gF * phi_F
    using pre-computed geometric interpolation weights (gC, gF) for high-order
    accuracy at no extra runtime cost.

    Args:
        phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
        face_owner: Owner-cell index per face [N_faces]
        face_neighbor: Neighbour-cell index per face [N_faces]
        owner_weight: Linear-interpolation weight for the owner cell [N_faces, 1, 1]
        neighbor_weight: Linear-interpolation weight for the neighbour cell [N_faces, 1, 1]

    Returns:
        Face-centered field values [N_faces, N_vars]

    Reference:
        Moukalled, F., Mangani, L., & Darwish, M. (2016). The finite volume method
        in computational fluid dynamics. Springer, Section 9.2.
    """
    # Expand phi_cell for broadcasting with geometric coefficients
    phi_cpd_cell_expanded = phi_cpd_cell[:, :, None]

    # Compute interpolated value using distance-weighted averaging at interior face
    phi_face = (phi_cpd_cell_expanded[face_owner] * owner_weight +
                phi_cpd_cell_expanded[face_neighbor] * neighbor_weight).squeeze(2)

    # # Preserve Dirichlet BC values on non-cyclic boundary faces
    # if torch.all(self.mask_boundary_non_cyclic_face):
    #     phi_face = phi_cpd_cell[self.mask_boundary_non_cyclic_cpd_cell]
    # else:
    #     mask = self.mask_boundary_non_cyclic_face[:, None]
    #     mask_expanded = mask.expand(-1, phi_face.shape[1])
    #     boundary_values = phi_cpd_cell[self.mask_boundary_non_cyclic_cpd_cell]
    #     boundary_values_full = torch.zeros_like(phi_face).masked_scatter(
    #         mask_expanded, boundary_values.reshape(-1)
    #     )
    #     phi_face = torch.where(mask_expanded, boundary_values_full, phi_face)

    return phi_face
