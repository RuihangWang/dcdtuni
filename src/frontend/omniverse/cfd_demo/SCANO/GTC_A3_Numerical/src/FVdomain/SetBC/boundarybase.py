import torch
import os
import sys
from typing import Dict, Any, Optional
from torch.nn import Module

# Add src to path for imports
file_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.append(file_dir)

# Import boundary condition functions
from FVdomain.SetBC.onthefly import setU, setP, setP_rgh, setT, setRho

class fvPatch(Module):
    """
    Finite Volume Patch class for handling boundary conditions.
    Each patch represents a boundary region with specific boundary conditions for different fields.
    """

    def __init__(self, patch_dict: Dict[str, Any]):
        """
        Initialize fvPatch with patch information.

        Args:
            patch_dict: Dictionary containing patch information from mesh parsing
        """
        self.patch_name = patch_dict["patch_name"]
        self.start_idx_face = patch_dict.get("start_idx_face", patch_dict.get("start_face", 0))
        self.end_idx_face = patch_dict.get("end_idx_face", self.start_idx_face + patch_dict.get("n_faces", 0))
        self.start_idx_cpd_cell = patch_dict.get("start_idx_cpd_cell", self.start_idx_face)
        self.end_idx_cpd_cell = patch_dict.get("end_idx_cpd_cell", self.end_idx_face)
        self.geom_type = patch_dict.get("geom_type", "patch")
        self.neighbour_patch = patch_dict.get("neighbour_patch", None)

        # Store registered variables and their boundary conditions
        self.variable_list = []
        self.boundary_conditions = {}

        # Mapping from field names to their boundary condition modules
        self.bc_modules = {
            'U': setU,
            'p': setP,
            'p_rgh': setP_rgh,
            'T': setT,
            'rho': setRho
        }

    def register_variable_and_bc(self, field_name: str, bc_info: Dict[str, Any], toml_info: Optional[Dict] = None):
        """
        Register a variable and its boundary conditions for this patch.

        Args:
            field_name: Name of the field (e.g., 'U', 'p', 'T')
            bc_info: Boundary condition information from TOML file
            toml_info: Additional TOML configuration information
        """
        if field_name not in self.variable_list:
            self.variable_list.append(field_name)

        bc_type = bc_info.get('type', 'zeroGradient')

        # Get the appropriate boundary condition module
        bc_module = self.bc_modules.get(field_name)
        if bc_module is None:
            raise ValueError(f"No boundary condition module found for field '{field_name}'")

        # Get the boundary condition function
        bc_function_name = bc_type.lower()
        if hasattr(bc_module, bc_function_name):
            bc_function = getattr(bc_module, bc_function_name)
        else:
            # Fallback to zerogradient if specific BC type not found
            bc_function = getattr(bc_module, 'zerogradient', None)
            if bc_function is None:
                raise ValueError(f"No boundary condition function found for type '{bc_type}' in field '{field_name}'")

        # Prepare boundary condition parameters
        bc_params = self._prepare_bc_parameters(bc_info, toml_info)

        # Store the boundary condition configuration
        self.boundary_conditions[field_name] = {
            'type': bc_type,
            'function': bc_function,
            'parameters': bc_params,
            'start_idx': self.start_idx_cpd_cell,
            'end_idx': self.end_idx_cpd_cell,
            'start_idx_face': self.start_idx_face,
            'end_idx_face': self.end_idx_face
        }

        # Create dynamic attribute for easy access
        setattr(self, field_name, self.boundary_conditions[field_name])

    def _prepare_bc_parameters(self, bc_info: Dict[str, Any], toml_info: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Prepare parameters for boundary condition functions.

        Args:
            bc_info: Boundary condition information from TOML
            toml_info: Additional TOML configuration

        Returns:
            Dictionary of parameters for the boundary condition function
        """
        params = {}

        # Extract value information
        if 'value' in bc_info:
            value_info = bc_info['value']
            if isinstance(value_info, dict):
                params['value_type'] = value_info.get('value_type', 'uniform')
                params['value'] = value_info.get('val', None)
            else:
                params['value'] = value_info
                params['value_type'] = 'uniform'

        # Extract other common parameters
        for param_name in ['gradient', 'inletValue', 'freestreamValue', 'p0', 'rho', 'psi', 'gamma']:
            if param_name in bc_info:
                param_value = bc_info[param_name]
                if isinstance(param_value, dict) and 'val' in param_value:
                    params[param_name] = param_value['val']
                else:
                    params[param_name] = param_value

        # Add patch geometry information
        params['patch_name'] = self.patch_name
        params['geom_type'] = self.geom_type
        params['neighbour_patch'] = self.neighbour_patch

        return params

    def apply_boundary_condition(self, field_name: str, field_data: torch.Tensor, **kwargs) -> torch.Tensor:
        """
        Apply boundary condition for a specific field.

        Args:
            field_name: Name of the field
            field_data: Field data tensor
            **kwargs: Additional arguments for the boundary condition function

        Returns:
            Modified field data tensor
        """
        if field_name not in self.boundary_conditions:
            raise ValueError(f"Field '{field_name}' not registered for patch '{self.patch_name}'")

        bc_config = self.boundary_conditions[field_name]
        bc_function = bc_config['function']
        bc_params = bc_config['parameters'].copy()
        bc_params.update(kwargs)

        # Apply the boundary condition
        return bc_function(
            field_data,
            bc_config['start_idx'],
            bc_config['end_idx'],
            **bc_params
        )

    def has_field(self, field_name: str) -> bool:
        """Check if this patch has a specific field registered."""
        return field_name in self.variable_list

    def get_bc_type(self, field_name: str) -> Optional[str]:
        """Get the boundary condition type for a specific field."""
        if field_name in self.boundary_conditions:
            return self.boundary_conditions[field_name]['type']
        return None

    def __repr__(self):
        return f"fvPatch(name='{self.patch_name}', type='{self.geom_type}', fields={self.variable_list})"

    def forward(self, phi_cpd_cell: torch.Tensor) -> torch.Tensor:
        pass