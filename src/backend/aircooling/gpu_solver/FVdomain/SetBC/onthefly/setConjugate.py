"""Conjugate heat transfer: the temperature condition that joins a fluid region and a solid region (CHT-0a,
docs/research/2026-10-15-cht-heatsink.md).

The two regions are two disconnected components of ONE mesh (the OpenFOAM region meshes merged without stitching);
the interface appears twice, as a patch of each component, face for face coincident. Both patches carry this condition
with ``partner_patch`` naming the other one (``type = "conjugateTemperature"`` in bc/T.toml). Each side's ghost -- the
value at the face centre -- is the interface temperature that makes the two discrete normal fluxes equal:

    T_Gamma = (a_1 delta_1 T_1 + a_2 delta_2 T_2) / (a_1 delta_1 + a_2 delta_2)

with a the face diffusivity the energy residual uses on that patch face (``_alpha_eff_face`` / ``alpha_coeff``: the
conductivity of that side divided by the fluid's rho c_p) and delta the face's ``nonOrthDeltaCoeffs``
(1 / max(n . d, 0.05 |d|), the owner-to-face-centre distance the boundary snGrad divides by). A COUPLED condition is
not first-type, so the boundary snGrad correction is zero on these faces and snGrad = (T_Gamma - T_owner) delta
exactly: a_1 snGrad_1 + a_2 snGrad_2 = 0 to rounding (flux continuity), and both patches hold the same T_Gamma.

Like the rack discharge (``setRack.Rackoutlettemperature``) the condition reads the two OWNER cells only, so it is
independent of the order in which the conditions run, and it declares ``couples_cell_pairs``: the Jacobian assembler
adds the pair edge (owner, partner owner) to the colouring pattern, and the JVP is exact through the ghost.
"""
from __future__ import annotations

from typing import Dict

import torch

from FVdomain.SetBC.onthefly.BCbase import BoundaryCondition, BCType, VARType
from FVdomain.SetBC.onthefly.Parafunction import Functionbase, ParametricManager
from FVdomain.SetBC.onthefly.setRack import _partner_patch_idx, _patch_scalar_to_int


def coincident_face_permutation(source_pos: torch.Tensor, target_pos: torch.Tensor, tol_rel: float = 1e-6):
    """``perm`` with ``target_pos[perm] == source_pos`` up to ``tol_rel`` of the patch extent (both [n, 3]).

    Faces are matched by their centres, rounded to a grid of the tolerance and sorted lexicographically on both sides;
    the match is then checked face by face, so a non-conformal interface is refused, never paired approximately."""
    if source_pos.shape != target_pos.shape:
        raise ValueError(f"conjugate interface: {source_pos.shape[0]} faces against {target_pos.shape[0]}")
    n = source_pos.shape[0]
    if n == 0:
        return torch.zeros(0, dtype=torch.long, device=source_pos.device)
    lo = torch.minimum(source_pos.min(0).values, target_pos.min(0).values)
    hi = torch.maximum(source_pos.max(0).values, target_pos.max(0).values)
    scale = float((hi - lo).max())
    tol = tol_rel * max(scale, 1e-300)

    def order(pos):
        key = torch.round((pos - lo) / tol).to(torch.long)
        idx = torch.arange(n, device=pos.device)
        for c in (2, 1, 0):                         # stable sorts: lexicographic in (x, y, z)
            idx = idx[torch.sort(key[idx, c], stable=True).indices]
        return idx

    s, t = order(source_pos.to(torch.float64)), order(target_pos.to(torch.float64))
    perm = torch.empty(n, dtype=torch.long, device=source_pos.device)
    perm[s] = t
    gap = float((target_pos[perm].to(torch.float64) - source_pos.to(torch.float64)).abs().max())
    if gap > 10.0 * tol:
        raise ValueError(f"conjugate interface is not conformal: largest face-centre gap {gap:.3e} "
                         f"(tolerance {tol:.3e})")
    return perm


class Conjugatetemperature(BoundaryCondition):
    """The fluid-solid interface temperature (module docstring). ``partner_patch``: the other side's patch."""

    couples_cell_pairs = True
    has_global_reduction = False

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                 parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.COUPLED)
        self.is_coupled = True
        self.partner_patch_name, self.partner_patch_idx = _partner_patch_idx(boundary_info, patch_name)
        self.value = boundary_info.get("value", {"value_type": "uniform", "val": 0.0})
        if parametric_manager is not None and "value" in boundary_info:
            self.value = parametric_manager.resolve_value(boundary_info["value"])
        self.tol_rel = float(boundary_info.get("pairingTolerance", 1e-6))
        self._pair_cache = None
        # mesh schema v2: the pairing the parser stored (OpenFOAM's mapped patches); None -> pair by face centres
        schema = boundary_info.get("region_schema")
        self.stored_perm = None
        if schema is not None and boundary_info.get("patch_dict"):
            f, _side = schema.interface_of_patch(patch_name)
            if f is not None:
                partner = f["patch_b"] if f["patch_a"] == patch_name else f["patch_a"]
                if partner != self.partner_patch_name:
                    raise ValueError(f"conjugate condition on '{patch_name}': partner_patch "
                                     f"'{self.partner_patch_name}', the mesh's interface pairs it with '{partner}'")
                self.stored_perm = torch.as_tensor(schema.partner_local_order(patch_name, boundary_info["patch_dict"]))

    def init_field(self, t_cpd_cell, fvpatch, **kwargs):
        start = _patch_scalar_to_int(fvpatch.start_idx_cpd_cell[self.patch_idx])
        end = _patch_scalar_to_int(fvpatch.end_idx_cpd_cell[self.patch_idx])
        t_cpd_cell[..., start:end, 0:1] = Functionbase(value_type=self.value["value_type"], value=self.value["val"],
                                                       num_element=end - start, channel_dim=1
                                                       ).eval(device=t_cpd_cell.device)
        return t_cpd_cell

    def _pairing(self, integrator, patch_idx_offset: int):
        """(own faces, partner faces in own order, own owner, own ghost, partner owner): geometry, cached on the
        patch batch object."""
        fvpatch_batch = integrator.fvpatch_batch
        if self._pair_cache is not None and self._pair_cache[0] is fvpatch_batch:
            return self._pair_cache[1]
        own = self.patch_idx + patch_idx_offset
        par = self.partner_patch_idx + patch_idx_offset
        s0 = _patch_scalar_to_int(fvpatch_batch.start_idx_face[own])
        s1 = _patch_scalar_to_int(fvpatch_batch.end_idx_face[own])
        t0 = _patch_scalar_to_int(fvpatch_batch.start_idx_face[par])
        t1 = _patch_scalar_to_int(fvpatch_batch.end_idx_face[par])
        if self.stored_perm is not None:
            perm = self.stored_perm.to(integrator.face_pos.device)
            if perm.shape[0] != s1 - s0:
                raise ValueError(f"conjugate interface '{self.patch_name}': stored pairing has {perm.shape[0]} faces, "
                                 f"the patch {s1 - s0}")
            # the stored pairing must still be face-for-face coincident (same check as the geometric pairing)
            own_pos = integrator.face_pos[s0:s1].to(torch.float64)
            par_pos = integrator.face_pos[t0:t1].to(torch.float64)[perm]
            ext = float(torch.cat([own_pos, par_pos]).amax(0).sub(torch.cat([own_pos, par_pos]).amin(0)).max())
            gap = float((own_pos - par_pos).abs().max()) if perm.numel() else 0.0
            if gap > 10.0 * self.tol_rel * max(ext, 1e-300):
                raise ValueError(f"conjugate interface '{self.patch_name}': stored pairing is not coincident "
                                 f"(largest face-centre gap {gap:.3e})")
        else:
            perm = coincident_face_permutation(integrator.face_pos[s0:s1], integrator.face_pos[t0:t1], self.tol_rel)
        pair_index = integrator.neighbor_cpd_cell.long()
        own_owner, own_ghost = pair_index[:, s0:s1]
        par_owner = pair_index[0, t0:t1].index_select(0, perm)
        own_faces = torch.arange(s0, s1, device=perm.device)
        par_faces = (t0 + perm).to(own_faces.device)
        pairing = (own_faces, par_faces, own_owner, own_ghost, par_owner)
        self._pair_cache = (fvpatch_batch, pairing)
        return pairing

    def cell_pairs(self, integrator, patch_idx_offset: int):
        """(own owner, partner owner) interior-cell pairs, for the Jacobian assembler's pattern."""
        _f, _pf, own_owner, _g, par_owner = self._pairing(integrator, patch_idx_offset)
        return own_owner, par_owner

    def ghost_second_source(self, integrator, patch_idx_offset: int):
        """(own ghost, partner owner) compound-cell ids: the second cell each ghost of this patch reads besides its
        own owner (the Warp backend's two-source ghost map, CHT-0b)."""
        _f, _pf, _own_owner, own_ghost, par_owner = self._pairing(integrator, patch_idx_offset)
        return own_ghost, par_owner

    @staticmethod
    def _face_diffusivity(integrator):
        a = getattr(integrator, "_alpha_eff_face", None)
        return a if a is not None else integrator.alpha_coeff

    @staticmethod
    def _delta(integrator, faces, owner):
        d = getattr(integrator, "nonOrthDeltaCoeffs", None)
        if d is not None:
            return d.reshape(-1, 1)[faces]
        # the same formula as FVInterpolation: 1 / max(n . (x_f - x_P), 0.05 |x_f - x_P|)
        vec = integrator.face_pos[faces] - integrator.cpd_cell_pos[owner]
        n_dot_d = (vec * integrator.unv_face[faces]).sum(-1, keepdim=True)
        return 1.0 / torch.clamp(n_dot_d, min=0.05 * vec.norm(dim=-1, keepdim=True))

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        own_faces, par_faces, own_owner, own_ghost, par_owner = self._pairing(integrator, patch_idx_offset)
        t = fvfield["t_cpd_cell"]
        t_own = t[..., own_owner, 0:1]
        t_par = t[..., par_owner, 0:1]
        alpha = self._face_diffusivity(integrator).reshape(-1, 1)
        w_own = alpha[own_faces] * self._delta(integrator, own_faces, own_owner)
        w_par = alpha[par_faces] * self._delta(integrator, par_faces, par_owner)
        t_gamma = (w_own * t_own + w_par * t_par) / (w_own + w_par)
        t[..., own_ghost, 0:1] = t_gamma.to(t.dtype)
        fvfield["t_cpd_cell"] = t
        return fvfield
