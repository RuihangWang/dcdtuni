"""
Boundary condition implementations for temperature field (T).
"""

import re
import torch
from typing import Optional, Union, List, Any, Dict
from FVdomain.SetBC.onthefly.Parafunction import Functionbase, ParametricManager
from FVdomain.SetBC.onthefly.BCbase import BoundaryCondition, VARType, BCType
from FVdomain.SetBC.onthefly.setCyclic import apply_cyclic_face_values
# Reachable as `setT.Rackoutlettemperature`, which is the only name
# Load_mesh._get_bc_class will look up for a bc/T.toml entry reading
# `type = "rackOutletTemperature"`.  See the note in setU.py.
from FVdomain.SetBC.onthefly.setRack import Rackoutlettemperature  # noqa: F401
# Conjugate heat transfer: `type = "conjugateTemperature"` on the two interface patches of a fluid / solid case.
from FVdomain.SetBC.onthefly.setConjugate import Conjugatetemperature  # noqa: F401


class Empty(BoundaryCondition):
    """
    Empty boundary condition for temperature field.
    Used for 2D simulations - does not participate in flux calculations.

    For GNN+FVM architecture:
    - Ghost cells are copied from owner cells to ensure proper GNN message passing
    - This prevents ghost cells from having random/unphysical values
    """

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.EMPTY)

    def init_field(self, T_cpd_cell, fvpatch, **kwargs):
        """
        Initialize empty boundary ghost cells by copying from owner cells.
        """
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face = fvpatch.start_idx_face[self.patch_idx]
        end_idx_face = fvpatch.end_idx_face[self.patch_idx]

        # Copy owner cell values to ghost cells
        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            T_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, :] = T_cpd_cell[owner_cells, :]

        return T_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Update empty boundary ghost cells by copying from owner cells.
        This ensures ghost cells have physically meaningful values for GNN.
        """
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Update ghost cell values by copying from owner cells
        if "t_cpd_cell" in fvfield:
            t_cpd_cell = fvfield["t_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            # Copy owner cell values to ghost cells
            t_cpd_cell[..., neighbor_cells, :] = t_cpd_cell[..., owner_cells, :]
            fvfield["t_cpd_cell"] = t_cpd_cell

        return fvfield


class Cyclic(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.CYCLIC)

    def init_field(self, T_cpd_cell, fvpatch, **kwargs):

        neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
        if neighbor_cpd_cell is None:
            raise ValueError("No neighbor_cpd_cell provided")

        return apply_cyclic_face_values(
            T_cpd_cell,
            fvpatch,
            neighbor_cpd_cell,
            self.patch_idx,
        )

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        t_cpd_cell = fvfield.get("t_cpd_cell", None)
        grad_t_cpd_cell = fvfield.get("grad_t_cpd_cell", None)

        t_cpd_cell = apply_cyclic_face_values(
            t_cpd_cell,
            fvpatch_batch,
            neighbor_cpd_cell,
            global_patch_idx,
        )
        grad_t_cpd_cell = apply_cyclic_face_values(
            grad_t_cpd_cell,
            fvpatch_batch,
            neighbor_cpd_cell,
            global_patch_idx,
            gradient=True,
        )

        fvfield["t_cpd_cell"] = t_cpd_cell
        fvfield["grad_t_cpd_cell"] = grad_t_cpd_cell

        return fvfield


''' >>> FirstTypeBoundary >>> '''
class Fixedvalue(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        if parametric_manager is not None:
            self.value = parametric_manager.resolve_value(boundary_info["value"])
        else:
            self.value = boundary_info["value"]
        self.value_tensor = None
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.FIRST)

    def init_field(self, T_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Apply to boundary cells (ghost cells)
        self.value_tensor = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end_idx_cpd_cell - start_idx_cpd_cell,
            channel_dim=1  # 温度是标量
        )

        # ``expression`` (e.g. the tall cavity's measured top/bottom wall temperatures, a piecewise-
        # linear T(x)) is evaluated at the patch's ghost positions = its face centres, exactly as setU.Fixedvalue
        # does.  Every other value_type takes the call below.
        if self.value["value_type"] == "expression":
            pos = kwargs.get("cpd_cell_pos", None)
            if pos is None:   # filled on the first residual evaluation (eval below)
                T_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = 0.0
            else:
                T_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = self.value_tensor.eval(
                    device=T_cpd_cell.device,
                    cpd_cell_pos=pos[..., start_idx_cpd_cell:end_idx_cpd_cell, :]).to(T_cpd_cell.dtype)
        else:
            T_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = self.value_tensor.eval(device=T_cpd_cell.device)

        return T_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch

        T_cpd_cell = fvfield["t_cpd_cell"]

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx], fvpatch_batch.end_idx_cpd_cell[global_patch_idx]

        # Apply to boundary cells (ghost cells)
        if self.value["value_type"] == "expression":
            T_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = self.value_tensor.eval(
                device=T_cpd_cell.device,
                cpd_cell_pos=integrator.cpd_cell_pos[..., start_idx_cpd_cell:end_idx_cpd_cell, :]
            ).to(T_cpd_cell.dtype)
        else:
            T_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = self.value_tensor.eval(device=T_cpd_cell.device)

        fvfield["t_cpd_cell"] = T_cpd_cell
        return fvfield


''' >>> SecondTypeBoundary >>> '''
class Zerogradient(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.MIXED)

    def init_field(self, T_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face, end_idx_face = fvpatch.start_idx_face[self.patch_idx], fvpatch.end_idx_face[self.patch_idx]

        # Zero gradient BC: initialize boundary cells by copying from owner cells
        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            T_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = T_cpd_cell[owner_cells, 0:1]
        else:
            T_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = 0.0

        return T_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply zero gradient boundary condition for temperature.

        For GNN+FVM architecture, this BC has two roles:
        1. Update ghost cell values (copy from owner) - for GNN and gradient reconstruction
        2. Set gradient flux to zero - for diffusion term in FVM

        Args:
            fvfield: A Dict that may contain "t_cpd_cell" and/or "grad_t_flux_face"
            **kwargs: Additional parameters
        """

        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Role 1: Update ghost cell values (ALWAYS do this for GNN+FVM)
        if "t_cpd_cell" in fvfield:
            t_cpd_cell = fvfield["t_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            t_cpd_cell[..., neighbor_cells, :] = t_cpd_cell[..., owner_cells, :]
            fvfield["t_cpd_cell"] = t_cpd_cell

        if "grad_t_flux_face" in fvfield:
            grad_t_flux_face = fvfield["grad_t_flux_face"]
            # Zero gradient: set gradient flux to zero
            grad_t_flux_face[..., start_idx_face:end_idx_face, :] = 0.0
            fvfield["grad_t_flux_face"] = grad_t_flux_face

        return fvfield


class Symmetry(BoundaryCondition):
    """
    Symmetry plane boundary condition for temperature field.

    Physics:
    - Temperature has zero normal gradient at the symmetry plane (∂T/∂n = 0)
    - This is equivalent to zeroGradient for scalar fields

    Implementation:
    - Ghost cell temperature is copied from owner cell
    - T_ghost = T_owner
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.MIXED)

    def init_field(self, T_cpd_cell, fvpatch, **kwargs):
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face = fvpatch.start_idx_face[self.patch_idx]
        end_idx_face = fvpatch.end_idx_face[self.patch_idx]

        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            T_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = T_cpd_cell[owner_cells, 0:1]
        else:
            T_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = 0.0
        return T_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply symmetry boundary condition for temperature.
        Copies owner cell values to ghost cells (zero normal gradient).
        """
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        if "t_cpd_cell" in fvfield:
            t_cpd_cell = fvfield["t_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            t_cpd_cell[..., neighbor_cells, :] = t_cpd_cell[..., owner_cells, :]
            fvfield["t_cpd_cell"] = t_cpd_cell

        if "grad_t_flux_face" in fvfield:
            grad_t_flux_face = fvfield["grad_t_flux_face"]
            grad_t_flux_face[..., start_idx_face:end_idx_face, :] = 0.0
            fvfield["grad_t_flux_face"] = grad_t_flux_face

        return fvfield


class Freestream(BoundaryCondition):
    """
    Freestream boundary condition for temperature in compressible flow (similar to SU2 MARKER_FAR).
    Applies characteristic-based boundary conditions using Riemann invariants.

    Theory:
    - Subsonic inflow (Ma < 1, u·n < 0): Fix farfield temperature
      (entropy characteristic enters)
    - Subsonic outflow (Ma < 1, u·n > 0): Extrapolate temperature from interior
      (entropy characteristic leaves)
    - Supersonic inflow (Ma >= 1, u·n < 0): Fix farfield temperature
      (all characteristics enter)
    - Supersonic outflow (Ma >= 1, u·n >= 0): Extrapolate from interior
      (all characteristics leave)
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        # Freestream temperature value
        if parametric_manager is not None:
            self.value = parametric_manager.resolve_value(boundary_info.get("value", None))
        else:
            self.value = boundary_info.get("value", None)
        self.fvconfig = boundary_info.get("fvconfig", {})
        self.value_tensor = None
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.MIXED)

    def init_field(self, t_cpd_cell, fvpatch, **kwargs):
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Initialize with freestream temperature
        if self.value is None:
            fvconfig = kwargs.get("fvconfig", self.fvconfig)
            tpde = fvconfig.get("theta_pde", {}) if fvconfig else {}
            T_inf = tpde.get("T_inf", 273.15)
            if isinstance(T_inf, dict):
                T_inf = T_inf.get("val", T_inf)
            self.value = {"value_type": "uniform", "val": float(T_inf)}

        self.value_tensor = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end_idx_cpd_cell - start_idx_cpd_cell,
            channel_dim=1  # Temperature is scalar
        )

        t_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = self.value_tensor.eval(device=t_cpd_cell.device)

        return t_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply characteristic-based freestream temperature boundary condition.

        NOTE: This is now handled by setU.py which updates all primitive variables
        (rho, uvw, p, T) simultaneously using Riemann invariants.
        This method is a pass-through to avoid overwriting the correct values.
        """
        return fvfield


class Calculated(BoundaryCondition):
    """
    Calculated boundary condition for temperature field.

    For this GNN+FVM setup, `calculated` is a strict no-op.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.MIXED)

    def init_field(self, t_cpd_cell, fvpatch, **kwargs):
        # Initialize boundary cells by copying adjacent owner cell values.
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face = fvpatch.start_idx_face[self.patch_idx]
        end_idx_face = fvpatch.end_idx_face[self.patch_idx]

        neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            t_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = t_cpd_cell[owner_cells, 0:1]

        return t_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        # No-op: do not enforce anything for calculated BC
        return fvfield

class Exprfixedvalue(Fixedvalue):
    """Fallback for exprFixedValue — behaves as a plain Fixedvalue.

    Case-specific coupling logic (e.g. T_out = T_in_avg + deltaT) should
    be implemented as a custom BC in the case's bc/ directory, following
    the pattern in mesh_example/Data_Center_sym/h5/bc/setT.py.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                 parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, boundary_info, parametric_manager)
