"""
Boundary condition implementations for temperature field (T).
"""

import torch
from typing import Optional, Union

def fixedvalue(T_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               value: Optional[Union[float, dict]] = None,
               value_type: str = "uniform",
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply fixed value boundary condition for temperature.

    Args:
        T_cpd_cell: Temperature field tensor [N_cells, 1]
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        value: Fixed temperature value
        value_type: Type of value specification
        neighbor_cell: Neighbor cell connectivity [2, N_faces]
        **kwargs: Additional parameters
    """
    if value is None:
        raise ValueError("Fixed value boundary condition requires 'value' parameter")

    # Extract value if it's in dictionary format
    if isinstance(value, dict):
        temp_value = value.get('val', 300.0)  # Default to 300K
    else:
        temp_value = float(value)

    # Convert to tensor
    value_tensor = torch.tensor(temp_value, dtype=T_cpd_cell.dtype, device=T_cpd_cell.device)

    # Apply to boundary cells
    if neighbor_cell is not None:
        boundary_cells = neighbor_cell[1, start_idx:end_idx]
        T_cpd_cell[boundary_cells, 0] = value_tensor
    else:
        T_cpd_cell[start_idx:end_idx, 0] = value_tensor

    return T_cpd_cell

def zerogradient(T_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                 neighbor_cell: Optional[torch.Tensor] = None,
                 **kwargs) -> torch.Tensor:
    """
    Apply zero gradient boundary condition for temperature.

    Args:
        T_cpd_cell: Temperature field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if neighbor_cell is not None:
        owner_cells = neighbor_cell[0, start_idx:end_idx]
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Set boundary cell values equal to owner cell values
        T_cpd_cell[boundary_cells] = T_cpd_cell[owner_cells]

    return T_cpd_cell

def freestream(T_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               freestreamValue: Optional[float] = None,
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply freestream boundary condition for temperature.

    Args:
        T_cpd_cell: Temperature field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        freestreamValue: Freestream temperature value
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if freestreamValue is not None:
        return fixedvalue(T_cpd_cell, start_idx, end_idx, value=freestreamValue,
                         neighbor_cell=neighbor_cell, **kwargs)
    else:
        return zerogradient(T_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

def fixedgradient(T_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                  gradient: Optional[Union[float, dict]] = None,
                  face_delta: Optional[torch.Tensor] = None,
                  neighbor_cell: Optional[torch.Tensor] = None,
                  **kwargs) -> torch.Tensor:
    """
    Apply fixed gradient boundary condition for temperature.

    Args:
        T_cpd_cell: Temperature field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        gradient: Fixed gradient value
        face_delta: Distance from cell center to face center
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if gradient is None:
        return zerogradient(T_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

    # Extract gradient value
    if isinstance(gradient, dict):
        grad_value = gradient.get('val', 0.0)
    else:
        grad_value = float(gradient)

    if neighbor_cell is not None and face_delta is not None:
        owner_cells = neighbor_cell[0, start_idx:end_idx]
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Calculate boundary temperature based on gradient
        delta = face_delta[start_idx:end_idx]
        T_cpd_cell[boundary_cells] = T_cpd_cell[owner_cells] + grad_value * delta.unsqueeze(1)
    else:
        # Fallback to zero gradient
        return zerogradient(T_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

    return T_cpd_cell

def convectiveheatflux(T_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                       h: Optional[float] = None,  # Heat transfer coefficient
                       T_inf: Optional[float] = None,  # Ambient temperature
                       neighbor_cell: Optional[torch.Tensor] = None,
                       **kwargs) -> torch.Tensor:
    """
    Apply convective heat flux boundary condition for temperature.
    q = h * (T_wall - T_inf)

    Args:
        T_cpd_cell: Temperature field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        h: Heat transfer coefficient
        T_inf: Ambient temperature
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if h is None or T_inf is None:
        # If parameters not specified, use zero gradient
        return zerogradient(T_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

    # For now, implement as fixed value with T_inf
    # In a full implementation, this would involve solving for the wall temperature
    return fixedvalue(T_cpd_cell, start_idx, end_idx, value=T_inf,
                     neighbor_cell=neighbor_cell, **kwargs)

def adiabaticwall(T_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                  neighbor_cell: Optional[torch.Tensor] = None,
                  **kwargs) -> torch.Tensor:
    """
    Apply adiabatic wall boundary condition for temperature (zero gradient).

    Args:
        T_cpd_cell: Temperature field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    return zerogradient(T_cpd_cell, start_idx, end_idx,
                       neighbor_cell=neighbor_cell, **kwargs)

# Aliases for common boundary condition names
fixedValue = fixedvalue
zeroGradient = zerogradient
fixedGradient = fixedgradient
convectiveHeatFlux = convectiveheatflux
adiabaticWall = adiabaticwall