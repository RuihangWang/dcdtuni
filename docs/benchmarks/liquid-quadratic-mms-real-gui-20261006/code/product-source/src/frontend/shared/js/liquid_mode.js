/**
 * Liquid cooling mode interactions: pipe drag-connect state machine,
 * port indicator rendering, T-branch junction management.
 */
(function() {
  'use strict';

  // Pipe creation mode state
  let _pipeMode = false;
  let _pipeStartPort = null;
  let _portIndicators = [];
  let _waypointSpheres = [];
  let _junctionTC = null;  // Dedicated TransformControls for junction rotation
  let _junctionTCHelper = null;
  let _junctionTCTarget = null;  // Currently attached junction mesh

  // geometry_pure's helper scale registry (WP-S1), read defensively: this
  // module also loads in test environments where window.geometryPure is not
  // installed, and the indicators must still build at their data size there.
  function _pure() {
    return (typeof window !== 'undefined' && window.geometryPure) || null;
  }
  function _registerHelper(mesh, dataRadius) {
    const p = _pure();
    if (p && typeof p.registerHelper === 'function') p.registerHelper(mesh, dataRadius);
  }
  function _unregisterHelper(mesh) {
    const p = _pure();
    if (p && typeof p.unregisterHelper === 'function') p.unregisterHelper(mesh);
  }

  // js/pbr_materials.js, read defensively like geometryPure above. Every call
  // returns a FRESH material: the legacy temperature overlay recolours valve
  // and tee materials in place.
  function _pbr() {
    return (typeof window !== 'undefined' && window.pbrMaterials) || null;
  }
  function _fittingMaterial(role, params) {
    const lib = _pbr();
    return lib ? lib.material(role, params) : new THREE.MeshStandardMaterial(params);
  }
  function _fittingCylinderUv(geometry, radius, height, role) {
    const lib = _pbr();
    if (lib) lib.cylinderUv(geometry, radius, height, role);
  }

  function enterPipeCreationMode() {
    if (_pipeMode) return;
    _pipeMode = true;
    _buildPortIndicators();
    const btn = document.getElementById('btn-new-pipe');
    if (btn) {
      btn.style.background = 'rgba(255,204,0,0.2)';
      btn.style.borderColor = '#ffcc00';
      btn.style.color = '#ffcc00';
    }
    if (window.requestRender) window.requestRender();
    console.log('[liquid_mode] Pipe creation mode ON');
  }

  function exitPipeCreationMode() {
    _pipeMode = false;
    _clearPortIndicators();
    _pipeStartPort = null;
    const btn = document.getElementById('btn-new-pipe');
    if (btn) {
      btn.style.background = 'rgba(0,180,220,0.08)';
      btn.style.borderColor = 'rgba(0,180,220,0.4)';
      btn.style.color = '#00b4dc';
    }
    if (window.requestRender) window.requestRender();
    console.log('[liquid_mode] Pipe creation mode OFF');
  }

  function _buildPortIndicators() {
    _clearPortIndicators();
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined' || !window.state) return;
    window.allModels().forEach(m => {
      if (!m.meta.enabled) return;
      if (m.kind === 'cdu') {
        _addPortIndicator(m.uid, 'supply', _getCduPortPos(m, 'supply'), 'out');
        _addPortIndicator(m.uid, 'return', _getCduPortPos(m, 'return'), 'in');
      } else if (m.kind === 'cold_plate') {
        _addPortIndicator(m.uid, 'cp_in', _getColdPlatePortPos(m, 'in'), 'in');
        _addPortIndicator(m.uid, 'cp_out', _getColdPlatePortPos(m, 'out'), 'out');
      } else if (m.kind === 'crh') {
        _addPortIndicator(m.uid, 'crh_in', _getCrhPortPos(m, 'in'), 'in');
        _addPortIndicator(m.uid, 'crh_out', _getCrhPortPos(m, 'out'), 'out');
      }
    });
  }

  function _addPortIndicator(modelId, portId, position, type) {
    const color = type === 'in' ? 0x2196F3 : 0xF44336;
    // Unit sphere + registry (WP-S1): shows at the 30 mm data radius, shrinks
    // with the camera up close. The baseline scale keeps the pre-sync size.
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(1, 12, 12),
      new THREE.MeshBasicMaterial({color: color, transparent: true, opacity: 0.9})
    );
    mesh.scale.setScalar(0.03);
    mesh.position.copy(position);
    mesh.userData = {isPortIndicator: true, modelId: modelId, portId: portId, portType: type};
    _registerHelper(mesh, 0.03);
    globalGroup.add(mesh);
    _portIndicators.push(mesh);
  }

  function _clearPortIndicators() {
    _portIndicators.forEach(m => {
      _unregisterHelper(m);
      if (m.parent) m.parent.remove(m);
      if (m.geometry) m.geometry.dispose();
      if (m.material) m.material.dispose();
    });
    _portIndicators = [];
  }

  function _buildWaypointSpheres() {
    _clearWaypointSpheres();
    if (typeof window._liquidPipeRenderer !== 'object'
        || typeof window._liquidPipeRenderer.buildPipeSegmentWaypointSpheres !== 'function') {
      return;
    }
    _waypointSpheres = window._liquidPipeRenderer.buildPipeSegmentWaypointSpheres();
  }

  function _clearWaypointSpheres() {
    _waypointSpheres.forEach(function(s) {
      // Registered by liquid_pipe_renderer.buildPipeSegmentWaypointSpheres;
      // this is the dispose path that destroys them, so unregister here.
      _unregisterHelper(s);
      if (s.parent) s.parent.remove(s);
      if (s.geometry) s.geometry.dispose();
      if (s.material) s.material.dispose();
    });
    _waypointSpheres = [];
  }

  function _getCduPortPos(m, which) {
    const g = m.geometry || {};
    const a = g.location || {};
    const s = g.size || {};
    const w = s.x, d = s.z, h = s.y;
    const cx = a.x + w / 2;
    const z = a.z + h * 0.85;
    const zOffset = which === 'supply' ? 0 : -0.05;
    return new THREE.Vector3(cx, a.y + d + 0.02, z + zOffset);
  }

  function _getColdPlatePortPos(m, which) {
    const g = m.geometry || {};
    const a = g.location || {};
    const s = g.size || {};
    const w = s.x;
    return new THREE.Vector3(
      which === 'in' ? a.x - 0.02 : a.x + w + 0.02,
      a.y + s.z / 2,
      a.z + s.y / 2
    );
  }

  function _getCrhPortPos(m, which) {
    const g = m.geometry || {};
    const a = g.location || {};
    const s = g.size || {};
    const yOff = which === 'in' ? -0.02 : 0.04;
    return new THREE.Vector3(a.x + s.x / 2, a.y + yOff, a.z + s.y * 0.5);
  }

  function _pickPortAtMouse(event) {
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return null;
    const raycaster = new THREE.Raycaster();
    const target = event.target;
    const rect = (target && target.getBoundingClientRect) ? target.getBoundingClientRect() : {left: 0, top: 0, width: window.innerWidth, height: window.innerHeight};
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    const hits = raycaster.intersectObjects(_portIndicators, false);
    return hits.length > 0 ? hits[0].object : null;
  }

  function handlePipeClick(event) {
    if (!_pipeMode) return false;
    const hit = _pickPortAtMouse(event);
    if (!hit) return false;
    if (!_pipeStartPort) {
      if (hit.userData.portType !== 'out') {
        alert(t('liquid.alert_start_outlet'));
        return true;
      }
      _pipeStartPort = {
        modelId: hit.userData.modelId,
        portId: hit.userData.portId,
        position: hit.position.clone(),
        type: 'out'
      };
      return true;
    } else {
      if (hit.userData.portType !== 'in') {
        alert(t('liquid.alert_connect_inlet'));
        return true;
      }
      _createPipeBetween(_pipeStartPort, {
        modelId: hit.userData.modelId,
        portId: hit.userData.portId,
        position: hit.position.clone(),
        type: 'in'
      });
      _pipeStartPort = null;
      exitPipeCreationMode();
      return true;
    }
  }

  function _createPipeBetween(fromPort, toPort) {
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('createPipeSegment');
    if (typeof createPipeSegmentModel !== 'function') {
      alert('createPipeSegmentModel not available');
      return;
    }
    const pipe = createPipeSegmentModel(
      'Pipe_' + fromPort.modelId + '_' + toPort.modelId,
      fromPort.modelId + '.' + fromPort.portId,
      toPort.modelId + '.' + toPort.portId,
      'supply'
    );
    // Generate simple L-shape path
    const midZ = Math.max(fromPort.position.z, toPort.position.z) + 0.1;
    if (!pipe.geometry) pipe.geometry = {};
    pipe.geometry.waypoints = [
      {x: fromPort.position.x, y: fromPort.position.y, z: fromPort.position.z},
      {x: fromPort.position.x, y: fromPort.position.y, z: midZ},
      {x: toPort.position.x, y: fromPort.position.y, z: midZ},
      {x: toPort.position.x, y: toPort.position.y, z: midZ},
      {x: toPort.position.x, y: toPort.position.y, z: toPort.position.z}
    ];
    window.addModel(pipe);
    if (typeof _modelMap !== 'undefined' && _modelMap) _modelMap.set(pipe.uid, pipe);
    if (typeof rebuildGeometry === 'function') rebuildGeometry();
    if (typeof logOperation === 'function') {
      logOperation('createPipeSegment', 'Create pipe segment', {pipeId: pipe.uid}, 'create');
    }
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Junction rotation gizmo (G.5): dedicated TransformControls with Z-axis only
  // ─────────────────────────────────────────────────────────────────────────────

  function _ensureJunctionTC() {
    if (_junctionTC) return _junctionTC;
    if (typeof THREE === 'undefined' || typeof THREE.TransformControls === 'undefined') return null;
    if (typeof camera === 'undefined' || typeof renderer === 'undefined' || typeof scene === 'undefined') return null;

    _junctionTC = new THREE.TransformControls(camera, renderer.domElement);
    _junctionTC.setMode('rotate');
    _junctionTC.setSize(0.9);
    _junctionTC.showX = false;
    _junctionTC.showY = false;
    _junctionTC.showZ = true;
    _junctionTCHelper = _junctionTC.getHelper();
    scene.add(_junctionTCHelper);

    // When the gizmo is being dragged, disable OrbitControls
    _junctionTC.addEventListener('dragging-changed', function(e) {
      if (typeof controls !== 'undefined' && controls) {
        controls.enabled = !e.value;
      }
      if (e.value === true && typeof pushUndoCheckpoint === 'function') {
        pushUndoCheckpoint('rotateJunction');
      }
      if (e.value === false && typeof logOperation === 'function' && _junctionTCTarget) {
        const jid = _junctionTCTarget.userData && _junctionTCTarget.userData.junctionId;
        if (jid) logOperation('rotateJunction', 'Rotated junction ' + jid, { junctionId: jid }, 'modify');
      }
    });

    // On rotation change, write quaternion back to the model and reroute pipes
    _junctionTC.addEventListener('objectChange', function() {
      if (!_junctionTCTarget) return;
      const junctionId = _junctionTCTarget.userData && _junctionTCTarget.userData.junctionId;
      if (!junctionId) return;
      const model = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(junctionId) : null;
      if (!model || model.kind !== 'pipe_junction') return;
      const q = _junctionTCTarget.quaternion;
      if (!model.cooling) model.cooling = {};
      model.cooling.quaternion = { x: q.x, y: q.y, z: q.z, w: q.w };
      // Recompute connected pipe endpoints
      if (typeof window._reroutePipesForJunction === 'function') {
        window._reroutePipesForJunction(junctionId);
      }
    });

    return _junctionTC;
  }

  function attachJunctionGizmo(junctionId) {
    const tc = _ensureJunctionTC();
    if (!tc) return;
    // Find the junction's container in the scene — the Group tagged
    // `isJunctionBody` that carries its position + quaternion (WP-W4; the
    // body cube and the port spheres hang under it, so rotating the container
    // turns the whole junction, and objectChange reads back ITS quaternion).
    let cube = null;
    if (typeof globalGroup !== 'undefined') {
      globalGroup.traverse(function(o) {
        if (o.userData && o.userData.isJunctionBody && o.userData.junctionId === junctionId) {
          cube = o;
        }
      });
    }
    if (!cube) return;
    tc.attach(cube);
    _junctionTCTarget = cube;
  }

  function detachJunctionGizmo() {
    if (_junctionTC) {
      _junctionTC.detach();
    }
    _junctionTCTarget = null;
  }

  /**
   * Re-attach the junction gizmo to the newly-rendered cube after a geometry rebuild.
   * Called by liquid_pipe_renderer.js:updatePipeGeometry() to avoid the gizmo holding a stale reference.
   */
  function refreshJunctionGizmo() {
    if (!_junctionTC || !_junctionTCTarget) return;
    // Preserve the junctionId from the previously attached target
    const prevId = _junctionTCTarget.userData && _junctionTCTarget.userData.junctionId;
    if (!prevId) return;
    // If selection is no longer this junction (or no longer a junction at all), detach
    const sel = (typeof getSelectedModel === 'function') ? getSelectedModel() : null;
    if (!sel || sel.kind !== 'pipe_junction' || sel.uid !== prevId) {
      detachJunctionGizmo();
      return;
    }
    // Re-find the fresh cube in the scene and re-attach
    let newCube = null;
    if (typeof globalGroup !== 'undefined') {
      globalGroup.traverse(function(o) {
        if (o.userData && o.userData.isJunctionBody && o.userData.junctionId === prevId) {
          newCube = o;
        }
      });
    }
    if (newCube) {
      _junctionTC.attach(newCube);
      _junctionTCTarget = newCube;
    } else {
      detachJunctionGizmo();
    }
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Pipe waypoint drag gizmo: TransformControls in translate mode
  // ─────────────────────────────────────────────────────────────────────────────

  let _pipeWaypointTC = null;
  let _pipeWaypointTCHelper = null;
  let _pipeWaypointGizmo = null;
  let _pipeWaypointTarget = null;

  function _ensurePipeWaypointTC() {
    if (_pipeWaypointTC) return _pipeWaypointTC;
    if (typeof THREE === 'undefined' || typeof THREE.TransformControls === 'undefined') return null;
    if (typeof camera === 'undefined' || typeof renderer === 'undefined' || typeof scene === 'undefined') return null;
    if (typeof window.createTranslateGizmo !== 'function') return null;

    // Shared translate gizmo — identical implementation to the model move tool and
    // the streamline-seed ball (window.createTranslateGizmo, in STL_generate.html).
    // It freezes orbit during drag and gives 1:1 cursor tracking + handle highlight.
    _pipeWaypointGizmo = window.createTranslateGizmo(null, {
      space: 'world',
      size: 0.7,
      onDragStart: function() {
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('dragPipeWaypoint');
        _onWaypointDragStart();
      },
      onChange: _syncWaypointFromSphere,
      onDragEnd: _onWaypointDragEnd
    });
    _pipeWaypointTC = _pipeWaypointGizmo.tc;
    _pipeWaypointTCHelper = _pipeWaypointGizmo.helper;
    return _pipeWaypointTC;
  }

  function _attachPipeWaypointGizmo(sphere) {
    var tc = _ensurePipeWaypointTC();
    if (!tc) return;
    sphere.quaternion.identity();
    tc.setSpace('world');
    tc.attach(sphere);
    _pipeWaypointTarget = sphere;
    if (typeof requestRender === 'function') requestRender();
  }

  function _detachPipeWaypointGizmo() {
    _pipeWaypointTarget = null;
    if (_pipeWaypointTC) {
      _pipeWaypointTC.detach();
    }
    if (typeof controls !== 'undefined') controls.enabled = true;
    if (typeof requestRender === 'function') requestRender();
  }

  var _pipeGeomUpdateQueued = false;

  // Drag state: saved position at drag start for 90° corner detection
  var _dragStartPos = null;
  var _cornerInserted = false;

  function _onWaypointDragStart() {
    _cornerInserted = false;
    _dragStartPos = null;
    if (!_pipeWaypointTarget) return;
    var ud = _pipeWaypointTarget.userData;
    if (!ud || !ud.isEndpoint) return;
    _dragStartPos = {
      x: _pipeWaypointTarget.position.x,
      y: _pipeWaypointTarget.position.y,
      z: _pipeWaypointTarget.position.z
    };
  }

  function _syncWaypointFromSphere() {
    if (!_pipeWaypointTarget) return;
    var ud = _pipeWaypointTarget.userData;
    if (!ud || !ud.isPipeControlPoint) return;
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
    if (!pipe || !pipe.geometry || !pipe.geometry.waypoints) return;
    var pCool = pipe.cooling || {};
    var wps = pipe.geometry.waypoints;
    var wp = wps[ud.waypointIndex];
    if (!wp) return;

    if (!ud.isEndpoint) {
      // Middle control point: translate entire pipe
      var dx = _pipeWaypointTarget.position.x - wp.x;
      var dy = _pipeWaypointTarget.position.y - wp.y;
      var dz = _pipeWaypointTarget.position.z - wp.z;
      for (var i = 0; i < wps.length; i++) {
        wps[i].x += dx;
        wps[i].y += dy;
        wps[i].z += dz;
      }
      if (typeof globalGroup !== 'undefined') {
        globalGroup.traverse(function(obj) {
          if (obj.userData && obj.userData.isPipeControlPoint && obj.userData.pipeId === ud.pipeId && obj !== _pipeWaypointTarget) {
            var owp = wps[obj.userData.waypointIndex];
            if (owp) obj.position.set(owp.x, owp.y, owp.z);
          }
        });
      }
      if (pCool.valves && pCool.valves.length > 0) {
        for (var vi = 0; vi < pCool.valves.length; vi++) {
          pCool.valves[vi].position.x += dx;
          pCool.valves[vi].position.y += dy;
          pCool.valves[vi].position.z += dz;
        }
        if (window._liquidMode && window._liquidMode.renderValves) window._liquidMode.renderValves();
      }
    } else {
      // Endpoint: auto-insert 90° corner if dragging perpendicular
      if (_dragStartPos && !_cornerInserted) {
        var dispX = _pipeWaypointTarget.position.x - _dragStartPos.x;
        var dispY = _pipeWaypointTarget.position.y - _dragStartPos.y;
        var dispZ = _pipeWaypointTarget.position.z - _dragStartPos.z;
        var totalDisp = Math.sqrt(dispX*dispX + dispY*dispY + dispZ*dispZ);

        if (totalDisp > 0.03) {
          // Compute pipe axial direction at this endpoint
          var isStart = (ud.waypointIndex === 0);
          var neighborIdx = isStart ? 1 : wps.length - 2;
          var neighbor = wps[neighborIdx];
          var axial = new THREE.Vector3(
            _dragStartPos.x - neighbor.x,
            _dragStartPos.y - neighbor.y,
            _dragStartPos.z - neighbor.z
          ).normalize();

          var disp = new THREE.Vector3(dispX, dispY, dispZ);
          var axialComp = Math.abs(disp.dot(axial));
          var perpComp = Math.sqrt(Math.max(0, totalDisp*totalDisp - axialComp*axialComp));

          // If perpendicular > axial: insert corner at drag start position
          if (perpComp > axialComp && perpComp > 0.02) {
            var corner = { x: _dragStartPos.x, y: _dragStartPos.y, z: _dragStartPos.z };
            if (isStart) {
              wps.splice(1, 0, corner);
              // waypointIndex stays 0 (we insert AFTER index 0)
            } else {
              wps.splice(wps.length - 1, 0, corner);
              ud.waypointIndex = wps.length - 1;
            }
            _cornerInserted = true;
          }
        }
      }

      // Update endpoint waypoint to sphere position
      wp = wps[ud.waypointIndex];
      if (wp) {
        wp.x = _pipeWaypointTarget.position.x;
        wp.y = _pipeWaypointTarget.position.y;
        wp.z = _pipeWaypointTarget.position.z;
      }
    }

    if (!_pipeGeomUpdateQueued) {
      _pipeGeomUpdateQueued = true;
      requestAnimationFrame(function() {
        _pipeGeomUpdateQueued = false;
        if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) {
          window._liquidPipeRenderer.updatePipeGeometry();
        }
        if (_pipeWaypointTC && _pipeWaypointTarget && !_pipeWaypointTarget.parent) {
          var ud = _pipeWaypointTarget.userData;
          if (ud && ud.pipeId) {
            var _newSphere = null;
            if (typeof globalGroup !== 'undefined') {
              globalGroup.traverse(function(obj) {
                if (obj.userData && obj.userData.isPipeControlPoint && obj.userData.pipeId === ud.pipeId && obj.userData.waypointIndex === ud.waypointIndex) _newSphere = obj;
              });
            }
            if (_newSphere) {
              _pipeWaypointTarget = _newSphere;
              _pipeWaypointTC.attach(_newSphere);
            } else {
              _pipeWaypointTC.detach();
              _pipeWaypointTarget = null;
            }
          }
        }
        if (typeof requestRender === 'function') requestRender();
      });
    }
    if (ud.isEndpoint) {
      _updateSnapPreview();
      _checkDisconnectOnDrag(pipe, ud);
    }
  }

  function _onWaypointDragEnd() {
    _lastDragEndTime = Date.now();
    var hadCorner = _cornerInserted;
    _cornerInserted = false;
    _dragStartPos = null;
    _checkSnapOnDragEnd();
    if (typeof logOperation === 'function' && _pipeWaypointTarget) {
      logOperation('dragPipeWaypoint', 'Drag pipe waypoint', {
        pipeId: _pipeWaypointTarget.userData.pipeId,
        waypointIndex: _pipeWaypointTarget.userData.waypointIndex
      }, 'modify');
    }
    // If corner was inserted, rebuild control points in next frame
    // to avoid disrupting TransformControls internal state
    if (hadCorner && _pipeWaypointTarget) {
      var pipeId = _pipeWaypointTarget.userData.pipeId;
      var wpIdx = _pipeWaypointTarget.userData.waypointIndex;
      setTimeout(function() {
        if (window._liquidPipeRenderer && window._liquidPipeRenderer.buildPipeControlPoints) {
          if (_pipeWaypointTC) _pipeWaypointTC.detach();
          window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
          var newSphere = null;
          if (typeof globalGroup !== 'undefined') {
            globalGroup.traverse(function(obj) {
              if (obj.userData && obj.userData.isPipeControlPoint && obj.userData.pipeId === pipeId && obj.userData.waypointIndex === wpIdx) {
                newSphere = obj;
              }
            });
          }
          if (newSphere) {
            _pipeWaypointTarget = newSphere;
            _attachPipeWaypointGizmo(newSphere);
          }
        }
        if (typeof updateUI === 'function') updateUI();
      }, 50);
    }
  }

  var _lastDragEndTime = 0;

  function handleControlPointClick(event, maxDistance) {
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return false;
    if (typeof renderer === 'undefined' || !renderer.domElement) return false;
    var _maxDist = (typeof maxDistance === 'number') ? maxDistance : Infinity;

    // Suppress click events that fire immediately after a gizmo drag ends
    if (Date.now() - _lastDragEndTime < 200) return true;

    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);

    var controlPoints = [];
    if (typeof globalGroup !== 'undefined') {
      globalGroup.traverse(function(obj) {
        if (obj.userData && obj.userData.isPipeControlPoint) {
          obj.updateMatrixWorld(true);
          controlPoints.push(obj);
        }
      });
    }
    if (controlPoints.length > 0) {
      var hits = raycaster.intersectObjects(controlPoints, false);
      if (hits.length > 0 && hits[0].distance < _maxDist) {
        var chosen = hits[0].object;
        for (var hi = 1; hi < hits.length; hi++) {
          if (hits[hi].object.userData.isEndpoint && !chosen.userData.isEndpoint &&
              hits[hi].distance - hits[0].distance < 0.3) {
            chosen = hits[hi].object;
            break;
          }
        }
        _attachPipeWaypointGizmo(chosen);
        return true;
      }
    }

    // Second: try pipe tube mesh — if hit and closer than model, select pipe
    var pipeMeshes = [];
    if (typeof globalGroup !== 'undefined') {
      globalGroup.traverse(function(obj) {
        if (obj.isMesh && obj.userData && obj.userData.isPipe && obj.userData.pipeModelId) pipeMeshes.push(obj);
      });
    }
    if (pipeMeshes.length > 0) {
      var pipeHits = raycaster.intersectObjects(pipeMeshes, false);
      if (pipeHits.length > 0 && pipeHits[0].distance < _maxDist) {
        var pipeModelId = pipeHits[0].object.userData.pipeModelId;
        if (typeof setSelection === 'function') setSelection('model', pipeModelId);
        if (window._liquidPipeRenderer && window._liquidPipeRenderer.buildPipeControlPoints) {
          window._liquidPipeRenderer.buildPipeControlPoints(pipeModelId);
        }
        if (typeof updateUI === 'function') updateUI();
        return true;
      }
    }

    // No hit on any pipe element — detach gizmo
    if (_pipeWaypointTarget) {
      _detachPipeWaypointGizmo();
    }
    return false;
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Snap system: auto-connect pipe endpoints to CDU/RACK ports
  // ─────────────────────────────────────────────────────────────────────────────

  var SNAP_PREVIEW_DISTANCE = 0.3;
  var SNAP_DISCONNECT_DISTANCE = 0.4;
  var _snapHighlightMesh = null;
  var _snapAnimFrame = null;

  // CDU containment-based snap: pipe endpoint inside CDU box → connect
  var _cduHighlightMesh = null;
  var _cduHighlightAnimId = null;

  function _isPointInsideCdu(point, cduModel) {
    var g = cduModel.geometry || {};
    var a = g.location || {};
    var s = g.size || {};
    var W = s.x, D = s.z, H = s.y;
    return point.x >= a.x && point.x <= a.x + W &&
           point.y >= a.y && point.y <= a.y + H &&
           point.z >= a.z && point.z <= a.z + D;
  }

  function _isCduPort(portId) {
    if (!portId) return false;
    return (portId.endsWith('.supply') || portId.endsWith('.return')) && portId.indexOf('.manifold_') < 0;
  }

  function _showCduSnapHighlight(cduModel) {
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return;
    var g = cduModel.geometry || {};
    var a = g.location || {};
    var s = g.size || {};
    var W = s.x, D = s.z, H = s.y;
    if (!_cduHighlightMesh) {
      var geo = new THREE.BoxGeometry(1, 1, 1);
      var mat = new THREE.MeshBasicMaterial({
        color: 0xFFD700, transparent: true, opacity: 0.12,
        depthTest: false, side: THREE.DoubleSide
      });
      _cduHighlightMesh = new THREE.Mesh(geo, mat);
      _cduHighlightMesh.renderOrder = 998;
      globalGroup.add(_cduHighlightMesh);
      if (!_cduHighlightAnimId) {
        _cduHighlightAnimId = setInterval(function() {
          if (!_cduHighlightMesh) return;
          _cduHighlightMesh.material.opacity = 0.08 + 0.12 * Math.abs(Math.sin(Date.now() * 0.004));
          if (typeof requestRender === 'function') requestRender();
        }, 50);
      }
    }
    _cduHighlightMesh.scale.set(W, H, D);
    _cduHighlightMesh.position.set(a.x + W / 2, a.y + H / 2, a.z + D / 2);
  }

  function _hideCduSnapHighlight() {
    if (_cduHighlightMesh) {
      if (_cduHighlightMesh.parent) _cduHighlightMesh.parent.remove(_cduHighlightMesh);
      if (_cduHighlightMesh.geometry) _cduHighlightMesh.geometry.dispose();
      if (_cduHighlightMesh.material) _cduHighlightMesh.material.dispose();
      _cduHighlightMesh = null;
    }
    if (_cduHighlightAnimId) { clearInterval(_cduHighlightAnimId); _cduHighlightAnimId = null; }
  }

  function _collectSnapTargets() {
    var targets = [];
    if (!window.state) return targets;
    var getCduPortFn = (window._liquidPipeRenderer && window._liquidPipeRenderer.getCduPort)
      ? window._liquidPipeRenderer.getCduPort : null;

    window.allModels().forEach(function(m) {
      if (!m.meta.enabled) return;

      if (m.kind === 'cdu' && getCduPortFn) {
        var sp = getCduPortFn(m, 'supply');
        var rp = getCduPortFn(m, 'return');
        if (sp) targets.push({ modelId: m.uid, portType: 'supply', portId: m.uid + '.supply', position: sp, color: 'supply' });
        if (rp) targets.push({ modelId: m.uid, portType: 'return', portId: m.uid + '.return', position: rp, color: 'return' });
      }

      if (m.kind === 'rack' && m.geometry) {
        ['supply', 'return'].forEach(function(pt) {
          ['top', 'bottom'].forEach(function(end) {
            var pos = window._getManifoldEndpoint ? window._getManifoldEndpoint(m, pt, end) : null;
            if (pos) targets.push({
              modelId: m.uid, portType: pt,
              portId: m.uid + '.manifold_' + pt + '_' + end,
              position: pos, color: pt
            });
          });
        });
      }

      // Pipe endpoints: snap pipe-to-pipe (exclude the pipe being dragged,
      // endpoints already docked to a device port, and blind ends)
      var mGeom = m.geometry || {};
      var mCool = m.cooling || {};
      var mConn = mCool.connection || {};
      if (m.kind === 'pipe_segment' && mGeom.waypoints && mGeom.waypoints.length >= 2) {
        var draggingPipeId = _pipeWaypointTarget && _pipeWaypointTarget.userData ? _pipeWaypointTarget.userData.pipeId : null;
        if (m.uid !== draggingPipeId) {
          var wps = mGeom.waypoints;
          var pipeColor = mCool.loopRole || 'unconnected';
          var blindEnds = mConn.blindEnds || { start: false, end: false };
          if (!mConn.startPortId && !blindEnds.start) {
            var startPos = new THREE.Vector3(wps[0].x, wps[0].y, wps[0].z);
            targets.push({ modelId: m.uid, portType: pipeColor, portId: m.uid + '.start', position: startPos, color: pipeColor });
          }
          if (!mConn.endPortId && !blindEnds.end) {
            var endPos = new THREE.Vector3(wps[wps.length-1].x, wps[wps.length-1].y, wps[wps.length-1].z);
            targets.push({ modelId: m.uid, portType: pipeColor, portId: m.uid + '.end', position: endPos, color: pipeColor });
          }
        }
      }
    });
    return targets;
  }

  function _updateSnapPreview() {
    if (!_pipeWaypointTarget || !_pipeWaypointTarget.userData || !_pipeWaypointTarget.userData.isEndpoint) {
      _hideSnapHighlight();
      _hideCduSnapHighlight();
      return;
    }

    var _snapUd = _pipeWaypointTarget.userData;
    var _snapPipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(_snapUd.pipeId) : null;
    var _isStartEnd = _snapUd.waypointIndex === 0;

    // Already-connected CDU endpoints must not re-snap
    var _snapConn = (_snapPipe && _snapPipe.cooling && _snapPipe.cooling.connection) || null;
    if (_snapConn) {
      var _curPortId = _isStartEnd ? _snapConn.startPortId : _snapConn.endPortId;
      if (_curPortId && _isCduPort(_curPortId)) {
        _hideSnapHighlight();
        _hideCduSnapHighlight();
        return;
      }
    }

    var pos = _pipeWaypointTarget.position;

    // CDU containment: endpoint inside CDU box → show yellow CDU highlight
    // Checked BEFORE blind-end guard so blind-end endpoints CAN connect to CDU
    var insideCdu = null;
    if (window.state) {
      var _cms = window.allModels();
      for (var ci = 0; ci < _cms.length; ci++) {
        var cm = _cms[ci];
        if (cm.kind === 'cdu' && cm.enabled && _isPointInsideCdu(pos, cm)) {
          insideCdu = cm;
          break;
        }
      }
    }
    if (insideCdu) {
      var ud = _pipeWaypointTarget.userData;
      var _pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
      var _pipeConn = (_pipe && _pipe.cooling && _pipe.cooling.connection) || null;
      var _curPort = _pipeConn ? (ud.waypointIndex === 0 ? _pipeConn.startPortId : _pipeConn.endPortId) : '';
      if (_curPort && _curPort.indexOf(insideCdu.id + '.') === 0) {
        _hideSnapHighlight();
        _hideCduSnapHighlight();
        return;
      }
      _hideSnapHighlight();
      _showCduSnapHighlight(insideCdu);
      return;
    }
    _hideCduSnapHighlight();

    // Blind-end endpoints must not snap to OTHER PIPES (but CDU snap above is OK)
    if (_snapConn) {
      var _be = _snapConn.blindEnds || { start: false, end: false };
      if ((_isStartEnd && _be.start) || (!_isStartEnd && _be.end)) {
        _hideSnapHighlight();
        return;
      }
    }

    // Distance-based snap for non-CDU targets (rack manifold, pipe merge)
    var targets = _collectSnapTargets();
    var closest = null;
    var closestDist = Infinity;
    targets.forEach(function(t) {
      if (_isCduPort(t.portId)) return;
      var d = pos.distanceTo(t.position);
      if (d < SNAP_PREVIEW_DISTANCE && d < closestDist) {
        closest = t;
        closestDist = d;
      }
    });
    if (closest) {
      _showSnapHighlight(closest);
    } else {
      _hideSnapHighlight();
    }
  }

  function _showSnapHighlight(target) {
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return;
    if (!_snapHighlightMesh) {
      var geo = new THREE.RingGeometry(0.06, 0.08, 32);
      var mat = new THREE.MeshBasicMaterial({
        color: target.color === 'return' ? 0xF44336 : 0x2196F3,
        transparent: true, opacity: 0.7,
        side: THREE.DoubleSide, depthTest: false
      });
      _snapHighlightMesh = new THREE.Mesh(geo, mat);
      _snapHighlightMesh.renderOrder = 999;
      globalGroup.add(_snapHighlightMesh);
      _startSnapAnimation();
    }
    _snapHighlightMesh.material.color.set(target.color === 'return' ? 0xF44336 : 0x2196F3);
    _snapHighlightMesh.position.copy(target.position);
    if (typeof camera !== 'undefined') _snapHighlightMesh.lookAt(camera.position);
  }

  function _hideSnapHighlight() {
    if (_snapHighlightMesh) {
      if (_snapHighlightMesh.parent) _snapHighlightMesh.parent.remove(_snapHighlightMesh);
      if (_snapHighlightMesh.geometry) _snapHighlightMesh.geometry.dispose();
      if (_snapHighlightMesh.material) _snapHighlightMesh.material.dispose();
      _snapHighlightMesh = null;
    }
    if (_snapAnimFrame) {
      cancelAnimationFrame(_snapAnimFrame);
      _snapAnimFrame = null;
    }
  }

  function _startSnapAnimation() {
    function animate() {
      if (!_snapHighlightMesh) return;
      _snapHighlightMesh.material.opacity = 0.3 + 0.5 * Math.abs(Math.sin(Date.now() * 0.005));
      if (typeof camera !== 'undefined') _snapHighlightMesh.lookAt(camera.position);
      if (typeof requestRender === 'function') requestRender();
      _snapAnimFrame = requestAnimationFrame(animate);
    }
    _snapAnimFrame = requestAnimationFrame(animate);
  }

  function _checkSnapOnDragEnd() {
    if (!_pipeWaypointTarget || !_pipeWaypointTarget.userData || !_pipeWaypointTarget.userData.isEndpoint) {
      _hideSnapHighlight();
      _hideCduSnapHighlight();
      return;
    }
    var ud = _pipeWaypointTarget.userData;
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
    if (!pipe || pipe.kind !== 'pipe_segment') { _hideSnapHighlight(); _hideCduSnapHighlight(); return; }
    var pGeom = pipe.geometry || {};
    var pCool = (pipe.cooling = pipe.cooling || {});
    var pConn = (pCool.connection = pCool.connection || {});

    var pos = _pipeWaypointTarget.position;
    var wpIdx = ud.waypointIndex;
    var isStart = (wpIdx === 0);

    // Blind-end endpoints must not snap or connect
    var _beSnap = pConn.blindEnds || { start: false, end: false };
    if ((isStart && _beSnap.start) || (!isStart && _beSnap.end)) {
      _hideSnapHighlight();
      _hideCduSnapHighlight();
      return;
    }

    // 1. CDU containment: endpoint inside CDU → connect (keep endpoint position)
    var insideCdu = null;
    if (window.state) {
      var _cms = window.allModels();
      for (var ci = 0; ci < _cms.length; ci++) {
        var cm = _cms[ci];
        if (cm.kind === 'cdu' && cm.enabled && _isPointInsideCdu(pos, cm)) {
          insideCdu = cm;
          break;
        }
      }
    }
    if (insideCdu) {
      var circuitType = _getPipeCircuitType(pipe);
      var portType = circuitType || pCool.loopRole || 'supply';
      if (portType === 'unconnected') {
        var getCduPortFn = (window._liquidPipeRenderer && window._liquidPipeRenderer.getCduPort) || null;
        if (getCduPortFn) {
          var sp = getCduPortFn(insideCdu, 'supply');
          var rp = getCduPortFn(insideCdu, 'return');
          if (sp && rp) portType = pos.distanceTo(sp) <= pos.distanceTo(rp) ? 'supply' : 'return';
        }
      }
      if (isStart) pConn.startPortId = insideCdu.id + '.' + portType;
      else pConn.endPortId = insideCdu.id + '.' + portType;
      _updatePipeColor(pipe);
      _hideCduSnapHighlight();
      _hideSnapHighlight();
      requestAnimationFrame(function() {
        if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
        if (typeof requestRender === 'function') requestRender();
      });
      return;
    }
    _hideCduSnapHighlight();

    // 2. Distance-based snap for non-CDU targets
    var targets = _collectSnapTargets();
    var closest = null;
    var closestDist = Infinity;
    targets.forEach(function(t) {
      if (_isCduPort(t.portId)) return;
      var d = pos.distanceTo(t.position);
      if (d < SNAP_PREVIEW_DISTANCE && d < closestDist) {
        closest = t;
        closestDist = d;
      }
    });

    if (closest) {
      var isPipeMerge = closest.portId && (closest.portId.endsWith('.start') || closest.portId.endsWith('.end'));
      if (isPipeMerge) {
        _mergePipes(pipe, isStart, closest);
        _hideSnapHighlight();
        _pipeWaypointTC.detach();
        _pipeWaypointTarget = null;
        requestAnimationFrame(function() {
          if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
          if (window._liquidPipeRenderer && window._liquidPipeRenderer.clearPipeControlPoints) window._liquidPipeRenderer.clearPipeControlPoints();
          if (typeof updateUI === 'function') updateUI();
        });
        return;
      }

      _pipeWaypointTarget.position.copy(closest.position);
      pGeom.waypoints[wpIdx] = { x: closest.position.x, y: closest.position.y, z: closest.position.z };
      if (isStart) pConn.startPortId = closest.portId;
      else pConn.endPortId = closest.portId;
      _updatePipeColor(pipe);
    } else {
      // Disconnect if dragged away from non-CDU port
      var currentPortId = isStart ? pConn.startPortId : pConn.endPortId;
      if (currentPortId && !_isCduPort(currentPortId)) {
        if (isStart) pConn.startPortId = '';
        else pConn.endPortId = '';
        _updatePipeColor(pipe);
      }
    }

    _hideSnapHighlight();
    requestAnimationFrame(function() {
      if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
      if (typeof requestRender === 'function') requestRender();
    });
  }

  function _mergePipes(draggingPipe, draggingIsStart, snapTarget) {
    var otherPipeId = snapTarget.modelId;
    var otherIsStart = snapTarget.portId.endsWith('.start');
    var otherPipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(otherPipeId) : null;
    var otherGeom = (otherPipe && otherPipe.geometry) || null;
    if (!otherPipe || !otherGeom || !otherGeom.waypoints) return;
    var otherConn = (otherPipe.cooling && otherPipe.cooling.connection) || {};
    var dragGeom = (draggingPipe.geometry = draggingPipe.geometry || {});
    var dragConn = ((draggingPipe.cooling = draggingPipe.cooling || {}).connection = (draggingPipe.cooling.connection || {}));

    var draggingWps = dragGeom.waypoints.slice();
    var otherWps = otherGeom.waypoints.slice();

    // Determine merged waypoint order:
    // dragging pipe's start → ... → dragging pipe's end
    // We want: far end of dragging pipe → ... → snap point → ... → far end of other pipe
    var merged = [];

    if (draggingIsStart && otherIsStart) {
      // dragging start snaps to other start: reverse dragging, then other (skip first of other = shared point)
      draggingWps.reverse();
      merged = draggingWps.concat(otherWps.slice(1));
    } else if (draggingIsStart && !otherIsStart) {
      // dragging start snaps to other end: reverse dragging, then reverse other (skip first = shared)
      draggingWps.reverse();
      otherWps.reverse();
      merged = draggingWps.concat(otherWps.slice(1));
    } else if (!draggingIsStart && otherIsStart) {
      // dragging end snaps to other start: dragging as-is, then other (skip first = shared)
      merged = draggingWps.concat(otherWps.slice(1));
    } else {
      // dragging end snaps to other end: dragging as-is, then reverse other (skip first = shared)
      otherWps.reverse();
      merged = draggingWps.concat(otherWps.slice(1));
    }

    // Inherit port connections from both pipes
    var startPortId, endPortId;
    if (draggingIsStart) {
      startPortId = dragConn.endPortId || '';
      endPortId = otherIsStart ? (otherConn.endPortId || '') : (otherConn.startPortId || '');
    } else {
      startPortId = dragConn.startPortId || '';
      endPortId = otherIsStart ? (otherConn.endPortId || '') : (otherConn.startPortId || '');
    }

    // Update dragging pipe with merged data
    dragGeom.waypoints = merged;
    dragConn.startPortId = startPortId;
    dragConn.endPortId = endPortId;
    _updatePipeColor(draggingPipe);

    // Remove other pipe from state
    if (window.state && otherPipe) {
      window.removeModelByUid(otherPipe.id);
    }
    if (typeof _modelMap !== 'undefined' && _modelMap) {
      _modelMap.delete(otherPipeId);
    }

    if (typeof setSelection === 'function') setSelection('model', draggingPipe.id);

    if (typeof logOperation === 'function') {
      logOperation('mergePipes', 'Merge pipe ' + otherPipe.name + ' into ' + draggingPipe.name, {
        mergedInto: draggingPipe.id, removed: otherPipeId, waypointCount: merged.length
      }, 'modify');
    }
  }

  function _checkDisconnectOnDrag(pipe, ud) {
    var isStart = (ud.waypointIndex === 0);
    var pConn = (pipe.cooling && pipe.cooling.connection) || {};
    var portId = isStart ? pConn.startPortId : pConn.endPortId;
    if (!portId) return;
    var pos = _pipeWaypointTarget.position;

    // CDU port: disconnect when endpoint leaves the CDU bounding box
    if (_isCduPort(portId)) {
      var cduId = portId.split('.')[0];
      var cduModel = window.state ? window.allModels().find(function(m) { return m.uid === cduId; }) : null;
      if (cduModel && !_isPointInsideCdu(pos, cduModel)) {
        if (isStart) pConn.startPortId = '';
        else pConn.endPortId = '';
        _updatePipeColor(pipe);
      }
      return;
    }

    // Non-CDU port: disconnect by distance
    var targets = _collectSnapTargets();
    var match = targets.find(function(t) { return t.portId === portId; });
    if (match && pos.distanceTo(match.position) > SNAP_DISCONNECT_DISTANCE) {
      if (isStart) pConn.startPortId = '';
      else pConn.endPortId = '';
      _updatePipeColor(pipe);
    }
  }

  function _updatePipeColor(pipe) {
    var pCool = (pipe.cooling = pipe.cooling || {});
    var pConn = pCool.connection || {};
    var startPort = pConn.startPortId;
    var endPort = pConn.endPortId;
    var color = 'unconnected';
    if (startPort || endPort) {
      var portId = startPort || endPort;
      var targets = _collectSnapTargets();
      var match = targets.find(function(t) { return t.portId === portId; });
      if (match) color = match.color;
    }
    if (color === 'unconnected' && pCool.valves) {
      var targets2 = null;
      for (var vi = 0; vi < pCool.valves.length; vi++) {
        var v = pCool.valves[vi];
        if (v.connectedManifoldPortId) {
          if (!targets2) targets2 = _collectSnapTargets();
          var vm = targets2.find(function(t) { return t.portId === v.connectedManifoldPortId; });
          if (vm && vm.color !== 'unconnected') { color = vm.color; break; }
        }
      }
    }
    pCool.loopRole = color;
  }

  function _getPipeCircuitType(pipe) {
    var pCool = pipe.cooling || {};
    var pConn = pCool.connection || {};
    var targets = _collectSnapTargets();
    if (pConn.startPortId || pConn.endPortId) {
      var portId = pConn.startPortId || pConn.endPortId;
      var m = targets.find(function(t) { return t.portId === portId; });
      if (m && m.color !== 'unconnected') return m.color;
    }
    if (pCool.valves) {
      for (var i = 0; i < pCool.valves.length; i++) {
        if (pCool.valves[i].connectedManifoldPortId) {
          var vm = targets.find(function(t) { return t.portId === pCool.valves[i].connectedManifoldPortId; });
          if (vm && vm.color !== 'unconnected') return vm.color;
        }
      }
    }
    return null;
  }

  window._upgradeToTBranch = function(pipeId, waypointIndex) {
    const pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    if (!pipe || pipe.kind !== 'pipe_segment') {
      console.warn('[liquid_mode] _upgradeToTBranch: pipe not found', pipeId);
      return null;
    }
    const wps = (pipe.geometry && pipe.geometry.waypoints) || [];
    if (waypointIndex <= 0 || waypointIndex >= wps.length - 1) {
      alert(t('liquid.alert_upgrade_intermediate_only'));
      return null;
    }
    const W = wps[waypointIndex];

    // Verify factories exist BEFORE pushing undo checkpoint to avoid orphan checkpoints on failure
    if (typeof createPipeJunctionModel !== 'function') {
      console.error('[liquid_mode] createPipeJunctionModel not found');
      return null;
    }
    if (typeof createPipeSegmentModel !== 'function') {
      console.error('[liquid_mode] createPipeSegmentModel not found');
      return null;
    }

    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('upgradeToTBranch');

    // 1. Create the junction model at the waypoint position
    const junction = createPipeJunctionModel(
      'TBranch_' + pipe.meta.name + '_' + waypointIndex,
      pipeId,
      { x: W.x, y: W.y, z: W.z }
    );
    // Define 3 ports (local offsets)
    const srcConn = (pipe.cooling && pipe.cooling.connection) || {};
    const srcCool = pipe.cooling || {};
    if (!junction.cooling) junction.cooling = {};
    junction.cooling.type = 'T_branch';
    junction.cooling.quaternion = { x: 0, y: 0, z: 0, w: 1 };
    junction.cooling.ports = [
      { id: 'port_1', offset: { x: -0.04, y: 0, z: 0 }, type: 'inout', connectedPipeId: null },
      { id: 'port_2', offset: { x:  0.04, y: 0, z: 0 }, type: 'inout', connectedPipeId: null },
      { id: 'port_3', offset: { x: 0, y: 0, z:  0.04 }, type: 'out',   connectedPipeId: null }
    ];
    junction.connectedPipes = [];

    // 2. Create Pipe_A (original start → junction.port_1)
    const pipeA = createPipeSegmentModel(
      pipe.meta.name + '_A',
      srcConn.startPortId,
      junction.uid + '.port_1',
      srcCool.loopRole || 'supply'
    );
    if (!pipeA.geometry) pipeA.geometry = {};
    if (!pipeA.cooling) pipeA.cooling = {};
    pipeA.geometry.waypoints = wps.slice(0, waypointIndex + 1).map(p => ({x: p.x, y: p.y, z: p.z}));
    pipeA.cooling.spec = srcCool.spec;

    // 3. Create Pipe_B (junction.port_2 → original end)
    const pipeB = createPipeSegmentModel(
      pipe.meta.name + '_B',
      junction.uid + '.port_2',
      srcConn.endPortId,
      srcCool.loopRole || 'supply'
    );
    if (!pipeB.geometry) pipeB.geometry = {};
    if (!pipeB.cooling) pipeB.cooling = {};
    pipeB.geometry.waypoints = wps.slice(waypointIndex).map(p => ({x: p.x, y: p.y, z: p.z}));
    pipeB.cooling.spec = srcCool.spec;

    // 4. Wire up port connections on the junction
    junction.cooling.ports[0].connectedPipeId = pipeA.uid;
    junction.cooling.ports[1].connectedPipeId = pipeB.uid;

    // 5. Delete the original pipe from state
    window.removeModelByUid(pipeId);
    if (typeof _modelMap !== 'undefined' && _modelMap) _modelMap.delete(pipeId);

    // 6. Push new models
    window.addModel(pipeA);
    window.addModel(pipeB);
    window.addModel(junction);
    if (typeof _modelMap !== 'undefined' && _modelMap) {
      _modelMap.set(pipeA.uid, pipeA);
      _modelMap.set(pipeB.uid, pipeB);
      _modelMap.set(junction.uid, junction);
    }

    // 7. Rebuild geometry first (so the cube exists), then select (so the gizmo can attach)
    if (typeof rebuildGeometry === 'function') rebuildGeometry();
    if (typeof setSelection === 'function') setSelection('model', junction.uid);
    if (typeof logOperation === 'function') {
      logOperation('upgradeToTBranch', 'Split pipe at waypoint ' + waypointIndex,
        { originalPipeId: pipeId, junctionId: junction.uid, pipeAId: pipeA.uid, pipeBId: pipeB.uid },
        'create');
    }
    return junction.uid;
  };

  /**
   * Right-click handler — shows context menu if a waypoint sphere is hit.
   */
  function handleContextMenu(event) {
    if (typeof isLiquidMode !== 'function' || !isLiquidMode()) return;
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return;

    // Compute closest regular model hit distance for depth comparison
    var _cmMaxDist = Infinity;
    var _modelMeshes = [];
    if (typeof globalGroup !== 'undefined') {
      globalGroup.traverse(function(obj) {
        if (obj.isMesh && obj.userData && obj.userData.modelId &&
            !obj.userData.isPipe && !obj.userData.isValve && !obj.userData.isPipeControlPoint &&
            !obj.userData.isValveConnection && !obj.userData.isRackPartition) {
          _modelMeshes.push(obj);
        }
      });
    }
    if (_modelMeshes.length > 0) {
      var _r2 = new THREE.Raycaster();
      var _tgt = event.target;
      var _rect2 = (_tgt && _tgt.getBoundingClientRect) ? _tgt.getBoundingClientRect() : {left:0,top:0,width:window.innerWidth,height:window.innerHeight};
      var _m2 = new THREE.Vector2(((event.clientX - _rect2.left) / _rect2.width) * 2 - 1, -((event.clientY - _rect2.top) / _rect2.height) * 2 + 1);
      _r2.setFromCamera(_m2, camera);
      var _mh = _r2.intersectObjects(_modelMeshes, false);
      if (_mh.length > 0) _cmMaxDist = _mh[0].distance;
    }

    // Check pipe control points first
    if (handleControlPointContextMenu(event, _cmMaxDist)) return;
    // Build waypoint spheres on demand
    if (_waypointSpheres.length === 0) _buildWaypointSpheres();
    if (_waypointSpheres.length === 0) return;
    var _raycaster = new THREE.Raycaster();
    var _target = event.target;
    var _rect = (_target && _target.getBoundingClientRect) ? _target.getBoundingClientRect() : {left:0,top:0,width:window.innerWidth,height:window.innerHeight};
    var _mouse = new THREE.Vector2(((event.clientX - _rect.left) / _rect.width) * 2 - 1, -((event.clientY - _rect.top) / _rect.height) * 2 + 1);
    _raycaster.setFromCamera(_mouse, camera);
    var hits = _raycaster.intersectObjects(_waypointSpheres, false);
    if (hits.length > 0 && hits[0].distance < _cmMaxDist) {
      var hit = hits[0].object;
      if (hit.userData && hit.userData.isWaypoint) {
        event.preventDefault();
        event.stopPropagation();
        _showWaypointContextMenu(event.clientX, event.clientY, hit.userData.pipeSegmentId, hit.userData.waypointIndex);
        return;
      }
    }
    // Check valve right-click
    if (_valveMeshes.length > 0) {
      var valveHits = _raycaster.intersectObjects(_valveMeshes, true);
      if (valveHits.length > 0 && valveHits[0].distance < _cmMaxDist) {
        event.preventDefault();
        event.stopPropagation();
        var valveMesh = valveHits[0].object;
        while (valveMesh && !(valveMesh.userData && valveMesh.userData.isValve)) valveMesh = valveMesh.parent;
        if (valveMesh && valveMesh.userData && valveMesh.userData.isValve) {
          _showValveContextMenu(event.clientX, event.clientY, valveMesh.userData.pipeId, valveMesh.userData.valveId);
          return;
        }
      }
    }
    // No waypoint/valve hit — check pipe tube mesh
    var pipeMeshes = [];
    if (typeof globalGroup !== 'undefined') {
      globalGroup.traverse(function(obj) {
        if (obj.isMesh && obj.userData && obj.userData.isPipe && obj.userData.pipeModelId) pipeMeshes.push(obj);
      });
    }
    if (pipeMeshes.length > 0) {
      var pipeHits = _raycaster.intersectObjects(pipeMeshes, false);
      if (pipeHits.length > 0 && pipeHits[0].distance < _cmMaxDist) {
        event.preventDefault();
        event.stopPropagation();
        var hitPoint = pipeHits[0].point.clone();
        if (typeof globalGroup !== 'undefined') globalGroup.worldToLocal(hitPoint);
        var pipeModelId = pipeHits[0].object.userData.pipeModelId;
        _showPipeBodyContextMenu(event.clientX, event.clientY, pipeModelId, hitPoint);
      }
    }
  }

  function _showWaypointContextMenu(clientX, clientY, pipeSegmentId, waypointIndex) {
    // Waypoint right-click is now a no-op; T-Junctions are added via pipe body menu
  }

  function _closeContextMenu() {
    const menu = document.getElementById('liquid-pipe-context-menu');
    if (menu && menu.parentNode) menu.parentNode.removeChild(menu);
    document.removeEventListener('keydown', _escapeCloseContextMenu);
  }

  function _escapeCloseContextMenu(e) {
    if (e.key === 'Escape') _closeContextMenu();
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Pipe body right-click: Flow Control Valve
  // ─────────────────────────────────────────────────────────────────────────────

  var _valveMeshes = [];
  var _valveConnectionLines = [];
  var _selectedValve = null;
  var _selectedValves = [];
  var _valveDragLine = null;
  var _valveDragging = false;

  function _showPipeBodyContextMenu(cx, cy, pipeId, hitPoint) {
    _closeContextMenu();
    var menu = document.createElement('div');
    menu.id = 'liquid-pipe-context-menu';
    menu.style.cssText = 'position:fixed;left:' + cx + 'px;top:' + cy + 'px;z-index:10000;background:#1e1e2e;border:1px solid #444;border-radius:4px;padding:4px 0;min-width:260px;box-shadow:0 4px 12px rgba(0,0,0,0.5);font-size:12px;color:#ccc;';

    var menuItems = [
      { label: t('liquid.menu_add_valve_above'), type: 'flow_control', side: 'above', color: '#ccc' },
      { label: t('liquid.menu_add_valve_below'), type: 'flow_control', side: 'below', color: '#ccc' },
      { label: t('liquid.menu_add_tee_above'), type: 'tee_junction', side: 'above', color: '#26C6DA' },
      { label: t('liquid.menu_add_tee_below'), type: 'tee_junction', side: 'below', color: '#26C6DA' }
    ];
    menuItems.forEach(function(mi) {
      var item = document.createElement('div');
      item.textContent = mi.label;
      item.style.cssText = 'padding:6px 12px;cursor:pointer;font-size:12px;color:' + mi.color + ';';
      item.addEventListener('mouseenter', function() { this.style.background = '#333'; });
      item.addEventListener('mouseleave', function() { this.style.background = 'none'; });
      item.addEventListener('click', function() {
        _closeContextMenu();
        _addFitting(pipeId, hitPoint, mi.type, mi.side);
      });
      menu.appendChild(item);
    });

    document.body.appendChild(menu);
    setTimeout(function() {
      document.addEventListener('click', _closeContextMenu, { once: true });
      document.addEventListener('keydown', _escapeCloseContextMenu);
    }, 10);
  }

  function _makeMenuItem(label, color, onClick) {
    var item = document.createElement('div');
    item.textContent = label;
    item.style.cssText = 'padding:6px 12px;cursor:pointer;font-size:12px;color:' + (color || '#ccc') + ';';
    item.addEventListener('mouseenter', function() { this.style.background = '#333'; });
    item.addEventListener('mouseleave', function() { this.style.background = 'none'; });
    item.addEventListener('click', onClick);
    return item;
  }

  function _rotateValvesBySide(valveIds) {
    if (valveIds.length === 0) return;
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('rotateValves');
    valveIds.forEach(function(vid) {
      window.allModels().forEach(function(m) {
        var mValves = m.cooling && m.cooling.valves;
        if (m.kind !== 'pipe_segment' || !mValves) return;
        var v = mValves.find(function(vv) { return vv.id === vid; });
        if (!v) return;
        v.side = (v.side === 'above') ? 'below' : 'above';
      });
    });
    _renderValves();
    _deselectAllValves();
    if (typeof requestRender === 'function') requestRender();
    if (typeof logOperation === 'function') logOperation('flipValves', 'Flip fittings above/below', { count: valveIds.length }, 'modify');
  }

  function _showValveContextMenu(cx, cy, pipeId, valveId) {
    _closeContextMenu();
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    var pipeValves = pipe && pipe.cooling && pipe.cooling.valves;
    var valve = pipeValves ? pipeValves.find(function(v) { return v.id === valveId; }) : null;
    var isTee = valve && valve.type === 'tee_junction';
    var menu = document.createElement('div');
    menu.id = 'liquid-pipe-context-menu';
    menu.style.cssText = 'position:fixed;left:' + cx + 'px;top:' + cy + 'px;z-index:10000;background:#1e1e2e;border:1px solid #444;border-radius:4px;padding:4px 0;min-width:200px;box-shadow:0 4px 12px rgba(0,0,0,0.5);font-size:12px;color:#ccc;';

    var targetIds = _selectedValves.length > 1
      ? _selectedValves.map(function(m) { return m.userData.valveId; })
      : [valveId];
    var countLabel = targetIds.length > 1 ? ' (' + targetIds.length + ')' : '';

    menu.appendChild(_makeMenuItem(t('liquid.menu_flip_side') + countLabel, '#FFD600', function() {
      _closeContextMenu();
      _rotateValvesBySide(targetIds);
    }));

    var sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:#444;margin:4px 0;';
    menu.appendChild(sep);

    if (targetIds.length > 1) {
      menu.appendChild(_makeMenuItem(t('liquid.menu_remove_selected') + countLabel, '#F44336', function() {
        _closeContextMenu();
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('removeValves');
        targetIds.forEach(function(vid) {
          window.allModels().forEach(function(m) {
            if (m.kind !== 'pipe_segment' || !m.cooling || !m.cooling.valves) return;
            m.cooling.valves = m.cooling.valves.filter(function(v) { return v.id !== vid; });
          });
        });
        _deselectAllValves();
        _renderValves();
        if (typeof updateUI === 'function') updateUI();
        if (typeof logOperation === 'function') logOperation('removeValves', 'Remove ' + targetIds.length + ' fittings', {}, 'modify');
      }));
    } else {
      menu.appendChild(_makeMenuItem(isTee ? t('liquid.menu_remove_tee') : t('liquid.menu_remove_valve'), '#F44336', function() {
        _closeContextMenu();
        _removeValve(pipeId, valveId);
      }));
    }

    document.body.appendChild(menu);
    setTimeout(function() {
      document.addEventListener('click', _closeContextMenu, { once: true });
      document.addEventListener('keydown', _escapeCloseContextMenu);
    }, 10);
  }

  function _removeValve(pipeId, valveId) {
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    if (!pipe || !pipe.cooling || !pipe.cooling.valves) return;
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('removeValve');
    pipe.cooling.valves = pipe.cooling.valves.filter(function(v) { return v.id !== valveId; });
    _deselectValve();
    _renderValves();
    if (typeof updateUI === 'function') updateUI();
    if (typeof logOperation === 'function') logOperation('removeValve', 'Remove flow control valve', { pipeId: pipeId, valveId: valveId }, 'modify');
  }

  function _projectPointOnPipeCenterline(pipe, point) {
    var wps = pipe.geometry && pipe.geometry.waypoints;
    if (!wps || wps.length < 2) return point;
    var bestPoint = null;
    var bestDist = Infinity;
    for (var i = 0; i < wps.length - 1; i++) {
      var a = new THREE.Vector3(wps[i].x, wps[i].y, wps[i].z);
      var b = new THREE.Vector3(wps[i + 1].x, wps[i + 1].y, wps[i + 1].z);
      var ab = b.clone().sub(a);
      var ap = point.clone().sub(a);
      var t = Math.max(0, Math.min(1, ap.dot(ab) / ab.dot(ab)));
      var proj = a.clone().add(ab.multiplyScalar(t));
      var d = proj.distanceTo(point);
      if (d < bestDist) { bestDist = d; bestPoint = proj; }
    }
    return bestPoint || point;
  }

  function _getPipeDirectionAtPoint(pipe, pos) {
    var wps = pipe.geometry && pipe.geometry.waypoints;
    if (!wps || wps.length < 2) return new THREE.Vector3(1, 0, 0);
    var bestDir = new THREE.Vector3(1, 0, 0);
    var bestDist = Infinity;
    for (var i = 0; i < wps.length - 1; i++) {
      var a = new THREE.Vector3(wps[i].x, wps[i].y, wps[i].z);
      var b = new THREE.Vector3(wps[i + 1].x, wps[i + 1].y, wps[i + 1].z);
      var ab = b.clone().sub(a);
      var ap = new THREE.Vector3(pos.x, pos.y, pos.z).sub(a);
      var t = Math.max(0, Math.min(1, ap.dot(ab) / ab.dot(ab)));
      var proj = a.clone().add(ab.clone().multiplyScalar(t));
      var d = proj.distanceTo(new THREE.Vector3(pos.x, pos.y, pos.z));
      if (d < bestDist) {
        bestDist = d;
        bestDir = ab.normalize();
      }
    }
    return bestDir.length() > 1e-8 ? bestDir.normalize() : new THREE.Vector3(1, 0, 0);
  }

  function _addFitting(pipeId, position, fittingType, side) {
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    if (!pipe || !pipe.geometry) return;
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('addFitting');
    if (!pipe.cooling) pipe.cooling = {};
    if (!pipe.cooling.valves) pipe.cooling.valves = [];
    var pos = (typeof THREE !== 'undefined') ? _projectPointOnPipeCenterline(pipe, new THREE.Vector3(position.x, position.y, position.z)) : position;
    var prefix = fittingType === 'tee_junction' ? 'tee_' : 'valve_';
    var fittingId = prefix + Date.now();
    pipe.cooling.valves.push({
      id: fittingId,
      type: fittingType || 'flow_control',
      side: side || 'below',
      position: { x: pos.x, y: pos.y, z: pos.z },
      connectedManifoldPortId: '',
      connectedRackId: ''
    });
    _renderValves();
    if (typeof updateUI === 'function') updateUI();
    var label = fittingType === 'tee_junction' ? 'Add T-junction' : 'Add flow control valve';
    if (typeof logOperation === 'function') logOperation('addFitting', label, { pipeId: pipeId, fittingId: fittingId, type: fittingType, side: side || 'below' }, 'modify');
  }

  function _addFlowControlValve(pipeId, position) {
    _addFitting(pipeId, position, 'flow_control');
  }

  function _clearValveMeshes() {
    _valveMeshes.forEach(function(m) {
      m.traverse(function(child) {
        if (child.geometry) child.geometry.dispose();
        if (child.material) child.material.dispose();
      });
      if (m.parent) m.parent.remove(m);
    });
    _valveMeshes = [];
    _valveConnectionLines.forEach(function(l) {
      if (l.parent) l.parent.remove(l);
      if (l.geometry) l.geometry.dispose();
      if (l.material) l.material.dispose();
    });
    _valveConnectionLines = [];
  }

  function _renderValves() {
    _clearValveMeshes();
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return;
    if (!window.state) return;
    // Hide valves in rack-detail mode — the detail rack is re-centred at the origin
    // while valves sit on world-positioned pipes, so they'd clutter the isolated view.
    if (typeof window.isRackDetailMode === 'function' && window.isRackDetailMode()) return;

    window.allModels().forEach(function(m) {
      var mCool = m.cooling || {};
      if (m.kind !== 'pipe_segment' || !mCool.valves) return;
      if (!m.meta.enabled) return;
      mCool.valves.forEach(function(valve) {
        var diameter = valve.type !== 'tee_junction' && valve.branchDiameterM || (m.geometry && m.geometry.diameterM) || 0.025;
        var isTee = valve.type === 'tee_junction';
        var _pipeColor = mCool.loopRole || 'unconnected';
        var sideSign = (valve.side === 'above') ? 1 : -1;
        var body;

        if (isTee) {
          var teeColor = _pipeColor === 'supply' ? 0x26C6DA : _pipeColor === 'return' ? 0xEF5350 : 0x78909C;
          var throughRadius = diameter * 0.65;
          var throughLength = diameter * 3;
          var branchRadius = valve.branchDiameterM ? valve.branchDiameterM * 0.65 : diameter * 0.5;
          var branchHeight = diameter * 1.5;

          var group = new THREE.Group();

          var throughGeo = new THREE.CylinderGeometry(throughRadius, throughRadius, throughLength, 16);
          _fittingCylinderUv(throughGeo, throughRadius, throughLength, 'stainless');
          var throughMat = _fittingMaterial('stainless', { color: teeColor, metalness: 0.5, roughness: 0.4 });
          var throughMesh = new THREE.Mesh(throughGeo, throughMat);
          throughMesh.rotation.z = Math.PI / 2;
          group.add(throughMesh);

          var branchGeo = new THREE.CylinderGeometry(branchRadius, branchRadius, branchHeight, 12);
          _fittingCylinderUv(branchGeo, branchRadius, branchHeight, 'stainless');
          var branchMat = _fittingMaterial('stainless', { color: teeColor, metalness: 0.5, roughness: 0.4 });
          var branchMesh = new THREE.Mesh(branchGeo, branchMat);
          branchMesh.position.y = sideSign * branchHeight / 2;
          group.add(branchMesh);

          var ringGeo = new THREE.TorusGeometry(throughRadius * 1.1, throughRadius * 0.15, 8, 24);
          var ringMat = _fittingMaterial('stainless', { color: 0x90A4AE, metalness: 0.6, roughness: 0.3 });
          var ringMesh = new THREE.Mesh(ringGeo, ringMat);
          ringMesh.rotation.x = Math.PI / 2;
          group.add(ringMesh);

          group.position.set(valve.position.x, valve.position.y, valve.position.z);
          var teeDir = _getPipeDirectionAtPoint(m, valve.position);
          group.quaternion.setFromUnitVectors(new THREE.Vector3(1, 0, 0), teeDir);
          if (valve.branchWaypoints && valve.branchWaypoints.length > 1) {
            var branchDir = new THREE.Vector3(valve.branchWaypoints[1].x-valve.position.x,
              valve.branchWaypoints[1].y-valve.position.y,valve.branchWaypoints[1].z-valve.position.z).normalize();
            var localBranchDir = branchDir.applyQuaternion(group.quaternion.clone().invert());
            branchMesh.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),localBranchDir);
            branchMesh.position.copy(localBranchDir.multiplyScalar(branchHeight/2));
          }
          group.userData = { isValve: true, isTeeJunction: true, valveId: valve.id, pipeId: m.uid, side: valve.side || 'below' };
          globalGroup.add(group);
          _valveMeshes.push(group);
          body = group;
        } else {
          // Gate valve: compact hexagonal body + stem + handwheel
          var _valveColor = _pipeColor === 'supply' ? 0x2196F3 : _pipeColor === 'return' ? 0xF44336 : 0xFF9800;
          var _metalColor = 0x90A4AE;
          var vR = diameter * 0.55;
          var bodyH = diameter * 0.8;
          var stemR = diameter * 0.12;
          var stemH = diameter * 0.6;
          var wheelR = diameter * 0.4;
          var totalH = bodyH + stemH;

          var group = new THREE.Group();

          // Hexagonal body (6-sided prism)
          var hexGeo = new THREE.CylinderGeometry(vR, vR, bodyH, 6);
          // Cast brass. Its hue is in the texture, so the loop's colour code
          // (supply / return / unconnected) moves to the flanges below.
          var hexMat = _fittingMaterial('brass', { metalness: 0.85, roughness: 0.36 });
          var hexMesh = new THREE.Mesh(hexGeo, hexMat);
          group.add(hexMesh);

          // Flanges (thin discs at top/bottom of body)
          var flangeGeo = new THREE.CylinderGeometry(vR * 1.15, vR * 1.15, bodyH * 0.12, 16);
          // Colour-coded painted flanges: the valve's loop colour lives here now.
          var flangeMat = _fittingMaterial('painted-steel', { color: _valveColor, metalness: 0.3, roughness: 0.45 });
          var flangeTop = new THREE.Mesh(flangeGeo, flangeMat);
          flangeTop.position.y = bodyH * 0.44;
          group.add(flangeTop);
          // A second material, not flangeMat.clone(): a clone made before the
          // textures land would never receive them.
          var flangeBot = new THREE.Mesh(flangeGeo.clone(),
            _fittingMaterial('painted-steel', { color: _valveColor, metalness: 0.3, roughness: 0.45 }));
          flangeBot.position.y = -bodyH * 0.44;
          group.add(flangeBot);

          // Stem
          var stemGeo = new THREE.CylinderGeometry(stemR, stemR, stemH, 8);
          var stemMat = _fittingMaterial('stainless', { color: _metalColor, metalness: 0.7, roughness: 0.3 });
          var stemMesh = new THREE.Mesh(stemGeo, stemMat);
          stemMesh.position.y = bodyH / 2 + stemH / 2;
          group.add(stemMesh);

          // Handwheel (torus + cross spokes + center hub)
          var wheelY = bodyH / 2 + stemH;
          var spokeR = stemR * 0.5;
          var wheelGeo = new THREE.TorusGeometry(wheelR, stemR * 0.8, 6, 16);
          // Rubber-gripped handwheel (rim, spokes and hub share it, as before).
          var wheelMat = _fittingMaterial('rubber', { color: 0x333333, metalness: 0.0, roughness: 0.75 });
          var wheelMesh = new THREE.Mesh(wheelGeo, wheelMat);
          wheelMesh.position.y = wheelY;
          wheelMesh.rotation.x = Math.PI / 2;
          group.add(wheelMesh);
          // Cross spokes (4 bars connecting hub to rim)
          for (var si = 0; si < 4; si++) {
            var spokeGeo = new THREE.CylinderGeometry(spokeR, spokeR, wheelR * 2, 4);
            var spokeMesh = new THREE.Mesh(spokeGeo, wheelMat);
            spokeMesh.position.y = wheelY;
            spokeMesh.rotation.x = Math.PI / 2;
            spokeMesh.rotation.z = si * Math.PI / 4;
            group.add(spokeMesh);
          }
          // Center hub
          var hubGeo = new THREE.CylinderGeometry(stemR * 1.5, stemR * 1.5, stemR * 2, 8);
          var hubMesh = new THREE.Mesh(hubGeo, wheelMat);
          hubMesh.position.y = wheelY;
          group.add(hubMesh);

          group.position.set(valve.position.x, valve.position.y + (valve.seriesWithValveId ? 0 : sideSign * (bodyH / 2 + diameter / 2)), valve.position.z);
          group.userData = { isValve: true, isTeeJunction: false, valveId: valve.id, pipeId: m.uid, side: valve.side || 'below' };
          if (sideSign < 0) group.rotation.z = Math.PI;
          globalGroup.add(group);
          _valveMeshes.push(group);
          body = group;
        }

        if (valve.connectedManifoldPortId && valve.connectedRackId) {
          var rackModel = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(valve.connectedRackId) : null;
          if (rackModel && window._getManifoldEndpoint) {
            var portType = valve.connectedManifoldPortId.indexOf('supply') >= 0 ? 'supply' : 'return';
            var portEnd = valve.connectedManifoldPortId.indexOf('top') >= 0 ? 'top' : 'bottom';
            var manifoldPos = window._getManifoldEndpoint(rackModel, portType, portEnd);
            if (manifoldPos) {
              var connStart = isTee
                ? new THREE.Vector3(valve.position.x, valve.position.y + sideSign * diameter * 0.75, valve.position.z)
                : new THREE.Vector3(valve.position.x, body.position.y, valve.position.z);
              var linePath = new THREE.CurvePath();
              if (valve.branchWaypoints) {
                var branchPoints = valve.branchWaypoints.map(p => new THREE.Vector3(p.x,p.y,p.z));
                branchPoints[0].set(valve.position.x,valve.position.y,valve.position.z);
                branchPoints[branchPoints.length-1].copy(manifoldPos);
                for (var bi=0;bi<branchPoints.length-1;bi++) linePath.add(new THREE.LineCurve3(branchPoints[bi],branchPoints[bi+1]));
              } else linePath.add(new THREE.LineCurve3(connStart, manifoldPos));
              var thinRadius = valve.branchDiameterM ? valve.branchDiameterM/2 : (isTee ? diameter * 0.25 : diameter * 0.1);
              var connColor = portType === 'supply' ? 0x2196F3 : 0xF44336;
              var connMat = _fittingMaterial('rubber', { color: connColor, metalness: 0.0, roughness: 0.7, transparent: true, opacity: 0.7 });
              if (valve.branchWaypoints) {
                // Build each authored straight separately: uniform arc samples
                // over a CurvePath can miss a corner and bridge across it.
                for (var si=0;si<branchPoints.length-1;si++) {
                  var straight=new THREE.LineCurve3(branchPoints[si],branchPoints[si+1]);
                  var connGeo=new THREE.TubeGeometry(straight,1,thinRadius,8,false);
                  if (_pbr()) _pbr().tubeUv(connGeo,straight.getLength(),thinRadius,'rubber');
                  var connMesh=new THREE.Mesh(connGeo,connMat);
                  connMesh.userData={isValveConnection:true,valveId:valve.id};
                  globalGroup.add(connMesh);_valveConnectionLines.push(connMesh);
                  if (si>0) {
                    var bendMesh=new THREE.Mesh(new THREE.SphereGeometry(thinRadius,8,6),connMat);
                    bendMesh.position.copy(branchPoints[si]);
                    bendMesh.userData={isValveConnection:true,valveId:valve.id};
                    globalGroup.add(bendMesh);_valveConnectionLines.push(bendMesh);
                  }
                }
              } else {
                var connGeo=new THREE.TubeGeometry(linePath,8,thinRadius,6,false);
                if (_pbr()) _pbr().tubeUv(connGeo,linePath.getLength(),thinRadius,'rubber');
                var connMesh=new THREE.Mesh(connGeo,connMat);
                connMesh.userData={isValveConnection:true,valveId:valve.id};
                globalGroup.add(connMesh);_valveConnectionLines.push(connMesh);
              }
            }
          }
        }
      });
    });
    if (typeof requestRender === 'function') requestRender();
  }

  function handleValveClick(event, maxDistance) {
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return false;
    if (typeof renderer === 'undefined' || !renderer.domElement) return false;
    if (_valveMeshes.length === 0) return false;
    var _maxDist = (typeof maxDistance === 'number') ? maxDistance : Infinity;
    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    var hits = raycaster.intersectObjects(_valveMeshes, true);
    if (hits.length === 0 || hits[0].distance >= _maxDist) {
      if (_selectedValves.length > 0) { _deselectAllValves(); }
      return false;
    }
    var hitObj = hits[0].object;
    var valve = _findValveRoot(hitObj);
    var isMulti = event.shiftKey || event.ctrlKey || event.metaKey;
    if (isMulti) {
      var idx = _selectedValves.indexOf(valve);
      if (idx >= 0) {
        _setEmissive(valve, 0x000000, 0);
        _selectedValves.splice(idx, 1);
      } else {
        _selectedValves.push(valve);
        _setEmissive(valve, 0xFFFF00, 0.4);
      }
      _selectedValve = _selectedValves.length > 0 ? _selectedValves[_selectedValves.length - 1] : null;
      _removeValveActionButtons();
      if (_selectedValves.length === 1) _createValveActionButtons();
      _renderValveInspector();
    } else {
      _deselectAllValves();
      _selectedValve = valve;
      _selectedValves = [valve];
      _setEmissive(valve, 0xFFFF00, 0.4);
      _createValveActionButtons();
      _renderValveInspector();
    }
    if (typeof requestRender === 'function') requestRender();
    return true;
  }

  function _findValveRoot(obj) {
    var cur = obj;
    while (cur) {
      if (cur.userData && cur.userData.isValve && cur.userData.valveId) return cur;
      cur = cur.parent;
    }
    return obj;
  }

  function _setEmissive(obj, color, intensity) {
    if (obj.material) {
      obj.material.emissive = new THREE.Color(color);
      obj.material.emissiveIntensity = intensity;
    }
    if (obj.children) {
      obj.children.forEach(function(c) { _setEmissive(c, color, intensity); });
    }
  }

  function _selectValve(mesh) {
    _deselectAllValves();
    _selectedValve = mesh;
    _selectedValves = [mesh];
    _setEmissive(mesh, 0xFFFF00, 0.4);
    _createValveActionButtons();
    _renderValveInspector();
    if (typeof requestRender === 'function') requestRender();
  }

  function _deselectValve() {
    _deselectAllValves();
  }

  function _deselectAllValves() {
    _removeValveActionButtons();
    _stopValveMove();
    _selectedValves.forEach(function(v) { _setEmissive(v, 0x000000, 0); });
    _selectedValves = [];
    _selectedValve = null;
    _stopValveDrag();
    _clearValveInspector();
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Floating Action Buttons for Selected Valve
  // ─────────────────────────────────────────────────────────────────────────────

  var _valveActionContainer = null;
  var _valveActionAnimId = null;
  var _valveMoving = false;
  var _valveMoveData = null;

  function _createValveActionButtons() {
    _removeValveActionButtons();
    if (!_selectedValve) return;

    var container = document.createElement('div');
    container.id = 'valve-action-buttons';
    container.style.cssText = 'position:fixed;z-index:9999;pointer-events:none;display:flex;gap:8px;transform:translate(-50%,-100%);';

    function makeBtn(icon, title, borderColor, hoverColor, cursorStyle) {
      var btn = document.createElement('div');
      btn.style.cssText = 'pointer-events:auto;width:30px;height:30px;border-radius:50%;' +
        'background:rgba(24,24,38,0.88);border:1.5px solid ' + borderColor + ';' +
        'color:' + borderColor + ';display:flex;align-items:center;justify-content:center;' +
        'font-size:16px;line-height:1;cursor:' + cursorStyle + ';' +
        'backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);' +
        'box-shadow:0 2px 10px rgba(0,0,0,0.5),inset 0 1px 0 rgba(255,255,255,0.05);' +
        'transition:all 0.15s ease;user-select:none;touch-action:none;';
      btn.textContent = icon;
      btn.title = title;
      btn.addEventListener('mouseenter', function() {
        this.style.borderColor = hoverColor;
        this.style.color = hoverColor;
        this.style.boxShadow = '0 0 14px ' + hoverColor + '44,0 2px 10px rgba(0,0,0,0.5)';
        this.style.transform = 'scale(1.1)';
      });
      btn.addEventListener('mouseleave', function() {
        this.style.borderColor = borderColor;
        this.style.color = borderColor;
        this.style.boxShadow = '0 2px 10px rgba(0,0,0,0.5),inset 0 1px 0 rgba(255,255,255,0.05)';
        this.style.transform = 'scale(1)';
      });
      return btn;
    }

    var moveBtn = makeBtn('↔', t('liquid.btn_move_along_pipe'), 'rgba(0,180,220,0.7)', '#00e5ff', 'grab');
    var connectBtn = makeBtn('⊕', t('liquid.btn_connect_manifold'), 'rgba(255,152,0,0.7)', '#ffab40', 'crosshair');

    moveBtn.addEventListener('pointerdown', function(e) {
      e.preventDefault();
      e.stopPropagation();
      _startValveMoveAlongPipe(e);
    });
    connectBtn.addEventListener('pointerdown', function(e) {
      e.preventDefault();
      e.stopPropagation();
      _startValveConnectFromButton(e);
    });

    container.appendChild(moveBtn);
    container.appendChild(connectBtn);
    document.body.appendChild(container);
    _valveActionContainer = container;
    _startValveActionPositionLoop();
  }

  function _removeValveActionButtons() {
    if (_valveActionAnimId) { cancelAnimationFrame(_valveActionAnimId); _valveActionAnimId = null; }
    if (_valveActionContainer && _valveActionContainer.parentNode) {
      _valveActionContainer.parentNode.removeChild(_valveActionContainer);
    }
    _valveActionContainer = null;
  }

  function _startValveActionPositionLoop() {
    function update() {
      if (!_valveActionContainer || !_selectedValve) {
        if (_valveActionAnimId) { cancelAnimationFrame(_valveActionAnimId); _valveActionAnimId = null; }
        return;
      }
      if (typeof camera === 'undefined' || typeof renderer === 'undefined' || !renderer || !renderer.domElement) {
        _valveActionAnimId = requestAnimationFrame(update);
        return;
      }
      var pos = _selectedValve.position.clone();
      if (typeof globalGroup !== 'undefined') {
        globalGroup.updateMatrixWorld(true);
        pos.applyMatrix4(globalGroup.matrixWorld);
      }
      pos.project(camera);

      if (pos.z > 1) {
        _valveActionContainer.style.display = 'none';
      } else {
        _valveActionContainer.style.display = 'flex';
        var rect = renderer.domElement.getBoundingClientRect();
        var x = (pos.x * 0.5 + 0.5) * rect.width + rect.left;
        var y = (-pos.y * 0.5 + 0.5) * rect.height + rect.top;
        _valveActionContainer.style.left = x + 'px';
        _valveActionContainer.style.top = (y - 20) + 'px';
      }
      _valveActionAnimId = requestAnimationFrame(update);
    }
    _valveActionAnimId = requestAnimationFrame(update);
  }

  // ─── Move Valve Along Pipe ────────────────────────────────────────────────

  function _stopValveMove() {
    if (_valveMoving) {
      _valveMoving = false;
      _valveMoveData = null;
      document.body.style.cursor = '';
      if (typeof controls !== 'undefined') controls.enabled = true;
      document.removeEventListener('pointermove', _onValveMovePointerMove);
      document.removeEventListener('pointerup', _onValveMovePointerUp);
    }
  }

  function _startValveMoveAlongPipe(event) {
    if (!_selectedValve) return;
    var ud = _selectedValve.userData;
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
    if (!pipe || !pipe.geometry) return;

    _valveMoving = true;
    _valveMoveData = { pipeId: ud.pipeId, valveId: ud.valveId, checkpointPushed: false };
    if (typeof controls !== 'undefined') controls.enabled = false;
    document.body.style.cursor = 'grabbing';

    _valveConnectionLines.forEach(function(l) {
      if (l.userData && l.userData.valveId === ud.valveId) l.visible = false;
    });

    document.addEventListener('pointermove', _onValveMovePointerMove);
    document.addEventListener('pointerup', _onValveMovePointerUp);
  }

  function _onValveMovePointerMove(event) {
    if (!_valveMoving || !_selectedValve || !_valveMoveData) return;
    var ud = _selectedValve.userData;
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
    if (!pipe || !pipe.geometry) return;

    if (!_valveMoveData.checkpointPushed) {
      if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('moveValveAlongPipe');
      _valveMoveData.checkpointPushed = true;
    }

    if (typeof camera === 'undefined' || typeof renderer === 'undefined' || !renderer || !renderer.domElement || typeof THREE === 'undefined') return;
    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    var cameraDir = new THREE.Vector3();
    camera.getWorldDirection(cameraDir);
    var plane = new THREE.Plane().setFromNormalAndCoplanarPoint(cameraDir, _selectedValve.position);
    var worldPoint = new THREE.Vector3();
    raycaster.ray.intersectPlane(plane, worldPoint);
    if (!worldPoint) return;

    var projPoint = _projectPointOnPipeCenterline(pipe, worldPoint);

    var pipeValves = pipe.cooling && pipe.cooling.valves;
    var valve = pipeValves ? pipeValves.find(function(v) { return v.id === _valveMoveData.valveId; }) : null;
    if (valve) {
      valve.position.x = projPoint.x;
      valve.position.y = projPoint.y;
      valve.position.z = projPoint.z;
    }

    var diameter = (pipe.geometry && pipe.geometry.diameterM) || 0.025;
    var isTee = _selectedValve.userData && _selectedValve.userData.isTeeJunction;
    var _moveSideSign = (_selectedValve.userData && _selectedValve.userData.side === 'above') ? 1 : -1;
    if (isTee) {
      _selectedValve.position.set(projPoint.x, projPoint.y, projPoint.z);
    } else {
      var _bodyH = diameter * 0.8;
      _selectedValve.position.set(projPoint.x, projPoint.y + _moveSideSign * (_bodyH / 2 + diameter / 2), projPoint.z);
    }

    if (typeof requestRender === 'function') requestRender();
  }

  function _onValveMovePointerUp(event) {
    var valveId = _valveMoveData ? _valveMoveData.valveId : null;
    _stopValveMove();
    _removeValveActionButtons();
    _renderValves();

    if (valveId) {
      var newMesh = _valveMeshes.find(function(m) { return m.userData.valveId === valveId; });
      if (newMesh) _selectValve(newMesh);
    }
    if (typeof logOperation === 'function') logOperation('moveValveAlongPipe', 'Move valve along pipe', { valveId: valveId }, 'modify');
  }

  // ─── Connect Valve to Manifold (from button) ─────────────────────────────

  function _startValveConnectFromButton(event) {
    if (!_selectedValve) return;
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return;
    if (typeof controls !== 'undefined') controls.enabled = false;
    _valveDragging = true;

    if (!_valveDragLine) {
      var mat = new THREE.LineDashedMaterial({ color: 0xFFFFFF, dashSize: 0.02, gapSize: 0.01, linewidth: 1 });
      var geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
      _valveDragLine = new THREE.Line(geo, mat);
      _valveDragLine.computeLineDistances();
      globalGroup.add(_valveDragLine);
    }
    var positions = _valveDragLine.geometry.attributes.position.array;
    positions[0] = _selectedValve.position.x;
    positions[1] = _selectedValve.position.y;
    positions[2] = _selectedValve.position.z;
    positions[3] = _selectedValve.position.x;
    positions[4] = _selectedValve.position.y;
    positions[5] = _selectedValve.position.z;
    _valveDragLine.geometry.attributes.position.needsUpdate = true;

    document.body.style.cursor = 'crosshair';
    document.addEventListener('pointermove', _onValveConnectPointerMove);
    document.addEventListener('pointerup', _onValveConnectPointerUp);
  }

  function _onValveConnectPointerMove(event) {
    _updateValveDrag(event);
  }

  function _onValveConnectPointerUp(event) {
    document.removeEventListener('pointermove', _onValveConnectPointerMove);
    document.removeEventListener('pointerup', _onValveConnectPointerUp);
    document.body.style.cursor = '';

    var valveId = _selectedValve ? _selectedValve.userData.valveId : null;
    _endValveDrag(event);

    if (valveId) {
      setTimeout(function() {
        var mesh = _valveMeshes.find(function(m) { return m.userData.valveId === valveId; });
        if (mesh) _selectValve(mesh);
      }, 50);
    }
  }

  function _startValveDrag(event) {
    if (!_selectedValve) return false;
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return false;
    if (typeof renderer === 'undefined' || !renderer.domElement) return false;
    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    var hits = raycaster.intersectObjects([_selectedValve], true);
    if (hits.length === 0) return false;
    if (typeof controls !== 'undefined') controls.enabled = false;
    _valveDragging = true;
    if (!_valveDragLine) {
      var mat = new THREE.LineDashedMaterial({ color: 0xFFFFFF, dashSize: 0.02, gapSize: 0.01, linewidth: 1 });
      var geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
      _valveDragLine = new THREE.Line(geo, mat);
      _valveDragLine.computeLineDistances();
      globalGroup.add(_valveDragLine);
    }
    return true;
  }

  function _updateValveDrag(event) {
    if (!_valveDragging || !_valveDragLine || !_selectedValve) return;
    if (typeof camera === 'undefined' || typeof renderer === 'undefined') return;
    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    var cameraDir = new THREE.Vector3();
    camera.getWorldDirection(cameraDir);
    var plane = new THREE.Plane().setFromNormalAndCoplanarPoint(cameraDir, _selectedValve.position);
    var endPoint = new THREE.Vector3();
    raycaster.ray.intersectPlane(plane, endPoint);
    if (!endPoint) return;

    var positions = _valveDragLine.geometry.attributes.position.array;
    positions[0] = _selectedValve.position.x;
    positions[1] = _selectedValve.position.y;
    positions[2] = _selectedValve.position.z;
    positions[3] = endPoint.x;
    positions[4] = endPoint.y;
    positions[5] = endPoint.z;
    _valveDragLine.geometry.attributes.position.needsUpdate = true;
    _valveDragLine.computeLineDistances();

    var snapTargets = _collectSnapTargets();
    var closest = null;
    var closestDist = 0.3;
    snapTargets.forEach(function(t) {
      if (!t.portId || t.portId.indexOf('.manifold_') < 0) return;
      var dEnd = endPoint.distanceTo(t.position);
      var dRay = raycaster.ray.distanceToPoint(t.position);
      var d = Math.min(dEnd, dRay);
      if (d < closestDist) { closest = t; closestDist = d; }
    });
    if (closest) {
      _showValveSnapHighlight(closest);
    } else {
      _hideValveSnapHighlight();
    }

    if (typeof requestRender === 'function') requestRender();
  }

  var _valveSnapRing = null;
  var _valveSnapAnimId = null;

  function _showValveSnapHighlight(target) {
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return;
    if (!_valveSnapRing) {
      var geo = new THREE.RingGeometry(0.04, 0.06, 32);
      var mat = new THREE.MeshBasicMaterial({
        color: 0xFFD700, transparent: true, opacity: 0.8,
        side: THREE.DoubleSide, depthTest: false
      });
      _valveSnapRing = new THREE.Mesh(geo, mat);
      _valveSnapRing.renderOrder = 999;
      globalGroup.add(_valveSnapRing);
      if (!_valveSnapAnimId) {
        _valveSnapAnimId = setInterval(function() {
          if (!_valveSnapRing) return;
          _valveSnapRing.material.opacity = 0.4 + 0.5 * Math.abs(Math.sin(Date.now() * 0.006));
          if (typeof camera !== 'undefined') _valveSnapRing.lookAt(camera.position);
          if (typeof requestRender === 'function') requestRender();
        }, 50);
      }
    }
    _valveSnapRing.position.copy(target.position);
    if (typeof camera !== 'undefined') _valveSnapRing.lookAt(camera.position);
  }

  function _hideValveSnapHighlight() {
    if (_valveSnapRing) {
      if (_valveSnapRing.parent) _valveSnapRing.parent.remove(_valveSnapRing);
      if (_valveSnapRing.geometry) _valveSnapRing.geometry.dispose();
      if (_valveSnapRing.material) _valveSnapRing.material.dispose();
      _valveSnapRing = null;
    }
    if (_valveSnapAnimId) { clearInterval(_valveSnapAnimId); _valveSnapAnimId = null; }
  }

  function _endValveDrag(event) {
    if (!_valveDragging || !_selectedValve) return;
    _valveDragging = false;
    // Check snap to rack manifold
    var targets = _collectSnapTargets();
    var valvePos = _selectedValve.position;
    var best = null;
    var bestDist = 0.3; // snap distance for valve connection
    targets.forEach(function(t) {
      if (!t.portId || t.portId.indexOf('.manifold_') < 0) return;
      var d = valvePos.distanceTo(t.position);
      if (d < bestDist) { best = t; bestDist = d; }
    });

    // Also check endpoint of drag line
    if (!best && _valveDragLine) {
      var endPos = new THREE.Vector3(
        _valveDragLine.geometry.attributes.position.array[3],
        _valveDragLine.geometry.attributes.position.array[4],
        _valveDragLine.geometry.attributes.position.array[5]
      );
      targets.forEach(function(t) {
        if (!t.portId || t.portId.indexOf('.manifold_') < 0) return;
        var d = endPos.distanceTo(t.position);
        if (d < bestDist) { best = t; bestDist = d; }
      });
    }

    // Ray-based snap: cursor pointing at manifold regardless of depth
    if (!best && event && typeof renderer !== 'undefined' && renderer && renderer.domElement && typeof camera !== 'undefined') {
      var _rc = new THREE.Raycaster();
      var _rect = renderer.domElement.getBoundingClientRect();
      var _m = new THREE.Vector2(
        ((event.clientX - _rect.left) / _rect.width) * 2 - 1,
        -((event.clientY - _rect.top) / _rect.height) * 2 + 1
      );
      _rc.setFromCamera(_m, camera);
      var rayBestDist = 0.2;
      targets.forEach(function(t) {
        if (!t.portId || t.portId.indexOf('.manifold_') < 0) return;
        var d = _rc.ray.distanceToPoint(t.position);
        if (d < rayBestDist) { best = t; rayBestDist = d; }
      });
    }

    if (best) {
      var ud = _selectedValve.userData;
      var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
      var pipeValves = pipe && pipe.cooling && pipe.cooling.valves;
      if (pipeValves) {
        var targetColor = best.color || 'unconnected';
        var existingType = _getPipeCircuitType(pipe);
        if (existingType && targetColor !== 'unconnected' && existingType !== targetColor) {
          var typeLabel = existingType === 'supply' ? 'Supply / Cold' : 'Return / Hot';
          var targetLabel = targetColor === 'supply' ? 'Supply / Cold' : 'Return / Hot';
          if (typeof logOperation === 'function') logOperation('valveConnectionBlocked', 'Blocked: pipe is ' + typeLabel + ', cannot connect to ' + targetLabel, { valveId: ud.valveId, portId: best.portId }, 'modify');
          alert('This pipe is already connected as ' + typeLabel + '.\nCannot connect a valve to a ' + targetLabel + ' port.\n\nEach pipe must carry only one circuit type (supply OR return).');
          _stopValveDrag();
          if (typeof requestRender === 'function') requestRender();
          return;
        }
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('connectValveToManifold');
        var valve = pipeValves.find(function(v) { return v.id === ud.valveId; });
        if (valve) {
          valve.connectedManifoldPortId = best.portId;
          valve.connectedRackId = best.modelId;
          _updatePipeColor(pipe);
          _renderValves();
          if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) {
            window._liquidPipeRenderer.updatePipeGeometry();
          }
        }
      }
      if (typeof logOperation === 'function') logOperation('connectValveToManifold', 'Connect valve to manifold', { valveId: ud.valveId, portId: best.portId }, 'modify');
    }
    _stopValveDrag();
    if (typeof requestRender === 'function') requestRender();
  }

  function _stopValveDrag() {
    _valveDragging = false;
    _hideValveSnapHighlight();
    if (typeof controls !== 'undefined') controls.enabled = true;
    if (_valveDragLine && _valveDragLine.parent) {
      _valveDragLine.parent.remove(_valveDragLine);
      _valveDragLine.geometry.dispose();
      _valveDragLine.material.dispose();
      _valveDragLine = null;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Right-click context menu for pipe control points
  // ─────────────────────────────────────────────────────────────────────────────

  function handleControlPointContextMenu(event, maxDistance) {
    var _maxDist = (typeof maxDistance === 'number') ? maxDistance : Infinity;
    var target = event.target || (typeof renderer !== 'undefined' && renderer && renderer.domElement);
    if (typeof camera !== 'undefined' && typeof THREE !== 'undefined' && target) {
      var raycaster = new THREE.Raycaster();
      var rect = target.getBoundingClientRect();
      var mouse = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1
      );
      raycaster.setFromCamera(mouse, camera);

      var controlPoints = [];
      if (typeof globalGroup !== 'undefined') {
        globalGroup.traverse(function(obj) {
          if (obj.userData && obj.userData.isPipeControlPoint) {
            obj.updateMatrixWorld(true);
            controlPoints.push(obj);
          }
        });
      }

      if (controlPoints.length > 0) {
        var hits = raycaster.intersectObjects(controlPoints, false);
        if (hits.length > 0 && hits[0].distance < _maxDist) {
          var chosen = hits[0].object;
          for (var hi = 1; hi < hits.length; hi++) {
            if (hits[hi].object.userData.isEndpoint && !chosen.userData.isEndpoint &&
                hits[hi].distance - hits[0].distance < 0.3) {
              chosen = hits[hi].object;
              break;
            }
          }
          _attachPipeWaypointGizmo(chosen);
          event.preventDefault();
          event.stopImmediatePropagation();
          var ud = chosen.userData;
          _showControlPointMenu(event.clientX, event.clientY, ud.pipeId, ud.waypointIndex, ud.isEndpoint);
          return true;
        }
      }
    }

    if (_pipeWaypointTarget && _pipeWaypointTarget.userData && _pipeWaypointTarget.userData.isPipeControlPoint) {
      event.preventDefault();
      event.stopImmediatePropagation();
      var ud = _pipeWaypointTarget.userData;
      _showControlPointMenu(event.clientX, event.clientY, ud.pipeId, ud.waypointIndex, ud.isEndpoint);
      return true;
    }
    return false;
  }

  function _showControlPointMenu(cx, cy, pipeId, wpIdx, isEndpoint) {
    var existing = document.getElementById('pipe-cp-context-menu');
    if (existing) existing.remove();

    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    if (!pipe || !pipe.geometry) return;
    var pConn = ((pipe.cooling = pipe.cooling || {}).connection = (pipe.cooling.connection || {}));
    var wp = pipe.geometry.waypoints;

    var menu = document.createElement('div');
    menu.id = 'pipe-cp-context-menu';
    menu.style.cssText = 'position:fixed;left:' + cx + 'px;top:' + cy + 'px;z-index:10000;background:#1e1e2e;border:1px solid #444;border-radius:4px;padding:4px 0;min-width:180px;box-shadow:0 4px 12px rgba(0,0,0,0.5);font-size:12px;color:#ccc;';

    function makeItem(text, color, onClick) {
      var item = document.createElement('div');
      item.textContent = text;
      item.style.cssText = 'padding:6px 12px;cursor:pointer;' + (color ? 'color:' + color + ';' : '');
      item.onmouseenter = function() { this.style.background = '#333'; };
      item.onmouseleave = function() { this.style.background = 'none'; };
      item.onclick = function() { menu.remove(); onClick(); };
      return item;
    }

    if (wpIdx < wp.length - 1) {
      menu.appendChild(makeItem(t('liquid.menu_add_point'), null, function() {
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('addPipeControlPoint');
        var curr = wp[wpIdx];
        var next = wp[wpIdx + 1];
        var mid = { x: (curr.x + next.x) / 2, y: (curr.y + next.y) / 2, z: (curr.z + next.z) / 2 };
        wp.splice(wpIdx + 1, 0, mid);
        if (window._liquidPipeRenderer) {
          if (window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
          if (window._liquidPipeRenderer.buildPipeControlPoints) window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
        }
        if (typeof logOperation === 'function') logOperation('addPipeControlPoint', 'Add pipe control point', { pipeId: pipeId, index: wpIdx + 1 }, 'modify');
      }));
    }

    if (!isEndpoint && wp.length > 2) {
      menu.appendChild(makeItem(t('liquid.menu_delete_point'), '#F44336', function() {
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('deletePipeControlPoint');
        wp.splice(wpIdx, 1);
        _detachPipeWaypointGizmo();
        if (window._liquidPipeRenderer) {
          if (window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
          if (window._liquidPipeRenderer.buildPipeControlPoints) window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
        }
        if (typeof logOperation === 'function') logOperation('deletePipeControlPoint', 'Delete pipe control point', { pipeId: pipeId, index: wpIdx }, 'modify');
      }));
    }

    if (isEndpoint) {
      var isStart = (wpIdx === 0);
      var hasConn = isStart ? !!pConn.startPortId : !!pConn.endPortId;
      if (hasConn) {
        menu.appendChild(makeItem(t('liquid.menu_disconnect'), '#FF9800', function() {
          if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('disconnectPipeEndpoint');
          if (isStart) pConn.startPortId = '';
          else pConn.endPortId = '';
          _updatePipeColor(pipe);
          if (window._liquidPipeRenderer) {
            if (window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
            if (window._liquidPipeRenderer.buildPipeControlPoints) window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
          }
          if (typeof logOperation === 'function') logOperation('disconnectPipeEndpoint', 'Disconnect pipe endpoint', { pipeId: pipeId, end: isStart ? 'start' : 'end' }, 'modify');
        }));
      }
      // Blind end toggle
      if (!pConn.blindEnds) pConn.blindEnds = { start: false, end: false };
      var blindKey = isStart ? 'start' : 'end';
      var isBlind = !!pConn.blindEnds[blindKey];
      var blindLabel = isBlind ? t('liquid.menu_remove_blind') : t('liquid.menu_set_blind');
      menu.appendChild(makeItem(blindLabel, isBlind ? '#FF9800' : '#90A4AE', function() {
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('toggleBlindEnd');
        if (!pConn.blindEnds) pConn.blindEnds = { start: false, end: false };
        pConn.blindEnds[blindKey] = !pConn.blindEnds[blindKey];
        if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
        if (typeof updateUI === 'function') updateUI();
        if (typeof logOperation === 'function') logOperation('toggleBlindEnd', (pConn.blindEnds[blindKey] ? 'Set' : 'Remove') + ' blind end', { pipeId: pipeId, end: blindKey }, 'modify');
      }));
      if (wp.length > 2) {
        menu.appendChild(makeItem(t('liquid.menu_delete_endpoint'), '#F44336', function() {
          _deleteWaypoint(pipeId, wpIdx);
        }));
      }
    }

    document.body.appendChild(menu);
    setTimeout(function() {
      function removeMenu(e) {
        if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', removeMenu); }
      }
      document.addEventListener('click', removeMenu);
    }, 50);
  }

  // Install the context menu listener on the renderer DOM element after load
  if (typeof window !== 'undefined') {
    window.addEventListener('load', function() {
      const rendererEl = (typeof renderer !== 'undefined' && renderer && renderer.domElement)
        ? renderer.domElement
        : document.querySelector('canvas');
      if (rendererEl) {
        rendererEl.addEventListener('contextmenu', handleContextMenu);
        console.log('[liquid_mode.js] Context menu listener attached');
      }
    });
  }

  window._enterPipeCreationMode = enterPipeCreationMode;
  window._exitPipeCreationMode = exitPipeCreationMode;
  window._isPipeCreationMode = function() { return _pipeMode; };
  window._handlePipeClick = handlePipeClick;

  function onPipeSelectionChanged(newSelection) {
    if (!window._liquidPipeRenderer) return;
    if (!newSelection || newSelection.type !== 'model') {
      if (window._liquidPipeRenderer.clearPipeControlPoints) {
        window._liquidPipeRenderer.clearPipeControlPoints();
      }
      return;
    }
    var model = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(newSelection.modelId) : null;
    if (!model || model.kind !== 'pipe_segment') {
      if (window._liquidPipeRenderer.clearPipeControlPoints) {
        window._liquidPipeRenderer.clearPipeControlPoints();
      }
      return;
    }
    if (window._liquidPipeRenderer.buildPipeControlPoints) {
      window._liquidPipeRenderer.buildPipeControlPoints(model.uid);
    }
  }

  function _deleteWaypoint(pipeId, wpIdx) {
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    if (!pipe || !pipe.geometry || !pipe.geometry.waypoints) return;
    var wps = pipe.geometry.waypoints;
    if (wps.length <= 2) return;
    var pConn = ((pipe.cooling = pipe.cooling || {}).connection = (pipe.cooling.connection || {}));
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('deleteWaypoint');
    var isStart = (wpIdx === 0);
    var isEnd = (wpIdx === wps.length - 1);
    if (isStart && pConn.startPortId) { pConn.startPortId = ''; _updatePipeColor(pipe); }
    if (isEnd && pConn.endPortId) { pConn.endPortId = ''; _updatePipeColor(pipe); }
    wps.splice(wpIdx, 1);
    _detachPipeWaypointGizmo();
    if (window._liquidPipeRenderer) {
      if (window._liquidPipeRenderer.updatePipeGeometry) window._liquidPipeRenderer.updatePipeGeometry();
      if (window._liquidPipeRenderer.buildPipeControlPoints) window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
    }
    if (typeof updateUI === 'function') updateUI();
    if (typeof logOperation === 'function') logOperation('deleteWaypoint', 'Delete waypoint', { pipeId: pipeId, index: wpIdx }, 'modify');
  }

  function _deleteSelectedWaypoint() {
    if (!_pipeWaypointTarget || !_pipeWaypointTarget.userData) return false;
    var ud = _pipeWaypointTarget.userData;
    if (!ud.isPipeControlPoint || !ud.pipeId) return false;
    var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
    if (!pipe || !pipe.geometry || !pipe.geometry.waypoints || pipe.geometry.waypoints.length <= 2) return false;
    _deleteWaypoint(ud.pipeId, ud.waypointIndex);
    return true;
  }

  function isGizmoDraggable(event) {
    if (!_pipeWaypointTC || !_pipeWaypointTC.object) return false;
    if (typeof renderer === 'undefined' || !renderer.domElement) return false;
    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    var picker = _pipeWaypointTC._gizmo && _pipeWaypointTC._gizmo.picker
      ? _pipeWaypointTC._gizmo.picker[_pipeWaypointTC.mode] : null;
    if (!picker) return false;
    var hits = raycaster.intersectObject(picker, true);
    return hits.length > 0;
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Valve Inspector Panel (right sidebar) + Rotate 90°
  // ─────────────────────────────────────────────────────────────────────────────

  function _clearValveInspector() {
    var prop = document.getElementById('model-properties');
    if (!prop) return;
    if (prop.dataset.valveInspector === 'true') prop.innerHTML = '';
  }

  function _renderValveInspector() {
    var prop = document.getElementById('model-properties');
    if (!prop) return;
    if (_selectedValves.length === 0) { _clearValveInspector(); return; }

    var isMulti = _selectedValves.length > 1;
    var html = '';
    if (isMulti) {
      html += '<div class="prop-group" style="border-color:#FFD600;">';
      html += '<div class="prop-title" style="color:#FFD600;">' + t('liquid.fitting_multi_title') + '</div>';
      html += '<div class="prop-row"><label>' + t('liquid.fitting_count') + '</label><span class="badge">' + _selectedValves.length + '</span></div>';
      var valveCount = 0, teeCount = 0;
      _selectedValves.forEach(function(v) {
        if (v.userData.isTeeJunction) teeCount++; else valveCount++;
      });
      if (valveCount > 0) html += '<div class="prop-row"><label>' + t('liquid.fitting_valves') + '</label><span class="badge">' + valveCount + '</span></div>';
      if (teeCount > 0) html += '<div class="prop-row"><label>' + t('liquid.fitting_tees') + '</label><span class="badge">' + teeCount + '</span></div>';
      html += '<div style="padding:4px 8px;">';
      html += '<button class="btn" style="width:100%;margin:4px 0;background:rgba(255,214,0,0.1);border-color:rgba(255,214,0,0.4);color:#FFD600;" onclick="window._liquidMode.rotateSelectedValves90()">' + t('liquid.btn_batch_rotate_90') + '</button>';
      html += '</div></div>';
    } else {
      var mesh = _selectedValves[0];
      var ud = mesh.userData;
      var isTee = ud.isTeeJunction;
      var sideLabel = (ud.side === 'above') ? t('liquid.side_above') : t('liquid.side_below');
      var typeLabel = isTee ? t('liquid.fitting_type_tee') : t('liquid.fitting_type_valve');
      html += '<div class="prop-group" style="border-color:#FFD600;">';
      html += '<div class="prop-title" style="color:#FFD600;">' + (isTee ? '🔀 ' : '🔧 ') + typeLabel + '</div>';
      html += '<div class="prop-row"><label>' + t('liquid.fitting_side') + '</label><span>' + sideLabel + '</span></div>';
      html += '<div class="prop-row"><label>ID</label><span style="font-size:10px;color:#888;">' + ud.valveId + '</span></div>';
      html += '<div style="padding:4px 8px;">';
      html += '<button class="btn" style="width:100%;margin:4px 0;background:rgba(255,214,0,0.1);border-color:rgba(255,214,0,0.4);color:#FFD600;" onclick="window._liquidMode.rotateSelectedValves90()">' + t('liquid.btn_rotate_90') + '</button>';
      html += '</div></div>';
    }
    prop.innerHTML = html;
    prop.dataset.valveInspector = 'true';
  }

  function _rotateSelectedValves90() {
    if (_selectedValves.length === 0) return;
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('rotateValves90');
    _selectedValves.forEach(function(mesh) {
      var ud = mesh.userData;
      var pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(ud.pipeId) : null;
      var pipeValves = pipe && pipe.cooling && pipe.cooling.valves;
      if (!pipeValves) return;
      var valve = pipeValves.find(function(v) { return v.id === ud.valveId; });
      if (!valve) return;
      valve.side = (valve.side === 'above') ? 'below' : 'above';
    });
    var selectedIds = _selectedValves.map(function(v) { return v.userData.valveId; });
    _renderValves();
    _selectedValves = [];
    _selectedValve = null;
    selectedIds.forEach(function(vid) {
      var m = _valveMeshes.find(function(vm) { return vm.userData.valveId === vid; });
      if (m) {
        _selectedValves.push(m);
        _setEmissive(m, 0xFFFF00, 0.4);
      }
    });
    _selectedValve = _selectedValves.length > 0 ? _selectedValves[_selectedValves.length - 1] : null;
    if (_selectedValves.length === 1) _createValveActionButtons();
    _renderValveInspector();
    if (typeof requestRender === 'function') requestRender();
    if (typeof logOperation === 'function') logOperation('rotateValves90', 'Rotate fittings 90°', { count: selectedIds.length }, 'modify');
  }

  window._liquidMode = window._liquidMode || {};
  window._liquidMode.isGizmoDraggable = isGizmoDraggable;
  window._liquidMode.clearWaypointSpheres = _clearWaypointSpheres;
  window._liquidMode.buildWaypointSpheres = _buildWaypointSpheres;
  window._liquidMode.attachJunctionGizmo = attachJunctionGizmo;
  window._liquidMode.detachJunctionGizmo = detachJunctionGizmo;
  window._liquidMode.refreshJunctionGizmo = refreshJunctionGizmo;
  window._liquidMode.onPipeSelectionChanged = onPipeSelectionChanged;
  window._liquidMode.handleControlPointClick = handleControlPointClick;
  window._liquidMode.detachPipeWaypointGizmo = _detachPipeWaypointGizmo;
  window._liquidMode.handleControlPointContextMenu = handleControlPointContextMenu;
  // The right-click entry point, exported so a host that loads this module
  // AFTER window.load can attach it itself. The listener below is registered on
  // the `load` event, which has already fired by the time a bundler-driven
  // dynamic import resolves — so in that host the pipe context menu (insert
  // valve / pump / quick connect / heat exchanger) never reaches the user.
  window._liquidMode.handleContextMenu = handleContextMenu;
  window._liquidMode._deleteSelectedWaypoint = _deleteSelectedWaypoint;
  window._liquidMode.handleValveClick = handleValveClick;
  window._liquidMode.startValveDrag = _startValveDrag;
  window._liquidMode.updateValveDrag = _updateValveDrag;
  window._liquidMode.endValveDrag = _endValveDrag;
  window._liquidMode.renderValves = _renderValves;
  window._liquidMode._selectValveByMesh = function(mesh) { _selectValve(mesh); };
  window._liquidMode.rotateSelectedValves90 = _rotateSelectedValves90;
  window._liquidMode.getSelectedValves = function() { return _selectedValves; };
  window._liquidMode.deselectAllValves = _deselectAllValves;

  console.log('[liquid_mode.js] Loaded');
})();
