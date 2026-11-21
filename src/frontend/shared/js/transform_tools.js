/**
 * Moving a model: bounding boxes, collision, snapping.
 *
 * Extracted from STL_generate.html so the React editor and the legacy page run
 * the SAME arithmetic. This is not cosmetic code — a snap that lands a rack
 * 3 mm from where the other app would put it changes the geometry that gets
 * meshed, and the difference is invisible on screen.
 *
 * Two halves:
 *
 *   PURE          getModelOBB / getOBBCorners / obbOverlapSAT / hasModelRotation
 *                 depend on nothing but the model and THREE.
 *   createTransformTools(ctx)
 *                 the rest needs the BUILT scene — world bounds come from the
 *                 rendered meshes, not from the project — plus the project's
 *                 own bounds and the view's grid spacing. The snap cache is per
 *                 instance rather than module-global, so two editors on one
 *                 page cannot poison each other's grid.
 *
 * ctx: {
 *   state()                     the project (for geometry.bounds)
 *   uiState()                   view settings (for gridSpacing)
 *   allModels()                 every model, flat
 *   renderRoots()               the built per-model groups
 *   getRenderRootWorldBounds(root) -> THREE.Box3 | null
 *   getRenderRootByModelId(id)  -> Object3D | null
 *   isModelEnabled(modelOrId)   -> boolean
 *   getModelById(id)            -> model | null
 *   snapPixelWorld()            -> metres per screen pixel at the selection
 *                                  (optional; makes the snap radius a fixed
 *                                  10 px instead of 12% of the model size)
 * }
 *
 * Snapping (2026-09-22 rewrite) works on the NOMINAL boxes — location + size
 * + rotation, the geometry the STL export writes — never on rendered bounds,
 * and quantises every coordinate it writes to a micrometre. See getModelAABB,
 * quantize, findModelGaps.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.transformTools = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    function _THREE() {
        var T = (typeof window !== 'undefined') ? window.THREE : null;
        if (!T) throw new Error('transform_tools.js needs THREE loaded first');
        return T;
    }

    // Baffles and floors are SHEETS that legitimately intersect what they
    // separate — a containment baffle sits between racks, a raised floor runs
    // under them. Collision-testing them would make the room unbuildable.
    var COLLISION_EXEMPT_KINDS = ['baffle', 'floor'];

    function isCollisionExempt(kind) {
        return COLLISION_EXEMPT_KINDS.indexOf(kind) >= 0;
    }

    function hasModelRotation(model) {
        var q = model && model.geometry && model.geometry.quaternion;
        return !!(q && (Math.abs(q.x) > 1e-6 || Math.abs(q.y) > 1e-6 || Math.abs(q.z) > 1e-6));
    }

    // Every stored coordinate is a whole number of micrometres. Two faces
    // that are meant to coincide then carry the SAME double, and the STL
    // writer's toFixed(6) prints the same string for both — the watertight
    // seam the mesher needs. A snap that adds a delta to a raw double leaves
    // float dust (3.5999999999999996) that the export turns into a 1e-16 gap
    // and the weld has to close; quantising on every write removes it.
    var COORD_QUANTUM = 1e-6;
    function quantize(v) {
        return Math.round(v * 1e6) / 1e6;
    }

    function _hasMirror(model) {
        var s = model && model.geometry && model.geometry.scale;
        return !!(s && ((s.x || 1) < 0 || (s.y || 1) < 0 || (s.z || 1) < 0));
    }

    /**
     * The model's NOMINAL world-space box: location + size, rotated by its
     * quaternion. This is the box the STL export writes, so it is the only box
     * a snap may align — the rendered meshes carry doors, hole proxies and slot
     * lines that float 1 mm off the body, and snapping those flush leaves the
     * bodies 1-2 mm apart (the "tiny gap" the mesher's weld then has to seal).
     *
     * Unrotated, unmirrored models take the exact path: min = location and
     * max = location + size, no centre round-trip, so a face coordinate
     * computed here equals the one the exporter computes.
     */
    function getModelAABB(model) {
        var THREE = _THREE();
        var g = model && model.geometry;
        if (!g || !g.size || !g.location) return null;
        if (!hasModelRotation(model) && !_hasMirror(model)) {
            var s = g.scale || {};
            var w = (g.size.x || 0) * (s.x || 1);
            var h = (g.size.y || 0) * (s.y || 1);
            var d = (g.size.z || 0) * (s.z || 1);
            return new THREE.Box3(
                new THREE.Vector3(g.location.x, g.location.y, g.location.z),
                new THREE.Vector3(g.location.x + w, g.location.y + h, g.location.z + d)
            );
        }
        return new THREE.Box3().setFromPoints(getOBBCorners(getModelOBB(model)));
    }

    function _axisOverlap(aMin, aMax, bMin, bMax) {
        return Math.min(aMax, bMax) - Math.max(aMin, bMin);
    }

    /**
     * Pairs of models whose nominal boxes face each other across a gap of at
     * most `maxGap` metres (and more than zero) on one axis while overlapping
     * on the other two — a rack 1 mm from its neighbour, a baffle 0.5 mm off a
     * wall. These are the gaps a drag left behind and the mesher would either
     * weld shut or turn into a sliver channel. Sorted smallest gap first.
     */
    function findModelGaps(models, maxGap) {
        var limit = (maxGap == null) ? 0.005 : maxGap;
        // Below one quantum a "gap" is float dust between micrometre values
        // (1.234567 + 0.6 vs 1.834567) or a sub-micrometre residue no move
        // can change: the STL writer's toFixed(6) prints both faces the same
        // (or one micrometre apart, which the mesher's weld closes), so it
        // is not a seam and not something the closer could act on.
        var floor = COORD_QUANTUM;
        var boxes = [];
        (models || []).forEach(function (m) {
            var b = getModelAABB(m);
            if (b) boxes.push({ model: m, box: b });
        });
        var axes = ['x', 'y', 'z'];
        var gaps = [];
        for (var i = 0; i < boxes.length; i++) {
            for (var j = i + 1; j < boxes.length; j++) {
                var A = boxes[i].box, B = boxes[j].box;
                for (var k = 0; k < 3; k++) {
                    var ax = axes[k];
                    var gap = Math.max(B.min[ax] - A.max[ax], A.min[ax] - B.max[ax]);
                    if (!(gap > floor && gap <= limit)) continue;
                    var o1 = axes[(k + 1) % 3], o2 = axes[(k + 2) % 3];
                    if (_axisOverlap(A.min[o1], A.max[o1], B.min[o1], B.max[o1]) <= 1e-6) continue;
                    if (_axisOverlap(A.min[o2], A.max[o2], B.min[o2], B.max[o2]) <= 1e-6) continue;
                    gaps.push({
                        a: boxes[i].model.uid, b: boxes[j].model.uid,
                        axis: ax, gap: gap,
                        // +1: b lies on the positive side of a
                        direction: (B.min[ax] - A.max[ax] > 0) ? 1 : -1
                    });
                }
            }
        }
        gaps.sort(function (p, q) { return p.gap - q.gap; });
        return gaps;
    }

    /**
     * Close the gaps `findModelGaps` reports.
     *
     * In rounds, the smaller model of each pair moves onto the larger one's
     * face (ties: the later model moves); a model moves once per round, and a
     * baffle facing a whole row closes every seam in one move. Float dust is
     * not a gap (see findModelGaps) and nothing else is touched: a project the
     * audit reports clean is left byte-identical. Returns the moves made.
     */
    function closeModelGaps(models, maxGap) {
        var byUid = {};
        (models || []).forEach(function (m) { byUid[m.uid] = m; });
        var moves = [];
        for (var round = 0; round < 8; round++) {
            var gaps = findModelGaps(models, maxGap);
            if (!gaps.length) break;
            var moved = {};
            var n = 0;
            gaps.forEach(function (g) {
                var a = byUid[g.a], b = byUid[g.b];
                if (!a || !b || moved[g.a] || moved[g.b]) return;
                var va = _volumeOf(a), vb = _volumeOf(b);
                var mover = (vb < va) ? b : (va < vb ? a : b);
                var sign = (mover === b) ? -g.direction : g.direction;
                var loc = mover.geometry.location;
                var before = loc[g.axis];
                var to = quantize(before + sign * g.gap);
                if (to === before) return;   // no progress possible at this quantum
                loc[g.axis] = to;
                moved[mover.uid] = true;
                n++;
                moves.push({ uid: mover.uid, axis: g.axis, from: before, to: loc[g.axis], gap: g.gap, against: (mover === b) ? g.a : g.b });
            });
            if (!n) break;
        }
        return moves;
    }

    function _volumeOf(model) {
        var s = (model.geometry && model.geometry.size) || {};
        return Math.abs((s.x || 0) * (s.y || 0) * (s.z || 0));
    }

    /**
     * The model's oriented bounding box, from its anchor, size and rotation.
     *
     * `location` is the MIN-CORNER anchor, not the centre, so the centre is the
     * half-size rotated and added to it. Treating the anchor as the centre puts
     * every rotated model half a body away from where it renders.
     *
     * Y-up: size.x is width (X), size.y is height (Y), size.z is depth (Z).
     */
    function getModelOBB(model) {
        var THREE = _THREE();
        var t = model.geometry || {};
        var q = t.quaternion || { x: 0, y: 0, z: 0, w: 1 };
        var s = t.scale || { x: 1, y: 1, z: 1 };
        var quat = new THREE.Quaternion(q.x, q.y, q.z, q.w).normalize();
        var sx = s.x || 1, sy = s.y || 1, sz = s.z || 1;
        var halfW = t.size.x / 2, halfD = t.size.z / 2, halfH = t.size.y / 2;
        var localCenter = new THREE.Vector3(halfW * sx, halfH * sy, halfD * sz);
        var anchor = new THREE.Vector3(t.location.x, t.location.y, t.location.z);
        var center = localCenter.applyQuaternion(quat).add(anchor);
        // The axes carry the scale's SIGN: a mirrored model has a negative
        // scale, and normalising away the sign would flip its box back.
        var axisX = new THREE.Vector3(sx, 0, 0).applyQuaternion(quat).normalize();
        var axisY = new THREE.Vector3(0, sy, 0).applyQuaternion(quat).normalize();
        var axisZ = new THREE.Vector3(0, 0, sz).applyQuaternion(quat).normalize();
        return {
            center: center,
            half: [halfW * Math.abs(sx), halfH * Math.abs(sy), halfD * Math.abs(sz)],
            axes: [axisX, axisY, axisZ]
        };
    }

    /** The eight world-space corners of an OBB. */
    function getOBBCorners(obb) {
        var corners = [];
        for (var sx = -1; sx <= 1; sx += 2) {
            for (var sy = -1; sy <= 1; sy += 2) {
                for (var sz = -1; sz <= 1; sz += 2) {
                    corners.push(obb.center.clone()
                        .addScaledVector(obb.axes[0], sx * obb.half[0])
                        .addScaledVector(obb.axes[1], sy * obb.half[1])
                        .addScaledVector(obb.axes[2], sz * obb.half[2]));
                }
            }
        }
        return corners;
    }

    /**
     * Separating-axis test between two oriented boxes.
     *
     * The nine cross-product axes matter as much as the six face axes: two
     * boxes rotated 45 degrees to each other can be apart along every face
     * normal and still overlap through an edge.
     */
    function obbOverlapSAT(a, b, eps) {
        var THREE = _THREE();
        function projectOBB(obb, axis) {
            return Math.abs(obb.axes[0].dot(axis)) * obb.half[0]
                 + Math.abs(obb.axes[1].dot(axis)) * obb.half[1]
                 + Math.abs(obb.axes[2].dot(axis)) * obb.half[2];
        }
        var d = b.center.clone().sub(a.center);
        var testAxes = [a.axes[0], a.axes[1], a.axes[2], b.axes[0], b.axes[1], b.axes[2]];
        for (var i = 0; i < 3; i++) {
            for (var j = 0; j < 3; j++) {
                var cross = new THREE.Vector3().crossVectors(a.axes[i], b.axes[j]);
                if (cross.lengthSq() > 1e-10) testAxes.push(cross.normalize());
            }
        }
        for (var k = 0; k < testAxes.length; k++) {
            var axis = testAxes[k];
            var overlap = projectOBB(a, axis) + projectOBB(b, axis) - Math.abs(d.dot(axis));
            if (overlap <= eps) return false;
        }
        return true;
    }

    var _SNAP_CELL_SIZE = 2.0;
    // Zero, as in the legacy page: every pointermove recomputes. The throttle
    // hook is kept because the spatial grid is what makes that affordable, and
    // a non-zero value here would silently make the two apps snap differently.
    var _SNAP_THROTTLE_MS = 0;

    function createTransformTools(ctx) {
        var THREE = _THREE();
        // modelId -> Box3 | null. The snap path asks for a model's world bounds
        // on every pointermove, and recomputing them means walking every vertex
        // of a 40-part rack each time.
        var _bboxCache = new Map();
        var _snapGrid = null;
        var _snapAvgSize = null;
        var _snapThreshold = null;
        var _lastSnapResult = { x: 0, y: 0, z: 0 };
        var _lastSnapTime = 0;
        var _scratch = new THREE.Vector3();

        function eachRoot(fn) {
            (ctx.renderRoots() || []).forEach(fn);
        }

        /** Drop cached bounds — for one model, or for all when called bare. */
        function invalidateBounds(modelId) {
            if (modelId) _bboxCache.delete(modelId);
            else _bboxCache.clear();
        }

        /**
         * A model group's world-space bounding box.
         *
         * Computed from the BUILT meshes rather than from the project's size,
         * because a rack's real extent includes its doors, rails and equipment
         * — snapping to the nominal box would leave a visible gap.
         *
         * Anchor markers and slot guides are skipped: they are UI decoration
         * that extends past the body, and including them would snap the model
         * to where its handle is rather than where its surface is.
         */
        function getRenderRootWorldBounds(root) {
            if (!root) return null;
            var modelId = root.userData && root.userData.modelId;
            if (modelId && _bboxCache.has(modelId)) return _bboxCache.get(modelId);

            root.updateMatrixWorld(true);
            var bounds = new THREE.Box3();
            root.traverse(function (child) {
                if (!child.geometry) return;
                var ud = child.userData || {};
                if (ud.isAnchorMarker || ud.isRackSlotGuide) return;
                // Doors, hole proxies and slot lines float 1 mm off the body
                // to dodge z-fighting; they are not surface.
                if (ud.isOverlay || ud.isDoor || ud.isPartitionWallHoleProxy || ud.isSlotBoundaryGuide || child.isLine) return;
                if (!child.visible && child !== root) return;
                child.updateWorldMatrix(false, false);
                var childBounds = new THREE.Box3()
                    .setFromBufferAttribute(child.geometry.attributes.position);
                childBounds.applyMatrix4(child.matrixWorld);
                bounds.union(childBounds);
            });
            var result = bounds.isEmpty() ? null : bounds;
            if (modelId) _bboxCache.set(modelId, result);
            return result;
        }

        /**
         * Build the proximity-snap acceleration structure for one drag.
         *
         * A 2D hash over the floor plan, not 3D: a data centre is wide and
         * flat, so the vertical axis separates almost nothing and a third
         * dimension would cost memory for no pruning. The threshold is 12% of
         * the average model size, which keeps the snap distance meaningful in
         * both a 4 m lab and a 40 m hall.
         */
        // Snap locks: per axis, the target coordinate the moving box is held
        // on and which of its faces is held there. A lock engages inside the
        // threshold and releases only at 2.5x it (Schmitt trigger), so a hand
        // resting on the boundary does not toggle the snap frame by frame and a
        // dense row of racks does not flip the box between neighbours.
        var _snapLocks = { x: null, y: null, z: null };
        var _SNAP_PIXELS = 10;        // engage radius on screen
        var _SNAP_MIN_M = 0.005;      // never below 5 mm ...
        var _SNAP_MAX_M = 0.5;        // ... nor above half a metre
        var _SNAP_RELEASE = 2.5;

        /**
         * The engage distance in metres. Screen-space when the host supplies
         * `ctx.snapPixelWorld()` (metres per pixel at the selection): a snap
         * that always reaches ten pixels feels the same zoomed in on a baffle
         * as zoomed out on a hall. Without it, 12% of the average model size,
         * the legacy rule.
         */
        function currentSnapThreshold() {
            if (ctx.snapPixelWorld) {
                var mpp = ctx.snapPixelWorld();
                if (isFinite(mpp) && mpp > 0) {
                    return Math.min(_SNAP_MAX_M, Math.max(_SNAP_MIN_M, mpp * _SNAP_PIXELS));
                }
            }
            if (_snapThreshold != null) return _snapThreshold;
            return _avgModelSize() * 0.12;
        }

        function _avgModelSize() {
            var allSizes = [];
            (ctx.allModels() || []).forEach(function (m) {
                if (!ctx.isModelEnabled(m)) return;
                var wb = getModelAABB(m);
                if (wb) {
                    var sz = wb.getSize(_scratch);
                    allSizes.push(Math.max(sz.x, sz.y, sz.z));
                }
            });
            return allSizes.length
                ? allSizes.reduce(function (a, b) { return a + b; }, 0) / allSizes.length
                : 1;
        }

        /**
         * Build the proximity-snap acceleration structure for one drag.
         *
         * A 2D hash over the floor plan (X/Z), not 3D: a data centre is wide
         * and flat, so the vertical axis separates almost nothing. The boxes
         * hashed are the NOMINAL ones (see getModelAABB): the snap aligns the
         * geometry that gets exported, never a door plane floating off it.
         */
        function initSnapCache() {
            _snapAvgSize = _avgModelSize();
            _snapThreshold = _snapAvgSize * 0.12;
            _snapLocks = { x: null, y: null, z: null };

            _snapGrid = new Map();
            (ctx.allModels() || []).forEach(function (m) {
                var mid = m && m.uid;
                if (!mid || !ctx.isModelEnabled(m)) return;
                var wb = getModelAABB(m);
                if (!wb) return;
                var minCx = Math.floor(wb.min.x / _SNAP_CELL_SIZE);
                var minCy = Math.floor(wb.min.z / _SNAP_CELL_SIZE);
                var maxCx = Math.floor(wb.max.x / _SNAP_CELL_SIZE);
                var maxCy = Math.floor(wb.max.z / _SNAP_CELL_SIZE);
                for (var cx = minCx; cx <= maxCx; cx++) {
                    for (var cy = minCy; cy <= maxCy; cy++) {
                        var key = cx + ',' + cy;
                        if (!_snapGrid.has(key)) _snapGrid.set(key, []);
                        _snapGrid.get(key).push({ id: mid, bounds: wb });
                    }
                }
            });
        }

        function clearSnapCache() {
            _snapAvgSize = null;
            _snapThreshold = null;
            _snapGrid = null;
            _snapLocks = { x: null, y: null, z: null };
        }

        function querySnapCandidates(box, margin) {
            if (!_snapGrid) return [];
            var minCx = Math.floor((box.min.x - margin) / _SNAP_CELL_SIZE);
            var minCy = Math.floor((box.min.z - margin) / _SNAP_CELL_SIZE);
            var maxCx = Math.floor((box.max.x + margin) / _SNAP_CELL_SIZE);
            var maxCy = Math.floor((box.max.z + margin) / _SNAP_CELL_SIZE);
            var seen = new Set();
            var results = [];
            for (var cx = minCx; cx <= maxCx; cx++) {
                for (var cy = minCy; cy <= maxCy; cy++) {
                    var entries = _snapGrid.get(cx + ',' + cy);
                    if (!entries) continue;
                    for (var i = 0; i < entries.length; i++) {
                        if (!seen.has(entries[i].id)) {
                            seen.add(entries[i].id);
                            results.push(entries[i]);
                        }
                    }
                }
            }
            return results;
        }

        /**
         * How far to nudge the moving models to align with a neighbour.
         *
         * Face snapping aligns SURFACES that already overlap on the other two
         * axes — two racks side by side — while edge snapping aligns extents
         * whether or not they overlap, which is what lines a row up. Both are
         * capped at the same threshold and the nearest wins per axis.
         *
         * The spatial hash is what keeps this affordable on every pointermove;
         * the throttle is left at zero so the snap follows the cursor exactly.
         */
        function computeProximitySnap(movingIds, includeFace, includeEdge) {
            var now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
            if (now - _lastSnapTime < _SNAP_THROTTLE_MS) return _lastSnapResult;

            var movingIdSet = new Set(movingIds);
            var combined = null;
            movingIds.forEach(function (id) {
                var wb = getModelAABB(ctx.getModelById(id));
                if (!wb) return;
                combined = combined ? combined.union(wb) : wb.clone();
            });
            if (!combined) return { x: 0, y: 0, z: 0 };

            var threshold = currentSnapThreshold();
            // best[axis] = { dist, target, side } — the nearest candidate face
            // per axis and which face of the moving box it would hold.
            var best = { x: null, y: null, z: null };
            function offer(axis, dist, target, side) {
                if (dist >= threshold) return;
                var b = best[axis];
                if (!b || dist < b.dist) best[axis] = { dist: dist, target: target, side: side };
            }
            var candidates = _snapGrid ? querySnapCandidates(combined, threshold) : null;

            function overlaps(aMin, aMax, bMin, bMax) {
                return aMax > bMin + 1e-6 && bMax > aMin + 1e-6;
            }

            function processEntry(otherId, ob, otherModel) {
                if (!otherId || movingIdSet.has(otherId) || !ctx.isModelEnabled(otherId)) return;
                if (!ob) return;
                var o = { min: ob.min, max: ob.max };
                var cmin = combined.min, cmax = combined.max;
                if (includeFace) {
                    // Face-to-face: the boxes already overlap on the other two axes.
                    if (overlaps(cmin.y, cmax.y, o.min.y, o.max.y) && overlaps(cmin.z, cmax.z, o.min.z, o.max.z)) {
                        offer('x', Math.abs(cmax.x - o.min.x), o.min.x, 'max');
                        offer('x', Math.abs(cmin.x - o.max.x), o.max.x, 'min');
                    }
                    if (overlaps(cmin.x, cmax.x, o.min.x, o.max.x) && overlaps(cmin.z, cmax.z, o.min.z, o.max.z)) {
                        offer('y', Math.abs(cmax.y - o.min.y), o.min.y, 'max');
                        offer('y', Math.abs(cmin.y - o.max.y), o.max.y, 'min');
                    }
                    if (overlaps(cmin.x, cmax.x, o.min.x, o.max.x) && overlaps(cmin.y, cmax.y, o.min.y, o.max.y)) {
                        offer('z', Math.abs(cmax.z - o.min.z), o.min.z, 'max');
                        offer('z', Math.abs(cmin.z - o.max.z), o.max.z, 'min');
                    }
                }
                if (includeEdge) {
                    // Extent-to-extent, whether or not the boxes overlap: what lines a row up.
                    ['x', 'y', 'z'].forEach(function (ax) {
                        offer(ax, Math.abs(cmin[ax] - o.min[ax]), o.min[ax], 'min');
                        offer(ax, Math.abs(cmax[ax] - o.max[ax]), o.max[ax], 'max');
                        if (ax === 'y') return;
                        offer(ax, Math.abs(cmin[ax] - o.max[ax]), o.max[ax], 'min');
                        offer(ax, Math.abs(cmax[ax] - o.min[ax]), o.min[ax], 'max');
                    });
                }
            }

            if (candidates) {
                for (var i = 0; i < candidates.length; i++) {
                    processEntry(candidates[i].id, candidates[i].bounds, ctx.getModelById(candidates[i].id));
                }
            } else {
                (ctx.allModels() || []).forEach(function (m) {
                    processEntry(m && m.uid, getModelAABB(m), m);
                });
            }

            // Hysteresis: a held target survives until the RAW box (where the
            // pointer put it) is 2.5 thresholds away; only then may a nearer
            // candidate take over.
            var result = { x: 0, y: 0, z: 0 };
            ['x', 'y', 'z'].forEach(function (ax) {
                var lock = _snapLocks[ax];
                if (lock) {
                    var face = lock.side === 'min' ? combined.min[ax] : combined.max[ax];
                    if (Math.abs(face - lock.target) < threshold * _SNAP_RELEASE) {
                        result[ax] = lock.target - face;
                        return;
                    }
                    _snapLocks[ax] = null;
                }
                var b = best[ax];
                if (!b) return;
                var f = b.side === 'min' ? combined.min[ax] : combined.max[ax];
                result[ax] = b.target - f;
                _snapLocks[ax] = { target: b.target, side: b.side };
            });

            _lastSnapResult = result;
            _lastSnapTime = now;
            return _lastSnapResult;
        }

        /** The faces currently held by a snap lock, for guide lines. */
        function snapLocks() {
            return { x: _snapLocks.x, y: _snapLocks.y, z: _snapLocks.z };
        }

        /**
         * Snap the primary model's ANCHOR to the nearest grid intersection.
         *
         * The anchor rather than the bounding-box minimum, so the model origin
         * lands on a drawn grid line — which is what the user is aiming at. X
         * and Z only: the floor plan is the XOZ plane and Y stays free, or
         * every vertical nudge would jump a whole grid step.
         */
        function computeGridSnap(movingIds) {
            var view = ctx.uiState() && ctx.uiState().view;
            var spacing = (view && isFinite(view.gridSpacing)) ? view.gridSpacing : 0.1;
            if (spacing < 1e-6 || !movingIds.length) return { x: 0, y: 0, z: 0 };
            var primary = ctx.getModelById(movingIds[0]);
            if (!primary) return { x: 0, y: 0, z: 0 };
            function snapAxis(v) { return Math.round(v / spacing) * spacing - v; }
            return {
                x: snapAxis(primary.geometry.location.x),
                y: 0,
                z: snapAxis(primary.geometry.location.z)
            };
        }

        /** Would the moving models overlap anything they are not allowed to? */
        function analyticalCollisionCheck(movingModels, movingIds) {
            var movingIdSet = new Set(movingIds);
            var eps = 1e-4;
            var nonExempt = movingModels.filter(function (m) { return !isCollisionExempt(m.kind); });
            if (!nonExempt.length) return false;
            var movingOBBs = nonExempt.map(getModelOBB);
            // Snapshotted once: allModels() rebuilds its array on every call,
            // and reading it inside the loop made this O(N^2) per pointermove.
            var all = ctx.allModels();
            for (var i = 0; i < all.length; i++) {
                var other = all[i];
                if (movingIdSet.has(other.uid) || !ctx.isModelEnabled(other)) continue;
                if (isCollisionExempt(other.kind)) continue;
                var otherOBB = getModelOBB(other);
                for (var j = 0; j < movingOBBs.length; j++) {
                    if (obbOverlapSAT(movingOBBs[j], otherOBB, eps)) return true;
                }
            }
            return false;
        }

        function isModelWithinProjectBounds(modelId) {
            var b = ctx.state().geometry.bounds;
            var keys = ['xMin', 'xMax', 'yMin', 'yMax', 'zMin', 'zMax'];
            var anyEnabled = keys.some(function (k) { return b[k].enabled; });
            if (!anyEnabled) return true;
            // The nominal box, like clampModelToProjectBounds: the rendered
            // one carries 1 mm door planes and would hold a rack 1 mm off the
            // wall it is meant to touch.
            var wb = getModelAABB(ctx.getModelById(modelId));
            if (!wb) return true;
            var tol = 1e-5;
            if (b.xMin.enabled && wb.min.x < b.xMin.value - tol) return false;
            if (b.xMax.enabled && wb.max.x > b.xMax.value + tol) return false;
            if (b.yMin.enabled && wb.min.y < b.yMin.value - tol) return false;
            if (b.yMax.enabled && wb.max.y > b.yMax.value + tol) return false;
            if (b.zMin.enabled && wb.min.z < b.zMin.value - tol) return false;
            if (b.zMax.enabled && wb.max.z > b.zMax.value + tol) return false;
            return true;
        }

        function areModelsWithinProjectBounds(modelIds) {
            return (modelIds || []).every(isModelWithinProjectBounds);
        }

        return {
            getRenderRootWorldBounds: getRenderRootWorldBounds,
            invalidateBounds: invalidateBounds,
            // Exposed for the resize drag, which snaps ONE edge rather than a
            // whole body and so needs the threshold and the candidate query
            // without the full body-snap result.
            snapThreshold: function () { return (_snapGrid || ctx.snapPixelWorld) ? currentSnapThreshold() : _snapThreshold; },
            snapLocks: snapLocks,
            getModelAABB: getModelAABB,
            querySnapCandidates: querySnapCandidates,
            initSnapCache: initSnapCache,
            clearSnapCache: clearSnapCache,
            computeProximitySnap: computeProximitySnap,
            computeGridSnap: computeGridSnap,
            analyticalCollisionCheck: analyticalCollisionCheck,
            isModelWithinProjectBounds: isModelWithinProjectBounds,
            areModelsWithinProjectBounds: areModelsWithinProjectBounds
        };
    }

    return {
        COLLISION_EXEMPT_KINDS: COLLISION_EXEMPT_KINDS,
        isCollisionExempt: isCollisionExempt,
        hasModelRotation: hasModelRotation,
        getModelOBB: getModelOBB,
        getOBBCorners: getOBBCorners,
        obbOverlapSAT: obbOverlapSAT,
        getModelAABB: getModelAABB,
        quantize: quantize,
        COORD_QUANTUM: COORD_QUANTUM,
        findModelGaps: findModelGaps,
        closeModelGaps: closeModelGaps,
        createTransformTools: createTransformTools
    };
});
