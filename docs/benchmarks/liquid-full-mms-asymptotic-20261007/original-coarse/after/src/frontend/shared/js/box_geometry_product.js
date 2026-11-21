/**
 * Owned structural BOX/CDU buffers. Numerical functions use only their inputs
 * and the shared pure clipping/triangulation/naming dependencies.
 * Materials, image IO, PBR/image UV adaptation and renderer lifetimes stay in
 * carriers. Buffers are fresh per call and are never project document fields.
 */
(function (root, factory) {
    var common = typeof module === 'object' && module.exports;
    var api = factory(
        common ? require('../vendor/polygon-clipping.umd.js') : root.polygonClipping,
        common ? require('../vendor/earcut.umd.js') : root.dcdtuniEarcut,
        common ? require('./stl_naming.js') : root.stlNaming
    );
    if (common) module.exports = api;
    if (root) root.boxGeometryProduct = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (clipping, earcut, naming) {
    'use strict';
    if (!clipping || typeof clipping.difference !== 'function' || typeof earcut !== 'function'
        || !naming || typeof naming.composeModelScopedExportName !== 'function') {
        throw new Error('box_geometry_product.js needs polygon-clipping, earcut and stl_naming loaded first');
    }

    function circleToPoly(cx, cy, r) {
        const ring = [];
        for (let i = 0; i < 32; i++) ring.push([cx + r * Math.cos(i / 32 * Math.PI * 2), cy + r * Math.sin(i / 32 * Math.PI * 2)]);
        ring.push([ring[0][0], ring[0][1]]);
        return [[ring]];
    }

    function rectToPoly(x, y, w, h) {
        return [[[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]]];
    }

    function quadCornersUV(x, y, w, h, offsets) {
        const o = (i) => (Array.isArray(offsets) && offsets[i]) ? offsets[i] : { x: 0, y: 0 };
        const ox = (i) => Number(o(i).x) || 0, oy = (i) => Number(o(i).y) || 0;
        return [
            [x + ox(0), y + oy(0)],
            [x + w + ox(1), y + oy(1)],
            [x + w + ox(2), y + h + oy(2)],
            [x + ox(3), y + h + oy(3)]
        ];
    }

    function hasCornerOffsets(offsets) {
        return Array.isArray(offsets) && offsets.length === 4
            && offsets.some(o => o && ((Number(o.x) || 0) !== 0 || (Number(o.y) || 0) !== 0));
    }

    function quadToPoly(x, y, w, h, offsets) {
        if (!hasCornerOffsets(offsets)) return rectToPoly(x, y, w, h);
        const c = quadCornersUV(x, y, w, h, offsets);
        return [[[c[0], c[1], c[2], c[3], c[0]]]];
    }

    function buildFaceIslands(model) {
        const facesIslands = {};
        Object.keys(model.geometry.faces).forEach(faceId => {
            const face = model.geometry.faces[faceId];
            // V4 racks store geometry.faces as an array of door/role entries that carry
            // no `.patches`; those models render via renderRackModel and never consume
            // these islands. Skip any face entry that isn't a patch-bearing face dict.
            if (!face || !Array.isArray(face.patches)) return;
            let islands = [];
            face.patches.forEach(patch => {
                const poly = patch.type === 'circle' ? circleToPoly(patch.cx, patch.cy, patch.r) : quadToPoly(patch.x, patch.y, patch.w, patch.h, patch.cornerOffsets);
                let current = { poly, name: naming.getPatchFinalExportName(model, faceId, patch), color: patch.color, refIds: [patch.id] };
                let nextIslands = [];
                islands.forEach(island => {
                    let inter = [];
                    try { inter = clipping.intersection(island.poly, current.poly); } catch (e) {}
                    if (inter && inter.length > 0) {
                        current.poly = clipping.union(current.poly, island.poly);
                        current.refIds.push(...island.refIds);
                        current.name = island.name;
                        current.color = island.color;
                    } else {
                        nextIslands.push(island);
                    }
                });
                nextIslands.push(current);
                islands = nextIslands;
            });
            facesIslands[faceId] = islands;
        });
        return facesIslands;
    }

    function vector(x = 0, y = 0, z = 0) { return [x, y, z]; }
    function frame(origin, right, up, w, h, color, extDepth) {
        var x = right[1] * up[2] - right[2] * up[1];
        var y = right[2] * up[0] - right[0] * up[2];
        var z = right[0] * up[1] - right[1] * up[0];
        var factor = 1 / (Math.sqrt(x * x + y * y + z * z) || 1);
        x *= factor; y *= factor; z *= factor;
        return { origin, right, up, w, h, color, extDepth,
            matrix: [right[0], right[1], right[2], 0, up[0], up[1], up[2], 0,
                x, y, z, 0, origin[0], origin[1], origin[2], 1] };
    }
    function getBoxFaceFrames(model) {
        var size = (model.geometry && model.geometry.size) || {};
        var W = size.x, H = size.y, D = size.z;
        return {
            front: frame(vector(W, 0, 0), vector(-1, 0, 0), vector(0, 1, 0), W, H, '#5c6370', D),
            back: frame(vector(0, 0, D), vector(1, 0, 0), vector(0, 1, 0), W, H, '#7f848e', D),
            left: frame(vector(0, 0, 0), vector(0, 0, 1), vector(0, 1, 0), D, H, '#7f848e', W),
            right: frame(vector(W, 0, D), vector(0, 0, -1), vector(0, 1, 0), D, H, '#7f848e', W),
            top: frame(vector(0, H, D), vector(1, 0, 0), vector(0, 0, -1), W, D, '#9ba0a9', H),
            bottom: frame(vector(0, 0, 0), vector(1, 0, 0), vector(0, 0, 1), W, D, '#4b5263', H)
        };
    }

    // Line paths sample each segment at t=0/1, deduplicate consecutive exact
    // points, then ShapeUtils removes only a duplicate closing endpoint.
    function pathPoints(ring) {
        var points = [];
        function push(p) {
            var last = points[points.length - 1];
            if (!last || last[0] !== p[0] || last[1] !== p[1]) points.push(p);
        }
        for (var i = 1; i < ring.length; i++) {
            var a = ring[i - 1], b = ring[i];
            push([(b[0] - a[0]) * 0 + a[0], (b[1] - a[1]) * 0 + a[1]]);
            push([b[0], b[1]]);
        }
        return points;
    }
    function area(points) {
        var a = 0.0, n = points.length;
        for (var p = n - 1, q = 0; q < n; p = q++) {
            a += points[p][0] * points[q][1] - points[q][0] * points[p][1];
        }
        return a * 0.5;
    }
    function removeClosingPoint(points) {
        var n = points.length;
        if (n > 2 && points[n - 1][0] === points[0][0] && points[n - 1][1] === points[0][1]) points.pop();
    }
    function normalMatrix(e) {
        var n11 = e[0], n21 = e[1], n31 = e[2],
            n12 = e[4], n22 = e[5], n32 = e[6],
            n13 = e[8], n23 = e[9], n33 = e[10];
        var t11 = n33 * n22 - n32 * n23,
            t12 = n32 * n13 - n33 * n12,
            t13 = n23 * n12 - n22 * n13;
        var det = n11 * t11 + n21 * t12 + n31 * t13;
        if (det === 0) return [0, 0, 0, 0, 0, 0, 0, 0, 0];
        var f = 1 / det;
        // Inverse transpose, in the original arithmetic order.
        return [t11 * f, t12 * f, t13 * f,
            (n31 * n23 - n33 * n21) * f, (n33 * n11 - n31 * n13) * f, (n21 * n13 - n23 * n11) * f,
            (n32 * n21 - n31 * n22) * f, (n31 * n12 - n32 * n11) * f, (n22 * n11 - n21 * n12) * f];
    }
    function polygonBuffers(polygon, e) {
        var contour = pathPoints(polygon[0]), holes = polygon.slice(1).map(pathPoints);
        if (!(area(contour) < 0)) {
            contour.reverse();
        }
        holes.forEach(function (hole) { if (area(hole) < 0) hole.reverse(); });
        removeClosingPoint(contour);
        var xy = [], holeIndices = [], count = contour.length;
        contour.forEach(function (p) { xy.push(p[0], p[1]); });
        holes.forEach(function (hole) {
            removeClosingPoint(hole); holeIndices.push(count); count += hole.length;
            hole.forEach(function (p) { xy.push(p[0], p[1]); });
        });
        var triangleIndices = earcut(xy, holeIndices, 2);
        var positions = new Float32Array(count * 3), normals = new Float32Array(count * 3), uvs = new Float32Array(count * 2);
        for (var i = 0; i < count; i++) {
            positions[i * 3] = xy[i * 2]; positions[i * 3 + 1] = xy[i * 2 + 1]; positions[i * 3 + 2] = 0;
            normals[i * 3] = 0; normals[i * 3 + 1] = 0; normals[i * 3 + 2] = 1;
            uvs[i * 2] = xy[i * 2]; uvs[i * 2 + 1] = xy[i * 2 + 1];
        }
        var nm = normalMatrix(e);
        for (var i = 0; i < count; i++) {
            var j = i * 3, x = positions[j], y = positions[j + 1], z = positions[j + 2];
            var w = 1 / (e[3] * x + e[7] * y + e[11] * z + e[15]);
            positions[j] = (e[0] * x + e[4] * y + e[8] * z + e[12]) * w;
            positions[j + 1] = (e[1] * x + e[5] * y + e[9] * z + e[13]) * w;
            positions[j + 2] = (e[2] * x + e[6] * y + e[10] * z + e[14]) * w;
            x = normals[j]; y = normals[j + 1]; z = normals[j + 2];
            var nx = nm[0] * x + nm[3] * y + nm[6] * z;
            var ny = nm[1] * x + nm[4] * y + nm[7] * z;
            var nz = nm[2] * x + nm[5] * y + nm[8] * z;
            var f = 1 / (Math.sqrt(nx * nx + ny * ny + nz * nz) || 1);
            normals[j] = nx * f; normals[j + 1] = ny * f; normals[j + 2] = nz * f;
        }
        var large = triangleIndices.some(function (index) { return index >= 65535; });
        return { positions, normals, uvs, indices: large ? new Uint32Array(triangleIndices) : new Uint16Array(triangleIndices) };
    }
    function wallOpacity(face, index) {
        return Array.isArray(face.wallOpacities) && Number.isFinite(face.wallOpacities[index])
            ? face.wallOpacities[index] : (Number.isFinite(face.opacity) ? face.opacity : 1);
    }
    function createProduct(source, kind, context) {
        if (kind !== 'box' && kind !== 'cdu') throw new Error('Unsupported geometry product kind: ' + kind);
        var model = { ...source, kind }, faces = model.geometry.faces;
        var frames = getBoxFaceFrames(model), islandsByFace = context && context.faceIslands || buildFaceIslands(model);
        var parts = [], dim = context && context.dimOpacity != null ? context.dimOpacity : 1;
        var cduColors = { front: '#007a99', back: '#009ab8', left: '#008aaa', right: '#008aaa', top: '#00b4dc', bottom: '#005e77' };
        Object.keys(faces).forEach(function (faceId) {
            var face = faces[faceId], layout = Object.prototype.hasOwnProperty.call(frames, faceId) && frames[faceId];
            if (!face || !layout) return;
            var islands = islandsByFace[faceId] || [], wall = rectToPoly(0, 0, layout.w, layout.h);
            islands.forEach(function (island, islandIndex) {
                wall = clipping.difference(wall, island.poly);
                island.poly.forEach(function (polygon, polygonIndex) {
                    parts.push({ ...polygonBuffers(polygon, layout.matrix),
                        partId: model.uid + '/' + faceId + '/patch/' + islandIndex + '/' + polygonIndex,
                        faceId, exportName: naming.composeModelScopedExportName(model, faceId, face,
                            { isPatch: true, patchIndex: islandIndex, patchCount: islands.length }),
                        refIds: island.refIds.slice(), islandIndex, wallIndex: undefined, isBaseWall: false,
                        materialRecipe: { role: 'structural-patch', uvMode: 'raw',
                            color: island.color, opacity: (face.opacity ?? 1) * dim,
                            source: { ownerUid: model.uid, faceId, patchIds: island.refIds.slice() } } });
                });
            });
            wall.forEach(function (polygon, wallIndex) {
                parts.push({ ...polygonBuffers(polygon, layout.matrix),
                    partId: model.uid + '/' + faceId + '/wall/' + wallIndex,
                    faceId, exportName: naming.composeModelScopedExportName(model, faceId, face,
                        { isWallPart: true, partIndex: wallIndex, partCount: wall.length }),
                    refIds: [], wallIndex, isBaseWall: true,
                    materialRecipe: { role: 'structural-wall', uvMode: 'raw',
                        color: kind === 'cdu' ? cduColors[faceId] || '#00b4dc' : layout.color,
                        opacity: wallOpacity(face, wallIndex) * dim,
                        source: { ownerUid: model.uid, faceId, patchIds: [] } } });
            });
        });
        return { schema: 'dcdtuni.geometry-product/v1', modelUid: model.uid, kind, parts };
    }
    return Object.freeze({ createProduct, getBoxFaceFrames, buildFaceIslands,
        circleToPoly, rectToPoly, quadCornersUV, hasCornerOffsets, quadToPoly });
});
