# Sub-C1: CDU Boundary Conditions + Topology Validation

## Goal

Fix CDU boundary condition semantics (Dirichlet supply temperature + free-outflow return temperature), expose supply pressure in the CDU inspector panel, handle blind-end pipes in graph compilation, and add topology validation ensuring complete CDU→RACK→CDU loops through cold plates.

## Background

The liquid ODIL solver (Sub-A + Sub-B complete, 136 tests passing) currently treats **all** `pressure_bc` nodes identically for temperature: every BC node gets a fixed Dirichlet temperature from `bc_T`. This is wrong for CDU return nodes, which should have **free outflow** temperature (the return water temperature is determined by the network, not prescribed).

Additionally:
- CDU supply/return pressures are hardcoded in `liquid_graphgen.js` (250kPa / 100kPa)
- Pipe blind ends (`blindEnds` flags) are stored in UI state but ignored during graph compilation
- Topology validation only checks node references and duplicates — no loop completeness check

## Scope

| In Scope | Out of Scope |
|----------|--------------|
| CDU BC semantics (Dirichlet vs free-outflow T) | Solver settings UI (Sub-C2) |
| Supply pressure in CDU panel | Optimizer/loss weight panels (Sub-C2) |
| Blind end pipe compilation | Convergence monitoring (Sub-C2) |
| Topology validation (BFS loop check) | Results visualization (Sub-D) |
| Backend tests for new BC logic | Frontend E2E tests |

---

## Design

### 1. CDU Boundary Condition Model

Each CDU produces two `pressure_bc` nodes and one `pump` edge:

| Node | Pressure BC | Temperature BC | UI Exposure |
|------|------------|----------------|-------------|
| `{cdu}_supply` | Dirichlet, user-specified (default 250 kPa) | Dirichlet, user-specified (`supplyTemperature`, already exists) | Supply Pressure input in CDU panel |
| `{cdu}_return` | Dirichlet, fixed 101325 Pa | **None** (free outflow — treated as junction for temperature) | Hidden; help text explains |

The return node's `T_K` field is omitted from the `.liquidrun` JSON (`"bc": {"P_Pa": 101325}`), signaling to the backend that no temperature constraint applies.

### 2. Backend: `bc_T_mask` Tensor

**Problem:** Currently `compiler.py` fills `bc_T[i]` with `ic.T_fluid_K` when `node.bc.T_K is None`, and `model.py` applies Dirichlet temperature to **all** `bc_mask=True` nodes indiscriminately.

**Solution:** Add a separate `bc_T_mask: Tensor [N_nodes] bool` to `CompiledGraph`:

- `bc_T_mask[i] = True` — node has explicit `T_K` → Dirichlet temperature
- `bc_T_mask[i] = False` — node has no `T_K` → free outflow (compute temperature functionally)

#### `compiled_graph.py`

Add field after `bc_T`:

```python
bc_T_mask: Tensor  # [N_nodes] bool — True where T is prescribed (Dirichlet)
```

#### `compiler.py`

Change the node compilation loop:

```python
bc_T_mask = torch.zeros(N_nodes, dtype=torch.bool)

for i, node in enumerate(config.nodes):
    if node.type == "pressure_bc":
        bc_mask[i] = True
        if node.bc is not None:
            bc_P[i] = node.bc.P_Pa
            if node.bc.T_K is not None:
                bc_T[i] = node.bc.T_K
                bc_T_mask[i] = True
            else:
                bc_T[i] = ic.T_fluid_K  # placeholder, won't be used
                bc_T_mask[i] = False
        else:
            bc_P[i] = ic.P_uniform_Pa
            bc_T[i] = ic.T_fluid_K
            bc_T_mask[i] = False
```

#### `model.py` `_build_T_all_nodes()`

Only apply Dirichlet temperature to nodes where `bc_T_mask=True`. Nodes with `bc_mask=True` but `bc_T_mask=False` participate in flow-weighted junction mixing:

```python
# Dirichlet temperature only where bc_T_mask is True
if self.bc_T_mask.any():
    dirichlet_idx = self.bc_T_mask.nonzero(as_tuple=True)[0]
    T_all[dirichlet_idx] = self.bc_T[dirichlet_idx].unsqueeze(1).expand(-1, N_t)

# Flow-weighted mixing for junctions AND free-outflow BC nodes
mixing_mask = self.junction_mask | (self.bc_mask & ~self.bc_T_mask)
```

### 3. Blind End Pipe Handling

**Location:** `liquid_graphgen.js`, pipe compilation section.

When a pipe has `blindEnds.start = true` or `blindEnds.end = true`:

1. Create a `junction` type node at the blind end:
   - ID: `{pipeId}_blind_start` or `{pipeId}_blind_end`
   - Position: pipe's start/end waypoint coordinates
   - No `bc` field (pure junction)

2. Connect the pipe edge from/to this junction node instead of leaving it dangling.

3. The blind-end junction has exactly 1 connected edge → mass conservation enforces `m = 0` for that pipe.

4. If both ends are blind, two junction nodes are created — the pipe is fully isolated.

**No backend changes needed.** Existing junction handling + mass conservation produces the correct physics.

### 4. Topology Validation

**Location:** `liquid_graphgen.js` `_validateGraph()`, appended after existing checks.

#### Algorithm

For each CDU (identified by `{cduName}_supply` / `{cduName}_return` node pairs):

```
function validateCduLoop(graph, supplyNodeId, returnNodeId):
    // Build adjacency from edges
    adj = buildAdjacency(graph.edges, graph.nodes)
    
    // BFS from supply node
    visited = {}
    queue = [{node: supplyNodeId, passedColdPlate: false}]
    reachedReturn = false
    reachedReturnViaColdPlate = false
    
    while queue not empty:
        {node, passedColdPlate} = queue.shift()
        if visited[node] with passedColdPlate already: continue
        visited[node + ':' + passedColdPlate] = true
        
        for each neighbor (nextNode, edgeType) of node:
            newPassedCP = passedColdPlate || (edgeType === 'cold_plate')
            if nextNode === returnNodeId:
                reachedReturn = true
                if newPassedCP: reachedReturnViaColdPlate = true
            else:
                queue.push({node: nextNode, passedColdPlate: newPassedCP})
    
    if !reachedReturn:
        error("CDU loop broken: cannot reach return from supply")
    else if !reachedReturnViaColdPlate:
        error("CDU short circuit: supply reaches return without passing through any cold plate")
```

#### Additional Check

For each RACK's served cold plate edges: verify they appear in at least one CDU supply→return path. If not, emit warning: `"RACK {rackId} cold plate {cpId} is not on any CDU loop"`.

#### Error Handling

- Validation errors **block** the `.liquidrun` export
- Errors are shown to the user via `alert()` (consistent with existing validation error display)
- Console warnings for non-critical issues (unreachable cold plates)

### 5. Frontend CDU Panel Change

**Location:** `STL_generate.html` `renderCduModelInspector()`.

Add one new input field after "Supply Temperature":

```html
<div class="prop-row">
  <label>Supply Pressure (kPa)</label>
  <input type="number" value="{supplyPressure}" step="10" min="100"
         onchange="updateCduLiquidParam(modelId, 'supplyPressure', parseFloat(this.value))">
  <div class="prop-help">Return pressure fixed at 101.325 kPa (atmospheric)</div>
</div>
```

**State:** `model.liquidCooling.supplyPressure` (default: 250).

**Initialization:** In CDU model creation (`createCduModel()`), add `supplyPressure: 250` to the `liquidCooling` object.

### 6. `liquid_graphgen.js` CDU Compilation Changes

Current hardcoded values replaced:

```javascript
// Supply node
bc: {
    P_Pa: cdu.liquidCooling.supplyPressure * 1000,  // kPa → Pa
    T_K: cdu.liquidCooling.supplyTemperature + 273.15
}

// Return node  
bc: {
    P_Pa: 101325  // fixed atmospheric reference
    // T_K intentionally omitted → free outflow
}
```

---

## Files Changed

| File | Change |
|------|--------|
| `src/backend/liquidcooling/compiled_graph.py` | Add `bc_T_mask: Tensor` field |
| `src/backend/liquidcooling/compiler.py` | Populate `bc_T_mask` based on `T_K` presence |
| `src/backend/liquidcooling/model.py` | Use `bc_T_mask` in `_build_T_all_nodes()` |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | CDU inspector: add supply pressure input |
| `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js` | CDU pressures from config, blind end nodes, topology BFS |
| `src/backend/tests/test_liquid/test_compiled_graph.py` | Add `bc_T_mask` to test helper |
| `src/backend/tests/test_liquid/test_bc_t_mask.py` (new) | Tests for Dirichlet vs free-outflow temperature |
| `src/backend/tests/test_liquid/test_blind_end.py` (new) | Tests for blind-end pipe compilation + m=0 |

## Testing Strategy

### Backend Tests (pytest)

1. **bc_T_mask compilation** — supply node with T_K → mask=True; return node without T_K → mask=False
2. **Free-outflow temperature** — return node temperature equals flow-weighted upstream temperature, not a fixed value
3. **Dirichlet temperature preserved** — supply node temperature stays at bc_T value
4. **Blind end m=0** — compile a graph with one blind-end pipe, verify ODIL converges with m≈0 for that edge
5. **Existing tests pass** — all 136 existing tests must continue passing

### Frontend Validation (manual via browser-use)

1. CDU panel shows supply pressure input with correct default
2. Exported .liquidrun has correct supply P, return P=101325, return has no T_K
3. Blind-end pipe creates junction node in .liquidrun
4. Short-circuit topology (no cold plate in loop) triggers error alert
5. Broken loop (disconnected return) triggers error alert
