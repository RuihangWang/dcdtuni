/**
 * stl_writer.js — turning Three.js meshes into ASCII STL facets.
 *
 * This is the ATOM of the whole STL export: every solid in every exported file
 * is ultimately these three functions run over a mesh. Lifted out of
 * STL_generate.html verbatim so the React editor can produce byte-identical
 * output instead of growing a second implementation.
 *
 * A second implementation is the dangerous kind here. A flipped normal, a
 * dropped degenerate triangle, or one fewer decimal place all still produce a
 * valid STL that renders correctly — and an OpenFOAM patch that is silently
 * wrong. `parity/stl_oracle.py` exists to compare exports byte for byte for
 * exactly this reason.
 *
 * Public API:
 *   stlWriter.triangleHasCoincidentVertices(a, b, c, epsSq?) -> bool
 *   stlWriter.getTriangleFacetData(vA, vB, vC, areaEps?)     -> {normal,vA,vB,vC} | null
 *   stlWriter.appendMeshFacets(parts, mesh, targetParent)    -> void
 *   stlWriter.exportToStlString(name, mesh, parts, isMirrored, targetParent) -> void
 *
 * `THREE` must be a global before this loads (both apps vendor r184 globally).
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.stlWriter = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    function _three() {
        var T = (typeof THREE !== 'undefined') ? THREE
              : (typeof window !== 'undefined' ? window.THREE : undefined);
        if (!T) throw new Error('stl_writer.js needs THREE as a global');
        return T;
    }

    function triangleHasCoincidentVertices(a, b, c, epsSq) {
        if (epsSq === undefined) epsSq = 1e-18;
        return a.distanceToSquared(b) <= epsSq
            || b.distanceToSquared(c) <= epsSq
            || c.distanceToSquared(a) <= epsSq;
    }

    /**
     * The unit normal of a triangle, or null when the triangle is degenerate.
     *
     * Degenerate triangles are DROPPED, not emitted with a zero normal: a facet
     * with a zero-length normal makes surfaceFeatureExtract and snappyHexMesh
     * disagree about the surface, and the mesh then fails somewhere far from
     * the cause.
     */
    function getTriangleFacetData(vA, vB, vC, areaEps) {
        if (areaEps === undefined) areaEps = 1e-12;
        var THREE = _three();
        if (triangleHasCoincidentVertices(vA, vB, vC)) return null;
        var cross = new THREE.Vector3().crossVectors(
            new THREE.Vector3().subVectors(vB, vA),
            new THREE.Vector3().subVectors(vC, vA)
        );
        var area2 = cross.length();
        if (!Number.isFinite(area2) || area2 <= areaEps) return null;
        var normal = cross.multiplyScalar(1 / area2);
        return { normal: normal, vA: vA, vB: vB, vC: vC };
    }

    /**
     * Push one mesh's facets, in WORLD coordinates, onto `parts`.
     *
     * `targetParent` is only used for a mesh that is not in the scene yet: it is
     * temporarily parented so `updateMatrixWorld` produces the same transform an
     * attached mesh would get, then removed again. Skipping that would export
     * such a mesh in local coordinates.
     *
     * A mirrored world matrix (negative determinant) swaps two vertices, because
     * mirroring reverses winding and the facet normal has to keep pointing at
     * the fluid.
     */
    function appendMeshFacets(parts, mesh, targetParent) {
        var THREE = _three();
        if (mesh.userData && mesh.userData.isLiquidManifold) return;
        var pos = mesh.geometry.attributes.position;
        var index = mesh.geometry.index;
        var count = index ? index.count : pos.count;
        if (count === 0) return;

        var originalParent = mesh.parent;
        var parent = originalParent || targetParent;
        if (!originalParent) {
            if (!parent) throw new Error('appendMeshFacets: orphan mesh needs a targetParent');
            parent.add(mesh);
        }
        mesh.updateMatrixWorld(true);
        var mirrored = mesh.matrixWorld.determinant() < 0;

        for (var i = 0; i < count; i += 3) {
            var a = index ? index.getX(i) : i;
            var b = index ? index.getX(i + 1) : i + 1;
            var c = index ? index.getX(i + 2) : i + 2;
            if (mirrored) { var tmp = b; b = c; c = tmp; }

            var vA = new THREE.Vector3().fromBufferAttribute(pos, a).applyMatrix4(mesh.matrixWorld);
            var vB = new THREE.Vector3().fromBufferAttribute(pos, b).applyMatrix4(mesh.matrixWorld);
            var vC = new THREE.Vector3().fromBufferAttribute(pos, c).applyMatrix4(mesh.matrixWorld);
            var tri = getTriangleFacetData(vA, vB, vC);
            if (!tri) continue;
            // Six decimals, exactly as before. The digit count is part of the
            // output contract: stl_oracle.py compares exports byte for byte, and
            // watertightness depends on adjacent solids rounding a shared vertex
            // to the same string.
            parts.push('  facet normal ' + tri.normal.x.toFixed(6) + ' ' + tri.normal.y.toFixed(6) + ' ' + tri.normal.z.toFixed(6) +
                '\n    outer loop\n      vertex ' + tri.vA.x.toFixed(6) + ' ' + tri.vA.y.toFixed(6) + ' ' + tri.vA.z.toFixed(6) +
                '\n      vertex ' + tri.vB.x.toFixed(6) + ' ' + tri.vB.y.toFixed(6) + ' ' + tri.vB.z.toFixed(6) +
                '\n      vertex ' + tri.vC.x.toFixed(6) + ' ' + tri.vC.y.toFixed(6) + ' ' + tri.vC.z.toFixed(6) +
                '\n    endloop\n  endfacet\n');
        }

        if (!originalParent) parent.remove(mesh);
    }

    /** One named solid block. `isMirrored` is accepted and unused, as before. */
    function exportToStlString(name, mesh, parts, isMirrored, targetParent) {
        parts.push('solid ' + name + '\n');
        appendMeshFacets(parts, mesh, targetParent);
        parts.push('endsolid ' + name + '\n\n');
    }

    return {
        triangleHasCoincidentVertices: triangleHasCoincidentVertices,
        getTriangleFacetData: getTriangleFacetData,
        appendMeshFacets: appendMeshFacets,
        exportToStlString: exportToStlString
    };
});
