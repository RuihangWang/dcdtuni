# 2D Floor Plan Editor (MVP) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A 2D canvas editor for drawing room floor plans that converts to Wall + Floor + Ceiling 3D models.

**Architecture:** New `web/room_2d_editor.js` file loaded by main HTML. Shares `state` object. Canvas 2D API for drawing. Mode switch hides 3D viewport, shows 2D canvas. "Apply" generates models from polygon vertices using existing `createWallModel`/`createFloorModel` factories.

**Tech Stack:** HTML5 Canvas 2D API, vanilla JS, shared `state` with STL_generate.html

**Spec:** `docs/superpowers/specs/2026-04-04-2d-floor-plan-editor-design.md`

---

### Task 1: HTML scaffolding + script loading + mode switch skeleton

**Files:**
- Create: `STLGeneratorForOpenFoam/web/room_2d_editor.js`
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add 2D editor HTML elements in STL_generate.html**

Find the `<div id="center-view"></div>` element (~line 545). AFTER it, add:

```html
<div id="room2d-container" style="display:none; position:absolute; top:0; left:0; right:0; bottom:0; background:#1a1e26;">
    <canvas id="room2d-canvas" style="width:100%; height:100%;"></canvas>
</div>
```

Find the top toolbar `<div id="toolbar">` (~line 547). AFTER the closing `</div>` of the toolbar, add:

```html
<div id="room2d-toolbar" style="display:none;" class="toolbar">
    <button class="tool-btn active" id="room2d-btn-draw" onclick="room2d.setTool('draw')">🖊 <span data-i18n="label.draw_mode">绘制</span></button>
    <button class="tool-btn" id="room2d-btn-select" onclick="room2d.setTool('select')">✋ <span data-i18n="label.select_mode">选择</span></button>
    <span style="width:1px; height:24px; background:var(--border); margin:0 8px;"></span>
    <button class="tool-btn" id="room2d-btn-gridsnap" onclick="room2d.toggleGridSnap()">📐 <span data-i18n="label.grid_snap">网格吸附</span></button>
    <button class="tool-btn" id="room2d-btn-vtxsnap" onclick="room2d.toggleVertexSnap()">⊞ <span data-i18n="label.vertex_snap">顶点吸附</span></button>
    <button class="tool-btn" id="room2d-btn-edgealign" onclick="room2d.toggleEdgeAlign()">═ <span data-i18n="label.edge_align">边对齐</span></button>
    <span style="width:1px; height:24px; background:var(--border); margin:0 8px;"></span>
    <label style="color:var(--text-dim); font-size:12px;">Grid:</label>
    <select id="room2d-grid-spacing" onchange="room2d.setGridSpacing(parseFloat(this.value))" style="background:var(--input-bg); color:var(--text); border:1px solid var(--border); font-size:12px; padding:2px;">
        <option value="0.05">0.05m</option>
        <option value="0.1" selected>0.1m</option>
        <option value="0.5">0.5m</option>
        <option value="1">1m</option>
    </select>
    <button class="tool-btn" style="margin-left:auto; color:var(--error-color);" onclick="room2d.clearAll()" data-i18n="label.clear_all">清除全部</button>
</div>
```

- [ ] **Step 2: Replace water-transfer button with 2D editor button**

Find line ~33:
```html
<button class="file-btn import" onclick="document.getElementById('file-stl').click()" data-i18n="btn.import_stl">🛸 导入水密曲面 STL 进行水转印</button>
```

Replace with:
```html
<button class="file-btn import" onclick="document.getElementById('file-stl').click()" data-i18n="btn.import_stl">🛸 导入水密曲面 STL 进行水转印</button>
<button class="file-btn" onclick="room2d.enterEditor()" data-i18n="btn.room_2d_editor" style="background:rgba(0,212,255,0.15); border-color:rgba(0,212,255,0.4); color:var(--accent3);">🏠 房间 2D 平面设计</button>
```

- [ ] **Step 3: Add script tag for room_2d_editor.js**

Find the `<script src="theme/theme-loader.js">` tag (~line 722). AFTER it, add:

```html
<script src="room_2d_editor.js"></script>
```

- [ ] **Step 4: Create room_2d_editor.js with mode switch skeleton**

Create `STLGeneratorForOpenFoam/web/room_2d_editor.js`:

```javascript
/**
 * room_2d_editor.js — 2D Floor Plan Editor for STL Generator
 * Draws room polygon outlines, converts to Wall + Floor + Ceiling models.
 * Loaded by STL_generate.html, shares state object.
 */
(function() {
    'use strict';

    const room2d = {};
    window.room2d = room2d;

    // --- State ---
    let _active = false;
    let _canvas = null;
    let _ctx = null;
    let _tool = 'draw';  // 'draw' | 'select'
    let _view = { offsetX: 0, offsetY: 0, scale: 40 }; // 40 px per meter
    let _vertices = [];   // [[x,y], ...] in world meters
    let _closed = false;
    let _selectedVertex = -1;
    let _selectedEdge = -1;
    let _dragVertex = -1;
    let _dragEdge = -1;
    let _dragStart = null;
    let _mouseWorld = { x: 0, y: 0 };
    let _gridSnap = true;
    let _vertexSnap = true;
    let _edgeAlign = true;
    let _gridSpacing = 0.1;
    let _height = 3.0;

    // --- Coordinate transforms ---
    function worldToCanvas(wx, wy) {
        return {
            x: (wx - _view.offsetX) * _view.scale + _canvas.width / 2,
            y: _canvas.height / 2 - (wy - _view.offsetY) * _view.scale
        };
    }
    function canvasToWorld(cx, cy) {
        return {
            x: (cx - _canvas.width / 2) / _view.scale + _view.offsetX,
            y: (_canvas.height / 2 - cy) / _view.scale + _view.offsetY
        };
    }

    // --- Enter / Exit ---
    room2d.enterEditor = function() {
        if (_active) return;
        _active = true;
        _canvas = document.getElementById('room2d-canvas');
        _ctx = _canvas.getContext('2d');

        // Load existing polygon if any
        if (state.roomPolygon && state.roomPolygon.vertices && state.roomPolygon.vertices.length >= 3) {
            _vertices = state.roomPolygon.vertices.map(v => [v[0], v[1]]);
            _closed = true;
            _height = state.roomPolygon.height || 3.0;
            // Auto-fit view to polygon
            let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
            _vertices.forEach(([x, y]) => { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; });
            _view.offsetX = (minX + maxX) / 2;
            _view.offsetY = (minY + maxY) / 2;
            const spanX = maxX - minX, spanY = maxY - minY;
            _view.scale = Math.min(_canvas.width / (spanX + 4), _canvas.height / (spanY + 4));
        } else {
            _vertices = [];
            _closed = false;
            _height = 3.0;
            _view = { offsetX: 0, offsetY: 0, scale: 40 };
        }
        _selectedVertex = -1;
        _selectedEdge = -1;
        _tool = 'draw';

        // Show 2D UI, hide 3D UI
        document.getElementById('room2d-container').style.display = 'block';
        document.getElementById('room2d-toolbar').style.display = 'flex';
        document.getElementById('center-view').style.display = 'none';
        document.getElementById('toolbar').style.display = 'none';

        // Update right panel
        _updateRightPanel();
        _resizeCanvas();
        _bindEvents();
        _render();

        if (window.logOperation) logOperation('room2d.enter', 'Enter 2D floor plan editor', {}, 'view');
    };

    room2d.exitEditor = function(apply) {
        if (!_active) return;
        _unbindEvents();
        _active = false;

        document.getElementById('room2d-container').style.display = 'none';
        document.getElementById('room2d-toolbar').style.display = 'none';
        document.getElementById('center-view').style.display = '';
        document.getElementById('toolbar').style.display = '';

        if (apply && _closed && _vertices.length >= 3) {
            if (window.pushUndoCheckpoint) pushUndoCheckpoint('applyRoom2D');
            state.roomPolygon = {
                vertices: _vertices.map(v => [v[0], v[1]]),
                height: _height
            };
            _generateRoomModels();
            if (window.rebuildGeometry) rebuildGeometry();
            if (window.fitCameraToScene) fitCameraToScene();
            if (window.logOperation) logOperation('room2d.apply', 'Applied 2D floor plan to 3D', { vertices: _vertices.length, height: _height }, 'modify');
        }

        // Restore right panel to normal inspector
        if (window.updateUI) updateUI();
    };

    // --- Model Generation ---
    function _generateRoomModels() {
        // Remove existing room boundary models
        state.models = state.models.filter(m => !m.isRoomBoundary);
        if (window._modelMap) {
            state.models.forEach(m => _modelMap.set(m.id, m));
            // Clean stale entries
            const validIds = new Set(state.models.map(m => m.id));
            for (const [id] of _modelMap) { if (!validIds.has(id)) _modelMap.delete(id); }
        }

        const n = _vertices.length;
        const h = _height;

        // Generate walls
        for (let i = 0; i < n; i++) {
            const a = _vertices[i];
            const b = _vertices[(i + 1) % n];
            const dx = b[0] - a[0], dy = b[1] - a[1];
            const length = Math.hypot(dx, dy);
            if (length < 1e-6) continue;
            const angle = Math.atan2(dy, dx);

            const wall = window.createWallModel ? createWallModel('Room_Wall_' + (i + 1)) : createModelFromRaw({ kind: 'wall', name: 'Room_Wall_' + (i + 1) });
            wall.isRoomBoundary = true;
            wall.anchor = { x: a[0], y: a[1], z: 0 };
            wall.base.W = length;
            wall.base.D = 0;
            wall.base.H = h;
            wall.transform = cloneModelTransform({
                quaternion: { x: 0, y: 0, z: Math.sin(angle / 2), w: Math.cos(angle / 2) },
                scale: { x: 1, y: 1, z: 1 }
            });
            state.models.push(wall);
            if (window._modelMap) _modelMap.set(wall.id, wall);
        }

        // Generate floor
        const floor = window.createFloorModel ? createFloorModel('Room_Floor') : createModelFromRaw({ kind: 'floor', name: 'Room_Floor' });
        floor.isRoomBoundary = true;
        floor.anchor = { x: 0, y: 0, z: 0 };
        floor.polygon = _vertices.map(v => [v[0], v[1]]);
        if (window.syncFloorBaseFromPolygon) syncFloorBaseFromPolygon(floor);
        state.models.push(floor);
        if (window._modelMap) _modelMap.set(floor.id, floor);

        // Generate ceiling
        const ceiling = window.createFloorModel ? createFloorModel('Room_Ceiling') : createModelFromRaw({ kind: 'floor', name: 'Room_Ceiling' });
        ceiling.isRoomBoundary = true;
        ceiling.anchor = { x: 0, y: 0, z: h };
        ceiling.polygon = _vertices.map(v => [v[0], v[1]]);
        if (window.syncFloorBaseFromPolygon) syncFloorBaseFromPolygon(ceiling);
        state.models.push(ceiling);
        if (window._modelMap) _modelMap.set(ceiling.id, ceiling);

        // Rebuild group map
        if (window._rebuildModelGroupMap) _rebuildModelGroupMap();
    }

    // --- Canvas Rendering ---
    function _resizeCanvas() {
        if (!_canvas) return;
        const container = _canvas.parentElement;
        _canvas.width = container.clientWidth;
        _canvas.height = container.clientHeight;
    }

    function _render() {
        if (!_active || !_ctx) return;
        const ctx = _ctx;
        const W = _canvas.width, H = _canvas.height;
        ctx.clearRect(0, 0, W, H);

        // Background
        ctx.fillStyle = '#1a1e26';
        ctx.fillRect(0, 0, W, H);

        // Grid
        _drawGrid(ctx);

        // Axes
        _drawAxes(ctx);

        // Polygon edges
        if (_vertices.length > 0) {
            ctx.strokeStyle = '#00d4ff';
            ctx.lineWidth = 2;
            ctx.beginPath();
            const p0 = worldToCanvas(_vertices[0][0], _vertices[0][1]);
            ctx.moveTo(p0.x, p0.y);
            for (let i = 1; i < _vertices.length; i++) {
                const p = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                ctx.lineTo(p.x, p.y);
            }
            if (_closed) ctx.closePath();
            ctx.stroke();

            // Preview line (drawing mode, not closed)
            if (!_closed && _tool === 'draw' && _vertices.length > 0) {
                const last = worldToCanvas(_vertices[_vertices.length - 1][0], _vertices[_vertices.length - 1][1]);
                const mouse = worldToCanvas(_mouseWorld.x, _mouseWorld.y);
                ctx.setLineDash([5, 5]);
                ctx.beginPath();
                ctx.moveTo(last.x, last.y);
                ctx.lineTo(mouse.x, mouse.y);
                ctx.stroke();
                ctx.setLineDash([]);
            }

            // Vertices
            for (let i = 0; i < _vertices.length; i++) {
                const p = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                ctx.beginPath();
                ctx.arc(p.x, p.y, 6, 0, Math.PI * 2);
                ctx.fillStyle = i === _selectedVertex ? '#ffffff' : '#00d4ff';
                ctx.fill();
                ctx.strokeStyle = '#00d4ff';
                ctx.lineWidth = 2;
                ctx.stroke();
            }

            // Edge highlight
            if (_selectedEdge >= 0 && _selectedEdge < _vertices.length) {
                const a = worldToCanvas(_vertices[_selectedEdge][0], _vertices[_selectedEdge][1]);
                const b = worldToCanvas(_vertices[(_selectedEdge + 1) % _vertices.length][0], _vertices[(_selectedEdge + 1) % _vertices.length][1]);
                ctx.strokeStyle = '#ffcc00';
                ctx.lineWidth = 3;
                ctx.beginPath();
                ctx.moveTo(a.x, a.y);
                ctx.lineTo(b.x, b.y);
                ctx.stroke();
            }

            // Close indicator
            if (!_closed && _tool === 'draw' && _vertices.length >= 3) {
                const p0c = worldToCanvas(_vertices[0][0], _vertices[0][1]);
                const mouse = worldToCanvas(_mouseWorld.x, _mouseWorld.y);
                const dist = Math.hypot(p0c.x - mouse.x, p0c.y - mouse.y);
                if (dist < 15) {
                    ctx.beginPath();
                    ctx.arc(p0c.x, p0c.y, 12, 0, Math.PI * 2);
                    ctx.strokeStyle = '#00ff88';
                    ctx.lineWidth = 2;
                    ctx.stroke();
                }
            }
        }

        // Dimension labels on edges
        if (_closed) {
            ctx.font = '11px Share Tech Mono, monospace';
            ctx.fillStyle = '#8be9fd';
            for (let i = 0; i < _vertices.length; i++) {
                const a = _vertices[i], b = _vertices[(i + 1) % _vertices.length];
                const mid = worldToCanvas((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
                const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
                ctx.fillText(len.toFixed(2) + 'm', mid.x + 5, mid.y - 5);
            }
        }
    }

    function _drawGrid(ctx) {
        const topLeft = canvasToWorld(0, 0);
        const bottomRight = canvasToWorld(_canvas.width, _canvas.height);
        const minX = Math.floor(topLeft.x / _gridSpacing) * _gridSpacing;
        const maxX = Math.ceil(bottomRight.x / _gridSpacing) * _gridSpacing;
        const minY = Math.floor(bottomRight.y / _gridSpacing) * _gridSpacing;
        const maxY = Math.ceil(topLeft.y / _gridSpacing) * _gridSpacing;

        ctx.strokeStyle = '#2a2e38';
        ctx.lineWidth = 0.5;
        for (let x = minX; x <= maxX; x += _gridSpacing) {
            const p = worldToCanvas(x, 0);
            ctx.beginPath();
            ctx.moveTo(p.x, 0);
            ctx.lineTo(p.x, _canvas.height);
            ctx.stroke();
        }
        for (let y = minY; y <= maxY; y += _gridSpacing) {
            const p = worldToCanvas(0, y);
            ctx.beginPath();
            ctx.moveTo(0, p.y);
            ctx.lineTo(_canvas.width, p.y);
            ctx.stroke();
        }
    }

    function _drawAxes(ctx) {
        const origin = worldToCanvas(0, 0);
        // X axis (red)
        ctx.strokeStyle = '#e06c75';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(origin.x, origin.y);
        ctx.lineTo(_canvas.width, origin.y);
        ctx.stroke();
        ctx.fillStyle = '#e06c75';
        ctx.font = '12px Share Tech Mono';
        ctx.fillText('X', _canvas.width - 15, origin.y - 5);
        // Y axis (green)
        ctx.strokeStyle = '#98c379';
        ctx.beginPath();
        ctx.moveTo(origin.x, origin.y);
        ctx.lineTo(origin.x, 0);
        ctx.stroke();
        ctx.fillStyle = '#98c379';
        ctx.fillText('Y', origin.x + 5, 15);
    }

    // --- Snap ---
    function _applySnap(wx, wy) {
        let x = wx, y = wy;
        if (_gridSnap) {
            x = Math.round(x / _gridSpacing) * _gridSpacing;
            y = Math.round(y / _gridSpacing) * _gridSpacing;
        }
        if (_vertexSnap && _vertices.length > 0) {
            const snapRadius = 10 / _view.scale; // 10 pixels in world units
            let bestDist = snapRadius;
            _vertices.forEach(([vx, vy], i) => {
                if (i === _dragVertex) return; // don't snap to self
                const d = Math.hypot(x - vx, y - vy);
                if (d < bestDist) { bestDist = d; x = vx; y = vy; }
            });
        }
        if (_edgeAlign && _vertices.length > 0 && _dragVertex < 0) {
            // Constrain to horizontal or vertical if close to prev vertex
            const prev = _vertices[_vertices.length - 1];
            if (prev) {
                const dx = Math.abs(x - prev[0]), dy = Math.abs(y - prev[1]);
                const threshold = 5 / _view.scale;
                if (dx < threshold && dy > threshold) x = prev[0]; // vertical
                if (dy < threshold && dx > threshold) y = prev[1]; // horizontal
            }
        }
        return { x, y };
    }

    // --- Event Handling ---
    let _boundHandlers = {};

    function _bindEvents() {
        const c = _canvas;
        _boundHandlers.mousedown = _onMouseDown.bind(null);
        _boundHandlers.mousemove = _onMouseMove.bind(null);
        _boundHandlers.mouseup = _onMouseUp.bind(null);
        _boundHandlers.wheel = _onWheel.bind(null);
        _boundHandlers.contextmenu = e => e.preventDefault();
        _boundHandlers.keydown = _onKeyDown.bind(null);
        _boundHandlers.resize = () => { _resizeCanvas(); _render(); };
        c.addEventListener('mousedown', _boundHandlers.mousedown);
        c.addEventListener('mousemove', _boundHandlers.mousemove);
        c.addEventListener('mouseup', _boundHandlers.mouseup);
        c.addEventListener('wheel', _boundHandlers.wheel);
        c.addEventListener('contextmenu', _boundHandlers.contextmenu);
        document.addEventListener('keydown', _boundHandlers.keydown);
        window.addEventListener('resize', _boundHandlers.resize);
    }

    function _unbindEvents() {
        const c = _canvas;
        if (!c) return;
        c.removeEventListener('mousedown', _boundHandlers.mousedown);
        c.removeEventListener('mousemove', _boundHandlers.mousemove);
        c.removeEventListener('mouseup', _boundHandlers.mouseup);
        c.removeEventListener('wheel', _boundHandlers.wheel);
        c.removeEventListener('contextmenu', _boundHandlers.contextmenu);
        document.removeEventListener('keydown', _boundHandlers.keydown);
        window.removeEventListener('resize', _boundHandlers.resize);
    }

    let _isPanning = false;
    let _panStart = null;

    function _onMouseDown(e) {
        const rect = _canvas.getBoundingClientRect();
        const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
        const w = canvasToWorld(cx, cy);

        // Middle or right button: pan
        if (e.button === 1 || e.button === 2) {
            _isPanning = true;
            _panStart = { x: e.clientX, y: e.clientY, ox: _view.offsetX, oy: _view.offsetY };
            return;
        }

        if (_tool === 'draw' && !_closed) {
            const snapped = _applySnap(w.x, w.y);
            // Check close
            if (_vertices.length >= 3) {
                const p0 = worldToCanvas(_vertices[0][0], _vertices[0][1]);
                const pc = worldToCanvas(snapped.x, snapped.y);
                if (Math.hypot(p0.x - pc.x, p0.y - pc.y) < 15) {
                    _closed = true;
                    _tool = 'select';
                    _updateToolButtons();
                    _updateRightPanel();
                    _render();
                    return;
                }
            }
            _vertices.push([snapped.x, snapped.y]);
            _render();
            _updateRightPanel();
            return;
        }

        if (_tool === 'select' && _closed) {
            // Hit test vertices
            for (let i = 0; i < _vertices.length; i++) {
                const p = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                if (Math.hypot(cx - p.x, cy - p.y) < 10) {
                    _selectedVertex = i;
                    _selectedEdge = -1;
                    _dragVertex = i;
                    _dragStart = { x: _vertices[i][0], y: _vertices[i][1] };
                    _updateRightPanel();
                    _render();
                    return;
                }
            }
            // Hit test edges
            for (let i = 0; i < _vertices.length; i++) {
                const a = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                const b = worldToCanvas(_vertices[(i + 1) % _vertices.length][0], _vertices[(i + 1) % _vertices.length][1]);
                const dist = _pointToSegmentDist(cx, cy, a.x, a.y, b.x, b.y);
                if (dist < 8) {
                    _selectedEdge = i;
                    _selectedVertex = -1;
                    _dragEdge = i;
                    _dragStart = { x: w.x, y: w.y };
                    _updateRightPanel();
                    _render();
                    return;
                }
            }
            // Deselect
            _selectedVertex = -1;
            _selectedEdge = -1;
            _updateRightPanel();
            _render();
        }
    }

    function _onMouseMove(e) {
        const rect = _canvas.getBoundingClientRect();
        const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
        const w = canvasToWorld(cx, cy);
        _mouseWorld = w;

        if (_isPanning && _panStart) {
            const dx = (e.clientX - _panStart.x) / _view.scale;
            const dy = -(e.clientY - _panStart.y) / _view.scale;
            _view.offsetX = _panStart.ox - dx;
            _view.offsetY = _panStart.oy - dy;
            _render();
            return;
        }

        if (_dragVertex >= 0) {
            const snapped = _applySnap(w.x, w.y);
            _vertices[_dragVertex] = [snapped.x, snapped.y];
            _render();
            _updateRightPanel();
            return;
        }

        if (_dragEdge >= 0 && _dragStart) {
            const dx = w.x - _dragStart.x, dy = w.y - _dragStart.y;
            const i = _dragEdge, j = (i + 1) % _vertices.length;
            _vertices[i] = [_vertices[i][0] + dx, _vertices[i][1] + dy];
            _vertices[j] = [_vertices[j][0] + dx, _vertices[j][1] + dy];
            _dragStart = { x: w.x, y: w.y };
            _render();
            _updateRightPanel();
            return;
        }

        // Preview render for drawing mode
        if (_tool === 'draw' && !_closed) _render();
    }

    function _onMouseUp(e) {
        _isPanning = false;
        _panStart = null;
        _dragVertex = -1;
        _dragEdge = -1;
        _dragStart = null;
    }

    function _onWheel(e) {
        e.preventDefault();
        const rect = _canvas.getBoundingClientRect();
        const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
        const wBefore = canvasToWorld(cx, cy);
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        _view.scale = Math.max(2, Math.min(2000, _view.scale * factor));
        const wAfter = canvasToWorld(cx, cy);
        _view.offsetX += wBefore.x - wAfter.x;
        _view.offsetY += wBefore.y - wAfter.y;
        _render();
    }

    function _onKeyDown(e) {
        if (!_active) return;
        if (e.key === 'Enter' && !_closed && _vertices.length >= 3) {
            _closed = true;
            _tool = 'select';
            _updateToolButtons();
            _updateRightPanel();
            _render();
        }
        if (e.key === 'Escape') {
            if (!_closed) {
                _vertices = [];
                _render();
                _updateRightPanel();
            }
        }
        if ((e.key === 'Delete' || e.key === 'Backspace') && _selectedVertex >= 0 && _closed) {
            _vertices.splice(_selectedVertex, 1);
            _selectedVertex = -1;
            if (_vertices.length < 3) { _closed = false; _vertices = []; _tool = 'draw'; _updateToolButtons(); }
            _render();
            _updateRightPanel();
        }
    }

    // --- Helpers ---
    function _pointToSegmentDist(px, py, ax, ay, bx, by) {
        const dx = bx - ax, dy = by - ay;
        const lenSq = dx * dx + dy * dy;
        if (lenSq < 1e-10) return Math.hypot(px - ax, py - ay);
        let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
        t = Math.max(0, Math.min(1, t));
        return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
    }

    // --- Right Panel ---
    function _updateRightPanel() {
        const prop = document.getElementById('prop-content');
        const title = document.getElementById('prop-title');
        if (!prop || !title) return;
        title.textContent = '🏠 ' + (window.t ? t('btn.room_2d_editor') : 'Room 2D Editor');

        let html = '';
        // Room height
        html += '<div class="prop-group"><div class="prop-title" style="color:var(--accent3)">Room Settings</div>';
        html += '<div class="prop-row"><label>' + (window.t ? t('label.room_height') : 'Height') + ' (m)</label><input type="number" step="0.1" min="0.1" value="' + _height.toFixed(2) + '" onchange="room2d.setHeight(parseFloat(this.value))"></div>';
        html += '</div>';

        // Selected vertex
        if (_selectedVertex >= 0 && _selectedVertex < _vertices.length) {
            const v = _vertices[_selectedVertex];
            html += '<div class="prop-group"><div class="prop-title">Selected Vertex #' + (_selectedVertex + 1) + '</div>';
            html += '<div class="prop-row"><label>X (m)</label><input type="number" step="0.01" value="' + v[0].toFixed(3) + '" onchange="room2d.setVertexCoord(' + _selectedVertex + ', 0, parseFloat(this.value))"></div>';
            html += '<div class="prop-row"><label>Y (m)</label><input type="number" step="0.01" value="' + v[1].toFixed(3) + '" onchange="room2d.setVertexCoord(' + _selectedVertex + ', 1, parseFloat(this.value))"></div>';
            html += '</div>';
        }

        // Selected edge
        if (_selectedEdge >= 0 && _selectedEdge < _vertices.length && _closed) {
            const a = _vertices[_selectedEdge], b = _vertices[(_selectedEdge + 1) % _vertices.length];
            const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
            const angle = Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
            html += '<div class="prop-group"><div class="prop-title">Selected Edge #' + (_selectedEdge + 1) + '</div>';
            html += '<div class="prop-row"><label>Length</label><span>' + len.toFixed(3) + ' m</span></div>';
            html += '<div class="prop-row"><label>Angle</label><span>' + angle.toFixed(1) + '°</span></div>';
            html += '</div>';
        }

        // Polygon info
        if (_vertices.length >= 3 && _closed) {
            const perimeter = _vertices.reduce((s, v, i) => {
                const w = _vertices[(i + 1) % _vertices.length];
                return s + Math.hypot(w[0] - v[0], w[1] - v[1]);
            }, 0);
            const area = window.computePolygonArea ? computePolygonArea(_vertices) : 0;
            html += '<div class="prop-group"><div class="prop-title">Polygon Info</div>';
            html += '<div class="prop-row"><label>Vertices</label><span>' + _vertices.length + '</span></div>';
            html += '<div class="prop-row"><label>Perimeter</label><span>' + perimeter.toFixed(2) + ' m</span></div>';
            html += '<div class="prop-row"><label>Area</label><span>' + area.toFixed(2) + ' m²</span></div>';
            html += '</div>';
        }

        // Apply / Cancel buttons
        html += '<div style="margin-top:16px; display:flex; flex-direction:column; gap:8px;">';
        html += '<button class="btn primary" onclick="room2d.exitEditor(true)" style="background:rgba(0,212,255,0.2); border-color:var(--accent3); color:var(--accent3);">' + (window.t ? t('btn.apply_to_3d') : '✅ Apply to 3D') + '</button>';
        html += '<button class="btn" onclick="room2d.exitEditor(false)">' + (window.t ? t('btn.cancel_2d') : '❌ Cancel') + '</button>';
        html += '</div>';

        prop.innerHTML = html;
    }

    // --- Public API ---
    room2d.setTool = function(tool) {
        _tool = tool;
        _updateToolButtons();
    };
    room2d.toggleGridSnap = function() {
        _gridSnap = !_gridSnap;
        _updateToolButtons();
    };
    room2d.toggleVertexSnap = function() {
        _vertexSnap = !_vertexSnap;
        _updateToolButtons();
    };
    room2d.toggleEdgeAlign = function() {
        _edgeAlign = !_edgeAlign;
        _updateToolButtons();
    };
    room2d.setGridSpacing = function(v) {
        _gridSpacing = Math.max(0.01, v);
        _render();
    };
    room2d.setHeight = function(v) {
        if (Number.isFinite(v) && v > 0) _height = v;
    };
    room2d.setVertexCoord = function(idx, axis, value) {
        if (idx >= 0 && idx < _vertices.length && Number.isFinite(value)) {
            _vertices[idx][axis] = value;
            _render();
            _updateRightPanel();
        }
    };
    room2d.clearAll = function() {
        _vertices = [];
        _closed = false;
        _selectedVertex = -1;
        _selectedEdge = -1;
        _tool = 'draw';
        _updateToolButtons();
        _render();
        _updateRightPanel();
    };

    function _updateToolButtons() {
        const setActive = (id, active) => {
            const btn = document.getElementById(id);
            if (btn) btn.classList.toggle('active', active);
        };
        setActive('room2d-btn-draw', _tool === 'draw');
        setActive('room2d-btn-select', _tool === 'select');
        setActive('room2d-btn-gridsnap', _gridSnap);
        setActive('room2d-btn-vtxsnap', _vertexSnap);
        setActive('room2d-btn-edgealign', _edgeAlign);
    }

})();
```

- [ ] **Step 5: Add `isRoomBoundary` preservation in `createModelFromRaw` in STL_generate.html**

Find `function createModelFromRaw` in STL_generate.html. In the model object literal, after the `polygon:` line, add:

```javascript
            isRoomBoundary: !!rawModel.isRoomBoundary,
```

- [ ] **Step 6: Add `roomPolygon` validation in `normalizeState`**

Find the "Floor polygon validation" block in `normalizeState`. AFTER it, add:

```javascript
        // Room polygon validation
        if (next.roomPolygon != null) {
            if (!next.roomPolygon.vertices || !Array.isArray(next.roomPolygon.vertices)
                || next.roomPolygon.vertices.length < 3
                || !next.roomPolygon.vertices.every(v => Array.isArray(v) && v.length >= 2
                    && Number.isFinite(v[0]) && Number.isFinite(v[1]))
                || !Number.isFinite(next.roomPolygon.height) || next.roomPolygon.height <= 0) {
                next.roomPolygon = null;
            }
        }
```

- [ ] **Step 7: Add i18n keys**

Add to `web/i18n/zh-CN.json`:
```json
  "btn.room_2d_editor": "🏠 房间 2D 平面设计",
  "btn.apply_to_3d": "✅ 应用转换为 3D 房间",
  "btn.cancel_2d": "❌ 取消",
  "label.room_height": "房间高度",
  "label.draw_mode": "绘制",
  "label.select_mode": "选择",
  "label.grid_snap": "网格吸附",
  "label.vertex_snap": "顶点吸附",
  "label.edge_align": "边对齐",
  "label.clear_all": "清除全部",
```

Add to `web/i18n/en-US.json`:
```json
  "btn.room_2d_editor": "🏠 Room 2D Floor Plan",
  "btn.apply_to_3d": "✅ Apply to 3D Room",
  "btn.cancel_2d": "❌ Cancel",
  "label.room_height": "Room Height",
  "label.draw_mode": "Draw",
  "label.select_mode": "Select",
  "label.grid_snap": "Grid Snap",
  "label.vertex_snap": "Vertex Snap",
  "label.edge_align": "Edge Align",
  "label.clear_all": "Clear All",
```

- [ ] **Step 8: Commit**

```bash
git add STLGeneratorForOpenFoam/web/room_2d_editor.js STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat(room2d): 2D floor plan editor with polygon drawing + 3D generation

New file web/room_2d_editor.js: Canvas 2D editor for drawing room outlines.
- Draw mode: click vertices, close polygon by clicking first vertex or Enter
- Select mode: drag vertices/edges, delete vertices, coordinate input
- Grid snap, vertex snap, edge alignment toggles
- Apply: generates Wall (rotated) + Floor (polygon) + Ceiling per edge/face
- Cancel: exits without changes
- Loads existing state.roomPolygon on re-entry
- i18n keys for zh-CN and en-US

Integration in STL_generate.html:
- Entry button replaces water-transfer-printing button area
- 2D canvas overlays 3D viewport when active
- isRoomBoundary flag on generated models for re-apply cleanup
- roomPolygon validation in normalizeState"
```

---

### Task 2: Manual testing

- [ ] **Step 1: Start server and test**

```bash
fuser -k 8005/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir dctwin/case &
```

- [ ] **Step 2: Test basic drawing flow**
1. Click "🏠 房间 2D 平面设计" → verify 2D canvas appears, 3D hidden
2. Click 4 points to draw rectangle → close by clicking first vertex
3. Click "✅ 应用转换为 3D 房间" → verify 4 walls + floor + ceiling generated
4. Walls correctly rotated at each edge angle

- [ ] **Step 3: Test triangle room**
1. Re-enter 2D → existing rectangle should load
2. Clear all → draw triangle (3 points)
3. Apply → verify 3 walls + triangular floor + ceiling

- [ ] **Step 4: Test L-shape (concave)**
1. Draw L-shape (6 points) → apply → verify 6 walls + L-shaped floor

- [ ] **Step 5: Test editing**
1. Enter 2D → drag a vertex → verify polygon updates
2. Select edge → verify length/angle shown in right panel
3. Delete vertex → verify polygon reconnects
4. Set vertex coords in right panel → verify canvas updates

- [ ] **Step 6: Test persistence**
1. Apply room → save project → reload → verify roomPolygon + isRoomBoundary preserved
2. Re-enter 2D editor → verify polygon loaded

- [ ] **Step 7: Test undo**
1. Apply room → Ctrl+Z → verify room models removed

- [ ] **Step 8: Test cancel**
1. Enter 2D → draw something → cancel → verify no changes to 3D
