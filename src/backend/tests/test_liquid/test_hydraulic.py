"""
End-to-end hydraulic loop test: load a simple pipe network from
simple_loop.liquidrun, solve via ODIL, and verify physical sanity.
"""

import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph
from backend.liquidcooling.solver.model import LiquidODILModel
from backend.liquidcooling.solver.adam_odil import solve

FIXTURE_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _load_and_solve(fixture_name, **solver_overrides):
    """Load a .liquidrun fixture, compile, build model, and solve.

    Applies smart initialisation so the ODIL solver converges reliably:
    - BC node pressures are initialised to their target values (avoids the
      huge initial BC penalty that stalls Adam when starting from a uniform
      P_init far from the boundary values).
    - Mass flow rates are initialised to a small positive value (breaks the
      zero-gradient saddle point for momentum).
    """
    raw = json.loads((FIXTURE_DIR / fixture_name).read_text())
    config = LiquidrunConfig.model_validate(raw)

    # Apply any solver overrides
    for key, val in solver_overrides.items():
        setattr(config.solver, key, val)

    graph = compile_graph(config)
    model = LiquidODILModel(graph)

    # Smart initialisation
    with torch.no_grad():
        # Set BC node pressures to their target values
        for i, (is_bc, p_bc) in enumerate(zip(graph.bc_mask, graph.bc_P)):
            if is_bc:
                model.state["P"].data[i, :] = p_bc.item()
        # Seed mass flows with a small positive value
        model.state["m"].data[:, :] = 1.0

    logs = solve(model, config.solver, print_every=500)
    return model, graph, logs


# ---------------------------------------------------------------------------
# Shared fixture -- solve once, reuse across all tests in the class
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def solution():
    """Solve the simple loop once for the whole module."""
    model, graph, logs = _load_and_solve(
        "simple_loop.liquidrun",
        lr=1.0,           # lr=1.0 is well-matched to the Pa-scale pressures
        max_epochs=5000,
    )
    return model, graph, logs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSimpleLoop:

    def test_converges(self, solution):
        """Final total loss must be below 10 (pipe momentum dominates)."""
        _model, _graph, logs = solution
        final_loss = logs[-1].total
        assert final_loss < 10.0, (
            f"Solver did not converge: final loss = {final_loss:.6e}"
        )

    def test_mass_conservation(self, solution):
        """Both pipes in series must carry the same mass flow rate (rel err < 1%)."""
        model, _graph, _logs = solution
        m = model.state["m"].detach()  # [N_edges, N_t]

        # Edge 0 = pipe_supply, Edge 1 = pipe_return
        m0 = m[0, 0].item()
        m1 = m[1, 0].item()

        # Relative error based on average magnitude
        avg = (abs(m0) + abs(m1)) / 2.0
        assert avg > 1e-12, "Both flows are essentially zero"
        rel_err = abs(m0 - m1) / avg
        assert rel_err < 0.01, (
            f"Mass not conserved: m_supply={m0:.6e}, m_return={m1:.6e}, "
            f"rel_err={rel_err:.4e}"
        )

    def test_flow_positive(self, solution):
        """Flow direction must be from supply (high P) to return (low P), i.e. m > 0."""
        model, _graph, _logs = solution
        m = model.state["m"].detach()

        m0 = m[0, 0].item()
        m1 = m[1, 0].item()

        assert m0 > 0, f"pipe_supply flow should be positive, got {m0:.6e}"
        assert m1 > 0, f"pipe_return flow should be positive, got {m1:.6e}"

    def test_junction_pressure_between_bcs(self, solution):
        """Mid-junction pressure must lie between the two BC pressures (100-200 kPa)."""
        model, graph, _logs = solution
        P = model.state["P"].detach()  # [N_nodes, N_t]

        # Node ordering: 0=cdu_supply, 1=mid, 2=cdu_return
        assert graph.node_ids[1] == "mid"
        P_mid = P[1, 0].item()

        assert 100000.0 < P_mid < 200000.0, (
            f"Junction pressure {P_mid:.1f} Pa is not between 100 kPa and 200 kPa"
        )

    def test_bc_pressures_enforced(self, solution):
        """Supply node P ~ 200000 Pa, return node P ~ 100000 Pa (within 100 Pa)."""
        model, graph, _logs = solution
        P = model.state["P"].detach()

        # Node 0 = cdu_supply (200 kPa), Node 2 = cdu_return (100 kPa)
        assert graph.node_ids[0] == "cdu_supply"
        assert graph.node_ids[2] == "cdu_return"

        P_supply = P[0, 0].item()
        P_return = P[2, 0].item()

        assert abs(P_supply - 200000.0) < 100.0, (
            f"Supply BC not enforced: P={P_supply:.1f} Pa, expected ~200000 Pa"
        )
        assert abs(P_return - 100000.0) < 100.0, (
            f"Return BC not enforced: P={P_return:.1f} Pa, expected ~100000 Pa"
        )
