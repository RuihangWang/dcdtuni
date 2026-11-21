"""
Compiler: transform a validated .liquidrun config into a CompiledGraph.

The compiler builds index tensors, batched parameter tensors, and a sparse
incidence matrix that the solver can consume directly.
"""

from __future__ import annotations

import math
from collections import deque
from typing import List, Optional, Tuple

import torch
from torch import Tensor

from backend.liquidcooling.schema import LiquidrunConfig, EdgeConfig
from backend.liquidcooling.graphcompiling.compiled_graph import (
    CompiledGraph,
    PipeIndices,
    PipeBatchParams,
    PumpIndices,
    PumpBatchParams,
    ColdPlateIndices,
    ColdPlateBatchParams,
    ValveIndices,
    ValveBatchParams,
    HeatExchangerIndices,
    HeatExchangerBatchParams,
    RackMeta,
)


# Local loss of one tee leg (standard tee branch, Idelchik), and the rule that
# places it, registered in every DAE provenance record as solver_laws.tee_law.
# v1 added K to each PIPE touching a node of degree >= 3, so a branch that
# left its tee through a flow-control valve carried none; v2 gives every leg
# its K, following a zero-volume valve chain to the leg's pipe.
K_TEE_DEFAULT = 0.9
TEE_LAW = 'tee_K0p9_per_leg_through_valves/v2'


# ── edge type encoding ──────────────────────────────────────────────────────

_EDGE_TYPE_TO_INT = {
    "pipe": 0,
    "cold_plate": 1,
    "valve": 2,
    "pump": 3,
    "heat_exchanger": 4,
}


# ── polyline helpers ─────────────────────────────────────────────────────────

def _interp_polyline(
    pts: Tensor,
    cumlen: Tensor,
    s: float,
) -> Tensor:
    """Interpolate a 3-D position along a polyline at arc-length *s*.

    Parameters
    ----------
    pts : Tensor [N_pts, 3]
        Ordered waypoint positions.
    cumlen : Tensor [N_pts]
        Cumulative arc-length at each waypoint (``cumlen[0] == 0``).
    s : float
        Arc-length at which to interpolate (clamped to ``[0, cumlen[-1]]``).

    Returns
    -------
    Tensor [3]
        Interpolated 3-D position.
    """
    total = cumlen[-1].item()
    s = max(0.0, min(s, total))

    # Find the segment that contains s
    for i in range(1, len(cumlen)):
        if cumlen[i].item() >= s:
            seg_start = cumlen[i - 1].item()
            seg_len = cumlen[i].item() - seg_start
            if seg_len < 1e-15:
                return pts[i].clone()
            t = (s - seg_start) / seg_len
            return pts[i - 1] * (1.0 - t) + pts[i] * t

    # Fallback: return the last point
    return pts[-1].clone()


def _cell_heat_series(
    spec, cell_index: int, n_cells: int, cell_ds: float, N_t: int
) -> List[float]:
    """Distributed pipe heat input for one cell, in WATTS, over all time steps.

    `heat_source_W_per_m` on a pipe edge accepts, in increasing generality:

      * a scalar        -- uniform along the pipe and constant in time
      * [n_cells]       -- per cell, constant in time
      * [n_cells][N_t]  -- per cell, per time step

    The last form is what code verification needs: a manufactured solution's
    source term varies in both x and t, and there is no way to impose it through
    a scalar. Physically the same field is an ambient gain or loss along the run.

    Returned in watts (the per-metre value times this cell's length) because the
    energy balance is written in watts; converting once here keeps the solve
    paths from each having to remember the unit.
    """
    if spec is None:
        return [0.0] * N_t
    if isinstance(spec, (int, float)):
        return [float(spec) * cell_ds] * N_t
    if not isinstance(spec, (list, tuple)):
        raise TypeError(
            f"heat_source_W_per_m must be a number or a list, got {type(spec).__name__}"
        )
    if len(spec) != n_cells:
        raise ValueError(
            f"heat_source_W_per_m has {len(spec)} entries but the pipe has "
            f"{n_cells} cells; a per-cell source must match the cell count"
        )
    entry = spec[cell_index]
    if isinstance(entry, (int, float)):
        return [float(entry) * cell_ds] * N_t
    if len(entry) != N_t:
        raise ValueError(
            f"heat_source_W_per_m[{cell_index}] has {len(entry)} time samples "
            f"but the run has {N_t}; a time-varying source must match the step count"
        )
    return [float(v) * cell_ds for v in entry]


def _compute_cumlen(pts: Tensor) -> Tensor:
    """Compute cumulative arc-length along a polyline.

    Parameters
    ----------
    pts : Tensor [N_pts, 3]

    Returns
    -------
    Tensor [N_pts]
        ``cumlen[0] == 0``, ``cumlen[-1] == total_length``.
    """
    diffs = pts[1:] - pts[:-1]  # [N_pts-1, 3]
    seg_lengths = diffs.norm(dim=1)  # [N_pts-1]
    cumlen = torch.zeros(len(pts), dtype=pts.dtype)
    cumlen[1:] = torch.cumsum(seg_lengths, dim=0)
    return cumlen


# ── pipe compilation ─────────────────────────────────────────────────────────

def _compile_pipes(
    pipe_edges: List[Tuple[int, EdgeConfig, int, int]],
    gravity: Tensor,
    dtype: torch.dtype,
    N_t: int,
) -> Tuple[
    PipeIndices,
    PipeBatchParams,
    Tensor,   # cell_edge_id
    Tensor,   # cell_ds
    Tensor,   # cell_volume
    Tensor,   # cell_dh
    Tensor,   # cell_position
    Tensor,   # cell_heat_W [N_cells, N_t]
    int,      # total cells
]:
    """Compile all pipe edges into batched tensors.

    Parameters
    ----------
    pipe_edges : list of (edge_index, EdgeConfig, node_from_idx, node_to_idx)
    gravity : Tensor [3]
    dtype : torch.dtype

    Returns
    -------
    Tuple of PipeIndices, PipeBatchParams, cell arrays, total_cells.
    """
    g_norm = gravity.norm().item()
    if g_norm > 1e-12:
        g_unit = gravity / g_norm
    else:
        g_unit = torch.zeros(3, dtype=dtype)

    N_pipes = len(pipe_edges)

    # Per-pipe scalars
    edge_ids_list: List[int] = []
    node_from_list: List[int] = []
    node_to_list: List[int] = []
    D_list: List[float] = []
    L_list: List[float] = []
    dh_list: List[float] = []
    K_minor_list: List[float] = []
    A_list: List[float] = []
    roughness_list: List[float] = []
    cell_start_list: List[int] = []
    cell_end_list: List[int] = []

    # Cell arrays (accumulated)
    all_cell_edge_id: List[int] = []
    all_cell_ds: List[float] = []
    all_cell_volume: List[float] = []
    # Distributed heat input per cell, in W. Optional and zero by default, so
    # every existing case compiles to exactly what it compiled to before.
    all_cell_heat_W: List[List[float]] = []
    all_cell_dh: List[float] = []
    all_cell_pos: List[List[float]] = []

    cell_offset = 0

    for pipe_idx, (edge_idx, edge, nf, nt) in enumerate(pipe_edges):
        params = edge.params
        D = float(params["D_m"])
        roughness = float(params.get("roughness_m", 1.5e-6))
        n_cells = int(params["n_cells"])
        waypoints = params["waypoints_m"]
        K_minor_per_bend = float(params.get("K_minor_per_bend", 0.5))
        heat_source = params.get("heat_source_W_per_m", 0.0)

        pts = torch.tensor(waypoints, dtype=dtype)  # [N_wp, 3]
        cumlen = _compute_cumlen(pts)
        total_L = cumlen[-1].item()

        # Elevation change: dh = -sum(segments . g_unit)
        # positive dh = upward against gravity
        segments = pts[1:] - pts[:-1]  # [N_seg, 3]
        dh = -(segments @ g_unit).sum().item()

        # Bend detection: count where cosine between consecutive segments < 0.95
        n_bends = 0
        if len(segments) >= 2:
            for j in range(len(segments) - 1):
                s1 = segments[j]
                s2 = segments[j + 1]
                n1 = s1.norm().item()
                n2 = s2.norm().item()
                if n1 > 1e-12 and n2 > 1e-12:
                    cos_angle = (s1 @ s2).item() / (n1 * n2)
                    cos_angle = max(-1.0, min(1.0, cos_angle))
                    if cos_angle < 0.95:
                        n_bends += 1

        K_minor = n_bends * K_minor_per_bend
        # A valve at an authored bend can move that bend to two pipe endpoints.
        # Its explicit residual loss belongs to one segment, without new states.
        additional_K = params.get("K_minor_additional")
        if additional_K is not None:
            if (isinstance(additional_K, bool) or not isinstance(additional_K, (int, float))
                    or not math.isfinite(additional_K) or additional_K < 0):
                raise ValueError("Pipe K_minor_additional must be a finite non-negative number")
            K_minor += float(additional_K)
        A = math.pi / 4.0 * D ** 2

        edge_ids_list.append(edge_idx)
        node_from_list.append(nf)
        node_to_list.append(nt)
        D_list.append(D)
        L_list.append(total_L)
        dh_list.append(dh)
        K_minor_list.append(K_minor)
        A_list.append(A)
        roughness_list.append(roughness)
        cell_start_list.append(cell_offset)
        cell_end_list.append(cell_offset + n_cells)

        # Subdivide pipe into thermal cells
        cell_ds_val = total_L / n_cells if n_cells > 0 else 0.0
        cell_vol = A * cell_ds_val

        for c in range(n_cells):
            s_center = (c + 0.5) * cell_ds_val
            s_lo = c * cell_ds_val
            s_hi = (c + 1) * cell_ds_val

            # Cell position at center
            pos = _interp_polyline(pts, cumlen, s_center)
            all_cell_pos.append(pos.tolist())

            # Cell elevation change
            p_lo = _interp_polyline(pts, cumlen, s_lo)
            p_hi = _interp_polyline(pts, cumlen, s_hi)
            seg_vec = p_hi - p_lo
            cell_dh_val = -(seg_vec @ g_unit).item()

            all_cell_edge_id.append(pipe_idx)
            all_cell_ds.append(cell_ds_val)
            all_cell_volume.append(cell_vol)
            all_cell_dh.append(cell_dh_val)
            all_cell_heat_W.append(
                _cell_heat_series(heat_source, c, n_cells, cell_ds_val, N_t)
            )

        cell_offset += n_cells

    total_cells = cell_offset

    pipe_indices = PipeIndices(
        edge_ids=torch.tensor(edge_ids_list, dtype=torch.long),
        node_from=torch.tensor(node_from_list, dtype=torch.long),
        node_to=torch.tensor(node_to_list, dtype=torch.long),
        cell_start=torch.tensor(cell_start_list, dtype=torch.long),
        cell_end=torch.tensor(cell_end_list, dtype=torch.long),
    )

    pipe_params = PipeBatchParams(
        D=torch.tensor(D_list, dtype=dtype),
        L=torch.tensor(L_list, dtype=dtype),
        dh=torch.tensor(dh_list, dtype=dtype),
        K_minor=torch.tensor(K_minor_list, dtype=dtype),
        A=torch.tensor(A_list, dtype=dtype),
        roughness=torch.tensor(roughness_list, dtype=dtype),
    )

    cell_edge_id = torch.tensor(all_cell_edge_id, dtype=torch.long) if all_cell_edge_id else torch.zeros(0, dtype=torch.long)
    cell_ds = torch.tensor(all_cell_ds, dtype=dtype) if all_cell_ds else torch.zeros(0, dtype=dtype)
    cell_volume = torch.tensor(all_cell_volume, dtype=dtype) if all_cell_volume else torch.zeros(0, dtype=dtype)
    cell_dh = torch.tensor(all_cell_dh, dtype=dtype) if all_cell_dh else torch.zeros(0, dtype=dtype)
    cell_position = torch.tensor(all_cell_pos, dtype=dtype) if all_cell_pos else torch.zeros(0, 3, dtype=dtype)
    cell_heat_W = (
        torch.tensor(all_cell_heat_W, dtype=dtype)
        if all_cell_heat_W
        else torch.zeros(0, max(N_t, 1), dtype=dtype)
    )

    return (
        pipe_indices,
        pipe_params,
        cell_edge_id,
        cell_ds,
        cell_volume,
        cell_dh,
        cell_position,
        cell_heat_W,
        total_cells,
    )


# ── pump compilation ─────────────────────────────────────────────────────────

def _compile_pumps(
    pump_edges: List[Tuple[int, EdgeConfig, int, int]],
    N_t: int,
    events: list,
    dt_s: float,
    dtype: torch.dtype,
) -> Tuple[PumpIndices, PumpBatchParams]:
    """Compile all pump edges into batched tensors."""
    N_pumps = len(pump_edges)

    edge_ids_list: List[int] = []
    node_from_list: List[int] = []
    node_to_list: List[int] = []
    H0_list: List[float] = []
    a_coef_list: List[float] = []
    rpm_ratio_list: List[List[float]] = []

    edge_id_to_local: dict[str, int] = {}

    for local_idx, (edge_idx, edge, nf, nt) in enumerate(pump_edges):
        params = edge.params
        edge_ids_list.append(edge_idx)
        node_from_list.append(nf)
        node_to_list.append(nt)
        H0_list.append(float(params.get("H0_m", params.get("H0", 0.0))))
        a_coef_list.append(float(params.get("a_coef", 0.0)))
        edge_id_to_local[edge.id] = local_idx

        # rpm_ratio can be a scalar (expanded to [N_t]) or a list
        rpm_raw = params.get("rpm_ratio", 1.0)
        if isinstance(rpm_raw, (int, float)):
            rpm_ratio_list.append([float(rpm_raw)] * N_t)
        else:
            # Pad or truncate to N_t
            rpm = [float(v) for v in rpm_raw]
            if len(rpm) < N_t:
                rpm.extend([rpm[-1]] * (N_t - len(rpm)))
            rpm_ratio_list.append(rpm[:N_t])

    # Apply events that target rpm_ratio
    for ev in events:
        if ev.target in edge_id_to_local and ev.field == "rpm_ratio":
            local = edge_id_to_local[ev.target]
            step_idx = min(int(ev.time_s / dt_s), N_t - 1)
            for t in range(step_idx, N_t):
                rpm_ratio_list[local][t] = ev.value

    pump_indices = PumpIndices(
        edge_ids=torch.tensor(edge_ids_list, dtype=torch.long),
        node_from=torch.tensor(node_from_list, dtype=torch.long),
        node_to=torch.tensor(node_to_list, dtype=torch.long),
    )

    pump_params = PumpBatchParams(
        H0=torch.tensor(H0_list, dtype=dtype),
        a_coef=torch.tensor(a_coef_list, dtype=dtype),
        rpm_ratio=torch.tensor(rpm_ratio_list, dtype=dtype),  # [N_pumps, N_t]
    )

    return pump_indices, pump_params


def _compile_valves(
    valve_edges: List[Tuple[int, EdgeConfig, int, int]],
    N_t: int,
    events: list,
    dt_s: float,
    dtype: torch.dtype,
) -> Tuple[ValveIndices, ValveBatchParams]:
    """Compile all valve edges into batched tensors."""
    char_map = {"linear": 0, "equal_percentage": 1, "quick_opening": 2}

    edge_ids_list: List[int] = []
    node_from_list: List[int] = []
    node_to_list: List[int] = []
    D_list: List[float] = []
    Kv_max_list: List[float] = []
    opening_list: List[List[float]] = []
    char_list: List[int] = []

    edge_id_to_local: dict[str, int] = {}

    for local_idx, (edge_idx, edge, nf, nt) in enumerate(valve_edges):
        p = edge.params
        edge_ids_list.append(edge_idx)
        node_from_list.append(nf)
        node_to_list.append(nt)
        D_list.append(float(p.get("D_m", 0.015)))
        Kv_max_list.append(float(p.get("Kv_max", 2.5)))
        char_list.append(char_map.get(p.get("characteristic", "linear"), 0))
        edge_id_to_local[edge.id] = local_idx

        opening_raw = p.get("opening", 1.0)
        if isinstance(opening_raw, (int, float)):
            opening_list.append([float(opening_raw)] * N_t)
        else:
            o = [float(v) for v in opening_raw]
            if len(o) < N_t:
                o.extend([o[-1]] * (N_t - len(o)))
            opening_list.append(o[:N_t])

    # Apply events that target valve opening
    for ev in events:
        if ev.target in edge_id_to_local and ev.field == "opening":
            local = edge_id_to_local[ev.target]
            step_idx = min(int(ev.time_s / dt_s), N_t - 1)
            for t in range(step_idx, N_t):
                opening_list[local][t] = ev.value

    return (
        ValveIndices(
            edge_ids=torch.tensor(edge_ids_list, dtype=torch.long),
            node_from=torch.tensor(node_from_list, dtype=torch.long),
            node_to=torch.tensor(node_to_list, dtype=torch.long),
        ),
        ValveBatchParams(
            D=torch.tensor(D_list, dtype=dtype),
            Kv_max=torch.tensor(Kv_max_list, dtype=dtype),
            opening=torch.tensor(opening_list, dtype=dtype),
            characteristic=torch.tensor(char_list, dtype=torch.long),
        ),
    )


def _compile_cold_plates(
    cp_edges: List[Tuple[int, EdgeConfig, int, int]],
    N_t: int,
    events: list,
    dt_s: float,
    dtype: torch.dtype,
) -> Tuple[ColdPlateIndices, ColdPlateBatchParams]:
    """Compile all cold plate edges into batched tensors."""
    edge_ids_list: List[int] = []
    node_from_list: List[int] = []
    node_to_list: List[int] = []
    dp_a_list: List[float] = []
    dp_b_list: List[float] = []
    UA_list: List[float] = []
    C_solid_list: List[float] = []
    V_fluid_list: List[float] = []
    Q_W_list: List[List[float]] = []

    edge_id_to_local: dict[str, int] = {}

    for local_idx, (edge_idx, edge, nf, nt) in enumerate(cp_edges):
        p = edge.params
        edge_ids_list.append(edge_idx)
        node_from_list.append(nf)
        node_to_list.append(nt)
        dp_a_list.append(float(p.get("dp_a", 1e8)))
        dp_b_list.append(float(p.get("dp_b", 1e5)))
        UA_list.append(float(p.get("UA_W_per_K", 150.0)))
        C_solid_list.append(float(p.get("C_solid_J_per_K", 50.0)))
        V_fluid_list.append(float(p.get("V_fluid_m3", 5e-6)))
        edge_id_to_local[edge.id] = local_idx

        Q_raw = p.get("Q_W", 350.0)
        if isinstance(Q_raw, (int, float)):
            Q_W_list.append([float(Q_raw)] * N_t)
        else:
            q = [float(v) for v in Q_raw]
            if len(q) < N_t:
                q.extend([q[-1]] * (N_t - len(q)))
            Q_W_list.append(q[:N_t])

    # Apply events that target Q_W
    for ev in events:
        if ev.target in edge_id_to_local and ev.field == "Q_W":
            local = edge_id_to_local[ev.target]
            step_idx = min(int(ev.time_s / dt_s), N_t - 1)
            for t in range(step_idx, N_t):
                Q_W_list[local][t] = ev.value

    N_cp = len(cp_edges)
    cp_ids = torch.arange(N_cp, dtype=torch.long)

    return (
        ColdPlateIndices(
            edge_ids=torch.tensor(edge_ids_list, dtype=torch.long),
            node_from=torch.tensor(node_from_list, dtype=torch.long),
            node_to=torch.tensor(node_to_list, dtype=torch.long),
            cp_ids=cp_ids,
        ),
        ColdPlateBatchParams(
            dp_a=torch.tensor(dp_a_list, dtype=dtype),
            dp_b=torch.tensor(dp_b_list, dtype=dtype),
            UA=torch.tensor(UA_list, dtype=dtype),
            C_solid=torch.tensor(C_solid_list, dtype=dtype),
            V_fluid=torch.tensor(V_fluid_list, dtype=dtype),
            Q_W=torch.tensor(Q_W_list, dtype=dtype),
        ),
    )


# ── heat exchanger compilation ──────────────────────────────────────────────

def _compile_heat_exchangers(
    hx_edges: List[Tuple[int, EdgeConfig, int, int]],
    dtype: torch.dtype,
) -> Tuple[HeatExchangerIndices, HeatExchangerBatchParams]:
    """Compile all heat exchanger edges into batched tensors."""
    edge_ids_list: List[int] = []
    node_from_list: List[int] = []
    node_to_list: List[int] = []
    UA_list: List[float] = []
    T_secondary_list: List[float] = []
    dp_a_list: List[float] = []
    dp_b_list: List[float] = []

    for local_idx, (edge_idx, edge, nf, nt) in enumerate(hx_edges):
        p = edge.params
        edge_ids_list.append(edge_idx)
        node_from_list.append(nf)
        node_to_list.append(nt)
        UA_list.append(float(p.get("UA_W_per_K", 5000.0)))
        T_secondary_list.append(float(p.get("T_secondary_K", 293.15)))
        dp_a_list.append(float(p.get("dp_a", 5e6)))
        dp_b_list.append(float(p.get("dp_b", 1e4)))

    return (
        HeatExchangerIndices(
            edge_ids=torch.tensor(edge_ids_list, dtype=torch.long),
            node_from=torch.tensor(node_from_list, dtype=torch.long),
            node_to=torch.tensor(node_to_list, dtype=torch.long),
        ),
        HeatExchangerBatchParams(
            UA=torch.tensor(UA_list, dtype=dtype),
            T_secondary=torch.tensor(T_secondary_list, dtype=dtype),
            dp_a=torch.tensor(dp_a_list, dtype=dtype),
            dp_b=torch.tensor(dp_b_list, dtype=dtype),
        ),
    )


# ── hydrostatics ───────────────────────────────────────────────────────────

def _component_elevation_rise(
    node_position: Tensor, edge_from: Tensor, edge_to: Tensor, is_pipe: list[bool], gravity: Tensor,
) -> Tensor:
    """Elevation rise from->to [m] of every NON-pipe edge, from its node coordinates.

    Pipes carry the rise of their waypoint route in pipe_params.dh. A pump,
    valve, cold plate or HX has no route, so its ends are its nodes. Ignoring
    this left a phantom rho*g*dh drive whenever such an edge spans a height
    (report D5: 0.05 m on each NV mock pump, ~489 Pa per loop).
    """
    g_norm = gravity.norm().item()
    if g_norm <= 1e-12:
        return torch.zeros(len(is_pipe), dtype=node_position.dtype)
    height = -(node_position @ (gravity / g_norm))
    rise = height[edge_to] - height[edge_from]
    mask = torch.tensor(is_pipe, dtype=torch.bool)
    return torch.where(mask, torch.zeros_like(rise), rise)


def _check_elevation_closure(
    edge_ids: list[str], edge_from: list[int], edge_to: list[int], rise: list[float],
    node_height: list[float], is_pipe: list[bool],
) -> None:
    """Every loop's elevation rises must sum to zero (a height potential exists).

    A spanning forest assigns node potentials; each remaining edge closes one
    loop, whose mismatch is a pressure source rho*g*mismatch that no physical
    loop has. That only happens when a pipe route's end heights disagree with
    its nodes, so it is reported (naming the loop and those pipes), never
    absorbed.
    """
    N_nodes = len(node_height)
    adjacency: list[list[tuple[int, int, float]]] = [[] for _ in range(N_nodes)]
    for edge, (a, b, d) in enumerate(zip(edge_from, edge_to, rise)):
        adjacency[a].append((b, edge, d))
        adjacency[b].append((a, edge, -d))
    potential: list[float | None] = [None] * N_nodes
    parent: list[tuple[int, int] | None] = [None] * N_nodes  # (parent node, edge)
    for root in range(N_nodes):
        if potential[root] is not None:
            continue
        potential[root] = 0.0
        stack = [root]
        while stack:
            node = stack.pop()
            for other, edge, d in adjacency[node]:
                if potential[other] is None:
                    potential[other] = potential[node] + d
                    parent[other] = (node, edge)
                    stack.append(other)
    tree = {link[1] for link in parent if link is not None}
    scale = max([1.0] + [abs(v) for v in potential if v is not None])
    tolerance = 1e-6 + 1e-9 * scale
    worst = None
    for edge, (a, b, d) in enumerate(zip(edge_from, edge_to, rise)):
        if edge in tree:
            continue
        mismatch = potential[b] - potential[a] - d
        if abs(mismatch) > tolerance and (worst is None or abs(mismatch) > abs(worst[1])):
            worst = (edge, mismatch)
    if worst is None:
        return
    closing, mismatch = worst

    def to_root(node):
        path = []
        while parent[node] is not None:
            node, edge = parent[node]
            path.append(edge)
        return path
    left, right = to_root(edge_from[closing]), to_root(edge_to[closing])
    shared = set(left) & set(right)
    loop = [closing] + [e for e in left if e not in shared] + [e for e in right if e not in shared]
    suspects = []
    for edge in loop:
        nodes_rise = node_height[edge_to[edge]] - node_height[edge_from[edge]]
        if is_pipe[edge] and abs(rise[edge] - nodes_rise) > tolerance:
            suspects.append(f"pipe {edge_ids[edge]!r} route rises {rise[edge]:.6g} m but its nodes rise {nodes_rise:.6g} m")
    names = ", ".join(repr(edge_ids[e]) for e in loop[:12]) + (", ..." if len(loop) > 12 else "")
    raise ValueError(
        f"edge elevations do not close around the loop [{names}]: its rises sum to "
        f"{-mismatch:.6g} m instead of 0, a phantom hydrostatic drive no closed loop has"
        + (". " + "; ".join(suspects) if suspects else "")
        + ". Make each pipe's waypoint end heights match its node positions.")


# ── main entry point ─────────────────────────────────────────────────────────

def compile_graph(
    config: LiquidrunConfig,
    device: str = "cpu",
) -> CompiledGraph:
    """Transform a validated ``.liquidrun`` config into a :class:`CompiledGraph`.

    Parameters
    ----------
    config : LiquidrunConfig
        Already-validated Pydantic model.
    device : str, optional
        Torch device for all tensors (default ``"cpu"``).

    Returns
    -------
    CompiledGraph
        Fully compiled graph ready for the solver.
    """
    dtype = torch.float64

    # ── 1. node_id → index mapping ──────────────────────────────────────

    node_id_to_idx = {n.id: i for i, n in enumerate(config.nodes)}
    N_nodes = len(config.nodes)
    node_ids = [n.id for n in config.nodes]

    # ── 2. per-node arrays ──────────────────────────────────────────────

    node_position = torch.zeros(N_nodes, 3, dtype=dtype)
    bc_mask = torch.zeros(N_nodes, dtype=torch.bool)
    bc_P = torch.zeros(N_nodes, dtype=dtype)
    bc_T = torch.zeros(N_nodes, dtype=dtype)
    bc_T_mask = torch.zeros(N_nodes, dtype=torch.bool)
    junction_mask = torch.zeros(N_nodes, dtype=torch.bool)

    ic = config.initial_conditions

    for i, node in enumerate(config.nodes):
        node_position[i] = torch.tensor(node.position_m, dtype=dtype)
        if node.type == "pressure_bc":
            bc_mask[i] = True
            if node.bc is not None:
                bc_P[i] = node.bc.P_Pa
                if node.bc.T_K is not None:
                    bc_T[i] = node.bc.T_K
                    bc_T_mask[i] = True
                else:
                    bc_T[i] = ic.T_fluid_K
                    bc_T_mask[i] = False
            else:
                bc_P[i] = ic.P_uniform_Pa
                bc_T[i] = ic.T_fluid_K
                bc_T_mask[i] = False
        elif node.type == "junction":
            junction_mask[i] = True
            bc_P[i] = ic.P_uniform_Pa
            bc_T[i] = ic.T_fluid_K

    # ── 2b. junction indexing ──────────────────────────────────────────
    junction_indices = torch.where(junction_mask)[0]
    N_junctions = int(junction_indices.shape[0])
    node_to_junction = torch.full((N_nodes,), -1, dtype=torch.long)
    for j_idx, n_idx in enumerate(junction_indices.tolist()):
        node_to_junction[n_idx] = j_idx

    # ── 3. edge arrays ──────────────────────────────────────────────────

    N_edges = len(config.edges)
    edge_str_ids = [edge.id for edge in config.edges]
    edge_from = torch.zeros(N_edges, dtype=torch.long)
    edge_to = torch.zeros(N_edges, dtype=torch.long)
    edge_type = torch.zeros(N_edges, dtype=torch.long)

    for j, edge in enumerate(config.edges):
        edge_from[j] = node_id_to_idx[edge.from_node]
        edge_to[j] = node_id_to_idx[edge.to_node]
        edge_type[j] = _EDGE_TYPE_TO_INT[edge.type]

    # ── 4. sparse incidence matrix ──────────────────────────────────────

    # B[node, edge] = +1 if node is edge.from, -1 if node is edge.to
    row_indices: List[int] = []
    col_indices: List[int] = []
    values: List[float] = []

    for j in range(N_edges):
        nf = edge_from[j].item()
        nt = edge_to[j].item()
        # +1 for departure
        row_indices.append(nf)
        col_indices.append(j)
        values.append(1.0)
        # -1 for arrival
        row_indices.append(nt)
        col_indices.append(j)
        values.append(-1.0)

    incidence_indices = torch.tensor([row_indices, col_indices], dtype=torch.long)
    incidence_values = torch.tensor(values, dtype=dtype)
    incidence = torch.sparse_coo_tensor(
        incidence_indices, incidence_values, (N_nodes, N_edges),
    )

    # ── 5. classify edges by type ───────────────────────────────────────

    pipe_edges: List[Tuple[int, EdgeConfig, int, int]] = []
    pump_edges: List[Tuple[int, EdgeConfig, int, int]] = []
    cold_plate_edges: List[Tuple[int, EdgeConfig, int, int]] = []
    valve_edges: List[Tuple[int, EdgeConfig, int, int]] = []
    hx_edges: List[Tuple[int, EdgeConfig, int, int]] = []

    for j, edge in enumerate(config.edges):
        nf = node_id_to_idx[edge.from_node]
        nt = node_id_to_idx[edge.to_node]
        entry = (j, edge, nf, nt)
        if edge.type == "pipe":
            pipe_edges.append(entry)
        elif edge.type == "pump":
            pump_edges.append(entry)
        elif edge.type == "cold_plate":
            cold_plate_edges.append(entry)
        elif edge.type == "valve":
            valve_edges.append(entry)
        elif edge.type == "heat_exchanger":
            hx_edges.append(entry)

    N_t = config.time.N_t
    gravity = torch.tensor(config.gravity, dtype=dtype)
    g_mag = gravity.norm().item()

    # ── 6. compile pipes ────────────────────────────────────────────────

    pipe_indices: Optional[PipeIndices] = None
    pipe_params: Optional[PipeBatchParams] = None
    total_cells = 0

    # Initialise cell tensors to empty
    cell_edge_id = torch.zeros(0, dtype=torch.long)
    cell_ds = torch.zeros(0, dtype=dtype)
    cell_volume = torch.zeros(0, dtype=dtype)
    cell_dh = torch.zeros(0, dtype=dtype)
    cell_position = torch.zeros(0, 3, dtype=dtype)
    cell_heat_W = torch.zeros(0, max(N_t, 1), dtype=dtype)

    if pipe_edges:
        (
            pipe_indices,
            pipe_params,
            cell_edge_id,
            cell_ds,
            cell_volume,
            cell_dh,
            cell_position,
            cell_heat_W,
            total_cells,
        ) = _compile_pipes(pipe_edges, gravity, dtype, N_t)

    # ── 6b. hydrostatics of non-pipe edges + loop closure ─────────────
    is_pipe = [edge.type == "pipe" for edge in config.edges]
    component_dh = _component_elevation_rise(node_position, edge_from, edge_to, is_pipe, gravity)
    all_rise = component_dh.clone()
    if pipe_indices is not None:
        all_rise[pipe_indices.edge_ids] = pipe_params.dh
    g_norm = gravity.norm().item()
    node_height = (-(node_position @ (gravity / g_norm)) if g_norm > 1e-12
                   else torch.zeros(N_nodes, dtype=dtype))
    _check_elevation_closure(edge_str_ids, edge_from.tolist(), edge_to.tolist(), all_rise.tolist(),
                             node_height.tolist(), is_pipe)

    # ── 7. compile pumps ────────────────────────────────────────────────

    pump_indices: Optional[PumpIndices] = None
    pump_params: Optional[PumpBatchParams] = None

    if pump_edges:
        pump_indices, pump_params = _compile_pumps(
            pump_edges, N_t, config.events, config.time.dt_s, dtype
        )

    # ── 8. compile valves ─────────────────────────────────────────────

    valve_indices: Optional[ValveIndices] = None
    valve_params: Optional[ValveBatchParams] = None

    if valve_edges:
        valve_indices, valve_params = _compile_valves(
            valve_edges, N_t, config.events, config.time.dt_s, dtype
        )

    # ── 8b. compile cold plates ────────────────────────────────────────

    cp_indices: Optional[ColdPlateIndices] = None
    cp_params: Optional[ColdPlateBatchParams] = None
    N_cp = len(cold_plate_edges)

    if cold_plate_edges:
        cp_indices, cp_params = _compile_cold_plates(
            cold_plate_edges, N_t, config.events, config.time.dt_s, dtype
        )
        N_cp = len(cold_plate_edges)

    # ── 8c. compile heat exchangers ──────────────────────────────────────

    hx_indices: Optional[HeatExchangerIndices] = None
    hx_params: Optional[HeatExchangerBatchParams] = None
    N_hx = len(hx_edges)

    if hx_edges:
        hx_indices, hx_params = _compile_heat_exchangers(hx_edges, dtype)

    # ── 9. junction in/out edge lists ───────────────────────────────────

    node_in_edges: list[list[int]] = [[] for _ in range(N_nodes)]
    node_out_edges: list[list[int]] = [[] for _ in range(N_nodes)]

    for j in range(N_edges):
        nf = edge_from[j].item()
        nt = edge_to[j].item()
        node_out_edges[nf].append(j)
        node_in_edges[nt].append(j)

    # ── 9b. T-junction minor losses ───────────────────────────────────
    # Every leg of a tee (a node of degree >= 3) carries K_TEE_DEFAULT once,
    # on the pipe that forms that leg (TEE_LAW). A leg that leaves the tee
    # through a zero-volume valve (a drawn flow-control fitting is inserted
    # right at the tee) is followed through the valve chain to its pipe; tee
    # law v1 counted only pipes touching the tee node, so such a branch lost
    # its loss while the two run legs kept theirs. A leg that reaches no pipe
    # (a component connected straight to the tee) has none to carry it.
    if pipe_indices is not None and pipe_params is not None:
        pipe_local = {pipe_indices.edge_ids[i].item(): i for i in range(pipe_indices.edge_ids.shape[0])}
        valve_edges = {e for e, edge in enumerate(config.edges) if edge.type == 'valve'}

        def degree(n: int) -> int:
            return len(node_in_edges[n]) + len(node_out_edges[n])

        def other_end(e: int, n: int) -> int:
            f, t = edge_from[e].item(), edge_to[e].item()
            return t if f == n else f

        for tee in range(N_nodes):
            if degree(tee) < 3:
                continue
            for leg in node_in_edges[tee] + node_out_edges[tee]:
                edge, node, seen = leg, tee, set()
                while edge in valve_edges and edge not in seen:
                    seen.add(edge)
                    nxt = other_end(edge, node)
                    if degree(nxt) != 2:
                        break
                    node = nxt
                    edge = next(e for e in node_in_edges[nxt] + node_out_edges[nxt] if e != edge)
                if edge in pipe_local:
                    pipe_params.K_minor[pipe_local[edge]] += K_TEE_DEFAULT

    # ── 10. rack metadata ──────────────────────────────────────────────

    rack_meta: list[RackMeta] = []
    for rack in config.racks:
        server_ids = [s.id for s in rack.servers]
        # All of a server's plates, in series order (report D2: only the
        # first pair was kept, so a 400 W server summarised as 100 W).
        server_edge_ids: list[list[str]] = [s.cold_plate_edge_ids() for s in rack.servers]
        rack_meta.append(RackMeta(
            rack_id=rack.id,
            server_ids=server_ids,
            server_edge_ids=server_edge_ids,
        ))

    # ── 10b. max junction chain depth (BFS) ───────────────────────────
    junction_list = junction_indices.tolist()
    max_junction_depth = 0
    if junction_list:
        junction_set = set(junction_list)
        visited_depth: dict[int, int] = {}
        bfs_queue: deque[int] = deque()
        # Seed: junctions with at least one non-junction incoming neighbor
        for j_idx in junction_list:
            has_non_junction_in = False
            for e in node_in_edges[j_idx]:
                from_n = edge_from[e].item()
                if from_n not in junction_set:
                    has_non_junction_in = True
                    break
            if has_non_junction_in:
                visited_depth[j_idx] = 1
                bfs_queue.append(j_idx)
        # BFS through junction chain
        while bfs_queue:
            n = bfs_queue.popleft()
            d = visited_depth[n]
            if d > max_junction_depth:
                max_junction_depth = d
            for e in node_out_edges[n]:
                to_n = edge_to[e].item()
                if to_n in junction_set and to_n not in visited_depth:
                    visited_depth[to_n] = d + 1
                    bfs_queue.append(to_n)
        # Ensure at least 1 if there are any junctions
        if max_junction_depth == 0 and junction_list:
            max_junction_depth = 1

    # ── 11. pack CompiledGraph ──────────────────────────────────────────

    graph = CompiledGraph(
        N_nodes=N_nodes,
        N_edges=N_edges,
        N_cells=total_cells,
        N_cp=N_cp,
        N_t=N_t,
        node_ids=node_ids,
        node_position=node_position,
        bc_mask=bc_mask,
        bc_P=bc_P,
        bc_T=bc_T,
        bc_T_mask=bc_T_mask,
        junction_mask=junction_mask,
        N_junctions=N_junctions,
        max_junction_depth=max_junction_depth,
        junction_indices=junction_indices,
        node_to_junction=node_to_junction,
        edge_str_ids=edge_str_ids,
        edge_from=edge_from,
        edge_to=edge_to,
        edge_type=edge_type,
        incidence=incidence,
        pipe_indices=pipe_indices,
        cp_indices=cp_indices,
        valve_indices=valve_indices,
        pump_indices=pump_indices,
        hx_indices=hx_indices,
        pipe_params=pipe_params,
        cp_params=cp_params,
        valve_params=valve_params,
        pump_params=pump_params,
        hx_params=hx_params,
        N_hx=N_hx,
        cell_edge_id=cell_edge_id,
        cell_ds=cell_ds,
        cell_volume=cell_volume,
        cell_dh=cell_dh,
        cell_position=cell_position,
        cell_heat_W=cell_heat_W,
        component_dh=component_dh,
        node_in_edges=node_in_edges,
        node_out_edges=node_out_edges,
        dt=config.time.dt_s,
        bdf_order=config.time.bdf_order,
        T_fluid_init=ic.T_fluid_K,
        T_solid_init=ic.T_solid_K,
        P_init=ic.P_uniform_Pa,
        solver_config=config.solver,
        rack_meta=rack_meta,
        fluid_rho=config.fluid.rho_kg_m3,
        fluid_cp=config.fluid.cp_J_kgK,
        fluid_mu=config.fluid.mu_Pa_s,
        g_mag=g_mag,
    )

    return graph.to(device)
