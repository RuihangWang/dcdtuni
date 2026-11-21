# Liquid Solver BC/IC Management Module — Design Spec

**Date:** 2026-05-19
**Sub-project:** B (BC/IC Management) — first of four phases in the liquid solver overhaul
**Roadmap:** B (this) → A (Newton solver) → C (missing physics) → D (CDU model)

## Problem

Boundary condition enforcement, initial condition handling, and state initialization are scattered across four files:

- `model.py:255-264` — BC pressure loss (soft penalty, weight=100)
- `model.py:153-164` — dead-end edge detection and zero-flow loss
- `model.py:287-311` — temperature Dirichlet enforcement in `_build_T_all_nodes()`
- `runner.py:150-154` — naive initialization (BC pressures copied, all m=1.0)
- `handlers/pipe.py:201` — pipe IC residual (T_w[:,0] vs T_fluid_init)
- `handlers/cold_plate.py:180` — cold plate IC residual (T_s[:,0] vs T_solid_init)

This causes three problems:
1. No single place to understand or modify BC enforcement strategy
2. Naive initialization (all flows = 1.0 kg/s) forces the optimizer to discover flow directions and magnitudes from scratch, wasting thousands of epochs
3. No separated BC/IC monitoring — hard to diagnose whether convergence failures are due to BC violation, IC mismatch, or physics residuals

## Solution

New file `src/backend/liquidcooling/bc_ic.py` containing three classes with clear responsibilities.

## Architecture

### `BoundaryConditionEnforcer`

**Responsibility:** Own all BC data and provide both hard enforcement and soft loss.

**Constructor:** Takes `CompiledGraph` → extracts `bc_mask`, `bc_P`, `bc_T`, `bc_T_mask`, and computes `dead_end_mask` (edges where both endpoints are dead ends or only one endpoint connects to the network).

**Methods:**

`enforce(state: dict[str, nn.Parameter]) -> None`
- Hard-clamps `state["P"][bc_mask, :] = bc_P[bc_mask]` (unsqueezed to broadcast over N_t)
- Zeros `state["m"][dead_end_mask, :] = 0`
- Called after each optimizer step in the solve loop
- Operates with `torch.no_grad()` — this is a projection, not a differentiable operation

`loss(state: dict[str, nn.Parameter], w_bc: float) -> dict[str, Tensor]`
- Returns `{"bc_P": w_bc * mean((P_bc - P_target)^2), "bc_dead": w_bc * mean(m_dead^2)}`
- Called during `model.forward()` for gradient signal before the hard clamp
- Temperature BC is not included here — it is already hard-enforced in `_build_T_all_nodes()` via Dirichlet substitution

**Dead-end detection logic** (moved from `model.py:153-164`):
- An edge is dead-end if its from-node or to-node has degree 1 and is not a BC node
- The existing detection is correct; it just moves to the enforcer

### `StateInitializer`

**Responsibility:** Topology-aware smart initialization, replacing the naive `m.fill_(1.0)`.

**Constructor:** Takes `CompiledGraph`.

**Methods:**

`initialize(state: dict[str, nn.Parameter]) -> None`
- Mutates state in-place with `torch.no_grad()`
- Algorithm:
  1. **Identify supply/return BC nodes:** supply = BC node with outgoing edges; return = BC node with incoming edges
  2. **BFS pressure propagation from supply:** start with `P[supply] = bc_P[supply]`. For each edge traversed, estimate `dP = R_rough * m_est^2` using rough friction: `R_rough = f_est * L / D / (2 * rho * A^2)` with `f_est = 0.02` (typical turbulent). Cold plates use `dp_a * m_est^2`, pumps add head `rho * g * H0`, valves use `Kv`-derived resistance.
  3. **Global flow scale:** compare propagated P at return node vs `bc_P[return]`. Scale all `m_est` by `sqrt(target_total_dP / propagated_total_dP)` to match the BC pressure difference.
  4. **Set flow signs:** `m[edge] = sign(P[from] - P[to]) * |m_est|` — correct flow directions from pressure gradient.
  5. **Temperature init:** `T_w[:, :] = T_fluid_init`, `T_s[:, :] = T_solid_init` (same as current, centralized).
  6. **Pressure init:** `P[:, :] = P_propagated` (broadcast over N_t).
  7. **Fallback:** unreached nodes get `P = P_uniform_Pa` (from ICConfig), unreached edges get `m = 0.01`.

### `BCICMonitor`

**Responsibility:** Compute aggregate BC/IC metrics for SSE reporting.

**Constructor:** Takes `CompiledGraph` (for `T_fluid_init`, `T_solid_init`).

**Methods:**

`compute(state: dict[str, Tensor], bc_enforcer: BoundaryConditionEnforcer) -> dict`
- Returns `{"total_bc": float, "total_ic": float}`
- `total_bc`: sum of `bc_P` and `bc_dead` loss values (unweighted, for monitoring only)
- `total_ic`: `mean((T_w[:,0] - T_fluid_init)^2) + mean((T_s[:,0] - T_solid_init)^2)`
- Called in the solve callback, results merged into SSE payload

## Integration Changes

### `model.py`

**Remove:**
- BC loss computation (lines 255-264) — `P_bc_err` and `losses["bc_P"]`
- Dead-end detection and loss (lines 153-164, related loss lines)

**Add:**
- `self.bc_enforcer` attribute, set via constructor parameter
- In `forward()`: `bc_losses = self.bc_enforcer.loss(state, w.bc)` → merge into losses dict
- IC losses from handlers continue as-is, aggregated under `w.ic`

**Keep unchanged:**
- `_build_T_all_nodes()` temperature Dirichlet enforcement (lines 287-311) — this is already correct hard enforcement
- Junction temperature computation (lines 93-98, 313-370)
- Mass conservation residual (lines 226-238)
- Handler dispatch loop

### `runner.py`

**Remove:**
- Lines 150-154 (naive init: `P.data[i,:] = bc_P[i]`, `m.data.fill_(1.0)`)

**Add:**
- After model construction: `bc_enforcer = BoundaryConditionEnforcer(graph)`
- Pass enforcer to model: `model = LiquidODILModel(graph, bc_enforcer=bc_enforcer)`
- After model construction: `StateInitializer(graph).initialize(model.state)`
- In `_monitor_callback`: `bc_ic = BCICMonitor(graph).compute(model.state, bc_enforcer)` → merge `total_bc`, `total_ic` into SSE payload

### `solver.py`

**Add one line** after `optimizer.step()`:
```python
bc_enforcer.enforce(model.state)
```

The enforcer is passed to `solve()` as a parameter (or accessed via `model.bc_enforcer`).

### `handlers/pipe.py` and `handlers/cold_plate.py`

**No structural changes.** IC residuals (`r_ic`) stay in handler output dicts. They are collected by `model.forward()` and weighted under `w.ic` as before. The `BCICMonitor` computes its own IC metric independently for monitoring (avoiding dependency on handler internals).

## SSE Payload Changes

Current:
```json
{"epoch": 500, "total": 0.034, "losses": {"mass": ..., "momentum": ..., "energy": ...}}
```

After:
```json
{"epoch": 500, "total": 0.034, "losses": {"mass": ..., "momentum": ..., "energy": ..., "bc_P": ..., "bc_dead": ...}, "total_bc": 0.0001, "total_ic": 0.0003}
```

The frontend monitoring window (`liquid_solve_monitor.js`) already handles arbitrary loss keys in the `losses` dict — `bc_P` and `bc_dead` will appear automatically as new series in the loss chart.

## File Map

| File | Action | Lines Changed |
|---|---|---|
| `bc_ic.py` | **NEW** | ~200-250 lines |
| `model.py` | Extract BC loss, add enforcer dependency | -30, +10 |
| `runner.py` | Replace naive init, wire enforcer + monitor | -5, +15 |
| `solver.py` | Add enforce() call after step | +3 |
| `handlers/pipe.py` | No change | 0 |
| `handlers/cold_plate.py` | No change | 0 |
| `compiled_graph.py` | No change | 0 |
| `schema.py` | No change | 0 |

## Testing

- Existing 158 liquid tests must pass (no regressions)
- New unit tests for `bc_ic.py`:
  - `test_enforcer_clamps_P`: after enforce(), BC node pressures equal targets
  - `test_enforcer_zeros_dead_ends`: dead-end edge flows are zero
  - `test_initializer_flow_directions`: supply→return flows are positive, return→supply are negative
  - `test_initializer_pressure_monotonic`: pressure decreases from supply to return along flow path
  - `test_monitor_reports_aggregates`: total_bc and total_ic are non-negative scalars

## Future Compatibility

This module is designed to work with both the current Adam solver and the future Newton-Raphson solver (sub-project A):
- `enforce()` is a post-step projection — works identically after an Adam step or Newton step
- `loss()` provides residuals that can be included in either a loss sum (Adam) or a residual vector (Newton)
- `StateInitializer` runs once before the solve loop regardless of solver choice
- `BCICMonitor` is solver-agnostic — reads state tensors directly
