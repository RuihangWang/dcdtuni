"""Cold plate batch residual handler — hydraulic (momentum) + thermal ODE (energy)."""

from __future__ import annotations

import torch
from torch import Tensor

from backend.liquidcooling.solver.bdf import bdf_diff, smooth_abs
from backend.liquidcooling.handlers.base import BaseHandler
from backend.liquidcooling.graphcompiling.compiled_graph import ColdPlateIndices, ColdPlateBatchParams


class ColdPlateBatchHandler(BaseHandler):
    """
    Computes momentum (algebraic) and energy (ODE) residuals for all cold
    plates in one vectorised pass.

    Hydraulic model (algebraic pressure drop):
        dp = dp_a * m * |m| + dp_b * m

    Thermal model (lumped solid ODE):
        C_s * dT_s/dt = Q_IT(t) - |m| * cp * (T_w_out - T_in)

    where the water outlet temperature is computed via NTU-effectiveness:
        NTU     = UA / (|m| * cp + eps)
        eff     = 1 - exp(-NTU)
        T_w_out = T_in + eff * (T_s - T_in)

    with T_in = T_fluid_init by default (overrideable by model.py via
    junction mixing once that layer is implemented).  The NTU-effectiveness
    formulation correctly saturates T_w_out at T_s for large UA/m ratios,
    unlike the crude linear approximation it replaces.

    Parameters (registered as buffers — move with .to(device/dtype)):
        dp_a       [N_cp]        quadratic pressure-drop coefficient  [Pa·s²/kg²]
        dp_b       [N_cp]        linear  pressure-drop coefficient    [Pa·s/kg]
        UA         [N_cp]        overall heat-transfer conductance    [W/K]
        C_solid    [N_cp]        solid thermal mass                   [J/K]
        V_fluid    [N_cp]        fluid volume per cold plate          [m³]
        Q_W        [N_cp, N_t]  chip/IT heat load vs. time           [W]

    Scalar fluid properties (plain float attributes):
        rho        fluid density                [kg/m³]
        cp         fluid specific heat capacity [J/(kg·K)]
        g_mag      gravitational acceleration   [m/s²]

    Scalar initial condition:
        T_solid_init  initial solid/chip temperature [K]
        T_fluid_init  supply fluid temperature        [K]  (used as T_supply)
    """

    def __init__(
        self,
        params: ColdPlateBatchParams,
        rho: float,
        cp: float,
        g_mag: float,
        T_fluid_init: float = 308.15,
        T_solid_init: float = 333.15,
    ) -> None:
        super().__init__()

        # Register all per-cold-plate parameters as buffers so they are
        # tracked by nn.Module and participate in .to(device/dtype).
        self.register_buffer("dp_a", params.dp_a.double())      # [N_cp]
        self.register_buffer("dp_b", params.dp_b.double())      # [N_cp]
        self.register_buffer("UA", params.UA.double())           # [N_cp]
        self.register_buffer("C_solid", params.C_solid.double()) # [N_cp]
        self.register_buffer("V_fluid", params.V_fluid.double()) # [N_cp]
        self.register_buffer("Q_W", params.Q_W.double())         # [N_cp, N_t]

        # Scalar fluid properties — no gradient needed.
        self.rho: float = float(rho)
        self.cp: float = float(cp)
        self.g_mag: float = float(g_mag)

        # Initial conditions — plain floats.
        self.T_fluid_init: float = float(T_fluid_init)
        self.T_solid_init: float = float(T_solid_init)

    # ------------------------------------------------------------------
    # Residual computation
    # ------------------------------------------------------------------

    def compute_residuals(
        self,
        state: dict[str, Tensor],
        indices: ColdPlateIndices,
        dt: float,
        bdf_order: int,
    ) -> dict[str, Tensor]:
        """
        Compute momentum and energy residuals for all cold plates.

        The thermal model uses NTU-effectiveness to compute the water outlet
        temperature: NTU = UA / (|m|*cp), eff = 1 - exp(-NTU),
        T_w_out = T_in + eff * (T_s - T_in).  This correctly saturates the
        outlet temperature at T_s for large UA/m ratios, replacing the former
        crude linear approximation.

        Args:
            state:     Global state dict with keys:
                         'P'   [N_nodes, N_t]  nodal pressure
                         'm'   [N_edges, N_t]  edge mass flow rates
                         'T_s' [N_cp,    N_t]  solid/chip temperatures
                       Optionally:
                         'T_w' — if present, used as supply temperature
                           (not yet consumed; reserved for junction mixing).
            indices:   ColdPlateIndices for this cold-plate batch.
            dt:        Time step size [s].
            bdf_order: BDF order (1, 2, or 3) for the ODE residual.

        Returns:
            {
                "momentum": r_hydraulic  [N_cp, N_t]   algebraic residual
                "energy":   r_solid      [N_cp, N_t-1] ODE residual
                "ic":       r_ic         [N_cp]         initial condition residual
            }
        """
        # ----------------------------------------------------------------
        # Gather state variables for this cold-plate batch
        # ----------------------------------------------------------------
        P_from = state["P"][indices.node_from]   # [N_cp, N_t]
        P_to   = state["P"][indices.node_to]     # [N_cp, N_t]
        m      = state["m"][indices.edge_ids]    # [N_cp, N_t]
        T_s    = state["T_s"][indices.cp_ids]    # [N_cp, N_t]

        # ----------------------------------------------------------------
        # Algebraic residual: pressure drop
        # dp_model = dp_a * m * |m| + dp_b * m
        # r_hydraulic = (P_from - P_to) - dp_model  →  zero when satisfied
        # ----------------------------------------------------------------
        dp_a = self.dp_a.unsqueeze(1)   # [N_cp, 1]  broadcasts over N_t
        dp_b = self.dp_b.unsqueeze(1)   # [N_cp, 1]

        dp_model    = dp_a * m * smooth_abs(m) + dp_b * m   # [N_cp, N_t]
        r_hydraulic = (P_from - P_to) - dp_model             # [N_cp, N_t]

        # ----------------------------------------------------------------
        # ODE residual: solid/chip temperature (lumped thermal mass)
        #
        # Energy balance on the solid:
        #   C_s * dT_s/dt = Q_IT(t) - UA * (T_s - T_w_out)
        #
        # Water outlet temperature via NTU-effectiveness:
        #   NTU     = UA / (|m| * cp + eps)
        #   eff     = 1 - exp(-NTU)
        #   T_w_out = T_in + eff * (T_s - T_in)
        #
        # This is exact for a single-pass heat exchanger and correctly
        # saturates T_w_out → T_s as UA/(m*cp) → ∞.  model.py supplies
        # the actual junction-mixed T_in via T_all_nodes; falls back to
        # T_fluid_init when T_all_nodes is not available (e.g. self-test).
        # ----------------------------------------------------------------
        T_all = state.get("T_all_nodes", None)
        if T_all is not None:
            T_in = T_all[indices.node_from]                  # [N_cp, N_t]
        else:
            T_in = self.T_fluid_init                         # scalar fallback
        UA         = self.UA.unsqueeze(1)                    # [N_cp, 1]
        m_abs_cp   = smooth_abs(m) * self.cp + 1e-10         # [N_cp, N_t]

        NTU = UA / m_abs_cp                                   # [N_cp, N_t]
        effectiveness = 1.0 - torch.exp(-NTU)                 # [N_cp, N_t]
        T_w_out = T_in + effectiveness * (T_s - T_in)         # [N_cp, N_t]

        # Heat removed by coolant (NTU-consistent): Q_cp = |m|·cp·(T_w_out − T_in)
        Q_cp = m_abs_cp * (T_w_out - T_in)                   # [N_cp, N_t]
        rhs = self.Q_W - Q_cp                                 # [N_cp, N_t]

        # BDF time derivative of T_s: shape [N_cp, N_t-1]
        dTs_dt = bdf_diff(T_s, dt, bdf_order)                # [N_cp, N_t-1]
        N_bdf  = dTs_dt.shape[-1]

        C_s    = self.C_solid.unsqueeze(1)                   # [N_cp, 1]
        r_solid = C_s * dTs_dt - rhs[..., -N_bdf:]          # [N_cp, N_t-1]

        # ----------------------------------------------------------------
        # Initial condition residual:  T_s[:, 0] == T_solid_init
        # ----------------------------------------------------------------
        r_ic = T_s[:, 0] - self.T_solid_init                 # [N_cp]

        return {
            "momentum": r_hydraulic,
            "energy":   r_solid,
            "ic":       r_ic,
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    torch.set_default_dtype(torch.float64)

    # -----------------------------------------------------------------------
    # Single cold plate test
    # Parameters:
    #   dp_a = 1e8   Pa·s²/kg²   (quadratic coefficient)
    #   dp_b = 1e5   Pa·s/kg     (linear coefficient)
    #   UA   = 150   W/K
    #   C_solid = 50 J/K
    #   V_fluid = 5e-6 m³
    #   Q_W  = 350 W
    #
    # At m = 0.01 kg/s:
    #   dp = 1e8 * 0.01 * |0.01| + 1e5 * 0.01
    #      = 1e8 * 0.01 * 0.01   + 1000
    #      = 10000                 + 1000
    #      = 11000 Pa
    # -----------------------------------------------------------------------

    N_cp = 1
    N_t  = 3   # a few time steps

    dp_a_val   = 1e8
    dp_b_val   = 1e5
    UA_val     = 150.0
    C_solid_val = 50.0
    V_fluid_val = 5e-6
    Q_W_val    = 350.0
    m_val      = 0.01   # kg/s

    # Expected pressure drop (analytical, using exact |m| since m > 0)
    dp_expected = dp_a_val * m_val * abs(m_val) + dp_b_val * m_val
    print(f"Expected dp = {dp_a_val}*{m_val}*{abs(m_val)} + {dp_b_val}*{m_val}")
    print(f"           = {dp_a_val * m_val * abs(m_val)} + {dp_b_val * m_val}")
    print(f"           = {dp_expected:.1f} Pa")

    # Build params
    params = ColdPlateBatchParams(
        dp_a    = torch.tensor([dp_a_val]),
        dp_b    = torch.tensor([dp_b_val]),
        UA      = torch.tensor([UA_val]),
        C_solid = torch.tensor([C_solid_val]),
        V_fluid = torch.tensor([V_fluid_val]),
        Q_W     = torch.full((N_cp, N_t), Q_W_val),
    )

    # Fluid properties (water-like)
    rho_val = 998.0
    cp_val  = 4182.0
    g_val   = 9.81
    T_fluid_init = 308.15   # 35 °C supply temperature
    T_solid_init = 333.15   # 60 °C initial chip temperature

    handler = ColdPlateBatchHandler(
        params,
        rho=rho_val,
        cp=cp_val,
        g_mag=g_val,
        T_fluid_init=T_fluid_init,
        T_solid_init=T_solid_init,
    )

    # -----------------------------------------------------------------------
    # Build state tensors
    # Two nodes: node 0 = inlet, node 1 = outlet
    # One edge:  edge 0 = the cold plate
    # One cold plate: cp index 0
    # -----------------------------------------------------------------------
    P_state   = torch.zeros(2, N_t, dtype=torch.float64)
    P_state[0, :] = dp_expected   # inlet pressure
    P_state[1, :] = 0.0           # outlet = reference

    m_state   = torch.full((1, N_t), m_val, dtype=torch.float64)

    # Solid temperature: constant at T_solid_init to check IC residual = 0
    T_s_state = torch.full((1, N_t), T_solid_init, dtype=torch.float64)

    state = {"P": P_state, "m": m_state, "T_s": T_s_state}

    # Build indices
    indices = ColdPlateIndices(
        edge_ids  = torch.tensor([0], dtype=torch.long),
        node_from = torch.tensor([0], dtype=torch.long),
        node_to   = torch.tensor([1], dtype=torch.long),
        cp_ids    = torch.tensor([0], dtype=torch.long),
    )

    # Compute residuals
    res = handler.compute_residuals(state, indices, dt=0.1, bdf_order=1)

    # -----------------------------------------------------------------------
    # Momentum residual check
    # With smooth_abs the residual has a small smoothing error (~1e-6 eps term)
    # For m=0.01, smooth_abs(0.01) ≈ sqrt(0.01^2 + 1e-12) ≈ 0.01000000000050
    # The error is < 1e-8 * dp_expected, so we use a generous tolerance.
    # -----------------------------------------------------------------------
    r_mom = res["momentum"]
    max_r_mom = r_mom.abs().max().item()
    print(f"\nMomentum residual (Pa): {r_mom.squeeze().tolist()}")
    print(f"Max |momentum residual| = {max_r_mom:.6e} Pa")
    assert max_r_mom < 1.0, (
        f"FAIL: momentum residual {max_r_mom:.4f} Pa exceeds 1 Pa tolerance"
    )

    # -----------------------------------------------------------------------
    # IC residual check: T_s[:, 0] - T_solid_init should be 0
    # -----------------------------------------------------------------------
    r_ic = res["ic"]
    print(f"\nIC residual (K): {r_ic.tolist()}")
    assert r_ic.abs().max().item() < 1e-10, (
        f"FAIL: IC residual {r_ic.abs().max().item():.3e} K is not zero"
    )

    # -----------------------------------------------------------------------
    # Energy residual: constant T_s → dT_s/dt = 0, so r_solid = -rhs
    # Just verify it has the right shape and is finite.
    # -----------------------------------------------------------------------
    r_energy = res["energy"]
    print(f"\nEnergy residual shape: {r_energy.shape}  (expected [{N_cp}, {N_t - 1}])")
    assert r_energy.shape == (N_cp, N_t - 1), (
        f"FAIL: energy residual shape {r_energy.shape} != ({N_cp}, {N_t - 1})"
    )
    assert torch.isfinite(r_energy).all(), "FAIL: energy residual contains non-finite values"

    print("\nPASS: cold_plate handler self-test")
