"""TDD: pipe_energy residual after hybrid_solve should be near-zero.

The hybrid solver (Newton hydraulics + direct thermal march) computes exact
pipe cell temperatures.  When thermal_forward() is called afterwards to
report residuals, the pipe energy residual should be consistent with the
direct solver's solution.

A large pipe_energy (>>1) indicates that _build_T_all_nodes() in
thermal_forward computes different junction node temperatures than
the direct solver used internally, causing a mismatch at the first
cell of each junction-fed pipe.
"""

import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

FIXTURE_DIR = Path(__file__).parent


@pytest.fixture(scope="module")
def hybrid_result():
    """Run hybrid_solve on the single-rack fixture and return (model, logs)."""
    from backend.liquidcooling.schema import LiquidrunConfig
    from backend.liquidcooling.graphcompiling.compiler import compile_graph
    from backend.liquidcooling.solver.model import LiquidODILModel
    from backend.liquidcooling.solver.hybrid import hybrid_solve

    raw = json.loads((FIXTURE_DIR / "single_rack.liquidrun").read_text())
    cfg = LiquidrunConfig.model_validate(raw)
    graph = compile_graph(cfg)
    model = LiquidODILModel(graph)
    logs = hybrid_solve(model, graph.solver_config, print_every=9999)
    return model, logs


class TestHybridPipeResidual:

    def test_pipe_energy_small_after_hybrid(self, hybrid_result):
        """After hybrid_solve, pipe_energy residual should be < 1.0.

        The direct solver sets T_w exactly; thermal_forward should report
        near-zero pipe residuals when T_all_nodes is consistent.
        """
        model, logs = hybrid_result
        losses = model.thermal_forward()
        pipe_energy = losses.get("pipe_energy", torch.tensor(0.0)).item()
        assert pipe_energy < 1.0, (
            f"pipe_energy={pipe_energy:.2f} is too large after hybrid_solve. "
            f"Expected < 1.0 (near-zero for consistent node temps)."
        )

    def test_cold_plate_energy_small(self, hybrid_result):
        """CP energy should remain near-zero (sanity check, already passes)."""
        model, logs = hybrid_result
        losses = model.thermal_forward()
        cp_energy = losses.get("cold_plate_energy", torch.tensor(0.0)).item()
        assert cp_energy < 1.0, f"cold_plate_energy={cp_energy:.2f}"

    def test_final_log_total_small(self, hybrid_result):
        """The final reported total loss should also be small."""
        model, logs = hybrid_result
        final_total = logs[-1].total
        assert final_total < 1.0, (
            f"Final logged total={final_total:.2f}, expected < 1.0"
        )
