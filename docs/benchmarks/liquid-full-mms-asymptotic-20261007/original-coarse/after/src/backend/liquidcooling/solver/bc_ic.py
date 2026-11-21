"""
BC/IC management for the liquid cooling solver.

Three classes:
- BoundaryConditionEnforcer: hard-clamp BCs after each solver step + soft loss
- StateInitializer: topology-aware BFS initialization
- BCICMonitor: aggregate BC/IC metrics for SSE reporting
"""
from __future__ import annotations



import torch
import torch.nn as nn
from torch import Tensor

from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph


class BoundaryConditionEnforcer:
    """Hard-clamp boundary conditions and compute soft BC loss."""

    def __init__(self, graph: CompiledGraph) -> None:
        self.bc_mask = graph.bc_mask
        self.bc_P = graph.bc_P
        self.bc_T = graph.bc_T
        self.bc_T_mask = graph.bc_T_mask
        self.N_t = graph.N_t

        dead_end_edge_set: set[int] = set()
        for n_idx in range(graph.N_nodes):
            if not graph.junction_mask[n_idx].item():
                continue
            all_edges = graph.node_in_edges[n_idx] + graph.node_out_edges[n_idx]
            if len(all_edges) == 1:
                dead_end_edge_set.add(all_edges[0])
        self.dead_end_edge_mask = torch.zeros(graph.N_edges, dtype=torch.bool)
        for e_idx in dead_end_edge_set:
            self.dead_end_edge_mask[e_idx] = True

        # Pre-compute mass-conservation projection matrix.
        # After each optimizer step, project the flow vector onto the
        # mass-conservation manifold: B_a @ m = 0 at all active nodes.
        # Active = junctions + multi-edge BC nodes.
        # Dead-end edges are excluded (enforced to zero separately).
        inc = graph.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        inc = inc.to(torch.float64)

        # Build active-node mask (same logic as model.forward)
        mass_mask = graph.junction_mask.clone()
        n_edges_per_node = (inc != 0).sum(dim=1)
        mass_mask = mass_mask | (graph.bc_mask & (n_edges_per_node >= 2))

        # Free edges = all non-dead-end edges
        free_edge_ids = [e for e in range(graph.N_edges)
                         if not self.dead_end_edge_mask[e].item()]
        active_node_ids = mass_mask.nonzero(as_tuple=True)[0].tolist()

        # Only enable projection when the resistance ratio across edges
        # is extreme (>10000), indicating low-R interconnect pipes that
        # diverge under Adam.  For well-conditioned graphs the projection
        # slows convergence unnecessarily.
        enable_proj = False
        if active_node_ids and free_edge_ids and self.dead_end_edge_mask.any().item():
            R_vals = []
            init_cls = StateInitializer(graph)
            for e in free_edge_ids:
                R_vals.append(max(init_cls._estimate_resistance(e), 1e-6))
            if R_vals and max(R_vals) / min(R_vals) > 10000:
                enable_proj = True
        if enable_proj:
            fe = torch.tensor(free_edge_ids, dtype=torch.long)
            an = torch.tensor(active_node_ids, dtype=torch.long)
            B = inc[an][:, fe]  # [N_active, N_free]
            BBT = B @ B.T
            BBT_pinv = torch.linalg.pinv(BBT)
            self._mass_proj = B.T @ BBT_pinv @ B  # [N_free, N_free]
            self._free_edge_ids = fe
        else:
            self._mass_proj = None
            self._free_edge_ids = None
        self._init_flow_sign: torch.Tensor | None = None

    def to(self, device) -> BoundaryConditionEnforcer:
        self.bc_mask = self.bc_mask.to(device)
        self.bc_P = self.bc_P.to(device)
        self.bc_T = self.bc_T.to(device)
        self.bc_T_mask = self.bc_T_mask.to(device)
        self.dead_end_edge_mask = self.dead_end_edge_mask.to(device)
        if self._mass_proj is not None:
            self._mass_proj = self._mass_proj.to(device)
        if self._free_edge_ids is not None:
            self._free_edge_ids = self._free_edge_ids.to(device)
        return self

    def enforce(self, state: dict[str, nn.Parameter]) -> None:
        with torch.no_grad():
            bc_idx = self.bc_mask.nonzero(as_tuple=True)[0]
            if bc_idx.numel() > 0:
                state["P"].data[bc_idx] = self.bc_P[bc_idx].unsqueeze(1).expand(-1, self.N_t)
            if self.dead_end_edge_mask.any():
                dead_idx = self.dead_end_edge_mask.nonzero(as_tuple=True)[0]
                state["m"].data[dead_idx] = 0.0
            pass

    def capture_init_flow_signs(self, state: dict[str, nn.Parameter]) -> None:
        """Record the sign of each free edge's flow for direction preservation."""
        if self._free_edge_ids is not None:
            m_free = state["m"].data[self._free_edge_ids, 0]
            self._init_flow_sign = torch.sign(m_free)

    def loss(self, state: dict[str, nn.Parameter], w_bc: float) -> dict[str, Tensor]:
        losses = {}
        P_bc_err = state["P"][self.bc_mask] - self.bc_P[self.bc_mask].unsqueeze(1)
        losses["bc_P"] = w_bc * P_bc_err.pow(2).mean()
        if self.dead_end_edge_mask.any():
            m_dead = state["m"][self.dead_end_edge_mask]
            losses["bc_dead"] = w_bc * m_dead.pow(2).mean()
        return losses


class StateInitializer:
    """Topology-aware BFS initialization for pressure and mass flow.

    Uses a breadth-first walk from the supply BC node to assign physically
    reasonable initial pressures (monotonically decreasing along flow path)
    and mass flow rates (positive in the supply-to-return direction).
    Temperatures are filled with their configured init values.
    """

    # Estimation constants for initial pressure drop
    _F_EST = 0.02    # Darcy friction factor estimate
    _M_EST = 0.1     # mass flow rate estimate (kg/s)
    _DP_DEFAULT = 1000.0  # fallback dP for unknown edge types (Pa)

    def __init__(self, graph: CompiledGraph) -> None:
        self.graph = graph

    @torch.no_grad()
    def initialize(self, state: dict[str, nn.Parameter]) -> None:
        """Fill state tensors with physically reasonable initial values."""
        from collections import deque

        g = self.graph

        # --- 1. Temperatures ---
        if g.N_cells > 0:
            state["T_w"].data.fill_(g.T_fluid_init)
            if g.cell_T_init is not None:
                state["T_w"].data[:, 0] = g.cell_T_init.to(state["T_w"].dtype).to(
                    state["T_w"].device
                )
        if g.N_cp > 0:
            state["T_s"].data.fill_(g.T_solid_init)

        # --- 2. Identify supply and return BC nodes ---
        # Supply has highest BC pressure, return has lowest.
        # Edge-direction heuristics fail when pumps create loops
        # (a BC node can have both in-edges and out-edges).
        bc_nodes = [i for i in range(g.N_nodes) if g.bc_mask[i]]
        if len(bc_nodes) < 2:
            state["P"].data.fill_(g.P_init)
            for i in range(g.N_nodes):
                if g.bc_mask[i]:
                    state["P"].data[i, :] = g.bc_P[i]
            state["m"].data.fill_(0.01)
            return
        bc_nodes.sort(key=lambda i: g.bc_P[i].item(), reverse=True)
        supply_node = bc_nodes[0]
        return_node = bc_nodes[-1]

        P_supply = g.bc_P[supply_node].item()
        P_return = g.bc_P[return_node].item()

        # --- 3. Undirected BFS from supply and return ---
        # Pipe edge directions may not follow physical flow direction
        # (e.g. a pipe from tee→CDU_supply carries flow in the reverse
        # direction with negative m).  Undirected BFS reaches all nodes
        # regardless of edge orientation.  Pump edges are excluded to
        # prevent the pump shortcut from collapsing all distances to 1.
        pump_edge_set = set()
        for e_idx in range(g.N_edges):
            if g.edge_type[e_idx].item() == 3:
                pump_edge_set.add(e_idx)

        def _bfs_undirected(start):
            dist = [-1] * g.N_nodes
            dist[start] = 0
            queue = deque([start])
            while queue:
                n = queue.popleft()
                for e in g.node_out_edges[n]:
                    if e in pump_edge_set:
                        continue
                    t = g.edge_to[e].item()
                    if dist[t] < 0:
                        dist[t] = dist[n] + 1
                        queue.append(t)
                for e in g.node_in_edges[n]:
                    if e in pump_edge_set:
                        continue
                    f = g.edge_from[e].item()
                    if dist[f] < 0:
                        dist[f] = dist[n] + 1
                        queue.append(f)
            return dist

        d_supply = _bfs_undirected(supply_node)
        d_return = _bfs_undirected(return_node)

        # --- 4. Pressure initialization ---
        # Use Kirchhoff linear-circuit solve when edge resistances span a
        # wide range (>1000:1), so the pressure drop is correctly allocated
        # to high-resistance components.  Otherwise BFS hop-fraction
        # interpolation works well and converges faster.
        R_vals = []
        for e_idx in range(g.N_edges):
            if g.edge_type[e_idx].item() == 3:
                continue
            R_vals.append(max(self._estimate_resistance(e_idx), 1e-6))
        use_kirchhoff = (len(R_vals) >= 2
                         and max(R_vals) / min(R_vals) > 1000)
        if use_kirchhoff:
            P_est = self._kirchhoff_pressure_init(
                supply_node, return_node, P_supply, P_return, pump_edge_set,
            )
        else:
            P_est = torch.full((g.N_nodes,), g.P_init, dtype=torch.float64)
            for i in range(g.N_nodes):
                ds = d_supply[i]
                dr = d_return[i]
                if ds >= 0 and dr >= 0 and (ds + dr) > 0:
                    frac = ds / (ds + dr)
                    P_est[i] = P_supply + frac * (P_return - P_supply)
                elif ds >= 0:
                    P_est[i] = P_supply
                elif dr >= 0:
                    P_est[i] = P_return
            for i in range(g.N_nodes):
                if g.bc_mask[i]:
                    P_est[i] = g.bc_P[i].item()

        state["P"].data[:] = P_est.unsqueeze(1).expand(-1, g.N_t)

        # --- 5. Flow: derive from pressure gradient + mass propagation ---
        # First pass: edges with significant dP get flow from sqrt(dP/R).
        # Edges with near-zero dP (low-resistance interconnects) are deferred.
        pump_edges: list[int] = []
        deferred_edges: list[int] = []
        resolved = [False] * g.N_edges
        dead_edges: set[int] = set()
        for n_idx in range(g.N_nodes):
            if not g.junction_mask[n_idx]:
                continue
            all_e = g.node_in_edges[n_idx] + g.node_out_edges[n_idx]
            if len(all_e) == 1:
                dead_edges.add(all_e[0])

        for e_idx in range(g.N_edges):
            if e_idx in dead_edges:
                state["m"].data[e_idx, :] = 0.0
                resolved[e_idx] = True
                continue
            etype = g.edge_type[e_idx].item()
            if etype == 3:
                pump_edges.append(e_idx)
                continue
            fn = g.edge_from[e_idx].item()
            tn = g.edge_to[e_idx].item()
            dP = P_est[fn] - P_est[tn]
            R = self._estimate_resistance(e_idx)
            if R > 0 and abs(dP.item()) > 1.0:
                m_val = (abs(dP) / R).sqrt()
                state["m"].data[e_idx, :] = torch.sign(dP) * m_val
                resolved[e_idx] = True
            else:
                deferred_edges.append(e_idx)

        # Second pass: propagate flows via mass conservation.
        # Walk from nodes with all-but-one edge resolved, and derive
        # the remaining edge's flow.  Repeat until no progress.
        changed = True
        while changed and deferred_edges:
            changed = False
            still_deferred = []
            for e_idx in deferred_edges:
                fn = g.edge_from[e_idx].item()
                tn = g.edge_to[e_idx].item()
                solved_at = self._solve_from_mass_conservation(
                    e_idx, fn, tn, state, g, resolved, pump_edges,
                )
                if solved_at is not None:
                    state["m"].data[e_idx, :] = solved_at
                    resolved[e_idx] = True
                    changed = True
                else:
                    still_deferred.append(e_idx)
            deferred_edges = still_deferred

        # Any remaining deferred edges: use small default
        for e_idx in deferred_edges:
            state["m"].data[e_idx, :] = 0.01
            resolved[e_idx] = True

        # Third pass: pump flow from mass conservation at its to-node.
        for e_idx in pump_edges:
            tn = g.edge_to[e_idx].item()
            net_out = 0.0
            for e in g.node_out_edges[tn]:
                if e != e_idx:
                    net_out += state["m"].data[e, 0].item()
            for e in g.node_in_edges[tn]:
                if e != e_idx:
                    net_out -= state["m"].data[e, 0].item()
            state["m"].data[e_idx, :] = max(abs(net_out), 0.01)

    @staticmethod
    def _solve_from_mass_conservation(
        target_edge: int,
        fn: int,
        tn: int,
        state: dict[str, torch.nn.Parameter],
        g,
        resolved: list[bool],
        pump_edges: list[int],
    ) -> float | None:
        """Try to derive flow on *target_edge* from mass conservation.

        For each endpoint, check if all OTHER non-pump edges at that node
        are resolved.  If so, the target edge flow is uniquely determined
        by mass conservation: sum of signed flows at the node must be zero.
        """
        for node in (fn, tn):
            all_edges = g.node_in_edges[node] + g.node_out_edges[node]
            non_pump = [e for e in all_edges
                        if e != target_edge and e not in pump_edges]
            if all(resolved[e] for e in non_pump):
                net_signed = 0.0
                for e in non_pump:
                    m_val = state["m"].data[e, 0].item()
                    if e in g.node_in_edges[node]:
                        net_signed += m_val
                    else:
                        net_signed -= m_val
                # target_edge must balance: if it's an in-edge at node,
                # net_signed + m_target = 0 → m_target = -net_signed
                # if it's an out-edge, net_signed - m_target = 0 → m_target = net_signed
                if target_edge in g.node_in_edges[node]:
                    return -net_signed
                else:
                    return net_signed
        return None

    def _kirchhoff_pressure_init(
        self,
        supply_node: int,
        return_node: int,
        P_supply: float,
        P_return: float,
        pump_edge_set: set[int],
    ) -> torch.Tensor:
        """Solve a linear circuit analogy for initial node pressures.

        Each non-pump edge is treated as a resistor with conductance
        g_e = 1 / R_e.  Kirchhoff's current law at each junction node
        gives a sparse linear system whose solution distributes the BC
        pressure drop in proportion to component resistance.
        """
        g = self.graph
        N = g.N_nodes
        G = torch.zeros((N, N), dtype=torch.float64)
        rhs = torch.zeros(N, dtype=torch.float64)

        for e_idx in range(g.N_edges):
            if e_idx in pump_edge_set:
                continue
            fn = g.edge_from[e_idx].item()
            tn = g.edge_to[e_idx].item()
            R = self._estimate_resistance(e_idx)
            ge = 1.0 / max(R, 1e-6)
            G[fn, fn] += ge
            G[tn, tn] += ge
            G[fn, tn] -= ge
            G[tn, fn] -= ge

            # Gravity handled post-hoc via momentum weight scaling;
            # Kirchhoff solve uses friction-only conductance.

        # Pin BC node pressures (Dirichlet rows)
        for i in range(N):
            if g.bc_mask[i]:
                G[i, :] = 0.0
                G[i, i] = 1.0
                rhs[i] = g.bc_P[i].item()

        # Fallback for isolated nodes: set diagonal to 1, rhs to P_init
        for i in range(N):
            if G[i, i].abs().item() < 1e-12:
                G[i, i] = 1.0
                rhs[i] = g.P_init

        try:
            P_est = torch.linalg.solve(G, rhs)
        except Exception:
            P_est = torch.full((N,), g.P_init, dtype=torch.float64)
            for i in range(N):
                if g.bc_mask[i]:
                    P_est[i] = g.bc_P[i].item()

        # Clamp to physical range
        P_est = P_est.clamp(min=min(P_supply, P_return) * 0.5,
                            max=max(P_supply, P_return) * 1.5)
        for i in range(N):
            if g.bc_mask[i]:
                P_est[i] = g.bc_P[i].item()

        return P_est

    def _estimate_gravity(self, e_idx: int) -> float:
        """Return rho * g * dh for pipe edge *e_idx*, or 0 for other types."""
        g = self.graph
        if g.edge_type[e_idx].item() != 0 or g.pipe_indices is None:
            return 0.0
        mask = (g.pipe_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
        if mask.numel() == 0:
            return 0.0
        idx = mask[0].item()
        dh = g.pipe_params.dh[idx].item()
        return g.fluid_rho * g.g_mag * dh

    def _estimate_dP(self, e_idx: int) -> float:
        """Rough pressure drop estimate for edge *e_idx*."""
        g = self.graph
        etype = g.edge_type[e_idx].item()

        if etype == 0 and g.pipe_indices is not None:
            # Pipe: dP = (f*L/D + K_minor) * m^2 / (2 * rho * A^2)
            mask = (g.pipe_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
            if mask.numel() > 0:
                idx = mask[0].item()
                D = g.pipe_params.D[idx].item()
                L = g.pipe_params.L[idx].item()
                A = g.pipe_params.A[idx].item()
                K = g.pipe_params.K_minor[idx].item()
                rho = g.fluid_rho
                denom = 2.0 * rho * A * A
                if denom > 0 and D > 0:
                    return (self._F_EST * (L / D) + K) * (self._M_EST ** 2) / denom
            return self._DP_DEFAULT

        if etype == 1 and g.cp_indices is not None:
            # Cold plate: dP = dp_a * m^2
            mask = (g.cp_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
            if mask.numel() > 0:
                idx = mask[0].item()
                dp_a = g.cp_params.dp_a[idx].item()
                return dp_a * (self._M_EST ** 2)
            return self._DP_DEFAULT

        if etype == 3 and g.pump_indices is not None:
            # Pump: negative dP (adds head)
            mask = (g.pump_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
            if mask.numel() > 0:
                idx = mask[0].item()
                H0 = g.pump_params.H0[idx].item()
                return -(g.fluid_rho * g.g_mag * H0)
            return self._DP_DEFAULT

        if etype == 4 and g.hx_indices is not None:
            # Heat exchanger: dP = dp_a * m^2
            mask = (g.hx_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
            if mask.numel() > 0:
                idx = mask[0].item()
                dp_a = g.hx_params.dp_a[idx].item()
                return dp_a * (self._M_EST ** 2)
            return self._DP_DEFAULT

        return self._DP_DEFAULT

    def _estimate_resistance(self, e_idx: int) -> float:
        """Estimate resistance R such that dP ~ R * m^2, for deriving m from dP."""
        g = self.graph
        etype = g.edge_type[e_idx].item()

        if etype == 0 and g.pipe_indices is not None:
            mask = (g.pipe_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
            if mask.numel() > 0:
                idx = mask[0].item()
                D = g.pipe_params.D[idx].item()
                L = g.pipe_params.L[idx].item()
                A = g.pipe_params.A[idx].item()
                K = g.pipe_params.K_minor[idx].item()
                rho = g.fluid_rho
                denom = 2.0 * rho * A * A
                if denom > 0 and D > 0:
                    return (self._F_EST * (L / D) + K) / denom
            return self._DP_DEFAULT

        if etype == 1 and g.cp_indices is not None:
            mask = (g.cp_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
            if mask.numel() > 0:
                idx = mask[0].item()
                return g.cp_params.dp_a[idx].item()
            return self._DP_DEFAULT

        if etype == 4 and g.hx_indices is not None:
            mask = (g.hx_indices.edge_ids == e_idx).nonzero(as_tuple=True)[0]
            if mask.numel() > 0:
                idx = mask[0].item()
                return g.hx_params.dp_a[idx].item()
            return self._DP_DEFAULT

        # For pumps and valves, use default resistance
        return self._DP_DEFAULT


class BCICMonitor:
    """Compute aggregate BC/IC metrics for SSE reporting."""

    def __init__(self, graph: CompiledGraph) -> None:
        self.T_fluid_init = graph.T_fluid_init
        self.cell_T_init = graph.cell_T_init
        self.T_solid_init = graph.T_solid_init

    def compute(self, state: dict[str, torch.Tensor], bc_enforcer: BoundaryConditionEnforcer) -> dict[str, float]:
        with torch.no_grad():
            bc_losses = bc_enforcer.loss(state, w_bc=1.0)
            total_bc = sum(v.item() for v in bc_losses.values())
            total_ic = 0.0
            T_w = state.get("T_w")
            if T_w is not None and T_w.numel() > 0:
                ref = (
                    self.cell_T_init.to(T_w.dtype).to(T_w.device)
                    if self.cell_T_init is not None
                    else self.T_fluid_init
                )
                total_ic += (T_w[:, 0] - ref).pow(2).mean().item()
            T_s = state.get("T_s")
            if T_s is not None and T_s.numel() > 0:
                total_ic += (T_s[:, 0] - self.T_solid_init).pow(2).mean().item()
        return {"total_bc": total_bc, "total_ic": total_ic}
