"""
LiquidODILModel — ODIL model for hydraulic + thermal liquid cooling.

Optimises nodal pressures (P), edge mass flow rates (m), pipe water cell
temperatures (T_w), and cold-plate chip temperatures (T_s) by minimising
physics residuals: mass conservation at junctions, component momentum
equations, energy equations, and boundary-condition penalties.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from backend.liquidcooling.solver.bdf import smooth_abs
from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph
from backend.liquidcooling.handlers.pipe import PipeBatchHandler
from backend.liquidcooling.handlers.pump import PumpBatchHandler
from backend.liquidcooling.handlers.valve import ValveBatchHandler
from backend.liquidcooling.handlers.cold_plate import ColdPlateBatchHandler
from backend.liquidcooling.handlers.heat_exchanger import HeatExchangerBatchHandler


class LiquidODILModel(nn.Module):
    """
    ODIL model for the coupled hydraulic + thermal sub-problem.

    State variables (optimised by the outer Adam loop):
        P   — nodal pressures              [N_nodes, N_t]
        m   — edge mass flows              [N_edges, N_t]
        T_w — pipe water cell temperatures [N_cells, N_t]  (empty if N_cells==0)
        T_s — cold-plate chip temperatures [N_cp,    N_t]  (empty if N_cp==0)

    The forward() method returns a dict of scalar loss terms keyed by
    component type and residual name, plus a "total" key.
    """

    # Map handler keys to CompiledGraph index-attribute names.
    # Most follow the pattern "{comp_type}_indices", but cold_plate uses
    # the abbreviated "cp_indices" on CompiledGraph.
    _HANDLER_TO_GRAPH_ATTR = {
        "pipe": "pipe_indices",
        "pump": "pump_indices",
        "valve": "valve_indices",
        "cold_plate": "cp_indices",
        "heat_exchanger": "hx_indices",
    }

    def __init__(self, graph: CompiledGraph, bc_enforcer=None) -> None:
        super().__init__()
        self.graph = graph
        self.bc_enforcer = bc_enforcer

        # State variables to optimise
        state_dict = {
            "P": nn.Parameter(
                torch.full(
                    (graph.N_nodes, graph.N_t),
                    graph.P_init,
                    dtype=torch.float64,
                )
            ),
            "m": nn.Parameter(
                torch.zeros(graph.N_edges, graph.N_t, dtype=torch.float64)
            ),
        }

        # Thermal state: T_w (pipe water cells)
        if graph.N_cells > 0:
            state_dict["T_w"] = nn.Parameter(
                torch.full(
                    (graph.N_cells, graph.N_t),
                    graph.T_fluid_init,
                    dtype=torch.float64,
                )
            )
        else:
            state_dict["T_w"] = nn.Parameter(
                torch.empty(0, graph.N_t, dtype=torch.float64)
            )

        # Thermal state: T_s (cold-plate chip temperatures)
        if graph.N_cp > 0:
            state_dict["T_s"] = nn.Parameter(
                torch.full(
                    (graph.N_cp, graph.N_t),
                    graph.T_solid_init,
                    dtype=torch.float64,
                )
            )
        else:
            state_dict["T_s"] = nn.Parameter(
                torch.empty(0, graph.N_t, dtype=torch.float64)
            )

        # Note: junction temperatures (T_node) are NOT free parameters.
        # They are computed functionally in forward() as the flow-weighted
        # mixed temperature from all incoming edges.  This avoids adding
        # extra optimisation variables that compete with P/m momentum
        # convergence.

        self.state = nn.ParameterDict(state_dict)

        # Create handlers only for component types that exist in the graph
        self.handlers = nn.ModuleDict()
        if graph.pipe_indices is not None:
            self.handlers["pipe"] = PipeBatchHandler(
                graph.pipe_params,
                rho=graph.fluid_rho,
                mu=graph.fluid_mu,
                cp=graph.fluid_cp,
                g_mag=graph.g_mag,
                cell_ds=graph.cell_ds,
                cell_volume=graph.cell_volume,
                cell_dh=graph.cell_dh,
                cell_pipe_id=graph.cell_edge_id,
                cell_heat_W=graph.cell_heat_W,
                cell_T_init=graph.cell_T_init,
                N_pipes=graph.pipe_indices.edge_ids.shape[0],
                T_fluid_init=graph.T_fluid_init,
            )
        if graph.pump_indices is not None:
            self.handlers["pump"] = PumpBatchHandler(
                graph.pump_params,
                rho=graph.fluid_rho,
                g_mag=graph.g_mag,
            )
        if graph.valve_indices is not None:
            self.handlers["valve"] = ValveBatchHandler(
                graph.valve_params,
                rho=graph.fluid_rho,
            )
        if graph.cp_indices is not None:
            self.handlers["cold_plate"] = ColdPlateBatchHandler(
                graph.cp_params,
                rho=graph.fluid_rho,
                cp=graph.fluid_cp,
                g_mag=graph.g_mag,
                T_fluid_init=graph.T_fluid_init,
                T_solid_init=graph.T_solid_init,
            )
        if graph.hx_indices is not None:
            self.handlers["heat_exchanger"] = HeatExchangerBatchHandler(
                graph.hx_params,
                rho=graph.fluid_rho,
                cp=graph.fluid_cp,
            )

        # Register topology buffers (moved to device with the model)
        self.register_buffer("bc_mask", graph.bc_mask)
        self.register_buffer("bc_P", graph.bc_P)
        self.register_buffer("bc_T", graph.bc_T)
        self.register_buffer("bc_T_mask", graph.bc_T_mask)
        self.register_buffer("junction_mask", graph.junction_mask)

        inc = graph.incidence
        if inc.is_sparse:
            inc = inc.to_dense()
        self.register_buffer("inc_dense", inc.to(torch.float64))


    # ------------------------------------------------------------------
    # Device transfer — also move the graph dataclass
    # ------------------------------------------------------------------

    def _move_graph(self, device):
        self.graph = self.graph.to(device)

    def to(self, *args, **kwargs):
        out = super().to(*args, **kwargs)
        device = None
        for a in args:
            if isinstance(a, (torch.device, str)):
                device = a
                break
            if isinstance(a, torch.dtype):
                continue
            if isinstance(a, torch.Tensor):
                device = a.device
                break
        if device is None:
            device = kwargs.get("device")
        if device is not None:
            out._move_graph(device)
        return out

    def cuda(self, device=None):
        out = super().cuda(device)
        cuda_dev = f"cuda:{device}" if device is not None else "cuda"
        out._move_graph(cuda_dev)
        return out

    def cpu(self):
        out = super().cpu()
        out._move_graph("cpu")
        return out

    # ------------------------------------------------------------------
    # Forward pass — compute all loss terms
    # ------------------------------------------------------------------

    def forward(self) -> dict[str, torch.Tensor]:
        w = self.graph.solver_config.weights
        losses: dict[str, torch.Tensor] = {}
        state = {
            "P": self.state["P"],
            "m": self.state["m"],
            "T_w": self.state["T_w"],
            "T_s": self.state["T_s"],
        }

        # Differentiable mass-conservation projection: project the free-edge
        # flows onto the null space of the incidence matrix so that B @ m = 0
        # at all active nodes.  Because this is inside the autograd graph,
        # gradients flow through the projection correctly.
        if (self.bc_enforcer is not None
                and self.bc_enforcer._mass_proj is not None):
            fe = self.bc_enforcer._free_edge_ids
            proj = self.bc_enforcer._mass_proj.to(state["m"].device)
            m_free = state["m"][fe]
            m_proj = m_free - proj @ m_free
            m_new = state["m"].clone()
            m_new[fe] = m_proj
            state["m"] = m_new

        # Build full node temperature map [N_nodes, N_t]
        # BC nodes with T_K get Dirichlet temperature; junction nodes and
        # free-outflow BC nodes get the flow-weighted mixed temperature from
        # all incoming edges.
        # Junction temps are computed iteratively (one pass per depth level)
        # to handle chains of junctions correctly.
        state["T_all_nodes"] = self._build_T_all_nodes(state)

        # 1. Mass conservation at nodes
        #    r_mass = B @ m  (incidence matrix times mass-flow vector)
        #    Enforce at junction nodes AND at BC nodes with ≥ 2 edges
        #    (BC nodes with 2+ edges are part of a closed loop, e.g. pump +
        #    pipe at a CDU, and mass conservation is physically required).
        r_mass = self.inc_dense @ state["m"]             # [N_nodes, N_t]
        # Build mass-conservation mask: junctions + multi-edge BC nodes
        mass_mask = self.junction_mask.clone()
        n_edges_per_node = (self.inc_dense != 0).sum(dim=1)  # [N_nodes]
        mass_mask = mass_mask | (self.bc_mask & (n_edges_per_node >= 2))
        r_mass_active = r_mass[mass_mask]            # [N_active, N_t]
        if r_mass_active.numel() > 0:
            w_mass = w.mass
            losses["mass"] = w_mass * r_mass_active.pow(2).mean()
        else:
            losses["mass"] = torch.tensor(0.0, dtype=torch.float64)

        # 2. Component residuals (momentum for each pipe / pump)
        for comp_type, handler in self.handlers.items():
            attr = self._HANDLER_TO_GRAPH_ATTR.get(
                comp_type, f"{comp_type}_indices"
            )
            idx = getattr(self.graph, attr)
            residuals = handler.compute_residuals(
                state, idx, self.graph.dt, self.graph.bdf_order
            )
            for res_name, res_val in residuals.items():
                loss_key = f"{comp_type}_{res_name}"
                weight = getattr(w, res_name, 1.0)
                losses[loss_key] = weight * res_val.pow(2).mean()

        # 3. Boundary-condition losses (delegated to enforcer)
        if self.bc_enforcer is not None:
            bc_losses = self.bc_enforcer.loss(state, w.bc)
            losses.update(bc_losses)
        else:
            P_bc_err = state["P"][self.bc_mask] - self.bc_P[self.bc_mask].unsqueeze(1)
            losses["bc_P"] = w.bc * P_bc_err.pow(2).mean()

        # Note: BC temperature (C3) and junction mixing (C1) are
        # satisfied by construction — T_all_nodes is computed functionally
        # from the flow field (m, T_w, T_s) rather than being a free
        # parameter.  BC nodes get bc_T directly; junction nodes get the
        # flow-weighted mixed temperature from incoming edges.

        # 4. Total loss (sum of all terms except the "total" entry itself)
        losses["total"] = sum(v for k, v in losses.items() if k != "total")

        return losses

    # ------------------------------------------------------------------
    # Thermal-only mode: freeze hydraulic state, optimise T only
    # ------------------------------------------------------------------

    def set_hydraulic_state(self, P: torch.Tensor, m: torch.Tensor) -> None:
        """Freeze P and m as non-parameter tensors for thermal-only solving."""
        with torch.no_grad():
            self.state["P"] = nn.Parameter(P.clone(), requires_grad=False)
            self.state["m"] = nn.Parameter(m.clone(), requires_grad=False)

    def thermal_parameters(self):
        """Yield only thermal parameters (T_w, T_s) for optimizer."""
        for key in ("T_w", "T_s"):
            p = self.state[key]
            if p.requires_grad and p.numel() > 0:
                yield p

    def thermal_forward(self, T_all_nodes_override=None) -> dict[str, torch.Tensor]:
        """Compute only thermal losses (energy + IC). No mass or momentum.

        If *T_all_nodes_override* is given, use it directly instead of
        calling ``_build_T_all_nodes``.  This is used after the direct
        thermal solver to ensure residuals are evaluated against the same
        node temperatures the solver used internally.
        """
        w = self.graph.solver_config.weights
        losses: dict[str, torch.Tensor] = {}
        state = {
            "P": self.state["P"],
            "m": self.state["m"],
            "T_w": self.state["T_w"],
            "T_s": self.state["T_s"],
        }
        if T_all_nodes_override is not None:
            state["T_all_nodes"] = T_all_nodes_override
        else:
            state["T_all_nodes"] = self._build_T_all_nodes(state)

        for comp_type, handler in self.handlers.items():
            attr = self._HANDLER_TO_GRAPH_ATTR.get(
                comp_type, f"{comp_type}_indices"
            )
            idx = getattr(self.graph, attr)
            residuals = handler.compute_residuals(
                state, idx, self.graph.dt, self.graph.bdf_order
            )
            for res_name, res_val in residuals.items():
                if res_name == "momentum":
                    continue
                loss_key = f"{comp_type}_{res_name}"
                weight = getattr(w, res_name, 1.0)
                losses[loss_key] = weight * res_val.pow(2).mean()

        losses["total"] = sum(v for k, v in losses.items() if k != "total")
        return losses

    # ------------------------------------------------------------------
    # Build T_all_nodes: computed junction temperatures
    # ------------------------------------------------------------------

    @torch.compiler.disable
    def _build_T_all_nodes(
        self, state: dict[str, torch.Tensor]
    ) -> torch.Tensor:
        """Build the [N_nodes, N_t] temperature map.

        BC nodes with ``bc_T_mask=True`` get their prescribed bc_T (Dirichlet).
        BC nodes with ``bc_T_mask=False`` (free-outflow) and junction nodes get
        the flow-weighted mixed temperature from all incoming edges, computed
        iteratively to handle chains of junctions.

        The computation is fully differentiable — gradients flow from
        junction temperatures back to T_w, T_s, and m (the state
        variables that determine outlet temperatures and flow weights).
        """
        g = self.graph
        N_nodes = g.N_nodes
        N_t = g.N_t
        device = state["P"].device
        m = state["m"]

        # Start with T_fluid_init everywhere
        T_all = torch.full(
            (N_nodes, N_t), g.T_fluid_init,
            dtype=torch.float64, device=device,
        )

        # Dirichlet temperature only where bc_T_mask is True
        if self.bc_T_mask.any():
            dirichlet_idx = self.bc_T_mask.nonzero(as_tuple=True)[0]
            if g.bc_T_series is not None:
                T_all[dirichlet_idx] = g.bc_T_series[dirichlet_idx].to(T_all.dtype).to(device)
            else:
                T_all[dirichlet_idx] = self.bc_T[dirichlet_idx].unsqueeze(1).expand(-1, N_t)

        if g.N_junctions == 0:
            return T_all

        # Compute junction temperatures iteratively (batch mode).
        # Each iteration computes all junction temperatures from the same
        # T_all, then updates them all at once.  Two iterations suffice
        # for most practical chain depths.
        junction_list = g.junction_indices.tolist()
        n_iter = max(getattr(g, 'max_junction_depth', 2), 1)
        for _iter in range(n_iter):
            new_junction_temps = []
            for n_idx in junction_list:
                in_edges = g.node_in_edges[n_idx]
                if len(in_edges) == 0:
                    new_junction_temps.append(T_all[n_idx])
                    continue

                weighted_T_sum = torch.zeros(N_t, dtype=torch.float64, device=device)
                weight_sum = torch.zeros(N_t, dtype=torch.float64, device=device)

                for e_idx in in_edges:
                    m_e = m[e_idx]
                    w_e = smooth_abs(m_e)

                    etype = g.edge_type[e_idx].item()
                    if etype == 0:
                        T_out = self._pipe_outlet_temp(state, e_idx)
                    elif etype == 1:
                        T_out = self._cp_outlet_temp_from_T_all(state, e_idx, T_all)
                    elif etype == 4:
                        T_out = self._hx_outlet_temp_from_T_all(state, e_idx, T_all)
                    else:
                        from_node = g.edge_from[e_idx].item()
                        T_out = T_all[from_node]

                    weighted_T_sum = weighted_T_sum + w_e * T_out
                    weight_sum = weight_sum + w_e

                T_mixed = weighted_T_sum / (weight_sum + 1e-10)
                new_junction_temps.append(T_mixed)

            # Batch update all junctions at once
            junction_stack = torch.stack(new_junction_temps, dim=0)
            idx_expanded = g.junction_indices.unsqueeze(1).expand(-1, N_t)
            T_all = T_all.scatter(0, idx_expanded, junction_stack)

        # Free-outflow BC nodes (bc_mask=True, bc_T_mask=False): compute
        # flow-weighted mixed temperature from incoming edges.
        # Flow weights (m) are detached to avoid a gradient coupling between
        # the free-outflow temperature and the mass-flow optimisation variable,
        # which would interfere with mass-conservation convergence.
        # Outlet temperatures (T_out) keep their gradient so that the
        # free-outflow temperature correctly reflects the upstream pipe/cp state.
        free_outflow_mask = self.bc_mask & ~self.bc_T_mask
        free_outflow_indices = torch.where(free_outflow_mask)[0]
        if free_outflow_indices.numel() > 0:
            new_fo_temps = []
            for n_idx in free_outflow_indices.tolist():
                in_edges = g.node_in_edges[n_idx]
                if len(in_edges) == 0:
                    new_fo_temps.append(T_all[n_idx])
                    continue

                weighted_T_sum = torch.zeros(N_t, dtype=torch.float64, device=device)
                weight_sum = torch.zeros(N_t, dtype=torch.float64, device=device)

                for e_idx in in_edges:
                    # Detach m to prevent gradient from T_all[return] → m
                    # coupling back into the mass-conservation residuals.
                    m_e = m[e_idx].detach()
                    w_e = smooth_abs(m_e)

                    etype = g.edge_type[e_idx].item()
                    if etype == 0:
                        T_out = self._pipe_outlet_temp(state, e_idx)
                    elif etype == 1:
                        T_out = self._cp_outlet_temp_from_T_all(state, e_idx, T_all)
                    elif etype == 4:
                        T_out = self._hx_outlet_temp_from_T_all(state, e_idx, T_all)
                    else:
                        from_node = g.edge_from[e_idx].item()
                        T_out = T_all[from_node]

                    weighted_T_sum = weighted_T_sum + w_e * T_out
                    weight_sum = weight_sum + w_e

                T_mixed = weighted_T_sum / (weight_sum + 1e-10)
                new_fo_temps.append(T_mixed)

            fo_stack = torch.stack(new_fo_temps, dim=0)
            fo_idx_expanded = free_outflow_indices.unsqueeze(1).expand(-1, N_t)
            T_all = T_all.scatter(0, fo_idx_expanded, fo_stack)

        return T_all

    def _cp_outlet_temp_from_T_all(
        self, state: dict[str, torch.Tensor], global_edge_idx: int,
        T_all: torch.Tensor,
    ) -> torch.Tensor:
        """Like _cp_outlet_temp but uses the given T_all for inlet temp."""
        g = self.graph
        if g.cp_indices is None:
            return torch.full(
                (g.N_t,), g.T_fluid_init, dtype=torch.float64,
                device=state["P"].device,
            )
        cp_edge_ids = g.cp_indices.edge_ids
        local_mask = (cp_edge_ids == global_edge_idx)
        if not local_mask.any():
            return torch.full(
                (g.N_t,), g.T_fluid_init, dtype=torch.float64,
                device=state["P"].device,
            )
        local_idx = local_mask.nonzero(as_tuple=True)[0][0].item()
        T_s = state["T_s"][g.cp_indices.cp_ids[local_idx]]
        m_abs = state["m"][global_edge_idx].abs()
        UA = g.cp_params.UA[local_idx]
        from_node = g.edge_from[global_edge_idx].item()
        T_in = T_all[from_node]
        m_abs_cp = m_abs * g.fluid_cp + 1e-10
        NTU = UA / m_abs_cp
        eff = 1.0 - torch.exp(-NTU)
        return T_in + eff * (T_s - T_in)

    # ------------------------------------------------------------------
    # Helper: HX outlet temperature (NTU-effectiveness)
    # ------------------------------------------------------------------

    def _hx_outlet_temp_from_T_all(
        self, state: dict[str, torch.Tensor], global_edge_idx: int,
        T_all: torch.Tensor,
    ) -> torch.Tensor:
        """Compute HX outlet temperature using NTU-effectiveness and T_all for inlet."""
        g = self.graph
        if g.hx_indices is None:
            return torch.full(
                (g.N_t,), g.T_fluid_init, dtype=torch.float64,
                device=state["P"].device,
            )
        hx_edge_ids = g.hx_indices.edge_ids
        local_mask = (hx_edge_ids == global_edge_idx)
        if not local_mask.any():
            return torch.full(
                (g.N_t,), g.T_fluid_init, dtype=torch.float64,
                device=state["P"].device,
            )
        local_idx = local_mask.nonzero(as_tuple=True)[0][0].item()
        m_abs = smooth_abs(state["m"][global_edge_idx])
        UA = g.hx_params.UA[local_idx]
        T_sec = g.hx_params.T_secondary[local_idx]
        from_node = g.edge_from[global_edge_idx].item()
        T_in = T_all[from_node]
        m_abs_cp = m_abs * g.fluid_cp + 1e-10
        NTU = UA / m_abs_cp
        eff = 1.0 - torch.exp(-NTU)
        # HX cools: T_out = T_in - eff * (T_in - T_secondary)
        return T_in - eff * (T_in - T_sec)

    # ------------------------------------------------------------------
    # Helper: pipe outlet temperature (last cell T_w)
    # ------------------------------------------------------------------

    def _pipe_outlet_temp(
        self, state: dict[str, torch.Tensor], global_edge_idx: int
    ) -> torch.Tensor:
        """Return the temperature at the last cell of a pipe edge.

        Falls back to T_fluid_init if the pipe has no thermal cells.
        """
        g = self.graph
        if g.pipe_indices is None:
            return torch.full(
                (g.N_t,), g.T_fluid_init, dtype=torch.float64,
                device=state["P"].device,
            )
        # Find the local pipe index for this global edge
        pipe_edge_ids = g.pipe_indices.edge_ids
        local_mask = (pipe_edge_ids == global_edge_idx)
        if not local_mask.any():
            return torch.full(
                (g.N_t,), g.T_fluid_init, dtype=torch.float64,
                device=state["P"].device,
            )
        local_idx = local_mask.nonzero(as_tuple=True)[0][0].item()
        c_start = g.pipe_indices.cell_start[local_idx].item()
        c_end = g.pipe_indices.cell_end[local_idx].item()
        if c_end <= c_start:
            return torch.full(
                (g.N_t,), g.T_fluid_init, dtype=torch.float64,
                device=state["P"].device,
            )
        # Last cell of this pipe
        return state["T_w"][c_end - 1]  # [N_t]

