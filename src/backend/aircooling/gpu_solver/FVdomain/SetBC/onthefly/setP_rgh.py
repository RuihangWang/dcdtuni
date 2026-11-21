"""
Boundary condition implementations for p_rgh field (pressure minus hydrostatic pressure).
"""

import torch
from typing import Optional, Union, List, Any, Dict
from FVdomain.SetBC.onthefly.Parafunction import Functionbase, ParametricManager
from FVdomain.SetBC.onthefly.BCbase import BoundaryCondition, VARType, BCType
from FVdomain.SetBC.onthefly.setCyclic import apply_cyclic_face_values


class Empty(BoundaryCondition):
    """
    Empty boundary condition for p_rgh field.
    Used for 2D simulations - does not participate in flux calculations.

    For GNN+FVM architecture:
    - Ghost cells are copied from owner cells to ensure proper GNN message passing
    - This prevents ghost cells from having random/unphysical values
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.PRESSURE_RGH, BCType.EMPTY)

    def init_field(self, p_rgh_cpd_cell, fvpatch, **kwargs):
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
            p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, :] = p_rgh_cpd_cell[owner_cells, :]

        return p_rgh_cpd_cell

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
        if "p_rgh_cpd_cell" in fvfield:
            p_rgh_cpd_cell = fvfield["p_rgh_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            # Copy owner cell values to ghost cells
            p_rgh_cpd_cell[..., neighbor_cells, :] = p_rgh_cpd_cell[..., owner_cells, :]
            fvfield["p_rgh_cpd_cell"] = p_rgh_cpd_cell

        return fvfield


class Cyclic(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.PRESSURE_RGH, BCType.CYCLIC)

    def init_field(self, p_rgh_cpd_cell, fvpatch, **kwargs):

        neighbor_cpd_cell = kwargs.get("neighbor_cpd_cell", None)
        if neighbor_cpd_cell is None:
            raise ValueError("No neighbor_cpd_cell provided")

        return apply_cyclic_face_values(
            p_rgh_cpd_cell,
            fvpatch,
            neighbor_cpd_cell,
            self.patch_idx,
        )

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        p_rgh_cpd_cell = fvfield.get("p_rgh_cpd_cell", None)
        grad_p_rgh_cpd_cell = fvfield.get("grad_p_rgh_cpd_cell", None)

        p_rgh_cpd_cell = apply_cyclic_face_values(
            p_rgh_cpd_cell,
            fvpatch_batch,
            neighbor_cpd_cell,
            global_patch_idx,
        )
        grad_p_rgh_cpd_cell = apply_cyclic_face_values(
            grad_p_rgh_cpd_cell,
            fvpatch_batch,
            neighbor_cpd_cell,
            global_patch_idx,
            gradient=True,
        )

        fvfield["p_rgh_cpd_cell"] = p_rgh_cpd_cell
        fvfield["grad_p_rgh_cpd_cell"] = grad_p_rgh_cpd_cell

        return fvfield


class Porousbafflepressure(Cyclic):
    """``porousBafflePressure`` on a cyclic baffle pair (OpenFOAM v2406/v2412,
    ``porousBafflePressureFvPatchField.C::updateCoeffs``): a pressure jump across the baffle

        J = -sign(Un) (D nu + 0.5 I |Un|) |Un| length = -(D nu + 0.5 I |Un|) Un length

    with Un the face-normal velocity of the OWNER side of the pair (the lower patch index,
    ``cyclicPolyPatch::owner``). ``fixedJumpFvPatchField::jump`` returns the owner side's jump on both
    sides, and ``jumpCyclicFvPatchField::patchNeighbourField`` gives the owner side the neighbour value
    ``p_partner - J`` and the other side ``p_partner + J``. The ghost row of this repository's cyclic
    face IS that neighbour value (``apply_cyclic_face_values`` copies the partner owner), so the jump is
    subtracted / added on the ghost and every operator reading the ghost (pressure gradient, MWI flux)
    sees it.

    Deviations, both recorded: Un is the normal component of the mean of the two owner velocities, not
    the converged face flux phi/|Sf| (the flux is not available to a BC pass; at a converged state the two
    agree to interpolation order); OpenFOAM's under-relaxation of the jump (``relax``) is a segregated-solver
    device and is not applied (the steady fixed point is the same). ``D`` multiplies the laminar nu of
    the case (the turbulence model's ``nu()`` in OpenFOAM); dctwin baffles carry D = 0.
    """
    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, boundary_info, parametric_manager)
        res = (parametric_manager.resolve_value if parametric_manager is not None else (lambda v: v))
        def _num(key, default):
            v = res(boundary_info.get(key, default))
            if isinstance(v, dict):
                v = v.get("val", default)
            return float(v)
        self.I = _num("I", 0.0)
        self.D = _num("D", 0.0)
        self.length = _num("length", 1.0)
        self.nu = _num("nu", 0.0)

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        fvfield = super().eval(fvfield, integrator, patch_idx_offset, **kwargs)
        from FVdomain.SetBC.onthefly.setCyclic import get_cyclic_patch_pair_indices, _patch_scalar_to_int
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell
        pairing = getattr(fvpatch_batch, "neighbor_patch_idx", None)
        if pairing is None:
            pairing = fvpatch_batch.neighbour_patch_idx
        partner_idx = _patch_scalar_to_int(pairing[global_patch_idx])
        is_owner = global_patch_idx < partner_idx
        own_patch = global_patch_idx if is_owner else partner_idx
        o_owner, _o_ghost, o_partner, _ = get_cyclic_patch_pair_indices(fvpatch_batch, neighbor_cpd_cell, own_patch)
        start = _patch_scalar_to_int(fvpatch_batch.start_idx_face[own_patch])
        end = _patch_scalar_to_int(fvpatch_batch.end_idx_face[own_patch])
        n = integrator.unv_face[start:end]
        uvw = fvfield["uvw_cpd_cell"]
        u_face = 0.5 * (uvw[..., o_owner, 0:3] + uvw[..., o_partner, 0:3])
        Un = torch.sum(u_face * n.to(u_face.dtype), dim=-1, keepdim=True)
        J = -(self.D * self.nu + 0.5 * self.I * torch.abs(Un)) * Un * self.length
        _s_owner, ghost, _t_owner, _ = get_cyclic_patch_pair_indices(fvpatch_batch, neighbor_cpd_cell, global_patch_idx)
        p = fvfield["p_rgh_cpd_cell"]
        # Faces of the two halves correspond by index (get_cyclic_patch_pair_indices checks equal counts).
        p[..., ghost, :] = p[..., ghost, :] + (-J if is_owner else J)
        fvfield["p_rgh_cpd_cell"] = p
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
        # Optional per-patch switch (bc/p_rgh.toml: detach_owner = true).
        # Severs the backprop path through the owner-layer p of this Dirichlet
        # patch: forward values unchanged, but the loss cannot push owner p.
        # Rationale: the vortex-vs-Dirichlet conflict at the outlet writes an
        # odd-even alternating p pattern through the owner layer (the residual
        # cannot see the accumulated 2dx mode, so it is never erased).
        self.detach_owner = bool(boundary_info.get("detach_owner", False))
        self._detach_owner_announced = False
        super().__init__(patch_name, patch_idx, VARType.PRESSURE_RGH, BCType.FIRST)

    def init_field(self, p_rgh_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]

        # Apply to boundary cells (ghost cells)
        self.value_tensor = Functionbase(
            value_type=self.value["value_type"], 
            value=self.value["val"], 
            num_element=end_idx_cpd_cell - start_idx_cpd_cell,
            channel_dim=1  # p_rgh是标量
        )

        p_rgh_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = self.value_tensor.eval(device=p_rgh_cpd_cell.device)

        return p_rgh_cpd_cell

    def eval(self, fvfield:dict, integrator, patch_idx_offset: int, **kwargs):

        global_patch_idx = self.patch_idx + patch_idx_offset

        fvpatch_batch = integrator.fvpatch_batch

        p_rgh_cpd_cell = fvfield["p_rgh_cpd_cell"]

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch_batch.start_idx_cpd_cell[global_patch_idx], fvpatch_batch.end_idx_cpd_cell[global_patch_idx]
        start_idx_face, end_idx_face = fvpatch_batch.start_idx_face[global_patch_idx], fvpatch_batch.end_idx_face[global_patch_idx]
        
        # Apply to ghost cells only (Dirichlet BC must not overwrite interior cells)
        p_rgh_cpd_cell[..., start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = self.value_tensor.eval(device=p_rgh_cpd_cell.device)

        if self.detach_owner and torch.is_grad_enabled() and p_rgh_cpd_cell.requires_grad:
            owner_cells = integrator.neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            p_rgh_cpd_cell[..., owner_cells, 0:1] = p_rgh_cpd_cell[..., owner_cells, 0:1].detach()
            if not self._detach_owner_announced:
                print(f"[setP_rgh.Fixedvalue] patch '{self.patch_name}': detach_owner ACTIVE "
                      f"({end_idx_face - start_idx_face} owner cells severed from backprop)")
                self._detach_owner_announced = True

        fvfield["p_rgh_cpd_cell"] = p_rgh_cpd_cell

        return fvfield


''' >>> SecondTypeBoundary >>> '''
class Zerogradient(BoundaryCondition):

    def __init__(self, patch_name:str, patch_idx:int, boundary_info:Dict, parametric_manager: ParametricManager = None):
        # 其中boundary_info是来自toml文件里面patch_name与patch_dict里面对应额部分
        super().__init__(patch_name, patch_idx, VARType.PRESSURE_RGH, BCType.MIXED)

    def init_field(self, p_rgh_cpd_cell, fvpatch, **kwargs):

        start_idx_cpd_cell, end_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx], fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face, end_idx_face = fvpatch.start_idx_face[self.patch_idx], fvpatch.end_idx_face[self.patch_idx]

        # Zero gradient BC: initialize boundary cells by copying from owner cells
        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = p_rgh_cpd_cell[owner_cells, 0:1]
        else:
            p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = 0.0

        return p_rgh_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply zero gradient boundary condition for p_rgh.

        For GNN+FVM architecture, this BC has two roles:
        1. Update ghost cell values (copy from owner) - for GNN and gradient reconstruction
        2. Set gradient flux to zero - for pressure gradient term in FVM

        Args:
            fvfield: A Dict that may contain "p_rgh_cpd_cell" and/or "grad_p_rgh_flux_face"
            **kwargs: Additional parameters
        """

        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Role 1: Update ghost cell values (ALWAYS do this for GNN+FVM)
        if "p_rgh_cpd_cell" in fvfield:
            p_rgh_cpd_cell = fvfield["p_rgh_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            p_rgh_cpd_cell[..., neighbor_cells, :] = p_rgh_cpd_cell[..., owner_cells, :]
            fvfield["p_rgh_cpd_cell"] = p_rgh_cpd_cell

        if "grad_p_rgh_flux_face" in fvfield:
            fvfield["grad_p_rgh_flux_face"][..., start_idx_face:end_idx_face, :] = 0.0

        return fvfield


''' >>> MixedBoundary >>> '''
class Fixedfluxpressure(BoundaryCondition):
    """
    Fixed flux pressure boundary condition for p_rgh.

    Hard enforcement performed by eval():
    - Ghost cell values: copy from owner cell (zeroGradient-like, so the GNN
      stencil and the gradient reconstruction see a sane boundary value)
    - Face gradient flux: overwritten with the buoyancy target
      snGrad(p_rgh)|wall = ghf · β · snGrad(T), falling back to 0 on the first
      BC pass while grad_t_flux_face is not yet populated.

    Note: In OpenFOAM's iterative solvers, fixedFluxPressure adjusts snGrad(p) to ensure
    flux consistency with the momentum predictor. Here the same wall snGrad target is
    imposed directly, while the interior pressure field is driven by the continuity
    residual in the loss rather than by a SIMPLE/PISO correction step.

    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        if parametric_manager is not None:
            self.value = parametric_manager.resolve_value(boundary_info.get("value", {"value_type": "uniform", "val": 0.0}))
        else:
            self.value = boundary_info.get("value", {"value_type": "uniform", "val": 0.0})
        super().__init__(patch_name, patch_idx, VARType.PRESSURE_RGH, BCType.MIXED)

    def init_field(self, p_rgh_cpd_cell, fvpatch, **kwargs):
        """Initialize ghost cells with specified value (default: 0)."""
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]

        value_tensor = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end_idx_cpd_cell - start_idx_cpd_cell,
            channel_dim=1
        ).eval(device=p_rgh_cpd_cell.device)

        p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = value_tensor
        return p_rgh_cpd_cell

    @staticmethod
    def _buoyancy_snGrad_p_rgh(integrator, s_f, e_f, snGrad_T):
        """OpenFOAM constrainPressure() target for p_rgh on walls.

        Derived from buoyantBoussinesqPimpleFoam's pEqn.H:
            phig = -rAUf·ghf·snGrad(rhok)·magSf
            snGrad(p_rgh)|wall = phig/(magSf·rAUf) = -ghf·snGrad(rhok)
                               = ghf·β·snGrad(T)    (rhok = 1 - β(T-TRef))

        Returns (N_bdy, 1) scalar field to assign as the face-normal p_rgh gradient.
        """
        batch_face_bdy = integrator.batch_face[s_f:e_f]
        g_bdy = integrator.gravity[batch_face_bdy]                    # (N_bdy, 3)
        hRef_bdy = integrator.hRef[batch_face_bdy]                    # (N_bdy, 1)
        beta_bdy = integrator.beta[batch_face_bdy]                    # (N_bdy, 1)
        face_pos_bdy = integrator.face_pos[s_f:e_f]                   # (N_bdy, 3)
        ghf = (face_pos_bdy * g_bdy).sum(dim=1, keepdim=True) \
              - torch.norm(g_bdy, dim=1, keepdim=True) * hRef_bdy      # (N_bdy, 1)
        return ghf * beta_bdy * snGrad_T                              # (N_bdy, 1)

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """Apply fixedFluxPressure BC with OpenFOAM-correct buoyancy correction.

        Ghost cell values: copy from owner (zeroGradient for GNN stencil support).
        Face gradient flux: snGrad(p_rgh)|wall = ghf · β · snGrad(T), matching
        OpenFOAM buoyantBoussinesqPimpleFoam/constrainPressure. Falls back to 0
        on the first BC pass when grad_t_flux_face is not yet populated.
        """
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Update ghost cell values: copy from owner (zeroGradient for GNN stencil)
        if "p_rgh_cpd_cell" in fvfield:
            p_rgh_cpd_cell = fvfield["p_rgh_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]
            p_rgh_cpd_cell[..., neighbor_cells, :] = p_rgh_cpd_cell[..., owner_cells, :]
            fvfield["p_rgh_cpd_cell"] = p_rgh_cpd_cell

        if "grad_p_rgh_flux_face" in fvfield:
            if "grad_t_flux_face" in fvfield:
                # snGrad(T_new) at face (col 0 of grad_t_flux_face; col 1 = T_old)
                #
                # CLONED, and the clone is load-bearing.  The slice is a VIEW of
                # grad_t_flux_face, the multiply below saves it for backward, and
                # a later boundary condition on this same patch zeroes that slice
                # IN PLACE -- which is how this repository imposes zero-gradient.
                # The saved view is then stale and autograd refuses: "modified by
                # an inplace operation: [256, 1] ... is at version 4; expected
                # version 2" (the 256 is adiabaticWalls; measured on
                # the Ra = 1e5 cavity).
                #
                # It never fired before because of WHICH derivative needs the
                # saved value.  d/d(snGrad_T) is ghf*beta and reads nothing that
                # was saved, so every state derivative -- the Jacobian, the JVP,
                # the whole Newton path -- is unaffected.  d/d(beta) is
                # ghf*snGrad_T and does read it, so the defect is invisible until
                # someone differentiates with respect to a PARAMETER, which is
                # exactly what CoupledNewton.newton.differentiable_solution does.
                # The clone changes no value and costs one face-sized copy.
                snGrad_T_bdy = fvfield["grad_t_flux_face"][
                    ..., start_idx_face:end_idx_face, 0:1].clone()
                fvfield["grad_p_rgh_flux_face"][..., start_idx_face:end_idx_face, :] = \
                    self._buoyancy_snGrad_p_rgh(integrator, start_idx_face, end_idx_face, snGrad_T_bdy)
            else:
                # First BC pass: grad_t_flux_face not populated yet → zero fallback
                fvfield["grad_p_rgh_flux_face"][..., start_idx_face:end_idx_face, :] = 0.0

        return fvfield


class Naturaloutlet(BoundaryCondition):
    """
    Natural outlet boundary condition for p_rgh (Robin-type, flux-only).

    WARNING: This boundary condition implementation has known errors and is currently disabled.
    Please use an alternative BC (e.g., fixedvalue, zerogradient, fixedfluxpressure) in your
    toml configuration file instead.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        raise NotImplementedError(
            f"[p_rgh] 'naturaloutlet' boundary condition on patch '{patch_name}' is currently disabled "
            f"due to known implementation errors. Please change the BC type for this patch in your "
            f"toml configuration file. Recommended alternatives: 'fixedvalue', 'zerogradient', or 'fixedfluxpressure'."
        )

    def init_field(self, *args, **kwargs):
        """Unreachable: __init__ raises NotImplementedError before this is ever called."""
        raise NotImplementedError("Naturaloutlet is disabled.")

    def eval(self, *args, **kwargs):
        """Unreachable: __init__ raises NotImplementedError before this is ever called."""
        raise NotImplementedError("Naturaloutlet is disabled.")


# Aliases for common boundary condition names
# fixedValue = fixedvalue
# uniformFixedValue = uniformfixedvalue
# zeroGradient = zerogradient
# fixedFluxPressure = fixedfluxpressure (now implemented above)
# totalPressure = totalpressure
# porousBafflePressure = porousbafflepressure
# freestreamPressure = freestreampressure


class Symmetry(BoundaryCondition):
    """
    Symmetry boundary condition for p_rgh field.

    Physics:
    - Zero gradient of pressure normal to the symmetry plane (∂p_rgh/∂n = 0)
    - This is physically correct because the pressure field should be symmetric
      about the symmetry plane.

    For GNN+FVM architecture:
    - Ghost cells are copied from owner cells to ensure proper GNN message passing
    - Gradient flux is set to zero at the boundary faces
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.PRESSURE_RGH, BCType.MIXED)

    def init_field(self, p_rgh_cpd_cell, fvpatch, **kwargs):
        """
        Initialize symmetry boundary ghost cells by copying from owner cells.
        """
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face = fvpatch.start_idx_face[self.patch_idx]
        end_idx_face = fvpatch.end_idx_face[self.patch_idx]

        # Zero gradient BC: initialize boundary cells by copying from owner cells
        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = p_rgh_cpd_cell[owner_cells, 0:1]
        else:
            p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = 0.0

        return p_rgh_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply symmetry boundary condition for p_rgh.

        For symmetry plane, pressure has zero normal gradient:
        ∂p_rgh/∂n = 0

        This is implemented by:
        1. Copying owner cell values to ghost cells (ensures p_ghost = p_owner)
        2. Setting gradient flux to zero at boundary faces
        """
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Role 1: Update ghost cell values (ALWAYS do this for GNN+FVM)
        if "p_rgh_cpd_cell" in fvfield:
            p_rgh_cpd_cell = fvfield["p_rgh_cpd_cell"]
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

            p_rgh_cpd_cell[..., neighbor_cells, :] = p_rgh_cpd_cell[..., owner_cells, :]
            fvfield["p_rgh_cpd_cell"] = p_rgh_cpd_cell

        if "grad_p_rgh_flux_face" in fvfield:
            fvfield["grad_p_rgh_flux_face"][..., start_idx_face:end_idx_face, :] = 0.0

        return fvfield


class Calculated(BoundaryCondition):
    """
    Calculated boundary condition for p_rgh field.

    This BC does NOT impose any explicit boundary condition on p_rgh.
    It follows the PINN/ODIL philosophy where pressure doesn't need physical BC
    on walls - only a gauge (reference point) is needed to remove the constant
    degree of freedom.

    Physical meaning:
    - For enclosed natural convection, walls only need velocity BC (no-slip)
      and temperature BC (fixed T or zero gradient for adiabatic).
    - Pressure is NOT a physical boundary condition on walls.
    - The pressure field is determined by:
      1. The continuity constraint (∇·U = 0)
      2. A pressure reference constraint on the volume-averaged p_rgh

    For GNN+FVM architecture:
    - Ghost cells are copied from owner cells for gradient reconstruction stability
    - No modification is made to gradient fluxes (they are computed naturally)

    Reference: OpenFOAM documentation for "calculated" BC type.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict, parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.PRESSURE_RGH, BCType.MIXED)

    def init_field(self, p_rgh_cpd_cell, fvpatch, **kwargs):
        """
        Initialize boundary ghost cells by copying from owner cells.
        This provides reasonable initial values without imposing physical constraints.
        """
        start_idx_cpd_cell = fvpatch.start_idx_cpd_cell[self.patch_idx]
        end_idx_cpd_cell = fvpatch.end_idx_cpd_cell[self.patch_idx]
        start_idx_face = fvpatch.start_idx_face[self.patch_idx]
        end_idx_face = fvpatch.end_idx_face[self.patch_idx]

        # Copy owner cell values to ghost cells for stability
        neighbor_cpd_cell = kwargs.get('neighbor_cpd_cell', None)
        if neighbor_cpd_cell is not None:
            owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
            p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = p_rgh_cpd_cell[owner_cells, 0:1]
        else:
            p_rgh_cpd_cell[start_idx_cpd_cell:end_idx_cpd_cell, 0:1] = 0.0

        return p_rgh_cpd_cell

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        """
        Apply calculated boundary condition for p_rgh.

        This BC does the minimum necessary for numerical stability:
        1. Copy owner cell values to ghost cells (for gradient reconstruction)
        2. Do NOT modify gradient fluxes (let them be computed naturally)

        The key insight from PINN/ODIL: for enclosed cavities, p_rgh doesn't
        need a physical BC. The pressure field is fully determined by:
        - Continuity equation (∇·U = 0)
        - The pressure reference loss, which drives the volume-weighted average
          p_rgh toward pRefValue (it is not a single-cell pin)

        Args:
            fvfield: Dictionary containing field variables
            integrator: FVM integrator with mesh geometry
            patch_idx_offset: Offset for global patch indexing
        """
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        neighbor_cpd_cell = integrator.neighbor_cpd_cell

        start_idx_face = fvpatch_batch.start_idx_face[global_patch_idx]
        end_idx_face = fvpatch_batch.end_idx_face[global_patch_idx]

        # Role 1: Update ghost cell values for GNN/gradient reconstruction stability
        # if "p_rgh_cpd_cell" in fvfield:
        #     p_rgh_cpd_cell = fvfield["p_rgh_cpd_cell"]
        #     owner_cells = neighbor_cpd_cell[0, start_idx_face:end_idx_face]
        #     neighbor_cells = neighbor_cpd_cell[1, start_idx_face:end_idx_face]

        #     # Copy owner cell values to ghost cells
        #     p_rgh_cpd_cell[neighbor_cells] = p_rgh_cpd_cell[owner_cells]
        #     fvfield["p_rgh_cpd_cell"] = p_rgh_cpd_cell

        # Role 2: Do NOT modify gradient fluxes (calculated BC)
        return fvfield
