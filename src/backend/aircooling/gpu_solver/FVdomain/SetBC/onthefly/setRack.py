"""Cross-patch rack black-box boundary conditions (Bai et al. 2024, IBBM).

The rack is EXCLUDED from the computational domain.  Its two air faces become
domain boundaries that are COUPLED to each other:

* the rack INTAKE face is where air leaves the domain (a domain outflow),
* the rack DISCHARGE face is where air re-enters (a domain inflow),

and "inlet"/"outlet" below always name the RACK's frame, matching the paper.

Equations, quoted by number from
Bai F., Gong X.-M., Li H.-W., Guo H.-B., Tao W.-Q., "An improved black box
model and the details of its numerical treatments for rack in data center
simulation", Int. Comm. Heat Mass Transfer 158 (2024) 107916:

    Eq. 11  q_m = N rho q_V                    rack mass flow
    Eq. 12  pointwise mass-flux conservation between the paired faces
    Eq. 13  u^n = q_V / A_server,in            intake NORMAL velocity prescribed
    Eq. 14  d u^tau / dn = 0                   intake TANGENTIAL zero-gradient
    Eq. 16  Q = cp q_m dT                      the server temperature rise
    Eq. 17  T_out(x,z) = T_in(x,z) + dT        IBBM, POINTWISE (what we ship)
    Eq. 15  T_out = mean(T_in) + dT            CBBM, the conventional model

Eq. 17 is implemented; Eq. 15 is available only as a diagnostic and refuses to
run under an assembled Jacobian, because a patch mean is a global reduction
whose derivative is a dense rank-1 block that no mesh-hop colouring contains
(see docs/research/2026-08-17-dc-rack-bc-design.md section 4).

Pairing differs from `setCyclic`.  A cyclic pair is coincident modulo a rigid
transform and the mesh generator guarantees the two patches' face ORDER
corresponds; a rack pair is separated by the rack depth and nothing orders its
faces, so the correspondence is rebuilt here by matching face centres in the
plane transverse to the rack axis, then gated as a bijection.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import torch

from FVdomain.SetBC.onthefly.BCbase import BoundaryCondition, VARType, BCType
from FVdomain.SetBC.onthefly.Parafunction import Functionbase, ParametricManager
from FVdomain.SetBC.onthefly.setCyclic import _patch_scalar_to_int


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------
def rack_through_direction(
    source_face_pos: torch.Tensor,
    target_face_pos: torch.Tensor,
    source_face_normal: Optional[torch.Tensor] = None,
) -> torch.Tensor:
    """Unit vector along the rack: the source patch's mean face normal.

    The NORMAL is used, not the centroid offset, even though the offset looks
    like the more obvious choice.  An offset absorbs whatever transverse
    misalignment the two patches have -- projecting along it then makes ANY two
    patches overlap, so a toml naming the wrong partner is accepted and the
    faces are paired at a skew.  Measured on the fixture: two patches 9 units
    apart transversally paired without complaint under the offset rule and are
    rejected under this one (``test_pairing_rejects_non_overlapping_patches``).
    A rack's intake face normal IS its axis, so nothing is given up.

    The centroid offset is the fallback for callers with no normal to hand
    (the pure-geometry unit tests), and it is exact whenever the two patches
    are true transverse copies.
    """
    if source_face_normal is not None:
        mean_normal = source_face_normal.mean(dim=0)
        norm = torch.linalg.norm(mean_normal)
        if norm > 1e-12:
            return mean_normal / norm

    offset = target_face_pos.mean(dim=0) - source_face_pos.mean(dim=0)
    norm = torch.linalg.norm(offset)
    if norm <= 1e-12:
        raise ValueError(
            "rack patches share a centroid and carry no usable mean face "
            "normal, so the through-rack direction is undetermined")
    return offset / norm


def rack_face_permutation(
    source_face_pos: torch.Tensor,
    target_face_pos: torch.Tensor,
    *,
    source_face_normal: Optional[torch.Tensor] = None,
    tol_rel: float = 1e-3,
) -> torch.Tensor:
    """``perm`` with ``target[perm[i]]`` facing ``source[i]`` across the rack.

    Eq. 17 is a POINTWISE statement, so it needs a face-to-face correspondence
    that the mesh does not provide for two patches the generator wrote
    independently.  Face centres are projected onto the plane orthogonal to the
    rack axis and matched by nearest neighbour there; the result is then gated
    twice, on being a bijection and on the residual transverse offset, so a
    mismatched pair fails loudly instead of silently transposing the rack.

    ``tol_rel`` is relative to the transverse extent of the source patch.
    """
    if source_face_pos.shape[0] != target_face_pos.shape[0]:
        raise ValueError(
            f"rack patch face count mismatch: {source_face_pos.shape[0]} against "
            f"{target_face_pos.shape[0]}; a pointwise (Eq. 17) rack model needs "
            "one intake face per discharge face")

    # The whole projection runs in float64, AXIS INCLUDED.  Promoting only the
    # positions raised "expected scalar type Double but found Float" the first
    # time this met a real mesh (the DC room, float32 geometry); every fixture
    # is float64 end to end, so the mismatch could not appear there.  Matching a
    # face centre across a rack is a difference of large coordinates, which is
    # the arithmetic that wants the wider type in the first place.
    axis = rack_through_direction(
        source_face_pos.double(), target_face_pos.double(),
        None if source_face_normal is None else source_face_normal.double())

    def transverse(points: torch.Tensor) -> torch.Tensor:
        return points - (points @ axis).unsqueeze(-1) * axis

    source_t = transverse(source_face_pos.double())
    target_t = transverse(target_face_pos.double())

    distance = torch.cdist(source_t, target_t)
    residual, perm = distance.min(dim=1)

    # Distance gate BEFORE the bijection gate: a wrongly named partner trips
    # both, and "these patches do not overlap" points at the toml while "not a
    # bijection" points at the mesh.
    extent = source_t.max(dim=0).values - source_t.min(dim=0).values
    scale = float(torch.linalg.norm(extent))
    if scale <= 0.0:
        scale = 1.0
    worst = float(residual.max())
    if worst > tol_rel * scale:
        raise ValueError(
            f"rack face pairing residual {worst:.3e} exceeds {tol_rel:.1e} x the "
            f"patch extent {scale:.3e}; the paired patches do not overlap when "
            "projected along the rack axis")

    if torch.unique(perm).numel() != perm.numel():
        raise ValueError(
            "rack face pairing is not a bijection: two intake faces matched the "
            "same discharge face, so the two patches are not transverse copies")

    return perm.to(device=source_face_pos.device, dtype=torch.long)


def get_rack_patch_pair_indices(
    fvpatch_like,
    neighbor_cpd_cell: torch.Tensor,
    face_pos: torch.Tensor,
    source_patch_idx: int,
    target_patch_idx: int,
    *,
    face_normal: Optional[torch.Tensor] = None,
    tol_rel: float = 1e-3,
):
    """Owner/ghost indices for a rack pair, target side ALREADY reordered.

    Returns ``(source_owner, source_ghost, target_owner, target_ghost, perm)``
    where every array is in SOURCE face order, so a caller writes Eq. 17 as one
    elementwise expression and never re-indexes.
    """
    source_patch_idx = _patch_scalar_to_int(source_patch_idx)
    target_patch_idx = _patch_scalar_to_int(target_patch_idx)

    source_start = _patch_scalar_to_int(fvpatch_like.start_idx_face[source_patch_idx])
    source_end = _patch_scalar_to_int(fvpatch_like.end_idx_face[source_patch_idx])
    target_start = _patch_scalar_to_int(fvpatch_like.start_idx_face[target_patch_idx])
    target_end = _patch_scalar_to_int(fvpatch_like.end_idx_face[target_patch_idx])

    pair_index = neighbor_cpd_cell.long()
    source_owner, source_ghost = pair_index[:, source_start:source_end]
    target_owner, target_ghost = pair_index[:, target_start:target_end]

    perm = rack_face_permutation(
        face_pos[source_start:source_end],
        face_pos[target_start:target_end],
        source_face_normal=(None if face_normal is None
                            else face_normal[source_start:source_end]),
        tol_rel=tol_rel,
    )

    return (source_owner, source_ghost,
            target_owner.index_select(0, perm), target_ghost.index_select(0, perm),
            perm)


def rack_cell_pairs(solver, lookup):
    """Interior-cell pairs joined across every rack coupling, for the assembler.

    Mirrors ``SparseSolver.jacobian_assembly._cyclic_cell_pairs``.  Injecting
    these as extra adjacency EDGES before ``adjacency @ adjacency`` is what puts
    the Eq. 17 entry inside the assembled pattern; without it, the coupling is
    an edge that exists at NO number of mesh hops and the assembled Jacobian
    silently loses it, which is the failure mode already on record for the
    three-hop ``d_f`` colouring (2.16e-09 of the product).

    Returns empty arrays when no rack BC is present, so callers need no branch.
    """
    import numpy as np

    empty = (np.array([], dtype=np.int64), np.array([], dtype=np.int64))
    batch = getattr(solver, "bcpatch_batch", None)
    if batch is None:
        return empty

    left, right = [], []
    for group_i, group in enumerate(getattr(batch, "coupled", [])):
        offset = batch.coupled_patch_idx_offset[group_i]
        for condition in group:
            if not getattr(condition, "couples_cell_pairs", False):
                continue
            source_owner, target_owner = condition.cell_pairs(solver, offset)
            a = lookup[source_owner].cpu().numpy()
            b = lookup[target_owner].cpu().numpy()
            keep = (a >= 0) & (b >= 0)
            left.append(a[keep])
            right.append(b[keep])

    if not left:
        return empty
    return np.concatenate(left), np.concatenate(right)


def cbbm_outer_pass(solver, fvfield):
    """One outer pass of Eq. 15 over EVERY lagged rack on the case.

    Returns ``(worst, report)``: ``worst`` is the largest per-rack change in
    kelvin (``inf`` on the seeding pass, so the first pass can never be
    mistaken for convergence), and ``report`` is a list of
    ``(patch_name, previous, new, change)`` for logging -- the reference
    prints exactly this, and quoting the per-rack residual beside the result
    is the house rule the DataCenter campaign exists to enforce.

    Returns ``(0.0, [])`` when the case carries no lagged rack, so a caller
    can run this unconditionally.
    """
    batch = getattr(solver, "bcpatch_batch", None)
    if batch is None:
        return 0.0, []

    worst = 0.0
    report = []
    for group_i, group in enumerate(getattr(batch, "coupled", [])):
        offset = batch.coupled_patch_idx_offset[group_i]
        for condition in group:
            update = getattr(condition, "update_lagged_mean", None)
            if update is None:
                continue
            result = update(fvfield, solver, offset)
            if result is None:
                continue
            previous, fresh, change = result
            report.append((condition.patch_name, previous, fresh, change))
            worst = max(worst, change)
    return worst, report


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _resolve(boundary_info: Dict, key: str, parametric_manager, default=None):
    value = boundary_info.get(key, default)
    if parametric_manager is not None:
        value = parametric_manager.resolve_value(value)
    return value


def _scalar(spec, default=None):
    """A float out of a toml entry that may be a number or ``{value_type,val}``."""
    if spec is None:
        return default
    if isinstance(spec, dict):
        spec = spec.get("val", default)
    if spec is None:
        return default
    if torch.is_tensor(spec):
        return float(spec.reshape(-1)[0])
    return float(spec)


def _theta(boundary_info: Dict, key: str, default: float) -> float:
    theta_pde = boundary_info.get("fvconfig", {}).get("theta_pde", {})
    return _scalar(theta_pde.get(key), default)


def _partner_patch_idx(boundary_info: Dict, patch_name: str) -> tuple:
    patch_dict = boundary_info.get("patch_dict", {})
    partner = boundary_info.get("partner_patch", boundary_info.get("inlet_patch"))
    if partner is None:
        raise ValueError(
            f"rack BC on '{patch_name}' needs 'partner_patch' naming the other "
            "face of the same rack")
    if partner not in patch_dict:
        raise ValueError(
            f"rack BC on '{patch_name}': partner patch '{partner}' is not in "
            "patch_dict")
    return str(partner), int(patch_dict[partner]["patch_idx"])


def _volumetric_rate(boundary_info: Dict, parametric_manager, patch_name: str) -> float:
    """Rack volumetric flow, from q_V directly or from q_m via Eq. 11."""
    volumetric = _scalar(_resolve(boundary_info, "volumetricFlowRate", parametric_manager))
    mass = _scalar(_resolve(boundary_info, "massFlowRate", parametric_manager))
    servers = _scalar(_resolve(boundary_info, "nServers", parametric_manager), 1.0)
    rho = _scalar(_resolve(boundary_info, "rho", parametric_manager),
                  _theta(boundary_info, "rho", 1.1614))
    if volumetric is not None:
        return servers * volumetric          # Eq. 11 with rho divided out
    if mass is not None:
        return mass / rho
    raise ValueError(
        f"rack BC on '{patch_name}' needs volumetricFlowRate (per server, with "
        "nServers) or massFlowRate (per rack)")


# ---------------------------------------------------------------------------
# Velocity
# ---------------------------------------------------------------------------
class _RackVelocityBase(BoundaryCondition):
    """Shared plumbing: patch face range, prescribed normal speed, init_field."""

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                 parametric_manager: ParametricManager = None,
                 *, bc_type: BCType):
        self.flow_rate = _volumetric_rate(boundary_info, parametric_manager, patch_name)
        self.value = _resolve(boundary_info, "value", parametric_manager,
                              {"value_type": "uniform", "val": [0.0, 0.0, 0.0]})
        super().__init__(patch_name, patch_idx, VARType.VELOCITY, bc_type)

    def init_field(self, uvw_cpd_cell, fvpatch, **kwargs):
        start = _patch_scalar_to_int(fvpatch.start_idx_cpd_cell[self.patch_idx])
        end = _patch_scalar_to_int(fvpatch.end_idx_cpd_cell[self.patch_idx])
        uvw_cpd_cell[..., start:end, 0:3] = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end - start,
            channel_dim=3,
        ).eval(device=uvw_cpd_cell.device)
        return uvw_cpd_cell

    def _patch_geometry(self, integrator, patch_idx_offset: int):
        global_patch_idx = self.patch_idx + patch_idx_offset
        fvpatch_batch = integrator.fvpatch_batch
        start = _patch_scalar_to_int(fvpatch_batch.start_idx_face[global_patch_idx])
        end = _patch_scalar_to_int(fvpatch_batch.end_idx_face[global_patch_idx])
        normals = integrator.unv_face[start:end]
        areas = integrator.face_area[start:end].reshape(-1, 1)
        owner = integrator.neighbor_cpd_cell[0, start:end]
        ghost = integrator.neighbor_cpd_cell[1, start:end]
        # Eq. 13 with A the WHOLE patch area, so that summing rho u^n A over the
        # patch returns exactly q_m however the faces are cut.
        speed = self.flow_rate / areas.sum()
        return start, end, normals, areas, owner, ghost, speed


class Rackinletvelocity(_RackVelocityBase):
    """Rack INTAKE face: Eq. 13 on the normal, Eq. 14 on the tangential.

    ``u_ghost = (I - n n^T) u_owner + (q_V / A) n``.  The normal component is
    Dirichlet and the tangential is mirrored from the owner, which is what
    preserves the approach flow's pattern on the face -- the whole point of
    Eq. 14, and the reason this is not simply a fixed-value patch.

    Its Jacobian is a 3x3 projector against the owner cell alone, i.e. one hop,
    so it adds nothing to the assembled sparsity.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                 parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, boundary_info, parametric_manager,
                         bc_type=BCType.MIXED)

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        start, end, normals, _areas, owner, ghost, speed = self._patch_geometry(
            integrator, patch_idx_offset)

        uvw_cpd_cell = fvfield["uvw_cpd_cell"]
        uvw_owner = uvw_cpd_cell[..., owner, :]
        normal_component = torch.sum(uvw_owner * normals, dim=-1, keepdim=True)
        tangential = uvw_owner - normal_component * normals      # Eq. 14
        # unv_face points OUT of the domain, and intake air leaves the domain,
        # so the prescribed normal component is +speed.
        uvw_cpd_cell[..., ghost, :] = tangential + speed * normals   # Eq. 13
        fvfield["uvw_cpd_cell"] = uvw_cpd_cell

        # Eq. 14 is a statement about the TANGENTIAL velocity only, so the
        # viscous face flux keeps its normal-normal part and loses the rest;
        # zeroing the whole thing (the zeroGradient convention in setU.Slip)
        # would also delete d u^n / dn, which Bai prescribes nothing about.
        #
        # The key holds ONE 3-vector d u_i / dn per state it carries, stacked
        # along the last axis: production writes 6 (new 0:3, old 3:6, the layout
        # setU.Symmetry writes into) and the unit fixtures write 3.  A guard for
        # width == 3 therefore skipped this projection on every solver that can
        # host a rack -- picoext:1633, picocmpt:1068 and both buoyantBoussinesq
        # forwards all publish 6 -- and Eq. 14 was enforced on the ghost value
        # alone.  Each 3-block is projected separately because the contraction
        # is with the 3-component face normal; a 6-vector has no normal.
        grad_flux = fvfield.get("grad_uvw_flux_face", None)
        if grad_flux is not None:
            width = grad_flux.shape[-1]
            if width % 3:
                raise ValueError(
                    f"rack intake '{self.patch_name}': grad_uvw_flux_face is "
                    f"{width} channels wide, which is not a whole number of "
                    "d u_i / dn vectors, so Eq. 14 has nothing to project")
            normals_b = normals.unsqueeze(-2)                    # [F, 1, 3]
            blocks = grad_flux[..., start:end, :].unflatten(-1, (width // 3, 3))
            grad_flux[..., start:end, :] = (
                (blocks * normals_b).sum(dim=-1, keepdim=True) * normals_b
            ).flatten(-2)
            fvfield["grad_uvw_flux_face"] = grad_flux

        return fvfield


class Rackoutletvelocity(_RackVelocityBase):
    """Rack DISCHARGE face: Eq. 12, the paired face's mass flux blown back in.

    With the rack flow pre-given (Bai's Eq. 11 q_V, the fan-curve closure of
    their Fig. 2 deferred), Eq. 12 is satisfied by construction rather than by
    reading the partner face: both patches carry the same q_m, so
    ``u_ghost = -(q_V / A) n`` returns exactly what the intake removed.  That
    makes this a plain Dirichlet with ZERO derivative, and it is why the
    velocity half of the rack model costs the Jacobian nothing.

    ``tangential="preserve"`` mirrors the owner's tangential component instead
    of discharging axially; the default is axial, which is what a rack fan
    does.
    """

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                 parametric_manager: ParametricManager = None):
        self.tangential = str(boundary_info.get("tangential", "zero")).lower()
        if self.tangential not in ("zero", "preserve"):
            raise ValueError(
                f"rack discharge '{patch_name}': tangential must be 'zero' or "
                f"'preserve', got {self.tangential!r}")
        super().__init__(patch_name, patch_idx, boundary_info, parametric_manager,
                         bc_type=BCType.FIRST)

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        _start, _end, normals, _areas, owner, ghost, speed = self._patch_geometry(
            integrator, patch_idx_offset)

        uvw_cpd_cell = fvfield["uvw_cpd_cell"]
        # Air ENTERS the domain here, against the outward normal.
        discharge = -speed * normals
        if self.tangential == "preserve":
            uvw_owner = uvw_cpd_cell[..., owner, :]
            normal_component = torch.sum(uvw_owner * normals, dim=-1, keepdim=True)
            discharge = discharge + (uvw_owner - normal_component * normals)
        uvw_cpd_cell[..., ghost, :] = discharge
        fvfield["uvw_cpd_cell"] = uvw_cpd_cell
        return fvfield


# ---------------------------------------------------------------------------
# Temperature
# ---------------------------------------------------------------------------
class Rackoutlettemperature(BoundaryCondition):
    """Rack DISCHARGE face temperature: Eq. 17 pointwise (IBBM).

    ``T_ghost[i] = T_owner_of_paired_intake_face[perm[i]] + dT``, with
    ``dT = Q / (cp q_m)`` from Eq. 16.  The intake side reads its temperature
    off the solution, which in our contract is `zeroGradient` on the intake
    patch -- a MIXED BC, and MIXED is applied before COUPLED in
    ``BoundaryConditionBatch.iter_conditions``, so the ordering is already
    right.  This reads the paired OWNER cell rather than the paired GHOST
    anyway, which makes the result independent of that ordering AND keeps the
    coupling exactly one pair-edge long (see the design note, section 4).

    ``model="cbbm"`` reproduces Eq. 15, the conventional uniform outlet, for
    the comparison Bai reports.  It sets ``has_global_reduction`` and clears
    ``couples_cell_pairs``, because an area mean is not an edge and no hop
    count contains it.

    **``lagMean = true`` is how CBBM reaches an ASSEMBLED Newton path.**  The
    area mean's derivative is a dense rank-1 block over a whole patch, so
    ``SparseSolver.jacobian_assembly`` refuses it outright -- correctly, since
    no mesh-hop colouring can represent it and assembling anyway would write
    that derivative onto unrelated cells.  Lagging freezes the mean at a
    constant during a solve, which turns the discharge ghost into a plain
    Dirichlet: it then depends on NO cell, so the condition declares neither a
    global reduction nor a cell pair and the Jacobian is exactly the one the
    assembler already colours.  The mean is restored by an OUTER iteration
    (``update_lagged_mean`` below, driven by ``cbbm_outer_pass``), which is a
    contraction whose gain is the rack's recirculation fraction.

    This is not an approximation of Eq. 15; it is Eq. 15 solved by a different
    ordering, and it converges to the same fixed point.  It is also exactly
    what the OpenFOAM reference does -- ``experiments/DataCenter/openfoam-turb/
    run.sh`` solves, rereads each intake mean, rewrites the four discharge
    values and restarts -- and that reference reached 0.00925 K on a 0.01 K
    tolerance in three passes, contracting ~0.13 per pass
    (docs/research/2026-08-18-dc-bai-likeforlike.md section 4.1).
    """

    # Read by ``rack_cell_pairs`` and, later, by the assembler that injects the
    # pair edges.  Never true for the CBBM mean, lagged or not.
    couples_cell_pairs = True
    has_global_reduction = False

    def __init__(self, patch_name: str, patch_idx: int, boundary_info: Dict,
                 parametric_manager: ParametricManager = None):
        super().__init__(patch_name, patch_idx, VARType.TEMPERATURE, BCType.COUPLED)
        self.is_coupled = True

        self.model = str(boundary_info.get("model", "ibbm")).lower()
        if self.model not in ("ibbm", "cbbm"):
            raise ValueError(
                f"rack discharge '{patch_name}': model must be 'ibbm' (Eq. 17) "
                f"or 'cbbm' (Eq. 15), got {self.model!r}")
        # Lagging is meaningless for the pointwise model -- Eq. 17's coupling
        # IS an edge and the assembler colours it -- so the key is refused
        # there BY NAME rather than silently ignored.
        self.lag_mean = bool(boundary_info.get("lagMean", False))
        if self.lag_mean and self.model != "cbbm":
            raise ValueError(
                f"rack discharge '{patch_name}': lagMean applies to the CBBM "
                "area mean (Eq. 15); the pointwise model (Eq. 17) has no mean "
                "to lag and its coupling is already an assemblable edge")
        # CBBM intake mean: "area" (default, Eq. 15) or "flux" -- sum(phi T) / sum(phi) over the intake faces with
        # phi = (u_ghost . n) A, the dctwin / OpenFOAM ``tSink`` expression.  The two coincide exactly when the
        # intake carries a uniform normal speed (rackInletVelocity); they differ under an extrapolated profile
        # (flowRateOutletVelocity).
        self.mean_weight = str(boundary_info.get("meanWeight", "area")).lower()
        if self.mean_weight not in ("area", "flux"):
            raise ValueError(
                f"rack discharge '{patch_name}': meanWeight must be 'area' or 'flux', got {self.mean_weight!r}")
        if self.mean_weight != "area" and self.model != "cbbm":
            raise ValueError(
                f"rack discharge '{patch_name}': meanWeight applies to the CBBM intake mean (model = 'cbbm')")
        if self.model == "cbbm":
            self.couples_cell_pairs = False
            # A LAGGED mean is a frozen constant, so the discharge ghost
            # depends on nothing: no dense block, hence nothing for
            # jacobian_assembly to refuse.  An UNLAGGED mean keeps the
            # declaration and stays refused, which is the behaviour that sent
            # this work here in the first place.
            self.has_global_reduction = not self.lag_mean
        # Frozen intake mean, one scalar per rack, held as a plain float so it
        # can never carry a graph into the next solve.  None until the first
        # eval seeds it from the state (no separate initialisation step, and
        # no seed value in src/).
        self._lagged_mean = None

        self.partner_patch_name, self.partner_patch_idx = _partner_patch_idx(
            boundary_info, patch_name)

        delta = _scalar(_resolve(boundary_info, "deltaT", parametric_manager))
        if delta is None:
            heat = _scalar(_resolve(boundary_info, "Q_W", parametric_manager))
            if heat is None:
                raise ValueError(
                    f"rack discharge '{patch_name}' needs deltaT, or Q_W plus a "
                    "flow rate so Eq. 16 can supply it")
            rho = _scalar(_resolve(boundary_info, "rho", parametric_manager),
                          _theta(boundary_info, "rho", 1.1614))
            cp = _scalar(_resolve(boundary_info, "Cp", parametric_manager),
                         _theta(boundary_info, "Cp", 1005.0))
            mass_rate = rho * _volumetric_rate(boundary_info, parametric_manager,
                                               patch_name)          # Eq. 11
            delta = heat / (cp * mass_rate)                          # Eq. 16
        self.deltaT = float(delta)

        self.value = _resolve(boundary_info, "value", parametric_manager,
                              {"value_type": "uniform", "val": 0.0})
        self.tol_rel = float(boundary_info.get("pairingTolerance", 1e-3))
        self._pair_cache = None

    def init_field(self, t_cpd_cell, fvpatch, **kwargs):
        start = _patch_scalar_to_int(fvpatch.start_idx_cpd_cell[self.patch_idx])
        end = _patch_scalar_to_int(fvpatch.end_idx_cpd_cell[self.patch_idx])
        t_cpd_cell[..., start:end, 0:1] = Functionbase(
            value_type=self.value["value_type"],
            value=self.value["val"],
            num_element=end - start,
            channel_dim=1,
        ).eval(device=t_cpd_cell.device)
        return t_cpd_cell

    def _pairing(self, integrator, patch_idx_offset: int):
        """Face correspondence, built once and keyed on the patch batch object.

        The pairing is pure geometry, so it survives every state update; it is
        rebuilt only if the case is reloaded onto a different patch batch (the
        identity-key pattern ``Turbulence/eddy_viscosity.py`` already uses).
        """
        fvpatch_batch = integrator.fvpatch_batch
        if self._pair_cache is not None and self._pair_cache[0] is fvpatch_batch:
            return self._pair_cache[1]

        pairing = get_rack_patch_pair_indices(
            fvpatch_batch,
            integrator.neighbor_cpd_cell,
            integrator.face_pos,
            self.patch_idx + patch_idx_offset,
            self.partner_patch_idx + patch_idx_offset,
            face_normal=getattr(integrator, "unv_face", None),
            tol_rel=self.tol_rel,
        )
        self._pair_cache = (fvpatch_batch, pairing)
        return pairing

    def _cbbm_indices(self, integrator, patch_idx_offset: int):
        """CBBM needs no face pairing (data-centre line 2026-11-06): the discharge ghosts of THIS patch in its
        own face order, and the intake owners and areas of the partner patch in ITS face order. The two patches
        may have different face counts (dctwin servers: 26 discharge faces against 95 intake faces), which the
        pointwise pairing refuses; and the area weights must follow the intake's own order, which a pairing
        permutation would not."""
        fvpatch_batch = integrator.fvpatch_batch
        own = self.patch_idx + patch_idx_offset
        partner = self.partner_patch_idx + patch_idx_offset
        s0 = _patch_scalar_to_int(fvpatch_batch.start_idx_face[own])
        s1 = _patch_scalar_to_int(fvpatch_batch.end_idx_face[own])
        p0 = _patch_scalar_to_int(fvpatch_batch.start_idx_face[partner])
        p1 = _patch_scalar_to_int(fvpatch_batch.end_idx_face[partner])
        nb = integrator.neighbor_cpd_cell.long()
        return nb[1, s0:s1], nb[0, p0:p1], integrator.face_area[p0:p1].reshape(-1, 1)

    def _intake_flux(self, fvfield, integrator, patch_idx_offset: int, areas):
        """phi = (u_ghost . n) A on the partner intake faces, in its face order (``meanWeight = "flux"``)."""
        fvpatch_batch = integrator.fvpatch_batch
        partner = self.partner_patch_idx + patch_idx_offset
        p0 = _patch_scalar_to_int(fvpatch_batch.start_idx_face[partner])
        p1 = _patch_scalar_to_int(fvpatch_batch.end_idx_face[partner])
        ghost = integrator.neighbor_cpd_cell.long()[1, p0:p1]
        uvw = fvfield["uvw_cpd_cell"][..., ghost, 0:3]
        normal_speed = (uvw * integrator.unv_face[p0:p1].to(uvw.dtype)).sum(dim=-1, keepdim=True)
        return normal_speed * areas.to(uvw.dtype)

    def cell_pairs(self, integrator, patch_idx_offset: int):
        """The (discharge owner, intake owner) interior-cell pairs, for the assembler."""
        source_owner, _source_ghost, target_owner, _target_ghost, _perm = self._pairing(
            integrator, patch_idx_offset)
        return source_owner, target_owner

    def eval(self, fvfield, integrator, patch_idx_offset: int, **kwargs):
        if self.model == "cbbm":
            t_cpd_cell = fvfield["t_cpd_cell"]
            source_ghost, target_owner, areas = self._cbbm_indices(integrator, patch_idx_offset)
            intake = t_cpd_cell[..., target_owner, 0:1]
            if self.mean_weight == "flux":
                # the area mean where the intake carries no flow (the flux weights are undefined, 0 / 0)
                phi = self._intake_flux(fvfield, integrator, patch_idx_offset, areas)
                still = phi.sum(dim=-2, keepdim=True) == 0
                live = self._area_mean(intake, torch.where(still, areas.to(intake.dtype), phi))
            else:
                live = self._area_mean(intake, areas.to(intake.dtype))
            if self.lag_mean:
                if self._lagged_mean is None:
                    self._lagged_mean = float(live.detach().reshape(-1)[0])
                outlet = torch.full_like(t_cpd_cell[..., source_ghost, 0:1], self._lagged_mean + self.deltaT)
            else:
                outlet = live.expand_as(t_cpd_cell[..., source_ghost, 0:1]) + self.deltaT
            t_cpd_cell[..., source_ghost, 0:1] = outlet
            fvfield["t_cpd_cell"] = t_cpd_cell
            return fvfield
        _source_owner, source_ghost, target_owner, _target_ghost, _perm = self._pairing(
            integrator, patch_idx_offset)

        t_cpd_cell = fvfield["t_cpd_cell"]
        intake = t_cpd_cell[..., target_owner, 0:1]

        if self.model == "ibbm":
            outlet = intake + self.deltaT                            # Eq. 17
        else:
            global_patch_idx = self.patch_idx + patch_idx_offset
            fvpatch_batch = integrator.fvpatch_batch
            partner_global = self.partner_patch_idx + patch_idx_offset
            start = _patch_scalar_to_int(fvpatch_batch.start_idx_face[partner_global])
            end = _patch_scalar_to_int(fvpatch_batch.end_idx_face[partner_global])
            areas = integrator.face_area[start:end].reshape(-1, 1).to(intake.dtype)
            # Eq. 15: one number for the whole patch.
            live = self._area_mean(intake, areas)
            if self.lag_mean:
                # Frozen: every discharge ghost depends on NOTHING, so the
                # Jacobian row is a plain Dirichlet and autograd sees a leaf.
                # Seeded from the live mean on the first evaluation, so a cold
                # start needs no configured seed and a warm start inherits its
                # own field.
                if self._lagged_mean is None:
                    # detach, not just float(): the seed is read off a state
                    # that carries a graph, and the frozen mean must be a leaf
                    # in every later solve or the "depends on nothing" claim
                    # this whole mode rests on is only true by accident.
                    self._lagged_mean = float(live.detach().reshape(-1)[0])
                outlet = torch.full_like(intake, self._lagged_mean + self.deltaT)
            else:
                # Live: every discharge ghost depends on every intake cell.
                outlet = live.expand_as(intake) + self.deltaT
            del global_patch_idx

        t_cpd_cell[..., source_ghost, 0:1] = outlet
        fvfield["t_cpd_cell"] = t_cpd_cell
        return fvfield

    @staticmethod
    def _area_mean(intake, areas):
        """Area-weighted intake mean, ``[.., 1, 1]`` -- Eq. 15's single number."""
        return ((intake * areas).sum(dim=-2, keepdim=True)
                / areas.sum(dim=-2, keepdim=True))

    def _intake_areas(self, integrator, patch_idx_offset: int):
        fvpatch_batch = integrator.fvpatch_batch
        partner_global = self.partner_patch_idx + patch_idx_offset
        start = _patch_scalar_to_int(fvpatch_batch.start_idx_face[partner_global])
        end = _patch_scalar_to_int(fvpatch_batch.end_idx_face[partner_global])
        return integrator.face_area[start:end].reshape(-1, 1)

    def update_lagged_mean(self, fvfield, integrator, patch_idx_offset: int):
        """One outer pass of Eq. 15's fixed point; returns ``(old, new, |d|)``.

        Reads the CONVERGED intake mean off the current state and installs it
        as the frozen value the next solve will use.  Returns the change in
        kelvin so the caller can gate on it -- the reference gates at 0.01 K,
        which is the tolerance Bai's own numerical-speed criterion uses
        (his Sec. 4.6, "the average temperature on the rack inlet surface
        varies within 0.01 C").

        No-op returning ``None`` for a condition that is not a lagged mean, so
        a caller can walk every rack BC without branching.
        """
        if not (self.model == "cbbm" and self.lag_mean):
            return None
        _source_owner, _source_ghost, target_owner, _target_ghost, _perm = (
            self._pairing(integrator, patch_idx_offset))
        with torch.no_grad():
            intake = fvfield["t_cpd_cell"][..., target_owner, 0:1]
            areas = self._intake_areas(integrator, patch_idx_offset).to(intake.dtype)
            fresh = float(self._area_mean(intake, areas).reshape(-1)[0])
        previous = self._lagged_mean
        self._lagged_mean = fresh
        if previous is None:
            # Nothing was frozen yet, so there is no change to report and the
            # pass must not be counted as converged.
            return None, fresh, float("inf")
        return previous, fresh, abs(fresh - previous)
