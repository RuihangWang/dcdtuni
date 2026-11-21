# Liquid Cooling Monitoring & Post-Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the solver tab to show liquid-specific monitoring controls (CP-CU + CDU), and add post-processing click-to-popup with time-series charts for liquid cooling results.

**Architecture:** Minimal modification approach — add `isLiquidMode()` branches in `_applyLeftTabUI()` for solver and post-processing tabs, extend `liquid_solve_monitor.js` with CDU handling and a post-processing entry point, and enrich the backend SSE callback with CDU-level metrics.

**Tech Stack:** Vanilla JS (no framework), HTML5 Canvas 2D charts, Python (runner.py + server.py), SSE streaming, REST JSON

---

## File Structure

| File | Role | Changes |
|------|------|---------|
| `src/backend/liquidcooling/runner.py` | Backend solver runner | Add `_extract_cdu_monitor()`, integrate into SSE callback |
| `src/backend/tests/test_liquid/test_runner_cdu.py` | Unit test for CDU monitor | New file — tests CDU data extraction |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Main frontend | Fix `_applyLeftTabUI()` solver/postprocess branches; add CDU HTML; wire tree clicks |
| `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js` | Liquid monitor module | CDU SSE parsing; `lsmShowPostProcessWindow()`; post-process chart rendering |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | Chinese translations | Add liquid monitoring i18n keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | English translations | Add liquid monitoring i18n keys |

---

### Task 1: Backend — Add CDU monitor data extraction

**Files:**
- Modify: `src/backend/liquidcooling/runner.py:20-53` (after `_extract_cp_monitor`)
- Modify: `src/backend/liquidcooling/runner.py:114-125` (callback wrapping)
- Create: `src/backend/tests/test_liquid/test_runner_cdu.py`

- [ ] **Step 1: Write failing test for `_extract_cdu_monitor`**

Create `src/backend/tests/test_liquid/test_runner_cdu.py`:

```python
"""Unit test for CDU-level monitoring data extraction."""
import numpy as np
import pytest
import torch

from dcdtuni.liquid.runner import _extract_cdu_monitor


class FakeGraph:
    """Minimal graph stub with BC nodes and edge connectivity."""
    def __init__(self):
        self.N_nodes = 4
        self.N_edges = 3
        self.bc_mask = torch.tensor([True, False, False, True])  # nodes 0,3 are BCs
        self.bc_P = torch.tensor([200000.0, 0.0, 0.0, 100000.0])
        self.bc_T = torch.tensor([288.15, 0.0, 0.0, 0.0])  # supply BC at node 0
        self.bc_T_mask = torch.tensor([True, False, False, False])
        self.node_ids = ["supply_bc", "junc_1", "junc_2", "return_bc"]
        self.edge_from = torch.tensor([0, 1, 2])
        self.edge_to = torch.tensor([1, 2, 3])
        self.node_in_edges = [[], [0], [1], [2]]
        self.node_out_edges = [[0], [1], [2], []]
        self.fluid_cp = 4182.0


class FakeState:
    """Minimal model state stub."""
    def __init__(self):
        self.state = {
            "m": torch.tensor([[0.5], [0.5], [0.5]]),  # [N_edges, N_t=1]
            "P": torch.tensor([[200000.0], [180000.0], [120000.0], [100000.0]]),  # [N_nodes, N_t=1]
        }


def test_extract_cdu_monitor_returns_dict():
    graph = FakeGraph()
    model = FakeState()
    result = _extract_cdu_monitor(model, graph)
    assert isinstance(result, dict)
    assert "supply_P_Pa" in result
    assert "return_P_Pa" in result
    assert "total_flow_kg_s" in result


def test_extract_cdu_monitor_values():
    graph = FakeGraph()
    model = FakeState()
    result = _extract_cdu_monitor(model, graph)
    # Supply pressure from BC node 0
    assert abs(result["supply_P_Pa"] - 200000.0) < 1.0
    # Return pressure from BC node 3
    assert abs(result["return_P_Pa"] - 100000.0) < 1.0
    # Total flow = sum of absolute flows at supply BC outgoing edges
    assert result["total_flow_kg_s"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_runner_cdu.py -v
```

Expected: FAIL — `_extract_cdu_monitor` not defined.

- [ ] **Step 3: Implement `_extract_cdu_monitor` in runner.py**

Add after `_extract_cp_monitor` (after line 53) in `src/backend/liquidcooling/runner.py`:

```python
def _extract_cdu_monitor(model, graph):
    """Extract CDU-level summary: supply/return pressure, total flow."""
    result = {}
    with torch.no_grad():
        P = model.state["P"]         # [N_nodes, N_t]
        m = model.state["m"]         # [N_edges, N_t]

        bc_indices = graph.bc_mask.nonzero(as_tuple=True)[0]
        if len(bc_indices) < 2:
            return result

        # Identify supply BC (has outgoing edges) and return BC (has incoming edges)
        supply_idx = None
        return_idx = None
        for idx in bc_indices.tolist():
            if len(graph.node_out_edges[idx]) > 0:
                supply_idx = idx
            if len(graph.node_in_edges[idx]) > 0:
                return_idx = idx

        if supply_idx is not None:
            result["supply_P_Pa"] = round(P[supply_idx, -1].item(), 1)
            if graph.bc_T_mask[supply_idx]:
                result["supply_T_K"] = round(graph.bc_T[supply_idx].item(), 2)
        if return_idx is not None:
            result["return_P_Pa"] = round(P[return_idx, -1].item(), 1)

        # Total flow: sum of absolute flows on supply BC's outgoing edges
        if supply_idx is not None and graph.node_out_edges[supply_idx]:
            out_edges = torch.tensor(graph.node_out_edges[supply_idx])
            total_flow = m[out_edges, -1].abs().sum().item()
            result["total_flow_kg_s"] = round(total_flow, 4)
        else:
            result["total_flow_kg_s"] = 0.0

    return result
```

- [ ] **Step 4: Run test to verify it passes**

```bash
conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/test_runner_cdu.py -v
```

Expected: PASS (all 2 tests).

- [ ] **Step 5: Integrate CDU monitor into SSE callback**

In `src/backend/liquidcooling/runner.py`, modify the callback wrapping block (around line 114-125). Replace:

```python
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
```

With:

```python
    if callback:
        _orig_cb = callback
        def _monitor_callback(log_dict):
            if log_dict.get("epoch") is not None:
                if monitor_cp_ids:
                    log_dict["cp_monitor"] = _extract_cp_monitor(
                        model, graph, monitor_cp_ids
                    )
                log_dict["cdu_monitor"] = _extract_cdu_monitor(model, graph)
            _orig_cb(log_dict)
        solve_callback = _monitor_callback
    else:
        solve_callback = callback
```

Key change: CDU monitor runs on every epoch regardless of whether CP monitoring is requested, and the callback wrapping triggers whenever `callback` is provided (not only when `monitor_cp_ids` is set).

- [ ] **Step 6: Run full liquid test suite**

```bash
conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -v
```

Expected: All existing tests PASS plus the 2 new CDU tests.

- [ ] **Step 7: Commit**

```bash
git add src/backend/liquidcooling/runner.py src/backend/tests/test_liquid/test_runner_cdu.py
git commit -m "feat(liquid): add CDU-level monitoring to SSE callback

Extract supply/return pressure and total flow from BC nodes during
ODIL solver epochs. CDU data is now included in every SSE event
alongside per-CP monitoring.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Frontend — Fix solver tab to show liquid panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:21143-21159` (`_applyLeftTabUI` solver branch)

- [ ] **Step 1: Add liquid mode branch to solver tab UI logic**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find the solver branch in `_applyLeftTabUI()` (around line 21143). The current code is:

```javascript
} else if (isSolver) {
    if (rpTabs) rpTabs.style.display = 'none';
    if (propTitle) propTitle.style.display = 'none';
    if (propContent) propContent.style.display = 'none';
    if (ppRight) ppRight.style.display = 'none';
    if (mpRight) mpRight.style.display = 'none';
    if (srsTabs) srsTabs.style.display = '';
    var srsActive = window._srsActiveSubTab || 'monitor';
    if (srsActive === 'sensor') {
        if (smRight) smRight.style.display = 'none';
        if (senRight) senRight.style.display = 'flex';
    } else {
        if (smRight) smRight.style.display = 'flex';
        if (senRight) senRight.style.display = 'none';
    }
    window.smLoadServerPatches();
    if (typeof window._updateSensorListUI === 'function') window._updateSensorListUI();
}
```

Replace it with:

```javascript
} else if (isSolver) {
    if (rpTabs) rpTabs.style.display = 'none';
    if (propTitle) propTitle.style.display = 'none';
    if (propContent) propContent.style.display = 'none';
    if (ppRight) ppRight.style.display = 'none';
    if (mpRight) mpRight.style.display = 'none';

    var liqRight = document.getElementById('liquid-solver-monitoring-right');

    if (typeof isLiquidMode === 'function' && isLiquidMode()) {
        // Liquid mode: show liquid monitoring panel, hide air-cooling panels
        if (srsTabs) srsTabs.style.display = 'none';
        if (smRight) smRight.style.display = 'none';
        if (senRight) senRight.style.display = 'none';
        if (liqRight) liqRight.style.display = 'flex';
        window._lsmPopulateCPListFromState();
    } else {
        // Air mode: show air-cooling sub-tabs and panels
        if (liqRight) liqRight.style.display = 'none';
        if (srsTabs) srsTabs.style.display = '';
        var srsActive = window._srsActiveSubTab || 'monitor';
        if (srsActive === 'sensor') {
            if (smRight) smRight.style.display = 'none';
            if (senRight) senRight.style.display = 'flex';
        } else {
            if (smRight) smRight.style.display = 'flex';
            if (senRight) senRight.style.display = 'none';
        }
        window.smLoadServerPatches();
        if (typeof window._updateSensorListUI === 'function') window._updateSensorListUI();
    }
}
```

- [ ] **Step 2: Add `_lsmPopulateCPListFromState` function to liquid_solve_monitor.js**

In `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`, add after `lsmGetMonitoringConfig` (after line 79):

```javascript
    window._lsmPopulateCPListFromState = function() {
        // Build a graph-like structure from the frontend state for lsmLoadCPList
        if (typeof state === 'undefined' || !state.models) return;
        var racks = [];
        state.models.forEach(function(m) {
            if (m.kind !== 'rack') return;
            var rackEntry = { id: m.name || m.id, servers: [] };
            (m.equipment || []).forEach(function(eq) {
                if (!eq.liquidCooling || !eq.liquidCooling.cpCuPairs || eq.liquidCooling.cpCuPairs.length === 0) return;
                var srvEntry = { id: eq.name || eq.id, cp_cu_pairs: [] };
                eq.liquidCooling.cpCuPairs.forEach(function(cp) {
                    srvEntry.cp_cu_pairs.push({
                        id: cp.name || ('CP-' + srvEntry.cp_cu_pairs.length),
                        edge_id: cp.edge_id || (rackEntry.id + '/' + srvEntry.id + '/' + (cp.name || ('CP-' + srvEntry.cp_cu_pairs.length))),
                        Q_W: cp.heatLoad || cp.Q_W || 0
                    });
                });
                rackEntry.servers.push(srvEntry);
            });
            if (rackEntry.servers.length > 0) racks.push(rackEntry);
        });
        window.lsmLoadCPList({ racks: racks });
    };
```

- [ ] **Step 3: Verify by starting server and using browser-use**

```bash
# Start server (if not already running)
fuser -k 8005/tcp 2>/dev/null; sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam && nohup conda run -n dcdtuni python server/server.py --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case > /tmp/stlgen_server.log 2>&1 &
sleep 3

# Open browser and load Liquid project
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
sleep 2
browser-use eval "window.loadProjectFromPath && window.loadProjectFromPath('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Liquid.cfdproj')"
sleep 2

# Verify liquid mode is active
browser-use eval "typeof isLiquidMode === 'function' && isLiquidMode()"
# Expected: true

# Switch to solver tab
browser-use eval "window.switchLeftTab('solver')"
sleep 1
browser-use screenshot /tmp/solver_tab_liquid.png
```

Verify screenshot shows:
- Right sidebar displays "🌊 液冷求解监控" (NOT "🔬 求解监控")
- No "传感器位点" sub-tab visible
- CP-CU list area visible

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js
git commit -m "fix(liquid): solver tab shows liquid monitoring panel in liquid mode

Add isLiquidMode() branch to _applyLeftTabUI() solver case. When in
liquid mode, hide air-cooling sub-tabs/panels and show
#liquid-solver-monitoring-right instead. CP-CU list populates from
frontend state.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Frontend — Add CDU monitoring section to liquid panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:1023-1042` (liquid monitoring panel HTML)
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js` (CDU SSE handling)
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Expand liquid monitoring panel HTML with CDU section**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find the closing `</div>` of `#liquid-solver-monitoring-right` (around line 1042). Before that closing tag, add the CDU monitoring section:

After the existing `lsm-cp-list` div block (after `</div>` of the CP section around line 1040), add:

```html
  <div style="padding:8px; border-top:1px solid #333;">
    <div style="margin-bottom:4px;">
      <label style="font-size:11px; color:#aaa;" data-i18n="lsm.cdu_monitor">🏭 CDU 监控</label>
    </div>
    <div style="display:flex; flex-direction:column; gap:2px;">
      <label style="font-size:10px; color:#ccc;">
        <input type="checkbox" class="lsm-cdu-cb" data-metric="return_P" checked>
        <span data-i18n="lsm.cdu_return_pressure">回水压力 (Return Pressure)</span>
      </label>
      <label style="font-size:10px; color:#ccc;">
        <input type="checkbox" class="lsm-cdu-cb" data-metric="supply_P" checked>
        <span data-i18n="lsm.cdu_supply_pressure">供水压力 (Supply Pressure)</span>
      </label>
      <label style="font-size:10px; color:#ccc;">
        <input type="checkbox" class="lsm-cdu-cb" data-metric="total_flow" checked>
        <span data-i18n="lsm.cdu_total_flow">总流量 (Total Flow)</span>
      </label>
    </div>
  </div>
```

- [ ] **Step 2: Add i18n keys**

Add to `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`:

```json
"lsm.cdu_monitor": "🏭 CDU 监控",
"lsm.cdu_return_pressure": "回水压力",
"lsm.cdu_supply_pressure": "供水压力",
"lsm.cdu_total_flow": "总流量"
```

Add to `STLGeneratorForOpenFoam/web/i18n/en-US.json`:

```json
"lsm.cdu_monitor": "🏭 CDU Monitor",
"lsm.cdu_return_pressure": "Return Pressure",
"lsm.cdu_supply_pressure": "Supply Pressure",
"lsm.cdu_total_flow": "Total Flow"
```

- [ ] **Step 3: Handle CDU SSE data in `_lsmOnSSE`**

In `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`, find `_lsmOnSSE` (line 96). After the `cpMon` block (after line 143, before the final `_lsmDrawChart()` call at line 145), add CDU data handling:

```javascript
        var cduMon = payload.cdu_monitor;
        if (cduMon) {
            if (cduMon.supply_P_Pa != null) {
                var spKey = 'CDU supply_P';
                if (!mon.flows[spKey]) mon.flows[spKey] = [];
                mon.flows[spKey].push({ epoch: epoch, value: cduMon.supply_P_Pa });
            }
            if (cduMon.return_P_Pa != null) {
                var rpKey = 'CDU return_P';
                if (!mon.flows[rpKey]) mon.flows[rpKey] = [];
                mon.flows[rpKey].push({ epoch: epoch, value: cduMon.return_P_Pa });
            }
            if (cduMon.total_flow_kg_s != null) {
                var tfKey = 'CDU total_flow';
                if (!mon.flows[tfKey]) mon.flows[tfKey] = [];
                mon.flows[tfKey].push({ epoch: epoch, value: cduMon.total_flow_kg_s });
            }
            if (cduMon.supply_T_K != null) {
                var stKey = 'CDU supply_T';
                if (!mon.temperatures[stKey]) mon.temperatures[stKey] = [];
                mon.temperatures[stKey].push({ epoch: epoch, value: cduMon.supply_T_K });
            }
        }
```

- [ ] **Step 4: Add CDU metrics to monitoring config**

In `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`, modify `lsmGetMonitoringConfig` (line 65-79) to include CDU settings:

Replace:

```javascript
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
```

With:

```javascript
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
        var cduCbs = document.querySelectorAll('.lsm-cdu-cb:checked');
        var cduMetrics = [];
        for (var j = 0; j < cduCbs.length; j++) {
            cduMetrics.push(cduCbs[j].getAttribute('data-metric'));
        }
        return {
            loss: lossCb ? lossCb.checked : true,
            cpList: cpList,
            cduMetrics: cduMetrics
        };
    };
```

- [ ] **Step 5: Visual verification**

```bash
export PATH="$HOME/.local/bin:$PATH"
# Restart server to pick up changes
fuser -k 8005/tcp 2>/dev/null; sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam && nohup conda run -n dcdtuni python server/server.py --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case > /tmp/stlgen_server.log 2>&1 &
sleep 3

browser-use open http://localhost:8005/STL_generate.html
sleep 2
browser-use eval "window.loadProjectFromPath && window.loadProjectFromPath('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Liquid.cfdproj')"
sleep 2
browser-use eval "window.switchLeftTab('solver')"
sleep 1
browser-use screenshot /tmp/solver_tab_cdu.png
```

Verify screenshot shows CDU section below CP-CU list with three checkboxes.

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat(liquid): add CDU monitoring section to liquid solver panel

Add CDU checkboxes (return pressure, supply pressure, total flow) to
the liquid monitoring right panel. Handle cdu_monitor SSE events and
plot CDU metrics in the floating chart window.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — Fix post-processing tab to restore model tree

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:21132-21142` (`_applyLeftTabUI` postprocess branch)

- [ ] **Step 1: Add liquid mode branch to post-processing UI logic**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find the postprocess branch in `_applyLeftTabUI()` (around line 21132). The current code is:

```javascript
if (isPostprocess) {
    if (rpTabs) rpTabs.style.display = 'none';
    if (srsTabs) srsTabs.style.display = 'none';
    if (propTitle) propTitle.style.display = 'none';
    if (propContent) propContent.style.display = 'none';
    if (ppRight && window._ppState && window._ppState.initialized) {
        ppRight.style.display = 'flex';
    }
    if (smRight) smRight.style.display = 'none';
    if (senRight) senRight.style.display = 'none';
    if (mpRight) mpRight.style.display = 'none';
}
```

Replace with:

```javascript
if (isPostprocess) {
    if (rpTabs) rpTabs.style.display = 'none';
    if (srsTabs) srsTabs.style.display = 'none';
    if (propTitle) propTitle.style.display = 'none';
    if (propContent) propContent.style.display = 'none';
    if (smRight) smRight.style.display = 'none';
    if (senRight) senRight.style.display = 'none';
    if (mpRight) mpRight.style.display = 'none';

    var liqRight = document.getElementById('liquid-solver-monitoring-right');
    if (liqRight) liqRight.style.display = 'none';

    if (typeof isLiquidMode === 'function' && isLiquidMode()) {
        // Liquid post-processing: show model tree, hide air post-process panels
        if (ppRight) ppRight.style.display = 'none';
        // Show model tree
        if (treeContainer) treeContainer.style.display = '';
        // Show hint in right panel
        window._lsmShowPostProcessHint();
    } else {
        // Air post-processing: existing behavior
        if (ppRight && window._ppState && window._ppState.initialized) {
            ppRight.style.display = 'flex';
        }
    }
}
```

- [ ] **Step 2: Also fix the tree container visibility line**

In the same `_applyLeftTabUI()` function, find the line that controls tree visibility (around line 21105):

```javascript
if (treeContainer) treeContainer.style.display = isGeo ? '' : 'none';
```

Replace with:

```javascript
var showTree = isGeo || (isPostprocess && typeof isLiquidMode === 'function' && isLiquidMode());
if (treeContainer) treeContainer.style.display = showTree ? '' : 'none';
```

- [ ] **Step 3: Add post-process hint function and right panel placeholder**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find the `#liquid-solver-monitoring-right` closing div (around line 1042). After it, add a new panel:

```html
<div id="liquid-postprocess-right" style="display:none; flex-direction:column; overflow-y:auto; flex:1; padding:0;">
  <div class="header" style="color:#00b4dc;" data-i18n="lsm.pp_header">📊 液冷后处理</div>
  <div id="lsm-pp-hint" style="padding:16px; color:#888; font-size:12px; text-align:center;">
    <div style="font-size:24px; margin-bottom:8px;">📈</div>
    <div data-i18n="lsm.pp_hint">点击模型树中的 CP-CU 或 CDU 查看结果曲线</div>
  </div>
  <div id="lsm-pp-status" style="padding:8px; font-size:10px; color:#666; display:none;"></div>
</div>
```

In `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`, add the hint function (after `_lsmPopulateCPListFromState`):

```javascript
    window._lsmShowPostProcessHint = function() {
        var ppRight = document.getElementById('liquid-postprocess-right');
        if (ppRight) ppRight.style.display = 'flex';
    };
```

Then update `_applyLeftTabUI` postprocess branch to also hide `liquid-postprocess-right` when not in liquid postprocess mode. In the common hide section at the top of `isPostprocess`, add:

```javascript
var liqPPRight = document.getElementById('liquid-postprocess-right');
```

And in the non-liquid branch:
```javascript
if (liqPPRight) liqPPRight.style.display = 'none';
```

And ensure it's hidden in all other tab cases too (add to the `else` block where non-postprocess tabs hide things).

- [ ] **Step 4: Add i18n keys**

Add to `zh-CN.json`:

```json
"lsm.pp_header": "📊 液冷后处理",
"lsm.pp_hint": "点击模型树中的 CP-CU 或 CDU 查看结果曲线"
```

Add to `en-US.json`:

```json
"lsm.pp_header": "📊 Liquid Post-Processing",
"lsm.pp_hint": "Click a CP-CU pair or CDU in the model tree to view result curves"
```

- [ ] **Step 5: Visual verification**

```bash
export PATH="$HOME/.local/bin:$PATH"
fuser -k 8005/tcp 2>/dev/null; sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam && nohup conda run -n dcdtuni python server/server.py --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case > /tmp/stlgen_server.log 2>&1 &
sleep 3

browser-use open http://localhost:8005/STL_generate.html
sleep 2
browser-use eval "window.loadProjectFromPath && window.loadProjectFromPath('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Liquid.cfdproj')"
sleep 2
browser-use eval "window.switchLeftTab('postprocess')"
sleep 1
browser-use screenshot /tmp/postprocess_tab_liquid.png
```

Verify screenshot shows:
- Left sidebar: model tree visible with rack/server/equipment nodes
- Right sidebar: "📊 液冷后处理" header + hint text

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat(liquid): restore model tree in post-processing tab for liquid mode

In liquid mode, the post-processing tab now shows the model tree in the
left sidebar (instead of hiding it) and displays a hint panel in the
right sidebar prompting users to click CP-CU or CDU nodes.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Frontend — Implement post-processing floating window

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js` (add `lsmShowPostProcessWindow`)

- [ ] **Step 1: Add result caching and data fetching**

In `STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js`, add at the top of the IIFE (after line 13, before the `_lsmLossColors` block):

```javascript
    var _lsmCachedResult = null;
    var _lsmCachedResultTime = 0;
    var _LSM_CACHE_TTL = 30000; // 30 seconds

    function _lsmFetchResults(callback) {
        var now = Date.now();
        if (_lsmCachedResult && (now - _lsmCachedResultTime) < _LSM_CACHE_TTL) {
            callback(null, _lsmCachedResult);
            return;
        }
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '/api/liquid/results', true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        // Include session cookie
        xhr.withCredentials = true;
        xhr.onload = function() {
            if (xhr.status === 200) {
                try {
                    var data = JSON.parse(xhr.responseText);
                    _lsmCachedResult = data;
                    _lsmCachedResultTime = Date.now();
                    callback(null, data);
                } catch (e) {
                    callback('JSON parse error: ' + e.message);
                }
            } else {
                callback('HTTP ' + xhr.status + ': ' + xhr.responseText);
            }
        };
        xhr.onerror = function() { callback('Network error'); };
        xhr.send();
    }
```

- [ ] **Step 2: Add `lsmShowPostProcessWindow` entry function**

After the fetch function, add:

```javascript
    window.lsmShowPostProcessWindow = function(objectType, objectId, objectLabel) {
        // objectType: 'cp' | 'cdu'
        // objectId: edge_id string for CP, or 'cdu' for CDU
        // objectLabel: display name for window title

        var statusEl = document.getElementById('lsm-pp-status');
        if (statusEl) {
            statusEl.style.display = '';
            statusEl.textContent = 'Loading results...';
        }

        _lsmFetchResults(function(err, data) {
            if (statusEl) statusEl.style.display = 'none';
            if (err) {
                alert('Failed to load results: ' + err);
                return;
            }
            _lsmShowPPWindow(objectType, objectId, objectLabel, data);
        });
    };

    function _lsmShowPPWindow(objectType, objectId, label, result) {
        // Build datasets for the floating window
        var time_s = result.time_s || [];
        var datasets = { temperature: {}, flow: {}, pressure: {} };

        if (objectType === 'cp') {
            // Find CP index in edge_ids
            var edgeIdx = (result.edge_ids || []).indexOf(objectId);
            var cpIdx = -1;
            // CP indices map: chip_temp_K and water_outlet_temp_K are indexed by CP order
            // We need to find which CP index corresponds to this edge
            // The order matches the order of cold_plate edges in edge_ids
            var cpCount = 0;
            for (var i = 0; i < (result.edge_ids || []).length; i++) {
                // We don't have edge_type in result, so count CPs by checking chip_temp_K rows
                if (result.edge_ids[i] === objectId) {
                    cpIdx = cpCount;
                    break;
                }
                // Heuristic: CP edges have chip_temp data; count them
                // Better approach: match by iterating rack_summaries
                cpCount++;
            }

            // Use rack_summaries to find CP index reliably
            cpIdx = -1;
            var cpEdgeOrder = [];
            (result.rack_summaries || []).forEach(function(rack) {
                (rack.servers || []).forEach(function(srv) {
                    (srv.cp_edge_ids || []).forEach(function(eid) {
                        cpEdgeOrder.push(eid);
                    });
                });
            });
            cpIdx = cpEdgeOrder.indexOf(objectId);

            if (cpIdx >= 0 && result.chip_temp_K && result.chip_temp_K[cpIdx]) {
                var chipTemps = result.chip_temp_K[cpIdx];
                var waterTemps = (result.water_outlet_temp_K || [])[cpIdx] || [];
                datasets.temperature['T_chip'] = _lsmBuildTimeSeries(time_s, chipTemps);
                if (waterTemps.length > 0) {
                    datasets.temperature['T_water_out'] = _lsmBuildTimeSeries(time_s, waterTemps);
                }
            }

            if (edgeIdx >= 0 && result.edge_flow_kg_s && result.edge_flow_kg_s[edgeIdx]) {
                datasets.flow['m'] = _lsmBuildTimeSeries(time_s, result.edge_flow_kg_s[edgeIdx]);
            }

            // Pressure: find connected nodes
            if (edgeIdx >= 0 && result.node_pressure_Pa) {
                // We need edge connectivity — not in result JSON directly
                // Use the compiled_graph if available, or derive from rack_summaries
                // For now, show overall system pressure from BC nodes
                _lsmAddBCPressure(datasets.pressure, result, time_s);
            }

        } else if (objectType === 'cdu') {
            // CDU: aggregate from rack_summaries + BC nodes
            (result.rack_summaries || []).forEach(function(rack) {
                if (rack.supply_temp_K) {
                    datasets.temperature['Supply T (' + rack.rack_id + ')'] = _lsmBuildTimeSeries(time_s, rack.supply_temp_K);
                }
                if (rack.return_temp_K) {
                    datasets.temperature['Return T (' + rack.rack_id + ')'] = _lsmBuildTimeSeries(time_s, rack.return_temp_K);
                }
                if (rack.total_flow_kg_s) {
                    datasets.flow['Flow (' + rack.rack_id + ')'] = _lsmBuildTimeSeries(time_s, rack.total_flow_kg_s);
                }
            });
            _lsmAddBCPressure(datasets.pressure, result, time_s);
        }

        // Create the floating window with post-process data
        _lsmCreatePostProcessWindow(label || objectId, datasets);
    }

    function _lsmBuildTimeSeries(time_s, values) {
        var arr = [];
        var len = Math.min(time_s.length, values.length);
        for (var i = 0; i < len; i++) {
            arr.push({ epoch: time_s[i], value: values[i] });
        }
        return arr;
    }

    function _lsmAddBCPressure(pressureDatasets, result, time_s) {
        if (!result.node_pressure_Pa || !result.node_ids) return;
        for (var i = 0; i < result.node_ids.length; i++) {
            var nid = result.node_ids[i];
            // BC nodes typically have "_bc" in their ID
            if (nid.indexOf('supply') >= 0 || nid.indexOf('return') >= 0 ||
                nid.indexOf('_bc') >= 0 || nid.indexOf('BC') >= 0) {
                var pArr = result.node_pressure_Pa[i];
                if (pArr) {
                    pressureDatasets[nid] = _lsmBuildTimeSeries(time_s, pArr);
                }
            }
        }
    }
```

- [ ] **Step 3: Create the post-processing floating window**

Still in `liquid_solve_monitor.js`, add the window creation function:

```javascript
    var _lsmPPActiveTab = 'temperature';

    function _lsmCreatePostProcessWindow(title, datasets) {
        _lsmCleanupListeners();
        var existing = document.getElementById('lsm-monitor-window');
        if (existing) existing.remove();

        // Store post-process datasets globally for chart drawing
        window._lsmPPDatasets = datasets;
        _lsmPPActiveTab = 'temperature';

        var win = document.createElement('div');
        win.id = 'lsm-monitor-window';
        win.style.cssText = 'position:fixed; z-index:900; top:60px; right:20px; width:640px; height:400px; ' +
            'display:flex; flex-direction:column; ' +
            'background:#1a1a2e; border:1px solid #555; border-radius:6px; ' +
            'box-shadow:0 4px 20px rgba(0,0,0,0.6);';

        // Header
        var header = document.createElement('div');
        header.style.cssText = 'display:flex; align-items:center; justify-content:space-between; ' +
            'padding:4px 8px; background:#252542; border-bottom:1px solid #444; border-radius:6px 6px 0 0; cursor:move; flex-shrink:0;';
        header.innerHTML = '<span style="font-size:11px; color:#00b4dc; font-weight:600;">📊 ' + title + '</span>' +
            '<div style="display:flex; gap:4px;">' +
            '<button style="font-size:10px; background:#1a1a2e; border:1px solid #444; color:#aaa; cursor:pointer; padding:1px 4px; border-radius:2px;" id="lsm-pp-font-down">A−</button>' +
            '<button style="font-size:10px; background:#1a1a2e; border:1px solid #444; color:#aaa; cursor:pointer; padding:1px 4px; border-radius:2px;" id="lsm-pp-font-up">A+</button>' +
            '<button style="font-size:12px; background:#1a1a2e; border:1px solid #444; color:#e06c75; cursor:pointer; padding:1px 6px; border-radius:2px;" id="lsm-pp-close">×</button>' +
            '</div>';

        // Drag behavior
        var dragging = false, dx = 0, dy = 0;
        header.addEventListener('mousedown', function(e) {
            if (e.target.tagName === 'BUTTON') return;
            dragging = true; dx = e.clientX - win.offsetLeft; dy = e.clientY - win.offsetTop;
        });
        _lsmDragMoveHandler = function(e) {
            if (!dragging) return;
            win.style.left = (e.clientX - dx) + 'px';
            win.style.top = (e.clientY - dy) + 'px';
            win.style.right = 'auto';
        };
        _lsmDragUpHandler = function() { dragging = false; };
        document.addEventListener('mousemove', _lsmDragMoveHandler);
        document.addEventListener('mouseup', _lsmDragUpHandler);

        // Tab bar
        var tabBar = document.createElement('div');
        tabBar.style.cssText = 'display:flex; border-bottom:1px solid #333; flex-shrink:0;';
        var ppTabs = [
            { id: 'temperature', label: '🌡 温度' },
            { id: 'flow', label: '💧 流量' },
            { id: 'pressure', label: '📏 水压' }
        ];
        ppTabs.forEach(function(tab) {
            var btn = document.createElement('button');
            btn.id = 'lsm-pp-tab-' + tab.id;
            btn.textContent = tab.label;
            btn.style.cssText = 'flex:1; padding:4px 0; font-size:11px; border:none; cursor:pointer; ' +
                (tab.id === 'temperature' ? 'background:#1a1a2e; color:#00b4dc; border-bottom:2px solid #00b4dc;' :
                                            'background:#111; color:#666; border-bottom:2px solid transparent;');
            btn.addEventListener('click', function() {
                _lsmPPActiveTab = tab.id;
                ppTabs.forEach(function(t2) {
                    var b = document.getElementById('lsm-pp-tab-' + t2.id);
                    if (b) {
                        b.style.background = t2.id === tab.id ? '#1a1a2e' : '#111';
                        b.style.color = t2.id === tab.id ? '#00b4dc' : '#666';
                        b.style.borderBottom = t2.id === tab.id ? '2px solid #00b4dc' : '2px solid transparent';
                    }
                });
                _lsmDrawPPChart();
            });
            tabBar.appendChild(btn);
        });

        // Body with canvas + legend
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

        // Wire buttons
        document.getElementById('lsm-pp-font-down').addEventListener('click', function() {
            window._lsmFontScale = Math.max(0.6, window._lsmFontScale - 0.1); _lsmDrawPPChart();
        });
        document.getElementById('lsm-pp-font-up').addEventListener('click', function() {
            window._lsmFontScale = Math.min(2, window._lsmFontScale + 0.1); _lsmDrawPPChart();
        });
        document.getElementById('lsm-pp-close').addEventListener('click', function() {
            _lsmCleanupListeners();
            var w = document.getElementById('lsm-monitor-window');
            if (w) w.remove();
        });

        // ResizeObserver
        _lsmResizeObserver = new ResizeObserver(function() { _lsmResizePPCanvas(); });
        _lsmResizeObserver.observe(canvasWrap);
        setTimeout(_lsmResizePPCanvas, 50);
    }

    function _lsmResizePPCanvas() {
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
        _lsmDrawPPChart();
    }

    function _lsmDrawPPChart() {
        var canvas = document.getElementById('lsm-chart-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var W = canvas.width, H = canvas.height;
        var dpr = window.devicePixelRatio || 1;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var lW = W / dpr, lH = H / dpr;
        var fs = window._lsmFontScale || 1.0;

        var ppData = window._lsmPPDatasets;
        if (!ppData) return;

        var datasets = ppData[_lsmPPActiveTab] || {};
        var yLabel = _lsmPPActiveTab === 'temperature' ? 'K' :
                     _lsmPPActiveTab === 'flow' ? 'kg/s' : 'Pa';

        var seriesNames = Object.keys(datasets);

        ctx.fillStyle = '#0d0d1a';
        ctx.fillRect(0, 0, lW, lH);

        if (seriesNames.length === 0) {
            ctx.fillStyle = '#666';
            ctx.font = Math.round(12 * fs) + 'px monospace';
            ctx.fillText('No data for this tab', lW / 2 - 70, lH / 2);
            _lsmUpdatePPLegend(seriesNames);
            return;
        }

        var labelFs = Math.round(9 * fs);
        var ml = Math.max(65, 60 * fs + 5), mr = 10, mt = 10, mb = Math.max(25, 20 * fs + 5);
        var cw = lW - ml - mr, ch = lH - mt - mb;
        if (cw < 20 || ch < 20) return;

        // Compute ranges
        var xMin = Infinity, xMax = -Infinity;
        var yMin = Infinity, yMax = -Infinity;
        for (var k = 0; k < seriesNames.length; k++) {
            if (window._lsmHiddenSeries[seriesNames[k]]) continue;
            var arr = datasets[seriesNames[k]];
            for (var j = 0; j < arr.length; j++) {
                if (arr[j].epoch < xMin) xMin = arr[j].epoch;
                if (arr[j].epoch > xMax) xMax = arr[j].epoch;
                if (arr[j].value < yMin) yMin = arr[j].value;
                if (arr[j].value > yMax) yMax = arr[j].value;
            }
        }
        if (xMin === Infinity) { xMin = 0; xMax = 1; }
        if (xMax <= xMin) xMax = xMin + 1;
        if (yMin === Infinity) { yMin = 0; yMax = 1; }
        var yPad = Math.max((yMax - yMin) * 0.1, 0.5);
        yMin -= yPad; yMax += yPad;

        // Grid
        ctx.strokeStyle = '#222'; ctx.lineWidth = 0.5;
        ctx.font = labelFs + 'px monospace'; ctx.fillStyle = '#555';

        var ySteps = 5;
        for (var i = 0; i <= ySteps; i++) {
            var yVal = yMin + i * (yMax - yMin) / ySteps;
            var py = mt + ch - (yVal - yMin) / (yMax - yMin) * ch;
            ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + cw, py); ctx.stroke();
            var label = _lsmFormatValue(yVal, _lsmPPActiveTab);
            ctx.fillText(label, 4, py + 3);
        }

        var xSteps = 5;
        for (var i = 0; i <= xSteps; i++) {
            var xVal = xMin + i * (xMax - xMin) / xSteps;
            var px = ml + (xVal - xMin) / (xMax - xMin) * cw;
            ctx.beginPath(); ctx.moveTo(px, mt); ctx.lineTo(px, mt + ch); ctx.stroke();
            ctx.fillText(xVal.toFixed(1) + 's', px - 12, mt + ch + 14);
        }

        // Axis labels
        ctx.save();
        ctx.translate(12, mt + ch / 2); ctx.rotate(-Math.PI / 2);
        ctx.fillStyle = '#888'; ctx.fillText(yLabel, -15, 0);
        ctx.restore();
        ctx.fillStyle = '#888'; ctx.fillText('Time (s)', ml + cw / 2 - 20, lH - 2);

        // Border
        ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
        ctx.strokeRect(ml, mt, cw, ch);

        // Draw series
        for (var k = 0; k < seriesNames.length; k++) {
            var name = seriesNames[k];
            if (window._lsmHiddenSeries[name]) continue;
            var arr = datasets[name];
            if (arr.length < 2) continue;
            ctx.strokeStyle = _lsmColors[k % _lsmColors.length];
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            for (var j = 0; j < arr.length; j++) {
                var px = ml + (arr[j].epoch - xMin) / (xMax - xMin) * cw;
                var py = mt + ch - (arr[j].value - yMin) / (yMax - yMin) * ch;
                if (j === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
            }
            ctx.stroke();
        }

        _lsmUpdatePPLegend(seriesNames);
    }

    function _lsmFormatValue(val, tab) {
        if (tab === 'pressure') return (val / 1000).toFixed(1) + 'kPa';
        if (tab === 'flow') return val.toFixed(4);
        return val.toFixed(1);
    }

    function _lsmUpdatePPLegend(seriesNames) {
        var legend = document.getElementById('lsm-chart-legend');
        if (!legend) return;
        var fs = window._lsmFontScale || 1.0;
        legend.style.fontSize = Math.round(10 * fs) + 'px';
        legend.innerHTML = '';

        if (seriesNames.length === 0) {
            var empty = document.createElement('div');
            empty.style.cssText = 'color:#666; line-height:1.4;';
            empty.textContent = 'No data';
            legend.appendChild(empty);
            return;
        }

        for (var i = 0; i < seriesNames.length; i++) {
            var name = seriesNames[i];
            var color = _lsmColors[i % _lsmColors.length];
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
                _lsmDrawPPChart();
            });
            legend.appendChild(item);
        }
    }
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/js/liquid_solve_monitor.js
git commit -m "feat(liquid): add post-processing floating window with time-series charts

Implement lsmShowPostProcessWindow() that fetches /api/liquid/results,
extracts temperature/flow/pressure data for the selected CP or CDU,
and renders interactive Canvas 2D charts in a draggable floating window
with Temperature/Flow/Pressure tabs.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Frontend — Wire model tree clicks to post-processing popup

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (model tree click handler)

- [ ] **Step 1: Add liquid post-processing click handler for model tree**

In `STLGeneratorForOpenFoam/web/STL_generate.html`, find the `switchLeftTab` function (line 21042). After it, add a new function:

```javascript
window._lsmOnTreeClickPostProcess = function(modelId) {
    if (state.dctwinConfig.activeTab !== 'postprocess') return;
    if (typeof isLiquidMode !== 'function' || !isLiquidMode()) return;

    // Find the model
    var model = null;
    for (var i = 0; i < state.models.length; i++) {
        if (state.models[i].id === modelId) { model = state.models[i]; break; }
    }
    if (!model) return;

    // CDU click
    if (model.kind === 'cdu' || (model.name && model.name.toLowerCase().indexOf('cdu') >= 0)) {
        window.lsmShowPostProcessWindow('cdu', 'cdu', 'CDU');
        return;
    }

    // Rack click — check for equipment with CP-CU pairs
    if (model.kind === 'rack') {
        // Don't open popup for rack itself, only for specific equipment
        return;
    }

    // Equipment with liquid cooling CP-CU pairs (selected via equipment inspector)
    // Equipment is nested inside rack models as model.equipment[]
    // The tree click may target a rack, and selection state tracks the equipment
    // Check if current selection has cpCuPairs
    var sel = state.selection;
    if (sel && sel.equipmentIndex != null) {
        var rack = null;
        for (var i = 0; i < state.models.length; i++) {
            if (state.models[i].id === sel.modelId && state.models[i].kind === 'rack') {
                rack = state.models[i]; break;
            }
        }
        if (rack && rack.equipment && rack.equipment[sel.equipmentIndex]) {
            var eq = rack.equipment[sel.equipmentIndex];
            if (eq.liquidCooling && eq.liquidCooling.cpCuPairs && eq.liquidCooling.cpCuPairs.length > 0) {
                var firstCP = eq.liquidCooling.cpCuPairs[0];
                var edgeId = firstCP.edge_id || (rack.name + '/' + (eq.name || eq.id) + '/' + (firstCP.name || 'CP-0'));
                var label = (eq.name || eq.id) + ' / ' + (firstCP.name || 'CP-0');
                window.lsmShowPostProcessWindow('cp', edgeId, label);
                return;
            }
        }
    }
};
```

- [ ] **Step 2: Hook into existing tree click handler**

Find the tree container's click handler. The tree is at `id="tree-container"` (line 125). Find where tree node selection triggers model selection — likely in a function like `selectNode` or the tree container's `onclick`. 

In the existing `selectModel` or tree click handler, add a call at the end:

```javascript
if (typeof window._lsmOnTreeClickPostProcess === 'function') {
    window._lsmOnTreeClickPostProcess(modelId);
}
```

This needs to be placed where the model tree dispatches selection events. Find the tree node click handler and append this call.

- [ ] **Step 3: Visual verification — full end-to-end**

```bash
export PATH="$HOME/.local/bin:$PATH"
fuser -k 8005/tcp 2>/dev/null; sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam && nohup conda run -n dcdtuni python server/server.py --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case > /tmp/stlgen_server.log 2>&1 &
sleep 3

browser-use open http://localhost:8005/STL_generate.html
sleep 2
browser-use eval "window.loadProjectFromPath && window.loadProjectFromPath('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Liquid.cfdproj')"
sleep 2

# Switch to post-processing
browser-use eval "window.switchLeftTab('postprocess')"
sleep 1
browser-use screenshot /tmp/pp_tree.png
# Verify: model tree visible on left

# Click a rack node in the tree to select equipment
# (Exact interaction depends on tree structure — use browser-use state to find clickable elements)
browser-use state
# Find and click an equipment node that has CP-CU pairs
# browser-use click <N>  — the index of a server/equipment tree node
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(liquid): wire model tree clicks to post-processing popup

In liquid post-processing mode, clicking a CP-CU pair or CDU in the
model tree opens a floating window with time-series charts loaded from
/api/liquid/results.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Integration test — End-to-end verification

**Files:** None (testing only)

- [ ] **Step 1: Start fresh server and load project**

```bash
export PATH="$HOME/.local/bin:$PATH"
fuser -k 8005/tcp 2>/dev/null; sleep 1
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam && nohup conda run -n dcdtuni python server/server.py --case-dir /mnt/lv/litianyu/mycode/dcdtuni/case > /tmp/stlgen_server.log 2>&1 &
sleep 3

browser-use open http://localhost:8005/STL_generate.html
sleep 2
browser-use eval "window.loadProjectFromPath && window.loadProjectFromPath('/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/projects/Liquid.cfdproj')"
sleep 2
```

- [ ] **Step 2: Verify solver tab in liquid mode**

```bash
browser-use eval "window.switchLeftTab('solver')"
sleep 1
browser-use screenshot /tmp/e2e_solver.png
```

Checkpoint: Right sidebar shows "🌊 液冷求解监控" with CP-CU list and CDU section. No air-cooling panels visible.

- [ ] **Step 3: Verify air mode is unaffected**

```bash
browser-use eval "window.toggleDisplayMode()"
sleep 1
browser-use eval "window.switchLeftTab('solver')"
sleep 1
browser-use screenshot /tmp/e2e_solver_air.png
```

Checkpoint: Right sidebar shows air-cooling "🔬 求解监控" with sub-tabs. Toggle back to liquid mode after:

```bash
browser-use eval "window.toggleDisplayMode()"
sleep 1
```

- [ ] **Step 4: Run liquid solve and verify real-time monitoring**

```bash
# Export liquidrun first
browser-use eval "window.switchLeftTab('solver')"
sleep 1
# Trigger solve (if liquidrun exists)
browser-use eval "typeof window.runLiquidSolve === 'function'"
# If true, run solve:
browser-use eval "window.runLiquidSolve()"
sleep 10
browser-use screenshot /tmp/e2e_solve_running.png
```

Checkpoint: Floating chart window visible with Loss curve updating. After solve completes, Temperature and Flow tabs should have data.

- [ ] **Step 5: Verify post-processing tab**

```bash
browser-use eval "window.switchLeftTab('postprocess')"
sleep 1
browser-use screenshot /tmp/e2e_postprocess.png
```

Checkpoint: Model tree visible on left, right sidebar shows "📊 液冷后处理" hint.

- [ ] **Step 6: Verify post-processing popup**

```bash
# Click a tree node with CP data
browser-use state
# Find equipment node, click it
# browser-use click <N>
sleep 1
browser-use screenshot /tmp/e2e_pp_popup.png
```

Checkpoint: Floating window appears with Temperature/Flow/Pressure tabs and time-series curves.

- [ ] **Step 7: Cleanup**

```bash
browser-use close
```

All 7 checkpoints passed = feature complete.
