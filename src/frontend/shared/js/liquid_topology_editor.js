/**
 * Equipment Level View — 2D Canvas editor for server cold plate configuration.
 * Replaces the 3D viewport when activated.
 * Phase 6A: drag-connect wiring for custom routing.
 */
(function() {
  'use strict';

  // Layout constants
  const MARGIN = 20;
  const HEADER_HEIGHT = 50;
  const SERVER_HEIGHT = 40;
  const SERVER_GAP = 4;
  const COLD_PLATE_W = 80;
  const COLD_PLATE_H = 28;
  const PORT_RADIUS = 6;
  const MANIFOLD_HEIGHT = 30;
  const LABEL_FONT = '12px monospace';
  const TITLE_FONT = 'bold 14px monospace';

  // Colors
  const BG_COLOR = '#1a1a2e';
  const SERVER_BG = '#2a2a3e';
  const SERVER_BORDER = '#444466';
  const SERVER_LC_BG = '#1a2a3e';
  const CP_FILL = '#334466';
  const CP_BORDER = '#5588cc';
  const SUPPLY_COLOR = '#2196F3';
  const RETURN_COLOR = '#F44336';
  const PORT_EMPTY = '#555577';
  const TEXT_COLOR = '#ccccdd';
  const HIGHLIGHT_COLOR = '#ffcc00';
  const CONN_COLOR = '#66aaff';
  const CONN_SELECTED_COLOR = '#ffcc00';

  let _canvas = null;
  let _ctx = null;
  let _container = null;
  let _active = false;
  let _selectedRackId = null;
  let _hoveredPort = null;

  // Drag-connect state
  let _dragFrom = null;        // {eqId, portId, x, y}
  let _dragMouseX = 0;
  let _dragMouseY = 0;
  let _selectedConnection = null; // {eqId, fromPort, toPort}
  let _portPositions = [];     // [{eqId, portId, type:'in'|'out', x, y}]

  /**
   * Initialize the Equipment Level View.
   * Creates a canvas overlay that replaces the 3D viewport.
   */
  function activate() {
    if (_active) return;
    _active = true;

    // Find the 3D viewer container
    var viewer = document.getElementById('viewer');
    if (!viewer) return;

    // Hide 3D content
    viewer.style.position = 'relative';

    // Create overlay container
    _container = document.createElement('div');
    _container.id = 'equipment-level-view';
    _container.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;background:' + BG_COLOR + ';z-index:100;display:flex;flex-direction:column;';

    // Header bar
    var header = document.createElement('div');
    header.style.cssText = 'display:flex;align-items:center;padding:8px 12px;gap:8px;background:#16162a;border-bottom:1px solid #333;flex-shrink:0;';

    var backBtn = document.createElement('button');
    backBtn.className = 'btn';
    backBtn.textContent = '\u2190 返回3D / Back to 3D';
    backBtn.style.cssText = 'font-size:12px;padding:4px 12px;';
    backBtn.addEventListener('click', deactivate);
    header.appendChild(backBtn);

    var sep = document.createElement('span');
    sep.style.cssText = 'color:#555;margin:0 8px;';
    sep.textContent = '|';
    header.appendChild(sep);

    // Rack selector tabs
    var tabsDiv = document.createElement('div');
    tabsDiv.id = 'elv-rack-tabs';
    tabsDiv.style.cssText = 'display:flex;gap:4px;';
    header.appendChild(tabsDiv);

    // Preset buttons
    var presetsDiv = document.createElement('div');
    presetsDiv.style.cssText = 'margin-left:auto;display:flex;gap:4px;align-items:center;';
    [{label: '串联 / Series', key: 'series'}, {label: '并联 / Parallel', key: 'parallel'}, {label: '自定义 / Custom', key: 'custom'}].forEach(function(preset) {
      var btn = document.createElement('button');
      btn.className = 'btn';
      btn.textContent = preset.label;
      btn.style.cssText = 'font-size:11px;padding:3px 10px;';
      btn.addEventListener('click', function() { applyRoutingPreset(preset.key); });
      presetsDiv.appendChild(btn);
    });

    // Disconnect button
    var disconnectBtn = document.createElement('button');
    disconnectBtn.className = 'btn';
    disconnectBtn.textContent = '断开 / Disconnect';
    disconnectBtn.style.cssText = 'font-size:11px;padding:3px 10px;background:rgba(180,50,50,0.3);border-color:#aa3333;color:#ffaaaa;';
    disconnectBtn.addEventListener('click', function() { window.deleteSelectedConnection && window.deleteSelectedConnection(); });
    presetsDiv.appendChild(disconnectBtn);

    header.appendChild(presetsDiv);

    _container.appendChild(header);

    // Canvas
    _canvas = document.createElement('canvas');
    _canvas.style.cssText = 'flex:1;cursor:default;';
    _container.appendChild(_canvas);
    _ctx = _canvas.getContext('2d');

    viewer.appendChild(_container);

    // Resize canvas to container
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // Replace click with mousedown/mouseup/mousemove
    _canvas.addEventListener('mousedown', onCanvasMouseDown);
    _canvas.addEventListener('mouseup', onCanvasMouseUp);
    _canvas.addEventListener('mousemove', onCanvasMouseMove);

    // Keyboard: Delete/Backspace deletes selected connection
    document.addEventListener('keydown', onKeyDown);

    // Select first rack
    var racks = window.allModels().filter(function(m) { return m.kind === 'rack' && m.meta.enabled; });
    if (racks.length > 0) {
      _selectedRackId = racks[0].uid;
    }

    buildRackTabs();
    render();
  }

  function deactivate() {
    if (!_active) return;
    _active = false;
    window.removeEventListener('resize', resizeCanvas);
    document.removeEventListener('keydown', onKeyDown);
    if (_container && _container.parentNode) {
      _container.parentNode.removeChild(_container);
    }
    _canvas = null;
    _ctx = null;
    _container = null;
    _selectedRackId = null;
    _dragFrom = null;
    _selectedConnection = null;
    _portPositions = [];
    // Trigger 3D re-render
    if (window.requestRender) window.requestRender();
  }

  function resizeCanvas() {
    if (!_canvas || !_container) return;
    var rect = _canvas.getBoundingClientRect();
    _canvas.width = rect.width * window.devicePixelRatio;
    _canvas.height = rect.height * window.devicePixelRatio;
    _ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    render();
  }

  function buildRackTabs() {
    var tabsDiv = document.getElementById('elv-rack-tabs');
    if (!tabsDiv) return;
    tabsDiv.innerHTML = '';
    var racks = window.allModels().filter(function(m) { return m.kind === 'rack' && m.meta.enabled; });
    racks.forEach(function(rack) {
      var btn = document.createElement('button');
      btn.className = 'btn';
      btn.textContent = rack.meta.name;
      btn.style.cssText = 'font-size:11px;padding:3px 10px;' +
        (rack.uid === _selectedRackId ? 'background:rgba(0,180,220,0.2);border-color:#00b4dc;color:#00b4dc;' : '');
      btn.addEventListener('click', function() {
        _selectedRackId = rack.uid;
        buildRackTabs();
        render();
      });
      tabsDiv.appendChild(btn);
    });
  }

  /**
   * Main render function — draws the rack front view with servers and cold plates.
   */
  function render() {
    if (!_ctx || !_canvas) return;
    var W = _canvas.width / window.devicePixelRatio;
    var H = _canvas.height / window.devicePixelRatio;
    var ctx = _ctx;

    // Reset port positions at start of each render
    _portPositions = [];

    // Clear
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    ctx.fillStyle = BG_COLOR;
    ctx.fillRect(0, 0, W, H);

    var rack = _selectedRackId ? window._modelMap && window._modelMap.get(_selectedRackId) : null;
    if (!rack || rack.kind !== 'rack') {
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = TITLE_FONT;
      ctx.textAlign = 'center';
      ctx.fillText(t('liquid.no_rack_selected'), W / 2, H / 2);
      return;
    }

    var equipments = Object.values((rack.constructions && rack.constructions.servers) || {});
    var unitCount = (rack.geometry && rack.geometry.slot) || 42;

    // Layout
    var contentX = MARGIN + 60;
    var contentW = W - contentX - MARGIN;
    var startY = MARGIN;

    // Title
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = TITLE_FONT;
    ctx.textAlign = 'left';
    ctx.fillText(rack.meta.name + ' \u2014 Equipment Level View', contentX, startY + 14);
    startY += 28;

    // Supply manifold
    ctx.strokeStyle = SUPPLY_COLOR;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(contentX, startY + MANIFOLD_HEIGHT / 2);
    ctx.lineTo(contentX + contentW, startY + MANIFOLD_HEIGHT / 2);
    ctx.stroke();
    ctx.fillStyle = SUPPLY_COLOR;
    ctx.font = '11px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('供水总管 / Supply Manifold', contentX + 4, startY + MANIFOLD_HEIGHT / 2 - 6);
    startY += MANIFOLD_HEIGHT + 4;

    // Draw each equipment
    equipments.sort(function(a, b) {
      return ((b.geometry && b.geometry.slotPosition) || 0) - ((a.geometry && a.geometry.slotPosition) || 0);
    }); // Top to bottom

    equipments.forEach(function(eq) {
      var startUnit = (eq.geometry && eq.geometry.slotPosition) || 0;
      var spanU = (eq.geometry && eq.geometry.slotOccupation) || 1;
      var eqH = Math.max(SERVER_HEIGHT, spanU * 8);
      var hasLC = eq.liquidCooling && eq.liquidCooling.enabled;

      // Server background
      ctx.fillStyle = hasLC ? SERVER_LC_BG : SERVER_BG;
      ctx.strokeStyle = SERVER_BORDER;
      ctx.lineWidth = 1;
      roundRect(ctx, contentX, startY, contentW, eqH, 4);
      ctx.fill();
      ctx.stroke();

      // U-slot label
      ctx.fillStyle = '#888899';
      ctx.font = '10px monospace';
      ctx.textAlign = 'right';
      var uLabel = 'U' + startUnit + '-' + (startUnit + spanU - 1);
      ctx.fillText(uLabel, contentX - 6, startY + eqH / 2 + 4);

      // Equipment name + kind
      ctx.fillStyle = TEXT_COLOR;
      ctx.font = LABEL_FONT;
      ctx.textAlign = 'left';
      var kindBadge = eq.type === 'fanServer' ? '[FAN]' : '[NONFAN]';
      ctx.fillText(kindBadge + ' ' + ((eq.meta && eq.meta.name) || eq.uid || 'Equipment'), contentX + 8, startY + 14);

      // Liquid cooling toggle
      var toggleX = contentX + contentW - 120;
      var toggleY = startY + 4;
      ctx.fillStyle = hasLC ? '#00b4dc' : '#555';
      ctx.font = '11px monospace';
      ctx.textAlign = 'left';
      ctx.fillText(hasLC ? '\u25cf 液冷 ON' : '\u25cb 液冷 OFF', toggleX, toggleY + 12);

      // Store click region for toggle
      eq._uiToggleRect = { x: toggleX - 4, y: toggleY, w: 80, h: 18 };

      // Cold plates (if LC enabled)
      if (hasLC && eq.liquidCooling.coldPlates && eq.liquidCooling.coldPlates.length > 0) {
        var cpStartX = contentX + 160;
        var cpY = startY + (eqH - COLD_PLATE_H) / 2;
        var routing = (eq.liquidCooling.routing && eq.liquidCooling.routing.type) || 'series';

        // Add manifold ports for this LC-enabled server
        _portPositions.push({eqId: eq.uid, portId: 'manifold_in', type: 'out', x: contentX + 140, y: startY + 2});
        _portPositions.push({eqId: eq.uid, portId: 'manifold_out', type: 'in', x: contentX + 140, y: startY + eqH - 2});

        eq.liquidCooling.coldPlates.forEach(function(cp, cpIdx) {
          var cpX = cpStartX + cpIdx * (COLD_PLATE_W + 30);

          // Cold plate box
          ctx.fillStyle = CP_FILL;
          ctx.strokeStyle = CP_BORDER;
          ctx.lineWidth = 1.5;
          roundRect(ctx, cpX, cpY, COLD_PLATE_W, COLD_PLATE_H, 3);
          ctx.fill();
          ctx.stroke();

          // Label
          ctx.fillStyle = TEXT_COLOR;
          ctx.font = '10px monospace';
          ctx.textAlign = 'center';
          ctx.fillText(cp.label || cp.id || 'CP', cpX + COLD_PLATE_W / 2, cpY + COLD_PLATE_H / 2 + 4);

          // Inlet port (left, blue)
          ctx.beginPath();
          ctx.arc(cpX - 2, cpY + COLD_PLATE_H / 2, PORT_RADIUS, 0, Math.PI * 2);
          ctx.fillStyle = SUPPLY_COLOR;
          ctx.fill();
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 1;
          ctx.stroke();
          // Track inlet port position
          _portPositions.push({eqId: eq.uid, portId: cp.id + '.in', type: 'in', x: cpX - 2, y: cpY + COLD_PLATE_H / 2});

          // Outlet port (right, red)
          ctx.beginPath();
          ctx.arc(cpX + COLD_PLATE_W + 2, cpY + COLD_PLATE_H / 2, PORT_RADIUS, 0, Math.PI * 2);
          ctx.fillStyle = RETURN_COLOR;
          ctx.fill();
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 1;
          ctx.stroke();
          // Track outlet port position
          _portPositions.push({eqId: eq.uid, portId: cp.id + '.out', type: 'out', x: cpX + COLD_PLATE_W + 2, y: cpY + COLD_PLATE_H / 2});

          // Draw connection lines between cold plates (series)
          if (routing === 'series' && cpIdx > 0) {
            var prevCpX = cpStartX + (cpIdx - 1) * (COLD_PLATE_W + 30);
            ctx.strokeStyle = '#66aaff';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(prevCpX + COLD_PLATE_W + 2 + PORT_RADIUS, cpY + COLD_PLATE_H / 2);
            ctx.lineTo(cpX - 2 - PORT_RADIUS, cpY + COLD_PLATE_H / 2);
            ctx.stroke();
            // Arrow
            var arrowX = cpX - 2 - PORT_RADIUS - 6;
            ctx.beginPath();
            ctx.moveTo(arrowX, cpY + COLD_PLATE_H / 2 - 4);
            ctx.lineTo(arrowX + 8, cpY + COLD_PLATE_H / 2);
            ctx.lineTo(arrowX, cpY + COLD_PLATE_H / 2 + 4);
            ctx.fillStyle = '#66aaff';
            ctx.fill();
          }
        });

        // Draw custom routing connections as bezier curves
        if (routing === 'custom') {
          var connections = (eq.liquidCooling.routing && eq.liquidCooling.routing.connections) || [];
          connections.forEach(function(conn) {
            var fromPort = _portPositions.find(function(p) { return p.eqId === eq.uid && p.portId === conn.fromPort; });
            var toPort = _portPositions.find(function(p) { return p.eqId === eq.uid && p.portId === conn.toPort; });
            if (fromPort && toPort) {
              var isSelected = _selectedConnection &&
                _selectedConnection.eqId === eq.uid &&
                _selectedConnection.fromPort === conn.fromPort &&
                _selectedConnection.toPort === conn.toPort;
              drawBezierConnection(ctx, fromPort.x, fromPort.y, toPort.x, toPort.y, isSelected);
            }
          });
        }

        // Routing label
        ctx.fillStyle = '#888';
        ctx.font = '10px monospace';
        ctx.textAlign = 'left';
        ctx.fillText('routing: ' + routing, cpStartX, startY + eqH - 4);
      }

      startY += eqH + SERVER_GAP;
    });

    // Return manifold
    startY += 4;
    ctx.strokeStyle = RETURN_COLOR;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(contentX, startY + MANIFOLD_HEIGHT / 2);
    ctx.lineTo(contentX + contentW, startY + MANIFOLD_HEIGHT / 2);
    ctx.stroke();
    ctx.fillStyle = RETURN_COLOR;
    ctx.font = '11px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('回水总管 / Return Manifold', contentX + 4, startY + MANIFOLD_HEIGHT / 2 - 6);

    // Draw in-progress drag line
    if (_dragFrom) {
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = HIGHLIGHT_COLOR;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(_dragFrom.x, _dragFrom.y);
      ctx.lineTo(_dragMouseX, _dragMouseY);
      ctx.stroke();
      ctx.setLineDash([]);
      // Draw a small circle at drag origin
      ctx.beginPath();
      ctx.arc(_dragFrom.x, _dragFrom.y, PORT_RADIUS + 2, 0, Math.PI * 2);
      ctx.strokeStyle = HIGHLIGHT_COLOR;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();
    }
  }

  // ─── Helper: draw bezier curve for a connection ───────────────────────────

  function drawBezierConnection(ctx, x1, y1, x2, y2, isSelected) {
    var dx = Math.abs(x2 - x1) * 0.5 + 20;
    ctx.save();
    ctx.strokeStyle = isSelected ? CONN_SELECTED_COLOR : CONN_COLOR;
    ctx.lineWidth = isSelected ? 3 : 2;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.bezierCurveTo(x1 + dx, y1, x2 - dx, y2, x2, y2);
    ctx.stroke();
    // Arrowhead at destination
    var angle = Math.atan2(y2 - y1, x2 - x1);
    ctx.fillStyle = isSelected ? CONN_SELECTED_COLOR : CONN_COLOR;
    ctx.beginPath();
    ctx.translate(x2, y2);
    ctx.rotate(angle);
    ctx.moveTo(0, 0);
    ctx.lineTo(-10, -4);
    ctx.lineTo(-10, 4);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  // ─── Helper: get canvas-relative mouse position ───────────────────────────

  function getCanvasPos(e) {
    var rect = _canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  // ─── Helper: hit-test a port near (x,y) ──────────────────────────────────

  function hitTestPort(x, y, typeFilter) {
    var best = null;
    var bestDist = PORT_RADIUS * 2.5;
    for (var i = 0; i < _portPositions.length; i++) {
      var p = _portPositions[i];
      if (typeFilter && p.type !== typeFilter) continue;
      var dx = x - p.x;
      var dy = y - p.y;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < bestDist) {
        bestDist = dist;
        best = p;
      }
    }
    return best;
  }

  // ─── Helper: hit-test a custom connection near (x,y) ─────────────────────

  function hitTestConnection(x, y) {
    var rack = _selectedRackId ? window._modelMap && window._modelMap.get(_selectedRackId) : null;
    if (!rack) return null;
    var equipments = Object.values((rack.constructions && rack.constructions.servers) || {});
    for (var ei = 0; ei < equipments.length; ei++) {
      var eq = equipments[ei];
      if (!eq.liquidCooling || !eq.liquidCooling.enabled) continue;
      var routing = eq.liquidCooling.routing;
      if (!routing || routing.type !== 'custom') continue;
      var connections = routing.connections || [];
      for (var ci = 0; ci < connections.length; ci++) {
        var conn = connections[ci];
        var fromPort = _portPositions.find(function(p) { return p.eqId === eq.uid && p.portId === conn.fromPort; });
        var toPort = _portPositions.find(function(p) { return p.eqId === eq.uid && p.portId === conn.toPort; });
        if (!fromPort || !toPort) continue;
        // Sample bezier at a few points to estimate proximity
        var dx = Math.abs(toPort.x - fromPort.x) * 0.5 + 20;
        var pts = 12;
        for (var t = 0; t <= pts; t++) {
          var u = t / pts;
          // Cubic bezier
          var bx = cubicBezier(fromPort.x, fromPort.x + dx, toPort.x - dx, toPort.x, u);
          var by = cubicBezier(fromPort.y, fromPort.y, toPort.y, toPort.y, u);
          var ddx = x - bx;
          var ddy = y - by;
          if (Math.sqrt(ddx * ddx + ddy * ddy) < 8) {
            return { eqId: eq.uid, fromPort: conn.fromPort, toPort: conn.toPort };
          }
        }
      }
    }
    return null;
  }

  function cubicBezier(p0, p1, p2, p3, t) {
    var mt = 1 - t;
    return mt * mt * mt * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t * t * t * p3;
  }

  // ─── Helper: point-to-line-segment distance ───────────────────────────────

  function distToSegment(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1;
    var dy = y2 - y1;
    var lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return Math.sqrt((px - x1) * (px - x1) + (py - y1) * (py - y1));
    var t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / lenSq));
    var projX = x1 + t * dx;
    var projY = y1 + t * dy;
    var ddx = px - projX;
    var ddy = py - projY;
    return Math.sqrt(ddx * ddx + ddy * ddy);
  }

  // ─── Create a connection with undo/log ────────────────────────────────────

  function createConnection(eqId, fromPortId, toPortId) {
    var rack = _selectedRackId ? window._modelMap && window._modelMap.get(_selectedRackId) : null;
    if (!rack) return;
    var equipments = Object.values((rack.constructions && rack.constructions.servers) || {});
    var eq = equipments.find(function(e) { return e.uid === eqId; });
    if (!eq || !eq.liquidCooling || !eq.liquidCooling.enabled) return;
    if (!eq.liquidCooling.routing || eq.liquidCooling.routing.type !== 'custom') return;

    // Prevent duplicate
    var existing = eq.liquidCooling.routing.connections || [];
    for (var i = 0; i < existing.length; i++) {
      if (existing[i].fromPort === fromPortId && existing[i].toPort === toPortId) return;
    }

    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('createConnection');

    if (!eq.liquidCooling.routing.connections) {
      eq.liquidCooling.routing.connections = [];
    }
    eq.liquidCooling.routing.connections.push({ fromPort: fromPortId, toPort: toPortId });

    render();

    if (typeof logOperation === 'function') {
      logOperation('createConnection', 'Add custom connection ' + fromPortId + ' → ' + toPortId, {
        rackName: rack.meta.name, eqId: eqId, fromPort: fromPortId, toPort: toPortId
      }, 'modify');
    }
  }

  // ─── Delete selected connection (exposed as window fn) ────────────────────

  window.deleteSelectedConnection = function() {
    if (!_selectedConnection) return;
    var rack = _selectedRackId ? window._modelMap && window._modelMap.get(_selectedRackId) : null;
    if (!rack) return;
    var equipments = Object.values((rack.constructions && rack.constructions.servers) || {});
    var eq = equipments.find(function(e) { return e.uid === _selectedConnection.eqId; });
    if (!eq || !eq.liquidCooling || !eq.liquidCooling.routing) return;

    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('deleteSelectedConnection');

    var conns = eq.liquidCooling.routing.connections || [];
    eq.liquidCooling.routing.connections = conns.filter(function(c) {
      return !(c.fromPort === _selectedConnection.fromPort && c.toPort === _selectedConnection.toPort);
    });

    var deleted = _selectedConnection;
    _selectedConnection = null;
    render();

    if (typeof logOperation === 'function') {
      logOperation('deleteSelectedConnection', 'Delete custom connection ' + deleted.fromPort + ' → ' + deleted.toPort, {
        rackName: rack.meta.name, eqId: deleted.eqId, fromPort: deleted.fromPort, toPort: deleted.toPort
      }, 'modify');
    }
  };

  // ─── Event handlers ───────────────────────────────────────────────────────

  function onCanvasMouseDown(e) {
    var pos = getCanvasPos(e);
    var x = pos.x;
    var y = pos.y;

    // Check if clicking an outlet port → start drag
    var outPort = hitTestPort(x, y, 'out');
    if (outPort) {
      _dragFrom = { eqId: outPort.eqId, portId: outPort.portId, x: outPort.x, y: outPort.y };
      _dragMouseX = x;
      _dragMouseY = y;
      _selectedConnection = null;
      render();
      return;
    }

    // Check if clicking a custom connection
    var conn = hitTestConnection(x, y);
    if (conn) {
      _selectedConnection = conn;
      render();
      return;
    }

    // Check toggle click
    var rack = _selectedRackId ? window._modelMap && window._modelMap.get(_selectedRackId) : null;
    if (rack) {
      var equipments = Object.values((rack.constructions && rack.constructions.servers) || {});
      for (var i = 0; i < equipments.length; i++) {
        var eq = equipments[i];
        var tr = eq._uiToggleRect;
        if (tr && x >= tr.x && x <= tr.x + tr.w && y >= tr.y && y <= tr.y + tr.h) {
          toggleEquipmentLC(rack, eq);
          return;
        }
      }
    }

    // Click on empty space — deselect connection
    _selectedConnection = null;
    render();
  }

  function onCanvasMouseUp(e) {
    if (!_dragFrom) return;

    var pos = getCanvasPos(e);
    var x = pos.x;
    var y = pos.y;

    // Check if releasing on an inlet port
    var inPort = hitTestPort(x, y, 'in');
    if (inPort && inPort.eqId === _dragFrom.eqId) {
      createConnection(_dragFrom.eqId, _dragFrom.portId, inPort.portId);
    }

    _dragFrom = null;
    render();
  }

  function onCanvasMouseMove(e) {
    if (_dragFrom) {
      var pos = getCanvasPos(e);
      _dragMouseX = pos.x;
      _dragMouseY = pos.y;
      render();
    }
  }

  function onKeyDown(e) {
    if (!_active) return;
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (_selectedConnection) {
        e.preventDefault();
        window.deleteSelectedConnection && window.deleteSelectedConnection();
      }
    }
  }

  // ─── Utility: round rect ──────────────────────────────────────────────────

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  /**
   * Toggle liquid cooling on/off for a rack equipment.
   */
  function toggleEquipmentLC(rack, eq) {
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('toggleEquipmentLC');

    if (!eq.liquidCooling) {
      eq.liquidCooling = {
        enabled: true,
        coldPlates: [
          {
            id: 'cp_cpu',
            label: 'CPU',
            specName: 'cp_default',
            chipTDP: 350,
            thermalModel: { chipArea: 0.004, chipThickness: 0.001, thermalConductivity: 148 },
            position: { x: 0.25, y: 0.5 }
          }
        ],
        routing: { type: 'series', connections: [] }
      };
    } else {
      eq.liquidCooling.enabled = !eq.liquidCooling.enabled;
    }

    render();
    if (typeof logOperation === 'function') {
      logOperation('toggleEquipmentLC', 'Toggle equipment liquid cooling', {
        rackName: rack.meta.name, eqName: (eq.meta && eq.meta.name), enabled: eq.liquidCooling.enabled
      }, 'modify');
    }
  }

  /**
   * Apply series, parallel, or custom routing preset to all LC-enabled equipment in current rack.
   */
  function applyRoutingPreset(preset) {
    var rack = _selectedRackId ? window._modelMap && window._modelMap.get(_selectedRackId) : null;
    if (!rack) return;

    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('applyRoutingPreset');

    var equipments = Object.values((rack.constructions && rack.constructions.servers) || {});
    equipments.forEach(function(eq) {
      if (eq.liquidCooling && eq.liquidCooling.enabled) {
        if (preset === 'custom') {
          eq.liquidCooling.routing = { type: 'custom', connections: [] };
        } else {
          eq.liquidCooling.routing = { type: preset, connections: [] };
        }
      }
    });

    render();
    if (typeof logOperation === 'function') {
      logOperation('applyRoutingPreset', 'Apply ' + preset + ' routing to ' + rack.meta.name, { preset: preset }, 'modify');
    }
  }

  // Expose
  window.activateEquipmentLevelView = activate;
  window.deactivateEquipmentLevelView = deactivate;
  window.isEquipmentLevelViewActive = function() { return _active; };

  console.log('[liquid_topology_editor.js] Equipment Level View loaded (Phase 6A drag-connect)');
})();
