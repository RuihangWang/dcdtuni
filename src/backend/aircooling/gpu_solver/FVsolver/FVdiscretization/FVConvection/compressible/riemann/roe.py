"""Roe's approximate Riemann solver for the compressible Euler equations."""
import torch


def roe_riemann_flux(rho_L, uvw_L, p_L, rho_R, uvw_R, p_R, face_normal, gamma=1.4):
    """
    Roe's approximate Riemann solver for compressible Euler equations.

    Computes the numerical flux at face centers using Roe's averaging for
    the Euler equations in conservative form.

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
    # R = 287.87  # Gas constant for air [J/(kg·K)]
    # cv = R / (gamma - 1.0)

    # Compute normal velocities
    un_L = torch.sum(uvw_L * face_normal, dim=-1, keepdim=True)
    un_R = torch.sum(uvw_R * face_normal, dim=-1, keepdim=True)

    # Compute specific total enthalpy
    # e = p / ((gamma-1) * rho)
    # H = e + p/rho + 0.5*u^2 = p/((gamma-1)rho) + p/rho + 0.5*u^2
    #   = (p/rho) * (1/(gamma-1) + 1) + 0.5*u^2
    #   = (p/rho) * (gamma/(gamma-1)) + 0.5*u^2
    
    H_L = (gamma * p_L) / ((gamma - 1.0) * rho_L) + 0.5*torch.sum(uvw_L**2, dim=-1, keepdim=True)
    H_R = (gamma * p_R) / ((gamma - 1.0) * rho_R) + 0.5*torch.sum(uvw_R**2, dim=-1, keepdim=True)
    
    # Specific internal energy (needed for conservative variables later)
    e_L = p_L / ((gamma - 1.0) * rho_L)
    e_R = p_R / ((gamma - 1.0) * rho_R)

    # Roe averaging
    sqrt_rho_L = torch.sqrt(rho_L)
    sqrt_rho_R = torch.sqrt(rho_R)
    denom = sqrt_rho_L + sqrt_rho_R

    # Roe-averaged quantities
    rho_avg = sqrt_rho_L * sqrt_rho_R
    uvw_avg = (sqrt_rho_L * uvw_L + sqrt_rho_R * uvw_R) / denom
    H_avg = (sqrt_rho_L * H_L + sqrt_rho_R * H_R) / denom

    # Roe-averaged normal velocity
    un_avg = torch.sum(uvw_avg * face_normal, dim=-1, keepdim=True)

    # Sound speed
    a_avg = torch.sqrt((gamma - 1.0) * (H_avg - 0.5*torch.sum(uvw_avg**2, dim=-1, keepdim=True)))

    # Eigenvalues
    lambda_1 = un_avg - a_avg
    lambda_2 = un_avg
    lambda_3 = un_avg + a_avg

    # Entropy fix (prevent sonic glitches)
    eps = 0.1 * a_avg
    lambda_1 = torch.where(torch.abs(lambda_1) < eps, (lambda_1**2 + eps**2)/(2*eps), torch.abs(lambda_1))
    lambda_2 = torch.where(torch.abs(lambda_2) < eps, (lambda_2**2 + eps**2)/(2*eps), torch.abs(lambda_2))
    lambda_3 = torch.where(torch.abs(lambda_3) < eps, (lambda_3**2 + eps**2)/(2*eps), torch.abs(lambda_3))

    # Differences
    drho = rho_R - rho_L
    duvw = uvw_R - uvw_L
    dp = p_R - p_L
    dun = un_R - un_L

    # Wave strengths
    alpha_1 = (dp - rho_avg * a_avg * dun) / (2.0 * a_avg**2)
    alpha_2 = drho - dp / a_avg**2
    alpha_3 = (dp + rho_avg * a_avg * dun) / (2.0 * a_avg**2)

    # Conservative variables
    rho_E_L = rho_L * (e_L + 0.5*torch.sum(uvw_L**2, dim=-1, keepdim=True))
    rho_E_R = rho_R * (e_R + 0.5*torch.sum(uvw_R**2, dim=-1, keepdim=True))

    # Compute fluxes on left and right
    # Mass flux
    mass_flux_L = rho_L * un_L
    mass_flux_R = rho_R * un_R

    # Momentum flux
    momentum_flux_L = rho_L * uvw_L * un_L + p_L * face_normal
    momentum_flux_R = rho_R * uvw_R * un_R + p_R * face_normal

    # Energy flux
    energy_flux_L = (rho_E_L + p_L) * un_L
    energy_flux_R = (rho_E_R + p_R) * un_R

    # Average fluxes
    mass_flux = 0.5 * (mass_flux_L + mass_flux_R)
    momentum_flux = 0.5 * (momentum_flux_L + momentum_flux_R)
    energy_flux = 0.5 * (energy_flux_L + energy_flux_R)

    # Roe correction
    mass_flux -= 0.5 * (
        alpha_1 * lambda_1 +
        alpha_2 * lambda_2 +
        alpha_3 * lambda_3
    )

    momentum_flux -= 0.5 * (
        alpha_1 * lambda_1 * (uvw_avg - a_avg * face_normal) +
        alpha_2 * lambda_2 * uvw_avg +
        alpha_3 * lambda_3 * (uvw_avg + a_avg * face_normal)
    )

    energy_flux -= 0.5 * (
        alpha_1 * lambda_1 * (H_avg - un_avg * a_avg) +
        alpha_2 * lambda_2 * 0.5 * torch.sum(uvw_avg**2, dim=-1, keepdim=True) +
        alpha_3 * lambda_3 * (H_avg + un_avg * a_avg)
    )

    return mass_flux, momentum_flux, energy_flux
