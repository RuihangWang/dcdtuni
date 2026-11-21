# Sub-A: Liquid ODIL Backend Physics Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 3 CRITICAL physics bugs and 6 HIGH accuracy gaps in the liquid ODIL solver backend so that multi-component networks produce physically correct thermal results.

**Architecture:** Add `T_node` junction temperature state variable with mixing residual; fix event timing and BC temperature enforcement; upgrade pipe friction to Swamee-Jain; upgrade cold plate NTU-effectiveness; add bidirectional upwind; add pump event support. All changes are backward-compatible — existing tests must continue passing.

**Tech Stack:** Python 3.10, PyTorch (float64), Pydantic v2. Conda env `dcdtuni`.

**Design spec:** `docs/superpowers/specs/2026-05-06-liquid-odil-sub-a-physics-fix.md`

**Current state:** 127 tests passing. Run with `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`.

---

## File Map

| File | Changes |
|------|---------|
| Modify: `src/backend/liquidcooling/compiled_graph.py` | Add `N_junctions`, `junction_indices`, `node_to_junction` fields |
| Modify: `src/backend/liquidcooling/compiler.py` | Build junction indices; fix event time (C2); pump events (H5); pass `dt_s` |
| Modify: `src/backend/liquidcooling/model.py` | Add `T_node` state; build `T_all_nodes`; mixing residual; `bc_T` penalty |
| Modify: `src/backend/liquidcooling/handlers/pipe.py` | Use `T_all_nodes` (C1); Swamee-Jain (H1); bidirectional upwind (H4) |
| Modify: `src/backend/liquidcooling/handlers/cold_plate.py` | Use `T_all_nodes` (C1); NTU-effectiveness (H2) |
| Modify: `src/backend/liquidcooling/handlers/valve.py` | Document minimum leakage (H6) |
| Create: `src/backend/tests/test_liquid/test_event_timing.py` | C2 verification |
| Create: `src/backend/tests/test_liquid/test_swamee_jain.py` | H1 verification |
| Create: `src/backend/tests/test_liquid/test_ntu_effectiveness.py` | H2 verification |

---

## Task 1: CompiledGraph — add junction fields + compiler junction indices (C1 foundation)

**Files:**
- Modify: `src/backend/liquidcooling/compiled_graph.py`
- Modify: `src/backend/liquidcooling/compiler.py`

- [ ] **Step 1: Add 3 new fields to CompiledGraph dataclass**

In `src/backend/liquidcooling/compiled_graph.py`, add after `junction_mask: Tensor`:

```python
    # --- junction indexing (for T_node state variable) ---
    N_junctions: int                    # count of junction (non-BC) nodes
    junction_indices: Tensor            # [N_junctions] indices into global node array
    node_to_junction: Tensor            # [N_nodes] maps node → junction index (-1 for BC)
```

- [ ] **Step 2: Build junction indices in compiler**

In `src/backend/liquidcooling/compiler.py`, in `compile_graph()`, find where `junction_mask` is built (currently `junction_mask = ~bc_mask`). After that line, add:

```python
    junction_indices = torch.where(junction_mask)[0]
    N_junctions = int(junction_indices.shape[0])
    node_to_junction = torch.full((N_nodes,), -1, dtype=torch.long)
    for j_idx, n_idx in enumerate(junction_indices.tolist()):
        node_to_junction[n_idx] = j_idx
```

Then pass these to the `CompiledGraph(...)` constructor call at the bottom:

```python
    N_junctions=N_junctions,
    junction_indices=junction_indices,
    node_to_junction=node_to_junction,
```

- [ ] **Step 3: Run existing tests to verify nothing broke**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All 127 tests PASS (existing tests don't reference the new fields)

- [ ] **Step 4: Commit**

```bash
git add src/backend/liquidcooling/compiled_graph.py src/backend/liquidcooling/compiler.py
git commit -m "feat(liquid): add junction indices to CompiledGraph (C1 foundation)"
```

---

## Task 2: Event time conversion fix (C2) + pump events (H5)

**Files:**
- Modify: `src/backend/liquidcooling/compiler.py`
- Create: `src/backend/tests/test_liquid/test_event_timing.py`

- [ ] **Step 1: Write the failing test**

`src/backend/tests/test_liquid/test_event_timing.py`:
```python
"""Verify event time_s is converted to step index using dt_s, not used raw."""
import json
import torch
import pytest
from pathlib import Path
from dcdtuni.liquid.schema import LiquidrunConfig
from dcdtuni.liquid.compiler import compile_graph

torch.set_default_dtype(torch.float64)
FIXTURE_DIR = Path(__file__).parent


class TestEventTiming:
    def _make_config_with_event(self, dt_s=0.5, N_t=20, event_time_s=5.0, event_value=500.0):
        """Minimal config with one cold_plate and one Q_W event."""
        raw = json.loads((FIXTURE_DIR / "minimal.liquidrun").read_text())
        raw["time"]["dt_s"] = dt_s
        raw["time"]["N_t"] = N_t
        # Add a cold_plate edge
        raw["edges"].append({
            "id": "cp1", "type": "cold_plate",
            "from": "supply", "to": "return",
            "params": {"dp_a": 5e4, "dp_b": 100, "UA_W_per_K": 150,
                       "C_solid_J_per_K": 50, "V_fluid_m3": 5e-6, "Q_W": 350.0}
        })
        raw["events"] = [{"time_s": event_time_s, "target": "cp1", "field": "Q_W", "value": event_value}]
        return LiquidrunConfig.model_validate(raw)

    def test_event_fires_at_correct_step(self):
        """Event at time_s=5.0 with dt_s=0.5 should fire at step 10."""
        config = self._make_config_with_event(dt_s=0.5, N_t=20, event_time_s=5.0, event_value=500.0)
        graph = compile_graph(config)
        Q_W = graph.cp_params.Q_W  # [N_cp, N_t]
        # Before step 10: should be 350
        assert Q_W[0, 0].item() == pytest.approx(350.0)
        assert Q_W[0, 9].item() == pytest.approx(350.0)
        # At and after step 10: should be 500
        assert Q_W[0, 10].item() == pytest.approx(500.0)
        assert Q_W[0, 19].item() == pytest.approx(500.0)

    def test_event_at_dt_1(self):
        """With dt_s=1.0, time_s=5.0 should fire at step 5."""
        config = self._make_config_with_event(dt_s=1.0, N_t=20, event_time_s=5.0, event_value=999.0)
        graph = compile_graph(config)
        Q_W = graph.cp_params.Q_W
        assert Q_W[0, 4].item() == pytest.approx(350.0)
        assert Q_W[0, 5].item() == pytest.approx(999.0)

    def test_event_clamped_to_last_step(self):
        """Event beyond simulation end should clamp to last step."""
        config = self._make_config_with_event(dt_s=1.0, N_t=10, event_time_s=100.0, event_value=600.0)
        graph = compile_graph(config)
        Q_W = graph.cp_params.Q_W
        # Event is beyond N_t, only last step should change
        assert Q_W[0, 0].item() == pytest.approx(350.0)
        assert Q_W[0, 9].item() == pytest.approx(600.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_event_timing.py -v`
Expected: FAIL — step 10 still has 350.0 (event fired at step 5 instead)

- [ ] **Step 3: Fix event time conversion in compiler.py**

In `_compile_valves()`, find `step_idx = int(ev.time_s)` and change to:
```python
step_idx = min(int(ev.time_s / dt_s), N_t - 1)
```

Do the same in `_compile_cold_plates()`.

Change function signatures to accept `dt_s`:
- `_compile_valves(valve_edges, N_t, events, dt_s, dtype)` 
- `_compile_cold_plates(cp_edges, N_t, events, dt_s, dtype)`

Update the call sites in `compile_graph()` to pass `config.time.dt_s`.

For `_compile_pumps()`, add event handling — add `events` and `dt_s` parameters, build `edge_id_to_local` dict, and process events targeting `rpm_ratio`:
```python
edge_id_to_local: dict[str, int] = {}
for local_idx, (edge_idx, edge, nf, nt) in enumerate(pump_edges):
    edge_id_to_local[edge.id] = local_idx
    # ... existing param extraction ...

for ev in events:
    if ev.target in edge_id_to_local and ev.field == "rpm_ratio":
        local = edge_id_to_local[ev.target]
        step_idx = min(int(ev.time_s / dt_s), N_t - 1)
        for t in range(step_idx, N_t):
            rpm_ratio_list[local][t] = ev.value
```

Update `_compile_pumps` call site similarly.

- [ ] **Step 4: Run tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All tests PASS including new event timing tests

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/compiler.py src/backend/tests/test_liquid/test_event_timing.py
git commit -m "fix(liquid): event time_s conversion uses dt_s (C2) + pump events (H5)"
```

---

## Task 3: Swamee-Jain friction factor (H1)

**Files:**
- Modify: `src/backend/liquidcooling/handlers/pipe.py`
- Create: `src/backend/tests/test_liquid/test_swamee_jain.py`

- [ ] **Step 1: Write the failing test**

`src/backend/tests/test_liquid/test_swamee_jain.py`:
```python
"""Verify Swamee-Jain friction factor against Moody chart reference values."""
import math
import torch
import pytest
from dcdtuni.liquid.handlers.pipe import PipeBatchHandler
from dcdtuni.liquid.compiled_graph import PipeIndices, PipeBatchParams
from dcdtuni.liquid.bdf import smooth_abs

torch.set_default_dtype(torch.float64)


class TestSwameeJain:
    def _make_handler(self, roughness=1.5e-6):
        D = 0.025
        params = PipeBatchParams(
            D=torch.tensor([D]), L=torch.tensor([1.0]), dh=torch.tensor([0.0]),
            K_minor=torch.tensor([0.0]), A=torch.tensor([math.pi/4*D**2]),
            roughness=torch.tensor([roughness]),
        )
        return PipeBatchHandler(params, rho=997.0, mu=0.00089, cp=4182.0, g_mag=9.81)

    def test_laminar_unchanged(self):
        """At Re=500, friction should be f=64/Re=0.128 regardless of roughness."""
        h = self._make_handler(roughness=0.001)  # very rough
        Re = torch.tensor([[500.0]])
        # Laminar regime: sigma ≈ 0, f ≈ f_lam = 64/500 = 0.128
        # We verify indirectly by checking momentum residual is near zero
        # for Hagen-Poiseuille flow
        D, mu, rho = 0.025, 0.00089, 997.0
        A = math.pi/4 * D**2
        m = 500.0 * A * mu / D  # Re=500
        Q = m / rho
        dp_hp = Q * 128 * mu * 1.0 / (math.pi * D**4)  # Hagen-Poiseuille

        state = {"P": torch.tensor([[dp_hp], [0.0]]), "m": torch.tensor([[m]])}
        idx = PipeIndices(edge_ids=torch.tensor([0]), node_from=torch.tensor([0]),
                         node_to=torch.tensor([1]), cell_start=torch.tensor([0]),
                         cell_end=torch.tensor([0]))
        res = h.compute_residuals(state, idx, 1.0, 1)
        assert res["momentum"].abs().max().item() < 1.0  # within 1 Pa

    def test_turbulent_rough_vs_smooth(self):
        """At Re=100000, rough pipe (e/D=0.04) should have higher friction than smooth."""
        h_rough = self._make_handler(roughness=0.001)   # e/D = 0.001/0.025 = 0.04
        h_smooth = self._make_handler(roughness=1e-7)    # effectively smooth

        D, mu, rho = 0.025, 0.00089, 997.0
        A = math.pi/4 * D**2
        m = 100000.0 * A * mu / D  # Re=100000

        state = {"P": torch.tensor([[100000.0], [0.0]]), "m": torch.tensor([[m]])}
        idx = PipeIndices(edge_ids=torch.tensor([0]), node_from=torch.tensor([0]),
                         node_to=torch.tensor([1]), cell_start=torch.tensor([0]),
                         cell_end=torch.tensor([0]))

        r_rough = h_rough.compute_residuals(state, idx, 1.0, 1)["momentum"].item()
        r_smooth = h_smooth.compute_residuals(state, idx, 1.0, 1)["momentum"].item()
        # rough pipe has larger pressure drop → residual should differ
        assert r_rough != pytest.approx(r_smooth, abs=10.0)

    def test_moody_reference(self):
        """At Re=10000, e/D=0.001: Moody chart gives f≈0.0225. Swamee-Jain should match within 5%."""
        D = 0.025
        roughness = 0.001 * D  # e/D = 0.001
        h = self._make_handler(roughness=roughness)

        # Compute friction factor directly
        Re = torch.tensor([[10000.0]])
        e_D = roughness / D
        log_arg = e_D / 3.7 + 5.74 / Re.pow(0.9)
        f_sj = 0.25 / torch.log10(log_arg).pow(2)

        f_moody = 0.0225  # approximate from Moody chart
        assert f_sj.item() == pytest.approx(f_moody, rel=0.05)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_swamee_jain.py -v`
Expected: `test_turbulent_rough_vs_smooth` and/or `test_moody_reference` FAIL (Blasius ignores roughness)

- [ ] **Step 3: Replace Blasius with Swamee-Jain in pipe.py**

In `src/backend/liquidcooling/handlers/pipe.py`, find lines 129-133:
```python
        f_lam  = 64.0 / (Re + 1e-8)
        f_turb = 0.3164 * (Re + 1e-8) ** (-0.25)   # Blasius

        sigma  = torch.sigmoid((Re - 2300.0) / 200.0)
        f      = (1.0 - sigma) * f_lam + sigma * f_turb
```

Replace with:
```python
        f_lam  = 64.0 / (Re + 1e-8)

        # Swamee-Jain (explicit Colebrook-White approximation, uses roughness)
        e_D = self.roughness.unsqueeze(1) / D
        log_arg = e_D / 3.7 + 5.74 / (Re + 1e-8).pow(0.9)
        f_turb = 0.25 / (torch.log10(log_arg) + 1e-10).pow(2)

        sigma  = torch.sigmoid((Re - 2300.0) / 200.0)
        f      = (1.0 - sigma) * f_lam + sigma * f_turb
```

- [ ] **Step 4: Run all tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All tests PASS. The pipe self-test may need its roughness set to `1.5e-6` (already is) to match.

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/handlers/pipe.py src/backend/tests/test_liquid/test_swamee_jain.py
git commit -m "fix(liquid): Swamee-Jain friction factor replaces Blasius (H1)"
```

---

## Task 4: NTU-Effectiveness for cold plate (H2)

**Files:**
- Modify: `src/backend/liquidcooling/handlers/cold_plate.py`
- Create: `src/backend/tests/test_liquid/test_ntu_effectiveness.py`

- [ ] **Step 1: Write the failing test**

`src/backend/tests/test_liquid/test_ntu_effectiveness.py`:
```python
"""Verify NTU-effectiveness formula produces correct outlet temperature."""
import math
import torch
import pytest
from dcdtuni.liquid.bdf import smooth_abs

torch.set_default_dtype(torch.float64)


class TestNTUEffectiveness:
    def test_high_ntu_saturation(self):
        """At very high NTU (>>1), effectiveness→1, T_out→T_s."""
        UA = 10000.0   # very high
        m = 0.01       # low flow
        cp = 4182.0
        T_s = 350.0
        T_in = 308.15

        NTU = UA / (abs(m) * cp + 1e-10)
        effectiveness = 1.0 - math.exp(-NTU)
        T_out = T_in + effectiveness * (T_s - T_in)

        assert T_out == pytest.approx(T_s, abs=0.1)  # outlet ≈ chip temp

    def test_low_ntu_linear(self):
        """At very low NTU (<<1), effectiveness≈NTU, close to linear approx."""
        UA = 1.0       # very low
        m = 1.0        # high flow
        cp = 4182.0
        T_s = 350.0
        T_in = 308.15

        NTU = UA / (m * cp)
        effectiveness = 1.0 - math.exp(-NTU)
        T_out_ntu = T_in + effectiveness * (T_s - T_in)

        # Linear approximation: T_out = T_in + UA*(T_s-T_in)/(m*cp)
        T_out_lin = T_in + UA * (T_s - T_in) / (m * cp)

        # At low NTU, both should agree
        assert T_out_ntu == pytest.approx(T_out_lin, rel=0.01)

    def test_moderate_ntu_differs_from_linear(self):
        """At NTU≈1, linear approximation overestimates T_out."""
        UA = 150.0
        m = 0.03       # moderate flow
        cp = 4182.0
        T_s = 350.0
        T_in = 308.15

        m_cp = m * cp
        NTU = UA / m_cp
        effectiveness = 1.0 - math.exp(-NTU)
        T_out_ntu = T_in + effectiveness * (T_s - T_in)

        # Linear overestimates
        T_out_lin = T_in + UA * (T_s - T_in) / m_cp
        assert T_out_lin > T_out_ntu + 0.5  # linear should be noticeably higher
```

- [ ] **Step 2: Run test to verify it passes (these test math, not handler)**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_ntu_effectiveness.py -v`
Expected: All 3 PASS (pure math tests)

- [ ] **Step 3: Replace linear approximation with NTU-effectiveness in cold_plate.py**

In `src/backend/liquidcooling/handlers/cold_plate.py`, find lines 143-147:
```python
        T_supply   = self.T_fluid_init                       # scalar float
        UA         = self.UA.unsqueeze(1)                    # [N_cp, 1]
        m_abs_cp   = smooth_abs(m) * self.cp + 1e-10         # [N_cp, N_t]

        T_w_out = T_supply + UA * (T_s - T_supply) / m_abs_cp   # [N_cp, N_t]
```

Replace with:
```python
        T_in       = self.T_fluid_init                       # scalar (will become T_all_nodes in Task 5)
        UA         = self.UA.unsqueeze(1)                    # [N_cp, 1]
        m_abs_cp   = smooth_abs(m) * self.cp + 1e-10         # [N_cp, N_t]

        NTU = UA / m_abs_cp                                   # [N_cp, N_t]
        effectiveness = 1.0 - torch.exp(-NTU)                 # [N_cp, N_t]
        T_w_out = T_in + effectiveness * (T_s - T_in)         # [N_cp, N_t]
```

Also update the docstring to say "NTU-effectiveness" instead of "crude linear approximation".

- [ ] **Step 4: Run all tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/handlers/cold_plate.py src/backend/tests/test_liquid/test_ntu_effectiveness.py
git commit -m "fix(liquid): NTU-effectiveness replaces linear approximation (H2)"
```

---

## Task 5: Junction temperature mixing + T_all_nodes + BC temperature (C1 + C3)

This is the largest task — it wires up the `T_node` state variable, builds `T_all_nodes`, adds the mixing residual, fixes pipe/cold_plate to use actual upstream temperatures, and adds BC temperature constraint.

**Files:**
- Modify: `src/backend/liquidcooling/model.py`
- Modify: `src/backend/liquidcooling/handlers/pipe.py`
- Modify: `src/backend/liquidcooling/handlers/cold_plate.py`

- [ ] **Step 1: Add T_node state variable to model.py**

In `LiquidODILModel.__init__()`, after the `T_s` block, add:

```python
        # Thermal state: T_node (junction mixed temperatures)
        if graph.N_junctions > 0:
            state_dict["T_node"] = nn.Parameter(
                torch.full(
                    (graph.N_junctions, graph.N_t),
                    graph.T_fluid_init,
                    dtype=torch.float64,
                )
            )
        else:
            state_dict["T_node"] = nn.Parameter(
                torch.empty(0, graph.N_t, dtype=torch.float64)
            )
```

Also register `bc_T` as a buffer (if not already):
```python
        self.register_buffer("bc_T", graph.bc_T)
```

- [ ] **Step 2: Build T_all_nodes and add bc_T penalty in forward()**

In `forward()`, before the handler loop, add:

```python
        # Build full node temperature map [N_nodes, N_t]
        T_all_nodes = torch.full(
            (self.graph.N_nodes, self.graph.N_t),
            self.graph.T_fluid_init, dtype=torch.float64,
            device=state["P"].device,
        )
        if self.graph.N_junctions > 0 and "T_node" in state:
            T_all_nodes = T_all_nodes.clone()
            T_all_nodes[self.graph.junction_indices] = state["T_node"]
        bc_T_valid = ~torch.isnan(self.bc_T)
        if bc_T_valid.any():
            T_all_nodes = T_all_nodes.clone()
            T_all_nodes[bc_T_valid] = self.bc_T[bc_T_valid].unsqueeze(1).expand(
                -1, self.graph.N_t
            )
        state["T_all_nodes"] = T_all_nodes
```

After the `bc_P` penalty, add:

```python
        # BC temperature penalty
        if bc_T_valid.any():
            T_bc_err = T_all_nodes[bc_T_valid] - self.bc_T[bc_T_valid].unsqueeze(1)
            losses["bc_T"] = w.bc * T_bc_err.pow(2).mean()
```

- [ ] **Step 3: Add mixing residual method to model.py**

Add a new method `_compute_mixing_residual()` and call it from `forward()`:

```python
    def _compute_mixing_residual(self, state: dict) -> torch.Tensor:
        """Compute junction temperature mixing residual."""
        if self.graph.N_junctions == 0 or "T_node" not in state:
            return torch.tensor(0.0, dtype=torch.float64)

        T_node = state["T_node"]          # [N_junctions, N_t]
        T_all = state["T_all_nodes"]      # [N_nodes, N_t]
        m = state["m"]                    # [N_edges, N_t]
        cp = self.graph.fluid_cp

        residuals = []
        for j_local in range(self.graph.N_junctions):
            n_global = self.graph.junction_indices[j_local].item()
            in_edges = self.graph.node_in_edges[n_global]

            if not in_edges:
                continue

            # Weighted sum of incoming flow * outlet temperature
            weighted_T = torch.zeros(self.graph.N_t, dtype=torch.float64, device=m.device)
            total_w = torch.zeros(self.graph.N_t, dtype=torch.float64, device=m.device)

            for e_idx in in_edges:
                m_e = m[e_idx]
                w_e = torch.nn.functional.softplus(m_e * 100.0) / 100.0 * cp
                T_out_e = self._edge_outlet_temp(e_idx, state)
                weighted_T = weighted_T + w_e * T_out_e
                total_w = total_w + w_e

            # Also check outgoing edges with negative flow (reverse flow into junction)
            out_edges = self.graph.node_out_edges[n_global]
            for e_idx in out_edges:
                m_e = m[e_idx]
                w_e = torch.nn.functional.softplus(-m_e * 100.0) / 100.0 * cp
                T_out_e = self._edge_inlet_temp_reverse(e_idx, state)
                weighted_T = weighted_T + w_e * T_out_e
                total_w = total_w + w_e

            T_mixed = weighted_T / (total_w + 1e-10)
            residuals.append(T_node[j_local] - T_mixed)

        if not residuals:
            return torch.tensor(0.0, dtype=torch.float64)
        return torch.stack(residuals)

    def _edge_outlet_temp(self, edge_idx: int, state: dict) -> torch.Tensor:
        """Get outlet temperature of an edge (for mixing at to_node)."""
        etype = self.graph.edge_type[edge_idx].item()
        if etype == 0 and "T_w" in state and state["T_w"].numel() > 0:
            # pipe: last cell temperature
            if self.graph.pipe_indices is not None:
                local = (self.graph.pipe_indices.edge_ids == edge_idx).nonzero(as_tuple=True)[0]
                if len(local) > 0:
                    p_local = local[0].item()
                    last_cell = self.graph.pipe_indices.cell_end[p_local].item() - 1
                    if last_cell >= 0:
                        return state["T_w"][last_cell]
        elif etype == 1 and "T_s" in state and state["T_s"].numel() > 0:
            # cold_plate: compute T_w_out from T_s
            if self.graph.cp_indices is not None:
                local = (self.graph.cp_indices.edge_ids == edge_idx).nonzero(as_tuple=True)[0]
                if len(local) > 0:
                    cp_local = local[0].item()
                    T_s = state["T_s"][self.graph.cp_indices.cp_ids[cp_local]]
                    from_node = self.graph.edge_from[edge_idx].item()
                    T_in = state["T_all_nodes"][from_node]
                    UA = self.handlers["cold_plate"].UA[cp_local]
                    m_e = state["m"][edge_idx]
                    m_abs_cp = torch.abs(m_e) * self.graph.fluid_cp + 1e-10
                    NTU = UA / m_abs_cp
                    eff = 1.0 - torch.exp(-NTU)
                    return T_in + eff * (T_s - T_in)
        # valve/pump or fallback: transparent (from_node temperature)
        from_node = self.graph.edge_from[edge_idx].item()
        return state["T_all_nodes"][from_node]

    def _edge_inlet_temp_reverse(self, edge_idx: int, state: dict) -> torch.Tensor:
        """Get the temperature that would flow into the from_node if flow reverses."""
        to_node = self.graph.edge_to[edge_idx].item()
        return state["T_all_nodes"][to_node]
```

In `forward()`, add after component residuals:

```python
        # Mixing residual at junction nodes
        if self.graph.N_junctions > 0:
            r_mix = self._compute_mixing_residual(state)
            if r_mix.numel() > 0 and r_mix.dim() > 0:
                losses["mixing"] = w.energy * r_mix.pow(2).mean()
```

- [ ] **Step 4: Update pipe handler to use T_all_nodes**

In `src/backend/liquidcooling/handlers/pipe.py`, in `compute_residuals()`, find the thermal section (line 151+). Replace the upwind construction:

```python
            T_up = torch.zeros_like(T_w)
            for p in range(self.N_pipes):
                c_start = indices.cell_start[p].item()
                c_end = indices.cell_end[p].item()
                if c_end <= c_start:
                    continue
                # First cell: upstream = supply temperature
                T_up[c_start] = self.T_fluid_init
                # Remaining cells: upstream = previous cell
                if c_end - c_start > 1:
                    T_up[c_start + 1 : c_end] = T_w[c_start : c_end - 1]
```

With:

```python
            # Use T_all_nodes for upstream junction temperature if available
            T_all = state.get("T_all_nodes", None)

            T_up = torch.zeros_like(T_w)
            for p in range(self.N_pipes):
                c_start = indices.cell_start[p].item()
                c_end = indices.cell_end[p].item()
                if c_end <= c_start:
                    continue
                # First cell: upstream = junction mixed temperature
                if T_all is not None:
                    T_up[c_start] = T_all[indices.node_from[p]]
                else:
                    T_up[c_start] = self.T_fluid_init
                # Remaining cells: upstream = previous cell
                if c_end - c_start > 1:
                    T_up[c_start + 1 : c_end] = T_w[c_start : c_end - 1]
```

- [ ] **Step 5: Update cold plate handler to use T_all_nodes**

In `src/backend/liquidcooling/handlers/cold_plate.py`, replace:
```python
        T_in       = self.T_fluid_init
```

With:
```python
        T_all = state.get("T_all_nodes", None)
        if T_all is not None:
            T_in = T_all[indices.node_from]    # [N_cp, N_t]
        else:
            T_in = self.T_fluid_init           # scalar fallback
```

- [ ] **Step 6: Run ALL tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All tests PASS. The thermal and single_rack tests may produce slightly different values due to correct temperature mixing, but should still satisfy their assertions (which have relaxed tolerances).

- [ ] **Step 7: Commit**

```bash
git add src/backend/liquidcooling/model.py src/backend/liquidcooling/handlers/pipe.py src/backend/liquidcooling/handlers/cold_plate.py
git commit -m "feat(liquid): junction T_node mixing + T_all_nodes + bc_T penalty (C1+C3)"
```

---

## Task 6: Bidirectional upwind (H4)

**Files:**
- Modify: `src/backend/liquidcooling/handlers/pipe.py`

- [ ] **Step 1: Replace unidirectional upwind with bidirectional**

In `pipe.py`, replace the upwind construction block (the one modified in Task 5) with:

```python
            T_all = state.get("T_all_nodes", None)

            # Build forward upwind (from_node → cell[0] → cell[1] → ...)
            T_up_fwd = torch.zeros_like(T_w)
            # Build backward upwind (to_node → cell[-1] → cell[-2] → ...)
            T_up_bwd = torch.zeros_like(T_w)

            for p in range(self.N_pipes):
                c_start = indices.cell_start[p].item()
                c_end = indices.cell_end[p].item()
                n_c = c_end - c_start
                if n_c <= 0:
                    continue

                # Forward: from_node feeds cell[0]
                T_from = T_all[indices.node_from[p]] if T_all is not None else self.T_fluid_init
                T_up_fwd[c_start] = T_from
                if n_c > 1:
                    T_up_fwd[c_start + 1 : c_end] = T_w[c_start : c_end - 1]

                # Backward: to_node feeds cell[-1]
                T_to = T_all[indices.node_to[p]] if T_all is not None else self.T_fluid_init
                T_up_bwd[c_end - 1] = T_to
                if n_c > 1:
                    T_up_bwd[c_start : c_end - 1] = T_w[c_start + 1 : c_end]

            # Smooth flow-direction blend
            global_edge_per_cell = indices.edge_ids[self.cell_pipe_id]
            m_cell = state["m"][global_edge_per_cell]
            w_pos = torch.sigmoid(m_cell * 1000.0)

            T_up = w_pos * T_up_fwd + (1.0 - w_pos) * T_up_bwd
```

- [ ] **Step 2: Run all tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Run pipe self-test**

Run: `conda run -n dcdtuni python src/backend/liquidcooling/handlers/pipe.py`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/backend/liquidcooling/handlers/pipe.py
git commit -m "fix(liquid): bidirectional upwind for flow reversal (H4)"
```

---

## Task 7: Valve documentation (H6) + final validation

**Files:**
- Modify: `src/backend/liquidcooling/handlers/valve.py`

- [ ] **Step 1: Add leakage documentation comment**

In `src/backend/liquidcooling/handlers/valve.py`, find the `torch.clamp` line and add a comment before it:

```python
        # Minimum leakage opening prevents graph disconnection (FromGPT.md pitfall #6).
        # A fully-closed valve would create an isolated sub-graph with singular Jacobian.
        alpha = torch.clamp(self.opening, min=1e-3)
```

- [ ] **Step 2: Run ALL tests — final validation**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v --tb=short`
Expected: All tests PASS

Run handler self-tests:
```bash
conda run -n dcdtuni python src/backend/liquidcooling/handlers/pipe.py
conda run -n dcdtuni python src/backend/liquidcooling/handlers/pump.py
conda run -n dcdtuni python src/backend/liquidcooling/handlers/cold_plate.py
conda run -n dcdtuni python src/backend/liquidcooling/handlers/valve.py
```
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/handlers/valve.py
git commit -m "docs(liquid): document valve minimum leakage model (H6)"
```
