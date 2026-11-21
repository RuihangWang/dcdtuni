# Liquid Solver BC/IC Management Module — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract boundary condition enforcement, initial condition handling, and state initialization from scattered locations into a dedicated `bc_ic.py` module with hard-clamping, topology-aware smart init, and aggregate monitoring.

**Architecture:** New `bc_ic.py` with three classes — `BoundaryConditionEnforcer` (hard clamp + soft loss), `StateInitializer` (BFS pressure/flow propagation), `BCICMonitor` (aggregate total_bc/total_ic reporting). Integrates with existing model/solver/runner via dependency injection.

**Tech Stack:** Python 3.10, PyTorch (float64 tensors), Pydantic v2, pytest

**Spec:** `docs/superpowers/specs/2026-05-19-liquid-bc-ic-management-design.md`

---

### Task 1: BoundaryConditionEnforcer — enforce() hard clamp

**Files:**
- Create: `src/backend/liquidcooling/bc_ic.py`
- Test: `src/backend/tests/test_liquid/test_bc_ic.py`

- [ ] **Step 1: Write the failing test for enforce()**

```python
# src/backend/tests/test_liquid/test_bc_ic.py
"""Tests for the bc_ic module: BoundaryConditionEnforcer, StateInitializer, BCICMonitor."""
import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiler import compile_graph

FIXTURE_DIR = Path(__file__).parent


def _make_graph(fixture_name="minimal.liquidrun"):
    raw = json.loads((FIXTURE_DIR / fixture_name).read_text())
    config = LiquidrunConfig.model_validate(raw)
    return compile_graph(config), config


class TestBoundaryConditionEnforcer:

    def test_enforce_clamps_P(self):
        """After enforce(), BC node pressures equal their target values."""
        graph, _ = _make_graph()
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)

        # Create fake state with wrong pressures at BC nodes
        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), 999999.0)),
            "m": nn.Parameter(torch.ones(graph.N_edges, graph.N_t)),
        }
        enforcer.enforce(state)

        # BC nodes must now match targets
        for i in range(graph.N_nodes):
            if graph.bc_mask[i]:
                assert torch.allclose(
                    state["P"].data[i, :],
                    torch.full((graph.N_t,), graph.bc_P[i].item()),
                ), f"Node {i}: P={state['P'].data[i,0].item():.1f}, expected={graph.bc_P[i].item():.1f}"

        # Non-BC nodes should be unchanged (still 999999.0)
        for i in range(graph.N_nodes):
            if not graph.bc_mask[i]:
                assert state["P"].data[i, 0].item() == pytest.approx(999999.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestBoundaryConditionEnforcer::test_enforce_clamps_P -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dcdtuni.liquid.bc_ic'`

- [ ] **Step 3: Write BoundaryConditionEnforcer with enforce()**

```python
# src/backend/liquidcooling/bc_ic.py
"""
BC/IC management for the liquid cooling solver.

Three classes:
- BoundaryConditionEnforcer: hard-clamp BCs after each solver step + soft loss
- StateInitializer: topology-aware BFS initialization
- BCICMonitor: aggregate BC/IC metrics for SSE reporting
"""
from __future__ import annotations

from collections import deque

import torch
import torch.nn as nn
from torch import Tensor

from dcdtuni.liquid.compiled_graph import CompiledGraph


class BoundaryConditionEnforcer:
    """Hard-clamp boundary conditions and compute soft BC loss."""

    def __init__(self, graph: CompiledGraph) -> None:
        self.bc_mask = graph.bc_mask                    # [N_nodes] bool
        self.bc_P = graph.bc_P                          # [N_nodes]
        self.bc_T = graph.bc_T                          # [N_nodes]
        self.bc_T_mask = graph.bc_T_mask                # [N_nodes] bool
        self.N_t = graph.N_t

        # Dead-end detection: junction nodes with exactly 1 connected edge
        dead_end_edge_set: set[int] = set()
        for n_idx in range(graph.N_nodes):
            if not graph.junction_mask[n_idx].item():
                continue
            all_edges = graph.node_in_edges[n_idx] + graph.node_out_edges[n_idx]
            if len(all_edges) == 1:
                dead_end_edge_set.add(all_edges[0])
        self.dead_end_edge_mask = torch.zeros(graph.N_edges, dtype=torch.bool)
        for e_idx in dead_end_edge_set:
            self.dead_end_edge_mask[e_idx] = True

    def to(self, device) -> BoundaryConditionEnforcer:
        """Move tensors to device (called when model moves to GPU)."""
        self.bc_mask = self.bc_mask.to(device)
        self.bc_P = self.bc_P.to(device)
        self.bc_T = self.bc_T.to(device)
        self.bc_T_mask = self.bc_T_mask.to(device)
        self.dead_end_edge_mask = self.dead_end_edge_mask.to(device)
        return self

    def enforce(self, state: dict[str, nn.Parameter]) -> None:
        """Hard-clamp P at BC nodes and zero m on dead-end edges."""
        with torch.no_grad():
            bc_idx = self.bc_mask.nonzero(as_tuple=True)[0]
            if bc_idx.numel() > 0:
                state["P"].data[bc_idx] = self.bc_P[bc_idx].unsqueeze(1).expand(-1, self.N_t)
            if self.dead_end_edge_mask.any():
                dead_idx = self.dead_end_edge_mask.nonzero(as_tuple=True)[0]
                state["m"].data[dead_idx] = 0.0

    def loss(self, state: dict[str, nn.Parameter], w_bc: float) -> dict[str, Tensor]:
        """Soft BC residual losses for gradient signal."""
        losses = {}
        P_bc_err = state["P"][self.bc_mask] - self.bc_P[self.bc_mask].unsqueeze(1)
        losses["bc_P"] = w_bc * P_bc_err.pow(2).mean()
        if self.dead_end_edge_mask.any():
            m_dead = state["m"][self.dead_end_edge_mask]
            losses["bc_dead"] = w_bc * m_dead.pow(2).mean()
        return losses
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestBoundaryConditionEnforcer::test_enforce_clamps_P -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/bc_ic.py src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "feat(liquid): BoundaryConditionEnforcer with enforce() hard clamp"
```

---

### Task 2: BoundaryConditionEnforcer — dead-end zeroing + loss()

**Files:**
- Modify: `src/backend/liquidcooling/bc_ic.py` (already created)
- Modify: `src/backend/tests/test_liquid/test_bc_ic.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_bc_ic.py inside TestBoundaryConditionEnforcer class:

    def test_enforce_zeros_dead_ends(self):
        """Dead-end edge flows are zeroed by enforce()."""
        graph, _ = _make_graph("blind_end.liquidrun")
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)
        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), 150000.0)),
            "m": nn.Parameter(torch.ones(graph.N_edges, graph.N_t) * 5.0),
        }
        enforcer.enforce(state)

        # Dead-end edges should be zero
        for e_idx in range(graph.N_edges):
            if enforcer.dead_end_edge_mask[e_idx]:
                assert state["m"].data[e_idx, 0].item() == pytest.approx(0.0), (
                    f"Dead-end edge {e_idx} should be zero, got {state['m'].data[e_idx, 0].item()}"
                )

    def test_loss_returns_bc_P(self):
        """loss() returns bc_P key with positive scalar."""
        graph, _ = _make_graph()
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)
        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), 999999.0)),
            "m": nn.Parameter(torch.ones(graph.N_edges, graph.N_t)),
        }
        losses = enforcer.loss(state, w_bc=100.0)
        assert "bc_P" in losses
        assert losses["bc_P"].item() > 0
        assert losses["bc_P"].dim() == 0  # scalar
```

- [ ] **Step 2: Run tests to verify they fail (or pass if implementation from Task 1 already covers them)**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestBoundaryConditionEnforcer -v`
Expected: All 3 tests PASS (enforce and loss were both implemented in Task 1)

- [ ] **Step 3: Commit**

```bash
git add src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "test(liquid): dead-end zeroing and loss() tests for BoundaryConditionEnforcer"
```

---

### Task 3: StateInitializer — topology-aware BFS init

**Files:**
- Modify: `src/backend/liquidcooling/bc_ic.py`
- Modify: `src/backend/tests/test_liquid/test_bc_ic.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to test_bc_ic.py:

class TestStateInitializer:

    def test_flow_directions_correct(self):
        """Supply→return flows should be positive (from-node pressure > to-node pressure)."""
        graph, _ = _make_graph()
        from dcdtuni.liquid.bc_ic import StateInitializer
        import torch.nn as nn

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), graph.P_init)),
            "m": nn.Parameter(torch.zeros(graph.N_edges, graph.N_t)),
            "T_w": nn.Parameter(torch.full((graph.N_cells, graph.N_t), graph.T_fluid_init)),
            "T_s": nn.Parameter(torch.full((graph.N_cp, graph.N_t), graph.T_solid_init)),
        }
        StateInitializer(graph).initialize(state)

        # For minimal.liquidrun: supply(200kPa) → return(100kPa), single pipe
        # Flow should be positive (from supply to return)
        m_val = state["m"].data[0, 0].item()
        assert m_val > 0, f"Flow should be positive (supply→return), got {m_val}"

    def test_pressure_monotonic(self):
        """Pressure should decrease from supply to return along flow path."""
        graph, _ = _make_graph()
        from dcdtuni.liquid.bc_ic import StateInitializer
        import torch.nn as nn

        state = {
            "P": nn.Parameter(torch.full((graph.N_nodes, graph.N_t), graph.P_init)),
            "m": nn.Parameter(torch.zeros(graph.N_edges, graph.N_t)),
            "T_w": nn.Parameter(torch.full((graph.N_cells, graph.N_t), graph.T_fluid_init)),
            "T_s": nn.Parameter(torch.full((graph.N_cp, graph.N_t), graph.T_solid_init)),
        }
        StateInitializer(graph).initialize(state)

        # supply node should have higher P than return node
        supply_idx = graph.node_ids.index("supply")
        return_idx = graph.node_ids.index("return")
        P_supply = state["P"].data[supply_idx, 0].item()
        P_return = state["P"].data[return_idx, 0].item()
        assert P_supply > P_return, (
            f"P_supply={P_supply:.0f} should be > P_return={P_return:.0f}"
        )

    def test_temperatures_initialized(self):
        """T_w and T_s should be at their init values."""
        graph, _ = _make_graph()
        from dcdtuni.liquid.bc_ic import StateInitializer
        import torch.nn as nn

        state = {
            "P": nn.Parameter(torch.zeros(graph.N_nodes, graph.N_t)),
            "m": nn.Parameter(torch.zeros(graph.N_edges, graph.N_t)),
            "T_w": nn.Parameter(torch.zeros(graph.N_cells, graph.N_t)),
            "T_s": nn.Parameter(torch.zeros(graph.N_cp, graph.N_t)),
        }
        StateInitializer(graph).initialize(state)

        if graph.N_cells > 0:
            assert torch.allclose(
                state["T_w"].data,
                torch.full_like(state["T_w"].data, graph.T_fluid_init),
            )
        if graph.N_cp > 0:
            assert torch.allclose(
                state["T_s"].data,
                torch.full_like(state["T_s"].data, graph.T_solid_init),
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestStateInitializer -v`
Expected: FAIL with `ImportError: cannot import name 'StateInitializer' from 'dcdtuni.liquid.bc_ic'`

- [ ] **Step 3: Implement StateInitializer**

Add to `src/backend/liquidcooling/bc_ic.py`:

```python
class StateInitializer:
    """Topology-aware BFS initialization for pressure, flow, and temperature."""

    F_EST = 0.02  # rough turbulent friction factor for initialization

    def __init__(self, graph: CompiledGraph) -> None:
        self.graph = graph

    def initialize(self, state: dict[str, nn.Parameter]) -> None:
        """Populate state with smart initial guesses via BFS from BC nodes."""
        g = self.graph
        with torch.no_grad():
            # Temperature init (centralized)
            if state["T_w"].numel() > 0:
                state["T_w"].data.fill_(g.T_fluid_init)
            if state["T_s"].numel() > 0:
                state["T_s"].data.fill_(g.T_solid_init)

            # Identify supply (outgoing edges) and return (incoming edges) BC nodes
            supply_node = None
            return_node = None
            for i in range(g.N_nodes):
                if not g.bc_mask[i]:
                    continue
                if len(g.node_out_edges[i]) > 0 and supply_node is None:
                    supply_node = i
                if len(g.node_in_edges[i]) > 0 and return_node is None:
                    return_node = i

            if supply_node is None or return_node is None:
                # Fallback: uniform pressure, small positive flow
                state["P"].data.fill_(g.P_init)
                state["m"].data.fill_(0.01)
                return

            # BFS from supply node: propagate pressure estimates
            P_est = torch.full((g.N_nodes,), g.P_init, dtype=torch.float64)
            P_est[supply_node] = g.bc_P[supply_node].item()
            visited = torch.zeros(g.N_nodes, dtype=torch.bool)
            visited[supply_node] = True
            queue = deque([supply_node])
            edge_visited = torch.zeros(g.N_edges, dtype=torch.bool)

            while queue:
                node = queue.popleft()
                for e_idx in g.node_out_edges[node]:
                    to_node = g.edge_to[e_idx].item()
                    if visited[to_node]:
                        continue
                    dP = self._estimate_edge_dP(e_idx, g)
                    P_est[to_node] = P_est[node] - dP
                    visited[to_node] = True
                    edge_visited[e_idx] = True
                    queue.append(to_node)
                for e_idx in g.node_in_edges[node]:
                    from_node = g.edge_from[e_idx].item()
                    if visited[from_node]:
                        continue
                    dP = self._estimate_edge_dP(e_idx, g)
                    P_est[from_node] = P_est[node] + dP
                    visited[from_node] = True
                    edge_visited[e_idx] = True
                    queue.append(from_node)

            # Global flow scale: adjust so return node matches its BC pressure
            if return_node is not None and visited[return_node]:
                P_target_return = g.bc_P[return_node].item()
                P_actual_return = P_est[return_node].item()
                total_dP_propagated = P_est[supply_node].item() - P_actual_return
                total_dP_target = P_est[supply_node].item() - P_target_return
                if abs(total_dP_propagated) > 1.0:
                    scale = (abs(total_dP_target) / abs(total_dP_propagated)) ** 0.5
                else:
                    scale = 1.0
                # Re-scale pressures (interpolate between supply and BFS result)
                for i in range(g.N_nodes):
                    if i == supply_node:
                        continue
                    dP_from_supply = P_est[supply_node].item() - P_est[i].item()
                    P_est[i] = P_est[supply_node].item() - dP_from_supply * (
                        total_dP_target / (total_dP_propagated + 1e-10)
                    )

            # Enforce BC pressures exactly
            for i in range(g.N_nodes):
                if g.bc_mask[i]:
                    P_est[i] = g.bc_P[i].item()

            # Set pressure state (broadcast over N_t)
            state["P"].data[:] = P_est.unsqueeze(1).expand(-1, g.N_t)

            # Derive flow from pressure gradient
            for e_idx in range(g.N_edges):
                fn = g.edge_from[e_idx].item()
                tn = g.edge_to[e_idx].item()
                dP = P_est[fn] - P_est[tn]
                R = self._estimate_edge_R(e_idx, g)
                m_est = torch.sign(dP) * (dP.abs() / (R + 1e-10)).sqrt()
                state["m"].data[e_idx, :] = m_est

            # Fallback for unvisited
            for i in range(g.N_nodes):
                if not visited[i]:
                    state["P"].data[i, :] = g.P_init
            for e_idx in range(g.N_edges):
                if not edge_visited[e_idx] and state["m"].data[e_idx, 0].abs() < 1e-12:
                    state["m"].data[e_idx, :] = 0.01

    def _estimate_edge_dP(self, e_idx: int, g: CompiledGraph) -> float:
        """Rough pressure drop estimate for BFS propagation."""
        etype = g.edge_type[e_idx].item()
        if etype == 0 and g.pipe_params is not None:
            # Pipe: dP = f * L/D * rho * v^2 / 2, assume m_est ~ 0.1 kg/s
            local_mask = (g.pipe_indices.edge_ids == e_idx)
            if local_mask.any():
                li = local_mask.nonzero(as_tuple=True)[0][0].item()
                D = g.pipe_params.D[li].item()
                L = g.pipe_params.L[li].item()
                A = 3.14159 * D * D / 4.0
                m_est = 0.1
                return self.F_EST * L / D * m_est**2 / (2 * g.fluid_rho * A**2)
        elif etype == 1 and g.cp_params is not None:
            # Cold plate: use dp_a * m^2
            local_mask = (g.cp_indices.edge_ids == e_idx)
            if local_mask.any():
                li = local_mask.nonzero(as_tuple=True)[0][0].item()
                dp_a = g.cp_params.dp_a[li].item()
                return dp_a * 0.1**2
        elif etype == 3 and g.pump_indices is not None:
            # Pump: negative dP (adds head)
            local_mask = (g.pump_indices.edge_ids == e_idx)
            if local_mask.any():
                li = local_mask.nonzero(as_tuple=True)[0][0].item()
                H0 = g.pump_params.H0[li].item()
                return -(g.fluid_rho * g.g_mag * H0)
        # Default: small resistance
        return 1000.0

    def _estimate_edge_R(self, e_idx: int, g: CompiledGraph) -> float:
        """Rough hydraulic resistance R such that dP = R * m^2."""
        etype = g.edge_type[e_idx].item()
        if etype == 0 and g.pipe_params is not None:
            local_mask = (g.pipe_indices.edge_ids == e_idx)
            if local_mask.any():
                li = local_mask.nonzero(as_tuple=True)[0][0].item()
                D = g.pipe_params.D[li].item()
                L = g.pipe_params.L[li].item()
                A = 3.14159 * D * D / 4.0
                return self.F_EST * L / D / (2 * g.fluid_rho * A**2)
        elif etype == 1 and g.cp_params is not None:
            local_mask = (g.cp_indices.edge_ids == e_idx)
            if local_mask.any():
                li = local_mask.nonzero(as_tuple=True)[0][0].item()
                return g.cp_params.dp_a[li].item()
        return 1e6  # high resistance fallback
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestStateInitializer -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/bc_ic.py src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "feat(liquid): StateInitializer with BFS pressure/flow propagation"
```

---

### Task 4: BCICMonitor

**Files:**
- Modify: `src/backend/liquidcooling/bc_ic.py`
- Modify: `src/backend/tests/test_liquid/test_bc_ic.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to test_bc_ic.py:

class TestBCICMonitor:

    def test_reports_aggregates(self):
        """compute() returns total_bc and total_ic as non-negative floats."""
        graph, _ = _make_graph()
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer, BCICMonitor
        import torch.nn as nn

        enforcer = BoundaryConditionEnforcer(graph)
        monitor = BCICMonitor(graph)

        # State with wrong pressures and temperatures
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
        # With wrong values, these should be non-zero
        assert result["total_bc"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestBCICMonitor -v`
Expected: FAIL with `ImportError: cannot import name 'BCICMonitor'`

- [ ] **Step 3: Implement BCICMonitor**

Add to `src/backend/liquidcooling/bc_ic.py`:

```python
class BCICMonitor:
    """Compute aggregate BC/IC metrics for SSE reporting."""

    def __init__(self, graph: CompiledGraph) -> None:
        self.T_fluid_init = graph.T_fluid_init
        self.T_solid_init = graph.T_solid_init

    def compute(
        self,
        state: dict[str, torch.Tensor],
        bc_enforcer: BoundaryConditionEnforcer,
    ) -> dict[str, float]:
        """Return {"total_bc": float, "total_ic": float} for monitoring."""
        with torch.no_grad():
            # BC: unweighted residual magnitude
            bc_losses = bc_enforcer.loss(state, w_bc=1.0)
            total_bc = sum(v.item() for v in bc_losses.values())

            # IC: temperature residuals at t=0
            total_ic = 0.0
            T_w = state.get("T_w")
            if T_w is not None and T_w.numel() > 0:
                total_ic += (T_w[:, 0] - self.T_fluid_init).pow(2).mean().item()
            T_s = state.get("T_s")
            if T_s is not None and T_s.numel() > 0:
                total_ic += (T_s[:, 0] - self.T_solid_init).pow(2).mean().item()

        return {"total_bc": total_bc, "total_ic": total_ic}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestBCICMonitor -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/bc_ic.py src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "feat(liquid): BCICMonitor for aggregate BC/IC reporting"
```

---

### Task 5: Integrate enforcer into model.py — extract BC loss

**Files:**
- Modify: `src/backend/liquidcooling/model.py:47,139-164,254-264`
- Modify: `src/backend/tests/test_liquid/test_bc_ic.py`

- [ ] **Step 1: Write integration test**

```python
# Add to test_bc_ic.py:

class TestModelIntegration:

    def test_model_uses_enforcer(self):
        """Model.forward() returns bc_P and bc_dead losses from enforcer."""
        graph, config = _make_graph()
        from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer
        from dcdtuni.liquid.model import LiquidODILModel

        enforcer = BoundaryConditionEnforcer(graph)
        model = LiquidODILModel(graph, bc_enforcer=enforcer)
        losses = model()
        assert "bc_P" in losses
        assert losses["bc_P"].item() >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestModelIntegration -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'bc_enforcer'`

- [ ] **Step 3: Modify model.py**

In `src/backend/liquidcooling/model.py`:

**a)** Change `__init__` signature (line 47) to accept optional enforcer:

```python
    def __init__(self, graph: CompiledGraph, bc_enforcer=None) -> None:
        super().__init__()
        self.graph = graph
        self.bc_enforcer = bc_enforcer
```

**b)** Remove the dead-end detection block (lines 150-164) — delete from `# Detect dead-end junction nodes` through `self.register_buffer("dead_end_edge_mask", dead_end_edge_mask)`.

**c)** Replace the BC loss section in `forward()` (lines 254-264) with:

```python
        # 3. Boundary-condition losses (delegated to enforcer)
        if self.bc_enforcer is not None:
            bc_losses = self.bc_enforcer.loss(state, w.bc)
            losses.update(bc_losses)
        else:
            # Fallback: inline BC loss (backward compat for tests without enforcer)
            P_bc_err = state["P"][self.bc_mask] - self.bc_P[self.bc_mask].unsqueeze(1)
            losses["bc_P"] = w.bc * P_bc_err.pow(2).mean()
```

**d)** Remove the `dead_end_edge_mask` buffer reference in the fallback path and the old `dead_end_flow` loss.

- [ ] **Step 4: Run integration test + full regression**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestModelIntegration -v`
Expected: PASS

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v --timeout=120`
Expected: All existing tests PASS (backward compat via `bc_enforcer=None` fallback)

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/model.py src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "refactor(liquid): model.py delegates BC loss to BoundaryConditionEnforcer"
```

---

### Task 6: Integrate into solver.py — enforce() after step

**Files:**
- Modify: `src/backend/liquidcooling/solver.py:32-68`

- [ ] **Step 1: Modify solver.py**

In `src/backend/liquidcooling/solver.py`, change the `solve` function signature to accept `bc_enforcer`:

```python
def solve(
    model,
    config: SolverConfig,
    log_dir: Path = None,
    callback: Callable = None,
    print_every: int = 100,
    bc_enforcer=None,
) -> list:
```

Add one line after `optimizer.step()` (after line 68):

```python
        optimizer.step()
        if bc_enforcer is not None:
            bc_enforcer.enforce(model.state)
        scheduler.step(losses["total"])
```

- [ ] **Step 2: Run regression tests**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v --timeout=120`
Expected: All tests PASS (bc_enforcer=None means no enforcement, backward compat)

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/solver.py
git commit -m "feat(liquid): solver calls bc_enforcer.enforce() after each optimizer step"
```

---

### Task 7: Integrate into runner.py — wire everything together

**Files:**
- Modify: `src/backend/liquidcooling/runner.py:103-200`

- [ ] **Step 1: Modify runner.py**

In `src/backend/liquidcooling/runner.py`:

**a)** Add import at top:

```python
from dcdtuni.liquid.bc_ic import BoundaryConditionEnforcer, StateInitializer, BCICMonitor
```

**b)** Replace the model construction + init section (lines 143-154) with:

```python
    # 3. Build model with BC enforcer
    bc_enforcer = BoundaryConditionEnforcer(graph)
    model = LiquidODILModel(graph, bc_enforcer=bc_enforcer)
    if device == "cuda" and torch.cuda.is_available():
        model = model.cuda()
        bc_enforcer = bc_enforcer.to(model.state["P"].device)
    graph = model.graph

    # 4. Smart initialization
    StateInitializer(graph).initialize(model.state)
```

**c)** In the `_monitor_callback` closure (around line 175), add BC/IC monitoring:

```python
    bc_ic_monitor = BCICMonitor(graph)

    if callback:
        _orig_cb = callback
        def _monitor_callback(log_dict):
            if log_dict.get("epoch") is not None:
                log_dict["cdu_monitor"] = _extract_cdu_monitor(model, model.graph)
                bc_ic_metrics = bc_ic_monitor.compute(
                    {k: v.data for k, v in model.state.items()}, bc_enforcer
                )
                log_dict["total_bc"] = bc_ic_metrics["total_bc"]
                log_dict["total_ic"] = bc_ic_metrics["total_ic"]
                if monitor_cp_ids:
                    log_dict["cp_monitor"] = _extract_cp_monitor(
                        model, model.graph, monitor_cp_ids
                    )
            _orig_cb(log_dict)
        solve_callback = _monitor_callback
    else:
        solve_callback = callback
```

**d)** Pass bc_enforcer to solve():

```python
    logs = solve(model, config.solver, log_dir=log_dir, callback=solve_callback,
                 print_every=100, bc_enforcer=bc_enforcer)
```

- [ ] **Step 2: Run full regression**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v --timeout=120`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/runner.py
git commit -m "feat(liquid): runner wires BoundaryConditionEnforcer, StateInitializer, BCICMonitor"
```

---

### Task 8: Final regression + cleanup

**Files:**
- All liquid module files

- [ ] **Step 1: Run the full test suite**

Run: `cd /mnt/lv/litianyu/mycode/dcdtuni && conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v --timeout=300 2>&1 | tail -30`
Expected: All 158+ tests PASS, plus 7 new bc_ic tests

- [ ] **Step 2: Run a quick solve to verify SSE payload**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -c "
import json, torch
torch.set_default_dtype(torch.float64)
from pathlib import Path
from dcdtuni.liquid.runner import run_liquid_solve

result = run_liquid_solve(
    Path('src/backend/tests/test_liquid/minimal.liquidrun'),
    case_dir=Path('/tmp/bc_ic_test'),
    device='cpu',
    callback=lambda d: print(json.dumps({k: d[k] for k in ['epoch','total_bc','total_ic'] if k in d})) if 'epoch' in d else None,
)
print('converged:', result.converged, 'loss:', result.final_loss)
"
```

Expected: `total_bc` and `total_ic` appear in callback output. `total_bc` should be near zero (hard clamp working). `total_ic` should be near zero (temperatures initialized correctly).

- [ ] **Step 3: Commit any fixups**

```bash
git add -A
git commit -m "chore(liquid): bc_ic integration cleanup and final regression pass"
```
