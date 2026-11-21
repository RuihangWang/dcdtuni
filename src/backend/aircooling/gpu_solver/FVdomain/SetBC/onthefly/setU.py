
"""
Boundary condition implementations for velocity field (U).
"""

import os
import torch
import math
from typing import Dict
from FVdomain.SetBC.onthefly.Parafunction import Functionbase, ParametricManager
from FVdomain.SetBC.onthefly.BCbase import BoundaryCondition, VARType, BCType
from FVdomain.SetBC.onthefly.setCyclic import apply_cyclic_face_values
# The loader resolves a BC class as `set<var>.<Type>` (Load_mesh._get_bc_class),
# so a class serving TWO variables has to be reachable from both modules.  The
# rack's velocity pair lives in setRack because it is one cross-patch model, not
# because it is not a velocity BC; without these names a bc/U.toml saying
# `type = "rackInletVelocity"` raises AttributeError at load.
from FVdomain.SetBC.onthefly.setRack import (  # noqa: F401
    Rackinletvelocity, Rackoutletvelocity)

class Empty(BoundaryCondition):
    """
    Empty boundary condition for velocity field.
    Used for 2D simulations - does not participate in flux calculations.

    For GNN+FVM architecture:
    - Ghost cells are copied from owner cells to ensure proper GNN message passing
    - This prevents ghost cells from having random/unphysical values
    """

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.EMPTY)

    def init_field(self, phi_cpd_cell, fvpatch, **kwargs):
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
            phi_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, :] = phi_cpd_cell[owner_cells, :]

        return phi_cpd_cell

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
        if "uvw_cpd_cell" in fvfield:
            uvw_cpd_cell = fvfield["uvw_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            # Copy owner cell values to ghost cells
            uvw_cpd_cell[..., neighbor_cells, :] = uvw_cpd_cell[..., owner_cells, :]
            fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        return fvfield


class Cyclic(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.CYCLIC)
    
    def init_field(self, phi_cpd_cell, fvpatch, **kwargs):

        neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
        if neighbor_cpd_cell is None:
            raise ValueError("No neighbor_cpd_cell provided")

        return apply_cyclic_face_values(
            phi_cpd_cell,
            fvpatch,
            neighbor_cpd_cell,
            self.patch_idx,
        )
    
    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        uvw_cpd_cell = fvfield.get("uvw_cpd_cell", None)
        grad_uvw_cpd_cell = fvfield.get("grad_uvw_cpd_cell", None)

        uvw_cpd_cell = apply_cyclic_face_values(
            uvw_cpd_cell,
            fvpatch_batch,
            neighbor_cpd_cell,
            global_patch_idx,
        )
        grad_uvw_cpd_cell = apply_cyclic_face_values(
            grad_uvw_cpd_cell,
            fvpatch_batch,
            neighbor_cpd_cell,
            global_patch_idx,
            gradient=True,
        )

        fvfield["uvw_cpd_cell"] = uvw_cpd_cell
        fvfield["grad_uvw_cpd_cell"] = grad_uvw_cpd_cell
        
        return fvfield


''' >>> FirstTypeBoundary >>> '''
class Fixedvalue(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        # parametric_manager 用于解析 $variable_name 引用

        # 如果有 parametric_manager，则解析 boundary_info 中的参数引用
        if parametric_manager is not None:
            self.value = parametric_manager.resolve_value(boundary_info["value"])
        else:
            self.value = boundary_info["value"]

        self.value_tensor = None
        super().__init__(patch_name,patch_idx, VARType.VELOCITY, BCType.FIRST)
    
    def init_field(self, uvw_cpd_cell:torch.Tensor, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Apply to boundary cells (ghost cells)
        self.value_tensor = Functionbase(
            value_type=self.value["value_type"], 
            value=self.value["val"], 
            num_element=end_idx_cpd_cell-start_idx_cpd_cell, 
            channel_dim=3 # 这里是速度场
        )

        if self.value["value_type"] == "expression":
            pos = kwargs.get("cpd_cell_pos", None)
            if pos is None:   # filled on the first residual evaluation (eval below)
                uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = 0.0
            else:
                uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = self.value_tensor.eval(
                    device=uvw_cpd_cell.device,
                    cpd_cell_pos=pos[..., start_idx_cpd_cell:end_idx_cpd_cell, :]).to(uvw_cpd_cell.dtype)
        else:
            uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = self.value_tensor.eval(device=uvw_cpd_cell.device)
        
        return uvw_cpd_cell
    
    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        uvw_cpd_cell = fvfield["uvw_cpd_cell"]

        fvpatch_batch = integrator.fvpatch_batch

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx], fvpatch_batch.end_idx_cpd_cell[global_patch_idx]

        # Apply to compound boundary cells (ghost cells made from boundary faces)
        if self.value["value_type"] == "expression":
            uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = self.value_tensor.eval(
                device=uvw_cpd_cell.device,
                cpd_cell_pos=integrator.cpd_cell_pos[..., start_idx_cpd_cell:end_idx_cpd_cell, :]
            ).to(uvw_cpd_cell.dtype)
        else:
            uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = self.value_tensor.eval(device=uvw_cpd_cell.device)
        # print(f"self.value_tensor.eval(device=uvw_cpd_cell.device): {self.value_tensor.eval(device=uvw_cpd_cell.device)}")
        fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        return fvfield

class Uniformfixedvalue(BoundaryCondition):
    """

    """
    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        if parametric_manager is not None:
            self.uniformValue = parametric_manager.resolve_value(boundary_info["uniformValue"])
            self.value = parametric_manager.resolve_value(boundary_info["value"])
        else:
            self.uniformValue = boundary_info["uniformValue"]
            self.value = boundary_info["value"]
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.FIRST)
        
    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Apply to boundary cells (ghost cells)
        self.value_tensor = Functionbase(
            value_type=self.value["value_type"], 
            value=self.value["val"], 
            num_element=end_idx_cpd_cell-start_idx_cpd_cell, 
            channel_dim=3 # 这里是速度场
        )

        uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell,0:3] = self.value_tensor.eval(device=uvw_cpd_cell.device)
        
        return uvw_cpd_cell
    
    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        uvw_cpd_cell = fvfield["uvw_cpd_cell"]

        fvpatch_batch = integrator.fvpatch_batch

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx], fvpatch_batch.end_idx_cpd_cell[global_patch_idx]

        # 这个边界条件的详细信息需要从uniformValue里面去展开
        # IMPORTANT: Use get() instead of pop() to avoid modifying self.uniformValue
        # pop() would delete the key, causing errors on subsequent calls
        value_type = self.uniformValue.get("value_type")
        val = self.uniformValue.get("val", None) # 允许为None，如果是None，那么说明定了expression或者其他类型

        # Apply to boundary cells (ghost cells)
        value_tensor = Functionbase(
            value_type=value_type, value=val, num_element=end_idx_cpd_cell-start_idx_cpd_cell, channel_dim=3
        ).eval(device=uvw_cpd_cell.device)

        uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = value_tensor

        fvfield["uvw_cpd_cell"] = uvw_cpd_cell
        return fvfield
    
class Noslip(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.FIRST)
        
    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]

        uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell,0:3] = 0.
        
        return uvw_cpd_cell
    
    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        uvw_cpd_cell = fvfield["uvw_cpd_cell"]

        fvpatch_batch = integrator.fvpatch_batch

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx], fvpatch_batch.end_idx_cpd_cell[global_patch_idx]
        uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = 0.0

        fvfield["uvw_cpd_cell"] = uvw_cpd_cell
        return fvfield
    
class Flowrateinletvelocity(BoundaryCondition):

    # Patch-sum hook (opt-in, multi-GPU, docs/development/multi-gpu.md): a partitioned run replaces it on the
    # instance by a sum over the faces its part owns followed by an all-reduce; the default is the sum below.
    @staticmethod
    def _patch_total(values):
        return torch.sum(values)

    def __init__(self ,patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        if parametric_manager is not None:
            self.volumetricFlowRate = parametric_manager.resolve_value(boundary_info.get("volumetricFlowRate", None))
            self.massFlowRate = parametric_manager.resolve_value(boundary_info.get("massFlowRate", None))
            self.value = parametric_manager.resolve_value(boundary_info["value"])
        else:
            self.volumetricFlowRate = boundary_info.get("volumetricFlowRate",None)
            self.massFlowRate = boundary_info.get("massFlowRate",None)
            self.value = boundary_info["value"]

        # Handle rho: can be a number or "rho" string (reference to field)
        rho_value = boundary_info.get("rho", 1.0)
        if isinstance(rho_value, str):
            # If rho is a string like "rho", use rhoInlet value instead
            self.rho = boundary_info.get("rhoInlet", 1.0)
        else:
            self.rho = rho_value

        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.FIRST)
        
    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Apply to boundary cells (ghost cells)
        self.value_tensor = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end_idx_cpd_cell-start_idx_cpd_cell,
            channel_dim=3 # 这里是速度场
        )

        uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell,0:3] = self.value_tensor.eval(device=uvw_cpd_cell.device)

        return uvw_cpd_cell
      
    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply flow rate inlet velocity boundary condition.

        Args:
            U_cpd_cell: Velocity field tensor
            volumetricFlowRate: Volumetric flow rate
            massFlowRate: Mass flow rate
            rho: Density
            **kwargs: Additional parameters
        """

        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch
        uvw_cpd_cell = fvfield["uvw_cpd_cell"]

        face_area = integrator.face_area
        unv_face = integrator.unv_face  # Unit normal vector for each face
        cpd_neighbor = integrator.cpd_neighbor  # [2, num_faces] edge index
        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]
        start_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx]
        end_idx_cpd_cell = fvpatch_batch.end_idx_cpd_cell[global_patch_idx]

        if face_area is None or unv_face is None:
            raise ValueError("Flow rate inlet velocity requires face_area and unv_face")

        # Calculate total area of inlet faces (ensure on same device as uvw_cpd_cell)
        total_area = self._patch_total(face_area[start_idx_face:end_idx_face].to(uvw_cpd_cell.device))

        if self.volumetricFlowRate is not None:
            # Calculate velocity magnitude from volumetric flow rate
            volumetricFlowRate_value = Functionbase(
                value_type=self.volumetricFlowRate["value_type"],
                value=self.volumetricFlowRate["val"],
                num_element=1,
                channel_dim=1  # Flow rate is scalar
            ).eval(device=uvw_cpd_cell.device)
            velocity_magnitude = volumetricFlowRate_value / total_area
        elif self.massFlowRate is not None:
            # Calculate velocity magnitude from mass flow rate
            massFlowRate_value = Functionbase(
                value_type=self.massFlowRate["value_type"],
                value=self.massFlowRate["val"],
                num_element=1,
                channel_dim=1  # Flow rate is scalar
            ).eval(device=uvw_cpd_cell.device)
            velocity_magnitude = massFlowRate_value / (self.rho * total_area)
        else:
            raise ValueError("Either volumetricFlowRate or massFlowRate must be specified")

        # Get face normals for inlet faces
        # Note: unv_face points outward from owner cell, so for inlet we need negative
        normals = unv_face[start_idx_face:end_idx_face].to(uvw_cpd_cell.device)

        # Calculate velocity vector (negative normal for inlet, since normal points outward)
        velocity_vector = -velocity_magnitude * normals

        # Apply to compound boundary cells (ghost cells)
        uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = velocity_vector

        fvfield["uvw_cpd_cell"] = uvw_cpd_cell
        return fvfield

class Flowrateoutletvelocity(BoundaryCondition):

    # Patch-sum hook (opt-in, multi-GPU, docs/development/multi-gpu.md): a partitioned run replaces it on the
    # instance by a sum over the faces its part owns followed by an all-reduce; the default is the sum below.
    @staticmethod
    def _patch_sum(terms, what=None):
        return torch.sum(terms, dim=-2, keepdim=True)

    def __init__(self ,patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        if parametric_manager is not None:
            self.volumetricFlowRate = parametric_manager.resolve_value(boundary_info.get("volumetricFlowRate", None))
            self.massFlowRate = parametric_manager.resolve_value(boundary_info.get("massFlowRate", None))
            self.value = parametric_manager.resolve_value(boundary_info["value"])
        else:
            self.volumetricFlowRate = boundary_info.get("volumetricFlowRate", None)
            self.massFlowRate = boundary_info.get("massFlowRate", None)
            self.value = boundary_info["value"]

        # Handle rho: can be a number or "rho" string (reference to field)
        rho_value = boundary_info.get("rho", 1.0)
        if isinstance(rho_value, str):
            self.rho = boundary_info.get("rhoOutlet", boundary_info.get("rhoInlet", 1.0))
        else:
            self.rho = rho_value

        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.FIRST)

    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]

        self.value_tensor = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end_idx_cpd_cell-start_idx_cpd_cell,
            channel_dim=3
        )

        uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell,0:3] = self.value_tensor.eval(device=uvw_cpd_cell.device)

        return uvw_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply flow rate outlet velocity boundary condition (OpenFOAM-style).

        Logic:
        - Start from patchInternalField (owner cell values)
        - Remove normal component, clamp normal outflow (no backflow)
        - Adjust normal component to match target flow rate
        """
        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch
        uvw_cpd_cell = fvfield["uvw_cpd_cell"]

        face_area = integrator.face_area
        unv_face = integrator.unv_face
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]
        start_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx]
        end_idx_cpd_cell = fvpatch_batch.end_idx_cpd_cell[global_patch_idx]

        if face_area is None or unv_face is None:
            raise ValueError("flowRateOutletVelocity requires face_area and unv_face")

        owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
        normals = unv_face[start_idx_face:end_idx_face].to(uvw_cpd_cell.device)
        areas = face_area[start_idx_face:end_idx_face].to(uvw_cpd_cell.device)

        # Extrapolate patch velocity from interior (owner) cells
        uvw_owner = uvw_cpd_cell[..., owner_cells, :]
        nUp = torch.sum(uvw_owner * normals, dim=-1, keepdim=True)
        uvw_tan = uvw_owner - nUp * normals

        # Remove any reverse flow (no backflow at outlet)
        nUp = torch.clamp(nUp, min=0.0)

        # Compute target flow rate
        if self.volumetricFlowRate is not None:
            flow_rate_value = Functionbase(
                value_type=self.volumetricFlowRate["value_type"],
                value=self.volumetricFlowRate["val"],
                num_element=1,
                channel_dim=1
            ).eval(device=uvw_cpd_cell.device)
            target_flow = flow_rate_value.squeeze()
            rho_for_flux = 1.0
        elif self.massFlowRate is not None:
            flow_rate_value = Functionbase(
                value_type=self.massFlowRate["value_type"],
                value=self.massFlowRate["val"],
                num_element=1,
                channel_dim=1
            ).eval(device=uvw_cpd_cell.device)
            target_flow = flow_rate_value.squeeze()
            rho_for_flux = self.rho
        else:
            raise ValueError("Either volumetricFlowRate or massFlowRate must be specified")

        # Estimated flow rate from current outflow profile
        estimated_flow = self._patch_sum(rho_for_flux * areas * nUp, "estimate")

        # Adjust normal component to hit target flow
        area_sum = self._patch_sum(rho_for_flux * areas, "area")
        use_scale = estimated_flow > (0.5 * target_flow)
        scale = torch.abs(target_flow / (estimated_flow + 1e-12))
        adjustment = (target_flow - estimated_flow) / (area_sum + 1e-12)
        band = float(os.environ.get("LPCNS_WARP_DC_FRO_BAND", "0"))
        if band > 0.0:
            # the warp backend's smooth blend over estimate/Q in [0.5 - band, 0.5 + band] (FRO_BAND there); a patch
            # without flow (Q = 0: a stopped unit, a server without heat) has no ratio estimate/Q and keeps the switch,
            # whose two branches both give a zero normal outflow there
            still = target_flow == 0
            q_safe = torch.where(still, torch.ones_like(target_flow), target_flow)
            t = torch.clamp((estimated_flow / q_safe - 0.5 + band) / (2.0 * band), 0.0, 1.0)
            w = t * t * (3.0 - 2.0 * t)
            nUp = torch.where(still, torch.where(use_scale, nUp * scale, nUp + adjustment),
                              w * (nUp * scale) + (1.0 - w) * (nUp + adjustment))
        else:
            nUp = torch.where(use_scale, nUp * scale, nUp + adjustment)

        # Add corrected normal component back to tangential component
        uvw_boundary = uvw_tan + nUp * normals

        uvw_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = uvw_boundary
        fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        return fvfield
''' >>> SecondTypeBoundary: 已弃用该分类，第二类边界条件通通放入mixed中处理 >>> '''
class Zerogradient(BoundaryCondition):

    def __init__(self,patch_name, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.MIXED)

    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face, end_idx_face = fvpatch.start_idx_face[self.patch_idx], fvpatch.end_idx_face[self.patch_idx]

        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = uvw_cpd_cell[owner_cells, 0:3]
        else:
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = 0.0

        return uvw_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply zero gradient boundary condition for velocity.

        For GNN+FVM architecture, this BC has two roles:
        1. Update ghost cell values (copy from owner) - for GNN and gradient reconstruction
        2. Set gradient flux to zero - for diffusion term in FVM

        Args:
            fvfield: A Dict that may contain "uvw_cpd_cell" and/or "grad_uvw_flux_face"
            **kwargs: Additional parameters
        """

        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Role 1: Update ghost cell values (ALWAYS do this for GNN+FVM)
        if "uvw_cpd_cell" in fvfield:
            uvw_cpd_cell = fvfield["uvw_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            # Copy owner cell values to ghost cells (implements ∂U/∂n = 0)
            uvw_cpd_cell[..., neighbor_cells, :] = uvw_cpd_cell[..., owner_cells, :]
            fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        # Role 2: Set gradient flux to zero (for diffusion term)
        if "grad_uvw_flux_face" in fvfield:
            grad_uvw_flux_face = fvfield["grad_uvw_flux_face"]
            # Zero gradient: set gradient flux to zero
            grad_uvw_flux_face[..., start_idx_face:end_idx_face, :] = 0.0
            fvfield["grad_uvw_flux_face"] = grad_uvw_flux_face

        return fvfield


class Advectiveoutlet(BoundaryCondition):
    """Convective (advective) outflow for U: ∂u_g/∂t + U_c (u_g − u_owner)/δ = 0.

    The ghost value gets its own time evolution that TRANSPORTS structures out
    of the domain at the local advection speed U_c = max(u_owner_old·n̂, 0),
    instead of mirroring the owner back (zeroGradient acts as a soft wall for
    arriving vortices — the reflection is the residual "pump" that feeds the
    outlet-band oscillation modes). Discrete per-face update, built ONLY from
    the OLD state so every inner iteration of a time step sees the same ghost:

        α       = clamp(U_c·Δt/δ, α_floor, 1)
        u_g^{n+1} = u_g^n − α (u_g^n − u_owner^n)

    δ = |x_ghost − x_owner| (ghost cpd centers sit at the face). α_floor keeps
    stagnant/backflow faces slowly relaxing toward the owner so the ghost
    cannot drift unboundedly (inletOutlet-style safety without a hard switch).
    Ghost values are pure data (detached), like a Dirichlet whose value is
    yesterday's field advected. Diffusive flux across the patch is zeroed
    exactly as in Zerogradient. Requires the solver to expose the old field as
    fvfield["uvw_old_cpd_cell"]; falls back to zeroGradient (with a one-time
    warning) when the key is absent (e.g. legacy solvers / enforce_bcs_only).

    Δt is ``integrator._bc_dt_cell`` when a transient stepper armed one together
    with the true u^n (``BaseSolver.set_bc_old_state``), else
    ``integrator.dt_cell`` as before.
    """

    depends_on_old_state = True

    def __init__(self, patch_name, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        self.alpha_floor = float(boundary_info.get("alphaFloor", 0.02))
        self._warned_no_old = False
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.MIXED)

    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):
        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face, end_idx_face = fvpatch.start_idx_face[self.patch_idx], fvpatch.end_idx_face[self.patch_idx]

        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = uvw_cpd_cell[owner_cells, 0:3]
        else:
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = 0.0

        return uvw_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]
        owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
        neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

        if "uvw_cpd_cell" in fvfield:
            uvw_cpd_cell = fvfield["uvw_cpd_cell"]
            uvw_old = fvfield.get("uvw_old_cpd_cell", None)
            if uvw_old is None:
                if not self._warned_no_old:
                    print(f"[Advectiveoutlet:{self.patch_name}] fvfield has no 'uvw_old_cpd_cell' — falling back to zeroGradient ghost fill")
                    self._warned_no_old = True
                uvw_cpd_cell[..., neighbor_cells, :] = uvw_cpd_cell[..., owner_cells, :]
            else:
                with torch.no_grad():
                    owner_old = uvw_old[..., owner_cells, 0:3]
                    ghost_old = uvw_old[..., neighbor_cells, 0:3]
                    n_hat = integrator.unv_face[start_idx_face:end_idx_face]
                    delta = (integrator.cpd_cell_pos[neighbor_cells] - integrator.cpd_cell_pos[owner_cells]).norm(dim=1, keepdim=True).clamp_min(1e-12)
                    U_c = torch.sum(owner_old * n_hat, dim=-1, keepdim=True).clamp_min(0.0)
                    # The step this explicit ghost update spans.  A transient stepper
                    # that armed the true u^n as the old level (set_bc_old_state, H9)
                    # arms the true dt with it; otherwise dt_cell, which under a
                    # two-level BDF is the EFFECTIVE step dt/a0, not t^{n+1} - t^n.
                    dt_cell = getattr(integrator, "_bc_dt_cell", None)
                    if dt_cell is None:
                        dt_cell = integrator.dt_cell
                    dt = dt_cell[owner_cells]  # owner is an interior cell → valid dt row
                    alpha = (U_c * dt / delta).clamp(self.alpha_floor, 1.0)
                    ghost_new = ghost_old - alpha * (ghost_old - owner_old)
                uvw_cpd_cell[..., neighbor_cells, :] = ghost_new.to(uvw_cpd_cell.dtype)
            fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        if "grad_uvw_flux_face" in fvfield:
            grad_uvw_flux_face = fvfield["grad_uvw_flux_face"]
            grad_uvw_flux_face[..., start_idx_face:end_idx_face, :] = 0.0
            fvfield["grad_uvw_flux_face"] = grad_uvw_flux_face

        return fvfield


''' >>>  MixedBoundary: >>> '''
class Slip(BoundaryCondition):
    def __init__(self,patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.MIXED)
        
    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face, end_idx_face = fvpatch.start_idx_face[self.patch_idx], fvpatch.end_idx_face[self.patch_idx]

        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = uvw_cpd_cell[owner_cells, 0:3]
        else:
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = 0.0

        return uvw_cpd_cell
    
    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply slip boundary condition for velocity (Euler wall / inviscid wall).

        Following SU2 implementation (CFVMFlowSolverBase.inl:1160-1179):
        1. Normal velocity component is reflected: u_n_boundary = -u_n_interior
        2. Tangential velocity component is preserved (zero gradient)
        3. This is achieved by: u_boundary = u_interior - 2*(u_interior·n)*n

        For compressible flow, this ensures:
        - No mass flux through the wall (u·n = 0 at the wall)
        - Free slip in tangential direction (∂u_t/∂n = 0)

        Args:
            fvfield: Dictionary containing velocity field and other variables
            integrator: Solver object with mesh information
            patch_idx_offset: Offset for global patch index
        """

        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch
        uvw_cpd_cell = fvfield["uvw_cpd_cell"]

        # Get boundary face range
        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Get face connectivity and normals
        neighbor_cpd_cell = integrator.neighbor_cpd_cell
        face_normal = integrator.unv_face  # Unit normal vectors [N_faces, 3]

        # Get owner (interior) and neighbor (boundary ghost) cells for this patch
        owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]  # Interior cells
        boundary_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]  # Boundary ghost cells

        # Get face normals for this boundary (pointing outward from domain)
        normals = face_normal[start_idx_face:end_idx_face]  # [N_boundary, 3]

        # Get interior (owner) cell velocities
        uvw_interior = uvw_cpd_cell[..., owner_cells, :]  # [..., N_boundary, 3]

        # Compute normal velocity component: u_n = u · n
        un_interior = torch.sum(uvw_interior * normals, dim=-1, keepdim=True)  # [..., N_boundary, 1]

        # Apply slip BC: reflect normal component, preserve tangential component
        # u_boundary = u_interior - 2*(u_interior·n)*n
        uvw_boundary = uvw_interior - 2.0 * un_interior * normals  # [N_boundary, 3]

        # Set boundary ghost cell values
        uvw_cpd_cell[..., boundary_cells, :] = uvw_boundary
        fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        # Handle flux face if it exists (for incompressible solvers)
        if "uvw_flux_face" in fvfield:
            uvw_flux_face = fvfield["uvw_flux_face"]
            # Normal velocity flux at wall should be zero
            uvw_flux_face[..., start_idx_face:end_idx_face, :] = 0.
            fvfield["uvw_flux_face"] = uvw_flux_face

        if "mass_flux_face" in fvfield:
            # No mass crosses a slip wall
            mass_flux_face = fvfield["mass_flux_face"]
            mass_flux_face[..., start_idx_face:end_idx_face, :] = 0.
            fvfield["mass_flux_face"] = mass_flux_face

        # Handle gradient flux for the compressible solver
        if "grad_uvw_flux_face" in fvfield:
            grad_uvw_flux_face = fvfield["grad_uvw_flux_face"]
            # For slip BC: normal gradient of tangential velocity is zero
            # This is automatically satisfied by the ghost cell construction above
            # But we still set the flux to zero for consistency
            grad_uvw_flux_face[..., start_idx_face:end_idx_face, :] = 0
            fvfield["grad_uvw_flux_face"] = grad_uvw_flux_face

        if "grad_rho_uvw_p_flux_face" in fvfield:
            # Zero out convective/diffusive flux contributions on slip walls
            grad_rho_uvw_p_flux_face = fvfield["grad_rho_uvw_p_flux_face"]
            grad_rho_uvw_p_flux_face[..., start_idx_face:end_idx_face, :] = 0
            fvfield["grad_rho_uvw_p_flux_face"] = grad_rho_uvw_p_flux_face

        return fvfield


class Inletoutlet(BoundaryCondition):
    def __init__(self,patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        if parametric_manager is not None:
            self.inletValue = parametric_manager.resolve_value(boundary_info["inletValue"])
            self.value = parametric_manager.resolve_value(boundary_info["value"])
        else:
            self.inletValue = boundary_info["inletValue"]
            self.value = boundary_info["value"]
        self.value_tensor=None
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.MIXED)
        
    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Apply to boundary cells (ghost cells)
        self.value_tensor = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"], 
            num_element=end_idx_cpd_cell-start_idx_cpd_cell, 
            channel_dim=3 # 这里是速度场
        )

        uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell,0:3] = self.value_tensor.eval(device=uvw_cpd_cell.device)

        return uvw_cpd_cell
      
    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply inlet-outlet boundary condition for velocity.
        Acts as inlet when flow is inward, outlet when flow is outward.

        Args:
            U_cpd_cell: Velocity field tensor
            inletValue: Inlet velocity value
            **kwargs: Additional parameters
        """

        global_patch_idx = self.patch_idx + patch_idx_offset

        uvw_cpd_cell = fvfield.get("uvw_cpd_cell", None)
        uvw_flux_face = fvfield.get("uvw_flux_face", None)
        fvpatch_batch = integrator.fvpatch_batch
        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        if start_idx_face is None or end_idx_face is None or neighbor_cpd_cell is None:
            raise ValueError(
                "inletOutlet boundary condition requires start/end face indices and neighbor_cpd_cell"
            )

        owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
        neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

        # Determine inflow/outflow using flux if available
        if uvw_flux_face is not None:
            local_flux = uvw_flux_face[..., start_idx_face:end_idx_face, 0:1]
            outflow_mask = local_flux >= 0
        else:
            # Fallback: compute from owner velocity and face normal
            face_normal = integrator.unv_face[start_idx_face:end_idx_face]
            owner_u = uvw_cpd_cell[..., owner_cells, :]
            local_flux = torch.sum(owner_u * face_normal, dim=-1, keepdim=True)
            outflow_mask = local_flux >= 0

        # Every time slab selects its own branch.  The static inlet value
        # broadcasts across leading time/batch dimensions.
        owner_values = uvw_cpd_cell[..., owner_cells, :]
        inlet_values = Functionbase(
            value_type=self.inletValue["value_type"],
            value=self.inletValue["val"],
            num_element=neighbor_cells.shape[0],
            channel_dim=3,
        ).eval(device=uvw_cpd_cell.device)
        uvw_cpd_cell[..., neighbor_cells, :] = torch.where(
            outflow_mask,
            owner_values,
            inlet_values,
        )

        # Zero gradient for diffusion flux at outflow
        if "grad_uvw_flux_face" in fvfield:
            grad_uvw_flux_face = fvfield["grad_uvw_flux_face"]
            patch_gradient = grad_uvw_flux_face[..., start_idx_face:end_idx_face, :]
            grad_uvw_flux_face[..., start_idx_face:end_idx_face, :] = torch.where(
                outflow_mask,
                torch.zeros_like(patch_gradient),
                patch_gradient,
            )
            fvfield["grad_uvw_flux_face"] = grad_uvw_flux_face

        fvfield["uvw_cpd_cell"] = uvw_cpd_cell
        return fvfield

class Symmetry(BoundaryCondition):
    """
    Symmetry plane boundary condition for velocity field (OpenFOAM-compatible).

    Key formulas:
    - u_ghost = u_owner - 2*(u·n)*n  (reflection)
    - u_face = 0.5 * (u_owner + u_ghost) = u_owner - (u·n)*n
    - snGrad = -deltaCoeffs * (u·n) * n

    Note: cpd_cell stores boundary face values, not ghost values.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.MIXED)

    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face = fvpatch.start_idx_face[self.patch_idx]
        end_idx_face = fvpatch.end_idx_face[self.patch_idx]

        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = uvw_cpd_cell[owner_cells, 0:3]
        else:
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = 0.0
        return uvw_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        uvw_cpd_cell = fvfield["uvw_cpd_cell"]

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        neighbor_cpd_cell = integrator.neighbor_cpd_cell
        face_normal = integrator.unv_face

        owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
        boundary_face_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]  # Points to boundary face in cpd_cell
        normals = face_normal[start_idx_face:end_idx_face]

        uvw_owner = uvw_cpd_cell[..., owner_cells, :]
        un_owner = torch.sum(uvw_owner * normals, dim=-1, keepdim=True)

        # Boundary face value: u_face = u_owner - (u·n)*n
        # This is equivalent to 0.5 * (u_owner + u_ghost) where u_ghost = u_owner - 2*(u·n)*n
        uvw_boundary_face = uvw_owner - un_owner * normals

        # Store boundary face value in cpd_cell (not ghost value!)
        uvw_cpd_cell[..., boundary_face_cells, :] = uvw_boundary_face
        fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        # Normal flux = 0
        if "uvw_flux_face" in fvfield:
            fvfield["uvw_flux_face"][..., start_idx_face:end_idx_face, :] = 0.0

        if "mass_flux_face" in fvfield:
            fvfield["mass_flux_face"][..., start_idx_face:end_idx_face, :] = 0.0

        # Gradient flux: snGrad = -deltaCoeffs * (u·n) * n
        # grad_uvw_flux_face shape: [N_faces, 6] = uvw_new(0:3) + uvw_old(3:6)
        if "grad_uvw_flux_face" in fvfield:
            grad_uvw_flux_face = fvfield["grad_uvw_flux_face"]
            n_cols = grad_uvw_flux_face.shape[-1]

            if hasattr(integrator, 'dCF') and integrator.dCF is not None:
                num_patch_faces = end_idx_face - start_idx_face
                dCF = integrator.dCF[start_idx_face:end_idx_face]
                if dCF.numel() != num_patch_faces:
                    raise ValueError(
                        "Symmetry expects one dCF coefficient per patch face, "
                        f"got shape {tuple(dCF.shape)}"
                    )
                # Geometry may be stored as [F,1] or [F,1,1]. Canonicalize it
                # to [F,1] so it broadcasts against both [F,1] and [T,F,1]
                # field layouts without creating a spurious face axis.
                dCF = dCF.reshape(num_patch_faces, 1)
                delta_coeffs = 1.0 / (dCF + 1e-12)
            else:
                delta_coeffs = torch.ones((end_idx_face - start_idx_face, 1),
                                         device=uvw_owner.device, dtype=uvw_owner.dtype)

            if n_cols >= 6 and "uvw_old_cpd_cell" in fvfield:
                # Vectorized: concatenate new and old, compute snGrad together
                uvw_old_cpd_cell = fvfield["uvw_old_cpd_cell"]
                uvw_old_owner = uvw_old_cpd_cell[..., owner_cells, :]

                # un for both: [N_boundary, 2]
                un_new = torch.sum(uvw_owner * normals, dim=-1, keepdim=True)
                un_old = torch.sum(uvw_old_owner * normals, dim=-1, keepdim=True)

                # snGrad = -deltaCoeffs * un * n
                snGrad_new = -delta_coeffs * un_new * normals
                snGrad_old = -delta_coeffs * un_old * normals

                grad_uvw_flux_face[..., start_idx_face:end_idx_face, 0:3] = snGrad_new
                grad_uvw_flux_face[..., start_idx_face:end_idx_face, 3:6] = snGrad_old
            else:
                # Only new velocity available
                snGrad_uvw_new = -delta_coeffs * un_owner * normals
                if n_cols >= 6:
                    grad_uvw_flux_face[..., start_idx_face:end_idx_face, 0:3] = snGrad_uvw_new
                    grad_uvw_flux_face[..., start_idx_face:end_idx_face, 3:6] = snGrad_uvw_new
                else:
                    grad_uvw_flux_face[..., start_idx_face:end_idx_face, :] = snGrad_uvw_new

            fvfield["grad_uvw_flux_face"] = grad_uvw_flux_face

        if "grad_rho_uvw_p_flux_face" in fvfield:
            fvfield["grad_rho_uvw_p_flux_face"][..., start_idx_face:end_idx_face, :] = 0.0

        return fvfield


class Freestream(BoundaryCondition):
    """
    Freestream boundary condition for compressible flow (similar to SU2 MARKER_FAR).
    Applies characteristic-based boundary conditions using Riemann invariants.

    Theory:
    - Subsonic inflow (Ma < 1, u·n < 0): 3 characteristics enter domain
      → Fix farfield values for velocity and temperature, extrapolate pressure
    - Subsonic outflow (Ma < 1, u·n > 0): 1 characteristic enters domain
      → Extrapolate velocity and temperature from interior, fix farfield pressure
    - Supersonic inflow (Ma >= 1, u·n < 0): All characteristics enter
      → Fix all farfield values
    - Supersonic outflow (Ma >= 1, u·n >= 0): All characteristics leave
      → Extrapolate all values from interior
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        """
        boundary_info does not need explicit freestream values; they are taken from
        integrator (fvconfig theta_pde) at runtime to keep consistency.
        """
        if parametric_manager is not None:
            self.value = parametric_manager.resolve_value(boundary_info.get("value", None))
        else:
            self.value = boundary_info.get("value", None)  # Freestream value if provided (favored)
        self.value_tensor = None
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.MIXED)

    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Initialize boundary ghosts with freestream if provided
        if self.value is None:
            # Build from fvconfig if provided in kwargs
            fvconfig = kwargs.get("fvconfig", {})
            tpde = fvconfig.get("theta_pde", {}) if fvconfig else {}
            gamma = tpde.get("gamma", 1.4)
            R_gas = tpde.get("R", 287.0)
            p_inf = tpde.get("p_inf", 101325.0)
            if isinstance(p_inf, dict): p_inf = p_inf.get("val", p_inf)
            T_inf = tpde.get("T_inf", 273.15)
            if isinstance(T_inf, dict): T_inf = T_inf.get("val", T_inf)
            Ma_inf = tpde.get("Ma", 0.0)
            if isinstance(Ma_inf, dict): Ma_inf = Ma_inf.get("val", Ma_inf)
            AOA_deg = tpde.get("AOA", 0.0)
            if isinstance(AOA_deg, dict): AOA_deg = AOA_deg.get("val", AOA_deg)
            c_inf = (gamma * R_gas * T_inf) ** 0.5
            u_mag_inf = Ma_inf * c_inf
            AOA_rad = math.radians(AOA_deg)

            # Get dimension from fvconfig (default to 2D for backward compatibility)
            # Following SU2 convention:
            # - 2D: AOA in X-Y plane, velocity = [cos(AOA), sin(AOA), 0]
            # - 3D: AOA in X-Z plane, velocity = [cos(AOA), 0, sin(AOA)]
            dim = tpde.get("dim", None)
            if dim is None:
                solver_config = fvconfig.get("solver", {}) if fvconfig else {}
                dim = solver_config.get("dim", 2)

            if dim == 3:
                # 3D: AOA in X-Z plane (Y is spanwise, like SU2 ONERA M6)
                uvw_val = [
                    float(u_mag_inf * math.cos(AOA_rad)),  # X (streamwise)
                    0.0,                                    # Y (spanwise)
                    float(u_mag_inf * math.sin(AOA_rad)),  # Z (vertical/lift)
                ]
            else:
                # 2D: AOA in X-Y plane (Z is extruded direction)
                uvw_val = [
                    float(u_mag_inf * math.cos(AOA_rad)),  # X (streamwise)
                    float(u_mag_inf * math.sin(AOA_rad)),  # Y (vertical/lift)
                    0.0,                                    # Z (extruded)
                ]
            self.value = {"value_type": "uniform", "val": uvw_val}

        self.value_tensor = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end_idx_cpd_cell - start_idx_cpd_cell,
            channel_dim=3,
        )

        uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = self.value_tensor.eval(device=uvw_cpd_cell.device)

        return uvw_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply characteristic-based freestream boundary condition for velocity.
        Uses Riemann invariants to determine boundary state based on flow direction.
        
        This method updates ALL primitive variables (rho, uvw, p, T) to ensure consistency.
        The pressure, temperature and density BC modules should defer to this one.
        """
        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch
        uvw_cpd_cell = fvfield.get("uvw_cpd_cell", None)
        p_cpd_cell = fvfield.get("p_cpd_cell", None)
        T_cpd_cell = fvfield.get("t_cpd_cell", None)
        rho_cpd_cell = fvfield.get("rho_cpd_cell", None)

        if uvw_cpd_cell is None:
            return fvfield

        # Get boundary face range
        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Get boundary cell range
        start_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx]
        end_idx_cpd_cell = fvpatch_batch.end_idx_cpd_cell[global_patch_idx]

        # Use the compressible solver's freestream bundle for consistency
        freestream = fvfield.get("freestream", None)
        if freestream is None:
            freestream = getattr(integrator, "freestream", {})
        gamma = freestream.get("gamma", getattr(integrator, 'gamma', 1.4))
        R = freestream.get("R", getattr(integrator, 'R', 287.0))
        rho_inf = freestream.get("rho_inf", getattr(integrator, 'rho_inf', 1.225))
        p_inf = freestream.get("p_inf", getattr(integrator, 'p_inf', 101325.0))
        c_inf = freestream.get("c_inf", getattr(integrator, 'c_inf', 340.0))

        # Freestream velocity vector from BC value (already constructed in init_field)
        if self.value_tensor is None:
            # Fallback: build from current BC value if not set
            if self.value is None:
                raise ValueError("Freestream BC missing value; expected injection from fvconfig.")
            self.value_tensor = Functionbase(
                value_type=self.value["value_type"],
                value=self.value["val"],
                num_element=end_idx_cpd_cell - start_idx_cpd_cell,
                channel_dim=3,
            )
        uvw_inf = self.value_tensor.eval(device=uvw_cpd_cell.device)  # [N_boundary, 3]
        
        # Get face connectivity and normals
        neighbor_cpd_cell = integrator.neighbor_cpd_cell
        face_normal = integrator.unv_face  # Unit normal vectors [N_faces, 3]

        # Get owner (interior) and neighbor (boundary) cells for this patch
        owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]  # Interior cells
        boundary_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]  # Boundary ghost cells

        # Get face normals for this boundary (pointing outward from domain)
        normals = face_normal[start_idx_face:end_idx_face]  # [N_boundary, 3]

        # Get interior (owner) cell states
        rho_in = rho_cpd_cell[..., owner_cells, :]
        uvw_in = uvw_cpd_cell[..., owner_cells, :]
        p_in = p_cpd_cell[..., owner_cells, :]
        # T_in = T_cpd_cell[owner_cells] # Not strictly needed if we use p/rho

        # Calculate derived interior quantities
        c_in = torch.sqrt(gamma * p_in / rho_in)
        un_in = torch.sum(uvw_in * normals, dim=-1, keepdim=True)

        # Calculate un_inf
        un_inf = torch.sum(uvw_inf * normals, dim=-1, keepdim=True)
        # Riemann Invariants
        # R+ = un + 2c/(gamma-1)
        # R- = un - 2c/(gamma-1)

        # SU2 Logic:
        # if (Vn_Inf > -SoundSpeed_Inf) { // Subsonic Inflow or Outflow
        #    RiemannPlus = Vn_Bound + 2*c_Bound/(g-1); // From Interior
        # } else { // Supersonic Inflow
        #    RiemannPlus = Vn_Inf + 2*c_Inf/(g-1); // From Farfield
        # }
        
        # if (Vn_Inf > SoundSpeed_Inf) { // Supersonic Outflow
        #    RiemannMinus = Vn_Bound - 2*c_Bound/(g-1); // From Interior
        # } else { // Subsonic Inflow or Outflow
        #    RiemannMinus = Vn_Inf - 2*c_Inf/(g-1); // From Farfield
        # }

        R_plus = torch.where(
            un_inf > -c_inf,
            un_in + 2 * c_in / (gamma - 1), # From Interior
            un_inf + 2 * c_inf / (gamma - 1) # From Farfield
        )

        R_minus = torch.where(
            un_inf > c_inf,
            un_in - 2 * c_in / (gamma - 1), # From Interior
            un_inf - 2 * c_inf / (gamma - 1) # From Farfield
        )

        # Reconstruct states
        un_b = 0.5 * (R_plus + R_minus)
        c_b = 0.25 * (gamma - 1) * (R_plus - R_minus)

        # Entropy/Temperature/Density
        # SU2:
        # if Inflow: Entropy = Pressure_Inf / pow(Density_Inf, Gamma)
        # if Outflow: Entropy = Pressure_Bound / pow(Density_Bound, Gamma)
        
        entropy_inf = p_inf / (rho_inf ** gamma)
        entropy_in = p_in / (rho_in ** gamma)

        entropy_b = torch.where(un_b < 0, entropy_inf, entropy_in)

        rho_b = (c_b**2 / (gamma * entropy_b)) ** (1 / (gamma - 1))
        p_b = entropy_b * (rho_b ** gamma)
        T_b = p_b / (rho_b * R)

        # Tangential velocity
        # If Inflow: Tangential from Farfield
        # If Outflow: Tangential from Interior
        
        # u_t = u - un * n
        ut_in = uvw_in - un_in * normals
        ut_inf = uvw_inf - un_inf * normals

        ut_b = torch.where(un_b < 0, ut_inf, ut_in)

        uvw_b = ut_b + un_b * normals

        # Update ghost cells for ALL variables
        # NOTE: Do NOT use detach() here for ODIL optimization method!
        # Ghost cell values are computed from interior cells via Riemann invariants,
        # and the gradient should flow back to interior cells for proper optimization.
        if rho_cpd_cell is not None:
            rho_cpd_cell[..., boundary_cells, :] = rho_b
            fvfield["rho_cpd_cell"] = rho_cpd_cell

        if uvw_cpd_cell is not None:
            uvw_cpd_cell[..., boundary_cells, :] = uvw_b
            fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        if p_cpd_cell is not None:
            p_cpd_cell[..., boundary_cells, :] = p_b
            fvfield["p_cpd_cell"] = p_cpd_cell

        if T_cpd_cell is not None:
            T_cpd_cell[..., boundary_cells, :] = T_b
            fvfield["t_cpd_cell"] = T_cpd_cell

        return fvfield


class Calculated(BoundaryCondition):
    """
    Calculated boundary condition for velocity field.

    In this GNN+FVM workflow, `calculated` is a strict no-op:
    do not modify boundary cell values or face fluxes.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, BCType.MIXED)

    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):
        # Initialize boundary cells by copying adjacent owner cell values.
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face = fvpatch.start_idx_face[self.patch_idx]
        end_idx_face = fvpatch.end_idx_face[self.patch_idx]

        neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            uvw_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:3] = uvw_cpd_cell[owner_cells, 0:3]

        return uvw_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        # No-op: do not enforce anything for calculated BC
        return fvfield
