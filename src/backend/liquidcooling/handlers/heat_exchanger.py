"""CDU heat exchanger batch residual handler — NTU-effectiveness model."""

from __future__ import annotations

import torch
from torch import Tensor

from backend.liquidcooling.solver.bdf import smooth_abs
from backend.liquidcooling.handlers.base import BaseHandler
from backend.liquidcooling.graphcompiling.compiled_graph import HeatExchangerIndices, HeatExchangerBatchParams


class HeatExchangerBatchHandler(BaseHandler):
    """CDU heat exchanger: NTU-effectiveness model.

    Primary side: part of pipe network (hot return -> cooled supply).
    Secondary side: fixed temperature BC (facility water).

    Hydraulic model (algebraic pressure drop):
        dp = dp_a * m * |m| + dp_b * m

    Thermal model (NTU-effectiveness):
        NTU = UA / (|m| * cp + eps)
        eff = 1 - exp(-NTU)
        T_out = T_in - eff * (T_in - T_secondary)

    The HX *cools* primary water: T_out < T_in (heat rejection to
    secondary side), unlike cold plates which heat water (heat absorption
    from chips).

    Parameters (registered as buffers):
        UA            [N_hx]  overall heat transfer coefficient  [W/K]
        T_secondary   [N_hx]  secondary-side temperature         [K]
        dp_a          [N_hx]  quadratic pressure-drop coeff      [Pa*s^2/kg^2]
        dp_b          [N_hx]  linear pressure-drop coeff         [Pa*s/kg]

    Scalar fluid properties:
        rho   fluid density               [kg/m^3]
        cp    fluid specific heat capacity [J/(kg*K)]
    """

    def __init__(
        self,
        params: HeatExchangerBatchParams,
        rho: float,
        cp: float,
        g_mag: float = 9.81,
        dh: Tensor | None = None,
    ) -> None:
        super().__init__()
        self._n_rise = int(params.UA.numel())
        self.g_mag: float = float(g_mag)

        self.register_buffer("UA", params.UA.double())
        self.register_buffer("T_secondary", params.T_secondary.double())
        self.register_buffer("dp_a", params.dp_a.double())
        self.register_buffer("dp_b", params.dp_b.double())

        self.rho: float = float(rho)
        self.cp: float = float(cp)

        # Rise of the edge from its node heights (graph.component_dh): the
        # DAE and GGA add rho*g*dh to every non-pipe edge (report D5).
        self.register_buffer("dh", (torch.zeros(self._n_rise, dtype=torch.float64) if dh is None
                                    else torch.as_tensor(dh, dtype=torch.float64).reshape(-1)))
    # ------------------------------------------------------------------
    # Residual computation
    # ------------------------------------------------------------------

    def compute_residuals(
        self,
        state: dict[str, Tensor],
        indices: HeatExchangerIndices,
        dt: float,
        bdf_order: int,
    ) -> dict[str, Tensor]:
        """Compute momentum and energy residuals for all heat exchangers.

        Args:
            state:     Global state dict with keys:
                         'P'           [N_nodes, N_t]  nodal pressure
                         'm'           [N_edges, N_t]  edge mass flow rates
                       Optionally:
                         'T_all_nodes' [N_nodes, N_t]  full node temperature map
            indices:   HeatExchangerIndices for this HX batch.
            dt:        Time step size [s].
            bdf_order: BDF order (unused for HX -- algebraic model only).

        Returns:
            {
                "momentum": r_hydraulic  [N_hx, N_t]  algebraic residual
                "energy":   r_energy     [N_hx, N_t]  NTU outlet temp residual
            }
        """
        P_from = state["P"][indices.node_from]    # [N_hx, N_t]
        P_to = state["P"][indices.node_to]        # [N_hx, N_t]
        m = state["m"][indices.edge_ids]          # [N_hx, N_t]

        # ---- Momentum residual: pressure drop ----
        dp_a = self.dp_a.unsqueeze(1)   # [N_hx, 1]
        dp_b = self.dp_b.unsqueeze(1)   # [N_hx, 1]

        dp_model = dp_a * m * smooth_abs(m) + dp_b * m
        r_momentum = (P_from - P_to) - dp_model - self.rho * self.g_mag * self.dh.unsqueeze(1)

        # ---- Energy residual: NTU-effectiveness outlet temperature ----
        T_all = state.get("T_all_nodes", None)
        if T_all is not None:
            T_in = T_all[indices.node_from]       # [N_hx, N_t]
        else:
            T_in = state["P"].new_full(
                (indices.edge_ids.shape[0], state["P"].shape[1]),
                308.15,
            )

        UA = self.UA.unsqueeze(1)                          # [N_hx, 1]
        T_sec = self.T_secondary.unsqueeze(1)              # [N_hx, 1]
        m_abs_cp = smooth_abs(m) * self.cp + 1e-10         # [N_hx, N_t]

        NTU = UA / m_abs_cp
        effectiveness = 1.0 - torch.exp(-NTU)

        # HX cools: T_out = T_in - eff * (T_in - T_secondary)
        T_out_ntu = T_in - effectiveness * (T_in - T_sec)

        # Actual outlet temperature from the node temperature map
        if T_all is not None:
            T_out_actual = T_all[indices.node_to]   # [N_hx, N_t]
        else:
            T_out_actual = T_out_ntu  # placeholder when no T_all

        r_energy = T_out_actual - T_out_ntu

        return {
            "momentum": r_momentum,
            "energy": r_energy,
        }

    # ------------------------------------------------------------------
    # Outlet temperature helper (used by model._build_T_all_nodes)
    # ------------------------------------------------------------------

    def outlet_temperature(
        self,
        state: dict[str, Tensor],
        indices: HeatExchangerIndices,
        local_idx: int,
    ) -> Tensor:
        """Compute NTU-effectiveness outlet temperature for HX *local_idx*.

        Returns:
            Tensor [N_t] — predicted outlet temperature.
        """
        edge_id = indices.edge_ids[local_idx]
        m_e = state["m"][edge_id]                # [N_t]
        m_abs = smooth_abs(m_e)

        UA = self.UA[local_idx]
        T_sec = self.T_secondary[local_idx]

        m_abs_cp = m_abs * self.cp + 1e-10
        NTU = UA / m_abs_cp
        eff = 1.0 - torch.exp(-NTU)

        # Get inlet temperature
        T_all = state.get("T_all_nodes", None)
        if T_all is not None:
            from_node = indices.node_from[local_idx]
            T_in = T_all[from_node]              # [N_t]
        else:
            T_in = torch.full_like(m_e, 308.15)

        return T_in - eff * (T_in - T_sec)
