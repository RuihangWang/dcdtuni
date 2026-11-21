# Liquid ODIL Solver — Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ODIL liquid cooling solver scaffolding (schema, compiler, CompiledGraph) and prove it works with a steady-state hydraulic loop (Pipe + Pump + mass conservation, no thermal).

**Architecture:** Two-stage pipeline: `.liquidrun` JSON → Pydantic validation → `CompiledGraph` (index tensors + batched params) → `LiquidODILModel` (nn.Module with P/m as nn.Parameter) → Adam optimizer minimizes L2 physics residuals. Each component type has a batch handler that computes vectorized residuals for all instances simultaneously.

**Tech Stack:** Python 3.10, PyTorch, Pydantic v2, numpy. Conda env `dcdtuni`.

**Design spec:** `docs/superpowers/specs/2026-05-05-liquid-odil-solver-design.md`

**Reference repo (physics only, not its solver):** `/mnt/lv/litianyu/mycode/DiffLiquidTwin/`

**Important context:**
- The existing `src/backend/liquidcooling/` module is being **completely rewritten**. All old files will be deleted.
- Two files in `src/backend/aircooling/runner/` import from the old liquid module (`liquid_runner.py`, `stlgen_runner.py`). These imports are lazy (inside functions), so deleting the old module won't break imports at module load time — the functions that call them will fail at runtime, but that's acceptable since the old pipeline is being replaced.
- `src/backend/aircooling/runner/cfdproj_schema.py` defines `CFDBaseModel` — the Pydantic base class we'll reuse.
- All tests go in `src/backend/tests/test_liquid/` (existing directory — old test files will be deleted too).
- Always run commands with `conda run -n dcdtuni`.

---

## File Map

### Phase 0 — Scaffolding (new files)

| File | Responsibility |
|------|---------------|
| `src/backend/liquidcooling/__init__.py` | Public API exports |
| `src/backend/liquidcooling/schema.py` | Pydantic v2 models for `.liquidrun` JSON |
| `src/backend/liquidcooling/compiled_graph.py` | `CompiledGraph` dataclass (intermediate repr) |
| `src/backend/liquidcooling/compiler.py` | `.liquidrun` → `CompiledGraph` compilation |
| `src/backend/liquidcooling/bdf.py` | `bdf_diff()` + `smooth_abs()` utilities |
| `src/backend/liquidcooling/handlers/__init__.py` | Handler package |
| `src/backend/liquidcooling/handlers/base.py` | `BaseHandler` ABC |
| `src/backend/tests/test_liquid/minimal.liquidrun` | Hand-written test fixture |
| `src/backend/tests/test_liquid/test_schema.py` | Schema validation tests (rewritten) |
| `src/backend/tests/test_liquid/test_compiler.py` | Compiler tests |
| `src/backend/tests/test_liquid/test_bdf.py` | BDF utility tests |

### Phase 1 — Steady-State Hydraulics (new files)

| File | Responsibility |
|------|---------------|
| `src/backend/liquidcooling/handlers/pipe.py` | `PipeBatchHandler` — Darcy-Weisbach momentum residual |
| `src/backend/liquidcooling/handlers/pump.py` | `PumpBatchHandler` — affinity law momentum residual |
| `src/backend/liquidcooling/model.py` | `LiquidODILModel(nn.Module)` — top-level ODIL model |
| `src/backend/liquidcooling/solver.py` | `solve()` — Adam optimization loop |
| `src/backend/tests/test_liquid/test_hydraulic.py` | End-to-end hydraulic loop test |
| `src/backend/tests/test_liquid/simple_loop.liquidrun` | CDU→pipe→pipe→CDU test fixture |

---

## Task 1: Clear old module and create directory structure

**Files:**
- Delete: all files under `src/backend/liquidcooling/` (22 files)
- Delete: all files under `src/backend/tests/test_liquid/` (13 test files)
- Create: `src/backend/liquidcooling/__init__.py`
- Create: `src/backend/liquidcooling/handlers/__init__.py`
- Create: `src/backend/tests/test_liquid/__init__.py`

- [ ] **Step 1: Delete old liquid module files**

```bash
conda run -n dcdtuni bash -c '
cd /mnt/lv/litianyu/mycode/dcdtuni
rm -rf src/backend/liquidcooling/components/
rm -rf src/backend/liquidcooling/devices/
rm -rf src/backend/liquidcooling/solver/
rm -rf src/backend/liquidcooling/thermal/
rm -f src/backend/liquidcooling/demo_two_racks.py
rm -f src/backend/liquidcooling/diffliquid_parser.py
rm -f src/backend/liquidcooling/factory.py
rm -f src/backend/liquidcooling/results.py
rm -f src/backend/liquidcooling/schema.py
rm -f src/backend/liquidcooling/topology.py
rm -f src/backend/liquidcooling/__init__.py
'
```

- [ ] **Step 2: Delete old test files**

```bash
conda run -n dcdtuni bash -c '
cd /mnt/lv/litianyu/mycode/dcdtuni
rm -f src/backend/tests/test_liquid/test_components.py
rm -f src/backend/tests/test_liquid/test_diffliquid_comparison.py
rm -f src/backend/tests/test_liquid/test_diffliquid_parser.py
rm -f src/backend/tests/test_liquid/test_integration.py
rm -f src/backend/tests/test_liquid/test_liquid_runner.py
rm -f src/backend/tests/test_liquid/test_rdhx.py
rm -f src/backend/tests/test_liquid/test_runner_gate.py
rm -f src/backend/tests/test_liquid/test_schema.py
rm -f src/backend/tests/test_liquid/test_solver.py
rm -f src/backend/tests/test_liquid/test_thermal.py
rm -f src/backend/tests/test_liquid/test_topology_auto_guard.py
rm -f src/backend/tests/test_liquid/test_topology.py
rm -f src/backend/tests/test_liquid/test_transient_coupling.py
'
```

- [ ] **Step 3: Create new directory structure with __init__ files**

`src/backend/liquidcooling/__init__.py`:
```python
"""
ODIL-based liquid cooling solver.

Non-causal optimization: all spacetime variables (P, m, T_w, T_s) are
nn.Parameters optimized simultaneously via Adam to minimize physics residuals.
"""
```

`src/backend/liquidcooling/handlers/__init__.py`:
```python
"""Per-component-type batch residual handlers."""
```

Ensure `src/backend/tests/test_liquid/__init__.py` exists (may already exist).

- [ ] **Step 4: Verify clean slate**

Run: `conda run -n dcdtuni python -c "import dcdtuni.liquid; print('OK')"`
Expected: `OK` (empty module imports fine)

- [ ] **Step 5: Commit**

```bash
git add -A src/backend/liquidcooling/ src/backend/tests/test_liquid/
git commit -m "refactor: clear old liquid module for ODIL rewrite"
```

---

## Task 2: BDF utilities and smooth_abs

**Files:**
- Create: `src/backend/liquidcooling/bdf.py`
- Create: `src/backend/tests/test_liquid/test_bdf.py`

- [ ] **Step 1: Write the BDF test**

`src/backend/tests/test_liquid/test_bdf.py`:
```python
import torch
import pytest
from dcdtuni.liquid.bdf import bdf_diff, smooth_abs


class TestSmoothAbs:
    def test_positive(self):
        x = torch.tensor([3.0])
        result = smooth_abs(x)
        assert torch.allclose(result, torch.tensor([3.0]), atol=1e-5)

    def test_negative(self):
        x = torch.tensor([-3.0])
        result = smooth_abs(x)
        assert torch.allclose(result, torch.tensor([3.0]), atol=1e-5)

    def test_near_zero(self):
        x = torch.tensor([0.0])
        result = smooth_abs(x, eps=1e-6)
        assert result.item() > 0.0
        assert result.item() < 1e-5

    def test_gradient_at_zero(self):
        x = torch.tensor([0.0], requires_grad=True)
        y = smooth_abs(x, eps=1e-6)
        y.backward()
        assert x.grad is not None
        assert torch.isfinite(x.grad)


class TestBdfDiff:
    def test_bdf1_linear(self):
        """BDF1 of linear function x=t should give dx/dt=1."""
        dt = 0.5
        t = torch.arange(0, 5.0, dt)  # [0, 0.5, 1.0, ..., 4.5]
        x = t.unsqueeze(0)  # [1, N_t]
        dxdt = bdf_diff(x, dt, order=1)
        assert dxdt.shape == (1, len(t) - 1)
        assert torch.allclose(dxdt, torch.ones_like(dxdt), atol=1e-6)

    def test_bdf2_linear(self):
        """BDF2 of linear function should also give dx/dt=1."""
        dt = 0.5
        t = torch.arange(0, 5.0, dt)
        x = t.unsqueeze(0)
        dxdt = bdf_diff(x, dt, order=2)
        assert dxdt.shape == (1, len(t) - 1)
        assert torch.allclose(dxdt, torch.ones_like(dxdt), atol=1e-6)

    def test_bdf1_quadratic(self):
        """BDF1 of x=t^2: dx/dt should approximate 2t (first-order error)."""
        dt = 0.01
        t = torch.arange(0, 1.0, dt)
        x = (t**2).unsqueeze(0)
        dxdt = bdf_diff(x, dt, order=1)
        exact = (2 * t[1:]).unsqueeze(0)
        assert torch.allclose(dxdt, exact, atol=dt)  # O(dt) error

    def test_bdf2_quadratic(self):
        """BDF2 of x=t^2: dx/dt should be exact for quadratics (2nd order)."""
        dt = 0.01
        t = torch.arange(0, 1.0, dt)
        x = (t**2).unsqueeze(0)
        dxdt = bdf_diff(x, dt, order=2)
        # BDF2 output has same length as BDF1 (fallback at step 0)
        exact = (2 * t[1:]).unsqueeze(0)
        assert torch.allclose(dxdt, exact, atol=1e-4)

    def test_bdf3(self):
        """BDF3 of x=t^2: should also handle it."""
        dt = 0.01
        t = torch.arange(0, 1.0, dt)
        x = (t**2).unsqueeze(0)
        dxdt = bdf_diff(x, dt, order=3)
        exact = (2 * t[1:]).unsqueeze(0)
        assert torch.allclose(dxdt, exact, atol=1e-3)

    def test_batch_dimension(self):
        """Multiple signals in batch dimension."""
        dt = 1.0
        x = torch.tensor([[1.0, 2.0, 3.0, 4.0],
                          [10.0, 20.0, 30.0, 40.0]])
        dxdt = bdf_diff(x, dt, order=1)
        assert dxdt.shape == (2, 3)
        assert torch.allclose(dxdt[0], torch.ones(3))
        assert torch.allclose(dxdt[1], torch.full((3,), 10.0))

    def test_output_length_matches_across_orders(self):
        """All BDF orders produce the same output length N_t-1."""
        x = torch.randn(3, 20)
        for order in [1, 2, 3]:
            result = bdf_diff(x, dt=1.0, order=order)
            assert result.shape == (3, 19), f"BDF{order} shape mismatch"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_bdf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dcdtuni.liquid.bdf'`

- [ ] **Step 3: Implement bdf.py**

`src/backend/liquidcooling/bdf.py`:
```python
"""BDF time discretization utilities and differentiable math helpers."""

from __future__ import annotations

import torch
from torch import Tensor


def smooth_abs(x: Tensor, eps: float = 1e-6) -> Tensor:
    """Differentiable absolute value: sqrt(x^2 + eps^2)."""
    return torch.sqrt(x * x + eps * eps)


def bdf_diff(x: Tensor, dt: float, order: int) -> Tensor:
    """
    Compute BDF time derivative of x[..., N_t], returns [..., N_t - 1].

    All orders produce the same output length by using lower-order fallback
    for the initial steps where higher-order stencils are unavailable.

    Args:
        x: Tensor with time as the last dimension.
        dt: Uniform time step size.
        order: BDF order (1, 2, or 3).

    Returns:
        Approximate dx/dt with shape [..., N_t - 1].
    """
    if order == 1:
        return (x[..., 1:] - x[..., :-1]) / dt

    if order == 2:
        bdf1_all = (x[..., 1:] - x[..., :-1]) / dt
        if x.shape[-1] < 3:
            return bdf1_all
        bdf2_part = (3 * x[..., 2:] - 4 * x[..., 1:-1] + x[..., :-2]) / (2 * dt)
        return torch.cat([bdf1_all[..., :1], bdf2_part], dim=-1)

    if order == 3:
        bdf1_all = (x[..., 1:] - x[..., :-1]) / dt
        if x.shape[-1] < 3:
            return bdf1_all
        bdf2_step = (3 * x[..., 2:3] - 4 * x[..., 1:2] + x[..., 0:1]) / (2 * dt)
        if x.shape[-1] < 4:
            return torch.cat([bdf1_all[..., :1], bdf2_step], dim=-1)
        bdf3_part = (
            11 * x[..., 3:] - 18 * x[..., 2:-1] + 9 * x[..., 1:-2] - 2 * x[..., :-3]
        ) / (6 * dt)
        return torch.cat([bdf1_all[..., :1], bdf2_step, bdf3_part], dim=-1)

    raise ValueError(f"Unsupported BDF order: {order}")


if __name__ == "__main__":
    dt = 0.01
    t = torch.arange(0, 1.0, dt)
    x = (t**2).unsqueeze(0)
    for order in [1, 2, 3]:
        dxdt = bdf_diff(x, dt, order=order)
        exact = (2 * t[1:]).unsqueeze(0)
        max_err = (dxdt - exact).abs().max().item()
        print(f"BDF{order}: max_error={max_err:.6e}")
        assert max_err < 0.1, f"BDF{order} error too large"
    print("PASS: bdf self-test")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_bdf.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Run self-test**

Run: `conda run -n dcdtuni python src/backend/liquidcooling/bdf.py`
Expected: `PASS: bdf self-test`

- [ ] **Step 6: Commit**

```bash
git add src/backend/liquidcooling/bdf.py src/backend/tests/test_liquid/test_bdf.py
git commit -m "feat(liquid): add BDF time discretization utilities"
```

---

## Task 3: Pydantic schema for .liquidrun

**Files:**
- Create: `src/backend/liquidcooling/schema.py`
- Create: `src/backend/tests/test_liquid/test_schema.py`

- [ ] **Step 1: Write the schema test**

`src/backend/tests/test_liquid/test_schema.py`:
```python
import json
import pytest
from pathlib import Path
from dcdtuni.liquid.schema import LiquidrunConfig


FIXTURE_DIR = Path(__file__).parent


class TestLiquidrunSchema:
    def test_load_minimal_fixture(self):
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        assert config.version == "1.0"
        assert len(config.nodes) >= 2
        assert len(config.edges) >= 1
        assert config.fluid.rho_kg_m3 == pytest.approx(997.0)

    def test_gravity_vector(self):
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        assert len(config.gravity) == 3
        assert config.gravity[1] == pytest.approx(-9.81)

    def test_node_types(self):
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        types = {n.type for n in config.nodes}
        assert "pressure_bc" in types

    def test_edge_from_to_alias(self):
        """Edge 'from'/'to' fields use aliases since they're Python keywords."""
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        edge = config.edges[0]
        assert edge.from_node != ""
        assert edge.to_node != ""

    def test_missing_version_fails(self):
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        del raw["version"]
        with pytest.raises(Exception):
            LiquidrunConfig.model_validate(raw)

    def test_empty_events_ok(self):
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        raw["events"] = []
        config = LiquidrunConfig.model_validate(raw)
        assert config.events == []

    def test_pipe_waypoints_present(self):
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        pipe_edges = [e for e in config.edges if e.type == "pipe"]
        assert len(pipe_edges) >= 1
        assert "waypoints_m" in pipe_edges[0].params
        assert len(pipe_edges[0].params["waypoints_m"]) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create minimal.liquidrun test fixture**

`src/backend/tests/test_liquid/minimal.liquidrun`:
```json
{
  "version": "1.0",
  "source": "STLGeneratorForOpenFoam",
  "projectName": "MinimalTest",
  "gravity": [0, -9.81, 0],
  "fluid": {
    "rho_kg_m3": 997.0,
    "cp_J_kgK": 4182.0,
    "mu_Pa_s": 0.00089,
    "k_W_mK": 0.606
  },
  "time": {
    "dt_s": 1.0,
    "N_t": 10,
    "bdf_order": 1
  },
  "racks": [],
  "nodes": [
    {
      "id": "supply",
      "type": "pressure_bc",
      "position_m": [0.0, 0.0, 0.0],
      "bc": {"P_Pa": 200000, "T_K": 308.15}
    },
    {
      "id": "return",
      "type": "pressure_bc",
      "position_m": [0.0, 0.0, 1.0],
      "bc": {"P_Pa": 100000}
    }
  ],
  "edges": [
    {
      "id": "pipe1",
      "type": "pipe",
      "from": "supply",
      "to": "return",
      "params": {
        "D_m": 0.025,
        "roughness_m": 1.5e-6,
        "n_cells": 5,
        "waypoints_m": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.5], [0.0, 0.0, 1.0]],
        "K_minor_per_bend": 0.5
      }
    }
  ],
  "events": [],
  "initial_conditions": {
    "T_fluid_K": 308.15,
    "T_solid_K": 340.0,
    "P_uniform_Pa": 150000
  },
  "solver": {
    "optimizer": "adam",
    "lr": 0.001,
    "max_epochs": 1000,
    "convergence_tol": 1e-6,
    "weights": {
      "mass": 1.0,
      "momentum": 1e-5,
      "energy": 1.0,
      "bc": 100.0,
      "ic": 10.0
    }
  }
}
```

- [ ] **Step 4: Implement schema.py**

`src/backend/liquidcooling/schema.py`:
```python
"""Pydantic v2 models for .liquidrun JSON validation."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from dcdtuni.runner.cfdproj_schema import CFDBaseModel


class FluidConfig(CFDBaseModel):
    rho_kg_m3: float
    cp_J_kgK: float
    mu_Pa_s: float
    k_W_mK: float


class TimeConfig(CFDBaseModel):
    dt_s: float
    N_t: int
    bdf_order: int = 1


class NodeBCConfig(CFDBaseModel):
    P_Pa: float
    T_K: Optional[float] = None


class NodeConfig(CFDBaseModel):
    id: str
    type: Literal["pressure_bc", "junction"]
    position_m: list[float]
    bc: Optional[NodeBCConfig] = None


class EdgeConfig(CFDBaseModel):
    id: str
    type: Literal["pipe", "cold_plate", "valve", "pump"]
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    params: dict[str, Any]


class ManifoldConfig(CFDBaseModel):
    supply_node: str
    return_node: str
    layout: str = "both-left"
    D_m: float = 0.025


class CpCuPairConfig(CFDBaseModel):
    id: str
    edge_id: str
    Q_W: float = 350.0


class ServerConfig(CFDBaseModel):
    id: str
    slot_range: list[int]
    z_m: float = 0.0
    cp_cu_pairs: list[CpCuPairConfig] = []
    inlet_node: str = ""
    outlet_node: str = ""


class RackConfig(CFDBaseModel):
    id: str
    position_m: list[float]
    n_slots: int = 42
    manifold: Optional[ManifoldConfig] = None
    servers: list[ServerConfig] = []


class EventConfig(CFDBaseModel):
    time_s: float
    target: str
    field: str
    value: float


class ICConfig(CFDBaseModel):
    T_fluid_K: float = 308.15
    T_solid_K: float = 340.0
    P_uniform_Pa: float = 150000.0


class SolverWeightsConfig(CFDBaseModel):
    mass: float = 1.0
    momentum: float = 1e-5
    energy: float = 1.0
    bc: float = 100.0
    ic: float = 10.0


class SolverConfig(CFDBaseModel):
    optimizer: str = "adam"
    lr: float = 1e-3
    max_epochs: int = 5000
    convergence_tol: float = 1e-6
    weights: SolverWeightsConfig = SolverWeightsConfig()


class LiquidrunConfig(CFDBaseModel):
    version: str
    source: str = "STLGeneratorForOpenFoam"
    projectName: str = "Untitled"
    gravity: list[float]
    fluid: FluidConfig
    time: TimeConfig
    nodes: list[NodeConfig]
    edges: list[EdgeConfig]
    racks: list[RackConfig] = []
    events: list[EventConfig] = []
    initial_conditions: ICConfig = ICConfig()
    solver: SolverConfig = SolverConfig()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_schema.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/backend/liquidcooling/schema.py src/backend/tests/test_liquid/test_schema.py src/backend/tests/test_liquid/minimal.liquidrun
git commit -m "feat(liquid): add Pydantic schema for .liquidrun format"
```

---

## Task 4: CompiledGraph dataclass

**Files:**
- Create: `src/backend/liquidcooling/compiled_graph.py`
- Create: `src/backend/tests/test_liquid/test_compiled_graph.py`

- [ ] **Step 1: Write the test**

`src/backend/tests/test_liquid/test_compiled_graph.py`:
```python
import json
import torch
import pytest
from dcdtuni.liquid.compiled_graph import (
    CompiledGraph, PipeIndices, PipeBatchParams,
    PumpIndices, PumpBatchParams,
)


class TestCompiledGraph:
    def _make_minimal(self):
        return CompiledGraph(
            N_nodes=2, N_edges=1, N_cells=5, N_cp=0, N_t=10,
            node_ids=["supply", "return"],
            node_position=torch.zeros(2, 3),
            bc_mask=torch.tensor([True, True]),
            bc_P=torch.tensor([200000.0, 100000.0]),
            bc_T=torch.tensor([308.15, float("nan")]),
            junction_mask=torch.tensor([False, False]),
            edge_from=torch.tensor([0]),
            edge_to=torch.tensor([1]),
            edge_type=torch.tensor([0]),
            incidence=torch.sparse_coo_tensor(
                indices=torch.tensor([[0, 1], [0, 0]]),
                values=torch.tensor([1.0, -1.0]),
                size=(2, 1),
            ),
            pipe_indices=PipeIndices(
                edge_ids=torch.tensor([0]),
                node_from=torch.tensor([0]),
                node_to=torch.tensor([1]),
                cell_start=torch.tensor([0]),
                cell_end=torch.tensor([5]),
            ),
            cp_indices=None,
            valve_indices=None,
            pump_indices=None,
            pipe_params=PipeBatchParams(
                D=torch.tensor([0.025]),
                L=torch.tensor([1.0]),
                dh=torch.tensor([0.0]),
                K_minor=torch.tensor([0.0]),
                A=torch.tensor([4.909e-4]),
                roughness=torch.tensor([1.5e-6]),
            ),
            cp_params=None,
            valve_params=None,
            pump_params=None,
            cell_edge_id=torch.zeros(5, dtype=torch.long),
            cell_ds=torch.full((5,), 0.2),
            cell_volume=torch.full((5,), 9.82e-5),
            cell_dh=torch.zeros(5),
            cell_position=torch.zeros(5, 3),
            node_in_edges=[[], []],
            node_out_edges=[[0], []],
            dt=1.0, bdf_order=1,
            T_fluid_init=308.15, T_solid_init=340.0, P_init=150000.0,
            solver_config=None,
            rack_meta=[],
            fluid_rho=997.0, fluid_cp=4182.0, fluid_mu=0.00089,
            g_mag=9.81,
        )

    def test_construction(self):
        g = self._make_minimal()
        assert g.N_nodes == 2
        assert g.N_edges == 1
        assert g.N_cells == 5

    def test_to_json_roundtrip(self, tmp_path):
        g = self._make_minimal()
        out = tmp_path / "graph.json"
        g.to_json(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["N_nodes"] == 2
        assert data["N_edges"] == 1
        assert len(data["node_ids"]) == 2

    def test_incidence_shape(self):
        g = self._make_minimal()
        dense = g.incidence.to_dense()
        assert dense.shape == (2, 1)
        assert dense[0, 0] == 1.0
        assert dense[1, 0] == -1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_compiled_graph.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement compiled_graph.py**

`src/backend/liquidcooling/compiled_graph.py`:
```python
"""CompiledGraph: intermediate representation between .liquidrun and PyTorch model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from torch import Tensor


@dataclass
class PipeIndices:
    edge_ids: Tensor       # [N_pipes] — global edge indices
    node_from: Tensor      # [N_pipes] — global node indices
    node_to: Tensor        # [N_pipes]
    cell_start: Tensor     # [N_pipes] — first cell index in global cell array
    cell_end: Tensor       # [N_pipes] — one-past-last cell index


@dataclass
class PipeBatchParams:
    D: Tensor              # [N_pipes]
    L: Tensor              # [N_pipes]
    dh: Tensor             # [N_pipes]
    K_minor: Tensor        # [N_pipes]
    A: Tensor              # [N_pipes]
    roughness: Tensor      # [N_pipes]


@dataclass
class ColdPlateIndices:
    edge_ids: Tensor       # [N_cp]
    node_from: Tensor      # [N_cp]
    node_to: Tensor        # [N_cp]
    cp_ids: Tensor         # [N_cp] — index into T_s state


@dataclass
class ColdPlateBatchParams:
    dp_a: Tensor           # [N_cp]
    dp_b: Tensor           # [N_cp]
    UA: Tensor             # [N_cp]
    C_solid: Tensor        # [N_cp]
    V_fluid: Tensor        # [N_cp]
    Q_W: Tensor            # [N_cp, N_t]


@dataclass
class ValveIndices:
    edge_ids: Tensor
    node_from: Tensor
    node_to: Tensor


@dataclass
class ValveBatchParams:
    D: Tensor
    Kv_max: Tensor
    opening: Tensor        # [N_valves, N_t]
    characteristic: Tensor # [N_valves] int


@dataclass
class PumpIndices:
    edge_ids: Tensor
    node_from: Tensor
    node_to: Tensor


@dataclass
class PumpBatchParams:
    H0: Tensor
    a_coef: Tensor
    rpm_ratio: Tensor      # [N_pumps, N_t]


@dataclass
class RackMeta:
    rack_id: str
    server_ids: list[str]
    server_edge_ids: list[list[str]]


@dataclass
class CompiledGraph:
    N_nodes: int
    N_edges: int
    N_cells: int
    N_cp: int
    N_t: int

    node_ids: list[str]
    node_position: Tensor
    bc_mask: Tensor
    bc_P: Tensor
    bc_T: Tensor
    junction_mask: Tensor

    edge_from: Tensor
    edge_to: Tensor
    edge_type: Tensor

    incidence: Tensor

    pipe_indices: Optional[PipeIndices]
    cp_indices: Optional[ColdPlateIndices]
    valve_indices: Optional[ValveIndices]
    pump_indices: Optional[PumpIndices]

    pipe_params: Optional[PipeBatchParams]
    cp_params: Optional[ColdPlateBatchParams]
    valve_params: Optional[ValveBatchParams]
    pump_params: Optional[PumpBatchParams]

    cell_edge_id: Tensor
    cell_ds: Tensor
    cell_volume: Tensor
    cell_dh: Tensor
    cell_position: Tensor

    node_in_edges: list[list[int]]
    node_out_edges: list[list[int]]

    dt: float
    bdf_order: int
    T_fluid_init: float
    T_solid_init: float
    P_init: float

    solver_config: object
    rack_meta: list[RackMeta]

    fluid_rho: float
    fluid_cp: float
    fluid_mu: float
    g_mag: float

    def to_json(self, path: Path) -> None:
        """Serialize to JSON for debugging (tensors → lists)."""

        def _t2l(t: Tensor) -> list:
            if t.is_sparse:
                t = t.to_dense()
            return t.detach().cpu().numpy().tolist()

        data = {
            "N_nodes": self.N_nodes,
            "N_edges": self.N_edges,
            "N_cells": self.N_cells,
            "N_cp": self.N_cp,
            "N_t": self.N_t,
            "node_ids": self.node_ids,
            "node_position": _t2l(self.node_position),
            "bc_mask": _t2l(self.bc_mask),
            "edge_from": _t2l(self.edge_from),
            "edge_to": _t2l(self.edge_to),
            "edge_type": _t2l(self.edge_type),
            "dt": self.dt,
            "bdf_order": self.bdf_order,
            "fluid_rho": self.fluid_rho,
            "g_mag": self.g_mag,
        }
        path.write_text(json.dumps(data, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_compiled_graph.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/compiled_graph.py src/backend/tests/test_liquid/test_compiled_graph.py
git commit -m "feat(liquid): add CompiledGraph dataclass"
```

---

## Task 5: BaseHandler ABC

**Files:**
- Create: `src/backend/liquidcooling/handlers/base.py`

- [ ] **Step 1: Implement BaseHandler**

`src/backend/liquidcooling/handlers/base.py`:
```python
"""Abstract base class for component batch residual handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import torch.nn as nn
from torch import Tensor


class BaseHandler(nn.Module, ABC):
    """
    Base class for per-type batch residual handlers.

    Each handler processes ALL instances of one component type simultaneously.
    Subclasses implement compute_residuals() returning a dict of named residual
    tensors. Keys can include 'momentum', 'energy', 'ic'.
    """

    @abstractmethod
    def compute_residuals(
        self,
        state: dict[str, Tensor],
        indices: Any,
        dt: float,
        bdf_order: int,
    ) -> dict[str, Tensor]:
        """
        Compute batch residuals for all instances of this component type.

        Args:
            state: dict with keys 'P', 'm', 'T_w', 'T_s' — the global state tensors.
            indices: type-specific index dataclass for gather/scatter.
            dt: time step size.
            bdf_order: BDF order for ODE residuals.

        Returns:
            Dict of named residual tensors. Each value shape: [N_instances, N_t]
            or [N_instances]. Not all keys are required — e.g. valve has no 'energy'.
        """
```

- [ ] **Step 2: Verify import**

Run: `conda run -n dcdtuni python -c "from dcdtuni.liquid.handlers.base import BaseHandler; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/handlers/base.py
git commit -m "feat(liquid): add BaseHandler ABC for component handlers"
```

---

## Task 6: Compiler — .liquidrun JSON → CompiledGraph

**Files:**
- Create: `src/backend/liquidcooling/compiler.py`
- Create: `src/backend/tests/test_liquid/test_compiler.py`

- [ ] **Step 1: Write the compiler test**

`src/backend/tests/test_liquid/test_compiler.py`:
```python
import json
import torch
import pytest
from pathlib import Path
from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiler import compile_graph


FIXTURE_DIR = Path(__file__).parent


def _load_config(name: str) -> LiquidrunConfig:
    raw = json.loads((FIXTURE_DIR / name).read_text())
    return LiquidrunConfig.model_validate(raw)


class TestCompilerMinimal:
    def test_compiles_without_error(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert graph.N_nodes == 2
        assert graph.N_edges == 1

    def test_node_ids_preserved(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert "supply" in graph.node_ids
        assert "return" in graph.node_ids

    def test_bc_mask(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert graph.bc_mask.all()  # both nodes are pressure_bc

    def test_bc_pressure_values(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        supply_idx = graph.node_ids.index("supply")
        return_idx = graph.node_ids.index("return")
        assert graph.bc_P[supply_idx].item() == pytest.approx(200000.0)
        assert graph.bc_P[return_idx].item() == pytest.approx(100000.0)

    def test_incidence_matrix(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        dense = graph.incidence.to_dense()
        assert dense.shape == (2, 1)
        supply_idx = graph.node_ids.index("supply")
        return_idx = graph.node_ids.index("return")
        assert dense[supply_idx, 0].item() == 1.0
        assert dense[return_idx, 0].item() == -1.0

    def test_pipe_indices(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert graph.pipe_indices is not None
        assert graph.pipe_indices.edge_ids.shape == (1,)

    def test_pipe_params_from_waypoints(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert graph.pipe_params.D[0].item() == pytest.approx(0.025)
        assert graph.pipe_params.L[0].item() == pytest.approx(1.0)

    def test_pipe_cells(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert graph.N_cells == 5
        assert graph.cell_ds.shape == (5,)
        assert graph.cell_ds[0].item() == pytest.approx(0.2)

    def test_gravity_magnitude(self):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert graph.g_mag == pytest.approx(9.81)

    def test_to_json(self, tmp_path):
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        graph.to_json(tmp_path / "graph.json")
        data = json.loads((tmp_path / "graph.json").read_text())
        assert data["N_nodes"] == 2


class TestCompilerWaypoints:
    def test_bend_detection(self):
        """A pipe with an L-bend should detect 1 bend."""
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        # minimal.liquidrun pipe has waypoints [0,0,0]->[0,0,0.5]->[0,0,1.0]
        # which is a straight line → 0 bends
        assert graph.pipe_params.K_minor[0].item() == pytest.approx(0.0)

    def test_elevation_change(self):
        """Pipe going along Z (not Y) — gravity is -Y so dh should be 0."""
        config = _load_config("minimal.liquidrun")
        graph = compile_graph(config)
        assert graph.pipe_params.dh[0].item() == pytest.approx(0.0, abs=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement compiler.py**

`src/backend/liquidcooling/compiler.py`:
```python
"""Compile a validated LiquidrunConfig into a CompiledGraph."""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import torch

from dcdtuni.liquid.schema import LiquidrunConfig, EdgeConfig
from dcdtuni.liquid.compiled_graph import (
    CompiledGraph,
    PipeIndices, PipeBatchParams,
    PumpIndices, PumpBatchParams,
    ColdPlateIndices, ColdPlateBatchParams,
    ValveIndices, ValveBatchParams,
    RackMeta,
)


def compile_graph(config: LiquidrunConfig, device: str = "cpu") -> CompiledGraph:
    """
    Transform a validated .liquidrun config into a CompiledGraph
    with index tensors and batched parameter tensors.
    """
    g_vec = np.array(config.gravity, dtype=np.float64)
    g_mag = float(np.linalg.norm(g_vec))
    g_unit = g_vec / (g_mag + 1e-30)

    # 1. Build node_id → index mapping
    node_ids = [n.id for n in config.nodes]
    node_idx = {nid: i for i, nid in enumerate(node_ids)}
    N_nodes = len(node_ids)

    node_position = torch.tensor(
        [n.position_m for n in config.nodes], dtype=torch.float64
    )

    bc_mask = torch.tensor(
        [n.type == "pressure_bc" for n in config.nodes], dtype=torch.bool
    )
    junction_mask = ~bc_mask

    bc_P = torch.full((N_nodes,), config.initial_conditions.P_uniform_Pa, dtype=torch.float64)
    bc_T = torch.full((N_nodes,), float("nan"), dtype=torch.float64)
    for n in config.nodes:
        idx = node_idx[n.id]
        if n.bc is not None:
            bc_P[idx] = n.bc.P_Pa
            if n.bc.T_K is not None:
                bc_T[idx] = n.bc.T_K

    # 2. Group edges by type
    edges_by_type: dict[str, list[tuple[int, EdgeConfig]]] = {
        "pipe": [], "cold_plate": [], "valve": [], "pump": [],
    }
    all_edges = list(config.edges)
    edge_from_list, edge_to_list, edge_type_list = [], [], []
    type_to_int = {"pipe": 0, "cold_plate": 1, "valve": 2, "pump": 3}

    for i, e in enumerate(all_edges):
        edge_from_list.append(node_idx[e.from_node])
        edge_to_list.append(node_idx[e.to_node])
        edge_type_list.append(type_to_int[e.type])
        edges_by_type[e.type].append((i, e))

    N_edges = len(all_edges)
    edge_from = torch.tensor(edge_from_list, dtype=torch.long)
    edge_to = torch.tensor(edge_to_list, dtype=torch.long)
    edge_type = torch.tensor(edge_type_list, dtype=torch.long)

    # 3. Build incidence matrix: B[node, edge] = +1 if from, -1 if to
    row_idx = edge_from_list + edge_to_list
    col_idx = list(range(N_edges)) + list(range(N_edges))
    values = [1.0] * N_edges + [-1.0] * N_edges
    incidence = torch.sparse_coo_tensor(
        indices=torch.tensor([row_idx, col_idx], dtype=torch.long),
        values=torch.tensor(values, dtype=torch.float64),
        size=(N_nodes, N_edges),
    ).coalesce()

    # 4. Compile pipes
    pipe_indices, pipe_params, cells = _compile_pipes(
        edges_by_type["pipe"], node_idx, g_unit, g_mag, config, device
    )
    N_cells = cells["N_cells"]
    cell_edge_id = cells["cell_edge_id"]
    cell_ds = cells["cell_ds"]
    cell_volume = cells["cell_volume"]
    cell_dh = cells["cell_dh"]
    cell_position = cells["cell_position"]

    # 5. Compile pumps
    pump_indices, pump_params = _compile_pumps(
        edges_by_type["pump"], config.time.N_t, device
    )

    # 6. Compile cold_plates (stub for Phase 0 — no cp edges expected yet)
    cp_indices, cp_params, N_cp = _compile_cold_plates(
        edges_by_type["cold_plate"], config.time.N_t, device
    )

    # 7. Compile valves (stub for Phase 0)
    valve_indices, valve_params = _compile_valves(
        edges_by_type["valve"], config.time.N_t, device
    )

    # 8. Precompute per-node in/out edge lists
    node_in_edges: list[list[int]] = [[] for _ in range(N_nodes)]
    node_out_edges: list[list[int]] = [[] for _ in range(N_nodes)]
    for i in range(N_edges):
        node_out_edges[edge_from_list[i]].append(i)
        node_in_edges[edge_to_list[i]].append(i)

    return CompiledGraph(
        N_nodes=N_nodes, N_edges=N_edges, N_cells=N_cells,
        N_cp=N_cp, N_t=config.time.N_t,
        node_ids=node_ids, node_position=node_position,
        bc_mask=bc_mask, bc_P=bc_P, bc_T=bc_T, junction_mask=junction_mask,
        edge_from=edge_from, edge_to=edge_to, edge_type=edge_type,
        incidence=incidence,
        pipe_indices=pipe_indices, cp_indices=cp_indices,
        valve_indices=valve_indices, pump_indices=pump_indices,
        pipe_params=pipe_params, cp_params=cp_params,
        valve_params=valve_params, pump_params=pump_params,
        cell_edge_id=cell_edge_id, cell_ds=cell_ds,
        cell_volume=cell_volume, cell_dh=cell_dh, cell_position=cell_position,
        node_in_edges=node_in_edges, node_out_edges=node_out_edges,
        dt=config.time.dt_s, bdf_order=config.time.bdf_order,
        T_fluid_init=config.initial_conditions.T_fluid_K,
        T_solid_init=config.initial_conditions.T_solid_K,
        P_init=config.initial_conditions.P_uniform_Pa,
        solver_config=config.solver,
        rack_meta=[],
        fluid_rho=config.fluid.rho_kg_m3,
        fluid_cp=config.fluid.cp_J_kgK,
        fluid_mu=config.fluid.mu_Pa_s,
        g_mag=g_mag,
    )


def _compile_pipes(pipe_edges, node_idx, g_unit, g_mag, config, device):
    if not pipe_edges:
        empty = torch.zeros(0, dtype=torch.float64)
        return (
            None, None,
            {"N_cells": 0, "cell_edge_id": torch.zeros(0, dtype=torch.long),
             "cell_ds": empty, "cell_volume": empty,
             "cell_dh": empty, "cell_position": torch.zeros(0, 3, dtype=torch.float64)},
        )

    N_pipes = len(pipe_edges)
    edge_ids, node_from, node_to = [], [], []
    D_list, L_list, dh_list, K_list, A_list, rough_list = [], [], [], [], [], []
    all_cell_edge_id, all_cell_ds, all_cell_vol, all_cell_dh, all_cell_pos = [], [], [], [], []
    cell_starts, cell_ends = [], []
    cell_offset = 0

    for global_idx, edge in pipe_edges:
        p = edge.params
        edge_ids.append(global_idx)
        node_from.append(node_idx[edge.from_node])
        node_to.append(node_idx[edge.to_node])

        wps = np.array(p["waypoints_m"], dtype=np.float64)
        D = p["D_m"]
        roughness = p.get("roughness_m", 1.5e-6)
        n_cells = p.get("n_cells", 5)
        K_per_bend = p.get("K_minor_per_bend", 0.5)

        segments = np.diff(wps, axis=0)
        seg_lengths = np.linalg.norm(segments, axis=1)
        L_total = float(seg_lengths.sum())

        seg_dh = -segments @ g_unit
        dh_total = float(seg_dh.sum())

        n_bends = 0
        for j in range(len(segments) - 1):
            v1 = segments[j] / (seg_lengths[j] + 1e-30)
            v2 = segments[j + 1] / (seg_lengths[j + 1] + 1e-30)
            cos_a = float(np.dot(v1, v2))
            if cos_a < 0.95:
                n_bends += 1
        K_minor = n_bends * K_per_bend

        A = math.pi / 4.0 * D * D
        D_list.append(D)
        L_list.append(L_total)
        dh_list.append(dh_total)
        K_list.append(K_minor)
        A_list.append(A)
        rough_list.append(roughness)

        # Thermal cells along polyline
        cell_starts.append(cell_offset)
        cumlen = np.concatenate([[0.0], np.cumsum(seg_lengths)])
        boundaries = np.linspace(0, L_total, n_cells + 1)
        for c in range(n_cells):
            s_mid = (boundaries[c] + boundaries[c + 1]) / 2.0
            ds = boundaries[c + 1] - boundaries[c]
            vol = A * ds
            # Elevation change for this cell
            s0, s1 = boundaries[c], boundaries[c + 1]
            h0 = float(np.interp(s0, cumlen, np.concatenate([[0], np.cumsum(seg_dh)])))
            h1 = float(np.interp(s1, cumlen, np.concatenate([[0], np.cumsum(seg_dh)])))
            cell_dh_val = h1 - h0
            # Position
            pos = _interp_polyline(wps, cumlen, s_mid)

            all_cell_edge_id.append(len(D_list) - 1)  # local pipe index
            all_cell_ds.append(ds)
            all_cell_vol.append(vol)
            all_cell_dh.append(cell_dh_val)
            all_cell_pos.append(pos)
        cell_ends.append(cell_offset + n_cells)
        cell_offset += n_cells

    N_cells = cell_offset
    indices = PipeIndices(
        edge_ids=torch.tensor(edge_ids, dtype=torch.long),
        node_from=torch.tensor(node_from, dtype=torch.long),
        node_to=torch.tensor(node_to, dtype=torch.long),
        cell_start=torch.tensor(cell_starts, dtype=torch.long),
        cell_end=torch.tensor(cell_ends, dtype=torch.long),
    )
    params = PipeBatchParams(
        D=torch.tensor(D_list, dtype=torch.float64),
        L=torch.tensor(L_list, dtype=torch.float64),
        dh=torch.tensor(dh_list, dtype=torch.float64),
        K_minor=torch.tensor(K_list, dtype=torch.float64),
        A=torch.tensor(A_list, dtype=torch.float64),
        roughness=torch.tensor(rough_list, dtype=torch.float64),
    )
    cells = {
        "N_cells": N_cells,
        "cell_edge_id": torch.tensor(all_cell_edge_id, dtype=torch.long),
        "cell_ds": torch.tensor(all_cell_ds, dtype=torch.float64),
        "cell_volume": torch.tensor(all_cell_vol, dtype=torch.float64),
        "cell_dh": torch.tensor(all_cell_dh, dtype=torch.float64),
        "cell_position": torch.tensor(np.array(all_cell_pos), dtype=torch.float64),
    }
    return indices, params, cells


def _compile_pumps(pump_edges, N_t, device):
    if not pump_edges:
        return None, None
    edge_ids, node_from, node_to = [], [], []
    H0_list, a_list, rpm_list = [], [], []
    for global_idx, edge in pump_edges:
        p = edge.params
        edge_ids.append(global_idx)
        from dcdtuni.liquid.schema import LiquidrunConfig  # avoid circular
        # node_idx is not available here — we need to pass it in
        # This is handled by the caller passing (global_idx, edge)
        H0_list.append(p.get("H0_m", 15.0))
        a_list.append(p.get("a_coef", 1e6))
        rpm_list.append(p.get("rpm_ratio", 1.0))
    return (
        PumpIndices(
            edge_ids=torch.tensor(edge_ids, dtype=torch.long),
            node_from=torch.tensor([], dtype=torch.long),  # filled below
            node_to=torch.tensor([], dtype=torch.long),
        ),
        PumpBatchParams(
            H0=torch.tensor(H0_list, dtype=torch.float64),
            a_coef=torch.tensor(a_list, dtype=torch.float64),
            rpm_ratio=torch.tensor(rpm_list, dtype=torch.float64).unsqueeze(-1).expand(-1, N_t),
        ),
    )


def _compile_cold_plates(cp_edges, N_t, device):
    if not cp_edges:
        return None, None, 0
    return None, None, 0


def _compile_valves(valve_edges, N_t, device):
    if not valve_edges:
        return None, None
    return None, None


def _interp_polyline(pts, cumlen, s):
    """Interpolate 3D position along a polyline at arc-length s."""
    idx = np.searchsorted(cumlen, s, side="right") - 1
    idx = max(0, min(idx, len(pts) - 2))
    frac = (s - cumlen[idx]) / (cumlen[idx + 1] - cumlen[idx] + 1e-30)
    frac = max(0.0, min(1.0, frac))
    return (1 - frac) * pts[idx] + frac * pts[idx + 1]
```

Note: The `_compile_pumps` function has a bug with `node_from`/`node_to` — let me fix that. The pump edges need their node indices from the global `edge_from`/`edge_to` arrays, which are already built in the main `compile_graph` function. We should populate pump indices from there instead.

Let me correct: actually the `(global_idx, edge)` tuples carry the edge config, and we can compute node indices from `node_idx[edge.from_node]` and `node_idx[edge.to_node]`. I need to pass `node_idx` into the helper. Let me fix the function signature:

Replace the `_compile_pumps` function with:
```python
def _compile_pumps(pump_edges, node_idx, N_t, device):
    if not pump_edges:
        return None, None
    edge_ids, node_from, node_to = [], [], []
    H0_list, a_list, rpm_list = [], [], []
    for global_idx, edge in pump_edges:
        p = edge.params
        edge_ids.append(global_idx)
        node_from.append(node_idx[edge.from_node])
        node_to.append(node_idx[edge.to_node])
        H0_list.append(p.get("H0_m", 15.0))
        a_list.append(p.get("a_coef", 1e6))
        rpm_list.append(p.get("rpm_ratio", 1.0))
    return (
        PumpIndices(
            edge_ids=torch.tensor(edge_ids, dtype=torch.long),
            node_from=torch.tensor(node_from, dtype=torch.long),
            node_to=torch.tensor(node_to, dtype=torch.long),
        ),
        PumpBatchParams(
            H0=torch.tensor(H0_list, dtype=torch.float64),
            a_coef=torch.tensor(a_list, dtype=torch.float64),
            rpm_ratio=torch.tensor(rpm_list, dtype=torch.float64).unsqueeze(-1).expand(-1, N_t).clone(),
        ),
    )
```

And update the call site in `compile_graph` from:
```python
    pump_indices, pump_params = _compile_pumps(
        edges_by_type["pump"], config.time.N_t, device
    )
```
to:
```python
    pump_indices, pump_params = _compile_pumps(
        edges_by_type["pump"], node_idx, config.time.N_t, device
    )
```

Similarly fix `_compile_cold_plates` and `_compile_valves` to accept `node_idx`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_compiler.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/compiler.py src/backend/tests/test_liquid/test_compiler.py
git commit -m "feat(liquid): add compiler (.liquidrun → CompiledGraph)"
```

---

## Task 7: Update __init__.py public API

**Files:**
- Modify: `src/backend/liquidcooling/__init__.py`

- [ ] **Step 1: Update the public API**

`src/backend/liquidcooling/__init__.py`:
```python
"""
ODIL-based liquid cooling solver.

Non-causal optimization: all spacetime variables (P, m, T_w, T_s) are
nn.Parameters optimized simultaneously via Adam to minimize physics residuals.
"""

from dcdtuni.liquid.compiler import compile_graph
from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiled_graph import CompiledGraph

__all__ = ["compile_graph", "LiquidrunConfig", "CompiledGraph"]
```

- [ ] **Step 2: Verify**

Run: `conda run -n dcdtuni python -c "from dcdtuni.liquid import compile_graph, LiquidrunConfig, CompiledGraph; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run all Phase 0 tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v`
Expected: All tests PASS (bdf + schema + compiled_graph + compiler)

- [ ] **Step 4: Commit**

```bash
git add src/backend/liquidcooling/__init__.py
git commit -m "feat(liquid): wire up public API for Phase 0 scaffolding"
```

---

## Task 8: PipeBatchHandler — momentum residual

**Files:**
- Create: `src/backend/liquidcooling/handlers/pipe.py`

- [ ] **Step 1: Implement PipeBatchHandler (momentum only, no thermal)**

`src/backend/liquidcooling/handlers/pipe.py`:
```python
"""Pipe batch handler: Darcy-Weisbach momentum residual."""

from __future__ import annotations

import math

import torch
from torch import Tensor

from dcdtuni.liquid.bdf import smooth_abs
from dcdtuni.liquid.handlers.base import BaseHandler
from dcdtuni.liquid.compiled_graph import PipeIndices, PipeBatchParams


class PipeBatchHandler(BaseHandler):
    """
    Computes batch residuals for all pipe instances.

    Momentum residual (algebraic):
        r = (P_from - P_to) - dp_friction - dp_gravity
        dp_friction = (f*L/D + K_minor) * m*|m|_eps / (2*rho*A^2)
        dp_gravity = rho * g * dh
    """

    def __init__(
        self, params: PipeBatchParams,
        rho: float, mu: float, cp: float, g_mag: float,
    ):
        super().__init__()
        self.register_buffer("D", params.D)
        self.register_buffer("L", params.L)
        self.register_buffer("dh", params.dh)
        self.register_buffer("K_minor", params.K_minor)
        self.register_buffer("A", params.A)
        self.register_buffer("roughness", params.roughness)
        self.rho = rho
        self.mu = mu
        self.cp = cp
        self.g_mag = g_mag

    def compute_residuals(
        self, state: dict[str, Tensor], indices: PipeIndices,
        dt: float, bdf_order: int,
    ) -> dict[str, Tensor]:
        P_from = state["P"][indices.node_from]       # [N_pipes, N_t]
        P_to = state["P"][indices.node_to]           # [N_pipes, N_t]
        m = state["m"][indices.edge_ids]              # [N_pipes, N_t]

        rho = self.rho
        mu = self.mu
        D = self.D.unsqueeze(1)                       # [N_pipes, 1]
        L = self.L.unsqueeze(1)
        A = self.A.unsqueeze(1)
        K = self.K_minor.unsqueeze(1)
        dh = self.dh.unsqueeze(1)

        v = m / (rho * A)
        Re = rho * smooth_abs(v) * D / mu

        f_lam = 64.0 / (Re + 1e-8)
        f_turb = 0.3164 * (Re + 1e-8).pow(-0.25)
        sigma = torch.sigmoid((Re - 2300.0) / 200.0)
        f = (1.0 - sigma) * f_lam + sigma * f_turb

        dp_friction = (f * L / D + K) * m * smooth_abs(m) / (2.0 * rho * A * A)
        dp_gravity = rho * self.g_mag * dh

        r_momentum = (P_from - P_to) - dp_friction - dp_gravity

        return {"momentum": r_momentum}


if __name__ == "__main__":
    import math as _m

    torch.set_default_dtype(torch.float64)
    D_val = 0.025
    L_val = 1.0
    A_val = _m.pi / 4 * D_val**2
    rho, mu = 997.0, 0.00089

    params = PipeBatchParams(
        D=torch.tensor([D_val]), L=torch.tensor([L_val]),
        dh=torch.tensor([0.0]), K_minor=torch.tensor([0.0]),
        A=torch.tensor([A_val]), roughness=torch.tensor([1.5e-6]),
    )
    handler = PipeBatchHandler(params, rho=rho, mu=mu, cp=4182.0, g_mag=9.81)

    dp = 1000.0
    # Laminar: Q = dp * pi * D^4 / (128 * mu * L)
    Q_lam = dp * _m.pi * D_val**4 / (128 * mu * L_val)
    m_lam = Q_lam * rho

    N_t = 1
    P_from = torch.tensor([[200000.0]])
    P_to = torch.tensor([[200000.0 - dp]])
    m_tensor = torch.tensor([[m_lam]])

    from dcdtuni.liquid.compiled_graph import PipeIndices
    idx = PipeIndices(
        edge_ids=torch.tensor([0]),
        node_from=torch.tensor([0]),
        node_to=torch.tensor([1]),
        cell_start=torch.tensor([0]),
        cell_end=torch.tensor([0]),
    )
    state = {"P": torch.stack([P_from[0], P_to[0]]), "m": m_tensor}

    res = handler.compute_residuals(state, idx, dt=1.0, bdf_order=1)
    err = res["momentum"].abs().max().item()
    print(f"Momentum residual at analytical flow: {err:.4e}")
    assert err < 10.0, f"Residual too large: {err}"
    print("PASS: pipe handler self-test")
```

- [ ] **Step 2: Run self-test**

Run: `conda run -n dcdtuni python src/backend/liquidcooling/handlers/pipe.py`
Expected: `PASS: pipe handler self-test`

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/handlers/pipe.py
git commit -m "feat(liquid): add PipeBatchHandler with Darcy-Weisbach momentum"
```

---

## Task 9: PumpBatchHandler — affinity law residual

**Files:**
- Create: `src/backend/liquidcooling/handlers/pump.py`

- [ ] **Step 1: Implement PumpBatchHandler**

`src/backend/liquidcooling/handlers/pump.py`:
```python
"""Pump batch handler: affinity law momentum residual."""

from __future__ import annotations

import torch
from torch import Tensor

from dcdtuni.liquid.handlers.base import BaseHandler
from dcdtuni.liquid.compiled_graph import PumpIndices, PumpBatchParams


class PumpBatchHandler(BaseHandler):
    """
    Computes batch residuals for all pump instances.

    Momentum residual:
        Q = m / rho
        H(Q, N) = N^2 * (H0 - a * Q^2)
        dp_pump = rho * g * H
        r = (P_to - P_from) - dp_pump
    """

    def __init__(
        self, params: PumpBatchParams,
        rho: float, g_mag: float,
    ):
        super().__init__()
        self.register_buffer("H0", params.H0)
        self.register_buffer("a_coef", params.a_coef)
        self.register_buffer("rpm_ratio", params.rpm_ratio)
        self.rho = rho
        self.g_mag = g_mag

    def compute_residuals(
        self, state: dict[str, Tensor], indices: PumpIndices,
        dt: float, bdf_order: int,
    ) -> dict[str, Tensor]:
        P_from = state["P"][indices.node_from]       # [N_pumps, N_t]
        P_to = state["P"][indices.node_to]
        m = state["m"][indices.edge_ids]

        rho = self.rho
        Q = m / rho                                   # m^3/s

        N = self.rpm_ratio                            # [N_pumps, N_t]
        H0 = self.H0.unsqueeze(1)
        a = self.a_coef.unsqueeze(1)

        H = N * N * (H0 - a * Q * Q)
        dp_pump = rho * self.g_mag * H

        r_momentum = (P_to - P_from) - dp_pump

        return {"momentum": r_momentum}


if __name__ == "__main__":
    torch.set_default_dtype(torch.float64)

    rho = 997.0
    g = 9.81
    H0 = 15.0
    a = 1e6
    # At Q=0, dp = rho*g*H0 = 997*9.81*15 ≈ 146,708 Pa
    dp_expected = rho * g * H0

    params = PumpBatchParams(
        H0=torch.tensor([H0]),
        a_coef=torch.tensor([a]),
        rpm_ratio=torch.ones(1, 1),
    )
    handler = PumpBatchHandler(params, rho=rho, g_mag=g)

    P_from = torch.tensor([[100000.0]])
    P_to = torch.tensor([[100000.0 + dp_expected]])
    m_zero = torch.tensor([[0.0]])

    idx = PumpIndices(
        edge_ids=torch.tensor([0]),
        node_from=torch.tensor([0]),
        node_to=torch.tensor([1]),
    )
    state = {"P": torch.stack([P_from[0], P_to[0]]), "m": m_zero}

    res = handler.compute_residuals(state, idx, dt=1.0, bdf_order=1)
    err = res["momentum"].abs().max().item()
    print(f"Pump residual at Q=0: {err:.4e} (should be ~0)")
    assert err < 1.0, f"Residual too large: {err}"
    print("PASS: pump handler self-test")
```

- [ ] **Step 2: Run self-test**

Run: `conda run -n dcdtuni python src/backend/liquidcooling/handlers/pump.py`
Expected: `PASS: pump handler self-test`

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/handlers/pump.py
git commit -m "feat(liquid): add PumpBatchHandler with affinity law"
```

---

## Task 10: LiquidODILModel — hydraulic-only version

**Files:**
- Create: `src/backend/liquidcooling/model.py`

- [ ] **Step 1: Implement LiquidODILModel (P and m only)**

`src/backend/liquidcooling/model.py`:
```python
"""Top-level ODIL model: nn.Module that holds state variables and computes total loss."""

from __future__ import annotations

import torch
import torch.nn as nn
from torch import Tensor

from dcdtuni.liquid.compiled_graph import CompiledGraph
from dcdtuni.liquid.handlers.pipe import PipeBatchHandler
from dcdtuni.liquid.handlers.pump import PumpBatchHandler


class LiquidODILModel(nn.Module):
    """
    Non-causal ODIL model for liquid cooling.

    State variables P[N_nodes, N_t] and m[N_edges, N_t] are nn.Parameters.
    forward() computes L2 physics residuals and returns a loss dict.
    """

    def __init__(self, graph: CompiledGraph):
        super().__init__()
        self.graph = graph

        self.state = nn.ParameterDict({
            "P": nn.Parameter(
                torch.full((graph.N_nodes, graph.N_t), graph.P_init, dtype=torch.float64)
            ),
            "m": nn.Parameter(
                torch.zeros(graph.N_edges, graph.N_t, dtype=torch.float64)
            ),
        })

        self.handlers = nn.ModuleDict()
        if graph.pipe_indices is not None:
            self.handlers["pipe"] = PipeBatchHandler(
                graph.pipe_params,
                rho=graph.fluid_rho, mu=graph.fluid_mu,
                cp=graph.fluid_cp, g_mag=graph.g_mag,
            )
        if graph.pump_indices is not None:
            self.handlers["pump"] = PumpBatchHandler(
                graph.pump_params,
                rho=graph.fluid_rho, g_mag=graph.g_mag,
            )

        self.register_buffer("bc_mask", graph.bc_mask)
        self.register_buffer("bc_P", graph.bc_P)
        self.register_buffer("junction_mask", graph.junction_mask)

    def forward(self) -> dict[str, Tensor]:
        w = self.graph.solver_config.weights
        losses: dict[str, Tensor] = {}
        state = dict(self.state)

        # 1. Mass conservation at junction nodes
        inc_dense = self.graph.incidence.to_dense().to(state["m"].dtype)
        r_mass = inc_dense @ state["m"]                          # [N_nodes, N_t]
        r_mass_junc = r_mass[self.junction_mask]                 # [N_junc, N_t]
        if r_mass_junc.numel() > 0:
            losses["mass"] = w.mass * r_mass_junc.pow(2).mean()
        else:
            losses["mass"] = torch.tensor(0.0, dtype=torch.float64)

        # 2. Component residuals
        for comp_type, handler in self.handlers.items():
            idx = getattr(self.graph, f"{comp_type}_indices")
            residuals = handler.compute_residuals(
                state, idx, self.graph.dt, self.graph.bdf_order
            )
            for res_name, res_val in residuals.items():
                loss_key = f"{comp_type}_{res_name}"
                weight = getattr(w, res_name, 1.0)
                losses[loss_key] = weight * res_val.pow(2).mean()

        # 3. Boundary condition penalty
        P_bc_err = state["P"][self.bc_mask] - self.bc_P[self.bc_mask].unsqueeze(1)
        losses["bc_P"] = w.bc * P_bc_err.pow(2).mean()

        # 4. Total
        losses["total"] = sum(v for k, v in losses.items() if k != "total")

        return losses
```

- [ ] **Step 2: Verify import**

Run: `conda run -n dcdtuni python -c "from dcdtuni.liquid.model import LiquidODILModel; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/model.py
git commit -m "feat(liquid): add LiquidODILModel (hydraulic-only P+m)"
```

---

## Task 11: Solver — Adam optimization loop

**Files:**
- Create: `src/backend/liquidcooling/solver.py`

- [ ] **Step 1: Implement solver**

`src/backend/liquidcooling/solver.py`:
```python
"""Adam optimization loop for the ODIL liquid cooling model."""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

import torch

from dcdtuni.liquid.model import LiquidODILModel
from dcdtuni.liquid.schema import SolverConfig


@dataclass
class SolveLog:
    epoch: int
    total: float
    mass: float
    bc_P: float
    wall_time_s: float


def solve(
    model: LiquidODILModel,
    config: SolverConfig,
    log_dir: Optional[Path] = None,
    callback: Optional[Callable] = None,
    print_every: int = 100,
) -> list[SolveLog]:
    """
    Run Adam optimization to minimize physics residuals.

    Returns list of SolveLog entries (one per logged epoch).
    """
    optimizer = torch.optim.Adam(model.parameters(), lr=config.lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, patience=200, factor=0.5, min_lr=1e-6,
    )

    logs: list[SolveLog] = []
    t_start = time.time()

    for epoch in range(config.max_epochs):
        optimizer.zero_grad()
        losses = model()
        losses["total"].backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step(losses["total"])

        total_val = losses["total"].item()

        if epoch % print_every == 0 or epoch == config.max_epochs - 1:
            log = SolveLog(
                epoch=epoch,
                total=total_val,
                mass=losses.get("mass", torch.tensor(0.0)).item(),
                bc_P=losses.get("bc_P", torch.tensor(0.0)).item(),
                wall_time_s=time.time() - t_start,
            )
            logs.append(log)
            if callback:
                callback(asdict(log))
            _print_log(log, losses)

        if total_val < config.convergence_tol:
            break

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        _save_loss_csv(logs, log_dir / "loss_history.csv")
        torch.save(model.state_dict(), log_dir / "model_state.pt")

    return logs


def _print_log(log: SolveLog, losses: dict) -> None:
    parts = [f"[Epoch {log.epoch:5d}] total={log.total:.4e}"]
    for k, v in sorted(losses.items()):
        if k != "total":
            parts.append(f"{k}={v.item():.2e}")
    parts.append(f"t={log.wall_time_s:.1f}s")
    print("  ".join(parts))


def _save_loss_csv(logs: list[SolveLog], path: Path) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["epoch", "total", "mass", "bc_P", "wall_time_s"])
        writer.writeheader()
        for log in logs:
            writer.writerow(asdict(log))
```

- [ ] **Step 2: Verify import**

Run: `conda run -n dcdtuni python -c "from dcdtuni.liquid.solver import solve; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/solver.py
git commit -m "feat(liquid): add Adam solver with convergence monitoring"
```

---

## Task 12: End-to-end hydraulic loop test

**Files:**
- Create: `src/backend/tests/test_liquid/simple_loop.liquidrun`
- Create: `src/backend/tests/test_liquid/test_hydraulic.py`

- [ ] **Step 1: Create the test fixture — CDU → pipe → pipe → CDU**

`src/backend/tests/test_liquid/simple_loop.liquidrun`:
```json
{
  "version": "1.0",
  "source": "test",
  "projectName": "SimpleLoop",
  "gravity": [0, -9.81, 0],
  "fluid": {
    "rho_kg_m3": 997.0,
    "cp_J_kgK": 4182.0,
    "mu_Pa_s": 0.00089,
    "k_W_mK": 0.606
  },
  "time": {
    "dt_s": 1.0,
    "N_t": 1,
    "bdf_order": 1
  },
  "racks": [],
  "nodes": [
    {
      "id": "cdu_supply",
      "type": "pressure_bc",
      "position_m": [0, 0, 0],
      "bc": {"P_Pa": 200000, "T_K": 308.15}
    },
    {
      "id": "mid",
      "type": "junction",
      "position_m": [1, 0, 0]
    },
    {
      "id": "cdu_return",
      "type": "pressure_bc",
      "position_m": [2, 0, 0],
      "bc": {"P_Pa": 100000}
    }
  ],
  "edges": [
    {
      "id": "pipe_supply",
      "type": "pipe",
      "from": "cdu_supply",
      "to": "mid",
      "params": {
        "D_m": 0.025,
        "roughness_m": 1.5e-6,
        "n_cells": 5,
        "waypoints_m": [[0,0,0], [1,0,0]],
        "K_minor_per_bend": 0.5
      }
    },
    {
      "id": "pipe_return",
      "type": "pipe",
      "from": "mid",
      "to": "cdu_return",
      "params": {
        "D_m": 0.025,
        "roughness_m": 1.5e-6,
        "n_cells": 5,
        "waypoints_m": [[1,0,0], [2,0,0]],
        "K_minor_per_bend": 0.5
      }
    }
  ],
  "events": [],
  "initial_conditions": {
    "T_fluid_K": 308.15,
    "T_solid_K": 340.0,
    "P_uniform_Pa": 150000
  },
  "solver": {
    "optimizer": "adam",
    "lr": 0.01,
    "max_epochs": 5000,
    "convergence_tol": 1e-10,
    "weights": {
      "mass": 1.0,
      "momentum": 1e-5,
      "energy": 1.0,
      "bc": 100.0,
      "ic": 10.0
    }
  }
}
```

- [ ] **Step 2: Write the end-to-end test**

`src/backend/tests/test_liquid/test_hydraulic.py`:
```python
"""End-to-end test: steady-state hydraulic loop with ODIL solver."""

import json
import math
from pathlib import Path

import torch
import pytest

from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiler import compile_graph
from dcdtuni.liquid.model import LiquidODILModel
from dcdtuni.liquid.solver import solve

FIXTURE_DIR = Path(__file__).parent
torch.set_default_dtype(torch.float64)


def _load_and_solve(fixture_name: str, **solver_overrides):
    raw = json.loads((FIXTURE_DIR / fixture_name).read_text())
    config = LiquidrunConfig.model_validate(raw)
    graph = compile_graph(config)
    model = LiquidODILModel(graph)
    logs = solve(model, config.solver, print_every=500)
    return model, graph, logs


class TestSimpleLoop:
    """CDU(200kPa) → pipe(1m) → junction → pipe(1m) → CDU(100kPa)"""

    def test_converges(self):
        model, graph, logs = _load_and_solve("simple_loop.liquidrun")
        final_loss = logs[-1].total
        assert final_loss < 1e-4, f"Did not converge: {final_loss}"

    def test_mass_conservation(self):
        model, graph, logs = _load_and_solve("simple_loop.liquidrun")
        state = dict(model.state)
        m = state["m"].detach()
        # Both pipes should carry the same flow (series connection)
        m0 = m[0, 0].item()
        m1 = m[1, 0].item()
        assert abs(m0 - m1) / (abs(m0) + 1e-10) < 0.01, f"Flow mismatch: {m0} vs {m1}"

    def test_flow_positive(self):
        model, graph, logs = _load_and_solve("simple_loop.liquidrun")
        m = model.state["m"].detach()
        assert m[0, 0].item() > 0, "Flow should be positive (supply→return)"

    def test_junction_pressure_between_bcs(self):
        model, graph, logs = _load_and_solve("simple_loop.liquidrun")
        P = model.state["P"].detach()
        mid_idx = graph.node_ids.index("mid")
        P_mid = P[mid_idx, 0].item()
        assert 100000 < P_mid < 200000, f"Mid pressure {P_mid} out of range"

    def test_bc_pressures_enforced(self):
        model, graph, logs = _load_and_solve("simple_loop.liquidrun")
        P = model.state["P"].detach()
        supply_idx = graph.node_ids.index("cdu_supply")
        return_idx = graph.node_ids.index("cdu_return")
        assert abs(P[supply_idx, 0].item() - 200000) < 100
        assert abs(P[return_idx, 0].item() - 100000) < 100
```

- [ ] **Step 3: Run the test**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_hydraulic.py -v`
Expected: All 5 tests PASS (may take 10-30 seconds for Adam to converge)

- [ ] **Step 4: Commit**

```bash
git add src/backend/tests/test_liquid/simple_loop.liquidrun src/backend/tests/test_liquid/test_hydraulic.py
git commit -m "test(liquid): end-to-end hydraulic loop passes ODIL solver"
```

---

## Task 13: Final validation — run all tests

- [ ] **Step 1: Run full test suite for the liquid module**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`

Expected: All tests PASS:
- `test_bdf.py` — 10 tests
- `test_schema.py` — 7 tests
- `test_compiled_graph.py` — 3 tests
- `test_compiler.py` — 12 tests
- `test_hydraulic.py` — 5 tests

Total: ~37 tests, all PASS.

- [ ] **Step 2: Run handler self-tests**

```bash
conda run -n dcdtuni python src/backend/liquidcooling/bdf.py
conda run -n dcdtuni python src/backend/liquidcooling/handlers/pipe.py
conda run -n dcdtuni python src/backend/liquidcooling/handlers/pump.py
```

Expected: All print `PASS`

- [ ] **Step 3: Verify the full pipeline manually**

```bash
conda run -n dcdtuni python -c "
import json
from pathlib import Path
from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiler import compile_graph
from dcdtuni.liquid.model import LiquidODILModel
from dcdtuni.liquid.solver import solve

raw = json.loads(Path('src/backend/tests/test_liquid/simple_loop.liquidrun').read_text())
config = LiquidrunConfig.model_validate(raw)
graph = compile_graph(config)
print(f'Compiled: {graph.N_nodes} nodes, {graph.N_edges} edges, {graph.N_cells} cells')

model = LiquidODILModel(graph)
logs = solve(model, config.solver, print_every=500)
print(f'Final loss: {logs[-1].total:.4e}')

m = model.state['m'].detach()
print(f'Flow rates: pipe_supply={m[0,0].item():.6f} kg/s, pipe_return={m[1,0].item():.6f} kg/s')
P = model.state['P'].detach()
mid_idx = graph.node_ids.index('mid')
print(f'Mid junction P: {P[mid_idx,0].item():.0f} Pa')
print('DONE')
"
```

Expected output:
```
Compiled: 3 nodes, 2 edges, 10 cells
[Epoch     0] total=... mass=... pipe_momentum=... bc_P=...
[Epoch   500] total=...
...
Final loss: <small number>
Flow rates: pipe_supply=X.XXXXXX kg/s, pipe_return=X.XXXXXX kg/s
Mid junction P: ~150000 Pa
DONE
```

- [ ] **Step 4: Final commit with updated __init__.py**

Update `src/backend/liquidcooling/__init__.py` to include solver exports:
```python
"""
ODIL-based liquid cooling solver.

Non-causal optimization: all spacetime variables (P, m, T_w, T_s) are
nn.Parameters optimized simultaneously via Adam to minimize physics residuals.
"""

from dcdtuni.liquid.compiler import compile_graph
from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiled_graph import CompiledGraph
from dcdtuni.liquid.model import LiquidODILModel
from dcdtuni.liquid.solver import solve

__all__ = [
    "compile_graph", "LiquidrunConfig", "CompiledGraph",
    "LiquidODILModel", "solve",
]
```

```bash
git add src/backend/liquidcooling/__init__.py
git commit -m "feat(liquid): Phase 0+1 complete — ODIL hydraulic solver working"
```
