# Sub-C2: Frontend Solver UI + Real-Time Convergence Monitoring

## Goal

Expose all ODIL solver settings (time stepping, optimizer, loss weights, device) in the liquid solver tab, replace the broken SSE/fetch mismatch with proper EventSource streaming, and add a real-time convergence monitoring window with per-CP-CU temperature/flow tracking — reusing the air-mode `_sm*` monitoring architecture.

## Background

The liquid ODIL solver (Sub-A + Sub-B + Sub-C1 complete, 148 tests passing) currently hardcodes all solver parameters in `liquid_graphgen.js`: `dt_s=1.0`, `N_t=10`, `bdf_order=1`, `lr=0.1`, `max_epochs=10000`, `convergence_tol=1e-10`, and all loss weights. Users cannot tune these without editing code.

Additionally:
- `server.py` `/api/liquid/solve` sets SSE headers but sends only ONE final message; frontend uses `resp.json()` — a protocol mismatch
- `solver.py` already has a per-epoch callback mechanism but `server.py` doesn't wire it to SSE
- No convergence monitoring exists — solve runs silently until completion
- The air-mode monitoring system (`_sm*` functions in STL_generate.html) provides a proven, reusable pattern: floating canvas window, right-sidebar checkbox panel, SSE data flow, custom Chart 2D rendering

## Scope

| In Scope | Out of Scope |
|----------|--------------|
| Solver settings UI (dt/N_t/bdf_order/lr/weights/device) | Results visualization (Sub-D) |
| SSE streaming with per-epoch data | 3D temperature rendering (Sub-D) |
| Floating convergence monitor window | New physics or solver changes |
| Right-sidebar CP-CU selection panel | Backend test changes |
| Per-CP temperature/flow monitoring | |

---

## Design

### 1. Solver Settings Panel

**Location:** Liquid solver tab (`liquid-cooling-solver-group`), between enable/fluid controls and the Run button.

Three collapsible sections (default collapsed, `<details>` elements):

#### 1a. Time Stepping

| Field | Key | Type | Default | Range |
|-------|-----|------|---------|-------|
| Time step (s) | `dt_s` | number | 1.0 | min=0.001, step=0.1 |
| Time steps | `N_t` | integer | 10 | min=1, step=1 |
| BDF order | `bdf_order` | select | 1 | 1, 2, 3 |

#### 1b. Optimizer

| Field | Key | Type | Default | Range |
|-------|-----|------|---------|-------|
| Learning rate | `lr` | number | 0.1 | min=1e-6, step=0.01 |
| Max epochs | `max_epochs` | integer | 10000 | min=100, step=1000 |
| Convergence tol | `convergence_tol` | number | 1e-6 | min=1e-15, step=1e-7 |
| Device | `device` | select | cpu | cpu, cuda |

#### 1c. Loss Weights (Advanced)

| Field | Key | Default |
|-------|-----|---------|
| Mass | `mass` | 1.0 |
| Momentum | `momentum` | 1e-6 |
| Energy | `energy` | 1.0 |
| BC | `bc` | 100.0 |
| IC | `ic` | 10.0 |

**State storage:** `state.liquidCooling.solver` object:

```javascript
solver: {
    dt_s: 1.0,
    N_t: 10,
    bdf_order: 1,
    lr: 0.1,
    max_epochs: 10000,
    convergence_tol: 1e-6,
    device: 'cpu',
    weights: { mass: 1.0, momentum: 1e-6, energy: 1.0, bc: 100.0, ic: 10.0 }
}
```

**UI update function:** `window.updateLiquidSolverParam(key, value)` — updates `state.liquidCooling.solver[key]` (or nested `weights[key]`), calls `logOperation`.

### 2. Graph Generation: Read Settings from State

**Location:** `liquid_graphgen.js` `buildLiquidrunJson()`

Replace hardcoded `time` and `solver` objects with reads from `liquidState.solver`:

```javascript
// Currently hardcoded:
time: { dt_s: 1.0, N_t: 10, bdf_order: 1 }
solver: { optimizer: 'adam', lr: 0.1, max_epochs: 10000, ... }

// After: read from state with fallbacks
var solverCfg = liquidState.solver || {};
time: {
    dt_s:      solverCfg.dt_s      != null ? solverCfg.dt_s      : 1.0,
    N_t:       solverCfg.N_t       != null ? solverCfg.N_t       : 10,
    bdf_order: solverCfg.bdf_order != null ? solverCfg.bdf_order : 1
}
solver: {
    optimizer: 'adam',
    lr:               solverCfg.lr               != null ? solverCfg.lr               : 0.1,
    max_epochs:       solverCfg.max_epochs       != null ? solverCfg.max_epochs       : 10000,
    convergence_tol:  solverCfg.convergence_tol  != null ? solverCfg.convergence_tol  : 1e-6,
    weights: solverCfg.weights || { mass: 1.0, momentum: 1e-6, energy: 1.0, bc: 100.0, ic: 10.0 }
}
```

### 3. SSE Streaming Architecture

#### 3a. Server Side (`server.py`)

Refactor `/api/liquid/solve` to stream per-epoch SSE:

```
Client: POST /api/liquid/solve {device: "cpu", monitor_cps: ["srv1_cp0", ...]}
Server: 200 OK, Content-Type: text/event-stream

data: {"stage": "compiling"}

data: {"stage": "solving", "max_epochs": 10000}

data: {"stage": "epoch", "epoch": 0, "total": 23330.0,
       "losses": {"mass": 0.0, "pipe_momentum": 23330.0, ...},
       "wall_time_s": 0.3,
       "cp_monitor": {
         "srv1_cp0": {"T_chip": 340.0, "T_water_out": 308.15, "m": 1.0},
         ...
       }}

data: {"stage": "epoch", "epoch": 100, ...}

... (every print_every=100 epochs)

data: {"stage": "done", "converged": true, "final_loss": 0.05, "epochs": 3200}
```

**Implementation:** Pass a `callback` to `run_liquid_solve()` that:
1. Receives `SolveLog` dict from `solver.py`
2. Extracts `cp_monitor` data from model state (T_s, T_water_out via NTU, m for each monitored CP edge)
3. Calls `_send_sse()` with the combined payload

#### 3b. Runner Side (`runner.py`)

Add a `_cp_monitor_callback_wrapper` that wraps the user callback to extract per-CP data:

```python
def _extract_cp_monitor(model, graph, monitor_cp_ids):
    """Extract T_chip, T_water_out, m for monitored cold plates."""
    result = {}
    if not monitor_cp_ids or graph.cp_indices is None:
        return result
    with torch.no_grad():
        T_all = model._build_T_all_nodes(dict(model.state))
        for cp_str_id in monitor_cp_ids:
            # Find global edge index for this CP
            if cp_str_id not in graph.edge_str_ids:
                continue
            global_idx = graph.edge_str_ids.index(cp_str_id)
            local_mask = (graph.cp_indices.edge_ids == global_idx)
            if not local_mask.any():
                continue
            local_idx = local_mask.nonzero(as_tuple=True)[0][0].item()
            result[cp_str_id] = {
                "T_chip": model.state["T_s"][local_idx, -1].item(),
                "T_water_out": _compute_cp_water_out(model, graph, global_idx, local_idx, T_all),
                "m": model.state["m"][global_idx, -1].abs().item(),
            }
    return result
```

#### 3c. Frontend Side (`liquid_cooling.js`)

Replace `fetch().then(resp.json())` with EventSource pattern:

```javascript
window.runLiquidSolve = async function() {
    var exported = await window.exportLiquidrun();
    if (!exported) return null;

    // Get monitoring config from right sidebar
    var monCfg = window.lsmGetMonitoringConfig();

    // Start monitoring window
    if (monCfg.loss || monCfg.cpList.length > 0) {
        window._lsmStartMonitoring(monCfg);
    }

    // POST to start solve, receive SSE stream
    try {
        var resp = await fetch('/api/liquid/solve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                device: (state.liquidCooling.solver || {}).device || 'cpu',
                monitor_cps: monCfg.cpList.map(function(cp) { return cp.id; })
            })
        });

        // Read SSE from response body stream
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';

        while (true) {
            var chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });

            // Parse SSE lines
            var lines = buffer.split('\n');
            buffer = lines.pop();  // keep incomplete line
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (line.startsWith('data: ')) {
                    var payload = JSON.parse(line.substring(6));
                    window._lsmOnSSE(payload);
                }
            }
        }
    } catch (e) {
        alert('液冷求解错误: ' + e.message);
    }
};
```

Note: Using `fetch()` + `ReadableStream` reader instead of `EventSource` because the POST body (with `monitor_cps`) cannot be sent via EventSource (GET only). This is the standard pattern for POST-initiated SSE.

### 4. Convergence Monitor Window

**New file:** `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`

Replicates the `_sm*` monitoring architecture with `_lsm*` prefix (liquid solver monitor):

| Air-mode function | Liquid-mode equivalent | Purpose |
|-------------------|----------------------|---------|
| `_smCreateMonitorWindow` | `_lsmCreateMonitorWindow` | Create floating canvas window |
| `_smDrawChart` | `_lsmDrawChart` | Render chart on canvas (log/linear) |
| `_smUpdateLegend` | `_lsmUpdateLegend` | Update series legend with toggle |
| `_smResizeCanvas` | `_lsmResizeCanvas` | Handle DPI + resize |
| `_smParseLogLine` | — (not needed) | Air-mode parses text; liquid uses structured JSON |
| `smGetMonitoringConfig` | `lsmGetMonitoringConfig` | Read checkbox state from right sidebar |

**Monitor window tabs:**

| Tab | Data source | Y-axis | Series |
|-----|------------|--------|--------|
| Loss | `epoch.losses` | log₁₀ | total, mass, pipe_momentum, cold_plate_energy, bc_P, ... |
| Temperature | `epoch.cp_monitor[].T_chip/T_water_out` | linear (°C) | Per-CP chip/water lines |
| Flow | `epoch.cp_monitor[].m` | linear (kg/s) | Per-CP flow lines |

**Data storage:**

```javascript
window._liquidSolverMonitor = {
    active: false,
    losses: {},        // { "total": [{epoch, value}], "mass": [...], ... }
    temperatures: {},  // { "srv1_cp0_T_chip": [{epoch, value}], ... }
    flows: {},         // { "srv1_cp0_m": [{epoch, value}], ... }
};
```

### 5. Right Sidebar CP-CU Monitoring Panel

**Location:** `STL_generate.html`, new `<div id="liquid-solver-monitoring-right">` (hidden by default, shown when solve starts).

**Structure:**

```html
<div id="liquid-solver-monitoring-right" style="display:none;">
  <div class="header">🌊 液冷求解监控</div>
  <div style="padding:8px; border-bottom:1px solid #333;">
    <label><input type="checkbox" id="lsm-loss-cb" checked> 监控 Loss 收敛</label>
  </div>
  <div style="padding:8px;">
    <div style="display:flex; justify-content:space-between;">
      <label>📊 CP-CU 监控</label>
      <div><button onclick="window.lsmToggleAllCP(true)">全选</button>
           <button onclick="window.lsmToggleAllCP(false)">全不选</button></div>
    </div>
    <div id="lsm-cp-list">
      <!-- Dynamically populated from graph racks.servers.cp_cu_pairs -->
    </div>
  </div>
</div>
```

**Population:** `window.lsmLoadCPList()` reads the current `.liquidrun` graph JSON (built by `buildLiquidrunJson()` before solve) and extracts all `cp_cu_pairs` from `racks[].servers[]`. Each entry becomes a checkbox row showing `{rackId}_{serverId}_{cpId} ({Q_W}W)`.

**Activation flow:**
1. User clicks "Run Liquid Solve"
2. `exportLiquidrun()` builds graph JSON
3. `lsmLoadCPList()` populates right sidebar from graph's rack data
4. Right sidebar switches to `liquid-solver-monitoring-right`
5. `lsmGetMonitoringConfig()` reads checkbox states
6. Monitoring window created, SSE stream starts
7. On solve completion, monitoring window stays (user can examine final state)
8. Right sidebar stays until user clicks elsewhere or switches mode

---

## Files Changed

| File | Change |
|------|--------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Solver settings UI in liquid tab + right sidebar monitoring panel + `state.liquidCooling.solver` init + `<script>` tag for new JS |
| `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js` | Read solver settings from `liquidState.solver` instead of hardcoded |
| `STLGeneratorForOpenFoam/web/js/liquid_cooling.js` | Rewrite `runLiquidSolve()` for SSE streaming + monitoring activation |
| `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js` (new) | Floating monitor window, canvas chart, legend, CP-list population |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | i18n keys for solver settings + monitoring UI |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | Same |
| `STLGeneratorForOpenFoam/server/server.py` | `/api/liquid/solve` streams per-epoch SSE with callback + cp_monitor |
| `src/backend/liquidcooling/runner.py` | `run_liquid_solve()` wraps callback with cp_monitor extraction |

## Testing Strategy

- **Manual via browser-use:** Start server, open liquid mode, set solver params, run solve, verify monitoring window shows real-time loss curves
- **No new pytest tests:** Pure frontend UI + server endpoint changes with no new physics logic
