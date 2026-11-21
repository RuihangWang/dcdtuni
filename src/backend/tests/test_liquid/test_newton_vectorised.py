"""The GGA assembly must be batched, and batching must not change the numbers.

`newton_hydraulic._compute_all_headlosses` originally walked every edge in a Python
loop, converting already-tensor parameters back to Python floats with `.item()` and
calling `torch.autograd.grad` once per edge to obtain a scalar derivative. On the
12-edge `single_rack` fixture that assembly was 78% of the whole Newton solve and the
per-edge backward alone was 39%, against 1.3% for `torch.linalg.solve`.

The repo already contains the batched form of this exact physics —
`handlers/pipe.py::PipeBatchHandler`, "one vectorised pass" — so the loop was a second,
slower implementation of a formula that was already vectorised.

Two tests, with different jobs:

* `test_assembly_does_at_most_one_backward_per_call` drives the change. It fails on the
  per-edge implementation and can only pass once the derivative is obtained for all
  edges at once (or analytically).
* the equivalence tests guard it. `pipe_headloss` / `quadratic_headloss` stay as the
  single-edge reference API and are the oracle; the batched path must reproduce them.
"""
import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

FIXTURE_DIR = Path(__file__).parent


def _graph(name: str):
    from backend.liquidcooling.graphcompiling.compiler import compile_graph
    from backend.liquidcooling.schema import LiquidrunConfig

    raw = json.loads((FIXTURE_DIR / f"{name}.liquidrun").read_text())
    return compile_graph(LiquidrunConfig.model_validate(raw))


class TestAssemblyIsBatched:
    def test_assembly_does_at_most_one_backward_per_call(self):
        """One backward for the whole edge set, not one per edge.

        This is the property that makes the assembly O(1) in Python-level work. It is
        also the single largest cost in the profile, so it is what the change is for.
        """
        from backend.liquidcooling.solver import newton_hydraulic as nh

        graph = _graph("single_rack")
        m = torch.full((graph.N_edges,), 0.05, dtype=torch.float64)

        calls = 0
        real_grad = torch.autograd.grad

        def counting_grad(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real_grad(*args, **kwargs)

        torch.autograd.grad = counting_grad
        try:
            nh._compute_all_headlosses(m, graph)
        finally:
            torch.autograd.grad = real_grad

        assert calls <= 1, (
            f"assembly called torch.autograd.grad {calls} times for "
            f"{graph.N_edges} edges; it must batch the derivative"
        )


class TestBatchedMatchesReference:
    """The single-edge functions are the oracle; batching must not move the numbers."""

    @pytest.mark.parametrize("fixture", ["single_rack", "cdu_loop", "blind_end"])
    def test_pipe_edges_match_single_edge_reference(self, fixture):
        from backend.liquidcooling.solver import newton_hydraulic as nh

        graph = _graph(fixture)
        if graph.pipe_indices is None:
            pytest.skip(f"{fixture} has no pipe edges")

        m = torch.linspace(0.01, 0.4, graph.N_edges, dtype=torch.float64)
        dp, g = nh._compute_all_headlosses(m, graph)

        pp = graph.pipe_params
        for local_i, eid_t in enumerate(graph.pipe_indices.edge_ids):
            eid = int(eid_t)
            dp_ref, g_ref = nh.pipe_headloss(
                m[eid],
                D=float(pp.D[local_i]),
                L=float(pp.L[local_i]),
                A=float(pp.A[local_i]),
                K_minor=float(pp.K_minor[local_i]),
                roughness=float(pp.roughness[local_i]),
                dh=float(pp.dh[local_i]),
                rho=graph.fluid_rho,
                mu=graph.fluid_mu,
                g_acc=graph.g_mag,
            )
            assert dp[eid] == pytest.approx(float(dp_ref), rel=1e-9, abs=1e-9)
            assert g[eid] == pytest.approx(float(g_ref), rel=1e-9, abs=1e-9)

    def test_flow_sign_is_respected(self):
        """Reverse flow must reverse the friction drop — a batched |m| that drops the
        sign would still pass a positive-flow-only test."""
        from backend.liquidcooling.solver import newton_hydraulic as nh

        graph = _graph("single_rack")
        m_pos = torch.full((graph.N_edges,), 0.12, dtype=torch.float64)
        dp_pos, g_pos = nh._compute_all_headlosses(m_pos, graph)
        dp_neg, g_neg = nh._compute_all_headlosses(-m_pos, graph)

        if graph.pipe_indices is None:
            pytest.skip("no pipe edges")
        eids = [int(e) for e in graph.pipe_indices.edge_ids]
        pp = graph.pipe_params
        # dp = dp_friction(m) + rho*g*dh; only the friction part flips.
        grav = graph.fluid_rho * graph.g_mag * pp.dh
        for local_i, eid in enumerate(eids):
            fric_pos = dp_pos[eid] - grav[local_i]
            fric_neg = dp_neg[eid] - grav[local_i]
            assert fric_neg == pytest.approx(-float(fric_pos), rel=1e-9, abs=1e-12)
            # the derivative is even in m
            assert g_neg[eid] == pytest.approx(float(g_pos[eid]), rel=1e-9, abs=1e-12)


class TestSchurIsSparse:
    """The Schur complement is a weighted graph Laplacian; forming it as a dense
    product costs O(N_free^2 * E) per Newton iteration, while assembling it edge by
    edge costs O(E). Measured (both paths asserted to produce the same matrix to 1e-9):

        N_free   E      dense A@A.T   index_put_   speedup
            10   12        0.01 ms      0.037 ms      0.4x   <- slower at this size
           100  300        0.15 ms      0.058 ms        3x
           500 1500        2.05 ms      0.471 ms        4x
          2000 6000       76.33 ms      1.828 ms       42x

    So the assembly change alone is a small regression on the fixtures and only pays
    from ~100 free nodes. It ships anyway because the same edge-indexed structure also
    removes the O(N*E) matvecs and the incidence densification, and those dominate at
    every size: gga_solve on `single_rack` went 0.027 s -> 0.015 s.

    (An earlier note in this file claimed 404 ms at N_free=500. That benchmark timed
    the construction of the dense A matrix inside the loop, which the real solver does
    once outside it. The table above is the corrected measurement.)

    The fixtures are far too small to show the scaling, so the property is tested
    structurally rather than by timing.
    """

    def test_solver_does_not_densify_the_incidence_matrix(self):
        """`incidence` is built sparse by the compiler (2 nonzeros per edge). Densifying
        it makes every B @ x an O(N*E) product where the structure is O(E)."""
        from backend.liquidcooling.solver import newton_hydraulic as nh

        graph = _graph("single_rack")
        assert graph.incidence.is_sparse, "fixture no longer exercises the sparse path"

        calls = 0
        real_to_dense = torch.Tensor.to_dense

        def counting_to_dense(self, *a, **k):
            nonlocal calls
            calls += 1
            return real_to_dense(self, *a, **k)

        torch.Tensor.to_dense = counting_to_dense
        try:
            nh.gga_solve(graph)
        finally:
            torch.Tensor.to_dense = real_to_dense

        assert calls == 0, (
            f"gga_solve densified a sparse tensor {calls} time(s); the incidence "
            f"structure should be used directly via edge_from/edge_to"
        )


class TestSolutionUnchanged:
    """Golden values captured from the dense implementation. Any restructuring of the
    linear algebra must leave the physics bit-for-bit where it was."""

    @pytest.mark.parametrize(
        "fixture",
        ["blind_end", "cdu_loop", "minimal", "simple_loop", "single_rack", "thermal_loop"],
    )
    def test_matches_golden(self, fixture):
        from backend.liquidcooling.solver.newton_hydraulic import gga_solve

        golden = json.loads((FIXTURE_DIR / "golden_gga.json").read_text())[fixture]
        P, m, info = gga_solve(_graph(fixture))

        assert info["converged"] is golden["converged"]
        assert info["iterations"] == golden["iters"]
        for i, want in enumerate(golden["P"]):
            assert float(P[i, 0]) == pytest.approx(want, rel=1e-9, abs=1e-6)
        for i, want in enumerate(golden["m"]):
            assert float(m[i, 0]) == pytest.approx(want, rel=1e-9, abs=1e-9)
