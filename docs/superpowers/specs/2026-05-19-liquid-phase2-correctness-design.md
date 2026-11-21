# Liquid Solver Phase 2 — Correctness Fixes

**Date:** 2026-05-19
**Sub-project:** Phase 2 of liquid solver overhaul (B done → **C/Phase 2** → Phase 3 → A)

## Three Fixes

### Fix 1: T-Junction Minor Losses

**Problem:** `compiler.py:164-189` only counts geometric bends in pipe waypoints for K_minor. Manifold branch tees (where a branch pipe splits from the main manifold) have K=0.5-2.0 but get K=0 currently.

**Solution:** In `compiler.py`, after creating manifold-to-server branch pipe edges, detect T-junction topology: when a pipe edge's from-node or to-node has degree >= 3 (i.e., 3+ connected edges = a tee), add `K_tee` to that pipe's K_minor. Default `K_tee = 0.9` (standard tee branch, per Idelchik).

**Where:**
- `compiler.py` around line 189: after computing `K_minor` from bends, add a second pass that checks node degree for tee detection
- `schema.py`: add optional `K_tee_branch` field to `EdgeConfig.params` (default 0.9) so users can override per-edge
- `handlers/pipe.py`: no change needed — K_minor is already used in the momentum equation (line 141)

### Fix 2: Valve Kv Unit Conversion

**Problem:** `valve.py:100-101` uses `dP` in Pa but Kv is in `m³/h/√bar`. Missing the `1e5 Pa/bar` conversion factor, giving ~316x error.

**Solution:** Convert dP from Pa to bar before applying the ISA equation:

```python
dP_bar = dP.abs() / 1e5                    # Pa → bar
rho_rel = self.rho / 1000.0                # relative density (water=1)
Q_m3h = Kv * (dP_bar / rho_rel + 1e-10).sqrt()  # m³/h
m_model = Q_m3h * self.rho / 3600.0        # kg/s
```

**Where:** `handlers/valve.py` lines 95-103 — replace the current momentum residual computation.

### Fix 3: Junction Temperature Iteration Depth

**Problem:** `model.py:303` hardcodes `range(2)` for junction temperature propagation. Shinaide has ~170 junctions with chains potentially 5-10 deep.

**Solution:** Replace `range(2)` with `range(max_depth)` where `max_depth` is computed from the graph topology at compile time. Use BFS from pipe/cold-plate outlet nodes to find the maximum junction chain depth, then store it on `CompiledGraph`.

**Where:**
- `compiled_graph.py`: add `max_junction_depth: int` field
- `compiler.py`: compute max junction depth via BFS at end of compilation
- `model.py:303`: `range(self.graph.max_junction_depth)` instead of `range(2)`

## Testing

- Existing 166 tests must pass
- New tests:
  - `test_tee_K_minor`: compile a manifold topology, verify branch pipes have K_minor > 0 (includes tee contribution)
  - `test_valve_kv_flow`: single valve with known Kv=2.5, dP=1 bar → verify m matches ISA equation
  - `test_junction_depth_computed`: compile Shinaide-like topology, verify max_junction_depth > 2
