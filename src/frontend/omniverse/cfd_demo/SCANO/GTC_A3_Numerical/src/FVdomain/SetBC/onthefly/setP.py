"""
Boundary condition implementations for pressure field (p).
"""

import torch
from typing import Optional, Union

def fixedvalue(p_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               value: Optional[Union[float, dict]] = None,
               value_type: str = "uniform",
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply fixed value boundary condition for pressure.

    Args:
        p_cpd_cell: Pressure field tensor [N_cells, 1]
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        value: Fixed pressure value
        value_type: Type of value specification
        neighbor_cell: Neighbor cell connectivity [2, N_faces]
        **kwargs: Additional parameters
    """
    if value is None:
        raise ValueError("Fixed value boundary condition requires 'value' parameter")

    # Extract value if it's in dictionary format
    if isinstance(value, dict):
        pressure_value = value.get('val', 0.0)
    else:
        pressure_value = float(value)

    # Convert to tensor
    value_tensor = torch.tensor(pressure_value, dtype=p_cpd_cell.dtype, device=p_cpd_cell.device)

    # Apply to boundary cells
    if neighbor_cell is not None:
        boundary_cells = neighbor_cell[1, start_idx:end_idx]
        p_cpd_cell[boundary_cells, 0] = value_tensor
    else:
        p_cpd_cell[start_idx:end_idx, 0] = value_tensor

    return p_cpd_cell

def zerogradient(p_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                 neighbor_cell: Optional[torch.Tensor] = None,
                 **kwargs) -> torch.Tensor:
    """
    Apply zero gradient boundary condition for pressure.

    Args:
        p_cpd_cell: Pressure field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if neighbor_cell is not None:
        owner_cells = neighbor_cell[0, start_idx:end_idx]
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Set boundary cell values equal to owner cell values
        p_cpd_cell[boundary_cells] = p_cpd_cell[owner_cells]

    return p_cpd_cell

def freestreampressure(p_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                       freestreamValue: Optional[float] = None,
                       neighbor_cell: Optional[torch.Tensor] = None,
                       **kwargs) -> torch.Tensor:
    """
    Apply freestream pressure boundary condition.

    Args:
        p_cpd_cell: Pressure field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        freestreamValue: Freestream pressure value
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if freestreamValue is not None:
        return fixedvalue(p_cpd_cell, start_idx, end_idx, value=freestreamValue,
                         neighbor_cell=neighbor_cell, **kwargs)
    else:
        return zerogradient(p_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

def freestream(p_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               freestreamValue: Optional[float] = None,
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply freestream boundary condition for pressure.

    Args:
        p_cpd_cell: Pressure field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        freestreamValue: Freestream pressure value
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    return freestreampressure(p_cpd_cell, start_idx, end_idx,
                             freestreamValue=freestreamValue,
                             neighbor_cell=neighbor_cell, **kwargs)

def fanpressure(p_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                p0: Optional[Union[float, dict]] = None,
                fanCurveCoeffs: Optional[dict] = None,
                direction: str = "in",
                neighbor_cell: Optional[torch.Tensor] = None,
                **kwargs) -> torch.Tensor:
    """
    Apply fan pressure boundary condition.

    Args:
        p_cpd_cell: Pressure field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        p0: Reference pressure
        fanCurveCoeffs: Fan curve coefficients
        direction: Fan direction ("in" or "out")
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    # For now, implement as fixed value with p0
    if p0 is not None:
        if isinstance(p0, dict):
            pressure_value = p0.get('val', 0.0)
        else:
            pressure_value = float(p0)

        return fixedvalue(p_cpd_cell, start_idx, end_idx, value=pressure_value,
                         neighbor_cell=neighbor_cell, **kwargs)
    else:
        return zerogradient(p_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

def totalpressure(p_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                  p0: Optional[Union[float, dict]] = None,
                  U: Optional[torch.Tensor] = None,
                  rho: float = 1.0,
                  neighbor_cell: Optional[torch.Tensor] = None,
                  **kwargs) -> torch.Tensor:
    """
    Apply total pressure boundary condition.
    p = p0 - 0.5 * rho * |U|^2

    Args:
        p_cpd_cell: Pressure field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        p0: Total pressure
        U: Velocity field tensor
        rho: Density
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if p0 is None:
        return zerogradient(p_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

    # Extract p0 value
    if isinstance(p0, dict):
        p0_value = p0.get('val', 0.0)
    else:
        p0_value = float(p0)

    if neighbor_cell is not None and U is not None:
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Calculate dynamic pressure
        U_boundary = U[boundary_cells]
        U_mag_sq = torch.sum(U_boundary * U_boundary, dim=1, keepdim=True)
        dynamic_pressure = 0.5 * rho * U_mag_sq

        # Set static pressure
        p_cpd_cell[boundary_cells] = p0_value - dynamic_pressure
    else:
        # Fallback to fixed value
        return fixedvalue(p_cpd_cell, start_idx, end_idx, value=p0_value,
                         neighbor_cell=neighbor_cell, **kwargs)

    return p_cpd_cell

# Aliases for common boundary condition names
fixedValue = fixedvalue
zeroGradient = zerogradient
freestreamPressure = freestreampressure
fanPressure = fanpressure
totalPressure = totalpressure