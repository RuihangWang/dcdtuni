"""Pipe batch residual handler — hydraulic momentum + thermal energy."""

from __future__ import annotations

import math

import torch
from torch import Tensor

from backend.liquidcooling.solver.bdf import bdf_diff, smooth_abs
from backend.liquidcooling.handlers.base import BaseHandler
from backend.liquidcooling.graphcompiling.compiled_graph import PipeIndices, PipeBatchParams


class PipeBatchHandler(BaseHandler):
    """
    Computes momentum residuals for all pipe segments in one vectorised pass,
    and (optionally) energy + initial-condition residuals for pipe water cells.

    Parameters (registered as buffers so they move with .to(device)):
        D, L, dh, K_minor, A, roughness — from PipeBatchParams, all [N_pipes].
        cell_ds, cell_volume, cell_dh, cell_pipe_id — per-cell buffers [N_cells].
        cell_heat_W — per-cell distributed heat source [N_cells, N_t], WATTS.

    Fluid properties stored as plain float attributes:
        rho, mu, cp, g_mag.
    """

    def __init__(
        self,
        params: PipeBatchParams,
        rho: float,
        mu: float,
        cp: float,
        g_mag: float,
        cell_ds: Tensor | None = None,
        cell_volume: Tensor | None = None,
        cell_dh: Tensor | None = None,
        cell_pipe_id: Tensor | None = None,
        cell_heat_W: Tensor | None = None,
        cell_T_init: Tensor | None = None,
        N_pipes: int = 0,
        T_fluid_init: float = 308.15,
    ) -> None:
        super().__init__()

        # Register all PipeBatchParams fields as buffers.
        # This ensures they participate in .to(device/dtype) and state_dict().
        self.register_buffer("D", params.D)
        self.register_buffer("L", params.L)
        self.register_buffer("dh", params.dh)
        self.register_buffer("K_minor", params.K_minor)
        self.register_buffer("A", params.A)
        self.register_buffer("roughness", params.roughness)

        # Per-cell buffers for the thermal sub-problem (optional).
        if cell_ds is not None:
            self.register_buffer("cell_ds", cell_ds.double())
        else:
            self.register_buffer("cell_ds", torch.empty(0, dtype=torch.float64))
        if cell_volume is not None:
            self.register_buffer("cell_volume", cell_volume.double())
        else:
            self.register_buffer("cell_volume", torch.empty(0, dtype=torch.float64))
        if cell_dh is not None:
            self.register_buffer("cell_dh", cell_dh.double())
        else:
            self.register_buffer("cell_dh", torch.empty(0, dtype=torch.float64))
        if cell_pipe_id is not None:
            self.register_buffer("cell_pipe_id", cell_pipe_id.long())
        else:
            self.register_buffer("cell_pipe_id", torch.empty(0, dtype=torch.long))
        # Distributed heat input per cell, in WATTS, per time step [N_cells, N_t].
        # Zero for every ordinary run; non-zero only for a manufactured-solution
        # (MMS) case, which injects the analytic source term here.
        if cell_heat_W is not None:
            self.register_buffer("cell_heat_W", cell_heat_W.double())
        else:
            self.register_buffer("cell_heat_W", torch.empty(0, dtype=torch.float64))
        # Per-cell initial temperature [N_cells]; overrides the scalar
        # T_fluid_init in the IC residual when present.
        if cell_T_init is not None:
            self.register_buffer("cell_T_init", cell_T_init.double())
        else:
            self.register_buffer("cell_T_init", torch.empty(0, dtype=torch.float64))

        self.N_pipes: int = int(N_pipes)
        self.T_fluid_init: float = float(T_fluid_init)

        # Fluid properties — scalar, no grad needed.
        self.rho = float(rho)
        self.mu = float(mu)
        self.cp = float(cp)
        self.g_mag = float(g_mag)

    # ------------------------------------------------------------------
    # Residual computation
    # ------------------------------------------------------------------

    def compute_residuals(
        self,
        state: dict[str, Tensor],
        indices: PipeIndices,
        dt: float,
        bdf_order: int,
    ) -> dict[str, Tensor]:
        """
        Compute momentum residuals for all pipes.

        Args:
            state:     Global state dict with keys 'P' [N_nodes, N_t] and
                       'm' [N_edges, N_t].
            indices:   PipeIndices for this pipe batch.
            dt:        Time step (unused in steady/quasi-steady momentum eq).
            bdf_order: BDF order (unused here, kept for API consistency).

        Returns:
            {"momentum": r_momentum}  shape [N_pipes, N_t]
        """
        # --- Gather pressures and mass flow rates ---
        # P shape: [N_nodes, N_t]  →  gathered: [N_pipes, N_t]
        P_from = state["P"][indices.node_from]   # [N_pipes, N_t]
        P_to   = state["P"][indices.node_to]     # [N_pipes, N_t]

        # m shape: [N_edges, N_t]  →  gathered: [N_pipes, N_t]
        m = state["m"][indices.edge_ids]         # [N_pipes, N_t]

        # --- Broadcast [N_pipes] params to [N_pipes, N_t] ---
        D        = self.D.unsqueeze(1)        # [N_pipes, 1]
        L        = self.L.unsqueeze(1)
        dh       = self.dh.unsqueeze(1)
        K_minor  = self.K_minor.unsqueeze(1)
        A        = self.A.unsqueeze(1)

        rho = self.rho
        mu  = self.mu

        # --- Velocity ---
        v = m / (rho * A)                    # [N_pipes, N_t]

        # --- Reynolds number ---
        Re = rho * smooth_abs(v) * D / mu    # [N_pipes, N_t]

        # --- Friction factor (laminar–turbulent smooth blend) ---
        f_lam  = 64.0 / (Re + 1e-8)
        # Swamee-Jain (explicit Colebrook-White, uses roughness)
        e_D = self.roughness.unsqueeze(1) / D          # relative roughness [N_pipes, 1]
        # Swamee-Jain's log argument passes 1 near Re=6.97 (smooth pipe), a
        # pole the laminar blend cannot hide; cap it at 0.5 exactly as the DAE
        # friction law does (newton_hydraulic._pipe_dp_friction, report D3).
        log_arg = torch.clamp(e_D / 3.7 + 5.74 / (Re + 1e-8).pow(0.9), max=0.5)
        f_turb = 0.25 / (torch.log10(log_arg) + 1e-10).pow(2)

        sigma  = torch.sigmoid((Re - 2300.0) / 200.0)
        f      = (1.0 - sigma) * f_lam + sigma * f_turb

        # --- Pressure drop terms ---
        # dp_friction: Darcy-Weisbach + minor losses, sign-preserving quadratic
        # m * smooth_abs(m) keeps the sign of m while giving |m|^2 scaling
        dp_friction = (f * L / D + K_minor) * m * smooth_abs(m) / (2.0 * rho * A ** 2)

        # dp_gravity: hydrostatic head (positive dh = pipe going up)
        dp_gravity = rho * self.g_mag * dh   # [N_pipes, 1] broadcasts to [N_pipes, N_t]

        # --- Momentum residual ---
        # (P_from - P_to) must balance friction + gravity
        r_momentum = (P_from - P_to) - dp_friction - dp_gravity

        result: dict[str, Tensor] = {"momentum": r_momentum}

        # --- Thermal residual (only when T_w is present, non-empty, and has
        #     at least 2 time steps so bdf_diff can compute a derivative) ---
        if "T_w" in state and state["T_w"].numel() > 0 and state["T_w"].shape[-1] >= 2:
            T_w = state["T_w"]  # [N_cells, N_t]

            # Bidirectional upwind: smooth blend between forward and backward
            # directions based on flow sign (H4).
            T_all = state.get("T_all_nodes", None)

            T_up_fwd = torch.zeros_like(T_w)
            T_up_bwd = torch.zeros_like(T_w)

            for p in range(self.N_pipes):
                c_start = indices.cell_start[p].item()
                c_end = indices.cell_end[p].item()
                n_c = c_end - c_start
                if n_c <= 0:
                    continue

                T_from = T_all[indices.node_from[p]] if T_all is not None else self.T_fluid_init
                T_to = T_all[indices.node_to[p]] if T_all is not None else self.T_fluid_init

                # Forward: from_node → cell[0] → cell[1] → ...
                T_up_fwd[c_start] = T_from
                if n_c > 1:
                    T_up_fwd[c_start + 1 : c_end] = T_w[c_start : c_end - 1]

                # Backward: to_node → cell[-1] → cell[-2] → ...
                T_up_bwd[c_end - 1] = T_to
                if n_c > 1:
                    T_up_bwd[c_start : c_end - 1] = T_w[c_start + 1 : c_end]

            # Smooth flow-direction blend
            global_edge_per_cell = indices.edge_ids[self.cell_pipe_id]  # [N_cells]
            m_cell = state["m"][global_edge_per_cell]  # [N_cells, N_t]
            w_pos = torch.sigmoid(m_cell * 1000.0)

            T_up = w_pos * T_up_fwd + (1.0 - w_pos) * T_up_bwd

            # Energy balance per cell (advection uses |m|, not signed m):
            #   C_cell * dT/dt = |m_cell| * cp * (T_up - T_w) + Q_cell
            C_cell = self.rho * self.cp * self.cell_volume.unsqueeze(1)  # [N_cells, 1]
            rhs = smooth_abs(m_cell) * self.cp * (T_up - T_w)  # [N_cells, N_t]
            if self.cell_heat_W.numel() > 0:
                rhs = rhs + self.cell_heat_W.to(rhs.dtype)  # [N_cells, N_t], watts

            dTdt = bdf_diff(T_w, dt, bdf_order)  # [N_cells, N_t-1]
            N_bdf = dTdt.shape[-1]
            r_energy = C_cell * dTdt - rhs[..., -N_bdf:]  # [N_cells, N_t-1]

            # Initial condition residual
            if self.cell_T_init.numel() > 0:
                r_ic = T_w[:, 0] - self.cell_T_init.to(T_w.dtype)  # [N_cells]
            else:
                r_ic = T_w[:, 0] - self.T_fluid_init  # [N_cells]

            result["energy"] = r_energy
            result["ic"] = r_ic

        return result


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math

    torch.set_default_dtype(torch.float64)

    # --- Pipe geometry ---
    D_val      = 0.025      # m  (25 mm diameter)
    L_val      = 1.0        # m
    dh_val     = 0.0        # horizontal pipe
    K_val      = 0.0        # no minor losses
    A_val      = math.pi * D_val ** 2 / 4.0

    # --- Fluid properties (water-like) ---
    rho_val = 998.0     # kg/m³
    mu_val  = 1e-3      # Pa·s
    cp_val  = 4182.0    # J/(kg·K)
    g_val   = 9.81      # m/s²

    # --- Analytical laminar flow: Q = dp * pi * D^4 / (128 * mu * L) ---
    # Choose m such that Re < 2300 (laminar regime).
    # Re = rho * v * D / mu = rho * (m / (rho*A)) * D / mu = m * D / (A * mu)
    # Target Re = 500 → m = Re * A * mu / D
    Re_target = 500.0
    m_ana = Re_target * A_val * mu_val / D_val   # kg/s  (ensures laminar)
    Q_ana = m_ana / rho_val

    # Back-compute dp from Hagen-Poiseuille: Q = dp * pi * D^4 / (128 * mu * L)
    # → dp = Q * 128 * mu * L / (pi * D^4)
    dp_target = Q_ana * 128.0 * mu_val * L_val / (math.pi * D_val ** 4)

    print(f"Analytical: dp={dp_target:.4f} Pa,  Q={Q_ana:.6f} m³/s,  m={m_ana:.6f} kg/s")

    # Verify laminar regime
    v_ana = m_ana / (rho_val * A_val)
    Re_ana = rho_val * v_ana * D_val / mu_val
    print(f"  Re = {Re_ana:.2f}  (should be << 2300 for laminar)")

    # --- Build handler ---
    N_pipes = 1
    N_t     = 3   # a few time steps

    params = PipeBatchParams(
        D        = torch.tensor([D_val]),
        L        = torch.tensor([L_val]),
        dh       = torch.tensor([dh_val]),
        K_minor  = torch.tensor([K_val]),
        A        = torch.tensor([A_val]),
        roughness= torch.tensor([0.0]),
    )

    handler = PipeBatchHandler(params, rho=rho_val, mu=mu_val, cp=cp_val, g_mag=g_val)

    # --- Build state ---
    # Two nodes: node 0 = inlet (high P), node 1 = outlet (low P)
    # One edge:  edge 0 = the pipe
    P_state = torch.zeros(2, N_t, dtype=torch.float64)
    P_state[0, :] = float(dp_target)   # inlet pressure
    P_state[1, :] = 0.0                # outlet pressure (reference)

    m_state = torch.zeros(1, N_t, dtype=torch.float64)
    m_state[0, :] = m_ana       # set mass flow to analytical value

    state = {"P": P_state, "m": m_state}

    # --- Build indices ---
    indices = PipeIndices(
        edge_ids   = torch.tensor([0], dtype=torch.long),
        node_from  = torch.tensor([0], dtype=torch.long),
        node_to    = torch.tensor([1], dtype=torch.long),
        cell_start = torch.tensor([0], dtype=torch.long),
        cell_end   = torch.tensor([1], dtype=torch.long),
    )

    # --- Compute residuals ---
    res = handler.compute_residuals(state, indices, dt=0.1, bdf_order=1)
    r = res["momentum"]

    print(f"Momentum residual (Pa): {r.squeeze().tolist()}")
    max_res = r.abs().max().item()
    print(f"Max |residual| = {max_res:.4f} Pa")

    assert max_res < 10.0, f"FAIL: residual {max_res:.4f} Pa >= 10 Pa threshold"
    print("PASS: pipe handler self-test")
