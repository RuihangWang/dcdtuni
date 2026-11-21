/**
 * The flow arrows of a liquid-cooling result.
 *
 * Cones drift along the pipework in the direction the coolant actually flows,
 * at a speed proportional to its velocity (times a display scale). Purely a
 * display helper: WHERE the arrows go, which way they point and what they
 * carry is decided by the caller from the result (React: liquid/liquidFlow.ts
 * `liquidFlowArrows`, the rule Kit's liquid_flow.flow_arrows shares); this
 * module draws and animates what it is given, in both editors.
 *
 *   rebuild(spec, parent)
 *     spec.tracks  [{points: [[x,y,z], ...]}]  polylines the arrows ride on
 *                  (a rendered pipe's centreline, a manifold axis, a stub)
 *     spec.arrows  [{track, s, sMin, sMax, dir, radius, length, color, speed}]
 *                  s: where the arrow stands (arc length along its track, m);
 *                  [sMin, sMax]: the stretch it moves in and wraps around
 *                  (its cell), dir: +1 along the track, -1 against it,
 *                  speed: m/s along the track before the display scale,
 *                  lift: how far off the centreline it rides (m, optional):
 *                  on the side of the pipe that faces the camera, so an
 *                  arrow is never buried in its own tube and leaves the
 *                  tube's colour readable
 *     spec.density arrows drawn per entry, evenly spaced in its stretch
 *     spec.animate / spec.speedScale  (also setOptions)
 *   tick(nowMs, camera?)  places every instance for that time, lifted toward
 *                  `camera` (a position in the parent's frame; none: upward);
 *                  false when hidden/empty
 *   setVisible(v), setOptions({animate, speedScale}), dispose(), count()
 *
 * One InstancedMesh: a few thousand arrows cost one draw call. The material
 * is unlit, so an arrow's colour is exactly the legend's. The group and the
 * mesh carry `isLiquidFlow`, the tag the Omniverse scene snapshot skips, and
 * the mesh never takes a pick.
 *
 * Both editors call `tick()` once per rendered frame; the HTML editor renders
 * on demand, so the module keeps its own frame loop alive while it is shown
 * unless the host sets `window.__liquidFlowSelfDrive = false` (React does).
 */
(function () {
  'use strict';
  var group = null;
  var mesh = null;
  var parentRef = null;
  var tracks = [];      // {points: [[x,y,z]], cum: [0, ...]}
  var items = [];       // per instance: {arrow, offset}
  var visible = true;
  var animate = true;
  var speedScale = 1;
  var running = false;
  var tmp = null;       // scratch THREE objects, made on first rebuild

  function T() { return (typeof THREE !== 'undefined') ? THREE : null; }

  function dispose() {
    if (group && group.parent) group.parent.remove(group);
    if (mesh) { mesh.geometry.dispose(); mesh.material.dispose(); mesh.dispose && mesh.dispose(); }
    group = null; mesh = null; tracks = []; items = [];
  }

  function prepareTrack(t) {
    var pts = (t && t.points) || [];
    var cum = [0];
    for (var i = 1; i < pts.length; i++) {
      var a = pts[i - 1], b = pts[i];
      cum.push(cum[i - 1] + Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]));
    }
    return { points: pts, cum: cum };
  }

  /** Point and unit tangent at arc length s (clamped to the track). */
  function sample(track, s, out, tangent) {
    var pts = track.points, cum = track.cum, n = pts.length;
    if (n === 0) { out.set(0, 0, 0); tangent.set(0, 1, 0); return; }
    if (n === 1) { out.set(pts[0][0], pts[0][1], pts[0][2]); tangent.set(0, 1, 0); return; }
    var total = cum[n - 1];
    if (s <= 0) s = 0; else if (s >= total) s = total;
    var lo = 0, hi = n - 1;
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (cum[mid] <= s) lo = mid; else hi = mid; }
    // Skip zero-length segments so the tangent is defined.
    while (hi < n - 1 && cum[hi] - cum[lo] <= 0) hi++;
    var a = pts[lo], b = pts[hi], len = cum[hi] - cum[lo];
    var f = len > 0 ? (s - cum[lo]) / len : 0;
    out.set(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f);
    if (len > 0) tangent.set((b[0] - a[0]) / len, (b[1] - a[1]) / len, (b[2] - a[2]) / len);
    else tangent.set(0, 1, 0);
  }

  function frac(x) { return x - Math.floor(x); }

  function rebuild(spec, parent) {
    dispose();
    parentRef = parent || parentRef;
    var three = T();
    if (!three || !parentRef || !spec) return;
    if (!tmp) tmp = { p: new three.Vector3(), t: new three.Vector3(), q: new three.Quaternion(), s: new three.Vector3(),
      m: new three.Matrix4(), up: new three.Vector3(0, 1, 0), c: new three.Color(), v: new three.Vector3(),
      cam: new three.Vector3(), hasCam: false };
    if (typeof spec.animate === 'boolean') animate = spec.animate;
    if (typeof spec.speedScale === 'number' && isFinite(spec.speedScale) && spec.speedScale >= 0) speedScale = spec.speedScale;
    tracks = (spec.tracks || []).map(prepareTrack);
    var density = Math.max(1, Math.min(8, Math.round(spec.density || 1)));
    (spec.arrows || []).forEach(function (a) {
      var track = tracks[a.track];
      var span = a.sMax - a.sMin;
      if (!track || !(span > 0) || !isFinite(a.s)) return;
      var start = (a.dir < 0 ? (a.sMax - a.s) : (a.s - a.sMin)) / span;
      for (var k = 0; k < density; k++) items.push({ arrow: a, track: track, span: span, offset: start + k / density });
    });
    if (!items.length) return;
    group = new three.Group();
    group.name = 'liquidFlowArrows';
    group.userData.isLiquidFlow = true;
    // A unit cone centred on its middle, pointing +Y: scaled per instance.
    var geometry = new three.ConeGeometry(1, 1, 12);
    var material = new three.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
    mesh = new three.InstancedMesh(geometry, material, items.length);
    mesh.name = 'liquidFlowArrowInstances';
    mesh.userData.isLiquidFlow = true;
    mesh.frustumCulled = false;
    mesh.raycast = function () {};
    mesh.instanceMatrix.setUsage(three.DynamicDrawUsage);
    items.forEach(function (it, i) { mesh.setColorAt(i, tmp.c.setHex(it.arrow.color >>> 0)); });
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    group.add(mesh);
    group.visible = visible;
    parentRef.add(group);
    place(0);
    ensureLoop();
  }

  /** tmp.v := the unit offset across the pipe toward the camera (or up). */
  function across(p, t) {
    var v = tmp.v;
    if (tmp.hasCam) v.copy(tmp.cam).sub(p); else v.set(0, 1, 0);
    var len = v.length();
    v.addScaledVector(t, -v.dot(t));
    if (v.length() > 1e-6 * Math.max(len, 1)) return v.normalize();
    // Looking along the pipe: the crown, or for a vertical pipe +x.
    v.set(0, 1, 0).addScaledVector(t, -t.y);
    if (v.length() > 1e-6) return v.normalize();
    return v.set(1, 0, 0).addScaledVector(t, -t.x).normalize();
  }

  function place(nowMs) {
    if (!mesh) return;
    var t = animate ? (nowMs || 0) / 1000 : 0;
    for (var i = 0; i < items.length; i++) {
      var it = items[i], a = it.arrow;
      var u = frac(it.offset + (animate ? t * a.speed * speedScale / it.span : 0));
      var s = a.dir < 0 ? a.sMax - u * it.span : a.sMin + u * it.span;
      sample(it.track, s, tmp.p, tmp.t);
      if (a.lift > 0) tmp.p.addScaledVector(across(tmp.p, tmp.t), a.lift);
      if (a.dir < 0) tmp.t.multiplyScalar(-1);
      tmp.q.setFromUnitVectors(tmp.up, tmp.t);
      tmp.s.set(a.radius, a.length, a.radius);
      tmp.m.compose(tmp.p, tmp.q, tmp.s);
      mesh.setMatrixAt(i, tmp.m);
    }
    mesh.instanceMatrix.needsUpdate = true;
  }

  function tick(nowMs, camera) {
    if (!mesh || !visible) return false;
    tmp.hasCam = !!camera && isFinite(camera.x) && isFinite(camera.y) && isFinite(camera.z);
    if (tmp.hasCam) tmp.cam.set(camera.x, camera.y, camera.z);
    place(typeof nowMs === 'number' ? nowMs : performance.now());
    return true;
  }

  function setVisible(v) {
    v = !!v;
    if (v === visible) return;
    visible = v;
    if (group) group.visible = visible;
    if (visible) ensureLoop();
    if (typeof window !== 'undefined' && typeof window.requestRender === 'function') window.requestRender();
  }

  function setOptions(o) {
    if (!o) return;
    if (typeof o.animate === 'boolean') animate = o.animate;
    if (typeof o.speedScale === 'number' && isFinite(o.speedScale) && o.speedScale >= 0) speedScale = o.speedScale;
    if (!animate) place(0);
    ensureLoop();
  }

  function ensureLoop() {
    if (running || !mesh || !visible || !animate) return;
    if (typeof window === 'undefined' || window.__liquidFlowSelfDrive === false) return;
    if (typeof requestAnimationFrame !== 'function') return;
    running = true;
    var step = function () {
      if (!mesh || !visible || !animate) { running = false; return; }
      var cam = window.camera && window.camera.position;
      tick(performance.now(), cam && group && group.parent ? group.parent.worldToLocal(cam.clone()) : undefined);
      if (typeof window.requestRender === 'function') window.requestRender();
      setTimeout(function () { requestAnimationFrame(step); }, 33);
    };
    requestAnimationFrame(step);
  }

  /** One instance's placement, for tests and the browser check. */
  function instance(i) {
    if (!mesh || i < 0 || i >= items.length) return null;
    var three = T();
    var m = new three.Matrix4(), p = new three.Vector3(), q = new three.Quaternion(), s = new three.Vector3();
    mesh.getMatrixAt(i, m);
    m.decompose(p, q, s);
    var d = new three.Vector3(0, 1, 0).applyQuaternion(q);
    var c = new three.Color();
    if (mesh.instanceColor) mesh.getColorAt(i, c);
    return { position: p.toArray(), direction: d.toArray(), scale: s.toArray(), color: c.getHex() };
  }

  var api = {
    rebuild: rebuild, tick: tick, dispose: dispose, setVisible: setVisible, setOptions: setOptions,
    isVisible: function () { return visible; },
    count: function () { return items.length; },
    instance: instance,
    options: function () { return { animate: animate, speedScale: speedScale }; }
  };
  if (typeof window !== 'undefined') window.liquidFlowAnim = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
