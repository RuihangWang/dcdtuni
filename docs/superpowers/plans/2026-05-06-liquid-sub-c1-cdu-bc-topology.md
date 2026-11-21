# Sub-C1: CDU Boundary Conditions + Topology Validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix CDU boundary condition semantics (Dirichlet supply T + free-outflow return T), expose supply pressure in the CDU panel, handle blind-end pipes, and add BFS topology validation ensuring CDU loops pass through cold plates.

**Architecture:** Add a `bc_T_mask` boolean tensor to `CompiledGraph` that distinguishes Dirichlet temperature nodes (supply, `T_K` present) from free-outflow nodes (return, `T_K` absent). The model's `_build_T_all_nodes()` treats free-outflow BC nodes as junctions for temperature mixing. Frontend changes add supply pressure to the CDU inspector, generate blind-end junction nodes, and validate loop topology via BFS.

**Tech Stack:** Python 3.10, PyTorch, Pydantic v2, vanilla JS (ES5), HTML

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/backend/liquidcooling/compiled_graph.py` | Add `bc_T_mask` field to CompiledGraph dataclass |
| `src/backend/liquidcooling/compiler.py` | Populate `bc_T_mask` from config node `T_K` presence |
| `src/backend/liquidcooling/model.py` | Use `bc_T_mask` in `_build_T_all_nodes()` for Dirichlet vs mixing |
| `src/backend/tests/test_liquid/test_compiled_graph.py` | Add `bc_T_mask` to `_minimal_graph()` helper |
| `src/backend/tests/test_liquid/test_bc_t_mask.py` (new) | Tests for bc_T_mask compilation + free-outflow temperature |
| `src/backend/tests/test_liquid/test_blind_end.py` (new) | Tests for blind-end pipe compilation + m=0 convergence |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | CDU inspector: supply pressure input + model default |
| `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js` | CDU pressures from config, blind-end nodes, topology BFS |

---

### Task 1: Add `bc_T_mask` to CompiledGraph + fix test helper

**Files:**
- Modify: `src/backend/liquidcooling/compiled_graph.py:130` (add field after `bc_T`)
- Modify: `src/backend/tests/test_liquid/test_compiled_graph.py:88-134` (add `bc_T_mask` to `_minimal_graph()`)

- [ ] **Step 1: Add `bc_T_mask` field to CompiledGraph**

In `src/backend/liquidcooling/compiled_graph.py`, add after line 130 (`bc_T: Tensor`):

```python
    bc_T_mask: Tensor       # [N_nodes] bool — True where T is prescribed (Dirichlet)
```

- [ ] **Step 2: Update `_minimal_graph()` test helper**

In `src/backend/tests/test_liquid/test_compiled_graph.py`, in the `_minimal_graph()` function, add `bc_T_mask` to the constructor call. The minimal graph has 2 nodes: node 0 has `bc_mask=True` (with explicit T_K implied), node 1 has `bc_mask=False`. Add after `bc_T=bc_T,`:

```python
        bc_T_mask=torch.tensor([True, False]),
```

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_compiled_graph.py -v --tb=short`
Expected: All tests in TestMinimalConstruction, TestToJson, TestIncidenceMatrix, TestRackMeta PASS

- [ ] **Step 4: Commit**

```bash
git add src/backend/liquidcooling/compiled_graph.py src/backend/tests/test_liquid/test_compiled_graph.py
git commit -m "feat(liquid): add bc_T_mask field to CompiledGraph"
```

---

### Task 2: Populate `bc_T_mask` in compiler

**Files:**
- Modify: `src/backend/liquidcooling/compiler.py:493-514` (node compilation loop)
- Modify: `src/backend/liquidcooling/compiler.py:672-718` (CompiledGraph constructor call)

- [ ] **Step 1: Write the failing test**

Create `src/backend/tests/test_liquid/test_bc_t_mask.py`:

```python
"""Tests for bc_T_mask compilation and free-outflow temperature."""
import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiler import compile_graph


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_bc_t_mask.py::TestBcTMaskCompilation -v --tb=short`
Expected: FAIL — `compile_graph()` missing `bc_T_mask` argument to CompiledGraph constructor

- [ ] **Step 3: Implement bc_T_mask in compiler**

In `src/backend/liquidcooling/compiler.py`, make two changes:

**Change 1:** In the node compilation section (around line 494-514), add `bc_T_mask` initialisation and populate it. Replace the entire block:

```python
    # ── 2. per-node arrays ──────────────────────────────────────────────

    node_position = torch.zeros(N_nodes, 3, dtype=dtype)
    bc_mask = torch.zeros(N_nodes, dtype=torch.bool)
    bc_P = torch.zeros(N_nodes, dtype=dtype)
    bc_T = torch.zeros(N_nodes, dtype=dtype)
    junction_mask = torch.zeros(N_nodes, dtype=torch.bool)

    ic = config.initial_conditions

    for i, node in enumerate(config.nodes):
        node_position[i] = torch.tensor(node.position_m, dtype=dtype)
        if node.type == "pressure_bc":
            bc_mask[i] = True
            if node.bc is not None:
                bc_P[i] = node.bc.P_Pa
                bc_T[i] = node.bc.T_K if node.bc.T_K is not None else ic.T_fluid_K
            else:
                bc_P[i] = ic.P_uniform_Pa
                bc_T[i] = ic.T_fluid_K
        elif node.type == "junction":
            junction_mask[i] = True
            bc_P[i] = ic.P_uniform_Pa
            bc_T[i] = ic.T_fluid_K
```

with:

```python
    # ── 2. per-node arrays ──────────────────────────────────────────────

    node_position = torch.zeros(N_nodes, 3, dtype=dtype)
    bc_mask = torch.zeros(N_nodes, dtype=torch.bool)
    bc_P = torch.zeros(N_nodes, dtype=dtype)
    bc_T = torch.zeros(N_nodes, dtype=dtype)
    bc_T_mask = torch.zeros(N_nodes, dtype=torch.bool)
    junction_mask = torch.zeros(N_nodes, dtype=torch.bool)

    ic = config.initial_conditions

    for i, node in enumerate(config.nodes):
        node_position[i] = torch.tensor(node.position_m, dtype=dtype)
        if node.type == "pressure_bc":
            bc_mask[i] = True
            if node.bc is not None:
                bc_P[i] = node.bc.P_Pa
                if node.bc.T_K is not None:
                    bc_T[i] = node.bc.T_K
                    bc_T_mask[i] = True
                else:
                    bc_T[i] = ic.T_fluid_K
                    bc_T_mask[i] = False
            else:
                bc_P[i] = ic.P_uniform_Pa
                bc_T[i] = ic.T_fluid_K
                bc_T_mask[i] = False
        elif node.type == "junction":
            junction_mask[i] = True
            bc_P[i] = ic.P_uniform_Pa
            bc_T[i] = ic.T_fluid_K
```

**Change 2:** In the CompiledGraph constructor call (around line 672-718), add `bc_T_mask=bc_T_mask,` after the `bc_T=bc_T,` line:

```python
        bc_T=bc_T,
        bc_T_mask=bc_T_mask,
        junction_mask=junction_mask,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_bc_t_mask.py::TestBcTMaskCompilation -v --tb=short`
Expected: All 4 tests PASS

- [ ] **Step 5: Run full test suite to verify nothing breaks**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All 136+ tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/backend/liquidcooling/compiler.py src/backend/tests/test_liquid/test_bc_t_mask.py
git commit -m "feat(liquid): populate bc_T_mask in compiler based on T_K presence"
```

---

### Task 3: Use `bc_T_mask` in model for free-outflow temperature

**Files:**
- Modify: `src/backend/liquidcooling/model.py:139-142` (register bc_T_mask buffer)
- Modify: `src/backend/liquidcooling/model.py:234-241` (_build_T_all_nodes BC section)
- Modify: `src/backend/liquidcooling/model.py:240` (junction iteration scope)

- [ ] **Step 1: Write the failing test**

Append to `src/backend/tests/test_liquid/test_bc_t_mask.py`:

```python
from dcdtuni.liquid.model import LiquidODILModel
from dcdtuni.liquid.solver import solve


class TestFreeOutflowTemperature:
    """Verify that return nodes without T_K get flow-weighted mixed temperature,
    not a fixed Dirichlet value."""

    @pytest.fixture(scope="class")
    def solved(self):
        raw = json.loads((FIXTURE_DIR / "thermal_loop.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        model = LiquidODILModel(graph)

        with torch.no_grad():
            for i, (is_bc, p_bc) in enumerate(zip(graph.bc_mask, graph.bc_P)):
                if is_bc:
                    model.state["P"].data[i, :] = p_bc.item()
            model.state["m"].data[:, :] = 1.0

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_bc_t_mask.py::TestFreeOutflowTemperature -v --tb=short`
Expected: FAIL — `test_return_temp_differs_from_init` fails because the return node currently gets a fixed Dirichlet temperature equal to T_fluid_init

- [ ] **Step 3: Implement free-outflow in model.py**

Make three changes to `src/backend/liquidcooling/model.py`:

**Change 1:** Register `bc_T_mask` as a buffer. After line 142 (`self.register_buffer("bc_T", graph.bc_T)`), add:

```python
        self.register_buffer("bc_T_mask", graph.bc_T_mask)
```

**Change 2:** In `_build_T_all_nodes()`, replace the BC node temperature assignment block (lines 236-238):

```python
        # BC nodes: fixed temperatures
        if self.bc_mask.any():
            bc_idx = self.bc_mask.nonzero(as_tuple=True)[0]
            T_all[bc_idx] = self.bc_T[bc_idx].unsqueeze(1).expand(-1, N_t)
```

with:

```python
        # Dirichlet temperature only where bc_T_mask is True
        if self.bc_T_mask.any():
            dirichlet_idx = self.bc_T_mask.nonzero(as_tuple=True)[0]
            T_all[dirichlet_idx] = self.bc_T[dirichlet_idx].unsqueeze(1).expand(-1, N_t)
```

**Change 3:** In `_build_T_all_nodes()`, expand the junction iteration to include free-outflow BC nodes. Replace the early return and junction list setup (lines 240-247):

```python
        if g.N_junctions == 0:
            return T_all

        # Compute junction temperatures iteratively (batch mode).
        # Each iteration computes all junction temperatures from the same
        # T_all, then updates them all at once.  Two iterations suffice
        # for most practical chain depths.
        junction_list = g.junction_indices.tolist()
```

with:

```python
        # Build mixing list: junctions + free-outflow BC nodes
        mixing_mask = g.junction_mask | (self.bc_mask & ~self.bc_T_mask)
        mixing_indices = torch.where(mixing_mask)[0]

        if mixing_indices.numel() == 0:
            return T_all

        # Compute mixing-node temperatures iteratively (batch mode).
        # Each iteration computes all mixing-node temperatures from the same
        # T_all, then updates them all at once.  Two iterations suffice
        # for most practical chain depths.
        junction_list = mixing_indices.tolist()
```

And in the batch update section near line 280, replace:

```python
            junction_stack = torch.stack(new_junction_temps, dim=0)
            idx_expanded = g.junction_indices.unsqueeze(1).expand(-1, N_t)
            T_all = T_all.scatter(0, idx_expanded, junction_stack)
```

with:

```python
            junction_stack = torch.stack(new_junction_temps, dim=0)
            idx_expanded = mixing_indices.unsqueeze(1).expand(-1, N_t)
            T_all = T_all.scatter(0, idx_expanded, junction_stack)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_bc_t_mask.py -v --tb=short`
Expected: All 7 tests PASS (4 compilation + 3 free-outflow)

- [ ] **Step 5: Run full test suite**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All tests PASS (existing tests should not change — supply nodes in all fixtures have explicit T_K, so their bc_T_mask=True and behaviour is unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/backend/liquidcooling/model.py src/backend/tests/test_liquid/test_bc_t_mask.py
git commit -m "feat(liquid): free-outflow temperature for BC nodes without T_K"
```

---

### Task 4: Blind-end pipe test fixture + compilation test

**Files:**
- Create: `src/backend/tests/test_liquid/blind_end.liquidrun`
- Create: `src/backend/tests/test_liquid/test_blind_end.py`

- [ ] **Step 1: Create blind-end test fixture**

Create `src/backend/tests/test_liquid/blind_end.liquidrun`:

```json
{
  "version": "1.0",
  "source": "test",
  "projectName": "BlindEndTest",
  "gravity": [0, -9.81, 0],
  "fluid": {
    "rho_kg_m3": 997.0,
    "cp_J_kgK": 4182.0,
    "mu_Pa_s": 0.00089,
    "k_W_mK": 0.606
  },
  "time": {
    "dt_s": 1.0,
    "N_t": 3,
    "bdf_order": 1
  },
  "racks": [],
  "nodes": [
    {"id": "supply", "type": "pressure_bc", "position_m": [0,0,0], "bc": {"P_Pa": 200000, "T_K": 308.15}},
    {"id": "mid", "type": "junction", "position_m": [1,0,0]},
    {"id": "return", "type": "pressure_bc", "position_m": [2,0,0], "bc": {"P_Pa": 100000}},
    {"id": "dead_end", "type": "junction", "position_m": [1,1,0]}
  ],
  "edges": [
    {
      "id": "pipe_main_a", "type": "pipe",
      "from": "supply", "to": "mid",
      "params": {"D_m": 0.025, "roughness_m": 1.5e-6, "n_cells": 3,
                 "waypoints_m": [[0,0,0],[1,0,0]], "K_minor_per_bend": 0.5}
    },
    {
      "id": "pipe_main_b", "type": "pipe",
      "from": "mid", "to": "return",
      "params": {"D_m": 0.025, "roughness_m": 1.5e-6, "n_cells": 3,
                 "waypoints_m": [[1,0,0],[2,0,0]], "K_minor_per_bend": 0.5}
    },
    {
      "id": "pipe_blind", "type": "pipe",
      "from": "mid", "to": "dead_end",
      "params": {"D_m": 0.025, "roughness_m": 1.5e-6, "n_cells": 2,
                 "waypoints_m": [[1,0,0],[1,1,0]], "K_minor_per_bend": 0.5}
    }
  ],
  "events": [],
  "initial_conditions": {"T_fluid_K": 308.15, "T_solid_K": 340.0, "P_uniform_Pa": 150000},
  "solver": {
    "optimizer": "adam", "lr": 0.01, "max_epochs": 5000, "convergence_tol": 1e-6,
    "weights": {"mass": 1.0, "momentum": 1e-5, "energy": 1.0, "bc": 100.0, "ic": 10.0}
  }
}
```

- [ ] **Step 2: Write the test**

Create `src/backend/tests/test_liquid/test_blind_end.py`:

```python
"""Tests for blind-end pipe handling: dead-end junction → m ≈ 0."""
import json
from pathlib import Path

import pytest
import torch

torch.set_default_dtype(torch.float64)

from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiler import compile_graph
from dcdtuni.liquid.model import LiquidODILModel
from dcdtuni.liquid.solver import solve

FIXTURE_DIR = Path(__file__).parent


class TestBlindEnd:
    """A dead-end junction (single edge) should have m ≈ 0 after ODIL solve."""

    @pytest.fixture(scope="class")
    def solved(self):
        raw = json.loads((FIXTURE_DIR / "blind_end.liquidrun").read_text())
        config = LiquidrunConfig.model_validate(raw)
        graph = compile_graph(config)
        model = LiquidODILModel(graph)

        with torch.no_grad():
            for i, (is_bc, p_bc) in enumerate(zip(graph.bc_mask, graph.bc_P)):
                if is_bc:
                    model.state["P"].data[i, :] = p_bc.item()
            model.state["m"].data[:, :] = 1.0

        logs = solve(model, config.solver, print_every=500)
        return model, graph, logs

    def test_converges(self, solved):
        model, graph, logs = solved
        assert logs[-1].total < 1.0

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
        assert m_blind < 0.01, (
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
```

- [ ] **Step 3: Run test to verify it passes**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_blind_end.py -v --tb=short`
Expected: All 5 tests PASS (the fixture already has the dead-end topology — no frontend changes needed for this backend-only test)

- [ ] **Step 4: Commit**

```bash
git add src/backend/tests/test_liquid/blind_end.liquidrun src/backend/tests/test_liquid/test_blind_end.py
git commit -m "test(liquid): add blind-end pipe fixture and convergence tests"
```

---

### Task 5: Frontend — CDU supply pressure in inspector panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:2872-2873` (CDU model defaults)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:10272-10274` (CDU inspector panel)

- [ ] **Step 1: Add `supplyPressure` default to CDU model creation**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, in the `createCduModel()` function (line 2872), add `supplyPressure: 250,` after `supplyTemperature: 35,`:

```javascript
            liquidCooling: {
                supplyTemperature: 35,
                supplyPressure: 250,
                totalFlowRate: 60,
```

Also add `supplyPressure: 250,` to any other CDU liquidCooling default objects in the file (around lines 3193, 3304 — search for `supplyTemperature: 35` and add `supplyPressure: 250,` after each).

- [ ] **Step 2: Add supply pressure input to CDU inspector panel**

In `renderCduModelInspector()` (line 10273), add a new `prop-row` for supply pressure after the supply temperature row. Change:

```javascript
                <div class="prop-row"><label>${t('liquid.supply_temp')}</label><input type="number" step="0.5" value="${lc.supplyTemperature ?? 35}" onchange="window.updateCduLiquidParam('${model.id}', 'supplyTemperature', parseFloat(this.value))"></div>
                <div class="prop-row"><label>${t('liquid.flow_rate')}</label>
```

to:

```javascript
                <div class="prop-row"><label>${t('liquid.supply_temp')}</label><input type="number" step="0.5" value="${lc.supplyTemperature ?? 35}" onchange="window.updateCduLiquidParam('${model.id}', 'supplyTemperature', parseFloat(this.value))"></div>
                <div class="prop-row"><label>${t('liquid.supply_pressure')}</label><input type="number" step="10" min="100" value="${lc.supplyPressure ?? 250}" onchange="window.updateCduLiquidParam('${model.id}', 'supplyPressure', parseFloat(this.value))"></div>
                <div style="font-size:0.68rem; color:var(--text-dim); margin:-4px 0 4px 8px;">Return pressure: 101.325 kPa (atmospheric, fixed)</div>
                <div class="prop-row"><label>${t('liquid.flow_rate')}</label>
```

- [ ] **Step 3: Add i18n key for supply pressure**

In `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`, add (in the `liquid.*` section):

```json
"liquid.supply_pressure": "供水压力 (kPa)"
```

In `STLGeneratorForOpenFoam/web/i18n/en-US.json`, add:

```json
"liquid.supply_pressure": "Supply Pressure (kPa)"
```

- [ ] **Step 4: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat(liquid): add supply pressure input to CDU inspector panel"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "fix: update submodule — CDU supply pressure in inspector panel"
```

---

### Task 6: Frontend — CDU pressure compilation + return node T_K omission

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js:205-222` (CDU node compilation)

- [ ] **Step 1: Update CDU supply node to use `supplyPressure` from config**

In `liquid_graphgen.js`, in the CDU node compilation section (around lines 200-222), replace:

```javascript
            addNode({
                id:          supplyId,
                type:        'pressure_bc',
                position_m:  _getPortPosition(models, cdu.id + '.supply'),
                bc: {
                    P_Pa: 250000,
                    T_K:  supplyT_K
                }
            });

            addNode({
                id:          returnId,
                type:        'pressure_bc',
                position_m:  _getPortPosition(models, cdu.id + '.return'),
                bc: {
                    P_Pa: 100000
                }
            });
```

with:

```javascript
            var supplyP_Pa = (lc.supplyPressure != null ? lc.supplyPressure : 250) * 1000;

            addNode({
                id:          supplyId,
                type:        'pressure_bc',
                position_m:  _getPortPosition(models, cdu.id + '.supply'),
                bc: {
                    P_Pa: supplyP_Pa,
                    T_K:  supplyT_K
                }
            });

            addNode({
                id:          returnId,
                type:        'pressure_bc',
                position_m:  _getPortPosition(models, cdu.id + '.return'),
                bc: {
                    P_Pa: 101325
                }
            });
```

- [ ] **Step 2: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_graphgen.js
git commit -m "feat(liquid): CDU supply pressure from config, return at 101325 Pa"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "fix: update submodule — CDU pressure compilation from config"
```

---

### Task 7: Frontend — Blind-end pipe handling in graph generation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js:420-473` (pipe compilation section)

- [ ] **Step 1: Add blind-end node creation in pipe compilation**

In `liquid_graphgen.js`, in the pipe segment compilation section (around line 425-473), modify the `pipeModels.forEach` callback. Replace:

```javascript
        pipeModels.forEach(function (pm) {
            var p = pm.pipe;
            if (!p.startPortId || !p.endPortId) return;

            var fromNodeId = _portToNodeId(p.startPortId);
            var toNodeId   = _portToNodeId(p.endPortId);

            // Ensure nodes exist for pipe endpoints
            addNode({
                id:         fromNodeId,
                type:       'junction',
                position_m: _getPortPosition(models, p.startPortId)
            });
            addNode({
                id:         toNodeId,
                type:       'junction',
                position_m: _getPortPosition(models, p.endPortId)
            });
```

with:

```javascript
        pipeModels.forEach(function (pm) {
            var p = pm.pipe;
            if (!p.startPortId || !p.endPortId) return;

            var blindEnds = p.blindEnds || { start: false, end: false };
            var fromNodeId, toNodeId;

            if (blindEnds.start) {
                fromNodeId = pm.name + '_blind_start';
                var wps = p.waypoints || [];
                var startPos = wps.length > 0
                    ? [wps[0].x / 1000.0, wps[0].y / 1000.0, wps[0].z / 1000.0]
                    : _getPortPosition(models, p.startPortId);
                addNode({
                    id:         fromNodeId,
                    type:       'junction',
                    position_m: startPos
                });
            } else {
                fromNodeId = _portToNodeId(p.startPortId);
                addNode({
                    id:         fromNodeId,
                    type:       'junction',
                    position_m: _getPortPosition(models, p.startPortId)
                });
            }

            if (blindEnds.end) {
                toNodeId = pm.name + '_blind_end';
                var wps2 = p.waypoints || [];
                var endPos = wps2.length > 0
                    ? [wps2[wps2.length - 1].x / 1000.0, wps2[wps2.length - 1].y / 1000.0, wps2[wps2.length - 1].z / 1000.0]
                    : _getPortPosition(models, p.endPortId);
                addNode({
                    id:         toNodeId,
                    type:       'junction',
                    position_m: endPos
                });
            } else {
                toNodeId = _portToNodeId(p.endPortId);
                addNode({
                    id:         toNodeId,
                    type:       'junction',
                    position_m: _getPortPosition(models, p.endPortId)
                });
            }
```

The rest of the pipe edge creation (waypoint conversion, edge push) stays the same — it uses `fromNodeId` and `toNodeId` which are now correctly resolved.

- [ ] **Step 2: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_graphgen.js
git commit -m "feat(liquid): blind-end pipe creates junction node in graph"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "fix: update submodule — blind-end pipe graph compilation"
```

---

### Task 8: Frontend — BFS topology validation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js:114-162` (append to `_validateGraph`)
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js:540-544` (change warning-only to error-blocking)

- [ ] **Step 1: Add CDU loop validation to `_validateGraph()`**

In `liquid_graphgen.js`, add a new validation function before `_validateGraph`:

```javascript
    /**
     * BFS check: verify that supply can reach return via at least one cold_plate edge.
     */
    function _validateCduLoop(graph, supplyId, returnId, cduName) {
        var errors = [];

        // Build undirected adjacency: node → [{neighbor, edgeType}]
        var adj = {};
        var i, e;
        for (i = 0; i < graph.nodes.length; i++) {
            adj[graph.nodes[i].id] = [];
        }
        for (i = 0; i < graph.edges.length; i++) {
            e = graph.edges[i];
            if (adj[e.from]) adj[e.from].push({ node: e.to, type: e.type });
            if (adj[e.to]) adj[e.to].push({ node: e.from, type: e.type });
        }

        // BFS tracking both node and "passed cold_plate" state
        var visited = {};
        var queue = [{ node: supplyId, passedCP: false }];
        var reachedReturn = false;
        var reachedReturnViaCP = false;

        while (queue.length > 0) {
            var cur = queue.shift();
            var stateKey = cur.node + ':' + (cur.passedCP ? '1' : '0');
            if (visited[stateKey]) continue;
            visited[stateKey] = true;

            var neighbors = adj[cur.node] || [];
            for (var j = 0; j < neighbors.length; j++) {
                var nb = neighbors[j];
                var newPassedCP = cur.passedCP || (nb.type === 'cold_plate');

                if (nb.node === returnId) {
                    reachedReturn = true;
                    if (newPassedCP) reachedReturnViaCP = true;
                } else {
                    queue.push({ node: nb.node, passedCP: newPassedCP });
                }
            }
        }

        if (!reachedReturn) {
            errors.push(cduName + ': CDU loop broken — cannot reach return from supply');
        } else if (!reachedReturnViaCP) {
            errors.push(cduName + ': CDU short circuit — supply reaches return without passing through any cold plate');
        }

        return errors;
    }
```

Then in `_validateGraph()`, after the existing check 4 (duplicate edge IDs, around line 160), add:

```javascript
        // 5. CDU loop validation — each CDU supply must reach its return via cold_plate
        var nodeList = graph.nodes;
        var cduSupplyNodes = [];
        for (i = 0; i < nodeList.length; i++) {
            n = nodeList[i];
            if (n.type === 'pressure_bc' && n.id.indexOf('_supply') >= 0) {
                var baseName = n.id.replace('_supply', '');
                var returnId = baseName + '_return';
                // Verify return node exists
                var hasReturn = false;
                for (var k = 0; k < nodeList.length; k++) {
                    if (nodeList[k].id === returnId) { hasReturn = true; break; }
                }
                if (hasReturn) {
                    var loopErrors = _validateCduLoop(graph, n.id, returnId, baseName);
                    for (var le = 0; le < loopErrors.length; le++) {
                        errors.push(loopErrors[le]);
                    }
                }
            }
        }
```

- [ ] **Step 2: Change validation from warning-only to error-blocking**

In `buildLiquidrunJson`, replace the validation section (around line 540-546):

```javascript
        var errors = _validateGraph(graph);
        if (errors.length > 0) {
            console.warn('[liquid_graphgen] Validation warnings (' + errors.length + '):');
            errors.forEach(function (msg) { console.warn('  • ' + msg); });
        }

        return graph;
```

with:

```javascript
        var errors = _validateGraph(graph);
        if (errors.length > 0) {
            var msg = 'Liquid graph validation failed:\n\n' + errors.join('\n');
            console.error('[liquid_graphgen] ' + msg);
            alert(msg);
            return null;
        }

        return graph;
```

- [ ] **Step 3: Handle null return in exportLiquidrun()**

In `liquid_cooling.js`, the `exportLiquidrun()` function calls `buildLiquidrunJson()`. It should handle the `null` return. Check that the existing code in `exportLiquidrun` already guards against a null graph (it checks `if (!graph)` before posting). If not, add a guard:

```javascript
        var graph = window.buildLiquidrunJson(state.models, state.liquidCooling);
        if (!graph) return null;
```

- [ ] **Step 4: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_graphgen.js web/js/liquid_cooling.js
git commit -m "feat(liquid): BFS topology validation — CDU loop must pass through cold plate"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "fix: update submodule — CDU loop topology validation"
```

---

### Task 9: Final regression test

**Files:** None (verification only)

- [ ] **Step 1: Run full backend test suite**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All tests PASS (136 existing + 12 new = ~148 total)

- [ ] **Step 2: Verify test count**

Check output for total test count. Should include:
- `test_bc_t_mask.py`: 7 tests (4 compilation + 3 free-outflow)
- `test_blind_end.py`: 5 tests
- All existing tests unchanged

- [ ] **Step 3: Commit (if any fixups needed)**

If any test required a fixup, commit the fix.
