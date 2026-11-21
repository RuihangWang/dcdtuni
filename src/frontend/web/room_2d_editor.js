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
    let _viewRotation = 0; // 0, 90, 180, 270 degrees
    let _showAxes = true;
    let _bgColor = '#1a1e26';
    let _showAnnotations = false;
    let _lastAisleLabels = [];
    let _vertices = [];   // [[x,y], ...] in world meters
    let _closed = false;
    let _selectedVertex = -1;
    let _selectedEdge = -1;
    let _dragVertex = -1;
    let _dragEdge = -1;
    let _dragStart = null;
    let _mouseWorld = { x: 0, y: 0 };
    let _coordTooltip = null;  // DOM overlay for coordinate preview in draw mode
    let _gridSnap = true;
    let _vertexSnap = false;  // mutually exclusive with gridSnap
    let _edgeAlign = false;   // mutually exclusive with gridSnap
    let _gridSpacing = 0.1;
    let _height = 3.0;
    const _DRAGGABLE_KINDS = new Set(['rack', 'acu', 'box']);
    let _boxSelectStart = null;  // {x, y} canvas coords — start of box selection
    let _boxSelectEnd = null;    // {x, y} canvas coords — end of box selection
    let _boxSelectedVertices = new Set();  // indices of box-selected vertices
    let _boxSelectedEdges = new Set();    // indices of box-selected edges
    let _dragModel = null;  // { id, startX, startY, anchorStartX, anchorStartY }
    let _selectedModelId = null;  // ID of model selected in 2D for visual highlight
    let _dragModels = null;   // [{id, anchorStartX, anchorStartY}] for multi-model drag
    let _dragStart2d = null;  // {x, y} world coords at drag start

    function _isModel2dSelected(modelId) {
        return window.uiState.selection && window.uiState.selection.type === 'model' &&
               Array.isArray(window.uiState.selection.modelIds) &&
               window.uiState.selection.modelIds.indexOf(modelId) >= 0;
    }

    let _rotateHandle = null;  // { id, centroidX, centroidY, startAngle, currentAngle, startQuat, startAnchorX, startAnchorY, dragStartAngle }
    let _rotateHoverModelId = null;  // model ID when mouse hovers near rotation ring
    let _rotationSnap = false;

    // Compute rotation ring geometry for a model in canvas pixel coordinates.
    // Used by BOTH drawing and hit-testing to ensure exact alignment.
    function _getRotateRingParams(m) {
        if (!m.rotateTool || !m.rotateTool.enabled || !m.rotateTool.axes || !m.rotateTool.axes.yaw) return null;
        const obb = window.getModelOBB ? getModelOBB(m) : null;
        if (!obb) return null;
        // 2026-04-18 Y-up: plan canvas Y = world Z (center.z).
        const centroid = worldToCanvas(obb.center.x, obb.center.z);
        const tl = worldToCanvas(obb.center.x - obb.half[0], obb.center.z + obb.half[2]);
        const br = worldToCanvas(obb.center.x + obb.half[0], obb.center.z - obb.half[2]);
        const diagPx = Math.hypot(br.x - tl.x, br.y - tl.y);
        const radius = Math.max(25, diagPx * 0.6);
        const q = (m.geometry && m.geometry.quaternion) || {x:0,y:0,z:0,w:1};
        // Same `w || 1` pitfall as the render path — w===0 (180°) must NOT fallback to 1.
        const _qw = (q.w === undefined || q.w === null) ? 1 : q.w;
        // 2026-04-18 Y-up: yaw (rotation in XZ plan) is around world-Y axis, not Z.
        const angle = 2 * Math.atan2(q.y || 0, _qw);
        // 2026-04-19 Y-up DIRECTION FIX: handle must track the rack's visible
        // orientation in canvas. `worldToCanvas` flips world-Z to canvas-Y,
        // so R_y(+ω)·(1,0,0) = (cos ω, 0, -sin ω) appears at canvas offset
        // (cos ω, +sin ω) — i.e. rack rotates CLOCKWISE visually as ω grows.
        // Prior code used cos(-angle)/sin(-angle), which placed the handle
        // going CCW while the rack went CW, so dragging the dot moved the
        // rack in the opposite direction.
        const hx = centroid.x + radius * Math.cos(angle);
        const hy = centroid.y + radius * Math.sin(angle);
        return { centroid, radius, angle, hx, hy };
    }

    // --- Unit display helpers (sync with project unit from STL_generate.html) ---
    function _fmt(value) {
        // Format length value using project unit system
        return window.formatLength ? formatLength(value) : value.toFixed(3);
    }
    function _unitLabel() {
        return window.getLengthUnitLabel ? getLengthUnitLabel() : 'm';
    }
    function _fmtArea(value) {
        if (window.getActiveUnitConfig) {
            const cfg = getActiveUnitConfig();
            return (value / (cfg.scale * cfg.scale)).toFixed(cfg.precision);
        }
        return value.toFixed(2);
    }

    // --- Coordinate transforms (rotation-aware) ---
    function _rotatePoint(wx, wy, angleDeg) {
        if (angleDeg === 0) return { x: wx, y: wy };
        const rad = angleDeg * Math.PI / 180;
        const c = Math.cos(rad), s = Math.sin(rad);
        return { x: c * wx - s * wy, y: s * wx + c * wy };
    }
    function _unrotatePoint(rx, ry, angleDeg) {
        return _rotatePoint(rx, ry, -angleDeg);
    }
    function worldToCanvas(wx, wy) {
        const r = _rotatePoint(wx, wy, _viewRotation);
        return {
            x: (r.x - _view.offsetX) * _view.scale + _canvas.width / 2,
            y: _canvas.height / 2 - (r.y - _view.offsetY) * _view.scale
        };
    }
    function canvasToWorld(cx, cy) {
        const rx = (cx - _canvas.width / 2) / _view.scale + _view.offsetX;
        const ry = (_canvas.height / 2 - cy) / _view.scale + _view.offsetY;
        return _unrotatePoint(rx, ry, _viewRotation);
    }

    function _getExplicitRoomCeilingHeight() {
        const models = (state && window.cfdprojStateV4) ? (window.cfdprojStateV4.allModels(state) || []) : [];
        const heights = [];
        models.forEach(function(m) {
            const loc = m && m.geometry && m.geometry.location;
            if (!m || m.kind !== 'floor' || !loc || !Number.isFinite(loc.y)) return;
            const cool = m.cooling || {};
            if (cool.roomBoundaryLocation === 'top') heights.push(loc.y);
        });
        return heights.length ? Math.max.apply(null, heights) : null;
    }

    function _syncRoomPolygonHeightFromExplicitCeiling() {
        const h = _getExplicitRoomCeilingHeight();
        if (!(h > 0) || !state.geometry.plane) return;
        state.geometry.planeHeight = h;
    }

    // --- Enter / Exit ---
    room2d.enterEditor = function() {
        if (_active) return;
        _active = true;
        _canvas = document.getElementById('room2d-canvas');
        _ctx = _canvas.getContext('2d');

        // The 2D editor writes its controls into the shared #prop-content / #prop-title.
        // On the mesh/solver/postprocess tabs those elements are display:none (their tab
        // panels occupy the right column), so entering the editor from one of those tabs
        // leaves the right panel blank. Force the Geometry & Properties tab so the editor's
        // panel is visible.
        if (window.uiState && window.uiState.session && window.uiState.session.activeTab !== 'geometry'
            && typeof window.switchLeftTab === 'function') {
            window.switchLeftTab('geometry');
        }

        // Load existing polygon if any
        if (state.geometry.plane && state.geometry.plane.length >= 3) {
            _syncRoomPolygonHeightFromExplicitCeiling();
            _vertices = state.geometry.plane.map(v => [v[0], v[1]]);
            _closed = true;
            _height = (state.geometry.planeHeight != null ? state.geometry.planeHeight : state.geometry.height) || 3.0;
            _viewRotation = 0;
            // View fit is deferred to after _resizeCanvas
        } else {
            _vertices = [];
            _closed = false;
            _height = 3.0;
            _view = { offsetX: 0, offsetY: 0, scale: 40 };
            _viewRotation = 0;
        }
        _selectedVertex = -1;
        _selectedEdge = -1;
        _tool = _closed ? 'select' : 'draw';

        // Show 2D UI, hide 3D UI
        document.getElementById('room2d-container').style.display = 'block';
        document.getElementById('room2d-toolbar').style.display = 'flex';
        document.getElementById('center-view').style.display = 'none';
        document.getElementById('toolbar').style.display = 'none';
        var fpsEl = document.getElementById('fps-display');
        if (fpsEl) fpsEl.style.display = 'none';
        var vcEl = document.getElementById('view-cube-panel');
        if (vcEl) vcEl.style.display = 'none';
        var cornerAxes = document.getElementById('corner-axes-gizmo');
        if (cornerAxes) cornerAxes.style.display = 'none';

        // Update right panel and toolbar button states
        _updateRightPanel();
        _updateToolButtons();
        // Sync grid spacing from 3D state
        if (window.uiState.view && Number.isFinite(window.uiState.view.gridSpacing) && window.uiState.view.gridSpacing > 0) {
            _gridSpacing = window.uiState.view.gridSpacing;
        }
        _updateGridBadge();
        _updateGridSlider();
        _resizeCanvas();
        // Auto-fit view to polygon AFTER canvas is properly sized
        if (_vertices.length >= 3) {
            room2d.zoomToFit();
        }
        _bindEvents();
        _scheduleRender();

        // Change entry button text to "返回3D视图"
        const entryBtn = document.querySelector('[data-i18n="btn.room_2d_editor"]');
        if (entryBtn) {
            entryBtn.setAttribute('data-i18n', 'btn.return_to_3d');
            entryBtn.textContent = window.t ? t('btn.return_to_3d') : '\uD83D\uDD19 返回3D视图';
            entryBtn.onclick = function() { room2d.exitEditor(false); };
        }

        if (window.logOperation) logOperation('room2d.enter', 'Enter 2D floor plan editor', {}, 'view');
    };

    room2d.exitEditor = function(apply) {
        if (!_active) return;
        _unbindEvents();
        _active = false;
        _destroyCoordTooltip();

        document.getElementById('room2d-container').style.display = 'none';
        document.getElementById('room2d-toolbar').style.display = 'none';
        document.getElementById('center-view').style.display = '';
        document.getElementById('toolbar').style.display = '';

        if (apply && _closed && _vertices.length >= 3) {
            if (window.pushUndoCheckpoint) pushUndoCheckpoint('applyRoom2D');
            state.geometry.plane = _vertices.map(v => [v[0], v[1]]);
            state.geometry.planeHeight = _height;
            _generateRoomModels();
            if (window.rebuildGeometry) rebuildGeometry();
            if (window.fitCameraToScene) fitCameraToScene();
            if (window.logOperation) logOperation('room2d.apply', 'Applied 2D floor plan to 3D', { vertices: _vertices.length, height: _height }, 'modify');
        }

        // Restore entry button
        const entryBtn = document.querySelector('[data-i18n="btn.return_to_3d"]');
        if (entryBtn) {
            entryBtn.setAttribute('data-i18n', 'btn.room_2d_editor');
            entryBtn.textContent = window.t ? t('btn.room_2d_editor') : '\uD83C\uDFE0 房间 2D 平面设计';
            entryBtn.onclick = function() { room2d.enterEditor(); };
        }

        // Restore right panel to normal inspector
        if (window.updateUI) updateUI();
        var fpsEl = document.getElementById('fps-display');
        if (fpsEl) fpsEl.style.display = '';
        var vcEl = document.getElementById('view-cube-panel');
        if (vcEl) vcEl.style.display = '';
        var cornerAxes = document.getElementById('corner-axes-gizmo');
        if (cornerAxes) cornerAxes.style.display = '';
    };

    // --- Model Generation ---
    function _generateRoomModels() {
        // Clear selection before removing models
        if (window.setSelection) setSelection('project');

        // Remove existing room boundary models.
        // The legacy construction array is a read-only getter over the V4 buckets —
        // assignment no-ops, so collect the room-boundary uids and remove them through
        // the document API instead.
        const _boundaryUids = window.cfdprojStateV4.allModels(state)
            .filter(m => m.isRoomBoundary)
            .map(m => m.uid);
        if (_boundaryUids.length && window.removeModelsByUids) window.removeModelsByUids(_boundaryUids);

        const n = _vertices.length;
        const h = _height;

        // Signed area: positive → CCW, negative → CW (in Y-up 2D coords).
        // CW traversal produces inward-facing front normals that need reversal.
        // CCW traversal produces outward-facing front normals — no reversal needed.
        let signedArea2 = 0;
        for (let i = 0; i < n; i++) {
            const va = _vertices[i], vb = _vertices[(i + 1) % n];
            signedArea2 += (va[0] * vb[1] - vb[0] * va[1]);
        }
        const needsNormalFlip = signedArea2 < 0; // CW polygon

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
            // 2026-04-18 Y-up: plan coords are (plan_x, plan_depth); in 3D world, depth → Z, vertical → Y.
            wall.geometry.location = { x: a[0], y: 0, z: a[1] };
            wall.geometry.size = { x: length, y: h, z: 0 };
            // 2026-04-18 Y-up: walls rotate around world-Y axis (was Z axis in Z-up).
            // Negative Y component: a positive plan angle (CCW from +X toward +Y
            // in plan) must map local +X to world (cos θ, 0, +sin θ). A right-hand
            // +Y rotation sends +X → (cos θ, 0, -sin θ), so we rotate around -Y.
            wall.geometry.quaternion = { x: 0, y: -Math.sin(angle / 2), z: 0, w: Math.cos(angle / 2) };
            wall.geometry.scale = { x: 1, y: 1, z: 1 };
            if (needsNormalFlip && wall.geometry.faces && wall.geometry.faces.front) {
                wall.geometry.faces.front.reverseSolidNormal = true;
            }
            window.addModel(wall);
        }

        // Generate floor — polygon in LOCAL coordinates (relative to anchor).
        // 2026-04-18 Y-up: the top-face layout has up=(0,0,-1), so face-local y
        // increases toward world -Z. To land plan vertex (px, py) at world
        // (px, 0, py), face-local y must be (polyMaxY - py).
        const polyMinX = Math.min(..._vertices.map(v => v[0]));
        const polyMinY = Math.min(..._vertices.map(v => v[1]));
        const polyMaxY = Math.max(..._vertices.map(v => v[1]));
        const localPoly = _vertices.map(v => [v[0] - polyMinX, polyMaxY - v[1]]);

        const floor = window.createFloorModel ? createFloorModel('Room_Floor') : createModelFromRaw({ kind: 'floor', name: 'Room_Floor' });
        floor.isRoomBoundary = true;
        // createFloorModel already finalizes V4 cooling defaults (roomBoundaryLocation,
        // defaultBoundaryType). Override the room-boundary location to ground floor and
        // force the default boundary type to 'wall' (string per _normalizeDefaultBC).
        if (!floor.cooling) floor.cooling = {};
        floor.cooling.roomBoundaryLocation = 'bottom';
        floor.cooling.defaultBoundaryType = 'wall';
        // 2026-04-18 Y-up: plan depth → world Z; floor sits at world Y=0.
        floor.geometry.location = { x: polyMinX, y: 0, z: polyMinY };
        floor.geometry.polygon = localPoly;
        // Floor at y=0: normal must point -Y (outward, away from room, into earth).
        // The 'top' face layout has normal +Y, so reverse it for the ground floor.
        if (floor.geometry.faces && floor.geometry.faces.top) floor.geometry.faces.top.reverseSolidNormal = true;
        if (window.syncFloorBaseFromPolygon) syncFloorBaseFromPolygon(floor);
        window.addModel(floor);

        // Generate ceiling
        const ceiling = window.createFloorModel ? createFloorModel('Room_Ceiling') : createModelFromRaw({ kind: 'floor', name: 'Room_Ceiling' });
        ceiling.isRoomBoundary = true;
        if (!ceiling.cooling) ceiling.cooling = {};
        ceiling.cooling.roomBoundaryLocation = 'top';
        ceiling.cooling.defaultBoundaryType = 'wall';
        // 2026-04-18 Y-up: ceiling is at world Y=h (height), plan depth → world Z.
        ceiling.geometry.location = { x: polyMinX, y: h, z: polyMinY };
        ceiling.geometry.polygon = localPoly;
        if (window.syncFloorBaseFromPolygon) syncFloorBaseFromPolygon(ceiling);
        window.addModel(ceiling);

        // Rebuild all maps from the V4 construction buckets
        if (window._rebuildModelMap) _rebuildModelMap();
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

    // rAF-based render coalescing — cap rendering to display refresh rate
    let _renderDirty = false;
    function _scheduleRender() {
        if (!_renderDirty) {
            _renderDirty = true;
            requestAnimationFrame(() => { _renderDirty = false; _render(); });
        }
    }

    function _render() {
        if (!_active || !_ctx) return;
        const ctx = _ctx;
        const W = _canvas.width, H = _canvas.height;
        ctx.clearRect(0, 0, W, H);

        // Background
        ctx.fillStyle = _bgColor;
        ctx.fillRect(0, 0, W, H);

        // Grid
        _drawGrid(ctx);

        // Axes
        if (_showAxes) _drawAxes(ctx);

        // 3D model projections (semi-transparent)
        _drawModelProjections(ctx);

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

            // Preview line (drawing mode, not closed) — uses snapped position
            if (!_closed && _tool === 'draw' && _vertices.length > 0) {
                const snappedPreview = _applySnap(_mouseWorld.x, _mouseWorld.y);
                const last = worldToCanvas(_vertices[_vertices.length - 1][0], _vertices[_vertices.length - 1][1]);
                const mouse = worldToCanvas(snappedPreview.x, snappedPreview.y);
                ctx.setLineDash([5, 5]);
                ctx.strokeStyle = _shiftHeld ? '#ffcc00' : '#00d4ff';
                ctx.beginPath();
                ctx.moveTo(last.x, last.y);
                ctx.lineTo(mouse.x, mouse.y);
                ctx.stroke();
                ctx.setLineDash([]);
                // Show snapped cursor position
                ctx.beginPath();
                ctx.arc(mouse.x, mouse.y, 4, 0, Math.PI * 2);
                ctx.fillStyle = _shiftHeld ? '#ffcc00' : '#00d4ff';
                ctx.fill();
                // Real-time preview line length label
                const previewLen = Math.hypot(snappedPreview.x - _vertices[_vertices.length-1][0],
                                              snappedPreview.y - _vertices[_vertices.length-1][1]);
                if (previewLen > 1e-6) {
                    const labelX = (last.x + mouse.x) / 2 + 8;
                    const labelY = (last.y + mouse.y) / 2 - 10;
                    ctx.font = '14px Share Tech Mono, monospace';
                    ctx.fillStyle = _shiftHeld ? '#ffcc00' : '#00d4ff';
                    ctx.fillText(_fmt(previewLen) + ' ' + _unitLabel(), labelX, labelY);
                }
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

        // Box selection rectangle
        if (_boxSelectStart && _boxSelectEnd) {
            ctx.strokeStyle = 'rgba(0,212,255,0.6)';
            ctx.fillStyle = 'rgba(0,212,255,0.08)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            const bx = Math.min(_boxSelectStart.x, _boxSelectEnd.x);
            const by = Math.min(_boxSelectStart.y, _boxSelectEnd.y);
            const bw = Math.abs(_boxSelectEnd.x - _boxSelectStart.x);
            const bh = Math.abs(_boxSelectEnd.y - _boxSelectStart.y);
            ctx.fillRect(bx, by, bw, bh);
            ctx.strokeRect(bx, by, bw, bh);
            ctx.setLineDash([]);
        }

        // Box-selected vertices highlight (expanded wireframe)
        if (_boxSelectedVertices.size > 0) {
            _boxSelectedVertices.forEach(i => {
                if (i < _vertices.length) {
                    const p = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, 10, 0, Math.PI * 2);
                    ctx.strokeStyle = '#ffcc00';
                    ctx.lineWidth = 2;
                    ctx.stroke();
                }
            });
        }
        if (_boxSelectedEdges.size > 0) {
            _boxSelectedEdges.forEach(i => {
                if (i < _vertices.length) {
                    const j = (i + 1) % _vertices.length;
                    const a = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                    const b = worldToCanvas(_vertices[j][0], _vertices[j][1]);
                    ctx.strokeStyle = '#ffcc00';
                    ctx.lineWidth = 3;
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.stroke();
                }
            });
        }

        // Drawing mode: cursor circle indicator — snapped to grid or preview position
        if (_tool === 'draw' && !_closed) {
            var cursorWorld = _coordPreviewPos || _applySnap(_mouseWorld.x, _mouseWorld.y);
            var mc = worldToCanvas(cursorWorld.x, cursorWorld.y);
            var isPreview = !!_coordPreviewPos;
            ctx.beginPath();
            ctx.arc(mc.x, mc.y, isPreview ? 14 : 12, 0, Math.PI * 2);
            ctx.strokeStyle = isPreview ? 'rgba(255,204,0,0.7)' : 'rgba(0,212,255,0.5)';
            ctx.lineWidth = isPreview ? 2 : 1.5;
            ctx.stroke();
            // Cross-hair inside circle
            ctx.beginPath();
            ctx.moveTo(mc.x - 6, mc.y); ctx.lineTo(mc.x + 6, mc.y);
            ctx.moveTo(mc.x, mc.y - 6); ctx.lineTo(mc.x, mc.y + 6);
            ctx.strokeStyle = isPreview ? 'rgba(255,204,0,0.4)' : 'rgba(0,212,255,0.3)';
            ctx.lineWidth = 1;
            ctx.stroke();
            // Preview label
            if (isPreview) {
                ctx.font = '10px Share Tech Mono, monospace';
                ctx.fillStyle = '#ffcc00';
                ctx.textAlign = 'center';
                ctx.fillText('Enter→preview  Space→place', mc.x, mc.y - 18);
                ctx.textAlign = 'start';
            }
        }

        // Coordinate tooltip for draw mode
        _updateCoordTooltip();

        // Paper annotations (optional overlay)
        if (_showAnnotations) _drawPaperAnnotations(ctx);

        // Dimension labels on edges (use project unit)
        if (_vertices.length >= 2) {
            ctx.font = '13px Share Tech Mono, monospace';
            ctx.fillStyle = '#8be9fd';
            const edgeCount = _closed ? _vertices.length : _vertices.length - 1;
            for (let i = 0; i < edgeCount; i++) {
                const a = _vertices[i], b = _vertices[(i + 1) % _vertices.length];
                const mid = worldToCanvas((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
                const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
                ctx.fillText(_fmt(len) + ' ' + _unitLabel(), mid.x + 5, mid.y - 8);
            }
        }
    }

    // ── Paper Annotations (for publication figures) ──
    function _drawPaperAnnotations(ctx) {
        if (!state || !window.cfdprojStateV4) return;
        var racks = window.cfdprojStateV4.allModels(state).filter(function(m) { return m.kind === 'rack' && !(m.meta && m.meta.enabled === false); });
        if (racks.length === 0) return;

        var fontSize = Math.max(12, Math.round(14 * (_canvas.width / 1920)));
        var smallFont = Math.max(10, Math.round(11 * (_canvas.width / 1920)));

        // ── Group racks into columns by world-X position ──
        var cols = [];
        racks.slice().sort(function(a, b) { return a.geometry.location.x - b.geometry.location.x; }).forEach(function(r) {
            var placed = false;
            for (var ci = 0; ci < cols.length; ci++) {
                if (Math.abs(cols[ci].x - r.geometry.location.x) < 0.5) { cols[ci].racks.push(r); placed = true; break; }
            }
            if (!placed) cols.push({ x: r.geometry.location.x, racks: [r] });
        });
        cols.forEach(function(c) { c.x = c.racks.reduce(function(s, r) { return s + r.geometry.location.x; }, 0) / c.racks.length; });

        // Get each column's outlet direction in world coords
        cols.forEach(function(c) {
            var r0 = c.racks[0];
            var q = (r0.geometry && r0.geometry.quaternion) || { x: 0, y: 0, z: 0, w: 1 };
            var qw = (q.w === undefined || q.w === null) ? 1 : q.w;
            var quat = new THREE.Quaternion(q.x || 0, q.y || 0, q.z || 0, qw).normalize();
            var outDir = new THREE.Vector3(0, 0, 1).applyQuaternion(quat);
            c.outDirX = outDir.x;
            c.outDirZ = outDir.z;
            var zs = c.racks.map(function(r) { return r.geometry.location.z; });
            c.minZ = Math.min.apply(null, zs);
            c.maxZ = Math.max.apply(null, zs) + (c.racks[0].geometry.size.z || 1.2);
        });

        // ── 1. Hot / Cold Aisle Labels ──
        // Step A: Determine dominant rack facing direction (±X or ±Z)
        var xCount = 0, zCount = 0;
        cols.forEach(function(c) { if (Math.abs(c.outDirX) > Math.abs(c.outDirZ)) xCount += c.racks.length; else zCount += c.racks.length; });
        var facesX = xCount > zCount;

        // Step B: Re-group racks into "rows" along the FACING direction
        // facesX → aisles between X-adjacent groups (current cols are correct)
        // facesZ → aisles between Z-adjacent groups (need to re-group by Z center)
        var rows;
        if (facesX) {
            rows = cols; // cols already grouped by X
        } else {
            // Re-group all racks by their world-Z center position
            rows = [];
            racks.forEach(function(r) {
                var q = (r.geometry && r.geometry.quaternion) || {x:0,y:0,z:0,w:1};
                var qw = (q.w === null || q.w === undefined) ? 1 : q.w;
                var quat = new THREE.Quaternion(q.x||0, q.y||0, q.z||0, qw).normalize();
                var c0 = new THREE.Vector3(0,0,0).applyQuaternion(quat);
                var c1 = new THREE.Vector3(r.geometry.size.x,0,r.geometry.size.z).applyQuaternion(quat);
                var zCenter = r.geometry.location.z + (c0.z + c1.z) / 2;
                var outDir = new THREE.Vector3(0,0,1).applyQuaternion(quat);
                var placed = false;
                for (var ri = 0; ri < rows.length; ri++) {
                    if (Math.abs(rows[ri].pos - zCenter) < 0.8) { rows[ri].racks.push(r); rows[ri].outDirs.push(outDir.z); placed = true; break; }
                }
                if (!placed) rows.push({ pos: zCenter, racks: [r], outDirs: [outDir.z] });
            });
            rows.sort(function(a, b) { return a.pos - b.pos; });
            rows.forEach(function(row) {
                var avg = row.outDirs.reduce(function(s, v) { return s + v; }, 0) / row.outDirs.length;
                row.outSign = avg > 0 ? +1 : -1;
                var xs = row.racks.map(function(r) { return r.geometry.location.x; });
                row.perpMin = Math.min.apply(null, xs);
                row.perpMax = Math.max.apply(null, xs) + (row.racks[0].geometry.size.x || 0.6);
            });
        }

        // Step C: For facesX layout, compute outSign for each col
        if (facesX) {
            rows.forEach(function(row) {
                row.outSign = row.outDirX > 0 ? +1 : -1;
                row.pos = row.x;
                row.perpMin = row.minZ;
                row.perpMax = row.maxZ;
            });
        }

        // Step D: Detect aisles between adjacent rows
        var aisles = [];
        for (var i = 0; i < rows.length - 1; i++) {
            var rA = rows[i], rB = rows[i + 1];
            if (Math.abs(rA.pos - rB.pos) > 6) continue;
            // Outlets face each other → HOT: rA outlet toward rB (+1) AND rB outlet toward rA (-1)
            var isHot = (rA.outSign > 0 && rB.outSign < 0);
            // Inlets face each other → COLD: rA outlet away from rB (-1) AND rB outlet away from rA (+1)
            var isCold = (rA.outSign < 0 && rB.outSign > 0);
            if (!isHot && !isCold) continue;
            var midPos = (rA.pos + rB.pos) / 2;
            var perpOverlapMin = Math.max(rA.perpMin, rB.perpMin);
            var perpOverlapMax = Math.min(rA.perpMax, rB.perpMax);
            if (perpOverlapMax < perpOverlapMin) perpOverlapMax = (rA.perpMax + rB.perpMax) / 2;
            var midPerp = (perpOverlapMin + perpOverlapMax) / 2;
            var wx = facesX ? midPos : midPerp;
            var wz = facesX ? midPerp : midPos;
            aisles.push({ type: isHot ? 'HOT' : 'COLD', worldX: wx, worldZ: wz, vertical: !facesX });
        }

        // Step E: Check baffle enclosure to refine labels
        var baffleModels = window.cfdprojStateV4.allModels(state).filter(function(m) { return m.kind === 'baffle' && !(m.meta && m.meta.enabled === false); });
        if (baffleModels.length > 0) {
            aisles.forEach(function(a) {
                var nearBaffle = baffleModels.some(function(b) {
                    var dist = Math.hypot(b.geometry.location.x - a.worldX, b.geometry.location.z - a.worldZ);
                    return dist < 5;
                });
                if (nearBaffle) a.contained = true;
            });
        }

        _lastAisleLabels = aisles.map(function(a) { return a.type + (a.contained ? '_C' : ''); });

        // Step F: Render labels
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = 'bold ' + fontSize + 'px Share Tech Mono, monospace';
        aisles.forEach(function(a) {
            var cp = worldToCanvas(a.worldX, a.worldZ);
            ctx.save();
            ctx.translate(cp.x, cp.y);
            if (a.vertical) ctx.rotate(-Math.PI / 2);
            ctx.fillStyle = a.type === 'HOT' ? 'rgba(224,108,117,0.9)' : 'rgba(97,175,239,0.9)';
            ctx.fillText(a.type + ' AISLE', 0, 0);
            ctx.restore();
        });

        // ── 3. Column Labels (C1, C2, ...) ──
        ctx.font = smallFont + 'px Share Tech Mono, monospace';
        ctx.fillStyle = 'rgba(200,200,200,0.7)';
        ctx.textAlign = 'center';
        for (var ci = 0; ci < cols.length; ci++) {
            var col = cols[ci];
            var topRack = col.racks.reduce(function(best, r) { return r.geometry.location.z < best.geometry.location.z ? r : best; }, col.racks[0]);
            var labelWorldX = col.x + (topRack.geometry.size.x || 0.6) / 2;
            var labelWorldZ = topRack.geometry.location.z - 0.3;
            var lp = worldToCanvas(labelWorldX, labelWorldZ);
            ctx.fillText('C' + (ci + 1), lp.x, lp.y);
        }

        // ── 5. Single Rack Dimension (annotate first rack only) ──
        if (racks.length > 0) {
            var r0 = racks[0];
            var rW = r0.geometry.size.x, rD = r0.geometry.size.z;
            var q0 = (r0.geometry && r0.geometry.quaternion) || { x: 0, y: 0, z: 0, w: 1 };
            var qw0 = (q0.w === undefined || q0.w === null) ? 1 : q0.w;
            var quat0 = new THREE.Quaternion(q0.x || 0, q0.y || 0, q0.z || 0, qw0).normalize();
            var localCorners = [
                new THREE.Vector3(0, 0, 0), new THREE.Vector3(rW, 0, 0),
                new THREE.Vector3(rW, 0, rD), new THREE.Vector3(0, 0, rD)
            ];
            var wCorners = localCorners.map(function(lc) {
                var wc = lc.clone().applyQuaternion(quat0);
                return worldToCanvas(r0.geometry.location.x + wc.x, r0.geometry.location.z + wc.z);
            });
            ctx.strokeStyle = 'rgba(255,255,100,0.6)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 3]);
            // W dimension line (between corner 0-1)
            var wMid = { x: (wCorners[0].x + wCorners[1].x) / 2, y: (wCorners[0].y + wCorners[1].y) / 2 };
            ctx.beginPath(); ctx.moveTo(wCorners[0].x, wCorners[0].y); ctx.lineTo(wCorners[1].x, wCorners[1].y); ctx.stroke();
            // D dimension line (between corner 1-2)
            var dMid = { x: (wCorners[1].x + wCorners[2].x) / 2, y: (wCorners[1].y + wCorners[2].y) / 2 };
            ctx.beginPath(); ctx.moveTo(wCorners[1].x, wCorners[1].y); ctx.lineTo(wCorners[2].x, wCorners[2].y); ctx.stroke();
            ctx.setLineDash([]);
            ctx.font = smallFont + 'px Share Tech Mono, monospace';
            ctx.fillStyle = 'rgba(255,255,100,0.8)';
            ctx.textAlign = 'center';
            ctx.fillText(rW.toFixed(1) + 'm', wMid.x, wMid.y - 6);
            ctx.fillText(rD.toFixed(1) + 'm', dMid.x + 12, dMid.y);
        }

        // ── 7. Airflow Legend (top-right corner) ──
        var legX = _canvas.width - 200, legY = 30;
        ctx.fillStyle = 'rgba(30,34,42,0.8)';
        ctx.fillRect(legX, legY, 180, 50);
        ctx.strokeStyle = 'rgba(90,167,255,0.5)';
        ctx.lineWidth = 1;
        ctx.strokeRect(legX, legY, 180, 50);
        ctx.font = smallFont + 'px Share Tech Mono, monospace';
        ctx.fillStyle = '#5aa7ff';
        ctx.textAlign = 'left';
        ctx.fillText('Airflow Direction', legX + 10, legY + 18);
        // Arrow
        ctx.strokeStyle = '#00d4ff';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(legX + 10, legY + 38); ctx.lineTo(legX + 60, legY + 38); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(legX + 55, legY + 33); ctx.lineTo(legX + 60, legY + 38); ctx.lineTo(legX + 55, legY + 43); ctx.stroke();
        ctx.fillStyle = 'rgba(200,200,200,0.7)';
        ctx.fillText('inlet → outlet', legX + 70, legY + 42);

        // ── 8. Corner Axes (bottom-left) ──
        var axLen = 50;
        var axX = 40, axY = _canvas.height - 40;
        ctx.strokeStyle = '#e06c75';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(axX, axY); ctx.lineTo(axX + axLen, axY); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(axX + axLen - 6, axY - 5); ctx.lineTo(axX + axLen, axY); ctx.lineTo(axX + axLen - 6, axY + 5); ctx.stroke();
        ctx.fillStyle = '#e06c75';
        ctx.font = 'bold ' + fontSize + 'px Share Tech Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText('X', axX + axLen + 12, axY + 4);
        ctx.strokeStyle = '#98c379';
        ctx.beginPath(); ctx.moveTo(axX, axY); ctx.lineTo(axX, axY - axLen); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(axX - 5, axY - axLen + 6); ctx.lineTo(axX, axY - axLen); ctx.lineTo(axX + 5, axY - axLen + 6); ctx.stroke();
        ctx.fillStyle = '#98c379';
        ctx.fillText('Z', axX, axY - axLen - 10);

        // ── 9. Scale Bar (bottom-right) ──
        var scaleMeters = 5;
        if (_view.scale * scaleMeters < 50) scaleMeters = 10;
        if (_view.scale * scaleMeters < 50) scaleMeters = 20;
        if (_view.scale * scaleMeters > 400) scaleMeters = 2;
        if (_view.scale * scaleMeters > 400) scaleMeters = 1;
        var scalePx = scaleMeters * _view.scale;
        var sbX = _canvas.width - scalePx - 40, sbY = _canvas.height - 30;
        ctx.strokeStyle = '#00d4ff';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(sbX, sbY); ctx.lineTo(sbX + scalePx, sbY); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(sbX, sbY - 5); ctx.lineTo(sbX, sbY + 5); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(sbX + scalePx, sbY - 5); ctx.lineTo(sbX + scalePx, sbY + 5); ctx.stroke();
        ctx.fillStyle = '#00d4ff';
        ctx.font = fontSize + 'px Share Tech Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(scaleMeters + ' m', sbX + scalePx / 2, sbY - 10);

        ctx.textAlign = 'start';
        ctx.textBaseline = 'alphabetic';
    }

    function _drawGrid(ctx) {
        const pixelSpacing = _gridSpacing * _view.scale;
        // Don't draw grid if lines would be too dense (< 3px apart)
        if (pixelSpacing < 3) return;

        const topLeft = canvasToWorld(0, 0);
        const bottomRight = canvasToWorld(_canvas.width, _canvas.height);
        const spacing = _gridSpacing;
        const minX = Math.floor(topLeft.x / spacing) * spacing;
        const maxX = Math.ceil(bottomRight.x / spacing) * spacing;
        const minY = Math.floor(bottomRight.y / spacing) * spacing;
        const maxY = Math.ceil(topLeft.y / spacing) * spacing;

        // Safety: cap at 2000 lines to prevent freeze
        const xCount = (maxX - minX) / spacing;
        const yCount = (maxY - minY) / spacing;
        if (xCount > 2000 || yCount > 2000) return;

        // Batch all lines into single path
        ctx.strokeStyle = '#2a2e38';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        for (let x = minX; x <= maxX; x += spacing) {
            const px = (x - _view.offsetX) * _view.scale + _canvas.width / 2;
            ctx.moveTo(px, 0);
            ctx.lineTo(px, _canvas.height);
        }
        for (let y = minY; y <= maxY; y += spacing) {
            const py = _canvas.height / 2 - (y - _view.offsetY) * _view.scale;
            ctx.moveTo(0, py);
            ctx.lineTo(_canvas.width, py);
        }
        ctx.stroke();
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

    // --- 3D Model Projections ---
    function _drawModelProjections(ctx) {
        if (!state || !window.cfdprojStateV4) return;
        const colors = {
            rack: 'rgba(90,167,255,0.35)', acu: 'rgba(152,195,121,0.35)',
            box: 'rgba(229,192,123,0.25)', baffle: 'rgba(176,128,64,0.25)',
            wall: 'rgba(100,100,120,0.15)', floor: 'rgba(100,140,100,0.10)'
        };
        const borderColors = {
            rack: '#5aa7ff', acu: '#98c379',
            box: '#e5c07b', baffle: '#d19a66',
            wall: 'rgba(100,100,120,0.5)', floor: 'rgba(100,140,100,0.3)'
        };
        window.cfdprojStateV4.allModels(state).forEach(m => {
            const _en = m.meta ? m.meta.enabled : undefined;
            if (!_en && _en !== undefined) return;
            if (m.isRoomBoundary) return; // skip boundary models
            const fill = colors[m.kind] || 'rgba(128,128,128,0.1)';
            const border = borderColors[m.kind] || 'rgba(128,128,128,0.3)';
            // Project AABB onto XY plane using anchor + base (for axis-aligned)
            // For rotated models, use OBB corners
            let corners;
            if (window.hasModelRotation && hasModelRotation(m)) {
                // Project only the bottom face (Z=0) corners onto XY plane.
                // Using all 8 corners + AABB was wrong: it drew an axis-aligned
                // bounding box that pulsed/squashed as the model rotated.
                const _q = (m.geometry && m.geometry.quaternion) || {x:0,y:0,z:0,w:1};
                // NOTE: `_q.w || 1` is WRONG when w === 0 (180° rotation → mistaken for 90°
                // because quat becomes (0,0,1,1) normalized). Use explicit null-check.
                const _qw = (_q.w === undefined || _q.w === null) ? 1 : _q.w;
                const _quat = new THREE.Quaternion(_q.x||0, _q.y||0, _q.z||0, _qw).normalize();
                const _s = (m.geometry && m.geometry.scale) || {x:1,y:1,z:1};
                const sw = m.geometry.size.x * (_s.x||1), sd = m.geometry.size.z * (_s.z||1);
                // 2026-04-18 Y-up: depth (D) is along world-Z, not world-Y.
                // Bottom face corners in local space: X=0..W, Z=0..D, Y=0 (floor level).
                const _bottomCorners = [
                    new THREE.Vector3(0, 0, 0),
                    new THREE.Vector3(sw, 0, 0),
                    new THREE.Vector3(sw, 0, sd),
                    new THREE.Vector3(0, 0, sd),
                ];
                corners = _bottomCorners.map(lc => {
                    const wc = lc.clone().applyQuaternion(_quat);
                    // 2026-04-18 Y-up: plan canvas Y = world Z.
                    return [m.geometry.location.x + wc.x, m.geometry.location.z + wc.z];
                });
                // Draw the actual rotated quadrilateral
                const canvasCorners = corners.map(([x, y]) => worldToCanvas(x, y));
                ctx.beginPath();
                ctx.moveTo(canvasCorners[0].x, canvasCorners[0].y);
                for (let ci = 1; ci < canvasCorners.length; ci++) ctx.lineTo(canvasCorners[ci].x, canvasCorners[ci].y);
                ctx.closePath();
                ctx.fillStyle = fill;
                ctx.fill();
                ctx.strokeStyle = border;
                ctx.lineWidth = 1;
                ctx.stroke();
            } else {
                // 2026-04-18 Y-up: plan canvas Y = world Z (location.z + size.z).
                const ax = m.geometry.location.x, az = m.geometry.location.z;
                const tl = worldToCanvas(ax, az + m.geometry.size.z);
                const br = worldToCanvas(ax + m.geometry.size.x, az);
                ctx.fillStyle = fill;
                ctx.fillRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
                ctx.strokeStyle = border;
                ctx.lineWidth = 1;
                ctx.strokeRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
            }
            // Compute visual bounding info for label + highlight
            // For rotated models, reuse the bottom-face corners computed above
            let visTL, visBR;
            if (corners && corners.length) {
                let _mnX = Infinity, _mxX = -Infinity, _mnY = Infinity, _mxY = -Infinity;
                corners.forEach(([x, y]) => { if(x<_mnX)_mnX=x;if(x>_mxX)_mxX=x;if(y<_mnY)_mnY=y;if(y>_mxY)_mxY=y; });
                visTL = worldToCanvas(_mnX, _mxY);
                visBR = worldToCanvas(_mxX, _mnY);
            } else {
                // 2026-04-18 Y-up: plan canvas Y = world Z.
                visTL = worldToCanvas(m.geometry.location.x, m.geometry.location.z + m.geometry.size.z);
                visBR = worldToCanvas(m.geometry.location.x + m.geometry.size.x, m.geometry.location.z);
            }
            // Highlight selected model — rotated polygon for rotated models
            if (_isModel2dSelected(m.uid)) {
                ctx.strokeStyle = '#ffcc00';
                ctx.lineWidth = 2.5;
                ctx.setLineDash([6, 3]);
                if (corners && corners.length) {
                    const _hlCorners = corners.map(([x, y]) => worldToCanvas(x, y));
                    ctx.beginPath();
                    ctx.moveTo(_hlCorners[0].x - 2, _hlCorners[0].y - 2);
                    _hlCorners.forEach((c, i) => { if (i > 0) ctx.lineTo(c.x, c.y); });
                    ctx.closePath();
                    ctx.stroke();
                } else {
                    ctx.strokeRect(visTL.x - 2, visTL.y - 2, visBR.x - visTL.x + 4, visBR.y - visTL.y + 4);
                }
                ctx.setLineDash([]);
            }
            // Label at visual center (truncated to fit)
            if (m.kind !== 'wall' && m.kind !== 'baffle') {
                const labelCX = (visTL.x + visBR.x) / 2;
                const labelCY = (visTL.y + visBR.y) / 2;
                ctx.font = '10px Share Tech Mono, monospace';
                ctx.fillStyle = _isModel2dSelected(m.uid) ? '#ffcc00' : border;
                ctx.textAlign = 'center';
                const maxLabelW = Math.max(20, Math.abs(visBR.x - visTL.x) - 4);
                let label = (m.meta && m.meta.name) || '';
                while (ctx.measureText(label).width > maxLabelW && label.length > 3) {
                    label = label.slice(0, -2) + '…';
                }
                ctx.fillText(label, labelCX, labelCY + 4);
                ctx.textAlign = 'start';
            }
            // Rack inlet→outlet direction arrow (spec 2026-04-18, item 6).
            // Local: front (y=0) is inlet, back (y=D) is outlet by default. Arrow
            // spans 25%→75% of depth, passes through width-center, then quaternion-
            // rotated + anchor-translated into world coords.
            if (m.kind === 'rack') {
                const _qa = (m.geometry && m.geometry.quaternion) || {x:0,y:0,z:0,w:1};
                const _qaw = (_qa.w === undefined || _qa.w === null) ? 1 : _qa.w;
                const _quatA = new THREE.Quaternion(_qa.x||0, _qa.y||0, _qa.z||0, _qaw).normalize();
                const _sa = (m.geometry && m.geometry.scale) || {x:1,y:1,z:1};
                const _W = m.geometry.size.x * (_sa.x||1);
                // 2026-04-18 Y-up: size.z is depth along world-Z; scale factor is _sa.z.
                const _D = m.geometry.size.z * (_sa.z||1);
                // If face/back roles are swapped in this model, invert direction.
                // Default: front = inlet, back = outlet (arrow: 0→D along world-Z).
                let frontIsInlet = true;
                const _mfaces = m.geometry && m.geometry.faces;
                if (_mfaces) {
                    const fdn = (_mfaces.front && _mfaces.front.displayName) || 'inlet';
                    frontIsInlet = (fdn === 'inlet');
                }
                // 2026-04-18 Y-up: depth (D) is along local-Z, not local-Y.
                const startLocal = new THREE.Vector3(_W/2, 0, frontIsInlet ? _D*0.25 : _D*0.75).applyQuaternion(_quatA);
                const endLocal   = new THREE.Vector3(_W/2, 0, frontIsInlet ? _D*0.75 : _D*0.25).applyQuaternion(_quatA);
                // 2026-04-18 Y-up: plan canvas Y = world Z (location.z + local.z).
                const pS = worldToCanvas(m.geometry.location.x + startLocal.x, m.geometry.location.z + startLocal.z);
                const pE = worldToCanvas(m.geometry.location.x + endLocal.x, m.geometry.location.z + endLocal.z);
                const dxA = pE.x - pS.x, dyA = pE.y - pS.y;
                const lenA = Math.hypot(dxA, dyA);
                if (lenA > 3) {
                    const ux = dxA / lenA, uy = dyA / lenA;
                    const headLen = Math.min(9, lenA * 0.35);
                    const headHalf = headLen * 0.55;
                    ctx.strokeStyle = 'rgba(0,212,255,0.9)';
                    ctx.fillStyle = 'rgba(0,212,255,0.9)';
                    ctx.lineWidth = 2;
                    ctx.beginPath();
                    ctx.moveTo(pS.x, pS.y);
                    // Shorten shaft so it ends at the arrowhead base
                    ctx.lineTo(pE.x - ux * headLen, pE.y - uy * headLen);
                    ctx.stroke();
                    // Triangle arrowhead
                    const lx = pE.x - ux * headLen - uy * headHalf;
                    const ly = pE.y - uy * headLen + ux * headHalf;
                    const rx = pE.x - ux * headLen + uy * headHalf;
                    const ry = pE.y - uy * headLen - ux * headHalf;
                    ctx.beginPath();
                    ctx.moveTo(pE.x, pE.y);
                    ctx.lineTo(lx, ly);
                    ctx.lineTo(rx, ry);
                    ctx.closePath();
                    ctx.fill();
                }
            }
            // Rotation handle (when rotateTool enabled)
            const _rrp = _getRotateRingParams(m);
            if (_rrp) {
                const _isRotating = _rotateHandle && _rotateHandle.id === m.uid;
                const _isHover = !_isRotating && _rotateHoverModelId === m.uid;
                // Ring: 3 states — dragging (yellow), hover (bright cyan), default (dim cyan)
                ctx.beginPath();
                ctx.arc(_rrp.centroid.x, _rrp.centroid.y, _rrp.radius, 0, Math.PI * 2);
                ctx.strokeStyle = _isRotating ? '#ffcc00' : _isHover ? '#00d4ff' : 'rgba(0,212,255,0.35)';
                ctx.lineWidth = _isRotating ? 3 : _isHover ? 2.5 : 1.5;
                ctx.setLineDash(_isRotating ? [] : [4, 4]);
                ctx.stroke();
                ctx.setLineDash([]);
                // Handle dot
                ctx.beginPath(); ctx.arc(_rrp.hx, _rrp.hy, _isHover || _isRotating ? 8 : 6, 0, Math.PI * 2);
                ctx.fillStyle = _isRotating ? '#ffcc00' : _isHover ? '#00e5ff' : '#00d4ff'; ctx.fill();
                ctx.strokeStyle = '#1a1e26'; ctx.lineWidth = 1.5; ctx.stroke();
                // Guide line
                ctx.beginPath(); ctx.moveTo(_rrp.centroid.x, _rrp.centroid.y); ctx.lineTo(_rrp.hx, _rrp.hy);
                ctx.strokeStyle = _isRotating ? 'rgba(255,204,0,0.5)' : _isHover ? 'rgba(0,212,255,0.5)' : 'rgba(0,212,255,0.25)';
                ctx.lineWidth = 1; ctx.stroke();
                // Angle label during drag
                if (_isRotating) {
                    const _degLabel = (THREE.MathUtils.radToDeg(_rotateHandle.currentAngle) % 360).toFixed(1) + '\u00B0';
                    ctx.font = '13px Share Tech Mono, monospace'; ctx.fillStyle = '#ffcc00';
                    ctx.textAlign = 'center'; ctx.fillText(_degLabel, _rrp.centroid.x, _rrp.centroid.y - _rrp.radius - 10);
                    ctx.textAlign = 'start';
                }
            }
        });
    }

    // --- Coordinate tooltip (draw mode) ---

    let _coordInputFocused = false;  // true when any coord input has focus — locks tooltip position
    let _coordPreviewPos = null;     // {x, y} world coords from Enter key — preview only, not placed

    function _isCoordInputFocused() {
        if (!_coordTooltip) return false;
        var active = document.activeElement;
        return active && _coordTooltip.contains(active);
    }

    function _createCoordTooltip() {
        if (_coordTooltip) return _coordTooltip;
        var container = document.getElementById('room2d-container');
        if (!container) return null;
        var div = document.createElement('div');
        div.id = 'room2d-coord-tooltip';
        div.style.cssText = 'position:absolute;pointer-events:auto;z-index:50;background:rgba(16,21,28,0.92);border:1px solid rgba(0,212,255,0.35);padding:4px 6px;font:11px "Share Tech Mono",monospace;color:#8be9fd;display:none;white-space:nowrap;';
        var inputStyle = 'width:62px;background:rgba(0,0,0,0.4);border:1px solid rgba(0,212,255,0.25);color:#fff;font:11px "Share Tech Mono",monospace;padding:1px 4px;margin:0 2px;text-align:right;';
        div.innerHTML = '<span style="color:rgba(0,212,255,0.6);">X</span><input id="r2d-coord-x" type="number" step="any" style="' + inputStyle + '" tabindex="1">'
            + ' <span style="color:rgba(0,212,255,0.6);">Y</span><input id="r2d-coord-y" type="number" step="any" style="' + inputStyle + '" tabindex="2">'
            + ' <span style="color:rgba(0,212,255,0.6);">Z</span><input id="r2d-coord-z" type="number" step="any" style="' + inputStyle + 'opacity:0.5;pointer-events:none;" tabindex="-1" readonly>'
            + ' <span style="color:rgba(0,212,255,0.4);font-size:9px;">' + _unitLabel() + '</span>'
            + '<div style="margin-top:3px;font-size:9px;color:rgba(0,212,255,0.35);line-height:1.4;white-space:normal;max-width:220px;">'
            + (window.t ? t('hint.coord_tooltip_keys') : 'Tab: focus/cycle inputs | Enter: preview | Space: place | Esc: cancel')
            + '</div>';
        container.appendChild(div);

        var inputs = div.querySelectorAll('input');
        var inputArr = Array.prototype.slice.call(inputs);
        for (var i = 0; i < inputs.length; i++) {
            inputs[i].addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    // Enter = move cursor preview to coordinates (don't place vertex)
                    e.preventDefault();
                    e.stopPropagation();
                    _movePreviewToCoords();
                } else if (e.key === ' ') {
                    // Space = confirm and place vertex
                    e.preventDefault();
                    e.stopPropagation();
                    _confirmCoordVertex();
                } else if (e.key === 'Tab') {
                    // Tab = cycle X↔Y only (Z is readonly)
                    e.preventDefault();
                    e.stopPropagation();
                    var editableInputs = inputArr.filter(function(inp) { return !inp.readOnly; });
                    var cur = editableInputs.indexOf(e.target);
                    if (cur < 0) cur = 0;
                    var next = e.shiftKey ? (cur - 1 + editableInputs.length) % editableInputs.length : (cur + 1) % editableInputs.length;
                    editableInputs[next].focus();
                    editableInputs[next].select();
                } else if (e.key === 'Escape') {
                    // Escape = blur and return to mouse-follow mode
                    e.preventDefault();
                    e.target.blur();
                    _coordPreviewPos = null;
                    _scheduleRender();
                }
            });
            inputs[i].addEventListener('focus', function() { _coordInputFocused = true; });
            inputs[i].addEventListener('blur', function() {
                // Delay check — blur may fire before focus on next input
                setTimeout(function() { _coordInputFocused = _isCoordInputFocused(); }, 10);
            });
            inputs[i].addEventListener('mousedown', function(e) { e.stopPropagation(); });
            inputs[i].addEventListener('click', function(e) { e.stopPropagation(); });
        }
        _coordTooltip = div;
        return div;
    }

    /** Enter key: move cursor preview to typed coordinates (no vertex placed yet) */
    function _movePreviewToCoords() {
        var xEl = document.getElementById('r2d-coord-x');
        var yEl = document.getElementById('r2d-coord-y');
        if (!xEl || !yEl) return;
        var unitScale = window.LENGTH_UNITS && state.meta.inputUnit ? LENGTH_UNITS[state.meta.inputUnit] || 1 : 1;
        var wx = parseFloat(xEl.value) * unitScale;
        var wy = parseFloat(yEl.value) * unitScale;
        if (isNaN(wx) || isNaN(wy)) return;
        _coordPreviewPos = { x: wx, y: wy };
        _scheduleRender();
    }

    /** Space key or mouse click: confirm and place vertex at preview/current position */
    function _confirmCoordVertex() {
        var pos = _coordPreviewPos;
        if (!pos) {
            // No preview set — use current snapped mouse position
            var snapped = _applySnap(_mouseWorld.x, _mouseWorld.y);
            pos = { x: snapped.x, y: snapped.y };
        }
        if (_closed) return;
        // Check close
        if (_vertices.length >= 3) {
            var p0 = worldToCanvas(_vertices[0][0], _vertices[0][1]);
            var pc = worldToCanvas(pos.x, pos.y);
            if (Math.hypot(p0.x - pc.x, p0.y - pc.y) < 15) {
                _closed = true;
                _tool = 'select';
                _updateToolButtons();
                _updateRightPanel();
                _scheduleRender();
                _coordPreviewPos = null;
                return;
            }
        }
        _vertices.push([pos.x, pos.y]);
        _coordPreviewPos = null;
        _scheduleRender();
        _updateRightPanel();
    }

    function _updateCoordTooltip() {
        if (_tool !== 'draw' || _closed) {
            if (_coordTooltip) _coordTooltip.style.display = 'none';
            return;
        }
        var tt = _createCoordTooltip();
        if (!tt) return;

        // When an input is focused, lock tooltip position (don't follow mouse)
        if (!_coordInputFocused) {
            var snapped = _applySnap(_mouseWorld.x, _mouseWorld.y);
            var mc = worldToCanvas(snapped.x, snapped.y);
            tt.style.left = (mc.x + 20) + 'px';
            tt.style.top = (mc.y + 20) + 'px';
        }
        tt.style.display = '';

        var unitScale = window.LENGTH_UNITS && state.meta.inputUnit ? LENGTH_UNITS[state.meta.inputUnit] || 1 : 1;
        var snapped = _applySnap(_mouseWorld.x, _mouseWorld.y);
        var displayX = snapped.x / unitScale;
        var displayY = snapped.y / unitScale;
        var displayZ = 0;

        // Only update values if no input is focused
        if (!_coordInputFocused) {
            var xEl = document.getElementById('r2d-coord-x');
            var yEl = document.getElementById('r2d-coord-y');
            var zEl = document.getElementById('r2d-coord-z');
            if (xEl) xEl.value = displayX.toFixed(1);
            if (yEl) yEl.value = displayY.toFixed(1);
            if (zEl) zEl.value = displayZ.toFixed(1);
        }
    }

    /** Sync local _vertices to state.geometry.plane/planeHeight so undo snapshots capture current geometry. */
    function _syncVerticesToState() {
        if (_closed && _vertices.length >= 3) {
            state.geometry.plane = _vertices.map(function(v) { return [v[0], v[1]]; });
            state.geometry.planeHeight = _height;
        }
    }

    function _destroyCoordTooltip() {
        if (_coordTooltip) {
            _coordTooltip.remove();
            _coordTooltip = null;
        }
        _coordInputFocused = false;
        _coordPreviewPos = null;
    }

    // --- Snap ---
    let _shiftHeld = false;

    function _applySnap(wx, wy, forceShiftConstrain) {
        let x = wx, y = wy;
        if (_gridSnap) {
            x = Math.round(x / _gridSpacing) * _gridSpacing;
            y = Math.round(y / _gridSpacing) * _gridSpacing;
        }
        if (_vertexSnap) {
            const snapRadius = 10 / _view.scale;
            let bestDist = snapRadius;
            // Snap to polygon vertices
            _vertices.forEach(([vx, vy], i) => {
                if (i === _dragVertex) return;
                const d = Math.hypot(x - vx, y - vy);
                if (d < bestDist) { bestDist = d; x = vx; y = vy; }
            });
            // Snap to model corners (ALL models including walls, baffles)
            if (state && window.cfdprojStateV4) {
                window.cfdprojStateV4.allModels(state).forEach(m => {
                    if (m.meta && m.meta.enabled === false) return;
                    // 2026-04-18 Y-up: plan canvas Y = world Z (location.z + size.z).
                    const loc = m.geometry.location, size = m.geometry.size;
                    const corners = [
                        [loc.x, loc.z],
                        [loc.x + size.x, loc.z],
                        [loc.x + size.x, loc.z + size.z],
                        [loc.x, loc.z + size.z]
                    ];
                    corners.forEach(([cx, cy]) => {
                        const d = Math.hypot(x - cx, y - cy);
                        if (d < bestDist) { bestDist = d; x = cx; y = cy; }
                    });
                });
            }
        }
        if (_edgeAlign) {
            // Snap to model edges (align X or Y to any model edge)
            const edgeThreshold = 8 / _view.scale;
            if (state && window.cfdprojStateV4) {
                window.cfdprojStateV4.allModels(state).forEach(m => {
                    if (m.meta && m.meta.enabled === false) return;
                    // 2026-04-18 Y-up: plan canvas Y = world Z.
                    const mx = m.geometry.location.x, mz = m.geometry.location.z;
                    const edges = [mx, mx + m.geometry.size.x]; // X edges
                    const yEdges = [mz, mz + m.geometry.size.z]; // Z (plan-Y) edges
                    edges.forEach(ex => { if (Math.abs(x - ex) < edgeThreshold) x = ex; });
                    yEdges.forEach(ey => { if (Math.abs(y - ey) < edgeThreshold) y = ey; });
                });
            }
        }
        // Shift constraint: force horizontal or vertical from last vertex
        if ((_shiftHeld) && _vertices.length > 0 && !_closed && _dragVertex < 0) {
            const prev = _vertices[_vertices.length - 1];
            if (prev) {
                const dx = Math.abs(x - prev[0]), dy = Math.abs(y - prev[1]);
                if (dx >= dy) { y = prev[1]; } // horizontal
                else { x = prev[0]; } // vertical
            }
        } else if (_edgeAlign && _vertices.length > 0 && _dragVertex < 0 && !_closed) {
            const prev = _vertices[_vertices.length - 1];
            if (prev) {
                const dx = Math.abs(x - prev[0]), dy = Math.abs(y - prev[1]);
                const threshold = 5 / _view.scale;
                if (dx < threshold && dy > threshold) x = prev[0];
                if (dy < threshold && dx > threshold) y = prev[1];
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
        _boundHandlers.keyup = function(e) { _shiftHeld = !!e.shiftKey; if (_tool === 'draw' && !_closed) _scheduleRender(); };
        _boundHandlers.resize = () => { _resizeCanvas(); _render(); };
        c.addEventListener('mousedown', _boundHandlers.mousedown);
        c.addEventListener('mousemove', _boundHandlers.mousemove);
        c.addEventListener('mouseup', _boundHandlers.mouseup);
        c.addEventListener('wheel', _boundHandlers.wheel);
        c.addEventListener('contextmenu', _boundHandlers.contextmenu);
        document.addEventListener('keydown', _boundHandlers.keydown);
        document.addEventListener('keyup', _boundHandlers.keyup);
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
        document.removeEventListener('keyup', _boundHandlers.keyup);
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
            _shiftHeld = !!e.shiftKey;
            const snapped = _applySnap(w.x, w.y);
            // Check close — click near first vertex
            if (_vertices.length >= 3) {
                const p0 = worldToCanvas(_vertices[0][0], _vertices[0][1]);
                const pc = worldToCanvas(snapped.x, snapped.y);
                if (Math.hypot(p0.x - pc.x, p0.y - pc.y) < 15) {
                    _closed = true;
                    _tool = 'select';
                    _coordPreviewPos = null;
                    _updateToolButtons();
                    _updateRightPanel();
                    _scheduleRender();
                    return;
                }
            }
            // Click near existing vertex (not first) → delete it
            for (let i = 1; i < _vertices.length; i++) {
                const pv = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                if (Math.hypot(cx - pv.x, cy - pv.y) < 10) {
                    _vertices.splice(i, 1);
                    _coordPreviewPos = null;
                    _scheduleRender();
                    _updateRightPanel();
                    return;
                }
            }
            _vertices.push([snapped.x, snapped.y]);
            _coordPreviewPos = null;
            _scheduleRender();
            _updateRightPanel();
            return;
        }

        // Rotation handle hit test (before model hit test)
        // Uses _getRotateRingParams() — same function as drawing — for exact alignment
        if (_tool === 'select') {
            const _allRot = window.cfdprojStateV4.allModels(state);
            for (let _ri = 0; _ri < _allRot.length; _ri++) {
                const _rm = _allRot[_ri];
                const _rrp = _getRotateRingParams(_rm);
                if (!_rrp) continue;
                const _rdistToCenter = Math.hypot(cx - _rrp.centroid.x, cy - _rrp.centroid.y);
                const _rdistToRing = Math.abs(_rdistToCenter - _rrp.radius);
                const _rdistToHandle = Math.hypot(cx - _rrp.hx, cy - _rrp.hy);
                if (_rdistToRing < 10 || _rdistToHandle < 14) {
                    if (window.pushUndoCheckpoint) pushUndoCheckpoint('2dRotate');
                    _rotateHoverModelId = null;
                    _canvas.style.cursor = 'grabbing';
                    const _robb = getModelOBB(_rm);
                    _rotateHandle = {
                        id: _rm.uid,
                        // 2026-04-18 Y-up: plan canvas Y = world Z (center.z).
                        centroidX: _robb.center.x, centroidY: _robb.center.z,
                        startAngle: _rrp.angle, currentAngle: _rrp.angle,
                        startQuat: { ...(_rm.geometry.quaternion || {x:0,y:0,z:0,w:1}) },
                        startAnchorX: _rm.geometry.location.x, startAnchorY: _rm.geometry.location.z,
                        // 2026-04-19: canvas-visual rotation sense (CW+) — no Y-flip on cy;
                        // must match _getRotateRingParams handle placement.
                        dragStartAngle: Math.atan2(cy - _rrp.centroid.y, cx - _rrp.centroid.x)
                    };
                    // Collect all selected models with rotate enabled for multi-rotate
                    var _selRotateIds = (window.getSelectedModelIds ? getSelectedModelIds() : [_rm.uid])
                        .filter(function(id) {
                            var mm = window.getModelById ? getModelById(id) : null;
                            if (!mm) return false;
                            var rt = window.ensureModelRotateTool ? ensureModelRotateTool(mm) : null;
                            return rt && rt.enabled && rt.axes && rt.axes.yaw;
                        });
                    _rotateHandle._targets = _selRotateIds.map(function(id) {
                        var mm = getModelById(id);
                        var _robb2 = window.getModelOBB ? getModelOBB(mm) : null;
                        return {
                            id: id,
                            centroidX: _robb2 ? _robb2.center.x : mm.geometry.location.x,
                            // 2026-04-18 Y-up: plan canvas Y = world Z.
                            centroidY: _robb2 ? _robb2.center.z : mm.geometry.location.z,
                            startQuat: { ...(mm.geometry.quaternion || {x:0,y:0,z:0,w:1}) },
                            startAnchorX: mm.geometry.location.x,
                            startAnchorY: mm.geometry.location.z
                        };
                    });
                    _scheduleRender();
                    return;
                }
            }
        }

        // Model hit test (RACK/ACU/BOX only — not baffle/wall/floor)
        if (_tool === 'select') {
            const DRAGGABLE_KINDS = _DRAGGABLE_KINDS;
            const _allHit = window.cfdprojStateV4.allModels(state);
            for (let mi = 0; mi < _allHit.length; mi++) {
                const m = _allHit[mi];
                if ((m.meta && m.meta.enabled === false) || m.isRoomBoundary || !DRAGGABLE_KINDS.has(m.kind)) continue;
                // Use OBB for rotated models
                let hitModel = false;
                if (window.hasModelRotation && hasModelRotation(m) && window.getModelOBB && window.getOBBCorners) {
                    const obb = getModelOBB(m);
                    const corners = getOBBCorners(obb);
                    let mnX = Infinity, mxX = -Infinity, mnY = Infinity, mxY = -Infinity;
                    // 2026-04-18 Y-up: plan canvas Y = world Z (c.z).
                    corners.forEach(c => { if(c.x<mnX)mnX=c.x;if(c.x>mxX)mxX=c.x;if(c.z<mnY)mnY=c.z;if(c.z>mxY)mxY=c.z; });
                    const tl = worldToCanvas(mnX, mxY), br = worldToCanvas(mxX, mnY);
                    hitModel = cx >= tl.x && cx <= br.x && cy >= tl.y && cy <= br.y;
                } else {
                    // 2026-04-18 Y-up: plan canvas Y = world Z (location.z + size.z).
                    const tl = worldToCanvas(m.geometry.location.x, m.geometry.location.z + m.geometry.size.z);
                    const br = worldToCanvas(m.geometry.location.x + m.geometry.size.x, m.geometry.location.z);
                    // Add 5px padding for easier clicking on small projections
                    hitModel = cx >= tl.x - 5 && cx <= br.x + 5 && cy >= tl.y - 5 && cy <= br.y + 5;
                }
                if (hitModel) {
                    var moveTool = window.ensureModelMoveTool ? ensureModelMoveTool(m) : null;
                    // Already selected with move enabled → start multi-drag
                    if (_isModel2dSelected(m.uid) && moveTool && moveTool.enabled) {
                        var _selIds = window.getSelectedModelIds ? getSelectedModelIds() : [m.uid];
                        _dragModels = _selIds.filter(function(id) {
                            var mm = window.getModelById ? getModelById(id) : null;
                            if (!mm) return false;
                            var mt = window.ensureModelMoveTool ? ensureModelMoveTool(mm) : null;
                            return mt && mt.enabled;
                        }).map(function(id) {
                            var mm = getModelById(id);
                            // 2026-04-18 Y-up: plan canvas Y = world Z.
                            return { id: id, anchorStartX: mm.geometry.location.x, anchorStartY: mm.geometry.location.z };
                        });
                        _dragStart2d = { x: w.x, y: w.y };
                        _dragModel = null;
                        _scheduleRender();
                        return;
                    }
                    // Ctrl/Shift: toggle multi-select
                    if (e.ctrlKey || e.metaKey || e.shiftKey) {
                        var currentIds = window.getSelectedModelIds ? getSelectedModelIds() : [];
                        var idSet = new Set(currentIds);
                        if (idSet.has(m.uid)) { idSet.delete(m.uid); } else { idSet.add(m.uid); }
                        var nextIds = window.cfdprojStateV4.allModels(state).map(function(x) { return x.uid; }).filter(function(id) { return idSet.has(id); });
                        var anchor = (window.uiState.selection && window.uiState.selection.anchorModelId) || m.uid;
                        if (window.setSelection) setSelection('model', nextIds[0] || null, null, null, null, null, null, null, null, null, null, null, nextIds, anchor);
                    } else {
                        // Single select (no modifier)
                        if (window.setSelection) setSelection('model', m.uid, null, null, null, null, null, null, null, null, null, null, [m.uid], m.uid);
                    }
                    _selectedModelId = null;
                    _dragModel = null;
                    _dragModels = null;
                    _selectedVertex = -1;
                    _selectedEdge = -1;
                    _boxSelectedVertices.clear();
                    _boxSelectedEdges.clear();
                    // setSelection() already rendered the model inspector into #prop-content
                    // (its fast path calls _updateInspectorOnly), so a second
                    // _updateRightPanel() here would just rebuild the same inspector — a
                    // redundant ~40ms on dense racks. Skip it; only re-render the canvas.
                    _scheduleRender();
                    return;
                }
            }
            // No model hit in select mode — deselect any selected model
            if (window.getSelectedModelIds && getSelectedModelIds().length) {
                _selectedModelId = null;
                if (window.setSelection) setSelection('project');
                _scheduleRender();
                _updateRightPanel();
            }
        }

        if (_tool === 'select' && _vertices.length >= 2) {
            // Hit test vertices (works for both closed and open polygons)
            for (let i = 0; i < _vertices.length; i++) {
                const p = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                if (Math.hypot(cx - p.x, cy - p.y) < 10) {
                    _selectedVertex = i;
                    _selectedEdge = -1;
                    _dragVertex = i;
                    _dragStart = { x: _vertices[i][0], y: _vertices[i][1] };
                    if (window.pushUndoCheckpoint) pushUndoCheckpoint('2dDragVertex');
                    _syncVerticesToState();
                    _updateRightPanel();
                    _scheduleRender();
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
                    if (window.pushUndoCheckpoint) pushUndoCheckpoint('2dDragEdge');
                    _syncVerticesToState();
                    _updateRightPanel();
                    _scheduleRender();
                    return;
                }
            }
            // Start box selection (nothing was hit) — deselect model globally
            _selectedVertex = -1;
            _selectedEdge = -1;
            if (window.getSelectedModelIds && getSelectedModelIds().length) {
                _selectedModelId = null;
                if (window.setSelection) setSelection('project');
            }
            _boxSelectStart = { x: cx, y: cy };
            _boxSelectEnd = { x: cx, y: cy };
            _boxSelectedVertices.clear();
            _boxSelectedEdges.clear();
            _updateRightPanel();
            _scheduleRender();
        }
    }

    function _onMouseMove(e) {
        _shiftHeld = !!e.shiftKey;
        const rect = _canvas.getBoundingClientRect();
        const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
        const w = canvasToWorld(cx, cy);
        _mouseWorld = w;

        if (_isPanning && _panStart) {
            const dx = (e.clientX - _panStart.x) / _view.scale;
            const dy = -(e.clientY - _panStart.y) / _view.scale;
            _view.offsetX = _panStart.ox - dx;
            _view.offsetY = _panStart.oy - dy;
            _scheduleRender();
            return;
        }

        if (_dragVertex >= 0) {
            const snapped = _applySnap(w.x, w.y);
            _vertices[_dragVertex] = [snapped.x, snapped.y];
            _scheduleRender();
            _updateRightPanel();
            return;
        }

        if (_dragEdge >= 0 && _dragStart) {
            // Snap the first vertex of the edge, then move second by same delta
            const i = _dragEdge, j = (i + 1) % _vertices.length;
            const rawNewX = _vertices[i][0] + (w.x - _dragStart.x);
            const rawNewY = _vertices[i][1] + (w.y - _dragStart.y);
            const snapped = _applySnap(rawNewX, rawNewY);
            const sdx = snapped.x - _vertices[i][0], sdy = snapped.y - _vertices[i][1];
            _vertices[i] = [_vertices[i][0] + sdx, _vertices[i][1] + sdy];
            _vertices[j] = [_vertices[j][0] + sdx, _vertices[j][1] + sdy];
            _dragStart = { x: w.x, y: w.y };
            _scheduleRender();
            _updateRightPanel();
            return;
        }

        if (_rotateHandle) {
            const _rcCanvas = worldToCanvas(_rotateHandle.centroidX, _rotateHandle.centroidY);
            // 2026-04-19: canvas-visual rotation sense (CW+), matches dragStartAngle and handle draw.
            const _rmAngle = Math.atan2(cy - _rcCanvas.y, cx - _rcCanvas.x);
            const _rdAngle = _rmAngle - _rotateHandle.dragStartAngle;
            let _rfAngle = _rotateHandle.startAngle + _rdAngle;
            if (_shiftHeld) _rfAngle = Math.round(_rfAngle / (Math.PI / 12)) * (Math.PI / 12);
            if (_rotationSnap) {
                // Snap to integer degrees (1°, 2°, 3° ... 360°)
                var _deg1 = Math.PI / 180;
                _rfAngle = Math.round(_rfAngle / _deg1) * _deg1;
            }
            _rotateHandle.currentAngle = _rfAngle;
            var _totalDA = _rfAngle - _rotateHandle.startAngle;
            var _rotateTargets = _rotateHandle._targets || [_rotateHandle];
            _rotateTargets.forEach(function(tgt) {
                var _rm = window.getModelById ? getModelById(tgt.id) : null;
                if (!_rm) return;
                // 2026-04-18 Y-up: 2D plan rotation is around world-Y axis (vertical), not Z.
                var _dq = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), _totalDA);
                var _sq = new THREE.Quaternion(tgt.startQuat.x, tgt.startQuat.y, tgt.startQuat.z, tgt.startQuat.w);
                var _nq = _dq.clone().multiply(_sq);
                if (!_rm.geometry) _rm.geometry = {};
                _rm.geometry.quaternion = { x: _nq.x, y: _nq.y, z: _nq.z, w: _nq.w };
                var _cos = Math.cos(_totalDA), _sin = Math.sin(_totalDA);
                var _adx = tgt.startAnchorX - tgt.centroidX;
                var _ady = tgt.startAnchorY - tgt.centroidY;
                // 2026-04-19 Y-up FIX: the anchor-rotation formula must match
                // `setFromAxisAngle(Y, θ)` geometry rotation. For +Y rotation
                // in right-handed Y-up, (X,Z) → (X·cos + Z·sin, -X·sin + Z·cos).
                // Prior code used the Z-up XY-plane matrix (opposite sign on
                // the sin terms) — kept the centroid drifting away from its
                // original location instead of rotating-in-place.
                _rm.geometry.location.x = tgt.centroidX + _adx * _cos + _ady * _sin;
                _rm.geometry.location.z = tgt.centroidY - _adx * _sin + _ady * _cos;
                if (window.syncModelRenderTransform) syncModelRenderTransform(_rm);
                if (window.invalidateBBoxCache) invalidateBBoxCache(_rm.uid);
            });
            if (window.updateUI) updateUI();
            _scheduleRender();
            return;
        }

        if (_dragModels && _dragModels.length && _dragStart2d) {
            if (!_dragModels._undoPushed) {
                if (window.pushUndoCheckpoint) pushUndoCheckpoint('2dMoveMultiModel');
                _dragModels._undoPushed = true;
            }
            var dx = w.x - _dragStart2d.x, dy = w.y - _dragStart2d.y;
            _dragModels.forEach(function(dm) {
                var mm = window.getModelById ? getModelById(dm.id) : null;
                if (!mm) return;
                mm.geometry.location.x = dm.anchorStartX + dx;
                // 2026-04-18 Y-up: plan canvas Y = world Z.
                mm.geometry.location.z = dm.anchorStartY + dy;
                // V4: a rack's geometry IS mm.geometry (no separate rack.geometry sub-object),
                // so the location write above is the single source of truth.
                if (window.syncModelRenderTransform) syncModelRenderTransform(mm);
                if (window.invalidateBBoxCache) invalidateBBoxCache(mm.uid);
            });
            if (window.updateUI) updateUI();
            _scheduleRender();
            return;
        }

        if (_dragModel) {
            if (!_dragModel.undoPushed) {
                if (window.pushUndoCheckpoint) pushUndoCheckpoint('2dMoveModel');
                _dragModel.undoPushed = true;
            }
            const dx = w.x - _dragModel.startX, dy = w.y - _dragModel.startY;
            const m = window.getModelById ? getModelById(_dragModel.id) : window.cfdprojStateV4.allModels(state).find(x => x.uid === _dragModel.id);
            if (m) {
                // 2026-04-18 Y-up: drag in plan (canvas X/Y) maps to world X/Z.
                let nx = _dragModel.anchorStartX + dx, ny = _dragModel.anchorStartY + dy;
                // Apply snap: grid snap OR edge/vertex snap against other models
                if (_gridSnap) {
                    nx = Math.round(nx / _gridSpacing) * _gridSpacing;
                    ny = Math.round(ny / _gridSpacing) * _gridSpacing;
                }
                if (_vertexSnap) {
                    // Snap model corners to other model corners
                    // Plan coords: [world_x, world_z]
                    const myCorners = [[nx, ny], [nx + m.geometry.size.x, ny], [nx + m.geometry.size.x, ny + m.geometry.size.z], [nx, ny + m.geometry.size.z]];
                    const snapR = 10 / _view.scale;
                    let bestD = snapR, bestDx = 0, bestDy = 0;
                    window.cfdprojStateV4.allModels(state).forEach(other => {
                        if (other.uid === m.uid || (other.meta && other.meta.enabled === false)) return;
                        const oloc = other.geometry.location, osize = other.geometry.size;
                        const oc = [[oloc.x, oloc.z], [oloc.x + osize.x, oloc.z],
                                    [oloc.x + osize.x, oloc.z + osize.z], [oloc.x, oloc.z + osize.z]];
                        myCorners.forEach(([mx, my]) => {
                            oc.forEach(([ox, oy]) => {
                                const d = Math.hypot(mx - ox, my - oy);
                                if (d < bestD) { bestD = d; bestDx = ox - mx; bestDy = oy - my; }
                            });
                        });
                    });
                    if (bestD < snapR) { nx += bestDx; ny += bestDy; }
                }
                if (_edgeAlign) {
                    // Snap model edges to other model edges (face-to-face equivalent)
                    const snapT = 8 / _view.scale;
                    const myEdgesX = [nx, nx + m.geometry.size.x];
                    const myEdgesY = [ny, ny + m.geometry.size.z];
                    let bestSnapX = null, bestDistX = snapT;
                    let bestSnapY = null, bestDistY = snapT;
                    window.cfdprojStateV4.allModels(state).forEach(other => {
                        if (other.uid === m.uid || (other.meta && other.meta.enabled === false)) return;
                        const ox = [other.geometry.location.x, other.geometry.location.x + other.geometry.size.x];
                        // 2026-04-18 Y-up: plan Y = world Z (location.z + size.z).
                        const oy = [other.geometry.location.z, other.geometry.location.z + other.geometry.size.z];
                        myEdgesX.forEach(mex => { ox.forEach(oex => {
                            const d = Math.abs(mex - oex);
                            if (d < bestDistX) { bestDistX = d; bestSnapX = oex - mex; }
                        }); });
                        myEdgesY.forEach(mey => { oy.forEach(oey => {
                            const d = Math.abs(mey - oey);
                            if (d < bestDistY) { bestDistY = d; bestSnapY = oey - mey; }
                        }); });
                    });
                    if (bestSnapX !== null) nx += bestSnapX;
                    if (bestSnapY !== null) ny += bestSnapY;
                }
                // Micrometre grid, like the 3D drag (transform_tools.quantize):
                // a plan drag must not leave float dust the export turns into a seam.
                const _q = (window.transformTools && window.transformTools.quantize) || (v => Math.round(v * 1e6) / 1e6);
                m.geometry.location.x = _q(nx);
                // 2026-04-18 Y-up: plan canvas Y = world Z.
                m.geometry.location.z = _q(ny);
                if (window.syncModelRenderTransform) syncModelRenderTransform(m);
                if (window.invalidateBBoxCache) invalidateBBoxCache(m.uid);
            }
            _scheduleRender();
            return;
        }

        // Box selection drag
        if (_boxSelectStart && _tool === 'select') {
            const rect2 = _canvas.getBoundingClientRect();
            _boxSelectEnd = { x: e.clientX - rect2.left, y: e.clientY - rect2.top };
            // Determine which vertices/edges are inside the box
            _boxSelectedVertices.clear();
            _boxSelectedEdges.clear();
            const x1 = Math.min(_boxSelectStart.x, _boxSelectEnd.x);
            const x2 = Math.max(_boxSelectStart.x, _boxSelectEnd.x);
            const y1 = Math.min(_boxSelectStart.y, _boxSelectEnd.y);
            const y2 = Math.max(_boxSelectStart.y, _boxSelectEnd.y);
            for (let i = 0; i < _vertices.length; i++) {
                const p = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                if (p.x >= x1 && p.x <= x2 && p.y >= y1 && p.y <= y2) {
                    _boxSelectedVertices.add(i);
                }
            }
            if (_closed) {
                for (let i = 0; i < _vertices.length; i++) {
                    const j = (i + 1) % _vertices.length;
                    const pa = worldToCanvas(_vertices[i][0], _vertices[i][1]);
                    const pb = worldToCanvas(_vertices[j][0], _vertices[j][1]);
                    if (pa.x >= x1 && pa.x <= x2 && pa.y >= y1 && pa.y <= y2 &&
                        pb.x >= x1 && pb.x <= x2 && pb.y >= y1 && pb.y <= y2) {
                        _boxSelectedEdges.add(i);
                    }
                }
            }
            _scheduleRender();
            return;
        }

        // Rotation ring hover detection (uses same _getRotateRingParams as drawing)
        if (_tool === 'select' && !_rotateHandle && !_dragModel) {
            let _foundHover = null;
            const _allHover = window.cfdprojStateV4.allModels(state);
            for (let _hi = 0; _hi < _allHover.length; _hi++) {
                const _hrrp = _getRotateRingParams(_allHover[_hi]);
                if (!_hrrp) continue;
                const _hdist = Math.abs(Math.hypot(cx - _hrrp.centroid.x, cy - _hrrp.centroid.y) - _hrrp.radius);
                const _hDistHandle = Math.hypot(cx - _hrrp.hx, cy - _hrrp.hy);
                if (_hdist < 12 || _hDistHandle < 14) { _foundHover = _allHover[_hi].uid; break; }
            }
            if (_foundHover !== _rotateHoverModelId) {
                _rotateHoverModelId = _foundHover;
                _canvas.style.cursor = _foundHover ? 'grab' : '';
                _scheduleRender();
            }
        }

        // Preview render for drawing mode
        if (_tool === 'draw' && !_closed) _scheduleRender();
    }

    function _onMouseUp(e) {
        _isPanning = false;
        _panStart = null;
        // Sync vertices to state after vertex/edge drag so undo captures the change.
        // Note: rebuildGeometry() is intentionally NOT called here — 3D scene updates
        // only on exit/apply to avoid expensive rebuilds during interactive editing.
        if ((_dragVertex >= 0 || _dragEdge >= 0) && _closed) {
            _syncVerticesToState();
            _generateRoomModels();
            if (window.requestRender) requestRender();
            if (window.logOperation) logOperation('room2d.dragVertex', 'Drag room vertex/edge in 2D', {}, 'drag');
        }
        _dragVertex = -1;
        _dragEdge = -1;
        _dragStart = null;
        if (_boxSelectStart || _boxSelectEnd) {
            _boxSelectStart = null;
            _boxSelectEnd = null;
            _scheduleRender();
        }
        // Keep _boxSelectedVertices and _boxSelectedEdges for Delete key
        if (_rotateHandle) {
            if (window.requestRender) requestRender();
            var _rTargets = _rotateHandle._targets || [_rotateHandle];
            _rTargets.forEach(function(tgt) {
                if (window.rebuildSingleModel) rebuildSingleModel(tgt.id);
            });
            _rotateHandle = null;
            _canvas.style.cursor = '';
        }
        if (_dragModel) {
            if (window.requestRender) requestRender();
            _dragModel = null;
        }
        _dragModels = null;
        _dragStart2d = null;
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
        _scheduleRender();
    }

    function _onKeyDown(e) {
        if (!_active) return;
        // Prevent Ctrl+Z/Y from triggering global undo while in 2D mode
        // Allow Ctrl+Z/Y to pass through to global undo/redo handler,
        // then refresh 2D editor state from restored state
        if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'y' || e.key === 'Z')) {
            // Let the global handler run first, then sync our state
            setTimeout(function() {
                if (state.geometry.plane && state.geometry.plane.length >= 3) {
                    _vertices = state.geometry.plane.map(v => [v[0], v[1]]);
                    _closed = true;
                    _height = (state.geometry.planeHeight != null ? state.geometry.planeHeight : state.geometry.height) || _height;
                } else {
                    _vertices = [];
                    _closed = false;
                }
                _scheduleRender();
                _updateRightPanel();
            }, 50);
            return; // don't preventDefault — let global handler process
        }
        // Tab key: focus coordinate tooltip inputs for precise entry
        if (e.key === 'Tab' && _tool === 'draw' && !_closed) {
            e.preventDefault();
            var xInput = document.getElementById('r2d-coord-x');
            if (xInput) { xInput.focus(); xInput.select(); }
            return;
        }
        // Space key: confirm vertex at current/preview position
        if (e.key === ' ' && _tool === 'draw' && !_closed) {
            e.preventDefault();
            _confirmCoordVertex();
            return;
        }
        if (e.key === 'Enter' && !_closed && _vertices.length >= 3) {
            e.preventDefault();
            _closed = true;
            _tool = 'select';
            _updateToolButtons();
            _updateRightPanel();
            _scheduleRender();
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            // Switch to select mode without deleting vertices
            _tool = 'select';
            _updateToolButtons();
            _scheduleRender();
            _updateRightPanel();
        }
        if ((e.key === 'Delete' || e.key === 'Backspace') && _vertices.length >= 2) {
            e.preventDefault();
            if (_selectedVertex >= 0) {
                // Delete vertex
                _vertices.splice(_selectedVertex, 1);
                _selectedVertex = -1;
                if (_vertices.length < 3) _closed = false;
                if (_vertices.length < 2) { _vertices = []; }
            } else if (_selectedEdge >= 0) {
                // Delete edge → remove the connection but keep both endpoints
                // This breaks the polygon open between the two endpoints
                // Reorder vertices so the break is at the end (between last and first)
                const breakAt = _selectedEdge;
                const reordered = [];
                for (let k = 0; k < _vertices.length; k++) {
                    reordered.push(_vertices[(breakAt + 1 + k) % _vertices.length]);
                }
                _vertices = reordered;
                _selectedEdge = -1;
                _closed = false;
            }
            _scheduleRender();
            _updateRightPanel();
        }
        // Batch delete box-selected vertices
        if ((e.key === 'Delete' || e.key === 'Backspace') && (_boxSelectedVertices.size > 0 || _boxSelectedEdges.size > 0)) {
            e.preventDefault();
            if (_boxSelectedVertices.size > 0) {
                // Delete vertices in reverse order to preserve indices
                const sorted = [..._boxSelectedVertices].sort((a, b) => b - a);
                sorted.forEach(i => { if (i < _vertices.length) _vertices.splice(i, 1); });
                _closed = false;
            }
            _boxSelectedVertices.clear();
            _boxSelectedEdges.clear();
            _selectedVertex = -1;
            _selectedEdge = -1;
            if (_vertices.length < 2) _vertices = [];
            _scheduleRender();
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
        // 2D/3D parity: when a WHOLE model is selected, the 2D sidebar must equal the 3D
        // sidebar — delegate to the shared inspector dispatch. Only 'model' is delegated:
        // the 2D editor only ever produces 'model' (clicking a projection) or 'project'
        // (deselect). Sub-element types (face/region/rackSlot/...) only ever arrive as
        // STALE state carried in from the 3D viewport, and getSelectedModel() is
        // type-agnostic (truthy for any modelId), so delegating them would hijack the 2D
        // vertex/edge/room panel (the 2D deselect path is gated on getSelectedModelIds(),
        // which is empty for non-'model' types, so it never clears them). Letting them fall
        // through renders the correct 2D panel; clicking a projection re-selects as 'model'.
        const _sel = window.uiState && window.uiState.selection;
        if (_sel && _sel.type === 'model' && window.getSelectedModel && window.getSelectedModel() && window._updateInspectorOnly) {
            window._updateInspectorOnly();
            return;
        }
        // The btn.room_2d_editor translation already carries the 🏠 emoji, so use it
        // directly — do not prepend another (that double-stacked the emoji once window.t
        // resolved to the real translator).
        title.textContent = (window.t ? t('btn.room_2d_editor') : '\u{1F3E0} Room 2D Editor');

        let html = '';
        // Room height
        html += '<div class="prop-group"><div class="prop-title" style="color:var(--accent3)">' + (window.t ? t('room2d.room_settings') : 'Room Settings') + '</div>';
        var _step = window.getLengthStep ? getLengthStep() : 0.1;
        html += '<div class="prop-row"><label>' + (window.t ? t('label.room_height') : 'Height') + ' (' + _unitLabel() + ')</label><input type="number" step="' + _step + '" min="' + _step + '" value="' + _fmt(_height) + '" onchange="room2d.setHeight(parseFloat(this.value))"></div>';
        html += '</div>';

        // Selected vertex
        if (_selectedVertex >= 0 && _selectedVertex < _vertices.length) {
            const v = _vertices[_selectedVertex];
            html += '<div class="prop-group"><div class="prop-title">Selected Vertex #' + (_selectedVertex + 1) + '</div>';
            html += '<div class="prop-row"><label>X (' + _unitLabel() + ')</label><input type="number" step="' + _step + '" value="' + _fmt(v[0]) + '" onchange="room2d.setVertexCoord(' + _selectedVertex + ', 0, parseFloat(this.value))"></div>';
            html += '<div class="prop-row"><label>Y (' + _unitLabel() + ')</label><input type="number" step="' + _step + '" value="' + _fmt(v[1]) + '" onchange="room2d.setVertexCoord(' + _selectedVertex + ', 1, parseFloat(this.value))"></div>';
            html += '</div>';
        }

        // Selected edge
        if (_selectedEdge >= 0 && _selectedEdge < _vertices.length && _closed) {
            const a = _vertices[_selectedEdge], b = _vertices[(_selectedEdge + 1) % _vertices.length];
            const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
            const angle = Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
            html += '<div class="prop-group"><div class="prop-title">Selected Edge #' + (_selectedEdge + 1) + '</div>';
            html += '<div class="prop-row"><label>Length</label><span>' + len.toFixed(3) + ' m</span></div>';
            html += '<div class="prop-row"><label>' + (window.t ? t('room2d.edge_angle') : 'Angle') + '</label><span>' + angle.toFixed(1) + '\u00B0</span></div>';
            html += '</div>';
        }

        // Polygon info
        if (_vertices.length >= 3 && _closed) {
            const perimeter = _vertices.reduce((s, v, i) => {
                const w = _vertices[(i + 1) % _vertices.length];
                return s + Math.hypot(w[0] - v[0], w[1] - v[1]);
            }, 0);
            const area = window.computePolygonArea ? computePolygonArea(_vertices) : 0;
            html += '<div class="prop-group"><div class="prop-title">' + (window.t ? t('room2d.polygon_info') : 'Polygon Info') + '</div>';
            html += '<div class="prop-row"><label>' + (window.t ? t('room2d.vertices_count') : 'Vertices') + '</label><span>' + _vertices.length + '</span></div>';
            html += '<div class="prop-row"><label>' + (window.t ? t('room2d.perimeter') : 'Perimeter') + '</label><span>' + perimeter.toFixed(2) + ' m</span></div>';
            html += '<div class="prop-row"><label>' + (window.t ? t('room2d.area') : 'Area') + '</label><span>' + area.toFixed(2) + ' m\u00B2</span></div>';
            html += '</div>';
        }

        // Apply / Cancel buttons
        html += '<div style="margin-top:16px; display:flex; flex-direction:column; gap:8px;">';
        const _canApply = _closed && _vertices.length >= 3;
        html += '<button class="btn primary" onclick="room2d.exitEditor(true)" style="background:rgba(0,212,255,0.2); border-color:var(--accent3); color:var(--accent3);' + (_canApply ? '' : ' opacity:0.4; cursor:not-allowed;') + '"' + (_canApply ? '' : ' disabled') + '>' + (window.t ? t('btn.apply_to_3d') : '\u2705 Apply to 3D') + '</button>';
        if (!_canApply) {
            html += '<div style="font-size:0.75rem; color:var(--error-color); text-align:center;">' + (window.t ? t('warn.polygon_not_closed') : 'Please close the polygon first (≥3 vertices)') + '</div>';
        }
        html += '<button class="btn" onclick="room2d.exitEditor(false)">' + (window.t ? t('btn.cancel_2d') : '\u274C Cancel') + '</button>';
        html += '</div>';

        prop.innerHTML = html;
    }

    // --- Public API ---
    room2d.setTool = function(tool) {
        _tool = tool;
        _updateToolButtons();
    };
    room2d._scheduleRender = function() { if (_active) _scheduleRender(); };
    // Public so _updateRightPanel can be re-asserted after a mutation that runs updateUI()
    // (which writes the 3D inspector into the shared #prop-content).
    room2d._updateRightPanel = function() { if (_active) _updateRightPanel(); };

    // Public query for active state — `_active` is closure-scoped so callers
    // can't read `room2d._active` directly. Bug 2026-04-19: enterRackDetail
    // checked `room2d._active` and always got undefined, so it never called
    // exitEditor when invoked from the 2D editor and the viewport stayed on
    // the 2D canvas.
    room2d.isActive = function() { return _active; };
    room2d.getDebugState = function() {
        return { tool: _tool, closed: _closed, vertices: _vertices.length, view: {..._view}, active: _active, viewRotation: _viewRotation, canvasWidth: _canvas ? _canvas.width : 0, canvasHeight: _canvas ? _canvas.height : 0, aisleLabels: _lastAisleLabels };
    };

    room2d.zoomToFit = function() {
        if (!_canvas) return;
        var pts = [];
        if (state.geometry.plane && state.geometry.plane.length >= 3) {
            state.geometry.plane.forEach(function(v) { pts.push([v[0], v[1]]); });
        } else if (state && window.cfdprojStateV4) {
            window.cfdprojStateV4.allModels(state).forEach(function(m) {
                if (m.meta && m.meta.enabled === false) return;
                const loc = m.geometry.location, size = m.geometry.size;
                pts.push([loc.x, loc.z]);
                pts.push([loc.x + (size.x || 0), loc.z + (size.z || 0)]);
            });
        }
        if (pts.length === 0) return;
        var rotatedPts = pts.map(function(p) { return _rotatePoint(p[0], p[1], _viewRotation); });
        var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        rotatedPts.forEach(function(p) {
            if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
            if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
        });
        _view.offsetX = (minX + maxX) / 2;
        _view.offsetY = (minY + maxY) / 2;
        var spanX = maxX - minX, spanY = maxY - minY;
        var margin = Math.max(spanX, spanY) * 0.05;
        _view.scale = Math.min(_canvas.width / (spanX + margin), _canvas.height / (spanY + margin));
        if (_view.scale < 1) _view.scale = 1;
        _scheduleRender();
    };

    room2d.setShowAxes = function(show) {
        _showAxes = !!show;
        _scheduleRender();
    };

    room2d.setAnnotations = function(show) {
        _showAnnotations = !!show;
        _scheduleRender();
    };

    room2d.setBgColor = function(color) {
        _bgColor = color || '#1a1e26';
        _scheduleRender();
    };

    room2d.exportCanvasPNG = function(width, height) {
        if (!_canvas) return null;
        var origW = _canvas.width, origH = _canvas.height;
        _canvas.width = width || 3840;
        _canvas.height = height || 2160;
        room2d.zoomToFit();
        _render();
        var dataUrl = _canvas.toDataURL('image/png');
        _canvas.width = origW;
        _canvas.height = origH;
        room2d.zoomToFit();
        _scheduleRender();
        return dataUrl;
    };

    room2d.setViewRotation = function(deg) {
        _viewRotation = ((deg % 360) + 360) % 360;
        room2d.zoomToFit();
    };

    room2d.rotateView90 = function() {
        _viewRotation = (_viewRotation + 90) % 360;
        room2d.zoomToFit();
        if (window.logOperation) logOperation('room2d.rotateView90', 'Rotate 2D view by 90°', { rotation: _viewRotation }, 'view');
    };
    room2d.toggleGridSnap = function() {
        _gridSnap = !_gridSnap;
        // Mutually exclusive: grid snap vs vertex/edge snap and rotation snap
        if (_gridSnap) { _vertexSnap = false; _edgeAlign = false; _rotationSnap = false; }
        _updateToolButtons();
    };
    room2d.toggleVertexSnap = function() {
        _vertexSnap = !_vertexSnap;
        // Mutually exclusive: vertex/edge snap vs grid snap
        if (_vertexSnap) _gridSnap = false;
        _updateToolButtons();
    };
    room2d.toggleEdgeAlign = function() {
        _edgeAlign = !_edgeAlign;
        if (_edgeAlign) _gridSnap = false;
        _updateToolButtons();
    };
    room2d.toggleRotationSnap = function() {
        _rotationSnap = !_rotationSnap;
        if (_rotationSnap) _gridSnap = false;  // mutually exclusive with grid snap
        // Sync to 3D rotation snap
        if (window.setRotationSnap3D) setRotationSnap3D(_rotationSnap);
        _updateToolButtons();
    };
    room2d._setRotationSnap = function(val) {
        _rotationSnap = !!val;
        if (_rotationSnap) _gridSnap = false;
        _updateToolButtons();
    };

    room2d.setGridSpacing = function(v) {
        _gridSpacing = Math.max(0.001, v);
        // Sync with 3D grid
        if (window.uiState.view) window.uiState.view.gridSpacing = _gridSpacing;
        _updateGridBadge();
        _scheduleRender();
    };
    room2d.previewGridSpacing = function(displayValue) {
        // Slider value is in display units — convert to internal (meters)
        const scale = window.getActiveUnitConfig ? getActiveUnitConfig().scale : 1;
        _gridSpacing = Math.max(0.001, parseFloat(displayValue) * scale);
        _updateGridBadge();
        _scheduleRender();
    };
    room2d.setGridSpacingFromSlider = function(displayValue) {
        const scale = window.getActiveUnitConfig ? getActiveUnitConfig().scale : 1;
        _gridSpacing = Math.max(0.001, parseFloat(displayValue) * scale);
        if (window.uiState.view) window.uiState.view.gridSpacing = _gridSpacing;
        _updateGridBadge();
        _scheduleRender();
    };
    room2d.editGridBadge = function(el) {
        const current = _fmt(_gridSpacing);
        const input = prompt((window.t ? t('toolbar.grid_spacing') : 'Grid Spacing') + ' (' + _unitLabel() + '):', current);
        if (input !== null) {
            const val = parseFloat(input);
            if (Number.isFinite(val) && val > 0) {
                const scale = window.getActiveUnitConfig ? getActiveUnitConfig().scale : 1;
                _gridSpacing = val * scale;
                if (window.uiState.view) window.uiState.view.gridSpacing = _gridSpacing;
                _updateGridBadge();
                _updateGridSlider();
                _scheduleRender();
            }
        }
    };
    function _updateGridBadge() {
        const badge = document.getElementById('room2d-grid-badge');
        if (badge) badge.textContent = _fmt(_gridSpacing) + ' ' + _unitLabel();
    }
    function _updateGridSlider() {
        const slider = document.getElementById('room2d-grid-slider');
        if (slider) {
            const scale = window.getActiveUnitConfig ? getActiveUnitConfig().scale : 1;
            slider.value = Math.round(_gridSpacing / scale);
        }
    }
    room2d.setHeight = function(v) {
        if (!Number.isFinite(v) || v <= 0) return;
        _height = window.fromDisplayLength ? fromDisplayLength(v) : v;
    };
    room2d.setVertexCoord = function(idx, axis, value) {
        if (idx >= 0 && idx < _vertices.length && Number.isFinite(value)) {
            _vertices[idx][axis] = window.fromDisplayLength ? fromDisplayLength(value) : value;
            _scheduleRender();
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
        _scheduleRender();
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
        setActive('room2d-btn-rotsnap', _rotationSnap);
    }

    // ===================== Self-Tests =====================
    // Run in browser console: room2d.runTests()
    room2d.runTests = function() {
        let pass = 0, fail = 0;
        function assert(name, actual, expected) {
            const a = JSON.stringify(actual), e = JSON.stringify(expected);
            if (a === e) { console.log('%c  PASS: ' + name, 'color:#98c379'); pass++; }
            else { console.log('%c  FAIL: ' + name + ' (got ' + a + ', expected ' + e + ')', 'color:#e06c75'); fail++; }
        }

        console.log('%c=== computePolygonArea ===', 'color:#61afef; font-weight:bold');
        assert('Triangle', +computePolygonArea([[0,0],[10,0],[5,8.66]]).toFixed(2), 43.30);
        assert('Rectangle', +computePolygonArea([[0,0],[10,0],[10,8],[0,8]]).toFixed(2), 80.00);
        assert('Unit square', +computePolygonArea([[0,0],[1,0],[1,1],[0,1]]).toFixed(2), 1.00);
        assert('Empty', computePolygonArea([]), 0);
        assert('L-shape', +computePolygonArea([[0,0],[6,0],[6,3],[3,3],[3,6],[0,6]]).toFixed(2), 27.00);

        console.log('%c=== syncFloorBaseFromPolygon ===', 'color:#61afef; font-weight:bold');
        // V4-shaped fixtures: geometry.{size,polygon}. syncFloorBaseFromPolygon reads
        // the outline (via the legacy polygon alias when present) and writes geometry.size.
        var m1 = {kind:'floor',geometry:{size:{x:0,y:0,z:0},polygon:[[0,0],[10,0],[5,8.66]]},polygon:[[0,0],[10,0],[5,8.66]]};
        syncFloorBaseFromPolygon(m1);
        assert('Triangle W', +m1.geometry.size.x.toFixed(2), 10.00);
        assert('Triangle D', +m1.geometry.size.z.toFixed(2), 8.66);
        var m2 = {kind:'wall',geometry:{size:{x:5,y:3,z:0},polygon:[[0,0],[10,0],[5,8]]},polygon:[[0,0],[10,0],[5,8]]};
        syncFloorBaseFromPolygon(m2);
        assert('Skip non-floor', m2.geometry.size.x, 5);

        console.log('%c=== getModelOBB ===', 'color:#61afef; font-weight:bold');
        // 2026-04-18 Y-up: size.x→X (half=1), size.y→Y (half=3), size.z→Z (half=2). Center=(1,3,2).
        // getModelOBB reads geometry.{location,size} plus the legacy transform alias.
        var obb1 = getModelOBB({geometry:{location:{x:0,y:0,z:0},size:{x:2,y:6,z:4}},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}});
        assert('Center', [+obb1.center.x.toFixed(1),+obb1.center.y.toFixed(1),+obb1.center.z.toFixed(1)], [1.0,3.0,2.0]);
        assert('Half', obb1.half, [1,3,2]);
        assert('8 corners', getOBBCorners(obb1).length, 8);

        console.log('%c=== obbOverlapSAT ===', 'color:#61afef; font-weight:bold');
        var mkOBB = (x) => getModelOBB({geometry:{location:{x:x,y:0,z:0},size:{x:2,y:2,z:2}},transform:{quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}}});
        assert('Overlapping', obbOverlapSAT(mkOBB(0), mkOBB(1), 1e-4), true);
        assert('Face-touching', obbOverlapSAT(mkOBB(0), mkOBB(2), 1e-4), false);
        assert('Separated', obbOverlapSAT(mkOBB(0), mkOBB(5), 1e-4), false);

        console.log('%c=== hasModelRotation ===', 'color:#61afef; font-weight:bold');
        assert('Identity', hasModelRotation({transform:{quaternion:{x:0,y:0,z:0,w:1}}}), false);
        assert('Z rot', hasModelRotation({transform:{quaternion:{x:0,y:0,z:0.383,w:0.924}}}), true);
        assert('No transform', hasModelRotation({}), false);

        console.log('%c=== normalizeState ===', 'color:#61afef; font-weight:bold');
        var s1 = normalizeState({models:[{kind:'floor',name:'F',polygon:'bad'}]});
        assert('Invalid polygon nulled', s1.models[0].polygon, null);
        var s2 = normalizeState({models:[{kind:'floor',name:'F',polygon:[[0,0],[1,0],[1,1]]}]});
        assert('Valid polygon preserved', s2.models[0].polygon.length, 3);
        var s3 = normalizeState({roomPolygon:{vertices:[[0,0]],height:3}});
        assert('Invalid roomPolygon nulled', s3.roomPolygon, null);
        var s4 = normalizeState({roomPolygon:{vertices:[[0,0],[10,0],[5,8]],height:3}});
        assert('Valid roomPolygon preserved', s4.roomPolygon.vertices.length, 3);

        console.log('%c=== Model Group CRUD ===', 'color:#61afef; font-weight:bold');
        // V4: the legacy construction view is read-only over the buckets, so the group
        // test snapshots/restores only window.uiState.groups. createModelGroup/getModelGroup
        // operate on member-id strings via the group map and need no bucket entries.
        var origGroups = window.uiState.groups;
        window.uiState.groups = [];
        _rebuildModelGroupMap();
        createModelGroup('TG', ['ta','tb']);
        assert('Group created', window.uiState.groups.length, 1);
        assert('Group members', window.uiState.groups[0].memberIds.length, 2);
        assert('Reverse lookup', getModelGroup('ta') !== null, true);
        dissolveModelGroup(window.uiState.groups[0].id);
        assert('Group dissolved', window.uiState.groups.length, 0);
        window.uiState.groups = origGroups;
        _rebuildModelGroupMap();

        console.log('%c=== Results: ' + pass + ' passed, ' + fail + ' failed ===', fail ? 'color:#e06c75; font-weight:bold' : 'color:#98c379; font-weight:bold');
        return { pass, fail };
    };

})();
