# Sub-A: Liquid ODIL Backend Physics Fix — Design Spec

**Date**: 2026-05-06
**Status**: Approved
**Scope**: Fix 3 CRITICAL physics bugs + 6 HIGH gaps in the liquid ODIL solver backend
**Parent**: [Liquid ODIL Gap-Fill Plan](../../../.claude/projects/-mnt-lv-litianyu-mycode-dcdtuni/memory/project_liquid_odil_gap_fill.md)
**Prerequisite**: Phase 0-5 complete (127 tests passing)

---

## 1. Overview

The Phase 0-5 liquid ODIL solver has correct architecture but incorrect thermal physics. A gap audit against FromGPT.md Q3/A3 found 3 CRITICAL bugs that produce wrong results in any multi-component network, plus 6 HIGH gaps that degrade accuracy.

### Bugs to fix

| ID | Severity | Description |
|----|----------|-------------|
| C1 | CRITICAL | Junction temperature mixing not implemented — pipe cell[0] and cold_plate both use `T_fluid_init` instead of actual upstream mixed temperature |
| C2 | CRITICAL | Event time_s→step conversion uses `int(ev.time_s)` instead of `int(ev.time_s / dt_s)` |
| C3 | CRITICAL | No BC temperature constraint — `bc_T` exists in CompiledGraph but `model.py` never penalizes it |
| H1 | HIGH | Roughness compiled but unused — friction uses Blasius (smooth pipe) instead of Swamee-Jain |
| H2 | HIGH | NTU-effectiveness formula is crude linear approximation |
| H3 | HIGH | Cold plate water-side thermal mass ignored — deferred to Phase 6, but NTU formula improved |
| H4 | HIGH | Upwind direction doesn't flip with flow reversal |
| H5 | HIGH | Pump events (rpm_ratio changes) silently ignored by compiler |
| H6 | HIGH | Valve minimum leakage model undocumented |

---

## 2. C1: Junction Temperature Mixing

### New state variable

Add `T_node[N_junctions, N_t]` as an `nn.Parameter` in `LiquidODILModel`. This represents the mixed fluid temperature at each junction node.

### CompiledGraph additions

```python
# New fields in CompiledGraph
N_junctions: int                    # number of junction (non-BC) nodes
junction_indices: Tensor            # [N_junctions] — indices into global node array
node_to_junction: Tensor            # [N_nodes] — maps node index to junction index (-1 for BC nodes)
```

### Compiler changes

In `compile_graph()`, after building `junction_mask`:

```python
junction_indices = torch.where(junction_mask)[0]              # [N_junctions]
N_junctions = junction_indices.shape[0]
node_to_junction = torch.full((N_nodes,), -1, dtype=torch.long)
for j_idx, n_idx in enumerate(junction_indices.tolist()):
    node_to_junction[n_idx] = j_idx
```

### Model changes

**New state variable:**
```python
if graph.N_junctions > 0:
    state_dict["T_node"] = nn.Parameter(
        torch.full((graph.N_junctions, graph.N_t), graph.T_fluid_init, dtype=torch.float64)
    )
```

**Build T_all_nodes in forward():**
```python
# Construct full node temperature map [N_nodes, N_t]
T_all_nodes = torch.full((self.graph.N_nodes, self.graph.N_t),
                          self.graph.T_fluid_init, dtype=torch.float64)
# Junction nodes from optimized T_node
if self.graph.N_junctions > 0:
    T_all_nodes[self.graph.junction_indices] = state["T_node"]
# BC nodes from specified values (this also fixes C3)
bc_T_valid = ~torch.isnan(self.bc_T)
if bc_T_valid.any():
    T_all_nodes[bc_T_valid] = self.bc_T[bc_T_valid].unsqueeze(1).expand_as(
        T_all_nodes[bc_T_valid]
    )
# Pass T_all_nodes to handlers via state dict
state["T_all_nodes"] = T_all_nodes
```

**Mixing residual:**
```python
def _compute_mixing_residual(self, state):
    """
    For each junction node j at each time step t:
      T_node[j,t] * sum(m_in_positive * cp) = sum(m_in_positive * cp * T_out_upstream)
    
    Uses softplus for smooth positive-flow extraction.
    T_out_upstream depends on edge type:
      - pipe: T_w[last_cell_of_pipe]
      - cold_plate: computed T_w_out from handler
      - valve/pump: T_all_nodes[from_node] (transparent, no thermal effect)
    """
```

### Handler interface changes

Handlers receive `state["T_all_nodes"]` and use it to look up upstream temperature:

**pipe.py:**
```python
# Replace: T_up[c_start] = self.T_fluid_init
# With:    T_up[c_start] = state["T_all_nodes"][indices.node_from[p]]
```

**cold_plate.py:**
```python
# Replace: T_supply = self.T_fluid_init
# With:    T_in = state["T_all_nodes"][indices.node_from]  # [N_cp, N_t]
```

---

## 3. C2: Event Time Conversion Fix

In `compiler.py`, all three event expansion functions (`_compile_valves`, `_compile_cold_plates`, `_compile_pumps`):

```python
# BEFORE (broken):
step_idx = int(ev.time_s)

# AFTER (correct):
step_idx = min(int(ev.time_s / config.time.dt_s), N_t - 1)
```

The `dt_s` must be passed to each `_compile_*` function (currently only `N_t` and `events` are passed).

---

## 4. C3: BC Temperature Constraint

In `model.py` forward(), add after bc_P penalty:

```python
bc_T_valid = ~torch.isnan(self.bc_T)
if bc_T_valid.any():
    T_bc_target = self.bc_T[bc_T_valid].unsqueeze(1)
    T_bc_actual = state["T_all_nodes"][bc_T_valid]
    losses["bc_T"] = w.bc * (T_bc_actual - T_bc_target).pow(2).mean()
```

This ensures CDU supply temperature is actually enforced at the boundary.

---

## 5. H1: Swamee-Jain Friction Factor

In `handlers/pipe.py`, replace turbulent friction with Swamee-Jain (explicit Colebrook-White approximation):

```python
# BEFORE (Blasius, ignores roughness):
f_turb = 0.3164 * (Re + 1e-8).pow(-0.25)

# AFTER (Swamee-Jain, uses roughness):
e_D = self.roughness.unsqueeze(1) / D                  # relative roughness
log_arg = e_D / 3.7 + 5.74 / (Re + 1e-8).pow(0.9)
f_turb = 0.25 / (torch.log10(log_arg) + 1e-10).pow(2)
```

Keep the existing laminar-turbulent sigmoid blend unchanged.

---

## 6. H2: NTU-Effectiveness Formula

In `handlers/cold_plate.py`, replace the linear approximation:

```python
# BEFORE (linear, only valid for NTU << 1):
T_w_out = T_in + UA * (T_s - T_in) / m_abs_cp

# AFTER (NTU-effectiveness for constant-temperature source):
NTU = UA / (m_abs_cp + 1e-10)
effectiveness = 1.0 - torch.exp(-NTU)
T_w_out = T_in + effectiveness * (T_s - T_in)
```

This correctly captures the exponential saturation behavior at high NTU values.

---

## 7. H3: Cold Plate Water-Side Thermal Mass

**Decision: Deferred to Phase 6.**

The cold plate water volume (5e-6 m³) has thermal mass C_w = ρ·cp·V = 997·4182·5e-6 ≈ 20.85 J/K, which is much smaller than the chip thermal mass (50 J/K). For the current dt=1s time step, the water-side lag is negligible. The NTU-effectiveness fix (H2) provides the main accuracy improvement.

---

## 8. H4: Bidirectional Upwind

In `handlers/pipe.py`, the upwind temperature construction must handle both flow directions:

```python
# Flow direction weight: ~1 for positive flow, ~0 for negative
w_pos = torch.sigmoid(m_cell * 1000.0)

# Forward upwind: from_node → cell[0] → cell[1] → ...
T_up_fwd = build_forward_upwind(T_w, T_node_from, indices)

# Backward upwind: to_node → cell[-1] → cell[-2] → ...
T_up_bwd = build_backward_upwind(T_w, T_node_to, indices)

# Smooth blend
T_up = w_pos * T_up_fwd + (1.0 - w_pos) * T_up_bwd
```

The `build_backward_upwind` mirrors the forward logic but in reverse order, using the `to_node` temperature as the boundary.

---

## 9. H5: Pump Event Expansion

Add event processing to `_compile_pumps()` in `compiler.py`:

```python
edge_id_to_local: dict[str, int] = {}
for local_idx, (edge_idx, edge, nf, nt) in enumerate(pump_edges):
    edge_id_to_local[edge.id] = local_idx
    # ... existing param extraction ...

# Apply events targeting pump rpm_ratio
for ev in events:
    if ev.target in edge_id_to_local and ev.field == "rpm_ratio":
        local = edge_id_to_local[ev.target]
        step_idx = min(int(ev.time_s / dt_s), N_t - 1)
        for t in range(step_idx, N_t):
            rpm_ratio_list[local][t] = ev.value
```

Function signature changes: `_compile_pumps(pump_edges, node_idx, N_t, events, dt_s, dtype)`.

---

## 10. H6: Valve Minimum Leakage Documentation

No code change needed. The existing `torch.clamp(self.opening, min=1e-3)` in `handlers/valve.py` is correct. Add a comment:

```python
# Minimum leakage opening prevents graph disconnection (FromGPT.md pitfall #6).
# A fully-closed valve would create an isolated sub-graph with singular Jacobian.
alpha = torch.clamp(self.opening, min=1e-3)
```

---

## 11. Files Modified

| File | Changes |
|------|---------|
| `compiled_graph.py` | Add `N_junctions`, `junction_indices`, `node_to_junction` fields |
| `compiler.py` | Build junction indices; fix event time conversion (C2); add pump events (H5); pass dt_s to compile helpers |
| `model.py` | Add `T_node` state; build `T_all_nodes`; add mixing residual; add `bc_T` penalty (C3) |
| `handlers/pipe.py` | Use `T_all_nodes` for upwind (C1); Swamee-Jain friction (H1); bidirectional upwind (H4) |
| `handlers/cold_plate.py` | Use `T_all_nodes` for inlet (C1); NTU-effectiveness (H2) |
| `handlers/valve.py` | Add documentation comment (H6) |
| `handlers/pump.py` | No change (events handled by compiler) |

## 12. Test Updates

Existing 127 tests should still pass after these changes (interface-compatible). New tests needed:

| Test | Verifies |
|------|----------|
| `test_junction_mixing.py` | T_node mixing residual with known analytical solution (Y-junction with two inflows at different temperatures) |
| `test_event_timing.py` | Event at time_s=5.0 with dt_s=0.5 fires at step 10 (not step 5) |
| `test_bc_temperature.py` | CDU supply temperature is enforced at the boundary node |
| `test_swamee_jain.py` | Friction factor matches Moody chart at Re=10000, e/D=0.001 |
| `test_ntu_effectiveness.py` | Cold plate outlet temp matches analytical NTU formula |
| `test_bidirectional_upwind.py` | Negative flow produces correct reverse thermal transport |
| Update `test_thermal.py` | Existing thermal test still passes with improved physics |
| Update `test_single_rack.py` | Existing rack test still passes |
