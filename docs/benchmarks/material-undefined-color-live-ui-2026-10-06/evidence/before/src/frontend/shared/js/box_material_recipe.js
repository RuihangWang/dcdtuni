/** BOX/CDU material creation intent. Carrier owns texture IO, readiness and caches. */
(function (root, factory) {
    var api = Object.freeze(factory());
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.boxMaterialRecipe = api;
    if (typeof window !== 'undefined') window.boxMaterialRecipe = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';
    function boxPreset() {
        return { metalness: 0.2, roughness: 0.7, normalScale: 0.3, envMapIntensity: 0.3, tintColor: null, tileM: 0.4 };
    }
    function paintedSteelRole() {
        return { set: 'painted-steel', roughness: 0.55, metalness: 0.05, normalScale: 0.6, tileM: 0.40, color: 0x8c949b };
    }
    function paintedSteelSet() { return { meanRoughness: 0.550, tintable: true }; }
    function scalarRoughness(wanted, mean) { return Math.max(0, Math.min(1, wanted / (mean || 1))); }
    function textureRepeat(options, context) {
        var n = options && options.uvNormalize;
        if (n && (n.type === 'stretch' || n.type === 'tile-per-u')) return 1;
        if (context && context.clampToEdge) return 1;
        var tile = (n && n.tileSize) || (context && context.tileSize) || 0.4;
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
        var photo = typeKey.indexOf('face-image:') === 0;
        if (!photo && typeKey !== 'box' && typeKey !== 'cdu-body') throw new Error('Unsupported material recipe kind: ' + typeKey);
        var opacity = options.opacity ?? 1;
        var transparent = typeof options.transparent === 'boolean' ? options.transparent : opacity < 0.999;
        var parameters = {
            color: photo ? 0xffffff : options.color,
            side: options.doubleSide ? 'double' : 'front', polygonOffset: true,
            polygonOffsetFactor: options.polygonOffsetFactor ?? 1,
            transparent: transparent, opacity: opacity, depthWrite: options.depthWrite ?? !transparent
        };
        var role = context.paintedSteelRole || paintedSteelRole();
        var preset = context.boxPreset || boxPreset();
        var library = !photo && typeKey === 'cdu-body' && !!context.libraryAvailable;
        if (photo) { parameters.shininess = 18; parameters.specular = 0x222222; }
        else if (!library) { parameters.shininess = Math.round((1 - preset.roughness) * 80) + 10; parameters.specular = 0x111111; }
        var roughness = photo ? 0.6 : library ? role.roughness : preset.roughness;
        var metalness = photo ? 0.0 : library ? role.metalness : preset.metalness;
        var uv = options.uvNormalize ? {
            type: options.uvNormalize.type,
            ...(options.uvNormalize.tileSize !== undefined ? { tileSize: options.uvNormalize.tileSize } : {}),
            ...(options.uvNormalize.uCount !== undefined ? { uCount: options.uvNormalize.uCount } : {})
        } : null;
        return {
            model: library ? 'library' : 'phong', objectKind: typeKey,
            parameters: parameters,
            pbr: { roughness: roughness, metalness: metalness,
                normalScale: photo ? 0 : library ? role.normalScale : preset.normalScale,
                ...(library ? { role: 'painted-steel', set: role.set,
                    defaultColor: role.color, tileM: role.tileM,
                    boundRoughness: scalarRoughness(roughness, context.meanRoughness ?? paintedSteelSet().meanRoughness) } : {}) },
            texture: { source: photo ? typeKey.slice('face-image:'.length) : null, repeat: textureRepeat(options, context) },
            uv: { normalize: uv, physicalScale: library && !uv && !options.pbrUvReady ? 1 / (role.tileM || 0.4) : null },
            renderOrder: Number.isFinite(options.renderOrder) ? options.renderOrder : 0
        };
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
        var typeKey = image ? 'face-image:' + image : part.isBaseWall && resolvedKind === 'cdu' ? 'cdu-body' : 'box';
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
