/**
 * Air-side pipe editor for the legacy (HTML) editor.
 *
 * The React editor implements the same object in TypeScript
 * (react/src/geometry/airPipe.ts + airPipeHandles.ts + AirPipeInspector.tsx);
 * this module is the HTML app's copy of that behaviour, written against the
 * page's globals the way js/liquid_mode.js is:
 *
 *   - a `pipe` (bucket `pipes`) is a swept solid along geometry.waypoints,
 *     which are LOCAL to geometry.location; round pipes carry diameterM,
 *     rectangular ones widthM/heightM; `joins` pairs snapped ends;
 *   - selecting a pipe shows a ball per waypoint (blue start, red end, yellow
 *     body). Pressing a ball attaches the shared translate gizmo: an END ball
 *     moves that end (a sideways pull drops a 90° corner where the drag
 *     began), a BODY ball moves the whole pipe; releasing an end near another
 *     pipe's free end snaps and joins the two (a ring previews the target);
 *   - the inspector edits shape, bore and the polyline in world coordinates.
 *
 * Globals read lazily (all installed by STL_generate.html): THREE, scene,
 * camera, renderer, controls, getModelById, allModels, pushUndoCheckpoint,
 * logOperation, updateUI, rebuildGeometry, requestRender, createTranslateGizmo,
 * uiState, t, formatLength, fromDisplayLength, getLengthStep, getLengthUnitLabel,
 * anchorControlsForModel, opacityToPercent, updateModelName, removeModel.
 */
(function () {
  'use strict';

  var START_COLOR = 0x2196F3, END_COLOR = 0xF44336, BODY_COLOR = 0xFFEB3B;
  var SNAP_DISTANCE = 0.12, UNJOIN_DISTANCE = 0.05, MIN_BORE = 0.01;
  var CORNER_MIN_DRAG = 0.03, CORNER_MIN_PERP = 0.02;

  // geometry_pure's helper scale registry (WP-S1), read defensively so the
  // editor still works (handles at their data size) if the shared API is
  // absent.
  function _pure() { return (typeof window !== 'undefined' && window.geometryPure) || null; }
  function _registerHelper(mesh, dataRadius) {
    var p = _pure();
    if (p && typeof p.registerHelper === 'function') p.registerHelper(mesh, dataRadius);
  }
  function _unregisterHelper(mesh) {
    var p = _pure();
    if (p && typeof p.unregisterHelper === 'function') p.unregisterHelper(mesh);
  }

  function num(v, d) { var n = Number(v); return isFinite(n) ? n : d; }
  function vec(v) { v = v || {}; return { x: num(v.x, 0), y: num(v.y, 0), z: num(v.z, 0) }; }
  function tr(k, vars) { return (typeof window.t === 'function') ? window.t(k, vars) : k; }
  function isPipe(m) { return !!m && m.kind === 'pipe'; }
  function pipeById(uid) {
    var m = (typeof window.getModelById === 'function') ? window.getModelById(uid) : null;
    return isPipe(m) ? m : null;
  }
  function allPipes() {
    var ms = (typeof window.allModels === 'function') ? window.allModels() : [];
    return ms.filter(isPipe);
  }

  // ── geometry ──────────────────────────────────────────────────────────────
  function geom(m) {
    var g = m.geometry || (m.geometry = {});
    if (!Array.isArray(g.waypoints)) g.waypoints = [];
    if (!Array.isArray(g.joins)) g.joins = [];
    return g;
  }
  function quat(m) { var q = (m.geometry && m.geometry.quaternion) || {}; return { x: num(q.x, 0), y: num(q.y, 0), z: num(q.z, 0), w: num(q.w, 1) }; }
  function rotate(q, v) {
    var x = q.x, y = q.y, z = q.z, w = q.w;
    var ix = w * v.x + y * v.z - z * v.y, iy = w * v.y + z * v.x - x * v.z, iz = w * v.z + x * v.y - y * v.x, iw = -x * v.x - y * v.y - z * v.z;
    return { x: ix * w + iw * -x + iy * -z - iz * -y, y: iy * w + iw * -y + iz * -x - ix * -z, z: iz * w + iw * -z + ix * -y - iy * -x };
  }
  function localToWorld(m, p) { var l = vec(m.geometry && m.geometry.location); var r = rotate(quat(m), vec(p)); return { x: r.x + l.x, y: r.y + l.y, z: r.z + l.z }; }
  function worldToLocal(m, p) { var l = vec(m.geometry && m.geometry.location); var q = quat(m); return rotate({ x: -q.x, y: -q.y, z: -q.z, w: q.w }, { x: p.x - l.x, y: p.y - l.y, z: p.z - l.z }); }
  function worldPoints(m) { return geom(m).waypoints.map(function (p) { return localToWorld(m, p); }); }
  function radius(m) { var g = geom(m); return g.shape === 'rect' ? Math.max(num(g.widthM, 0.2), num(g.heightM, 0.2)) / 2 : num(g.diameterM, 0.15) / 2; }
  function endOfIndex(m, i) { var n = geom(m).waypoints.length; return i === 0 ? 'start' : (i === n - 1 ? 'end' : null); }
  function endIndex(m, end) { return end === 'start' ? 0 : Math.max(0, geom(m).waypoints.length - 1); }
  function endWorld(m, end) { var pts = worldPoints(m); return pts.length ? pts[endIndex(m, end)] : null; }
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z); }
  function joinAt(m, end) { return geom(m).joins.filter(function (j) { return j && j.end === end; })[0] || null; }

  function writePointWorld(m, i, world) { var wps = geom(m).waypoints; if (i < 0 || i >= wps.length) return; wps[i] = worldToLocal(m, world); }
  function translate(m, d) { var g = geom(m); var l = vec(g.location); g.location = { x: l.x + d.x, y: l.y + d.y, z: l.z + d.z }; }
  function insertPoint(m, i) {
    var wps = geom(m).waypoints; if (!wps.length) return -1;
    i = Math.max(0, Math.min(i, wps.length - 1));
    var p;
    if (i < wps.length - 1) { var a = wps[i], b = wps[i + 1]; p = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 }; }
    else { var a2 = wps[i], pr = wps[i - 1] || { x: a2.x, y: a2.y - 0.3, z: a2.z }; var d = { x: a2.x - pr.x, y: a2.y - pr.y, z: a2.z - pr.z }; var len = Math.hypot(d.x, d.y, d.z) || 1; p = { x: a2.x + d.x / len * 0.3, y: a2.y + d.y / len * 0.3, z: a2.z + d.z / len * 0.3 }; }
    wps.splice(i + 1, 0, p); return i + 1;
  }
  function removePoint(m, i) { var wps = geom(m).waypoints; if (wps.length <= 2 || i < 0 || i >= wps.length) return false; wps.splice(i, 1); return true; }

  function unjoin(uid, end) {
    var m = pipeById(uid); if (!m) return;
    var mine = geom(m).joins; var j = null;
    for (var i = 0; i < mine.length; i++) if (mine[i].end === end) { j = mine[i]; mine.splice(i, 1); break; }
    if (!j) return;
    var other = pipeById(j.pipeUid); if (!other) return;
    var theirs = geom(other).joins;
    for (var k = theirs.length - 1; k >= 0; k--) if (theirs[k].pipeUid === uid && theirs[k].end === j.pipeEnd) theirs.splice(k, 1);
  }
  function join(uid, end, otherUid, otherEnd) {
    if (uid === otherUid) return false;
    var m = pipeById(uid), other = pipeById(otherUid); if (!m || !other) return false;
    var target = endWorld(other, otherEnd); if (!target) return false;
    unjoin(uid, end); unjoin(otherUid, otherEnd);
    writePointWorld(m, endIndex(m, end), target);
    geom(m).joins.push({ end: end, pipeUid: otherUid, pipeEnd: otherEnd });
    geom(other).joins.push({ end: otherEnd, pipeUid: uid, pipeEnd: end });
    return true;
  }
  function joinBroken(m, end) {
    var j = joinAt(m, end); if (!j) return false;
    var other = pipeById(j.pipeUid); var mine = endWorld(m, end); var theirs = other ? endWorld(other, j.pipeEnd) : null;
    if (!mine || !theirs) return true;
    return dist(mine, theirs) > UNJOIN_DISTANCE;
  }
  function freeEnds(exceptUid) {
    var out = [];
    allPipes().forEach(function (m) {
      if (m.uid === exceptUid || (m.meta && m.meta.enabled === false)) return;
      ['start', 'end'].forEach(function (end) {
        if (joinAt(m, end)) return;
        var w = endWorld(m, end); if (w) out.push({ uid: m.uid, end: end, world: w, radius: radius(m) });
      });
    });
    return out;
  }
  function findTarget(uid, world) {
    var best = null, bestD = SNAP_DISTANCE;
    freeEnds(uid).forEach(function (c) { var d = dist(c.world, world); if (d <= bestD) { best = c; bestD = d; } });
    return best;
  }

  // ── handles ───────────────────────────────────────────────────────────────
  var group = null, balls = [], gizmo = null, drag = null, ring = null, hovered = null, lastDragEnd = 0, rebuildQueued = false;
  var raycaster = null, pointer = null;

  function ensureGroup() {
    if (group || typeof THREE === 'undefined' || typeof scene === 'undefined') return;
    group = new THREE.Group(); group.name = 'airPipeHandles'; scene.add(group);
    raycaster = new THREE.Raycaster(); pointer = new THREE.Vector2();
  }
  function clearBalls() {
    balls.forEach(function (b) { _unregisterHelper(b); if (b.parent) b.parent.remove(b); b.geometry.dispose(); b.material.dispose(); });
    balls = []; hovered = null;
  }
  function hideRing() { if (!ring) return; _unregisterHelper(ring); scene.remove(ring); ring.geometry.dispose(); ring.material.dispose(); ring = null; }
  function showRing(at, r) {
    if (!ring) {
      // The ring geometry is already UNIT (inner 1, outer 1.35), so it only
      // needs the registry entry (WP-S1). One ring mesh is reused across snap
      // targets; re-registering updates its data radius instead of adding a
      // second entry. The explicit scale stays for the pre-sync frame.
      ring = new THREE.Mesh(new THREE.RingGeometry(1, 1.35, 32), new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.8, side: THREE.DoubleSide, depthTest: false }));
      ring.renderOrder = 999; scene.add(ring);
    }
    var s = Math.max(0.05, r * 1.2); ring.scale.set(s, s, s); ring.position.set(at.x, at.y, at.z); ring.lookAt(camera.position);
    _registerHelper(ring, s);
  }
  function ensureGizmo() {
    if (gizmo) return gizmo;
    if (typeof window.createTranslateGizmo !== 'function') return null;
    gizmo = window.createTranslateGizmo(null, { space: 'world', size: 0.7, onDragStart: onDragStart, onChange: onDragMove, onDragEnd: onDragEnd });
    return gizmo;
  }
  var attached = null;
  function attachTo(ball) {
    var gz = ensureGizmo(); if (!gz) return;
    ball.quaternion.identity(); gz.tc.setSpace('world'); gz.tc.attach(ball); attached = ball;
    if (typeof requestRender === 'function') requestRender();
  }
  function detach() { attached = null; if (gizmo) gizmo.tc.detach(); if (typeof controls !== 'undefined') controls.enabled = true; }
  function ballAt(i) { for (var k = 0; k < balls.length; k++) if (balls[k].userData.index === i) return balls[k]; return null; }
  function build(m) {
    ensureGroup(); if (!group) return; clearBalls();
    var pts = worldPoints(m), r = radius(m), n = pts.length;
    pts.forEach(function (p, i) {
      var end = endOfIndex(m, i); var isEnd = end !== null && n > 1;
      var rad = isEnd ? Math.max(0.03, r * 1.2) : Math.max(0.02, r * 0.8);
      var color = end === 'start' ? START_COLOR : (end === 'end' ? END_COLOR : BODY_COLOR);
      // Unit sphere + registry (WP-S1): shows at the bore-relative data
      // radius, shrinks with the camera up close. The baseline scale keeps
      // the pre-sync size; picking is unaffected (raycast sees the scaled
      // matrixWorld, and userData is untouched). TransformControls only
      // writes ball.position, so it never fights the synced scale.
      var ball = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12), new THREE.MeshBasicMaterial({ color: color, transparent: true, opacity: 0.6 }));
      ball.scale.setScalar(rad);
      ball.position.set(p.x, p.y, p.z);
      ball.userData = { isAirPipeHandle: true, uid: m.uid, index: i, end: end };
      _registerHelper(ball, rad);
      group.add(ball); balls.push(ball);
    });
    if (typeof requestRender === 'function') requestRender();
  }
  function moveOtherBalls(m, except) {
    var pts = worldPoints(m);
    balls.forEach(function (b) { if (b === except) return; var p = pts[b.userData.index]; if (p) b.position.set(p.x, p.y, p.z); });
  }
  function queueRebuild() {
    if (rebuildQueued) return; rebuildQueued = true;
    requestAnimationFrame(function () { rebuildQueued = false; if (typeof rebuildGeometry === 'function') rebuildGeometry(); if (typeof requestRender === 'function') requestRender(); });
  }

  function onDragStart() {
    var ball = attached; if (!ball) return;
    var m = pipeById(ball.userData.uid); if (!m) return;
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('dragAirPipePoint');
    m = pipeById(ball.userData.uid);   // the checkpoint may have swapped the live object
    var p = ball.position;
    drag = { uid: m.uid, ball: ball, startWorld: { x: p.x, y: p.y, z: p.z }, lastWorld: { x: p.x, y: p.y, z: p.z }, cornerInserted: false, target: null };
  }
  function onDragMove() {
    if (!drag || !attached) return;
    var ball = attached, ud = ball.userData, m = pipeById(drag.uid); if (!m) return;
    var now = { x: ball.position.x, y: ball.position.y, z: ball.position.z };
    if (ud.end === null) {
      translate(m, { x: now.x - drag.lastWorld.x, y: now.y - drag.lastWorld.y, z: now.z - drag.lastWorld.z });
    } else {
      var wps = geom(m).waypoints;
      if (!drag.cornerInserted && wps.length >= 2) {
        var disp = { x: now.x - drag.startWorld.x, y: now.y - drag.startWorld.y, z: now.z - drag.startWorld.z };
        var total = Math.hypot(disp.x, disp.y, disp.z);
        if (total > CORNER_MIN_DRAG) {
          var pts = worldPoints(m), isStart = ud.end === 'start';
          var nb = isStart ? pts[1] : pts[pts.length - 2];
          var ax = { x: drag.startWorld.x - nb.x, y: drag.startWorld.y - nb.y, z: drag.startWorld.z - nb.z };
          var al = Math.hypot(ax.x, ax.y, ax.z) || 1;
          var along = Math.abs((disp.x * ax.x + disp.y * ax.y + disp.z * ax.z) / al);
          var perp = Math.sqrt(Math.max(0, total * total - along * along));
          if (perp > along && perp > CORNER_MIN_PERP) {
            var c = wps[isStart ? 0 : wps.length - 1]; var corner = { x: c.x, y: c.y, z: c.z };
            if (isStart) wps.splice(1, 0, corner); else { wps.splice(wps.length - 1, 0, corner); ud.index = wps.length - 1; }
            drag.cornerInserted = true;
          }
        }
      }
      writePointWorld(m, ud.index, now);
      drag.target = findTarget(drag.uid, now);
      if (drag.target) showRing(drag.target.world, drag.target.radius); else hideRing();
    }
    drag.lastWorld = now;
    moveOtherBalls(m, ball);
    queueRebuild();
  }
  function onDragEnd() {
    lastDragEnd = Date.now();
    var state = drag; drag = null; hideRing();
    if (!state) return;
    var m = pipeById(state.uid); if (!m) return;
    var ud = state.ball.userData, keep = ud.index;
    if (ud.end !== null) {
      if (state.target) join(state.uid, ud.end, state.target.uid, state.target.end);
      else if (joinAt(m, ud.end) && joinBroken(m, ud.end)) unjoin(state.uid, ud.end);
    } else {
      ['start', 'end'].forEach(function (e) { if (joinAt(m, e) && joinBroken(m, e)) unjoin(state.uid, e); });
    }
    if (typeof logOperation === 'function') logOperation('dragAirPipePoint', 'Drag pipe point ' + state.uid + ' #' + keep, { modelId: state.uid, index: keep }, 'modify');
    if (typeof rebuildGeometry === 'function') rebuildGeometry();
    if (typeof updateUI === 'function') updateUI();
    var fresh = pipeById(state.uid); detach();
    if (fresh) { build(fresh); var b = ballAt(keep); if (b) attachTo(b); }
  }

  function setRay(event) {
    var rect = renderer.domElement.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
  }
  /** True when the press landed on a ball: the click is consumed. */
  function handleClick(event, maxDistance) {
    // A Ctrl/Shift/Cmd click is a selection gesture (add or remove a model),
    // never a handle grab. Checked before the post-drag guard too: a modifier
    // click on the active pipe's end ball used to grab the gizmo and leave the
    // selection unchanged. (Pressing a gizmo AXIS is gated on pointerdown.)
    if (event && (event.shiftKey || event.ctrlKey || event.metaKey)) return false;
    if (!balls.length || !raycaster) return false;
    if (Date.now() - lastDragEnd < 200) return true;
    setRay(event);
    var hits = raycaster.intersectObjects(balls, false);
    if (!hits.length || hits[0].distance >= (typeof maxDistance === 'number' ? maxDistance : Infinity)) return false;
    var chosen = hits[0].object;
    for (var i = 1; i < hits.length; i++) {
      if (hits[i].object.userData.end && !chosen.userData.end && hits[i].distance - hits[0].distance < 0.3) { chosen = hits[i].object; break; }
    }
    attachTo(chosen);
    return true;
  }
  function gizmoAxis() { return !!(gizmo && gizmo.tc.object && gizmo.tc.axis); }
  function hover(event) {
    if (drag || !balls.length || !raycaster) return;
    setRay(event);
    var h = raycaster.intersectObjects(balls, false)[0]; var hit = h ? h.object : null;
    if (hit === hovered) return;
    if (hovered) hovered.material.opacity = 0.6;
    hovered = hit; if (hovered) hovered.material.opacity = 1;
    if (typeof requestRender === 'function') requestRender();
  }
  function onSelectionChanged(sel) {
    // Handles exist for ONE selected pipe only. With several models selected
    // the group move/rotate tools own dragging, and balls left on the active
    // pipe would sit over the very spot a Ctrl/Shift click aims at.
    var single = !!sel && sel.type === 'model' && (!Array.isArray(sel.modelIds) || sel.modelIds.length <= 1);
    var uid = single ? sel.modelId : null;
    var m = uid ? pipeById(uid) : null;
    var keep = (attached && m && attached.userData.uid === uid) ? attached.userData.index : null;
    if (drag) return;
    detach();
    if (!m) { clearBalls(); hideRing(); return; }
    build(m);
    if (keep !== null) { var b = ballAt(keep); if (b) attachTo(b); }
  }

  // ── committed edits (inspector) ───────────────────────────────────────────
  function edit(action, uid, fn) {
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint(action);
    var m = pipeById(uid); if (!m) return;
    fn(m);
    if (typeof rebuildGeometry === 'function') rebuildGeometry();
    if (typeof updateUI === 'function') updateUI();
    if (typeof logOperation === 'function') logOperation(action, action + ' ' + uid, { modelId: uid }, 'modify');
  }
  window.updateAirPipeField = function (uid, field, value) {
    edit('updateAirPipe', uid, function (m) {
      var g = geom(m);
      if (field === 'shape') g.shape = value === 'rect' ? 'rect' : 'round';
      else { var v = fromDisplayLength(parseFloat(value)); if (isFinite(v) && v >= MIN_BORE) g[field] = v; }
    });
  };
  window.updateAirPipePoint = function (uid, index, axis, value) {
    edit('updateAirPipePoint', uid, function (m) {
      var pts = worldPoints(m); var p = pts[index]; if (!p) return;
      p[axis] = fromDisplayLength(parseFloat(value));
      writePointWorld(m, index, p);
      var end = endOfIndex(m, index);
      if (end && joinBroken(m, end)) unjoin(uid, end);
    });
  };
  window.insertAirPipePoint = function (uid, index) { edit('insertAirPipePoint', uid, function (m) { insertPoint(m, index); }); };
  window.deleteAirPipePoint = function (uid, index) {
    edit('deleteAirPipePoint', uid, function (m) { var end = endOfIndex(m, index); if (end) unjoin(uid, end); removePoint(m, index); });
  };
  window.unjoinAirPipe = function (uid, end) { edit('unjoinAirPipe', uid, function () { unjoin(uid, end); }); };

  function renderInspector(prop, model) {
    var g = geom(model), pts = worldPoints(model), unit = getLengthUnitLabel(), step = getLengthStep();
    var esc = function (s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); };
    var nameOf = function (uid) { var o = window.getModelById(uid); return o && o.meta ? o.meta.name : uid; };
    var html = '<div class="prop-group" style="border-color:#b8bec6;">' +
      '<div class="prop-title" style="color:#b8bec6;">' + tr('pipe.air_title') + '</div>' +
      '<div class="prop-row"><label>' + tr('liquid.pipe_name') + '</label><input type="text" id="air-pipe-name" value="' + esc(model.meta.name) + '" onchange="updateModelName(\'' + model.uid + '\', this.value)"></div>' +
      '<div class="prop-row"><label>' + tr('pipe.shape') + '</label><select id="air-pipe-shape" onchange="updateAirPipeField(\'' + model.uid + '\', \'shape\', this.value)">' +
      '<option value="round"' + (g.shape !== 'rect' ? ' selected' : '') + '>' + tr('pipe.shape_round') + '</option>' +
      '<option value="rect"' + (g.shape === 'rect' ? ' selected' : '') + '>' + tr('pipe.shape_rect') + '</option></select></div>';
    if (g.shape === 'rect') {
      html += '<div class="prop-row"><label>' + tr('pipe.width') + ' (' + unit + ')</label><input type="number" id="air-pipe-width" step="' + step + '" value="' + formatLength(num(g.widthM, 0.2)) + '" onchange="updateAirPipeField(\'' + model.uid + '\', \'widthM\', this.value)"></div>' +
        '<div class="prop-row"><label>' + tr('pipe.height') + ' (' + unit + ')</label><input type="number" id="air-pipe-height" step="' + step + '" value="' + formatLength(num(g.heightM, 0.2)) + '" onchange="updateAirPipeField(\'' + model.uid + '\', \'heightM\', this.value)"></div>';
    } else {
      html += '<div class="prop-row"><label>' + tr('pipe.diameter') + ' (' + unit + ')</label><input type="number" id="air-pipe-diameter" step="' + step + '" value="' + formatLength(num(g.diameterM, 0.15)) + '" onchange="updateAirPipeField(\'' + model.uid + '\', \'diameterM\', this.value)"></div>';
    }
    html += '<div style="font-size:0.72rem; color:var(--text-dim); line-height:1.5; padding:4px 0;">' + tr('pipe.hint_drag') + '</div></div>';
    html += '<div class="prop-group"><div class="prop-title">' + tr('pipe.waypoints_title') + ' (' + unit + ')</div>';
    pts.forEach(function (p, i) {
      var end = endOfIndex(model, i); var color = end === 'start' ? '#2196F3' : (end === 'end' ? '#F44336' : '#FFEB3B');
      var j = end ? joinAt(model, end) : null;
      html += '<div class="air-pipe-point" data-index="' + i + '" style="margin-bottom:6px;">' +
        '<div class="prop-row" style="align-items:center;"><label style="color:' + color + '">● ' + tr('pipe.point_n', { n: i + 1 }) + '</label><span style="display:flex;gap:4px;">' +
        '<button class="btn" id="air-pipe-insert-' + i + '" title="' + esc(tr('pipe.add_point_after')) + '" onclick="insertAirPipePoint(\'' + model.uid + '\', ' + i + ')">＋</button>' +
        '<button class="btn" id="air-pipe-delete-' + i + '" title="' + esc(tr('pipe.delete_point')) + '"' + (pts.length <= 2 ? ' disabled' : '') + ' onclick="deleteAirPipePoint(\'' + model.uid + '\', ' + i + ')">－</button></span></div>';
      ['x', 'y', 'z'].forEach(function (axis) {
        html += '<div class="prop-row"><label>' + axis.toUpperCase() + '</label><input type="number" id="air-pipe-wp-' + i + '-' + axis + '" step="' + step + '" value="' + formatLength(p[axis]) + '" onchange="updateAirPipePoint(\'' + model.uid + '\', ' + i + ', \'' + axis + '\', this.value)"></div>';
      });
      if (j) html += '<div class="prop-row"><span style="font-size:11px;color:#00e5ff;" id="air-pipe-join-' + end + '">' + tr('pipe.joined_with', { name: nameOf(j.pipeUid) }) + '</span><button class="btn" id="air-pipe-unjoin-' + end + '" onclick="unjoinAirPipe(\'' + model.uid + '\', \'' + end + '\')">✕</button></div>';
      html += '</div>';
    });
    html += '</div>';
    html += (typeof anchorControlsForModel === 'function') ? anchorControlsForModel(model) : '';
    var pct = opacityToPercent((model.meta.display && model.meta.display.opacity) != null ? model.meta.display.opacity : 1);
    html += '<div class="prop-group"><div class="prop-title">' + tr('common.display') + '</div>' +
      '<div class="prop-row"><label>' + tr('label.model_opacity') + '</label><input type="range" min="0" max="100" step="1" value="' + pct + '" oninput="previewModelOpacity(\'' + model.uid + '\', this)" onchange="updateModelOpacity(\'' + model.uid + '\', this.value)"><span class="badge">' + pct + '%</span></div></div>';
    html += '<div class="prop-group" style="border-color:#e06c75;"><button class="btn" id="air-pipe-delete" style="width:100%; background:rgba(224,108,117,0.1); border:1px solid rgba(224,108,117,0.4); color:#e06c75;" onclick="removeModel(null, \'' + model.uid + '\')">' + tr('pipe.delete') + '</button></div>';
    prop.innerHTML = html;
  }

  window._airPipeEditor = {
    isPipe: isPipe, worldPoints: worldPoints, worldToLocal: worldToLocal, radius: radius,
    join: join, unjoin: unjoin, findTarget: findTarget, freeEnds: freeEnds,
    handleClick: handleClick, gizmoAxis: gizmoAxis, hover: hover, onSelectionChanged: onSelectionChanged,
    grab: function (index) { var b = ballAt(index); if (!b) return false; attachTo(b); return true; },
    clear: function () { detach(); clearBalls(); hideRing(); },
    renderInspector: renderInspector,
  };
})();
