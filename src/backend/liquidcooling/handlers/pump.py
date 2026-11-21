"""Pump batch residual handler (affinity-law head curve)."""

from __future__ import annotations

import torch
from torch import Tensor

from backend.liquidcooling.handlers.base import BaseHandler
from backend.liquidcooling.graphcompiling.compiled_graph import PumpIndices, PumpBatchParams


class PumpBatchHandler(BaseHandler):
    """
    Batch handler for centrifugal pumps using the affinity-law head curve.

    Head model (affinity law on the rated curve H0 - a*Q^2; reverse flow is a
    loss, so a stopped pump cannot drive a loop backwards):
        H = N^2 * H0 - a * Q * |Q|

    where N = rpm_ratio (dimensionless speed ratio relative to rated RPM),
    Q = volumetric flow [m^3/s], H0 = shutoff head at rated speed [m],
    a = head-flow coefficient [m / (m^3/s)^2].

    The pump imposes a pressure rise:
        P_to - P_from = rho * g * H

    Residual (zero when satisfied):
        r_momentum = (P_to - P_from) - rho * g * H
    """

    def __init__(
        self,
        params: PumpBatchParams,
        rho: float,
        g_mag: float,
        dh: Tensor | None = None,
    ) -> None:
        super().__init__()
        self._n_rise = int(params.H0.numel())
        # Register buffers so tensors move with .to(device) / .to(dtype)
        self.register_buffer("H0", params.H0.double())        # [N_pumps]
        self.register_buffer("a_coef", params.a_coef.double())  # [N_pumps]
        self.register_buffer("rpm_ratio", params.rpm_ratio.double())  # [N_pumps] or [N_pumps, N_t]

        # Scalar fluid/physical properties stored as plain attributes
        self.rho: float = float(rho)
        self.g_mag: float = float(g_mag)

        # Rise of the edge from its node heights (graph.component_dh): the
        # DAE and GGA add rho*g*dh to every non-pipe edge (report D5).
        self.register_buffer("dh", (torch.zeros(self._n_rise, dtype=torch.float64) if dh is None
                                    else torch.as_tensor(dh, dtype=torch.float64).reshape(-1)))
    # ------------------------------------------------------------------
    def compute_residuals(
        self,
        state: dict[str, Tensor],
        indices: PumpIndices,
        dt: float,
        bdf_order: int,
    ) -> dict[str, Tensor]:
        """
        Compute momentum residuals for all pumps.

        Args:
            state: global state dict with keys 'P' [N_nodes, N_t] and
                   'm' [N_edges, N_t].
            indices: PumpIndices with node_from, node_to, edge_ids.
            dt: time step (unused — purely algebraic).
            bdf_order: BDF order (unused — purely algebraic).

        Returns:
            {'momentum': r_momentum}  shape [N_pumps, N_t]
        """
        P_from = state["P"][indices.node_from]   # [N_pumps, N_t]
        P_to   = state["P"][indices.node_to]     # [N_pumps, N_t]
        m      = state["m"][indices.edge_ids]    # [N_pumps, N_t]

        Q = m / self.rho                         # volumetric flow [m^3/s]

        # rpm_ratio may be [N_pumps] (static) or [N_pumps, N_t] (time-varying)
        N = self.rpm_ratio
        if N.dim() == 1:
            N = N.unsqueeze(1)                   # [N_pumps, 1] → broadcasts over N_t

        H0 = self.H0.unsqueeze(1)               # [N_pumps, 1]
        a  = self.a_coef.unsqueeze(1)           # [N_pumps, 1]

        H        = N * N * H0 - a * Q * Q.abs()  # pump head [m] (affinity law)
        dp_pump  = self.rho * self.g_mag * H    # pressure rise [Pa]

        # Pump provides pressure: P_to - P_from = dp_pump - rho*g*dh
        r_momentum = (P_to - P_from) - dp_pump + self.rho * self.g_mag * self.dh.unsqueeze(1)

        return {"momentum": r_momentum}


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math

    # Single pump: H0=15 m, a_coef=1e6 m/(m^3/s)^2, rpm_ratio=1.0
    H0       = torch.tensor([15.0],  dtype=torch.float64)
    a_coef   = torch.tensor([1e6],   dtype=torch.float64)
    rpm_ratio = torch.tensor([1.0],  dtype=torch.float64)

    rho   = 997.0   # kg/m^3
    g_mag = 9.81    # m/s^2

    params = PumpBatchParams(H0=H0, a_coef=a_coef, rpm_ratio=rpm_ratio)
    handler = PumpBatchHandler(params, rho=rho, g_mag=g_mag)

    # At Q=0: dp = rho * g * H0
    dp_expected = rho * g_mag * H0.item()   # ≈ 146708 Pa
    print(f"Expected dp at Q=0: {dp_expected:.2f} Pa")

    # Build state tensors: shape [N_nodes, N_t] and [N_edges, N_t]
    # We use 1 time step (N_t=1), node 0 = from, node 1 = to, edge 0 = pump
    N_t = 1
    P = torch.zeros(2, N_t, dtype=torch.float64)
    P[0, :] = 100_000.0                      # P_from
    P[1, :] = 100_000.0 + dp_expected        # P_to  (exact solution)
    m = torch.zeros(1, N_t, dtype=torch.float64)  # Q=0

    state = {"P": P, "m": m}

    indices = PumpIndices(
        edge_ids  = torch.tensor([0], dtype=torch.long),
        node_from = torch.tensor([0], dtype=torch.long),
        node_to   = torch.tensor([1], dtype=torch.long),
    )

    residuals = handler.compute_residuals(state, indices, dt=1.0, bdf_order=1)
    r = residuals["momentum"]
    print(f"Momentum residual: {r.item():.6e} Pa")

    tol = 1e-6 * abs(dp_expected)
    assert abs(r.item()) < tol, (
        f"FAIL: residual {r.item():.3e} exceeds tolerance {tol:.3e}"
    )

    print("PASS: pump handler self-test")
