"""Tests for blind-end pipe handling: dead-end junction → m ≈ 0."""
import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer
from backend.liquidcooling.solver.model import LiquidODILModel
from backend.liquidcooling.solver.adam_odil import solve

FIXTURE_DIR = Path(__file__).parent


class TestBlindEnd:
    """A dead-end junction (single edge) should have m ≈ 0 after ODIL solve."""

    @pytest.fixture(scope="class")
    def solved(self):
        raw = json.loads((FIXTURE_DIR / "blind_end.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        # Override solver: lr=0.01 and 5000 epochs converge reliably for
        # this topology (gravity=0 so no hydrostatic offset in blind pipe).
        config.solver.lr = 0.01
        config.solver.max_epochs = 5000
        graph = compile_graph(config)
        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)

        with torch.no_grad():
            for i, (is_bc, p_bc) in enumerate(zip(graph.bc_mask, graph.bc_P)):
                if is_bc:
                    model.state["P"].data[i, :] = p_bc.item()
            model.state["m"].data[:, :] = 1.0

        logs = solve(model, config.solver, print_every=500)
        return model, graph, logs

    def test_converges(self, solved):
        model, graph, logs = solved
        assert logs[-1].total < 10.0

    def test_main_flow_positive(self, solved):
        """Main pipe edges (0 and 1) should carry positive flow."""
        model, graph, _ = solved
        m = model.state["m"].detach()
        # pipe_main_a (edge 0) and pipe_main_b (edge 1)
        assert m[0, 0].item() > 0.01
        assert m[1, 0].item() > 0.01

    def test_blind_pipe_flow_near_zero(self, solved):
        """Blind-end pipe (edge 2) should have m ≈ 0."""
        model, graph, _ = solved
        m = model.state["m"].detach()
        # pipe_blind is edge 2 (third edge)
        m_blind = m[2, 0].abs().item()
        assert m_blind < 0.02, (
            f"Blind-end pipe flow {m_blind:.4f} kg/s should be near zero"
        )

    def test_dead_end_node_exists(self, solved):
        """The dead_end junction node should be in the graph."""
        _, graph, _ = solved
        assert "dead_end" in graph.node_ids

    def test_node_count(self, solved):
        """Graph should have 4 nodes: supply, mid, return, dead_end."""
        _, graph, _ = solved
        assert graph.N_nodes == 4
