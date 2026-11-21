"""HLLC approximate Riemann solver for the compressible Euler equations."""
import torch


def hllc_riemann_flux(rho_L, uvw_L, p_L, rho_R, uvw_R, p_R, face_normal, R=287.0, gamma=1.4):
    """
    HLLC approximate Riemann solver for compressible Euler equations.

    A robust and accurate Riemann solver that handles strong shocks well.

    Args:
        rho_L, rho_R: Density on left and right sides [N_faces, 1]
        uvw_L, uvw_R: Velocity vectors on left and right sides [N_faces, 3]
        p_L, p_R: Pressure on left and right sides [N_faces, 1]
        face_normal: Unit normal vector pointing from left to right [N_faces, 3]
        gamma: Specific heat ratio (default 1.4 for air)

    Returns:
        Tuple of fluxes: (mass_flux, momentum_flux, energy_flux)
    """
    # Gas properties
    # cv = R / (gamma - 1.0)

    # Compute normal and tangential velocities
    un_L = torch.sum(uvw_L * face_normal, dim=-1, keepdim=True)
    un_R = torch.sum(uvw_R * face_normal, dim=-1, keepdim=True)

    # Tangential velocity components
    ut_L = uvw_L - un_L * face_normal
    ut_R = uvw_R - un_R * face_normal

    # Sound speeds
    a_L = torch.sqrt(gamma * p_L / rho_L)
    a_R = torch.sqrt(gamma * p_R / rho_R)

    # Compute specific total energy
    # e = p / ((gamma-1) * rho)
    e_L = p_L / ((gamma - 1.0) * rho_L)
    e_R = p_R / ((gamma - 1.0) * rho_R)
    
    E_L = e_L + 0.5*torch.sum(uvw_L**2, dim=-1, keepdim=True)
    E_R = e_R + 0.5*torch.sum(uvw_R**2, dim=-1, keepdim=True)

    # Wave speed estimates (Toro's method)
    # Roe-averaged values
    sqrt_rho_L = torch.sqrt(rho_L)
    sqrt_rho_R = torch.sqrt(rho_R)
    denom = sqrt_rho_L + sqrt_rho_R
    un_avg = (sqrt_rho_L * un_L + sqrt_rho_R * un_R) / denom
    
    # H = E + p/rho
    H_L = E_L + p_L/rho_L
    H_R = E_R + p_R/rho_R
    H_avg = (sqrt_rho_L * H_L + sqrt_rho_R * H_R) / denom
    a_avg = torch.sqrt((gamma - 1.0) * (H_avg - 0.5*un_avg**2))

    # Wave speeds
    S_L = torch.minimum(un_L - a_L, un_avg - a_avg)
    S_R = torch.maximum(un_R + a_R, un_avg + a_avg)

    # Middle wave speed
    S_M = ((p_R - p_L) + rho_L*un_L*(S_L - un_L) - rho_R*un_R*(S_R - un_R)) / \
          (rho_L*(S_L - un_L) - rho_R*(S_R - un_R))

    # HLLC fluxes
    # First compute the fluxes
    # Mass flux
    mass_flux_L = rho_L * un_L
    mass_flux_R = rho_R * un_R

    # Momentum flux
    momentum_flux_L = rho_L * uvw_L * un_L + p_L * face_normal
    momentum_flux_R = rho_R * uvw_R * un_R + p_R * face_normal

    # Energy flux
    energy_flux_L = (rho_L * E_L + p_L) * un_L
    energy_flux_R = (rho_R * E_R + p_R) * un_R

    # Select appropriate flux based on wave speeds
    # Case 1: S_L >= 0 (supersonic flow to the right)
    cond1 = S_L >= 0

    # Case 2: S_L < 0 and S_M >= 0 (subsonic, left of contact)
    cond2 = (S_L < 0) & (S_M >= 0)

    # Case 3: S_M < 0 and S_R > 0 (subsonic, right of contact)
    cond3 = (S_M < 0) & (S_R > 0)

    # Case 4: S_R <= 0 (supersonic flow to the left)
    cond4 = S_R <= 0

    # Star region quantities (left)
    p_star_L = rho_L * (un_L - S_L) * (un_L - S_M) + p_L
    rho_star_L = rho_L * (S_L - un_L) / (S_L - S_M)

    # Star region quantities (right)
    p_star_R = rho_R * (un_R - S_R) * (un_R - S_M) + p_R
    rho_star_R = rho_R * (S_R - un_R) / (S_R - S_M)

    # Compute star fluxes
    mass_flux_star_L = mass_flux_L + S_L * (rho_star_L - rho_L)

    uvw_star_L = uvw_L + (S_M - un_L) * face_normal
    momentum_flux_star_L = momentum_flux_L + S_L * (rho_star_L * uvw_star_L - rho_L * uvw_L)

    E_star_L = E_L + (S_M - un_L) * (S_M + p_L/(rho_L*(S_L - un_L)))
    energy_flux_star_L = energy_flux_L + S_L * (rho_star_L * E_star_L - rho_L * E_L)

    mass_flux_star_R = mass_flux_R + S_R * (rho_star_R - rho_R)

    uvw_star_R = uvw_R + (S_M - un_R) * face_normal
    momentum_flux_star_R = momentum_flux_R + S_R * (rho_star_R * uvw_star_R - rho_R * uvw_R)

    E_star_R = E_R + (S_M - un_R) * (S_M + p_R/(rho_R*(S_R - un_R)))
    energy_flux_star_R = energy_flux_R + S_R * (rho_star_R * E_star_R - rho_R * E_R)

    # Select flux based on conditions
    mass_flux = torch.where(cond1, mass_flux_L,
                torch.where(cond2, mass_flux_star_L,
                torch.where(cond3, mass_flux_star_R, mass_flux_R)))

    momentum_flux = torch.where(cond1, momentum_flux_L,
                    torch.where(cond2, momentum_flux_star_L,
                    torch.where(cond3, momentum_flux_star_R, momentum_flux_R)))

    energy_flux = torch.where(cond1, energy_flux_L,
                  torch.where(cond2, energy_flux_star_L,
                  torch.where(cond3, energy_flux_star_R, energy_flux_R)))

    return mass_flux, momentum_flux, energy_flux
