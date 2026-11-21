/**
 * BOX/CDU material creation intent. Carrier owns texture IO, readiness and caches.
 *
 * Every number comes from textures/materials.json through js/material_library.js:
 * the `box`, `cdu-body` (the painted-steel library role) and `face-image`
 * materials. The policy describes a MeshStandardMaterial (or, for the CDU, the
 * library material) — no renderer object is made here.
 */
(function (root, factory) {
    function library() {
        var lib = (root && root.materialLibrary) || (typeof window !== 'undefined' && window.materialLibrary) || null;
        if (!lib && typeof module === 'object' && module.exports && typeof require === 'function') lib = require('./material_library.js');
        if (!lib) throw new Error('box_material_recipe.js needs js/material_library.js loaded first');
        return lib;
    }
    var api = Object.freeze(factory(library));
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.boxMaterialRecipe = api;
    if (typeof window !== 'undefined') window.boxMaterialRecipe = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (library) {
    'use strict';
    var FACE_IMAGE = 'face-image:';
    function entry(kind) {
        var m = library().material(kind);
        if (!m) throw new Error('materials.json has no material ' + kind);
        return m;
    }
    /** The generic cabinet surface (a fresh copy). */
    function boxPreset() {
        var m = entry('box');
        return { metalness: m.metallic, roughness: m.roughness, normalScale: m.normalScale, tileM: m.tileM,
            set: m.set, baseColor: m.baseColor };
    }
    /** The painted-steel library role the CDU cabinet uses (a fresh copy, pbr_materials ROLES shape). */
    function paintedSteelRole() {
        var m = entry('painted-steel');
        return { set: m.set, roughness: m.roughness, metalness: m.metallic, normalScale: m.normalScale,
            tileM: m.tileM, color: library().parseColor(m.baseColor) };
    }
    function paintedSteelSet() {
        var s = library().set(entry('painted-steel').set);
        return { meanRoughness: s.stats.meanRoughness, tintable: s.tintable };
    }
    function scalarRoughness(wanted, mean) { return Math.max(0, Math.min(1, wanted / (mean || 1))); }
    function textureRepeat(options, context) {
        var n = options && options.uvNormalize;
        if (n && (n.type === 'stretch' || n.type === 'tile-per-u')) return 1;
        if (context && context.clampToEdge) return 1;
        var tile = (n && n.tileSize) || (context && context.tileSize) || entry('box').tileM;
        return Math.round(1000 / tile) / 1000;
    }
    function overlayDefaults(options) {
        var overlay = Number.isFinite(options.renderOrder) && options.renderOrder > 0;
        return {
            polygonOffsetFactor: overlay && options.polygonOffsetFactor === undefined ? 0 : options.polygonOffsetFactor,
            keepUserColor: overlay && options.keepUserColor === undefined ? true : options.keepUserColor
        };
    }
    function imageSource(value) {
        if (!value) return null;
        var src = value.textureDataUrl || value.textureUrl;
        return (typeof src === 'string' && src) ? src : null;
    }
    function materialPolicy(typeKey, options, context) {
        options = options || {}; context = context || {};
        var photo = typeKey.indexOf(FACE_IMAGE) === 0;
        if (!photo && typeKey !== 'box' && typeKey !== 'cdu-body') throw new Error('Unsupported material recipe kind: ' + typeKey);
        var kind = photo ? 'face-image' : typeKey;
        var m = entry(kind);
        var set = m.set ? library().set(m.set) : null;
        var opacity = (options.opacity ?? 1) * m.opacity;
        // Translucent by nature (the manifest's opacity, or alpha in the colour map).
        var natural = m.opacity < 0.999 || !!(set && set.alphaInColor);
        var transparent = natural || (typeof options.transparent === 'boolean' ? options.transparent : opacity < 0.999);
        var useLibrary = !photo && typeKey === 'cdu-body' && !!context.libraryAvailable;
        var role = context.paintedSteelRole || paintedSteelRole();
        var parameters = {
            side: options.doubleSide || m.doubleSide ? 'double' : 'front', polygonOffset: true,
            polygonOffsetFactor: options.polygonOffsetFactor ?? 1,
            transparent: transparent, opacity: opacity, depthWrite: options.depthWrite ?? !transparent
        };
        // The library material keeps the caller colour (the CDU face colour);
        // a standard one shows what the manifest's colour rules decide.
        if (useLibrary) { if (options.color !== undefined) parameters.color = options.color; }
        else parameters.color = libraryColor(kind, options);
        var roughness = useLibrary ? role.roughness : m.roughness;
        var metalness = useLibrary ? role.metalness : m.metallic;
        if (!useLibrary) { parameters.roughness = roughness; parameters.metalness = metalness; }
        var uv = options.uvNormalize ? {
            type: options.uvNormalize.type,
            ...(options.uvNormalize.tileSize !== undefined ? { tileSize: options.uvNormalize.tileSize } : {}),
            ...(options.uvNormalize.uCount !== undefined ? { uCount: options.uvNormalize.uCount } : {})
        } : null;
        return {
            model: useLibrary ? 'library' : 'standard', objectKind: typeKey,
            parameters: parameters,
            pbr: { roughness: roughness, metalness: metalness,
                normalScale: photo ? 0 : useLibrary ? role.normalScale : m.normalScale,
                set: photo ? null : useLibrary ? role.set : m.set,
                ...(useLibrary ? { role: 'painted-steel',
                    defaultColor: role.color, tileM: role.tileM,
                    boundRoughness: scalarRoughness(roughness, context.meanRoughness ?? paintedSteelSet().meanRoughness) } : {}) },
            texture: { source: photo ? typeKey.slice(FACE_IMAGE.length) : null,
                repeat: textureRepeat(options, { ...context, tileSize: context.tileSize ?? m.tileM }) },
            uv: { normalize: uv, physicalScale: useLibrary && !uv && !options.pbrUvReady ? 1 / (role.tileM || entry('box').tileM) : null },
            renderOrder: Number.isFinite(options.renderOrder) ? options.renderOrder : 0
        };
    }
    function libraryColor(kind, options) {
        return library().colorFor(kind, options.color, !!options.keepUserColor);
    }
    function createRecipe(source, resolvedKind, part, context) {
        if (resolvedKind !== 'box' && resolvedKind !== 'cdu') throw new Error('Unsupported material recipe kind: ' + resolvedKind);
        if (!part || !part.materialRecipe) throw new Error('material recipe requires a structural part');
        context = context || {};
        var face = source.geometry && source.geometry.faces && source.geometry.faces[part.faceId];
        var image;
        if (part.isBaseWall) image = imageSource(face);
        else if (Object.prototype.hasOwnProperty.call(context, 'patchImageSource')) image = context.patchImageSource;
        else {
            var patch = face && (face.patches || []).find(function (p) { return p && p.id === (part.refIds || [])[0]; });
            image = imageSource(patch);
        }
        var options = {
            color: image ? '#ffffff' : part.materialRecipe.color,
            opacity: part.materialRecipe.opacity,
            ...(image ? { uvNormalize: { type: 'stretch' } } : {}),
            ...(!part.isBaseWall ? { renderOrder: 3, polygonOffsetFactor: 0, keepUserColor: true } : {})
        };
        var typeKey = image ? FACE_IMAGE + image : part.isBaseWall && resolvedKind === 'cdu' ? 'cdu-body' : 'box';
        var policy = materialPolicy(typeKey, options, context);
        return {
            schema: 'dcdtuni.material-recipe/v1', kind: resolvedKind,
            source: { ownerUid: source.uid, partId: part.partId, faceId: part.faceId, patchIds: (part.refIds || []).slice() },
            options: { ...options, objectKind: typeKey }, policy: policy
        };
    }
    return { boxPreset: boxPreset, paintedSteelRole: paintedSteelRole, paintedSteelSet: paintedSteelSet,
        scalarRoughness: scalarRoughness, textureRepeat: textureRepeat, overlayDefaults: overlayDefaults,
        imageSource: imageSource, materialPolicy: materialPolicy, createRecipe: createRecipe };
});
