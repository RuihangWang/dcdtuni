"""Tests for bc_T_mask compilation and free-outflow temperature."""
import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph


FIXTURE_DIR = Path(__file__).parent


class TestBcTMaskCompilation:
    """Verify that bc_T_mask is populated correctly from .liquidrun config."""

    def test_supply_has_dirichlet_T(self):
        """Supply node with explicit T_K should have bc_T_mask=True."""
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        # Node 0 is "supply" with bc.T_K = 308.15
        assert graph.bc_T_mask[0].item() is True

    def test_return_has_free_outflow(self):
        """Return node without T_K should have bc_T_mask=False."""
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        # Node 1 is "return" with no T_K
        assert graph.bc_T_mask[1].item() is False

    def test_junction_has_no_dirichlet(self):
        """Junction nodes should have bc_T_mask=False."""
        raw = json.loads((FIXTURE_DIR / "simple_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        # Node "mid" (index 1) is a junction
        assert graph.bc_T_mask[1].item() is False

    def test_mask_shape_matches_nodes(self):
        """bc_T_mask should have shape [N_nodes]."""
        raw = json.loads((FIXTURE_DIR / "thermal_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        assert graph.bc_T_mask.shape == (graph.N_nodes,)
        assert graph.bc_T_mask.dtype == torch.bool


from backend.liquidcooling.solver.model import LiquidODILModel
from backend.liquidcooling.solver.adam_odil import solve


class TestFreeOutflowTemperature:
    """Verify that return nodes without T_K get flow-weighted mixed temperature,
    not a fixed Dirichlet value."""

    @pytest.fixture(scope="class")
    def solved(self):
        raw = json.loads((FIXTURE_DIR / "thermal_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        model = LiquidODILModel(graph)

        config.solver.max_epochs = 5000

        with torch.no_grad():
            for i, (is_bc, p_bc) in enumerate(zip(graph.bc_mask, graph.bc_P)):
                if is_bc:
                    model.state["P"].data[i, :] = p_bc.item()
            model.state["m"].data[:, :] = 6.0

        logs = solve(model, config.solver, print_every=500)
        return model, graph, logs

    def test_supply_temp_is_dirichlet(self, solved):
        """Supply node temperature should equal bc_T (prescribed)."""
        model, graph, _ = solved
        with torch.no_grad():
            T_all = model._build_T_all_nodes(dict(model.state))
        supply_idx = graph.node_ids.index("cdu_supply")
        assert T_all[supply_idx, 0].item() == pytest.approx(308.15, abs=0.01)

    def test_return_temp_differs_from_init(self, solved):
        """Return node temperature should NOT equal T_fluid_init — it should
        be the actual upstream mixed temperature (warmer due to cold plate heat)."""
        model, graph, _ = solved
        with torch.no_grad():
            T_all = model._build_T_all_nodes(dict(model.state))
        return_idx = graph.node_ids.index("cdu_return")
        T_return = T_all[return_idx, 0].item()
        # With 350W heat load, return temp must be above supply temp (308.15K)
        assert T_return > 308.15 + 0.1, (
            f"Return temp {T_return:.2f}K should be above supply 308.15K "
            f"(cold plate adds heat)"
        )

    def test_return_temp_below_chip(self, solved):
        """Return water temp should be below chip temperature."""
        model, graph, _ = solved
        with torch.no_grad():
            T_all = model._build_T_all_nodes(dict(model.state))
        return_idx = graph.node_ids.index("cdu_return")
        T_return = T_all[return_idx, 0].item()
        T_chip = model.state["T_s"].data[0, 0].item()
        assert T_return < T_chip
