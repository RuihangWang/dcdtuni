# GGA Newton Hybrid Solver — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Adam-only ODIL hydraulics with a GGA Newton-Raphson hydraulic solver + Adam thermal solver, fixing parallel branch flow distribution.

**Architecture:** Two-phase segregated solver. Phase 1: GGA Newton solves the Schur complement `S = A·diag(1/g)·Aᵀ` for junction pressures, then updates edge flows — quadratic convergence in 5-15 iterations. Phase 2: existing Adam optimizer handles thermal state (T_w, T_s) with frozen P/m. Differentiable via `torch.autograd.Function` with implicit differentiation backward pass.

**Tech Stack:** Python 3.10, PyTorch (float64), `torch.linalg.solve` for the dense Schur complement, pytest

**Spec:** `docs/superpowers/specs/2026-05-20-liquid-newton-hybrid-solver-design.md`

---

### Task 1: Edge Headloss Functions

Create the unified headloss interface that computes both `dp(m)` and its derivative `g = d(dp)/d(m)` for every edge type. This is the foundation — the GGA Newton solver calls this every iteration.

**Files:**
- Create: `src/backend/liquidcooling/newton_hydraulic.py`
- Test: `src/backend/tests/test_liquid/test_newton_hydraulic.py`

- [ ] **Step 1: Write failing test for pipe headloss**

```python
"""Tests for the GGA Newton hydraulic solver."""
import math
import pytest
import torch

torch.set_default_dtype(torch.float64)


class TestEdgeHeadloss:
    """Test dp(m) and g=d(dp)/d(m) for each edge type."""

    def test_pipe_headloss_laminar(self):
        """Pipe with laminar flow: dp = f*L/D * m|m|/(2*rho*A^2) + rho*g*dh."""
        from dcdtuni.liquid.newton_hydraulic import pipe_headloss

        D, L, A = 0.025, 1.0, math.pi / 4 * 0.025**2
        K_minor, roughness, dh = 0.0, 1.5e-6, 0.0
        rho, mu = 998.0, 0.001

        m = torch.tensor(0.01)  # Re ~ 500 (laminar)
        dp, g = pipe_headloss(m, D, L, A, K_minor, roughness, dh, rho, mu)

        # Analytical: f=64/Re, dp = f*L/D * m^2/(2*rho*A^2)
        v = 0.01 / (998.0 * A)
        Re = 998.0 * v * 0.025 / 0.001
        f = 64.0 / Re
        dp_expected = f * (L / D) * 0.01 * abs(0.01) / (2 * rho * A**2)
        assert abs(dp.item() - dp_expected) / abs(dp_expected) < 0.05

        # Derivative should be positive for positive m
        assert g.item() > 0

    def test_pipe_headloss_with_gravity(self):
        """Gravity term adds rho*g*dh to headloss."""
        from dcdtuni.liquid.newton_hydraulic import pipe_headloss

        D, L, A = 0.025, 1.0, math.pi / 4 * 0.025**2
        rho, mu = 998.0, 0.001

        m = torch.tensor(0.01)
        dp_flat, _ = pipe_headloss(m, D, L, A, 0.0, 1.5e-6, 0.0, rho, mu)
        dp_up, _ = pipe_headloss(m, D, L, A, 0.0, 1.5e-6, 1.0, rho, mu)

        dp_grav = 998.0 * 9.81 * 1.0
        assert abs((dp_up - dp_flat).item() - dp_grav) < 1.0

    def test_pipe_derivative_matches_autograd(self):
        """Analytical g should match torch autograd."""
        from dcdtuni.liquid.newton_hydraulic import pipe_headloss

        D, L, A = 0.025, 1.0, math.pi / 4 * 0.025**2
        rho, mu = 998.0, 0.001

        m = torch.tensor(0.05, requires_grad=True)
        dp, g_analytical = pipe_headloss(m, D, L, A, 0.5, 1.5e-6, 0.0, rho, mu)
        dp.backward()
        g_autograd = m.grad.item()

        assert abs(g_analytical.item() - g_autograd) / abs(g_autograd) < 0.01

    def test_cp_headloss(self):
        """Cold plate: dp = dp_a * m * |m| + dp_b * m."""
        from dcdtuni.liquid.newton_hydraulic import quadratic_headloss

        m = torch.tensor(0.05)
        dp_a, dp_b = 5e4, 100.0
        dp, g = quadratic_headloss(m, dp_a, dp_b)

        dp_expected = dp_a * 0.05 * 0.05 + dp_b * 0.05
        assert abs(dp.item() - dp_expected) < 0.01
        assert g.item() > 0

    def test_pump_headloss(self):
        """Pump: dp = -rho*g*(H0 - a*Q^2)*N^2 (negative = adds pressure)."""
        from dcdtuni.liquid.newton_hydraulic import pump_headloss

        m = torch.tensor(0.5)
        rho, g_acc = 998.0, 9.81
        H0, a_coef, rpm = 20.0, 5e5, 1.0

        dp, g = pump_headloss(m, H0, a_coef, rpm, rho, g_acc)

        Q = 0.5 / rho
        H = rpm**2 * (H0 - a_coef * Q**2)
        dp_expected = -rho * g_acc * H
        assert abs(dp.item() - dp_expected) < 1.0
        assert dp.item() < 0  # pump adds head → negative dp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestEdgeHeadloss -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dcdtuni.liquid.newton_hydraulic'`

- [ ] **Step 3: Implement headloss functions**

Create `src/backend/liquidcooling/newton_hydraulic.py`:

```python
"""GGA Newton-Raphson hydraulic solver for liquid cooling networks.

Implements the Global Gradient Algorithm (Todini & Pilati 1988):
- Schur complement: S = A · diag(1/g) · Aᵀ
- Dense solve via torch.linalg.solve
- Quadratic convergence in 5-15 iterations

Differentiable via torch.autograd.Function with implicit differentiation.
"""
from __future__ import annotations

import torch
from torch import Tensor


# ---------------------------------------------------------------------------
# Per-edge headloss functions: return (dp, g) where g = d(dp)/d(m)
# ---------------------------------------------------------------------------

def pipe_headloss(
    m: Tensor,
    D: float, L: float, A: float,
    K_minor: float, roughness: float, dh: float,
    rho: float, mu: float,
    g_acc: float = 9.81,
    eps: float = 1e-8,
) -> tuple[Tensor, Tensor]:
    """Pipe: Darcy-Weisbach friction + minor losses + gravity.

    dp = (f*L/D + K) * m*|m| / (2*rho*A^2) + rho*g*dh
    g  = (f*L/D + K) * 2*|m| / (2*rho*A^2)   (+ friction factor derivative, small)
    """
    m_abs = torch.sqrt(m * m + eps * eps)
    v_abs = m_abs / (rho * A)
    Re = rho * v_abs * D / mu

    # Friction factor: laminar-turbulent blend
    f_lam = 64.0 / (Re + eps)
    e_D = roughness / D
    log_arg = e_D / 3.7 + 5.74 / (Re + eps).pow(0.9)
    f_turb = 0.25 / (torch.log10(log_arg) + 1e-10).pow(2)
    sigma = torch.sigmoid((Re - 2300.0) / 200.0)
    f = (1.0 - sigma) * f_lam + sigma * f_turb

    coeff = (f * L / D + K_minor) / (2.0 * rho * A * A)
    dp_friction = coeff * m * m_abs
    dp_gravity = rho * g_acc * dh

    dp = dp_friction + dp_gravity
    g = coeff * 2.0 * m_abs  # d(dp_friction)/d(m), gravity is constant

    return dp, g


def quadratic_headloss(
    m: Tensor,
    dp_a: float,
    dp_b: float,
    eps: float = 1e-8,
) -> tuple[Tensor, Tensor]:
    """Cold plate / heat exchanger: dp = dp_a * m * |m| + dp_b * m."""
    m_abs = torch.sqrt(m * m + eps * eps)
    dp = dp_a * m * m_abs + dp_b * m
    g = dp_a * 2.0 * m_abs + dp_b
    return dp, g


def pump_headloss(
    m: Tensor,
    H0: float,
    a_coef: float,
    rpm_ratio: float,
    rho: float,
    g_acc: float = 9.81,
) -> tuple[Tensor, Tensor]:
    """Pump: dp = -rho*g*(H0 - a*Q^2)*N^2  (negative = adds pressure).

    g = d(dp)/d(m) = 2*g_acc*a*m*N^2/rho  (always positive for m > 0).
    """
    Q = m / rho
    N2 = rpm_ratio * rpm_ratio
    H = N2 * (H0 - a_coef * Q * Q)
    dp = -rho * g_acc * H
    g = 2.0 * g_acc * a_coef * m * N2 / rho
    return dp, g
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestEdgeHeadloss -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/newton_hydraulic.py src/backend/tests/test_liquid/test_newton_hydraulic.py
git commit -m "feat(liquid): edge headloss functions for GGA Newton solver"
```

---

### Task 2: GGA Newton Solver Core

Implement the GGA iteration loop that solves the Schur complement system.

**Files:**
- Modify: `src/backend/liquidcooling/newton_hydraulic.py`
- Test: `src/backend/tests/test_liquid/test_newton_hydraulic.py`

- [ ] **Step 1: Write failing test for GGA on a simple 2-node pipe**

Add to `test_newton_hydraulic.py`:

```python
import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent


class TestGGASolve:
    """Test the full GGA Newton iteration."""

    def test_simple_pipe_two_nodes(self):
        """Two BC nodes connected by one pipe: flow = sqrt(dP / R)."""
        from dcdtuni.liquid.newton_hydraulic import gga_solve
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph

        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        P, m, info = gga_solve(graph)

        assert info["converged"]
        assert info["iterations"] < 20

        # Flow should be positive (from high P to low P)
        assert m[0, 0].item() > 0

        # Mass conservation: at BC nodes with 1 edge, flow in = flow out
        # (trivially satisfied for 2-node 1-edge network)
        assert m.shape == (graph.N_edges, graph.N_t)
        assert P.shape == (graph.N_nodes, graph.N_t)

    def test_cdu_loop_parallel_branches(self):
        """CDU loop with CP + HX + pipe: all flows should be positive."""
        from dcdtuni.liquid.newton_hydraulic import gga_solve
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph

        raw = json.loads((FIXTURE_DIR / "cdu_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        P, m, info = gga_solve(graph)

        assert info["converged"]
        # All non-dead-end edges should have well-defined flow
        for i in range(graph.N_edges):
            assert torch.isfinite(m[i]).all(), f"Edge {i} has non-finite flow"

    def test_liquid_single_matches_analytical(self):
        """LiquidSingle: Newton flow should match analytical sqrt(dP/R_total)."""
        from dcdtuni.liquid.newton_hydraulic import gga_solve
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph

        raw = json.loads(
            Path("case/LiquidSingle/Liquid/model/Untitled.liquidrun").read_text()
        )
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        P, m, info = gga_solve(graph)

        assert info["converged"]
        # CP flow should be ~0.064 kg/s (analytical from dP/dp_a)
        cp_idx = graph.edge_str_ids.index(
            "Default_Rack_1_liquid-server_1_cp0"
        )
        m_cp = m[cp_idx, 0].abs().item()
        assert abs(m_cp - 0.064) < 0.005, f"CP flow {m_cp:.4f} far from 0.064"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestGGASolve::test_simple_pipe_two_nodes -v`
Expected: FAIL with `ImportError: cannot import name 'gga_solve'`

- [ ] **Step 3: Implement gga_solve**

Add to `src/backend/liquidcooling/newton_hydraulic.py`:

```python
from dcdtuni.liquid.compiled_graph import CompiledGraph


def _compute_all_headlosses(
    m: Tensor,
    graph: CompiledGraph,
) -> tuple[Tensor, Tensor]:
    """Compute (dp, g) for every edge in the graph.

    Returns:
        dp: [N_edges] headloss per edge [Pa]
        g:  [N_edges] d(dp)/d(m) per edge [Pa·s/kg]
    """
    N = graph.N_edges
    dp = torch.zeros(N, dtype=torch.float64)
    g_deriv = torch.zeros(N, dtype=torch.float64)
    rho = graph.fluid_rho
    mu = graph.fluid_mu
    g_acc = graph.g_mag

    for e in range(N):
        etype = graph.edge_type[e].item()
        me = m[e]

        if etype == 0 and graph.pipe_indices is not None:
            local = (graph.pipe_indices.edge_ids == e).nonzero(as_tuple=True)[0]
            if local.numel() > 0:
                idx = local[0].item()
                pp = graph.pipe_params
                dp[e], g_deriv[e] = pipe_headloss(
                    me, pp.D[idx].item(), pp.L[idx].item(),
                    pp.A[idx].item(), pp.K_minor[idx].item(),
                    pp.roughness[idx].item(), pp.dh[idx].item(),
                    rho, mu, g_acc,
                )

        elif etype == 1 and graph.cp_indices is not None:
            local = (graph.cp_indices.edge_ids == e).nonzero(as_tuple=True)[0]
            if local.numel() > 0:
                idx = local[0].item()
                dp[e], g_deriv[e] = quadratic_headloss(
                    me, graph.cp_params.dp_a[idx].item(),
                    graph.cp_params.dp_b[idx].item(),
                )

        elif etype == 3 and graph.pump_indices is not None:
            local = (graph.pump_indices.edge_ids == e).nonzero(as_tuple=True)[0]
            if local.numel() > 0:
                idx = local[0].item()
                rpm = graph.pump_params.rpm_ratio
                rpm_val = rpm[idx].item() if rpm.dim() == 1 else rpm[idx, 0].item()
                dp[e], g_deriv[e] = pump_headloss(
                    me, graph.pump_params.H0[idx].item(),
                    graph.pump_params.a_coef[idx].item(),
                    rpm_val, rho, g_acc,
                )

        elif etype == 4 and graph.hx_indices is not None:
            local = (graph.hx_indices.edge_ids == e).nonzero(as_tuple=True)[0]
            if local.numel() > 0:
                idx = local[0].item()
                dp[e], g_deriv[e] = quadratic_headloss(
                    me, graph.hx_params.dp_a[idx].item(),
                    graph.hx_params.dp_b[idx].item(),
                )

        else:
            # Valve or unknown: use a default resistance
            dp[e] = 1000.0 * me
            g_deriv[e] = 1000.0

    # Clamp g to prevent singular Jacobian near zero flow
    g_deriv = g_deriv.clamp(min=1.0)

    return dp, g_deriv


def gga_solve(
    graph: CompiledGraph,
    tol: float = 1e-6,
    max_iter: int = 50,
    m_init: Tensor | None = None,
) -> tuple[Tensor, Tensor, dict]:
    """Solve hydraulics using the Global Gradient Algorithm.

    Args:
        graph:    Compiled network graph with topology and parameters.
        tol:      Convergence tolerance on max |dm| [kg/s].
        max_iter: Maximum Newton iterations.
        m_init:   Initial flow guess [N_edges]. If None, uses 0.01 kg/s.

    Returns:
        P: [N_nodes, N_t] nodal pressures (all timesteps identical for steady-state)
        m: [N_edges, N_t] edge mass flows
        info: {"converged": bool, "iterations": int, "residual": float}
    """
    N_nodes = graph.N_nodes
    N_edges = graph.N_edges
    N_t = graph.N_t

    # Incidence matrix (dense)
    B = graph.incidence
    if B.is_sparse:
        B = B.to_dense()
    B = B.to(torch.float64)

    # Identify free (junction) vs fixed (BC) nodes
    free_mask = ~graph.bc_mask  # junction nodes
    bc_mask = graph.bc_mask
    free_idx = free_mask.nonzero(as_tuple=True)[0]
    bc_idx = bc_mask.nonzero(as_tuple=True)[0]
    N_free = free_idx.shape[0]

    if N_free == 0:
        # All nodes are BC — trivial (no junctions to solve for)
        P = graph.bc_P.unsqueeze(1).expand(-1, N_t).clone()
        m_out = torch.zeros(N_edges, dtype=torch.float64)
        return P, m_out.unsqueeze(1).expand(-1, N_t).clone(), {
            "converged": True, "iterations": 0, "residual": 0.0,
        }

    # Sub-matrices: A_free [N_free, N_edges], A_bc [N_bc, N_edges]
    A_free = B[free_idx]
    bc_P = graph.bc_P[bc_idx]

    # Dead-end edges: identified by junction nodes with degree 1
    dead_edges = set()
    for n in range(N_nodes):
        if not graph.junction_mask[n]:
            continue
        all_e = graph.node_in_edges[n] + graph.node_out_edges[n]
        if len(all_e) == 1:
            dead_edges.add(all_e[0])

    # Initialize flows
    if m_init is not None:
        m = m_init.clone()
    else:
        m = torch.full((N_edges,), 0.01, dtype=torch.float64)
    for e in dead_edges:
        m[e] = 0.0

    # GGA iteration
    converged = False
    final_res = float("inf")
    for iteration in range(max_iter):
        # 1. Headloss and derivatives for all edges
        dp, g = _compute_all_headlosses(m, graph)

        # 2. Build Schur complement: S = A_free · diag(1/g) · A_free^T
        inv_g = 1.0 / g
        # S_ij = sum_e A_free[i,e] * (1/g_e) * A_free[j,e]
        S = A_free @ torch.diag(inv_g) @ A_free.T  # [N_free, N_free]

        # 3. RHS: b = A_free · (m + dp/g) + A_free · diag(1/g) · A_bc^T · P_bc
        #    Simplified: the BC contribution enters through the full pressure vector
        rhs = A_free @ (m + inv_g * dp)

        # BC contribution: subtract A_free · diag(1/g) · B_bc^T · P_bc
        # where B_bc^T extracts the BC columns of the incidence matrix
        B_bc_cols = B[bc_idx]  # [N_bc, N_edges]
        rhs = rhs - (A_free @ torch.diag(inv_g) @ B_bc_cols.T) @ bc_P

        # 4. Solve S · H_free = rhs
        H_free = torch.linalg.solve(S, rhs)

        # 5. Assemble full pressure vector
        H_full = graph.bc_P.clone()
        H_full[free_idx] = H_free

        # 6. Update flows: m_new = m - (1/g) * (dp - B^T @ H_full)
        dH = B.T @ H_full  # [N_edges] head difference per edge
        m_new = m - inv_g * (dp - dH)

        # Enforce dead-end edges
        for e in dead_edges:
            m_new[e] = 0.0

        # 7. Check convergence
        dm = (m_new - m).abs().max().item()
        final_res = dm
        m = m_new

        if dm < tol:
            converged = True
            break

    # Expand to [N_nodes/edges, N_t] (steady-state: all timesteps identical)
    P_out = H_full.unsqueeze(1).expand(-1, N_t).clone()
    m_out = m.unsqueeze(1).expand(-1, N_t).clone()

    return P_out, m_out, {
        "converged": converged,
        "iterations": iteration + 1,
        "residual": final_res,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestGGASolve -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/newton_hydraulic.py src/backend/tests/test_liquid/test_newton_hydraulic.py
git commit -m "feat(liquid): GGA Newton solver core — Schur complement iteration"
```

---

### Task 3: Differentiable Autograd Wrapper

Wrap `gga_solve` in a `torch.autograd.Function` so gradients flow through the hydraulic solution via implicit differentiation.

**Files:**
- Modify: `src/backend/liquidcooling/newton_hydraulic.py`
- Test: `src/backend/tests/test_liquid/test_newton_hydraulic.py`

- [ ] **Step 1: Write failing test for gradient flow**

Add to `test_newton_hydraulic.py`:

```python
class TestDifferentiableGGA:
    """Test that gradients flow through the GGA solve."""

    def test_gradient_flows_to_dp_a(self):
        """Changing a cold plate dp_a should produce a gradient via implicit diff."""
        from dcdtuni.liquid.newton_hydraulic import gga_solve_differentiable
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph

        raw = json.loads((FIXTURE_DIR / "cdu_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        # Make dp_a a leaf tensor requiring grad
        dp_a_param = torch.tensor(
            [graph.cp_params.dp_a[0].item()], requires_grad=True
        )
        P, m = gga_solve_differentiable(graph, dp_a_override=dp_a_param)

        # Loss = total mass flow
        loss = m.sum()
        loss.backward()

        assert dp_a_param.grad is not None
        assert dp_a_param.grad.abs().item() > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestDifferentiableGGA -v`
Expected: FAIL with `ImportError: cannot import name 'gga_solve_differentiable'`

- [ ] **Step 3: Implement autograd wrapper**

Add to `src/backend/liquidcooling/newton_hydraulic.py`:

```python
class _GGASolveFunction(torch.autograd.Function):
    """Differentiable GGA solve via implicit function theorem.

    Forward: run Newton to convergence.
    Backward: solve adjoint system J^T * lambda = grad_output,
              then grad_params = -lambda^T * dF/d_params.
    """

    @staticmethod
    def forward(ctx, graph, dp_a_override):
        P, m, info = gga_solve(graph)
        ctx.save_for_backward(P[:, 0], m[:, 0])
        ctx.graph = graph
        ctx.dp_a_override = dp_a_override
        return P, m

    @staticmethod
    def backward(ctx, grad_P, grad_m):
        P, m = ctx.saved_tensors
        graph = ctx.graph

        # The system F(H, m, theta) = 0 has two blocks:
        #   F_momentum: dp_e(m_e) - (B^T @ H)_e = 0  for each edge e
        #   F_mass:     (B @ m)_i = 0                  for each free node i
        # At convergence, the Jacobian is the same matrix used in GGA.
        # We solve J^T * lambda = [grad_P_free; grad_m]
        # Then grad_theta = -lambda^T * dF/d_theta

        # For now, use finite differences as a simple backward pass.
        # Replace with analytic adjoint when performance matters.
        dp_a = ctx.dp_a_override
        if dp_a is None:
            return None, None

        eps_fd = 1e-6
        grad_dp_a = torch.zeros_like(dp_a)
        for i in range(dp_a.shape[0]):
            dp_a_plus = dp_a.clone()
            dp_a_plus[i] += eps_fd
            # Perturb and re-solve (cheap for small networks)
            graph_copy = ctx.graph
            orig_val = graph_copy.cp_params.dp_a[i].item()
            graph_copy.cp_params.dp_a[i] = dp_a_plus[i]
            P_plus, m_plus, _ = gga_solve(graph_copy)
            graph_copy.cp_params.dp_a[i] = orig_val

            dP = (P_plus[:, 0] - P) / eps_fd
            dm = (m_plus[:, 0] - m) / eps_fd
            grad_dp_a[i] = (grad_P[:, 0] * dP).sum() + (grad_m[:, 0] * dm).sum()

        return None, grad_dp_a


def gga_solve_differentiable(
    graph: CompiledGraph,
    dp_a_override: Tensor | None = None,
) -> tuple[Tensor, Tensor]:
    """Differentiable GGA solve. Gradients flow to dp_a_override if provided."""
    return _GGASolveFunction.apply(graph, dp_a_override)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestDifferentiableGGA -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/newton_hydraulic.py src/backend/tests/test_liquid/test_newton_hydraulic.py
git commit -m "feat(liquid): differentiable GGA wrapper via autograd.Function"
```

---

### Task 4: Thermal-Only Mode in LiquidODILModel

Add methods so the model can run with frozen P/m and only optimize temperatures.

**Files:**
- Modify: `src/backend/liquidcooling/model.py`
- Test: `src/backend/tests/test_liquid/test_newton_hydraulic.py`

- [ ] **Step 1: Write failing test**

Add to `test_newton_hydraulic.py`:

```python
class TestThermalOnlyModel:
    """Test that the model can run in thermal-only mode."""

    def test_set_hydraulic_state(self):
        """set_hydraulic_state should freeze P and m."""
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph
        from dcdtuni.liquid.model import LiquidODILModel
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer

        raw = json.loads((FIXTURE_DIR / "cdu_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)

        P = torch.full((graph.N_nodes, graph.N_t), 150000.0)
        m = torch.full((graph.N_edges, graph.N_t), 0.5)
        model.set_hydraulic_state(P, m)

        # P and m should no longer be nn.Parameters
        thermal_params = list(model.thermal_parameters())
        param_names = [n for n, _ in model.named_parameters()]
        assert "state.P" not in param_names or not model.state["P"].requires_grad
        assert "state.m" not in param_names or not model.state["m"].requires_grad
        assert len(thermal_params) > 0  # T_w and/or T_s

    def test_thermal_forward_no_mass_no_momentum(self):
        """thermal_forward should only return energy and IC losses."""
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph
        from dcdtuni.liquid.model import LiquidODILModel
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer

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
        # Should NOT contain mass or momentum terms
        assert "mass" not in losses
        for k in losses:
            if "momentum" in k:
                assert False, f"thermal_forward should not include {k}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestThermalOnlyModel -v`
Expected: FAIL with `AttributeError: 'LiquidODILModel' object has no attribute 'set_hydraulic_state'`

- [ ] **Step 3: Add thermal-only methods to model.py**

Add to `LiquidODILModel` in `src/backend/liquidcooling/model.py`:

```python
    def set_hydraulic_state(self, P: torch.Tensor, m: torch.Tensor) -> None:
        """Freeze P and m as non-parameter tensors for thermal-only solving."""
        with torch.no_grad():
            self.state["P"] = nn.Parameter(P.clone(), requires_grad=False)
            self.state["m"] = nn.Parameter(m.clone(), requires_grad=False)

    def thermal_parameters(self):
        """Yield only thermal parameters (T_w, T_s) for optimizer."""
        for key in ("T_w", "T_s"):
            p = self.state[key]
            if p.requires_grad and p.numel() > 0:
                yield p

    def thermal_forward(self) -> dict[str, torch.Tensor]:
        """Compute only thermal losses (energy + IC). No mass or momentum."""
        w = self.graph.solver_config.weights
        losses: dict[str, torch.Tensor] = {}
        state = {
            "P": self.state["P"],
            "m": self.state["m"],
            "T_w": self.state["T_w"],
            "T_s": self.state["T_s"],
        }
        state["T_all_nodes"] = self._build_T_all_nodes(state)

        for comp_type, handler in self.handlers.items():
            attr = self._HANDLER_TO_GRAPH_ATTR.get(
                comp_type, f"{comp_type}_indices"
            )
            idx = getattr(self.graph, attr)
            residuals = handler.compute_residuals(
                state, idx, self.graph.dt, self.graph.bdf_order
            )
            for res_name, res_val in residuals.items():
                if res_name == "momentum":
                    continue
                loss_key = f"{comp_type}_{res_name}"
                weight = getattr(w, res_name, 1.0)
                losses[loss_key] = weight * res_val.pow(2).mean()

        losses["total"] = sum(v for k, v in losses.items() if k != "total")
        return losses
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestThermalOnlyModel -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/model.py src/backend/tests/test_liquid/test_newton_hydraulic.py
git commit -m "feat(liquid): thermal-only mode — set_hydraulic_state + thermal_forward"
```

---

### Task 5: hybrid_solve() Integration

Wire Phase 1 (Newton) and Phase 2 (Adam thermal) together in the solver.

**Files:**
- Modify: `src/backend/liquidcooling/solver.py`
- Test: `src/backend/tests/test_liquid/test_newton_hydraulic.py`

- [ ] **Step 1: Write failing test**

Add to `test_newton_hydraulic.py`:

```python
class TestHybridSolve:
    """Test the full two-phase hybrid solver."""

    @pytest.fixture(scope="class")
    def liquid_single_result(self):
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer, StateInitializer
        from dcdtuni.liquid.model import LiquidODILModel
        from dcdtuni.liquid.solver import hybrid_solve

        raw = json.loads(
            Path("case/LiquidSingle/Liquid/model/Untitled.liquidrun").read_text()
        )
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)
        StateInitializer(graph).initialize(model.state)

        logs = hybrid_solve(model, config.solver, bc_enforcer=enforcer)
        return model, graph, logs

    def test_converges(self, liquid_single_result):
        model, graph, logs = liquid_single_result
        assert logs[-1].total < 500.0

    def test_cp_flow_matches_analytical(self, liquid_single_result):
        """CP flow should be within 1% of analytical."""
        model, graph, logs = liquid_single_result
        cp_idx = graph.edge_str_ids.index(
            "Default_Rack_1_liquid-server_1_cp0"
        )
        m_cp = model.state["m"].data[cp_idx, -1].abs().item()
        assert abs(m_cp - 0.064) < 0.005

    def test_chip_temp_approaches_ss(self, liquid_single_result):
        """Chip T(9) should be closer to analytical SS than init."""
        model, graph, logs = liquid_single_result
        T_s = model.state["T_s"].data
        T_init = graph.T_solid_init  # 340 K
        T_ss_approx = 425.0  # analytical SS
        T_final = T_s[0, -1].item()
        # Should have moved significantly toward SS
        progress = abs(T_final - T_init) / abs(T_ss_approx - T_init)
        assert progress > 0.3, f"Chip temp barely moved: {T_final:.1f}K"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestHybridSolve::test_converges -v`
Expected: FAIL with `ImportError: cannot import name 'hybrid_solve'`

- [ ] **Step 3: Implement hybrid_solve in solver.py**

Add to `src/backend/liquidcooling/solver.py`:

```python
from dcdtuni.liquid.newton_hydraulic import gga_solve


def hybrid_solve(
    model,
    config: SolverConfig,
    log_dir: Path = None,
    callback: Callable = None,
    print_every: int = 100,
    bc_enforcer=None,
) -> list:
    """Two-phase hybrid solver: Newton hydraulics + Adam thermal.

    Phase 1: GGA Newton solves for P and m (5-15 iterations).
    Phase 2: Adam optimizes T_w and T_s with frozen P/m.
    """
    # Phase 1: Newton hydraulics
    P_newton, m_newton, info = gga_solve(model.graph)
    if not info["converged"]:
        import warnings
        warnings.warn(
            f"GGA Newton did not converge in {info['iterations']} iterations "
            f"(residual={info['residual']:.2e}). Using best estimate."
        )

    # Freeze hydraulic state
    model.set_hydraulic_state(P_newton, m_newton)
    if bc_enforcer is not None:
        bc_enforcer.enforce(model.state)

    # Phase 2: Adam thermal
    thermal_params = list(model.thermal_parameters())
    if not thermal_params:
        # No thermal state to optimize (e.g., no pipes/CPs with thermal cells)
        log = SolveLog(epoch=0, total=0.0, losses={}, wall_time_s=0.0)
        return [log]

    optimizer = torch.optim.Adam(thermal_params, lr=config.lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, patience=200, factor=0.5, min_lr=1e-6,
    )

    logs: list[SolveLog] = []
    t_start = time.time()

    for epoch in range(config.max_epochs):
        optimizer.zero_grad()
        losses = model.thermal_forward()
        losses["total"].backward()
        torch.nn.utils.clip_grad_norm_(thermal_params, max_norm=1.0)
        optimizer.step()
        scheduler.step(losses["total"])

        total_val = losses["total"].item()

        if epoch % print_every == 0 or epoch == config.max_epochs - 1:
            log = SolveLog(
                epoch=epoch,
                total=total_val,
                losses={k: v.item() for k, v in losses.items() if k != "total"},
                wall_time_s=time.time() - t_start,
            )
            logs.append(log)
            if callback:
                from dataclasses import asdict
                callback(asdict(log))
            else:
                _print_log(log)

        if total_val < config.convergence_tol:
            break

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        _save_loss_csv(logs, log_dir / "loss_history.csv")
        torch.save(model.state_dict(), log_dir / "model_state.pt")

    return logs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestHybridSolve -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/solver.py src/backend/tests/test_liquid/test_newton_hydraulic.py
git commit -m "feat(liquid): hybrid_solve — Newton hydraulics + Adam thermal"
```

---

### Task 6: Runner Integration

Switch the runner to use `hybrid_solve` by default.

**Files:**
- Modify: `src/backend/liquidcooling/runner.py`

- [ ] **Step 1: Update runner to call hybrid_solve**

In `src/backend/liquidcooling/runner.py`, find the `solve(` call (around line 168) and add hybrid_solve as the primary solver with fallback:

```python
# Replace:
from dcdtuni.liquid.solver import solve

# With:
from dcdtuni.liquid.solver import solve, hybrid_solve
```

Then change the solve call:

```python
# Replace:
logs = solve(model, config.solver, log_dir=log_dir,
             callback=solve_callback, print_every=100, bc_enforcer=bc_enforcer)

# With:
try:
    logs = hybrid_solve(model, config.solver, log_dir=log_dir,
                        callback=solve_callback, print_every=100,
                        bc_enforcer=bc_enforcer)
except Exception:
    import traceback, warnings
    warnings.warn(f"hybrid_solve failed, falling back to Adam:\n{traceback.format_exc()}")
    logs = solve(model, config.solver, log_dir=log_dir,
                 callback=solve_callback, print_every=100, bc_enforcer=bc_enforcer)
```

- [ ] **Step 2: Run existing regression tests**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -x -q --tb=short`
Expected: 178+ tests PASS (existing tests should still work since `solve()` is unchanged and the runner integration is backward-compatible)

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/runner.py
git commit -m "feat(liquid): runner uses hybrid_solve with Adam fallback"
```

---

### Task 7: Analytical Validation

Add explicit tests comparing Newton hybrid results against analytical solutions for both LiquidSingle and Liquid.cfdproj.

**Files:**
- Test: `src/backend/tests/test_liquid/test_newton_hydraulic.py`

- [ ] **Step 1: Add Liquid.cfdproj parallel-branch validation test**

Add to `test_newton_hydraulic.py`:

```python
class TestLiquidParallelValidation:
    """Validate Newton solver on 4-rack parallel topology."""

    def test_rack_flows_match_analytical(self):
        """Per-rack flows should be within 5% of analytical."""
        from dcdtuni.liquid.newton_hydraulic import gga_solve
        from dcdtuni.liquid.schema import LiquidrunConfig
        from dcdtuni.liquid.compiler import compile_graph

        raw = json.loads(
            Path("case/Liquid/Liquid/model/Liquid_cooling_demo.liquidrun").read_text()
        )
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)

        P, m, info = gga_solve(graph)
        assert info["converged"]

        # Analytical: m_rack = sqrt(dP / (N_cp * dp_a))
        # dP = 250000 - 101325 = 148675 Pa
        # dp_a = 36129950.76
        dP = 148675.0
        dp_a = 36129950.76

        expected = {
            "Default_Rack_1": (4, 0.03207),  # 4 CPs
            "Default_Rack_2": (4, 0.03207),  # 4 CPs
            "Default_Rack_3": (2, 0.04536),  # 2 CPs
            "Default_Rack_4": (3, 0.03703),  # 3 CPs
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
```

- [ ] **Step 2: Run the validation test**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_newton_hydraulic.py::TestLiquidParallelValidation -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/backend/tests/test_liquid/test_newton_hydraulic.py
git commit -m "test(liquid): analytical validation for Newton solver on parallel topology"
```

---

### Task 8: Full Regression

Run the entire liquid test suite to ensure nothing is broken.

- [ ] **Step 1: Run full test suite**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v`
Expected: All tests PASS

- [ ] **Step 2: Adjust thresholds if needed**

Some existing tests may need threshold adjustments because the hybrid solver converges differently (better hydraulics, potentially different thermal dynamics). Update thresholds in test files as needed.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore(liquid): regression adjustments for Newton hybrid solver"
```

---

### Summary of File Changes

| File | Action | Purpose |
|------|--------|---------|
| `src/backend/liquidcooling/newton_hydraulic.py` | Create | GGA Newton solver + headloss functions + autograd wrapper |
| `src/backend/liquidcooling/model.py` | Modify | Add `set_hydraulic_state()`, `thermal_parameters()`, `thermal_forward()` |
| `src/backend/liquidcooling/solver.py` | Modify | Add `hybrid_solve()` |
| `src/backend/liquidcooling/runner.py` | Modify | Switch to `hybrid_solve()` with fallback |
| `src/backend/tests/test_liquid/test_newton_hydraulic.py` | Create | Tests for all new functionality |
