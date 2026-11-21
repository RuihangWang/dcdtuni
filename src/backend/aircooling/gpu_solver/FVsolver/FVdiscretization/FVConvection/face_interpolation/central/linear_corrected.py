"""Gradient-corrected linear (central) face interpolation."""
import torch

from .._common import batched_matvec


def linear_corrected_face_value(
    phi_cpd_cell,
    grad_phi_cpd_cell,
    *,
    cell_pos,
    face_pos,
    face_owner,
    face_neighbor,
    owner_weight,
    neighbor_weight,
) -> torch.Tensor:
    """
    "Linear corrected": distance-weighted linear interpolation plus a
    gradient-based correction for the offset between the face center and the
    weighted cell-center midpoint.

    Implements the scalar interpolation scheme from Moukalled et al.,
    "The Finite Volume Method in Computational Fluid Dynamics", Section 9.2, page 276:
        phi_face = gC * phi_C + gF * phi_F + grad_face . (x_face - x_hat_face)
    where x_hat_face = gC * x_C + gF * x_F is the weighted cell-center
    midpoint and grad_face = gC * grad_C + gF * grad_F is the weighted face
    gradient. The correction restores exactness on affine fields even when
    the face center is offset from x_hat_face (skewed meshes).

    Args:
        phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
        grad_phi_cpd_cell: Cell-centered gradients [N_cpd_cells, N_vars, 3]
        cell_pos: Cell-center coordinates [N_cpd_cells, 3]
        face_pos: Face-center coordinates [N_faces, 3]
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

    phi_face = (phi_cpd_cell_expanded[face_owner] * owner_weight +
                phi_cpd_cell_expanded[face_neighbor] * neighbor_weight).squeeze(2)

    grad_linear_face = (owner_weight*grad_phi_cpd_cell[face_owner] +
                        neighbor_weight*grad_phi_cpd_cell[face_neighbor])

    r_hat_face = (cell_pos[face_owner,:,None]*owner_weight + \
        cell_pos[face_neighbor,:,None]*neighbor_weight).squeeze(2)

    correction = batched_matvec(grad_linear_face, (face_pos-r_hat_face)[:,:,None]).squeeze(2)

    phi_face_corrected = phi_face + correction

    # # Preserve Dirichlet BC values on non-cyclic boundary faces
    # if torch.all(self.mask_boundary_non_cyclic_face):
    #     phi_face_corrected = phi_cpd_cell[self.mask_boundary_non_cyclic_cpd_cell]
    # else:
    #     mask = self.mask_boundary_non_cyclic_face[:, None]
    #     mask_expanded = mask.expand(-1, phi_face_corrected.shape[1])
    #     boundary_values = phi_cpd_cell[self.mask_boundary_non_cyclic_cpd_cell]
    #     boundary_values_full = torch.zeros_like(phi_face_corrected).masked_scatter(
    #         mask_expanded, boundary_values.reshape(-1)
    #     )
    #     phi_face_corrected = torch.where(
    #         mask_expanded, boundary_values_full, phi_face_corrected
        # )

    return phi_face_corrected
