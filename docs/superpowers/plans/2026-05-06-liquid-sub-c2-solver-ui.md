# Sub-C2: Solver UI + Real-Time Convergence Monitoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose ODIL solver settings in the liquid solver tab, fix SSE streaming to deliver per-epoch data, and add a floating convergence monitor window with per-CP-CU tracking — reusing the air-mode `_sm*` architecture.

**Architecture:** Settings stored in `state.liquidCooling.solver`, read by `liquid_graphgen.js` instead of hardcoded values. Server streams SSE per-epoch via callback; frontend reads with `fetch` + `ReadableStream` reader. New `liquid_solve_monitor.js` creates a floating canvas chart window (`_lsm*` prefix) mirroring the air-mode `_sm*` pattern. Right sidebar shows CP-CU checkbox list for monitoring selection.

**Tech Stack:** Python 3.10, vanilla JS (ES5), HTML5 Canvas 2D, Server-Sent Events

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/backend/liquidcooling/runner.py` | Add `monitor_cp_ids` param + `_extract_cp_monitor()` helper |
| `STLGeneratorForOpenFoam/server/server.py` | Wire callback → SSE stream with cp_monitor data |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Solver settings UI + right-sidebar monitoring panel + state init + script tag |
| `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js` | Read solver settings from `liquidState.solver` |
| `STLGeneratorForOpenFoam/web/js/liquid_cooling.js` | Rewrite `runLiquidSolve()` for SSE + monitoring activation |
| `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js` (new) | Floating monitor window: create/draw/legend/resize/data handlers |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | i18n keys for solver settings + monitoring |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | Same |

---

### Task 1: Backend — Add cp_monitor extraction to runner.py

**Files:**
- Modify: `src/backend/liquidcooling/runner.py`

- [ ] **Step 1: Add `_extract_cp_monitor()` function**

In `src/backend/liquidcooling/runner.py`, add this function before `_extract_results()`:

```python
def _extract_cp_monitor(model, graph, monitor_cp_ids):
    """Extract T_chip, T_water_out, m for monitored cold plates (real-time)."""
    result = {}
    if not monitor_cp_ids or graph.cp_indices is None:
        return result
    with torch.no_grad():
        T_all = model._build_T_all_nodes(dict(model.state))
        for cp_str_id in monitor_cp_ids:
            if cp_str_id not in graph.edge_str_ids:
                continue
            global_idx = graph.edge_str_ids.index(cp_str_id)
            local_mask = (graph.cp_indices.edge_ids == global_idx)
            if not local_mask.any():
                continue
            local_idx = local_mask.nonzero(as_tuple=True)[0][0].item()

            T_chip = model.state["T_s"][local_idx, -1].item()
            m_val = model.state["m"][global_idx, -1].abs().item()

            # NTU-effectiveness for T_water_out
            UA = graph.cp_params.UA[local_idx].item()
            m_abs_cp = m_val * graph.fluid_cp + 1e-10
            NTU = UA / m_abs_cp
            eff = 1.0 - np.exp(-NTU)
            from_node = graph.edge_from[global_idx].item()
            T_in = T_all[from_node, -1].item()
            T_water_out = T_in + eff * (T_chip - T_in)

            result[cp_str_id] = {
                "T_chip": round(T_chip, 2),
                "T_water_out": round(T_water_out, 2),
                "m": round(m_val, 4),
            }
    return result
```

- [ ] **Step 2: Add `monitor_cp_ids` parameter to `run_liquid_solve()`**

Change the function signature from:

```python
def run_liquid_solve(
    liquidrun_path: Path,
    case_dir: Path = None,
    device: str = "cpu",
    callback: Callable = None,
) -> LiquidResult:
```

to:

```python
def run_liquid_solve(
    liquidrun_path: Path,
    case_dir: Path = None,
    device: str = "cpu",
    callback: Callable = None,
    monitor_cp_ids: list[str] = None,
) -> LiquidResult:
```

- [ ] **Step 3: Wrap callback to include cp_monitor data**

In `run_liquid_solve()`, before the `solve()` call (line 76), wrap the callback:

Replace:

```python
    logs = solve(model, config.solver, log_dir=log_dir, callback=callback, print_every=500)
```

with:

```python
    # Wrap callback to include cp_monitor data
    if callback and monitor_cp_ids:
        _orig_cb = callback
        def _monitor_callback(log_dict):
            if log_dict.get("epoch") is not None:
                log_dict["cp_monitor"] = _extract_cp_monitor(
                    model, graph, monitor_cp_ids
                )
            _orig_cb(log_dict)
        solve_callback = _monitor_callback
    else:
        solve_callback = callback

    logs = solve(model, config.solver, log_dir=log_dir, callback=solve_callback, print_every=100)
```

Note: Changed `print_every` from 500 to 100 for more responsive monitoring.

- [ ] **Step 4: Run existing tests to verify nothing breaks**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -q --tb=short`
Expected: All 148 tests PASS (callback wrapping only activates when `monitor_cp_ids` is provided)

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/runner.py
git commit -m "feat(liquid): add cp_monitor extraction to runner callback"
```

---

### Task 2: Backend — Wire SSE streaming in server.py

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py`

- [ ] **Step 1: Rewrite handle_liquid_solve to stream per-epoch SSE**

Find `handle_liquid_solve` (around line 3225) and replace the entire function body (lines 3225-3285) with:

```python
def handle_liquid_solve(handler):
    """POST /api/liquid/solve — Run ODIL liquid cooling solver, streams SSE."""
    if not DCTWIN_AVAILABLE:
        _send_json(handler, 500, {"error": "dcdtuni not available"})
        return
    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set"})
        return

    liquid_dir = case_dir / "Liquid"
    model_dir = liquid_dir / "model"

    liquidrun_files = sorted(model_dir.glob("*.liquidrun"))
    if not liquidrun_files:
        _send_json(handler, 404, {"error": "No .liquidrun file found in " + str(model_dir)})
        return

    liquidrun_path = liquidrun_files[-1]

    try:
        body = _read_body(handler) if handler.headers.get("Content-Length") else {}
    except Exception:
        body = {}
    device = (body or {}).get("device", "cpu")
    monitor_cps = (body or {}).get("monitor_cps", [])

    # Send SSE headers
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    def _send_sse(payload: dict):
        try:
            msg = f"data: {json.dumps(payload)}\n\n"
            handler.wfile.write(msg.encode())
            handler.wfile.flush()
        except BrokenPipeError:
            pass

    _send_sse({"stage": "compiling"})

    try:
        import torch
        torch.set_default_dtype(torch.float64)

        from dcdtuni.liquid.runner import run_liquid_solve

        def _sse_callback(log_dict):
            _send_sse(log_dict)

        result = run_liquid_solve(
            liquidrun_path,
            case_dir=liquid_dir,
            device=device,
            callback=_sse_callback,
            monitor_cp_ids=monitor_cps if monitor_cps else None,
        )
        _send_sse({
            "stage": "done",
            "converged": result.converged,
            "final_loss": result.final_loss,
            "epochs": result.epochs,
        })
    except Exception as e:
        import traceback
        _send_sse({
            "stage": "error",
            "message": str(e),
            "traceback": traceback.format_exc(),
        })
```

- [ ] **Step 2: Commit (inside submodule)**

```bash
cd STLGeneratorForOpenFoam
git add server/server.py
git commit -m "feat(liquid): wire per-epoch SSE callback in /api/liquid/solve"
cd ..
```

---

### Task 3: Frontend — Solver settings UI in STL_generate.html

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `solver` object to `state.liquidCooling` initialization**

Find the `liquidCooling` initialization block (around line 3194). After `results: null`, add a comma and the `solver` object:

```javascript
                results: null,
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

- [ ] **Step 2: Add solver settings collapsible panels in liquid-cooling-solver-group**

Find the `liquid-cooling-solver-group` div (around line 382). Between the coolant select (line 395-396) and the buttons div (line 397), insert the solver settings panels:

```html
                <!-- ODIL Solver Settings -->
                <details style="margin:4px 0; border:1px solid #333; border-radius:4px; background:#0d0d1a;">
                    <summary style="padding:4px 8px; cursor:pointer; font-size:11px; color:#74c7ec;">⏱ ${t('lsm.time_stepping')}</summary>
                    <div style="padding:4px 8px;">
                        <div class="prop-row"><label>${t('lsm.dt_s')}</label><input type="number" step="0.1" min="0.001" value="${(state.liquidCooling.solver||{}).dt_s||1.0}" onchange="window.updateLiquidSolverParam('dt_s', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>${t('lsm.n_t')}</label><input type="number" step="1" min="1" value="${(state.liquidCooling.solver||{}).N_t||10}" onchange="window.updateLiquidSolverParam('N_t', parseInt(this.value))"></div>
                        <div class="prop-row"><label>${t('lsm.bdf_order')}</label><select onchange="window.updateLiquidSolverParam('bdf_order', parseInt(this.value))"><option value="1" ${((state.liquidCooling.solver||{}).bdf_order||1)===1?'selected':''}>BDF1</option><option value="2" ${((state.liquidCooling.solver||{}).bdf_order||1)===2?'selected':''}>BDF2</option><option value="3" ${((state.liquidCooling.solver||{}).bdf_order||1)===3?'selected':''}>BDF3</option></select></div>
                    </div>
                </details>
                <details style="margin:4px 0; border:1px solid #333; border-radius:4px; background:#0d0d1a;">
                    <summary style="padding:4px 8px; cursor:pointer; font-size:11px; color:#74c7ec;">⚙ ${t('lsm.optimizer')}</summary>
                    <div style="padding:4px 8px;">
                        <div class="prop-row"><label>${t('lsm.lr')}</label><input type="number" step="0.01" min="1e-6" value="${(state.liquidCooling.solver||{}).lr||0.1}" onchange="window.updateLiquidSolverParam('lr', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>${t('lsm.max_epochs')}</label><input type="number" step="1000" min="100" value="${(state.liquidCooling.solver||{}).max_epochs||10000}" onchange="window.updateLiquidSolverParam('max_epochs', parseInt(this.value))"></div>
                        <div class="prop-row"><label>${t('lsm.convergence_tol')}</label><input type="number" step="1e-7" min="1e-15" value="${(state.liquidCooling.solver||{}).convergence_tol||1e-6}" onchange="window.updateLiquidSolverParam('convergence_tol', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>${t('lsm.device')}</label><select onchange="window.updateLiquidSolverParam('device', this.value)"><option value="cpu" ${((state.liquidCooling.solver||{}).device||'cpu')==='cpu'?'selected':''}>CPU</option><option value="cuda" ${((state.liquidCooling.solver||{}).device||'cpu')==='cuda'?'selected':''}>CUDA (GPU)</option></select></div>
                    </div>
                </details>
                <details style="margin:4px 0; border:1px solid #333; border-radius:4px; background:#0d0d1a;">
                    <summary style="padding:4px 8px; cursor:pointer; font-size:11px; color:#888;">⚖ ${t('lsm.loss_weights')} (Advanced)</summary>
                    <div style="padding:4px 8px;">
                        <div class="prop-row"><label>Mass</label><input type="number" step="0.1" value="${((state.liquidCooling.solver||{}).weights||{}).mass||1.0}" onchange="window.updateLiquidSolverParam('weights.mass', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>Momentum</label><input type="number" step="1e-7" value="${((state.liquidCooling.solver||{}).weights||{}).momentum||1e-6}" onchange="window.updateLiquidSolverParam('weights.momentum', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>Energy</label><input type="number" step="0.1" value="${((state.liquidCooling.solver||{}).weights||{}).energy||1.0}" onchange="window.updateLiquidSolverParam('weights.energy', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>BC</label><input type="number" step="10" value="${((state.liquidCooling.solver||{}).weights||{}).bc||100.0}" onchange="window.updateLiquidSolverParam('weights.bc', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>IC</label><input type="number" step="1" value="${((state.liquidCooling.solver||{}).weights||{}).ic||10.0}" onchange="window.updateLiquidSolverParam('weights.ic', parseFloat(this.value))"></div>
                    </div>
                </details>
```

**IMPORTANT:** The `liquid-cooling-solver-group` is static HTML (not inside a template literal), so the `${...}` expressions must be replaced with plain default values. The actual state binding happens via `updateUI()` which re-renders the solver tab. Alternatively, use `onchange` handlers that read/write `state.liquidCooling.solver` directly.

Since this panel is static HTML (not dynamically generated like the inspector panels), use fixed default values and bind via `updateUI()`:

```html
                <!-- ODIL Solver Settings -->
                <details id="lsm-time-details" style="margin:4px 0; border:1px solid #333; border-radius:4px; background:#0d0d1a;">
                    <summary style="padding:4px 8px; cursor:pointer; font-size:11px; color:#74c7ec;">⏱ Time Stepping</summary>
                    <div style="padding:4px 8px;">
                        <div class="prop-row"><label>dt (s)</label><input type="number" id="lsm-dt" step="0.1" min="0.001" value="1.0" onchange="window.updateLiquidSolverParam('dt_s', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>N_t</label><input type="number" id="lsm-nt" step="1" min="1" value="10" onchange="window.updateLiquidSolverParam('N_t', parseInt(this.value))"></div>
                        <div class="prop-row"><label>BDF Order</label><select id="lsm-bdf" onchange="window.updateLiquidSolverParam('bdf_order', parseInt(this.value))"><option value="1">BDF1</option><option value="2">BDF2</option><option value="3">BDF3</option></select></div>
                    </div>
                </details>
                <details id="lsm-optim-details" style="margin:4px 0; border:1px solid #333; border-radius:4px; background:#0d0d1a;">
                    <summary style="padding:4px 8px; cursor:pointer; font-size:11px; color:#74c7ec;">⚙ Optimizer</summary>
                    <div style="padding:4px 8px;">
                        <div class="prop-row"><label>Learning Rate</label><input type="number" id="lsm-lr" step="0.01" min="1e-6" value="0.1" onchange="window.updateLiquidSolverParam('lr', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>Max Epochs</label><input type="number" id="lsm-epochs" step="1000" min="100" value="10000" onchange="window.updateLiquidSolverParam('max_epochs', parseInt(this.value))"></div>
                        <div class="prop-row"><label>Conv. Tol</label><input type="number" id="lsm-tol" step="1e-7" min="1e-15" value="1e-6" onchange="window.updateLiquidSolverParam('convergence_tol', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>Device</label><select id="lsm-device" onchange="window.updateLiquidSolverParam('device', this.value)"><option value="cpu">CPU</option><option value="cuda">CUDA (GPU)</option></select></div>
                    </div>
                </details>
                <details id="lsm-weights-details" style="margin:4px 0; border:1px solid #333; border-radius:4px; background:#0d0d1a;">
                    <summary style="padding:4px 8px; cursor:pointer; font-size:11px; color:#888;">⚖ Loss Weights (Advanced)</summary>
                    <div style="padding:4px 8px;">
                        <div class="prop-row"><label>Mass</label><input type="number" id="lsm-w-mass" step="0.1" value="1.0" onchange="window.updateLiquidSolverParam('weights.mass', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>Momentum</label><input type="number" id="lsm-w-mom" step="1e-7" value="1e-6" onchange="window.updateLiquidSolverParam('weights.momentum', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>Energy</label><input type="number" id="lsm-w-energy" step="0.1" value="1.0" onchange="window.updateLiquidSolverParam('weights.energy', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>BC</label><input type="number" id="lsm-w-bc" step="10" value="100.0" onchange="window.updateLiquidSolverParam('weights.bc', parseFloat(this.value))"></div>
                        <div class="prop-row"><label>IC</label><input type="number" id="lsm-w-ic" step="1" value="10.0" onchange="window.updateLiquidSolverParam('weights.ic', parseFloat(this.value))"></div>
                    </div>
                </details>
```

- [ ] **Step 3: Add `updateLiquidSolverParam` function**

In the JavaScript section of `STL_generate.html` (near other `window.*` functions), add:

```javascript
    window.updateLiquidSolverParam = function(key, value) {
        if (!state.liquidCooling.solver) {
            state.liquidCooling.solver = {
                dt_s: 1.0, N_t: 10, bdf_order: 1, lr: 0.1,
                max_epochs: 10000, convergence_tol: 1e-6, device: 'cpu',
                weights: { mass: 1.0, momentum: 1e-6, energy: 1.0, bc: 100.0, ic: 10.0 }
            };
        }
        if (key.startsWith('weights.')) {
            var wKey = key.split('.')[1];
            if (!state.liquidCooling.solver.weights) {
                state.liquidCooling.solver.weights = { mass: 1.0, momentum: 1e-6, energy: 1.0, bc: 100.0, ic: 10.0 };
            }
            state.liquidCooling.solver.weights[wKey] = value;
        } else {
            state.liquidCooling.solver[key] = value;
        }
        logOperation('updateLiquidSolverParam', 'Solver ' + key + ' = ' + value, {key: key, value: value}, 'modify');
    };
```

- [ ] **Step 4: Add right-sidebar monitoring panel HTML**

After the existing `solver-monitoring-right` div (around line 985), add the liquid monitoring panel:

```html
        <!-- Liquid solver monitoring panel (right) -->
        <div id="liquid-solver-monitoring-right" style="display:none; flex-direction:column; overflow-y:auto; flex:1; padding:0;">
          <div class="header" style="color:#00b4dc;">🌊 液冷求解监控</div>
          <div style="padding:8px; border-bottom:1px solid #333;">
            <label style="font-size:11px; color:#ccc;">
              <input type="checkbox" id="lsm-loss-cb" checked> <span>监控 Loss 收敛</span>
            </label>
          </div>
          <div style="padding:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
              <label style="font-size:11px; color:#aaa;">📊 CP-CU 监控</label>
              <div style="display:flex; gap:2px;">
                <button onclick="window.lsmToggleAllCP(true)" style="font-size:10px; padding:2px 6px; background:#1a2a3a; border:1px solid #2a4a5a; color:#aaa; cursor:pointer;">全选</button>
                <button onclick="window.lsmToggleAllCP(false)" style="font-size:10px; padding:2px 6px; background:#1a2a3a; border:1px solid #2a4a5a; color:#aaa; cursor:pointer;">全不选</button>
              </div>
            </div>
            <div id="lsm-cp-list" style="max-height:500px; overflow-y:auto;">
              <div style="font-size:10px; color:#666; padding:4px;">点击 "Run Liquid Solve" 后自动填充</div>
            </div>
          </div>
        </div>
```

- [ ] **Step 5: Add `<script>` tag for liquid_solve_monitor.js**

After the `liquid_graphgen.js` script tag (line 1036), add:

```html
<script src="js/liquid_solve_monitor.js"></script>
```

- [ ] **Step 6: Commit (inside submodule)**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(liquid): solver settings UI + right-sidebar monitoring panel"
cd ..
```

---

### Task 4: Frontend — Read solver settings in liquid_graphgen.js

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_graphgen.js`

- [ ] **Step 1: Replace hardcoded time and solver objects**

Find the graph assembly section (around lines 607-636). Replace:

```javascript
            time: {
                dt_s:      1.0,
                N_t:       10,
                bdf_order: 1
            },
```

with:

```javascript
            time: {
                dt_s:      solverCfg.dt_s      != null ? solverCfg.dt_s      : 1.0,
                N_t:       solverCfg.N_t       != null ? solverCfg.N_t       : 10,
                bdf_order: solverCfg.bdf_order != null ? solverCfg.bdf_order : 1
            },
```

And replace:

```javascript
            solver: {
                optimizer:        'adam',
                lr:               0.1,
                max_epochs:       10000,
                convergence_tol:  1e-10,
                weights: {
                    mass:      1.0,
                    momentum:  1e-6,
                    energy:    1.0,
                    bc:        100.0,
                    ic:        10.0
                }
            }
```

with:

```javascript
            solver: {
                optimizer:       'adam',
                lr:              solverCfg.lr              != null ? solverCfg.lr              : 0.1,
                max_epochs:      solverCfg.max_epochs      != null ? solverCfg.max_epochs      : 10000,
                convergence_tol: solverCfg.convergence_tol != null ? solverCfg.convergence_tol : 1e-6,
                weights: wCfg
            }
```

- [ ] **Step 2: Add solverCfg and wCfg variable declarations**

Before the graph assembly (`var graph = {`), add:

```javascript
        var solverCfg = liquidState.solver || {};
        var wDef = { mass: 1.0, momentum: 1e-6, energy: 1.0, bc: 100.0, ic: 10.0 };
        var wCfg = solverCfg.weights ? Object.assign({}, wDef, solverCfg.weights) : wDef;
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_graphgen.js
git commit -m "feat(liquid): read solver settings from state instead of hardcoded"
cd ..
```

---

### Task 5: Frontend — Rewrite runLiquidSolve() for SSE streaming

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_cooling.js`

- [ ] **Step 1: Rewrite runLiquidSolve()**

Replace the existing `window.runLiquidSolve` function (lines 105-128) with:

```javascript
/**
 * Run ODIL liquid cooling solver via server API with SSE streaming.
 * Exports .liquidrun first, then streams per-epoch progress.
 */
window.runLiquidSolve = async function() {
  var exported = await window.exportLiquidrun();
  if (!exported) return null;

  // Populate CP-CU list from the exported graph
  if (window.lsmLoadCPList) window.lsmLoadCPList(exported);

  // Show the liquid monitoring right sidebar
  var lsmRight = document.getElementById('liquid-solver-monitoring-right');
  if (lsmRight) lsmRight.style.display = 'flex';

  // Get monitoring config
  var monCfg = window.lsmGetMonitoringConfig ? window.lsmGetMonitoringConfig() : { loss: true, cpList: [] };

  // Start monitoring window
  if (window._lsmStartMonitoring) window._lsmStartMonitoring(monCfg);

  var solverCfg = (state.liquidCooling && state.liquidCooling.solver) || {};

  try {
    var resp = await fetch('/api/liquid/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        device: solverCfg.device || 'cpu',
        monitor_cps: monCfg.cpList.map(function(cp) { return cp.id; })
      })
    });

    if (!resp.ok) {
      var errData = await resp.json();
      alert('液冷求解失败: ' + (errData.error || 'Unknown error'));
      return null;
    }

    // Read SSE from response body stream
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    var lastData = null;

    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });

      var lines = buffer.split('\n');
      buffer = lines.pop();
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (line.startsWith('data: ')) {
          try {
            var payload = JSON.parse(line.substring(6));
            lastData = payload;
            if (window._lsmOnSSE) window._lsmOnSSE(payload);
          } catch (parseErr) {
            console.warn('[liquid_cooling] SSE parse error:', parseErr);
          }
        }
      }
    }

    // Process final result
    if (lastData && lastData.stage === 'done') {
      state.liquidCooling.results = lastData;
      if (window.logOperation) logOperation('runLiquidSolve', 'ODIL solved', {converged: lastData.converged, epochs: lastData.epochs}, 'file');
      return lastData;
    } else if (lastData && lastData.stage === 'error') {
      alert('液冷求解错误: ' + (lastData.message || 'Unknown'));
      return null;
    }

    return lastData;
  } catch (e) {
    alert('液冷求解错误: ' + e.message);
    return null;
  }
};
```

- [ ] **Step 2: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_cooling.js
git commit -m "feat(liquid): rewrite runLiquidSolve for SSE streaming + monitoring"
cd ..
```

---

### Task 6: Frontend — Create liquid_solve_monitor.js

**Files:**
- Create: `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`

- [ ] **Step 1: Create the monitor module**

Create `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`:

```javascript
/**
 * liquid_solve_monitor.js
 * Floating convergence monitor window for ODIL liquid cooling solver.
 * Mirrors the air-mode _sm* monitoring architecture with _lsm* prefix.
 */

(function() {
    'use strict';

    // ------------------------------------------------------------------ //
    // State
    // ------------------------------------------------------------------ //

    window._liquidSolverMonitor = null;
    window._lsmActiveTab = 'loss';
    window._lsmHiddenSeries = {};
    window._lsmFontScale = 1.0;

    var _lsmColors = [
        '#e06c75','#e5c07b','#61afef','#c678dd','#56b6c2',
        '#98c379','#d19a66','#ff6b9d','#4ecdc4','#f7dc6f'
    ];

    var _lsmLossColors = {
        'total': '#ffffff',
        'mass': '#e06c75',
        'pipe_momentum': '#e5c07b',
        'pipe_energy': '#61afef',
        'pipe_ic': '#c678dd',
        'cold_plate_momentum': '#56b6c2',
        'cold_plate_energy': '#98c379',
        'cold_plate_ic': '#d19a66',
        'bc_P': '#ff6b9d',
        'dead_end_flow': '#4ecdc4'
    };

    // ------------------------------------------------------------------ //
    // CP-CU list population
    // ------------------------------------------------------------------ //

    window.lsmLoadCPList = function(graph) {
        var listEl = document.getElementById('lsm-cp-list');
        if (!listEl) return;
        var racks = graph.racks || [];
        if (racks.length === 0) {
            listEl.innerHTML = '<div style="font-size:10px; color:#666; padding:4px;">No CP-CU pairs in scene</div>';
            return;
        }
        listEl.innerHTML = '';
        racks.forEach(function(rack) {
            (rack.servers || []).forEach(function(srv) {
                (srv.cp_cu_pairs || []).forEach(function(cp) {
                    var row = document.createElement('div');
                    row.style.cssText = 'font-size:10px; padding:1px 0; color:#ccc;';
                    var qw = cp.Q_W != null ? cp.Q_W : '?';
                    row.innerHTML = '<label><input type="checkbox" class="lsm-cp-cb" ' +
                        'data-cp-id="' + cp.edge_id + '" ' +
                        'data-label="' + rack.id + '/' + srv.id + '/' + cp.id + '" ' +
                        'checked> ' +
                        rack.id + '/' + srv.id + '/' + cp.id + ' (' + qw + 'W)</label>';
                    listEl.appendChild(row);
                });
            });
        });
    };

    window.lsmToggleAllCP = function(checked) {
        var cbs = document.querySelectorAll('.lsm-cp-cb');
        for (var i = 0; i < cbs.length; i++) cbs[i].checked = checked;
    };

    window.lsmGetMonitoringConfig = function() {
        var lossCb = document.getElementById('lsm-loss-cb');
        var cpCbs = document.querySelectorAll('.lsm-cp-cb:checked');
        var cpList = [];
        for (var i = 0; i < cpCbs.length; i++) {
            cpList.push({
                id: cpCbs[i].getAttribute('data-cp-id'),
                label: cpCbs[i].getAttribute('data-label')
            });
        }
        return {
            loss: lossCb ? lossCb.checked : true,
            cpList: cpList
        };
    };

    // ------------------------------------------------------------------ //
    // Monitoring lifecycle
    // ------------------------------------------------------------------ //

    window._lsmStartMonitoring = function(monCfg) {
        window._liquidSolverMonitor = {
            active: true,
            losses: {},
            temperatures: {},
            flows: {},
            maxEpochs: 10000
        };
        window._lsmHiddenSeries = {};
        window._lsmActiveTab = 'loss';
        _lsmCreateMonitorWindow();
    };

    // ------------------------------------------------------------------ //
    // SSE data handler
    // ------------------------------------------------------------------ //

    window._lsmOnSSE = function(payload) {
        var mon = window._liquidSolverMonitor;
        if (!mon) return;

        if (payload.stage === 'solving') {
            mon.maxEpochs = payload.max_epochs || 10000;
            return;
        }

        if (payload.stage === 'done' || payload.stage === 'error') {
            mon.active = false;
            _lsmDrawChart();
            return;
        }

        // Only process epoch data from solver callback
        if (payload.epoch == null) return;

        var epoch = payload.epoch;
        var losses = payload.losses || {};
        var total = payload.total;

        // Store loss data
        if (total != null) {
            if (!mon.losses['total']) mon.losses['total'] = [];
            mon.losses['total'].push({ epoch: epoch, value: total });
        }
        for (var k in losses) {
            if (!mon.losses[k]) mon.losses[k] = [];
            mon.losses[k].push({ epoch: epoch, value: losses[k] });
        }

        // Store CP monitor data
        var cpMon = payload.cp_monitor;
        if (cpMon) {
            for (var cpId in cpMon) {
                var d = cpMon[cpId];
                var chipKey = cpId + ' T_chip';
                var waterKey = cpId + ' T_out';
                var flowKey = cpId + ' m';

                if (!mon.temperatures[chipKey]) mon.temperatures[chipKey] = [];
                mon.temperatures[chipKey].push({ epoch: epoch, value: d.T_chip });

                if (!mon.temperatures[waterKey]) mon.temperatures[waterKey] = [];
                mon.temperatures[waterKey].push({ epoch: epoch, value: d.T_water_out });

                if (!mon.flows[flowKey]) mon.flows[flowKey] = [];
                mon.flows[flowKey].push({ epoch: epoch, value: d.m });
            }
        }

        _lsmDrawChart();
    };

    // ------------------------------------------------------------------ //
    // Monitor window creation (mirrors _smCreateMonitorWindow)
    // ------------------------------------------------------------------ //

    function _lsmCreateMonitorWindow() {
        // Remove existing
        var existing = document.getElementById('lsm-monitor-window');
        if (existing) existing.remove();

        var win = document.createElement('div');
        win.id = 'lsm-monitor-window';
        win.style.cssText = 'position:fixed; z-index:900; top:60px; right:20px; width:640px; height:400px; ' +
            'display:flex; flex-direction:column; ' +
            'background:#1a1a2e; border:1px solid #555; border-radius:6px; ' +
            'box-shadow:0 4px 20px rgba(0,0,0,0.6);';

        // Header (draggable)
        var header = document.createElement('div');
        header.style.cssText = 'display:flex; align-items:center; justify-content:space-between; ' +
            'padding:4px 8px; background:#252542; border-bottom:1px solid #444; border-radius:6px 6px 0 0; cursor:move; flex-shrink:0;';
        header.innerHTML = '<span style="font-size:11px; color:#00b4dc; font-weight:600;">🌊 液冷求解监控</span>' +
            '<div style="display:flex; gap:4px;">' +
            '<button onclick="window._lsmFontScale=Math.max(0.6,window._lsmFontScale-0.1);window._lsmDrawChart()" style="font-size:10px; background:#1a1a2e; border:1px solid #444; color:#aaa; cursor:pointer; padding:1px 4px; border-radius:2px;">A−</button>' +
            '<button onclick="window._lsmFontScale=Math.min(2,window._lsmFontScale+0.1);window._lsmDrawChart()" style="font-size:10px; background:#1a1a2e; border:1px solid #444; color:#aaa; cursor:pointer; padding:1px 4px; border-radius:2px;">A+</button>' +
            '<button onclick="document.getElementById(\'lsm-monitor-window\').remove()" style="font-size:12px; background:#1a1a2e; border:1px solid #444; color:#e06c75; cursor:pointer; padding:1px 6px; border-radius:2px;">×</button>' +
            '</div>';

        // Make draggable
        var dragging = false, dx = 0, dy = 0;
        header.addEventListener('mousedown', function(e) {
            dragging = true;
            dx = e.clientX - win.offsetLeft;
            dy = e.clientY - win.offsetTop;
        });
        document.addEventListener('mousemove', function(e) {
            if (!dragging) return;
            win.style.left = (e.clientX - dx) + 'px';
            win.style.top = (e.clientY - dy) + 'px';
            win.style.right = 'auto';
        });
        document.addEventListener('mouseup', function() { dragging = false; });

        // Tab bar
        var tabBar = document.createElement('div');
        tabBar.style.cssText = 'display:flex; border-bottom:1px solid #333; flex-shrink:0;';
        var tabs = [
            { id: 'loss', label: 'Loss' },
            { id: 'temperature', label: '温度' },
            { id: 'flow', label: '流量' }
        ];
        tabs.forEach(function(tab) {
            var btn = document.createElement('button');
            btn.id = 'lsm-tab-' + tab.id;
            btn.textContent = tab.label;
            btn.style.cssText = 'flex:1; padding:4px 0; font-size:11px; border:none; cursor:pointer; ' +
                (tab.id === 'loss' ? 'background:#1a1a2e; color:#00b4dc; border-bottom:2px solid #00b4dc;' :
                                      'background:#111; color:#666; border-bottom:2px solid transparent;');
            btn.addEventListener('click', function() {
                window._lsmActiveTab = tab.id;
                tabs.forEach(function(t) {
                    var b = document.getElementById('lsm-tab-' + t.id);
                    if (b) {
                        b.style.background = t.id === tab.id ? '#1a1a2e' : '#111';
                        b.style.color = t.id === tab.id ? '#00b4dc' : '#666';
                        b.style.borderBottom = t.id === tab.id ? '2px solid #00b4dc' : '2px solid transparent';
                    }
                });
                _lsmDrawChart();
            });
            tabBar.appendChild(btn);
        });

        // Body: canvas + legend
        var body = document.createElement('div');
        body.style.cssText = 'display:flex; flex:1; min-height:0; padding:6px;';

        var canvasWrap = document.createElement('div');
        canvasWrap.id = 'lsm-canvas-wrap';
        canvasWrap.style.cssText = 'flex:1; min-width:0; position:relative;';

        var canvas = document.createElement('canvas');
        canvas.id = 'lsm-chart-canvas';
        canvas.style.cssText = 'background:#0d0d1a; border:1px solid #333; border-radius:3px; display:block; width:100%; height:100%;';
        canvasWrap.appendChild(canvas);

        var legend = document.createElement('div');
        legend.id = 'lsm-chart-legend';
        legend.style.cssText = 'width:130px; flex-shrink:0; overflow-y:auto; padding:4px; margin-left:6px; ' +
            'font-family:monospace; font-size:10px; display:flex; flex-direction:column; gap:2px;';

        body.appendChild(canvasWrap);
        body.appendChild(legend);

        win.appendChild(header);
        win.appendChild(tabBar);
        win.appendChild(body);
        document.body.appendChild(win);

        // Resize observer
        var ro = new ResizeObserver(function() { _lsmResizeCanvas(); });
        ro.observe(canvasWrap);
        setTimeout(_lsmResizeCanvas, 50);
    }

    // ------------------------------------------------------------------ //
    // Canvas resize
    // ------------------------------------------------------------------ //

    function _lsmResizeCanvas() {
        var canvas = document.getElementById('lsm-chart-canvas');
        var wrap = document.getElementById('lsm-canvas-wrap');
        if (!canvas || !wrap) return;
        var dpr = window.devicePixelRatio || 1;
        var w = wrap.clientWidth;
        var h = wrap.clientHeight;
        if (w < 10 || h < 10) return;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = w + 'px';
        canvas.style.height = h + 'px';
        _lsmDrawChart();
    }
    window._lsmResizeCanvas = _lsmResizeCanvas;

    // ------------------------------------------------------------------ //
    // Chart drawing (mirrors _smDrawChart)
    // ------------------------------------------------------------------ //

    function _lsmDrawChart() {
        var canvas = document.getElementById('lsm-chart-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var W = canvas.width, H = canvas.height;
        var dpr = window.devicePixelRatio || 1;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var lW = W / dpr, lH = H / dpr;

        var fs = window._lsmFontScale || 1.0;
        var mon = window._liquidSolverMonitor;
        if (!mon) return;

        var activeTab = window._lsmActiveTab;
        var isLog = (activeTab === 'loss');
        var datasets;
        var yLabel;
        if (activeTab === 'loss') {
            datasets = mon.losses;
            yLabel = 'log₁₀(loss)';
        } else if (activeTab === 'temperature') {
            datasets = mon.temperatures;
            yLabel = '°C';
        } else {
            datasets = mon.flows;
            yLabel = 'kg/s';
        }

        var seriesNames = Object.keys(datasets || {});

        // Clear
        ctx.fillStyle = '#0d0d1a';
        ctx.fillRect(0, 0, lW, lH);

        if (seriesNames.length === 0) {
            ctx.fillStyle = '#666';
            ctx.font = Math.round(12 * fs) + 'px monospace';
            ctx.fillText('Waiting for data...', lW / 2 - 60, lH / 2);
            _lsmUpdateLegend(seriesNames, isLog);
            return;
        }

        // Plot margins
        var labelFs = Math.round(9 * fs);
        var ml = Math.max(55, 50 * fs + 5), mr = 10, mt = 10, mb = Math.max(25, 20 * fs + 5);
        var cw = lW - ml - mr, ch = lH - mt - mb;
        if (cw < 20 || ch < 20) return;

        // X range (epochs)
        var xMin = Infinity, xMax = -Infinity;
        for (var k = 0; k < seriesNames.length; k++) {
            if (window._lsmHiddenSeries[seriesNames[k]]) continue;
            var arr = datasets[seriesNames[k]];
            for (var j = 0; j < arr.length; j++) {
                if (arr[j].epoch < xMin) xMin = arr[j].epoch;
                if (arr[j].epoch > xMax) xMax = arr[j].epoch;
            }
        }
        if (xMin === Infinity) { xMin = 0; xMax = 100; }
        if (xMax <= xMin) xMax = xMin + 100;

        // Y range
        var yMin, yMax;
        if (isLog) {
            yMin = -8; yMax = 6;
            for (var k = 0; k < seriesNames.length; k++) {
                if (window._lsmHiddenSeries[seriesNames[k]]) continue;
                var arr = datasets[seriesNames[k]];
                for (var j = 0; j < arr.length; j++) {
                    var lv = Math.log10(Math.max(arr[j].value, 1e-15));
                    if (lv < yMin) yMin = lv;
                    if (lv > yMax) yMax = lv;
                }
            }
            yMin = Math.floor(yMin) - 1;
            yMax = Math.ceil(yMax) + 1;
        } else {
            yMin = Infinity; yMax = -Infinity;
            for (var k = 0; k < seriesNames.length; k++) {
                if (window._lsmHiddenSeries[seriesNames[k]]) continue;
                var arr = datasets[seriesNames[k]];
                for (var j = 0; j < arr.length; j++) {
                    var val = activeTab === 'temperature' ? arr[j].value - 273.15 : arr[j].value;
                    if (val < yMin) yMin = val;
                    if (val > yMax) yMax = val;
                }
            }
            if (yMin === Infinity) { yMin = 0; yMax = 100; }
            var padding = Math.max((yMax - yMin) * 0.1, 0.5);
            yMin -= padding; yMax += padding;
        }

        // Grid
        ctx.strokeStyle = '#222';
        ctx.lineWidth = 0.5;
        ctx.font = labelFs + 'px monospace';
        ctx.fillStyle = '#555';

        // Y grid
        var ySteps = isLog ? (yMax - yMin) : 5;
        for (var i = 0; i <= ySteps; i++) {
            var yVal = isLog ? (yMin + i) : (yMin + i * (yMax - yMin) / 5);
            var py = mt + ch - (yVal - yMin) / (yMax - yMin) * ch;
            ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + cw, py); ctx.stroke();
            var label = isLog ? ('1e' + Math.round(yVal)) : yVal.toFixed(1);
            ctx.fillText(label, 4, py + 3);
        }

        // X grid
        var xSteps = 5;
        for (var i = 0; i <= xSteps; i++) {
            var xVal = xMin + i * (xMax - xMin) / xSteps;
            var px = ml + (xVal - xMin) / (xMax - xMin) * cw;
            ctx.beginPath(); ctx.moveTo(px, mt); ctx.lineTo(px, mt + ch); ctx.stroke();
            ctx.fillText(Math.round(xVal).toString(), px - 10, mt + ch + 14);
        }

        // Y-axis label
        ctx.save();
        ctx.translate(12, mt + ch / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.fillStyle = '#888';
        ctx.fillText(yLabel, -20, 0);
        ctx.restore();

        // X-axis label
        ctx.fillStyle = '#888';
        ctx.fillText('Epoch', ml + cw / 2 - 15, lH - 2);

        // Plot border
        ctx.strokeStyle = '#444';
        ctx.lineWidth = 1;
        ctx.strokeRect(ml, mt, cw, ch);

        // Draw series
        for (var k = 0; k < seriesNames.length; k++) {
            var name = seriesNames[k];
            if (window._lsmHiddenSeries[name]) continue;
            var arr = datasets[name];
            if (arr.length < 2) continue;

            var color = isLog ? (_lsmLossColors[name] || _lsmColors[k % _lsmColors.length])
                              : _lsmColors[k % _lsmColors.length];
            ctx.strokeStyle = color;
            ctx.lineWidth = name === 'total' ? 2 : 1;
            ctx.beginPath();

            for (var j = 0; j < arr.length; j++) {
                var px = ml + (arr[j].epoch - xMin) / (xMax - xMin) * cw;
                var rawVal = isLog ? Math.log10(Math.max(arr[j].value, 1e-15))
                           : (activeTab === 'temperature' ? arr[j].value - 273.15 : arr[j].value);
                var py = mt + ch - (rawVal - yMin) / (yMax - yMin) * ch;
                if (j === 0) ctx.moveTo(px, py);
                else ctx.lineTo(px, py);
            }
            ctx.stroke();
        }

        _lsmUpdateLegend(seriesNames, isLog);
    }
    window._lsmDrawChart = _lsmDrawChart;

    // ------------------------------------------------------------------ //
    // Legend
    // ------------------------------------------------------------------ //

    function _lsmUpdateLegend(seriesNames, isLog) {
        var legend = document.getElementById('lsm-chart-legend');
        if (!legend) return;
        var fs = window._lsmFontScale || 1.0;
        legend.style.fontSize = Math.round(10 * fs) + 'px';
        legend.innerHTML = '';

        if (seriesNames.length === 0) {
            var empty = document.createElement('div');
            empty.style.cssText = 'color:#666; line-height:1.4;';
            empty.textContent = 'Waiting...';
            legend.appendChild(empty);
            return;
        }

        for (var i = 0; i < seriesNames.length; i++) {
            var name = seriesNames[i];
            var color = isLog ? (_lsmLossColors[name] || _lsmColors[i % _lsmColors.length])
                              : _lsmColors[i % _lsmColors.length];
            var hidden = !!window._lsmHiddenSeries[name];
            var item = document.createElement('div');
            item.style.cssText = 'cursor:pointer; display:flex; align-items:flex-start; gap:6px; ' +
                'padding:3px 4px; border:1px solid #2f3448; border-radius:3px; background:#151a28;' +
                (hidden ? ' opacity:0.3;' : '');
            item.innerHTML = '<span style="display:inline-block; width:10px; height:3px; margin-top:6px; flex-shrink:0; background:' + color + '; border-radius:999px;"></span>' +
                '<span style="min-width:0; overflow-wrap:anywhere; word-break:break-word; line-height:1.3; font-size:9px;">' + name + '</span>';
            item.setAttribute('data-series', name);
            item.addEventListener('click', function() {
                var s = this.getAttribute('data-series');
                window._lsmHiddenSeries[s] = !window._lsmHiddenSeries[s];
                _lsmDrawChart();
            });
            legend.appendChild(item);
        }
    }

    console.log('[liquid_solve_monitor.js] Liquid solver monitor loaded');
})();
```

- [ ] **Step 2: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_solve_monitor.js
git commit -m "feat(liquid): add floating convergence monitor window"
cd ..
```

---

### Task 7: Frontend — i18n keys

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add zh-CN keys**

In `zh-CN.json`, add in the `lsm.*` section (near existing `liquid.*` keys):

```json
"lsm.time_stepping": "时间步进",
"lsm.dt_s": "时间步长 (s)",
"lsm.n_t": "时间步数",
"lsm.bdf_order": "BDF 阶数",
"lsm.optimizer": "优化器",
"lsm.lr": "学习率",
"lsm.max_epochs": "最大迭代数",
"lsm.convergence_tol": "收敛阈值",
"lsm.device": "计算设备",
"lsm.loss_weights": "Loss 权重"
```

- [ ] **Step 2: Add en-US keys**

In `en-US.json`, add:

```json
"lsm.time_stepping": "Time Stepping",
"lsm.dt_s": "Time Step (s)",
"lsm.n_t": "Time Steps",
"lsm.bdf_order": "BDF Order",
"lsm.optimizer": "Optimizer",
"lsm.lr": "Learning Rate",
"lsm.max_epochs": "Max Epochs",
"lsm.convergence_tol": "Convergence Tol",
"lsm.device": "Device",
"lsm.loss_weights": "Loss Weights"
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat(liquid): i18n keys for solver settings and monitoring"
cd ..
```

---

### Task 8: Submodule update + final verification

**Files:** None (verification + submodule commit)

- [ ] **Step 1: Update submodule reference in parent repo**

```bash
git add STLGeneratorForOpenFoam
git commit -m "fix: update submodule — solver UI, SSE streaming, convergence monitor"
```

- [ ] **Step 2: Run backend tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -q --tb=short`
Expected: All 148 tests PASS

- [ ] **Step 3: Verify all commits**

Run: `git log --oneline -10`
Expected: Shows Task 1-8 commits in order
