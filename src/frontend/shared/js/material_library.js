/**
 * material_library.js — the reader of the material and texture pool manifest.
 *
 * `src/frontend/shared/textures/materials.json` (schema
 * dcdtuni.material-library/v1) is the single source of every surface the
 * editors draw: which texture set a kind uses, its colour, roughness,
 * metalness, normal strength, tiling, UV mode and wrap, the patch presets and
 * the shared lighting. This module is the only code that reads it; the
 * geometry builder, the recipes and the PBR library ask it, and none of them
 * keeps a number of its own. The Omniverse side reads the same file.
 *
 * Installing the manifest:
 * - React imports the JSON and calls `install` before the renderers load
 *   (src/legacy/materialManifest.ts);
 * - the legacy page `load`s it from `textures/materials.json` at start-up;
 * - CommonJS (node, the standalone kernel tests) gets it installed from the
 *   pool by `require`.
 *
 * The colour, roughness and UV rules are the manifest's `colorRules`; they are
 * implemented here once so both editors apply them identically.
 *
 * No THREE, no DOM: it runs in a bare script context.
 */
(function (root, factory) {
    var api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
        if (typeof require === 'function') {
            try { api.install(require('../textures/materials.json')); } catch (e) { /* a bundler installs it explicitly */ }
        }
    }
    if (root) root.materialLibrary = api;
})(typeof globalThis !== 'undefined' ? globalThis : (typeof window !== 'undefined' ? window : this), function () {
    'use strict';

    var SCHEMA = 'dcdtuni.material-library/v1';
    var _manifest = null;
    var _resolved = {};
    var _waiters = [];
    var _loading = null;

    function install(manifest) {
        if (!manifest || manifest.schema !== SCHEMA) {
            throw new Error('material_library: expected a ' + SCHEMA + ' manifest, got ' + (manifest && manifest.schema));
        }
        _manifest = manifest;
        _resolved = {};
        var waiting = _waiters;
        _waiters = [];
        waiting.forEach(function (fn) { fn(api); });
        return api;
    }

    function isReady() { return !!_manifest; }

    function manifest() {
        if (!_manifest) {
            throw new Error('material_library: textures/materials.json is not installed yet ' +
                '(React installs it in src/legacy/materialManifest.ts; the legacy page loads it at start-up)');
        }
        return _manifest;
    }

    /** Run `fn(api)` now if the manifest is in, else as soon as it is. */
    function whenReady(fn) {
        if (_manifest) fn(api);
        else _waiters.push(fn);
    }

    /** Fetch and install the manifest (idempotent). For a page that cannot import JSON. */
    function load(url) {
        if (_manifest) return Promise.resolve(api);
        if (_loading) return _loading;
        if (typeof fetch !== 'function') return Promise.reject(new Error('material_library: no fetch to load ' + url));
        _loading = fetch(url, { cache: 'no-cache' }).then(function (r) {
            if (!r.ok) throw new Error('material_library: ' + url + ' answered ' + r.status);
            return r.json();
        }).then(install, function (err) {
            _loading = null;
            throw err;
        });
        return _loading;
    }

    /**
     * The material of an objectKind or library role, or null. A role kind
     * (`role`) answers with its library material's fields plus its own
     * `kind`, `role` and `hueInMap`. Treat the result as read-only.
     */
    function material(kind) {
        var all = manifest().materials;
        if (Object.prototype.hasOwnProperty.call(_resolved, kind)) return _resolved[kind];
        var entry = Object.prototype.hasOwnProperty.call(all, kind) ? all[kind] : null;
        var out = null;
        if (entry && entry.role) {
            var target = all[entry.role];
            if (!target || target.role) throw new Error('material_library: ' + kind + ' names an unknown role ' + entry.role);
            out = Object.assign({}, target, { kind: kind, role: entry.role, hueInMap: !!entry.hueInMap, label: entry.label });
        } else if (entry) {
            out = Object.assign({}, entry, { kind: kind, role: entry.library ? kind : null, hueInMap: false });
        }
        _resolved[kind] = out;
        return out;
    }

    function set(name) {
        var sets = manifest().sets;
        return Object.prototype.hasOwnProperty.call(sets, name) ? sets[name] : null;
    }

    function setNames() { return Object.keys(manifest().sets); }

    /** The sets some material draws with — exactly what an editor must load. */
    function setsInUse() {
        var used = {};
        Object.keys(manifest().materials).forEach(function (kind) {
            var m = material(kind);
            if (m && m.set) used[m.set] = true;
        });
        return Object.keys(used);
    }

    /** The library roles (pbr_materials.js), in manifest order. */
    function libraryRoles() {
        var all = manifest().materials;
        return Object.keys(all).filter(function (k) { return all[k].library === true; });
    }

    function meanRoughness(setName) {
        var s = set(setName);
        return s ? s.stats.meanRoughness : 1;
    }

    /** A map's pool-relative file (`color` | `normal` | `roughness`). */
    function mapFile(setName, mapKey) {
        var s = set(setName);
        if (!s) throw new Error('material_library: unknown set ' + setName);
        var ref = s.maps[mapKey];
        if (!ref) throw new Error('material_library: set ' + setName + ' has no ' + mapKey + ' map');
        return ref.file;
    }

    /** `<base>/<file>`, where `base` is how the app serves the pool ('/textures' or 'textures'). */
    function textureUrl(base, file) {
        return String(base || 'textures').replace(/\/+$/, '') + '/' + file;
    }

    function hexOf(value) {
        if (typeof value === 'number' && isFinite(value)) return '#' + ('000000' + (value >>> 0 & 0xffffff).toString(16)).slice(-6);
        if (typeof value !== 'string') return null;
        var s = value.trim().toLowerCase();
        if (/^#[0-9a-f]{6}$/.test(s)) return s;
        if (/^#[0-9a-f]{3}$/.test(s)) return '#' + s[1] + s[1] + s[2] + s[2] + s[3] + s[3];
        return null;
    }

    function parseColor(value) {
        var hex = hexOf(value);
        return hex ? parseInt(hex.slice(1), 16) : null;
    }

    /**
     * The colour a mesh of `kind` shows, from what the renderer handed over.
     *
     * The manifest's colorRules: a non-tintable material shows its base colour
     * (the map carries the hue). A tintable one shows its base colour unless
     * the mesh carries an AUTHORED colour — a patch overlay (`keepUserColor`),
     * or, for `colorSource: model`, any colour that is not a renderer default
     * listed in `factoryColors`.
     */
    function colorFor(kind, incoming, keepUserColor) {
        var m = material(kind);
        if (!m) return incoming;
        if (!m.tintable) return m.baseColor;
        var given = incoming !== undefined && incoming !== null && incoming !== '';
        if (given && keepUserColor) return incoming;
        if (given && m.colorSource === 'model') {
            var hex = hexOf(incoming);
            var factory = (m.factoryColors || []).some(function (c) { return c === hex; });
            if (!factory) return incoming;
        }
        return m.baseColor;
    }

    /** The scalar roughness: wanted / the map's mean when the map is bound, else wanted. */
    function roughnessScalar(kind, mapBound) {
        var m = material(kind);
        if (!m) throw new Error('material_library: unknown kind ' + kind);
        if (!mapBound || !m.set) return m.roughness;
        return Math.max(0, Math.min(1, m.roughness / (meanRoughness(m.set) || 1)));
    }

    function lighting() { return manifest().lighting; }
    function patchPresets() { return manifest().patchPresets; }
    function photos() { return manifest().photos; }

    var _byFile = null;
    function fileIndex() {
        if (_byFile && _byFile.manifest === _manifest) return _byFile.index;
        var index = {};
        var m = manifest();
        Object.keys(m.sets).forEach(function (n) {
            Object.keys(m.sets[n].maps).forEach(function (k) { index[m.sets[n].maps[k].file] = m.sets[n].maps[k]; });
        });
        Object.keys(m.photos).forEach(function (n) { index[m.photos[n].image.file] = m.photos[n].image; });
        m.patchPresets.forEach(function (p) { index[p.image.file] = p.image; });
        _byFile = { manifest: _manifest, index: index };
        return index;
    }

    /** The pool file a served URL names (`/textures/<file>` or `textures/<file>`), or null. */
    function poolFile(url) {
        if (typeof url !== 'string') return null;
        var path = url.split(/[?#]/, 1)[0];
        try { path = decodeURI(path); } catch (e) { return null; }
        var match = /^(?:https?:\/\/[^/]+)?\/?(?:api\/)?textures\/(.+)$/.exec(path);
        if (!match || match[1].split('/').some(function (seg) { return seg === '..' || seg === '.' || seg === ''; })) return null;
        var ref = fileIndex()[match[1]];
        return ref ? ref : null;
    }

    /** `pool:<file>#<sha256>` for a pool image, or null — what the Omniverse bridge sends instead of bytes. */
    function poolRef(url) {
        var ref = poolFile(url);
        return ref ? 'pool:' + ref.file + '#' + ref.sha256 : null;
    }

    var api = {
        SCHEMA: SCHEMA,
        install: install,
        isReady: isReady,
        whenReady: whenReady,
        load: load,
        manifest: manifest,
        material: material,
        set: set,
        setNames: setNames,
        setsInUse: setsInUse,
        libraryRoles: libraryRoles,
        meanRoughness: meanRoughness,
        mapFile: mapFile,
        textureUrl: textureUrl,
        parseColor: parseColor,
        colorFor: colorFor,
        roughnessScalar: roughnessScalar,
        lighting: lighting,
        patchPresets: patchPresets,
        photos: photos,
        poolFile: poolFile,
        poolRef: poolRef,
    };
    return api;
});
