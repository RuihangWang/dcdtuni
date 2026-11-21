/**
 * liquid_graphgen.js
 * Compiles the 3D liquid cooling scene into a .liquidrun graph JSON.
 *
 * Public API:
 *   window.buildLiquidrunJson(models, liquidState) -> Object
 *
 * Units contract:
 *   - Input:  frontend scene positions in METERS (storage is always meters;
 *             meta.inputUnit is a display/input unit only, converted to meters
 *             at the UI boundary via fromDisplayLength), temperatures in °C
 *   - Output: all SI — meters, Kelvin, Pascals, W, kg, s
 */

(function () {
    'use strict';

    var U_HEIGHT_M = 0.04445; // 1U = 44.45 mm

    // Validation messages reach the readiness card as they are: through the
    // app's translator when it has the key, else the English text below.
    function msg(key, fallback, vars) {
        var text = typeof window !== 'undefined' && window.t ? window.t(key, vars) : null;
        return text && text !== key ? text : fallback;
    }

    // ------------------------------------------------------------------ //
    // Unit conversion helpers
    // ------------------------------------------------------------------ //

    function _unitScale() {
        // Scene coordinates and base dimensions are ALWAYS stored in meters.
        // meta.inputUnit is only a display/input unit (see formatLength /
        // fromDisplayLength in STL_generate.html); typed input is converted to
        // meters before storage. So the scene->meter scale is always 1.
        // Previously this returned an inputUnit-derived factor (0.001 for mm,
        // 0.01 for cm), which shrank every liquid-network position 1000x/100x
        // for non-meter projects (e.g. supply node Y = 0.0019 m for a 1.87 m
        // rack), while the render and air/OpenFOAM pipeline correctly used meters.
        return 1;
    }

    function _worldToMeters(vec3) {
        var s = _unitScale();
        return [vec3.x * s, vec3.y * s, vec3.z * s];
    }

    function _celsiusToKelvin(T_C) {
        return T_C + 273.15;
    }

    // ------------------------------------------------------------------ //
    // Port-position resolution
    // ------------------------------------------------------------------ //

    /**
     * Return the [x, y, z] position (metres) of a named port on a model.
     * portId format: "modelId.portType"
     *
     * For CDU models the supply/return ports are offset from the model
     * anchor using the same geometry assumed by _getCduPortPosition() in the
     * frontend's liquid_topology_editor.js.
     *
     * For all other model kinds we fall back to the model's anchor point.
     */
    function _getPortPosition(models, portId) {
        var parts = portId.split('.');
        var modelId = parts[0];
        var portType = parts[1];

        var model = null;
        for (var i = 0; i < models.length; i++) {
            if (models[i].uid === modelId) { model = models[i]; break; }
        }
        if (!model) return [0, 0, 0];

        var pure = window.geometryPure;
        if (pure && typeof pure.liquidEquipmentPortFrame === 'function') {
            var frame = pure.liquidEquipmentPortFrame(model, portType, /_bottom$/.test(portType) ? 'bottom' : 'top');
            if (frame && frame.position) return _worldToMeters(frame.position);
        }

        // Default: use model anchor
        var anchor = (model.geometry && model.geometry.location) || { x: 0, y: 0, z: 0 };
        return _worldToMeters(anchor);
    }

    function _modelLocalPosition(model, point) {
        var pure = window.geometryPure;
        if (pure && typeof pure.modelLocalPointToWorld === 'function') {
            var world = pure.modelLocalPointToWorld(model, point);
            if (world) return _worldToMeters(world);
        }
        var anchor = (model.geometry && model.geometry.location) || { x: 0, y: 0, z: 0 };
        return _worldToMeters({
            x: anchor.x + (point.x || 0),
            y: anchor.y + (point.y || 0),
            z: anchor.z + (point.z || 0)
        });
    }

    // ------------------------------------------------------------------ //
    // ID helpers
    // ------------------------------------------------------------------ //

    /**
     * Convert a portId ("modelId.portType") into a valid graph node ID by
     * replacing dots with underscores.
     */
    function _portToNodeId(portId) {
        return portId.replace(/\./g, '_');
    }

    // ------------------------------------------------------------------ //
    // Geometry helpers
    // ------------------------------------------------------------------ //

    function _polylineLength(wps) {
        var len = 0;
        for (var i = 1; i < wps.length; i++) {
            var dx = wps[i][0] - wps[i - 1][0];
            var dy = wps[i][1] - wps[i - 1][1];
            var dz = wps[i][2] - wps[i - 1][2];
            len += Math.sqrt(dx * dx + dy * dy + dz * dz);
        }
        return len;
    }

    function _dist3(a, b) {
        var dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
        return Math.sqrt(dx * dx + dy * dy + dz * dz);
    }

    function _projectOntoPolyline(polyline, pt) {
        var bestDist = Infinity, bestArc = 0, cumLen = 0;
        for (var i = 0; i < polyline.length - 1; i++) {
            var a = polyline[i], b = polyline[i + 1];
            var segLen = _dist3(a, b);
            var t = 0;
            if (segLen > 1e-12) {
                t = ((pt[0]-a[0])*(b[0]-a[0]) + (pt[1]-a[1])*(b[1]-a[1]) + (pt[2]-a[2])*(b[2]-a[2])) / (segLen * segLen);
                t = Math.max(0, Math.min(1, t));
            }
            var proj = [a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1]), a[2]+t*(b[2]-a[2])];
            var d = _dist3(pt, proj);
            if (d < bestDist) { bestDist = d; bestArc = cumLen + t * segLen; }
            cumLen += segLen;
        }
        return bestArc;
    }

    function _polylineArcLengths(polyline) {
        var arcs = [0];
        for (var i = 1; i < polyline.length; i++) {
            arcs.push(arcs[i - 1] + _dist3(polyline[i - 1], polyline[i]));
        }
        return arcs;
    }

    function _pointAtArc(polyline, arcs, arc) {
        for (var i = 1; i < polyline.length; i++) {
            if (arc <= arcs[i]) {
                var length = arcs[i] - arcs[i - 1];
                var t = length > 0 ? Math.max(0, Math.min(1, (arc - arcs[i - 1]) / length)) : 0;
                return polyline[i - 1].map(function (value, axis) {
                    return value + t * (polyline[i][axis] - value);
                });
            }
        }
        return polyline[polyline.length - 1].slice();
    }

    /** Keep every original elbow strictly inside the two fitting arcs. */
    function _slicePolylineAtArcs(polyline, arcs, startArc, endArc) {
        var segment = [_pointAtArc(polyline, arcs, startArc)];
        for (var i = 1; i < polyline.length - 1; i++) {
            if (arcs[i] > startArc && arcs[i] < endArc) segment.push(polyline[i]);
        }
        segment.push(_pointAtArc(polyline, arcs, endArc));
        return segment;
    }

    // ------------------------------------------------------------------ //
    // Topology validation
    // ------------------------------------------------------------------ //

    /**
     * BFS check: verify that supply can reach return via at least one cold_plate edge.
     */
    function _validateCduLoop(graph, supplyId, returnId, cduName) {
        var errors = [];

        // Build undirected adjacency: node → [{neighbor, edgeType}]
        var adj = {};
        var i, e;
        for (i = 0; i < graph.nodes.length; i++) {
            adj[graph.nodes[i].id] = [];
        }
        for (i = 0; i < graph.edges.length; i++) {
            e = graph.edges[i];
            if (adj[e.from]) adj[e.from].push({ node: e.to, type: e.type });
            if (adj[e.to]) adj[e.to].push({ node: e.from, type: e.type });
        }

        // BFS tracking both node and "passed cold_plate" state
        var visited = {};
        var queue = [{ node: supplyId, passedCP: false }];
        var reachedReturn = false;
        var reachedReturnViaCP = false;

        while (queue.length > 0) {
            var cur = queue.shift();
            var stateKey = cur.node + ':' + (cur.passedCP ? '1' : '0');
            if (visited[stateKey]) continue;
            visited[stateKey] = true;

            var neighbors = adj[cur.node] || [];
            for (var j = 0; j < neighbors.length; j++) {
                var nb = neighbors[j];
                var newPassedCP = cur.passedCP || (nb.type === 'cold_plate');

                if (nb.node === returnId) {
                    reachedReturn = true;
                    if (newPassedCP) reachedReturnViaCP = true;
                } else {
                    queue.push({ node: nb.node, passedCP: newPassedCP });
                }
            }
        }

        if (!reachedReturn) {
            errors.push(msg('lqgraph.cdu_loop_broken', cduName + ': CDU loop broken — cannot reach return from supply', { cdu: cduName }));
        } else if (!reachedReturnViaCP) {
            errors.push(msg('lqgraph.cdu_short_circuit', cduName + ': CDU short circuit — supply reaches return without passing through any cold plate', { cdu: cduName }));
        }

        return errors;
    }

    function _validateGraph(graph) {
        var errors = [];
        var nodeIds = {};

        // Build node-id lookup
        var i, n, e;
        for (i = 0; i < graph.nodes.length; i++) {
            n = graph.nodes[i];
            nodeIds[n.id] = true;
        }

        // 1. All edge from/to must reference existing nodes
        for (i = 0; i < graph.edges.length; i++) {
            e = graph.edges[i];
            if (!nodeIds[e.from]) {
                errors.push(msg('lqgraph.unknown_from_node', 'Edge ' + e.id + ': unknown from-node "' + e.from + '"', { edge: e.id, node: e.from }));
            }
            if (!nodeIds[e.to]) {
                errors.push(msg('lqgraph.unknown_to_node', 'Edge ' + e.id + ': unknown to-node "' + e.to + '"', { edge: e.id, node: e.to }));
            }
        }

        // 2. At least one pressure_bc node (required for solver BCs)
        var hasPBC = false;
        for (i = 0; i < graph.nodes.length; i++) {
            if (graph.nodes[i].type === 'pressure_bc') { hasPBC = true; break; }
        }
        if (!hasPBC) {
            errors.push(msg('lqgraph.no_pressure_bc', 'No pressure_bc node — solver will not converge'));
        }

        // 3. No duplicate node IDs
        var seenNodeIds = {};
        for (i = 0; i < graph.nodes.length; i++) {
            n = graph.nodes[i];
            if (seenNodeIds[n.id]) errors.push(msg('lqgraph.duplicate_node', 'Duplicate node ID: ' + n.id, { id: n.id }));
            seenNodeIds[n.id] = true;
        }

        // 4. No duplicate edge IDs
        var seenEdgeIds = {};
        for (i = 0; i < graph.edges.length; i++) {
            e = graph.edges[i];
            if (seenEdgeIds[e.id]) errors.push(msg('lqgraph.duplicate_edge', 'Duplicate edge ID: ' + e.id, { id: e.id }));
            seenEdgeIds[e.id] = true;
        }

        // 5. CDU loop validation — each CDU supply must reach its return via cold_plate
        var nodeList = graph.nodes;
        for (i = 0; i < nodeList.length; i++) {
            n = nodeList[i];
            if (n.type === 'pressure_bc' && n.id.indexOf('_supply') >= 0) {
                var baseName = n.id.replace('_supply', '');
                var returnId = baseName + '_return';
                var hasReturn = false;
                for (var k = 0; k < nodeList.length; k++) {
                    if (nodeList[k].id === returnId) { hasReturn = true; break; }
                }
                if (hasReturn) {
                    var loopErrors = _validateCduLoop(graph, n.id, returnId, baseName);
                    for (var le = 0; le < loopErrors.length; le++) {
                        errors.push(loopErrors[le]);
                    }
                }
            }
        }

        return errors;
    }

    // ------------------------------------------------------------------ //
    // Main entry point
    // ------------------------------------------------------------------ //

    /**
     * buildLiquidrunJson
     *
     * @param {Array}  models      - The full models[] array from global state.
     * @param {Object} liquidState - V4-native liquid view { fluid, solver, enabled }.
     * @returns {Object} A .liquidrun graph JSON object ready for JSON.stringify.
     */
    window.buildLiquidrunJson = function (models, liquidState) {
        var nodes    = [];
        var edges    = [];
        var racks    = [];
        var nodeIdSet = {};
        var fluid    = liquidState.fluid || {};

        var modelById = {};
        for (var mi = 0; mi < models.length; mi++) {
            modelById[models[mi].uid] = models[mi];
        }

        /** Guard-wrapped node push — silently ignores duplicate IDs. */
        function addNode(node) {
            if (!nodeIdSet[node.id]) {
                nodes.push(node);
                nodeIdSet[node.id] = true;
            }
        }

        // ---------------------------------------------------------------- //
        // 1. CDU nodes + pump edges
        //    Each CDU contributes:
        //      • A supply pressure_bc node (known inlet T & P)
        //      • A return pressure_bc node (known outlet P)
        //      • A pump edge from return → supply
        // ---------------------------------------------------------------- //

        var cdus = models.filter(function (m) { return m.kind === 'cdu' && m.meta.enabled; });

        cdus.forEach(function (cdu) {
            var cduCool    = cdu.cooling || {};
            var cduCons    = cdu.constructions || {};
            var supplyT_K  = _celsiusToKelvin(cduCool.supplyTemperatureC != null ? cduCool.supplyTemperatureC : 35);
            var supplyId   = cdu.meta.name + '_supply';
            var returnId   = cdu.meta.name + '_return';

            var supplyP_Pa = (cduCool.supplyPressureKpa != null ? cduCool.supplyPressureKpa : 250) * 1000;

            addNode({
                id:          supplyId,
                type:        'pressure_bc',
                position_m:  _getPortPosition(models, cdu.uid + '.supply'),
                bc: {
                    P_Pa: supplyP_Pa,
                    T_K:  supplyT_K
                }
            });

            addNode({
                id:          returnId,
                type:        'pressure_bc',
                position_m:  _getPortPosition(models, cdu.uid + '.return'),
                bc: {
                    P_Pa: 101325
                }
            });

            // Pump edge: drives flow from return header back to supply header.
            // H0_m / a_coef are placeholder pump-curve coefficients;
            // the backend solver will accept explicit Q-H tables in future versions.
            var pumpRpmRatio = 1.0;
            if (cduCool.advancedMode && cduCons.pump) {
                var rpmRef  = 3000; // nominal RPM assumed for ratio=1
                pumpRpmRatio = (cduCons.pump.rpm || rpmRef) / rpmRef;
            }

            edges.push({
                id:   cdu.meta.name + '_pump',
                type: 'pump',
                from: returnId,
                to:   supplyId,
                params: {
                    H0_m:      20.0,
                    a_coef:    5e5,
                    rpm_ratio: pumpRpmRatio
                }
            });
        });

        // ---------------------------------------------------------------- //
        // 2. Rack + manifold + server cold-plate expansion
        //    For each liquid-cooled rack:
        //      • Create supply/return manifold junction nodes
        //      • For each liquid-cooled equipment:
        //          – Create inlet/outlet junction nodes
        //          – Branch supply pipe (manifold → equipment inlet)
        //          – Branch return pipe (equipment outlet → manifold)
        //          – Cold-plate edges (series chain across CP-CU pairs)
        // ---------------------------------------------------------------- //

        var liquidRacks = models.filter(function (m) {
            return m.kind === 'rack' && m.cooling && m.cooling.type === 'liquid' && m.meta.enabled;
        });

        liquidRacks.forEach(function (rack) {
            var rackGeom   = rack.geometry || {};
            var rackSize   = rackGeom.size || {};
            var basePos    = _worldToMeters(rackGeom.location || { x: 0, y: 0, z: 0 });
            var mfCfg      = (rack.cooling && rack.cooling.liquidManifold) || {};
            var mfLayout   = mfCfg.layout || 'both-left';
            var mfDiam     = (mfCfg.supplyPipe || {}).diameter || 0.025;
            var rackHeight = ((rackSize.y != null ? rackSize.y : 1.8)) * _unitScale();
            var pure = window.geometryPure;
            var supplyFrame = pure && typeof pure.liquidEquipmentPortFrame === 'function'
                ? pure.liquidEquipmentPortFrame(rack, 'supply', 'top') : null;
            var returnFrame = pure && typeof pure.liquidEquipmentPortFrame === 'function'
                ? pure.liquidEquipmentPortFrame(rack, 'return', 'top') : null;
            var supplyHeaderPos = supplyFrame ? _worldToMeters(supplyFrame.position)
                : [basePos[0], basePos[1] + rackHeight, basePos[2]];
            var returnHeaderPos = returnFrame ? _worldToMeters(returnFrame.position)
                : [basePos[0] + 0.05, basePos[1] + rackHeight, basePos[2]];

            var supplyNodeId = rack.meta.name + '_mf_supply';
            var returnNodeId = rack.meta.name + '_mf_return';

            // Manifold header nodes sit at the top of the rack.
            // Supply header is at [baseX, top, baseZ];
            // Return header is offset 50 mm in X to keep IDs distinct.
            addNode({
                id:         supplyNodeId,
                type:       'junction',
                position_m: supplyHeaderPos
            });
            addNode({
                id:         returnNodeId,
                type:       'junction',
                position_m: returnHeaderPos
            });

            var rackEntry = {
                id:         rack.meta.name,
                position_m: basePos,
                n_slots:    rackGeom.slot || 42,
                manifold: {
                    supply_node: supplyNodeId,
                    return_node: returnNodeId,
                    layout:      mfLayout,
                    D_m:         mfDiam
                },
                servers: []
            };

            var equipments = Object.values((rack.constructions && rack.constructions.servers) || {}).filter(function (eq) {
                return eq.liquidCooling && eq.liquidCooling.cpCuPairs && eq.liquidCooling.cpCuPairs.length > 0;
            });

            equipments.forEach(function (eq) {
                var eqGeom   = eq.geometry || {};
                var eqName   = (eq.meta && eq.meta.name);
                var slotYLocal = (Number(rack.safeMarginBottom) || 0) +
                    (eqGeom.slotPosition || 0) * U_HEIGHT_M;
                var supplyLocal = supplyFrame && supplyFrame.localPosition
                    ? supplyFrame.localPosition : { x: 0, y: rackHeight, z: 0 };
                var returnLocal = returnFrame && returnFrame.localPosition
                    ? returnFrame.localPosition : { x: 0.05, y: rackHeight, z: 0 };
                var inletPos = _modelLocalPosition(rack, {
                    x: supplyLocal.x, y: slotYLocal, z: supplyLocal.z
                });
                var outletPos = _modelLocalPosition(rack, {
                    x: returnLocal.x, y: slotYLocal, z: returnLocal.z
                });
                var slotY = inletPos[1];
                var inletId  = rack.meta.name + '_' + eqName + '_in';
                var outletId = rack.meta.name + '_' + eqName + '_out';

                addNode({
                    id:         inletId,
                    type:       'junction',
                    position_m: inletPos
                });
                addNode({
                    id:         outletId,
                    type:       'junction',
                    position_m: outletPos
                });

                // Branch supply pipe: manifold header → equipment liquid inlet
                edges.push({
                    id:   rack.meta.name + '_' + eqName + '_branch_in',
                    type: 'pipe',
                    from: supplyNodeId,
                    to:   inletId,
                    params: {
                        D_m:             mfDiam,
                        roughness_m:     1.5e-6,
                        n_cells:         2,
                        waypoints_m: [
                            supplyHeaderPos,
                            inletPos
                        ],
                        K_minor_per_bend: 0.3
                    }
                });

                // Branch return pipe: equipment liquid outlet → manifold header
                edges.push({
                    id:   rack.meta.name + '_' + eqName + '_branch_out',
                    type: 'pipe',
                    from: outletId,
                    to:   returnNodeId,
                    params: {
                        D_m:             mfDiam,
                        roughness_m:     1.5e-6,
                        n_cells:         2,
                        waypoints_m: [
                            outletPos,
                            returnHeaderPos
                        ],
                        K_minor_per_bend: 0.3
                    }
                });

                // Cold-plate edges (series chain across CP-CU pairs)
                var pairs    = (eq.liquidCooling.cpCuPairs || []);
                var srvStart = eqGeom.slotPosition || 0;
                var srvEntry = {
                    id:          eqName,
                    slot_range:  [srvStart, srvStart + (eqGeom.slotOccupation || 1)],
                    z_m:         slotY,
                    cp_cu_pairs: [],
                    inlet_node:  inletId,
                    outlet_node: outletId
                };

                var prevNodeId = inletId;

                pairs.forEach(function (cp, i) {
                    var cpKey = cp._stable_uid != null ? '_' + cp._stable_uid : String(i);
                    var isLast     = (i === pairs.length - 1);
                    var nextNodeId = isLast
                        ? outletId
                        : rack.meta.name + '_' + eqName + '_cp' + cpKey + '_out';

                    if (!isLast) {
                        addNode({
                            id:         nextNodeId,
                            type:       'junction',
                            position_m: inletPos
                        });
                    }

                    var edgeId = rack.meta.name + '_' + eqName + '_cp' + cpKey;

                    if (!window.coldPlatePairParameters) {
                        throw new Error('cold_plate_pair_parameters.js is not loaded');
                    }
                    var cpParams = window.coldPlatePairParameters.parameters(cp, fluid);

                    edges.push({
                        id:   edgeId,
                        type: 'cold_plate',
                        from: prevNodeId,
                        to:   nextNodeId,
                        params: cpParams
                    });

                    srvEntry.cp_cu_pairs.push({
                        id:      cp.name  || ('cp' + i),
                        edge_id: edgeId,
                        Q_W:     cpParams.Q_W
                    });

                    prevNodeId = nextNodeId;
                });

                rackEntry.servers.push(srvEntry);
            });

            racks.push(rackEntry);
        });

        // ---------------------------------------------------------------- //
        // 3. User-drawn pipe segments
        //    Each pipe_segment becomes one or more pipe edges.  Valves with
        //    connectedRackId create T-junction nodes that split the main
        //    pipe and add spoke edges to rack manifold headers.
        // ---------------------------------------------------------------- //

        function _resolvePortNodeId(portId) {
            var parts = portId.split('.');
            var refModel = modelById[parts[0]];
            if (refModel && refModel.kind === 'cdu') {
                return refModel.meta.name + '_' + parts[1];
            }
            if (refModel && refModel.kind === 'pipe_junction') {
                if (!(refModel.cooling.ports || []).some(p => p.id === parts[1]))
                    throw new Error('Unknown junction port ' + portId);
                return refModel.meta.name + '_junction';
            }
            return _portToNodeId(portId);
        }

        var pipeModels = models.filter(function (m) {
            return m.kind === 'pipe_segment' && m.meta.enabled && m.geometry;
        });

        // A multi-port junction is one ideal mixing/pressure node. Port frames
        // still locate the physical pipe endpoints at the fitting's faces.
        models.filter(m => m.kind === 'pipe_junction' && m.meta.enabled).forEach(function (jm) {
            addNode({id:jm.meta.name + '_junction',type:'junction',
                position_m:_worldToMeters(jm.geometry.location)});
        });
        pipeModels.forEach(function (pm) {
            var pGeom     = pm.geometry || {};
            var pCool     = pm.cooling || {};
            var pConn     = pCool.connection || {};
            var blindEnds = pConn.blindEnds || { start: false, end: false };
            if (!pConn.startPortId && !blindEnds.start) return;
            if (!pConn.endPortId && !blindEnds.end) return;

            var fromNodeId, toNodeId;
            var fromPos, toPos;

            if (blindEnds.start) {
                fromNodeId = pm.meta.name + '_blind_start';
                var wpsS = pGeom.waypoints || [];
                fromPos = wpsS.length > 0
                    ? _worldToMeters(wpsS[0])
                    : [0, 0, 0];
                addNode({ id: fromNodeId, type: 'junction', position_m: fromPos });
            } else {
                fromNodeId = _resolvePortNodeId(pConn.startPortId);
                fromPos = _getPortPosition(models, pConn.startPortId);
                addNode({ id: fromNodeId, type: 'junction', position_m: fromPos });
            }

            if (blindEnds.end) {
                toNodeId = pm.meta.name + '_blind_end';
                var wpsE = pGeom.waypoints || [];
                toPos = wpsE.length > 0
                    ? _worldToMeters(wpsE[wpsE.length - 1])
                    : [0, 0, 0];
                addNode({ id: toNodeId, type: 'junction', position_m: toPos });
            } else {
                toNodeId = _resolvePortNodeId(pConn.endPortId);
                toPos = _getPortPosition(models, pConn.endPortId);
                addNode({ id: toNodeId, type: 'junction', position_m: toPos });
            }

            var wps = (pGeom.waypoints || []).map(function (wp) {
                return _worldToMeters(wp);
            });
            if (wps.length < 2) {
                wps = [fromPos, toPos];
            } else {
                // The displayed polyline terminates at the live equipment
                // frame. Keep its interior route, but replace stale endpoints
                // after an equipment move/rotation so render and physics agree.
                if (!blindEnds.start) wps[0] = fromPos;
                if (!blindEnds.end) wps[wps.length - 1] = toPos;
            }
            var routeArcs = _polylineArcLengths(wps);

            // Collect T-junction / flow-control valves that connect to racks
            var tees = (pCool.valves || []).filter(function (v) {
                return v.connectedRackId && v.connectedManifoldPortId;
            });

            tees.forEach(function (v) {
                var authoredPos = _worldToMeters(v.position);
                v._arc   = _projectOntoPolyline(wps, authoredPos);
                v._pos_m = _pointAtArc(wps, routeArcs, v._arc);
                if (_dist3(authoredPos, v._pos_m) > 1e-6) {
                    throw new Error('Fitting ' + v.id + ' is off the pipe centreline');
                }
            });
            tees.sort(function (a, b) { return a._arc - b._arc; });
            var chainArcs = [0];

            // Build chain: start → tee1 → tee2 → ... → end
            var chainNodeIds = [fromNodeId];

            tees.forEach(function (v) {
                var teeNodeId = pm.meta.name + '_tee_' + v.id;
                addNode({ id: teeNodeId, type: 'junction', position_m: v._pos_m });
                chainNodeIds.push(teeNodeId);
                chainArcs.push(v._arc);

                // Spoke edge to rack manifold
                var rackModel = modelById[v.connectedRackId];
                if (rackModel) {
                    var portType = v.connectedManifoldPortId.split('.')[1] || '';
                    var isSupply = portType.indexOf('supply') >= 0;
                    var mfNodeId = (rackModel.meta && rackModel.meta.name) + '_mf_' + (isSupply ? 'supply' : 'return');

                    if (nodeIdSet[mfNodeId]) {
                        var header = nodes.find(n => n.id === mfNodeId);
                        var end = /_bottom$/.test(portType) ? 'bottom' : 'top';
                        var frame = window.geometryPure.liquidEquipmentPortFrame(rackModel, portType, end);
                        if (!frame) throw new Error('Unknown rack manifold port ' + v.connectedManifoldPortId);
                        var mfPos = _worldToMeters(frame.position), spokeNode = mfNodeId;
                        if (end === 'bottom') {
                            spokeNode += '_bottom';
                            if (!nodeIdSet[spokeNode]) {
                                addNode({id:spokeNode,type:'junction',position_m:mfPos});
                                var diameter = ((rackModel.cooling.liquidManifold || {})[isSupply ? 'supplyPipe' : 'returnPipe'] || {}).diameter || 0.025;
                                edges.push({id:spokeNode + '_header',type:'pipe',from:spokeNode,to:mfNodeId,
                                    params:{D_m:diameter,roughness_m:1.5e-6,n_cells:Math.max(2,Math.round(_dist3(mfPos,header.position_m)/0.2)),
                                        waypoints_m:[mfPos,header.position_m],K_minor_per_bend:0.5}});
                            }
                        }
                        var branchWps = v.branchWaypoints ? v.branchWaypoints.map(_worldToMeters) : [v._pos_m,mfPos];
                        if (branchWps.length < 2) throw new Error('Branch route needs two points');
                        branchWps[0] = v._pos_m; branchWps[branchWps.length-1] = mfPos;
                        var branchD = v.branchDiameterM === undefined ? (pGeom.diameterM || 0.025) : v.branchDiameterM;
                        if (!Number.isFinite(branchD) || branchD <= 0) throw new Error('Invalid branch bore');
                        edges.push({id:pm.meta.name + '_spoke_' + v.id,type:'pipe',from:teeNodeId,to:spokeNode,
                            params:{D_m:branchD,roughness_m:1.5e-6,n_cells:Math.max(2,Math.round(_polylineLength(branchWps)/0.2)),
                                waypoints_m:branchWps,K_minor_per_bend:0.5}});
                    }
                }
            });

            chainNodeIds.push(toNodeId);
            chainArcs.push(routeArcs[routeArcs.length - 1]);

            // Create pipe segment edges for the chain
            for (var seg = 0; seg < chainNodeIds.length - 1; seg++) {
                var segmentWaypoints = tees.length === 0
                    ? wps
                    : _slicePolylineAtArcs(wps, routeArcs, chainArcs[seg], chainArcs[seg + 1]);
                var segLen = _polylineLength(segmentWaypoints);
                var nCells = Math.max(2, Math.round(segLen / 0.2));
                edges.push({
                    id:   pm.meta.name + (chainNodeIds.length > 2 ? '_seg' + seg : ''),
                    type: 'pipe',
                    from: chainNodeIds[seg],
                    to:   chainNodeIds[seg + 1],
                    params: {
                        D_m:              pGeom.diameterM || 0.025,
                        roughness_m:      1.5e-6,
                        n_cells:          nCells,
                        waypoints_m:      segmentWaypoints,
                        K_minor_per_bend: 0.5
                    }
                });
            }
        });

        // ---------------------------------------------------------------- //
        // 4. Events (time-varying parameters — reserved for future use)
        // ---------------------------------------------------------------- //

        var events = [];
        // Future: iterate liquid-config events when that field exists.

        // ---------------------------------------------------------------- //
        // 5. Assemble the .liquidrun JSON
        // ---------------------------------------------------------------- //

        var projName = (window.state && window.state.meta.projectName)
                       ? window.state.meta.projectName
                       : 'Untitled';

        var solverCfg = liquidState.solver || {};
        var wDef = { mass: 1.0, momentum: 1e-6, energy: 1.0, bc: 100.0, ic: 10.0 };
        var wCfg = solverCfg.weights ? Object.assign({}, wDef, solverCfg.weights) : wDef;

        var graph = {
            version:     '1.0',
            source:      'frontend',
            projectName: projName,
            gravity:     [0, -9.81, 0],

            fluid: {
                rho_kg_m3: fluid.rho  != null ? fluid.rho  : 997.0,
                cp_J_kgK:  fluid.cp   != null ? fluid.cp   : 4182.0,
                mu_Pa_s:   fluid.mu   != null ? fluid.mu   : 0.00089,
                k_W_mK:    fluid.k    != null ? fluid.k    : 0.606
            },

            time: {
                dt_s:      solverCfg.dt_s      != null ? solverCfg.dt_s      : 1.0,
                N_t:       solverCfg.N_t       != null ? solverCfg.N_t       : 10,
                bdf_order: solverCfg.bdf_order != null ? solverCfg.bdf_order : 1
            },

            racks:  racks,
            nodes:  nodes,
            edges:  edges,
            events: events,

            initial_conditions: {
                T_fluid_K:    _celsiusToKelvin(fluid.supplyTemp != null ? fluid.supplyTemp : 35),
                T_solid_K:    340.0,   // ~67 °C initial solid temperature
                P_uniform_Pa: 175000   // mid-range initial pressure
            },

            solver: {
                optimizer:       'adam',
                lr:              solverCfg.lr              != null ? solverCfg.lr              : 0.1,
                max_epochs:      solverCfg.max_epochs      != null ? solverCfg.max_epochs      : 10000,
                convergence_tol: solverCfg.convergence_tol != null ? solverCfg.convergence_tol : 1e-6,
                weights: wCfg
            }
        };

        // ---------------------------------------------------------------- //
        // 6. Validate topology and emit any warnings to the console
        // ---------------------------------------------------------------- //

        var errors = _validateGraph(graph);
        if (errors.length > 0) {
            var msg = 'Liquid graph validation failed:\n\n' + errors.join('\n');
            console.error('[liquid_graphgen] ' + msg);
            alert(msg);
            return null;
        }

        return graph;
    };

})();
