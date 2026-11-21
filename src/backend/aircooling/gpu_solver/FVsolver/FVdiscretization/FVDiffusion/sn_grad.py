"""Surface-normal gradient kernels at faces (OpenFOAM-style snGrad)."""
import torch


def orthogonal_sn_grad(
    phi_cpd_cell,
    *,
    face_owner,
    face_neighbor,
    delta_coeffs,
) -> torch.Tensor:
    """
    "orthogonal": deltaCoeffs (1/|d|), no correction (orthogonalSnGrad).

    Args:
        phi_cpd_cell: [N_cpd, N_var] cell-centered field values
        face_owner: Owner-cell index per face [N_face]
        face_neighbor: Neighbour-cell index per face [N_face]
        delta_coeffs: Orthogonal delta coefficient 1/|d| [N_face, 1]

    Returns:
        [N_face, N_var] face-normal gradient (scalar per face per variable)
    """
    phi_C = phi_cpd_cell[face_owner]   # [N_face, N_var]
    phi_F = phi_cpd_cell[face_neighbor]  # [N_face, N_var]

    return (phi_F - phi_C) * delta_coeffs


def corrected_sn_grad(
    phi_cpd_cell,
    *,
    face_owner,
    face_neighbor,
    non_orth_delta_coeffs,
    grad_cell=None,
    owner_weight=None,
    neighbor_weight=None,
    correction_vectors=None,
) -> torch.Tensor:
    """
    "corrected": nonOrthDeltaCoeffs + plain-linear-interp correction (correctedSnGrad).
    "uncorrected": nonOrthDeltaCoeffs, no correction (uncorrectedSnGrad) --
    reached by leaving grad_cell=None.

    Key difference from previous version: this method takes CELL gradients
    and performs its own plain linear interpolation internally (matching
    OpenFOAM's linear<>::dotInterpolate), avoiding double correction.

    Args:
        phi_cpd_cell: [N_cpd, N_var] cell-centered field values
        face_owner: Owner-cell index per face [N_face]
        face_neighbor: Neighbour-cell index per face [N_face]
        non_orth_delta_coeffs: Non-orthogonal delta coefficient [N_face, 1]
        grad_cell: [N_cpd, N_var, 3] cell-centered gradients (for correction);
            None reproduces the uncorrected path exactly
        owner_weight: Interpolation weight for the owner cell [N_face, 1, 1]
        neighbor_weight: Interpolation weight for the neighbour cell [N_face, 1, 1]
        correction_vectors: Non-orthogonal correction vector [N_face, 3]

    Returns:
        [N_face, N_var] face-normal gradient (scalar per face per variable)
    """
    phi_C = phi_cpd_cell[face_owner]   # [N_face, N_var]
    phi_F = phi_cpd_cell[face_neighbor]  # [N_face, N_var]

    # uncorrected and corrected both use nonOrthDeltaCoeffs
    snGrad_val = (phi_F - phi_C) * non_orth_delta_coeffs  # [N_face, N_var]

    if grad_cell is not None:
        # Plain linear interpolation of CELL gradients (OpenFOAM: linear<>::dotInterpolate)
        # gC, gF: [N_face, 1, 1] broadcast with grad: [N_face, N_var, 3]
        grad_face = (grad_cell[face_owner] * owner_weight +
                     grad_cell[face_neighbor] * neighbor_weight)
        # correctionVectors: [N_face, 3] → [N_face, 1, 3] for broadcasting
        correction = (grad_face * correction_vectors[:, None, :]).sum(dim=-1)
        snGrad_val = snGrad_val + correction

    return snGrad_val  # [N_face, N_var]
