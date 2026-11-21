/* First-Person Mode — creative-mode FPS navigation + editing.
 * Public API: window.fpsMode.{enter, exit, toggle, isActive}
 * Test hooks: window.fpsMode.{_tick, _injectInput, _setLook, _state}
 * Reads shared Three.js handles from window.__three; touches STL_generate.html minimally. */
(function () {
  'use strict';

  // ---- Tunables (meters, seconds) ----
  var CAP_RADIUS = 0.3;
  var STAND_EYE = 1.7, CROUCH_EYE = 1.0;
  var WALK_SPEED = 1.5, SPRINT_SPEED = 4.0, CROUCH_SPEED = 0.8, FLY_SPEED = 6.0;
  var GRAVITY = 9.81;
  var JUMP_VEL = Math.sqrt(2 * GRAVITY * 0.7); // ~0.7 m apex
  var LOOK_SENS = 0.0022;
  var MAX_DT = 0.05;

  var UP = new THREE.Vector3(0, 1, 0);

  var FPS = {
    active: false, mode: 'walk',
    saved: null,
    pos: new THREE.Vector3(), vel: new THREE.Vector3(),
    yaw: 0, pitch: 0,
    grounded: false,
    eyeHeight: STAND_EYE, targetEye: STAND_EYE,
    input: { forward: false, back: false, left: false, right: false, jump: false, sprint: false, crouch: false, flyUp: false, flyDown: false },
    rafId: null, lastTime: 0,
    colliderGeom: null,
    pointerLocked: false, cursorMode: false, _freshLock: false, _maxMove: 0,
    dragLook: false, draggingLook: false, dragX: 0, dragY: 0, lockRequestGeneration: 0,
    T: null,
    dom: { crosshair: null, hint: null, pause: null },
    _h: null,
    // --- selection (click-to-select, inspect only) ---
    edit: null
  };

  // scratch objects (avoid per-frame allocation)
  var _euler = new THREE.Euler(0, 0, 0, 'YXZ');
  var _fwd = new THREE.Vector3(), _right = new THREE.Vector3(), _wish = new THREE.Vector3();
  var _seg = new THREE.Line3(), _box = new THREE.Box3();
  var _v1 = new THREE.Vector3(), _v2 = new THREE.Vector3(), _v3 = new THREE.Vector3();

  function isActive() { return FPS.active; }
  function _zeroInput() { var i = FPS.input; for (var k in i) i[k] = false; }
  function _toast(msg) { console.warn('[fpsMode] ' + msg); try { if (window.logOperation) window.logOperation('fpsMode', msg, {}, 'info'); } catch (e) {} }

  function _toggleFly() {
    FPS.mode = (FPS.mode === 'fly') ? 'walk' : 'fly';
    FPS.vel.set(0, 0, 0);
    FPS.input.jump = false; FPS.input.flyUp = false; // clear stale vertical intent across the switch
    _setHint(_modeHint());
  }

  // Toggle between camera control (pointer locked, crosshair, mouse looks) and cursor mode
  // (pointer free, cursor visible for clicking UI). Bound to ALT; works in walk and fly.
  function _toggleCursor() {
    FPS.cursorMode = !FPS.cursorMode;
    if (FPS.cursorMode) {
      _zeroInput();                 // stop walking/flying — no input bleeds into cursor mode
      _setCrosshairVisible(false);
      _setHint('Cursor free — ALT to look again · Esc to exit');
      _exitPointerLock();           // onPlChange (drop) re-affirms this state
    } else {
      _setCrosshairVisible(true);
      _setHint(_modeHint());
      if (FPS.dragLook) { _setCrosshairVisible(true); _setHint(_modeHint()); }
      else _requestPointerLock();    // onPlChange (lock) clears cursorMode + arms _freshLock
    }
  }

  function _blocked() {
    if (window.state && window.uiState.session && window.uiState.session._uiFrozen) return 'busy meshing/solving';
    if (window.room2d && window.room2d.isActive && window.room2d.isActive()) return 'exit the 2D editor first';
    if (typeof window.isLiquidMode === 'function' && window.isLiquidMode()) return 'exit liquid mode first';
    if (typeof window.isRackDetailMode === 'function' && window.isRackDetailMode()) return 'exit rack-detail first';
    return null;
  }

  function enter() {
    if (FPS.active) return;
    var T = window.__three;
    if (!T || !T.renderer || !T.camera || !T.controls) { _toast('3D viewport not ready'); return; }
    var why = _blocked();
    if (why) { _toast('Cannot enter first-person mode: ' + why); return; }

    FPS.T = T;
    FPS.edit = (T.edit || null);
    FPS.saved = { position: T.camera.position.clone(), target: T.controls.target.clone(), up: T.camera.up.clone(), fov: T.camera.fov };
    T.controls.enabled = false;
    window._fpsActive = true;
    FPS.active = true;
    FPS.dragLook = false; FPS.draggingLook = false;
    FPS.mode = 'fly'; // free-fly preserves the inherited god-view height (walk gravity would drop the eye to the floor)
    FPS.cursorMode = false;
    FPS.vel.set(0, 0, 0);
    FPS.eyeHeight = STAND_EYE; FPS.targetEye = STAND_EYE;
    _zeroInput();

    _buildCollider();
    _inheritFromGodView(T);
    T.camera.up.copy(UP);
    _applyLook();
    T.camera.position.copy(FPS.pos);

    _showHud();
    _updateToggleButton(true);
    _attachListeners();
    _requestPointerLock();

    FPS.lastTime = performance.now();
    FPS.rafId = requestAnimationFrame(_loop);
    if (window.requestRender) window.requestRender();
  }

  function exit() {
    if (!FPS.active) return;
    FPS.active = false;
    FPS.lockRequestGeneration++;
    if (FPS.rafId) { cancelAnimationFrame(FPS.rafId); FPS.rafId = null; }
    _exitPointerLock();
    _detachListeners();
    _hideHud();
    var T = FPS.T, s = FPS.saved;
    if (T) {
      // Hand the current FPS camera transform back to the god-view orbit camera so "Back to
      // God View" resumes exactly where you were looking — no teleport. The orbit target is
      // placed along the current forward at the previous orbit radius (preserves pivot/zoom
      // feel); up is leveled to world-up (FPS carries no roll). Because position + facing are
      // identical to the last FPS frame, the switch is seamless.
      _euler.set(FPS.pitch, FPS.yaw, 0, 'YXZ');
      _fwd.set(0, 0, -1).applyEuler(_euler).normalize();
      var radius = s ? s.position.distanceTo(s.target) : 0;
      if (!(radius > 0.1)) radius = 5; // sensible pivot distance if the prior orbit was degenerate
      T.camera.position.copy(FPS.pos);
      T.controls.target.copy(FPS.pos).addScaledVector(_fwd, radius);
      T.camera.up.copy(UP);
      if (s && T.camera.fov !== s.fov) { T.camera.fov = s.fov; T.camera.updateProjectionMatrix(); }
      T.camera.lookAt(T.controls.target);
      T.controls.enabled = true;
      T.controls.update();
    }
    if (FPS.colliderGeom) {
      if (FPS.colliderGeom.disposeBoundsTree) FPS.colliderGeom.disposeBoundsTree();
      FPS.colliderGeom.dispose();
    }
    FPS.colliderGeom = null;
    FPS.dragLook = false; FPS.draggingLook = false;
    FPS.cursorMode = false;
    window._fpsActive = false;
    _updateToggleButton(false);
    if (window.requestRender) window.requestRender();
  }

  function toggle() { FPS.active ? exit() : enter(); }

  // ---- spawn ----
  function _computeSpawn() {
    // Spawn from the collidable geometry's bounds (_buildCollider runs first), NOT
    // setFromObject(globalGroup): the latter includes invisible helper meshes (e.g. a
    // hidden mesh parked far from the room) and would offset the spawn into empty space.
    var box = null;
    if (FPS.colliderGeom) { FPS.colliderGeom.computeBoundingBox(); box = FPS.colliderGeom.boundingBox; }
    if (!box || box.isEmpty()) { FPS.pos.set(0, STAND_EYE + 0.3, 0); FPS.yaw = 0; FPS.pitch = 0; return; }
    FPS.pos.set((box.min.x + box.max.x) / 2, box.min.y + STAND_EYE + 0.3, (box.min.z + box.max.z) / 2);
    FPS.yaw = 0; FPS.pitch = 0;
  }

  // Adopt the god-view orbit camera's exact position + facing so entering FPS never teleports
  // the view. yaw/pitch are the inverse of _applyLook's Euler('YXZ'): for forward d,
  // pitch = asin(d.y), yaw = atan2(-d.x, -d.z). Falls back to the bbox spawn only if the
  // orbit camera is somehow invalid (NaN). Caller starts in fly mode to keep the height.
  function _inheritFromGodView(T) {
    var gp = T.camera.position, gt = T.controls.target;
    if (!gp || !isFinite(gp.x) || !isFinite(gp.y) || !isFinite(gp.z)) { _computeSpawn(); return; }
    FPS.pos.copy(gp);
    _v1.copy(gt).sub(gp);                          // camera -> orbit target = look direction
    if (_v1.lengthSq() < 1e-9) T.camera.getWorldDirection(_v1); // target == position: use camera forward
    _v1.normalize();
    FPS.pitch = Math.asin(Math.max(-1, Math.min(1, _v1.y)));
    FPS.yaw = Math.atan2(-_v1.x, -_v1.z);
  }

  // ---- collider: merged world-space BVH from all visible scene meshes ----
  function _buildCollider() {
    FPS.colliderGeom = null;
    var T = FPS.T;
    if (!T || !T.globalGroup) return;
    T.globalGroup.updateMatrixWorld(true);
    var positions = [];
    var v = _v1;
    T.globalGroup.traverse(function (o) {
      if (!o.isMesh || !o.geometry || o.visible === false) return;
      var g = o.geometry;
      var src = g.index ? g.toNonIndexed() : g;
      var pos = src.getAttribute('position');
      if (pos) {
        var m = o.matrixWorld;
        for (var i = 0; i < pos.count; i++) {
          v.fromBufferAttribute(pos, i).applyMatrix4(m);
          positions.push(v.x, v.y, v.z);
        }
      }
      if (g.index && src.dispose) src.dispose(); // free temp non-indexed clone
    });
    if (positions.length === 0) return;
    var geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));
    geom.computeBoundsTree(); // patched onto BufferGeometry.prototype by three-mesh-bvh
    FPS.colliderGeom = geom;
  }

  // ---- look ----
  function _applyLook() {
    FPS.pitch = Math.max(-Math.PI / 2 + 0.01, Math.min(Math.PI / 2 - 0.01, FPS.pitch));
    _euler.set(FPS.pitch, FPS.yaw, 0, 'YXZ');
    FPS.T.camera.quaternion.setFromEuler(_euler);
  }
  function _setLook(yaw, pitch) { FPS.yaw = yaw; FPS.pitch = pitch; if (FPS.active) _applyLook(); }

  // ---- physics step ----
  function _tick(dt) {
    if (!FPS.T || dt <= 0) return;
    if (dt > MAX_DT) dt = MAX_DT;
    var cam = FPS.T.camera;

    if (FPS.mode === 'fly') {
      var fspeed = FPS.input.sprint ? FLY_SPEED * 2 : FLY_SPEED;
      cam.getWorldDirection(_fwd).normalize();
      _right.crossVectors(_fwd, UP).normalize();
      _wish.set(0, 0, 0);
      if (FPS.input.forward) _wish.add(_fwd);
      if (FPS.input.back) _wish.addScaledVector(_fwd, -1);
      if (FPS.input.right) _wish.add(_right);
      if (FPS.input.left) _wish.addScaledVector(_right, -1);
      if (FPS.input.flyUp) _wish.add(UP);                                  // Space held = up (released = stop)
      if (FPS.input.crouch || FPS.input.flyDown) _wish.addScaledVector(UP, -1);  // C held = down
      if (_wish.lengthSq() > 0) _wish.normalize().multiplyScalar(fspeed);
      FPS.pos.addScaledVector(_wish, dt);
      FPS.vel.set(0, 0, 0);
      FPS.grounded = false;
    } else {
      // walk
      FPS.targetEye = FPS.input.crouch ? CROUCH_EYE : STAND_EYE;
      FPS.eyeHeight += (FPS.targetEye - FPS.eyeHeight) * Math.min(1, dt * 10);
      var speed = FPS.input.crouch ? CROUCH_SPEED : (FPS.input.sprint ? SPRINT_SPEED : WALK_SPEED);

      cam.getWorldDirection(_fwd); _fwd.y = 0; _fwd.normalize();
      _right.crossVectors(_fwd, UP).normalize();
      _wish.set(0, 0, 0);
      if (FPS.input.forward) _wish.add(_fwd);
      if (FPS.input.back) _wish.addScaledVector(_fwd, -1);
      if (FPS.input.right) _wish.add(_right);
      if (FPS.input.left) _wish.addScaledVector(_right, -1);
      if (_wish.lengthSq() > 0) _wish.normalize().multiplyScalar(speed);
      FPS.vel.x = _wish.x; FPS.vel.z = _wish.z;

      FPS.vel.y -= GRAVITY * dt;
      if (FPS.input.jump && FPS.grounded) { FPS.vel.y = JUMP_VEL; FPS.grounded = false; }
      FPS.input.jump = false; // consume (one jump per keypress; _setKey ignores key-repeat)

      FPS.pos.addScaledVector(FPS.vel, dt);

      if (FPS.colliderGeom) {
        _resolveCollision();
      } else {
        var feetY = FPS.pos.y - FPS.eyeHeight; // floor fallback at y=0
        if (feetY <= 0) { FPS.pos.y = FPS.eyeHeight; if (FPS.vel.y < 0) FPS.vel.y = 0; FPS.grounded = true; }
        else { FPS.grounded = false; }
      }
    }

    cam.position.copy(FPS.pos);
    _applyLook();
  }

  function _resolveCollision() {
    if (!FPS.colliderGeom) return;
    var R = CAP_RADIUS;
    // world-space capsule segment: top sphere centre .. bottom sphere centre
    _seg.start.copy(FPS.pos).addScaledVector(UP, -R);
    _seg.end.copy(FPS.pos).addScaledVector(UP, -(FPS.eyeHeight - R));
    // capsule AABB (segment expanded by radius)
    _box.makeEmpty();
    _box.expandByPoint(_seg.start); _box.expandByPoint(_seg.end);
    _box.min.addScalar(-R); _box.max.addScalar(R);

    FPS.colliderGeom.boundsTree.shapecast({
      intersectsBounds: function (bvBox) { return bvBox.intersectsBox(_box); },
      intersectsTriangle: function (tri) {
        var triPoint = _v2, capPoint = _v3;
        var dist = tri.closestPointToSegment(_seg, triPoint, capPoint);
        if (dist < R) {
          var depth = R - dist;
          var dir = capPoint.sub(triPoint).normalize();
          _seg.start.addScaledVector(dir, depth);
          _seg.end.addScaledVector(dir, depth);
        }
      }
    });

    // map the corrected segment back to the eye position
    var newEyeY = _seg.start.y + R;
    var corrY = newEyeY - FPS.pos.y;
    FPS.pos.set(_seg.start.x, newEyeY, _seg.start.z);

    if (corrY > 1e-4 && FPS.vel.y <= 0.01) { FPS.grounded = true; FPS.vel.y = 0; }
    else if (corrY < -1e-4) { if (FPS.vel.y > 0) FPS.vel.y = 0; FPS.grounded = false; }
    else { FPS.grounded = false; }
  }

  function _injectInput(partial) { if (partial) for (var k in partial) FPS.input[k] = partial[k]; }

  // ---- click-to-select at crosshair (granular; identical to god-view) ----
  // Aiming alone does nothing. A click raycasts through the crosshair pixel and
  // hands off to god-view's handleViewportSelection, which resolves the hit to a
  // U-slot / server-patch / ACU panel / face / whole model (and honours Shift/Ctrl).
  // Selection is sticky: it stays put while you look around — nothing ever moves.
  function _selectAtCrosshair(srcEv) {
    if (!FPS.edit || !FPS.edit.handleViewportSelection) return;
    if (FPS.edit.canSelectGeometry && !FPS.edit.canSelectGeometry()) return;
    var cam = FPS.T && FPS.T.camera;
    if (cam) cam.updateMatrixWorld(); // ray must match the just-rendered FPS view
    // Raycast through the CANVAS centre (NDC 0,0 = the camera's optical axis, where the
    // crosshair is drawn). The canvas is offset from the window centre by the toolbar/log
    // panels, so window-centre coords would miss the optical axis and select the wrong thing.
    var el = (cam && FPS.T.renderer) ? FPS.T.renderer.domElement : null;
    var rect = el ? el.getBoundingClientRect() : { left: 0, top: 0, width: window.innerWidth || 0, height: window.innerHeight || 0 };
    var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
    var ev = new MouseEvent('click', {
      clientX: cx, clientY: cy, button: 0,
      shiftKey: !!(srcEv && srcEv.shiftKey),
      ctrlKey: !!(srcEv && srcEv.ctrlKey),
      metaKey: !!(srcEv && srcEv.metaKey)
    });
    try { FPS.edit.handleViewportSelection(ev); } catch (e) { _toast('select error: ' + (e && e.message ? e.message : e)); }
  }

  // ---- input mapping ----
  function _setKey(code, down, repeat) {
    switch (code) {
      case 'KeyW': FPS.input.forward = down; return true;
      case 'KeyS': FPS.input.back = down; return true;
      case 'KeyA': FPS.input.left = down; return true;
      case 'KeyD': FPS.input.right = down; return true;
      case 'ShiftLeft': case 'ShiftRight': FPS.input.sprint = down; return true;
      case 'KeyC': FPS.input.crouch = down; return true;
      // Space: HELD "up" in fly (flyUp tracks the key state), one-shot jump in walk.
      case 'Space': FPS.input.flyUp = down; if (down && !repeat) FPS.input.jump = true; return true;
      case 'KeyF': if (down && !repeat) _toggleFly(); return true;
      default: return false;
    }
  }

  function _translated(key, fallback) {
    var translate = window._fpsTranslate;
    if (!translate && window.I18n) translate = window.I18n.t;
    var label = typeof translate === 'function' ? translate(key) : null;
    return label && label !== key ? label : fallback;
  }
  function _fallbackDragLook(generation) {
    // Rejected promises may finish after the user exits or starts a new view.
    if (!FPS.active || generation !== FPS.lockRequestGeneration) return;
    FPS.pointerLocked = false;
    FPS.dragLook = true; FPS.draggingLook = false; FPS.cursorMode = false;
    _zeroInput(); _hidePauseMenu(); _setCrosshairVisible(true);
    _setHint(_modeHint());
  }
  function _requestPointerLock() {
    var el = FPS.T.renderer.domElement;
    var generation = ++FPS.lockRequestGeneration;
    if (!el || !el.requestPointerLock) { _fallbackDragLook(generation); return; }
    function fail() { _fallbackDragLook(generation); }
    function plain() {
      if (!FPS.active || generation !== FPS.lockRequestGeneration) return;
      try {
        var result = el.requestPointerLock();
        if (result && typeof result.catch === 'function') result.catch(fail);
      } catch (error) { fail(); }
    }
    function rawFailed(error) {
      // Retry without raw input only when that option is unsupported. A
      // document/security denial also rejects a plain lock; use drag look.
      if (error && error.name === 'NotSupportedError') plain();
      else fail();
    }
    try {
      var result = el.requestPointerLock({ unadjustedMovement: true });
      if (result && typeof result.catch === 'function') result.catch(rawFailed);
    } catch (error) { rawFailed(error); }
  }
  function _exitPointerLock() {
    if (document.exitPointerLock && document.pointerLockElement) { try { document.exitPointerLock(); } catch (e) {} }
  }

  function _attachListeners() {
    var el = FPS.T.renderer.domElement;
    var onKeyDown = function (e) {
      if (!FPS.active) return;
      // ALT toggles cursor mode (free the mouse for UI ↔ camera control). Works in walk and
      // fly. preventDefault stops Chrome stealing ALT to focus its menu bar / drop focus.
      if (e.code === 'AltLeft' || e.code === 'AltRight') {
        if (!e.repeat) _toggleCursor();
        e.preventDefault(); e.stopPropagation(); return;
      }
      if (e.code === 'Escape') { exit(); return; }       // Esc always exits, even in cursor mode
      if (FPS.cursorMode) return;                         // cursor free → keys pass through to the page/UI
      if (_setKey(e.code, true, e.repeat)) { e.preventDefault(); e.stopPropagation(); }
    };
    var onKeyUp = function (e) {
      if (!FPS.active) return;
      if (e.code === 'AltLeft' || e.code === 'AltRight') { e.preventDefault(); e.stopPropagation(); return; }
      if (FPS.cursorMode) return;
      if (_setKey(e.code, false, false)) { e.preventDefault(); e.stopPropagation(); }
    };
    var onMouseMove = function (e) {
      if (!FPS.active || FPS.cursorMode || (!FPS.pointerLocked && !(FPS.dragLook && FPS.draggingLook && (e.buttons & 1)))) return;
      // Drop the first event right after a (re)lock: pointer lock reports a huge spurious
      // movementX/Y there (the delta from the pre-lock cursor position), which would make
      // the view jump. The real look starts from the next event.
      if (FPS._freshLock) { FPS._freshLock = false; return; }
      // A single mousemove can BATCH several physical moves (event coalescing, common on
      // high-Hz mice). Reading only e.movementX/Y drops the batched sub-moves, so fast
      // motion under-rotates and feels jumpy/inconsistent. Sum every coalesced sub-event
      // instead. Each sub-event delta is clamped to ±MAXD so one spurious driver/VM SPIKE
      // still can't make the view lurch, while genuine fast motion accumulates smoothly.
      var MAXD = 120;
      var list;
      if (FPS.dragLook && !FPS.pointerLocked) {
        list = [{ movementX: e.clientX - FPS.dragX, movementY: e.clientY - FPS.dragY }];
        FPS.dragX = e.clientX; FPS.dragY = e.clientY;
      } else {
        list = (typeof e.getCoalescedEvents === 'function') ? e.getCoalescedEvents() : null;
      }
      if (!list || !list.length) list = [e];
      var mx = 0, my = 0;
      for (var k = 0; k < list.length; k++) {
        var dx = list[k].movementX || 0, dy = list[k].movementY || 0;
        var amax = Math.abs(dx) > Math.abs(dy) ? Math.abs(dx) : Math.abs(dy);
        if (amax > FPS._maxMove) FPS._maxMove = amax; // diagnostics: read fpsMode._state._maxMove
        if (dx > MAXD) dx = MAXD; else if (dx < -MAXD) dx = -MAXD;
        if (dy > MAXD) dy = MAXD; else if (dy < -MAXD) dy = -MAXD;
        mx += dx; my += dy;
      }
      FPS.yaw -= mx * LOOK_SENS;
      FPS.pitch -= my * LOOK_SENS;
    };
    var onPlChange = function () {
      var nowLocked = (document.pointerLockElement === el);
      var wasLocked = FPS.pointerLocked;
      FPS.pointerLocked = nowLocked;
      if (!FPS.active) return;
      if (nowLocked) {
        FPS.dragLook = false; FPS.draggingLook = false;
        FPS.cursorMode = false;       // a live lock always means camera control
        FPS._freshLock = true;        // skip the spurious post-lock movementX spike
        _hidePauseMenu();             // re-locked → looking again, dismiss the pause menu
        _setCrosshairVisible(true);
        _setHint(_modeHint());
      } else if (wasLocked) {
        // Pointer lock ended. Either we released it on purpose (ALT → cursor mode) or the
        // browser/OS dropped it mid-session (fast 360 spin, focus loss, alt-tab). In BOTH
        // cases stay in first-person and hold the current heading — never exit / restore the
        // saved god-view camera: that yanks the view to a "random" heading and is the single
        // most annoying FPS bug. The loop keeps applying yaw/pitch. Esc still exits.
        _zeroInput();
        if (FPS.cursorMode) {
          _setCrosshairVisible(false);
          _setHint('Cursor free — ALT to look again · Esc to exit');
        } else {
          // Lock dropped while looking (Esc, alt-tab, focus loss, fast spin). The cursor is now
          // free, so surface an explicit, clickable choice instead of a dead-end hint: Resume
          // (re-lock) or Back to God View (exit). This is the reliable exit path — the toolbar
          // button is unreachable while locked, and Esc alone can't be trusted to reach exit().
          _setHint('Paused — choose Resume or Back to God View · Esc to exit');
          _showPauseMenu();
          _toast('pointer lock released — paused (choose Resume or Back to God View)');
        }
      }
    };
    var onPlError = function () { _fallbackDragLook(FPS.lockRequestGeneration); };
    var onBlur = function () { _zeroInput(); FPS.draggingLook = false; };
    var onPointerUp = function () { FPS.draggingLook = false; };
    var onResize = function () { _positionCrosshair(); };
    // Suppress all god-view mouse interaction on the canvas while active.
    var onCanvasPointer = function (e) {
      if (!FPS.active) return;
      // In cursor mode the pointer is free for UI; a canvas click must NOT re-lock (ALT is the
      // only re-lock path) and must NOT select geometry. Otherwise: click to (re)lock, or — once
      // locked — click to select at the crosshair.
      if (e.type === 'pointerdown' && e.button === 0 && !FPS.cursorMode) {
        if (FPS.dragLook && !FPS.pointerLocked) {
          FPS.draggingLook = true; FPS.dragX = e.clientX; FPS.dragY = e.clientY;
        } else if (!FPS.pointerLocked) _requestPointerLock(); else _selectAtCrosshair(e);
      }
      e.stopPropagation();
      if (e.type === 'contextmenu' || e.type === 'wheel') e.preventDefault();
    };
    document.addEventListener('keydown', onKeyDown, true);
    document.addEventListener('keyup', onKeyUp, true);
    document.addEventListener('mousemove', onMouseMove, false);
    document.addEventListener('pointerup', onPointerUp, false);
    document.addEventListener('pointerlockchange', onPlChange, false);
    document.addEventListener('pointerlockerror', onPlError, false);
    window.addEventListener('blur', onBlur, false);
    window.addEventListener('resize', onResize, false);
    var ptrTypes = ['pointerdown', 'pointerup', 'click', 'dblclick', 'contextmenu', 'wheel'];
    ptrTypes.forEach(function (t) { el.addEventListener(t, onCanvasPointer, true); });
    FPS._h = { el: el, onKeyDown: onKeyDown, onKeyUp: onKeyUp, onMouseMove: onMouseMove, onPointerUp: onPointerUp, onPlChange: onPlChange, onPlError: onPlError, onBlur: onBlur, onResize: onResize, onCanvasPointer: onCanvasPointer, ptrTypes: ptrTypes };
  }
  function _detachListeners() {
    var h = FPS._h; if (!h) return;
    document.removeEventListener('keydown', h.onKeyDown, true);
    document.removeEventListener('keyup', h.onKeyUp, true);
    document.removeEventListener('mousemove', h.onMouseMove, false);
    document.removeEventListener('pointerup', h.onPointerUp, false);
    document.removeEventListener('pointerlockchange', h.onPlChange, false);
    document.removeEventListener('pointerlockerror', h.onPlError, false);
    window.removeEventListener('blur', h.onBlur, false);
    window.removeEventListener('resize', h.onResize, false);
    h.ptrTypes.forEach(function (t) { h.el.removeEventListener(t, h.onCanvasPointer, true); });
    FPS._h = null;
  }

  // ---- HUD (DOM overlay) ----
  function _baseHint() { return 'WASD move · Mouse look · Click select · Shift sprint · Space jump · C crouch · F fly · ALT cursor · Esc exit'; }
  function _modeHint() { if (FPS.dragLook) return _translated('fps.drag_look_hint', 'Drag the viewport to look around · WASD move · Esc exit'); return FPS.mode === 'fly' ? 'FLY — Space up · C down · F walk · ALT cursor · Esc exit' : _baseHint(); }
  function _setHint(msg) { if (FPS.dom.hint) FPS.dom.hint.textContent = msg; }
  function _showHud() {
    if (!FPS.dom.crosshair) {
      var c = document.createElement('div');
      c.id = 'fps-crosshair';
      // Pinned to the CANVAS centre (camera optical axis) via _positionCrosshair, NOT the
      // window centre — the canvas is offset by the toolbar/log panels. translate(-50%,-50%)
      // centres the 16px box on that pixel.
      c.style.cssText = 'position:fixed;width:16px;height:16px;transform:translate(-50%,-50%);pointer-events:none;z-index:99999;';
      c.innerHTML = '<div style="position:absolute;left:7px;top:0;width:2px;height:16px;background:rgba(255,255,255,0.9);box-shadow:0 0 2px #000;"></div>'
                  + '<div style="position:absolute;left:0;top:7px;width:16px;height:2px;background:rgba(255,255,255,0.9);box-shadow:0 0 2px #000;"></div>';
      document.body.appendChild(c); FPS.dom.crosshair = c;
    }
    if (!FPS.dom.hint) {
      var h = document.createElement('div');
      h.id = 'fps-hint';
      h.style.cssText = 'position:fixed;left:50%;bottom:84px;transform:translateX(-50%);padding:8px 14px;border-radius:8px;background:rgba(0,0,0,0.6);color:#fff;font:12px/1.4 monospace;z-index:99999;pointer-events:none;text-align:center;';
      document.body.appendChild(h); FPS.dom.hint = h;
    }
    FPS.dom.crosshair.style.display = 'block';
    FPS.dom.hint.style.display = 'block';
    _positionCrosshair();
    _setHint(_modeHint());
  }
  function _positionCrosshair() {
    if (!FPS.dom.crosshair || !FPS.T || !FPS.T.renderer) return;
    var rect = FPS.T.renderer.domElement.getBoundingClientRect();
    FPS.dom.crosshair.style.left = (rect.left + rect.width / 2) + 'px';
    FPS.dom.crosshair.style.top = (rect.top + rect.height / 2) + 'px';
  }
  function _hideHud() {
    if (FPS.dom.crosshair) FPS.dom.crosshair.style.display = 'none';
    if (FPS.dom.hint) FPS.dom.hint.style.display = 'none';
    _hidePauseMenu();
  }
  function _setCrosshairVisible(v) {
    if (FPS.dom.crosshair) FPS.dom.crosshair.style.display = v ? 'block' : 'none';
  }

  // ---- pause menu (shown whenever pointer lock drops while looking) ----
  // While the pointer is locked, the OS hides the cursor and NO DOM element (not even the
  // toolbar "Back to God View" button) can be clicked. Chrome also swallows the Esc keydown
  // that releases the lock, so the page never sees Esc to exit on that first press. The result
  // was a dead-end: the user pressed Esc, the lock dropped, and a passive "Look paused" hint
  // told them to click the viewport — which silently re-locked them. This pause menu turns that
  // lock-drop into an explicit, always-clickable choice: the cursor is free the instant the lock
  // drops, so these buttons are guaranteed reachable. Esc → unlock → this menu → click "Back to
  // God View" is now a reliable, game-familiar exit. Built with addEventListener (never inline
  // onclick) so it works under the headless test browser too.
  function _buildPauseMenu() {
    if (FPS.dom.pause) return;
    var ov = document.createElement('div');
    ov.id = 'fps-pause';
    ov.style.cssText = 'position:fixed;inset:0;z-index:100000;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,0.45);pointer-events:auto;';
    var panel = document.createElement('div');
    panel.style.cssText = 'min-width:280px;padding:24px 28px;border-radius:14px;background:rgba(20,20,28,0.96);box-shadow:0 8px 40px rgba(0,0,0,0.6);text-align:center;font:13px/1.5 system-ui,sans-serif;color:#fff;';
    var title = document.createElement('div');
    title.style.cssText = 'font-size:16px;font-weight:600;margin-bottom:6px;';
    title.textContent = '第一人称已暂停 · First-person paused';
    var sub = document.createElement('div');
    sub.style.cssText = 'opacity:0.7;margin-bottom:18px;';
    sub.textContent = '继续游览或返回上帝视角 · Resume looking or return to god view';
    var resumeBtn = document.createElement('button');
    resumeBtn.id = 'fps-pause-resume';
    resumeBtn.style.cssText = 'display:block;width:100%;margin:8px 0;padding:11px 16px;border-radius:8px;border:1px solid rgba(120,90,220,0.5);background:rgba(120,90,220,0.22);color:#cbb8ff;font-size:14px;cursor:pointer;';
    resumeBtn.textContent = '继续游览 · Resume (click viewport)';
    var exitBtn = document.createElement('button');
    exitBtn.id = 'fps-pause-exit';
    exitBtn.style.cssText = 'display:block;width:100%;margin:8px 0;padding:11px 16px;border-radius:8px;border:1px solid rgba(255,193,7,0.6);background:rgba(255,193,7,0.2);color:#ffc107;font-size:14px;font-weight:600;cursor:pointer;';
    exitBtn.textContent = '返回上帝视角 · Back to God View';
    resumeBtn.addEventListener('click', function (e) { e.stopPropagation(); _hidePauseMenu(); _requestPointerLock(); });
    exitBtn.addEventListener('click', function (e) { e.stopPropagation(); exit(); });
    panel.appendChild(title); panel.appendChild(sub); panel.appendChild(resumeBtn); panel.appendChild(exitBtn);
    ov.appendChild(panel);
    document.body.appendChild(ov);
    FPS.dom.pause = ov;
  }
  function _showPauseMenu() {
    _buildPauseMenu();
    if (FPS.dom.pause) FPS.dom.pause.style.display = 'flex';
  }
  function _hidePauseMenu() {
    if (FPS.dom.pause) FPS.dom.pause.style.display = 'none';
  }

  // ---- toolbar button state ----
  // Active (in FPS): yellow "Back to God View". Inactive: purple "Enter FPS". We set both the
  // data-i18n key (so a later language switch re-applies the right label) and textContent now.
  function _updateToggleButton(active) {
    var btn = document.getElementById('fps-toggle-btn');
    if (!btn) return;
    var key = active ? 'btn.back_to_god_view' : 'btn.first_person';
    btn.setAttribute('data-i18n', key);
    var label = _translated(key, active ? 'Back to God View' : 'Enter FPS');
    btn.textContent = label;
    if (active) {
      btn.style.background = 'rgba(255,193,7,0.18)';
      btn.style.borderColor = 'rgba(255,193,7,0.6)';
      btn.style.color = '#ffc107';
    } else {
      // Default (god-view) state: no color — match the gray Load/Save buttons.
      btn.style.background = '';
      btn.style.borderColor = '';
      btn.style.color = '';
    }
  }

  // ---- continuous loop ----
  function _loop(now) {
    if (!FPS.active) return;
    var dt = (now - FPS.lastTime) / 1000;
    FPS.lastTime = now;
    // Guard: a thrown frame must never kill the loop. If _tick throws, the next
    // requestAnimationFrame below would be skipped, freezing the camera at its
    // last heading — looking "stuck / forced to a direction" while the mouse keeps
    // changing yaw invisibly. Catch, log, and keep scheduling frames regardless.
    try { _tick(dt); } catch (e) { _toast('tick error: ' + (e && e.message ? e.message : e)); }
    if (window.requestRender) window.requestRender();
    FPS.rafId = requestAnimationFrame(_loop);
  }

  // Build stamp — run `fpsMode.version` in the console to confirm which build is loaded.
  // If you still see the camera SNAP to a random direction on a 360 spin, you are on an
  // old cached build: reload the PAGE (Ctrl+Shift+R) — re-entering FPS does NOT reload JS.
  var VERSION = '2026-10-05-v9-drag-look';
  try { console.log('[fpsMode] build ' + VERSION + ' loaded (select-only · lock-drop shows pause menu w/ reliable Back-to-God-View · spikes clamped · fly Space=up-while-held · ALT toggles cursor · button swaps purple↔yellow · camera inherited both ways)'); } catch (e) {}

  window.fpsMode = {
    version: VERSION,
    enter: enter, exit: exit, toggle: toggle, isActive: isActive,
    _tick: _tick, _injectInput: _injectInput, _setLook: _setLook, _toggleCursor: _toggleCursor,
    _showPauseMenu: _showPauseMenu, _hidePauseMenu: _hidePauseMenu, _state: FPS
  };
})();
