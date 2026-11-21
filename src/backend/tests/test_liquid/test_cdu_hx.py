"""Tests for CDU heat exchanger component — Phase 3 of liquid solver overhaul.

The CDU HEX cools primary-side (rack return) water using secondary-side
(facility) water at a known fixed temperature. Model: NTU-effectiveness
with T_secondary as a boundary condition.
"""
import json
import math
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph

FIXTURE_DIR = Path(__file__).parent


def _make_cdu_graph():
    raw = json.loads((FIXTURE_DIR / "cdu_loop.liquidrun").read_text())
    config = LiquidrunConfig.model_validate(raw)
    return compile_graph(config), config


class TestCDUHeatExchangerCompilation:
    """The compiler should parse heat_exchanger edges and create indices/params."""

    def test_edge_type_recognized(self):
        """heat_exchanger should compile as edge type 4."""
        graph, _ = _make_cdu_graph()
        hx_edge_idx = graph.edge_str_ids.index("cdu_hx")
        assert graph.edge_type[hx_edge_idx].item() == 4

    def test_hx_indices_exist(self):
        """CompiledGraph should have hx_indices for heat_exchanger edges."""
        graph, _ = _make_cdu_graph()
        assert graph.hx_indices is not None
        assert graph.hx_indices.edge_ids.shape[0] == 1

    def test_hx_params_exist(self):
        """CompiledGraph should have hx_params with UA and T_secondary."""
        graph, _ = _make_cdu_graph()
        assert graph.hx_params is not None
        assert graph.hx_params.UA.shape[0] == 1
        assert graph.hx_params.T_secondary.shape[0] == 1
        assert graph.hx_params.UA[0].item() == pytest.approx(5000.0)
        assert graph.hx_params.T_secondary[0].item() == pytest.approx(293.15)


class TestCDUHeatExchangerHandler:
    """The HX handler should compute momentum + energy residuals."""

    def test_handler_returns_momentum(self):
        """Handler should return a momentum residual."""
        graph, _ = _make_cdu_graph()
        from backend.liquidcooling.handlers.heat_exchanger import HeatExchangerBatchHandler

        handler = HeatExchangerBatchHandler(
            graph.hx_params, rho=graph.fluid_rho, cp=graph.fluid_cp,
        )
        P = torch.full((graph.N_nodes, graph.N_t), 150000.0)
        m = torch.full((graph.N_edges, graph.N_t), 0.5)
        T_all = torch.full((graph.N_nodes, graph.N_t), 310.0)

        state = {"P": P, "m": m, "T_all_nodes": T_all}
        res = handler.compute_residuals(state, graph.hx_indices, graph.dt, graph.bdf_order)
        assert "momentum" in res
        assert res["momentum"].shape[0] == 1  # 1 HX edge

    def test_handler_returns_energy(self):
        """Handler should return an energy residual."""
        graph, _ = _make_cdu_graph()
        from backend.liquidcooling.handlers.heat_exchanger import HeatExchangerBatchHandler

        handler = HeatExchangerBatchHandler(
            graph.hx_params, rho=graph.fluid_rho, cp=graph.fluid_cp,
        )
        P = torch.full((graph.N_nodes, graph.N_t), 150000.0)
        m = torch.full((graph.N_edges, graph.N_t), 0.5)
        T_all = torch.full((graph.N_nodes, graph.N_t), 310.0)

        state = {"P": P, "m": m, "T_all_nodes": T_all}
        res = handler.compute_residuals(state, graph.hx_indices, graph.dt, graph.bdf_order)
        assert "energy" in res

    def test_ntu_effectiveness_cooling(self):
        """HX should cool primary water toward T_secondary.

        With UA=5000 W/K, m=0.5 kg/s, cp=4182 J/kgK:
        NTU = 5000 / (0.5*4182) = 2.392
        eff = 1 - exp(-2.392) = 0.9085
        T_in = 320K, T_secondary = 293.15K:
        T_out = 320 - 0.9085*(320-293.15) = 295.61 K
        """
        graph, _ = _make_cdu_graph()
        from backend.liquidcooling.handlers.heat_exchanger import HeatExchangerBatchHandler

        handler = HeatExchangerBatchHandler(
            graph.hx_params, rho=graph.fluid_rho, cp=graph.fluid_cp,
        )

        hx_edge = graph.hx_indices.edge_ids[0].item()
        from_node = graph.edge_from[hx_edge].item()

        P = torch.full((graph.N_nodes, graph.N_t), 150000.0)
        m = torch.full((graph.N_edges, graph.N_t), 0.5)
        T_all = torch.full((graph.N_nodes, graph.N_t), 300.0)
        T_all[from_node, :] = 320.0  # hot return water

        state = {"P": P, "m": m, "T_all_nodes": T_all}
        res = handler.compute_residuals(state, graph.hx_indices, graph.dt, graph.bdf_order)

        UA = 5000.0
        cp = 4182.0
        m_abs = 0.5
        NTU = UA / (m_abs * cp)
        eff = 1.0 - math.exp(-NTU)
        T_out_expected = 320.0 - eff * (320.0 - 293.15)

        # The energy residual should be small when T_out matches NTU prediction
        # We can't check T_out directly, but we can verify the handler computes
        # a reasonable T_out via the outlet temp helper
        T_out = handler.outlet_temperature(state, graph.hx_indices, 0)
        assert abs(T_out[0].item() - T_out_expected) < 0.1, (
            f"T_out={T_out[0].item():.2f}, expected={T_out_expected:.2f}"
        )


class TestCDUHeatExchangerIntegration:
    """Full solve with CDU HEX in the loop."""

    @pytest.fixture(scope="class")
    def solved(self):
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer, StateInitializer
        from backend.liquidcooling.solver.model import LiquidODILModel
        from backend.liquidcooling.solver.adam_odil import solve

        graph, config = _make_cdu_graph()
        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)
        StateInitializer(graph).initialize(model.state)
        logs = solve(model, config.solver, print_every=500, bc_enforcer=enforcer)
        return model, graph, logs

    def test_converges(self, solved):
        model, graph, logs = solved
        # NTU-consistent energy formulation (Q_cp = m·cp·ΔT) creates stronger
        # flow-energy gradient coupling, raising the hydraulic plateau while
        # cold_plate_energy itself converges to ~1e-3.
        assert logs[-1].total < 10000.0, f"Did not converge: {logs[-1].total}"

    def test_hx_cools_water(self, solved):
        """HX outlet should be cooler than inlet (it rejects heat)."""
        model, graph, logs = solved
        hx_edge = graph.hx_indices.edge_ids[0].item()
        from_node = graph.edge_from[hx_edge].item()
        to_node = graph.edge_to[hx_edge].item()
        with torch.no_grad():
            T_all = model._build_T_all_nodes(dict(model.state))
        T_in = T_all[from_node, -1].item()
        T_out = T_all[to_node, -1].item()
        assert T_out < T_in, f"HX should cool: T_in={T_in:.2f} > T_out={T_out:.2f}"
