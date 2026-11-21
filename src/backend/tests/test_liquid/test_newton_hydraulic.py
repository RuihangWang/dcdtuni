"""Tests for the GGA Newton hydraulic solver."""
import json
import math
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

FIXTURE_DIR = Path(__file__).parent
# liquid_single.liquidrun / liquid_cooling_demo.liquidrun are frozen copies of the
# LiquidSingle and Liquid case exports (case/*/Liquid is gitignored).


class TestEdgeHeadloss:
    """Test dp(m) and g=d(dp)/d(m) for each edge type."""

    def test_pipe_headloss_laminar(self):
        """Pipe with laminar flow: dp = f*L/D * m|m|/(2*rho*A^2) + rho*g*dh."""
        from backend.liquidcooling.solver.newton_hydraulic import pipe_headloss
        D, L, A = 0.025, 1.0, math.pi / 4 * 0.025**2
        K_minor, roughness, dh = 0.0, 1.5e-6, 0.0
        rho, mu = 998.0, 0.001
        m = torch.tensor(0.01)
        dp, g = pipe_headloss(m, D, L, A, K_minor, roughness, dh, rho, mu)
        v = 0.01 / (998.0 * A)
        Re = 998.0 * v * 0.025 / 0.001
        f = 64.0 / Re
        dp_expected = f * (L / D) * 0.01 * abs(0.01) / (2 * rho * A**2)
        assert abs(dp.item() - dp_expected) / abs(dp_expected) < 0.05
        assert g.item() > 0

    def test_pipe_headloss_with_gravity(self):
        """Gravity term adds rho*g*dh to headloss."""
        from backend.liquidcooling.solver.newton_hydraulic import pipe_headloss
        D, L, A = 0.025, 1.0, math.pi / 4 * 0.025**2
        rho, mu = 998.0, 0.001
        m = torch.tensor(0.01)
        dp_flat, _ = pipe_headloss(m, D, L, A, 0.0, 1.5e-6, 0.0, rho, mu)
        dp_up, _ = pipe_headloss(m, D, L, A, 0.0, 1.5e-6, 1.0, rho, mu)
        dp_grav = 998.0 * 9.81 * 1.0
        assert abs((dp_up - dp_flat).item() - dp_grav) < 1.0

    def test_pipe_derivative_matches_autograd(self):
        """Analytical g should match torch autograd."""
        from backend.liquidcooling.solver.newton_hydraulic import pipe_headloss
        D, L, A = 0.025, 1.0, math.pi / 4 * 0.025**2
        rho, mu = 998.0, 0.001
        m = torch.tensor(0.05, requires_grad=True)
        dp, g_analytical = pipe_headloss(m, D, L, A, 0.5, 1.5e-6, 0.0, rho, mu)
        dp.backward()
        g_autograd = m.grad.item()
        assert abs(g_analytical.item() - g_autograd) / abs(g_autograd) < 0.01

    def test_cp_headloss(self):
        """Cold plate: dp = dp_a * m * |m| + dp_b * m."""
        from backend.liquidcooling.solver.newton_hydraulic import quadratic_headloss
        m = torch.tensor(0.05)
        dp_a, dp_b = 5e4, 100.0
        dp, g = quadratic_headloss(m, dp_a, dp_b)
        dp_expected = dp_a * 0.05 * 0.05 + dp_b * 0.05
        assert abs(dp.item() - dp_expected) < 0.01
        assert g.item() > 0

    def test_pump_headloss(self):
        """Pump affinity law at reduced speed: H = N^2*H0 - a*Q^2 (D1).

        (H0, a) are the rated-speed curve; H_N(Q) = N^2*H_rated(Q/N). The
        pre-2026-10-21 law N^2*(H0 - a*Q^2) kept the zero-head flow fixed.
        """
        from backend.liquidcooling.solver.newton_hydraulic import pump_headloss
        m = torch.tensor(0.5, dtype=torch.float64, requires_grad=True)
        rho, g_acc = 998.0, 9.81
        H0, a_coef, rpm = 20.0, 5e5, 0.6
        dp, g = pump_headloss(m, H0, a_coef, rpm, rho, g_acc)
        Q = 0.5 / rho
        H = rpm**2 * H0 - a_coef * Q**2
        dp_expected = -rho * g_acc * H
        assert dp.item() == pytest.approx(dp_expected, rel=1e-12)
        assert dp.item() < 0
        (exact,) = torch.autograd.grad(dp, m)
        assert g.item() == pytest.approx(exact.item(), rel=1e-12)
        assert g.item() == pytest.approx(2.0 * g_acc * a_coef * 0.5 / rho, rel=1e-12)

    def test_pump_zero_head_flow_scales_with_speed(self):
        """Affinity: the zero-head flow is N*sqrt(H0/a), the shut-off head N^2*H0."""
        from backend.liquidcooling.solver.newton_hydraulic import pump_headloss
        rho, g_acc, H0, a_coef = 998.0, 9.81, 20.0, 5e5
        for rpm in (1.0, 0.8, 0.6):
            m0 = torch.tensor(rho * rpm * (H0 / a_coef) ** 0.5, dtype=torch.float64)
            dp, _ = pump_headloss(m0, H0, a_coef, rpm, rho, g_acc)
            assert abs(dp.item()) < 1e-9 * rho * g_acc * H0
            shut, _ = pump_headloss(torch.tensor(0., dtype=torch.float64), H0, a_coef, rpm, rho, g_acc)
            assert shut.item() == pytest.approx(-rho * g_acc * rpm**2 * H0, rel=1e-12)


# ---------------------------------------------------------------------------
# Helper: load liquidrun → compile → gga_solve
# ---------------------------------------------------------------------------

def _load_and_gga(fixture_path, **kwargs):
    """Load a .liquidrun file, compile graph, and run gga_solve."""
    from backend.liquidcooling.schema import LiquidrunConfig
    from backend.liquidcooling.graphcompiling.compiler import compile_graph
    from backend.liquidcooling.solver.newton_hydraulic import gga_solve

    raw = json.loads(Path(fixture_path).read_text())
    config = LiquidrunConfig.model_validate(raw)
    graph = compile_graph(config)
    P, m, info = gga_solve(graph, **kwargs)
    return P, m, info, graph


# ---------------------------------------------------------------------------
# GGA Newton solver tests
# ---------------------------------------------------------------------------

class TestGGASolve:

    def test_simple_pipe_two_nodes(self):
        """Two BC nodes connected by one pipe: flow = sqrt(dP / R)."""
        P, m, info, graph = _load_and_gga(FIXTURE_DIR / "minimal.liquidrun")

        # Must converge
        assert info["converged"], (
            f"GGA did not converge in {info['iterations']} iterations"
        )
        assert info["iterations"] < 20

        # Flow must be positive (supply P > return P)
        assert m[0, 0].item() > 0, f"Expected positive flow, got {m[0, 0].item()}"

        # BC pressures must be respected
        assert abs(P[0, 0].item() - 200000.0) < 1.0
        assert abs(P[1, 0].item() - 100000.0) < 1.0

    def test_cdu_loop_three_edges(self):
        """CDU loop with CP + HX + pipe: all flows should be finite."""
        P, m, info, graph = _load_and_gga(FIXTURE_DIR / "cdu_loop.liquidrun")

        assert info["converged"], (
            f"GGA did not converge in {info['iterations']} iterations"
        )

        # All flows should be finite and nonzero
        assert torch.isfinite(m[:, 0]).all(), "Some flows are not finite"
        for e in range(graph.N_edges):
            assert m[e, 0].abs().item() > 1e-10, (
                f"Edge {e} flow is essentially zero: {m[e, 0].item()}"
            )

    def test_liquid_single_matches_analytical(self):
        """LiquidSingle: Newton flow should match analytical sqrt(dP/R_total)."""
        liquidrun_path = FIXTURE_DIR / "liquid_single.liquidrun"

        P, m, info, graph = _load_and_gga(liquidrun_path)

        assert info["converged"], (
            f"GGA did not converge in {info['iterations']} iterations"
        )

        # Find the cold plate edge (edge_type == 1)
        cp_mask = graph.edge_type == 1
        cp_idx = torch.where(cp_mask)[0]
        assert len(cp_idx) == 1, f"Expected 1 CP edge, found {len(cp_idx)}"

        cp_flow = m[cp_idx[0], 0].abs().item()
        # Analytical target is ~0.064 kg/s; allow 50% tolerance for
        # network resistance differences
        assert 0.02 < cp_flow < 0.15, (
            f"CP flow {cp_flow:.4f} kg/s outside expected range [0.02, 0.15]"
        )

    def test_dead_end_edges_zero(self):
        """Dead-end edges should be clamped to zero flow."""
        liquidrun_path = FIXTURE_DIR / "liquid_single.liquidrun"

        P, m, info, graph = _load_and_gga(liquidrun_path)
        assert info["converged"]

        # Dead-end nodes: junction nodes with exactly 1 edge
        for i in range(graph.N_nodes):
            if not graph.junction_mask[i]:
                continue
            degree = len(graph.node_in_edges[i]) + len(graph.node_out_edges[i])
            if degree == 1:
                # Find the single edge
                edges = graph.node_in_edges[i] + graph.node_out_edges[i]
                for e in edges:
                    assert m[e, 0].abs().item() < 1e-12, (
                        f"Dead-end edge {e} at node {graph.node_ids[i]} "
                        f"has nonzero flow: {m[e, 0].item()}"
                    )

    def test_mass_conservation_at_junctions(self):
        """At every junction, sum of in-flows == sum of out-flows."""
        P, m, info, graph = _load_and_gga(FIXTURE_DIR / "cdu_loop.liquidrun")
        assert info["converged"]

        B = graph.incidence.to_dense() if graph.incidence.is_sparse else graph.incidence
        # B @ m gives net flow at each node (positive = net outflow)
        residual = B @ m[:, 0]
        for i in range(graph.N_nodes):
            if graph.junction_mask[i]:
                assert abs(residual[i].item()) < 1e-4, (
                    f"Mass imbalance at junction {graph.node_ids[i]}: "
                    f"{residual[i].item():.6e}"
                )

    def test_output_shapes(self):
        """P and m must have correct shapes: [N_nodes, N_t] and [N_edges, N_t]."""
        P, m, info, graph = _load_and_gga(FIXTURE_DIR / "minimal.liquidrun")
        assert P.shape == (graph.N_nodes, graph.N_t)
        assert m.shape == (graph.N_edges, graph.N_t)


class TestThermalOnlyModel:
    def test_set_hydraulic_state(self):
        """set_hydraulic_state should freeze P and m."""
        from backend.liquidcooling.schema import LiquidrunConfig
        from backend.liquidcooling.graphcompiling.compiler import compile_graph
        from backend.liquidcooling.solver.model import LiquidODILModel
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer

        raw = json.loads((FIXTURE_DIR / "cdu_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)

        P = torch.full((graph.N_nodes, graph.N_t), 150000.0)
        m = torch.full((graph.N_edges, graph.N_t), 0.5)
        model.set_hydraulic_state(P, m)

        # P and m should not require grad
        assert not model.state["P"].requires_grad
        assert not model.state["m"].requires_grad

        # Thermal params should still require grad
        thermal_params = list(model.thermal_parameters())
        assert len(thermal_params) > 0

    def test_thermal_forward_no_momentum(self):
        """thermal_forward should only return energy and IC losses."""
        from backend.liquidcooling.schema import LiquidrunConfig
        from backend.liquidcooling.graphcompiling.compiler import compile_graph
        from backend.liquidcooling.solver.model import LiquidODILModel
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer

        raw = json.loads((FIXTURE_DIR / "cdu_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)

        P = torch.full((graph.N_nodes, graph.N_t), 150000.0)
        m = torch.full((graph.N_edges, graph.N_t), 0.5)
        model.set_hydraulic_state(P, m)

        losses = model.thermal_forward()
        assert "total" in losses
        for k in losses:
            assert "momentum" not in k, f"thermal_forward should not include {k}"
            assert k != "mass", f"thermal_forward should not include mass"


# ---------------------------------------------------------------------------
# Hybrid solver tests
# ---------------------------------------------------------------------------

class TestHybridSolve:
    """Test hybrid_solve (Newton hydraulics + Adam thermal) on LiquidSingle."""

    LIQUIDRUN = FIXTURE_DIR / "liquid_single.liquidrun"

    @pytest.fixture(scope="class")
    def hybrid_result(self):
        from backend.liquidcooling.schema import LiquidrunConfig
        from backend.liquidcooling.graphcompiling.compiler import compile_graph
        from backend.liquidcooling.solver.model import LiquidODILModel
        from backend.liquidcooling.solver.bc_ic import BoundaryConditionEnforcer
        from backend.liquidcooling.solver.hybrid import hybrid_solve

        raw = json.loads(self.LIQUIDRUN.read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)

        logs = hybrid_solve(
            model,
            config=config.solver,
            bc_enforcer=enforcer,
            print_every=500,
        )
        return model, graph, logs

    def test_converges(self, hybrid_result):
        """hybrid_solve should complete without error."""
        model, graph, logs = hybrid_result
        assert len(logs) > 0, "hybrid_solve returned no logs"

    def test_cp_flow_matches_analytical(self, hybrid_result):
        """CP flow from Newton phase should be within 1% of 0.064 kg/s."""
        model, graph, logs = hybrid_result
        cp_mask = graph.edge_type == 1
        cp_idx = torch.where(cp_mask)[0]
        assert len(cp_idx) >= 1, "No cold plate edges found"

        cp_flow = model.state["m"].data[cp_idx[0], 0].abs().item()
        assert abs(cp_flow - 0.064) / 0.064 < 0.01, (
            f"CP flow {cp_flow:.6f} kg/s not within 1% of 0.064"
        )

    def test_chip_temp_matches_analytical(self, hybrid_result):
        """Chip T_s at t=9 should be within 1K of analytical (412.96K).

        Uses Q_cp = m·cp·eff·(T_s − T_in) energy formulation (NTU-consistent).
        """
        model, graph, logs = hybrid_result
        T_s = model.state["T_s"].data
        if T_s.numel() == 0:
            pytest.skip("No cold plate chip temperatures in this case")

        T_anal_t9 = 412.96  # BDF-1 analytical at t=9 (NTU-consistent energy)
        T_current = T_s[0, -1].item()
        assert abs(T_current - T_anal_t9) < 1.0, (
            f"Chip T_s(9)={T_current:.2f}K, analytical={T_anal_t9:.2f}K, "
            f"err={abs(T_current - T_anal_t9):.2f}K > 1K"
        )


# ---------------------------------------------------------------------------
# Analytical validation — parallel topology
# ---------------------------------------------------------------------------

class TestLiquidParallelValidation:
    """Validate Newton solver on the 4-rack parallel topology (Liquid.cfdproj)."""

    LIQUIDRUN = FIXTURE_DIR / "liquid_cooling_demo.liquidrun"

    def test_rack_flows_match_analytical(self):
        """Per-rack flows should be within 5% of analytical."""
        from backend.liquidcooling.solver.newton_hydraulic import gga_solve
        from backend.liquidcooling.schema import LiquidrunConfig
        from backend.liquidcooling.graphcompiling.compiler import compile_graph

        raw = json.loads(self.LIQUIDRUN.read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        P, m, info = gga_solve(graph)
        assert info["converged"]

        expected = {
            "Default_Rack_1": (4, 0.03207),
            "Default_Rack_2": (4, 0.03207),
            "Default_Rack_3": (2, 0.04536),
            "Default_Rack_4": (3, 0.03703),
        }

        for rack in graph.rack_meta:
            n_cp, m_analytical = expected[rack.rack_id]
            first_cp = rack.server_edge_ids[0][0]
            cp_edge = graph.edge_str_ids.index(first_cp)
            m_numerical = m[cp_edge, 0].abs().item()

            rel_err = abs(m_numerical - m_analytical) / m_analytical
            assert rel_err < 0.05, (
                f"{rack.rack_id}: m={m_numerical:.5f}, "
                f"expected={m_analytical:.5f}, err={rel_err:.1%}"
            )

    def test_total_flow_conservation(self):
        """Total flow into supply BC == total flow out of return BC."""
        from backend.liquidcooling.solver.newton_hydraulic import gga_solve
        from backend.liquidcooling.schema import LiquidrunConfig
        from backend.liquidcooling.graphcompiling.compiler import compile_graph

        raw = json.loads(self.LIQUIDRUN.read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        P, m, info = gga_solve(graph)
        assert info["converged"]

        B = graph.incidence.to_dense() if graph.incidence.is_sparse else graph.incidence
        residual = B @ m[:, 0]
        for i in range(graph.N_nodes):
            if graph.junction_mask[i]:
                assert abs(residual[i].item()) < 1e-4, (
                    f"Mass imbalance at {graph.node_ids[i]}: {residual[i].item():.2e}"
                )
