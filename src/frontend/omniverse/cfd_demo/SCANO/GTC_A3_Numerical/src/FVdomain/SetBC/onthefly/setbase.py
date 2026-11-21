"""
Base module for boundary condition implementations.
This module provides common utilities and base classes for boundary conditions.
"""

import torch
from typing import Optional, Union

class BCBase:
    """
    Base class for boundary condition implementations.
    Provides common functionality for all boundary condition types.
    """

    @staticmethod
    def validate_inputs(field_tensor: torch.Tensor, start_idx: int, end_idx: int) -> None:
        """
        Validate common inputs for boundary condition functions.

        Args:
            field_tensor: Field data tensor
            start_idx: Start index for boundary faces
            end_idx: End index for boundary faces
        """
        if field_tensor is None:
            raise ValueError("Field tensor cannot be None")

        if start_idx < 0 or end_idx < 0:
            raise ValueError("Indices cannot be negative")

        if start_idx >= end_idx:
            raise ValueError("Start index must be less than end index")

        if end_idx > field_tensor.shape[0]:
            raise ValueError("End index exceeds tensor size")

    @staticmethod
    def extract_value(value_input: Union[float, dict, list], default: float = 0.0) -> Union[float, torch.Tensor]:
        """
        Extract value from various input formats.

        Args:
            value_input: Input value in various formats
            default: Default value if extraction fails

        Returns:
            Extracted value as float or tensor
        """
        if value_input is None:
            return default

        if isinstance(value_input, dict):
            return value_input.get('val', default)
        elif isinstance(value_input, (int, float)):
            return float(value_input)
        elif isinstance(value_input, (list, tuple)):
            return torch.tensor(value_input, dtype=torch.float32)
        else:
            return default

    @staticmethod
    def apply_to_boundary_cells(field_tensor: torch.Tensor,
                               start_idx: int,
                               end_idx: int,
                               value: Union[float, torch.Tensor],
                               neighbor_cell: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Apply value to boundary cells.

        Args:
            field_tensor: Field data tensor
            start_idx: Start index for boundary faces
            end_idx: End index for boundary faces
            value: Value to apply
            neighbor_cell: Neighbor cell connectivity

        Returns:
            Modified field tensor
        """
        if neighbor_cell is not None:
            boundary_cells = neighbor_cell[1, start_idx:end_idx]
            if isinstance(value, torch.Tensor):
                if value.dim() == 1 and field_tensor.dim() == 2:
                    field_tensor[boundary_cells] = value.unsqueeze(0).expand(boundary_cells.shape[0], -1)
                else:
                    field_tensor[boundary_cells] = value
            else:
                if field_tensor.dim() == 2:
                    field_tensor[boundary_cells, 0] = value
                else:
                    field_tensor[boundary_cells] = value
        else:
            # Fallback: apply to index range directly
            if isinstance(value, torch.Tensor):
                field_tensor[start_idx:end_idx] = value
            else:
                if field_tensor.dim() == 2:
                    field_tensor[start_idx:end_idx, 0] = value
                else:
                    field_tensor[start_idx:end_idx] = value

        return field_tensor

def create_boundary_condition_registry():
    """
    Create a registry of all available boundary condition functions.

    Returns:
        Dictionary mapping field names to their boundary condition modules
    """
    from . import setU, setP, setP_rgh, setT, setRho

    return {
        'U': setU,
        'p': setP,
        'p_rgh': setP_rgh,
        'T': setT,
        'rho': setRho
    }