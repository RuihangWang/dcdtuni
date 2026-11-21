"""Tests for _extract_cdu_monitor in runner.py.

Validates that CDU-level metrics (supply/return pressure, total flow, supply
temperature) are correctly extracted from the model state and graph topology.

TDD: tests written before implementation.
"""

import torch
import pytest

# ---------------------------------------------------------------------------
# Fake graph / state helpers
# ---------------------------------------------------------------------------

class FakeGraph:
    """Minimal stand-in for CompiledGraph, sufficient to exercise _extract_cdu_monitor."""

    def __init__(self):
        self.N_nodes = 4
        self.N_edges = 3

        # bc_mask: nodes 0 and 3 are boundary-condition nodes
        self.bc_mask = torch.tensor([True, False, False, True])

        # BC pressures — only meaningful at BC nodes
        self.bc_P = torch.tensor([200000.0, 0.0, 0.0, 100000.0])

        # BC temperatures — only node 0 (supply) has a prescribed T
        self.bc_T = torch.tensor([288.15, 0.0, 0.0, 0.0])
        self.bc_T_mask = torch.tensor([True, False, False, False])

        # String IDs in order
        self.node_ids = ["supply_bc", "junc_1", "junc_2", "return_bc"]

        # Edge topology: 0→1, 1→2, 2→3
        self.edge_from = torch.tensor([0, 1, 2], dtype=torch.long)
        self.edge_to   = torch.tensor([1, 2, 3], dtype=torch.long)

        # Adjacency lists (incoming / outgoing edge indices per node)
        self.node_in_edges  = [[], [0], [1], [2]]
        self.node_out_edges = [[0], [1], [2], []]

        # Fluid specific heat (J kg⁻¹ K⁻¹)
        self.fluid_cp = 4182.0


class FakeModel:
    """Minimal stand-in for LiquidODILModel (only .state is used)."""

    def __init__(self):
        # Mass flows [N_edges, 1] — all 0.5 kg/s forward
        # Pressures  [N_nodes, 1]
        self.state = {
            "m": torch.tensor([[0.5], [0.5], [0.5]], dtype=torch.float64),
            "P": torch.tensor(
                [[200000.0], [180000.0], [120000.0], [100000.0]],
                dtype=torch.float64,
            ),
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExtractCduMonitorReturnsDict:
    """Smoke test: function returns a dict with the required keys."""

    def setup_method(self):
        from backend.liquidcooling.runner import _extract_cdu_monitor
        self._fn = _extract_cdu_monitor
        self.graph = FakeGraph()
        self.model = FakeModel()

    def test_returns_dict(self):
        result = self._fn(self.model, self.graph)
        assert isinstance(result, dict)

    def test_has_supply_P_Pa(self):
        result = self._fn(self.model, self.graph)
        assert "supply_P_Pa" in result

    def test_has_return_P_Pa(self):
        result = self._fn(self.model, self.graph)
        assert "return_P_Pa" in result

    def test_has_total_flow_kg_s(self):
        result = self._fn(self.model, self.graph)
        assert "total_flow_kg_s" in result


class TestExtractCduMonitorValues:
    """Verify extracted values match the fake graph/state."""

    def setup_method(self):
        from backend.liquidcooling.runner import _extract_cdu_monitor
        self._fn = _extract_cdu_monitor
        self.graph = FakeGraph()
        self.model = FakeModel()
        self.result = self._fn(self.model, self.graph)

    def test_supply_P_Pa(self):
        # Node 0 is supply BC; its pressure in state["P"] is 200 000 Pa
        assert abs(self.result["supply_P_Pa"] - 200000.0) < 1.0

    def test_return_P_Pa(self):
        # Node 3 is return BC; its pressure in state["P"] is 100 000 Pa
        assert abs(self.result["return_P_Pa"] - 100000.0) < 1.0

    def test_total_flow_positive(self):
        # Supply BC (node 0) has one outgoing edge with |m| = 0.5 kg/s
        assert self.result["total_flow_kg_s"] > 0.0

    def test_total_flow_value(self):
        # Exactly 0.5 kg/s on the single outgoing edge of the supply node
        assert abs(self.result["total_flow_kg_s"] - 0.5) < 1e-4


class TestExtractCduMonitorSupplyTemp:
    """Verify supply temperature is included when bc_T_mask is set."""

    def setup_method(self):
        from backend.liquidcooling.runner import _extract_cdu_monitor
        self._fn = _extract_cdu_monitor
        self.graph = FakeGraph()
        self.model = FakeModel()
        self.result = self._fn(self.model, self.graph)

    def test_has_supply_T_K(self):
        assert "supply_T_K" in self.result

    def test_supply_T_value(self):
        # Supply BC has bc_T_mask=True and bc_T=288.15 K
        assert abs(self.result["supply_T_K"] - 288.15) < 0.01


def test_transient_callbacks_sample_current_solved_cp_and_cdu_frame():
    import math
    from backend.tests.test_liquid.test_dynamic_thermal import loop_graph
    from backend.liquidcooling.solver.model import LiquidODILModel
    from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
    from backend.liquidcooling.runner import _extract_cp_monitor, _extract_cdu_monitor

    g = loop_graph(n=4)
    g.bc_T_mask[0] = True
    g.bc_T_series = g.bc_T[:, None].expand(-1, g.N_t).clone()
    g.bc_T_series[0] = torch.tensor([298., 300., 303., 307.])
    model = LiquidODILModel(g)
    with torch.no_grad():
        model.state['m'][:] = torch.tensor([.3, .2, -.1, .4])
        model.state['P'][0] = torch.tensor([2e5, 2.1e5, 1.9e5, 2.2e5])
    observed = []
    def callback(t, n, max_chip):
        cp = _extract_cp_monitor(model, g, ['cold_plate_1'])['cold_plate_1']
        cdu = _extract_cdu_monitor(model, g)
        assert cp['T_chip'] == round(max_chip, 2)
        e = int(g.cp_indices.edge_ids[0])
        signed_flow = model.state['m'][e, t].item()
        assert cp['m'] == round(abs(signed_flow), 4)
        assert model.thermal_node_history.shape[1] == t + 1
        inlet = int(g.edge_from[e] if signed_flow >= 0 else g.edge_to[e])
        tin = model.thermal_node_history[inlet, t].item()
        eps = 1. - math.exp(-g.cp_params.UA[0].item() / (abs(signed_flow) * g.fluid_cp))
        assert cp['T_water_out'] == round(tin + eps * (max_chip - tin), 2)
        assert cdu['supply_P_Pa'] == round(model.state['P'][0, t].item(), 2)
        assert cdu['return_P_Pa'] == round(model.state['P'][0, t].item(), 2)
        assert cdu['supply_T_K'] == round(g.bc_T_series[0, t].item(), 2)
        assert cdu['total_flow_kg_s'] == round(abs(signed_flow), 4)
        observed.append(cp)
    _solve_thermal_direct(model, g, timestep_callback=callback)
    assert len(observed) == 3
    assert getattr(model, 'thermal_time_index', None) is None
    assert _extract_cp_monitor(model, g, ['cold_plate_1'])['cold_plate_1'] == observed[-1]


def test_hybrid_thermal_callback_includes_physical_time():
    from backend.tests.test_liquid.test_dynamic_thermal import loop_graph
    from backend.liquidcooling.solver.model import LiquidODILModel
    from backend.liquidcooling.solver.hybrid import hybrid_solve
    g = loop_graph(n=4, dt=2.5)
    frames = []
    hybrid_solve(LiquidODILModel(g), g.solver_config, callback=frames.append)
    thermal = [f for f in frames if 'thermal_step' in f['losses']]
    assert len(thermal) == 3
    for t, frame in enumerate(thermal, 1):
        assert frame['time_index'] == t
        assert frame['time_s'] == t * g.dt
        assert frame['losses']['thermal_step'] == t
    assert frames[-1]['time_s'] == (g.N_t - 1) * g.dt


def test_legacy_cdu_temperature_series_defaults_to_final_sample():
    from backend.liquidcooling.runner import _extract_cdu_monitor
    graph, model = FakeGraph(), FakeModel()
    graph.bc_T_series = graph.bc_T[:, None].repeat(1, 3)
    graph.bc_T_series[0] = torch.tensor([290., 300., 310.])
    model.state = {name: x.repeat(1, 3) for name, x in model.state.items()}
    assert _extract_cdu_monitor(model, graph)['supply_T_K'] == 310.
