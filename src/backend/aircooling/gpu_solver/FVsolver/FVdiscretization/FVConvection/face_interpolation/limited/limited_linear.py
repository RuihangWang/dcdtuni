"""TVD-limited linear face interpolation, NVDTVD formula, for SCALAR fields."""
import torch

from .._common import apply_boundary_face_values, batched_matvec, select_upwind_state

# Diagnostic trace (smoothness test): when set to a list, every call appends the per-face
# branch state of the limiter -- 0 clamped at 0 (upwind), 1 linear branch, 2 clamped at 1 (central), +3 when the
# large-ratio switch is active -- and the upwind side (face_flux > 0).  ``None`` (default): no cost.
TRACE = None


def limited_linear_face_value(
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
    smooth=None,
) -> torch.Tensor:
    """
    "LimitedLinear k": TVD limited scheme (OpenFOAM compatible) for SCALAR fields,
    using the NVDTVD formula.

    Blends the upwind value with the linear (central) interpolation through a
    limiter psi in [0, 1] computed from the normalized-variable ratio r:
        phi_face = phi_upwind + psi * (phi_linear - phi_upwind)
        psi = clamp(2*r / max(k, SMALL), 0, 1)

    Flux sign selects the upwind cell via a strictly-positive test
    (face_flux > 0); zero flux therefore selects the neighbour cell, the
    repository's established convention -- see select_upwind_state.

    Reference: OpenFOAM src/finiteVolume/interpolation/surfaceInterpolation/
               limitedSchemes/LimitedScheme/NVDTVD.H

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
        smooth: None (default, the OpenFOAM limiter above) or ``(eps, sharpness)`` -- a C-infinity variant for
            Newton (the hard limiter makes the k residual non-differentiable on the nearly
            uniform free stream, Taylor remainder 0.79 at relative steps >= 1e-7, so exact Newton converges only
            linearly).  The ratio is regularised as q = (gradcf gradf + delta^2) / (gradf^2 + delta^2),
            delta^2 = eps^2 mean_cells(phi^2) -- q -> 1 (linear, the r = 1 the hard version assigns to gradf ~ 0)
            where the data are uniform on the FIELD's scale and q -> gradcf / gradf once |gradf| >> delta.  The
            scale is GLOBAL and detached (a per-evaluation constant; its derivative, O(eps^2 / N_cells) per entry,
            is left out of J): a LOCAL scale eps |phi_P| was measured useless on the flat plate (k rows still 0.78
            at t >= 1e-7, i.e. perturbations far below 1e-3 |k| still crossed the kink), the global one makes the
            k rows O(t) down to t = 1e-10.  On uniform data psi does not change the face value at all, so this moves the discrete solution
            only where |gradf| ~ delta.  The clamp to [0, 1]
            is the softplus difference (softplus(s x) - softplus(s (x - 1))) / s, off by <= ln2 / s at the
            corners.  Precedent: Venkatakrishnan (1993, 1995) on limiter-induced stalls and differentiable limiters
            in Newton-Krylov RANS codes.

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

    # === NVDTVD formula for SCALAR fields (LimitedLinear) ===
    # OpenFOAM: gradf = phiN - phiP (scalar difference)
    gradf = gradfV  # [N_faces, N_vars]

    # OpenFOAM: gradcf = d & gradcP (or gradcN based on flux direction)
    gradcf = d_dot_grad  # [N_faces, N_vars]

    if smooth is not None:
        eps, sharp = float(smooth[0]), float(smooth[1])
        tiny = torch.finfo(gradf.dtype).tiny
        delta2 = (eps * eps) * phi_cpd_cell.detach().pow(2).mean(dim=-2, keepdim=True) + tiny
        q = (gradcf * gradf + delta2) / (gradf * gradf + delta2)
        xs = 2.0 * (2.0 * q - 1.0) / max(k, SMALL)
        psi = (torch.nn.functional.softplus(sharp * xs) - torch.nn.functional.softplus(sharp * (xs - 1.0))) / sharp
        if TRACE is not None:
            branch = torch.where(xs <= 0.0, 0, torch.where(xs >= 1.0, 2, 1))
            TRACE.append({"branch": branch.detach().to("cpu", torch.int8),
                          "upwind": (face_flux > 0).detach().reshape(-1).to("cpu")})
        phi_face = phi_upwind + psi * (phi_linear - phi_upwind)
        return apply_boundary_face_values(
            phi_face, phi_cpd_cell, all_boundary_non_cyclic, boundary_cpd_indices,
            boundary_face_mask,
        )

    # OpenFOAM style with large ratio handling
    mag_gradf = gradf.abs()
    mag_gradcf = gradcf.abs()
    large_ratio = mag_gradcf >= 1000.0 * mag_gradf

    # Safe division for normal case (preserve sign of gradf)
    gradf_safe = gradf + torch.sign(gradf) * SMALL
    # Handle gradf ≈ 0 case
    gradf_safe = torch.where(mag_gradf < SMALL, torch.ones_like(gradf), gradf_safe)
    r_normal = 2.0 * gradcf / gradf_safe - 1.0

    # Large ratio case
    r_large = 2000.0 * torch.sign(gradcf) * torch.sign(gradf) - 1.0

    # Select based on ratio
    r = torch.where(large_ratio, r_large, r_normal)

    # When gradf ≈ 0, use r=1
    r = torch.where(mag_gradf < SMALL, torch.ones_like(r), r)

    # Apply limiter: ψ = clamp(2r/k, 0, 1) - OpenFOAM limitedLinearLimiter
    # twoByk = 2.0 / max(k, SMALL)
    # return clamp(twoByk * r, zero_one{})
    psi = torch.clamp(2.0 * r / max(k, SMALL), 0.0, 1.0)
    if TRACE is not None:
        raw = 2.0 * r / max(k, SMALL)
        branch = torch.where(raw <= 0.0, 0, torch.where(raw >= 1.0, 2, 1)) + 3 * large_ratio.to(torch.long)
        TRACE.append({"branch": branch.detach().to("cpu", torch.int8),
                      "upwind": (face_flux > 0).detach().reshape(-1).to("cpu")})

    # Blend: phi_face = phi_upwind + ψ * (phi_linear - phi_upwind)
    phi_face = phi_upwind + psi * (phi_linear - phi_upwind)

    return apply_boundary_face_values(
        phi_face, phi_cpd_cell, all_boundary_non_cyclic, boundary_cpd_indices,
        boundary_face_mask,
    )
