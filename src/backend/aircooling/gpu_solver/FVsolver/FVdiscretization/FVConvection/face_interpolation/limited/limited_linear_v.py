"""TVD-limited linear face interpolation, NVDVTVDV formula, for VECTOR fields."""
import torch

from .._common import apply_boundary_face_values, batched_matvec, select_upwind_state


def limited_linear_v_face_value(
    phi_cpd_cell,
    grad_phi_cpd_cell,
    *,
    k,
    owner_weight,
    neighbor_weight,
    cell_pos,
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
    "LimitedLinearV k": TVD limited scheme (OpenFOAM compatible) for VECTOR
    fields, using the NVDVTVDV formula.

    Blends the upwind value with the linear (central) interpolation through a
    limiter psi in [0, 1] computed from the normalized-variable ratio r:
        phi_face = phi_upwind + psi * (phi_linear - phi_upwind)
        psi = clamp(2*r / max(k, SMALL), 0, 1)
    Unlike the scalar NVDTVD formula, r is derived from the vector dot
    products |gradfV|^2 and gradfV . (d . gradcP), so a single scalar limiter
    is shared across all vector components at a face.

    Flux sign selects the upwind cell via a strictly-positive test
    (face_flux > 0); zero flux therefore selects the neighbour cell, the
    repository's established convention -- see select_upwind_state.

    Reference: OpenFOAM src/finiteVolume/interpolation/surfaceInterpolation/
               limitedSchemes/LimitedScheme/NVDVTVDV.H

    Args:
        phi_cpd_cell: Cell-centered field values [N_cpd_cells, N_vars]
        grad_phi_cpd_cell: Cell-centered gradients [N_cpd_cells, N_vars, 3]
        k: Limiter blending coefficient (0 = pure upwind, 1 = pure linear)
        owner_weight: Linear-interpolation weight for the owner cell [N_faces, 1, 1]
        neighbor_weight: Linear-interpolation weight for the neighbour cell [N_faces, 1, 1]
        cell_pos: Cell-center coordinates [N_cpd_cells, 3]
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
    phi_C, phi_F, phi_upwind, grad_upwind, _ = select_upwind_state(
        phi_cpd_cell, grad_phi_cpd_cell, face_flux, face_owner, face_neighbor,
        owner_to_face, neighbor_to_face,
    )

    # Linear interpolation as high-order baseline: [N_faces, N_vars]
    phi_linear = (
        phi_cpd_cell.unsqueeze(-1).index_select(-3, face_owner) * owner_weight +
        phi_cpd_cell.unsqueeze(-1).index_select(-3, face_neighbor) * neighbor_weight
    ).squeeze(-1)

    # gradfV = phi_F - phi_C: face difference [N_faces, N_vars]
    gradfV = phi_F - phi_C

    # d = neighbour_pos - owner_pos: [N_faces, 3]
    d = cell_pos[face_neighbor] - cell_pos[face_owner]

    # grad_upwind · d: [N_faces, N_vars]
    d_dot_grad = batched_matvec(grad_upwind, d[:, :, None]).squeeze(-1)

    # Small number for numerical stability (use larger value for float32)
    SMALL = 1e-10

    # === NVDVTVDV formula for VECTOR fields (LimitedLinearV) ===
    # OpenFOAM: gradf = gradfV & gradfV (dot product = |gradfV|²)
    gradf = (gradfV * gradfV).sum(dim=-1, keepdim=True)  # [..., N_faces, 1]

    # OpenFOAM: gradcf = gradfV & (d & gradcP)
    gradcf = (gradfV * d_dot_grad).sum(dim=-1, keepdim=True)  # [..., N_faces, 1]

    # OpenFOAM style: r = 2*(gradcf/gradf) - 1
    # With large ratio handling: if mag(gradcf) >= 1000*mag(gradf)
    #   return 2*1000*sign(gradcf)*sign(gradf) - 1
    mag_gradf = gradf.abs()
    mag_gradcf = gradcf.abs()
    large_ratio = mag_gradcf >= 1000.0 * mag_gradf

    # Safe division for normal case
    r_normal = 2.0 * gradcf / (gradf + SMALL) - 1.0

    # Large ratio case: OpenFOAM returns 2*1000*sign(gradcf)*sign(gradf) - 1
    # For vector field, gradf >= 0 always, so sign(gradf) = 1 when gradf > 0
    sign_gradf = torch.where(gradf > SMALL, torch.ones_like(gradf), torch.zeros_like(gradf))
    sign_gradcf = torch.sign(gradcf)
    r_large = 2000.0 * sign_gradcf * sign_gradf - 1.0

    # Select based on ratio
    r = torch.where(large_ratio, r_large, r_normal)

    # When gradf ≈ 0 (phi_C ≈ phi_F), use r=1 (full upwind, psi=2/k clamped to 1)
    r = torch.where(gradf < SMALL, torch.ones_like(r), r)

    # Apply limiter: ψ = clamp(2r/k, 0, 1) - OpenFOAM limitedLinearLimiter
    # twoByk = 2.0 / max(k, SMALL)
    # return clamp(twoByk * r, zero_one{})
    psi = torch.clamp(2.0 * r / max(k, SMALL), 0.0, 1.0)

    # Blend: phi_face = phi_upwind + ψ * (phi_linear - phi_upwind)
    phi_face = phi_upwind + psi * (phi_linear - phi_upwind)

    return apply_boundary_face_values(
        phi_face, phi_cpd_cell, all_boundary_non_cyclic, boundary_cpd_indices,
        boundary_face_mask,
    )
