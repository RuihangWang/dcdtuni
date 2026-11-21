"""Zonal pressure-enthalpy air network on a coarse finite-volume mesh: the L0 model of a data hall.

What it is.  The airflow-network (zonal) model of dcdtuni / ISPO (``backend.aircooling.zonal``, after the EnergyPlus
airflow network: https://bigladdersoftware.com/epx/docs/24-2/engineering-reference/airflownetwork-model.html) with
its nodes and links taken from a mesh instead of a hand-built zone graph: every cell of the mesh (a dcCart L0 mesh,
about 1/1000 of the fine cells, every patch of the room kept) is a well-mixed zone, the faces between two cells are
one passive opening, and the boundary conditions are the ones the RANS application reads (``bc/*.toml`` of the
same case), so the L0 and the L1 / HF models share one input.

Unknowns per zone (per cell): the kinematic pressure ``p_rgh`` (m2/s2) and the temperature ``T`` (K, the excess over
``TRef`` as in the RANS case).  Rows per zone: the volume balance (m3/s) and the enthalpy balance (K m3/s; the
energy divided by rho cp), so

    mass_i   = sum_links Q_ij + sum_boundary Q_b                                          = 0
    energy_i = cap V_i dT_i/dt + sum_links E_ij + sum_boundary E_b                          = 0

Links (one per pair of cells joined by interior faces; per pair of cells joined through a cyclic baffle):

* driving pressure ``D = p_i - p_j + ghf (rhok_i - rhok_j)``, ``rhok = 1 - beta (T - TRef)`` and ``ghf = g . x_f``
  at the area-weighted centre of the faces (the Boussinesq ``p_rgh`` of the RANS case and its ``phig``);
* open faces: the orifice law ``Q = Cd A sqrt(2) D / (D^2 + delta^2)^(1/4)``;
* porous baffles (``porousBafflePressure``: jump ``(D_f nu + I |U|/2) U L``): ``Q = A U`` with the jump equal to D,
  ``U = 2 D / (a + sqrt(a^2 + 4 b sqrt(D^2 + delta^2)))``, ``a = D_f nu L``, ``b = I L / 2``;
* the enthalpy flux: smoothed upwind ``E = q+ T_i - q- T_j`` (``q+ = (sqrt(Q^2 + eps^2) + Q) / 2``, ``q- = q+ - Q``)
  plus the exchange flow of the zonal model, ``m A (T_i - T_j)`` (heat, no mass).

Boundary conditions (per patch, from the case's ``U``, ``T`` and ``p_rgh`` entries):

* ``flowRateInletVelocity``: the patch flow enters, shared over its faces by area; the inflow temperature is the
  patch's ``fixedValue`` (a cooling unit's supply), ``rackOutletTemperature`` (CBBM: the flux-weighted mean
  temperature of the zones the partner intake draws from, plus ``deltaT``; a server exhaust) or ``zeroGradient``;
* ``flowRateOutletVelocity``: the patch flow leaves at the temperature of its zones (a unit's return, a server intake);
* ``p_rgh fixedValue`` with ``U zeroGradient``: an opening to that pressure through the orifice law (the pressure
  reference of the hall);
* walls (``noSlip``, ``slip``), ``symmetry``, ``empty``: no flux (adiabatic walls).

Anything else is refused by name.  Without a pressure opening the volume row of zone 0 is replaced by ``p_0 = 0``.

The coefficients default to ISPO's L0 (``agent.dcdesign.l0``): Cd 0.62, exchange 0.01 m3/s per m2, upwind smoothing
1e-3 m3/s, pressure smoothing 0.01 Pa.

This module is the torch reference (every derivative by autograd, parameters included);
``zonal_network_warp`` evaluates the same residual and its exact sparse Jacobian in Warp kernels for a batch of
states ``[B, N, 2]`` (time steps of a window or snapshots) and is tested against it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np
import torch

try:
    import tomllib
except ModuleNotFoundError:                          # Python 3.10 (dcdtuni): the same parser as a package
    import tomli as tomllib

VERSION = "zonal-mesh-network/v1-2026-11-16"

WALL_U_TYPES = ("noSlip", "slip", "fixedValue_zero", "movingWallVelocity")
SKIP_PATCH_TYPES = ("empty", "symmetry", "symmetryPlane", "wedge")


@dataclass
class ZonalCoefficients:
    """The declared coefficients of the network (ISPO's L0 values by default)."""
    discharge_coefficient: float = 0.62
    mixing_m3s_per_m2: float = 0.01
    thermal_flow_smoothing_m3s: float = 1e-3
    pressure_smoothing_pa: float = 0.01
    rho: float = 1.18                   # only converts the pressure smoothing to kinematic units
    thermal_capacity_factor: float = 1.0  # cap: multiplies the air volume of every zone in the time term


@dataclass
class ZonalBoundaryValues:
    """The boundary values a solve varies (one row per batch item, B >= 1): what an inversion fits."""
    flow: torch.Tensor         # (B, n_flow_patches)  m3/s of every flowRate patch (inlets and outlets)
    t_fixed: torch.Tensor      # (B, n_fixed_t)      K: the fixedValue inflow temperatures
    delta_t: torch.Tensor      # (B, n_racks)        K: the CBBM temperature rise of every server exhaust
    p_ref: torch.Tensor        # (B, n_pref)         m2/s2: the pressure of every opening
    porous_i: torch.Tensor | None = None   # (B, n_porous) 1/m: the inertial coefficient I of every porous patch
                                           # (None: the case's own, fixed in the links)

    def to(self, device=None, dtype=torch.float64) -> "ZonalBoundaryValues":
        return ZonalBoundaryValues(**{f.name: (None if getattr(self, f.name) is None else
                                               getattr(self, f.name).to(device=device, dtype=dtype))
                                      for f in fields(self)})

    def expand(self, batch: int) -> "ZonalBoundaryValues":
        return ZonalBoundaryValues(**{f.name: (None if getattr(self, f.name) is None else
                                               getattr(self, f.name).expand(batch, -1).contiguous())
                                      for f in fields(self)})


@dataclass
class ZonalMesh:
    """The network of one mesh: every array on ``device`` (int64 indices, float64 values)."""
    n_cells: int
    volume: torch.Tensor          # (N,)
    centre: torch.Tensor          # (N, 3)
    beta: float
    t_ref: float
    nu: float
    gravity: tuple[float, float, float]
    coef: ZonalCoefficients
    # links
    link_i: torch.Tensor          # (L,)
    link_j: torch.Tensor          # (L,)
    link_area: torch.Tensor       # (L,)  m2
    link_ghf: torch.Tensor        # (L,)  g . x_f (m2/s2)
    link_porous: torch.Tensor     # (L,)  bool: porous-baffle law
    link_a: torch.Tensor          # (L,)  porous D_f nu L   (m/s per (m/s)  -> s^-1 m)
    link_b: torch.Tensor          # (L,)  porous I L / 2
    link_mix: torch.Tensor        # (L,)  bool: the exchange flow applies
    # flow boundaries, aggregated per (cell, patch)
    in_cell: torch.Tensor         # (Ki,) zone of an inflow item
    in_patch: torch.Tensor        # (Ki,) its flow patch (column of ``flow``)
    in_share: torch.Tensor        # (Ki,) its share of the patch flow (area fraction)
    in_mode: torch.Tensor         # (Ki,) 0 fixedValue (in_index: column of t_fixed), 1 CBBM (in_index: rack), 2 zeroGradient
    in_index: torch.Tensor        # (Ki,)
    out_cell: torch.Tensor        # (Ko,)
    out_patch: torch.Tensor       # (Ko,)
    out_share: torch.Tensor       # (Ko,)
    # CBBM racks: the zones the partner intake draws from, with their share of its flow
    rack_cell: torch.Tensor       # (Kr,)
    rack_of: torch.Tensor         # (Kr,) rack of each entry
    rack_share: torch.Tensor      # (Kr,)
    # pressure openings, aggregated per (cell, patch)
    pref_cell: torch.Tensor       # (Kp,)
    pref_index: torch.Tensor      # (Kp,) column of p_ref
    pref_area: torch.Tensor       # (Kp,)
    pref_t_fixed: torch.Tensor    # (Kp,) column of t_fixed for a fixedValue T of the opening, -1: zeroGradient
    # names and the case's own values
    flow_patches: list[str] = field(default_factory=list)
    fixed_t_patches: list[str] = field(default_factory=list)
    racks: list[str] = field(default_factory=list)
    pref_patches: list[str] = field(default_factory=list)
    default_values: ZonalBoundaryValues | None = None
    report: dict = field(default_factory=dict)
    # area-weighted face centroids (m) of every link and boundary item: the cell velocity reconstruction
    link_centre: torch.Tensor | None = None       # (L, 3)
    in_centre: torch.Tensor | None = None         # (Ki, 3)
    out_centre: torch.Tensor | None = None        # (Ko, 3)
    pref_centre: torch.Tensor | None = None       # (Kp, 3)
    # porous patches: the patch of every porous link (-1 elsewhere) and its L / 2, so I is a boundary value
    porous_patches: list[str] = field(default_factory=list)       # the first side of every porous cyclic pair
    porous_partners: list[str] = field(default_factory=list)      # the second side
    link_ppatch: torch.Tensor | None = None       # (L,) column of porous_i, -1: not porous
    link_half_len: torch.Tensor | None = None     # (L,) L / 2 of the porous links
    # the polyhedra of the zones (numpy, host): points, face_offsets, face_points, owner, neighbour -- cells_at finds
    # the zone that contains a point (an agglomerated dcCart cell is not convex; its centre can lie outside it)
    cell_geometry: dict | None = None

    @property
    def device(self) -> torch.device:
        return self.volume.device

    @property
    def n_links(self) -> int:
        return int(self.link_i.numel())

    @property
    def gauge(self) -> bool:
        """True when no pressure opening fixes the pressure level (zone 0's volume row becomes p_0 = 0)."""
        return int(self.pref_cell.numel()) == 0

    def mass_vector(self) -> torch.Tensor:
        """(N, 2): the capacity of every row in the time term (0 for the volume rows)."""
        m = torch.zeros(self.n_cells, 2, dtype=torch.float64, device=self.device)
        m[:, 1] = self.coef.thermal_capacity_factor * self.volume
        return m

    # ------------------------------------------------------------------ the residual (torch reference)
    def porous_b(self, bv: ZonalBoundaryValues | None, B: int) -> torch.Tensor:
        """(B, L) the inertial term I L / 2 of every link (the case's own, or ``bv.porous_i`` where given)."""
        b = self.link_b.unsqueeze(0).expand(B, -1)
        if bv is None or bv.porous_i is None or self.link_ppatch is None or not bv.porous_i.shape[1]:
            return b
        pi = bv.porous_i.expand(B, -1)[:, self.link_ppatch.clamp(min=0)] * self.link_half_len
        return torch.where(self.link_ppatch >= 0, pi, b)

    def link_flow(self, X: torch.Tensor, cd: torch.Tensor | float | None = None, bv: ZonalBoundaryValues | None = None):
        """(Q, D) of every link for states X (B, N, 2) (``bv``: the porous coefficients of ``bv.porous_i``)."""
        c = self.coef
        cd = c.discharge_coefficient if cd is None else cd
        p, T = X[..., 0], X[..., 1]
        rk = 1.0 - self.beta * (T - self.t_ref)
        i, j = self.link_i, self.link_j
        D = p[:, i] - p[:, j] + self.link_ghf * (rk[:, i] - rk[:, j])
        delta = c.pressure_smoothing_pa / c.rho
        orifice = cd * self.link_area * math.sqrt(2.0) * D / (D.square() + delta ** 2).pow(0.25)
        m = (D.square() + delta ** 2).sqrt()
        # the porous law where it applies; elsewhere a harmless b = 1 so that torch.where's backward sees no inf
        a = torch.where(self.link_porous, self.link_a, torch.zeros_like(self.link_a))
        b = torch.where(self.link_porous, self.porous_b(bv, X.shape[0]), torch.ones_like(self.link_b))
        porous = self.link_area * 2.0 * D / (a + (a.square() + 4.0 * b * m).sqrt())
        return torch.where(self.link_porous, porous, orifice), D

    def residual(self, X: torch.Tensor, bv: ZonalBoundaryValues, *, cd=None, mixing=None) -> torch.Tensor:
        """(B, N, 2) steady residual (volume, enthalpy rows) of states X (B, N, 2)."""
        c = self.coef
        B, N = X.shape[0], self.n_cells
        mixing = c.mixing_m3s_per_m2 if mixing is None else mixing
        p, T = X[..., 0], X[..., 1]
        i, j = self.link_i, self.link_j
        Q, _ = self.link_flow(X, cd, bv)
        eps = c.thermal_flow_smoothing_m3s
        qp = 0.5 * ((Q.square() + eps ** 2).sqrt() + Q)
        qm = qp - Q
        E = qp * T[:, i] - qm * T[:, j] + torch.where(self.link_mix, mixing * self.link_area,
                                                      torch.zeros_like(self.link_area)) * (T[:, i] - T[:, j])
        mass = X.new_zeros(B, N).index_add(1, i, Q).index_add(1, j, -Q)
        heat = X.new_zeros(B, N).index_add(1, i, E).index_add(1, j, -E)
        # inflow
        qin = bv.flow[:, self.in_patch] * self.in_share
        rack_mean = X.new_zeros(B, len(self.racks)).index_add(1, self.rack_of, self.rack_share * T[:, self.rack_cell])
        t_in = torch.where(self.in_mode == 0, bv.t_fixed[:, self.in_index.clamp(max=max(bv.t_fixed.shape[1] - 1, 0))]
                           if bv.t_fixed.shape[1] else T[:, self.in_cell],
                           torch.where(self.in_mode == 1,
                                       (rack_mean + bv.delta_t)[:, self.in_index.clamp(max=max(len(self.racks) - 1, 0))]
                                       if self.racks else T[:, self.in_cell],
                                       T[:, self.in_cell]))
        mass = mass.index_add(1, self.in_cell, -qin)
        heat = heat.index_add(1, self.in_cell, -qin * t_in)
        # outflow
        qout = bv.flow[:, self.out_patch] * self.out_share
        mass = mass.index_add(1, self.out_cell, qout)
        heat = heat.index_add(1, self.out_cell, qout * T[:, self.out_cell])
        # pressure openings
        if self.pref_cell.numel():
            cd_ = c.discharge_coefficient if cd is None else cd
            delta = c.pressure_smoothing_pa / c.rho
            Dp = p[:, self.pref_cell] - bv.p_ref[:, self.pref_index]
            Qp = cd_ * self.pref_area * math.sqrt(2.0) * Dp / (Dp.square() + delta ** 2).pow(0.25)
            qpp = 0.5 * ((Qp.square() + eps ** 2).sqrt() + Qp)
            tb = torch.where(self.pref_t_fixed >= 0, bv.t_fixed[:, self.pref_t_fixed.clamp(min=0)]
                             if bv.t_fixed.shape[1] else T[:, self.pref_cell], T[:, self.pref_cell])
            Ep = qpp * T[:, self.pref_cell] - (qpp - Qp) * tb
            mass = mass.index_add(1, self.pref_cell, Qp)
            heat = heat.index_add(1, self.pref_cell, Ep)
        else:
            mass = torch.cat((p[:, :1], mass[:, 1:]), dim=1)
        return torch.stack((mass, heat), dim=-1)

    def velocity(self, X: torch.Tensor, bv: ZonalBoundaryValues, *, cd=None) -> torch.Tensor:
        """(B, N, 3) the cell velocity of the network's flows: ``U_c = (1 / V_c) sum_f phi_f (x_f - x_c)`` over the
        cell's links and boundary items (``phi_f`` the outward volume flow, ``x_f`` the face centroid) -- the
        flux-to-cell reconstruction, exact for a uniform velocity in a closed cell."""
        if self.link_centre is None or self.in_centre is None or self.out_centre is None:
            raise ValueError("zonal network: no face centroids (built without them)")
        B, N = X.shape[0], self.n_cells
        C = self.centre
        Q, _ = self.link_flow(X, cd, bv)
        acc = X.new_zeros(B, N, 3)
        dxi = (self.link_centre - C[self.link_i])
        dxj = (self.link_centre - C[self.link_j])
        acc = acc.index_add(1, self.link_i, Q[..., None] * dxi).index_add(1, self.link_j, -Q[..., None] * dxj)
        qin = bv.flow[:, self.in_patch] * self.in_share
        acc = acc.index_add(1, self.in_cell, -qin[..., None] * (self.in_centre - C[self.in_cell]))
        qout = bv.flow[:, self.out_patch] * self.out_share
        acc = acc.index_add(1, self.out_cell, qout[..., None] * (self.out_centre - C[self.out_cell]))
        if self.pref_cell.numel() and self.pref_centre is not None:
            c = self.coef
            cd_ = c.discharge_coefficient if cd is None else cd
            delta = c.pressure_smoothing_pa / c.rho
            Dp = X[..., 0][:, self.pref_cell] - bv.p_ref[:, self.pref_index]
            Qp = cd_ * self.pref_area * math.sqrt(2.0) * Dp / (Dp.square() + delta ** 2).pow(0.25)
            acc = acc.index_add(1, self.pref_cell, Qp[..., None] * (self.pref_centre - C[self.pref_cell]))
        return acc / self.volume[None, :, None]

    def initial_state(self, bv: ZonalBoundaryValues) -> torch.Tensor:
        """(B, N, 2): zero pressure and the mean inflow temperature everywhere (``zonal_network_warp`` adds the
        isothermal Picard pressures of zonal.py before Newton)."""
        B = bv.flow.shape[0]
        X = torch.zeros(B, self.n_cells, 2, dtype=torch.float64, device=self.device)
        if bv.t_fixed.shape[1]:
            X[..., 1] = bv.t_fixed.mean(dim=1, keepdim=True)
        return X

    # ------------------------------------------------------------------ observations
    def rack_inlet_temperature(self, X: torch.Tensor) -> torch.Tensor:
        """(B, n_racks): the CBBM intake mean of every server (the temperature its exhaust adds deltaT to)."""
        T = X[..., 1]
        return X.new_zeros(X.shape[0], len(self.racks)).index_add(1, self.rack_of, self.rack_share * T[:, self.rack_cell])

    def cells_at(self, points: np.ndarray, *, candidates: int = 400) -> np.ndarray:
        """The zone of every point: with ``cell_geometry``, the zone whose polyhedron contains it (ray parity, the
        majority of three rays, over the ``candidates`` zones with the nearest centres); the nearest centre where no
        zone contains it (a point in a solid or outside the fluid) and without geometry. On the Base201 L0 mesh the
        nearest centre was another zone for 43 of the 148 room sensors (agglomerated cells are not convex)."""
        C = self.centre.detach().cpu().numpy()
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        out = np.empty(len(pts), dtype=np.int64)
        for k in range(0, len(pts), 4096):
            d = ((pts[k:k + 4096, None, :] - C[None, :, :]) ** 2).sum(-1)
            out[k:k + 4096] = d.argmin(1)
        g = self.cell_geometry
        if g is None:
            return out
        tris = self._zone_triangles()
        rays = np.array([[0.5773, 0.6123, 0.5401], [-0.7071, 0.3162, 0.6325], [0.2673, -0.8018, 0.5345]])
        rays /= np.linalg.norm(rays, axis=1, keepdims=True)
        for i, p in enumerate(pts):
            order = np.argsort(((C - p) ** 2).sum(1))[:candidates]
            for c in order:
                a, b, e = tris[int(c)]
                if a.shape[0] and sum(_ray_parity(p, r, a, b, e) for r in rays) >= 2:
                    out[i] = int(c)
                    break
        return out

    def _zone_triangles(self):
        # every zone's boundary as a fan triangulation of its faces: (vertex a, edge b - a, edge c - a) per triangle
        cached = getattr(self, "_tri_cache", None)
        if cached is not None:
            return cached
        g = self.cell_geometry
        P, off, fp = (np.asarray(g[k]) for k in ("points", "face_offsets", "face_points"))
        own, nei = np.asarray(g["owner"], dtype=np.int64), np.asarray(g["neighbour"], dtype=np.int64)
        per = [[] for _ in range(self.n_cells)]
        for f in range(len(own)):
            per[int(own[f])].append(f)
        for f in range(len(nei)):
            per[int(nei[f])].append(f)
        tris = []
        for faces in per:
            A, B, E = [], [], []
            for f in faces:
                v = P[fp[off[f]:off[f + 1]]]
                for j in range(1, len(v) - 1):
                    A.append(v[0]); B.append(v[j] - v[0]); E.append(v[j + 1] - v[0])
            z = np.zeros((0, 3))
            tris.append((np.asarray(A) if A else z, np.asarray(B) if B else z, np.asarray(E) if E else z))
        object.__setattr__(self, "_tri_cache", tris)
        return tris


# ---------------------------------------------------------------------------------------------- construction
def _ray_parity(p, d, a, e1, e2) -> int:
    """1 when the ray p + t d (t > 0) crosses the triangles (a, a + e1, a + e2) an odd number of times
    (Moller-Trumbore, vectorised over the triangles)."""
    h = np.cross(d, e2)
    det = (e1 * h).sum(1)
    ok = np.abs(det) > 1e-14
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = p - a
    u = (s * h).sum(1) * inv
    q = np.cross(s, e1)
    w = (q @ d) * inv
    t = (e2 * q).sum(1) * inv
    hit = ok & (u >= 0.0) & (u <= 1.0) & (w >= 0.0) & (u + w <= 1.0) & (t > 0.0)
    return int(hit.sum()) % 2


def _val(entry: dict | None, default=None):
    if entry is None:
        return default
    if isinstance(entry, dict):
        return entry.get("val", default)
    return entry


def _toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _aggregate(cells: np.ndarray, weights: np.ndarray, key: np.ndarray | None = None):
    """Sum ``weights`` per cell (per (key, cell) when key is given): (cells, keys, sums)."""
    k = np.zeros_like(cells) if key is None else key
    pair = np.stack((k, cells), 1)
    uniq, inv = np.unique(pair, axis=0, return_inverse=True)
    sums = np.bincount(inv.reshape(-1), weights=weights, minlength=len(uniq))
    return uniq[:, 1], uniq[:, 0], sums


def build_zonal_mesh(case_dir: str | Path, bc_dir: str | Path, fvconfig: str | Path | dict | None = None, *,
                     coefficients: ZonalCoefficients | None = None, device: str | torch.device = "cpu",
                     keep: str | None = "connected_open", case=None) -> ZonalMesh:
    """The zonal network of the OpenFOAM case ``case_dir`` (serial or decomposed) with the boundary conditions of
    ``bc_dir`` (``U.toml``, ``T.toml``, ``p_rgh.toml`` as ``Parse_mesh/dctwin_bc.py`` writes them) and the physical
    constants of ``fvconfig`` (``[theta_pde]``: beta, TRef, nu, g)."""
    from Parse_mesh.foamio.decomposed import read_case

    coef = coefficients or ZonalCoefficients()
    case = case if case is not None else read_case(case_dir, keep=keep)
    bc_dir = Path(bc_dir)
    U_bc = _toml(bc_dir / "U.toml").get("boundaryfield", {})
    T_bc = _toml(bc_dir / "T.toml").get("boundaryfield", {})
    P_bc = _toml(bc_dir / "p_rgh.toml").get("boundaryfield", {})
    if fvconfig is None:
        cfg = {}
    elif isinstance(fvconfig, dict):
        cfg = fvconfig
    else:
        cfg = _toml(Path(fvconfig))
    tp = cfg.get("theta_pde", {})
    beta = float(_val(tp.get("beta"), 0.0033653))
    t_ref = float(_val(tp.get("TRef"), 0.0))
    nu = float(_val(tp.get("nu"), 1.5e-5))
    g = tuple(float(v) for v in _val(tp.get("g"), [0.0, -9.81, 0.0]))
    gv = np.asarray(g)

    N = case.n_cells
    Fi = case.n_internal_faces
    owner, neigh = np.asarray(case.owner, dtype=np.int64), np.asarray(case.neighbour, dtype=np.int64)
    magSf, Cf = np.asarray(case.magSf, dtype=np.float64), np.asarray(case.Cf, dtype=np.float64)

    # interior links: one per (owner, neighbour) pair; owner < neighbour on every OpenFOAM interior face
    a, b = owner[:Fi], neigh
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    pairs, inv = np.unique(np.stack((lo, hi), 1), axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    area = np.bincount(inv, weights=magSf[:Fi], minlength=len(pairs))
    ghf = np.bincount(inv, weights=magSf[:Fi] * (Cf[:Fi] @ gv), minlength=len(pairs)) / np.maximum(area, 1e-300)

    def centroid(index, faces, total, m):
        return np.stack([np.bincount(index, weights=magSf[faces] * Cf[faces, d], minlength=m) for d in range(3)],
                        1) / np.maximum(total, 1e-300)[:, None]

    li, lj = [pairs[:, 0]], [pairs[:, 1]]
    larea, lghf = [area], [ghf]
    lcen = [centroid(inv, np.arange(Fi), area, len(pairs))]
    lpor, la, lb = [np.zeros(len(pairs), bool)], [np.zeros(len(pairs))], [np.zeros(len(pairs))]
    lpp, lhl = [np.full(len(pairs), -1, np.int64)], [np.zeros(len(pairs))]
    porous_names, porous_partner, porous_i0 = [], [], []

    # cyclic baffles: a link between the two owners of every matched face pair
    cyclic_names = set()
    for cp in case.cyclic_pairs:
        cyclic_names.update((cp["patch_a"], cp["patch_b"]))
        fa, fb = np.asarray(cp["faces_a"]), np.asarray(cp["faces_b"])
        oa, ob = owner[fa], owner[fb]
        keepf = oa != ob
        if not keepf.any():
            continue
        oa, ob, fa = oa[keepf], ob[keepf], fa[keepf]
        pc, pinv = np.unique(np.stack((oa, ob), 1), axis=0, return_inverse=True)
        pinv = pinv.reshape(-1)
        A = np.bincount(pinv, weights=magSf[fa], minlength=len(pc))
        gh = np.bincount(pinv, weights=magSf[fa] * (Cf[fa] @ gv), minlength=len(pc)) / np.maximum(A, 1e-300)
        pb = P_bc.get(cp["patch_a"], {})
        if pb.get("type") == "porousBafflePressure":
            L_ = float(pb.get("length", 1.0))
            Dc, Ic = float(pb.get("D", 0.0)), float(pb.get("I", 0.0))
            if Dc <= 0.0 and Ic <= 0.0:
                raise ValueError(f"{cp['patch_a']}: porousBafflePressure without resistance")
            por, av, bvv = True, Dc * nu * L_, 0.5 * Ic * L_
            col, half = len(porous_names), 0.5 * L_
            porous_names.append(cp["patch_a"]); porous_partner.append(cp["patch_b"]); porous_i0.append(Ic)
        elif pb.get("type") in (None, "cyclic"):
            por, av, bvv = False, 0.0, 0.0
            col, half = -1, 0.0
        else:
            raise NotImplementedError(f"{cp['patch_a']}: cyclic with p_rgh {pb.get('type')!r}")
        li.append(pc[:, 0]); lj.append(pc[:, 1]); larea.append(A); lghf.append(gh)
        lcen.append(centroid(pinv, fa, A, len(pc)))
        lpor.append(np.full(len(pc), por)); la.append(np.full(len(pc), av)); lb.append(np.full(len(pc), bvv))
        lpp.append(np.full(len(pc), col, np.int64)); lhl.append(np.full(len(pc), half))

    link_i, link_j = np.concatenate(li), np.concatenate(lj)
    link_porous = np.concatenate(lpor)

    # boundaries
    flow_patches, fixed_t, racks, prefs = [], [], [], []
    ins, outs, rack_entries, pref_items = [], [], [], []
    patches = {p.name: p for p in case.patches}
    skipped, walls = [], 0
    pending_racks = []
    for p in case.patches:
        if p.size == 0 or p.name in cyclic_names:
            continue
        if p.type in SKIP_PATCH_TYPES:
            skipped.append(p.name)
            continue
        ub, tb, pb = U_bc.get(p.name, {}), T_bc.get(p.name, {}), P_bc.get(p.name, {})
        ut = ub.get("type")
        faces = np.arange(p.start, p.start + p.size)
        cells = owner[faces]
        share_cells, _, share = _aggregate(cells, magSf[faces])
        fcen = np.stack([_aggregate(cells, magSf[faces] * Cf[faces, d])[2] for d in range(3)], 1) / share[:, None]
        share = share / share.sum()
        if ut in ("flowRateInletVelocity", "flowRateOutletVelocity"):
            col = len(flow_patches)
            flow_patches.append((p.name, float(_val(ub.get("volumetricFlowRate"), 0.0))))
            if ut == "flowRateOutletVelocity":
                outs.append((share_cells, np.full(len(share_cells), col), share, fcen))
                continue
            tt = tb.get("type", "zeroGradient")
            if tt == "fixedValue":
                mode, idx = 0, len(fixed_t)
                fixed_t.append((p.name, float(_val(tb.get("value"), 0.0))))
            elif tt == "rackOutletTemperature":
                if str(tb.get("model", "cbbm")).lower() != "cbbm" or tb.get("lagMean", False):
                    raise NotImplementedError(f"{p.name}: rackOutletTemperature {tb.get('model')} lagMean="
                                              f"{tb.get('lagMean')} (the zonal model takes the CBBM mean)")
                mode, idx = 1, len(racks)
                racks.append((p.name, float(tb.get("deltaT", _val(tb.get("value"), 0.0)))))
                pending_racks.append((idx, tb["partner_patch"]))
            elif tt == "zeroGradient":
                mode, idx = 2, 0
            else:
                raise NotImplementedError(f"{p.name}: inflow temperature {tt!r}")
            ins.append((share_cells, np.full(len(share_cells), col), share, np.full(len(share_cells), mode),
                        np.full(len(share_cells), idx), fcen))
        elif ut == "zeroGradient" and pb.get("type") in ("fixedValue", "totalPressure", "prghPressure"):
            if pb.get("type") != "fixedValue":
                raise NotImplementedError(f"{p.name}: p_rgh {pb.get('type')!r}")
            col = len(prefs)
            prefs.append((p.name, float(_val(pb.get("value"), 0.0))))
            tcol = -1
            if tb.get("type") == "fixedValue":
                tcol = len(fixed_t)
                fixed_t.append((p.name, float(_val(tb.get("value"), 0.0))))
            elif tb.get("type") not in (None, "zeroGradient", "inletOutlet"):
                raise NotImplementedError(f"{p.name}: opening temperature {tb.get('type')!r}")
            pc_, _, pa = _aggregate(cells, magSf[faces])
            pref_items.append((pc_, np.full(len(pc_), col), pa, np.full(len(pc_), tcol), fcen))
        elif ut in WALL_U_TYPES or (ut == "fixedValue" and np.allclose(_val(ub.get("value"), [0, 0, 0]), 0.0)):
            if tb.get("type") not in (None, "zeroGradient"):
                raise NotImplementedError(f"{p.name}: wall temperature {tb.get('type')!r} (adiabatic walls only)")
            walls += 1
        else:
            raise NotImplementedError(f"{p.name}: U {ut!r} / p_rgh {pb.get('type')!r} is not a zonal boundary")
    for idx, partner in pending_racks:
        q = patches.get(partner)
        if q is None or q.size == 0:
            raise ValueError(f"rack {racks[idx][0]}: partner patch {partner!r} has no faces")
        faces = np.arange(q.start, q.start + q.size)
        rc, _, rs = _aggregate(owner[faces], magSf[faces])
        rack_entries.append((rc, np.full(len(rc), idx), rs / rs.sum()))

    def cat(items, k, dtype):
        return np.concatenate([it[k] for it in items]).astype(dtype) if items else np.zeros(0, dtype)

    def cat3(items, k):
        return np.concatenate([it[k] for it in items]) if items else np.zeros((0, 3))

    t64 = lambda a_, dt=torch.float64: torch.as_tensor(a_, dtype=dt, device=device)  # noqa: E731
    lng = torch.long
    defaults = ZonalBoundaryValues(
        flow=t64([[q for _, q in flow_patches]]).reshape(1, -1),
        t_fixed=t64([[t for _, t in fixed_t]]).reshape(1, -1),
        delta_t=t64([[d for _, d in racks]]).reshape(1, -1),
        p_ref=t64([[v for _, v in prefs]]).reshape(1, -1),
        porous_i=t64([porous_i0]).reshape(1, -1),
    )
    net = ZonalMesh(
        n_cells=N, volume=t64(case.V), centre=t64(case.C), beta=beta, t_ref=t_ref, nu=nu, gravity=g, coef=coef,
        link_i=t64(link_i, lng), link_j=t64(link_j, lng), link_area=t64(np.concatenate(larea)),
        link_ghf=t64(np.concatenate(lghf)), link_porous=t64(link_porous, torch.bool),
        link_a=t64(np.concatenate(la)), link_b=t64(np.concatenate(lb)),
        link_mix=t64(np.ones(len(link_i), bool), torch.bool),
        in_cell=t64(cat(ins, 0, np.int64), lng), in_patch=t64(cat(ins, 1, np.int64), lng),
        in_share=t64(cat(ins, 2, np.float64)), in_mode=t64(cat(ins, 3, np.int64), lng),
        in_index=t64(cat(ins, 4, np.int64), lng),
        out_cell=t64(cat(outs, 0, np.int64), lng), out_patch=t64(cat(outs, 1, np.int64), lng),
        out_share=t64(cat(outs, 2, np.float64)),
        rack_cell=t64(cat(rack_entries, 0, np.int64), lng), rack_of=t64(cat(rack_entries, 1, np.int64), lng),
        rack_share=t64(cat(rack_entries, 2, np.float64)),
        pref_cell=t64(cat(pref_items, 0, np.int64), lng), pref_index=t64(cat(pref_items, 1, np.int64), lng),
        pref_area=t64(cat(pref_items, 2, np.float64)), pref_t_fixed=t64(cat(pref_items, 3, np.int64), lng),
        flow_patches=[n for n, _ in flow_patches], fixed_t_patches=[n for n, _ in fixed_t],
        racks=[n for n, _ in racks], pref_patches=[n for n, _ in prefs], default_values=defaults,
        link_centre=t64(np.concatenate(lcen)), in_centre=t64(cat3(ins, 5)), out_centre=t64(cat3(outs, 3)),
        pref_centre=t64(cat3(pref_items, 4)),
        porous_patches=porous_names, porous_partners=porous_partner,
        link_ppatch=t64(np.concatenate(lpp), lng), link_half_len=t64(np.concatenate(lhl)),
        cell_geometry={"points": np.asarray(case.points, dtype=np.float64), "face_offsets": np.asarray(case.face_offsets),
                       "face_points": np.asarray(case.face_points), "owner": owner, "neighbour": neigh},
    )
    in_cols = {int(c) for it in ins for c in it[1][:1]}
    inflow = sum(q for k, (_, q) in enumerate(flow_patches) if k in in_cols)
    outflow = sum(q for k, (_, q) in enumerate(flow_patches) if k not in in_cols)
    net.report = {
        "version": VERSION, "cells": N, "links": int(len(link_i)), "porous_links": int(link_porous.sum()),
        "interior_faces": int(Fi), "flow_patches": len(flow_patches), "inflow_patches": len(ins),
        "outflow_patches": len(outs), "racks": len(racks), "pressure_openings": len(prefs),
        "wall_patches": walls, "skipped_patches": skipped, "gauge": not prefs,
        "inflow_m3s": float(inflow), "outflow_m3s": float(outflow),
    }
    return net
