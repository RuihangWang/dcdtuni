"""JST central scheme with artificial dissipation, and its sensor/spectral
radius/undivided-Laplacian helpers (the artificial-dissipation machinery the
scheme needs)."""
import torch
from torch_geometric.utils import scatter

from .._topology import create_two_way_boundary_mask


def jst_flux(rho_L, uvw_L, p_L, rho_R, uvw_R, p_R,
             face_normal, sensor_L, sensor_R, laplacian_U_L, laplacian_U_R,
             num_neighbors_L, num_neighbors_R,
             lambda_L=None, lambda_R=None, stretch_param=0.3,
             kappa_2=0.5, kappa_4=0.02, gamma=1.4, R=287.0,
             face_area=None):
    """
    JST (Jameson-Schmidt-Turkel) central scheme with artificial dissipation.

    This implementation follows SU2's JST scheme exactly:
    - Central flux: Flux of Averages (not Average of Fluxes)
    - Dissipation: D = λ * [ε²·ΔU - ε⁴·Δ(∇²U)]
    - Total flux: F_JST = F_central + D  (dissipation is ADDED, not subtracted)
    - Spectral radius correction for stretched meshes (correctedSpectralRadius)

    Key differences from naive implementation (matching SU2):
    1. Central flux uses "Flux of Averages" not "Average of Fluxes"
    2. Energy diffU uses enthalpy: (ρH_L - ρH_R), not (ρE_L - ρE_R)
    3. eps4 = max(0, kappa4 - eps2) * sc4, NOT max(0, kappa4*sc4 - eps2)
    4. Sensor uses average: 0.5*(s_L + s_R), not max(s_L, s_R)
    5. No 0.5 factor in front of dissipation
    6. correctedSpectralRadius for stretched meshes (O-grid, etc.)

    Reference: SU2 source code
    - SU2_CFD/include/numerics_simd/flow/convection/centered.hpp (CJSTScheme)
    - SU2_CFD/include/numerics_simd/flow/convection/common.hpp (correctedSpectralRadius)
    - SU2_CFD/src/solvers/CEulerSolver.cpp (Undivided Laplacian)

    Args:
        rho_L, rho_R: Density on left and right sides [N_faces, 1]
        uvw_L, uvw_R: Velocity vectors on left and right sides [N_faces, 3]
        p_L, p_R: Pressure on left and right sides [N_faces, 1]
        face_normal: Unit normal vector pointing from left to right [N_faces, 3]
        sensor_L, sensor_R: Pressure sensor values at cells [N_faces, 1]
        laplacian_U_L, laplacian_U_R: Undivided Laplacian [N_faces, 5]
            Note: Energy component (index 4) should be ρH Laplacian (consistent with diffU)!
        num_neighbors_L, num_neighbors_R: Number of neighbors for each cell [N_faces, 1]
        lambda_L, lambda_R: Cell-based spectral radius [N_faces, 1] (optional, for correctedSpectralRadius)
            If None, skip the stretch correction. Note: these should include area factor!
        stretch_param: Stretch parameter for correctedSpectralRadius (default 0.3, same as SU2)
        kappa_2: Second-order dissipation coefficient (default 0.5, same as SU2)
        kappa_4: Fourth-order dissipation coefficient (default 0.02, same as SU2)
        gamma: Specific heat ratio (default 1.4 for air)
        R: Gas constant [J/(kg·K)] (default 287.0 for air)
        face_area: Face area [N_faces, 1] (required for correctedSpectralRadius consistency)

    Returns:
        Tuple of fluxes: (mass_flux, momentum_flux, energy_flux)
    """
    # ========== STEP 1: Compute Averaged Primitive Variables ==========
    # SU2 uses "Flux of Averages": compute average primitives first, then flux
    rho_avg = 0.5 * (rho_L + rho_R)
    uvw_avg = 0.5 * (uvw_L + uvw_R)
    p_avg = 0.5 * (p_L + p_R)

    # Safe density for division
    rho_avg_safe = torch.clamp(torch.abs(rho_avg), min=1e-12)
    rho_L_safe = torch.clamp(torch.abs(rho_L), min=1e-12)
    rho_R_safe = torch.clamp(torch.abs(rho_R), min=1e-12)

    # ========== STEP 2: Compute Central Flux (Flux of Averages) ==========
    # Following SU2's inviscidProjFlux:
    # - Mass flux: mass_flux_central = ρ_avg * (u_avg · n)
    # - Momentum flux: mass_flux_central * u_avg + p_avg * n
    # - Energy flux: mass_flux_central * H_avg

    # Projected velocity (average)
    un_avg = torch.sum(uvw_avg * face_normal, dim=-1, keepdim=True)

    # Mass flux: ρ_avg * (u_avg · n)
    mass_flux_central = rho_avg * un_avg

    # Central fluxes
    momentum_flux_central = mass_flux_central * uvw_avg + p_avg * face_normal

    # Enthalpy for energy flux: H = E + p/ρ = e + 0.5*|u|^2 + p/ρ = γ/(γ-1) * p/ρ + 0.5*|u|^2
    e_avg = p_avg / ((gamma - 1.0) * rho_avg_safe)
    E_avg = e_avg + 0.5 * torch.sum(uvw_avg**2, dim=-1, keepdim=True)
    H_avg = E_avg + p_avg / rho_avg_safe  # H = E + p/ρ

    energy_flux_central = mass_flux_central * H_avg

    # ========== STEP 3: Compute Spectral Radius ==========
    # SU2: lambda = abs(projVel) + avgV.speedSound()*area
    # where projVel = u · normal_with_area = S * (u·n)
    # So lambda = S * (|u·n| + c) includes area factor
    # Clamp p_avg to avoid NaN from sqrt of negative (can happen during L-BFGS line search)
    p_avg_safe = torch.clamp(p_avg, min=1e-12)
    a_avg = torch.sqrt(gamma * p_avg_safe / rho_avg_safe)

    # Base spectral radius (no area) to avoid accidental area^2 when flux is later multiplied by face_area
    lambda_base = torch.abs(un_avg) + a_avg

    # Area-weighted version (needed for correctedSpectralRadius)
    lambda_area = lambda_base * face_area if face_area is not None else lambda_base

    # Apply correctedSpectralRadius for stretched meshes (SU2: common.hpp:418-432)
    # This correction balances dissipation across mesh stretching (e.g., O-grid)
    # Formula: lambda_corrected = 4*phi_i*phi_j / (phi_i + phi_j) * avgLambda
    # where phi_i = (0.25 * lambda_i / avgLambda)^stretch_param
    if lambda_L is not None and lambda_R is not None and face_area is not None:
        # Clamp lambda values to avoid division by zero
        lambda_area_safe = torch.clamp(lambda_area, min=1e-12)
        lambda_L_safe = torch.clamp(lambda_L, min=1e-12)
        lambda_R_safe = torch.clamp(lambda_R, min=1e-12)

        # Compute phi values (SU2: phi = pow(0.25*lambda/avgLambda, stretchParam))
        phi_L = torch.pow(0.25 * lambda_L_safe / lambda_area_safe, stretch_param)
        phi_R = torch.pow(0.25 * lambda_R_safe / lambda_area_safe, stretch_param)

        # Harmonic-like correction: 4*phi_i*phi_j / (phi_i + phi_j) * avgLambda
        phi_sum = phi_L + phi_R
        phi_sum_safe = torch.clamp(phi_sum, min=1e-12)
        lambda_corr_area = 4.0 * phi_L * phi_R / phi_sum_safe * lambda_area

        # Convert back to non-area form because flux will be multiplied by face_area later
        face_area_safe = torch.clamp(face_area, min=1e-12)
        lambda_ij = lambda_corr_area / face_area_safe
    else:
        # No stretch correction or missing face area: use non-area spectral radius directly
        lambda_ij = lambda_base

    # ========== STEP 4: Compute Dissipation Coefficients ==========
    # SU2 formula: sc2 = 3*(n_i + n_j)/(n_i * n_j), sc4 = sc2^2/4
    denom = num_neighbors_L * num_neighbors_R
    denom = torch.clamp(denom, min=1.0)
    sc2 = 3.0 * (num_neighbors_L + num_neighbors_R) / denom
    sc4 = 0.25 * sc2 * sc2

    # SU2: eps2 = kappa2 * 0.5*(s_i + s_j) * sc2 (uses AVERAGE, not max!)
    # CRITICAL FIX: SU2 uses average of sensors, not maximum
    # Reference: centered.hpp:247 - const Double eps2 = kappa2 * 0.5*(si+sj) * sc2;
    # Using max would over-activate dissipation and smear shocks incorrectly
    sensor_avg = 0.5 * (sensor_L + sensor_R)
    eps_2 = kappa_2 * sensor_avg * sc2

    # SU2: eps4 = max(0, kappa4 - eps2) * sc4 (NOT kappa4*sc4 - eps2!)
    eps_4 = torch.maximum(torch.zeros_like(eps_2), kappa_4 - eps_2) * sc4

    # ========== STEP 5: Compute Conservative Variable Differences ==========
    # For mass, momentum: diffU = U_L - U_R (SU2 convention: i - j)
    # For energy: use ENTHALPY difference! diffU[4] = ρH_L - ρH_R

    # Left state
    e_L = p_L / ((gamma - 1.0) * rho_L_safe)
    E_L = e_L + 0.5 * torch.sum(uvw_L**2, dim=-1, keepdim=True)
    H_L = E_L + p_L / rho_L_safe
    rhoH_L = rho_L * H_L

    # Right state
    e_R = p_R / ((gamma - 1.0) * rho_R_safe)
    E_R = e_R + 0.5 * torch.sum(uvw_R**2, dim=-1, keepdim=True)
    H_R = E_R + p_R / rho_R_safe
    rhoH_R = rho_R * H_R

    # Conservative variable difference (SU2: U_i - U_j, where i=L, j=R)
    # Note: For energy, SU2 uses enthalpy: ρH_i - ρH_j
    diff_rho = rho_L - rho_R
    diff_rhoU = rho_L * uvw_L - rho_R * uvw_R
    diff_rhoH = rhoH_L - rhoH_R  # Energy uses ENTHALPY!

    diffU = torch.cat([diff_rho, diff_rhoU, diff_rhoH], dim=-1)  # [..., N_faces, 5]

    # Laplacian difference (SU2: lapl_i - lapl_j)
    # Note: Energy Laplacian uses ρH (consistent with diffU which uses ρH_i - ρH_j)
    diff_laplacian = laplacian_U_L - laplacian_U_R

    # ========== STEP 6: Compute Dissipation and Total Flux ==========
    # SU2: flux += (eps2*diffU - eps4*diff_laplacian) * lambda
    # Note: NO 0.5 factor! And dissipation is ADDED to central flux
    dissipation = lambda_ij * (eps_2 * diffU - eps_4 * diff_laplacian)

    # Total flux = central + dissipation
    mass_flux = mass_flux_central + dissipation[..., 0:1]
    momentum_flux = momentum_flux_central + dissipation[..., 1:4]
    energy_flux = energy_flux_central + dissipation[..., 4:5]

    # (Debug prints removed for production training)

    return mass_flux, momentum_flux, energy_flux


def compute_pressure_sensor(p_cpd_cell: torch.Tensor, *, neighbor_cpd_cell, mask_boundary_cell, num_cpd_cells, mask_non_empty_face=None) -> torch.Tensor:
    """
    Compute Jameson's pressure sensor for JST scheme (PyG two-way edges).

    The pressure sensor detects shocks and activates second-order dissipation.
    Matches SU2's centered sensor implementation:
        ν_i = | Σ_j(p_j - p_i) | / Σ_j(p_j + p_i)
    Boundary cells only use boundary neighbors (skip interior contributions).

    Args:
        p_cpd_cell: Pressure at all cpd cells [N_cpd_cells, 1]

    Returns:
        sensor: Pressure sensor values [N_cpd_cells, 1]
    """
    # Get valid edges
    mask_valid_face = mask_non_empty_face
    if mask_valid_face is None:
        mask_valid_face = torch.ones_like(neighbor_cpd_cell[0], dtype=torch.bool)

    sources = neighbor_cpd_cell[0][mask_valid_face]  # [N_valid_faces]
    targets = neighbor_cpd_cell[1][mask_valid_face]  # [N_valid_faces]

    # Create two-way edges (PyG style)
    two_way_sources = torch.cat([sources, targets], dim=0)  # [2*N_edges]
    two_way_targets = torch.cat([targets, sources], dim=0)  # [2*N_edges]

    # Create boundary mask for two-way edges
    two_way_keep = create_two_way_boundary_mask(
        mask_boundary_cell, sources, targets
    )  # [2*N_edges]

    # Get pressure at target cells (neighbors)
    p_targets = p_cpd_cell.index_select(-2, two_way_targets)  # [..., 2*N_edges, 1]
    p_sources = p_cpd_cell.index_select(-2, two_way_sources)  # [..., 2*N_edges, 1]

    # Accumulate neighbor pressures
    sum_p_neighbors = scatter(
        p_targets * two_way_keep.unsqueeze(-1),
        two_way_sources,
        dim=-2,
        dim_size=num_cpd_cells,
        reduce='sum',
    )  # [N_cpd_cells, 1]

    # Count valid neighbors
    ones = torch.ones_like(p_targets)
    valid_neighbor_count = scatter(
        ones * two_way_keep.unsqueeze(-1),
        two_way_sources,
        dim=-2,
        dim_size=num_cpd_cells,
        reduce='sum',
    )  # [N_cpd_cells, 1]

    # Numerator = |sum(p_neighbors) - n_neighbors * p_i|
    numerator = torch.abs(sum_p_neighbors - valid_neighbor_count * p_cpd_cell)

    # Compute denominator: Σ_j(p_j + p_i)
    # For each edge, add both p_target and p_source
    sum_for_denominator = scatter(
        (p_targets + p_sources) * two_way_keep.unsqueeze(-1),
        two_way_sources,
        dim=-2,
        dim_size=num_cpd_cells,
        reduce='sum',
    )  # [N_cpd_cells, 1]
    denominator = sum_for_denominator.clamp(min=1e-12)

    # Compute sensor: ν = numerator / denominator
    sensor = numerator / denominator

    # if not hasattr(self, "_debug_sensor_logged"):
    #     print(f"\n[DEBUG Sensor] Pressure sensor computation:")
    #     print(f"  p_cpd_cell min/max/mean: {p_cpd_cell.min().item():.6e}/{p_cpd_cell.max().item():.6e}/{p_cpd_cell.mean().item():.6e}")
    #     print(f"  numerator min/max/mean: {numerator.min().item():.6e}/{numerator.max().item():.6e}/{numerator.mean().item():.6e}")
    #     print(f"  denominator min/max/mean: {denominator.min().item():.6e}/{denominator.max().item():.6e}/{denominator.mean().item():.6e}")
    #     print(f"  sensor min/max/mean: {sensor.min().item():.6e}/{sensor.max().item():.6e}/{sensor.mean().item():.6e}")
    #     print(f"  valid_neighbor_count min/max/mean: {valid_neighbor_count.min().item():.1f}/{valid_neighbor_count.max().item():.1f}/{valid_neighbor_count.mean().item():.1f}")
    #     self._debug_sensor_logged = True

    return sensor


def compute_undivided_laplacian(U_cpd_cell: torch.Tensor, rhoH_cpd_cell: torch.Tensor = None, *, neighbor_cpd_cell, mask_boundary_cell, num_cpd_cells, mask_non_empty_face=None) -> torch.Tensor:
    """
    Compute undivided Laplacian for JST fourth-order dissipation (PyG two-way edges).

    Following SU2's implementation (centered.hpp):
    - For mass and momentum: ∇²U_i = Σ_j (U_j - U_i)
    - For energy: ∇²(ρH)_i = Σ_j ((ρH)_j - (ρH)_i)

    IMPORTANT: The energy Laplacian uses ρH (density × enthalpy), NOT pressure!
    This is consistent with the diffU computation in jst_flux() which uses:
        diffU[energy] = ρH_L - ρH_R

    Reference: SU2 centered.hpp lines 131-134:
        diffU(nVar-1) = V.i.density()*V.i.enthalpy() - V.j.density()*V.j.enthalpy();

    Args:
        U_cpd_cell: Conservative variables at all cpd cells [N_cpd_cells, 5]
                    Contains [ρ, ρu, ρv, ρw, ρE]
        rhoH_cpd_cell: ρH (density × enthalpy) at all cpd cells [N_cpd_cells, 1]
                       If provided, used for energy Laplacian (consistent with diffU)
                       If None, uses ρE from U_cpd_cell (standard conservative form)

    Returns:
        laplacian: Undivided Laplacian [N_cpd_cells, 5]
                   Last component is ρH Laplacian if rhoH_cpd_cell provided
    """
    # Get valid edges
    mask_valid_face = mask_non_empty_face
    if mask_valid_face is None:
        mask_valid_face = torch.ones_like(neighbor_cpd_cell[0], dtype=torch.bool)

    sources = neighbor_cpd_cell[0][mask_valid_face]  # [N_valid_faces]
    targets = neighbor_cpd_cell[1][mask_valid_face]  # [N_valid_faces]

    # Get conservative variables at source and target cells
    U_sources = U_cpd_cell.index_select(-2, sources)  # [..., N_valid_faces, 5]
    U_targets = U_cpd_cell.index_select(-2, targets)  # [..., N_valid_faces, 5]

    # Compute forward differences: U_target - U_source
    delta_U_forward = U_targets - U_sources  # [N_valid_faces, 5]

    # Handle ρH for energy equation
    if rhoH_cpd_cell is not None:
        rhoH_sources = rhoH_cpd_cell.index_select(-2, sources)
        rhoH_targets = rhoH_cpd_cell.index_select(-2, targets)
        delta_rhoH_forward = rhoH_targets - rhoH_sources
        delta_U_forward = torch.cat(
            (delta_U_forward[..., :4], delta_rhoH_forward), dim=-1
        )  # Replace energy component without an autograd-hostile in-place write

    # Create two-way edges (PyG style)
    # For backward edges, delta is negated: U_source - U_target = -delta_U_forward
    two_way_sources = torch.cat([sources, targets], dim=0)
    two_way_delta_U = torch.cat([delta_U_forward, -delta_U_forward], dim=-2)

    # Create boundary mask for two-way edges
    two_way_keep = create_two_way_boundary_mask(
        mask_boundary_cell, sources, targets
    )

    # Accumulate: ∇²U_i = Σ_j (U_j - U_i)
    laplacian = scatter(
        two_way_delta_U * two_way_keep.unsqueeze(-1),
        two_way_sources,
        dim=-2,
        dim_size=num_cpd_cells,
        reduce='sum',
    )  # [N_cpd_cells, 5]

    return laplacian


def compute_cell_lambda(rho_cpd_cell: torch.Tensor, uvw_cpd_cell: torch.Tensor, p_cpd_cell: torch.Tensor, *, neighbor_cpd_cell, num_cpd_cells, face_area, unv_face, sound_speed, mask_non_empty_face=None) -> torch.Tensor:
    """
    Compute cell-based maximum eigenvalue (lambda) for JST correctedSpectralRadius (PyG two-way edges).

    Following SU2's implementation (CFVMFlowSolverBase.hpp:632-685):
    For each cell, lambda_i = Σ_faces (|u·n| + c) * Area, using arithmetic mean of
    projected velocity and speed of sound across the face. No boundary filtering.

    This is used in correctedSpectralRadius to handle stretched meshes:
    - Cells with many large faces get higher lambda
    - The correction balances dissipation across mesh stretching

    Reference: SU2 centered.hpp (common.hpp:418-432)

    Args:
        rho_cpd_cell: Density at all cpd cells [N_cpd_cells, 1]
        uvw_cpd_cell: Velocity at all cpd cells [N_cpd_cells, 3]
        p_cpd_cell: Pressure at all cpd cells [N_cpd_cells, 1]

    Returns:
        lambda_cell: Maximum eigenvalue for each cell [N_cpd_cells, 1]
    """
    # Get valid edges
    mask_valid_face = mask_non_empty_face
    if mask_valid_face is None:
        mask_valid_face = torch.ones_like(neighbor_cpd_cell[0], dtype=torch.bool)

    sources = neighbor_cpd_cell[0][mask_valid_face]
    targets = neighbor_cpd_cell[1][mask_valid_face]

    # Get face normals and areas
    face_normal = unv_face[mask_valid_face]  # Unit normals [N_valid_faces, 3]
    face_area = face_area[mask_valid_face]   # Face areas [N_valid_faces, 1]

    # Densities, pressures, velocities at source/target
    rho_sources = rho_cpd_cell.index_select(-2, sources)
    rho_targets = rho_cpd_cell.index_select(-2, targets)
    p_sources = p_cpd_cell.index_select(-2, sources)
    p_targets = p_cpd_cell.index_select(-2, targets)
    uvw_sources = uvw_cpd_cell.index_select(-2, sources)
    uvw_targets = uvw_cpd_cell.index_select(-2, targets)

    # Projected velocities on face normal (unit normal)
    un_sources = torch.sum(uvw_sources * face_normal, dim=-1, keepdim=True)
    un_targets = torch.sum(uvw_targets * face_normal, dim=-1, keepdim=True)
    un_mean = 0.5 * (un_sources + un_targets)

    # Speed of sound: arithmetic mean of c_i, c_j (matches SU2 SoundSpeed functor)
    c_sources = sound_speed(p_sources, rho_sources)
    c_targets = sound_speed(p_targets, rho_targets)
    c_mean = 0.5 * (c_sources + c_targets)

    # Face spectral radius (includes area)
    lambda_face = (torch.abs(un_mean) + c_mean) * face_area  # [N_valid_faces, 1]

    # Accumulate to owner and neighbor (no boundary filtering, consistent with SU2)
    two_way_sources = torch.cat([sources, targets], dim=0)
    two_way_lambda = torch.cat([lambda_face, lambda_face], dim=-2)

    lambda_cell = scatter(two_way_lambda, two_way_sources, dim=-2, dim_size=num_cpd_cells, reduce='sum')

    return lambda_cell
