/**
 * The supply-direction animation of a wall-mounted ACU.
 *
 * For every supply patch on a wall-mounted unit a short stream of translucent
 * chevrons drifts away from the outlet along the supply direction — the
 * louvre's surface normal tilted by `physics.supplyAngleDeg` — so the angle
 * typed in the inspector is visible in the viewport. Purely a display helper:
 * the CFD direction comes from the same helper in geometry_pure and travels
 * through the .cfdrun (`directionWorld`).
 *
 * Both editors call `rebuild(models, parent)` after the scene is rebuilt and
 * `tick()` once per rendered frame; in the HTML editor, which renders on
 * demand, the module keeps its own frame loop alive while it has streams.
 *
 * The streams are a geometry-tab, room-view helper: each editor calls
 * `setVisible(false)` on the mesh / solver / post-processing tabs, and clears
 * them (`dispose`) in rack detail mode, which isolates one rack at the origin.
 * A hidden stream is not animated and asks for no frames.
 */
(function () {
  'use strict';
  var streams = [];      // {group, dir, origin, len, phase}
  var parentRef = null;
  var running = false;
  var visible = true;
  var N = 6, LEN = 0.9, SPEED = 0.35;   // chevrons, metres, metres per second
  // geometry_pure's helper scale registry (WP-S1), feature-detected: the
  // module also runs in tests that install a stub window.geometryPure without
  // these functions, and the chevrons must still build at their data size.
  function _registerHelper(mesh, r) {
    var p = (typeof window !== 'undefined') ? window.geometryPure : null;
    if (p && typeof p.registerHelper === 'function') p.registerHelper(mesh, r);
  }
  function _unregisterHelper(mesh) {
    var p = (typeof window !== 'undefined') ? window.geometryPure : null;
    if (p && typeof p.unregisterHelper === 'function') p.unregisterHelper(mesh);
  }
  function dispose() {
    streams.forEach(function (s) { if (s.group.parent) s.group.parent.remove(s.group); s.group.traverse(function (o) { _unregisterHelper(o); if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); }); });
    streams = [];
  }
  function rebuild(models, parent) {
    dispose();
    parentRef = parent || parentRef;
    if (typeof THREE === 'undefined' || !parentRef) return;
    var pure = (typeof window !== 'undefined') ? window.geometryPure : null;
    if (!pure || typeof pure.isWallMountedAcu !== 'function') return;
    (models || []).forEach(function (m) {
      if (!pure.isWallMountedAcu(m) || !m.meta || m.meta.enabled === false) return;
      var faces = (m.geometry && m.geometry.faces) || {};
      Object.keys(faces).forEach(function (fid) {
        (faces[fid].patches || []).forEach(function (p) {
          if (!p || !p.physics || p.physics.role !== 'supply') return;
          var origin, dir;
          if (fid === 'front') { origin = pure.wallAcuPatchCentreWorld(m, p); dir = pure.wallAcuSupplyDirectionWorld(m, p); }
          if (!origin || !dir) return;
          var group = new THREE.Group();
          group.name = 'supplyFlow:' + m.uid + ':' + p.id;
          // Semantic tag: exporters (the Omniverse snapshot) skip display helpers by tag.
          group.userData.isSupplyFlow = true;
          var mat = new THREE.MeshBasicMaterial({ color: 0x4dd0ff, transparent: true, opacity: 0.55, depthWrite: false });
          var w = Math.max(0.05, (p.w || 0.3) * 0.5);
          for (var k = 0; k < N; k++) {
            // Unit cone (r:h = 1:3, the old 0.03:0.09 ratio) + registry
            // (WP-S1): at scale = 0.03 it shows the old 30 mm radius / 90 mm
            // height; syncHelperScales owns the scale from the first synced
            // frame on, so tick() below only ever writes position/opacity.
            var cone = new THREE.Mesh(new THREE.ConeGeometry(1, 3, 12), mat);
            cone.scale.setScalar(0.03);
            cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
            cone.userData.k = k;
            _registerHelper(cone, 0.03);
            group.add(cone);
          }
          group.userData.w = w;
          group.visible = visible;
          parentRef.add(group);
          streams.push({ group: group, dir: dir.clone().normalize(), origin: origin.clone(), len: LEN, phase: 0 });
        });
      });
    });
    ensureLoop();
  }
  function setVisible(v) {
    v = !!v;
    if (v === visible) return;
    visible = v;
    streams.forEach(function (s) { s.group.visible = visible; });
    if (visible) ensureLoop();
    if (typeof window !== 'undefined' && typeof window.requestRender === 'function') window.requestRender();
  }
  function tick(nowMs) {
    if (!streams.length || !visible) return false;
    var t = (nowMs || performance.now()) / 1000;
    streams.forEach(function (s) {
      s.group.children.forEach(function (c) {
        var f = ((t * SPEED / s.len) + c.userData.k / N) % 1;
        var d = f * s.len;
        // slight fan-out: the jet widens with distance
        var side = new THREE.Vector3(0, 1, 0).cross(s.dir).normalize();
        var spread = (c.userData.k % 2 ? 1 : -1) * 0.25 * s.group.userData.w * f;
        c.position.copy(s.origin).addScaledVector(s.dir, 0.05 + d).addScaledVector(side, spread);
        c.material.opacity = 0.6 * (1 - f);
      });
    });
    return true;
  }
  function ensureLoop() {
    if (running || !streams.length || !visible) return;
    // the React viewport renders every frame and calls tick itself; the HTML
    // editor renders on demand and needs to be asked
    if (typeof window !== 'undefined' && window.__supplyFlowSelfDrive === false) return;
    running = true;
    var step = function () {
      if (!streams.length || !visible) { running = false; return; }
      tick(performance.now());
      if (typeof window.requestRender === 'function') window.requestRender();
      setTimeout(function () { requestAnimationFrame(step); }, 33);
    };
    requestAnimationFrame(step);
  }
  var api = {
    rebuild: rebuild, tick: tick, dispose: dispose, setVisible: setVisible,
    isVisible: function () { return visible; },
    count: function () { return streams.length; }
  };
  if (typeof window !== 'undefined') window.supplyFlowAnim = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
