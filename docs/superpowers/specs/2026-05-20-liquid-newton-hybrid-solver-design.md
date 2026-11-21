# GGA Newton Hydraulic + Adam Thermal Hybrid Solver — Design Spec

**Date:** 2026-05-20
**Goal:** Replace the pure-Adam ODIL hydraulic solver with a two-phase approach: Newton-Raphson (GGA) for pressure-flow, Adam for temperatures. Fix parallel branch flow distribution and chip temperature convergence.

---

## 1. Problem Statement

The current ODIL solver treats all state variables (P, m, T_w, T_s) as free parameters optimized by Adam. This fails on parallel branches because:

- Momentum weight 1e-6 creates ~0.002 Pa/step gradient on junction pressures that need to move ~10,000 Pa.
- Adam (first-order) cannot navigate the narrow valleys created by the quadratic headloss `dp = R·m²` at junction nodes.
- The LR scheduler reduces lr before pressures converge.

**Evidence:**
- LiquidSingle (series): CP flow 0.18% error, but chip T 33K off due to pipe IC contamination.
- Liquid.cfdproj (4 parallel racks): per-rack flows 32-119% error, Rack 4 has negative flow.

The literature (see `Ref_paper/LiquidCooling/literature_review.md`) confirms: hydraulic networks require second-order methods (Newton-Raphson). The segregated two-phase approach (Newton hydraulics + transient thermal) is the dominant paradigm in district heating and DC liquid cooling simulation.

---

## 2. Architecture

```
hybrid_solve(graph, config):
  
  Phase 1: GGA Newton Hydraulic Solve
    Input:  CompiledGraph, current T field (for fluid properties)
    Output: P [N_nodes, N_t], m [N_edges, N_t]
    Method: Global Gradient Algorithm (Todini & Pilati 1988)
            - Schur complement: S = A · diag(1/g) · Aᵀ  [N_junctions × N_junctions]
            - Dense solve: torch.linalg.solve(S, b)
            - Flow update: m -= (1/g) · (dp - Aᵀ · H)
            - 5-15 iterations, quadratic convergence
    Differentiable: Yes, via torch.autograd.Function + implicit differentiation

  Phase 2: Adam Thermal Solve
    Input:  P, m from Phase 1 (differentiable), CompiledGraph
    Output: T_w [N_cells, N_t], T_s [N_cp, N_t]
    Method: Existing Adam ODIL on energy + IC residuals only
            - P and m are NOT nn.Parameters — they're fixed tensors from Phase 1
            - Only T_w and T_s are optimized
            - Existing cold_plate, pipe energy handlers unchanged
    
  Outer coupling (optional):
    Re-run Phase 1 if max ΔT > threshold (re-evaluate ρ, μ at new T)
    Typically 1-2 outer iterations for water in 10-65°C range.
```

---

## 3. New File: `src/backend/liquidcooling/newton_hydraulic.py`

### 3.1 Unified Headloss Interface

Each edge type provides headloss `dp` and its derivative `g = d(dp)/d(m)`:

```python
def edge_headloss(m, edge_type, params, rho, mu, g_accel):
    """Compute headloss and derivative for one edge.
    
    Returns: (dp, g) where dp = pressure drop [Pa], g = d(dp)/d(m) [Pa·s/kg]
    """
```

| Edge Type | dp(m) | g = d(dp)/d(m) |
|-----------|-------|----------------|
| Pipe | (f·L/D + K)·m·\|m\|/(2ρA²) + ρ·g·dh | (f·L/D + K)·2·\|m\|/(2ρA²) |
| Cold plate | dp_a·m·\|m\| + dp_b·m | dp_a·2·\|m\| + dp_b |
| Heat exchanger | dp_a·m·\|m\| + dp_b·m | dp_a·2·\|m\| + dp_b |
| Pump | -ρ·g·(H0 - a·Q²)·N² | 2·g·a·m/ρ·N² |
| Valve | ISA: sign(dP)·(Kv·√(\|dP_bar\|/ρ_rel))·ρ/3600 | (derived from ISA eq.) |

The friction factor `f` uses the existing Swamee-Jain + laminar blend (already differentiable in the pipe handler).

**Gravity handling:** For pipes, `dp` includes the gravity term `ρ·g·dh`. This is a constant offset (independent of m) and does NOT contribute to the derivative `g`. The GGA naturally handles gravity as part of the headloss — no special treatment needed.

### 3.2 GGA Newton Solver

```python
def gga_solve(graph, T_field, tol=1e-8, max_iter=50):
    """Solve hydraulic network using Global Gradient Algorithm.
    
    Args:
        graph: CompiledGraph with topology and component parameters
        T_field: current temperature field (for fluid property evaluation)
        tol: convergence tolerance on max |Δm| [kg/s]
        max_iter: maximum Newton iterations
    
    Returns: (P, m) — nodal pressures and edge mass flows
    """
```

**Algorithm per iteration:**
1. Compute per-edge `(dp_e, g_e)` from current `m` using `edge_headloss()`
2. Build incidence matrix `A` [N_free_nodes × N_edges] (rows for non-BC nodes only)
3. Assemble Schur complement: `S = A · diag(1/g) · Aᵀ` — dense [N_free × N_free]
4. Assemble RHS: `b = A · (m + dp/g) + contributions from BC nodes`
5. Solve: `H = torch.linalg.solve(S, b)` — gives junction pressures
6. Update flows: `m_new = m - (1/g) · (dp - Aᵀ_full · H_full)` where `H_full` includes BC pressures
7. Check convergence: `max|m_new - m| < tol`

**BC handling:** BC nodes have fixed pressures (from `graph.bc_P`). They are eliminated from the Schur system — their contribution moves to the RHS. Dead-end edges get `m = 0` (enforced, not solved).

**Zero-flow protection:** When `|m| < m_threshold` (e.g., 1e-8 kg/s), switch to linear headloss `dp = g_linear · m` to prevent singular Jacobians.

### 3.3 Differentiable Wrapper

```python
class GGASolveFunction(torch.autograd.Function):
    @staticmethod
    def forward(ctx, pipe_params, pump_params, ..., topology):
        H, m = gga_solve(topology, ...)
        ctx.save_for_backward(H, m, ...)
        return H, m
    
    @staticmethod
    def backward(ctx, grad_H, grad_m):
        # Implicit function theorem:
        # F(H, m, θ) = 0 at convergence
        # dH/dθ = -[∂F/∂H]⁻¹ · [∂F/∂θ]
        # Solve adjoint: Jᵀ · λ = [grad_H; grad_m]
        # Then: grad_θ = -λᵀ · ∂F/∂θ
        ...
```

Memory cost: O(1) in Newton iterations — only the converged state is stored, not intermediate iterates.

### 3.4 Per-Timestep vs Steady-State

The hydraulic system reaches steady state in milliseconds (pressure waves at ~1000 m/s). For our dt=1s timesteps, the hydraulic solution is effectively steady-state at each timestep.

**Option A (simpler):** Solve hydraulics ONCE for the entire simulation. Assume flow doesn't change over the 10-timestep window. This is valid when BCs (pump speed, valve position) are constant.

**Option B (general):** Solve hydraulics at each timestep. Needed when events change BCs mid-simulation.

**Recommendation:** Start with Option A (single solve, all timesteps share the same P, m). Add per-timestep solve later when events are implemented.

---

## 4. Modified File: `src/backend/liquidcooling/solver.py`

Add `hybrid_solve()` alongside existing `solve()`:

```python
def hybrid_solve(model, config, bc_enforcer=None, callback=None):
    """Two-phase hybrid solver: Newton hydraulics + Adam thermal."""
    
    # Phase 1: Newton hydraulics
    P, m = GGASolveFunction.apply(model.graph, ...)
    
    # Set hydraulic state (not as nn.Parameters)
    model.set_hydraulic_state(P, m)
    
    # Phase 2: Adam thermal
    # Only T_w and T_s are optimized
    optimizer = torch.optim.Adam(model.thermal_parameters(), lr=config.lr)
    for epoch in range(config.max_epochs):
        losses = model.thermal_forward()  # energy + IC residuals only
        losses["total"].backward()
        optimizer.step()
        ...
    
    return logs
```

The existing `solve()` function is kept for backward compatibility. The `hybrid_solve()` is the new default called by the runner.

---

## 5. Modified File: `src/backend/liquidcooling/model.py`

Add methods to `LiquidODILModel`:

- `set_hydraulic_state(P, m)` — store P, m as non-parameter tensors
- `thermal_parameters()` — return only T_w and T_s parameters
- `thermal_forward()` — compute only energy + IC losses (no mass, no momentum, no BC_P)

The existing `forward()` method is unchanged for backward compatibility.

---

## 6. Integration: `src/backend/liquidcooling/runner.py`

Change the runner to call `hybrid_solve()` instead of `solve()`:

```python
# Before:
logs = solve(model, config.solver, bc_enforcer=enforcer)

# After:
logs = hybrid_solve(model, config.solver, bc_enforcer=enforcer)
```

Fallback: if `hybrid_solve` fails (e.g., Newton diverges), fall back to the existing `solve()`.

---

## 7. What Stays the Same

- All handler physics (pipe, cold_plate, pump, valve, heat_exchanger)
- CompiledGraph structure and compiler
- Schema and liquidrun format
- BoundaryConditionEnforcer (dead-end detection, BC clamping)
- StateInitializer (still used for the initial guess to Newton)
- Frontend/server integration
- SSE reporting format

---

## 8. Success Criteria

| Test Case | Metric | Target |
|-----------|--------|--------|
| LiquidSingle (series) | CP flow error | < 1% |
| LiquidSingle | Chip T(9) error vs analytical | < 5K |
| Liquid.cfdproj (4 parallel racks) | Per-rack flow error | < 5% |
| Liquid.cfdproj | Chip T(9) error vs analytical | < 5K |
| CDU loop test (test_cdu_hx.py) | Converges | loss < 5000 |
| Single rack test | All assertions pass | — |
| Full regression | 178 tests pass | — |
| Newton convergence | Iterations | < 20 |
| Wall-clock time | Compared to pure Adam | ≤ current |

---

## 9. Non-Goals (Explicitly Out of Scope)

- Sparse GPU solver (torch-sla) — only needed for >100 node networks
- Explicit upwind FV thermal solver — keep Adam for now
- Per-timestep hydraulic re-solve — single steady-state solve for now
- Events (pump speed changes mid-simulation) — future work
- Two-phase flow or supercritical fluids — single-phase water only
