/**
 * Liquid cooling pipe renderer.
 * Renders supply (blue) and return (red) pipes between CDU and Rack models.
 * Called after buildGeometryData() to overlay pipe visualization.
 *
 * NOTE: Current routing is point-to-point (CDU→Rack). Shared trunk line
 * with T-branches is deferred to Phase 6 (manual pipe editing).
 *
 * Equipment endpoints use geometryPure's canonical model transform, the same
 * quaternion/scale/anchor chain as the rendered cabinet and rack.
 */

(function() {
  'use strict';

  // Pipe rendering config (aligned with design spec Section 5.4)
  const SUPPLY_COLOR = 0x2196F3;  // Blue
  const RETURN_COLOR = 0xF44336;  // Red
  const UNCONNECTED_COLOR = 0x8B6914;  // Brown (unconnected pipe)
  const PIPE_RADIUS = 0.015;      // Visual radius (m) — spec: 0.015m
  const PIPE_SEGMENTS = 32;       // Tube segments along path
  const PIPE_RADIAL = 8;          // Radial segments
  const PIPE_OPACITY = 0.85;
  const RETURN_Y_OFFSET = 0.05;   // Return pipe vertical offset from supply (Y-up) — spec: 0.05m

  // Internal storage for pipe meshes
  let _pipeGroup = null;

  // Edit mode state
  let _editMode = false;
  let _waypointSpheres = [];
  let _componentIcons = [];

  // geometry_pure's helper scale registry (WP-S1), read defensively: this
  // module also loads in test environments where window.geometryPure is not
  // installed, and the pipe visuals must still build at their data size there.
  function _pure() {
    return (typeof window !== 'undefined' && window.geometryPure) || null;
  }
  function _registerHelper(mesh, dataRadius) {
    var p = _pure();
    if (p && typeof p.registerHelper === 'function') p.registerHelper(mesh, dataRadius);
  }
  function _unregisterHelper(mesh) {
    var p = _pure();
    if (p && typeof p.unregisterHelper === 'function') p.unregisterHelper(mesh);
  }

  // js/pbr_materials.js, read defensively for the same reason as geometryPure:
  // without it (a test that loads this file alone) the pipes keep the plain
  // MeshStandardMaterial they always had.
  function _pbr() {
    return (typeof window !== 'undefined' && window.pbrMaterials) || null;
  }
  /** A fresh PBR material for `role`, or the plain fallback. Never shared. */
  function _liquidMaterial(role, params) {
    var lib = _pbr();
    if (lib) return lib.material(role, params);
    return new THREE.MeshStandardMaterial(params);
  }
  /** Physical texture tiling on a unique CylinderGeometry (axis Y). */
  function _cylinderUv(geometry, radius, height, role) {
    var lib = _pbr();
    if (lib) lib.cylinderUv(geometry, radius, height, role);
  }

  /**
   * Remove all existing pipe meshes from the scene.
   */
  function clearPipes() {
    if (_pipeGroup && _pipeGroup.parent) {
      _pipeGroup.parent.remove(_pipeGroup);
    }
    if (_pipeGroup) {
      _pipeGroup.traverse(function(child) {
        _unregisterHelper(child);
        if (child.geometry) child.geometry.dispose();
        if (child.userData && child.userData._pipeDisplaySource) child.userData._pipeDisplaySource.geometry.dispose();
        if (child.material) child.material.dispose();
      });
    }
    // Remove junction containers and their body-cube / port-sphere children
    // from globalGroup
    if (_pipeGroup && _pipeGroup.userData && _pipeGroup.userData._junctionMeshes) {
      _pipeGroup.userData._junctionMeshes.forEach(function(container) {
        if (container.parent) container.parent.remove(container);
        container.traverse(function(child) {
          // Covers the body cube and the port spheres (WP-S1); the container
          // Group itself carries neither geometry nor material.
          _unregisterHelper(child);
          if (child.geometry) child.geometry.dispose();
          if (child.material) child.material.dispose();
        });
      });
    }
    _pipeGroup = null;
  }

  /**
   * Get the world-space connection point for a CDU's supply/return port.
   * Y-up: ports are on the back face (Z = anchor.z + depth), near the top (Y).
   */
  function getCduPort(model, portType) {
    var p = _pure();
    if (p && typeof p.liquidEquipmentPortFrame === 'function') {
      var frame = p.liquidEquipmentPortFrame(model, portType, 'top');
      if (frame && frame.position) return frame.position.clone();
    }
    var a = model.geometry.location;
    var sz = model.geometry.size;
    var w = sz.x;
    var d = sz.z;
    var h = sz.y;
    var cx = a.x + w / 2;
    var portY = a.y + h * 0.85;  // Port at 85% height (Y-up)
    var backZ = a.z + d;

    if (portType === 'supply') {
      return new THREE.Vector3(cx, portY, backZ);
    } else {
      return new THREE.Vector3(cx, portY - RETURN_Y_OFFSET, backZ);
    }
  }

  /**
   * Get the world-space connection point for a Rack's inlet/outlet.
   * Y-up: ports are on the left side (X = anchor.x), near the top (Y).
   */
  function getRackPort(model, portType) {
    var p = _pure();
    if (p && typeof p.liquidEquipmentPortFrame === 'function') {
      var frame = p.liquidEquipmentPortFrame(model, portType, 'top');
      if (frame && frame.position) return frame.position.clone();
    }
    var a = model.geometry.location;
    var d = model.geometry.size.z;
    var h = model.geometry.size.y;
    var cz = a.z + d / 2;
    var portY = a.y + h * 0.9;

    if (portType === 'inlet') {
      return new THREE.Vector3(a.x, portY, cz);
    } else {
      return new THREE.Vector3(a.x, portY - RETURN_Y_OFFSET, cz);
    }
  }

  function _connectedEquipmentPort(portId) {
    if (!portId || typeof window.allModels !== 'function') return null;
    var parts = String(portId).split('.');
    var model = window.allModels().find(function(m) { return m.uid === parts[0]; });
    var p = _pure();
    if (!model || !p || typeof p.liquidEquipmentPortFrame !== 'function') return null;
    return p.liquidEquipmentPortFrame(model, parts[1] || '', 'top');
  }

  /** Resolve the endpoint tokens stored by legacy/automatic chain routing. */
  function resolveChainEquipmentPort(token) {
    if (typeof token !== 'string' || typeof window.allModels !== 'function') return null;
    var models = window.allModels();
    for (var i = 0; i < models.length; i++) {
      var model = models[i];
      if (!model || (model.kind !== 'cdu' && model.kind !== 'rack')) continue;
      var name = String((model.meta && model.meta.name) || '').replace(/[^A-Za-z0-9_]/g, '_');
      if (model.kind === 'cdu') {
        if (token === name + '_supply') return getCduPort(model, 'supply');
        if (token === name + '_return') return getCduPort(model, 'return');
      } else {
        if (token === name + '_manifold_in' || token === name + '_manifold_supply') {
          return getRackPort(model, 'inlet');
        }
        if (token === name + '_manifold_out' || token === name + '_manifold_return') {
          return getRackPort(model, 'outlet');
        }
      }
    }
    return null;
  }

  /**
   * Generate a pipe route from one port to another (Y-up).
   * Route: source → up (Y) to clearance height → horizontal to target X →
   *        horizontal to target Z → down (Y) to target.
   */
  function generateRoute(fromPort, toPort) {
    var midY = Math.max(fromPort.y, toPort.y) + 0.1;
    var points = [
      fromPort.clone(),
      new THREE.Vector3(fromPort.x, midY, fromPort.z),
      new THREE.Vector3(toPort.x, midY, fromPort.z),
      new THREE.Vector3(toPort.x, midY, toPort.z),
      new THREE.Vector3(toPort.x, toPort.y, toPort.z),
    ];
    return points;
  }

  /**
   * Create a tube mesh from a list of points.
   */
  function _buildSmoothedPath(points, filletRadius) {
    var path = new THREE.CurvePath();
    if (points.length < 2) return path;
    if (points.length === 2) {
      path.add(new THREE.LineCurve3(points[0].clone(), points[1].clone()));
      return path;
    }
    var fr = filletRadius || 0.03;
    var fillets = [];
    for (var i = 1; i < points.length - 1; i++) {
      var prev = points[i - 1], cur = points[i], next = points[i + 1];
      var dirIn = new THREE.Vector3().subVectors(cur, prev).normalize();
      var dirOut = new THREE.Vector3().subVectors(next, cur).normalize();
      var lenIn = cur.distanceTo(prev);
      var lenOut = cur.distanceTo(next);
      var trim = Math.min(fr, lenIn * 0.45, lenOut * 0.45);
      var arcStart = cur.clone().sub(dirIn.clone().multiplyScalar(trim));
      var arcEnd = cur.clone().add(dirOut.clone().multiplyScalar(trim));
      fillets.push({ arcStart: arcStart, arcEnd: arcEnd, corner: cur.clone() });
    }
    // First straight segment
    path.add(new THREE.LineCurve3(points[0].clone(), fillets[0].arcStart));
    for (var j = 0; j < fillets.length; j++) {
      var f = fillets[j];
      var mid = f.arcStart.clone().add(f.arcEnd).multiplyScalar(0.5);
      var pull = f.corner.clone().sub(mid).multiplyScalar(0.4);
      mid.add(pull);
      path.add(new THREE.QuadraticBezierCurve3(f.arcStart, mid, f.arcEnd));
      var lineEnd = (j < fillets.length - 1) ? fillets[j + 1].arcStart : points[points.length - 1].clone();
      path.add(new THREE.LineCurve3(f.arcEnd, lineEnd));
    }
    return path;
  }

  function createTubeMesh(points, color, radius, pipeModelId, equipmentEndpoints) {
    if (points.length < 2) return null;
    var r = (typeof radius === 'number' && radius > 0) ? radius : PIPE_RADIUS;
    var authoredModel = pipeModelId && typeof window.allModels === 'function'
      ? window.allModels().find(function(m) { return m.uid === pipeModelId; }) : null;
    if (points.length > 2 && authoredModel && authoredModel.geometry.routeMode === 'polyline') {
      var group = new THREE.Group();
      for (var i=0;i<points.length-1;i++) {
        var endpoints=(equipmentEndpoints || []).filter(function(e) {
          return e.end === 'start' ? i===0 : i===points.length-2;
        });
        var straight=createTubeMesh([points[i],points[i+1]],color,r,pipeModelId,endpoints);
        if (straight) group.add(straight);
        if (i>0) {
          var bend=new THREE.Mesh(new THREE.SphereGeometry(r,16,12),
            _liquidMaterial('pipe-insulation',{color:color,metalness:0,roughness:.82,transparent:true,opacity:PIPE_OPACITY}));
          bend.position.copy(points[i]);
          bend.userData={isPipe:true,isPolylineBend:true,pipeColor:color,pipeModelId:pipeModelId};
          group.add(bend);
        }
      }
      return group;
    }
    var path = _buildSmoothedPath(points, r * 3);
    var totalSegs = Math.max(8, PIPE_SEGMENTS * (points.length - 1));
    var geometry = new THREE.TubeGeometry(path, totalSegs, r, PIPE_RADIAL, false);
    // Keep a pristine display source with normalized longitudinal UVs. A
    // display refresh changes geometry only, retaining result materials and
    // object selection. These buffers never enter the STL render registry.
    var displaySource = null, displayDiagnostic = null;
    if (typeof window._displayLiquidPipeGeometry === 'function') {
      displaySource = {
        geometry: geometry.clone(),
        context: {path: path, points: points.map(function(p) { return p.clone(); }),
          segments: totalSegs, pipeModelId: pipeModelId || null, equipmentEndpoints: equipmentEndpoints || []},
        radius: r,
      };
      // BufferGeometry.clone shares userData; the retained source must not
      // inherit the display instance's one-time PBR UV scaling marker.
      displaySource.geometry.userData = Object.assign({}, geometry.userData);
      var displayed = window._displayLiquidPipeGeometry(geometry, displaySource.context);
      if (displayed && displayed.geometry) {
        if (displayed.geometry !== geometry) geometry.dispose();
        geometry = displayed.geometry;
        displayDiagnostic = displayed.diagnostic || null;
      }
    }
    // Closed-cell elastomeric insulation. The supply/return colour stays the
    // material colour (the foam's colour map is a high-key neutral), so the
    // coding reads as before and a temperature colour still replaces it.
    var material = _liquidMaterial('pipe-insulation', {
      color: color,
      metalness: 0.0,
      roughness: 0.82,
      transparent: true,
      opacity: PIPE_OPACITY,
      depthWrite: true,
    });
    var lib = _pbr();
    if (lib) lib.tubeUv(geometry, path.getLength(), r, 'pipe-insulation');
    var mesh = new THREE.Mesh(geometry, material);
    mesh.userData = { isPipe: true, pipeColor: color, pipeModelId: pipeModelId || null };
    if (displaySource) mesh.userData._pipeDisplaySource = displaySource;
    if (displayDiagnostic) mesh.userData.pipeDisplayDiagnostic = displayDiagnostic;
    mesh.visible = geometry.getAttribute('position').count > 0;
    mesh.userData.pipeDisplayConcealed = !mesh.visible;
    return mesh;
  }

  /** Metal neck + role collar ending at the logical connection center. */
  function createPortCoupler(position, color, direction) {
    var axis = direction && direction.lengthSq && direction.lengthSq() > 0
      ? direction.clone().normalize() : new THREE.Vector3(0, 0, 1);
    var length = Math.max(PIPE_RADIUS * 2.4, 0.035);
    var group = new THREE.Group();
    group.userData = { isPipe: true, isPortCoupler: true };
    group.position.copy(position).addScaledVector(axis, -length / 2);
    group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis);
    var stubGeo = new THREE.CylinderGeometry(PIPE_RADIUS * 1.15, PIPE_RADIUS * 1.15, length, 16);
    _cylinderUv(stubGeo, PIPE_RADIUS * 1.15, length, 'stainless');
    var stub = new THREE.Mesh(
      stubGeo,
      _liquidMaterial('stainless', { color: 0x8d969c, metalness: 0.72, roughness: 0.3 })
    );
    stub.userData = { isPipe: true, isPortCoupler: true };
    stub.raycast = function() {};
    group.add(stub);
    // The role collar: a colour-coded powder-coated ring.
    var ring = new THREE.Mesh(
      new THREE.CylinderGeometry(PIPE_RADIUS * 1.5, PIPE_RADIUS * 1.5, Math.min(0.012, length * 0.28), 16),
      _liquidMaterial('painted-steel', { color: color, metalness: 0.25, roughness: 0.45 })
    );
    ring.position.y = length * 0.35;
    ring.userData = { isPipe: true, isPortCoupler: true };
    ring.raycast = function() {};
    group.add(ring);
    return group;
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Render from pipe_segment models (Phase D+E schema)
  // ─────────────────────────────────────────────────────────────────────────────

  function renderFromPipeSegmentModels() {
    if (!window.state) return;
    // Reuse existing _pipeGroup if it was already created (e.g. by updatePipeGeometry
    // pre-init for junction tracking); otherwise create a fresh one.
    if (!_pipeGroup) {
      _pipeGroup = new THREE.Group();
      _pipeGroup.userData = { isPipeGroup: true };
    }

    var pipes = window.allModels().filter(function(m) { return m.kind === 'pipe_segment' && m.meta.enabled; });
    pipes.forEach(function(p) {
      var wp = p.geometry && p.geometry.waypoints;
      if (!wp || wp.length < 2) return;
      var points = wp.map(function(w) { return new THREE.Vector3(w.x, w.y, w.z); });
      var loopRole = p.cooling && p.cooling.loopRole;
      var connection = (p.cooling && p.cooling.connection) || {};
      var blindEnds = connection.blindEnds || {};
      var startFrame = !blindEnds.start ? _connectedEquipmentPort(connection.startPortId) : null;
      var endFrame = !blindEnds.end ? _connectedEquipmentPort(connection.endPortId) : null;
      if (startFrame && startFrame.position) points[0].copy(startFrame.position);
      if (endFrame && endFrame.position) points[points.length - 1].copy(endFrame.position);
      var color;
      if (loopRole === 'return') color = RETURN_COLOR;
      else if (loopRole === 'unconnected') color = UNCONNECTED_COLOR;
      else color = SUPPLY_COLOR;
      var diam = p.geometry && p.geometry.diameterM;
      var radius = (diam && diam > 0) ? diam / 2 : PIPE_RADIUS;
      var equipmentEndpoints = [];
      if (startFrame) equipmentEndpoints.push({modelId: String(connection.startPortId).split('.')[0], end: 'start', frame: startFrame});
      if (endFrame) equipmentEndpoints.push({modelId: String(connection.endPortId).split('.')[0], end: 'end', frame: endFrame});
      var mesh = createTubeMesh(points, color, radius, p.uid, equipmentEndpoints);
      if (mesh) _pipeGroup.add(mesh);

      // Blind end caps: black flat cylinder at capped endpoints
      var blindEnds = (p.cooling && p.cooling.connection && p.cooling.connection.blindEnds) || {};
      if (blindEnds.start && points.length >= 2) {
        var capGeo = new THREE.CylinderGeometry(radius * 1.15, radius * 1.15, radius * 0.4, 16);
        var capMat = _liquidMaterial('rubber', { color: 0x1a1a1a, metalness: 0.0, roughness: 0.8 });
        var capMesh = new THREE.Mesh(capGeo, capMat);
        var dir = new THREE.Vector3().subVectors(points[0], points[1]).normalize();
        capMesh.position.copy(points[0]).addScaledVector(dir, radius * 0.2);
        capMesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
        capMesh.userData = { isBlindEndCap: true, pipeId: p.uid, end: 'start' };
        _pipeGroup.add(capMesh);
      }
      if (blindEnds.end && points.length >= 2) {
        var capGeo2 = new THREE.CylinderGeometry(radius * 1.15, radius * 1.15, radius * 0.4, 16);
        var capMat2 = _liquidMaterial('rubber', { color: 0x1a1a1a, metalness: 0.0, roughness: 0.8 });
        var capMesh2 = new THREE.Mesh(capGeo2, capMat2);
        var dir2 = new THREE.Vector3().subVectors(points[points.length - 1], points[points.length - 2]).normalize();
        capMesh2.position.copy(points[points.length - 1]).addScaledVector(dir2, radius * 0.2);
        capMesh2.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir2);
        capMesh2.userData = { isBlindEndCap: true, pipeId: p.uid, end: 'end' };
        _pipeGroup.add(capMesh2);
      }
    });

    if (_pipeGroup.children.length > 0 && typeof globalGroup !== 'undefined') {
      globalGroup.add(_pipeGroup);
    }
  }

  /**
   * Build interactive waypoint spheres for all pipe_segment models.
   * Used in liquid mode for right-click upgrade to T-branch.
   * Spheres carry userData with pipeSegmentId + waypointIndex for raycasting lookup.
   * Returns an array of sphere meshes (caller is responsible for disposal).
   */
  function buildPipeSegmentWaypointSpheres() {
    const spheres = [];
    if (!window.state) return spheres;
    const pipes = window.allModels().filter(m => m.kind === 'pipe_segment' && m.meta.enabled);
    pipes.forEach(pipe => {
      const wps = (pipe.geometry && pipe.geometry.waypoints) || [];
      wps.forEach((wp, wi) => {
        // Unit sphere + registry (WP-S1): shows at the 25 mm data radius.
        // The caller disposes these spheres (liquid_mode
        // ._clearWaypointSpheres), which unregisters them there.
        const sphere = new THREE.Mesh(
          new THREE.SphereGeometry(1, 12, 12),
          new THREE.MeshStandardMaterial({ color: 0xffcc00, roughness: 0.3 })
        );
        sphere.scale.setScalar(0.025);
        sphere.position.set(wp.x, wp.y, wp.z);
        sphere.userData = {
          isWaypoint: true,
          pipeSegmentId: pipe.uid,
          waypointIndex: wi
        };
        _registerHelper(sphere, 0.025);
        if (typeof globalGroup !== 'undefined') globalGroup.add(sphere);
        spheres.push(sphere);
      });
    });
    return spheres;
  }

  /**
   * Render a pipe_junction model as a colored cube with 3 port spheres.
   * A container Group added to globalGroup carries the junction's position +
   * quaternion; the cube body mesh and the port spheres hang off the container
   * as independent children, so the ports inherit its rotation for
   * rotation-aware raycasting without inheriting the body's helper scale.
   * The container is tracked in _pipeGroup.userData._junctionMeshes for
   * disposal in clearPipes().
   */
  function renderPipeJunction(junctionModel) {
    // `meta.enabled`, not `.enabled`: V4 moved the flag and this line did not
    // follow, so every junction failed the guard and NO cube was ever drawn —
    // in either editor. The caller at :630 already reads `m.meta.enabled`.
    if (!junctionModel || junctionModel.kind !== 'pipe_junction' || !junctionModel.meta.enabled) return;
    const j = junctionModel.cooling || {};
    const anchor = (junctionModel.geometry && junctionModel.geometry.location) || { x: 0, y: 0, z: 0 };
    const q = j.quaternion || { x: 0, y: 0, z: 0, w: 1 };

    // 1. Container: the junction's position + quaternion, and the object
    //    `attachJunctionGizmo` finds and TransformControls rotates (it looks
    //    for `isJunctionBody` + `junctionId` by traversing globalGroup, and
    //    its objectChange handler reads the target's quaternion back into
    //    `cooling.quaternion` — the container is what must carry both).
    //
    //    WP-W4 restructure: the body cube used to BE this object, which is why
    //    it could not join the WP-S1 helper scale registry — a per-frame
    //    mesh.scale on the cube multiplied every port sphere's local DATA
    //    offset (ports at 3% of their offset from the centre up close),
    //    dragging the port indicators away from the pipe endpoints they mark.
    //    Splitting transform (container) from size (body mesh) lets the body
    //    shrink with the camera while the port offsets stay data-exact.
    const container = new THREE.Group();
    container.position.set(anchor.x, anchor.y, anchor.z);
    container.quaternion.set(q.x, q.y, q.z, q.w);
    // `.uid`, not `.id` — same V4 rename. With `undefined` here,
    // `attachJunctionGizmo` could never find the junction it was asked for,
    // and the port spheres below already use `.uid`.
    container.userData = { isJunctionBody: true, junctionId: junctionModel.uid };
    if (typeof globalGroup !== 'undefined') globalGroup.add(container);
    if (_pipeGroup) _pipeGroup.userData._junctionMeshes = _pipeGroup.userData._junctionMeshes || [];
    if (_pipeGroup) _pipeGroup.userData._junctionMeshes.push(container);

    // An explicitly sized physical fitting fills the gap from its common
    // mixing centre to each authored port face. Helpers keep their camera
    // scaling; these pipe legs keep metre geometry.
    if (Number.isFinite(j.fittingDiameterM) && j.fittingDiameterM > 0) {
      (j.ports || []).forEach(function(port) {
        var o = port.offset, end = new THREE.Vector3(o.x,o.y,o.z), length = end.length();
        if (!(length > 0)) return;
        var radius = j.fittingDiameterM / 2;
        var geometry = new THREE.CylinderGeometry(radius*1.1,radius*1.1,length,16);
        _cylinderUv(geometry,radius*1.1,length,'stainless');
        var color = j.loopRole === 'return' ? RETURN_COLOR : SUPPLY_COLOR;
        var leg = new THREE.Mesh(geometry,_liquidMaterial('stainless',{color:color,metalness:.55,roughness:.38}));
        leg.position.copy(end).multiplyScalar(.5);
        leg.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),end.normalize());
        leg.userData={isPipe:true,isPhysicalJunctionLeg:true,junctionId:junctionModel.uid,portId:port.id};
        container.add(leg);
      });
    }

    // 2. Junction body (cube): unit box + registry (WP-S1), registered with
    //    the HALF edge (0.03) of the old 0.06 m cube, so at scale = r the
    //    shown edge is 2r = 0.06 m — the data size — and it shrinks with the
    //    camera up close like every other helper. No children of its own (the
    //    ports are siblings under the container), so scaling it is safe.
    //    Deliberately NOT tagged `isJunctionBody`: the gizmo's traverse takes
    //    the LAST match, and a body-mesh match would attach TransformControls
    //    to a child that rotates without the ports.
    const cubeGeom = new THREE.BoxGeometry(1, 1, 1);
    const cubeMat = new THREE.MeshStandardMaterial({
      color: 0x9c27b0,  // purple — distinct from CDU blue / CRH blue
      roughness: 0.4,
      metalness: 0.3
    });
    const cube = new THREE.Mesh(cubeGeom, cubeMat);
    cube.scale.setScalar(0.03);
    cube.userData = { isJunctionBodyMesh: true, junctionId: junctionModel.uid };
    _registerHelper(cube, 0.03);
    container.add(cube);

    // 3. Port spheres (children of the container so they inherit rotation)
    const ports = j.ports || [];
    ports.forEach(port => {
      const isFree = !port.connectedPipeId;
      const color = isFree ? 0x00ff88 : 0x666666;  // lime green = free, gray = connected
      // Unit sphere + registry (WP-S1): shows at the 15 mm data radius. The
      // container stays at scale 1 (only the body mesh scales), and
      // syncHelperScales divides the parent's world scale out regardless, so
      // the child relationship never distorts the sphere's own size. The
      // local DATA offset is untouched. Unregistered by clearPipes' traverse
      // over the junction container.
      const sphereGeom = new THREE.SphereGeometry(1, 12, 12);
      const sphereMat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.3 });
      const sphere = new THREE.Mesh(sphereGeom, sphereMat);
      sphere.scale.setScalar(0.015);
      sphere.position.set(port.offset.x, port.offset.y, port.offset.z);  // local to container
      _registerHelper(sphere, 0.015);
      sphere.userData = {
        isJunctionPort: true,
        junctionId: junctionModel.uid,
        portId: port.id,
        isFree: isFree
      };
      container.add(sphere);  // attach to container as child
    });
  }

  /**
   * Recompute endpoint waypoints of pipes connected to a rotated junction.
   * Called when the junction's quaternion changes.
   */
  function reroutePipesForJunction(junctionId) {
    if (!window.state) return;
    const junction = window.allModels().find(function(m) { return m.uid === junctionId && m.kind === 'pipe_junction'; });
    if (!junction || !junction.cooling) return;
    const anchor = junction.geometry && junction.geometry.location;
    const q = junction.cooling.quaternion;
    const qt = new THREE.Quaternion(q.x, q.y, q.z, q.w);
    const ports = junction.cooling.ports || [];

    ports.forEach(function(port) {
      if (!port.connectedPipeId) return;
      const pipe = window.allModels().find(function(m) { return m.uid === port.connectedPipeId && m.kind === 'pipe_segment'; });
      if (!pipe || !pipe.geometry) return;
      // World position of this port
      const localOff = new THREE.Vector3(port.offset.x, port.offset.y, port.offset.z);
      localOff.applyQuaternion(qt);
      const worldPos = { x: anchor.x + localOff.x, y: anchor.y + localOff.y, z: anchor.z + localOff.z };
      // Update whichever end of the pipe connects to this port
      const portFullId = junctionId + '.' + port.id;
      const conn = (pipe.cooling && pipe.cooling.connection) || {};
      const wps = pipe.geometry.waypoints || [];
      if (conn.startPortId === portFullId && wps.length > 0) {
        wps[0] = worldPos;
      } else if (conn.endPortId === portFullId && wps.length > 0) {
        wps[wps.length - 1] = worldPos;
      }
    });

    // Rebuild pipe tube meshes
    if (typeof updatePipeGeometry === 'function') updatePipeGeometry();
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Edit mode — enter / exit
  // ─────────────────────────────────────────────────────────────────────────────

  function enterEditMode() {
    if (_editMode) return;
    _editMode = true;
    var state = window.state;
    if (!state) return;
    // Convert auto to manual if needed
    var _nm = window._liqNetMain(false);
    if (!_nm || !_nm.chains || _nm.chains.length === 0) {
      autoToManualConversion();
    }
    window._liqNetMain(true).autoGenerated = false;
    rebuildEditVisuals();
    updatePipeGeometry();
    if (window.requestRender) window.requestRender();
    // Show "Done Editing" button
    var doneBtn = document.getElementById('btn-pipe-done-editing');
    if (doneBtn) doneBtn.style.display = '';
    var editBtn = document.getElementById('btn-pipe-edit-network');
    if (editBtn) editBtn.style.display = 'none';
  }

  function exitEditMode() {
    if (!_editMode) return;
    _editMode = false;
    clearEditVisuals();
    updatePipeGeometry();
    if (window.requestRender) window.requestRender();
    // Swap button visibility
    var doneBtn = document.getElementById('btn-pipe-done-editing');
    if (doneBtn) doneBtn.style.display = 'none';
    var editBtn = document.getElementById('btn-pipe-edit-network');
    if (editBtn) editBtn.style.display = '';
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Auto-to-manual conversion: generate chains from current auto-routing
  // ─────────────────────────────────────────────────────────────────────────────

  function autoToManualConversion() {
    var state = window.state;
    var chains = [];
    var cduModels = window.allModels().filter(function(m) { return m.kind === 'cdu' && m.meta.enabled; });
    var rackByName = {};
    window.allModels().filter(function(m) { return m.kind === 'rack' && m.meta.enabled; }).forEach(function(r) { rackByName[r.meta.name] = r; });

    cduModels.forEach(function(cdu) {
      var servedRacks = (cdu.meta && cdu.meta.racks) || [];
      var supplyPort = getCduPort(cdu, 'supply');
      var returnPort = getCduPort(cdu, 'return');
      var cduName = cdu.meta.name.replace(/[^A-Za-z0-9_]/g, '_');

      servedRacks.forEach(function(rackName) {
        var rack = rackByName[rackName];
        if (!rack) return;
        var rackInlet = getRackPort(rack, 'inlet');
        var rackOutlet = getRackPort(rack, 'outlet');
        var rName = rackName.replace(/[^A-Za-z0-9_]/g, '_');
        var _nmSpec = window._liqNetMain(false);
        var pipeSpec = (_nmSpec && _nmSpec.mainPipeSpec) || 'pipe_25mm';

        var supplyWp = generateRoute(supplyPort, rackInlet).map(function(v) { return {x: +v.x.toFixed(3), y: +v.y.toFixed(3), z: +v.z.toFixed(3)}; });
        chains.push({id: 'auto_' + cduName + '_' + rName + '_supply', path: [
          cduName + '_supply',
          {pipe: pipeSpec, waypoints: supplyWp},
          rName + '_manifold_in'
        ]});

        var returnWp = generateRoute(returnPort, rackOutlet).map(function(v) { return {x: +v.x.toFixed(3), y: +v.y.toFixed(3), z: +v.z.toFixed(3)}; });
        chains.push({id: 'auto_' + cduName + '_' + rName + '_return', path: [
          rName + '_manifold_out',
          {pipe: pipeSpec, waypoints: returnWp},
          cduName + '_return'
        ]});
      });
    });
    window._liqNetMain(true).chains = chains;
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Render from chains (manual mode)
  // ─────────────────────────────────────────────────────────────────────────────

  // Same canonical token registry as resolveChainEquipmentPort, read only.
  function chainCduDisplayEndpoint(token, end) {
    if (typeof token !== 'string' || typeof window.allModels !== 'function') return null;
    var pure = _pure();
    if (!pure || typeof pure.liquidEquipmentPortFrame !== 'function') return null;
    var models = window.allModels();
    for (var i = 0; i < models.length; i++) {
      var model = models[i];
      if (model.kind !== 'cdu') continue;
      var name = String((model.meta && model.meta.name) || '').replace(/[^A-Za-z0-9_]/g, '_');
      var role = token === name + '_supply' ? 'supply' : token === name + '_return' ? 'return' : null;
      if (role) return {modelId: model.uid, end: end, frame: pure.liquidEquipmentPortFrame(model, role, 'top')};
    }
    return null;
  }

  function renderFromChains() {
    // Reuse existing _pipeGroup if updatePipeGeometry pre-initialized one
    // (for orphan junction tracking); otherwise create a fresh one.
    if (!_pipeGroup) {
      _pipeGroup = new THREE.Group();
      _pipeGroup.userData = { isPipeGroup: true };
    }
    var _nmRC = window._liqNetMain(false);
    var chains = (_nmRC && _nmRC.chains) || [];
    chains.forEach(function(chain) {
      var path = chain.path || [];
      path.forEach(function(item, itemIndex) {
        if (typeof item !== 'object' || !item.pipe || !item.waypoints || item.waypoints.length < 2) return;
        var points = item.waypoints.map(function(wp) { return new THREE.Vector3(wp.x, wp.y, wp.z); });
        var start = resolveChainEquipmentPort(path[itemIndex - 1]);
        var end = resolveChainEquipmentPort(path[itemIndex + 1]);
        if (start) points[0].copy(start);
        if (end) points[points.length - 1].copy(end);
        var isSupply = chain.id.indexOf('supply') !== -1;
        var endpoints = [chainCduDisplayEndpoint(path[itemIndex - 1], 'start'),
          chainCduDisplayEndpoint(path[itemIndex + 1], 'end')].filter(Boolean);
        var mesh = createTubeMesh(points, isSupply ? SUPPLY_COLOR : RETURN_COLOR, undefined, null, endpoints);
        if (mesh) _pipeGroup.add(mesh);
      });
    });
    if (_pipeGroup.children.length > 0 && typeof globalGroup !== 'undefined') globalGroup.add(_pipeGroup);
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Edit mode visuals (waypoint spheres + component cubes)
  // ─────────────────────────────────────────────────────────────────────────────

  function rebuildEditVisuals() {
    clearEditVisuals();
    var _nmRV = window._liqNetMain ? window._liqNetMain(false) : null;
    var chains = (_nmRV && _nmRV.chains) || [];
    chains.forEach(function(chain, ci) {
      (chain.path || []).forEach(function(item, ii) {
        if (typeof item === 'object' && item.pipe && item.waypoints) {
          item.waypoints.forEach(function(wp, wi) {
            // Unit sphere + registry (WP-S1): shows at the 30 mm data radius.
            var s = new THREE.Mesh(
              new THREE.SphereGeometry(1, 12, 12),
              new THREE.MeshStandardMaterial({color: 0xffcc00, roughness: 0.3})
            );
            s.scale.setScalar(0.03);
            s.position.set(wp.x, wp.y, wp.z);
            s.userData = {isWaypoint: true, chainIdx: ci, itemIdx: ii, wpIdx: wi};
            // Waypoints ARE raycasted (so they're clickable) — no override
            _registerHelper(s, 0.03);
            if (typeof globalGroup !== 'undefined') globalGroup.add(s);
            _waypointSpheres.push(s);
          });
        }
        if (typeof item === 'object' && (item.valve || item.pump || item.quick_connect || item.heat_exchanger)) {
          var color = item.valve ? 0x00cc44 : item.pump ? 0x2196F3 : item.quick_connect ? 0x888888 : 0xff8800;
          // Unit box + registry (WP-S1): registered with the HALF edge (0.03)
          // of the old 0.06 m cube, so at scale = r the shown edge is 2r =
          // 0.06 m — the data size. No children, so scaling is safe here.
          var c = new THREE.Mesh(
            new THREE.BoxGeometry(1, 1, 1),
            new THREE.MeshStandardMaterial({color: color, roughness: 0.4})
          );
          c.scale.setScalar(0.03);
          c.userData = {isComponentIcon: true, chainIdx: ci, itemIdx: ii};
          _registerHelper(c, 0.03);
          if (typeof globalGroup !== 'undefined') globalGroup.add(c);
          _componentIcons.push(c);
        }
      });
    });
  }

  function clearEditVisuals() {
    _waypointSpheres.forEach(function(s) {
      _unregisterHelper(s);
      if (s.parent) s.parent.remove(s);
      if (s.geometry) s.geometry.dispose();
      if (s.material) s.material.dispose();
    });
    _waypointSpheres = [];
    _componentIcons.forEach(function(s) {
      _unregisterHelper(s);
      if (s.parent) s.parent.remove(s);
      if (s.geometry) s.geometry.dispose();
      if (s.material) s.material.dispose();
    });
    _componentIcons = [];
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Right-click context menu for component insertion
  // ─────────────────────────────────────────────────────────────────────────────

  function showPipeContextMenu(e, chainIdx, segmentIdx) {
    var old = document.getElementById('pipe-context-menu');
    if (old) old.remove();
    var menu = document.createElement('div');
    menu.id = 'pipe-context-menu';
    menu.style.cssText = 'position:fixed;z-index:1000;background:#2a2a3e;border:1px solid #555;border-radius:4px;padding:4px 0;font-size:12px;color:#ccc;min-width:180px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';
    menu.style.left = e.clientX + 'px';
    menu.style.top = e.clientY + 'px';

    [{label: (typeof t === 'function' ? t('liquid.insert_valve') : 'Insert Valve'), type:'valve', spec:'valve_default'},
     {label: (typeof t === 'function' ? t('liquid.insert_pump') : 'Insert Pump'), type:'pump', spec:'pump_default'},
     {label: (typeof t === 'function' ? t('liquid.insert_quick_connect') : 'Insert Quick Connect'), type:'quick_connect', spec:'cp_default'},
     {label: (typeof t === 'function' ? t('liquid.insert_heat_exchanger') : 'Insert Heat Exchanger'), type:'heat_exchanger', spec:'hx_default'}
    ].forEach(function(item) {
      var row = document.createElement('div');
      row.style.cssText = 'padding:6px 16px;cursor:pointer;';
      row.textContent = item.label;
      row.addEventListener('mouseenter', function() { row.style.background = '#3a3a5e'; });
      row.addEventListener('mouseleave', function() { row.style.background = 'transparent'; });
      row.addEventListener('click', function() {
        menu.remove();
        insertComponentInChain(chainIdx, segmentIdx, item.type, item.spec);
      });
      menu.appendChild(row);
    });

    document.body.appendChild(menu);
    setTimeout(function() {
      document.addEventListener('click', function close() {
        if (menu.parentNode) menu.remove();
        document.removeEventListener('click', close);
      }, {once: true});
    }, 10);
  }

  function insertComponentInChain(chainIdx, segmentIdx, compType, specName) {
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('insertComponent');
    var _nmIC = window._liqNetMain(false);
    var chains = _nmIC && _nmIC.chains;
    if (!chains || !chains[chainIdx]) return;
    var path = chains[chainIdx].path;
    var pipeItem = null, pipeIdx = -1, count = 0;
    for (var i = 0; i < path.length; i++) {
      if (typeof path[i] === 'object' && path[i].pipe) {
        if (count === segmentIdx) { pipeItem = path[i]; pipeIdx = i; break; }
        count++;
      }
    }
    if (!pipeItem || pipeIdx < 0) return;

    var jn = chains[chainIdx].id + '_j_' + Date.now();
    var jn2 = jn + '_post';
    var compObj = {};
    compObj[compType] = specName;
    if (compType === 'valve') compObj.opening = 1.0;
    if (compType === 'pump') compObj.rpm = 3000;
    var wp = pipeItem.waypoints || [];
    var mid = Math.floor(wp.length / 2);
    var wp1 = wp.slice(0, Math.max(mid, 1));
    var wp2 = wp.slice(Math.max(mid, 1));
    if (wp1.length < 2) wp1 = wp.slice(0, 2);
    if (wp2.length < 2) wp2 = wp.slice(-2);

    var newItems = [{pipe: pipeItem.pipe, waypoints: wp1}, jn, compObj, jn2, {pipe: pipeItem.pipe, waypoints: wp2}];
    Array.prototype.splice.apply(path, [pipeIdx, 1].concat(newItems));

    rebuildEditVisuals();
    updatePipeGeometry();
    if (window.requestRender) window.requestRender();
    if (typeof logOperation === 'function') logOperation('insertComponent', 'Insert ' + compType, {chainIdx: chainIdx, compType: compType}, 'modify');
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Main function: rebuild all pipe geometry
  // ─────────────────────────────────────────────────────────────────────────────

  /**
   * Main function: rebuild all pipe geometry.
   * Called after buildGeometryData() in doRebuildGeometry.
   */
  function updatePipeGeometry() {
    if (typeof window._beginLiquidPipeDisplay === 'function') window._beginLiquidPipeDisplay();
    try { _updatePipeGeometry(); }
    finally { if (typeof window._endLiquidPipeDisplay === 'function') window._endLiquidPipeDisplay(); }
  }

  /** Refresh actual buffers while keeping result materials and mesh identity. */
  function refreshPipeDisplayGeometry() {
    if (!_pipeGroup || typeof window._displayLiquidPipeGeometry !== 'function') return;
    if (typeof window._beginLiquidPipeDisplay === 'function') window._beginLiquidPipeDisplay();
    try {
      _pipeGroup.traverse(function(mesh) {
        var source = mesh.userData && mesh.userData._pipeDisplaySource;
        if (!mesh.isMesh || !source) return;
        var raw = source.geometry.clone();
        raw.userData = Object.assign({}, source.geometry.userData);
        var displayed = window._displayLiquidPipeGeometry(raw, source.context);
        var next = displayed && displayed.geometry ? displayed.geometry : raw;
        if (next !== raw) raw.dispose();
        var lib = _pbr();
        if (lib) lib.tubeUv(next, source.context.path.getLength(), source.radius, 'pipe-insulation');
        mesh.geometry.dispose();
        mesh.geometry = next;
        var wasVisible = mesh.visible || mesh.userData.pipeDisplayConcealed === true;
        mesh.visible = wasVisible && next.getAttribute('position').count > 0;
        mesh.userData.pipeDisplayConcealed = wasVisible && next.getAttribute('position').count === 0;
        if (displayed && displayed.diagnostic) mesh.userData.pipeDisplayDiagnostic = displayed.diagnostic;
        else delete mesh.userData.pipeDisplayDiagnostic;
      });
    } finally { if (typeof window._endLiquidPipeDisplay === 'function') window._endLiquidPipeDisplay(); }
    if (typeof requestRender === 'function') requestRender();
  }

  function _updatePipeGeometry() {
    clearPipes();

    // Clear stale waypoint spheres — they'll be rebuilt below for enabled pipes only
    if (window._liquidMode && typeof window._liquidMode.clearWaypointSpheres === 'function') {
      window._liquidMode.clearWaypointSpheres();
    }

    // Rack-detail mode re-centres the target rack at the origin while pipes are authored
    // in world coordinates, so they can never align — and showing them defeats the
    // single-rack isolation. Stay cleared until detail mode exits.
    if (typeof window.isRackDetailMode === 'function' && window.isRackDetailMode()) {
      return;
    }

    // Ensure _pipeGroup exists so junction meshes can be tracked for disposal.
    // Junctions are rendered independently of pipe_segments so that orphan
    // junctions (no connected pipes) remain visible after both branch pipes
    // are deleted. renderFromPipeSegmentModels() reuses this group if set.
    if (!_pipeGroup) {
      _pipeGroup = new THREE.Group();
      _pipeGroup.userData = { isPipeGroup: true };
      if (typeof globalGroup !== 'undefined') globalGroup.add(_pipeGroup);
    }

    // Render pipe_junction models unconditionally (independent of pipe_segment
    // presence) — handles orphan junctions after both branch pipes are deleted.
    if (window.state) {
      window.allModels()
        .filter(function(m) { return m.kind === 'pipe_junction' && m.meta.enabled; })
        .forEach(renderPipeJunction);
    }

    // Re-attach junction gizmo if it was previously attached — the rebuild
    // invalidated its target. Runs unconditionally so orphan-junction-only
    // scenes (no pipes, just a lone junction) also refresh the TC target.
    if (window._liquidMode && typeof window._liquidMode.refreshJunctionGizmo === 'function') {
      window._liquidMode.refreshJunctionGizmo();
    }

    // Render from pipe_segment models if any exist (new Phase D+E schema)
    var hasPipeSegments = window.state && window.allModels().some(function(m) { return m.kind === 'pipe_segment' && m.meta.enabled; });
    if (hasPipeSegments) {
      renderFromPipeSegmentModels();
      if (window._liquidMode && typeof window._liquidMode.buildWaypointSpheres === 'function') {
        window._liquidMode.buildWaypointSpheres();
      }
      return;
    }

    // Check if liquid cooling is enabled
    if (!window.state || !window._liqCfg || !window._liqCfg().enabled) return;

    // Manual mode: render from chains
    var _nmUG = window._liqNetMain(false);
    if (_nmUG && !_nmUG.autoGenerated && _nmUG.chains && _nmUG.chains.length > 0) {
      renderFromChains();
      return;
    }

    // Find CDU and Rack models
    var cduModels = window.allModels().filter(function(m) { return m.kind === 'cdu' && m.meta.enabled; });
    var rackModels = window.allModels().filter(function(m) { return m.kind === 'rack' && m.meta.enabled; });
    if (cduModels.length === 0 || rackModels.length === 0) return;

    // Build rack name -> model lookup
    var rackByName = {};
    rackModels.forEach(function(r) { rackByName[r.meta.name] = r; });

    // Reuse existing _pipeGroup if updatePipeGeometry pre-initialized one
    // (for orphan junction tracking); otherwise create a fresh one.
    if (!_pipeGroup) {
      _pipeGroup = new THREE.Group();
      _pipeGroup.userData = { isPipeGroup: true };
    }

    cduModels.forEach(function(cdu) {
      var servedRacks = (cdu.meta && cdu.meta.racks) || [];
      if (servedRacks.length === 0) return;

      var supplyPort = getCduPort(cdu, 'supply');
      var returnPort = getCduPort(cdu, 'return');
      var pure = _pure();
      var supplyFrame = pure && pure.liquidEquipmentPortFrame
        ? pure.liquidEquipmentPortFrame(cdu, 'supply', 'top') : null;
      var returnFrame = pure && pure.liquidEquipmentPortFrame
        ? pure.liquidEquipmentPortFrame(cdu, 'return', 'top') : null;

      // CDU port indicators
      _pipeGroup.add(createPortCoupler(supplyPort, SUPPLY_COLOR, supplyFrame && supplyFrame.direction));
      _pipeGroup.add(createPortCoupler(returnPort, RETURN_COLOR, returnFrame && returnFrame.direction));

      servedRacks.forEach(function(rackName) {
        var rack = rackByName[rackName];
        if (!rack) return;

        var rackInlet = getRackPort(rack, 'inlet');
        var rackOutlet = getRackPort(rack, 'outlet');
        var rackInletFrame = pure && pure.liquidEquipmentPortFrame
          ? pure.liquidEquipmentPortFrame(rack, 'inlet', 'top') : null;
        var rackOutletFrame = pure && pure.liquidEquipmentPortFrame
          ? pure.liquidEquipmentPortFrame(rack, 'outlet', 'top') : null;

        // Supply pipe: CDU supply -> Rack inlet (blue)
        var supplyMesh = createTubeMesh(generateRoute(supplyPort, rackInlet), SUPPLY_COLOR, undefined, null, [{modelId: cdu.uid, end: 'start', frame: supplyFrame}]);
        if (supplyMesh) _pipeGroup.add(supplyMesh);

        // Return pipe: CDU return <- Rack outlet (red)
        var returnMesh = createTubeMesh(generateRoute(returnPort, rackOutlet), RETURN_COLOR, undefined, null, [{modelId: cdu.uid, end: 'start', frame: returnFrame}]);
        if (returnMesh) _pipeGroup.add(returnMesh);

        // Rack port indicators
        _pipeGroup.add(createPortCoupler(rackInlet, SUPPLY_COLOR, rackInletFrame && rackInletFrame.direction));
        _pipeGroup.add(createPortCoupler(rackOutlet, RETURN_COLOR, rackOutletFrame && rackOutletFrame.direction));
      });
    });

    // Render RDHx back panels on racks that have RDHx enabled
    (window.state ? window.allModels() : []).forEach(function(m) {
      var rdhx = m.constructions && m.constructions.rdhx;
      if (m.kind !== 'rack' || !m.meta.enabled || !rdhx || !rdhx.enabled) return;
      var a = m.geometry.location;
      var w = m.geometry.size.x;
      var h = m.geometry.size.y;
      var geometry = new THREE.PlaneGeometry(w, h);
      var material = new THREE.MeshStandardMaterial({
        color: 0x2196F3,
        transparent: true,
        opacity: 0.25,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      var plane = new THREE.Mesh(geometry, material);
      // Position at back face (Y = anchor.y + depth), centered horizontally and vertically
      plane.position.set(a.x + w / 2, a.y + m.geometry.size.z + 0.01, a.z + h / 2);
      plane.rotation.x = -Math.PI / 2; // PlaneGeometry lies in XY by default; rotate to stand upright in XZ
      plane.userData = { isPipe: true };
      plane.raycast = function() {};
      _pipeGroup.add(plane);
    });

    // Add to scene
    if (_pipeGroup.children.length > 0 && typeof globalGroup !== 'undefined') {
      globalGroup.add(_pipeGroup);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Temperature coloring
  // ─────────────────────────────────────────────────────────────────────────────

  var _lcCmapPreset = 'coolwarm';
  var _lcClimMin = null;
  var _lcClimMax = null;

  function _tempToColor(T, Tmin, Tmax) {
    var t = Math.max(0, Math.min(1, (T - Tmin) / (Tmax - Tmin + 1e-6)));
    var cmapFn = window._ppColormapPresets && window._ppColormapPresets[_lcCmapPreset];
    if (cmapFn) {
      var data = cmapFn();
      var cidx = Math.floor(t * 255) * 4;
      return (data[cidx] << 16) | (data[cidx + 1] << 8) | data[cidx + 2];
    }
    var r = Math.round(0x21 + t * (0xF4 - 0x21));
    var g = Math.round(0x96 + t * (0x43 - 0x96));
    var b = Math.round(0xF3 + t * (0x36 - 0xF3));
    return (r << 16) | (g << 8) | b;
  }

  function _isLiquidGadget(ud) {
    return ud && (ud.isPipe || ud.isPipeGroup || ud.isBlindEndCap ||
      ud.isJunctionBody || ud.isJunctionPort ||
      ud.isValve || ud.isTeeJunction || ud.isValveConnection ||
      ud.isWaypoint || ud.isPipeControlPoint ||
      ud.isCpCuConnection || ud.isPortIndicator);
  }

  var _savedMatProps = {};
  var _lcColorbarWidget = null;
  var _hiddenWaypoints = [];

  var _LC_MAT = { metalness: 0.15, roughness: 0.65, emissiveIntensity: 0, opacity: 1.0 };

  function _collectLiquidMeshes() {
    var meshes = [];
    var seen = {};
    function _add(m) { if (!seen[m.uuid]) { seen[m.uuid] = true; meshes.push(m); } }
    if (_pipeGroup) {
      _pipeGroup.traverse(function(c) {
        if ((c.isMesh || c.isLineSegments) && c.userData.isPipe) _add(c);
      });
    }
    if (typeof globalGroup !== 'undefined' && globalGroup) {
      globalGroup.children.forEach(function(top) {
        var ud = top.userData || {};
        if (ud.isWaypoint || ud.isPipeControlPoint) return;
        if (_isLiquidGadget(ud)) {
          top.traverse(function(c) { if (c.isMesh || c.isLineSegments) _add(c); });
        }
        top.traverse(function(c) {
          if ((c.isMesh || c.isLineSegments) && c.userData && c.userData.isLiquidManifold) _add(c);
        });
      });
    }
    return meshes;
  }

  function _hideWaypoints() {
    _hiddenWaypoints = [];
    if (typeof globalGroup === 'undefined' || !globalGroup) return;
    globalGroup.children.forEach(function(top) {
      var ud = top.userData || {};
      if (ud.isWaypoint || ud.isPipeControlPoint) {
        if (top.visible) {
          _hiddenWaypoints.push(top);
          top.visible = false;
        }
      }
    });
  }

  function _showWaypoints() {
    _hiddenWaypoints.forEach(function(obj) { obj.visible = true; });
    _hiddenWaypoints = [];
  }

  function _saveMat(mesh) {
    if (_savedMatProps[mesh.uuid]) return;
    var m = mesh.material;
    _savedMatProps[mesh.uuid] = {
      map: m.map || null,
      color: m.color ? m.color.getHex() : 0x888888,
      metalness: m.metalness,
      roughness: m.roughness,
      emissive: m.emissive ? m.emissive.getHex() : 0,
      emissiveIntensity: m.emissiveIntensity,
      opacity: m.opacity,
      transparent: m.transparent
    };
  }

  function _restoreMat(mesh) {
    var s = _savedMatProps[mesh.uuid];
    if (!s) return;
    var m = mesh.material;
    m.color.setHex(s.color);
    if (m.metalness !== undefined) m.metalness = s.metalness;
    if (m.roughness !== undefined) m.roughness = s.roughness;
    if (m.emissive) m.emissive.setHex(s.emissive);
    m.emissiveIntensity = s.emissiveIntensity;
    m.opacity = s.opacity;
    m.transparent = s.transparent;
    if ('map' in s) m.map = s.map;
    if (m.userData) delete m.userData.pbrMapSuppressed;
    m.needsUpdate = true;
  }

  function colorPipesByTemperature(enabled) {
    var results = window.state ? window.state.liquidResults : null;

    if (enabled && results && results.cell_temp_K && results.cell_temp_K.length > 0) {
      _hideWaypoints();

      var cellTemps = results.cell_temp_K;
      var lastStepTemps = [];
      for (var i = 0; i < cellTemps.length; i++) {
        var row = cellTemps[i];
        lastStepTemps.push(Array.isArray(row) ? row[row.length - 1] : row);
      }

      var autoMin = 1e9, autoMax = -1e9;
      for (var i = 0; i < lastStepTemps.length; i++) {
        if (lastStepTemps[i] < autoMin) autoMin = lastStepTemps[i];
        if (lastStepTemps[i] > autoMax) autoMax = lastStepTemps[i];
      }
      var tMin = (_lcClimMin != null) ? _lcClimMin : autoMin;
      var tMax = (_lcClimMax != null) ? _lcClimMax : autoMax;

      var allMeshes = _collectLiquidMeshes();
      if (allMeshes.length > 0 && lastStepTemps.length > 0) {
        var cellsPer = Math.max(1, Math.floor(lastStepTemps.length / allMeshes.length));
        for (var p = 0; p < allMeshes.length; p++) {
          var mesh = allMeshes[p];
          _saveMat(mesh);
          var m = mesh.material;
          if (m.metalness !== undefined) m.metalness = _LC_MAT.metalness;
          if (m.roughness !== undefined) m.roughness = _LC_MAT.roughness;
          if (m.emissive) m.emissive.setHex(0x000000);
          m.emissiveIntensity = _LC_MAT.emissiveIntensity;
          m.opacity = _LC_MAT.opacity;
          m.transparent = false;
          // A result colour is the colour: drop the surface's colour map while
          // painted (the normal/roughness maps keep the relief) and bring it
          // back on restore. `pbrMapSuppressed` stops a late-arriving texture
          // (js/pbr_materials.js) from re-attaching it mid-display.
          if (m.map) m.map = null;
          if (m.userData) m.userData.pbrMapSuppressed = true;
          m.needsUpdate = true;

          var cStart = p * cellsPer;
          var cEnd = Math.min(cStart + cellsPer, lastStepTemps.length);
          if (cStart >= lastStepTemps.length) cStart = lastStepTemps.length - 1;
          var sum = 0, count = 0;
          for (var c = cStart; c < cEnd; c++) { sum += lastStepTemps[c]; count++; }
          var avgT = count > 0 ? sum / count : (tMin + tMax) / 2;
          m.color.setHex(_tempToColor(avgT, tMin, tMax));
        }
      }

      if (typeof window._ppCreateColorbarWidget === 'function') {
        if (!_lcColorbarWidget) {
          _lcColorbarWidget = window._ppCreateColorbarWidget({
            label: 'Pipe Temperature',
            fieldName: 'T',
            climMin: tMin,
            climMax: tMax,
            cmapPreset: _lcCmapPreset,
            rightOffset: 20,
            onAutoScale: function() { return { min: autoMin, max: autoMax }; },
            onApply: function(newMin, newMax, newCmap) {
              _lcClimMin = newMin; _lcClimMax = newMax; _lcCmapPreset = newCmap;
              colorPipesByTemperature(true);
            }
          });
        } else {
          _lcColorbarWidget.update({ climMin: tMin, climMax: tMax, cmapPreset: _lcCmapPreset });
          _lcColorbarWidget.show();
        }
      }
    } else {
      _showWaypoints();
      var allMeshes = _collectLiquidMeshes();
      allMeshes.forEach(function(mesh) { _restoreMat(mesh); });
      _savedMatProps = {};
      _lcClimMin = null; _lcClimMax = null;
      if (_lcColorbarWidget) { _lcColorbarWidget.destroy(); _lcColorbarWidget = null; }
    }
    var oldBar = document.getElementById('lc-colorbar');
    if (oldBar) oldBar.style.display = 'none';
    if (window.requestRender) window.requestRender();
  }

  /**
   * Show the shared liquid colorbar for a KELVIN range.
   *
   * Kelvin, not Celsius: the widget is told `fieldName: 'T'` and converts for
   * display itself, exactly as it does for the pipe path a few lines above
   * (`climMin: tMin` there is raw `cell_temp_K`). Converting here as well
   * subtracts 273.15 twice and the bar reads −228 °C for a 45 °C chip.
   *
   * Both temperature overlays share the one widget, so the label says which of
   * them last wrote to it.
   */
  function _showColorbar(tMinK, tMaxK) {
    if (typeof window._ppCreateColorbarWidget !== 'function') return;
    if (!_lcColorbarWidget) {
      _lcColorbarWidget = window._ppCreateColorbarWidget({
        label: 'Chip Temperature',
        fieldName: 'T',
        climMin: tMinK,
        climMax: tMaxK,
        cmapPreset: _lcCmapPreset,
        rightOffset: 20,
        onAutoScale: function() { return { min: tMinK, max: tMaxK }; }
      });
    } else {
      _lcColorbarWidget.update({ climMin: tMinK, climMax: tMaxK, cmapPreset: _lcCmapPreset });
      _lcColorbarWidget.show();
    }
  }

  function _hideColorbar() {
    if (_lcColorbarWidget) { _lcColorbarWidget.destroy(); _lcColorbarWidget = null; }
  }

  window._togglePipeTemperatureColoring = function(enabled) {
    colorPipesByTemperature(enabled);
  };

  var _savedEquipColors = {};

  window._toggleServerTemperatureColoring = function(enabled) {
    var results = window.state ? window.state.liquidResults : null;
    if (!results || !results.chip_temp_K || results.chip_temp_K.length === 0) return;

    var chipTemps = results.chip_temp_K;
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

    if (!window.state) return;
    var racks = results.rack_summaries || [];
    var cpIdx = 0;

    if (enabled) {
      racks.forEach(function(rack) {
        var rackModel = window.allModels().find(function(m) { return m.kind === 'rack' && m.meta.name === rack.rack_id; });
        if (!rackModel) return;
        var eqs = Object.values((rackModel.constructions && rackModel.constructions.servers) || {});
        (rack.servers || []).forEach(function(srv) {
          var eq = eqs.find(function(e) { return (e.meta && e.meta.name) === srv.server_id; });
          if (!eq || cpIdx >= lastChip.length) return;
          var T = lastChip[cpIdx];
          cpIdx++;
          if (typeof globalGroup === 'undefined') return;
          globalGroup.traverse(function(child) {
            // `.uid`, not `.id`: the scene tags meshes with `model.uid`
            // (js/geometry_scene.js:1003) and V4 models have no `.id`, so this
            // compared against `undefined` and NO server was ever recoloured.
            // The line above already reads `m.meta.name` the V4 way.
            if (child.isMesh && child.userData.modelId === rackModel.uid && child.userData.equipmentId === eq.uid) {
              _savedEquipColors[child.uuid] = child.material.color.getHex();
              child.material.color.setHex(_tempToColor(T, tMin, tMax));
            }
          });
        });
      });
      // `_updateColorbar` used to be called here and is defined NOWHERE — not
      // in this file, not in the shared modules, not in either editor. Every
      // call threw a ReferenceError that took out the rest of the handler.
      // The pipe path a few lines above owns the real colorbar, so this reuses
      // it rather than reviving a name with no implementation.
      _showColorbar(tMin, tMax);
    } else {
      if (typeof globalGroup !== 'undefined') {
        globalGroup.traverse(function(child) {
          if (child.isMesh && _savedEquipColors[child.uuid] != null) {
            child.material.color.setHex(_savedEquipColors[child.uuid]);
          }
        });
      }
      _savedEquipColors = {};
      // Only when the PIPES are not still coloured: they share this one
      // colorbar, and tearing it down would leave the pipes coloured with no
      // scale to read them against.
      var pipesCb = document.getElementById('lc-color-pipes');
      if (!pipesCb || !pipesCb.checked) _hideColorbar();
    }
    if (window.requestRender) window.requestRender();
  };

  // ─────────────────────────────────────────────────────────────────────────────
  // Expose globally
  // ─────────────────────────────────────────────────────────────────────────────

  window._updatePipeGeometry = updatePipeGeometry;
  window._clearPipeGeometry = clearPipes;
  window._enterPipeEditMode = enterEditMode;
  Object.defineProperty(window, '_pipeRenderGroup', { get: function() { return _pipeGroup; } });
  window._exitPipeEditMode = exitEditMode;
  window._isPipeEditMode = function() { return _editMode; };
  window._showPipeContextMenu = showPipeContextMenu;

  // ─────────────────────────────────────────────────────────────────────────────
  // Pipe control point spheres (shown when pipe is selected)
  // ─────────────────────────────────────────────────────────────────────────────

  let _controlPointSpheres = [];

  function clearPipeControlPoints() {
    _controlPointSpheres.forEach(function(s) {
      if (s.parent) s.parent.remove(s);
      if (s.geometry) s.geometry.dispose();
      if (s.material) s.material.dispose();
    });
    _controlPointSpheres = [];
  }

  function buildPipeControlPoints(pipeId) {
    clearPipeControlPoints();
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return [];
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    if (!pipe || pipe.kind !== 'pipe_segment' || !pipe.geometry || !pipe.geometry.waypoints || !pipe.meta.enabled) return [];

    var wp = pipe.geometry.waypoints;
    var diamM = pipe.geometry.diameterM;
    var diameter = (diamM && diamM > 0) ? diamM : 0.025;
    var loopRole = pipe.cooling && pipe.cooling.loopRole;
    var pipeColor;
    if (loopRole === 'return') pipeColor = RETURN_COLOR;
    else if (loopRole === 'unconnected') pipeColor = UNCONNECTED_COLOR;
    else pipeColor = SUPPLY_COLOR;

    wp.forEach(function(w, i) {
      var isEndpoint = (i === 0 || i === wp.length - 1);
      var radius = isEndpoint ? diameter * 1.5 : diameter * 0.8;
      var color = isEndpoint ? pipeColor : 0xFFEB3B;

      var geo = new THREE.SphereGeometry(radius, 16, 12);
      var mat = new THREE.MeshBasicMaterial({
        color: color,
        transparent: true,
        opacity: 0.6
      });
      var sphere = new THREE.Mesh(geo, mat);
      sphere.position.set(w.x, w.y, w.z);
      sphere.userData = {
        isPipeControlPoint: true,
        pipeId: pipeId,
        waypointIndex: i,
        isEndpoint: isEndpoint
      };
      globalGroup.add(sphere);
      _controlPointSpheres.push(sphere);
    });

    if (typeof requestRender === 'function') requestRender();
    return _controlPointSpheres;
  }

  window._liquidPipeRenderer = window._liquidPipeRenderer || {};
  window._liquidPipeRenderer.buildPipeSegmentWaypointSpheres = buildPipeSegmentWaypointSpheres;
  window._liquidPipeRenderer.buildPipeControlPoints = buildPipeControlPoints;
  window._liquidPipeRenderer.clearPipeControlPoints = clearPipeControlPoints;
  window._liquidPipeRenderer.getCduPort = getCduPort;
  window._liquidPipeRenderer.getRackPort = getRackPort;
  window._liquidPipeRenderer.resolveChainEquipmentPort = resolveChainEquipmentPort;
  window._liquidPipeRenderer.updatePipeGeometry = updatePipeGeometry;
  window._liquidPipeRenderer.refreshPipeDisplayGeometry = refreshPipeDisplayGeometry;
  window._reroutePipesForJunction = reroutePipesForJunction;

  console.log('[liquid_pipe_renderer.js] Pipe renderer loaded');
})();
