"""Valve batch residual handler (ISA control valve equation)."""

from __future__ import annotations

import torch
from torch import Tensor

from backend.liquidcooling.handlers.base import BaseHandler
from backend.liquidcooling.graphcompiling.compiled_graph import ValveIndices, ValveBatchParams


def effective_valve_kv(Kv_max: Tensor, opening: Tensor, characteristic: Tensor) -> Tensor:
    """ISA Kv in m3/h/sqrt(bar); tensors must have broadcast-compatible shapes.

    Positive openings use the existing characteristic (rangeability 50 for equal
    percentage). Zero/negative opening is ideal shutoff, with no assumed leakage.
    Its backward convention is zero for every characteristic, including the
    quick-opening endpoint. This finite numerical subgradient is not the
    right-hand derivative of sqrt(alpha), which is unbounded at zero.
    """
    alpha = opening.clamp(min=0.0)
    # torch.where evaluates both branches: keep sqrt's inactive argument away
    # from zero to avoid 0 * inf -> NaN in backward, then apply exact shutoff.
    # Equality (rather than > 0) deliberately preserves NaN for diagnostics.
    sqrt_alpha = torch.where(alpha == 0.0, torch.ones_like(alpha), alpha).sqrt()
    kv = torch.where(characteristic == 1, Kv_max * 50.0 ** (alpha - 1.0),
                     torch.where(characteristic == 2, Kv_max * sqrt_alpha, Kv_max * alpha))
    return torch.where(opening <= 0.0, torch.zeros_like(kv), kv)


class ValveBatchHandler(BaseHandler):
    """
    Batch handler for control valves using the ISA valve equation.

    Flow model (ISA):
        Q[m³/h] = Kv * sqrt(dP[bar] / (rho / 1000))
        m[kg/s] = sign(dP) * Q * rho / 3600

    where Kv is the effective flow coefficient [m^3/h / sqrt(bar)] computed
    from Kv_max and opening according to the selected characteristic curve:

        0 = linear:           Kv = Kv_max * alpha
        1 = equal_percentage: Kv = Kv_max * 50^(alpha - 1)
        2 = quick_opening:    Kv = Kv_max * sqrt(alpha)

    Residual (zero when satisfied):
        r_momentum = m - m_model

    Parameters (registered as buffers so they move with .to(device)):
        D             [N_valves]         nominal diameter (informational)
        Kv_max        [N_valves]         maximum flow coefficient
        opening       [N_valves, N_t]    normalised opening in [0, 1]; zero is ideal shutoff
        characteristic [N_valves] int    curve type (0/1/2)

    Fluid properties stored as plain float attribute:
        rho  [kg/m^3]
    """

    def __init__(
        self,
        params: ValveBatchParams,
        rho: float,
        g_mag: float = 9.81,
        dh: Tensor | None = None,
    ) -> None:
        super().__init__()
        self._n_rise = int(params.Kv_max.numel())
        self.g_mag: float = float(g_mag)

        self.register_buffer("D", params.D.double())                    # [N_valves]
        self.register_buffer("Kv_max", params.Kv_max.double())          # [N_valves]
        self.register_buffer("opening", params.opening.double())        # [N_valves, N_t]
        self.register_buffer("characteristic", params.characteristic)   # [N_valves] int

        # Scalar fluid property — no gradient needed
        self.rho: float = float(rho)

        # Rise of the edge from its node heights (graph.component_dh): the
        # DAE and GGA add rho*g*dh to every non-pipe edge (report D5).
        self.register_buffer("dh", (torch.zeros(self._n_rise, dtype=torch.float64) if dh is None
                                    else torch.as_tensor(dh, dtype=torch.float64).reshape(-1)))
    # ------------------------------------------------------------------
    def compute_residuals(
        self,
        state: dict[str, Tensor],
        indices: ValveIndices,
        dt: float,
        bdf_order: int,
    ) -> dict[str, Tensor]:
        """
        Compute momentum residuals for all valves.

        Args:
            state:     Global state dict with keys 'P' [N_nodes, N_t] and
                       'm' [N_edges, N_t].
            indices:   ValveIndices with node_from, node_to, edge_ids.
            dt:        Time step (unused — purely algebraic).
            bdf_order: BDF order (unused — purely algebraic).

        Returns:
            {'momentum': r_momentum}  shape [N_valves, N_t]
        """
        P_from = state["P"][indices.node_from]   # [N_valves, N_t]
        P_to   = state["P"][indices.node_to]     # [N_valves, N_t]
        m      = state["m"][indices.edge_ids]    # [N_valves, N_t]

        opening = self.opening if self.opening.ndim == 2 else self.opening[:, None]
        Kv = effective_valve_kv(self.Kv_max[:, None], opening,
                                self.characteristic[:, None])

        # --- ISA valve equation ---
        # Q[m³/h] = Kv * sqrt(dP[bar] / (rho/1000))
        # m[kg/s] = sign(dP) * Q * rho / 3600
        dP      = P_from - P_to - self.rho * self.g_mag * self.dh.unsqueeze(1)
        dP_bar  = dP.abs() / 1e5                          # Pa → bar
        rho_rel = self.rho / 1000.0                        # relative to water
        Q_m3h   = Kv * (dP_bar / rho_rel + 1e-10).sqrt()  # m³/h
        m_model = torch.sign(dP) * Q_m3h * self.rho / 3600.0  # kg/s

        r_momentum = m - m_model
        return {"momentum": r_momentum}


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    torch.set_default_dtype(torch.float64)

    # --- Single linear valve: Kv_max=2.5, fully open, characteristic=0 ---
    rho_val = 997.0   # kg/m^3 (water at ~25 °C)

    N_t = 3   # a few time steps

    params = ValveBatchParams(
        D             = torch.tensor([0.05], dtype=torch.float64),
        Kv_max        = torch.tensor([2.5],  dtype=torch.float64),
        opening       = torch.ones(1, N_t,  dtype=torch.float64),   # fully open
        characteristic= torch.tensor([0],    dtype=torch.long),      # linear
    )

    handler = ValveBatchHandler(params, rho=rho_val)

    # --- At full open with dP = 10 000 Pa ---
    # Kv = Kv_max * 1.0 = 2.5
    # dP_bar = 10000 / 1e5 = 0.1 bar
    # Q = 2.5 * sqrt(0.1 / 0.997) ≈ 0.7913 m³/h
    # m = Q * 997 / 3600 ≈ 0.2192 kg/s
    import math
    dP_val  = 10_000.0
    dP_bar  = dP_val / 1e5
    rho_rel = rho_val / 1000.0
    Q_m3h   = 2.5 * math.sqrt(dP_bar / rho_rel)
    m_expected = Q_m3h * rho_val / 3600.0
    print(f"Expected m_model ≈ {m_expected:.4f} kg/s")

    # Build state: 2 nodes, 1 edge, N_t time steps
    P_state = torch.zeros(2, N_t, dtype=torch.float64)
    P_state[0, :] = dP_val   # inlet
    P_state[1, :] = 0.0      # outlet

    m_state = torch.full((1, N_t), m_expected, dtype=torch.float64)

    state = {"P": P_state, "m": m_state}

    indices = ValveIndices(
        edge_ids  = torch.tensor([0], dtype=torch.long),
        node_from = torch.tensor([0], dtype=torch.long),
        node_to   = torch.tensor([1], dtype=torch.long),
    )

    res = handler.compute_residuals(state, indices, dt=1.0, bdf_order=1)
    r   = res["momentum"]

    print(f"Momentum residual (kg/s): {r.squeeze().tolist()}")
    max_res = r.abs().max().item()
    print(f"Max |residual| = {max_res:.6e} kg/s")

    tol = 1e-6 * abs(m_expected)
    assert max_res < tol, (
        f"FAIL: residual {max_res:.3e} kg/s exceeds tolerance {tol:.3e}"
    )

    print("PASS: valve handler self-test")
