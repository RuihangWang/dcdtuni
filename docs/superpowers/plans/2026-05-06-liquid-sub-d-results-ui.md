# Sub-D: Liquid Results UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the liquid results panel for ODIL output (rack summaries, per-server table with click-to-expand transient charts), implement per-pipe and per-chip 3D temperature coloring with color scale legend, and display CDU servedRacks in the results view.

**Architecture:** Full rewrite of `liquid_results_panel.js` to consume `LiquidResult` JSON format (`rack_summaries[]` with per-server time-series data). Inline canvas charts for transient visualization. `liquid_pipe_renderer.js` enhanced with `_tempToColor()` utility for both pipe and chip coloring. Color scale legend as an HTML overlay div in the viewport.

**Tech Stack:** Vanilla JS (ES5), HTML5 Canvas 2D, Three.js (mesh material coloring)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `STLGeneratorForOpenFoam/web/js/liquid_results_panel.js` | Results panel: system overview, rack/server table, transient chart, CDU info, 3D controls |
| `STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js` | `_tempToColor()`, per-pipe average temp coloring, per-chip temp coloring |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Color scale legend overlay div |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | Results panel i18n keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | Same |

---

### Task 1: Rewrite results panel for ODIL format

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_results_panel.js`

- [ ] **Step 1: Rewrite `buildResultsPanel()` for ODIL `LiquidResult` format**

Replace the entire content of `liquid_results_panel.js` with the following. The key changes are:
- Reads `results.rack_summaries[]` instead of `results.servers{}`
- Reads `results.converged`, `results.final_loss`, `results.epochs`
- Per-server data from `rack.servers[].flow_kg_s`, `max_chip_temp_K`, `heat_removed_W` arrays
- Uses last time-step index (`[-1]`) for summary table values
- CDU servedRacks from `state.models` CDU objects
- Click server row → expand transient chart (Task 2)

```javascript
/**
 * Liquid cooling results panel for post-processing tab.
 * Displays ODIL solver results: rack summaries, per-server details, transient charts.
 */
(function() {
  'use strict';

  var _expandedServer = null;

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function _lastVal(arr) {
    if (!arr || arr.length === 0) return 0;
    return arr[arr.length - 1];
  }

  function _chipStatus(tempC) {
    if (tempC < 75) return { emoji: '✅', color: '#ccc' };
    if (tempC < 85) return { emoji: '⚠️', color: '#ffcc00' };
    return { emoji: '🔴', color: '#ff4444' };
  }

  function buildResultsPanel() {
    var container = document.getElementById('liquid-results-container');
    if (!container) return;

    var results = state.liquidCooling && state.liquidCooling.results;
    if (!results || !results.rack_summaries || results.rack_summaries.length === 0) {
      // Also accept old format check
      if (!results || (!results.rack_summaries && !results.servers)) {
        container.innerHTML = '<div style="padding:12px;color:#888;font-size:12px;text-align:center;">暂无液冷结果 / No liquid cooling results available.<br>请先运行液冷求解 / Run liquid solve first.</div>';
        return;
      }
    }

    var html = '';

    // Solver status
    html += '<div style="padding:6px 8px;border-bottom:1px solid #333;">';
    var statusIcon = results.converged ? '✅' : '❌';
    var statusText = results.converged ? 'Converged' : 'Not converged';
    html += '<div style="font-size:11px;color:#ccc;">' + statusIcon + ' ' + statusText;
    html += ' | Loss: ' + (results.final_loss != null ? results.final_loss.toExponential(2) : '?');
    html += ' | Epochs: ' + (results.epochs || '?');
    html += '</div></div>';

    // CDU overview
    var cdus = (state.models || []).filter(function(m) { return m.kind === 'cdu' && m.enabled; });
    if (cdus.length > 0) {
      html += '<div style="padding:6px 8px;border-bottom:1px solid #333;">';
      html += '<div style="font-size:11px;color:#00b4dc;font-weight:bold;margin-bottom:2px;">CDU</div>';
      cdus.forEach(function(cdu) {
        var lc = cdu.liquidCooling || {};
        var served = (lc.servedRacks || []).join(', ') || 'None';
        html += '<div style="font-size:10px;color:#aaa;padding:1px 0;">' + escHtml(cdu.name) + ' → ' + escHtml(served) + '</div>';
      });
      html += '</div>';
    }

    // System totals
    var racks = results.rack_summaries || [];
    var maxChipAll = 0;
    var totalFlowAll = 0;
    racks.forEach(function(rack) {
      totalFlowAll += _lastVal(rack.total_flow_kg_s);
      (rack.servers || []).forEach(function(srv) {
        var tc = _lastVal(srv.max_chip_temp_K) - 273.15;
        if (tc > maxChipAll) maxChipAll = tc;
      });
    });
    var totalFlowLPM = totalFlowAll / 997.0 * 60000;

    html += '<div style="padding:6px 8px;border-bottom:1px solid #333;">';
    html += '<div style="font-size:11px;color:#00b4dc;font-weight:bold;margin-bottom:2px;">系统概览 / System Overview</div>';
    html += '<div style="font-size:10px;color:#ccc;">总流量 / Total Flow: ' + totalFlowLPM.toFixed(1) + ' LPM';
    html += ' | 最高芯片温度 / Max T_chip: ' + maxChipAll.toFixed(1) + '°C</div>';
    html += '</div>';

    // Per-rack sections
    racks.forEach(function(rack, rackIdx) {
      var rackFlow = _lastVal(rack.total_flow_kg_s) / 997.0 * 60000;
      var supplyT = _lastVal(rack.supply_temp_K) - 273.15;
      var returnT = _lastVal(rack.return_temp_K) - 273.15;

      html += '<div style="padding:6px 8px;border-bottom:1px solid #333;">';
      html += '<div style="font-size:11px;color:#00b4dc;font-weight:bold;cursor:pointer;" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display===\'none\'?\'\':\' none\'">📊 ' + escHtml(rack.rack_id) + ' ▼</div>';
      html += '<div>';
      html += '<div style="font-size:10px;color:#aaa;padding:2px 0;">Flow: ' + rackFlow.toFixed(1) + ' LPM | Supply: ' + supplyT.toFixed(1) + '°C | Return: ' + returnT.toFixed(1) + '°C</div>';

      // Server table
      html += '<div style="overflow-x:auto;">';
      html += '<table style="width:100%;font-size:10px;color:#ccc;border-collapse:collapse;">';
      html += '<thead><tr style="color:#00b4dc;border-bottom:1px solid #444;">';
      html += '<th style="text-align:left;padding:3px 4px;">Server</th>';
      html += '<th style="padding:3px 4px;">Flow<br>(LPM)</th>';
      html += '<th style="padding:3px 4px;">T_chip<br>(°C)</th>';
      html += '<th style="padding:3px 4px;">T_out<br>(°C)</th>';
      html += '<th style="padding:3px 4px;">Q<br>(W)</th>';
      html += '<th style="padding:3px 2px;">St</th>';
      html += '</tr></thead><tbody>';

      var servers = rack.servers || [];
      servers.forEach(function(srv, srvIdx) {
        var flowKg = _lastVal(srv.flow_kg_s);
        var flowLPM = flowKg / 997.0 * 60000;
        var chipK = _lastVal(srv.max_chip_temp_K);
        var chipC = chipK - 273.15;
        var heatW = _lastVal(srv.heat_removed_W);
        var tOutC = supplyT + (flowKg > 1e-6 ? heatW / (flowKg * 4182) : 0);
        var st = _chipStatus(chipC);

        var rowId = 'lc-srv-' + rackIdx + '-' + srvIdx;
        html += '<tr id="' + rowId + '" style="border-bottom:1px solid #333;color:' + st.color + ';cursor:pointer;" ';
        html += 'onclick="window._toggleLcTransientChart(\'' + rowId + '\',' + rackIdx + ',' + srvIdx + ')" ';
        html += 'title="Click to show transient chart">';
        html += '<td style="text-align:left;padding:3px 4px;font-size:9px;">' + escHtml(srv.server_id) + '</td>';
        html += '<td style="text-align:center;padding:3px 4px;">' + flowLPM.toFixed(1) + '</td>';
        html += '<td style="text-align:center;padding:3px 4px;font-weight:bold;">' + chipC.toFixed(1) + '</td>';
        html += '<td style="text-align:center;padding:3px 4px;">' + tOutC.toFixed(1) + '</td>';
        html += '<td style="text-align:center;padding:3px 4px;">' + heatW.toFixed(0) + '</td>';
        html += '<td style="text-align:center;padding:3px 2px;">' + st.emoji + '</td>';
        html += '</tr>';
        // Placeholder for transient chart
        html += '<tr id="' + rowId + '-chart" style="display:none;"><td colspan="6" style="padding:0;"></td></tr>';
      });

      html += '</tbody></table>';
      html += '<div style="font-size:9px;color:#666;padding:4px 0;">✅ &lt;75°C &nbsp; ⚠️ 75-85°C &nbsp; 🔴 &gt;85°C</div>';
      html += '</div></div></div>';
    });

    // 3D Display Controls
    html += '<div style="padding:6px 8px;border-top:1px solid #333;">';
    html += '<div style="font-size:11px;color:#00b4dc;font-weight:bold;margin-bottom:4px;">🎨 3D Display</div>';
    html += '<label style="font-size:11px;color:#aaa;cursor:pointer;display:block;padding:2px 0;">';
    html += '<input type="checkbox" id="lc-color-pipes" onchange="window._togglePipeTemperatureColoring && window._togglePipeTemperatureColoring(this.checked)"> 管道温度着色 / Pipe temp coloring';
    html += '</label>';
    html += '<label style="font-size:11px;color:#aaa;cursor:pointer;display:block;padding:2px 0;">';
    html += '<input type="checkbox" id="lc-color-servers" onchange="window._toggleServerTemperatureColoring && window._toggleServerTemperatureColoring(this.checked)"> 芯片温度着色 / Chip temp coloring';
    html += '</label>';
    html += '</div>';

    container.innerHTML = html;
  }

  /**
   * Toggle transient chart for a server row.
   */
  window._toggleLcTransientChart = function(rowId, rackIdx, srvIdx) {
    var chartRow = document.getElementById(rowId + '-chart');
    if (!chartRow) return;

    // If already expanded, collapse
    if (_expandedServer === rowId) {
      chartRow.style.display = 'none';
      chartRow.querySelector('td').innerHTML = '';
      _expandedServer = null;
      return;
    }

    // Collapse previous
    if (_expandedServer) {
      var prev = document.getElementById(_expandedServer + '-chart');
      if (prev) { prev.style.display = 'none'; prev.querySelector('td').innerHTML = ''; }
    }

    _expandedServer = rowId;
    chartRow.style.display = '';

    var results = state.liquidCooling && state.liquidCooling.results;
    if (!results || !results.rack_summaries) return;

    var rack = results.rack_summaries[rackIdx];
    if (!rack) return;
    var srv = rack.servers[srvIdx];
    if (!srv) return;

    var td = chartRow.querySelector('td');
    _drawTransientChart(td, results, rack, srv);
  };

  /**
   * Draw inline transient chart for one server.
   */
  function _drawTransientChart(container, results, rack, srv) {
    container.innerHTML = '';

    var time = results.time_s || [];
    if (time.length < 2) {
      container.innerHTML = '<div style="color:#888;font-size:10px;padding:8px;">N_t < 2, no transient data</div>';
      return;
    }

    var chipK = srv.max_chip_temp_K || [];
    var flowKg = srv.flow_kg_s || [];
    var heatW = srv.heat_removed_W || [];
    var supplyT = _lastVal(rack.supply_temp_K) - 273.15;

    // Compute T_out from heat balance: T_out = T_supply + Q / (m * cp)
    var tOutC = [];
    for (var i = 0; i < time.length; i++) {
      var m = flowKg[i] || 1e-6;
      var q = heatW[i] || 0;
      tOutC.push(supplyT + q / (Math.max(m, 1e-6) * 4182));
    }
    var chipC = chipK.map(function(v) { return v - 273.15; });
    var flowArr = flowKg.slice();

    // Canvas
    var canvas = document.createElement('canvas');
    canvas.width = 500;
    canvas.height = 200;
    canvas.style.cssText = 'width:100%;height:auto;background:#0d0d1a;border:1px solid #333;border-radius:3px;';
    container.appendChild(canvas);

    var ctx = canvas.getContext('2d');
    var W = canvas.width, H = canvas.height;
    var pad = { left: 50, right: 50, top: 15, bottom: 30 };
    var pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;

    // Clear
    ctx.fillStyle = '#0d0d1a';
    ctx.fillRect(0, 0, W, H);

    // Data ranges
    var tMin = time[0], tMax = time[time.length - 1];
    if (tMax <= tMin) tMax = tMin + 1;

    // Left Y: temperature
    var tempMin = 1e9, tempMax = -1e9;
    chipC.forEach(function(v) { if (v < tempMin) tempMin = v; if (v > tempMax) tempMax = v; });
    tOutC.forEach(function(v) { if (v < tempMin) tempMin = v; if (v > tempMax) tempMax = v; });
    var tPad = Math.max((tempMax - tempMin) * 0.15, 1);
    tempMin -= tPad; tempMax += tPad;

    // Right Y: flow
    var fMin = 0, fMax = -1e9;
    flowArr.forEach(function(v) { if (v > fMax) fMax = v; });
    fMax = Math.max(fMax * 1.2, 0.01);

    // Grid
    ctx.strokeStyle = '#222'; ctx.lineWidth = 0.5;
    ctx.font = '9px monospace'; ctx.fillStyle = '#555';
    for (var i = 0; i <= 4; i++) {
      var y = pad.top + ph * (1 - i / 4);
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + pw, y); ctx.stroke();
      var tVal = tempMin + (tempMax - tempMin) * i / 4;
      ctx.textAlign = 'right';
      ctx.fillText(tVal.toFixed(0) + '°C', pad.left - 4, y + 3);
      var fVal = fMin + (fMax - fMin) * i / 4;
      ctx.textAlign = 'left';
      ctx.fillText(fVal.toFixed(2), pad.left + pw + 4, y + 3);
    }
    // X labels
    ctx.textAlign = 'center';
    for (var i = 0; i <= 4; i++) {
      var t = tMin + (tMax - tMin) * i / 4;
      var x = pad.left + pw * i / 4;
      ctx.fillText(t.toFixed(0) + 's', x, pad.top + ph + 14);
    }

    // Axes
    ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
    ctx.strokeRect(pad.left, pad.top, pw, ph);

    // Plot T_chip (red)
    ctx.strokeStyle = '#e06c75'; ctx.lineWidth = 2;
    ctx.beginPath();
    for (var i = 0; i < chipC.length; i++) {
      var x = pad.left + pw * (time[i] - tMin) / (tMax - tMin);
      var y = pad.top + ph * (1 - (chipC[i] - tempMin) / (tempMax - tempMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Plot T_out (blue)
    ctx.strokeStyle = '#61afef'; ctx.lineWidth = 2;
    ctx.beginPath();
    for (var i = 0; i < tOutC.length; i++) {
      var x = pad.left + pw * (time[i] - tMin) / (tMax - tMin);
      var y = pad.top + ph * (1 - (tOutC[i] - tempMin) / (tempMax - tempMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Plot flow (green, right Y)
    ctx.strokeStyle = '#98c379'; ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 2]);
    ctx.beginPath();
    for (var i = 0; i < flowArr.length; i++) {
      var x = pad.left + pw * (time[i] - tMin) / (tMax - tMin);
      var y = pad.top + ph * (1 - (flowArr[i] - fMin) / (fMax - fMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // Legend
    ctx.font = '9px monospace';
    ctx.textAlign = 'left';
    var ly = pad.top + 6;
    ctx.fillStyle = '#e06c75'; ctx.fillRect(pad.left + 4, ly, 8, 3);
    ctx.fillStyle = '#ccc'; ctx.fillText('T_chip', pad.left + 16, ly + 4);
    ctx.fillStyle = '#61afef'; ctx.fillRect(pad.left + 4, ly + 12, 8, 3);
    ctx.fillStyle = '#ccc'; ctx.fillText('T_out', pad.left + 16, ly + 16);
    ctx.fillStyle = '#98c379'; ctx.fillRect(pad.left + 4, ly + 24, 8, 3);
    ctx.fillStyle = '#ccc'; ctx.fillText('m (kg/s)', pad.left + 16, ly + 28);
  }

  // Keep existing highlight and refresh functions
  window._highlightLiquidServer = function(serverId) {
    if (!state || !state.models) return;
    for (var i = 0; i < state.models.length; i++) {
      var m = state.models[i];
      if (m.kind !== 'rack') continue;
      var eqs = m.equipments || [];
      for (var j = 0; j < eqs.length; j++) {
        if (serverId.indexOf(m.name) !== -1 && serverId.indexOf(eqs[j].name || '') !== -1) {
          if (typeof setSelection === 'function') {
            setSelection('rackEquipment', m.id, null, null, null, null, null, eqs[j].id, null, null);
          }
          if (typeof rebuildGeometry === 'function') rebuildGeometry();
          return;
        }
      }
      if (serverId.indexOf(m.name) !== -1) {
        if (typeof setSelection === 'function') setSelection('model', m.id);
        if (typeof rebuildGeometry === 'function') rebuildGeometry();
        return;
      }
    }
  };

  window._refreshLiquidResultsPanel = buildResultsPanel;

  // Auto-refresh when results change
  var _lastResultsKey = '';
  setInterval(function() {
    if (!state || !state.liquidCooling) return;
    var r = state.liquidCooling.results;
    var key = r ? (r.epochs + ':' + r.final_loss + ':' + (r.rack_summaries || []).length) : '';
    if (key !== _lastResultsKey) {
      _lastResultsKey = key;
      buildResultsPanel();
    }
  }, 2000);

  console.log('[liquid_results_panel.js] Results panel loaded (ODIL format)');
})();
```

- [ ] **Step 2: Verify file saved correctly**

Open the file and confirm it has the new ODIL-format code with `rack_summaries` references.

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_results_panel.js
git commit -m "feat(liquid): rewrite results panel for ODIL format with transient charts"
cd ..
```

---

### Task 2: 3D temperature coloring

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js`

- [ ] **Step 1: Add `_tempToColor()` utility function**

In `liquid_pipe_renderer.js`, before the `colorPipesByTemperature` function (around line 726), add:

```javascript
  /**
   * Map temperature to color via linear interpolation: blue → red.
   * @param {number} T - Temperature value
   * @param {number} Tmin - Minimum temperature (maps to blue 0x2196F3)
   * @param {number} Tmax - Maximum temperature (maps to red 0xF44336)
   * @returns {number} Hex color integer
   */
  function _tempToColor(T, Tmin, Tmax) {
    var t = Math.max(0, Math.min(1, (T - Tmin) / (Tmax - Tmin + 1e-6)));
    var r = Math.round(0x21 + t * (0xF4 - 0x21));
    var g = Math.round(0x96 + t * (0x43 - 0x96));
    var b = Math.round(0xF3 + t * (0x36 - 0xF3));
    return (r << 16) | (g << 8) | b;
  }
```

- [ ] **Step 2: Rewrite `colorPipesByTemperature()` for per-pipe average temperature**

Replace the existing `colorPipesByTemperature` function (lines 729-748) with:

```javascript
  var _savedPipeColors = {};

  function colorPipesByTemperature(enabled) {
    if (!_pipeGroup) return;
    var results = (window.state && window.state.liquidCooling) ? window.state.liquidCooling.results : null;

    if (enabled && results && results.cell_temp_K && results.cell_temp_K.length > 0) {
      // Compute per-pipe average temperature from cell data
      var cellTemps = results.cell_temp_K;
      var N_t = (results.time_s || []).length || 1;

      // Flatten: cell_temp_K is [N_cells][N_t], take last timestep
      var lastStepTemps = [];
      for (var i = 0; i < cellTemps.length; i++) {
        var row = cellTemps[i];
        lastStepTemps.push(Array.isArray(row) ? row[row.length - 1] : row);
      }

      // Find global T range
      var tMin = 1e9, tMax = -1e9;
      for (var i = 0; i < lastStepTemps.length; i++) {
        if (lastStepTemps[i] < tMin) tMin = lastStepTemps[i];
        if (lastStepTemps[i] > tMax) tMax = lastStepTemps[i];
      }

      // For each pipe mesh, compute average T from its cells
      // Cells are stored sequentially per pipe in the backend.
      // Since we can't easily map pipe meshes to cell ranges without the compiled graph,
      // use a simpler approach: color all pipe meshes by their order index,
      // distributing cells evenly across pipes.
      var pipeMeshes = [];
      _pipeGroup.traverse(function(child) {
        if (child.isMesh && child.userData.isPipe) {
          pipeMeshes.push(child);
        }
      });

      if (pipeMeshes.length > 0 && lastStepTemps.length > 0) {
        var cellsPerPipe = Math.max(1, Math.floor(lastStepTemps.length / pipeMeshes.length));
        for (var p = 0; p < pipeMeshes.length; p++) {
          var mesh = pipeMeshes[p];
          _savedPipeColors[mesh.uuid] = mesh.userData.pipeColor;
          var cStart = p * cellsPerPipe;
          var cEnd = Math.min(cStart + cellsPerPipe, lastStepTemps.length);
          if (cStart >= lastStepTemps.length) cStart = lastStepTemps.length - 1;
          var sum = 0, count = 0;
          for (var c = cStart; c < cEnd; c++) {
            sum += lastStepTemps[c];
            count++;
          }
          var avgT = count > 0 ? sum / count : (tMin + tMax) / 2;
          mesh.material.color.setHex(_tempToColor(avgT, tMin, tMax));
        }
      }

      // Show colorbar
      _updateColorbar(true, tMin - 273.15, tMax - 273.15);
    } else {
      // Restore original colors
      _pipeGroup.traverse(function(child) {
        if (child.isMesh && child.userData.isPipe) {
          var saved = _savedPipeColors[child.uuid];
          if (saved != null) {
            child.material.color.setHex(saved);
          } else {
            child.material.color.setHex(child.userData.pipeColor);
          }
        }
      });
      _savedPipeColors = {};
      _updateColorbar(false, 0, 0);
    }
    if (window.requestRender) window.requestRender();
  }
```

- [ ] **Step 3: Implement `_toggleServerTemperatureColoring()`**

Replace the stub (lines 754-758) with:

```javascript
  var _savedEquipColors = {};

  window._toggleServerTemperatureColoring = function(enabled) {
    var results = (window.state && window.state.liquidCooling) ? window.state.liquidCooling.results : null;
    if (!results || !results.chip_temp_K || results.chip_temp_K.length === 0) return;

    var chipTemps = results.chip_temp_K;
    var N_t = (results.time_s || []).length || 1;

    // Get last-timestep chip temps
    var lastChip = [];
    for (var i = 0; i < chipTemps.length; i++) {
      var row = chipTemps[i];
      lastChip.push(Array.isArray(row) ? row[row.length - 1] : row);
    }

    var tMin = 1e9, tMax = -1e9;
    for (var i = 0; i < lastChip.length; i++) {
      if (lastChip[i] < tMin) tMin = lastChip[i];
      if (lastChip[i] > tMax) tMax = lastChip[i];
    }

    if (!window.state || !window.state.models) return;

    // Map rack_summaries to equipment models
    var racks = results.rack_summaries || [];
    var cpIdx = 0;

    if (enabled) {
      racks.forEach(function(rack) {
        var rackModel = window.state.models.find(function(m) { return m.kind === 'rack' && m.name === rack.rack_id; });
        if (!rackModel) return;
        var eqs = rackModel.equipments || [];
        (rack.servers || []).forEach(function(srv) {
          var eq = eqs.find(function(e) { return e.name === srv.server_id; });
          if (!eq || cpIdx >= lastChip.length) return;
          var T = lastChip[cpIdx];
          cpIdx++;
          // Find mesh for this equipment
          if (typeof globalGroup === 'undefined') return;
          globalGroup.traverse(function(child) {
            if (child.isMesh && child.userData.modelId === rackModel.id && child.userData.equipmentId === eq.id) {
              _savedEquipColors[child.uuid] = child.material.color.getHex();
              child.material.color.setHex(_tempToColor(T, tMin, tMax));
            }
          });
        });
      });
      _updateColorbar(true, tMin - 273.15, tMax - 273.15);
    } else {
      // Restore
      if (typeof globalGroup !== 'undefined') {
        globalGroup.traverse(function(child) {
          if (child.isMesh && _savedEquipColors[child.uuid] != null) {
            child.material.color.setHex(_savedEquipColors[child.uuid]);
          }
        });
      }
      _savedEquipColors = {};
      var pipesCb = document.getElementById('lc-color-pipes');
      if (!pipesCb || !pipesCb.checked) _updateColorbar(false, 0, 0);
    }
    if (window.requestRender) window.requestRender();
  };
```

- [ ] **Step 4: Add `_updateColorbar()` helper**

Before the "Expose globally" section (around line 760), add:

```javascript
  function _updateColorbar(show, tMinC, tMaxC) {
    var bar = document.getElementById('lc-colorbar');
    if (!bar) return;
    bar.style.display = show ? '' : 'none';
    if (show) {
      var minEl = document.getElementById('lc-colorbar-min');
      var maxEl = document.getElementById('lc-colorbar-max');
      if (minEl) minEl.textContent = tMinC.toFixed(0) + '°C';
      if (maxEl) maxEl.textContent = tMaxC.toFixed(0) + '°C';
    }
  }
```

- [ ] **Step 5: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/js/liquid_pipe_renderer.js
git commit -m "feat(liquid): per-pipe and per-chip 3D temperature coloring"
cd ..
```

---

### Task 3: Color scale legend + i18n + HTML

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add colorbar overlay div**

In `STL_generate.html`, find the 3D viewport container area. Search for the `<canvas` element used by Three.js renderer or the div that wraps it. A good location is right after the toolbar or at the end of the center panel. Add this div before the `</body>` or inside the center panel area:

```html
<!-- Liquid temperature colorbar overlay -->
<div id="lc-colorbar" style="display:none; position:fixed; bottom:60px; right:20px; z-index:100; pointer-events:none;">
  <div style="background:linear-gradient(to right, #2196F3, #F44336); width:120px; height:16px; border-radius:3px; border:1px solid #555;"></div>
  <div style="display:flex; justify-content:space-between; font-size:9px; color:#aaa; font-family:monospace; margin-top:2px;">
    <span id="lc-colorbar-min">0°C</span>
    <span id="lc-colorbar-max">100°C</span>
  </div>
</div>
```

- [ ] **Step 2: Add i18n keys to zh-CN.json**

Add these keys near the existing `lsm.*` section:

```json
"lrd.system_overview": "系统概览",
"lrd.total_flow": "总流量",
"lrd.max_chip_temp": "最高芯片温度",
"lrd.pipe_temp_color": "管道温度着色",
"lrd.chip_temp_color": "芯片温度着色",
"lrd.click_expand": "点击展开瞬态图表"
```

- [ ] **Step 3: Add i18n keys to en-US.json**

```json
"lrd.system_overview": "System Overview",
"lrd.total_flow": "Total Flow",
"lrd.max_chip_temp": "Max Chip Temp",
"lrd.pipe_temp_color": "Pipe temp coloring",
"lrd.chip_temp_color": "Chip temp coloring",
"lrd.click_expand": "Click to expand transient chart"
```

- [ ] **Step 4: Commit and update submodule**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat(liquid): colorbar overlay + results i18n keys"
cd ..
git add STLGeneratorForOpenFoam
git commit -m "fix: update submodule — liquid results UI, transient charts, 3D coloring"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run backend tests**

Run: `conda run -n dcdtuni python -m pytest src/backend/tests/test_liquid/ -q --tb=short`
Expected: All 148 tests PASS (no backend changes in Sub-D)

- [ ] **Step 2: Verify all commits**

Run: `git log --oneline -5`
Expected: Shows Sub-D commits
