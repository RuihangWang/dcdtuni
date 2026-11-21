"""Primitive <-> conservative variable conversion for compressible flow."""
import torch


def primitive_to_conservative(
        rho: torch.Tensor,
        uvw: torch.Tensor,
        p: torch.Tensor,
        gamma,
    ) -> torch.Tensor:
    """
    Convert primitive variables to conservative variables.

    Primitive: (ρ, u, v, w, p)
    Conservative: (ρ, ρu, ρv, ρw, ρE)

    Args:
        rho: Density [N, 1]
        uvw: Velocity vector [N, 3]
        p: Pressure [N, 1]

    Returns:
        Conservative variables [N, 5]
    """
    # 1. Momentum: ρu
    rho_uvw = rho * uvw  # [N, 3]

    # 2. Kinetic energy density: 0.5 * ρ * |u|^2
    # directly compute 0.5 * rho * (u^2 + v^2 + w^2)
    # using rho (not rho_safe) preserves the gradients accurately
    rho_kinetic = 0.5 * rho * torch.sum(uvw**2, dim=-1, keepdim=True) # [..., N, 1]

    # 3. Internal energy density: ρe = p / (gamma - 1)
    # This formula is mathematically equivalent but avoids division by rho
    rho_internal = p / (gamma - 1.0) # [N, 1]

    # 4. Total energy density: ρE = ρe + 0.5ρu^2
    rho_E = rho_internal + rho_kinetic  # [N, 1]

    # Concatenate
    conservative = torch.cat([rho, rho_uvw, rho_E], dim=-1)  # [..., N, 5]

    return conservative
