"""
Boundary condition implementations for density field (rho).
"""

import torch
from typing import Optional, Union

def fixedvalue(rho_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               value: Optional[Union[float, dict]] = None,
               value_type: str = "uniform",
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply fixed value boundary condition for density.

    Args:
        rho_cpd_cell: Density field tensor [N_cells, 1]
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        value: Fixed density value
        value_type: Type of value specification
        neighbor_cell: Neighbor cell connectivity [2, N_faces]
        **kwargs: Additional parameters
    """
    if value is None:
        raise ValueError("Fixed value boundary condition requires 'value' parameter")

    # Extract value if it's in dictionary format
    if isinstance(value, dict):
        rho_value = value.get('val', 1.0)  # Default to 1.0 kg/m³
    else:
        rho_value = float(value)

    # Convert to tensor
    value_tensor = torch.tensor(rho_value, dtype=rho_cpd_cell.dtype, device=rho_cpd_cell.device)

    # Apply to boundary cells
    if neighbor_cell is not None:
        boundary_cells = neighbor_cell[1, start_idx:end_idx]
        rho_cpd_cell[boundary_cells, 0] = value_tensor
    else:
        rho_cpd_cell[start_idx:end_idx, 0] = value_tensor

    return rho_cpd_cell

def zerogradient(rho_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                 neighbor_cell: Optional[torch.Tensor] = None,
                 **kwargs) -> torch.Tensor:
    """
    Apply zero gradient boundary condition for density.

    Args:
        rho_cpd_cell: Density field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if neighbor_cell is not None:
        owner_cells = neighbor_cell[0, start_idx:end_idx]
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Set boundary cell values equal to owner cell values
        rho_cpd_cell[boundary_cells] = rho_cpd_cell[owner_cells]

    return rho_cpd_cell

def freestream(rho_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               freestreamValue: Optional[float] = None,
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply freestream boundary condition for density.

    Args:
        rho_cpd_cell: Density field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        freestreamValue: Freestream density value
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if freestreamValue is not None:
        return fixedvalue(rho_cpd_cell, start_idx, end_idx, value=freestreamValue,
                         neighbor_cell=neighbor_cell, **kwargs)
    else:
        return zerogradient(rho_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

def calculated(rho_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               p: Optional[torch.Tensor] = None,
               T: Optional[torch.Tensor] = None,
               R: float = 287.0,  # Specific gas constant for air
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply calculated boundary condition for density.
    For ideal gas: rho = p / (R * T)

    Args:
        rho_cpd_cell: Density field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        p: Pressure field tensor
        T: Temperature field tensor
        R: Specific gas constant
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if neighbor_cell is not None and p is not None and T is not None:
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Calculate density using ideal gas law
        p_boundary = p[boundary_cells]
        T_boundary = T[boundary_cells]

        # Avoid division by zero
        T_boundary = torch.clamp(T_boundary, min=1e-6)

        rho_cpd_cell[boundary_cells] = p_boundary / (R * T_boundary)
    else:
        # Fallback to zero gradient
        return zerogradient(rho_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

    return rho_cpd_cell

def inletoutlet(rho_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                inletValue: Optional[Union[float, dict]] = None,
                neighbor_cell: Optional[torch.Tensor] = None,
                **kwargs) -> torch.Tensor:
    """
    Apply inlet-outlet boundary condition for density.
    Acts as inlet when flow is inward, outlet when flow is outward.

    Args:
        rho_cpd_cell: Density field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        inletValue: Inlet density value
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    # For now, implement as fixed value with inlet value
    if inletValue is not None:
        return fixedvalue(rho_cpd_cell, start_idx, end_idx, value=inletValue,
                         neighbor_cell=neighbor_cell, **kwargs)
    else:
        # Default to zero gradient if no inlet value specified
        return zerogradient(rho_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

def waveTransmissive(rho_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                     neighbor_cell: Optional[torch.Tensor] = None,
                     **kwargs) -> torch.Tensor:
    """
    Apply wave transmissive boundary condition for density.
    This is typically used for outflow boundaries in compressible flow.

    Args:
        rho_cpd_cell: Density field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    # For now, implement as zero gradient
    return zerogradient(rho_cpd_cell, start_idx, end_idx,
                       neighbor_cell=neighbor_cell, **kwargs)

# Aliases for common boundary condition names
fixedValue = fixedvalue
zeroGradient = zerogradient
inletOutlet = inletoutlet