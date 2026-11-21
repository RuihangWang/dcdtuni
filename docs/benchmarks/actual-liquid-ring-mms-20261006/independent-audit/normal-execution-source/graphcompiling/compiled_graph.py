"""
CompiledGraph dataclass and supporting types for the liquid cooling solver.

All graph topology, physical parameters, and solver configuration are bundled
into a single CompiledGraph so downstream code never needs to look up graph
structure separately.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

import torch
from torch import Tensor


def _move_dataclass(obj, device, dtype=None):
    """Recursively move all Tensor fields in a dataclass to *device*."""
    if obj is None:
        return obj
    changes = {}
    for f in fields(obj):
        val = getattr(obj, f.name)
        if isinstance(val, Tensor):
            changes[f.name] = val.to(device=device, dtype=dtype if val.is_floating_point() else None)
        elif dataclasses.is_dataclass(val) and not isinstance(val, type):
            changes[f.name] = _move_dataclass(val, device, dtype)
    if changes:
        return dataclasses.replace(obj, **changes)
    return obj


# ---------------------------------------------------------------------------
# Pipe
# ---------------------------------------------------------------------------

@dataclass
class PipeIndices:
    edge_ids: Tensor    # [N_pipes] global edge indices
    node_from: Tensor   # [N_pipes] global node indices
    node_to: Tensor     # [N_pipes]
    cell_start: Tensor  # [N_pipes] first cell index in global cell array
    cell_end: Tensor    # [N_pipes] one-past-last cell index


@dataclass
class PipeBatchParams:
    D: Tensor          # [N_pipes]
    L: Tensor          # [N_pipes]
    dh: Tensor         # [N_pipes]
    K_minor: Tensor    # [N_pipes]
    A: Tensor          # [N_pipes]
    roughness: Tensor  # [N_pipes]


# ---------------------------------------------------------------------------
# Cold plate
# ---------------------------------------------------------------------------

@dataclass
class ColdPlateIndices:
    edge_ids: Tensor
    node_from: Tensor
    node_to: Tensor
    cp_ids: Tensor


@dataclass
class ColdPlateBatchParams:
    dp_a: Tensor
    dp_b: Tensor
    UA: Tensor
    C_solid: Tensor
    V_fluid: Tensor
    Q_W: Tensor   # [N_cp, N_t]


# ---------------------------------------------------------------------------
# Valve
# ---------------------------------------------------------------------------

@dataclass
class ValveIndices:
    edge_ids: Tensor
    node_from: Tensor
    node_to: Tensor


@dataclass
class ValveBatchParams:
    D: Tensor
    Kv_max: Tensor
    opening: Tensor        # [N_valves, N_t]
    characteristic: Tensor  # [N_valves] int


# ---------------------------------------------------------------------------
# Pump
# ---------------------------------------------------------------------------

@dataclass
class PumpIndices:
    edge_ids: Tensor
    node_from: Tensor
    node_to: Tensor


@dataclass
class PumpBatchParams:
    H0: Tensor
    a_coef: Tensor
    rpm_ratio: Tensor  # [N_pumps, N_t]


# ---------------------------------------------------------------------------
# Heat exchanger (CDU)
# ---------------------------------------------------------------------------

@dataclass
class HeatExchangerIndices:
    edge_ids: Tensor      # [N_hx] global edge indices
    node_from: Tensor     # [N_hx] from-node indices
    node_to: Tensor       # [N_hx] to-node indices


@dataclass
class HeatExchangerBatchParams:
    UA: Tensor            # [N_hx] overall heat transfer coefficient (W/K)
    T_secondary: Tensor   # [N_hx] secondary-side (facility water) temperature (K)
    dp_a: Tensor          # [N_hx] quadratic pressure drop coefficient
    dp_b: Tensor          # [N_hx] linear pressure drop coefficient


# ---------------------------------------------------------------------------
# Rack metadata
# ---------------------------------------------------------------------------

@dataclass
class RackMeta:
    rack_id: str
    server_ids: list[str]
    server_edge_ids: list[list[str]]


# ---------------------------------------------------------------------------
# CompiledGraph — top-level container
# ---------------------------------------------------------------------------

@dataclass
class CompiledGraph:
    # --- topology sizes ---
    N_nodes: int
    N_edges: int
    N_cells: int
    N_cp: int
    N_t: int

    # --- node data ---
    node_ids: list[str]
    node_position: Tensor   # [N_nodes, 3]
    bc_mask: Tensor         # [N_nodes] bool — True where pressure/temp are fixed
    bc_P: Tensor            # [N_nodes]
    bc_T: Tensor            # [N_nodes]
    bc_T_mask: Tensor       # [N_nodes] bool — True where T is prescribed (Dirichlet)
    junction_mask: Tensor   # [N_nodes] bool

    # --- junction indexing (for T_node state variable) ---
    N_junctions: int                    # count of junction (non-BC) nodes
    max_junction_depth: int             # max BFS depth through junction-only chains
    junction_indices: Tensor            # [N_junctions] indices into global node array
    node_to_junction: Tensor            # [N_nodes] maps node → junction index (-1 for BC)

    # --- edge data ---
    edge_str_ids: list[str]  # [N_edges] string IDs from config
    edge_from: Tensor   # [N_edges] long
    edge_to: Tensor     # [N_edges] long
    edge_type: Tensor   # [N_edges] long

    # --- incidence matrix ---
    incidence: Tensor   # [N_nodes, N_edges] sparse (or dense)

    # --- component indices ---
    pipe_indices: Optional[PipeIndices]
    cp_indices: Optional[ColdPlateIndices]
    valve_indices: Optional[ValveIndices]
    pump_indices: Optional[PumpIndices]
    hx_indices: Optional[HeatExchangerIndices]

    # --- component parameters ---
    pipe_params: Optional[PipeBatchParams]
    cp_params: Optional[ColdPlateBatchParams]
    valve_params: Optional[ValveBatchParams]
    pump_params: Optional[PumpBatchParams]
    hx_params: Optional[HeatExchangerBatchParams]

    # --- component counts ---
    N_hx: int

    # --- cell data (1-D discretization of each pipe) ---
    cell_edge_id: Tensor    # [N_cells] PIPE-LOCAL owner; map via pipe_indices.edge_ids for global edges
    cell_ds: Tensor         # [N_cells] cell length
    cell_volume: Tensor     # [N_cells]
    cell_dh: Tensor         # [N_cells] hydraulic diameter
    cell_position: Tensor   # [N_cells, 3]

    # --- adjacency lists (pre-built for fast iteration) ---
    node_in_edges: list[list[int]]   # [N_nodes] incoming edge indices
    node_out_edges: list[list[int]]  # [N_nodes] outgoing edge indices

    # --- time-stepping ---
    dt: float
    bdf_order: int
    T_fluid_init: float
    T_solid_init: float
    P_init: float

    # --- solver knobs (arbitrary config object) ---
    solver_config: object

    # --- per-rack metadata ---
    rack_meta: list[RackMeta]

    # --- fluid properties ---
    fluid_rho: float
    fluid_cp: float
    fluid_mu: float
    g_mag: float

    # --- verification overrides (None for every ordinary run) ---
    # Distributed heat input per cell, in WATTS, per time step [N_cells, N_t].
    # None (or all-zero) for every ordinary run; a manufactured-solution case
    # injects its analytic source term here.
    cell_heat_W: Optional[Tensor] = None
    # Per-cell initial fluid temperature [N_cells]; overrides the scalar
    # ``T_fluid_init`` when present.  A manufactured-solution case needs an
    # x-dependent initial field, which a single scalar cannot express.
    cell_T_init: Optional[Tensor] = None
    # Time-varying Dirichlet node temperature [N_nodes, N_t]; overrides the
    # constant ``bc_T`` when present.
    bc_T_series: Optional[Tensor] = None

    # --- .liquidrun v4 endpoint forms (None = v3 behaviour) ---
    # Time-varying node pressure [N_nodes, N_t]; overrides ``bc_P`` in the
    # residual. ``bc_P`` stays the 1-D device/dtype reference.
    bc_P_series: Optional[Tensor] = None
    # Flow-controlled pumps [N_pump] bool and their set point [N_pump, N_t] (kg/s).
    pump_flow_mode: Optional[Tensor] = None
    pump_flow_set: Optional[Tensor] = None
    # Cold plates whose coolant temperature rise is prescribed [N_cp] bool and
    # the rise [N_cp, N_t] (K); their heat is |m|·cp·dT.
    cp_rise_mode: Optional[Tensor] = None
    cp_dT_set: Optional[Tensor] = None
    # Heat exchangers holding a secondary supply set point [N_hx] bool and the
    # set point [N_hx, N_t] (K).
    hx_setpoint_mode: Optional[Tensor] = None
    hx_T_set: Optional[Tensor] = None

    # ------------------------------------------------------------------
    # Device transfer
    # ------------------------------------------------------------------

    def to(self, device: torch.device | str, dtype: torch.dtype | None = None) -> CompiledGraph:
        """Move the complete graph once; preserve integer indices and masks."""
        if dtype is not None and dtype not in (torch.float32, torch.float64):
            raise ValueError('liquid graph precision must be float32 or float64')
        return _move_dataclass(self, device, dtype)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_json(self, path: Path) -> None:
        """Serialize topology info to JSON for debugging.

        Writes a subset of scalar and tensor fields.  Tensors are converted to
        nested Python lists; sparse tensors are densified first.
        """

        def _t(tensor: Tensor) -> list:
            """Convert a (possibly sparse) tensor to a nested list."""
            t = tensor
            if t.is_sparse:
                t = t.to_dense()
            return t.tolist()

        data = {
            "N_nodes": self.N_nodes,
            "N_edges": self.N_edges,
            "N_cells": self.N_cells,
            "N_cp": self.N_cp,
            "N_t": self.N_t,
            "node_ids": self.node_ids,
            "node_position": _t(self.node_position),
            "bc_mask": _t(self.bc_mask),
            "edge_from": _t(self.edge_from),
            "edge_to": _t(self.edge_to),
            "edge_type": _t(self.edge_type),
            "dt": self.dt,
            "bdf_order": self.bdf_order,
            "fluid_rho": self.fluid_rho,
            "g_mag": self.g_mag,
        }

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
