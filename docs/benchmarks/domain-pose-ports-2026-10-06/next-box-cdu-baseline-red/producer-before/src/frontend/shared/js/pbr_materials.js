/**
 * pbr_materials.js — physically based materials for the liquid-cooling equipment.
 *
 * Shared by BOTH editors (the legacy page loads it with a <script>, React
 * imports it for the side effect) so a pipe, a manifold or a valve looks the
 * same in either. Every call returns a FRESH MeshStandardMaterial: the legacy
 * temperature overlay (liquid_pipe_renderer.js `colorPipesByTemperature`)
 * recolours `mesh.material` in place, so a material shared between two pipes
 * would paint both with the last one's temperature. Textures ARE shared — one
 * image set per surface, uploaded to the GPU once.
 *
 * ## Textures bind late, never early
 *
 * The images are requested by `loadTextureSets(base)`, which the scene's
 * `loadPbrTextureSet` calls — and only where WebGL is in use (React skips it
 * when `webglAvailable === false`, i.e. in jsdom). A map is attached to a
 * material only AFTER its image has loaded:
 *
 * - the render-scene snapshot refuses a texture whose image is not ready, so a
 *   half-loaded map would make an Omniverse export throw;
 * - a material built before the images land is remembered and gets its maps
 *   the moment they arrive. The pipes and valves are NOT rebuilt by the
 *   viewport's texture-loaded callback (that rebuilds the structural models
 *   only), so without the late binding they would stay bare until the next
 *   edit.
 *
 * ## Colour stays on `material.color`
 *
 * The "tintable" sets ship a high-key neutral colour map; the hue is the
 * material colour (supply blue, return red, a model colour, or a liquid
 * RESULT colour written by the temperature overlays). Copper and brass carry
 * their own hue in the map and a white material colour.
 *
 * ## Tiling lives in the geometry's UVs
 *
 * Liquid geometries are unique per mesh, so physical tiling is written into
 * the UV attribute (`tubeUv`, `cylinderUv`, `boxUv`) and every texture keeps
 * repeat (1,1). One shared texture object per image, no per-mesh clones.
 *
 * `THREE` is read off the global at call time; both apps install r184 globally.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.pbrMaterials = api;
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    function T() {
        var t = (typeof window !== 'undefined' && window.THREE) || (typeof THREE !== 'undefined' ? THREE : null);
        if (!t) throw new Error('pbr_materials.js needs a global THREE');
        return t;
    }

    // Folder -> the mean roughness baked into its roughness.jpg (measured by
    // tools/gen_pbr_textures.py; generated_pbr_sets.json is the source and a
    // test keeps the two in step). The scalar `roughness` of a material whose
    // roughness map is bound is (wanted / mean), so the product averages to
    // the role's roughness.
    var SETS = {
        'brushed-stainless': { meanRoughness: 0.340, tintable: true },
        'copper':            { meanRoughness: 0.302, tintable: false },
        'painted-steel':     { meanRoughness: 0.550, tintable: true },
        'pipe-insulation':   { meanRoughness: 0.821, tintable: true },
        'galvanized-steel':  { meanRoughness: 0.422, tintable: true },
        'brass':             { meanRoughness: 0.363, tintable: false },
        'rubber':            { meanRoughness: 0.860, tintable: true },
    };
    var MAP_FILES = { diffuse: 'color.jpg', normal: 'normal.jpg', roughness: 'roughness.jpg' };

    // role -> surface. `tileM` is the physical size of one texture tile, used
    // by the UV helpers; `color` is the default tint when the caller gives none.
    var ROLES = {
        'pipe-insulation': { set: 'pipe-insulation',   roughness: 0.82, metalness: 0.0,  normalScale: 1.0, tileM: 0.25, color: 0x2a2d31 },
        'stainless':       { set: 'brushed-stainless', roughness: 0.34, metalness: 0.8,  normalScale: 0.8, tileM: 0.20, color: 0xc4c9ce },
        'galvanized':      { set: 'galvanized-steel',  roughness: 0.42, metalness: 0.75, normalScale: 0.8, tileM: 0.50, color: 0xb9bfc5 },
        'painted-steel':   { set: 'painted-steel',     roughness: 0.55, metalness: 0.05, normalScale: 0.6, tileM: 0.40, color: 0x8c949b },
        // Copper and brass carry their hue in the colour map, so their bound
        // colour is white; until the map lands they show `unboundColor`.
        'copper':          { set: 'copper',            roughness: 0.30, metalness: 0.9,  normalScale: 0.7, tileM: 0.15, color: 0xffffff, unboundColor: 0xb87333 },
        'brass':           { set: 'brass',             roughness: 0.36, metalness: 0.9,  normalScale: 0.8, tileM: 0.12, color: 0xffffff, unboundColor: 0xb5a642 },
        'rubber':          { set: 'rubber',            roughness: 0.86, metalness: 0.0,  normalScale: 0.8, tileM: 0.10, color: 0x2b2b2b },
    };

    var _textures = {};   // set -> { diffuse, normal, roughness } (only LOADED textures)
    var _requested = {};  // set -> true once loadTextureSets asked for it
    var _failed = {};     // set -> true if any of its images failed
    var _pending = {};    // set -> materials waiting for that set
    var _loadedBases = {};

    function _setReady(set) {
        var t = _textures[set];
        return !!(t && t.diffuse && t.normal && t.roughness);
    }

    function _scalarRoughness(set, wanted) {
        var mean = (SETS[set] && SETS[set].meanRoughness) || 1;
        return Math.max(0, Math.min(1, wanted / mean));
    }

    function _bind(material) {
        var ud = material.userData || {};
        var set = ud.pbrSet;
        if (!set || !_setReady(set)) return false;
        var tex = _textures[set];
        if (!ud.pbrMapSuppressed) material.map = tex.diffuse;
        if (typeof ud.pbrBoundColor === 'number') {
            material.color.setHex(ud.pbrBoundColor);
            delete ud.pbrBoundColor;
        }
        material.normalMap = tex.normal;
        var ns = typeof ud.pbrNormalScale === 'number' ? ud.pbrNormalScale : 1;
        material.normalScale.set(ns, ns);
        material.roughnessMap = tex.roughness;
        material.roughness = _scalarRoughness(set, ud.pbrRoughness);
        material.needsUpdate = true;
        return true;
    }

    /** Hand a loaded texture to the library (the loader's onLoad; tests inject fakes). */
    function registerTexture(set, mapType, texture) {
        if (!SETS[set]) throw new Error('pbr_materials: unknown texture set ' + set);
        if (!MAP_FILES[mapType]) throw new Error('pbr_materials: unknown map type ' + mapType);
        var THREE_ = T();
        texture.wrapS = texture.wrapT = THREE_.RepeatWrapping;
        texture.colorSpace = mapType === 'diffuse' ? THREE_.SRGBColorSpace : THREE_.NoColorSpace;
        texture.name = 'pbr:' + set + '/' + MAP_FILES[mapType];
        // Shared by every liquid mesh across rebuilds: geometryPure's
        // disposeRenderObject skips `_isCached` textures, so clearing the scene
        // does not free (and force a re-upload of) the GPU copy each time.
        texture._isCached = true;
        if (!_textures[set]) _textures[set] = {};
        _textures[set][mapType] = texture;
        if (_setReady(set) && _pending[set]) {
            var list = _pending[set];
            delete _pending[set];
            list.forEach(_bind);
            if (typeof window !== 'undefined' && typeof window.requestRender === 'function') {
                try { window.requestRender(); } catch (e) { /* host render hook is best-effort */ }
            }
        }
    }

    /**
     * Request every set's images from `<base>/<set>/{color,normal,roughness}.jpg`.
     * Idempotent per base. Only a host that actually renders calls this.
     */
    function loadTextureSets(basePath) {
        var base = basePath || 'textures';
        if (_loadedBases[base]) return;
        _loadedBases[base] = true;
        var THREE_ = T();
        var loader = new THREE_.TextureLoader();
        Object.keys(SETS).forEach(function (set) {
            _requested[set] = true;
            Object.keys(MAP_FILES).forEach(function (mapType) {
                var url = base + '/' + set + '/' + MAP_FILES[mapType];
                loader.load(url, function (texture) {
                    registerTexture(set, mapType, texture);
                }, undefined, function (err) {
                    _failed[set] = true;
                    delete _pending[set];
                    if (typeof console !== 'undefined') console.warn('pbr_materials: failed to load ' + url, err);
                });
            });
        });
    }

    /**
     * A fresh MeshStandardMaterial for `role`.
     *
     * `options` may carry any MeshStandardMaterial parameter (color, opacity,
     * transparent, emissive, side, ...) plus `roughness`/`metalness`/
     * `normalScale` overrides. Maps are bound now if the set has loaded, else
     * when it does.
     */
    function material(role, options) {
        var preset = ROLES[role];
        if (!preset) throw new Error('pbr_materials: unknown role ' + role);
        var THREE_ = T();
        var opts = {};
        Object.keys(options || {}).forEach(function (k) {
            if (options[k] !== undefined) opts[k] = options[k];
        });
        var callerColor = 'color' in opts;
        var roughness = typeof opts.roughness === 'number' ? opts.roughness : preset.roughness;
        var metalness = typeof opts.metalness === 'number' ? opts.metalness : preset.metalness;
        var normalScale = typeof opts.normalScale === 'number' ? opts.normalScale : preset.normalScale;
        delete opts.roughness; delete opts.metalness; delete opts.normalScale;
        var unbound = !callerColor && typeof preset.unboundColor === 'number';
        var params = Object.assign({ color: unbound ? preset.unboundColor : preset.color }, opts,
            { roughness: roughness, metalness: metalness });
        var mat = new THREE_.MeshStandardMaterial(params);
        if (unbound) mat.userData.pbrBoundColor = preset.color;
        // Plain numbers and strings only: Material.copy deep-copies userData
        // through JSON, so a texture reference here would not survive a clone.
        mat.userData.pbrRole = role;
        mat.userData.pbrSet = preset.set;
        mat.userData.pbrRoughness = roughness;
        mat.userData.pbrNormalScale = normalScale;
        if (!_bind(mat) && _requested[preset.set] && !_failed[preset.set]) {
            (_pending[preset.set] = _pending[preset.set] || []).push(mat);
        }
        return mat;
    }

    function _markScaled(geometry) {
        if (geometry.userData.pbrUvScaled) {
            throw new Error('pbr_materials: UVs of this geometry were already scaled (shared geometry?)');
        }
        geometry.userData.pbrUvScaled = true;
    }

    /** uv -> (u*su, v*sv), or (v*su, u*sv) when `swap`. */
    function scaleUv(geometry, su, sv, swap) {
        var uv = geometry && geometry.attributes && geometry.attributes.uv;
        if (!uv) return geometry;
        _markScaled(geometry);
        for (var i = 0; i < uv.count; i++) {
            var u = uv.getX(i), v = uv.getY(i);
            if (swap) uv.setXY(i, v * su, u * sv);
            else uv.setXY(i, u * su, v * sv);
        }
        uv.needsUpdate = true;
        return geometry;
    }

    function _tile(role, tileM) {
        if (typeof tileM === 'number' && tileM > 0) return tileM;
        return (ROLES[role] && ROLES[role].tileM) || 0.25;
    }

    /** Whole tiles around a circumference, so the wrap seam is invisible. */
    function _around(radius, tile) {
        return Math.max(1, Math.round((2 * Math.PI * Math.max(radius, 0)) / tile));
    }

    /**
     * TubeGeometry: u runs 0..1 along the path, v 0..1 around. The texture's
     * u axis (brushing, extrusion lines) follows the pipe.
     */
    function tubeUv(geometry, lengthM, radius, role, tileM) {
        var tile = _tile(role, tileM);
        return scaleUv(geometry, Math.max(lengthM, 1e-6) / tile, _around(radius, tile), false);
    }

    /**
     * CylinderGeometry (axis Y): u runs around, v along the height. Swapped so
     * the texture's u axis follows the cylinder axis, like a tube.
     */
    function cylinderUv(geometry, radius, height, role, tileM) {
        var tile = _tile(role, tileM);
        return scaleUv(geometry, Math.max(height, 1e-6) / tile, _around(radius, tile), true);
    }

    /** BoxGeometry: every face tiled at its own physical size. */
    function boxUv(geometry, role, tileM) {
        var p = geometry && geometry.parameters;
        var uv = geometry && geometry.attributes && geometry.attributes.uv;
        if (!p || !uv) return geometry;
        _markScaled(geometry);
        var tile = _tile(role, tileM);
        var ws = p.widthSegments || 1, hs = p.heightSegments || 1, ds = p.depthSegments || 1;
        // three's BoxGeometry face order and (u, v) extents: px, nx, py, ny, pz, nz
        var faces = [
            [p.depth, p.height, ds, hs], [p.depth, p.height, ds, hs],
            [p.width, p.depth, ws, ds], [p.width, p.depth, ws, ds],
            [p.width, p.height, ws, hs], [p.width, p.height, ws, hs],
        ];
        var start = 0;
        faces.forEach(function (f) {
            var count = (f[2] + 1) * (f[3] + 1);
            for (var i = start; i < start + count && i < uv.count; i++) {
                uv.setXY(i, uv.getX(i) * f[0] / tile, uv.getY(i) * f[1] / tile);
            }
            start += count;
        });
        uv.needsUpdate = true;
        return geometry;
    }

    function isSetReady(set) { return _setReady(set); }

    function _resetForTests() {
        _textures = {}; _requested = {}; _failed = {}; _pending = {}; _loadedBases = {};
    }

    return {
        SETS: SETS,
        ROLES: ROLES,
        MAP_FILES: MAP_FILES,
        loadTextureSets: loadTextureSets,
        registerTexture: registerTexture,
        isSetReady: isSetReady,
        material: material,
        scaleUv: scaleUv,
        tubeUv: tubeUv,
        cylinderUv: cylinderUv,
        boxUv: boxUv,
        _resetForTests: _resetForTests,
    };
});
