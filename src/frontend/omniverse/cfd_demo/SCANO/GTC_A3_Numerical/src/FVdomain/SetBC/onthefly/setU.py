
"""
Boundary condition implementations for velocity field (U).
"""

import torch
from typing import Optional, Union, List

def fixedvalue(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               value: Optional[Union[float, List[float]]] = None,
               value_type: str = "uniform",
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply fixed value boundary condition for velocity.

    Args:
        U_cpd_cell: Velocity field tensor [N_cells, 3]
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        value: Fixed velocity value
        value_type: Type of value specification
        neighbor_cell: Neighbor cell connectivity [2, N_faces]
        **kwargs: Additional parameters
    """
    if value is None:
        raise ValueError("Fixed value boundary condition requires 'value' parameter")

    # Convert value to tensor
    if isinstance(value, (int, float)):
        value_tensor = torch.tensor([value, 0.0, 0.0], dtype=U_cpd_cell.dtype, device=U_cpd_cell.device)
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        value_tensor = torch.tensor(value, dtype=U_cpd_cell.dtype, device=U_cpd_cell.device)
    else:
        raise ValueError(f"Invalid value format for velocity: {value}")

    # Apply to boundary cells (ghost cells)
    if neighbor_cell is not None:
        # Get boundary cell indices (neighbor cells for boundary faces)
        boundary_cells = neighbor_cell[1, start_idx:end_idx]
        U_cpd_cell[boundary_cells] = value_tensor
    else:
        # Fallback: apply to cell range directly
        U_cpd_cell[start_idx:end_idx] = value_tensor

    return U_cpd_cell

def uniformfixedvalue(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                      uniformValue: Optional[dict] = None,
                      neighbor_cell: Optional[torch.Tensor] = None,
                      **kwargs) -> torch.Tensor:
    """
    Apply uniform fixed value boundary condition for velocity.

    Args:
        U_cpd_cell: Velocity field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        uniformValue: Dictionary containing uniform value specification
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if uniformValue is None:
        raise ValueError("Uniform fixed value boundary condition requires 'uniformValue' parameter")

    # Extract value from uniformValue dictionary
    value = uniformValue.get('val', [0.0, 0.0, 0.0])

    return fixedvalue(U_cpd_cell, start_idx, end_idx, value=value,
                     neighbor_cell=neighbor_cell, **kwargs)

def zerogradient(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                 neighbor_cell: Optional[torch.Tensor] = None,
                 **kwargs) -> torch.Tensor:
    """
    Apply zero gradient boundary condition for velocity.
    For zero gradient, boundary cell values equal interior cell values.

    Args:
        U_cpd_cell: Velocity field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        neighbor_cell: Neighbor cell connectivity [2, N_faces]
        **kwargs: Additional parameters
    """
    if neighbor_cell is not None:
        # Get owner and neighbor cell indices for boundary faces
        owner_cells = neighbor_cell[0, start_idx:end_idx]
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Set boundary cell values equal to owner cell values (zero gradient)
        U_cpd_cell[boundary_cells] = U_cpd_cell[owner_cells]

    return U_cpd_cell

def slip(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
         face_normal: Optional[torch.Tensor] = None,
         neighbor_cell: Optional[torch.Tensor] = None,
         **kwargs) -> torch.Tensor:
    """
    Apply slip boundary condition for velocity.
    Normal component is zero, tangential components are free.

    Args:
        U_cpd_cell: Velocity field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        face_normal: Face normal vectors [N_faces, 3]
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if neighbor_cell is not None and face_normal is not None:
        owner_cells = neighbor_cell[0, start_idx:end_idx]
        boundary_cells = neighbor_cell[1, start_idx:end_idx]

        # Get face normals for this boundary
        normals = face_normal[start_idx:end_idx]

        # Get owner cell velocities
        U_owner = U_cpd_cell[owner_cells]

        # Calculate normal component
        U_normal = torch.sum(U_owner * normals, dim=1, keepdim=True) * normals

        # Set boundary velocity to tangential component only (remove normal component)
        U_cpd_cell[boundary_cells] = U_owner - 2 * U_normal

    return U_cpd_cell

def noslip(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
           neighbor_cell: Optional[torch.Tensor] = None,
           **kwargs) -> torch.Tensor:
    """
    Apply no-slip boundary condition for velocity (wall boundary).
    Sets velocity to zero at the boundary.

    Args:
        U_cpd_cell: Velocity field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if neighbor_cell is not None:
        # Get boundary cell indices
        boundary_cells = neighbor_cell[1, start_idx:end_idx]
        U_cpd_cell[boundary_cells, :] = 0.0
    else:
        # Fallback: apply to cell range directly
        U_cpd_cell[start_idx:end_idx, :] = 0.0

    return U_cpd_cell

def inletoutlet(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                inletValue: Optional[Union[float, List[float]]] = None,
                neighbor_cell: Optional[torch.Tensor] = None,
                **kwargs) -> torch.Tensor:
    """
    Apply inlet-outlet boundary condition for velocity.
    Acts as inlet when flow is inward, outlet when flow is outward.

    Args:
        U_cpd_cell: Velocity field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        inletValue: Inlet velocity value
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    # For now, implement as fixed value with inlet value
    if inletValue is not None:
        return fixedvalue(U_cpd_cell, start_idx, end_idx, value=inletValue,
                         neighbor_cell=neighbor_cell, **kwargs)
    else:
        # Default to zero gradient if no inlet value specified
        return zerogradient(U_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

def freestream(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
               freestreamValue: Optional[Union[float, List[float]]] = None,
               neighbor_cell: Optional[torch.Tensor] = None,
               **kwargs) -> torch.Tensor:
    """
    Apply freestream boundary condition for velocity.

    Args:
        U_cpd_cell: Velocity field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        freestreamValue: Freestream velocity value
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if freestreamValue is not None:
        return fixedvalue(U_cpd_cell, start_idx, end_idx, value=freestreamValue,
                         neighbor_cell=neighbor_cell, **kwargs)
    else:
        return zerogradient(U_cpd_cell, start_idx, end_idx,
                           neighbor_cell=neighbor_cell, **kwargs)

def flowrateinletvelocity(U_cpd_cell: torch.Tensor, start_idx: int, end_idx: int,
                          volumetricFlowRate: Optional[float] = None,
                          massFlowRate: Optional[float] = None,
                          face_area: Optional[torch.Tensor] = None,
                          face_normal: Optional[torch.Tensor] = None,
                          rho: float = 1.0,
                          neighbor_cell: Optional[torch.Tensor] = None,
                          **kwargs) -> torch.Tensor:
    """
    Apply flow rate inlet velocity boundary condition.

    Args:
        U_cpd_cell: Velocity field tensor
        start_idx: Start index for boundary faces
        end_idx: End index for boundary faces
        volumetricFlowRate: Volumetric flow rate
        massFlowRate: Mass flow rate
        face_area: Face area tensor [N_faces]
        face_normal: Face normal vectors [N_faces, 3]
        rho: Density
        neighbor_cell: Neighbor cell connectivity
        **kwargs: Additional parameters
    """
    if face_area is None or face_normal is None:
        raise ValueError("Flow rate inlet velocity requires face_area and face_normal")

    # Calculate total area of inlet faces
    total_area = torch.sum(face_area[start_idx:end_idx])

    if volumetricFlowRate is not None:
        # Calculate velocity magnitude from volumetric flow rate
        velocity_magnitude = volumetricFlowRate / total_area
    elif massFlowRate is not None:
        # Calculate velocity magnitude from mass flow rate
        velocity_magnitude = massFlowRate / (rho * total_area)
    else:
        raise ValueError("Either volumetricFlowRate or massFlowRate must be specified")

    # Get face normals for inlet faces (assuming inward normal)
    normals = face_normal[start_idx:end_idx]

    # Calculate velocity vector (negative normal for inlet)
    velocity_vector = -velocity_magnitude * normals

    if neighbor_cell is not None:
        boundary_cells = neighbor_cell[1, start_idx:end_idx]
        U_cpd_cell[boundary_cells] = velocity_vector
    else:
        U_cpd_cell[start_idx:end_idx] = velocity_vector

    return U_cpd_cell

# Alias for common boundary condition names
fixedValue = fixedvalue
uniformFixedValue = uniformfixedvalue
zeroGradient = zerogradient
noSlip = noslip