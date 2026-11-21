"""Tests for the bc_ic module: BoundaryConditionEnforcer, StateInitializer, BCICMonitor."""
import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph

FIXTURE_DIR = Path(__file__).parent


def _make_graph(fixture_name="minimal.liquidrun"):
    raw = json.loads((FIXTURE_DIR / fixture_name).read_text())
    config = LiquidrunConfig.model_validate(raw)
    return compile_graph(config), config


class TestBoundaryConditionEnforcer:

    def test_enforce_clamps_P(self):
        """After enforce(), BC node pressures equal their target values."""
        graph, _ = _make_graph()
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), 999999.0)),
            "m": nn.Parameter(torch.ones(graph.N_edges, graph.N_t)),
        }
        enforcer.enforce(state)

        for i in range(graph.N_nodes):
            if graph.bc_mask[i]:
                assert torch.allclose(
                    state["P"].data[i, :],
                    torch.full((graph.N_t,), graph.bc_P[i].item()),
                ), f"Node {i}: P={state['P'].data[i,0].item():.1f}, expected={graph.bc_P[i].item():.1f}"

        for i in range(graph.N_nodes):
            if not graph.bc_mask[i]:
                assert state["P"].data[i, 0].item() == pytest.approx(999999.0)

    def test_enforce_zeros_dead_ends(self):
        """After enforce(), dead-end edges have m clamped to zero."""
        graph, _ = _make_graph("blind_end.liquidrun")
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), 150000.0)),
            "m": nn.Parameter(torch.full((graph.N_edges, graph.N_t), 5.0)),
        }
        enforcer.enforce(state)

        assert enforcer.dead_end_edge_mask.any(), "blind_end fixture should have at least one dead-end edge"
        dead_idx = enforcer.dead_end_edge_mask.nonzero(as_tuple=True)[0]
        assert torch.all(state["m"].data[dead_idx] == 0.0), "dead-end edge mass flows must be zeroed after enforce()"

        live_idx = (~enforcer.dead_end_edge_mask).nonzero(as_tuple=True)[0]
        if live_idx.numel() > 0:
            assert torch.allclose(state["m"].data[live_idx], torch.full_like(state["m"].data[live_idx], 5.0)), "non-dead-end edges must be unchanged"

    def test_loss_returns_bc_P(self):
        """loss() must return a dict with a positive scalar 'bc_P' key."""
        graph, _ = _make_graph()
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), 999999.0)),
            "m": nn.Parameter(torch.ones(graph.N_edges, graph.N_t)),
        }
        losses = enforcer.loss(state, w_bc=100.0)

        assert "bc_P" in losses, "loss() must return a 'bc_P' key"
        bc_P_val = losses["bc_P"]
        assert bc_P_val.item() > 0.0, "bc_P loss must be positive when pressures are wrong"
        assert bc_P_val.ndim == 0, "bc_P loss must be a scalar tensor"


class TestStateInitializer:

    def test_flow_directions_correct(self):
        """Supply->return flows should be positive."""
        graph, _ = _make_graph()
        from backend.liquidcooling.solver.bc_ic import StateInitializer
        import torch.nn as nn

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), graph.P_init)),
            "m": nn.Parameter(torch.zeros(graph.N_edges, graph.N_t)),
            "T_w": nn.Parameter(torch.full((graph.N_cells, graph.N_t), graph.T_fluid_init)),
            "T_s": nn.Parameter(torch.full((graph.N_cp, graph.N_t), graph.T_solid_init)),
        }
        StateInitializer(graph).initialize(state)
        m_val = state["m"].data[0, 0].item()
        assert m_val > 0, f"Flow should be positive (supply->return), got {m_val}"

    def test_pressure_monotonic(self):
        """Pressure should decrease from supply to return."""
        graph, _ = _make_graph()
        from backend.liquidcooling.solver.bc_ic import StateInitializer
        import torch.nn as nn

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), graph.P_init)),
            "m": nn.Parameter(torch.zeros(graph.N_edges, graph.N_t)),
            "T_w": nn.Parameter(torch.full((graph.N_cells, graph.N_t), graph.T_fluid_init)),
            "T_s": nn.Parameter(torch.full((graph.N_cp, graph.N_t), graph.T_solid_init)),
        }
        StateInitializer(graph).initialize(state)
        supply_idx = graph.node_ids.index("supply")
        return_idx = graph.node_ids.index("return")
        P_supply = state["P"].data[supply_idx, 0].item()
        P_return = state["P"].data[return_idx, 0].item()
        assert P_supply > P_return, f"P_supply={P_supply:.0f} should be > P_return={P_return:.0f}"

    def test_temperatures_initialized(self):
        """T_w and T_s should be at their init values."""
        graph, _ = _make_graph()
        from backend.liquidcooling.solver.bc_ic import StateInitializer
        import torch.nn as nn

        state = {
            "P": nn.Parameter(torch.zeros(graph.N_nodes, graph.N_t)),
            "m": nn.Parameter(torch.zeros(graph.N_edges, graph.N_t)),
            "T_w": nn.Parameter(torch.zeros(graph.N_cells, graph.N_t)),
            "T_s": nn.Parameter(torch.zeros(graph.N_cp, graph.N_t)),
        }
        StateInitializer(graph).initialize(state)
        if graph.N_cells > 0:
            assert torch.allclose(state["T_w"].data, torch.full_like(state["T_w"].data, graph.T_fluid_init))
        if graph.N_cp > 0:
            assert torch.allclose(state["T_s"].data, torch.full_like(state["T_s"].data, graph.T_solid_init))


class TestModelIntegration:

    def test_model_uses_enforcer(self):
        """Model.forward() returns bc_P loss from enforcer."""
        graph, config = _make_graph()
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer
        from backend.liquidcooling.solver.model import LiquidODILModel

        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)
        losses = model()
        assert "bc_P" in losses
        assert losses["bc_P"].item() >= 0


class TestBCICMonitor:

    def test_reports_aggregates(self):
        """compute() returns total_bc and total_ic as non-negative floats."""
        graph, _ = _make_graph()
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer, BCICMonitor
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)
        monitor = BCICMonitor(graph)

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), 999999.0)),
            "m": nn.Parameter(torch.ones(graph.N_edges, graph.N_t)),
            "T_w": nn.Parameter(torch.full((graph.N_cells, graph.N_t), 400.0)),
            "T_s": nn.Parameter(torch.full((graph.N_cp, graph.N_t), 500.0)),
        }
        result = monitor.compute(state, enforcer)

        assert "total_bc" in result
        assert "total_ic" in result
        assert isinstance(result["total_bc"], float)
        assert isinstance(result["total_ic"], float)
        assert result["total_bc"] >= 0
        assert result["total_ic"] >= 0
        assert result["total_bc"] > 0  # wrong pressures = nonzero BC residual


class TestValveKvUnits:
    def test_valve_flow_isa_equation(self):
        """Valve with Kv=2.5, dP=1 bar should give correct ISA flow."""
        # ISA: Q[m³/h] = Kv * sqrt(dP[bar] / (rho/1000))
        # m[kg/s] = Q * rho / 3600
        Kv = 2.5
        dP_bar = 1.0
        rho = 997.0
        Q_expected = Kv * (dP_bar / (rho / 1000.0)) ** 0.5
        m_expected = Q_expected * rho / 3600.0

        from backend.liquidcooling.handlers.valve import ValveBatchHandler
        from backend.liquidcooling.graphcompiling.compiled_graph import ValveBatchParams, ValveIndices

        params = ValveBatchParams(
            D=torch.tensor([0.05]),
            Kv_max=torch.tensor([Kv]),
            opening=torch.tensor([[1.0]]),
            characteristic=torch.tensor([0]),  # linear
        )
        indices = ValveIndices(
            edge_ids=torch.tensor([0], dtype=torch.long),
            node_from=torch.tensor([0], dtype=torch.long),
            node_to=torch.tensor([1], dtype=torch.long),
        )
        handler = ValveBatchHandler(params, rho=rho)

        # 1 bar = 100000 Pa pressure difference
        P = torch.tensor([[250000.0], [150000.0]])
        m = torch.tensor([[0.5]])
        state = {"P": P, "m": m}

        res = handler.compute_residuals(state, indices, dt=1.0, bdf_order=1)
        r_mom = res["momentum"]
        m_model = m[0, 0].item() - r_mom[0, 0].item()
        assert abs(m_model - m_expected) < 0.01, (
            f"Valve m_model={m_model:.4f}, expected={m_expected:.4f}"
        )


class TestJunctionDepth:
    def test_depth_computed_single_rack(self):
        """single_rack has junctions, depth should be >= 2."""
        graph, _ = _make_graph("single_rack.liquidrun")
        assert hasattr(graph, "max_junction_depth")
        assert graph.max_junction_depth >= 2

    def test_depth_minimal_no_junctions(self):
        """minimal has no junctions, depth should be 0."""
        graph, _ = _make_graph("minimal.liquidrun")
        assert hasattr(graph, "max_junction_depth")
        assert graph.max_junction_depth == 0


class TestTJunctionKMinor:
    def test_branch_pipes_have_tee_K(self):
        """Manifold branch pipes should have K_minor including tee contribution."""
        graph, _ = _make_graph("single_rack.liquidrun")
        assert graph.pipe_params is not None
        for local_i in range(graph.pipe_params.K_minor.shape[0]):
            edge_id = graph.pipe_indices.edge_ids[local_i].item()
            edge_str = graph.edge_str_ids[edge_id]
            K = graph.pipe_params.K_minor[local_i].item()
            if "branch" in edge_str:
                assert K > 0, f"Branch pipe {edge_str} should have K_minor > 0 (tee), got {K}"
