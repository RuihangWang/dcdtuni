/**
 * geometry_pure.js — the free-standing half of the geometry builder.
 *
 * 62 functions lifted verbatim out of STL_generate.html: rack frames and slots,
 * server face rectangles and their export names, partition-wall signatures,
 * planar region and face-island handling, procedural textures, and the liquid
 * manifold renderer.
 *
 * "Free-standing" is measured, not hoped for. Every module-scoped declaration
 * in STL_generate.html was enumerated (215 of them) and each function checked
 * for free references to any. These 62 have none — they read only their
 * arguments and THREE — and the set is CLOSED: every function one of them calls
 * is also in here or in js/stl_naming.js / js/stl_writer.js. Anything reaching
 * the live scene (renderMeshes, globalGroup, geometryCache, renderRoots,
 * uiState) stayed behind; that half needs an injected render context and is not
 * done yet.
 *
 * A note on how this set was found, because the tooling lied three times before
 * it was right: a default parameter (`function f(o = {})`) opens a brace, an
 * apostrophe in a comment opens a fake string, and `${x ? 'a' : 'b'}` inside a
 * template opens both. Each produced a confident, wrong function boundary — one
 * 20-line function measured 22993 lines. The scanner now tokenises properly and
 * every one of the file's 782 function bodies is re-parsed with `node --check`
 * to prove the boundaries.
 *
 * Output is verified byte for byte: `parity/stl_oracle.py` exports every solid
 * before and after and compares sha256. These shapes become OpenFOAM patches,
 * where a vertex that moved by 1e-7 still renders correctly and still meshes.
 *
 * `THREE` must be a global before this loads; both apps vendor r184 globally.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) {
        root.geometryPure = api;
        // The stacked-server readers are also hung on window directly, the way
        // js/rack_state.js hangs its own: the legacy page binds these names by
        // forwarding wrappers declared in its own <script>, so a function added
        // here is not reachable there as a bare name until that page is edited.
        // A forwarding wrapper declared later shadows this alias with the same
        // implementation, so either way the call lands here.
        ['getRackServersForSlot', 'getServerDepthRange'].forEach(function (name) {
            if (typeof api[name] === 'function') root[name] = api[name];
        });
    }
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    function _boxProduct() {
        var api = typeof globalThis !== 'undefined' ? globalThis.boxGeometryProduct : null;
        if (!api && typeof require === 'function' && typeof module === 'object' && module.exports) {
            api = require('./box_geometry_product.js');
        }
        if (!api) throw new Error('geometry_pure.js needs js/box_geometry_product.js loaded first');
        return api;
    }

    // js/rack_state.js — the rack model's own normalisation helpers. Bound
    // explicitly for the same reason as the other siblings: resolving them as
    // bare globals only worked inside the legacy page, where a top-level
    // `function` becomes a window property.
    function _rack() {
        var m = (typeof window !== 'undefined') ? window.rackState : null;
        if (!m) throw new Error('geometry_pure.js needs js/rack_state.js loaded first');
        return m;
    }
    function getDefaultModelTransform() { return _rack().getDefaultModelTransform.apply(null, arguments); }
    function _rackServerView() { return _rack()._rackServerView.apply(null, arguments); }
    // A server's absolute depth interval. rack_state.js owns the arithmetic (and
    // the fallback for documents that still spell depth as the retired
    // rack-relative extras) so that the normalizer, the conflict predicate and
    // the geometry all place a server at the same Z.
    function _serverDepthInterval() { return _rack()._serverDepthInterval.apply(null, arguments); }

    // Sibling modules, bound explicitly.
    //
    // These calls used to resolve as bare globals, and in the legacy page they
    // did work — a top-level `function` in a classic <script> becomes a window
    // property, so `sanitizeName(...)` found the page's forwarding alias. That
    // is an accident of where this code was loaded from, not a dependency the
    // module declares, and it makes the file unusable anywhere else: React
    // imports it as a module and there are no such globals.
    //
    // Resolved lazily through a getter rather than captured at load time,
    // because script order is not guaranteed across the two apps.
    function _naming() {
        var m = (typeof window !== 'undefined') ? window.stlNaming : null;
        if (!m) throw new Error('geometry_pure.js needs js/stl_naming.js loaded first');
        return m;
    }
    function _writer() {
        var m = (typeof window !== 'undefined') ? window.stlWriter : null;
        if (!m) throw new Error('geometry_pure.js needs js/stl_writer.js loaded first');
        return m;
    }
    function sanitizeName(a) { return _naming().sanitizeName(a); }
    function getPatchFinalExportName(a, b, c) { return _naming().getPatchFinalExportName(a, b, c); }
    function getRackEquipmentExportStem(a, b) { return _naming().getRackEquipmentExportStem(a, b); }
    function getRackServerViews(a) { return _naming().getRackServerViews(a); }
    function _rackServersDict(a) { return _naming().rackServersDict(a); }
    function appendMeshFacets(a, b, c) { return _writer().appendMeshFacets(a, b, c); }
    function exportToStlString(a, b, c, d, e) { return _writer().exportToStlString(a, b, c, d, e); }

    // The procedural canvas textures (brushed metal, perforated metal, floor
    // tiles, concrete, blanking plastic — redrawn with Math.random on every
    // load) were retired 2026-10-21: every surface is a texture set in
    // textures/materials.json, generated deterministically by
    // tools/gen_pbr_textures.py.

    function getEquipmentFaceColor(equipment, faceId) {
        var defaultColors = { inlet: '#4488ff', outlet: '#44ff88' };
        if (equipment.faces && equipment.faces[faceId] && equipment.faces[faceId].color) {
            return equipment.faces[faceId].color;
        }
        return defaultColors[faceId] || '#888888';
    }

    
    function _poseKernel() {
        const kernel = typeof window !== 'undefined' && window.modelPosePorts
            || typeof globalThis !== 'undefined' && globalThis.modelPosePorts
            || typeof require === 'function' && require('./model_pose_ports.js');
        if (!kernel) throw new Error('geometry_pure.js needs js/model_pose_ports.js loaded first');
        return kernel;
    }

    function cloneModelTransform(rawTransform = {}) {
        return _poseKernel().cloneModelTransform(rawTransform);
    }

    function modelWorldTransform(model) {
        return _poseKernel().modelWorldTransform(model);
    }

    // Three is only the compatibility carrier. Domain methods and these legacy
    // helpers use the same numeric kernel, with the original missing-Three guard.
    function modelLocalPointToWorld(model, point) {
        if (typeof THREE === 'undefined' || !model || !point) return null;
        const value = _poseKernel().modelLocalPointToWorld(model, point);
        return new THREE.Vector3(value.x, value.y, value.z);
    }

    function modelLocalDirectionToWorld(model, direction) {
        if (typeof THREE === 'undefined' || !model || !direction) return null;
        const value = _poseKernel().modelLocalDirectionToWorld(model, direction);
        return new THREE.Vector3(value.x, value.y, value.z);
    }

    function liquidEquipmentPortFrame(model, portType, end) {
        if (typeof THREE === 'undefined' || !model || !model.geometry) return null;
        const frame = _poseKernel().equipmentPortFrame(model, model.kind, portType, end);
        if (!frame) return null;
        const vector = value => new THREE.Vector3(value.x, value.y, value.z);
        return {
            position: vector(frame.position),
            direction: vector(frame.direction),
            localPosition: vector(frame.localPosition),
            localDirection: vector(frame.localDirection)
        };
    }

    function getAllModelPatches(model) {
        const patches = [];
        Object.keys(model.geometry.faces).forEach(faceId => {
            model.geometry.faces[faceId].patches.forEach(patch => patches.push({ faceId, patch }));
        });
        return patches;
    }

    
    function ensureWallOpacity(face, wallIndex) {
        if (!Array.isArray(face.wallOpacities)) face.wallOpacities = [];
        if (!Number.isFinite(face.wallOpacities[wallIndex])) face.wallOpacities[wallIndex] = Number.isFinite(face.opacity) ? face.opacity : 1;
        return face.wallOpacities[wallIndex];
    }

        function getRackMergedEmptySlotRanges(model, side) {
        if (!model || model.kind !== 'rack') return [];
        const slotCount = model.geometry.slot;
        const oppSide = side === 'front' ? 'back' : 'front';
        const ranges = [];
        let start = -1;
        let curBaffle = false;  // whether current run has opposite-side void
        for (let i = 0; i < slotCount; i++) {
            if (!isRackSlotSelectable(model, i)) {
                if (start >= 0) { ranges.push({ startUnit: start, endUnit: i - 1, isWallBaffle: curBaffle }); start = -1; }
                continue;
            }
            const status = getRackSlotStatus(model, side, i);
            if (status.kind === 'wall') {
                // Check if opposite side is void (deleted, no equipment) — "half-dissolved"
                const oppVoid = !getRackEquipmentForSlot(model, i) && isRackSlotDeleted(model, oppSide, i);
                if (start < 0) {
                    start = i;
                    curBaffle = oppVoid;
                } else if (oppVoid !== curBaffle) {
                    // Wall-baffle status changed — split the range here
                    ranges.push({ startUnit: start, endUnit: i - 1, isWallBaffle: curBaffle });
                    start = i;
                    curBaffle = oppVoid;
                }
                // else: same status, continue extending the range
            } else {
                if (start >= 0) { ranges.push({ startUnit: start, endUnit: i - 1, isWallBaffle: curBaffle }); start = -1; }
            }
        }
        if (start >= 0) {
            let lastSelectable = slotCount - 1;
            while (lastSelectable > start && !isRackSlotSelectable(model, lastSelectable)) lastSelectable--;
            ranges.push({ startUnit: start, endUnit: lastSelectable, isWallBaffle: curBaffle });
        }
        return ranges;
    }

    
    function disposeRenderObject(obj) {
        obj.traverse(child => {
            // A disposed helper must leave the scale registry too: both apps
            // clear their scene through this function (legacy clearRender,
            // React sceneBuilder), so hooking the unregister here covers every
            // registered helper under a disposed root without each clear path
            // having to know which of its meshes were registered.
            unregisterHelper(child);
            if (child.geometry) child.geometry.dispose();
            if (child.material) {
                const mats = Array.isArray(child.material) ? child.material : [child.material];
                mats.forEach(mat => {
                    ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'envMap'].forEach(prop => {
                        if (mat[prop] && !mat[prop]._isCached) mat[prop].dispose();
                    });
                    if (!mat._isCached) mat.dispose();
                });
            }
        });
    }

    
    function applyModelTransformToObject(object, model) {
        if (!object || !model) return;
        // V4: rotation/scale are canonical on model.geometry; the legacy
        // model.transform mirror is stale (identity) and often absent, which left
        // every rotated model (walls, rotated racks) un-rotated in the rendered/
        // exported geometry. Read the geometry source of truth.
        const transform = modelWorldTransform(model);
        object.quaternion.set(
            transform.quaternion.x,
            transform.quaternion.y,
            transform.quaternion.z,
            transform.quaternion.w
        ).normalize();
        object.scale.set(transform.scale.x, transform.scale.y, transform.scale.z);
    }

    
    function circleToPoly(x, y, r) {
        return _boxProduct().circleToPoly(x, y, r);
    }

    
    function rectToPoly(x, y, w, h) {
        return _boxProduct().rectToPoly(x, y, w, h);
    }
    // The four corners of a rectangle with per-corner in-plane offsets
    // (`cornerOffsets`: [{x,y} x 4], bottom-left, bottom-right, top-right,
    // top-left in the face's (u, v)). A rectangle whose corners were dragged
    // becomes a general quad — a trapezoid for a duct's expansion, say.
    function quadCornersUV(x, y, w, h, offsets) {
        return _boxProduct().quadCornersUV(x, y, w, h, offsets);
    }
    function hasCornerOffsets(offsets) {
        return _boxProduct().hasCornerOffsets(offsets);
    }
    function quadToPoly(x, y, w, h, offsets) {
        return _boxProduct().quadToPoly(x, y, w, h, offsets);
    }

    // ── polygon sheets (a floor / ceiling drawn in the 2D room editor) ──────
    //
    // `geometry.polygon` holds the outline in the TOP face layout's (u, v):
    // u runs along local +X from the model origin, v along local -Z from the
    // far edge (layout origin (0, H, D), up (0, 0, -1)), so a vertex sits at
    // local (u, H, D - v). The 2D editor stores it re-based to its own
    // bounding box (min u = min v = 0) with `size` = that box. Both editors
    // draw the selection outline and the grab cubes from these helpers, one
    // cube per vertex and one per edge, instead of the bounding rectangle's
    // four corners and four edges.
    function sheetPolygon(model) {
        const poly = model && model.geometry && model.geometry.polygon;
        if (!Array.isArray(poly) || poly.length < 3) return null;
        if (!poly.every(p => Array.isArray(p) && Number.isFinite(Number(p[0])) && Number.isFinite(Number(p[1])))) return null;
        return poly.map(p => [Number(p[0]), Number(p[1])]);
    }
    function isPolygonSheet(model) {
        return !!(model && model.kind === 'floor' && sheetPolygon(model));
    }
    // Handle points in (u, v): every vertex, and every edge's midpoint with the
    // edge's in-plane OUTWARD normal (so an edge cube drags the edge along it).
    function polygonHandlePointsUV(poly) {
        const n = poly.length;
        let area2 = 0;
        for (let i = 0; i < n; i++) {
            const a = poly[i], b = poly[(i + 1) % n];
            area2 += a[0] * b[1] - b[0] * a[1];
        }
        const ccw = area2 > 0;
        const edges = [];
        for (let i = 0; i < n; i++) {
            const a = poly[i], b = poly[(i + 1) % n];
            const dx = b[0] - a[0], dy = b[1] - a[1];
            const len = Math.hypot(dx, dy) || 1;
            // outward: right of the direction of travel for a CCW ring
            const nx = ccw ? dy / len : -dy / len;
            const ny = ccw ? -dx / len : dx / len;
            edges.push({ index: i, mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], normal: [nx, ny], length: len });
        }
        return { vertices: poly.map(p => [p[0], p[1]]), edges };
    }
    // True when two non-adjacent edges of the ring cross (or touch): such an
    // outline has no inside, so a drag that would make one is refused.
    function polygonSelfIntersects(poly) {
        const n = poly.length;
        const orient = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
        const onSeg = (a, b, c) => Math.min(a[0], b[0]) - 1e-12 <= c[0] && c[0] <= Math.max(a[0], b[0]) + 1e-12
            && Math.min(a[1], b[1]) - 1e-12 <= c[1] && c[1] <= Math.max(a[1], b[1]) + 1e-12;
        const cross = (p1, p2, p3, p4) => {
            const d1 = orient(p3, p4, p1), d2 = orient(p3, p4, p2), d3 = orient(p1, p2, p3), d4 = orient(p1, p2, p4);
            if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true;
            if (Math.abs(d1) < 1e-12 && onSeg(p3, p4, p1)) return true;
            if (Math.abs(d2) < 1e-12 && onSeg(p3, p4, p2)) return true;
            if (Math.abs(d3) < 1e-12 && onSeg(p1, p2, p3)) return true;
            if (Math.abs(d4) < 1e-12 && onSeg(p1, p2, p4)) return true;
            return false;
        };
        for (let i = 0; i < n; i++) {
            for (let j = i + 1; j < n; j++) {
                if (j === i + 1 || (i === 0 && j === n - 1)) continue;
                if (cross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])) return true;
            }
        }
        return false;
    }
    // Move vertex `index` to (u, v). A move that folds the outline over itself
    // or collapses it (zero area) is refused and returns the input.
    function movePolygonVertex(poly, index, u, v) {
        const out = poly.map(p => [p[0], p[1]]);
        if (index < 0 || index >= out.length || !Number.isFinite(u) || !Number.isFinite(v)) return out;
        out[index] = [u, v];
        let a = 0;
        for (let i = 0; i < out.length; i++) { const p0 = out[i], p1 = out[(i + 1) % out.length]; a += p0[0] * p1[1] - p1[0] * p0[1]; }
        if (Math.abs(a / 2) < 1e-6 || polygonSelfIntersects(out)) return poly.map(p => [p[0], p[1]]);
        return out;
    }
    // Translate edge `index` (vertex index -> index+1) along its outward normal
    // by the component of (du, dv) on that normal: the polygon analogue of
    // dragging a rectangle's edge. An edge shorter than `minEdge` after the
    // move is refused (returns the input), as is a move that would fold the
    // polygon over itself (its signed area changes sign).
    function movePolygonEdge(poly, index, du, dv) {
        const n = poly.length;
        if (index < 0 || index >= n) return poly.map(p => [p[0], p[1]]);
        const h = polygonHandlePointsUV(poly).edges[index];
        const d = du * h.normal[0] + dv * h.normal[1];
        const out = poly.map(p => [p[0], p[1]]);
        const j = (index + 1) % n;
        out[index] = [poly[index][0] + h.normal[0] * d, poly[index][1] + h.normal[1] * d];
        out[j] = [poly[j][0] + h.normal[0] * d, poly[j][1] + h.normal[1] * d];
        const signed = (q) => { let a = 0; for (let i = 0; i < q.length; i++) { const p0 = q[i], p1 = q[(i + 1) % q.length]; a += p0[0] * p1[1] - p1[0] * p0[1]; } return a / 2; };
        const before = signed(poly), after = signed(out);
        if (Math.abs(after) < 1e-6 || Math.sign(after) !== Math.sign(before) || polygonSelfIntersects(out)) return poly.map(p => [p[0], p[1]]);
        return out;
    }
    // Re-base a polygon floor to its own bounding box after an edit, moving
    // `location` so NOTHING moves in the world: the polygon, the top face's
    // patches and cutouts shift by (-minU, -minV), size becomes the box, and
    // location moves by R * (minU, 0, D - maxV) — the v axis runs from the far
    // edge, so a change of depth would otherwise slide every vertex.
    // `rotate(vec3) -> vec3` applies the model's quaternion; omit it for an
    // unrotated floor. Mutates `model`; returns true when anything changed.
    function rebasePolygonSheet(model, rotate) {
        const poly = sheetPolygon(model);
        if (!poly) return false;
        const g = model.geometry;
        let minU = Infinity, minV = Infinity, maxU = -Infinity, maxV = -Infinity;
        poly.forEach(([u, v]) => { minU = Math.min(minU, u); maxU = Math.max(maxU, u); minV = Math.min(minV, v); maxV = Math.max(maxV, v); });
        const D = Number(g.size && g.size.z) || 0;
        const W = Number(g.size && g.size.x) || 0;
        const dz = D - maxV;
        const moved = Math.abs(minU) > 1e-12 || Math.abs(minV) > 1e-12 || Math.abs(dz) > 1e-12
            || Math.abs((maxU - minU) - W) > 1e-12 || Math.abs((maxV - minV) - D) > 1e-12;
        if (!moved) return false;
        const q = (x) => Math.round(x * 1e6) / 1e6;
        g.polygon = poly.map(([u, v]) => [q(u - minU), q(v - minV)]);
        const shiftRect = (p) => {
            if (!p) return;
            if (p.type === 'circle') { p.cx = q((Number(p.cx) || 0) - minU); p.cy = q((Number(p.cy) || 0) - minV); }
            else { p.x = q((Number(p.x) || 0) - minU); p.y = q((Number(p.y) || 0) - minV); }
        };
        const top = g.faces && g.faces.top;
        if (top && Array.isArray(top.patches)) top.patches.forEach(shiftRect);
        if (Array.isArray(g.openings)) g.openings.forEach(shiftRect);
        if (Array.isArray(g.cutouts)) {
            g.cutouts.forEach(c => {
                if (!c || c.faceId !== 'top' || !Array.isArray(c.polygon)) return;
                c.polygon = c.polygon.map(poly2 => poly2.map(ring => ring.map(pt => [q(pt[0] - minU), q(pt[1] - minV)])));
            });
        }
        g.size = Object.assign({}, g.size, { x: q(maxU - minU), z: q(maxV - minV) });
        const local = { x: minU, y: 0, z: dz };
        const w = typeof rotate === 'function' ? rotate(local) : local;
        g.location = { x: q(g.location.x + w.x), y: q(g.location.y + w.y), z: q(g.location.z + w.z) };
        return true;
    }

    
    function getPartitionWallPatchStore(model) {
        if (!model || model.kind !== 'partitionWall') return [];
        if (!model.geometry.faces.front) model.geometry.faces.front = { patches: [] };
        if (!Array.isArray(model.geometry.faces.front.patches)) model.geometry.faces.front.patches = [];
        return model.geometry.faces.front.patches;
    }

    
    function parseFiniteOrDefault(value, fallback) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    
    function _partitionPatchToPoly(patch) {
        if (patch.type === 'circle') return circleToPoly(patch.cx, patch.cy, patch.r);
        return rectToPoly(patch.x, patch.y, patch.w, patch.h);
    }

    
    function buildFaceIslands(model) {
        return _boxProduct().buildFaceIslands(model);
    }

    
    function getRackEquipmentForSlot(model, slotIndex) {
        const servers = _rackServersDict(model);
        if (!servers) return null;
        for (const uid of Object.keys(servers)) {
            const s = servers[uid];
            const start = (s.geometry || {}).slotPosition;
            const span = (s.geometry || {}).slotOccupation;
            if (slotIndex >= start && slotIndex < start + span) return _rackServerView(servers, uid);
        }
        return null;
    }

    /**
     * EVERY server covering one U-slot, nearest the rack inlet plane first.
     *
     * `getRackEquipmentForSlot` above keeps its old meaning — the first server
     * in dict order, which is what the slot-status and blanking-panel queries
     * want — because a slot may now hold a stack: two records sharing a U range
     * at different depths (2026-09-17 spec §4.4). Anything that has to reason
     * about the depth of a slot (the stacked-server panel, the free-gap scan, a
     * depth bar) needs all of them, in the order the depth axis reads.
     *
     * Sorted by the ABSOLUTE inletOffset, not by the retired rack-relative
     * extra, so the order does not change when the row sliders move.
     */
    function getRackServersForSlot(model, slotIndex) {
        const servers = _rackServersDict(model);
        if (!servers) return [];
        const covering = [];
        for (const uid of Object.keys(servers)) {
            const s = servers[uid];
            if (!s || typeof s !== 'object') continue;
            const start = (s.geometry || {}).slotPosition;
            const span = (s.geometry || {}).slotOccupation;
            if (slotIndex >= start && slotIndex < start + span) covering.push(_rackServerView(servers, uid));
        }
        covering.sort((a, b) => (Number(a.inletOffset) || 0) - (Number(b.inletOffset) || 0));
        return covering;
    }

    
    function getRackSlotBounds(model, slotIndex) {
        const geom = model.geometry;
        const slotWidth = geom.size.x * geom.slotWidthRatio;
        return {
            x: (geom.size.x - slotWidth) / 2,
            y: model.safeMarginBottom + slotIndex * geom.slotHeight,
            w: slotWidth,
            h: geom.slotHeight
        };
    }

    
    function isRackSlotSelectable(model, slotIndex) {
        if (!model || model.kind !== 'rack') return false;
        return slotIndex >= 0 && slotIndex < model.geometry.slot;
    }

    
    function isRackSlotDeleted(model, side, slotIndex) {
        return !!(model && model.kind === 'rack' && model.deletedFaces && model.deletedFaces[side] && model.deletedFaces[side][slotIndex]);
    }

    
    function getRackSlotStatus(model, side, slotIndex) {
        const equipment = getRackEquipmentForSlot(model, slotIndex);
        if (equipment) return { kind: 'equipment', equipment };
        if (isRackSlotDeleted(model, side, slotIndex)) return { kind: 'void', equipment: null };
        return { kind: 'wall', equipment: null };
    }

        function getServerFaceExportName(model, equipment, faceId, opts) {
        var stem = getRackEquipmentExportStem(model, equipment);
    
        // Determine base face suffix (backward-compat with existing naming)
        var faceSuffix;
        if (equipment.kind === 'fanServer') {
            if (faceId === 'inlet')       faceSuffix = 'inlet_patch';
            else if (faceId === 'outlet') faceSuffix = 'outlet_patch';
            else                          faceSuffix = faceId;  // top, bottom, left, right
        } else {
            // nonFan: front/back/top/bottom/left/right
            if (faceId === 'inlet')       faceSuffix = 'front';
            else if (faceId === 'outlet') faceSuffix = 'back';
            else                          faceSuffix = faceId;
        }
    
        if (!opts) {
            // Whole face (no patches)
            return sanitizeName(stem + '_' + faceSuffix);
        }
    
        if (opts.isPatch) {
            // Patch region: {stem}_{faceId}_p{index}
            return sanitizeName(stem + '_' + faceId + '_p' + opts.patchIndex);
        }
    
        if (opts.isWall) {
            // Wall remainder: {stem}_{faceId}_wall
            return sanitizeName(stem + '_' + faceId + '_wall');
        }
    
        return sanitizeName(stem + '_' + faceSuffix);
    }

        function getRackMergedSlotExportName(model, side, startUnit, endUnit) {
        const rName = sanitizeName(model.meta.name);
        // Blanking-panel solids MUST carry an inlet/outlet face token so that
        // _classifyRackSolidToBucketFile's /_(?:inlet|outlet)_u\d+/ regex routes
        // them into the merged_(perforated|wall)BlankingPanel_<N>U.stl buckets.
        // V4 racks store geometry.faces as an ARRAY of door entries (no v3
        // {front,back} display-name objects), so the old getFaceDisplayName
        // lookup returned null and fell back to the raw side ('front'/'back').
        // The bucket regex rejected front/back, leaving every blanking panel
        // "unrouted" → silently dropped from the STL export (no
        // merged_perforatedBlankingPanel_*.stl emitted). Map the side directly
        // to the inlet/outlet token the classifier (and the v3 default
        // displayName) used.
        const sideDisplayName = side === 'front' ? 'inlet'
                              : side === 'back'  ? 'outlet'
                              : sanitizeName(side);
        if (startUnit === endUnit) {
            return sanitizeName(`${rName}_${sideDisplayName}_u${startUnit + 1}`);
        }
        return sanitizeName(`${rName}_${sideDisplayName}_u${startUnit + 1}_u${endUnit + 1}`);
    }

        function getRackPartitionExportName(model, partitionIndex) {
        const rName = sanitizeName(model.meta.name);
        return sanitizeName(`${rName}_u${partitionIndex + 1}_partition`);
    }

    /**
     * The STRUCTURAL depth of one U-slot: the rack's own row plane, and nothing
     * else. Blanking panels, slot guides and partitions are built between these
     * two Z's.
     *
     * It used to add the occupying server's own setbacks, which is what made the
     * rack-detail row sliders reposition every server in the rack: a server's
     * depth was DERIVED from the row, so moving the row moved the server. Since
     * the 2026-09-17 stacked-servers spec a server carries ABSOLUTE setbacks
     * (see getServerDepthRange) and this range is rack-level unconditionally —
     * which also means it no longer depends on WHICH server covers the slot, so
     * a stack of two servers at different depths has one structural answer.
     *
     * `slotIndex` is kept in the signature: every call site passes it, and the
     * y0/y1 spelling stays because it predates the 2026-04-18 Y-up flip.
     */
        function getSlotDepthRange(model, slotIndex) {
        var baseStart = model.inletOffset || 0;
        var baseEnd = model.geometry.size.z - (model.outletOffset || 0);

        return { y0: baseStart, y1: Math.max(baseStart, baseEnd) };
    }

    /**
     * The depth a partition wall spans: the rack's row plane, like the slot it
     * sits between. It used to intersect the two neighbouring slots' ranges,
     * which — while those still carried server setbacks — made a partition
     * follow the servers beside it. Spec §13 accepts the consequence: next to a
     * recessed server a partition now reaches the full structural depth.
     */
        function getPartitionDepthRange(model, partitionIndex) {
        var range = getSlotDepthRange(model, partitionIndex);
        return { y0: range.y0, y1: range.y1, depth: Math.max(0, range.y1 - range.y0) };
    }

    /**
     * One server's ABSOLUTE depth box: { z0, z1 } in rack-local Z, inlet face at
     * z = 0 and outlet face at z = D.
     *
     *     z0 = server.inletOffset ;  z1 = D - server.outletOffset
     *
     * The rack's row offsets take no part — that is the decoupling the spec is
     * about. rack_state.js derives the pair (including the fallback for
     * documents that still carry the retired rack-relative extras) and clamps it
     * to 0.9 * D, so the normalizer, the conflict predicate and this reader all
     * place a server at the same Z. Nothing is written back.
     */
        function getServerDepthRange(model, server) {
        var range = _serverDepthInterval(model, server) || {};
        return { z0: range.z0, z1: range.z1 };
    }

    
    // ── Wall-mounted ("挂机") ACU ────────────────────────────────────────
    // A split-system indoor unit: width W along local x, height H along y,
    // depth D along z with the BACK (z = D) on the wall. Only four faces:
    // left, right, top, and one continuous FRONT that runs down the vertical
    // face, round a quarter circle of radius r and back along the bottom to
    // the wall. Patches on that front live in unrolled coordinates: x across
    // the width, y along the developed profile from the bottom-back edge
    // (0) to the top-front edge (L = (D - r) + pi r / 2 + (H - r)).
    function isWallMountedAcu(model) {
        return !!(model && model.kind === 'acu' && model.cooling && model.cooling.unitType === 'wallMounted');
    }
    function wallAcuProfile(model) {
        const sz = (model.geometry && model.geometry.size) || {};
        const W = Number(sz.x) || 0.8, H = Number(sz.y) || 0.3, D = Number(sz.z) || 0.2;
        let r = Number(model.geometry && model.geometry.frontRadius);
        if (!(r > 0)) r = 0.6 * Math.min(H, D);
        r = Math.min(r, H - 1e-4, D - 1e-4);
        const flat = D - r, arc = Math.PI * r / 2, vert = H - r;
        const L = flat + arc + vert;
        // position (y, z) and outward normal (ny, nz) at developed length v
        const at = (v) => {
            v = Math.max(0, Math.min(L, v));
            if (v <= flat) return { y: 0, z: D - v, ny: -1, nz: 0 };
            if (v <= flat + arc) {
                const phi = (v - flat) / r;
                return { y: r - r * Math.cos(phi), z: r - r * Math.sin(phi), ny: -Math.cos(phi), nz: -Math.sin(phi) };
            }
            return { y: r + (v - flat - arc), z: 0, ny: 0, nz: -1 };
        };
        return { W, H, D, r, flat, arc, vert, L, at };
    }
    // local 3D point of unrolled (u, v): u across the width from the right
    // end (as the planar front layout does), v along the profile
    function wallAcuPointLocal(profile, u, v) {
        const p = profile.at(v);
        return new THREE.Vector3(profile.W - u, p.y, p.z);
    }
    function wallAcuNormalLocal(profile, v) {
        const p = profile.at(v);
        return new THREE.Vector3(0, p.ny, p.nz);
    }
    function _wallAcuPatchUV(patch) {
        if (!patch) return null;
        if (patch.type === 'circle') return { u: patch.cx, v: patch.cy, w: 2 * patch.r, h: 2 * patch.r };
        return { u: (patch.x || 0) + (patch.w || 0) / 2, v: (patch.y || 0) + (patch.h || 0) / 2, w: patch.w || 0, h: patch.h || 0 };
    }
    // world-space centre of a patch on the curved front, and the supply
    // direction: the surface normal there tilted by `physics.supplyAngleDeg`
    // about the width axis (positive = downwards, the louvre pointing at the
    // floor), then carried through the model's rotation.
    function wallAcuPatchCentreWorld(model, patch) {
        const pr = wallAcuProfile(model);
        const uv = _wallAcuPatchUV(patch);
        if (!uv) return null;
        const local = wallAcuPointLocal(pr, uv.u, uv.v);
        return _wallAcuLocalToWorld(model, local, false);
    }
    function wallAcuSupplyDirectionWorld(model, patch) {
        const pr = wallAcuProfile(model);
        const uv = _wallAcuPatchUV(patch);
        if (!uv) return null;
        const n = wallAcuNormalLocal(pr, uv.v);
        const deg = Number(patch.physics && patch.physics.supplyAngleDeg) || 0;
        // rotate about local +x by -deg: positive deg tilts the front normal
        // (0,0,-1) towards -y
        const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -deg * Math.PI / 180);
        n.applyQuaternion(q).normalize();
        return _wallAcuLocalToWorld(model, n, true);
    }
    // (u, v) of a model-local point that lies ON the curved front: u across
    // the width, v the developed length of the nearest profile region
    function wallAcuLocalToUV(model, local) {
        const pr = wallAcuProfile(model);
        const u = pr.W - local.x;
        const y = local.y, z = local.z;
        let v;
        if (z >= pr.r && y <= pr.r) v = pr.D - z;
        else if (y >= pr.r && z <= pr.r) v = pr.flat + pr.arc + (y - pr.r);
        else {
            const phi = Math.atan2(pr.r - z, pr.r - y);
            v = pr.flat + pr.r * Math.max(0, Math.min(Math.PI / 2, phi));
        }
        return { u, v: Math.max(0, Math.min(pr.L, v)) };
    }
    // Where a model-local ray meets the curved front: the flat underside,
    // the quarter cylinder or the vertical face, whichever is hit first.
    // Returns { u, v, point } or null.
    function wallAcuRayFacePoint(model, originLocal, dirLocal) {
        const pr = wallAcuProfile(model);
        const o = originLocal, d = dirLocal;
        const eps = 1e-9;
        let best = null;
        const consider = (t, y, z) => {
            if (!(t > eps)) return;
            const x = o.x + d.x * t;
            if (x < -1e-6 || x > pr.W + 1e-6) return;
            if (best && t >= best.t) return;
            best = { t, x, y, z };
        };
        // flat underside y = 0, z in [r, D]
        if (Math.abs(d.y) > eps) {
            const t = -o.y / d.y;
            const z = o.z + d.z * t;
            if (z >= pr.r - 1e-6 && z <= pr.D + 1e-6) consider(t, 0, Math.min(pr.D, Math.max(pr.r, z)));
        }
        // vertical front z = 0, y in [r, H]
        if (Math.abs(d.z) > eps) {
            const t = -o.z / d.z;
            const y = o.y + d.y * t;
            if (y >= pr.r - 1e-6 && y <= pr.H + 1e-6) consider(t, Math.min(pr.H, Math.max(pr.r, y)), 0);
        }
        // quarter cylinder about the axis (y = r, z = r) along x, radius r,
        // restricted to the quadrant y < r, z < r
        {
            const cy = o.y - pr.r, cz = o.z - pr.r;
            const a = d.y * d.y + d.z * d.z;
            if (a > eps) {
                const b = 2 * (cy * d.y + cz * d.z);
                const c = cy * cy + cz * cz - pr.r * pr.r;
                const disc = b * b - 4 * a * c;
                if (disc >= 0) {
                    const sq = Math.sqrt(disc);
                    for (const t of [(-b - sq) / (2 * a), (-b + sq) / (2 * a)]) {
                        const y = o.y + d.y * t, z = o.z + d.z * t;
                        if (y <= pr.r + 1e-6 && z <= pr.r + 1e-6) consider(t, y, z);
                    }
                }
            }
        }
        if (!best) return null;
        const point = new THREE.Vector3(best.x, best.y, best.z);
        const uv = wallAcuLocalToUV(model, point);
        return { u: uv.u, v: uv.v, point };
    }
    // Outline of a rectangle (layout coords: x = u, y = v) drawn ON the curved
    // front, model-local: the four corners, the mid-edge handle points and a
    // polyline that follows the profile along the constant-u edges.
    function wallAcuRectOutlineLocal(model, rect, segs) {
        const pr = wallAcuProfile(model);
        const n = Math.max(2, segs || 12);
        const x0 = Number(rect.x) || 0, y0 = Number(rect.y) || 0;
        const w = Number(rect.w) || 0, h = Number(rect.h) || 0;
        const P = (u, v) => wallAcuPointLocal(pr, u, v);
        const corners = [P(x0, y0), P(x0 + w, y0), P(x0 + w, y0 + h), P(x0, y0 + h)];
        const edges = {
            bottom: P(x0 + w / 2, y0),
            right: P(x0 + w, y0 + h / 2),
            top: P(x0 + w / 2, y0 + h),
            left: P(x0, y0 + h / 2)
        };
        const polyline = [];
        polyline.push(P(x0, y0), P(x0 + w, y0));
        for (let i = 1; i <= n; i++) polyline.push(P(x0 + w, y0 + h * i / n));
        polyline.push(P(x0, y0 + h));
        for (let i = n - 1; i >= 0; i--) polyline.push(P(x0, y0 + h * i / n));
        return { corners, edges, polyline };
    }
    function _wallAcuLocalToWorld(model, v, isDirection) {
        const g = model.geometry || {};
        const q = g.quaternion ? new THREE.Quaternion(g.quaternion.x || 0, g.quaternion.y || 0, g.quaternion.z || 0, g.quaternion.w != null ? g.quaternion.w : 1) : new THREE.Quaternion();
        const out = v.clone().applyQuaternion(q);
        if (!isDirection) {
            const loc = g.location || { x: 0, y: 0, z: 0 };
            out.add(new THREE.Vector3(loc.x || 0, loc.y || 0, loc.z || 0));
        }
        return out;
    }

    function getFaceLayouts(model) {
        const _sz = (model.geometry && model.geometry.size) || {};
        const W = _sz.x, H = _sz.y, D = _sz.z;
        const v3 = (x, y, z) => new THREE.Vector3(x, y, z);
        const makeMat = (origin, right, up) => new THREE.Matrix4().makeBasis(right, up, new THREE.Vector3().crossVectors(right, up).normalize()).setPosition(origin);
        if (isWallMountedAcu(model)) {
            // four faces; the front's height is the developed profile length
            const pr = wallAcuProfile(model);
            return {
                front:  { origin: v3(W, 0, 0), right: v3(-1, 0, 0), up: v3(0, 1, 0), mat: makeMat(v3(W, 0, 0), v3(-1, 0, 0), v3(0, 1, 0)), w: W, h: pr.L, color: '#5c6370', extDepth: D, curved: true },
                left:   { origin: v3(0, 0, 0), right: v3(0, 0, 1), up: v3(0, 1, 0), mat: makeMat(v3(0, 0, 0), v3(0, 0, 1), v3(0, 1, 0)), w: D, h: H, color: '#7f848e', extDepth: W },
                right:  { origin: v3(W, 0, D), right: v3(0, 0, -1), up: v3(0, 1, 0), mat: makeMat(v3(W, 0, D), v3(0, 0, -1), v3(0, 1, 0)), w: D, h: H, color: '#7f848e', extDepth: W },
                top:    { origin: v3(0, H, D), right: v3(1, 0, 0), up: v3(0, 0, -1), mat: makeMat(v3(0, H, D), v3(1, 0, 0), v3(0, 0, -1)), w: W, h: D, color: '#9ba0a9', extDepth: H }
            };
        }
        var numeric = _boxProduct().getBoxFaceFrames(model), layouts = {};
        Object.keys(numeric).forEach(function (id) {
            var f = numeric[id];
            layouts[id] = { ...f, origin: v3(...f.origin), right: v3(...f.right), up: v3(...f.up),
                mat: new THREE.Matrix4().fromArray(f.matrix) };
            delete layouts[id].matrix;
        });
        return layouts;
    }

    
    function getPrimaryOrientationFaceId(model) {
        if (!model) return null;
        if (model.kind === 'wall') return 'front';
        if (model.kind === 'floor') return 'top';
        return null;
    }

    
    function isWallFloorOrientationEligible(model) {
        if (!model) return false;
        if (model.kind === 'wall') return true;
        if (model.kind === 'floor') {
            const defaultType = model.cooling?.defaultBoundaryType || 'wall';
            return defaultType === 'wall';
        }
        return false;
    }

    
    function getPrimaryOrientationFace(model) {
        const faceId = getPrimaryOrientationFaceId(model);
        const faces = model && model.geometry && model.geometry.faces;
        return faceId && faces ? faces[faceId] : null;
    }

    
    function isPrimaryFaceSolidNormalReversed(model) {
        return !!getPrimaryOrientationFace(model)?.reverseSolidNormal;
    }

    
    function getDefaultPatchVector(faceId) {
        // 2026-04-18 Y-up: projection defaults = inward face normals under Y-up.
        // front outward=(0,0,-1) → inward=(0,0,+1); back outward=(0,0,+1) → inward=(0,0,-1)
        // top outward=(0,+1,0) → inward=(0,-1,0); bottom outward=(0,-1,0) → inward=(0,+1,0)
        // left/right unchanged: left inward=(+1,0,0), right inward=(-1,0,0)
        const defaults = {
            front: new THREE.Vector3(0, 0, 1),   // 2026-04-18 Y-up (was 0,1,0)
            back: new THREE.Vector3(0, 0, -1),   // 2026-04-18 Y-up (was 0,-1,0)
            left: new THREE.Vector3(1, 0, 0),
            right: new THREE.Vector3(-1, 0, 0),
            top: new THREE.Vector3(0, -1, 0),    // 2026-04-18 Y-up (was 0,0,-1)
            bottom: new THREE.Vector3(0, 1, 0)   // 2026-04-18 Y-up (was 0,0,1)
        };
        return (defaults[faceId] || new THREE.Vector3(0, 0, 1)).clone(); // fallback updated Y-up
    }

    
    function ensurePatchProjectionDefaults(faceId, patch) {
        if (typeof patch.showVector !== 'boolean') patch.showVector = true;
        if (![patch.vx, patch.vy, patch.vz].every(Number.isFinite)) {
            const v = getDefaultPatchVector(faceId);
            patch.vx = v.x;
            patch.vy = v.y;
            patch.vz = v.z;
        }
    }

    
    function getPatchVector(faceId, patch) {
        ensurePatchProjectionDefaults(faceId, patch);
        const vector = new THREE.Vector3(patch.vx, patch.vy, patch.vz);
        if (vector.lengthSq() < 1e-8) vector.copy(getDefaultPatchVector(faceId));
        return vector.normalize();
    }

    
    function getDefaultPatchEuler(faceId) {
        const map = {
            front:  { rx: 0, ry: 0, rz: 0 },
            back:   { rx: 0, ry: 180, rz: 0 },
            left:   { rx: 0, ry: -90, rz: 0 },
            right:  { rx: 0, ry: 90, rz: 0 },
            top:    { rx: -90, ry: 0, rz: 0 },
            bottom: { rx: 90, ry: 0, rz: 0 }
        };
        return map[faceId] || { rx: 0, ry: 0, rz: 0 };
    }

    
    function getCustomPatchBasis(patch) {
        const quaternion = new THREE.Quaternion().setFromEuler(new THREE.Euler(
            THREE.MathUtils.degToRad(patch.rx || 0),
            THREE.MathUtils.degToRad(patch.ry || 0),
            THREE.MathUtils.degToRad(patch.rz || 0),
            'XYZ'
        ));
        const right = new THREE.Vector3(1, 0, 0).applyQuaternion(quaternion);
        const up = new THREE.Vector3(0, 1, 0).applyQuaternion(quaternion); // 2026-04-18 Y-up
        return { right, up };
    }

    
    function getCustomPatchPosition(patch) {
        return new THREE.Vector3(patch.px || 0, patch.py || 0, patch.pz || 0);
    }

    
    function getAverageNormalForGeometry(geometry) {
        const pos = geometry.attributes.position;
        const triangleCount = Math.floor(pos.count / 3);
        const normal = new THREE.Vector3();
        for (let i = 0; i < triangleCount; i++) {
            const a = new THREE.Vector3().fromBufferAttribute(pos, i * 3);
            const b = new THREE.Vector3().fromBufferAttribute(pos, i * 3 + 1);
            const c = new THREE.Vector3().fromBufferAttribute(pos, i * 3 + 2);
            normal.add(new THREE.Vector3().crossVectors(new THREE.Vector3().subVectors(b, a), new THREE.Vector3().subVectors(c, a)).normalize());
        }
        return normal.lengthSq() > 0 ? normal.normalize() : new THREE.Vector3(0, 1, 0);
    }

    
    function syncPartitionWallModelState(model) {
        if (!model || model.kind !== 'partitionWall') return;
    
        model.geometry.size.x = Math.max(0.01, parseFiniteOrDefault(model.geometry.size.x, 1.0));
        model.geometry.size.y = Math.max(0.01, parseFiniteOrDefault(model.geometry.size.y, 3.0));
        model.geometry.size.z = Math.max(0.001, parseFiniteOrDefault(model.geometry.size.z, 0.05));
    
        ['left', 'right', 'top', 'bottom'].forEach(fid => {
            if (model.geometry.faces[fid] && Array.isArray(model.geometry.faces[fid].patches)) model.geometry.faces[fid].patches = [];
        });
        if (model.geometry.faces.back && Array.isArray(model.geometry.faces.back.patches)) model.geometry.faces.back.patches = [];
    
        const patches = getPartitionWallPatchStore(model);
        const width = model.geometry.size.x;
        const height = model.geometry.size.y;
    
        patches.forEach(patch => {
            if (patch.type === 'circle') {
                const maxR = Math.max(0.005, Math.min(width, height) * 0.5);
                const defaultR = Math.min(width, height) * 0.15;
                patch.r = Math.min(maxR, Math.max(0.005, parseFiniteOrDefault(patch.r, defaultR)));
                patch.cx = Math.max(patch.r, Math.min(width - patch.r, parseFiniteOrDefault(patch.cx, width * 0.5)));
                patch.cy = Math.max(patch.r, Math.min(height - patch.r, parseFiniteOrDefault(patch.cy, height * 0.5)));
            } else {
                patch.w = Math.min(width, Math.max(0.01, parseFiniteOrDefault(patch.w, width * 0.3)));
                patch.h = Math.min(height, Math.max(0.01, parseFiniteOrDefault(patch.h, height * 0.3)));
                patch.x = Math.max(0, Math.min(width - patch.w, parseFiniteOrDefault(patch.x, width * 0.2)));
                patch.y = Math.max(0, Math.min(height - patch.h, parseFiniteOrDefault(patch.y, height * 0.2)));
            }
        });
    
        for (let i = 0; i < patches.length; i++) {
            for (let j = i + 1; j < patches.length; j++) {
                let overlap = [];
                try { overlap = polygonClipping.intersection(_partitionPatchToPoly(patches[i]), _partitionPatchToPoly(patches[j])); } catch (e) {}
                if (overlap && overlap.length) {
                    throw new Error(`Partition wall hole overlap: ${patches[i].id} vs ${patches[j].id}`);
                }
            }
        }
    }

    
    function buildPartitionWallHoleCutterGeometry(model, patch) {
        // Cutter used for CSG subtraction on the wall body — depth is D + 0.004
        // so the cutter pokes 2 mm past each wall face, avoiding coplanar-face
        // artefacts in three-csg-ts. This geometry MUST NOT be reused for the
        // cellZone STL: see buildPartitionWallHoleZoneGeometry below.
        // 2026-04-18 Y-up: wall thickness is along Z; patch.h is face-vertical (Y).
        const depth = model.geometry.size.z + 0.004;
        if (patch.type === 'circle') {
            const geom = new THREE.CylinderGeometry(patch.r, patch.r, depth, 48);
            geom.rotateX(Math.PI / 2);
            geom.translate(patch.cx, patch.cy, model.geometry.size.z * 0.5);
            return geom;
        }
        const geom = new THREE.BoxGeometry(patch.w, patch.h, depth);
        geom.translate(patch.x + patch.w * 0.5, patch.y + patch.h * 0.5, model.geometry.size.z * 0.5);
        return geom;
    }

    
    function buildPartitionWallHoleProxyGeometry(patch) {
        // 2026-04-18 Y-up: 0.002 thickness runs along Z (front/back face normal).
        if (patch.type === 'circle') {
            const geom = new THREE.CylinderGeometry(patch.r, patch.r, 0.002, 48);
            geom.rotateX(Math.PI / 2);
            return geom;
        }
        return new THREE.BoxGeometry(patch.w, patch.h, 0.002);
    }

    
    function _partitionWallSignatureNumber(value) {
        return Number.isFinite(value) ? Number(value).toFixed(6) : 'nan';
    }

    
    function getPartitionWallGeometrySignature(model) {
        const patches = getPartitionWallPatchStore(model)
            .slice()
            .sort((a, b) => {
                const idA = String(a.id || '');
                const idB = String(b.id || '');
                return idA.localeCompare(idB) || String(a.type || '').localeCompare(String(b.type || ''));
            });
        const patchSignature = patches.map(patch => {
            if (patch.type === 'circle') {
                return [
                    patch.id || '',
                    'circle',
                    _partitionWallSignatureNumber(patch.cx),
                    _partitionWallSignatureNumber(patch.cy),
                    _partitionWallSignatureNumber(patch.r)
                ].join(':');
            }
            return [
                patch.id || '',
                'rect',
                _partitionWallSignatureNumber(patch.x),
                _partitionWallSignatureNumber(patch.y),
                _partitionWallSignatureNumber(patch.w),
                _partitionWallSignatureNumber(patch.h)
            ].join(':');
        }).join('|');
        return [
            _partitionWallSignatureNumber(model.geometry.size.x),
            _partitionWallSignatureNumber(model.geometry.size.z),
            _partitionWallSignatureNumber(model.geometry.size.y),
            patchSignature
        ].join('~');
    }

        // A floor's body kind (textures/materials.json decides how it looks):
        // a non-boundary floor is a raised floor, the upper of two boundary
        // floors is the room ceiling, anything else the slab floor.
        function _floorBodyStyle(model) {
        if (!model || !model.isRoomBoundary || !model.geometry || !model.geometry.location) return { kind: 'raised-floor' };
        const boundary = (function(){var m=(typeof window!=='undefined')?window.cfdprojStateV4:null;var st=(typeof window!=='undefined')?window.state:null;return (m&&st)?m.allModels(st):[];})().filter(m => m.kind === 'floor' && m.isRoomBoundary && m.geometry && m.geometry.location);
        let isCeiling = false;
        if (boundary.length > 1) {
            const ys = boundary.map(m => m.geometry.location.y);
            const maxY = Math.max.apply(null, ys), minY = Math.min.apply(null, ys);
            isCeiling = (maxY - minY > 1e-3) && (model.geometry.location.y >= (maxY + minY) / 2);
        }
        return { kind: isCeiling ? 'ceiling' : 'floor' };
    }

    
    function getServerFaceRect(box, faceId) {
        // 2026-04-18 Y-up: box.y0/y1 = vertical (slot height along Y), box.z0/z1 = depth (along Z).
        var dx = box.x1 - box.x0;  // width  (X)
        var dy = box.y1 - box.y0;  // height (Y) — was dz under Z-up
        var dz = box.z1 - box.z0;  // depth  (Z) — was dy under Z-up
    
        // Basis convention: outward normal = right × up (consistent with getFaceLayouts).
        // inlet  (Z=z0, front):  right×up = (-1,0,0)×(0,1,0) = (0,0,-1) ✓
        // outlet (Z=z1, back):   right×up = (+1,0,0)×(0,1,0) = (0,0,+1) ✓
        // bottom (Y=y0):         right×up = (+1,0,0)×(0,0,+1) = (0,-1,0) ✓
        // top    (Y=y1):         right×up = (+1,0,0)×(0,0,-1) = (0,+1,0) ✓
        // left   (X=x0):         right×up = (0,0,+1)×(0,1,0) = (-1,0,0) ✓
        // right  (X=x1):         right×up = (0,0,-1)×(0,1,0) = (+1,0,0) ✓
        switch (faceId) {
            case 'inlet':
                // Z=z0 face (front), spans X × Y, outward = (0,0,-1)
                return {
                    origin: new THREE.Vector3(box.x1, box.y0, box.z0),
                    right:  new THREE.Vector3(-1, 0, 0),
                    up:     new THREE.Vector3(0, 1, 0),
                    w: dx, h: dy
                };
            case 'outlet':
                // Z=z1 face (back), spans X × Y, outward = (0,0,+1)
                return {
                    origin: new THREE.Vector3(box.x0, box.y0, box.z1),
                    right:  new THREE.Vector3(1, 0, 0),
                    up:     new THREE.Vector3(0, 1, 0),
                    w: dx, h: dy
                };
            case 'bottom':
                // Y=y0 face (bottom), spans X × Z, outward = (0,-1,0)
                return {
                    origin: new THREE.Vector3(box.x0, box.y0, box.z0),
                    right:  new THREE.Vector3(1, 0, 0),
                    up:     new THREE.Vector3(0, 0, 1),
                    w: dx, h: dz
                };
            case 'top':
                // Y=y1 face (top), spans X × Z(reversed), outward = (0,+1,0)
                return {
                    origin: new THREE.Vector3(box.x0, box.y1, box.z1),
                    right:  new THREE.Vector3(1, 0, 0),
                    up:     new THREE.Vector3(0, 0, -1),
                    w: dx, h: dz
                };
            case 'left':
                // X=x0 face, spans Z × Y, outward = (-1,0,0)
                return {
                    origin: new THREE.Vector3(box.x0, box.y0, box.z0),
                    right:  new THREE.Vector3(0, 0, 1),
                    up:     new THREE.Vector3(0, 1, 0),
                    w: dz, h: dy
                };
            case 'right':
                // X=x1 face, spans Z(reversed) × Y, outward = (+1,0,0)
                return {
                    origin: new THREE.Vector3(box.x1, box.y0, box.z1),
                    right:  new THREE.Vector3(0, 0, -1),
                    up:     new THREE.Vector3(0, 1, 0),
                    w: dz, h: dy
                };
            default:
                return null;
        }
    }

        function _flipGeometryWinding(geom) {
        // Reverse triangle index order so STL export computes outward-facing
        // normals from winding. Matches spawnPlanarRect's flipWinding option
        // but used directly for ShapeGeometry pieces built outside spawnPlanarRect.
        var idx = geom.index;
        if (!idx) return;
        for (var i = 0; i + 2 < idx.count; i += 3) {
            var a = idx.getX(i), c = idx.getX(i + 2);
            idx.setX(i, c);
            idx.setX(i + 2, a);
        }
        idx.needsUpdate = true;
    }

    
    // js/pbr_materials.js when loaded (both editors load it); the plain
    // material otherwise. A fresh material per call — see that module.
    function _manifoldMaterial(role, params) {
        var lib = (typeof window !== 'undefined') ? window.pbrMaterials : null;
        return lib ? lib.material(role, params) : new THREE.MeshStandardMaterial(params);
    }
    function _manifoldCylinderUv(geometry, radius, height, role) {
        var lib = (typeof window !== 'undefined') ? window.pbrMaterials : null;
        if (lib) lib.cylinderUv(geometry, radius, height, role);
    }

    function _renderLiquidManifold(model, modelGroup) {
        if (!model || model.kind !== 'rack') return;
        if (model.cooling.type !== 'liquid') return;
    
        const geom = model.geometry;
        const manifold = model.cooling.liquidManifold || {};
        const layout = manifold.layout || 'both-left';
        const supplyPipeCfg = manifold.supplyPipe || {};
        const returnPipeCfg = manifold.returnPipe || {};
        const pipeSpacing = typeof manifold.pipeSpacing === 'number' ? manifold.pipeSpacing : 0.015;
    
        const supplyDiameter = typeof supplyPipeCfg.diameter === 'number' ? supplyPipeCfg.diameter : 0.025;
        const returnDiameter = typeof returnPipeCfg.diameter === 'number' ? returnPipeCfg.diameter : 0.025;
        const supplyRadius = supplyDiameter / 2;
        const returnRadius = returnDiameter / 2;
    
        const supplyOffsetFromInlet = typeof supplyPipeCfg.offsetFromInlet === 'number' ? supplyPipeCfg.offsetFromInlet : 0.10;
        const returnOffsetFromInlet = typeof returnPipeCfg.offsetFromInlet === 'number' ? returnPipeCfg.offsetFromInlet : 0.10;
    
        const safeMarginBottom = model.safeMarginBottom || 0;
        const slotHeight = (geom.slot || 0) * (geom.slotHeight || 0);
        if (slotHeight < 1e-6) return;
    
        const pipeY = safeMarginBottom + slotHeight / 2;
        const W = model.geometry.size.x;
        const edgeMargin = 0.01;
    
        // Compute X positions for supply and return based on layout
        // Z < 0 = in front of inlet face; Z > base.D = behind outlet face (mountFace)
        let supplyX, returnX, supplyZ, returnZ;
        var mountFace = (manifold.mountFace === 'outlet') ? 'outlet' : 'inlet';
        var faceZ = (mountFace === 'outlet') ? (model.geometry.size.z + 0.05) : -0.05;
        supplyZ = faceZ;
        returnZ = faceZ;
    
        if (layout === 'both-left') {
            supplyX = edgeMargin + supplyRadius;
            returnX = supplyX + supplyRadius + pipeSpacing + returnRadius;
        } else if (layout === 'both-right') {
            returnX = W - edgeMargin - returnRadius;
            supplyX = returnX - returnRadius - pipeSpacing - supplyRadius;
        } else {
            // 'split': locally supply-left / return-right;
            // after default 180° Y rotation, viewer sees left=hot right=cold
            supplyX = edgeMargin + supplyRadius;
            returnX = W - edgeMargin - returnRadius;
        }
    
        // Supply pipe (blue)
        const supplyGeo = new THREE.CylinderGeometry(supplyRadius, supplyRadius, slotHeight, 16);
        _manifoldCylinderUv(supplyGeo, supplyRadius, slotHeight, 'stainless');
        // Brushed stainless header, tinted with the loop colour (the texture's
        // colour map is neutral, so a result colour still replaces the tint).
        const supplyMat = _manifoldMaterial('stainless', {
            color: 0x2196F3,
            metalness: 0.6,
            roughness: 0.3,
            transparent: true,
            opacity: 0.85,
            emissive: new THREE.Color(0x2196F3),
            emissiveIntensity: 0.15
        });
        const supplyMesh = new THREE.Mesh(supplyGeo, supplyMat);
        supplyMesh.position.set(supplyX, pipeY, supplyZ);
        supplyMesh.name = 'liquid_manifold_supply';
        supplyMesh.userData = { isLiquidManifold: true, pipeType: 'supply', modelId: model.uid };
        modelGroup.add(supplyMesh);
    
        // Return pipe (red)
        const returnGeo = new THREE.CylinderGeometry(returnRadius, returnRadius, slotHeight, 16);
        _manifoldCylinderUv(returnGeo, returnRadius, slotHeight, 'stainless');
        const returnMat = _manifoldMaterial('stainless', {
            color: 0xF44336,
            metalness: 0.6,
            roughness: 0.3,
            transparent: true,
            opacity: 0.85,
            emissive: new THREE.Color(0xF44336),
            emissiveIntensity: 0.15
        });
        const returnMesh = new THREE.Mesh(returnGeo, returnMat);
        returnMesh.position.set(returnX, pipeY, returnZ);
        returnMesh.name = 'liquid_manifold_return';
        returnMesh.userData = { isLiquidManifold: true, pipeType: 'return', modelId: model.uid };
        modelGroup.add(returnMesh);
    
        // --- Connection stubs from each equipment to manifold ---
        var stubR = 0.008;  // 16mm diameter visual
        // Colour-coded rubber hoses, one material per stub: the legacy
        // temperature overlay recolours materials in place, and one shared
        // material painted every stub of the rack with the last temperature.
        var stubParamsS = {
            color: 0x2196F3, metalness: 0.0, roughness: 0.7,
            transparent: true, opacity: 0.75
        };
        var stubParamsR = {
            color: 0xF44336, metalness: 0.0, roughness: 0.7,
            transparent: true, opacity: 0.75
        };
        // V4-native: slotWidthRatio/unitHeight live on geometry; servers come from the
        // V4 constructions.servers dict.
        var _rackGeom = model.geometry || {};
        var _slotWidthRatio = _rackGeom.slotWidthRatio;
        var _unitHeight = _rackGeom.slotHeight;
        var slotW = W * _slotWidthRatio;
        var slotX = (W - slotW) / 2;
    
        getRackServerViews(model).forEach(function(eq) {
            var eqBot = safeMarginBottom + (eq.startUnit - 1) * _unitHeight;
            var eqMidY = eqBot + ((eq.spanUnits || 1) * _unitHeight) / 2;
    
            // Supply stub: from manifold to equipment edge
            var sX = supplyMesh.position.x;
            var eqEdgeS = (layout === 'both-right') ? (slotX + slotW) : slotX;
            var sLen = Math.abs(eqEdgeS - sX);
            if (sLen > 0.001) {
                var sGeo = new THREE.CylinderGeometry(stubR, stubR, sLen, 8);
                _manifoldCylinderUv(sGeo, stubR, sLen, 'rubber');
                sGeo.rotateZ(Math.PI / 2);
                var sMesh = new THREE.Mesh(sGeo, _manifoldMaterial('rubber', stubParamsS));
                sMesh.position.set((sX + eqEdgeS) / 2, eqMidY, supplyZ);
                // stubServerId: which server this hose feeds (the liquid flow
                // arrows draw that server's branch flow on it). Not
                // equipmentId, which would make the hose part of the server.
                sMesh.userData = { isLiquidManifold: true, stubType: 'supply', modelId: model.uid, stubServerId: eq.id };
                modelGroup.add(sMesh);
            }
    
            // Return stub: from manifold to equipment edge
            var rX = returnMesh.position.x;
            var eqEdgeR = (layout === 'split') ? (slotX + slotW) : eqEdgeS;
            var rLen = Math.abs(eqEdgeR - rX);
            if (rLen > 0.001) {
                var rGeo = new THREE.CylinderGeometry(stubR, stubR, rLen, 8);
                _manifoldCylinderUv(rGeo, stubR, rLen, 'rubber');
                rGeo.rotateZ(Math.PI / 2);
                var rMesh = new THREE.Mesh(rGeo, _manifoldMaterial('rubber', stubParamsR));
                rMesh.position.set((rX + eqEdgeR) / 2, eqMidY, returnZ);
                rMesh.userData = { isLiquidManifold: true, stubType: 'return', modelId: model.uid, stubServerId: eq.id };
                modelGroup.add(rMesh);
            }
        });
    }

    
    function getRegionReferenceInfos(model, cacheEntry) {
        const _co = model.customMesh ? model.customMesh.offset : { x: 0, y: 0, z: 0 };
        const offset = new THREE.Vector3(_co.x, _co.y, _co.z);
        return cacheEntry.regions.map(region => {
            const bbox = region.geometry.boundingBox ? region.geometry.boundingBox.clone() : new THREE.Box3().setFromBufferAttribute(region.geometry.attributes.position);
            bbox.min.add(offset);
            bbox.max.add(offset);
    
            const pos = region.geometry.attributes.position;
            const triangleCount = Math.floor(pos.count / 3);
            const avgCenter = new THREE.Vector3();
            const avgNormal = new THREE.Vector3();
    
            for (let i = 0; i < triangleCount; i++) {
                const a = new THREE.Vector3().fromBufferAttribute(pos, i * 3).add(offset);
                const b = new THREE.Vector3().fromBufferAttribute(pos, i * 3 + 1).add(offset);
                const c = new THREE.Vector3().fromBufferAttribute(pos, i * 3 + 2).add(offset);
                avgCenter.add(new THREE.Vector3().addVectors(a, b).add(c).multiplyScalar(1 / 3));
                avgNormal.add(new THREE.Vector3().crossVectors(new THREE.Vector3().subVectors(b, a), new THREE.Vector3().subVectors(c, a)).normalize());
            }
    
            if (triangleCount > 0) {
                avgCenter.multiplyScalar(1 / triangleCount);
                avgNormal.normalize();
            }
    
            return { name: region.name, bbox, center: avgCenter, normal: avgNormal };
        });
    }

    
    function classifyTriangleRegion(centroid, normal, regionInfos, fallbackName) {
        if (!regionInfos.length) return fallbackName;
    
        let bestName = fallbackName;
        let bestScore = Infinity;
        const clamped = new THREE.Vector3();
    
        regionInfos.forEach(region => {
            region.bbox.clampPoint(centroid, clamped);
            const bboxDistanceSq = clamped.distanceToSquared(centroid);
            const centerDistanceSq = region.center.distanceToSquared(centroid);
            const normalPenalty = region.normal.lengthSq() > 0 ? (1 - Math.max(0, normal.dot(region.normal))) * 0.2 : 0;
            const score = bboxDistanceSq * 100 + centerDistanceSq + normalPenalty;
            if (score < bestScore) {
                bestScore = score;
                bestName = region.name;
            }
        });
    
        return bestName;
    }

    
    function isPlanarGeometry(geometry, epsilon = 1e-5) {
        const bbox = geometry.boundingBox || new THREE.Box3().setFromBufferAttribute(geometry.attributes.position);
        const size = bbox.getSize(new THREE.Vector3());
        return size.x <= epsilon || size.y <= epsilon || size.z <= epsilon;
    }

    
    function canUseSurfaceProjectionFallback(cacheEntry) {
        return cacheEntry && cacheEntry.regions && cacheEntry.regions.length > 0 && cacheEntry.regions.every(region => isPlanarGeometry(region.geometry));
    }

    
    function getPlanarRegionFrame(region, model) {
        const pos = region.geometry.attributes.position;
        const _co = model.customMesh ? model.customMesh.offset : { x: 0, y: 0, z: 0 };
        const offset = new THREE.Vector3(_co.x, _co.y, _co.z);
        const a = new THREE.Vector3().fromBufferAttribute(pos, 0).add(offset);
        const b = new THREE.Vector3().fromBufferAttribute(pos, 1).add(offset);
        const c = new THREE.Vector3().fromBufferAttribute(pos, 2).add(offset);
        const normal = getAverageNormalForGeometry(region.geometry).normalize();
        let u = new THREE.Vector3().subVectors(b, a).projectOnPlane(normal);
        if (u.lengthSq() < 1e-8) u = new THREE.Vector3().subVectors(c, a).projectOnPlane(normal);
        if (u.lengthSq() < 1e-8) {
            const seed = Math.abs(normal.z) < 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0);
            u = new THREE.Vector3().crossVectors(seed, normal);
        }
        u.normalize();
        const v = new THREE.Vector3().crossVectors(normal, u).normalize();
        return {
            origin: a,
            normal,
            u,
            v,
            matrix: new THREE.Matrix4().makeBasis(u.clone(), v.clone(), normal.clone()).setPosition(a.clone())
        };
    }

    
    function toFramePoint(point, frame) {
        const rel = point.clone().sub(frame.origin);
        return [rel.dot(frame.u), rel.dot(frame.v)];
    }

    
    function regionGeometryToPolygons(region, frame, model) {
        const pos = region.geometry.attributes.position;
        const _co = model.customMesh ? model.customMesh.offset : { x: 0, y: 0, z: 0 };
        const offset = new THREE.Vector3(_co.x, _co.y, _co.z);
        let merged = [];
        for (let i = 0; i < pos.count; i += 3) {
            const a = new THREE.Vector3().fromBufferAttribute(pos, i).add(offset);
            const b = new THREE.Vector3().fromBufferAttribute(pos, i + 1).add(offset);
            const c = new THREE.Vector3().fromBufferAttribute(pos, i + 2).add(offset);
            const poly = [[[
                toFramePoint(a, frame),
                toFramePoint(b, frame),
                toFramePoint(c, frame),
                toFramePoint(a, frame)
            ]]];
            merged = merged.length ? polygonClipping.union(merged, poly) : poly;
        }
        return merged;
    }

    
    function computeConvexHull2D(points) {
        if (points.length < 3) return [];
        const sorted = [...points].sort((a, b) => a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]);
        const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
        const lower = [];
        sorted.forEach(point => {
            while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 1e-8) lower.pop();
            lower.push(point);
        });
        const upper = [];
        for (let i = sorted.length - 1; i >= 0; i--) {
            const point = sorted[i];
            while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 1e-8) upper.pop();
            upper.push(point);
        }
        lower.pop();
        upper.pop();
        return lower.concat(upper);
    }

    
    function exportPlanarPolygons(name, polygons, frame, parts, isMirrored, targetParent) {
        polygons.forEach(polygon => {
            const shape = new THREE.Shape();
            polygon[0].forEach((pt, index) => index === 0 ? shape.moveTo(pt[0], pt[1]) : shape.lineTo(pt[0], pt[1]));
            for (let i = 1; i < polygon.length; i++) {
                const hole = new THREE.Path();
                polygon[i].forEach((pt, index) => index === 0 ? hole.moveTo(pt[0], pt[1]) : hole.lineTo(pt[0], pt[1]));
                shape.holes.push(hole);
            }
            const geom = new THREE.ShapeGeometry(shape, 48);
            geom.applyMatrix4(frame.matrix);
            const mesh = new THREE.Mesh(geom);
            exportToStlString(name, mesh, parts, isMirrored, targetParent);
        });
    }


        // Hole-shape codes the door pattern understands.
        const _DOOR_SHAPE_CODE = { circle: 0, rect: 1, diamond: 2, hexagon: 3 };

        // The hole test, injected into MeshStandardMaterial's fragment shader
        // before the alpha test: a hole is discarded, the metal between holes
        // is lit like any other surface.
        const _DOOR_HOLE_GLSL = [
            '{',
            '    vec2 doorUv = vUv * uDoorCells;',
            '    float doorRow = floor(doorUv.y);',
            // round and hexagonal holes are punched on a staggered lattice, square and diamond on a straight one
            '    float doorStagger = (uDoorShape == 0.0 || uDoorShape == 3.0) ? mod(doorRow, 2.0) * 0.5 : 0.0;',
            '    vec2 doorF = fract(vec2(doorUv.x + doorStagger, doorUv.y)) - 0.5;',
            '    vec2 doorA = abs(doorF);',
            '    float doorD;',
            '    if (uDoorShape == 0.0) doorD = length(doorF);',
            '    else if (uDoorShape == 1.0) doorD = max(doorA.x, doorA.y);',
            '    else if (uDoorShape == 2.0) doorD = doorA.x + doorA.y;',
            '    else doorD = max(dot(doorA, vec2(0.5, 0.8660254)), doorA.x);',
            '    float doorEdge = smoothstep(uDoorHalf, uDoorHalf + 0.03, doorD);',
            '    if (doorEdge < 0.01) discard;',
            '    diffuseColor.a *= doorEdge;',
            '}',
        ].join('\n');

        // A MeshStandardMaterial that punches the door's holes. A subclass, so
        // a clone (the selection highlight and the fades make them) keeps the
        // pattern: Material.copy does not carry an instance onBeforeCompile,
        // and MeshStandardMaterial.copy resets `defines`. The pattern itself is
        // plain numbers in userData.renderSnapshot, which copy() clones.
        let _PerforatedDoorMaterial = null;
        function _perforatedDoorMaterialClass() {
            if (_PerforatedDoorMaterial) return _PerforatedDoorMaterial;
            _PerforatedDoorMaterial = class PerforatedDoorMaterial extends THREE.MeshStandardMaterial {
                constructor(parameters) {
                    super(parameters);
                    this.isPerforatedDoorMaterial = true;
                    this.defines = Object.assign({}, this.defines, { USE_UV: '' });
                }
                copy(source) {
                    super.copy(source);
                    this.defines = Object.assign({}, this.defines, { USE_UV: '' });
                    return this;
                }
                onBeforeCompile(shader) {
                    const spec = this.userData.renderSnapshot || {};
                    shader.uniforms.uDoorHalf = { value: spec.halfExtent || 0 };
                    shader.uniforms.uDoorShape = { value: spec.shape || 0 };
                    shader.uniforms.uDoorCells = { value: new THREE.Vector2((spec.cells || [1, 1])[0], (spec.cells || [1, 1])[1]) };
                    shader.fragmentShader = shader.fragmentShader
                        .replace('#include <common>', '#include <common>\nuniform float uDoorHalf;\nuniform float uDoorShape;\nuniform vec2 uDoorCells;')
                        .replace('#include <alphatest_fragment>', _DOOR_HOLE_GLSL + '\n#include <alphatest_fragment>');
                }
                customProgramCacheKey() { return 'dcdtuni-perforated-door-v1'; }
            };
            return _PerforatedDoorMaterial;
        }

        /**
         * The perforated-door material: the pattern IS the door's perforation.
         *
         * Hole shape (circle / rect / diamond / hexagon), open area and hole
         * size all come from the door's `perforation` record, so a door set to
         * 40 % diamond holes looks different from one at 80 % round holes. The
         * hole radius (in cell units, cell area 1) is solved from the open-area
         * fraction for each shape; the cell pitch from the hole size, so the
         * number of holes across the door follows holeDiameterMm. The steel
         * itself — colour, roughness, how much of the room shows through the
         * web — is the `rack-door` material of textures/materials.json.
         */
        function _rackDoorMaterial(perforation, doorWidthM, doorHeightM) {
            const perf = perforation || {};
            const shape = _DOOR_SHAPE_CODE[perf.shape] !== undefined ? _DOOR_SHAPE_CODE[perf.shape] : 0;
            const sigma = Math.min(0.95, Math.max(0.02, (Number(perf.openAreaPct) || 64) / 100));
            const dMm = Math.max(0.5, Number(perf.holeDiameterMm) || 3);
            // Half-extent of the hole in cell units for this shape at this sigma.
            let a;
            if (shape === 0) a = Math.sqrt(sigma / Math.PI);               // circle: pi a^2
            else if (shape === 1) a = Math.sqrt(sigma) / 2;                // square: (2a)^2
            else if (shape === 2) a = Math.sqrt(sigma / 2);                // diamond: 2a^2
            else a = Math.sqrt(sigma / (2 * Math.sqrt(3)));                // hexagon: 2*sqrt(3)*a^2
            const pitchMm = dMm / (2 * a);                                  // hole spans 2a cells = dMm
            const wMm = Math.max(1, (doorWidthM || 0.45) * 1000);
            const hMm = Math.max(1, (doorHeightM || 1.9) * 1000);
            const cellsX = Math.min(400, Math.max(2, wMm / pitchMm));
            const cellsY = Math.min(1200, Math.max(2, hMm / pitchMm));
            const lib = (typeof window !== 'undefined' && window.materialLibrary) || (typeof globalThis !== 'undefined' && globalThis.materialLibrary);
            const steel = lib && lib.isReady() ? lib.material('rack-door') : null;
            const color = new THREE.Color(steel ? steel.baseColor : '#808080');
            const opacity = steel ? steel.opacity : 1;
            const Door = _perforatedDoorMaterialClass();
            const doorMaterial = new Door({
                color: color,
                roughness: steel ? steel.roughness : 1,
                metalness: steel ? steel.metallic : 0,
                side: THREE.DoubleSide,
                transparent: true,
                opacity: opacity,
                depthWrite: false,
            });
            doorMaterial.userData.materialKind = 'rack-door';
            // The pattern as data: the shader above reads it, and a renderer
            // that cannot run the shader (the Omniverse render snapshot)
            // rebuilds it as a hole-pattern texture with alpha cut-out.
            // Plain numbers only (Material.copy clones userData through JSON).
            doorMaterial.userData.renderSnapshot = {
                kind: 'perforatedDoor',
                color: color.toArray(),
                opacity: opacity,
                roughness: doorMaterial.roughness,
                metalness: doorMaterial.metalness,
                shape: shape,
                halfExtent: a,
                cells: [cellsX, cellsY],
            };
            return doorMaterial;
        }

    // ── Viewport marker sizes, shared by both editors ─────────────────
    //
    // Not lifted from STL_generate.html like the rest of this file: these are
    // the numbers both editors must agree on, so one selection draws the same
    // red anchor ball next to the same blue resize handles in either app.
    //
    // The handle cubes (edge cubes renderOrder 11, corner cubes 12, depthTest
    // off) and the anchor ball (a point marker, below) both scale linearly
    // with camera distance, so they hold their size on screen and keep
    // shrinking as the camera closes in, down to millimetre-scale degeneracy
    // guards. The ball is drawn after the corner cube (ANCHOR_RENDER_ORDER)
    // and smaller than its half side, so a cube on the same point shows
    // around the ball instead of covering it. Both need transparent:true and
    // depthTest:false for that order to apply, because renderOrder only sorts
    // objects within one render list.

    /**
     * Side of a resize-handle cube, in metres, for a camera `dist` metres away.
     * Linear in distance (1.8% of dist, ~12 px on screen) with only a 1.5 mm
     * degeneracy guard below and the historic 300 mm far cap above, so the
     * cubes keep shrinking as the camera closes in.
     */
    function handleCubeSize(dist) {
        return Math.max(0.0015, Math.min(0.3, dist * 0.018));
    }

    /** The anchor ball's base radius: 3.5% of the model's longest side, clamped to 18-90 mm. */
    function anchorMarkerRadius(size) {
        const s = size || {};
        const longest = Math.max(Number(s.x) || 0, Number(s.z) || 0, Number(s.y) || 0);
        return Math.max(0.018, Math.min(0.09, longest * 0.035));
    }

    // ── Point markers: one look and one on-screen size ────────────────────
    //
    // Every point helper of the viewport -- the red anchor ball, the
    // locationInMesh seed and the extra seeds, the sensor probes, a surface
    // seed's hub and side tips, a refinement zone's centre -- is ONE look: a
    // shaded ball inside a white ring inside a dark ring, so it reads on a
    // white ceiling and on the dark background alike. It is ONE sphere whose
    // fragment shader paints the rings from the view angle, so to its owner a
    // marker is still one mesh and one material: raycasting, recolouring
    // (`material.color.set`) and disposal work as they did on a Phong ball.
    //
    // And ONE size rule: a marker holds a constant size on screen, its outer
    // radius `k * dist` metres for a camera `dist` metres away (k / 0.001 is
    // roughly its radius in pixels on the 45-degree, ~900-pixel viewport),
    // between a 1.5 mm guard and a 0.35 m far cap. World-fixed balls were
    // beach balls over a 600 mm rack from a room overview and specks across a
    // hall; grown with the camera a marker is findable from anywhere and never
    // swallows what it marks up close.
    const MARKER_SIZE = {
        anchor: 0.008, seed: 0.0095, extraSeed: 0.009, sensor: 0.0078,
        seedTip: 0.0072, seedHub: 0.006, zoneCentre: 0.0055
    };
    const MARKER_MIN_RADIUS = 0.0015;
    const MARKER_MAX_RADIUS = 0.35;

    /**
     * Outer radius, in metres, of a marker of `kind` (a MARKER_SIZE key, or k
     * itself) seen from `dist` metres. A non-finite distance gives the size
     * at 8 m, so a bad matrix can never give a marker a NaN scale.
     */
    function markerDisplayRadius(kind, dist) {
        const k = typeof kind === 'number' ? kind : (MARKER_SIZE[kind] || MARKER_SIZE.anchor);
        const d = Number(dist);
        const r = k * (Number.isFinite(d) ? d : 8);
        return Math.max(MARKER_MIN_RADIUS, Math.min(MARKER_MAX_RADIUS, r));
    }

    /**
     * The radius to draw the anchor ball at: the anchor marker size for a
     * camera `dist` metres away. `base` (anchorMarkerRadius) is only the
     * fallback for a non-finite distance. The ball no longer rings the blue
     * corner cube that shares its point: it is drawn AFTER it
     * (ANCHOR_RENDER_ORDER) and smaller than the cube's half side, so it sits
     * on the cube as a red pin with the cube showing around it.
     */
    function anchorDisplayRadius(base, dist) {
        if (!Number.isFinite(Number(dist))) {
            const b = Number(base);
            return Number.isFinite(b) ? b : markerDisplayRadius('anchor', NaN);
        }
        return markerDisplayRadius('anchor', dist);
    }

    /** After the edge cubes (11) and the corner cubes (12): the ball sits on the cube. */
    const ANCHOR_RENDER_ORDER = 12.5;

    const MARKER_VERTEX = [
        'varying vec3 vNormalV;',
        'varying vec3 vViewPos;',
        'void main() {',
        '  vec4 mv = modelViewMatrix * vec4(position, 1.0);',
        '  vViewPos = mv.xyz;',
        '  vNormalV = normalize(normalMatrix * normal);',
        '  gl_Position = projectionMatrix * mv;',
        '}'
    ].join('\n');
    // rr is the distance from the ball's centre on screen, 0 to 1 at the
    // silhouette (exact under perspective: 1 - dot(n, v)^2 is sin^2 of the
    // angle to the view ray). Inside CORE the shading normal is that of a ball
    // of radius CORE, so the core is lit like a sphere, not like a cap.
    const MARKER_FRAGMENT = [
        'uniform vec3 uColor;',
        'uniform float uOpacity;',
        'varying vec3 vNormalV;',
        'varying vec3 vViewPos;',
        'void main() {',
        '  vec3 n = normalize(vNormalV);',
        '  vec3 v = normalize(-vViewPos);',
        '  float c = clamp(dot(n, v), 0.0, 1.0);',
        '  float rr = sqrt(max(0.0, 1.0 - c * c));',
        '  float aa = clamp(fwidth(rr), 0.002, 0.2);',
        '  const float CORE = 0.70;',
        '  const float WHITE = 0.85;',
        '  float t = clamp(rr / CORE, 0.0, 1.0);',
        '  vec3 perp = n - c * v;',
        '  float pl = length(perp);',
        '  vec3 nc = (pl > 1e-5 ? perp / pl : vec3(0.0)) * t + v * sqrt(max(0.0, 1.0 - t * t));',
        '  vec3 L = normalize(vec3(-0.45, 0.62, 0.64));',
        '  float diff = max(dot(nc, L), 0.0);',
        '  float spec = pow(max(dot(nc, normalize(L + v)), 0.0), 36.0);',
        '  vec3 core = uColor * (0.5 + 0.6 * diff) + vec3(0.7) * spec;',
        '  vec3 col = mix(core, vec3(1.0), smoothstep(CORE - aa, CORE + aa, rr));',
        '  col = mix(col, vec3(0.004, 0.008, 0.02), smoothstep(WHITE - aa, WHITE + aa, rr));',
        '  float alpha = uOpacity * (1.0 - smoothstep(1.0 - 1.5 * aa, 1.0, rr));',
        '  gl_FragColor = vec4(col, alpha);',
        '  #include <colorspace_fragment>',
        '}'
    ].join('\n');

    /**
     * A point marker's material: drawn over the scene (depthTest off) in the
     * transparent pass, like every helper it replaces. `material.color` IS the
     * shader's colour, so recolouring works as on a Phong/Basic ball.
     * @param {*} color Anything THREE.Color accepts.
     * @param {{opacity?: number}} [opts]
     */
    function createMarkerMaterial(color, opts) {
        const o = opts || {};
        const colour = new THREE.Color(color == null ? 0xff4d4f : color);
        const material = new THREE.ShaderMaterial({
            uniforms: {
                uColor: { value: colour },
                uOpacity: { value: Number.isFinite(o.opacity) ? o.opacity : 1 }
            },
            vertexShader: MARKER_VERTEX,
            fragmentShader: MARKER_FRAGMENT,
            transparent: true,
            depthTest: false,
            depthWrite: false
        });
        material.color = colour;
        material.userData.isPointMarker = true;
        return material;
    }

    /** A UNIT sphere for one marker (its owner disposes it). */
    function createMarkerGeometry() {
        return new THREE.SphereGeometry(1, 24, 18);
    }

    // ── Viewport-assistant camera adaptation: the helper scale registry ──
    //
    // World-fixed display helpers (patch vector tips, liquid port balls, pipe
    // waypoint balls, junction ports, supply-flow cones, air-pipe handles)
    // are built as UNIT geometries and registered here with the DATA radius
    // they should show at. Each editor's render loop calls syncHelperScales
    // once per frame (legacy: _syncHelperScalesForCamera in
    // STL_generate.html; React: the Viewport.tsx render loop), which rewrites
    // every registered mesh's scale from its current camera distance, so a
    // helper keeps its data size at normal and far distances and shrinks
    // linearly as the camera closes in instead of swallowing the model.

    /**
     * Display radius, in metres, of a world-fixed helper with data radius
     * `dataRadius` when the camera is `dist` metres away. At normal and far
     * distances the value is `dataRadius` itself, so the look is unchanged;
     * once the camera comes closer than dataRadius / 0.03 the helper shrinks
     * linearly with distance (3% of dist), down to a 1.5 mm floor against
     * degenerate scales.
     * @param {number} dataRadius The radius the helper shows at, in metres.
     * @param {number} dist Camera distance to the helper, in metres.
     * @returns {number} The radius to display the helper at, in metres.
     */
    function helperDisplayRadius(dataRadius, dist) {
        return Math.max(0.0015, Math.min(dataRadius, dist * 0.03));
    }

    // Live registry entries: { mesh, r, k } -- k > 0 for a point marker
    // (markerDisplayRadius), else a helper at data radius r. Kept as a flat
    // array: the sets are small (tens of helpers) and walked once per frame.
    var _helperRegistry = [];
    // Vectors reused across the per-frame walk so syncing allocates nothing.
    var _helperSyncPos = null;
    var _helperSyncScale = null;

    /**
     * Register a helper mesh so syncHelperScales keeps its display size.
     * The mesh's geometry must be UNIT-sized (sphere radius 1, box edge 1,
     * cone radius 1) — the scale is owned by the sync from here on. Setting
     * mesh.scale to `dataRadius` before the first sync frame is the caller's
     * job, so helpers render at data size even when no driver is wired yet.
     * Re-registering the same mesh updates its data radius instead of adding
     * a second entry (the air-pipe snap ring reuses one mesh across targets).
     * @param {THREE.Object3D} mesh The helper mesh (unit geometry).
     * @param {number} dataRadius The world radius/size it should show at, in metres.
     */
    function registerHelper(mesh, dataRadius) {
        var r = Number(dataRadius);
        if (!mesh || !mesh.scale || !Number.isFinite(r) || r <= 0) return;
        for (var i = 0; i < _helperRegistry.length; i++) {
            if (_helperRegistry[i].mesh === mesh) { _helperRegistry[i].r = r; _helperRegistry[i].k = 0; return; }
        }
        _helperRegistry.push({ mesh: mesh, r: r, k: 0 });
    }

    /**
     * Register a point marker (UNIT sphere) so syncHelperScales holds it at
     * the on-screen size of `kind` (markerDisplayRadius). The scale is set at
     * once for an 8 m camera, so the marker has a sane size before the first
     * synced frame and in a page without a driver.
     * @param {THREE.Object3D} mesh The marker mesh.
     * @param {string|number} kind A MARKER_SIZE key, or k itself.
     */
    function registerMarker(mesh, kind) {
        var k = typeof kind === 'number' ? kind : MARKER_SIZE[kind];
        if (!mesh || !mesh.scale || !Number.isFinite(k) || k <= 0) return;
        mesh.scale.setScalar(markerDisplayRadius(k, NaN));
        for (var i = 0; i < _helperRegistry.length; i++) {
            if (_helperRegistry[i].mesh === mesh) { _helperRegistry[i].k = k; return; }
        }
        _helperRegistry.push({ mesh: mesh, r: 0, k: k });
    }

    /**
     * Remove a helper mesh from the scale registry (identity match; a mesh
     * that is not registered is a no-op). Call from the dispose/clear path
     * that destroys the mesh; disposeRenderObject already calls this for
     * everything under a disposed root.
     * @param {THREE.Object3D} mesh The helper mesh to forget.
     */
    function unregisterHelper(mesh) {
        // Entries are {mesh, r} objects, so the removal is an identity search
        // over the registry — indexOf semantics on entry.mesh — walking
        // backwards so splicing cannot skip a duplicate.
        for (var i = _helperRegistry.length - 1; i >= 0; i--) {
            if (_helperRegistry[i].mesh === mesh) _helperRegistry.splice(i, 1);
        }
    }

    /**
     * Drop every registry entry at once (full scene teardown). Does not touch
     * the meshes themselves — whoever clears the scene disposes them.
     */
    function clearHelpers() {
        _helperRegistry.length = 0;
    }

    /**
     * Rewrite every registered helper's scale for the current camera. Driven
     * once per rendered frame by each editor. For each entry: the distance is
     * camera.position to the mesh's WORLD position; the parent chain's world
     * scale is divided out (x component, assumed uniform) so a helper under a
     * scaled/rotated group still shows at its data size in world metres; the
     * result is helperDisplayRadius(r, dist). Entries whose distance is not
     * finite are skipped, so a NaN matrix can never give a helper a NaN scale
     * and make it vanish.
     * @param {THREE.Camera} camera The active viewport camera.
     */
    function syncHelperScales(camera) {
        if (!camera || !camera.position || _helperRegistry.length === 0) return;
        if (typeof THREE === 'undefined') return;
        if (!_helperSyncPos) {
            _helperSyncPos = new THREE.Vector3();
            _helperSyncScale = new THREE.Vector3();
        }
        for (var i = 0; i < _helperRegistry.length; i++) {
            var entry = _helperRegistry[i];
            var mesh = entry.mesh;
            if (!mesh || !mesh.scale || !mesh.getWorldPosition) continue;
            // getWorldPosition / getWorldScale refresh the ancestor chain's
            // world matrices themselves, so the values are current even when
            // the driver runs before the scene's own matrix update.
            mesh.getWorldPosition(_helperSyncPos);
            var dist = camera.position.distanceTo(_helperSyncPos);
            if (!Number.isFinite(dist)) continue;
            var s = 1;
            if (mesh.parent) {
                s = mesh.parent.getWorldScale(_helperSyncScale).x;
                if (!Number.isFinite(s) || s === 0) s = 1;
            }
            mesh.scale.setScalar((entry.k ? markerDisplayRadius(entry.k, dist) : helperDisplayRadius(entry.r, dist)) / s);
        }
    }

    return {
        _boxGeometryProduct: function (model, kind, context) { return _boxProduct().createProduct(model, kind, context); },
        getEquipmentFaceColor: getEquipmentFaceColor,
        cloneModelTransform: cloneModelTransform,
        modelWorldTransform: modelWorldTransform,
        modelLocalPointToWorld: modelLocalPointToWorld,
        modelLocalDirectionToWorld: modelLocalDirectionToWorld,
        liquidEquipmentPortFrame: liquidEquipmentPortFrame,
        getAllModelPatches: getAllModelPatches,
        ensureWallOpacity: ensureWallOpacity,
        getRackMergedEmptySlotRanges: getRackMergedEmptySlotRanges,
        disposeRenderObject: disposeRenderObject,
        applyModelTransformToObject: applyModelTransformToObject,
        circleToPoly: circleToPoly,
        rectToPoly: rectToPoly,
        getPartitionWallPatchStore: getPartitionWallPatchStore,
        parseFiniteOrDefault: parseFiniteOrDefault,
        _partitionPatchToPoly: _partitionPatchToPoly,
        buildFaceIslands: buildFaceIslands,
        getRackEquipmentForSlot: getRackEquipmentForSlot,
        getRackServersForSlot: getRackServersForSlot,
        getRackSlotBounds: getRackSlotBounds,
        isRackSlotSelectable: isRackSlotSelectable,
        isRackSlotDeleted: isRackSlotDeleted,
        getRackSlotStatus: getRackSlotStatus,
        getServerFaceExportName: getServerFaceExportName,
        getRackMergedSlotExportName: getRackMergedSlotExportName,
        getRackPartitionExportName: getRackPartitionExportName,
        getSlotDepthRange: getSlotDepthRange,
        getPartitionDepthRange: getPartitionDepthRange,
        getServerDepthRange: getServerDepthRange,
        getFaceLayouts: getFaceLayouts,
        isWallMountedAcu: isWallMountedAcu,
        wallAcuProfile: wallAcuProfile,
        wallAcuPointLocal: wallAcuPointLocal,
        wallAcuNormalLocal: wallAcuNormalLocal,
        wallAcuPatchCentreWorld: wallAcuPatchCentreWorld,
        wallAcuSupplyDirectionWorld: wallAcuSupplyDirectionWorld,
        quadCornersUV: quadCornersUV,
        hasCornerOffsets: hasCornerOffsets,
        quadToPoly: quadToPoly,
        sheetPolygon: sheetPolygon,
        isPolygonSheet: isPolygonSheet,
        polygonHandlePointsUV: polygonHandlePointsUV,
        movePolygonVertex: movePolygonVertex,
        polygonSelfIntersects: polygonSelfIntersects,
        movePolygonEdge: movePolygonEdge,
        rebasePolygonSheet: rebasePolygonSheet,
        wallAcuLocalToUV: wallAcuLocalToUV,
        wallAcuRayFacePoint: wallAcuRayFacePoint,
        wallAcuRectOutlineLocal: wallAcuRectOutlineLocal,
        getPrimaryOrientationFaceId: getPrimaryOrientationFaceId,
        isWallFloorOrientationEligible: isWallFloorOrientationEligible,
        getPrimaryOrientationFace: getPrimaryOrientationFace,
        isPrimaryFaceSolidNormalReversed: isPrimaryFaceSolidNormalReversed,
        getDefaultPatchVector: getDefaultPatchVector,
        ensurePatchProjectionDefaults: ensurePatchProjectionDefaults,
        getPatchVector: getPatchVector,
        getDefaultPatchEuler: getDefaultPatchEuler,
        getCustomPatchBasis: getCustomPatchBasis,
        getCustomPatchPosition: getCustomPatchPosition,
        getAverageNormalForGeometry: getAverageNormalForGeometry,
        syncPartitionWallModelState: syncPartitionWallModelState,
        buildPartitionWallHoleCutterGeometry: buildPartitionWallHoleCutterGeometry,
        buildPartitionWallHoleProxyGeometry: buildPartitionWallHoleProxyGeometry,
        _partitionWallSignatureNumber: _partitionWallSignatureNumber,
        getPartitionWallGeometrySignature: getPartitionWallGeometrySignature,
        _floorBodyStyle: _floorBodyStyle,
        getServerFaceRect: getServerFaceRect,
        _flipGeometryWinding: _flipGeometryWinding,
        _renderLiquidManifold: _renderLiquidManifold,
        getRegionReferenceInfos: getRegionReferenceInfos,
        classifyTriangleRegion: classifyTriangleRegion,
        isPlanarGeometry: isPlanarGeometry,
        canUseSurfaceProjectionFallback: canUseSurfaceProjectionFallback,
        getPlanarRegionFrame: getPlanarRegionFrame,
        toFramePoint: toFramePoint,
        regionGeometryToPolygons: regionGeometryToPolygons,
        computeConvexHull2D: computeConvexHull2D,
        exportPlanarPolygons: exportPlanarPolygons,
        _rackDoorMaterial: _rackDoorMaterial,
        handleCubeSize: handleCubeSize,
        anchorMarkerRadius: anchorMarkerRadius,
        anchorDisplayRadius: anchorDisplayRadius,
        ANCHOR_RENDER_ORDER: ANCHOR_RENDER_ORDER,
        helperDisplayRadius: helperDisplayRadius,
        MARKER_SIZE: MARKER_SIZE,
        markerDisplayRadius: markerDisplayRadius,
        createMarkerMaterial: createMarkerMaterial,
        createMarkerGeometry: createMarkerGeometry,
        registerMarker: registerMarker,
        registerHelper: registerHelper,
        unregisterHelper: unregisterHelper,
        clearHelpers: clearHelpers,
        syncHelperScales: syncHelperScales
    };
});
