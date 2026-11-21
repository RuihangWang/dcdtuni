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

    var results = window.state && window.state.liquidResults;
    if (!results || !results.rack_summaries || results.rack_summaries.length === 0) {
      container.innerHTML = '<div style="padding:12px;color:#888;font-size:12px;text-align:center;">' + t('liquid.no_results') + ' available.<br>请先运行液冷求解 / Run liquid solve first.</div>';
      return;
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
    var cdus = window.allModels().filter(function(m) { return m.kind === 'cdu' && m.meta.enabled; });
    if (cdus.length > 0) {
      html += '<div style="padding:6px 8px;border-bottom:1px solid #333;">';
      html += '<div style="font-size:11px;color:#00b4dc;font-weight:bold;margin-bottom:2px;">CDU</div>';
      cdus.forEach(function(cdu) {
        var served = ((cdu.meta && cdu.meta.racks) || []).join(', ') || 'None';
        html += '<div style="font-size:10px;color:#aaa;padding:1px 0;">' + escHtml(cdu.meta.name) + ' → ' + escHtml(served) + '</div>';
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
    html += '<div style="font-size:10px;color:#ccc;">' + t('lrd.total_flow') + ': ' + totalFlowLPM.toFixed(1) + ' LPM';
    html += ' | 最高芯片温度 / Max T_chip: ' + maxChipAll.toFixed(1) + '°C</div>';
    html += '</div>';

    // Per-rack sections
    racks.forEach(function(rack, rackIdx) {
      var rackFlow = _lastVal(rack.total_flow_kg_s) / 997.0 * 60000;
      var supplyT = _lastVal(rack.supply_temp_K) - 273.15;
      var returnT = _lastVal(rack.return_temp_K) - 273.15;

      html += '<div style="padding:6px 8px;border-bottom:1px solid #333;">';
      html += '<div style="font-size:11px;color:#00b4dc;font-weight:bold;cursor:pointer;" onclick="var el=this.nextElementSibling;el.style.display=el.style.display===\'none\'?\'\':\'none\'">📊 ' + escHtml(rack.rack_id) + ' ▼</div>';
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

  window._toggleLcTransientChart = function(rowId, rackIdx, srvIdx) {
    var results = window.state && window.state.liquidResults;
    if (!results || !results.rack_summaries) return;

    var rack = results.rack_summaries[rackIdx];
    if (!rack) return;
    var srv = rack.servers[srvIdx];
    if (!srv) return;

    var cpEdgeIds = srv.cp_edge_ids || [];
    var edgeId = cpEdgeIds[0] || '';
    if (!edgeId && results.cp_details) {
      var flatIdx = 0;
      for (var ri = 0; ri < rackIdx; ri++) {
        flatIdx += (results.rack_summaries[ri].servers || []).length;
      }
      flatIdx += srvIdx;
      if (results.cp_details[flatIdx]) {
        edgeId = results.cp_details[flatIdx].edge_id;
      }
    }

    var label = rack.rack_id + ' / ' + srv.server_id;
    if (typeof window.lsmShowPostProcessWindow === 'function' && edgeId) {
      window.lsmShowPostProcessWindow('cp', edgeId, label);
    }
  };

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

    var tOutC = [];
    for (var i = 0; i < time.length; i++) {
      var m = flowKg[i] || 1e-6;
      var q = heatW[i] || 0;
      tOutC.push(supplyT + q / (Math.max(m, 1e-6) * 4182));
    }
    var chipC = chipK.map(function(v) { return v - 273.15; });
    var flowArr = flowKg.slice();

    var canvas = document.createElement('canvas');
    canvas.width = 500;
    canvas.height = 200;
    canvas.style.cssText = 'width:100%;height:auto;background:#0d0d1a;border:1px solid #333;border-radius:3px;';
    container.appendChild(canvas);

    var ctx = canvas.getContext('2d');
    var W = canvas.width, H = canvas.height;
    var pad = { left: 50, right: 50, top: 15, bottom: 30 };
    var pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;

    ctx.fillStyle = '#0d0d1a';
    ctx.fillRect(0, 0, W, H);

    var tMin = time[0], tMax = time[time.length - 1];
    if (tMax <= tMin) tMax = tMin + 1;

    var tempMin = 1e9, tempMax = -1e9;
    chipC.forEach(function(v) { if (v < tempMin) tempMin = v; if (v > tempMax) tempMax = v; });
    tOutC.forEach(function(v) { if (v < tempMin) tempMin = v; if (v > tempMax) tempMax = v; });
    var tPad = Math.max((tempMax - tempMin) * 0.15, 1);
    tempMin -= tPad; tempMax += tPad;

    var fMin = 0, fMax = -1e9;
    flowArr.forEach(function(v) { if (v > fMax) fMax = v; });
    fMax = Math.max(fMax * 1.2, 0.01);

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
    ctx.textAlign = 'center';
    for (var i = 0; i <= 4; i++) {
      var t = tMin + (tMax - tMin) * i / 4;
      var x = pad.left + pw * i / 4;
      ctx.fillText(t.toFixed(0) + 's', x, pad.top + ph + 14);
    }

    ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
    ctx.strokeRect(pad.left, pad.top, pw, ph);

    // T_chip (red)
    ctx.strokeStyle = '#e06c75'; ctx.lineWidth = 2;
    ctx.beginPath();
    for (var i = 0; i < chipC.length; i++) {
      var x = pad.left + pw * (time[i] - tMin) / (tMax - tMin);
      var y = pad.top + ph * (1 - (chipC[i] - tempMin) / (tempMax - tempMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // T_out (blue)
    ctx.strokeStyle = '#61afef'; ctx.lineWidth = 2;
    ctx.beginPath();
    for (var i = 0; i < tOutC.length; i++) {
      var x = pad.left + pw * (time[i] - tMin) / (tMax - tMin);
      var y = pad.top + ph * (1 - (tOutC[i] - tempMin) / (tempMax - tempMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Flow (green, right Y, dashed)
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
    ctx.font = '9px monospace'; ctx.textAlign = 'left';
    var ly = pad.top + 6;
    ctx.fillStyle = '#e06c75'; ctx.fillRect(pad.left + 4, ly, 8, 3);
    ctx.fillStyle = '#ccc'; ctx.fillText('T_chip', pad.left + 16, ly + 4);
    ctx.fillStyle = '#61afef'; ctx.fillRect(pad.left + 4, ly + 12, 8, 3);
    ctx.fillStyle = '#ccc'; ctx.fillText('T_out', pad.left + 16, ly + 16);
    ctx.fillStyle = '#98c379'; ctx.fillRect(pad.left + 4, ly + 24, 8, 3);
    ctx.fillStyle = '#ccc'; ctx.fillText('m (kg/s)', pad.left + 16, ly + 28);
  }

  window._highlightLiquidServer = function(serverId) {
    if (!state) return;
    var _cms = window.allModels();
    for (var i = 0; i < _cms.length; i++) {
      var m = _cms[i];
      if (m.kind !== 'rack') continue;
      var eqs = Object.values((m.constructions && m.constructions.servers) || {});
      for (var j = 0; j < eqs.length; j++) {
        if (serverId.indexOf(m.meta.name) !== -1 && serverId.indexOf((eqs[j].meta && eqs[j].meta.name) || '') !== -1) {
          if (typeof setSelection === 'function') {
            setSelection('rackEquipment', m.uid, null, null, null, null, null, eqs[j].uid, null, null);
          }
          if (typeof rebuildGeometry === 'function') rebuildGeometry();
          return;
        }
      }
      if (serverId.indexOf(m.meta.name) !== -1) {
        if (typeof setSelection === 'function') setSelection('model', m.uid);
        if (typeof rebuildGeometry === 'function') rebuildGeometry();
        return;
      }
    }
  };

  window._refreshLiquidResultsPanel = buildResultsPanel;

  var _lastResultsKey = '';
  setInterval(function() {
    if (typeof isLiquidMode !== 'function' || !isLiquidMode()) return;
    if (!window.state) return;
    var r = window.state.liquidResults;
    var key = r ? (r.epochs + ':' + r.final_loss + ':' + (r.rack_summaries || []).length) : '';
    if (key !== _lastResultsKey) {
      _lastResultsKey = key;
      buildResultsPanel();
    }
  }, 2000);

  console.log('[liquid_results_panel.js] Results panel loaded (ODIL format)');
})();
