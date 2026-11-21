# Liquid Solver Phase 2 Correctness Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three correctness issues: T-junction minor losses, valve Kv unit conversion, and junction temperature iteration depth.

**Architecture:** Three independent fixes in compiler/handler/model layers. Each fix is self-contained and testable.

**Tech Stack:** Python 3.10, PyTorch (float64), pytest

**Spec:** `docs/superpowers/specs/2026-05-19-liquid-phase2-correctness-design.md`

---

### Task 1: Valve Kv Unit Fix (smallest, most self-contained)

**Files:**
- Modify: `src/backend/liquidcooling/handlers/valve.py:95-103`
- Test: `src/backend/tests/test_liquid/test_bc_ic.py` (add valve test)

- [ ] **Step 1: Write failing test**

Add to `src/backend/tests/test_liquid/test_bc_ic.py`:

```python
class TestValveKvUnits:
    def test_valve_flow_isa_equation(self):
        """Valve with Kv=2.5, dP=1 bar should give correct ISA flow."""
        import torch
        torch.set_default_dtype(torch.float64)

        # ISA equation: Q[m³/h] = Kv * sqrt(dP[bar] / (rho/1000))
        # For water: rho=997, dP=1 bar: Q = 2.5 * sqrt(1.0 / 0.997) = 2.504 m³/h
        # m = Q * rho / 3600 = 2.504 * 997 / 3600 = 0.6936 kg/s
        Kv = 2.5
        dP_bar = 1.0
        rho = 997.0
        Q_expected = Kv * (dP_bar / (rho / 1000.0)) ** 0.5  # m³/h
        m_expected = Q_expected * rho / 3600.0  # kg/s

        from dcdtuni.liquid.handlers.valve import ValveBatchHandler
        from dcdtuni.liquid.compiled_graph import ValveBatchParams, ValveIndices

        # Create minimal valve setup
        params = ValveBatchParams(
            Kv_max=torch.tensor([Kv]),
            opening=torch.tensor([1.0]),
            characteristic=torch.tensor([0]),  # linear
        )
        indices = ValveIndices(
            edge_ids=torch.tensor([0], dtype=torch.long),
            node_from=torch.tensor([0], dtype=torch.long),
            node_to=torch.tensor([1], dtype=torch.long),
        )
        handler = ValveBatchHandler(params, rho=rho)

        P = torch.tensor([[250000.0], [150000.0]])  # 1 bar dP
        m = torch.tensor([[0.5]])  # some initial guess
        state = {"P": P, "m": m}

        res = handler.compute_residuals(state, indices, dt=1.0, bdf_order=1)
        r_mom = res["momentum"]
        # r_momentum = m - m_model → at correct m, residual should be 0
        # m_model should be ~0.694 kg/s
        m_model = m[0, 0].item() - r_mom[0, 0].item()
        assert abs(m_model - m_expected) < 0.01, (
            f"Valve m_model={m_model:.4f}, expected={m_expected:.4f} (ISA with Kv={Kv}, dP=1bar)"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestValveKvUnits -v`
Expected: FAIL (current code gives ~316x wrong flow)

- [ ] **Step 3: Fix valve.py**

In `src/backend/liquidcooling/handlers/valve.py`, find the momentum residual computation (around lines 95-103). Replace:

```python
rho_kPa = self.rho / 1000.0
m_model = Kv * torch.sign(dP) * (dP.abs() / rho_kPa + 1e-10).sqrt()
```

With:

```python
dP_bar = dP.abs() / 1e5
rho_rel = self.rho / 1000.0
Q_m3h = Kv * (dP_bar / rho_rel + 1e-10).sqrt()
m_model = torch.sign(dP) * Q_m3h * self.rho / 3600.0
```

Also update the docstring at line 17 to clarify the unit conversion.

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestValveKvUnits -v`
Expected: PASS

- [ ] **Step 5: Run regression**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/backend/liquidcooling/handlers/valve.py src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "fix(liquid): valve Kv unit conversion — Pa→bar + m³/h→kg/s per ISA standard"
```

---

### Task 2: T-Junction Minor Loss Coefficients

**Files:**
- Modify: `src/backend/liquidcooling/compiler.py:140-198`
- Test: `src/backend/tests/test_liquid/test_bc_ic.py`

- [ ] **Step 1: Write failing test**

```python
class TestTJunctionKMinor:
    def test_branch_pipes_have_tee_K(self):
        """Manifold branch pipes should have K_minor including tee contribution."""
        graph, _ = _make_graph("single_rack.liquidrun")
        # Branch pipes (edges 3-8) connect to manifold nodes (degree >= 3)
        # They should have K_minor > 0 from the tee junction
        assert graph.pipe_params is not None
        for local_i in range(graph.pipe_params.K_minor.shape[0]):
            edge_id = graph.pipe_indices.edge_ids[local_i].item()
            edge_str = graph.edge_str_ids[edge_id]
            K = graph.pipe_params.K_minor[local_i].item()
            if "branch" in edge_str:
                assert K > 0, f"Branch pipe {edge_str} should have K_minor > 0 (tee), got {K}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestTJunctionKMinor -v`
Expected: FAIL (branch pipes currently have K_minor=0 because no bends in their waypoints)

- [ ] **Step 3: Implement tee detection in compiler.py**

In `src/backend/liquidcooling/compiler.py`, after the existing K_minor bend computation (around line 189), add a second pass that checks node connectivity:

```python
# --- T-junction minor loss: add K_tee for pipes connected to high-degree nodes ---
K_TEE_DEFAULT = 0.9  # standard tee branch, per Idelchik
node_degree = [len(node_in_edges[i]) + len(node_out_edges[i]) for i in range(N_nodes)]

for pipe_local_i, pipe_global_edge in enumerate(pipe_edge_ids):
    from_n = edge_from_list[pipe_global_edge]
    to_n = edge_to_list[pipe_global_edge]
    K_tee_param = float(edge_configs[pipe_global_edge].params.get("K_tee_branch", K_TEE_DEFAULT))
    tee_count = 0
    if node_degree[from_n] >= 3:
        tee_count += 1
    if node_degree[to_n] >= 3:
        tee_count += 1
    K_minor_list[pipe_local_i] += tee_count * K_tee_param
```

This adds K_tee for each end of the pipe that connects to a tee junction (degree >= 3). A pipe between two tees gets 2 × K_tee.

Note: `edge_configs` needs to be accessible at this point. You'll need to read the exact variable names from the compiler code.

- [ ] **Step 4: Run test + regression**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/test_bc_ic.py::TestTJunctionKMinor -v`
Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v`
Expected: All PASS (existing tests may need threshold adjustments since K_minor changes affect momentum convergence)

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/compiler.py src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "feat(liquid): T-junction minor loss K=0.9 at manifold branch points"
```

---

### Task 3: Junction Temperature Iteration Depth

**Files:**
- Modify: `src/backend/liquidcooling/compiled_graph.py` — add `max_junction_depth` field
- Modify: `src/backend/liquidcooling/compiler.py` — compute max junction depth
- Modify: `src/backend/liquidcooling/model.py:303` — use computed depth
- Test: `src/backend/tests/test_liquid/test_bc_ic.py`

- [ ] **Step 1: Write failing test**

```python
class TestJunctionDepth:
    def test_depth_computed(self):
        """Compiled graph should have max_junction_depth > 0 for networks with junctions."""
        graph, _ = _make_graph("single_rack.liquidrun")
        assert hasattr(graph, "max_junction_depth")
        assert graph.max_junction_depth >= 2  # single_rack has at least 2-deep chains

    def test_minimal_no_junctions(self):
        """Minimal graph (no junctions) should have max_junction_depth = 0."""
        graph, _ = _make_graph("minimal.liquidrun")
        assert hasattr(graph, "max_junction_depth")
        assert graph.max_junction_depth == 0
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with `AttributeError: 'CompiledGraph' has no attribute 'max_junction_depth'`

- [ ] **Step 3: Add max_junction_depth to CompiledGraph**

In `src/backend/liquidcooling/compiled_graph.py`, add field after `N_junctions`:
```python
max_junction_depth: int
```

- [ ] **Step 4: Compute depth in compiler.py**

At the end of the compiler (before packing CompiledGraph), add BFS from non-junction nodes (pipe outlets, cold plate outlets, BC nodes) into the junction subgraph:

```python
# Compute max junction chain depth
max_junction_depth = 0
if junction_indices:
    junction_set = set(junction_indices)
    visited_depth = {}
    from collections import deque
    queue = deque()
    # Seed: junction nodes that have at least one non-junction neighbor
    for j_idx in junction_indices:
        for e in node_in_edges[j_idx]:
            from_n = edge_from_list[e]
            if from_n not in junction_set:
                if j_idx not in visited_depth or visited_depth[j_idx] > 1:
                    visited_depth[j_idx] = 1
                    queue.append(j_idx)
    # BFS through junction-only chains
    while queue:
        n = queue.popleft()
        d = visited_depth[n]
        max_junction_depth = max(max_junction_depth, d)
        for e in node_in_edges[n]:
            from_n = edge_from_list[e]
            if from_n in junction_set and from_n not in visited_depth:
                visited_depth[from_n] = d + 1
                queue.append(from_n)
```

Pass `max_junction_depth` to CompiledGraph constructor.

- [ ] **Step 5: Update model.py to use computed depth**

In `src/backend/liquidcooling/model.py`, line 303, change:
```python
for _iter in range(2):
```
To:
```python
for _iter in range(max(g.max_junction_depth, 1)):
```

- [ ] **Step 6: Run tests + regression**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/backend/liquidcooling/compiled_graph.py src/backend/liquidcooling/compiler.py src/backend/liquidcooling/model.py src/backend/tests/test_liquid/test_bc_ic.py
git commit -m "fix(liquid): junction temp iteration uses computed max_junction_depth"
```

---

### Task 4: Final Regression

- [ ] **Step 1: Run full test suite**

Run: `conda run -n dcdtuni pytest src/backend/tests/test_liquid/ -v`
Expected: All tests PASS

- [ ] **Step 2: Commit any threshold adjustments**

```bash
git add -A && git commit -m "chore(liquid): phase 2 regression adjustments"
```
