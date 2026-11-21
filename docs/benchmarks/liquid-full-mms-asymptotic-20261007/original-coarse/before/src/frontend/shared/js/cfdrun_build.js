/**
 * cfdrun_build.js — turning a project into the .cfdrun manifest's inputs.
 *
 * Lifted out of STL_generate.html: `buildCfdrunJson` (the transitional v1
 * meshGeometry blob the runner still reads) and `buildComprehensiveStlMap`
 * (the solid-name map the V2 boundaryBindings are built from), plus the
 * geometry and physics helpers they need.
 *
 * The manifest BUILDER was already a shared module — js/cfdrun_export.js — so
 * this is the last page-bound piece between a project and a .cfdrun, and with
 * it the React editor can export one.
 *
 * Four pieces of page state are injected rather than reached for: `state`,
 * `globalGroup` (for world matrices), and the `LENGTH_UNITS` / `_MESH_MODES`
 * tables. Everything else is arguments.
 *
 * A wrong value here is worse than a crash. The manifest is what the runner
 * validates and meshes from, so a mis-derived blockMesh bound or a solid name
 * that does not match the exported STL yields a case that meshes cleanly
 * around the wrong geometry.
 *
 * `cellPad` and `patchSolidName` are NESTED inside their callers and travel
 * with them; they are not separate exports. Replacing a nested function
 * independently is what corrupted the first attempt at this extraction — the
 * inner edit invalidated the outer function's end offset and spliced the two
 * together mid-token.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.cfdrunBuild = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';


    // ── sibling modules, bound explicitly ────────────────────────────────
    //
    // These were bare calls. On the legacy page they resolved only because a
    // top-level `function` there becomes a window property — so the module
    // worked in one host and threw in any other. The React editor is that other
    // host: `sanitizeName is not defined` was the first thing its CFD-package
    // export said.
    function _naming() {
        var m = (typeof window !== 'undefined') ? window.stlNaming : null;
        if (!m) throw new Error('cfdrun_build.js needs js/stl_naming.js loaded first');
        return m;
    }
    function _pure() {
        var m = (typeof window !== 'undefined') ? window.geometryPure : null;
        if (!m) throw new Error('cfdrun_build.js needs js/geometry_pure.js loaded first');
        return m;
    }
    function _rack() {
        var m = (typeof window !== 'undefined') ? window.rackState : null;
        if (!m) throw new Error('cfdrun_build.js needs js/rack_state.js loaded first');
        return m;
    }
    function sanitizeName() { return _naming().sanitizeName.apply(null, arguments); }
    function composeModelScopedExportName() { return _naming().composeModelScopedExportName.apply(null, arguments); }
    function getRackEquipmentExportStem() { return _naming().getRackEquipmentExportStem.apply(null, arguments); }
    function getRackServerViews() { return _naming().getRackServerViews.apply(null, arguments); }
    function getRackThroughZoneGroups() { return _naming().getRackThroughZoneGroups.apply(null, arguments); }
    function getRackPartitionExportName() { return _pure().getRackPartitionExportName.apply(null, arguments); }
    function getRackMergedEmptySlotRanges() { return _pure().getRackMergedEmptySlotRanges.apply(null, arguments); }
    function getServerFaceExportName() { return _pure().getServerFaceExportName.apply(null, arguments); }
    function getPartitionWallPatchStore() { return _pure().getPartitionWallPatchStore.apply(null, arguments); }
    function getDefaultCfd() { return _rack().getDefaultCfd.apply(null, arguments); }
    function modelWorldTransform() { return _pure().modelWorldTransform.apply(null, arguments); }
    function _rackServersDict() { return _naming().rackServersDict.apply(null, arguments); }

    function createCfdrunBuild(ctx) {
        var required = ['state', 'globalGroup', 'LENGTH_UNITS', '_MESH_MODES'];
        for (var i = 0; i < required.length; i++) {
            if (!(required[i] in ctx)) {
                throw new Error('cfdrun_build: context is missing ' + required[i]);
            }
        }

        
        
        // Same remaining body polygons and names as geometry_scene's box faces.
        // A patch can split a face into several solids or cover it completely.
        function heatSourceBodyParts(model) {
            const layouts = _pure().getFaceLayouts(model);
            const islands = _pure().buildFaceIslands(model);
            const parts = [];
            Object.entries(model.geometry.faces).forEach(([fid, face]) => {
                const layout = layouts[fid];
                if (!face || !layout) return;
                let wallPoly = _pure().rectToPoly(0, 0, layout.w, layout.h);
                (islands[fid] || []).forEach(island => {
                    wallPoly = window.polygonClipping.difference(wallPoly, island.poly);
                });
                wallPoly.forEach((polygon, index) => {
                    parts.push({ name: composeModelScopedExportName(model, fid, face,
                        { isWallPart: true, partIndex: index, partCount: wallPoly.length }), faceId: fid });
                });
            });
            return parts;
        }

        function _autoComputeBlockMeshBounds() {
            // Use room polygon bounds + 1-cell padding (matches backend compute_multiblock_grid).
            const baseSize = Math.max(Number(ctx.state.runDefaults.meshConfig.meshBaseSize), 0.01);
            var xMin, xMax, yMin, yMax, zMin, zMax;
            var rp = ctx.state.geometry.plane ? { vertices: ctx.state.geometry.plane, height: (ctx.state.geometry.planeHeight != null ? ctx.state.geometry.planeHeight : ctx.state.geometry.height) } : null;
            var rm = ctx.state.room;
            if (rp && rp.vertices && rp.vertices.length >= 3) {
                var xs = rp.vertices.map(function(v) { return v[0]; });
                var zs = rp.vertices.map(function(v) { return v[1]; });
                xMin = Math.min.apply(null, xs);
                xMax = Math.max.apply(null, xs);
                zMin = Math.min.apply(null, zs);
                zMax = Math.max.apply(null, zs);
                yMin = 0;
                yMax = rp.height || Number(ctx.state.geometry.height) || 3.0;
            } else if (rm && rm.plane && rm.plane.length >= 3) {
                var xs2 = rm.plane.map(function(p) { return p.x; });
                var zs2 = rm.plane.map(function(p) { return p.y; });
                xMin = Math.min.apply(null, xs2);
                xMax = Math.max.apply(null, xs2);
                zMin = Math.min.apply(null, zs2);
                zMax = Math.max.apply(null, zs2);
                yMin = 0;
                yMax = rm.height || Number(ctx.state.geometry.height) || 3.0;
            } else {
                var bbox = new THREE.Box3();
                ctx.globalGroup.children.forEach(function(child) {
                    if (child.userData && (child.userData.isBMFace || child.userData.isBMPlane || child.userData.isBMGrid)) return;
                    if (child.userData && (child.userData.isLocInMesh || child.userData.isExtraLocInMesh)) return;
                    if (child.userData && (child.userData.isRZone || child.userData.isRZPlane || child.userData.isRZCenter)) return;
                    bbox.expandByObject(child);
                });
                if (bbox.isEmpty()) return { min:{x:-1,y:-1,z:-0.1}, max:{x:12,y:10,z:4} };
                xMin = bbox.min.x; xMax = bbox.max.x;
                yMin = bbox.min.y; yMax = bbox.max.y;
                zMin = bbox.min.z; zMax = bbox.max.z;
            }
            function cellPad(extent) {
                var n = Math.max(1, Math.round(extent / baseSize));
                return extent / n;
            }
            var mx = cellPad(xMax - xMin);
            var my = cellPad(yMax - yMin);
            var mz = cellPad(zMax - zMin);
            return {
                min: { x: Math.round((xMin - mx)*1000)/1000,
                       y: Math.round((yMin - my)*1000)/1000,
                       z: Math.round((zMin - mz)*1000)/1000 },
                max: { x: Math.round((xMax + mx)*1000)/1000,
                       y: Math.round((yMax + my)*1000)/1000,
                       z: Math.round((zMax + mz)*1000)/1000 },
            };
        }

        
        function _buildServerFacesExport(model, equipment) {
            var result = {};
            // FOUR faces on purpose — pinned convention (WP-W6, 2026-09-18).
            // The scene ALSO builds the server's left/right faces, but those
            // are INTERFACE WALLS coincident with the rack frame's inner cavity
            // walls; the STL export routes them to their own bucket
            // (merged_server_interface_walls.stl). This dict is the single
            // enumeration behind BOTH the v1 server.faces blob AND the
            // comprehensive stlMap's source triples, and the stlMap is what the
            // V2 boundaryBindings join against (cfdrun_export.js bySource) —
            // so adding left/right here would hand the mesh pipeline surfaces
            // it cannot tell apart from the cavity walls they duplicate, and
            // silently create wall bindings for them. The informational
            // interfaceSolidNames field (below) is where the interface-wall
            // names live instead.
            var faceIds = ['inlet', 'outlet', 'top', 'bottom'];

            faceIds.forEach(function(faceId) {
                var faceDef = (equipment.faces && equipment.faces[faceId]) || { patches: [] };
                var entry = { solid: null, patches: [], wallSolid: null };
        
                if (faceDef.patches && faceDef.patches.length > 0) {
                    // Face is subdivided into patches
                    entry.solid = null;
                    faceDef.patches.forEach(function(patch, pIdx) {
                        entry.patches.push({
                            solid: getServerFaceExportName(model, equipment, faceId,
                                { isPatch: true, patchIndex: pIdx }),
                            patchId: patch.id
                        });
                    });
                    entry.wallSolid = getServerFaceExportName(model, equipment, faceId, { isWall: true });
                } else {
                    // Whole face — single solid
                    entry.solid = getServerFaceExportName(model, equipment, faceId);
                }
        
                result[faceId] = entry;
            });

            return result;
        }

        /**
         * INFORMATIONAL ONLY (WP-W6, 2026-09-18): the solid names of a server's
         * left/right INTERFACE WALLS — the faces the scene builds and the
         * manifold-patch STL export routes to merged_server_interface_walls.stl,
         * but that NO manifest face/binding enumerates (see
         * _buildServerFacesExport above). Mirrors its patch/wall subdivision so
         * the list equals the solids the bucket actually carries. Present so a
         * future mesh-fusion pass can find the interface walls without
         * re-deriving the naming; the runner must NOT depend on it (the v1 blob
         * is extra="allow", so it round-trips and is ignored today).
         */
        function _buildServerInterfaceSolidNames(model, equipment) {
            var names = [];
            ['left', 'right'].forEach(function(faceId) {
                var faceDef = (equipment.faces && equipment.faces[faceId]) || { patches: [] };
                if (faceDef.patches && faceDef.patches.length > 0) {
                    faceDef.patches.forEach(function(patch, pIdx) {
                        names.push(getServerFaceExportName(model, equipment, faceId,
                            { isPatch: true, patchIndex: pIdx }));
                    });
                    names.push(getServerFaceExportName(model, equipment, faceId, { isWall: true }));
                } else {
                    names.push(getServerFaceExportName(model, equipment, faceId));
                }
            });
            return names;
        }

            function _computeAllModelsBBox() {
            if (!window.allModels().length) return null;
            let xMin = Infinity, xMax = -Infinity;
            let yMin = Infinity, yMax = -Infinity;
            let zMin = Infinity, zMax = -Infinity;
            window.allModels().forEach(m => {
                const W = m.geometry.size.x || 0, D = m.geometry.size.z || 0, H = m.geometry.size.y || 0;
                const localCorners = [
                    new THREE.Vector3(0, 0, 0), new THREE.Vector3(W, 0, 0),
                    new THREE.Vector3(W, D, 0), new THREE.Vector3(0, D, 0),
                    new THREE.Vector3(0, 0, H), new THREE.Vector3(W, 0, H),
                    new THREE.Vector3(W, D, H), new THREE.Vector3(0, D, H),
                ];
                const q = m.geometry && m.geometry.quaternion;
                const hasRotation = q && (Math.abs(q.x) > 1e-6 || Math.abs(q.y) > 1e-6 || Math.abs(q.z) > 1e-6);
                if (hasRotation) {
                    const quat = new THREE.Quaternion(q.x, q.y, q.z, q.w);
                    localCorners.forEach(c => c.applyQuaternion(quat));
                }
                localCorners.forEach(c => {
                    const wx = m.geometry.location.x + c.x, wy = m.geometry.location.y + c.y, wz = m.geometry.location.z + c.z;
                    if (wx < xMin) xMin = wx; if (wx > xMax) xMax = wx;
                    if (wy < yMin) yMin = wy; if (wy > yMax) yMax = wy;
                    if (wz < zMin) zMin = wz; if (wz > zMax) zMax = wz;
                });
            });
            if (!Number.isFinite(xMin)) return null;
            return { xMin, xMax, yMin, yMax, zMin, zMax };
        }

        
        function _getBMBounds() {
            return ctx.state.runDefaults.meshConfig.blockMeshBounds || _autoComputeBlockMeshBounds();
        }

            function _resolveMeshMode(cr) {
            // The semantics follow the ENGINE (2026-09-09): snappy is body-fitted
            // (snap on), dcCart is pure cut-cell on a uniform background with
            // per-object refinement (snap off). The old 3-way meshMode and the
            // legacy cartesianMode/structuredGrid booleans are ignored.
            return (cr && cr.meshEngine === 'dcCart') ? 'cartesianLocalized' : 'bodyFitted';
        }

            function _t3bQuatRotate(v, q) {
            const x = v[0], y = v[1], z = v[2];
            const qx = q.x || 0, qy = q.y || 0, qz = q.z || 0, qw = (q.w != null ? q.w : 1);
            // t = 2 * cross(q.xyz, v)
            const tx = 2 * (qy * z - qz * y);
            const ty = 2 * (qz * x - qx * z);
            const tz = 2 * (qx * y - qy * x);
            // v' = v + qw*t + cross(q.xyz, t)
            return [
                x + qw * tx + (qy * tz - qz * ty),
                y + qw * ty + (qz * tx - qx * tz),
                z + qw * tz + (qx * ty - qy * tx),
            ];
        }

            function _t3bVecCross(a, b) {
            return [
                a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0],
            ];
        }

            function _t3bVecNormalize(v) {
            const n = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
            if (!(n > 1e-12)) return null;
            return [v[0] / n, v[1] / n, v[2] / n];
        }

        
        function buildCfdrunJson() {
            const unitScale = ctx.LENGTH_UNITS[ctx.state.meta.inputUnit] || 1;
            const plane = inferRoomPolygonFromWalls();
            const roomHeight = inferRoomHeightFromWalls();
        
            const DEFAULT_PERF = { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 };
            const toPerf = (src) => ({
                shape: (src && src.shape) || DEFAULT_PERF.shape,
                holeDiameterMm: Number.isFinite(src && src.holeDiameterMm) ? src.holeDiameterMm : DEFAULT_PERF.holeDiameterMm,
                openAreaPct: Number.isFinite(src && src.openAreaPct) ? src.openAreaPct : DEFAULT_PERF.openAreaPct,
            });
        
            const cfdData = {
                version: '1.0',
                source: 'frontend',
                projectName: sanitizeName(ctx.state.meta.projectName || ctx.state.meta.exportFileName || 'CFD_Project'),
                room: {
                    height: roomHeight,
                    plane: plane || [{ x: 0, y: 0, z: 0 }, { x: 10, y: 0, z: 0 }, { x: 10, y: 10, z: 0 }, { x: 0, y: 10, z: 0 }],
                    locationInMesh: { ...ctx.state.runDefaults.meshConfig.locationInMesh },
                    extraLocationsInMesh: (ctx.state.runDefaults.simulationConfig.extraLocations || []).map(function(e) { return { x: e.x, y: e.y, z: e.z }; })
                },
                racks: {},
                acus: {},
                wallObjects: {},
                heatSources: {},
                baffles: {},
                floors: {},
                walls: {},
                partitionWalls: {},
                meshConfig: {
                    baseSize: ctx.state.runDefaults.meshConfig.meshBaseSize,
                    // 2026-04-19: detectBaseSize is omitted when null/0 (matches
                    // pydantic exclude_none on runner side; keeps existing files
                    // diff-clean on re-save).
                    ...(ctx.state.runDefaults.meshConfig.meshDetectBaseSize && ctx.state.runDefaults.meshConfig.meshDetectBaseSize > 0
                        ? { detectBaseSize: ctx.state.runDefaults.meshConfig.meshDetectBaseSize }
                        : {}),
                    maxGlobalCells: parseInt(ctx.state.runDefaults.meshConfig.maxGlobalCells) || 5000000,
                    refinementLevel: ctx.state.runDefaults.meshConfig.refinementLevel || [1, 1],
                    rackRegionLevel: ctx.state.runDefaults.meshConfig.rackRegionLevel || 3,
                    // 3-way mesh generation mode: bodyFitted | cartesianLocalized | structured.
                    meshMode: _resolveMeshMode(ctx.state.runDefaults.meshConfig),
                    // Orthogonal axis: which executable builds the mesh. Emitted only
                    // when set (absent resolves to snappy in the runner), so blobs of
                    // projects that never chose one stay as they were.
                    ...(['snappy', 'dcCart', 'meshgen'].indexOf(ctx.state.runDefaults.meshConfig.meshEngine) >= 0
                        ? { meshEngine: ctx.state.runDefaults.meshConfig.meshEngine }
                        : {}),
                    // dcCart near-coincidence weld tolerance (m). Emitted only when the
                    // project sets one; absent, the runner writes its own default
                    // (1.2e-3 m) into dcCartMeshDict, and 0 turns the weld off.
                    ...(ctx.state.runDefaults.meshConfig.weldTolerance !== undefined
                        && ctx.state.runDefaults.meshConfig.weldTolerance !== null
                        && ctx.state.runDefaults.meshConfig.weldTolerance !== ''
                        && Number.isFinite(Number(ctx.state.runDefaults.meshConfig.weldTolerance))
                        ? { weldTolerance: Number(ctx.state.runDefaults.meshConfig.weldTolerance) }
                        : {}),
                    // dcCart first-layer floor on top of the weld (fraction of the
                    // surface's level spacing / cap in m / widest gap it may seal in
                    // m). Each emitted only when the project sets it; absent, the
                    // runner writes its own defaults (off / 0.005 / weldTolerance).
                    ...(['firstLayerMinFraction', 'firstLayerMax', 'firstLayerSealGap', 'rackCellMinSize', 'meshTimeLimitSeconds'].reduce(function(acc, key) {
                        var v = ctx.state.runDefaults.meshConfig[key];
                        if (v !== undefined && v !== null && v !== '' && Number.isFinite(Number(v))) {
                            acc[key] = Number(v);
                        }
                        return acc;
                    }, {})),
                    ...(ctx.state.runDefaults.meshConfig.firstCellSize && ctx.state.runDefaults.meshConfig.firstCellSize > 0
                        ? { firstCellSize: ctx.state.runDefaults.meshConfig.firstCellSize }
                        : {}),
                    floorRefinementLevel: ctx.state.runDefaults.meshConfig.floorRefinementLevel || [1, 1],
                    acuRefinementLevel: ctx.state.runDefaults.meshConfig.acuRefinementLevel || [1, 1],
                    boxRefinementLevel: ctx.state.runDefaults.meshConfig.boxRefinementLevel || [1, 1],
                    partitionWallRefinementLevel: ctx.state.runDefaults.simulationConfig.partitionWallRefinementLevel || [1, 1],
                    baffleRefinementLevel: ctx.state.runDefaults.meshConfig.wallBafflesRefinementLevel || [1, 1],
                    defaultFeatureLevel: ctx.state.runDefaults.meshConfig.defaultFeatureLevel || 2,
                    floorFeatureLevel: ctx.state.runDefaults.meshConfig.floorFeatureLevel || 2,
                    acuFeatureLevel: ctx.state.runDefaults.meshConfig.acuFeatureLevel || 2,
                    boxFeatureLevel: ctx.state.runDefaults.meshConfig.boxFeatureLevel || 2,
                    partitionWallFeatureLevel: ctx.state.runDefaults.simulationConfig.partitionWallFeatureLevel || 2,
                    baffleFeatureLevel: ctx.state.runDefaults.meshConfig.baffleFeatureLevel || 2,
                    parallelMesh: ctx.state.runDefaults.meshConfig.parallelMesh,
                    meshProcess: ctx.state.runDefaults.meshConfig.meshProcess,
                    blockMeshBounds: (() => {
                        const b = _getBMBounds();
                        return { min: { x: b.min.x, y: b.min.y, z: b.min.z }, max: { x: b.max.x, y: b.max.y, z: b.max.z } };
                    })(),
                    refinementZones: (ctx.state.runDefaults.meshConfig.refinementZones || []).map(z => ({
                        name: sanitizeName(z.name),
                        level: z.level,
                        min: { x: z.min.x, y: z.min.y, z: z.min.z },
                        max: { x: z.max.x, y: z.max.y, z: z.max.z }
                    }))
                },
                simulationConfig: {
                    endTime: parseInt(ctx.state.runDefaults.simulationConfig.endTime) || 500,
                    writeInterval: parseInt(ctx.state.runDefaults.simulationConfig.writeInterval) || 100,
                    doublePrecisionWrite: ctx.state.runDefaults.simulationConfig.doublePrecisionWrite || false,
                    writePrecision: ctx.state.runDefaults.simulationConfig.writePrecision || 'single',
                    endTimeSeconds: ctx.state.runDefaults.simulationConfig.endTimeSeconds || 300,
                    deltaT: ctx.state.runDefaults.simulationConfig.deltaT || 0.1,
                    maxDeltaT: ctx.state.runDefaults.simulationConfig.maxDeltaT || 1.0,
                    maxCo: ctx.state.runDefaults.simulationConfig.maxCo || 1.0,
                    writeIntervalSeconds: ctx.state.runDefaults.simulationConfig.writeIntervalSeconds || 10,
                    adjustTimeStep: ctx.state.runDefaults.simulationConfig.adjustTimeStep !== false,
                    startFrom: ctx.state.runDefaults.simulationConfig.startFrom || 'latestTime',
                    nOuterCorrectors: ctx.state.runDefaults.simulationConfig.nOuterCorrectors || 1,
                    nCorrectors: ctx.state.runDefaults.simulationConfig.nCorrectors || 2,
                    ddtScheme: ctx.state.runDefaults.simulationConfig.ddtScheme || 'Euler',
                    crankNicolsonCoeff: ctx.state.runDefaults.simulationConfig.crankNicolsonCoeff || 0.9,
                    solverType: ctx.state.runDefaults.simulationConfig.solverType || 'steady',
                    roomTemperature: ctx.state.geometry.temperature != null ? ctx.state.geometry.temperature : 22,
                    parallelSolve: ctx.state.runDefaults.simulationConfig.parallelSolve || false,
                    solveProcess: ctx.state.runDefaults.simulationConfig.solveProcess || 1,
                    gravity: {
                        x: ctx.state.runDefaults.simulationConfig.gravityX != null ? ctx.state.runDefaults.simulationConfig.gravityX : 0,
                        y: ctx.state.runDefaults.simulationConfig.gravityY != null ? ctx.state.runDefaults.simulationConfig.gravityY : -9.81,
                        z: ctx.state.runDefaults.simulationConfig.gravityZ != null ? ctx.state.runDefaults.simulationConfig.gravityZ : 0
                    },
                    monitoring: window.smGetMonitoringConfig ? window.smGetMonitoringConfig() : {},
                    residualControl: {
                        p_rgh: ctx.state.runDefaults.simulationConfig.residualControl ? ctx.state.runDefaults.simulationConfig.residualControl.p_rgh : 1e-4,
                        U: ctx.state.runDefaults.simulationConfig.residualControl ? ctx.state.runDefaults.simulationConfig.residualControl.U : 1e-5,
                        T: ctx.state.runDefaults.simulationConfig.residualControl ? ctx.state.runDefaults.simulationConfig.residualControl.T : 1e-5,
                        k_epsilon: ctx.state.runDefaults.simulationConfig.residualControl ? ctx.state.runDefaults.simulationConfig.residualControl.k_epsilon : 1e-4
                    },
                    schemes: ctx.state.runDefaults.simulationConfig.schemes || {},
                    relaxationFactors: ctx.state.runDefaults.simulationConfig.relaxationFactors || { p_rgh: 0.3, U: 0.7, T: 0.7, k_epsilon: 0.5 },
                    nNonOrthogonalCorrectors: ctx.state.runDefaults.simulationConfig.nNonOrthogonalCorrectors || 0,
                    solverPreset: ctx.state.runDefaults.simulationConfig.solverPreset || 'fast',
                    nonOrthoCorrection: ctx.state.runDefaults.simulationConfig.nonOrthoCorrection || false
                },
                inheritLiquidResults: !!(ctx.state.runDefaults && ctx.state.runDefaults.liquid && ctx.state.runDefaults.liquid.inheritResults),
                // Sensor points feed controlDict probes; runner reads at top level.
                // State already stores xyz in meters (see addSensorPoint). Runner
                // expects {x,y,z}; name/enabled are extras for round-trip.
                sensorPoints: Object.values((ctx.state.instrumentation && ctx.state.instrumentation.probes) || {}).map(function(p) {
                    return { id: p.uid, name: p.meta.name,
                             x: p.geometry.location.x, y: p.geometry.location.y, z: p.geometry.location.z, enabled: p.meta.enabled !== false };
                })
            };
        
            // Compute the same numbered patch solid name that rendering produces
            function patchSolidName(mdl, faceId, patchId) {
                const face = mdl.geometry.faces[faceId];
                const patches = face ? (face.patches || []) : [];
                const pIdx = patches.findIndex(p => p.id === patchId);
                return composeModelScopedExportName(mdl, faceId, face, {
                    isPatch: true, patchIndex: Math.max(0, pIdx), patchCount: patches.length || 1
                });
            }
        
            window.allModels().forEach(model => {
                // Note: model.meta.enabled only controls 3D viewport visibility, NOT data inclusion.
                // All models in the tree are included in the CFD export regardless of visibility.
                const name = sanitizeName(model.meta.name);
                const cfd = model.cooling || getDefaultCfd(model.kind);
                const xf = modelWorldTransform(model);
                const geom = {
                    anchor: { ...model.geometry.location },
                    size: { W: model.geometry.size.x, D: model.geometry.size.z, H: model.geometry.size.y },
                    orientation: quaternionToOrientationDeg(xf.quaternion),
                    quaternion: { x: xf.quaternion.x, y: xf.quaternion.y,
                                  z: xf.quaternion.z, w: xf.quaternion.w },
                    scale: { x: xf.scale.x, y: xf.scale.y,
                             z: xf.scale.z }
                };
        
                if (model.kind === 'box') {
                    // A box patch with role 'return' is an ACU return OUTLET on an
                    // odd-shaped unit modelled as ACU + box (the return grilles on
                    // the box's side): a return-only entry under `acus`, exactly as
                    // a ceiling return grille on a floor is exported.
                    const boxReturnPatches = Object.entries(model.geometry.faces)
                        .flatMap(([fid, f]) => (f.patches || []).map(p => ({ ...p, faceId: fid })))
                        .filter(p => p.physics && p.physics.role === 'return');
                    if (boxReturnPatches.length) {
                        cfdData.acus[name] = {
                            stlFile: 'merged_box.stl',
                            geometry: geom,
                            supplyFaces: [],
                            returnFaces: boxReturnPatches.map(p => ({
                                solidName: patchSolidName(model, p.faceId, p.id),
                                patchId: p.id,
                                face: p.faceId,
                                physics: {
                                    volumeFlowRate: p.physics.volumeFlowRate || 1.0,
                                    velocity: p.physics.velocity || 2.0
                                }
                            })),
                            wallSolidNames: []
                        };
                    }
                }
                if (model.kind === 'rack') {
                    const modelTV = model.timeVarying || {};
                    const servers = getRackServerViews(model).map(eq => {
                        const srvCfd = getRackServerPhysics(model, eq.id);
                        const exportStem = getRackEquipmentExportStem(model, eq);
                        // A fan-server that is switched OFF (cooling.powerMode
                        // 'unpowered') moves no air and makes no heat: it exports
                        // like an unpowered non-fan block — every face a no-slip,
                        // adiabatic wall — instead of a fan with zero flow, which
                        // the solver cannot take (fan_outlet ΔT = P / (ρ·Cp·Q)).
                        if (eq.kind === 'fanServer' && (srvCfd.powerMode || 'powered') !== 'unpowered') {
                            var facesExport = _buildServerFacesExport(model, eq);
        
                            const fanSrvObj = {
                                id: exportStem,
                                type: 'fanServer',
                                startSlot: eq.startUnit,
                                spanSlots: eq.spanUnits,
                                // Legacy fields for backward compat
                                inletSolidName:  facesExport.inlet.solid,
                                outletSolidName: facesExport.outlet.solid,
                                // New faces dict
                                faces: facesExport,
                                physics: Object.assign({
                                    volumeFlowRate: srvCfd.volumeFlowRate || 0.5,
                                    power: srvCfd.power || 1000,
                                    serverBCMode: srvCfd.serverBCMode || 'powerAndFlow',
                                    temperatureRise: srvCfd.temperatureRise || null,
                                    fixedTemperature: null,
                                    fixedTempFace: null
                                }, (function() {
                                    var tv = {};
                                    if (modelTV['power:' + eq.id]) tv.power = modelTV['power:' + eq.id];
                                    if (modelTV['airflow:' + eq.id]) tv.airflow = modelTV['airflow:' + eq.id];
                                    return Object.keys(tv).length ? {timeVarying: tv} : {};
                                })()),
                                // Absolute server depth (2026-09-17 spec §8): metres back
                                // from the rack inlet plane and from the rack outlet plane.
                                // The extra*Offset keys this replaces were relative to the
                                // rack's own row offsets, and no backend ever read them.
                                inletOffsetM: eq.inletOffset || 0,
                                outletOffsetM: eq.outletOffset || 0,
                                // Informational (WP-W6): left/right interface-wall
                                // solids — exported to
                                // merged_server_interface_walls.stl, never bound.
                                interfaceSolidNames: _buildServerInterfaceSolidNames(model, eq)
                            };
                            // Include liquid cooling config if enabled
                            if (eq.liquidCooling && eq.liquidCooling.enabled) {
                                fanSrvObj.liquidCooling = JSON.parse(JSON.stringify(eq.liquidCooling));
                            }
                            return fanSrvObj;
                        } else {
                            // nonFan: per-face independent settings
                            const powerMode = srvCfd.powerMode || 'powered';
                            const inletMode = powerMode === 'unpowered' ? 'unpowered' : (srvCfd.inletMode || 'powered');
                            const outletMode = powerMode === 'unpowered' ? 'unpowered' : (srvCfd.outletMode || 'powered');
        
                            function facePhysics(mode, heatType, temp, power) {
                                if (mode === 'unpowered') return { bcType: 'wall', temperature: null, power: null };
                                if (heatType === 'fixedPower') return { bcType: 'nonfan_heat_flux', temperature: null, power: power || 500 };
                                return { bcType: 'nonfan_fixed_temp', temperature: temp != null ? temp : 35.0, power: null };
                            }
        
                            const inletPhys = facePhysics(inletMode, srvCfd.inletHeatType || 'fixedTemp', srvCfd.inletTemperature, srvCfd.inletPower);
                            const outletPhys = facePhysics(outletMode, srvCfd.outletHeatType || 'fixedTemp', srvCfd.outletTemperature, srvCfd.outletPower);
        
                            var facesExport = _buildServerFacesExport(model, eq);
        
                            const nonFanObj = {
                                id: exportStem,
                                type: 'nonFan',
                                startSlot: eq.startUnit,
                                spanSlots: eq.spanUnits,
                                // Legacy fields for backward compat
                                inletSolidName:  facesExport.inlet.solid,
                                outletSolidName: facesExport.outlet.solid,
                                topSolidName:    facesExport.top.solid,
                                bottomSolidName: facesExport.bottom.solid,
                                // New faces dict
                                faces: facesExport,
                                physics: {
                                    inlet: inletPhys,
                                    outlet: outletPhys,
                                },
                                // The same absolute depth pair the fanServer branch carries.
                                inletOffsetM: eq.inletOffset || 0,
                                outletOffsetM: eq.outletOffset || 0,
                                // Informational (WP-W6): left/right interface-wall
                                // solids — exported to
                                // merged_server_interface_walls.stl, never bound.
                                interfaceSolidNames: _buildServerInterfaceSolidNames(model, eq)
                            };
                            // Include liquid cooling config if enabled
                            if (eq.liquidCooling && eq.liquidCooling.enabled) {
                                nonFanObj.liquidCooling = JSON.parse(JSON.stringify(eq.liquidCooling));
                            }
                            return nonFanObj;
                        }
                    });
                    const throughZoneGroups = getRackThroughZoneGroups(model);
                    const throughSlots = throughZoneGroups.flatMap(g => {
                        const slots = [];
                        for (let i = g.startUnit; i <= g.endUnit; i++) slots.push(i);
                        return slots;
                    });
                    geom.unitCount = model.geometry.slot;
                    geom.unitHeight = model.geometry.slotHeight;
                    geom.slotWidthRatio = model.geometry.slotWidthRatio;
                    geom.inletOffset = model.inletOffset || 0;
                    geom.outletOffset = model.outletOffset || 0;
                    // wallBaffleSolids is deprecated — blankingPanelType routes ALL
                    // blanking panels to perforatedBlankingPanel or wallBlankingPanel buckets.
                    const wallBaffleSolids = [];
                    const frameSolidNames = getRackFrameSolidNames(model);
                    const rackExportObj = {
                        stlFile: 'merged_rack_frame.stl',
                        leakageStlFile: null,
                        geometry: geom,
                        frameSolidNames: frameSolidNames,
                        safeMarginTop: model.safeMarginTop || 0,
                        safeMarginBottom: model.safeMarginBottom || 0,
                        partitions: ((model.constructions && model.constructions.partitions) || [])
                            .filter(function(p) { return !p.deleted; })
                            .map(function(p) {
                                return {
                                    index: p.index,
                                    solidName: getRackPartitionExportName(model, p.index)
                                };
                            }),
                        servers,
                        leakage: {
                            enabled: throughZoneGroups.length > 0,
                            throughSlots,
                            groups: throughZoneGroups.map(g => ({
                                startUnit: g.startUnit,
                                endUnit: g.endUnit,
                                sigma: g.sigma,
                                stlSolidName: `${name}_u${g.startUnit + 1}_u${g.endUnit + 1}_through_zone`
                            }))
                        },
                        wallBaffleSolids,
                        blankingPanelType: (model.cooling && model.cooling.blankingPanelType) || 'typicalSmallGap',
                        inletDoor: (() => {
                            var _faces = Array.isArray(model.geometry.faces) ? model.geometry.faces : [];
                            var _door = _faces.find(function(f){ return f && f.uid === 'inletDoor' && f.role === 'door'; });
                            if (!_door) return null;
                            var slotAreaH = model.geometry.slot * model.geometry.slotHeight;
                            if (slotAreaH <= 1e-6) return null;
                            return {
                                enabled: true,
                                perforated: toPerf(_door.perforation),
                                solidName: sanitizeName(model.meta.name) + '_inlet_door',
                            };
                        })(),
                        outletDoor: (() => {
                            var _faces = Array.isArray(model.geometry.faces) ? model.geometry.faces : [];
                            var _door = _faces.find(function(f){ return f && f.uid === 'outletDoor' && f.role === 'door'; });
                            if (!_door) return null;
                            var slotAreaH = model.geometry.slot * model.geometry.slotHeight;
                            if (slotAreaH <= 1e-6) return null;
                            return {
                                enabled: true,
                                perforated: toPerf(_door.perforation),
                                solidName: sanitizeName(model.meta.name) + '_outlet_door',
                            };
                        })(),
                        wallSolidNames: frameSolidNames.slice()
                    };
                    // RDHx
                    const _consRdhx = model.constructions && model.constructions.rdhx;
                    if (_consRdhx && _consRdhx.enabled) {
                        rackExportObj.rdhx = {
                            enabled: true,
                            waterFlowRate: _consRdhx.waterFlowRate != null ? _consRdhx.waterFlowRate : 20,
                            waterInletTemp: _consRdhx.waterInletTemp != null ? _consRdhx.waterInletTemp : 25,
                            effectiveness: _consRdhx.effectiveness != null ? _consRdhx.effectiveness : 0.5,
                        };
                    }
                    cfdData.racks[name] = rackExportObj;
                } else if (model.kind === 'acu') {
                    const allPatches = Object.entries(model.geometry.faces).flatMap(([fid, f]) => f.patches.map(p => ({...p, faceId: fid})));
                    const supplyPatches = allPatches.filter(p => p.physics && p.physics.role === 'supply');
                    const returnPatches = allPatches.filter(p => p.physics && p.physics.role === 'return');
                    const supplyFaces = supplyPatches.map(p => {
                        const phys = {
                            temperature: p.physics.temperature || 15.0,
                            volumeFlowRate: p.physics.volumeFlowRate || 4.0,
                            velocity: p.physics.velocity || 2.0,
                        };
                        if (p.physics.coolingCapacity != null) phys.coolingCapacity = p.physics.coolingCapacity;
                        if (p.physics.setPointTemperature != null) phys.setPointTemperature = p.physics.setPointTemperature;
                        if (p.physics.coolantTemperature != null) phys.coolantTemperature = p.physics.coolantTemperature;
                        if (p.physics.ratedReturnTemp != null) phys.ratedReturnTemp = p.physics.ratedReturnTemp;
                        var acuMode = model.acuPerformanceMode || 'fixedTemp';
                        if (acuMode !== 'fixedTemp') {
                            var cm = {mode: acuMode};
                            if (acuMode === 'linearCapacity') {
                                cm.ratedReturnTemp = p.physics.ratedReturnTemp || 35;
                            } else if (acuMode === 'tableCapacity') {
                                cm.performanceTable = model.coolingPerformanceTable || [
                                    {returnTemp: 25, capacity: 20},
                                    {returnTemp: 30, capacity: 30},
                                    {returnTemp: 35, capacity: 35}
                                ];
                            }
                            phys.coolingModel = cm;
                        }
                        if (model.timeVarying && Object.keys(model.timeVarying).length > 0) phys.timeVarying = model.timeVarying;
                        return {
                            solidName: patchSolidName(model, p.faceId, p.id),
                            patchId: p.id,
                            face: p.faceId,
                            physics: phys,
                        };
                    });
                    const returnFaces = returnPatches.map(p => {
                        const retPhys = {
                            volumeFlowRate: p.physics.volumeFlowRate || 4.0,
                            velocity: p.physics.velocity || 2.0
                        };
                        if (model.timeVarying && Object.keys(model.timeVarying).length > 0) retPhys.timeVarying = model.timeVarying;
                        return {
                            solidName: patchSolidName(model, p.faceId, p.id),
                            patchId: p.id,
                            face: p.faceId,
                            physics: retPhys
                        };
                    });
                    cfdData.acus[name] = {
                        stlFile: 'merged_acu.stl',
                        geometry: geom,
                        supplyFaces,
                        returnFaces,
                        wallSolidNames: []
                    };
                } else if (model.kind === 'partitionWall') {
                    const bodySolidName = sanitizeName(`${model.meta.name}_body`);
                    const holes = getPartitionWallPatchStore(model).map((patch, index) => {
                        const phys = cfdprojStateV4.getPatchPhysics(model, patch.id) || { leakageRate: 100 };
                        const zoneName = sanitizeName(`${model.meta.name}_hole_${index + 1}_zone`);
                        const leakageRate = phys.leakageRate != null ? phys.leakageRate : 100;
                        // 2026-04-16 simplification: only leakageRate (< 100%)
                        // gates the source term; the legacy resistanceEnabled
                        // checkbox was removed from the UI.
                        const enabled = leakageRate < 100;
                        return {
                            patchId: patch.id,
                            shape: patch.type === 'circle' ? 'circle' : 'rectangle',
                            name: patch.name || `hole_${index + 1}`,
                            zoneName,
                            zoneStlFile: enabled ? `partition_hole_${zoneName}.stl` : null,
                            // Emit `resistanceEnabled: true` for backward compat
                            // with backends that still gate on it.
                            physics: { resistanceEnabled: enabled, leakageRate }
                        };
                    });
                    cfdData.partitionWalls[name] = {
                        stlFile: 'merged_partition_wall.stl',
                        geometry: {
                            anchor: { ...geom.anchor },
                            size: { W: geom.size.W, D: geom.size.D, H: geom.size.H },
                            orientation: geom.orientation,
                            transformQuaternion: xf.quaternion
                        },
                        wallSolidNames: [bodySolidName],
                        holes
                    };
                } else if (model.kind === 'box' && cfd.role === 'wallObject') {
                    const heatPatches = [];
                    Object.entries(model.geometry.faces).forEach(([fid, face]) => {
                        (face.patches || []).forEach(patch => {
                            const patchCfd = patch.cfd || {};
                            if (patchCfd.fixedTemperature != null) {
                                heatPatches.push({
                                    solidName: patchSolidName(model, fid, patch.id),
                                    patchId: patch.id,
                                    physics: { heatPower: 0, fixedTemperature: patchCfd.fixedTemperature }
                                });
                            }
                        });
                    });
                    (cfd.heatPatches || []).forEach(hp => {
                        const alreadyExported = heatPatches.some(h => h.patchId === hp.patchId);
                        if (!alreadyExported) {
                            heatPatches.push({
                                solidName: hp.solidName || 'heat_patch',
                                patchId: hp.patchId,
                                physics: { heatPower: hp.heatPower || 0, fixedTemperature: null }
                            });
                        }
                    });
                    cfdData.wallObjects[name] = {
                        stlFile: 'merged_box.stl',
                        geometry: geom,
                        heatPatches,
                        wallSolidNames: []
                    };
                } else if (model.kind === 'box' && cfd.role === 'heatSource') {
                    // Heat Source: all body (non-patch) solids get uniform heat BC
                    const wallSolids = heatSourceBodyParts(model).map(part => part.name);
                    cfdData.heatSources[name] = {
                        stlFile: 'merged_box.stl',
                        geometry: geom,
                        heatMode: cfd.heatMode || 'power',
                        totalPower: cfd.totalPower != null ? cfd.totalPower : 100,
                        fixedTemperature: cfd.fixedTemperature != null ? cfd.fixedTemperature : null,
                        wallSolidNames: wallSolids,
                        timeVarying: model.timeVarying || {}
                    };
                } else if (model.kind === 'box' || model.kind === 'wall') {
                    // Generic box (non-wallObject role) or wall: export as wallObject if any patch
                    // has fixedTemperature set via the patch inspector
                    const heatPatches = [];
                    Object.entries(model.geometry.faces).forEach(([fid, face]) => {
                        (face.patches || []).forEach(patch => {
                            const patchCfd = patch.cfd || {};
                            if (patchCfd.fixedTemperature != null) {
                                heatPatches.push({
                                    solidName: patchSolidName(model, fid, patch.id),
                                    patchId: patch.id,
                                    physics: { heatPower: 0, fixedTemperature: patchCfd.fixedTemperature }
                                });
                            }
                        });
                    });
                    if (heatPatches.length > 0) {
                        cfdData.wallObjects[name] = {
                            stlFile: 'merged_box.stl',
                            geometry: geom,
                            heatPatches,
                            wallSolidNames: []
                        };
                    } else if (model.kind === 'wall') {
                        // isRoomBoundary tells the runner the room enclosure from interior walls:
                        // only an enclosure wall must reach room.height, or the room shell is open
                        // (STLGenRunner.validate_geometry); an interior wall below the ceiling is fine.
                        cfdData.walls[name] = { stlFile: 'room_wall.stl', geometry: geom, isRoomBoundary: model.isRoomBoundary === true };
                    }
                    // Generic box with no heat patches and no special role: not exported (room wall)
                } else if (model.kind === 'baffle') {
                    // V4 baffle cooling: remaining-area type at cooling.type, the
                    // perforated block at cooling.perforation; surviving seed configs
                    // keep their names on cooling.
                    const cooling = model.cooling || {};
                    const baffleDefaultType = cooling.type || 'wall';
                    // Build per-patch physics: maps STL solid name -> {baffleType, porosity}
                    const bafflePatchPhysicsMap = {};
                    const bafflePatches = Object.values(model.geometry.faces).flatMap(f => f.patches);
                    bafflePatches.forEach(patch => {
                        const pp = patch.physics || { baffleType: baffleDefaultType };
                        const faceId = Object.keys(model.geometry.faces).find(fid =>
                            (model.geometry.faces[fid].patches || []).some(p => p.id === patch.id)
                        ) || 'front';
                        const solidName = patchSolidName(model, faceId, patch.id);
                        const entry = {
                            baffleType: pp.baffleType || 'wall',
                            seedConfig: cloneSeedConfig(pp.seedConfig)
                        };
                        if (entry.baffleType === 'perforated') {
                            entry.perforated = toPerf(pp.perforated || cooling.perforation);
                        }
                        bafflePatchPhysicsMap[solidName] = entry;
                    });
                    cfdData.baffles[name] = {
                        stlFile: 'merged_baffle.stl',
                        geometry: { anchor: geom.anchor, size: { W: geom.size.W, H: geom.size.H }, orientation: geom.orientation },
                        physics: (() => {
                            const phys = { baffleType: baffleDefaultType };
                            if (phys.baffleType === 'perforated') phys.perforated = toPerf(cooling.perforation);
                            return phys;
                        })(),
                        seedConfig: cloneSeedConfig(cooling.seedConfig),
                        wallRegionSeedConfigs: cloneSeedConfigMap(cooling.wallRegionSeedConfigs),
                        patchPhysics: bafflePatchPhysicsMap
                    };
                } else if (model.kind === 'floor') {
                    // V4 floor: openings live at geometry.openings (each opening's
                    // `perforated`->`perforation`); the remaining-area BC type is the
                    // bare string cooling.defaultBoundaryType, its perforated block is
                    // cooling.defaultPerforation, and the room-boundary semantic is
                    // cooling.roomBoundaryLocation. The default seed configs are
                    // cooling.defaultSeedConfig / cooling.defaultSeedConfigs.
                    const cooling = model.cooling || {};
                    // Filter out openings whose patch has been deleted from the model
                    const allPatches = Object.values(model.geometry.faces).flatMap(f => f.patches || []);
                    // A floor patch with role 'return' is an ACU return OUTLET (a
                    // ceiling return grille), not an opening: it leaves the openings
                    // list and is emitted as a return-only entry under `acus`, which
                    // is what makes the mesher give its solid `patchInfo { type patch; }`
                    // (stlgen_runner acu_return_solids) and the BC layer an acu_return.
                    const floorReturnPatches = Object.entries(model.geometry.faces)
                        .flatMap(([fid, f]) => (f.patches || []).map(p => ({ ...p, faceId: fid })))
                        .filter(p => p.physics && p.physics.role === 'return');
                    const floorReturnIds = new Set(floorReturnPatches.map(p => p.id));
                    const openings = ((model.geometry && model.geometry.openings) || []).filter(op =>
                        allPatches.some(p => p.id === op.patchId) && !floorReturnIds.has(op.patchId)
                    ).map(op => {
                        const patch = allPatches.find(p => p.id === op.patchId);
                        return {
                            solidName: (() => {
                                if (!patch) return `opening_${op.patchId}`;
                                const faceId = Object.keys(model.geometry.faces).find(fid =>
                                    (model.geometry.faces[fid].patches || []).some(p => p.id === op.patchId)
                                ) || 'top';
                                return patchSolidName(model, faceId, op.patchId);
                            })(),
                            patchId: op.patchId,
                            physics: (() => {
                                const phys = { type: op.type || 'wall' };
                                if (phys.type === 'perforated') phys.perforated = toPerf(op.perforation);
                                return phys;
                            })(),
                            seedConfig: cloneSeedConfig(op.seedConfig)
                        };
                    });
                    const floorDefaultBCType = cooling.defaultBoundaryType || 'wall';
                    cfdData.floors[name] = {
                        stlFile: 'merged_floor_ceiling.stl',
                        geometry: { anchor: geom.anchor, size: { W: geom.size.W, D: geom.size.D } },
                        roomBoundaryType: ['top', 'bottom'].includes(cooling.roomBoundaryLocation) ? cooling.roomBoundaryLocation : 'none',
                        defaultBC: floorDefaultBCType,
                        // 2026-04-16 unification: floor remaining-area perforated
                        // BC carries a full `perforated` block (shape, hole size,
                        // open area pct) instead of a scalar porosity. Only emit
                        // when the toggle is actually set to 'perforated'.
                        defaultBCPerforated: (floorDefaultBCType === 'perforated')
                            ? toPerf(cooling.defaultPerforation) : null,
                        defaultBCSeedConfig: cloneSeedConfig(cooling.defaultSeedConfig),
                        defaultBCSeedConfigs: cloneSeedConfigMap(cooling.defaultSeedConfigs),
                        openings
                    };
                    if (floorReturnPatches.length) {
                        cfdData.acus[name] = {
                            stlFile: 'merged_floor_ceiling.stl',
                            geometry: { anchor: geom.anchor, size: { W: geom.size.W, D: geom.size.D, H: 0 } },
                            supplyFaces: [],
                            returnFaces: floorReturnPatches.map(p => ({
                                solidName: patchSolidName(model, p.faceId, p.id),
                                patchId: p.id,
                                face: p.faceId,
                                physics: {
                                    volumeFlowRate: p.physics.volumeFlowRate || 4.0,
                                    velocity: p.physics.velocity || 2.0
                                }
                            })),
                            wallSolidNames: []
                        };
                    }
                } else if (model.kind === 'cdu') {
                    // CDU liquid config is collected by buildLiquidCoolingExport.
                    // CDU box geometry is always exported as a wallObject so it
                    // participates in meshing as a solid obstacle (noSlip + zeroGradient).
                    cfdData.wallObjects[name] = {
                        stlFile: 'merged_box.stl',
                        geometry: geom,
                        heatPatches: [],
                        wallSolidNames: []
                    };
                }
            });
        
            // Build liquid cooling section from CDU models + global state
            if (window.buildLiquidCoolingExport) {
                const lcExport = window.buildLiquidCoolingExport(window.allModels());
                if (lcExport) {
                    cfdData.liquidCooling = lcExport;
                }
            }
        
            return cfdData;
        }

            function buildComprehensiveStlMap(state) {
            const stlMap = {};
        
            // Reuse buildCfdrunJson's numbered-patch solid name (ACU / box patches).
            const patchSolidNameFor = (mdl, faceId, patchId) => {
                const face = mdl.geometry.faces[faceId];
                const patches = face ? (face.patches || []) : [];
                const pIdx = patches.findIndex(p => p.id === patchId);
                return composeModelScopedExportName(mdl, faceId, face, {
                    isPatch: true, patchIndex: Math.max(0, pIdx), patchCount: patches.length || 1
                });
            };
        
            const put = (solidName, entry) => {
                if (!solidName) return;
                entry.solidName = solidName;
                entry.patchName = sanitizeName(solidName);
                stlMap[solidName] = entry;
            };
        
            window.allModels().forEach(model => {
                const modelName = sanitizeName(model.meta.name);
        
                if (model.kind === 'rack') {
                    const rackUid = model.uid;
                    const rackObjPath = 'constructions.racks.' + rackUid;
        
                    // -- servers: fan / nonFan / liquid (liquid exports as nonFan) --
                    getRackServerViews(model).forEach(eq => {
                        const serverUid = eq.id;
                        const srvObjPath = rackObjPath + '.servers.' + serverUid;
                        // FOUR faces only (inlet/outlet/top/bottom) — the pinned
                        // enumeration in _buildServerFacesExport. The left/right
                        // interface walls (WP-W6) get NO stlMap entry, so no
                        // source triple, so the V2 bySource join can never bind
                        // them. Do not "complete" this loop to six faces.
                        const facesExport = _buildServerFacesExport(model, eq);
                        Object.keys(facesExport).forEach(faceId => {
                            const fe = facesExport[faceId];
                            if (fe.patches && fe.patches.length > 0) {
                                fe.patches.forEach(p => {
                                    put(p.solid, {
                                        kind: 'rack_server',
                                        objectPath: srvObjPath,
                                        surfaceUid: faceId + '_' + p.patchId,
                                        source: { modelUid: serverUid, faceId: faceId, patchId: p.patchId },
                                    });
                                });
                                if (fe.wallSolid) {
                                    put(fe.wallSolid, {
                                        kind: 'rack_server',
                                        objectPath: srvObjPath,
                                        surfaceUid: faceId + '___wall__',
                                        source: { modelUid: serverUid, faceId: faceId, patchId: '__wall__' },
                                    });
                                }
                            } else if (fe.solid) {
                                put(fe.solid, {
                                    kind: 'rack_server',
                                    objectPath: srvObjPath,
                                    surfaceUid: faceId + '___face__',
                                    source: { modelUid: serverUid, faceId: faceId, patchId: '__face__' },
                                });
                            }
                        });
                    });
        
                    // -- frame solids (all wall) --
                    getRackFrameSolidNames(model).forEach(fs => {
                        put(fs, { kind: 'frame', objectPath: rackObjPath,
                                  source: { modelUid: rackUid } });
                    });
        
                    // -- rack internal partitions (all wall) --
                    ((model.constructions && model.constructions.partitions) || [])
                        .filter(p => !p.deleted)
                        .forEach(p => {
                            put(getRackPartitionExportName(model, p.index), {
                                kind: 'rack_partition', objectPath: rackObjPath,
                                source: { modelUid: rackUid } });
                        });
        
                    // -- through-zone (leakage) solids: through_zone, sigma per group --
                    getRackThroughZoneGroups(model).forEach(g => {
                        const solid = modelName + '_u' + (g.startUnit + 1) + '_u' + (g.endUnit + 1) + '_through_zone';
                        put(solid, {
                            kind: 'through_zone', objectPath: rackObjPath,
                            sigma: (typeof g.sigma === 'number' && isFinite(g.sigma)) ? g.sigma : 0.05,
                            source: { modelUid: rackUid } });
                    });
        
                } else if (model.kind === 'acu') {
                    const acuUid = model.uid;
                    const acuObjPath = 'constructions.acus.' + acuUid;
                    const allPatches = Object.entries(model.geometry.faces)
                        .flatMap(([fid, f]) => (f.patches || []).map(p => ({ ...p, faceId: fid })));
                    allPatches.forEach(p => {
                        const role = p.physics && p.physics.role;
                        if (role !== 'supply' && role !== 'return') return;
                        put(patchSolidNameFor(model, p.faceId, p.id), {
                            kind: 'acu', objectPath: acuObjPath,
                            surfaceUid: p.faceId + '_' + p.id,
                            source: { modelUid: acuUid, faceId: p.faceId, patchId: p.id },
                        });
                    });
        
                } else if (model.kind === 'wall') {
                    // Room walls → wall. The room_wall STL solid name equals the
                    // sanitized model name (gold matches san.startswith(san_wall);
                    // single-edge walls produce exactly {name}).
                    put(modelName, {
                        kind: 'room_wall',
                        objectPath: 'constructions.walls.' + model.uid,
                        source: { modelUid: model.uid } });
        
                } else if (model.kind === 'partitionWall') {
                    // Partition-wall body → wall (gold wallSolidNames = [{name}_body]).
                    put(sanitizeName(model.meta.name + '_body'), {
                        kind: 'partition_wall',
                        objectPath: 'constructions.partitionWalls.' + model.uid,
                        source: { modelUid: model.uid } });
                    // NOTE: partition-wall HOLES (porousZone sourceTerms) are T3b.

                } else if (model.kind === 'floor') {
                    // Floor-hosted ACU return grilles: a device entry with the full
                    // source triple, so the V2 join (bySource) can bind an acu_return
                    // to it. Every other floor solid is porous/boundary work below.
                    Object.entries(model.geometry.faces).forEach(([fid, f]) => {
                        (f.patches || []).forEach(p => {
                            if (!(p.physics && p.physics.role === 'return')) return;
                            put(patchSolidNameFor(model, fid, p.id), {
                                kind: 'acu', objectPath: 'constructions.floors.' + model.uid,
                                surfaceUid: fid + '_' + p.id,
                                source: { modelUid: model.uid, faceId: fid, patchId: p.id },
                            });
                        });
                    });
        
                } else if (model.kind === 'box' || model.kind === 'cdu') {
                    const cfd = model.cooling || getDefaultCfd(model.kind);
                    const role = cfd.role;
                    // Box-hosted ACU return grilles (see the acus export above).
                    Object.entries(model.geometry.faces).forEach(([fid, f]) => {
                        (f.patches || []).forEach(p => {
                            if (!(p.physics && p.physics.role === 'return')) return;
                            put(patchSolidNameFor(model, fid, p.id), {
                                kind: 'acu', objectPath: 'constructions.boxes.' + model.uid,
                                surfaceUid: fid + '_' + p.id,
                                source: { modelUid: model.uid, faceId: fid, patchId: p.id },
                            });
                        });
                    });
                    if (role === 'heatSource') {
                        // Heat source: uniform heat BC on every body (non-patch) solid.
                        const size = { W: model.geometry.size.x, D: model.geometry.size.z, H: model.geometry.size.y };
                        heatSourceBodyParts(model).forEach(part => {
                            put(part.name, {
                                kind: 'heat_source',
                                objectPath: 'constructions.boxes.' + model.uid,
                                heatMode: cfd.heatMode || 'power',
                                totalPower: cfd.totalPower != null ? cfd.totalPower : 100,
                                fixedTemperature: cfd.fixedTemperature != null ? cfd.fixedTemperature : null,
                                size: size,
                                timeVarying: model.timeVarying || {},
                                source: { modelUid: model.uid, faceId: part.faceId } });
                        });
                    } else {
                        // wallObject (box role=wallObject, generic box/wall with heat
                        // patches, CDU obstacle). Gold solid = {modelName}_{hp.solidName};
                        // emit one heat-patch entry per patch carrying fixedTemperature.
                        const objSize = { W: model.geometry.size.x, D: model.geometry.size.z, H: model.geometry.size.y };
                        Object.entries(model.geometry.faces).forEach(([fid, face]) => {
                            (face.patches || []).forEach(patch => {
                                const patchCfd = patch.cfd || {};
                                if (patchCfd.fixedTemperature == null) return;
                                const hpSolid = patchSolidNameFor(model, fid, patch.id);
                                put(sanitizeName(modelName + '_' + hpSolid), {
                                    kind: 'wall_object',
                                    objectPath: 'constructions.boxes.' + model.uid,
                                    physics: { heatPower: 0, fixedTemperature: patchCfd.fixedTemperature },
                                    objSize: objSize,
                                    source: { modelUid: model.uid, faceId: fid, patchId: patch.id } });
                            });
                        });
                        // Legacy cfd.heatPatches (power-based) → wall_object too.
                        (cfd.heatPatches || []).forEach(hp => {
                            const already = Object.keys(stlMap).some(k => stlMap[k].source
                                && stlMap[k].source.patchId === hp.patchId
                                && stlMap[k].source.modelUid === model.uid);
                            if (already) return;
                            const hpSolid = hp.solidName || 'heat_patch';
                            put(sanitizeName(modelName + '_' + hpSolid), {
                                kind: 'wall_object',
                                objectPath: 'constructions.boxes.' + model.uid,
                                physics: { heatPower: hp.heatPower || 0, fixedTemperature: null },
                                objSize: objSize,
                                source: { modelUid: model.uid, patchId: hp.patchId } });
                        });
                    }
                }
                // floor / baffle → T3b (porous, emitted below from the grouped STL).
                // Other kinds: no device binding.
            });
        
            // --------------------------------------------------------------------
            // P3 T3b — POROUS / baffle-family kinds + partition-hole sourceTerms.
            //
            // The baffle-family solids (floor openings + remaining-area, baffle
            // patches + remaining-area, rack doors, blanking panels, the unified
            // merged_* baffle solids) are POST-EXPORT artifacts: their solid NAMES
            // are produced by buildGroupedStlMap() (the same grouped STL the backend
            // meshes), then createBaffles splits each into <solid>_master/_slave.
            // _build_bridge_bc derives the registry by iterating the backend's
            // _stl_solids (which == buildGroupedStlMap output). We mirror that here:
            // parse buildGroupedStlMap once, classify each porous-relevant solid by
            // its bucket FILENAME + name (exactly the gold's classification), and
            // emit a stlMap entry tagged with kind + raw perforation. The translators
            // (translateOpenings / translateRackExtras) do sigma=clamp(openAreaPct/
            // 100); <=0 → wallBaffle, >0 → perforatedBaffle, and emit master+slave.
            //
            // Gold reference: _build_bridge_bc stlgen_runner.py:3499-3754.
            // --------------------------------------------------------------------
            const DEFAULT_PERF_T3B = { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: 64.0 };
            const toPerfT3b = (src) => ({
                shape: (src && src.shape) || DEFAULT_PERF_T3B.shape,
                holeDiameterMm: Number.isFinite(src && src.holeDiameterMm) ? src.holeDiameterMm : DEFAULT_PERF_T3B.holeDiameterMm,
                openAreaPct: Number.isFinite(src && src.openAreaPct) ? src.openAreaPct : DEFAULT_PERF_T3B.openAreaPct,
            });
            // _BLANKING_PANEL_SIGMA == 0.6401 (stlgen_runner.py:102). Small-gap perf
            // block: {circle, 1.0mm, 64.01%} → translator sigma = 0.6401.
            const BLANKING_PERF_T3B = { shape: 'circle', holeDiameterMm: 1.0, openAreaPct: 64.01 };
        
            // ---- State-derived classification indices (keyed by sanitized solid) ----
            // Floor openings: solidName → {type, perforation}; plus per-floor
            // defaultBC + defaultBCPerforated, and the set of each floor's sanitized
            // name (prefix for remaining-area solids).
            const floorOpeningInfo = {};        // sanSolid → {type:'perforated'|'wallBaffle'|..., perforation?}
            const floorOpeningSans = new Set(); // all floor opening sanitized solid names
            const floorDefaults = [];           // [{sanName, defaultBC, perforation?}]
            // Baffle patches: solidName → {baffleType, perforation?}; baffle default
            // per model; set of baffle sanitized model-name prefixes.
            const bafflePatchInfo = {};         // sanSolid → {baffleType, perforation?}
            const baffleDefaults = [];          // [{sanName, baffleType, perforation?}]
            // Rack doors: sanitized solid → {perforation}; and the per-rack door
            // info so the unified merged_rack_door key can derive the uniform sigma.
            const doorInfo = {};                // sanSolid → {perforation}
            const doorSigmas = new Set();
            // State-derived "any wall baffle exists" flag. The backend consolidates
            // ALL wall-type baffle solids into merged_wall_baffle.stl → one unified
            // master/slave pair. We can't rely on parsing the grouped STL for it: the
            // headless export sometimes renders no geometry for standalone containment
            // baffle models, yet the meshed registry still carries merged_wall_baffle.
            // So derive it from state: a wall baffle exists iff any baffle model is not
            // entirely perforated, or any floor defaults/openings to wallBaffle.
            let hasWallBaffleState = false;
        
            window.allModels().forEach(model => {
                const sanModel = sanitizeName(model.meta.name);
                if (model.kind === 'floor') {
                    const cooling = model.cooling || {};
                    const allPatches = Object.values(model.geometry.faces).flatMap(f => f.patches || []);
                    const returnIds = new Set(allPatches.filter(p => p.physics && p.physics.role === 'return').map(p => p.id));
                    ((model.geometry && model.geometry.openings) || [])
                        .filter(op => allPatches.some(p => p.id === op.patchId) && !returnIds.has(op.patchId))
                        .forEach(op => {
                            const patch = allPatches.find(p => p.id === op.patchId);
                            const faceId = patch
                                ? (Object.keys(model.geometry.faces).find(fid =>
                                    (model.geometry.faces[fid].patches || []).some(p => p.id === op.patchId)) || 'top')
                                : 'top';
                            const solidName = patch ? patchSolidNameFor(model, faceId, op.patchId) : ('opening_' + op.patchId);
                            const san = sanitizeName(solidName);
                            const type = op.type || 'wall';
                            const info = { type: type };
                            if (type === 'perforated') info.perforation = toPerfT3b(op.perforation);
                            else if (type === 'wallBaffle') hasWallBaffleState = true;
                            floorOpeningInfo[san] = info;
                            floorOpeningSans.add(san);
                        });
                    const defaultBC = cooling.defaultBoundaryType || 'wall';
                    const fd = { sanName: sanModel, defaultBC: defaultBC };
                    if (defaultBC === 'perforated') fd.perforation = toPerfT3b(cooling.defaultPerforation);
                    else if (defaultBC === 'wallBaffle') hasWallBaffleState = true;
                    floorDefaults.push(fd);
                } else if (model.kind === 'baffle') {
                    const cooling = model.cooling || {};
                    const baffleDefaultType = cooling.type || 'wall';
                    const bd = { sanName: sanModel, baffleType: baffleDefaultType };
                    if (baffleDefaultType === 'perforated') bd.perforation = toPerfT3b(cooling.perforation);
                    baffleDefaults.push(bd);
                    // Any baffle that is not 'perforated' by default has a wall part
                    // (its whole-solid or wall remainder) → merged_wall_baffle.
                    if (baffleDefaultType !== 'perforated') hasWallBaffleState = true;
                    Object.entries(model.geometry.faces).forEach(([fid, face]) => {
                        (face.patches || []).forEach(patch => {
                            const pp = patch.physics || { baffleType: baffleDefaultType };
                            const solidName = patchSolidNameFor(model, fid, patch.id);
                            const san = sanitizeName(solidName);
                            const bt = pp.baffleType || 'wall';
                            const info = { baffleType: bt };
                            if (bt === 'perforated') info.perforation = toPerfT3b(pp.perforated || cooling.perforation);
                            else hasWallBaffleState = true; // a wall-type patch → merged_wall_baffle
                            bafflePatchInfo[san] = info;
                        });
                    });
                } else if (model.kind === 'rack') {
                    const faces = Array.isArray(model.geometry.faces) ? model.geometry.faces : [];
                    const slotAreaH = model.geometry.slot * model.geometry.slotHeight;
                    ['inlet', 'outlet'].forEach(side => {
                        const door = faces.find(f => f && f.uid === (side + 'Door') && f.role === 'door');
                        if (!door) return;
                        if (slotAreaH <= 1e-6) return;
                        const solidName = sanModel + '_' + side + '_door';
                        const san = sanitizeName(solidName);
                        const perf = toPerfT3b(door.perforation);
                        doorInfo[san] = { perforation: perf };
                        doorSigmas.add(Math.max(0.0, Math.min(1.0, perf.openAreaPct / 100.0)));
                    });
                }
            });
        
            // Helper: derive a remaining-area floor/baffle solid's perforation from
            // the floor/baffle DEFAULT whose sanitized name prefixes the solid.
            const remainingPerfFor = (san, defaults) => {
                // Longest-prefix match (mirrors gold's startswith on _sanitize(name)).
                let best = null;
                defaults.forEach(d => {
                    if (san.startsWith(d.sanName) && (!best || d.sanName.length > best.sanName.length)) best = d;
                });
                return best;
            };
        
            const putPorous = (solidName, entry) => {
                if (!solidName) return;
                if (stlMap[solidName]) return; // already emitted (device kind wins / dedupe)
                entry.solidName = solidName;
                entry.patchName = sanitizeName(solidName);
                stlMap[solidName] = entry;
            };
        
            // Parse the grouped STL (the exact solid set the backend meshes).
            let groupedMap = {};
            try {
                const allIds = window.allModels().map(m => m.uid);
                // Through the context: this is an INSTANCE method of the geometry
                // scene, not a module-level export, and the legacy page's
                // top-level wrapper is not reachable from another host. Without
                // it the catch below would quietly skip every porous solid.
                var _grouped = ctx.buildGroupedStlMap
                    || (typeof window !== 'undefined' ? window.buildGroupedStlMap : null);
                if (typeof _grouped !== 'function') {
                    throw new Error('cfdrun_build: ctx.buildGroupedStlMap is missing');
                }
                groupedMap = _grouped(allIds, true);
                // Stash for the mesh-upload path (runMeshGeneration) to reuse —
                // buildGroupedStlMap re-tessellates the ENTIRE scene (8 MB+ ASCII STL
                // for 200+ racks) and the upload calls it again immediately after with
                // identical args. Consumed + cleared there so it can never go stale
                // (buildComprehensiveStlMap re-runs at the start of every mesh flow).
                try { window._meshExportGroupedMap = groupedMap; } catch (e2) {}
            } catch (e) {
                console.warn('[buildComprehensiveStlMap] buildGroupedStlMap failed; porous solids skipped:', e);
                groupedMap = {};
            }
        
            // Thermal load identities and areas must describe the actual uploaded
            // world-space STL, including body subtraction and model scaling.
            const thermalNames = Object.keys(stlMap).filter(name => stlMap[name].kind === 'heat_source');
            if (thermalNames.length) {
                const wanted = new Set(thermalNames);
                const actualAreas = new Map();
                const fileIds = new Map(Object.keys(groupedMap).sort().map((name, i) => [name, 'stl_' + String(i).padStart(3, '0')]));
                Object.entries(groupedMap).forEach(([filename, text]) => {
                    const solidRe = /^solid\s+(\S+)\s*\r?\n([\s\S]*?)^endsolid\b/gm;
                    let match;
                    while ((match = solidRe.exec(text)) !== null) {
                        const name = match[1];
                        if (!wanted.has(name)) continue;
                        if (actualAreas.has(name)) throw new Error('duplicate thermal source STL solid: ' + name);
                        const vertices = Array.from(match[2].matchAll(/vertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)/g),
                            m => [Number(m[1]), Number(m[2]), Number(m[3])]);
                        let area = 0;
                        if (!vertices.length || vertices.length % 3) throw new Error('invalid thermal STL triangles: ' + name);
                        for (let i = 0; i < vertices.length; i += 3) {
                            const a = vertices[i], b = vertices[i + 1], c = vertices[i + 2];
                            const u = b.map((v, j) => v - a[j]), v = c.map((n, j) => n - a[j]);
                            area += Math.hypot(u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]) / 2;
                        }
                        if (!Number.isFinite(area) || area <= 1e-8) throw new Error('invalid thermal STL area: ' + name);
                        actualAreas.set(name, area);
                        stlMap[name].path = filename;
                        stlMap[name].stlFileId = fileIds.get(filename);
                    }
                });
                const totalAreas = new Map();
                thermalNames.forEach(name => {
                    if (!actualAreas.has(name)) throw new Error('thermal source body is missing its STL solid: ' + name);
                    const entry = stlMap[name], uid = entry.source.modelUid;
                    entry.areaM2 = actualAreas.get(name);
                    totalAreas.set(uid, (totalAreas.get(uid) || 0) + entry.areaM2);
                });
                thermalNames.forEach(name => {
                    stlMap[name].thermalSurfaceAreaM2 = totalAreas.get(stlMap[name].source.modelUid);
                });
            }

            const parseSolidNames = (stl) => {
                const names = [];
                if (!stl) return names;
                const re = /solid\s+(\S+)\s*\n/g;
                let m;
                while ((m = re.exec(stl)) !== null) names.push(m[1]);
                return names;
            };
        
            // Classify each grouped file's solids, EXACTLY mirroring the backend's
            // _split_internal_face_solids bucketing (stlgen_runner.py:2191-2345):
            //   - PERFORATED floor openings / baffle patches / perforated remaining
            //     → individual perforated_floor_<san>.stl / perforated_baffle_<san>.stl
            //       → registered PER-SOLID (<san>_master/_slave).
            //   - WALL baffles (wall-type baffle models, wall floor openings, wallBaffle
            //     floor remaining-area, rack fullBlock) → consolidated into ONE
            //     merged_wall_baffle.stl → registered ONLY via merged_wall_baffle_master.
            //
            // So a wall-type baffle solid must NOT be emitted per-solid (the gold has
            // no Baffle_1_master); it only sets hasWallBaffle, which triggers the
            // unified merged_wall_baffle key below. A floor-remaining wallBaffle solid
            // (e.g. Raised_Floor_copy_part1) IS registered per-solid by the gold's
            // floor-remaining loop (:3609) AND absorbed into merged_wall_baffle — so we
            // emit it per-solid AND set hasWallBaffle.
            const groupedSolids = {}; // filename → [solidName,...]
            Object.keys(groupedMap).forEach(fn => { groupedSolids[fn] = parseSolidNames(groupedMap[fn]); });
        
            // Source files split into FLOOR vs BAFFLE families (mirroring the gold's
            // _floor_search_keys vs _baffle_source_keys). The distinction matters for
            // the DEFAULT classification of an unrecognized solid:
            //   - FLOOR files: a non-opening solid is a floor remaining-area solid,
            //     classified by the owning floor's defaultBC (wall floors → nothing;
            //     wallBaffle/perforated floors → baffle).
            //   - BAFFLE files: an unrecognized solid defaults to WALL baffle (gold
            //     _baffle_physics.get(san, {wall}) — :3581). Baffle export solid names
            //     do NOT always match meta.name (e.g. the Shinaide containment baffles
            //     export as ACU2_* / ACU3_*), so name-prefix matching is unreliable;
            //     the wall default is what reproduces the registry.
            const FLOOR_FILES = ['merged_floor_ceiling.stl', 'merged_perforated.stl'];
            const BAFFLE_FILES = [
                'merged_baffle.stl', 'merged_all_baffles.stl',
                'merged_wall_baffles.stl', 'merged_perforated_baffles.stl',
            ];
        
            let hasWallBaffle = false; // any solid the backend routes to merged_wall_baffle.stl
            const seenPorous = new Set(); // de-dupe across source files
        
            // --- FLOOR source files ---
            FLOOR_FILES.forEach(fn => {
                (groupedSolids[fn] || []).forEach(solidName => {
                    const san = sanitizeName(solidName);
                    if (seenPorous.has(san)) return;
                    // A floor-hosted ACU return is a device patch, never porous.
                    if (stlMap[solidName] && stlMap[solidName].kind === 'acu') return;
        
                    // Floor opening (explicit solidName in state).
                    if (floorOpeningInfo[san]) {
                        const info = floorOpeningInfo[san];
                        if (info.type === 'perforated' && info.perforation) {
                            putPorous(solidName, { kind: 'floor_opening', objectPath: 'floors',
                                surfaceUid: solidName, source: { modelUid: solidName },
                                perforation: info.perforation });
                        } else {
                            hasWallBaffle = true; // wallBaffle-type opening → merged_wall_baffle
                        }
                        seenPorous.add(san);
                        return;
                    }
                    // Floor remaining-area (prefix match a floor whose defaultBC is
                    // wallBaffle or perforated; 'wall' floors → Room_Floor/Ceiling,
                    // not a baffle).
                    const fd = remainingPerfFor(san, floorDefaults);
                    if (fd && (fd.defaultBC === 'wallBaffle' || fd.defaultBC === 'perforated')) {
                        if (fd.defaultBC === 'perforated' && fd.perforation) {
                            putPorous(solidName, { kind: 'floor_remaining', objectPath: 'floors',
                                surfaceUid: solidName, source: { modelUid: solidName },
                                perforation: fd.perforation });
                        } else {
                            // wallBaffle remaining-area → per-solid wall_baffle (gold :3609)
                            // AND absorbed into merged_wall_baffle.
                            putPorous(solidName, { kind: 'floor_remaining', objectPath: 'floors',
                                surfaceUid: solidName, source: { modelUid: solidName } });
                            hasWallBaffle = true;
                        }
                        seenPorous.add(san);
                        return;
                    }
                    // else: a 'wall' default floor solid (Room_Floor / Room_Ceiling) —
                    // not a baffle; handled as a room boundary, not here.
                });
            });
        
            // --- BAFFLE source files ---
            BAFFLE_FILES.forEach(fn => {
                (groupedSolids[fn] || []).forEach(solidName => {
                    const san = sanitizeName(solidName);
                    if (seenPorous.has(san)) return;
        
                    // A baffle solid may itself be a PERFORATED floor opening/baffle
                    // patch (gold classifies floor openings even from baffle files).
                    if (floorOpeningInfo[san] && floorOpeningInfo[san].type === 'perforated'
                        && floorOpeningInfo[san].perforation) {
                        putPorous(solidName, { kind: 'floor_opening', objectPath: 'floors',
                            surfaceUid: solidName, source: { modelUid: solidName },
                            perforation: floorOpeningInfo[san].perforation });
                        seenPorous.add(san);
                        return;
                    }
                    if (bafflePatchInfo[san] && bafflePatchInfo[san].baffleType === 'perforated'
                        && bafflePatchInfo[san].perforation) {
                        putPorous(solidName, { kind: 'baffle', objectPath: 'baffles',
                            surfaceUid: solidName, source: { modelUid: solidName },
                            perforation: bafflePatchInfo[san].perforation });
                        seenPorous.add(san);
                        return;
                    }
                    const bd = remainingPerfFor(san, baffleDefaults);
                    if (bd && bd.baffleType === 'perforated' && bd.perforation) {
                        putPorous(solidName, { kind: 'baffle', objectPath: 'baffles',
                            surfaceUid: solidName, source: { modelUid: solidName },
                            perforation: bd.perforation });
                        seenPorous.add(san);
                        return;
                    }
                    // DEFAULT: any other baffle-file solid → wall baffle (gold default
                    // _baffle_physics.get(san,{wall})). Absorbed into merged_wall_baffle;
                    // no per-solid key.
                    hasWallBaffle = true;
                    seenPorous.add(san);
                });
            });
        
            // 5. Rack doors. Per-rack door solids live in merged_rack_door.stl (and
            //    legacy merged_door_<N>U.stl buckets). Gold registers each door solid
            //    by its <rack>_<side>_door name with the door's perforation (default
            //    {circle,3,64}), then ALSO registers a unified merged_rack_door key.
            Object.keys(groupedSolids).forEach(fn => {
                if (fn !== 'merged_rack_door.stl' && !fn.startsWith('merged_door_')) return;
                (groupedSolids[fn] || []).forEach(solidName => {
                    const san = sanitizeName(solidName);
                    const info = doorInfo[san];
                    const entry = { kind: 'rack_door', objectPath: 'racks',
                                    surfaceUid: solidName, source: { modelUid: solidName } };
                    // perforation from state door spec, else translator's default.
                    if (info && info.perforation) entry.perforation = info.perforation;
                    putPorous(solidName, entry);
                });
            });
            // Unified merged_rack_door key (gold :3701-3714): one master/slave pair
            // keyed 'merged_rack_door', sigma = uniform door sigma (default 0.64).
            if (groupedSolids['merged_rack_door.stl'] && groupedSolids['merged_rack_door.stl'].length > 0) {
                let uniformSigma = 0.64;
                if (doorSigmas.size === 1) uniformSigma = Array.from(doorSigmas)[0];
                putPorous('merged_rack_door', {
                    kind: 'rack_door', objectPath: 'racks',
                    surfaceUid: 'merged_rack_door', source: { modelUid: 'merged_rack_door' },
                    perforation: { shape: 'circle', holeDiameterMm: 3.0, openAreaPct: uniformSigma * 100.0 },
                });
            }
        
            // 6. Unified merged_wall_baffle (gold :3720-3724): one wall_baffle pair,
            //    emitted whenever ANY solid is routed to merged_wall_baffle.stl. We
            //    OR the grouped-STL signal (hasWallBaffle, floor remaining etc.) with
            //    the state-derived signal (hasWallBaffleState, any non-perforated baffle
            //    model / wallBaffle floor) — the latter survives headless renders that
            //    emit no geometry for standalone containment baffle models.
            if (hasWallBaffle || hasWallBaffleState) {
                putPorous('merged_wall_baffle', {
                    kind: 'baffle', objectPath: 'baffles',
                    surfaceUid: 'merged_wall_baffle', source: { modelUid: 'merged_wall_baffle' },
                    // no perforation → translator emits wall_baffle master+slave.
                });
            }
        
            // 7. Blanking-panel bucket keys (gold :3728-3754). The backend buckets
            //    every rack empty/wall-slot blanking panel into one merged file per
            //    U-span: merged_perforatedBlankingPanel_<N>U.stl (typicalSmallGap →
            //    perforated, σ=0.6401) or merged_wallBlankingPanel_<N>U.stl (fullBlock
            //    → wall). Each bucket FILE registers ONE master/slave keyed by the
            //    file stem. We enumerate these spans directly from state via
            //    getRackMergedEmptySlotRanges (the exact ranges the renderer/exporter
            //    use), because the headless STL export's blanking solid names depend
            //    on the face DISPLAY name (front/back vs inlet/outlet) and so may not
            //    route through _classifyRackSolidToBucketFile — but the U-SPAN bucket
            //    (the registry key) is deterministic from the slot ranges.
            const blankingBuckets = {}; // stem → isPorous
            window.allModels().forEach(model => {
                if (model.kind !== 'rack') return;
                const isPorous = ((model.cooling && model.cooling.blankingPanelType) || 'typicalSmallGap') === 'typicalSmallGap';
                ['front', 'back'].forEach(side => {
                    (getRackMergedEmptySlotRanges(model, side) || []).forEach(range => {
                        const span = (range.endUnit - range.startUnit) + 1;
                        if (!(span > 0)) return;
                        const stem = (isPorous ? 'merged_perforatedBlankingPanel_' : 'merged_wallBlankingPanel_') + span + 'U';
                        // last writer wins is fine — isPorous is per-rack but all racks
                        // sharing a span+type collapse to one bucket (matches backend).
                        if (!(stem in blankingBuckets)) blankingBuckets[stem] = isPorous;
                    });
                });
            });
            Object.keys(blankingBuckets).forEach(stem => {
                if (blankingBuckets[stem]) {
                    putPorous(stem, {
                        kind: 'blanking_panel', objectPath: 'racks',
                        surfaceUid: stem, source: { modelUid: stem },
                        perforation: { shape: 'circle', holeDiameterMm: 1.0, openAreaPct: 64.01 },
                    });
                } else {
                    putPorous(stem, {
                        kind: 'blanking_panel', objectPath: 'racks',
                        surfaceUid: stem, source: { modelUid: stem },
                        // no perforation → wall_baffle master+slave.
                    });
                }
            });
        
            // 8. Partition-wall holes → partition_hole sourceTerms (porousZone).
            //    Gated on leakageRate < 100 (gold _build_partition_hole_fvoptions_config
            //    :1522-1595). None of the 5 baselines exercise this (all holes are
            //    leakageRate>=100), so it never affects the registry comparison —
            //    but emit it for fidelity. Geometry (origin/e1/e2) is computed here
            //    (Arch A: browser does the geometry, translator does the physics).
            window.allModels().forEach(model => {
                if (model.kind !== 'partitionWall') return;
                const q = (model.geometry && model.geometry.quaternion) || { x: 0, y: 0, z: 0, w: 1 };
                // Y-up: thickness D is along local-Z; rotate local-Z to world.
                const e1 = _t3bVecNormalize(_t3bQuatRotate([0, 0, 1], q)) || [0, 0, 1];
                const tmpAxis = Math.abs(e1[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
                let e2 = _t3bVecNormalize(_t3bVecCross(tmpAxis, e1));
                if (!e2) {
                    const fb = (tmpAxis[2] === 1) ? [1, 0, 0] : [0, 0, 1];
                    e2 = _t3bVecNormalize(_t3bVecCross(fb, e1)) || [0, 1, 0];
                }
                const depth = Math.max(0.001, Number(model.geometry.size.z) || 0.001);
                const loc = model.geometry.location || { x: 0, y: 0, z: 0 };
                const store = (typeof getPartitionWallPatchStore === 'function')
                    ? getPartitionWallPatchStore(model) : [];
                store.forEach((patch, index) => {
                    let leakageRate = 100;
                    try {
                        const phys = (cfdprojStateV4 && cfdprojStateV4.getPatchPhysics)
                            ? cfdprojStateV4.getPatchPhysics(model, patch.id) : null;
                        if (phys && phys.leakageRate != null) leakageRate = phys.leakageRate;
                    } catch (e) { /* default 100 */ }
                    if (leakageRate >= 100) return; // gated — no source term
                    const zoneName = sanitizeName(model.meta.name + '_hole_' + (index + 1) + '_zone');
                    const cellzoneName = sanitizeName('partition_hole_' + zoneName);
                    // Approx origin: partition-wall world location (the hole-zone STL
                    // centroid the backend uses isn't reconstructable without the
                    // mesh; the wall location is the closest state-only proxy).
                    const origin = [loc.x, loc.y, loc.z];
                    // Key by a synthetic solid name so putPorous can dedupe.
                    putPorous('__partition_hole__' + cellzoneName, {
                        kind: 'partition_hole', objectPath: 'partitionWalls.' + model.uid,
                        cellzoneName: cellzoneName, leakageRate: leakageRate, depth: depth,
                        origin: origin, e1: e1, e2: e2,
                        source: { modelUid: model.uid },
                    });
                });
            });
        
            return stlMap;
        }

        
        function cloneSeedConfig(seedConfig) {
            if (!seedConfig || typeof seedConfig !== 'object') return null;
            return {
                enabled: seedConfig.enabled !== false,
                anchor: toPlainVec3(seedConfig.anchor),
                direction: toPlainVec3(seedConfig.direction, { x: 0, y: 0, z: 1 }),
                offset: Number.isFinite(seedConfig.offset) ? seedConfig.offset : 0
            };
        }

        
        function cloneSeedConfigMap(seedConfigMap) {
            if (!seedConfigMap || typeof seedConfigMap !== 'object') return {};
            const next = {};
            Object.keys(seedConfigMap).forEach(key => {
                const cloned = cloneSeedConfig(seedConfigMap[key]);
                if (cloned) next[key] = cloned;
            });
            return next;
        }

            function getRackFrameSolidNames(model) {
            if (!model || model.kind !== 'rack') return [];
            var geom = model.geometry;
            var rName = sanitizeName(model.meta.name);
            var framePrefix = sanitizeName(rName + '_frame');
            // 2026-04-19 TEST: when merged, cfdrun reports a single frame solid
            // name per rack to match the unified `solid` block emitted by
            // buildRackFrameGeometry / exportBoxModel.
            if (window.MERGE_RACK_FRAME_SOLIDS) {
                return [framePrefix];
            }
            var slotWidth = geom.size.x * geom.slotWidthRatio;
            var slotX = (geom.size.x - slotWidth) / 2;
            var slotAreaHeight = geom.slot * geom.slotHeight;
            var marginBottom = model.safeMarginBottom || 0;
            var marginTop = model.safeMarginTop || 0;
            var inletOff = model.inletOffset || 0;
            var outletOff = model.outletOffset || 0;
            var rightFrameW = model.geometry.size.x - slotX - slotWidth;
        
            var names = [];
            var push = function(sub) { names.push(sanitizeName(framePrefix + '_' + sub)); };
        
            push('outer_left');
            push('outer_right');
            push('outer_top');
            push('outer_bottom');
        
            ['front', 'back'].forEach(function(side) {
                if (marginBottom > 1e-6) push(side + '_margin_bottom');
                if (marginTop > 1e-6) push(side + '_margin_top');
                if (slotX > 1e-6 && slotAreaHeight > 1e-6) push(side + '_strip_left');
                if (rightFrameW > 1e-6 && slotAreaHeight > 1e-6) push(side + '_strip_right');
            });
        
            var effectiveSlotDepth = (model.geometry.size.z - outletOff) - inletOff;
            if (slotAreaHeight > 1e-6 && effectiveSlotDepth > 1e-6) {
                push('inner_left');
                push('inner_right');
            }
            if (slotWidth > 1e-6) {
                if (marginBottom > 1e-6) push('inner_floor');
                if (marginTop > 1e-6) push('inner_ceiling');
            }
            if (inletOff > 1e-6 && slotAreaHeight > 1e-6) {
                push('inlet_cavity_left');
                push('inlet_cavity_right');
            }
            if (outletOff > 1e-6 && slotAreaHeight > 1e-6) {
                push('outlet_cavity_left');
                push('outlet_cavity_right');
            }
        
            return names;
        }

            function getRackServerPhysics(model, equipmentId, create) {
            const servers = _rackServersDict(model);
            const srv = servers ? servers[equipmentId] : null;
            if (!srv) return create ? {} : {};
            if (!srv.cooling) { if (create) srv.cooling = {}; else return {}; }
            return srv.cooling || {};
        }

        
        function inferRoomHeightFromWalls() {
            // Priority 1: use explicit floor semantic binding for room ceiling.
            const topBoundaryFloors = window.allModels().filter(m =>
                m && m.kind === 'floor'
                && m.cooling && m.cooling.roomBoundaryLocation === 'top'
                && m.geometry.location && Number.isFinite(m.geometry.location.y)
            );
            if (topBoundaryFloors.length > 0) {
                return Math.max(...topBoundaryFloors.map(m => m.geometry.location.y));
            }
            // Priority 2: use explicit roomPolygon height from 2D editor
            if (ctx.state.geometry.plane) {
                const planeH = ctx.state.geometry.planeHeight != null ? ctx.state.geometry.planeHeight : ctx.state.geometry.height;
                if (Number.isFinite(planeH) && planeH > 0) {
                    return planeH;
                }
            }
            // Priority 3: infer from model heights
            const bbox = _computeAllModelsBBox();
            if (!bbox) return ctx.state.geometry.height;
            return bbox.zMax;
        }

        
        function inferRoomPolygonFromWalls() {
            // Priority 1: use explicit roomPolygon from 2D editor
            if (ctx.state.geometry.plane && ctx.state.geometry.plane.length >= 3) {
                return ctx.state.geometry.plane.map(v => ({ x: v[0], y: v[1], z: 0 }));
            }
            // Priority 2: infer from all models' AABB
            const bbox = _computeAllModelsBBox();
            if (!bbox) return null;
            return [
                { x: bbox.xMin, y: bbox.yMin, z: 0 },
                { x: bbox.xMax, y: bbox.yMin, z: 0 },
                { x: bbox.xMax, y: bbox.yMax, z: 0 },
                { x: bbox.xMin, y: bbox.yMax, z: 0 }
            ];
        }

        
        function quaternionToOrientationDeg(q) {
            const angle = 2 * Math.atan2(q.z, q.w) * 180 / Math.PI;
            return ((angle % 360) + 360) % 360;
        }

        
        function toPlainVec3(value, fallback = { x: 0, y: 0, z: 0 }) {
            return {
                x: Number.isFinite(value?.x) ? value.x : fallback.x,
                y: Number.isFinite(value?.y) ? value.y : fallback.y,
                z: Number.isFinite(value?.z) ? value.z : fallback.z
            };
        }

        return {
            _autoComputeBlockMeshBounds: _autoComputeBlockMeshBounds,
            _buildServerFacesExport: _buildServerFacesExport,
            _computeAllModelsBBox: _computeAllModelsBBox,
            _getBMBounds: _getBMBounds,
            _resolveMeshMode: _resolveMeshMode,
            _t3bQuatRotate: _t3bQuatRotate,
            _t3bVecCross: _t3bVecCross,
            _t3bVecNormalize: _t3bVecNormalize,
            buildCfdrunJson: buildCfdrunJson,
            buildComprehensiveStlMap: buildComprehensiveStlMap,
            cloneSeedConfig: cloneSeedConfig,
            cloneSeedConfigMap: cloneSeedConfigMap,
            getRackFrameSolidNames: getRackFrameSolidNames,
            getRackServerPhysics: getRackServerPhysics,
            inferRoomHeightFromWalls: inferRoomHeightFromWalls,
            inferRoomPolygonFromWalls: inferRoomPolygonFromWalls,
            quaternionToOrientationDeg: quaternionToOrientationDeg,
            toPlainVec3: toPlainVec3
        };
    }

    return { create: createCfdrunBuild };
});
